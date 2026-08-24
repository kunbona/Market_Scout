"""
行业趋势均线系统分类 (industry MA-trend classifier)
====================================================
用法:
    python quant/industry_ma_trend.py [--tail 300] [--date YYYY-MM-DD]

思路:
    1. 行业指数直接用**申万官方一级行业指数**(fetch_sw_industry_index.py 拉取并缓存
       至 data/sw_industry_index.parquet)。自建"今日成分回溯"指数有幸存者偏差+
       新股涨幅注入(医药实证: 自建等权后复权 5y位置 83.5% vs 官方申万 40.9%),
       官方指数每天用"当时"的成分与权重编制, 高低点位置才是真实历史区间位置。
    2. 本地 snapshot 个股数据仅做两个个股层指标: 广度(站上MA20家数占比)、
       K线形态净强度(上升占比-下降占比)。个股广度/形态用 cal_fuquan_price 后复权价。
    3. 对官方指数计算 MA5 / MA10 / MA20 / MA45 / MA60 / MA90 / MA120。
    4. 用均线系统做大致分类:
         - 强势多头: 价格>所有MA 且 MA5>MA10>MA20>MA60>MA120 (多头排列)
         - 多头:     价格>MA20 且 MA20>MA60>MA120 (中期向上, 短期或回踩)
         - 震荡粘合: 各MA彼此贴近 (spread<3%), 无方向
         - 震荡:     其余无明确排列
         - 筑底反转: 价格<MA60/MA120 (长期弱) 但 >MA5/MA10/MA20 (短期转强)
         - 筑底回踩: 价格<MA120 但 >MA20 且 <MA5 (底部反弹中的短期回踩,
           多头回踩的底部版: 中期结构已修复, 只差短期均线收复)
         - 空头:     价格<MA20 且 MA20<MA60<MA120 (中期向下)
         - 强势空头: 价格<所有MA 且 MA5<MA10<MA20<MA60<MA120 (空头排列)
    5. 输出 CSV + HTML 报告 (分类汇总 + 全表)。
    补充列(2026-08-21): 形态净值(个股形态净强度原值)、广度%/广度20日Δ(站上MA20占比及
    其20日变化, 内部热度计)、vs各基准% 共6列(行业20日涨幅与 科创50/创业板指/上证50/沪深300/
    中证1000/深证成指 **各自独立对比**的超额, 看行业压过/被压制哪路风格)、量能比(官方指数
    20日均成交额/120日均额, >1.2放量<0.8缩量, 慢档水位)、量能比5(5日均额/120日均额,
    快档启动探测器, 快档穿越慢档=量能拐点)。
    资金流列(2026-08-21, fetch_sw_industry_fundflow.py 维护, 板块级主力净额, 主力+散户=0):
    当日/5日/20日三档, 净占比一律"Σ净额÷Σ成交额"(按钱加权, 不用逐日净占比均值——地量日
    极端占比会与大成交日等权污染)。当日净占比%只当警报灯(|净占比|>=3%才着色, 单日噪声大);
    5日净占比%=快档, 主力净占比%=20日慢档水位; 资金流Δ=5日净占比-20日净占比(pp), 抓
    "表面温和实际被加速抛售"的行业(主力长期净流出是常态, 净占比看横截面相对强弱)。
"""

import sys
import os
from pathlib import Path
import argparse
import pandas as pd
import numpy as np

QUANT = Path("/Users/kun/Documents/market-radar/quant")
sys.path.insert(0, str(QUANT))
from snapshot import iter_stock_frames, snapshot_meta  # noqa: E402

SW_CACHE = QUANT / "data" / "sw_industry_index.parquet"
FF_CACHE = QUANT / "data" / "sw_industry_fundflow.parquet"  # 主力资金流(fetch_sw_industry_fundflow.py 维护)
# 基准指数(用户指定, 2026-08-21): 六个风格指数**各自独立**与行业对比(不合成一个数),
# 行业20日涨幅 - 各基准20日涨幅 = 行业相对该风格指数的超额, 看行业压过哪路风格/被哪路压制。
# 读本地 stock-main-index-data/, 不含北证。
BENCH_CODES = {"sh000688": "科创50", "sz399006": "创业板指",
               "sh000016": "上证50", "sh000300": "沪深300",
               "sh000852": "中证1000", "sz399001": "深证成指"}
BENCH_NAMES = list(BENCH_CODES.values())

COL_DATE = "交易日期"
COL_CLOSE = "收盘价"
COL_PREV = "前收盘价"
COL_IND = "新版申万一级行业名称"
COL_HIGH = "最高价"
COL_LOW = "最低价"
COL_MC = "流通市值"   # 申万指数用自由流通市值加权 -> 对齐用户参考的 cap-weighted 行业指数

MAS = [5, 10, 20, 45, 60, 90, 120]


def cal_fuquan_price(close_s: pd.Series, prev_s: pd.Series, fuquan_type: str = "后复权") -> pd.Series:
    """后复权价 (复用 core/market_essentials.cal_fuquan_price 算法, 函数本身正确, 勿改)。
    复权因子 = cumprod(收盘价 / 前收盘价); 后复权价 = 因子 × (首日均价/首日因子)。
    注: 本数据集前收盘价已为除权调整参考价, 故重建后的复权收益率=后复权(含分红送股)收益率。"""
    s_close = close_s.reset_index(drop=True)
    s_prev = prev_s.reset_index(drop=True)
    fq_factor = (s_close / s_prev).cumprod()
    if fuquan_type == "后复权":
        scale = s_close.iloc[0] / fq_factor.iloc[0]
    else:
        scale = s_close.iloc[-1] / fq_factor.iloc[-1]
    return fq_factor * scale


def candle_state(high_s: pd.Series, low_s: pd.Series, close_s: pd.Series, n: int = 20) -> pd.Series:
    """道氏三态(收紧版): 用滚动 N 日高低点 + 收盘区间位置 判断 上升/调整/下降。
    上升 = 高点创新高 且 低点抬高 且 收盘在区间上 60% 以上(真正强势上行);
    下降 = 高点下降 且 低点下降 且 收盘在区间下 40% 以下(真正弱势下行);
    其余 = 调整(横盘/收敛/高低点矛盾)。返回与输入等长的 形态 序列。"""
    hh = high_s.rolling(n, min_periods=int(n * 0.5)).max()
    ll = low_s.rolling(n, min_periods=int(n * 0.5)).min()
    hh_prev = hh.shift(n)
    ll_prev = ll.shift(n)
    hh_up = hh > hh_prev
    ll_up = ll > ll_prev
    hh_dn = hh < hh_prev
    ll_dn = ll < ll_prev
    rng = (hh - ll).replace(0, np.nan)
    close_pos = (close_s - ll) / rng            # 0=区间底, 1=区间顶
    up = (hh_up & ll_up & (close_pos > 0.6)).fillna(False)
    dn = (hh_dn & ll_dn & (close_pos < 0.4)).fillna(False)
    state = pd.Series(index=high_s.index, dtype=object)
    state[up] = "上升"
    state[dn] = "下降"
    state[~(up | dn)] = "调整"
    return state


def load_industry_returns(tail: int, end_date: str | None):
    """返回 (piv, breadth, pat_net)。
    piv = 行业**等权**日收益%(现仅作参考, 主脚本已改用申万官方指数);
    breadth/pat 为个股级等权聚合(占比/形态净强度)。"""
    cols = [COL_DATE, COL_CLOSE, COL_PREV, COL_HIGH, COL_LOW, COL_MC, COL_IND]
    frames = []
    for code, df in iter_stock_frames(cols, tail_rows=tail):
        if code.lower().startswith("bj"):
            continue
        if df is None or df.empty or COL_IND not in df.columns:
            continue
        df = df.dropna(subset=[COL_IND])
        if df.empty:
            continue
        df[COL_DATE] = pd.to_datetime(df[COL_DATE])
        if end_date:
            df = df[df[COL_DATE] <= pd.Timestamp(end_date)]
            if df.empty:
                continue   # 截止日晚于其上市日的新股: 该截面无数据, 跳过(不影响最新日输出)
        df = df.sort_values(COL_DATE)
        close = df[COL_CLOSE].astype(float)
        prev = df[COL_PREV].astype(float)
        high = df[COL_HIGH].astype(float)
        low = df[COL_LOW].astype(float)
        mc = df[COL_MC].astype(float)
        # 全链路统一用 cal_fuquan_price 的后复权价(函数正确, 原样保留)。
        # 行业指数收益率 ret、广度 above20、形态 都基于同一后复权价 fq:
        # 后复权价剔除除权跳空、含分红送股再投资, 跨期内部一致(幂等), 高低点位置判断不被除权跳空干扰。
        fq = cal_fuquan_price(close, prev)
        ret = fq.pct_change() * 100           # 行业指数日收益: 用后复权价, 与广度/形态统一口径
        ma20 = fq.rolling(20, min_periods=int(20 * 0.85)).mean()
        above20 = (fq > ma20).astype(float)   # 个股站上MA20=1/否=0, 用于行业广度
        st = candle_state(high, low, close)    # 个股每日 K线形态: 上升/调整/下降
        up_flag = (st == "上升").astype(float)
        dn_flag = (st == "下降").astype(float)
        df = pd.DataFrame({
            COL_DATE: df[COL_DATE].values,
            "ret": ret.values,
            "mc": mc.values,
            "above20": above20.values,
            "up_flag": up_flag.values,
            "dn_flag": dn_flag.values,
            COL_IND: df[COL_IND].astype(str).values,
        })
        frames.append(df)

    if not frames:
        raise RuntimeError("没有可用股票数据")
    all_df = pd.concat(frames, ignore_index=True)
    all_df = all_df.dropna(subset=["ret"])
    # 行业日收益 = 成分股**等权**均值(简单平均), 用于构建 MA 指数与长周期高低点。
    # 为何不等权→市值加权(对齐申万)？
    #   试过 Σ(mc*ret)/Σ(mc), 但本快照的市值加权会把近期新晋大市值股(石药创新+275%、
    #   百利天恒等)过度加权, 使行业指数在 2024-2026 反弹中超过 2021 泡沫顶, 与申万医药
    #   "2021 见顶后深度回调、现价仍处长期底部"的真实轨迹相反(医药真实中位个股 2021→2026 跌 -31%)。
    #   等权更稳健地反映行业广度, 高低点位置判断也更贴近"板块整体所处区间"。
    piv = all_df.pivot_table(index=COL_DATE, columns=COL_IND, values="ret", aggfunc="mean").sort_index()
    # 每日每行业"站上MA20占比"(广度): 个股 above20 均值
    breadth = all_df.pivot_table(index=COL_DATE, columns=COL_IND,
                                 values="above20", aggfunc="mean").sort_index()
    # 每日每行业"形态净强度": 上升占比 - 下降占比 (-1~1), 用于 K线形态验证
    pat_net = (all_df.pivot_table(index=COL_DATE, columns=COL_IND, values="up_flag", aggfunc="mean")
               - all_df.pivot_table(index=COL_DATE, columns=COL_IND, values="dn_flag", aggfunc="mean")).sort_index()
    return piv, breadth, pat_net


def _zt_threshold(code: str) -> float:
    """涨停阈值(按板块): 主板10%, 创业板/科创板20%, 北交所30%。与 dm_kun 一致。"""
    c = str(code).lower()
    if c.startswith("bj"):
        return 29.95
    if c.startswith("sz3") or c.startswith("sh688"):
        return 19.95
    return 9.95


def load_daily_cross_section(end_date: str | None) -> pd.DataFrame:
    """当日个股层聚合: 涨家数/跌家数/涨停数/跌停数/大肉%/大面%/领涨市值/领涨股。

    数据源: quant.snapshot 快照(全字段)。当日涨跌幅 = (收盘/前收盘-1)*100
    (快照无涨跌幅列, 自算; 口径与 dm_kun industry_enhanced_analyzer 一致)。
    返回 DataFrame, index=行业名。
    """
    try:
        from quant.snapshot import load_snapshot
    except ModuleNotFoundError:
        from snapshot import load_snapshot
    df = load_snapshot(["股票代码", "股票名称", "交易日期", "收盘价", "前收盘价",
                        "流通市值", "新版申万一级行业名称"])
    if end_date:
        df = df[df["交易日期"] <= pd.Timestamp(end_date)]
    if df.empty:
        return pd.DataFrame()
    latest = df["交易日期"].max()
    d = df[df["交易日期"] == latest].copy()
    d = d[~d["股票代码"].astype(str).str.lower().str.startswith("bj")]
    d = d.dropna(subset=["新版申万一级行业名称"])
    d["pct"] = (pd.to_numeric(d["收盘价"], errors="coerce")
                / pd.to_numeric(d["前收盘价"], errors="coerce") - 1) * 100
    d = d.dropna(subset=["pct"])
    if d.empty:
        return pd.DataFrame()
    th = d["股票代码"].map(_zt_threshold)
    d["is_zt"] = d["pct"] >= th
    d["is_dt"] = d["pct"] <= -th
    d["mc"] = pd.to_numeric(d["流通市值"], errors="coerce")

    def _lead_cap(sub: pd.DataFrame) -> str:
        """领涨市值档: 大(>=500亿)/中(100-500亿)/小(<100亿) 各档平均涨幅最高者。"""
        s = sub.dropna(subset=["mc"])
        if s.empty:
            return "-"
        big = s[s["mc"] >= 500e8]["pct"].mean()
        mid = s[(s["mc"] >= 100e8) & (s["mc"] < 500e8)]["pct"].mean()
        sml = s[s["mc"] < 100e8]["pct"].mean()
        m = {"大市值": big, "中市值": mid, "小市值": sml}
        m = {k: v for k, v in m.items() if pd.notna(v)}
        return max(m, key=m.get) if m else "-"

    def _lead_stocks(sub: pd.DataFrame) -> str:
        top = sub.nlargest(2, "pct")
        return "/".join(f"{r['股票名称']}{r['pct']:+.1f}%" for _, r in top.iterrows())

    g = d.groupby("新版申万一级行业名称")
    out = pd.DataFrame({
        "涨家数": g["pct"].apply(lambda s: int((s > 0).sum())),
        "跌家数": g["pct"].apply(lambda s: int((s < 0).sum())),
        "涨家数%": g["pct"].apply(lambda s: round((s > 0).mean() * 100, 1)),
        "涨停数": g["is_zt"].apply(lambda s: int(s.sum())),
        "跌停数": g["is_dt"].apply(lambda s: int(s.sum())),
        "大肉%": g["pct"].apply(lambda s: round((s >= 5).mean() * 100, 1)),
        "大面%": g["pct"].apply(lambda s: round((s <= -5).mean() * 100, 1)),
    })
    out["领涨市值"] = g.apply(_lead_cap, include_groups=False)
    out["领涨股"] = g.apply(_lead_stocks, include_groups=False)
    out.index.name = "行业"

    # 当日行业涨跌幅: 申万官方指数收盘 pct_change(1) (真实点位, 非个股等权)
    try:
        sw = load_sw_index(end_date)
        if not sw.empty and len(sw) >= 2:
            daily_ret = (sw.pct_change(1).iloc[-1] * 100).round(1)
            out["当日涨幅%"] = out.index.map(lambda ind: daily_ret.get(ind, np.nan))
        else:
            out["当日涨幅%"] = np.nan
    except Exception:
        out["当日涨幅%"] = np.nan
    return out


def load_sw_index(end_date: str | None) -> pd.DataFrame:
    """读取申万一级行业**官方指数**缓存(由 fetch_sw_industry_index.py 拉取维护)。
    返回宽表: index=交易日, columns=行业名, values=收盘价(申万官方真实点位)。
    为什么用官方指数而非自建: 自建"今日成分回溯"指数有幸存者偏差(退市/调出的老股
    缺席, 最深的下跌被漏掉)+新股涨幅注入(新上市大市值股从上市日就有满仓位);
    医药实证: 自建等权后复权 5y位置 83.5%/市值加权 98.9% vs 官方申万 40.9%。
    官方指数每天用"当时"的成分与权重编制(日记式), 无回溯偏差。"""
    if not SW_CACHE.exists():
        raise RuntimeError(
            f"申万行业指数缓存不存在: {SW_CACHE}\n"
            "请先运行: python quant/fetch_sw_industry_index.py")
    df = pd.read_parquet(SW_CACHE)
    df["date"] = pd.to_datetime(df["date"])
    if end_date:
        df = df[df["date"] <= pd.Timestamp(end_date)]
    piv = df.pivot_table(index="date", columns="name", values="close").sort_index()
    return piv


def load_sw_amount(end_date: str | None) -> pd.DataFrame:
    """读取申万指数**成交额**宽表(index=日期, columns=行业名), 用于量能比。
    量能比 = 20日均额 / 120日均额: >1 近期放量(资金介入), <1 缩量(关注度退潮)。"""
    if not SW_CACHE.exists():
        raise RuntimeError(f"申万行业指数缓存不存在: {SW_CACHE}")
    df = pd.read_parquet(SW_CACHE)
    df["date"] = pd.to_datetime(df["date"])
    if end_date:
        df = df[df["date"] <= pd.Timestamp(end_date)]
    return df.pivot_table(index="date", columns="name", values="amount").sort_index()


def load_fundflow(end_date: str | None):
    """读取申万行业**主力净流入**宽表(单位元, index=日期, columns=行业名)。
    数据源 westockdata `fund flow`(板块级, 主力/散户两分桶, 主力净额+散户净额=0),
    由 fetch_sw_industry_fundflow.py 拉取维护。缓存缺失时返回 None(资金流列置空)。"""
    if not FF_CACHE.exists():
        return None
    df = pd.read_parquet(FF_CACHE)
    df["date"] = pd.to_datetime(df["date"])
    if end_date:
        df = df[df["date"] <= pd.Timestamp(end_date)]
    return df.pivot_table(index="date", columns="name", values="main_net").sort_index()


def load_benchmarks(end_date: str | None):
    """读取六个基准指数(用户指定)的**收盘价宽表**(index=日期, columns=指数名)。
    用途: 各基准与行业**独立对比**(不合成), 每个行业输出 6 列超额(vs各风格指数)。
    数据源: 本地 QUANT_DATA_ROOT/stock-main-index-data/。缺失时返回 None(超额列退化为绝对涨幅)。"""
    root = os.environ.get("QUANT_DATA_ROOT")
    if not root:
        return None
    frames = {}
    for code, name in BENCH_CODES.items():
        p = Path(root) / "stock-main-index-data" / f"{code}.csv"
        if not p.exists():
            continue
        df = pd.read_csv(p)
        df["date"] = pd.to_datetime(df["candle_end_time"])
        if end_date:
            df = df[df["date"] <= pd.Timestamp(end_date)]
        frames[name] = df.set_index("date")["close"].sort_index()
    if not frames:
        return None
    return pd.DataFrame(frames).sort_index()


def classify_row(p, m):
    """p=指数最新值, m={5:MA5,...}。
    返回 (分类, 多头计数(0-5), 排列描述, 趋势得分)。
    趋势得分 = 各MA偏离度的均值*100 (正=价格整体在MA上方, 偏多头; 负=偏空)。
    """
    above = {w: p > m[w] for w in MAS}
    up_count = sum(above.values())
    asc = m[5] > m[10] > m[20] > m[60] > m[120]
    desc = m[5] < m[10] < m[20] < m[60] < m[120]
    golden = m[5] > m[20] > m[60]          # 短中期金叉(多头)
    death = m[5] < m[20] < m[60]           # 短中期死叉(空头)
    mas_vals = [m[w] for w in MAS]
    spread = (max(mas_vals) - min(mas_vals)) / p
    score = round(np.mean([(p - m[w]) / m[w] for w in MAS]) * 100, 1)

    if asc and above[120]:
        return "强势多头", up_count, "多头排列", score
    if desc and not above[5]:
        return "强势空头", up_count, "空头排列", score
    # 价格站上中/长MA, 且短中期金叉 → 多头
    if above[20] and above[120] and golden:
        return "多头", up_count, "中长多·金叉", score
    # 价格站上中/长MA, 但短期回踩(破MA5) → 多头回踩
    if above[20] and above[120] and not above[5]:
        return "多头回踩", up_count, "长多·短调", score
    # 价格低于长MA(MA120), 但站上短/中MA → 筑底反转
    if (not above[120]) and above[5] and above[10] and above[20]:
        return "筑底反转", up_count, "短强长弱", score
    # 价格低于长MA(MA120), 站上MA20但短期回踩(破MA5) → 筑底回踩(底部反弹中的回踩, 多头回踩的底部版)
    if (not above[120]) and above[20] and (not above[5]):
        return "筑底回踩", up_count, "短强长弱·回踩", score
    # 价格低于长MA, 仅短MA反弹 → 空头反弹
    if (not above[120]) and above[5] and above[10] and (not above[20]):
        return "空头反弹", up_count, "短弹长压", score
    # 价格跌破中/长MA, 短中期死叉 → 空头
    if (not above[20]) and (not above[120]) and death:
        return "空头", up_count, "中长空·死叉", score
    if spread < 0.03:
        return "震荡粘合", up_count, "均线粘合", score
    return "震荡", up_count, "无排列", score


def trend_stage(cat, pos250, pos3y, pos5y, r2_20):
    """方向分类(cat)之后补一层'趋势阶段', 区分'主升多头'与'底部启动多头'等。
    多头类(强势/多头)用双周期闸门(原版, 勿改): min(pos250, pos3y) >= 70% 才算主升
      —— 250日与3年区间位置都高才是'真主升'(成熟多头, 已脱离长期底部);
      任一周期不足 70% 即为'底部启动'(站上中长均线、短期反弹, 但离长期高位尚远
      —— 医药生物即典型: 2021见顶后深度回调, 250日位置尚在 70% 下方)。
    pos5y 仅作参考展示, 不参与闸门判定。
    其余类直接映射阶段。"""
    if cat in ("强势多头", "多头"):
        if min(pos250, pos3y) >= 0.70:
            return "主升"
        return "底部启动"
    if cat == "多头回踩":
        return "回踩"
    if cat == "筑底反转":
        return "筑底"
    if cat == "筑底回踩":
        return "筑底回踩"
    if cat == "空头反弹":
        return "下跌中继"
    if cat in ("空头", "强势空头"):
        return "主跌"
    if cat in ("震荡粘合", "震荡"):
        return "横盘"
    return "-"


def _regression_trend(close: pd.Series, window: int):
    """行业指数回归趋势: 返回 (mom, r2) 两个全序列 Series。
    算法同 DM-kun 中等生·选股_回归动量(_regression_momentum): log价对 0..N-1 时间轴
    滚动 OLS, mom=expm1(斜率*(N-1))(区间收益), r2=corr(logp,x)^2(拟合优度=趋势稳定度)。
    R² 对 x 平移不变, 用 rolling cov/corr 向量化, 与逐窗 OLS 数值等价。"""
    logp = np.log(close.where(close > 0))
    x = pd.Series(np.arange(len(logp), dtype=float), index=logp.index)
    var_x = window * (window + 1) / 12.0
    slope = logp.rolling(window, min_periods=int(window * 0.85)).cov(x) / var_x
    r2 = logp.rolling(window, min_periods=int(window * 0.85)).corr(x) ** 2
    mom = np.expm1(slope * (window - 1))
    return mom, r2


def _pick(s, ind):
    """从末行 Series(行业索引) 安全取值: 行业缺失/NaN → NaN。"""
    if s is None or ind not in s.index:
        return np.nan
    v = s[ind]
    return float(v) if not pd.isna(v) else np.nan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tail", type=int, default=300)
    ap.add_argument("--date", type=str, default=None)
    ap.add_argument("--out", type=str,
                    default=str(QUANT / "industry_ma_trend"))
    args = ap.parse_args()

    end_date = args.date
    print(f"[加载] 请求截止={end_date or '数据最大日'}, tail={args.tail} 交易日")

    # ---- 行业指数: 申万官方指数(位置/趋势/分类的基准, 无成分回溯偏差) ----
    idx_long = load_sw_index(end_date)
    if idx_long.empty:
        raise RuntimeError("申万行业指数缓存为空")
    amt_long = load_sw_amount(end_date)      # 成交额宽表(量能比用)
    bench_df = load_benchmarks(end_date)     # 六基准收盘宽表(独立对比用)
    ff_df = load_fundflow(end_date)          # 主力净流入宽表(元, 资金流列用)
    # ---- 本地个股数据: 仅用于广度(站上MA20占比)与形态净强度(个股层指标) ----
    # 等权收益 piv 不再用于建指数, 仅加载以复用既有函数(单次读盘, 不重复加载)
    _eq_piv, breadth, pat = load_industry_returns(args.tail, end_date)
    # 当日个股层聚合: 涨家数/跌家数/涨停/跌停/大肉/大面/领涨市值/领涨股 (8列)
    try:
        daily_cs = load_daily_cross_section(end_date)
        print(f"[加载] 当日截面聚合: {len(daily_cs)} 行业 (个股层)")
    except Exception as exc:
        print(f"[警告] 当日截面聚合失败({exc}), 8列当日指标将置空")
        daily_cs = pd.DataFrame()
    # 僵尸过滤: 本地数据不足(退市/旧标签)的行业, 广度/形态列剔除(主循环有默认值兜底)
    min_obs = int(120 * 0.85)  # 与 MA120 的 min_periods 一致
    valid = breadth.notna().sum()
    dropped = [c for c in breadth.columns if valid[c] < min_obs]
    if dropped:
        breadth = breadth.drop(columns=dropped)
        pat = pat.drop(columns=dropped)
        print(f"[过滤] 剔除 {len(dropped)} 个本地数据不足行业的广度/形态列: {dropped}")
    # 长周期高低点: 直接用官方指数收盘价(真实点位), 滚动高低点/位置即真实历史区间。
    # 医药 2021-06~10 大顶在官方指数序列内(缓存自 2021-08 起), 5年区间高点会被如实计入。
    HL_WIN = 250
    hi250 = idx_long.rolling(HL_WIN, min_periods=int(HL_WIN * 0.8)).max()
    lo250 = idx_long.rolling(HL_WIN, min_periods=int(HL_WIN * 0.8)).min()
    rng250 = (hi250 - lo250).replace(0, np.nan)
    pos250 = ((idx_long - lo250) / rng250).iloc[-1]        # 0=区间底, 1=区间顶
    dd250 = ((idx_long / hi250 - 1) * 100).iloc[-1]         # 距250日高点回撤%
    rb250 = ((idx_long / lo250 - 1) * 100).iloc[-1]         # 距250日低点反弹%
    # 3 年(750交易日)维度: 同一逻辑、更长窗口, 区分"年高附近但仍在3年低位(长期下跌中的强反弹)"与"真正跨周期多头"
    HL3Y = 750
    hi3y = idx_long.rolling(HL3Y, min_periods=int(HL3Y * 0.8)).max()
    lo3y = idx_long.rolling(HL3Y, min_periods=int(HL3Y * 0.8)).min()
    rng3y = (hi3y - lo3y).replace(0, np.nan)
    pos3y = ((idx_long - lo3y) / rng3y).iloc[-1]            # 0=3年区间底, 1=3年区间顶
    dd3y = ((idx_long / hi3y - 1) * 100).iloc[-1]           # 距3年高点回撤%
    rb3y = ((idx_long / lo3y - 1) * 100).iloc[-1]           # 距3年低点反弹%
    # 5 年(1320交易日≈5.2年)维度: 同一逻辑、最长窗口, 与 250日/3年 横向对比, 看跨周期位置。
    # 取 1320 而非 1260: 1260≈5.0年 从今(2026-08)仅回溯到 2021-08, 会漏掉 2021-06 医药大顶,
    # 导致 5年高/低/位置 失真(把"长期下跌中途"误判为"近5年高位")。1320 安全覆盖 2021-06。
    HL5Y = 1320
    hi5y = idx_long.rolling(HL5Y, min_periods=int(HL5Y * 0.8)).max()
    lo5y = idx_long.rolling(HL5Y, min_periods=int(HL5Y * 0.8)).min()
    rng5y = (hi5y - lo5y).replace(0, np.nan)
    pos5y = ((idx_long - lo5y) / rng5y).iloc[-1]            # 0=5年区间底, 1=5年区间顶
    dd5y = ((idx_long / hi5y - 1) * 100).iloc[-1]           # 距5年高点回撤%
    rb5y = ((idx_long / lo5y - 1) * 100).iloc[-1]           # 距5年低点反弹%
    # 主指数: 官方指数最近 300 交易日的真实收盘价(即申万指数点位, MA 直接用收盘价)
    idx = idx_long.tail(300)
    breadth, pat = breadth.tail(300), pat.tail(300)
    if end_date is None:
        end_date = str(idx_long.index.max().date())
    print(f"[构建] 实际截止={end_date}, 行业数={idx.shape[1]}(申万官方指数), "
          f"主窗口={len(idx)}日, 长窗口={len(idx_long)}日")
    # 每个窗口的滚动均值都是 (日期 x 行业) 的 DataFrame, 用字典存, 再取末行
    ma = {w: idx.rolling(w, min_periods=int(w * 0.85)).mean() for w in MAS}
    last = idx.iloc[-1]
    last_ma = pd.DataFrame({f"MA{w}": ma[w].iloc[-1] for w in MAS})
    # 各周期涨幅(申万官方指数 trailing return, %): N 个交易日前到今日的累计收益
    RET_W = [5, 20, 60, 120]
    rets = {w: idx.pct_change(w).iloc[-1] * 100 for w in RET_W}  # 末行 Series, 按行业索引
    # 各基准20日涨幅(独立对比): 行业20日涨幅 - 各基准20日涨幅 = 相对该风格指数的超额
    ret20_bench = {}
    if bench_df is not None:
        for cname in BENCH_NAMES:
            if cname in bench_df.columns:
                s = bench_df[cname].dropna()
                if len(s) >= 21:
                    ret20_bench[cname] = float(s.pct_change(20).iloc[-1] * 100)
    if ret20_bench:
        print("[基准20日] " + "  ".join(f"{k}{v:+.1f}%" for k, v in ret20_bench.items()))
    # 量能比: 官方指数 20日均成交额 / 120日均成交额 (>1.2 放量, <0.8 缩量) —— 慢档水位
    amt20 = amt_long.rolling(20, min_periods=17).mean().iloc[-1]
    amt120 = amt_long.rolling(120, min_periods=102).mean().iloc[-1]
    vol_ratio = amt20 / amt120
    # 量能比5: 5日均成交额 / 120日均成交额 —— 快档启动探测器(20日档滞后, 近一周的
    # 量能突变会错过; 快档穿越慢档 = 量能拐点)。毛刺多(单周脉冲), 与慢档互证用。
    amt5 = amt_long.rolling(5, min_periods=5).mean().iloc[-1]
    vol_ratio5 = amt5 / amt120
    # 资金流(主力净额, 元) —— 三档统一"加总净额 ÷ 加总成交额"口径(按钱加权):
    # 逐日净占比再平均会让地量日的极端占比与大成交日等权, 加总相除无此污染。
    # 当日 = 事件警报灯(单日噪声大, 只看|净占比|>=3%的显著值);
    # 5日 = 快档; 20日 = 慢档水位; Δ = 5日净占比 - 20日净占比(加速度, 抓"表面
    # 温和实际被抛"的行业)。ff 与 amt 按公共交易日对齐, 保证分子分母同日。
    ffd = ffd_pct = ff5 = ff5_pct = ff20 = ff_ratio = ff_delta = None
    if ff_df is not None and not ff_df.empty:
        common = ff_df.index.intersection(amt_long.index)
        ff_a, amt_a = ff_df.loc[common], amt_long.loc[common]
        ffd = ff_a.iloc[-1] / 1e8                                                          # 当日亿
        ffd_pct = ff_a.iloc[-1] / amt_a.iloc[-1] * 100                                     # 当日净占比%
        ff5 = ff_a.rolling(5, min_periods=5).sum().iloc[-1] / 1e8                          # 5日亿
        ff5_pct = (ff_a.rolling(5, min_periods=5).sum().iloc[-1]
                   / amt_a.rolling(5, min_periods=5).sum().iloc[-1] * 100)                 # 5日净占比%
        ff20 = ff_a.rolling(20, min_periods=17).sum().iloc[-1] / 1e8                       # 20日亿
        ff_ratio = (ff_a.rolling(20, min_periods=17).sum().iloc[-1]
                    / amt_a.rolling(20, min_periods=17).sum().iloc[-1] * 100)              # 20日净占比%
        ff_delta = ff5_pct - ff_ratio                                                      # 资金流Δ(pp)
        top = ff_delta.dropna().sort_values(ascending=False)
        if len(top):
            print("[资金流Δpp(5日-20日净占比)] 改善前3: "
                  + "  ".join(f"{k}{v:+.1f}" for k, v in top.head(3).items())
                  + " | 恶化前3: " + "  ".join(f"{k}{v:+.1f}" for k, v in top.tail(3).items()))
        alert = ffd_pct.dropna()
        alert = alert[alert.abs() >= 3]
        if len(alert):
            print("[当日资金警报|净占比>=3%] "
                  + "  ".join(f"{k}{v:+.1f}%" for k, v in alert.items()))
    # 广度末值与20日前值(个股站上MA20占比): 末值看水位, 差值看方向(资金进/出个股层面)
    br_last = breadth.iloc[-1]
    br_20ago = breadth.iloc[-21] if len(breadth) >= 21 else None
    # 回归趋势(含R²): 20/60日窗口, 末值按行业索引。R²=趋势稳定度权重(中等生·回归动量原味)
    _mom20, _r2_20 = _regression_trend(idx, 20)
    _mom60, _r2_60 = _regression_trend(idx, 60)
    reg_mom = 0.6 * (_mom20 * _r2_20) + 0.4 * (_mom60 * _r2_60)   # 末行 Series(行业)
    r2_last = _r2_20.iloc[-1]                                      # 末行 Series(行业)
    rows = []
    for ind in idx.columns:
        p = last[ind]
        m = {w: last_ma[f"MA{w}"][ind] for w in MAS}
        if pd.isna(m[120]):
            cat, uc, align, score = "数据不足", 0, "-", 0.0
            br = 0.5
            pat_net = 0.0
            dist120 = 0.0
        else:
            cat, uc, align, score = classify_row(p, m)
            br = float(br_last[ind]) if ind in br_last.index else 0.5
            pat_net = float(pat.iloc[-1][ind]) if ind in pat.columns else 0.0
            dist120 = (p - m[120]) / m[120] * 100
        # 新增列: 广度20日变化 / 超额 / 量能比
        br20_v = float(br_20ago[ind]) if (br_20ago is not None and ind in br_20ago.index and not pd.isna(br_20ago[ind])) else np.nan
        vr_v = float(vol_ratio[ind]) if (ind in vol_ratio.index and not pd.isna(vol_ratio[ind])) else np.nan
        vr5_v = _pick(vol_ratio5, ind)
        ffd_v = _pick(ffd, ind)        # 当日主力净额(亿)
        ffdp_v = _pick(ffd_pct, ind)   # 当日净占比%
        ff5_v = _pick(ff5, ind)        # 5日净额(亿)
        ff5p_v = _pick(ff5_pct, ind)   # 5日净占比%
        ff20_v = _pick(ff20, ind)      # 20日净额(亿)
        ffr_v = _pick(ff_ratio, ind)   # 20日净占比%
        ffdlt_v = _pick(ff_delta, ind) # 资金流Δ = 5日净占比 - 20日净占比(pp)
        # ---- K线形态标签: 净强度(上升占比-下降占比)阈值映射 ----
        pat_label = "上升" if pat_net > 0.15 else ("下降" if pat_net < -0.15 else "调整")
        # ---- 趋势强度 SI(0-100): 方向基准+偏离位置+动量斜率+广度+形态+长期位置+中期动量 ----
        dir_base = {"强势多头": 15, "多头": 15, "多头回踩": 5, "筑底反转": 0,
                    "筑底回踩": 0,
                    "震荡粘合": 0, "震荡": 0, "空头反弹": -5, "空头": -15,
                    "强势空头": -15, "数据不足": 0}.get(cat, 0)
        dev = max(-15.0, min(15.0, score))                      # 维度2 偏离位置
        reg_v = float(reg_mom[ind]) if ind in reg_mom.index else 0.0   # 回归动量(斜率×R², 双窗口)
        r2_v = float(r2_last[ind]) if ind in r2_last.index else 0.0    # 趋势R²(稳定度)
        breadth_comp = (br - 0.5) * 30                          # 维度4 广度(0-1→±15)
        pattern_comp = pat_net * 10.0                           # 维度5 形态净强度(-1~1→±10)
        slope = max(-15.0, min(15.0, 60.0 * reg_v))             # 维度3 回归动量(R²加权斜率)
        # 维度6 长期位置: 3年/5年位置均值(0底~1顶), 长期低位加分(底部反弹空间大)
        pos3y_v0 = float(pos3y[ind]) if ind in pos3y.index else 0.5
        pos5y_v0 = float(pos5y[ind]) if ind in pos5y.index else 0.5
        long_pos = (pos3y_v0 + pos5y_v0) / 2
        long_pos_comp = (long_pos - 0.5) * 20                   # ±10
        # 维度7 中期动量: 20日涨幅(%)归一化 ±15% → ±10
        ret20_v = float(rets[20][ind]) if ind in rets[20].index else 0.0
        ret20_comp = max(-10.0, min(10.0, ret20_v / 15 * 10))
        # 维度8 偏离MA60: 距MA60% ±20% → ±10 (过热减分/过冷加分)
        dist60 = (p - m[60]) / m[60] * 100
        dist60_comp = max(-10.0, min(10.0, -dist60 / 20 * 10))   # 负号: 高于MA60减分
        si = max(0.0, min(100.0, 50 + dir_base + dev + slope + breadth_comp + pattern_comp
                          + long_pos_comp + ret20_comp + dist60_comp))
        # ---- 当日动能分(0-100): 当日涨幅+涨家数%+大肉%+大面%+资金流Δ+量能比5+当日净占比+5日净占比+涨停+领涨市值 ----
        cs0 = daily_cs.loc[ind] if (not daily_cs.empty and ind in daily_cs.index) else None
        def _cs0(col, default=np.nan):
            return cs0[col] if cs0 is not None else default
        day_ret = _cs0("当日涨幅%")
        up_ratio = _cs0("涨家数%")
        meat = _cs0("大肉%")
        mian = _cs0("大面%")
        zt = _cs0("涨停数")
        lead_cap = _cs0("领涨市值", "-")
        ff5p_v0 = _pick(ff5_pct, ind)   # 5日净占比%
        # 归一化到 [-1,1] 再映射: 10 个维度各 ±10 权重
        _dr = max(-1.0, min(1.0, (day_ret if pd.notna(day_ret) else 0) / 3))       # 当日涨幅 ±3%
        _ur = max(-1.0, min(1.0, ((up_ratio if pd.notna(up_ratio) else 50) - 50) / 30))  # 涨家数% 50±30
        _mt = max(-1.0, min(1.0, ((meat if pd.notna(meat) else 0) - 2) / 5))        # 大肉% 2±5
        _mn = max(-1.0, min(1.0, -((mian if pd.notna(mian) else 0) - 2) / 5))       # 大面% 2±5 (负号: 大面多减分)
        _ff = max(-1.0, min(1.0, (ffdlt_v if pd.notna(ffdlt_v) else 0) / 2))        # 资金流Δ ±2pp
        _vr = max(-1.0, min(1.0, ((vr5_v if pd.notna(vr5_v) else 1) - 1) / 0.5))    # 量能比5 1±0.5
        _fn = max(-1.0, min(1.0, (ffdp_v if pd.notna(ffdp_v) else 0) / 3))          # 当日净占比 ±3%
        _f5 = max(-1.0, min(1.0, (ff5p_v0 if pd.notna(ff5p_v0) else 0) / 2))        # 5日净占比 ±2%
        _zt = max(-1.0, min(1.0, ((zt if pd.notna(zt) else 0) - 1) / 2))            # 涨停数 1±2
        _lc = {"大市值": 1.0, "中市值": 0.3, "小市值": -0.5, "-": 0}.get(lead_cap, 0)  # 领涨市值结构
        momentum = max(0.0, min(100.0, 50 + (_dr + _ur + _mt + _mn + _ff + _vr + _fn + _f5 + _zt + _lc) * 2))
        # ---- 综合分: SI 80% + 当日动能 20% ----
        composite = round(0.8 * si + 0.2 * momentum, 1)
        pos_v = float(pos250[ind]) if ind in pos250.index else 0.5       # 250日区间位置(0底~1顶)
        dd250_v = float(dd250[ind]) if ind in dd250.index else 0.0        # 距250日高点回撤%
        rb250_v = float(rb250[ind]) if ind in rb250.index else 0.0        # 距250日低点反弹%
        pos3y_v = float(pos3y[ind]) if ind in pos3y.index else 0.5        # 3年区间位置(0底~1顶)
        dd3y_v = float(dd3y[ind]) if ind in dd3y.index else 0.0           # 距3年高点回撤%
        rb3y_v = float(rb3y[ind]) if ind in rb3y.index else 0.0           # 距3年低点反弹%
        pos5y_v = float(pos5y[ind]) if ind in pos5y.index else 0.5        # 5年区间位置(0底~1顶)
        dd5y_v = float(dd5y[ind]) if ind in dd5y.index else 0.0           # 距5年高点回撤%
        rb5y_v = float(rb5y[ind]) if ind in rb5y.index else 0.0           # 距5年低点反弹%
        stage = trend_stage(cat, pos_v, pos3y_v, pos5y_v, r2_v)   # 趋势阶段(主升/底部启动/...)
        # 当日个股层 8 列 (从 daily_cs 按行业取)
        cs = daily_cs.loc[ind] if (not daily_cs.empty and ind in daily_cs.index) else None
        def _cs(col, default=np.nan):
            return cs[col] if cs is not None else default
        rows.append({
            "行业": ind,
            "分类": cat,
            "阶段": stage,
            "趋势得分": score,
            "趋势强度": round(si, 1),
            "当日动能": round(momentum, 1),
            "综合分": composite,
            "形态": pat_label,
            "形态净值": round(pat_net, 2),
            "广度%": round(br * 100, 0),
            "广度20日Δ": round((br - br20_v) * 100, 0) if not pd.isna(br20_v) else np.nan,
            "趋势R²": round(r2_v, 2),
            "250位置%": round(pos_v * 100, 0),
            "距250高%": round(dd250_v, 1),
            "距250低%": round(rb250_v, 1),
            "3年位置%": round(pos3y_v * 100, 0),
            "距3年高%": round(dd3y_v, 1),
            "距3年低%": round(rb3y_v, 1),
            "5年位置%": round(pos5y_v * 100, 0),
            "距5年高%": round(dd5y_v, 1),
            "距5年低%": round(rb5y_v, 1),
            "指数": round(p, 1),
            "MA5": round(m[5], 1),
            "MA10": round(m[10], 1),
            "MA20": round(m[20], 1),
            "MA45": round(m[45], 1),
            "MA60": round(m[60], 1),
            "MA90": round(m[90], 1),
            "MA120": round(m[120], 1),
            "距MA60%": round((p - m[60]) / m[60] * 100, 1),
            "距MA120%": round((p - m[120]) / m[120] * 100, 1),
            "5日涨幅": round(rets[5][ind], 1),
            "20日涨幅": round(rets[20][ind], 1),
            "60日涨幅": round(rets[60][ind], 1),
            "120日涨幅": round(rets[120][ind], 1),
            "量能比": round(vr_v, 2) if not pd.isna(vr_v) else np.nan,
            "量能比5": round(vr5_v, 2) if not pd.isna(vr5_v) else np.nan,
            "主力当日(亿)": round(ffd_v, 1) if not pd.isna(ffd_v) else np.nan,
            "当日净占比%": round(ffdp_v, 1) if not pd.isna(ffdp_v) else np.nan,
            "主力5日(亿)": round(ff5_v, 1) if not pd.isna(ff5_v) else np.nan,
            "5日净占比%": round(ff5p_v, 1) if not pd.isna(ff5p_v) else np.nan,
            "主力20日(亿)": round(ff20_v, 1) if not pd.isna(ff20_v) else np.nan,
            "主力净占比%": round(ffr_v, 1) if not pd.isna(ffr_v) else np.nan,
            "资金流Δ": round(ffdlt_v, 2) if not pd.isna(ffdlt_v) else np.nan,
            # ── 当日个股层 (时效性) ──
            "当日涨幅%": _cs("当日涨幅%"),
            "当日等权涨幅%": round(float(_eq_piv.iloc[-1].get(ind, np.nan)), 1) if (not _eq_piv.empty and ind in _eq_piv.columns) else np.nan,
            "涨家数": _cs("涨家数"),
            "跌家数": _cs("跌家数"),
            "涨家数%": _cs("涨家数%"),
            "涨停数": _cs("涨停数"),
            "跌停数": _cs("跌停数"),
            "大肉%": _cs("大肉%"),
            "大面%": _cs("大面%"),
            "领涨市值": _cs("领涨市值", "-"),
            "领涨股": _cs("领涨股", "-"),
            **{f"vs{cname}%": round(rets[20][ind] - bv, 1)
               for cname, bv in ret20_bench.items()},
            "多头计数": uc,
            "排列": align,
        })

    res = pd.DataFrame(rows)
    # 分类排序优先级 (多头→空头)
    order = {"强势多头": 0, "多头": 1, "多头回踩": 2, "筑底反转": 3, "筑底回踩": 4,
             "震荡粘合": 5, "震荡": 6, "空头反弹": 7, "空头": 8,
             "强势空头": 9, "数据不足": 10}
    res["__o"] = res["分类"].map(order)
    res = res.sort_values(["__o", "综合分"], ascending=[True, False]).drop(columns="__o")

    out_csv = args.out + ".csv"
    out_html = args.out + ".html"
    res.to_csv(out_csv, index=False, encoding="utf-8-sig")

    # ---- HTML ----
    summary = (res[res["分类"] != "数据不足"]["分类"]
               .value_counts()               .reindex(
                   ["强势多头", "多头", "多头回踩", "筑底反转", "筑底回踩",
                    "震荡粘合", "震荡", "空头反弹", "空头", "强势空头"])
               .dropna())
    color_map = {
        "强势多头": "#1D9E75", "多头": "#5DCAA5", "多头回踩": "#9FE1CB",
        "筑底反转": "#BA7517", "筑底回踩": "#CE9B54", "震荡粘合": "#888780", "震荡": "#B4B2A9",
        "空头反弹": "#F0997B", "空头": "#D85A30", "强势空头": "#A32D2D",
        "数据不足": "#5F5E5A",
    }
    sum_html = " · ".join(
        f'<span style="color:{color_map.get(k,"#000")};font-weight:600">{k} {v}</span>'
        for k, v in summary.items())
    def _chg_color(v):
        if pd.isna(v):
            return "#666"
        if v > 0:
            return "#d8392b"   # 涨 红
        if v < 0:
            return "#1a9e5f"   # 跌 绿
        return "#666"
    def _ffd_color(v):
        # 当日净占比噪声大, 只当"警报灯": |净占比|>=3% 才着色, 其余灰
        if pd.isna(v):
            return "#666"
        if v >= 3:
            return "#d8392b"
        if v <= -3:
            return "#1a9e5f"
        return "#666"
    def _si_color(v):
        if pd.isna(v):
            return "#666"
        if v >= 60:
            return "#1D9E75"   # 强 teal
        if v >= 40:
            return "#888780"   # 中 gray
        return "#D85A30"       # 弱 coral
    def _pat_color(v):
        if v == "上升":
            return "#1D9E75"   # teal
        if v == "下降":
            return "#D85A30"   # coral
        return "#888780"       # gray 调整
    def _r2_color(v):
        if pd.isna(v):
            return "#666"
        if v >= 0.5:
            return "#1D9E75"   # 强趋势稳定 teal
        if v >= 0.3:
            return "#888780"   # 中 gray
        return "#D85A30"       # 弱/震荡 coral
    def _pos_color(v):
        if pd.isna(v):
            return "#666"
        if v >= 70:
            return "#1D9E75"   # 贴近年高 teal
        if v <= 30:
            return "#D85A30"   # 贴近年低 coral
        return "#888780"       # 区间中部 gray
    def _br_color(v):
        if pd.isna(v):
            return "#666"
        if v >= 60:
            return "#1D9E75"   # 广度高(多数个股走强) teal
        if v <= 40:
            return "#D85A30"   # 广度低 coral
        return "#888780"
    def _vr_color(v):
        if pd.isna(v):
            return "#666"
        if v >= 1.2:
            return "#1D9E75"   # 放量 teal
        if v <= 0.8:
            return "#D85A30"   # 缩量 coral
        return "#888780"
    stage_color = {
        "主升": "#1D9E75", "底部启动": "#BA7517", "底部": "#C99A3E",
        "回踩": "#9FE1CB", "筑底": "#BA7517", "筑底回踩": "#CE9B54", "下跌中继": "#F0997B",
        "主跌": "#D85A30", "横盘": "#888780", "-": "#666",
    }
    def _stage_color(v):
        return stage_color.get(v, "#666")

    bench_cols = [f"vs{c}%" for c in ret20_bench]   # 独立对比基准列(动态)
    bench_head = "".join(f"<th>vs{c}%</th>" for c in ret20_bench)
    rows_html = ""
    for _, r in res.iterrows():
        c = color_map.get(r["分类"], "#000")
        rows_html += (
            f"<tr><td>{r['行业']}</td>"
            f'<td style="color:{c};font-weight:600">{r["分类"]}</td>'
            f'<td style="color:{_stage_color(r["阶段"])};font-weight:600">{r["阶段"]}</td>'
            f"<td>{r['趋势得分']}</td>"
            f"<td style='color:{_si_color(r['趋势强度'])};font-weight:600'>{r['趋势强度']}</td>"
            f"<td style='color:{_pat_color(r['形态'])}'>{r['形态']}</td>"
            f"<td>{r['形态净值']}</td>"
            f"<td style='color:{_br_color(r['广度%'])};font-weight:600'>{r['广度%']}</td>"
            f"<td style='color:{_chg_color(r['广度20日Δ'])}'>{r['广度20日Δ']}</td>"
            f"<td style='color:{_r2_color(r['趋势R²'])}'>{r['趋势R²']}</td>"
            f"<td style='color:{_pos_color(r['250位置%'])};font-weight:600'>{r['250位置%']}</td>"
            f"<td>{r['距250高%']}</td><td>{r['距250低%']}</td>"
            f"<td style='color:{_pos_color(r['3年位置%'])};font-weight:600'>{r['3年位置%']}</td>"
            f"<td>{r['距3年高%']}</td><td>{r['距3年低%']}</td>"
            f"<td style='color:{_pos_color(r['5年位置%'])};font-weight:600'>{r['5年位置%']}</td>"
            f"<td>{r['距5年高%']}</td><td>{r['距5年低%']}</td>"
            f"<td>{r['指数']}</td><td>{r['MA5']}</td><td>{r['MA10']}</td>"
            f"<td>{r['MA20']}</td><td>{r['MA45']}</td><td>{r['MA60']}</td><td>{r['MA90']}</td><td>{r['MA120']}</td>"
            f"<td>{r['距MA60%']}</td><td>{r['距MA120%']}</td>"
            f"<td style='color:{_chg_color(r['5日涨幅'])}'>{r['5日涨幅']}</td>"
            f"<td style='color:{_chg_color(r['20日涨幅'])}'>{r['20日涨幅']}</td>"
            f"<td style='color:{_chg_color(r['60日涨幅'])}'>{r['60日涨幅']}</td>"
            f"<td style='color:{_chg_color(r['120日涨幅'])}'>{r['120日涨幅']}</td>"
            f"<td style='color:{_vr_color(r['量能比'])};font-weight:600'>{r['量能比']}</td>"
            f"<td style='color:{_vr_color(r['量能比5'])}'>{r['量能比5']}</td>"
            f"<td style='color:{_ffd_color(r['当日净占比%'])}'>{r['主力当日(亿)']}</td>"
            f"<td style='color:{_ffd_color(r['当日净占比%'])}'>{r['当日净占比%']}</td>"
            f"<td style='color:{_chg_color(r['主力5日(亿)'])}'>{r['主力5日(亿)']}</td>"
            f"<td style='color:{_chg_color(r['5日净占比%'])}'>{r['5日净占比%']}</td>"
            f"<td style='color:{_chg_color(r['主力20日(亿)'])};font-weight:600'>{r['主力20日(亿)']}</td>"
            f"<td style='color:{_chg_color(r['主力净占比%'])};font-weight:600'>{r['主力净占比%']}</td>"
            f"<td style='color:{_chg_color(r['资金流Δ'])};font-weight:600'>{r['资金流Δ']}</td>"
            + "".join(f"<td style='color:{_chg_color(r[c])}'>{r[c]}</td>" for c in bench_cols)
            + f"<td>{r['多头计数']}</td><td>{r['排列']}</td></tr>"
        )
    html = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>行业趋势均线分类</title>
<style>
body{{font-family:-apple-system,"PingFang SC",Segoe UI,sans-serif;margin:24px;color:#222;background:#fafafa}}
h1{{font-size:18px}} .meta{{color:#666;font-size:13px;margin:4px 0 16px}}
.sum{{font-size:15px;margin:12px 0 20px}}
table{{border-collapse:collapse;width:100%;font-size:13px;background:#fff}}
th,td{{border:1px solid #e3e3e3;padding:6px 8px;text-align:right}}
th{{background:#f2f2f2;position:sticky;top:0}} td:first-child,th:first-child{{text-align:left}}
tr:nth-child(even){{background:#f7f7f7}}
</style></head><body>
<h1>行业趋势均线系统分类</h1>
<div class="meta">截止 {end_date} · 样本 {len(idx)} 交易日 · 均线 MA5/10/20/60/120 (申万官方一级行业指数) · 广度/形态来自本地个股(后复权) · 资金流为板块级主力净额(腾讯自选股)</div>
<div class="sum">分类汇总: {sum_html}</div>
<table>
<tr><th>行业</th><th>分类</th><th>阶段</th><th>趋势得分</th><th>趋势强度</th><th>形态</th><th>形态净值</th><th>广度%</th><th>广度20日Δ</th><th>趋势R²</th><th>250位置%</th><th>距250高%</th><th>距250低%</th><th>3年位置%</th><th>距3年高%</th><th>距3年低%</th><th>5年位置%</th><th>距5年高%</th><th>距5年低%</th><th>指数</th><th>MA5</th><th>MA10</th><th>MA20</th><th>MA45</th><th>MA60</th><th>MA90</th><th>MA120</th><th>距MA60%</th><th>距MA120%</th><th>5日涨幅</th><th>20日涨幅</th><th>60日涨幅</th><th>120日涨幅</th><th>量能比</th><th>量能比5</th><th>主力当日(亿)</th><th>当日净占比%</th><th>主力5日(亿)</th><th>5日净占比%</th><th>主力20日(亿)</th><th>主力净占比%</th><th>资金流Δ</th>{bench_head}<th>多头计数</th><th>排列</th></tr>
{rows_html}
</table></body></html>"""
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"[输出] {out_csv}")
    print(f"[输出] {out_html}")
    print("\n分类汇总:")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print("\n明细(前15):")
    print(res.head(15).to_string(index=False))


if __name__ == "__main__":
    main()

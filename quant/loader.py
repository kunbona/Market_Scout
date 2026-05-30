"""
quant/loader.py — 本地量价数据读取工具

数据路径: /mnt/ssd_1T/runist/data/Quant_Data/
主数据源: stg_cache/预处理数据/股票预处理数据.parquet（优先）
回退数据源: stock-trading-data-pro/{code}.csv（GBK 编码, skiprows=1）

up_limit/down_limit 已在 stg_cache parquet 预计算，无需重新算。
"""

import logging
import os as _os
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

_data_root_env = _os.environ.get("QUANT_DATA_ROOT", "").strip()
DATA_ROOT = Path(_data_root_env) if _data_root_env else None

# ── stg_cache parquet 路径（动态，跟随 DATA_ROOT 变化）─────────────────────────
def _get_preproc_parquet():
    """动态获取 stg_cache parquet 路径，支持运行时修改 DATA_ROOT。"""
    return DATA_ROOT / "stg_cache" / "预处理数据" / "股票预处理数据.parquet" if DATA_ROOT else None

# 模块级别的别名，供外部（daily_compute.py 的 _prev_trade_date 等）使用
_PREPROC_PARQUET = _get_preproc_parquet()

# ── stg_cache 中文字段 → 英文映射（资金流部分）────────────────────────────────
_FUND_COL_MAP = {
    "机构资金买入额": "inst_buy",
    "机构资金卖出额": "inst_sell",
    "大户资金买入额": "big_buy",
    "大户资金卖出额": "big_sell",
    "中户资金买入额": "mid_buy",
    "中户资金卖出额": "mid_sell",
    "散户资金买入额": "small_buy",
    "散户资金卖出额": "small_sell",
    "新版申万一级行业名称": "industry_l1",
    "新版申万二级行业名称": "industry_l2",
    "新版申万三级行业名称": "industry_l3",
    "name": "name",
}

# ── CSV 列名映射 ─────────────────────────────────────────────────────────────
_TRADING_COL_MAP = {
    "股票代码": "code",
    "股票名称": "stock_name",
    "交易日期": "trade_date",
    "开盘价": "open",
    "最高价": "high",
    "最低价": "low",
    "收盘价": "close",
    "前收盘价": "pre_close",
    "成交量": "vol",
    "成交额": "amount",
    "流通市值": "circ_mv",
    "总市值": "total_mv",
    "新版申万一级行业名称": "industry_l1",
    "新版申万二级行业名称": "industry_l2",
    "新版申万三级行业名称": "industry_l3",
    "机构资金买入额": "inst_buy",
    "机构资金卖出额": "inst_sell",
    "大户资金买入额": "big_buy",
    "大户资金卖出额": "big_sell",
    "中户资金买入额": "mid_buy",
    "中户资金卖出额": "mid_sell",
    "散户资金买入额": "small_buy",
    "散户资金卖出额": "small_sell",
}


def _normalize_code(code: str) -> str:
    """
    将股票代码标准化为带前缀格式 (sh/sz/bj)。
    例如: '600000' -> 'sh600000', '300001' -> 'sz300001', '430418' -> 'bj430418'
    已有前缀则直接返回。
    """
    code = code.strip().lower()
    if code.startswith(("sh", "sz", "bj")):
        return code
    num = code[-6:] if len(code) >= 6 else code
    if num.startswith("6"):
        return "sh" + num
    elif num.startswith(("0", "3")):
        return "sz" + num
    elif num.startswith(("4", "8", "9", "2")):
        return "bj" + num
    else:
        return "sh" + num


# ── 核心函数：日截面快照 ──────────────────────────────────────────────────────


def load_daily_snapshot(trade_date: str, lookback: int = 30) -> pd.DataFrame:
    """
    加载指定交易日的全市场截面数据，返回当日一行一股的 DataFrame。

    优先从 stg_cache/预处理数据/股票预处理数据.parquet 读取（已含 up_limit/down_limit）。
    若 parquet 不存在则回退到逐文件读取 stock-trading-data-pro CSV。

    lookback: 向前取多少个交易日（含当日），用于计算连板次数/滚动均值，建议 >= 30。

    返回列：
        code, name, trade_date, open, high, low, close, pre_close,
        vol, amount, circ_mv, total_mv,
        inst_buy, inst_sell, big_buy, big_sell, mid_buy, mid_sell, small_buy, small_sell,
        industry_l1, industry_l2, industry_l3,
        pct_chg, up_limit, down_limit,
        is_zt, is_zb, is_dt,
        inst_net_pct,
        lianzban_cnt,
        amount_mean_5, amount_mean_20
    """
    try:
        df_window = _load_window(trade_date, lookback)
        if df_window.empty:
            logger.warning("[loader] load_daily_snapshot: %s 无数据", trade_date)
            return pd.DataFrame()

        # ── 计算衍生列 ────────────────────────────────────────────────────────

        # pct_chg（若 stg_cache 已有则复用，否则重算）
        if "pct_chg" not in df_window.columns:
            mask_pc = df_window["pre_close"] > 0
            df_window["pct_chg"] = 0.0
            df_window.loc[mask_pc, "pct_chg"] = (
                df_window.loc[mask_pc, "close"] / df_window.loc[mask_pc, "pre_close"] - 1
            )

        # is_zt / is_zb / is_dt（需要 up_limit/down_limit）
        df_window["is_zt"] = (df_window["close"] >= df_window["up_limit"]).astype(int)
        df_window["is_zb"] = (
            (df_window["high"] >= df_window["up_limit"]) & (df_window["close"] < df_window["up_limit"])
        ).astype(int)
        df_window["is_dt"] = (df_window["close"] <= df_window["down_limit"]).astype(int)

        # inst_net_pct
        inst_sum = df_window["inst_buy"].fillna(0) + df_window["inst_sell"].fillna(0)
        inst_net = df_window["inst_buy"].fillna(0) - df_window["inst_sell"].fillna(0)
        df_window["inst_net_pct"] = inst_net.where(inst_sum > 0, 0.0) / inst_sum.where(inst_sum > 0, 1.0)
        df_window["inst_net_pct"] = df_window["inst_net_pct"].fillna(0.0)

        # amount_mean_5 / amount_mean_20（按股票分组滚动均值）
        df_window = df_window.sort_values(["code", "trade_date"])
        grp = df_window.groupby("code", sort=False)["amount"]
        df_window["amount_mean_5"] = grp.transform(lambda s: s.rolling(5, min_periods=1).mean())
        df_window["amount_mean_20"] = grp.transform(lambda s: s.rolling(20, min_periods=1).mean())

        # lianzban_cnt：状态机推导
        df_window = _compute_lianzban_cnt(df_window)

        # ── 只返回当日截面 ────────────────────────────────────────────────────
        result = df_window[df_window["trade_date"] == trade_date].copy()
        result = result.reset_index(drop=True)

        _output_cols = [
            "code", "name", "trade_date", "open", "high", "low", "close", "pre_close",
            "vol", "amount", "circ_mv", "total_mv",
            "inst_buy", "inst_sell", "big_buy", "big_sell",
            "mid_buy", "mid_sell", "small_buy", "small_sell",
            "industry_l1", "industry_l2", "industry_l3",
            "pct_chg", "up_limit", "down_limit",
            "is_zt", "is_zb", "is_dt",
            "inst_net_pct",
            "lianzban_cnt",
            "amount_mean_5", "amount_mean_20",
        ]
        # 只保留存在的列
        existing = [c for c in _output_cols if c in result.columns]
        result = result[existing]
        return result

    except Exception as e:
        logger.error("[loader] load_daily_snapshot 失败 %s: %s", trade_date, e)
        return pd.DataFrame()


def load_daily_range(trade_date: str, days: int = 25) -> pd.DataFrame:
    """
    返回过去 `days` 个交易日（含当日）的完整多行 DataFrame。

    用于计算需要时间序列的指标（sector_flow_acceleration、advance_decline MA20 等）。
    列名与 load_daily_snapshot 一致，但包含多个 trade_date。
    """
    try:
        lookback = max(days + 5, 30)  # 多取一点以保证够 days 天
        df_window = _load_window(trade_date, lookback)
        if df_window.empty:
            return pd.DataFrame()

        # 只计算基础衍生列（不算 lianzban_cnt/amount_mean_* 以节省时间）
        if "pct_chg" not in df_window.columns:
            mask_pc = df_window["pre_close"] > 0
            df_window["pct_chg"] = 0.0
            df_window.loc[mask_pc, "pct_chg"] = (
                df_window.loc[mask_pc, "close"] / df_window.loc[mask_pc, "pre_close"] - 1
            )

        df_window["is_zt"] = (df_window["close"] >= df_window["up_limit"]).astype(int)
        df_window["is_zb"] = (
            (df_window["high"] >= df_window["up_limit"]) & (df_window["close"] < df_window["up_limit"])
        ).astype(int)
        df_window["is_dt"] = (df_window["close"] <= df_window["down_limit"]).astype(int)

        inst_sum = df_window["inst_buy"].fillna(0) + df_window["inst_sell"].fillna(0)
        inst_net = df_window["inst_buy"].fillna(0) - df_window["inst_sell"].fillna(0)
        df_window["inst_net_pct"] = inst_net.where(inst_sum > 0, 0.0) / inst_sum.where(inst_sum > 0, 1.0)
        df_window["inst_net_pct"] = df_window["inst_net_pct"].fillna(0.0)

        # 只返回 <= trade_date 的最近 days 个交易日
        sorted_dates = sorted(df_window["trade_date"].unique())
        if trade_date in sorted_dates:
            td_idx = sorted_dates.index(trade_date)
            keep_dates = set(sorted_dates[max(0, td_idx - days + 1): td_idx + 1])
            df_window = df_window[df_window["trade_date"].isin(keep_dates)].copy()

        return df_window.reset_index(drop=True)

    except Exception as e:
        logger.error("[loader] load_daily_range 失败 %s: %s", trade_date, e)
        return pd.DataFrame()


# ── 内部辅助：加载 lookback 窗口数据 ─────────────────────────────────────────


def _load_window(trade_date: str, lookback: int) -> pd.DataFrame:
    """
    加载 trade_date 及之前 lookback 个交易日的全市场数据。
    优先 stg_cache parquet，回退到 CSV。
    """
    p = _get_preproc_parquet()
    if p and p.exists():
        return _load_window_from_parquet(trade_date, lookback, p)
    else:
        logger.warning("[loader] stg_cache parquet 不存在，回退到 CSV 模式（较慢）")
        return _load_window_from_csv(trade_date, lookback)


def _load_window_from_parquet(trade_date: str, lookback: int, parquet_path=None) -> pd.DataFrame:
    """从 stg_cache parquet 加载窗口数据并标准化列名。"""
    if parquet_path is None:
        parquet_path = _get_preproc_parquet()
    try:
        # 先只读 trade_date 列，确定窗口日期范围，避免全量加载
        df_dates = pd.read_parquet(
            parquet_path,
            columns=["trade_date"],
        )
        df_dates["trade_date"] = df_dates["trade_date"].astype(str)
        all_dates = sorted(df_dates["trade_date"].unique())

        if trade_date not in all_dates:
            # 找最近可用日期
            close_dates = [d for d in all_dates if d <= trade_date]
            if not close_dates:
                logger.warning("[loader] parquet 无 <= %s 的数据", trade_date)
                return pd.DataFrame()
            trade_date = close_dates[-1]
            logger.warning("[loader] 使用最近可用日期: %s", trade_date)

        td_idx = all_dates.index(trade_date)
        window_dates = set(all_dates[max(0, td_idx - lookback + 1): td_idx + 1])

        # 选取需要的列
        needed_cols = [
            "trade_date", "code", "name", "open", "high", "low", "close", "pre_close",
            "vol", "amount", "circ_mv", "total_mv",
            "机构资金买入额", "机构资金卖出额",
            "大户资金买入额", "大户资金卖出额",
            "中户资金买入额", "中户资金卖出额",
            "散户资金买入额", "散户资金卖出额",
            "新版申万一级行业名称", "新版申万二级行业名称", "新版申万三级行业名称",
            "up_limit", "down_limit",
        ]
        df = pd.read_parquet(parquet_path, columns=needed_cols)
        df["trade_date"] = df["trade_date"].astype(str)
        df = df[df["trade_date"].isin(window_dates)].copy()

        # 重命名资金流和行业列
        df = df.rename(columns=_FUND_COL_MAP)

        # 确保数值列正常
        for col in ["open", "high", "low", "close", "pre_close", "amount", "circ_mv", "total_mv",
                    "up_limit", "down_limit"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        return df

    except Exception as e:
        logger.error("[loader] _load_window_from_parquet 失败: %s", e)
        return pd.DataFrame()


def _load_window_from_csv(trade_date: str, lookback: int) -> pd.DataFrame:
    """回退方案：从 stock-trading-data-pro CSV 批量读取并合并。慢！"""
    try:
        trading_dir = DATA_ROOT / "stock-trading-data-pro"
        if not trading_dir.exists():
            logger.warning("[loader] stock-trading-data-pro 目录不存在")
            return pd.DataFrame()

        all_dfs = []
        for csv_path in trading_dir.glob("*.csv"):
            try:
                df_tmp = pd.read_csv(
                    csv_path, encoding="GBK", skiprows=1,
                    usecols=list(_TRADING_COL_MAP.keys()),
                    dtype={"股票代码": str},
                )
                df_tmp = df_tmp.rename(columns=_TRADING_COL_MAP)
                df_tmp["trade_date"] = df_tmp["trade_date"].astype(str)
                # 先过滤日期（比全量合并后过滤快）
                df_tmp = df_tmp[df_tmp["trade_date"] <= trade_date]
                if df_tmp.empty:
                    continue
                sorted_dates = sorted(df_tmp["trade_date"].unique())
                keep_dates = set(sorted_dates[-lookback:])
                df_tmp = df_tmp[df_tmp["trade_date"].isin(keep_dates)]
                all_dfs.append(df_tmp)
            except Exception:
                pass

        if not all_dfs:
            return pd.DataFrame()

        df = pd.concat(all_dfs, ignore_index=True)

        # CSV 没有预计算的 up_limit/down_limit，需要自行计算
        df = _calc_zdt_price_vectorized(df)

        return df

    except Exception as e:
        logger.error("[loader] _load_window_from_csv 失败: %s", e)
        return pd.DataFrame()


def _calc_zdt_price_vectorized(df: pd.DataFrame) -> pd.DataFrame:
    """
    向量化计算涨跌停价格（仅在 CSV 回退模式下使用）。
    规则与 market_essentials.cal_zdt_price 一致。
    """
    from decimal import Decimal, ROUND_HALF_UP, ROUND_DOWN as _ROUND_DOWN

    pre = df["pre_close"].fillna(0)
    code = df["code"].fillna("").astype(str)
    name = df.get("stock_name", df.get("name", pd.Series([""] * len(df), index=df.index))).fillna("").astype(str)
    trade_date_s = df["trade_date"].astype(str)

    is_st = name.str.contains("ST", regex=False)
    is_kcb = code.str.contains("sh68", regex=False)
    is_cyb_new = (trade_date_s > "2020-08-23") & code.str.contains("sz3", regex=False)
    is_bj = code.str.startswith("bj")

    # 基础倍率
    zt_ratio = pd.Series(1.1, index=df.index)
    dt_ratio = pd.Series(0.9, index=df.index)

    zt_ratio[is_st] = 1.05
    dt_ratio[is_st] = 0.95

    merge_rule = is_kcb | is_cyb_new
    zt_ratio[merge_rule] = 1.2
    dt_ratio[merge_rule] = 0.8

    zt_ratio[is_bj] = 1.3
    dt_ratio[is_bj] = 0.7

    zt_raw = pre * zt_ratio
    dt_raw = pre * dt_ratio

    def _round_price(val: float, bj: bool, up: bool) -> float:
        if bj:
            rounding = _ROUND_DOWN if not up else ROUND_HALF_UP
            return float(Decimal(str(val) + ("1e-7" if not up else "-1e-7")).quantize(
                Decimal("0.01"), rounding
            ))
        return round(val, 2)

    zt_arr = zt_raw.values
    dt_arr = dt_raw.values
    bj_arr = is_bj.values
    n = len(df)
    zt_prices = [_round_price(float(zt_arr[i]), bool(bj_arr[i]), False) for i in range(n)]
    dt_prices = [_round_price(float(dt_arr[i]), bool(bj_arr[i]), True) for i in range(n)]

    df = df.copy()
    df["up_limit"] = zt_prices
    df["down_limit"] = dt_prices
    return df


def _compute_lianzban_cnt(df: pd.DataFrame) -> pd.DataFrame:
    """
    状态机推导连板次数（lianzban_cnt）。
    df 必须包含 code, trade_date, is_zt 列，且已按 [code, trade_date] 排序。
    过滤异常值 > 30。
    """
    df = df.sort_values(["code", "trade_date"])

    # 使用 shift + groupby 的方式向量化计算连板
    # 思路：对每只股票，先算出 is_zt 的累积分组，然后在组内计数
    grp = df.groupby("code", sort=False)

    # 对每只股票：今日涨停=1则累加，否则归0
    # 用自定义 apply（比纯 shift 方法更正确）
    def _cnt_per_stock(s: pd.Series) -> pd.Series:
        cnt = 0
        result = []
        for v in s:
            if v == 1:
                cnt += 1
            else:
                cnt = 0
            result.append(cnt)
        return pd.Series(result, index=s.index)

    df["lianzban_cnt"] = grp["is_zt"].transform(_cnt_per_stock)
    # 过滤脏数据
    df.loc[df["lianzban_cnt"] > 30, "lianzban_cnt"] = 0
    return df


# ── 原有接口保留 ──────────────────────────────────────────────────────────────


def get_trading_data(code: str) -> pd.DataFrame:
    """
    读取单股日K数据。

    code 格式示例: 'sh600000' 或 '600000' (自动补前缀: sh/sz/bj)
    路径: DATA_ROOT/stock-trading-data-pro/{code}.csv
    返回标准化列名的 DataFrame。
    找不到文件时返回空 DataFrame。
    """
    normalized = _normalize_code(code)
    csv_path = DATA_ROOT / "stock-trading-data-pro" / f"{normalized}.csv"

    if not csv_path.exists():
        logger.warning("[loader] 文件不存在: %s", csv_path)
        return pd.DataFrame()

    try:
        df = pd.read_csv(csv_path, encoding="GBK", skiprows=1)
        df = df.rename(columns=_TRADING_COL_MAP)
        if "trade_date" in df.columns:
            df["trade_date"] = df["trade_date"].astype(str)
        return df
    except Exception as e:
        logger.error("[loader] 读取 %s 失败: %s", csv_path, e)
        return pd.DataFrame()


def get_factor_slice(factor_name: str, trade_date: str) -> pd.DataFrame:
    """
    [已废弃] 从 factors/stock/daily/{factor_name}.parquet 读取截面。
    parquet 文件在 Windows 端不存在时返回空 DataFrame。
    请改用 load_daily_snapshot(trade_date) 替代。
    """
    parquet_path = DATA_ROOT / "factors" / "stock" / "daily" / f"{factor_name}.parquet"

    if not parquet_path.exists():
        logger.warning("[loader] Parquet 不存在 (已废弃接口): %s", parquet_path)
        return pd.DataFrame()

    try:
        df = pd.read_parquet(parquet_path)
        if "trade_date" not in df.columns:
            return pd.DataFrame()
        df["trade_date"] = df["trade_date"].astype(str)
        return df[df["trade_date"] == trade_date].copy()
    except Exception as e:
        logger.error("[loader] 读取 %s 失败: %s", parquet_path, e)
        return pd.DataFrame()


def get_latest_trade_date() -> str:
    """
    从本地数据获取最新交易日期（YYYY-MM-DD 字符串）。
    优先从 stg_cache parquet 取；回退到扫描 stock-trading-data-pro 目录。
    """
    # 优先 stg_cache parquet
    p = _get_preproc_parquet()
    if p and p.exists():
        try:
            df = pd.read_parquet(p, columns=["trade_date"])
            if not df.empty:
                latest = str(df["trade_date"].astype(str).max())
                return latest
        except Exception as e:
            logger.warning("[loader] stg_cache 获取最新日期失败: %s", e)

    # 回退：扫描 stock-trading-data-pro CSV（取样几个文件即可）
    try:
        trading_dir = DATA_ROOT / "stock-trading-data-pro"
        if not trading_dir.exists():
            return ""
        sample_files = list(trading_dir.glob("sh6*.csv"))[:20]
        max_date = ""
        for fp in sample_files:
            try:
                df_tmp = pd.read_csv(fp, encoding="GBK", skiprows=1, usecols=["交易日期"])
                latest = df_tmp["交易日期"].astype(str).max()
                if latest > max_date:
                    max_date = latest
            except Exception:
                pass
        return max_date
    except Exception as e:
        logger.error("[loader] get_latest_trade_date 失败: %s", e)
        return ""


def get_sector_stocks(industry: str) -> list:
    """
    获取申万一级行业下的所有股票代码列表。
    优先从 stg_cache parquet 取最新截面；回退到 申万行业.parquet。
    """
    # 优先 stg_cache
    p = _get_preproc_parquet()
    if p and p.exists():
        try:
            df = pd.read_parquet(
                p,
                columns=["trade_date", "code", "新版申万一级行业名称"],
            )
            df["trade_date"] = df["trade_date"].astype(str)
            latest = df["trade_date"].max()
            df_latest = df[df["trade_date"] == latest]
            return df_latest[df_latest["新版申万一级行业名称"] == industry]["code"].tolist()
        except Exception as e:
            logger.warning("[loader] get_sector_stocks stg_cache 失败: %s", e)

    # 回退
    parquet_path = DATA_ROOT / "factors" / "stock" / "daily" / "申万行业.parquet"
    if not parquet_path.exists():
        logger.warning("[loader] 申万行业.parquet 不存在")
        return []

    try:
        df = pd.read_parquet(parquet_path, columns=["trade_date", "code", "新版申万一级行业名称"])
        df["trade_date"] = df["trade_date"].astype(str)
        latest_date = df["trade_date"].max()
        latest_df = df[df["trade_date"] == latest_date]
        return latest_df[latest_df["新版申万一级行业名称"] == industry]["code"].tolist()
    except Exception as e:
        logger.error("[loader] get_sector_stocks(%s) 失败: %s", industry, e)
        return []

"""
[AI-GENERATED] 本文件由 AI 辅助创建，非框架原始文件。
创建目的：题材梯队分析（广发证券《情绪投资的机构视角》2024-05-16 的落地实现）

方法论要点（广发情绪投资系列·机构视角）：
  短线情绪常态带（注册制生态）：涨停家数 30-50 家（春节后 60-90），高度板 3-5B（新规下通常 ≤7B）。
    涨停家数超上沿 = 情绪过于一致，次日大概率反转；低于下沿 = 分歧/冰点。
  主线题材确认：单一题材（含分支）涨停家数占全市场涨停 40-50% 以上、连续 2-3 天 → 主线地位，
    正反馈（认可度扩散 → 持续性+赚钱效应 → 资金参与意愿↑）。
  梯队金字塔：低位 1-3B / 中位 4-6B / 高位 7B+，越完整（金字塔型）→ 题材认可度、持续性、
    龙头溢价越强。中位股率先跳水是退潮前兆。
  晋级率分布：1进2 极低（首板以量化参与为主）；2进3 常态仅 3-8%，能进 3B 即确认题材认可度。
  首板家数推算持续性（情绪上沿环境）：启动日 ≥35 家首板 → 约 1 周；≥45 家 → 2-3 周。
  机构视角两阶段：①轮动补涨阶段（主升后区间震荡）→ 容量核心（中军，日成交额15亿+）波段低吸，
    分支持续性上限约 3 天；②趋势阶段（大题材二轮行情）→ 容量核心买入持有（沿5日线）。
    二轮行情充要条件：容量核心守住震荡区间支撑 + 有新分支（新容量核心）启动。
  题材荒（无主线）→ 过渡题材环境：次新 / 券商（成交额高+板块充分整理+指数突破预期）/ 指数共振。

扩展内容（淘股吧短线知识地图·游资心法版）：
  板块内五类结构：龙头（最高板/情绪核心）、中军（成交额最大/容量核心）、人气股（次高板或近5日
    涨幅前列的非龙头活跃股，其结束意味着分歧结束）、先锋（早盘秒板，09:35已涨停近似）、
    杂毛（其余跟风涨停股）。支持三种口径：申万一级 / 申万二级 / 概念（stock-popular-concept-detail，
    「所属概念」顿号分隔多值，一股多概念逐概念计入）。
  三口径主线判定对比：三个口径各自涨停家数 Top5 并排 + 概念→行业主导映射（标注多口径一致项）。
  次日观察池：容量核心 / 情绪核心 / 人气股 / 补涨候选（近3日异动但未连板）/
    低吸候选（近20日有过涨停的前期强势股，回踩5日/10日均线附近且未破位），每只附入选理由；
    覆盖一级主线/候选题材 + 概念口径 Top 主线概念（条目标注来源口径）。

用法:
    uv run python tools/theme_ladder_analyzer.py [--date YYYY-MM-DD] [--summary-only]
"""

import argparse
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from collections import defaultdict

import pandas as pd
from dotenv import load_dotenv

from ._paths import require_quant_data_root

load_dotenv()

# ==================== 配置 ====================
DATA_DIR = require_quant_data_root() + "/stock-trading-data-pro"
DAYS_TO_KEEP = 25       # 尾部读取天数（连板回溯 + 近3日题材统计足够）
MAINLINE_SHARE = 40.0   # 主线题材涨停占比阈值（%，广发：40-50%）
MAINLINE_DAYS = 2       # 主线确认需连续天数（广发：2-3天，取下沿）
LU_NORMAL_LO, LU_NORMAL_HI = 30, 50   # 涨停家数常态带（注册制生态）
HEIGHT_NORMAL = "3-5B"                # 高度板常态
CAPACITY_AMOUNT_YI = 15.0             # 容量核心（中军）日成交额门槛（亿）

COL_DATE = "交易日期"
COL_CLOSE = "收盘价"
COL_PREV_CLOSE = "前收盘价"
COL_NAME = "股票名称"
COL_AMOUNT = "成交额"
COL_INDUSTRY1 = "新版申万一级行业名称"
COL_INDUSTRY2 = "新版申万二级行业名称"
COL_0935 = "09:35收盘价"   # 早盘秒板近似：09:35 已涨停 → 先锋

# 概念口径数据（stock-popular-concept-detail，2016-11 起，北交所概念列可能为空）
_QDR = require_quant_data_root()
CONCEPT_DIR = _QDR + "/stock-popular-concept-detail"
CONCEPT_TOP_N = 3         # 观察池纳入的概念口径 Top 主线概念数
CONCEPT_TAIL_BYTES = 16384  # 概念文件尾部读取字节数（约百行，足够覆盖目标日）

# 观察池 / 五类结构参数
BUCHANG_PCT_3D = 8.0      # 补涨候选：近3日涨幅异动阈值（%）
BUCHANG_VOL_MULT = 2.0    # 补涨候选：今日成交额 ≥ 前5日均额的倍数（放量异动）
DIXI_NEAR_MA = 0.03       # 低吸候选：收盘价距 MA5/MA10 的容忍幅度
DIXI_KEEP_MA10 = 0.99     # 低吸候选：未破位 = 收盘价 ≥ MA10 × 该系数


def limit_threshold(code: str) -> float:
    """涨停阈值（%）：北交30 / 创业科创20 / 主板10。"""
    if code.startswith("bj"):
        return 29.95
    if code.startswith("sz3") or code.startswith("sh688"):
        return 19.95
    return 9.95


def process_stock_file(file_path: str, target_date: str | None = None) -> dict | None:
    """单文件处理：近3日涨停标记 + 今日/昨日连板数 + 今日成交额/涨幅
    + 五类结构/观察池所需字段（秒板、近5日/3日涨幅、放量、均线、20日涨停史）。"""
    try:
        code = os.path.basename(file_path).replace(".csv", "")
        skip = 0
        with open(file_path, "r", encoding="gbk", errors="ignore") as f:
            if "股票代码" not in f.readline():
                skip = 1
        df = pd.read_csv(file_path, encoding="gbk", skiprows=skip)
        if df.empty or COL_DATE not in df.columns:
            return None
        df[COL_DATE] = pd.to_datetime(df[COL_DATE])
        if target_date:
            df = df[df[COL_DATE] <= pd.Timestamp(target_date)]
        df = df.tail(DAYS_TO_KEEP)
        if len(df) < 6:
            return None
        need = [COL_DATE, COL_CLOSE, COL_PREV_CLOSE, COL_NAME, COL_AMOUNT, COL_INDUSTRY1, COL_INDUSTRY2, COL_0935]
        avail = [c for c in need if c in df.columns]
        df = df[avail].copy()
        df = df.sort_values(COL_DATE).reset_index(drop=True)

        thr = limit_threshold(code)
        close_s = df[COL_CLOSE].astype(float)
        prev_s = df[COL_PREV_CLOSE].astype(float)
        pct = (close_s - prev_s) / prev_s * 100
        lu = (pct >= thr).tolist()  # 每日涨停标记

        # 连板数：从第 i 日向前回溯连续涨停天数
        def boards_at(i: int) -> int:
            n = 0
            while i - n >= 0 and lu[i - n]:
                n += 1
            return n

        last = len(df) - 1
        b_today = boards_at(last)
        b_yesterday = boards_at(last - 1)

        amt_s = df[COL_AMOUNT].astype(float) if COL_AMOUNT in df.columns else pd.Series([0.0] * len(df))

        # 秒板：今日 09:35 收盘价已触及涨停（先锋近似）
        lu_early = False
        if COL_0935 in df.columns:
            p0935 = pd.to_numeric(df[COL_0935], errors="coerce").iloc[-1]
            prev_last = float(prev_s.iloc[-1])
            if pd.notna(p0935) and prev_last > 0:
                lu_early = bool((float(p0935) - prev_last) / prev_last * 100 >= thr)

        # 近5日/近3日累计涨幅、近5日成交额、放量倍数（今日 vs 前5日均额）
        pct_5d = float((close_s.iloc[-1] / close_s.iloc[-6] - 1) * 100)
        pct_3d = float((close_s.iloc[-1] / close_s.iloc[-4] - 1) * 100)
        amt_5d_yi = float(amt_s.tail(5).sum() / 1e8)
        amt_ma5_prev = float(amt_s.iloc[-6:-1].mean())
        vol_mult = float(amt_s.iloc[-1] / amt_ma5_prev) if amt_ma5_prev > 0 else 0.0

        # 均线（低吸候选用）
        ma5 = float(close_s.tail(5).mean())
        ma10 = float(close_s.tail(10).mean()) if len(df) >= 10 else float("nan")

        return {
            "code": code,
            "name": str(df[COL_NAME].iloc[-1]) if COL_NAME in df.columns else code,
            "industry": str(df[COL_INDUSTRY1].iloc[-1]) if COL_INDUSTRY1 in df.columns else "未知",
            "industry2": str(df[COL_INDUSTRY2].iloc[-1]) if COL_INDUSTRY2 in df.columns else "未知",
            "last_date": df[COL_DATE].iloc[-1],
            "lu_d0": bool(lu[last]), "lu_d1": bool(lu[last - 1]), "lu_d2": bool(lu[last - 2]),
            "boards_today": b_today, "boards_yesterday": b_yesterday,
            "amount_yi": float(amt_s.iloc[-1]) / 1e8,
            "pct_today": round(float(pct.iloc[-1]), 2),
            "close": float(close_s.iloc[-1]),
            "lu_early_d0": lu_early,
            "pct_5d": round(pct_5d, 2), "pct_3d": round(pct_3d, 2),
            "amt_5d_yi": round(amt_5d_yi, 2), "vol_mult": round(vol_mult, 2),
            "ma5": ma5, "ma10": ma10,
            "had_lu_20d": bool(any(lu[-20:])),
        }
    except Exception:
        return None


def band_of_boards(b: int) -> str:
    """梯队分层：低位1-3B / 中位4-6B / 高位7B+。"""
    if b >= 7:
        return "高位7B+"
    if b >= 4:
        return "中位4-6B"
    if b >= 1:
        return "低位1-3B"
    return ""


# ==================== 板块内五类结构（淘股吧短线知识地图·游资心法版） ====================

def five_layers(grp: pd.DataFrame) -> dict:
    """题材内五类分层：龙头 / 中军 / 人气股 / 先锋 / 杂毛。

    判定规则：
      龙头  —— 题材内最高连板（并列取成交额大者），即情绪核心；
      中军  —— 题材内当日成交额最大者，即容量核心；
      人气股 —— 剔除龙头/中军后，次高连板（≥2B，并列取成交额大者）；
               若无，则取「成交额进题材前40%」中近5日累计涨幅最高者；
      先锋  —— 当日涨停且 09:35 已涨停（早盘秒板），剔除已归类个股；
      杂毛  —— 其余当日涨停的跟风股。
    """
    layers: dict = {"龙头": None, "中军": None, "人气": None, "先锋": [], "杂毛": []}
    if grp.empty:
        return layers
    bgrp = grp[grp["boards_today"] >= 1]
    leader = None
    if len(bgrp):
        leader = bgrp.sort_values(["boards_today", "amount_yi"], ascending=False).iloc[0]
    layers["龙头"] = leader
    zhongjun = grp.loc[grp["amount_yi"].idxmax()]
    layers["中军"] = zhongjun
    used = {s["code"] for s in (leader, zhongjun) if s is not None}

    rest = grp[~grp["code"].isin(used)]
    renqi = None
    rb = rest[rest["boards_today"] >= 2]
    if len(rb):
        renqi = rb.sort_values(["boards_today", "amount_yi"], ascending=False).iloc[0]
    elif len(rest):
        act = rest[rest["amount_yi"] >= rest["amount_yi"].quantile(0.6)]
        pool = act if len(act) else rest
        renqi = pool.sort_values(["pct_5d", "amt_5d_yi"], ascending=False).iloc[0]
    layers["人气"] = renqi
    if renqi is not None:
        used.add(renqi["code"])

    xf = grp[grp["lu_d0"] & grp["lu_early_d0"] & (~grp["code"].isin(used))]
    layers["先锋"] = list(xf.sort_values("amount_yi", ascending=False).to_dict("records"))
    used |= set(xf["code"])

    zm = grp[grp["lu_d0"] & (~grp["code"].isin(used))]
    layers["杂毛"] = list(zm.sort_values("amount_yi", ascending=False).to_dict("records"))
    return layers


def _nm(row) -> str:
    """个股展示：名称(代码)。"""
    return f"{row['name']}({row['code']})"


def read_concept_tail(file_path: str, target_date: str | None = None) -> tuple[str, str] | None:
    """读概念明细文件尾部，返回 (代码, 所属概念串)。

    文件按日期升序，取 ≤ target_date 的最后一行；概念数据 2016-11 才有、
    北交所概念列可能为空，读不到/为空均优雅返回。"""
    try:
        code = os.path.basename(file_path).replace(".csv", "")
        with open(file_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - CONCEPT_TAIL_BYTES))
            tail = f.read().decode("gbk", errors="ignore")
        best = ""
        found = False
        for line in tail.splitlines():
            parts = line.split(",")
            # 数据行形如：sh600000,浦发银行,2026-08-04,...(共7列，末列=所属概念，顿号分隔)
            if len(parts) < 7 or not parts[0].startswith(("sh", "sz", "bj")) or len(parts[2]) != 10:
                continue
            if target_date is None or parts[2] <= target_date:
                best, found = parts[6].strip(), True  # 升序遍历，最后一个满足条件的即最新
        if not found:
            return None
        return code, best
    except Exception:
        return None


def append_layers_section(L: list, title: str, layers_map: dict, with_note: bool = False):
    """输出某口径下各题材/概念的五类分层名单（龙头/人气股/中军/先锋/杂毛）。"""
    L.append(title)
    L.append("")
    for name, ly in layers_map.items():
        leader_txt = f"{_nm(ly['龙头'])}（{int(ly['龙头']['boards_today'])}B）" if ly["龙头"] is not None else "—"
        if ly["人气"] is not None:
            rq = ly["人气"]
            rq_txt = f"{_nm(rq)}（{int(rq['boards_today'])}B）" if rq["boards_today"] >= 1 else f"{_nm(rq)}（近5日{rq['pct_5d']:.1f}%）"
        else:
            rq_txt = "—"
        zj = ly["中军"]
        zj_txt = f"{_nm(zj)}（{zj['amount_yi']:.1f}亿）" if zj is not None else "—"
        xf_txt = "、".join(_nm(r) for r in ly["先锋"][:3]) if ly["先锋"] else "—"
        zm = ly["杂毛"]
        zm_txt = f"{len(zm)}家" + ("：" + "、".join(_nm(r) for r in zm[:5]) + ("…" if len(zm) > 5 else "") if zm else "")
        L.append(f"**{name}** —— 龙头：{leader_txt} ｜ 人气股：{rq_txt} ｜ 中军：{zj_txt} ｜ 先锋：{xf_txt} ｜ 杂毛：{zm_txt}")
    L.append("")
    if with_note:
        L.append("> 判定口径：龙头=最高板（并列取成交额大者）；中军=成交额最大；人气股=次高连板（无则成交额前40%中近5日涨幅最高）；"
                 "先锋=当日涨停且09:35已封板（秒板近似）；杂毛=其余跟风涨停股。")
        L.append("")


def build_watchlist(theme_groups: list[tuple[str, pd.DataFrame]], layers_map: dict) -> dict:
    """次日观察池（半路首板 + 低吸思路），分类输出，每只附入选理由。

    theme_groups: [(展示标签, 题材成分股DataFrame)]，标签如「电子」/「概念:存储芯片」（标注来源口径）。
    同一类别内按代码去重（一股可能同时属于行业主线与概念主线），先到先得。

    类别：
      容量核心 —— 各题材中军，接力/趋势持有观察；
      情绪核心 —— 各题材龙头，分歧转一致观察；
      人气股   —— 各题材次高，低吸/接力观察；
      补涨候选 —— 题材内近3日有异动（涨幅≥8% 或 今日放量≥2倍）但尚未连板的中低位股；
      低吸候选 —— 近20日有过涨停的前期强势股，当前回踩 MA5/MA10 附近且未破 10 日线。
    """
    pool: dict[str, list[tuple[str, str, str]]] = {k: [] for k in ("容量核心", "情绪核心", "人气股", "补涨候选", "低吸候选")}
    seen: dict[str, set] = {k: set() for k in pool}

    def _add(cat: str, code: str, item: tuple[str, str, str]):
        if code not in seen[cat]:
            seen[cat].add(code)
            pool[cat].append(item)

    for ind, grp in theme_groups:
        if grp.empty:
            continue
        layers = layers_map.get(ind) or five_layers(grp)
        picked = set()

        cap = layers["中军"]
        if cap is not None:
            _add("容量核心", cap["code"], (ind, _nm(cap), f"题材成交额第1（{cap['amount_yi']:.1f}亿），中军趋势/接力观察"))
            picked.add(cap["code"])
        emo = layers["龙头"]
        if emo is not None:
            _add("情绪核心", emo["code"], (ind, _nm(emo), f"{int(emo['boards_today'])}B龙头，分歧转一致观察"))
            picked.add(emo["code"])
        rq = layers["人气"]
        if rq is not None:
            if rq["boards_today"] >= 2:
                reason = f"{int(rq['boards_today'])}B次高板（人气股），低吸/接力观察"
            else:
                reason = f"近5日涨{rq['pct_5d']:.1f}%、成交额题材前列（人气股），低吸观察"
            _add("人气股", rq["code"], (ind, _nm(rq), reason))
            picked.add(rq["code"])

        # 补涨候选：近3日异动（涨幅或放量）但今日未连板
        bc = grp[(grp["boards_today"] == 0) & (~grp["code"].isin(picked))
                 & ((grp["pct_3d"] >= BUCHANG_PCT_3D) | (grp["vol_mult"] >= BUCHANG_VOL_MULT))]
        for r in bc.sort_values("pct_3d", ascending=False).head(3).to_dict("records"):
            why = []
            if r["pct_3d"] >= BUCHANG_PCT_3D:
                why.append(f"近3日涨{r['pct_3d']:.1f}%")
            if r["vol_mult"] >= BUCHANG_VOL_MULT:
                why.append(f"今日放量{r['vol_mult']:.1f}倍")
            _add("补涨候选", r["code"], (ind, _nm(r), "、".join(why) + "，尚未连板，补涨观察"))

        # 低吸候选：前期强势股回踩均线且未破位
        dx = grp[(grp["had_lu_20d"]) & (grp["boards_today"] == 0) & (~grp["code"].isin(picked))
                 & grp["ma10"].notna() & (grp["close"] >= grp["ma10"] * DIXI_KEEP_MA10)
                 & (((grp["close"] / grp["ma5"] - 1).abs() <= DIXI_NEAR_MA)
                    | ((grp["close"] / grp["ma10"] - 1).abs() <= DIXI_NEAR_MA))]
        for r in dx.sort_values("amount_yi", ascending=False).head(3).to_dict("records"):
            near = "MA5" if abs(r["close"] / r["ma5"] - 1) <= DIXI_NEAR_MA else "MA10"
            _add("低吸候选", r["code"], (ind, _nm(r), f"近20日有过涨停，回踩{near}附近且未破10日线，低吸观察"))
    return pool


def main():
    parser = argparse.ArgumentParser(description="题材梯队分析（广发机构视角）")
    parser.add_argument("--date", default=None, help="分析截止日期 YYYY-MM-DD（默认自动检测最新交易日）")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()

    files = sorted(
        os.path.join(DATA_DIR, f) for f in os.listdir(DATA_DIR)
        if f.endswith(".csv") and not f.startswith("bj")
    )
    t0 = datetime.now()
    recs = []
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(process_stock_file, f, args.date): f for f in files}
        for fut in as_completed(futs):
            try:
                r = fut.result()
                if r is not None:
                    recs.append(r)
            except Exception:
                pass
    print(f"[题材梯队] 有效股票 {len(recs)} 只, 耗时 {(datetime.now() - t0).total_seconds():.1f}秒", file=sys.stderr)
    if not recs:
        print("[题材梯队] 错误：无有效数据")
        sys.exit(1)

    df = pd.DataFrame(recs)
    df = df[df["industry"].notna() & (df["industry"] != "未知") & (df["industry"] != "nan")]
    # 剔除数据断档股（退市/长期停牌：CSV 尾部日期早于全市场最新交易日）与退市整理股（名称含「退」）
    global_latest = df["last_date"].max()
    df = df[df["last_date"] >= global_latest - pd.Timedelta(days=3)]
    df = df[~df["name"].str.contains("退", na=False)]
    print(f"[题材梯队] 数据日期 {pd.Timestamp(global_latest).strftime('%Y-%m-%d')}, 对齐后 {len(df)} 只", file=sys.stderr)

    # ===== 概念口径数据加载（尾部单行并行读取；2016-11 前无数据则 concept_members 为空、相关章节跳过） =====
    concept_map: dict[str, str] = {}
    if os.path.isdir(CONCEPT_DIR):
        tc0 = datetime.now()
        cfiles = [os.path.join(CONCEPT_DIR, f) for f in os.listdir(CONCEPT_DIR) if f.endswith(".csv")]
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            for res in ex.map(read_concept_tail, cfiles, [args.date] * len(cfiles), chunksize=64):
                if res is not None and res[1]:
                    concept_map[res[0]] = res[1]
        print(f"[题材梯队] 概念标签覆盖 {len(concept_map)} 只, 耗时 {(datetime.now() - tc0).total_seconds():.1f}秒", file=sys.stderr)
    # 概念 → 成分股代码集合（一股多概念，按「、」拆分逐概念计入）
    concept_members: dict[str, set] = defaultdict(set)
    for code, cs in concept_map.items():
        for c in cs.split("、"):
            c = c.strip()
            if c:
                concept_members[c].add(code)

    lu_today = df[df["lu_d0"]]
    n_lu = len(lu_today)
    max_boards = int(df["boards_today"].max())
    n_first_board = int((df["boards_today"] == 1).sum())

    L = []
    L.append("## 题材梯队与主线判定（广发机构视角）")
    L.append("")

    # ===== 1. 短线情绪常态带 =====
    if n_lu > LU_NORMAL_HI:
        lu_state = f"🔥 超上沿（{LU_NORMAL_HI}家）→ 情绪过于一致，**次日反转概率大**"
    elif n_lu < LU_NORMAL_LO:
        lu_state = f"🧊 低于下沿（{LU_NORMAL_LO}家）→ 分歧/偏弱"
    else:
        lu_state = "常态带内"
    h_state = "常态" if 3 <= max_boards <= 5 else ("⚠️ 超常态（注意异动监管风险）" if max_boards >= 6 else "偏弱")
    L.append(f"**短线情绪**：涨停 {n_lu} 家（常态带 {LU_NORMAL_LO}-{LU_NORMAL_HI}）→ {lu_state}；"
             f"高度板 {max_boards}B（常态 {HEIGHT_NORMAL}）→ {h_state}；首板 {n_first_board} 家")
    L.append("")

    # ===== 2. 首板家数 → 持续性推算 =====
    if n_first_board >= 45:
        fb_hint = "≥45家 → 可支撑 2-3 周大题材（情绪上沿前提下，龙头6B后晋级率跃升、不断带动补涨）"
    elif n_first_board >= 35:
        fb_hint = "≥35家 → 可支撑约 1 周主线行情"
    elif n_first_board >= 20:
        fb_hint = "20-35家 → 轮动常态，题材持续性 1-3 天"
    else:
        fb_hint = "🧊 <20家 → 题材荒/冰点，持续性差，等启动信号"
    L.append(f"**首板持续性推算**（广发：启动日35家→1周 / 45家→2-3周）：今日首板 {n_first_board} 家 → {fb_hint}")
    L.append("")

    # ===== 3. 主线题材判定（近3日题材涨停占比） =====
    L.append("### 主线题材判定（涨停占比≥40% 连续2天 = 主线确认）")
    L.append("")
    share_d0 = lu_today.groupby("industry").size().sort_values(ascending=False)
    lu_d1 = df[df["lu_d1"]]
    lu_d2 = df[df["lu_d2"]]
    share_d1 = lu_d1.groupby("industry").size()
    share_d2 = lu_d2.groupby("industry").size()
    n_d1, n_d2 = max(len(lu_d1), 1), max(len(lu_d2), 1)

    rows = []
    for ind, cnt in share_d0.head(8).items():
        p0 = cnt / max(n_lu, 1) * 100
        p1 = share_d1.get(ind, 0) / n_d1 * 100
        p2 = share_d2.get(ind, 0) / n_d2 * 100
        days_ge40 = sum(1 for p in (p0, p1, p2) if p >= MAINLINE_SHARE)
        if p0 >= MAINLINE_SHARE and p1 >= MAINLINE_SHARE:
            status = "✅ 主线确认"
        elif p0 >= MAINLINE_SHARE:
            status = "🔶 主线候选（单日，需明日确认）"
        elif days_ge40 >= 2:
            status = "🔶 反复活跃"
        else:
            status = ""
        rows.append((ind, cnt, p0, p1, p2, status))
    L.append("| 题材(申万一级) | 今日涨停 | 今日占比 | 昨日占比 | 前日占比 | 判定 |")
    L.append("|--------------|---------|---------|---------|---------|------|")
    for ind, cnt, p0, p1, p2, status in rows:
        L.append(f"| {ind} | {cnt} | {p0:.0f}% | {p1:.0f}% | {p2:.0f}% | {status} |")
    mainline = [r for r in rows if r[5].startswith("✅")]
    candidate = [r for r in rows if r[5].startswith("🔶 主线候选")]
    if not mainline and not candidate:
        L.append("")
        L.append("**⚠️ 题材荒（无主线）**：活跃资金或沿交易惯例寻找过渡题材 —— 关注次新 / "
                 "券商（成交额高+板块充分整理+指数突破预期）/ 近1-2季度与指数强共振或逆指数的板块。")
    L.append("")

    # ===== 3.5 三口径主线判定对比（申万一级 vs 申万二级 vs 概念） =====
    lu2_today = lu_today[~lu_today["industry2"].isin(["未知", "nan"])]
    share2_d0 = lu2_today.groupby("industry2").size().sort_values(ascending=False)
    lu_codes = set(lu_today["code"])
    concept_lu_cnt = sorted(((c, len(codes & lu_codes)) for c, codes in concept_members.items()), key=lambda kv: -kv[1])
    top1 = list(share_d0.head(5).items())
    top2 = list(share2_d0.head(5).items())
    topc = [kv for kv in concept_lu_cnt if kv[1] > 0][:5]
    L.append("### 主线判定对比（申万一级 vs 申万二级 vs 概念）")
    L.append("")
    L.append("| 排名 | 申万一级(家数/占比) | 申万二级(家数/占比) | 概念(家数/占比) |")
    L.append("|------|--------------------|--------------------|-----------------|")
    for i in range(5):
        c1 = f"{top1[i][0]}（{top1[i][1]}/{top1[i][1] / max(n_lu, 1) * 100:.0f}%）" if i < len(top1) else "—"
        c2 = f"{top2[i][0]}（{top2[i][1]}/{top2[i][1] / max(n_lu, 1) * 100:.0f}%）" if i < len(top2) else "—"
        cc = f"{topc[i][0]}（{topc[i][1]}/{topc[i][1] / max(n_lu, 1) * 100:.0f}%）" if i < len(topc) else "—"
        L.append(f"| {i + 1} | {c1} | {c2} | {cc} |")
    L.append("")
    if topc:
        top1_names = {n for n, _ in top1}
        top2_names = {n for n, _ in top2}
        L.append("概念 Top → 行业口径映射（概念内涨停股的主导行业；✅=该行业同在对应口径 Top5，即多口径指向同一热点）：")
        L.append("")
        L.append("| 概念 | 涨停家数 | 主导申万一级 | 主导申万二级 |")
        L.append("|------|---------|--------------|--------------|")
        for c, cnt in topc:
            sub = lu_today[lu_today["code"].isin(concept_members[c])]
            m1 = sub["industry"].value_counts()
            m2 = sub[~sub["industry2"].isin(["未知", "nan"])]["industry2"].value_counts()
            m1_txt = (f"{m1.index[0]}（{m1.iloc[0]}/{cnt}）" + ("✅" if m1.index[0] in top1_names else "")) if len(m1) else "—"
            m2_txt = (f"{m2.index[0]}（{m2.iloc[0]}/{cnt}）" + ("✅" if m2.index[0] in top2_names else "")) if len(m2) else "—"
            L.append(f"| {c} | {cnt} | {m1_txt} | {m2_txt} |")
        L.append("")
    else:
        L.append("概念口径无数据（概念数据2016-11起 / 当日涨停股均无概念标签），一致项标注跳过。")
        L.append("")

    # ===== 4. 题材梯队金字塔（Top 5 题材） =====
    L.append("### 题材梯队金字塔（低位1-3B / 中位4-6B / 高位7B+，越完整持续性越强）")
    L.append("")
    board_stocks = df[df["boards_today"] >= 1]
    L.append("| 题材 | 低位1-3B | 中位4-6B | 高位7B+ | 最高板 | 梯队形态 |")
    L.append("|------|---------|---------|--------|--------|---------|")
    theme_ladder = {}
    for ind, _ in share_d0.head(5).items():
        grp = board_stocks[board_stocks["industry"] == ind]
        low = int((grp["boards_today"].between(1, 3)).sum())
        mid = int((grp["boards_today"].between(4, 6)).sum())
        high = int((grp["boards_today"] >= 7).sum())
        mb = int(grp["boards_today"].max()) if len(grp) else 0
        if low >= 3 and mid >= 1 and high >= 1:
            shape = "🔺完整金字塔（认可度/持续性/龙头溢价最强）"
        elif low >= 2 and mid >= 1:
            shape = "较完整（缺高位，观察4B能否晋级）"
        elif low >= 1 and mid == 0 and high == 0:
            shape = "仅低位（启动期，待2进3确认）"
        elif low == 0 and (mid >= 1 or high >= 1):
            shape = "⚠️ 高位悬空（无新血，退潮风险）"
        else:
            shape = "—"
        theme_ladder[ind] = shape
        L.append(f"| {ind} | {low} | {mid} | {high} | {mb}B | {shape} |")
    L.append("")

    # ===== 5. 晋级率（vs 广发常态：2进3 仅3-8%） =====
    L.append("### 晋级率（昨日N板 → 今日N+1板）")
    L.append("")
    L.append("| 路径 | 昨日基数 | 今日晋级 | 晋级率 | 广发常态参考 |")
    L.append("|------|---------|---------|--------|-------------|")
    promo_refs = {1: "极低（首板量化主导，赚隔夜溢价）", 2: "3-8%（进3B=题材认可度确认）", 3: "中位晋级，失败回撤20-30%"}
    for n in (1, 2, 3):
        base = df[df["boards_yesterday"] == n]
        promoted = base[base["boards_today"] == n + 1]
        rate = len(promoted) / len(base) * 100 if len(base) else 0.0
        L.append(f"| {n}进{n+1} | {len(base)} | {len(promoted)} | {rate:.0f}% | {promo_refs[n]} |")
    L.append("")

    # ===== 6. 容量核心 / 情绪核心（Top 5 题材） =====
    L.append("### 题材双核心（容量核心=中军：机构/低风偏资金载体；情绪核心：高风偏接力载体）")
    L.append("")
    L.append("| 题材 | 容量核心候选（成交额最大） | 成交额(亿) | 中军特征(≥15亿) | 情绪核心（最高板） | 板数 |")
    L.append("|------|--------------------------|-----------|----------------|-------------------|------|")
    for ind, _ in share_d0.head(5).items():
        grp = df[df["industry"] == ind]
        cap = grp.loc[grp["amount_yi"].idxmax()] if len(grp) else None
        bgrp = grp[grp["boards_today"] >= 1]
        # 并列最高板时取成交额大者，与「板块内五类结构」的龙头口径一致（原 idxmax 依赖行序、结果不稳定）
        emo = bgrp.sort_values(["boards_today", "amount_yi"], ascending=False).iloc[0] if len(bgrp) else None
        if cap is not None:
            cap_flag = "✅" if cap["amount_yi"] >= CAPACITY_AMOUNT_YI else "❌"
            emo_txt = f"{emo['name']}({emo['code']})" if emo is not None else "—"
            emo_b = f"{int(emo['boards_today'])}B" if emo is not None else "—"
            L.append(f"| {ind} | {cap['name']}({cap['code']}) | {cap['amount_yi']:.1f} | {cap_flag} | {emo_txt} | {emo_b} |")
    L.append("")

    # ===== 6.5 板块内五类结构（申万一级，淘股吧短线知识地图·游资心法版） =====
    top_themes = [ind for ind, _ in share_d0.head(5).items()]
    layers_map = {ind: five_layers(df[df["industry"] == ind]) for ind in top_themes}
    append_layers_section(L, "### 板块内五类结构·申万一级（龙头 / 人气股 / 中军 / 先锋 / 杂毛）", layers_map, with_note=True)

    # ===== 6.6 板块内五类结构（申万二级） =====
    top2_themes = [n for n, _ in top2]
    layers2_map = {n: five_layers(df[df["industry2"] == n]) for n in top2_themes}
    append_layers_section(L, "### 板块内五类结构（申万二级）", layers2_map)

    # ===== 6.7 板块内五类结构（概念口径） =====
    if topc:
        layersc_map = {c: five_layers(df[df["code"].isin(concept_members[c])]) for c, _ in topc}
        append_layers_section(L, "### 板块内五类结构（概念口径）", layersc_map)
    else:
        L.append("### 板块内五类结构（概念口径）")
        L.append("")
        L.append("概念口径无数据（概念数据2016-11起 / 当日涨停股均无概念标签），跳过。")
        L.append("")

    # ===== 7. 机构视角阶段判定与策略 =====
    L.append("### 机构视角：阶段判定 → 策略")
    L.append("")
    if mainline:
        ind0 = mainline[0][0]
        shape = theme_ladder.get(ind0, "")
        if "完整金字塔" in shape or "较完整" in shape:
            stage = ("**趋势/主升阶段**：主线确认 + 梯队完整 → 容量核心**买入持有**（沿5日线，"
                     "缩量或跌破均线=认可度分歧，对应减仓）；分支题材可持续挖掘。")
        else:
            stage = ("**主升初期**：主线确认但梯队未展开 → 观察2进3晋级率，"
                     "确认后容量核心建仓；跟风股持续性≤3B，补涨股≤启动时龙头高度。")
    elif candidate:
        stage = ("**主线候选待确认**：单题材料今日占比≥40%但未连续 → 明日占比维持则主线确认；"
                 "不确认则按轮动补涨处理（分支持续性上限约3天，短线套利后卖出）。")
    else:
        stage = ("**轮动补涨/题材荒阶段**：绝大多数行业持续性过短、不具备关注价值（广发）。"
                 "①老主线容量核心若守住震荡区间支撑 → 波段低吸；②有新分支+新容量核心启动 → 二轮行情可能；"
                 "③纯题材荒 → 过渡题材（次新/券商/指数共振），不重仓。")
    L.append(stage)
    L.append("")
    L.append("> 二轮行情充要条件（广发）：①容量核心不跌破轮动补涨阶段震荡区间支撑位（破位=核心资金认可度松动，大概率无二轮）；"
             "②轮动补涨阶段有具备持续催化的新分支启动（老核心分支弹性已兑现，二轮抓手是新容量核心）。")
    L.append("")

    # ===== 8. 次日观察池（半路首板 + 低吸思路，淘股吧游资心法版） =====
    watch_themes = [r[0] for r in mainline] + [r[0] for r in candidate]
    if not watch_themes:
        watch_themes = top_themes[:3]  # 题材荒时退化为当日涨停占比前3题材
    watch_groups: list[tuple[str, pd.DataFrame]] = [(ind, df[df["industry"] == ind]) for ind in watch_themes]
    # 概念口径 Top 主线概念一并纳入（条目标注来源口径，如 [概念:存储芯片]）
    for c, _ in topc[:CONCEPT_TOP_N]:
        watch_groups.append((f"概念:{c}", df[df["code"].isin(concept_members[c])]))
    pool = build_watchlist(watch_groups, layers_map)
    L.append("### 次日观察池（半路首板 + 低吸）")
    L.append("")
    L.append(f"覆盖题材：{'、'.join(label for label, _ in watch_groups)}")
    L.append("")
    cat_hint = {
        "容量核心": "接力/趋势持有观察",
        "情绪核心": "分歧转一致观察",
        "人气股": "低吸/接力观察",
        "补涨候选": "近3日异动但未连板，半路首板观察",
        "低吸候选": "前期强势股回踩均线未破位",
    }
    for cat, hint in cat_hint.items():
        items = pool[cat]
        L.append(f"**{cat}**（{hint}，{len(items)}只）")
        if items:
            for ind, stock, reason in items:
                L.append(f"- [{ind}] {stock} —— {reason}")
        else:
            L.append("- 无")
        L.append("")

    md = "\n".join(L)
    print(md)

    if not args.summary_only:
        out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kun", "data")
        os.makedirs(out_dir, exist_ok=True)
        tag = (args.date or datetime.now().strftime("%Y-%m-%d")).replace("-", "")
        out_path = os.path.join(out_dir, f"theme_ladder_{tag}.md")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"\n[题材梯队] 已保存: {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()

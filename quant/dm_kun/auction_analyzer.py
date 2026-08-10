"""
[AI-GENERATED] 本文件由 AI 辅助创建，非框架原始文件。
创建目的：集合竞价分析（「淘股吧短线知识地图·游资心法版」【时间节点】模块的落地实现）

图上规则 → 本脚本指标映射：
  9:15 第一秒  高开程度 = 市场惯性（首个时点撮合价 vs 前收盘）
  9:20         不可撤单分界：9:15→9:20 价格漂移大 = 挂单虚假（可撤单期的挂单不算真实态度）
  9:25 最后一秒 最终撮合价/量 = 主力态度（竞价涨幅、竞价量比、未匹配净单）
  9:20→9:25    真实竞价走向（上行/走平/下行）= 撤单分水岭后的真实资金态度
  9:30 第一秒  开盘价 vs 竞价末价 = 市场态度对主力态度的确认/否定

数据源（本地 xbx 数据中心，GBK 编码、第 1 行 vendor 水印）：
  <DC>/stock-call-auction-data/<code>.csv  每行一个交易日，列含：
    盘前竞价时间/盘前撮合价格/盘前撮合量（累计，单位手）/盘前未撮合买单/盘前未撮合卖单
    —— 五个等长逗号分隔列表（时点间隔约 3 秒，早期年份稀疏；部分日期行只覆盖 9:24 后窗口，
       属 vendor 采集残缺，9:15/9:20 节点指标自动记为缺失）
    最新价（= 末时点撮合价）、集合竞价成交量（手，= 末时点累计撮合量）、集合竞价成交额（元）、
    买1~5价量 / 卖1~5价量（9:25 撮合后快照）
  <DC>/stock-trading-data-pro/<code>.csv  日线：前收盘价、开盘价、成交量（股）、股票名称

未匹配方向语义（实际探查确认，2026-08）：
  数据没有单一「方向」列，而是「盘前未撮合买单」「盘前未撮合卖单」两个独立列表，
  取值非负、单位手。净未匹配 = 未撮合买单 - 未撮合卖单：
    大幅为正 = 买压残留（抢筹未成交），大幅为负 = 卖压残留（抛压未消化）。
  例：sh600000 2026-08-05 末时点 买0/卖1269 → 净 -1269 手，卖压残留，与当日收跌一致。
  注意 9:25 撮合完成后未匹配量理论上趋近 0，残留量级本身就是信号。

单位注意：竞价量为「手」（已验证 集合竞价成交额 ≈ 量×100×价格），日线成交量为「股」，
  竞价量比 = 集合竞价成交量(手)×100 / 昨日成交量(股)。

用法:
    uv run python tools/auction_analyzer.py [--date YYYY-MM-DD] [--summary-only]
    uv run python tools/auction_analyzer.py --codes sh600000 sz000001 [--date YYYY-MM-DD]
"""

import argparse
import csv
import io
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

# ==================== 配置 ====================
DC = os.environ.get("QUANT_DATA_ROOT") or "/Users/kun/Desktop/AGdata"
AUCTION_DIR = os.path.join(DC, "stock-call-auction-data")
DAILY_DIR = os.path.join(DC, "stock-trading-data-pro")

TAIL_BYTES = 64 * 1024   # 尾部读取窗口：竞价行约 4~6KB/行，64KB 稳定覆盖近 10 行
N_WORKERS = 12

# 平开带宽（|竞价涨幅| < 0.2% 记为平开）
FLAT_BAND = 0.2
# 抢筹榜门槛：竞价高开 ≥3% 且 9:20 后仍上行
GRAB_MIN_GAP = 3.0
# 诱多榜门槛：竞价高开 ≥2% 但 9:20 后跳水 ≤-1%
TRAP_MIN_GAP = 2.0
TRAP_MAX_TREND = -1.0
# 9:20→9:25 走向判定带宽（|漂移| < 0.2% 记为走平）
TREND_BAND = 0.2

# 时间节点（秒）
def _hm(h: int, m: int, s: int = 0) -> int:
    return h * 3600 + m * 60 + s

T_915_LATEST = _hm(9, 16, 30)   # 首个时点晚于 9:16:30 视为 9:15 节点缺失
T_920 = _hm(9, 20, 0)           # 不可撤单分界，取 ±3s 内最近时点
T_925_EARLIEST = _hm(9, 24, 30)  # 末时点早于 9:24:30 视为数据异常


def _t2s(v: int) -> int:
    """竞价时点整数（如 91501 / 92500）→ 当日秒数。"""
    return (v // 10000) * 3600 + ((v // 100) % 100) * 60 + v % 100


def _s2t(sec: int) -> str:
    """当日秒数 → HH:MM:SS。"""
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def _split_list(s: str) -> list[str]:
    """逗号分隔列表字段 → 去尾空元素列表。"""
    return [x for x in s.split(",") if x != ""]


def _read_tail_rows(path: str) -> list[list[str]]:
    """二进制读文件尾部 → GBK 解码 → csv 解析出完整数据行（跳过水印/表头/残缺首行）。"""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        f.seek(max(0, size - TAIL_BYTES))
        raw = f.read()
    text = raw.decode("gbk", errors="ignore")
    # 首行可能被 seek 截断，直接丢弃
    text = text[text.find("\n") + 1:] if "\n" in text else ""
    def _is_date(x: str) -> bool:
        return len(x) == 10 and x[4] == "-" and x[:4].isdigit()

    rows = []
    for r in csv.reader(io.StringIO(text)):
        # 数据行：竞价文件日期在第 0 列，日线文件日期在第 2 列（第 0/1 列为代码/名称）
        if r and (_is_date(r[0]) or (len(r) > 2 and _is_date(r[2]))):
            rows.append(r)
    return rows


def _f(x: str) -> float:
    """安全 float。"""
    try:
        return float(x)
    except (ValueError, TypeError):
        return math.nan


def parse_auction_row(r: list[str]) -> dict | None:
    """解析一行竞价数据 → 四节点指标 + 全量时点序列。

    列序（已按真实文件核实）：
      0 交易日期 / 1 盘前竞价时间 / 2 盘前撮合价格 / 3 盘前撮合量(累计,手) /
      4 盘前未撮合买单 / 5 盘前未撮合卖单 / 6 最新价 / 7 集合竞价成交量(手) /
      8 集合竞价成交额 / 9-18 买1~5价 卖1~5价 / 19-28 买1~5量 卖1~5量 / 29 股票代码
    """
    try:
        times = [_t2s(int(x)) for x in _split_list(r[1])]
        prices = [_f(x) for x in _split_list(r[2])]
        vols = [_f(x) for x in _split_list(r[3])]
        unbuy = [_f(x) for x in _split_list(r[4])]
        unsell = [_f(x) for x in _split_list(r[5])]
    except (ValueError, IndexError):
        return None
    # vendor 怪癖：时间列表偶尔比其他列表少 1 个元素（末个 9:25 撮合时点缺时间戳，
    # 实测 sh600000 2026-08-05：时间 199 个、其余 200 个，末价格恰等于「最新价」）。
    # 缺一 → 尾部补合成时点；其他长度不一致 → 按最短截断兜底。
    m = min(len(prices), len(vols), len(unbuy), len(unsell))
    if m == 0:
        return None
    if len(times) == m - 1 and times and times[-1] >= _hm(9, 24, 50):
        times = times + [_hm(9, 25, 0)]
    n = min(m, len(times))
    if n == 0:
        return None
    times, prices, vols, unbuy, unsell = times[:n], prices[:n], vols[:n], unbuy[:n], unsell[:n]

    # —— 9:15 首个时点（市场惯性）：取 9:16:30 前第一个有真实撮合价的时点
    # （9:15:01 首 tick 常无成交、价格为 0，需跳过）——
    p_first = math.nan
    for t, p in zip(times, prices):
        if t > T_915_LATEST:
            break
        if p > 0:
            p_first = p
            break
    # —— 9:20 撤单分水岭：取 ≤9:20:00 的最后一个有效时点（分水岭时刻的在场价格）——
    # 注意低流动性股票的时点只在有变化时记录，±3s 窗口会大量落空，故用「最后在场价」
    p_920 = math.nan
    for t, p in zip(times, prices):
        if t > T_920:
            break
        if p > 0:
            p_920 = p
    # —— 9:25 末时点（主力态度）——
    p_last = prices[-1] if times[-1] >= T_925_EARLIEST else math.nan
    vol_last = vols[-1]          # 累计撮合量（手）
    net_unmatched = unbuy[-1] - unsell[-1]  # 净未匹配（手），正=买压残留

    return {
        "code": r[29] if len(r) > 29 else "",
        "date": r[0],
        "p_first": p_first, "p_920": p_920, "p_last": p_last,
        "auction_vol_shou": _f(r[7]) if _f(r[7]) == _f(r[7]) else vol_last,  # 集合竞价成交量(手)
        "auction_amt": _f(r[8]),
        "net_unmatched": net_unmatched,
        "unbuy_last": unbuy[-1], "unsell_last": unsell[-1],
        # 全量时点序列（个股模式用）
        "series": list(zip(times, prices, vols, unbuy, unsell)),
    }


def parse_daily(path: str, target: str) -> dict | None:
    """日线尾部找目标日：前收盘 / 开盘 / 股票名称 / 昨日成交量（股）。"""
    try:
        rows = _read_tail_rows(path)
    except OSError:
        return None
    # 日线列序：0 股票代码 1 股票名称 2 交易日期 3 开盘价 4 最高 5 最低 6 收盘 7 前收盘 8 成交量 ...
    idx = next((i for i, r in enumerate(rows) if len(r) > 8 and r[2] == target), None)
    if idx is None:
        return None
    cur = rows[idx]
    prev_vol = _f(rows[idx - 1][8]) if idx > 0 and len(rows[idx - 1]) > 8 else math.nan
    return {
        "name": cur[1],
        "open": _f(cur[3]),
        "prev_close": _f(cur[7]),
        "prev_vol": prev_vol,  # 昨日总成交量（股）
    }


def process_one(args: tuple[str, str, bool]) -> dict | None:
    """单只股票：竞价行 + 日线 join → 四节点指标记录。"""
    code, target, detail = args
    apath = os.path.join(AUCTION_DIR, f"{code}.csv")
    if not os.path.exists(apath):
        return None
    try:
        rows = _read_tail_rows(apath)
    except OSError:
        return None
    row = next((r for r in reversed(rows) if r[0] == target), None)
    if row is None:
        return None
    a = parse_auction_row(row)
    if a is None:
        return None
    d = parse_daily(os.path.join(DAILY_DIR, f"{code}.csv"), target)
    if d is None or math.isnan(d["prev_close"]) or d["prev_close"] <= 0:
        return None

    rec = {**a, **d}
    pc = d["prev_close"]
    rec["gap_pct"] = (a["p_last"] / pc - 1) * 100 if not math.isnan(a["p_last"]) else math.nan
    rec["gap_first_pct"] = (a["p_first"] / pc - 1) * 100 if not math.isnan(a["p_first"]) else math.nan
    # 9:15→9:20 漂移（虚假挂单识别）
    rec["drift_15_20"] = (
        (a["p_920"] / a["p_first"] - 1) * 100
        if not (math.isnan(a["p_first"]) or math.isnan(a["p_920"]) or a["p_first"] == 0) else math.nan
    )
    # 9:20→9:25 真实竞价走向（主力态度）
    rec["trend_20_25"] = (
        (a["p_last"] / a["p_920"] - 1) * 100
        if not (math.isnan(a["p_920"]) or math.isnan(a["p_last"]) or a["p_920"] == 0) else math.nan
    )
    # 竞价量比 = 竞价量(手)×100 / 昨日成交量(股)
    rec["vol_ratio"] = (
        a["auction_vol_shou"] * 100 / d["prev_vol"]
        if not (math.isnan(d["prev_vol"]) or d["prev_vol"] <= 0) else math.nan
    )
    # 9:30 开盘 vs 竞价末价（市场态度）
    rec["open_vs_auction"] = (
        (d["open"] / a["p_last"] - 1) * 100
        if not (math.isnan(a["p_last"]) or math.isnan(d["open"]) or a["p_last"] == 0) else math.nan
    )
    if not detail:
        rec.pop("series", None)
    return rec


def _fmt(x: float, nd: int = 2, dash: str = "—") -> str:
    return f"{x:.{nd}f}" if x == x else dash  # nan → —


def _trend_label(t: float) -> str:
    if t != t:
        return "缺失"
    if t > TREND_BAND:
        return "上行"
    if t < -TREND_BAND:
        return "下行"
    return "走平"


def detect_latest_date() -> str | None:
    """默认日期：浦发银行竞价文件末行日期。"""
    try:
        rows = _read_tail_rows(os.path.join(AUCTION_DIR, "sh600000.csv"))
        return rows[-1][0] if rows else None
    except OSError:
        return None


# ==================== 全市场模式 ====================

def market_report(recs: list[dict], target: str, top: int) -> str:
    """全市场竞价情绪总览 + 异动榜单（Markdown）。"""
    import pandas as pd

    df = pd.DataFrame(recs)
    L: list[str] = []
    L.append(f"## 集合竞价情绪总览（{target}）")
    L.append("")
    n = len(df)
    L.append(f"覆盖 **{n}** 只（竞价数据覆盖约 5461 只，少于全市场，缺失股票已跳过）")
    L.append("")

    # —— 高/平/低开分布 ——
    g = df["gap_pct"].dropna()
    n_hi3 = int((g >= 3).sum())
    n_hi = int((g >= FLAT_BAND).sum())
    n_lo3 = int((g <= -3).sum())
    n_lo = int((g <= -FLAT_BAND).sum())
    n_flat = len(g) - n_hi - n_lo
    L.append("### 竞价高开/平开/低开分布（9:25 末价 vs 前收盘）")
    L.append("")
    L.append("| 区间 | 家数 | 占比 |")
    L.append("|------|------|------|")
    for label, cnt in [
        ("高开 ≥3%", n_hi3),
        (f"高开 {FLAT_BAND}%~3%", n_hi - n_hi3),
        (f"平开 ±{FLAT_BAND}%", n_flat),
        (f"低开 -3%~-{FLAT_BAND}%", n_lo - n_lo3),
        ("低开 ≤-3%", n_lo3),
    ]:
        L.append(f"| {label} | {cnt} | {cnt / len(g) * 100:.1f}% |")
    med = float(g.median())
    mood = "偏多（高开家数显著占优）" if n_hi > n_lo * 1.5 else (
        "偏空（低开家数显著占优）" if n_lo > n_hi * 1.5 else "均衡")
    L.append("")
    L.append(f"竞价涨幅中位数 **{med:.2f}%**，整体情绪：**{mood}**")
    L.append("")

    # —— 竞价量比分布 ——
    vr = df["vol_ratio"].dropna() * 100  # 转百分比
    L.append("### 竞价量比分布（集合竞价量 / 昨日总量）")
    L.append("")
    L.append(f"中位数 **{float(vr.median()):.2f}%**；≥1% 的 {int((vr >= 1).sum())} 只（{(vr >= 1).mean() * 100:.1f}%）；"
             f"≥3% 的 {int((vr >= 3).sum())} 只（{(vr >= 3).mean() * 100:.1f}%，竞价放巨量）")
    L.append("")

    # —— 9:20→9:25 真实竞价走向（主力态度）——
    tr = df["trend_20_25"].dropna()
    n_up = int((tr > TREND_BAND).sum())
    n_dn = int((tr < -TREND_BAND).sum())
    L.append("### 9:20→9:25 真实竞价走向（撤单分水岭后 = 主力态度）")
    L.append("")
    L.append(f"上行 **{n_up}** 只（{n_up / len(tr) * 100:.1f}%）｜走平 {len(tr) - n_up - n_dn} 只｜"
             f"下行 **{n_dn}** 只（{n_dn / len(tr) * 100:.1f}%）")
    L.append("")

    # —— 9:30 开盘确认 ——
    ov = df["open_vs_auction"].dropna()
    n_oup = int((ov > 0).sum())
    L.append("### 9:30 开盘确认（开盘价 vs 竞价末价 = 市场态度）")
    L.append("")
    L.append(f"开盘价高于竞价末价 **{n_oup}** 只（{n_oup / len(ov) * 100:.1f}%），"
             f"中位偏离 **{float(ov.median()):.2f}%**——"
             + ("市场认可竞价方向（高开高走向）" if float(ov.median()) > 0.1 else
                "市场否定竞价方向（开盘兑现/砸盘占优）" if float(ov.median()) < -0.1 else "开盘与竞价基本一致"))
    L.append("")

    # —— 抢筹榜 ——
    grab = df[(df["gap_pct"] >= GRAB_MIN_GAP) & (df["trend_20_25"] > 0)].sort_values("vol_ratio", ascending=False)
    L.append(f"### 竞价抢筹榜（高开≥{GRAB_MIN_GAP:.0f}% 且 9:20 后上行，按竞价量比排序，Top {top}）")
    L.append("")
    L.append("| 代码 | 名称 | 竞价涨幅% | 9:15→9:20漂移% | 9:20→9:25走向% | 竞价量比% | 净未匹配(手) | 开盘确认% |")
    L.append("|------|------|----------|----------------|----------------|----------|--------------|----------|")
    for _, r in grab.head(top).iterrows():
        L.append(f"| {r['code']} | {r['name']} | {_fmt(r['gap_pct'])} | {_fmt(r['drift_15_20'])} | "
                 f"{_fmt(r['trend_20_25'])} | {_fmt(r['vol_ratio'] * 100)} | "
                 f"{_fmt(r['net_unmatched'], 0)} | {_fmt(r['open_vs_auction'])} |")
    L.append("")

    # —— 诱多榜 ——
    trap = df[(df["gap_pct"] >= TRAP_MIN_GAP) & (df["trend_20_25"] <= TRAP_MAX_TREND)].sort_values("trend_20_25")
    L.append(f"### 竞价诱多榜（高开≥{TRAP_MIN_GAP:.0f}% 但 9:20 后跳水≤{TRAP_MAX_TREND:.0f}%，按跳水幅度排序，Top {top}）")
    L.append("")
    L.append("| 代码 | 名称 | 竞价涨幅% | 9:15→9:20漂移% | 9:20→9:25走向% | 竞价量比% | 净未匹配(手) | 开盘确认% |")
    L.append("|------|------|----------|----------------|----------------|----------|--------------|----------|")
    for _, r in trap.head(top).iterrows():
        L.append(f"| {r['code']} | {r['name']} | {_fmt(r['gap_pct'])} | {_fmt(r['drift_15_20'])} | "
                 f"{_fmt(r['trend_20_25'])} | {_fmt(r['vol_ratio'] * 100)} | "
                 f"{_fmt(r['net_unmatched'], 0)} | {_fmt(r['open_vs_auction'])} |")
    L.append("")
    L.append("> 读法：抢筹榜 = 主力竞价真实抢筹候选（9:20 撤单分水岭后仍上行，挂单真实）；"
             "诱多榜 = 9:15 高挂吸引眼球、9:20 后真实资金撤退，开盘易跳水。"
             "「9:15→9:20漂移」绝对值大 = 可撤单期挂单虚假，态度以 9:20 后为准。")
    return "\n".join(L)


# ==================== 个股模式 ====================

def stock_report(rec: dict, target: str) -> str:
    """个股：竞价逐时点明细 + 四节点解读。"""
    L: list[str] = []
    L.append(f"## {rec['name']}（{rec['code']}）{target} 集合竞价明细")
    L.append("")
    pc = rec["prev_close"]
    L.append(f"前收盘 **{pc:.2f}**｜9:30 开盘 **{_fmt(rec['open'])}**｜"
             f"竞价量 **{_fmt(rec['auction_vol_shou'], 0)}** 手（{_fmt(rec['auction_amt'] / 1e4)} 万元）")
    L.append("")

    # —— 四节点解读 ——
    L.append("### 四节点解读")
    L.append("")
    gf, gp = rec["gap_first_pct"], rec["gap_pct"]
    drift, trend, ov = rec["drift_15_20"], rec["trend_20_25"], rec["open_vs_auction"]
    L.append(f"- **9:15 第一秒（市场惯性）**：首时点开 {_fmt(gf)}%——"
             + ("大幅高开，隔夜惯性偏多" if gf == gf and gf >= 2 else
                "明显低开，隔夜惯性偏空" if gf == gf and gf <= -2 else
                "接近平开" if gf == gf else "数据缺失（该行竞价窗口不完整）"))
    L.append(f"- **9:20 撤单分界（获利盘态度）**：9:15→9:20 漂移 {_fmt(drift)}%——"
             + ("漂移大，9:20 前挂单虚假成分高，态度以 9:20 后为准" if drift == drift and abs(drift) >= 1 else
                "漂移小，挂单较真实" if drift == drift else "数据缺失"))
    L.append(f"- **9:25 最后一秒（主力态度）**：末价对应涨幅 {_fmt(gp)}%，"
             f"9:20→9:25 {_trend_label(trend)}（{_fmt(trend)}%），"
             f"竞价量比 {_fmt(rec['vol_ratio'] * 100)}%，"
             f"净未匹配 {_fmt(rec['net_unmatched'], 0)} 手（买{_fmt(rec['unbuy_last'], 0)}/卖{_fmt(rec['unsell_last'], 0)}）——"
             + ("主力真实抢筹" if trend == trend and trend > TREND_BAND and gp == gp and gp > 0 else
                "主力撤退/诱多嫌疑" if trend == trend and trend < -TREND_BAND else "态度中性"))
    L.append(f"- **9:30 开盘第一秒（市场态度）**：开盘价 vs 竞价末价 {_fmt(ov)}%——"
             + ("市场认可，高开高走" if ov == ov and ov > 0.1 else
                "市场否定，开盘兑现/砸盘" if ov == ov and ov < -0.1 else "基本一致"))
    L.append("")

    # —— 逐时点明细 ——
    L.append("### 逐时点明细（量=累计撮合量，未匹配单位：手）")
    L.append("")
    L.append("| 时间 | 撮合价 | 涨幅% | 累计量(手) | 未撮合买 | 未撮合卖 | 净未匹配 |")
    L.append("|------|--------|-------|-----------|----------|----------|----------|")
    for t, p, v, ub, us in rec["series"]:
        L.append(f"| {_s2t(t)} | {_fmt(p)} | {_fmt((p / pc - 1) * 100) if p == p and p > 0 else '—'} | "
                 f"{_fmt(v, 0)} | {_fmt(ub, 0)} | {_fmt(us, 0)} | {_fmt(ub - us, 0)} |")
    return "\n".join(L)


# ==================== 主入口 ====================

def main() -> None:
    parser = argparse.ArgumentParser(description="集合竞价分析（淘股吧游资心法·时间节点模块）")
    parser.add_argument("--date", default=None, help="交易日 YYYY-MM-DD（默认最近一个有竞价数据的交易日）")
    parser.add_argument("--codes", nargs="*", default=None, help="个股模式：如 sh600000 sz000001")
    parser.add_argument("--top", type=int, default=20, help="榜单长度（默认 20）")
    parser.add_argument("--workers", type=int, default=N_WORKERS)
    parser.add_argument("--summary-only", action="store_true", help="只输出 Markdown，不保存文件")
    args = parser.parse_args()

    target = args.date or detect_latest_date()
    if not target:
        print("[竞价分析] 错误：无法确定交易日期", file=sys.stderr)
        sys.exit(1)

    t0 = datetime.now()

    # —— 个股模式 ——
    if args.codes:
        for code in args.codes:
            rec = process_one((code, target, True))
            if rec is None:
                print(f"[竞价分析] {code} 在 {target} 无竞价/日线数据，跳过", file=sys.stderr)
                continue
            print(stock_report(rec, target))
            print()
        return

    # —— 全市场模式 ——
    files = sorted(
        f[:-4] for f in os.listdir(AUCTION_DIR) if f.endswith(".csv")
    )
    recs: list[dict] = []
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        for r in ex.map(process_one, [(c, target, False) for c in files], chunksize=32):
            if r is not None:
                recs.append(r)
    elapsed = (datetime.now() - t0).total_seconds()
    print(f"[竞价分析] {target} 有效股票 {len(recs)}/{len(files)} 只, 耗时 {elapsed:.1f}秒", file=sys.stderr)
    if not recs:
        print(f"[竞价分析] 错误：{target} 无有效数据（非交易日或数据未覆盖）", file=sys.stderr)
        sys.exit(1)

    md = market_report(recs, target, args.top)
    print(md)

    if not args.summary_only:
        out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kun", "data")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"auction_{target.replace('-', '')}.md")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"\n[竞价分析] 已保存: {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()

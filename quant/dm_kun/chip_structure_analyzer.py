"""
[AI-GENERATED] 本文件由 AI 辅助创建，非框架原始文件。
创建目的：筹码结构分析（淘股吧短线知识地图·游资心法版【筹码】模块的落地实现）

方法论要点（筹码模块）：
  获利盘：低于收盘价的筹码（直接用数据源的「胜率」列，0~1）。获利盘过重（>90%）→ 抛压风险。
  套牢盘：高于收盘价的筹码。获利盘 <10% → 深度套牢（反弹抛压轻、但也说明趋势弱）。
  中间平均成本线：所有筹码的加权平均成本（「加权平均成本」列），
    股价在其上方 = 平均持仓浮盈，下方 = 浮亏；偏离度衡量多空盈亏状态。
  价格区间和集中度：集中度数值越低表明筹码越集中（变盘蓄势）。
    本脚本集中度 = (90分位成本-10分位成本)/(90分位成本+10分位成本)，并给近一年分位。
  支撑/压力：当前价下方最近的密集成本区（相邻分位成本间距最窄处）= 筹码密集支撑，
    重要的筹码支撑区域止跌 → 低吸介入位置；上方密集区 = 压力（套牢盘解套抛压）。

数据源：<QUANT_DATA_ROOT>/stock-chip-distribution/<code>.csv（GBK，第1行为 vendor 水印需跳过，
  分位成本每 5% 一档：5分位成本 ~ 95分位成本，另有加权平均成本、胜率=获利盘比例）。
  价格为后复权口径，分位成本与价格同口径，直接可比。

用法:
    uv run python tools/chip_structure_analyzer.py --date 2026-08-05 --summary-only
    uv run python tools/chip_structure_analyzer.py --date 2026-08-05            # 全市场扫描 + 落盘 md
    uv run python tools/chip_structure_analyzer.py --codes sh600000,sz000001 --date 2026-08-05
"""

import argparse
import io
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime

import pandas as pd
from dotenv import load_dotenv

from ._paths import require_quant_data_root

# ==================== 配置 ====================
load_dotenv()
_QDR = require_quant_data_root()
DATA_DIR = _QDR + "/stock-chip-distribution"
TRADE_DIR = _QDR + "/stock-trading-data-pro"
TAIL_DAYS = 260          # 尾部读取交易日数（近一年，用于集中度分位）
TREND_DAYS = 20          # 个股模式趋势表天数
TAIL_BYTES = 120_000     # 尾部二进制读取窗口（260 行 × 约 250 字节，留足余量）
HEAD_BYTES = 8_192       # 头部读取窗口（取表头行）
PROFIT_HI = 0.90         # 获利盘过重阈值（抛压风险）
PROFIT_LO = 0.10         # 深度套牢阈值
NEAR_SUPPORT_PCT = 2.0   # 距支撑 <2% 视为贴近支撑（低吸观察位）

COL_CODE = "股票代码"
COL_DATE = "交易日期"
COL_CLOSE = "后复权价格"
COL_AVG = "加权平均成本"
COL_WIN = "胜率"
QCOLS = [f"{q}分位成本" for q in range(5, 100, 5)]  # 5~95 分位成本，每 5% 一档


def read_tail_rows(file_path: str, n: int) -> pd.DataFrame | None:
    """只读文件尾部 n 行（GBK，跳过第 1 行 vendor 水印），避免全量解析 ~6000 行历史。"""
    try:
        with open(file_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            # 表头（第 2 行，第 1 行是水印）
            f.seek(0)
            head = f.read(HEAD_BYTES).decode("gbk", errors="ignore").splitlines()
            header = next((ln for ln in head if ln.startswith(COL_CODE)), None)
            if header is None:
                return None
            # 尾部
            f.seek(max(0, size - TAIL_BYTES))
            tail = f.read().decode("gbk", errors="ignore").splitlines()
        if len(tail) < 2:
            return None
        rows = tail[1:] if size > TAIL_BYTES else tail  # 窗口中首行可能截断，丢弃
        rows = [ln for ln in rows if ln.strip()][-n:]
        if not rows:
            return None
        df = pd.read_csv(io.StringIO("\n".join([header, *rows])))
        if df.empty or COL_DATE not in df.columns:
            return None
        return df
    except Exception:
        return None


def dense_zone(costs: list[float], close: float, side: str) -> float | None:
    """密集成本区：相邻分位成本间距最窄处的中点。side='below' 取收盘价下方（支撑），'above' 取上方（压力）。"""
    best_mid, best_gap = None, None
    for lo, hi in zip(costs[:-1], costs[1:]):
        gap = hi - lo
        mid = (hi + lo) / 2
        if side == "below" and hi > close:
            continue
        if side == "above" and lo < close:
            continue
        if gap <= 0:
            continue
        if best_gap is None or gap < best_gap:
            best_gap, best_mid = gap, mid
    return best_mid


def chip_metrics(df: pd.DataFrame, target_date: pd.Timestamp | None) -> dict | None:
    """单股票筹码画像：获利盘 / 偏离度 / 集中度(含近一年分位) / 90%成本区间 / 支撑压力位。"""
    df = df.copy()
    df[COL_DATE] = pd.to_datetime(df[COL_DATE], format="ISO8601")
    df = df.sort_values(COL_DATE)
    if target_date is not None:
        df = df[df[COL_DATE] <= target_date]
    if len(df) < 2:
        return None
    last = df.iloc[-1]
    close = float(last[COL_CLOSE])
    avg = float(last[COL_AVG])
    win = float(last[COL_WIN])
    costs = [float(last[c]) for c in QCOLS]
    if close <= 0 or avg <= 0:
        return None

    # 集中度：(90分位-10分位)/(90分位+10分位)，越低越集中；近一年分位 = 当前值在过去一年中的排名分位
    conc_series = (df["90分位成本"] - df["10分位成本"]) / (df["90分位成本"] + df["10分位成本"])
    conc_series = conc_series.replace([float("inf"), float("-inf")], pd.NA).dropna()
    conc = (costs[17] - costs[1]) / (costs[17] + costs[1])  # 90分位 - 10分位
    conc_pct = float((conc_series <= conc).mean()) if len(conc_series) else float("nan")

    support = dense_zone(costs, close, "below")
    pressure = dense_zone(costs, close, "above")
    dist_support = (close - support) / close * 100 if support else None
    dist_pressure = (pressure - close) / close * 100 if pressure else None

    return {
        "code": str(last[COL_CODE]),
        "date": last[COL_DATE],
        "close": close,
        "win_rate": win,                          # 获利盘比例（0~1）
        "avg_cost": avg,
        "dev_pct": (close - avg) / avg * 100,     # 现价 vs 平均成本偏离度（%）
        "conc": conc,                             # 集中度（越低越集中）
        "conc_pct_1y": conc_pct,                  # 集中度近一年分位（0~1，越低=相对自身越集中）
        "band_lo": costs[0],                      # 90% 成本区间下沿（5分位）
        "band_hi": costs[18],                     # 90% 成本区间上沿（95分位）
        "support": support,
        "pressure": pressure,
        "dist_support_pct": dist_support,         # 距支撑距离（%），小 = 贴近支撑
        "dist_pressure_pct": dist_pressure,       # 距压力距离（%）
    }


def process_stock_file(file_path: str, target: str | None) -> dict | None:
    df = read_tail_rows(file_path, TAIL_DAYS)
    if df is None:
        return None
    td = pd.Timestamp(target) if target else None
    return chip_metrics(df, td)


def find_file(code: str) -> str | None:
    p = os.path.join(DATA_DIR, f"{code}.csv")
    return p if os.path.exists(p) else None


# ==================== 股票名称映射（从 trading-data 尾部快速提取） ====================

def _read_name_from_trade_file(file_path: str) -> tuple[str, str] | None:
    """从 stock-trading-data-pro CSV 读最后一行的股票名称。头部取表头 + 尾部取末行。"""
    try:
        with open(file_path, "rb") as f:
            # 头部：取表头（跳过 vendor 水印行）
            head = f.read(4096).decode("gbk", errors="ignore").splitlines()
            header_line = None
            for ln in head:
                if "股票名称" in ln and "股票代码" in ln:
                    header_line = ln
                    break
            if header_line is None:
                return None
            header = header_line.split(",")
            code_idx = header.index("股票代码") if "股票代码" in header else None
            name_idx = header.index("股票名称") if "股票名称" in header else None
            if code_idx is None or name_idx is None:
                return None
            # 尾部：取最后一行数据
            f.seek(max(0, os.path.getsize(file_path) - 2048))
            tail = f.read().decode("gbk", errors="ignore").splitlines()
            # 取最后一个非空行
            last_line = None
            for ln in reversed(tail):
                ln = ln.strip()
                if ln and not ln.startswith("股票代码") and "," in ln:
                    last_line = ln
                    break
            if last_line is None:
                return None
            cols = last_line.split(",")
            if len(cols) <= max(code_idx, name_idx):
                return None
            return (cols[code_idx].strip(), cols[name_idx].strip())
    except Exception:
        return None


def build_name_map(workers: int = 12) -> dict[str, str]:
    """从 stock-trading-data-pro 并行读取全部股票代码→名称映射。"""
    import glob as _glob
    t0 = datetime.now()
    files = sorted(_glob.glob(os.path.join(TRADE_DIR, "*.csv")))
    name_map: dict[str, str] = {}
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_read_name_from_trade_file, f): f for f in files}
        for fut in as_completed(futs):
            try:
                r = fut.result()
                if r:
                    name_map[r[0]] = r[1]
            except Exception:
                pass
    elapsed = (datetime.now() - t0).total_seconds()
    print(f"[筹码结构] 名称映射 {len(name_map)} 只, 耗时 {elapsed:.1f}秒", file=sys.stderr)
    return name_map


# ==================== 全市场扫描 ====================

def run_market_scan(args) -> str:
    # 1) 先建代码→名称映射
    name_map = build_name_map(args.workers)

    files = sorted(
        os.path.join(DATA_DIR, f) for f in os.listdir(DATA_DIR)
        if f.endswith(".csv") and not f.startswith("bj")  # 与 workspace 惯例一致：剔除北交所
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
    elapsed = (datetime.now() - t0).total_seconds()
    print(f"[筹码结构] 有效股票 {len(recs)} 只, 耗时 {elapsed:.1f}秒", file=sys.stderr)
    if not recs:
        print("[筹码结构] 错误：无有效数据")
        sys.exit(1)

    df = pd.DataFrame(recs)
    # 注入股票名称
    df["name"] = df["code"].map(name_map).fillna("—")
    # 剔除数据断档股（退市/长期停牌：选定行日期早于全市场最新日期）
    latest = df["date"].max()
    df = df[df["date"] >= latest - pd.Timedelta(days=3)]
    print(f"[筹码结构] 数据日期 {pd.Timestamp(latest).strftime('%Y-%m-%d')}, 对齐后 {len(df)} 只", file=sys.stderr)

    top = args.top
    L = []
    L.append(f"## 筹码结构全市场扫描（{pd.Timestamp(latest).strftime('%Y-%m-%d')}）")
    L.append("")

    # ===== 1. 全市场概览 =====
    med_win = df["win_rate"].median()
    med_dev = df["dev_pct"].median()
    med_conc = df["conc"].median()
    n_over = int((df["win_rate"] > PROFIT_HI).sum())
    n_deep = int((df["win_rate"] < PROFIT_LO).sum())
    n_near_sup = int((df["dist_support_pct"] < NEAR_SUPPORT_PCT).sum())
    if med_win > 0.7:
        win_state = "全市场获利盘偏高 → 整体浮盈重，警惕一致性兑现抛压"
    elif med_win < 0.3:
        win_state = "全市场套牢盘偏重 → 整体浮亏，反弹初期抛压轻但趋势弱"
    else:
        win_state = "盈亏结构中性"
    L.append(f"**全市场概览**：获利盘比例中位数 {med_win:.0%}（{win_state}）；"
             f"现价偏离平均成本中位数 {med_dev:+.1f}%；集中度中位数 {med_conc:.3f}")
    L.append("")
    L.append(f"- 获利盘 >90%（抛压风险）：{n_over} 只；获利盘 <10%（深度套牢）：{n_deep} 只；"
             f"距支撑 <{NEAR_SUPPORT_PCT:.0f}%（贴近密集支撑）：{n_near_sup} 只")
    L.append("")

    def stock_table(sub: pd.DataFrame, extra_col: str, extra_name: str, fmt: str) -> list[str]:
        rows = ["| 代码 | 名称 | 收盘(后复权) | 获利盘 | 偏离成本 | 集中度 | 集中度1年分位 | "
                f"{extra_name} |",
                "|------|------|------------|--------|---------|--------|--------------|"
                + "------|"]
        for _, r in sub.iterrows():
            extra = fmt.format(r[extra_col]) if pd.notna(r[extra_col]) else "—（上方无密集区）"
            rows.append(
                f"| {r['code']} | {r['name']} | {r['close']:.2f} | {r['win_rate']:.0%} | {r['dev_pct']:+.1f}% | "
                f"{r['conc']:.3f} | {r['conc_pct_1y']:.0%} | {extra} |"
            )
        return rows

    # ===== 2. 获利盘过重（抛压风险） =====
    L.append(f"### 获利盘过重 Top {top}（获利盘>90%：浮盈盘兑现抛压风险）")
    L.append("")
    over = df[df["win_rate"] > PROFIT_HI].nlargest(top, "win_rate")
    if len(over):
        L += stock_table(over, "dist_pressure_pct", "距压力", "{:+.1f}%")
    else:
        L.append("（无）")
    L.append("")

    # ===== 3. 深度套牢 =====
    L.append(f"### 深度套牢 Top {top}（获利盘<10%：上方层层套牢，反弹即解套抛压）")
    L.append("")
    deep = df[df["win_rate"] < PROFIT_LO].nsmallest(top, "win_rate")
    if len(deep):
        L += stock_table(deep, "dist_pressure_pct", "距压力", "{:+.1f}%")
    else:
        L.append("（无）")
    L.append("")

    # ===== 4. 筹码高度集中（变盘蓄势） =====
    L.append(f"### 筹码最集中 Top {top}（集中度最低：筹码高度集中 = 变盘蓄势，配合放量确认方向）")
    L.append("")
    conc_top = df.nsmallest(top, "conc")
    L += stock_table(conc_top, "dist_support_pct", "距支撑", "{:+.1f}%")
    L.append("")

    # ===== 5. 贴近筹码支撑（低吸观察位） =====
    L.append(f"### 贴近密集支撑 Top {top}（距支撑最近：重要筹码支撑区止跌 → 低吸介入候选，"
             f"需自行结合企稳/放量信号确认）")
    L.append("")
    near = df[df["dist_support_pct"].notna()].nsmallest(top, "dist_support_pct")
    L += stock_table(near, "dist_support_pct", "距支撑", "{:+.1f}%")
    L.append("")

    L.append("> 口径：价格与成本均为后复权口径；获利盘=数据源「胜率」列；"
             "集中度=(90分位-10分位)/(90分位+10分位)成本，越低越集中；"
             "支撑/压力=现价下/上方相邻分位成本间距最窄的密集区中点。")
    return "\n".join(L)


# ==================== 个股详细画像 ====================

def run_stock_detail(args) -> str:
    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    target = pd.Timestamp(args.date) if args.date else None
    L = []
    for code in codes:
        path = find_file(code)
        if path is None:
            L.append(f"## {code}\n\n（数据文件不存在：{DATA_DIR}/{code}.csv）")
            continue
        df = read_tail_rows(path, max(TAIL_DAYS, TREND_DAYS))
        if df is None:
            L.append(f"## {code}\n\n（数据读取失败）")
            continue
        m = chip_metrics(df, target)
        if m is None:
            L.append(f"## {code}\n\n（指定日期前无有效数据）")
            continue

        d = pd.Timestamp(m["date"]).strftime("%Y-%m-%d")
        L.append(f"## {code} 筹码画像（{d}，后复权口径）")
        L.append("")
        L.append(f"- **获利盘比例（胜率）**：{m['win_rate']:.1%}"
                 + (" — ⚠️ 获利盘过重，抛压风险" if m["win_rate"] > PROFIT_HI else "")
                 + (" — 深度套牢" if m["win_rate"] < PROFIT_LO else ""))
        L.append(f"- **套牢盘比例**：{1 - m['win_rate']:.1%}")
        L.append(f"- **加权平均成本**：{m['avg_cost']:.2f}，现价 {m['close']:.2f}，"
                 f"偏离 {m['dev_pct']:+.1f}%（{'现价在平均成本上方，整体浮盈' if m['dev_pct'] > 0 else '现价在平均成本下方，整体浮亏'}）")
        L.append(f"- **筹码集中度**：{m['conc']:.3f}（近一年分位 {m['conc_pct_1y']:.0%}，"
                 + ("相对自身历史高度集中，变盘蓄势" if m["conc_pct_1y"] < 0.2 else
                    "相对自身历史明显分散" if m["conc_pct_1y"] > 0.8 else "中性") + "）")
        L.append(f"- **90% 成本区间**：[{m['band_lo']:.2f}, {m['band_hi']:.2f}]")
        sup = f"{m['support']:.2f}（距现价 {m['dist_support_pct']:.1f}%）" if m["support"] else "无（现价低于全部密集成本区）"
        pre = f"{m['pressure']:.2f}（距现价 {m['dist_pressure_pct']:.1f}%）" if m["pressure"] else "无（现价高于全部密集成本区，上方无套牢压力）"
        L.append(f"- **筹码密集支撑**：{sup}"
                 + (" — 贴近支撑区，止跌企稳即低吸观察位" if m["dist_support_pct"] is not None and m["dist_support_pct"] < NEAR_SUPPORT_PCT else ""))
        L.append(f"- **筹码密集压力**：{pre}")
        L.append("")

        # 近 20 日趋势
        df2 = df.copy()
        df2[COL_DATE] = pd.to_datetime(df2[COL_DATE], format="ISO8601")
        if target is not None:
            df2 = df2[df2[COL_DATE] <= target]
        trend = df2.sort_values(COL_DATE).tail(TREND_DAYS)
        L.append(f"### 近 {len(trend)} 日筹码趋势")
        L.append("")
        L.append("| 日期 | 收盘 | 获利盘 | 偏离成本 | 集中度 | 支撑 | 压力 | 距支撑 |")
        L.append("|------|------|--------|---------|--------|------|------|--------|")
        for _, row in trend.iterrows():
            close = float(row[COL_CLOSE])
            avg = float(row[COL_AVG])
            costs = [float(row[c]) for c in QCOLS]
            conc = (costs[17] - costs[1]) / (costs[17] + costs[1])
            sup_v = dense_zone(costs, close, "below")
            pre_v = dense_zone(costs, close, "above")
            dev = (close - avg) / avg * 100
            L.append(
                f"| {row[COL_DATE].strftime('%Y-%m-%d')} | {close:.2f} | {float(row[COL_WIN]):.0%} | "
                f"{dev:+.1f}% | {conc:.3f} | "
                f"{f'{sup_v:.2f}' if sup_v else '—'} | {f'{pre_v:.2f}' if pre_v else '—'} | "
                f"{f'{(close - sup_v) / close * 100:.1f}%' if sup_v else '—'} |"
            )
        L.append("")
    return "\n".join(L)


def main():
    parser = argparse.ArgumentParser(description="筹码结构分析（淘股吧短线知识地图·筹码模块）")
    parser.add_argument("--date", default=None, help="分析截止日期 YYYY-MM-DD（默认各股取最新一行）")
    parser.add_argument("--codes", default=None, help="指定个股，逗号分隔，如 sh600000,sz000001；不填则全市场扫描")
    parser.add_argument("--top", type=int, default=20, help="全市场榜单长度（默认 20）")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--summary-only", action="store_true", help="只输出 Markdown 摘要，不落盘")
    args = parser.parse_args()

    md = run_stock_detail(args) if args.codes else run_market_scan(args)
    print(md)

    if not args.summary_only:
        out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kun", "data")
        os.makedirs(out_dir, exist_ok=True)
        tag = (args.date or datetime.now().strftime("%Y-%m-%d")).replace("-", "")
        suffix = "stocks" if args.codes else "market"
        out_path = os.path.join(out_dir, f"chip_structure_{suffix}_{tag}.md")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"\n[筹码结构] 已保存: {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()

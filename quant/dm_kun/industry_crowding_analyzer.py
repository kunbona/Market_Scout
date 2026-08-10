"""
[AI-GENERATED] 本文件由 AI 辅助创建，非框架原始文件。
创建目的：行业拥挤度分析（广发证券《情绪指标权重提升下拥挤度方法论》2024-05-16 的落地实现）

方法论要点（广发情绪投资系列）：
  拥挤度 = 行业成交额 / 全A成交额，MA5 平滑（熨平单日波动），衡量情绪/预期演绎程度。
  法则一（比较区间）：只看近一年（~250 交易日）分位数，不拉长——行业逻辑变迁会使占比中枢漂移
                      （新能源车 3-6%→4-9%→3-4.5%；白酒 1-4%→5%→2%）。
  法则二（顶部规律）：分位 ≥80% → 拥挤，对利好钝化、对利空敏感，超额收益易阶段性见顶。
                      对主题类/稳定价值/顺周期有效；景气成长若景气上行可突破上限（弱指引）。
  法则三（底部规律）：分位 ≤20% → 出清，性价比回升。稳定价值/顺周期可左侧配置；
                      成长类可能在底部躺很久（22年TMT/23年新能源），必须等右侧催化。
  存量博弈权重：增量资金匮乏（存量博弈）时占比此消彼长，拥挤度模型指引增强；
                哑铃两端（稳定价值 vs 主题成长）占比负相关越强，存量博弈特征越明显。

用法:
    uv run python tools/industry_crowding_analyzer.py [--date YYYY-MM-DD] [--summary-only]
"""

import argparse
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime

import numpy as np
import pandas as pd

# ==================== 配置 ====================
DATA_DIR = os.environ.get("QUANT_DATA_ROOT", "/Users/kun/Desktop/AGdata") + "/stock-trading-data-pro"
LOOKBACK_CALENDAR = 400  # 日历天数，确保覆盖 ~260 个交易日（近一年250 + MA5预热）
PERCENTILE_WINDOW = 250  # 近一年分位数窗口（交易日）
MA_WINDOW = 5            # 拥挤度 MA5 平滑
TOP_QUANTILE = 80.0      # 顶部规律阈值（分位%）
BOTTOM_QUANTILE = 20.0   # 底部规律阈值（分位%）

COL_CODE = "股票代码"
COL_DATE = "交易日期"
COL_AMOUNT = "成交额"
COL_INDUSTRY1 = "新版申万一级行业名称"

# ==================== 三类资产映射（申万一级 -> 资产类别） ====================
# 广发框架：顶部规律对主题/稳定/顺周期指引强，对景气成长弱；
#           底部规律对稳定/顺周期指引强，对成长弱（需右侧催化）。
ASSET_CLASS = {
    # 稳定价值（红利）：顶/底规律均强
    "公用事业": "稳定价值", "交通运输": "稳定价值", "银行": "稳定价值",
    "煤炭": "稳定价值", "石油石化": "稳定价值",
    # 顺经济周期：顶/底规律较强
    "食品饮料": "顺经济周期", "房地产": "顺经济周期", "建筑材料": "顺经济周期",
    "建筑装饰": "顺经济周期", "非银金融": "顺经济周期", "钢铁": "顺经济周期",
    "基础化工": "顺经济周期", "有色金属": "顺经济周期",
    # 主题类成长：顶部规律最强（缺业绩支撑，纯情绪演绎），底部需右侧催化
    "电子": "主题成长", "计算机": "主题成长", "通信": "主题成长",
    "传媒": "主题成长", "国防军工": "主题成长",
    # 景气成长：景气上行可突破拥挤度上限，顶/底规律均偏弱
    "电力设备": "景气成长", "汽车": "景气成长", "机械设备": "景气成长",
    "家用电器": "景气成长", "医药生物": "景气成长",
}
# 其余行业（农林牧渔/纺织服饰/轻工制造/商贸零售/社会服务/美容护理/环保/综合等）归「其他」，
# 拥挤度信号按通用顶部/底部规律参考，不做资产类别特化解读。

ASSET_RULE_HINT = {
    "稳定价值": "顶/底规律均强：≥80%分位减仓兑现、≤20%分位可左侧配置",
    "顺经济周期": "顶/底规律较强：顶部有效，底部可左侧但需经济预期配合",
    "主题成长": "顶部规律最强（钝化即撤）；底部必须等右侧催化，禁左侧抄底",
    "景气成长": "顶/底规律均弱：景气上行可突破上限，需判断基本面持续性",
    "其他": "通用参考：≥80%警惕、≤20%关注",
}


def process_stock_file(args_tuple: tuple) -> pd.DataFrame | None:
    """单文件处理（供 ProcessPoolExecutor 调用）：取交易日/成交额/一级行业。"""
    file_path, end_date_str = args_tuple
    try:
        # 探测跳过版权行（首行不含表头则跳过）
        skip_rows = 0
        with open(file_path, "r", encoding="gbk", errors="ignore") as f:
            first_line = f.readline()
            if "股票代码" not in first_line and "交易日期" not in first_line:
                skip_rows = 1
        df = pd.read_csv(file_path, encoding="gbk", skiprows=skip_rows)
        if df.empty or COL_DATE not in df.columns or COL_AMOUNT not in df.columns:
            return None
        if COL_INDUSTRY1 not in df.columns:
            return None
        df = df[[COL_DATE, COL_AMOUNT, COL_INDUSTRY1]].copy()
        df = df[df[COL_INDUSTRY1].notna()]
        if df.empty:
            return None
        df[COL_DATE] = pd.to_datetime(df[COL_DATE])
        df = df[df[COL_DATE] <= pd.Timestamp(end_date_str)]
        if len(df) < 30:
            return None
        if len(df) > LOOKBACK_CALENDAR:
            df = df.tail(LOOKBACK_CALENDAR)
        return df
    except Exception:
        return None


def load_panel(data_dir: str, analysis_date: str, n_jobs: int = 12) -> pd.DataFrame:
    """并行加载全量股票的 日期×成交额×行业 面板。"""
    all_files = sorted(
        os.path.join(data_dir, f)
        for f in os.listdir(data_dir)
        if f.endswith(".csv") and not f.startswith("bj")
    )
    print(f"[拥挤度] 股票文件数: {len(all_files)}, 并行进程: {n_jobs}")
    t0 = datetime.now()
    parts = []
    with ProcessPoolExecutor(max_workers=n_jobs) as executor:
        futures = {executor.submit(process_stock_file, (f, analysis_date)): f for f in all_files}
        for fut in as_completed(futures):
            try:
                r = fut.result()
                if r is not None:
                    parts.append(r)
            except Exception:
                pass
    print(f"[拥挤度] 加载完成: {len(parts)} 只有效股票, 耗时 {(datetime.now() - t0).total_seconds():.1f}秒")
    if not parts:
        return pd.DataFrame()
    full = pd.concat(parts, ignore_index=True)
    return full


def compute_crowding(full: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """核心：行业成交额占比(MA5) + 近一年分位数。

    返回 (每日行业占比矩阵[date × industry], 全A每日成交额)。
    """
    # 全A每日成交额（亿）
    total = full.groupby(COL_DATE)[COL_AMOUNT].sum() / 1e8
    # 行业每日成交额占比（%）
    ind_amt = full.groupby([COL_DATE, COL_INDUSTRY1])[COL_AMOUNT].sum().unstack(fill_value=0) / 1e8
    ratio = ind_amt.div(total, axis=0) * 100
    # MA5 平滑（广发：熨平单日情绪波动）
    ratio_ma5 = ratio.rolling(MA_WINDOW, min_periods=1).mean()
    return ratio_ma5, total


def percentile_of_latest(series: pd.Series, window: int) -> float:
    """最新值在近 window 个交易日分布中的分位数（0-100）。"""
    s = series.dropna().tail(window)
    if len(s) < 20:
        return np.nan
    return round(float((s <= s.iloc[-1]).mean() * 100), 1)


def build_report(ratio_ma5: pd.DataFrame, total_amt: pd.Series, analysis_date: str) -> str:
    """生成 Markdown 摘要（可嵌入市场分析报告 L3.5 层）。"""
    latest = ratio_ma5.index[-1]
    lines = []
    lines.append(f"## 行业拥挤度（成交额占比 MA5，近一年分位）— {latest.strftime('%Y-%m-%d')}")
    lines.append("")

    # ===== 全市场情绪权重：存量博弈检测 =====
    amt20 = total_amt.tail(20).mean()
    amt60 = total_amt.tail(60).mean()
    amt_trend = "缩量" if amt20 < amt60 else "放量"
    stable_cols = [c for c in ratio_ma5.columns if ASSET_CLASS.get(c) == "稳定价值"]
    theme_cols = [c for c in ratio_ma5.columns if ASSET_CLASS.get(c) == "主题成长"]
    dumbbell_note = ""
    if stable_cols and theme_cols:
        stable_sum = ratio_ma5[stable_cols].sum(axis=1).tail(60)
        theme_sum = ratio_ma5[theme_cols].sum(axis=1).tail(60)
        corr = stable_sum.corr(theme_sum)
        game = "存量博弈显著（哑铃两端此消彼长），拥挤度模型权重↑" if corr < -0.3 else \
               "两端同向，增量/减量特征，拥挤度顶部规律可能钝化"
        dumbbell_note = f"稳定价值 vs 主题成长 60日占比相关系数 {corr:+.2f} → {game}"
    lines.append(f"**全市场成交额**：近20日均值 {amt20:.0f} 亿 vs 60日均值 {amt60:.0f} 亿（{amt_trend}）")
    if dumbbell_note:
        lines.append(f"\n**哑铃结构**：{dumbbell_note}")
    lines.append("")

    # ===== 行业拥挤度全量表 =====
    rows = []
    for ind in ratio_ma5.columns:
        s = ratio_ma5[ind]
        cur = s.iloc[-1]
        pct = percentile_of_latest(s, PERCENTILE_WINDOW)
        pct_5d_ago = percentile_of_latest(s.iloc[:-5], PERCENTILE_WINDOW) if len(s) > 25 else np.nan
        year = s.dropna().tail(PERCENTILE_WINDOW)
        direction = "↑" if not np.isnan(pct_5d_ago) and pct > pct_5d_ago + 2 else \
                    "↓" if not np.isnan(pct_5d_ago) and pct < pct_5d_ago - 2 else "→"
        asset = ASSET_CLASS.get(ind, "其他")
        if not np.isnan(pct) and pct >= TOP_QUANTILE:
            zone = "🔴拥挤"
        elif not np.isnan(pct) and pct <= BOTTOM_QUANTILE:
            zone = "🟢出清"
        else:
            zone = "中性"
        rows.append({
            "行业": ind, "资产类别": asset, "占比MA5(%)": round(float(cur), 2),
            "近一年分位(%)": pct, "5日方向": direction, "区间": zone,
            "近一年均值(%)": round(float(year.mean()), 2),
            "近一年最高(%)": round(float(year.max()), 2),
            "近一年最低(%)": round(float(year.min()), 2),
        })
    df = pd.DataFrame(rows).sort_values("近一年分位(%)", ascending=False)

    crowded = df[df["区间"] == "🔴拥挤"]
    cleared = df[df["区间"] == "🟢出清"]

    if not crowded.empty:
        lst = "、".join(f"{r['行业']}({r['近一年分位(%)']:.0f}%)" for _, r in crowded.iterrows())
        lines.append(f"**🔴 拥挤区（分位≥80%，顶部规律生效：对利好钝化、超额收益易阶段性见顶）**：{lst}")
    else:
        lines.append("**🔴 拥挤区（分位≥80%）**：无")
    if not cleared.empty:
        lst = "、".join(f"{r['行业']}({r['近一年分位(%)']:.0f}%)" for _, r in cleared.iterrows())
        lines.append(f"\n**🟢 出清区（分位≤20%，情绪出清：价值类可左侧关注，成长类等右侧催化）**：{lst}")
    else:
        lines.append("\n**🟢 出清区（分位≤20%）**：无")
    lines.append("")
    lines.append("| 行业 | 资产类别 | 占比MA5(%) | 近一年分位 | 5日方向 | 区间 | 年均值 | 年最高 | 年最低 |")
    lines.append("|------|---------|-----------|-----------|--------|------|--------|--------|--------|")
    for _, r in df.iterrows():
        lines.append(
            f"| {r['行业']} | {r['资产类别']} | {r['占比MA5(%)']:.2f} | {r['近一年分位(%)']:.1f}% | "
            f"{r['5日方向']} | {r['区间']} | {r['近一年均值(%)']:.2f} | {r['近一年最高(%)']:.2f} | {r['近一年最低(%)']:.2f} |"
        )
    lines.append("")

    # ===== 拥挤/出清行业的资产类别解读 =====
    focus = pd.concat([crowded, cleared])
    if not focus.empty:
        lines.append("**分资产类别解读**（广发三法则）：")
        lines.append("")
        lines.append("| 行业 | 区间 | 资产类别 | 适用规律 |")
        lines.append("|------|------|---------|---------|")
        for _, r in focus.iterrows():
            lines.append(f"| {r['行业']} | {r['区间']} | {r['资产类别']} | {ASSET_RULE_HINT[r['资产类别']]} |")
        lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="行业拥挤度分析（广发拥挤度方法论）")
    parser.add_argument("--date", default=None, help="分析截止日期 YYYY-MM-DD（默认自动检测最新交易日）")
    parser.add_argument("--workers", type=int, default=12, help="并行进程数")
    parser.add_argument("--summary-only", action="store_true", help="仅输出可嵌入报告的 Markdown 摘要")
    args = parser.parse_args()

    analysis_date = args.date or datetime.now().strftime("%Y-%m-%d")
    full = load_panel(DATA_DIR, analysis_date, args.workers)
    if full.empty:
        print("[拥挤度] 错误：没有有效数据")
        sys.exit(1)

    # 截断到 --date 且只保留近一年窗口
    full = full[full[COL_DATE] <= pd.Timestamp(analysis_date)]
    unique_dates = sorted(full[COL_DATE].unique())
    selected = unique_dates[-(PERCENTILE_WINDOW + MA_WINDOW):]
    full = full[full[COL_DATE].isin(selected)]
    actual_date = pd.Timestamp(selected[-1]).strftime("%Y-%m-%d")

    ratio_ma5, total_amt = compute_crowding(full)
    md = build_report(ratio_ma5, total_amt, actual_date)
    print(md)

    if not args.summary_only:
        out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kun", "data")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"industry_crowding_{actual_date.replace('-', '')}.md")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"\n[拥挤度] 已保存: {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()

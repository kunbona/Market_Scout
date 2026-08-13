"""
[AI-GENERATED] 本文件由 AI 辅助创建，非框架原始文件。
创建目的：统计 2024-01-01 至今每日炸板率，计算 z-score 和历史分位
用途：
  - 市场分析Skill中的脚本F，提供炸板率zscore章节
  - 用法1（完整输出）：python tools/bomb_rate_history.py
  - 用法2（指定日期）：python tools/bomb_rate_history.py --date 2026-06-11
  - 用法3（仅Markdown摘要）：python tools/bomb_rate_history.py --summary-only --date 2026-06-11
"""

import argparse
import os
import sys
import time
from collections import defaultdict
from multiprocessing import Pool, cpu_count

import numpy as np
import pandas as pd

from ._paths import require_quant_data_root

BJ_PREFIX = ('8',)
STAR_PREFIXES = ('68', '30')  # 科创板 + 创业板

START_DATE = "2024-01-01"
DATA_DIR = require_quant_data_root() + "/stock-trading-data-pro"
N_JOBS = max(cpu_count() - 3, 1)


def get_limit_ratio(code: str) -> float:
    """根据股票代码返回涨停比例（非百分比阈值，精确计算涨停价用）
    参数 code: 如 'sh600000', 'sz300750', 'bj920906' — 含交易所前缀
    """
    # 去交易所前缀：前2字符是 'sh'/'sz'/'bj'
    numeric = code[2:] if len(code) > 2 else code
    if numeric.startswith('8'):
        return 0.30  # 北交所 ±30%
    if numeric.startswith(('68', '30')):
        return 0.20  # 科创板+创业板 ±20%
    return 0.10  # 主板 ±10%


def process_stock_batch(file_paths, end_date=None):
    """处理一批股票，返回每日 {date: (touched, bomb)} 的字典
    Args:
        file_paths: 股票CSV文件路径列表
        end_date: 截止日期，None则使用数据中的最新日期
    """
    result = defaultdict(lambda: [0, 0])  # date -> [touched_count, bomb_count]

    for file_path in file_paths:
        try:
            code = os.path.basename(file_path).replace('.csv', '')
            limit_ratio = get_limit_ratio(code)

            # 快速读取CSV，跳过版权行
            skip_rows = 0
            with open(file_path, 'r', encoding='gbk', errors='ignore') as f:
                first_line = f.readline()
                if '股票代码' not in first_line and '交易日期' not in first_line:
                    skip_rows = 1

            df = pd.read_csv(file_path, encoding='gbk', skiprows=skip_rows,
                             usecols=['交易日期', '收盘价', '最高价', '前收盘价'],
                             dtype={'收盘价': float, '最高价': float, '前收盘价': float})

            if df.empty:
                continue

            # 过滤日期范围
            df = df[(df['交易日期'] >= START_DATE)]
            if end_date:
                df = df[(df['交易日期'] <= end_date)]
            if df.empty:
                continue

            # 按实际涨停价判断（A股涨停价=round(前收盘价×(1+涨跌幅限制), 2)）
            df['limit_up_price'] = (df['前收盘价'] * (1 + limit_ratio)).round(2)
            df['is_limit_up'] = df['收盘价'] >= df['limit_up_price']
            df['touched'] = df['最高价'] >= df['limit_up_price']
            df['is_bomb'] = df['touched'] & (~df['is_limit_up'])

            for _, row in df.iterrows():
                date = row['交易日期']
                if row['touched']:
                    result[date][0] += 1
                if row['is_bomb']:
                    result[date][1] += 1

        except Exception:
            continue

    return dict(result)


def get_summary_markdown(df, analysis_date):
    """返回可直接嵌入市场分析报告的Markdown摘要"""
    latest = df[df['date'] == pd.to_datetime(analysis_date)]
    if latest.empty:
        latest = df.iloc[-1:]
        analysis_date = str(df.iloc[-1]['date'].date())
    latest = latest.iloc[-1]

    mean_br = df['bomb_rate'].mean()
    std_br = df['bomb_rate'].std()
    pct_rank = (df['bomb_rate'] < latest['bomb_rate']).mean() * 100
    prev = df.iloc[-2] if len(df) >= 2 else None
    delta = latest['bomb_rate'] - prev['bomb_rate'] if prev is not None else 0

    # 近5日均值和近20日均值
    ma5 = df['bomb_rate'].tail(5).mean()
    ma20 = df['bomb_rate'].tail(20).mean()

    # 解释文本
    z = latest['zscore']
    if z > 2:
        level = "🔴 极度偏高"
        interp = f"炸板率{z:+.1f}σ，封板极度困难。历史仅2.9%的交易日达到此水平，通常对应恐慌性抛售。"
    elif z > 1:
        level = "🟠 偏高"
        interp = f"炸板率{z:+.1f}σ，封板偏困难。高于历史84%分位。"
    elif z > -1:
        level = "🟡 正常"
        interp = f"炸板率在正常范围（历史71%的交易日在此区间）。"
    elif z > -2:
        level = "🟢 偏低"
        interp = f"炸板率{z:+.1f}σ，封板质量好。低于历史88%分位，市场情绪健康。"
    else:
        level = "🟢 极度偏低"
        interp = f"炸板率{z:+.1f}σ，封板极度容易。历史仅2.2%的交易日达到此水平，通常为牛市爆发日。"

    # Δ炸板率信号
    if delta < -15:
        delta_signal = "🔥 炸板率骤降（<-15pp），历史胜率88%，次日强烈看多"
    elif delta < -5:
        delta_signal = f"✅ 炸板率下降{delta:+.1f}pp，历史胜率70%，次日偏多"
    elif delta > 15:
        delta_signal = "🔴 炸板率骤升（>+15pp），历史胜率仅30%，次日强烈看空"
    elif delta > 5:
        delta_signal = f"⚠️ 炸板率上升{delta:+.1f}pp，历史胜率37%，次日偏空"
    else:
        delta_signal = f"➖ 炸板率变化{delta:+.1f}pp，无显著方向信号"

    md = f"""### L0.5 💣 炸板率分析

| 指标 | 数值 | 历史分位 |
|------|------|---------|
| 触及涨停 | {int(latest['touched'])} 只 | — |
| 封板涨停 | {int(latest['limit_up'])} 只 | — |
| 炸板 | {int(latest['bomb'])} 只 | — |
| **炸板率** | **{latest['bomb_rate']:.1f}%** | **{pct_rank:.0f}%** |
| 全历史 Z-Score | **{z:+.2f}σ** | {level} |
| 120日滚动 Z-Score | **{latest['roll_zscore']:+.2f}σ** | — |
| Δ炸板率(日) | {delta:+.1f}pp | — |
| MA5炸板率 | {ma5:.1f}% | — |
| MA20炸板率 | {ma20:.1f}% | — |

> **判断**: {level} | {interp}
> **Δ信号**: {delta_signal}

**近5日炸板率趋势**:

| 日期 | 触及 | 炸板 | 炸板率 | Z-Score |
|------|------|------|--------|---------|
"""
    for _, r in df.tail(5).iterrows():
        md += f"| {str(r['date'].date())[-5:]} | {int(r['touched'])} | {int(r['bomb'])} | {r['bomb_rate']:.1f}% | {r['zscore']:+.2f}σ |\n"

    md += f"""
**历史极值参考**:
- 炸板率均值: {mean_br:.1f}% | 标准差: {std_br:.1f}%
- 最高: {df['bomb_rate'].max():.1f}% ({str(df.loc[df['bomb_rate'].idxmax(), 'date'].date())}) | 最低: {df['bomb_rate'].min():.1f}% ({str(df.loc[df['bomb_rate'].idxmin(), 'date'].date())})
- >+2σ（极度烂板）: {int((df['zscore']>2).sum())}天/{len(df)}天
- <-2σ（极度封板）: {int((df['zscore']<-2).sum())}天/{len(df)}天
"""
    return md


def main(end_date=None, summary_only=False):
    """主函数
    Args:
        end_date: 分析截止日期，None则自动检测最新交易日
        summary_only: True则只输出Markdown摘要（用于嵌入报告）
    """
    if end_date is None:
        end_date = "2099-12-31"  # 自动检测

    print("=" * 60)
    print(f"炸板率历史统计 (2024-01-01 ~ {end_date if end_date != '2099-12-31' else '最新'})")
    print("=" * 60)

    # 收集所有股票文件
    stock_files = []
    for f in os.listdir(DATA_DIR):
        if f.endswith('.csv') and len(f) > 6 and f[2:3].isdigit():
            stock_files.append(os.path.join(DATA_DIR, f))
    print(f"\n📂 找到 {len(stock_files)} 个股票文件")

    # 动态日期范围
    actual_end = end_date if end_date != "2099-12-31" else None

    # 分批处理
    batch_size = max(len(stock_files) // N_JOBS, 1)
    batches = [stock_files[i:i + batch_size] for i in range(0, len(stock_files), batch_size)]

    print(f"🔧 使用 {N_JOBS} 进程，共 {len(batches)} 批次")
    t0 = time.time()

    # 传递日期参数
    with Pool(N_JOBS) as pool:
        batch_results = pool.starmap(process_stock_batch,
                                     [(b, actual_end) for b in batches])

    t1 = time.time()
    print(f"✅ 数据处理完成，耗时 {t1 - t0:.1f} 秒")

    # 合并结果
    daily = defaultdict(lambda: [0, 0])
    for br in batch_results:
        for date, (touched, bomb) in br.items():
            daily[date][0] += touched
            daily[date][1] += bomb

    # 构建DataFrame
    rows = []
    for date in sorted(daily.keys()):
        touched, bomb = daily[date]
        bomb_rate = bomb / touched * 100 if touched > 0 else 0
        rows.append({
            'date': date,
            'touched': int(touched),
            'limit_up': int(touched - bomb),
            'bomb': int(bomb),
            'bomb_rate': round(bomb_rate, 2),
        })

    df = pd.DataFrame(rows)
    df['date'] = pd.to_datetime(df['date'])

    # ==================== Z-Score 计算 ====================
    # 使用全部历史数据计算均值和标准差
    mean_br = df['bomb_rate'].mean()
    std_br = df['bomb_rate'].std()
    df['zscore'] = (df['bomb_rate'] - mean_br) / std_br

    # 滚动 z-score（120日窗口）
    rolling_window = 120
    df['roll_mean'] = df['bomb_rate'].rolling(rolling_window, min_periods=20).mean()
    df['roll_std'] = df['bomb_rate'].rolling(rolling_window, min_periods=20).std()
    df['roll_zscore'] = (df['bomb_rate'] - df['roll_mean']) / df['roll_std']

    # ==================== 输出现状 ====================
    latest = df.iloc[-1]
    pct_rank = (df['bomb_rate'] < latest['bomb_rate']).mean() * 100

    print(f"\n{'=' * 60}")
    print("📊 炸板率统计概要")
    print(f"{'=' * 60}")
    print(f"  统计区间: {df['date'].min().date()} ~ {df['date'].max().date()}")
    print(f"  交易日数: {len(df)}")
    print(f"  炸板率均值: {mean_br:.2f}%")
    print(f"  炸板率标准差: {std_br:.2f}%")
    print(f"  炸板率中位数: {df['bomb_rate'].median():.2f}%")
    print(f"  最高炸板率: {df['bomb_rate'].max():.2f}% ({df.loc[df['bomb_rate'].idxmax(), 'date'].date()})")
    print(f"  最低炸板率: {df['bomb_rate'].min():.2f}% ({df.loc[df['bomb_rate'].idxmin(), 'date'].date()})")
    print(f"\n  🔴 最新 ({latest['date'].date()}):")
    print(f"    触及涨停: {int(latest['touched'])} 只")
    print(f"    封板涨停: {int(latest['limit_up'])} 只")
    print(f"    炸板: {int(latest['bomb'])} 只")
    print(f"    炸板率: {latest['bomb_rate']:.2f}%")
    print(f"    全历史 Z-Score: {latest['zscore']:+.2f}σ")
    print(f"    120日滚动 Z-Score: {latest['roll_zscore']:+.2f}σ")
    print(f"    历史分位: {pct_rank:.1f}%（高于 {100-pct_rank:.1f}% 的交易日）")

    # ==================== 分段统计 ====================
    # 动态计算截止日期段
    max_date_str = str(df['date'].max().date())
    from datetime import datetime, timedelta
    max_dt = datetime.strptime(max_date_str, '%Y-%m-%d')
    recent_1m = str(max_dt - timedelta(days=31))
    recent_1w = str(max_dt - timedelta(days=7))

    print(f"\n{'=' * 60}")
    print("📅 分段统计")
    print(f"{'=' * 60}")

    segments = [
        ("2024全年", "2024-01-01", "2024-12-31"),
        ("2025全年", "2025-01-01", "2025-12-31"),
        ("2026至今", "2026-01-01", max_date_str),
        ("2024Q1", "2024-01-01", "2024-03-31"),
        ("2024Q2", "2024-04-01", "2024-06-30"),
        ("2024Q3", "2024-07-01", "2024-09-30"),
        ("2024Q4", "2024-10-01", "2024-12-31"),
        ("2025Q1", "2025-01-01", "2025-03-31"),
        ("2025Q2", "2025-04-01", "2025-06-30"),
        ("2025Q3", "2025-07-01", "2025-09-30"),
        ("2025Q4", "2025-10-01", "2025-12-31"),
        ("2026Q1", "2026-01-01", "2026-03-31"),
        ("2026Q2至今", "2026-04-01", max_date_str),
        ("近1月", recent_1m, max_date_str),
        ("近1周", recent_1w, max_date_str),
    ]

    print(f"{'时段':<14} {'交易日':<8} {'触及均值':<10} {'炸板率均值':<12} {'炸板率中位':<12} {'Z-Score均值':<12}")
    print("-" * 68)
    for label, start, end in segments:
        seg = df[(df['date'] >= start) & (df['date'] <= end)]
        if seg.empty:
            continue
        seg_z = ((seg['bomb_rate'] - mean_br) / std_br).mean()
        print(f"{label:<14} {len(seg):<8} {seg['touched'].mean():<10.0f} "
              f"{seg['bomb_rate'].mean():<12.2f}% {seg['bomb_rate'].median():<12.2f}% "
              f"{seg_z:<+12.2f}σ")

    # ==================== Z-Score 分位数分析 ====================
    print(f"\n{'=' * 60}")
    print("📏 Z-Score 分布（全历史）")
    print(f"{'=' * 60}")
    for label, low, high in [
        ("极高 (>+2σ，极度烂板)", 2.0, 99),
        ("偏高 (+1σ ~ +2σ)", 1.0, 2.0),
        ("正常 (-1σ ~ +1σ)", -1.0, 1.0),
        ("偏低 (-2σ ~ -1σ)", -2.0, -1.0),
        ("极低 (<-2σ，极度封板)", -99, -2.0),
    ]:
        cnt = df[(df['zscore'] >= low) & (df['zscore'] < high)].shape[0]
        pct = cnt / len(df) * 100
        print(f"  {label}: {cnt} 天 ({pct:.1f}%)")

    # ==================== 极端炸板率日期 ====================
    print(f"\n{'=' * 60}")
    print("🔝 炸板率最高 Top 15 交易日")
    print(f"{'=' * 60}")
    top15 = df.nlargest(15, 'bomb_rate')[['date', 'touched', 'bomb', 'bomb_rate', 'zscore']]
    for _, r in top15.iterrows():
        print(f"  {r['date'].date()}  触及{r['touched']:<5}  炸板{r['bomb']:<5}  "
              f"炸板率{r['bomb_rate']:.1f}%  Z={r['zscore']:+.2f}σ")

    print(f"\n{'=' * 60}")
    print("🔚 炸板率最低 Top 15 交易日（封板质量最好）")
    print(f"{'=' * 60}")
    bot15 = df.nsmallest(15, 'bomb_rate')[['date', 'touched', 'bomb', 'bomb_rate', 'zscore']]
    for _, r in bot15.iterrows():
        print(f"  {r['date'].date()}  触及{r['touched']:<5}  炸板{r['bomb']:<5}  "
              f"炸板率{r['bomb_rate']:.1f}%  Z={r['zscore']:+.2f}σ")

    # ==================== 近期趋势 ====================
    print(f"\n{'=' * 60}")
    print("📈 近30交易日炸板率趋势")
    print(f"{'=' * 60}")
    recent = df.tail(30)
    print(f"{'日期':<12} {'触及':<6} {'炸板':<6} {'炸板率':<10} {'全历史Z':<10} {'120日滚Z':<10}")
    print("-" * 54)
    for _, r in recent.iterrows():
        print(f"{str(r['date'].date()):<12} {int(r['touched']):<6} {int(r['bomb']):<6} "
              f"{r['bomb_rate']:<10.2f}% {r['zscore']:<+10.2f}σ {r['roll_zscore']:<+10.2f}σ")

    # ==================== 保存结果 ====================
    analysis_date_str = str(df['date'].max().date()).replace('-', '')
    out_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "docs", "市场分析", f"炸板率历史统计_{analysis_date_str}.csv")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    df.to_csv(out_path, index=False, encoding='utf-8-sig')
    print(f"\n💾 完整数据已保存至: {out_path}")

    # ==================== 炸板率时序图（savefig 到 kun/data/） ====================
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS"]
        plt.rcParams["axes.unicode_minus"] = False

        plot_df = df.sort_values("date").tail(250).copy()  # 近250日
        mean_br = df["bomb_rate"].mean()
        std_br = df["bomb_rate"].std()

        fig, ax = plt.subplots(figsize=(14, 5))
        ax.plot(plot_df["date"], plot_df["bomb_rate"], color="#d62728", linewidth=1.0, label="炸板率")
        ax.axhline(mean_br, color="#2ca02c", linestyle="--", linewidth=1, label=f"历史均值 {mean_br:.1f}%")
        ax.axhline(mean_br + std_br, color="#ff7f0e", linestyle=":", linewidth=1, label=f"+1σ {mean_br+std_br:.1f}%")
        ax.axhline(mean_br - std_br, color="#ff7f0e", linestyle=":", linewidth=1, label=f"-1σ {mean_br-std_br:.1f}%")
        ax.fill_between(plot_df["date"], mean_br-std_br, mean_br+std_br, color="#2ca02c", alpha=0.08)
        ax.set_title(f"炸板率时序（近250日，截止 {analysis_date_str}）")
        ax.set_ylabel("炸板率 %")
        ax.legend(loc="upper right", fontsize=8)
        ax.grid(True, alpha=0.3)
        fig.autofmt_xdate()
        plt.tight_layout()

        fig_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "kun", "data", f"炸板率时序_{analysis_date_str}.png")
        os.makedirs(os.path.dirname(fig_path), exist_ok=True)
        fig.savefig(fig_path, dpi=100, bbox_inches="tight")
        plt.close(fig)
        print(f"📊 炸板率时序图已保存至: {fig_path}")
    except Exception as e:
        print(f"⚠️ 炸板率时序图生成失败（不影响主流程）: {e}")

    # ==================== Markdown 摘要 ====================
    analysis_date_dt = str(df['date'].max().date())
    md = get_summary_markdown(df, analysis_date_dt)

    if summary_only:
        print("\n" + "=" * 60)
        print("📋 Markdown 摘要（可直接嵌入报告）")
        print("=" * 60)
        print(md)
    else:
        print("\n" + md)

    # 返回关键数据供后续使用
    return df


def parse_args():
    parser = argparse.ArgumentParser(description='炸板率历史统计与zscore分析')
    parser.add_argument('--date', type=str, default=None,
                        help='分析截止日期 YYYY-MM-DD（默认自动检测最新交易日）')
    parser.add_argument('--summary-only', action='store_true', default=False,
                        help='仅输出Markdown摘要（用于嵌入市场分析报告）')
    return parser.parse_args()


if __name__ == '__main__':
    args = parse_args()
    main(end_date=args.date, summary_only=args.summary_only)

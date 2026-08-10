import os
import re
import sys
import pandas as pd
import numpy as np
from datetime import datetime
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor

# Raw data paths
STOCK_DATA_DIR = os.environ.get('QUANT_DATA_ROOT', '/Users/kun/Desktop/AGdata') + '/stock-trading-data-pro'
INDEX_DATA_DIR = os.environ.get('QUANT_DATA_ROOT', '/Users/kun/Desktop/AGdata') + '/stock-main-index-data'


def _read_stock_for_snapshot(file_path):
    """Lightweight: read one stock file, return last row's key stats."""
    try:
        file_name = os.path.basename(file_path)
        if file_name.lower().startswith('bj'):
            return None
        # Detect skip rows
        skip_rows = 0
        with open(file_path, 'r', encoding='gbk', errors='ignore') as f:
            first_line = f.readline()
            if '股票代码' not in first_line and '交易日期' not in first_line:
                skip_rows = 1
        # Find needed columns first by reading header only
        df_head = pd.read_csv(file_path, encoding='gbk', skiprows=skip_rows, nrows=0)
        all_cols = df_head.columns.tolist()

        date_col = next((c for c in all_cols if '交易日期' in c), None)
        amount_col = next((c for c in all_cols if '成交额' in c and '买入' not in c and '卖出' not in c), None)
        industry_col = next((c for c in all_cols if '申万一级行业' in c), None)
        inst_buy = next((c for c in all_cols if '机构资金买入额' in c), None)
        inst_sell = next((c for c in all_cols if '机构资金卖出额' in c), None)
        big_buy = next((c for c in all_cols if '大户资金买入额' in c), None)
        big_sell = next((c for c in all_cols if '大户资金卖出额' in c), None)

        if not all([date_col, amount_col]):
            return None

        need_cols = [date_col, amount_col]
        for c in [inst_buy, inst_sell, big_buy, big_sell, industry_col]:
            if c:
                need_cols.append(c)

        # Read full file but only needed columns, take tail
        df = pd.read_csv(file_path, encoding='gbk', skiprows=skip_rows, usecols=need_cols)
        if df.empty:
            return None
        df = df.tail(250)
        if df.empty:
            return None

        # Rename for consistent access
        df = df.rename(columns={date_col: 'date', amount_col: 'amount'})
        if industry_col:
            df = df.rename(columns={industry_col: 'industry'})

        df['date'] = pd.to_datetime(df['date'], errors='coerce')
        df = df.dropna(subset=['date'])

        # Compute main_net per row
        ib = df[inst_buy].fillna(0) if inst_buy else 0
        is_ = df[inst_sell].fillna(0) if inst_sell else 0
        bb = df[big_buy].fillna(0) if big_buy else 0
        bs = df[big_sell].fillna(0) if big_sell else 0
        df['main_net'] = (ib + bb) - (is_ + bs)

        records = []
        for _, row in df.iterrows():
            records.append({
                'date': row['date'],
                'amount': row['amount'] if pd.notna(row['amount']) else 0,
                'main_net': row['main_net'],
                'industry': str(row.get('industry', '未知')).strip() if 'industry' in df.columns else '未知',
            })
        return records
    except Exception:
        return None


def generate_daily_snapshots(target_dates=None):
    """
    Generate daily market snapshots directly from raw stock data.
    Covers ALL available trading days (or specified dates).
    Returns a list of daily summary dicts.
    """
    files = list(Path(STOCK_DATA_DIR).glob('*.csv'))
    print(f"   扫描 {len(files)} 只股票...")

    max_workers = max(1, min(12, (os.cpu_count() or 4) - 2))
    all_records = []
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        for result in executor.map(_read_stock_for_snapshot, files):
            if result:
                all_records.extend(result)

    if not all_records:
        return []

    df = pd.DataFrame(all_records)
    df['date'] = pd.to_datetime(df['date'])
    # Filter to target dates if specified
    if target_dates is not None:
        df = df[df['date'].isin(pd.to_datetime(target_dates))]

    # Convert amount from 元 to 亿
    df['amount_yi'] = df['amount'] / 1e8
    # Convert main_net from 万元 to 亿
    df['main_net_yi'] = df['main_net'] / 10000

    daily_stats = []
    for date, group in df.groupby('date'):
        total_amt = group['amount_yi'].sum()
        total_main = group['main_net_yi'].sum()

        # Top sector by average return is not available without price data,
        # but we can get top sector by total amount
        sector_amt = group.groupby('industry')['amount_yi'].sum()
        top_sector = sector_amt.idxmax() if len(sector_amt) > 0 else '-'

        daily_stats.append({
            'date': date.strftime('%Y%m%d'),
            'total_amount': total_amt,
            'main_net_inflow': total_main,
            'top_sector_amt': top_sector,
            'stock_count': len(group),
        })

    return sorted(daily_stats, key=lambda x: x['date'])


class TrendAnalyzer:
    def __init__(self, data_dir):
        self.data_dir = data_dir

    def parse_md_table(self, content, header_keyword):
        """
        Extract a markdown table following a header containing the keyword.
        """
        lines = content.split('\n')
        start_idx = -1
        for i, line in enumerate(lines):
            if header_keyword in line and line.startswith('#'):
                start_idx = i
                break

        if start_idx == -1:
            return None

        # Read table lines
        table_lines = []
        for i in range(start_idx + 1, len(lines)):
            line = lines[i].strip()
            if not line:
                if table_lines: break # End of table
                else: continue # Skip empty lines before table
            if line.startswith('#'): # Next header
                break
            table_lines.append(line)

        if not table_lines:
            return None

        try:
            # Filter out separator lines (containing ---)
            data_lines = [l for l in table_lines if '---' not in l]
            if len(data_lines) < 2: return None # Need header and at least one row

            # Parse header
            headers = [c.strip() for c in data_lines[0].strip('|').split('|')]

            # Parse rows
            data = []
            for line in data_lines[1:]:
                # Handle cases where value might contain | inside quotes? Unlikely for this report.
                row = [c.strip() for c in line.strip('|').split('|')]
                # Align row length with headers (handle missing trailing | or extra)
                if len(row) == len(headers):
                    data.append(row)
                elif len(row) > len(headers):
                    data.append(row[:len(headers)])
                else:
                    # Pad with None
                    data.append(row + [None]*(len(headers)-len(row)))

            return pd.DataFrame(data, columns=headers)
        except Exception as e:
            print(f"Error parsing table for {header_keyword}: {e}")
            return None

    def extract_value(self, content, key):
        """Extract key-value pairs like '- **Total Volume**: 1234.56'"""
        pattern = re.escape(key) + r":\s*(.*?)\s*$"
        match = re.search(pattern, content, re.MULTILINE)
        if match:
            return match.group(1)
        return None

    def run(self, from_raw=False):
        """
        Generate trend report.

        Args:
            from_raw: If True, generate daily snapshots from raw stock data
                      to fill gaps in report coverage.
        """
        # 1. Find all existing reports
        files = sorted([f for f in os.listdir(self.data_dir)
                       if f.startswith('sector_analysis_report_') and f.endswith('.md')])

        if not files and not from_raw:
            print("No reports found for trend analysis.")
            return

        if files:
            print(f"Found {len(files)} historical reports.")

        history = []

        # 2. Parse existing reports
        for f in files:
            date_str = f.replace('sector_analysis_report_', '').replace('.md', '')
            path = os.path.join(self.data_dir, f)
            with open(path, 'r', encoding='utf-8') as f_obj:
                content = f_obj.read()

            total_vol = self.extract_value(content, "**全市场成交额**")
            net_inflow = self.extract_value(content, "**主力净流入**")
            df_gainers = self.parse_md_table(content, "领涨板块")
            df_inflow = self.parse_md_table(content, "主力净流入")
            df_outflow = self.parse_md_table(content, "主力净流出")

            history.append({
                'date': date_str,
                'vol': total_vol,
                'net_inflow': net_inflow,
                'gainers': df_gainers,
                'inflow': df_inflow,
                'outflow': df_outflow,
                '_source': 'report',
            })

        # 3. Fill gaps with raw data snapshots
        if from_raw:
            report_dates = set(h['date'] for h in history)

            # Get all trading dates from sh000001 index
            sh_path = os.path.join(INDEX_DATA_DIR, 'sh000001.csv')
            all_dates = set()
            if os.path.exists(sh_path):
                idx_df = pd.read_csv(sh_path, encoding='gbk')
                idx_df['candle_end_time'] = pd.to_datetime(idx_df['candle_end_time'])
                # 范围：最新报告日往前推 74 个交易日（覆盖趋势图所需窗口）
                # 而非限制在已有报告 min~max（只有1天报告时会补不出历史）
                if history:
                    max_date = pd.Timestamp(max(h['date'] for h in history))
                    idx_df = idx_df[idx_df['candle_end_time'] <= max_date].sort_values('candle_end_time')
                    idx_df = idx_df.tail(74)
                all_dates = set(idx_df['candle_end_time'].dt.strftime('%Y%m%d'))

            missing_dates = sorted(all_dates - report_dates)
            print(f"Reports cover {len(report_dates)} days, "
                  f"{len(missing_dates)} trading days missing. Generating snapshots...")

            if missing_dates:
                snapshots = generate_daily_snapshots(missing_dates)
                for snap in snapshots:
                    vol_str = f"{snap['total_amount']:.2f} 亿" if snap['total_amount'] > 0 else "N/A"
                    inflow_str = f"{snap['main_net_inflow']:.2f} 亿" if abs(snap['main_net_inflow']) > 0.01 else "0.00 亿"
                    history.append({
                        'date': snap['date'],
                        'vol': vol_str,
                        'net_inflow': inflow_str,
                        'gainers': None,  # Raw snapshots don't have sector detail
                        'inflow': None,
                        'outflow': None,
                        '_source': 'snapshot',
                    })

        # Sort by date
        history.sort(key=lambda x: x['date'])

        # Generate Summary Report
        self.generate_report(history)

    def clean_number(self, s):
        if not s: return 0
        s = str(s).replace('亿', '').replace('%', '').replace(',', '')
        try:
            return float(s)
        except:
            return 0

    def generate_report(self, history):
        lines = []
        lines.append(f"# 市场趋势深度复盘 ({history[-1]['date']})")
        lines.append(f"> 自动生成于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"> 分析范围: {history[0]['date']} 至 {history[-1]['date']} (共 {len(history)} 个交易日)")
        lines.append(f"> 数据来源: {sum(1 for h in history if h.get('_source')=='report')} 份完整报告"
                     f" + {sum(1 for h in history if h.get('_source')=='snapshot')} 日原始快照")

        # 1. 市场情绪概览
        lines.append("\n## 1. 市场情绪趋势")
        lines.append("| 日期 | 全市场成交额 | 主力净流入 | 领涨板块 | 数据源 |")
        lines.append("| --- | --- | --- | --- | --- |")

        for h in history:
            source_tag = "📊" if h.get('_source') == 'report' else "📡"
            if h['gainers'] is not None and not h['gainers'].empty:
                top_sector = h['gainers'].iloc[0]['板块名称']
                top_gains = h['gainers'].iloc[0]['今日涨幅']
                sector_str = f"{top_sector} ({top_gains})"
            else:
                sector_str = "-"
            lines.append(f"| {h['date']} | {h['vol']} | {h['net_inflow']} | {sector_str} | {source_tag} |")

        # ... rest of generate_report unchanged
        # 2. 板块轮动分析
        lines.append("\n## 2. 强势板块持续性 (Top 3 出现次数)")

        sector_counts = {}
        sector_scores = {} # Score = 8 - rank (1st = 8pts, 8th = 1pt)

        recent_days = 5 # Analyze last 5 reports
        target_history = [h for h in history if h.get('gainers') is not None][-recent_days:]

        for h in target_history:
            if h['gainers'] is not None:
                for idx, row in h['gainers'].iterrows():
                    sec = row['板块名称']
                    sector_counts[sec] = sector_counts.get(sec, 0) + 1
                    # Rank score
                    score = 8 - int(idx) if isinstance(idx, (int, float)) else 8 - row.name
                    try:
                        score = 8 - int(idx) if isinstance(idx, (int, float)) else 8
                    except:
                        score = 1
                    if score > 0:
                        sector_scores[sec] = sector_scores.get(sec, 0) + score

        # Sort by Score
        sorted_sectors = sorted(sector_scores.items(), key=lambda x: x[1], reverse=True)

        lines.append("| 板块名称 | 综合热度分 | 上榜次数 (近5份报告) |")
        lines.append("| --- | --- | --- |")
        for sec, score in sorted_sectors[:10]:
            count = sector_counts.get(sec, 0)
            lines.append(f"| {sec} | {score} | {count} |")

        # 3. 资金抱团分析
        lines.append("\n## 3. 资金持续流入板块")
        inflow_counts = {}
        for h in target_history:
            if h['inflow'] is not None:
                for idx, row in h['inflow'].iterrows():
                    sec = row['板块名称']
                    inflow_counts[sec] = inflow_counts.get(sec, 0) + 1

        sorted_inflow = sorted(inflow_counts.items(), key=lambda x: x[1], reverse=True)

        lines.append("| 板块名称 | 流入榜上榜次数 (近5份报告) |")
        lines.append("| --- | --- |")
        for sec, count in sorted_inflow[:10]:
            lines.append(f"| {sec} | {count} |")

        # 4. 风险警示 (持续流出)
        lines.append("\n## 4. 资金持续流出板块 (风险)")
        outflow_counts = {}
        for h in target_history:
            if h['outflow'] is not None:
                for idx, row in h['outflow'].iterrows():
                    sec = row['板块名称']
                    outflow_counts[sec] = outflow_counts.get(sec, 0) + 1

        sorted_outflow = sorted(outflow_counts.items(), key=lambda x: x[1], reverse=True)

        lines.append("| 板块名称 | 流出榜上榜次数 (近5份报告) |")
        lines.append("| --- | --- |")
        for sec, count in sorted_outflow[:10]:
            lines.append(f"| {sec} | {count} |")

        # 5. 全量成交额趋势（含快照数据）
        lines.append("\n## 5. 全量成交额与主力资金趋势")
        lines.append("| 日期 | 成交额(亿) | 主力净流入(亿) |")
        lines.append("| --- | --- | --- |")
        for h in history:
            vol_val = self.clean_number(h['vol'])
            inf_val = self.clean_number(h['net_inflow'])
            lines.append(f"| {h['date']} | {vol_val:.0f} | {inf_val:.1f} |")

        # Save
        output_path = os.path.join(self.data_dir, "market_trend_summary.md")
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write("\n".join(lines))

        print(f"趋势分析报告已生成: {output_path}")

        # ==================== 成交额+主力资金双折线图（savefig 到 kun/data/） ====================
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS"]
            plt.rcParams["axes.unicode_minus"] = False

            recent = [h for h in history if h.get('vol') and h.get('net_inflow')][-74:]
            if not recent:
                raise ValueError("无可用趋势数据")
            last_date = recent[-1]['date'].replace("-", "")
            # 日期转 datetime，让 autofmt_xdate + 刻度稀疏化生效（字符串日期会全叠一起）
            dates = [pd.Timestamp(h['date']) for h in recent]
            vols = [self.clean_number(h['vol']) for h in recent]
            inflows = [self.clean_number(h['net_inflow']) for h in recent]

            fig, ax1 = plt.subplots(figsize=(14, 5))
            ax1.bar(dates, vols, color="#1f77b4", alpha=0.6, label="成交额(亿)")
            ax1.set_ylabel("成交额(亿)", color="#1f77b4")
            ax1.tick_params(axis="y", labelcolor="#1f77b4")

            ax2 = ax1.twinx()
            ax2.plot(dates, inflows, color="#d62728", linewidth=1.2, label="主力净流入(亿)")
            ax2.axhline(0, color="gray", linestyle="-", linewidth=0.5)
            ax2.set_ylabel("主力净流入(亿)", color="#d62728")
            ax2.tick_params(axis="y", labelcolor="#d62728")

            ax1.set_title(f"成交额与主力资金趋势（近74日）")
            ax1.grid(True, alpha=0.3)
            # x轴刻度稀疏化：只显示约12个日期，避免74天标签叠一起又不太稀
            from matplotlib.ticker import MaxNLocator
            ax1.xaxis.set_major_locator(MaxNLocator(nbins=12))
            fig.autofmt_xdate(rotation=45)
            plt.tight_layout()

            fig_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                    "kun", "data", f"趋势时序_{last_date}.png")
            os.makedirs(os.path.dirname(fig_path), exist_ok=True)
            fig.savefig(fig_path, dpi=100, bbox_inches="tight")
            plt.close(fig)
            print(f"📊 趋势时序图已保存至: {fig_path}")
        except Exception as e:
            print(f"⚠️ 趋势时序图生成失败（不影响主流程）: {e}")


if __name__ == "__main__":
    current_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(current_dir, "data")
    analyzer = TrendAnalyzer(data_dir)
    # 默认使用 from_raw=True 填补断档
    from_raw = "--from-raw" in sys.argv or len(sys.argv) == 1
    analyzer.run(from_raw=from_raw)

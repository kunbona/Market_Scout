"""
[AI-GENERATED] 短线情绪周期分析脚本
基于淘股吧情绪战法知识地图，计算连板高度、涨停生态、炸板率、情绪周期阶段判定。
输出 Markdown 报告，供市场分析报告整合使用。
"""
import pandas as pd
import numpy as np
import os
import sys
import warnings
from datetime import datetime, timedelta
from concurrent.futures import ProcessPoolExecutor, as_completed
from collections import Counter

warnings.filterwarnings('ignore')

STOCK_PATH = os.environ.get('QUANT_DATA_ROOT', '/Users/kun/Desktop/AGdata') + '/stock-trading-data-pro'
DAYS_TO_KEEP = 45  # 读取最近45天（足够追踪连板 + MA20 + 20日收益计算）

# 科创板/创业板代码前缀（20%涨跌停）
STAR_PREFIXES = ('sh688', 'sz300', 'sz301')
# 北交所代码前缀（30%涨跌停）
BJ_PREFIX = 'bj'
# 主板/中小板（10%涨跌停）
# ST 股票（5%涨跌停）— 用代码含 'st' 判断

# ==================== 阶段 → 操作模式映射表 ====================
# 来源：淘股吧短线知识地图（游资心法版）。
# 仓位档位按「赢面=胜率×涨跌空间比」五档金字塔命名：
#   观望（赢面<60%）/ 小仓（60-70%）/ 中仓（70-80%）/ 大仓（80-90%）/ 满仓（>90%）
# 「原仓位上限」对应第4节 stage_detail 里的既有仓位建议，二者并列展示，便于对照调优。
# match 为该映射命中的阶段 emoji 前缀（与第4节 stage 字符串 startswith 对齐）。
STAGE_MODE_MAP = [
    {
        'key': '冰点期', 'match': ('🧊',),
        '允许模式': '不操作或极小仓试错',
        '允许板位': '只跟踪不买入',
        '仓位档位': '观望（赢面<60%）',
        '原仓位上限': '20%',
        '纪律': '弱市忍手不动，等待破冰',
    },
    {
        'key': '修复期', 'match': ('🌱',),
        '允许模式': '低位新题材一进二（半路或打板），做弱转强；含分歧阶段低位接力',
        '允许板位': '一进二（低位新题材）',
        '仓位档位': '小仓（赢面60-70%）',
        '原仓位上限': '30%',
        '纪律': '轻仓试错，只做低位新题材接力',
    },
    {
        'key': '发酵期', 'match': ('🔥',),
        '允许模式': '二进三个股半路或打板接力，主线龙头持有',
        '允许板位': '二进三 / 主线龙头',
        '仓位档位': '中仓（赢面70-80%）',
        '原仓位上限': '70%',
        '纪律': '紧跟主流买强买龙，不碰杂毛',
    },
    {
        'key': '高潮期', 'match': ('🚀',),
        '允许模式': '不追高、不开新仓；已有龙头持有为主，准备分歧卖出',
        '允许板位': '—（持有为主，不接新高）',
        '仓位档位': '中仓封顶不加仓（赢面70-80%）',
        '原仓位上限': '逐步减至30%',
        '纪律': '过于一致即是卖点，等分歧',
    },
    {
        'key': '退潮期', 'match': ('📉',),
        '允许模式': '高位分歧持有龙头为主，其余卖出；空仓或控制仓位，只操作弱转强接力',
        '允许板位': '弱转强接力（唯一买点）',
        '仓位档位': '观望-小仓（赢面<70%）',
        '原仓位上限': '0%',
        '纪律': '大回撤基本都出现在弱势',
    },
    {
        'key': '轮动/分歧', 'match': ('⚡', '🔄'),
        '允许模式': '高低切，做低位新题材一进二',
        '允许板位': '一进二（低位新题材）',
        '仓位档位': '小仓（赢面60-70%）',
        '原仓位上限': '30%（首分）/ 20%（轮动）',
        '纪律': '做加法容易做减法难，控制频率',
    },
]


def resolve_stage_mapping(stage, limit_up_today, max_consecutive, avg_next_return):
    """根据阶段判定输出解析映射条目。

    返回 (entry, override_triggered)：
    - entry: STAGE_MODE_MAP 中命中的条目；未匹配返回 None。
    - override_triggered: 结构脆弱性触发（涨停≥50 但连板≤3 且次日溢价为负）
      时强制按修复期输出，便于在高涨跌停数假象下降级操作模式。
    """
    entry = None
    for e in STAGE_MODE_MAP:
        if any(stage.startswith(prefix) for prefix in e['match']):
            entry = e
            break
    override = False
    if entry is not None and entry['key'] != '修复期' and \
            limit_up_today >= 50 and max_consecutive <= 3 and avg_next_return < 0:
        entry = next(e for e in STAGE_MODE_MAP if e['key'] == '修复期')
        override = True
    return entry, override


def print_mapping_section(stage, entry, override, limit_up_today, max_consecutive, avg_next_return):
    """输出「情绪阶段 → 操作模式映射」完整章节（全量输出用）"""
    print("\n### 4.5 情绪阶段 → 操作模式映射\n")
    print("> 来源：淘股吧短线知识地图（游资心法版）。仓位档位按「赢面=胜率×涨跌空间比」五档金字塔："
          "观望(<60%) / 小仓(60-70%) / 中仓(70-80%) / 大仓(80-90%) / 满仓(>90%)\n")

    if entry is None:
        print("**当前阶段未匹配到映射条目（阶段未知），不输出操作建议**")
        return

    if override:
        print(f"⚠️ **结构脆弱性触发**：涨停≥50（{limit_up_today}只）但连板≤3（{max_consecutive}板）"
              f"且次日溢价为负（{avg_next_return:.2f}%）→ 映射降级按**修复期**输出\n")

    print("| 项目 | 内容 |")
    print("|------|------|")
    print(f"| 当前阶段 | {stage} |")
    print(f"| 允许操作模式 | {entry['允许模式']} |")
    print(f"| 允许板位/介入方式 | {entry['允许板位']} |")
    print(f"| 仓位档位（五档金字塔） | {entry['仓位档位']} |")
    print(f"| 原仓位上限建议 | {entry['原仓位上限']} |")
    print(f"| 纪律 | 「{entry['纪律']}」 |")

    if entry['key'] == '高潮期' and limit_up_today >= 100:
        print(f"\n🔺 **超常态上沿提示**：涨停 {limit_up_today} 只 ≥100，已达情绪极值，"
              "次日反转风险高，只卖不买、准备分歧兑现。")

    print("\n**完整映射表**:\n")
    print("| 阶段 | 允许操作模式 | 允许板位 | 仓位档位 | 原仓位上限 | 纪律 |")
    print("|------|--------------|----------|----------|------------|------|")
    for e in STAGE_MODE_MAP:
        marker = " ← 当前" if e is entry else ""
        print(f"| {e['key']}{marker} | {e['允许模式']} | {e['允许板位']} | "
              f"{e['仓位档位']} | {e['原仓位上限']} | 「{e['纪律']}」 |")


def print_mapping_summary(ctx):
    """输出映射摘要（--summary-only 用，紧凑版）"""
    entry = ctx['mapping_entry']
    if entry is None:
        print("\n- **操作模式映射**: 阶段未知，无映射输出")
        return
    if ctx['mapping_override']:
        print("- ⚠️ **结构脆弱性触发**：涨停≥50但连板≤3且次日溢价为负 → 映射按**修复期**输出")
    print("\n**阶段 → 操作模式映射**:")
    print(f"- 允许模式: {entry['允许模式']}")
    print(f"- 允许板位: {entry['允许板位']}")
    print(f"- 仓位档位: {entry['仓位档位']}（原仓位上限 {entry['原仓位上限']}）")
    print(f"- 纪律: 「{entry['纪律']}」")


def get_limit_ratio(code):
    """根据股票代码返回涨停比例（小数），用于计算实际涨停价"""
    # 去交易所前缀：前2字符是 'sh'/'sz'/'bj'
    numeric = code[2:] if len(code) > 2 else code
    if numeric.startswith('8'):
        return 0.30  # 北交所 ±30%
    if numeric.startswith(('68', '30')):
        return 0.20  # 科创板+创业板 ±20%
    return 0.10  # 主板 ±10%


def read_tail_csv(file_path, n_lines=40, encoding='gbk'):
    """读取CSV尾部数据，自动跳过版权行"""
    try:
        skip_rows = 0
        with open(file_path, 'r', encoding=encoding, errors='ignore') as f:
            first_line = f.readline()
            if '股票代码' not in first_line and '交易日期' not in first_line:
                skip_rows = 1
        df = pd.read_csv(file_path, encoding=encoding, skiprows=skip_rows)
        if not df.empty and len(df) > n_lines:
            return df.tail(n_lines).copy()
        return df
    except Exception:
        return None


def process_stock_sentiment(file_path):
    """处理单只股票的情绪指标（含烂板分析所需数据）"""
    try:
        code = os.path.basename(file_path).replace('.csv', '')
        df = read_tail_csv(file_path, n_lines=DAYS_TO_KEEP + 10)
        if df is None or df.empty:
            return None

        cols = df.columns.tolist()

        # 识别列
        date_col = None
        for c in cols:
            if '交易日期' in c:
                date_col = c
                break
        close_col = '收盘价' if '收盘价' in cols else None
        high_col = '最高价' if '最高价' in cols else None
        low_col = '最低价' if '最低价' in cols else None
        pre_close_col = '前收盘价' if '前收盘价' in cols else None
        name_col = '股票名称' if '股票名称' in cols else None
        amount_col = '成交额' if '成交额' in cols else None
        volume_col = '成交量' if '成交量' in cols else None

        # 09:55分钟价格（用于判断早盘强度）
        early_col = None
        for c in cols:
            c_stripped = c.strip()
            if '09:55' in c_stripped and '收盘' in c_stripped:
                early_col = c
                break

        # 流通市值列（用于计算换手率）
        circ_mv_col = None
        for c in cols:
            if '流通市值' in c.replace(' ', ''):
                circ_mv_col = c
                break
        # 换手率列（直接取用）
        turnover_col = None
        for c in cols:
            if '换手' in c:
                turnover_col = c
                break

        # 行业列
        industry_col = None
        for cand in ['新版申万一级行业名称', '所属行业', '一级行业']:
            for c in cols:
                if cand in c.strip():
                    industry_col = c
                    break
            if industry_col:
                break

        if any(v is None for v in [date_col, close_col, pre_close_col]):
            return None

        use_cols = [date_col, close_col, pre_close_col]
        if high_col:
            use_cols.append(high_col)
        if low_col:
            use_cols.append(low_col)
        if name_col:
            use_cols.append(name_col)
        if industry_col:
            use_cols.append(industry_col)
        if amount_col:
            use_cols.append(amount_col)
        if volume_col:
            use_cols.append(volume_col)
        if early_col:
            use_cols.append(early_col)
        if circ_mv_col:
            use_cols.append(circ_mv_col)
        if turnover_col:
            use_cols.append(turnover_col)

        df = df[use_cols].copy()
        df.columns = [c.strip() for c in df.columns]
        rename_map = {date_col: 'date', close_col: 'close', pre_close_col: 'pre_close'}
        if high_col:
            rename_map[high_col] = 'high'
        if low_col:
            rename_map[low_col] = 'low'
        if name_col:
            rename_map[name_col] = 'name'
        if industry_col:
            rename_map[industry_col] = 'industry'
        if amount_col:
            rename_map[amount_col] = 'amount'
        if volume_col:
            rename_map[volume_col] = 'volume'
        if early_col:
            rename_map[early_col] = 'early_price'
        if circ_mv_col:
            rename_map[circ_mv_col] = 'circ_mv'
        if turnover_col:
            rename_map[turnover_col] = 'turnover'
        df = df.rename(columns=rename_map)

        df['date'] = pd.to_datetime(df['date'], errors='coerce')
        df = df.dropna(subset=['date', 'close', 'pre_close'])
        df = df.sort_values('date')

        if len(df) < 3:
            return None

        # 计算涨跌幅
        df['pct_chg'] = (df['close'] - df['pre_close']) / df['pre_close'] * 100
        if 'high' in df.columns:
            df['high_pct'] = (df['high'] - df['pre_close']) / df['pre_close'] * 100
        if 'low' in df.columns:
            df['low_pct'] = (df['low'] - df['pre_close']) / df['pre_close'] * 100
        # 振幅
        if 'high_pct' in df.columns and 'low_pct' in df.columns:
            df['amp'] = df['high_pct'] - df['low_pct']
        # 早盘强度（09:55相对前收盘的涨幅）
        if 'early_price' in df.columns:
            df['early_pct'] = (df['early_price'] - df['pre_close']) / df['pre_close'] * 100
        # 换手率
        if 'turnover' not in df.columns and 'circ_mv' in df.columns and 'amount' in df.columns:
            df['turnover'] = df['amount'] / df['circ_mv'] * 100

        # ---- 均线位置计算（多维度仓位评估用） ----
        # 为减少内存开销，只对最新一行算 MA 位置，其余行填 None
        df['ma5'] = df['close'].rolling(5, min_periods=3).mean()
        df['ma10'] = df['close'].rolling(10, min_periods=5).mean()
        df['ma20'] = df['close'].rolling(20, min_periods=10).mean()
        df['ret_5d'] = df['close'].pct_change(5) * 100
        df['ret_20d'] = df['close'].pct_change(20) * 100
        # 当日是否上涨（资金流代理）
        df['is_advancing'] = df['pct_chg'] > 0

        # 获取涨停比例，计算实际涨停价（A股涨停价=round(前收×(1+比例),2)）
        limit_ratio = get_limit_ratio(code)
        df['limit_up_price'] = (df['pre_close'] * (1 + limit_ratio)).round(2)
        df['limit_down_price'] = (df['pre_close'] * (1 - limit_ratio)).round(2)

        # 标记涨停/跌停/炸板（用实际涨停价而非固定百分比）
        df['is_limit_up'] = df['close'] >= df['limit_up_price']
        df['is_limit_down'] = df['close'] <= df['limit_down_price']
        if 'high' in df.columns:
            df['is_bomb'] = (df['high'] >= df['limit_up_price']) & (~df['is_limit_up'])
        else:
            df['is_bomb'] = False

        # 当日是否有交易（成交额 > 0）
        if 'amount' in df.columns:
            df['is_trading'] = df['amount'] > 0
        else:
            df['is_trading'] = True

        # --- 构建返回记录 ---
        records = []
        for _, row in df.iterrows():
            rec = {
                'code': code,
                'date': row['date'],
                'pct_chg': row['pct_chg'],
                'is_limit_up': row['is_limit_up'],
                'is_limit_down': row['is_limit_down'],
                'is_bomb': row['is_bomb'],
                'is_trading': row['is_trading'],
                'industry': row.get('industry', '未知') if pd.notna(row.get('industry')) else '未知',
            }
            if 'name' in df.columns and pd.notna(row.get('name')):
                rec['name'] = row['name']
            # 烂板分析所需额外字段（仅涨停股有意义，但存储成本低）
            if 'high_pct' in df.columns:
                rec['high_pct'] = row['high_pct'] if pd.notna(row['high_pct']) else None
            if 'low_pct' in df.columns:
                rec['low_pct'] = row['low_pct'] if pd.notna(row['low_pct']) else None
            if 'amp' in df.columns:
                rec['amp'] = row['amp'] if pd.notna(row['amp']) else None
            if 'early_pct' in df.columns:
                rec['early_pct'] = row['early_pct'] if pd.notna(row['early_pct']) else None
            if 'turnover' in df.columns:
                rec['turnover'] = row['turnover'] if pd.notna(row['turnover']) else None
            # ---- 多维度仓位评估字段 ----
            rec['close'] = row['close'] if pd.notna(row['close']) else None
            rec['above_ma5'] = bool(row['close'] > row['ma5']) if pd.notna(row.get('ma5')) and pd.notna(row['close']) else None
            rec['above_ma10'] = bool(row['close'] > row['ma10']) if pd.notna(row.get('ma10')) and pd.notna(row['close']) else None
            rec['above_ma20'] = bool(row['close'] > row['ma20']) if pd.notna(row.get('ma20')) and pd.notna(row['close']) else None
            rec['ma5_val'] = float(row['ma5']) if pd.notna(row.get('ma5')) else None
            rec['ma10_val'] = float(row['ma10']) if pd.notna(row.get('ma10')) else None
            rec['ma20_val'] = float(row['ma20']) if pd.notna(row.get('ma20')) else None
            rec['ret_5d'] = row['ret_5d'] if pd.notna(row.get('ret_5d')) else None
            rec['ret_20d'] = row['ret_20d'] if pd.notna(row.get('ret_20d')) else None
            rec['is_advancing'] = bool(row['is_advancing']) if pd.notna(row.get('is_advancing')) else None
            rec['amount'] = float(row['amount']) if 'amount' in df.columns and pd.notna(row.get('amount')) else None
            records.append(rec)

        return records
    except Exception:
        return None


def compute_consecutive_limit_ups(records_by_stock, all_dates_sorted):
    """计算每只股票的连板情况"""
    date_set = set(all_dates_sorted)
    results = []

    for code, records in records_by_stock.items():
        if len(records) < 2:
            continue

        # 按日期排序
        records = sorted(records, key=lambda r: r['date'])
        date_to_rec = {r['date']: r for r in records}

        # 计算连板（从最近交易日向前回溯）
        consecutive = 0
        # 从最近的交易日开始
        for d in reversed(all_dates_sorted):
            if d in date_to_rec:
                rec = date_to_rec[d]
                if rec['is_limit_up'] and rec['is_trading']:
                    consecutive += 1
                else:
                    break
            else:
                break  # 数据断档，停止回溯

        if consecutive > 0:
            latest = records[-1]
            results.append({
                'code': code,
                'name': latest.get('name', code),
                'industry': latest.get('industry', '未知'),
                'consecutive_days': consecutive,
                'latest_date': latest['date'],
                'latest_pct': latest['pct_chg'],
                'is_today_limit_up': latest['is_limit_up'],
            })

    return results


def _run_analysis(analysis_date=None):
    """核心分析：计算情绪周期指标并输出 Markdown 报告。

    返回映射上下文字典（供 --summary-only 摘要复用）；数据不足等早退场景返回 None。
    """
    # 设置 stdout 为 UTF-8 编码，避免 Windows GBK 下 emoji 报错
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    if analysis_date is None:
        analysis_date = datetime.now().strftime('%Y-%m-%d')

    print(f"\n## 短线情绪周期分析 ({analysis_date})\n")

    # 获取所有股票文件
    stock_files = []
    for fname in os.listdir(STOCK_PATH):
        if fname.endswith('.csv'):
            stock_files.append(os.path.join(STOCK_PATH, fname))

    if not stock_files:
        print("**错误**: 未找到股票数据文件")
        return

    total_stocks = len(stock_files)
    print(f"*全市场 {total_stocks} 只股票，并行处理中...*\n")

    # 并行处理
    n_workers = min(12, os.cpu_count() or 4)
    all_records = []
    completed = 0

    with ProcessPoolExecutor(max_workers=n_workers) as executor:
        futures = {executor.submit(process_stock_sentiment, f): f for f in stock_files}
        for future in as_completed(futures):
            completed += 1
            if completed % 1000 == 0:
                print(f"  进度: {completed}/{total_stocks}...", flush=True)
            result = future.result()
            if result:
                all_records.extend(result)

    print(f"  完成: {completed}/{total_stocks}，有效记录 {len(all_records)} 条\n")

    if not all_records:
        print("**错误**: 无有效数据")
        return

    # 按日期组织数据
    df_all = pd.DataFrame(all_records)
    df_all['date'] = pd.to_datetime(df_all['date'])
    all_dates = sorted(df_all['date'].unique())
    latest_date = all_dates[-1]

    df_latest = df_all[df_all['date'] == latest_date]
    trading_count = df_latest['is_trading'].sum()

    # ==================== 1. 涨停/跌停/炸板统计 ====================
    print("### 1. 涨停生态总览\n")

    # 当日
    limit_up_today = df_latest[df_latest['is_limit_up']].shape[0]
    limit_down_today = df_latest[df_latest['is_limit_down']].shape[0]
    bomb_today = df_latest[df_latest['is_bomb']].shape[0]
    total_limit_touch = limit_up_today + bomb_today
    bomb_rate = bomb_today / total_limit_touch * 100 if total_limit_touch > 0 else 0

    print(f"| 指标 | 数值 |")
    print(f"|------|------|")
    print(f"| 涨停家数 | {limit_up_today} 只 |")
    print(f"| 跌停家数 | {limit_down_today} 只 |")
    print(f"| 触及涨停总数 | {total_limit_touch} 只 |")
    print(f"| 炸板数 | {bomb_today} 只 |")
    print(f"| 炸板率 | {bomb_rate:.1f}% |")
    ratio = limit_up_today / limit_down_today if limit_down_today > 0 else 99
    print(f"| 涨跌停比 | {ratio:.1f}:1 |")

    # 近5日趋势
    print("\n**近5日涨停/跌停趋势**:\n")
    recent_5d = sorted(all_dates)[-5:]
    print(f"| 日期 | 涨停 | 跌停 | 炸板 | 涨跌停比 |")
    print(f"|------|------|------|------|----------|")
    for d in recent_5d:
        dd = df_all[df_all['date'] == d]
        up = dd['is_limit_up'].sum()
        down = dd['is_limit_down'].sum()
        bomb = dd['is_bomb'].sum()
        r = up / down if down > 0 else 99
        print(f"| {d.strftime('%m-%d')} | {up} | {down} | {bomb} | {r:.1f} |")

    # ==================== 2. 连板天梯 ====================
    print("\n### 2. 连板天梯\n")

    # 按股票组织记录
    records_by_stock = {}
    for r in all_records:
        code = r['code']
        if code not in records_by_stock:
            records_by_stock[code] = []
        records_by_stock[code].append(r)

    # 计算连板
    consecutive_stocks = compute_consecutive_limit_ups(records_by_stock, all_dates)
    if consecutive_stocks:
        max_consecutive = max(s['consecutive_days'] for s in consecutive_stocks)
        df_cons = pd.DataFrame(consecutive_stocks)

        print(f"**最高连板**: {max_consecutive} 板\n")

        # 连板梯队分布
        print(f"| 连板数 | 股票数 | 代表标的 |")
        print(f"|--------|--------|----------|")
        for n in range(max_consecutive, 0, -1):
            tier = df_cons[df_cons['consecutive_days'] == n]
            count = len(tier)
            names = ', '.join(tier['name'].head(5).tolist())
            print(f"| {n}板 | {count} 只 | {names} |")

        # 最高板详情
        print(f"\n**最高连板个股 ({max_consecutive}板)**:\n")
        top_tier = df_cons[df_cons['consecutive_days'] == max_consecutive]
        print(f"| 代码 | 名称 | 行业 | 连板数 | 今日涨幅 |")
        print(f"|------|------|------|--------|----------|")
        for _, s in top_tier.iterrows():
            print(f"| {s['code']} | {s['name']} | {s['industry']} | {s['consecutive_days']}板 | {s['latest_pct']:.1f}% |")

        # 连板行业分布
        print(f"\n**连板行业分布 Top 8**:\n")
        industry_tier = df_cons.groupby('industry').agg(
            最高连板=('consecutive_days', 'max'),
            股票数=('code', 'count'),
            代表=('name', lambda x: ', '.join(x.head(3)))
        ).sort_values('股票数', ascending=False).head(8)
        print(f"| 行业 | 最高连板 | 股票数 | 代表标的 |")
        print(f"|------|----------|--------|----------|")
        for ind, row in industry_tier.iterrows():
            print(f"| {ind} | {int(row['最高连板'])}板 | {int(row['股票数'])}只 | {row['代表']} |")
    else:
        max_consecutive = 0
        print("**当日无连板股票**（情绪冰点信号）")

    # ==================== 2.5 烂板股分析 ====================
    print("\n### 2.5 烂板股分析\n")

    # 获取当日涨停股的详细数据
    limit_up_records = [r for r in all_records if r['date'] == latest_date and r['is_limit_up']]

    hard_board = []          # 硬板：早盘强 + 振幅小 = 一致封板
    divergence_board = []    # 分歧回封板：早盘强 + 振幅大 = 分歧转一致（偏正面）
    late_weak_board = []     # 尾盘烂板：早盘弱 + 振幅小 = 尾盘偷袭（负面）
    volatile_board = []      # 大波动烂板：早盘弱 + 振幅大 = 全天挣扎（最差）
    normal_board = []        # 普通板：中间地带

    for r in limit_up_records:
        early = r.get('early_pct')
        amp = r.get('amp')
        turnover_val = r.get('turnover')

        # 分类逻辑（基于09:55早盘强弱 + 全日振幅）
        # 关键区分：早盘弱才叫"烂板"，早盘强+振幅大是"分歧回封"（偏正面）
        early_strong = early is not None and early >= 6.0      # 09:55已涨6%+ = 早盘启动
        early_weak = early is not None and early < 4.0         # 09:55涨幅<4% = 尾盘才发力
        high_amp = amp is not None and amp > 7.0              # 振幅>7% = 盘中剧烈波动

        if early is None:
            # 无分钟数据，仅用振幅判断
            if amp is not None and amp < 5:
                r['board_type'] = '硬板（推定）'
                hard_board.append(r)
            elif amp is not None and amp > 8:
                r['board_type'] = '大波动烂板（推定）'
                volatile_board.append(r)
            else:
                r['board_type'] = '普通板（无分钟数据）'
                normal_board.append(r)
        elif early_strong and not high_amp:
            r['board_type'] = '硬板'
            hard_board.append(r)
        elif early_strong and high_amp:
            r['board_type'] = '分歧回封板'
            divergence_board.append(r)
        elif early_weak and not high_amp:
            r['board_type'] = '尾盘烂板'
            late_weak_board.append(r)
        elif early_weak and high_amp:
            r['board_type'] = '大波动烂板'
            volatile_board.append(r)
        else:
            # 中间地带（4%<=09:55<6%，或中等振幅）
            r['board_type'] = '普通板'
            normal_board.append(r)

    hard_count = len(hard_board)
    divergence_count = len(divergence_board)
    late_weak_count = len(late_weak_board)
    volatile_count = len(volatile_board)
    normal_count = len(normal_board)
    total_board = hard_count + divergence_count + late_weak_count + volatile_count + normal_count
    bad_board_total = late_weak_count + volatile_count  # 真正的烂板（早盘弱）

    if total_board > 0:
        hard_pct = hard_count / total_board * 100
        div_pct = divergence_count / total_board * 100
        bad_pct = bad_board_total / total_board * 100

        print(f"**涨停质量分布** ({total_board}只涨停):\n")
        print(f"| 类型 | 数量 | 占比 | 特征 |")
        print(f"|------|------|------|------|")
        print(f"| 🔒 **硬板** | {hard_count} 只 | {hard_pct:.1f}% | 09:55已涨6%+，振幅<7%，一致封板 |")
        print(f"| ⚡ **分歧回封板** | {divergence_count} 只 | {div_pct:.1f}% | 09:55已涨6%+，振幅>7%，强势分歧后回封（偏正面） |")
        print(f"| 🕐 **尾盘烂板** | {late_weak_count} 只 | {late_weak_count/total_board*100:.1f}% | 09:55涨幅<4%，尾盘偷袭封板 |")
        print(f"| 🌊 **大波动烂板** | {volatile_count} 只 | {volatile_count/total_board*100:.1f}% | 09:55涨幅<4%+振幅>7%，全天挣扎后勉强封板 |")
        print(f"| 📄 **普通板** | {normal_count} 只 | {normal_count/total_board*100:.1f}% | 各项指标居中间地带 |")

        # 涨停质量判断
        high_quality = hard_count + divergence_count  # 硬板+分歧回封 = 有质量的涨停
        high_quality_pct = high_quality / total_board * 100
        if high_quality_pct >= 50:
            quality_judge = "✅ **涨停质量优秀**"
            quality_note = f"高质量涨停(硬板+分歧回封)占比{high_quality_pct:.0f}%，封板意愿强"
        elif high_quality_pct >= 30:
            quality_judge = "⚠️ **涨停质量一般**"
            quality_note = f"高质量涨停仅{high_quality_pct:.0f}%，尾盘烂板+大波动烂板占主导"
        else:
            quality_judge = "❌ **涨停质量较差**"
            quality_note = f"高质量涨停仅{high_quality_pct:.0f}%，烂板泛滥，封板意愿弱"

        print(f"\n**质量判断**: {quality_judge} — {quality_note}")

        # 烂板行业分布（仅真正的烂板：尾盘+大波动）
        bad_boards = late_weak_board + volatile_board
        if bad_boards:
            bad_by_ind = {}
            for b in bad_boards:
                ind = b.get('industry', '未知')
                if ind not in bad_by_ind:
                    bad_by_ind[ind] = []
                bad_by_ind[ind].append(b)

            print(f"\n**烂板行业分布**:\n")
            print(f"| 行业 | 烂板数 | 占该行业涨停比 | 代表标的 |")
            print(f"|------|--------|---------------|----------|")
            # 按烂板数排序
            sorted_bad = sorted(bad_by_ind.items(), key=lambda x: len(x[1]), reverse=True)[:8]
            for ind, boards in sorted_bad:
                # 计算该行业总涨停数
                ind_total = sum(1 for r in limit_up_records if r.get('industry', '未知') == ind)
                ratio = len(boards) / ind_total * 100 if ind_total > 0 else 0
                names = ', '.join([b.get('name', b['code']) for b in boards[:3]])
                print(f"| {ind} | {len(boards)}只 | {ratio:.0f}% | {names} |")

        # 烂板个股明细（Top 15，按振幅降序）
        if bad_boards:
            bad_sorted = sorted(bad_boards, key=lambda r: r.get('amp') or 0, reverse=True)
            print(f"\n**烂板个股明细** (振幅Top 15):\n")
            print(f"| 代码 | 名称 | 行业 | 类型 | 振幅 | 09:55涨幅 | 换手率 |")
            print(f"|------|------|------|------|------|----------|--------|")
            for b in bad_sorted[:15]:
                name = b.get('name', b['code'])
                ind = b.get('industry', '未知')
                bt = b.get('board_type', '')
                amp_str = f"{b['amp']:.1f}%" if b.get('amp') is not None else '-'
                early_str = f"{b['early_pct']:.1f}%" if b.get('early_pct') is not None else '-'
                to_str = f"{b['turnover']:.1f}%" if b.get('turnover') is not None else '-'
                print(f"| {b['code']} | {name} | {ind} | {bt} | {amp_str} | {early_str} | {to_str} |")
    else:
        print("**当日无涨停股**")

    # 烂板次日表现（昨日烂板 → 今日走势）
    print(f"\n### 2.6 烂板次日表现\n")

    # 昨日烂板 vs 硬板 vs 分歧回封 今日表现对比
    if len(all_dates) >= 2:
        prev_date = all_dates[-2]
        df_prev_all = [r for r in all_records if r['date'] == prev_date]

        # 用同样逻辑重新分类昨日涨停
        prev_limit_ups = [r for r in df_prev_all if r['is_limit_up']]
        prev_hard, prev_divergence, prev_bad, prev_normal = [], [], [], []
        for r in prev_limit_ups:
            early = r.get('early_pct')
            amp = r.get('amp')
            early_strong = early is not None and early >= 6.0
            early_weak = early is not None and early < 4.0
            high_amp = amp is not None and amp > 7.0

            if early_strong and not high_amp:
                prev_hard.append(r)
            elif early_strong and high_amp:
                prev_divergence.append(r)
            elif early_weak:
                prev_bad.append(r)  # 尾盘或大波动烂板
            else:
                prev_normal.append(r)

        prev_bad_codes = set(r['code'] for r in prev_bad)
        prev_hard_codes = set(r['code'] for r in prev_hard)
        prev_div_codes = set(r['code'] for r in prev_divergence)

        def get_today_returns(code_set):
            rets = []
            for code in code_set:
                today_recs = [r for r in all_records if r['code'] == code and r['date'] == latest_date]
                if today_recs:
                    rets.append(today_recs[0]['pct_chg'])
            avg_r = np.mean(rets) if rets else 0
            win_r = sum(1 for r in rets if r > 0) / len(rets) * 100 if rets else 0
            return len(code_set), avg_r, win_r

        hard_n, hard_avg, hard_win = get_today_returns(prev_hard_codes)
        div_n, div_avg, div_win = get_today_returns(prev_div_codes)
        bad_n, bad_avg, bad_win = get_today_returns(prev_bad_codes)

        if bad_n + hard_n + div_n > 0:
            print(f"| 类型 | 昨日数 | 今日均涨幅 | 今日上涨占比 |")
            print(f"|------|--------|-----------|-------------|")
            print(f"| 🔒 昨日硬板 | {hard_n}只 | {hard_avg:.2f}% | {hard_win:.0f}% |")
            print(f"| ⚡ 昨日分歧回封 | {div_n}只 | {div_avg:.2f}% | {div_win:.0f}% |")
            print(f"| 🕐 昨日烂板 | {bad_n}只 | {bad_avg:.2f}% | {bad_win:.0f}% |")

            # 烂板出妖股信号（昨日烂板今日涨>3%）
            bad_weak_to_strong = []
            for code in prev_bad_codes:
                today_recs = [r for r in all_records if r['code'] == code and r['date'] == latest_date]
                if today_recs:
                    ret = today_recs[0]['pct_chg']
                    if ret > 3:
                        bad_weak_to_strong.append({
                            'code': code,
                            'name': today_recs[0].get('name', code),
                            'industry': today_recs[0].get('industry', '未知'),
                            'return': ret,
                        })

            if bad_weak_to_strong:
                print(f"\n🔥 **烂板出妖股 — 昨日烂板今日弱转强** ({len(bad_weak_to_strong)}只):\n")
                print(f"| 代码 | 名称 | 行业 | 今日涨幅 |")
                print(f"|------|------|------|----------|")
                for w in bad_weak_to_strong[:10]:
                    print(f"| {w['code']} | {w['name']} | {w['industry']} | {w['return']:.1f}% |")
            else:
                print(f"\n⚠️ 昨日烂板今日无弱转强信号，情绪偏弱")

            # 对比分析
            if hard_n > 0 and bad_n > 0:
                diff = bad_avg - hard_avg
                if diff > 0:
                    print(f"\n💡 烂板今日表现**优于**硬板（差值+{diff:.1f}%），市场偏好分歧转一致")
                else:
                    print(f"\n📉 烂板今日表现**弱于**硬板（差值{diff:.1f}%），追硬板更安全")

            if div_n > 0:
                print(f"📊 分歧回封板今日均涨幅{div_avg:.2f}%，上涨占比{div_win:.0f}% — ", end='')
                if div_avg > bad_avg:
                    print("分歧回封优于烂板，说明市场认可'早盘强+分歧后回封'模式")
                else:
                    print("分歧回封也未能持续，分歧信号偏负面")
        else:
            print("昨日无涨停股数据")
    else:
        print("数据不足（需要至少2个交易日）")

    # ==================== 3. 涨停次日溢价 ====================
    print("\n### 3. 涨停次日溢价\n")

    avg_next_return = 0
    today_returns_win_rate = 50  # 默认中性

    if len(all_dates) >= 2 and limit_up_today > 0:
        # 找到昨日涨停的股票，计算今日涨幅
        prev_date = all_dates[-2]
        df_prev = df_all[df_all['date'] == prev_date]
        prev_limit_up_codes = set(df_prev[df_prev['is_limit_up']]['code'].tolist())

        today_returns = []
        for code in prev_limit_up_codes:
            today_rec = df_latest[df_latest['code'] == code]
            if not today_rec.empty:
                today_returns.append(today_rec.iloc[0]['pct_chg'])

        if today_returns:
            avg_next_return = np.mean(today_returns)
            today_returns_win_rate = sum(1 for r in today_returns if r > 0) / len(today_returns) * 100
            print(f"| 指标 | 数值 |")
            print(f"|------|------|")
            print(f"| 昨日涨停数 | {len(prev_limit_up_codes)} 只 |")
            print(f"| 今日平均涨幅 | {avg_next_return:.2f}% |")
            print(f"| 今日上涨占比 | {today_returns_win_rate:.1f}% |")
            print(f"| 溢价判断 | {'✅ 正溢价' if avg_next_return > 0 else '❌ 负溢价（短线接力亏钱）'} |")
            if today_returns_win_rate < 50:
                print(f"| ⚠️ 风险提示 | 虽然平均涨幅为正，但上涨占比仅{today_returns_win_rate:.0f}%，多数昨日涨停股今日下跌，接力胜率低 |")
        else:
            print("数据不足，无法计算溢价")
    else:
        print("数据不足（需要至少2个交易日）")

    # ==================== 4. 情绪周期阶段判定 ====================
    print("\n### 4. 情绪周期阶段判定\n")

    # 计算近5日滚动数据
    recent_dates_5 = all_dates[-5:] if len(all_dates) >= 5 else all_dates
    rolling_up = []
    rolling_down = []
    rolling_bomb = []
    for d in recent_dates_5:
        dd = df_all[df_all['date'] == d]
        rolling_up.append(dd['is_limit_up'].sum())
        rolling_down.append(dd['is_limit_down'].sum())
        rolling_bomb.append(dd['is_bomb'].sum())

    avg_up_5d = np.mean(rolling_up)
    avg_down_5d = np.mean(rolling_down)
    avg_bomb_rate = np.mean([b / (u + b) * 100 if (u + b) > 0 else 0
                             for u, b in zip(rolling_up, rolling_bomb)])

    # 涨停趋势方向
    if len(rolling_up) >= 3:
        up_trend = rolling_up[-1] - rolling_up[0]
    else:
        up_trend = 0

    # ---- 阶段判定逻辑 ----
    # 优先级: 冰点 > 退潮 > 修复 > 高潮 > 发酵 > 分歧
    stage = "未知"
    stage_confidence = "低"
    stage_detail = ""

    # 溢价值（昨日涨停今日表现）
    win_rate = today_returns_win_rate if 'today_returns_win_rate' in locals() else 50
    avg_return = avg_next_return if 'avg_next_return' in locals() else 0

    # 涨停趋势结构：V型修复判定（前期低谷→当前反弹）
    is_v_recovery = (len(rolling_up) >= 5 and
                     rolling_up[-1] > 40 and
                     min(rolling_up[:-1]) < rolling_up[-1] * 0.6)

    if limit_up_today <= 20 and max_consecutive <= 3:
        # --- 冰点期 ---
        stage = "🧊 **冰点期**"
        stage_confidence = "高" if limit_up_today <= 10 and limit_down_today >= 5 else "中"
        stage_detail = (
            "涨停稀少、连板高度压缩至3板以下，恐慌情绪蔓延，无主流热点。\n"
            "  - **操作策略**: 空仓观望为主；关注逆势抗跌个股（可能为下一周期龙头）\n"
            "  - **仓位上限**: 20%"
        )
    elif (limit_down_today >= 10 and limit_up_today < 50 and up_trend <= 0) or \
         (limit_up_today < 30 and limit_down_today >= 10):
        # --- 退潮期：跌停多 + 涨停被压制 + 趋势不向上 ---
        stage = "📉 **退潮期**"
        stage_confidence = "高" if limit_down_today >= 15 and up_trend < -15 else "中"
        stage_detail = (
            "龙头滞涨或断板，核按钮频现，中位股率先跳水，连板高度骤降。\n"
            "  - **操作策略**: **空仓是铁律**；清仓跟风股；退潮期的新题材和高位股是亏损之源\n"
            "  - **仓位上限**: 0%"
        )
    elif is_v_recovery and max_consecutive <= 5:
        # --- V型修复：前期退潮→今日涨停大幅反弹 ---
        stage = "🌱 **修复期（V型反弹）**"
        stage_confidence = "高" if win_rate < 50 else "中"
        lo = min(rolling_up[:-1]) if len(rolling_up) >= 2 else 0
        stage_detail = (
            f"市场经历短暂退潮（涨停最低降至{lo}只）后快速修复，今日涨停回升至{limit_up_today}只。\n"
            f"但连板高度仅{max_consecutive}板，涨停次日上涨占比仅{win_rate:.0f}%，说明是「高低切」而非主升延续。\n"
            "  - **操作策略**: 聚焦新启动的低位首板/二板（新周期试错），回避旧周期高位跟风\n"
            "  - **仓位上限**: 30%"
        )
    elif limit_up_today >= 80 and max_consecutive >= 7 and avg_bomb_rate < 20:
        # --- 高潮期 ---
        stage = "🚀 **高潮期**"
        stage_confidence = "高" if limit_up_today >= 100 else "中"
        stage_detail = (
            "龙头加速缩量涨停，百股涨停，散户疯狂追涨，断板次日照样反包。\n"
            "  - **操作策略**: 逐步减仓后排跟风，仅保留龙头底仓；不追高非核心标的\n"
            "  - **仓位上限**: 逐步减至30%"
        )
    elif limit_up_today >= 50 and max_consecutive >= 5 and up_trend > 0:
        # --- 发酵期 ---
        stage = "🔥 **发酵期**"
        stage_confidence = "高" if max_consecutive >= 6 and win_rate >= 50 else "中"
        stage_detail = (
            "龙头确立（3-4板+），板块跟风批量涨停，成交量放大，梯队完整。\n"
            "  - **操作策略**: 加仓龙头，挖掘低位补涨\n"
            "  - **仓位上限**: 70%"
        )
    elif max_consecutive >= 3 and limit_up_today >= 30:
        # --- 分歧/轮动期（默认）---
        if max_consecutive >= 5:
            stage = "⚡ **分歧期（首分）**"
            stage_detail = (
                "高潮后的首次分歧，多空博弈激烈，是筹码交换后继续向上的关键节点。\n"
                "  - **操作策略**: 尾盘分歧低吸龙头（首阴战法）；若次日不能强修复则清仓\n"
                "  - **仓位上限**: 30%"
            )
        else:
            stage = "🔄 **轮动/混沌期**"
            stage_detail = (
                f"涨停数量尚可（{limit_up_today}只）但连板高度有限（{max_consecutive}板），板块快速轮动无主线。\n"
                "  - **操作策略**: 轻仓参与日内最强板块首板；不做接力；等主线明朗\n"
                "  - **仓位上限**: 20%"
            )
        stage_confidence = "低"

    print(f"**阶段判定**: {stage}")
    print(f"**置信度**: {stage_confidence}")
    print(f"\n**量化依据**:")
    print(f"| 指标 | 当前值 | 冰点 | 启动 | 发酵 | 高潮 | 退潮 |")
    print(f"|------|--------|------|------|------|------|------|")
    print(f"| 涨停家数 | {limit_up_today} | <10 | 10-40 | 50+ | 80+ | <30 |")
    print(f"| 连板高度 | {max_consecutive}板 | ≤3板 | 3-4板 | 5-7板 | 7板+ | ≤4板 |")
    print(f"| 跌停家数 | {limit_down_today} | 倍增 | 减少 | <5 | 近乎0 | ≥10 |")
    print(f"| 炸板率 | {bomb_rate:.0f}% | >30% | 25-30% | <20% | <20% | >30% |")

    print(f"\n{stage_detail}")

    # ==================== 4.5 情绪阶段 → 操作模式映射 ====================
    # 基于第4节阶段判定结果 + 结构脆弱性触发条件，映射到操作模式/板位/五档仓位/纪律
    mapping_entry, mapping_override = resolve_stage_mapping(
        stage, limit_up_today, max_consecutive, avg_next_return)
    print_mapping_section(stage, mapping_entry, mapping_override,
                          limit_up_today, max_consecutive, avg_next_return)

    # 映射上下文（供 --summary-only 摘要复用）
    mapping_ctx = {
        'analysis_date': analysis_date,
        'stage': stage,
        'stage_confidence': stage_confidence,
        'limit_up_today': limit_up_today,
        'limit_down_today': limit_down_today,
        'bomb_rate': bomb_rate,
        'max_consecutive': max_consecutive,
        'avg_next_return': avg_next_return,
        'win_rate': win_rate,
        'mapping_entry': mapping_entry,
        'mapping_override': mapping_override,
    }

    # ==================== 5. 情绪温度计 ====================
    print("\n### 5. 情绪温度计\n")

    # 打包6个子维度评分
    # 维度1: 涨停数量 (归一化到0-100, 100只涨停=满分)
    s1 = min(limit_up_today * 1.0, 100)
    # 维度2: 连板高度 (7板=满分)
    s2 = min(max_consecutive / 7 * 100, 100)
    # 维度3: 涨跌停比 (3:1=满分)
    s3 = min(ratio / 3 * 100, 100)
    # 维度4: 炸板率 (反转, <15%=满分)
    s4 = max(0, 100 - bomb_rate * 3.33)
    # 维度5: 溢价 (正向, >3%=满分; 负溢价=惩罚)
    s5 = max(0, min(avg_next_return * 20 + 50, 100))
    # 维度6: 涨停趋势 (上升趋势为正; 下降低于-20=严重惩罚)
    s6 = max(0, min(50 + up_trend * 1.5, 100))

    scores = [s1, s2, s3, s4, s5, s6]
    overall = np.mean(scores)

    labels = ['涨停数量', '连板高度', '涨跌停比', '封板率(1-炸板率)', '次日溢价', '涨停趋势']
    print(f"| 维度 | 评分(0-100) | 解读 |")
    print(f"|------|-------------|------|")

    interpretations = [
        "极低" if s1 < 20 else "偏低" if s1 < 40 else "正常" if s1 < 70 else "火热",
        "受压制" if s2 < 30 else "恢复中" if s2 < 55 else "正常" if s2 < 80 else "极强",
        "恐慌" if s3 < 30 else "偏弱" if s3 < 55 else "健康" if s3 < 80 else "极度乐观",
        "封板困难" if s4 < 30 else "封板不稳" if s4 < 55 else "正常" if s4 < 80 else "封板强",
        "亏钱效应" if s5 < 40 else "中性" if s5 < 60 else "正溢价",
        "快速萎缩" if s6 < 35 else "缓慢下降" if s6 < 50 else "平稳" if s6 < 65 else "加速上升",
    ]
    for i in range(6):
        print(f"| {labels[i]} | {scores[i]:.0f} | {interpretations[i]} |")

    # 温度计颜色（与阶段判定协同，修复期有结构性缺陷时降级）
    structural_weakness = (max_consecutive < 5 and today_returns_win_rate < 50)
    if overall >= 80 and not structural_weakness:
        temp_bar = "🔴🔴🔴🔴🔴"
        temp_label = "过热 — 高潮特征，警惕退潮"
    elif overall >= 60 and not structural_weakness:
        temp_bar = "🟠🟠🟠🟠⚪"
        temp_label = "偏热 — 发酵期，积极做多"
    elif overall >= 60 and structural_weakness:
        temp_bar = "🟡🟡🟡🟡⚪"
        temp_label = "表面偏热但结构脆弱 — 涨停多但连板低+溢价弱，高低切特征，按修复期操作"
    elif overall >= 40:
        temp_bar = "🟡🟡🟡⚪⚪"
        temp_label = "温和 — 修复/启动期，轻仓试错"
    elif overall >= 20:
        temp_bar = "🔵🔵⚪⚪⚪"
        temp_label = "偏冷 — 冰点末期，等待信号"
    else:
        temp_bar = "⚪⚪⚪⚪⚪"
        temp_label = "极冷 — 深度冰点，空仓观望"

    print(f"\n**情绪温度**: {overall:.0f}/100  {temp_bar}")
    print(f"**状态**: {temp_label}")

    # ==================== 6. 策略适配建议 ====================
    print("\n### 6. 情绪-策略适配建议\n")

    if stage.startswith("🧊"):
        strategies = [
            ("空仓观望", "★★★★★", "等待冰点后的第一个换手2板"),
            ("首板试错", "★★☆☆☆", "仅限新题材、10:30前封板的硬板"),
        ]
    elif stage.startswith("🌱"):
        strategies = [
            ("首板/二板接力", "★★★★☆", "聚焦换手充分的2板，拒绝一字"),
            ("新题材试错", "★★★☆☆", "冰点新题材，情绪高低切"),
        ]
    elif stage.startswith("🔥"):
        strategies = [
            ("聚焦总龙头", "★★★★★", "放量分歧板/缩量加速板都是买点"),
            ("低位补涨挖掘", "★★★★☆", "同属性首板/二板"),
            ("板块龙套利", "★★★☆☆", "买不到总龙头时的次选"),
        ]
    elif stage.startswith("🚀"):
        strategies = [
            ("持有龙头底仓", "★★★★☆", "不轻易下车，但不再加仓"),
            ("卖出跟风", "★★★★★", "后排跟风逐步清仓"),
            ("不再追高", "★★★★★", "高潮次日不接不买"),
        ]
    elif stage.startswith("📉"):
        strategies = [
            ("空仓", "★★★★★", "退潮期空仓能避免80%的亏损"),
            ("防御配置", "★☆☆☆☆", "仅红利/公用事业等低波动品种"),
        ]
    elif stage.startswith("⚡"):
        strategies = [
            ("首阴低吸总龙头", "★★★☆☆", "仅限总龙头，不参与跟风分歧"),
            ("观望为主", "★★★★☆", "等待首分修复信号再行动"),
        ]
    else:
        strategies = [("观望", "★★★☆☆", "等待明确的周期信号")]

    print(f"| 策略动作 | 推荐度 | 说明 |")
    print(f"|----------|--------|------|")
    for name, stars, note in strategies:
        print(f"| {name} | {stars} | {note} |")

    # ==================== 7. 多维度仓位评估 ====================
    print("\n### 7. 多维度仓位评估\n")
    print("> 仓位 = 趋势(30%) + 宽度(25%) + 资金流(20%) + 情绪(15%) + 结构(10%)\n")

    # ---- 聚合最新交易日的全市场数据 ----
    latest_records = [r for r in all_records if r['date'] == latest_date and r['is_trading']]
    n_stocks = len(latest_records)
    if n_stocks == 0:
        print("**数据不足，无法评估**")
        return mapping_ctx

    # -- 维度1: 趋势强度 (30%) --
    # 子维度: MA5上方% + MA10上方% + MA20上方% + 5日涨跌幅中位数方向
    above_ma5_pct = sum(1 for r in latest_records if r.get('above_ma5')) / n_stocks * 100
    above_ma10_pct = sum(1 for r in latest_records if r.get('above_ma10')) / n_stocks * 100
    above_ma20_pct = sum(1 for r in latest_records if r.get('above_ma20')) / n_stocks * 100
    median_ret_5d = np.median([r['ret_5d'] for r in latest_records if r.get('ret_5d') is not None])

    # 趋势分 = MA5(0.35) + MA10(0.25) + MA20(0.25) + 5d方向(0.15)
    # MA子分: 0%→0分, 80%→100分
    trend_s5 = min(above_ma5_pct / 80 * 100, 100)
    trend_s10 = min(above_ma10_pct / 80 * 100, 100)
    trend_s20 = min(above_ma20_pct / 80 * 100, 100)
    trend_s_dir = min(max(50 + median_ret_5d * 5, 0), 100)  # 5d中位数: 0%→50分, +10%→100分
    trend_score = trend_s5 * 0.35 + trend_s10 * 0.25 + trend_s20 * 0.25 + trend_s_dir * 0.15

    # -- 维度2: 市场宽度 (25%) --
    # 子维度: MA20上方% + 20日收益中位数 + 上涨家数占比
    advancing_pct = sum(1 for r in latest_records if r.get('is_advancing')) / n_stocks * 100
    ret_20d_list = [r['ret_20d'] for r in latest_records if r.get('ret_20d') is not None]
    median_ret_20d = np.median(ret_20d_list) if ret_20d_list else 0

    # MA20上方: 30%→50分(中性), 60%→100分
    breadth_ma20 = min(above_ma20_pct / 60 * 100, 100)
    # 20日收益中位数: -10%→30分, 0%→50分, +10%→80分
    breadth_ret = min(max(50 + median_ret_20d * 5, 10), 100)
    # 上涨占比: 30%→30分, 50%→60分, 70%→100分
    breadth_adv = min(advancing_pct / 70 * 100, 100)
    breadth_score = breadth_ma20 * 0.4 + breadth_ret * 0.35 + breadth_adv * 0.25

    # -- 维度3: 资金流代理 (20%) --
    # 用成交量加权上涨比替代真实主力资金流（无法从日线CSV获取机构数据）
    up_amount = sum(r['amount'] for r in latest_records if r.get('is_advancing') and r.get('amount'))
    total_amount = sum(r['amount'] for r in latest_records if r.get('amount'))
    up_amount_ratio = up_amount / total_amount * 100 if total_amount > 0 else 50

    # 上涨成交占比: 40%→20分(恐慌抛售), 50%→50分, 65%→100分
    flow_score = min(max((up_amount_ratio - 40) / 25 * 100, 0), 100)

    # -- 维度4: 情绪质量 (15%) --
    # 直接复用情绪温度计 overall 分（已是0-100）
    sentiment_score = overall

    # -- 维度5: 风格结构 (10%) --
    # 行业集中度 = Top3行业占全市场总成交额的比例（过高→结构性风险）
    ind_amount = {}
    for r in latest_records:
        ind = r.get('industry', '未知')
        amt = r.get('amount') or 0
        ind_amount[ind] = ind_amount.get(ind, 0) + amt
    ind_total = sum(ind_amount.values())
    top3_share = sum(sorted(ind_amount.values(), reverse=True)[:3]) / ind_total * 100 if ind_total > 0 else 60

    # Top3集中度: 30%→100分(分散健康), 50%→60分, 70%→20分(过度集中)
    structure_score = min(max(100 - (top3_share - 30) * 2, 0), 100)

    # ---- 加权总分 ----
    weights = {'趋势强度': 0.30, '市场宽度': 0.25, '资金流代理': 0.20, '情绪质量': 0.15, '风格结构': 0.10}
    weighted_score = (
        trend_score * 0.30 +
        breadth_score * 0.25 +
        flow_score * 0.20 +
        sentiment_score * 0.15 +
        structure_score * 0.10
    )

    # 总分 0-100 → 仓位 0%-100%（线性映射，最低5%底仓观察）
    position_pct = max(5, min(100, weighted_score))

    # ---- 输出 ----
    print("**各维度评分明细**:\n")
    print(f"| 维度 | 权重 | 原始分 | 加权 | 关键子指标 |")
    print(f"|------|------|--------|------|-----------|")
    print(f"| 📈 趋势强度 | 30% | {trend_score:.0f} | {trend_score*0.30:.0f} | "
          f"MA5上方{above_ma5_pct:.0f}% / MA10{above_ma10_pct:.0f}% / MA20{above_ma20_pct:.0f}% / 5d中位{median_ret_5d:+.1f}% |")
    print(f"| 📊 市场宽度 | 25% | {breadth_score:.0f} | {breadth_score*0.25:.0f} | "
          f"MA20上方{above_ma20_pct:.0f}% / 20d中位{median_ret_20d:+.1f}% / 上涨{advancing_pct:.0f}% |")
    print(f"| 💰 资金流代理 | 20% | {flow_score:.0f} | {flow_score*0.20:.0f} | "
          f"上涨成交占比{up_amount_ratio:.0f}%（全市场{total_amount/1e8:.0f}亿） |")
    print(f"| 🎭 情绪质量 | 15% | {sentiment_score:.0f} | {sentiment_score*0.15:.0f} | "
          f"情绪温度计（6维综合） |")
    print(f"| 🏗️ 风格结构 | 10% | {structure_score:.0f} | {structure_score*0.10:.0f} | "
          f"Top3行业成交集中度{top3_share:.0f}% |")

    print(f"\n| | **加权总分** | **建议仓位** | |")
    print(f"|------|------|------|------|")

    # 仓位区间定性标签
    if position_pct >= 70:
        label = "🟢 积极"
        note = "全面做多，各维度共振向上"
    elif position_pct >= 50:
        label = "🟡 中性偏多"
        note = "多数维度向好，可适度参与"
    elif position_pct >= 30:
        label = "🟠 谨慎"
        note = "多空矛盾，控制仓位等方向明朗"
    else:
        label = "🔴 防御"
        note = "多个维度恶化，以保全本金为先"

    print(f"| | **{weighted_score:.0f}/100** | **{position_pct:.0f}%** ({label}) | {note} |")

    # 各维度背离/共振检测
    scores_list = [
        ('趋势强度', trend_score),
        ('市场宽度', breadth_score),
        ('资金流代理', flow_score),
        ('情绪质量', sentiment_score),
        ('风格结构', structure_score),
    ]
    high_dims = [(n, s) for n, s in scores_list if s >= 60]
    low_dims = [(n, s) for n, s in scores_list if s < 40]
    if high_dims and low_dims:
        high_names = ', '.join(n for n, _ in high_dims)
        low_names = ', '.join(n for n, _ in low_dims)
        print(f"\n> ⚠️ **维度背离**: {high_names}偏多 vs {low_names}偏空 → 结构性行情，选对方向比仓位更重要")

    # ---- 反骨仔检测 ----
    # 触发条件: 仓位<50%（整体偏防御）或 维度严重背离（结构性行情）
    show_rebels = position_pct < 50 or (high_dims and low_dims)
    if show_rebels:
        if position_pct < 50:
            trigger_reason = "整体仓位建议偏低"
        else:
            trigger_reason = "各维度严重背离，结构性分化"
        print("\n### 7.5 🦴 反骨仔检测 — 逆势强势行业与个股\n")
        print(f"> {trigger_reason}，以下行业/个股无视大盘弱势独立走强。\n")

        # 按行业聚合
        ind_metrics = {}
        for r in latest_records:
            ind = r.get('industry', '未知')
            if ind == '未知':
                continue
            if ind not in ind_metrics:
                ind_metrics[ind] = {
                    'n': 0, 'above_ma20': 0, 'above_ma10': 0,
                    'ret_20d_list': [], 'ret_5d_list': [],
                    'advancing': 0, 'stocks': [],
                }
            m = ind_metrics[ind]
            m['n'] += 1
            if r.get('above_ma20'): m['above_ma20'] += 1
            if r.get('above_ma10'): m['above_ma10'] += 1
            if r.get('ret_20d') is not None: m['ret_20d_list'].append(r['ret_20d'])
            if r.get('ret_5d') is not None: m['ret_5d_list'].append(r['ret_5d'])
            if r.get('is_advancing'): m['advancing'] += 1
            # 收集强势个股（站上MA20且20d正收益），计算MA回调安全分
            if r.get('above_ma20') and (r.get('ret_20d') or 0) > 0:
                close = r.get('close') or 0
                ma20 = r.get('ma20_val') or close
                ma10 = r.get('ma10_val') or close
                ma5 = r.get('ma5_val') or close
                ret20 = r.get('ret_20d') or 0
                ret5 = r.get('ret_5d') or 0

                # 距均线偏离度（%）
                dist_ma20 = (close - ma20) / ma20 * 100 if ma20 > 0 else 0
                dist_ma10 = (close - ma10) / ma10 * 100 if ma10 > 0 else 0

                # === MA回调安全分（满分100）===
                # 1. MA20乖离安全(50%): 距MA20 0~8%最优，<0或>15%扣分
                if dist_ma20 < -3:
                    ma_safety = max(0, 40 + dist_ma20 * 10)   # 跌破MA20→严厉扣分
                elif dist_ma20 < 0:
                    ma_safety = 65 + dist_ma20 * 8             # 略低于MA20→轻扣
                elif dist_ma20 <= 6:
                    ma_safety = 85 + (6 - dist_ma20) * 2.5     # 0~6%→85~100（黄金区间）
                elif dist_ma20 <= 15:
                    ma_safety = max(0, 85 - (dist_ma20 - 6) * 4.5)  # 6~15%→逐渐扣分
                else:
                    ma_safety = max(0, 45 - (dist_ma20 - 15) * 0.8)  # >15%→超买

                # 2. 趋势确认(30%): 20d正收益但不极端
                trend_score = min(ret20 / 40 * 100, 100) if ret20 > 0 else 0

                # 3. 近期稳定性(20%): 5日微调（-3%~+5%最优，回调到位）
                if -3 <= ret5 <= 5:
                    stability = 100 - abs(ret5 - 1) * 10
                elif ret5 > 5:
                    stability = max(0, 70 - (ret5 - 5) * 3)
                else:
                    stability = max(0, 70 + ret5 * 10)

                safety = ma_safety * 0.50 + trend_score * 0.30 + stability * 0.20

                # 乖离标签
                if dist_ma20 < 0:
                    bias_label = f'⚡贴MA20({dist_ma20:+.0f}%)'
                elif dist_ma20 < 5:
                    bias_label = f'🟢靠MA20({dist_ma20:+.0f}%)'
                elif dist_ma20 < 10:
                    bias_label = f'🟡离MA20({dist_ma20:+.0f}%)'
                elif dist_ma20 < 20:
                    bias_label = f'🟠远MA20({dist_ma20:+.0f}%)'
                else:
                    bias_label = f'🔴超买({dist_ma20:+.0f}%)'

                m['stocks'].append({
                    'code': r['code'],
                    'name': r.get('name', r['code']),
                    'ret_20d': ret20,
                    'ret_5d': ret5,
                    'amount': r.get('amount') or 0,
                    'dist_ma20': dist_ma20,
                    'safety': safety,
                    'bias_label': bias_label,
                })

        # 行业逆势强度 = (MA20%偏离 + 20d中位偏离 + 上涨比)
        contrarian_industries = []
        for ind, m in ind_metrics.items():
            if m['n'] < 10:
                continue
            ind_ma20 = m['above_ma20'] / m['n'] * 100
            ind_ret20 = np.median(m['ret_20d_list']) if m['ret_20d_list'] else 0
            ind_adv = m['advancing'] / m['n'] * 100

            # 必须同时跑赢市场
            if ind_ma20 > above_ma20_pct + 5 and ind_ret20 > median_ret_20d:
                ma20_dev = ind_ma20 - above_ma20_pct
                ret20_dev = ind_ret20 - median_ret_20d
                ma20_score = min(ma20_dev / 30 * 100, 100)
                ret20_score = min(max(ret20_dev / 15 * 100, 0), 100)
                adv_score = min(ind_adv / 70 * 100, 100)
                rebellion_score = ma20_score * 0.4 + ret20_score * 0.4 + adv_score * 0.2

                contrarian_industries.append({
                    'industry': ind, 'n': m['n'],
                    'ma20_pct': ind_ma20, 'ret20_median': ind_ret20,
                    'advancing_pct': ind_adv, 'rebellion_score': rebellion_score,
                    'top_stocks': sorted(m['stocks'], key=lambda s: s['safety'], reverse=True)[:5],
                })

        if contrarian_industries:
            contrarian_industries.sort(key=lambda x: x['rebellion_score'], reverse=True)

            print("**逆势强势行业**:\n")
            print(f"| 行业 | 逆势分 | MA20上方 | 20d中位 | 上涨比 | 样本 | 代表个股(20d涨幅) |")
            print(f"|------|--------|---------|---------|--------|------|-------------------|")
            for ci in contrarian_industries:  # 全量输出，不做截断
                stocks_str = ', '.join(
                    f"{s['name']}(安全{s['safety']:.0f} {s['bias_label']})"
                    for s in ci['top_stocks'][:3]
                )
                print(f"| {ci['industry']} | {ci['rebellion_score']:.0f} | {ci['ma20_pct']:.0f}% | "
                      f"{ci['ret20_median']:+.1f}% | {ci['advancing_pct']:.0f}% | {ci['n']} | {stocks_str} |")

            # 全行业个股明细（每行业5只，按安全分排序）
            print(f"\n**🦴 反骨仔全量明细** (按MA回调安全分排序，贴均线=更安全):\n")
            for ci in contrarian_industries:
                print(f"\n**{ci['industry']}** (逆势分 {ci['rebellion_score']:.0f} | MA20上方 {ci['ma20_pct']:.0f}% | 20d中位 {ci['ret20_median']:+.1f}%)\n")
                print(f"| 代码 | 名称 | 安全分 | MA20乖离 | 20d涨幅 | 5d涨幅 | 成交额(亿) |")
                print(f"|------|------|--------|----------|---------|--------|-----------|")
                for s in ci['top_stocks']:
                    amt = s['amount'] / 1e8 if s['amount'] else 0
                    print(f"| {s['code']} | {s['name']} | {s['safety']:.0f} | {s['bias_label']} | "
                          f"{s['ret_20d']:+.1f}% | {s['ret_5d']:+.1f}% | {amt:.1f} |")
        else:
            print("**未检测到逆势强势行业** — 市场全面走弱，无结构性机会，建议空仓等待。")

    print(f"\n---")
    print(f"*分析日期: {analysis_date} | 数据覆盖: {total_stocks} 只股票 | 情绪周期: {stage}*")

    return mapping_ctx


def main(analysis_date=None, summary_only=False):
    """主入口：全量输出或 --summary-only 摘要（摘要含阶段判定 + 操作模式映射）"""
    if not summary_only:
        _run_analysis(analysis_date)
        return

    # 摘要模式：全量分析照常跑（捕获其输出丢弃），只回放关键摘要 + 映射
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ctx = _run_analysis(analysis_date)

    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    print(f"## 短线情绪周期分析摘要 ({analysis_date})\n")
    if ctx is None:
        print("**错误**: 数据不足或分析失败，无法生成摘要")
        return
    print(f"- **阶段判定**: {ctx['stage']}（置信度 {ctx['stage_confidence']}）")
    print(f"- 涨停 {ctx['limit_up_today']} 只 / 跌停 {ctx['limit_down_today']} 只 / "
          f"炸板率 {ctx['bomb_rate']:.1f}% / 最高连板 {ctx['max_consecutive']}板 / "
          f"次日溢价 {ctx['avg_next_return']:.2f}%（上涨占比 {ctx['win_rate']:.0f}%）")
    print_mapping_summary(ctx)


# [AI-MODIFIED] 周复盘系统 - 改为 argparse 形式支持 --date 参数（保留位置参数以兼容旧调用）
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='短线情绪周期分析')
    parser.add_argument('date_pos', nargs='?', default=None,
                        help='分析日期 YYYYMMDD（位置参数，向后兼容）')
    parser.add_argument('--date', type=str, default=None,
                        help='分析日期 YYYYMMDD（默认最新交易日）')
    parser.add_argument('--summary-only', action='store_true',
                        help='只输出摘要：阶段判定 + 阶段→操作模式映射（全量章节不打印）')
    args = parser.parse_args()
    # 优先使用 --date，未提供则回退到位置参数
    main(args.date or args.date_pos, summary_only=args.summary_only)

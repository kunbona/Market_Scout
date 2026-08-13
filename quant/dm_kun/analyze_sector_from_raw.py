import pandas as pd
import numpy as np
import os
import sys
import io
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
from tqdm import tqdm

from ._paths import require_quant_data_root
from .trend_analyzer import TrendAnalyzer
try:
    from financial_analyzer import FinancialAnalyzer
except ImportError:
    FinancialAnalyzer = None  # 可选模块，缺失时跳过财务分析

# 配置
_QDR = require_quant_data_root()
DATA_DIR = _QDR + '/stock-trading-data-pro'
FINANCE_DIR = _QDR + '/stock-fin-data-xbx'
ANALYST_DIE = _QDR + '/stock-analyst-ranking'
DAYS_TO_KEEP = 250  # 扩大回看窗口，确保有足够数据计算 MA20/MA60

# [AI-MODIFIED] 支持 --date 参数指定分析日期，未指定时取最新
ANALYSIS_DATE = None

# 设置显示
pd.set_option('display.max_rows', 100)
pd.set_option('display.width', 1000)
pd.set_option('display.unicode.ambiguous_as_wide', True)
pd.set_option('display.unicode.east_asian_width', True)

def read_tail_csv(file_path, n_lines=200, encoding='gbk'):
    """
    安全读取 CSV 的方法：
    使用 pandas 官方读取，自动探测跳过版权行（邢不行的文件第一行可能是版权声明）。
    """
    try:
        # 探测是否需要跳过第一行
        skip_rows = 0
        with open(file_path, 'r', encoding=encoding, errors='ignore') as f:
            first_line = f.readline()
            if '股票代码' not in first_line and '交易日期' not in first_line:
                skip_rows = 1
                
        df = pd.read_csv(file_path, encoding=encoding, skiprows=skip_rows)
        if not df.empty and len(df) > n_lines:
            return df.tail(n_lines).copy()
        return df
    except Exception as e:
        return None

def process_stock_file(file_path):
    try:
        # 0. 快速过滤：如果是北交所股票，直接跳过
        file_name = os.path.basename(file_path)
        if file_name.lower().startswith('bj'):
            return None

        # 1. 读取数据 (优化：只读尾部)
        # 注意：这里我们假设 read_tail_csv 能正确返回带列名的 DataFrame
        df = read_tail_csv(file_path, n_lines=DAYS_TO_KEEP + 20)
        
        if df is None or df.empty:
            return None
            
        cols = df.columns.tolist()

        
        # 确定关键列名
        date_col = '交易日期'
        code_col = '股票代码'
        name_col = '股票名称'
        open_col = '开盘价' # 确保开盘价存在
        close_col = '收盘价'
        pre_close_col = '前收盘价'
        amount_col = '成交额'
        
        # 新增指标列
        early_price_col = '09:55收盘价'  # 早盘半小时
        mkt_cap_col = '总市值'
        profit_col = '净利润TTM'
        
        # 查找换手率和流通市值列
        turnover_col = None
        for c in cols:
            if '换手' in c:
                turnover_col = c
                break
        
        circ_mv_col = None
        for c in cols:
            if '流通市值' in c.replace(' ', ''):
                circ_mv_col = c
                break

        # 资金流相关列
        money_cols = [
            '机构资金买入额', '机构资金卖出额',
            '大户资金买入额', '大户资金卖出额',
            '中户资金买入额', '中户资金卖出额',
            '散户资金买入额', '散户资金卖出额'
        ]
        
        # 检查必需列是否存在
        required_cols = [date_col, code_col, close_col, pre_close_col, amount_col]
        missing_cols = [c for c in required_cols if c not in cols]
        if missing_cols:
            return None
        
        # 寻找行业列
        industry_col = None
        candidates = ['新版申万一级行业名称', '所属行业', '行业', '一级行业', '申万一级行业']
        for cand in candidates:
            # 这里的列名可能有空格
            matched = [c for c in cols if cand in c.strip()]
            if matched:
                industry_col = matched[0]
                break
        
        use_cols = [date_col, code_col, name_col, close_col, pre_close_col, amount_col]
        if open_col in cols:
            use_cols.append(open_col)
        if industry_col:
            use_cols.append(industry_col)
            
        # 添加存在的资金流列
        existing_money_cols = [c for c in money_cols if c in cols]
        use_cols.extend(existing_money_cols)
        
        # 添加新增指标列
        if early_price_col in cols:
            use_cols.append(early_price_col)
        if mkt_cap_col in cols:
            use_cols.append(mkt_cap_col)
        if profit_col in cols:
            use_cols.append(profit_col)
        if turnover_col:
            use_cols.append(turnover_col)
        if circ_mv_col:
            use_cols.append(circ_mv_col)
            
        # 筛选列
        # df 已经在开头读取了，这里只需要过滤列
        df = df[use_cols].copy()
        
        if df.empty:
            return None
            
        # 转换日期 (优化速度：指定format)
        # 有的股票数据可能格式不完全一致，这里去掉强校验 format='%Y-%m-%d'，改用默认智能解析，以防NaT过滤掉大量数据
        df[date_col] = pd.to_datetime(df[date_col], errors='coerce')
        
        # 不要在这里过早 dropna，特别是 date_col
        # 有时最后一行可能有问题，我们先过滤有效日期
        df = df.dropna(subset=[date_col])
        
        # 很多停牌股票成交额为0或缺失，不要直接过滤掉，这会影响大盘成交额的完整性（虽然为0不影响sum，但其他逻辑可能用到）
        
        # 排序并取最后 N 天
        df = df.sort_values(date_col).tail(DAYS_TO_KEEP)
        
        # 不要过早 dropna，因为 MA 等指标会产生 NaN，特别是我们用了 min_periods=1
        # 但如果是其它导致整行缺失的问题，可以保留，反正最后会取最新的一天

        
        # 统一行业列名
        if industry_col:
            df['行业'] = df[industry_col]
        else:
            df['行业'] = '未知'
            
        # 统一换手率列名
        if turnover_col:
            df = df.rename(columns={turnover_col: '换手率'})
        elif circ_mv_col:
             # 如果没有换手率但有流通市值，计算换手率
             # 成交额 / 流通市值 * 100
             # 注意成交额单位是元，流通市值单位通常也是元
             df = df.rename(columns={circ_mv_col: '流通市值'})
             df['换手率'] = df[amount_col] / df['流通市值'] * 100
             
        # ==========================
        # 计算技术指标
        # ==========================
        # 1. 均线与乖离率 (Trend & BIAS)
        # MA10 (用于风格研判)
        df['MA10'] = df[close_col].rolling(window=10, min_periods=1).mean()
        # MA20
        df['MA20'] = df[close_col].rolling(window=20, min_periods=1).mean()
        # BIAS20: (收盘 - MA20) / MA20
        df['BIAS20'] = (df[close_col] - df['MA20']) / df['MA20'] * 100
        
        # MA60 (中期趋势)
        df['MA60'] = df[close_col].rolling(window=60, min_periods=1).mean()
        # 60日涨幅 (Trend Strength): (收盘 - 60日前收盘) / 60日前收盘
        # 这里用简单的方法：当前收盘 / shift(60) - 1
        # 注意：如果数据不足60天，这里会是NaN
        df['Trend60'] = (df[close_col] / df[close_col].shift(59) - 1) * 100
        
        # 2. 资金异动 (Volume Anomaly)
        # 5日均量 (成交额)
        df['Amount_MA5'] = df[amount_col].rolling(window=5, min_periods=1).mean()
        # 量比 (今日成交额 / 5日均量)
        # 避免除以0或极小值
        df['Amount_MA5'] = df['Amount_MA5'].replace(0, np.nan)
        df['Vol_Ratio'] = df[amount_col] / df['Amount_MA5']
            
        return df

    except Exception as e:
        # print(f"Error processing {file_path}: {e}")
        return None


# ==========================
# 新增分析函数
# ==========================

def analyze_regime_style(latest_df, add_md, df_to_md):
    """分析市场风格状态 (搅屎棍风格判别)"""
    if 'MA10' not in latest_df.columns or '收盘价' not in latest_df.columns:
        add_md("缺少 MA10 或 收盘价 数据，无法计算风格")
        return
    
    # 行业定义
    cyclical_sectors = ['银行', '钢铁', '有色金属', '煤炭', '石油石化', '基础化工', '机械设备', '建筑材料']
    value_sectors = ['公用事业', '交通运输', '银行', '非银金融', '食品饮料', '家用电器', '建筑装饰', '纺织服饰']
    
    latest_df['大于MA10'] = latest_df['收盘价'] > latest_df['MA10']
    
    sector_strength = latest_df.groupby('行业').agg(
        总数=('股票代码', 'count'),
        强势数=('大于MA10', 'sum')
    )
    sector_strength['强势占比'] = sector_strength['强势数'] / sector_strength['总数'] * 100
    
    # 判定强势行业 (> 50% 视为强势)
    strong_sectors = sector_strength[sector_strength['强势占比'] > 50].index.tolist()
    
    cyclical_strong_count = sum(1 for s in cyclical_sectors if s in strong_sectors)
    value_strong_count = sum(1 for s in value_sectors if s in strong_sectors)
    
    regime = "成长模式 (Growth)"
    if cyclical_strong_count >= 5:
        regime = "周期模式 (Cyclical)"
    elif value_strong_count >= 4:
        regime = "价值模式 (Value)"
        
    add_md(f"\n**当前市场主导风格**: 【{regime}】")
    add_md(f"  - 强势周期行业数: {cyclical_strong_count} / 8 (阈值: 5) -> [银行/钢铁/有色/煤炭/石化/化工/机械/建材]")
    add_md(f"  - 强势价值行业数: {value_strong_count} / 8 (阈值: 4) -> [公用/交运/银行/非银/食品/家电/建筑/纺织]")
    
    if regime == "周期模式 (Cyclical)":
        add_md("  - **策略建议**: 适配【周期轮动、红利策略】，规避【小盘成长】。")
    elif regime == "价值模式 (Value)":
        add_md("  - **策略建议**: 适配【价值投资、现金流策略、大盘蓝筹】。")
    else:
        add_md("  - **策略建议**: 适配【小市值、动量策略、题材炒作】。")
    
    add_md("\n**各风格代表行业强势度 (收盘价>MA10的个股占比)**")
    display_data = []
    for s in set(cyclical_sectors + value_sectors):
        if s in sector_strength.index:
            strength = sector_strength.loc[s, '强势占比']
            tags = []
            if s in cyclical_sectors: tags.append('周期')
            if s in value_sectors: tags.append('价值')
            display_data.append({'行业': s, '属性': ','.join(tags), '强势占比': strength})
    
    if display_data:
        style_df = pd.DataFrame(display_data).sort_values('强势占比', ascending=False)
        add_md(df_to_md(style_df, {'行业': '行业', '属性': '风格属性', '强势占比': '强势占比'}))

def analyze_huddle_ratio(full_df, add_md, df_to_md):
    """分析抱团度 (结构化 Alpha)"""
    if '成交额(亿)' not in full_df.columns:
        add_md("缺少 成交额(亿) 数据，无法计算抱团度")
        return
        
    daily_huddle = []
    for date, group in full_df.groupby('交易日期'):
        total_amt = group['成交额(亿)'].sum()
        if total_amt == 0: continue
        top5_count = max(1, int(len(group) * 0.05))
        top5_amt = group.nlargest(top5_count, '成交额(亿)')['成交额(亿)'].sum()
        daily_huddle.append({'交易日期': date, '抱团占比': (top5_amt / total_amt) * 100})
        
    if not daily_huddle:
        return
        
    huddle_df = pd.DataFrame(daily_huddle).sort_values('交易日期')
    huddle_df['MA20'] = huddle_df['抱团占比'].rolling(20).mean()
    
    latest = huddle_df.iloc[-1]
    add_md(f"\n**当前市场抱团度 (Top 5% 成交额占比)**: {latest['抱团占比']:.2f}%")
    if pd.notna(latest['MA20']):
        add_md(f"**20日均值**: {latest['MA20']:.2f}%")
        if latest['抱团占比'] < latest['MA20']:
            add_md("  - **结论**: 抱团度低于20日均线，资金下沉，市场倾向于【小盘/分散模式】")
        else:
            add_md("  - **结论**: 抱团度高于20日均线，资金集中，市场倾向于【大盘/抱团模式】")
    
    recent_5 = huddle_df.tail(5).copy()
    recent_5['交易日期'] = recent_5['交易日期'].dt.strftime('%Y-%m-%d')
    add_md("\n**近5日抱团度趋势**")
    add_md(df_to_md(recent_5, {'交易日期': '日期', '抱团占比': '抱团占比', 'MA20': '20日均值(占比)'}))

def _get_limit_pct(code):
    """根据股票代码返回涨停/跌停阈值"""
    c = str(code).lower()
    if c.startswith(('bj', '92')):
        return 29.5, -29.5  # 北交所 30% 涨跌幅
    if c.startswith(('sh688', 'sz300', 'sz301')):
        return 19.5, -19.5  # 科创板/创业板 20% 涨跌幅
    return 9.5, -9.5  # 主板 10% 涨跌幅


def analyze_limit_up_down(latest_df, full_df, add_md, df_to_md):
    """分析涨跌停板情况"""
    if '涨跌幅' not in latest_df.columns:
        add_md("缺少涨跌幅数据，无法分析涨跌停")
        return

    # 按各板块实际涨停阈值分别筛选（科创板20%，主板10%，北交所30%）
    code_col = '股票代码' if '股票代码' in latest_df.columns else '代码'
    limits = latest_df[code_col].apply(_get_limit_pct)
    limit_up = latest_df[latest_df['涨跌幅'] >= limits.apply(lambda x: x[0])]
    limit_down = latest_df[latest_df['涨跌幅'] <= limits.apply(lambda x: x[1])]

    add_md(f"**涨停板**: {len(limit_up)} 只 | **跌停板**: {len(limit_down)} 只")

    if not limit_up.empty:
        add_md("\n**涨停板行业分布 Top 10**")
        up_sectors = limit_up['行业'].value_counts().head(10).reset_index()
        up_sectors.columns = ['行业', '涨停数量']
        add_md(df_to_md(up_sectors, {'行业': '行业', '涨停数量': '涨停数量'}))

    if not limit_down.empty:
        add_md("\n**跌停板行业分布 Top 10**")
        down_sectors = limit_down['行业'].value_counts().head(10).reset_index()
        down_sectors.columns = ['行业', '跌停数量']
        add_md(df_to_md(down_sectors, {'行业': '行业', '跌停数量': '跌停数量'}))

    dates = sorted(full_df['交易日期'].unique())
    if len(dates) >= 5:
        recent_5d = full_df[full_df['交易日期'].isin(dates[-5:])]
        # 近5日涨停 — 使用板块特定阈值
        code_col_5d = '股票代码' if '股票代码' in recent_5d.columns else '代码'
        limits_5d = recent_5d[code_col_5d].apply(_get_limit_pct)
        limit_up_5d = recent_5d[recent_5d['涨跌幅'] >= limits_5d.apply(lambda x: x[0])]
        limit_up_count = limit_up_5d.groupby('交易日期')['涨跌幅'].count()
        add_md("\n**近5日涨停板数量**")
        for d, cnt in limit_up_count.items():
            add_md(f"  {d.strftime('%Y-%m-%d')}: {cnt} 只")


def analyze_market_cap(latest_df, add_md, df_to_md):
    """分析市值分布：大盘/中盘/小盘"""
    cap_col = '流通市值' if '流通市值' in latest_df.columns else ('总市值' if '总市值' in latest_df.columns else None)
    if cap_col is None:
        add_md("缺少市值数据，无法分析")
        return

    latest_df['市值(亿)'] = latest_df[cap_col] / 1e8

    conditions = [
        latest_df['市值(亿)'] < 50,
        (latest_df['市值(亿)'] >= 50) & (latest_df['市值(亿)'] < 200),
        latest_df['市值(亿)'] >= 200
    ]
    labels = ['小盘(<50亿)', '中盘(50-200亿)', '大盘(>200亿)']
    latest_df['市值分类'] = np.select(conditions, labels, default='未知')

    cap_stats = latest_df.groupby('市值分类').agg({
        '涨跌幅': ['mean', 'count', lambda x: (x > 0).sum()],
        '市值(亿)': 'sum',
        '成交额(亿)': 'sum'
    })
    cap_stats.columns = ['平均涨幅', '家数', '上涨家数', '总市值(亿)', '成交额(亿)']
    cap_stats['上涨占比'] = cap_stats['上涨家数'] / cap_stats['家数']

    order = ['大盘(>200亿)', '中盘(50-200亿)', '小盘(<50亿)']
    cap_stats = cap_stats.reindex([o for o in order if o in cap_stats.index])

    add_md(f"\n**市值分布表现**")
    add_md(df_to_md(cap_stats, {'平均涨幅': '平均涨幅', '家数': '家数', '上涨占比': '上涨占比', '成交额(亿)': '成交额(亿)'}))

    add_md("\n**各市值分类领涨股**")
    for cap_type in order:
        if cap_type in latest_df['市值分类'].values:
            subset = latest_df[latest_df['市值分类'] == cap_type].sort_values('涨跌幅', ascending=False).head(3)
            names = ", ".join([f"{row['股票名称']}({row['涨跌幅']:.1f}%)" for _, row in subset.iterrows()])
            add_md(f"  {cap_type}: {names}")


def analyze_price_level(latest_df, add_md, df_to_md):
    """分析价格区间分布"""
    if '收盘价' not in latest_df.columns:
        add_md("缺少收盘价数据，无法分析")
        return

    conditions = [
        latest_df['收盘价'] < 10,
        (latest_df['收盘价'] >= 10) & (latest_df['收盘价'] < 50),
        (latest_df['收盘价'] >= 50) & (latest_df['收盘价'] < 100),
        latest_df['收盘价'] >= 100
    ]
    labels = ['低价(<10元)', '中低价(10-50)', '中高价(50-100)', '高价(>100元)']
    latest_df['价格分类'] = np.select(conditions, labels, default='未知')

    price_stats = latest_df.groupby('价格分类').agg({
        '涨跌幅': ['mean', 'count', lambda x: (x > 0).sum()],
        '成交额(亿)': 'sum'
    })
    price_stats.columns = ['平均涨幅', '家数', '上涨家数', '成交额(亿)']
    price_stats['上涨占比'] = price_stats['上涨家数'] / price_stats['家数']

    order = ['低价(<10元)', '中低价(10-50)', '中高价(50-100)', '高价(>100元)']
    price_stats = price_stats.reindex([o for o in order if o in price_stats.index])

    add_md(f"\n**价格区间表现**")
    add_md(df_to_md(price_stats, {'平均涨幅': '平均涨幅', '家数': '家数', '上涨占比': '上涨占比', '成交额(亿)': '成交额(亿)'}))


def analyze_turnover(latest_df, add_md, df_to_md):
    """分析换手率分布"""
    if '换手率' not in latest_df.columns:
        add_md("缺少换手率数据，无法分析")
        return

    conditions = [
        latest_df['换手率'] < 1,
        (latest_df['换手率'] >= 1) & (latest_df['换手率'] < 3),
        (latest_df['换手率'] >= 3) & (latest_df['换手率'] < 5),
        (latest_df['换手率'] >= 5) & (latest_df['换手率'] < 10),
        latest_df['换手率'] >= 10
    ]
    labels = ['低迷(<1%)', '一般(1-3%)', '较活(3-5%)', '活跃(5-10%)', '高度活跃(>10%)']
    latest_df['换手分类'] = np.select(conditions, labels, default='未知')

    turnover_stats = latest_df.groupby('换手分类').agg({
        '涨跌幅': ['mean', 'count'],
        '成交额(亿)': 'sum'
    })
    turnover_stats.columns = ['平均涨幅', '家数', '成交额(亿)']

    order = ['低迷(<1%)', '一般(1-3%)', '较活(3-5%)', '活跃(5-10%)', '高度活跃(>10%)']
    turnover_stats = turnover_stats.reindex([o for o in order if o in turnover_stats.index])

    add_md(f"\n**换手率分布表现**")
    add_md(df_to_md(turnover_stats, {'平均涨幅': '平均涨幅', '家数': '家数', '成交额(亿)': '成交额(亿)'}))

    add_md("\n**最高换手率 Top 10**")
    top_turnover = latest_df.nlargest(10, '换手率')[['股票代码', '股票名称', '涨跌幅', '换手率', '成交额(亿)']]
    top_turnover = top_turnover.rename(columns={'股票代码': '代码', '股票名称': '名称', '涨跌幅': '涨幅', '换手率': '换手率', '成交额(亿)': '成交额'})
    add_md(df_to_md(top_turnover, {c: c for c in top_turnover.columns}))


def analyze_money_flow_behavior(full_df, add_md, df_to_md):
    """资金行为综合分析"""
    required_cols = ['机构资金买入额', '大户资金买入额', '散户资金买入额']
    if not all(c in full_df.columns for c in required_cols):
        add_md("缺少资金流数据，无法分析")
        return

    dates = sorted(full_df['交易日期'].unique())
    if len(dates) < 5:
        add_md("数据天数不足5天，无法分析资金趋势")
        return

    recent_5d = full_df[full_df['交易日期'].isin(dates[-5:])]

    daily_flow = recent_5d.groupby('交易日期').agg({
        '机构资金买入额': 'sum', '机构资金卖出额': 'sum',
        '大户资金买入额': 'sum', '大户资金卖出额': 'sum',
        '散户资金买入额': 'sum', '散户资金卖出额': 'sum'
    })

    daily_flow['主力净流入'] = (daily_flow['机构资金买入额'] + daily_flow['大户资金买入额'] -
                                daily_flow['机构资金卖出额'] - daily_flow['大户资金卖出额']) / 10000
    daily_flow['散户净流入'] = (daily_flow['散户资金买入额'] - daily_flow['散户资金卖出额']) / 10000

    add_md("\n**近5日资金流向 (亿元)**")
    flow_display = daily_flow[['主力净流入', '散户净流入']].copy()
    flow_display.index = flow_display.index.strftime('%Y-%m-%d')
    flow_display = flow_display.reset_index().rename(columns={'交易日期': '日期'})
    add_md(df_to_md(flow_display, {'日期': '日期', '主力净流入': '主力', '散户净流入': '散户'}))

    latest_date = dates[-1]
    latest_day = full_df[full_df['交易日期'] == latest_date].copy()
    latest_day['主力净流入'] = (latest_day['机构资金买入额'] + latest_day['大户资金买入额'] -
                               latest_day['机构资金卖出额'] - latest_day['大户资金卖出额']) / 10000

    if len(dates) >= 3:
        add_md("\n**连续3日主力净流入个股 Top 20**")
        recent_3d = full_df[full_df['交易日期'].isin(dates[-3:])]
        stock_flow = recent_3d.groupby('股票代码').agg({
            '主力净流入': 'sum', '股票名称': 'first', '行业': 'first', '涨跌幅': 'last'
        }).sort_values('主力净流入', ascending=False).head(20)
        stock_flow = stock_flow.reset_index()
        stock_flow.columns = ['代码', '主力净流入(3日合计)', '名称', '行业', '今日涨幅']
        add_md(df_to_md(stock_flow, {c: c for c in stock_flow.columns}))

    inflow_stocks = latest_day[latest_day['主力净流入'] > 0]
    outflow_stocks = latest_day[latest_day['主力净流入'] < 0]

    add_md(f"\n**主力资金行为统计**")
    add_md(f"  主力净流入股票: {len(inflow_stocks)} 只 ({len(inflow_stocks)/len(latest_day)*100:.1f}%)")
    add_md(f"  主力净流出股票: {len(outflow_stocks)} 只 ({len(outflow_stocks)/len(latest_day)*100:.1f}%)")
    if len(inflow_stocks) > 0:
        add_md(f"  主力净流入股票平均涨幅: {inflow_stocks['涨跌幅'].mean():.2f}%")
    if len(outflow_stocks) > 0:
        add_md(f"  主力净流出股票平均涨幅: {outflow_stocks['涨跌幅'].mean():.2f}%")


def analyze_industry_money_full(full_df, add_md, df_to_md):
    """
    全 31 行业资金画像（资金行为综合分析 - 全行业版）

    与原 analyze_money_flow_behavior 的区别：
    - 原：股票级（Top 20 净流入/流出股票）
    - 此：行业级（**全部 31 个行业**的资金画像）

    字段：
    - 5 日成交额（亿）
    - 主力净流入（亿，已扣流出）
    - 散户净流入（亿，已扣流出）
    - 净流入（亿 = 主力+散户）
    - 资金强度（% = 净流入/5日成交额，**跨行业可比**）
    """
    # [AI-MODIFIED] 周复盘系统 - 新增全 31 行业资金画像（替代 Top 8 限制）
    required_cols = [
        '机构资金买入额', '机构资金卖出额',
        '大户资金买入额', '大户资金卖出额',
        '散户资金买入额', '散户资金卖出额',
        '行业', '成交额(亿)',  # [AI-MODIFIED] 用 "成交额(亿)" 列（值已是亿单位）
    ]
    if not all(c in full_df.columns for c in required_cols):
        add_md("缺少行业资金数据，无法分析")
        return

    dates = sorted(full_df['交易日期'].unique())
    if len(dates) < 1:
        return

    # 取最近 5 天（资金趋势窗口）
    recent_5d = full_df[full_df['交易日期'].isin(dates[-5:])]

    # 行业级聚合
    industry_flow = recent_5d.groupby('行业', observed=True).agg({
        '机构资金买入额': 'sum',
        '机构资金卖出额': 'sum',
        '大户资金买入额': 'sum',
        '大户资金卖出额': 'sum',
        '散户资金买入额': 'sum',
        '散户资金卖出额': 'sum',
        '成交额(亿)': 'sum',
    }).reset_index()

    # 计算净流入（亿元）= (买入 - 卖出) / 10000
    industry_flow['主力净流入(亿)'] = (
        (industry_flow['机构资金买入额'] + industry_flow['大户资金买入额']
         - industry_flow['机构资金卖出额'] - industry_flow['大户资金卖出额']) / 10000
    )
    industry_flow['散户净流入(亿)'] = (
        (industry_flow['散户资金买入额'] - industry_flow['散户资金卖出额']) / 10000
    )
    industry_flow['5日成交额(亿)'] = industry_flow['成交额(亿)']  # [AI-MODIFIED] 已是亿单位，不再除 10000
    industry_flow['净流入(亿)'] = industry_flow['主力净流入(亿)'] + industry_flow['散户净流入(亿)']
    # 资金强度（%）= 净流入 / 5 日成交额 × 100
    industry_flow['资金强度(%)'] = industry_flow.apply(
        lambda r: r['净流入(亿)'] / r['5日成交额(亿)'] * 100 if r['5日成交额(亿)'] > 0 else 0,
        axis=1
    )
    industry_flow['主力强度(%)'] = industry_flow.apply(
        lambda r: r['主力净流入(亿)'] / r['5日成交额(亿)'] * 100 if r['5日成交额(亿)'] > 0 else 0,
        axis=1
    )

    # 排序：按资金强度降序
    industry_flow = industry_flow.sort_values('资金强度(%)', ascending=False).reset_index(drop=True)
    industry_flow.insert(0, '排名', range(1, len(industry_flow) + 1))

    add_md("\n**全 31 行业资金画像 (近 5 日累计，按资金强度排序)**")
    add_md("\n*字段说明*：")
    add_md("- `资金强度` = 净流入 / 5日成交额 × 100%（**跨行业可比，标准化指标**）")
    add_md("- `净流入 = 主力 + 散户`（已扣各自流出，**真净流入**）")
    add_md("- `主力净流入` = (机构买入+大户买入-机构卖出-大户卖出) / 10000（亿）")
    add_md("")
    add_md(df_to_md(industry_flow, {
        '排名': '排名',
        '行业': '行业',
        '5日成交额(亿)': '5日成交额(亿)',
        '主力净流入(亿)': '主力净流入(亿)',
        '散户净流入(亿)': '散户净流入(亿)',
        '净流入(亿)': '净流入(亿)',
        '资金强度(%)': '资金强度(%)',
        '主力强度(%)': '主力强度(%)',
    }))

    # 资金强度细化（4 位小数，因大部分板块资金强度 < 0.01%）
    add_md("\n**资金强度细化排名（4 位小数，识别真实强度差异）**")
    industry_sorted = industry_flow.sort_values('资金强度(%)', ascending=False)
    add_md(df_to_md(industry_sorted[['排名', '行业', '资金强度(%)', '主力强度(%)']], {
        '排名': '排名',
        '行业': '行业',
        '资金强度(%)': '资金强度(%)',
        '主力强度(%)': '主力强度(%)',
    }))


def analyze_sector_rotation(daily_sector_pct, add_md, df_to_md):
    """分析行业轮动趋势"""
    if daily_sector_pct.empty or len(daily_sector_pct) < 3:
        add_md("数据不足，无法分析行业轮动")
        return

    recent_3d = daily_sector_pct.tail(3)
    recent_5d = daily_sector_pct.tail(5)

    rank_3d = recent_3d.mean().sort_values(ascending=False)
    rank_5d = recent_5d.mean().sort_values(ascending=False)

    rank_change = pd.DataFrame({
        '5日排名': rank_5d.rank(ascending=False),
        '3日排名': rank_3d.rank(ascending=False)
    })
    rank_change['排名变化'] = rank_change['5日排名'] - rank_change['3日排名']

    rising_sectors = rank_change[rank_change['排名变化'] > 2].sort_values('排名变化', ascending=False).head(10)
    falling_sectors = rank_change[rank_change['排名变化'] < -2].sort_values('排名变化').head(10)

    add_md("\n**轮动加速上升行业 (排名上升>2位)**")
    if not rising_sectors.empty:
        rising_display = rising_sectors.reset_index()
        rising_display.columns = ['行业', '5日排名', '3日排名', '变化']
        add_md(df_to_md(rising_display, {c: c for c in rising_display.columns}))
    else:
        add_md("  无明显上升行业")

    add_md("\n**轮动加速下滑行业 (排名下滑>2位)**")
    if not falling_sectors.empty:
        falling_display = falling_sectors.reset_index()
        falling_display.columns = ['行业', '5日排名', '3日排名', '变化']
        add_md(df_to_md(falling_display, {c: c for c in falling_display.columns}))
    else:
        add_md("  无明显下滑行业")


def analyze_market_sentiment(latest_df, full_df, sector_stats, has_money_flow, add_md, df_to_md):
    """市场情绪综合评分"""
    sentiment = {}

    trading_df = latest_df[latest_df['成交额(亿)'] > 0]
    if len(trading_df) > 0:
        up_ratio = (trading_df['涨跌幅'] > 0).mean() * 100
    else:
        up_ratio = 0
    sentiment['上涨比例'] = min(up_ratio * 2, 100)

    if '涨跌幅' in latest_df.columns:
        code_col_s = '股票代码' if '股票代码' in latest_df.columns else '代码'
        lim_vals = latest_df[code_col_s].apply(_get_limit_pct)
        limit_up_count = len(latest_df[latest_df['涨跌幅'] >= lim_vals.apply(lambda x: x[0])])
        sentiment['涨停数量'] = min(limit_up_count * 5, 100)

    dates = sorted(full_df['交易日期'].unique())
    if len(dates) >= 20:
        recent_20d = full_df[full_df['交易日期'].isin(dates[-20:])]
        avg_amount_20d = recent_20d.groupby('交易日期')['成交额(亿)'].sum().mean()
        latest_amount = latest_df['成交额(亿)'].sum()
        amount_ratio = latest_amount / avg_amount_20d if avg_amount_20d > 0 else 1
        sentiment['成交活跃度'] = min(amount_ratio * 50, 100)

    if has_money_flow and '主力净流入(亿)' in latest_df.columns:
        total_main = latest_df['主力净流入(亿)'].sum()
        if total_main > 0:
            sentiment['主力态度'] = min(total_main / 50 * 100, 100)
        else:
            sentiment['主力态度'] = max(total_main / 30 * 100, 0)

    if '上涨占比' in sector_stats.columns:
        sector_up_ratio = (sector_stats['上涨占比'] > 0.5).mean() * 100
        sentiment['行业广度'] = sector_up_ratio

    if 'PE中位数' in sector_stats.columns:
        median_pe = sector_stats['PE中位数'].median()
        if 15 <= median_pe <= 40:
            sentiment['估值合理度'] = 100
        elif median_pe < 15:
            sentiment['估值合理度'] = max(100 - (15 - median_pe) * 5, 0)
        else:
            sentiment['估值合理度'] = max(100 - (median_pe - 40) * 2, 0)

    if sentiment:
        total_score = sum(sentiment.values()) / len(sentiment)
        sentiment['综合评分'] = total_score

    add_md("\n**情绪指标得分 (0-100分)**")
    for k, v in sentiment.items():
        if k != '综合评分':
            bar = "█" * int(v / 10) + "░" * (10 - int(v / 10))
            add_md(f"  {k}: {v:.1f}分 {bar}")

    if '综合评分' in sentiment:
        score = sentiment['综合评分']
        if score >= 80:
            level = "🔥 极度亢奋"
        elif score >= 65:
            level = "😊 乐观"
        elif score >= 45:
            level = "😐 中性"
        elif score >= 30:
            level = "😟 谨慎"
        else:
            level = "😱 恐慌"

        add_md(f"\n**综合情绪评分: {score:.1f}分 ({level})**")

        add_md("\n**操作建议**")
        if score >= 70:
            add_md("  - 市场情绪亢奋，注意短期回调风险")
            add_md("  - 可适当减仓涨幅较大的热门股")
        elif score >= 50:
            add_md("  - 市场情绪中性偏多")
            add_md("  - 建议持有为主，逢低吸纳")
        elif score >= 30:
            add_md("  - 市场情绪谨慎")
            add_md("  - 控制仓位，关注超跌反弹机会")
        else:
            add_md("  - 市场情绪低迷/恐慌")
            add_md("  - 等待企稳，可开始分批建仓")


def main(target_date: str = None):
    # [AI-MODIFIED] 支持 --date 参数指定分析日期，未指定时取最新
    if target_date:
        global ANALYSIS_DATE
        ANALYSIS_DATE = pd.to_datetime(target_date)
        print(f"\n[CLI] 使用指定日期: {ANALYSIS_DATE.strftime('%Y-%m-%d')}")
    else:
        ANALYSIS_DATE = None
        print(f"\n[CLI] 未指定日期，使用最新交易日")
    print(f"正在扫描数据目录: {DATA_DIR}")
    files = list(Path(DATA_DIR).glob('*.csv'))
    print(f"找到 {len(files)} 个股票文件")

    if not files:
        print("未找到数据文件，请检查路径。")
        return

    all_data = []

    # 多进程读取
    print("正在并行读取数据...")

    # 根据CPU核心数设置进程数，保留一些余量
    max_workers = max(1, (os.cpu_count() or 4) - 2)
    # 限制最大进程数，防止内存占用过高 (每个进程约占用 100-200MB)
    # 假设32GB内存，开16-20个进程比较安全。如果只有16GB，建议限制在8-10个。
    # 这里保守限制为 12
    if max_workers > 12:
        max_workers = 12

    print(f"启动进程池: {max_workers} workers")

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        results = list(tqdm(executor.map(process_stock_file, files), total=len(files)))

    # 过滤空结果
    all_data = [df for df in results if df is not None]

    if not all_data:
        print("未成功读取任何数据。")
        return

    print("正在合并数据...")
    full_df = pd.concat(all_data, ignore_index=True)

    # [AI-MODIFIED] 如果指定了 --date，过滤掉指定日期之后的数据，使所有下游"取最新"的逻辑服从 ANALYSIS_DATE
    if ANALYSIS_DATE is not None:
        before_count = len(full_df)
        full_df = full_df[full_df['交易日期'] <= ANALYSIS_DATE].copy()
        after_count = len(full_df)
        print(f"[CLI] 日期过滤完成: 保留 {ANALYSIS_DATE.strftime('%Y-%m-%d')} 及之前的数据，"
              f"行数 {before_count} → {after_count}")
        if after_count == 0:
            print(f"错误: 指定日期 {ANALYSIS_DATE.strftime('%Y-%m-%d')} 及之前无数据，请检查 --date 参数。")
            return
    
    # 转换成交额单位为亿 (原始单位: 元)
    # 根据 stock-trading-data-pro-columns.csv: 成交额单位是元
    full_df['成交额(亿)'] = full_df['成交额'] / 1e8
    
    # 填充成交额 NaN 为 0
    full_df['成交额(亿)'] = full_df['成交额(亿)'].fillna(0)
    
    # 计算主力净流入 (机构+大户)
    # 根据 stock-trading-data-pro-columns.csv: 
    # 机构/大户/中户/散户资金买入/卖出额 单位均为: 万元
    # 所以需要先 * 10000 转换为元，再 / 1e8 转换为亿，或者直接 / 10000
    
    # 先填充 NaN
    money_cols = [
        '机构资金买入额', '机构资金卖出额',
        '大户资金买入额', '大户资金卖出额',
        '散户资金买入额', '散户资金卖出额'
    ]
    for col in money_cols:
        if col in full_df.columns:
            full_df[col] = full_df[col].fillna(0)
    
    # 如果有主力资金数据，计算主力净流入
    has_money_flow = False
    if set(['机构资金买入额', '大户资金买入额']).issubset(full_df.columns):
        # 原始单位是万元，计算净流入(万元)
        net_inflow_wan = (full_df['机构资金买入额'] + full_df['大户资金买入额']) - \
                         (full_df['机构资金卖出额'] + full_df['大户资金卖出额'])
        
        # 转换为亿: 万元 / 10000 = 亿
        full_df['主力净流入(亿)'] = net_inflow_wan / 10000
        # 保存原始单位(万元)方便后续可能使用，或者直接用亿
        full_df['主力净流入'] = full_df['主力净流入(亿)'] 
        has_money_flow = True
        
        # 计算散户净流入
        if set(['散户资金买入额', '散户资金卖出额']).issubset(full_df.columns):
             retail_inflow_wan = full_df['散户资金买入额'] - full_df['散户资金卖出额']
             full_df['散户净流入(亿)'] = retail_inflow_wan / 10000
    else:
        print("提示: 缺少主力资金流数据列")
    
    # 计算涨跌幅
    full_df['涨跌幅'] = (full_df['收盘价'] - full_df['前收盘价']) / full_df['前收盘价'] * 100
    
    # 计算早盘涨跌幅 (09:55)
    if '09:55收盘价' in full_df.columns:
        # 有些数据可能缺失，填充
        # 如果没有开盘价，用前收盘价
        if '开盘价' in full_df.columns:
             full_df['09:55收盘价'] = full_df['09:55收盘价'].fillna(full_df['开盘价']) 
        else:
             full_df['09:55收盘价'] = full_df['09:55收盘价'].fillna(full_df['前收盘价'])
             
        full_df['早盘涨幅'] = (full_df['09:55收盘价'] - full_df['前收盘价']) / full_df['前收盘价'] * 100
    else:
        full_df['早盘涨幅'] = np.nan
        
    # 计算估值 (PE TTM)
    if '总市值' in full_df.columns and '净利润TTM' in full_df.columns:
        # 避免除以0或负数导致PE失真，通常只统计正PE
        full_df['PE'] = full_df['总市值'] / full_df['净利润TTM']
        # 将负值或异常大值设为 NaN 以免影响中位数
        full_df.loc[full_df['PE'] < 0, 'PE'] = np.nan
        full_df.loc[full_df['PE'] > 500, 'PE'] = np.nan
    else:
        full_df['PE'] = np.nan

    # 恢复北交所股票统计（之前过滤了 bj 开头）
    # 注：北交所和科创板如果有，也会被统计在全市场成交额中
    
    # 之前代码中有这段过滤，我们先保留过滤的提示，但不把它从成交额统计里彻底删除
    # 但由于之前的过滤是在前面，我们把过滤逻辑稍微调整一下，把大盘数据保存一份。
    
    # 获取最新日期
    latest_date = full_df['交易日期'].max()
    print(f"\n分析基准日期: {latest_date.strftime('%Y-%m-%d')}")
    print(f"包含交易日数量: {full_df['交易日期'].nunique()}")
    
    # ==========================
    # 1. 最新截面分析 (T-0)
    # ==========================
    latest_df_raw = full_df[full_df['交易日期'] == latest_date].copy()
    
    # 计算未过滤前的全市场总成交额，排除明显异常的数据（比如成交额单位错乱导致超大数值）
    # 正常A股单日单票成交额很难超过2000亿
    total_market_amount_raw = latest_df_raw[latest_df_raw['成交额(亿)'] < 3000]['成交额(亿)'].sum()
    
    latest_df = latest_df_raw.copy()
    # 过滤北交所股票 (代码以 bj 开头)
    if '股票代码' in latest_df.columns:
        original_count = len(latest_df)
        latest_df = latest_df[~latest_df['股票代码'].astype(str).str.lower().str.startswith('bj')]
        filtered_count = len(latest_df)
        print(f"已过滤北交所股票: {original_count - filtered_count} 只")
    
    agg_dict = {
        '涨跌幅': ['mean', 'count', lambda x: (x > 0).sum()],
        '成交额(亿)': 'sum',
        '早盘涨幅': 'mean',
        'PE': 'median', # 行业估值看中位数
        'BIAS20': 'mean', # 行业平均偏离度
        'Vol_Ratio': 'mean', # 行业平均量比 (热度异动)
        'Trend60': 'median' # 行业中期趋势 (中位数更稳健)
    }
    if has_money_flow:
        agg_dict['主力净流入(亿)'] = 'sum'
        if '散户净流入(亿)' in latest_df.columns:
            agg_dict['散户净流入(亿)'] = 'sum'
            
    # 填充缺失行业，避免 groupby 忽略 NaN 导致数据丢失
    latest_df['行业'] = latest_df['行业'].fillna('未知')
        
    # 按行业聚合
    sector_stats = latest_df.groupby('行业').agg(agg_dict)
    
    # 重命名列
    # 注意 agg 后列是多级索引
    # 这种重命名方式需要按顺序，容易错，改为更稳健的方式
    
    # 扁平化列名
    sector_stats.columns = ['_'.join(col).strip() for col in sector_stats.columns.values]
    
    # 映射常用名
    rename_map = {
        '涨跌幅_mean': '平均涨幅',
        '涨跌幅_count': '总家数',
        '涨跌幅_<lambda_0>': '上涨家数',
        '成交额(亿)_sum': '总成交额',
        '早盘涨幅_mean': '早盘平均涨幅',
        'PE_median': 'PE中位数',
        'BIAS20_mean': '平均偏离度',
        'Vol_Ratio_mean': '平均量比',
        'Trend60_median': '60日趋势',
        '主力净流入(亿)_sum': '主力净流入',
        '散户净流入(亿)_sum': '散户净流入'
    }
    sector_stats = sector_stats.rename(columns=rename_map)
    
    sector_stats['上涨占比'] = sector_stats['上涨家数'] / sector_stats['总家数']
    
    # 计算全市场成交额占比 (直接使用全市场总和，避免浮点数或缺失导致误差)
    total_amount = total_market_amount_raw
    sector_stats['成交额占比'] = sector_stats['总成交额'] / total_amount
    
    # 寻找领涨股
    leaders = []
    for sector in sector_stats.index:
        sector_df = latest_df[latest_df['行业'] == sector]
        if not sector_df.empty:
            # 取前3名
            top3 = sector_df.sort_values('涨跌幅', ascending=False).head(3)
            strs = [f"{row['股票名称']}({row['涨跌幅']:.1f}%)" for _, row in top3.iterrows()]
            leaders.append(" ".join(strs))
        else:
            leaders.append("-")
    sector_stats['领涨股'] = leaders
    
    # 排序
    top_gainers = sector_stats.sort_values('平均涨幅', ascending=False)
    if has_money_flow:
        top_inflow = sector_stats.sort_values('主力净流入', ascending=False)
    top_money = sector_stats.sort_values('总成交额', ascending=False)
    # 新增排序：异动榜（量比）和 趋势榜（偏离度）
    top_vol = sector_stats.sort_values('平均量比', ascending=False)
    top_bias = sector_stats.sort_values('平均偏离度', ascending=False)
    
    # ==========================
    # 2. 趋势分析 (Trend)
    # ==========================
    # 计算近 3 日、5 日行业平均涨幅
    # 这里需要先按日期、行业聚合，再计算滚动
    
    # 按日期、行业聚合平均涨幅
    daily_sector_pct = full_df.groupby(['交易日期', '行业'])['涨跌幅'].mean().unstack()
    
    # 计算近3日、5日累计涨幅 (近似相加)
    cum_3d = daily_sector_pct.tail(3).sum()
    cum_5d = daily_sector_pct.tail(5).sum()
    
    # 合并到 sector_stats
    sector_stats['3日涨幅'] = cum_3d
    sector_stats['5日涨幅'] = cum_5d
    
    # 重新排序，因为添加了新列
    top_gainers = sector_stats.sort_values('平均涨幅', ascending=False)
    if has_money_flow:
        top_inflow = sector_stats.sort_values('主力净流入', ascending=False)
    top_money = sector_stats.sort_values('总成交额', ascending=False)
    top_vol = sector_stats.sort_values('平均量比', ascending=False)
    top_bias = sector_stats.sort_values('平均偏离度', ascending=False)
    
    # ==========================
    # 3. 输出报告 (Markdown & Console)
    # ==========================
    # 强制刷新缓冲区
    sys.stdout.flush()
    # 设置输出编码为 utf-8
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    
    md_lines = []
    
    def add_md(text):
        md_lines.append(text)
        print(text)
        
    def add_md_header(text, level=2):
        md_lines.append(f"\n{'#' * level} {text}")
        print(f"\n{'='*40}\n{text}\n{'='*40}")

    def df_to_md(df, cols_map):
        """简单将DataFrame转换为Markdown表格"""
        if df.empty:
            return "无数据"
            
        # 筛选和重命名列
        temp_df = df[list(cols_map.keys())].rename(columns=cols_map).copy()
        
        # 构建表头
        headers = ['板块名称'] + list(temp_df.columns)
        header_line = "| " + " | ".join(headers) + " |"
        sep_line = "| " + " | ".join(["---"] * len(headers)) + " |"
        
        rows = []
        for idx, row in temp_df.iterrows():
            vals = [str(idx)]
            for col in temp_df.columns:
                val = row[col]
                # 简单格式化
                if isinstance(val, float):
                    if '强度' in col or '涨跌幅' in col:
                        # 资金强度 / 涨跌幅：4 位小数（数值通常 < 0.01%）
                        vals.append(f"{val:.4f}%")
                    elif '涨幅' in col or '占比' in col or '偏离' in col or '趋势' in col or '强弱' in col:
                        vals.append(f"{val:.2f}%")
                    elif 'PE' in col or '量比' in col:
                         vals.append(f"{val:.2f}")
                    else:
                        vals.append(f"{val:.2f}")
                else:
                    vals.append(str(val))
            rows.append("| " + " | ".join(vals) + " |")
            
        return "\n".join([header_line, sep_line] + rows)

    # 准备数据：计算额外显示字段
    # 1. 领涨板块处理
    top_gainers_disp = top_gainers.head(8).copy()
    top_gainers_disp['早盘强弱'] = top_gainers_disp['平均涨幅'] - top_gainers_disp['早盘平均涨幅']
    
    # 2. 资金板块处理
    top_money_disp = top_money.head(8).copy()
    
    # 3. 主力净流入处理
    if has_money_flow:
        top_inflow_disp = top_inflow.head(8).copy()
        # 领跌（流出前5）
        top_outflow_disp = top_inflow.tail(5).sort_values('主力净流入').copy()
        # 为流出榜找领跌股
        outflow_leaders = []
        for idx in top_outflow_disp.index:
            s_df = latest_df[latest_df['行业'] == idx]
            if not s_df.empty:
                # 取倒数3名
                tail3 = s_df.sort_values('涨跌幅', ascending=True).head(3)
                strs = [f"{row['股票名称']}({row['涨跌幅']:.1f}%)" for _, row in tail3.iterrows()]
                outflow_leaders.append(" ".join(strs))
            else:
                outflow_leaders.append("-")
        top_outflow_disp['领跌股'] = outflow_leaders
        
    # 4. 偏离度处理
    top_bias_disp = top_bias.head(5).copy()
    bottom_bias_disp = top_bias.tail(5).sort_values('平均偏离度').copy()
    
    # 5. 量比处理
    top_vol_disp = top_vol.head(5).copy()
    
    # 6. 领跌/滞涨板块
    tail_sectors = top_gainers.tail(5).sort_values('平均涨幅').copy()
    # 找领跌股
    laggards = []
    for idx in tail_sectors.index:
        s_df = latest_df[latest_df['行业'] == idx]
        if not s_df.empty:
            # 取倒数3名
            tail3 = s_df.sort_values('涨跌幅', ascending=True).head(3)
            strs = [f"{row['股票名称']}({row['涨跌幅']:.1f}%)" for _, row in tail3.iterrows()]
            laggards.append(" ".join(strs))
        else:
            laggards.append("-")
    tail_sectors['领跌股'] = laggards

    # --- 生成报告内容 ---
    
    add_md_header(f"板块效应深度分析报告 ({latest_date.strftime('%Y-%m-%d')})", 1)
    
    trading_df = latest_df[latest_df['成交额(亿)'] > 0]
    
    # 市场概览
    summary = []
    
    # 我们使用未过滤北交所等原始大盘数据计算总成交额
    # 过滤掉个别可能因为单位异常导致超大成交额（比如 > 3000亿 的异常票）
    total_amount = total_market_amount_raw
    summary.append(f"- **全市场成交额**: {total_amount:.2f} 亿")
    if has_money_flow:
        total_inflow = latest_df_raw['主力净流入(亿)'].sum()
        summary.append(f"- **主力净流入**: {total_inflow:.2f} 亿")
        if '散户净流入(亿)' in latest_df_raw.columns:
            total_retail = latest_df_raw['散户净流入(亿)'].sum()
            summary.append(f"- **散户净流入**: {total_retail:.2f} 亿")
    
    # 只统计当日有交易的股票（使用全市场包含北交所）
    trading_df = latest_df_raw[latest_df_raw['成交额(亿)'] > 0]
    up_count = (trading_df['涨跌幅']>0).sum()
    up_ratio = up_count / len(trading_df) if len(trading_df) > 0 else 0
    summary.append(f"- **上涨家数**: {up_count} / {len(trading_df)} ({up_ratio:.1%})")
    
    add_md("\n".join(summary))
    
    # 1. 领涨板块
    add_md_header("领涨板块 Top 8 (Momentum & Early Attack)")
    cols_gainers = {
        '平均涨幅': '今日涨幅', 
        '早盘平均涨幅': '早盘涨幅', 
        '早盘强弱': '早盘强弱', 
        'PE中位数': 'PE(TTM)', 
        '领涨股': '领涨股'
    }
    add_md(df_to_md(top_gainers_disp, cols_gainers))
    
    # 2. 资金战场
    add_md_header("资金战场 Top 8 (Valuation & Retail)")
    cols_money = {
        '总成交额': '成交额(亿)', 
        '主力净流入': '主力流入', 
        '散户净流入': '散户流入', 
        'PE中位数': 'PE(TTM)'
    }
    if not has_money_flow:
        cols_money.pop('主力净流入', None)
        cols_money.pop('散户净流入', None)
        
    add_md(df_to_md(top_money_disp, cols_money))
    
    # 3. 主力资金
    if has_money_flow:
        add_md_header("主力净流入 Top 8 (Smart Money)")
        cols_inflow = {
            '主力净流入': '净流入(亿)', 
            '平均涨幅': '今日涨幅', 
            'PE中位数': 'PE(TTM)', 
            '领涨股': '领涨股'
        }
        add_md(df_to_md(top_inflow_disp, cols_inflow))
        
        add_md_header("主力净流出 Top 5 (Net Outflow)")
        cols_outflow = {
            '主力净流入': '净流出(亿)', 
            '平均涨幅': '今日涨幅', 
            '领跌股': '领跌股'
        }
        add_md(df_to_md(top_outflow_disp, cols_outflow))

    # 4. 趋势偏离
    add_md_header("趋势偏离度 Top 5 (Overbought/BIAS 20)")
    cols_bias = {
        '平均偏离度': '平均偏离%', 
        '60日趋势': '60日趋势%', 
        '平均涨幅': '今日涨幅'
    }
    add_md(df_to_md(top_bias_disp, cols_bias))
    
    add_md_header("超卖/反弹潜力 Top 5 (Oversold)")
    add_md(df_to_md(bottom_bias_disp, cols_bias))
    
    # 5. 资金异动
    add_md_header("资金异动排行榜 Top 5 (Volume Anomaly)")
    cols_vol = {
        '平均量比': '平均量比', 
        '平均涨幅': '今日涨幅', 
        '主力净流入': '主力流入'
    }
    if not has_money_flow:
        cols_vol.pop('主力净流入', None)
    add_md(df_to_md(top_vol_disp, cols_vol))
    
    # 6. 领跌板块
    add_md_header("领跌/滞涨板块 Top 5")
    cols_tail = {
        '平均涨幅': '今日涨幅',
        '3日涨幅': '3日累计',
        '5日涨幅': '5日累计',
        '领跌股': '领跌股'
    }
    add_md(df_to_md(tail_sectors, cols_tail))

    # ==========================
    # 市场状态研判 (Market Regime)
    # ==========================
    add_md_header("市场状态研判 (Market Regime Analysis)")
    
    # 7.0 风格判别 (搅屎棍)
    analyze_regime_style(latest_df, add_md, df_to_md)
    
    # 7.1 抱团度分析 (结构化 Alpha)
    analyze_huddle_ratio(full_df, add_md, df_to_md)

    # ==========================
    # 7. 涨跌停板分析
    # ==========================
    add_md_header("涨跌停板分析 (Market Mood)")
    analyze_limit_up_down(latest_df, full_df, add_md, df_to_md)

    # ==========================
    # 8. 市值分布分析
    # ==========================
    add_md_header("市值分布分析 (Size Factor)")
    analyze_market_cap(latest_df, add_md, df_to_md)

    # ==========================
    # 9. 价格区间分析
    # ==========================
    add_md_header("价格区间分布 (Price Level)")
    analyze_price_level(latest_df, add_md, df_to_md)

    # ==========================
    # 10. 换手率分布分析
    # ==========================
    add_md_header("换手率分布分析 (Activity)")
    analyze_turnover(latest_df, add_md, df_to_md)

    # ==========================
    # 11. 资金行为综合分析
    # ==========================
    if has_money_flow:
        add_md_header("资金行为综合分析 (Smart Money vs Retail)")
        analyze_money_flow_behavior(full_df, add_md, df_to_md)

    # [AI-MODIFIED] 周复盘系统 - 新增全 31 行业资金画像
    if has_money_flow:
        add_md_header("全 31 行业资金画像 (近 5 日累计)")
        analyze_industry_money_full(full_df, add_md, df_to_md)

    # ==========================
    # 12. 行业轮动趋势分析
    # ==========================
    add_md_header("行业轮动趋势 (Sector Rotation)")
    analyze_sector_rotation(daily_sector_pct, add_md, df_to_md)

    # ==========================
    # 13. 市场情绪综合评分
    # ==========================
    add_md_header("市场情绪综合评分 (Market Sentiment)")
    analyze_market_sentiment(latest_df, full_df, sector_stats, has_money_flow, add_md, df_to_md)
    
    # 7. 个股涨跌幅榜
    add_md_header("全市场个股涨幅榜 Top 10")
    # 准备列名映射
    stock_cols = {
        '股票代码': '代码', 
        '股票名称': '名称', 
        '涨跌幅': '涨幅', 
        '收盘价': '现价', 
        '成交额(亿)': '成交额', 
        '行业': '行业',
        'PE': 'PE'
    }
    if has_money_flow:
        stock_cols['主力净流入(亿)'] = '主力流入'
        
    # 涨幅榜
    add_md_header("全市场个股涨幅榜 Top 50")
    top_stocks = latest_df.sort_values('涨跌幅', ascending=False).head(50)
    add_md(df_to_md(top_stocks, stock_cols))
    
    # 涨幅榜行业分布
    add_md_header("涨幅榜 Top 50 行业分布")
    top_sector_counts = top_stocks['行业'].value_counts().reset_index()
    top_sector_counts.columns = ['行业', '上榜数量']
    add_md(df_to_md(top_sector_counts, {'行业': '行业', '上榜数量': '上榜数量'}))
    
    add_md_header("全市场个股跌幅榜 Top 50")
    # 跌幅榜
    bottom_stocks = latest_df.sort_values('涨跌幅', ascending=True).head(50)
    add_md(df_to_md(bottom_stocks, stock_cols))

    # 跌幅榜行业分布
    add_md_header("跌幅榜 Top 50 行业分布")
    bottom_sector_counts = bottom_stocks['行业'].value_counts().reset_index()
    bottom_sector_counts.columns = ['行业', '上榜数量']
    add_md(df_to_md(bottom_sector_counts, {'行业': '行业', '上榜数量': '上榜数量'}))

    # ==========================
    # 8. 成交额Top30新晋个股分析
    # ==========================
    analyze_newcomers(full_df, add_md, add_md_header, df_to_md)

    # 保存文件
    #report_file = "sector_analysis_report.md"
    #按日期命名
    report_file = f"sector_analysis_report_{latest_date.strftime('%Y%m%d')}.md"
    # 保存到当前脚本所在目录的data文件夹
    current_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(current_dir, "data", report_file)
    
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(md_lines))
        
    print(f"\n报告已保存至: {file_path}")

    # ==========================
    # 4. 自动生成趋势复盘
    # ==========================
    print("\n正在生成趋势复盘报告...")
    try:
        analyzer = TrendAnalyzer(os.path.join(current_dir, "data"))
        analyzer.run()
    except Exception as e:
        print(f"趋势分析生成失败: {e}")

def analyze_newcomers(full_df, add_md, add_md_header, df_to_md):
    """
    分析成交额前30且换手率>5%的新晋个股
    """
    # 配置
    TOP_N = 30
    MIN_TURNOVER = 5.0
    TARGET_LOOKBACK_WINDOW = 10
    
    # 获取日期列表
    dates = sorted(full_df['交易日期'].unique())
    if len(dates) < TARGET_LOOKBACK_WINDOW + 1:
        add_md(f"\n警告: 数据天数不足，无法进行新晋个股分析 (需至少{TARGET_LOOKBACK_WINDOW+1}天)")
        return
        
    # 锁定最近11个交易日
    recent_dates = dates[-(TARGET_LOOKBACK_WINDOW + 1):]
    target_date = recent_dates[-1]
    history_dates = recent_dates[:-1]
    
    add_md_header(f"成交额Top30新晋个股分析 ({target_date.strftime('%Y-%m-%d')})")
    
    # 1. 计算历史Top30集合
    top_stocks_history = set()
    for d in history_dates:
        day_data = full_df[full_df['交易日期'] == d]
        # 按成交额降序取前30
        top_30 = day_data.sort_values('成交额', ascending=False).head(TOP_N)
        top_stocks_history.update(top_30['股票代码'].tolist())
        
    # 2. 获取当日Top30
    target_data = full_df[full_df['交易日期'] == target_date].copy()
    target_top_30_raw = target_data.sort_values('成交额', ascending=False).head(TOP_N)
    
    # 展示当日Top30榜单
    add_md(f"\n**今日成交额前{TOP_N}榜单**")
    
    # 准备展示数据
    top30_disp = target_top_30_raw.copy()
    # 确保有换手率
    if '换手率' not in top30_disp.columns:
        top30_disp['换手率'] = np.nan
        
    cols_top30 = {
        '股票代码': '代码',
        '股票名称': '名称',
        '行业': '行业',
        '涨跌幅': '涨跌幅(%)',
        '收盘价': '收盘价',
        '成交额(亿)': '成交额(亿)',
        '换手率': '换手率(%)',
        'PE': 'PE(TTM)'
    }
    add_md(df_to_md(top30_disp, cols_top30))

    # 3. 筛选新晋个股
    # 条件1: 换手率 > 5%
    if '换手率' in target_top_30_raw.columns:
        target_top_30 = target_top_30_raw[target_top_30_raw['换手率'] > MIN_TURNOVER]
    else:
        # 如果没有换手率，尝试计算或跳过过滤
        add_md("\n提示: 缺少换手率数据，跳过换手率筛选。")
        target_top_30 = target_top_30_raw
        
    # 条件2: 不在历史Top30中
    newcomers = target_top_30[~target_top_30['股票代码'].isin(top_stocks_history)]
    
    if newcomers.empty:
        add_md("\n**今日无符合条件的新晋个股。**")
        return
        
    add_md(f"\n**发现 {len(newcomers)} 只新晋主力个股 (换手率>{MIN_TURNOVER}%)**")
    
    # 4. 深度分析
    try:
        analyzer = FinancialAnalyzer()
        results_list = []
        
        for idx, row in newcomers.iterrows():
            stock_code = row['股票代码']
            
            # 获取财务数据
            fin_metrics = analyzer.get_latest_metrics(stock_code)
            # 获取评级数据
            analyst_metrics = analyzer.get_analyst_metrics(stock_code)
            
            item = {
                '代码': stock_code,
                '名称': row['股票名称'],
                '行业': row['行业'],
                '涨跌幅(%)': row.get('涨跌幅', np.nan),
                '收盘价': row['收盘价'],
                '成交额(亿)': row['成交额(亿)'],
                '换手率(%)': row.get('换手率', np.nan),
                '最新财报': fin_metrics.get('report_date', 'N/A'),
                '营收增速(%)': fin_metrics.get('revenue_sq_yoy', np.nan),
                '净利增速(%)': fin_metrics.get('net_profit_sq_yoy', np.nan),
                'PE(TTM)': row.get('PE', np.nan),
                '买入评级': analyst_metrics.get('买入评级', 0),
                '增持评级': analyst_metrics.get('增持评级', 0)
            }
            results_list.append(item)
            
        res_df = pd.DataFrame(results_list)
        # 排序
        res_df = res_df.sort_values('成交额(亿)', ascending=False)
        
        # 转换为Markdown
        # 这里的列名已经是中文了，直接展示
        cols_res = {k:k for k in res_df.columns}
        add_md(df_to_md(res_df, cols_res))
        
    except Exception as e:
        add_md(f"\n深度分析出错: {e}")

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='板块效应全量分析')
    parser.add_argument('--date', type=str, default=None,
                        help='分析日期 YYYYMMDD（默认最新交易日）')
    args = parser.parse_args()
    main(args.date)

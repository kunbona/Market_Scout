"""
[AI-GENERATED] 个股推荐脚本
从强势行业中多维度打分选股，输出 Top N 推荐名单
用法: python tools/stock_recommender.py 电子 有色金属 [--top 10] [--print]
"""
import pandas as pd
import numpy as np
import os
import sys
import argparse
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
from functools import partial

STOCK_PATH = os.environ.get('QUANT_DATA_ROOT', '/Users/kun/Desktop/AGdata') + '/stock-trading-data-pro'
LOOKBACK = 60  # 加载最近60个交易日用于计算指标
MAX_STALENESS_DAYS = 90  # 股票最新交易日距分析日超过此天数视为退市/停更

# ============================================================
# 1. 股票数据读取（只读需要的列）
# ============================================================
def read_stock(file_path, min_days=120, max_staleness_days=90, ref_date=None):
    """读取单只股票数据，只提取打分需要的列。
    min_days: 最少上市交易天数（过滤新股）
    max_staleness_days: 最新交易日超过此天数视为退市/停更（过滤僵尸股）
    ref_date: 参考日期，用于计算staleness。None则自动取全市场最新日期
    """
    try:
        fname = os.path.basename(file_path)
        code_prefix = fname.lower()[:2]
        # 过滤北交所
        if code_prefix == 'bj':
            return None

        # 探测跳过行
        skip_rows = 0
        with open(file_path, 'r', encoding='gbk', errors='ignore') as f:
            if '股票代码' not in f.readline():
                skip_rows = 1

        # 只读需要的列（用列索引避免编码问题）
        # 0:股票代码 1:股票名称 2:交易日期 6:收盘价 8:成交量 9:成交额
        # 10:流通市值 11:总市值 12:净利润TTM
        # 18:机构资金买入额 19:机构资金卖出额
        # 20:大户资金买入额 21:大户资金卖出额
        # 32:申万一级行业
        df = pd.read_csv(file_path, encoding='gbk', skiprows=skip_rows,
                         usecols=[0, 1, 2, 6, 8, 9, 10, 11, 18, 19, 20, 21, 32],
                         dtype={18: float, 19: float, 20: float, 21: float})
        df.columns = ['code', 'name', 'date', 'close', 'volume', 'amount',
                      'float_mv', 'total_mv', 'inst_buy', 'inst_sell',
                      'big_buy', 'big_sell', 'industry']

        df['date'] = pd.to_datetime(df['date'], errors='coerce')
        df = df.dropna(subset=['date', 'close'])
        df = df.sort_values('date')

        # --- 过滤退市/停更（僵尸股）---
        last_trade_date = df['date'].max()
        if ref_date is None:
            ref_date = pd.Timestamp.now()  # 默认用当前日期，>90天未交易视为退市
        staleness = (pd.Timestamp(ref_date) - last_trade_date).days
        if staleness > max_staleness_days:
            return None  # 数据停更太久，已退市或摘牌

        # --- 过滤 ST / *ST / 退市股 ---
        stock_name = str(df['name'].iloc[-1])
        if 'ST' in stock_name or '退' in stock_name:
            return None

        # 过滤新股：上市不足 min_days 个交易日
        total_days = len(df)
        if total_days < min_days:
            return None

        # 只保留最近 LOOKBACK 天用于计算指标
        df = df.tail(LOOKBACK)

        if len(df) < 15:
            return None

        # --- 计算所有指标 ---
        close = df['close'].values
        cur = close[-1]

        # 收益率
        ret5 = cur / close[-6] - 1 if len(close) > 5 else np.nan
        ret10 = cur / close[-11] - 1 if len(close) > 10 else np.nan
        ret20 = cur / close[-21] - 1 if len(close) > 20 else np.nan

        # 均线
        ma10 = np.mean(close[-10:]) if len(close) >= 10 else cur
        ma20 = np.mean(close[-20:]) if len(close) >= 20 else cur

        # 量比（今日成交额 / 5日均额）
        amount_s = df['amount'].values
        amt_ma5 = np.mean(amount_s[-6:-1]) if len(amount_s) >= 6 else amount_s[-1]
        vol_ratio = amount_s[-1] / amt_ma5 if amt_ma5 > 0 else 1.0

        # 换手率（成交额/流通市值）
        fv = df['float_mv'].iloc[-1]
        turnover = amount_s[-1] / fv if pd.notna(fv) and fv > 0 else 0

        # 主力净流入（万元→亿元）
        money_cols = ['inst_buy', 'inst_sell', 'big_buy', 'big_sell']
        for c in money_cols:
            df[c] = df[c].fillna(0)
        df['main_net'] = (df['inst_buy'] + df['big_buy']) - (df['inst_sell'] + df['big_sell'])
        main_net_3d = df['main_net'].tail(3).sum() / 10000  # 万元→亿
        main_net_5d = df['main_net'].tail(5).sum() / 10000
        main_net_1d = df['main_net'].iloc[-1] / 10000

        # PE
        total_mv = df['total_mv'].iloc[-1]
        profit = df.iloc[-1].get('total_mv', np.nan)  # placeholder
        # PE from 总市值/净利润TTM - 需要额外列
        # 简化：用流通市值/成交额作为估值代理

        # 振幅（最近5日）
        high_low_vol = (close[-5:].max() - close[-5:].min()) / close[-5:].mean() if len(close) >= 5 else 0

        return {
            'code': df['code'].iloc[-1],
            'name': df['name'].iloc[-1],
            'industry': str(df['industry'].iloc[-1]).strip(),
            'close': cur,
            'ret5': ret5, 'ret10': ret10, 'ret20': ret20,
            'above_ma10': cur > ma10, 'above_ma20': cur > ma20,
            'vol_ratio': vol_ratio,
            'turnover': turnover,
            'main_net_1d': main_net_1d,
            'main_net_3d': main_net_3d,
            'main_net_5d': main_net_5d,
            'float_mv': fv if pd.notna(fv) else 0,
            'total_mv': total_mv if pd.notna(total_mv) else 0,
            'amount_yi': amount_s[-1] / 1e8,
            'hl_volatility': high_low_vol,
        }
    except Exception:
        return None


# ============================================================
# 2. 多维度打分
# ============================================================
def compute_scores(df, industry_strength=None):
    """对每只股票计算综合得分"""
    scores = pd.DataFrame()
    scores['code'] = df['code']
    scores['name'] = df['name']
    scores['industry'] = df['industry']

    # --- 维度1: 主力资金（25分）---
    # 3日净流入，行业内标准化
    for ind in df['industry'].unique():
        mask = df['industry'] == ind
        vals = df.loc[mask, 'main_net_3d'].fillna(0)
        if vals.std() > 0:
            scores.loc[mask, 'S_money'] = (vals - vals.min()) / (vals.max() - vals.min()) * 25
        else:
            scores.loc[mask, 'S_money'] = 12.5
    scores['S_money'] = scores['S_money'].fillna(0).clip(0, 25)

    # --- 维度2: 趋势动量（20分）---
    for ind in df['industry'].unique():
        mask = df['industry'] == ind
        vals = df.loc[mask, 'ret20'].fillna(0)
        if vals.std() > 0:
            scores.loc[mask, 'S_trend'] = (vals - vals.min()) / (vals.max() - vals.min()) * 20
        else:
            scores.loc[mask, 'S_trend'] = 10
    scores['S_trend'] = scores['S_trend'].fillna(0).clip(0, 20)

    # --- 维度3: 近期热度（15分）---
    for ind in df['industry'].unique():
        mask = df['industry'] == ind
        # ret5 + vol_ratio 组合
        r5 = df.loc[mask, 'ret5'].fillna(0)
        vr = df.loc[mask, 'vol_ratio'].fillna(1)
        heat = (r5.rank(pct=True) + vr.rank(pct=True)) / 2
        scores.loc[mask, 'S_heat'] = heat * 15
    scores['S_heat'] = scores['S_heat'].fillna(0).clip(0, 15)

    # --- 维度4: 流动性（10分）---
    # 换手率适中最好（排除极端值），成交额大更好
    for ind in df['industry'].unique():
        mask = df['industry'] == ind
        to = df.loc[mask, 'turnover'].fillna(0).clip(0, 0.2)  # 上限20%
        amt = df.loc[mask, 'amount_yi'].fillna(0)
        liq = to.rank(pct=True) * 0.4 + amt.rank(pct=True) * 0.6
        scores.loc[mask, 'S_liq'] = liq * 10
    scores['S_liq'] = scores['S_liq'].fillna(0).clip(0, 10)

    # --- 维度5: 低波保护（10分）---
    for ind in df['industry'].unique():
        mask = df['industry'] == ind
        vals = df.loc[mask, 'hl_volatility'].fillna(0)
        if vals.std() > 0:
            # 低波高分（反向排名）
            scores.loc[mask, 'S_lowvol'] = (1 - (vals - vals.min()) / (vals.max() - vals.min())) * 10
        else:
            scores.loc[mask, 'S_lowvol'] = 5
    scores['S_lowvol'] = scores['S_lowvol'].fillna(0).clip(0, 10)

    # --- 维度6: 行业强势加分（10分）---
    if industry_strength:
        scores['S_industry'] = df['industry'].map(industry_strength).fillna(0) / 100 * 10
    else:
        scores['S_industry'] = 5
    scores['S_industry'] = scores['S_industry'].clip(0, 10)

    # --- 维度7: 规模合理性（10分）---
    for ind in df['industry'].unique():
        mask = df['industry'] == ind
        mv = df.loc[mask, 'total_mv'].fillna(0) / 1e8  # 转亿
        if mv.std() > 0:
            # 偏好行业中位数附近的市值（太大涨不动，太小风险高）
            median_mv = mv.median()
            mv_score = 1 - abs(mv - median_mv) / (mv.max() - mv.min() + 1)
            scores.loc[mask, 'S_size'] = mv_score * 10
        else:
            scores.loc[mask, 'S_size'] = 5
    scores['S_size'] = scores['S_size'].fillna(0).clip(0, 10)

    # ======== 综合得分 ========
    scores['总分'] = (scores['S_money'] + scores['S_trend'] + scores['S_heat'] +
                     scores['S_liq'] + scores['S_lowvol'] + scores['S_industry'] +
                     scores['S_size'])
    return scores.sort_values('总分', ascending=False)


# ============================================================
# 3. 生成推荐理由
# ============================================================
def generate_reason(row):
    """根据各维度得分生成一句话理由"""
    reasons = []

    if row['S_money'] >= 18:
        reasons.append(f"主力3日净流入{row.get('main_net_3d', 0):.1f}亿")
    elif row['S_money'] >= 8:
        reasons.append("主力资金偏多")

    if row['S_trend'] >= 15:
        reasons.append(f"20日涨{row.get('ret20', 0)*100:.0f}%趋势强")

    if row['S_heat'] >= 10:
        reasons.append(f"近期放量活跃(量比{row.get('vol_ratio', 1):.1f})")

    if row['S_lowvol'] >= 7:
        reasons.append("低波动稳健")

    if row['S_industry'] >= 7:
        reasons.append(f"{row['industry']}行业强势")

    if row['S_size'] >= 7:
        mv_yi = row.get('total_mv', 0) / 1e8
        reasons.append(f"市值适中({mv_yi:.0f}亿)")

    if not reasons:
        reasons.append("综合评分靠前")

    return "；".join(reasons[:3])


# ============================================================
# 4. 主函数
# ============================================================
def main():
    parser = argparse.ArgumentParser(description='强势行业内个股推荐')
    parser.add_argument('industries', nargs='+', help='强势行业名称，如: 电子 有色金属')
    parser.add_argument('--top', type=int, default=10, help='每个行业推荐数量（默认10）')
    parser.add_argument('--min-amount', type=float, default=1.0, help='最小成交额(亿)')
    parser.add_argument('--min-turnover', type=float, default=0.01, help='最小换手率')
    parser.add_argument('--min-days', type=int, default=120, help='最少上市交易天数（默认120≈半年）')
    parser.add_argument('--max-staleness', type=int, default=MAX_STALENESS_DAYS,
                        help=f'最新交易日超过此天数视为退市（默认{MAX_STALENESS_DAYS}天）')
    parser.add_argument('--strength', type=str, default=None,
                        help='行业强势占比，如: "电子:55.6,有色金属:52.2"')
    parser.add_argument('--output', type=str, default=None, help='输出文件路径')
    args = parser.parse_args()

    # 解析行业强势度
    industry_strength = {}
    if args.strength:
        for pair in args.strength.split(','):
            k, v = pair.split(':')
            industry_strength[k.strip()] = float(v)

    print(f"🔍 扫描行业: {args.industries}")
    print(f"📊 行业强势度: {industry_strength if industry_strength else '未提供'}")

    # 扫描股票文件
    files = list(Path(STOCK_PATH).glob('*.csv'))
    print(f"📂 共 {len(files)} 个股票文件，筛选目标行业...")

    # 复用 sentiment_cycle 同一份 lookback 逻辑: 凌晨 02:50 跑应该用 8/12 而不是 8/13
    # pd.Timestamp.now() 会显示当天 — CSV 8/13 还没开盘实际数据是 8/12
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))  # 让 import quant.loader 找得到
    try:
        from quant.loader import get_latest_trade_date as _gltd
        _analysis_date = _gltd() or pd.Timestamp.now().strftime('%Y-%m-%d')
    except Exception:
        _analysis_date = pd.Timestamp.now().strftime('%Y-%m-%d')

    def _resolve_analysis_date() -> str:
        return _analysis_date

    # 并行读取
    max_workers = max(1, min(12, (os.cpu_count() or 4) - 2))
    results = []
    read_func = partial(read_stock, min_days=args.min_days,
                        max_staleness_days=args.max_staleness)
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        for r in executor.map(read_func, files):
            if r and r['industry'] in args.industries:
                results.append(r)

    if not results:
        print(f"❌ 未找到属于 {args.industries} 的股票（已自动过滤退市/ST/停更>{args.max_staleness}天个股）")
        return

    df = pd.DataFrame(results)
    kept = len(df)
    total = len(files)
    print(f"✅ 找到 {kept} 只目标行业股票（全市场{total}只，已过滤退市/ST/停更>{args.max_staleness}天个股）")

    # 基础过滤
    df = df[df['amount_yi'] >= args.min_amount]
    df = df[df['turnover'] >= args.min_turnover]
    print(f"🔎 过滤后（成交>{args.min_amount}亿, 换手>{args.min_turnover*100:.0f}%）: {len(df)} 只")

    # 打分
    scores = compute_scores(df, industry_strength)

    # 合并原始数据
    scores = scores.merge(
        df[['code', 'name', 'industry', 'close', 'ret5', 'ret20', 'vol_ratio',
            'turnover', 'main_net_3d', 'amount_yi', 'total_mv']],
        on=['code', 'name', 'industry'], how='left'
    )

    # 按行业分组输出
    lines = []
    lines.append(f"# 强势行业个股推荐（{_resolve_analysis_date()}）\n")

    for ind in args.industries:
        ind_scores = scores[scores['industry'] == ind].head(args.top)
        if ind_scores.empty:
            continue

        strength_str = f" (强势占比 {industry_strength.get(ind, '?')}%)" if ind in industry_strength else ""
        lines.append(f"## {ind}{strength_str}\n")
        lines.append("| 排名 | 代码 | 名称 | 现价 | 综合分 | 20日涨 | 主力3日 | 核心理由 |")
        lines.append("|------|------|------|------|--------|--------|---------|---------|")

        for rank, (_, row) in enumerate(ind_scores.iterrows(), 1):
            reason = generate_reason(row)
            lines.append(
                f"| {rank} | {row['code']} | {row['name']} | {row['close']:.2f} | "
                f"{row['总分']:.0f} | {row['ret20']*100:+.1f}% | "
                f"{row['main_net_3d']:+.1f}亿 | {reason} |"
            )
        lines.append("")

    # 输出
    output = "\n".join(lines)
    print(output)

    if args.output:
        os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(output)
        print(f"\n📁 报告已保存: {args.output}")


if __name__ == '__main__':
    main()

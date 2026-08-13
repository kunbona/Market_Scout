"""
[AI-GENERATED] 市场状态分析脚本
分析A股当前市场状态：趋势/波动率/风格/宽度/行业轮动

[AI-MODIFIED] 周复盘系统 - 改造为支持 --date 参数指定历史分析日期（保留自动检测向后兼容）
"""
import pandas as pd
import numpy as np
import json
import os

from ._paths import require_quant_data_root
import warnings
warnings.filterwarnings('ignore')

DATA_PATH = require_quant_data_root()
INDEX_PATH = os.path.join(DATA_PATH, 'stock-main-index-data')
STOCK_PATH = os.path.join(DATA_PATH, 'stock-trading-data-pro')
# [AI-MODIFIED] 改为可通过 --date 参数指定，未指定时自动从指数数据中检测最新交易日
ANALYSIS_DATE = None  # CLI 启动时根据参数设置，单元测试可手动指定

# ==================== 1. 宽基指数分析 ====================
INDEX_MAP = {
    'sh000001': '上证综指', 'sh000016': '上证50', 'sh000300': '沪深300',
    'sh000905': '中证500', 'sh000852': '中证1000', 'sh932000': '中证2000',
    'sh000688': '科创50', 'sz399001': '深证成指', 'sz399006': '创业板指',
    'sz399303': '国证2000', 'sz399330': '深证100', 'bj899050': '北证50',
}

def load_index_data(code):
    fp = os.path.join(INDEX_PATH, f'{code}.csv')
    if not os.path.exists(fp):
        return None
    df = pd.read_csv(fp, encoding='gbk')
    df['candle_end_time'] = pd.to_datetime(df['candle_end_time'])
    df = df.set_index('candle_end_time').sort_index()
    return df

def calc_returns(df, periods=[1, 5, 10, 20, 60, 120, 250]):
    results = {}
    close = df['close']
    for p in periods:
        if len(close) > p:
            ret = close.iloc[-1] / close.iloc[-p-1] - 1
            results[f'{p}日涨跌幅'] = round(ret * 100, 2)
    # 年内表现
    idx = close.index
    # [AI-MODIFIED] 根据 ANALYSIS_DATE 动态确定年初（支持历史日期回测）
    if ANALYSIS_DATE:
        target_year = pd.Timestamp(ANALYSIS_DATE).year
    else:
        target_year = idx[-1].year
    year_start_mask = (idx >= pd.Timestamp(f'{target_year}-01-01'))
    if year_start_mask.any():
        year_start = close[year_start_mask].iloc[0]
        results['年内涨跌幅'] = round((close.iloc[-1] / year_start - 1) * 100, 2)
    return results

def calc_ma_info(df):
    close = df['close']
    mas = {}
    for p in [5, 10, 20, 60, 120]:
        ma = close.rolling(p).mean()
        mas[f'MA{p}'] = round(ma.iloc[-1], 2)
    current = close.iloc[-1]
    ma_status = {}
    for name, ma_val in mas.items():
        ratio = round((current / ma_val - 1) * 100, 2)
        ma_status[name] = {'值': ma_val, '偏离%': ratio, '上方': current > ma_val}
    return current, ma_status

def calc_volatility(df, window=20):
    close = df['close']
    rets = close.pct_change().dropna()
    # 20日已实现波动率(年化)
    vol20 = rets.tail(window).std() * np.sqrt(252) * 100
    # 60日已实现波动率
    vol60 = rets.tail(60).std() * np.sqrt(252) * 100 if len(rets) >= 60 else vol20
    # 历史波动率分位数
    rolling_vol = rets.rolling(20).std() * np.sqrt(252)
    percentile = (rolling_vol.dropna() < (vol20 / 100)).mean() * 100
    return round(vol20, 2), round(vol60, 2), round(percentile, 1)

def calc_volume_trend(df, window=5):
    volume = df['amount']
    if len(volume) < window * 2:
        return '数据不足'
    recent_avg = volume.tail(window).mean()
    prev_avg = volume.iloc[-(window*2):-window].mean()
    ratio = recent_avg / prev_avg
    return f"{'放量' if ratio > 1.1 else '缩量' if ratio < 0.9 else '持平'}({ratio:.2f})"


def main(analysis_date=None):
    """主函数：分析市场状态并输出报告

    Args:
        analysis_date: 分析日期（字符串 YYYYMMDD 或 YYYY-MM-DD），None 表示自动检测最新交易日
    """
    global ANALYSIS_DATE

    # ==================== 1. 设置分析日期 ====================
    if analysis_date is not None:
        # 支持 YYYYMMDD 或 YYYY-MM-DD 格式
        if isinstance(analysis_date, str):
            analysis_date = analysis_date.replace('-', '')
        ANALYSIS_DATE = pd.Timestamp(analysis_date).strftime('%Y-%m-%d')

    # ==================== 2. 加载指数数据 ====================
    print("\n[1/5] 加载指数数据...")
    index_data = {}
    for code, name in INDEX_MAP.items():
        df = load_index_data(code)
        if df is not None and len(df) > 0:
            index_data[code] = df

    # ==================== 3. 自动检测最新交易日 ====================
    if ANALYSIS_DATE is None and index_data:
        ref_df = index_data.get('sh000001')
        if ref_df is not None and len(ref_df) > 0:
            last_dt = ref_df.index[-1]
            ANALYSIS_DATE = last_dt.strftime('%Y-%m-%d')
            print(f"  [自动检测] 最新交易日: {ANALYSIS_DATE}")

    # 打印标题（带分析日期）
    print("\n" + "=" * 60)
    print(f"A股市场状态分析 - {ANALYSIS_DATE}")
    print("=" * 60)

    # ==================== 4. 根据 ANALYSIS_DATE 切片指数数据 ====================
    # [AI-MODIFIED] 周复盘系统 - 根据 --date 参数切片所有指数数据，使后续 iloc[-1] 自动取目标日期
    if ANALYSIS_DATE:
        target_date = pd.Timestamp(ANALYSIS_DATE).normalize()
        sliced_count = 0
        empty_codes = []
        for code in list(index_data.keys()):
            df = index_data[code]
            df = df[df.index <= target_date]
            if len(df) > 0:
                index_data[code] = df
                sliced_count += 1
            else:
                del index_data[code]
                empty_codes.append(code)
        if sliced_count > 0:
            print(f"  [日期切片] 已将 {sliced_count} 个指数数据切片至 {ANALYSIS_DATE} 或之前")
        if empty_codes:
            print(f"  [警告] {len(empty_codes)} 个指数在 {ANALYSIS_DATE} 之前无数据: {empty_codes}")

    # ==================== 指数分析 ====================
    print("[2/5] 分析指数表现...")
    index_results = []
    for code, name in INDEX_MAP.items():
        if code not in index_data:
            continue
        df = index_data[code]
        rets = calc_returns(df)
        close, ma_info = calc_ma_info(df)
        vol20, vol60, vol_pct = calc_volatility(df)
        vol_trend = calc_volume_trend(df)

        # MA均线排列确认
        ma_values = [ma_info[f'MA{p}']['值'] for p in [5,10,20,60,120] if f'MA{p}' in ma_info]
        is_bullish_alignment = all(ma_values[i] > ma_values[i+1] for i in range(len(ma_values)-1))
        is_bearish_alignment = all(ma_values[i] < ma_values[i+1] for i in range(len(ma_values)-1))

        # 价格在MA60上方百分比
        above_ma_cnt = sum(1 for k, v in ma_info.items() if v['上方'])
        total_ma = len(ma_info)

        index_results.append({
            'code': code, 'name': name, 'close': round(close, 2),
            'returns': rets,
            'ma_status': {f'MA{p}': '↑' if ma_info[f'MA{p}']['上方'] else '↓' for p in [5,10,20,60,120] if f'MA{p}' in ma_info},
            'ma_bullish': is_bullish_alignment,
            'ma_bearish': is_bearish_alignment,
            'above_ma_ratio': f"{above_ma_cnt}/{total_ma}",
            'volatility_20d': vol20,
            'volatility_60d': vol60,
            'vol_percentile': vol_pct,
            'volume_trend': vol_trend,
        })

    # 打印指数分析表格
    print(f"\n{'='*110}")
    print(f"{'指数':<10} {'收盘价':>8} {'当日':>6} {'5日':>6} {'10日':>6} {'20日':>6} {'60日':>6} {'年内':>6} {'MA5':>4} {'MA10':>4} {'MA20':>4} {'MA60':>4} {'MA120':>4} {'20日波':>7}")
    print(f"{'='*110}")
    for r in index_results:
        name = r['name']
        close = r['close']
        rets = r['returns']
        ma = r['ma_status']
        print(f"{name:<10} {close:>8.0f} "
              f"{rets.get('1日涨跌幅', 0):>5.1f}% "
              f"{rets.get('5日涨跌幅', 0):>5.1f}% "
              f"{rets.get('10日涨跌幅', 0):>5.1f}% "
              f"{rets.get('20日涨跌幅', 0):>5.1f}% "
              f"{rets.get('60日涨跌幅', 0):>5.1f}% "
              f"{rets.get('年内涨跌幅', 0):>5.1f}% "
              f"{ma.get('MA5','-'):>4} {ma.get('MA10','-'):>4} {ma.get('MA20','-'):>4} {ma.get('MA60','-'):>4} {ma.get('MA120','-'):>4} "
              f"{r['volatility_20d']:>6.1f}%")

    # ==================== 风格分析 ====================
    print("\n" + "=" * 60)
    print("风格对比分析")
    print("=" * 60)

    # 大盘 vs 小盘：沪深300 vs 中证2000/国证2000
    for label, large, small in [('大盘vs小盘', 'sh000300', 'sh932000'),
                                  ('超大盘vs中盘', 'sh000016', 'sh000905'),
                                  ('价值vs成长', 'sh000016', 'sz399006')]:
        if large in index_data and small in index_data:
            large_close = index_data[large]['close']
            small_close = index_data[small]['close']

            # 相对强弱：过去20日
            large_ret20 = large_close.pct_change().tail(20).sum()
            small_ret20 = small_close.pct_change().tail(20).sum()
            diff = (small_ret20 - large_ret20) * 100

            large_name = INDEX_MAP[large]
            small_name = INDEX_MAP[small]
            winner = small_name if diff > 0 else large_name
            print(f"  {label}: 近20日 {small_name}({small_ret20*100:.1f}%) vs {large_name}({large_ret20*100:.1f}%), "
                  f"差值{diff:.1f}%, 当前{winner}占优")

    # ==================== 市场宽度与行业分析 ====================
    # Stock data columns (by index, avoiding encoding issues):
    #   0:股票代码, 2:交易日期, 3:开盘价, 6:收盘价, 8:成交量, 9:成交额
    #   32:新版申万一级行业名称, 33:新版申万二级行业名称

    print("\n[3/5] 加载股票数据（全市场采样分析）...")
    stock_files = [f for f in os.listdir(STOCK_PATH) if f.endswith('.csv')]
    np.random.seed(42)
    sample_files = np.random.choice(stock_files, size=min(3000, len(stock_files)), replace=False)

    # [AI-MODIFIED] 周复盘系统 - 根据 ANALYSIS_DATE 动态确定股票数据范围
    if ANALYSIS_DATE:
        target_dt = pd.Timestamp(ANALYSIS_DATE)
        recent_start = target_dt - pd.Timedelta(days=120)
    else:
        recent_start = pd.Timestamp('2026-03-01')

    width_data = []
    for fname in sample_files:
        try:
            fp = os.path.join(STOCK_PATH, fname)
            df = pd.read_csv(fp, encoding='gbk', header=1, usecols=[0, 2, 6, 32])
            df.columns = ['code', 'date', 'close', 'industry']
            df['date'] = pd.to_datetime(df['date'])
            df = df.dropna(subset=['date', 'close'])
            df = df.sort_values('date')
            # 同时按目标日期上限切片
            if ANALYSIS_DATE:
                df = df[df['date'] <= target_dt]
            recent = df[df['date'] >= recent_start]
            if len(recent) < 25:  # Need 20+ points for MA20
                continue
            close_s = recent['close'].dropna()
            if len(close_s) < 20:
                continue
            ma10 = close_s.rolling(10).mean().iloc[-1]
            ma20 = close_s.rolling(20).mean().iloc[-1]
            cur_close = close_s.iloc[-1]
            ret5 = cur_close / close_s.iloc[-6] - 1 if len(close_s) > 5 else np.nan
            ret20 = cur_close / close_s.iloc[-21] - 1 if len(close_s) > 20 else np.nan
            industry = str(recent.iloc[-1]['industry']).strip()
            if industry in ('nan', '', '未知'):
                industry = '未知'
            width_data.append({
                'code': fname.replace('.csv', ''),
                'ret5': ret5, 'ret20': ret20,
                'above_ma10': cur_close > ma10 if not pd.isna(ma10) else None,
                'above_ma20': cur_close > ma20 if not pd.isna(ma20) else None,
                'industry': industry,
            })
        except Exception:
            continue

    print(f"  成功加载: {len(width_data)} 只股票")

    print("\n" + "=" * 60)
    print("市场宽度分析")
    print("=" * 60)

    if width_data:
        wdf = pd.DataFrame(width_data)
        valid_ma10 = wdf[wdf['above_ma10'].notna()]
        valid_ma20 = wdf[wdf['above_ma20'].notna()]
        pct_above_ma10 = valid_ma10['above_ma10'].mean() * 100 if len(valid_ma10) > 0 else 0
        pct_above_ma20 = valid_ma20['above_ma20'].mean() * 100 if len(valid_ma20) > 0 else 0
        pct_positive_5d = (wdf['ret5'] > 0).mean() * 100
        pct_positive_20d = (wdf['ret20'] > 0).mean() * 100
        med_ret5 = wdf['ret5'].median() * 100
        med_ret20 = wdf['ret20'].median() * 100

        print(f"  股价 > MA10 占比: {pct_above_ma10:.1f}%")
        print(f"  股价 > MA20 占比: {pct_above_ma20:.1f}%")
        print(f"  近5日上涨占比: {pct_positive_5d:.1f}%")
        print(f"  近20日上涨占比: {pct_positive_20d:.1f}%")
        print(f"  中位数5日收益率: {med_ret5:.2f}%")
        print(f"  中位数20日收益率: {med_ret20:.2f}%")

        if pct_above_ma20 > 60:
            width_signal = "强势（多数股票在均线上方）"
        elif pct_above_ma20 > 40:
            width_signal = "中性偏强"
        elif pct_above_ma20 > 25:
            width_signal = "中性偏弱"
        else:
            width_signal = "弱势（多数股票在均线下方）"
        print(f"  宽度判断: {width_signal}")

    # ==================== 行业轮动分析 (搅屎棍方法) ====================
    print("\n" + "=" * 60)
    print("行业轮动分析（搅屎棍方法）")
    print("=" * 60)

    if width_data:
        wdf = pd.DataFrame(width_data)
        industry_strength = {}
        for ind, group in wdf.groupby('industry'):
            if ind == '未知' or len(group) < 3:
                continue
            valid = group[group['above_ma10'].notna()]
            if len(valid) < 3:
                continue
            strength = valid['above_ma10'].mean() * 100
            avg_ret5 = valid['ret5'].mean() * 100
            avg_ret20 = valid['ret20'].mean() * 100
            industry_strength[ind] = {
                '强势占比': round(strength, 1),
                '5日平均涨幅': round(avg_ret5, 2),
                '20日平均涨幅': round(avg_ret20, 2),
                '样本数': len(valid),
            }

        sorted_industries = sorted(industry_strength.items(), key=lambda x: x[1]['强势占比'], reverse=True)

        print(f"\n{'行业':<14} {'强势占比':>8} {'5日涨幅':>8} {'20日涨幅':>8} {'样本数':>6}")
        print("-" * 50)
        for ind, data in sorted_industries:
            bar = '█' * int(data['强势占比'] / 10)
            print(f"{ind:<14} {data['强势占比']:>7.1f}% {data['5日平均涨幅']:>7.2f}% {data['20日平均涨幅']:>7.2f}% {data['样本数']:>6} {bar}")

        cycle_industries = ['银行', '钢铁', '有色金属', '煤炭', '石油石化', '基础化工', '机械设备', '建筑材料']
        value_industries = ['公用事业', '交通运输', '非银金融', '食品饮料', '家用电器', '建筑装饰', '纺织服饰', '银行']

        print("\n" + "-" * 50)
        print("周期/价值/成长 风格判断:")

        cycle_strong = [ind for ind in cycle_industries if ind in industry_strength and industry_strength[ind]['强势占比'] > 50]
        value_strong = [ind for ind in value_industries if ind in industry_strength and industry_strength[ind]['强势占比'] > 50]
        cycle_avg_strength = np.mean([industry_strength[ind]['强势占比'] for ind in cycle_industries if ind in industry_strength])
        value_avg_strength = np.mean([industry_strength[ind]['强势占比'] for ind in value_industries if ind in industry_strength])
        all_avg = np.mean([d['强势占比'] for d in industry_strength.values()])

        print(f"  周期行业平均强势占比: {cycle_avg_strength:.1f}%  (强势行业: {len(cycle_strong)}/8)")
        print(f"  价值行业平均强势占比: {value_avg_strength:.1f}%  (强势行业: {len(value_strong)}/8)")
        print(f"  全行业平均强势占比: {all_avg:.1f}%")

        if len(cycle_strong) >= 5:
            style = "周期主导"
        elif len(value_strong) >= 4 and cycle_avg_strength < value_avg_strength:
            style = "价值主导"
        else:
            style = "成长/混合"

        if len(cycle_strong) >= 3 and len(value_strong) >= 3:
            style += "（周期价值共振）"

        print(f"  => 当前风格: {style}")

    # ==================== 综合判断 ====================
    print("\n" + "=" * 60)
    print("综合市场状态判断")
    print("=" * 60)

    # 计算综合指标
    if 'sh000001' in index_data:
        sh = index_data['sh000001']
        close_all = sh['close']
        ma60 = close_all.rolling(60).mean().iloc[-1]
        ma20 = close_all.rolling(20).mean().iloc[-1]

    # 从指数分析结果提取关键信息
    sh_ret20 = None
    for r in index_results:
        if r['code'] == 'sh000001':
            sh_ret20 = r['returns'].get('20日涨跌幅', 0)
            break

    # 趋势判断
    if sh_ret20 is not None:
        if sh_ret20 > 5:
            trend = "📈 强势上涨"
        elif sh_ret20 > 0:
            trend = "📈 温和上涨"
        elif sh_ret20 > -5:
            trend = "📉 弱势下跌"
        else:
            trend = "📉 明显下跌"

    # 波动率判断
    avg_vol20 = np.mean([r['volatility_20d'] for r in index_results]) if index_results else 0
    vol_judgment = "高波动" if avg_vol20 > 25 else "中等波动" if avg_vol20 > 15 else "低波动"

    print(f"  趋势: {trend} (上证近20日: {sh_ret20:.1f}%)" if sh_ret20 is not None else f"  趋势: {trend}")
    print(f"  波动率: {vol_judgment} (平均20日年化波: {avg_vol20:.1f}%)")
    print(f"  宽度: {width_signal if width_data else 'N/A'}")
    print(f"  风格: {style if width_data else 'N/A'}")

    # Regime标签
    if width_data and sh_ret20 is not None:
        if sh_ret20 > 0 and avg_vol20 < 20 and pct_above_ma20 > 50:
            regime = "低波上涨"
            confidence = "高"
        elif sh_ret20 < 0 and avg_vol20 > 20:
            regime = "高波下跌"
            confidence = "高" if pct_above_ma20 < 35 else "中"
        elif abs(sh_ret20) < 3 and avg_vol20 < 20:
            regime = "低波震荡"
            confidence = "中"
        elif abs(sh_ret20) < 3 and avg_vol20 > 20:
            regime = "高波震荡"
            confidence = "中"
        elif sh_ret20 > 0 and avg_vol20 > 20:
            regime = "高波上涨"
            confidence = "低"
        else:
            regime = "弱势下跌"
            confidence = "中"

        print(f"\n  Regime标签: {regime}")
        print(f"  置信度: {confidence}")

        # 策略建议
        print("\n" + "=" * 60)
        print("策略适配建议")
        print("=" * 60)

        if regime == "低波上涨":
            print("  ✅ 推荐: 小市值策略、动量策略")
            print("  ⚠️ 谨慎: 红利策略（相对弱势）")
        elif regime == "高波下跌":
            print("  ✅ 推荐: 红利策略、诺亚方舟策略")
            print("  ❌ 回避: 小市值策略（回撤大）")
        elif "震荡" in regime:
            print("  ✅ 推荐: 价值策略、现金流策略")
            print("  ⚠️ 谨慎: 动量策略（频繁止损）")
        elif regime == "高波上涨":
            print("  ✅ 推荐: 小市值（控制仓位）、动量")
            print("  ⚠️ 注意: 设好止损，高波环境下回撤风险大")
        else:
            print("  ✅ 推荐: 红利策略、诺亚方舟策略（防御为主）")
            print("  ❌ 回避: 小市值策略、高贝塔策略")

        # 如果判断错了的应对方案
        print(f"\n  ⚠️ 如果判断错了: 若市场在未来2周内转向，")
        if "上涨" in regime:
            print(f"     关注MA20是否被跌破，若跌破且放量则可能转震荡/下跌，应及时减仓")
        elif "下跌" in regime:
            print(f"     关注指数是否重新站上MA20且成交量放大，可能是反转信号")
        else:
            print(f"     关注指数是否突破震荡区间（上下轨），放量突破方向即为新趋势方向")

    print("\n" + "=" * 60)
    print("分析完成")
    print("=" * 60)


# [AI-MODIFIED] 周复盘系统 - argparse 入口，支持 --date 参数指定历史分析日期
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='A股市场状态分析（趋势/波动/风格/宽度/行业轮动）')
    parser.add_argument('date_pos', nargs='?', default=None,
                        help='分析日期 YYYYMMDD（位置参数，向后兼容）')
    parser.add_argument('--date', type=str, default=None,
                        help='分析日期 YYYYMMDD（默认最新交易日）')
    args = parser.parse_args()
    target = args.date or args.date_pos
    if target:
        print(f"\n[CLI] 使用指定日期: {target}")
    main(target)

"""
指标预处理模块

阶段0：PE指标合成、指标子集选择、分组、组内相关性检查。

将11个指标降维为7个（4个PE合成为1个综合PE，移除below_net_asset），
并按逃顶/抄底分别选择指标子集和分组。
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, List, Tuple, Optional

from config import PROCESSED_DATA_DIR


# ==================== 指标名称映射 ====================

# 所有可用指标及其数据文件名
INDICATOR_FILES = {
    "hs300_equity_premium": "equity_premium_沪深300.csv",
    "hs300_pe_valuation": "pe_valuation_沪深300_滚动市盈率.csv",
    "sz50_pe_valuation": "pe_valuation_上证50_滚动市盈率.csv",
    "zz500_pe_valuation": "pe_valuation_中证500_滚动市盈率.csv",
    "zz1000_pe_valuation": "pe_valuation_中证1000_滚动市盈率.csv",
    "price_percentile": "price_percentile.csv",
    "market_turnover_percentile": "market_turnover_percentile.csv",
    "ma250_bias": "ma250_bias.csv",
    "market_crowdedness": "market_crowdedness.csv",
    "below_net_asset": "below_net_asset_全部A股.csv",
    "herding_rate": "herding_rate.csv",
}

# PE指标列表（用于合成）
PE_INDICATORS = [
    "hs300_pe_valuation",
    "sz50_pe_valuation",
    "zz500_pe_valuation",
    "zz1000_pe_valuation",
]

# ==================== 双分数指标分组 ====================

# 逃顶风险分：3组（V5简化）
# V5消融实验: 背离预警组(market_crowdedness_vel AUC=0.550, herding_rate_vel AUC=0.499)
#   移除后AUC仅-0.007, 两个指标接近随机噪音, 已删除
TOP_SCORE_GROUPS = {
    "泡沫估值组": ["pe_composite", "hs300_equity_premium",
                    "hs300_equity_premium_vel"],
    "资金过热组": ["market_crowdedness", "herding_rate"],
    "量能过热组": ["market_turnover_percentile"],
}

TOP_INVERTED_INDICATORS: set = set()  # V5: 背离组已移除，无需反转

# 抄底机会分：2组（V5简化）
# V5消融实验: 3个高d值velocity + 2个结构性base指标最优
#   比V4原版(9指标,3组) AUC高0.075, 前瞻60日收益+0.24%, 最大回撤更小
#   移除的弱指标: pe_composite(AUC=0.350), herding_rate(AUC=0.524),
#     market_turnover_percentile(AUC=0.548), price_percentile_vel(AUC=0.640, 与ma250_bias_vel r=0.93 冗余)
BOTTOM_SCORE_GROUPS = {
    "价值动量组": ["pe_composite_vel", "hs300_equity_premium_vel",
                    "below_net_asset_vel"],
    "技术结构组": ["ma250_bias", "market_crowdedness"],
}


def load_indicator_data(
    data_dir: Optional[Path] = None,
) -> Dict[str, pd.Series]:
    """加载所有指标的 risk_percentage 时间序列

    Args:
        data_dir: 处理后数据目录，默认使用 config.PROCESSED_DATA_DIR

    Returns:
        Dict[str, pd.Series]: 指标名称 -> risk_percentage 时间序列，
                              索引为 datetime 类型的交易日期
    """
    data_dir = data_dir or PROCESSED_DATA_DIR
    indicator_data = {}

    for name, filename in INDICATOR_FILES.items():
        filepath = data_dir / filename
        if not filepath.exists():
            print(f"⚠ 指标数据文件不存在，跳过: {filepath}")
            continue

        df = pd.read_csv(filepath, encoding="utf-8-sig")

        # 统一日期列名（不同指标文件使用不同的列名）
        date_col = None
        for col_name in ["交易日期", "日期", "date", "目标日期"]:
            if col_name in df.columns:
                date_col = col_name
                break

        if date_col is None:
            print(f"⚠ 指标 {name} 缺少日期列，跳过")
            continue

        # 统一值列名：优先用 risk_percentage，其次用 percentile
        # 注意：部分指标的 percentile 含义不同:
        #   - risk_percentage: 0=低风险, 100=高风险（正向）
        #   - avg_percentile/percentile: 大多数也是正向
        #   - below_net_asset: risk_percentage 已翻转（破净率高=低风险）
        value_col = None
        if "risk_percentage" in df.columns:
            value_col = "risk_percentage"
        elif "avg_percentile" in df.columns:
            value_col = "avg_percentile"
        elif "percentile" in df.columns:
            value_col = "percentile"

        if value_col is None:
            print(f"⚠ 指标 {name} 缺少风险百分比/分位数列，跳过")
            continue

        df[date_col] = pd.to_datetime(df[date_col])
        series = df.set_index(date_col)[value_col].sort_index()

        # 自动检测并修正0-1尺度 → 0-100尺度
        # 所有指标应统一为0-100的risk_percentage尺度
        # 检测条件: 最大值<=1.0且最小值>=0，说明数据在0-1范围
        if series.max() <= 1.0 and series.min() >= 0.0:
            print(f"  ⚠ {name}: 检测到0-1尺度，自动转换为0-100")
            series = series * 100
        series.name = name
        indicator_data[name] = series

    return indicator_data


def composite_pe(
    indicator_data: Dict[str, pd.Series],
) -> pd.Series:
    """将4个PE指标等权合成为综合PE估值指标

    Args:
        indicator_data: 所有指标的 risk_percentage 时间序列

    Returns:
        pd.Series: 综合PE估值的 risk_percentage 时间序列
    """
    pe_series_list = []
    for name in PE_INDICATORS:
        if name in indicator_data:
            pe_series_list.append(indicator_data[name])
        else:
            print(f"⚠ PE指标 {name} 不可用，跳过")

    if not pe_series_list:
        raise ValueError("没有可用的PE指标数据，无法合成综合PE")

    # 对齐日期并取等权平均
    pe_df = pd.concat(pe_series_list, axis=1)
    pe_composite = pe_df.mean(axis=1)
    pe_composite.name = "pe_composite"

    print(f"✓ 综合PE估值合成完成: {len(pe_series_list)}个PE指标, "
          f"{len(pe_composite)}个交易日")

    return pe_composite


def build_indicator_matrix(
    indicator_data: Dict[str, pd.Series],
    pe_composite_series: pd.Series,
) -> pd.DataFrame:
    """构建完整的指标矩阵（合成PE替代4个独立PE）

    Args:
        indicator_data: 原始指标数据
        pe_composite_series: 综合PE时间序列

    Returns:
        pd.DataFrame: 指标矩阵，列为指标名，行为交易日期
    """
    # 构建非PE指标列表
    non_pe_indicators = {
        name: series
        for name, series in indicator_data.items()
        if name not in PE_INDICATORS
    }

    # 合并所有指标（去除重复日期，保留最后一条）
    all_series = {}
    for name, series in non_pe_indicators.items():
        s = series[~series.index.duplicated(keep="last")]
        all_series[name] = s
    all_series["pe_composite"] = pe_composite_series[
        ~pe_composite_series.index.duplicated(keep="last")
    ]
    matrix = pd.DataFrame(all_series)

    # 按日期排序，去除全NaN行
    matrix = matrix.sort_index()
    matrix = matrix.dropna(how="all")

    print(f"✓ 指标矩阵构建完成: {matrix.shape[1]}个指标, "
          f"{matrix.shape[0]}个交易日")

    return matrix


def select_top_indicators(
    matrix: pd.DataFrame,
) -> pd.DataFrame:
    """选择逃顶风险分使用的指标子集

    逃顶使用: 综合PE、股权溢价、拥挤度、抱团率、破净率

    Returns:
        pd.DataFrame: 逃顶指标子集
    """
    top_indicators = []
    for group_indicators in TOP_SCORE_GROUPS.values():
        top_indicators.extend(group_indicators)

    available = [col for col in top_indicators if col in matrix.columns]
    missing = [col for col in top_indicators if col not in matrix.columns]

    if missing:
        print(f"⚠ 逃顶指标缺失: {missing}")

    return matrix[available].copy()


def select_bottom_indicators(
    matrix: pd.DataFrame,
) -> pd.DataFrame:
    """选择抄底机会分使用的指标子集

    抄底使用: 综合PE、MA250偏离、换手率、拥挤度、抱团率

    Returns:
        pd.DataFrame: 抄底指标子集
    """
    bottom_indicators = []
    for group_indicators in BOTTOM_SCORE_GROUPS.values():
        bottom_indicators.extend(group_indicators)

    available = [col for col in bottom_indicators if col in matrix.columns]
    missing = [col for col in bottom_indicators if col not in matrix.columns]

    if missing:
        print(f"⚠ 抄底指标缺失: {missing}")

    return matrix[available].copy()


def group_indicators(
    indicator_subset: pd.DataFrame,
    groups: Dict[str, List[str]],
    inverted_indicators: Optional[set] = None,
) -> pd.DataFrame:
    """将指标按组聚合（组内等权平均）

    Args:
        indicator_subset: 指标子集 DataFrame
        groups: 分组定义，如 TOP_SCORE_GROUPS
        inverted_indicators: 需要反转的指标集合（100-x），用于反向信号

    Returns:
        pd.DataFrame: 组级指标矩阵，每列为一组的等权平均值
    """
    inverted_indicators = inverted_indicators or set()
    group_series = {}

    for group_name, indicator_names in groups.items():
        available = [name for name in indicator_names if name in indicator_subset.columns]
        if not available:
            print(f"⚠ 组 '{group_name}' 无可用指标")
            continue

        # 组内等权平均（NaN感知：只用当日有数据的指标）
        group_data = indicator_subset[available].copy()

        # 反转指定指标: 100 - percentile
        for col in available:
            if col in inverted_indicators:
                group_data[col] = 100.0 - group_data[col]

        group_mean = group_data.mean(axis=1, skipna=True)

        # 组内所有指标都是NaN的日期，结果也是NaN
        all_nan_mask = group_data.isna().all(axis=1)
        group_mean[all_nan_mask] = np.nan

        group_series[group_name] = group_mean

    result = pd.DataFrame(group_series)
    print(f"✓ 指标分组完成: {len(result.columns)}组")

    return result


# ==================== 速度特征 ====================

VELOCITY_WINDOW = 20  # 速度特征回看窗口（交易日）
ACCELERATION_FAST_SPAN = 3   # 加速度快EMA窗口
ACCELERATION_SLOW_SPAN = 10  # 加速度慢EMA窗口


def compute_velocity_features(
    matrix: pd.DataFrame,
    window: int = VELOCITY_WINDOW,
) -> pd.DataFrame:
    """计算指标速度特征（20日变化量）

    速度 = 当前百分位 - N日前百分位
    然后用 expanding percentile rank 归一化到 0-100。

    注意：只使用过去数据，无前视偏差。

    Args:
        matrix: 指标矩阵（列为指标，值为 risk_percentage 0-100）
        window: 回看窗口天数

    Returns:
        pd.DataFrame: 速度特征矩阵，列名为 "{原指标名}_vel"
    """
    # 计算原始速度（当前值 - N日前值）
    raw_velocity = matrix - matrix.shift(window)

    # Expanding percentile rank 归一化到 0-100
    velocity_features = pd.DataFrame(index=matrix.index)
    for col in raw_velocity.columns:
        series = raw_velocity[col].dropna()
        if len(series) < window * 2:
            continue
        # expanding rank: 当前值在历史所有值中的百分位
        ranked = series.expanding(min_periods=window).rank(pct=True) * 100
        velocity_features[f"{col}_vel"] = ranked

    print(f"✓ 速度特征计算完成: {len(velocity_features.columns)}个特征, "
          f"窗口={window}天")

    return velocity_features


def compute_acceleration_features(
    matrix: pd.DataFrame,
    velocity_window: int = VELOCITY_WINDOW,
    fast_span: int = ACCELERATION_FAST_SPAN,
    slow_span: int = ACCELERATION_SLOW_SPAN,
) -> pd.DataFrame:
    """计算指标加速度特征（速度的变化率）

    加速度 = EMA(velocity, fast) - EMA(velocity, slow)
    先平滑再差分，减少瞬时冲击噪声。
    最后用 expanding percentile rank 归一化到 0-100。

    加速度峰值通常领先于指标水平峰值，提供更早的预警信号。

    注意：只使用过去数据，无前视偏差。

    Args:
        matrix: 指标矩阵（列为指标，值为 risk_percentage 0-100）
        velocity_window: 速度回看窗口
        fast_span: 快速EMA窗口
        slow_span: 慢速EMA窗口

    Returns:
        pd.DataFrame: 加速度特征矩阵，列名为 "{原指标名}_acc"
    """
    # 先计算原始速度
    raw_velocity = matrix - matrix.shift(velocity_window)

    acceleration_features = pd.DataFrame(index=matrix.index)
    min_periods = velocity_window + slow_span

    for col in raw_velocity.columns:
        vel_series = raw_velocity[col].dropna()
        if len(vel_series) < min_periods * 2:
            continue

        # 平滑后差分: EMA(fast) - EMA(slow)
        ema_fast = vel_series.ewm(span=fast_span, min_periods=fast_span).mean()
        ema_slow = vel_series.ewm(span=slow_span, min_periods=slow_span).mean()
        raw_acc = ema_fast - ema_slow

        # Expanding percentile rank 归一化到 0-100
        valid_acc = raw_acc.dropna()
        if len(valid_acc) < min_periods:
            continue
        ranked = valid_acc.expanding(min_periods=min_periods).rank(pct=True) * 100
        acceleration_features[f"{col}_acc"] = ranked

    print(f"✓ 加速度特征计算完成: {len(acceleration_features.columns)}个特征, "
          f"EMA({fast_span},{slow_span})")

    return acceleration_features


def check_group_correlation(
    matrix: pd.DataFrame,
    threshold: float = 0.85,
) -> pd.DataFrame:
    """检查指标间的相关性，识别高相关指标对

    Args:
        matrix: 指标矩阵
        threshold: 相关性警告阈值

    Returns:
        pd.DataFrame: 相关系数矩阵
    """
    # 去除NaN后计算相关系数
    corr_matrix = matrix.dropna().corr(method="pearson")

    # 识别高相关对
    high_corr_pairs = []
    cols = corr_matrix.columns
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            r = corr_matrix.iloc[i, j]
            if abs(r) > threshold:
                high_corr_pairs.append((cols[i], cols[j], r))

    if high_corr_pairs:
        print(f"⚠ 发现 {len(high_corr_pairs)} 对高相关指标 (|r| > {threshold}):")
        for name1, name2, r in high_corr_pairs:
            print(f"    {name1} <-> {name2}: r = {r:.3f}")
    else:
        print(f"✓ 未发现高相关指标对 (阈值 |r| > {threshold})")

    return corr_matrix


def preprocess_all(
    data_dir: Optional[Path] = None,
) -> Dict:
    """执行完整的指标预处理流程

    Returns:
        Dict: 包含以下键:
            - indicator_data: 原始指标数据
            - pe_composite: 综合PE时间序列
            - full_matrix: 完整指标矩阵（8个指标）
            - top_matrix: 逃顶指标子集
            - bottom_matrix: 抄底指标子集
            - top_grouped: 逃顶组级矩阵（3组）
            - bottom_grouped: 抄底组级矩阵（3组）
            - correlation_matrix: 相关系数矩阵
    """
    print("=" * 60)
    print("阶段0: 指标预处理")
    print("=" * 60)

    # 1. 加载所有指标数据
    indicator_data = load_indicator_data(data_dir)
    print(f"✓ 加载了 {len(indicator_data)} 个指标")

    # 2. PE合成
    pe_composite_series = composite_pe(indicator_data)

    # 3. 构建完整矩阵
    full_matrix = build_indicator_matrix(indicator_data, pe_composite_series)

    # 3.5 计算速度特征并合并到完整矩阵
    velocity_matrix = compute_velocity_features(full_matrix)

    # V5: 加速度特征已移除（消融实验证明对逃顶/抄底均无贡献，不在任何组中使用）

    if not velocity_matrix.empty:
        full_matrix = pd.concat([full_matrix, velocity_matrix], axis=1)
        print(f"✓ 合并速度特征后矩阵: {full_matrix.shape[1]}个指标, "
              f"{full_matrix.shape[0]}个交易日")

    # 4. 相关性检查
    corr_matrix = check_group_correlation(full_matrix)

    # 5. 逃顶/抄底指标选择
    top_matrix = select_top_indicators(full_matrix)
    bottom_matrix = select_bottom_indicators(full_matrix)

    # 6. 分组聚合（逃顶传入反转指标集）
    top_grouped = group_indicators(
        top_matrix, TOP_SCORE_GROUPS,
        inverted_indicators=TOP_INVERTED_INDICATORS,
    )
    bottom_grouped = group_indicators(bottom_matrix, BOTTOM_SCORE_GROUPS)

    print()
    print(f"逃顶指标: {list(top_matrix.columns)} -> {list(top_grouped.columns)}")
    print(f"抄底指标: {list(bottom_matrix.columns)} -> {list(bottom_grouped.columns)}")

    # 诊断: 每个指标的最早可用日期
    print("\n指标最早可用日期:")
    for col in full_matrix.columns:
        first_valid = full_matrix[col].first_valid_index()
        if first_valid is not None:
            print(f"  {col}: {first_valid.date()}")
        else:
            print(f"  {col}: 无数据")

    return {
        "indicator_data": indicator_data,
        "pe_composite": pe_composite_series,
        "full_matrix": full_matrix,
        "top_matrix": top_matrix,
        "bottom_matrix": bottom_matrix,
        "top_grouped": top_grouped,
        "bottom_grouped": bottom_grouped,
        "correlation_matrix": corr_matrix,
    }

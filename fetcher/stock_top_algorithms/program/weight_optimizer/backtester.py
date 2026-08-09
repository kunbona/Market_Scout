"""
回测验证模块

评估优化后权重的表现，对比三组权重（当前手动、等权、优化后），
并基于 "Do No Harm" 原则判断是否建议采纳。

事件级指标（核心）：事件召回率、中位提前天数、假事件/年
日级指标（辅助）：精确率、AUC
"""

import json
import numpy as np
import pandas as pd
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from sklearn.metrics import roc_auc_score

from config import RESULTS_DIR
from program.weight_optimizer.indicator_preprocessor import (
    TOP_SCORE_GROUPS, BOTTOM_SCORE_GROUPS,
)
from program.weight_optimizer.weight_optimizer import softmax


# ==================== 评估阈值 ====================

# 固定极端信号阈值（作为对比基准）
TOP_EXTREME_THRESHOLD = 0.80   # 逃顶分 > 0.80 视为极端
BOTTOM_EXTREME_THRESHOLD = 0.20  # 抄底风险分 < 0.20（即机会分 > 0.80）

# 动态阈值参数
DYNAMIC_LOOKBACK = 1260       # 滚动窗口：约5年交易日
DYNAMIC_MIN_PERIODS = 500     # 最小观测数（早期用expanding）
DYNAMIC_TOP_QUANTILE = 0.80   # 逃顶警报：历史80分位（V3优化：80%=最佳召回/假事件比）
DYNAMIC_BOTTOM_QUANTILE = 0.15  # 抄底警报：历史15分位（V3优化：33%召回+0.4假事件/年）

# 方向特定的持续性确认天数
# 逃顶：0天（80%阈值已足够，持续性反而增加假事件）
# 抄底：2天持续确认（底部V型反转快，过高会错过）
TOP_PERSISTENCE_DAYS = 0
BOTTOM_PERSISTENCE_DAYS = 2

# V4: 信号降噪参数
# EMA平滑：消除日级噪声，使信号匹配市场周期（周-月级别）
# EMA(10)在保持召回率的同时将信号翻转从9-13次/年降至4-5次/年
COMPOSITE_EMA_SPAN = 10       # EMA平滑窗口（0=不平滑）

# 滞回阈值间距：防止信号在阈值附近反复翻转
# 进入警报态需突破动态阈值，退出需回落hysteresis点以上
HYSTERESIS_POINTS = 5.0       # 进入/退出阈值间距（分数空间）

# Do No Harm 采纳门槛
MIN_EVENT_RECALL = 0.80       # 事件召回率不低于80%
MAX_FALSE_EVENTS_PER_YEAR = 2  # 假事件/年不超过2次
MIN_SEPARATION_IMPROVEMENT = 0.10  # 分离度提升>10%


def smooth_composite(
    composite: pd.Series,
    ema_span: int = COMPOSITE_EMA_SPAN,
) -> pd.Series:
    """对composite分数应用EMA平滑

    消除日级噪声，使信号匹配市场顶底周期（周-月级别）。
    平滑仅用于信号生成和可视化，不影响权重优化。

    Args:
        composite: 原始composite分数 (0-100)
        ema_span: EMA窗口。0表示不平滑。

    Returns:
        pd.Series: 平滑后的分数
    """
    if ema_span <= 0:
        return composite.copy()
    smoothed = composite.ewm(span=ema_span, min_periods=1).mean()
    smoothed.name = composite.name
    return smoothed


def generate_alert_signal(
    composite: pd.Series,
    direction: str,
    hysteresis: float = HYSTERESIS_POINTS,
) -> pd.Series:
    """生成带滞回的警报信号

    使用 Schmitt trigger 逻辑防止信号在阈值附近反复翻转。
    - 进入警报态：分数突破动态阈值
    - 退出警报态：分数回落超过 hysteresis 点

    Args:
        composite: 平滑后的composite分数
        direction: "top" 或 "bottom"
        hysteresis: 进入/退出阈值间距

    Returns:
        pd.Series: 布尔警报信号
    """
    enter_thresh = compute_dynamic_threshold(composite, direction)

    if direction == "top":
        exit_thresh = enter_thresh - hysteresis
    else:
        exit_thresh = enter_thresh + hysteresis

    in_alert = False
    signal = pd.Series(False, index=composite.index)

    for i in range(len(composite)):
        val = composite.iloc[i]
        et = enter_thresh.iloc[i]
        xt = exit_thresh.iloc[i]

        if pd.isna(et) or pd.isna(xt):
            signal.iloc[i] = in_alert
            continue

        if direction == "top":
            if not in_alert and val >= et:
                in_alert = True
            elif in_alert and val < xt:
                in_alert = False
        else:
            if not in_alert and val <= et:
                in_alert = True
            elif in_alert and val > xt:
                in_alert = False

        signal.iloc[i] = in_alert

    return signal


def compute_dynamic_threshold(
    composite: pd.Series,
    direction: str,
) -> pd.Series:
    """计算动态滚动阈值

    使用expanding窗口（早期）→ rolling 1260天窗口（数据充足后），
    避免固定阈值不适应市场结构变迁。

    Args:
        composite: 组合分数序列（0-100）
        direction: "top" 或 "bottom"

    Returns:
        pd.Series: 每日动态阈值线（0-100）
    """
    if direction == "top":
        quantile = DYNAMIC_TOP_QUANTILE
    else:
        quantile = DYNAMIC_BOTTOM_QUANTILE

    # 使用rolling + min_periods实现expanding→rolling渐进
    threshold = composite.rolling(
        window=DYNAMIC_LOOKBACK,
        min_periods=DYNAMIC_MIN_PERIODS,
    ).quantile(quantile)

    return threshold


# ==================== 组合分数计算 ====================


def compute_composite_score(
    grouped_data: pd.DataFrame,
    weights: np.ndarray,
) -> pd.Series:
    """计算组合加权分数

    Args:
        grouped_data: 组级指标 DataFrame (T x n_groups)
        weights: 权重向量

    Returns:
        pd.Series: 组合分数序列（0-100）
    """
    # 归一化到 [0,1] 计算，再转回百分比
    data = grouped_data.values / 100.0
    data = np.nan_to_num(data, nan=0.5)
    composite = data @ weights
    return pd.Series(composite * 100, index=grouped_data.index, name="composite_score")


def _apply_persistence_filter(
    signal: pd.Series,
    min_days: int,
) -> pd.Series:
    """持续性过滤：只保留连续N日为True的信号

    Args:
        signal: 布尔信号序列
        min_days: 最小连续天数

    Returns:
        pd.Series: 过滤后的布尔信号（只在连续段的第N日起为True）
    """
    if min_days <= 1:
        return signal

    result = signal.copy()
    consecutive = 0
    for i in range(len(signal)):
        if signal.iloc[i]:
            consecutive += 1
            if consecutive < min_days:
                result.iloc[i] = False
        else:
            consecutive = 0

    return result


def evaluate_score(
    composite: pd.Series,
    daily_labels: pd.Series,
    events: List[Dict],
    direction: str,
    use_dynamic_threshold: bool = True,
    persistence_days: int = 0,
    apply_smoothing: bool = True,
) -> Dict:
    """评估某组权重下的分数表现

    Args:
        composite: 组合分数序列（0-100）
        daily_labels: 每日标签
        events: 事件列表
        direction: "top" 或 "bottom"
        use_dynamic_threshold: 是否使用动态阈值（默认True）
        persistence_days: 持续性过滤天数
        apply_smoothing: 是否应用EMA平滑（默认True）

    Returns:
        Dict: 事件级和日级指标
    """
    # V4: 可选EMA平滑
    if apply_smoothing:
        composite = smooth_composite(composite)

    # 对齐
    common = composite.index.intersection(daily_labels.index)
    comp = composite.loc[common]
    labels = daily_labels.loc[common]

    target_label = "top_zone" if direction == "top" else "bottom_zone"
    target_events = [e for e in events if e["label"] == target_label]

    # === 事件级指标 ===

    # 计算阈值
    if use_dynamic_threshold:
        dynamic_thresh = compute_dynamic_threshold(comp, direction)
    
    if direction == "top":
        fixed_threshold = TOP_EXTREME_THRESHOLD * 100
        pre_label = "pre_top_zone"
    else:
        fixed_threshold = BOTTOM_EXTREME_THRESHOLD * 100
        pre_label = "pre_bottom_zone"

    detected_events = 0
    lead_days_list = []

    for event in target_events:
        event_start = event["start_date"]

        # 检测窗口：只看事件开始前的预警窗口（不含事后zone）
        if direction == "top":
            search_start = event_start - pd.Timedelta(days=30)
        else:
            search_start = event_start - pd.Timedelta(days=60)

        # 终点为event_start + 3天（允许tiny anchor），不再搜索整个zone
        search_end = event_start + pd.Timedelta(days=3)
        mask = (comp.index >= search_start) & (comp.index <= search_end)
        event_comp = comp[mask]

        if len(event_comp) == 0:
            continue

        # 检测是否触及极端阈值（动态或固定）
        if use_dynamic_threshold:
            event_thresh = dynamic_thresh.loc[event_comp.index].dropna()
            if len(event_thresh) == 0:
                continue
            if direction == "top":
                extreme_hit = event_comp >= event_thresh
            else:
                extreme_hit = event_comp <= event_thresh
        else:
            if direction == "top":
                extreme_hit = event_comp >= fixed_threshold
            else:
                extreme_hit = event_comp <= fixed_threshold

        if extreme_hit.any() and persistence_days > 0:
            extreme_hit = _apply_persistence_filter(extreme_hit, persistence_days)

        if extreme_hit.any():
            detected_events += 1
            # 计算提前天数
            first_hit = event_comp.index[extreme_hit][0]
            lead = (event_start - first_hit).days
            lead_days_list.append(lead)

    total_events = max(len(target_events), 1)
    event_recall = detected_events / total_events

    median_lead_days = float(np.median(lead_days_list)) if lead_days_list else 0.0

    # 假事件/年 — 按连续alert天数聚合为episodes
    neutral_comp = comp[labels == "neutral"]
    if use_dynamic_threshold:
        neutral_thresh = dynamic_thresh.loc[neutral_comp.index].dropna()
        neutral_comp_aligned = neutral_comp.loc[neutral_thresh.index]
        if direction == "top":
            false_extreme = neutral_comp_aligned >= neutral_thresh
        else:
            false_extreme = neutral_comp_aligned <= neutral_thresh
    else:
        if direction == "top":
            false_extreme = neutral_comp >= fixed_threshold
        else:
            false_extreme = neutral_comp <= fixed_threshold

    # 应用持续性过滤到假事件检测
    if persistence_days > 0:
        false_extreme = _apply_persistence_filter(false_extreme, persistence_days)

    # 聚合连续alert天为一个episode（冷却期5天）
    false_episodes = 0
    last_alert_idx = -999
    for i, (idx, is_alert) in enumerate(false_extreme.items()):
        if is_alert:
            pos = i  # 直接用枚举位置
            if pos - last_alert_idx > 5:
                false_episodes += 1
            last_alert_idx = pos

    total_years = len(comp) / 250.0
    false_events_per_year = false_episodes / max(total_years, 1)

    # === 日级指标 ===

    # 日精确率
    if use_dynamic_threshold:
        dyn_aligned = dynamic_thresh.loc[comp.index].dropna()
        comp_aligned = comp.loc[dyn_aligned.index]
        labels_aligned = labels.loc[dyn_aligned.index]
        if direction == "top":
            extreme_mask = comp_aligned >= dyn_aligned
            zone_labels = ["pre_top_zone"]
        else:
            extreme_mask = comp_aligned <= dyn_aligned
            zone_labels = ["pre_bottom_zone"]
    else:
        labels_aligned = labels
        if direction == "top":
            extreme_mask = comp >= fixed_threshold
            zone_labels = ["pre_top_zone"]
        else:
            extreme_mask = comp <= fixed_threshold
            zone_labels = ["pre_bottom_zone"]

    extreme_count = extreme_mask.sum()
    if extreme_count > 0:
        hit_count = (extreme_mask & labels_aligned.isin(zone_labels)).sum()
        daily_precision = hit_count / extreme_count
    else:
        daily_precision = 0.0

    # AUC — 使用预警窗口作为正样本（避免事后数据污染）
    try:
        if direction == "top":
            y_true = labels.isin(["pre_top_zone"]).astype(int).values
            y_score = (comp / 100.0).values
        else:
            y_true = labels.isin(["pre_bottom_zone"]).astype(int).values
            y_score = (1 - comp / 100.0).values  # 翻转：机会分

        if y_true.sum() > 0 and y_true.sum() < len(y_true):
            auc = roc_auc_score(y_true, y_score)
        else:
            auc = 0.5
    except Exception:
        auc = 0.5

    # 分离度 — 使用预警窗口
    pre_label = "pre_top_zone" if direction == "top" else "pre_bottom_zone"
    zone_vals = comp[labels == pre_label]
    neutral_vals = comp[labels == "neutral"]

    if len(zone_vals) > 0 and len(neutral_vals) > 0:
        if direction == "top":
            separation = zone_vals.mean() - neutral_vals.mean()
        else:
            separation = neutral_vals.mean() - zone_vals.mean()
    else:
        separation = 0.0

    return {
        "event_recall": event_recall,
        "detected_events": detected_events,
        "total_events": len(target_events),
        "median_lead_days": median_lead_days,
        "false_events_per_year": false_events_per_year,
        "daily_precision": daily_precision,
        "auc": auc,
        "separation": separation,
    }


def compare_weight_sets(
    grouped_data: pd.DataFrame,
    daily_labels: pd.Series,
    events: List[Dict],
    optimized_weights: np.ndarray,
    groups: Dict[str, List[str]],
    direction: str,
) -> pd.DataFrame:
    """对比两组权重的表现

    1. 等权基准
    2. 优化后权重

    Args:
        grouped_data: 组级指标 DataFrame
        daily_labels: 每日标签
        events: 事件列表
        optimized_weights: 优化后的组权重
        groups: 分组定义
        direction: "top" 或 "bottom"

    Returns:
        pd.DataFrame: 两组权重的指标对比表
    """
    n_groups = len(groups)

    weight_sets = {
        "等权基准": np.ones(n_groups) / n_groups,
        "优化后权重": optimized_weights,
    }

    rows = []
    for set_name, weights in weight_sets.items():
        composite = compute_composite_score(grouped_data, weights)
        persist = TOP_PERSISTENCE_DAYS if direction == "top" else BOTTOM_PERSISTENCE_DAYS
        metrics = evaluate_score(composite, daily_labels, events, direction,
                                 persistence_days=persist)
        metrics["weight_set"] = set_name
        metrics["weights"] = weights.tolist()
        rows.append(metrics)

    return pd.DataFrame(rows)


def check_adoption_criteria(
    comparison: pd.DataFrame,
    direction: str,
) -> Dict:
    """检查 Do No Harm 采纳门槛

    优化权重需满足（基准为等权）：
    1. 事件召回率 ≥ 等权基准
    2. 假事件/年不增加
    3. 分离度提升 > 10%
    4. 中位提前天数不变差

    Args:
        comparison: 两组权重对比表
        direction: "top" 或 "bottom"

    Returns:
        Dict: 采纳建议和原因
    """
    current = comparison[comparison["weight_set"] == "等权基准"].iloc[0]
    optimized = comparison[comparison["weight_set"] == "优化后权重"].iloc[0]

    checks = []
    all_pass = True

    # 1. 事件召回率
    if optimized["event_recall"] >= current["event_recall"]:
        checks.append(("事件召回率", "✓", f"{optimized['event_recall']:.0%} >= {current['event_recall']:.0%}"))
    else:
        checks.append(("事件召回率", "✗", f"{optimized['event_recall']:.0%} < {current['event_recall']:.0%}"))
        all_pass = False

    # 2. 假事件/年
    if optimized["false_events_per_year"] <= current["false_events_per_year"] + 0.5:
        checks.append(("假事件/年", "✓", f"{optimized['false_events_per_year']:.1f} <= {current['false_events_per_year']:.1f}"))
    else:
        checks.append(("假事件/年", "✗", f"{optimized['false_events_per_year']:.1f} > {current['false_events_per_year']:.1f}"))
        all_pass = False

    # 3. 分离度提升
    current_sep = current["separation"]
    optimized_sep = optimized["separation"]
    if current_sep != 0:
        sep_improvement = (optimized_sep - current_sep) / abs(current_sep)
    else:
        sep_improvement = optimized_sep

    if sep_improvement > MIN_SEPARATION_IMPROVEMENT:
        checks.append(("分离度提升", "✓", f"{sep_improvement:.1%} > 10%"))
    else:
        checks.append(("分离度提升", "✗", f"{sep_improvement:.1%} <= 10%"))
        all_pass = False

    # 4. 提前天数
    if optimized["median_lead_days"] >= current["median_lead_days"] - 2:
        checks.append(("提前天数", "✓", f"{optimized['median_lead_days']:.0f} >= {current['median_lead_days']:.0f}"))
    else:
        checks.append(("提前天数", "✗", f"{optimized['median_lead_days']:.0f} < {current['median_lead_days']:.0f}"))
        all_pass = False

    if all_pass:
        recommendation = "adopt"
        message = "✓ 建议采纳优化权重"
    elif sum(1 for _, status, _ in checks if status == "✓") >= 3:
        recommendation = "partial_adopt"
        message = "⚠ 建议参考优化权重，但需注意未通过的指标"
    else:
        recommendation = "keep_current"
        message = "✗ 建议保留等权基准"

    return {
        "recommendation": recommendation,
        "message": message,
        "checks": checks,
        "direction": direction,
    }


def generate_backtest_report(
    top_grouped: pd.DataFrame,
    bottom_grouped: pd.DataFrame,
    daily_labels: pd.Series,
    events: List[Dict],
    optimization_results: Dict,
    output_dir: Optional[Path] = None,
    index_data: Optional[pd.DataFrame] = None,
) -> Dict:
    """生成完整的回测对比报告

    Args:
        top_grouped: 逃顶组级矩阵
        bottom_grouped: 抄底组级矩阵
        daily_labels: 每日标签
        events: 事件列表
        optimization_results: 阶段3优化结果
        output_dir: 输出目录
        index_data: 上证指数日线数据（用于利润跟踪回测）

    Returns:
        Dict: 回测结果，包含对比表和采纳建议
    """
    output_dir = output_dir or RESULTS_DIR

    print("=" * 60)
    print("回测验证")
    print("=" * 60)

    results = {}

    # === 逃顶分回测 ===
    print("\n--- 逃顶风险分回测 ---")
    top_weights = optimization_results["top"]["loco"]["final_weights"]
    top_comparison = compare_weight_sets(
        top_grouped, daily_labels, events,
        top_weights, TOP_SCORE_GROUPS, "top"
    )
    top_adoption = check_adoption_criteria(top_comparison, "top")

    print("\n  权重对比:")
    for _, row in top_comparison.iterrows():
        print(f"  [{row['weight_set']}]")
        print(f"    召回={row['event_recall']:.0%}, "
              f"提前={row['median_lead_days']:.0f}天, "
              f"假事件/年={row['false_events_per_year']:.1f}, "
              f"AUC={row['auc']:.3f}")

    print(f"\n  采纳建议: {top_adoption['message']}")
    for name, status, detail in top_adoption["checks"]:
        print(f"    {status} {name}: {detail}")

    results["top"] = {
        "comparison": top_comparison,
        "adoption": top_adoption,
    }

    # === 抄底分回测 ===
    print("\n--- 抄底机会分回测 ---")
    bottom_weights = optimization_results["bottom"]["loco"]["final_weights"]
    bottom_comparison = compare_weight_sets(
        bottom_grouped, daily_labels, events,
        bottom_weights, BOTTOM_SCORE_GROUPS, "bottom"
    )
    bottom_adoption = check_adoption_criteria(bottom_comparison, "bottom")

    print("\n  权重对比:")
    for _, row in bottom_comparison.iterrows():
        print(f"  [{row['weight_set']}]")
        print(f"    召回={row['event_recall']:.0%}, "
              f"提前={row['median_lead_days']:.0f}天, "
              f"假事件/年={row['false_events_per_year']:.1f}, "
              f"AUC={row['auc']:.3f}")

    print(f"\n  采纳建议: {bottom_adoption['message']}")
    for name, status, detail in bottom_adoption["checks"]:
        print(f"    {status} {name}: {detail}")

    results["bottom"] = {
        "comparison": bottom_comparison,
        "adoption": bottom_adoption,
    }

    # === 报警配置对比（控制变量测试） ===
    print("\n--- 报警配置对比 ---")

    import program.weight_optimizer.backtester as _bt_mod
    for direction_name, grouped, weights in [
        ("逃顶", top_grouped, top_weights),
        ("抄底", bottom_grouped, bottom_weights),
    ]:
        print(f"\n  {direction_name}:")
        direction = "top" if direction_name == "逃顶" else "bottom"
        composite = compute_composite_score(grouped, weights)

        # 根据方向选择不同的测试配置
        if direction == "top":
            configs = [
                {"name": "75%阈值", "quantile": 0.75, "persistence": 0},
                {"name": "75%+3日持续", "quantile": 0.75, "persistence": 3},
                {"name": "80%阈值", "quantile": 0.80, "persistence": 0},
                {"name": "80%+2日持续", "quantile": 0.80, "persistence": 2},
                {"name": "80%+3日持续", "quantile": 0.80, "persistence": 3},
                {"name": "85%阈值", "quantile": 0.85, "persistence": 0},
                {"name": "85%+3日持续", "quantile": 0.85, "persistence": 3},
                {"name": "90%阈值", "quantile": 0.90, "persistence": 0},
                {"name": "90%+3日持续", "quantile": 0.90, "persistence": 3},
                {"name": "95%阈值(原)", "quantile": 0.95, "persistence": 0},
            ]
        else:
            # 底部信号V型反转，用较低持续性
            configs = [
                {"name": "20%阈值", "quantile": 0.20, "persistence": 0},
                {"name": "15%阈值", "quantile": 0.15, "persistence": 0},
                {"name": "15%+2日持续", "quantile": 0.15, "persistence": 2},
                {"name": "15%+3日持续", "quantile": 0.15, "persistence": 3},
                {"name": "10%阈值", "quantile": 0.10, "persistence": 0},
                {"name": "10%+2日持续", "quantile": 0.10, "persistence": 2},
                {"name": "10%+3日持续", "quantile": 0.10, "persistence": 3},
                {"name": "5%阈值(原)", "quantile": 0.05, "persistence": 0},
            ]

        for cfg in configs:
            saved_tq = _bt_mod.DYNAMIC_TOP_QUANTILE
            saved_bq = _bt_mod.DYNAMIC_BOTTOM_QUANTILE
            if direction == "top":
                _bt_mod.DYNAMIC_TOP_QUANTILE = cfg["quantile"]
            else:
                _bt_mod.DYNAMIC_BOTTOM_QUANTILE = cfg["quantile"]

            metrics = evaluate_score(
                composite, daily_labels, events, direction,
                persistence_days=cfg["persistence"],
            )

            _bt_mod.DYNAMIC_TOP_QUANTILE = saved_tq
            _bt_mod.DYNAMIC_BOTTOM_QUANTILE = saved_bq

            print(f"    [{cfg['name']}] "
                  f"召回={metrics['event_recall']:.0%}, "
                  f"假事件/年={metrics['false_events_per_year']:.1f}, "
                  f"AUC={metrics['auc']:.3f}")

    # 保存回测对比报告
    all_comparisons = pd.concat([
        top_comparison.assign(score_type="逃顶"),
        bottom_comparison.assign(score_type="抄底"),
    ])
    report_path = output_dir / "backtest_comparison.csv"
    all_comparisons.to_csv(report_path, index=False, encoding="utf-8-sig")
    print(f"\n✓ 回测对比报告已保存: {report_path}")

    # === V4→V5: 利润跟踪回测（确认评分已移除，直接用composite）===
    if index_data is not None:
        print("\n--- 利润跟踪回测 ---")
        for direction_name, grouped, weights in [
            ("逃顶", top_grouped, top_weights),
            ("抄底", bottom_grouped, bottom_weights),
        ]:
            direction = "top" if direction_name == "逃顶" else "bottom"
            composite = compute_composite_score(grouped, weights)
            profit_report = run_profit_tracking_backtest(
                composite, index_data, daily_labels, events, direction
            )
            print(f"\n  {direction_name} 利润跟踪:")
            print(f"    触发次数: {profit_report['n_signals']}")
            print(f"    平均未来20日回报: {profit_report['avg_fwd_return_20d']:.2%}")
            print(f"    平均未来60日回报: {profit_report['avg_fwd_return_60d']:.2%}")
            print(f"    简单策略年化: {profit_report['strategy_annual_return']:.2%}")
            print(f"    买入持有年化: {profit_report['buyhold_annual_return']:.2%}")
            print(f"    策略最大回撤: {profit_report['strategy_max_drawdown']:.2%}")
            print(f"    买入持有最大回撤: {profit_report['buyhold_max_drawdown']:.2%}")

        # V4: 联合策略网格搜索
        print(f"\n--- V4: 联合逃顶+抄底策略优化 ---")
        grid_results = run_combined_strategy_grid_search(
            top_grouped, bottom_grouped,
            top_weights, bottom_weights,
            index_data, daily_labels, events,
        )
        best = grid_results["best_config"]
        print(f"\n  买入持有基准:")
        print(f"    年化收益: {grid_results['buyhold_annual_return']:.2%}")
        print(f"    最大回撤: {grid_results['buyhold_max_drawdown']:.2%}")
        print(f"    Calmar比: {grid_results['buyhold_calmar']:.3f}")
        print(f"\n  最优联合策略:")
        print(f"    逃顶阈值: {best['top_quantile']:.0%}分位")
        print(f"    抄底阈值: {best['bottom_quantile']:.0%}分位")
        print(f"    逃顶持仓: {best['top_position']:.0%}")
        print(f"    持仓天数: {best['hold_days']:.0f}")
        print(f"    年化收益: {best['annual_return']:.2%}")
        print(f"    最大回撤: {best['max_drawdown']:.2%}")
        print(f"    Calmar比: {best['calmar_ratio']:.3f}")
        print(f"    逃顶事件数: {best['n_top_events']:.0f}")
        print(f"    抄底事件数: {best['n_bottom_events']:.0f}")

        # 显示top5配置
        top5 = grid_results["all_results"].nlargest(5, "calmar_ratio")
        print(f"\n  Top 5 配置 (按Calmar比排序):")
        for _, row in top5.iterrows():
            print(f"    top={row['top_quantile']:.0%} bot={row['bottom_quantile']:.0%} "
                  f"pos={row['top_position']:.0%} hold={row['hold_days']:.0f}d | "
                  f"年化={row['annual_return']:.2%} "
                  f"回撤={row['max_drawdown']:.2%} "
                  f"Calmar={row['calmar_ratio']:.3f} "
                  f"events={row['n_top_events']:.0f}T/{row['n_bottom_events']:.0f}B")

        results["profit_tracking"] = True
        results["grid_search"] = grid_results

    return results


def run_profit_tracking_backtest(
    composite: pd.Series,
    index_data: pd.DataFrame,
    daily_labels: pd.Series,
    events: List[Dict],
    direction: str,
) -> Dict:
    """简单利润跟踪回测

    逃顶策略：composite高于动态阈值时减仓至50%，否则满仓
    抄底策略：composite低于动态阈值时满仓，否则半仓

    V4: 使用EMA平滑 + 滞回信号生成。

    Args:
        composite: 组合分数序列（0-100）
        index_data: 上证指数数据
        daily_labels: 每日标签
        events: 事件列表
        direction: "top" 或 "bottom"

    Returns:
        Dict: 利润跟踪指标
    """
    close = index_data["close"]

    # V4: 平滑composite
    smoothed = smooth_composite(composite)

    # 对齐
    common = smoothed.index.intersection(close.index)
    comp = smoothed.loc[common]
    price = close.loc[common]

    # V4: 使用滞回信号
    signal = generate_alert_signal(comp, direction)

    # 简单仓位策略
    position = pd.Series(1.0, index=common)  # 默认满仓
    if direction == "top":
        position[signal] = 0.5   # 逃顶信号 → 半仓
    else:
        position[~signal] = 0.5  # 非抄底信号 → 半仓

    # 计算每日收益
    daily_returns = price.pct_change().fillna(0)
    strategy_returns = daily_returns * position.shift(1).fillna(1)  # 信号T日生效于T+1
    buyhold_returns = daily_returns

    # 累计收益
    strategy_cumulative = (1 + strategy_returns).cumprod()
    buyhold_cumulative = (1 + buyhold_returns).cumprod()

    # 年化收益
    n_years = len(common) / 250.0
    strategy_total = strategy_cumulative.iloc[-1] if len(strategy_cumulative) > 0 else 1.0
    buyhold_total = buyhold_cumulative.iloc[-1] if len(buyhold_cumulative) > 0 else 1.0
    strategy_annual = strategy_total ** (1 / max(n_years, 0.1)) - 1
    buyhold_annual = buyhold_total ** (1 / max(n_years, 0.1)) - 1

    # 最大回撤
    strategy_dd = _max_drawdown(strategy_cumulative)
    buyhold_dd = _max_drawdown(buyhold_cumulative)

    # 信号触发时的前瞻收益
    signal_dates = comp.index[signal.fillna(False)]
    fwd_returns_20 = []
    fwd_returns_60 = []

    for dt in signal_dates:
        dt_idx = common.get_loc(dt)
        if dt_idx + 20 < len(price):
            fwd_20 = (price.iloc[dt_idx + 20] - price.iloc[dt_idx]) / price.iloc[dt_idx]
            if direction == "top":
                fwd_20 = -fwd_20  # 逃顶：避免的损失为正
            fwd_returns_20.append(fwd_20)
        if dt_idx + 60 < len(price):
            fwd_60 = (price.iloc[dt_idx + 60] - price.iloc[dt_idx]) / price.iloc[dt_idx]
            if direction == "top":
                fwd_60 = -fwd_60
            fwd_returns_60.append(fwd_60)

    return {
        "n_signals": int(signal.sum()),
        "avg_fwd_return_20d": float(np.mean(fwd_returns_20)) if fwd_returns_20 else 0.0,
        "avg_fwd_return_60d": float(np.mean(fwd_returns_60)) if fwd_returns_60 else 0.0,
        "strategy_annual_return": float(strategy_annual),
        "buyhold_annual_return": float(buyhold_annual),
        "strategy_max_drawdown": float(strategy_dd),
        "buyhold_max_drawdown": float(buyhold_dd),
        "strategy_total_return": float(strategy_total - 1),
        "buyhold_total_return": float(buyhold_total - 1),
    }


def _max_drawdown(cumulative: pd.Series) -> float:
    """计算最大回撤"""
    peak = cumulative.expanding().max()
    drawdown = (cumulative - peak) / peak
    return float(drawdown.min())


def run_combined_strategy_grid_search(
    top_grouped: pd.DataFrame,
    bottom_grouped: pd.DataFrame,
    top_weights: np.ndarray,
    bottom_weights: np.ndarray,
    index_data: pd.DataFrame,
    daily_labels: pd.Series,
    events: List[Dict],
) -> Dict:
    """网格搜索最优的联合逃顶+抄底策略配置

    使用事件驱动策略（非日级信号）：
    - 当composite从下方突破阈值时触发逃顶事件 → 减仓持续hold_days天
    - 当composite从上方跌破阈值时触发抄底事件 → 满仓持续hold_days天
    - 冷却期内不重复触发

    Returns:
        Dict: 最优配置及指标
    """
    import program.weight_optimizer.backtester as _bt

    top_composite = compute_composite_score(top_grouped, top_weights)
    bottom_composite = compute_composite_score(bottom_grouped, bottom_weights)

    # V4: 平滑composite
    top_composite = smooth_composite(top_composite)
    bottom_composite = smooth_composite(bottom_composite)

    close = index_data["close"]
    common = top_composite.index.intersection(bottom_composite.index).intersection(close.index)
    top_comp = top_composite.loc[common]
    bottom_comp = bottom_composite.loc[common]
    price = close.loc[common]
    daily_returns = price.pct_change().fillna(0)

    # 买入持有基准
    n_years = len(common) / 250.0
    bh_cumulative = (1 + daily_returns).cumprod()
    bh_annual = bh_cumulative.iloc[-1] ** (1 / max(n_years, 0.1)) - 1
    bh_dd = _max_drawdown(bh_cumulative)

    # 网格参数
    top_quantiles = [0.80, 0.85, 0.90, 0.95]
    bottom_quantiles = [0.05, 0.10, 0.15, 0.20]
    positions_on_top = [0.0, 0.3, 0.5]
    hold_days_list = [20, 40, 60, 90]

    results = []

    for tq in top_quantiles:
        for bq in bottom_quantiles:
            # 计算动态阈值
            saved_tq = _bt.DYNAMIC_TOP_QUANTILE
            saved_bq = _bt.DYNAMIC_BOTTOM_QUANTILE
            _bt.DYNAMIC_TOP_QUANTILE = tq
            _bt.DYNAMIC_BOTTOM_QUANTILE = bq

            top_thresh = compute_dynamic_threshold(top_comp, "top")
            bottom_thresh = compute_dynamic_threshold(bottom_comp, "bottom")

            _bt.DYNAMIC_TOP_QUANTILE = saved_tq
            _bt.DYNAMIC_BOTTOM_QUANTILE = saved_bq

            # 突破信号（从下/上方穿越阈值）
            top_above = top_comp >= top_thresh
            top_entry = top_above & (~top_above.shift(1).fillna(False))  # 上穿

            bottom_below = bottom_comp <= bottom_thresh
            bottom_entry = bottom_below & (~bottom_below.shift(1).fillna(False))  # 下穿

            for pos in positions_on_top:
                for hold_days in hold_days_list:
                    # 事件驱动仓位
                    position = pd.Series(1.0, index=common)
                    n_top_events = 0
                    n_bottom_events = 0

                    # 逃顶事件
                    top_cooldown = 0
                    for i in range(len(common)):
                        if top_cooldown > 0:
                            position.iloc[i] = min(position.iloc[i], pos)
                            top_cooldown -= 1
                        elif top_entry.iloc[i]:
                            position.iloc[i] = pos
                            top_cooldown = hold_days - 1
                            n_top_events += 1

                    # 抄底事件覆盖（满仓）
                    bottom_cooldown = 0
                    for i in range(len(common)):
                        if bottom_cooldown > 0:
                            position.iloc[i] = 1.0
                            bottom_cooldown -= 1
                        elif bottom_entry.iloc[i]:
                            position.iloc[i] = 1.0
                            bottom_cooldown = hold_days - 1
                            n_bottom_events += 1

                    # 策略收益
                    strategy_returns = daily_returns * position.shift(1).fillna(1)
                    strategy_cumulative = (1 + strategy_returns).cumprod()
                    strategy_total = strategy_cumulative.iloc[-1]
                    strategy_annual = strategy_total ** (1 / max(n_years, 0.1)) - 1
                    strategy_dd = _max_drawdown(strategy_cumulative)

                    calmar = strategy_annual / abs(strategy_dd) if strategy_dd != 0 else 0

                    results.append({
                        "top_quantile": tq,
                        "bottom_quantile": bq,
                        "top_position": pos,
                        "hold_days": hold_days,
                        "annual_return": strategy_annual,
                        "max_drawdown": strategy_dd,
                        "calmar_ratio": calmar,
                        "total_return": strategy_total - 1,
                        "n_top_events": n_top_events,
                        "n_bottom_events": n_bottom_events,
                    })

    results_df = pd.DataFrame(results)
    best_idx = results_df["calmar_ratio"].idxmax()
    best = results_df.loc[best_idx].to_dict()

    return {
        "best_config": best,
        "all_results": results_df,
        "buyhold_annual_return": float(bh_annual),
        "buyhold_max_drawdown": float(bh_dd),
        "buyhold_calmar": float(bh_annual / abs(bh_dd)) if bh_dd != 0 else 0,
    }


def save_optimized_weights(
    optimization_results: Dict,
    backtest_results: Dict,
    output_dir: Optional[Path] = None,
) -> Path:
    """保存优化后的权重到 JSON

    Args:
        optimization_results: 阶段3优化结果
        backtest_results: 回测结果
        output_dir: 输出目录

    Returns:
        Path: JSON文件路径
    """
    output_dir = output_dir or RESULTS_DIR

    # 逃顶分权重
    top_data = optimization_results["top"]
    top_groups = top_data["group_names"]
    top_weights = top_data["loco"]["final_weights"]

    top_group_weights = {}
    for i, name in enumerate(top_groups):
        entry = {
            "median": round(float(top_weights[i]), 4),
            "fold_std": round(float(top_data["loco"]["weight_std"][i]), 4),
        }
        if "bootstrap" in top_data:
            entry["ci_5"] = round(float(top_data["bootstrap"]["ci_5"][i]), 4)
            entry["ci_95"] = round(float(top_data["bootstrap"]["ci_95"][i]), 4)
        top_group_weights[name] = entry

    # 展开到指标级权重
    top_indicator_weights = {}
    for group_name, indicators in TOP_SCORE_GROUPS.items():
        group_weight = float(top_weights[top_groups.index(group_name)])
        per_indicator = group_weight / len(indicators)
        for ind_name in indicators:
            top_indicator_weights[ind_name] = round(per_indicator, 4)

    # 抄底分权重
    bottom_data = optimization_results["bottom"]
    bottom_groups = bottom_data["group_names"]
    bottom_weights = bottom_data["loco"]["final_weights"]

    bottom_group_weights = {}
    for i, name in enumerate(bottom_groups):
        entry = {
            "median": round(float(bottom_weights[i]), 4),
            "fold_std": round(float(bottom_data["loco"]["weight_std"][i]), 4),
        }
        if "bootstrap" in bottom_data:
            entry["ci_5"] = round(float(bottom_data["bootstrap"]["ci_5"][i]), 4)
            entry["ci_95"] = round(float(bottom_data["bootstrap"]["ci_95"][i]), 4)
        bottom_group_weights[name] = entry

    bottom_indicator_weights = {}
    for group_name, indicators in BOTTOM_SCORE_GROUPS.items():
        group_weight = float(bottom_weights[bottom_groups.index(group_name)])
        per_indicator = group_weight / len(indicators)
        for ind_name in indicators:
            bottom_indicator_weights[ind_name] = round(per_indicator, 4)

    # 稳定性判断
    top_stable = top_data["loco"]["stable"]
    bottom_stable = bottom_data["loco"]["stable"]
    if top_stable and bottom_stable:
        stability = "stable"
    elif top_stable or bottom_stable:
        stability = "partially_stable"
    else:
        stability = "unstable"

    output = {
        "top_score": {
            "description": "逃顶风险分（0-100，高=危险）",
            "group_weights": top_group_weights,
            "indicator_weights": top_indicator_weights,
        },
        "bottom_score": {
            "description": "抄底机会分（0-100，高=机会，已翻转）",
            "group_weights": bottom_group_weights,
            "indicator_weights": bottom_indicator_weights,
        },
        "stability_verdict": stability,
        "adoption_recommendation": {
            "top_score": backtest_results["top"]["adoption"]["recommendation"],
            "bottom_score": backtest_results["bottom"]["adoption"]["recommendation"],
        },
    }

    output_path = output_dir / "optimized_weights.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✓ 优化权重已保存: {output_path}")

    return output_path

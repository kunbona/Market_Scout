"""
权重优化模块

阶段3：基于事件级排序损失的权重优化。

特性：
- Softmax参数化确保权重sum=1且非负
- 先验惩罚（贝叶斯收缩），限制偏离诊断先验±30%
- 事件间尺度对齐，确保阈值跨周期可比
- 差分进化优化（无梯度，适合非凸损失）
- LOCO交叉验证 + Bootstrap置信区间
"""

import numpy as np
import pandas as pd
from scipy.optimize import differential_evolution
from typing import Dict, List, Tuple, Optional
import warnings


# ==================== 超参数 ====================

# 损失函数权重（spec 推荐值）
ALPHA = 0.5     # 排序损失权重
BETA = 0.3      # 分离度权重
GAMMA = 0.2     # 事件间尺度对齐权重
LAMBDA_PRIOR = 0.5  # 先验惩罚强度
LAMBDA_L2 = 0.05    # L2正则化
LAMBDA_ENTROPY = 0.02  # 熵正则化（鼓励分散）
MARGIN = 0.1    # 排序损失 margin

# 优化器参数
DE_MAXITER = 200
DE_POPSIZE = 30
DE_TOL = 1e-6
DE_SEED = 42

# Bootstrap参数
BOOTSTRAP_ITERATIONS = 200
BOOTSTRAP_SEED = 42

# (PRE_ZONE_DISCOUNT removed — pre_mask is now the sole scoring window)


def softmax(logits: np.ndarray) -> np.ndarray:
    """Softmax转换：无约束logits -> 归一化权重

    Args:
        logits: 无约束参数向量

    Returns:
        权重向量，sum=1, 各元素≥0
    """
    # 数值稳定性
    shifted = logits - np.max(logits)
    exp_vals = np.exp(shifted)
    return exp_vals / exp_vals.sum()


def _compute_event_score(
    composite: np.ndarray,
    event: Dict,
) -> float:
    """计算单个事件的综合得分（仅使用预警窗口）

    只使用 pre_mask（转折前窗口）作为正样本，避免事后数据污染。
    confidence_weight 不再乘入分数，而是在损失函数层面使用。

    Args:
        composite: 复合指标序列
        event: 事件字典（含 zone_mask, pre_mask）

    Returns:
        事件原始得分（不含置信度缩放）
    """
    pre_mask = event["pre_mask"]
    zone_mask = event["zone_mask"]

    pre_vals = composite[pre_mask]

    if len(pre_vals) >= 3:
        # 主路径：仅用预警窗口（转折前）
        return float(np.mean(pre_vals))

    # 回退：pre_mask为空或太短，用zone前3天（转折点附近）
    zone_indices = np.where(zone_mask)[0]
    if len(zone_indices) == 0:
        return 0.0
    anchor_indices = zone_indices[:3]  # 最多取zone前3天
    anchor_vals = composite[anchor_indices]
    return float(np.mean(anchor_vals))


def top_ranking_loss(
    logits: np.ndarray,
    grouped_data: np.ndarray,
    top_events: List[Dict],
    neutral_mask: np.ndarray,
    prior_weights: np.ndarray,
) -> float:
    """逃顶分事件级排序损失

    目标：逃顶风险分在顶部事件期间尽可能高，中性期间尽可能低。

    Args:
        logits: 无约束参数（len=n_groups）
        grouped_data: 组级指标矩阵 (T x n_groups)，值域[0,1]
        top_events: 顶部事件列表
        neutral_mask: 中性区域布尔掩码
        prior_weights: 先验权重

    Returns:
        总损失值（越小越好）
    """
    weights = softmax(logits)
    composite = grouped_data @ weights  # (T,)

    # 1. 事件得分（含置信度权重，用于加权损失）
    event_scores = []
    event_conf_weights = []
    for event in top_events:
        score = _compute_event_score(composite, event)
        if score > 0:
            event_scores.append(score)
            event_conf_weights.append(event["confidence_weight"])

    if len(event_scores) == 0:
        return 1e6

    neutral_score = np.mean(composite[neutral_mask]) if neutral_mask.any() else 0.5

    # 2. 排序损失：每个top事件得分应 > neutral + margin（置信度加权）
    ranking_loss = sum(
        w * max(0, MARGIN - (ts - neutral_score))
        for ts, w in zip(event_scores, event_conf_weights)
    )

    # 3. 分离度奖励
    separation = np.mean(event_scores) - neutral_score
    separation_loss = -separation

    # 4. 事件间尺度对齐
    if len(event_scores) >= 2:
        calibration_loss = np.var(event_scores)
    else:
        calibration_loss = 0.0

    # 5. 先验惩罚
    prior_penalty = LAMBDA_PRIOR * np.sum((weights - prior_weights) ** 2)

    # 6. 正则化
    entropy = -np.sum(weights * np.log(weights + 1e-8))
    reg_loss = LAMBDA_L2 * np.sum(weights ** 2) - LAMBDA_ENTROPY * entropy

    total = (
        ALPHA * ranking_loss
        + BETA * separation_loss
        + GAMMA * calibration_loss
        + prior_penalty
        + reg_loss
    )

    return total


def bottom_ranking_loss(
    logits: np.ndarray,
    grouped_data: np.ndarray,
    bottom_events: List[Dict],
    neutral_mask: np.ndarray,
    prior_weights: np.ndarray,
) -> float:
    """抄底分事件级排序损失

    目标：加权风险分在底部事件期间尽可能低，中性期间尽可能高。
    （翻转后：抄底机会分 = 100 - 风险分，底部时高）

    Args:
        logits: 无约束参数
        grouped_data: 组级指标矩阵
        bottom_events: 底部事件列表
        neutral_mask: 中性区域布尔掩码
        prior_weights: 先验权重

    Returns:
        总损失值
    """
    weights = softmax(logits)
    composite = grouped_data @ weights  # (T,)

    # 底部事件得分（风险分应该低）+ 置信度权重
    event_scores = []
    event_conf_weights = []
    for event in bottom_events:
        score = _compute_event_score(composite, event)
        event_scores.append(score)
        event_conf_weights.append(event["confidence_weight"])

    if len(event_scores) == 0:
        return 1e6

    neutral_score = np.mean(composite[neutral_mask]) if neutral_mask.any() else 0.5

    # 排序损失：neutral > bottom（底部风险分低 = 好）（置信度加权）
    ranking_loss = sum(
        w * max(0, MARGIN - (neutral_score - bs))
        for bs, w in zip(event_scores, event_conf_weights)
    )

    # 分离度：中性与底部的差距越大越好
    separation = neutral_score - np.mean(event_scores)
    separation_loss = -separation

    # 事件间尺度对齐
    if len(event_scores) >= 2:
        calibration_loss = np.var(event_scores)
    else:
        calibration_loss = 0.0

    # 先验惩罚 + 正则化
    prior_penalty = LAMBDA_PRIOR * np.sum((weights - prior_weights) ** 2)
    entropy = -np.sum(weights * np.log(weights + 1e-8))
    reg_loss = LAMBDA_L2 * np.sum(weights ** 2) - LAMBDA_ENTROPY * entropy

    total = (
        ALPHA * ranking_loss
        + BETA * separation_loss
        + GAMMA * calibration_loss
        + prior_penalty
        + reg_loss
    )

    return total


def _prepare_optimization_data(
    grouped_data: pd.DataFrame,
    daily_labels: pd.Series,
    events: List[Dict],
    direction: str,
    index_data: Optional[pd.DataFrame] = None,
) -> Tuple[np.ndarray, List[Dict], np.ndarray]:
    """准备优化所需的数据

    对齐日期、归一化到[0,1]、构建掩码。
    V4: 增加事件严重性权重（基于后续回撤/反弹幅度）。

    Args:
        grouped_data: 组级指标 DataFrame
        daily_labels: 每日标签
        events: 事件列表
        direction: "top" 或 "bottom"
        index_data: 上证指数数据（用于计算严重性权重）

    Returns:
        Tuple: (grouped_array, target_events, neutral_mask)
    """
    # 对齐日期
    common_dates = grouped_data.index.intersection(daily_labels.index)
    data = grouped_data.loc[common_dates].copy()
    labels = daily_labels.loc[common_dates]

    # 归一化到 [0, 1]
    data_array = data.values / 100.0

    # 填充NaN为0.5（中性值）
    data_array = np.nan_to_num(data_array, nan=0.5)

    # 构建中性掩码
    neutral_mask = (labels == "neutral").values

    # 过滤目标事件（只保留在数据范围内且有指标覆盖的）
    target_label = "top_zone" if direction == "top" else "bottom_zone"
    target_events = []

    for event in events:
        if event["label"] != target_label:
            continue

        # 过滤：事件必须在数据范围内
        if event["start_date"] < common_dates[0] or event["start_date"] > common_dates[-1]:
            continue

        # 重新构建掩码（对齐后的索引）
        zone_mask = np.zeros(len(common_dates), dtype=bool)
        pre_mask = np.zeros(len(common_dates), dtype=bool)

        for i, dt in enumerate(common_dates):
            if dt in daily_labels.index:
                orig_idx = daily_labels.index.get_loc(dt)
                if isinstance(orig_idx, int) or isinstance(orig_idx, np.integer):
                    if orig_idx < len(event["zone_mask"]) and event["zone_mask"][orig_idx]:
                        zone_mask[i] = True
                    if orig_idx < len(event["pre_mask"]) and event["pre_mask"][orig_idx]:
                        pre_mask[i] = True

        if zone_mask.sum() == 0:
            continue

        # 2010-2014期间指标覆盖不完整，降低权重
        conf_weight = event["confidence_weight"]
        if event["start_date"] < pd.Timestamp("2014-01-01"):
            conf_weight *= 0.7

        # V5: 严重性加权已移除（消融实验仅+0.002 AUC，不值得增加复杂度）

        target_events.append({
            "start_date": event["start_date"],
            "end_date": event["end_date"],
            "label": event["label"],
            "confidence": event["confidence"],
            "confidence_weight": conf_weight,
            "zone_mask": zone_mask,
            "pre_mask": pre_mask,
        })

    return data_array, target_events, neutral_mask


def optimize_weights(
    grouped_data: pd.DataFrame,
    daily_labels: pd.Series,
    events: List[Dict],
    prior_weights: np.ndarray,
    direction: str,
    seed: int = DE_SEED,
    index_data: Optional[pd.DataFrame] = None,
) -> Dict:
    """执行权重优化

    使用差分进化优化组间权重。

    Args:
        grouped_data: 组级指标 DataFrame
        daily_labels: 每日标签
        events: 事件列表
        prior_weights: 先验权重
        direction: "top" 或 "bottom"
        seed: 随机种子
        index_data: 上证指数数据（保留接口兼容性）

    Returns:
        Dict: 优化结果（权重、损失、详细信息）
    """
    n_groups = grouped_data.shape[1]
    data_array, target_events, neutral_mask = _prepare_optimization_data(
        grouped_data, daily_labels, events, direction, index_data
    )

    if len(target_events) == 0:
        warnings.warn(f"无可用的{direction}事件，使用先验权重")
        return {
            "weights": prior_weights,
            "logits": np.zeros(n_groups),
            "loss": float("inf"),
            "n_events": 0,
            "note": "无可用事件",
        }

    # 损失函数
    if direction == "top":
        loss_fn = lambda logits: top_ranking_loss(
            logits, data_array, target_events, neutral_mask, prior_weights
        )
    else:
        loss_fn = lambda logits: bottom_ranking_loss(
            logits, data_array, target_events, neutral_mask, prior_weights
        )

    # 搜索范围（logits无约束，但限制范围防止数值问题）
    bounds = [(-3.0, 3.0)] * n_groups

    result = differential_evolution(
        loss_fn,
        bounds=bounds,
        maxiter=DE_MAXITER,
        popsize=DE_POPSIZE,
        tol=DE_TOL,
        seed=seed,
        polish=True,
    )

    optimized_logits = result.x
    optimized_weights = softmax(optimized_logits)

    return {
        "weights": optimized_weights,
        "logits": optimized_logits,
        "loss": result.fun,
        "n_events": len(target_events),
        "success": result.success,
        "message": result.message,
    }


def cross_validate_loco(
    grouped_data: pd.DataFrame,
    daily_labels: pd.Series,
    events: List[Dict],
    prior_weights: np.ndarray,
    direction: str,
    index_data: Optional[pd.DataFrame] = None,
) -> Dict:
    """Leave-One-Cycle-Out 交叉验证

    对每个事件逐一留出，用剩余事件训练，评估稳定性。

    Args:
        grouped_data: 组级指标 DataFrame
        daily_labels: 每日标签
        events: 事件列表
        prior_weights: 先验权重
        direction: "top" 或 "bottom"
        index_data: 上证指数数据（传递给optimize_weights）

    Returns:
        Dict: 各fold权重、最终权重（中位数）、稳定性指标
    """
    target_label = "top_zone" if direction == "top" else "bottom_zone"
    target_events = [e for e in events if e["label"] == target_label]

    n_events = len(target_events)
    if n_events < 2:
        warnings.warn(f"事件数({n_events})不足，跳过LOCO")
        return {
            "final_weights": prior_weights,
            "fold_weights": [],
            "weight_std": np.zeros(len(prior_weights)),
            "stable": False,
            "note": "事件不足",
        }

    fold_weights = []
    fold_losses = []

    for i in range(n_events):
        # 留出第i个事件
        train_events = [e for j, e in enumerate(events) if
                        not (e["label"] == target_label and
                             e["start_date"] == target_events[i]["start_date"])]

        result = optimize_weights(
            grouped_data, daily_labels, train_events,
            prior_weights, direction, seed=DE_SEED + i,
            index_data=index_data
        )

        fold_weights.append(result["weights"])
        fold_losses.append(result["loss"])

    fold_weights_arr = np.array(fold_weights)

    # 最终权重：中位数聚合
    final_weights = np.median(fold_weights_arr, axis=0)
    # 重新归一化
    final_weights = final_weights / final_weights.sum()

    # 稳定性检查
    weight_std = np.std(fold_weights_arr, axis=0)
    max_std = np.max(weight_std)
    stable = max_std < 0.10

    if not stable:
        warnings.warn(f"⚠ LOCO权重不稳定: max_std={max_std:.3f}")

    return {
        "final_weights": final_weights,
        "fold_weights": fold_weights_arr,
        "fold_losses": fold_losses,
        "weight_std": weight_std,
        "max_std": max_std,
        "stable": stable,
    }


def bootstrap_weight_intervals(
    grouped_data: pd.DataFrame,
    daily_labels: pd.Series,
    events: List[Dict],
    prior_weights: np.ndarray,
    direction: str,
    n_iterations: int = BOOTSTRAP_ITERATIONS,
    seed: int = BOOTSTRAP_SEED,
    index_data: Optional[pd.DataFrame] = None,
) -> Dict:
    """Bootstrap权重置信区间

    按事件块有放回抽样，生成权重分布。

    Args:
        grouped_data: 组级指标 DataFrame
        daily_labels: 每日标签
        events: 事件列表
        prior_weights: 先验权重
        direction: "top" 或 "bottom"
        n_iterations: Bootstrap迭代次数
        seed: 随机种子
        index_data: 上证指数数据

    Returns:
        Dict: 权重分布统计（中位数、5-95%置信区间）
    """
    target_label = "top_zone" if direction == "top" else "bottom_zone"
    target_events = [e for e in events if e["label"] == target_label]
    other_events = [e for e in events if e["label"] != target_label]

    n_events = len(target_events)
    if n_events < 2:
        return {
            "median_weights": prior_weights,
            "ci_5": prior_weights,
            "ci_95": prior_weights,
            "all_weights": np.array([prior_weights]),
            "note": "事件不足，使用先验权重",
        }

    rng = np.random.RandomState(seed)
    all_weights = []

    for _ in range(n_iterations):
        # 按事件块有放回抽样
        bootstrap_indices = rng.choice(n_events, size=n_events, replace=True)
        bootstrap_events = [target_events[i] for i in bootstrap_indices] + other_events

        result = optimize_weights(
            grouped_data, daily_labels, bootstrap_events,
            prior_weights, direction, seed=rng.randint(0, 100000),
            index_data=index_data
        )

        all_weights.append(result["weights"])

    all_weights_arr = np.array(all_weights)

    return {
        "median_weights": np.median(all_weights_arr, axis=0),
        "ci_5": np.percentile(all_weights_arr, 5, axis=0),
        "ci_95": np.percentile(all_weights_arr, 95, axis=0),
        "all_weights": all_weights_arr,
        "mean_weights": np.mean(all_weights_arr, axis=0),
        "std_weights": np.std(all_weights_arr, axis=0),
    }


def run_full_optimization(
    top_grouped: pd.DataFrame,
    bottom_grouped: pd.DataFrame,
    daily_labels: pd.Series,
    events: List[Dict],
    top_prior_weights: np.ndarray,
    bottom_prior_weights: np.ndarray,
    run_bootstrap: bool = True,
    index_data: Optional[pd.DataFrame] = None,
) -> Dict:
    """执行完整的权重优化流程

    对逃顶分和抄底分分别独立优化。
    V4: 传递index_data实现事件严重性加权。

    Args:
        top_grouped: 逃顶组级矩阵
        bottom_grouped: 抄底组级矩阵
        daily_labels: 每日标签
        events: 事件列表
        top_prior_weights: 逃顶先验权重
        bottom_prior_weights: 抄底先验权重
        run_bootstrap: 是否运行Bootstrap
        index_data: 上证指数数据（V4: 事件严重性加权）

    Returns:
        Dict: 完整优化结果
    """
    print("=" * 60)
    print("阶段3: 权重优化")
    print("=" * 60)

    results = {}

    # === 逃顶权重优化 ===
    print("\n--- 逃顶风险分优化 ---")
    top_opt = optimize_weights(
        top_grouped, daily_labels, events,
        top_prior_weights, "top", index_data=index_data
    )
    print(f"  优化权重: {dict(zip(top_grouped.columns, top_opt['weights']))}")
    print(f"  损失: {top_opt['loss']:.4f}, 事件数: {top_opt['n_events']}")

    # LOCO
    print("  执行LOCO交叉验证...")
    top_loco = cross_validate_loco(
        top_grouped, daily_labels, events,
        top_prior_weights, "top", index_data=index_data
    )
    print(f"  LOCO最终权重: {dict(zip(top_grouped.columns, top_loco['final_weights']))}")
    print(f"  权重标准差: {top_loco['weight_std']}, 稳定: {top_loco['stable']}")

    results["top"] = {
        "optimization": top_opt,
        "loco": top_loco,
        "prior_weights": top_prior_weights,
        "group_names": list(top_grouped.columns),
    }

    # Bootstrap（可选，耗时较长）
    if run_bootstrap:
        print("  执行Bootstrap权重估计...")
        top_bootstrap = bootstrap_weight_intervals(
            top_grouped, daily_labels, events,
            top_prior_weights, "top", index_data=index_data
        )
        results["top"]["bootstrap"] = top_bootstrap
        print(f"  Bootstrap中位数: {top_bootstrap['median_weights']}")
        print(f"  95%CI: [{top_bootstrap['ci_5']}, {top_bootstrap['ci_95']}]")

    # === 抄底权重优化 ===
    print("\n--- 抄底机会分优化 ---")
    bottom_opt = optimize_weights(
        bottom_grouped, daily_labels, events,
        bottom_prior_weights, "bottom", index_data=index_data
    )
    print(f"  优化权重: {dict(zip(bottom_grouped.columns, bottom_opt['weights']))}")
    print(f"  损失: {bottom_opt['loss']:.4f}, 事件数: {bottom_opt['n_events']}")

    # LOCO
    print("  执行LOCO交叉验证...")
    bottom_loco = cross_validate_loco(
        bottom_grouped, daily_labels, events,
        bottom_prior_weights, "bottom", index_data=index_data
    )
    print(f"  LOCO最终权重: {dict(zip(bottom_grouped.columns, bottom_loco['final_weights']))}")
    print(f"  权重标准差: {bottom_loco['weight_std']}, 稳定: {bottom_loco['stable']}")

    results["bottom"] = {
        "optimization": bottom_opt,
        "loco": bottom_loco,
        "prior_weights": bottom_prior_weights,
        "group_names": list(bottom_grouped.columns),
    }

    if run_bootstrap:
        print("  执行Bootstrap权重估计...")
        bottom_bootstrap = bootstrap_weight_intervals(
            bottom_grouped, daily_labels, events,
            bottom_prior_weights, "bottom", index_data=index_data
        )
        results["bottom"]["bootstrap"] = bottom_bootstrap
        print(f"  Bootstrap中位数: {bottom_bootstrap['median_weights']}")
        print(f"  95%CI: [{bottom_bootstrap['ci_5']}, {bottom_bootstrap['ci_95']}]")

    return results

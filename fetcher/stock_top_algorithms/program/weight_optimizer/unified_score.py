"""
统一牛熊指标模块

将独立的逃顶风险分和抄底机会分通过 sigmoid 连续证据融合，
输出 0-100 的统一市场温度：
  0-20:  极度低估（底部强证据）
  20-40: 偏低估
  40-60: 中性
  60-80: 偏高估
  80-100: 极度高估（顶部强证据）

设计原则：
1. 连续性 — sigmoid 保证阈值附近平滑过渡，不会跳变
2. 语义正确 — 只在接近/超越阈值时才产生方向性证据
3. 保留非对称优化 — top/bottom 各用自己的优化权重
4. 冲突可见 — bear/bull 同时活跃时标记为"市场分化"
5. 不额外 EMA — 输入已平滑，避免延迟叠加
"""

import numpy as np
import pandas as pd
from scipy.special import expit as sigmoid
from typing import Dict, Optional


# ==================== 默认参数 ====================

DEFAULT_TOP_BAND = 10.0     # 逃顶证据敏感度带宽
DEFAULT_BOTTOM_BAND = 8.0   # 抄底证据敏感度带宽
CONFLICT_THRESHOLD = 0.3    # bear/bull 同时 > 此值视为冲突
VELOCITY_WINDOW = 5         # 速度计算窗口（交易日）
VELOCITY_THRESHOLD = 2.0    # 速度阈值（用于升温/降温标注）


# ==================== 市场阶段定义 ====================

MARKET_PHASES = {
    "极度低估": {"emoji": "🟢", "range": (0, 20), "advice": "底部区域，积极建仓"},
    "偏多":     {"emoji": "🟢", "range": (20, 40), "advice": "安全区域，持有为主"},
    "中性":     {"emoji": "⚪", "range": (40, 60), "advice": "观望，按计划执行"},
    "偏空":     {"emoji": "🟠", "range": (60, 80), "advice": "开始减仓，提高警惕"},
    "极度高估": {"emoji": "🔴", "range": (80, 100), "advice": "顶部区域，果断减仓"},
}

# 扩展阶段（速度 + 冲突）
EXTENDED_PHASES = {
    **MARKET_PHASES,
    "升温中": {"emoji": "⬆️", "range": (40, 60), "advice": "注意风险积累"},
    "降温中": {"emoji": "⬇️", "range": (40, 60), "advice": "关注买入机会"},
    "冲突":   {"emoji": "⚠️", "range": None, "advice": "市场分化，谨慎观察"},
}


def compute_evidence(
    top_composite: pd.Series,
    bottom_composite: pd.Series,
    top_threshold: pd.Series,
    bottom_threshold: pd.Series,
    top_band: float = DEFAULT_TOP_BAND,
    bottom_band: float = DEFAULT_BOTTOM_BAND,
) -> pd.DataFrame:
    """提取方向性证据

    Args:
        top_composite: 逃顶风险分 (0-100)
        bottom_composite: 抄底机会分 (0-100, 低=买点)
        top_threshold: 逃顶动态阈值序列
        bottom_threshold: 抄底动态阈值序列
        top_band: 逃顶证据敏感度带宽
        bottom_band: 抄底证据敏感度带宽

    Returns:
        pd.DataFrame with columns: bear_evidence, bull_evidence, net_evidence
    """
    common = (
        top_composite.index
        .intersection(bottom_composite.index)
        .intersection(top_threshold.dropna().index)
        .intersection(bottom_threshold.dropna().index)
    )
    if len(common) == 0:
        raise ValueError("No overlapping dates between inputs (after dropping NaN thresholds)")

    top = top_composite.loc[common].values
    bottom = bottom_composite.loc[common].values
    top_thresh = top_threshold.loc[common].values
    bottom_thresh = bottom_threshold.loc[common].values

    bear = sigmoid((top - top_thresh) / top_band)
    bull = sigmoid((bottom_thresh - bottom) / bottom_band)
    net = bear - bull

    return pd.DataFrame({
        "bear_evidence": bear,
        "bull_evidence": bull,
        "net_evidence": net,
    }, index=common)


def compute_unified_score(
    top_composite: pd.Series,
    bottom_composite: pd.Series,
    top_threshold: pd.Series,
    bottom_threshold: pd.Series,
    top_band: float = DEFAULT_TOP_BAND,
    bottom_band: float = DEFAULT_BOTTOM_BAND,
) -> pd.Series:
    """计算统一牛熊指标 (0-100)

    0 = 极度看多, 50 = 中性, 100 = 极度看空

    Args:
        top_composite: 逃顶风险分 (0-100)
        bottom_composite: 抄底机会分 (0-100, 低=买点)
        top_threshold: 逃顶动态阈值序列
        bottom_threshold: 抄底动态阈值序列
        top_band: 逃顶证据敏感度带宽
        bottom_band: 抄底证据敏感度带宽

    Returns:
        pd.Series: 统一分数 (0-100)
    """
    evidence = compute_evidence(
        top_composite, bottom_composite,
        top_threshold, bottom_threshold,
        top_band, bottom_band,
    )
    unified = 50 + 50 * evidence["net_evidence"]
    return unified.clip(0, 100).rename("unified_score")


def classify_market_phase(
    unified_score: pd.Series,
    evidence: Optional[pd.DataFrame] = None,
    velocity_window: int = VELOCITY_WINDOW,
    velocity_threshold: float = VELOCITY_THRESHOLD,
    conflict_threshold: float = CONFLICT_THRESHOLD,
) -> pd.DataFrame:
    """为每个交易日分配市场阶段标签

    Args:
        unified_score: 统一分数序列 (0-100)
        evidence: compute_evidence 的输出（可选，用于冲突检测）
        velocity_window: 速度计算窗口
        velocity_threshold: 速度判定阈值
        conflict_threshold: 冲突判定阈值

    Returns:
        pd.DataFrame with columns:
            unified_score, phase, emoji, advice, velocity, is_conflicted
    """
    velocity = unified_score.diff(velocity_window) / velocity_window

    # 基础阶段判定
    score = unified_score
    phases = pd.Series("中性", index=score.index)
    phases[score < 20] = "极度低估"
    phases[(score >= 20) & (score < 40)] = "偏多"
    phases[(score >= 40) & (score < 60)] = "中性"
    phases[(score >= 60) & (score < 80)] = "偏空"
    phases[score >= 80] = "极度高估"

    # 冲突检测
    is_conflicted = pd.Series(False, index=score.index)
    if evidence is not None:
        aligned = evidence.reindex(score.index)
        is_conflicted = (
            (aligned["bear_evidence"] > conflict_threshold) &
            (aligned["bull_evidence"] > conflict_threshold)
        ).fillna(False)

    # 速度标注（仅中性区间且非冲突）
    neutral_mask = phases == "中性"
    warming = neutral_mask & ~is_conflicted & (velocity > velocity_threshold)
    cooling = neutral_mask & ~is_conflicted & (velocity < -velocity_threshold)
    phases[warming] = "升温中"
    phases[cooling] = "降温中"

    # 冲突覆盖
    phases[is_conflicted] = "冲突"

    # 构建结果
    result = pd.DataFrame({
        "unified_score": unified_score,
        "phase": phases,
        "velocity": velocity,
        "is_conflicted": is_conflicted,
    })

    result["emoji"] = result["phase"].map(
        lambda p: EXTENDED_PHASES.get(p, {}).get("emoji", ""))
    result["advice"] = result["phase"].map(
        lambda p: EXTENDED_PHASES.get(p, {}).get("advice", ""))

    return result


def compute_full_unified_report(
    top_composite: pd.Series,
    bottom_composite: pd.Series,
    top_threshold: pd.Series,
    bottom_threshold: pd.Series,
    top_band: float = DEFAULT_TOP_BAND,
    bottom_band: float = DEFAULT_BOTTOM_BAND,
) -> Dict:
    """计算完整的统一指标报告

    返回最新一天的全量诊断信息 + 完整时间序列（供可视化用）。

    Returns:
        Dict with keys:
            date, top_score, bottom_score, unified_score, unified_label,
            bear_evidence, bull_evidence, net_evidence, is_conflicted,
            velocity, phase, emoji, advice,
            unified_series, evidence_df, phases_df
    """
    evidence = compute_evidence(
        top_composite, bottom_composite,
        top_threshold, bottom_threshold,
        top_band, bottom_band,
    )
    unified = 50 + 50 * evidence["net_evidence"]
    unified = unified.clip(0, 100).rename("unified_score")

    phases = classify_market_phase(unified, evidence)

    # 取最新有效一天
    valid_unified = unified.dropna()
    if valid_unified.empty:
        raise ValueError("No valid unified score; dynamic thresholds may all be NaN")

    latest_idx = valid_unified.index[-1]
    latest_ev = evidence.loc[latest_idx]
    latest_phase = phases.loc[latest_idx]

    # 获取对齐后的 top/bottom 最新值
    common = evidence.index
    latest_top = top_composite.reindex(common).loc[latest_idx]
    latest_bottom = bottom_composite.reindex(common).loc[latest_idx]

    vel = latest_phase["velocity"]

    return {
        "date": latest_idx.strftime("%Y-%m-%d") if hasattr(latest_idx, "strftime") else str(latest_idx),
        "top_score": round(float(latest_top), 2),
        "bottom_score": round(float(latest_bottom), 2),
        "unified_score": round(float(valid_unified.iloc[-1]), 2),
        "unified_label": latest_phase["phase"],
        "bear_evidence": round(float(latest_ev["bear_evidence"]), 4),
        "bull_evidence": round(float(latest_ev["bull_evidence"]), 4),
        "net_evidence": round(float(latest_ev["net_evidence"]), 4),
        "is_conflicted": bool(latest_phase["is_conflicted"]),
        "velocity": round(float(vel), 4) if not pd.isna(vel) else 0.0,
        "phase": latest_phase["phase"],
        "emoji": latest_phase["emoji"],
        "advice": latest_phase["advice"],
        # 完整时间序列
        "unified_series": unified,
        "evidence_df": evidence,
        "phases_df": phases,
    }

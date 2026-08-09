"""
权重优化系统

通过历史顶底数据，量化分析每个指标的有效性并优化权重配置。
实现双分数系统：逃顶风险分 + 抄底机会分 + 统一牛熊指标。
"""

from program.weight_optimizer.unified_score import (
    compute_unified_score,
    compute_evidence,
    classify_market_phase,
    compute_full_unified_report,
)

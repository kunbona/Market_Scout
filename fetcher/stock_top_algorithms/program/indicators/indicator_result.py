"""
指标结果数据模型

定义指标计算结果的统一数据结构。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any

from .risk_level import RiskLevel


@dataclass
class IndicatorResult:
    """指标计算结果
    
    Attributes:
        indicator_name: 指标名称（唯一标识符）
        risk_percentage: 风险百分比，范围0-100
        risk_level: 风险等级（低/中/高）
        timestamp: 计算时间戳
        message: 可选的提示信息或说明
        extra_data: 可选的额外数据（用于存储指标特定的详细信息）
    """
    indicator_name: str
    risk_percentage: float
    risk_level: RiskLevel
    timestamp: datetime
    message: Optional[str] = None
    extra_data: Optional[Dict[str, Any]] = None
    
    def __post_init__(self):
        """验证数据有效性"""
        if not 0 <= self.risk_percentage <= 100:
            raise ValueError(
                f"风险百分比必须在0-100范围内，当前值: {self.risk_percentage}"
            )
        if not self.indicator_name:
            raise ValueError("指标名称不能为空")
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式
        
        Returns:
            Dict: 包含所有字段的字典
        """
        return {
            "indicator_name": self.indicator_name,
            "risk_percentage": self.risk_percentage,
            "risk_level": self.risk_level.value,
            "timestamp": self.timestamp.isoformat(),
            "message": self.message,
            "extra_data": self.extra_data,
        }


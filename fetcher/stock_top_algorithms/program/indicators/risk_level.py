"""
风险等级定义模块

定义风险等级的枚举和判断逻辑，用于将风险百分比转换为风险等级。
"""

from enum import Enum
from dataclasses import dataclass
from typing import Optional


class RiskLevel(Enum):
    """风险等级枚举"""
    LOW = "low"           # 低风险
    MEDIUM = "medium"     # 中风险
    HIGH = "high"         # 高风险


@dataclass
class RiskThreshold:
    """风险阈值配置
    
    Attributes:
        low_max: 低风险的最大值（不包含），默认30
        medium_max: 中风险的最大值（不包含），默认70
        high_max: 高风险的最大值，固定为100
    """
    low_max: float = 30.0
    medium_max: float = 70.0
    high_max: float = 100.0
    
    def get_risk_level(self, risk_percentage: float) -> RiskLevel:
        """根据风险百分比获取风险等级
        
        Args:
            risk_percentage: 风险百分比，范围0-100
            
        Returns:
            RiskLevel: 对应的风险等级
            
        Raises:
            ValueError: 如果风险百分比不在0-100范围内
        """
        if not 0 <= risk_percentage <= 100:
            raise ValueError(
                f"风险百分比必须在0-100范围内，当前值: {risk_percentage}"
            )
        
        if risk_percentage < self.low_max:
            return RiskLevel.LOW
        elif risk_percentage < self.medium_max:
            return RiskLevel.MEDIUM
        else:
            return RiskLevel.HIGH


# 默认风险阈值配置
DEFAULT_RISK_THRESHOLD = RiskThreshold()


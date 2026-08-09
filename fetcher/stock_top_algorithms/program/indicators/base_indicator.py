"""
基础指标接口模块

定义所有风险指标必须实现的统一接口规范。
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, Dict, Any

from .indicator_result import IndicatorResult
from .risk_level import RiskLevel, DEFAULT_RISK_THRESHOLD, RiskThreshold


class BaseIndicator(ABC):
    """基础指标抽象类
    
    所有风险指标必须继承此类并实现抽象方法。
    此类定义了统一的接口规范，确保所有指标的一致性。
    """
    
    def __init__(
        self,
        name: str,
        description: Optional[str] = None,
        risk_threshold: Optional[RiskThreshold] = None
    ):
        """初始化指标
        
        Args:
            name: 指标名称（唯一标识符）
            description: 指标描述信息
            risk_threshold: 风险阈值配置，如果为None则使用默认配置
        """
        self.name = name
        self.description = description or ""
        self.risk_threshold = risk_threshold or DEFAULT_RISK_THRESHOLD
    
    @abstractmethod
    def calculate(self, **kwargs) -> IndicatorResult:
        """计算指标风险值
        
        这是所有指标必须实现的核心方法。每个指标根据自身逻辑
        计算当前市场的风险百分比（0-100）。
        
        Args:
            **kwargs: 指标特定的参数，不同的指标可能需要不同的输入数据
                     例如：market_data, price_data, volume_data等
        
        Returns:
            IndicatorResult: 包含风险百分比、风险等级等信息的计算结果
            
        Raises:
            NotImplementedError: 子类必须实现此方法
            ValueError: 如果输入数据无效或计算失败
        """
        raise NotImplementedError(
            f"指标 {self.name} 必须实现 calculate 方法"
        )
    
    def get_risk_level(self, risk_percentage: float) -> RiskLevel:
        """根据风险百分比获取风险等级
        
        这是一个便捷方法，使用指标配置的风险阈值来判断风险等级。
        
        Args:
            risk_percentage: 风险百分比，范围0-100
            
        Returns:
            RiskLevel: 对应的风险等级
        """
        return self.risk_threshold.get_risk_level(risk_percentage)
    
    def create_result(
        self,
        risk_percentage: float,
        message: Optional[str] = None,
        extra_data: Optional[Dict[str, Any]] = None,
        timestamp: Optional[datetime] = None
    ) -> IndicatorResult:
        """创建指标结果对象
        
        便捷方法，用于创建标准化的 IndicatorResult 对象。
        
        Args:
            risk_percentage: 风险百分比，范围0-100
            message: 可选的提示信息
            extra_data: 可选的额外数据
            timestamp: 时间戳，如果为None则使用当前时间
            
        Returns:
            IndicatorResult: 标准化的结果对象
        """
        risk_level = self.get_risk_level(risk_percentage)
        timestamp = timestamp or datetime.now()
        
        return IndicatorResult(
            indicator_name=self.name,
            risk_percentage=risk_percentage,
            risk_level=risk_level,
            timestamp=timestamp,
            message=message,
            extra_data=extra_data
        )
    
    def get_info(self) -> Dict[str, Any]:
        """获取指标信息
        
        Returns:
            Dict: 包含指标名称、描述等信息的字典
        """
        return {
            "name": self.name,
            "description": self.description,
            "risk_threshold": {
                "low_max": self.risk_threshold.low_max,
                "medium_max": self.risk_threshold.medium_max,
                "high_max": self.risk_threshold.high_max
            }
        }
    
    def __repr__(self) -> str:
        """返回指标的字符串表示"""
        return f"<{self.__class__.__name__}(name='{self.name}')>"


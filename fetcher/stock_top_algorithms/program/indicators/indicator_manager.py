"""
指标管理器模块

提供统一管理、注册和调用所有风险指标的接口。
"""

from typing import Dict, List, Optional, Any
from datetime import datetime

from .base_indicator import BaseIndicator
from .indicator_result import IndicatorResult
from .risk_level import RiskLevel


class IndicatorManager:
    """指标管理器
    
    负责管理所有风险指标的注册、调用和结果聚合。
    所有指标应该通过此管理器进行统一管理。
    """
    
    def __init__(self):
        """初始化指标管理器"""
        self._indicators: Dict[str, BaseIndicator] = {}
    
    def register(self, indicator: BaseIndicator, name: Optional[str] = None) -> None:
        """注册指标
        
        Args:
            indicator: 要注册的指标实例
            name: 可选的指标名称，如果不提供则使用indicator.name
            
        Raises:
            ValueError: 如果指标名称为空或已存在同名指标
        """
        indicator_name = name or indicator.name
        if not indicator_name:
            raise ValueError("指标名称不能为空")
        
        if indicator_name in self._indicators:
            raise ValueError(
                f"指标名称 '{indicator_name}' 已存在，"
                f"请使用不同的名称或先取消注册"
            )
        
        self._indicators[indicator_name] = indicator
    
    def unregister(self, name: str) -> None:
        """取消注册指标
        
        Args:
            name: 指标名称
            
        Raises:
            KeyError: 如果指标不存在
        """
        if name not in self._indicators:
            raise KeyError(f"指标 '{name}' 不存在")
        
        del self._indicators[name]
    
    def get_indicator(self, name: str) -> BaseIndicator:
        """获取指标实例
        
        Args:
            name: 指标名称
            
        Returns:
            BaseIndicator: 指标实例
            
        Raises:
            KeyError: 如果指标不存在
        """
        if name not in self._indicators:
            raise KeyError(f"指标 '{name}' 不存在")
        
        return self._indicators[name]
    
    def list_indicators(self) -> List[str]:
        """列出所有已注册的指标名称
        
        Returns:
            List[str]: 指标名称列表
        """
        return list(self._indicators.keys())
    
    def calculate_all(
        self,
        common_params: Optional[Dict[str, Any]] = None,
        indicator_specific_params: Optional[Dict[str, Dict[str, Any]]] = None
    ) -> Dict[str, IndicatorResult]:
        """计算所有已注册指标的风险值
        
        Args:
            common_params: 所有指标共用的参数
            indicator_specific_params: 特定指标的参数，格式为：
                {
                    "indicator_name": {"param1": value1, "param2": value2},
                    ...
                }
        
        Returns:
            Dict[str, IndicatorResult]: 指标名称到结果的映射
            
        Raises:
            Exception: 如果某个指标计算失败，异常会被捕获并记录在结果中
        """
        common_params = common_params or {}
        indicator_specific_params = indicator_specific_params or {}
        
        results = {}
        
        for indicator_name, indicator in self._indicators.items():
            try:
                # 合并通用参数和指标特定参数
                params = {**common_params}
                if indicator_name in indicator_specific_params:
                    params.update(indicator_specific_params[indicator_name])
                
                # 计算指标
                result = indicator.calculate(**params)
                results[indicator_name] = result
                
            except Exception as e:
                # 如果计算失败，创建一个表示错误的结果
                # 这里可以扩展为更详细的错误处理
                error_result = IndicatorResult(
                    indicator_name=indicator_name,
                    risk_percentage=0.0,  # 错误时默认风险为0
                    risk_level=RiskLevel.LOW,
                    timestamp=datetime.now(),
                    message=f"计算失败: {str(e)}",
                    extra_data={"error": True, "error_message": str(e)}
                )
                results[indicator_name] = error_result
        
        return results
    
    def calculate_single(
        self,
        name: str,
        **kwargs
    ) -> IndicatorResult:
        """计算单个指标的风险值
        
        Args:
            name: 指标名称
            **kwargs: 传递给指标calculate方法的参数
        
        Returns:
            IndicatorResult: 计算结果
            
        Raises:
            KeyError: 如果指标不存在
        """
        indicator = self.get_indicator(name)
        return indicator.calculate(**kwargs)
    
    def get_aggregated_risk(
        self,
        results: Optional[Dict[str, IndicatorResult]] = None,
        method: str = "average"
    ) -> Dict[str, Any]:
        """聚合所有指标的风险评估结果
        
        Args:
            results: 指标结果字典，如果为None则重新计算所有指标
            method: 聚合方法，支持 "average"（平均）和 "max"（最大值）
        
        Returns:
            Dict: 包含聚合后的风险百分比、风险等级等信息
        """
        if results is None:
            results = self.calculate_all()
        
        if not results:
            return {
                "aggregated_risk_percentage": 0.0,
                "aggregated_risk_level": RiskLevel.LOW.value,
                "indicator_count": 0,
                "indicator_results": {}
            }
        
        risk_percentages = [
            result.risk_percentage
            for result in results.values()
            if not (result.extra_data and result.extra_data.get("error", False))
        ]
        
        if not risk_percentages:
            aggregated_percentage = 0.0
        elif method == "average":
            aggregated_percentage = sum(risk_percentages) / len(risk_percentages)
        elif method == "max":
            aggregated_percentage = max(risk_percentages)
        else:
            raise ValueError(f"不支持的聚合方法: {method}，支持 'average' 或 'max'")
        
        # 根据聚合后的百分比确定风险等级
        from .risk_level import DEFAULT_RISK_THRESHOLD
        aggregated_risk_level = DEFAULT_RISK_THRESHOLD.get_risk_level(
            aggregated_percentage
        )
        
        return {
            "aggregated_risk_percentage": aggregated_percentage,
            "aggregated_risk_level": aggregated_risk_level.value,
            "indicator_count": len(results),
            "indicator_results": {
                name: result.to_dict()
                for name, result in results.items()
            },
            "method": method
        }
    
    def get_indicators_info(self) -> Dict[str, Dict[str, Any]]:
        """获取所有指标的详细信息
        
        Returns:
            Dict: 指标名称到指标信息的映射
        """
        return {
            name: indicator.get_info()
            for name, indicator in self._indicators.items()
        }


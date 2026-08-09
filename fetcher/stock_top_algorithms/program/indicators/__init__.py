"""
指标模块

提供统一的风险指标接口和规范，用于牛市逃顶风险提示。

主要组件：
- BaseIndicator: 基础指标抽象类，所有指标必须继承此类
- IndicatorResult: 指标计算结果数据模型
- RiskLevel: 风险等级枚举
- IndicatorManager: 指标管理器，用于统一管理所有指标
"""

from .base_indicator import BaseIndicator
from .indicator_result import IndicatorResult
from .risk_level import RiskLevel, RiskThreshold, DEFAULT_RISK_THRESHOLD
from .indicator_manager import IndicatorManager
from .equity_premium_indicator import EquityPremiumIndicator
from .macd_divergence_indicator import MACDDivergenceIndicator
from .pe_valuation_indicator import PEValuationIndicator
from .price_precentile_indicator import PricePrecentileIndicator
from .market_turnover_percentile_indicator import MarketTurnoverPercentileIndicator
from .ma250_bias_indicator import MA250BiasIndicator
from .market_crowdedness_indicator import MarketCrowdednessIndicator
from .below_net_asset_indicator import BelowNetAssetIndicator
from .herding_rate_indicator import HerdingRateIndicator

__all__ = [
    "BaseIndicator",
    "IndicatorResult",
    "IndicatorManager",
    "RiskLevel",
    "RiskThreshold",
    "DEFAULT_RISK_THRESHOLD",
    "EquityPremiumIndicator",
    "MACDDivergenceIndicator",
    "PEValuationIndicator",
    "PricePrecentileIndicator",
    "MarketTurnoverPercentileIndicator",
    "MA250BiasIndicator",
    "MarketCrowdednessIndicator",
    "BelowNetAssetIndicator",
    "HerdingRateIndicator",
]

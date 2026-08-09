# 风险指标系统接口规范

## 概述

本文档定义了牛市逃顶风险指标系统的统一接口和规范。所有风险指标必须遵循此规范进行开发。

## 核心概念

### 风险百分比（Risk Percentage）
- **范围**：0-100
- **含义**：数值越高表示市场风险越高，越接近牛市顶部
- **0-30**：低风险
- **30-70**：中风险
- **70-100**：高风险

### 风险等级（Risk Level）
- **LOW**：低风险
- **MEDIUM**：中风险
- **HIGH**：高风险

## 核心组件

### 1. BaseIndicator（基础指标类）

所有指标必须继承 `BaseIndicator` 并实现 `calculate` 方法。

#### 必须实现的方法

```python
def calculate(self, **kwargs) -> IndicatorResult:
    """计算指标风险值
    
    Args:
        **kwargs: 指标特定的参数
        
    Returns:
        IndicatorResult: 包含风险百分比、风险等级等信息的计算结果
    """
    # 实现指标的计算逻辑
    risk_percentage = ...  # 计算风险百分比（0-100）
    
    return self.create_result(
        risk_percentage=risk_percentage,
        message="可选的提示信息",
        extra_data={"可选的额外数据": "value"}
    )
```

#### 提供的便捷方法

- `get_risk_level(risk_percentage)`: 根据风险百分比获取风险等级
- `create_result(...)`: 创建标准化的 IndicatorResult 对象
- `get_info()`: 获取指标信息

### 2. IndicatorResult（指标结果）

标准化的指标计算结果数据结构：

```python
@dataclass
class IndicatorResult:
    indicator_name: str           # 指标名称
    risk_percentage: float        # 风险百分比（0-100）
    risk_level: RiskLevel        # 风险等级
    timestamp: datetime           # 计算时间戳
    message: Optional[str]        # 可选的提示信息
    extra_data: Optional[Dict]   # 可选的额外数据
```

### 3. IndicatorManager（指标管理器）

统一管理所有指标的注册、调用和结果聚合。

#### 主要方法

- `register(indicator, name=None)`: 注册指标
- `unregister(name)`: 取消注册指标
- `calculate_all(...)`: 计算所有已注册指标
- `calculate_single(name, **kwargs)`: 计算单个指标
- `get_aggregated_risk(...)`: 聚合所有指标的风险评估

## 使用示例

### 示例1：创建一个简单的指标

```python
from program.indicators import BaseIndicator, IndicatorResult
from datetime import datetime

class SimpleIndicator(BaseIndicator):
    """简单的示例指标"""
    
    def __init__(self):
        super().__init__(
            name="simple_indicator",
            description="这是一个简单的示例指标"
        )
    
    def calculate(self, market_data=None, **kwargs) -> IndicatorResult:
        # 实现你的指标计算逻辑
        # 假设根据某些条件计算风险百分比
        risk_percentage = 45.5  # 示例值
        
        return self.create_result(
            risk_percentage=risk_percentage,
            message="市场处于中等风险状态",
            extra_data={"calculation_details": "..."}
        )
```

### 示例2：使用指标管理器

```python
from program.indicators import IndicatorManager

# 创建管理器
manager = IndicatorManager()

# 注册指标
indicator1 = SimpleIndicator()
manager.register(indicator1)

# 计算所有指标
results = manager.calculate_all(
    common_params={"market_data": some_data}
)

# 获取聚合风险
aggregated = manager.get_aggregated_risk(results)
print(f"聚合风险百分比: {aggregated['aggregated_risk_percentage']}")
print(f"聚合风险等级: {aggregated['aggregated_risk_level']}")
```

### 示例3：自定义风险阈值

```python
from program.indicators import BaseIndicator, RiskThreshold

# 创建自定义风险阈值
custom_threshold = RiskThreshold(low_max=20.0, medium_max=60.0)

class CustomIndicator(BaseIndicator):
    def __init__(self):
        super().__init__(
            name="custom_indicator",
            risk_threshold=custom_threshold
        )
    
    def calculate(self, **kwargs) -> IndicatorResult:
        # 实现计算逻辑
        risk_percentage = 50.0
        return self.create_result(risk_percentage=risk_percentage)
```

## 开发规范

### 1. 指标命名
- 使用小写字母和下划线（snake_case）
- 名称应该清晰描述指标的功能
- 例如：`price_momentum_indicator`, `volume_analysis_indicator`

### 2. 参数设计
- 使用 `**kwargs` 接收参数，保持灵活性
- 在文档中明确说明需要哪些参数
- 对于可选参数，提供合理的默认值

### 3. 错误处理
- 验证输入参数的有效性
- 如果计算失败，抛出有意义的异常
- 指标管理器会自动捕获异常并创建错误结果

### 4. 额外数据
- 使用 `extra_data` 存储指标特定的详细信息
- 保持数据结构简单和可序列化
- 在文档中说明额外数据的结构

### 5. 消息提示
- `message` 字段应该提供人类可读的风险提示
- 可以包含建议或警告信息
- 保持简洁明了

## 扩展指南

要添加新指标：

1. **创建指标类**：继承 `BaseIndicator`
2. **实现 calculate 方法**：根据指标逻辑计算风险百分比
3. **注册指标**：使用 `IndicatorManager.register()` 注册
4. **测试验证**：确保指标正常工作

## 注意事项

1. **指标独立性**：所有指标应该是互相独立的，不依赖其他指标
2. **风险百分比范围**：必须确保返回值在 0-100 范围内
3. **线程安全**：如果指标需要访问共享资源，考虑线程安全问题
4. **性能考虑**：指标计算可能频繁调用，注意性能优化


# 本地量价数据接入接口

## 背景

本地量化数据存储于 `/mnt/ssd_1T/runist/data/Quant_Data/`，主要数据集：
- `stock-trading-data-pro/`：每股一个 CSV，含 OHLC、成交额、资金流、申万行业等
- `stock-notices-title/`：公告标题，5843 只股票
- `stock-analyst-ranking-*`：分析师报告数据

## 如何接入

在 `quant_data/` 目录下新建计算模块，将计算结果写入 SQLite 的 `quant_signals` 表：

```python
# quant_data/your_signal.py
from db.storage import insert_quant_signal

def compute_and_store(signal_date: str):
    """
    读取本地量价数据，计算信号，写入数据库。
    """
    results = [
        {
            "signal_date": signal_date,     # "YYYY-MM-DD"
            "stock_code": "sh600036",
            "signal_type": "your_signal_name",
            "signal_value": 1.23,           # 信号数值
            "extra_json": '{"detail": ...}' # 可选，JSON 字符串存额外信息
        },
        ...
    ]
    for r in results:
        insert_quant_signal(**r)
```

## 数据库表结构

```sql
CREATE TABLE IF NOT EXISTS quant_signals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_date  TEXT,         -- 信号日期
    stock_code   TEXT,         -- 股票代码
    signal_type  TEXT,         -- 信号类型名称（自定义）
    signal_value REAL,         -- 信号数值
    extra_json   TEXT,         -- 额外字段（JSON 字符串）
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## UI 展示

写入 `quant_signals` 表后，数据自动出现在 Streamlit「📊 量价信号」Tab，按 signal_type 分组展示。

## 已规划的量价信号

| 信号名称 | 逻辑 | 优先级 |
|---------|------|--------|
| 板块涨停密度 | 板块内涨停数/总家数，>30% 触发 | P1 |
| 量比异动 | 今日量/5日均量 > 3 | P1 |
| 龙头 ND 因子 | 开源证券龙头-行业动量复合因子 | P2 |
| 分析师预期修正 | 行业层面买入报告密度变化 | P2 |

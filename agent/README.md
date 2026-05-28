# Agent 综合判断接口

## 定位

Agent 模块接收看板中所有已抓取和计算的数据，调用 LLM 生成综合判断文本，展示在「🤖 Agent 判断」Tab。

**前期不启用，等数据层稳定后再接入。**

## 接口规范

```python
# agent/summarizer.py
from db.storage import get_agent_context, insert_agent_summary

def run_summary():
    """
    1. 从数据库读取最新数据快照
    2. 调用 LLM API（Claude / DeepSeek / 本地模型均可）
    3. 将结果写入 agent_summary 表
    """
    context = get_agent_context()   # 返回结构化的数据摘要字典
    prompt = build_prompt(context)  # 构建提示词
    response = call_llm(prompt)     # 调用 LLM
    insert_agent_summary(
        content=response,
        data_snapshot_json=json.dumps(context)
    )
```

## 数据库表结构

```sql
CREATE TABLE IF NOT EXISTS agent_summary (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    summary_time         TEXT,    -- 生成时间
    content              TEXT,    -- LLM 输出的综合判断文本
    data_snapshot_json   TEXT,    -- 输入数据快照（JSON 字符串）
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## Agent 接收的数据范围

Agent 在生成判断时，`get_agent_context()` 会聚合以下数据：
- 最近 2 小时财联社电报（重要快讯）
- 今日政策原文标题列表
- 板块资金流 Top 10（净流入排序）
- 今日涨停池（附连板数和板块）
- 量价信号（quant_signals 当日数据）

## 提示词设计原则（待实现时参考）

- 不要求 Agent 给出买卖建议，只要求识别：当前市场在关注什么方向、资金在往哪里聚集、有无政策催化
- 输出格式：3~5 条简洁结论，每条附数据依据
- 模型选择：DeepSeek-V3（成本低）或 Claude（质量高），通过环境变量配置

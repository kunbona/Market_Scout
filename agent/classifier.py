"""
新闻分类器 Agent。
当前版本：基于关键词规则的本地分类（无需 LLM）。
未来版本：替换为本地 Qwen 7B 或小模型 API。
"""
import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 分类关键词规则（硬编码，高优先级）
_POLICY_KEYWORDS = ["国务院", "发改委", "证监会", "人民银行", "财政部", "工信部", "意见", "通知", "办法", "规定", "条例"]
_INDUSTRY_KEYWORDS = ["板块", "概念", "赛道", "产业链", "新能源", "AI", "人工智能", "芯片", "机器人", "低空"]
_STOCK_KEYWORDS = ["涨停", "跌停", "大宗", "减持", "增持", "回购", "定增", "并购", "重组"]
_MACRO_KEYWORDS = ["CPI", "PPI", "GDP", "PMI", "非农", "美联储", "降息", "加息", "通胀"]
_IRRELEVANT_KEYWORDS = ["娱乐", "体育", "明星", "电影", "综艺", "足球", "篮球"]

# 重要性关键词
_HIGH_URGENCY = ["重磅", "紧急", "突发", "重大", "重要", "关键", "核心", "首次", "历史"]
_LOW_URGENCY = ["提示", "公告", "披露", "例行"]


def classify_news(title: str, content: str = "") -> dict:
    """
    对单条新闻进行分类。

    返回格式：
    {
        "category": "政策|产业|个股|海外|市场情绪|无关",
        "urgency": 1,           # 1=低 2=中 3=高
        "sector_tags": [],      # 识别到的板块标签
        "summary": "",          # 标题前15字
        "keep": True            # False 则下游过滤掉
    }
    """
    text = title + " " + content

    # 确定分类
    if any(k in text for k in _IRRELEVANT_KEYWORDS):
        category = "无关"
    elif any(k in text for k in _POLICY_KEYWORDS):
        category = "政策"
    elif any(k in text for k in _MACRO_KEYWORDS):
        category = "海外"
    elif any(k in text for k in _INDUSTRY_KEYWORDS):
        category = "产业"
    elif any(k in text for k in _STOCK_KEYWORDS):
        category = "个股"
    else:
        category = "市场情绪"

    # 确定紧急程度
    if any(k in text for k in _HIGH_URGENCY):
        urgency = 3
    elif any(k in text for k in _LOW_URGENCY):
        urgency = 1
    else:
        urgency = 2

    # 过滤规则
    keep = not (urgency == 1 and category in ("无关", "市场情绪"))

    # 板块标签
    sector_tags = [k for k in _INDUSTRY_KEYWORDS if k in text]

    return {
        "category": category,
        "urgency": urgency,
        "sector_tags": sector_tags[:3],
        "summary": title[:15],
        "keep": keep
    }


def batch_classify(news_list: list[dict], max_output: int = 20) -> list[dict]:
    """
    批量分类新闻列表，过滤噪音，返回最多 max_output 条。
    news_list: [{"title": ..., "content": ..., ...}, ...]
    """
    results = []
    for item in news_list:
        title = item.get("title", "")
        content = item.get("content", "")
        cls = classify_news(title, content)
        if cls["keep"]:
            item["_classification"] = cls
            results.append(item)

    # 按 urgency 降序，取前 max_output
    results.sort(key=lambda x: x["_classification"]["urgency"], reverse=True)
    return results[:max_output]

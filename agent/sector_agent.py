"""
板块叙事 Agent。
当前版本：预留接口，返回占位数据结构。
未来版本：接入 Claude Haiku 或 DeepSeek V3。
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 未来版本：从环境变量读取 API key
# CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY")
# DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")


def run_sector_agent(
    top_sectors_data: list[dict],    # 来自 sector_zt_density 的 top 板块
    sector_flow_data: list[dict],    # 来自 sector_flow 的近3日累计
    filtered_news: list[dict],       # 来自 classifier 的 urgency>=2 新闻
) -> dict:
    """
    板块叙事分析。

    返回格式：
    {
        "top_sectors": [
            {
                "name": "低空经济",
                "stage": "爆发|萌芽|退潮|data_insufficient",
                "evidence": "...",
                "risk": "..."
            }
        ],
        "market_breadth": "强|中|弱",
        "theme_coherence": "集中|分散",
        "warning": "",
        "source": "rule_based|llm"  # 标注是规则还是 LLM 生成
    }
    """
    # TODO: Phase V3 接入 LLM
    # 当前版本：基于规则的简单判断

    result = {
        "top_sectors": [],
        "market_breadth": "中",
        "theme_coherence": "分散",
        "warning": "",
        "source": "rule_based"
    }

    for sector in top_sectors_data[:5]:
        density = sector.get("zt_density", 0)
        stage = "爆发" if density >= 0.10 else ("萌芽" if density >= 0.05 else "data_insufficient")
        result["top_sectors"].append({
            "name": sector.get("industry", ""),
            "stage": stage,
            "evidence": f"涨停密度{density:.1%}，涨停{sector.get('zt_count', 0)}只",
            "risk": "密度已达高位，关注退潮" if density >= 0.15 else ""
        })

    if top_sectors_data:
        max_density = max(s.get("zt_density", 0) for s in top_sectors_data)
        result["market_breadth"] = "强" if max_density >= 0.10 else ("弱" if max_density < 0.03 else "中")

    return result


def is_available() -> bool:
    """检查 LLM API 是否可用（未来版本使用）"""
    return False  # 当前版本始终返回 False，使用规则模式

"""
风控时机 Agent。
这是所有其他 Agent 的门控层。
当前版本：硬规则判断，无需 LLM。
"""
import logging

logger = logging.getLogger(__name__)


def run_risk_agent(
    market_emotion_today: dict,   # 今日 market_emotion 记录
    sector_flow_3d: list[dict],   # 近3日板块资金流
    policy_news: list[dict] = None, # 近3天政策新闻（用于判断政策周期）
) -> dict:
    """
    风控判断。输出决定是否运行下游 Agent。

    返回格式：
    {
        "market_mode": "正常|谨慎|不操作",
        "policy_phase": "吹风|征求意见|正式落地|无明显政策",
        "active_sector_status": {},   # {板块名: "健康|退潮风险|已退潮"}
        "reason": "...",
        "should_run_stock_agent": True
    }
    """
    result = {
        "market_mode": "正常",
        "policy_phase": "无明显政策",
        "active_sector_status": {},
        "reason": "",
        "should_run_stock_agent": True
    }

    zt_total = market_emotion_today.get("zt_total", 0) if market_emotion_today else 0
    dt_total = market_emotion_today.get("dt_total", 0) if market_emotion_today else 0
    zb_rate = market_emotion_today.get("zb_rate", 0.0) if market_emotion_today else 0.0

    reasons = []

    # 硬规则判断
    if zt_total < 10:
        result["market_mode"] = "不操作"
        result["should_run_stock_agent"] = False
        reasons.append(f"涨停仅{zt_total}只，市场极弱")
    elif zt_total < 20:
        result["market_mode"] = "谨慎"
        reasons.append(f"涨停{zt_total}只，市场偏弱")
    elif dt_total > 0 and zt_total > 0 and dt_total > zt_total * 0.5:
        result["market_mode"] = "谨慎"
        reasons.append(f"跌停{dt_total}只超过涨停50%")
    else:
        reasons.append(f"涨停{zt_total}只，炸板率{zb_rate:.1%}")

    # 政策周期判断（关键词规则）
    if policy_news:
        texts = " ".join(n.get("title", "") for n in policy_news)
        if any(k in texts for k in ["征求意见", "公开征求", "意见稿"]):
            result["policy_phase"] = "征求意见"
        elif any(k in texts for k in ["正式发布", "正式实施", "印发", "发布"]):
            result["policy_phase"] = "正式落地"
        elif any(k in texts for k in ["研究", "探索", "考虑", "推进"]):
            result["policy_phase"] = "吹风"

    result["reason"] = "；".join(reasons)
    return result

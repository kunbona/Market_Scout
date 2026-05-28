"""
个股候选 Agent。
当前版本：预留接口，基于规则评分。
未来版本：接入 Claude Sonnet。
"""
import logging

logger = logging.getLogger(__name__)


def run_stock_agent(
    zt_pool_today: list[dict],       # 今日涨停池
    volume_breakout_today: list[dict], # 今日成交额异动
    active_sectors: list[dict],       # 来自 sector_agent 的活跃板块
    lhb_data: list[dict] = None,      # 龙虎榜（盘后才有）
) -> dict:
    """
    个股候选筛选。最多返回 5 只。

    返回格式：
    {
        "candidates": [
            {
                "ticker": "300XXX",
                "name": "XX科技",
                "direction": "短线|波段",
                "evidence": "...",
                "entry_note": "...",
                "risk_note": "...",
                "confidence": "高|中|低",
                "data_gaps": []
            }
        ],
        "source": "rule_based|llm"
    }
    """
    # TODO: Phase V3 接入 LLM
    candidates = []
    active_sector_names = {s.get("name", "") for s in (active_sectors or [])}

    for stock in (zt_pool_today or [])[:50]:
        score = 0
        data_gaps = []
        evidence_parts = []

        lianzban = stock.get("zt_count", 1)
        first_time = stock.get("first_zt_time", "")
        sector = stock.get("sector", "")

        evidence_parts.append(f"连板{lianzban}板")

        # 封板时间加分
        if first_time and first_time <= "10:30":
            score += 2
            evidence_parts.append(f"封板{first_time}")
        elif first_time:
            score += 1
            evidence_parts.append(f"封板{first_time}")
        else:
            data_gaps.append("封板时间")

        # 连板数加分
        if lianzban >= 3:
            score += 3
        elif lianzban >= 2:
            score += 2
        else:
            score += 1

        # 板块共振加分
        if sector and any(s in sector for s in active_sector_names):
            score += 3
            evidence_parts.append(f"板块{sector}活跃")

        # 龙虎榜加分
        if lhb_data:
            lhb_codes = {r.get("stock_code", "") for r in lhb_data}
            if stock.get("stock_code", "") in lhb_codes:
                score += 2
                evidence_parts.append("龙虎榜上榜")
        else:
            data_gaps.append("龙虎榜未出")

        confidence = "高" if score >= 7 else ("中" if score >= 4 else "低")

        candidates.append({
            "ticker": stock.get("stock_code", ""),
            "name": stock.get("stock_name", ""),
            "direction": "波段" if lianzban >= 3 else "短线",
            "evidence": "，".join(evidence_parts),
            "entry_note": "次日竞价缩量可考虑" if lianzban <= 2 else "高位注意风险",
            "risk_note": "板块退潮注意" if lianzban >= 3 else "",
            "confidence": confidence,
            "data_gaps": data_gaps,
            "_score": score
        })

    candidates.sort(key=lambda x: x["_score"], reverse=True)
    for c in candidates:
        c.pop("_score", None)

    return {
        "candidates": candidates[:5],
        "source": "rule_based"
    }

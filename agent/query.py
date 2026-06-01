"""
市场数据查询脚本。供 Claude CLI 通过 Bash 工具调用，结果 JSON 输出到 stdout。

用法：
  python agent/query.py <command> [options]

Commands:
  market_emotion          今日市场情绪（涨停/跌停/炸板率/连板/溢价）
  zt_pool                 今日涨停池
  sector_zt_density       行业涨停密度 Top10
  sector_flow_accel       机构资金加速度 Top10
  lhb                     今日龙虎榜
  lianzban_chain          连板链条（2板+）
  volume_breakout         成交额异动（5d/20d > 2x）
  news                    最近N小时财经新闻  --hours N（默认2）
  policy_news             近3日政策新闻
  f10                     涨停股F10基本面   --codes 300XXX,600XXX
  sector_flow             行业资金流最新快照
  context                 完整上下文（所有数据，供完整报告使用）
"""
import argparse
import json
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path

# 确保从项目根目录能找到 db 模块
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _out(data):
    print(json.dumps(data, ensure_ascii=False, default=str))


def cmd_market_emotion(args):
    from db.storage import get_agent_context
    ctx = get_agent_context()
    emotion = ctx.get("market_emotion_today")
    if not emotion:
        _out({"error": "INSUFFICIENT_DATA", "reason": "今日 market_emotion 记录不存在，可能是非交易日或日线计算未运行"})
        return
    _out(emotion)


def cmd_zt_pool(args):
    from db.storage import get_zt_pool
    today = datetime.now().strftime("%Y-%m-%d")
    data = get_zt_pool(today)
    if not data:
        _out({"note": "今日涨停池为空，可能是非交易日或数据未采集", "data": []})
        return
    _out(data)


def cmd_sector_zt_density(args):
    from db.storage import get_sector_zt_density
    today = datetime.now().strftime("%Y-%m-%d")
    data = get_sector_zt_density(today)
    if not data:
        _out({"note": "今日行业涨停密度数据为空", "data": []})
        return
    _out(data[:10])


def cmd_sector_flow_accel(args):
    from db.storage import get_sector_flow_accel
    today = datetime.now().strftime("%Y-%m-%d")
    data = get_sector_flow_accel(today)
    if not data:
        _out({"note": "今日机构资金加速度数据为空", "data": []})
        return
    _out(data[:10])


def cmd_lhb(args):
    from db.storage import get_lhb_data, get_lhb_seat
    today = datetime.now().strftime("%Y-%m-%d")
    data = get_lhb_data(today)

    # 附加席位明细（来自本地量化数据，含游资识别）
    seats = get_lhb_seat(today)
    seat_map: dict = {}
    for s in seats:
        code = s.get("stock_code", "")
        seat_map.setdefault(code, []).append({
            "seat_name":  s.get("seat_name"),
            "seat_type":  s.get("seat_type"),   # 游资/机构/北向/其他
            "net_amount": s.get("net_amount"),
            "buy_amount": s.get("buy_amount"),
            "sell_amount": s.get("sell_amount"),
        })

    if not data and not seat_map:
        _out({
            "note": "龙虎榜今日暂无数据。通常收盘后17:30发布，本地席位数据18:30同步。",
            "data": []
        })
        return

    # 将席位信息合并到 lhb_data
    for row in data:
        code = row.get("stock_code", "")
        row_seats = seat_map.get(code, [])
        row["seats"] = row_seats
        # 快捷字段：席位性质汇总（供 Claude 直接判断）
        types = {s["seat_type"] for s in row_seats}
        if "游资" in types and "机构" in types:
            row["seat_nature"] = "游资+机构混合"
        elif "机构" in types:
            row["seat_nature"] = "机构主导"
        elif "游资" in types:
            row["seat_nature"] = "游资主导"
        elif row_seats:
            row["seat_nature"] = "其他"
        else:
            row["seat_nature"] = "席位数据未就绪"

    _out(data if data else list(seat_map.values()))


def cmd_lianzban_chain(args):
    from db.storage import get_lianzban_chain
    today = datetime.now().strftime("%Y-%m-%d")
    data = get_lianzban_chain(today)
    if not data:
        _out({"note": "今日连板链条数据为空（无2板及以上个股）", "data": []})
        return
    _out(data)


def cmd_volume_breakout(args):
    from db.storage import get_volume_breakout
    today = datetime.now().strftime("%Y-%m-%d")
    data = get_volume_breakout(today)
    if not data:
        _out({"note": "今日成交额异动数据为空", "data": []})
        return
    _out(data)


def cmd_news(args):
    from db.storage import get_cls_news
    from agent.classifier import batch_classify

    hours = getattr(args, "hours", 2)
    cutoff = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    all_news = get_cls_news(limit=100)
    recent = [n for n in all_news if n.get("pub_time", "") >= cutoff]

    # 过滤分类，只留有价值的
    classified = batch_classify(recent)
    filtered = [n for n in classified if n.get("keep", True)]

    _out({
        "query_hours": hours,
        "cutoff": cutoff,
        "total_raw": len(recent),
        "after_filter": len(filtered),
        "news": filtered[:30]
    })


def cmd_policy_news(args):
    from db.storage import get_policy_news
    from agent.classifier import batch_classify

    three_days_ago = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
    all_policy = get_policy_news(limit=50)
    recent = [n for n in all_policy if (n.get("pub_time") or "") >= three_days_ago]

    classified = batch_classify(recent)
    filtered = [n for n in classified if n.get("urgency", 1) >= 2]

    _out({
        "since": three_days_ago,
        "total_raw": len(recent),
        "after_filter": len(filtered),
        "news": filtered[:20]
    })


def cmd_f10(args):
    from db.storage import get_fundamentals_f10
    today = datetime.now().strftime("%Y-%m-%d")

    codes_str = getattr(args, "codes", "") or ""
    if not codes_str:
        _out({"error": "请通过 --codes 300XXX,600XXX 指定股票代码"})
        return

    codes = [c.strip() for c in codes_str.split(",") if c.strip()]
    results = []
    for code in codes[:20]:  # 最多20只
        rows = get_fundamentals_f10(today, code)
        if rows:
            # 只取公司概况
            overview = next((r for r in rows if r.get("category") == "公司概况"), None)
            if overview:
                results.append({
                    "stock_code": code,
                    "category": "公司概况",
                    "content": (overview.get("content") or "")[:500]
                })
            else:
                results.append({"stock_code": code, "note": "无公司概况数据"})
        else:
            results.append({"stock_code": code, "note": "无F10数据（该股可能未进入今日涨停/强势池）"})

    _out(results)


def cmd_sector_flow(args):
    from db.storage import get_sector_flow_latest
    data = get_sector_flow_latest(source_type="industry")
    if not data:
        _out({"note": "行业资金流数据为空", "data": []})
        return
    _out(data[:20])


def cmd_lockup(args):
    """查询解禁数据。--codes 指定个股，不传则返回未来30天全市场解禁列表。"""
    codes_str = getattr(args, "codes", "") or ""

    if codes_str:
        from db.storage import get_lockup_expiry_by_code
        codes = [c.strip() for c in codes_str.split(",") if c.strip()]
        results = []
        for code in codes[:20]:
            rows = get_lockup_expiry_by_code(code, days=30)
            if rows:
                total_ratio = sum(r.get("lift_ratio", 0) for r in rows)
                results.append({
                    "stock_code": code,
                    "stock_name": rows[0].get("stock_name", ""),
                    "lockup_events": rows,
                    "total_lift_ratio_30d": round(total_ratio, 4),
                    "risk_level": "高" if total_ratio >= 0.10 else ("中" if total_ratio >= 0.05 else "低"),
                })
            else:
                results.append({
                    "stock_code": code,
                    "note": "30天内无解禁记录",
                    "risk_level": "无",
                })
        _out(results)
    else:
        from db.storage import get_lockup_expiry
        data = get_lockup_expiry(days=30)
        if not data:
            _out({"note": "未来30天无解禁记录", "data": []})
            return
        _out(data[:50])


def cmd_context(args):
    """完整上下文，一次性返回所有分析所需数据。"""
    from db.storage import get_agent_context
    from agent.classifier import batch_classify

    ctx = get_agent_context()

    # 新闻过滤
    all_news = ctx.get("recent_cls_news", []) + ctx.get("policy_news_today", [])
    classified = batch_classify(all_news)
    filtered_news = [n for n in classified if n.get("keep") and n.get("urgency", 1) >= 2]

    _out({
        "query_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "market_emotion": ctx.get("market_emotion_today"),
        "sector_zt_density_top10": ctx.get("sector_zt_density_top10", []),
        "sector_flow_accel_top10": ctx.get("sector_flow_accel_today", []),
        "zt_pool": ctx.get("zt_pool_today", []),
        "lianzban_chain": ctx.get("lianzban_chain_today", []),
        "volume_breakout": ctx.get("volume_breakout_today", []),
        "lhb": ctx.get("lhb_today", []),
        "filtered_news": filtered_news[:20],
        "f10": ctx.get("f10_today", []),
    })


COMMANDS = {
    "market_emotion":    cmd_market_emotion,
    "zt_pool":           cmd_zt_pool,
    "sector_zt_density": cmd_sector_zt_density,
    "sector_flow_accel": cmd_sector_flow_accel,
    "lhb":               cmd_lhb,
    "lianzban_chain":    cmd_lianzban_chain,
    "volume_breakout":   cmd_volume_breakout,
    "news":              cmd_news,
    "policy_news":       cmd_policy_news,
    "f10":               cmd_f10,
    "sector_flow":       cmd_sector_flow,
    "lockup":            cmd_lockup,
    "context":           cmd_context,
}


def main():
    parser = argparse.ArgumentParser(description="market-radar 数据查询工具")
    parser.add_argument("command", choices=list(COMMANDS.keys()), help="查询类型")
    parser.add_argument("--hours", type=int, default=2, help="新闻查询小时数（news 命令使用）")
    parser.add_argument("--codes", type=str, default="", help="股票代码列表，逗号分隔（f10/lockup 命令使用）")
    args = parser.parse_args()

    try:
        COMMANDS[args.command](args)
    except Exception as e:
        _out({"error": str(e), "command": args.command})
        sys.exit(1)


if __name__ == "__main__":
    main()

"""
市场数据查询脚本。供 Claude CLI 通过 Bash 工具调用，结果 JSON 输出到 stdout。

用法：
  python agent/query.py <command> [options]

Commands:
  data_health             【必须最先调用】数据健康检查：实时 vs 静态数据一致性，冲突检测，降级提示
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


def cmd_data_health(args):
    """
    数据健康检查（Pre-flight hook）。
    检查实时数据与静态数据的新鲜度，检测两者冲突，输出降级建议。
    必须在所有其他查询之前调用。
    """
    import sqlite3
    from db.storage import DB_PATH

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    now_h = now.hour

    status = {}
    conflicts = []
    fallback_hints = {}

    with sqlite3.connect(DB_PATH) as conn:
        def _latest_date(table, date_col="trade_date"):
            try:
                row = conn.execute(
                    f"SELECT {date_col}, COUNT(*) FROM {table} WHERE {date_col} = "
                    f"(SELECT MAX({date_col}) FROM {table})"
                ).fetchone()
                return (row[0], row[1]) if row and row[0] else (None, 0)
            except Exception:
                return (None, 0)

        def _latest_ts(table, ts_col):
            try:
                row = conn.execute(
                    f"SELECT MAX({ts_col}) FROM {table}"
                ).fetchone()
                return row[0] if row else None
            except Exception:
                return None

        def _gap_days(date_str):
            if not date_str:
                return None
            try:
                d = datetime.strptime(date_str, "%Y-%m-%d")
                return (now - d).days
            except Exception:
                return None

        # ── 实时数据（东财抓取，盘中更新）
        zt_date, zt_count = _latest_date("zt_pool")
        dt_date, dt_count = _latest_date("dt_pool")
        news_ts = _latest_ts("cls_news", "pub_time")
        sector_flow_ts = _latest_ts("sector_flow", "fetch_time")
        lhb_date, lhb_count = _latest_date("lhb_data")
        northbound_ts = _latest_ts("northbound_flow", "fetch_time")

        # ── 静态数据（本地量化日线，daily_compute 每日09:00生成）
        emotion_date, _ = _latest_date("market_emotion")
        sector_density_date, _ = _latest_date("sector_zt_density")
        advance_decline_date, _ = _latest_date("advance_decline")
        lianzban_date, lianzban_count = _latest_date("lianzban_chain")

    # ── 盘期判断（用 AKShare 交易日历确认，不依赖 zt_pool 是否有数据）
    is_weekend = now.weekday() >= 5
    try:
        import akshare as ak
        cal = ak.tool_trade_date_hist_sina()
        is_trade_day = today in cal["trade_date"].astype(str).values
    except Exception:
        # AKShare 异常时 fallback：周一至周五视为交易日（节假日会误判，可接受）
        is_trade_day = not is_weekend
    is_pre_market = is_trade_day and (now_h * 60 + now.minute < 9 * 60 + 30)
    # 细粒度盘期标签，供 agent 决策
    if is_trade_day:
        if now_h < 9 or (now_h == 9 and now.minute < 15):
            session = "pre_open"          # 交易日但还没到集合竞价（极罕见）
        elif now_h < 15 or (now_h == 15 and now.minute == 0):
            session = "intraday"          # 盘中
        elif now_h < 17:
            session = "post_close"        # 收盘后 ~ 17:00
        else:
            session = "evening"           # 17:00 以后（龙虎榜/本地数据应已就绪）
    elif is_weekend:
        session = "weekend"               # 周末
    else:
        session = "holiday"               # 非交易日（节假日），AKShare 日历确认

    status["realtime"] = {
        "zt_pool":        {"latest_date": zt_date, "count": zt_count, "fresh": zt_date == today},
        "dt_pool":        {"latest_date": dt_date, "count": dt_count, "fresh": dt_date == today},
        "cls_news":       {"latest_time": news_ts, "fresh": bool(news_ts and news_ts[:10] == today)},
        "sector_flow":    {"latest_time": sector_flow_ts, "fresh": bool(sector_flow_ts and sector_flow_ts[:10] == today)},
        "lhb_data":       {
            "latest_date": lhb_date, "count": lhb_count,
            "fresh": lhb_date == today,
            "note": "17:30后才有，盘中为空属正常" if now_h < 17 else ("已出榜" if lhb_date == today else "盘后仍为空，可能抓取失败"),
        },
        "northbound":     {"latest_time": northbound_ts, "fresh": bool(northbound_ts and northbound_ts[:10] == today)},
    }

    emotion_gap = _gap_days(emotion_date)
    status["static"] = {
        "market_emotion":   {
            "latest_date": emotion_date,
            "fresh": emotion_date == today,
            "gap_days": emotion_gap,
            "note": "由 daily_compute 每日09:00生成，依赖 QUANT_DATA_ROOT 本地量价文件",
        },
        "sector_zt_density": {"latest_date": sector_density_date, "fresh": sector_density_date == today, "gap_days": _gap_days(sector_density_date)},
        "advance_decline":   {"latest_date": advance_decline_date, "fresh": advance_decline_date == today, "gap_days": _gap_days(advance_decline_date)},
        "lianzban_chain":    {"latest_date": lianzban_date, "count": lianzban_count, "fresh": lianzban_date == today},
    }

    # ── 冲突检测（盘前 session 下静态数据是昨天属正常，不触发冲突）
    if is_trade_day and not status["static"]["market_emotion"]["fresh"]:
        conflicts.append(
            f"【数据冲突】zt_pool今日有{zt_count}条（确认交易日），"
            f"但market_emotion最新日期是{emotion_date}（落后{emotion_gap}天）。"
            f"daily_compute可能未运行或QUANT_DATA_ROOT未配置。"
        )

    if is_trade_day and not status["static"]["sector_zt_density"]["fresh"]:
        conflicts.append(
            f"【数据冲突】zt_pool今日有数据，但sector_zt_density最新日期是{sector_density_date}，"
            f"板块密度分析需切换到实时降级推算。"
        )

    if is_weekend and is_trade_day:
        # 节假日补班：周末有数据属特殊情况
        conflicts.append("【提示】日历确认今日为交易日但当前是周末，为节假日补班交易日，请注意。")

    # ── 降级提示：静态数据不可用时如何用实时数据替代
    if not status["static"]["market_emotion"]["fresh"] and is_trade_day:
        fallback_hints["emotion_proxy"] = (
            "market_emotion缺失时，可从zt_pool/dt_pool直接推算市场温度：\n"
            f"  - zt_count = {zt_count}（来自zt_pool实时数据）\n"
            f"  - dt_count = {dt_count}（来自dt_pool实时数据）\n"
            "  - zb_rate = zt_pool中zb_count>0的行数 / zt_pool总行数\n"
            "  - max_lianzban = zt_pool中zt_count的最大值\n"
            "  注意：这是实时快照推算，精度低于日线静态数据，请在结论中标注"
        )

    if not status["static"]["sector_zt_density"]["fresh"] and is_trade_day:
        fallback_hints["sector_proxy"] = (
            "sector_zt_density缺失时，可从zt_pool按sector字段分组统计：\n"
            "  - 同一行业/板块涨停数 ÷ 该行业总股票数（粗估）\n"
            "  - 或直接按sector出现频次排序，找高频板块作为主线线索\n"
            "  注意：这是粗略估算，结论中需标注sector_zt_density数据不可用"
        )

    if not status["realtime"]["lhb_data"]["fresh"] and now_h >= 18:
        fallback_hints["lhb_missing"] = (
            "当前时间已过18:00但龙虎榜仍无今日数据，可能抓取失败。"
            "个股评级应降低置信度，data_gaps中标注'龙虎榜缺失'。"
        )

    SESSION_INSTRUCTIONS = {
        "pre_market": (
            "当前为盘前时段（工作日 06:00–09:30）。"
            "静态数据（market_emotion/sector_zt_density）显示昨日日期属正常——"
            "daily_compute 尚未运行，数据将在 09:30 后刷新。"
            "此时应基于昨日静态数据 + 今日新闻做前瞻判断，"
            "重点关注催化剂质量和昨日情绪延续性，结论中注明'盘前预判'。"
        ),
        "intraday": (
            "当前为盘中时段。"
            "若 static 数据 stale 使用 fallback_hints 降级推算；"
            "若 static 数据 fresh，以 static 为主、realtime 交叉验证。"
        ),
        "post_close": (
            "当前为收盘后（15:00–17:00）。"
            "龙虎榜尚未发布（17:30 后才有），个股评级置信度受限，"
            "data_gaps 中标注'龙虎榜待出'。"
        ),
        "evening": (
            "当前为盘后时段（17:00 后）。"
            "龙虎榜应已发布；若 lhb_data.fresh=false 说明抓取失败，需标注。"
            "静态数据应已由 daily_compute 更新；若仍 stale 说明 QUANT_DATA_ROOT 未配置，"
            "使用 fallback_hints 降级推算。"
        ),
        "pre_open": (
            "当前为交易日开盘前极早时段。静态数据显示昨日日期属正常，处理方式同 pre_market。"
        ),
        "weekend": (
            "当前为周末。无实时交易数据，只能基于新闻/历史静态数据做下周前瞻判断，"
            "结论中明确标注'周末前瞻，数据截至上周五收盘'。"
        ),
        "holiday": (
            "当前为节假日（AKShare 交易日历确认非交易日）。"
            "无实时交易数据，只能基于新闻/历史静态数据做节后前瞻判断，"
            "结论中明确标注'节假日前瞻，数据截至节前最后交易日收盘'。"
        ),
    }

    _out({
        "check_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "today": today,
        "session": session,
        "is_trade_day": is_trade_day,
        "is_weekend": is_weekend,
        "realtime_data_status": status["realtime"],
        "static_data_status": status["static"],
        "conflicts": conflicts,
        "fallback_hints": fallback_hints,
        "instruction": SESSION_INSTRUCTIONS.get(session, ""),
    })


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
    # 字段规范化：将 stock_code/stock_name 映射为 code/name，供 agent 统一访问
    normalized = []
    for row in data:
        normalized.append({
            "code": row.get("stock_code", ""),
            "name": row.get("stock_name", ""),
            "zt_count": row.get("zt_count", 1),
            "sector": row.get("sector", ""),
            "seal_time": row.get("first_zt_time", ""),
            "last_zt_time": row.get("last_zt_time", ""),
            "seal_amount": row.get("seal_amount"),
            "zb_count": row.get("zb_count", 0),
            "turnover_rate": row.get("turnover_rate"),
            "circ_mv": row.get("circ_mv"),
            "trade_date": row.get("trade_date", ""),
        })
    _out(normalized)


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
    "data_health":       cmd_data_health,
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

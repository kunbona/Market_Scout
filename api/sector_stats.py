"""api/sector_stats.py — 板块/市场宽度统计薄路由域 (自 server.py 拆出)。

只读薄路由合集: 板块资金加速 / 成交额异动 / 机构调研热度 / 板块筹码压力 /
板块竞价情绪 / 换手率分层 / 市值分布 / 市场宽度涨跌家数 / 交易日历 /
AI 时段切换。全部纯 DB 读 + _ok/_err 包装, 无模块级状态。

这些路由路径前缀不统一 (sector-*/turnover-*/market-cap-*/advance-decline/
trade-calendar/agent/time-slot), 用 url_prefix="/api" 保路径不变。
"""
from flask import Blueprint, request

from api.common import _computed_date, _err, _ok

bp = Blueprint("sector_stats", __name__, url_prefix="/api")


# ---------------------------------------------------------------------------
# Sector flow acceleration / Volume breakout / Research activity
# ---------------------------------------------------------------------------

@bp.route("/sector-flow-accel")
def api_sector_flow_accel():
    try:
        from db.storage import get_sector_flow_accel
        trade_date = _computed_date()
        rows = get_sector_flow_accel(trade_date)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)

@bp.route("/volume-breakout")
def api_volume_breakout():
    try:
        from db.storage import get_volume_breakout
        trade_date = _computed_date()
        rows = get_volume_breakout(trade_date)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)

@bp.route("/research-activity")
def api_research_activity():
    try:
        from db.storage import get_research_activity
        trade_date = _computed_date()
        rows = get_research_activity(trade_date)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Sector chip pressure / Auction sentiment
# ---------------------------------------------------------------------------

@bp.route("/sector-chip-pressure")
def api_sector_chip_pressure():
    try:
        from db.storage import get_sector_chip_pressure
        trade_date = _computed_date()
        rows = get_sector_chip_pressure(trade_date)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@bp.route("/sector-auction-sentiment")
def api_sector_auction_sentiment():
    try:
        from db.storage import get_sector_auction_sentiment
        trade_date = _computed_date()
        rows = get_sector_auction_sentiment(trade_date)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Turnover stats / Market cap dist / Advance-decline
# ---------------------------------------------------------------------------

@bp.route("/turnover-stats")
def api_turnover_stats():
    try:
        from db.storage import get_turnover_stats
        trade_date = _computed_date()
        data = get_turnover_stats(trade_date)
        return _ok(data)
    except Exception as exc:
        return _err(exc)

@bp.route("/market-cap-dist")
def api_market_cap_dist():
    try:
        from db.storage import get_market_cap_dist
        trade_date = _computed_date()
        data = get_market_cap_dist(trade_date)
        return _ok(data)
    except Exception as exc:
        return _err(exc)

@bp.route("/advance-decline")
def api_advance_decline():
    try:
        from db.storage import get_advance_decline
        trade_date = _computed_date()
        data = get_advance_decline(trade_date)
        return _ok(data)
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Trade calendar / AI time slot
# ---------------------------------------------------------------------------

@bp.route("/trade-calendar/today")
def api_trade_calendar_today():
    try:
        from agent.query import get_market_session
        return _ok(get_market_session())
    except Exception as exc:
        return _err(exc)


@bp.route("/agent/time-slot")
def api_agent_time_slot():
    """
    返回当前应显示的 AI 分析时段按钮高亮状态，以及下次切换时间。

    逻辑：
    - 交易日 00:00–09:15 → morning
    - 交易日 09:15–15:30 → intraday
    - 交易日 15:30–24:00 → evening
    - 非交易日（周末/节假日）→ evening，直到下一个交易日 00:00 切换为 morning
    """
    try:
        from datetime import datetime, timedelta, time as dtime
        from agent.query import _get_trade_calendar

        now = datetime.now()
        today = now.date()
        total_min = now.hour * 60 + now.minute

        try:
            cal = _get_trade_calendar()
            trade_dates = sorted(cal["trade_date"].values)
            is_trade_today = today in trade_dates
        except Exception:
            is_trade_today = today.weekday() < 5
            trade_dates = []

        def _next_trade_date_after(d):
            """返回 d 之后第一个交易日（不含 d）。"""
            for td in trade_dates:
                if td > d:
                    return td
            # fallback：跳过周末往后找
            nxt = d + timedelta(days=1)
            while nxt.weekday() >= 5:
                nxt += timedelta(days=1)
            return nxt

        if is_trade_today:
            if total_min < 9 * 60 + 15:
                slot = "morning"
                # 下次切换：今天 09:15
                next_change = datetime.combine(today, dtime(9, 15))
            elif total_min < 15 * 60 + 30:
                slot = "intraday"
                next_change = datetime.combine(today, dtime(15, 30))
            else:
                slot = "evening"
                nxt = _next_trade_date_after(today)
                next_change = datetime.combine(nxt, dtime(0, 0))
        else:
            slot = "evening"
            nxt = _next_trade_date_after(today)
            next_change = datetime.combine(nxt, dtime(0, 0))

        return _ok({
            "slot": slot,
            "is_trade_today": is_trade_today,
            "next_change_at": next_change.isoformat(),
            "server_time": now.isoformat(),
        })
    except Exception as exc:
        return _err(exc)

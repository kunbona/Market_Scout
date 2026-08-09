"""
QMT 数据 API（统一入口）。

设计：
- 检测 QMT_BRIDGE_URL 环境变量，配置了就走远端桥（VM 上的 qmt-bridge 服务）
- 没配置就回退到本地 xtquant（适合同机部署或开发）
- 两套路径对调用方完全透明：所有内部 xtdata 调用都走 _xt() shim

调用方（如 fetcher.qmt_monitors、quant.security_meta）零改动，行为语义跟以前一致：
- QMT 不可用 → 返回 {} / [] / None
- 已有 _is_enabled() / qmt_connect() 等兼容接口保留
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from fetcher import qmt_client

logger = logging.getLogger(__name__)

# ─── 本地 xtquant：bridge 模式下根本不会用，延迟导入是历史兼容 ───────────────

try:
    from xtquant import xtdata
except Exception:  # pragma: no cover - 运行时依赖
    xtdata = None


# ─── 运行时切换：每次调用都检查 env，允许设置页改完即时生效 ──────────────────

def _use_bridge() -> bool:
    return qmt_client.is_configured()


def _xt(method: str, *args: Any, **kwargs: Any) -> Any:
    """
    统一 xtdata 调用 shim。
    - bridge 配置 → HTTP 调远端
    - 否则 → 本地 xtquant
    任何失败（网络/超时/未装/白名单拒绝）一律返回 None。
    """
    if _use_bridge():
        return qmt_client._call(method, *args, **kwargs)
    if xtdata is None or not qmt_connect():
        return None
    fn = getattr(xtdata, method, None)
    if fn is None or not callable(fn):
        return None
    try:
        return fn(*args, **kwargs)
    except Exception:
        return None


# ─── 辅助函数（清洗，跟 transport 无关） ────────────────────────────────────

QMT_CALENDAR_MARKETS = ("SH", "SZ")


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _normalize_trade_date_text(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("empty trade date")
    if len(raw) >= 19:
        return datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d")
    if len(raw) == 10 and raw.count("-") == 2:
        return datetime.strptime(raw, "%Y-%m-%d").strftime("%Y-%m-%d")
    if len(raw) == 14 and raw.isdigit():
        return datetime.strptime(raw, "%Y%m%d%H%M%S").strftime("%Y-%m-%d")
    if len(raw) >= 8 and raw[:8].isdigit():
        return datetime.strptime(raw[:8], "%Y%m%d").strftime("%Y-%m-%d")
    raise ValueError(f"unsupported trade date value: {value}")


def _normalize_qmt_trade_date(raw_date: Any) -> str:
    if isinstance(raw_date, datetime):
        return raw_date.strftime("%Y-%m-%d")
    if isinstance(raw_date, date):
        return raw_date.strftime("%Y-%m-%d")
    if isinstance(raw_date, str):
        return _normalize_trade_date_text(raw_date)
    if isinstance(raw_date, (int, float)):
        timestamp = float(raw_date)
        if timestamp > 10_000_000_000:
            timestamp /= 1000.0
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
    raise ValueError(f"unsupported qmt trade date type: {type(raw_date)!r}")


def _normalize_today(today: str | None = None) -> str:
    if today:
        return _normalize_trade_date_text(today)
    return date.today().strftime("%Y-%m-%d")


# ─── 公开 API ───────────────────────────────────────────────────────────────

def qmt_connect() -> bool:
    """
    兼容旧接口：返回 QMT 是否可用。
    - 本地模式：调 xtdata.connect() 试一下
    - bridge 模式：返回 True（具体 alive 状态由 _xt() 实际调用判断，失败返 None）
    """
    if _use_bridge():
        return True
    if xtdata is None:
        return False
    try:
        xtdata.connect()
        return True
    except Exception:
        return False


def get_recent_qmt_trading_dates(today: str | None = None, lookback_days: int = 60) -> list[str]:
    normalized_today = _normalize_today(today)
    # 提前探测：bridge 模式靠 _xt 内部 try/except 即可，不需要预连
    if not _use_bridge() and (xtdata is None or not qmt_connect()):
        return []

    end_date = datetime.strptime(normalized_today, "%Y-%m-%d").date()
    start_date = end_date - timedelta(days=max(lookback_days, 1))
    trade_dates: set[str] = set()

    for market in QMT_CALENDAR_MARKETS:
        raw_dates = _xt(
            "get_trading_dates",
            market,
            start_date.strftime("%Y%m%d"),
            end_date.strftime("%Y%m%d"),
        ) or []
        for raw_date in raw_dates:
            try:
                normalized = _normalize_qmt_trade_date(raw_date)
            except (TypeError, ValueError, OSError):
                continue
            if normalized <= normalized_today:
                trade_dates.add(normalized)

    return sorted(trade_dates)


def get_current_qmt_trade_date(today: str | None = None) -> str:
    normalized_today = _normalize_today(today)
    trade_dates = get_recent_qmt_trading_dates(normalized_today)
    if trade_dates:
        return trade_dates[-1]
    return normalized_today


def list_a_shares(sector_name: str = "沪深A股") -> list[str]:
    if not _use_bridge() and (xtdata is None or not qmt_connect()):
        return []
    codes = _xt("get_stock_list_in_sector", sector_name) or []
    return [str(code).strip() for code in codes if str(code).strip()]


def get_full_tick_snapshot(codes: list[str]) -> dict[str, dict[str, Any]]:
    if not codes:
        return {}
    if not _use_bridge() and (xtdata is None or not qmt_connect()):
        return {}
    raw_ticks = _xt("get_full_tick", codes) or {}

    result: dict[str, dict[str, Any]] = {}
    for code in codes:
        tick = raw_ticks.get(code) or {}
        result[code] = {
            "stock_code": code,
            "last_price": _to_float(tick.get("lastPrice") or tick.get("last_price")),
            "last_close": _to_float(tick.get("lastClose") or tick.get("last_close")),
            "open": _to_float(tick.get("open")),
            "high": _to_float(tick.get("high")),
            "low": _to_float(tick.get("low")),
            "volume": _to_float(tick.get("volume")),
            "amount": _to_float(tick.get("amount")),
            "bid_price": _to_float(tick.get("bidPrice") or tick.get("bid_price")),
            "ask_price": _to_float(tick.get("askPrice") or tick.get("ask_price")),
            "raw": tick,
        }
    return result


def get_market_bars(
    codes: list[str],
    period: str,
    count: int = -1,
    dividend_type: str = "none",
    fields: list[str] | None = None,
) -> dict[str, Any]:
    if not codes:
        return {}
    if not _use_bridge() and (xtdata is None or not qmt_connect()):
        return {}
    field_list = fields or ["time", "open", "high", "low", "close", "volume", "amount"]
    return _xt(
        "get_market_data",
        field_list=field_list,
        stock_list=codes,
        period=period,
        count=count,
        dividend_type=dividend_type,
        fill_data=True,
    ) or {}


def get_security_detail_fallback(code: str) -> dict[str, Any]:
    if not code:
        return {}
    if not _use_bridge() and (xtdata is None or not qmt_connect()):
        return {}
    detail = _xt("get_instrument_detail", code) or {}

    return {
        "stock_code": code,
        "stock_name": str(detail.get("InstrumentName") or detail.get("stock_name") or "").strip(),
        "last_close": detail.get("PreClose"),
        "up_limit": detail.get("UpStopPrice"),
        "down_limit": detail.get("DownStopPrice"),
        "status": detail.get("InstrumentStatus"),
        "raw": detail,
    }

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


def with_market_suffix(code: str) -> str:
    """
    给纯 6 位股票代码补市场后缀 (.SH / .SZ / .BJ)。

    规则（按 A 股惯例）:
    - 已有后缀 (.SH / .SZ / .BJ / .sh / .sz / .bj) → 原样返回
    - 60xxxx / 68xxxx / 90xxxx → 上交所 SH
    - 00xxxx / 30xxxx → 深交所 SZ
    - 8xxxxx / 43xxxx / 92xxxx → 北交所 BJ
    - 非法输入 → 原样返回 (上层 _xt 拿到空 dict 兜底)
    """
    raw = (code or "").strip()
    if not raw:
        return raw
    if "." in raw:
        return raw
    if not raw.isdigit() or len(raw) != 6:
        return raw
    head2 = raw[:2]
    head3 = raw[:3]
    if head3 in {"600", "601", "603", "605", "688", "689", "900"}:
        return f"{raw}.SH"
    if head3 in {"000", "001", "002", "003", "300", "301"} or head2 == "15":
        return f"{raw}.SZ"
    if head3 in {"400", "420", "430", "830", "831", "836", "837", "838", "839",
                 "870", "871", "872", "873", "874", "875", "876", "877", "878",
                 "920", "921", "922", "923", "924", "925", "926", "927", "928",
                 "929", "930", "931"} or head2 in {"83", "87", "43"}:
        return f"{raw}.BJ"
    return raw


def get_full_tick_snapshot(codes: list[str]) -> dict[str, dict[str, Any]]:
    """
    拿 QMT 实时 tick 快照。

    自动给纯 6 位 code 补 .SH/.SZ/.BJ 后缀（xtdata 不认裸 code，会返 {}）。
    返回的 key 用入参 code 形式（裸 / 带后缀 都原样保留），方便调用方按入参索引。
    """
    if not codes:
        return {}
    if not _use_bridge() and (xtdata is None or not qmt_connect()):
        return {}
    # 补后缀（idempotent，已有 .SH/.SZ 不动）
    enriched = [with_market_suffix(c) for c in codes]
    raw_ticks = _xt("get_full_tick", enriched) or {}

    result: dict[str, dict[str, Any]] = {}
    for original_code, queried_code in zip(codes, enriched):
        tick = raw_ticks.get(queried_code) or {}
        result[original_code] = {
            "stock_code": original_code,
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


def get_qmt_close_window(
    codes: list[str],
    period: str = "1d",
    days_back: int = 14,
) -> dict[str, list[float]]:
    """
    全市场一次拉 14 天日 K close（升序, 最新在末尾），用于算 MA10 等指标。

    实测 (2026-08-12, 全市场 5208 只, period='1d', 14 天 7/29-8/11):
    - download_history_data2: 0.0s (VM xtquant cache 已齐, 触发秒返)
    - get_market_data: 0.5s 拿 52022/52080 (99.9% 命中, 平均 10.0 天/只)
    - 总耗时 < 1s, bridge 60s timeout 远远够

    实现参考 quant-data-d1-main/vendors/qmt/qmt_min.py QmtMinuteReader:
    1. download_history_data2(stock_list=, period=, start_time=, end_time=)
       - 批量下载 (DM-kun qmt_min.py:174, 注释 qmt_tick.py:184-198 说返回值不可靠, 失败靠异常)
    2. get_market_data(field_list=, stock_list=, period=, start_time=, end_time=)
       - 读 VM 本地缓存 (DM-kun qmt_min.py:181-190 标准签名, 不用 count 用 start_time/end_time)
    3. bridge _sanitize 用 to_dict(orient="records") 转 list[dict]
       - 索引对齐 codes 顺序 (DataFrame.index 经 JSON 序列化丢失, 依赖 xtquant 稳定性)

    失败（白名单/网络/VM 不可用）→ 返 {}，调用方降级到 CSV。
    不会抛异常，不污染调用方。
    """
    if not codes:
        return {}
    if not _use_bridge() and (xtdata is None or not qmt_connect()):
        return {}

    from datetime import date, timedelta
    end_date = date.today().strftime("%Y%m%d")
    start_date = (date.today() - timedelta(days=days_back)).strftime("%Y%m%d")

    # 阶段 1: 触发下载 (DM-kun qmt_min.py:174)
    _xt(
        "download_history_data2",
        stock_list=codes,
        period=period,
        start_time=start_date,
        end_time=end_date,
    )
    # 阶段 2: 读本地缓存 (DM-kun qmt_min.py:181-190)
    raw = _xt(
        "get_market_data",
        field_list=["time", "close"],
        stock_list=codes,
        period=period,
        start_time=start_date,
        end_time=end_date,
    ) or {}

    # 解析: {field: [row_dict, ...]} list 长度 = len(codes), 顺序对齐
    close_list = raw.get("close") or []
    time_list = raw.get("time") or []
    n = min(len(close_list), len(time_list), len(codes))
    result: dict[str, list[float]] = {}
    for i in range(n):
        code = codes[i]
        close_dict = close_list[i] if isinstance(close_list[i], dict) else {}
        time_dict = time_list[i] if isinstance(time_list[i], dict) else {}
        if not close_dict or not time_dict:
            continue
        common_dates = [d for d in time_dict.keys() if d in close_dict]
        try:
            sorted_dates = sorted(
                common_dates,
                key=lambda d: int(time_dict[d]) if isinstance(time_dict[d], (int, float)) else 0,
            )
        except (TypeError, ValueError):
            sorted_dates = common_dates
        closes: list[float] = []
        for d in sorted_dates:
            try:
                c = float(close_dict.get(d, 0))
                if c == c and c > 0:  # 排除 NaN
                    closes.append(c)
            except (TypeError, ValueError):
                continue
        if len(closes) >= 3:
            result[code] = closes
    return result


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

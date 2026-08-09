from __future__ import annotations

from typing import TypedDict

from fetcher.qmt_data_api import get_security_detail_fallback


class SecurityMeta(TypedDict):
    stock_code: str
    stock_name: str
    sector: str
    industry_l1: str
    industry_l2: str
    industry_l3: str
    is_st: bool


def _normalize_code(code: str) -> str:
    raw = str(code or "").strip().upper()
    if "." in raw:
        return raw
    if raw.startswith(("6", "9")):
        return f"{raw}.SH"
    if raw.startswith(("4", "8", "2")):
        return f"{raw}.BJ"
    return f"{raw}.SZ"


def _is_st_name(name: str) -> bool:
    return "ST" in str(name or "").upper()


def _load_local_security_meta(code: str) -> dict[str, str]:
    try:
        from quant.loader import get_trading_data
    except Exception:
        return {}

    raw_code = str(code or "").split(".", 1)[0].strip()
    if not raw_code:
        return {}

    try:
        df = get_trading_data(raw_code)
    except Exception:
        return {}

    if df is None or df.empty:
        return {}

    last_row = df.iloc[-1]
    return {
        "stock_name": str(last_row.get("name", "") or "").strip(),
        "sector": str(last_row.get("industry_l1", "") or "").strip(),
        "industry_l1": str(last_row.get("industry_l1", "") or "").strip(),
        "industry_l2": str(last_row.get("industry_l2", "") or "").strip(),
        "industry_l3": str(last_row.get("industry_l3", "") or "").strip(),
    }


def get_security_meta(code: str) -> SecurityMeta:
    stock_code = _normalize_code(code)
    local_meta = _load_local_security_meta(stock_code)
    # 本地 parquet 已有名字就跳过远端 detail 调用（5000 只股 × 1 次 HTTP = 250s，
    # 是 fetch_dt_pool_v3 卡死的根因；本地没名字的极少数才 fallback 到 bridge）
    if local_meta.get("stock_name"):
        qmt_detail = {}
    else:
        qmt_detail = get_security_detail_fallback(stock_code)

    stock_name = local_meta.get("stock_name") or str(qmt_detail.get("stock_name") or "").strip()
    sector = local_meta.get("sector") or ""
    industry_l1 = local_meta.get("industry_l1") or sector
    industry_l2 = local_meta.get("industry_l2") or ""
    industry_l3 = local_meta.get("industry_l3") or ""

    return {
        "stock_code": stock_code,
        "stock_name": stock_name,
        "sector": sector,
        "industry_l1": industry_l1,
        "industry_l2": industry_l2,
        "industry_l3": industry_l3,
        "is_st": _is_st_name(stock_name),
    }

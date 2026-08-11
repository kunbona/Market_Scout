import logging
from datetime import date, datetime

import akshare as ak
import requests

from db.storage import (
    clear_dt_pool,
    clear_dt_pool_v2,
    clear_strong_pool,
    clear_zbgc_pool,
    clear_zt_pool,
    insert_dt_pool,
    insert_dt_pool_v2,
    replace_dt_pool_v3,
    insert_sector_flow,
    insert_strong_pool,
    insert_zbgc_pool,
    insert_zt_pool,
)
from fetcher.qmt_data_api import (
    get_current_qmt_trade_date,
    get_full_tick_snapshot,
    list_a_shares,
)
from fetcher.xtquant_breadth import connect
from fetcher.xtquant_limit_down import compute_down_limit
from quant.security_meta import get_security_meta

logger = logging.getLogger(__name__)
EASTMONEY_DT_POOL_URL = "https://push2ex.eastmoney.com/getTopicDTPool"


def _resolve_qmt_trade_day(target_trade_date: str | None = None) -> tuple[str, str]:
    if target_trade_date:
        value = str(target_trade_date).strip()[:10]
        return value.replace("-", ""), value

    try:
        trade_day = get_current_qmt_trade_date()
        return trade_day.replace("-", ""), trade_day
    except Exception:
        pass

    trade_day = date.today()
    return trade_day.strftime("%Y%m%d"), trade_day.strftime("%Y-%m-%d")


def _load_local_stock_meta(code: str) -> tuple[str, str]:
    try:
        from quant.loader import get_trading_data
    except Exception:
        return "", ""

    raw_code = str(code or "").strip()
    if "." in raw_code:
        raw_code = raw_code.split(".", 1)[0]
    if not raw_code:
        return "", ""

    try:
        df = get_trading_data(raw_code)
    except Exception:
        return "", ""
    if df is None or df.empty:
        return "", ""

    last_row = df.iloc[-1]
    stock_name = str(last_row.get("name", "") or "").strip()
    sector = str(last_row.get("industry_l1", "") or "").strip()
    return stock_name, sector


def _is_st_stock_name(stock_name: str) -> bool:
    return "ST" in str(stock_name or "").upper()


def _normalize_monitor_row(row: dict) -> dict:
    stock_name = str(row.get("stock_name") or "").strip()
    sector = str(row.get("sector") or "").strip()
    last_price = row.get("last_price")
    last_close = row.get("last_close")
    down_limit = row.get("down_limit")

    try:
        last_price = float(last_price) if last_price is not None else None
    except (TypeError, ValueError):
        last_price = None
    try:
        last_close = float(last_close) if last_close is not None else None
    except (TypeError, ValueError):
        last_close = None
    try:
        down_limit = float(down_limit) if down_limit is not None else None
    except (TypeError, ValueError):
        down_limit = None

    is_st = _is_st_stock_name(stock_name)
    limit_gap_pct = None
    if last_price is not None and down_limit not in (None, 0):
        limit_gap_pct = (last_price / down_limit - 1) * 100

    tags = ["ST" if is_st else "非ST", "跌停"]
    return {
        "stock_code": str(row.get("stock_code") or "").strip(),
        "stock_name": stock_name,
        "sector": sector,
        "trade_date": str(row.get("trade_date") or "").strip(),
        "last_price": last_price,
        "last_close": last_close,
        "down_limit": down_limit,
        "limit_gap_pct": limit_gap_pct,
        "is_st": is_st,
        "tags": tags,
    }


def build_qmt_limit_down_monitor_payload(rows: list[dict]) -> dict:
    items = [_normalize_monitor_row(row) for row in rows]
    sector_counts: dict[str, int] = {}
    for item in items:
        sector = item["sector"]
        if not sector:
            continue
        sector_counts[sector] = sector_counts.get(sector, 0) + 1

    industry_distribution = [
        {"sector": sector, "count": count}
        for sector, count in sorted(sector_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    st_count = sum(1 for item in items if item["is_st"])
    return {
        "summary": {
            "total_count": len(items),
            "st_count": st_count,
            "non_st_count": len(items) - st_count,
            "industry_count": len(industry_distribution),
        },
        "industry_distribution": industry_distribution,
        "items": items,
    }


def build_qmt_industry_draggers_payload(rows: list[dict]) -> dict:
    items = [_normalize_monitor_row(row) for row in rows]
    grouped: dict[str, list[dict]] = {}
    for item in items:
        sector = item["sector"] or ""
        grouped.setdefault(sector, []).append(item)

    result_items = []
    for sector, group in grouped.items():
        st_count = sum(1 for item in group if item["is_st"])
        limit_down_count = len(group)
        lead = group[0]
        result_items.append({
            "sector": sector,
            "limit_down_count": limit_down_count,
            "st_count": st_count,
            "non_st_count": limit_down_count - st_count,
            "lead_stock_code": lead["stock_code"],
            "lead_stock_name": lead["stock_name"],
            "drag_score": limit_down_count + st_count * 0.2,
        })

    result_items.sort(key=lambda item: (-item["limit_down_count"], -item["st_count"], item["sector"]))
    top = result_items[0] if result_items else None
    return {
        "summary": {
            "sector_count": len(result_items),
            "top_dragger_sector": top["sector"] if top else None,
            "top_dragger_limit_down_count": top["limit_down_count"] if top else 0,
        },
        "items": result_items,
    }


def fetch_zt_pool() -> None:
    try:
        trade_date = date.today().strftime("%Y%m%d")
        db_date = date.today().strftime("%Y-%m-%d")
        df = ak.stock_zt_pool_em(date=trade_date)
        clear_zt_pool(db_date)

        col_code       = next((c for c in df.columns if "代码" in c), None)
        col_name       = next((c for c in df.columns if "名称" in c), None)
        col_count      = next((c for c in df.columns if c == "连板数"), None) or \
                         next((c for c in df.columns if "连板" in c or "连续" in c), None)
        col_time       = next((c for c in df.columns if "首次" in c), None)
        col_sector     = next((c for c in df.columns if "行业" in c or "板块" in c or "概念" in c), None)
        # 新增字段列名探测
        col_last_time   = next((c for c in df.columns if "最后" in c), None)
        col_seal_amount = next((c for c in df.columns if "封板资金" in c), None)
        col_zb_count    = next((c for c in df.columns if "炸板" in c), None)
        col_turnover    = next((c for c in df.columns if "换手率" in c), None)
        col_circ_mv     = next((c for c in df.columns if "流通市值" in c), None)

        for _, row in df.iterrows():
            stock_code = str(row[col_code]).strip() if col_code else ""
            stock_name = str(row[col_name]).strip() if col_name else ""
            try:
                zt_count = int(row[col_count]) if col_count else 1
            except (ValueError, TypeError):
                zt_count = 1
            first_zt_time = str(row[col_time]).strip() if col_time else ""
            sector = str(row[col_sector]).strip() if col_sector else ""
            # 新增字段提取，做好类型转换保护
            last_zt_time = str(row[col_last_time]).strip() if col_last_time else ""
            try:
                seal_amount = float(row[col_seal_amount]) if col_seal_amount else None
            except (ValueError, TypeError):
                seal_amount = None
            try:
                zb_count = int(row[col_zb_count]) if col_zb_count else 0
            except (ValueError, TypeError):
                zb_count = 0
            try:
                turnover_rate = float(row[col_turnover]) if col_turnover else None
            except (ValueError, TypeError):
                turnover_rate = None
            try:
                circ_mv = float(row[col_circ_mv]) if col_circ_mv else None
            except (ValueError, TypeError):
                circ_mv = None
            insert_zt_pool(db_date, stock_code, stock_name, zt_count, first_zt_time, sector,
                           last_zt_time=last_zt_time, seal_amount=seal_amount,
                           zb_count=zb_count, turnover_rate=turnover_rate, circ_mv=circ_mv)
    except Exception as e:
        logger.warning(f"[sector_heat] fetch_zt_pool failed: {e}")


def fetch_dt_pool() -> None:
    # stock_dt_pool_em 已废弃，改用 stock_zt_pool_dtgc_em（跌停股池）
    try:
        trade_date = date.today().strftime("%Y%m%d")
        db_date = date.today().strftime("%Y-%m-%d")
        df = ak.stock_zt_pool_dtgc_em(date=trade_date)
        clear_dt_pool(db_date)
        if df is None or df.empty:
            return

        col_code   = next((c for c in df.columns if "代码" in c), None)
        col_name   = next((c for c in df.columns if "名称" in c), None)
        col_time   = next((c for c in df.columns if "最后封板" in c or "首次" in c or "时间" in c), None)
        col_sector = next((c for c in df.columns if "行业" in c or "板块" in c), None)

        for _, row in df.iterrows():
            stock_code    = str(row[col_code]).strip() if col_code else ""
            stock_name    = str(row[col_name]).strip() if col_name else ""
            first_dt_time = str(row[col_time]).strip() if col_time else ""
            sector        = str(row[col_sector]).strip() if col_sector else ""
            insert_dt_pool(db_date, stock_code, stock_name, first_dt_time, sector)
    except Exception as e:
        logger.warning(f"[sector_heat] fetch_dt_pool failed: {e}")


def fetch_dt_pool_v2() -> None:
    try:
        trade_day = date.today()
        trade_date = trade_day.strftime("%Y%m%d")
        db_date = trade_day.strftime("%Y-%m-%d")
        params = {
            "ut": "7eea3edcaed734bea9cbfc24409ed989",
            "dpt": "wz.ztzt",
            "Pageindex": "0",
            "pagesize": "20",
            "sort": "fund:asc",
            "date": trade_date,
        }
        headers = {
            "Referer": "https://quote.eastmoney.com/ztb/detail#type=dtgc",
            "User-Agent": "Mozilla/5.0",
        }
        response = requests.get(EASTMONEY_DT_POOL_URL, params=params, headers=headers, timeout=20)
        response.raise_for_status()
        payload = response.json()
        pool = ((payload.get("data") or {}).get("pool") or [])

        clear_dt_pool_v2(db_date)
        for row in pool:
            insert_dt_pool_v2(
                db_date,
                str(row.get("c", "")).strip(),
                str(row.get("n", "")).strip(),
                str(row.get("lbt", "")).strip(),
                str(row.get("hybk", "")).strip(),
            )
    except Exception as e:
        logger.warning(f"[sector_heat] fetch_dt_pool_v2 failed: {e}")


def fetch_dt_pool_v3(target_trade_date: str | None = None) -> None:
    try:
        if not connect():
            return

        trade_date, db_date = _resolve_qmt_trade_day(target_trade_date)
        stock_list = list_a_shares()
        if not stock_list:
            return

        ticks = get_full_tick_snapshot(stock_list)
        if not ticks:
            return

        # 第一遍：用 tick 里的 (last_price, last_close) 粗筛候选股，避免对 5000 只全调
        # get_security_meta（每个调一次 bridge，5000 × 50ms = 250s 必超时）
        # 粗判阈值 0.95：所有板块里跌停价 / 昨收的最大值就是 0.95（主板 ST 新规后）
        # 任何 last_price ≤ last_close * 0.95 的票才可能是跌停
        candidates: list[str] = []
        for code, tick in ticks.items():
            last_price = tick.get("last_price")
            last_close = tick.get("last_close")
            if not last_price or not last_close:
                continue
            if last_price <= last_close * 0.95:
                candidates.append(code)

        if not candidates:
            replace_dt_pool_v3(db_date, [])
            return

        # 第二遍：只对候选股（通常 0-几十只）调 get_security_meta 拿名字/行业
        # 精筛条件改为 abs(last_price - down_limit) ≤ 0.011（允许 1 分钱误差）
        # 修复前: 只过滤 last_price > down_limit 的, 会漏掉 last_price < down_limit 的
        # 跌穿跌停价 (按 A 股规则不存在) — 大概率是 QMT tick 脏数据 / 昨收错位 / 停牌瞬间
        rows_to_replace: list[dict] = []
        for code in candidates:
            tick = ticks.get(code, {})
            last_price = tick.get("last_price")
            last_close = tick.get("last_close")
            meta = get_security_meta(code)
            stock_name = meta["stock_name"]
            sector = meta["sector"]
            down_limit = compute_down_limit(code, stock_name, last_close, trade_date)
            if down_limit is None or down_limit <= 0:
                continue
            # ratio check (兼容新旧规则 + 抗脏数据):
            # - 主板普通股 10% 跌停: last_price / down_limit ≈ 1.0
            # - 主板 ST 旧规则 5% 跌停: down_limit = 0.95*lc, last_price ≈ 0.95*lc, ratio ≈ 1.0
            # - 主板 ST 新规则 10% 跌停 (2026-07-06): down_limit = 0.9*lc, last_price ≈ 0.9*lc, ratio ≈ 1.0
            # - 跌穿 (脏数据/异常): ratio < 0.9 → 挡
            # - 没跌停 (现价 > 跌停价): ratio > 1.01 → 挡
            ratio = last_price / down_limit
            if ratio < 0.9 or ratio > 1.01:
                continue  # 不是真跌停, 脏数据丢掉
            rows_to_replace.append(
                {
                    "stock_code": code,
                    "stock_name": stock_name,
                    "last_price": last_price,
                    "last_close": last_close,
                    "down_limit": down_limit,
                    "sector": sector,
                }
            )
        replace_dt_pool_v3(db_date, rows_to_replace)
    except Exception as e:
        logger.warning(f"[sector_heat] fetch_dt_pool_v3 failed: {e}")


def fetch_concept_heat() -> None:
    """
    概念板块涨跌幅 + 资金流。
    降级策略：stock_board_concept_name_em 走 push2.eastmoney.com，
    服务器 IP 被封时静默跳过，不写入数据，不刷 warning。
    """
    try:
        fetch_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        df = ak.stock_board_concept_name_em()

        col_name = next((c for c in df.columns if "板块" in c or "名称" in c or "概念" in c), None)
        col_change = next((c for c in df.columns if "涨跌幅" in c or "涨幅" in c), None)
        col_inflow = next((c for c in df.columns if "主力净流入" in c or "净流入" in c), None)
        col_inflow_pct = next((c for c in df.columns if "主力净流入占比" in c or "净占比" in c), None)

        for _, row in df.iterrows():
            sector_name = str(row[col_name]).strip() if col_name else ""
            try:
                change_pct = float(row[col_change]) if col_change else 0.0
            except (ValueError, TypeError):
                change_pct = 0.0
            try:
                main_inflow = float(row[col_inflow]) if col_inflow else 0.0
            except (ValueError, TypeError):
                main_inflow = 0.0
            try:
                main_inflow_pct = float(row[col_inflow_pct]) if col_inflow_pct else 0.0
            except (ValueError, TypeError):
                main_inflow_pct = 0.0
            insert_sector_flow(fetch_time, sector_name, change_pct, main_inflow, main_inflow_pct, source_type="concept")
    except Exception as e:
        # push2.eastmoney.com 在服务器 IP 上被封，静默降级（不刷 warning）
        logger.debug("[sector_heat] fetch_concept_heat 跳过（东财 push2 不可用）: %s", e)


def fetch_zbgc_pool() -> None:
    try:
        trade_date = date.today().strftime("%Y%m%d")
        db_date = date.today().strftime("%Y-%m-%d")
        df = ak.stock_zt_pool_zbgc_em(date=trade_date)
        clear_zbgc_pool(db_date)
        if df is None or df.empty:
            return
        col_code  = next((c for c in df.columns if "代码" in c), None)
        col_name  = next((c for c in df.columns if "名称" in c), None)
        col_time  = next((c for c in df.columns if "首次" in c), None)
        col_zb    = next((c for c in df.columns if "炸板" in c), None)
        col_amp   = next((c for c in df.columns if "振幅" in c), None)
        col_sec   = next((c for c in df.columns if "行业" in c or "板块" in c), None)
        for _, row in df.iterrows():
            def _s(c): return str(row[c]).strip() if c else ""
            def _f(c):
                try: return float(row[c]) if c else None
                except: return None
            def _i(c):
                try: return int(row[c]) if c else 0
                except: return 0
            insert_zbgc_pool(db_date, _s(col_code), _s(col_name),
                             _s(col_time), _i(col_zb), _f(col_amp), _s(col_sec))
    except Exception as e:
        logger.warning(f"[sector_heat] fetch_zbgc_pool failed: {e}")


def fetch_strong_pool() -> None:
    try:
        trade_date = date.today().strftime("%Y%m%d")
        db_date = date.today().strftime("%Y-%m-%d")
        df = ak.stock_zt_pool_strong_em(date=trade_date)
        clear_strong_pool(db_date)
        if df is None or df.empty:
            return
        col_code   = next((c for c in df.columns if "代码" in c), None)
        col_name   = next((c for c in df.columns if "名称" in c), None)
        col_pct    = next((c for c in df.columns if "涨跌幅" in c), None)
        col_high   = next((c for c in df.columns if "新高" in c), None)
        col_vr     = next((c for c in df.columns if "量比" in c), None)
        col_reason = next((c for c in df.columns if "理由" in c or "入选" in c), None)
        col_sec    = next((c for c in df.columns if "行业" in c or "板块" in c), None)
        for _, row in df.iterrows():
            def _s(c): return str(row[c]).strip() if c else ""
            def _f(c):
                try: return float(row[c]) if c else None
                except: return None
            insert_strong_pool(db_date, _s(col_code), _s(col_name),
                               _f(col_pct), _s(col_high), _f(col_vr),
                               _s(col_reason), _s(col_sec))
    except Exception as e:
        logger.warning(f"[sector_heat] fetch_strong_pool failed: {e}")

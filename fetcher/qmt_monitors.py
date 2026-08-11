from __future__ import annotations

import os
from datetime import date

import pandas as pd

from fetcher.qmt_data_api import (
    get_full_tick_snapshot,
    get_qmt_close_window,
    list_a_shares,
)
from fetcher.xtquant_limit_down import compute_down_limit, compute_up_limit
from quant.loader import get_trading_data


def _is_st_stock_name(stock_name: str) -> bool:
    return "ST" in str(stock_name or "").upper()


def _to_float_or_none(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def normalize_limit_down_row(row: dict) -> dict:
    stock_name = str(row.get("stock_name") or "").strip()
    sector = str(row.get("sector") or "").strip()
    last_price = _to_float_or_none(row.get("last_price"))
    last_close = _to_float_or_none(row.get("last_close"))
    down_limit = _to_float_or_none(row.get("down_limit"))

    is_st = _is_st_stock_name(stock_name)
    limit_gap_pct = None
    if last_price is not None and down_limit not in (None, 0):
        limit_gap_pct = (last_price / down_limit - 1) * 100

    return {
        "stock_code": str(row.get("stock_code") or "").strip(),
        "stock_name": stock_name,
        "sector": sector,
        "last_price": last_price,
        "last_close": last_close,
        "down_limit": down_limit,
        "limit_gap_pct": limit_gap_pct,
        "is_st": is_st,
        "tags": ["ST" if is_st else "非ST", "跌停"],
    }


def build_qmt_limit_down_monitor_payload(rows: list[dict]) -> dict:
    items = [normalize_limit_down_row(row) for row in rows]
    sector_counts: dict[str, int] = {}
    for item in items:
        sector = item["sector"]
        if not sector:
            continue
        sector_counts[sector] = sector_counts.get(sector, 0) + 1

    industry_distribution = [
        {"sector": sector, "count": count}
        for sector, count in sorted(sector_counts.items(), key=lambda value: (-value[1], value[0]))
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
    items = [normalize_limit_down_row(row) for row in rows]
    grouped: dict[str, list[dict]] = {}
    for item in items:
        sector = item["sector"] or ""
        grouped.setdefault(sector, []).append(item)

    result_items = []
    for sector, group in grouped.items():
        st_count = sum(1 for item in group if item["is_st"])
        limit_down_count = len(group)
        lead = group[0]
        result_items.append(
            {
                "sector": sector,
                "limit_down_count": limit_down_count,
                "st_count": st_count,
                "non_st_count": limit_down_count - st_count,
                "lead_stock_code": lead["stock_code"],
                "lead_stock_name": lead["stock_name"],
                "drag_score": round(limit_down_count + st_count * 0.2, 2),
            }
        )

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


def normalize_industry_stats_row(row: dict) -> dict | None:
    sector = str(row.get("sector") or "").strip()
    if not sector:
        return None

    try:
        last_price = float(row.get("last_price"))
        last_close = float(row.get("last_close"))
        ma10_csv = float(row.get("ma10_csv"))
        up_limit = float(row.get("up_limit"))
        down_limit = float(row.get("down_limit"))
    except (TypeError, ValueError):
        return None

    # QMT 备选 ma10: 阶段 1.5 拉到的 QMT 14 天 K 线平均, None 表示拉不到
    # KPI 卡 ma10_qmt_count 用这个字段统计"VM 断了能 fallback 多少票"
    ma10_qmt_raw = row.get("ma10_qmt")
    ma10_qmt: float | None = None
    if ma10_qmt_raw is not None:
        try:
            ma10_qmt = float(ma10_qmt_raw)
        except (TypeError, ValueError):
            ma10_qmt = None

    try:
        ma10_realtime = float(row.get("ma10_realtime") or ma10_csv)
    except (TypeError, ValueError):
        ma10_realtime = ma10_csv

    try:
        yesterday_main_inflow = float(row.get("yesterday_main_inflow") or 0.0)
    except (TypeError, ValueError):
        yesterday_main_inflow = 0.0

    try:
        today_main_inflow = float(row.get("today_main_inflow") or 0.0)
    except (TypeError, ValueError):
        today_main_inflow = 0.0

    change_pct = (last_price / last_close - 1) * 100 if last_close > 0 else None
    # above_ma10 (实时): tick 价 > 实时 ma10 = (前9天CSV + today_tick) / 10，今天盘口对均线位置
    # above_ma10_csv (昨收): 昨收 > CSV 10日均线，对比昨天截面时的位置
    return {
        "stock_code": str(row.get("stock_code") or "").strip(),
        "stock_name": str(row.get("stock_name") or "").strip(),
        "sector": sector,
        "last_price": last_price,
        "last_close": last_close,
        "ma10_csv": ma10_csv,
        "ma10_realtime": ma10_realtime,
        "up_limit": up_limit,
        "down_limit": down_limit,
        "change_pct": change_pct,
        "above_ma10": last_price > ma10_realtime,
        "above_ma10_csv": last_close > ma10_csv,
        "is_meat": change_pct is not None and change_pct >= 5,
        "is_big_loss": change_pct is not None and change_pct <= -5,
        "is_limit_up": last_price >= up_limit,
        "is_limit_down": last_price <= down_limit,
        "yesterday_main_inflow": yesterday_main_inflow,
        "today_main_inflow": today_main_inflow,
        "data_date": str(row.get("data_date") or "").strip(),
        "ma10_source": str(row.get("ma10_source") or "CSV").strip(),
        "ma10_qmt": ma10_qmt,  # 透传, KPI 卡 count 这个 (QMT 备选可用度)
    }


def build_qmt_industry_stats_payload(rows: list[dict]) -> dict:
    normalized_rows = []
    for row in rows:
        normalized = normalize_industry_stats_row(row)
        if normalized is not None:
            normalized_rows.append(normalized)

    grouped: dict[str, list[dict]] = {}
    for item in normalized_rows:
        grouped.setdefault(item["sector"], []).append(item)

    items = []
    for sector, group in grouped.items():
        stock_count = len(group)
        above_ma10_count = sum(1 for item in group if item["above_ma10"])
        above_ma10_count_csv = sum(1 for item in group if item["above_ma10_csv"])
        meat_count = sum(1 for item in group if item["is_meat"])
        big_loss_count = sum(1 for item in group if item["is_big_loss"])
        limit_up_count = sum(1 for item in group if item["is_limit_up"])
        limit_down_count = sum(1 for item in group if item["is_limit_down"])
        yesterday_main_inflow = sum(item["yesterday_main_inflow"] for item in group)
        today_main_inflow = sum(item["today_main_inflow"] for item in group)
        # 占比：
        #   above_ma10_ratio_realtime: tick > ma10_realtime（今日盘口站上均线的占比，实时变）
        #   above_ma10_ratio_csv:      last_close > ma10_csv（昨日截面对照，今天开盘前的样子，稳定的 T+1 截面）
        above_ma10_ratio_realtime = above_ma10_count / stock_count if stock_count else 0.0
        above_ma10_ratio_csv = above_ma10_count_csv / stock_count if stock_count else 0.0
        # 差值：实时相对昨日的变化，>0 表示今天盘口情绪比昨天强，<0 弱
        above_ma10_ratio_delta = above_ma10_ratio_realtime - above_ma10_ratio_csv
        # 该行业所有股票共享同一份 CSV 截面，data_date 取组内最新
        sector_data_date = max(
            (str(item.get("data_date") or "") for item in group),
            default="",
        )
        items.append(
            {
                "sector": sector,
                "stock_count": stock_count,
                "above_ma10_count": above_ma10_count,
                "above_ma10_count_csv": above_ma10_count_csv,
                "above_ma10_ratio_realtime": above_ma10_ratio_realtime,
                "above_ma10_ratio_csv": above_ma10_ratio_csv,
                "above_ma10_ratio_delta": above_ma10_ratio_delta,
                # 兼容旧字段名（前端 table 之前用 above_ma10_ratio，按 realtime 语义等价）
                "above_ma10_ratio": above_ma10_ratio_realtime,
                # 组内个股 ma10 算术平均（参考值，不是"行业指数 ma10"）
                "ma10_realtime": (
                    sum(item["ma10_realtime"] for item in group) / stock_count if stock_count else 0.0
                ),
                "ma10_csv": (
                    sum(item["ma10_csv"] for item in group) / stock_count if stock_count else 0.0
                ),
                "meat_count": meat_count,
                "big_loss_count": big_loss_count,
                "limit_up_count": limit_up_count,
                "limit_down_count": limit_down_count,
                "yesterday_main_inflow": yesterday_main_inflow,
                "today_main_inflow": today_main_inflow,
                "data_date": sector_data_date,
                # ma10 QMT 实时覆盖度: 该行业 N 只股票里 ma10 走 QMT 实时 K 线的占比 (0~1)
                # 0 = 全部降级 CSV+tick（白名单未生效 / VM 不通 / xtquant 缓存空）
                # 1 = 全部 QMT 实时（最理想态, 含 8/11 当日数据, 解决 CSV T+1 滞后）
                "ma10_qmt_ratio": sum(1 for item in group if item.get("ma10_qmt") is not None) / stock_count if stock_count else 0.0,
                "ma10_qmt_count": sum(1 for item in group if item.get("ma10_qmt") is not None),
            }
        )

    items.sort(
        key=lambda item: (
            -item["today_main_inflow"],
            -item["meat_count"],
            item["sector"],
        )
    )
    top_meat = max(items, key=lambda item: (item["meat_count"], item["sector"]), default=None)
    top_main_inflow = items[0] if items else None

    # data_date 来自本地 CSV（T+1 截面），与今天的日历差即 staleness
    # 周末/节假日会自然产生更长 lag（不特殊处理，UI 用颜色编码即可）
    all_data_dates = [str(item.get("data_date") or "") for item in items if item.get("data_date")]
    summary_data_date = max(all_data_dates) if all_data_dates else ""
    summary_data_lag_days: int | None = None
    if summary_data_date:
        try:
            from datetime import date as _date
            y, m, d = summary_data_date.split("-")
            summary_data_lag_days = (_date.today() - _date(int(y), int(m), int(d))).days
        except (ValueError, TypeError):
            summary_data_lag_days = None

    return {
        "summary": {
            "sector_count": len(items),
            # 强势行业 = 实时占比 > 50%（多数股今天站上自己 ma10）
            "strong_ma10_sector_count": sum(1 for item in items if item["above_ma10_ratio_realtime"] > 0.5),
            "strong_ma10_sector_count_yesterday": sum(1 for item in items if item["above_ma10_ratio_csv"] > 0.5),
            "top_meat_sector": top_meat["sector"] if top_meat else None,
            "top_main_inflow_sector": top_main_inflow["sector"] if top_main_inflow else None,
            "data_date": summary_data_date or None,
            "data_lag_days": summary_data_lag_days,
            # 实时数据日期 = 今天（即使 19:00 之后，盘中价反映的是今天）
            "realtime_data_date": date.today().isoformat(),
            # 全市场 ma10 QMT 实时覆盖度（行业表所有股票的累加）
            # 0/0 = 全部降级 CSV+tick（VM 端白名单未生效 / xtquant 缓存空 / 网络不通）
            # N/M = 正常 QMT 实时（最理想态, 含 8/11 当日数据, 解决 CSV T+1 滞后）
            "ma10_qmt_total_count": sum(int(item.get("ma10_qmt_count", 0)) for item in items),
            "ma10_qmt_total_stocks": sum(int(item.get("stock_count", 0)) for item in items),
        },
        "items": items,
    }


def _normalize_local_code_for_loader(stock_code: str) -> str:
    value = str(stock_code or "").strip()
    if "." in value:
        value = value.split(".", 1)[0]
    return value


def _prepare_local_history_for_trade_date(local_df: pd.DataFrame, target_trade_date: str | None) -> pd.DataFrame:
    if local_df is None or local_df.empty:
        return local_df

    history = local_df.copy()
    if "trade_date" in history.columns:
        normalized_trade_dates = history["trade_date"].astype(str).str.slice(0, 10)
        history = history.assign(trade_date=normalized_trade_dates)
        if target_trade_date:
            history = history.loc[history["trade_date"] <= str(target_trade_date)[:10]]

    return history


def build_qmt_industry_stats_source_rows(target_trade_date: str | None = None) -> list[dict]:
    rows: list[dict] = []
    stock_codes = list_a_shares()
    if not stock_codes:
        return rows

    # 阶段 1：并发读所有股票 CSV（I/O 密集，32 线程足够把 ~10 分钟压到 1 分钟内）
    # CSV 解析是 GIL-bound 但读盘和解析 pandas 释放 GIL，ThreadPool 也能拿满吞吐
    from concurrent.futures import ThreadPoolExecutor, as_completed

    workers = int(os.environ.get("INDUSTRY_STATS_WORKERS", "32"))
    code_to_df: dict[str, pd.DataFrame] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_code = {
            executor.submit(get_trading_data, _normalize_local_code_for_loader(code)): code
            for code in stock_codes
        }
        for future in as_completed(future_to_code):
            code = future_to_code[future]
            try:
                df = future.result()
                if df is not None and not df.empty:
                    code_to_df[code] = df
            except Exception:
                continue

    # 阶段 1.5：QMT 实时 K 线 → **直接用 ticks 已有 lastPrice**（get_full_tick 已含 lastPrice=今日收盘）
    # 阶段 1.5: 全市场一次性拉 14 天日 K close (实测 0.5-1s 拿 99%, DM-kun 模式)
    # 数据源: xtquant download_history_data2 + get_market_data
    # 失败（白名单阻挡 / VM 不通 / 超时）→ 降级 CSV ma10
    ma10_qmt_map: dict[str, float] = {}
    ma10_source_map: dict[str, str] = {}
    try:
        close_map = get_qmt_close_window(
            codes=stock_codes,
            period="1d",
            days_back=14,    # 14 天, 够 ma10 + 周末/节假日 + 停牌容错
        )
        for code, closes in close_map.items():
            if len(closes) >= 5:  # 至少 5 天数据（节假日/新股容忍）
                ma10_qmt_map[code] = sum(closes[-10:]) / min(len(closes), 10)
                ma10_source_map[code] = "QMT"
    except Exception:
        # 全部失败 → ma10_qmt_map 留空，阶段 2 自动降级 CSV
        pass

    # 阶段 2：一次性拿全市场 tick（QMT bridge 一次 5207 只）
    ticks = get_full_tick_snapshot(stock_codes)
    trade_day = str(target_trade_date or "").replace("-", "")
    for stock_code in stock_codes:
        tick = ticks.get(stock_code) or {}
        last_price = _to_float_or_none(tick.get("last_price"))
        last_close = _to_float_or_none(tick.get("last_close"))
        if last_close in (None, 0):
            continue
        # 非交易时段 QMT 返回 last_price=0，降级用 last_close
        if last_price in (None, 0):
            last_price = last_close

        local_df = code_to_df.get(stock_code)
        if local_df is None or local_df.empty:
            continue

        history = _prepare_local_history_for_trade_date(local_df, target_trade_date)
        if history is None or history.empty or len(history.index) < 2:
            continue
        if "close" not in history.columns:
            continue

        close_window = history["close"].tail(10)
        try:
            ma10_csv = float(close_window.astype(float).mean())
        except (TypeError, ValueError):
            continue

        # MA10 三档降级链（简化版, 阶段 1.5 留空 ma10_qmt_map, 走 CSV+tick）:
        # 1) ma10_qmt (理想态): QMT 10 天 K 线平均 (留空, 走 2 兜底)
        # 2) ma10_realtime (实际态): CSV 9 天 + lastPrice 平均
        #    - lastPrice 来自 QMT get_full_tick (= 今日 QMT 收盘 tick 价)
        # MA10 三档降级链 (CSV 默认 + QMT 备选):
        # 1) ma10_csv (默认): 本地 CSV 10 天 close 平均 — 数据稳定确定, 盘后入库含当日
        # 2) ma10_qmt (备选): QMT 14 天 K 线平均 — VM 端 cache 命中秒返, 用于 CSV 异常时降级
        # 3) ma10_realtime (兜底): CSV 9 天 + lastPrice 平均 — 老逻辑, CSV 缺/异常 + QMT 不可用
        # 设计取舍: 本地数据优先 (稳定, 不依赖 VM 桥), QMT 是双保险
        # 8/12 盘后 15:30 之后, CSV 入库 8/11, ma10_csv 跟 QMT 几乎一致
        # 凌晨 (CSV 截面 = 前一天), ma10_csv 缺今日, KPI 卡用 ma10_qmt_count 显示 QMT 备选可用度
        ma10_qmt = ma10_qmt_map.get(stock_code)
        if ma10_csv == ma10_csv and ma10_csv > 0:  # ma10_csv == ma10_csv 排除 NaN
            # 1) CSV 默认路径
            ma10_realtime = ma10_csv
            ma10_source = "CSV"
        elif ma10_qmt is not None:
            # 2) QMT 备选
            ma10_realtime = ma10_qmt
            ma10_source = "QMT(备选)"
        else:
            # 3) 老逻辑兜底: CSV 9 天 + lastPrice
            try:
                close_9d = close_window.head(9).astype(float).tolist()
                ma10_realtime = (sum(close_9d) + float(last_price)) / 10
                ma10_source = "CSV+tick"
            except (TypeError, ValueError):
                ma10_realtime = ma10_csv
                ma10_source = "CSV"

        latest_row = history.iloc[-1]
        stock_name = str(latest_row.get("name", "") or "").strip()
        sector = str(latest_row.get("industry_l1", "") or "").strip()
        up_limit = compute_up_limit(stock_code, stock_name, float(last_close), trade_day)
        down_limit = compute_down_limit(stock_code, stock_name, float(last_close), trade_day)
        if not sector or up_limit is None or down_limit is None:
            continue

        # 按 trade_date 找真正的上一交易日（跳过周末/节假日/停牌缺口）
        if "trade_date" in history.columns:
            latest_date = history["trade_date"].max()
            prev_dates = history.loc[history["trade_date"] < latest_date, "trade_date"]
            if prev_dates.empty:
                continue
            prev_date = prev_dates.max()
            prev_rows = history[history["trade_date"] == prev_date]
            if prev_rows.empty:
                continue
            prev_row = prev_rows.iloc[-1]
        else:
            if len(history.index) < 2:
                continue
            prev_row = history.iloc[-2]

        try:
            # 主力 = 机构 + 大户；CSV 中单位为万元，乘 10000 转为元
            _inst_buy = float(prev_row.get("inst_buy", 0.0) or 0.0)
            _inst_sell = float(prev_row.get("inst_sell", 0.0) or 0.0)
            _big_buy = float(prev_row.get("big_buy", 0.0) or 0.0)
            _big_sell = float(prev_row.get("big_sell", 0.0) or 0.0)
            yesterday_main_inflow = (_inst_buy + _big_buy - _inst_sell - _big_sell) * 10000
        except (TypeError, ValueError):
            yesterday_main_inflow = 0.0

        try:
            _l_inst_buy = float(latest_row.get("inst_buy", 0.0) or 0.0)
            _l_inst_sell = float(latest_row.get("inst_sell", 0.0) or 0.0)
            _l_big_buy = float(latest_row.get("big_buy", 0.0) or 0.0)
            _l_big_sell = float(latest_row.get("big_sell", 0.0) or 0.0)
            today_main_inflow = (_l_inst_buy + _l_big_buy - _l_inst_sell - _l_big_sell) * 10000
        except (TypeError, ValueError):
            today_main_inflow = 0.0

        rows.append(
            {
                "stock_code": stock_code,
                "stock_name": stock_name,
                "sector": sector,
                "last_price": float(last_price),
                "last_close": float(last_close),
                "ma10_csv": ma10_csv,
                "ma10_realtime": ma10_realtime,
                "ma10_source": ma10_source,
                "up_limit": up_limit,
                "down_limit": down_limit,
                "yesterday_main_inflow": yesterday_main_inflow,
                "today_main_inflow": today_main_inflow,
                # QMT 备选 ma10: 阶段 1.5 拉到的 QMT 14 天 K 线平均, None 表示拉不到 (VM 桥不通/缺数据)
                # KPI 卡 ma10_qmt_count 用这个字段统计"VM 断了能 fallback 多少票"
                "ma10_qmt": ma10_qmt_map.get(stock_code),
                # 数据截面日期（CSV 最后一行的 trade_date），用于 staleness 计算
                # 注意：CSV 是 T+1 截面，所以 data_date 通常 = 上一交易日
                "data_date": str(latest_date)[:10] if latest_date is not None else "",
            }
        )

    return rows

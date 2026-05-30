"""
fetcher/backfill.py — 新部署时自动回填历史数据

触发条件：zt_pool 表为空（说明是全新部署）
回填范围：最近 N 个交易日（默认 7 天）
支持的数据：zt_pool / dt_pool / zbgc_pool / strong_pool / lhb / northbound_flow / research
不支持回填：sector_flow / concept_heat（纯实时快照，无历史接口）
"""

import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)

# 回填天数（含今天往前数，跳过周末）
BACKFILL_DAYS = 7


def _trading_dates(n: int) -> list[str]:
    """返回最近 n 个自然日里的交易日（粗略过滤周末，不含节假日）。"""
    result = []
    d = date.today()
    checked = 0
    while len(result) < n and checked < n * 3:
        if d.weekday() < 5:  # 0=Mon … 4=Fri
            result.append(d.strftime("%Y-%m-%d"))
        d -= timedelta(days=1)
        checked += 1
    return result  # 从新到旧


def _is_new_deployment() -> bool:
    """zt_pool 为空则认为是新部署。"""
    try:
        import sqlite3
        from db.storage import DB_PATH
        with sqlite3.connect(DB_PATH) as conn:
            cnt = conn.execute("SELECT COUNT(*) FROM zt_pool").fetchone()[0]
            return cnt == 0
    except Exception:
        return False


def run_backfill() -> None:
    """
    检测是否新部署，若是则后台回填 BACKFILL_DAYS 天历史数据。
    设计为在 _initial_fetch 线程里调用，不会阻塞调度器启动。
    """
    if not _is_new_deployment():
        return

    logger.info("[backfill] 检测到新部署（zt_pool 为空），开始回填最近 %d 天历史数据", BACKFILL_DAYS)
    dates = _trading_dates(BACKFILL_DAYS)
    logger.info("[backfill] 回填日期: %s", dates)

    # 逐日回填，从旧到新
    for trade_date in reversed(dates):
        logger.info("[backfill] 回填 %s ...", trade_date)
        _backfill_date(trade_date)

    # northbound_flow 和 research 一次性拉取即可（接口本身返回多天数据）
    _backfill_northbound()
    _backfill_research(BACKFILL_DAYS)

    logger.info("[backfill] 历史数据回填完成")


def _backfill_date(trade_date: str) -> None:
    """回填单个交易日的涨停池/跌停池/炸板池/强势股/龙虎榜。"""
    date_nodash = trade_date.replace("-", "")

    _run("zt_pool",    lambda: _fetch_zt_pool(date_nodash, trade_date))
    _run("dt_pool",    lambda: _fetch_dt_pool(date_nodash, trade_date))
    _run("zbgc_pool",  lambda: _fetch_zbgc_pool(date_nodash, trade_date))
    _run("strong_pool",lambda: _fetch_strong_pool(date_nodash, trade_date))
    _run("lhb",        lambda: _fetch_lhb(date_nodash))


def _run(name: str, fn) -> None:
    try:
        fn()
    except Exception as e:
        logger.warning("[backfill] %s 失败: %s", name, e)


# ── 各数据源回填实现 ──────────────────────────────────────────────────────────

def _fetch_zt_pool(date_nodash: str, db_date: str) -> None:
    import akshare as ak
    from db.storage import insert_zt_pool
    df = ak.stock_zt_pool_em(date=date_nodash)
    if df is None or df.empty:
        return
    col_code       = next((c for c in df.columns if "代码" in c), None)
    col_name       = next((c for c in df.columns if "名称" in c), None)
    col_count      = next((c for c in df.columns if "连板" in c or "涨停统计" in c or "连续" in c), None)
    col_time       = next((c for c in df.columns if "首次" in c), None)
    col_sector     = next((c for c in df.columns if "行业" in c or "板块" in c or "概念" in c), None)
    col_last_time  = next((c for c in df.columns if "最后" in c), None)
    col_seal       = next((c for c in df.columns if "封板资金" in c), None)
    col_zb         = next((c for c in df.columns if "炸板" in c), None)
    col_turnover   = next((c for c in df.columns if "换手率" in c), None)
    col_circ_mv    = next((c for c in df.columns if "流通市值" in c), None)
    for _, row in df.iterrows():
        def _s(c): return str(row[c]).strip() if c else ""
        def _f(c):
            try: return float(row[c]) if c else None
            except: return None
        def _i(c):
            try: return int(row[c]) if c else 0
            except: return 0
        insert_zt_pool(db_date, _s(col_code), _s(col_name),
                       _i(col_count), _s(col_time), _s(col_sector),
                       last_zt_time=_s(col_last_time), seal_amount=_f(col_seal),
                       zb_count=_i(col_zb), turnover_rate=_f(col_turnover), circ_mv=_f(col_circ_mv))
    logger.info("[backfill] zt_pool %s: %d 条", db_date, len(df))


def _fetch_dt_pool(date_nodash: str, db_date: str) -> None:
    import akshare as ak
    from db.storage import insert_dt_pool
    df = ak.stock_zt_pool_dtgc_em(date=date_nodash)
    if df is None or df.empty:
        return
    col_code   = next((c for c in df.columns if "代码" in c), None)
    col_name   = next((c for c in df.columns if "名称" in c), None)
    col_time   = next((c for c in df.columns if "最后封板" in c or "首次" in c or "时间" in c), None)
    col_sector = next((c for c in df.columns if "行业" in c or "板块" in c), None)
    for _, row in df.iterrows():
        def _s(c): return str(row[c]).strip() if c else ""
        insert_dt_pool(db_date, _s(col_code), _s(col_name), _s(col_time), _s(col_sector))
    logger.info("[backfill] dt_pool %s: %d 条", db_date, len(df))


def _fetch_zbgc_pool(date_nodash: str, db_date: str) -> None:
    import akshare as ak
    from db.storage import insert_zbgc_pool
    df = ak.stock_zt_pool_zbgc_em(date=date_nodash)
    if df is None or df.empty:
        return
    col_code = next((c for c in df.columns if "代码" in c), None)
    col_name = next((c for c in df.columns if "名称" in c), None)
    col_time = next((c for c in df.columns if "首次" in c), None)
    col_zb   = next((c for c in df.columns if "炸板" in c), None)
    col_amp  = next((c for c in df.columns if "振幅" in c), None)
    col_sec  = next((c for c in df.columns if "行业" in c or "板块" in c), None)
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
    logger.info("[backfill] zbgc_pool %s: %d 条", db_date, len(df))


def _fetch_strong_pool(date_nodash: str, db_date: str) -> None:
    import akshare as ak
    from db.storage import insert_strong_pool
    df = ak.stock_zt_pool_strong_em(date=date_nodash)
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
    logger.info("[backfill] strong_pool %s: %d 条", db_date, len(df))


def _fetch_lhb(date_nodash: str) -> None:
    import akshare as ak
    from db.storage import insert_lhb_data
    df = ak.stock_lhb_detail_em(start_date=date_nodash, end_date=date_nodash)
    if df is None or df.empty:
        return
    col_code      = next((c for c in df.columns if "代码" in c), None)
    col_name      = next((c for c in df.columns if "名称" in c), None)
    col_date      = next((c for c in df.columns if "上榜日" in c or "日期" in c), None)
    col_interp    = next((c for c in df.columns if "解读" in c), None)
    col_pct       = next((c for c in df.columns if "涨跌幅" in c), None)
    col_net       = next((c for c in df.columns if "净买额" in c and "占" not in c), None)
    col_reason    = next((c for c in df.columns if "原因" in c or "上榜原因" in c), None)
    col_net_ratio = next((c for c in df.columns if "净买额占" in c), None)
    for _, row in df.iterrows():
        try:
            code = str(row[col_code]) if col_code else ""
            name = str(row[col_name]) if col_name else ""
            interpret = str(row[col_interp]) if col_interp else ""
            reason = str(row[col_reason]) if col_reason else ""
            net_buy = float(row[col_net]) if col_net else 0.0
            change_pct = float(row[col_pct]) if col_pct else None
            net_buy_ratio = float(row[col_net_ratio]) if col_net_ratio else None
            if col_date:
                raw = str(row[col_date]).strip()
                trade_date = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}" if (len(raw) == 8 and raw.isdigit()) else raw[:10]
            else:
                trade_date = date_nodash[:4] + "-" + date_nodash[4:6] + "-" + date_nodash[6:]
            insert_lhb_data(trade_date, code, name, reason, net_buy,
                            change_pct=change_pct, interpret=interpret, net_buy_ratio=net_buy_ratio)
        except Exception:
            continue
    logger.info("[backfill] lhb %s: %d 条", date_nodash, len(df))


def _backfill_northbound() -> None:
    """北向资金接口本身返回多天数据，直接调一次即可。"""
    try:
        import akshare as ak
        from db.storage import insert_northbound_flow
        from datetime import datetime
        df = ak.stock_hsgt_fund_flow_summary_em()
        if df is None or df.empty:
            return
        fetch_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        col_date = next((c for c in df.columns if "交易日" in c or "日期" in c), None)
        col_chan = next((c for c in df.columns if "板块" in c), None)
        col_dir  = next((c for c in df.columns if "方向" in c), None)
        col_net  = next((c for c in df.columns if "成交净买额" in c), None)
        col_inf  = next((c for c in df.columns if "资金净流入" in c), None)
        for _, row in df.iterrows():
            def _f(c):
                try: return float(row[c]) if c else 0.0
                except: return 0.0
            def _s(c): return str(row[c]).strip() if c else ""
            insert_northbound_flow(fetch_time, _s(col_date), _s(col_chan),
                                   _s(col_dir), _f(col_net), _f(col_inf))
        logger.info("[backfill] northbound_flow: %d 条", len(df))
    except Exception as e:
        logger.warning("[backfill] northbound_flow 失败: %s", e)


def _backfill_research(days_back: int) -> None:
    """研究报告直接用已有接口的 days_back 参数。"""
    try:
        from fetcher.research import fetch as fetch_research
        fetch_research(days_back=days_back)
        logger.info("[backfill] research: days_back=%d 完成", days_back)
    except Exception as e:
        logger.warning("[backfill] research 失败: %s", e)

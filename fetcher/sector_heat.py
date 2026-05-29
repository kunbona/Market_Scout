import logging
from datetime import date, datetime

import akshare as ak

from db.storage import insert_zt_pool, insert_dt_pool, insert_sector_flow, insert_zbgc_pool, insert_strong_pool

logger = logging.getLogger(__name__)


def fetch_zt_pool() -> None:
    try:
        trade_date = date.today().strftime("%Y%m%d")
        db_date = date.today().strftime("%Y-%m-%d")
        df = ak.stock_zt_pool_em(date=trade_date)

        col_code       = next((c for c in df.columns if "代码" in c), None)
        col_name       = next((c for c in df.columns if "名称" in c), None)
        col_count      = next((c for c in df.columns if "连板" in c or "涨停统计" in c or "连续" in c), None)
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
    try:
        trade_date = date.today().strftime("%Y%m%d")
        db_date = date.today().strftime("%Y-%m-%d")
        df = ak.stock_dt_pool_em(date=trade_date)

        col_code = next((c for c in df.columns if "代码" in c), None)
        col_name = next((c for c in df.columns if "名称" in c), None)
        col_time = next((c for c in df.columns if "首次" in c or "封板" in c or "时间" in c), None)
        col_sector = next((c for c in df.columns if "行业" in c or "板块" in c or "概念" in c), None)

        for _, row in df.iterrows():
            stock_code = str(row[col_code]).strip() if col_code else ""
            stock_name = str(row[col_name]).strip() if col_name else ""
            first_dt_time = str(row[col_time]).strip() if col_time else ""
            sector = str(row[col_sector]).strip() if col_sector else ""
            insert_dt_pool(db_date, stock_code, stock_name, first_dt_time, sector)
    except Exception as e:
        logger.warning(f"[sector_heat] fetch_dt_pool failed: {e}")


def fetch_concept_heat() -> None:
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
        logger.warning(f"[sector_heat] fetch_concept_heat failed: {e}")


def fetch_zbgc_pool() -> None:
    try:
        trade_date = date.today().strftime("%Y%m%d")
        db_date = date.today().strftime("%Y-%m-%d")
        df = ak.stock_zt_pool_zbgc_em(date=trade_date)
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

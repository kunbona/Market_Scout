import logging
import time
from datetime import datetime

import akshare as ak

from db.storage import insert_sector_flow, insert_lhb_data

logger = logging.getLogger(__name__)


def fetch_sector_flow() -> None:
    try:
        df = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流")
        if df is None or df.empty:
            return

        fetch_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        col_name = next((c for c in ["名称", "板块名称"] if c in df.columns), None)
        col_change = next((c for c in ["今日涨跌幅", "涨跌幅"] if c in df.columns), None)
        col_inflow = next((c for c in ["今日主力净流入-净额", "主力净流入-净额", "主力净流入净额"] if c in df.columns), None)
        col_inflow_pct = next((c for c in ["今日主力净流入-净占比", "主力净流入-净占比", "主力净流入净占比"] if c in df.columns), None)

        if col_name is None:
            logger.warning("[eastmoney] sector_flow: 未找到名称列，columns=%s", list(df.columns))
            return

        for _, row in df.iterrows():
            try:
                sector_name = str(row[col_name])
                change_pct = float(row[col_change]) if col_change else 0.0
                main_inflow = float(row[col_inflow]) if col_inflow else 0.0
                main_inflow_pct = float(row[col_inflow_pct]) if col_inflow_pct else 0.0
                insert_sector_flow(fetch_time, sector_name, change_pct, main_inflow, main_inflow_pct, source_type="industry")
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"[eastmoney] fetch_sector_flow failed: {e}")


def fetch_lhb() -> None:
    try:
        time.sleep(0.5)
        today = datetime.now().strftime("%Y%m%d")
        df = ak.stock_lhb_detail_em(symbol="全部", start_date=today, end_date=today)
        if df is None or df.empty:
            return

        trade_date = datetime.now().strftime("%Y-%m-%d")

        col_code = next((c for c in ["代码", "股票代码"] if c in df.columns), None)
        col_name = next((c for c in ["名称", "股票名称"] if c in df.columns), None)
        col_reason = next((c for c in ["解读", "上榜原因", "原因"] if c in df.columns), None)
        col_net_buy = next((c for c in ["净买额(万)", "净买额", "净买入额(万元)"] if c in df.columns), None)

        if col_code is None:
            logger.warning("[eastmoney] lhb: 未找到代码列，columns=%s", list(df.columns))
            return

        for _, row in df.iterrows():
            try:
                stock_code = str(row[col_code])
                stock_name = str(row[col_name]) if col_name else ""
                reason = str(row[col_reason]) if col_reason else ""
                net_buy = float(row[col_net_buy]) if col_net_buy else 0.0
                insert_lhb_data(trade_date, stock_code, stock_name, reason, net_buy)
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"[eastmoney] fetch_lhb failed: {e}")

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
        df = ak.stock_lhb_detail_em(start_date=today, end_date=today)
        if df is None or df.empty:
            return

        # 列名容错探测
        col_code      = next((c for c in df.columns if "代码" in c), None)
        col_name      = next((c for c in df.columns if "名称" in c), None)
        col_date      = next((c for c in df.columns if "上榜日" in c or "日期" in c), None)
        col_interp    = next((c for c in df.columns if "解读" in c), None)
        col_pct       = next((c for c in df.columns if "涨跌幅" in c), None)
        col_net       = next((c for c in df.columns if "净买额" in c and "占" not in c), None)
        col_reason    = next((c for c in df.columns if "原因" in c or "上榜原因" in c), None)
        col_net_ratio = next((c for c in df.columns if "净买额占" in c), None)

        if col_code is None:
            logger.warning("[eastmoney] lhb: 未找到代码列，columns=%s", list(df.columns))
            return

        for _, row in df.iterrows():
            try:
                stock_code = str(row[col_code])
                stock_name = str(row[col_name]) if col_name else ""
                interpret  = str(row[col_interp]) if col_interp else ""
                reason     = str(row[col_reason]) if col_reason else ""
                # net_buy: 龙虎榜净买额，单位元，直接存储
                net_buy    = float(row[col_net]) if col_net else 0.0
                change_pct = float(row[col_pct]) if col_pct else None
                net_buy_ratio = float(row[col_net_ratio]) if col_net_ratio else None
                # trade_date 优先取数据中的上榜日，避免 17:30 拉取时用 today 拿到明天日期
                if col_date:
                    raw_date = str(row[col_date]).strip()
                    # 支持 YYYY-MM-DD 或 YYYYMMDD
                    if len(raw_date) == 8 and raw_date.isdigit():
                        trade_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
                    else:
                        trade_date = raw_date[:10]
                else:
                    trade_date = datetime.now().strftime("%Y-%m-%d")
                insert_lhb_data(
                    trade_date, stock_code, stock_name, reason, net_buy,
                    change_pct=change_pct, interpret=interpret, net_buy_ratio=net_buy_ratio,
                )
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"[eastmoney] fetch_lhb failed: {e}")

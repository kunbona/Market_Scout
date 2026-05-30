import logging
import time
from datetime import datetime

import akshare as ak
import requests

from db.storage import insert_sector_flow, insert_lhb_data

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
_DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def eastmoney_datacenter(report_name: str, columns: str = "ALL", filter_str: str = "",
                          page_size: int = 50, sort_columns: str = "", sort_types: str = "-1") -> list:
    """东方财富 datacenter 通用接口，返回 data.data 列表（字典格式）。失败返回 []。"""
    params = {
        "reportName": report_name, "columns": columns,
        "filter": filter_str, "pageNumber": "1", "pageSize": str(page_size),
        "sortColumns": sort_columns, "sortTypes": sort_types,
        "source": "WEB", "client": "WEB",
    }
    try:
        r = requests.get(_DATACENTER_URL, params=params,
                         headers={"User-Agent": _UA, "Referer": "https://data.eastmoney.com/"},
                         timeout=15)
        j = r.json()
        return j.get("data", {}).get("data") or []
    except Exception as e:
        logger.warning("[eastmoney_datacenter] %s failed: %s", report_name, e)
        return []

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


def fetch_margin() -> None:
    """融资融券市场汇总（全市场最新一批，按 rzye 降序取 top 100）。"""
    from db.storage import insert_margin
    fetch_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = eastmoney_datacenter(
        "RPTA_WEB_RZRQ_GGMX",
        columns="TRADE_DATE,SCODE,SNAME,RZYE,RZMRE,RZCHE,RQYE,RQMCL,RQCHL,RZRQYE",
        sort_columns="RZYE", sort_types="-1",
        page_size=100,
    )
    for row in rows:
        try:
            insert_margin(
                fetch_time,
                str(row.get("TRADE_DATE", ""))[:10],
                str(row.get("SCODE", "")),
                str(row.get("SNAME", "")),
                float(row.get("RZYE") or 0),
                float(row.get("RZMRE") or 0),
                float(row.get("RZCHE") or 0),
                float(row.get("RQYE") or 0),
                float(row.get("RQMCL") or 0),
                float(row.get("RZRQYE") or 0),
            )
        except Exception:
            continue


def fetch_block_trade() -> None:
    """大宗交易（全市场最新 100 条，按 TRADE_DATE 降序）。"""
    from db.storage import insert_block_trade
    today = datetime.now().strftime("%Y-%m-%d")
    rows = eastmoney_datacenter(
        "RPT_DATA_BLOCKTRADE",
        columns="TRADE_DATE,SECURITY_CODE,SECURITY_NAME,DEAL_PRICE,CLOSE_PRICE,DEAL_VOLUME,DEAL_AMT,BUYER_NAME,SELLER_NAME",
        filter_str=f'(TRADE_DATE>="{today}")',
        sort_columns="TRADE_DATE,DEAL_AMT", sort_types="-1,-1",
        page_size=100,
    )
    for row in rows:
        try:
            insert_block_trade(
                str(row.get("TRADE_DATE", ""))[:10],
                str(row.get("SECURITY_CODE", "")),
                str(row.get("SECURITY_NAME", "")),
                float(row.get("DEAL_PRICE") or 0),
                float(row.get("CLOSE_PRICE") or 0),
                int(float(row.get("DEAL_VOLUME") or 0)),
                float(row.get("DEAL_AMT") or 0),
                str(row.get("BUYER_NAME", "")),
                str(row.get("SELLER_NAME", "")),
            )
        except Exception:
            continue


def fetch_holder_count() -> None:
    """股东人数变化（最近 100 条，按最新报告期降序）。"""
    from db.storage import insert_holder_count
    rows = eastmoney_datacenter(
        "RPT_HOLDERNUMLATEST",
        columns="END_DATE,SECURITY_CODE,SECURITY_NAME,HOLDER_NUM,HOLDER_NUM_CHANGE,HOLDER_NUM_RATIO,AVG_FREE_SHARES",
        sort_columns="END_DATE", sort_types="-1",
        page_size=100,
    )
    for row in rows:
        try:
            insert_holder_count(
                str(row.get("END_DATE", ""))[:10],
                str(row.get("SECURITY_CODE", "")),
                str(row.get("SECURITY_NAME", "")),
                int(float(row.get("HOLDER_NUM") or 0)),
                float(row.get("HOLDER_NUM_CHANGE") or 0),
                float(row.get("HOLDER_NUM_RATIO") or 0),
                float(row.get("AVG_FREE_SHARES") or 0),
            )
        except Exception:
            continue


def fetch_lockup_expiry() -> None:
    """近 30 天及未来 90 天解禁/减持计划（按解禁日期升序，取 200 条）。"""
    from db.storage import insert_lockup_expiry
    from datetime import datetime, timedelta
    today = datetime.now().strftime("%Y-%m-%d")
    future = (datetime.now() + timedelta(days=90)).strftime("%Y-%m-%d")
    rows = eastmoney_datacenter(
        "RPT_LIFT_STAGE",
        columns="SECURITY_CODE,SECURITY_NAME,FREE_DATE,LIFT_SHARES,LIFT_MARKET_CAP,LIFT_RATIO,HOLD_NUM,LIFT_TYPE",
        filter_str=f'(FREE_DATE>="{today}")(FREE_DATE<="{future}")',
        sort_columns="FREE_DATE", sort_types="1",
        page_size=200,
    )
    for row in rows:
        try:
            insert_lockup_expiry(
                str(row.get("FREE_DATE", ""))[:10],
                str(row.get("SECURITY_CODE", "")),
                str(row.get("SECURITY_NAME", "")),
                float(row.get("LIFT_SHARES") or 0),
                float(row.get("LIFT_MARKET_CAP") or 0),
                float(row.get("LIFT_RATIO") or 0),
                int(float(row.get("HOLD_NUM") or 0)),
                str(row.get("LIFT_TYPE", "")),
            )
        except Exception:
            continue


def fetch_dividend_history() -> None:
    """A 股最新分红送转记录（按除权日降序，取 200 条）。"""
    from db.storage import insert_dividend
    rows = eastmoney_datacenter(
        "RPT_SHAREBONUS_DET",
        columns="SECURITY_CODE,SECURITY_NAME,EX_DIVIDEND_DATE,PRETAX_BONUS_RMB,TRANSFER_RATIO,BONUS_RATIO,ASSIGN_PROGRESS",
        sort_columns="EX_DIVIDEND_DATE", sort_types="-1",
        page_size=200,
    )
    for row in rows:
        try:
            insert_dividend(
                str(row.get("EX_DIVIDEND_DATE", ""))[:10],
                str(row.get("SECURITY_CODE", "")),
                str(row.get("SECURITY_NAME", "")),
                float(row.get("PRETAX_BONUS_RMB") or 0),
                float(row.get("TRANSFER_RATIO") or 0),
                float(row.get("BONUS_RATIO") or 0),
                str(row.get("ASSIGN_PROGRESS", "")),
            )
        except Exception:
            continue


def fetch_industry_ranking() -> None:
    """全市场行业板块涨幅排行（东财 push2 clist）。"""
    from db.storage import insert_industry_ranking
    fetch_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    params = {
        "pn": "1", "pz": "100", "po": "1", "np": "1",
        "fltt": "2", "invt": "2",
        "fs": "m:90+t:2",
        "fields": "f2,f3,f4,f12,f14,f104,f105,f128,f136,f140,f141",
    }
    try:
        r = requests.get(
            "https://push2.eastmoney.com/api/qt/clist/get",
            params=params,
            headers={"User-Agent": _UA, "Referer": "https://quote.eastmoney.com/"},
            timeout=15,
        )
        items = r.json().get("data", {}).get("diff", []) or []
    except Exception as e:
        logger.warning("[eastmoney] fetch_industry_ranking failed: %s", e)
        return
    for item in items:
        try:
            insert_industry_ranking(
                fetch_time,
                str(item.get("f12", "")),   # 板块代码
                str(item.get("f14", "")),   # 板块名称
                float(item.get("f3") or 0),  # 涨跌幅
                float(item.get("f2") or 0),  # 最新价
                int(float(item.get("f104") or 0)),  # 上涨家数
                int(float(item.get("f105") or 0)),  # 下跌家数
                str(item.get("f128", "")),  # 领涨股
                float(item.get("f140") or 0),  # 领涨股涨幅
            )
        except Exception:
            continue


def fetch_ths_hot_stocks() -> None:
    """同花顺主题热股（带编辑打标的主题理由，每日一次）。"""
    from db.storage import insert_ths_hot_stock
    from datetime import datetime
    today = datetime.now().strftime("%Y%m%d")
    fetch_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    url = (
        f"http://zx.10jqka.com.cn/event/api/getharden/"
        f"date/{today}/orderby/date/orderway/desc/charset/UTF-8/"
    )
    try:
        r = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.warning("[ths_hot] fetch_ths_hot_stocks failed: %s", e)
        return

    items = data if isinstance(data, list) else data.get("data", data.get("list", []))
    for item in items:
        try:
            insert_ths_hot_stock(
                fetch_time,
                str(item.get("code", "")),
                str(item.get("name", "")),
                str(item.get("reason", "") or item.get("tag", "")),
                str(item.get("industry", "") or item.get("sector", "")),
                float(item.get("chg", 0) or 0),
            )
        except Exception:
            continue

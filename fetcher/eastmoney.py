import logging
import time
from datetime import datetime, timedelta

import akshare as ak
import requests

from db.storage import insert_sector_flow, insert_lhb_data
from fetcher.http_util import get_session, jitter_sleep, make_headers, random_ua

logger = logging.getLogger(__name__)

_EM_SESSION = get_session("eastmoney.com")
_DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def _em_retry(fn, retries: int = 3, base_delay: float = 2.0):
    """重试任意 callable（主要包裹 akshare 调用），每次重试换 UA + 随机延迟。"""
    for i in range(retries):
        try:
            if i > 0:
                jitter_sleep(base_delay, 2.0)
                _EM_SESSION.headers.update({"User-Agent": random_ua()})
            return fn()
        except Exception as e:
            if i == retries - 1:
                raise
            logger.debug("[eastmoney] retry %d/%d after: %s", i + 1, retries, e)


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
        r = _EM_SESSION.get(_DATACENTER_URL, params=params,
                            headers=make_headers(referer="https://data.eastmoney.com/"),
                            timeout=15)
        j = r.json()
        return (j.get("result") or j.get("data") or {}).get("data") or []
    except Exception as e:
        logger.warning("[eastmoney_datacenter] %s failed: %s", report_name, e)
        return []


def _fetch_sector_flow_ths(fetch_time: str) -> bool:
    """同花顺行业资金流降级方案，成功返回 True。"""
    try:
        df = ak.stock_fund_flow_industry(symbol="即时")
        if df is None or df.empty:
            return False
        col_name   = next((c for c in df.columns if "行业" in c), None)
        col_change = next((c for c in df.columns if "涨跌幅" in c), None)
        col_inflow = next((c for c in df.columns if "净额" in c or "净流入" in c), None)
        if col_name is None:
            return False
        count = 0
        for _, row in df.iterrows():
            try:
                sector_name = str(row[col_name])
                change_pct  = float(str(row[col_change]).replace("%", "")) if col_change else 0.0
                main_inflow = float(row[col_inflow]) if col_inflow else 0.0
                insert_sector_flow(fetch_time, sector_name, change_pct, main_inflow, 0.0, source_type="industry")
                count += 1
            except Exception:
                continue
        if count > 0:
            logger.info("[eastmoney] sector_flow 降级同花顺，写入 %d 条", count)
            return True
    except Exception as e:
        logger.debug("[eastmoney] sector_flow 同花顺降级也失败: %s", e)
    return False


def fetch_sector_flow() -> None:
    fetch_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        df = _em_retry(lambda: ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流"))
        if df is None or df.empty:
            raise ValueError("empty")

        col_name       = next((c for c in ["名称", "板块名称"] if c in df.columns), None)
        col_change     = next((c for c in ["今日涨跌幅", "涨跌幅"] if c in df.columns), None)
        col_inflow     = next((c for c in ["今日主力净流入-净额", "主力净流入-净额", "主力净流入净额"] if c in df.columns), None)
        col_inflow_pct = next((c for c in ["今日主力净流入-净占比", "主力净流入-净占比", "主力净流入净占比"] if c in df.columns), None)

        if col_name is None:
            raise ValueError(f"未找到名称列: {list(df.columns)}")

        for _, row in df.iterrows():
            try:
                insert_sector_flow(fetch_time, str(row[col_name]),
                                   float(row[col_change]) if col_change else 0.0,
                                   float(row[col_inflow]) if col_inflow else 0.0,
                                   float(row[col_inflow_pct]) if col_inflow_pct else 0.0,
                                   source_type="industry")
            except Exception:
                continue
    except Exception as e:
        logger.debug("[eastmoney] fetch_sector_flow 主源失败，尝试降级: %s", e)
        _fetch_sector_flow_ths(fetch_time)


def _parse_lhb_date(raw: str) -> str:
    """将上榜日原始字符串统一为 YYYY-MM-DD；支持 YYYYMMDD 和 YYYY-MM-DD 两种格式。"""
    raw = raw.strip()
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return raw[:10]


def fetch_lhb() -> None:
    try:
        time.sleep(0.5)
        today = datetime.now().strftime("%Y%m%d")
        df = ak.stock_lhb_detail_em(start_date=today, end_date=today)
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

        if col_code is None:
            logger.warning("[eastmoney] lhb: 未找到代码列，columns=%s", list(df.columns))
            return

        for _, row in df.iterrows():
            try:
                trade_date = (
                    _parse_lhb_date(str(row[col_date]))
                    if col_date
                    else datetime.now().strftime("%Y-%m-%d")
                )
                insert_lhb_data(
                    trade_date,
                    str(row[col_code]),
                    str(row[col_name]) if col_name else "",
                    str(row[col_reason]) if col_reason else "",
                    float(row[col_net]) if col_net else 0.0,
                    change_pct=float(row[col_pct]) if col_pct else None,
                    interpret=str(row[col_interp]) if col_interp else "",
                    net_buy_ratio=float(row[col_net_ratio]) if col_net_ratio else None,
                )
            except Exception:
                continue
    except Exception as e:
        logger.warning("[eastmoney] fetch_lhb failed: %s", e)


def fetch_margin() -> None:
    """融资融券市场汇总（全市场最新一批，按 rzye 降序取 top 100）。"""
    from db.storage import insert_margin
    fetch_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = eastmoney_datacenter(
        "RPTA_WEB_RZRQ_GGMX",
        columns="DATE,SCODE,SECNAME,RZYE,RZMRE,RZCHE,RQYE,RQMCL,RQCHL,RZRQYE",
        sort_columns="RZYE", sort_types="-1",
        page_size=100,
    )
    for row in rows:
        try:
            insert_margin(
                fetch_time,
                str(row.get("DATE", ""))[:10],
                str(row.get("SCODE", "")),
                str(row.get("SECNAME", "")),
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
        filter_str=f"(TRADE_DATE>='{today}')",
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
        columns="END_DATE,SECURITY_CODE,SECURITY_NAME_ABBR,HOLDER_NUM,HOLDER_NUM_CHANGE,HOLDER_NUM_RATIO,AVG_HOLD_NUM",
        sort_columns="END_DATE", sort_types="-1",
        page_size=100,
    )
    for row in rows:
        try:
            insert_holder_count(
                str(row.get("END_DATE", ""))[:10],
                str(row.get("SECURITY_CODE", "")),
                str(row.get("SECURITY_NAME_ABBR", "")),
                int(float(row.get("HOLDER_NUM") or 0)),
                float(row.get("HOLDER_NUM_CHANGE") or 0),
                float(row.get("HOLDER_NUM_RATIO") or 0),
                float(row.get("AVG_HOLD_NUM") or 0),
            )
        except Exception:
            continue


def fetch_lockup_expiry() -> None:
    """近 30 天及未来 90 天解禁/减持计划（按解禁日期升序，取 200 条）。"""
    from db.storage import insert_lockup_expiry
    today  = datetime.now().strftime("%Y-%m-%d")
    future = (datetime.now() + timedelta(days=90)).strftime("%Y-%m-%d")
    rows = eastmoney_datacenter(
        "RPT_LIFT_STAGE",
        columns="SECURITY_CODE,SECURITY_NAME_ABBR,FREE_DATE,FREE_SHARES,LIFT_MARKET_CAP,FREE_RATIO,BATCH_HOLDER_NUM,FREE_SHARES_TYPE",
        filter_str=f"(FREE_DATE>='{today}')(FREE_DATE<='{future}')",
        sort_columns="FREE_DATE", sort_types="1",
        page_size=200,
    )
    for row in rows:
        try:
            insert_lockup_expiry(
                str(row.get("FREE_DATE", ""))[:10],
                str(row.get("SECURITY_CODE", "")),
                str(row.get("SECURITY_NAME_ABBR", "")),
                float(row.get("FREE_SHARES") or 0),
                float(row.get("LIFT_MARKET_CAP") or 0),
                float(row.get("FREE_RATIO") or 0),
                int(float(row.get("BATCH_HOLDER_NUM") or 0)),
                str(row.get("FREE_SHARES_TYPE", "")),
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


def _fetch_industry_ranking_sina(fetch_time: str, insert_industry_ranking) -> bool:
    """新浪行业板块降级方案，成功返回 True。"""
    try:
        df = ak.stock_sector_spot(indicator="新浪行业")
        if df is None or df.empty:
            return False
        col_name    = next((c for c in df.columns if "名称" in c or "板块" in c), None)
        col_change  = next((c for c in df.columns if "涨跌幅" in c), None)
        col_lead    = next((c for c in df.columns if "领涨股" in c or "涨幅最大" in c), None)
        col_lead_pct = next((c for c in df.columns if "领涨" in c and "幅" in c and c != col_change), None)
        if col_name is None or col_change is None:
            return False
        count = 0
        for _, row in df.iterrows():
            try:
                insert_industry_ranking(
                    fetch_time,
                    "",                          # 新浪无板块代码
                    str(row[col_name]),
                    float(str(row[col_change]).replace("%", "")),
                    0.0,                         # 新浪无最新价
                    0, 0,                        # 新浪无上涨/下跌家数
                    str(row[col_lead]) if col_lead else "",
                    float(str(row[col_lead_pct]).replace("%", "")) if col_lead_pct else 0.0,
                )
                count += 1
            except Exception:
                continue
        if count > 0:
            logger.info("[eastmoney] industry_ranking 降级新浪，写入 %d 条", count)
            return True
    except Exception as e:
        logger.debug("[eastmoney] industry_ranking 新浪降级也失败: %s", e)
    return False


def fetch_industry_ranking() -> None:
    """全市场行业板块涨幅排行（东财 push2delay → 新浪降级）。"""
    from db.storage import insert_industry_ranking
    fetch_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    params = {
        "pn": "1", "pz": "100", "po": "1", "np": "1",
        "fltt": "2", "invt": "2",
        "fs": "m:90+t:2",
        "fields": "f2,f3,f4,f12,f14,f104,f105,f128,f136,f140,f141",
    }
    try:
        r = _em_retry(lambda: _EM_SESSION.get(
            "https://push2delay.eastmoney.com/api/qt/clist/get",
            params=params,
            headers=make_headers(referer="https://quote.eastmoney.com/"),
            timeout=15,
        ))
        items = r.json().get("data", {}).get("diff", []) or []
        if not items:
            raise ValueError("empty response")
        for item in items:
            try:
                insert_industry_ranking(
                    fetch_time,
                    str(item.get("f12", "")),
                    str(item.get("f14", "")),
                    float(item.get("f3") or 0),
                    float(item.get("f2") or 0),
                    int(float(item.get("f104") or 0)),
                    int(float(item.get("f105") or 0)),
                    str(item.get("f128", "")),
                    float(item.get("f140") or 0),
                )
            except Exception:
                continue
    except Exception as e:
        logger.debug("[eastmoney] fetch_industry_ranking 主源失败，尝试降级: %s", e)
        _fetch_industry_ranking_sina(fetch_time, insert_industry_ranking)


def fetch_ths_hot_stocks() -> None:
    """同花顺主题热股（带编辑打标的主题理由，每日一次）。"""
    from db.storage import insert_ths_hot_stock
    today      = datetime.now().strftime("%Y%m%d")
    fetch_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    url = (
        f"http://zx.10jqka.com.cn/event/api/getharden/"
        f"date/{today}/orderby/date/orderway/desc/charset/UTF-8/"
    )
    try:
        r = requests.get(url, headers={"User-Agent": random_ua()}, timeout=15)
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

import logging
from datetime import datetime
import pytz
from db.storage import insert_research_report
from fetcher.http_util import fetch_with_retry

logger = logging.getLogger(__name__)

BASE_URL = "https://reportapi.eastmoney.com/report/list"
_REFERER = "https://data.eastmoney.com/report/stock.jshtml"

# qType: 0=个股 1=行业 2=宏观 3=策略
QTYPES = [0, 1, 2, 3]

_TZ_BEIJING = pytz.timezone("Asia/Shanghai")


def _report_url(info_code: str, encode_url: str = "") -> str:
    if not info_code:
        return ""
    # 优先用 encodeUrl 构造直接 PDF 链接（dfcfw.com 可直接访问）
    # jshtml 链接已 404，不再使用
    if encode_url:
        return f"https://pdf.dfcfw.com/pdf/H3_{encode_url}_1.pdf"
    return ""


def _today_beijing() -> str:
    """返回北京时间今日日期字符串 YYYY-MM-DD。"""
    return datetime.now(_TZ_BEIJING).strftime("%Y-%m-%d")


def fetch(days_back: int = 0) -> None:
    """抓取东财研报今日流（默认只抓当天，days_back=0）。

    API 说明：
    - 接口 https://reportapi.eastmoney.com/report/list
    - 返回格式：顶层 data 字段为 list（不是 dict），直接迭代即可
    - TotalPage=1 时无需翻页，pageSize=100 覆盖单日全量
    """
    today = _today_beijing()
    # days_back>0 时往前多取几天（用于补数据场景）
    if days_back > 0:
        from datetime import timedelta
        begin = (datetime.now(_TZ_BEIJING) - timedelta(days=days_back)).strftime("%Y-%m-%d")
    else:
        begin = today
    end = today

    total = 0
    for qtype in QTYPES:
        page = 1
        while True:
            try:
                params = {
                    "qType": qtype,
                    "pageSize": 100,
                    "pageNo": page,
                    "industryCode": "*",
                    "industry": "*",
                    "rating": "*",
                    "ratingChange": "*",
                    "beginTime": begin,
                    "endTime": end,
                    "fields": "",
                    "orgCode": "",
                    "code": "*",
                    "rcode": "",
                }
                resp = fetch_with_retry(BASE_URL, domain="reportapi.eastmoney.com",
                                        referer=_REFERER, params=params, timeout=15)
                data = resp.json()

                # API 返回的 data 字段直接是 list（非 dict.list）
                raw = data.get("data", [])
                if isinstance(raw, dict):
                    items = raw.get("list", [])
                elif isinstance(raw, list):
                    items = raw
                else:
                    items = []

                for item in items:
                    title        = str(item.get("title", "") or "")
                    stock_code   = str(item.get("stockCode", "") or "")
                    stock_name   = str(item.get("stockName", "") or "")
                    org_name     = str(item.get("orgSName", "") or item.get("orgName", "") or "")
                    researcher   = str(item.get("researcher", "") or item.get("author", "") or "")
                    pub_raw      = str(item.get("publishDate", "") or "")
                    publish_date = pub_raw[:10] if pub_raw else ""
                    rating       = str(item.get("emRatingName", "") or item.get("sRatingName", "") or "")
                    aim_price    = str(item.get("indvAimPriceT", "") or item.get("indvAimPriceL", "") or "")
                    info_code    = str(item.get("infoCode", "") or "")
                    encode_url   = str(item.get("encodeUrl", "") or "")
                    report_url   = _report_url(info_code, encode_url)
                    if not title:
                        continue
                    insert_research_report(
                        title, stock_code, stock_name, org_name, researcher,
                        publish_date, rating, aim_price, report_url, qtype,
                    )
                    total += 1

                # 翻页判断
                total_pages = int(data.get("TotalPage", 1) or 1)
                if page >= total_pages:
                    break
                page += 1

            except Exception as e:
                logger.warning(f"[research] qtype={qtype} page={page} failed: {e}")
                break

    logger.info(f"[research] fetched {total} reports (begin={begin}, end={end})")

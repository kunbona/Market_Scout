import logging
import os
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

_CST = timezone(timedelta(hours=8))
from urllib.parse import urljoin

import akshare as ak
import feedparser
import requests
from bs4 import BeautifulSoup

from db.storage import insert_policy_news

logger = logging.getLogger(__name__)

RSSHUB = os.getenv("RSSHUB_BASE_URL", "http://localhost:1200")

RSS_SOURCES = [
    (f"{RSSHUB}/gov/ndrc/xwdt/xwfb", "发改委"),
    (f"{RSSHUB}/gov/ndrc/xwdt/tzgg", "发改委"),
    (f"{RSSHUB}/gov/csrc/news", "证监会"),
    (f"{RSSHUB}/sse/inquire", "上交所问询"),
    (f"{RSSHUB}/szse/inquire", "深交所问询"),
    (f"{RSSHUB}/szse/notice", "深交所公告"),
    # 财新深度文章：RSSHub 路由（内容比 akshare 更新，但需 RSSHub 可用）
    (f"{RSSHUB}/caixin/article", "财新"),
]

# RSSHub 不可用时的 HTML scrape 兜底（发改委 + 证监会）
HTML_FALLBACK = [
    ("https://www.ndrc.gov.cn/xwdt/xwfb/", "发改委"),
    ("https://www.ndrc.gov.cn/xwdt/tzgg/", "发改委"),
    ("https://www.csrc.gov.cn/csrc/c100028/c100031/list.shtml", "证监会", "div.lists", "content.shtml"),
]

# 始终用 HTML scrape 的来源（作为 RSSHub 证监会路由的补充兜底）
HTML_ALWAYS: list = []


def _to_cst(dt: datetime) -> str:
    if dt.tzinfo is not None:
        dt = dt.astimezone(_CST)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _parse_time(s: str) -> str:
    if not s:
        return ""
    try:
        return _to_cst(parsedate_to_datetime(s))
    except Exception:
        pass
    try:
        from dateutil.parser import parse as du_parse
        dt = du_parse(s)
        return _to_cst(dt) if dt.tzinfo else dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s.strip(), fmt).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    return ""


def _rsshub_available() -> bool:
    try:
        r = requests.get(f"{RSSHUB}/", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def _fetch_rss(url: str, source: str) -> None:
    feed = feedparser.parse(url)
    for entry in feed.entries:
        title = getattr(entry, "title", "")
        link = getattr(entry, "link", "")
        pub = getattr(entry, "published", "")
        pub_time = _parse_time(pub)
        if title:
            insert_policy_news(title, link, pub_time, source)


def _fetch_html(url: str, source: str, link_must_contain: str = "", container_selector: str = "") -> None:
    try:
        from fetcher.http_util import fetch_with_retry
        resp = fetch_with_retry(url, domain=url.split("/")[2], referer=url, timeout=10)
    except Exception as e:
        logger.warning(f"[policy_rss] {source} request failed: {e}")
        return
    try:
        soup = BeautifulSoup(resp.content, "html.parser")
        if container_selector:
            root = soup.select_one(container_selector) or soup
        else:
            root = soup
        items = root.select("li") or root.select("tr")
        for item in items:
            a = item.find("a")
            if not a:
                continue
            title = a.get_text(strip=True)
            link = a.get("href", "")
            if link and not link.startswith("http"):
                link = urljoin(url, link)
            if link_must_contain and link_must_contain not in link:
                continue
            time_tag = item.find("span") or item.find("td", class_=lambda c: c and "time" in c.lower())
            pub_time = _parse_time(time_tag.get_text(strip=True) if time_tag else "")
            if title and len(title) > 5 and link:
                insert_policy_news(title, link, pub_time, source)
    except Exception as e:
        logger.warning(f"[policy_rss] {source} parse failed: {e}")


def fetch() -> None:
    use_rsshub = _rsshub_available()

    if use_rsshub:
        for url, source in RSS_SOURCES:
            try:
                _fetch_rss(url, source)
            except Exception as e:
                logger.warning(f"[policy_rss] {source} failed: {e}")
    else:
        logger.warning("[policy_rss] RSSHub 不可用，降级到 HTML scrape")
        for entry in HTML_FALLBACK:
            url, source = entry[0], entry[1]
            container_sel = entry[2] if len(entry) > 2 else ""
            link_filter = entry[3] if len(entry) > 3 else ""
            try:
                _fetch_html(url, source, link_must_contain=link_filter, container_selector=container_sel)
            except Exception as e:
                logger.warning(f"[policy_rss] {source} failed: {e}")

    # 财新补充：无论 RSSHub 是否可用，始终用 akshare 抓财新首页文章
    # akshare stock_news_main_cx() 返回的 URL 固定但标题唯一，UNIQUE(title,source) 可正常去重
    try:
        df = ak.stock_news_main_cx()
        fetch_time = datetime.now(_CST).strftime("%Y-%m-%d %H:%M:%S")
        count = 0
        for _, row in df.iterrows():
            title = str(row.get("summary", "") or row.get("title", ""))
            link  = str(row.get("url", "") or row.get("link", ""))
            if not title:
                continue
            pub_time = (
                _parse_time(str(row.get("pub_time", "")))
                or _parse_time(str(row.get("time", "")))
                or _parse_time(str(row.get("date", "")))
                or fetch_time
            )
            insert_policy_news(title, link, pub_time, "财新")
            count += 1
        if count:
            logger.debug("[policy_rss] 财新 akshare 补充 %d 条", count)
    except Exception as e:
        logger.debug("[policy_rss] 财新 akshare 补充失败: %s", e)

    # 财新降级：RSSHub 不可用时尝试财新官方 RSS
    if not use_rsshub:
        for cx_url in [
            "https://www.caixin.com/rss/caixinnews.xml",
            "https://rss.caixin.com/home",
        ]:
            try:
                feed = feedparser.parse(cx_url)
                if feed.entries:
                    for entry in feed.entries:
                        title = getattr(entry, "title", "")
                        link  = getattr(entry, "link", "")
                        pub   = getattr(entry, "published", "")
                        if title:
                            insert_policy_news(title, link, _parse_time(pub), "财新")
                    logger.info("[policy_rss] 财新降级 RSS %s 获取 %d 条", cx_url, len(feed.entries))
                    break
            except Exception as e:
                logger.debug("[policy_rss] 财新降级 %s failed: %s", cx_url, e)


def fetch_cninfo() -> None:
    """巨潮资讯公司公告（东方财富接口）"""
    import akshare as ak
    from datetime import datetime
    # 重要公告类型白名单
    IMPORTANT_TYPES = {
        "业绩预告", "业绩快报", "定期报告", "重大合同", "股权激励",
        "并购重组", "增发", "配股", "可转债", "回购预案", "减持计划",
        "股东增持", "重大事项", "监管问询", "董事会决议公告", "股份质押、冻结",
    }
    try:
        today = datetime.now().strftime("%Y%m%d")
        df = ak.stock_notice_report(symbol="全部", date=today)
        for _, row in df.iterrows():
            try:
                notice_type = str(row.get("公告类型", "") or "")
                if notice_type not in IMPORTANT_TYPES:
                    continue
                title = str(row.get("名称", "")) + "：" + str(row.get("公告标题", ""))
                link  = str(row.get("网址", "") or "")
                pub_time = str(row.get("公告日期", "") or "")
                # 日期格式 2026-05-28，补上时间
                if pub_time and len(pub_time) == 10:
                    pub_time += " 00:00:00"
                if title.strip() and title != "：":
                    insert_policy_news(title.strip(), link, pub_time, "巨潮公告")
            except Exception:
                continue
    except KeyError:
        logger.debug("[policy_rss] fetch_cninfo skipped: akshare stock_notice_report 列名变更，等待库更新")
    except Exception as e:
        logger.warning(f"[policy_rss] fetch_cninfo failed: {e}")

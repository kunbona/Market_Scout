import logging
import os
import requests
import feedparser
from datetime import datetime, timezone, timedelta

_CST = timezone(timedelta(hours=8))


def _rss_to_cst(pub: str) -> str:
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(pub)
        if dt.tzinfo is not None:
            dt = dt.astimezone(_CST)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""
import akshare as ak
from db.storage import insert_cls_news

logger = logging.getLogger(__name__)
RSSHUB = os.getenv("RSSHUB_BASE_URL", "http://172.17.0.1:1200")


def fetch_cls_red() -> None:
    """财联社加红重点电报（RSSHub），信噪比高于全量接口"""
    try:
        feed = feedparser.parse(f"{RSSHUB}/cls/telegraph/red")
        if not feed.entries:
            return
        for entry in feed.entries:
            title = getattr(entry, "title", "")
            link = getattr(entry, "link", "")
            pub = getattr(entry, "published", "")
            pub_time = _rss_to_cst(pub)
            if title:
                insert_cls_news(title=title, content="", pub_time=pub_time, source="财联社", link=link)
    except Exception as e:
        logger.warning(f"[global_news] fetch_cls_red failed: {e}")


def fetch_em() -> None:
    try:
        df = ak.stock_info_global_em()
        for _, row in df.iterrows():
            insert_cls_news(
                title=str(row["标题"]),
                content=str(row["摘要"]),
                pub_time=str(row["发布时间"]),
                source="东方财富",
                link=str(row["链接"]),
            )
    except Exception as e:
        logger.warning(f"[global_news] fetch_em failed: {e}")


def fetch_ths() -> None:
    try:
        df = ak.stock_info_global_ths()
        for _, row in df.iterrows():
            insert_cls_news(
                title=str(row["标题"]),
                content=str(row["内容"]),
                pub_time=str(row["发布时间"]),
                source="同花顺",
                link=str(row["链接"]),
            )
    except Exception as e:
        logger.warning(f"[global_news] fetch_ths failed: {e}")


def fetch_yicai() -> None:
    """第一财经简讯（RSSHub /yicai/brief）"""
    try:
        feed = feedparser.parse(f"{RSSHUB}/yicai/brief")
        if not feed.entries:
            return
        for entry in feed.entries:
            title = getattr(entry, "title", "")
            link = getattr(entry, "link", "")
            pub = getattr(entry, "published", "")
            pub_time = _rss_to_cst(pub)
            if title:
                import re
                title = re.sub(r"<[^>]+>", "", title).strip()
                insert_cls_news(title=title, content="", pub_time=pub_time, source="第一财经", link=link)
    except Exception as e:
        logger.warning(f"[global_news] fetch_yicai failed: {e}")


def fetch_wscn() -> None:
    # 优先用 RSSHub 路由，失败则降级到直接 API
    try:
        feed = feedparser.parse(f"{RSSHUB}/wallstreetcn/live/a-stock/2")
        if feed.entries:
            for entry in feed.entries:
                title = getattr(entry, "title", "")
                link = getattr(entry, "link", "")
                pub = getattr(entry, "published", "")
                pub_time = _rss_to_cst(pub)
                if title:
                    insert_cls_news(title=title, content="", pub_time=pub_time, source="华尔街见闻", link=link)
            return
    except Exception:
        pass
    # 降级：直接调用 API
    try:
        url = "https://api.wallstreetcn.com/apiv1/content/articles?channel=a-shares&limit=50"
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        items = resp.json()["data"]["items"]
        for item in items:
            pub_time = datetime.fromtimestamp(item["display_time"]).strftime("%Y-%m-%d %H:%M:%S")
            insert_cls_news(
                title=str(item["title"]),
                content=str(item["content_short"]),
                pub_time=pub_time,
                source="华尔街见闻",
                link=str(item["uri"]),
            )
    except Exception as e:
        logger.warning(f"[global_news] fetch_wscn failed: {e}")


def fetch_jin10() -> None:
    """金十数据快讯（RSSHub /jin10）"""
    try:
        feed = feedparser.parse(f"{RSSHUB}/jin10")
        if not feed.entries:
            return
        for entry in feed.entries:
            title = getattr(entry, "title", "").strip()
            link  = getattr(entry, "link", "")
            pub   = getattr(entry, "published", "")
            pub_time = _rss_to_cst(pub)
            if title:
                insert_cls_news(title=title, content="", pub_time=pub_time, source="金十数据", link=link)
    except Exception as e:
        logger.warning(f"[global_news] fetch_jin10 failed: {e}")


def fetch_gelonghui() -> None:
    """格隆汇实时快讯（RSSHub /gelonghui/live）"""
    try:
        feed = feedparser.parse(f"{RSSHUB}/gelonghui/live")
        if not feed.entries:
            return
        for entry in feed.entries:
            title = getattr(entry, "title", "").strip()
            link  = getattr(entry, "link", "")
            pub   = getattr(entry, "published", "")
            pub_time = _rss_to_cst(pub)
            if title:
                insert_cls_news(title=title, content="", pub_time=pub_time, source="格隆汇", link=link)
    except Exception as e:
        logger.warning(f"[global_news] fetch_gelonghui failed: {e}")

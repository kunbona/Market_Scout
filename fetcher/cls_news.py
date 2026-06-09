import hashlib
import logging
import urllib.parse
from datetime import datetime, timezone, timedelta

from db.storage import insert_cls_news
from fetcher.http_util import fetch_with_retry

logger = logging.getLogger(__name__)

_CST = timezone(timedelta(hours=8))
_API = "https://www.cls.cn/v1/roll/get_roll_list"


def _sign(params: dict) -> str:
    """财联社接口签名：参数按 key 排序拼接 → SHA1 → MD5"""
    qs = urllib.parse.urlencode(sorted(params.items()))
    sha1 = hashlib.sha1(qs.encode()).hexdigest()
    return hashlib.md5(sha1.encode()).hexdigest()


def fetch() -> None:
    try:
        params = {
            "app": "CailianpressWeb",
            "id": "1000",
            "last_time": "0",
            "os": "web",
            "rn": "50",
            "sv": "8.4.6",
            "refresh_type": "1",
            "hasFirstVipArticle": "1",
            "category": "",
        }
        params["sign"] = _sign(params)

        r = fetch_with_retry(_API, domain="cls.cn", referer="https://www.cls.cn/",
                             params=params, timeout=10)
        items = r.json()["data"]["roll_data"]

        for item in items:
            try:
                ctime = item.get("ctime")
                if not ctime:
                    continue
                pub_time = datetime.fromtimestamp(ctime, tz=_CST).strftime("%Y-%m-%d %H:%M:%S")

                # brief 优先，没有再用 content 前100字
                title = str(item.get("brief") or item.get("title") or "").strip()
                if not title:
                    continue
                content = str(item.get("content") or "")
                link = str(item.get("shareurl") or "")

                insert_cls_news(title, content, pub_time, source="财联社", link=link)
            except Exception:
                continue

    except Exception as e:
        logger.warning("[cls_news] fetch failed: %s", e)

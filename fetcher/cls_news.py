import logging
from datetime import datetime, timezone, timedelta

import requests

from db.storage import insert_cls_news

logger = logging.getLogger(__name__)

_CST = timezone(timedelta(hours=8))
_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}


def fetch() -> None:
    try:
        r = requests.get(
            "https://www.cls.cn/nodeapi/telegraphList",
            params={"rn": 50},
            headers=_HEADERS,
            timeout=10,
        )
        items = r.json()["data"]["roll_data"]
        for item in items:
            try:
                dt = datetime.fromtimestamp(item["ctime"], tz=_CST)
                pub_time = dt.strftime("%Y-%m-%d %H:%M:%S")
                title = str(item.get("title", "")).strip()
                if not title:
                    continue
                content = str(item.get("content", ""))
                link = str(item.get("share_url", "") or "")
                insert_cls_news(title, content, pub_time, source="财联社", link=link)
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"[cls_news] fetch failed: {e}")

"""
市场宽度数据抓取。

数据来源：xtquant (miniQMT) — 全市逐票快照，up/down/flat 精确，含全市成交额。
QMT 不可用时静默跳过，不抛异常。
"""
import logging
from datetime import datetime
from db.storage import insert_market_breadth

logger = logging.getLogger(__name__)


def fetch() -> None:
    """从 miniQMT 拉取全市行情快照，写入 market_breadth 表。QMT 不可用时静默跳过。"""
    try:
        from fetcher.xtquant_breadth import fetch as _qmt_fetch
        if not _qmt_fetch():
            logger.debug("[market_breadth] QMT 不可用或未启用，跳过本次采集")
    except Exception as e:
        logger.debug("[market_breadth] QMT 调用异常: %s", e)

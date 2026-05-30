"""
mootdx 基本面数据抓取。
finance()  — 37 个财务字段（EPS / ROE / 净利润 / 总股本等）
F10()      — 公司概况 / 财务分析 / 股东研究 等 9 类文本型数据

数据面向 Agent 消费，按股票代码写入 fundamentals_finance / fundamentals_f10 表。
抓取对象：涨停池 + 强势股中今日出现的股票（避免全市场扫描）。
"""
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def _get_client():
    from mootdx.quotes import Quotes
    return Quotes.factory(market="std")


def _today_codes() -> list[str]:
    """从本地 pool 表取今日活跃股票代码，去重后返回。"""
    import sqlite3
    from db.storage import DB_PATH
    today = datetime.now().strftime("%Y-%m-%d")
    codes = set()
    try:
        with sqlite3.connect(DB_PATH) as conn:
            for tbl in ("zt_pool", "strong_pool", "zbgc_pool"):
                try:
                    for (code,) in conn.execute(
                        f"SELECT DISTINCT stock_code FROM {tbl} WHERE trade_date = ?", (today,)
                    ).fetchall():
                        if code:
                            codes.add(code.strip())
                except Exception:
                    continue
    except Exception:
        pass
    return list(codes)


def fetch_fundamentals_finance() -> None:
    """拉取今日活跃股的 mootdx finance() 财务字段。"""
    from db.storage import insert_fundamentals_finance
    codes = _today_codes()
    if not codes:
        return
    try:
        client = _get_client()
    except Exception as e:
        logger.warning("[fundamentals] mootdx client init failed: %s", e)
        return

    fetch_date = datetime.now().strftime("%Y-%m-%d")
    for code in codes:
        try:
            df = client.finance(symbol=code)
            if df is None or (hasattr(df, "empty") and df.empty):
                continue
            # finance() 返回单行 DataFrame 或 dict
            row = df.iloc[0].to_dict() if hasattr(df, "iloc") else df
            insert_fundamentals_finance(fetch_date, code, row)
        except Exception as e:
            logger.debug("[fundamentals] finance %s failed: %s", code, e)


def fetch_fundamentals_f10() -> None:
    """拉取今日活跃股的 mootdx F10() 公司概况。"""
    from db.storage import insert_fundamentals_f10
    codes = _today_codes()
    if not codes:
        return
    try:
        client = _get_client()
    except Exception as e:
        logger.warning("[fundamentals] mootdx client init failed: %s", e)
        return

    fetch_date = datetime.now().strftime("%Y-%m-%d")
    categories = ["公司概况", "财务分析", "股东研究"]
    for code in codes:
        for cat in categories:
            try:
                data = client.F10(symbol=code, name=cat)
                if not data:
                    continue
                import json
                content = json.dumps(data, ensure_ascii=False, default=str)
                insert_fundamentals_f10(fetch_date, code, cat, content)
            except Exception as e:
                logger.debug("[fundamentals] F10 %s/%s failed: %s", code, cat, e)

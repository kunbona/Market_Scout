"""
基本面数据抓取（腾讯行情 + akshare）

fetch_fundamentals_finance() — 实时估值字段（PE/PB/市值/换手率等）via 腾讯 qt.gtimg.cn
fetch_fundamentals_f10()     — 公司基本信息 via akshare stock_individual_info_em

数据面向 Agent 消费，按股票代码写入 fundamentals_finance / fundamentals_f10 表。
抓取对象：涨停池 + 强势池中今日出现的股票（避免全市场扫描）。
"""
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


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
    """
    通过腾讯 qt.gtimg.cn 批量拉取今日活跃股的实时估值行情。
    字段：name, price, change_pct, pe_ttm, pb, market_cap, float_cap, turnover, volume_ratio 等。
    """
    from db.storage import insert_fundamentals_finance
    from fetcher.tencent import fetch_quotes

    codes = _today_codes()
    if not codes:
        return

    fetch_date = datetime.now().strftime("%Y-%m-%d")
    quotes = fetch_quotes(codes)

    if not quotes:
        logger.warning("[fundamentals] 腾讯行情返回为空，跳过写入")
        return

    written = 0
    for code, data in quotes.items():
        try:
            insert_fundamentals_finance(fetch_date, code, data)
            written += 1
        except Exception as e:
            logger.debug("[fundamentals] insert %s failed: %s", code, e)

    logger.info("[fundamentals] finance 写入 %d/%d 条", written, len(codes))


def fetch_fundamentals_f10() -> None:
    """
    通过 akshare stock_individual_info_em 拉取公司基本信息，写入 fundamentals_f10 表。
    category 固定为 '公司概况'。
    """
    import json
    import akshare as ak
    from db.storage import insert_fundamentals_f10

    codes = _today_codes()
    if not codes:
        return

    fetch_date = datetime.now().strftime("%Y-%m-%d")
    written = 0
    for code in codes:
        try:
            df = ak.stock_individual_info_em(symbol=code)
            if df is None or (hasattr(df, "empty") and df.empty):
                continue
            # 转为 {item: value} 字典
            if "item" in df.columns and "value" in df.columns:
                info = dict(zip(df["item"], df["value"]))
            else:
                info = df.to_dict(orient="records")
            content = json.dumps(info, ensure_ascii=False, default=str)
            insert_fundamentals_f10(fetch_date, code, "公司概况", content)
            written += 1
        except Exception as e:
            logger.debug("[fundamentals] F10 %s failed: %s", code, e)

    logger.info("[fundamentals] f10 写入 %d/%d 条", written, len(codes))

"""
盘中实时行情快照，每 30 秒轮询一次（仅交易时间）。
只保存聚合指标到 market_pulse 表，不保存全量股票数据。
"""
import logging
import time
import akshare as ak
import pandas as pd
from datetime import datetime
from db.storage import insert_market_pulse

logger = logging.getLogger(__name__)

_RETRY_ATTEMPTS = 3
_RETRY_DELAY = 5   # 秒


def _ak_with_retry(fn, name: str):
    """对 AKShare 调用加重试，处理远端偶发断连（RemoteDisconnected/ConnectionAborted）。"""
    last_exc = None
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            if attempt < _RETRY_ATTEMPTS:
                logger.warning("[realtime_quote] %s 失败（第%d次），%ds后重试: %s", name, attempt, _RETRY_DELAY, e)
                time.sleep(_RETRY_DELAY)
    logger.warning("[realtime_quote] %s 重试%d次后仍失败: %s", name, _RETRY_ATTEMPTS, last_exc)
    return None


def fetch_realtime_snapshot() -> None:
    """
    拉取全市场价格快照 + 乐咕活跃度，写入 market_pulse 表。
    两个数据源独立 try/except，任一失败不影响另一个写入。
    """
    fetch_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── 1. 全市场价格快照（涨停/跌停计数）──
    zt_count = dt_count = zb_count = 0
    zt_dt_ratio = 0.0
    try:
        df = _ak_with_retry(ak.stock_zh_a_spot_em, "stock_zh_a_spot_em")
        if df is None:
            raise RuntimeError("重试耗尽，跳过本次快照")
        cols = df.columns.tolist()
        zt_price_col = next((c for c in cols if "涨停" in c and "价" in c), None)
        dt_price_col = next((c for c in cols if "跌停" in c and "价" in c), None)
        price_col    = next((c for c in cols if c in ["最新价", "现价", "price"]), None)
        if price_col:
            if zt_price_col:
                try:
                    dv = df[[price_col, zt_price_col]].apply(pd.to_numeric, errors="coerce").dropna()
                    zt_count = int((dv[price_col] >= dv[zt_price_col]).sum())
                except Exception as e:
                    logger.warning("[realtime_quote] 涨停计算失败: %s", e)
            if dt_price_col:
                try:
                    dv = df[[price_col, dt_price_col]].apply(pd.to_numeric, errors="coerce").dropna()
                    dt_count = int((dv[price_col] <= dv[dt_price_col]).sum())
                except Exception as e:
                    logger.warning("[realtime_quote] 跌停计算失败: %s", e)
        zt_dt_ratio = zt_count / dt_count if dt_count > 0 else float(zt_count)
    except Exception as e:
        logger.warning("[realtime_quote] stock_zh_a_spot_em 失败: %s", e)

    # ── 2. 乐咕活跃度（独立，不受上面影响）──
    real_zt = real_dt = advance = decline = None
    activity = None
    try:
        legu_df = _ak_with_retry(ak.stock_market_activity_legu, "stock_market_activity_legu")
        if legu_df is None:
            raise RuntimeError("重试耗尽")
        legu    = dict(zip(legu_df["item"], legu_df["value"]))
        real_zt  = int(float(legu.get("真实涨停", 0) or 0))
        real_dt  = int(float(legu.get("真实跌停", 0) or 0))
        advance  = int(float(legu.get("上涨", 0) or 0))
        decline  = int(float(legu.get("下跌", 0) or 0))
        act_str  = str(legu.get("活跃度", "0%")).replace("%", "").strip()
        activity = float(act_str) if act_str else None
        # 若全市场快照失败，用乐咕涨停数补全
        if zt_count == 0 and real_zt:
            zt_count = int(legu.get("涨停", 0) or 0)
            dt_count = int(legu.get("跌停", 0) or 0)
            zt_dt_ratio = zt_count / dt_count if dt_count > 0 else float(zt_count)
    except Exception as _e:
        logger.warning("[realtime_quote] legu 拉取失败: %s", _e)

    insert_market_pulse(fetch_time, zt_count, dt_count, zb_count, zt_dt_ratio,
                        real_zt=real_zt, real_dt=real_dt, activity=activity,
                        advance=advance, decline=decline)

"""
盘中实时行情快照，每 30 秒轮询一次（仅交易时间）。
只保存聚合指标到 market_pulse 表，不保存全量股票数据。

降级策略：
  东方财富 push2.eastmoney.com 对服务器 IP 直接断连（Empty reply），
  stock_zh_a_spot_em 无法在服务器环境使用。
  降级链：stock_zh_a_spot_em → 乐咕 stock_market_activity_legu（正常可用）。
  乐咕已包含真实涨停数/跌停数/活跃度，足以支撑 market_pulse 指标。
"""
import logging
import akshare as ak
from datetime import datetime
from db.storage import insert_market_pulse

logger = logging.getLogger(__name__)

# 东财 push2 在服务器 IP 上直接断连，无需重试，直接降级到乐咕
_EASTMONEY_PUSH2_BLOCKED = True


def fetch_realtime_snapshot() -> None:
    """
    拉取全市场价格快照 + 乐咕活跃度，写入 market_pulse 表。
    主数据源（东财全市场快照）在服务器环境被封，降级到乐咕活跃度指标。
    """
    fetch_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── 1. 全市场价格快照（涨停/跌停计数）──
    # 东财 push2 对服务器 IP 直接断连，跳过不重试，直接由乐咕数据补全
    zt_count = dt_count = zb_count = 0
    zt_dt_ratio = 0.0
    if not _EASTMONEY_PUSH2_BLOCKED:
        # 保留代码以便将来代理/本地环境使用
        import pandas as pd, time as _time
        try:
            df = ak.stock_zh_a_spot_em()
            cols = df.columns.tolist()
            zt_price_col = next((c for c in cols if "涨停" in c and "价" in c), None)
            dt_price_col = next((c for c in cols if "跌停" in c and "价" in c), None)
            price_col    = next((c for c in cols if c in ["最新价", "现价", "price"]), None)
            if price_col and zt_price_col:
                dv = df[[price_col, zt_price_col]].apply(pd.to_numeric, errors="coerce").dropna()
                zt_count = int((dv[price_col] >= dv[zt_price_col]).sum())
            if price_col and dt_price_col:
                dv = df[[price_col, dt_price_col]].apply(pd.to_numeric, errors="coerce").dropna()
                dt_count = int((dv[price_col] <= dv[dt_price_col]).sum())
            zt_dt_ratio = zt_count / dt_count if dt_count > 0 else float(zt_count)
        except Exception as e:
            logger.debug("[realtime_quote] stock_zh_a_spot_em 不可用（服务器IP被封）: %s", e)

    # ── 2. 乐咕活跃度（主力数据源，东财被封时唯一来源）──
    real_zt = real_dt = advance = decline = None
    activity = None
    try:
        legu_df = ak.stock_market_activity_legu()
        if legu_df is None:
            raise RuntimeError("返回空")
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
        logger.warning("[realtime_quote] legu 拉取失败，market_pulse 将写入零值: %s", _e)

    insert_market_pulse(fetch_time, zt_count, dt_count, zb_count, zt_dt_ratio,
                        real_zt=real_zt, real_dt=real_dt, activity=activity,
                        advance=advance, decline=decline)

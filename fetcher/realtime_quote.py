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


def fetch_realtime_snapshot() -> None:
    """
    拉取全市场价格快照，计算聚合指标后写入 market_pulse 表。

    聚合指标：
    - zt_count：当前价 >= 涨停价的股票数
    - dt_count：当前价 <= 跌停价的股票数
    - zb_count：炸板数（需要对比上一次快照，当前版本暂时从 zt_pool 近似）
    - zt_dt_ratio：涨停/跌停比值

    注意：
    - ak.stock_zh_a_spot_em() 轮询间隔 >= 10s，此函数由 scheduler 以 30s 间隔调用
    - 9:30-9:40 窗口 stock_fund_flow_individual("即时") 列数不稳定，暂不使用
    - 非交易时间不调用（由 scheduler 的 _guarded 机制保证）
    """
    try:
        df = ak.stock_zh_a_spot_em()

        # 字段名可能因版本不同，做兼容处理
        # 常见字段：最新价, 涨停价, 跌停价, 今开, 最高, 最低
        # 如果没有涨停价字段，用 昨收 * 1.1 近似（非ST股）

        fetch_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 获取涨停/跌停价列名（不同版本字段名不同）
        cols = df.columns.tolist()

        # 尝试找涨停价列
        zt_price_col = next((c for c in cols if "涨停" in c and "价" in c), None)
        dt_price_col = next((c for c in cols if "跌停" in c and "价" in c), None)
        price_col = next((c for c in cols if c in ["最新价", "现价", "price"]), None)

        if price_col is None:
            logger.warning("[realtime_quote] 找不到价格列，跳过")
            return

        zt_count = 0
        dt_count = 0

        if zt_price_col and price_col:
            try:
                df_valid = df[[price_col, zt_price_col]].dropna()
                df_valid = df_valid.apply(pd.to_numeric, errors="coerce").dropna()
                zt_count = int((df_valid[price_col] >= df_valid[zt_price_col]).sum())
            except Exception as e:
                logger.warning(f"[realtime_quote] 涨停计算失败: {e}")

        if dt_price_col and price_col:
            try:
                df_valid = df[[price_col, dt_price_col]].dropna()
                df_valid = df_valid.apply(pd.to_numeric, errors="coerce").dropna()
                dt_count = int((df_valid[price_col] <= df_valid[dt_price_col]).sum())
            except Exception as e:
                logger.warning(f"[realtime_quote] 跌停计算失败: {e}")

        # 炸板数：当前版本暂时设为 0（需要状态跟踪，后续版本实现）
        zb_count = 0
        zt_dt_ratio = zt_count / dt_count if dt_count > 0 else float(zt_count)

        insert_market_pulse(fetch_time, zt_count, dt_count, zb_count, zt_dt_ratio)

    except Exception as e:
        logger.warning(f"[realtime_quote] fetch failed: {e}")

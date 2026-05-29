import logging
from datetime import datetime
import akshare as ak
from db.storage import insert_hot_rank_up, insert_northbound_flow, insert_xq_hot

logger = logging.getLogger(__name__)


def fetch_hot_rank_up() -> None:
    try:
        fetch_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        df = ak.stock_hot_up_em()
        if df is None or df.empty:
            return
        col_chg  = next((c for c in df.columns if "变动" in c or "较昨" in c), None)
        col_rank = next((c for c in df.columns if "当前排名" in c or ("排名" in c and "变动" not in c and "较" not in c)), None)
        col_code = next((c for c in df.columns if "代码" in c), None)
        col_name = next((c for c in df.columns if "名称" in c or "简称" in c), None)
        col_price= next((c for c in df.columns if "最新价" in c or "现价" in c), None)
        col_pct  = next((c for c in df.columns if "涨跌幅" in c), None)
        for _, row in df.iterrows():
            def _i(c):
                try: return int(float(row[c])) if c else 0
                except: return 0
            def _f(c):
                try: return float(row[c]) if c else None
                except: return None
            def _s(c): return str(row[c]).strip() if c else ""
            insert_hot_rank_up(fetch_time, _i(col_chg), _i(col_rank),
                               _s(col_code), _s(col_name), _f(col_price), _f(col_pct))
        logger.info("[market_sentiment] hot_rank_up 写入 %d 条", len(df))
    except Exception as e:
        logger.warning("[market_sentiment] fetch_hot_rank_up failed: %s", e)


def fetch_northbound_flow() -> None:
    try:
        fetch_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        df = ak.stock_hsgt_fund_flow_summary_em()
        if df is None or df.empty:
            return
        col_date  = next((c for c in df.columns if "交易日" in c or "日期" in c), None)
        col_chan  = next((c for c in df.columns if "板块" in c), None)
        col_dir   = next((c for c in df.columns if "方向" in c), None)
        col_net   = next((c for c in df.columns if "成交净买额" in c), None)
        col_inf   = next((c for c in df.columns if "资金净流入" in c), None)
        for _, row in df.iterrows():
            def _f(c):
                try: return float(row[c]) if c else 0.0
                except: return 0.0
            def _s(c): return str(row[c]).strip() if c else ""
            insert_northbound_flow(fetch_time, _s(col_date), _s(col_chan),
                                   _s(col_dir), _f(col_net), _f(col_inf))
        logger.info("[market_sentiment] northbound_flow 写入 %d 条", len(df))
    except Exception as e:
        logger.warning("[market_sentiment] fetch_northbound_flow failed: %s", e)


def fetch_xq_hot(top_n=50) -> None:
    """雪球关注热度，每次约 50 秒，建议 1 小时调度一次"""
    try:
        fetch_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        df = ak.stock_hot_tweet_xq()
        if df is None or df.empty:
            return
        col_code  = next((c for c in df.columns if "代码" in c), None)
        col_name  = next((c for c in df.columns if "简称" in c or "名称" in c), None)
        col_follow = next((c for c in df.columns if "关注" in c), None)
        col_price = next((c for c in df.columns if "价" in c), None)
        for rank, (_, row) in enumerate(df.head(top_n).iterrows(), start=1):
            def _s(c): return str(row[c]).strip() if c else ""
            def _f(c):
                try: return float(row[c]) if c else None
                except: return None
            insert_xq_hot(fetch_time, rank, _s(col_code), _s(col_name),
                          _f(col_follow), _f(col_price))
        logger.info("[market_sentiment] xq_hot 写入 top %d 条", top_n)
    except Exception as e:
        logger.warning("[market_sentiment] fetch_xq_hot failed: %s", e)

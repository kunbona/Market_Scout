import logging
from datetime import datetime
import akshare as ak
from db.storage import insert_hot_rank_up, insert_northbound_flow, insert_xq_hot, insert_big_deal

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


def fetch_big_deal() -> None:
    """大单实时异动（东财），每次拉取最新大单列表写入 big_deal 表。"""
    try:
        fetch_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        df = ak.stock_fund_flow_big_deal()
        if df is None or df.empty:
            return
        # 列名容错
        col_time   = next((c for c in df.columns if "成交时间" in c or "时间" in c), None)
        col_code   = next((c for c in df.columns if "股票代码" in c or "代码" in c), None)
        col_name   = next((c for c in df.columns if "股票简称" in c or "名称" in c or "简称" in c), None)
        col_price  = next((c for c in df.columns if "成交价格" in c or "价格" in c), None)
        col_vol    = next((c for c in df.columns if "成交量" in c or "数量" in c), None)
        col_amt    = next((c for c in df.columns if "成交额" in c or "金额" in c), None)
        col_type   = next((c for c in df.columns if "大单性质" in c or "性质" in c or "方向" in c), None)
        col_pct    = next((c for c in df.columns if "涨跌幅" in c), None)
        col_change = next((c for c in df.columns if "涨跌额" in c), None)
        if not col_code:
            logger.warning("[market_sentiment] fetch_big_deal: 未找到代码列")
            return
        count = 0
        for _, row in df.iterrows():
            try:
                deal_time  = str(row[col_time]).strip() if col_time else fetch_time
                stock_code = str(row[col_code]).strip()
                stock_name = str(row[col_name]).strip() if col_name else ""
                price      = float(row[col_price]) if col_price else 0.0
                volume     = int(float(row[col_vol])) if col_vol else 0
                amount     = float(row[col_amt]) if col_amt else 0.0
                deal_type  = str(row[col_type]).strip() if col_type else ""
                change_pct_raw = str(row[col_pct]).replace("%", "") if col_pct else None
                change_pct = float(change_pct_raw) if change_pct_raw not in (None, "nan", "") else None
                change_amt = float(row[col_change]) if col_change else None
                insert_big_deal(fetch_time, deal_time, stock_code, stock_name,
                                price, volume, amount, deal_type, change_pct, change_amt)
                count += 1
            except Exception:
                continue
        logger.info("[market_sentiment] fetch_big_deal 写入 %d 条", count)
    except Exception as e:
        logger.warning("[market_sentiment] fetch_big_deal failed: %s", e)

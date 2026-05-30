import atexit
from datetime import datetime
from datetime import time
from pathlib import Path

# 加载本地环境变量（开发环境覆盖用，.env.local 不提交）
_env_file = Path(__file__).parent / ".env.local"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            import os; os.environ.setdefault(_k.strip(), _v.strip())

from apscheduler.schedulers.background import BackgroundScheduler
from fetch_status import fetch_state as _fetch_state, fetch_lock as _fetch_lock

from fetcher.cls_news import fetch as fetch_cls
from fetcher.policy_rss import fetch as fetch_policy, fetch_cninfo
from fetcher.eastmoney import fetch_sector_flow, fetch_lhb
from fetcher.sector_heat import fetch_zt_pool, fetch_dt_pool, fetch_concept_heat, fetch_zbgc_pool, fetch_strong_pool
from fetcher.global_news import fetch_cls_red, fetch_em, fetch_ths, fetch_wscn, fetch_yicai, fetch_jin10, fetch_gelonghui
from fetcher.research import fetch as fetch_research
from db.storage import cleanup_old_data
from fetcher.realtime_quote import fetch_realtime_snapshot
from fetcher.concept_flow import fetch_concept_flow
from fetcher.market_sentiment import fetch_hot_rank_up, fetch_northbound_flow, fetch_xq_hot, fetch_big_deal
from fetcher.eastmoney import fetch_margin, fetch_block_trade, fetch_holder_count
from fetcher.fundamentals import fetch_fundamentals_finance, fetch_fundamentals_f10
from quant.daily_compute import run_daily_compute


def _auto_run(name: str, fn) -> None:
    """执行自动抓取任务，同步更新共享状态。手动抓取进行中时跳过状态更新。"""
    from datetime import datetime as _dt
    with _fetch_lock:
        if _fetch_state["status"] == "running":
            # 手动抓取优先，不覆盖其状态
            pass
        else:
            _fetch_state["status"] = "auto"
            _fetch_state["auto_task"] = name
    try:
        fn()
    except Exception:
        pass
    finally:
        with _fetch_lock:
            if _fetch_state["status"] == "auto":
                _fetch_state["auto_ts"] = _dt.now().strftime("%H:%M:%S")
                _fetch_state["auto_task"] = ""
                _fetch_state["status"] = "idle"


def _in_trade_hours() -> bool:
    now = datetime.now().time()
    return time(9, 15) <= now <= time(15, 5)


def _guarded_sector_flow():
    if _in_trade_hours(): _auto_run("行业资金流", fetch_sector_flow)

def _guarded_zt_pool():
    if _in_trade_hours(): _auto_run("涨停池", fetch_zt_pool)

def _guarded_dt_pool():
    if _in_trade_hours(): _auto_run("跌停池", fetch_dt_pool)

def _guarded_concept_heat():
    if _in_trade_hours(): _auto_run("概念热度", fetch_concept_heat)

def _guarded_realtime_quote():
    if _in_trade_hours(): _auto_run("实时行情", fetch_realtime_snapshot)

def _guarded_concept_flow():
    if _in_trade_hours(): _auto_run("概念资金流", fetch_concept_flow)

def _guarded_zbgc_pool():
    if _in_trade_hours(): _auto_run("炸板池", fetch_zbgc_pool)

def _guarded_strong_pool():
    if _in_trade_hours(): _auto_run("强势股", fetch_strong_pool)

def _guarded_hot_rank_up():
    if _in_trade_hours(): _auto_run("人气飙升", fetch_hot_rank_up)

def _guarded_northbound():
    if _in_trade_hours(): _auto_run("北向资金", fetch_northbound_flow)

def _guarded_xq_hot():
    _auto_run("雪球热度", fetch_xq_hot)

def _guarded_big_deal():
    if _in_trade_hours(): _auto_run("大单异动", fetch_big_deal)

def _guarded_margin():
    if _in_trade_hours(): _auto_run("融资融券", fetch_margin)

def _guarded_block_trade():
    if _in_trade_hours(): _auto_run("大宗交易", fetch_block_trade)

def _guarded_fundamentals():
    if _in_trade_hours():
        _auto_run("基本面财务", fetch_fundamentals_finance)
        _auto_run("基本面F10",  fetch_fundamentals_f10)


def start_scheduler() -> None:
    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")

    scheduler.add_job(lambda: _auto_run("财联社快讯",  fetch_cls),        "interval", minutes=5)
    scheduler.add_job(lambda: _auto_run("财联社红电报", fetch_cls_red),    "interval", minutes=5)
    scheduler.add_job(lambda: _auto_run("东方财富快讯", fetch_em),         "interval", minutes=5)
    scheduler.add_job(lambda: _auto_run("同花顺快讯",  fetch_ths),         "interval", minutes=5)
    scheduler.add_job(lambda: _auto_run("华尔街见闻",  fetch_wscn),        "interval", minutes=5)
    scheduler.add_job(lambda: _auto_run("第一财经",    fetch_yicai),       "interval", minutes=5)
    scheduler.add_job(lambda: _auto_run("金十数据",    fetch_jin10),       "interval", minutes=5)
    scheduler.add_job(lambda: _auto_run("格隆汇",      fetch_gelonghui),   "interval", minutes=5)
    scheduler.add_job(lambda: _auto_run("政策 RSS",    fetch_policy),      "interval", minutes=30)
    scheduler.add_job(lambda: _auto_run("巨潮公告",    fetch_cninfo),      "interval", minutes=30)
    scheduler.add_job(lambda: _auto_run("研究报告",    fetch_research),    "interval", minutes=30)
    scheduler.add_job(_guarded_sector_flow, "interval", minutes=15)
    scheduler.add_job(fetch_lhb, "cron", hour=17, minute=30)
    scheduler.add_job(_guarded_zt_pool, "interval", minutes=5)
    scheduler.add_job(_guarded_dt_pool, "interval", minutes=5)
    scheduler.add_job(_guarded_concept_heat, "interval", minutes=5)
    scheduler.add_job(cleanup_old_data, "cron", hour=2, minute=0)
    scheduler.add_job(_guarded_realtime_quote, "interval", seconds=30)
    scheduler.add_job(_guarded_concept_flow, "interval", minutes=15)
    scheduler.add_job(run_daily_compute, "cron", hour=9, minute=0)
    scheduler.add_job(_guarded_zbgc_pool, "interval", minutes=5)
    scheduler.add_job(_guarded_strong_pool, "interval", minutes=15)
    scheduler.add_job(_guarded_hot_rank_up, "interval", minutes=30)
    scheduler.add_job(_guarded_northbound,  "interval", minutes=15)
    scheduler.add_job(_guarded_xq_hot, "interval", minutes=63)
    scheduler.add_job(_guarded_big_deal, "interval", minutes=3)
    scheduler.add_job(_guarded_margin, "interval", minutes=30)
    scheduler.add_job(_guarded_block_trade, "interval", minutes=30)
    # 股东人数变化频率低，每天收盘后一次即可
    scheduler.add_job(lambda: _auto_run("股东人数", fetch_holder_count), "cron", hour=17, minute=45)
    scheduler.add_job(_guarded_fundamentals, "interval", minutes=60)

    scheduler.start()
    atexit.register(scheduler.shutdown)

    import threading

    def _initial_fetch():
        news_tasks = [
            ("财联社快讯",  fetch_cls),
            ("财联社红电报", fetch_cls_red),
            ("东方财富快讯", fetch_em),
            ("同花顺快讯",  fetch_ths),
            ("华尔街见闻",  fetch_wscn),
            ("第一财经",    fetch_yicai),
            ("金十数据",    fetch_jin10),
            ("格隆汇",      fetch_gelonghui),
            ("政策 RSS",    fetch_policy),
            ("巨潮公告",    fetch_cninfo),
            ("研究报告",    fetch_research),
        ]
        for name, fn in news_tasks:
            _auto_run(name, fn)
        if _in_trade_hours():
            trade_tasks = [
                ("行业资金流", fetch_sector_flow),
                ("涨停池",     fetch_zt_pool),
                ("跌停池",     fetch_dt_pool),
                ("概念热度",   fetch_concept_heat),
                ("概念资金流", fetch_concept_flow),
                ("炸板池",     fetch_zbgc_pool),
                ("强势股",     fetch_strong_pool),
                ("人气飙升",   fetch_hot_rank_up),
                ("北向资金",   fetch_northbound_flow),
                ("大单异动",   fetch_big_deal),
                ("实时行情",   fetch_realtime_snapshot),
                ("融资融券",   fetch_margin),
                ("大宗交易",   fetch_block_trade),
                ("基本面财务", fetch_fundamentals_finance),
                ("基本面F10",  fetch_fundamentals_f10),
            ]
            for name, fn in trade_tasks:
                _auto_run(name, fn)
        _auto_run("雪球热度", fetch_xq_hot)

    threading.Thread(target=_initial_fetch, daemon=True).start()

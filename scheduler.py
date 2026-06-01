import atexit
import os
from datetime import datetime
from datetime import time
from pathlib import Path

# 加载本地环境变量（开发环境覆盖用，.env.local 不提交）
_env_file = Path(__file__).parent / ".env.local"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

from apscheduler.schedulers.background import BackgroundScheduler
from fetch_status import fetch_state as _fetch_state, fetch_lock as _fetch_lock

from fetcher.cls_news import fetch as fetch_cls
from fetcher.policy_rss import fetch as fetch_policy, fetch_cninfo
from fetcher.eastmoney import fetch_sector_flow, fetch_lhb
from fetcher.lhb_local import fetch_lhb_local
from fetcher.sector_heat import fetch_zt_pool, fetch_dt_pool, fetch_concept_heat, fetch_zbgc_pool, fetch_strong_pool
from fetcher.global_news import fetch_cls_red, fetch_em, fetch_ths, fetch_wscn, fetch_yicai, fetch_jin10, fetch_gelonghui
from fetcher.research import fetch as fetch_research
from db.storage import cleanup_old_data
from fetcher.realtime_quote import fetch_realtime_snapshot
from fetcher.concept_flow import fetch_concept_flow
from fetcher.market_sentiment import fetch_hot_rank_up, fetch_northbound_flow, fetch_xq_hot, fetch_big_deal
from fetcher.eastmoney import (fetch_margin, fetch_block_trade, fetch_holder_count,
                               fetch_lockup_expiry, fetch_dividend_history,
                               fetch_industry_ranking, fetch_ths_hot_stocks)
from fetcher.fundamentals import fetch_fundamentals_finance, fetch_fundamentals_f10
from quant.daily_compute import run_daily_compute
from fetcher.backfill import run_backfill


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


def _guarded(name: str, fn) -> None:
    """仅在交易时段内执行 fn，否则跳过。"""
    if _in_trade_hours():
        _auto_run(name, fn)


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
    scheduler.add_job(lambda: _guarded("行业资金流", fetch_sector_flow),      "interval", minutes=15)
    scheduler.add_job(fetch_lhb, "cron", hour=17, minute=30)
    scheduler.add_job(lambda: _auto_run("龙虎榜本地", fetch_lhb_local), "cron", hour=18, minute=30)
    scheduler.add_job(lambda: _guarded("涨停池",     fetch_zt_pool),          "interval", minutes=5)
    scheduler.add_job(lambda: _guarded("跌停池",     fetch_dt_pool),          "interval", minutes=5)
    scheduler.add_job(lambda: _guarded("概念热度",   fetch_concept_heat),     "interval", minutes=5)
    scheduler.add_job(cleanup_old_data, "cron", hour=2, minute=0)
    scheduler.add_job(lambda: _guarded("实时行情",   fetch_realtime_snapshot),"interval", seconds=30)
    scheduler.add_job(lambda: _guarded("概念资金流", fetch_concept_flow),     "interval", minutes=15)
    # ── 静态数据每日计算（两次：盘前 + 盘后）──────────────────────────────────
    _compute_enabled = os.environ.get("COMPUTE_ENABLED", "true").lower() == "true"
    if _compute_enabled:
        scheduler.add_job(lambda: _auto_run("每日计算", run_daily_compute), "cron", hour=9,  minute=0)
        scheduler.add_job(lambda: _auto_run("每日计算", run_daily_compute), "cron", hour=21, minute=0)
    scheduler.add_job(lambda: _guarded("炸板池",     fetch_zbgc_pool),        "interval", minutes=5)
    scheduler.add_job(lambda: _guarded("强势股",     fetch_strong_pool),      "interval", minutes=15)
    scheduler.add_job(lambda: _guarded("人气飙升",   fetch_hot_rank_up),      "interval", minutes=30)
    scheduler.add_job(lambda: _guarded("北向资金",   fetch_northbound_flow),  "interval", minutes=15)
    scheduler.add_job(lambda: _auto_run("雪球热度",  fetch_xq_hot),           "interval", minutes=63)
    scheduler.add_job(lambda: _guarded("大单异动",   fetch_big_deal),         "interval", minutes=3)
    scheduler.add_job(lambda: _guarded("融资融券",   fetch_margin),           "interval", minutes=30)
    scheduler.add_job(lambda: _guarded("大宗交易",   fetch_block_trade),      "interval", minutes=30)
    # 股东人数变化频率低，每天收盘后一次即可
    scheduler.add_job(lambda: _auto_run("股东人数", fetch_holder_count), "cron", hour=17, minute=45)
    scheduler.add_job(_guarded_fundamentals, "interval", minutes=60)
    scheduler.add_job(lambda: _guarded("行业排行",   fetch_industry_ranking), "interval", minutes=5)
    scheduler.add_job(lambda: _guarded("同花顺主题热股", fetch_ths_hot_stocks),"interval", minutes=30)
    # 解禁/减持和分红历史变动慢，每天早上更新一次
    scheduler.add_job(lambda: _auto_run("解禁减持", fetch_lockup_expiry), "cron", hour=9, minute=10)
    scheduler.add_job(lambda: _auto_run("分红历史", fetch_dividend_history), "cron", hour=9, minute=12)

    # ── Agent 分析定时任务（8次/天）────────────────────────────────────────────
    # 通过 orchestrator.run_agent_analysis() 触发三阶段 multi-agent 管道（非阻塞）
    _agent_enabled = os.environ.get("AGENT_ENABLED", "true").lower() == "true"
    if _agent_enabled:
        from agent.orchestrator import run_agent_analysis

        def _run_agent(run_type: str) -> None:
            _auto_run(f"Agent-{run_type}", lambda: run_agent_analysis(run_type))

        # 每日4次：盘前完整 / 盘中轻量x2 / 盘后完整（21:00龙虎榜已稳定）
        scheduler.add_job(lambda: _run_agent("morning"),  "cron", hour=6,  minute=0)
        scheduler.add_job(lambda: _run_agent("intraday"), "cron", hour=10, minute=0)
        scheduler.add_job(lambda: _run_agent("intraday"), "cron", hour=13, minute=30)
        scheduler.add_job(lambda: _run_agent("evening"),  "cron", hour=21, minute=0)

    scheduler.start()
    atexit.register(scheduler.shutdown)

    import threading

    def _initial_fetch():
        # 新部署检测：zt_pool 为空时自动回填最近 7 个交易日历史数据
        run_backfill()

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
                ("股东人数",   fetch_holder_count),
                ("基本面财务", fetch_fundamentals_finance),
                ("基本面F10",  fetch_fundamentals_f10),
                ("行业排行",   fetch_industry_ranking),
                ("同花顺主题热股", fetch_ths_hot_stocks),
            ]
            for name, fn in trade_tasks:
                _auto_run(name, fn)
        _auto_run("雪球热度", fetch_xq_hot)
        _auto_run("解禁减持", fetch_lockup_expiry)
        _auto_run("分红历史", fetch_dividend_history)

    threading.Thread(target=_initial_fetch, daemon=True).start()

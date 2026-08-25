import atexit
import logging
import os
import threading
from datetime import datetime, time
from pathlib import Path

# 加载本地环境变量（开发环境覆盖用，.env.local 不提交）
_env_file = Path(__file__).parent / ".env.local"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

from apscheduler.schedulers.background import BackgroundScheduler
from core.fetch_status import fetch_state as _fetch_state, fetch_lock as _fetch_lock

from fetcher.cls_news import fetch as fetch_cls
from fetcher.policy_rss import fetch as fetch_policy, fetch_cninfo
from fetcher.eastmoney import fetch_sector_flow, fetch_lhb
from fetcher.lhb_local import fetch_lhb_local
from fetcher.sector_heat import (
    fetch_zt_pool,
    fetch_dt_pool,
    fetch_dt_pool_v3,
    fetch_concept_heat,
    fetch_zbgc_pool,
    fetch_strong_pool,
)
from fetcher.global_news import fetch_cls_red, fetch_em, fetch_ths, fetch_wscn, fetch_yicai, fetch_jin10, fetch_gelonghui
from fetcher.research import fetch as fetch_research
from db.storage import cleanup_old_data
from fetcher.market_breadth import fetch as fetch_market_breadth
from fetcher.concept_flow import fetch_concept_flow
from fetcher.market_sentiment import fetch_hot_rank_up, fetch_northbound_flow, fetch_xq_hot, fetch_big_deal
from fetcher.eastmoney import (fetch_margin, fetch_block_trade, fetch_holder_count,
                               fetch_lockup_expiry, fetch_dividend_history,
                               fetch_industry_ranking, fetch_ths_hot_stocks)

from quant.daily_compute import run_daily_compute, run_daily_compute_if_stale
from fetcher.backfill import run_backfill


def _auto_run(name: str, fn) -> None:
    """执行自动抓取任务，同步更新共享状态。手动抓取进行中时跳过状态更新。"""
    with _fetch_lock:
        if _fetch_state["status"] != "running":
            _fetch_state["status"] = "auto"
            _fetch_state["auto_task"] = name
    try:
        fn()
    except Exception:
        pass
    finally:
        with _fetch_lock:
            if _fetch_state["status"] == "auto":
                _fetch_state["auto_ts"] = datetime.now().strftime("%H:%M:%S")
                _fetch_state["auto_task"] = ""
                _fetch_state["status"] = "idle"


def _is_trade_day() -> bool:
    """判断今天是否是 A 股交易日（工作日且非节假日）。"""
    today = datetime.now()
    if today.weekday() >= 5:
        return False
    try:
        import akshare as ak
        df = ak.tool_trade_date_hist_sina()
        dates = set(df.iloc[:, 0].astype(str).str.replace("-", ""))
        return today.strftime("%Y%m%d") in dates
    except Exception:
        # akshare 失败时降级：仅排除周末，不排除节假日
        return today.weekday() < 5


def _in_trade_hours() -> bool:
    now = datetime.now().time()
    return time(9, 15) <= now <= time(15, 0)


def _guarded(name: str, fn) -> None:
    """仅在交易时段内执行 fn，否则跳过。"""
    if _in_trade_hours():
        _auto_run(name, fn)



def start_scheduler() -> None:
    scheduler = BackgroundScheduler(
        timezone="Asia/Shanghai",
        job_defaults={
            "misfire_grace_time": 900,  # 任务可延迟15分钟执行: in-process 全量股票加载(review_compute/daily_compute)会阻塞 GIL 2-5 分钟, 60s 时 30+ interval 任务成批被丢弃; 900s 覆盖已知阻塞窗口, 恢复后 coalesce 补跑一次
            "coalesce": True,           # 积压的同一任务只执行一次，不补跑
            "max_instances": 4,         # 不同任务可并发（之前=1 时 warm_industry_stats 永远等不到 instance）
        },
    )

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
    scheduler.add_job(lambda: _guarded("行业资金流",   fetch_sector_flow), "interval", minutes=15)
    scheduler.add_job(lambda: _auto_run("龙虎榜",      fetch_lhb),         "cron", hour=17, minute=30)
    scheduler.add_job(lambda: _auto_run("龙虎榜本地",  fetch_lhb_local),   "cron", hour=18, minute=30)
    scheduler.add_job(lambda: _guarded("涨停池",       fetch_zt_pool),     "interval", minutes=5)
    scheduler.add_job(lambda: _guarded("跌停池",       fetch_dt_pool),     "interval", minutes=5)
    scheduler.add_job(lambda: _guarded("QMT跌停池",    fetch_dt_pool_v3),  "interval", minutes=1)
    scheduler.add_job(lambda: _guarded("概念热度",     fetch_concept_heat),"interval", minutes=5)
    scheduler.add_job(cleanup_old_data, "cron", hour=2, minute=0)
    scheduler.add_job(lambda: _guarded("市场宽度",     fetch_market_breadth),    "interval", seconds=30)
    scheduler.add_job(lambda: _guarded("概念资金流",   fetch_concept_flow),      "interval", minutes=15)

    # QMT 行业统计 warm-up：5 分钟跑一次填 cache，避免前端首次请求全量算 5200+ 只股票
    # 走 subprocess 路径：之前 in-process 跑被 waitress 8 worker 抢 GIL, 让 CyclePage 进页卡 13s+
    # (扫 5200+ 只 stock CSV 期间所有 waitress thread 全卡 PyThread_acquire_lock)
    # 启动 warmup 仍走 in-process 同步 (一次性 30-60s OK, 不抢 GIL 周期性 scheduler 才是元凶)
    def _warm_industry_stats() -> None:
        from server import _refresh_qmt_industry_stats_cache_subprocess, _current_qmt_trade_date
        try:
            _refresh_qmt_industry_stats_cache_subprocess(_current_qmt_trade_date())
        except Exception as e:
            print(f"[scheduler] warm_industry_stats failed: {e}")

    scheduler.add_job(_warm_industry_stats, "interval", minutes=5)
    # ── 静态数据每日计算（盘前全量 + 盘后补漏）────────────────────────────────
    # 9:00 全量: 兜夜间迟到数据(研报/龙虎榜等)重算一遍
    # 21:00 补漏: 20:30 复盘链路已跑过同一批 compute, 这里只补缺失, 不重复算
    _compute_enabled = os.environ.get("COMPUTE_ENABLED", "true").lower() == "true"
    if _compute_enabled:
        scheduler.add_job(lambda: _auto_run("每日计算", run_daily_compute), "cron", hour=9,  minute=0)
        scheduler.add_job(lambda: _auto_run("每日计算(补漏)", run_daily_compute_if_stale), "cron", hour=21, minute=0)
    scheduler.add_job(lambda: _guarded("炸板池",       fetch_zbgc_pool),        "interval", minutes=5)
    scheduler.add_job(lambda: _guarded("强势股",       fetch_strong_pool),      "interval", minutes=15)
    scheduler.add_job(lambda: _guarded("人气飙升",     fetch_hot_rank_up),      "interval", minutes=30)
    scheduler.add_job(lambda: _guarded("北向资金",     fetch_northbound_flow),  "interval", minutes=15)
    scheduler.add_job(lambda: _auto_run("雪球热度",    fetch_xq_hot),           "interval", minutes=63)
    scheduler.add_job(lambda: _guarded("大单异动",     fetch_big_deal),         "interval", minutes=3)
    scheduler.add_job(lambda: _guarded("融资融券",     fetch_margin),           "interval", minutes=30)
    scheduler.add_job(lambda: _guarded("大宗交易",     fetch_block_trade),      "interval", minutes=30)
    # 股东人数变化频率低，每天收盘后一次即可
    scheduler.add_job(lambda: _auto_run("股东人数",    fetch_holder_count),     "cron", hour=17, minute=45)

    scheduler.add_job(lambda: _guarded("行业排行",     fetch_industry_ranking), "interval", minutes=5)
    scheduler.add_job(lambda: _guarded("同花顺主题热股", fetch_ths_hot_stocks), "interval", minutes=30)
    # 解禁/减持和分红历史变动慢，每天早上更新一次
    scheduler.add_job(lambda: _auto_run("解禁减持", fetch_lockup_expiry),  "cron", hour=9, minute=10)
    scheduler.add_job(lambda: _auto_run("分红历史", fetch_dividend_history),"cron", hour=9, minute=12)

    # ── Agent 分析定时任务（每日4次）────────────────────────────────────────────
    _agent_enabled = os.environ.get("AGENT_ENABLED", "true").lower() == "true"
    if _agent_enabled:
        from agent.orchestrator import run_agent_analysis
        from agent.info_brief_v2 import run as run_info_brief
        from agent.strategist import run as run_strategist

        def _run_agent(run_type: str) -> None:
            if not _is_trade_day():
                return
            _auto_run(f"Agent-{run_type}", lambda: run_agent_analysis(run_type))

        def _run_info_brief(run_type: str) -> None:
            if not _is_trade_day():
                return
            _auto_run(f"InfoBrief-{run_type}", lambda: run_info_brief(run_type=run_type))

        def _run_strategist(run_type: str) -> None:
            if not _is_trade_day():
                return
            _auto_run(f"Strategist-{run_type}", lambda: run_strategist(run_type=run_type))

        # 盘前完整 / 盘中轻量x2 / 盘后完整（21:00龙虎榜已稳定）
        scheduler.add_job(lambda: _run_agent("morning"),  "cron", hour=6,  minute=0)
        scheduler.add_job(lambda: _run_agent("intraday"), "cron", hour=10, minute=0)
        scheduler.add_job(lambda: _run_agent("intraday"), "cron", hour=13, minute=30)
        scheduler.add_job(lambda: _run_agent("evening"),  "cron", hour=21, minute=0)

        # ── 信息情报简报 (info_brief), 在 chief 之后 5 分钟跑 ──
        _info_brief_enabled = os.environ.get("INFO_BRIEF_ENABLED", "true").lower() == "true"
        if _info_brief_enabled:
            scheduler.add_job(lambda: _run_info_brief("morning"),  "cron", hour=6,  minute=5)
            scheduler.add_job(lambda: _run_info_brief("intraday"), "cron", hour=10, minute=5)
            scheduler.add_job(lambda: _run_info_brief("intraday"), "cron", hour=13, minute=35)
            scheduler.add_job(lambda: _run_info_brief("evening"),  "cron", hour=21, minute=5)

            # ── 战略推理 (strategist), 在 info_brief 之后 5 分钟跑 ──
            # 基于最近一次 info_brief 输出做麦肯锡框架推理
            _strategist_enabled = os.environ.get("STRATEGIST_ENABLED", "true").lower() == "true"
            if _strategist_enabled:
                scheduler.add_job(lambda: _run_strategist("morning"),  "cron", hour=6,  minute=10)
                scheduler.add_job(lambda: _run_strategist("intraday"), "cron", hour=10, minute=10)
                scheduler.add_job(lambda: _run_strategist("intraday"), "cron", hour=13, minute=40)
                scheduler.add_job(lambda: _run_strategist("evening"),  "cron", hour=21, minute=10)

        # ── 复盘 (review_v2), 盘后 20:30 跑 ──
        # 链路: Exodia 增量更新全部 (等完成, 最多 25min) → review_v2 复盘 → DM-kun 级联
        # 原 16:00 — Exodia 收盘数据没那么快同步完, kun 要求改晚上: 先更新数据再复盘。
        from agent.review_v2 import run as run_review_v2
        _review_enabled = os.environ.get("REVIEW_ENABLED", "true").lower() == "true"
        if _review_enabled:
            def _run_review() -> None:
                if not _is_trade_day():
                    return
                # 1. 先跑「数据更新」页的 增量更新全部 (exodia all_data), 等它完成
                try:
                    from server import exodia_update_all_and_wait
                    _st = exodia_update_all_and_wait(timeout_sec=1500)
                    logging.getLogger(__name__).info(
                        "[scheduler] Exodia 增量更新: %s", _st)
                except Exception:
                    logging.getLogger(__name__).warning(
                        "[scheduler] Exodia 增量更新异常, 继续复盘", exc_info=True)
                # 2. 复盘 + 级联刷新 DM-kun 6 个 tab
                _auto_run("Review-9维", lambda: run_review_v2())
                try:
                    from api.dm_kun import dm_kun_recompute_all
                    dm_kun_recompute_all()
                except Exception:
                    logging.getLogger(__name__).warning(
                        "[scheduler] Review 后 DM-kun 级联刷新失败", exc_info=True)
                # 3. AI 总结: 9 维度 + DM-kun 数据喂给 claude, 落 agent_summary (run_type=review_ai)
                try:
                    from api.dm_kun import review_ai_with_dm_cache
                    _ai = review_ai_with_dm_cache()
                    logging.getLogger(__name__).info(
                        "[scheduler] 复盘 AI 总结: %s", _ai.get("status"))
                except Exception:
                    logging.getLogger(__name__).warning(
                        "[scheduler] 复盘 AI 总结失败", exc_info=True)
            scheduler.add_job(_run_review, "cron", hour=20, minute=30)

    scheduler.start()
    atexit.register(scheduler.shutdown)

    def _initial_fetch():
        # 新部署检测：zt_pool 为空时自动回填最近 7 个交易日历史数据
        run_backfill()

        news_tasks = [
            ("财联社快讯",   fetch_cls),
            ("财联社红电报", fetch_cls_red),
            ("东方财富快讯", fetch_em),
            ("同花顺快讯",   fetch_ths),
            ("华尔街见闻",   fetch_wscn),
            ("第一财经",     fetch_yicai),
            ("金十数据",     fetch_jin10),
            ("格隆汇",       fetch_gelonghui),
            ("政策 RSS",     fetch_policy),
            ("巨潮公告",     fetch_cninfo),
            ("研究报告",     fetch_research),
        ]
        for name, fn in news_tasks:
            _auto_run(name, fn)
        if _in_trade_hours():
            trade_tasks = [
                ("行业资金流",     fetch_sector_flow),
                ("涨停池",         fetch_zt_pool),
                ("跌停池",         fetch_dt_pool),
                ("QMT跌停池",      fetch_dt_pool_v3),
                ("概念热度",       fetch_concept_heat),
                ("概念资金流",     fetch_concept_flow),
                ("炸板池",         fetch_zbgc_pool),
                ("强势股",         fetch_strong_pool),
                ("人气飙升",       fetch_hot_rank_up),
                ("北向资金",       fetch_northbound_flow),
                ("大单异动",       fetch_big_deal),
                ("市场宽度",       fetch_market_breadth),
                ("融资融券",       fetch_margin),
                ("大宗交易",       fetch_block_trade),
                ("股东人数",       fetch_holder_count),

                ("行业排行",       fetch_industry_ranking),
                ("同花顺主题热股", fetch_ths_hot_stocks),
            ]
            for name, fn in trade_tasks:
                _auto_run(name, fn)
        _auto_run("雪球热度", fetch_xq_hot)
        _auto_run("解禁减持", fetch_lockup_expiry)
        _auto_run("分红历史", fetch_dividend_history)

    threading.Thread(target=_initial_fetch, daemon=True).start()

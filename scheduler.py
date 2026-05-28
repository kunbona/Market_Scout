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

from fetcher.cls_news import fetch as fetch_cls
from fetcher.policy_rss import fetch as fetch_policy, fetch_cninfo
from fetcher.eastmoney import fetch_sector_flow, fetch_lhb
from fetcher.sector_heat import fetch_zt_pool, fetch_dt_pool, fetch_concept_heat
from fetcher.global_news import fetch_cls_red, fetch_em, fetch_ths, fetch_wscn, fetch_yicai, fetch_jin10, fetch_gelonghui
from fetcher.research import fetch as fetch_research
from db.storage import cleanup_old_data


def _in_trade_hours() -> bool:
    now = datetime.now().time()
    return time(9, 15) <= now <= time(15, 5)


def _guarded_sector_flow():
    if _in_trade_hours():
        fetch_sector_flow()


def _guarded_zt_pool():
    if _in_trade_hours():
        fetch_zt_pool()


def _guarded_dt_pool():
    if _in_trade_hours():
        fetch_dt_pool()


def _guarded_concept_heat():
    if _in_trade_hours():
        fetch_concept_heat()


def start_scheduler() -> None:
    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")

    scheduler.add_job(fetch_cls, "interval", minutes=5)
    scheduler.add_job(fetch_cls_red, "interval", minutes=5)
    scheduler.add_job(fetch_em, "interval", minutes=5)
    scheduler.add_job(fetch_ths, "interval", minutes=5)
    scheduler.add_job(fetch_wscn, "interval", minutes=5)
    scheduler.add_job(fetch_yicai, "interval", minutes=5)
    scheduler.add_job(fetch_jin10,     "interval", minutes=5)
    scheduler.add_job(fetch_gelonghui, "interval", minutes=5)
    scheduler.add_job(fetch_policy, "interval", minutes=30)
    scheduler.add_job(fetch_cninfo, "interval", minutes=30)
    scheduler.add_job(fetch_research, "interval", minutes=30)
    scheduler.add_job(_guarded_sector_flow, "interval", minutes=15)
    scheduler.add_job(fetch_lhb, "cron", hour=17, minute=5)
    scheduler.add_job(_guarded_zt_pool, "interval", minutes=5)
    scheduler.add_job(_guarded_dt_pool, "interval", minutes=5)
    scheduler.add_job(_guarded_concept_heat, "interval", minutes=5)
    scheduler.add_job(cleanup_old_data, "cron", hour=2, minute=0)

    scheduler.start()
    atexit.register(scheduler.shutdown)

    import threading

    def _initial_fetch():
        for fn in [fetch_cls, fetch_cls_red, fetch_em, fetch_ths, fetch_wscn, fetch_yicai, fetch_jin10, fetch_gelonghui, fetch_policy, fetch_cninfo, fetch_research]:
            try:
                fn()
            except Exception:
                pass
        if _in_trade_hours():
            for fn in [fetch_sector_flow, fetch_zt_pool, fetch_dt_pool, fetch_concept_heat]:
                try:
                    fn()
                except Exception:
                    pass

    threading.Thread(target=_initial_fetch, daemon=True).start()

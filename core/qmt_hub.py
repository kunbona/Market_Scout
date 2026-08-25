"""core/qmt_hub.py — QMT 实时数据缓存枢纽 (自 server.py 拆出)。

承载 QMT 行业统计 cache、后台刷新冷却状态、运行时状态探测、
市场宽度 / 跌停池读取助手、industry stats warmup 引导。
纯 Python, 不依赖 Flask; core/scheduler.py 与 api/qmt.py 共用本模块。

storage 函数 (get_dt_pool_v3 / get_market_breadth_latest) 一律函数体内
懒导入 —— 测试 mock db.storage.xxx 时调用时才解析, patch 生效。
"""
import logging
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

_QMT_BACKGROUND_REFRESH_COOLDOWN_SECONDS = 30.0
_qmt_background_refresh_lock = threading.Lock()
_qmt_background_refresh_started_at: dict[str, float] = {}
_qmt_industry_stats_cache_lock = threading.Lock()
_qmt_industry_stats_cache: dict[str, object] = {
    "trade_date": "",
    "payload": None,
}

# 标记 industry stats warmup 是否完成（Flask 启动时等这个 event）
_industry_stats_warmup_done = threading.Event()


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _current_qmt_trade_date() -> str:
    today = _today()
    try:
        from fetcher.qmt_data_api import get_current_qmt_trade_date

        return get_current_qmt_trade_date(today)
    except Exception:
        pass
    return today


def _read_qmt_runtime_status() -> dict:
    qmt_enabled = os.environ.get("QMT_ENABLED", "false").lower() in ("true", "1", "yes")
    qmt_connected = False
    qmt_version = None
    if qmt_enabled:
        try:
            from fetcher.xtquant_breadth import connect as _qmt_connect, get_version as _qmt_ver

            qmt_connected = _qmt_connect()
            qmt_version = _qmt_ver()
        except Exception:
            pass
    # 熔断器快照：VM 不通时短路所有 bridge 调用，避免持续 30s 重试耗光资源
    breaker_snapshot: dict = {}
    try:
        from fetcher.qmt_breaker import snapshot as breaker_snapshot_fn
        breaker_snapshot = breaker_snapshot_fn()
    except Exception:
        pass
    return {
        "enabled": qmt_enabled,
        "connected": qmt_connected,
        "version": qmt_version,
        "breaker": breaker_snapshot,
    }


def _latest_total_market_breadth_row() -> dict | None:
    from db.storage import get_market_breadth_latest
    rows = get_market_breadth_latest(10)
    for row in reversed(rows):
        if str(row.get("market") or "").upper() == "TOTAL":
            return row
    return rows[-1] if rows else None


def _find_total_market_breadth_row(rows: list[dict], target_trade_date: str | None = None) -> dict | None:
    for row in reversed(rows):
        if str(row.get("market") or "").upper() != "TOTAL":
            continue
        if target_trade_date is not None:
            row_trade_date = _extract_fetch_date(row.get("fetch_time"))
            if row_trade_date != target_trade_date:
                continue
        return row
    if target_trade_date is None and rows:
        return rows[-1]
    return None


def _extract_fetch_date(fetch_time: str | None) -> str | None:
    value = str(fetch_time or "").strip()
    if not value:
        return None
    return value[:10] if len(value) >= 10 else None


def _schedule_qmt_background_refresh(refresh_name: str, refresh_fn: Callable[[], None]) -> bool:
    now = time.monotonic()
    with _qmt_background_refresh_lock:
        last_started_at = _qmt_background_refresh_started_at.get(refresh_name, 0.0)
        if now - last_started_at < _QMT_BACKGROUND_REFRESH_COOLDOWN_SECONDS:
            return False
        _qmt_background_refresh_started_at[refresh_name] = now

    def _runner() -> None:
        try:
            refresh_fn()
        except Exception:
            pass

    threading.Thread(
        target=_runner,
        daemon=True,
        name=f"qmt-refresh-{refresh_name}",
    ).start()
    return True


def _get_qmt_overview_breadth(status: dict) -> dict | None:
    from db.storage import get_market_breadth_latest
    target_trade_date = _current_qmt_trade_date()
    rows = get_market_breadth_latest(240)
    breadth = _find_total_market_breadth_row(rows, target_trade_date)
    if breadth:
        return breadth

    breadth = _find_total_market_breadth_row(rows)
    latest_date = _extract_fetch_date(breadth.get("fetch_time") if breadth else None)
    if latest_date == target_trade_date:
        return breadth
    if not status.get("enabled") or not status.get("connected"):
        return breadth

    try:
        from fetcher.market_breadth import fetch as fetch_market_breadth

        _schedule_qmt_background_refresh("market_breadth", fetch_market_breadth)
    except Exception:
        pass
    return breadth


def _get_qmt_limit_down_rows(status: dict) -> list[dict]:
    from db.storage import get_dt_pool_v3
    target_trade_date = _current_qmt_trade_date()
    target_rows = get_dt_pool_v3(target_trade_date)
    if target_rows:
        return target_rows

    rows = get_dt_pool_v3(None)
    latest_trade_date = str(rows[0].get("trade_date") or "").strip() if rows else ""
    if latest_trade_date == target_trade_date:
        return rows
    if not status.get("enabled") or not status.get("connected"):
        return rows

    try:
        from fetcher.sector_heat import fetch_dt_pool_v3

        _schedule_qmt_background_refresh(
            f"dt_pool_v3:{target_trade_date}",
            lambda: fetch_dt_pool_v3(target_trade_date),
        )
    except Exception:
        pass
    return rows


def _empty_qmt_industry_stats_payload() -> dict:
    return {
        "summary": {
            "sector_count": 0,
            "strong_ma10_sector_count": 0,
            "top_meat_sector": None,
            "top_main_inflow_sector": None,
            "data_date": None,
            "data_lag_days": None,
        },
        "items": [],
    }


def _refresh_qmt_industry_stats_cache_subprocess(target_trade_date: str) -> bool:
    """
    用 subprocess 跑 build_qmt_industry_stats_source_rows + payload,
    通过 pickle 中介把结果回写到 server 进程 cache dict。

    之前 in-process 跑被 scheduler 5min 触发一次, 扫 5200+ 只 stock CSV
    (ThreadPoolExecutor 32 worker) 抢 GIL, 让 waitress 8 worker 全卡死
    → CyclePage 进页 13s+。subprocess 跑完全隔离, server 进程无感知。

    返回 True/False (成功/失败)。失败时 caller 保留旧 cache (stale 也比 500 好)。
    """
    import pickle
    import subprocess
    from pathlib import Path as _P

    py = sys.executable  # server 自己用的 .venv python (已经 import qmt_data_api 等)
    cache_file = _P("/tmp/qmt_industry_stats_cache.pkl")
    log_file = _P("/tmp/qmt_industry_stats_refresh.log")

    # 一行 code (subprocess -c 单行), 调 build_*, pickle 写到 cache_file
    # 注: 本文件在 core/ 子目录, sys.path 注入需上溯一层到项目根
    code = (
        "import sys as _s, pickle as _pk, json as _j; "
        f"_s.path.insert(0, '{Path(__file__).resolve().parent.parent}'); "
        "from fetcher.qmt_monitors import build_qmt_industry_stats_source_rows, build_qmt_industry_stats_payload; "
        f"rows = build_qmt_industry_stats_source_rows('{target_trade_date}'); "
        "payload = build_qmt_industry_stats_payload(rows); "
        f"open('{cache_file}', 'wb').write(_pk.dumps({{'trade_date': '{target_trade_date}', 'payload': payload}})); "
        "print(f'[industry_subprocess] done: items={len(payload.get(\"items\", []))} rows={len(rows)}', flush=True); "
    )

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    try:
        proc = subprocess.Popen(
            [py, "-c", code],
            stdout=open(log_file, "a"),
            stderr=subprocess.STDOUT,
            env=env,
        )
    except Exception as e:
        logger.warning("[qmt-industry-subprocess] 启动失败: %s", e)
        return False

    # 后台等, 不阻塞 caller (caller 多半是 scheduler 5min 跑)
    def _wait_and_load():
        try:
            proc.wait(timeout=600)  # 10min 上限 (CSV 5200+ 全市场可能 1-3min)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                _qmt_industry_stats_cache  # noqa - 引用以确认 module state
                with _qmt_industry_stats_cache_lock:
                    _qmt_industry_stats_cache["payload"] = _empty_qmt_industry_stats_payload()
            except Exception:
                pass
            return
        if proc.returncode != 0:
            return
        try:
            data = pickle.loads(cache_file.read_bytes())
        except Exception as e:
            logger.warning("[qmt-industry-subprocess] 读 pickle 失败: %s", e)
            return
        with _qmt_industry_stats_cache_lock:
            _qmt_industry_stats_cache["trade_date"] = data.get("trade_date", target_trade_date)
            _qmt_industry_stats_cache["payload"] = data.get("payload", _empty_qmt_industry_stats_payload())

    threading.Thread(target=_wait_and_load, daemon=True, name="industry-stats-load").start()
    return True


def _refresh_qmt_industry_stats_cache(target_trade_date: str) -> None:
    """
    QMT 行业统计 cache 刷新。

    启动 warmup 一次性 (in-process, 30-60s 同步, OK);
    scheduler 5min 周期走 subprocess 路径 (避免反复抢 waitress GIL).
    Caller 选路径:
    - _bootstrap_industry_stats_warmup → _refresh_qmt_industry_stats_cache_inprocess (同步, 启动只一次)
    - scheduler._warm_industry_stats    → _refresh_qmt_industry_stats_cache_subprocess (异步, 不抢 GIL)
    """
    # 默认走 in-process (启动 warmup 用, 一次性, OK)
    _refresh_qmt_industry_stats_cache_inprocess(target_trade_date)


def _refresh_qmt_industry_stats_cache_inprocess(target_trade_date: str) -> None:
    """in-process 同步刷新: 启动 warmup 一次性用, scheduler 周期别调 (会抢 GIL)."""
    from fetcher.qmt_monitors import (
        build_qmt_industry_stats_payload,
        build_qmt_industry_stats_source_rows,
    )
    rows = build_qmt_industry_stats_source_rows(target_trade_date)
    payload = build_qmt_industry_stats_payload(rows)
    with _qmt_industry_stats_cache_lock:
        _qmt_industry_stats_cache["trade_date"] = target_trade_date
        _qmt_industry_stats_cache["payload"] = payload


def _get_qmt_industry_stats_payload(status: dict | None = None) -> dict:
    runtime_status = status or _read_qmt_runtime_status()
    if not runtime_status.get("enabled") or not runtime_status.get("connected"):
        return _empty_qmt_industry_stats_payload()

    target_trade_date = _current_qmt_trade_date()
    with _qmt_industry_stats_cache_lock:
        cached_trade_date = str(_qmt_industry_stats_cache.get("trade_date") or "")
        cached_payload = _qmt_industry_stats_cache.get("payload")
    if cached_trade_date == target_trade_date and isinstance(cached_payload, dict):
        return cached_payload

    # 缓存未命中时同步计算，避免首次请求返回空数据
    _refresh_qmt_industry_stats_cache(target_trade_date)
    with _qmt_industry_stats_cache_lock:
        return _qmt_industry_stats_cache.get("payload") or _empty_qmt_industry_stats_payload()


def _bootstrap_industry_stats_warmup() -> None:
    """server 启动后后台 warm 一次 industry stats cache，
    避免前端首次请求触发同步计算（5200+ 只股票 ~40s）。
    调用方在进程启动时调用，那时所有函数都已定义。"""
    global _industry_stats_warmup_done
    _industry_stats_warmup_done = threading.Event()

    def _run():
        try:
            _refresh_qmt_industry_stats_cache(_current_qmt_trade_date())
        except Exception as e:
            print(f"[warmup] industry_stats failed: {e}")
        finally:
            _industry_stats_warmup_done.set()

    threading.Thread(target=_run, daemon=True, name="industry-stats-warmup").start()


def _wait_for_industry_stats_cache(timeout: int = 90) -> None:
    """等 industry stats warmup 完成（最多 timeout 秒）。
    调用方在 Flask 启动前阻塞，避免 waitress 接收请求时 cache 还是空。"""
    if _industry_stats_warmup_done.wait(timeout=timeout):
        items = (_qmt_industry_stats_cache.get("payload") or {}).get("items") or []
        print(f"[warmup] industry_stats 完成，{len(items)} 个行业")
    else:
        print(f"[warmup] industry_stats 超时 {timeout}s，Flask 先启；前端首请求可能慢")

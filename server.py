"""
Market Scout — Flask 服务
启动方式：bash start.sh
端口通过 FLASK_PORT 环境变量配置（默认 20026），写入 .env.local 持久化。
"""

import sys
import os
import logging
import threading
import time
import warnings
from datetime import datetime
from pathlib import Path
from typing import Callable

# 自动加载 .env.local (QMT_ENABLED / QMT_BRIDGE_URL / QMT_BRIDGE_TOKEN 等)
# 避免每次手动 source .env.local 才能让 QMT 数据源生效
try:
    from dotenv import load_dotenv
    _env_local = Path(__file__).parent / ".env.local"
    if _env_local.exists():
        load_dotenv(_env_local, override=False)  # 不覆盖已设 env (priority 给 shell)
except ImportError:
    pass  # 没装 python-dotenv 也没事, bash 启的 start.sh 会处理

# 加载 .env.local (项目根 + 当前目录), 不覆盖已有 env, 解决 "server 不读 .env.local" 老大难
try:
    from dotenv import load_dotenv
    for _env_path in [
        os.path.join(os.path.dirname(__file__), ".env.local"),
        os.path.join(os.path.dirname(__file__), ".env"),
    ]:
        if os.path.exists(_env_path):
            load_dotenv(_env_path, override=False)
            break
except ImportError:
    pass

# 强制 line-buffering, 已在 _main() 里 (避免 module-level 触发 reconfigure
#  在 background thread 调 `from server import ...` 引起 server.py 重 exec 失败)

# 自定义 PIPELINE 级别（25），介于 INFO(20) 和 WARNING(30) 之间
# Claude/Kimi 对话内容走这个级别，根 logger 设 WARNING 压掉噪音后仍可见
PIPELINE_LEVEL = 25
logging.addLevelName(PIPELINE_LEVEL, "PIPELINE")

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
    force=True,
)
logger = logging.getLogger(__name__)
# 复盘重算进度跟踪 (默认 WARNING 看不到 INFO)
logging.getLogger("quant.review_compute").setLevel(logging.INFO)
# Flask 启动/请求日志保留 WARNING，apscheduler 完全静默
logging.getLogger("werkzeug").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)

# akshare 内部 pandas 兼容问题，与本项目代码无关，静默掉
warnings.filterwarnings("ignore", message="A value is trying to be set on a copy of a slice")

# 加载本地配置（.env.local 优先级最高，覆盖 .env）
def _load_env_local():
    env_path = os.path.join(os.path.dirname(__file__), ".env.local")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, val)


def _raise_file_limit():
    """industry_stats 全量算 5200+ 只股票会瞬时打开大量 CSV，
    macOS 默认 ulimit 256 触发 Too many open files。plist 配的 SoftResourceLimits
    在某些 launchd 版本下不生效，启动时主动抬到 8192 兜底。"""
    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        if soft < 8192:
            resource.setrlimit(resource.RLIMIT_NOFILE, (8192, max(8192, hard)))
    except Exception:
        pass


_raise_file_limit()
_load_env_local()


def _bootstrap_industry_stats_warmup() -> None:
    """server 启动后后台 warm 一次 industry stats cache，
    避免前端首次请求触发同步计算（5200+ 只股票 ~40s）。
    函数在文件末尾调，那时所有函数都已定义。"""
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

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

# Allow importing db/storage from the project root
sys.path.insert(0, os.path.dirname(__file__))
from db.storage import (
    get_dt_pool_v3,
    get_market_breadth_latest,
)
from fetcher.qmt_monitors import (
    build_qmt_industry_draggers_payload,
    build_qmt_industry_stats_payload,
    build_qmt_industry_stats_source_rows,
    build_qmt_limit_down_monitor_payload,
)
from fetcher.sector_heat import fetch_dt_pool_v3

DIST = os.path.join(os.path.dirname(__file__), "dashboard", "dist")

app = Flask(__name__, static_folder=None, static_url_path="/_static_disabled_")
# jsonify 默认 sort_keys=True 会把 dict 按 key 字母序重排——中文按 Unicode 编码排,
# 热力图 latest(综合分降序) 被排乱。关掉: 保序 dict 原样序列化。
app.json.sort_keys = False
# 关键: 关掉 Flask 默认的 static catch-all 路由. 之前用 static_folder=DIST +
# static_url_path="" 时, Flask 自动注册了 "/<path:filename>" 路由, 比我们的
# @app.route("/<path:path>") serve_spa 先注册, 所有路径 (如 /review /strategist) 都被它
# 截走到 dist 里找文件, 找不到返 404, 彻底破坏 React Router SPA fallback.
# 修法: static_folder=None (不再 serve 静态), static_url_path 设成无意义前缀.
# 我们自己的 serve_spa 会显式 send_from_directory(DIST, ...) 并加 cache 头.
CORS(app)

# ── Blueprint 注册 (按域拆分自本文件, 一次一域) ─────────────────────────────
from api.industry_trend import bp as industry_trend_bp
from api.cycle import bp as cycle_bp
from api.wisburg import bp as wisburg_bp
from api.dm_kun import bp as dm_kun_bp
from api.exodia import bp as exodia_bp
from api.wecom import bp as wecom_bp
from api.review import bp as review_bp
from api.agent import bp as agent_bp
from api.market_data import bp as market_data_bp
from api.watchlist import bp as watchlist_bp, pools_bp as watchlist_pools_bp
from api.sector_stats import bp as sector_stats_bp
from api.compute import bp as compute_bp
from api.config import bp as config_bp
app.register_blueprint(industry_trend_bp)
app.register_blueprint(cycle_bp)
app.register_blueprint(wisburg_bp)
app.register_blueprint(dm_kun_bp)
app.register_blueprint(exodia_bp)
app.register_blueprint(wecom_bp)
app.register_blueprint(review_bp)
app.register_blueprint(agent_bp)
app.register_blueprint(market_data_bp)
app.register_blueprint(watchlist_bp)
app.register_blueprint(watchlist_pools_bp)
app.register_blueprint(sector_stats_bp)
app.register_blueprint(compute_bp)
app.register_blueprint(config_bp)


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


# ---------------------------------------------------------------------------
# Frontend (SPA)
# ---------------------------------------------------------------------------

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_spa(path):
    """Serve React SPA; fall back to index.html for client-side routing."""
    if path.startswith("api/"):
        from flask import abort
        abort(404)
    full = os.path.join(DIST, path)
    # 防路径穿越：确保解析后的绝对路径仍在 DIST 目录下
    real_full = os.path.realpath(full)
    real_dist = os.path.realpath(DIST)
    if path and not real_full.startswith(real_dist + os.sep):
        from flask import abort
        abort(403)
    if path and os.path.exists(full):
        # Hashed assets: cache aggressively
        resp = send_from_directory(DIST, path)
        if path.startswith("assets/"):
            resp.cache_control.max_age = 31536000
            resp.cache_control.immutable = True
        return resp
    # index.html: never cache — ensures browser picks up new asset hashes after deploy
    resp = send_from_directory(DIST, "index.html")
    resp.cache_control.no_cache = True
    resp.cache_control.no_store = True
    return resp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _ok(data):
    return jsonify({"success": True, "data": data, "ts": datetime.now().isoformat()})


def _err(msg: str, code: int = 500):
    return jsonify({"success": False, "error": str(msg)}), code


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


def _latest_market_breadth_row() -> dict | None:
    rows = get_market_breadth_latest(1)
    return rows[-1] if rows else None


def _latest_total_market_breadth_row() -> dict | None:
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
    code = (
        "import sys as _s, pickle as _pk, json as _j; "
        f"_s.path.insert(0, '{Path(__file__).resolve().parent}'); "
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


# ---------------------------------------------------------------------------
# QMT 实时监控 (与 server.py QMT 缓存枢纽纠缠; 纯 DB 池已拆去 api/market_data.py)
# ---------------------------------------------------------------------------

@app.route("/api/qmt-breaker")
def api_qmt_breaker():
    """QMT Bridge 熔断器状态（前端轻量轮询用）。"""
    try:
        from fetcher.qmt_breaker import snapshot, reset as breaker_reset
        snap = snapshot()
        # 暴露手动重置端点（前端 debug 按钮可调）
        if request.args.get("reset") == "1":
            breaker_reset()
            snap = snapshot()
        return _ok(snap)
    except Exception as exc:
        return _err(exc)


@app.route("/api/qmt-overview")
def api_qmt_overview():
    try:
        status = _read_qmt_runtime_status()
        breadth = _get_qmt_overview_breadth(status)
        limit_down_rows = _get_qmt_limit_down_rows(status)
        snapshot = {
            "updated_at": breadth["fetch_time"] if breadth else None,
            "source": breadth["source"] if breadth else "QMT",
            "market": breadth["market"] if breadth else None,
        }
        kpis = {
            "limit_down_count": len(limit_down_rows),
            "up_count": breadth["up_count"] if breadth else None,
            "down_count": breadth["down_count"] if breadth else None,
            "flat_count": breadth["flat_count"] if breadth else None,
            "turnover": breadth["total_amount"] if breadth else None,
        }
        return _ok({
            "status": status,
            "snapshot": snapshot,
            "kpis": kpis,
        })
    except Exception as exc:
        return _err(exc)


@app.route("/api/qmt-limit-down-monitor")
def api_qmt_limit_down_monitor():
    try:
        status = _read_qmt_runtime_status()
        breadth = _latest_total_market_breadth_row()
        rows = _get_qmt_limit_down_rows(status)
        payload = build_qmt_limit_down_monitor_payload(rows)
        payload["status"] = status
        payload["snapshot"] = {
            "updated_at": breadth["fetch_time"] if breadth else None,
            "source": breadth["source"] if breadth else "QMT",
            "market": breadth["market"] if breadth else None,
        }
        return _ok(payload)
    except Exception as exc:
        return _err(exc)


@app.route("/api/qmt-industry-draggers")
def api_qmt_industry_draggers():
    try:
        status = _read_qmt_runtime_status()
        breadth = _latest_total_market_breadth_row()
        rows = _get_qmt_limit_down_rows(status)
        payload = build_qmt_industry_draggers_payload(rows)
        payload["status"] = status
        payload["snapshot"] = {
            "updated_at": breadth["fetch_time"] if breadth else None,
            "source": breadth["source"] if breadth else "QMT",
            "market": breadth["market"] if breadth else None,
        }
        return _ok(payload)
    except Exception as exc:
        return _err(exc)


@app.route("/api/qmt-industry-stats")
def api_qmt_industry_stats():
    try:
        status = _read_qmt_runtime_status()
        breadth = _get_qmt_overview_breadth(status)
        payload = _get_qmt_industry_stats_payload(status)
        payload["status"] = status
        payload["snapshot"] = {
            "updated_at": breadth["fetch_time"] if breadth else None,
            "source": breadth["source"] if breadth else "xtquant",
            "market": breadth["market"] if breadth else None,
        }
        return _ok(payload)
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Manual fetch trigger
# ---------------------------------------------------------------------------

from core.fetch_status import fetch_state as _fetch_state, fetch_lock as _fetch_lock


def _run_fetch_all():
    """在后台线程执行全量抓取，不受交易时段限制。"""
    import threading
    from datetime import datetime as _dt

    try:
        from fetcher.cls_news import fetch as fetch_cls
        from fetcher.global_news import (
            fetch_cls_red, fetch_em, fetch_ths, fetch_wscn,
            fetch_yicai, fetch_jin10, fetch_gelonghui,
        )
        from fetcher.policy_rss import fetch as fetch_policy
        from fetcher.sector_heat import (
            fetch_zt_pool, fetch_dt_pool, fetch_zbgc_pool, fetch_strong_pool,
            fetch_concept_heat,
        )
        from fetcher.eastmoney import fetch_sector_flow, fetch_lhb
        from fetcher.lhb_local import fetch_lhb_local
        from fetcher.market_sentiment import (
            fetch_northbound_flow, fetch_hot_rank_up, fetch_xq_hot,
            fetch_big_deal,
        )
        from fetcher.concept_flow import fetch_concept_flow
        from fetcher.eastmoney import (fetch_margin, fetch_block_trade, fetch_holder_count,
                                       fetch_lockup_expiry, fetch_dividend_history,
                                       fetch_industry_ranking, fetch_ths_hot_stocks)

    except Exception as e:
        with _fetch_lock:
            _fetch_state.update({"status": "done", "results": [{"name": "导入失败", "ok": False, "error": str(e)}], "ts": _dt.now().strftime("%H:%M:%S")})
        return

    tasks = [
        ("财经快讯(财联社)",  fetch_cls),
        ("财联社红电报",      fetch_cls_red),
        ("东方财富快讯",      fetch_em),
        ("同花顺快讯",        fetch_ths),
        ("华尔街见闻",        fetch_wscn),
        ("第一财经",          fetch_yicai),
        ("金十数据",          fetch_jin10),
        ("格隆汇",            fetch_gelonghui),
        ("政策动态",          fetch_policy),
        ("行业资金流",        fetch_sector_flow),
        ("涨停池",            fetch_zt_pool),
        ("跌停池",            fetch_dt_pool),
        ("炸板池",            fetch_zbgc_pool),
        ("强势股",            fetch_strong_pool),
        ("概念热度",          fetch_concept_heat),
        ("龙虎榜",            fetch_lhb),
        ("龙虎榜席位",        fetch_lhb_local),
        ("北向资金",          fetch_northbound_flow),
        ("人气飙升",          fetch_hot_rank_up),
        ("雪球热度",          fetch_xq_hot),
        ("大单异动",          fetch_big_deal),
        ("概念资金流",        fetch_concept_flow),
        ("融资融券",          fetch_margin),
        ("大宗交易",          fetch_block_trade),
        ("股东人数",          fetch_holder_count),

        ("行业排行",          fetch_industry_ranking),
        ("同花顺主题热股",    fetch_ths_hot_stocks),
        ("解禁减持",          fetch_lockup_expiry),
        ("分红历史",          fetch_dividend_history),
    ]

    results = []
    for name, fn in tasks:
        try:
            fn()
            results.append({"name": name, "ok": True})
        except Exception as e:
            results.append({"name": name, "ok": False, "error": str(e)[:80]})
        with _fetch_lock:
            _fetch_state["results"] = list(results)

    with _fetch_lock:
        _fetch_state.update({"status": "done", "ts": _dt.now().strftime("%H:%M:%S")})


@app.route("/api/fetch-all", methods=["POST"])
def api_fetch_all():
    """手动触发全量数据抓取（不受交易时段限制）。立即返回，后台执行。"""
    import threading
    with _fetch_lock:
        if _fetch_state["status"] == "running":
            return _ok({"started": False, "message": "抓取任务已在进行中"})
        _fetch_state.update({"status": "running", "results": []})
    t = threading.Thread(target=_run_fetch_all, daemon=True, name="fetch-all-worker")
    t.start()
    return _ok({"started": True})


@app.route("/api/fetch-all-status")
def api_fetch_all_status():
    """轮询抓取进度。"""
    with _fetch_lock:
        return _ok(dict(_fetch_state))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _serve(port: int) -> None:
    """用 waitress 生产级 WSGI 服务器启动，无开发警告。"""
    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=port, threads=8)
    except ImportError:
        # waitress 未安装时降级到 Flask 内置服务器
        app.run(host="0.0.0.0", port=port, debug=False,
                use_reloader=False, threaded=True)


def start_flask(port: int = 20026):
    """在后台线程启动 WSGI 服务器。"""
    import threading
    t = threading.Thread(target=lambda: _serve(port), daemon=True, name="flask-api")
    t.start()
    return t


if __name__ == "__main__":
    import signal, multiprocessing

    # 强制 line-buffering (主进程启动时跑一次, module-level 不能放)
    # background thread 调 `from server import ...` 触发 module 重 exec 时
    # sys.stdout 可能是 cycle_signal._RefreshStream (没 reconfigure 方法),
    # 放 module-level 会抛 AttributeError 阻断 import
    try:
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass

    # ── 修复坏 stdin (2026-08-25) ──────────────────────────────────────────
    # nohup/沙箱会话结束时父进程的控制终端关闭, fd 0 变成无效描述符;
    # 子进程 (subprocess.run 默认继承) 启动时 Python init_sys_streams 拿不到
    # 标准流 → "OSError: [Errno 9] Bad file descriptor", 即 /api/data-health
    # 500 的根因。这里把 fd 0 重定向到 /dev/null, 子进程继承的就是干净的空输入。
    try:
        os.fstat(0)
    except OSError:
        _devnull_fd = os.open(os.devnull, os.O_RDONLY)
        os.dup2(_devnull_fd, 0)
        os.close(_devnull_fd)
        sys.stdin = os.fdopen(0, "r")
        print("[server] stdin 为坏描述符, 已重定向到 /dev/null")

    # ── 清理死代理 (2026-08-25) ─────────────────────────────────────────────
    # 沙箱/临时会话注入的 HTTP(S)_PROXY (如 127.0.0.1:50944) 在服务继承后常驻,
    # 会话结束代理进程消失 → 所有外网抓取 ProxyError (财联社/东财/同花顺/QMT桥
    # 全部失败)。启动时探测一次, 不通就全部清掉, 走直连。
    try:
        import socket as _socket
        from urllib.parse import urlparse as _urlparse
        _proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("http_proxy") or os.environ.get("HTTP_PROXY") or ""
        if _proxy_url:
            _p = _urlparse(_proxy_url if "://" in _proxy_url else "http://" + _proxy_url)
            _dead = False
            if _p.hostname in ("127.0.0.1", "localhost", "::1"):
                try:
                    with _socket.create_connection((_p.hostname, _p.port or 80), timeout=0.5):
                        pass
                except OSError:
                    _dead = True
            if _dead:
                for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
                    os.environ.pop(_k, None)
                print(f"[server] 检测到死代理 {_proxy_url} (端口无监听), 已清除全部代理环境变量")
    except Exception as _e:
        print(f"[server] 代理探测异常(忽略, 不影响启动): {_e}")

    _port = int(os.environ.get("FLASK_PORT", 20026))

    # 同步预热 industry stats（在 scheduler 启动**之前**）：
    # 否则 fetch_dt_pool_v3 / fetch_zt_pool 等 scheduler task 会跟 warmup 抢 I/O，
    # 40s 算力被分散到 5+ 分钟，前端请求同步等
    print(f"[warmup] industry_stats 启动预热（scheduler 未起，独占 I/O）...")
    _bootstrap_industry_stats_warmup()
    # warmup 最多等 30 秒(原 120s), 外置盘 I/O 慢/缺文件时不再卡死启动
    _wait_for_industry_stats_cache(timeout=30)

    from core.scheduler import start_scheduler
    start_scheduler()
    start_flask(_port)

    # 后台预热股票名称索引（首次搜索不必等待遍历 5000+ CSV）
    try:
        from agent.stock_search import warm_up
        warm_up()
    except Exception:
        pass

    # 后台预热 DM-kun 市场分析 (3 个高频脚本并发跑, 5-60s 填 cache)
    try:
        from api.dm_kun import _dm_kun_prewarm
        _dm_kun_prewarm()
    except Exception as _e:
        print(f"[dm_kun] prewarm 启动失败 (非致命): {_e}")

    print(f"[server] 仪表盘已启动 → http://0.0.0.0:{_port}")

    # 派生独立 watchdog 守护进程：检测 server 假死（进程活着但端口不监听）自动 kickstart。
    # detached (start_new_session)，不依赖 launchd bootstrap / cron（当前环境两者均不可用）。
    # pidfile 互斥在脚本内处理，server 重启不会累积多个 watchdog。
    try:
        import subprocess as _sp
        _wd_script = Path(__file__).resolve().parent / "scripts" / "market_radar_watchdog_daemon.sh"
        if _wd_script.exists():
            _wd_log = open("/tmp/mra-watchdog.log", "a")
            _sp.Popen(
                ["/bin/bash", str(_wd_script)],
                start_new_session=True,
                stdout=_wd_log,
                stderr=_sp.STDOUT,
                close_fds=True,
            )
            print("[server] watchdog 守护进程已派生")
    except Exception as _e:
        print(f"[server] watchdog 派生失败 (非致命): {_e}")

    # 显眼打印企微推送模式, 启动时一眼看到
    try:
        from fetcher.cycle_notifier import is_dry_run, is_configured
        dry = is_dry_run()
        configured = is_configured()
        if configured:
            mode_label = "🟡 SANDBOX 沙盒 (推送只 print, 不真发)" if dry else "🟢 真发 (推送到企微群)"
            print(f"[wecom] {mode_label}  WECOM_DRY_RUN={os.environ.get('WECOM_DRY_RUN', '(未设,默认 0 真发)')}")
        else:
            print(f"[wecom] ⚠ 未配置 webhook key, 推送全部跳过 (设 WECOM_ROBOT_KEY 或在 config.py 写死)")
    except Exception as e:
        print(f"[wecom] 状态检查失败: {e}")


    def _shutdown(signum, frame):
        print("\n[server] 收到退出信号，正在终止子进程...")
        # 强制杀掉所有由本进程 fork 出的子进程（ProcessPoolExecutor workers）
        current = multiprocessing.current_process()
        for child in multiprocessing.active_children():
            child.terminate()
        raise SystemExit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # 主线程保持阻塞，让 daemon 子线程（flask/scheduler）持续运行
    # 注意：signal.pause() 会被任意信号唤醒（子进程退出时收到 SIGCHLD），
    # 必须循环调用，否则主线程会“走完”导致 daemon 线程被回收、进程退出
    try:
        while True:
            signal.pause()          # Linux/macOS
    except AttributeError:
        import time             # Windows 没有 signal.pause
        while True:
            time.sleep(3600)

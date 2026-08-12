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

from core.python_runtime import get_python_executable

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

# 强制 line-buffering，确保 PIPELINE 日志在重定向时也能实时刷出
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

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
    get_cls_news_by_source,
    count_cls_news_by_source,
    get_policy_news_by_source,
    get_policy_news,
    count_policy_news_by_source,
    get_sector_flow_latest,
    get_lhb_data, get_lhb_seat,
    get_zt_pool,
    get_dt_pool,
    get_dt_pool_v2,
    get_dt_pool_v3,
    get_zbgc_pool,
    get_strong_pool,
    get_research_reports,
    get_market_breadth_latest,
    get_market_emotion_summary,
    get_lianzban_stats,
    get_sector_zt_density,
    get_concept_zt_density,
    get_lianzban_chain,
    get_hot_rank_up_latest,
    get_northbound_flow_latest,
    get_xq_hot_latest,
    get_concept_flow_latest,
    get_agent_summary_latest,
    get_agent_summary_history,
    get_agent_summary_by_id,
    get_sector_flow_accel,
    get_volume_breakout,
    get_turnover_stats,
    get_market_cap_dist,
    get_advance_decline,
    get_big_deal_latest,
    get_margin_latest,
    get_block_trade_latest,
    get_holder_count_latest,
    get_lockup_expiry,
    get_dividend_latest,
    get_industry_ranking_latest,
    get_ths_hot_stocks_latest,
    get_latest_emotion_date,
    get_research_activity,
    get_review_daily,
    get_review_dates,
    insert_review_daily,
    get_watchlist,
    add_watchlist,
    remove_watchlist,
    update_watchlist_note,
    list_pools,
    create_pool,
    rename_pool,
    delete_pool,
    pool_exists,
    DEFAULT_POOL,
)
from fetcher.qmt_monitors import (
    build_qmt_industry_draggers_payload,
    build_qmt_industry_stats_payload,
    build_qmt_industry_stats_source_rows,
    build_qmt_limit_down_monitor_payload,
)
from fetcher.sector_heat import fetch_dt_pool_v3
from fetcher.cycle_signal import (
    build_cycle_summary as _build_cycle_summary,
    build_indicator_meta as _build_indicator_meta,
    build_indicator_series as _build_indicator_series,
    build_equity_curve as _build_cycle_equity_curve,
    build_unified_score_series as _build_unified_score_series,
    data_status as _cycle_data_status,
    get_pic_path as _cycle_get_pic_path,
    trigger_cycle_refresh as _trigger_cycle_refresh,
    cycle_refresh_status as _cycle_refresh_status,
)

DIST = os.path.join(os.path.dirname(__file__), "dashboard", "dist")

app = Flask(__name__, static_folder=None, static_url_path="/_static_disabled_")
# 关键: 关掉 Flask 默认的 static catch-all 路由. 之前用 static_folder=DIST +
# static_url_path="" 时, Flask 自动注册了 "/<path:filename>" 路由, 比我们的
# @app.route("/<path:path>") serve_spa 先注册, 所有路径 (如 /review /strategist) 都被它
# 截走到 dist 里找文件, 找不到返 404, 彻底破坏 React Router SPA fallback.
# 修法: static_folder=None (不再 serve 静态), static_url_path 设成无意义前缀.
# 我们自己的 serve_spa 会显式 send_from_directory(DIST, ...) 并加 cache 头.
CORS(app)


# ---------------------------------------------------------------------------
# DM-kun 市场分析工具集 (quant.dm_kun) - 内存 cache + 异步预热
# ---------------------------------------------------------------------------
# 11 个核心分析脚本里的 3 个高频 (市场状态/情绪周期/行业拥挤度),
# 跑一次 5-60s, 不能放 HTTP 同步路径. 用内存 cache + 启动后台预热 +
# POST /recompute 手动重算. cache 结构: {"markdown": str, "computed_at": iso, "loading": bool}.
import io as _io
from contextlib import redirect_stdout as _redirect_stdout

_DM_KUN_CACHE: dict[str, dict] = {
    "market_regime":     {"markdown": None, "computed_at": None, "loading": False, "error": None},
    "sentiment_cycle":   {"markdown": None, "computed_at": None, "loading": False, "error": None},
    "industry_crowding": {"markdown": None, "computed_at": None, "loading": False, "error": None},
    "industry_enhanced": {"markdown": None, "computed_at": None, "loading": False, "error": None},
    "theme_ladder":      {"markdown": None, "computed_at": None, "loading": False, "error": None},
    "stock_recommender": {"markdown": None, "computed_at": None, "loading": False, "error": None},
}
_DM_KUN_LOCK = threading.Lock()


def _dm_kun_run_one(name: str, script_module: str, extra_args: list[str] | None = None) -> None:
    """跑一个 quant.dm_kun 脚本 (独立子进程), 抓 stdout 当 markdown 存 cache.

    关键: 用 subprocess.run 起独立 Python 进程, capture_output=True 拿隔离的 stdout.
    不能直接调 main() 函数, 因为 main() 内部 ProcessPoolExecutor fork 的子进程
    stdout 直连父进程 fd, redirect_stdout 抓不到, 会跟并发跑的兄弟脚本串行.
    独立子进程 → stdout 完全隔离 → 干净 cache.

    extra_args: 传给脚本的额外 CLI args (e.g. ["--summary-only", "电子", "有色金属"]).
    """
    import subprocess as _sp
    with _DM_KUN_LOCK:
        _DM_KUN_CACHE[name]["loading"] = True
        _DM_KUN_CACHE[name]["error"] = None
    try:
        repo_root = os.path.dirname(os.path.abspath(__file__))
        cmd = [sys.executable, "-m", f"quant.dm_kun.{script_module}"]
        if extra_args:
            cmd.extend(extra_args)
        result = _sp.run(
            cmd, capture_output=True, text=True, timeout=180,
            cwd=repo_root, env=os.environ.copy(),
        )
        # 优先 stdout, 如果有 stderr 警告也保留 (前面)
        text = result.stdout or ""
        if result.returncode != 0 and not text:
            text = (result.stderr or "")[:4000]
        with _DM_KUN_LOCK:
            _DM_KUN_CACHE[name]["markdown"] = text
            _DM_KUN_CACHE[name]["computed_at"] = datetime.now().isoformat(timespec="seconds")
            if result.returncode != 0:
                _DM_KUN_CACHE[name]["error"] = f"exit {result.returncode}"
    except _sp.TimeoutExpired:
        with _DM_KUN_LOCK:
            _DM_KUN_CACHE[name]["error"] = "timeout (180s)"
        print(f"[dm_kun] {name} run timeout")
    except Exception as e:
        with _DM_KUN_LOCK:
            _DM_KUN_CACHE[name]["error"] = f"{type(e).__name__}: {e}"
        print(f"[dm_kun] {name} run failed: {e}")
    finally:
        with _DM_KUN_LOCK:
            _DM_KUN_CACHE[name]["loading"] = False


# Script module 名 → cache key (前端 url 里的 name 直接用 cache key)
_DM_KUN_SCRIPT = {
    "market_regime":      "market_regime_analyzer",
    "sentiment_cycle":    "sentiment_cycle_analyzer",
    "industry_crowding":  "industry_crowding_analyzer",
    "industry_enhanced":  "industry_enhanced_analyzer",
    "theme_ladder":       "theme_ladder_analyzer",
    "stock_recommender":  "stock_recommender",
}


# 某些脚本需要额外 CLI args 才能 print md 到 stdout (默认行为是写文件):
#   - industry_enhanced: 需 --summary-only 才会 print md (默认写文件到 kun/data/)
#   - theme_ladder: 默认就 print md (再额外写文件), 无需 flag
#   - stock_recommender: 必传行业名, 自动从 industry_crowding cache 抽 top 5 拥挤行业
_DM_KUN_EXTRA_ARGS: dict[str, list[str]] = {
    "industry_enhanced": ["--summary-only"],
    "theme_ladder":      [],
    "stock_recommender": [],  # 动态从 cache 拿 top 5 行业名
}


def _dm_kun_default_industries() -> list[str]:
    """stock_recommender 默认行业: 从 industry_crowding cache 抽拥挤区 (3年分位≥80%) 行业
    (最多 5 个). 没 cache 就用 5 个常见行业兜底."""
    with _DM_KUN_LOCK:
        md = _DM_KUN_CACHE["industry_crowding"].get("markdown") or ""
    # 抠 "🔴 拥挤区（3年分位≥80%...）**：" 后面那一行
    # 行业名格式: 通信(1y:98% / 3y:100% / 5y:100%) 或 建筑材料(1y:100% / 3y:99% / 5y:96%)
    # 注意 markdown `）**：` 之间有 markdown 加粗标记 `**`, regex 用 \*+ 容忍
    import re as _re
    m = _re.search(r"🔴\s*拥挤区（3年分位≥80%[^）]*）\s*\*+\s*[：:]\s*([^\n]+)", md)
    if m:
        # 抠出 "XXX(1y:NN% / ...)" 里的 XXX (注意里面有空格和冒号, 跟旧格式不一样)
        names = _re.findall(r"([^、，,\s()]+)\(1y:", m.group(1))
        if names:
            return names[:5]
    return ["电子", "电力设备", "有色金属", "医药生物", "通信"]


def _dm_kun_prewarm() -> None:
    """server 启动后, 后台线程跑 6 个分析填 cache. 失败不阻塞.

    串行跑 (不并发): subprocess 各自独立 stdout, 内容已隔离. 串行只是为了避免
    3 个脚本同时跑时占满内存 (sentiment 5879 股票 + industry 5477 同时跑会 ~6GB).
    累计耗时约 110s (industry 5s + sentiment 60s + regime 10s + enhanced 30s + ladder 10s + recommender 5s).
    """
    def _spawn(name, script_module):
        try:
            # stock_recommender 必传行业名, 从 industry_crowding cache 拿 top 5
            extra = list(_DM_KUN_EXTRA_ARGS.get(name, []))
            if name == "stock_recommender":
                extra.extend(_dm_kun_default_industries())
            print(f"[dm_kun] prewarm {name} ...", flush=True)
            _dm_kun_run_one(name, script_module, extra)
            with _DM_KUN_LOCK:
                cached = _DM_KUN_CACHE[name]
            if cached.get("error"):
                print(f"[dm_kun] prewarm {name} FAILED: {cached['error']}")
            else:
                size = len(cached.get("markdown") or "")
                print(f"[dm_kun] prewarm {name} OK ({size} chars, {cached['computed_at']})")
        except Exception as e:
            print(f"[dm_kun] prewarm {name} crash: {e}")

    def _runner():
        for name, mod in _DM_KUN_SCRIPT.items():
            _spawn(name, mod)
    t = threading.Thread(target=_runner, daemon=True, name="dm_kun_prewarm")
    t.start()


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


def _strip_html(html: str) -> str:
    """把 HTML 报告剥成纯文本 (保留段落/标题结构, 适合发到企微)."""
    import re
    if not html:
        return ""
    # 去 <style> / <script>
    s = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    s = re.sub(r"<script[^>]*>.*?</script>", "", s, flags=re.DOTALL | re.IGNORECASE)
    # 行级块加换行
    s = re.sub(r"</?(p|div|br|h[1-6]|li|tr|td|th|blockquote|pre|article|section|header|footer)[^>]*>", "\n", s, flags=re.IGNORECASE)
    # 去剩余标签
    s = re.sub(r"<[^>]+>", "", s)
    # HTML 实体
    s = (s.replace("&nbsp;", " ")
           .replace("&amp;", "&")
           .replace("&lt;", "<")
           .replace("&gt;", ">")
           .replace("&quot;", '"')
           .replace("&#39;", "'"))
    # 多余空行
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n[ \t]+", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _date_param(key: str = "date") -> str:
    """Return the query-string date param, falling back to today."""
    val = request.args.get(key, "").strip()
    return val if val else _today()


def _date_or_none(key: str = "date") -> str | None:
    """Return date param if provided, else None (let storage pick latest)."""
    val = request.args.get(key, "").strip()
    return val if val else None


def _computed_date(key: str = "date") -> str:
    """用于历史计算型接口：有参数用参数，无参数回落到最新已计算日期。"""
    val = request.args.get(key, "").strip()
    return val if val else (get_latest_emotion_date() or _today())


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


def _refresh_qmt_industry_stats_cache(target_trade_date: str) -> None:
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


def _save_env_local(updates: dict) -> None:
    """将 key=value 写入 .env.local，已有的 key 更新，不存在的追加。"""
    env_path = os.path.join(os.path.dirname(__file__), ".env.local")
    lines = []
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

    written = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue
        key = stripped.partition("=")[0].strip()
        if key in updates:
            new_lines.append(f"{key}={updates[key]}\n")
            written.add(key)
        else:
            new_lines.append(line)

    # 追加未出现过的 key
    for key, val in updates.items():
        if key not in written:
            new_lines.append(f"{key}={val}\n")

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)


# ---------------------------------------------------------------------------
# News & Policy
# ---------------------------------------------------------------------------

@app.route("/api/news")
def api_news():
    try:
        source    = request.args.get("source", "财联社")
        page_size = int(request.args.get("page_size", 30))
        page      = int(request.args.get("page", 1))
        offset    = (page - 1) * page_size
        rows  = get_cls_news_by_source(source, page_size, offset)
        total = count_cls_news_by_source(source)
        return _ok({"items": rows, "total": total, "page": page, "page_size": page_size})
    except Exception as exc:
        return _err(exc)


@app.route("/api/policy")
def api_policy():
    try:
        source    = request.args.get("source", "全部")
        page_size = int(request.args.get("page_size", 30))
        page      = int(request.args.get("page", 1))
        offset    = (page - 1) * page_size
        if source == "全部":
            rows  = get_policy_news(page_size, offset)
            total = sum(count_policy_news_by_source(s) for s in
                        ['巨潮公告','财新','发改委','证监会','上交所问询','深交所问询','深交所公告'])
        else:
            rows  = get_policy_news_by_source(source, page_size, offset)
            total = count_policy_news_by_source(source)
        return _ok({"items": rows, "total": total, "page": page, "page_size": page_size})
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Research reports
# ---------------------------------------------------------------------------

@app.route("/api/research")
def api_research():
    try:
        qtype = int(request.args.get("qtype", 0))
        limit = int(request.args.get("limit", 20))
        today_only_raw = request.args.get("today_only", "false").lower()
        today_only = today_only_raw in ("1", "true", "yes")
        rows = get_research_reports(qtype, limit, today_only)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Sector / Concept flow
# ---------------------------------------------------------------------------

@app.route("/api/sector-flow")
def api_sector_flow():
    try:
        source_type = request.args.get("type", "industry")
        rows = get_sector_flow_latest(source_type)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@app.route("/api/concept-flow")
def api_concept_flow():
    try:
        top_n = int(request.args.get("top_n", 30))
        rows = get_concept_flow_latest(top_n)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Dragon-Tiger List
# ---------------------------------------------------------------------------

@app.route("/api/lhb")
def api_lhb():
    try:
        trade_date = _date_or_none()
        rows = get_lhb_data(trade_date)
        # 附加席位明细
        seats = get_lhb_seat(trade_date)
        seat_map: dict = {}
        for s in seats:
            seat_map.setdefault(s["stock_code"], []).append(s)
        for row in rows:
            code = row.get("stock_code", "")
            row_seats = seat_map.get(code, [])
            row["seats"] = row_seats
            types = {s.get("seat_type") for s in row_seats}
            if "游资" in types and "机构" in types:
                row["seat_nature"] = "游资+机构"
            elif "机构" in types:
                row["seat_nature"] = "机构主导"
            elif "游资" in types:
                row["seat_nature"] = "游资主导"
            elif row_seats:
                row["seat_nature"] = "其他"
            else:
                row["seat_nature"] = None
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@app.route("/api/lhb-local")
def api_lhb_local():
    """龙虎榜本地版：只读 lhb_seat 表（由本地 CSV 写入），无数据返回空列表。"""
    try:
        trade_date = _date_or_none()
        seats = get_lhb_seat(trade_date)
        # 按 stock_code 聚合席位
        stock_map: dict = {}
        for s in seats:
            code = s["stock_code"]
            if code not in stock_map:
                stock_map[code] = {
                    "stock_code": code,
                    "seats": [],
                    "net_buy": 0.0,
                }
            stock_map[code]["seats"].append(s)
            stock_map[code]["net_buy"] += s.get("net_amount") or 0.0
        # 附加席位性质
        result = []
        for row in stock_map.values():
            types = {s.get("seat_type") for s in row["seats"]}
            if "游资" in types and "机构" in types:
                row["seat_nature"] = "游资+机构"
            elif "机构" in types:
                row["seat_nature"] = "机构主导"
            elif "游资" in types:
                row["seat_nature"] = "游资主导"
            elif row["seats"]:
                row["seat_nature"] = "其他"
            else:
                row["seat_nature"] = None
            result.append(row)
        result.sort(key=lambda r: r["net_buy"], reverse=True)
        return _ok(result)
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Pools
# ---------------------------------------------------------------------------

@app.route("/api/zt-pool")
def api_zt_pool():
    try:
        trade_date = _date_param()
        rows = get_zt_pool(trade_date)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@app.route("/api/dt-pool")
def api_dt_pool():
    try:
        trade_date = _date_param()
        rows = get_dt_pool(trade_date)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@app.route("/api/dt-pool-v2")
def api_dt_pool_v2():
    try:
        trade_date = _date_param()
        rows = get_dt_pool_v2(trade_date)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@app.route("/api/dt-pool-v3")
def api_dt_pool_v3():
    try:
        trade_date = _date_param()
        rows = get_dt_pool_v3(trade_date)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


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


@app.route("/api/zbgc-pool")
def api_zbgc_pool():
    try:
        trade_date = _date_param()
        rows = get_zbgc_pool(trade_date)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@app.route("/api/strong-pool")
def api_strong_pool():
    try:
        trade_date = _date_param()
        rows = get_strong_pool(trade_date)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Market emotion & pulse
# ---------------------------------------------------------------------------

@app.route("/api/market-emotion")
def api_market_emotion():
    try:
        trade_date = _date_or_none()
        data = get_market_emotion_summary(trade_date)
        return _ok(data)
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Lianzban (consecutive limit-up) statistics
# ---------------------------------------------------------------------------

@app.route("/api/lianzban-stats")
def api_lianzban_stats():
    try:
        days = int(request.args.get("days", 30))
        data = get_lianzban_stats(days)
        return _ok(data)
    except Exception as exc:
        return _err(exc)


@app.route("/api/lianzban-chain")
def api_lianzban_chain():
    try:
        trade_date = _computed_date()
        rows = get_lianzban_chain(trade_date)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# ZT density
# ---------------------------------------------------------------------------

@app.route("/api/sector-zt-density")
def api_sector_zt_density():
    try:
        trade_date = _computed_date()
        rows = get_sector_zt_density(trade_date)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@app.route("/api/concept-zt-density")
def api_concept_zt_density():
    try:
        trade_date = _computed_date()
        top_n = int(request.args.get("top_n", 15))
        rows = get_concept_zt_density(trade_date, top_n)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Hot rank & Northbound
# ---------------------------------------------------------------------------

@app.route("/api/hot-rank-up")
def api_hot_rank_up():
    try:
        top_n = int(request.args.get("top_n", 20))
        rows = get_hot_rank_up_latest(top_n)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@app.route("/api/northbound-flow")
def api_northbound_flow():
    try:
        data = get_northbound_flow_latest()
        return _ok(data)
    except Exception as exc:
        return _err(exc)


@app.route("/api/xq-hot")
def api_xq_hot():
    try:
        top_n = int(request.args.get("top_n", 30))
        rows = get_xq_hot_latest(top_n)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# AI summary
# ---------------------------------------------------------------------------

@app.route("/api/ai-summary")
def api_ai_summary():
    try:
        data = get_agent_summary_latest()
        return _ok(data)
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Agent analysis endpoints
# ---------------------------------------------------------------------------

@app.route("/api/agent/latest")
def api_agent_latest():
    """返回最新报告元数据（含 has_html、id，供前端决定用 iframe 还是 JSON 渲染）。"""
    try:
        import json
        row = get_agent_summary_latest()
        if not row:
            return _ok(None)
        has_html = bool(row.get("report_html"))
        snapshot = row.get("data_snapshot_json")
        if snapshot:
            try:
                data = json.loads(snapshot)
            except Exception:
                data = {"content": row.get("content"), "summary_time": row.get("summary_time")}
        else:
            data = {"content": row.get("content"), "summary_time": row.get("summary_time")}
        data["summary_time"] = row.get("summary_time")
        data["run_type"] = row.get("run_type", "")
        data["id"] = row.get("id")
        data["has_html"] = has_html
        return _ok(data)
    except Exception as exc:
        return _err(exc)


@app.route("/api/agent/report/<int:row_id>")
def api_agent_report_html(row_id: int):
    """直接返回 HTML 报告，供 <iframe src="..."> 使用。"""
    try:
        from flask import Response
        row = get_agent_summary_by_id(row_id)
        if not row:
            return Response("<h1>404 Not Found</h1>", status=404, mimetype="text/html")
        html = row.get("report_html") or ""
        if not html:
            return Response("<p>此记录无 HTML 报告</p>", status=404, mimetype="text/html")
        return Response(html, status=200, mimetype="text/html; charset=utf-8")
    except Exception as exc:
        return Response(f"<p>错误：{exc}</p>", status=500, mimetype="text/html")


@app.route("/api/agent/history")
def api_agent_history():
    """返回最近 N 条报告摘要（run_type、run_time、summary_text、has_html）。"""
    try:
        import json
        limit = int(request.args.get("limit", 20))
        today_only = request.args.get("today", "false").lower() == "true"
        rows = get_agent_summary_history(limit=limit, today_only=today_only)
        results = []
        for row in rows:
            snap = row.get("data_snapshot_json") if "data_snapshot_json" in row else None
            summary_text = None
            run_time = None
            if snap:
                try:
                    d = json.loads(snap)
                    summary_text = d.get("summary_text") or (d.get("changes", [""])[0] if d.get("changes") else None)
                    run_time = d.get("run_time")
                except Exception:
                    pass
            results.append({
                "id": row.get("id"),
                "summary_time": row.get("summary_time"),
                "run_type": row.get("run_type", ""),
                "run_time": run_time or row.get("summary_time"),
                "summary_text": summary_text or row.get("content", ""),
                "has_html": bool(row.get("has_html")),
            })
        return _ok(results)
    except Exception as exc:
        return _err(exc)


@app.route("/api/agent/history/<int:row_id>")
def api_agent_history_detail(row_id: int):
    """返回单条报告的完整结构化数据（含 has_html 标记）。"""
    try:
        import json
        row = get_agent_summary_by_id(row_id)
        if not row:
            return _err("not found", 404)
        has_html = bool(row.get("report_html"))
        snap = row.get("data_snapshot_json")
        if snap:
            try:
                data = json.loads(snap)
            except Exception:
                data = {"content": row.get("content")}
        else:
            data = {"content": row.get("content")}
        data["summary_time"] = row.get("summary_time")
        data["run_type"] = row.get("run_type", "")
        data["id"] = row.get("id")
        data["has_html"] = has_html
        return _ok(data)
    except Exception as exc:
        return _err(exc)


@app.route("/api/agent/trigger", methods=["POST"])
def api_agent_trigger():
    """手动触发 Agent 分析（非阻塞）。body: {"run_type": "evening"}"""
    try:
        from agent.orchestrator import run_agent_analysis
        body = request.get_json(silent=True) or {}
        run_type = body.get("run_type") or _infer_run_type()
        _VALID_RUN_TYPES = {"morning", "auction", "intraday", "closing", "evening", "policy", "research", "notice", "watchlist"}
        if run_type not in _VALID_RUN_TYPES:
            return _err(f"无效的 run_type: {run_type}", 400)
        # watchlist 可选 pool 限定分析池
        pool = str(body.get("pool", "") or "").strip() or None
        if pool and run_type != "watchlist":
            pool = None
        if pool and not pool_exists(pool):
            return _err(f"股池「{pool}」不存在", 400)
        result = run_agent_analysis(run_type, pool=pool)
        return _ok(result)
    except Exception as exc:
        return _err(exc)


@app.route("/api/agent/status")
def api_agent_status():
    """返回当前 Agent 运行状态。"""
    try:
        from agent.orchestrator import get_agent_state
        return _ok(get_agent_state())
    except Exception as exc:
        return _err(exc)


@app.route("/api/agent/stop", methods=["POST"])
def api_agent_stop():
    """请求停止当前正在运行的 Agent 分析。"""
    try:
        from agent.orchestrator import stop_agent_analysis
        stop_agent_analysis()
        return _ok({"stopped": True})
    except Exception as exc:
        return _err(exc)


@app.route("/api/info_brief/run", methods=["POST"])
def api_info_brief_run():
    """手动触发信息情报简报 (info_brief_v2: 分类整理 + AI 综合分析)。"""
    try:
        from agent.info_brief_v2 import run as run_info_brief
        body = request.get_json(silent=True) or {}
        run_type = body.get("run_type") or _infer_run_type()
        if run_type not in ("morning", "intraday", "evening"):
            run_type = "evening"
        result = run_info_brief(run_type=run_type)
        return _ok(result)
    except Exception as exc:
        return _err(exc)


@app.route("/api/info_brief/latest")
def api_info_brief_latest():
    """最近一次信息情报简报。"""
    try:
        rows = get_agent_summary_history(limit=20, run_type="info_brief")
        if not rows:
            return _ok({"row": None})
        return _ok({"row": rows[0]})
    except Exception as exc:
        return _err(exc)


@app.route("/api/info_brief/history")
def api_info_brief_history():
    """信息情报简报历史列表。"""
    try:
        limit = int(request.args.get("limit", 20))
        rows = get_agent_summary_history(limit=limit, run_type="info_brief")
        return _ok({"rows": rows})
    except Exception as exc:
        return _err(exc)


@app.route("/api/strategist/run", methods=["POST"])
def api_strategist_run():
    """手动触发战略推理 (基于最近一次 info_brief)。"""
    try:
        from agent.strategist import run as run_strategist
        body = request.get_json(silent=True) or {}
        run_type = body.get("run_type") or _infer_run_type()
        if run_type not in ("morning", "intraday", "evening"):
            run_type = "evening"
        result = run_strategist(run_type=run_type)
        return _ok(result)
    except Exception as exc:
        return _err(exc)


@app.route("/api/strategist/latest")
def api_strategist_latest():
    """最近一次战略推理。"""
    try:
        rows = get_agent_summary_history(limit=1, run_type="strategist")
        if not rows:
            return _ok({"row": None})
        return _ok({"row": rows[0]})
    except Exception as exc:
        return _err(exc)


@app.route("/api/strategist/history")
def api_strategist_history():
    """战略推理历史列表。"""
    try:
        limit = int(request.args.get("limit", 20))
        rows = get_agent_summary_history(limit=limit, run_type="strategist")
        return _ok({"rows": rows})
    except Exception as exc:
        return _err(exc)


def _infer_run_type() -> str:
    """根据当前时间推断 run_type。"""
    now = datetime.now()
    h, m = now.hour, now.minute
    total = h * 60 + m
    if total < 7 * 60:
        return "morning"
    if total < 9 * 60 + 30:
        return "auction"
    if total < 15 * 60:
        return "intraday"
    if total < 17 * 60:
        return "closing"
    return "evening"


# ---------------------------------------------------------------------------
# Watchlist（关注股池）
# ---------------------------------------------------------------------------

def _normalize_stock_code(raw: str) -> str:
    """归一化为 6 位数字代码；非法输入返回空串。"""
    from agent.stock_search import _normalize_code
    return _normalize_code(raw)


def _fill_stock_name(code: str) -> str:
    """名称自动补全：先查全量名称索引，回落本地量价 CSV 查名。"""
    try:
        from agent.stock_search import lookup_name
        return lookup_name(code)
    except Exception:
        return ""


def _resolve_pool(raw: str | None) -> str:
    """池名规整：去首尾空格，空则'默认'。"""
    name = (raw or "").strip()
    return name if name else DEFAULT_POOL


@app.route("/api/watchlist", methods=["GET"])
def api_watchlist_list():
    """返回关注股池列表。?pool=xxx 过滤单个池，不带返回全部（每项带 pool 字段）。"""
    try:
        pool = (request.args.get("pool", "") or "").strip() or None
        return _ok(get_watchlist(pool))
    except Exception as exc:
        return _err(exc)


# ── 股池分组管理 ──────────────────────────────────────────────────────────────

@app.route("/api/pools", methods=["GET"])
def api_pools_list():
    """池列表（含每个池股票数量）。"""
    try:
        return _ok(list_pools())
    except Exception as exc:
        return _err(exc)


@app.route("/api/pools", methods=["POST"])
def api_pools_create():
    """新建股池。body: {"name": "策略A"}（≤20 字符，重名/保留名 400）。"""
    try:
        body = request.get_json(silent=True) or {}
        name = str(body.get("name", "") or "").strip()
        if not name or len(name) > 20:
            return _err("池名需为 1-20 个字符", 400)
        if name in ("全部",):
            return _err(f"「{name}」为保留名称", 400)
        if pool_exists(name):
            return _err(f"股池「{name}」已存在", 400)
        create_pool(name)
        return _ok({"name": name})
    except Exception as exc:
        return _err(exc)


@app.route("/api/pools/<name>", methods=["PUT"])
def api_pools_rename(name: str):
    """池改名，池内股票跟随。body: {"new_name": "策略B"}。'默认'池拒绝。"""
    try:
        old = (name or "").strip()
        if old == DEFAULT_POOL:
            return _err("「默认」池不允许改名", 400)
        body = request.get_json(silent=True) or {}
        new_name = str(body.get("new_name", "") or "").strip()
        if not new_name or len(new_name) > 20:
            return _err("池名需为 1-20 个字符", 400)
        if new_name in ("全部", DEFAULT_POOL):
            return _err(f"「{new_name}」为保留名称", 400)
        if not pool_exists(old):
            return _err(f"股池「{old}」不存在", 404)
        if pool_exists(new_name):
            return _err(f"股池「{new_name}」已存在", 400)
        if not rename_pool(old, new_name):
            return _err("改名失败", 400)
        return _ok({"old": old, "new": new_name})
    except Exception as exc:
        return _err(exc)


@app.route("/api/pools/<name>", methods=["DELETE"])
def api_pools_delete(name: str):
    """删除股池并连带删除池内股票。'默认'池拒绝。"""
    try:
        pool = (name or "").strip()
        if pool == DEFAULT_POOL:
            return _err("「默认」池不允许删除", 400)
        if not pool_exists(pool):
            return _err(f"股池「{pool}」不存在", 404)
        removed = delete_pool(pool)
        if removed is None:
            return _err("删除失败", 400)
        return _ok({"deleted_pool": pool, "removed_stocks": removed})
    except Exception as exc:
        return _err(exc)


@app.route("/api/watchlist/search")
def api_watchlist_search():
    """模糊搜索股票（代码 / 拼音简写 / 中文名片段），供前端输入联想。"""
    try:
        from agent.stock_search import search_stocks
        q = request.args.get("q", "").strip()
        if not q:
            return _ok([])
        return _ok(search_stocks(q, limit=10))
    except Exception as exc:
        return _err(exc)


@app.route("/api/watchlist", methods=["POST"])
def api_watchlist_add():
    """添加关注股票。body: {"code": "600519", "note": "可选", "pool": "缺省默认"} 或 {"q": "gzmt|茅台|600519.SH"}。
    q 唯一命中直接添加；多命中返回 {"multiple": true, "candidates": [...]} 由前端选择。
    指定 pool 时池必须已存在（防止手误建新池），否则 400。
    """
    try:
        from agent.stock_search import search_stocks
        body = request.get_json(silent=True) or {}
        note = str(body.get("note", "") or "")[:200]
        q = str(body.get("q", "") or "").strip()
        pool = _resolve_pool(body.get("pool"))
        if not pool_exists(pool):
            return _err(f"股池「{pool}」不存在，请先创建该池", 400)

        if q:
            hits = search_stocks(q, limit=10, prefix_abbr=False)
            if not hits:
                return _err(f"未找到与「{q}」匹配的股票（支持代码 / 拼音简写 / 中文名片段）", 400)
            if len(hits) > 1:
                return _ok({"multiple": True, "candidates": hits})
            code = hits[0]["code"]
            name = hits[0].get("name") or _fill_stock_name(code)
        else:
            code = _normalize_stock_code(str(body.get("code", "")))
            if not code:
                return _err("无效的股票代码（需为 6 位数字，或使用 q 参数模糊搜索）", 400)
            name = _fill_stock_name(code)

        item = add_watchlist(code, name=name, note=note, pool=pool)
        return _ok(item)
    except Exception as exc:
        return _err(exc)


@app.route("/api/watchlist/import", methods=["POST"])
def api_watchlist_import():
    """批量导入股池。支持 multipart 文件上传（字段 file，可加字段 pool）或 JSON {"text": "...", "pool": "..."}。
    宽容解析：自动识别表头、多种代码写法、注释行；文件内与目标池现有股票去重。
    指定 pool 时池必须已存在，否则 400。
    响应: {"added": n, "skipped_existing": n, "failed": [...], "total": n, "pool": "..."}
    """
    try:
        from agent.stock_search import parse_import_text

        text = ""
        pool_raw: str | None = None
        if request.files:
            f = request.files.get("file")
            if f is None:
                return _err("multipart 请求中未找到 file 字段", 400)
            pool_raw = request.form.get("pool")
            raw = f.read()
            for enc in ("utf-8-sig", "gbk", "utf-8"):
                try:
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            if not text:
                return _err("文件编码无法识别（支持 UTF-8 / GBK）", 400)
        else:
            body = request.get_json(silent=True) or {}
            text = str(body.get("text", "") or "")
            pool_raw = body.get("pool")

        if not text.strip():
            return _err("导入内容为空", 400)

        pool = _resolve_pool(pool_raw)
        if not pool_exists(pool):
            return _err(f"股池「{pool}」不存在，请先创建该池", 400)

        rows, failed = parse_import_text(text)
        existing = {item["code"] for item in get_watchlist(pool)}

        added = 0
        skipped_existing = 0
        for row in rows:
            if row["code"] in existing:
                skipped_existing += 1
                continue
            name = _fill_stock_name(row["code"])
            add_watchlist(row["code"], name=name, note=row.get("note", ""), pool=pool)
            existing.add(row["code"])
            added += 1

        return _ok({
            "added": added,
            "skipped_existing": skipped_existing,
            "failed": failed,
            "total": len(rows) + len(failed),
            "pool": pool,
        })
    except Exception as exc:
        return _err(exc)


@app.route("/api/watchlist/<code>", methods=["DELETE"])
def api_watchlist_remove(code: str):
    """从股池删除一只股票。?pool=xxx 指定池（缺省'默认'）。"""
    try:
        norm = _normalize_stock_code(code)
        if not norm:
            return _err("无效的股票代码", 400)
        pool = _resolve_pool(request.args.get("pool"))
        removed = remove_watchlist(norm, pool)
        if not removed:
            return _err(f"该股不在股池「{pool}」中", 404)
        return _ok({"removed": norm, "pool": pool})
    except Exception as exc:
        return _err(exc)


@app.route("/api/watchlist/<code>", methods=["PUT"])
def api_watchlist_update(code: str):
    """更新股池股票备注。body: {"note": "...", "pool": "缺省默认"}"""
    try:
        norm = _normalize_stock_code(code)
        if not norm:
            return _err("无效的股票代码", 400)
        body = request.get_json(silent=True) or {}
        note = str(body.get("note", "") or "")[:200]
        pool = _resolve_pool(body.get("pool"))
        updated = update_watchlist_note(norm, note, pool)
        if not updated:
            return _err(f"该股不在股池「{pool}」中", 404)
        return _ok({"code": norm, "note": note, "pool": pool})
    except Exception as exc:
        return _err(exc)



# ---------------------------------------------------------------------------
# Sector flow acceleration / Volume breakout / Turnover stats / Market cap dist / Advance-decline
# ---------------------------------------------------------------------------

@app.route("/api/sector-flow-accel")
def api_sector_flow_accel():
    try:
        trade_date = _computed_date()
        rows = get_sector_flow_accel(trade_date)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)

@app.route("/api/volume-breakout")
def api_volume_breakout():
    try:
        trade_date = _computed_date()
        rows = get_volume_breakout(trade_date)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)

@app.route("/api/research-activity")
def api_research_activity():
    try:
        trade_date = _computed_date()
        rows = get_research_activity(trade_date)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@app.route("/api/sector-chip-pressure")
def api_sector_chip_pressure():
    try:
        from db.storage import get_sector_chip_pressure
        trade_date = _computed_date()
        rows = get_sector_chip_pressure(trade_date)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@app.route("/api/sector-auction-sentiment")
def api_sector_auction_sentiment():
    try:
        from db.storage import get_sector_auction_sentiment
        trade_date = _computed_date()
        rows = get_sector_auction_sentiment(trade_date)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)

@app.route("/api/turnover-stats")
def api_turnover_stats():
    try:
        trade_date = _computed_date()
        data = get_turnover_stats(trade_date)
        return _ok(data)
    except Exception as exc:
        return _err(exc)

@app.route("/api/market-cap-dist")
def api_market_cap_dist():
    try:
        trade_date = _computed_date()
        data = get_market_cap_dist(trade_date)
        return _ok(data)
    except Exception as exc:
        return _err(exc)

@app.route("/api/advance-decline")
def api_advance_decline():
    try:
        trade_date = _computed_date()
        data = get_advance_decline(trade_date)
        return _ok(data)
    except Exception as exc:
        return _err(exc)


@app.route("/api/trade-calendar/today")
def api_trade_calendar_today():
    try:
        from agent.query import get_market_session
        return _ok(get_market_session())
    except Exception as exc:
        return _err(exc)










@app.route("/api/agent/time-slot")
def api_agent_time_slot():
    """
    返回当前应显示的 AI 分析时段按钮高亮状态，以及下次切换时间。

    逻辑：
    - 交易日 00:00–09:15 → morning
    - 交易日 09:15–15:30 → intraday
    - 交易日 15:30–24:00 → evening
    - 非交易日（周末/节假日）→ evening，直到下一个交易日 00:00 切换为 morning
    """
    try:
        from datetime import datetime, timedelta, time as dtime
        from agent.query import _get_trade_calendar

        now = datetime.now()
        today = now.date()
        total_min = now.hour * 60 + now.minute

        try:
            cal = _get_trade_calendar()
            trade_dates = sorted(cal["trade_date"].values)
            is_trade_today = today in trade_dates
        except Exception:
            is_trade_today = today.weekday() < 5
            trade_dates = []

        def _next_trade_date_after(d):
            """返回 d 之后第一个交易日（不含 d）。"""
            for td in trade_dates:
                if td > d:
                    return td
            # fallback：跳过周末往后找
            nxt = d + timedelta(days=1)
            while nxt.weekday() >= 5:
                nxt += timedelta(days=1)
            return nxt

        if is_trade_today:
            if total_min < 9 * 60 + 15:
                slot = "morning"
                # 下次切换：今天 09:15
                next_change = datetime.combine(today, dtime(9, 15))
            elif total_min < 15 * 60 + 30:
                slot = "intraday"
                next_change = datetime.combine(today, dtime(15, 30))
            else:
                slot = "evening"
                nxt = _next_trade_date_after(today)
                next_change = datetime.combine(nxt, dtime(0, 0))
        else:
            slot = "evening"
            nxt = _next_trade_date_after(today)
            next_change = datetime.combine(nxt, dtime(0, 0))

        return _ok({
            "slot": slot,
            "is_trade_today": is_trade_today,
            "next_change_at": next_change.isoformat(),
            "server_time": now.isoformat(),
        })
    except Exception as exc:
        return _err(exc)


@app.route("/api/data-health")
def api_data_health():
    """
    数据健康检查接口，供前端感知数据故障告警。
    返回 data_alerts（level=error/warning）、abort_reason、session 等完整信息。
    冷调用约1-2秒（AKShare 日历查询），建议前端低频轮询（60秒一次）。
    """
    try:
        import subprocess, json as _json
        result = subprocess.run(
            [get_python_executable(), "agent/query.py", "data_health"],
            cwd=os.path.dirname(__file__),
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = _json.loads(result.stdout.strip())
        else:
            return _err(f"data_health 查询失败: {result.stderr[:200]}")

        return _ok(data)
    except Exception as exc:
        return _err(exc)


_compute_state: dict = {"status": "idle", "progress": [], "trade_date": "", "results": []}
_compute_lock = __import__("threading").Lock()


def _run_compute():
    """在独立线程中执行所有计算任务，更新 _compute_state。"""
    import threading
    from quant.daily_compute import (
        compute_market_emotion, compute_lianzban_stats,
        compute_sector_zt_density, compute_sector_flow_acceleration,
        compute_volume_breakout, compute_chip_status,
        compute_lianzban_chain, compute_research_activity,
        compute_concept_zt_density, compute_call_auction_stats,
        compute_turnover_stats, compute_market_cap_dist,
        compute_advance_decline,
        compute_sector_chip_pressure, compute_sector_auction_sentiment,
    )
    from quant.loader import get_latest_trade_date

    tasks = [
        ("市场情绪指标",   compute_market_emotion),
        ("连板梯队统计",   compute_lianzban_stats),
        ("板块涨停密度",   compute_sector_zt_density),
        ("资金流加速度",   compute_sector_flow_acceleration),
        ("成交额异动",     compute_volume_breakout),
        ("筹码状态",       compute_chip_status),
        ("板块筹码压力",   compute_sector_chip_pressure),
        ("集合竞价委比",   compute_call_auction_stats),
        ("板块竞价情绪",   compute_sector_auction_sentiment),
        ("连板链条",       compute_lianzban_chain),
        ("机构调研热度",   compute_research_activity),
        ("概念涨停密度",   compute_concept_zt_density),
        ("换手率分层",     compute_turnover_stats),
        ("市值分布",       compute_market_cap_dist),
        ("市场宽度",       compute_advance_decline),
    ]
    import quant.loader as _loader
    # 前置检查：DATA_ROOT 未配置或路径不存在时，整批标为失败并附上明确原因
    if not _loader.DATA_ROOT:
        _fail = [{"name": n, "ok": False, "error": "未配置 QUANT_DATA_ROOT，请在设置中填写本地数据路径"} for n, _ in tasks]
        with _compute_lock:
            _compute_state.update({"status": "done", "progress": _fail, "trade_date": "", "results": _fail})
        return
    if not _loader.DATA_ROOT.exists():
        _fail = [{"name": n, "ok": False, "error": f"路径不存在: {_loader.DATA_ROOT}"} for n, _ in tasks]
        with _compute_lock:
            _compute_state.update({"status": "done", "progress": _fail, "trade_date": "", "results": _fail})
        return

    td = get_latest_trade_date()
    if not td:
        _fail = [{"name": n, "ok": False, "error": "无法读取交易日期，请确认 factors/stock/daily/涨停相关因子.parquet 存在且有数据"} for n, _ in tasks]
        with _compute_lock:
            _compute_state.update({"status": "done", "progress": _fail, "trade_date": "", "results": _fail})
        return

    with _compute_lock:
        _compute_state.update({"status": "running", "progress": [], "trade_date": td, "results": []})

    results = []
    for name, fn in tasks:
        try:
            fn(td)
            r = {"name": name, "ok": True}
        except Exception as e:
            r = {"name": name, "ok": False, "error": str(e)}
        results.append(r)
        with _compute_lock:
            _compute_state["progress"] = list(results)

    with _compute_lock:
        _compute_state.update({"status": "done", "results": results})


@app.route("/api/compute", methods=["POST"])
def api_compute():
    """启动后台计算任务，立即返回。"""
    import threading
    with _compute_lock:
        if _compute_state["status"] == "running":
            return _ok({"started": False, "message": "计算任务已在运行中"})
        _compute_state["status"] = "running"
    t = threading.Thread(target=_run_compute, daemon=True, name="compute-worker")
    t.start()
    return _ok({"started": True})


@app.route("/api/compute-status")
def api_compute_status():
    """轮询计算进度。"""
    with _compute_lock:
        state = dict(_compute_state)
    return _ok(state)


# ---------------------------------------------------------------------------
# Daily review analysis
# ---------------------------------------------------------------------------

import json as _json

@app.route("/api/review/latest")
def api_review_latest():
    """最近一次复盘 — 永远返回 cache, 不触发自动重算 (compute 30-120s 太慢会拖死前端)。

    想拿新数据: 调 /api/review/data?date=YYYY-MM-DD (单日重算, 走 INSERT OR REPLACE)。
    想批量刷新: 手动跑 `python -c "from quant.review_compute import compute_daily_analysis; compute_daily_analysis()"`。
    """
    try:
        row = get_review_daily(None)
        if row:
            return _ok(_json.loads(row["payload"]))
        # 完全没 cache, 同步算一次 (用户首次访问场景, 偶尔卡)
        from quant.review_compute import compute_daily_analysis
        data = compute_daily_analysis()
        if data:
            payload = _json.dumps(data, ensure_ascii=False, default=str)
            insert_review_daily(data["trade_date"], payload)
            return _ok(data)
        return _ok(None)
    except Exception as exc:
        return _err(exc)


@app.route("/api/review/dates")
def api_review_dates():
    try:
        return _ok(get_review_dates())
    except Exception as exc:
        return _err(exc)


@app.route("/api/review/data")
def api_review_data():
    try:
        import time as _time
        trade_date = request.args.get("date", "").strip()
        force = request.args.get("force", "").strip() in ("1", "true", "yes")
        if not trade_date:
            if not force:
                return _err("缺少 date 参数", 400)
            # force=1 无 date → 自动用本地 CSV 最新交易日 (解决"DB 还没新日期"鸡生蛋)
            from quant.review_compute import _latest_trade_date
            trade_date = _latest_trade_date()
            logger.info("[review] force=1 无 date, 自动用 CSV 最新: %s", trade_date)
        # 兼容前端两种格式: '20260811' (compact) 或 '2026-08-11' (ISO) — DB 存 ISO
        if len(trade_date) == 8 and trade_date.isdigit():
            trade_date = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
        row = get_review_daily(trade_date)
        if row and not force:
            return _ok(_json.loads(row["payload"]))
        # force=1 或 没找到 → 同步重算 (1-2 分钟, 期间 HTTP 不返, 前端按钮转圈等)
        from quant.review_compute import compute_daily_analysis
        logger.info("[review] 重算开始 trade_date=%s force=%s (cache_hit=%s)", trade_date, force, bool(row))
        t0 = _time.time()
        data = compute_daily_analysis(trade_date)
        elapsed = round(_time.time() - t0, 1)
        if data:
            payload = _json.dumps(data, ensure_ascii=False, default=str)
            insert_review_daily(data["trade_date"], payload)
            logger.info("[review] 重算完成 trade_date=%s 耗时=%ss", data["trade_date"], elapsed)
            return _ok({**data, "_recompute": True, "_elapsed_sec": elapsed})
        logger.warning("[review] 重算未返回数据 trade_date=%s 耗时=%ss", trade_date, elapsed)
        return _ok(None)
    except Exception as exc:
        return _err(exc)


@app.route("/api/review/v2/run", methods=["POST"])
def api_review_v2_run():
    """手动触发 9 维度复盘 (review_v2: 编排 14 daily_compute + sector 板块效应)。"""
    try:
        from agent.review_v2 import run as run_review_v2
        body = request.get_json(silent=True) or {}
        trade_date = (body.get("trade_date") or "").strip() or None
        result = run_review_v2(trade_date)
        return _ok(result)
    except Exception as exc:
        return _err(exc)


@app.route("/api/review/v2/latest")
def api_review_v2_latest():
    """最近一次 9 维度复盘 (review_v2, 跟旧 review_daily 独立)。"""
    try:
        from db.storage import get_review_v2_daily
        row = get_review_v2_daily(None)
        if not row:
            return _ok({"row": None})
        return _ok({"row": {"id": row.get("id"), "trade_date": row.get("trade_date"),
                             "payload": row.get("payload"), "created_at": row.get("created_at")}})
    except Exception as exc:
        return _err(exc)


@app.route("/api/review/v2/dates")
def api_review_v2_dates():
    """9 维度复盘历史日期列表。"""
    try:
        from db.storage import get_review_v2_dates
        return _ok({"dates": get_review_v2_dates()})
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# DM-kun 市场分析 (quant.dm_kun) - 内存 cache + 手动重算
# ---------------------------------------------------------------------------
# 三个高频分析: 市场状态 / 情绪周期 / 行业拥挤度.
# 跑一次 5-60s, 不能放同步路径. server 启动时后台预热填 cache, 前端 GET 永远不阻塞.
# POST /api/dm-kun/<name>/recompute 手动触发重算, 用于 daily 复盘后刷新数据.

_DM_KUN_ENDPOINTS = {
    "market-regime":      "market_regime",
    "sentiment-cycle":    "sentiment_cycle",
    "industry-crowding":  "industry_crowding",
    "industry-enhanced":  "industry_enhanced",
    "theme-ladder":       "theme_ladder",
    "stock-recommender":  "stock_recommender",
}


@app.route("/api/dm-kun/<name>")
def api_dm_kun_get(name: str):
    """返 cache: {markdown, computed_at, loading, error}. cache 空时 loading=True."""
    if name not in _DM_KUN_ENDPOINTS:
        return _err(f"unknown dm-kun endpoint: {name}", 404)
    with _DM_KUN_LOCK:
        entry = dict(_DM_KUN_CACHE[_DM_KUN_ENDPOINTS[name]])
    return _ok(entry)


@app.route("/api/dm-kun/<name>/recompute", methods=["POST"])
def api_dm_kun_recompute(name: str):
    """手动重算: 启动后台线程跑 main(), 立即返 {started: True, loading: True}.
    跑完会自动更新 cache, 前端轮询 GET 看 loading=false.
    同一名字已 loading 时拒绝 (避免并发)."""
    if name not in _DM_KUN_ENDPOINTS:
        return _err(f"unknown dm-kun endpoint: {name}", 404)
    cache_key = _DM_KUN_ENDPOINTS[name]
    with _DM_KUN_LOCK:
        if _DM_KUN_CACHE[cache_key]["loading"]:
            return _err(f"{name} 已在计算中, 请等完成", 409)
    # 解析 JSON body 拿额外 args (e.g. industries list for stock_recommender)
    body = {}
    try:
        body = request.get_json(silent=True) or {}
    except Exception:
        body = {}
    user_industries = body.get("industries") if isinstance(body, dict) else None
    def _runner():
        extra = list(_DM_KUN_EXTRA_ARGS.get(cache_key, []))
        if cache_key == "stock_recommender":
            inds = user_industries if (isinstance(user_industries, list) and user_industries) else None
            extra.extend(inds if inds else _dm_kun_default_industries())
        _dm_kun_run_one(cache_key, _DM_KUN_SCRIPT[cache_key], extra)
    threading.Thread(target=_runner, daemon=True, name=f"dm_kun_recompute_{name}").start()
    return _ok({"started": True, "name": name})


@app.route("/api/dm-kun/list")
def api_dm_kun_list():
    """列出所有 dm-kun endpoint 状态 (给前端 dashboard 入口用)."""
    with _DM_KUN_LOCK:
        items = [{"name": n, "status": "loading" if _DM_KUN_CACHE[k]["loading"]
                                           else "ok" if _DM_KUN_CACHE[k]["markdown"]
                                           else "empty",
                  "computed_at": _DM_KUN_CACHE[k]["computed_at"],
                  "error": _DM_KUN_CACHE[k]["error"]}
                 for n, k in _DM_KUN_ENDPOINTS.items()]
    return _ok({"items": items})


# ---------------------------------------------------------------------------
# Runtime config (DATA_ROOT / RSSHub)
# ---------------------------------------------------------------------------

@app.route("/api/config", methods=["GET"])
def api_config_get():
    """返回当前运行时配置值。"""
    import quant.loader as loader
    rsshub_global = os.environ.get("RSSHUB_BASE_URL", "")
    data_root = str(loader.DATA_ROOT) if loader.DATA_ROOT else ""
    flask_port = os.environ.get("FLASK_PORT", "20026")
    quant_workers = os.environ.get("QUANT_WORKERS", "")
    agent_enabled = os.environ.get("AGENT_ENABLED", "true")
    compute_enabled = os.environ.get("COMPUTE_ENABLED", "true")
    qmt_path = os.environ.get("QMT_PATH", "")
    qmt_bridge_url = os.environ.get("QMT_BRIDGE_URL", "")
    qmt_bridge_token = os.environ.get("QMT_BRIDGE_TOKEN", "")
    qmt_status = _read_qmt_runtime_status()
    return _ok({
        "data_root": data_root,
        "rsshub_url": rsshub_global,
        "flask_port": flask_port,
        "quant_workers": quant_workers,
        "agent_enabled": agent_enabled,
        "compute_enabled": compute_enabled,
        "qmt_enabled": "true" if qmt_status["enabled"] else "false",
        "qmt_path": qmt_path,
        "qmt_bridge_url": qmt_bridge_url,
        "qmt_bridge_token": qmt_bridge_token,
        "qmt_connected": qmt_status["connected"],
        "qmt_version": qmt_status["version"],
    })


@app.route("/api/config", methods=["POST"])
def api_config_set():
    """更新运行时配置，并持久化到 .env.local。"""
    import quant.loader as loader
    from pathlib import Path
    body = request.get_json(silent=True) or {}
    changed = []
    env_updates = {}

    if "data_root" in body:
        new_path = body["data_root"].strip()
        if new_path and Path(new_path).exists():
            loader.DATA_ROOT = Path(new_path)
            os.environ["QUANT_DATA_ROOT"] = new_path
            env_updates["QUANT_DATA_ROOT"] = new_path
            changed.append(f"QUANT_DATA_ROOT → {new_path}")
        elif new_path:
            return _err(f"路径不存在: {new_path}", 400)
        else:
            # 清空路径
            loader.DATA_ROOT = None
            os.environ.pop("QUANT_DATA_ROOT", None)
            env_updates["QUANT_DATA_ROOT"] = ""
            changed.append("QUANT_DATA_ROOT 已清空")

    if "rsshub_url" in body:
        new_url = body["rsshub_url"].strip().rstrip("/")
        if new_url:
            os.environ["RSSHUB_BASE_URL"] = new_url
            env_updates["RSSHUB_BASE_URL"] = new_url
            import sys
            for mod_name in ("fetcher.global_news", "fetcher.policy_rss"):
                mod = sys.modules.get(mod_name)
                if mod:
                    mod.RSSHUB = new_url
            changed.append(f"RSSHUB_BASE_URL → {new_url}")

    if "flask_port" in body:
        new_port = body["flask_port"].strip()
        if new_port.isdigit() and 1024 <= int(new_port) <= 65535:
            env_updates["FLASK_PORT"] = new_port
            changed.append(f"FLASK_PORT → {new_port}（重启后生效）")
        elif new_port:
            return _err(f"端口无效: {new_port}，需为 1024-65535 之间的数字", 400)

    if "quant_workers" in body:
        val = body["quant_workers"].strip()
        if val == "":
            os.environ.pop("QUANT_WORKERS", None)
            env_updates["QUANT_WORKERS"] = ""
            changed.append("QUANT_WORKERS 已清空（自动检测并发数）")
        elif val == "0" or val == "1":
            # 0 或 1 均表示串行模式（单线程/单进程）
            os.environ["QUANT_WORKERS"] = val
            env_updates["QUANT_WORKERS"] = val
            changed.append(f"QUANT_WORKERS → {val}（串行模式，立即生效）")
        elif val.isdigit() and 2 <= int(val) <= 64:
            os.environ["QUANT_WORKERS"] = val
            env_updates["QUANT_WORKERS"] = val
            changed.append(f"QUANT_WORKERS → {val}（立即生效）")
        else:
            return _err(f"并发数无效: {val}，需为 0-64 之间的整数（0 或 1 表示串行）", 400)

    if "agent_enabled" in body:
        raw = body["agent_enabled"]
        if isinstance(raw, bool):
            val = "true" if raw else "false"
        else:
            val = "true" if str(raw).strip().lower() in ("true", "1", "yes") else "false"
        os.environ["AGENT_ENABLED"] = val
        env_updates["AGENT_ENABLED"] = val
        changed.append(f"AGENT_ENABLED → {val}（立即生效）")

    if "compute_enabled" in body:
        raw = body["compute_enabled"]
        if isinstance(raw, bool):
            val = "true" if raw else "false"
        else:
            val = "true" if str(raw).strip().lower() in ("true", "1", "yes") else "false"
        os.environ["COMPUTE_ENABLED"] = val
        env_updates["COMPUTE_ENABLED"] = val
        changed.append(f"COMPUTE_ENABLED → {val}（重启后生效）")

    if "qmt_enabled" in body:
        raw = body["qmt_enabled"]
        if isinstance(raw, bool):
            val = "true" if raw else "false"
        else:
            val = "true" if str(raw).strip().lower() in ("true", "1", "yes") else "false"
        os.environ["QMT_ENABLED"] = val
        # 同步到 xtquant_breadth 模块（如果已加载）
        import sys
        mod = sys.modules.get("fetcher.xtquant_breadth")
        if mod:
            pass  # xtquant_breadth 每次调用时读 os.environ，无需额外同步
        env_updates["QMT_ENABLED"] = val
        changed.append(f"QMT_ENABLED → {val}（立即生效）")

    if "qmt_path" in body:
        new_path = body["qmt_path"].strip()
        os.environ["QMT_PATH"] = new_path
        env_updates["QMT_PATH"] = new_path
        if new_path:
            changed.append(f"QMT_PATH → {new_path}")
        else:
            changed.append("QMT_PATH 已清空")

    if "qmt_bridge_url" in body:
        new_url = body["qmt_bridge_url"].strip().rstrip("/")
        if new_url:
            # 简单格式校验：必须以 http:// 或 https:// 开头
            if not (new_url.startswith("http://") or new_url.startswith("https://")):
                return _err(f"bridge URL 必须以 http:// 或 https:// 开头：{new_url}", 400)
            os.environ["QMT_BRIDGE_URL"] = new_url
            env_updates["QMT_BRIDGE_URL"] = new_url
            changed.append(f"QMT_BRIDGE_URL → {new_url}（立即生效）")
        else:
            os.environ.pop("QMT_BRIDGE_URL", None)
            env_updates["QMT_BRIDGE_URL"] = ""
            changed.append("QMT_BRIDGE_URL 已清空（QMT 数据源回退到本地 xtquant）")

    if "qmt_bridge_token" in body:
        new_token = body["qmt_bridge_token"].strip()
        if new_token:
            os.environ["QMT_BRIDGE_TOKEN"] = new_token
            env_updates["QMT_BRIDGE_TOKEN"] = new_token
            changed.append("QMT_BRIDGE_TOKEN → ******（已更新，立即生效）")
        else:
            os.environ.pop("QMT_BRIDGE_TOKEN", None)
            env_updates["QMT_BRIDGE_TOKEN"] = ""
            changed.append("QMT_BRIDGE_TOKEN 已清空")

    if env_updates:
        try:
            _save_env_local(env_updates)
        except Exception as e:
            logger_api = __import__("logging").getLogger(__name__)
            logger_api.warning("写入 .env.local 失败: %s", e)

    return _ok({"changed": changed})


@app.route("/api/config/test-data-root")
def api_test_data_root():
    """检查 DATA_ROOT 路径是否存在且包含必要的 parquet 文件。"""
    import quant.loader as loader
    from pathlib import Path
    path_str = request.args.get("path", "").strip()
    check_path = Path(path_str) if path_str else loader.DATA_ROOT
    if not check_path:
        return _ok({"ok": False, "reason": "未配置路径"})
    if not check_path.exists():
        return _ok({"ok": False, "reason": f"路径不存在: {check_path}"})
    # 检查关键文件
    key_file = check_path / "factors" / "stock" / "daily" / "涨停相关因子.parquet"
    if not key_file.exists():
        return _ok({"ok": False, "reason": f"未找到涨停因子文件，请确认路径正确"})
    return _ok({"ok": True, "reason": f"路径有效: {check_path}"})


@app.route("/api/config/test-rsshub")
def api_test_rsshub():
    """检查 RSSHub 服务是否可达。"""
    import requests as _req
    url_param = request.args.get("url", "").strip().rstrip("/")
    test_url = url_param or os.environ.get("RSSHUB_BASE_URL", "")
    if not test_url:
        return _ok({"ok": False, "reason": "未配置 RSSHub 地址"})
    try:
        r = _req.get(f"{test_url}/", timeout=4)
        if r.status_code < 500:
            return _ok({"ok": True, "reason": f"连通（HTTP {r.status_code}）"})
        return _ok({"ok": False, "reason": f"服务异常（HTTP {r.status_code}）"})
    except _req.exceptions.ConnectionError:
        return _ok({"ok": False, "reason": "连接被拒绝，请确认 RSSHub 已启动"})
    except _req.exceptions.Timeout:
        return _ok({"ok": False, "reason": "连接超时（>4s）"})
    except Exception as e:
        return _ok({"ok": False, "reason": str(e)})


@app.route("/api/config/test-qmt")
def api_test_qmt():
    """检测 miniQMT 是否可达，返回连接状态和 xtquant 版本。"""
    from pathlib import Path

    # 检查 xtquant 是否安装
    try:
        from fetcher.xtquant_breadth import connect as _qmt_connect, get_version as _qmt_ver
    except Exception as e:
        return _ok({"ok": False, "reason": f"xtquant 模块加载失败: {e}", "version": None})

    version = _qmt_ver()
    if version is None:
        return _ok({"ok": False, "reason": "xtquant 未安装（pip install xtquant）", "version": None})

    # 检查 QMT 路径（可选验证）
    qmt_path = request.args.get("path", "").strip() or os.environ.get("QMT_PATH", "")
    if qmt_path:
        p = Path(qmt_path)
        if not p.exists():
            return _ok({"ok": False, "reason": f"QMT 路径不存在: {qmt_path}", "version": version})

    # 尝试连接
    # 临时强制 QMT_ENABLED=true 以便 connect() 不因开关而短路
    _orig = os.environ.get("QMT_ENABLED", "false")
    os.environ["QMT_ENABLED"] = "true"
    try:
        connected = _qmt_connect()
    finally:
        os.environ["QMT_ENABLED"] = _orig

    if connected:
        return _ok({"ok": True, "reason": f"miniQMT 连接成功（xtquant {version}）", "version": version})
    else:
        return _ok({"ok": False, "reason": "miniQMT 连接失败，请确认客户端已启动并登录", "version": version})


@app.route("/api/config/test-qmt-bridge")
def api_test_qmt_bridge():
    """
    探测 QMT bridge（远端 VM）是否可达。
    优先用 URL 查询参数里给的值（未保存也能测），否则读环境变量。
    """
    from fetcher import qmt_client

    url = request.args.get("url", "").strip() or os.environ.get("QMT_BRIDGE_URL", "")
    token = request.args.get("token", "").strip() or os.environ.get("QMT_BRIDGE_TOKEN", "")

    if not url or not token:
        return _ok({
            "ok": False,
            "reason": "未配置 bridge URL 或 token",
            "xtquant_version": None,
        })

    # 临时覆盖模块的 env 探测（用完恢复）
    import fetcher.qmt_client as _qc
    orig_url = os.environ.get("QMT_BRIDGE_URL", "")
    orig_token = os.environ.get("QMT_BRIDGE_TOKEN", "")
    os.environ["QMT_BRIDGE_URL"] = url
    os.environ["QMT_BRIDGE_TOKEN"] = token
    try:
        # 1) /health 检查网络 + xtquant 可用性
        health = _qc.health()
        if health is None:
            return _ok({
                "ok": False,
                "reason": f"无法连接 {url}（网络不通 / 服务未启动 / 防火墙挡）",
                "xtquant_version": None,
            })
        if not health.get("xtdata_available"):
            err = health.get("xtdata_error", "xtquant 未就绪")
            return _ok({
                "ok": False,
                "reason": f"bridge 在线但 xtquant 不可用：{err}",
                "xtquant_version": None,
            })
        # 2) /qmt/version 拿版本号
        v = _qc.version() or "已安装（无版本号）"
        # 3) 探测一个白名单方法（get_trading_dates 轻量）
        result = _qc._call("get_trading_dates", "SH", "20240101", "20240131")
        sample_ok = isinstance(result, list)
        return _ok({
            "ok": True,
            "reason": f"bridge 连接成功（xtquant {v}）" + ("" if sample_ok else "；样例调用未返回 list，请查日志"),
            "xtquant_version": v,
            "sample_call_ok": sample_ok,
        })
    except Exception as e:
        return _ok({
            "ok": False,
            "reason": f"探测异常：{e}",
            "xtquant_version": None,
        })
    finally:
        # 恢复
        if orig_url:
            os.environ["QMT_BRIDGE_URL"] = orig_url
        else:
            os.environ.pop("QMT_BRIDGE_URL", None)
        if orig_token:
            os.environ["QMT_BRIDGE_TOKEN"] = orig_token
        else:
            os.environ.pop("QMT_BRIDGE_TOKEN", None)


@app.route("/api/big-deal")
def api_big_deal():
    try:
        limit = int(request.args.get("limit", 50))
        rows = get_big_deal_latest(limit)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@app.route("/api/margin")
def api_margin():
    try:
        top_n = int(request.args.get("top_n", 50))
        rows = get_margin_latest(top_n)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@app.route("/api/block-trade")
def api_block_trade():
    try:
        limit = int(request.args.get("limit", 50))
        rows = get_block_trade_latest(limit)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@app.route("/api/holder-count")
def api_holder_count():
    try:
        top_n = int(request.args.get("top_n", 50))
        rows = get_holder_count_latest(top_n)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)





@app.route("/api/lockup-expiry")
def api_lockup_expiry():
    try:
        days = int(request.args.get("days", 30))
        rows = get_lockup_expiry(days)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@app.route("/api/dividend")
def api_dividend():
    try:
        limit = int(request.args.get("limit", 100))
        rows = get_dividend_latest(limit)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@app.route("/api/industry-ranking")
def api_industry_ranking():
    try:
        rows = get_industry_ranking_latest()
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@app.route("/api/ths-hot-stocks")
def api_ths_hot_stocks():
    try:
        top_n = int(request.args.get("top_n", 50))
        rows = get_ths_hot_stocks_latest(top_n)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@app.route("/api/research/pdf")
def api_research_pdf():
    """代理下载东财研报 PDF，自动注入 Referer 绕过 403。"""
    import requests as _req
    from flask import Response, stream_with_context
    pdf_url = request.args.get("url", "").strip()
    if not pdf_url:
        return _err("无效的 PDF 地址", 400)
    from urllib.parse import urlparse as _urlparse
    _parsed = _urlparse(pdf_url)
    if _parsed.scheme != "https" or _parsed.netloc != "pdf.dfcfw.com":
        return _err("无效的 PDF 地址", 400)
    try:
        r = _req.get(
            pdf_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://data.eastmoney.com/",
            },
            stream=True,
            timeout=20,
        )
        if r.status_code != 200:
            return _err(f"上游返回 {r.status_code}", 502)
        filename = pdf_url.split("/")[-1] or "report.pdf"
        return Response(
            stream_with_context(r.iter_content(chunk_size=8192)),
            content_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{filename}"'},
        )
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
# Cycle signal (顶底量化 — 算法已 copy 到 fetcher/stock_top_algorithms/)
# Phase 2: 0 依赖原 stock-top-and-bottom-analysis 文件夹, 走自管算法
# ---------------------------------------------------------------------------

@app.route("/api/cycle/summary")
def api_cycle_summary():
    """周期信号摘要（顶/底定位、phase、advice、上次信号、freshness）。"""
    try:
        return _ok(_build_cycle_summary())
    except Exception as exc:
        return _err(exc)


@app.route("/api/cycle/status")
def api_cycle_status():
    """数据状态：8 指标 + summary + index 是否就绪（用于顶部 banner）。"""
    try:
        return _ok(_cycle_data_status())
    except Exception as exc:
        return _err(exc)


@app.route("/api/cycle/indicators")
def api_cycle_indicators():
    """8 指标的最新值（用于顶部卡片网格，不含 series）。"""
    try:
        return _ok(_build_indicator_meta())
    except Exception as exc:
        return _err(exc)


@app.route("/api/cycle/indicator/<key>")
def api_cycle_indicator(key: str):
    """单个指标的时间序列（用于画 Plotly 趋势图）。

    Args:
        max_points: 最大点数（默认 3000；超过则均匀降采样，保留首尾）
    """
    try:
        max_points = int(request.args.get("max_points", 3000))
        return _ok(_build_indicator_series(key, max_points=max_points))
    except Exception as exc:
        return _err(exc)


@app.route("/api/cycle/equity-curve")
def api_cycle_equity_curve():
    """上证指数 close 序列。"""
    try:
        max_points = int(request.args.get("max_points", 3000))
        return _ok(_build_cycle_equity_curve(max_points=max_points))
    except Exception as exc:
        return _err(exc)


@app.route("/api/cycle/unified-score")
def api_cycle_unified_score():
    """unified_score = 7 指标 risk_percentage 等权 mean（与 cycle_signal runner 一致）。"""
    try:
        max_points = int(request.args.get("max_points", 3000))
        return _ok(_build_unified_score_series(max_points=max_points))
    except Exception as exc:
        return _err(exc)


@app.route("/api/cycle/pic/<name>")
def api_cycle_pic(name: str):
    """proxy data/pic/ 下的 PNG（cycle_position.png 等）。"""
    from flask import abort, send_file
    path = _cycle_get_pic_path(name)
    if not path or not path.exists():
        abort(404)
    return send_file(str(path), mimetype="image/png", max_age=300)


@app.route("/api/cycle/refresh", methods=["POST"])
def api_cycle_refresh():
    """手动触发一次刷新（异步，单飞）。

    Query param:
      mode=quick (默认): 只跑 step2+3，假设 raw_data 已齐
      mode=full:         跑 step1+2+3（先 akshare 拉数据，再算指标）
      notify=0/1:        跑完成功后是否推企微 (默认 0, 不推避免打扰)
      notify_address:    推送地址 (默认 'info', 可选从 /api/wecom/addresses 看)
    """
    try:
        mode = request.args.get("mode", "quick")
        notify = request.args.get("notify", "0") in ("1", "true", "True")
        notify_address = request.args.get("notify_address", "info") or "info"
        return _ok(_trigger_cycle_refresh(mode=mode, notify=notify, notify_address=notify_address))
    except Exception as exc:
        return _err(exc)


@app.route("/api/cycle/refresh/full", methods=["POST"])
def api_cycle_refresh_full():
    """完整刷新（异步，单飞）：step1+2+3。先 akshare 拉数据，再算指标。

    Query param:
      notify=0/1: 跑完成功后是否推企微 (默认 0)
      notify_address: 推送地址 (默认 'info')

    与 /api/cycle/refresh?mode=full 等价，独立端点方便前端绑定。
    """
    try:
        notify = request.args.get("notify", "0") in ("1", "true", "True")
        notify_address = request.args.get("notify_address", "info") or "info"
        return _ok(_trigger_cycle_refresh(mode="full", notify=notify, notify_address=notify_address))
    except Exception as exc:
        return _err(exc)


@app.route("/api/cycle/refresh/status")
def api_cycle_refresh_status():
    """轮询刷新任务状态（前端每 2-3s 查一次）。"""
    try:
        return _ok(_cycle_refresh_status())
    except Exception as exc:
        return _err(exc)


@app.route("/api/cycle/notify", methods=["POST"])
def api_cycle_notify():
    """手动推送当前周期信号到企业微信机器人。

    不做 refresh, 直接读 market-radar/data/results/cycle_signal_latest.json
    + data/pic/cycle_position.png 推到 webhook。

    Query/Body param:
      address: 推送地址 (默认 'info', 可选从 list_addresses() 看)

    Returns:
        {configured, text_ok, image_ok, dry_run, address, skipped_reason}
    """
    try:
        from fetcher.cycle_notifier import (
            is_configured,
            push_cycle_from_disk,
        )
        body = request.get_json(silent=True) or {}
        address = body.get("address") or request.args.get("address") or "info"
        return _ok({
            "address": address,
            "configured": is_configured(address),
            **push_cycle_from_disk(address=address),
        })
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# 全局企微推送 service (所有 AI 智能分析共用)
# ---------------------------------------------------------------------------

@app.route("/api/wecom/addresses")
def api_wecom_addresses():
    """列出所有可用企微推送地址 (从 config.WECOM_ROBOT_KEYS + 环境变量)。"""
    try:
        from fetcher.wecom_notifier import list_addresses, is_dry_run
        return _ok({
            "addresses": list_addresses(),
            "dry_run": is_dry_run(),
        })
    except Exception as exc:
        return _err(exc)


@app.route("/api/wecom/send-text", methods=["POST"])
def api_wecom_send_text():
    """推任意文本。

    Body: {"content": "..."[, "address": "info"]}
    """
    try:
        from fetcher.wecom_notifier import send_text
        body = request.get_json(silent=True) or {}
        content = body.get("content", "").strip()
        address = body.get("address") or request.args.get("address") or "info"
        if not content:
            return _err("content 不能为空", 400)
        ok = send_text(content, address=address)
        return _ok({"address": address, "ok": ok})
    except Exception as exc:
        return _err(exc)


@app.route("/api/wecom/send-markdown", methods=["POST"])
def api_wecom_send_markdown():
    """推 markdown 卡片 (企微原生支持, 长度 ≤ 4096 字节)。

    Body: {"content": "# 标题\\n**加粗**..."[, "address": "info"]}
    """
    try:
        from fetcher.wecom_notifier import send_markdown
        body = request.get_json(silent=True) or {}
        content = body.get("content", "").strip()
        address = body.get("address") or request.args.get("address") or "info"
        if not content:
            return _err("content 不能为空", 400)
        ok = send_markdown(content, address=address)
        return _ok({"address": address, "ok": ok})
    except Exception as exc:
        return _err(exc)


@app.route("/api/wecom/send-ai-report", methods=["POST"])
def api_wecom_send_ai_report():
    """推 AI 报告 (markdown 卡片: 标题 + 摘要 + 可选完整内容)。

    Body: {
        "title": "📊 盘后首席策略",
        "summary": "**情绪**: 🟡 中性\\n**操作**: 减仓",
        "full_text": "可选, 完整报告会被截断到 markdown 长度上限",
        "address": "info" (默认)
    }
    """
    try:
        from fetcher.wecom_notifier import send_ai_report
        body = request.get_json(silent=True) or {}
        title = body.get("title", "").strip()
        summary = body.get("summary", "").strip()
        full_text = body.get("full_text")
        address = body.get("address") or request.args.get("address") or "info"
        if not title or not summary:
            return _err("title 和 summary 必填", 400)
        ok = send_ai_report(
            title=title, summary=summary,
            full_text=full_text, address=address,
        )
        return _ok({"address": address, "ok": ok})
    except Exception as exc:
        return _err(exc)


@app.route("/api/agent/report/<int:row_id>/push", methods=["POST"])
def api_agent_report_push(row_id: int):
    """把指定 agent 报告推到企微。

    从 DB 读 report row, 取 content / report_html 推 markdown 卡片。
    Body: {"address": "info" (默认), "include_full": false (默认不推完整内容)}
    """
    try:
        from fetcher.wecom_notifier import send_ai_report, is_dry_run
        from db.storage import get_agent_summary_by_id
        import json as _json

        body = request.get_json(silent=True) or {}
        address = body.get("address") or request.args.get("address") or "info"
        include_full = bool(body.get("include_full", False))

        row = get_agent_summary_by_id(row_id)
        if not row:
            return _err(f"找不到报告 id={row_id}", 404)

        run_type = row.get("run_type", "?")
        summary_time = row.get("summary_time", "")

        # 优先用 data_snapshot_json 里的 summary_text, fallback 到 content
        snap = row.get("data_snapshot_json")
        summary = ""
        # full_text 默认用 report_html 剥 HTML (比 content 字段丰富, content 通常只是占位)
        html = row.get("report_html") or ""
        full_text = _strip_html(html) if html else row.get("content", "")
        if snap:
            try:
                d = _json.loads(snap)
                summary = d.get("summary_text") or summary
            except Exception:
                pass
        if not summary:
            summary = (row.get("content") or "")[:500]

        # 标题
        run_type_cn = {
            "morning": "盘前首席", "intraday": "盘中解说",
            "evening": "盘后首席", "policy": "政策解读",
            "research": "研报展望", "notice": "公告解读",
            "watchlist": "关注股池", "auction": "集合竞价",
            "closing": "盘后速递",
        }.get(run_type, run_type)
        title = f"🤖 {run_type_cn} · {summary_time[:16] if summary_time else ''}"

        ok = send_ai_report(
            title=title,
            summary=summary[:2000],
            full_text=full_text if include_full else None,
            address=address,
        )
        return _ok({
            "address": address,
            "row_id": row_id,
            "ok": ok,
            "dry_run": is_dry_run(),
        })
    except Exception as exc:
        return _err(exc)


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

    _port = int(os.environ.get("FLASK_PORT", 20026))

    # 同步预热 industry stats（在 scheduler 启动**之前**）：
    # 否则 fetch_dt_pool_v3 / fetch_zt_pool 等 scheduler task 会跟 warmup 抢 I/O，
    # 40s 算力被分散到 5+ 分钟，前端请求同步等
    print(f"[warmup] industry_stats 启动预热（scheduler 未起，独占 I/O）...")
    _bootstrap_industry_stats_warmup()
    _wait_for_industry_stats_cache(timeout=120)

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
        _dm_kun_prewarm()
    except Exception as _e:
        print(f"[dm_kun] prewarm 启动失败 (非致命): {_e}")

    print(f"[server] 仪表盘已启动 → http://0.0.0.0:{_port}")

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

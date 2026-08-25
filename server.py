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
from pathlib import Path

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

# ── 剥离代理环境变量 (2026-08-25, 智堡投研 Connection refused 事故) ──────────
# 本服务需要直连公网数据源 (智堡/东财/同花顺/财联社 等)。
# 若从带代理的会话(如 WorkBuddy 沙箱 HTTP_PROXY=127.0.0.1:50032)拉起,
# httpx/requests 会自动走代理, 而该代理端口对服务进程不可达 →
# 所有外部 API 报 Connection refused。这里一律清除, 保证无论以何种方式
# (start.sh / watchdog / 会话托管) 启动都直连。
for _proxy_var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
                   "ALL_PROXY", "all_proxy"):
    os.environ.pop(_proxy_var, None)

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

from flask import Flask, send_from_directory
from flask_cors import CORS

# Allow importing db/storage from the project root
sys.path.insert(0, os.path.dirname(__file__))

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
from api.fetch_all import bp as fetch_all_bp
from api.qmt import bp as qmt_bp
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
app.register_blueprint(fetch_all_bp)
app.register_blueprint(qmt_bp)


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
    from core.qmt_hub import _bootstrap_industry_stats_warmup, _wait_for_industry_stats_cache
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

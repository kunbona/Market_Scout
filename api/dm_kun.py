"""api/dm_kun.py — DM-kun 市场分析域 Blueprint (自 server.py 拆出)。

quant.dm_kun 的 6 个高频分析脚本 (市场状态/情绪周期/行业拥挤度/行业增强/
题材梯队/个股推荐), 跑一次 5-60s, 不能放 HTTP 同步路径:
内存 cache + 启动后台预热 + POST /recompute 手动重算 + dm_kun_daily 按日期存档。

对外入口 (供 server.py 复盘级联 / core/scheduler.py 定时链调用):
- dm_kun_recompute_all(trade_date): 串行重算全部 6 个 tab
- review_ai_with_dm_cache(trade_date): 把 DM-kun cache markdown 喂给复盘 AI 总结
- _dm_kun_prewarm(): server 启动后后台预热
"""
import logging
import os
import sys
import threading
from datetime import datetime

from flask import Blueprint, request

from api.common import _err, _ok

logger = logging.getLogger(__name__)

bp = Blueprint("dm_kun", __name__, url_prefix="/api/dm-kun")

# 项目根: 本文件在 api/ 子目录, subprocess 跑 -m quant.dm_kun.* 需 cwd=项目根
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# 内存 cache + 异步预热
# ---------------------------------------------------------------------------
# 11 个核心分析脚本里的 6 个高频 (市场状态/情绪周期/行业拥挤度/行业增强/题材梯队/个股推荐),
# 跑一次 5-60s, 不能放 HTTP 同步路径. 用内存 cache + 启动后台预热 +
# POST /recompute 手动重算. cache 结构: {"markdown": str, "computed_at": iso, "loading": bool}.

_DM_KUN_CACHE: dict[str, dict] = {
    "market_regime":     {"markdown": None, "computed_at": None, "loading": False, "error": None},
    "sentiment_cycle":   {"markdown": None, "computed_at": None, "loading": False, "error": None},
    "industry_crowding": {"markdown": None, "computed_at": None, "loading": False, "error": None},
    "industry_enhanced": {"markdown": None, "computed_at": None, "loading": False, "error": None},
    "theme_ladder":      {"markdown": None, "computed_at": None, "loading": False, "error": None},
    "stock_recommender": {"markdown": None, "computed_at": None, "loading": False, "error": None},
}
_DM_KUN_LOCK = threading.Lock()

# endpoint 名 (url 里的 <name>, 短横线) → cache key (下划线)
_DM_KUN_ENDPOINTS = {
    "market-regime":      "market_regime",
    "sentiment-cycle":    "sentiment_cycle",
    "industry-crowding":  "industry_crowding",
    "industry-enhanced":  "industry_enhanced",
    "theme-ladder":       "theme_ladder",
    "stock-recommender":  "stock_recommender",
}


def _dm_kun_run_one(name: str, script_module: str, extra_args: list[str] | None = None,
                    trade_date: str | None = None) -> None:
    """跑一个 quant.dm_kun 脚本 (独立子进程), 抓 stdout 当 markdown 存 cache.

    关键: 用 subprocess.run 起独立 Python 进程, capture_output=True 拿隔离的 stdout.
    不能直接调 main() 函数, 因为 main() 内部 ProcessPoolExecutor fork 的子进程
    stdout 直连父进程 fd, redirect_stdout 抓不到, 会跟并发跑的兄弟脚本串行.
    独立子进程 → stdout 完全隔离 → 干净 cache.

    extra_args: 传给脚本的额外 CLI args (e.g. ["--summary-only", "电子", "有色金属"]).
    trade_date: 指定历史交易日 → 追加 --date (脚本已支持), 并把结果存进
                dm_kun_daily 按日期存档; None=最新日, 只更新内存 cache。
    """
    import subprocess as _sp
    with _DM_KUN_LOCK:
        _DM_KUN_CACHE[name]["loading"] = True
        _DM_KUN_CACHE[name]["error"] = None
    try:
        cmd = [sys.executable, "-m", f"quant.dm_kun.{script_module}"]
        if extra_args:
            cmd.extend(extra_args)
        if trade_date:
            td = trade_date.replace("-", "")
            cmd.extend(["--date", trade_date])  # 脚本接受 YYYY-MM-DD
        result = _sp.run(
            # timeout 600s: 正常 5-60s/个, 但与其他任务 (启动预热/复盘级联) 并发时
            # 内存竞争会显著变慢, 180s 曾在 industry_enhanced 上超时 (复盘页截图实证)
            # 不指定 encoding → 沿用系统 locale (mac 默认 UTF-8); errors=replace 防止
            # 个别脚本输出非 UTF-8 字节导致 markdown 带非法字符、Flask jsonify 500
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=600, cwd=_REPO_ROOT, env=os.environ.copy(),
        )
        # 优先 stdout, 如果有 stderr 警告也保留 (前面)
        text = result.stdout or ""
        if result.returncode != 0 and not text:
            text = (result.stderr or "")[:4000]
        # 从 markdown 标题 parse 数据时间 (e.g. '## ... (2026-08-12)') —
        # 跟 computed_at (重算时间) 区分, 前端显示应该用数据时间
        # 兼容: 大部分脚本标题里有 (YYYY-MM-DD), 但 industry_enhanced/theme_ladder 等
        # 用 CSV 自身日期, 没在标题里 — 放宽到前 30 行内第一个 YYYY-MM-DD
        import re as _re
        data_date = None
        for line in text.split("\n")[:30]:
            m = _re.search(r"\((\d{4}-\d{2}-\d{2})\)", line)
            if m:
                data_date = m.group(1)
                break
            # 也匹配 '日期: 2026-08-11' / '数据日期: 2026-08-11' / '数据 2026-08-12' 等
            m = _re.search(r"(?:日期|data|数据)[：:]\s*(\d{4}-\d{2}-\d{2})", line, _re.IGNORECASE)
            if m:
                data_date = m.group(1)
                break
        # 兜底: 找 markdown 里第一个 YYYY-MM-DD (排除时间部分, 不含冒号)
        if data_date is None:
            for line in text.split("\n")[:50]:
                m = _re.search(r"\b(\d{4}-\d{2}-\d{2})\b", line)
                if m:
                    data_date = m.group(1)
                    break
        # 终极兜底: 用 CSV 最新交易日 (industry_enhanced / theme_ladder 等脚本
        # markdown 里没标日期, 但实际是基于最新 CSV 算的)
        if data_date is None:
            try:
                from quant.loader import get_latest_trade_date
                data_date = get_latest_trade_date() or None
            except Exception:
                pass
        with _DM_KUN_LOCK:
            _DM_KUN_CACHE[name]["markdown"] = text
            _DM_KUN_CACHE[name]["computed_at"] = datetime.now().isoformat(timespec="seconds")
            _DM_KUN_CACHE[name]["data_date"] = data_date
            if result.returncode != 0:
                _DM_KUN_CACHE[name]["error"] = f"exit {result.returncode}"
        # 按日期存档: 复盘页 6 个 DM-kun tab 统一进日期管理, 切日期回看历史。
        # 存档 key 用请求的目标交易日 (跟复盘/行业趋势同一口径), 无 --date 时退回解析到的 data_date。
        # name 这里是 cache key (market_regime), 存档统一用 endpoint 名 (market-regime) 跟 GET 查询对齐。
        archive_date = trade_date or data_date
        if archive_date and text.strip():
            try:
                from db.storage import insert_dm_kun_daily
                ep_name = next((k for k, v in _DM_KUN_ENDPOINTS.items() if v == name), name)
                insert_dm_kun_daily(archive_date, ep_name, text, data_date=data_date,
                                    computed_at=_DM_KUN_CACHE[name]["computed_at"])
            except Exception as exc:
                logger.warning("[dm_kun] %s 存档失败: %s", name, exc)
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


# ---------------------------------------------------------------------------
# 级联重算 + 复盘 AI 联动 (供 server.py 复盘 job / core/scheduler.py 调用)
# ---------------------------------------------------------------------------

def dm_kun_recompute_all(trade_date: str | None = None) -> dict:
    """串行重算全部 6 个 DM-kun tab (阻塞调用方线程, 共 2-6 分钟).

    供两条路径复用:
    - 复盘重算完成后级联刷新 (按钮 / 16:00 定时任务)
    - 其他需要全量刷新 DM-kun 的场景
    模块名走 _DM_KUN_SCRIPT 映射 (market_regime → market_regime_analyzer),
    额外 args 跟 prewarm / 单 tab recompute 端点保持一致。
    trade_date: 复盘选中日期 → 各脚本按 --date 切片到该日并把结果按日期存档;
                None=最新日, 仍存档到解析出的 data_date。
    返回 {endpoint: "ok" | "error: ..."}
    """
    results = {}
    for ep_name, cache_key in _DM_KUN_ENDPOINTS.items():
        try:
            # 正在算 (如启动预热/单 tab 重算) → 跳过, 避免同一脚本双跑抢内存
            with _DM_KUN_LOCK:
                if _DM_KUN_CACHE[cache_key]["loading"]:
                    results[ep_name] = "skipped (已在计算中)"
                    continue
            extra = list(_DM_KUN_EXTRA_ARGS.get(cache_key, []))
            if cache_key == "stock_recommender":
                extra.extend(_dm_kun_default_industries())
            _dm_kun_run_one(cache_key, _DM_KUN_SCRIPT[cache_key], extra, trade_date=trade_date)
            with _DM_KUN_LOCK:
                err = _DM_KUN_CACHE[cache_key].get("error")
            results[ep_name] = "ok" if not err else f"error: {err}"
            logger.info("[dm-kun] 级联重算 %s %s (date=%s)", ep_name, results[ep_name], trade_date or "最新")
        except Exception as exc:
            results[ep_name] = f"error: {exc}"
            logger.warning("[dm-kun] 级联重算 %s 失败: %s", ep_name, exc)
    return results


def review_ai_with_dm_cache(trade_date: str | None = None) -> dict:
    """复盘 AI 总结: 把 DM-kun 内存 cache 的 markdown 一起喂给 agent.review_ai。

    在 server 进程内调用 (cache 在这里); 手动重算 job、手动按钮和 20:30 定时链共用。
    """
    try:
        from agent.review_ai import run as run_review_ai
        with _DM_KUN_LOCK:
            dm = {k: (v.get("markdown") or "") for k, v in _DM_KUN_CACHE.items()}
        return run_review_ai(trade_date, extra_context={"dm_kun": dm})
    except Exception as exc:
        logger.warning("[review-ai] 失败: %s", exc)
        return {"status": "error", "reason": str(exc)}


# ---------------------------------------------------------------------------
# 路由 (内存 cache + 手动重算)
# ---------------------------------------------------------------------------
# 跑一次 5-60s, 不能放同步路径. server 启动时后台预热填 cache, 前端 GET 永远不阻塞.
# POST /api/dm-kun/<name>/recompute 手动触发重算, 用于 daily 复盘后刷新数据.

@bp.route("/<name>")
def api_dm_kun_get(name: str):
    """返 {markdown, computed_at, data_date, loading, error, trade_date, archived}.

    - 带 ?date=YYYY-MM-DD: 读 dm_kun_daily 该日期存档(切日期回看历史);
      无存档返回 {archived:false, trade_date, markdown:null} 由前端显示引导。
    - 不带 date: 返内存 cache(最新一次重算), cache 空时 loading=True。
    """
    if name not in _DM_KUN_ENDPOINTS:
        return _err(f"unknown dm-kun endpoint: {name}", 404)
    date = (request.args.get("date") or "").strip()
    if date:
        if len(date) == 8 and date.isdigit():
            date = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
        from db.storage import get_dm_kun_daily
        row = get_dm_kun_daily(date, name)
        if row is None:
            return _ok({"trade_date": date, "name": name, "markdown": None,
                        "data_date": None, "computed_at": None,
                        "loading": False, "error": None, "archived": False})
        # 清洗可能混入的非 UTF-8 字符(早期 subprocess 用 locale 解码残留), 防 jsonify 500
        if row.get("markdown"):
            row["markdown"] = row["markdown"].encode("utf-8", "replace").decode("utf-8", "replace")
        return _ok({**row, "loading": False, "error": None, "archived": True})
    with _DM_KUN_LOCK:
        entry = dict(_DM_KUN_CACHE[_DM_KUN_ENDPOINTS[name]])
    # 内存 cache 空时从最新存档回填 (重启后预热未完成前也能看到最近一次数据)
    if not entry.get("markdown") and not entry.get("loading"):
        from db.storage import get_dm_kun_latest_by_name
        latest = get_dm_kun_latest_by_name(name)
        if latest and latest.get("markdown"):
            latest["markdown"] = latest["markdown"].encode("utf-8", "replace").decode("utf-8", "replace")
            latest["archived"] = True
            return _ok(latest)
    entry["archived"] = False
    return _ok(entry)


@bp.route("/dates")
def api_dm_kun_dates():
    """DM-kun 有存档的日期列表(任一 tab 有就算), 供前端判断历史可看性。"""
    try:
        from db.storage import get_dm_kun_dates
        return _ok(get_dm_kun_dates())
    except Exception as exc:
        return _err(exc)


@bp.route("/<name>/recompute", methods=["POST"])
def api_dm_kun_recompute(name: str):
    """手动重算: 启动后台线程跑 main(), 立即返 {started: True, loading: True}.
    跑完会自动更新 cache 并按日期存档, 前端轮询 GET 看 loading=false.
    同一名字已 loading 时拒绝 (避免并发).
    body 可带 date=YYYY-MM-DD → 按该历史日切片算并存档(复盘页统一管理)。"""
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
    date = (body.get("date") or "").strip() if isinstance(body, dict) else ""
    if date and len(date) == 8 and date.isdigit():
        date = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    trade_date = date or None
    def _runner():
        extra = list(_DM_KUN_EXTRA_ARGS.get(cache_key, []))
        if cache_key == "stock_recommender":
            inds = user_industries if (isinstance(user_industries, list) and user_industries) else None
            extra.extend(inds if inds else _dm_kun_default_industries())
        _dm_kun_run_one(cache_key, _DM_KUN_SCRIPT[cache_key], extra, trade_date=trade_date)
    threading.Thread(target=_runner, daemon=True, name=f"dm_kun_recompute_{name}").start()
    return _ok({"started": True, "name": name, "trade_date": trade_date})


@bp.route("/list")
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

"""api/review.py — 复盘域 Blueprint (自 server.py 拆出)。

三段合一:
- Daily review (legacy): /api/review/latest | dates | data
- 复盘 v2 异步任务: /api/review/v2/run | job | latest | dates
  (原实现同步阻塞 5-7 分钟, 浏览器/代理容易先超时断开。改为后台线程跑,
  复盘完成后自动级联刷新行业趋势 + DM-kun 6 个 tab, 两套系统一次按钮全刷新)
- 复盘 AI 总结手动触发: /api/agent/review_ai | /api/agent/review_ai/job

url_prefix="/api" (三段路径前缀不统一: /api/review 和 /api/agent/review_ai)。
"""
import json as _json
import logging
import threading
from datetime import datetime

from flask import Blueprint, request

from api.common import _err, _ok

logger = logging.getLogger(__name__)

bp = Blueprint("review", __name__, url_prefix="/api")


# ---------------------------------------------------------------------------
# Daily review analysis (legacy review_daily)
# ---------------------------------------------------------------------------

@bp.route("/review/latest")
def api_review_latest():
    """最近一次复盘 — 永远返回 cache, 不触发自动重算 (compute 30-120s 太慢会拖死前端)。

    想拿新数据: 调 /api/review/data?date=YYYY-MM-DD (单日重算, 走 INSERT OR REPLACE)。
    想批量刷新: 手动跑 `python -c "from quant.review_compute import compute_daily_analysis; compute_daily_analysis()"`。
    """
    try:
        from db.storage import get_review_daily, insert_review_daily
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


@bp.route("/review/dates")
def api_review_dates():
    try:
        from db.storage import get_review_dates
        dates = get_review_dates()
        # 把 Exodia 数据中心的最新数据日合并到顶部 (即使还没算过, 也要让用户能选它)
        # source of truth: products-status.json → stock-trading-data-pro-daily.dataContentTime
        try:
            from agent.review_v2 import _exodia_latest_stock_date
            ex = _exodia_latest_stock_date()
            if ex:
                # 归一化成 ISO (DB 存 ISO, dataContentTime 正常也是 ISO)
                if len(ex) == 8 and ex.isdigit():
                    ex = f"{ex[:4]}-{ex[4:6]}-{ex[6:8]}"
                if ex not in dates:
                    dates.insert(0, ex)
        except Exception as exc:
            logger.warning("[review/dates] 合并 Exodia 最新日失败: %s", exc)
        return _ok(dates)
    except Exception as exc:
        return _err(exc)


@bp.route("/review/data")
def api_review_data():
    try:
        import time as _time
        from db.storage import get_review_daily, insert_review_daily
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
        if not row and not force:
            # 该日期还没算过 → 返回空, 由前端提示用户点「重算选中日期」
            # (不再自动跑 legacy compute_daily_analysis — 那会写半成品 payload 污染缓存)
            logger.info("[review] %s 未计算且未 force, 返回空 (等用户显式重算)", trade_date)
            return _ok(None)
        # force=1 → 同步重算 (1-2 分钟, 期间 HTTP 不返, 前端按钮转圈等)
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


# ---------------------------------------------------------------------------
# 复盘 v2 异步任务 (POST 立即返回, 前端轮询 /api/review/v2/job)
# ---------------------------------------------------------------------------

_REVIEW_V2_JOB: dict = {
    "state": "idle",        # idle | running | done | error
    "trade_date": None,
    "progress": "",
    "result": None,
    "dm_kun": None,
    "industry_trend": None,   # 行业趋势页级联结果 (2026-08-20 联动)
    "error": None,
    "started_at": None,
    "finished_at": None,
}
_review_v2_job_lock = threading.Lock()


def _review_v2_job_worker(trade_date, force: bool) -> None:
    job = _REVIEW_V2_JOB
    try:
        job["progress"] = "复盘计算中 (L4 板块效应 + 汇总, 约 2-6 分钟)"
        from agent.review_v2 import run as run_review_v2
        result = run_review_v2(trade_date, force=force)
        job["result"] = result
        # 级联 1: 行业趋势页同日期截面 (用户要求"和复盘数据页一起联动",
        # 一次重算按钮两套系统同更新)。先增量刷申万指数/资金流缓存——
        # 否则新交易日 resolve_trade_date 会把日期归一到缓存里的旧一天, 联动静默失效。
        try:
            from quant.industry_trend_daily import refresh_sw_caches, run_industry_trend
            job["progress"] = "复盘完成, 级联刷新行业趋势 (先增量拉申万指数/资金流, 1-4 分钟)..."
            td = (result or {}).get("trade_date") or trade_date
            refresh_sw_caches()
            it = run_industry_trend(td, force_recompute=True)
            job["industry_trend"] = {
                "trade_date": (it or {}).get("trade_date"),
                "changes": len((it or {}).get("changes", [])),
            }
        except Exception as exc:
            job["industry_trend"] = {"error": str(exc)}
            logger.warning("[review-v2-job] 行业趋势级联失败: %s", exc)
        job["progress"] = "复盘完成, 级联刷新 DM-kun 6 个 tab (约 1-5 分钟)..."
        try:
            # 传 td → 各 DM-kun 脚本按 --date 切片到复盘选中日并按日期存档,
            # 6 个 tab 统一进日期管理 (切日期可回看历史)
            from api.dm_kun import dm_kun_recompute_all
            job["dm_kun"] = dm_kun_recompute_all(trade_date=td)
        except Exception as exc:
            job["dm_kun"] = {"error": str(exc)}
            logger.warning("[review-v2-job] DM-kun 级联失败: %s", exc)
        # 手动重算不跑 AI 总结 (无意义还多等 1-3 分钟); 晚间定时链 (scheduler 20:30
        # _run_review) 自带 AI, 不走本 worker。
        job["state"] = "done"
    except Exception as exc:
        job["state"] = "error"
        job["error"] = str(exc)
        logger.exception("[review-v2-job] 重算失败")
    finally:
        job["finished_at"] = datetime.now().isoformat(timespec="seconds")
        job["progress"] = ""


@bp.route("/review/v2/run", methods=["POST"])
def api_review_v2_run():
    """手动触发 9 维度复盘 (后台任务, 立即返回).

    body: {trade_date?: "YYYY-MM-DD", force?: bool}
    - 不传 trade_date: 自动从 Exodia status 拿 stock-trading-data-pro-daily 最新日
    - 完成后自动级联刷新 DM-kun 6 个 tab
    - 已有任务在跑: 返回 {started: false, state: "running"}
    前端轮询 GET /api/review/v2/job 拿进度和结果。
    """
    try:
        body = request.get_json(silent=True) or {}
        trade_date = (body.get("trade_date") or "").strip() or None
        force = bool(body.get("force", False))
        with _review_v2_job_lock:
            if _REVIEW_V2_JOB["state"] == "running":
                return _ok({"started": False, "state": "running",
                            "progress": _REVIEW_V2_JOB["progress"]})
            _REVIEW_V2_JOB.update(
                state="running", trade_date=trade_date, progress="启动中...",
                result=None, dm_kun=None, industry_trend=None, error=None, ai=None,
                started_at=datetime.now().isoformat(timespec="seconds"),
                finished_at=None,
            )
            threading.Thread(
                target=_review_v2_job_worker, args=(trade_date, force),
                daemon=True, name="review-v2-job",
            ).start()
        return _ok({"started": True, "state": "running"})
    except Exception as exc:
        return _err(exc)


@bp.route("/review/v2/job")
def api_review_v2_job():
    """复盘 v2 异步任务状态 (前端 5s 轮询)."""
    with _review_v2_job_lock:
        return _ok(dict(_REVIEW_V2_JOB))


@bp.route("/review/v2/latest")
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


@bp.route("/review/v2/dates")
def api_review_v2_dates():
    """9 维度复盘历史日期列表。"""
    try:
        from db.storage import get_review_v2_dates
        return _ok({"dates": get_review_v2_dates()})
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# 复盘 AI 总结 手动触发 (异步 job, 前端轮询 /api/agent/review_ai/job)
# ---------------------------------------------------------------------------

_REVIEW_AI_JOB: dict = {
    "state": "idle",        # idle | running | done | error
    "progress": "",
    "result": None,
    "error": None,
    "started_at": None,
    "finished_at": None,
}
_review_ai_job_lock = threading.Lock()


def _review_ai_job_worker(trade_date) -> None:
    from api.dm_kun import review_ai_with_dm_cache
    with _review_ai_job_lock:
        _REVIEW_AI_JOB["progress"] = "AI 正在阅读复盘数据 + DM-kun 分析 (约 1 分钟)..."
    result = review_ai_with_dm_cache(trade_date)
    with _review_ai_job_lock:
        st = result.get("status")
        if st in ("ok", "warn"):
            _REVIEW_AI_JOB.update(state="done", progress="完成", result=result, error=None)
        else:
            _REVIEW_AI_JOB.update(state="error", progress="失败",
                                  result=result, error=result.get("reason") or st)
        _REVIEW_AI_JOB["finished_at"] = datetime.now().isoformat(timespec="seconds")
    logger.info("[review-ai] 手动 job 结束: %s", result)


@bp.route("/agent/review_ai", methods=["POST"])
def api_agent_review_ai_run():
    """手动触发复盘 AI 总结 (后台线程, 立即返回; 前端轮询 GET /api/agent/review_ai/job)."""
    try:
        body = request.get_json(silent=True) or {}
        trade_date = (body.get("trade_date") or "").strip() or None
        with _review_ai_job_lock:
            if _REVIEW_AI_JOB["state"] == "running":
                return _ok({"started": False, "state": "running",
                            "progress": _REVIEW_AI_JOB["progress"]})
            _REVIEW_AI_JOB.update(
                state="running", progress="启动中...", result=None, error=None,
                started_at=datetime.now().isoformat(timespec="seconds"),
                finished_at=None,
            )
        threading.Thread(target=_review_ai_job_worker, args=(trade_date,),
                         daemon=True, name="review-ai-job").start()
        return _ok({"started": True, "state": "running"})
    except Exception as exc:
        return _err(exc)


@bp.route("/agent/review_ai/job")
def api_agent_review_ai_job():
    """复盘 AI 总结手动任务状态 (前端轮询)."""
    with _review_ai_job_lock:
        return _ok(dict(_REVIEW_AI_JOB))

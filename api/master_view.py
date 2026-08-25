"""api/master_view.py — 总览 AI 分析域 Blueprint。

汇总全部数据源 (行业趋势截面/复盘/DM-kun/资金流/既有 AI 结论) 做顶层
AI 交叉分析。跑一次 1-2 分钟, 异步 job + 前端轮询 (同 review_ai 模式)。

路由:
- POST /api/master-view/run   手动触发 (body 可带 trade_date)
- GET  /api/master-view/job   任务状态轮询
- GET  /api/master-view/latest  最新一条总览报告 (data_snapshot_json 解析后)
- GET  /api/master-view/history 历史列表
"""
import logging
import threading
from datetime import datetime

from flask import Blueprint, request

from api.common import _err, _ok

logger = logging.getLogger(__name__)

bp = Blueprint("master_view", __name__, url_prefix="/api/master-view")

# ---------------------------------------------------------------------------
# 异步 job (照抄 review_ai 的内存 job 状态模式)
# ---------------------------------------------------------------------------

_MASTER_VIEW_JOB: dict = {
    "state": "idle",        # idle | running | done | error
    "progress": "",
    "result": None,
    "error": None,
    "started_at": None,
    "finished_at": None,
}
_master_view_job_lock = threading.Lock()


def master_view_with_dm_cache(trade_date: str | None = None) -> dict:
    """把 DM-kun 内存 cache 的 markdown 一起喂给 agent.master_view。

    在 server 进程内调用 (DM-kun cache 在 api.dm_kun); 手动按钮和未来
    定时链共用这一入口。
    """
    try:
        from agent.master_view import run as run_master_view
        from api.dm_kun import _DM_KUN_CACHE, _DM_KUN_LOCK
        with _DM_KUN_LOCK:
            dm = {k: (v.get("markdown") or "") for k, v in _DM_KUN_CACHE.items()}
        return run_master_view(trade_date, extra_context={"dm_kun": dm})
    except Exception as exc:
        logger.warning("[master-view] 失败: %s", exc)
        return {"status": "error", "reason": str(exc)}


def _master_view_job_worker(trade_date) -> None:
    with _master_view_job_lock:
        _MASTER_VIEW_JOB["progress"] = "汇总行业趋势/复盘/DM-kun/资金流, AI 交叉分析中 (约 1-2 分钟)..."
    result = master_view_with_dm_cache(trade_date)
    with _master_view_job_lock:
        st = result.get("status")
        if st in ("ok", "warn"):
            _MASTER_VIEW_JOB.update(state="done", progress="完成", result=result, error=None)
        else:
            _MASTER_VIEW_JOB.update(state="error", progress="失败",
                                    result=result, error=result.get("reason") or st)
        _MASTER_VIEW_JOB["finished_at"] = datetime.now().isoformat(timespec="seconds")
    logger.info("[master-view] job 结束: %s", result)


@bp.route("/run", methods=["POST"])
def api_master_view_run():
    """手动触发总览 AI 分析 (后台线程, 立即返回; 前端轮询 /job)."""
    try:
        body = request.get_json(silent=True) or {}
        trade_date = (body.get("trade_date") or "").strip() or None
        if trade_date and len(trade_date) == 8 and trade_date.isdigit():
            trade_date = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
        with _master_view_job_lock:
            if _MASTER_VIEW_JOB["state"] == "running":
                return _ok({"started": False, "state": "running",
                            "progress": _MASTER_VIEW_JOB["progress"]})
            _MASTER_VIEW_JOB.update(
                state="running", progress="启动中...", result=None, error=None,
                started_at=datetime.now().isoformat(timespec="seconds"),
                finished_at=None,
            )
        threading.Thread(target=_master_view_job_worker, args=(trade_date,),
                         daemon=True, name="master-view-job").start()
        return _ok({"started": True, "state": "running"})
    except Exception as exc:
        return _err(exc)


@bp.route("/job")
def api_master_view_job():
    """总览分析任务状态 (前端轮询)."""
    with _master_view_job_lock:
        return _ok(dict(_MASTER_VIEW_JOB))


@bp.route("/latest")
def api_master_view_latest():
    """最新一条总览报告 (agent_summary run_type=master_view 的快照)."""
    try:
        from db.storage import get_agent_summary_latest_snapshot
        snap = get_agent_summary_latest_snapshot("master_view")
        return _ok(snap)
    except Exception as exc:
        return _err(exc)


@bp.route("/history")
def api_master_view_history():
    """总览分析历史列表."""
    try:
        from db.storage import get_agent_summary_history
        rows = get_agent_summary_history(limit=30, run_type="master_view")
        return _ok(rows)
    except Exception as exc:
        return _err(exc)

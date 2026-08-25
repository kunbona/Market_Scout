"""api/agent.py — Agent 分析域 Blueprint (自 server.py 拆出)。

AI 智能分析的 HTTP 层: 报告查询 (latest/history/html)、手动触发/停止、
状态轮询, 以及信息简报 (info_brief) 和战略推理 (strategist) 的
触发/查询端点。底层全走 agent.orchestrator / agent.info_brief_v2 /
agent.strategist (懒导入)。含 legacy /api/ai-summary 旧入口。

url_prefix="/api" (路径前缀不统一: /api/agent、/api/info_brief、/api/strategist、/api/ai-summary)。
"""
import logging
from datetime import datetime

from flask import Blueprint, request

from api.common import _err, _ok

logger = logging.getLogger(__name__)

bp = Blueprint("agent", __name__, url_prefix="/api")


@bp.route("/ai-summary")
def api_ai_summary():
    try:
        from db.storage import get_agent_summary_latest
        data = get_agent_summary_latest()
        return _ok(data)
    except Exception as exc:
        return _err(exc)


@bp.route("/agent/latest")
def api_agent_latest():
    """返回最新报告元数据（含 has_html、id，供前端决定用 iframe 还是 JSON 渲染）。"""
    try:
        import json
        from db.storage import get_agent_summary_latest
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


@bp.route("/agent/report/<int:row_id>")
def api_agent_report_html(row_id: int):
    """直接返回 HTML 报告，供 <iframe src="..."> 使用。"""
    try:
        from flask import Response
        from db.storage import get_agent_summary_by_id
        row = get_agent_summary_by_id(row_id)
        if not row:
            return Response("<h1>404 Not Found</h1>", status=404, mimetype="text/html")
        html = row.get("report_html") or ""
        if not html:
            return Response("<p>此记录无 HTML 报告</p>", status=404, mimetype="text/html")
        return Response(html, status=200, mimetype="text/html; charset=utf-8")
    except Exception as exc:
        return Response(f"<p>错误：{exc}</p>", status=500, mimetype="text/html")


@bp.route("/agent/history")
def api_agent_history():
    """返回最近 N 条报告摘要（run_type、run_time、summary_text、has_html）。"""
    try:
        import json
        from db.storage import get_agent_summary_history
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


@bp.route("/agent/history/<int:row_id>")
def api_agent_history_detail(row_id: int):
    """返回单条报告的完整结构化数据（含 has_html 标记）。"""
    try:
        import json
        from db.storage import get_agent_summary_by_id
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


@bp.route("/agent/trigger", methods=["POST"])
def api_agent_trigger():
    """手动触发 Agent 分析（非阻塞）。body: {"run_type": "evening"}"""
    try:
        from agent.orchestrator import run_agent_analysis
        from db.storage import pool_exists
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


@bp.route("/agent/status")
def api_agent_status():
    """返回当前 Agent 运行状态。"""
    try:
        from agent.orchestrator import get_agent_state
        return _ok(get_agent_state())
    except Exception as exc:
        return _err(exc)


@bp.route("/agent/stop", methods=["POST"])
def api_agent_stop():
    """请求停止当前正在运行的 Agent 分析。"""
    try:
        from agent.orchestrator import stop_agent_analysis
        stop_agent_analysis()
        return _ok({"stopped": True})
    except Exception as exc:
        return _err(exc)


@bp.route("/info_brief/run", methods=["POST"])
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


@bp.route("/info_brief/latest")
def api_info_brief_latest():
    """最近一次信息情报简报。"""
    try:
        from db.storage import get_agent_summary_history
        rows = get_agent_summary_history(limit=20, run_type="info_brief")
        if not rows:
            return _ok({"row": None})
        return _ok({"row": rows[0]})
    except Exception as exc:
        return _err(exc)


@bp.route("/info_brief/history")
def api_info_brief_history():
    """信息情报简报历史列表。"""
    try:
        from db.storage import get_agent_summary_history
        limit = int(request.args.get("limit", 20))
        rows = get_agent_summary_history(limit=limit, run_type="info_brief")
        return _ok({"rows": rows})
    except Exception as exc:
        return _err(exc)


@bp.route("/strategist/run", methods=["POST"])
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


@bp.route("/strategist/latest")
def api_strategist_latest():
    """最近一次战略推理。"""
    try:
        from db.storage import get_agent_summary_history
        rows = get_agent_summary_history(limit=1, run_type="strategist")
        if not rows:
            return _ok({"row": None})
        return _ok({"row": rows[0]})
    except Exception as exc:
        return _err(exc)


@bp.route("/strategist/history")
def api_strategist_history():
    """战略推理历史列表。"""
    try:
        from db.storage import get_agent_summary_history
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

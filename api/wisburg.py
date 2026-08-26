"""api/wisburg.py — 智堡投研域 Blueprint (自 server.py 拆出)。

6 个路由: 资源元数据/列表/详情 + AI 分析/简报 (后台任务, 前端轮询 ai-job)。
数据源: quant.dm_kun._wisburg 直连智堡开放 API (Bearer WISBURG_API_KEY);
AI: agent.wisburg_ai 调 claude CLI (分析/整理/总结/预测)。
"""
import json
import logging
import threading
from datetime import datetime

from flask import Blueprint, request

from api.common import _err, _ok

logger = logging.getLogger(__name__)

bp = Blueprint("wisburg", __name__, url_prefix="/api/wisburg")

_WISBURG_AI_JOB = {
    "state": "idle",        # idle | running | done | error
    "type": None,           # analyze | briefing
    "result": None,         # analyze: {title,datetime,markdown}; briefing: {markdown,count}
    "error": None,
    "started_at": None,
    "finished_at": None,
}
_wisburg_ai_lock = threading.Lock()


def _archive_result(kind: str, resource: str, result: dict) -> None:
    """AI 分析结果落 agent_summary 存档 (历史回看)。失败只告警不影响主流程。

    run_type: wisburg_analyze (单篇) / wisburg_briefing (日报)。
    2026-08-26 补: 此前智堡分析只存内存 _WISBURG_AI_JOB, 重启/切页即丢,
    无任何存档 —— 这是用户问"为什么没有记录存档"的根因。
    """
    try:
        from agent.wisburg_ai import RESOURCE_META
        from db.storage import insert_agent_summary
        md = result.get("markdown") or ""
        if not md or md.startswith("⚠️"):
            return  # 失败结果不存档
        if kind == "analyze":
            run_type = "wisburg_analyze"
            label = RESOURCE_META.get(resource, {}).get("label", resource)
            title = result.get("title") or "单篇分析"
            content = f"[{label}] {title}"[:500]
            snapshot = {
                "run_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "kind": "analyze", "resource": resource,
                "title": result.get("title"), "datetime": result.get("datetime"),
                "analysis_md": md, "ok": True,
            }
        else:
            run_type = "wisburg_briefing"
            if resource == "all":
                label = "综合 10 类日报"
            else:
                label = f"{RESOURCE_META.get(resource, {}).get('label', resource)}日报"
            content = f"智堡AI日报·{label}"[:500]
            snapshot = {
                "run_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "kind": "briefing", "resource": resource,
                "count": result.get("count"),
                "per_source": result.get("per_source"),
                "analysis_md": md, "ok": True,
            }
        insert_agent_summary(
            content=content,
            data_snapshot_json=json.dumps(snapshot, ensure_ascii=False, default=str),
            run_type=run_type,
            report_html="",
        )
        logger.info("[wisburg-ai] %s 结果已存档 (run_type=%s)", kind, run_type)
    except Exception as exc:
        logger.warning("[wisburg-ai] 存档失败 (不影响本次展示): %s", exc)


def _wisburg_ai_worker(kind: str, resource: str, **kwargs) -> None:
    """后台跑 AI 分析 (claude 串行 1-3 分钟, 不能阻塞 HTTP)。"""
    from agent.wisburg_ai import analyze_item, build_briefing, build_briefing_all, list_resource
    job = _WISBURG_AI_JOB
    try:
        if kind == "analyze":
            job["result"] = analyze_item(resource, kwargs["item_id"])
        elif resource == "all":
            job["result"] = build_briefing_all()
        else:
            items, _ = list_resource(resource, first=kwargs.get("first", 50),
                                     query=kwargs.get("query"))
            job["result"] = {"markdown": build_briefing(resource, items),
                             "count": len(items), "resource": resource}
        job["state"] = "done"
        _archive_result(kind, resource, job["result"] or {})
    except Exception as exc:
        job["state"] = "error"
        job["error"] = str(exc)
        logger.exception("[wisburg-ai] %s 分析失败", kind)
    finally:
        job["finished_at"] = datetime.now().isoformat(timespec="seconds")


@bp.route("/meta")
def api_wisburg_meta():
    try:
        from agent.wisburg_ai import resources_meta
        return _ok({"resources": resources_meta()})
    except Exception as exc:
        return _err(exc)


@bp.route("/list")
def api_wisburg_list():
    try:
        from agent.wisburg_ai import list_resource
        resource = request.args.get("resource", "feed").strip()
        first = int(request.args.get("first", 20))
        query = request.args.get("query", "").strip() or None
        after = request.args.get("after", "").strip() or None
        items, cursor = list_resource(resource, first=first, query=query, after=after)
        return _ok({"items": items, "after": cursor, "resource": resource})
    except Exception as exc:
        return _err(exc)


@bp.route("/detail")
def api_wisburg_detail():
    try:
        from agent.wisburg_ai import get_detail
        resource = request.args.get("resource", "").strip()
        item_id = int(request.args.get("id", 0))
        if not resource or not item_id:
            return _err("缺少 resource/id 参数", 400)
        return _ok(get_detail(resource, item_id))
    except Exception as exc:
        return _err(exc)


@bp.route("/analyze", methods=["POST"])
def api_wisburg_analyze():
    try:
        body = request.get_json(silent=True) or {}
        resource = (body.get("resource") or "").strip()
        item_id = int(body.get("id", 0))
        if not resource or not item_id:
            return _err("缺少 resource/id 参数", 400)
        with _wisburg_ai_lock:
            if _WISBURG_AI_JOB["state"] == "running":
                return _ok({"started": False, "state": "running"})
            _WISBURG_AI_JOB.update(
                state="running", type="analyze", result=None, error=None,
                started_at=datetime.now().isoformat(timespec="seconds"), finished_at=None,
            )
            threading.Thread(target=_wisburg_ai_worker, args=("analyze", resource),
                             kwargs={"item_id": item_id}, daemon=True,
                             name="wisburg-analyze").start()
        return _ok({"started": True, "state": "running"})
    except Exception as exc:
        return _err(exc)


@bp.route("/briefing", methods=["POST"])
def api_wisburg_briefing():
    try:
        body = request.get_json(silent=True) or {}
        resource = (body.get("resource") or "feed").strip()
        first = int(body.get("first", 50))
        query = (body.get("query") or "").strip() or None
        with _wisburg_ai_lock:
            if _WISBURG_AI_JOB["state"] == "running":
                return _ok({"started": False, "state": "running"})
            _WISBURG_AI_JOB.update(
                state="running", type="briefing", result=None, error=None,
                started_at=datetime.now().isoformat(timespec="seconds"), finished_at=None,
            )
            threading.Thread(target=_wisburg_ai_worker, args=("briefing", resource),
                             kwargs={"first": first, "query": query}, daemon=True,
                             name="wisburg-briefing").start()
        return _ok({"started": True, "state": "running"})
    except Exception as exc:
        return _err(exc)


@bp.route("/ai-job")
def api_wisburg_ai_job():
    with _wisburg_ai_lock:
        return _ok(dict(_WISBURG_AI_JOB))


@bp.route("/history")
def api_wisburg_history():
    """智堡 AI 分析存档列表 (run_type in wisburg_analyze/wisburg_briefing)。"""
    try:
        from db.storage import get_agent_summary_history
        rows = get_agent_summary_history(limit=50, run_type="wisburg_analyze")
        rows += get_agent_summary_history(limit=50, run_type="wisburg_briefing")
        rows.sort(key=lambda r: r.get("summary_time") or "", reverse=True)
        return _ok(rows[:50])
    except Exception as exc:
        return _err(exc)

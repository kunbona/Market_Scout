"""api/wisburg.py — 智堡投研域 Blueprint (自 server.py 拆出)。

6 个路由: 资源元数据/列表/详情 + AI 分析/简报 (后台任务, 前端轮询 ai-job)。
数据源: quant.dm_kun._wisburg 直连智堡开放 API (Bearer WISBURG_API_KEY);
AI: agent.wisburg_ai 调 claude CLI (分析/整理/总结/预测)。
"""
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

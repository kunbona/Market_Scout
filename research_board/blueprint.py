"""
research_board — Flask Blueprint
可插拔挂载到任何 Flask app：
    from research_board.blueprint import rb_bp
    app.register_blueprint(rb_bp)

所有 API 前缀：/api/rb/
"""

import json
import logging
import os
import threading

from flask import Blueprint, jsonify, request

from research_board.rb_storage import (
    init_db,
    create_project,
    get_project,
    list_projects,
    update_project_status,
    delete_project,
    get_rb_reports,
    get_rb_result,
    get_rb_analyses,
)
from research_board.rb_fetcher import (
    fetch_reports_for_project,
    download_pdfs_for_project,
)
from research_board.rb_analyzer import (
    get_progress,
    regenerate_single_tab,
    run_analysis,
)

logger = logging.getLogger(__name__)
rb_bp = Blueprint("research_board", __name__)

# 初始化数据库（Blueprint 注册时自动执行）
init_db()

# 默认分析维度（模仿博主结构）
# 产业全景由 Claude 生成总览 tab，不需要单独列为维度
DEFAULT_DIMENSIONS = [
    "成本构成与降本路径",   # BOM 拆解、各模块成本占比、降本曲线
    "竞争格局与核心标的",   # 龙头市占率、壁垒评分、国产化率
    "替代风险分析",         # 技术路线对比、可替代性评分、替代节点预测
    "估值与盈利预测",       # PE/PB/目标价、EPS预测、整机价格目标
    "产业里程碑与催化剂",   # 关键时间节点、出货量拐点、政策/客户催化
]

# 抓取任务状态（per project_id）
_fetch_state: dict[int, dict] = {}
_fetch_lock = threading.Lock()

# 单 Tab 重新生成任务状态（per project_id）
_regen_state: dict[int, dict] = {}
_regen_lock = threading.Lock()

# 分析任务去重锁（防止并发双启动）
_analyze_running: set[int] = set()
_analyze_lock = threading.Lock()


def _ok(data=None, **kwargs):
    return jsonify({"success": True, "data": data, **kwargs})


def _err(msg: str, code: int = 400):
    return jsonify({"success": False, "error": msg}), code


# ── 项目 CRUD ──────────────────────────────────────────────────────────────

@rb_bp.route("/api/rb/projects", methods=["GET"])
def rb_list_projects():
    return _ok(list_projects())


@rb_bp.route("/api/rb/projects", methods=["POST"])
def rb_create_project():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return _err("name 不能为空")

    keywords = body.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",") if k.strip()]

    dimensions = body.get("dimensions") or DEFAULT_DIMENSIONS
    days_back = int(body.get("days_back") or 180)
    qtype_filter = body.get("qtype_filter") or [0, 1, 2, 3]

    pid = create_project(name, keywords, dimensions, days_back, qtype_filter)
    return _ok(get_project(pid))


@rb_bp.route("/api/rb/projects/<int:pid>", methods=["GET"])
def rb_get_project(pid: int):
    p = get_project(pid)
    if not p:
        return _err("项目不存在", 404)
    return _ok(p)


@rb_bp.route("/api/rb/projects/<int:pid>", methods=["DELETE"])
def rb_delete_project(pid: int):
    delete_project(pid)
    return _ok({"deleted": pid})


# ── 抓取研报 ──────────────────────────────────────────────────────────────

@rb_bp.route("/api/rb/projects/<int:pid>/fetch", methods=["POST"])
def rb_fetch(pid: int):
    p = get_project(pid)
    if not p:
        return _err("项目不存在", 404)

    with _fetch_lock:
        state = _fetch_state.get(pid, {})
        if state.get("status") == "running":
            return _err("抓取任务已在运行中")

        _fetch_state[pid] = {"status": "running", "phase": "fetching", "message": ""}

    body = request.get_json(silent=True) or {}
    download_pdf = bool(body.get("download_pdf", False))
    max_pdf = int(body.get("max_pdf", 100))

    def _run():
        msgs = []

        def _cb(msg: str):
            msgs.append(msg)
            with _fetch_lock:
                _fetch_state[pid]["message"] = msg

        try:
            update_project_status(pid, "fetching")
            fetch_reports_for_project(pid, progress_cb=_cb)

            if download_pdf:
                with _fetch_lock:
                    _fetch_state[pid]["phase"] = "downloading_pdf"
                download_pdfs_for_project(pid, max_count=max_pdf, progress_cb=_cb)

            # 用数据库实际行数（而非本次新增数）更新 report_count，避免重复抓时归零
            actual_count = len(get_rb_reports(pid))
            update_project_status(pid, "idle", actual_count)
            with _fetch_lock:
                _fetch_state[pid] = {"status": "done", "phase": "done",
                                      "message": f"抓取完成，共 {actual_count} 篇"}
        except Exception as e:
            logger.exception(f"[rb_fetch] pid={pid} error: {e}")
            update_project_status(pid, "error")
            with _fetch_lock:
                _fetch_state[pid] = {"status": "error", "phase": "error", "message": str(e)}

    threading.Thread(target=_run, daemon=True, name=f"rb-fetch-{pid}").start()
    return _ok({"started": True})


@rb_bp.route("/api/rb/projects/<int:pid>/fetch-status", methods=["GET"])
def rb_fetch_status(pid: int):
    with _fetch_lock:
        state = dict(_fetch_state.get(pid, {"status": "idle"}))
    return _ok(state)


# ── 下载 PDF（独立触发）────────────────────────────────────────────────────

@rb_bp.route("/api/rb/projects/<int:pid>/download-pdf", methods=["POST"])
def rb_download_pdf(pid: int):
    p = get_project(pid)
    if not p:
        return _err("项目不存在", 404)

    body = request.get_json(silent=True) or {}
    max_count = int(body.get("max_count", 100))

    with _fetch_lock:
        state = _fetch_state.get(pid, {})
        if state.get("status") == "running":
            return _err("已有任务在运行")
        _fetch_state[pid] = {"status": "running", "phase": "downloading_pdf", "message": ""}

    def _run():
        def _cb(msg: str):
            with _fetch_lock:
                _fetch_state[pid]["message"] = msg

        try:
            done = download_pdfs_for_project(pid, max_count=max_count, progress_cb=_cb)
            update_project_status(pid, "idle")
            with _fetch_lock:
                _fetch_state[pid] = {"status": "done", "phase": "done",
                                      "message": f"PDF 提取完成，成功 {done} 篇"}
        except Exception as e:
            logger.exception(f"[rb_download_pdf] pid={pid} error: {e}")
            update_project_status(pid, "error")
            with _fetch_lock:
                _fetch_state[pid] = {"status": "error", "phase": "error", "message": str(e)}

    threading.Thread(target=_run, daemon=True, name=f"rb-pdf-{pid}").start()
    return _ok({"started": True})


# ── 分析 ─────────────────────────────────────────────────────────────────

@rb_bp.route("/api/rb/projects/<int:pid>/analyze", methods=["POST"])
def rb_analyze(pid: int):
    p = get_project(pid)
    if not p:
        return _err("项目不存在", 404)

    with _analyze_lock:
        if pid in _analyze_running:
            return _err("分析任务已在运行中")
        _analyze_running.add(pid)

    def _wrapped():
        try:
            run_analysis(pid)
        finally:
            with _analyze_lock:
                _analyze_running.discard(pid)

    import threading as _t
    _t.Thread(target=_wrapped, daemon=True, name=f"rb-analyzer-{pid}").start()
    return _ok({"started": True})


@rb_bp.route("/api/rb/projects/<int:pid>/analyze-status", methods=["GET"])
def rb_analyze_status(pid: int):
    return _ok(get_progress(pid))


# ── 结果 ──────────────────────────────────────────────────────────────────

@rb_bp.route("/api/rb/projects/<int:pid>/result", methods=["GET"])
def rb_result(pid: int):
    result = get_rb_result(pid)
    if not result:
        return _err("尚无分析结果", 404)
    raw = result.get("summary_json", "{}")
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = {}
    # HTML 在写入 DB 前已经过 clean_html()，这里直接返回
    result["data"] = parsed
    return _ok(result)


@rb_bp.route("/api/rb/projects/<int:pid>/reports", methods=["GET"])
def rb_reports(pid: int):
    status_filter = request.args.get("status")
    rows = get_rb_reports(pid, pdf_status=status_filter)
    return _ok(rows)


@rb_bp.route("/api/rb/projects/<int:pid>/analyses", methods=["GET"])
def rb_analyses(pid: int):
    rows = get_rb_analyses(pid)
    # 解析 result_json 字段
    for r in rows:
        if r.get("result_json"):
            try:
                r["result"] = json.loads(r["result_json"])
            except Exception:
                r["result"] = {}
    return _ok(rows)


# ── 单 Tab 重新生成 ───────────────────────────────────────────────────────────

@rb_bp.route("/api/rb/projects/<int:pid>/tabs/regenerate", methods=["POST"])
def rb_tab_regenerate(pid: int):
    p = get_project(pid)
    if not p:
        return _err("项目不存在", 404)

    body = request.get_json(silent=True) or {}
    tab_name = (body.get("tab_name") or "").strip()
    instruction = (body.get("instruction") or "").strip()

    if not tab_name:
        return _err("tab_name 不能为空")
    if not instruction:
        return _err("instruction 不能为空")
    if len(instruction) > 2000:
        return _err("instruction 不得超过 2000 字符")

    with _regen_lock:
        state = _regen_state.get(pid, {})
        if state.get("status") == "running":
            return _err(f"已有重生成任务在运行中（tab: {state.get('tab_name', '?')}）")
        _regen_state[pid] = {
            "status": "running",
            "tab_name": tab_name,
            "message": "任务已启动",
        }

    def _run():
        def _cb(msg: str):
            with _regen_lock:
                _regen_state[pid]["message"] = msg

        try:
            regenerate_single_tab(pid, tab_name, instruction, progress_cb=_cb)
            with _regen_lock:
                _regen_state[pid] = {
                    "status": "done",
                    "tab_name": tab_name,
                    "message": f"【{tab_name}】重新生成完成",
                }
        except Exception as e:
            logger.exception(f"[rb_tab_regenerate] pid={pid} tab={tab_name} error: {e}")
            with _regen_lock:
                _regen_state[pid] = {
                    "status": "error",
                    "tab_name": tab_name,
                    "message": str(e),
                }

    threading.Thread(
        target=_run, daemon=True, name=f"rb-regen-{pid}"
    ).start()
    return _ok({"started": True})


@rb_bp.route("/api/rb/projects/<int:pid>/tabs/regen-status", methods=["GET"])
def rb_tab_regen_status(pid: int):
    with _regen_lock:
        state = dict(_regen_state.get(pid, {
            "status": "idle",
            "tab_name": "",
            "message": "",
        }))
    return _ok(state)

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
    import_rb_reports,
    upsert_rb_result,
    upsert_rb_analysis,
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

    threading.Thread(target=_wrapped, daemon=True, name=f"rb-analyzer-{pid}").start()
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


# ── 导出 / 导入 ───────────────────────────────────────────────────────────────

@rb_bp.route("/api/rb/projects/<int:pid>/export", methods=["GET"])
def rb_export(pid: int):
    """把项目打包为 ZIP（project.json + reports.json + result.json + analyses.json）。"""
    import io
    import zipfile
    from datetime import datetime as _dt
    from flask import send_file

    project = get_project(pid)
    if not project:
        return _err(f"项目 {pid} 不存在", 404)

    reports = get_rb_reports(pid)
    result_row = get_rb_result(pid)
    analyses = get_rb_analyses(pid)

    date_str = _dt.now().strftime("%Y%m%d")
    safe_name = project["name"].replace("/", "_").replace(" ", "_")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("meta.json", json.dumps({
            "version": 1,
            "exported_at": _dt.now().isoformat(),
            "source_project_id": pid,
            "project_name": project["name"],
        }, ensure_ascii=False))

        zf.writestr("project.json", json.dumps(project, ensure_ascii=False, default=str))
        zf.writestr("reports.json", json.dumps(reports, ensure_ascii=False, default=str))

        if result_row:
            # summary_json 已是字符串，直接写入（保留完整 HTML）
            try:
                result_obj = json.loads(result_row["summary_json"])
            except Exception:
                result_obj = {}
            zf.writestr("result.json", json.dumps(result_obj, ensure_ascii=False))

        if analyses:
            zf.writestr("analyses.json", json.dumps(analyses, ensure_ascii=False, default=str))

    buf.seek(0)
    from urllib.parse import quote as _quote
    filename = f"rb_{safe_name}_{date_str}.zip"
    encoded = _quote(filename, safe='')
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=False,  # 不让 Flask 自己写 Content-Disposition
    ), 200, {"Content-Disposition": f"attachment; filename=\"export.zip\"; filename*=UTF-8''{encoded}"}


@rb_bp.route("/api/rb/projects/import", methods=["POST"])
def rb_import():
    """从 ZIP 文件导入项目，总是新建（不覆盖同名项目）。"""
    import io
    import zipfile

    if "file" not in request.files:
        return _err("缺少 file 字段")
    f = request.files["file"]
    if not f.filename or not f.filename.endswith(".zip"):
        return _err("请上传 .zip 文件")

    try:
        buf = io.BytesIO(f.read())
        zf = zipfile.ZipFile(buf, "r")
    except Exception as e:
        return _err(f"ZIP 解析失败: {e}")

    names = zf.namelist()

    # 校验 meta
    if "meta.json" not in names:
        return _err("ZIP 格式不正确（缺少 meta.json）")
    try:
        meta = json.loads(zf.read("meta.json"))
        if meta.get("version") != 1:
            return _err(f"不支持的导出版本：{meta.get('version')}")
    except Exception as e:
        return _err(f"meta.json 解析失败: {e}")

    # 读 project
    if "project.json" not in names:
        return _err("ZIP 格式不正确（缺少 project.json）")
    try:
        proj = json.loads(zf.read("project.json"))
    except Exception as e:
        return _err(f"project.json 解析失败: {e}")

    # 新建项目（总是新建）
    new_pid = create_project(
        name=proj["name"],
        keywords=proj.get("keywords", []),
        dimensions=proj.get("dimensions", []),
        days_back=proj.get("days_back", 180),
        qtype_filter=proj.get("qtype_filter", [0, 1, 2, 3]),
    )

    # 导入研报
    report_count = 0
    if "reports.json" in names:
        try:
            reports = json.loads(zf.read("reports.json"))
            report_count = import_rb_reports(new_pid, reports)
        except Exception as e:
            logger.warning(f"[rb_import] reports.json 导入失败: {e}")

    # 导入分析结果
    has_result = False
    if "result.json" in names:
        try:
            result_obj = json.loads(zf.read("result.json"))
            upsert_rb_result(new_pid, json.dumps(result_obj, ensure_ascii=False))
            has_result = True
        except Exception as e:
            logger.warning(f"[rb_import] result.json 导入失败: {e}")

    # 导入各维度分析记录
    if "analyses.json" in names:
        try:
            analyses = json.loads(zf.read("analyses.json"))
            for a in analyses:
                upsert_rb_analysis(
                    new_pid,
                    a.get("dimension", ""),
                    a.get("batch_index", 0),
                    a.get("result_json", "{}"),
                    a.get("status", "done"),
                    a.get("error_msg", ""),
                )
        except Exception as e:
            logger.warning(f"[rb_import] analyses.json 导入失败: {e}")

    # 推断项目状态
    status = "done" if has_result else ("idle" if report_count > 0 else "idle")
    update_project_status(new_pid, status, report_count if report_count > 0 else None)

    return _ok({"project_id": new_pid, "project_name": proj["name"], "report_count": report_count})


@rb_bp.route("/api/rb/projects/<int:pid>/export-html", methods=["GET"])
def rb_export_html(pid: int):
    """把所有 tab 打包为单个自包含 HTML 文件（离线浏览）。"""
    import html as _html_mod
    from datetime import datetime as _dt
    from flask import Response

    project = get_project(pid)
    if not project:
        return _err(f"项目 {pid} 不存在", 404)

    result_row = get_rb_result(pid)
    if not result_row:
        return _err("该项目尚无分析结果")

    try:
        summary = json.loads(result_row["summary_json"])
    except Exception:
        return _err("分析结果解析失败")

    tabs = summary.get("tabs", [])
    if not tabs:
        return _err("分析结果中没有 tab 内容")

    project_name = project["name"]
    report_count = summary.get("report_count", 0)
    generated_at = summary.get("generated_at", "")

    # 构建 tab 按钮列表和 iframe srcdoc 列表
    tab_buttons = []
    tab_iframes = []
    for i, tab in enumerate(tabs):
        name = tab.get("name", f"Tab {i+1}")
        tab_html = tab.get("html", "<p>内容为空</p>")
        escaped = _html_mod.escape(tab_html, quote=True)
        active = "active" if i == 0 else ""
        tab_buttons.append(
            f'<button class="tab-btn {active}" onclick="showTab({i})">{_html_mod.escape(name)}</button>'
        )
        display = "block" if i == 0 else "none"
        tab_iframes.append(
            f'<iframe id="tab-frame-{i}" class="tab-frame" style="display:{display}" '
            f'srcdoc="{escaped}" frameborder="0" scrolling="auto"></iframe>'
        )

    wrapper_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_html_mod.escape(project_name)} — 研报分析</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'PingFang SC', sans-serif;
         background: #f5f5f7; height: 100vh; display: flex; flex-direction: column; }}
  .header {{ background: #fff; border-bottom: 1px solid #e5e7eb; padding: 12px 20px;
             display: flex; align-items: center; gap: 16px; flex-shrink: 0; }}
  .header-title {{ font-size: 15px; font-weight: 600; color: #111827; }}
  .header-meta {{ font-size: 12px; color: #6b7280; }}
  .tab-bar {{ background: #fff; border-bottom: 1px solid #e5e7eb;
              padding: 0 16px; display: flex; gap: 2px; flex-shrink: 0;
              overflow-x: auto; white-space: nowrap; }}
  .tab-btn {{ padding: 10px 14px; font-size: 13px; border: none; background: none;
              cursor: pointer; color: #6b7280; border-bottom: 2px solid transparent;
              transition: all 0.15s; white-space: nowrap; }}
  .tab-btn:hover {{ color: #374151; }}
  .tab-btn.active {{ color: #6d28d9; border-bottom-color: #6d28d9; font-weight: 500; }}
  .tab-content {{ flex: 1; overflow: hidden; }}
  .tab-frame {{ width: 100%; height: 100%; border: none; }}
</style>
</head>
<body>
<div class="header">
  <span class="header-title">{_html_mod.escape(project_name)}</span>
  <span class="header-meta">{report_count} 篇研报 · 生成于 {_html_mod.escape(generated_at)}</span>
</div>
<div class="tab-bar">
  {''.join(tab_buttons)}
</div>
<div class="tab-content">
  {''.join(tab_iframes)}
</div>
<script>
function showTab(idx) {{
  document.querySelectorAll('.tab-btn').forEach(function(b, i) {{
    b.classList.toggle('active', i === idx);
  }});
  document.querySelectorAll('.tab-frame').forEach(function(f, i) {{
    f.style.display = i === idx ? 'block' : 'none';
    if (i === idx) {{
      // 通知 iframe 内 ECharts resize
      try {{ f.contentWindow.postMessage({{type:'tab-shown'}}, '*'); }} catch(e) {{}}
    }}
  }});
}}
</script>
</body>
</html>"""

    from urllib.parse import quote as _quote
    date_str = _dt.now().strftime("%Y%m%d")
    safe_name = project_name.replace("/", "_").replace(" ", "_")
    filename = f"rb_{safe_name}_{date_str}.html"
    encoded = _quote(filename, safe='')

    return Response(
        wrapper_html,
        mimetype="text/html; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=\"export.html\"; filename*=UTF-8''{encoded}"},
    )

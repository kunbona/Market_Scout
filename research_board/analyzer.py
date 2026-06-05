"""
research_board — 分析 pipeline 编排（CC 角色）
流程：
  1. 获取研报文字批次
  2. 并发启动多路 Kimi，每路负责一个维度的分析
  3. 多批次时做 Kimi 合并
  4. 调用最终汇总 Kimi 生成看板 JSON
  5. 写入 rb_result
"""

import json
import logging
import os
import threading
from datetime import datetime

from research_board.rb_fetcher import get_reports_text_batches
from research_board.rb_storage import (
    get_project,
    get_rb_analyses,
    get_rb_reports,
    update_project_status,
    upsert_rb_analysis,
    upsert_rb_result,
)
from research_board.cli_runner import (
    run_dimension_analysis,
    run_merge_analysis,
    _call_llm,
    _extract_json,
)

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")


def _load_template(name: str) -> str:
    path = os.path.join(_TEMPLATES_DIR, name)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# 全局进度状态（按 project_id）
_progress: dict[int, dict] = {}
_progress_lock = threading.Lock()


def get_progress(project_id: int) -> dict:
    with _progress_lock:
        return dict(_progress.get(project_id, {
            "status": "idle",
            "phase": "",
            "message": "",
            "done_dimensions": [],
            "total_dimensions": 0,
        }))


def _set_progress(project_id: int, **kwargs):
    with _progress_lock:
        if project_id not in _progress:
            _progress[project_id] = {}
        _progress[project_id].update(kwargs)


def run_analysis(project_id: int) -> None:
    """
    完整分析 pipeline。在后台线程中调用。
    """
    project = get_project(project_id)
    if not project:
        logger.error(f"[analyzer] project {project_id} not found")
        return

    project_name: str = project["name"]
    dimensions: list[str] = project["dimensions"]

    def _log(msg: str):
        logger.info(msg)
        _set_progress(project_id, message=msg)

    try:
        update_project_status(project_id, "analyzing")
        _set_progress(project_id,
                      status="analyzing",
                      phase="准备数据",
                      total_dimensions=len(dimensions),
                      done_dimensions=[])

        # ── Phase 1: 准备研报文字批次 ──────────────────────────────────
        _log(f"[analyzer] 正在分批处理研报文字...")
        batches = get_reports_text_batches(project_id)
        report_count = len(get_rb_reports(project_id))

        if not batches:
            _log("[analyzer] 没有可分析的研报文字，请先下载 PDF 或检查元数据")
            update_project_status(project_id, "error")
            _set_progress(project_id, status="error", message="没有研报文字可分析")
            return

        _log(f"[analyzer] 共 {report_count} 篇研报，分 {len(batches)} 批，开始分析 {len(dimensions)} 个维度")

        # ── Phase 2: 每个维度并发 Kimi 分析（维度间串行，批次间并发） ──
        dim_template = _load_template("dimension_analysis.txt")
        merge_template = _load_template("dimension_merge.txt")

        dimension_results: dict[str, dict] = {}

        for dim in dimensions:
            _set_progress(project_id, phase=f"分析维度: {dim}")
            _log(f"[analyzer] 开始分析维度: {dim}")

            try:
                batch_results = run_dimension_analysis(
                    project_name=project_name,
                    dimension=dim,
                    dimension_prompt_template=dim_template,
                    report_batches=batches,
                    progress_cb=_log,
                )

                # 多批次时合并
                if len(batch_results) > 1:
                    _log(f"[analyzer] {dim}: 合并 {len(batch_results)} 批次")
                    merged = run_merge_analysis(
                        project_name=project_name,
                        dimension=dim,
                        batch_results=batch_results,
                        merge_prompt_template=merge_template,
                        progress_cb=_log,
                    )
                else:
                    merged = batch_results[0] if batch_results else {}

                dimension_results[dim] = merged

                upsert_rb_analysis(
                    project_id, dim, 0,
                    json.dumps(merged, ensure_ascii=False),
                    "done"
                )

                with _progress_lock:
                    done = _progress.get(project_id, {}).get("done_dimensions", [])
                    _progress[project_id]["done_dimensions"] = done + [dim]

            except Exception as e:
                _log(f"[analyzer] 维度 {dim} 失败: {e}")
                upsert_rb_analysis(project_id, dim, 0, "{}", "failed", str(e))
                dimension_results[dim] = {"error": str(e)}

        # ── Phase 3: 最终汇总 ──────────────────────────────────────────
        _set_progress(project_id, phase="生成最终看板")
        _log("[analyzer] 生成最终汇总看板...")

        final_template = _load_template("final_summary.txt")
        all_dims_json = json.dumps(dimension_results, ensure_ascii=False, indent=2)
        final_prompt = final_template.format(
            project_name=project_name,
            all_dimensions_json=all_dims_json,
        )

        final_raw = _call_llm(final_prompt)
        final_json = _extract_json(final_raw)

        # 注入元信息
        if isinstance(final_json, dict):
            final_json["project_name"] = project_name
            final_json["report_count"] = report_count
            final_json["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            final_json["dimensions"] = dimension_results

        upsert_rb_result(project_id, json.dumps(final_json, ensure_ascii=False))
        update_project_status(project_id, "done", report_count)
        _set_progress(project_id, status="done", phase="完成", message="分析完成")
        _log(f"[analyzer] 项目 {project_id} 分析完成")

    except Exception as e:
        logger.exception(f"[analyzer] 项目 {project_id} 分析异常: {e}")
        update_project_status(project_id, "error")
        _set_progress(project_id, status="error", message=str(e))


def start_analysis_thread(project_id: int) -> threading.Thread:
    """在后台线程启动分析，立即返回。"""
    t = threading.Thread(
        target=run_analysis,
        args=(project_id,),
        daemon=True,
        name=f"rb-analyzer-{project_id}",
    )
    t.start()
    return t

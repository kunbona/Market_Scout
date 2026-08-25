"""api/wecom.py — 企微推送域 Blueprint (自 server.py 拆出)。

全局企微推送 service (所有 AI 智能分析共用): 列地址、推文本、推 markdown、
推 AI 报告、推指定 agent 报告行。底层全走 fetcher.wecom_notifier (懒导入),
WECOM_DRY_RUN 沙盒模式下只 print 不真发。

注意: /api/agent/report/<row_id>/push 是 agent 路径前缀下的推送入口,
挂在本域 (逻辑是企微推送), 但 url 不在 /api/wecom/ 下, 故 url_prefix="/api"。
"""
import logging

from flask import Blueprint, request

from api.common import _err, _ok

logger = logging.getLogger(__name__)

bp = Blueprint("wecom", __name__, url_prefix="/api")


@bp.route("/wecom/addresses")
def api_wecom_addresses():
    """列出所有可用企微推送地址 (从 config.WECOM_ROBOT_KEYS + 环境变量)。"""
    try:
        from fetcher.wecom_notifier import list_addresses, is_dry_run
        return _ok({
            "addresses": list_addresses(),
            "dry_run": is_dry_run(),
        })
    except Exception as exc:
        return _err(exc)


@bp.route("/wecom/send-text", methods=["POST"])
def api_wecom_send_text():
    """推任意文本。

    Body: {"content": "..."[, "address": "info"]}
    """
    try:
        from fetcher.wecom_notifier import send_text
        body = request.get_json(silent=True) or {}
        content = body.get("content", "").strip()
        address = body.get("address") or request.args.get("address") or "info"
        if not content:
            return _err("content 不能为空", 400)
        ok = send_text(content, address=address)
        return _ok({"address": address, "ok": ok})
    except Exception as exc:
        return _err(exc)


@bp.route("/wecom/send-markdown", methods=["POST"])
def api_wecom_send_markdown():
    """推 markdown 卡片 (企微原生支持, 长度 ≤ 4096 字节)。

    Body: {"content": "# 标题\\n**加粗**..."[, "address": "info"]}
    """
    try:
        from fetcher.wecom_notifier import send_markdown
        body = request.get_json(silent=True) or {}
        content = body.get("content", "").strip()
        address = body.get("address") or request.args.get("address") or "info"
        if not content:
            return _err("content 不能为空", 400)
        ok = send_markdown(content, address=address)
        return _ok({"address": address, "ok": ok})
    except Exception as exc:
        return _err(exc)


@bp.route("/wecom/send-ai-report", methods=["POST"])
def api_wecom_send_ai_report():
    """推 AI 报告 (markdown 卡片: 标题 + 摘要 + 可选完整内容)。

    Body: {
        "title": "📊 盘后首席策略",
        "summary": "**情绪**: 🟡 中性\\n**操作**: 减仓",
        "full_text": "可选, 完整报告会被截断到 markdown 长度上限",
        "address": "info" (默认)
    }
    """
    try:
        from fetcher.wecom_notifier import send_ai_report
        body = request.get_json(silent=True) or {}
        title = body.get("title", "").strip()
        summary = body.get("summary", "").strip()
        full_text = body.get("full_text")
        address = body.get("address") or request.args.get("address") or "info"
        if not title or not summary:
            return _err("title 和 summary 必填", 400)
        ok = send_ai_report(
            title=title, summary=summary,
            full_text=full_text, address=address,
        )
        return _ok({"address": address, "ok": ok})
    except Exception as exc:
        return _err(exc)


@bp.route("/agent/report/<int:row_id>/push", methods=["POST"])
def api_agent_report_push(row_id: int):
    """把指定 agent 报告推到企微。

    从 DB 读 report row, 取 content / report_html 推 markdown 卡片。
    Body: {"address": "info" (默认), "include_full": false (默认不推完整内容)}
    """
    try:
        from fetcher.wecom_notifier import send_ai_report, is_dry_run, _strip_html
        from db.storage import get_agent_summary_by_id
        import json as _json

        body = request.get_json(silent=True) or {}
        address = body.get("address") or request.args.get("address") or "info"
        include_full = bool(body.get("include_full", False))

        row = get_agent_summary_by_id(row_id)
        if not row:
            return _err(f"找不到报告 id={row_id}", 404)

        run_type = row.get("run_type", "?")
        summary_time = row.get("summary_time", "")

        # 优先用 data_snapshot_json 里的 summary_text, fallback 到 content
        snap = row.get("data_snapshot_json")
        summary = ""
        # full_text 默认用 report_html 剥 HTML (比 content 字段丰富, content 通常只是占位)
        html = row.get("report_html") or ""
        full_text = _strip_html(html) if html else row.get("content", "")
        if snap:
            try:
                d = _json.loads(snap)
                summary = d.get("summary_text") or summary
            except Exception:
                pass
        if not summary:
            summary = (row.get("content") or "")[:500]

        # 标题
        run_type_cn = {
            "morning": "盘前首席", "intraday": "盘中解说",
            "evening": "盘后首席", "policy": "政策解读",
            "research": "研报展望", "notice": "公告解读",
            "watchlist": "关注股池", "auction": "集合竞价",
            "closing": "盘后速递",
        }.get(run_type, run_type)
        title = f"🤖 {run_type_cn} · {summary_time[:16] if summary_time else ''}"

        ok = send_ai_report(
            title=title,
            summary=summary[:2000],
            full_text=full_text if include_full else None,
            address=address,
        )
        return _ok({
            "address": address,
            "row_id": row_id,
            "ok": ok,
            "dry_run": is_dry_run(),
        })
    except Exception as exc:
        return _err(exc)

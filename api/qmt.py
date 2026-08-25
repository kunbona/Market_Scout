"""api/qmt.py — QMT 实时监控域 (自 server.py 拆出)。

5 路由: qmt-breaker / qmt-overview / qmt-limit-down-monitor /
qmt-industry-draggers / qmt-industry-stats。

枢纽状态与助手函数在 core/qmt_hub.py (scheduler 共用); 本模块一律通过
`qmt_hub.xxx` 属性访问调用, 测试 mock.patch.object(qmt_hub, ...) 可拦截。

url_prefix="/api"。
"""
from flask import Blueprint, request

from api.common import _err, _ok
from core import qmt_hub
from fetcher.qmt_monitors import (
    build_qmt_industry_draggers_payload,
    build_qmt_limit_down_monitor_payload,
)

bp = Blueprint("qmt", __name__, url_prefix="/api")


@bp.route("/qmt-breaker")
def api_qmt_breaker():
    """QMT Bridge 熔断器状态（前端轻量轮询用）。"""
    try:
        from fetcher.qmt_breaker import snapshot, reset as breaker_reset
        snap = snapshot()
        # 暴露手动重置端点（前端 debug 按钮可调）
        if request.args.get("reset") == "1":
            breaker_reset()
            snap = snapshot()
        return _ok(snap)
    except Exception as exc:
        return _err(exc)


@bp.route("/qmt-overview")
def api_qmt_overview():
    try:
        status = qmt_hub._read_qmt_runtime_status()
        breadth = qmt_hub._get_qmt_overview_breadth(status)
        limit_down_rows = qmt_hub._get_qmt_limit_down_rows(status)
        snapshot = {
            "updated_at": breadth["fetch_time"] if breadth else None,
            "source": breadth["source"] if breadth else "QMT",
            "market": breadth["market"] if breadth else None,
        }
        kpis = {
            "limit_down_count": len(limit_down_rows),
            "up_count": breadth["up_count"] if breadth else None,
            "down_count": breadth["down_count"] if breadth else None,
            "flat_count": breadth["flat_count"] if breadth else None,
            "turnover": breadth["total_amount"] if breadth else None,
        }
        return _ok({
            "status": status,
            "snapshot": snapshot,
            "kpis": kpis,
        })
    except Exception as exc:
        return _err(exc)


@bp.route("/qmt-limit-down-monitor")
def api_qmt_limit_down_monitor():
    try:
        status = qmt_hub._read_qmt_runtime_status()
        breadth = qmt_hub._latest_total_market_breadth_row()
        rows = qmt_hub._get_qmt_limit_down_rows(status)
        payload = build_qmt_limit_down_monitor_payload(rows)
        payload["status"] = status
        payload["snapshot"] = {
            "updated_at": breadth["fetch_time"] if breadth else None,
            "source": breadth["source"] if breadth else "QMT",
            "market": breadth["market"] if breadth else None,
        }
        return _ok(payload)
    except Exception as exc:
        return _err(exc)


@bp.route("/qmt-industry-draggers")
def api_qmt_industry_draggers():
    try:
        status = qmt_hub._read_qmt_runtime_status()
        breadth = qmt_hub._latest_total_market_breadth_row()
        rows = qmt_hub._get_qmt_limit_down_rows(status)
        payload = build_qmt_industry_draggers_payload(rows)
        payload["status"] = status
        payload["snapshot"] = {
            "updated_at": breadth["fetch_time"] if breadth else None,
            "source": breadth["source"] if breadth else "QMT",
            "market": breadth["market"] if breadth else None,
        }
        return _ok(payload)
    except Exception as exc:
        return _err(exc)


@bp.route("/qmt-industry-stats")
def api_qmt_industry_stats():
    try:
        status = qmt_hub._read_qmt_runtime_status()
        breadth = qmt_hub._get_qmt_overview_breadth(status)
        payload = qmt_hub._get_qmt_industry_stats_payload(status)
        payload["status"] = status
        payload["snapshot"] = {
            "updated_at": breadth["fetch_time"] if breadth else None,
            "source": breadth["source"] if breadth else "xtquant",
            "market": breadth["market"] if breadth else None,
        }
        return _ok(payload)
    except Exception as exc:
        return _err(exc)

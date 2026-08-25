"""api/cycle.py — 周期信号域 Blueprint (自 server.py 拆出)。

11 个路由: 摘要/状态/指标/权益曲线/统一评分 + 图片代理 + 手动刷新/推送。
全部是 fetcher.cycle_signal / cycle_notifier 的薄包装, 无模块级状态。
"""
from flask import Blueprint, request

from api.common import _err, _ok

bp = Blueprint("cycle", __name__, url_prefix="/api/cycle")


@bp.route("/summary")
def api_cycle_summary():
    """周期信号摘要（顶/底定位、phase、advice、上次信号、freshness）。"""
    try:
        from fetcher.cycle_signal import build_cycle_summary
        return _ok(build_cycle_summary())
    except Exception as exc:
        return _err(exc)


@bp.route("/status")
def api_cycle_status():
    """数据状态：8 指标 + summary + index 是否就绪（用于顶部 banner）。"""
    try:
        from fetcher.cycle_signal import data_status
        return _ok(data_status())
    except Exception as exc:
        return _err(exc)


@bp.route("/indicators")
def api_cycle_indicators():
    """8 指标的最新值（用于顶部卡片网格，不含 series）。"""
    try:
        from fetcher.cycle_signal import build_indicator_meta
        return _ok(build_indicator_meta())
    except Exception as exc:
        return _err(exc)


@bp.route("/indicator/<key>")
def api_cycle_indicator(key: str):
    """单个指标的时间序列（用于画 Plotly 趋势图）。

    Args:
        max_points: 最大点数（默认 3000；超过则均匀降采样，保留首尾）
    """
    try:
        from fetcher.cycle_signal import build_indicator_series
        max_points = int(request.args.get("max_points", 3000))
        return _ok(build_indicator_series(key, max_points=max_points))
    except Exception as exc:
        return _err(exc)


@bp.route("/equity-curve")
def api_cycle_equity_curve():
    """上证指数 close 序列。"""
    try:
        from fetcher.cycle_signal import build_equity_curve
        max_points = int(request.args.get("max_points", 3000))
        return _ok(build_equity_curve(max_points=max_points))
    except Exception as exc:
        return _err(exc)


@bp.route("/unified-score")
def api_cycle_unified_score():
    """unified_score = 7 指标 risk_percentage 等权 mean（与 cycle_signal runner 一致）。"""
    try:
        from fetcher.cycle_signal import build_unified_score_series
        max_points = int(request.args.get("max_points", 3000))
        return _ok(build_unified_score_series(max_points=max_points))
    except Exception as exc:
        return _err(exc)


@bp.route("/pic/<name>")
def api_cycle_pic(name: str):
    """proxy data/pic/ 下的 PNG（cycle_position.png 等）。"""
    from flask import abort, send_file
    from fetcher.cycle_signal import get_pic_path
    path = get_pic_path(name)
    if not path or not path.exists():
        abort(404)
    return send_file(str(path), mimetype="image/png", max_age=300)


@bp.route("/refresh", methods=["POST"])
def api_cycle_refresh():
    """手动触发一次刷新（异步，单飞）。

    Query param:
      mode=quick (默认): 只跑 step2+3，假设 raw_data 已齐
      mode=full:         跑 step1+2+3（先 akshare 拉数据，再算指标）
      notify=0/1:        跑完成功后是否推企微 (默认 0, 不推避免打扰)
      notify_address:    推送地址 (默认 'info', 可选从 /api/wecom/addresses 看)
    """
    try:
        from fetcher.cycle_signal import trigger_cycle_refresh
        mode = request.args.get("mode", "quick")
        notify = request.args.get("notify", "0") in ("1", "true", "True")
        notify_address = request.args.get("notify_address", "info") or "info"
        return _ok(trigger_cycle_refresh(mode=mode, notify=notify, notify_address=notify_address))
    except Exception as exc:
        return _err(exc)


@bp.route("/refresh/full", methods=["POST"])
def api_cycle_refresh_full():
    """完整刷新（异步，单飞）：step1+2+3。先 akshare 拉数据，再算指标。

    Query param:
      notify=0/1: 跑完成功后是否推企微 (默认 0)
      notify_address: 推送地址 (默认 'info')

    与 /api/cycle/refresh?mode=full 等价，独立端点方便前端绑定。
    """
    try:
        from fetcher.cycle_signal import trigger_cycle_refresh
        notify = request.args.get("notify", "0") in ("1", "true", "True")
        notify_address = request.args.get("notify_address", "info") or "info"
        return _ok(trigger_cycle_refresh(mode="full", notify=notify, notify_address=notify_address))
    except Exception as exc:
        return _err(exc)


@bp.route("/refresh/status")
def api_cycle_refresh_status():
    """轮询刷新任务状态（前端每 2-3s 查一次）。"""
    try:
        from fetcher.cycle_signal import cycle_refresh_status
        return _ok(cycle_refresh_status())
    except Exception as exc:
        return _err(exc)


@bp.route("/notify", methods=["POST"])
def api_cycle_notify():
    """手动推送当前周期信号到企业微信机器人。

    不做 refresh, 直接读 market-radar/data/results/cycle_signal_latest.json
    + data/pic/cycle_position.png 推到 webhook。

    Query/Body param:
      address: 推送地址 (默认 'info', 可选从 list_addresses() 看)

    Returns:
        {configured, text_ok, image_ok, dry_run, address, skipped_reason}
    """
    try:
        from fetcher.cycle_notifier import (
            is_configured,
            push_cycle_from_disk,
        )
        body = request.get_json(silent=True) or {}
        address = body.get("address") or request.args.get("address") or "info"
        return _ok({
            "address": address,
            "configured": is_configured(address),
            **push_cycle_from_disk(address=address),
        })
    except Exception as exc:
        return _err(exc)

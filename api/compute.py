"""api/compute.py — 数据健康检查 + 手动重算域 (自 server.py 拆出)。

- /api/data-health: subprocess 跑 agent/query.py data_health
- /api/compute + /api/compute-status: 后台线程批量重算 15 项指标,
  模块级 _compute_state/_compute_lock 状态机。

url_prefix="/api"。
"""
import os
import threading

from flask import Blueprint

from api.common import _err, _ok

bp = Blueprint("compute", __name__, url_prefix="/api")

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@bp.route("/data-health")
def api_data_health():
    """
    数据健康检查接口，供前端感知数据故障告警。
    返回 data_alerts（level=error/warning）、abort_reason、session 等完整信息。
    冷调用约1-2秒（AKShare 日历查询），建议前端低频轮询（60秒一次）。
    """
    try:
        import subprocess, json as _json
        from core.python_runtime import get_python_executable
        result = subprocess.run(
            [get_python_executable(), "agent/query.py", "data_health"],
            cwd=_PROJECT_ROOT,
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = _json.loads(result.stdout.strip())
        else:
            return _err(f"data_health 查询失败: {result.stderr[:200]}")

        return _ok(data)
    except Exception as exc:
        return _err(exc)


_compute_state: dict = {"status": "idle", "progress": [], "trade_date": "", "results": []}
_compute_lock = threading.Lock()


def _run_compute():
    """在独立线程中执行所有计算任务，更新 _compute_state。"""
    from quant.daily_compute import (
        compute_market_emotion, compute_lianzban_stats,
        compute_sector_zt_density, compute_sector_flow_acceleration,
        compute_volume_breakout, compute_chip_status,
        compute_lianzban_chain, compute_research_activity,
        compute_concept_zt_density, compute_call_auction_stats,
        compute_turnover_stats, compute_market_cap_dist,
        compute_advance_decline,
        compute_sector_chip_pressure, compute_sector_auction_sentiment,
    )
    from quant.loader import get_latest_trade_date

    tasks = [
        ("市场情绪指标",   compute_market_emotion),
        ("连板梯队统计",   compute_lianzban_stats),
        ("板块涨停密度",   compute_sector_zt_density),
        ("资金流加速度",   compute_sector_flow_acceleration),
        ("成交额异动",     compute_volume_breakout),
        ("筹码状态",       compute_chip_status),
        ("板块筹码压力",   compute_sector_chip_pressure),
        ("集合竞价委比",   compute_call_auction_stats),
        ("板块竞价情绪",   compute_sector_auction_sentiment),
        ("连板链条",       compute_lianzban_chain),
        ("机构调研热度",   compute_research_activity),
        ("概念涨停密度",   compute_concept_zt_density),
        ("换手率分层",     compute_turnover_stats),
        ("市值分布",       compute_market_cap_dist),
        ("市场宽度",       compute_advance_decline),
    ]
    import quant.loader as _loader
    # 前置检查：DATA_ROOT 未配置或路径不存在时，整批标为失败并附上明确原因
    if not _loader.DATA_ROOT:
        _fail = [{"name": n, "ok": False, "error": "未配置 QUANT_DATA_ROOT，请在设置中填写本地数据路径"} for n, _ in tasks]
        with _compute_lock:
            _compute_state.update({"status": "done", "progress": _fail, "trade_date": "", "results": _fail})
        return
    if not _loader.DATA_ROOT.exists():
        _fail = [{"name": n, "ok": False, "error": f"路径不存在: {_loader.DATA_ROOT}"} for n, _ in tasks]
        with _compute_lock:
            _compute_state.update({"status": "done", "progress": _fail, "trade_date": "", "results": _fail})
        return

    td = get_latest_trade_date()
    if not td:
        _fail = [{"name": n, "ok": False, "error": "无法读取交易日期，请确认 factors/stock/daily/涨停相关因子.parquet 存在且有数据"} for n, _ in tasks]
        with _compute_lock:
            _compute_state.update({"status": "done", "progress": _fail, "trade_date": "", "results": _fail})
        return

    with _compute_lock:
        _compute_state.update({"status": "running", "progress": [], "trade_date": td, "results": []})

    results = []
    for name, fn in tasks:
        try:
            fn(td)
            r = {"name": name, "ok": True}
        except Exception as e:
            r = {"name": name, "ok": False, "error": str(e)}
        results.append(r)
        with _compute_lock:
            _compute_state["progress"] = list(results)

    with _compute_lock:
        _compute_state.update({"status": "done", "results": results})


@bp.route("/compute", methods=["POST"])
def api_compute():
    """启动后台计算任务，立即返回。"""
    with _compute_lock:
        if _compute_state["status"] == "running":
            return _ok({"started": False, "message": "计算任务已在运行中"})
        _compute_state["status"] = "running"
    t = threading.Thread(target=_run_compute, daemon=True, name="compute-worker")
    t.start()
    return _ok({"started": True})


@bp.route("/compute-status")
def api_compute_status():
    """轮询计算进度。"""
    with _compute_lock:
        state = dict(_compute_state)
    return _ok(state)

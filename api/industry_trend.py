"""api/industry_trend.py — 行业趋势域 Blueprint (自 server.py 拆出)。

11 个路由: 截面读取 (latest/dates/data) + 异步重算 (run/job) +
可视化数据 (heatmap/rps-heatmap/series/kline) + 类别 (categories/rotation)。

模块级状态 (job dict + lock) 随 Blueprint 整体搬迁, 不跨模块共享。
"""
import json as _json
import logging
import threading
from datetime import datetime

from flask import Blueprint, request

from api.common import _err, _ok

logger = logging.getLogger(__name__)

bp = Blueprint("industry_trend", __name__, url_prefix="/api/industry-trend")


# ── 异步重算任务 (照抄复盘 v2 模式: POST 立即返回, 前端轮询 job) ─────────────
# 与 data?force=1 同步路径的区别: 先增量刷申万指数/资金流缓存再算——
# 新交易日不先刷缓存, resolve_trade_date 会把日期归一到旧一天, 重算静默失效。

_INDUSTRY_TREND_JOB: dict = {
    "state": "idle",        # idle | running | done | error
    "trade_date": None,
    "progress": "",
    "result": None,         # {trade_date, changes}
    "error": None,
    "started_at": None,
    "finished_at": None,
}
_industry_trend_job_lock = threading.Lock()


def _industry_trend_job_worker(trade_date, force: bool) -> None:
    job = _INDUSTRY_TREND_JOB
    try:
        job["progress"] = "增量拉取申万指数/资金流缓存 (已最新则秒级跳过)..."
        from quant.industry_trend_daily import refresh_sw_caches, run_industry_trend
        if force:
            refresh_sw_caches()
        job["progress"] = "重算 50 列截面 + 存档 + 与上期对比 (industry_ma_trend.py, 1-3 分钟)..."
        it = run_industry_trend(trade_date or None, force_recompute=True)
        if not it:
            raise RuntimeError("重算未返回数据, 请查看后端日志")
        job["result"] = {"trade_date": it.get("trade_date"),
                         "changes": len(it.get("changes", []))}
        job["trade_date"] = it.get("trade_date")
        job["state"] = "done"
        logger.info("[industry-trend-job] 重算完成 %s changes=%s",
                    it.get("trade_date"), job["result"]["changes"])
    except Exception as exc:
        job["state"] = "error"
        job["error"] = str(exc)
        logger.exception("[industry-trend-job] 重算失败")
    finally:
        job["finished_at"] = datetime.now().isoformat(timespec="seconds")
        job["progress"] = ""


@bp.route("/latest")
def api_industry_trend_latest():
    """最近一次行业趋势截面 — 永远返回 cache, 不触发重算(重算走 data?force=1)。"""
    try:
        from db.storage import get_industry_trend_daily
        row = get_industry_trend_daily(None)
        return _ok(_json.loads(row["payload"]) if row else None)
    except Exception as exc:
        return _err(exc)


@bp.route("/dates")
def api_industry_trend_dates():
    try:
        from db.storage import get_industry_trend_dates
        dates = get_industry_trend_dates()
        # 把 Exodia 数据中心的最新数据日合并到顶部(照抄 /api/review/dates):
        # 收盘数据落地但还没算过时, 用户也能在下拉里选到新日期并点「重算选中日期」
        try:
            from agent.review_v2 import _exodia_latest_stock_date
            ex = _exodia_latest_stock_date()
            if ex:
                if len(ex) == 8 and ex.isdigit():
                    ex = f"{ex[:4]}-{ex[4:6]}-{ex[6:8]}"
                if ex not in dates:
                    dates.insert(0, ex)
        except Exception as exc:
            logger.warning("[industry-trend/dates] 合并 Exodia 最新日失败: %s", exc)
        return _ok(dates)
    except Exception as exc:
        return _err(exc)


@bp.route("/data")
def api_industry_trend_data():
    """单日截面。force=1 → 同步重算该交易日(industry_ma_trend.py 子进程, 1-3 分钟,
    前端「重算选中日期」按钮转圈等), 走 INSERT OR REPLACE 覆盖存档。"""
    try:
        import time as _time
        from db.storage import get_industry_trend_daily
        trade_date = request.args.get("date", "").strip()
        force = request.args.get("force", "").strip() in ("1", "true", "yes")
        if len(trade_date) == 8 and trade_date.isdigit():
            trade_date = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
        if not force:
            if not trade_date:
                row = get_industry_trend_daily(None)
                return _ok(_json.loads(row["payload"]) if row else None)
            row = get_industry_trend_daily(trade_date)
            if row:
                return _ok(_json.loads(row["payload"]))
            # 未算过 → 返回空, 前端提示点「重算选中日期」
            return _ok(None)
        # force=1: 重算(无 date → 数据最新交易日)
        from quant.industry_trend_daily import run_industry_trend
        logger.info("[industry-trend] 重算开始 date=%s", trade_date or "(最新)")
        t0 = _time.time()
        data = run_industry_trend(trade_date or None, force_recompute=True)
        elapsed = round(_time.time() - t0, 1)
        if data:
            logger.info("[industry-trend] 重算完成 %s 耗时=%ss", data["trade_date"], elapsed)
            return _ok({**data, "_recompute": True, "_elapsed_sec": elapsed})
        return _ok(None)
    except Exception as exc:
        return _err(exc)


@bp.route("/run", methods=["POST"])
def api_industry_trend_run():
    """手动触发行业趋势重算 (后台任务, 立即返回).

    body: {trade_date?: "YYYY-MM-DD", force?: bool}
    - 不传 trade_date: 自动用申万缓存最新交易日
    - force=true (默认建议): 先增量刷申万指数/资金流缓存, 新交易日数据才完整
    - 已有任务在跑: 返回 {started: false, state: "running"}
    前端轮询 GET /api/industry-trend/job 拿进度。
    """
    try:
        body = request.get_json(silent=True) or {}
        trade_date = (body.get("trade_date") or "").strip() or None
        force = bool(body.get("force", True))
        with _industry_trend_job_lock:
            if _INDUSTRY_TREND_JOB["state"] == "running":
                return _ok({"started": False, "state": "running",
                            "progress": _INDUSTRY_TREND_JOB["progress"]})
            _INDUSTRY_TREND_JOB.update(
                state="running", trade_date=trade_date, progress="启动中...",
                result=None, error=None,
                started_at=datetime.now().isoformat(timespec="seconds"),
                finished_at=None,
            )
            threading.Thread(
                target=_industry_trend_job_worker, args=(trade_date, force),
                daemon=True, name="industry-trend-job",
            ).start()
        return _ok({"started": True, "state": "running"})
    except Exception as exc:
        return _err(exc)


@bp.route("/job")
def api_industry_trend_job():
    """行业趋势异步任务状态 (前端轮询)."""
    with _industry_trend_job_lock:
        return _ok(dict(_INDUSTRY_TREND_JOB))


@bp.route("/rps-heatmap")
def api_industry_trend_rps_heatmap():
    """行业 RPS(相对强度)热力图: 日期 × 行业。"""
    try:
        days = int(request.args.get("days", "40"))
        import importlib
        import quant.industry_trend_daily as _itd
        importlib.reload(_itd)
        return _ok(_itd.get_rps_heatmap(days))
    except Exception as exc:
        return _err(exc)


@bp.route("/heatmap")
def api_industry_trend_heatmap():
    """综合分热力图: 日期 × 行业矩阵 (行业趋势页新标签页用)。"""
    try:
        days = int(request.args.get("days", "20"))
        import importlib
        import quant.industry_trend_daily as _itd
        importlib.reload(_itd)
        return _ok(_itd.get_score_heatmap(days))
    except Exception as exc:
        return _err(exc)


@bp.route("/categories")
def api_industry_trend_categories():
    """31 行业 → 6 大类别映射 (唯一来源: quant/industry_trend_daily.py, 前端只消费不硬编码)。"""
    try:
        from quant.industry_trend_daily import INDUSTRY_CATEGORY, CATEGORY_ORDER
        return _ok({"map": INDUSTRY_CATEGORY, "order": CATEGORY_ORDER})
    except Exception as exc:
        return _err(exc)


@bp.route("/category-rotation")
def api_industry_trend_category_rotation():
    """6 大板块类别轮动时序: 日期 × 类别 多指标均值 (类别轮动标签页用)。"""
    try:
        days = int(request.args.get("days", "60"))
        import importlib
        import quant.industry_trend_daily as _itd
        importlib.reload(_itd)
        return _ok(_itd.get_category_rotation(days))
    except Exception as exc:
        return _err(exc)


@bp.route("/series")
def api_industry_trend_series():
    """单行业关键指标时序(从存档聚合, 供折线图)。"""
    try:
        industry = request.args.get("industry", "").strip()
        days = int(request.args.get("days", "30"))
        if not industry:
            return _err("缺少 industry 参数", 400)
        from quant.industry_trend_daily import get_industry_series
        return _ok(get_industry_series(industry, days))
    except Exception as exc:
        return _err(exc)


@bp.route("/kline")
def api_industry_trend_kline():
    """单行业申万官方指数日K + 均线 + 1/3/5年位置高低点 (ECharts K线图)。

    params: industry (必填), days=1600 (返回根数), date=YYYY-MM-DD (可选,
    截断到该存档日, 与当日截面位置%口径一致, 不"偷看未来")。
    """
    try:
        industry = request.args.get("industry", "").strip()
        days = int(request.args.get("days", "1600"))
        end_date = (request.args.get("date") or "").strip() or None
        if not industry:
            return _err("缺少 industry 参数", 400)
        from quant.industry_trend_daily import get_industry_kline
        data = get_industry_kline(industry, days=days, end_date=end_date)
        if data is None:
            return _err(f"无该行业指数数据: {industry}", 404)
        return _ok(data)
    except Exception as exc:
        return _err(exc)

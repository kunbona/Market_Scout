"""
Market Radar — Flask 服务
启动方式：bash start.sh
端口通过 FLASK_PORT 环境变量配置（默认 20026），写入 .env.local 持久化。
"""

import sys
import os
import logging
import warnings
from datetime import datetime

# 强制 line-buffering，确保 PIPELINE 日志在重定向时也能实时刷出
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# 自定义 PIPELINE 级别（25），介于 INFO(20) 和 WARNING(30) 之间
# Claude/Kimi 对话内容走这个级别，根 logger 设 WARNING 压掉噪音后仍可见
PIPELINE_LEVEL = 25
logging.addLevelName(PIPELINE_LEVEL, "PIPELINE")

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
    force=True,
)
# Flask 启动/请求日志保留 WARNING，apscheduler 完全静默
logging.getLogger("werkzeug").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)

# akshare 内部 pandas 兼容问题，与本项目代码无关，静默掉
warnings.filterwarnings("ignore", message="A value is trying to be set on a copy of a slice")

# 加载本地配置（.env.local 优先级最高，覆盖 .env）
def _load_env_local():
    env_path = os.path.join(os.path.dirname(__file__), ".env.local")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, val)

_load_env_local()

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

# Allow importing db/storage from the project root
sys.path.insert(0, os.path.dirname(__file__))
from db.storage import (
    get_cls_news_by_source,
    count_cls_news_by_source,
    get_policy_news_by_source,
    get_policy_news,
    count_policy_news_by_source,
    get_sector_flow_latest,
    get_lhb_data, get_lhb_seat,
    get_zt_pool,
    get_dt_pool,
    get_zbgc_pool,
    get_strong_pool,
    get_research_reports,
    get_market_emotion_summary,
    get_lianzban_stats,
    get_sector_zt_density,
    get_concept_zt_density,
    get_lianzban_chain,
    get_market_pulse_latest,
    get_hot_rank_up_latest,
    get_northbound_flow_latest,
    get_xq_hot_latest,
    get_concept_flow_latest,
    get_agent_summary_latest,
    get_agent_summary_history,
    get_agent_summary_by_id,
    get_sector_flow_accel,
    get_volume_breakout,
    get_turnover_stats,
    get_market_cap_dist,
    get_advance_decline,
    get_big_deal_latest,
    get_margin_latest,
    get_block_trade_latest,
    get_holder_count_latest,
    get_fundamentals_finance,
    get_fundamentals_f10,
    get_lockup_expiry,
    get_dividend_latest,
    get_industry_ranking_latest,
    get_ths_hot_stocks_latest,
    get_latest_emotion_date,
    get_research_activity,
)

DIST = os.path.join(os.path.dirname(__file__), "dashboard", "dist")

app = Flask(__name__, static_folder=DIST, static_url_path="")
CORS(app)



# ---------------------------------------------------------------------------
# Frontend (SPA)
# ---------------------------------------------------------------------------

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_spa(path):
    """Serve React SPA; fall back to index.html for client-side routing."""
    if path.startswith("api/"):
        from flask import abort
        abort(404)
    full = os.path.join(DIST, path)
    # 防路径穿越：确保解析后的绝对路径仍在 DIST 目录下
    real_full = os.path.realpath(full)
    real_dist = os.path.realpath(DIST)
    if path and not real_full.startswith(real_dist + os.sep):
        from flask import abort
        abort(403)
    if path and os.path.exists(full):
        # Hashed assets: cache aggressively
        resp = send_from_directory(DIST, path)
        if path.startswith("assets/"):
            resp.cache_control.max_age = 31536000
            resp.cache_control.immutable = True
        return resp
    # index.html: never cache — ensures browser picks up new asset hashes after deploy
    resp = send_from_directory(DIST, "index.html")
    resp.cache_control.no_cache = True
    resp.cache_control.no_store = True
    return resp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _ok(data):
    return jsonify({"success": True, "data": data, "ts": datetime.now().isoformat()})


def _err(msg: str, code: int = 500):
    return jsonify({"success": False, "error": str(msg)}), code


def _date_param(key: str = "date") -> str:
    """Return the query-string date param, falling back to today."""
    val = request.args.get(key, "").strip()
    return val if val else _today()


def _date_or_none(key: str = "date") -> str | None:
    """Return date param if provided, else None (let storage pick latest)."""
    val = request.args.get(key, "").strip()
    return val if val else None


def _computed_date(key: str = "date") -> str:
    """用于历史计算型接口：有参数用参数，无参数回落到最新已计算日期。"""
    val = request.args.get(key, "").strip()
    return val if val else (get_latest_emotion_date() or _today())


def _save_env_local(updates: dict) -> None:
    """将 key=value 写入 .env.local，已有的 key 更新，不存在的追加。"""
    env_path = os.path.join(os.path.dirname(__file__), ".env.local")
    lines = []
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

    written = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue
        key = stripped.partition("=")[0].strip()
        if key in updates:
            new_lines.append(f"{key}={updates[key]}\n")
            written.add(key)
        else:
            new_lines.append(line)

    # 追加未出现过的 key
    for key, val in updates.items():
        if key not in written:
            new_lines.append(f"{key}={val}\n")

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)


# ---------------------------------------------------------------------------
# News & Policy
# ---------------------------------------------------------------------------

@app.route("/api/news")
def api_news():
    try:
        source    = request.args.get("source", "财联社")
        page_size = int(request.args.get("page_size", 30))
        page      = int(request.args.get("page", 1))
        offset    = (page - 1) * page_size
        rows  = get_cls_news_by_source(source, page_size, offset)
        total = count_cls_news_by_source(source)
        return _ok({"items": rows, "total": total, "page": page, "page_size": page_size})
    except Exception as exc:
        return _err(exc)


@app.route("/api/policy")
def api_policy():
    try:
        source    = request.args.get("source", "全部")
        page_size = int(request.args.get("page_size", 30))
        page      = int(request.args.get("page", 1))
        offset    = (page - 1) * page_size
        if source == "全部":
            rows  = get_policy_news(page_size, offset)
            total = sum(count_policy_news_by_source(s) for s in
                        ['巨潮公告','财新','发改委','证监会','上交所问询','深交所问询','深交所公告'])
        else:
            rows  = get_policy_news_by_source(source, page_size, offset)
            total = count_policy_news_by_source(source)
        return _ok({"items": rows, "total": total, "page": page, "page_size": page_size})
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Research reports
# ---------------------------------------------------------------------------

@app.route("/api/research")
def api_research():
    try:
        qtype = int(request.args.get("qtype", 0))
        limit = int(request.args.get("limit", 20))
        today_only_raw = request.args.get("today_only", "false").lower()
        today_only = today_only_raw in ("1", "true", "yes")
        rows = get_research_reports(qtype, limit, today_only)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Sector / Concept flow
# ---------------------------------------------------------------------------

@app.route("/api/sector-flow")
def api_sector_flow():
    try:
        source_type = request.args.get("type", "industry")
        rows = get_sector_flow_latest(source_type)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@app.route("/api/concept-flow")
def api_concept_flow():
    try:
        top_n = int(request.args.get("top_n", 30))
        rows = get_concept_flow_latest(top_n)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Dragon-Tiger List
# ---------------------------------------------------------------------------

@app.route("/api/lhb")
def api_lhb():
    try:
        trade_date = _date_or_none()
        rows = get_lhb_data(trade_date)
        # 附加席位明细
        seats = get_lhb_seat(trade_date)
        seat_map: dict = {}
        for s in seats:
            seat_map.setdefault(s["stock_code"], []).append(s)
        for row in rows:
            code = row.get("stock_code", "")
            row_seats = seat_map.get(code, [])
            row["seats"] = row_seats
            types = {s.get("seat_type") for s in row_seats}
            if "游资" in types and "机构" in types:
                row["seat_nature"] = "游资+机构"
            elif "机构" in types:
                row["seat_nature"] = "机构主导"
            elif "游资" in types:
                row["seat_nature"] = "游资主导"
            elif row_seats:
                row["seat_nature"] = "其他"
            else:
                row["seat_nature"] = None
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@app.route("/api/lhb-local")
def api_lhb_local():
    """龙虎榜本地版：只读 lhb_seat 表（由本地 CSV 写入），无数据返回空列表。"""
    try:
        trade_date = _date_or_none()
        seats = get_lhb_seat(trade_date)
        # 按 stock_code 聚合席位
        stock_map: dict = {}
        for s in seats:
            code = s["stock_code"]
            if code not in stock_map:
                stock_map[code] = {
                    "stock_code": code,
                    "seats": [],
                    "net_buy": 0.0,
                }
            stock_map[code]["seats"].append(s)
            stock_map[code]["net_buy"] += s.get("net_amount") or 0.0
        # 附加席位性质
        result = []
        for row in stock_map.values():
            types = {s.get("seat_type") for s in row["seats"]}
            if "游资" in types and "机构" in types:
                row["seat_nature"] = "游资+机构"
            elif "机构" in types:
                row["seat_nature"] = "机构主导"
            elif "游资" in types:
                row["seat_nature"] = "游资主导"
            elif row["seats"]:
                row["seat_nature"] = "其他"
            else:
                row["seat_nature"] = None
            result.append(row)
        result.sort(key=lambda r: r["net_buy"], reverse=True)
        return _ok(result)
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Pools
# ---------------------------------------------------------------------------

@app.route("/api/zt-pool")
def api_zt_pool():
    try:
        trade_date = _date_param()
        rows = get_zt_pool(trade_date)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@app.route("/api/dt-pool")
def api_dt_pool():
    try:
        trade_date = _date_param()
        rows = get_dt_pool(trade_date)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@app.route("/api/zbgc-pool")
def api_zbgc_pool():
    try:
        trade_date = _date_param()
        rows = get_zbgc_pool(trade_date)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@app.route("/api/strong-pool")
def api_strong_pool():
    try:
        trade_date = _date_param()
        rows = get_strong_pool(trade_date)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Market emotion & pulse
# ---------------------------------------------------------------------------

@app.route("/api/market-emotion")
def api_market_emotion():
    try:
        trade_date = _date_or_none()
        data = get_market_emotion_summary(trade_date)
        return _ok(data)
    except Exception as exc:
        return _err(exc)


@app.route("/api/market-pulse")
def api_market_pulse():
    try:
        n = int(request.args.get("n", 1))
        rows = get_market_pulse_latest(n)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Lianzban (consecutive limit-up) statistics
# ---------------------------------------------------------------------------

@app.route("/api/lianzban-stats")
def api_lianzban_stats():
    try:
        days = int(request.args.get("days", 30))
        data = get_lianzban_stats(days)
        return _ok(data)
    except Exception as exc:
        return _err(exc)


@app.route("/api/lianzban-chain")
def api_lianzban_chain():
    try:
        trade_date = _computed_date()
        rows = get_lianzban_chain(trade_date)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# ZT density
# ---------------------------------------------------------------------------

@app.route("/api/sector-zt-density")
def api_sector_zt_density():
    try:
        trade_date = _computed_date()
        rows = get_sector_zt_density(trade_date)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@app.route("/api/concept-zt-density")
def api_concept_zt_density():
    try:
        trade_date = _computed_date()
        top_n = int(request.args.get("top_n", 15))
        rows = get_concept_zt_density(trade_date, top_n)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Hot rank & Northbound
# ---------------------------------------------------------------------------

@app.route("/api/hot-rank-up")
def api_hot_rank_up():
    try:
        top_n = int(request.args.get("top_n", 20))
        rows = get_hot_rank_up_latest(top_n)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@app.route("/api/northbound-flow")
def api_northbound_flow():
    try:
        data = get_northbound_flow_latest()
        return _ok(data)
    except Exception as exc:
        return _err(exc)


@app.route("/api/xq-hot")
def api_xq_hot():
    try:
        top_n = int(request.args.get("top_n", 30))
        rows = get_xq_hot_latest(top_n)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# AI summary
# ---------------------------------------------------------------------------

@app.route("/api/ai-summary")
def api_ai_summary():
    try:
        data = get_agent_summary_latest()
        return _ok(data)
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Agent analysis endpoints
# ---------------------------------------------------------------------------

@app.route("/api/agent/latest")
def api_agent_latest():
    """返回最新报告元数据（含 has_html、id，供前端决定用 iframe 还是 JSON 渲染）。"""
    try:
        import json
        row = get_agent_summary_latest()
        if not row:
            return _ok(None)
        has_html = bool(row.get("report_html"))
        snapshot = row.get("data_snapshot_json")
        if snapshot:
            try:
                data = json.loads(snapshot)
            except Exception:
                data = {"content": row.get("content"), "summary_time": row.get("summary_time")}
        else:
            data = {"content": row.get("content"), "summary_time": row.get("summary_time")}
        data["summary_time"] = row.get("summary_time")
        data["run_type"] = row.get("run_type", "")
        data["id"] = row.get("id")
        data["has_html"] = has_html
        return _ok(data)
    except Exception as exc:
        return _err(exc)


@app.route("/api/agent/report/<int:row_id>")
def api_agent_report_html(row_id: int):
    """直接返回 HTML 报告，供 <iframe src="..."> 使用。"""
    try:
        from flask import Response
        row = get_agent_summary_by_id(row_id)
        if not row:
            return Response("<h1>404 Not Found</h1>", status=404, mimetype="text/html")
        html = row.get("report_html") or ""
        if not html:
            return Response("<p>此记录无 HTML 报告</p>", status=404, mimetype="text/html")
        return Response(html, status=200, mimetype="text/html; charset=utf-8")
    except Exception as exc:
        return Response(f"<p>错误：{exc}</p>", status=500, mimetype="text/html")


@app.route("/api/agent/history")
def api_agent_history():
    """返回最近 N 条报告摘要（run_type、run_time、summary_text、has_html）。"""
    try:
        import json
        limit = int(request.args.get("limit", 20))
        today_only = request.args.get("today", "false").lower() == "true"
        rows = get_agent_summary_history(limit=limit, today_only=today_only)
        results = []
        for row in rows:
            snap = row.get("data_snapshot_json") if "data_snapshot_json" in row else None
            summary_text = None
            run_time = None
            if snap:
                try:
                    d = json.loads(snap)
                    summary_text = d.get("summary_text") or (d.get("changes", [""])[0] if d.get("changes") else None)
                    run_time = d.get("run_time")
                except Exception:
                    pass
            results.append({
                "id": row.get("id"),
                "summary_time": row.get("summary_time"),
                "run_type": row.get("run_type", ""),
                "run_time": run_time or row.get("summary_time"),
                "summary_text": summary_text or row.get("content", ""),
                "has_html": bool(row.get("has_html")),
            })
        return _ok(results)
    except Exception as exc:
        return _err(exc)


@app.route("/api/agent/history/<int:row_id>")
def api_agent_history_detail(row_id: int):
    """返回单条报告的完整结构化数据（含 has_html 标记）。"""
    try:
        import json
        row = get_agent_summary_by_id(row_id)
        if not row:
            return _err("not found", 404)
        has_html = bool(row.get("report_html"))
        snap = row.get("data_snapshot_json")
        if snap:
            try:
                data = json.loads(snap)
            except Exception:
                data = {"content": row.get("content")}
        else:
            data = {"content": row.get("content")}
        data["summary_time"] = row.get("summary_time")
        data["run_type"] = row.get("run_type", "")
        data["id"] = row.get("id")
        data["has_html"] = has_html
        return _ok(data)
    except Exception as exc:
        return _err(exc)


@app.route("/api/agent/trigger", methods=["POST"])
def api_agent_trigger():
    """手动触发 Agent 分析（非阻塞）。body: {"run_type": "evening"}"""
    try:
        from agent.orchestrator import run_agent_analysis
        body = request.get_json(silent=True) or {}
        run_type = body.get("run_type") or _infer_run_type()
        _VALID_RUN_TYPES = {"morning", "auction", "intraday", "closing", "evening"}
        if run_type not in _VALID_RUN_TYPES:
            return _err(f"无效的 run_type: {run_type}", 400)
        result = run_agent_analysis(run_type)
        return _ok(result)
    except Exception as exc:
        return _err(exc)


@app.route("/api/agent/status")
def api_agent_status():
    """返回当前 Agent 运行状态。"""
    try:
        from agent.orchestrator import get_agent_state
        return _ok(get_agent_state())
    except Exception as exc:
        return _err(exc)


@app.route("/api/agent/stop", methods=["POST"])
def api_agent_stop():
    """请求停止当前正在运行的 Agent 分析。"""
    try:
        from agent.orchestrator import stop_agent_analysis
        stop_agent_analysis()
        return _ok({"stopped": True})
    except Exception as exc:
        return _err(exc)


def _infer_run_type() -> str:
    """根据当前时间推断 run_type。"""
    now = datetime.now()
    h, m = now.hour, now.minute
    total = h * 60 + m
    if total < 7 * 60:
        return "morning"
    if total < 9 * 60 + 30:
        return "auction"
    if total < 15 * 60:
        return "intraday"
    if total < 17 * 60:
        return "closing"
    return "evening"


# ---------------------------------------------------------------------------
# Sector flow acceleration / Volume breakout / Turnover stats / Market cap dist / Advance-decline
# ---------------------------------------------------------------------------

@app.route("/api/sector-flow-accel")
def api_sector_flow_accel():
    try:
        trade_date = _computed_date()
        rows = get_sector_flow_accel(trade_date)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)

@app.route("/api/volume-breakout")
def api_volume_breakout():
    try:
        trade_date = _computed_date()
        rows = get_volume_breakout(trade_date)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)

@app.route("/api/research-activity")
def api_research_activity():
    try:
        trade_date = _computed_date()
        rows = get_research_activity(trade_date)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@app.route("/api/sector-chip-pressure")
def api_sector_chip_pressure():
    try:
        from db.storage import get_sector_chip_pressure
        trade_date = _computed_date()
        rows = get_sector_chip_pressure(trade_date)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@app.route("/api/sector-auction-sentiment")
def api_sector_auction_sentiment():
    try:
        from db.storage import get_sector_auction_sentiment
        trade_date = _computed_date()
        rows = get_sector_auction_sentiment(trade_date)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)

@app.route("/api/turnover-stats")
def api_turnover_stats():
    try:
        trade_date = _computed_date()
        data = get_turnover_stats(trade_date)
        return _ok(data)
    except Exception as exc:
        return _err(exc)

@app.route("/api/market-cap-dist")
def api_market_cap_dist():
    try:
        trade_date = _computed_date()
        data = get_market_cap_dist(trade_date)
        return _ok(data)
    except Exception as exc:
        return _err(exc)

@app.route("/api/advance-decline")
def api_advance_decline():
    try:
        trade_date = _computed_date()
        data = get_advance_decline(trade_date)
        return _ok(data)
    except Exception as exc:
        return _err(exc)


@app.route("/api/trade-calendar/today")
def api_trade_calendar_today():
    try:
        from agent.query import get_market_session
        return _ok(get_market_session())
    except Exception as exc:
        return _err(exc)


@app.route("/api/agent/fundamental/trigger", methods=["POST"])
def api_fundamental_trigger():
    """手动触发基本面分析（独立于日常 pipeline）。"""
    try:
        from agent.orchestrator import run_fundamental_analysis
        result = run_fundamental_analysis()
        return _ok(result)
    except Exception as exc:
        return _err(exc)


@app.route("/api/agent/fundamental/status")
def api_fundamental_status():
    """返回基本面分析运行状态。"""
    try:
        from agent.orchestrator import get_fundamental_state
        return _ok(get_fundamental_state())
    except Exception as exc:
        return _err(exc)


@app.route("/api/agent/fundamental/latest")
def api_fundamental_latest():
    """返回最新一条未过期的基本面覆盖图。"""
    try:
        from db.storage import get_fundamental_coverage_latest
        import json as _json
        row = get_fundamental_coverage_latest()
        if not row:
            return _ok({"available": False})
        # 解析 coverage_json 减少前端处理量
        try:
            coverage = _json.loads(row.get("coverage_json") or "{}")
        except Exception:
            coverage = {}
        return _ok({
            "available": True,
            "id": row["id"],
            "generated_at": row["generated_at"],
            "expires_at": row["expires_at"],
            "has_html": bool(row.get("report_html")),
            "coverage": coverage,
        })
    except Exception as exc:
        return _err(exc)


@app.route("/api/agent/fundamental/report")
def api_fundamental_report():
    """返回最新基本面覆盖图的 HTML 报告，供 iframe 渲染。"""
    try:
        from db.storage import get_fundamental_coverage_latest
        row = get_fundamental_coverage_latest()
        if not row or not row.get("report_html"):
            return _err("暂无基本面覆盖图 HTML 报告", 404)
        from flask import Response
        return Response(row["report_html"], mimetype="text/html")
    except Exception as exc:
        return _err(exc)


@app.route("/api/agent/time-slot")
def api_agent_time_slot():
    """
    返回当前应显示的 AI 分析时段按钮高亮状态，以及下次切换时间。

    逻辑：
    - 交易日 00:00–09:15 → morning
    - 交易日 09:15–15:30 → intraday
    - 交易日 15:30–24:00 → evening
    - 非交易日（周末/节假日）→ evening，直到下一个交易日 00:00 切换为 morning
    """
    try:
        from datetime import datetime, timedelta, time as dtime
        from agent.query import _get_trade_calendar

        now = datetime.now()
        today = now.date()
        total_min = now.hour * 60 + now.minute

        try:
            cal = _get_trade_calendar()
            trade_dates = sorted(cal["trade_date"].values)
            is_trade_today = today in trade_dates
        except Exception:
            is_trade_today = today.weekday() < 5
            trade_dates = []

        def _next_trade_date_after(d):
            """返回 d 之后第一个交易日（不含 d）。"""
            for td in trade_dates:
                if td > d:
                    return td
            # fallback：跳过周末往后找
            nxt = d + timedelta(days=1)
            while nxt.weekday() >= 5:
                nxt += timedelta(days=1)
            return nxt

        if is_trade_today:
            if total_min < 9 * 60 + 15:
                slot = "morning"
                # 下次切换：今天 09:15
                next_change = datetime.combine(today, dtime(9, 15))
            elif total_min < 15 * 60 + 30:
                slot = "intraday"
                next_change = datetime.combine(today, dtime(15, 30))
            else:
                slot = "evening"
                nxt = _next_trade_date_after(today)
                next_change = datetime.combine(nxt, dtime(0, 0))
        else:
            slot = "evening"
            nxt = _next_trade_date_after(today)
            next_change = datetime.combine(nxt, dtime(0, 0))

        return _ok({
            "slot": slot,
            "is_trade_today": is_trade_today,
            "next_change_at": next_change.isoformat(),
            "server_time": now.isoformat(),
        })
    except Exception as exc:
        return _err(exc)


@app.route("/api/data-health")
def api_data_health():
    """
    数据健康检查接口，供前端感知数据故障告警。
    返回 data_alerts（level=error/warning）、abort_reason、session 等完整信息。
    冷调用约1-2秒（AKShare 日历查询），建议前端低频轮询（60秒一次）。
    """
    try:
        import subprocess, json as _json
        result = subprocess.run(
            ["python", "agent/query.py", "data_health"],
            cwd=os.path.dirname(__file__),
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
_compute_lock = __import__("threading").Lock()


def _run_compute():
    """在独立线程中执行所有计算任务，更新 _compute_state。"""
    import threading
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


@app.route("/api/compute", methods=["POST"])
def api_compute():
    """启动后台计算任务，立即返回。"""
    import threading
    with _compute_lock:
        if _compute_state["status"] == "running":
            return _ok({"started": False, "message": "计算任务已在运行中"})
        _compute_state["status"] = "running"
    t = threading.Thread(target=_run_compute, daemon=True, name="compute-worker")
    t.start()
    return _ok({"started": True})


@app.route("/api/compute-status")
def api_compute_status():
    """轮询计算进度。"""
    with _compute_lock:
        state = dict(_compute_state)
    return _ok(state)


# ---------------------------------------------------------------------------
# Runtime config (DATA_ROOT / RSSHub)
# ---------------------------------------------------------------------------

def _get_fundamental_coverage_status():
    """返回 (is_fresh: bool, expires_at: str|None)，失败时静默返回 (False, None)。"""
    try:
        from db.storage import get_fundamental_coverage_latest
        row = get_fundamental_coverage_latest()
        if row:
            return True, row.get("expires_at")
        return False, None
    except Exception:
        return False, None


@app.route("/api/config", methods=["GET"])
def api_config_get():
    """返回当前运行时配置值。"""
    import quant.loader as loader
    rsshub_global = os.environ.get("RSSHUB_BASE_URL", "")
    data_root = str(loader.DATA_ROOT) if loader.DATA_ROOT else ""
    flask_port = os.environ.get("FLASK_PORT", "20026")
    quant_workers = os.environ.get("QUANT_WORKERS", "")
    agent_enabled = os.environ.get("AGENT_ENABLED", "true")
    compute_enabled = os.environ.get("COMPUTE_ENABLED", "true")
    qmt_enabled = os.environ.get("QMT_ENABLED", "false")
    qmt_path = os.environ.get("QMT_PATH", "")
    # 实时探测 xtquant 是否可达（不阻塞，connect() 内部有超时保护）
    qmt_connected = False
    qmt_version = None
    if qmt_enabled.lower() in ("true", "1", "yes"):
        try:
            from fetcher.xtquant_breadth import connect as _qmt_connect, get_version as _qmt_ver
            qmt_connected = _qmt_connect()
            qmt_version = _qmt_ver()
        except Exception:
            pass
    return _ok({
        "data_root": data_root,
        "rsshub_url": rsshub_global,
        "flask_port": flask_port,
        "quant_workers": quant_workers,
        "agent_enabled": agent_enabled,
        "compute_enabled": compute_enabled,
        "qmt_enabled": qmt_enabled,
        "qmt_path": qmt_path,
        "qmt_connected": qmt_connected,
        "qmt_version": qmt_version,
    })


@app.route("/api/config", methods=["POST"])
def api_config_set():
    """更新运行时配置，并持久化到 .env.local。"""
    import quant.loader as loader
    from pathlib import Path
    body = request.get_json(silent=True) or {}
    changed = []
    env_updates = {}

    if "data_root" in body:
        new_path = body["data_root"].strip()
        if new_path and Path(new_path).exists():
            loader.DATA_ROOT = Path(new_path)
            os.environ["QUANT_DATA_ROOT"] = new_path
            env_updates["QUANT_DATA_ROOT"] = new_path
            changed.append(f"QUANT_DATA_ROOT → {new_path}")
        elif new_path:
            return _err(f"路径不存在: {new_path}", 400)
        else:
            # 清空路径
            loader.DATA_ROOT = None
            os.environ.pop("QUANT_DATA_ROOT", None)
            env_updates["QUANT_DATA_ROOT"] = ""
            changed.append("QUANT_DATA_ROOT 已清空")

    if "rsshub_url" in body:
        new_url = body["rsshub_url"].strip().rstrip("/")
        if new_url:
            os.environ["RSSHUB_BASE_URL"] = new_url
            env_updates["RSSHUB_BASE_URL"] = new_url
            import sys
            for mod_name in ("fetcher.global_news", "fetcher.policy_rss"):
                mod = sys.modules.get(mod_name)
                if mod:
                    mod.RSSHUB = new_url
            changed.append(f"RSSHUB_BASE_URL → {new_url}")

    if "flask_port" in body:
        new_port = body["flask_port"].strip()
        if new_port.isdigit() and 1024 <= int(new_port) <= 65535:
            env_updates["FLASK_PORT"] = new_port
            changed.append(f"FLASK_PORT → {new_port}（重启后生效）")
        elif new_port:
            return _err(f"端口无效: {new_port}，需为 1024-65535 之间的数字", 400)

    if "quant_workers" in body:
        val = body["quant_workers"].strip()
        if val == "":
            os.environ.pop("QUANT_WORKERS", None)
            env_updates["QUANT_WORKERS"] = ""
            changed.append("QUANT_WORKERS 已清空（自动检测并发数）")
        elif val == "0" or val == "1":
            # 0 或 1 均表示串行模式（单线程/单进程）
            os.environ["QUANT_WORKERS"] = val
            env_updates["QUANT_WORKERS"] = val
            changed.append(f"QUANT_WORKERS → {val}（串行模式，立即生效）")
        elif val.isdigit() and 2 <= int(val) <= 64:
            os.environ["QUANT_WORKERS"] = val
            env_updates["QUANT_WORKERS"] = val
            changed.append(f"QUANT_WORKERS → {val}（立即生效）")
        else:
            return _err(f"并发数无效: {val}，需为 0-64 之间的整数（0 或 1 表示串行）", 400)

    if "agent_enabled" in body:
        raw = body["agent_enabled"]
        if isinstance(raw, bool):
            val = "true" if raw else "false"
        else:
            val = "true" if str(raw).strip().lower() in ("true", "1", "yes") else "false"
        os.environ["AGENT_ENABLED"] = val
        env_updates["AGENT_ENABLED"] = val
        changed.append(f"AGENT_ENABLED → {val}（立即生效）")

    if "compute_enabled" in body:
        raw = body["compute_enabled"]
        if isinstance(raw, bool):
            val = "true" if raw else "false"
        else:
            val = "true" if str(raw).strip().lower() in ("true", "1", "yes") else "false"
        os.environ["COMPUTE_ENABLED"] = val
        env_updates["COMPUTE_ENABLED"] = val
        changed.append(f"COMPUTE_ENABLED → {val}（重启后生效）")

    if "qmt_enabled" in body:
        raw = body["qmt_enabled"]
        if isinstance(raw, bool):
            val = "true" if raw else "false"
        else:
            val = "true" if str(raw).strip().lower() in ("true", "1", "yes") else "false"
        os.environ["QMT_ENABLED"] = val
        # 同步到 xtquant_breadth 模块（如果已加载）
        import sys
        mod = sys.modules.get("fetcher.xtquant_breadth")
        if mod:
            pass  # xtquant_breadth 每次调用时读 os.environ，无需额外同步
        env_updates["QMT_ENABLED"] = val
        changed.append(f"QMT_ENABLED → {val}（立即生效）")

    if "qmt_path" in body:
        new_path = body["qmt_path"].strip()
        os.environ["QMT_PATH"] = new_path
        env_updates["QMT_PATH"] = new_path
        if new_path:
            changed.append(f"QMT_PATH → {new_path}")
        else:
            changed.append("QMT_PATH 已清空")

    if env_updates:
        try:
            _save_env_local(env_updates)
        except Exception as e:
            logger_api = __import__("logging").getLogger(__name__)
            logger_api.warning("写入 .env.local 失败: %s", e)

    return _ok({"changed": changed})


@app.route("/api/config/test-data-root")
def api_test_data_root():
    """检查 DATA_ROOT 路径是否存在且包含必要的 parquet 文件。"""
    import quant.loader as loader
    from pathlib import Path
    path_str = request.args.get("path", "").strip()
    check_path = Path(path_str) if path_str else loader.DATA_ROOT
    if not check_path:
        return _ok({"ok": False, "reason": "未配置路径"})
    if not check_path.exists():
        return _ok({"ok": False, "reason": f"路径不存在: {check_path}"})
    # 检查关键文件
    key_file = check_path / "factors" / "stock" / "daily" / "涨停相关因子.parquet"
    if not key_file.exists():
        return _ok({"ok": False, "reason": f"未找到涨停因子文件，请确认路径正确"})
    return _ok({"ok": True, "reason": f"路径有效: {check_path}"})


@app.route("/api/config/test-rsshub")
def api_test_rsshub():
    """检查 RSSHub 服务是否可达。"""
    import requests as _req
    url_param = request.args.get("url", "").strip().rstrip("/")
    test_url = url_param or os.environ.get("RSSHUB_BASE_URL", "")
    if not test_url:
        return _ok({"ok": False, "reason": "未配置 RSSHub 地址"})
    try:
        r = _req.get(f"{test_url}/", timeout=4)
        if r.status_code < 500:
            return _ok({"ok": True, "reason": f"连通（HTTP {r.status_code}）"})
        return _ok({"ok": False, "reason": f"服务异常（HTTP {r.status_code}）"})
    except _req.exceptions.ConnectionError:
        return _ok({"ok": False, "reason": "连接被拒绝，请确认 RSSHub 已启动"})
    except _req.exceptions.Timeout:
        return _ok({"ok": False, "reason": "连接超时（>4s）"})
    except Exception as e:
        return _ok({"ok": False, "reason": str(e)})


@app.route("/api/config/test-qmt")
def api_test_qmt():
    """检测 miniQMT 是否可达，返回连接状态和 xtquant 版本。"""
    from pathlib import Path

    # 检查 xtquant 是否安装
    try:
        from fetcher.xtquant_breadth import connect as _qmt_connect, get_version as _qmt_ver
    except Exception as e:
        return _ok({"ok": False, "reason": f"xtquant 模块加载失败: {e}", "version": None})

    version = _qmt_ver()
    if version is None:
        return _ok({"ok": False, "reason": "xtquant 未安装（pip install xtquant）", "version": None})

    # 检查 QMT 路径（可选验证）
    qmt_path = request.args.get("path", "").strip() or os.environ.get("QMT_PATH", "")
    if qmt_path:
        p = Path(qmt_path)
        if not p.exists():
            return _ok({"ok": False, "reason": f"QMT 路径不存在: {qmt_path}", "version": version})

    # 尝试连接
    # 临时强制 QMT_ENABLED=true 以便 connect() 不因开关而短路
    _orig = os.environ.get("QMT_ENABLED", "false")
    os.environ["QMT_ENABLED"] = "true"
    try:
        connected = _qmt_connect()
    finally:
        os.environ["QMT_ENABLED"] = _orig

    if connected:
        return _ok({"ok": True, "reason": f"miniQMT 连接成功（xtquant {version}）", "version": version})
    else:
        return _ok({"ok": False, "reason": "miniQMT 连接失败，请确认客户端已启动并登录", "version": version})


@app.route("/api/big-deal")
def api_big_deal():
    try:
        limit = int(request.args.get("limit", 50))
        rows = get_big_deal_latest(limit)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@app.route("/api/margin")
def api_margin():
    try:
        top_n = int(request.args.get("top_n", 50))
        rows = get_margin_latest(top_n)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@app.route("/api/block-trade")
def api_block_trade():
    try:
        limit = int(request.args.get("limit", 50))
        rows = get_block_trade_latest(limit)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@app.route("/api/holder-count")
def api_holder_count():
    try:
        top_n = int(request.args.get("top_n", 50))
        rows = get_holder_count_latest(top_n)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@app.route("/api/fundamentals/finance")
def api_fundamentals_finance():
    try:
        fetch_date = request.args.get("date", "").strip() or None
        rows = get_fundamentals_finance(fetch_date)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@app.route("/api/fundamentals/f10")
def api_fundamentals_f10():
    try:
        code = request.args.get("code", "").strip()
        fetch_date = request.args.get("date", "").strip() or None
        if not code:
            return _err("缺少 code 参数", 400)
        rows = get_fundamentals_f10(code, fetch_date)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@app.route("/api/lockup-expiry")
def api_lockup_expiry():
    try:
        days = int(request.args.get("days", 30))
        rows = get_lockup_expiry(days)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@app.route("/api/dividend")
def api_dividend():
    try:
        limit = int(request.args.get("limit", 100))
        rows = get_dividend_latest(limit)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@app.route("/api/industry-ranking")
def api_industry_ranking():
    try:
        rows = get_industry_ranking_latest()
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@app.route("/api/ths-hot-stocks")
def api_ths_hot_stocks():
    try:
        top_n = int(request.args.get("top_n", 50))
        rows = get_ths_hot_stocks_latest(top_n)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@app.route("/api/research/pdf")
def api_research_pdf():
    """代理下载东财研报 PDF，自动注入 Referer 绕过 403。"""
    import requests as _req
    from flask import Response, stream_with_context
    pdf_url = request.args.get("url", "").strip()
    if not pdf_url:
        return _err("无效的 PDF 地址", 400)
    from urllib.parse import urlparse as _urlparse
    _parsed = _urlparse(pdf_url)
    if _parsed.scheme != "https" or _parsed.netloc != "pdf.dfcfw.com":
        return _err("无效的 PDF 地址", 400)
    try:
        r = _req.get(
            pdf_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://data.eastmoney.com/",
            },
            stream=True,
            timeout=20,
        )
        if r.status_code != 200:
            return _err(f"上游返回 {r.status_code}", 502)
        filename = pdf_url.split("/")[-1] or "report.pdf"
        return Response(
            stream_with_context(r.iter_content(chunk_size=8192)),
            content_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{filename}"'},
        )
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Manual fetch trigger
# ---------------------------------------------------------------------------

from core.fetch_status import fetch_state as _fetch_state, fetch_lock as _fetch_lock


def _run_fetch_all():
    """在后台线程执行全量抓取，不受交易时段限制。"""
    import threading
    from datetime import datetime as _dt

    try:
        from fetcher.cls_news import fetch as fetch_cls
        from fetcher.global_news import (
            fetch_cls_red, fetch_em, fetch_ths, fetch_wscn,
            fetch_yicai, fetch_jin10, fetch_gelonghui,
        )
        from fetcher.policy_rss import fetch as fetch_policy
        from fetcher.sector_heat import (
            fetch_zt_pool, fetch_dt_pool, fetch_zbgc_pool, fetch_strong_pool,
            fetch_concept_heat,
        )
        from fetcher.eastmoney import fetch_sector_flow, fetch_lhb
        from fetcher.lhb_local import fetch_lhb_local
        from fetcher.market_sentiment import (
            fetch_northbound_flow, fetch_hot_rank_up, fetch_xq_hot,
            fetch_big_deal,
        )
        from fetcher.concept_flow import fetch_concept_flow
        from fetcher.realtime_quote import fetch_realtime_snapshot
        from fetcher.eastmoney import (fetch_margin, fetch_block_trade, fetch_holder_count,
                                       fetch_lockup_expiry, fetch_dividend_history,
                                       fetch_industry_ranking, fetch_ths_hot_stocks)
        from fetcher.fundamentals import fetch_fundamentals_finance, fetch_fundamentals_f10
    except Exception as e:
        with _fetch_lock:
            _fetch_state.update({"status": "done", "results": [{"name": "导入失败", "ok": False, "error": str(e)}], "ts": _dt.now().strftime("%H:%M:%S")})
        return

    tasks = [
        ("财经快讯(财联社)",  fetch_cls),
        ("财联社红电报",      fetch_cls_red),
        ("东方财富快讯",      fetch_em),
        ("同花顺快讯",        fetch_ths),
        ("华尔街见闻",        fetch_wscn),
        ("第一财经",          fetch_yicai),
        ("金十数据",          fetch_jin10),
        ("格隆汇",            fetch_gelonghui),
        ("政策动态",          fetch_policy),
        ("行业资金流",        fetch_sector_flow),
        ("涨停池",            fetch_zt_pool),
        ("跌停池",            fetch_dt_pool),
        ("炸板池",            fetch_zbgc_pool),
        ("强势股",            fetch_strong_pool),
        ("概念热度",          fetch_concept_heat),
        ("龙虎榜",            fetch_lhb),
        ("龙虎榜席位",        fetch_lhb_local),
        ("北向资金",          fetch_northbound_flow),
        ("人气飙升",          fetch_hot_rank_up),
        ("雪球热度",          fetch_xq_hot),
        ("大单异动",          fetch_big_deal),
        ("市场实时脉冲",      fetch_realtime_snapshot),
        ("概念资金流",        fetch_concept_flow),
        ("融资融券",          fetch_margin),
        ("大宗交易",          fetch_block_trade),
        ("股东人数",          fetch_holder_count),
        ("基本面财务",        fetch_fundamentals_finance),
        ("基本面F10",         fetch_fundamentals_f10),
        ("行业排行",          fetch_industry_ranking),
        ("同花顺主题热股",    fetch_ths_hot_stocks),
        ("解禁减持",          fetch_lockup_expiry),
        ("分红历史",          fetch_dividend_history),
    ]

    results = []
    for name, fn in tasks:
        try:
            fn()
            results.append({"name": name, "ok": True})
        except Exception as e:
            results.append({"name": name, "ok": False, "error": str(e)[:80]})
        with _fetch_lock:
            _fetch_state["results"] = list(results)

    with _fetch_lock:
        _fetch_state.update({"status": "done", "ts": _dt.now().strftime("%H:%M:%S")})


@app.route("/api/fetch-all", methods=["POST"])
def api_fetch_all():
    """手动触发全量数据抓取（不受交易时段限制）。立即返回，后台执行。"""
    import threading
    with _fetch_lock:
        if _fetch_state["status"] == "running":
            return _ok({"started": False, "message": "抓取任务已在进行中"})
        _fetch_state.update({"status": "running", "results": []})
    t = threading.Thread(target=_run_fetch_all, daemon=True, name="fetch-all-worker")
    t.start()
    return _ok({"started": True})


@app.route("/api/fetch-all-status")
def api_fetch_all_status():
    """轮询抓取进度。"""
    with _fetch_lock:
        return _ok(dict(_fetch_state))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _serve(port: int) -> None:
    """用 waitress 生产级 WSGI 服务器启动，无开发警告。"""
    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=port, threads=8)
    except ImportError:
        # waitress 未安装时降级到 Flask 内置服务器
        app.run(host="0.0.0.0", port=port, debug=False,
                use_reloader=False, threaded=True)


def start_flask(port: int = 20026):
    """在后台线程启动 WSGI 服务器。"""
    import threading
    t = threading.Thread(target=lambda: _serve(port), daemon=True, name="flask-api")
    t.start()
    return t


if __name__ == "__main__":
    import signal, multiprocessing

    _port = int(os.environ.get("FLASK_PORT", 20026))

    from core.scheduler import start_scheduler
    start_scheduler()
    start_flask(_port)
    print(f"[server] 仪表盘已启动 → http://0.0.0.0:{_port}")


    def _shutdown(signum, frame):
        print("\n[server] 收到退出信号，正在终止子进程...")
        # 强制杀掉所有由本进程 fork 出的子进程（ProcessPoolExecutor workers）
        current = multiprocessing.current_process()
        for child in multiprocessing.active_children():
            child.terminate()
        raise SystemExit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # 主线程保持阻塞，让 daemon 子线程（flask/scheduler）持续运行
    try:
        signal.pause()          # Linux/macOS
    except AttributeError:
        import time             # Windows 没有 signal.pause
        while True:
            time.sleep(3600)

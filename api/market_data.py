"""api/market_data.py — 数据面板长尾域 Blueprint (自 server.py 拆出)。

只读薄路由合集: 新闻/政策、研报、板块概念资金流、龙虎榜、
涨跌停池 (zt/dt/dt-v3/zbgc/strong)、市场情绪、连板梯队、涨停密度、
热度排行/北向、大单/融资/大宗/股东户数/解禁/分红/行业排行/同花顺热股、
研报 PDF 代理。全部纯 DB 读 + _ok/_err 包装, 无模块级状态。

QMT 实时 5 路由 (qmt-breaker/overview/limit-down-monitor/industry-draggers/
industry-stats) 与 server.py QMT 缓存枢纽纠缠, 留待枢纽阶段处理。

url_prefix="/api"。
"""
import logging

from flask import Blueprint, request

from api.common import _computed_date, _date_or_none, _date_param, _err, _ok

logger = logging.getLogger(__name__)

bp = Blueprint("market_data", __name__, url_prefix="/api")


# ---------------------------------------------------------------------------
# News & Policy
# ---------------------------------------------------------------------------

@bp.route("/news")
def api_news():
    try:
        from db.storage import count_cls_news_by_source, get_cls_news_by_source
        source    = request.args.get("source", "财联社")
        page_size = int(request.args.get("page_size", 30))
        page      = int(request.args.get("page", 1))
        offset    = (page - 1) * page_size
        rows  = get_cls_news_by_source(source, page_size, offset)
        total = count_cls_news_by_source(source)
        return _ok({"items": rows, "total": total, "page": page, "page_size": page_size})
    except Exception as exc:
        return _err(exc)


@bp.route("/policy")
def api_policy():
    try:
        from db.storage import (
            count_policy_news_by_source,
            get_policy_news,
            get_policy_news_by_source,
        )
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

@bp.route("/research")
def api_research():
    try:
        from db.storage import get_research_reports
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

@bp.route("/sector-flow")
def api_sector_flow():
    try:
        from db.storage import get_sector_flow_latest
        source_type = request.args.get("type", "industry")
        rows = get_sector_flow_latest(source_type)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@bp.route("/concept-flow")
def api_concept_flow():
    try:
        from db.storage import get_concept_flow_latest
        top_n = int(request.args.get("top_n", 30))
        rows = get_concept_flow_latest(top_n)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Dragon-Tiger List
# ---------------------------------------------------------------------------

@bp.route("/lhb")
def api_lhb():
    try:
        from db.storage import get_lhb_data, get_lhb_seat
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


@bp.route("/lhb-local")
def api_lhb_local():
    """龙虎榜本地版：只读 lhb_seat 表（由本地 CSV 写入），无数据返回空列表。"""
    try:
        from db.storage import get_lhb_seat
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
# Pools (纯 DB 存档池; QMT 实时路由留 server.py)
# ---------------------------------------------------------------------------

@bp.route("/zt-pool")
def api_zt_pool():
    try:
        from db.storage import get_zt_pool
        trade_date = _date_param()
        rows = get_zt_pool(trade_date)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@bp.route("/dt-pool")
def api_dt_pool():
    try:
        from db.storage import get_dt_pool
        trade_date = _date_param()
        rows = get_dt_pool(trade_date)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@bp.route("/dt-pool-v3")
def api_dt_pool_v3():
    try:
        from db.storage import get_dt_pool_v3
        trade_date = _date_param()
        rows = get_dt_pool_v3(trade_date)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@bp.route("/zbgc-pool")
def api_zbgc_pool():
    try:
        from db.storage import get_zbgc_pool
        trade_date = _date_param()
        rows = get_zbgc_pool(trade_date)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@bp.route("/strong-pool")
def api_strong_pool():
    try:
        from db.storage import get_strong_pool
        trade_date = _date_param()
        rows = get_strong_pool(trade_date)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Market emotion & pulse
# ---------------------------------------------------------------------------

@bp.route("/market-emotion")
def api_market_emotion():
    try:
        from db.storage import get_market_emotion_summary
        trade_date = _date_or_none()
        data = get_market_emotion_summary(trade_date)
        return _ok(data)
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Lianzban (consecutive limit-up) statistics
# ---------------------------------------------------------------------------

@bp.route("/lianzban-stats")
def api_lianzban_stats():
    try:
        from db.storage import get_lianzban_stats
        days = int(request.args.get("days", 30))
        data = get_lianzban_stats(days)
        return _ok(data)
    except Exception as exc:
        return _err(exc)


@bp.route("/lianzban-chain")
def api_lianzban_chain():
    try:
        from db.storage import get_lianzban_chain
        trade_date = _computed_date()
        rows = get_lianzban_chain(trade_date)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# ZT density
# ---------------------------------------------------------------------------

@bp.route("/sector-zt-density")
def api_sector_zt_density():
    try:
        from db.storage import get_sector_zt_density
        trade_date = _computed_date()
        rows = get_sector_zt_density(trade_date)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@bp.route("/concept-zt-density")
def api_concept_zt_density():
    try:
        from db.storage import get_concept_zt_density
        trade_date = _computed_date()
        top_n = int(request.args.get("top_n", 15))
        rows = get_concept_zt_density(trade_date, top_n)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Hot rank & Northbound
# ---------------------------------------------------------------------------

@bp.route("/hot-rank-up")
def api_hot_rank_up():
    try:
        from db.storage import get_hot_rank_up_latest
        top_n = int(request.args.get("top_n", 20))
        rows = get_hot_rank_up_latest(top_n)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@bp.route("/northbound-flow")
def api_northbound_flow():
    try:
        from db.storage import get_northbound_flow_latest
        data = get_northbound_flow_latest()
        return _ok(data)
    except Exception as exc:
        return _err(exc)


@bp.route("/xq-hot")
def api_xq_hot():
    try:
        from db.storage import get_xq_hot_latest
        top_n = int(request.args.get("top_n", 30))
        rows = get_xq_hot_latest(top_n)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# 大单 / 融资 / 大宗 / 股东户数 / 解禁 / 分红 / 行业排行 / 同花顺热股
# ---------------------------------------------------------------------------

@bp.route("/big-deal")
def api_big_deal():
    try:
        from db.storage import get_big_deal_latest
        limit = int(request.args.get("limit", 50))
        rows = get_big_deal_latest(limit)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@bp.route("/margin")
def api_margin():
    try:
        from db.storage import get_margin_latest
        top_n = int(request.args.get("top_n", 50))
        rows = get_margin_latest(top_n)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@bp.route("/block-trade")
def api_block_trade():
    try:
        from db.storage import get_block_trade_latest
        limit = int(request.args.get("limit", 50))
        rows = get_block_trade_latest(limit)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@bp.route("/holder-count")
def api_holder_count():
    try:
        from db.storage import get_holder_count_latest
        top_n = int(request.args.get("top_n", 50))
        rows = get_holder_count_latest(top_n)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@bp.route("/lockup-expiry")
def api_lockup_expiry():
    try:
        from db.storage import get_lockup_expiry
        days = int(request.args.get("days", 30))
        rows = get_lockup_expiry(days)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@bp.route("/dividend")
def api_dividend():
    try:
        from db.storage import get_dividend_latest
        limit = int(request.args.get("limit", 100))
        rows = get_dividend_latest(limit)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@bp.route("/industry-ranking")
def api_industry_ranking():
    try:
        from db.storage import get_industry_ranking_latest
        rows = get_industry_ranking_latest()
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


@bp.route("/ths-hot-stocks")
def api_ths_hot_stocks():
    try:
        from db.storage import get_ths_hot_stocks_latest
        top_n = int(request.args.get("top_n", 50))
        rows = get_ths_hot_stocks_latest(top_n)
        return _ok(rows)
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# 研报 PDF 代理 (注入 Referer 绕过东财 403)
# ---------------------------------------------------------------------------

@bp.route("/research/pdf")
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

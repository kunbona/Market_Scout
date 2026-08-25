"""api/fetch_all.py — 手动全量抓取域 (自 server.py 拆出)。

2 路由: POST /api/fetch-all (后台线程跑 30 项抓取) +
GET /api/fetch-all-status (轮询进度)。

状态挂在共享模块 core.fetch_status (fetch_state/fetch_lock),
scheduler 的自动抓取也写同一份状态, 故继续从那里导入。

url_prefix="/api"。
"""
from flask import Blueprint

from api.common import _ok
from core.fetch_status import fetch_state as _fetch_state, fetch_lock as _fetch_lock

bp = Blueprint("fetch_all", __name__, url_prefix="/api")


def _run_fetch_all():
    """在后台线程执行全量抓取，不受交易时段限制。"""
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
        from fetcher.eastmoney import (fetch_margin, fetch_block_trade, fetch_holder_count,
                                       fetch_lockup_expiry, fetch_dividend_history,
                                       fetch_industry_ranking, fetch_ths_hot_stocks)

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
        ("概念资金流",        fetch_concept_flow),
        ("融资融券",          fetch_margin),
        ("大宗交易",          fetch_block_trade),
        ("股东人数",          fetch_holder_count),

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


@bp.route("/fetch-all", methods=["POST"])
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


@bp.route("/fetch-all-status")
def api_fetch_all_status():
    """轮询抓取进度。"""
    with _fetch_lock:
        return _ok(dict(_fetch_state))

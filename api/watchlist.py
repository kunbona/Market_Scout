"""api/watchlist.py — 关注股池域 Blueprint (自 server.py 拆出)。

四段合一:
- Watchlist CRUD + 股池分组管理 (/api/watchlist*, /api/pools*)
- 实时报价合并 (/api/watchlist/quote, QMT tick)
- 关注股票情报聚合 (研报+快讯+政策+智堡 四路 + 个股/全池 AI 简报 job)
- iFinD 实时体检 (全池串行体检 job, 不用等 agent 跑)

url_prefix="/api/watchlist" (/api/pools 例外, 单独挂 /api 前缀的子 bp)。
"""
import logging
import os
import threading
from datetime import datetime
from pathlib import Path

from flask import Blueprint, request

from api.common import _err, _ok

logger = logging.getLogger(__name__)

bp = Blueprint("watchlist", __name__, url_prefix="/api/watchlist")
# 股池分组管理路径是 /api/pools, 单独一个 bp
pools_bp = Blueprint("watchlist_pools", __name__, url_prefix="/api/pools")

# 项目根: 本文件在 api/ 子目录, iFinD 配置文件在项目根
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _normalize_stock_code(raw: str) -> str:
    """归一化为 6 位数字代码；非法输入返回空串。"""
    from agent.stock_search import _normalize_code
    return _normalize_code(raw)


def _fill_stock_name(code: str) -> str:
    """名称自动补全：先查全量名称索引，回落本地量价 CSV 查名。"""
    try:
        from agent.stock_search import lookup_name
        return lookup_name(code)
    except Exception:
        return ""


def _resolve_pool(raw: str | None) -> str:
    """池名规整：去首尾空格，空则'默认'。"""
    from db.storage import DEFAULT_POOL
    name = (raw or "").strip()
    return name if name else DEFAULT_POOL


@bp.route("", methods=["GET"])
def api_watchlist_list():
    """返回关注股池列表。?pool=xxx 过滤单个池，不带返回全部（每项带 pool 字段）。"""
    try:
        from db.storage import get_watchlist
        pool = (request.args.get("pool", "") or "").strip() or None
        return _ok(get_watchlist(pool))
    except Exception as exc:
        return _err(exc)


# ── 股池分组管理 ──────────────────────────────────────────────────────────────

@pools_bp.route("", methods=["GET"])
def api_pools_list():
    """池列表（含每个池股票数量）。"""
    try:
        from db.storage import list_pools
        return _ok(list_pools())
    except Exception as exc:
        return _err(exc)


@pools_bp.route("", methods=["POST"])
def api_pools_create():
    """新建股池。body: {"name": "策略A"}（≤20 字符，重名/保留名 400）。"""
    try:
        from db.storage import create_pool, pool_exists
        body = request.get_json(silent=True) or {}
        name = str(body.get("name", "") or "").strip()
        if not name or len(name) > 20:
            return _err("池名需为 1-20 个字符", 400)
        if name in ("全部",):
            return _err(f"「{name}」为保留名称", 400)
        if pool_exists(name):
            return _err(f"股池「{name}」已存在", 400)
        create_pool(name)
        return _ok({"name": name})
    except Exception as exc:
        return _err(exc)


@pools_bp.route("/<name>", methods=["PUT"])
def api_pools_rename(name: str):
    """池改名，池内股票跟随。body: {"new_name": "策略B"}。'默认'池拒绝。"""
    try:
        from db.storage import DEFAULT_POOL, pool_exists, rename_pool
        old = (name or "").strip()
        if old == DEFAULT_POOL:
            return _err("「默认」池不允许改名", 400)
        body = request.get_json(silent=True) or {}
        new_name = str(body.get("new_name", "") or "").strip()
        if not new_name or len(new_name) > 20:
            return _err("池名需为 1-20 个字符", 400)
        if new_name in ("全部", DEFAULT_POOL):
            return _err(f"「{new_name}」为保留名称", 400)
        if not pool_exists(old):
            return _err(f"股池「{old}」不存在", 404)
        if pool_exists(new_name):
            return _err(f"股池「{new_name}」已存在", 400)
        if not rename_pool(old, new_name):
            return _err("改名失败", 400)
        return _ok({"old": old, "new": new_name})
    except Exception as exc:
        return _err(exc)


@pools_bp.route("/<name>", methods=["DELETE"])
def api_pools_delete(name: str):
    """删除股池并连带删除池内股票。'默认'池拒绝。"""
    try:
        from db.storage import DEFAULT_POOL, delete_pool, pool_exists
        pool = (name or "").strip()
        if pool == DEFAULT_POOL:
            return _err("「默认」池不允许删除", 400)
        if not pool_exists(pool):
            return _err(f"股池「{pool}」不存在", 404)
        removed = delete_pool(pool)
        if removed is None:
            return _err("删除失败", 400)
        return _ok({"deleted_pool": pool, "removed_stocks": removed})
    except Exception as exc:
        return _err(exc)


@bp.route("/search")
def api_watchlist_search():
    """模糊搜索股票（代码 / 拼音简写 / 中文名片段），供前端输入联想。"""
    try:
        from agent.stock_search import search_stocks
        q = request.args.get("q", "").strip()
        if not q:
            return _ok([])
        return _ok(search_stocks(q, limit=10))
    except Exception as exc:
        return _err(exc)


@bp.route("", methods=["POST"])
def api_watchlist_add():
    """添加关注股票。body: {"code": "600519", "note": "可选", "pool": "缺省默认"} 或 {"q": "gzmt|茅台|600519.SH"}。
    q 唯一命中直接添加；多命中返回 {"multiple": true, "candidates": [...]} 由前端选择。
    指定 pool 时池必须已存在（防止手误建新池），否则 400。
    """
    try:
        from agent.stock_search import search_stocks
        from db.storage import add_watchlist, pool_exists
        body = request.get_json(silent=True) or {}
        note = str(body.get("note", "") or "")[:200]
        q = str(body.get("q", "") or "").strip()
        pool = _resolve_pool(body.get("pool"))
        if not pool_exists(pool):
            return _err(f"股池「{pool}」不存在，请先创建该池", 400)

        if q:
            hits = search_stocks(q, limit=10, prefix_abbr=False)
            if not hits:
                return _err(f"未找到与「{q}」匹配的股票（支持代码 / 拼音简写 / 中文名片段）", 400)
            if len(hits) > 1:
                return _ok({"multiple": True, "candidates": hits})
            code = hits[0]["code"]
            name = hits[0].get("name") or _fill_stock_name(code)
        else:
            code = _normalize_stock_code(str(body.get("code", "")))
            if not code:
                return _err("无效的股票代码（需为 6 位数字，或使用 q 参数模糊搜索）", 400)
            name = _fill_stock_name(code)

        item = add_watchlist(code, name=name, note=note, pool=pool)
        return _ok(item)
    except Exception as exc:
        return _err(exc)


@bp.route("/import", methods=["POST"])
def api_watchlist_import():
    """批量导入股池。支持 multipart 文件上传（字段 file，可加字段 pool）或 JSON {"text": "...", "pool": "..."}。
    宽容解析：自动识别表头、多种代码写法、注释行；文件内与目标池现有股票去重。
    指定 pool 时池必须已存在，否则 400。
    响应: {"added": n, "skipped_existing": n, "failed": [...], "total": n, "pool": "..."}
    """
    try:
        from agent.stock_search import parse_import_text
        from db.storage import add_watchlist, get_watchlist, pool_exists

        text = ""
        pool_raw: str | None = None
        if request.files:
            f = request.files.get("file")
            if f is None:
                return _err("multipart 请求中未找到 file 字段", 400)
            pool_raw = request.form.get("pool")
            raw = f.read()
            for enc in ("utf-8-sig", "gbk", "utf-8"):
                try:
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            if not text:
                return _err("文件编码无法识别（支持 UTF-8 / GBK）", 400)
        else:
            body = request.get_json(silent=True) or {}
            text = str(body.get("text", "") or "")
            pool_raw = body.get("pool")

        if not text.strip():
            return _err("导入内容为空", 400)

        pool = _resolve_pool(pool_raw)
        if not pool_exists(pool):
            return _err(f"股池「{pool}」不存在，请先创建该池", 400)

        rows, failed = parse_import_text(text)
        existing = {item["code"] for item in get_watchlist(pool)}

        added = 0
        skipped_existing = 0
        for row in rows:
            if row["code"] in existing:
                skipped_existing += 1
                continue
            name = _fill_stock_name(row["code"])
            add_watchlist(row["code"], name=name, note=row.get("note", ""), pool=pool)
            existing.add(row["code"])
            added += 1

        return _ok({
            "added": added,
            "skipped_existing": skipped_existing,
            "failed": failed,
            "total": len(rows) + len(failed),
            "pool": pool,
        })
    except Exception as exc:
        return _err(exc)


@bp.route("/<code>", methods=["DELETE"])
def api_watchlist_remove(code: str):
    """从股池删除一只股票。?pool=xxx 指定池（缺省'默认'）。"""
    try:
        from db.storage import remove_watchlist
        norm = _normalize_stock_code(code)
        if not norm:
            return _err("无效的股票代码", 400)
        pool = _resolve_pool(request.args.get("pool"))
        removed = remove_watchlist(norm, pool)
        if not removed:
            return _err(f"该股不在股池「{pool}」中", 404)
        return _ok({"removed": norm, "pool": pool})
    except Exception as exc:
        return _err(exc)


@bp.route("/<code>", methods=["PUT"])
def api_watchlist_update(code: str):
    """更新股池股票备注。body: {"note": "...", "pool": "缺省默认"}"""
    try:
        from db.storage import update_watchlist_note
        norm = _normalize_stock_code(code)
        if not norm:
            return _err("无效的股票代码", 400)
        body = request.get_json(silent=True) or {}
        note = str(body.get("note", "") or "")[:200]
        pool = _resolve_pool(body.get("pool"))
        updated = update_watchlist_note(norm, note, pool)
        if not updated:
            return _err(f"该股不在股池「{pool}」中", 404)
        return _ok({"code": norm, "note": note, "pool": pool})
    except Exception as exc:
        return _err(exc)


@bp.route("/quote")
def api_watchlist_quote():
    """
    关注股池 + QMT 实时 tick 合并返回。

    ?pool=xxx 过滤单个池，不带返全部（每项带 pool 字段）。
    返回每只股票：watchlist 字段 + last_price / last_close / change / change_pct / open / high / low / volume / amount
    QMT 不可用 / bridge 离线时只返 watchlist 字段，报价字段为 null。
    """
    try:
        import time as _time
        from fetcher.qmt_data_api import get_full_tick_snapshot
        from fetcher import qmt_breaker
        from db.storage import get_watchlist

        pool = (request.args.get("pool", "") or "").strip() or None
        items = get_watchlist(pool)

        if not items:
            return _ok({
                "items": [],
                "quote_time": None,
                "quote_source": None,
                "bridge_state": qmt_breaker.snapshot().get("state"),
            })

        codes = [it["code"] for it in items if it.get("code")]
        t0 = _time.time()
        ticks = get_full_tick_snapshot(codes)
        elapsed_ms = round((_time.time() - t0) * 1000, 1)

        # A 股：红涨绿跌
        # change = last_price - last_close
        # change_pct = (last_price - last_close) / last_close * 100
        quote_time = None
        merged = []
        ok_count = 0
        for it in items:
            code = it.get("code", "")
            tick = ticks.get(code) or {}
            lp = tick.get("last_price") or 0.0
            lc = tick.get("last_close") or 0.0
            has_quote = bool(lp and lc)
            if has_quote:
                ok_count += 1
                change = round(lp - lc, 4)
                change_pct = round((lp - lc) / lc * 100, 2) if lc else 0.0
            else:
                change = 0.0
                change_pct = 0.0
            # 拿最新成交时间
            raw = tick.get("raw") or {}
            qt = raw.get("time") or raw.get("datetime")
            if qt is not None and quote_time is None and has_quote:
                quote_time = str(qt)
            merged.append({
                **it,
                "last_price": lp if has_quote else None,
                "last_close": lc if has_quote else None,
                "open": tick.get("open") or None,
                "high": tick.get("high") or None,
                "low": tick.get("low") or None,
                "volume": tick.get("volume") or None,
                "amount": tick.get("amount") or None,
                "change": change if has_quote else None,
                "change_pct": change_pct if has_quote else None,
                "has_quote": has_quote,
            })

        bridge_snap = qmt_breaker.snapshot()
        return _ok({
            "items": merged,
            "quote_time": quote_time,
            "quote_elapsed_ms": elapsed_ms,
            "quote_count": ok_count,
            "total_count": len(items),
            "quote_source": "QMT" if ok_count > 0 else None,
            "bridge_state": bridge_snap.get("state"),
            "bridge_offline_secs": bridge_snap.get("offline_secs", 0),
        })
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# 关注股票情报聚合（研报 + 财经快讯 + 政策 + 智堡）
# ---------------------------------------------------------------------------

_WATCHLIST_INTEL_JOB = {
    "state": "idle",        # idle | running | done | error
    "code": None,
    "name": None,
    "result": None,         # {markdown, code, name}
    "error": None,
    "started_at": None,
    "finished_at": None,
}
_watchlist_intel_lock = threading.Lock()


def _watchlist_intel_collect(code: str, name: str) -> dict:
    """聚合四路信息源（研报/快讯/政策/智堡）+ 股池动态分析的逐股体检，返回 {code,name,sources,checkup}。"""
    from db.storage import (
        search_research_by_stock,
        search_cls_news_by_keyword,
        search_policy_by_keyword,
    )
    research = search_research_by_stock(code, limit=10)
    news = search_cls_news_by_keyword(name, limit=10)
    policy = search_policy_by_keyword(name, limit=10)
    wisburg: list[dict] = []
    try:
        from agent.wisburg_ai import list_resource
        _seen_ids: set = set()
        for res in ("reports", "feed", "articles"):
            try:
                items, _ = list_resource(res, first=5, query=name)
                for it in items:
                    _id = it.get("id")
                    if _id in _seen_ids:
                        continue  # reports/feed 共用 id，去重
                    _seen_ids.add(_id)
                    wisburg.append({**it, "source_type": res})
            except Exception:
                continue
        wisburg.sort(key=lambda x: str(x.get("datetime") or ""), reverse=True)
    except Exception:
        pass
    return {
        "code": code,
        "name": name,
        "sources": {
            "research": research or [],
            "news": news or [],
            "policy": policy or [],
            "wisburg": wisburg[:10] or [],
        },
        "checkup": _get_stock_checkup(code),
    }


# 股池动态分析逐股体检缓存（5 分钟，避免每只股票重复查库）— core/cache TTLCache
from core.cache import TTLCache as _TTLCache

_checkup_cache = _TTLCache(ttl=300)


def _get_stock_checkup(code: str) -> dict:
    """从最近一次「股池动态分析」（agent_summary run_type=watchlist）结果里取该股的
    问题提醒 issues / 优势亮点 highlights / 涨跌幅。返回 {issues, highlights, change_pct}，无则空。"""
    def _load_all() -> dict:
        from db.storage import get_agent_summary_latest_snapshot
        cache: dict = {}
        snap = get_agent_summary_latest_snapshot("watchlist")
        if snap and snap.get("stocks"):
            for s in snap["stocks"]:
                cache[str(s.get("code"))] = {
                    "issues": s.get("issues") or [],
                    "highlights": s.get("highlights") or [],
                    "change_pct": s.get("change_pct"),
                    "analyze_ok": s.get("analyze_ok"),
                }
        return cache or None

    all_checkups = _checkup_cache.get_or_set("all", _load_all) or {}
    return all_checkups.get(str(code)) or {}


@bp.route("/intel")
def api_watchlist_intel():
    """关注股票情报聚合。?code=xxx&name=xxx → 按研报/快讯/政策/智堡四组返回。"""
    try:
        code = (request.args.get("code") or "").strip()
        name = (request.args.get("name") or "").strip()
        if not code or not name:
            return _err("缺少 code/name 参数", 400)
        return _ok(_watchlist_intel_collect(code, name))
    except Exception as exc:
        return _err(exc)


def _watchlist_intel_ai_worker(code: str, name: str) -> None:
    """后台生成 AI 综合简报（claude 串行，约 1-3 分钟）。"""
    from agent.wisburg_ai import call_claude
    job = _WATCHLIST_INTEL_JOB
    try:
        collected = _watchlist_intel_collect(code, name)
        src = collected["sources"]

        def _fmt(items, keys: tuple[str, ...]) -> str:
            lines = []
            for it in items:
                title = it.get("title", "")
                ts = it.get("publish_date") or it.get("pub_time") or it.get("datetime") or ""
                extra = " ".join(str(it.get(k) or "") for k in keys)
                summary = it.get("summary") or it.get("content") or it.get("description") or ""
                if isinstance(summary, str):
                    summary = summary[:200]
                else:
                    summary = ""
                lines.append(f"- [{str(ts)[:16]}] {title} {extra}\n  {summary}")
            return "\n".join(lines) if lines else "（无）"

        research_txt = _fmt(src["research"], ("org_name", "rating", "aim_price"))
        news_txt = _fmt(src["news"], ("source",))
        policy_txt = _fmt(src["policy"], ("source",))
        wisburg_txt = _fmt(src["wisburg"], ("source_type",))

        prompt = f"""你是一位资深投研分析师。下面是关注股票「{name}（{code}）」从四个信息源聚合到的相关资料，请做综合整理，输出一份个股情报简报。

## 一、券商研报（{len(src['research'])} 条）
{research_txt}

## 二、财经快讯（{len(src['news'])} 条）
{news_txt}

## 三、政策动态（{len(src['policy'])} 条）
{policy_txt}

## 四、智堡研究（{len(src['wisburg'])} 条）
{wisburg_txt}

请输出 markdown（不要代码块包裹），严格按以下结构：
1. `## 一句话概括` — 这只股票当前的核心状态
2. `## 研报观点` — 机构评级/目标价/核心逻辑（引用具体机构和评级）
3. `## 近期动态` — 快讯里的关键事件（业绩/公告/异动），按时间
4. `## 政策与行业` — 政策动态和智堡研究里与它相关的行业/宏观背景
5. `## 风险与关注点` — 值得注意的风险信号或待验证的逻辑

约束：只用给定资料里的信息，不编造；每条结论尽量标注来源；总长 600-1000 字。"""

        md = call_claude(prompt)
        job["result"] = {"markdown": md, "code": code, "name": name}
        job["state"] = "done"
    except Exception as exc:
        job["state"] = "error"
        job["error"] = str(exc)
        logger.exception("[watchlist-intel] AI 简报失败")
    finally:
        job["finished_at"] = datetime.now().isoformat(timespec="seconds")


@bp.route("/intel/ai", methods=["POST"])
def api_watchlist_intel_ai():
    """生成 AI 综合简报（后台任务，轮询 /api/watchlist/intel/ai-job）。"""
    try:
        body = request.get_json(silent=True) or {}
        code = (body.get("code") or "").strip()
        name = (body.get("name") or "").strip()
        if not code or not name:
            return _err("缺少 code/name 参数", 400)
        with _watchlist_intel_lock:
            if _WATCHLIST_INTEL_JOB["state"] == "running":
                return _ok({"started": False, "state": "running"})
            _WATCHLIST_INTEL_JOB.update(
                state="running", code=code, name=name, result=None, error=None,
                started_at=datetime.now().isoformat(timespec="seconds"), finished_at=None,
            )
            threading.Thread(target=_watchlist_intel_ai_worker, args=(code, name),
                             daemon=True, name="watchlist-intel-ai").start()
        return _ok({"started": True, "state": "running"})
    except Exception as exc:
        return _err(exc)


@bp.route("/intel/ai-job")
def api_watchlist_intel_ai_job():
    with _watchlist_intel_lock:
        return _ok(dict(_WATCHLIST_INTEL_JOB))


# ── 全池 AI 整理（一键整理里的 AI 横向分析）────────────────────

_WATCHLIST_INTEL_ALL_JOB = {
    "state": "idle",        # idle | running | done | error
    "result": None,         # {markdown, count}
    "error": None,
    "started_at": None,
    "finished_at": None,
}
_watchlist_intel_all_lock = threading.Lock()


def _watchlist_intel_all_worker(stocks: list[dict]) -> None:
    """后台聚合全池情报喂 claude，生成全池横向整理分析（约 1-3 分钟）。"""
    from agent.wisburg_ai import call_claude
    job = _WATCHLIST_INTEL_ALL_JOB
    try:
        parts = []
        for s in stocks:
            code = str(s.get("code") or "").strip()
            name = str(s.get("name") or "").strip()
            if not code or not name:
                continue
            collected = _watchlist_intel_collect(code, name)
            src = collected["sources"]
            def _brief(items, keys: tuple[str, ...], n: int = 3) -> str:
                out = []
                for it in items[:n]:
                    title = str(it.get("title") or "")
                    extra = " ".join(str(it.get(k) or "") for k in keys)
                    out.append(f"{title}{'（' + extra + '）' if extra else ''}")
                if len(items) > n:
                    out.append(f"…共{len(items)}条")
                return "；".join(out) if out else "无"
            parts.append(
                f"### {name}（{code}）\n"
                f"- 研报{len(src['research'])}条：{_brief(src['research'], ('org_name', 'rating'))}\n"
                f"- 快讯{len(src['news'])}条：{_brief(src['news'], ('source',))}\n"
                f"- 政策{len(src['policy'])}条：{_brief(src['policy'], ('source',))}\n"
                f"- 智堡{len(src['wisburg'])}条：{_brief(src['wisburg'], ('source_type',))}"
            )

        if not parts:
            raise RuntimeError("没有可分析的关注股票")

        prompt = f"""你是一位资深投研分析师。下面是关注股池 {len(parts)} 只股票的聚合情报（券商研报/财经快讯/政策动态/智堡研究）。请做全池横向整理分析，帮用户快速把握整个股池的状态。

## 股票情报
{chr(10).join(parts)}

请输出 markdown（不要代码块包裹），严格按以下结构：
1. `## 全池概览` — 一句话总结整个池子当前的状态（热点方向/情绪/信息密集度）
2. `## 个股速览` — 每只股票 1-2 句（当前核心逻辑 + 值得关注的点）
3. `## 横向对比` — 池内谁研报/关注度最高、谁信息最少最冷清；有没有共性主题（如 AI、半导体、铝业）
4. `## 风险提示` — 池内出现的风险信号（评级下调、业绩下滑、政策收紧等）
5. `## 操作线索` — 值得进一步研究的 2-3 条线索

约束：只用给定资料里的信息，不编造；每条结论尽量标注股票；总长 800-1500 字。"""

        md = call_claude(prompt)
        job["result"] = {"markdown": md, "count": len(parts)}
        job["state"] = "done"
    except Exception as exc:
        job["state"] = "error"
        job["error"] = str(exc)
        logger.exception("[watchlist-intel-all] 全池 AI 整理失败")
    finally:
        job["finished_at"] = datetime.now().isoformat(timespec="seconds")


@bp.route("/intel/ai-all", methods=["POST"])
def api_watchlist_intel_ai_all():
    """全池 AI 整理（后台任务，轮询 /api/watchlist/intel/ai-all-job）。body: {stocks: [{code,name}]}"""
    try:
        body = request.get_json(silent=True) or {}
        stocks = body.get("stocks") or []
        stocks = [s for s in stocks if (s.get("code") or "").strip() and (s.get("name") or "").strip()]
        if not stocks:
            return _err("缺少 stocks 参数", 400)
        with _watchlist_intel_all_lock:
            if _WATCHLIST_INTEL_ALL_JOB["state"] == "running":
                return _ok({"started": False, "state": "running"})
            _WATCHLIST_INTEL_ALL_JOB.update(
                state="running", result=None, error=None,
                started_at=datetime.now().isoformat(timespec="seconds"), finished_at=None,
            )
            threading.Thread(target=_watchlist_intel_all_worker, args=(stocks,),
                             daemon=True, name="watchlist-intel-all").start()
        return _ok({"started": True, "state": "running"})
    except Exception as exc:
        return _err(exc)


@bp.route("/intel/ai-all-job")
def api_watchlist_intel_ai_all_job():
    with _watchlist_intel_all_lock:
        return _ok(dict(_WATCHLIST_INTEL_ALL_JOB))


# ── iFinD 实时体检（复用股池动态分析的数据源，不用等 agent 跑）────────

_IFIND_CFG: dict | None = None


def _ifind_call(server_key: str, tool_name: str, args: dict, timeout: float = 30) -> str:
    """调 iFinD MCP 的 tools/call，返回文本内容。token 从项目根 ifind-mcp-config.txt 读。"""
    global _IFIND_CFG
    import json as _json
    import requests as _requests
    import urllib3
    urllib3.disable_warnings()
    if _IFIND_CFG is None:
        _cfg_path = _PROJECT_ROOT / "ifind-mcp-config.txt"
        _IFIND_CFG = _json.loads(_cfg_path.read_text(encoding="utf-8"))
    srv = _IFIND_CFG["mcpServers"].get(server_key)
    if not srv:
        raise RuntimeError(f"iFinD 服务未配置: {server_key}")
    resp = _requests.post(
        srv["url"],
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": tool_name, "arguments": args}},
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream",
                 "Authorization": srv["headers"]["Authorization"]},
        verify=False,
        timeout=timeout,
    )
    data = resp.json() if resp.text.strip() else {}
    if "error" in data:
        raise RuntimeError(f"iFinD {tool_name} 失败: {data['error']}")
    content = (data.get("result") or {}).get("content") or []
    for c in content:
        if c.get("type") == "text" and c.get("text"):
            return c["text"]
    return ""


def _ifind_parse(text: str) -> dict:
    """解析 iFinD 返回（data 字段是 JSON 字符串，可能是 answer 表格 / 数组 / 嵌套）。"""
    import json as _json
    try:
        d = _json.loads(text)
    except Exception:
        return {}
    data = d.get("data") if isinstance(d, dict) else None
    if isinstance(data, str):
        try:
            data = _json.loads(data)
        except Exception:
            data = None
    # 行情：{"answer": "markdown 表格"}
    if isinstance(data, dict) and "answer" in data:
        return {"answer": data["answer"]}
    # 新闻：{"data": "[...]"}
    if isinstance(data, dict) and isinstance(data.get("data"), str):
        try:
            return {"list": _json.loads(data["data"])}
        except Exception:
            return {}
    # 公告：数组
    if isinstance(data, list):
        return {"list": data}
    return {}


def _md_table_rows(answer: str) -> list[list[str]]:
    """解析 markdown 表格 → 行（跳过表头和分隔行）。"""
    rows = []
    for line in answer.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if all(set(c) <= set("-: ") for c in cells):
            continue
        rows.append(cells)
    return rows


def _ifind_stock_checkup(code: str, name: str) -> dict:
    """用 iFinD 实时拿行情/公告/新闻，规则化生成问题提醒/亮点（复用动态分析数据源）。"""
    import re
    from datetime import date, timedelta
    issues: list[str] = []
    highlights: list[str] = []
    change_pct: float | None = None
    price: float | None = None
    today = date.today().isoformat()
    start7 = (date.today() - timedelta(days=7)).isoformat()

    # 1. 行情（涨跌幅）—— iFinD 多指标问句列不稳定，用单一指标"今日涨跌幅"最稳
    try:
        txt = _ifind_call("hexin-ifind-ds-stock-mcp", "get_stock_performance",
                          {"query": f"{name} {code} 今日涨跌幅"})
        parsed = _ifind_parse(txt)
        rows = _md_table_rows(parsed.get("answer", ""))
        if len(rows) >= 2:
            header = rows[0]
            idx_chg = next((i for i, h in enumerate(header) if "涨跌幅" in h), None)
            idx_amt = next((i for i, h in enumerate(header) if "成交额" in h), None)
            for r in rows[1:]:
                if r and code in r[0]:
                    chg = None
                    if idx_chg is not None:
                        try:
                            chg = float(r[idx_chg])
                        except (TypeError, ValueError):
                            chg = None
                    amt = r[idx_amt] if idx_amt is not None and idx_amt < len(r) else ""
                    if chg is not None:
                        change_pct = chg
                        if chg <= -5:
                            issues.append(f"{today} 大跌 {chg:.2f}%（成交额 {amt}）（iFinD行情）")
                        elif chg >= 5:
                            highlights.append(f"{today} 大涨 {chg:.2f}%（成交额 {amt}）（iFinD行情）")
                        elif chg <= -3:
                            issues.append(f"{today} 下跌 {chg:.2f}%，需留意（iFinD行情）")
                        elif chg >= 3:
                            highlights.append(f"{today} 上涨 {chg:.2f}%（iFinD行情）")
                    break
    except Exception:
        pass

    # 2. 近期公告
    try:
        txt = _ifind_call("hexin-ifind-ds-news-mcp", "search_notice",
                          {"query": f"{name} {code} 公告", "time_start": start7, "time_end": today, "size": 5})
        for it in _ifind_parse(txt).get("list", [])[:3]:
            title = str(it.get("公告标题") or "").strip()
            if title:
                highlights.append(f"{title}（iFinD公告）" if not any(k in title for k in ("减持", "处罚", "诉讼", "亏损")) else f"{title}（iFinD公告，注意风险）")
    except Exception:
        pass

    # 3. 近期新闻
    try:
        txt = _ifind_call("hexin-ifind-ds-news-mcp", "search_news",
                          {"query": f"{name} {code} 新闻", "time_start": start7, "time_end": today, "size": 5})
        for it in _ifind_parse(txt).get("list", [])[:3]:
            title = str(it.get("资讯标题") or "").strip()
            dt = str(it.get("日期") or "")[:10]
            if title:
                highlights.append(f"{dt} {title}（iFinD新闻）")
    except Exception:
        pass

    return {"issues": issues, "highlights": highlights, "change_pct": change_pct,
            "price": price, "source": "iFinD实时"}


# 全池实时体检 job
_WATCHLIST_REALTIME_JOB = {
    "state": "idle",        # idle | running | done | error
    "result": None,         # {checkups: {code: {...}}, count}
    "error": None,
    "started_at": None,
    "finished_at": None,
}
_watchlist_realtime_lock = threading.Lock()


def _watchlist_realtime_worker(stocks: list[dict]) -> None:
    """后台逐只 iFinD 体检（串行 + 限速防并发超限），进度写回 job。"""
    import time as _t
    job = _WATCHLIST_REALTIME_JOB
    try:
        checkups: dict = {}
        for i, s in enumerate(stocks):
            code = str(s.get("code") or "").strip()
            name = str(s.get("name") or "").strip()
            if code and name:
                checkups[code] = _ifind_stock_checkup(code, name)
            job["result"] = {"checkups": checkups, "count": len(stocks), "done": i + 1}
            _t.sleep(0.6)  # iFinD 免费并发 2/s，串行 + 间隔
        job["state"] = "done"
    except Exception as exc:
        job["state"] = "error"
        job["error"] = str(exc)
        logger.exception("[watchlist-realtime] iFinD 实时体检失败")
    finally:
        job["finished_at"] = datetime.now().isoformat(timespec="seconds")


@bp.route("/intel/realtime", methods=["POST"])
def api_watchlist_intel_realtime():
    """全池 iFinD 实时体检（后台任务，轮询 /api/watchlist/intel/realtime-job）。body: {stocks}"""
    try:
        body = request.get_json(silent=True) or {}
        stocks = [s for s in (body.get("stocks") or []) if (s.get("code") or "").strip()]
        if not stocks:
            return _err("缺少 stocks 参数", 400)
        with _watchlist_realtime_lock:
            if _WATCHLIST_REALTIME_JOB["state"] == "running":
                return _ok({"started": False, "state": "running"})
            _WATCHLIST_REALTIME_JOB.update(
                state="running", result=None, error=None,
                started_at=datetime.now().isoformat(timespec="seconds"), finished_at=None,
            )
            threading.Thread(target=_watchlist_realtime_worker, args=(stocks,),
                             daemon=True, name="watchlist-realtime").start()
        return _ok({"started": True, "state": "running"})
    except Exception as exc:
        return _err(exc)


@bp.route("/intel/realtime-job")
def api_watchlist_intel_realtime_job():
    with _watchlist_realtime_lock:
        return _ok(dict(_WATCHLIST_REALTIME_JOB))

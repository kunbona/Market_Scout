"""
智堡(Wisburg)投研数据 + AI 分析后端模块。

数据源: quant.dm_kun._wisburg 直连智堡开放 API (Bearer WISBURG_API_KEY)
AI: claude CLI (复用 agent/review_ai.call_claude 的模式, prompt 走文件避免 argv 超长)

供 server.py /api/wisburg/* 路由调用:
  - list_resource()   列表 (10 类数据源, 含 mikko-logs)
  - get_detail()      详情 (归一化 title/datetime/text/url/meta/html/images)
  - analyze_item()    单篇 AI 分析 (整理+总结+观点评估+预测)
  - build_briefing()  全局 AI 日报 (主题聚类+精选+投资启示+预测)

无 WISBURG_API_KEY 时 list/detail 抛 RuntimeError(含配置指引), 由路由转成 _err 提示。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

from quant.dm_kun._wisburg import (  # noqa: E402
    DETAIL_RESOURCES,
    RESOURCES,
    get_detail as _wisburg_get_detail,
    list_items as _wisburg_list_items,
)

# claude CLI 路径 (orchestrator/review_ai 已验证的稳定路径, 找不到回退 which)
CLAUDE_BIN = "/Users/kun/.nvm/versions/node/v24.15.0/bin/claude"
CLAUDE_TIMEOUT = 300  # 秒

# 9 类数据源元数据: key -> {label, detail, desc}
RESOURCE_META: dict[str, dict] = {
    "feed":            {"label": "资讯流",     "detail": False, "desc": "股票/债券/外汇/大宗/宏观/货币政策动态"},
    "market-daily":    {"label": "市场日报",   "detail": False, "desc": "每日股债汇商行情与宏观政策要闻摘要"},
    "articles":        {"label": "研究文章",   "detail": True,  "desc": "翻译与原创深度分析（含 Mikko 日报）"},
    "reports":         {"label": "投行研报",   "detail": True,  "desc": "海外投行 + 国内券商投研笔记"},
    "am-reports":      {"label": "资管研报",   "detail": True,  "desc": "资管公司投资策略与市场展望"},
    "company-reports": {"label": "企业研究",   "detail": True,  "desc": "单一上市公司研究报告"},
    "earningscalls":   {"label": "电话会纪要", "detail": True,  "desc": "业绩交流会 / 财报电话会纪要"},
    "archives":        {"label": "文献",       "detail": True,  "desc": "归档文献资料"},
    "images":          {"label": "图片流",     "detail": False, "desc": "智堡图片资讯流"},
    "mikko-logs":      {"label": "Mikko 日志", "detail": True,  "desc": "Mikko 资讯流日志: 短内容 markdown 正文 + 配图(宏观/政策/数据快讯)"},
}


def list_resource(
    resource: str,
    *,
    first: int = 20,
    query: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    after: str | None = None,
) -> tuple[list[dict], str | None]:
    """拉取资源列表, 返回 (items, 下一页游标)。"""
    return _wisburg_list_items(
        resource, first=first, query=query,
        start_time=start_time, end_time=end_time, after=after,
    )


def _extract_text(d: dict) -> str:
    """从详情 dict 提取正文 (优先 summary, 其次 body, 再 content/description)。"""
    for key in ("summary", "body", "content", "description"):
        v = d.get(key)
        if v:
            return str(v)
    return ""


def _strip_html(html: str) -> str:
    """把 HTML 剥成纯文本 (articles 的 body 是 HTML, 喂 AI 前先剥标签)。"""
    import re
    if not html:
        return ""
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    s = re.sub(r"</?(p|div|br|h[1-6]|li|tr|td|th|blockquote|pre|article|section|ul|ol)[^>]*>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", "", s)
    s = (s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
           .replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'"))
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n[ \t]+", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def get_detail(resource: str, item_id: int) -> dict:
    """拉取单条详情, 归一化成 {id,title,datetime,text,url,meta,html,images}。

    articles 的 body 是 HTML (text 剥成纯文本, 原样 HTML 另存 html 字段);
    reports 等是 markdown summary; mikko-logs 是 markdown content + images 配图。
    """
    if resource not in DETAIL_RESOURCES:
        raise ValueError(f"资源 {resource!r} 无详情接口 (可选: {', '.join(DETAIL_RESOURCES)})")
    d = _wisburg_get_detail(resource, item_id)
    html = None
    if resource == "articles":
        html = d.get("body")
        text = _strip_html(d.get("body") or "")
    else:
        text = _extract_text(d)
    return {
        "id": d.get("id", item_id),
        "title": d.get("title", ""),
        "datetime": d.get("datetime", ""),
        "text": text,
        "html": html,
        "url": d.get("url"),
        "meta": d.get("meta"),
        "images": d.get("images") or [],   # mikko-logs 配图 URL 列表 (其余资源为空)
    }


def _find_claude() -> str:
    if Path(CLAUDE_BIN).is_file():
        return CLAUDE_BIN
    found = shutil.which("claude")
    return found or CLAUDE_BIN


def call_claude(prompt: str, timeout: int = CLAUDE_TIMEOUT) -> str:
    """调 claude CLI, 返回 markdown 文本 (失败返回 ⚠️ 开头提示)。"""
    if not Path(_find_claude()).is_file() and not shutil.which("claude"):
        return "⚠️ claude CLI 不存在, 跳过 AI 分析"
    # prompt 走文件, 避免 argv 超长 (长报告内容可达几十 KB)
    tmp = Path("/tmp") / f"mra-wisburg-ai-{os.getpid()}.txt"
    tmp.write_text(prompt, encoding="utf-8")
    try:
        proc = subprocess.run(
            [_find_claude(), "-p", f"按 {tmp} 文件里的完整指示执行",
             "--output-format", "text",
             "--dangerously-skip-permissions"],
            cwd=str(PROJECT_ROOT), capture_output=True, text=True,
            timeout=timeout,
        )
        out = (proc.stdout or "").strip()
        if out:
            # 去掉 claude 偶尔复述 "按 /tmp/xxx 文件..." 的开场白
            if out.startswith("按 "):
                _idx = out.find("\n\n")
                if _idx != -1:
                    out = out[_idx + 2:].lstrip()
            return out
        return f"⚠️ AI 分析为空 (exit {proc.returncode}): {(proc.stderr or '')[:300]}"
    except subprocess.TimeoutExpired:
        return f"⚠️ AI 分析超时 ({timeout}s)"
    except Exception as e:
        return f"⚠️ AI 分析异常: {e}"
    finally:
        tmp.unlink(missing_ok=True)


def _detail_resource_for(resource: str) -> str | None:
    """该资源补全文应走哪个详情接口 (None = 无详情, 用列表字段)。

    feed / market-daily 的列表 item id 即 report id (全文在 reports 详情);
    其余有详情接口的走各自详情; images 无详情。
    """
    if resource in ("feed", "market-daily"):
        return "reports"
    return resource if resource in DETAIL_RESOURCES else None


def _enrich_item(resource: str, item: dict, max_chars: int = 900) -> dict:
    """把列表 item 补全为全文文本块, 返回 {title, datetime, content}。

    feed/market-daily 走 reports 详情; articles/reports/am-reports/company-reports/
    earningscalls/archives 走各自详情; images 无详情用列表 description。
    详情失败时回退到列表字段 (feed 摘要 / images description)。
    """
    detail_res = _detail_resource_for(resource)
    if detail_res is not None:
        try:
            d = get_detail(detail_res, item.get("id"))
            if d.get("text"):
                return {
                    "title": d["title"] or item.get("title", ""),
                    "datetime": d["datetime"] or item.get("datetime", ""),
                    "content": d["text"][:max_chars],
                }
        except Exception:
            pass  # 详情失败回退到列表字段
    text = _extract_text(item)
    return {
        "title": item.get("title", ""),
        "datetime": item.get("datetime", ""),
        "content": text[:max_chars],
    }


def _item_block(idx: int, it: dict, category: str = "") -> str:
    """文本块 (喂 AI), category 为来源类别标签 (综合日报用)。"""
    title = it.get("title", "")
    dt = it.get("datetime", "")
    text = it.get("content") or _extract_text(it)
    tag = f" [{category}]" if category else ""
    return f"[{idx}]{tag} {title}\n    时间: {dt}\n    {text}"


def analyze_item(resource: str, item_id: int) -> dict:
    """单篇 AI 分析: 整理 + 总结 + 观点评估 + 预测。返回 {title,datetime,markdown}。"""
    label = RESOURCE_META.get(resource, {}).get("label", resource)

    # 拿全文: feed/market-daily 走 reports 详情, 其余有详情走各自详情, images 用列表 description
    detail_res = _detail_resource_for(resource)
    if detail_res is not None:
        d = get_detail(detail_res, item_id)
        title, dt, text, url = d["title"], d["datetime"], d["text"], d["url"]
    else:
        items, _ = list_resource(resource, first=100)
        item = next((x for x in items if str(x.get("id")) == str(item_id)), None)
        if item is None:
            raise RuntimeError(f"在 {resource} 里找不到 id={item_id} 的条目")
        title = item.get("title", "")
        dt = item.get("datetime", "")
        text = _extract_text(item)
        url = item.get("cover_url")

    if not text:
        raise RuntimeError("该条目没有可分析的正文内容")

    prompt = f"""你是一位资深投研分析师。下面是智堡「{label}」的一篇内容, 请做深度分析。

## 来源
标题: {title}
时间: {dt}
{'原文链接: ' + url if url else ''}

## 原文内容
{text}

请输出 markdown (不要代码块包裹), 严格按以下结构:
1. `## 一句话结论` — 这篇内容的核心观点
2. `## 要点整理` — 3-6 个要点, 结构化分条
3. `## 关键数据与论据` — 引用的关键数据/逻辑链条
4. `## 观点评估` — 该观点/论据的合理性与局限, 有无值得警惕的偏颇
5. `## 市场影响与预测` — 对 A股及相关资产的中短期影响预判, 给出情景+概率+应对

约束: 只用原文信息, 不编造数字; 结论先行; 总长 600-1000 字。"""

    md = call_claude(prompt)
    return {"title": title, "datetime": dt, "markdown": md, "resource": resource}


def build_briefing(resource: str, items: list[dict], max_items: int = 20) -> str:
    """单类 AI 日报: 主题聚类 + 精选推荐 + 投资启示 + 预测 (逐条补全文)。"""
    label = RESOURCE_META.get(resource, {}).get("label", resource)
    if not items:
        return "## 无数据\n\n当前没有可分析的资讯。"

    items = items[:max_items]
    enriched = [_enrich_item(resource, it) for it in items]
    enriched = [e for e in enriched if e.get("content")]
    blocks = "\n\n".join(_item_block(i, e) for i, e in enumerate(enriched))

    prompt = f"""你是一位资深全球宏观策略师。下面是智堡「{label}」的 {len(enriched)} 条投研资讯(全文), 请做全局分析。

## 资讯全文
{blocks}

请输出 markdown (不要代码块包裹), 严格按以下结构:
1. `## 今日核心主题` — 3-5 个主题, 每个一段 (主题名 + 概述 + 涉及条数 + 代表机构/观点)
2. `## 多空分歧与矛盾` — 对立观点或值得警惕的矛盾信号; 若无明显矛盾则说明"观点高度一致"并概括方向
3. `## 精选推荐` — 最值得细读的 5-10 条, 每条给推荐理由 + 关键结论
4. `## 投资启示与预测` — 从短期情绪面 / 中期配置方向 / 需警惕的风险 / 值得追踪的线索四个维度给判断, 预测要有情景+概率

约束: 视角宏观不逐条罗列; 区分信号与噪音; 不编造; 总长 800-1500 字。"""

    return call_claude(prompt)


# 综合日报: 每类拉取条数 (跨 10 类, 全文截断控制总量)
BRIEFING_SOURCES: list[tuple[str, int]] = [
    ("feed",            12),
    ("market-daily",     3),
    ("articles",         6),
    ("reports",          8),
    ("am-reports",      10),
    ("company-reports", 10),
    ("earningscalls",   10),
    ("archives",        10),
    ("images",          10),
    ("mikko-logs",      10),
]


def build_briefing_all() -> dict:
    """综合 10 类数据源的全局 AI 日报。返回 {markdown, count, per_source}。

    跨类别拉取 + 逐条补全文, 让 AI 看到全貌后再做主题聚类/精选/预测。
    """
    all_items: list[tuple[str, dict]] = []
    per_source: dict[str, int] = {}
    for res, first in BRIEFING_SOURCES:
        try:
            items, _ = list_resource(res, first=first)
            per_source[res] = len(items)
            for it in items:
                all_items.append((res, it))
        except Exception:
            per_source[res] = 0

    if not all_items:
        return {"markdown": "## 无数据\n\n智堡各数据源都没有拉取到内容, 请检查 WISBURG_API_KEY。",
                "count": 0, "per_source": per_source}

    # 逐条补全文 (feed/market-daily 走 reports, 其余走各自详情)
    enriched: list[tuple[str, dict]] = []
    for res, it in all_items:
        e = _enrich_item(res, it)
        if e.get("content"):
            enriched.append((res, e))

    blocks = "\n\n".join(
        _item_block(i, e, RESOURCE_META[res]["label"])
        for i, (res, e) in enumerate(enriched)
    )

    prompt = f"""你是一位资深全球宏观策略师, 服务于一家顶级对冲基金。下面是智堡投研平台今日 {len(enriched)} 条内容, 涵盖 10 类数据源 (资讯流/市场日报/研究文章/投行研报/资管研报/企业研究/电话会纪要/文献/图片流/Mikko日志), 每条已标注来源类别。请做跨类别的全局分析与预测。

## 内容全文
{blocks}

请输出 markdown (不要代码块包裹), 严格按以下结构:
1. `## 今日核心主题` — 3-5 个主题, 每个一段 (主题名 + 概述 + 涉及条数与类别 + 代表机构/观点)
2. `## 多空分歧与矛盾` — 跨类别找对立观点或矛盾信号; 若无明显矛盾则说明"观点高度一致"并概括方向
3. `## 精选推荐` — 最值得细读的 5-10 条, 每条标注来源类别 + 推荐理由 + 关键结论
4. `## 投资启示与预测` — 短期情绪面 / 中期配置方向 / 需警惕的风险 / 值得追踪的线索, 预测要有情景+概率

约束: 视角宏观不逐条罗列; 区分信号与噪音; 不编造; 总长 1000-1800 字。"""

    md = call_claude(prompt)
    return {"markdown": md, "count": len(enriched), "per_source": per_source}


def resources_meta() -> list[dict]:
    """返回 10 类数据源元数据 (前端 tab 用)。"""
    return [
        {"key": k, "label": v["label"], "detail": v["detail"], "desc": v["desc"]}
        for k, v in RESOURCE_META.items()
    ]

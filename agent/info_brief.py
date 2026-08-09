"""
信息情报简报 (info_brief) 主程序。

流程:
1. 4 路读取 (cls_news / policy_news / policy_news 巨潮 / research)
2. 4 版本预处理 (A 贝叶斯 / B 事件研究 / C 源加权 / D 原子化)
3. 双层贝叶斯 (版本内 + 联合前叠加)
4. 阈值卡线 (按 run_type)
5. 联合分析 (跨路主题重叠)
6. 输出 JSON + HTML

调用方式:
  python agent/info_brief.py --run-type evening
  python agent/info_brief.py --run-type morning
  python agent/info_brief.py --run-type intraday
"""
import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from db.storage import (
    get_cls_news, get_policy_news, get_research_reports,
    insert_agent_summary,
)
from fetcher.info_brief_filter import (
    is_market_moving_notice, get_notice_type, NOTICE_TYPE_WEIGHT,
    classify_policy_type, classify_policy_phase,
    clean_caixin_title, extract_ticker, aggregate_multi_source,
)

# ── 阈值 (按 run_type) ─────────────────────────────────────────
THRESHOLDS = {
    "morning":  {"flash": 0.75, "policy": 0.65, "notice": 0.50, "research": 0.65},
    "intraday": {"flash": 0.60, "policy": 0.65, "notice": 0.50, "research": 0.65},
    "evening":  {"flash": 0.65, "policy": 0.65, "notice": 0.50, "research": 0.65},
}

# 跨路印证加分
CROSS_SOURCE_BONUS = {
    1: 1.0,   # 单独 1 路
    2: 1.5,   # 2 路
    3: 2.0,   # 3 路
    4: 2.5,   # 4 路全有
}

# ── 4 路读取 ─────────────────────────────────────────────
def load_flash(hours: int) -> List[Dict[str, Any]]:
    """财经快讯: 取最近 N 小时。"""
    rows = get_cls_news(limit=2000)  # 多取, 代码里按 hours 过滤
    cutoff = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    items = []
    for r in rows:
        pt = r.get("pub_time", "")
        if pt and pt >= cutoff:
            items.append({
                "source_category": "flash",
                "source": r.get("source", ""),
                "title": r.get("title", ""),
                "content": r.get("content", ""),
                "pub_time": pt,
                "ticker": extract_ticker(r.get("title", "")),
            })
    return aggregate_multi_source(items)


def load_policy(days: int = 3) -> List[Dict[str, Any]]:
    """政策动态: 全部 policy_news (近 N 日, 排除巨潮)。"""
    rows = get_policy_news(limit=2000)
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    items = []
    for r in rows:
        pt = r.get("pub_time", "")
        if not pt or pt < cutoff:
            continue
        if r.get("source") == "巨潮公告":
            continue  # 巨潮归到 notice 路
        items.append({
            "source_category": "policy",
            "source": r.get("source", ""),
            "title": clean_caixin_title(r.get("title", "")) if r.get("source") == "财新" else r.get("title", ""),
            "content": "",
            "pub_time": pt,
            "link": r.get("link", ""),
        })
    return items


def load_notice(days: int = 3) -> List[Dict[str, Any]]:
    """公司公告: policy_news 巨潮部分, 流程性砍掉。"""
    rows = get_policy_news(limit=2000)
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    items = []
    for r in rows:
        if r.get("source") != "巨潮公告":
            continue
        pt = r.get("pub_time", "")
        if not pt or pt < cutoff:
            continue
        title = r.get("title", "")
        # Step 1: is_market_moving 过滤
        keep, reason = is_market_moving_notice(title)
        if not keep:
            continue
        # 从标题抽 ticker
        ticker = extract_ticker(title)
        # 抽公司名 (假设格式: "公司名: 公告标题")
        company_name = title.split(":", 1)[0].strip() if ":" in title else ""
        items.append({
            "source_category": "notice",
            "source": r.get("source", ""),
            "title": title,
            "content": "",
            "pub_time": pt,
            "ticker": ticker,
            "company_name": company_name,
            "notice_type": get_notice_type(title),
            "_filter_reason": reason,
        })
    return items


def load_research(days: int = 3) -> List[Dict[str, Any]]:
    """研报观点: research_report 表。"""
    rows = get_research_reports(limit=500)
    cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    items = []
    for r in rows:
        pd = r.get("publish_date", "")
        if not pd or pd < cutoff_date:
            continue
        items.append({
            "source_category": "research",
            "source": r.get("org_name", ""),  # 券商
            "title": r.get("title", ""),
            "content": "",
            "pub_time": pd,
            "researcher": r.get("researcher", ""),
            "rating": r.get("rating", ""),
            "target_price": r.get("aim_price"),
            "stock_code": r.get("stock_code"),
            "stock_name": r.get("stock_name"),
            "qtype": r.get("qtype", 0),  # 0=个股 1=行业 2=宏观 3=策略
        })
    return items


# ── 4 版本 posterior 计算 ─────────────────────────────────────
def version_a_posterior(item: Dict[str, Any]) -> float:
    """A 贝叶斯 (快讯): LR = 印证 × 量化 × 首现。"""
    lr = 1.0
    sc = item.get("_source_count", 1)
    if sc >= 5: lr *= 2.0
    elif sc >= 3: lr *= 1.5
    elif sc == 1: lr *= 0.7

    if item.get("ticker"): lr *= 1.1  # 有具体标的稍加
    if item.get("_is_first"): lr *= 1.5
    if item.get("_is_repeat_24h"): lr *= 0.5

    prior = 0.5
    return (lr * prior) / (lr * prior + (1 - prior))


def version_b_posterior(item: Dict[str, Any]) -> float:
    """B 事件研究 (政策): 政策类型权重 + 阶段权重, 无历史基线 (代码不联网)。"""
    title = item.get("title", "")
    ptype = classify_policy_type(title)
    phase = classify_policy_phase(title)

    # 政策类型权重
    type_w = {
        "产业规划": 0.75, "资本市场制度": 0.80, "价格调整": 0.70,
        "市场监管": 0.65, "工作会议": 0.40, "信用体系": 0.45,
        "民营经济": 0.55, "其他": 0.50,
    }.get(ptype, 0.50)

    # 阶段权重 (落地 > 征求意见 > 执行 > 吹风)
    phase_w = {
        "落地": 1.0, "征求意见": 0.85, "执行": 0.75, "吹风": 0.55, "NA": 0.60,
    }.get(phase, 0.60)

    # 发文机构 (从 source 推断)
    issuing_w = {
        "发改委": 0.9, "证监会": 0.85, "上交所问询": 0.7, "深交所公告": 0.7,
        "深交所问询": 0.7, "财新": 0.55,  # 财新不是一手政策
    }.get(item.get("source", ""), 0.5)

    return min(0.95, type_w * phase_w * issuing_w)


def version_c_posterior(item: Dict[str, Any]) -> float:
    """C 源加权 (公告): type_weight 直接用。"""
    return NOTICE_TYPE_WEIGHT.get(item.get("notice_type", "其他"), 0.30)


def version_d_posterior(item: Dict[str, Any]) -> float:
    """D 原子化 (研报): 共识强度 (简化版, 按报告类型)。"""
    qtype = item.get("qtype", 0)
    # 行业/宏观/策略 研报比个股研报更"共识" (影响面广)
    type_w = {0: 0.5, 1: 0.75, 2: 0.7, 3: 0.65}.get(qtype, 0.5)

    rating = item.get("rating", "")
    rating_w = {
        "买入": 0.9, "增持": 0.75, "强于大市": 0.75,
        "中性": 0.5, "持有": 0.5, "减持": 0.85, "弱于大市": 0.85,  # 极端评级权重高
    }.get(rating, 0.5)

    return min(0.95, type_w * rating_w)


VERSION_FNS = {
    "flash": version_a_posterior,
    "policy": version_b_posterior,
    "notice": version_c_posterior,
    "research": version_d_posterior,
}


# ── 双层贝叶斯叠加 (联合前) ─────────────────────────────────
def overlay_bayesian(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    第二层: 跨路印证, 重新算 final_posterior。
    同一 topic 在多路出现 → bonus。
    """
    # 1. 先按 topic 聚合 (LLM 暂未跑, 用 source_category 预筛 + 简化 topic)
    topic_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for it in items:
        # 简化 topic: 快讯用 title 前 8 字符, 其他用 title 关键词
        topic = _simple_topic(it)
        it["_topic"] = topic
        topic_groups[topic].append(it)

    # 2. 算每条的 final_posterior
    for topic, group in topic_groups.items():
        # 跨路: 多少不同 source_category
        cats = set(it["source_category"] for it in group)
        n_cats = len(cats)
        bonus = CROSS_SOURCE_BONUS.get(n_cats, 1.0)
        for it in group:
            version = it["source_category"]
            v_post = it.get("_version_posterior", 0.5)
            # 把 version posterior 转成 LR (近似: LR = v_post / (1 - v_post))
            if v_post >= 0.99: v_post = 0.99
            if v_post <= 0.01: v_post = 0.01
            lr = v_post / (1 - v_post)
            lr *= bonus
            prior = 0.5
            final = (lr * prior) / (lr * prior + (1 - prior))
            it["final_posterior"] = final
            it["_cross_source_count"] = n_cats
            it["_cross_source_bonus"] = bonus

    return items


def _simple_topic(item: Dict[str, Any]) -> str:
    """简化 topic: 避免 LLM, 用关键词匹配 (足够 demo, 真跑时 LLM 覆盖)。"""
    title = item.get("title", "")
    # 简易关键词分类
    KEYWORDS = {
        "人形机器人": ["人形机器人"],
        "半导体": ["半导体", "芯片", "存储", "光刻", "中芯", "存储芯片"],
        "新能源车": ["新能源车", "电动汽车", "锂电", "电池", "宁德", "比亚迪"],
        "光伏": ["光伏", "硅料", "硅片", "隆基", "通威"],
        "银行": ["银行", "央行", "降准", "LPR", "招行", "工行"],
        "消费": ["消费", "白酒", "茅台", "家电", "以旧换新", "汽车以旧换新"],
        "医药": ["医药", "创新药", "医疗器械", "生物"],
        "AI": ["AI", "人工智能", "算力", "大模型", "光通信"],
        "减持": ["减持", "股东减持"],
        "回购": ["回购"],
        "重组": ["重组", "吸并", "并购"],
    }
    for topic, kws in KEYWORDS.items():
        if any(kw in title for kw in kws):
            return topic
    # 兜底: title 前 6 字符
    return title[:6].strip() or "其他"


# ── 阈值卡线 ────────────────────────────────────────────
def apply_threshold(items: List[Dict[str, Any]], threshold: float) -> List[Dict[str, Any]]:
    """posterior >= threshold 保留。"""
    return [it for it in items if it.get("final_posterior", 0) >= threshold]


# ── 联合分析 (跨路主题重叠) ───────────────────────────────
def find_joint_links(all_kept: Dict[str, List[Dict[str, Any]]], max_links: int = 5) -> List[Dict[str, Any]]:
    """跨路主题重叠 → 标"共振"。"""
    topic_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for cat, items in all_kept.items():
        for it in items:
            topic_groups[it.get("_topic", "")].append({**it, "source_category": cat})

    links = []
    for topic, items in topic_groups.items():
        cats = set(it["source_category"] for it in items)
        if len(cats) < 2:
            continue
        links.append({
            "topic": topic,
            "from_sources": sorted(cats),
            "source_count": len(cats),
            "items": [
                {
                    "category": it["source_category"],
                    "title": it["title"][:80],
                    "final_posterior": round(it.get("final_posterior", 0), 3),
                }
                for it in items[:3]  # 每路最多 3 条
            ],
        })

    # 按 source_count 降序排
    links.sort(key=lambda x: x["source_count"], reverse=True)
    return links[:max_links]


# ── 输出 (HTML + JSON) ─────────────────────────────────────
def render_html(result: Dict[str, Any]) -> str:
    """信息情报简报 HTML (轻量版, 不复杂样式)。"""
    css = """
    body { font-family: 'Inter', system-ui, sans-serif; max-width: 1100px; margin: 20px auto; padding: 20px; background: #f9fafb; color: #111; }
    .header { background: #6366f1; color: white; padding: 20px; border-radius: 12px; margin-bottom: 20px; }
    .main-theme { background: #fff; padding: 16px 20px; border-radius: 12px; border: 1px solid #e5e7eb; font-size: 16px; margin-bottom: 16px; }
    .section { background: #fff; padding: 16px 20px; border-radius: 12px; border: 1px solid #e5e7eb; margin-bottom: 12px; }
    .section h3 { margin: 0 0 12px 0; font-size: 15px; }
    .item { padding: 8px 0; border-bottom: 1px dashed #e5e7eb; font-size: 13px; }
    .item:last-child { border-bottom: none; }
    .item .meta { color: #6b7280; font-size: 12px; }
    .badge { display: inline-block; padding: 2px 6px; border-radius: 4px; background: #e0e7ff; color: #4338ca; font-size: 11px; margin-right: 4px; }
    .footer { color: #6b7280; font-size: 12px; margin-top: 16px; }
    """
    html = [f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>信息情报简报</title><style>{css}</style></head><body>"]
    html.append('<div class="header">📊 信息情报简报 · ' + result["as_of"] + ' · run_type=' + result["run_type"] + '</div>')
    html.append('<div class="main-theme">▎主旋律: ' + result.get("main_theme", "—") + '</div>')

    SECTIONS = [
        ("flash", "📡 财经快讯速读 (A 贝叶斯)"),
        ("policy", "🏛️ 政策动态摘要 (B 事件研究)"),
        ("notice", "📋 公告要点 (C 源加权)"),
        ("research", "📑 研报观点 (D 原子化)"),
    ]
    for cat, title in SECTIONS:
        items = result.get("by_source", {}).get(cat, {}).get("items", [])
        threshold = result.get("thresholds_used", {}).get(cat, 0.5)
        html.append(f'<div class="section"><h3>{title}  (threshold={threshold}, {len(items)} 条)</h3>')
        if not items:
            html.append('<div class="item">今日无高优信号</div>')
        else:
            for it in items[:15]:
                pos = it.get("final_posterior", 0)
                topic = it.get("_topic", "")
                t = it.get("title", "")[:100]
                pt = it.get("pub_time", "")[:16]
                src = it.get("source", "")
                html.append(f'<div class="item"><span class="badge">{topic}</span> <b>{pos:.2f}</b> · <span class="meta">{pt} · {src}</span><br>{t}</div>')
        html.append('</div>')

    # 联合
    links = result.get("joint_links", [])
    html.append('<div class="section"><h3>🔗 跨源共振 (' + str(len(links)) + ' 条)</h3>')
    if not links:
        html.append('<div class="item">今日 4 路消息无明显共振</div>')
    else:
        for link in links:
            html.append(f'<div class="item"><span class="badge">{link["topic"]}</span> <b>{link["source_count"]} 路</b><br>')
            for it in link.get("items", []):
                html.append(f'&nbsp;&nbsp;[{it["category"]}] {it["title"]} (posterior={it["final_posterior"]})<br>')
            html.append('</div>')
    html.append('</div>')

    html.append('<div class="footer">数据: cls_news (A) / policy_news 政策 (B) / policy_news 巨潮 (C) / research (D)<br>')
    html.append('阈值: morning=0.75/0.65/0.50/0.65, intraday=0.60/0.65/0.50/0.65, evening=0.65/0.65/0.50/0.65<br>')
    html.append('剔除: posterior 低于阈值 / 流程性公告 / 24h 内重复 ≥3 次<br>')
    html.append('<script>window.addEventListener("load", function(){parent.postMessage({type:"mra-report-height", height: document.body.scrollHeight}, "*")});</script>')
    html.append('</div></body></html>')
    return "".join(html)


def render_main_theme(by_source: Dict[str, List[Dict[str, Any]]], joint_links: List[Dict[str, Any]]) -> str:
    """生成一句话主旋律。"""
    if joint_links:
        top = joint_links[0]
        return f"今天 {top['source_count']} 路消息同向关注「{top['topic']}」, 是当前信息面核心"
    # 兜底: 找保留最多的类别
    counts = {k: len(v) for k, v in by_source.items()}
    top_cat = max(counts, key=counts.get) if counts else None
    if top_cat and counts[top_cat] > 0:
        return f"今日信息面相对分散, 财经快讯/政策/公告/研报无明显共振"
    return "今日 4 路消息均无高优信号"


# ── 主流程 ────────────────────────────────────────────────
def run(run_type: str = "evening") -> Dict[str, Any]:
    """主入口: 跑一次信息情报简报。"""
    print(f"[info_brief] start run_type={run_type}", flush=True)

    # 1. 4 路读取
    flash_hours = {"morning": 15, "intraday": 3, "evening": 13}[run_type]
    raw_flash = load_flash(flash_hours)
    raw_policy = load_policy(days=3)
    raw_notice = load_notice(days=3)
    raw_research = load_research(days=3)
    print(f"[info_brief] loaded: flash={len(raw_flash)}, policy={len(raw_policy)}, notice={len(raw_notice)}, research={len(raw_research)}", flush=True)

    # 2. 4 版本 posterior
    by_source_raw = {
        "flash": raw_flash,
        "policy": raw_policy,
        "notice": raw_notice,
        "research": raw_research,
    }
    for cat, items in by_source_raw.items():
        fn = VERSION_FNS[cat]
        for it in items:
            it["_version_posterior"] = fn(it)

    # 3. 合并, 双层贝叶斯
    all_items = raw_flash + raw_policy + raw_notice + raw_research
    all_items = overlay_bayesian(all_items)

    # 4. 阈值卡线
    thresholds = THRESHOLDS[run_type]
    by_source_kept: Dict[str, List[Dict[str, Any]]] = {}
    by_source_stats: Dict[str, Dict[str, Any]] = {}
    for cat, items in by_source_raw.items():
        # 按 final_posterior 排序
        items_sorted = sorted(items, key=lambda x: x.get("final_posterior", 0), reverse=True)
        kept = apply_threshold(items_sorted, thresholds[cat])
        by_source_kept[cat] = kept
        by_source_stats[cat] = {
            "version": {"flash": "A", "policy": "B", "notice": "C", "research": "D"}[cat],
            "input": len(items),
            "kept": len(kept),
            "threshold": thresholds[cat],
            "items": kept,
        }

    # 5. 联合分析
    joint_links = find_joint_links(by_source_kept)

    # 6. 一句话主旋律
    main_theme = render_main_theme(by_source_kept, joint_links)

    # 7. 组装结果
    result = {
        "run_type": "info_brief",
        "as_of": datetime.now().strftime("%Y-%m-%d"),
        "run_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "thresholds_used": thresholds,
        "by_source": by_source_stats,
        "joint_links": joint_links,
        "main_theme": main_theme,
        "summary_text": main_theme,
        "data_gaps": [],
    }

    # 8. 落库 (复用 agent_summary 表, run_type="info_brief")
    html = render_html(result)
    try:
        # 落库 result 简版 (历史列表显示用)
        brief_result = {
            "run_type": "info_brief",
            "run_time": result["run_time"],
            "as_of": result["as_of"],
            "main_theme": main_theme,
            "summary_text": main_theme,
            "by_source_stats": {k: {"input": v["input"], "kept": v["kept"], "threshold": v["threshold"]} for k, v in by_source_stats.items()},
            "joint_link_count": len(joint_links),
            "data_gaps": [],
        }
        # 写 HTML 到 tmp
        run_id = os.environ.get("MRA_RUN_ID", "default")
        tmp_dir = Path(f"/tmp/mra-{run_id}")
        tmp_dir.mkdir(parents=True, exist_ok=True)
        html_path = tmp_dir / "info_brief_report.html"
        html_path.write_text(html, encoding="utf-8")

        row_id = insert_agent_summary(
            content=main_theme,
            data_snapshot_json=json.dumps(brief_result, ensure_ascii=False, default=str),
            run_type="info_brief",
            report_html=html,
        )
        # 写 last_row_id (跟 chief 一致, auto-notify 找得到)
        try:
            (tmp_dir / "last_row_id").write_text(str(row_id), encoding="utf-8")
        except Exception as e:
            print(f"[info_brief] write last_row_id failed: {e}", flush=True)

        result["row_id"] = row_id
        result["html_path"] = str(html_path)
    except Exception as e:
        print(f"[info_brief] write_result failed: {e}", flush=True)

    print(f"[info_brief] done. row_id={result.get('row_id')}, html={result.get('html_path')}", flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-type", default="evening", choices=["morning", "intraday", "evening"])
    args = parser.parse_args()
    result = run(args.run_type)
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2))


if __name__ == "__main__":
    main()

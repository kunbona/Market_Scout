"""
信息简报 v2: 分类整理 + AI 综合分析
- 不带贝叶斯, 不打 posterior
- 4 路分类 (复用 info_brief_classify 逻辑)
- 调 mra-info-brief-analyze skill 出 AI 综合分析
- 落库 + 推
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent.info_brief_classify import (
    load_flash, load_policy, load_notice, load_research,
    tag, classify_notice, classify_policy, classify_research,
    KEY_CATS,
)
from db.storage import insert_agent_summary

HOURS_BY_TYPE = {"morning": 15, "intraday": 3, "evening": 24}
DAYS_BY_TYPE = {"morning": 3, "intraday": 1, "evening": 3}
CLAUDE_BIN = "/Users/kun/.nvm/versions/node/v24.15.0/bin/claude"


def summarize_by_cat(items, cat_key, top_n=3):
    """按 cat_key 聚合, 每组取 top_n 最新。"""
    groups = defaultdict(list)
    for it in items:
        groups[it.get(cat_key, "其他")].append(it)
    result = []
    for cat, group in groups.items():
        group_sorted = sorted(group, key=lambda x: (x.get("days_old", 99), x.get("pub_time", "")))
        result.append({
            "cat": cat,
            "count": len(group),
            "top_items": [
                {
                    "title": it["title"][:100],
                    "pub_time": it.get("pub_time", ""),
                    "days_old": it.get("days_old", 99),
                    "source": it.get("source", ""),
                }
                for it in group_sorted[:top_n]
            ]
        })
    result.sort(key=lambda x: -x["count"])
    return result


def build_4way_summary(flash, policy, notice, research, run_type):
    real_notice = [it for it in notice if it.get("is_real")]
    noise_notice_count = len(notice) - len(real_notice)
    key_items = [it for it in real_notice if it.get("cat") in KEY_CATS]
    minor_items = [it for it in real_notice if it.get("cat") not in KEY_CATS]
    key_summary = summarize_by_cat(key_items, "cat", top_n=3)
    minor_summary = summarize_by_cat(minor_items, "cat", top_n=2)
    return {
        "as_of": datetime.now().strftime("%Y-%m-%d"),
        "run_type": run_type,
        "flash": {"total": len(flash), "tags": summarize_by_cat(flash, "tag", top_n=3)},
        "policy": {"total": len(policy), "cats": summarize_by_cat(policy, "cat", top_n=3)},
        "notice": {
            "total_raw": len(notice),
            "total_filtered": noise_notice_count,
            "total_actual": len(real_notice),
            "key_cats": key_summary,
            "minor_cats": minor_summary,
        },
        "research": {"total": len(research), "cats": summarize_by_cat(research, "cat", top_n=3)},
    }


def run_ai_analysis(input_json, run_id):
    run_type = input_json.get("run_type", "evening")
    tmp_dir = Path(f"/tmp/mra-{run_id}")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    input_path = tmp_dir / "info_brief_input.json"
    input_path.write_text(json.dumps(input_json, ensure_ascii=False, indent=2), encoding="utf-8")
    prompt = (
        f"先 cat {input_path} 看 4 路分类整理结果, "
        f"然后调用 /mra-info-brief-analyze 写综合分析, "
        f"写到 {tmp_dir}/info_brief_analysis.md 和 {tmp_dir}/info_brief_analysis.txt。"
    )
    if not Path(CLAUDE_BIN).exists():
        return f"⚠️ claude CLI 不存在 ({CLAUDE_BIN}), 跳过 AI 分析"
    try:
        proc = subprocess.run(
            [CLAUDE_BIN, "-p", prompt,
             "--output-format", "text",
             "--dangerously-skip-permissions"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=300,
        )
        out_path = tmp_dir / "info_brief_analysis.txt"
        if out_path.exists():
            return out_path.read_text(encoding="utf-8").strip()
        return f"⚠️ AI 分析文件未生成 (claude 退出码 {proc.returncode})"
    except subprocess.TimeoutExpired:
        return "⚠️ AI 分析超时 (300s)"
    except Exception as e:
        return f"⚠️ AI 分析异常: {e}"


def parse_ai_markdown(text):
    """解析 AI 输出 markdown → {main_theme: [{rank, text}], cross_links, watch_points}"""
    sections = {"main_theme": [], "cross_links": [], "watch_points": []}
    current = None
    for line in text.splitlines():
        s = line.rstrip()
        if not s.strip():
            continue
        if s.startswith("## "):
            title = s[3:].strip()
            if "主旋律" in title:
                current = "main_theme"
            elif "跨路关联" in title or "跨路" in title:
                current = "cross_links"
            elif "关注点" in title or "关注" in title:
                current = "watch_points"
            else:
                current = None
            continue
        if current == "main_theme":
            stripped = s.lstrip()
            m = re.match(r"^(\d+)[.\)、]\s*(.+)$", stripped)
            if m:
                sections["main_theme"].append({"rank": int(m.group(1)), "text": m.group(2).strip()})
            elif stripped.startswith("- "):
                sections["main_theme"].append({"rank": 99, "text": stripped[2:].strip()})
            elif sections["main_theme"]:
                sections["main_theme"][-1]["text"] += " " + s.strip()
        elif current == "cross_links":
            if s.lstrip().startswith("- "):
                item = s.lstrip()[2:].strip()
                # 解析 "主题 | 来源 · 来源 | 描述" 格式
                if "|" in item:
                    parts = [p.strip() for p in item.split("|")]
                    if len(parts) >= 3:
                        theme = parts[0]
                        sources_str = parts[1]
                        summary = " | ".join(parts[2:])
                        sources = [x.strip() for x in sources_str.split("·") if x.strip()]
                        sections["cross_links"].append({"theme": theme, "sources": sources, "summary": summary})
                    elif len(parts) == 2:
                        sections["cross_links"].append({"theme": parts[0], "sources": [], "summary": parts[1]})
                elif ":" in item or "：" in item:
                    sep = ":" if ":" in item else "："
                    theme, summary = item.split(sep, 1)
                    sections["cross_links"].append({"theme": theme.strip(), "sources": [], "summary": summary.strip()})
                else:
                    sections["cross_links"].append({"theme": "其他", "sources": [], "summary": item})
        elif current == "watch_points":
            stripped = s.lstrip()
            if stripped.startswith("- ") or stripped.startswith("· "):
                sections["watch_points"].append(stripped[2:].strip())
            elif stripped[0].isdigit() and stripped[1:3] in [". ", ") "]:
                sections["watch_points"].append(stripped[3:].strip())
            else:
                sections["watch_points"].append(s.strip())
    return sections


def render_html(summary_4way, ai_analysis):
    """4 路分类 + AI 分析 → HTML (主旋律按重要性的 1 行 1 条, 浅色突出)。"""
    ai = parse_ai_markdown(ai_analysis)
    main_items = ai["main_theme"]
    hours = HOURS_BY_TYPE.get(summary_4way["run_type"], 24)
    days = DAYS_BY_TYPE.get(summary_4way["run_type"], 3)

    css = """
    body { font-family: 'Inter', system-ui, sans-serif; max-width: 1100px; margin: 20px auto; padding: 20px; background: #f9fafb; color: #111; }
    .header { background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%); color: white; padding: 20px 24px; border-radius: 12px; margin-bottom: 16px; }
    .header h1 { margin: 0; font-size: 20px; }
    .header .sub { font-size: 12px; opacity: 0.85; margin-top: 4px; }
    .time-window { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
    .tw-tag { padding: 3px 10px; border-radius: 999px; background: rgba(255,255,255,0.2); color: white; font-size: 11px; }
    .main-theme { background: #fff; padding: 0; border-radius: 12px; border: 1px solid #e5e7eb; margin-bottom: 16px; overflow: hidden; }
    .main-theme-header { background: linear-gradient(135deg, #fef3c7 0%, #fde68a 100%); padding: 10px 20px; border-bottom: 1px solid #f59e0b; }
    .main-theme-label { font-size: 14px; font-weight: 700; color: #92400e; }
    .main-theme-list { padding: 0; margin: 0; list-style: none; }
    .main-theme-item { display: flex; align-items: flex-start; padding: 14px 20px; border-bottom: 1px solid #f3f4f6; font-size: 14px; line-height: 1.6; }
    .main-theme-item:last-child { border-bottom: none; }
    .main-theme-item:nth-child(odd) { background: #fafbfc; }
    .mt-rank { flex-shrink: 0; width: 28px; height: 28px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 13px; margin-right: 12px; margin-top: 1px; }
    .mt-rank-1 { background: linear-gradient(135deg, #ef4444 0%, #f59e0b 100%); color: white; box-shadow: 0 1px 3px rgba(239,68,68,0.3); }
    .mt-rank-2 { background: linear-gradient(135deg, #f59e0b 0%, #fbbf24 100%); color: white; }
    .mt-rank-3 { background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%); color: white; }
    .mt-rank-99 { background: #e5e7eb; color: #6b7280; }
    .mt-text { flex: 1; }
    .cross-section { background: #fff; padding: 16px 20px; border-radius: 12px; border: 1px solid #e5e7eb; margin-bottom: 16px; }
    .cross-section h3 { margin: 0 0 12px 0; font-size: 14px; color: #4338ca; }
    .cross-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 12px; }
    .cross-card { background: linear-gradient(135deg, #fafbff 0%, #f5f3ff 100%); padding: 14px 16px; border-radius: 10px; border: 1px solid #e0e7ff; box-shadow: 0 1px 2px rgba(99,102,241,0.05); transition: transform 0.15s; }
    .cross-card:hover { transform: translateY(-1px); box-shadow: 0 4px 12px rgba(99,102,241,0.12); }
    .cross-header { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
    .cross-num { flex-shrink: 0; width: 22px; height: 22px; border-radius: 50%; background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%); color: white; display: flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 700; }
    .cross-theme { font-size: 14px; font-weight: 700; color: #1e293b; }
    .cross-sources { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 10px; }
    .source-tag { display: inline-flex; align-items: center; gap: 3px; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 500; }
    .source-flash   { background: #dbeafe; color: #1e40af; }
    .source-policy  { background: #d1fae5; color: #065f46; }
    .source-notice  { background: #fed7aa; color: #9a3412; }
    .source-research { background: #ede9fe; color: #5b21b6; }
    .source-default { background: #f3f4f6; color: #4b5563; }
    .cross-summary { font-size: 12px; color: #4b5563; line-height: 1.6; }
    .watch-section { background: #fff; padding: 16px 20px; border-radius: 12px; border: 1px solid #e5e7eb; margin-bottom: 16px; }
    .watch-section h3 { margin: 0 0 12px 0; font-size: 14px; color: #047857; }
    .watch-chips { display: flex; flex-wrap: wrap; gap: 8px; }
    .watch-chip { display: inline-flex; align-items: center; gap: 6px; padding: 6px 12px; border-radius: 999px; background: #ecfdf5; color: #065f46; font-size: 13px; border: 1px solid #a7f3d0; }
    .watch-chip::before { content: "▸"; color: #10b981; font-weight: 700; }
    .section { background: #fff; padding: 16px 20px; border-radius: 12px; border: 1px solid #e5e7eb; margin-bottom: 12px; }
    .section h3 { margin: 0 0 12px 0; font-size: 15px; }
    .subhead { margin: 10px 0 6px 0; font-size: 13px; }
    .subhead-key { color: #10b981; font-weight: 600; }
    .subhead-minor { color: #9ca3af; font-weight: 600; }
    .item { padding: 8px 0; border-bottom: 1px dashed #e5e7eb; font-size: 13px; }
    .item:last-child { border-bottom: none; }
    .item .meta { color: #6b7280; font-size: 12px; }
    .badge { display: inline-block; padding: 2px 6px; border-radius: 4px; background: #e0e7ff; color: #4338ca; font-size: 11px; margin-right: 4px; }
    .badge-key { background: #d1fae5; color: #065f46; }
    .badge-minor { background: #f3f4f6; color: #6b7280; }
    .age-tag { display: inline-block; padding: 1px 5px; border-radius: 3px; font-size: 11px; margin-right: 4px; font-weight: 500; }
    .age-today { background: #10b981; color: white; }
    .age-yesterday { background: #f59e0b; color: white; }
    .age-old { background: #9ca3af; color: white; }
    .footer { color: #6b7280; font-size: 12px; margin-top: 16px; padding: 12px; border-top: 1px solid #e5e7eb; }
    """
    h = []
    h.append('<!DOCTYPE html><html><head><meta charset="utf-8"><title>信息情报简报</title><style>')
    h.append(css)
    h.append('</style></head><body>')
    h.append(f'<div class="header"><h1>📊 信息情报简报</h1><div class="sub">{summary_4way["as_of"]} · run_type={summary_4way["run_type"]} · 4 路综合</div>')
    h.append('<div class="time-window">')
    h.append(f'<span class="tw-tag">📡 财经快讯: 近 {hours}h</span>')
    h.append(f'<span class="tw-tag">🏛️ 政策: 近 {days} 日</span>')
    h.append(f'<span class="tw-tag">📋 公告: 近 {days} 日</span>')
    h.append(f'<span class="tw-tag">📑 研报: 近 {days} 日</span>')
    h.append('</div></div>')

    h.append('<div class="main-theme">')
    h.append('<div class="main-theme-header"><div class="main-theme-label">▎主旋律 (按重要性排序)</div></div>')
    h.append('<ul class="main-theme-list">')
    if not main_items:
        fallback = (ai_analysis or "AI 分析未生成").replace(chr(10), "<br>")
        h.append(f'<li class="main-theme-item"><div class="mt-rank mt-rank-99">-</div><div class="mt-text">{fallback}</div></li>')
    for item in main_items:
        rank = item["rank"]
        rank_cls = f"mt-rank-{rank}" if 1 <= rank <= 3 else "mt-rank-99"
        h.append(f'<li class="main-theme-item"><div class="mt-rank {rank_cls}">{rank}</div><div class="mt-text">{item["text"]}</div></li>')
    h.append('</ul></div>')

    if ai["cross_links"]:
        h.append(f'<div class="cross-section"><h3>🔗 跨路关联 ({len(ai["cross_links"])} 处共振)</h3>')
        h.append('<div class="cross-grid">')
        for i, link in enumerate(ai["cross_links"], 1):
            source_tags = ""
            for s in link.get("sources", []):
                s_lower = s.strip()
                cls = "source-default"
                if "快讯" in s_lower: cls = "source-flash"
                elif "政策" in s_lower: cls = "source-policy"
                elif "公告" in s_lower: cls = "source-notice"
                elif "研报" in s_lower: cls = "source-research"
                source_tags += f'<span class="source-tag {cls}">{s}</span>'
            h.append(f'<div class="cross-card"><div class="cross-header"><div class="cross-num">{i}</div><div class="cross-theme">{link["theme"]}</div></div>')
            if source_tags:
                h.append(f'<div class="cross-sources">{source_tags}</div>')
            h.append(f'<div class="cross-summary">{link["summary"]}</div></div>')
        h.append('</div></div>')

    if ai["watch_points"]:
        h.append('<div class="watch-section"><h3>👀 关注点</h3>')
        h.append('<div class="watch-chips">')
        for pt in ai["watch_points"]:
            h.append(f'<span class="watch-chip">{pt}</span>')
        h.append('</div></div>')

    def age_tag(d):
        if d == 0: return "<span class='age-tag age-today'>今天</span>"
        if d == 1: return "<span class='age-tag age-yesterday'>昨天</span>"
        return f"<span class='age-tag age-old'>{d}天前</span>"

    SECTIONS = [
        ("flash",    "📡 财经快讯速读", summary_4way['flash']),
        ("policy",   "🏛️ 政策动态摘要", summary_4way['policy']),
        ("notice",   "📋 公告要点",     summary_4way['notice']),
        ("research", "📑 研报观点",     summary_4way['research']),
    ]
    for cat, title, data in SECTIONS:
        total = data.get("total") if data.get("total") is not None else data.get("total_actual", 0)
        h.append(f'<div class="section"><h3>{title}  ({total} 条)</h3>')

        if cat == "notice":
            if data.get("key_cats"):
                h.append('<div class="subhead subhead-key">🔑 关键类</div>')
                for c in data['key_cats']:
                    h.append(f'<div class="item"><span class="badge badge-key">[{c["cat"]}]</span> <b>{c["count"]} 条</b></div>')
                    for it in c.get('top_items', []):
                        h.append(f'<div class="item">&nbsp;&nbsp;{age_tag(it["days_old"])} <span class="meta">{it["pub_time"][:16]}</span> · {it["title"]}</div>')
            if data.get("minor_cats"):
                h.append(f'<div class="subhead subhead-minor">次要类 (流程性已砍 {data.get("total_filtered", 0)} 条)</div>')
                for c in data['minor_cats'][:5]:
                    h.append(f'<div class="item"><span class="badge badge-minor">[{c["cat"]}]</span> <b>{c["count"]} 条</b></div>')
                    for it in c.get('top_items', []):
                        h.append(f'<div class="item">&nbsp;&nbsp;{age_tag(it["days_old"])} <span class="meta">{it["pub_time"][:16]}</span> · {it["title"]}</div>')
        else:
            cats = data.get("tags" if cat == "flash" else "cats", [])
            for c in cats:
                h.append(f'<div class="item"><span class="badge">[{c["cat"]}]</span> <b>{c["count"]} 条</b></div>')
                for it in c.get('top_items', []):
                    h.append(f'<div class="item">&nbsp;&nbsp;{age_tag(it["days_old"])} <span class="meta">{it["pub_time"][:16]}</span> · {it["title"]}</div>')

        h.append('</div>')

    h.append('<div class="footer">数据: cls_news + policy_news + research_report | 分类: 关键词 | 时效: pub_time - 当前<br>')
    h.append('剔除: 流程性公告 (董事会决议等) | 跨路关联 + 关注点: 由 AI 综合分析给出<br>')
    h.append('<script>window.addEventListener("load", function(){parent.postMessage({type:"mra-report-height", height: document.body.scrollHeight}, "*")});</script>')
    h.append('</div></body></html>')
    return "".join(h)


def run(run_type="evening"):
    print(f"[info_brief_v2] start run_type={run_type}", flush=True)
    hours = HOURS_BY_TYPE.get(run_type, 24)
    days = DAYS_BY_TYPE.get(run_type, 3)
    flash = load_flash(hours)
    policy = load_policy(days)
    notice = load_notice(days)
    research = load_research(days)
    print(f"[info_brief_v2] loaded: flash={len(flash)}, policy={len(policy)}, notice={len(notice)}, research={len(research)}", flush=True)

    summary_4way = build_4way_summary(flash, policy, notice, research, run_type)
    run_id = os.environ.get("MRA_RUN_ID", "default")
    print(f"[info_brief_v2] running AI analysis (claude skill)...", flush=True)
    ai_analysis = run_ai_analysis(summary_4way, run_id)
    print(f"[info_brief_v2] AI analysis done ({len(ai_analysis)} chars)", flush=True)

    html = render_html(summary_4way, ai_analysis)
    summary_text = ai_analysis.split("\n")[0][:200] if ai_analysis else "AI 分析生成失败"
    brief_result = {
        "run_type": "info_brief",
        "run_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "as_of": summary_4way["as_of"],
        "summary_text": summary_text,
        "by_source": {
            "flash": {"count": len(flash)},
            "policy": {"count": len(policy)},
            "notice": {"count": len([it for it in notice if it.get("is_real")]), "filtered": len(notice) - len([it for it in notice if it.get("is_real")])},
            "research": {"count": len(research)},
        },
        "ai_analysis": ai_analysis,
        "data_gaps": [],
    }

    tmp_dir = Path(f"/tmp/mra-{run_id}")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    html_path = tmp_dir / "info_brief_report.html"
    html_path.write_text(html, encoding="utf-8")

    try:
        row_id = insert_agent_summary(
            content=summary_text,
            data_snapshot_json=json.dumps(brief_result, ensure_ascii=False, default=str),
            run_type="info_brief",
            report_html=html,
        )
        try:
            (tmp_dir / "last_row_id").write_text(str(row_id), encoding="utf-8")
        except Exception as e:
            print(f"[info_brief_v2] write last_row_id failed: {e}", flush=True)
        print(f"[info_brief_v2] row_id={row_id}, html={html_path}", flush=True)
        brief_result["row_id"] = row_id
        brief_result["html_path"] = str(html_path)
    except Exception as e:
        print(f"[info_brief_v2] insert failed: {e}", flush=True)

    return brief_result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-type", default="evening", choices=["morning", "intraday", "evening"])
    args = parser.parse_args()
    result = run(args.run_type)
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2)[:2000])


if __name__ == "__main__":
    main()

"""
战略推理师 (Strategist)
- 读最近一次 info_brief 输出 (主旋律 + 跨路关联 + 关注点)
- 调 mra-strategist skill, 用麦肯锡框架做 1-4 周推理预测
- 落库 + 推
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from db.storage import get_agent_summary_by_id, get_agent_summary_history, insert_agent_summary

CLAUDE_BIN = "/Users/kun/.nvm/versions/node/v24.15.0/bin/claude"
RUN_TYPE = "strategist"
DISPLAY_NAME = "战略推理"

# 战略推理只在信息量大的时段跑 (morning/intraday 数据少, 跳过)
# 仅 evening 必跑, 其他时段依赖用户手动触发
EVENING_ONLY = True


def parse_latest_info_brief():
    """读最近一次 info_brief 的 ai_analysis (主旋律 + 跨路关联 + 关注点) + 4 路快照。

    注意: get_agent_summary_history 不返回 data_snapshot_json, 需要 by_id 取完整行。
    完整 markdown 在 data_snapshot_json.ai_analysis, content 只是截断 200 字摘要。
    """
    rows = get_agent_summary_history(limit=1, run_type="info_brief")
    if not rows:
        return None
    row_id = rows[0].get("id")
    full = get_agent_summary_by_id(row_id) or rows[0]
    data_snapshot = full.get("data_snapshot_json", "{}")
    try:
        snap = json.loads(data_snapshot) if isinstance(data_snapshot, str) else data_snapshot
    except Exception:
        snap = {}
    content = snap.get("ai_analysis") or full.get("content", "")
    return {
        "row_id": row_id,
        "as_of": snap.get("as_of") or full.get("summary_time", "")[:10],
        "info_brief_run_type": snap.get("run_type", ""),
        "content": content,
        "by_source": snap.get("by_source", {}),
    }


def content_to_structured(content):
    """把 info_brief 的 content (主旋律 + 跨路 + 关注点 文字) 拆成结构化字段。

    兼容两种格式:
    1. 带 `## ` 前缀的标准 markdown 标题
    2. 裸标题 (AI 实际输出常省略 ## 前缀, 仅留 "主旋律" / "跨路关联" / "关注点")
    """
    main_theme = []
    cross_links = []
    watch_points = []
    current = None
    for line in content.splitlines():
        s = line.rstrip()
        stripped = s.strip()
        if not stripped:
            continue
        # 兼容 ## 前缀
        if s.startswith("## "):
            stripped = s[3:].strip()
        # 裸标题检测: 整行只含章节名, 且后面紧跟空行 + 内容
        if stripped in ("主旋律", "跨路关联", "关注点") or stripped in ("## 主旋律", "## 跨路关联", "## 关注点"):
            if "主旋律" in stripped:
                current = "main_theme"
            elif "跨路" in stripped:
                current = "cross_links"
            elif "关注" in stripped:
                current = "watch_points"
            continue
        if current == "main_theme":
            m = re.match(r"^(\d+)[.\)、]\s*(.+)$", stripped)
            if m:
                main_theme.append(f"{m.group(1)}. {m.group(2).strip()}")
        elif current == "cross_links":
            if stripped.startswith("- ") or stripped.startswith("· "):
                cross_links.append(stripped[2:].strip())
        elif current == "watch_points":
            if stripped.startswith("- ") or stripped.startswith("· "):
                watch_points.append(stripped[2:].strip())
    return main_theme, cross_links, watch_points


def run_ai_strategist(structured, info_brief_meta, run_id):
    """调 mra-strategist skill, 写到 tmp_dir/strategist_output.md 和 .txt。"""
    tmp_dir = Path(f"/tmp/mra-{run_id}")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    input_path = tmp_dir / "strategist_input.json"
    strategist_input = {
        "as_of": info_brief_meta["as_of"],
        "info_brief_row_id": info_brief_meta["row_id"],
        "info_brief_run_type": info_brief_meta["info_brief_run_type"],
        "main_theme": structured["main_theme"],
        "cross_links": structured["cross_links"],
        "watch_points": structured["watch_points"],
    }
    input_path.write_text(json.dumps(strategist_input, ensure_ascii=False, indent=2), encoding="utf-8")
    prompt = (
        f"先 cat {input_path} 看 info_brief 的 4 路分析结果 (主旋律/跨路/关注点), "
        f"然后调用 /mra-strategist 做麦肯锡框架推理预测, "
        f"写到 {tmp_dir}/strategist_output.md 和 {tmp_dir}/strategist_output.txt。"
    )
    if not Path(CLAUDE_BIN).exists():
        return f"⚠️ claude CLI 不存在 ({CLAUDE_BIN}), 跳过战略推理"
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
        out_path = tmp_dir / "strategist_output.txt"
        if out_path.exists():
            return out_path.read_text(encoding="utf-8").strip()
        return f"⚠️ 战略推理文件未生成 (claude 退出码 {proc.returncode})"
    except subprocess.TimeoutExpired:
        return "⚠️ 战略推理超时 (300s)"
    except Exception as e:
        return f"⚠️ 战略推理异常: {e}"


def parse_strategist_md(text):
    """解析 8 段 markdown → dict, 供 HTML 渲染用。

    兼容裸标题 (AI 实际输出常省略 ## 前缀)。
    """
    sections = {
        "core": "", "pest": "", "five_forces": "", "logic_tree": "",
        "induction": "", "decision_matrix": "", "synthesis": "", "checkpoints": "",
    }
    current = None
    order = []
    for line in text.splitlines():
        s = line.rstrip()
        stripped = s.strip()
        if not stripped:
            continue
        # 兼容 ## 前缀
        if s.startswith("## "):
            stripped = s[3:].strip()
        # 裸标题检测 (整行只含章节名, 后面紧跟空行 + 内容)
        # 也兼容像 "## PEST 分析" 这种有 ## 的
        section_match = None
        if "核心判断" in stripped and len(stripped) < 20:
            section_match = "core"
        elif "PEST" in stripped and len(stripped) < 20:
            section_match = "pest"
        elif "五力" in stripped and len(stripped) < 20:
            section_match = "five_forces"
        elif "逻辑树" in stripped and len(stripped) < 20:
            section_match = "logic_tree"
        elif "归纳" in stripped and len(stripped) < 20:
            section_match = "induction"
        elif "决策矩阵" in stripped and len(stripped) < 20:
            section_match = "decision_matrix"
        elif "综合判断" in stripped and len(stripped) < 20:
            section_match = "synthesis"
        elif "跟踪" in stripped and len(stripped) < 20:
            section_match = "checkpoints"
        if section_match:
            current = section_match
            order.append(current)
            continue
        if current:
            sections[current] += line + "\n"
    return sections


def render_html(sections, info_brief_meta):
    """战略推理 → HTML (浅色主调, 8 段结构清晰展示)。"""
    core = sections["core"].strip()
    pest = sections["pest"].strip()
    five_forces = sections["five_forces"].strip()
    logic_tree = sections["logic_tree"].strip()
    induction = sections["induction"].strip()
    decision_matrix = sections["decision_matrix"].strip()
    synthesis = sections["synthesis"].strip()
    checkpoints = sections["checkpoints"].strip()

    def md_to_html(md):
        """简单 md → HTML 转义 + 表格保留。"""
        if not md:
            return ""
        # 转义
        out = md.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        # 保留表格
        lines = out.split("\n")
        html_lines = []
        in_table = False
        for ln in lines:
            stripped = ln.strip()
            if stripped.startswith("|") and stripped.endswith("|"):
                cells = [c.strip() for c in stripped.strip("|").split("|")]
                if not in_table:
                    html_lines.append("<table class='tbl'>")
                    html_lines.append("<thead><tr>" + "".join(f"<th>{c}</th>" for c in cells) + "</tr></thead>")
                    html_lines.append("<tbody>")
                    in_table = True
                elif set(cells[0]) <= {"-", " "} or all(re.match(r"^-+$", c.strip() or "-") for c in cells if c.strip()):
                    continue  # 分隔行
                else:
                    html_lines.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
            else:
                if in_table:
                    html_lines.append("</tbody></table>")
                    in_table = False
                if stripped.startswith("├──") or stripped.startswith("└──") or stripped.startswith("│"):
                    html_lines.append(f'<div class="tree-line">{stripped}</div>')
                elif stripped.startswith("**") and stripped.endswith("**"):
                    html_lines.append(f'<div class="bold-line">{stripped.strip("*")}</div>')
                elif stripped:
                    html_lines.append(f'<div class="para">{stripped}</div>')
        if in_table:
            html_lines.append("</tbody></table>")
        return "\n".join(html_lines)

    css = """
    body { font-family: 'Inter', system-ui, sans-serif; max-width: 1100px; margin: 20px auto; padding: 20px; background: #f9fafb; color: #111; }
    .header { background: linear-gradient(135deg, #1e293b 0%, #475569 100%); color: white; padding: 20px 24px; border-radius: 12px; margin-bottom: 16px; }
    .header h1 { margin: 0; font-size: 20px; }
    .header .sub { font-size: 12px; opacity: 0.85; margin-top: 4px; }
    .core { background: linear-gradient(135deg, #fef3c7 0%, #fde68a 100%); padding: 18px 22px; border-radius: 12px; border: 1px solid #f59e0b; margin-bottom: 16px; }
    .core-label { font-size: 12px; font-weight: 700; color: #92400e; margin-bottom: 6px; letter-spacing: 0.5px; }
    .core-text { font-size: 15px; line-height: 1.7; color: #1f2937; font-weight: 500; }
    .section { background: #fff; padding: 16px 20px; border-radius: 12px; border: 1px solid #e5e7eb; margin-bottom: 12px; }
    .section h3 { margin: 0 0 12px 0; font-size: 15px; color: #1e293b; display: flex; align-items: center; gap: 6px; }
    .section .sec-icon { font-size: 16px; }
    .tbl { width: 100%; border-collapse: collapse; font-size: 13px; margin: 8px 0; }
    .tbl th, .tbl td { border: 1px solid #e5e7eb; padding: 8px 10px; text-align: left; vertical-align: top; }
    .tbl th { background: #f3f4f6; font-weight: 600; color: #374151; }
    .tbl td { color: #1f2937; line-height: 1.5; }
    .para { margin: 6px 0; font-size: 13px; line-height: 1.6; color: #374151; }
    .bold-line { font-weight: 600; color: #1e293b; margin: 6px 0; font-size: 13px; }
    .tree-line { font-family: 'Menlo', 'Consolas', monospace; font-size: 12px; color: #4b5563; line-height: 1.5; padding-left: 8px; }
    .footer { color: #6b7280; font-size: 12px; margin-top: 16px; padding: 12px; border-top: 1px solid #e5e7eb; }
    """

    h = []
    h.append('<!DOCTYPE html><html><head><meta charset="utf-8"><title>战略推理</title><style>')
    h.append(css)
    h.append('</style></head><body>')
    h.append(f'<div class="header"><h1>🎯 战略推理 (Strategist)</h1>')
    h.append(f'<div class="sub">基于 {info_brief_meta["as_of"]} 信息情报简报 (row #{info_brief_meta["row_id"]}, {info_brief_meta["info_brief_run_type"]}) · 麦肯锡 6 框架推理</div></div>')

    if core:
        h.append('<div class="core">')
        h.append('<div class="core-label">▎核心判断 (一句话)</div>')
        h.append(f'<div class="core-text">{core.split(chr(10))[0]}</div>')
        h.append('</div>')

    sections_def = [
        ("📊", "PEST 分析", pest, "pest"),
        ("⚔️", "五力分析", five_forces, "five_forces"),
        ("🌳", "逻辑树", logic_tree, "logic_tree"),
        ("🔮", "归纳 → 演绎", induction, "induction"),
        ("🎲", "决策矩阵", decision_matrix, "decision_matrix"),
        ("🏛️", "综合判断", synthesis, "synthesis"),
        ("📌", "跟踪检查点", checkpoints, "checkpoints"),
    ]
    for icon, title, content_md, key in sections_def:
        if not content_md:
            continue
        h.append(f'<div class="section"><h3><span class="sec-icon">{icon}</span> {title}</h3>')
        h.append(md_to_html(content_md))
        h.append('</div>')

    h.append('<div class="footer">方法: 麦肯锡思考工具 (大岛祥誉) — PEST/五力/逻辑树/归纳演绎/决策矩阵/金字塔 | 输入: 信息情报简报 4 路分析 | 跑法: claude CLI + mra-strategist skill<br>')
    h.append('<script>window.addEventListener("load", function(){parent.postMessage({type:"mra-report-height", height: document.body.scrollHeight}, "*")});</script>')
    h.append('</div></body></html>')
    return "".join(h)


def run(run_type="evening"):
    """主入口: 读 info_brief → 调 strategist → 落库。"""
    print(f"[strategist] start run_type={run_type}", flush=True)
    brief_meta = parse_latest_info_brief()
    if not brief_meta:
        msg = "⚠️ 没有可用的 info_brief 历史, 战略推理跳过"
        print(f"[strategist] {msg}", flush=True)
        return {"skipped": True, "reason": msg}
    main, cross, watch = content_to_structured(brief_meta["content"])
    structured = {"main_theme": main, "cross_links": cross, "watch_points": watch}
    print(f"[strategist] from info_brief row={brief_meta['row_id']}: main={len(main)}, cross={len(cross)}, watch={len(watch)}", flush=True)

    run_id = os.environ.get("MRA_RUN_ID", "default")
    print(f"[strategist] running AI strategist (claude skill)...", flush=True)
    ai_text = run_ai_strategist(structured, brief_meta, run_id)
    print(f"[strategist] AI done ({len(ai_text)} chars)", flush=True)

    sections = parse_strategist_md(ai_text)
    html = render_html(sections, brief_meta)
    core_line = sections["core"].split("\n")[0].strip() if sections["core"] else "战略推理生成失败"
    summary_text = core_line[:200] if core_line else "战略推理生成失败"

    result = {
        "run_type": RUN_TYPE,
        "run_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "as_of": brief_meta["as_of"],
        "based_on_info_brief_row": brief_meta["row_id"],
        "summary_text": summary_text,
        "core": sections["core"],
        "ai_text": ai_text,
    }
    tmp_dir = Path(f"/tmp/mra-{run_id}")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    html_path = tmp_dir / "strategist_report.html"
    html_path.write_text(html, encoding="utf-8")

    try:
        row_id = insert_agent_summary(
            content=summary_text,
            data_snapshot_json=json.dumps(result, ensure_ascii=False, default=str),
            run_type=RUN_TYPE,
            report_html=html,
        )
        try:
            (tmp_dir / "last_row_id").write_text(str(row_id), encoding="utf-8")
        except Exception as e:
            print(f"[strategist] write last_row_id failed: {e}", flush=True)
        print(f"[strategist] row_id={row_id}, html={html_path}", flush=True)
        result["row_id"] = row_id
        result["html_path"] = str(html_path)
    except Exception as e:
        print(f"[strategist] insert failed: {e}", flush=True)

    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-type", default="evening", choices=["morning", "intraday", "evening"])
    args = parser.parse_args()
    result = run(args.run_type)
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2)[:2000])


if __name__ == "__main__":
    main()

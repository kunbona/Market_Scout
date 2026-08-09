"""
把 info_brief_classify.py 的文本输出渲染成 HTML 报告
"""
import sys
import re
from datetime import datetime
from pathlib import Path

src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/info_brief_test_output.txt")
out = src.with_suffix(".html")

lines = src.read_text(encoding="utf-8").splitlines()

# 简易 HTML 渲染
CSS = """
body { font-family: 'Inter', system-ui, sans-serif; max-width: 1100px; margin: 20px auto; padding: 24px; background: #f9fafb; color: #111; line-height: 1.7; }
h1 { background: #6366f1; color: white; padding: 16px 24px; border-radius: 12px; font-size: 20px; }
h2 { background: #fff; padding: 12px 20px; border-radius: 12px; border: 1px solid #e5e7eb; font-size: 16px; margin-top: 24px; }
h3 { font-size: 14px; margin-top: 16px; padding: 6px 12px; border-left: 4px solid #6366f1; background: #f3f4f6; }
.cat-key { border-left-color: #10b981 !important; background: #ecfdf5 !important; }
.item { background: #fff; padding: 8px 12px; margin: 4px 0; border-radius: 6px; border: 1px solid #e5e7eb; font-size: 13px; }
.time { color: #6b7280; font-size: 12px; font-family: 'JetBrains Mono', monospace; }
.age-today { background: #10b981; color: white; padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; }
.age-yesterday { background: #f59e0b; color: white; padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; }
.age-pre { background: #9ca3af; color: white; padding: 2px 6px; border-radius: 4px; font-size: 11px; }
.footer { color: #6b7280; font-size: 12px; margin-top: 24px; padding: 12px; border-top: 1px solid #e5e7eb; }
"""

html = [f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>信息简报 - 分类测试</title><style>{CSS}</style></head><body>"]
html.append(f"<h1>📊 信息简报 - 分类测试报告</h1>")
html.append(f"<p style='color:#6b7280'>生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>")

current_section = ""
current_cat = ""
for line in lines:
    s = line.rstrip()
    if not s.strip():
        continue
    # 主标题 (===)
    if s.startswith("==="):
        continue
    # section 标题 (含 "·  总" 的)
    if "总 " in s and "条" in s and s.startswith(("📡", "🏛️", "📋", "📑", "🔗")):
        html.append(f"<h2>{s.strip()}</h2>")
        continue
    # cat 标题
    if s.lstrip().startswith("[") and ("条" in s and s.rstrip().endswith("条") or s.lstrip().startswith("🔑")):
        m = re.search(r"\[(.*?)\]\s*(\d+)\s*条", s)
        if m:
            cat, n = m.group(1), m.group(2)
            is_key = "🔑" in s
            cls = "cat-key" if is_key else ""
            html.append(f'<h3 class="{cls}">{"🔑 " if is_key else ""}[{cat}]  {n} 条</h3>')
            current_cat = cat
            continue
    # 行内条目
    if s.startswith("    "):
        # 时效性标签
        s_html = s.strip()
        s_html = re.sub(r"\[今天\]", "<span class='age-today'>今天</span>", s_html)
        s_html = re.sub(r"\[昨天\]", "<span class='age-yesterday'>昨天</span>", s_html)
        s_html = re.sub(r"\[前天\]", "<span class='age-pre'>前天</span>", s_html)
        s_html = re.sub(r"\[(\d+)天前\]", r"<span class='age-pre'>\1天前</span>", s_html)
        html.append(f"<div class='item'>{s_html}</div>")
        continue
    # 公告/总计
    if s.startswith("公告: "):
        html.append(f"<h2>{s}</h2>")
        continue
    # 其他 (如 "时间窗:")
    if "时间窗:" in s:
        html.append(f"<p style='color:#6b7280'>{s}</p>")
        continue
    if s.startswith("📊 信息简报"):
        html.append(f"<h1>{s}</h1>")
        continue
    # 默认
    html.append(f"<p>{s}</p>")

html.append(f"<div class='footer'>数据来源: cls_news + policy_news + research_report | 分类规则: 关键词匹配 | 时效性: 公告 pub_time - 当前时间</div>")
html.append("</body></html>")

out.write_text("\n".join(html), encoding="utf-8")
print(f"渲染完成: {out} ({out.stat().st_size} bytes)")

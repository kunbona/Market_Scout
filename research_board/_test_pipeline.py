"""
独立测试脚本，不依赖 Flask，直接跑完整流程验证。
运行：/home/runist/miniconda3/envs/qtrade/bin/python research_board/_test_pipeline.py
"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 加载 .env.local
_env = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env.local")
if os.path.exists(_env):
    for line in open(_env):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from research_board.rb_storage import init_db, create_project, get_project, get_rb_reports
from research_board.rb_fetcher import fetch_reports_for_project, download_pdfs_for_project, get_reports_text_batches

# ── Step 0: 初始化 DB ────────────────────────────────────────
print("\n===== Step 0: 初始化数据库 =====")
init_db()
print("OK")

# ── Step 1: 创建测试项目 ─────────────────────────────────────
print("\n===== Step 1: 创建测试项目 =====")
pid = create_project(
    name="测试-AI算力",
    keywords=["AI算力", "光模块", "GPU"],
    dimensions=["产业链全景", "竞争格局与核心标的"],
    days_back=180,
    qtype_filter=[0, 1],   # 个股+行业，先不抓宏观/策略减少量
)
print(f"project_id = {pid}")

# ── Step 2: 抓取研报元数据 ───────────────────────────────────
print("\n===== Step 2: 抓取研报元数据（180天，关键词过滤）=====")
t0 = time.time()
count = fetch_reports_for_project(pid, progress_cb=print)
print(f"抓取完成：{count} 篇，耗时 {time.time()-t0:.1f}s")

reports = get_rb_reports(pid)
print(f"DB 中确认：{len(reports)} 篇")
if reports:
    print("前3篇样本：")
    for r in reports[:3]:
        print(f"  [{r['publish_date']}] {r['title'][:50]} | {r['org_name']} | url={bool(r['report_url'])}")

# ── Step 3: 下载 PDF（只试前5篇，验证可行性）────────────────
print("\n===== Step 3: 下载 PDF（前5篇）=====")
pending = [r for r in reports if r["report_url"]][:5]
print(f"有 PDF URL 的研报：{len([r for r in reports if r['report_url']])} 篇，本次试下 {len(pending)} 篇")

if pending:
    t0 = time.time()
    done = download_pdfs_for_project(pid, max_count=5, progress_cb=print)
    print(f"PDF 提取完成：{done}/{len(pending)} 篇成功，耗时 {time.time()-t0:.1f}s")

    # 检查文字质量
    after = get_rb_reports(pid, pdf_status="done")
    for r in after[:2]:
        text = r.get("full_text", "") or ""
        print(f"\n  [{r['title'][:40]}]")
        print(f"  字符数：{len(text)}")
        print(f"  前200字：{text[:200].replace(chr(10), ' ')}")
else:
    print("没有可下载的 PDF URL，跳过")

# ── Step 4: 分批情况 ─────────────────────────────────────────
print("\n===== Step 4: 文字分批情况 =====")
batches = get_reports_text_batches(pid)
print(f"共 {len(batches)} 个批次")
for i, b in enumerate(batches):
    print(f"  batch {i+1}: {len(b):,} 字符")

print("\n===== 全流程测试完成 =====")

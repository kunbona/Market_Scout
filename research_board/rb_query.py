"""
研报数据查询脚本。供 Claude / Kimi agent 通过 Bash 工具调用，结果 JSON 输出到 stdout。

用法：
  python research_board/rb_query.py <command> [options]

Commands:
  project       项目基本信息（name/dimensions/keywords 等）
  reports       项目所有研报元数据列表（不含 full_text）
  report_text   单篇研报完整正文
  summary       项目概况：研报数量、有全文数量、来源类型分布

Options:
  --project_id N    项目 ID（project/reports/summary 命令必须）
  --id N            研报 ID（report_text 命令必须）
  --with_text       reports 命令附带 full_text（默认不包含，节省输出）
"""
import argparse
import json
import sys
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _out(data):
    print(json.dumps(data, ensure_ascii=False, default=str))


def _err(msg: str):
    print(json.dumps({"error": msg}, ensure_ascii=False), file=sys.stderr)
    sys.exit(1)


def cmd_project(args):
    from research_board.rb_storage import get_project
    p = get_project(args.project_id)
    if not p:
        _err(f"project {args.project_id} not found")
    # 不输出无关字段
    _out({
        "id": p["id"],
        "name": p["name"],
        "keywords": p["keywords"],
        "dimensions": p["dimensions"],
        "days_back": p["days_back"],
        "status": p["status"],
        "report_count": p["report_count"],
    })


def cmd_reports(args):
    from research_board.rb_storage import get_rb_reports
    rows = get_rb_reports(args.project_id)
    result = []
    for r in rows:
        entry = {
            "id": r["id"],
            "title": r["title"],
            "org_name": r["org_name"],
            "researcher": r["researcher"],
            "publish_date": r["publish_date"],
            "rating": r["rating"],
            "aim_price": r["aim_price"],
            "stock_code": r["stock_code"],
            "stock_name": r["stock_name"],
            "source_type": r["source_type"],
            "pdf_status": r["pdf_status"],
            "has_full_text": bool(r.get("full_text") and r["full_text"].strip()),
        }
        if args.with_text:
            entry["full_text"] = r.get("full_text") or ""
        result.append(entry)
    _out(result)


def cmd_report_text(args):
    from research_board.rb_storage import _conn
    with _conn() as conn:
        row = conn.execute(
            "SELECT id, title, org_name, publish_date, full_text FROM rb_report WHERE id=?",
            (args.id,)
        ).fetchone()
    if not row:
        _err(f"report {args.id} not found")
    _out({
        "id": row["id"],
        "title": row["title"],
        "org_name": row["org_name"],
        "publish_date": row["publish_date"],
        "full_text": row["full_text"] or "",
    })


def cmd_summary(args):
    from research_board.rb_storage import get_project, get_rb_reports
    p = get_project(args.project_id)
    if not p:
        _err(f"project {args.project_id} not found")
    rows = get_rb_reports(args.project_id)
    total = len(rows)
    has_text = sum(1 for r in rows if r.get("full_text") and r["full_text"].strip())
    source_dist: dict = {}
    for r in rows:
        st = r.get("source_type") or "unknown"
        source_dist[st] = source_dist.get(st, 0) + 1
    _out({
        "project_id": args.project_id,
        "project_name": p["name"],
        "total_reports": total,
        "has_full_text": has_text,
        "no_full_text": total - has_text,
        "source_distribution": source_dist,
        "dimensions": p["dimensions"],
        "keywords": p["keywords"],
    })


def main():
    parser = argparse.ArgumentParser(description="研报数据查询脚本")
    parser.add_argument("command", choices=["project", "reports", "report_text", "summary"])
    parser.add_argument("--project_id", type=int)
    parser.add_argument("--id", type=int, dest="id")
    parser.add_argument("--with_text", action="store_true")
    args = parser.parse_args()

    if args.command in ("project", "reports", "summary") and not args.project_id:
        _err(f"命令 {args.command} 需要 --project_id")
    if args.command == "report_text" and not args.id:
        _err("命令 report_text 需要 --id")

    dispatch = {
        "project": cmd_project,
        "reports": cmd_reports,
        "report_text": cmd_report_text,
        "summary": cmd_summary,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()

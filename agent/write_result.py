"""
结果落地脚本。Claude 分析完成后调用，把 JSON 结果写入 agent_summary 表。

用法：
  python agent/write_result.py --run-type evening --result '<JSON字符串>'

或从文件读取（JSON 较长时推荐）：
  python agent/write_result.py --run-type evening --result-file /tmp/result.json
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def main():
    parser = argparse.ArgumentParser(description="写入 Agent 分析结果")
    parser.add_argument("--run-type", required=True,
                        choices=["morning", "auction", "intraday", "closing", "evening", "policy", "research", "notice", "watchlist"],
                        help="分析类型")
    parser.add_argument("--result", type=str, default="",
                        help="JSON 结果字符串")
    parser.add_argument("--result-file", type=str, default="",
                        help="JSON 结果文件路径（与 --result 二选一）")
    parser.add_argument("--html-file", type=str, default="",
                        help="HTML 报告文件路径，写入 report_html 字段")
    args = parser.parse_args()

    # 读取 JSON
    if args.result_file:
        try:
            result_json = Path(args.result_file).read_text(encoding="utf-8")
        except Exception as e:
            print(f"ERROR: 读取结果文件失败: {e}", file=sys.stderr)
            sys.exit(1)
    elif args.result:
        result_json = args.result
    else:
        print("ERROR: 必须提供 --result 或 --result-file", file=sys.stderr)
        sys.exit(1)

    # 解析 JSON
    try:
        result = json.loads(result_json)
    except json.JSONDecodeError as e:
        print(f"ERROR: JSON 解析失败: {e}", file=sys.stderr)
        sys.exit(1)

    # 补充元数据
    result.setdefault("run_type", args.run_type)
    result.setdefault("run_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    # 读取 HTML 文件（可选）
    report_html = ""
    if args.html_file:
        try:
            report_html = Path(args.html_file).read_text(encoding="utf-8")
        except Exception as e:
            print(f"WARNING: 读取 HTML 文件失败（忽略）: {e}", file=sys.stderr)

    # 提取摘要文本
    summary_text = (
        result.get("summary_text")
        or (result.get("changes") or [""])[0]
        or f"{args.run_type} 分析完成"
    )

    # 写入数据库
    try:
        from db.storage import insert_agent_summary
        row_id = insert_agent_summary(
            content=summary_text,
            data_snapshot_json=json.dumps(result, ensure_ascii=False, default=str),
            run_type=args.run_type,
            report_html=report_html,
        )
        print(json.dumps({
            "status": "ok",
            "run_type": args.run_type,
            "id": row_id,
            "has_html": bool(report_html),
            "summary": summary_text[:100],
            "written_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }, ensure_ascii=False))

        # ── 把 row_id 落到临时文件, 让 orchestrator 准确锁定本次 run 的行, 避免错推上次的报告 ──
        # orchestrator 清理时会读这个文件, 找到了才推, 找不到（chief 失败）就不推
        tmp_dir = os.environ.get("MRA_TMP_DIR", "").strip()
        if tmp_dir:
            try:
                Path(tmp_dir, "last_row_id").write_text(str(row_id), encoding="utf-8")
            except Exception as e:
                print(f"WARNING: 写 last_row_id 失败（不影响落库）: {e}", file=sys.stderr)
    except Exception as e:
        print(f"ERROR: 写入数据库失败: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

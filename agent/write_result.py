"""
结果落地脚本。Claude 分析完成后调用，把 JSON 结果写入 agent_summary 表。

用法：
  python agent/write_result.py --run-type evening --result '<JSON字符串>'

或从文件读取（JSON 较长时推荐）：
  python agent/write_result.py --run-type evening --result-file /tmp/result.json
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def main():
    parser = argparse.ArgumentParser(description="写入 Agent 分析结果")
    parser.add_argument("--run-type", required=True,
                        choices=["morning", "auction", "intraday", "closing", "evening"],
                        help="分析类型")
    parser.add_argument("--result", type=str, default="",
                        help="JSON 结果字符串")
    parser.add_argument("--result-file", type=str, default="",
                        help="JSON 结果文件路径（与 --result 二选一）")
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

    # 提取摘要文本
    summary_text = (
        result.get("summary_text")
        or (result.get("changes") or [""])[0]
        or f"{args.run_type} 分析完成"
    )

    # 写入数据库
    try:
        from db.storage import insert_agent_summary
        insert_agent_summary(
            content=summary_text,
            data_snapshot_json=json.dumps(result, ensure_ascii=False, default=str),
            run_type=args.run_type,
        )
        print(json.dumps({
            "status": "ok",
            "run_type": args.run_type,
            "summary": summary_text[:100],
            "written_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }, ensure_ascii=False))
    except Exception as e:
        print(f"ERROR: 写入数据库失败: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

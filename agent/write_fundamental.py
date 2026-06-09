"""
基本面覆盖图落地脚本。mra-fundamental skill 分析完成后调用，
把覆盖结果写入 fundamental_coverage 表。

用法：
  python agent/write_fundamental.py --result '<JSON字符串>' --html-file /tmp/mra-xxx/fundamental.html
"""
import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def main():
    parser = argparse.ArgumentParser(description="写入基本面覆盖图")
    parser.add_argument("--result", type=str, default="",
                        help="JSON 结果字符串（coverage_json 内容）")
    parser.add_argument("--result-file", type=str, default="",
                        help="JSON 结果文件路径（与 --result 二选一）")
    parser.add_argument("--html-file", type=str, default="",
                        help="HTML 报告文件路径")
    parser.add_argument("--expire-days", type=int, default=7,
                        help="覆盖图有效天数（默认 7 天）")
    args = parser.parse_args()

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

    try:
        coverage = json.loads(result_json)
    except json.JSONDecodeError as e:
        print(f"ERROR: JSON 解析失败: {e}", file=sys.stderr)
        sys.exit(1)

    report_html = ""
    if args.html_file:
        try:
            report_html = Path(args.html_file).read_text(encoding="utf-8")
        except Exception as e:
            print(f"WARNING: 读取 HTML 文件失败（忽略）: {e}", file=sys.stderr)

    now = datetime.now()
    generated_at = now.strftime("%Y-%m-%d %H:%M:%S")
    expires_at = (now + timedelta(days=args.expire_days)).strftime("%Y-%m-%d %H:%M:%S")

    project_ids = json.dumps(coverage.get("project_ids", []), ensure_ascii=False)

    try:
        from db.storage import insert_fundamental_coverage
        row_id = insert_fundamental_coverage(
            generated_at=generated_at,
            expires_at=expires_at,
            project_ids=project_ids,
            coverage_json=result_json,
            report_html=report_html,
        )
        print(json.dumps({
            "status": "ok",
            "id": row_id,
            "generated_at": generated_at,
            "expires_at": expires_at,
            "has_html": bool(report_html),
        }, ensure_ascii=False))
    except Exception as e:
        print(f"ERROR: 写入数据库失败: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

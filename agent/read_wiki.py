#!/usr/bin/env python3
"""
读取 wiki 日志（.claude/wiki/market_log.jsonl）最近 N 条记录。
由 mra-chief 在构建叙事链之前调用，用于判断主线延续性。
"""
import argparse
import json
from pathlib import Path

WIKI_FILE = Path(__file__).resolve().parent.parent / ".claude" / "wiki" / "market_log.jsonl"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--last", type=int, default=5, help="读取最近N条")
    args = parser.parse_args()

    if not WIKI_FILE.exists():
        print(json.dumps({"entries": [], "note": "wiki日志为空，这是首次分析"}, ensure_ascii=False))
        return

    entries = []
    for line in WIKI_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    recent = entries[-args.last:] if entries else []
    print(json.dumps({
        "entries": recent,
        "total_in_log": len(entries),
        "note": f"返回最近{len(recent)}条记录" if recent else "暂无历史记录",
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()

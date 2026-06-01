#!/usr/bin/env python3
"""
写入 wiki 日志（.claude/wiki/market_log.jsonl）。
由 mra-chief 在 write_result 之前调用。
每次追加一条 JSON 记录，自动保留最近 30 条。
"""
import argparse
import json
import os
from datetime import datetime
from pathlib import Path

WIKI_DIR = Path(__file__).resolve().parent.parent / ".claude" / "wiki"
WIKI_FILE = WIKI_DIR / "market_log.jsonl"
MAX_ENTRIES = 30


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--run-type", required=True)
    parser.add_argument("--narrative", required=True, help="核心叙事一句话")
    parser.add_argument("--sectors", default="", help="主线板块，逗号分隔")
    parser.add_argument("--t0", default="", help="T0候选代码，逗号分隔")
    parser.add_argument("--verdict", default="观望", help="偏多|偏空|观望")
    args = parser.parse_args()

    WIKI_DIR.mkdir(parents=True, exist_ok=True)

    entry = {
        "date": args.date,
        "run_type": args.run_type,
        "run_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "core_narrative": args.narrative,
        "main_sectors": [s.strip() for s in args.sectors.split(",") if s.strip()],
        "t0_tickers": [t.strip() for t in args.t0.split(",") if t.strip()],
        "verdict": args.verdict,
    }

    # 读取现有记录
    entries = []
    if WIKI_FILE.exists():
        for line in WIKI_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    entries.append(entry)
    # 只保留最近 MAX_ENTRIES 条
    entries = entries[-MAX_ENTRIES:]

    WIKI_FILE.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": "ok", "total_entries": len(entries)}, ensure_ascii=False))


if __name__ == "__main__":
    main()

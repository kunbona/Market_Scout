"""[AI-GENERATED] 智堡 API 通用 CLI — 覆盖全部 9 个资源端点。

用法（必须 dm exec，注入 .env）:
    # 列表（资源: feed/articles/market-daily/reports/archives/company-reports/earningscalls/images/am-reports）
    uv run dm exec tools/wisburg_api.py list feed --first 10
    uv run dm exec tools/wisburg_api.py list articles --query 半导体 --first 5
    uv run dm exec tools/wisburg_api.py list reports --start-time 2026-07-01 --first 10

    # 详情（资源: articles/reports/archives/company-reports/earningscalls/am-reports）
    uv run dm exec tools/wisburg_api.py detail reports 12345
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # 兼容 dm exec
from ._wisburg import DETAIL_RESOURCES, RESOURCES, get_detail, list_items

# 各资源列表展示时除 id/datetime/title 外额外展示的字段
EXTRA_FIELDS = {
    "articles": ("description", 60),
    "images": ("cover_url", 50),
    "feed": ("content", 60),
}


def cmd_list(args: argparse.Namespace) -> None:
    items, cursor = list_items(
        args.resource,
        first=args.first,
        query=args.query,
        start_time=args.start_time,
        end_time=args.end_time,
        after=args.after,
    )
    print(f"✅ {args.resource}: {len(items)} 条" + (f" | 下一页游标: {cursor}" if cursor else ""))
    extra, width = EXTRA_FIELDS.get(args.resource, (None, 0))
    for it in items:
        line = f"[{it.get('id', '-')}] {it.get('datetime', '-')[:16]}  {it.get('title', '-')}"
        print(line)
        if extra and it.get(extra):
            text = str(it[extra]).replace("\n", " ")
            print(f"    {text[:width]}...")


def cmd_detail(args: argparse.Namespace) -> None:
    d = get_detail(args.resource, args.id)
    print(f"# [{d.get('id')}] {d.get('title')}")
    print(f"  发布时间: {d.get('datetime')}")
    if d.get("url"):
        print(f"  原文链接: {d['url']}")
    if d.get("meta"):
        print(f"  元数据: {d['meta']}")
    body = d.get("summary") or d.get("body") or ""
    print(f"\n{body}")


def main() -> None:
    parser = argparse.ArgumentParser(description="智堡 API 通用 CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="拉取资源列表")
    p_list.add_argument("resource", choices=RESOURCES)
    p_list.add_argument("--first", type=int, default=20)
    p_list.add_argument("--query", type=str, default=None)
    p_list.add_argument("--start-time", type=str, default=None)
    p_list.add_argument("--end-time", type=str, default=None)
    p_list.add_argument("--after", type=str, default=None, help="分页游标")

    p_detail = sub.add_parser("detail", help="拉取单条详情")
    p_detail.add_argument("resource", choices=DETAIL_RESOURCES)
    p_detail.add_argument("id", type=int)

    args = parser.parse_args()
    if args.cmd == "list":
        cmd_list(args)
    else:
        cmd_detail(args)


if __name__ == "__main__":
    main()

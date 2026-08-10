"""
智堡资讯流 AI 日报 — 自动拉取、分类、摘要。

用法:
    uv run python tools/wisburg_feed_briefing.py                # 最近 20 条
    uv run python tools/wisburg_feed_briefing.py --first 50     # 最近 50 条
    uv run python tools/wisburg_feed_briefing.py --query 黄金   # 搜索关键词
    uv run python tools/wisburg_feed_briefing.py --output md    # 输出 markdown 文件
    uv run python tools/wisburg_feed_briefing.py --output json  # 输出 JSON 文件

环境变量（在 .env 中配置）:
    WISBURG_API_KEY    智堡 API Key（https://www.wisburg.com/user/developer?tab=apikeys）
    KIMI_API_KEY       Kimi API Key（https://platform.moonshot.cn/console/api-keys）
    KIMI_MODEL         可选，默认 kimi-k2-0905-preview
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))  # 兼容 dm exec（runpy 不加脚本目录到 sys.path）
from ._kimi_chat import chat as kimi_chat
from ._wisburg import list_items as wisburg_list

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "runtime" / "briefings"

CATEGORIES = [
    "宏观政策",   # 央行、财政、地缘政治、监管
    "股票评级",   # 个股评级调整、目标价变化
    "行业板块",   # 行业趋势、板块轮动
    "债券固收",   # 利率、信用、债券市场
    "外汇商品",   # 汇率、黄金、原油、大宗
    "其他",
]

# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class FeedItem:
    id: int
    title: str
    datetime: str
    content: str


@dataclass
class ClassifiedItem:
    item: FeedItem
    category: str
    tags: list[str] = field(default_factory=list)  # e.g. ["花旗", "日立", "买入"]
    one_liner: str = ""  # AI 生成的一句话摘要


@dataclass
class DailyBriefing:
    generated_at: str
    total_items: int
    categories: dict[str, list[ClassifiedItem]]
    overview: str  # AI 生成的全局概览


# ---------------------------------------------------------------------------
# 1. 拉取资讯流
# ---------------------------------------------------------------------------


def fetch_feed(
    api_key: str,
    first: int = 20,
    query: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    after: str | None = None,
) -> list[FeedItem]:
    """从智堡 API 拉取资讯流（api_key 参数保留兼容，实际走 _wisburg 统一鉴权）。"""
    items, next_cursor = wisburg_list(
        "feed", first=first, query=query, start_time=start_time, end_time=end_time, after=after
    )

    results = [
        FeedItem(id=it["id"], title=it["title"], datetime=it["datetime"], content=it["content"])
        for it in items
    ]

    if next_cursor:
        print(f"📄 本页 {len(items)} 条，下一页游标: {next_cursor}")

    return results


# ---------------------------------------------------------------------------
# 2. AI 分类 + 摘要
# ---------------------------------------------------------------------------


def classify_and_summarize(items: list[FeedItem]) -> DailyBriefing:
    """将资讯批量发给 Kimi，一次完成分类、标签提取、摘要、全局概览。"""

    if not items:
        return DailyBriefing(
            generated_at=datetime.now().isoformat(),
            total_items=0,
            categories={},
            overview="今日无资讯。",
        )

    # 构建 prompt
    items_text_parts: list[str] = []
    for i, it in enumerate(items):
        items_text_parts.append(
            f"<item id='{it.id}' index='{i}'>\n"
            f"  <title>{it.title}</title>\n"
            f"  <datetime>{it.datetime}</datetime>\n"
            f"  <content>{it.content}</content>\n"
            f"</item>"
        )
    items_text = "\n".join(items_text_parts)

    categories_str = "、".join(CATEGORIES)

    prompt = f"""你是一位资深金融资讯编辑。请对以下 {len(items)} 条智堡资讯进行分类、摘要和综述。

## 分类体系（从以下类别中选择最合适的）:
{categories_str}

## 任务:
1. 为每条资讯选择 **1 个** 最合适的一级分类
2. 为每条资讯提取 **1-3 个** 关键标签（公司名/机构名/主题/资产）
3. 为每条资讯写一句 **不超过 30 字** 的中文摘要
4. 写一段 **100-200 字** 的全局综述，概括今日资讯的整体特征和值得关注的重点

## 资讯列表:
{items_text}

## 输出格式 (严格 JSON):
```json
{{
  "overview": "全局综述...",
  "items": [
    {{
      "index": 0,
      "category": "股票评级",
      "tags": ["花旗", "日立", "买入"],
      "one_liner": "花旗维持日立买入评级，目标价上调至6500日元"
    }}
  ]
}}
```

注意:
- 每条 item 必须对应上述资讯的 index
- category 必须从指定的分类体系中选择
- one_liner 控制在 30 字以内
- overview 100-200 字
- 只输出 JSON，不要其他内容"""

    print(f"🤖 正在调用 Kimi 处理 {len(items)} 条资讯...")

    raw = kimi_chat(
        system="你是专业的金融资讯编辑，擅长分类和摘要。请严格按 JSON 格式输出，不要添加任何其他内容。",
        user=prompt,
        max_tokens=16384,
        json_mode=True,
    )

    # 去掉可能的 markdown 代码块包裹
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        if raw.endswith("```"):
            raw = raw.rsplit("\n", 1)[0]
        # 如果第一行是 json 说明
        if raw.startswith("json"):
            raw = raw.split("\n", 1)[1]

    result = json.loads(raw)

    # 组装结果
    classified: dict[str, list[ClassifiedItem]] = {cat: [] for cat in CATEGORIES}
    items_by_index = {i: it for i, it in enumerate(items)}

    for entry in result["items"]:
        idx = entry["index"]
        item = items_by_index[idx]
        cat = entry["category"]
        if cat not in classified:
            cat = "其他"
        classified[cat].append(
            ClassifiedItem(
                item=item,
                category=cat,
                tags=entry.get("tags", []),
                one_liner=entry.get("one_liner", ""),
            )
        )

    return DailyBriefing(
        generated_at=datetime.now().isoformat(),
        total_items=len(items),
        categories=classified,
        overview=result.get("overview", ""),
    )


# ---------------------------------------------------------------------------
# 3. 输出
# ---------------------------------------------------------------------------


def format_briefing_markdown(briefing: DailyBriefing) -> str:
    """格式化为 Markdown 日报。"""
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")

    lines = [
        f"# 智堡资讯日报 — {date_str} {time_str}",
        "",
        f"> 共 {briefing.total_items} 条资讯 | AI 自动分类摘要",
        "",
        "## 📊 全局综述",
        "",
        briefing.overview,
        "",
        "---",
        "",
        "## 📈 资讯分类",
        "",
    ]

    # 分类汇总统计
    lines.append("| 分类 | 数量 |")
    lines.append("|------|------|")
    for cat in CATEGORIES:
        count = len(briefing.categories.get(cat, []))
        if count > 0:
            lines.append(f"| {cat} | {count} |")
    lines.append("")

    # 逐分类展开
    for cat in CATEGORIES:
        items_in_cat = briefing.categories.get(cat, [])
        if not items_in_cat:
            continue

        lines.append(f"### {cat}（{len(items_in_cat)} 条）")
        lines.append("")

        for ci in items_in_cat:
            tags_str = " `".join(ci.tags)
            if tags_str:
                tags_str = f"`{tags_str}`"
            lines.append(f"- **{ci.item.title}**")
            lines.append(f"  {ci.one_liner}  {tags_str}")
            lines.append(f"  _{ci.item.datetime}_")
            lines.append("")

    # 附录：原文
    lines.append("---")
    lines.append("")
    lines.append("## 📋 附录：原文")
    lines.append("")
    for cat in CATEGORIES:
        items_in_cat = briefing.categories.get(cat, [])
        if not items_in_cat:
            continue
        for ci in items_in_cat:
            lines.append(f"### [{ci.category}] {ci.item.title}")
            lines.append("")
            lines.append(f"> {ci.item.datetime}")
            lines.append("")
            lines.append(ci.item.content)
            lines.append("")

    return "\n".join(lines)


def print_briefing_console(briefing: DailyBriefing):
    """终端友好输出。"""
    # 分类统计
    stats = []
    for cat in CATEGORIES:
        count = len(briefing.categories.get(cat, []))
        if count > 0:
            stats.append(f"{cat}:{count}")
    print(f"\n📊 {' | '.join(stats)} | 共 {briefing.total_items} 条\n")

    # 逐条
    for cat in CATEGORIES:
        items_in_cat = briefing.categories.get(cat, [])
        if not items_in_cat:
            continue
        print(f"━ {cat} ━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        for ci in items_in_cat:
            tags = ", ".join(ci.tags)
            print(f"  📌 {ci.item.title}")
            print(f"     {ci.one_liner}")
            print(f"     🏷 {tags}  🕐 {ci.item.datetime}")
            print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="智堡资讯流 AI 日报")
    parser.add_argument("--first", type=int, default=20, help="拉取条数（默认 20，最大 100）")
    parser.add_argument("--query", type=str, default=None, help="搜索关键词")
    parser.add_argument("--start-time", type=str, default=None, help="开始时间 (ISO 格式)")
    parser.add_argument("--end-time", type=str, default=None, help="结束时间 (ISO 格式)")
    parser.add_argument("--output", type=str, default="console", choices=["console", "md", "json", "all"])
    parser.add_argument("--dry-run", action="store_true", help="只拉取不调 AI")
    args = parser.parse_args()

    # 读取 API Key
    wisburg_key = os.environ.get("WISBURG_API_KEY")
    kimi_key = os.environ.get("KIMI_API_KEY") or os.environ.get("MOONSHOT_API_KEY")

    if not wisburg_key:
        print("❌ 请设置环境变量 WISBURG_API_KEY")
        print("   获取地址: https://www.wisburg.com/user/developer?tab=apikeys")
        print("   在 .env 中添加: WISBURG_API_KEY=你的key")
        sys.exit(1)

    if not kimi_key and not args.dry_run:
        print("❌ 请设置环境变量 KIMI_API_KEY")
        print("   获取地址: https://platform.moonshot.cn/console/api-keys")
        print("   在 .env 中添加: KIMI_API_KEY=你的key")
        sys.exit(1)

    # 1. 拉取
    print(f"📡 拉取智堡资讯流 (first={args.first})...")
    items = fetch_feed(
        api_key=wisburg_key,
        first=args.first,
        query=args.query,
        start_time=args.start_time,
        end_time=args.end_time,
    )
    print(f"✅ 拉取到 {len(items)} 条资讯\n")

    if not items:
        print("没有资讯。")
        return

    if args.dry_run:
        print("--- dry-run 模式：以下是拉取到的原始数据 ---\n")
        for it in items:
            print(f"[{it.id}] {it.title}")
            print(f"    {it.datetime}")
            print(f"    {it.content[:100]}...")
            print()
        return

    # 2. AI 分类摘要
    briefing = classify_and_summarize(items)

    # 3. 输出
    print(f"\n{'='*60}")
    print(f"📰 {briefing.overview}")
    print(f"{'='*60}")

    if args.output in ("console", "all"):
        print_briefing_console(briefing)

    # 写入文件
    file_base = f"wisburg_briefing_{datetime.now().strftime('%Y%m%d_%H%M')}"
    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.output in ("md", "all"):
        md_path = DEFAULT_OUTPUT_DIR / f"{file_base}.md"
        md_path.write_text(format_briefing_markdown(briefing), encoding="utf-8")
        print(f"📝 Markdown 已保存: {md_path}")

    if args.output in ("json", "all"):
        # 简化版 JSON
        json_data = {
            "generated_at": briefing.generated_at,
            "total_items": briefing.total_items,
            "overview": briefing.overview,
            "categories": {
                cat: [
                    {
                        "id": ci.item.id,
                        "title": ci.item.title,
                        "datetime": ci.item.datetime,
                        "one_liner": ci.one_liner,
                        "tags": ci.tags,
                        "content": ci.item.content,
                    }
                    for ci in items
                ]
                for cat, items in briefing.categories.items()
                if items
            },
        }
        json_path = DEFAULT_OUTPUT_DIR / f"{file_base}.json"
        json_path.write_text(json.dumps(json_data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"📝 JSON 已保存: {json_path}")


if __name__ == "__main__":
    main()

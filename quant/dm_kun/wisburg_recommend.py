"""
智堡资讯整理推荐 — 全局视角：主题聚类 + 精选推荐 + 投资启示。

用法:
    uv run python tools/wisburg_recommend.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # 兼容 dm exec（runpy 不加脚本目录到 sys.path）
from ._kimi_chat import chat as kimi_chat

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

RAW_FEED_PATH = Path(__file__).resolve().parent.parent / "runtime" / "briefings" / "feed_2026-07-07_raw.json"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "runtime" / "briefings"

SYSTEM_PROMPT = """你是一位资深全球宏观策略师，服务于一家顶级对冲基金。你的任务是：
1. 从当日资讯中识别核心主题和叙事线索
2. 筛选出最值得关注的 10-15 条高价值资讯
3. 给出可操作的投资启示

要求：
- 视角宏观，不要逐条罗列
- 关注信息之间的关联和矛盾
- 区分信号和噪音
- 用中文输出，专业但不晦涩"""


def build_prompt(items: list[dict]) -> str:
    """构建分析 prompt。"""
    items_text_parts = []
    for i, it in enumerate(items):
        items_text_parts.append(
            f"[{i}] **{it['title']}**\n"
            f"    {it['datetime']}\n"
            f"    {it['content']}\n"
        )
    items_text = "\n".join(items_text_parts)

    return f"""以下是智堡 2026年7月7日 全部 {len(items)} 条投研资讯。请做全局分析和推荐。

## 资讯全文
{items_text}

## 输出格式

请按以下结构输出（Markdown 格式）：

### 一、今日核心主题（3-5个）

每个主题写一段，格式：
**主题名**：主题概述。涉及 N 条资讯，代表机构：机构A、机构B。核心结论。

### 二、多空分歧与矛盾

找出今天资讯中存在对立观点或值得警惕的矛盾信号。如果没有明显矛盾，说明"今日资讯观点高度一致"并概括一致方向。

### 三、精选推荐（10-15条）

格式：
**序号. [评级：⭐⭐⭐/⭐⭐/⭐] 标题**
- 推荐理由（一句话，说明为什么这条值得细读）
- 关键数据/结论

### 四、投资启示

从以下维度给出简练判断（每条 1-2 句）：
- 短期情绪面
- 中期配置方向
- 需要警惕的风险
- 值得进一步研究的线索"""


def main():
    # 读取原始数据
    if not RAW_FEED_PATH.exists():
        print(f"❌ 找不到原始数据: {RAW_FEED_PATH}")
        sys.exit(1)

    with open(RAW_FEED_PATH, encoding="utf-8") as f:
        items = json.load(f)

    print(f"📡 已加载 {len(items)} 条资讯 ({items[-1]['datetime'][:10]})")

    # 构建 prompt
    prompt = build_prompt(items)
    print(f"📝 Prompt 长度: {len(prompt)} 字符")

    # 调用 AI
    kimi_key = os.environ.get("KIMI_API_KEY") or os.environ.get("MOONSHOT_API_KEY")
    if not kimi_key:
        print("❌ 请设置 KIMI_API_KEY（https://platform.moonshot.cn/console/api-keys）")
        sys.exit(1)

    print("🤖 正在分析...")
    report = kimi_chat(system=SYSTEM_PROMPT, user=prompt, max_tokens=8192)

    # 保存
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = OUTPUT_DIR / f"wisburg_recommend_{datetime.now().strftime('%Y%m%d_%H%M')}.md"

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# 智堡资讯整理推荐 — 2026-07-07\n\n")
        f.write(f"> 共 {len(items)} 条资讯 | 时间范围 {items[-1]['datetime']} ~ {items[0]['datetime']}\n\n")
        f.write("---\n\n")
        f.write(report)

    print(f"\n{'='*60}")
    print(report)
    print(f"{'='*60}")
    print(f"\n📝 报告已保存: {report_path}")


if __name__ == "__main__":
    main()

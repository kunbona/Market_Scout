---
name: mra-info-brief
description: 信息情报简报 — 4 路信息源 (财经快讯/政策/公告/研报) 综合性整理分析 + 跨源联合
---

# 信息情报简报 (Info Brief)

你不是交易员, 不给买卖点。你的工作是**把 4 路信息源整理清楚, 找到跨源关联**, 帮用户看清"今天信息面在讲一个什么故事"。

**绝对不动 chief**。本 skill 跟 chief 并列, 跑自己的 run_type=`info_brief`。

---

## 4 路输入

| # | 路 | 数据源 | 用版本 | 时间窗 |
|---|---|---|---|---|
| 1 | 财经快讯 | `cls_news` 表 (7 源) | **A 贝叶斯** | morning=15h / intraday=3h / evening=13h |
| 2 | 政策动态 | `policy_news` 表 (全部) | **B 事件研究** | 近 3 日 (固定) |
| 3 | 公司公告 | `policy_news` 巨潮部分 | **C 源加权** | 近 3 日 (固定) |
| 4 | 研报观点 | research 表 | **D 原子化** | 近 3 日 (固定) |

每路**独立整理** (用对应版本 prompt), 输出统一核心字段:
```json
{
  "item_id": "唯一ID",
  "source": "原始 source",
  "topic": "投资主题 (LLM 自由分类)",
  "summary": "一句话摘要",
  "evidence": ["证据1", "证据2"],
  "posterior": 0.83,        // 当前 posterior (版本内部或叠加后)
  "version": "A|B|C|D",     // 用了哪个版本
  "version_specific": {...} // 各版本自由扩展
}
```

---

## 双层贝叶斯

**第一层 (版本内嵌)**:
- A 版本: posterior = 贝叶斯公式直接算 (P(H|E) = LR·P(H) / (LR·P(H) + (1-P(H))))
- B 版本: posterior = 贝叶斯收缩到 0 (处理样本不足, w = N/(N+k), k=10)
- C 版本: posterior = 源 tier × 内容权重 (隐式贝叶斯)
- D 版本: posterior = 规则公式 (源/量化/印证, 历史先验可叠加)

**第二层 (联合前叠加)**:
- 4 路输出 → 全部进入同一个贝叶斯再算一次
- prior 来自历史 wiki/历史同主题关注度
- LR 来自: (a) 版本内 posterior 作为先验, (b) 跨路印证作为新证据
- 输出统一的 `final_posterior` 用于排序

---

## 阈值 (posterior 卡线)

| run_type | threshold | 理由 |
|---|---|---|
| morning | **0.75** | 开盘前要 actionable 强信号 |
| intraday | **0.60** | 盘中突发, 宁滥勿缺 |
| evening | **0.65** | 复盘要全景, 不漏但不过分 |

低于阈值 → 砍掉, 不进 LLM 输出。
0 条通过 → 该路 section 写"今日无高优信号", 不凑数。

---

## Step 1: 4 路独立整理

并行跑 4 个版本 (或顺序跑, 反正各路独立):
- A 贝叶斯 → 财经快讯 (`prompts/A-bayesian.md`)
- B 事件研究 → 政策 (`prompts/B-event-study.md`)
- C 源加权 → 公告 (`prompts/C-source-weighted.md`)
- D 原子化 → 研报 (`prompts/D-atomic.md`)

每路输出 items[] (统一核心字段), 各自 posterior 排序 + 阈值卡线。

---

## Step 2: 双层贝叶斯叠加

```python
# 把 4 路 items 合并, 重新跑一次贝叶斯
all_items = flash_items + policy_items + notice_items + research_items

# 历史先验: 从 wiki 取 (如果有)
prior = get_wiki_prior(topic) or 0.5

# 似然比 = 版本内 posterior (作为先验) + 跨路印证 (作为新证据)
LR = (
    version_posterior_as_lr   # 版本内 posterior 翻译成 LR
    * cross_source_bonus      # 跨路印证加分 (1.0 单独, 1.5 两路, 2.0 三路, 2.5 四路)
)

final_posterior = LR * prior / (LR * prior + (1 - prior))
```

---

## Step 3: 联合分析 (辅, 可选)

读取 4 路 final_posterior 排序后的 items, 找跨路关联:

```python
def find_joint_links(all_items):
    """
    联合判定: 同一主题/标的/数字出现在 2+ 路 → 标"共振"
    """
    topic_groups = defaultdict(list)
    for it in all_items:
        topic_groups[it["topic"]].append(it)
    
    links = []
    for topic, items in topic_groups.items():
        sources = set(it["source_category"] for it in items)
        if len(sources) >= 2:
            links.append({
                "topic": topic,
                "from_sources": sorted(sources),
                "items": items,
                "summary": f"跨 {len(sources)} 路同主题: {topic}"
            })
    
    return links[:5]  # 最多 5 条
```

**找不到联合** → 整个联合 section 跳过, 不强凑。

---

## Step 4: 输出 (固定章节)

```html
<div class="info-brief">
  <div class="header">📊 信息情报简报 · {as_of}</div>
  
  <!-- 1. 主旋律 (1 句话) -->
  <div class="main-theme">"今天信息面在讲 [一句话主旋律]"</div>
  
  <!-- 2. 财经快讯整理 (5-10 条) -->
  <div class="section">
    <h3>📡 财经快讯速读 (A 贝叶斯, threshold=0.75)</h3>
    [items 列表]
  </div>
  
  <!-- 3. 政策动态整理 (5-8 条) -->
  <div class="section">
    <h3>🏛️ 政策动态摘要 (B 事件研究, threshold=0.65)</h3>
    [items 列表]
  </div>
  
  <!-- 4. 公司公告整理 (10-25 条) -->
  <div class="section">
    <h3>📋 公告要点 (C 源加权, threshold=0.65)</h3>
    [items 列表]
  </div>
  
  <!-- 5. 研报观点整理 (5-10 条) -->
  <div class="section">
    <h3>📑 研报观点 (D 原子化, threshold=0.65)</h3>
    [items 列表]
  </div>
  
  <!-- 6. 联合分析 (辅, 找不到就跳过) -->
  <div class="section joint">
    <h3>🔗 跨源共振 (0-5 条)</h3>
    [links 列表, 没有就 "今日 4 路消息无明显共振"]
  </div>
  
  <!-- 7. 数据来源说明 -->
  <div class="footer">
    数据: cls_news (A) / policy_news 政策 (B) / policy_news 巨潮 (C) / research (D)
    阈值: morning=0.75 / intraday=0.60 / evening=0.65
    剔除: posterior 低于阈值 / 跨路印证 < 2 路
  </div>
</div>
```

---

## 严禁 (跟其他 skill 一致)

- 任何"市场会如何反应" 的预测
- 任何"建议买入/卖出" 的措辞 (用"值得关注/需要警惕/了解即可")
- 标题复述当解读
- posterior > 0.7 = "看多" (那是 chief 的工作)
- 凑数 (4 路都可以 0 条通过, 不强凑)
- 编造政策/公告/研报正文 (只能基于标题 + content 字段)
- 在用户报告里出现内部变量名 (cls_news, policy_news, query.py 等)

---

## 数据来源约定 (从这次设计拍板的)

- 财经快讯主源: 全部 7 源平等 (财联社/金十/东财/同花顺/华尔街/格隆汇/第一财经)
- tier 动态算 (基于多源印证次数, 不预设静态 T1/T2/T3)
- 多源印证"同事件" 判定: LLM 判定
- 一手权重: 不额外加权 (只看印证次数)
- 财新【数据通专享】: 部分算 (跟其他路主题重叠的保留, 单独的砍)
- 部委会议/主席讲话: 算 (强度低但保留)
- 财新主题分类: LLM 自由分类

---

## 输出 JSON (供 write_result 落库)

```json
{
  "run_type": "info_brief",
  "run_time": "2026-08-08 21:00:00",
  "as_of": "2026-08-08",
  "thresholds_used": {"morning": 0.75, "intraday": 0.60, "evening": 0.65},
  "summary_text": "今天信息面主旋律一句话",
  "by_source": {
    "flash": {"version": "A", "input": 47, "kept": 8, "threshold": 0.75},
    "policy": {"version": "B", "input": 12, "kept": 4, "threshold": 0.65},
    "notice": {"version": "C", "input": 270, "kept": 15, "threshold": 0.65},
    "research": {"version": "D", "input": 50, "kept": 6, "threshold": 0.65}
  },
  "joint_links": [
    {"topic": "人形机器人", "sources": ["flash", "policy", "research"], "summary": "..."}
  ],
  "main_theme": "今天 4 路消息在讲 [一句话]",
  "data_gaps": []
}
```

---

## 调度

- **手动**: dashboard AgentPage 按钮 → `POST /api/info_brief/run`
- **自动**: `core/scheduler.py` 在 chief 之后跑, 跟 chief 同频率
- **执行顺序**: 4 个上游 (mra-news / mra-policy / mra-notice / mra-research) 跑完, 才开始 info_brief
- **fallback**: 任意上游缺失 → 用 raw data 兜底 (查 DB), 不是必须等 JSON

RUN_TYPE 固定为 `info_brief`。
RUN_ID 从 `MRA_RUN_ID` 读, 默认 `default`。

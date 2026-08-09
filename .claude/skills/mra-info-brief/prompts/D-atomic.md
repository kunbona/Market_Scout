# D 版本: 原子化派 (研报观点)

**适用**: 研报 (research 表, 券商研报)
**核心**: 把研报拆成原子命题 (SPO), 按命题聚合, 不预测市场

---

## 输入

```json
[{
  "pub_time": "2026-08-08",
  "source": "中信证券",
  "title": "新能源车行业深度: 渗透率突破 50% 后的增长动力",
  "type": "行业研报",  // 个股/行业/宏观/策略
  "analyst": "...",
  "rating": "强于大市",  // 买入/增持/中性/减持 (如有)
  "target_price": null,   // 如有
  "content": "..."  // 研报正文 (如果可获取)
}]
```

## Step 1: 拆成原子命题 (LLM 唯一允许的"创造"动作)

每条研报 → 1-3 个 SPO 三元组:
```json
{
  "report_id": "research_001",
  "claims": [
    {
      "claim_id": "c_001",
      "subject": "新能源车",
      "predicate": "渗透率",
      "object": "突破 50%",
      "time_ref": "2026 H1",
      "source": "中信证券",
      "source_tier": "头部券商"
    },
    {
      "claim_id": "c_002",
      "subject": "新能源车",
      "predicate": "增长动力",
      "object": "出海 + 智能化",
      "time_ref": "2026-2028",
      "source": "中信证券",
      "source_tier": "头部券商"
    }
  ]
}
```

## Step 2: 跨研报命题聚合 (LLM 自由分类 + 聚合)

```json
{
  "topic": "新能源车",  // LLM 自由分类
  "claims": [
    {"claim": "渗透率突破 50%", "sources": ["中信证券", "中金", "华泰"], "count": 3},
    {"claim": "出海是新增长点", "sources": ["中信证券", "招商"], "count": 2},
    {"claim": "智能化兑现是关键", "sources": ["中金", "国君"], "count": 2}
  ],
  "version": "D"
}
```

## Step 3: posterior (代码算, 隐式贝叶斯)

```python
def compute_posterior(topic_group):
    # 共识强度 (多家机构同时支持)
    n_sources = len(set(c["source"] for c in topic_group["claims"]))
    consensus_strength = min(1.0, n_sources / 5)  # 5 家以上 = 满分共识
    
    # 评级分布
    ratings = [c.get("rating") for c in topic_group.get("ratings", [])]
    bullish = sum(1 for r in ratings if r in ["买入", "增持", "强于大市"])
    bearish = sum(1 for r in ratings if r in ["卖出", "减持", "弱于大市"])
    if bullish + bearish > 0:
        direction = bullish / (bullish + bearish)  # 0~1, 越高越偏多
    else:
        direction = 0.5  # 中性
    
    # posterior = 共识强度 × (1 - 不确定性)
    # 不确定性高 (来源少) → 拉低
    uncertainty = 1 - consensus_strength
    
    # 简化: 共识度 0.5, posterior 0.5
    # 共识度 1.0, posterior 0.9
    pos = 0.5 + consensus_strength * 0.4
    
    return pos
```

## Step 4: 阈值卡线

threshold = 0.65 (研报观点要明确, 阈值中)
posterior >= 0.65 → 保留

## 输出

```json
[{
  "item_id": "research_001",
  "source": "中信证券",
  "topic": "新能源车",
  "summary": "渗透率破 50% 后, 出海+智能化是核心增长动力",
  "claims": [
    {"claim": "渗透率突破 50%", "source_count": 3},
    {"claim": "出海+智能化", "source_count": 2}
  ],
  "consensus": "共识 (3 家)",  // 共识/分歧/单家
  "posterior": 0.78,
  "version": "D",
  "version_specific": {
    "source_count": 3,
    "consensus_strength": 0.6,
    "report_count": 1
  }
}]
```

## 严禁

- LLM 不算 posterior (代码算)
- LLM 不算共识强度 (代码算)
- LLM 只做: 拆命题 + 跨研报聚合 + 一句话摘要
- 看不懂研报正文 → 不编, 标"基于标题推断"
- 不写"建议买入", 只转述机构评级 ("据中信证券评级: 强于大市")
- 研报正文可能没有, 标题就是全部, 不能假装看到正文
- type=个股 研报里如果有具体 ticker, 必须来自输入, 不从记忆补

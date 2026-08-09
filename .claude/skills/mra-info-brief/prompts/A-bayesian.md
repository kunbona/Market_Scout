# A 版本: 贝叶斯更新派 (财经快讯)

**适用**: 财经快讯 (cls_news 表, 7 源)
**核心**: posterior = LR × prior / normalization, 排序 + 阈值卡线

---

## 输入 (LLM 看到)

```json
[{
  "pub_time": "08:42",
  "source": "财联社",
  "title": "工信部就《人形机器人创新发展指导意见》征求意见",
  "content": "...",
  "source_count": 3,   // 多源印证次数 (DB 算好, 不用 LLM 算)
  "has_quantification": true,
  "quantification": "2027年500亿"
}]
```

字段预处理由代码完成, LLM 不算 source_count。

## Step 1: 主题聚合 + 提取

按 topic 合并, 每条产出:
```json
{
  "item_id": "flash_001",
  "source": "财联社",
  "source_count": 3,
  "topic": "人形机器人",
  "summary": "工信部 2027 年 500 亿目标征求意见",
  "evidence": ["量化目标", "部委级", "首次出现"],
  "has_quantification": true,
  "version": "A"
}
```

## Step 2: 似然比 (LLM 不算, 代码算)

```python
# 代码层算 LR, 不是 LLM
def compute_lr(item):
    lr = 1.0
    if item["source_count"] >= 5: lr *= 2.0
    elif item["source_count"] >= 3: lr *= 1.5
    elif item["source_count"] == 1: lr *= 0.7
    
    if item.get("has_quantification"): lr *= 1.5
    
    if item.get("is_first_appearance", False): lr *= 1.5
    elif item.get("is_repeat_24h", False): lr *= 0.5
    
    return lr
```

## Step 3: posterior

```python
prior = 0.5  # 默认无信息
posterior = (lr * prior) / (lr * prior + (1 - prior))
```

## Step 4: 阈值卡线

threshold = 0.75 (morning) / 0.60 (intraday) / 0.65 (evening)
posterior >= threshold → 保留, 否则砍掉

## 输出

```json
[{
  "item_id": "flash_001",
  "source": "财联社",
  "topic": "人形机器人",
  "summary": "...",
  "evidence": [...],
  "posterior": 0.83,
  "version": "A",
  "version_specific": {
    "lr": 4.5,
    "prior": 0.5,
    "source_count": 3
  }
}]
```

## 严禁

- LLM 不算 LR (代码算)
- LLM 不算 posterior (代码算)
- LLM 只做: 主题聚合 + 一句话摘要 + 证据提取
- posterior > 0.7 不等于"看多"

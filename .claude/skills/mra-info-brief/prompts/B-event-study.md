# B 版本: 事件研究派 (政策动态)

**适用**: 政策动态 (policy_news 表, 全部)
**核心**: 找历史类似事件 CAR 基线, 贝叶斯收缩到 0

---

## 输入

```json
[{
  "pub_time": "2026-07-23",
  "source": "发改委",
  "title": "关于印发《可再生能源发展\"十五五\"规划》的通知",
  "content": "...",
  "has_quantification": true,
  "quantification": "2025-2030 规划"
}]
```

## Step 1: 主题聚合 + 政策分类 (LLM 自由)

```json
{
  "item_id": "policy_001",
  "source": "发改委",
  "topic": "可再生能源",  // LLM 自由分类
  "policy_type": "产业规划",
  "policy_phase": "落地",  // 吹风/征求意见/落地/执行
  "issuing_level": "部委",  // 党中央/国务院/部委/地方
  "summary": "可再生能源 2025-2030 五年规划正式发布",
  "evidence": ["正式印发", "部委级", "有时间节点"],
  "version": "B"
}
```

## Step 2: 找历史基线 (LLM 必须做, 联网查)

对每条政策, 联网搜:
- "[政策类型] A股 CAR 历史"
- "[行业] 政策 事件研究"

填入:
```json
{
  "historical_baseline": {
    "source": "JBF 2022 Made in China 2025 事件研究",
    "n": 47,
    "mean_car": 0.099,
    "std_car": 0.045,
    "window": "[-5, +5]"
  }
}
```

**找不到历史** → `historical_baseline: null` (不编)。

## Step 3: 贝叶斯收缩 (代码算, 不让 LLM 算)

```python
# 处理样本不足
def shrink_to_zero(baseline, k=10):
    if baseline is None:
        return {"mean": 0.0, "std": 0.0, "ci95": "未知"}
    
    n = baseline["n"]
    mean = baseline["mean_car"]
    std = baseline["std_car"]
    w = n / (n + k)
    
    return {
        "mean": w * mean,  # 收缩到 0
        "std": w * std,
        "ci95": f"[{w*mean - 1.96*w*std:.3f}, {w*mean + 1.96*w*std:.3f}]",
        "shrinkage_w": w
    }
```

## Step 4: posterior (代码算)

```python
# 收缩后 mean 转成"关注度" posterior
# mean > 0 → 政策偏利好 → posterior > 0.5
# 简化映射: posterior = 0.5 + mean/0.2 (cap [0, 1])
def car_to_posterior(car_result):
    mean = car_result["mean"]
    pos = 0.5 + mean / 0.2
    return max(0.0, min(1.0, pos))
```

## Step 5: 阈值卡线

threshold = 0.65 (政策不分 run_type, 固定 3 日窗)
posterior >= threshold → 保留

## 输出

```json
[{
  "item_id": "policy_001",
  "source": "发改委",
  "topic": "可再生能源",
  "policy_type": "产业规划",
  "policy_phase": "落地",
  "summary": "...",
  "posterior": 0.78,
  "version": "B",
  "version_specific": {
    "historical_baseline": {"n": 47, "mean_car": 0.099, "src": "JBF 2022"},
    "car_after_shrink": {"mean": 0.073, "ci95": "[0.041, 0.105]"},
    "issuing_level": "部委"
  }
}]
```

## 严禁

- LLM 不算收缩 (代码算)
- LLM 不算 posterior (代码算)
- LLM 只做: 主题分类 + 政策类型 + 历史基线查找 (联网) + 政策阶段判断
- 没找到历史基线 → 标 null, 不编
- posterior > 0.5 不等于"该政策利好" (那是 chief 的工作)

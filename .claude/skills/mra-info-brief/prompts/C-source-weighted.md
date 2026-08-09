# C 版本: 源加权派 (公司公告)

**适用**: 公司公告 (policy_news 巨潮公告部分)
**核心**: 流程性公告砍掉, 实质公告按源 tier + 金额加权

---

## 输入

```json
[{
  "pub_time": "2026-08-08",
  "source": "巨潮公告",
  "title": "四川双马:关于以集中竞价方式回购公司股份方案的公告暨回购报告书",
  "content": "...",
  "ticker": "000935",        // 代码, DB 预处理
  "company_name": "四川双马",
  "market_cap": 250_0000_0000  // 流通市值, DB 预处理
}]
```

## Step 1: 流程性公告过滤 (代码做, LLM 不参与)

```python
# 关键词 + 市值 + 金额三道关
NOISE_KEYWORDS = [
    "董事会决议", "独立董事专门会议", "股东大会决议", "监事会决议",
    "会议通知", "章程修订", "工商变更", "会计师事务所变更",
    "提示性公告", "进展公告", "停牌核查"
]

def is_market_moving(item):
    title = item["title"]
    
    # 1. 强关键词保留
    STRONG_KW = ["回购", "增持", "减持", "质押", "解除质押", "重组", "吸并",
                 "并购", "中标", "重大合同", "业绩预增", "业绩预减", "扭亏",
                 "立案", "监管函", "警示函", "ST", "退市", "摘牌", "复牌",
                 "海外大单", "产业链", "技术突破", "扩产", "涨价"]
    if any(kw in title for kw in STRONG_KW):
        return True, f"强关键词: {[k for k in STRONG_KW if k in title][0]}"
    
    # 2. 流程性关键词砍
    if any(kw in title for kw in NOISE_KEYWORDS):
        return False, f"流程性: {[k for k in NOISE_KEYWORDS if k in title][0]}"
    
    # 3. 中小盘
    if item.get("market_cap", 0) < 100_0000_0000:  # < 100亿
        return False, f"中小盘: {item.get('market_cap', 0)/1e8:.0f}亿"
    
    return False, "无明确市场级信号"
```

**预计 90% 在 Step 1 被砍**, 剩 10% 进 LLM。

## Step 2: LLM 摘要 (每条)

```json
{
  "item_id": "notice_001",
  "source": "巨潮公告",
  "ticker": "000935",
  "company_name": "四川双马",
  "topic": "回购",  // LLM 自由分类
  "notice_type": "回购方案",  // 业绩/重组/减持/回购/质押/...
  "direction": "利好",  // 利好/利空/中性/待定
  "summary": "公司拟以集中竞价方式回购股份, 用于...",
  "evidence": ["方案明确", "自有资金", "..."],
  "version": "C"
}
```

## Step 3: posterior (代码算, 隐式贝叶斯)

```python
def compute_posterior(item):
    # 源 tier 权重 (巨潮都是公告, 自身不算"源"差异, 这里用"类型"代替)
    type_weight = {
        "重组": 0.9, "并购": 0.85, "退市": 0.95, "ST": 0.9, "立案": 0.85,
        "回购": 0.7, "业绩预增": 0.7, "业绩预减": 0.7, "扭亏": 0.7,
        "减持": 0.65, "增持": 0.65, "质押": 0.5, "解除质押": 0.4,
        "中标": 0.6, "重大合同": 0.7, "海外大单": 0.75
    }
    tw = type_weight.get(item.get("notice_type", ""), 0.3)
    
    # 方向 (利好偏正面 posterior, 利空偏负面 posterior, 这里只算"关注度")
    # 关注度 = 重要程度, 不是市场反应方向
    # posterior > 0.5 = 值得关注, < 0.5 = 噪音
    pos = tw  # 0~1, 越高越值得关注
    return pos
```

## Step 4: 阈值卡线

threshold = 0.50 (公告量大, 阈值低)
posterior >= 0.50 → 保留

## 输出

```json
[{
  "item_id": "notice_001",
  "source": "巨潮公告",
  "ticker": "000935",
  "company_name": "四川双马",
  "topic": "回购",
  "notice_type": "回购方案",
  "direction": "利好",
  "summary": "...",
  "posterior": 0.7,
  "version": "C",
  "version_specific": {
    "type_weight": 0.7,
    "market_cap": 250_0000_0000
  }
}]
```

## 严禁

- LLM 不算 posterior (代码算)
- LLM 不做"是否值得关注" 判断 (代码基于 type_weight 算)
- LLM 只做: 主题分类 + 公告类型 + 利好/利空方向 + 一句话摘要
- 流程性公告不进 LLM (代码砍)
- 不写"建议买入/卖出", 只写"利好/利空/中性/待定"
- ticker 必须来自输入, 不从记忆补

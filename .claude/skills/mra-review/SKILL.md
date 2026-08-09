---
name: mra-review
description: 复盘分析师 — 基于本地日线 + 板块数据, 用 7 层 / 9 维度框架 (L0 情绪/L0.5 涨停生态/L1 指数/L2 风格/L3 行业/L3.5 资金加速/L4 板块效应/L5 资金流/L6 个股) 做每日复盘报告, 落库 review_daily + HTML。因子计算太复杂已剔除
---

# 复盘分析 (Review) — Market Scout 版

**方法论来源**: 借鉴 `dark-magician/select-stock-pro` 的「市场分析」7 层框架 (A 股市场状态分析助手), **剔除因子 ICIR (H/I 两脚本, 28 因子 × 历史 z-score 太重, 计算 5-8 分钟/次, 我们跑不动)**, 整合 Market Scout 已有 `quant/daily_compute.py` (15 个任务) + `quant/review_compute.py` (板块效应) 形成精简版。

**核心定位**:
- 跟 `info_brief` (信息面 4 路 LLM 提炼) 和 `strategist` (战略推理) **完全独立** — 纯本地日线 + 板块数据, **不调 LLM**, **不读快讯/政策/公告**
- 是"量化技术分析"复盘, 跟"信息面解读"复盘是两条腿
- 输出 `review_daily` 表 (按 trade_date 缓存, INSERT OR REPLACE)

---

## 输入 (调用方提供)

```python
{
  "trade_date": "2026-08-07",   # 收盘数据日期 (最近一个交易日, 非执行日)
  "include_html": true,         # 是否同时渲染 HTML 报告
  "as_of": "2026-08-10"         # 执行日期 (报告生成时间戳用)
}
```

**日期约定 (最高优先级)**: 所有 `trade_date` 必须是最近一个交易日, 不用"今天"。

---

## 9 维度框架 (整合自 select-stock-pro 7 层, 剔除因子)

| 层次 | 名称 | 覆盖内容 | 我们用哪个 compute | 表 |
|------|------|---------|------------------|-----|
| **L0** | 情绪周期 | zt/dt/zb/lianzban/次日溢价/情绪阶段 | `compute_market_emotion` | `market_emotion` |
| **L0.5** | 涨停生态 | 涨停家数/炸板率/连板高度/次日溢价/封板率 | `compute_lianzban_stats` + `compute_lianzban_chain` | `lianzban_stats`, `lianzban_chain` |
| **L1** | 指数层 + 宽度 | 12 宽基指数/MA 上方占比/中位数收益/成交额 | `compute_turnover_stats` + `compute_advance_decline` | `turnover_stats`, `advance_decline` |
| **L2** | 风格层 | 大盘 vs 小盘/超大盘 vs 中盘/价值 vs 成长 | (从 `compute_market_cap_dist` 派生) | `market_cap_dist` |
| **L3** | 行业增强 | 板块涨停密度/板块资金流加速/抱团 | `compute_sector_zt_density` + `compute_sector_flow_acceleration` | `sector_zt_density`, `sector_flow_accel` |
| **L3.5** | 概念热度 | 概念涨停密度 + 概念资金流 | `compute_concept_zt_density` | `concept_zt_density`, `concept_flow` |
| **L4** | 板块效应 | 板块效应深度 (主力/散户资金流/市值/涨跌停/情绪评分/抱团度/轮动/新晋个股) | `quant/review_compute.py:compute_daily_analysis` | `review_daily` (写入) |
| **L5** | 筹码 + 竞价 | 筹码状态/板块筹码压力/集合竞价情绪 | `compute_chip_status` + `compute_sector_chip_pressure` + `compute_call_auction_stats` + `compute_sector_auction_sentiment` | `chip_status`, `sector_chip_pressure`, `call_auction_stats`, `sector_auction_sentiment` |
| **L6** | 个股层 | 涨停股池/跌停股池/连板股池/突破股池 | `compute_volume_breakout` + (读) `zt_pool`/`dt_pool` | `volume_breakout`, `zt_pool`, `dt_pool_v3` |
| **L7** | 研报活跃 | 研报覆盖活跃度 (机构关注度信号) | `compute_research_activity` | `research_activity` |

**剔除的层**:
- L2.5 因子 ICIR (28 因子 × 历史 z-score, 5-8 分钟/次, 跑不动) — 改为文字说明"因子层跳过, 详见 DM-kun skill"

---

## 工作流程 (固定 4 步)

### Step 1: 收集 9 维度数据 (并行)

调用 `quant/daily_compute.py` 里所有相关 compute 函数, **每个 try/except 包裹**, 单任务失败不影响其他。

```python
from quant.daily_compute import (
    compute_market_emotion,           # L0
    compute_lianzban_stats,           # L0.5
    compute_lianzban_chain,           # L0.5
    compute_turnover_stats,           # L1
    compute_advance_decline,          # L1
    compute_sector_zt_density,        # L3
    compute_sector_flow_acceleration, # L3
    compute_concept_zt_density,       # L3.5
    compute_volume_breakout,          # L6
    compute_chip_status,              # L5
    compute_sector_chip_pressure,     # L5
    compute_call_auction_stats,       # L5
    compute_sector_auction_sentiment, # L5
    compute_research_activity,        # L7
)
for fn in [...]:  # 上面 14 个
    try: fn(trade_date)
    except Exception as e: logger.warning(...)

# L2 风格层: compute_market_cap_dist
compute_market_cap_dist(trade_date)

# L4 板块效应: quant/review_compute.py (单独慢, 异步)
from quant.review_compute import compute_daily_analysis
sector_data = compute_daily_analysis(trade_date)
```

**总耗时预估**: 14 个 daily_compute 各 1-3 秒 (读 DB), L4 sector 单独跑 (5000+ 股票 × 250 天, 30-60 秒), 合计 1-2 分钟。

### Step 2: 从各表读 9 维度汇总数据

每个表读 `WHERE trade_date = ?` 的最新 row, 整理成 dict, 供 HTML 渲染:

```python
emotion = read_market_emotion(trade_date)             # zt/zb/dt/lianzban/次日溢价/炸板率
lianzban = read_lianzban_stats(trade_date)            # 连板分布
breadth = read_advance_decline(trade_date)            # MA10/MA20 上方占比
turnover = read_turnover_stats(trade_date)            # 总成交额
sector_density = read_sector_zt_density(trade_date)   # 板块涨停密度排序
sector_flow = read_sector_flow_accel(trade_date)      # 板块资金流加速排序
concept_density = read_concept_zt_density(trade_date) # 概念涨停密度
chip = read_chip_status_summary(trade_date)           # 筹码: 集中度中位数等
auction = read_call_auction_stats(trade_date)         # 竞价: 高开/低开家数
volume_breakout = read_volume_breakout(trade_date)    # 突破股
research = read_research_activity(trade_date)         # 研报覆盖
sector = sector_data  # L4 板块效应全量
```

### Step 3: 综合判断 (核心 1 段)

不是 LLM 跑, 是**规则化的综合判断** (跟 info_brief 那种 LLM 提炼不同):
- **情绪阶段**: 涨停 30-50 = 常态, >50 = 修复/高潮, <30 = 冰点
- **宽度信号**: MA20 上方占比 > 60% = 健康, 30-60% = 震荡, <30% = 弱势
- **板块轮动**: 涨停密度前 3 行业 vs 上一次报告前 3, 标注"新晋"/"掉队"
- **资金信号**: 板块资金流加速 Top 5 vs 减速 Top 5, 找"资金切换"
- **筹码信号**: 高集中度 (90%+) 比例高 = 抱团, 跟板块轮动结合看

### Step 4: 输出报告 + 落库

写到 `tmp/mra-{run_id}/review_report.md` + `.html`, **INSERT OR REPLACE** `review_daily` 表 (按 trade_date 主键覆盖)。

---

## 输出 (固定 8 段 HTML 格式)

```html
<div class="header">📊 复盘分析 — 2026-08-07 (收盘数据)</div>

<!-- 1. 核心判断 (浅色主旋律) -->
<div class="core">情绪阶段: 修复 | 宽度: 健康 | 资金切换: 电子→通信</div>

<!-- 2. L0 + L0.5 情绪周期 + 涨停生态 -->
<div class="section">🔥 L0 情绪周期
  涨停 42 家 / 跌停 8 家 / 炸板率 16% / 最高板 6B
  次日溢价 +1.8% (中位数) / 情绪阶段: 修复 → 发酵边缘
</div>

<!-- 3. L1 指数层 + 宽度 -->
<div class="section">📈 L1 指数层 + 宽度
  沪深 300 +0.8% / MA20 上方占比 62% / 中位数收益 +0.3%
  全市场成交额 1.2 万亿 (环比 +5%)
</div>

<!-- 4. L2 风格层 -->
<div class="section">🎨 L2 风格层
  大盘 vs 小盘: -0.5pp (略偏大盘) / 价值 vs 成长: +0.3pp (略偏价值)
</div>

<!-- 5. L3 + L3.5 行业 + 概念 -->
<div class="section">🏭 L3 行业增强
  涨停密度 Top 5: 电子(8%) 通信(7%) 机械(5%) 计算机(5%) 医药(4%)
  资金流加速 Top 5: 通信 +12% / 电子 +8% / 医药 -3% / 银行 -5% / 地产 -8%
</div>

<!-- 6. L4 板块效应深度 -->
<div class="section">💰 L4 板块效应
  主力净流入 +156 亿 / 散户净流入 -82 亿
  抱团度: 高 (电子/通信双核)
  新晋个股 Top 3: xxxx
</div>

<!-- 7. L5 筹码 + 竞价 -->
<div class="section">🎰 L5 筹码 + 竞价
  筹码集中度 90%+: 32% (高位)
  集合竞价: 高开 1450 家 / 低开 2100 家 (偏空)
</div>

<!-- 8. L6 + L7 个股 + 研报 -->
<div class="section">🎯 L6 个股池
  涨停股池: 42 条
  连板股池: 6 条
  突破股池: 28 条
</div>
```

---

## 跟其他 agent 的区别

| 维度 | info_brief | strategist | **review (本 skill)** |
|------|-----------|-----------|---------------------|
| 数据源 | 4 路信息 (快讯/政策/公告/研报) | info_brief 输出 + 4 路底稿 | **本地日线 + 板块数据** |
| LLM | ✓ (1-2 分钟) | ✓ (1-2 分钟, 双保险) | **✗ 纯计算 (1-2 分钟)** |
| 输出 | 主旋律 + 跨路 + 关注点 | 6 框架推理 | **9 维度 + 综合判断** |
| 缓存 | agent_summary (按 run_type) | agent_summary (run_type=strategist) | **review_daily (按 trade_date)** |
| 触发 | 4 cron/天 + HTTP | 4 cron/天 + HTTP | **盘后 16:30 cron + HTTP** |
| Auto-notify | ✓ (企微) | ✓ (企微) | **✗ 暂不推** |

---

## 工程要点

1. **每个 compute 独立 try/except** — 单任务失败不影响其他 (学习 `quant/daily_compute.py` 的写法)
2. **L4 sector 是慢任务** — 30-60 秒, 单独跑, 失败也允许 review 落库 (没有 sector 部分的 review 仍然有效)
3. **INSERT OR REPLACE** — 复盘可能重算, 同 trade_date 覆盖 (跟 chief/strategist 每次新增不同)
4. **无 LLM** — 复盘是确定性计算, 不需要 LLM 提炼; LLM 提取不稳定性反而是噪音
5. **9 维度全有 fallback** — 某表数据缺失时, 该层标"暂无数据", 不阻断整份报告

---

## 严禁

- ❌ **不要调 LLM** — 复盘是确定性量化分析, 引入 LLM 会失稳
- ❌ **不要新增 daily_compute 函数** — 9 维度 + 14 个 compute 已经够用, 想加新分析先在文档中讨论
- ❌ **不要"建议买入/卖出"** — 复盘是"摆数据", 战略建议交给 `strategist` skill
- ❌ **不要做 7 层 → 5 层的简化** — 9 维度已经精简过 (剔了因子), 不能再砍

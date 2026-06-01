# Agent 分析模块开发规范

## 一、定位与边界

### 这个工具是什么

**短线情绪周期观测仪**，专门服务于主观短线/波段交易（持仓周期 3~20 天）。

它回答且只回答三个问题：
1. **今天市场适不适合操作？**（情绪状态：冷淡/启动/发酵/高潮/退潮）
2. **哪些板块有共振信号，处于哪个阶段？**（萌芽/爆发/退潮）
3. **今日哪些股票最像主线龙头？**（T0-T3 分级候选名单）

### 这个工具不是什么

| 不做 | 原因 |
|------|------|
| 预测明天涨跌 | 做不到，说了是幻觉 |
| 给出买卖价格/仓位 | 不在这个工具的观测角度 |
| 分析行业基本面 | 那是另一个工具（研报分析工具）的职责 |
| 判断长期主线（月/季度级别） | 数据粒度不支撑 |
| 覆盖 20+ 板块 | 信息太多等于没有信息 |

### 与未来研报分析工具的关系

```
market-radar（方向层）
  → 今天值得看的板块 + T0-T3 候选名单
      ↓
研报分析工具（深度层）[未来独立工具]
  → 这个行业基本面是否支撑本次行情？龙头公司质地如何？
```

两个工具各司其职。market-radar 回答"看哪里"，研报工具回答"值不值"。

---

## 二、核心交易框架（所有 Agent 输出必须服务于此）

```
产业催化（政策/新闻）← 分类器识别
    ↓
板块首只涨停出现    ← 量化介入点
    ↓
板块共振（涨停密度≥10%）← 最强信号，Agent 重点跟踪
    ↓
资金确认（龙虎榜/主力净流入）← 盘后才完整
    ↓
分析师集中上调    ← 滞后信号，持仓期参考
```

每条 Agent 结论都要能定位到信号链的哪个环节。

---

## 三、运行时机：8次/天

| 时间 | 类型 | 核心任务 |
|------|------|---------|
| **06:00** | 完整报告（盘前） | 隔夜消息消化 + 延续昨日盘后判断，标注"待竞价确认" |
| **09:25** | 竞价快照 | **唯一能拿到集合竞价数据的时间点**，判断今日高低开预期 |
| **10:30** | 增量更新 | 开盘1小时，板块方向是否按盘前预判展开 |
| **11:30** | 增量更新 | 上午收盘，主线是否确认 |
| **13:30** | 增量更新 | 午后开盘半小时，下午主攻方向 |
| **14:30** | 增量更新 | 机构大资金通常在此段动作 |
| **15:30** | 收盘快照 | 全市场含科创板/北交所收盘，盘面总结，**龙虎榜未出** |
| **18:00** | 完整报告（盘后）| 龙虎榜到位，**最重要的一次**，生成 T0-T3 定案名单 |

### 两种输出格式

**完整报告**（06:00 / 15:30 / 18:00）：市场状态 + 板块分析 + T0-T3 完整名单，用户认真阅读的格式。

**增量更新**（09:25 / 10:30 / 11:30 / 13:30 / 14:30）：只回答"和上次相比变化了什么"，格式极简 3-5 条。**没有变化时必须明确输出"无明显变化，维持上次判断"，不生成废话。**

---

## 四、候选股分级：T0-T3

| 级别 | 数量 | 定性 | 用途 |
|------|------|------|------|
| **T0** | 1-3只 | 主线确认龙头，板块共振+封板质量双优 | 重点跟踪，次日可考虑参与 |
| **T1** | 3-5只 | 同主线副龙头或质量略次的强势跟风 | 备选，T0 进不去再看 |
| **T2** | 5-8只 | 萌芽板块首板，或成交额异动但未涨停 | 观察，等待共振确认 |
| **T3** | 5-8只 | 题材相关但弱于前三级，或隔日留意标的 | 纯监控，不主动介入 |

**合计约 20 只**。T0/T1 是"今天要盯的"，T2/T3 是"放在雷达上的"。这个差异必须在 UI 上有清晰区分。

---

## 五、Agent 架构

### 5.1 运行流程

```
数据层（SQLite）
    ↓
[新闻分类器]   classifier.py     → 过滤噪音，输出结构化新闻摘要
    ↓
[风控时机Agent] risk_agent.py    → 门控层，判断 market_mode
    ↓ （market_mode = "不操作" 时，股票 Agent 不运行）
[板块叙事Agent] sector_agent.py  → 识别共振板块和生命周期阶段
    ↓
[个股候选Agent] stock_agent.py   → 从涨停池生成 T0-T3 名单
    ↓
[结果整合]     orchestrator.py   → 组装最终 JSON，写入 agent_summary 表
```

### 5.2 新闻分类器（`classifier.py`）

**现状**：已实现，基于关键词规则。

**功能**：过滤无关噪音，输出结构化分类 + 紧急度。

**输出格式**：
```json
{
  "category": "政策|产业|个股|海外|市场情绪|无关",
  "urgency": 2,
  "sector_tags": ["低空经济", "AI"],
  "summary": "15字以内摘要",
  "keep": true
}
```

**过滤规则**：urgency=1 且 category 为"无关"或"市场情绪" → 丢弃。每次传给下游上限 20 条。

**待升级**：当前关键词规则对"产业催化"类新闻识别不足，尤其是新兴题材（比如"低空经济"首次出现时关键词库没有）。后续可接入 Qwen 7B 本地小模型。

### 5.3 风控时机 Agent（`risk_agent.py`）

**现状**：已实现，基于硬规则。

**门控逻辑**：`market_mode = "不操作"` 时，`should_run_stock_agent = False`，个股候选 Agent 不运行，避免在极弱市场生成无意义名单。

**硬规则**（Python 计算，不走 LLM）：
```python
涨停数 < 10           → "不操作"
涨停数 < 20           → "谨慎"
跌停数 > 涨停数 × 0.5 → "谨慎"
```

**政策周期识别**（关键词匹配）：
- 吹风期：出现"研究""探索""考虑""推进"
- 征求意见：出现"征求意见""意见稿"
- 正式落地：出现"正式发布""印发""实施"

**输出格式**：
```json
{
  "market_mode": "正常|谨慎|不操作",
  "policy_phase": "吹风|征求意见|正式落地|无明显政策",
  "active_sector_status": {"低空经济": "健康"},
  "reason": "涨停42只，炸板率12%",
  "should_run_stock_agent": true
}
```

### 5.4 板块叙事 Agent（`sector_agent.py`）

**现状**：已实现基于规则的骨架，待接入 LLM。

**LLM 的价值所在**：连接"政策新闻"→"受益板块"的叙事逻辑，以及识别跨申万行业的主题共振（比如"人形机器人"横跨机械、电子、汽车三个申万行业）。这是纯规则做不到的。

**接入 LLM 后的输入（结构化表格，非自然语言）**：
```
今日涨停密度 Top5 板块（industry, zt_density, zt_count, max_lianzban）
近3日板块资金流累计 Top5（sector_name, main_inflow_3d）
机构资金加速度 Top5（industry, accel_ratio）
过滤后政策/产业新闻（来自分类器，urgency≥2，最多20条）
```

**Prompt 核心约束**：
- 最多输出 3 个活跃板块，不铺开
- 每个板块判断必须引用具体数字（涨停密度X%、净流入X亿）
- 禁止使用"较多""较强""明显"等无法量化的表述
- 判断依据不足时输出 `"stage": "data_insufficient"`

**输出格式**：
```json
{
  "top_sectors": [
    {
      "name": "低空经济",
      "stage": "爆发|萌芽|退潮|data_insufficient",
      "evidence": "涨停密度14.2%，机构3日净流入8.3亿，发改委昨日印发支持文件",
      "risk": "持续第3天，关注退潮信号"
    }
  ],
  "market_breadth": "强|中|弱",
  "theme_coherence": "集中|分散",
  "warning": "",
  "source": "rule_based|llm"
}
```

### 5.5 个股候选 Agent（`stock_agent.py`）

**现状**：已实现基于规则的评分，待接入 LLM（盘后完整报告时使用）。

**LLM 的价值所在**：区分龙头和跟风。这是评分规则做不好的——规则能算分，但不能理解"这只股票是这个题材的原生公司还是硬蹭的"。

**接入 LLM 后的强制链式推理**（prompt 中要求 Agent 依次回答）：
1. 首板还是几板？（cite 连板次数字段）
2. 封板时间是否在 10:30 前？（cite 首封时间字段）
3. 所属板块是否在活跃板块名单中？（cite 板块名称）
4. 是否出现在龙虎榜？（盘后才可用，盘中标注 data_gap）
5. 公司主营业务是否与题材直接相关？（基于 F10 基本面数据）

**T0-T3 分级标准**：

| 级别 | 条件组合 |
|------|---------|
| T0 | 活跃板块内 + 连板≥2 + 封板≤10:30 + 未炸板 + （龙虎榜机构净买入）|
| T1 | 活跃板块内 + 首板 + 封板≤10:30，或 T0 条件缺一 |
| T2 | 活跃板块内但未涨停 + 成交额异动(5d/20d>2x)，或萌芽板块首板 |
| T3 | 题材相关 + 处于量价启动位 + 未涨停 |

**输出格式**：
```json
{
  "T0": [
    {
      "ticker": "300XXX",
      "name": "XX科技",
      "direction": "短线|波段",
      "evidence": "首板，09:42封板，无炸板，板块低空经济爆发期，成交额2.3x立桩",
      "entry_note": "次日竞价溢价>2%且缩量可考虑",
      "risk_note": "板块第3天，注意退潮",
      "confidence": "高|中|低",
      "data_gaps": ["龙虎榜未出"]
    }
  ],
  "T1": [],
  "T2": [],
  "T3": [],
  "source": "rule_based|llm"
}
```

**硬性规则**：
- 禁止从训练记忆生成股票名，必须从输入的涨停池列表中选取
- 低置信度候选在 UI 中灰显，不隐藏
- 数据缺失标注 `data_gaps`，不编造

---

## 六、防幻觉通用规则（所有 LLM Agent 的 system prompt 必须包含）

```
规则1：每个判断必须引用输入数据中的具体字段名和数值。
       例如："涨停密度14.2%"而非"涨停较多"。

规则2：某维度数据缺失时，该字段输出 null，标注 data_gap。
       禁止根据通用知识推断。

规则3：股票名称和代码必须来自输入列表，禁止从训练记忆生成。

规则4：输入数据不足以支持判断时，输出：
       {"error": "INSUFFICIENT_DATA", "reason": "缺少XXX数据"}
       不生成流畅但无依据的废话。
```

---

## 七、完整报告的输出结构

盘前（06:00）、收盘（15:30）、盘后（18:00）三次完整报告，最终输出结构：

```json
{
  "run_type": "morning|closing|evening",
  "run_time": "2026-06-01 18:00:00",
  "market_status": {
    "mode": "正常",
    "emotion_score": "中性偏强",
    "zt_count": 47,
    "dt_count": 12,
    "zb_rate": "11%",
    "max_lianzban": 3,
    "yesterday_premium": "2.3%",
    "policy_phase": "吹风"
  },
  "active_sectors": [
    {
      "name": "低空经济",
      "stage": "爆发",
      "evidence": "...",
      "risk": "..."
    }
  ],
  "candidates": {
    "T0": [...],
    "T1": [...],
    "T2": [...],
    "T3": [...]
  },
  "summary_text": "200字以内的自然语言摘要，供前端直接展示",
  "data_completeness": {
    "lhb_available": true,
    "quant_data_date": "2026-05-30",
    "news_count": 18
  }
}
```

增量更新（09:25 / 10:30 / 11:30 / 13:30 / 14:30）的精简结构：

```json
{
  "run_type": "intraday_update",
  "run_time": "2026-06-01 10:30:00",
  "changes": [
    "↑ 低空经济涨停密度从6%→11%，越过共振阈值",
    "→ 昨日T1候选 300XXX 已封板，09:52",
    "- 无其他明显变化，维持上次判断"
  ],
  "no_change": false
}
```

---

## 八、后端实现：`agent/orchestrator.py`（待开发）

### 8.1 入口函数

```python
def run_agent_analysis(run_type: str) -> dict:
    """
    run_type: "morning" | "auction" | "intraday" | "closing" | "evening"
    
    流程：
    1. 从 SQLite 读取上下文数据
    2. 调用分类器处理新闻
    3. 调用风控 Agent
    4. 如 should_run_stock_agent=True，调用板块 Agent + 个股 Agent
    5. 组装 JSON 结果
    6. 写入 agent_summary 表（同时存 content 文本和 data_snapshot_json）
    7. 返回结果供 API 接口使用
    """
```

### 8.2 数据读取（基于现有 `get_agent_context()`）

`get_agent_context()` 已在 `db/storage.py` 实现，需扩展以支持分级候选所需数据：

```python
# 需要新增的查询（在 get_agent_context 中补充）：
- market_emotion 今日记录
- sector_zt_density 今日 top 10
- sector_flow_accel 今日
- volume_breakout 今日
- lianzban_chain 今日（2板+）
- lhb_data 今日（可能为空，盘后才有）
- research_report 近3日评级上调
- fundamentals_f10 当日涨停股的基本面简介（供个股 Agent 判断主营业务相关性）
```

### 8.3 LLM 调用配置

通过 `.env.local` 配置，支持多模型切换：

```bash
# .env.local 新增配置项
AGENT_MODEL=claude          # "claude" | "deepseek" | "rule_only"
CLAUDE_API_KEY=sk-ant-...
DEEPSEEK_API_KEY=sk-...
AGENT_ENABLED=true          # false 时退化为纯规则模式
```

`rule_only` 模式：不调用任何 LLM，完全使用现有规则代码，适合离线/测试场景。

### 8.4 调度器集成（`scheduler.py` 需新增）

```python
from agent.orchestrator import run_agent_analysis

# 在 start_scheduler() 中添加：
scheduler.add_job(lambda: _auto_run("Agent盘前", lambda: run_agent_analysis("morning")),
                  "cron", hour=6, minute=0)
scheduler.add_job(lambda: _auto_run("Agent竞价", lambda: run_agent_analysis("auction")),
                  "cron", hour=9, minute=25)
scheduler.add_job(lambda: _auto_run("Agent更新", lambda: run_agent_analysis("intraday")),
                  "cron", hour=10, minute=30)
scheduler.add_job(lambda: _auto_run("Agent更新", lambda: run_agent_analysis("intraday")),
                  "cron", hour=11, minute=30)
scheduler.add_job(lambda: _auto_run("Agent更新", lambda: run_agent_analysis("intraday")),
                  "cron", hour=13, minute=30)
scheduler.add_job(lambda: _auto_run("Agent更新", lambda: run_agent_analysis("intraday")),
                  "cron", hour=14, minute=30)
scheduler.add_job(lambda: _auto_run("Agent收盘", lambda: run_agent_analysis("closing")),
                  "cron", hour=15, minute=30)
scheduler.add_job(lambda: _auto_run("Agent盘后", lambda: run_agent_analysis("evening")),
                  "cron", hour=18, minute=0)
```

---

## 九、后端 API（`server.py` 需新增/修改）

### 现有接口（保留）

```
GET /api/ai-summary
→ 返回 agent_summary 表最新一条（content + summary_time）
```

### 需要新增的接口

```
GET /api/agent/latest
→ 返回最新完整报告的结构化 JSON（data_snapshot_json 字段反序列化后返回）
→ 包含 run_type、market_status、active_sectors、candidates（T0-T3）

GET /api/agent/history?limit=10
→ 返回最近 N 条报告摘要（run_type、run_time、summary_text）
→ 供前端展示历史记录

POST /api/agent/trigger
→ 手动触发一次 Agent 分析
→ body: {"run_type": "evening"}  （不传则默认根据当前时间判断）
→ 异步执行，立即返回 {"status": "started", "task_id": "..."}
→ 通过轮询 /api/agent/latest 获取结果

GET /api/agent/status
→ 返回当前 Agent 运行状态
→ {"running": false, "last_run": "18:00:00", "last_run_type": "evening"}
```

---

## 十、前端展示要求（供前端开发 Agent 参考）

### 10.1 页面位置

新增独立页面 **`AgentPage.tsx`**（或在现有 `MarketSentimentPage.tsx` 中新增 section），路由 `/agent`。

### 10.2 手动触发按钮

```
[立即分析] 按钮
- 点击后调用 POST /api/agent/trigger
- 按钮变为 loading 状态，文字改为"分析中..."
- 轮询 /api/agent/status，running=false 时刷新页面数据
- 旁边显示"上次运行：18:00 盘后报告"
```

### 10.3 候选股展示规范

T0-T3 必须**视觉上有明显层级差异**，不能是同一个列表：

```
T0 [今日重点]          → 大卡片，红/橙色边框，最显眼
T1 [备选关注]          → 中卡片，正常边框
T2 [雷达监控]          → 小列表行，字体略小
T3 [扩展关注]          → 折叠区，默认收起，展开才显示
低置信度股票           → 灰色/透明度降低，不隐藏
data_gaps 有内容时     → 显示 ⚠ 标记 + tooltip 说明缺少什么数据
```

### 10.4 增量更新 vs 完整报告的展示差异

- 完整报告：展示完整的 T0-T3 卡片
- 增量更新：只展示 `changes` 列表，用 `↑ ↓ →` 前缀标注变化方向
- 顶部标注报告类型和时间："盘后完整报告 · 18:03" / "盘中更新 · 10:30"

### 10.5 历史记录

页面底部展示今日所有报告的时间线：

```
[06:00 盘前] [09:25 竞价] [10:30 更新] [11:30 更新] ... [18:00 盘后✓]
```

点击任意时间节点可查看该次报告内容，方便用户回溯今日分析的演变过程。

---

## 十一、开发优先级

### Phase 1（立即可做，不需要 LLM）
- [ ] `agent/orchestrator.py` 骨架，串联现有四个 Agent
- [ ] `db/storage.py` 扩展 `get_agent_context()` 补充缺少的查询
- [ ] `server.py` 新增 `/api/agent/latest`、`/api/agent/trigger`、`/api/agent/status`
- [ ] `scheduler.py` 注册 8 个定时任务
- [ ] 前端 `AgentPage.tsx` 基础版：展示规则模式下的分析结果 + 手动触发按钮

### Phase 2（接入 LLM）
- [ ] `sector_agent.py` 接入 Claude Haiku 或 DeepSeek V3
- [ ] `stock_agent.py` 接入 Claude Sonnet（盘后完整报告时使用）
- [ ] `.env.local` 新增 `AGENT_MODEL` / `CLAUDE_API_KEY` / `DEEPSEEK_API_KEY`
- [ ] 增量更新使用轻量模型，完整报告使用 Sonnet

### Phase 3（优化迭代）
- [ ] `classifier.py` 升级为 Qwen 7B 本地模型（不依赖外部 API）
- [ ] 跑 2 周数据后，评估哪些时间节点的更新是冗余的，裁剪频率
- [ ] 龙虎榜席位识别（游资 vs 机构，建立白名单）

---

## 十二、日成本估算

| 调用 | 模型 | token/次 | 次数/天 | 日成本 |
|------|------|---------|--------|--------|
| 板块叙事（完整报告） | Claude Haiku | ~2000 | 3 | ~$0.02 |
| 个股候选（完整报告） | Claude Sonnet | ~3000 | 3 | ~$0.15 |
| 增量更新 | Claude Haiku | ~800 | 5 | ~$0.01 |
| 新闻分类 | 本地规则/Qwen | - | - | $0 |
| **合计** | | | | **~$0.20/天** |

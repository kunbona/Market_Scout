# 开发任务：AgentPage 前端页面

## 一、当前进度

### 已完成（后端全部就绪，无需改动）

| 文件 | 说明 |
|------|------|
| `agent/classifier.py` | 新闻分类器，关键词规则，已实现 |
| `agent/risk_agent.py` | 风控门控，硬规则，已实现 |
| `agent/sector_agent.py` | 板块叙事，规则骨架，已实现 |
| `agent/stock_agent.py` | 个股候选，规则评分 + T0-T3 分级，已实现 |
| `agent/orchestrator.py` | 四个 Agent 串联编排，已实现 |
| `db/storage.py` | `get_agent_context()` 已扩展，历史查询已加 |
| `server.py` | 4 个新 API 端点已注册 |
| `scheduler.py` | 8 个定时任务已注册 |

### 待开发（你的任务）

**唯一剩余工作：前端 `AgentPage.tsx`**

---

## 二、接入点

### 路由
`App.tsx` 第 364 行已预留：
```tsx
case 'ai-analysis':
  return <div>TODO: AI Analysis Page</div>;  // ← 替换这里
```

将 `TODO` 替换为 `<AgentPage />`，并在文件顶部 import 即可。

### API 工具函数
统一使用 `src/lib/api.ts` 中的 `apiFetch<T>(path)`，签名：
```ts
async function apiFetch<T>(path: string): Promise<T>
// 成功返回 json.data，失败 throw Error
```

---

## 三、后端 API

### `GET /api/agent/latest`
返回最新一次分析报告（完整结构）。

**完整报告结构**（run_type 为 morning / closing / evening）：
```json
{
  "run_type": "evening",
  "run_time": "2026-06-01 18:03:00",
  "summary_time": "2026-06-01 18:03:01",
  "market_status": {
    "mode": "正常",
    "zt_count": 47,
    "dt_count": 12,
    "zb_rate": "11.0%",
    "max_lianzban": 3,
    "yesterday_premium": "2.3%",
    "policy_phase": "吹风",
    "reason": "涨停47只，炸板率11.0%"
  },
  "active_sectors": [
    {
      "name": "低空经济",
      "stage": "爆发",
      "evidence": "涨停密度14.2%，涨停8只",
      "risk": "密度已达高位，关注退潮"
    }
  ],
  "market_breadth": "强",
  "theme_coherence": "集中",
  "candidates": {
    "T0": [ { "ticker": "300XXX", "name": "XX科技", "direction": "短线",
              "evidence": "连板2板，封板09:42，板块低空经济活跃",
              "entry_note": "次日竞价缩量可考虑", "risk_note": "",
              "confidence": "高", "data_gaps": [], "tier": "T0" } ],
    "T1": [...],
    "T2": [...],
    "T3": [...],
    "source": "rule_based"
  },
  "summary_text": "市场正常，涨停47只；活跃板块：低空经济；T0候选：XX科技",
  "data_completeness": {
    "lhb_available": true,
    "quant_data_date": "2026-05-30",
    "news_count": 18
  }
}
```

**增量更新结构**（run_type 为 intraday_update）：
```json
{
  "run_type": "intraday_update",
  "run_time": "2026-06-01 10:30:00",
  "changes": [
    "→ 涨停47只，市场正常",
    "→ 涨停密度最高板块：低空经济 14.2%",
    "↑ 发改委印发低空经济支持文件"
  ],
  "no_change": false
}
```

**无数据时**：`data` 字段为 `null`，直接展示占位提示即可。

---

### `GET /api/agent/history?limit=20&today=true`

参数：
- `limit`：返回条数，默认 20
- `today`：`true` 时只返回今日记录

返回数组，每项：
```json
{
  "id": 42,
  "summary_time": "2026-06-01 18:03:01",
  "run_type": "evening",
  "run_time": "2026-06-01 18:03:00",
  "summary_text": "市场正常，涨停47只；..."
}
```

---

### `POST /api/agent/trigger`

body（JSON）：
```json
{ "run_type": "evening" }
```
`run_type` 可选，不传时后端根据当前时间自动推断。

返回：
```json
{ "status": "started", "run_type": "evening" }
```
或（已在运行中）：
```json
{ "status": "already_running", "message": "Agent 正在运行，请稍后" }
```

---

### `GET /api/agent/status`

```json
{
  "running": false,
  "last_run": "18:03:01",
  "last_run_type": "evening",
  "last_error": null
}
```

---

## 四、页面结构

```
AgentPage
├── 顶部栏
│   ├── 报告类型 + 时间标签（"盘后完整报告 · 18:03"）
│   ├── [立即分析] 按钮
│   └── data_completeness 警告（龙虎榜未出 / 数据日期）
│
├── 完整报告视图（run_type ≠ intraday_update）
│   ├── 市场状态卡片（mode / zt_count / zb_rate / max_lianzban / policy_phase）
│   ├── 活跃板块列表（最多3个，含 stage badge + evidence + risk）
│   └── 候选股 T0-T3 分级展示（见下方规范）
│
├── 增量更新视图（run_type = intraday_update）
│   └── changes 列表（↑ ↓ → 前缀，保留原始符号）
│
└── 今日时间线（页面底部）
    └── 点击时间节点查看历史报告
```

---

## 五、候选股展示规范

**T0-T3 必须视觉上有明显层级差异，不能是同一个列表。**

| 层级 | 展示方式 | 视觉 |
|------|----------|------|
| T0（今日重点） | 大卡片，每行1张 | 橙/红色左边框，背景略深 |
| T1（备选关注） | 中卡片，每行2张 | 普通边框 |
| T2（雷达监控） | 紧凑列表行，字体略小 | 无边框 |
| T3（扩展关注） | 折叠区，默认**收起** | 折叠，展开后同 T2 样式 |

**每张卡片必须展示：**
- 股票名称 + 代码
- `evidence` 字段（证据，原文输出，不要重新措辞）
- `entry_note`（进场提示）
- `confidence` badge（高/中/低）
- `data_gaps` 不为空时显示 ⚠ 图标 + tooltip 说明缺什么数据
- `confidence === '低'` 的股票：灰显（opacity 降低），**不隐藏**

---

## 六、触发按钮行为

```
点击 [立即分析]
  → POST /api/agent/trigger
  → 按钮变 loading（"分析中..."），禁用点击
  → 每 2 秒轮询 GET /api/agent/status
  → status.running = false 时：
      停止轮询
      GET /api/agent/latest 刷新数据
      按钮恢复
```

按钮旁边显示："上次运行：18:03 盘后报告"（来自 status.last_run + last_run_type）

---

## 七、今日时间线

页面底部展示今日所有运行记录：

```
[06:00 盘前]  [09:25 竞价]  [10:30 更新]  [11:30 更新]  ...  [18:00 盘后 ✓]
```

数据来源：`GET /api/agent/history?limit=20&today=true`

- 点击任意节点，将该次报告的 `data_snapshot_json` 渲染到主区域
- 当前显示的节点高亮
- 未运行过的时间节点显示为灰色（不可点击）

run_type → 显示文字映射：
```
morning       → 盘前
auction       → 竞价
intraday_update → 更新
closing       → 收盘
evening       → 盘后
```

---

## 八、run_type 说明

| run_type | 中文标签 | 输出格式 |
|----------|----------|----------|
| `morning` | 盘前 | 完整报告 |
| `auction` | 竞价快照 | 增量更新 |
| `intraday` / `intraday_update` | 盘中更新 | 增量更新 |
| `closing` | 收盘快照 | 完整报告 |
| `evening` | 盘后报告 | 完整报告（最重要） |

判断是否渲染完整报告视图：`run_type !== 'intraday_update'`

---

## 九、无数据状态处理

- `/api/agent/latest` 返回 `null`：显示占位卡"尚无分析报告，点击[立即分析]生成"
- `market_status.mode === '不操作'`：在市场状态卡片顶部显示红色警告条"市场极弱，今日不建议操作"
- `should_run_stock_agent` 为 false 时后端不会生成候选股，T0-T3 均为空数组，前端显示"市场条件不足，无候选股"

---

## 十、文件清单

需要新建/修改的文件：

```
dashboard/src/
├── pages/
│   └── AgentPage.tsx          ← 新建，主页面
├── components/
│   └── agent/                 ← 建议新建目录
│       ├── MarketStatusCard.tsx
│       ├── SectorList.tsx
│       ├── CandidateCard.tsx  ← T0/T1 卡片
│       ├── CandidateRow.tsx   ← T2/T3 列表行
│       └── RunTimeline.tsx    ← 今日时间线
└── App.tsx                    ← 第 364 行替换 TODO
```

组件拆分建议，非强制，以清晰易读为准。

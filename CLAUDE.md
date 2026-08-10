# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在本仓库中工作时提供指引。

## 项目概览

Market Scout — A 股 Scout 派监控仪表盘。Flask 后端 + React SPA 前端。整合多源财经数据 + 本地量价 + LLM 推理 (周期定位 / 顶底量化 / 信息情报 / 战略推理 / 9 维度复盘)。

**技术栈：** Flask + Waitress（生产 WSGI）、React 18 + TypeScript + Vite + Tailwind CSS v4、APScheduler（后台调度）、SQLite（`db/market.db`）。

## 构建与运行命令

### Python 后端

```bash
pip install -r requirements.txt
python server.py                    # 启动 Flask，端口 20026（或 FLASK_PORT）
bash start.sh                       # Linux/WSL2：加载 .env.local 后启动
start.bat                           # Windows：自动检测 WSL2/Docker/RSSHub 后一键启动
```

没有 pytest 配置文件。用 `unittest` 运行单个测试：

```bash
python -m pytest tests/test_dt_pool_v3.py        # 或：
python -m unittest tests.test_dt_pool_v3
```

### 前端

```bash
cd dashboard
npm install
npm run dev          # Vite 开发服务器，端口 20027（需自行配置 proxy 转发 /api 到 Flask 20026）
npm run build        # TypeScript 检查 + Vite 构建 → dashboard/dist/
npm run lint         # ESLint
```

生产流程是 `npm run build` 然后 `python server.py` —— Flask 把 `dashboard/dist/` 作为静态 SPA 提供服务。前端 `apiFetch()` 向同源发送请求（没有独立的 API 基地址），因此只有 Flask 托管前端构建产物时才能正常工作。

运行前端测试：

```bash
cd dashboard
node --import tsx --test tests/flowFormatters.test.ts
```

## 配置

所有运行时配置放在 `.env.local`（git 忽略，模板为 `.env.example`）。关键变量：

| 变量 | 用途 |
|---|---|
| `FLASK_PORT` | Flask 监听端口（默认 20026） |
| `RSSHUB_BASE_URL` | RSSHub 代理地址，RSS 类新闻源依赖此项 |
| `QUANT_DATA_ROOT` | Parquet 日线数据根目录，情绪分析区依赖此项 |
| `PYTHON_EXECUTABLE` | 子进程 spawn 时使用的 Python 解释器路径（Agent 编排用） |
| `AGENT_ENABLED` | 是否启用 Agent 定时分析 |
| `QMT_ENABLED` / `QMT_PATH` | miniQMT 集成，用于市场宽度等数据 |

`server.py` 在 import 时通过内置函数加载 `.env.local`；`core/scheduler.py` 重复了此逻辑（调度器可能独立运行）。两者均使用 `os.environ.setdefault()`，因此已存在的环境变量优先级更高。

`core/python_runtime.py` 提供 `get_python_executable()`：若设置了 `PYTHON_EXECUTABLE` 环境变量则返回该值，否则返回 `sys.executable`。Agent 编排层通过此函数获取正确的 Python 路径来 spawn 子进程。

## 架构

### 数据流

```
APScheduler (core/scheduler.py) → fetcher/*.py → db/storage.py → SQLite
                                                                   ↓
Flask API 路由 (server.py) ← db/storage.py getter ←───────────────┘
        ↓
React SPA (dashboard/dist/)，由 Flask 在 / 路径托管
```

### 调度器（`core/scheduler.py`）

单个 `BackgroundScheduler`，在 `server.py` 主进程中启动。抓取器按 cron 调度运行：新闻 5 分钟、政策/市场数据 15-30 分钟、龙虎榜/股东人数等每日一次。每个自动任务通过 `_auto_run()` 包装，同步更新 `core/fetch_status.py` 中的共享状态（受 `threading.Lock` 保护的字典，供 `server.py` 的 `/api/fetch-all-status` 端点读取）。

手动全量抓取（`/api/fetch-all`）通过 `concurrent.futures.ThreadPoolExecutor` 在线程中运行抓取器。

### 抓取层（`fetcher/`）

每个 `.py` 文件是一个独立的数据源模块，暴露出顶层 `fetch()`（或类似命名）函数，调用外部 API 后通过 `db/storage.py` 的 insert 函数写入 SQLite。数据源包括：

- **快讯：** `cls_news.py`（财联社直连 API）、`global_news.py`（基于 RSSHub：金十/格隆汇/东财/同花顺/第一财经/华尔街见闻）
- **政策：** `policy_rss.py`（RSSHub + 巨潮公告）
- **市场：** `sector_heat.py`（涨停/跌停/炸板/强势股池 + 概念热度）、`eastmoney.py`（行业资金流/龙虎榜/融资融券/大宗交易/股东人数/解禁/行业排行）、`concept_flow.py`（同花顺概念资金流）、`market_sentiment.py`（人气飙升/北向/雪球/大单异动）、`market_breadth.py`（涨跌家数，可能使用 QMT）
- **QMT/xtquant：** `qmt_data_api.py`、`qmt_monitors.py`、`xtquant_breadth.py`、`xtquant_limit_down.py` — 可选 miniQMT 集成，提供全市场统计和跌停监控
- **研报：** `research.py`（东方财富研报）
- **工具：** `http_util.py`（带重试的共享 requests Session）、`backfill.py`（历史数据回填逻辑）

### 数据库（`db/storage.py`）

所有数据库访问均通过此模块。使用 `threading.Lock`（`_write_lock`）序列化 SQLite WAL 的写操作。内联迁移函数 `_migrate()` 在检测到缺少列时通过 `ALTER TABLE ADD COLUMN` 追加。没有迁移框架（无 Alembic）—— 仅靠 `PRAGMA table_info` 检查。

### 量价计算层（`quant/`）

基于 `QUANT_DATA_ROOT` 下的 Parquet 文件进行本地日线分析：

- `loader.py` — 读取 Parquet 文件（日线 K 线因子）
- `daily_compute.py` — `run_daily_compute()` 计算情绪指标（KPI 卡片、连板梯队、行业涨停密度等）并写入各表
- `security_meta.py` — 证券元数据工具

### Agent 系统（`agent/`）

多 Agent 分析编排，由定时调度或手动触发：

1. **第一阶段（并行）：** 4 个分析师子进程（`mra-emotion`、`mra-sector`、`mra-news`、`mra-risk`）+ 串行 `mra-scout`
2. **第二阶段：** 首席子进程（`mra-chief-morning` 或 `mra-chief-evening`）综合裁决

这些**不是** Python 模块 —— 它们是 Claude Code skill，通过 `agent/orchestrator.py` 中的 `subprocess` 启动。每个 skill 将中间 JSON 写入 `tmp/mra-{run_id}/`。首席 skill 读取所有中间文件，通过 `agent/write_result.py` 将最终结果写入 `agent_summary` 表。运行状态通过 `/api/agent/status` 暴露（前端轮询）。

关键文件：`orchestrator.py`（主编排入口）、`classifier.py`（基于关键词的新闻分类器）、`query.py`（Agent 数据查询）、`write_result.py`（持久化 Agent 输出）。

### 前端（`dashboard/src/`）

单页 React 应用，客户端 Tab 路由由 `App.tsx` 管理。页面与 Tab 对应关系：

| 页面 | 路由 key | 功能 |
|---|---|---|
| `NewsPage.tsx` | `news` | 7 路财经快讯聚合 |
| `PolicyPage.tsx` | `policy` | 政策/监管动态 |
| `MarketRealtimePage.tsx` | `market` | 市场实时数据（涨停池、资金流、龙虎榜等） |
| `MarketSentimentPage.tsx` | `sentiment` | 本地日线情绪指标 |
| `ResearchPage.tsx` | `research` | 研报列表 + PDF 预览 |
| `QmtDataPage.tsx` | `qmt` | QMT 监控套件（可选） |
| `AgentPage.tsx` | `agent` | Agent 分析状态与报告 |

`api.ts` 提供唯一的 `apiFetch<T>(path)` 辅助函数。所有 API 调用走同源 `/api/*`。

状态管理为逐页 `useState`/`useEffect` —— 无全局状态库。`useSettings.ts` 是唯一的共享 Hook（主题、每页条数偏好）。

### 子项目：`quant-trader/`

`quant-trader/` 是一个独立的量化实盘交易框架。有自己的 `AGENTS.md`、conda 环境（`quantENV`）和 skills。主 Market Scout 项目不从它导入代码；二者仅共享同一个 Git 仓库。

## 关键约定与注意事项

- **分支策略：** `lite` 是活跃开发分支，`main` 是稳定分支。始终在 `lite` 上开发。
- **无 .env.example：** 该文件在 `lite` 分支上已删除。直接使用 `.env.local`。
- **Python 子进程 spawn：** spawn Python 子进程（Agent、量价计算）时，务必使用 `core.python_runtime.get_python_executable()` 获取正确的解释器路径，该函数会遵循 `.env.local` 中的 `PYTHON_EXECUTABLE` 设置。
- **SQLite WAL + 写锁：** SQLite 使用 WAL 模式。`db/storage.py` 有模块级 `_write_lock`（`threading.Lock`）—— 所有写操作必须通过 storage 函数，禁止直接使用原始 `sqlite3` 连接。
- **Schema 迁移：** 在 `db/storage.py` 的 `_migrate()` 中通过 `PRAGMA table_info` + `ALTER TABLE ADD COLUMN` 添加新列。无迁移框架。
- **RSSHub 可选：** RSSHub 不可用时，RSS 类抓取器静默失败，对应新闻源显示为空。系统设计上支持优雅降级。
- **前端开发 vs 生产：** `npm run dev` 在 20027 端口启动 Vite，但 API 调用走同源 —— 需自行配置 Vite proxy 将 `/api/*` 转发到 Flask 的 20026 端口。文档标准流程是 `npm run build` + Flask 托管 SPA。
- **QMT/miniQMT：** 可选数据源，需要本机运行 miniQMT 客户端。`QMT_ENABLED` 控制 QMT 类抓取器是否尝试连接。QMT 不可用时市场宽度数据静默跳过。
- **量价数据根目录：** `QUANT_DATA_ROOT` 指向 Parquet 数据集目录。未配置时情绪 Tab 显示为空，不会报错。
- **APScheduler 进程内运行：** 调度器与 Flask 共享同一进程，无独立 worker。多 worker 部署会导致重复执行定时任务；当前单 worker Waitress 部署避免了此问题。
- **日志：** 根 logger 设为 `WARNING` 以压制第三方库噪音日志（apscheduler、werkzeug）。自定义 `PIPELINE` 级别（25）用于 Agent 对话日志。所有抓取器模块使用 `logging.getLogger(__name__)`。

## 并发 / I/O 基础设施速查

> **加新功能前先 grep 现有基础设施** —— 90% 的并发/批量场景已经有现成实现，重复造轮子会撞上别人已经解决过的坑（macOS ulimit 256、bridge 限速、CSV file handle 累积等）。

### CSV 批量读（**优先用这个**）

`quant/loader.py:209 _load_window(trade_date, lookback)` —— 并行读 `stock-trading-data-pro/*.csv` 整个目录，返回合并 DataFrame。

- **并发控制**：`QUANT_WORKERS` env（默认 CPU 核数一半）。**项目所有并行读 CSV 任务统一用这个 env**。
- **后端选择**：WSL 强制 ThreadPoolExecutor（避免 fork 死锁），其他系统 ProcessPoolExecutor。
- **用途**：需要"全市场 5200+ 只股票按日期过滤的数据"时直接调它；按 `stock_code` groupby 算指标。
- **不要**自己写 `for code in codes: get_trading_data(code)` 循环 5000+ 次 —— 这会撞 macOS ulimit 256（`pd.read_csv` 不关 file handle，5200 次累积触发 "Too many open files"）。

### 单只 CSV 读

`quant/loader.py:403 get_trading_data(code)` —— 读**单只**股票 CSV（已用 `with open()` 防 file handle 累积）。

- **只用于**精确知道"我需要这一只的某些列"的场景（如 `compute_up_limit` 算涨跌停价）。
- **不要**在循环里调 1000+ 次 —— 性能差且容易撞 I/O 瓶颈。**优先考虑**能否用 `_load_window` 一次拿全量再过滤。

### QMT bridge 批量调用

`fetcher/qmt_client.py` 通用 RPC client，白名单见 `tools/qmt-bridge/allowlist.py`。

- **配置**：`QMT_BRIDGE_URL` + `QMT_BRIDGE_TOKEN`（`.env.local`）。
- **已批量化的调用**：`get_full_tick_snapshot(codes)` 一次拿全市场 5200+ 只 tick；`get_market_data(period="1d", ...)` 拉日 K。
- **降级语义**：bridge 未配 / 超时 / 方法白名单拒绝 → 全部返回 `None`（**不抛异常**）。调用方要做 `if not data` 兜底。
- **慢速调用**：`get_security_meta(code)` 单只调用 ~50ms。**对 5000 只全调必超时** —— 必须先粗筛（`fetcher/sector_heat.py:dt_pool_v3` 是参考实现）。

### dt_pool_v3 two-pass 经验（**bridge 瓶颈场景的标准解法**）

适用场景：bridge 慢、单只调用 ~50ms、但大部分 stock 都不是目标。

```python
# 第一遍：纯内存粗筛（用已经在 ticks 字典里的 last_price/last_close）
candidates = [c for c, t in ticks.items()
              if t.get("last_price") and t.get("last_close")
              and t["last_price"] <= t["last_close"] * 0.95]

# 第二遍：只对 candidates 调 bridge
for code in candidates:
    meta = get_security_meta(code)  # ~50ms × 几十 = < 5s
    ...
```

**第一遍 zero bridge call**，第二遍 candidates 通常 0-几十只。**对 5000 只全调 = 250s 超时**。

### Flask 启动期任务顺序

`server.py` `if __name__ == "__main__":` 段是启动入口。**耗时 ≥ 30s 的同步任务必须在 scheduler 启动前完成**：

```python
# 1. _raise_file_limit()  — 调高 ulimit
# 2. _load_env_local()    — 读 .env.local
# 3. _bootstrap_industry_stats_warmup() + _wait_for_industry_stats_cache()
#    — 同步 warmup（在 scheduler 之前，独占 I/O）
# 4. start_scheduler()    — 启动后台任务
# 5. start_flask()        — 接受请求
```

**反例**：先启 scheduler 再 warmup，scheduler 的 `fetch_dt_pool_v3` (1min 一次) / `fetch_zt_pool` / `fetch_concept_flow` 会跟 warmup 抢 I/O，warmup 40s 算力被分散到 5+ 分钟，waitress 队列堆 7+ 个同步算请求 → UX 灾难。

### launchd 进程管理

- `pkill -f server.py` **杀不死** launchd 守护的进程（plist 在 `~/Library/LaunchAgents/com.kun.marketradar.plist`），会被 KeepAlive 自动拉起。
- **正确重启**：`launchctl kickstart -k "gui/$(id -u)/com.kun.marketradar"`。
- **改 plist 后**：`launchctl bootout + launchctl bootstrap`（kickstart 不重读 plist）。
- **进程搜索**：`lsof -nP -tiTCP:20026 -sTCP:LISTEN` 拿真 PID。

### macOS ulimit 兜底

- launchd plist 加 `<key>SoftResourceLimits</key><dict><key>NumberOfFiles</key><integer>8192</integer></dict>`。
- **plist 改动可能不生效**（某些 launchd 版本），需在 `server.py` 启动时主动 `resource.setrlimit(RLIMIT_NOFILE, (8192, hard))` 兜底（见 `_raise_file_limit()`）。

### 进程内 cache 调试

- `import server` 看 cache **没用** —— 新进程不共享内存。
- **用 HTTP API**：`curl http://localhost:20026/api/qmt-industry-stats` 看 server 进程的真状态。
- 如果需要看 server 内部状态，用 `py-spy dump --pid $PID` 抓 stack。

### 调度器 `max_instances` 坑

- `core/scheduler.py` `BackgroundScheduler` `max_instances: 4`（原本是 1）。
- **每个 add_job 可单独覆盖**：`scheduler.add_job(fn, "interval", seconds=30, max_instances=2)`。
- **改全局配置前先看现有任务** —— `1 → 4` 会让所有 5min 间隔的 fetcher（cls_news / eastmoney 等）也并发跑，可能撞上游 API 限速。

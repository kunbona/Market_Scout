# Market Radar

A 股财经信息聚合仪表盘，React 前端 + Flask 后端，实时整合多源财经快讯、政策动态、市场数据、研究报告。

---

## 功能概览

- **财经快讯**：财联社电报（含红电报）、金十数据、格隆汇、东方财富、同花顺、第一财经、华尔街见闻，7 路来源聚合
- **政策动态**：发改委、证监会、上交所、深交所、财新、巨潮公告，多源 RSS + 结构化抓取
- **市场实时**：涨停/跌停/炸板/强势股池、行业/概念资金流、行业板块排行、龙虎榜、大单异动、人气飙升、北向资金、雪球热度、融资融券、大宗交易、股东人数变化、同花顺主题热股、解禁/减持日历
- **情绪分析**：本地日线量价指标（KPI 8 卡、连板梯队、行业密度、换手分层、市值分布、连板链条、成交异动、机构资金加速度）
- **研究报告**：东方财富研报中心今日研报，支持 PDF 在线预览，按个股/行业/宏观/策略分类

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | React 18 + TypeScript + Vite + Tailwind CSS |
| 后端 | Flask + Waitress（生产 WSGI） |
| 数据抓取 | AkShare + feedparser + requests + curl_cffi（Chrome 指纹） + BeautifulSoup |
| 调度 | APScheduler（BackgroundScheduler，随 Flask 启动） |
| 存储 | SQLite（`db/market.db`） |
| 本地量价 | Parquet 日线因子（需自备 `Quant_Data/` 目录） |
| RSS 代理 | RSSHub（Docker 独立服务） |

---

## 快速启动

### Linux

```bash
# 1. 克隆项目（lite 分支）
git clone -b lite <repo>
cd market-radar

# 2. 安装 Python 依赖（推荐 conda 环境）
pip install -r requirements.txt

# 3. 配置本地环境变量
cp .env.example .env.local
# 编辑 .env.local，至少设置 QUANT_DATA_ROOT：
#   QUANT_DATA_ROOT=/path/to/Quant_Data

# 4. 启动 RSSHub（可选，RSS 源降级可跳过）
docker run -d --name rsshub --restart unless-stopped -p 1200:1200 diygod/rsshub

# 5. 构建前端
cd dashboard && npm install && npm run build && cd ..

# 6. 启动应用
bash start.sh
# 或直接: python server.py
```

访问 `http://localhost:20026`

---

### Windows（推荐方案）

> Windows 下 Python 环境直接运行即可，但 RSSHub 需要 Docker，而 Docker Desktop 在 Windows 上底层依赖 WSL2，因此 **RSS 代理部分建议在 WSL2 中启动**。主应用本身可以在 Windows 原生 Python 或 WSL2 中任选其一。

#### 方案一：一键启动（WSL2 运行 RSSHub + Windows 本地 Python）

项目提供 `start.bat` 脚本，可以在 Windows 下一键完成：自动检测 WSL2，在 WSL2 内启动 RSSHub Docker 容器，然后在 Windows 侧启动 Flask 服务。

**前提条件：**

1. 安装 WSL2（Windows 10 21H2 或 Windows 11）

   ```powershell
   # 管理员权限 PowerShell
   wsl --install
   # 重启后在 Ubuntu 终端中设置用户名密码
   ```

2. 在 WSL2 内安装 Docker（二选一）

   ```bash
   # 方法 A：安装 Docker Desktop（推荐，有图形界面）
   # 从 https://www.docker.com/products/docker-desktop/ 下载安装
   # 安装时勾选 "Use WSL 2 instead of Hyper-V"
   # Docker Desktop 安装后，WSL2 内的 docker 命令自动可用

   # 方法 B：在 WSL2 内直接安装 Docker Engine
   curl -fsSL https://get.docker.com | sh
   sudo usermod -aG docker $USER
   # 退出并重新进入终端使权限生效
   ```

3. 安装 Python 3.10+（Windows 侧）并将其加入 PATH

4. 构建前端（仅首次或代码更新后需要执行）

   ```powershell
   cd dashboard
   npm install
   npm run build
   cd ..
   ```

5. 配置 `.env.local`

   ```powershell
   copy .env.example .env.local
   # 用记事本或 VS Code 编辑 .env.local
   ```

   关键配置：

   ```
   RSSHUB_BASE_URL=http://localhost:1200
   QUANT_DATA_ROOT=C:\Users\你的用户名\Quant_Data
   FLASK_PORT=20026
   ```

6. 安装 Python 依赖

   ```powershell
   pip install -r requirements.txt
   ```

**启动方式：**

```powershell
# 双击 start.bat，或在 PowerShell / cmd 中执行：
.\start.bat
```

脚本行为：
- 自动检测 WSL2 和 Docker 是否可用
- 若 `rsshub` 容器不存在则自动创建；若已存在但停止则自动启动
- WSL2 或 Docker 不可用时跳过 RSSHub，RSS 数据源自动降级，其余功能不受影响
- 等待 RSSHub 就绪后再启动 Flask，减少启动初期的抓取失败

访问 `http://localhost:20026`

> **注意**：WSL2 内的容器在 Windows 重启后需要重新启动。可以在 Docker Desktop 设置中启用"Start Docker Desktop when you log in"，让容器跟随开机自动启动，从而保证 RSSHub 始终可用。

---

#### 方案二：WSL2 全环境（包含 RSSHub，无需 Windows Python）

适合希望所有组件统一在 Linux 环境中运行的场景。

**在 WSL2 终端中执行：**

```bash
# 克隆项目（推荐放在 WSL 文件系统内，IO 性能更好）
cd ~
git clone -b lite <repo>
cd market-radar

# 安装 Python 依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env.local
# 编辑 .env.local：
# RSSHUB_BASE_URL=http://localhost:1200
# QUANT_DATA_ROOT=/mnt/c/Users/你的用户名/Quant_Data  ← 若数据在 Windows 磁盘
# FLASK_PORT=20026

# 启动 RSSHub
docker run -d \
  --name rsshub \
  --restart unless-stopped \
  -p 1200:1200 \
  -e NODE_ENV=production \
  -e CACHE_TYPE=memory \
  diygod/rsshub

# 构建前端
cd dashboard && npm install && npm run build && cd ..

# 启动
bash start.sh
```

WSL2 内部服务 Windows 浏览器也可以直接访问：`http://localhost:20026`

> **路径说明**：WSL2 中 Windows 的 `C:\` 对应 `/mnt/c/`，`D:\` 对应 `/mnt/d/`，以此类推。

---

#### 方案三：Windows 原生 Python（不含 RSSHub）

无 Docker/WSL2 环境时的最简方案。RSS 相关来源自动降级，不影响其他数据源。

```powershell
pip install -r requirements.txt
copy .env.example .env.local
# 编辑 .env.local，设置 QUANT_DATA_ROOT 和 FLASK_PORT

cd dashboard
npm install
npm run build
cd ..

python server.py
```

访问 `http://localhost:20026`

---

#### 方案四：Docker Compose（一键启动，适合不需要改代码的部署）

需要先安装 Docker Desktop（Windows）或 Docker Engine（Linux/WSL2）。

```bash
# 构建前端（Docker Compose 不会自动构建前端）
cd dashboard && npm install && npm run build && cd ..

# 启动所有服务（RSSHub + market-radar）
docker compose up -d
```

访问 `http://localhost:20026`

---

## 环境变量

配置文件 `.env.local`（不提交到 git，基于 `.env.example` 复制）：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `RSSHUB_BASE_URL` | `http://rsshub:1200` | RSSHub 服务地址。本地开发用 `http://localhost:1200`；Docker Compose 内部用 `http://rsshub:1200` |
| `QUANT_DATA_ROOT` | `/path/to/Quant_Data` | 本地日线量价数据目录（可选，无此目录则情绪分析区显示空） |
| `FLASK_PORT` | `20026` | Flask 监听端口 |

> RSSHub 不可用时，RSS 相关来源自动降级，不影响系统启动

---

## RSSHub 说明

RSSHub 为财联社红电报、金十数据、格隆汇、第一财经、华尔街见闻等提供 RSS 代理，需要 Docker 环境。

### 验证 RSSHub 可用性

```bash
curl http://localhost:1200/cls/telegraph/red
curl http://localhost:1200/jin10
curl http://localhost:1200/gelonghui/live
curl http://localhost:1200/yicai/brief
curl http://localhost:1200/wallstreetcn/live/a-stock
```

---

## 数据源与更新频率

### Tab 1 — 财经快讯

| 模块 | 来源 | 接口 | 更新频率 |
|------|------|------|---------|
| 财联社电报 | 财联社直连 | `cls.cn/nodeapi/telegraphList` | 5 分钟 |
| 财联社红电报 | RSSHub | `/cls/telegraph/red` | 5 分钟 |
| 金十数据 | RSSHub | `/jin10` | 5 分钟 |
| 格隆汇直播 | RSSHub | `/gelonghui/live` | 5 分钟 |
| 东方财富 | AkShare | `stock_info_global_em` | 5 分钟 |
| 同花顺 | AkShare | `stock_info_global_ths` | 5 分钟 |
| 第一财经 | RSSHub | `/yicai/brief` | 5 分钟 |
| 华尔街见闻 | RSSHub | `/wallstreetcn/live/a-stock`（降级直连） | 5 分钟 |

### Tab 2 — 政策动态

| 模块 | 来源 | 接口 | 更新频率 |
|------|------|------|---------|
| 发改委新闻 | RSSHub | `/gov/ndrc/xwdt/xwfb`、`/tzgg` | 30 分钟 |
| 证监会公告 | RSSHub | `/gov/csrc/news` | 30 分钟 |
| 上交所问询 | RSSHub | `/sse/inquire` | 30 分钟 |
| 深交所问询/公告 | RSSHub | `/szse/inquire`、`/szse/notice` | 30 分钟 |
| 财新新闻 | AkShare | `stock_news_main_cx` | 30 分钟 |
| 巨潮公告 | AkShare | `stock_notice_report` | 30 分钟 |

### Tab 3 — 市场实时（实时行情区，盘中）

| 模块 | 来源 | 接口 | 更新频率 |
|------|------|------|---------|
| 行业资金流 | 东方财富 | `get_sector_flow_latest` | 15 分钟 |
| 概念资金流（387个） | 同花顺 | `stock_fund_flow_concept(即时)` | 15 分钟 |
| 行业板块排行（全市场） | 东方财富 | `push2/api/qt/clist/get` (fs=m:90+t:2) | 5 分钟 |
| 涨停池 | 东方财富 | `stock_zt_pool_em` | 5 分钟 |
| 跌停池 | 东方财富 | `stock_dt_pool_em` | 5 分钟 |
| 炸板池 | 东方财富 | `stock_zt_pool_zbgc_em` | 5 分钟 |
| 强势股池 | 东方财富 | `stock_zt_pool_strong_em` | 15 分钟 |
| 龙虎榜 | 东方财富 | `stock_lhb_detail_em` | 每日 17:30 |
| 大单异动 | 东方财富 | `stock_fund_flow_individual` | 3 分钟 |
| 人气飙升 | 东方财富 | `stock_hot_up_em` | 30 分钟 |
| 北向/南向资金 | 东方财富 | `stock_hsgt_fund_flow_summary_em` | 15 分钟 |
| 雪球热度 top50 | 雪球 | `stock_hot_tweet_xq` | 63 分钟 |
| 全市场涨跌快照 | 东方财富 | `stock_zh_a_spot_em` | 30 秒 |
| 融资融券余额 Top | 东方财富 datacenter | `RPTA_WEB_RZRQ_GGMX` | 30 分钟 |
| 大宗交易 | 东方财富 datacenter | `RPT_DATA_BLOCKTRADE` | 30 分钟 |
| 同花顺主题热股 | 同花顺 | `zx.10jqka.com.cn/event/api/getharden` | 30 分钟 |
| 股东人数变化 | 东方财富 datacenter | `RPT_HOLDERNUMLATEST` | 每日 17:45 |
| 解禁/减持日历（90天） | 东方财富 datacenter | `RPT_LIFT_STAGE` | 每日 09:10 |

### Tab 3 — 市场实时（情绪分析区，本地日线）

本地量价数据存储于 `QUANT_DATA_ROOT`，每日收盘后离线计算。

| 模块 | 指标说明 |
|------|---------|
| KPI 卡片 × 8 | 涨停/跌停、炸板率、溢价、最高连板/晋级率、活跃度、成交额/MA20、换手中位、市值偏好 |
| 连板梯队 | 1/2/3/4板+各层数量与晋级率 |
| 行业涨停密度 | 各申万一级行业涨停占比 |
| 概念涨停热度 | top 15 概念涨停数 |
| 换手率分层 | 涨停股低/中/高换手分布 |
| 涨停市值分布 | 小盘(<50亿)/中盘(50-300亿)/大盘(≥300亿) |
| 连板链条明细 | 2板+个股列表（板数/炸板/行业） |
| 成交额异动 | 5日均量/20日均量 > 2x 的个股 |
| 机构资金加速度 | 3日均值 / 20日均值，按申万行业 |

### Tab 4 — 研究报告

| 模块 | 来源 | 接口 | 更新频率 |
|------|------|------|---------|
| 今日研报 | 东方财富 | `reportapi.eastmoney.com/report/list` | 30 分钟 |
| PDF 预览 | 服务端代理 | `/api/research/pdf?url=...`（注入 Referer） | 按需 |

---

## 数据库表结构

| 表 | 说明 | 保留时长 |
|----|------|---------|
| `cls_news` | 财经快讯（7路来源） | 7 天 |
| `policy_news` | 政策动态 | 90 天 |
| `research_report` | 研究报告 | 90 天 |
| `sector_flow` | 行业资金流 | 30 天 |
| `concept_flow` | 概念板块资金流（387个） | 30 天 |
| `zt_pool` | 涨停池 | 7 天 |
| `dt_pool` | 跌停池 | 7 天 |
| `zbgc_pool` | 炸板股池 | 7 天 |
| `strong_pool` | 强势股池 | 7 天 |
| `lhb_data` | 龙虎榜 | 90 天 |
| `hot_rank_up` | 人气飙升榜 | 7 天 |
| `northbound_flow` | 北向/南向资金 | 30 天 |
| `xq_hot` | 雪球关注热度 top 50 | 7 天 |
| `big_deal` | 大单异动 | 7 天 |
| `market_pulse` | 全市场涨停快照 + 乐咕活跃度 | 30 天 |
| `margin` | 融资融券余额 | 30 天 |
| `block_trade` | 大宗交易 | 30 天 |
| `holder_count` | 股东人数变化 | 30 天 |
| `lockup_expiry` | 解禁/减持日历 | 90 天 |
| `dividend` | 分红历史 | 90 天 |
| `industry_ranking` | 行业板块排行（全市场） | 30 天 |
| `ths_hot_stocks` | 同花顺主题热股 | 7 天 |
| `fundamentals_finance` | mootdx 基本面财务 | 7 天 |
| `fundamentals_f10` | mootdx F10（公司概况/财务分析/股东研究） | 7 天 |
| `agent_summary` | Agent 分析摘要 | 60 天 |
| `market_emotion` | 涨停/跌停/炸板率/最高连板/溢价（日线） | 1 年 |
| `lianzban_stats` | 连板梯队分布 + 晋级率（日线） | 长期 |
| `sector_zt_density` | 申万一级行业涨停密度（日线） | 90 天 |
| `concept_zt_density` | 概念涨停热度（日线） | 90 天 |
| `sector_flow_accel` | 机构资金加速度（日线） | 90 天 |
| `turnover_stats` | 涨停股换手率分层（日线） | 1 年 |
| `market_cap_dist` | 涨停股流通市值分布（日线） | 1 年 |
| `advance_decline` | 全市场涨跌家数 + 成交额/MA20（日线） | 1 年 |
| `volume_breakout` | 成交额异动个股（5d/20d > 2x）（日线） | 90 天 |
| `lianzban_chain` | 连板链条个股明细（2板+）（日线） | 90 天 |
| `chip_status` | 筹码分布（日线） | 90 天 |
| `research_activity` | 机构调研热度（日线） | 90 天 |

---

## 项目结构

```
market-radar/
├── server.py                 # Flask 主程序（入口）
├── scheduler.py              # APScheduler 定时任务
├── fetch_status.py           # 抓取状态共享模块
├── start.sh                  # 启动脚本（Linux/macOS/WSL2，加载 .env.local）
├── start.bat                 # 启动脚本（Windows）
├── requirements.txt
├── docker-compose.yml
├── .env.example              # 环境变量模板
├── dashboard/                # React 前端
│   ├── src/
│   │   ├── pages/
│   │   │   ├── NewsPage.tsx
│   │   │   ├── PolicyPage.tsx
│   │   │   ├── MarketRealtimePage.tsx
│   │   │   ├── MarketSentimentPage.tsx
│   │   │   └── ResearchPage.tsx
│   │   ├── components/       # 公共组件
│   │   └── lib/api.ts
│   └── dist/                 # 构建产物（npm run build 生成）
├── db/
│   ├── storage.py            # SQLite CRUD
│   └── market.db             # 数据库（自动创建，不提交）
├── fetcher/
│   ├── cls_news.py           # 财联社电报
│   ├── global_news.py        # 金十/格隆汇/东财/同花顺/第一财经/华尔街见闻
│   ├── policy_rss.py         # 政策动态 RSS + 巨潮公告
│   ├── research.py           # 东财研报 API
│   ├── eastmoney.py          # 行业资金流/龙虎榜/融资融券/大宗交易等
│   ├── sector_heat.py        # 涨停/跌停/炸板/强势股池 + 概念热度
│   ├── concept_flow.py       # 同花顺概念资金流（387个概念）
│   ├── realtime_quote.py     # 全市场快照 + 乐咕活跃度
│   ├── market_sentiment.py   # 人气飙升/北向资金/雪球热度/大单异动
│   └── fundamentals.py       # mootdx 基本面财务/F10
├── quant/
│   ├── loader.py             # 本地 parquet 读取工具
│   └── daily_compute.py      # 日度计算任务（每日 09:00 触发）
└── agent/                    # Agent 分析层（接口预留）
    ├── classifier.py
    ├── sector_agent.py
    ├── stock_agent.py
    └── risk_agent.py
```

---

## 注意事项

- 数据来源均为公开可访问的网络接口，仅供个人研究使用
- `db/market.db` 不纳入版本控制，首次运行自动创建
- `.env.local` 不纳入版本控制，用于本地环境覆盖
- 前端 `dashboard/dist/` 需手动执行 `npm run build` 生成，不提交到 git
- `QUANT_DATA_ROOT` 未配置时，情绪分析区（本地日线指标）显示为空，不影响其他 Tab
- mootdx 基本面抓取仅在盘中运行，且只针对当日涨停/强势股池中的个股，不扫全市场
- 雪球热度每次抓取约需 50 秒（全量分页），不影响其他并发任务

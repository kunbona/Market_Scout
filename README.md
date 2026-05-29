# Market Radar

A 股财经信息聚合仪表盘，基于 Streamlit 构建，实时整合多源财经快讯、政策动态、市场数据与研究报告。

---

## 功能概览

- **财经快讯**：财联社、金十数据、格隆汇、东方财富、同花顺、第一财经、华尔街见闻，7 路来源实时聚合，三列布局
- **政策动态**：发改委、证监会、上交所、深交所、财新、巨潮公告，多源 RSS + 结构化抓取
- **市场数据**：实时行情区（涨停/跌停/炸板/强势股池、行业/概念资金流、龙虎榜、人气飙升、北向资金、雪球热度）+ 情绪分析区（本地日线指标：KPI 8 卡、连板梯队、行业密度、换手分层、市值分布、连板链条、成交异动等）
- **研究报告**：东方财富研报中心今日研报，按个股/行业/宏观/策略分类展示
- **四 Tab 切换**：JS 驱动无刷新切换，单页承载全部信息

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | Streamlit 1.57 + 纯 HTML/CSS/JS（iframe 渲染） |
| 数据抓取 | AkShare、feedparser、requests、BeautifulSoup |
| 调度 | APScheduler（后台线程，非阻塞启动） |
| 存储 | SQLite（本地文件 `db/market.db`） |
| 本地量价 | Parquet 日线因子（`/mnt/ssd_1T/runist/data/Quant_Data/`） |
| RSS 代理 | RSSHub（Docker 独立服务） |

---

## 数据源与更新频率

### Tab 1 — 财经快讯

| 模块 | 来源 | AKShare / 接口 | 更新频率 | 盘中限制 |
|------|------|---------------|---------|---------|
| 财联社电报 | 财联社直连 | `cls.cn/nodeapi/telegraphList`（rn=50） | 5 分钟 | 无 |
| 财联社加红 | RSSHub | `/cls/telegraph/red` | 5 分钟 | 无 |
| 金十数据 | RSSHub | `/jin10` | 5 分钟 | 无 |
| 格隆汇直播 | RSSHub | `/gelonghui/live` | 5 分钟 | 无 |
| 东方财富 | AKShare | `stock_info_global_em` | 5 分钟 | 无 |
| 同花顺 | AKShare | `stock_info_global_ths` | 5 分钟 | 无 |
| 第一财经 | RSSHub | `/yicai/brief` | 5 分钟 | 无 |
| 华尔街见闻 | RSSHub | `/wallstreetcn/live/a-stock`（降级直连） | 5 分钟 | 无 |

### Tab 2 — 政策动态

| 模块 | 来源 | AKShare / 接口 | 更新频率 | 盘中限制 |
|------|------|---------------|---------|---------|
| 发改委新闻 | RSSHub | `/gov/ndrc/xwdt/xwfb`、`/tzgg` | 30 分钟 | 无 |
| 证监会公告 | RSSHub | `/gov/csrc/news` | 30 分钟 | 无 |
| 上交所问询 | RSSHub | `/sse/inquire` | 30 分钟 | 无 |
| 深交所问询/公告 | RSSHub | `/szse/inquire`、`/szse/notice` | 30 分钟 | 无 |
| 财新新闻 | AKShare | `stock_news_main_cx` | 30 分钟 | 无 |
| 巨潮公告 | AKShare | `stock_notice_report`（重要类型白名单过滤） | 30 分钟 | 无 |

### Tab 3 — 市场数据（实时行情区）

| 模块 | 来源 | AKShare 接口 | 更新频率 | 盘中限制 |
|------|------|-------------|---------|---------|
| 行业资金流 | 东方财富 | `get_sector_flow_latest` | 15 分钟 | 仅盘中 |
| 概念资金流（387个概念） | 同花顺 | `stock_fund_flow_concept(即时)` | 15 分钟 | 仅盘中 |
| 涨停池 | 东方财富 | `stock_zt_pool_em`（含封板时间/封单/炸板次数） | 5 分钟 | 仅盘中 |
| 跌停池 | 东方财富 | `stock_dt_pool_em` | 5 分钟 | 仅盘中 |
| 炸板池 | 东方财富 | `stock_zt_pool_zbgc_em`（含振幅/炸板次数） | 5 分钟 | 仅盘中 |
| 强势股池 | 东方财富 | `stock_zt_pool_strong_em`（含入选理由/新高/量比） | 15 分钟 | 仅盘中 |
| 龙虎榜 | 东方财富 | `stock_lhb_detail_em`（含解读/席位类型/胜率） | 每日 17:30 | 无 |
| 人气飙升榜 | 东方财富 | `stock_hot_up_em`（排名较昨日变动） | 30 分钟 | 仅盘中 |
| 北向/南向资金 | 东方财富 | `stock_hsgt_fund_flow_summary_em` | 15 分钟 | 仅盘中 |
| 雪球关注热度 | 雪球 | `stock_hot_tweet_xq`（top 50，关注人数） | 63 分钟 | 无（全天） |
| 市场活跃度（乐咕） | 乐咕乐股 | `stock_market_activity_legu`（真实涨停/活跃度） | 30 秒 | 仅盘中 |
| 全市场涨跌快照 | 东方财富 | `stock_zh_a_spot_em`（涨停/跌停计数） | 30 秒 | 仅盘中 |

### Tab 3 — 市场数据（情绪分析区，本地日线）

本地量价数据来源：`/mnt/ssd_1T/runist/data/Quant_Data/`，每日收盘后离线计算，通过「⚡ 计算今日数据」按钮触发。

| 模块 | 数据来源（本地 Parquet） | 指标说明 |
|------|------------------------|---------|
| KPI 卡片 × 8 | 涨停相关因子、涨跌幅相关因子、乐咕实时 | 涨停/跌停、炸板率、溢价、最高连板/晋级率、活跃度、成交额/MA20、换手中位、市值偏好 |
| 连板梯队 | 涨停相关因子 | 1/2/3/4板+各层数量 |
| 行业涨停密度 | 涨停相关因子 + 申万行业 | 各申万一级行业涨停占比 |
| 概念涨停热度 | 涨停相关因子 + stock-popular-concept-detail | top 15 概念涨停数 |
| 换手率分层 | 股票预处理数据（amount/circ_mv） | 涨停股低/中/高换手分布 |
| 涨停市值分布 | 股票预处理数据（circ_mv） | 小盘(<50亿)/中盘(50-300亿)/大盘(≥300亿) |
| 连板链条明细 | 涨停相关因子 + 申万行业 | 2板+个股列表（板数/炸板/行业） |
| 成交额异动 | 成交额相关因子（amount_mean_5/20） | 5日均量/20日均量 > 2x 的个股 |
| 机构资金加速度 | 资金流相关因子（机构净买入占比） | 3日均值 / 20日均值，按申万行业 |

### Tab 4 — 研究报告

| 模块 | 来源 | 接口 | 更新频率 |
|------|------|------|---------|
| 今日研报（个股/行业/宏观/策略） | 东方财富 | `reportapi.eastmoney.com/report/list` | 30 分钟 |

---

## 数据库表结构

| 表 | 来源类型 | 说明 | 保留时长 |
|----|---------|------|---------|
| `cls_news` | 外部实时 | 财经快讯（7路来源） | 7 天 |
| `policy_news` | 外部实时 | 政策动态 | 90 天 |
| `research_report` | 外部实时 | 研究报告 | 90 天 |
| `sector_flow` | 外部实时 | 行业资金流 | 30 天 |
| `concept_flow` | 外部实时 | 概念板块资金流（387个） | 30 天 |
| `zt_pool` | 外部实时 | 涨停池（含封板时间/封单/炸板次数） | 7 天 |
| `dt_pool` | 外部实时 | 跌停池 | 7 天 |
| `zbgc_pool` | 外部实时 | 炸板股池 | 7 天 |
| `strong_pool` | 外部实时 | 强势股池 | 7 天 |
| `lhb_data` | 外部实时 | 龙虎榜（含解读/席位类型/胜率） | 90 天 |
| `hot_rank_up` | 外部实时 | 人气飙升榜 | 7 天 |
| `northbound_flow` | 外部实时 | 北向/南向资金 | 30 天 |
| `xq_hot` | 外部实时 | 雪球关注热度 top 50 | 7 天 |
| `market_pulse` | 外部实时 | 全市场涨停快照 + 乐咕活跃度 | 30 天 |
| `agent_summary` | 计算 | Agent 分析摘要 | 60 天 |
| `market_emotion` | 本地日线 | 涨停/跌停/炸板率/最高连板/溢价 | 长期 |
| `lianzban_stats` | 本地日线 | 连板梯队分布 + 1→2/2→3/3→4 晋级率 | 长期 |
| `sector_zt_density` | 本地日线 | 申万一级行业涨停密度 | 90 天 |
| `concept_zt_density` | 本地日线 | 概念涨停热度（来自本地概念文件） | 90 天 |
| `sector_flow_accel` | 本地日线 | 机构资金加速度（3d/20d窗口） | 90 天 |
| `turnover_stats` | 本地日线 | 涨停股换手率分层 | 长期 |
| `market_cap_dist` | 本地日线 | 涨停股流通市值分布 | 长期 |
| `advance_decline` | 本地日线 | 全市场涨跌家数 + 成交额/MA20 | 长期 |
| `volume_breakout` | 本地日线 | 成交额异动个股（5d/20d > 2x） | 90 天 |
| `lianzban_chain` | 本地日线 | 连板链条个股明细（2板+） | 90 天 |
| `call_auction_stats` | 本地日线 | 涨停股集合竞价委比 | 30 天 |
| `chip_status` | 本地日线 | 筹码分布（50/95分位成本、胜率） | 90 天 |
| `research_activity` | 本地日线 | 机构调研热度（5日内调研机构数） | 90 天 |

---

## 快速启动

### 方式一：Docker Compose（推荐）

```bash
git clone <repo>
cd market-radar
docker-compose up -d
```

访问 `http://localhost:20026`

`docker-compose.yml` 同时启动两个服务：
- **RSSHub**：`http://localhost:1200`，为财联社、金十、格隆汇等 RSS 源提供代理
- **market-radar**：Streamlit 应用，端口 20026

### 方式二：本地开发

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动 RSSHub（需要 Docker）
docker run -d -p 1200:1200 diygod/rsshub

# 3. 配置本地环境变量（可选）
cp .env.local.example .env.local
# 编辑 .env.local，设置 RSSHUB_BASE_URL=http://localhost:1200

# 4. 启动应用
streamlit run app.py --server.port 20026
```

---

## RSSHub 配置

项目使用 RSSHub 作为部分财经 RSS 源的代理服务。

### Docker 单独启动

```bash
docker run -d \
  --name rsshub \
  --restart unless-stopped \
  -p 1200:1200 \
  -e NODE_ENV=production \
  -e CACHE_TYPE=memory \
  diygod/rsshub
```

### 验证可用性

```bash
curl http://localhost:1200/cls/telegraph/red
curl http://localhost:1200/jin10
curl http://localhost:1200/gelonghui/live
```

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `RSSHUB_BASE_URL` | `http://172.17.0.1:1200` | RSSHub 服务地址。Docker Compose 内部通信用 `http://rsshub:1200`，本地开发用 `http://localhost:1200` |

> **注意**：若 RSSHub 不可用，RSS 相关来源会降级到直接 HTML 抓取或跳过，系统不会崩溃。

---

## 本地日线数据

本地量价数据存储于 `/mnt/ssd_1T/runist/data/Quant_Data/`，每日收盘后离线更新。

### 手动触发计算

在「市场数据」Tab 侧边栏点击「⚡ 计算今日数据」，依次执行 13 个计算任务并显示进度。

### 数据目录结构

```
Quant_Data/
├── factors/stock/daily/          # 21个日度因子 parquet（涨停/涨跌幅/成交额/资金流/均线/振幅等）
├── stg_cache/预处理数据/           # 主交易数据（14.9M行×40列，含 amount/circ_mv/大户资金）
├── stock-trading-data-pro/       # 各股日度 CSV（GBK，38列）
├── stock-1h-trading-data-pro/    # 各股 1小时 K线 parquet（含换手率/日内特征）
├── stock-popular-concept-detail/ # 各股概念归属（330种概念，86%覆盖率）
├── stock-call-auction-data/      # 集合竞价数据
└── stock-chip-distribution/      # 筹码分布（5/95分位成本/胜率）
```

---

## 项目结构

```
market-radar/
├── app.py                    # Streamlit 主应用，UI 渲染
├── scheduler.py              # APScheduler 定时任务（20个任务）
├── requirements.txt
├── docker-compose.yml
├── db/
│   ├── storage.py            # SQLite CRUD（28张表）
│   └── market.db             # 数据库（自动创建，不提交）
├── fetcher/
│   ├── cls_news.py           # 财联社电报（东财直连，50条/次）
│   ├── global_news.py        # 金十、格隆汇、东财、同花顺、第一财经、华尔街见闻
│   ├── policy_rss.py         # 政策动态 RSS + 巨潮公告
│   ├── research.py           # 东财研报 API
│   ├── eastmoney.py          # 行业资金流、龙虎榜
│   ├── sector_heat.py        # 涨停/跌停/炸板/强势股池
│   ├── concept_flow.py       # 同花顺概念资金流（387个概念）
│   ├── realtime_quote.py     # 全市场快照 + 乐咕活跃度
│   └── market_sentiment.py   # 人气飙升榜、北向资金、雪球热度
├── quant/
│   ├── loader.py             # 本地 parquet 读取工具
│   └── daily_compute.py      # 13个日度计算任务
└── agent/                    # Agent 分析层（规则 stub，待接入 LLM）
    ├── classifier.py
    ├── sector_agent.py
    ├── stock_agent.py
    └── risk_agent.py
```

---

## 注意事项

- 数据来源均为公开可访问的网络接口，仅供个人研究使用
- 东财、同花顺等接口可能随时变更，建议定期关注 [AkShare 文档](https://akshare.akfamily.xyz/)
- `db/market.db` 不纳入版本控制，首次运行自动创建
- `.env.local` 不纳入版本控制，用于本地环境覆盖
- 雪球热度每次抓取约需 50 秒（全量分页），不影响其他任务
- `stock_zh_a_spot_em` 偶发连接中断，乐咕活跃度会独立写入不受影响

# Market Radar

A 股财经信息聚合仪表盘，基于 Streamlit 构建，实时整合多源财经快讯、政策动态、市场数据与研究报告。

---

## 功能概览

- **财经快讯**：财联社、金十数据、格隆汇、东方财富、同花顺、第一财经、华尔街见闻，7 路来源实时聚合，三列布局
- **政策动态**：发改委、证监会、上交所、深交所、财新、巨潮公告，多源 RSS + 结构化抓取
- **市场数据**：行业资金流、涨停池、跌停池、龙虎榜，盘中实时更新
- **研究报告**：东方财富研报中心今日研报，按个股/行业/宏观/策略分类展示
- **四 Tab 切换**：纯 CSS 实现无刷新切换，单页承载全部信息

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | Streamlit 1.57 + 纯 HTML/CSS/JS（iframe 渲染） |
| 数据抓取 | AkShare、feedparser、requests、BeautifulSoup |
| 调度 | APScheduler（后台线程，非阻塞启动） |
| 存储 | SQLite（本地文件 `db/market.db`） |
| RSS 代理 | RSSHub（Docker 独立服务） |

---

## 数据源

### 财经快讯
| 来源 | 接入方式 |
|------|----------|
| 财联社 | 东财 `nodeapi/telegraphList` 直连（50条/次） |
| 财联社加红电报 | RSSHub `/cls/telegraph/red` |
| 金十数据 | RSSHub `/jin10` |
| 格隆汇 | RSSHub `/gelonghui/live` |
| 东方财富 | AkShare `stock_info_global_em` |
| 同花顺 | AkShare `stock_info_global_ths` |
| 第一财经 | RSSHub `/yicai/brief` |
| 华尔街见闻 | RSSHub `/wallstreetcn/live/a-stock`，降级到直连 API |

### 政策动态
| 来源 | 接入方式 |
|------|----------|
| 发改委 | RSSHub `/gov/ndrc/xwdt/xwfb` 及 `/tzgg` |
| 证监会 | RSSHub `/gov/csrc/news` |
| 上交所问询 | RSSHub `/sse/inquire` |
| 深交所问询/公告 | RSSHub `/szse/inquire`、`/szse/notice` |
| 财新 | AkShare `stock_news_main_cx` |
| 巨潮公告 | AkShare `stock_notice_report`（重要类型白名单过滤） |

### 市场数据
| 数据 | 接入方式 | 更新频率 |
|------|----------|----------|
| 行业资金流 | AkShare `get_sector_flow_latest` | 15 分钟（盘中） |
| 涨停池 / 跌停池 | AkShare `get_zt_pool / get_dt_pool` | 5 分钟（盘中） |
| 龙虎榜 | AkShare `get_lhb_data` | 每日 17:05 |

### 研究报告
| 数据 | 接入方式 |
|------|----------|
| 今日研报流 | 东财 `reportapi.eastmoney.com/report/list`，覆盖四类 |

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
# 测试 RSSHub 是否正常运行
curl http://localhost:1200/

# 测试财联社加红电报
curl http://localhost:1200/cls/telegraph/red

# 测试金十数据
curl http://localhost:1200/jin10

# 测试格隆汇
curl http://localhost:1200/gelonghui/live
```

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `RSSHUB_BASE_URL` | `http://172.17.0.1:1200` | RSSHub 服务地址。Docker Compose 内部通信用 `http://rsshub:1200`，本地开发用 `http://localhost:1200` |

> **注意**：若 RSSHub 不可用，RSS 相关来源会降级到直接 HTML 抓取或跳过，系统不会崩溃。

---

## 项目结构

```
market-radar/
├── app.py                  # Streamlit 主应用，UI 渲染
├── scheduler.py            # APScheduler 定时任务
├── requirements.txt
├── docker-compose.yml
├── db/
│   ├── storage.py          # SQLite CRUD 操作
│   └── market.db           # 数据库文件（自动创建，不提交）
└── fetcher/
    ├── cls_news.py         # 财联社电报（东财直连）
    ├── global_news.py      # 金十、格隆汇、东财、同花顺、第一财经、华尔街见闻
    ├── policy_rss.py       # 政策动态 RSS + 巨潮公告
    ├── research.py         # 东财研报 API
    ├── eastmoney.py        # 行业资金流、龙虎榜
    └── sector_heat.py      # 涨停池、跌停池、板块热度
```

---

## 数据库结构

| 表 | 说明 |
|----|------|
| `cls_news` | 财经快讯，含 source、title、content、pub_time、link |
| `policy_news` | 政策动态，含 source、title、link、pub_time |
| `research_report` | 研究报告，含 title、org_name、rating、aim_price、report_url、qtype |
| `sector_flow` | 行业资金流 |
| `zt_pool / dt_pool` | 涨停池 / 跌停池 |
| `lhb_data` | 龙虎榜 |

---

## 注意事项

- 数据来源均为公开可访问的网络接口，仅供个人研究使用
- 东财、同花顺等接口可能随时变更，建议定期关注 [AkShare 文档](https://akshare.akfamily.xyz/)
- 研报链接跳转至东方财富研报详情页，PDF 需在详情页内下载
- `db/market.db` 不纳入版本控制，首次运行自动创建
- `.env.local` 不纳入版本控制，用于本地环境覆盖

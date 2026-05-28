# market-radar 开发者文档

## 项目定位

**market-radar** 是一个运行在本地、展示在网页端的 A 股市场信息聚合看板，服务于**主观龙头选股**决策。

核心目标：
- 从可信信息源（政策原文、财联社电报、资金数据）识别市场方向，而非追热度
- 不扫 5000 只股票，通过信息流 + 聚合指标缩小关注范围
- 实时信号触发时自动召回历史上下文，辅助人工决策

---

## 核心交易哲学（所有开发决策的出发点）

> **板块共振做主线，封板质量选龙头，政策吹风期建仓，落地前止盈。**

这是工具服务的交易逻辑。所有功能和指标都必须能回答：**"这个信号在哪个环节帮助判断"**。

**信号链（主线识别的时序）**：

```
产业催化（政策/新闻触发）← 信息流模块识别
    ↓
板块首只涨停出现         ← 量化可介入
    ↓
板块共振（涨停密度>10%）  ← 最强信号，核心指标
    ↓
资金确认（龙虎榜/主力净流入）← 资金层验证
    ↓
分析师集中上调           ← 滞后信号（持仓期参考）
```

这个工具是**人工决策的辅助器**，不是自动交易系统。Agent 层负责信息整理和信号摘要，最终操作由人完成。

---

## 背景与设计原则

### 为什么要做这个项目

A 股市场约 43.8% 的时间没有明确主线。在不确定的市场中，**从信息流识别方向**比从 5000 只股票中筛选更高效。本项目不是追热度工具，而是一个信息过滤器——通过可信来源的信号变化，提前感知资金流向和政策催化方向。

### 信息来源层级

| 层级 | 来源 | 信噪比 | 市场反应时间 |
|------|------|-------|------------|
| 政策原文 | gov.cn、发改委、证监会、交易所问询函 | 极高 | 0~2 小时 |
| 权威快讯 | 财联社电报、华尔街见闻 | 高 | 5~15 分钟 |
| 研究报告 | 东方财富 reportapi | 高 | 当日内 |
| 资金数据 | 龙虎榜、板块资金流 | 高 | T+0 披露 |
| 板块热度 | 涨停池、概念排行 | 高 | 实时（5min 延迟） |

**应该避免的来源**：股吧/雪球评论区（情绪放大器）、券商 APP 推送（复述当日新闻）。

---

## 技术栈

| 层 | 技术 | 说明 |
|----|------|------|
| 数据抓取 | AKShare + feedparser + requests/BeautifulSoup | 无 LLM，纯爬虫/API |
| 量价计算 | pandas + numpy | 读本地 CSV/parquet，每日批量计算 |
| 存储 | SQLite（本地单文件） | `db/market.db` |
| 定时任务 | APScheduler BackgroundScheduler | 随 Streamlit 一起启动 |
| 前端展示 | Streamlit | 本地运行 `streamlit run app.py` |
| Agent 层 | Claude API / DeepSeek API + 本地 Qwen 7B | Phase 4，见 Agent 章节 |

---

## 目录结构

```
market-radar/
├── fetcher/
│   ├── cls_news.py           # 财联社电报
│   ├── global_news.py        # 东方财富/同花顺/华尔街见闻快讯
│   ├── policy_rss.py         # 政策原文（gov.cn/发改委/证监会/交易所）
│   ├── eastmoney.py          # 龙虎榜 + 板块资金流
│   ├── sector_heat.py        # 涨停池 + 跌停池 + 概念板块热度
│   ├── research_report.py    # 研究报告（东财 reportapi）
│   ├── realtime_quote.py     # 盘中实时行情快照（全市场/指定股票）
│   └── external/             # 扩展信息源，见 external/README.md
├── quant/
│   ├── __init__.py
│   ├── daily_compute.py      # 每日离线批量计算（见"量价计算"章节）
│   └── loader.py             # 本地数据读取工具函数
├── agent/
│   ├── __init__.py
│   ├── classifier.py         # 新闻分类器（本地小模型）
│   ├── sector_agent.py       # 板块叙事 Agent
│   ├── stock_agent.py        # 个股候选 Agent
│   ├── risk_agent.py         # 风控时机 Agent
│   └── README.md             # Agent 接口规范（见 Agent 章节）
├── db/
│   └── storage.py            # 建表 + 所有 CRUD 函数
├── scheduler.py              # 定时任务注册与启动
├── app.py                    # Streamlit 主程序（入口）
├── requirements.txt
└── DEVELOPMENT.md            # 本文档
```

---

## 数据流总览

```
定时任务（scheduler.py）
    ├── 每 5 分钟：快讯抓取（财联社/东财/同花顺/华尔街见闻）
    ├── 每 5 分钟：涨停池/跌停池（交易时间）
    ├── 每 15 分钟：板块资金流（交易时间）
    ├── 每 30 分钟：政策 RSS + 研究报告
    ├── 每日 09:00：量价日度指标批量计算（quant/daily_compute.py）
    ├── 每日 17:30：龙虎榜抓取
    └── 每日 02:00：数据清理（7天/30天老化）
         ↓
  db/storage.py 写入 SQLite
         ↓
  app.py 从 SQLite 读取展示（Streamlit）
         ↓
  Agent 层（按需触发，结果写入 agent_summary 表）
```

---

## 模块一：实时数据抓取

### 1.1 已实现（V1，需检查质量）

| 数据 | fetcher 文件 | AKShare 函数 | 刷新频率 | 存储表 |
|------|-------------|-------------|---------|--------|
| 财联社电报 | `cls_news.py` | `stock_info_global_cls()` | 每 5 分钟 | `cls_news` |
| 东方财富快讯 | `global_news.py` | `stock_info_global_em()` | 每 5 分钟 | `cls_news` |
| 同花顺快讯 | `global_news.py` | `stock_info_global_ths()` | 每 5 分钟 | `cls_news` |
| 华尔街见闻 | `global_news.py` | `stock_info_global_wscn()` | 每 5 分钟 | `cls_news` |
| 政策原文 | `policy_rss.py` | feedparser + HTML scrape | 每 30 分钟 | `policy_news` |
| 板块资金流（行业） | `eastmoney.py` | `stock_sector_fund_flow_rank()` | 每 15 分钟 | `sector_flow` |
| 板块资金流（概念） | `sector_heat.py` | `stock_board_concept_name_em()` | 每 5 分钟 | `sector_flow` |
| 涨停池 | `sector_heat.py` | `stock_zt_pool_em()` | 每 5 分钟 | `zt_pool` |
| 跌停池 | `sector_heat.py` | `stock_dt_pool_em()` | 每 5 分钟 | `dt_pool` |
| 龙虎榜 | `eastmoney.py` | `stock_lhb_detail_em()` | 每日 17:30 | `lhb_data` |

**注意**：`sector_flow` 表必须有 `source_type` 字段区分 `'industry'` 和 `'concept'`，否则两类数据混淆。

### 1.2 待实现：研究报告（`fetcher/research_report.py`）

**接口**：东方财富 reportapi，无需鉴权。

```python
# 全市场最新研报（按时间倒序）
import akshare as ak
df = ak.stock_research_report_em(symbol="")
# 返回字段：title, org（机构）, analyst, date, rating, target_price, pdf_url

# 直连 API（更实时，分钟级）
GET https://reportapi.eastmoney.com/report/list
    ?qType=0        # 0=个股 1=行业 2=宏观 3=策略 4=晨报
    &pageSize=50
    &beginTime=YYYY-MM-DD
```

**存储表**：`research_report`

```sql
CREATE TABLE IF NOT EXISTS research_report (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    pub_date     TEXT,
    stock_code   TEXT,
    stock_name   TEXT,
    institution  TEXT,
    analyst      TEXT,
    rating       TEXT,    -- "买入" / "增持" / "中性" / "减持"
    rating_change TEXT,   -- "上调" / "维持" / "下调" / "首次"
    target_price REAL,
    title        TEXT,
    pdf_url      TEXT,
    fetch_time   TEXT,
    UNIQUE(pub_date, stock_code, institution, title)
);
```

**刷新频率**：每 30 分钟。只保存 `rating_change = '上调'` 或 `'首次'` 的记录，其余过滤，控制噪音。

### 1.3 待实现：盘中实时行情快照（`fetcher/realtime_quote.py`）

盘中轮询，用于支持市场情绪指标实时更新。不保存全量 5000 只股票，只保存聚合指标到 `market_pulse` 表。

**AKShare 函数**：
```python
# 全市场价格快照（含涨停/跌停价格字段）
df = ak.stock_zh_a_spot_em()  # 每次约 5000 行，轮询间隔 >= 10s 防封

# 竞价数据（9:15-9:25）
df = ak.stock_zh_a_hist_pre_min_em(symbol="600000", start_time="09:00:00", end_time="09:30:00")

# 盘中主力资金流快照（同花顺，单次标量）
df = ak.stock_fund_flow_individual(symbol="即时")
# 警告：9:30-9:40 窗口此接口返回列数不稳定，需 try/except + 列数校验
```

**聚合计算（每次轮询后立即计算，写入 `market_pulse` 表）**：
- 全市场涨停家数（当前价 >= 涨停价的股票数）
- 全市场跌停家数
- 炸板数（曾涨停但现在跌破涨停价）
- 涨停/跌停家数比值

**`market_pulse` 表结构**：
```sql
CREATE TABLE IF NOT EXISTS market_pulse (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    fetch_time    TEXT,
    zt_count      INTEGER,   -- 当前涨停家数
    dt_count      INTEGER,   -- 当前跌停家数
    zb_count      INTEGER,   -- 炸板家数（曾涨停后打开）
    zt_dt_ratio   REAL,      -- 涨停/跌停比
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 模块二：量价日度指标（`quant/daily_compute.py`）

### 2.1 本地数据概览

本地数据路径：`/mnt/ssd_1T/runist/data/Quant_Data/`

**读取说明**：

```python
import pandas as pd

# CSV 文件：每股一个文件，GBK 编码，第 0 行是注释行，skiprows=1
df = pd.read_csv("/mnt/ssd_1T/runist/data/Quant_Data/stock-trading-data-pro/sh600000.csv",
                 encoding="gbk", skiprows=1)

# Parquet 文件：全市场宽表，直接读取
df = pd.read_parquet("/mnt/ssd_1T/runist/data/Quant_Data/factors/stock/daily/涨停相关因子.parquet")
```

**已有数据清单**（最新数据截至 2026-05-15）：

| 目录 | 格式 | 内容 | 关键字段 |
|------|------|------|---------|
| `stock-trading-data-pro/` | CSV/股 | 日K + 市值 + 行业 + 资金流 | 开/高/低/收/量/额/流通市值/总市值/机构买入额/大户买入额/散户买入额/机构卖出额.../申万一二三级行业/沪深300等成分标志/09:35-09:55收盘价 |
| `stock-money-flow/` | CSV/日 | 全市场资金流（按日期分文件）| 机构/中户/大户/散户 买入额/卖出额 |
| `stock-chip-distribution/` | CSV/股 | 筹码分布 | 后复权价格/5%-95%分位成本/加权平均成本/胜率 |
| `stock-lhb-organ/` | CSV/日 | 龙虎榜机构席位 | 股票代码/营业部名称/净买入额/买入额/上榜原因 |
| `stock-popular-concept-detail/` | CSV/股 | 概念热度时序 | 总热度(万)/自选热度/关注粘性/所属概念 |
| `stock-analyst-predicted/` | CSV/股 | 分析师预测 | 研究机构/评级/目标价/EPS/净利润/预测年份 |
| `stock-activation-records/` | CSV/股 | 机构调研记录 | 公告日期/参会机构名称/参会机构类别/活动内容简介 |
| `factors/stock/daily/涨停相关因子.parquet` | parquet | 全市场涨停因子 | 收盘涨停/是否炸板/连板次数/昨日连板次数 |
| `factors/stock/daily/资金流相关因子.parquet` | parquet | 资金流占比 | 机构净买入占比/大户净买入占比/中户净买入占比 |
| `factors/stock/daily/成交额相关因子.parquet` | parquet | 成交额统计 | amount_mean/std/sum for 5/10/20/30/60 days |
| `factors/stock/daily/均线相关因子.parquet` | parquet | 均线 | MA3/5/10/13/20/30/60/120/250 |
| `factors/stock/daily/BOLL.parquet` | parquet | 布林带 | MA_20/Upper_20/Lower_20 |
| `factors/stock/daily/波动率.parquet` | parquet | 波动率 | 波动率_5/10/20/30/60 |
| `factors/stock/daily/振幅因子.parquet` | parquet | 振幅 | amplitude_1/5/10/20 |
| `factors/stock/daily/涨跌幅相关因子.parquet` | parquet | 涨跌幅 | pct_chg_1/5/10/20/30/60/120/250 |
| `factors/stock/daily/5min_close.parquet` | parquet | 5分钟分时收盘价 | 覆盖 09:30-15:00 共 48 个时间点 |
| `factors/stock/daily/15min_close.parquet` | parquet | 15分钟分时收盘价 | 16 个时间点 |
| `factors/stock/daily/估值因子.parquet` | parquet | 估值 | BP/EP/EP_单季/SP |
| `factors/stock/daily/ROE.parquet` | parquet | 盈利 | ROE_TTM/ROE_单季 |
| `factors/stock/daily/净利润因子.parquet` | parquet | 利润增速 | 净利润_单季同比/净利润_单季同比加速度 |
| `factors/stock/daily/分析师日度指标.parquet` | parquet | 分析师活跃度 | 当日活跃机构数/当日买入报告数/距前次发报天数 |
| `stock-technical-factors/` | CSV/股 | 技术因子 | ATR/ATR_5/ATR_20（衰减加权） |
| `stock-volume-price-factors/` | CSV/股 | 量价因子 | EOM/Money_Flow/PVT（各有5日/20日衰减加权） |
| `stock-trend-factors/` | CSV/股 | 趋势因子 | MACD/MTM_ma/收集派发_ACD |
| `stock-energy-factors/` | CSV/股 | 能量因子 | VR成交量比率/人气指标BR/中间意愿CR |
| `stock-oscillator-factors/` | CSV/股 | 震荡因子 | coppock/SRMi |

### 2.2 每日批量计算任务（`quant/daily_compute.py`）

**触发时机**：每日 09:00（盘前，确保前一日数据已更新）

**计算任务列表**（结果写入 SQLite 的对应表）：

---

#### 任务 1：市场情绪日度指标 → `market_emotion` 表

从 `涨停相关因子.parquet` 全市场聚合：

| 指标 | 计算方法 | 说明 |
|------|---------|------|
| `zt_total` | 当日 `收盘涨停==True` 的股票数 | 全市场涨停总数 |
| `dt_total` | 当日收盘跌停股票数（需从 trading-data-pro 计算） | 全市场跌停总数 |
| `zb_total` | 当日 `是否炸板==True` 的股票数 | 炸板总数 |
| `max_lianzban` | `连板次数.max()` | 当日最高连板数 |
| `zt_yesterday_premium` | 昨日涨停今日收益率均值 | 涨停溢价，=昨日涨停股今日涨跌幅均值 |
| `zb_rate` | `zb_total / (zt_total + zb_total)` | 炸板率，炸板率高 = 市场分歧大 |

```sql
CREATE TABLE IF NOT EXISTS market_emotion (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date           TEXT UNIQUE,
    zt_total             INTEGER,
    dt_total             INTEGER,
    zb_total             INTEGER,
    max_lianzban         INTEGER,
    zt_yesterday_premium REAL,    -- 昨日涨停今日溢价均值（%）
    zb_rate              REAL     -- 炸板率
);
```

---

#### 任务 2：板块涨停密度时序 → `sector_zt_density` 表

从 `涨停相关因子.parquet` + `申万行业.parquet` 联表计算：

| 指标 | 计算方法 |
|------|---------|
| `zt_density` | 当日该申万一级行业内涨停股数 / 行业总股数 |
| `zt_count` | 当日该行业涨停绝对数量 |
| `max_lianzban` | 该行业内最高连板数 |

这是**最重要的共振检测指标**。`zt_density >= 0.10`（10%）是主线确认的量化阈值。

```sql
CREATE TABLE IF NOT EXISTS sector_zt_density (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date   TEXT,
    industry     TEXT,    -- 申万一级行业名称
    zt_count     INTEGER,
    zt_density   REAL,    -- 涨停密度，0~1
    max_lianzban INTEGER,
    UNIQUE(trade_date, industry)
);
```

---

#### 任务 3：板块资金流加速度 → 写入 `sector_flow` 表的衍生字段

从 `资金流相关因子.parquet` 按申万行业聚合：

| 指标 | 计算方法 |
|------|---------|
| `inst_inflow_3d_vs_20d` | 行业内机构净买入占比 3日均值 / 20日均值 | 资金加速度，>1.5 代表加速流入 |

这个字段追加到 `sector_flow` 表。

---

#### 任务 4：成交额异动检测 → `volume_breakout` 表

从 `成交额相关因子.parquet` 计算，找出成交额突变的股票：

```
筛选条件：amount_mean_5 / amount_mean_20 > 2.0
（近5日平均成交额 > 近20日均值的2倍 = "立桩量"信号）
```

**此指标是主力建仓开始的最强量价信号**，只保存满足条件的股票。

```sql
CREATE TABLE IF NOT EXISTS volume_breakout (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date   TEXT,
    stock_code   TEXT,
    stock_name   TEXT,
    industry     TEXT,
    ratio_5_20   REAL,    -- amount_mean_5 / amount_mean_20
    amount_5d    REAL,    -- 近5日日均成交额（万元）
    UNIQUE(trade_date, stock_code)
);
```

---

#### 任务 5：筹码状态快照 → `chip_status` 表

从 `stock-chip-distribution/` 读取最新日期数据，针对**当日涨停池股票**计算：

| 指标 | 计算方法 | 说明 |
|------|---------|------|
| `cost_50` | 中位成本（50分位） | 当前价对应的成本支撑 |
| `win_rate` | `胜率` 字段 | 持仓盈利比例 |
| `overhead_ratio` | 价格高于 `95分位成本` 的比例 | 套牢盘压力：接近0= 无压力 |
| `lock_ratio` | `加权平均成本` 附近筹码集中程度 | 近似代理，集中=锁仓 |

只计算涨停池 + volume_breakout 名单中的股票，不全量计算。

```sql
CREATE TABLE IF NOT EXISTS chip_status (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date     TEXT,
    stock_code     TEXT,
    cost_50        REAL,
    win_rate       REAL,
    overhead_ratio REAL,
    UNIQUE(trade_date, stock_code)
);
```

---

#### 任务 6：连板链条快照 → `lianzban_chain` 表

从 `涨停相关因子.parquet` 计算所有**连板次数 >= 2** 的股票：

```sql
CREATE TABLE IF NOT EXISTS lianzban_chain (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date   TEXT,
    stock_code   TEXT,
    stock_name   TEXT,
    industry     TEXT,
    lianzban_cnt INTEGER,   -- 连板次数
    is_zb        BOOLEAN,   -- 当日是否炸板
    UNIQUE(trade_date, stock_code)
);
```

---

#### 任务 7：机构调研热度 → 追加到 `research_activity` 表

从 `stock-activation-records/` 统计近 5 个交易日内同一股票被调研的机构数量：

```python
# 近5日内调研机构数 >= 3 = 活跃调研信号
```

```sql
CREATE TABLE IF NOT EXISTS research_activity (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date       TEXT,
    stock_code       TEXT,
    stock_name       TEXT,
    org_count_5d     INTEGER,   -- 近5日调研机构数
    last_visit_date  TEXT,
    UNIQUE(trade_date, stock_code)
);
```

---

### 2.3 `quant/loader.py` 工具函数

提供给其他模块调用的标准化读取函数：

```python
def get_trading_data(code: str) -> pd.DataFrame:
    """读取单股日K数据，返回标准化列名 DataFrame"""

def get_factor_slice(factor_name: str, trade_date: str) -> pd.DataFrame:
    """从 parquet 读取指定日期的全市场截面数据"""

def get_latest_trade_date() -> str:
    """从本地数据获取最新交易日期"""

def get_sector_stocks(industry: str) -> list[str]:
    """获取申万一级行业下的所有股票代码"""
```

---

## 模块三：SQLite 完整表结构

```sql
-- 财联社电报 + 多源快讯
CREATE TABLE IF NOT EXISTS cls_news (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT,
    content    TEXT,
    pub_time   TEXT,
    source     TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(pub_time, title)
);

-- 政策原文
CREATE TABLE IF NOT EXISTS policy_news (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    link       TEXT,
    pub_time   TEXT,
    source     TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(title, pub_time)   -- link 为空时用 title+time 去重
);

-- 板块资金流（行业 + 概念，source_type 区分）
CREATE TABLE IF NOT EXISTS sector_flow (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    fetch_time       TEXT,
    sector_name      TEXT,
    change_pct       REAL,
    main_inflow      REAL,
    main_inflow_pct  REAL,
    source_type      TEXT     -- 'industry' 或 'concept'
);

-- 龙虎榜
CREATE TABLE IF NOT EXISTS lhb_data (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date  TEXT,
    stock_code  TEXT,
    stock_name  TEXT,
    reason      TEXT,
    net_buy     REAL,
    UNIQUE(trade_date, stock_code, reason)
);

-- 涨停池
CREATE TABLE IF NOT EXISTS zt_pool (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date    TEXT,
    stock_code    TEXT,
    stock_name    TEXT,
    zt_count      INTEGER,
    first_zt_time TEXT,
    sector        TEXT,
    UNIQUE(trade_date, stock_code)
);

-- 跌停池
CREATE TABLE IF NOT EXISTS dt_pool (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date    TEXT,
    stock_code    TEXT,
    stock_name    TEXT,
    first_dt_time TEXT,
    sector        TEXT,
    UNIQUE(trade_date, stock_code)
);

-- 研究报告
CREATE TABLE IF NOT EXISTS research_report (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    pub_date      TEXT,
    stock_code    TEXT,
    stock_name    TEXT,
    institution   TEXT,
    analyst       TEXT,
    rating        TEXT,
    rating_change TEXT,
    target_price  REAL,
    title         TEXT,
    pdf_url       TEXT,
    fetch_time    TEXT,
    UNIQUE(pub_date, stock_code, institution, title)
);

-- 盘中市场脉搏（实时聚合，每 10s 一条）
CREATE TABLE IF NOT EXISTS market_pulse (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fetch_time  TEXT,
    zt_count    INTEGER,
    dt_count    INTEGER,
    zb_count    INTEGER,
    zt_dt_ratio REAL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 市场情绪日度（每日一条）
CREATE TABLE IF NOT EXISTS market_emotion (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date           TEXT UNIQUE,
    zt_total             INTEGER,
    dt_total             INTEGER,
    zb_total             INTEGER,
    max_lianzban         INTEGER,
    zt_yesterday_premium REAL,
    zb_rate              REAL
);

-- 板块涨停密度时序
CREATE TABLE IF NOT EXISTS sector_zt_density (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date   TEXT,
    industry     TEXT,
    zt_count     INTEGER,
    zt_density   REAL,
    max_lianzban INTEGER,
    UNIQUE(trade_date, industry)
);

-- 成交额异动（立桩量信号）
CREATE TABLE IF NOT EXISTS volume_breakout (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT,
    stock_code TEXT,
    stock_name TEXT,
    industry   TEXT,
    ratio_5_20 REAL,
    amount_5d  REAL,
    UNIQUE(trade_date, stock_code)
);

-- 筹码状态快照
CREATE TABLE IF NOT EXISTS chip_status (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date     TEXT,
    stock_code     TEXT,
    cost_50        REAL,
    win_rate       REAL,
    overhead_ratio REAL,
    UNIQUE(trade_date, stock_code)
);

-- 连板链条快照
CREATE TABLE IF NOT EXISTS lianzban_chain (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date   TEXT,
    stock_code   TEXT,
    stock_name   TEXT,
    industry     TEXT,
    lianzban_cnt INTEGER,
    is_zb        BOOLEAN,
    UNIQUE(trade_date, stock_code)
);

-- 机构调研活跃度
CREATE TABLE IF NOT EXISTS research_activity (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date      TEXT,
    stock_code      TEXT,
    stock_name      TEXT,
    org_count_5d    INTEGER,
    last_visit_date TEXT,
    UNIQUE(trade_date, stock_code)
);

-- Agent 综合判断
CREATE TABLE IF NOT EXISTS agent_summary (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    summary_time       TEXT,
    run_type           TEXT,    -- 'morning' / 'midday' / 'closing' / 'manual'
    content            TEXT,
    data_snapshot_json TEXT,
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 模块四：Agent 体系设计

Agent 层的定位：**LLM 是叙事合成器和跨域连接器，不是计算器，不是数据提取器**。

所有数字计算在 Python 代码里完成，传给 LLM 的是已经计算好的结果。LLM 的价值在于：
- 连接政策新闻 → 受益板块的叙事逻辑
- 识别跨行业概念共振（超越申万行业静态分类）
- 从多源信号合成简明判断

### 4.1 运行时机（事件驱动 + 定时触发）

| 触发条件 | 运行的 Agent | 输出 |
|---------|------------|------|
| 每日 09:00（盘前） | 全链路（分类器→板块→风控→个股→简报） | 晨间简报 |
| 每日 14:30（午后） | 板块 + 个股 Agent | 午后更新 |
| 每日 17:30（盘后）龙虎榜到位后 | 全链路 | 收盘复盘 |
| 实时触发：涨停密度 15min 变化 > 5% | 板块 Agent | 实时预警 |
| 手动触发（UI 按钮） | 全链路 | 即时报告 |

### 4.2 Agent 1：新闻分类器（`agent/classifier.py`）

**目的**：过滤掉无关噪音，减少下游 Agent 的 token 消耗。

**模型**：本地 Qwen 7B（或调用小模型 API），高频任务，不需要强推理。

**输入**：原始新闻标题列表（过去 2 小时内，`cls_news` + `policy_news`）

**输出 JSON**：
```json
{
  "category": "政策|产业|个股|海外|市场情绪|无关",
  "urgency": 1,           // 1=低 2=中 3=高
  "tickers": ["600519"],  // 提及的股票代码（可空）
  "sector_tags": ["白酒", "消费"],
  "summary": "15字以内摘要"
}
```

**过滤规则**（硬编码，不走 LLM）：
- `urgency=1` 且 `category=无关` → 丢弃
- `urgency=1` 且 `category=市场情绪` → 丢弃
- 每小时传递给下游的条目上限：20 条

### 4.3 Agent 2：板块叙事 Agent（`agent/sector_agent.py`）

**目的**：结合数据和新闻，判断哪些板块有主线共振，以及所处的生命周期阶段。

**模型**：Claude Haiku 或 DeepSeek V3（需要推理，但结构化输出）

**输入（结构化，非自然语言）**：
```
今日涨停密度 Top5 板块（industry, zt_density, zt_count, max_lianzban）
近3日板块资金流累计 Top5
过滤后的政策/产业新闻（来自分类器，urgency>=2）
```

**Prompt 核心指令**：
- 识别哪些板块有政策新闻 + 资金流 + 涨停密度的三重共振
- 判断每个活跃板块的阶段（萌芽/爆发/退潮）
- 每个判断必须引用具体数据字段和数值，不允许说"较多""较强"等模糊表述
- 如果数据不支持判断，输出 `"stage": "data_insufficient"`

**输出 JSON**：
```json
{
  "top_sectors": [
    {
      "name": "低空经济",
      "stage": "爆发",
      "evidence": "涨停密度15.3%，资金净流入+8.2亿/3日，政策新闻3条（含发改委正式文件）",
      "risk": "已持续3天，关注退潮信号"
    }
  ],
  "market_breadth": "强|中|弱",
  "theme_coherence": "集中|分散",
  "warning": ""
}
```

### 4.4 Agent 3：个股候选 Agent（`agent/stock_agent.py`）

**目的**：从今日涨停池 + 量价异动名单里，找出最符合龙头特征的股票。

**模型**：Claude Sonnet（最关键的判断任务，需要区分龙头/跟风）

**输入（必须是结构化列表，不能是空泛描述）**：
```
今日涨停池（最多50条）：代码, 名称, 板块, 首封时间, 连板数, 是否炸板
今日成交额异动名单（volume_breakout）
活跃板块（来自板块 Agent 输出的 top_sectors）
龙虎榜数据（如已出）
```

**Prompt 核心指令（强制引用数据的链式推理）**：

要求 Agent 对每只候选股，依次回答以下问题后再给结论：
1. 首板还是几板？（cite 连板次数字段）
2. 封板时间是否在 10:30 前？（cite 首封时间字段）
3. 是否炸板？（cite 是否炸板字段）
4. 所属板块是否在活跃板块名单里？（cite 板块名称）
5. 龙虎榜是否有机构席位净买入？（盘后才可用）

如果任何一个问题无法引用具体数据，该股票的对应维度标注 `null`，不得编造。

**输出 JSON**：
```json
{
  "candidates": [
    {
      "ticker": "300XXX",
      "name": "XX科技",
      "direction": "短线|波段",
      "evidence": "首板+封板09:47+板块低空经济爆发期+机构净买入2.1亿",
      "entry_note": "次日竞价溢价>2%且缩量可考虑",
      "risk_note": "板块第3天注意退潮",
      "confidence": "高|中|低",
      "data_gaps": ["龙虎榜未出"]
    }
  ]
}
```

**硬性规则**：
- 候选数量上限：5 只
- 低置信度的候选在 UI 中灰显，不隐藏
- 禁止从训练记忆中生成股票名（必须从输入列表中选取）

### 4.5 Agent 4：风控时机 Agent（`agent/risk_agent.py`）

**目的**：判断当前市场是否适合操作，识别政策周期阶段。这是所有其他 Agent 的门控层。

**运行逻辑**：风控 Agent 先于其他 Agent 运行。如果输出 `market_mode = "不操作"`，个股候选 Agent 不运行。

**判断规则（优先用代码实现，LLM 只写注解）**：

```python
# 硬规则（Python 计算，不走 LLM）
if zt_total < 20:          → market_mode = "谨慎"
if dt_total > zt_total * 0.5: → market_mode = "谨慎"
if zt_total < 10:          → market_mode = "不操作"
if 今日大盘跌幅 > 2%:      → market_mode = "不操作"
if 连续2日板块资金净流出:   → 对应板块标记 "退潮风险"
```

**LLM 的补充任务**：识别政策周期阶段

- 输入：过滤后的政策新闻（近 3 天），关键词：政策类别标签
- 输出：`policy_phase = "吹风|征求意见|正式落地|执行细则|无明显政策"`
- 吹风期 = 建仓窗口，正式落地 = 止盈窗口

**输出 JSON**：
```json
{
  "market_mode": "正常|谨慎|不操作",
  "policy_phase": "吹风|正式落地|无明显政策",
  "active_sector_status": {
    "低空经济": "健康|退潮风险|已退潮"
  },
  "reason": "涨停42只，炸板率12%，市场健康"
}
```

### 4.6 防幻觉机制（所有 Agent 通用）

在每个 Agent 的 system prompt 中必须包含以下规则：

```
规则1：每个判断必须引用输入数据中的具体字段名和数值。
       禁止使用"较多"、"较强"、"明显"等无法量化的表述。

规则2：如果某个维度没有对应数据，输出该字段为 null，
       不得根据通用知识推断。标注 "data_gap": ["缺失字段名"]。

规则3：禁止从训练知识中生成股票代码或公司名称。
       所有股票必须来自输入列表。

规则4：如果提供的数据不足以支持任何判断，
       输出 {"error": "INSUFFICIENT_DATA", "reason": "..."}
```

### 4.7 成本估算

| Agent | 模型 | 每次调用 token | 每日调用次数 | 日成本 |
|-------|------|-------------|------------|--------|
| 新闻分类器 | Qwen 7B 本地 | 200/条 × 50条 | 6次 | $0 |
| 板块叙事 | Claude Haiku | ~2000 | 5次 | ~$0.02 |
| 个股候选 | Claude Sonnet | ~3000 | 3次 | ~$0.15 |
| 风控时机 | Claude Haiku | ~1000 | 5次 | ~$0.01 |
| **合计** | | | | **~$0.20/天** |

---

## 模块五：调度器（`scheduler.py`）

```python
scheduler.add_job(fetch_cls,          "interval", minutes=5)
scheduler.add_job(fetch_global_news,  "interval", minutes=5)
scheduler.add_job(fetch_policy,       "interval", minutes=30)
scheduler.add_job(fetch_research,     "interval", minutes=30)
scheduler.add_job(_guarded_sector_flow,   "interval", minutes=15)
scheduler.add_job(_guarded_zt_pool,       "interval", minutes=5)
scheduler.add_job(_guarded_dt_pool,       "interval", minutes=5)
scheduler.add_job(_guarded_concept_heat,  "interval", minutes=5)
scheduler.add_job(_guarded_realtime_quote,"interval", seconds=30)  # 盘中实时快照
scheduler.add_job(fetch_lhb,          "cron", hour=17, minute=30)
scheduler.add_job(run_daily_compute,  "cron", hour=9,  minute=0)   # 量价批量计算
scheduler.add_job(run_morning_agent,  "cron", hour=9,  minute=15)  # 晨间 Agent
scheduler.add_job(run_closing_agent,  "cron", hour=17, minute=45)  # 收盘 Agent
scheduler.add_job(cleanup_old_data,   "cron", hour=2,  minute=0)
```

`_guarded_*` 函数只在 `9:15 <= now <= 15:05` 时执行。

---

## 开发注意事项

### AKShare 使用
- 版本：`akshare>=1.16.14`
- 限流：相邻接口调用之间加 `time.sleep(0.5)`
- `stock_zh_a_spot_em()` 全市场快照：轮询间隔 >= 10 秒，单次返回 ~5000 行
- `stock_fund_flow_individual("即时")`：9:30-9:40 窗口返回列数不稳定，需校验列数后再处理
- 非交易日：涨停池/龙虎榜接口抛异常，`try/except` 静默跳过

### 本地数据读取
```python
# 所有 CSV 文件：GBK 编码，第 0 行是版权注释，需 skiprows=1
df = pd.read_csv(path, encoding="gbk", skiprows=1)

# Parquet 文件：直接读取，trade_date 是字符串格式 "YYYY-MM-DD"
df = pd.read_parquet(path)
df = df[df["trade_date"] == "2026-05-15"]
```

### 数据老化清理
- `cls_news`：保留最近 7 天
- `sector_flow`、`market_pulse`：保留最近 30 天
- `market_emotion`、`sector_zt_density`、`volume_breakout`、`chip_status`、`lianzban_chain`：保留最近 90 天（时序数据，用于图表）
- `agent_summary`：保留最近 60 天

### Scheduler 与 Streamlit 共存
```python
if "scheduler_started" not in st.session_state:
    from scheduler import start_scheduler
    start_scheduler()
    st.session_state["scheduler_started"] = True
```

---

## 扩展开发路线

### 已完成（V1）
- 财联社/东财/同花顺/华尔街见闻快讯
- 政策原文 RSS（gov.cn/发改委/证监会/交易所）
- 板块资金流（行业+概念）
- 涨停池/跌停池
- 龙虎榜

### 当前阶段（V2）
- 研究报告抓取（东财 reportapi）
- 实时行情快照聚合（market_pulse）
- 量价日度批量计算（7项指标）
- 新 SQLite 表建立（10张新表）
- 调度器更新

### 下一阶段（V3）
- Agent 层实现（分类器→板块→风控→个股→简报）
- UI 升级：市场情绪仪表盘 + 板块涨停密度热力图 + 个股上下文召回

### 远期（V4）
- 龙虎榜席位识别（建立游资席位白名单）
- 动态概念标签（突破申万行业静态分类，识别跨行业主题）
- 本地 Qwen 7B 部署，新闻分类器零 API 成本

---

## 启动方式

```bash
cd /mnt/ssd_1T/runist/code/Q/market-radar
pip install -r requirements.txt
streamlit run app.py
# 访问 http://localhost:8501
```

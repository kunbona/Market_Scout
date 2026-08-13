---
name: mra-dm-market-analysis
description: |
  DM-kun 市场分析 skill 移植版 (从 dark-magician kernel 剥离).
  11 个核心分析脚本 + 3 个智堡 API 工具, 全部从 quant.dm_kun 包调用.
  触发词: 市场分析、市场状态、regime、风格判断、行业轮动、拥挤度、情绪周期、炸板率、筹码、选股、竞价、智堡.
allowed-tools:
  - Bash
  - Read
---

# mra-dm-market-analysis - DM-kun 市场分析工具集

## 是什么

从 `/Users/kun/Documents/Trade/DM-kun/.claude/skills/市场分析/` 整体迁移, 剔除:
- ❌ 因子相关 (factor_icir_*.py / 28 因子库 / select-stock-pro kernel)
- ❌ 偏科生选股策略 (`关联_*.py` / `中等生_*.py` / `双极回撤归因.py` 等)

保留 11 个核心分析脚本 + 3 个智堡 API 工具, 全部从 `quant.dm_kun` 包 import 或 `python -m quant.dm_kun.<脚本>` 跑.

## 数据路径

所有脚本从 `QUANT_DATA_ROOT` 环境变量读数据 (market-radar plist 注入, `.env.local` 配置). **env 缺失会 raise** — 走 `quant.dm_kun._paths.require_quant_data_root()`, 不再回落硬编码路径.

需要的数据子目录:
- `stock-trading-data-pro` — 5200+ A股日线 CSV (主要数据源)
- `stock-main-index-data` — 12 个指数 (上证/沪深300/中证500/科创50/创业板等)
- `stock-fin-data-xbx` — 财务数据
- `stock-popular-concept-detail` — 概念/题材分类
- `stock-analyst-ranking` — 分析师排名
- `stock-chip-distribution` — 筹码分布

## 11 个核心脚本 (CLI 入口)

```bash
# 全部用 python -m 跑, 必须从 market-radar/ 根目录
cd /Users/kun/Documents/market-radar && source .venv/bin/activate

# 1. 市场状态识别 (趋势/波动/风格/宽度 4 维, 输出"低波上涨"/"高波下跌"/"震荡"等 regime)
python -m quant.dm_kun.market_regime_analyzer
python -m quant.dm_kun.market_regime_analyzer 20260810  # 指定日期

# 2. 情绪周期 (涨停生态/连板天梯/炸板率/涨跌停比, 5879 只并行)
python -m quant.dm_kun.sentiment_cycle_analyzer
python -m quant.dm_kun.sentiment_cycle_analyzer 20260810

# 3. 行业拥挤度 (成交额占比 MA5 × 近一年分位, 5477 只 5.4 秒)
python -m quant.dm_kun.industry_crowding_analyzer
python -m quant.dm_kun.industry_crowding_analyzer 20260810

# 4. 行业增强分析
python -m quant.dm_kun.industry_enhanced_analyzer

# 5. 主题阶梯 (题材热度的梯次结构)
python -m quant.dm_kun.theme_ladder_analyzer

# 6. 趋势分析 (TrendAnalyzer class, 单只/批量)
python -m quant.dm_kun.trend_analyzer
python -c "from quant.dm_kun.trend_analyzer import TrendAnalyzer; ..."

# 7. 板块 raw 分析 (依赖 trend_analyzer)
python -m quant.dm_kun.analyze_sector_from_raw

# 8. 筹码结构
python -m quant.dm_kun.chip_structure_analyzer

# 9. 选股推荐
python -m quant.dm_kun.stock_recommender

# 10. 竞价分析 (集合竞价)
python -m quant.dm_kun.auction_analyzer

# 11. 炸板率历史
python -m quant.dm_kun.bomb_rate_history
```

## 3 个智堡 API 工具 (需要 API key)

```bash
# 智堡 API 客户端 (覆盖 9 个资源端点)
python -m quant.dm_kun.wisburg_api list feed --first 15
python -m quant.dm_kun.wisburg_api list articles --first 10
python -m quant.dm_kun.wisburg_api list market-daily --first 5
python -m quant.dm_kun.wisburg_api detail reports <id>

# 智堡简报 (一站式: 20 条资讯流 + Kimi 自动分类摘要)
python -m quant.dm_kun.wisburg_feed_briefing --first 20 --output md

# 智堡推荐
python -m quant.dm_kun.wisburg_recommend
```

注意: 智堡需要 `WISBURG_API_KEY` 环境变量, 走 `tools/_wisburg.py` 里读.

## 性能特征 (实测 2026-08-10 数据)

| 脚本 | 数据量 | 耗时 | 进程 |
|---|---|---|---|
| market_regime_analyzer | 12 指数 + 2839 股票 | ~10s | 12 worker |
| sentiment_cycle_analyzer | 5879 股票 × N日 | ~60s | ProcessPool (multiprocessing) |
| industry_crowding_analyzer | 5477 股票 | 5.4s | 12 worker |
| industry_enhanced_analyzer | 5477 股票 | ~30s | ProcessPool |
| theme_ladder_analyzer | 31 行业 | ~10s | ProcessPool |

⚠️ **多进程脚本 (sentiment_cycle / industry_enhanced / theme_ladder) 会 fork 大量 Python 子进程**, 单次占 RSS 2-4GB. 跑前确认内存够.

## 数据缓存约定

- 输出 markdown 报告默认存到 `quant/dm_kun/../kun/data/` (脚本里 `__file__` 定位)
- industry_crowding 输出: `quant/kun/data/industry_crowding_YYYYMMDD.md`
- 报告归档: 后续可加 `docs/市场分析/` 目录
- 暂无数据库落库 (脚本纯 CLI 输出). 如需给前端用, 走 server.py endpoint (见下).

## server.py 暴露的 endpoint

| 路由 | 函数 | 用途 |
|---|---|---|
| `GET /api/dm-kun/market-regime` | `api_dm_kun_market_regime()` | 市场状态 (regime + 风格 + 宽度) |
| `GET /api/dm-kun/sentiment-cycle` | `api_dm_kun_sentiment_cycle()` | 情绪周期 (涨停/连板/炸板) |
| `GET /api/dm-kun/industry-crowding` | `api_dm_kun_industry_crowding()` | 行业拥挤度 (含完整 markdown) |

(具体 endpoint 等 server.py 集成后填, 预计近期加)

## 跟 mra-* 系列 skill 关系

mra-emotion / mra-sector / mra-scout / mra-news / mra-policy / mra-notice / mra-research / mra-risk 这些 LLM 分析 skill 是**解读**角色, 调用这个 skill 是**数据计算**角色.

典型流程:
1. `mra-dm-market-analysis.market_regime_analyzer` 算出来 "高波上涨 + 中证2000 占优"
2. `mra-sector` 用这个 regime 解读"小盘股 + 动量策略 推荐"
3. `mra-strategist` 综合 regime + 4 路情报 + 9 维度复盘, 出 1-4 周战略推理

## 日期约定 (跟 DM-kun 一致)

**所有日期用「收盘数据日期」** (最近一个交易日, 通常是昨天/上周五), 不用「分析执行日期」.

- 脚本输出第一行 `[自动检测] 最新交易日: YYYY-MM-DD` 即为收盘数据日期
- CLI 参数: `python -m quant.dm_kun.<脚本> YYYYMMDD` (8 位无横线)
- 报告文件名统一用该日期

## ⚠️ 已知限制

- 不依赖 kernel, 不引入因子库, 不做 ICIR
- 智堡 API key 必须从 env 读 (不写死)
- ProcessPool 脚本在 macOS 上要 `if __name__ == '__main__':` 保护, 当前 11 个脚本都符合
- 11 个脚本是**独立 CLI**, 不共享 db/缓存 — 每次跑重新计算, 适合 spot-check
- 没集成到 market-radar 的核心 scheduler (下一步可加 cron)

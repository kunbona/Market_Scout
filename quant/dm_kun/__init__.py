"""
quant.dm_kun - DM-kun 市场分析 skill 移植包

从 /Users/kun/Documents/Trade/DM-kun/.claude/skills/市场分析/ 整体迁移,
剔除因子相关 (factor_icir_*) 和 wisburg 外部 API, 保留 11 个核心分析脚本.

每个脚本是独立 CLI 入口, 顶层有 main(). 通过 `python -m quant.dm_kun.<脚本>` 跑.

数据路径: 读 QUANT_DATA_ROOT 环境变量 (从 plist 注入, `.env.local` 配置).
通过 `quant.dm_kun._paths.require_quant_data_root()` 统一获取, env 缺失/路径不存在会 raise.
DATACENTER 子目录: stock-trading-data-pro / stock-fin-data-xbx / stock-main-index-data /
                   stock-popular-concept-detail / stock-analyst-ranking / stock-chip-distribution.

11 个核心脚本:
  - trend_analyzer             趋势分析 (TrendAnalyzer class, 被 analyze_sector_from_raw 引用)
  - market_regime_analyzer     市场状态识别 (趋势/波动/风格/宽度四维)
  - sentiment_cycle_analyzer   情绪周期
  - industry_crowding_analyzer  行业拥挤度 (成交额占比分位)
  - industry_enhanced_analyzer 行业增强分析
  - theme_ladder_analyzer      主题阶梯
  - chip_structure_analyzer    筹码结构
  - stock_recommender          选股推荐
  - auction_analyzer           竞价分析
  - bomb_rate_history          炸板率历史
  - analyze_sector_from_raw    板块 raw (依赖 trend_analyzer)

3 个 wisburg 工具 (需要 API key, 默认不跑):
  - wisburg_api                智堡 API 客户端
  - wisburg_feed_briefing      智堡简报
  - wisburg_recommend          智堡推荐

设计原则 (跟 DM-kun 保持一致):
  - 不依赖 select-stock-pro kernel
  - 不引入因子库
  - 数据路径走环境变量
  - 每个脚本可独立 import / 跑 CLI
"""

__all__ = [
    "trend_analyzer",
    "market_regime_analyzer",
    "sentiment_cycle_analyzer",
    "industry_crowding_analyzer",
    "industry_enhanced_analyzer",
    "theme_ladder_analyzer",
    "chip_structure_analyzer",
    "stock_recommender",
    "auction_analyzer",
    "bomb_rate_history",
    "analyze_sector_from_raw",
    # wisburg
    "wisburg_api",
    "wisburg_feed_briefing",
    "wisburg_recommend",
    # helpers
    "_wisburg",
    "_kimi_chat",
]

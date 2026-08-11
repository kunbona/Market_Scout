"""
QMT Bridge 方法白名单。

bridge 通过 /qmt/call 端点把请求转发到 xtquant.xtdata 模块上对应的方法。
为避免 Mac 端误调危险方法（私有 API、状态污染等），只暴露以下白名单内的方法。

新增方法：在下方 ALLOWED_METHODS 集合里加一行即可，无需改动 server.py。
删方法：从集合里移除。

约定：
- 方法名跟 xtquant.xtdata 上的方法名一致（精确匹配）
- 静态方法（不需要先 connect）放前面
- 涉及订阅/状态的方法（subscribe_quote 等）单独放一节
"""
from __future__ import annotations

# 全部允许通过 /qmt/call 代理调用的方法。Mac 端能调啥完全由这个集合决定。
ALLOWED_METHODS: set[str] = {
    # ─── 交易日历 ──────────────────────────────────────────
    "get_trading_dates",          # 区间交易日列表
    "get_trade_cal",              # 交易日历（备选）

    # ─── 股票清单 / 板块 ───────────────────────────────────
    "get_stock_list_in_sector",   # 取板块成分股
    "get_sector_list",            # 板块清单
    "get_industry",               # 行业分类（xtquant 新增）

    # ─── 实时行情快照 ──────────────────────────────────────
    "get_full_tick",              # 全字段 tick
    "get_latest_quote",           # 轻量最新价
    "get_market_data_ex",         # 增强版快照

    # ─── 历史 K 线 ─────────────────────────────────────────
    "get_market_data",            # 主力 K 线接口（需要先 download_history_data 同步本地缓存）
    "download_history_data",      # 同步下载 K 线到本地缓存（首次慢, 后续秒返）

    # ─── 财务 / 基础信息 ───────────────────────────────────
    "get_instrument_detail",      # 单只证券基础信息
    "get_financial_data",         # 财务数据

    # ─── 订阅类（持续推送，无返回值；需要后续 WS 化） ─────
    # 当前 bridge 同步返回空 dict，订阅在 bridge 端维护状态。
    # 想真用推送请先评估再开。
    # "subscribe_quote",
    # "unsubscribe_quote",
    # "subscribe_whole_quote",
}


def is_allowed(method: str) -> bool:
    return method in ALLOWED_METHODS

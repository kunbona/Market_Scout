"""
QMT Bridge 方法访问控制。

个人电脑（Mac/VM），没必要做严格的正向白名单挡自己。
设计：
- xtdata 行情类：全放行（任何 xtquant.xtdata 方法都能调，方便探索新接口）
- xttrader 交易类：黑名单拦截（防误调下单/撤单/连接交易通道）

为什么不完全开放：
- xttrader 是 xtquant 的**交易**模块，调 `order_stock` 直接真金白银下单
- 哪怕自己电脑，被脚本/AI 误调一次也是事故
- 行情怎么调都没损失，但交易方向的方法必须挡

新增/删方法：
- 行情：直接用，无需登记
- 交易黑名单：往 TRADING_BLACKLIST 加即可
"""
from __future__ import annotations

# 交易类方法黑名单（xttrader 模块 + 任何带 order/connect/智能单关键词的方法）
# 任何想通过 /qmt/call 调这些方法的请求都会被 server 拒绝 (HTTP 403)
TRADING_BLACKLIST: set[str] = {
    # ─── 下单 / 撤单（最危险）───
    "order_stock",                # 同步下单
    "cancel_order_stock",         # 同步撤单
    "order",                      # 异步下单 (新接口)
    "cancel_order",               # 异步撤单 (新接口)
    "smart_algo_order",           # 智能算法单
    "cancel_smart_algo_order",    # 撤算法单

    # ─── 交易通道连接 / 账户设置（产生真实交易副作用）───
    "xt_trader_connect",          # 连接交易通道
    "xt_trader_disconnect",       # 断开交易通道
    "set_account",                # 切换账户（券商账号）

    # ─── 委托 / 成交 推送订阅（持续状态, 推太多会卡桥）───
    "subscribe_orderbook",        # 订阅委托推送
    "unsubscribe_orderbook",
    "subscribe_position",         # 订阅持仓推送
    "unsubscribe_position",
}


def is_allowed(method: str) -> bool:
    """
    是否允许通过 /qmt/call 调该方法。

    规则：除 TRADING_BLACKLIST 之外的方法一律放行。
    """
    return method not in TRADING_BLACKLIST

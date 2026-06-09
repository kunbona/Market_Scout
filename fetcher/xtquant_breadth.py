"""
通过 xtquant (miniQMT) 获取全市场涨跌家数和全市成交额。

优势：
- up/down/flat 来自逐票实时快照，覆盖全部 A 股，精度远高于指数 K 线代理值
- total_amount 为 SH+SZ 全市合计成交额

前提：
- miniQMT 客户端已在本机启动并登录
- 已安装 xtquant：pip install xtquant

不满足以上条件时，所有函数静默返回 False/None，由调用方降级处理。
"""
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

# miniQMT 安装根目录，从环境变量读取，运行时可由 server.py 动态更新
_QMT_PATH = os.environ.get("QMT_PATH", "")

# 全市 A 股板块名称（xtdata 内置板块）
_SECTOR_A = "沪深A股"


def _is_enabled() -> bool:
    return os.environ.get("QMT_ENABLED", "false").lower() in ("true", "1", "yes")


def connect() -> bool:
    """
    尝试连接本地 miniQMT，返回是否成功。
    仅在 QMT_ENABLED=true 时尝试，否则直接返回 False。
    """
    if not _is_enabled():
        return False
    try:
        from xtquant import xtdata
        xtdata.connect()
        return True
    except ImportError:
        logger.debug("[xtquant] xtquant 未安装，跳过 QMT 数据源")
        return False
    except Exception as e:
        logger.debug("[xtquant] 连接失败: %s", e)
        return False


def get_version() -> str | None:
    """返回 xtquant 版本字符串，未安装时返回 None。"""
    try:
        import xtquant
        return getattr(xtquant, "__version__", "已安装")
    except ImportError:
        return None


def fetch() -> bool:
    """
    从 miniQMT 拉取全市行情快照，聚合涨跌家数和全市成交额，写入 market_breadth 表。

    写入两条记录：
      - market='SH'   source='xtquant'  含各市场分拆数据
      - market='TOTAL' source='xtquant' 含全市合计 total_amount

    返回 True 表示写入成功，False 表示不可用（调用方应降级）。
    """
    if not _is_enabled():
        return False

    try:
        from xtquant import xtdata
    except ImportError:
        logger.debug("[xtquant] xtquant 未安装")
        return False

    try:
        xtdata.connect()
    except Exception as e:
        logger.debug("[xtquant] 连接失败: %s", e)
        return False

    try:
        stock_list = xtdata.get_stock_list_in_sector(_SECTOR_A)
        if not stock_list:
            logger.warning("[xtquant] 获取 A 股列表为空，跳过")
            return False
    except Exception as e:
        logger.warning("[xtquant] 获取股票列表失败: %s", e)
        return False

    try:
        ticks = xtdata.get_full_tick(stock_list)
    except Exception as e:
        logger.warning("[xtquant] get_full_tick 失败: %s", e)
        return False

    if not ticks:
        logger.warning("[xtquant] get_full_tick 返回空数据")
        return False

    # 聚合统计
    sh_up = sh_down = sh_flat = 0
    sz_up = sz_down = sz_flat = 0
    sh_amount = sz_amount = 0.0

    for code, tick in ticks.items():
        if not isinstance(tick, dict):
            continue
        last  = tick.get("lastPrice") or tick.get("last_price")
        close = tick.get("lastClose") or tick.get("last_close")
        amount = tick.get("amount") or 0.0

        is_sh = code.endswith(".SH")
        is_sz = code.endswith(".SZ")

        if is_sh:
            sh_amount += amount
        elif is_sz:
            sz_amount += amount

        if last is None or close is None or close == 0:
            continue

        if last > close:
            if is_sh:
                sh_up += 1
            elif is_sz:
                sz_up += 1
        elif last < close:
            if is_sh:
                sh_down += 1
            elif is_sz:
                sz_down += 1
        else:
            if is_sh:
                sh_flat += 1
            elif is_sz:
                sz_flat += 1

    total_up    = sh_up    + sz_up
    total_down  = sh_down  + sz_down
    total_flat  = sh_flat  + sz_flat
    total_amount = sh_amount + sz_amount

    if total_up + total_down + total_flat == 0:
        logger.warning("[xtquant] 聚合后全部为 0，数据无效，跳过写入")
        return False

    fetch_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    from db.storage import insert_market_breadth

    def _ratio(up, down):
        return round(up / down, 4) if down > 0 else None

    # SH / SZ 分市场行；TOTAL 行只填 total_amount
    for market, up, down, flat, idx_amount, tot_amount in [
        ("SH",    sh_up,    sh_down,    sh_flat,    sh_amount,    None),
        ("SZ",    sz_up,    sz_down,    sz_flat,    sz_amount,    None),
        ("TOTAL", total_up, total_down, total_flat, None,         total_amount),
    ]:
        insert_market_breadth(
            fetch_time=fetch_time,
            source="xtquant",
            market=market,
            up_count=up,
            down_count=down,
            flat_count=flat,
            ad_ratio=_ratio(up, down),
            index_amount=idx_amount,
            index_price=None,
            total_amount=tot_amount,
        )

    logger.info(
        "[xtquant] up=%d down=%d flat=%d total_amount=%.0f 亿",
        total_up, total_down, total_flat, total_amount / 1e8 if total_amount else 0,
    )
    return True

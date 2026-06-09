"""
从本地量化数据读取龙虎榜席位明细。

数据路径：$QUANT_DATA_ROOT/stock-lhb-organ/<YYYY-MM-DD>.csv
字段：交易日期, 股票代码, 营业部名称, 买入金额, 买入占总成交比例,
       卖出金额, 卖出占总成交比例, 净成交额, 买卖类型, 上榜理由, 排名

调度：每天 18:30 运行（收盘后本地数据落库后）
QUANT_DATA_ROOT 未配置或子目录不存在时跳过（不报错）。
"""
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

def _resolve_lhb_dir() -> Path | None:
    """从 QUANT_DATA_ROOT 派生 LHB 数据目录，无需单独配置。"""
    from quant.loader import DATA_ROOT
    if DATA_ROOT and DATA_ROOT.is_dir():
        p = DATA_ROOT / "stock-lhb-organ"
        return p if p.is_dir() else None
    return None

# 已知游资席位关键词（模糊匹配）
_YOUZI_KEYWORDS = [
    "华鑫",
    "方正证券上海", "方正证券北京紫竹院",
    "华泰证券广州天河",
    "国信证券深圳红岭", "国信证券浙江互联网",
    "招商证券深圳",
    "国泰君安上海江苏路",
    "国泰海通证券股份有限公司上海长宁区江苏路",
    "广发证券上海虹桥",
    "东方财富证券股份有限公司拉萨",   # 拉萨东城、拉萨金融城等
    "中信建投北京海淀",
    "开源证券股份有限公司西安太华路",
    "浙商证券股份有限公司乐清",
    "平安证券股份有限公司杭州曙光路",
]

_INST_KEYWORDS = ["机构专用", "公募", "私募", "基金"]


def _classify_seat(seat_name: str) -> str:
    """判断席位类型：游资 / 机构 / 北向 / 其他"""
    if any(k in seat_name for k in _INST_KEYWORDS):
        return "机构"
    if "沪深港通" in seat_name or "北向" in seat_name:
        return "北向"
    if any(k in seat_name for k in _YOUZI_KEYWORDS):
        return "游资"
    return "其他"


def fetch_lhb_local(trade_date: str | None = None) -> None:
    """
    读取指定交易日的本地龙虎榜文件，写入 lhb_seat 表。
    trade_date: YYYY-MM-DD，默认今日。
    """
    lhb_dir = _resolve_lhb_dir()
    if not lhb_dir:
        logger.debug("[lhb_local] QUANT_DATA_ROOT 未配置或 stock-lhb-organ 子目录不存在，跳过")
        return

    from db.storage import insert_lhb_seat, init_db
    init_db()

    date = trade_date or datetime.now().strftime("%Y-%m-%d")
    csv_path = lhb_dir / f"{date}.csv"

    if not csv_path.exists():
        logger.info("[lhb_local] 本地文件不存在: %s", csv_path)
        return

    try:
        import pandas as pd
        # 第一行是注释，跳过；encoding gbk
        df = pd.read_csv(csv_path, encoding="gbk", skiprows=1)
    except Exception as e:
        logger.warning("[lhb_local] 读取文件失败 %s: %s", csv_path, e)
        return

    if df.empty:
        return

    # 列名映射（容错）
    col_map = {
        "交易日期": "trade_date",
        "股票代码": "stock_code",
        "营业部名称": "seat_name",
        "买入金额": "buy_amount",
        "买入占总成交比例": "buy_ratio",
        "卖出金额": "sell_amount",
        "卖出占总成交比例": "sell_ratio",
        "净成交额": "net_amount",
        "买卖类型": "seat_type_raw",
        "上榜理由": "reason",
        "排名": "rank",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

    required = {"stock_code", "seat_name"}
    if not required.issubset(df.columns):
        logger.warning("[lhb_local] 缺少必要列，实际列: %s", list(df.columns))
        return

    inserted = 0
    for _, row in df.iterrows():
        try:
            stock_code = str(row.get("stock_code", "")).strip()
            seat_name  = str(row.get("seat_name", "")).strip()
            if not stock_code or not seat_name:
                continue

            # 股票代码统一为 6 位数字（去掉 sz/sh 前缀）
            if stock_code[:2].lower() in ("sz", "sh", "bj"):
                stock_code = stock_code[2:]

            insert_lhb_seat(
                trade_date  = date,
                stock_code  = stock_code,
                seat_name   = seat_name,
                buy_amount  = float(row.get("buy_amount") or 0),
                sell_amount = float(row.get("sell_amount") or 0),
                net_amount  = float(row.get("net_amount") or 0),
                buy_ratio   = float(row.get("buy_ratio") or 0),
                sell_ratio  = float(row.get("sell_ratio") or 0),
                seat_type   = _classify_seat(seat_name),
                reason      = str(row.get("reason") or ""),
                rank        = int(float(row.get("rank") or 0)),
            )
            inserted += 1
        except Exception as e:
            logger.debug("[lhb_local] 跳过一行: %s", e)
            continue

    logger.info("[lhb_local] %s 写入 %d 条席位记录", date, inserted)

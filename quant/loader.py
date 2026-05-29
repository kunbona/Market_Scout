"""
quant/loader.py — 本地量价数据读取工具

数据路径: /mnt/ssd_1T/runist/data/Quant_Data/
- CSV 文件: GBK 编码, skiprows=1 (第0行是版权注释)
- Parquet 文件: 直接读取, trade_date 字段为 datetime.date 对象，格式可用 str() 转成 "YYYY-MM-DD"
"""

import logging
import os as _os
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

_data_root_env = _os.environ.get("QUANT_DATA_ROOT", "").strip()
DATA_ROOT = Path(_data_root_env) if _data_root_env else None

# ── 列名映射：CSV 中文列名 → 标准英文列名 ──────────────────────────────────────

_TRADING_COL_MAP = {
    "股票代码": "code",
    "股票名称": "stock_name",
    "交易日期": "trade_date",
    "开盘价": "open",
    "最高价": "high",
    "最低价": "low",
    "收盘价": "close",
    "前收盘价": "pre_close",
    "成交量": "volume",
    "成交额": "amount",
    "流通市值": "float_mv",
    "总市值": "total_mv",
    "新版申万一级行业名称": "industry_l1",
    "新版申万二级行业名称": "industry_l2",
    "新版申万三级行业名称": "industry_l3",
}


def _normalize_code(code: str) -> str:
    """
    将股票代码标准化为带前缀格式 (sh/sz/bj)。
    例如: '600000' -> 'sh600000', '300001' -> 'sz300001', '430418' -> 'bj430418'
    已有前缀则直接返回。
    """
    code = code.strip().lower()
    if code.startswith(("sh", "sz", "bj")):
        return code
    # 根据代码段判断前缀
    num = code[-6:] if len(code) >= 6 else code
    if num.startswith("6"):
        return "sh" + num
    elif num.startswith(("0", "3")):
        return "sz" + num
    elif num.startswith(("4", "8", "9", "2")):
        return "bj" + num
    else:
        return "sh" + num  # 默认 sh，兜底


# ── 核心函数 ───────────────────────────────────────────────────────────────────


def get_trading_data(code: str) -> pd.DataFrame:
    """
    读取单股日K数据。

    code 格式示例: 'sh600000' 或 '600000' (自动补前缀: sh/sz/bj)
    路径: DATA_ROOT/stock-trading-data-pro/{code}.csv
    返回标准化列名的 DataFrame，至少包含:
        trade_date, open, high, low, close, volume, amount
    GBK 编码，skiprows=1。
    找不到文件时返回空 DataFrame。
    """
    normalized = _normalize_code(code)
    csv_path = DATA_ROOT / "stock-trading-data-pro" / f"{normalized}.csv"

    if not csv_path.exists():
        logger.warning("[loader] 文件不存在: %s", csv_path)
        return pd.DataFrame()

    try:
        df = pd.read_csv(csv_path, encoding="GBK", skiprows=1)
        # 重命名已知列
        df = df.rename(columns=_TRADING_COL_MAP)
        # 确保 trade_date 为字符串格式 YYYY-MM-DD
        if "trade_date" in df.columns:
            df["trade_date"] = df["trade_date"].astype(str)
        return df
    except Exception as e:
        logger.error("[loader] 读取 %s 失败: %s", csv_path, e)
        return pd.DataFrame()


def get_factor_slice(factor_name: str, trade_date: str) -> pd.DataFrame:
    """
    从 parquet 读取指定日期的全市场截面数据。

    factor_name 示例: '涨停相关因子', '资金流相关因子', '成交额相关因子'
    路径: DATA_ROOT/factors/stock/daily/{factor_name}.parquet
    trade_date 格式: "YYYY-MM-DD"
    返回该 trade_date 的所有行。
    找不到文件或日期时返回空 DataFrame，不抛出异常。

    注意: parquet 中 trade_date 字段类型为 datetime.date 对象，
    本函数内部转为字符串后再做匹配。
    """
    parquet_path = DATA_ROOT / "factors" / "stock" / "daily" / f"{factor_name}.parquet"

    if not parquet_path.exists():
        logger.warning("[loader] Parquet 不存在: %s", parquet_path)
        return pd.DataFrame()

    try:
        df = pd.read_parquet(parquet_path)
        if "trade_date" not in df.columns:
            logger.warning("[loader] %s 缺少 trade_date 列", factor_name)
            return pd.DataFrame()

        # trade_date 可能是 datetime.date 对象，统一转为字符串再过滤
        df["trade_date"] = df["trade_date"].astype(str)
        result = df[df["trade_date"] == trade_date].copy()
        return result
    except Exception as e:
        logger.error("[loader] 读取 %s 失败: %s", parquet_path, e)
        return pd.DataFrame()


def get_latest_trade_date() -> str:
    """
    从本地数据获取最新交易日期（YYYY-MM-DD 字符串）。
    从 涨停相关因子.parquet 中取 trade_date 最大值。
    找不到时返回空字符串。
    """
    parquet_path = DATA_ROOT / "factors" / "stock" / "daily" / "涨停相关因子.parquet"

    if not parquet_path.exists():
        logger.warning("[loader] 涨停相关因子.parquet 不存在，无法获取最新交易日")
        return ""

    try:
        df = pd.read_parquet(parquet_path, columns=["trade_date"])
        if df.empty:
            return ""
        latest = df["trade_date"].max()
        # max() 返回 datetime.date 或字符串，统一转为 str
        return str(latest)
    except Exception as e:
        logger.error("[loader] 获取最新交易日期失败: %s", e)
        return ""


def get_sector_stocks(industry: str) -> list:
    """
    获取申万一级行业下的所有股票代码列表。

    实现方式: 从 申万行业.parquet 中取最新日期的截面，
    过滤 '新版申万一级行业名称' == industry，返回 code 列表。
    找不到时返回空列表。

    备注: stock-trading-data-pro CSV 中同样有行业字段，但读取全量 CSV 性能差，
    优先使用 parquet。
    """
    parquet_path = DATA_ROOT / "factors" / "stock" / "daily" / "申万行业.parquet"

    if not parquet_path.exists():
        logger.warning("[loader] 申万行业.parquet 不存在")
        return []

    try:
        df = pd.read_parquet(parquet_path, columns=["trade_date", "code", "新版申万一级行业名称"])
        if df.empty:
            return []

        # 取最新日期截面
        df["trade_date"] = df["trade_date"].astype(str)
        latest_date = df["trade_date"].max()
        latest_df = df[df["trade_date"] == latest_date]

        result = latest_df[latest_df["新版申万一级行业名称"] == industry]["code"].tolist()
        return result
    except Exception as e:
        logger.error("[loader] get_sector_stocks(%s) 失败: %s", industry, e)
        return []

"""
涨停/跌停占比指标

计算每日涨停和跌停个股占全市场活跃个股的比例。
用于两档信号系统的右侧确认（breadth thrust）。

数据源：xbx_stock_data.parquet（已预计算涨停价/跌停价）
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Optional, Tuple

from config import RAW_DATA_DIR, PROCESSED_DATA_DIR


def compute_limit_up_down_ratio(
    stock_data: pd.DataFrame,
) -> pd.DataFrame:
    """计算每日涨停/跌停占比

    涨停判定：收盘价 >= 涨停价
    跌停判定：收盘价 <= 跌停价

    Args:
        stock_data: 个股日线数据，必须包含 '交易日期', '收盘价', '涨停价', '跌停价' 列

    Returns:
        pd.DataFrame: 日期索引，列为 limit_up_ratio, limit_down_ratio (0.0 ~ 1.0)
    """
    required_cols = ["交易日期", "收盘价", "涨停价", "跌停价"]
    for col in required_cols:
        if col not in stock_data.columns:
            raise ValueError(f"数据缺少必要列: {col}")

    if stock_data.empty:
        raise ValueError("输入数据为空")

    df = stock_data[required_cols].copy()
    df["交易日期"] = pd.to_datetime(df["交易日期"])

    df["is_limit_up"] = df["收盘价"] >= df["涨停价"]
    df["is_limit_down"] = df["收盘价"] <= df["跌停价"]

    daily = df.groupby("交易日期").agg(
        total=("收盘价", "count"),
        limit_up=("is_limit_up", "sum"),
        limit_down=("is_limit_down", "sum"),
    )

    result = pd.DataFrame({
        "limit_up_ratio": daily["limit_up"] / daily["total"],
        "limit_down_ratio": daily["limit_down"] / daily["total"],
    })
    result.index.name = "交易日期"

    return result


def load_and_compute_limit_up_ratio(
    save: bool = True,
) -> Optional[pd.DataFrame]:
    """从XBX parquet加载并计算涨停/跌停占比

    Returns:
        pd.DataFrame with limit_up_ratio, limit_down_ratio or None if unavailable
    """
    parquet_path = RAW_DATA_DIR / "xbx_stock_data.parquet"

    if not parquet_path.exists():
        print(f"⚠ 涨停占比指标: xbx数据不可用 ({parquet_path})")
        return None

    print("计算涨停/跌停占比...")
    cols = ["交易日期", "收盘价", "涨停价", "跌停价"]
    df = pd.read_parquet(parquet_path, columns=cols)

    result = compute_limit_up_down_ratio(df)

    if save:
        output_path = PROCESSED_DATA_DIR / "limit_up_down_ratio.csv"
        out_df = result.reset_index()
        out_df.to_csv(output_path, index=False, encoding="utf-8-sig")
        print(f"✓ 涨停占比指标已保存: {output_path} ({len(result)} 天)")

    return result

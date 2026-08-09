"""
市场宽度指标 — 创新高个股占比

计算每日创N日新高的个股数量占全市场有交易个股数量的比例。
这是顶部背离的最强检测器：指数创新高但创新高个股减少 = 权重股拉指数的假突破。

数据源：xbx_stock_data.parquet（个股日线数据）
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Optional

from config import RAW_DATA_DIR, DATA_START_DATE, PROCESSED_DATA_DIR


DEFAULT_WINDOW = 60  # 60日新高


def compute_new_high_ratio(
    stock_data: pd.DataFrame,
    window: int = DEFAULT_WINDOW,
) -> pd.Series:
    """计算每日创N日新高个股占比

    Args:
        stock_data: 个股日线数据，必须包含 '股票代码', '交易日期', '收盘价_复权' 列
        window: 滚动窗口天数，默认60日

    Returns:
        pd.Series: 日期索引，值为创新高占比 (0.0 ~ 1.0)
    """
    required_cols = ["股票代码", "交易日期", "收盘价_复权"]
    for col in required_cols:
        if col not in stock_data.columns:
            raise ValueError(f"数据缺少必要列: {col}")

    if stock_data.empty:
        raise ValueError("输入数据为空")

    # 确保日期格式
    df = stock_data[required_cols].copy()
    df["交易日期"] = pd.to_datetime(df["交易日期"])
    df = df.sort_values(["股票代码", "交易日期"])

    # 计算每只股票的滚动N日最高价
    df["rolling_high"] = df.groupby("股票代码", observed=True)["收盘价_复权"].transform(
        lambda x: x.rolling(window=window, min_periods=window).max()
    )

    # 判断是否创新高：当日收盘价 >= 滚动最高价（NaN rolling_high → NaN）
    df["is_new_high"] = (df["收盘价_复权"] >= df["rolling_high"]).where(df["rolling_high"].notna())

    # 只统计窗口期已满（rolling_high非NaN）的股票
    valid = df.dropna(subset=["rolling_high"])
    daily = valid.groupby("交易日期").agg(
        new_high_count=("is_new_high", "sum"),
        total_stocks=("股票代码", "count"),
    )

    ratio = (daily["new_high_count"] / daily["total_stocks"]).rename("new_high_ratio")

    # 用全部交易日期重新索引，窗口不够的日期自然为NaN
    all_dates = sorted(df["交易日期"].unique())
    ratio = ratio.reindex(pd.DatetimeIndex(all_dates))
    ratio.index = ratio.index.as_unit("ns")
    ratio.index.name = "交易日期"

    return ratio


def load_and_compute_market_breadth(
    window: int = DEFAULT_WINDOW,
    save: bool = True,
) -> Optional[pd.Series]:
    """从xbx数据加载并计算市场宽度指标

    Returns:
        pd.Series or None if data unavailable
    """
    parquet_path = RAW_DATA_DIR / "xbx_stock_data.parquet"

    if not parquet_path.exists():
        print(f"⚠ 市场宽度指标: xbx数据不可用 ({parquet_path})")
        return None

    print("计算市场宽度指标（创新高占比）...")
    df = pd.read_parquet(parquet_path, columns=["股票代码", "交易日期", "收盘价_复权"])

    ratio = compute_new_high_ratio(df, window=window)

    if save:
        output_path = PROCESSED_DATA_DIR / "market_breadth_new_high_ratio.csv"
        out_df = ratio.reset_index()
        out_df.columns = ["交易日期", "new_high_ratio"]
        out_df.to_csv(output_path, index=False, encoding="utf-8-sig")
        print(f"✓ 市场宽度指标已保存: {output_path} ({len(ratio)} 天)")

    return ratio

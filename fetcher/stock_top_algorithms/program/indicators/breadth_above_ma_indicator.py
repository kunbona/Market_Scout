"""市场广度指标 — 站上MA60个股占比（历史分位）

计算每日全市场收盘价站上各自MA60的个股占比，再做 expanding 历史分位。

用途：周期信号的"背离分类器"，不进等权打分。
- 高分位 = 广度健康（多数股在中期均线上）→ 普涨牛市
- 低分位 = 广度萎缩（少数股拉指数）→ 结构性背离顶

关键金融逻辑：结构顶(2018/2021)的广度反而低（少数抱团股创新高、多数股已走弱），
所以广度绝对值不能直接等权进风险分数（会把结构顶分数拖更低，方向相反）。
它的正确用法是区分两种"高情绪"状态：
  情绪高 + 广度健康(高分位) → 健康牛市，不逃顶（如2020-07中继）
  情绪高 + 广度背离(低分位) → 结构顶，逃顶（如2018/2021）

数据源：xbx_stock_data.parquet（个股日线，复权收盘价）
输出：data/processed/breadth_above_ma60.csv（交易日期, above_ma60_ratio, breadth_pct）
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Optional

from config import RAW_DATA_DIR, PROCESSED_DATA_DIR


DEFAULT_MA_WINDOW = 60       # 中期均线窗口
PERCENTILE_MIN_PERIODS = 250  # expanding 分位最小样本（~1年），不足返回NaN


def compute_above_ma_ratio(
    stock_data: pd.DataFrame,
    ma_window: int = DEFAULT_MA_WINDOW,
) -> pd.Series:
    """计算每日站上MA_N的个股占比 (0.0~1.0)

    Args:
        stock_data: 个股日线，需含 '股票代码','交易日期','收盘价_复权'
        ma_window: 均线窗口

    Returns:
        pd.Series: 日期索引，站上MA占比
    """
    required = ["股票代码", "交易日期", "收盘价_复权"]
    for col in required:
        if col not in stock_data.columns:
            raise ValueError(f"数据缺少必要列: {col}")

    df = stock_data[required].copy()
    df["交易日期"] = pd.to_datetime(df["交易日期"])
    df = df.sort_values(["股票代码", "交易日期"])

    df["ma"] = df.groupby("股票代码", observed=True)["收盘价_复权"].transform(
        lambda x: x.rolling(window=ma_window, min_periods=ma_window).mean()
    )
    # 仅在 MA 可算时纳入统计（窗口未满 → NaN，不计入分母）
    df["above"] = (df["收盘价_复权"] >= df["ma"]).where(df["ma"].notna())

    valid = df.dropna(subset=["ma"])
    daily = valid.groupby("交易日期").agg(
        above_count=("above", "sum"),
        total=("above", "count"),
    )
    ratio = (daily["above_count"] / daily["total"].replace(0, np.nan)).rename("above_ma60_ratio")
    return ratio.sort_index()


def compute_breadth_percentile(ratio: pd.Series) -> pd.Series:
    """对站上MA占比做 expanding 历史分位 (0~100)，无未来函数

    Returns:
        pd.Series: breadth_pct，高=广度健康，低=广度背离
    """
    pct = ratio.expanding(min_periods=PERCENTILE_MIN_PERIODS).rank(pct=True) * 100
    return pct.rename("breadth_pct")


def load_and_compute_breadth(
    ma_window: int = DEFAULT_MA_WINDOW,
    save: bool = True,
    stock_data: Optional[pd.DataFrame] = None,
) -> Optional[pd.Series]:
    """从xbx数据加载并计算广度分位指标

    Args:
        ma_window: 均线窗口
        save: 是否落盘CSV
        stock_data: 可选，外部传入的共享股票数据（避免重复加载）

    Returns:
        pd.Series(breadth_pct) 或 None（数据不可用）
    """
    if stock_data is None:
        parquet_path = RAW_DATA_DIR / "xbx_stock_data.parquet"
        if not parquet_path.exists():
            print(f"⚠ 广度指标: xbx数据不可用 ({parquet_path})")
            return None
        print("计算市场广度指标（站上MA60占比 + 历史分位）...")
        stock_data = pd.read_parquet(
            parquet_path, columns=["股票代码", "交易日期", "收盘价_复权"]
        )
    else:
        print("计算市场广度指标（站上MA60占比 + 历史分位）...")

    ratio = compute_above_ma_ratio(stock_data, ma_window=ma_window)
    pct = compute_breadth_percentile(ratio)

    if save:
        output_path = PROCESSED_DATA_DIR / "breadth_above_ma60.csv"
        out_df = pd.DataFrame({
            "交易日期": ratio.index,
            "above_ma60_ratio": ratio.values,
            "breadth_pct": pct.reindex(ratio.index).values,
        })
        out_df.to_csv(output_path, index=False, encoding="utf-8-sig")
        print(f"✓ 市场广度指标已保存: {output_path} ({len(ratio)} 天)")

    return pct

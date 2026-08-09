"""抱团率原始数据生成模块

从 XBX 个股成交额数据计算"前5%个股成交额占比"的日频序列，生成
data/raw_data/herding_rate_daily.csv，供 HerdingRateIndicator 读取。

此前 herding_rate_daily.csv 是外部预计算的死文件（停在 2025-09，无生成代码），
现改为从 XBX 自动生成，与 market_crowdedness 同源（后者算前10%占比）。

口径（已对齐原文件，相关系数 0.999）：
  - 每日：前5%个股成交额 / 全市场成交额
  - "平均前5%个股成交额占比" = 每日占比的 20 日滚动平均
  - "最新占比" = 当日占比；"20日最高/最低" = 当日占比的 20 日滚动极值
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Optional

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from config import RAW_DATA_DIR, DATA_START_DATE, INDICATOR_START_DATE


# 口径常量（对齐原 herding_rate_daily.csv）
HERDING_TOP_PCT = 0.05      # 前5%个股
HERDING_AVG_WINDOW = 20     # 20日滚动平均（"回溯天数"）
# 输出起点：对齐原文件(2014-01)，保证 herding 指标的滚动分位窗口与历史回测一致。
# 占比本身从全量数据(2012)算以预热20日均值，但输出裁剪到此日期。
HERDING_OUTPUT_START = INDICATOR_START_DATE

OUTPUT_FILENAME = "herding_rate_daily.csv"


def _daily_top_pct_ratio(df: pd.DataFrame, top_pct: float) -> pd.Series:
    """每日：前 top_pct 个股成交额占全市场成交额比例"""
    def _ratio(group: pd.DataFrame) -> float:
        s = group["成交额"].dropna().sort_values(ascending=False)
        n = len(s)
        if n == 0:
            return np.nan
        top_n = max(1, int(n * top_pct))
        total = s.sum()
        if total == 0:
            return 0.0
        return s.iloc[:top_n].sum() / total

    return df.groupby("交易日期").apply(_ratio).sort_index()


def generate_herding_daily(
    stock_data: Optional[pd.DataFrame] = None,
    save: bool = True,
    top_pct: float = HERDING_TOP_PCT,
    window: int = HERDING_AVG_WINDOW,
) -> Optional[pd.DataFrame]:
    """从 XBX 个股成交额生成抱团率日频数据

    Args:
        stock_data: 可选，预加载的 XBX 数据（含 交易日期/股票代码/成交额）。
                    传入可避免重复加载（与 run_indicators 的共享数据复用）。
        save: 是否写入 data/raw_data/herding_rate_daily.csv
        top_pct: 前百分比（默认5%）
        window: 滚动平均窗口（默认20）

    Returns:
        DataFrame，列与原文件一致；XBX 不可用时返回 None
    """
    df = stock_data
    if df is None:
        parquet_path = RAW_DATA_DIR / "xbx_stock_data.parquet"
        if not parquet_path.exists():
            print(f"⚠ 抱团率生成: XBX数据不可用 ({parquet_path})，跳过")
            return None
        df = pd.read_parquet(parquet_path, columns=["交易日期", "股票代码", "成交额"])

    required = ["交易日期", "股票代码", "成交额"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        print(f"⚠ 抱团率生成: 缺少列 {missing}，跳过")
        return None

    df = df[required].copy()
    df["交易日期"] = pd.to_datetime(df["交易日期"])

    # 每日前5%占比
    daily_ratio = _daily_top_pct_ratio(df, top_pct)

    # 20日滚动统计
    avg = daily_ratio.rolling(window, min_periods=window).mean()
    rmax = daily_ratio.rolling(window, min_periods=1).max()
    rmin = daily_ratio.rolling(window, min_periods=1).min()

    # 回溯天数：前 window 天递增，之后固定为 window（对齐原文件）
    lookback = np.minimum(np.arange(1, len(daily_ratio) + 1), window)

    out = pd.DataFrame({
        "目标日期": daily_ratio.index,
        "回溯天数": lookback,
        "平均前5%个股成交额占比": avg.values,
        "最新占比": daily_ratio.values,
        "20日最高占比": rmax.values,
        "20日最低占比": rmin.values,
    })

    # 早期 avg 为 NaN（不足window天）时，用当日占比回退填充，保证连续
    out["平均前5%个股成交额占比"] = out["平均前5%个股成交额占比"].fillna(out["最新占比"])

    # 裁剪到输出起点（占比已用全量数据预热20日均值，此处只裁输出范围，
    # 保证 herding 指标的滚动分位窗口起点与历史回测一致）
    out = out[out["目标日期"] >= pd.Timestamp(HERDING_OUTPUT_START)].reset_index(drop=True)

    if save:
        output_path = RAW_DATA_DIR / OUTPUT_FILENAME
        out.to_csv(output_path, index=False, encoding="utf-8-sig")
        print(f"✓ 抱团率原始数据已生成: {output_path} "
              f"({len(out)}天, 最新 {out['目标日期'].max().date()})")

    return out


if __name__ == "__main__":
    generate_herding_daily(save=True)

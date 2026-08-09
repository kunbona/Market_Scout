"""
抱团率指标模块

计算前5%个股成交额占比（抱团率）的滚动Z-score和分位数。
抱团率越高说明资金越集中于头部个股，市场见顶风险越大。

关键设计：
- 使用滚动Z-score标准化（默认3年窗口），消除A股市场扩容（2600→5400只）导致的趋势漂移
- 输出滚动分位数（0-100）作为风险百分比
- 数据来源：预计算的日频抱团率CSV文件
"""

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime
from typing import Optional, Dict, Any
from pathlib import Path

from .base_indicator import BaseIndicator
from .indicator_result import IndicatorResult
from config import (
    INDICATOR_START_DATE, PROCESSED_DATA_DIR, PIC_DIR, RAW_DATA_DIR,
    HERDING_RATE_ROLLING_WINDOW_YEARS
)


class HerdingRateIndicator(BaseIndicator):
    """抱团率指标
    
    1. 加载日频抱团率数据（前5%个股成交额占比）
    2. 滚动Z-score标准化，消除市场扩容导致的趋势漂移
    3. 计算滚动分位数（0-100）作为风险百分比
    4. 以此判断市场资金集中度和见顶风险
    """
    
    def __init__(
        self,
        name: str = "herding_rate",
        description: Optional[str] = None,
        save_processed: bool = True
    ):
        if description is None:
            description = "抱团率指标：前5%个股成交额占比的滚动分位数"
        
        super().__init__(name=name, description=description)
        self.save_processed = save_processed
        self._processed_data = None
    
    def _load_data(self) -> pd.DataFrame:
        """加载抱团率日频数据"""
        file_path = RAW_DATA_DIR / "herding_rate_daily.csv"
        
        if not file_path.exists():
            raise ValueError(
                f"抱团率数据文件不存在: {file_path}\n"
                "请将抱团率_日频版.csv复制到data/raw_data/herding_rate_daily.csv"
            )
        
        df = pd.read_csv(file_path)
        
        required_cols = ['目标日期', '平均前5%个股成交额占比']
        missing = [c for c in required_cols if c not in df.columns]
        if missing:
            raise ValueError(f"数据缺少必要列: {missing}")
        
        df['目标日期'] = pd.to_datetime(df['目标日期'])
        df = df.sort_values('目标日期').reset_index(drop=True)
        
        return df
    
    def _standardize(self, df: pd.DataFrame) -> pd.DataFrame:
        """滚动Z-score标准化 + 滚动分位数
        
        使用滚动窗口消除A股市场扩容（2014年2600只→2024年5400只）
        导致的抱团率结构性上升趋势。
        """
        window = int(HERDING_RATE_ROLLING_WINDOW_YEARS * 250)
        min_periods = 125  # 至少半年数据
        
        vals = df['平均前5%个股成交额占比']
        
        # 滚动Z-score
        rolling_mean = vals.rolling(window, min_periods=min_periods).mean()
        rolling_std = vals.rolling(window, min_periods=min_periods).std()
        df['zscore'] = (vals - rolling_mean) / rolling_std
        
        # 滚动分位数 (0-100)
        df['percentile'] = vals.rolling(
            window, min_periods=min_periods
        ).rank(pct=True) * 100
        
        # 保留原始值
        df['raw_value'] = vals
        
        return df
    
    def calculate(self, start_date: Optional[str] = None, **kwargs) -> IndicatorResult:
        try:
            if start_date is None:
                start_date = INDICATOR_START_DATE
            filter_start_date = pd.to_datetime(start_date)
            
            # 加载数据
            df = self._load_data()
            
            if df.empty:
                raise ValueError("加载的抱团率数据为空")
            
            # 标准化
            df = self._standardize(df)
            
            # 过滤结果日期
            result_df = df[df['目标日期'] >= filter_start_date].copy()
            result_df = result_df.dropna(subset=['percentile'])
            
            if result_df.empty:
                raise ValueError(f"过滤后的结果为空 (start_date={start_date})")
            
            self._processed_data = result_df
            
            # 保存和绘图
            if self.save_processed:
                self._save_and_plot(result_df)
            
            # 构建结果
            latest = result_df.iloc[-1]
            current_percentile = latest['percentile']
            current_zscore = latest['zscore']
            current_raw = latest['raw_value']
            current_date = latest['目标日期']
            
            message = (
                f"抱团率分位数: {current_percentile:.2f}% "
                f"(Z-score: {current_zscore:.2f}, "
                f"原始值: {current_raw:.4f}, "
                f"日期: {current_date.strftime('%Y-%m-%d')})"
            )
            
            return self.create_result(
                risk_percentage=current_percentile,
                message=message,
                extra_data={
                    "percentile": float(current_percentile),
                    "zscore": float(current_zscore),
                    "raw_value": float(current_raw),
                    "date": current_date.strftime('%Y-%m-%d'),
                    "rolling_window_years": HERDING_RATE_ROLLING_WINDOW_YEARS
                },
                timestamp=current_date
            )
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            return self.create_result(
                risk_percentage=0.0,
                message=f"计算失败: {str(e)}",
                extra_data={"error": True, "error_message": str(e)}
            )
    
    def _save_and_plot(self, df: pd.DataFrame):
        """保存处理后数据并绘制趋势图"""
        # 保存数据
        output_file = PROCESSED_DATA_DIR / "herding_rate.csv"
        df.to_csv(output_file, index=False)
        
        # 绘图
        try:
            import platform
            if platform.system() == 'Darwin':
                plt.rcParams['font.sans-serif'] = ['Arial Unicode MS']
            elif platform.system() == 'Windows':
                plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei']
            else:
                plt.rcParams['font.sans-serif'] = ['WenQuanYi Micro Hei']
            plt.rcParams['axes.unicode_minus'] = False
            
            fig, axes = plt.subplots(3, 1, figsize=(14, 12), sharex=True)
            
            # 1. 原始抱团率
            ax1 = axes[0]
            ax1.plot(df['目标日期'], df['raw_value'], color='#1f77b4', linewidth=0.8)
            ax1.set_title("抱团率原始值（前5%个股成交额占比）", fontsize=13)
            ax1.set_ylabel("占比")
            ax1.grid(True, alpha=0.3)
            
            # 2. Z-score
            ax2 = axes[1]
            ax2.plot(df['目标日期'], df['zscore'], color='#d62728', linewidth=0.8)
            ax2.axhline(y=2.0, color='red', linestyle='--', alpha=0.5, label='Z=2.0 (极端)')
            ax2.axhline(y=1.5, color='orange', linestyle='--', alpha=0.5, label='Z=1.5 (高)')
            ax2.axhline(y=0, color='gray', linestyle='-', alpha=0.3)
            ax2.axhline(y=-1.5, color='green', linestyle='--', alpha=0.5, label='Z=-1.5 (低)')
            window_str = f"{HERDING_RATE_ROLLING_WINDOW_YEARS}年"
            ax2.set_title(f"抱团率滚动Z-score（{window_str}窗口）", fontsize=13)
            ax2.set_ylabel("Z-score")
            ax2.legend(loc='upper left', fontsize=9)
            ax2.grid(True, alpha=0.3)
            
            # 3. 滚动分位数
            ax3 = axes[2]
            ax3.fill_between(df['目标日期'], df['percentile'], alpha=0.3, color='#9467bd')
            ax3.plot(df['目标日期'], df['percentile'], color='#9467bd', linewidth=0.8)
            ax3.axhline(y=80, color='red', linestyle='--', alpha=0.5, label='高风险(80%)')
            ax3.axhline(y=20, color='green', linestyle='--', alpha=0.5, label='低风险(20%)')
            ax3.set_title(f"抱团率滚动分位数（{window_str}窗口）", fontsize=13)
            ax3.set_xlabel("日期")
            ax3.set_ylabel("分位数 (%)")
            ax3.legend(loc='upper left', fontsize=9)
            ax3.grid(True, alpha=0.3)
            ax3.set_ylim(0, 100)
            
            # 格式化日期
            ax3.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
            ax3.xaxis.set_major_locator(mdates.YearLocator())
            plt.xticks(rotation=45)
            
            plt.tight_layout()
            
            pic_file = PIC_DIR / "herding_rate_trend.png"
            plt.savefig(pic_file, dpi=300)
            plt.close(fig)
            print(f"✓ 抱团率趋势图已保存: {pic_file}")
            
        except Exception as e:
            print(f"绘图失败: {e}")
    
    def get_processed_data(self) -> Optional[pd.DataFrame]:
        """获取处理后的数据"""
        return self._processed_data

"""
MA250 Bias 指标模块

计算每个股票与自己年线MA250的Bias，统计每天所有股票Bias的平均值，
并计算该平均值在历史上的分位数，作为牛熊市进度表。
"""

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime
from typing import Optional, Dict, Any, List
from pathlib import Path

from .base_indicator import BaseIndicator
from .indicator_result import IndicatorResult
from config import (
    DATA_START_DATE, INDICATOR_START_DATE, PROCESSED_DATA_DIR, PIC_DIR,
    RAW_DATA_DIR
)

class MA250BiasIndicator(BaseIndicator):
    """MA250 Bias 指标
    
    1. 加载股票数据
    2. 计算每个股票的MA250和Bias: (Close - MA250) / MA250
    3. 计算每天所有股票Bias的平均值
    4. 计算该平均值在历史上的分位数 (expanding rank)
    5. 以此判断市场整体的牛熊位置
    """
    
    def __init__(
        self, 
        name: str = "ma250_bias",
        description: Optional[str] = None,
        save_processed: bool = True
    ):
        if description is None:
            description = "MA250 Bias指标：基于全市场股票偏离年线幅度的平均水平"
        
        super().__init__(name=name, description=description)
        self.save_processed = save_processed
        self._processed_data = None
        
    def _load_data(self) -> pd.DataFrame:
        """加载数据，优先从parquet加载"""
        parquet_path = RAW_DATA_DIR / "xbx_stock_data.parquet"
        
        if parquet_path.exists():
            try:
                print(f"从Parquet文件加载数据: {parquet_path}")
                df = pd.read_parquet(parquet_path)
                return df
            except Exception as e:
                print(f"加载Parquet文件失败: {e}")
        
        # 如果parquet不存在或加载失败，尝试从load_xbx_data加载
        try:
            from ..api.load_xbx_data import load_xbx_data
            print("尝试从原始CSV文件重新加载数据...")
            # 使用配置的数据开始日期，确保有足够历史数据计算MA250
            result = load_xbx_data(start_date=DATA_START_DATE, save_to_file=True)
            if result['success']:
                return result['data']
            else:
                raise ValueError(f"加载原始数据失败: {result.get('error')}")
        except ImportError:
            raise ValueError("无法导入load_xbx_data模块，且Parquet文件不可用")
            
    def calculate(self, start_date: Optional[str] = None, **kwargs) -> IndicatorResult:
        try:
            # 1. 确定过滤用的开始日期（结果输出日期）
            if start_date is None:
                start_date = INDICATOR_START_DATE
            filter_start_date = pd.to_datetime(start_date)
            
            # 2. 加载全量历史数据
            # 优先使用传入的共享数据，避免重复加载
            df = kwargs.get('stock_data')
            if df is None:
                df = self._load_data()
            
            if df is None or df.empty:
                raise ValueError("加载的数据为空")
                
            # 确保必要的列存在
            if '收盘价_复权' not in df.columns:
                raise ValueError("数据中缺少'收盘价_复权'列")
                
            # 3. 计算MA250和Bias
            # 先按代码和日期排序
            df = df.sort_values(by=['股票代码', '交易日期'])
            
            print("正在计算个股MA250和Bias(可能需要一些时间)...")
            
            # 计算MA250
            # min_periods=1 表示只要有数据就计算，但为了准确性通常年线需要一定数据量
            # 这里设置min_periods=200，保证至少有200天数据才计算年线
            df['ma250'] = df.groupby('股票代码', observed=True)['收盘价_复权'].transform(
                lambda x: x.rolling(window=250, min_periods=200).mean()
            )
            
            # 计算Bias
            # 使用对数Bias：ln(Price / MA250)
            # 这能有效压缩极端高值的影响（如2015年牛市），同时保持单调性
            df['bias'] = np.log(df['收盘价_复权'] / df['ma250'])
            
            # 移除无法计算Bias的行 (MA250为空)
            df_bias = df.dropna(subset=['bias'])
            
            if df_bias.empty:
                raise ValueError("计算Bias后没有有效数据")
            
            # 4. 按日期聚合，计算每天所有股票Bias的平均值
            daily_stats = df_bias.groupby('交易日期')['bias'].mean().reset_index()
            daily_stats.rename(columns={'bias': 'avg_bias'}, inplace=True)
            
            # 5. 计算Bias平均值的历史分位数 (Expanding Rank)
            daily_stats = daily_stats.sort_values('交易日期')
            daily_stats['percentile'] = daily_stats['avg_bias'].expanding().rank(pct=True) * 100
            
            # 6. 过滤结果日期
            result_df = daily_stats[daily_stats['交易日期'] >= filter_start_date].copy()
            
            if result_df.empty:
                raise ValueError(f"过滤后的结果为空 (start_date={start_date})")
                
            self._processed_data = result_df
            
            # 7. 保存和绘图
            if self.save_processed:
                self._save_and_plot(result_df)
                
            # 8. 构建结果
            latest = result_df.iloc[-1]
            current_val = latest['percentile']
            current_bias = latest['avg_bias']
            
            message = f"全市场平均Bias(Log)分位数: {current_val:.2f}% (平均Bias(Log): {current_bias:.4f}, 日期: {latest['交易日期'].strftime('%Y-%m-%d')})"
            
            return self.create_result(
                risk_percentage=current_val,
                message=message,
                extra_data={
                    "percentile": float(current_val),
                    "avg_bias": float(current_bias),
                    "date": latest['交易日期'].strftime('%Y-%m-%d')
                },
                timestamp=latest['交易日期']
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
        # 保存数据
        output_file = PROCESSED_DATA_DIR / "ma250_bias.csv"
        df.to_csv(output_file, index=False)
        
        # 绘图
        try:
            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), sharex=True)
            
            # 设置中文字体
            plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
            plt.rcParams['axes.unicode_minus'] = False
            
            # 绘制分位数趋势
            ax1.plot(df['交易日期'], df['percentile'], label='Bias平均值分位数', color='#1f77b4')
            ax1.axhline(y=80, color='red', linestyle='--', alpha=0.5, label='高风险(80%)')
            ax1.axhline(y=20, color='green', linestyle='--', alpha=0.5, label='低风险(20%)')
            ax1.set_title("全市场MA250 Bias平均值分位数历史走势", fontsize=14)
            ax1.set_ylabel("分位数 (%)")
            ax1.grid(True, alpha=0.3)
            ax1.legend(loc='best')
            
            # 绘制Bias绝对值趋势
            ax2.plot(df['交易日期'], df['avg_bias'], label='Bias(Log)平均值', color='#ff7f0e')
            ax2.axhline(y=0, color='black', linestyle='-', alpha=0.3)
            ax2.set_title("全市场MA250 Bias(Log)平均值走势", fontsize=14)
            ax2.set_xlabel("日期")
            ax2.set_ylabel("Bias(Log)值")
            ax2.grid(True, alpha=0.3)
            ax2.legend(loc='best')
            
            # 格式化日期
            ax2.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
            ax2.xaxis.set_major_locator(mdates.YearLocator())
            plt.xticks(rotation=45)
            
            plt.tight_layout()
            
            pic_file = PIC_DIR / "ma250_bias_trend.png"
            plt.savefig(pic_file, dpi=300)
            plt.close(fig)
            
        except Exception as e:
            print(f"绘图失败: {e}")

    def get_processed_data(self) -> Optional[pd.DataFrame]:
        """获取处理后的数据"""
        return self._processed_data


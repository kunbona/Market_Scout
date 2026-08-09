"""
价格分位数指标模块

计算每个个股的历史复权收盘价分位数，并计算全市场平均分位数。
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

try:
    from ..api.load_xbx_data import load_xbx_data
except ImportError:
    # 兼容直接运行的情况
    pass

class PricePrecentileIndicator(BaseIndicator):
    """价格分位数指标
    
    1. 加载股票数据（优先使用parquet）
    2. 计算每个个股复权收盘价在历史数据的分位数 (expanding rank)
    3. 计算所有股票分位数的等权平均值
    4. 以此判断市场整体的价格位置
    """
    
    def __init__(
        self, 
        name: str = "price_precentile",  # 保持与文件名一致的typo，或者修正？用户给的是price_precentile_indicator.py
        description: Optional[str] = None,
        save_processed: bool = True
    ):
        if description is None:
            description = "价格分位数指标：基于个股历史价格分位数的市场平均水位"
        
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
        # 注意：这需要原始CSV文件存在
        try:
            from ..api.load_xbx_data import load_xbx_data
            print("尝试从原始CSV文件重新加载数据...")
            # 使用配置的数据开始日期
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
            
            # 2. 加载全量历史数据（从DATA_START_DATE开始，用于计算历史分位数）
            # 优先使用传入的共享数据，避免重复加载
            df = kwargs.get('stock_data')
            if df is None:
                df = self._load_data()
            
            if df is None or df.empty:
                raise ValueError("加载的数据为空")
                
            # 确保必要的列存在
            if '收盘价_复权' not in df.columns:
                # 尝试计算复权价? 
                # 这里假设数据源已经是处理好的，如果从parquet加载，应该包含复权价
                # 如果是从load_xbx_data加载，也应该包含
                # 如果没有，可能需要重新计算，但这里简化处理，报错
                raise ValueError("数据中缺少'收盘价_复权'列")
                
            # 3. 计算分位数
            # 先按代码和日期排序
            df = df.sort_values(by=['股票代码', '交易日期'])
            
            # 使用transform计算expanding rank
            # 百分比 = rank / count * 100
            # pct=True gives values in [0, 1] (or 1/N to 1.0)
            
            print("正在计算个股历史分位数(可能需要一些时间)...")
            
            # 这种写法在数据量大时可能较慢，但最准确
            # 优化：
            # 如果内存允许，pandas的groupby expanding rank是可以的
            df['percentile'] = df.groupby('股票代码', observed=True)['收盘价_复权'].transform(
                lambda x: x.expanding().rank(pct=True) * 100
            )
            
            # 4. 按日期聚合，计算等权平均分位数
            daily_stats = df.groupby('交易日期')['percentile'].mean().reset_index()
            daily_stats.rename(columns={'percentile': 'avg_percentile'}, inplace=True)
            
            # 5. 过滤结果日期
            result_df = daily_stats[daily_stats['交易日期'] >= filter_start_date].copy()
            
            if result_df.empty:
                raise ValueError(f"过滤后的结果为空 (start_date={start_date})")
                
            self._processed_data = result_df
            
            # 6. 保存和绘图
            if self.save_processed:
                self._save_and_plot(result_df)
                
            # 7. 构建结果
            latest = result_df.iloc[-1]
            current_val = latest['avg_percentile']
            
            message = f"全市场平均价格分位数: {current_val:.2f}% (日期: {latest['交易日期'].strftime('%Y-%m-%d')})"
            
            return self.create_result(
                risk_percentage=current_val,
                message=message,
                extra_data={
                    "avg_percentile": float(current_val),
                    "date": latest['交易日期'].strftime('%Y-%m-%d'),
                    "stock_count": len(df['股票代码'].unique())
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
        output_file = PROCESSED_DATA_DIR / "price_percentile.csv"
        df.to_csv(output_file, index=False)
        
        # 绘图
        try:
            fig, ax = plt.subplots(figsize=(12, 6))
            
            # 设置中文字体
            plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
            plt.rcParams['axes.unicode_minus'] = False
            
            ax.plot(df['交易日期'], df['avg_percentile'], label='全市场平均价格分位数', color='#1f77b4')
            
            # 添加阈值线
            ax.axhline(y=80, color='red', linestyle='--', alpha=0.5, label='高风险(80%)')
            ax.axhline(y=20, color='green', linestyle='--', alpha=0.5, label='低风险(20%)')
            
            ax.set_title("全市场价格分位数历史走势", fontsize=14)
            ax.set_xlabel("日期")
            ax.set_ylabel("平均分位数 (%)")
            ax.grid(True, alpha=0.3)
            ax.legend(loc='best')
            
            # 格式化日期
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
            ax.xaxis.set_major_locator(mdates.YearLocator())
            plt.xticks(rotation=45)
            
            plt.tight_layout()
            
            pic_file = PIC_DIR / "price_percentile_trend.png"
            plt.savefig(pic_file, dpi=300)
            plt.close(fig)
            
        except Exception as e:
            print(f"绘图失败: {e}")

    def get_processed_data(self) -> Optional[pd.DataFrame]:
        """获取处理后的数据
        
        Returns:
            处理后的价格分位数数据DataFrame，如果尚未计算则返回None
        """
        return self._processed_data



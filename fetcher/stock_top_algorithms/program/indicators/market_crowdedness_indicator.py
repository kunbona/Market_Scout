"""
大盘拥挤度指标模块

计算过去Rolling 5天的成交额前5%的个股成交额占全市场的成交额比例。
比例越高说明市场越拥挤，风险越大。
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
    RAW_DATA_DIR, MARKET_CROWDEDNESS_ROLLING_WINDOW_YEARS
)

class MarketCrowdednessIndicator(BaseIndicator):
    """大盘拥挤度指标
    
    1. 加载股票数据
    2. 计算每个股票的Rolling 20天成交额
    3. 每天计算成交额前10%的个股的成交额总和占全市场总成交额的比例
    4. 计算该比例在历史上的分位数 (Rolling Rank, 默认10年)
    5. 以此判断市场整体的拥挤度和风险
    """
    
    def __init__(
        self, 
        name: str = "market_crowdedness",
        description: Optional[str] = None,
        save_processed: bool = True
    ):
        if description is None:
            description = "大盘拥挤度指标：头部股票成交额集中度"
        
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
            
            # 2. 加载全量历史数据（从DATA_START_DATE开始，用于Rolling计算）
            # 优先使用传入的共享数据，避免重复加载
            df = kwargs.get('stock_data')
            if df is None:
                df = self._load_data()
            
            if df is None or df.empty:
                raise ValueError("加载的数据为空")
                
            # 确保必要的列存在
            if '成交额' not in df.columns:
                raise ValueError("数据中缺少'成交额'列")
                
            # 3. 计算Rolling 20天成交额
            print("正在计算个股Rolling 20天成交额...")
            # 先按代码和日期排序
            df = df.sort_values(by=['股票代码', '交易日期'])
            
            # 计算5日滚动成交额
            df['rolling_turnover'] = df.groupby('股票代码', observed=True)['成交额'].transform(
                lambda x: x.rolling(window=20, min_periods=20).sum()
            )
            
            # 移除无法计算的行
            df_valid = df.dropna(subset=['rolling_turnover'])
            
            if df_valid.empty:
                raise ValueError("计算Rolling成交额后没有有效数据")
            
            # 4. 计算每日拥挤度
            print("正在计算每日拥挤度比例 (Top 10%成交额占比)...")
            
            def calculate_crowdedness(group):
                # 按滚动成交额降序排序
                sorted_group = group.sort_values('rolling_turnover', ascending=False)
                n = len(sorted_group)
                if n == 0:
                    return np.nan
                
                # 取前10%的个股
                top_n = max(1, int(n * 0.1)) # 至少取1只
                top_stocks = sorted_group.iloc[:top_n]
                
                total_turnover = sorted_group['rolling_turnover'].sum()
                if total_turnover == 0:
                    return 0.0
                    
                top_turnover = top_stocks['rolling_turnover'].sum()
                
                return top_turnover / total_turnover

            # 使用groupby + apply计算
            daily_crowdedness = df_valid.groupby('交易日期').apply(calculate_crowdedness).reset_index()
            daily_crowdedness.columns = ['交易日期', 'crowdedness_ratio']
            
            # 5. 计算拥挤度比例的历史分位数 (Rolling Rank)
            daily_crowdedness = daily_crowdedness.sort_values('交易日期')
            
            # 使用配置的滚动窗口计算分位数（默认5年，约1250个交易日）
            window_days = int(MARKET_CROWDEDNESS_ROLLING_WINDOW_YEARS * 250)
            print(f"正在计算拥挤度分位数 (Rolling Window: {MARKET_CROWDEDNESS_ROLLING_WINDOW_YEARS}年, {window_days}天)...")
            
            daily_crowdedness['percentile'] = daily_crowdedness['crowdedness_ratio'].rolling(
                window=window_days, 
                min_periods=250  # 至少需要1年的数据才能开始计算
            ).rank(pct=True) * 100
            
            # 对于早期数据不足的情况，回退到Expanding计算（可选，保证早期也有数据）
            # 或者就让它是NaN，反正我们会过滤INDICATOR_START_DATE之后的数据
            
            # 6. 过滤结果日期
            result_df = daily_crowdedness[daily_crowdedness['交易日期'] >= filter_start_date].copy()
            
            if result_df.empty:
                raise ValueError(f"过滤后的结果为空 (start_date={start_date})")
                
            self._processed_data = result_df
            
            # 7. 保存和绘图
            if self.save_processed:
                self._save_and_plot(result_df)
                
            # 8. 构建结果
            latest = result_df.iloc[-1]
            current_val = latest['percentile']
            current_ratio = latest['crowdedness_ratio']
            
            message = f"大盘拥挤度分位数: {current_val:.2f}% (拥挤度: {current_ratio:.2%}, 日期: {latest['交易日期'].strftime('%Y-%m-%d')})"
            
            return self.create_result(
                risk_percentage=current_val,
                message=message,
                extra_data={
                    "percentile": float(current_val),
                    "crowdedness_ratio": float(current_ratio),
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
        output_file = PROCESSED_DATA_DIR / "market_crowdedness.csv"
        df.to_csv(output_file, index=False)
        
        # 绘图
        try:
            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), sharex=True)
            
            # 设置中文字体
            plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
            plt.rcParams['axes.unicode_minus'] = False
            
            # 绘制分位数趋势
            ax1.plot(df['交易日期'], df['percentile'], label=f'拥挤度分位数 (Rolling {MARKET_CROWDEDNESS_ROLLING_WINDOW_YEARS}年)', color='#d62728')
            ax1.axhline(y=80, color='red', linestyle='--', alpha=0.5, label='高风险(80%)')
            ax1.axhline(y=20, color='green', linestyle='--', alpha=0.5, label='低风险(20%)')
            ax1.set_title("全市场拥挤度分位数历史走势", fontsize=14)
            ax1.set_ylabel("分位数 (%)")
            ax1.grid(True, alpha=0.3)
            ax1.legend(loc='best')
            
            # 绘制拥挤度比例趋势
            ax2.plot(df['交易日期'], df['crowdedness_ratio'], label='拥挤度比例(Top10%成交额占比)', color='#9467bd')
            ax2.set_title("全市场拥挤度比例走势 (Top10%个股)", fontsize=14)
            ax2.set_xlabel("日期")
            ax2.set_ylabel("成交额占比")
            ax2.grid(True, alpha=0.3)
            ax2.legend(loc='best')
            
            # 格式化日期
            ax2.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
            ax2.xaxis.set_major_locator(mdates.YearLocator())
            plt.xticks(rotation=45)
            
            plt.tight_layout()
            
            pic_file = PIC_DIR / "market_crowdedness_trend.png"
            plt.savefig(pic_file, dpi=300)
            plt.close(fig)
            
        except Exception as e:
            print(f"绘图失败: {e}")

    def get_processed_data(self) -> Optional[pd.DataFrame]:
        """获取处理后的数据"""
        return self._processed_data



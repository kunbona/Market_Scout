"""
全市场换手率分位数指标

逻辑：
1. 读取XBX股票数据（xbx_stock_data.parquet）
2. 按日期分组，分别计算每日全市场总成交额和总流通市值
3. 计算每日全市场换手率 = 当日总成交额 / 当日总流通市值
4. 使用expanding窗口计算每日换手率在历史数据的分位数（Percentile）
5. 反映当前市场交易活跃度在历史上的相对位置，剔除市值增长（通胀）影响
"""

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from typing import Optional, Dict, Any
from pathlib import Path

from config import RAW_DATA_DIR, DATA_START_DATE, INDICATOR_START_DATE, PROCESSED_DATA_DIR, PIC_DIR
from .base_indicator import BaseIndicator
from .indicator_result import IndicatorResult
from .risk_level import RiskLevel, RiskThreshold

class MarketTurnoverPercentileIndicator(BaseIndicator):
    """全市场换手率分位数指标
    
    通过计算全市场每日换手率（Turnover Rate）在历史数据中的分位数，判断当前市场热度。
    换手率 = 全市场成交额 / 全市场流通市值
    
    优势：
    相比纯成交额指标，换手率剔除了市场扩容和市值增长（通胀）的影响，
    更能准确反映市场交易的活跃程度和拥挤程度。
    
    风险判断：
    分位数越高，表示市场换手越频繁，投机氛围越浓（可能接近顶部）；
    分位数越低，表示市场交易越冷清（可能接近底部）。
    """
    
    def __init__(
        self, 
        name: str = "market_turnover_percentile",
        save_processed: bool = True
    ):
        super().__init__(
            name=name,
            description="全市场换手率历史分位数，反映市场活跃度（已剔除市值增长影响）",
            # 使用更严格的风险阈值：5%以下为低风险（底部），95%以上为高风险（顶部）
            risk_threshold=RiskThreshold(low_max=5.0, medium_max=95.0)
        )
        self.save_processed = save_processed
        self._processed_data = None
    
    def _load_data(self) -> pd.DataFrame:
        """加载XBX股票数据"""
        file_path = RAW_DATA_DIR / "xbx_stock_data.parquet"
        if not file_path.exists():
            raise FileNotFoundError(f"数据文件不存在: {file_path}")
            
        # 读取需要的列：交易日期、成交额、流通市值
        try:
            df = pd.read_parquet(file_path, columns=["交易日期", "成交额", "流通市值"])
        except Exception as e:
            raise IOError(f"读取Parquet文件失败: {e}")
            
        return df
        
    def calculate(self, start_date: Optional[str] = None, **kwargs) -> IndicatorResult:
        """计算指标
        
        Args:
            start_date: 结果输出的开始日期，如果为None则使用config.INDICATOR_START_DATE
            
        Returns:
            IndicatorResult: 包含风险百分比（即换手率分位数 * 100）
        """
        # 1. 确定结果输出的开始日期
        if start_date is None:
            start_date = INDICATOR_START_DATE
            
        # 2. 加载数据
        # 优先使用传入的共享数据
        df = kwargs.get("stock_data")
        if df is None and "df" in kwargs:
            df = kwargs["df"]
            
        if df is None:
            df = self._load_data()
            
        if df.empty:
            return self.create_result(0, "数据为空")
            
        # 3. 计算每日全市场总成交额和总流通市值
        # 确保日期格式正确
        df["交易日期"] = pd.to_datetime(df["交易日期"])
        
        # 按日期分组求和
        daily_stats = df.groupby("交易日期")[["成交额", "流通市值"]].sum().sort_index()

        # 对成交额和流通市值进行滚动平滑
        daily_stats["成交额"] = daily_stats["成交额"].rolling(window=5).mean()
        daily_stats["流通市值"] = daily_stats["流通市值"].rolling(window=5).mean()
        
        # 4. 计算全市场换手率
        # 换手率 = 成交额 / 流通市值
        # 使用5日滚动平均平滑数据，减少毛刺
        daily_stats["turnover_rate"] = daily_stats["成交额"] / daily_stats["流通市值"]
        
        # 5. 计算历史分位数 (Rolling Percentile / Expanding Percentile)
        # 使用expanding窗口，即每一天都与之前所有历史数据比较
        # rank(pct=True) 计算分位数，范围 0-1
        # 注意：由于使用了rolling(5)，前4天会有NaN，expanding会自动处理
        percentile_series = daily_stats["turnover_rate"].expanding(min_periods=1).rank(pct=True)
        
        # 6. 过滤日期范围
        start_datetime = pd.to_datetime(start_date)
        # 筛选出需要的结果，注意daily_stats也需要切片以保持对齐
        result_percentile = percentile_series[percentile_series.index >= start_datetime]
        result_stats = daily_stats[daily_stats.index >= start_datetime]
        
        if result_percentile.empty:
            return self.create_result(0, f"在 {start_date} 之后无数据")
            
        # 7. 合并数据以便保存和绘图
        # 将Series和DataFrame合并
        result_df = pd.DataFrame({
            "交易日期": result_percentile.index,
            "turnover_amount": result_stats["成交额"],
            "market_value": result_stats["流通市值"],
            "turnover_rate": result_stats["turnover_rate"],
            "percentile": result_percentile * 100  # 转换为百分比
        })
        
        self._processed_data = result_df
        
        # 8. 保存和绘图
        if self.save_processed:
            self._save_and_plot(result_df)

        # 9. 获取最新值
        latest_date = result_percentile.index[-1]
        latest_percentile = result_percentile.iloc[-1]
        latest_turnover_rate = result_stats.iloc[-1]["turnover_rate"]
        
        # 风险值 = 分位数 * 100
        risk_percentage = latest_percentile * 100
        
        # 10. 构建结果
        # 准备图表数据（复用 result_df，重命名列以匹配旧格式，或者直接使用新格式）
        chart_data = pd.DataFrame({
            "date": result_percentile.index,
            "turnover_amount": result_stats["成交额"],
            "market_value": result_stats["流通市值"],
            "turnover_rate": result_stats["turnover_rate"],
            "percentile": result_percentile
        })
        
        return self.create_result(
            risk_percentage=risk_percentage,
            message=f"当前全市场换手率: {latest_turnover_rate:.2%} (分位数: {risk_percentage:.2f}%)",
            extra_data={
                "series": chart_data,
                "latest_date": latest_date,
                "latest_turnover_rate": latest_turnover_rate,
                "latest_turnover_amount": result_stats.iloc[-1]["成交额"],
                "latest_market_value": result_stats.iloc[-1]["流通市值"]
            }
        )

    def _save_and_plot(self, df: pd.DataFrame):
        """保存数据并绘制图表"""
        # 保存数据
        output_file = PROCESSED_DATA_DIR / "market_turnover_percentile.csv"
        df.to_csv(output_file, index=False)
        
        # 绘图
        try:
            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), sharex=True)
            
            # 设置中文字体
            plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
            plt.rcParams['axes.unicode_minus'] = False
            
            # 绘制分位数趋势
            ax1.plot(df['交易日期'], df['percentile'], label='换手率分位数', color='#ff7f0e')
            ax1.axhline(y=95, color='red', linestyle='--', alpha=0.5, label='高风险(95%)')
            ax1.axhline(y=5, color='green', linestyle='--', alpha=0.5, label='低风险(5%)')
            ax1.set_title("全市场换手率分位数历史走势", fontsize=14)
            ax1.set_ylabel("分位数 (%)")
            ax1.grid(True, alpha=0.3)
            ax1.legend(loc='best')
            
            # 绘制换手率数值趋势
            ax2.plot(df['交易日期'], df['turnover_rate'], label='全市场换手率', color='#1f77b4')
            ax2.set_title("全市场换手率走势", fontsize=14)
            ax2.set_xlabel("日期")
            ax2.set_ylabel("换手率")
            ax2.grid(True, alpha=0.3)
            ax2.legend(loc='best')
            
            # 格式化日期
            ax2.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
            ax2.xaxis.set_major_locator(mdates.YearLocator())
            plt.xticks(rotation=45)
            
            plt.tight_layout()
            
            pic_file = PIC_DIR / "market_turnover_percentile_trend.png"
            plt.savefig(pic_file, dpi=300)
            plt.close(fig)
            
        except Exception as e:
            print(f"绘图失败: {e}")

    def get_processed_data(self) -> Optional[pd.DataFrame]:
        """获取处理后的数据"""
        return self._processed_data

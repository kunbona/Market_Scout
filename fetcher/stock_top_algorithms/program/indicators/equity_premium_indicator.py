"""
股权溢价指数指标模块

计算HS300的股权溢价指数，公式为：1/PE - 中国10年期国债利率
并基于历史rolling的最大最小值计算相对进度作为风险百分比。
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any
import matplotlib
matplotlib.use('Agg')  # 使用非交互式后端
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from .base_indicator import BaseIndicator
from .indicator_result import IndicatorResult
from config import RAW_DATA_DIR, PROCESSED_DATA_DIR, PIC_DIR

try:
    # 尝试相对导入
    from ..api.stock_index_pe import fetch_stock_index_pe
    from ..api.bond_zh_us_rate import fetch_bond_zh_us_rate
except ImportError:
    # 如果相对导入失败，尝试绝对导入
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from api.stock_index_pe import fetch_stock_index_pe
    from api.bond_zh_us_rate import fetch_bond_zh_us_rate


class EquityPremiumIndicator(BaseIndicator):
    """股权溢价指数指标
    
    计算HS300的股权溢价指数：
    - 股权溢价指数 = 1/PE - 中国10年期国债利率
    - 当股权溢价指数 < 3%时，市场高估
    - 当股权溢价指数 > 6%时，市场低估
    - 风险百分比基于rolling历史最大最小值计算相对进度
    """
    
    def __init__(
        self,
        name: str = "equity_premium_indicator",
        description: Optional[str] = None,
        high_threshold: float = 0.06,  # 6%，超过此值市场低估
        low_threshold: float = 0.03,   # 3%，低于此值市场高估
        save_processed: bool = True,
        line_alpha: float = 0.8  # 指数和股权溢价指数线条的透明度
    ):
        """初始化股权溢价指数指标
        
        Args:
            name: 指标名称
            description: 指标描述
            high_threshold: 高阈值（百分比形式，如0.06表示6%），超过此值市场低估
            low_threshold: 低阈值（百分比形式，如0.03表示3%），低于此值市场高估
            save_processed: 是否保存处理后的数据到processed文件夹
            line_alpha: 指数和股权溢价指数线条的透明度（0.0-1.0），默认0.1
        """
        if description is None:
            description = (
                f"股权溢价指数指标：1/PE - 10年期国债利率。"
                f"<{low_threshold*100}%表示市场高估，>{high_threshold*100}%表示市场低估"
            )
        
        super().__init__(name=name, description=description)
        
        self.high_threshold = high_threshold
        self.low_threshold = low_threshold
        self.save_processed = save_processed
        self.line_alpha = max(0.0, min(1.0, line_alpha))  # 确保透明度在0-1范围内
        self._processed_data = None
    
    def _load_pe_data(self, symbol: str = "沪深300") -> pd.DataFrame:
        """加载股票指数PE数据
        
        Args:
            symbol: 指数名称，默认沪深300
            
        Returns:
            DataFrame包含日期、指数点位和滚动市盈率数据
        """
        # 先尝试从本地文件读取
        raw_file_path = RAW_DATA_DIR / f"stock_index_pe_{symbol}.csv"
        
        if raw_file_path.exists():
            df = pd.read_csv(raw_file_path, encoding='utf-8-sig')
        else:
            # 如果本地文件不存在，则从API获取
            result = fetch_stock_index_pe(symbol=symbol, save_raw=True)
            if not result['success']:
                raise ValueError(f"无法获取PE数据: {result.get('error', '未知错误')}")
            df = result['data']
        
        # 转换日期列为datetime
        if '日期' in df.columns:
            df['日期'] = pd.to_datetime(df['日期'])
            df = df.sort_values('日期').reset_index(drop=True)
        
        # 选择需要的列：日期、指数点位和滚动市盈率
        if '滚动市盈率' not in df.columns:
            raise ValueError("数据中缺少'滚动市盈率'列")
        
        if '指数' not in df.columns:
            raise ValueError("数据中缺少'指数'列")
        
        return df[['日期', '指数', '滚动市盈率']].copy()
    
    def _load_bond_rate_data(self, start_date: str = "20050101") -> pd.DataFrame:
        """加载中国10年期国债利率数据
        
        Args:
            start_date: 开始日期，格式YYYYMMDD
            
        Returns:
            DataFrame包含日期和中国国债收益率10年数据
        """
        # 先尝试从本地文件读取
        raw_file_path = RAW_DATA_DIR / f"bond_zh_us_rate_{start_date}.csv"
        
        if raw_file_path.exists():
            df = pd.read_csv(raw_file_path, encoding='utf-8-sig')
        else:
            # 如果本地文件不存在，则从API获取
            result = fetch_bond_zh_us_rate(start_date=start_date, save_raw=True)
            if not result['success']:
                raise ValueError(f"无法获取国债利率数据: {result.get('error', '未知错误')}")
            df = result['data']
        
        # 转换日期列为datetime
        if '日期' in df.columns:
            df['日期'] = pd.to_datetime(df['日期'])
            df = df.sort_values('日期').reset_index(drop=True)
        
        # 选择需要的列：日期和中国国债收益率10年
        if '中国国债收益率10年' not in df.columns:
            raise ValueError("数据中缺少'中国国债收益率10年'列")
        
        return df[['日期', '中国国债收益率10年']].copy()
    
    def _calculate_equity_premium(
        self,
        pe_df: pd.DataFrame,
        bond_df: pd.DataFrame
    ) -> pd.DataFrame:
        """计算股权溢价指数
        
        Args:
            pe_df: PE数据DataFrame，包含日期、指数点位和滚动市盈率
            bond_df: 国债利率数据DataFrame，包含日期和中国国债收益率10年
            
        Returns:
            包含日期、指数点位和股权溢价指数的DataFrame
        """
        # 合并两个数据集，以日期为键
        merged = pd.merge(
            pe_df,
            bond_df,
            on='日期',
            how='inner'  # 只保留两个数据集都有的日期
        )
        
        # 确保日期列是datetime类型
        merged['日期'] = pd.to_datetime(merged['日期'])
        merged = merged.sort_values('日期').reset_index(drop=True)
        
        # 对国债利率进行ffill填充（前向填充）
        merged['中国国债收益率10年'] = merged['中国国债收益率10年'].ffill()
        
        # 过滤掉仍为NaN的行（如果第一行就是NaN，无法填充）
        merged = merged.dropna(subset=['中国国债收益率10年', '滚动市盈率'])
        
        # 计算1/PE（转换为百分比，即除以100）
        # PE是倍数形式，1/PE就是收益率（小数形式），需要转换为百分比
        merged['earnings_yield'] = (1 / merged['滚动市盈率']) * 100  # 转换为百分比
        
        # 计算股权溢价指数 = 1/PE(%) - 10年期国债利率(%)
        # 注意：国债收益率已经是百分比形式（如3.5表示3.5%）
        merged['equity_premium'] = merged['earnings_yield'] - merged['中国国债收益率10年']
        
        # 计算rolling的历史最大值和最小值（保留用于额外数据）
        merged['rolling_max'] = merged['equity_premium'].expanding().max()
        merged['rolling_min'] = merged['equity_premium'].expanding().min()
        
        # 计算历史百分位作为风险百分比
        # 股权溢价指数越小，市场风险越高
        # 百分位表示有多少比例的历史数据小于等于当前值
        # 如果百分位低（接近0%），说明当前值接近历史最小值，风险应该高（接近100%）
        # 如果百分位高（接近100%），说明当前值接近历史最大值，风险应该低（接近0%）
        # 公式：risk_percentage = 100 - percentile
        # 这样，如果当前值在第10百分位，风险百分比 = 100 - 10 = 90%（高风险）
        # 如果当前值在第90百分位，风险百分比 = 100 - 90 = 10%（低风险）
        
        def calculate_rolling_percentile(row_index, series):
            """计算到当前行为止的历史百分位"""
            if row_index == 0:
                # 第一行数据，无法计算百分位，设为中等风险
                return 50.0
            
            # 获取从开始到当前行的所有历史数据
            historical_data = series.iloc[:row_index + 1]
            current_value = series.iloc[row_index]
            
            # 计算有多少比例的历史数据小于等于当前值
            # 使用rank方法，method='min'确保相同值使用最小排名
            rank = historical_data.rank(method='min').iloc[row_index]
            total_count = len(historical_data)
            percentile = (rank - 1) / (total_count - 1) * 100 if total_count > 1 else 50.0
            
            # 风险百分比 = 100 - 百分位
            # 百分位越低（接近0%）→ 风险百分比越高（接近100%）
            risk_percentage = 100.0 - percentile
            return risk_percentage
        
        # 为每一行计算rolling百分位（使用expanding窗口）
        # 对于每一行，计算从开始到当前行的历史百分位
        merged['risk_percentage'] = [
            calculate_rolling_percentile(i, merged['equity_premium']) 
            for i in range(len(merged))
        ]
        
        # 确定风险等级和市场状态
        def get_market_status(row):
            """根据股权溢价指数判断市场状态"""
            if pd.isna(row['equity_premium']):
                return '未知'
            elif row['equity_premium'] < self.low_threshold * 100:  # 注意：阈值是百分比形式
                return '高估'
            elif row['equity_premium'] > self.high_threshold * 100:
                return '低估'
            else:
                return '正常'
        
        merged['market_status'] = merged.apply(get_market_status, axis=1)
        
        # 选择需要的列（包含指数点位）
        result_df = merged[[
            '日期',
            '指数',
            '滚动市盈率',
            '中国国债收益率10年',
            'earnings_yield',
            'equity_premium',
            'rolling_max',
            'rolling_min',
            'risk_percentage',
            'market_status'
        ]].copy()
        
        return result_df
    
    def calculate(
        self,
        symbol: str = "沪深300",
        bond_start_date: str = "20050101",
        start_date: str = "2010-06-01",
        **kwargs
    ) -> IndicatorResult:
        """计算股权溢价指数风险值
        
        Args:
            symbol: 股票指数名称，默认"沪深300"
            bond_start_date: 国债数据开始日期，格式YYYYMMDD
            start_date: 计算开始日期，格式YYYY-MM-DD，默认"2010-06-01"。仅保留此日期之后的数据进行计算和绘图
            **kwargs: 其他参数（未使用）
            
        Returns:
            IndicatorResult: 包含风险百分比、风险等级等信息的计算结果
        """
        try:
            # 加载PE数据
            pe_df = self._load_pe_data(symbol=symbol)
            
            # 加载国债利率数据
            bond_df = self._load_bond_rate_data(start_date=bond_start_date)
            
            # 计算股权溢价指数
            result_df = self._calculate_equity_premium(pe_df, bond_df)
            
            # 按开始日期过滤数据
            if start_date:
                start_datetime = pd.to_datetime(start_date)
                result_df = result_df[result_df['日期'] >= start_datetime].copy()
                result_df = result_df.reset_index(drop=True)
            
            # 保存处理后的数据
            if self.save_processed:
                output_file = PROCESSED_DATA_DIR / f"equity_premium_{symbol}.csv"
                result_df.to_csv(output_file, index=False, encoding='utf-8-sig')
            
            # 保存到实例变量，供后续使用
            self._processed_data = result_df
            
            # 获取最新一行的数据
            if len(result_df) == 0:
                raise ValueError("计算结果为空，无法计算风险值")
            
            latest = result_df.iloc[-1]
            current_equity_premium = latest['equity_premium']
            current_risk_percentage = latest['risk_percentage']
            market_status = latest['market_status']
            
            # 生成提示信息
            if market_status == '高估':
                message = f"股权溢价指数为{current_equity_premium:.2f}%，低于{self.low_threshold*100}%，市场处于高估状态"
            elif market_status == '低估':
                message = f"股权溢价指数为{current_equity_premium:.2f}%，高于{self.high_threshold*100}%，市场处于低估状态"
            else:
                message = f"股权溢价指数为{current_equity_premium:.2f}%，市场处于正常状态"
            
            # 创建额外数据
            extra_data = {
                'current_equity_premium': float(current_equity_premium),
                'current_index': float(latest['指数']),  # 当前指数点位
                'current_pe': float(latest['滚动市盈率']),
                'current_bond_rate': float(latest['中国国债收益率10年']),
                'current_earnings_yield': float(latest['earnings_yield']),
                'rolling_max': float(latest['rolling_max']),
                'rolling_min': float(latest['rolling_min']),
                'market_status': market_status,
                'high_threshold': self.high_threshold * 100,
                'low_threshold': self.low_threshold * 100,
                'data_points': len(result_df),
                'start_date': result_df['日期'].min().strftime('%Y-%m-%d'),  # 实际使用的开始日期（过滤后）
                'end_date': result_df['日期'].max().strftime('%Y-%m-%d'),
                'filtered_start_date': start_date  # 用户指定的过滤开始日期
            }
            
            # 确保风险百分比在0-100范围内
            risk_percentage = max(0.0, min(100.0, float(current_risk_percentage)))
            
            result = self.create_result(
                risk_percentage=risk_percentage,
                message=message,
                extra_data=extra_data,
                timestamp=latest['日期'].to_pydatetime() if hasattr(latest['日期'], 'to_pydatetime') else datetime.now()
            )
            
            # 自动绘制并保存图表
            if self.save_processed:
                try:
                    self._plot_equity_premium(result_df, symbol=symbol)
                except Exception as plot_error:
                    # 绘图失败不影响计算结果，只记录警告
                    import warnings
                    warnings.warn(f"绘图失败: {str(plot_error)}")
            
            return result
            
        except Exception as e:
            raise ValueError(f"计算股权溢价指数失败: {str(e)}")
    
    def _plot_equity_premium(
        self,
        data_df: pd.DataFrame,
        symbol: str = "沪深300"
    ) -> None:
        """绘制股权溢价指数可视化图表
        
        使用上下两个子图分开显示：
        - 上图：指数点位，根据市场状态（高估/低估/正常）进行颜色填充
        - 下图：股权溢价指数，显示阈值线
        
        Args:
            data_df: 包含股权溢价指数数据的DataFrame
            symbol: 股票指数名称，用于文件名
        """
        if data_df is None or len(data_df) == 0:
            return
        
        # 确保日期列是datetime类型
        data_df = data_df.copy()
        if not pd.api.types.is_datetime64_any_dtype(data_df['日期']):
            data_df['日期'] = pd.to_datetime(data_df['日期'])
        
        # 按日期排序
        data_df = data_df.sort_values('日期').reset_index(drop=True)
        
        # 创建上下两个子图
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 12), sharex=True)
        
        # 设置中文字体
        plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
        plt.rcParams['axes.unicode_minus'] = False
        
        # ==================== 上图：指数点位 ====================
        # 获取指数点位的最小值和最大值（用于填充区域）
        index_min = data_df['指数'].min()
        index_max = data_df['指数'].max()
        index_range = index_max - index_min
        fill_bottom = index_min - index_range * 0.02  # 填充区域底部
        
        # 根据market_status分组，为每个状态的时间段填充对应颜色
        dates = data_df['日期'].values
        index_values = data_df['指数'].values
        status_values = data_df['market_status'].values
        
        # 找出每个状态的连续区间
        current_status = None
        start_idx = 0
        
        for i in range(len(data_df)):
            status = status_values[i]
            
            # 如果状态发生变化，填充之前的区间
            if status != current_status and current_status is not None:
                end_idx = i - 1
                # 填充这个区间的颜色
                if end_idx >= start_idx:
                    self._fill_status_region(
                        ax1, dates, index_values, start_idx, end_idx,
                        current_status, fill_bottom
                    )
                start_idx = i
            
            current_status = status
        
        # 填充最后一个区间
        if current_status is not None:
            end_idx = len(data_df) - 1
            if end_idx >= start_idx:
                self._fill_status_region(
                    ax1, dates, index_values, start_idx, end_idx,
                    current_status, fill_bottom
                )
        
        # 绘制指数点位折线
        line1 = ax1.plot(
            data_df['日期'],
            data_df['指数'],
            color='#555555',  # 深灰色
            linewidth=2.5,
            alpha=self.line_alpha,
            label=f'{symbol}指数点位',
            zorder=5
        )
        
        # 设置上图标签和颜色
        ax1.set_ylabel(f'{symbol}指数点位', fontsize=12, fontweight='bold', color='#555555')
        ax1.tick_params(axis='y', labelcolor='#555555')
        ax1.grid(True, alpha=0.3, linestyle='--', zorder=1)
        
        # 上图图例
        from matplotlib.patches import Patch
        legend_elements_top = [
            line1[0],
            Patch(facecolor='red', alpha=0.3, label='高估区域'),
            Patch(facecolor='green', alpha=0.3, label='低估区域')
        ]
        ax1.legend(handles=legend_elements_top, loc='upper left', fontsize=10, framealpha=0.9)
        
        # ==================== 下图：股权溢价指数 ====================
        # 绘制股权溢价指数折线
        line2 = ax2.plot(
            data_df['日期'],
            data_df['equity_premium'],
            color='#888888',  # 浅灰色
            linewidth=2,
            alpha=self.line_alpha,
            label='股权溢价指数',
            zorder=4
        )
        
        # 设置下图标签和颜色
        ax2.set_xlabel('日期', fontsize=12, fontweight='bold')
        ax2.set_ylabel('股权溢价指数 (%)', fontsize=12, fontweight='bold', color='#888888')
        ax2.tick_params(axis='y', labelcolor='#888888')
        ax2.grid(True, alpha=0.3, linestyle='--', zorder=1)
        
        # 绘制股权溢价指数的阈值线
        low_threshold_pct = self.low_threshold * 100
        high_threshold_pct = self.high_threshold * 100
        ax2.axhline(y=low_threshold_pct, color='red', linestyle='--', linewidth=1.5, alpha=0.7, zorder=3, label=f'高估阈值 ({low_threshold_pct:.1f}%)')
        ax2.axhline(y=high_threshold_pct, color='green', linestyle='--', linewidth=1.5, alpha=0.7, zorder=3, label=f'低估阈值 ({high_threshold_pct:.1f}%)')
        
        # 下图图例
        legend_elements_bottom = [
            line2[0],
            plt.Line2D([0], [0], color='red', linestyle='--', linewidth=1.5, alpha=0.7, label=f'高估阈值 ({low_threshold_pct:.1f}%)'),
            plt.Line2D([0], [0], color='green', linestyle='--', linewidth=1.5, alpha=0.7, label=f'低估阈值 ({high_threshold_pct:.1f}%)')
        ]
        ax2.legend(handles=legend_elements_bottom, loc='upper left', fontsize=10, framealpha=0.9)
        
        # 格式化日期轴（只对下图设置，因为sharex=True会自动同步）
        ax2.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        ax2.xaxis.set_major_locator(mdates.YearLocator())
        
        # 如果数据跨度小于2年，使用月份格式
        if (data_df['日期'].max() - data_df['日期'].min()).days < 730:
            ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
            ax2.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        
        # 旋转日期标签
        plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45, ha='right')
        
        # 设置整体标题
        latest_equity_premium = data_df['equity_premium'].iloc[-1]
        latest_index = data_df['指数'].iloc[-1]
        latest_status = data_df['market_status'].iloc[-1]
        
        title = (
            f'{symbol}股权溢价指数分析图\n'
            f'当前股权溢价: {latest_equity_premium:.2f}% | '
            f'当前指数: {latest_index:.2f} | '
            f'市场状态: {latest_status}'
        )
        fig.suptitle(title, fontsize=14, fontweight='bold', y=0.995)
        
        # 调整布局
        plt.tight_layout(rect=[0, 0, 1, 0.96])  # 为标题留出空间
        
        # 保存图片
        pic_file = PIC_DIR / f"equity_premium_{symbol}.png"
        plt.savefig(pic_file, dpi=300, bbox_inches='tight', facecolor='white')
        plt.close(fig)  # 关闭图形以释放内存
    
    def _fill_status_region(
        self,
        ax,
        dates,
        index_values,
        start_idx: int,
        end_idx: int,
        status: str,
        fill_bottom: float
    ) -> None:
        """填充指定状态的时间段区域
        
        Args:
            ax: matplotlib坐标轴
            dates: 日期数组
            index_values: 指数点位数组
            start_idx: 开始索引
            end_idx: 结束索引
            status: 市场状态（'高估'/'低估'/'正常'）
            fill_bottom: 填充区域底部位置
        """
        # 正常状态不填充颜色（透明）
        if status == '正常':
            return
        
        # 确定颜色
        if status == '高估':
            color = 'red'
            alpha = 0.5
        elif status == '低估':
            color = 'green'
            alpha = 0.5
        else:
            color = 'gray'
            alpha = 0.5
        
        # 提取这个区间的日期和指数点位
        region_dates = dates[start_idx:end_idx+1]
        region_index = index_values[start_idx:end_idx+1]
        
        # 填充指数点位下方的区域
        ax.fill_between(
            region_dates,
            fill_bottom,
            region_index,
            color=color,
            alpha=alpha,
            zorder=2
        )
    
    def get_processed_data(self) -> Optional[pd.DataFrame]:
        """获取处理后的数据
        
        Returns:
            处理后的股权溢价指数数据DataFrame，如果尚未计算则返回None
        """
        return self._processed_data



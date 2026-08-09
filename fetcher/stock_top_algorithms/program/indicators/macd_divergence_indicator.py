"""
MACD顶底背离指标模块

计算指数的MACD顶底背离，支持日线和周线两种周期。
顶背离：价格创新高，但MACD未创新高（或MACD下降）
底背离：价格创新低，但MACD未创新低（或MACD上升）
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, Tuple, List
import matplotlib
matplotlib.use('Agg')  # 使用非交互式后端
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from .base_indicator import BaseIndicator
from .indicator_result import IndicatorResult
from config import RAW_DATA_DIR, PROCESSED_DATA_DIR, PIC_DIR, INDICATOR_START_DATE, MACD_DIVERGENCE_ONLY_VOLUME

try:
    # 尝试相对导入
    from ..api.stock_zh_index_daily import fetch_stock_zh_index_daily
except ImportError:
    # 如果相对导入失败，尝试绝对导入
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from api.stock_zh_index_daily import fetch_stock_zh_index_daily


class MACDDivergenceIndicator(BaseIndicator):
    """MACD顶底背离指标
    
    计算指数的MACD顶底背离：
    - 支持日线和周线两种周期
    - 顶背离：价格创新高，但MACD未创新高
    - 底背离：价格创新低，但MACD未创新低
    """
    
    def __init__(
        self,
        name: str = "macd_divergence_indicator",
        description: Optional[str] = None,
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9,
        save_processed: bool = True,
        volume_threshold: float = 0.30
    ):
        """初始化MACD顶底背离指标
        
        Args:
            name: 指标名称
            description: 指标描述
            fast_period: MACD快线周期，默认12
            slow_period: MACD慢线周期，默认26
            signal_period: MACD信号线周期，默认9
            save_processed: 是否保存处理后的数据到processed文件夹
            volume_threshold: 放量阈值（系数），默认0.20（20%），背离当天的成交量需比过去5天平均值大这个比例才认为是真的背离
        """
        if description is None:
            description = (
                f"MACD顶底背离指标：检测价格与MACD的背离情况。"
                f"顶背离表示价格创新高但MACD未创新高，底背离表示价格创新低但MACD未创新低。"
                f"放量背离（成交量比过去5天平均值大{volume_threshold*100}%）被认为是真的背离信号。"
            )
        
        super().__init__(name=name, description=description)
        
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.signal_period = signal_period
        self.save_processed = save_processed
        self.volume_threshold = volume_threshold
        self._processed_data = None
    
    def _load_index_data(self, symbol: str = "sh000001") -> pd.DataFrame:
        """加载指数数据
        
        Args:
            symbol: 指数代码，如"sh000001"（上证指数）、"sh000300"（沪深300）等
            
        Returns:
            DataFrame包含日期和收盘价数据
        """
        # 先尝试从本地文件读取
        raw_file_path = RAW_DATA_DIR / f"stock_zh_index_daily_{symbol}.csv"
        
        if raw_file_path.exists():
            df = pd.read_csv(raw_file_path, encoding='utf-8-sig')
        else:
            # 如果本地文件不存在，则从API获取
            result = fetch_stock_zh_index_daily(symbol=symbol, save_raw=True)
            if not result['success']:
                raise ValueError(f"无法获取指数数据: {result.get('error', '未知错误')}")
            df = result['data']
        
        # 转换日期列为datetime
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'])
            df = df.sort_values('date').reset_index(drop=True)
        else:
            raise ValueError("数据中缺少'date'列")
        
        # 选择需要的列：日期、收盘价和成交量
        if 'close' not in df.columns:
            raise ValueError("数据中缺少'close'列")
        if 'volume' not in df.columns:
            raise ValueError("数据中缺少'volume'列")
        
        return df[['date', 'close', 'volume']].copy()
    
    def _resample_to_weekly(self, daily_df: pd.DataFrame) -> pd.DataFrame:
        """将日线数据重采样为周线
        
        Args:
            daily_df: 日线数据DataFrame，包含date、close和volume列
            
        Returns:
            周线数据DataFrame，包含date、close和volume列
        """
        # 确保日期列是datetime类型并设置为索引
        weekly_df = daily_df.set_index('date').copy()
        
        # 使用resample将日线转为周线（周一到周五为一周）
        # 收盘价使用last()获取每周最后一个交易日的收盘价
        # 成交量使用sum()求和
        weekly_df = weekly_df.resample('W').agg({
            'close': 'last',
            'volume': 'sum'
        })
        
        # 重置索引，将date作为列
        weekly_df = weekly_df.reset_index()
        
        # 删除空值（如果某周没有交易日）
        weekly_df = weekly_df.dropna()
        
        return weekly_df[['date', 'close', 'volume']].copy()
    
    def _calculate_macd(
        self,
        df: pd.DataFrame,
        price_col: str = 'close'
    ) -> pd.DataFrame:
        """计算MACD指标
        
        Args:
            df: 包含价格数据的DataFrame
            price_col: 价格列名，默认'close'
            
        Returns:
            添加了MACD相关列的DataFrame
        """
        result_df = df.copy()
        
        # 计算EMA快线和慢线
        ema_fast = result_df[price_col].ewm(span=self.fast_period, adjust=False).mean()
        ema_slow = result_df[price_col].ewm(span=self.slow_period, adjust=False).mean()
        
        # 计算MACD线（DIF）
        result_df['macd'] = ema_fast - ema_slow
        
        # 计算信号线（DEA）
        result_df['signal'] = result_df['macd'].ewm(span=self.signal_period, adjust=False).mean()
        
        # 计算柱状图（MACD - Signal）
        result_df['histogram'] = result_df['macd'] - result_df['signal']
        
        return result_df
    
    def _detect_divergence(
        self,
        df: pd.DataFrame,
        price_col: str = 'close',
        lookback_period: int = 20,
        volume_col: str = 'volume',
        volume_avg_period: int = 5,
        volume_threshold: float = 0.30
    ) -> pd.DataFrame:
        """检测顶底背离
        
        这是共用的背离检测函数，可以用于日线和周线数据。
        
        顶背离：价格创新高，但MACD未创新高
        底背离：价格创新低，但MACD未创新低
        
        真的背离信号：背离当天的成交量比过去N天平均值大指定比例
        
        Args:
            df: 包含价格、MACD和成交量数据的DataFrame
            price_col: 价格列名，默认'close'
            lookback_period: 回看周期，用于寻找局部最高/最低点，默认20
            volume_col: 成交量列名，默认'volume'
            volume_avg_period: 计算成交量平均值的周期，默认5
            volume_threshold: 放量阈值（系数），默认0.30（30%）
            
        Returns:
            添加了背离检测列的DataFrame
        """
        result_df = df.copy()
        
        # 初始化背离列
        result_df['top_divergence'] = False  # 顶背离
        result_df['bottom_divergence'] = False  # 底背离
        result_df['real_top_divergence'] = False  # 真的顶背离（放量）
        result_df['real_bottom_divergence'] = False  # 真的底背离（放量）
        result_df['price_peak'] = False  # 价格高点
        result_df['price_trough'] = False  # 价格低点
        result_df['macd_peak'] = False  # MACD高点
        result_df['macd_trough'] = False  # MACD低点
        
        # 计算成交量的移动平均值（用于判断放量）
        if volume_col in result_df.columns:
            result_df['volume_avg'] = result_df[volume_col].rolling(window=volume_avg_period, min_periods=1).mean()
        else:
            # 如果没有成交量列，设置默认值
            result_df['volume_avg'] = 0
        
        # 重置索引，确保使用位置索引
        result_df = result_df.reset_index(drop=True)
        
        # 寻找局部最高点和最低点
        for i in range(lookback_period, len(result_df)):
            # 获取回看窗口
            window_start = max(0, i - lookback_period)
            window_end = i + 1
            
            price_window = result_df[price_col].iloc[window_start:window_end]
            macd_window = result_df['macd'].iloc[window_start:window_end]
            
            # 找到价格窗口内的最高点和最低点位置（使用位置索引）
            price_max_pos_in_window = price_window.values.argmax() + window_start
            price_min_pos_in_window = price_window.values.argmin() + window_start
            
            # 找到MACD窗口内的最高点和最低点位置（使用位置索引）
            macd_max_pos_in_window = macd_window.values.argmax() + window_start
            macd_min_pos_in_window = macd_window.values.argmin() + window_start
            
            # 检查当前点是否是价格高点
            if price_max_pos_in_window == i:
                result_df.loc[i, 'price_peak'] = True
                
                # 检查顶背离：当前价格是高点，但MACD不是高点
                if macd_max_pos_in_window != i:
                    # 进一步检查：当前MACD是否低于之前的MACD高点
                    if i > 0:
                        prev_macd_values = result_df['macd'].iloc[:i]
                        if len(prev_macd_values) > 0:
                            prev_macd_max = prev_macd_values.max()
                            if result_df.loc[i, 'macd'] < prev_macd_max:
                                result_df.loc[i, 'top_divergence'] = True
                                
                                # 检查是否为真的顶背离（放量）
                                if i >= volume_avg_period - 1:
                                    current_volume = result_df.loc[i, volume_col] if volume_col in result_df.columns else 0
                                    avg_volume = result_df.loc[i, 'volume_avg']
                                    if avg_volume > 0 and current_volume >= avg_volume * (1 + volume_threshold):
                                        result_df.loc[i, 'real_top_divergence'] = True
            
            # 检查当前点是否是价格低点
            if price_min_pos_in_window == i:
                result_df.loc[i, 'price_trough'] = True
                
                # 检查底背离：当前价格是低点，但MACD不是低点
                if macd_min_pos_in_window != i:
                    # 进一步检查：当前MACD是否高于之前的MACD低点
                    if i > 0:
                        prev_macd_values = result_df['macd'].iloc[:i]
                        if len(prev_macd_values) > 0:
                            prev_macd_min = prev_macd_values.min()
                            if result_df.loc[i, 'macd'] > prev_macd_min:
                                result_df.loc[i, 'bottom_divergence'] = True
                                
                                # 检查是否为真的底背离（放量）
                                if i >= volume_avg_period - 1:
                                    current_volume = result_df.loc[i, volume_col] if volume_col in result_df.columns else 0
                                    avg_volume = result_df.loc[i, 'volume_avg']
                                    if avg_volume > 0 and current_volume >= avg_volume * (1 + volume_threshold):
                                        result_df.loc[i, 'real_bottom_divergence'] = True
            
            # 标记MACD高点
            if macd_max_pos_in_window == i:
                result_df.loc[i, 'macd_peak'] = True
            
            # 标记MACD低点
            if macd_min_pos_in_window == i:
                result_df.loc[i, 'macd_trough'] = True
        
        return result_df
    
    def _calculate_divergence_for_period(
        self,
        df: pd.DataFrame,
        period_name: str = "daily",
        lookback_period: int = 20,
        volume_avg_period: int = 5,
        volume_threshold: float = 0.30
    ) -> pd.DataFrame:
        """为指定周期计算背离
        
        共用函数，用于计算日线或周线的背离。
        
        Args:
            df: 包含价格和成交量数据的DataFrame
            period_name: 周期名称，如"daily"或"weekly"
            lookback_period: 回看周期，默认20
            volume_avg_period: 计算成交量平均值的周期，默认5
            volume_threshold: 放量阈值（系数），默认0.30（30%）
            
        Returns:
            包含MACD和背离检测结果的DataFrame
        """
        # 计算MACD
        df_with_macd = self._calculate_macd(df, price_col='close')
        
        # 检测背离（包含放量判断）
        df_with_divergence = self._detect_divergence(
            df_with_macd,
            price_col='close',
            lookback_period=lookback_period,
            volume_col='volume',
            volume_avg_period=volume_avg_period,
            volume_threshold=volume_threshold
        )
        
        # 添加周期标识
        df_with_divergence['period'] = period_name
        
        return df_with_divergence
    
    def calculate(
        self,
        symbol: str = "sh000001",
        lookback_period_daily: int = 20,
        lookback_period_weekly: int = 10,
        start_date: Optional[str] = None,
        **kwargs
    ) -> IndicatorResult:
        """计算MACD顶底背离风险值
        
        Args:
            symbol: 指数代码，如"sh000001"（上证指数）、"sh000300"（沪深300）等
            lookback_period_daily: 日线回看周期，默认20
            lookback_period_weekly: 周线回看周期，默认10
            start_date: 计算开始日期，格式YYYY-MM-DD，如果为None则使用config.py中的INDICATOR_START_DATE
            **kwargs: 其他参数（未使用）
            
        Returns:
            IndicatorResult: 包含风险百分比、背离信息等信息的计算结果
        """
        try:
            # 使用config中的开始日期作为默认值
            if start_date is None:
                start_date = INDICATOR_START_DATE
            
            # 加载指数数据
            daily_df = self._load_index_data(symbol=symbol)
            
            # 按开始日期过滤数据
            start_datetime = pd.to_datetime(start_date)
            daily_df = daily_df[daily_df['date'] >= start_datetime].copy()
            daily_df = daily_df.reset_index(drop=True)
            
            if len(daily_df) == 0:
                raise ValueError("数据为空，无法计算背离")
            
            # 计算日线背离
            daily_result = self._calculate_divergence_for_period(
                daily_df,
                period_name="daily",
                lookback_period=lookback_period_daily,
                volume_avg_period=5,
                volume_threshold=self.volume_threshold
            )
            
            # 将日线数据重采样为周线
            weekly_df = self._resample_to_weekly(daily_df)
            
            # 计算周线背离
            weekly_result = self._calculate_divergence_for_period(
                weekly_df,
                period_name="weekly",
                lookback_period=lookback_period_weekly,
                volume_avg_period=5,
                volume_threshold=self.volume_threshold
            )
            
            # 合并结果
            daily_result['date'] = pd.to_datetime(daily_result['date'])
            weekly_result['date'] = pd.to_datetime(weekly_result['date'])
            
            # 根据配置过滤普通背离（如果只显示放量背离）
            if MACD_DIVERGENCE_ONLY_VOLUME:
                # 只保留放量背离（真的背离），将普通背离标记为False
                daily_result.loc[~daily_result['real_top_divergence'], 'top_divergence'] = False
                daily_result.loc[~daily_result['real_bottom_divergence'], 'bottom_divergence'] = False
                weekly_result.loc[~weekly_result['real_top_divergence'], 'top_divergence'] = False
                weekly_result.loc[~weekly_result['real_bottom_divergence'], 'bottom_divergence'] = False
            
            # 保存处理后的数据
            if self.save_processed:
                # 保存日线数据
                daily_output_file = PROCESSED_DATA_DIR / f"macd_divergence_{symbol}_daily.csv"
                daily_result.to_csv(daily_output_file, index=False, encoding='utf-8-sig')
                
                # 保存周线数据
                weekly_output_file = PROCESSED_DATA_DIR / f"macd_divergence_{symbol}_weekly.csv"
                weekly_result.to_csv(weekly_output_file, index=False, encoding='utf-8-sig')
            
            # 保存到实例变量，供后续使用
            self._processed_data = {
                'daily': daily_result,
                'weekly': weekly_result
            }
            
            # 获取最新的背离信息
            latest_daily = daily_result.iloc[-1]
            latest_weekly = weekly_result.iloc[-1]
            
            # 统计最近的背离情况
            recent_days = 60  # 最近60个交易日
            recent_weeks = 12  # 最近12周
            
            daily_recent = daily_result.tail(recent_days)
            weekly_recent = weekly_result.tail(recent_weeks)
            
            daily_top_divergence_count = daily_recent['top_divergence'].sum()
            daily_bottom_divergence_count = daily_recent['bottom_divergence'].sum()
            daily_real_top_divergence_count = daily_recent['real_top_divergence'].sum()
            daily_real_bottom_divergence_count = daily_recent['real_bottom_divergence'].sum()
            
            weekly_top_divergence_count = weekly_recent['top_divergence'].sum()
            weekly_bottom_divergence_count = weekly_recent['bottom_divergence'].sum()
            weekly_real_top_divergence_count = weekly_recent['real_top_divergence'].sum()
            weekly_real_bottom_divergence_count = weekly_recent['real_bottom_divergence'].sum()
            
            # 生成提示信息
            messages = []
            
            if latest_daily['real_top_divergence']:
                messages.append(f"⚠️ 日线检测到【真的顶背离】（放量背离，需重点关注）")
            elif not MACD_DIVERGENCE_ONLY_VOLUME and latest_daily['top_divergence']:
                messages.append(f"日线检测到顶背离（价格创新高但MACD未创新高）")
            
            if latest_daily['real_bottom_divergence']:
                messages.append(f"⚠️ 日线检测到【真的底背离】（放量背离，需重点关注）")
            elif not MACD_DIVERGENCE_ONLY_VOLUME and latest_daily['bottom_divergence']:
                messages.append(f"日线检测到底背离（价格创新低但MACD未创新低）")
            
            if latest_weekly['real_top_divergence']:
                messages.append(f"⚠️ 周线检测到【真的顶背离】（放量背离，需重点关注）")
            elif not MACD_DIVERGENCE_ONLY_VOLUME and latest_weekly['top_divergence']:
                messages.append(f"周线检测到顶背离（价格创新高但MACD未创新高）")
            
            if latest_weekly['real_bottom_divergence']:
                messages.append(f"⚠️ 周线检测到【真的底背离】（放量背离，需重点关注）")
            elif not MACD_DIVERGENCE_ONLY_VOLUME and latest_weekly['bottom_divergence']:
                messages.append(f"周线检测到底背离（价格创新低但MACD未创新低）")
            
            if not messages:
                messages.append("当前未检测到明显的顶底背离信号")
            
            message = " | ".join(messages)
            
            # 创建额外数据
            extra_data = {
                'symbol': symbol,
                'daily': {
                    'current_price': float(latest_daily['close']),
                    'current_macd': float(latest_daily['macd']),
                    'current_signal': float(latest_daily['signal']),
                    'current_histogram': float(latest_daily['histogram']),
                    'has_top_divergence': bool(latest_daily['top_divergence']),
                    'has_bottom_divergence': bool(latest_daily['bottom_divergence']),
                    'has_real_top_divergence': bool(latest_daily['real_top_divergence']),
                    'has_real_bottom_divergence': bool(latest_daily['real_bottom_divergence']),
                    'recent_top_divergence_count': int(daily_top_divergence_count),
                    'recent_bottom_divergence_count': int(daily_bottom_divergence_count),
                    'recent_real_top_divergence_count': int(daily_real_top_divergence_count),
                    'recent_real_bottom_divergence_count': int(daily_real_bottom_divergence_count),
                    'data_points': len(daily_result),
                    'start_date': daily_result['date'].min().strftime('%Y-%m-%d'),
                    'end_date': daily_result['date'].max().strftime('%Y-%m-%d')
                },
                'weekly': {
                    'current_price': float(latest_weekly['close']),
                    'current_macd': float(latest_weekly['macd']),
                    'current_signal': float(latest_weekly['signal']),
                    'current_histogram': float(latest_weekly['histogram']),
                    'has_top_divergence': bool(latest_weekly['top_divergence']),
                    'has_bottom_divergence': bool(latest_weekly['bottom_divergence']),
                    'has_real_top_divergence': bool(latest_weekly['real_top_divergence']),
                    'has_real_bottom_divergence': bool(latest_weekly['real_bottom_divergence']),
                    'recent_top_divergence_count': int(weekly_top_divergence_count),
                    'recent_bottom_divergence_count': int(weekly_bottom_divergence_count),
                    'recent_real_top_divergence_count': int(weekly_real_top_divergence_count),
                    'recent_real_bottom_divergence_count': int(weekly_real_bottom_divergence_count),
                    'data_points': len(weekly_result),
                    'start_date': weekly_result['date'].min().strftime('%Y-%m-%d'),
                    'end_date': weekly_result['date'].max().strftime('%Y-%m-%d')
                },
                'volume_threshold': self.volume_threshold,
                'macd_params': {
                    'fast_period': self.fast_period,
                    'slow_period': self.slow_period,
                    'signal_period': self.signal_period
                },
                'lookback_periods': {
                    'daily': lookback_period_daily,
                    'weekly': lookback_period_weekly
                }
            }
            
            # 暂时使用简单的风险百分比计算（后续可以优化）
            # 顶背离增加风险，底背离降低风险
            risk_percentage = 50.0  # 默认中等风险
            
            if latest_daily['top_divergence'] or latest_weekly['top_divergence']:
                risk_percentage = min(100.0, risk_percentage + 20.0)
            if latest_daily['bottom_divergence'] or latest_weekly['bottom_divergence']:
                risk_percentage = max(0.0, risk_percentage - 20.0)
            
            # 确保风险百分比在0-100范围内
            risk_percentage = max(0.0, min(100.0, risk_percentage))
            
            result = self.create_result(
                risk_percentage=risk_percentage,
                message=message,
                extra_data=extra_data,
                timestamp=latest_daily['date'].to_pydatetime() if hasattr(latest_daily['date'], 'to_pydatetime') else datetime.now()
            )
            
            # 自动绘制并保存图表
            if self.save_processed:
                try:
                    self._plot_macd_divergence(
                        daily_result,
                        weekly_result,
                        symbol=symbol
                    )
                except Exception as plot_error:
                    # 绘图失败不影响计算结果，只记录警告
                    import warnings
                    warnings.warn(f"绘图失败: {str(plot_error)}")
            
            return result
            
        except Exception as e:
            raise ValueError(f"计算MACD顶底背离失败: {str(e)}")
    
    def _plot_macd_divergence(
        self,
        daily_df: pd.DataFrame,
        weekly_df: pd.DataFrame,
        symbol: str = "sh000001"
    ) -> None:
        """绘制MACD顶底背离可视化图表
        
        使用四个子图显示：
        - 上图：日线价格和背离标记
        - 第二图：日线MACD
        - 第三图：周线价格和背离标记
        - 下图：周线MACD
        
        Args:
            daily_df: 日线数据DataFrame
            weekly_df: 周线数据DataFrame
            symbol: 指数代码，用于文件名
        """
        if daily_df is None or len(daily_df) == 0:
            return
        
        # 确保日期列是datetime类型
        daily_df = daily_df.copy()
        weekly_df = weekly_df.copy()
        
        if not pd.api.types.is_datetime64_any_dtype(daily_df['date']):
            daily_df['date'] = pd.to_datetime(daily_df['date'])
        if not pd.api.types.is_datetime64_any_dtype(weekly_df['date']):
            weekly_df['date'] = pd.to_datetime(weekly_df['date'])
        
        # 按日期排序
        daily_df = daily_df.sort_values('date').reset_index(drop=True)
        weekly_df = weekly_df.sort_values('date').reset_index(drop=True)
        
        # 创建四个子图
        fig, axes = plt.subplots(4, 1, figsize=(16, 16), sharex=False)
        ax1, ax2, ax3, ax4 = axes
        
        # 设置中文字体
        plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
        plt.rcParams['axes.unicode_minus'] = False
        
        # ==================== 第一个子图：日线价格 ====================
        ax1.plot(daily_df['date'], daily_df['close'], color='#333333', linewidth=1.5, label='日线收盘价')
        
        # 根据配置决定是否显示普通背离
        if not MACD_DIVERGENCE_ONLY_VOLUME:
            # 先标记普通顶背离（较小标记）
            normal_top_divergence = daily_df[(daily_df['top_divergence']) & (~daily_df['real_top_divergence'])]
            if len(normal_top_divergence) > 0:
                ax1.scatter(normal_top_divergence['date'], normal_top_divergence['close'], 
                           color='red', marker='v', s=80, zorder=4, label='顶背离', alpha=0.6, edgecolors='red', linewidths=1)
            
            # 标记普通底背离（较小标记）
            normal_bottom_divergence = daily_df[(daily_df['bottom_divergence']) & (~daily_df['real_bottom_divergence'])]
            if len(normal_bottom_divergence) > 0:
                ax1.scatter(normal_bottom_divergence['date'], normal_bottom_divergence['close'], 
                           color='green', marker='^', s=80, zorder=4, label='底背离', alpha=0.6, edgecolors='green', linewidths=1)
        
        # 重点标记真的顶背离（放量背离，红色透明点）
        real_top_divergence = daily_df[daily_df['real_top_divergence']]
        if len(real_top_divergence) > 0:
            ax1.scatter(real_top_divergence['date'], real_top_divergence['close'], 
                       color='red', marker='o', s=300, zorder=6, label='真的顶背离（放量）', 
                       alpha=0.5, edgecolors='red', linewidths=1)
        
        # 重点标记真的底背离（放量背离，红色透明点）
        real_bottom_divergence = daily_df[daily_df['real_bottom_divergence']]
        if len(real_bottom_divergence) > 0:
            ax1.scatter(real_bottom_divergence['date'], real_bottom_divergence['close'], 
                       color='red', marker='o', s=300, zorder=6, label='真的底背离（放量）', 
                       alpha=0.5, edgecolors='red', linewidths=1)
        
        ax1.set_ylabel('日线收盘价', fontsize=12, fontweight='bold')
        ax1.grid(True, alpha=0.3, linestyle='--')
        ax1.legend(loc='upper left', fontsize=10)
        
        # ==================== 第二个子图：日线MACD ====================
        ax2.plot(daily_df['date'], daily_df['macd'], color='blue', linewidth=1.5, label='MACD')
        ax2.plot(daily_df['date'], daily_df['signal'], color='orange', linewidth=1.5, label='Signal')
        ax2.bar(daily_df['date'], daily_df['histogram'], color='gray', alpha=0.3, label='Histogram')
        ax2.axhline(y=0, color='black', linestyle='-', linewidth=0.5, alpha=0.5)
        
        ax2.set_ylabel('日线MACD', fontsize=12, fontweight='bold')
        ax2.grid(True, alpha=0.3, linestyle='--')
        ax2.legend(loc='upper left', fontsize=10)
        
        # ==================== 第三个子图：周线价格 ====================
        ax3.plot(weekly_df['date'], weekly_df['close'], color='#333333', linewidth=2, label='周线收盘价')
        
        # 根据配置决定是否显示普通背离
        if not MACD_DIVERGENCE_ONLY_VOLUME:
            # 先标记普通顶背离（较小标记）
            normal_weekly_top_divergence = weekly_df[(weekly_df['top_divergence']) & (~weekly_df['real_top_divergence'])]
            if len(normal_weekly_top_divergence) > 0:
                ax3.scatter(normal_weekly_top_divergence['date'], normal_weekly_top_divergence['close'], 
                           color='red', marker='v', s=120, zorder=4, label='顶背离', alpha=0.6, edgecolors='red', linewidths=1)
            
            # 标记普通底背离（较小标记）
            normal_weekly_bottom_divergence = weekly_df[(weekly_df['bottom_divergence']) & (~weekly_df['real_bottom_divergence'])]
            if len(normal_weekly_bottom_divergence) > 0:
                ax3.scatter(normal_weekly_bottom_divergence['date'], normal_weekly_bottom_divergence['close'], 
                           color='green', marker='^', s=120, zorder=4, label='底背离', alpha=0.6, edgecolors='green', linewidths=1)
        
        # 重点标记真的顶背离（放量背离，红色透明点）
        real_weekly_top_divergence = weekly_df[weekly_df['real_top_divergence']]
        if len(real_weekly_top_divergence) > 0:
            ax3.scatter(real_weekly_top_divergence['date'], real_weekly_top_divergence['close'], 
                       color='red', marker='o', s=400, zorder=6, label='真的顶背离（放量）', 
                       alpha=0.5, edgecolors='red', linewidths=1)
        
        # 重点标记真的底背离（放量背离，红色透明点）
        real_weekly_bottom_divergence = weekly_df[weekly_df['real_bottom_divergence']]
        if len(real_weekly_bottom_divergence) > 0:
            ax3.scatter(real_weekly_bottom_divergence['date'], real_weekly_bottom_divergence['close'], 
                       color='red', marker='o', s=400, zorder=6, label='真的底背离（放量）', 
                       alpha=0.5, edgecolors='red', linewidths=1)
        
        ax3.set_ylabel('周线收盘价', fontsize=12, fontweight='bold')
        ax3.grid(True, alpha=0.3, linestyle='--')
        ax3.legend(loc='upper left', fontsize=10)
        
        # ==================== 第四个子图：周线MACD ====================
        ax4.plot(weekly_df['date'], weekly_df['macd'], color='blue', linewidth=2, label='MACD')
        ax4.plot(weekly_df['date'], weekly_df['signal'], color='orange', linewidth=2, label='Signal')
        ax4.bar(weekly_df['date'], weekly_df['histogram'], color='gray', alpha=0.3, label='Histogram')
        ax4.axhline(y=0, color='black', linestyle='-', linewidth=0.5, alpha=0.5)
        
        ax4.set_xlabel('日期', fontsize=12, fontweight='bold')
        ax4.set_ylabel('周线MACD', fontsize=12, fontweight='bold')
        ax4.grid(True, alpha=0.3, linestyle='--')
        ax4.legend(loc='upper left', fontsize=10)
        
        # 格式化日期轴
        for ax in [ax1, ax2, ax3, ax4]:
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
            ax.xaxis.set_major_locator(mdates.YearLocator())
            plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right')
        
        # 设置整体标题
        latest_daily_price = daily_df['close'].iloc[-1]
        latest_weekly_price = weekly_df['close'].iloc[-1]
        latest_daily_macd = daily_df['macd'].iloc[-1]
        latest_weekly_macd = weekly_df['macd'].iloc[-1]
        
        title = (
            f'{symbol} MACD顶底背离分析图\n'
            f'日线: 收盘价={latest_daily_price:.2f}, MACD={latest_daily_macd:.2f} | '
            f'周线: 收盘价={latest_weekly_price:.2f}, MACD={latest_weekly_macd:.2f}'
        )
        fig.suptitle(title, fontsize=14, fontweight='bold', y=0.995)
        
        # 调整布局
        plt.tight_layout(rect=[0, 0, 1, 0.96])  # 为标题留出空间
        
        # 保存图片
        pic_file = PIC_DIR / f"macd_divergence_{symbol}.png"
        plt.savefig(pic_file, dpi=300, bbox_inches='tight', facecolor='white')
        plt.close(fig)  # 关闭图形以释放内存
    
    def get_processed_data(self) -> Optional[Dict[str, pd.DataFrame]]:
        """获取处理后的数据
        
        Returns:
            包含日线和周线数据的字典，如果尚未计算则返回None
        """
        return self._processed_data


"""
PE估值指标模块

基于股票指数的PE（市盈率）数据计算估值风险。
通过计算当前PE值在历史数据中的分位数来判断市场估值水平。
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
from config import (
    RAW_DATA_DIR, PROCESSED_DATA_DIR, PIC_DIR, INDICATOR_START_DATE, 
    PE_VALUATION_TYPE, PE_VALUATION_METHOD,
    PE_VALUATION_OUTLIER_METHOD, PE_VALUATION_WINSORIZE_PERCENTILE,
    PE_VALUATION_ROLLING_WINDOW_YEARS, PE_VALUATION_IQR_MULTIPLIER
)

try:
    # 尝试相对导入
    from ..api.stock_index_pe import fetch_stock_index_pe
except ImportError:
    # 如果相对导入失败，尝试绝对导入
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from api.stock_index_pe import fetch_stock_index_pe


class PEValuationIndicator(BaseIndicator):
    """PE估值指标
    
    基于股票指数的PE数据计算估值风险：
    - 计算当前PE值在历史数据中的分位数
    - PE值越高，分位数越高，风险百分比越高
    - 风险百分比 = 历史分位数（0-100）
    """
    
    # 支持的PE类型列表
    SUPPORTED_PE_TYPES = [
        "等权滚动市盈率",
        "滚动市盈率",
        "等权静态市盈率",
        "静态市盈率",
        "滚动市盈率中位数",
        "静态市盈率中位数"
    ]
    
    def __init__(
        self,
        name: str = "pe_valuation_indicator",
        description: Optional[str] = None,
        pe_type: Optional[str] = None,
        method: Optional[str] = None,
        outlier_method: Optional[str] = None,
        winsorize_percentile: Optional[float] = None,
        rolling_window_years: Optional[int] = None,
        iqr_multiplier: Optional[float] = None,
        save_processed: bool = True,
        line_alpha: float = 0.8  # 指数和PE线条的透明度
    ):
        """初始化PE估值指标
        
        Args:
            name: 指标名称
            description: 指标描述
            pe_type: PE类型，如果为None则使用config中的PE_VALUATION_TYPE
            method: 计算方式，可选"rolling"或"full_history"，如果为None则使用config中的PE_VALUATION_METHOD
            outlier_method: 异常值处理方式，可选"none"/"winsorize"/"rolling_window"/"iqr"，如果为None则使用config中的PE_VALUATION_OUTLIER_METHOD
            winsorize_percentile: Winsorization截断百分比（如5.0表示各去除5%），如果为None则使用config中的PE_VALUATION_WINSORIZE_PERCENTILE
            rolling_window_years: 滚动窗口年数（如10表示10年），如果为None则使用config中的PE_VALUATION_ROLLING_WINDOW_YEARS
            iqr_multiplier: IQR倍数（如1.5表示1.5倍IQR），如果为None则使用config中的PE_VALUATION_IQR_MULTIPLIER
            save_processed: 是否保存处理后的数据到processed文件夹
            line_alpha: 指数和PE线条的透明度（0.0-1.0），默认0.8
        """
        if description is None:
            description = "PE估值指标：基于历史PE数据计算当前估值水平的分位数作为风险百分比"
        
        super().__init__(name=name, description=description)
        
        # 使用传入的pe_type或config中的默认值
        self.pe_type = pe_type or PE_VALUATION_TYPE
        
        # 验证PE类型是否支持
        if self.pe_type not in self.SUPPORTED_PE_TYPES:
            raise ValueError(
                f"不支持的PE类型: {self.pe_type}。"
                f"支持的PE类型: {', '.join(self.SUPPORTED_PE_TYPES)}"
            )
        
        # 使用传入的method或config中的默认值
        self.method = method or PE_VALUATION_METHOD
        
        # 验证计算方式是否支持
        if self.method not in ["rolling", "full_history"]:
            raise ValueError(
                f"不支持的计算方式: {self.method}。"
                f"支持的计算方式: 'rolling' 或 'full_history'"
            )
        
        # 使用传入的异常值处理方法或config中的默认值
        self.outlier_method = outlier_method or PE_VALUATION_OUTLIER_METHOD
        
        # 验证异常值处理方法是否支持
        if self.outlier_method not in ["none", "winsorize", "rolling_window", "iqr"]:
            raise ValueError(
                f"不支持的异常值处理方法: {self.outlier_method}。"
                f"支持的方法: 'none', 'winsorize', 'rolling_window', 'iqr'"
            )
        
        # 异常值处理参数
        self.winsorize_percentile = winsorize_percentile if winsorize_percentile is not None else PE_VALUATION_WINSORIZE_PERCENTILE
        self.rolling_window_years = rolling_window_years if rolling_window_years is not None else PE_VALUATION_ROLLING_WINDOW_YEARS
        self.iqr_multiplier = iqr_multiplier if iqr_multiplier is not None else PE_VALUATION_IQR_MULTIPLIER
        
        # 验证参数有效性
        if self.winsorize_percentile < 0 or self.winsorize_percentile >= 50:
            raise ValueError(f"winsorize_percentile必须在0-50之间，当前值: {self.winsorize_percentile}")
        if self.rolling_window_years <= 0:
            raise ValueError(f"rolling_window_years必须大于0，当前值: {self.rolling_window_years}")
        if self.iqr_multiplier <= 0:
            raise ValueError(f"iqr_multiplier必须大于0，当前值: {self.iqr_multiplier}")
        
        self.save_processed = save_processed
        self.line_alpha = max(0.0, min(1.0, line_alpha))  # 确保透明度在0-1范围内
        self._processed_data = None
    
    def _load_pe_data(self, symbol: str = "沪深300") -> pd.DataFrame:
        """加载股票指数PE数据
        
        Args:
            symbol: 指数名称，默认沪深300
            
        Returns:
            DataFrame包含日期、指数点位和PE数据
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
        
        # 验证必需的列是否存在
        if '指数' not in df.columns:
            raise ValueError("数据中缺少'指数'列")
        
        if self.pe_type not in df.columns:
            raise ValueError(
                f"数据中缺少'{self.pe_type}'列。"
                f"可用列: {', '.join(df.columns)}"
            )
        
        # 选择需要的列：日期、指数点位和指定的PE类型
        return df[['日期', '指数', self.pe_type]].copy()
    
    def _filter_outliers(
        self,
        pe_series: pd.Series,
        dates: pd.Series,
        current_date: pd.Timestamp,
        current_index: int
    ) -> pd.Series:
        """根据配置的异常值处理方法过滤数据
        
        Args:
            pe_series: PE值序列
            dates: 日期序列
            current_date: 当前日期
            current_index: 当前索引
            
        Returns:
            过滤后的PE值序列
        """
        if self.outlier_method == "none":
            # 不处理异常值，返回原始数据
            return pe_series
        
        elif self.outlier_method == "winsorize":
            # Winsorization截断法：去除最高和最低各winsorize_percentile%的数据
            if len(pe_series) < 2:
                return pe_series
            
            # 计算截断点
            lower_percentile = self.winsorize_percentile
            upper_percentile = 100 - self.winsorize_percentile
            
            lower_bound = pe_series.quantile(lower_percentile / 100.0)
            upper_bound = pe_series.quantile(upper_percentile / 100.0)
            
            # 过滤：只保留在范围内的数据
            filtered = pe_series[(pe_series >= lower_bound) & (pe_series <= upper_bound)]
            return filtered
        
        elif self.outlier_method == "rolling_window":
            # 滚动窗口法：使用最近rolling_window_years年的数据
            window_start_date = current_date - pd.DateOffset(years=self.rolling_window_years)
            # 确保dates和pe_series的索引对齐，使用布尔索引
            mask = dates >= window_start_date
            filtered = pe_series[mask]
            return filtered
        
        elif self.outlier_method == "iqr":
            # IQR方法：使用四分位距识别异常值
            if len(pe_series) < 4:
                return pe_series
            
            Q1 = pe_series.quantile(0.25)
            Q3 = pe_series.quantile(0.75)
            IQR = Q3 - Q1
            
            # 计算异常值边界
            lower_bound = Q1 - self.iqr_multiplier * IQR
            upper_bound = Q3 + self.iqr_multiplier * IQR
            
            # 过滤：只保留在正常范围内的数据
            filtered = pe_series[(pe_series >= lower_bound) & (pe_series <= upper_bound)]
            return filtered
        
        else:
            raise ValueError(f"不支持的异常值处理方法: {self.outlier_method}")
    
    def _calculate_pe_percentile(
        self,
        pe_df: pd.DataFrame,
        start_date: Optional[str] = None
    ) -> pd.DataFrame:
        """计算PE的历史分位数
        
        Args:
            pe_df: PE数据DataFrame，包含日期、指数点位和PE值
            start_date: 计算开始日期，格式YYYY-MM-DD，如果为None则使用config中的INDICATOR_START_DATE
            
        Returns:
            包含日期、指数点位、PE值和风险百分比的DataFrame
        """
        # 使用config中的开始日期作为默认值
        if start_date is None:
            start_date = INDICATOR_START_DATE
        
        # 按开始日期过滤数据
        if start_date:
            start_datetime = pd.to_datetime(start_date)
            pe_df = pe_df[pe_df['日期'] >= start_datetime].copy()
            pe_df = pe_df.reset_index(drop=True)
        
        # 过滤掉PE值为NaN的行
        pe_df = pe_df.dropna(subset=[self.pe_type])
        
        if len(pe_df) == 0:
            raise ValueError(f"过滤后的数据为空，无法计算分位数")
        
        # 计算rolling的历史最大值和最小值（保留用于额外数据）
        pe_df['rolling_max'] = pe_df[self.pe_type].expanding().max()
        pe_df['rolling_min'] = pe_df[self.pe_type].expanding().min()
        
        # 根据计算方式选择不同的分位数计算方法
        if self.method == "rolling":
            # Rolling方案：每行只使用到当前为止的历史数据计算分位数（expanding窗口）
            def calculate_rolling_percentile(row_index, series, dates):
                """计算到当前行为止的历史分位数（应用异常值处理）"""
                if row_index == 0:
                    # 第一行数据，无法计算分位数，设为中等风险
                    return 50.0
                
                # 获取从开始到当前行的所有历史数据
                historical_data = series.iloc[:row_index + 1]
                historical_dates = dates.iloc[:row_index + 1]
                current_value = series.iloc[row_index]
                current_date = dates.iloc[row_index]
                
                # 应用异常值处理
                filtered_data = self._filter_outliers(
                    historical_data, historical_dates, current_date, row_index
                )
                
                if len(filtered_data) < 2:
                    # 如果过滤后数据太少，使用原始数据
                    filtered_data = historical_data
                
                # 计算有多少比例的过滤后数据小于等于当前值
                rank = (filtered_data <= current_value).sum()
                total_count = len(filtered_data)
                percentile = (rank - 1) / (total_count - 1) * 100 if total_count > 1 else 50.0
                
                # 风险百分比 = 分位数
                # 分位数越高（接近100%）→ 风险百分比越高（接近100%）
                # 分位数越低（接近0%）→ 风险百分比越低（接近0%）
                risk_percentage = percentile
                return risk_percentage
            
            # 为每一行计算rolling分位数（使用expanding窗口）
            pe_df['risk_percentage'] = [
                calculate_rolling_percentile(i, pe_df[self.pe_type], pe_df['日期']) 
                for i in range(len(pe_df))
            ]
        
        elif self.method == "full_history":
            # 全历史方案：所有行都使用完整的历史数据集计算分位数
            full_series = pe_df[self.pe_type]
            full_dates = pe_df['日期']
            
            def calculate_full_history_percentile(row_index, current_value, current_date, full_series, full_dates):
                """使用全部历史数据计算分位数（应用异常值处理）"""
                # 应用异常值处理
                filtered_data = self._filter_outliers(
                    full_series, full_dates, current_date, row_index
                )
                
                if len(filtered_data) < 2:
                    # 如果过滤后数据太少，使用原始数据
                    filtered_data = full_series
                
                # 计算有多少比例的过滤后数据小于等于当前值
                rank = (filtered_data <= current_value).sum()
                total_count = len(filtered_data)
                percentile = (rank - 1) / (total_count - 1) * 100 if total_count > 1 else 50.0
                
                # 风险百分比 = 分位数
                risk_percentage = percentile
                return risk_percentage
            
            # 为每一行计算全历史分位数
            pe_df['risk_percentage'] = [
                calculate_full_history_percentile(
                    i, pe_df[self.pe_type].iloc[i], pe_df['日期'].iloc[i],
                    full_series, full_dates
                )
                for i in range(len(pe_df))
            ]
        
        else:
            raise ValueError(f"不支持的计算方式: {self.method}")
        
        # 确定估值状态
        def get_valuation_status(row):
            """根据PE分位数判断估值状态"""
            risk_pct = row['risk_percentage']
            if pd.isna(risk_pct):
                return '未知'
            elif risk_pct >= 80:
                return '严重高估'
            elif risk_pct >= 60:
                return '高估'
            elif risk_pct <= 20:
                return '严重低估'
            elif risk_pct <= 40:
                return '低估'
            else:
                return '正常'
        
        pe_df['valuation_status'] = pe_df.apply(get_valuation_status, axis=1)
        
        # 选择需要的列
        result_df = pe_df[[
            '日期',
            '指数',
            self.pe_type,
            'rolling_max',
            'rolling_min',
            'risk_percentage',
            'valuation_status'
        ]].copy()
        
        return result_df
    
    def calculate(
        self,
        symbol: str = "沪深300",
        start_date: Optional[str] = None,
        **kwargs
    ) -> IndicatorResult:
        """计算PE估值风险值
        
        Args:
            symbol: 股票指数名称，默认"沪深300"
            start_date: 计算开始日期，格式YYYY-MM-DD，如果为None则使用config中的INDICATOR_START_DATE
            **kwargs: 其他参数（未使用）
            
        Returns:
            IndicatorResult: 包含风险百分比、风险等级等信息的计算结果
        """
        try:
            # 使用config中的开始日期作为默认值
            if start_date is None:
                start_date = INDICATOR_START_DATE
            
            # 加载PE数据
            pe_df = self._load_pe_data(symbol=symbol)
            
            # 计算PE分位数
            result_df = self._calculate_pe_percentile(pe_df, start_date=start_date)
            
            # 保存处理后的数据
            if self.save_processed:
                # 生成安全的文件名（替换特殊字符）
                safe_symbol = symbol.replace("/", "_").replace("\\", "_")
                safe_pe_type = self.pe_type.replace("/", "_").replace("\\", "_")
                output_file = PROCESSED_DATA_DIR / f"pe_valuation_{safe_symbol}_{safe_pe_type}.csv"
                result_df.to_csv(output_file, index=False, encoding='utf-8-sig')
            
            # 保存到实例变量，供后续使用
            self._processed_data = result_df
            
            # 获取最新一行的数据
            if len(result_df) == 0:
                raise ValueError("计算结果为空，无法计算风险值")
            
            latest = result_df.iloc[-1]
            current_pe = latest[self.pe_type]
            current_risk_percentage = latest['risk_percentage']
            valuation_status = latest['valuation_status']
            
            # 生成提示信息
            message = (
                f"当前{self.pe_type}为{current_pe:.2f}，"
                f"历史分位数为{current_risk_percentage:.2f}%，"
                f"市场处于{valuation_status}状态"
            )
            
            # 创建额外数据
            extra_data = {
                'current_pe': float(current_pe),
                'current_index': float(latest['指数']),  # 当前指数点位
                'pe_type': self.pe_type,
                'method': self.method,  # 计算方式
                'outlier_method': self.outlier_method,  # 异常值处理方法
                'rolling_max': float(latest['rolling_max']),
                'rolling_min': float(latest['rolling_min']),
                'valuation_status': valuation_status,
                'data_points': len(result_df),
                'start_date': result_df['日期'].min().strftime('%Y-%m-%d'),  # 实际使用的开始日期
                'end_date': result_df['日期'].max().strftime('%Y-%m-%d'),
                'symbol': symbol
            }
            
            # 添加异常值处理参数信息
            if self.outlier_method == "winsorize":
                extra_data['winsorize_percentile'] = self.winsorize_percentile
            elif self.outlier_method == "rolling_window":
                extra_data['rolling_window_years'] = self.rolling_window_years
            elif self.outlier_method == "iqr":
                extra_data['iqr_multiplier'] = self.iqr_multiplier
            
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
                    self._plot_pe_valuation(result_df, symbol=symbol)
                except Exception as plot_error:
                    # 绘图失败不影响计算结果，只记录警告
                    import warnings
                    warnings.warn(f"绘图失败: {str(plot_error)}")
            
            return result
            
        except Exception as e:
            # 如果计算失败，返回错误结果
            error_result = IndicatorResult(
                indicator_name=self.name,
                risk_percentage=0.0,
                risk_level=self.get_risk_level(0.0),
                timestamp=datetime.now(),
                message=f"计算失败: {str(e)}",
                extra_data={"error": True, "error_message": str(e)}
            )
            return error_result
    
    def _plot_pe_valuation(
        self,
        data_df: pd.DataFrame,
        symbol: str = "沪深300"
    ) -> None:
        """绘制PE估值可视化图表
        
        使用上下两个子图分开显示：
        - 上图：指数点位，根据估值状态（严重高估/高估/正常/低估/严重低估）进行颜色填充
        - 下图：PE值，显示历史最大值和最小值
        
        Args:
            data_df: 包含PE估值数据的DataFrame
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
        
        # 根据valuation_status分组，为每个状态的时间段填充对应颜色
        dates = data_df['日期'].values
        index_values = data_df['指数'].values
        status_values = data_df['valuation_status'].values
        
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
            Patch(facecolor='red', alpha=0.5, label='高估区域'),
            Patch(facecolor='darkred', alpha=0.5, label='严重高估区域'),
            Patch(facecolor='green', alpha=0.5, label='低估区域'),
            Patch(facecolor='darkgreen', alpha=0.5, label='严重低估区域')
        ]
        ax1.legend(handles=legend_elements_top, loc='upper left', fontsize=10, framealpha=0.9)
        
        # ==================== 下图：PE值 ====================
        # 绘制PE值折线
        line2 = ax2.plot(
            data_df['日期'],
            data_df[self.pe_type],
            color='#888888',  # 浅灰色
            linewidth=2,
            alpha=self.line_alpha,
            label=f'{self.pe_type}',
            zorder=4
        )
        
        # 绘制历史最大值和最小值
        line_max = ax2.plot(
            data_df['日期'],
            data_df['rolling_max'],
            color='red',
            linestyle='--',
            linewidth=1.5,
            alpha=0.6,
            label='历史最大值',
            zorder=3
        )
        
        line_min = ax2.plot(
            data_df['日期'],
            data_df['rolling_min'],
            color='green',
            linestyle='--',
            linewidth=1.5,
            alpha=0.6,
            label='历史最小值',
            zorder=3
        )
        
        # 设置下图标签和颜色
        ax2.set_xlabel('日期', fontsize=12, fontweight='bold')
        ax2.set_ylabel(f'{self.pe_type}', fontsize=12, fontweight='bold', color='#888888')
        ax2.tick_params(axis='y', labelcolor='#888888')
        ax2.grid(True, alpha=0.3, linestyle='--', zorder=1)
        
        # 下图图例
        legend_elements_bottom = [
            line2[0],
            line_max[0],
            line_min[0]
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
        latest_pe = data_df[self.pe_type].iloc[-1]
        latest_index = data_df['指数'].iloc[-1]
        latest_status = data_df['valuation_status'].iloc[-1]
        latest_risk_pct = data_df['risk_percentage'].iloc[-1]
        
        title = (
            f'{symbol}PE估值分析图 ({self.pe_type})\n'
            f'当前PE: {latest_pe:.2f} | '
            f'当前指数: {latest_index:.2f} | '
            f'历史分位数: {latest_risk_pct:.2f}% | '
            f'估值状态: {latest_status}'
        )
        fig.suptitle(title, fontsize=14, fontweight='bold', y=0.995)
        
        # 调整布局
        plt.tight_layout(rect=[0, 0, 1, 0.96])  # 为标题留出空间
        
        # 保存图片
        safe_symbol = symbol.replace("/", "_").replace("\\", "_")
        safe_pe_type = self.pe_type.replace("/", "_").replace("\\", "_")
        pic_file = PIC_DIR / f"pe_valuation_{safe_symbol}_{safe_pe_type}.png"
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
        """填充指定估值状态的时间段区域
        
        Args:
            ax: matplotlib坐标轴
            dates: 日期数组
            index_values: 指数点位数组
            start_idx: 开始索引
            end_idx: 结束索引
            status: 估值状态（'严重高估'/'高估'/'正常'/'低估'/'严重低估'）
            fill_bottom: 填充区域底部位置
        """
        # 正常状态不填充颜色（透明）
        if status == '正常' or status == '未知':
            return
        
        # 确定颜色和透明度
        if status == '严重高估':
            color = 'darkred'
            alpha = 0.6
        elif status == '高估':
            color = 'red'
            alpha = 0.4
        elif status == '严重低估':
            color = 'darkgreen'
            alpha = 0.6
        elif status == '低估':
            color = 'green'
            alpha = 0.4
        else:
            color = 'gray'
            alpha = 0.3
        
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
            处理后的PE估值数据DataFrame，如果尚未计算则返回None
        """
        return self._processed_data


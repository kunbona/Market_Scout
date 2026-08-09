"""
破净率指标模块

计算破净股比例在历史上的分位数。
破净股是指市净率(PB)大于0并且小于1的股票，即市值跌破净资产值。
比例越高说明市场越悲观，可能是底部区域；比例越低说明市场越乐观，可能是顶部区域。
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
    INDICATOR_START_DATE, PROCESSED_DATA_DIR, PIC_DIR, RAW_DATA_DIR
)


class BelowNetAssetIndicator(BaseIndicator):
    """破净率指标
    
    1. 加载破净股统计数据
    2. 计算破净股比例（已由API提供）
    3. 计算破净股比例在全历史上的分位数
    4. 以此判断市场整体的估值水平和风险
    
    注意：破净率越高，说明市场越悲观，分位数越高代表风险越低（接近底部）
    因此需要反转分位数：risk_percentage = 100 - percentile
    """
    
    def __init__(
        self, 
        symbol: str = "全部A股",
        name: Optional[str] = None,
        description: Optional[str] = None,
        save_processed: bool = True
    ):
        """
        Args:
            symbol: 统计范围，默认"全部A股"。支持："全部A股", "沪深300", "上证50", "中证500"
            name: 指标名称，默认为"below_net_asset_{symbol}"
            description: 指标描述
            save_processed: 是否保存处理后的数据和绘图
        """
        if name is None:
            name = f"below_net_asset_{symbol}".replace(" ", "_")
        if description is None:
            description = f"破净率指标：{symbol}破净股比例分位数"
        
        super().__init__(name=name, description=description)
        self.symbol = symbol
        self.save_processed = save_processed
        self._processed_data = None
        
    def _load_data(self) -> pd.DataFrame:
        """加载 XBX 生成的破净股统计数据"""
        # 构建文件路径
        safe_symbol = self.symbol.replace(" ", "_")
        filename = f"stock_a_below_net_asset_statistics_{safe_symbol}.csv"
        file_path = RAW_DATA_DIR / filename

        needs_refresh = not file_path.exists()
        if file_path.exists():
            try:
                existing_dates = pd.read_csv(file_path, usecols=["date"])
                existing_latest = pd.to_datetime(existing_dates["date"], errors="coerce").max()
                xbx_path = RAW_DATA_DIR / "xbx_stock_data.parquet"
                if xbx_path.exists():
                    xbx_dates = pd.read_parquet(xbx_path, columns=["交易日期"])
                    xbx_latest = pd.to_datetime(xbx_dates["交易日期"], errors="coerce").max()
                    needs_refresh = pd.notna(xbx_latest) and (
                        pd.isna(existing_latest) or xbx_latest > existing_latest
                    )
            except Exception as e:
                raise ValueError(f"无法检查破净股数据新鲜度: {e}")

        if needs_refresh:
            try:
                from ..api.stock_a_below_net_asset_statistics import fetch_stock_a_below_net_asset_statistics
                print(f"正在从XBX刷新破净股数据: {self.symbol}")
                result = fetch_stock_a_below_net_asset_statistics(symbol=self.symbol, save_raw=True)
                if not result['success']:
                    raise ValueError(f"刷新数据失败: {result.get('error')}")
                df = result['data']
            except Exception as e:
                raise ValueError(f"无法加载破净股数据: {e}")
        else:
            # 从文件加载
            print(f"从文件加载破净股数据: {file_path}")
            df = pd.read_csv(file_path)
        
        # 确保日期列格式正确
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'])
            df = df.sort_values(by='date')
        else:
            raise ValueError("数据中缺少'date'列")
        
        # 确保必要的列存在
        required_cols = ['below_net_asset_ratio']
        for col in required_cols:
            if col not in df.columns:
                raise ValueError(f"数据中缺少'{col}'列")
        
        return df
            
    def calculate(self, start_date: Optional[str] = None, **kwargs) -> IndicatorResult:
        try:
            # 1. 确定过滤用的开始日期
            if start_date is None:
                start_date = INDICATOR_START_DATE
            filter_start_date = pd.to_datetime(start_date)
            
            # 2. 加载破净股数据
            df = self._load_data()
            
            if df is None or df.empty:
                raise ValueError("加载的数据为空")
            
            # 3. 计算破净率的全历史分位数
            print(f"正在计算破净率分位数 ({self.symbol})...")
            
            # 破净率越高，说明市场越悲观（接近底部），风险越低
            # 因此使用正向分位数（破净率高 -> 分位数高 -> 风险低）
            # 后续需要反转：risk_percentage = 100 - percentile
            df['percentile'] = df['below_net_asset_ratio'].expanding().rank(pct=True) * 100
            
            # 4. 过滤结果日期
            result_df = df[df['date'] >= filter_start_date].copy()
            
            if result_df.empty:
                raise ValueError(f"过滤后的结果为空 (start_date={start_date})")
                
            self._processed_data = result_df
            
            # 5. 保存和绘图
            if self.save_processed:
                self._save_and_plot(result_df)
                
            # 6. 构建结果
            latest = result_df.iloc[-1]
            percentile = latest['percentile']
            ratio = latest['below_net_asset_ratio']
            
            # 反转分位数：破净率高 -> 分位数高 -> 风险低
            # 因此：risk_percentage = 100 - percentile
            risk_percentage = 100 - percentile
            
            message = (f"破净率指标 ({self.symbol}): "
                      f"当前破净股比例={ratio:.2%}, "
                      f"历史分位数={percentile:.2f}%, "
                      f"风险评分={risk_percentage:.2f}% "
                      f"(日期: {latest['date'].strftime('%Y-%m-%d')})")
            
            return self.create_result(
                risk_percentage=risk_percentage,
                message=message,
                extra_data={
                    "symbol": self.symbol,
                    "below_net_asset_ratio": float(ratio),
                    "percentile": float(percentile),
                    "risk_percentage": float(risk_percentage),
                    "date": latest['date'].strftime('%Y-%m-%d'),
                    "below_net_asset_count": int(latest.get('below_net_asset', 0)),
                    "total_company": int(latest.get('total_company', 0))
                },
                timestamp=latest['date']
            )
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            return self.create_result(
                risk_percentage=50.0,  # 默认中性值
                message=f"计算失败: {str(e)}",
                extra_data={"error": True, "error_message": str(e)}
            )
            
    def _save_and_plot(self, df: pd.DataFrame):
        """保存数据并绘图"""
        # 保存数据
        safe_symbol = self.symbol.replace(" ", "_")
        output_file = PROCESSED_DATA_DIR / f"below_net_asset_{safe_symbol}.csv"
        
        # 准备保存的数据：包含日期、破净率、分位数、风险评分
        save_df = df[['date', 'below_net_asset_ratio', 'percentile']].copy()
        save_df['risk_percentage'] = 100 - save_df['percentile']
        save_df.to_csv(output_file, index=False)
        print(f"数据已保存至: {output_file}")
        
        # 绘图
        try:
            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10), sharex=True)
            
            # 设置中文字体
            plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
            plt.rcParams['axes.unicode_minus'] = False
            
            # 子图1：破净率的历史分位数（反转后的风险评分）
            risk_percentage = 100 - df['percentile']
            ax1.plot(df['date'], risk_percentage, 
                    label=f'破净率风险评分', color='#d62728', linewidth=1.5)
            ax1.axhline(y=80, color='red', linestyle='--', alpha=0.5, label='高风险(80%)')
            ax1.axhline(y=20, color='green', linestyle='--', alpha=0.5, label='低风险(20%)')
            ax1.fill_between(df['date'], 0, risk_percentage, 
                            where=(risk_percentage >= 80), alpha=0.1, color='red', 
                            label='高风险区域')
            ax1.fill_between(df['date'], 0, risk_percentage, 
                            where=(risk_percentage <= 20), alpha=0.1, color='green', 
                            label='低风险区域')
            ax1.set_title(f"{self.symbol} 破净率风险评分历史走势", fontsize=14, fontweight='bold')
            ax1.set_ylabel("风险评分 (%)", fontsize=12)
            ax1.grid(True, alpha=0.3, linestyle='--')
            ax1.legend(loc='best', fontsize=10)
            ax1.set_ylim(0, 100)
            
            # 子图2：破净股比例走势
            ax2.plot(df['date'], df['below_net_asset_ratio'] * 100, 
                    label='破净股比例', color='#1f77b4', linewidth=1.5)
            ax2.set_title(f"{self.symbol} 破净股比例历史走势", fontsize=14, fontweight='bold')
            ax2.set_xlabel("日期", fontsize=12)
            ax2.set_ylabel("破净股比例 (%)", fontsize=12)
            ax2.grid(True, alpha=0.3, linestyle='--')
            ax2.legend(loc='best', fontsize=10)
            
            # 格式化日期
            ax2.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
            ax2.xaxis.set_major_locator(mdates.YearLocator())
            plt.xticks(rotation=45)
            
            plt.tight_layout()
            
            pic_file = PIC_DIR / f"below_net_asset_{safe_symbol}.png"
            plt.savefig(pic_file, dpi=300, bbox_inches='tight')
            plt.close(fig)
            print(f"图表已保存至: {pic_file}")
            
        except Exception as e:
            print(f"绘图失败: {e}")
            import traceback
            traceback.print_exc()

    def get_processed_data(self) -> Optional[pd.DataFrame]:
        """获取处理后的数据"""
        return self._processed_data

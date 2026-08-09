"""
A股破净股统计数据获取模块

使用 XBX 个股数据计算破净股统计数据。
"""

import numpy as np
import pandas as pd
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime

# 支持直接运行和作为包导入
try:
    from config import RAW_DATA_DIR
except ImportError:
    # 如果直接运行，尝试从上级目录导入
    import sys
    from pathlib import Path
    parent_dir = Path(__file__).parent.parent.parent
    sys.path.insert(0, str(parent_dir))
    from config import RAW_DATA_DIR

# 支持的symbol列表。全部A股使用 XBX 当前股票池；指数口径使用 XBX 成分股标记列。
SUPPORTED_SYMBOLS = ["全部A股", "沪深300", "上证50", "中证500", "中证1000"]
SYMBOL_COMPONENT_COLUMNS = {
    "沪深300": "沪深300成分股",
    "上证50": "上证50成分股",
    "中证500": "中证500成分股",
    "中证1000": "中证1000成分股",
}
BASE_XBX_COLUMNS = ["交易日期", "股票代码", "总市值", "净资产"]


def _required_xbx_columns(symbol: str) -> list[str]:
    columns = list(BASE_XBX_COLUMNS)
    component_col = SYMBOL_COMPONENT_COLUMNS.get(symbol)
    if component_col:
        columns.append(component_col)
    return columns


def _load_xbx_stock_data(symbol: str, stock_data: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """加载计算破净率所需的 XBX 字段。"""
    required_cols = _required_xbx_columns(symbol)
    if stock_data is not None:
        missing = [col for col in required_cols if col not in stock_data.columns]
        if missing:
            raise ValueError(f"XBX数据缺少必要列: {', '.join(missing)}")
        return stock_data[required_cols].copy()

    parquet_path = RAW_DATA_DIR / "xbx_stock_data.parquet"
    if not parquet_path.exists():
        raise FileNotFoundError(f"XBX数据文件不存在: {parquet_path}")

    try:
        return pd.read_parquet(parquet_path, columns=required_cols)
    except Exception as e:
        raise IOError(f"读取XBX数据失败: {e}")


def calculate_below_net_asset_statistics_from_xbx(
    stock_data: pd.DataFrame,
    symbol: str = "全部A股",
) -> pd.DataFrame:
    """
    从 XBX 个股明细计算破净股统计。

    口径：
    - PB = 总市值 / 净资产
    - 分母：PB > 0 的股票，PB < 0（净资产为负）或缺失值不纳入统计
    - 分子：0 < PB < 1 的股票
    - 全部A股使用 XBX 当前股票池；指数使用对应成分股标记列
    """
    if symbol not in SUPPORTED_SYMBOLS:
        raise ValueError(f"不支持的symbol: {symbol}。支持的symbol: {', '.join(SUPPORTED_SYMBOLS)}")

    df = _load_xbx_stock_data(symbol, stock_data)

    component_col = SYMBOL_COMPONENT_COLUMNS.get(symbol)
    if component_col:
        df = df[df[component_col].eq("Y")].copy()

    if df.empty:
        raise ValueError(f"XBX数据为空，无法计算破净股统计: {symbol}")

    df["交易日期"] = pd.to_datetime(df["交易日期"], errors="coerce")
    df["总市值"] = pd.to_numeric(df["总市值"], errors="coerce")
    df["净资产"] = pd.to_numeric(df["净资产"], errors="coerce")
    df["pb"] = df["总市值"] / df["净资产"]

    valid = df[
        df["交易日期"].notna()
        & df["总市值"].gt(0)
        & df["pb"].gt(0)
        & np.isfinite(df["pb"])
    ].copy()
    if valid.empty:
        raise ValueError(f"没有可统计的有效PB数据: {symbol}")

    valid["below_net_asset_flag"] = valid["pb"].lt(1)
    daily = (
        valid.groupby("交易日期", observed=True)
        .agg(
            below_net_asset=("below_net_asset_flag", "sum"),
            total_company=("股票代码", "count"),
        )
        .reset_index()
        .rename(columns={"交易日期": "date"})
        .sort_values("date", ignore_index=True)
    )
    daily["below_net_asset"] = daily["below_net_asset"].astype(int)
    daily["total_company"] = daily["total_company"].astype(int)
    daily["below_net_asset_ratio"] = (
        daily["below_net_asset"] / daily["total_company"]
    ).round(4)

    return daily[["date", "below_net_asset", "total_company", "below_net_asset_ratio"]]


def stock_a_below_net_asset_statistics(
    symbol: str = "全部A股",
    stock_data: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """
    破净股统计历史走势。

    该函数保留原 AkShare 同名接口的本地兼容入口，但数据源已切换为 XBX。
    :param symbol: choice of {"全部A股", "沪深300", "上证50", "中证500"}
    :type symbol: str
    :param stock_data: 可选的 XBX 个股明细 DataFrame
    :return: 破净股统计历史走势
    :rtype: pandas.DataFrame
    """
    return calculate_below_net_asset_statistics_from_xbx(stock_data, symbol=symbol)


def fetch_stock_a_below_net_asset_statistics(
    symbol: str = "全部A股",
    save_raw: bool = True,
    stock_data: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """
    获取A股破净股统计数据
    
    Args:
        symbol: 统计范围，默认为"全部A股"。
            支持的值：
            - "全部A股"
            - "沪深300"
            - "上证50"
            - "中证500"
        save_raw: 是否保存原始数据到raw_data目录，默认True
        stock_data: 可选的 XBX 个股明细 DataFrame，传入可避免重复读取Parquet
        
    Returns:
        包含数据的字典:
        {
            'success': 是否成功,
            'data': pandas DataFrame,
            'raw_file_path': 原始数据文件路径,
            'error': 错误信息（如果有）
        }
    """
    try:
        if symbol not in SUPPORTED_SYMBOLS:
            return {
                'success': False,
                'data': None,
                'raw_file_path': None,
                'error': f"不支持的symbol: {symbol}。支持的symbol: {', '.join(SUPPORTED_SYMBOLS)}"
            }
        df = stock_a_below_net_asset_statistics(symbol=symbol, stock_data=stock_data)
        latest_date = pd.to_datetime(df["date"]).max().strftime("%Y-%m-%d")
        print(f"[INFO] 已通过 XBX 数据计算破净股统计(symbol='{symbol}')，最新日期 {latest_date}。")
        # 验证数据是否为空
        if df is None or df.empty:
            return {
                'success': False,
                'data': None,
                'raw_file_path': None,
                'error': f"获取的数据为空，请检查symbol是否正确: {symbol}"
            }
            
        # 确保日期列格式正确
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'])
            # 排序
            df = df.sort_values(by='date')
        
        # 保存原始数据
        raw_file_path = None
        if save_raw:
            # 确保raw_data目录存在
            raw_data_dir = Path(RAW_DATA_DIR)
            raw_data_dir.mkdir(parents=True, exist_ok=True)
            
            # 生成文件名：stock_a_below_net_asset_statistics_{symbol}.csv
            # 注意：如果symbol包含空格或特殊字符，可能需要处理，但目前"全部A股"等是安全的
            safe_symbol = symbol.replace(" ", "_")
            filename = f"stock_a_below_net_asset_statistics_{safe_symbol}.csv"
            file_path = raw_data_dir / filename
            
            # 保存为CSV文件（覆盖之前的文件）
            df.to_csv(file_path, index=False, encoding='utf-8-sig')
            raw_file_path = str(file_path)
        
        return {
            'success': True,
            'data': df,
            'raw_file_path': raw_file_path,
            'error': None
        }
        
    except Exception as e:
        return {
            'success': False,
            'data': None,
            'raw_file_path': None,
            'error': f"获取数据失败: {str(e)}"
        }

if __name__ == "__main__":
    
    # 测试：下载所有支持的symbol数据
    print("=" * 60)
    print("开始下载A股破净股统计数据")
    print("=" * 60)
    print(f"支持的symbol: {', '.join(SUPPORTED_SYMBOLS)}")
    print("=" * 60)
    print()
    
    timestamp = datetime.now()
    results = {}
    success_count = 0
    failed_count = 0
    
    print(f"正在下载: 全部A股 ...")
    result = fetch_stock_a_below_net_asset_statistics(symbol="全部A股", save_raw=True)
    results["全部A股"] = result
    
    if result['success']:
        success_count += 1
        print(f"  [成功] 数据条数: {len(result['data'])}, 保存至: {result['raw_file_path']}")
    else:
        failed_count += 1
        print(f"  [失败] 错误信息: {result['error']}")
            
    print()
    print("=" * 60)
    print("下载结果汇总")
    print("=" * 60)
    print(f"执行时间: {timestamp}")
    print(f"成功数量: {success_count}")
    print(f"失败数量: {failed_count}")
    print("=" * 60)
    print("下载完成！")

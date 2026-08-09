"""
股票指数历史数据获取模块

使用akshare库获取股票指数的历史行情数据（日频率）。
"""

import pandas as pd
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime

try:
    import akshare as ak
except ImportError:
    raise ImportError("请先安装akshare库: pip install akshare")

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

# 支持的指数代码映射（symbol -> 中文名称）
SYMBOL_NAME_MAP = {
    "sh000001": "上证指数",
    "sz399006": "创业板指",
    "sh000300": "沪深300",
    "sz399303": "国证2000",
    "sz399552": "中证500",
    "sz399001": "深证成指",
    "sz399005": "中小板指",
    "sh000016": "上证50",
    "sz399100": "中证1000",
    "sz399905": "中证500",
    "sz399006": "创业板指",
}


def fetch_stock_zh_index_daily(
    symbol: str = "sh000001",
    save_raw: bool = True
) -> Dict[str, Any]:
    """
    获取股票指数的历史行情数据（日频率）
    
    Args:
        symbol: 指数代码，格式为"sh000001"（上证指数）或"sz399006"（创业板指）等
            支持的指数代码：
            - sh000001: 上证指数
            - sz399006: 创业板指
            - sh000300: 沪深300
            - sz399303: 国证2000
            - sz399552: 中证500
            - sz399001: 深证成指
            - sz399005: 中小板指
            - sh000016: 上证50
            - sz399100: 中证1000
        save_raw: 是否保存原始数据到raw_data目录，默认True
        
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
        # 调用akshare获取数据
        df = ak.stock_zh_index_daily(symbol=symbol)
        
        # 验证数据是否为空
        if df is None or df.empty:
            return {
                'success': False,
                'data': None,
                'raw_file_path': None,
                'error': f"获取的数据为空，请检查symbol是否正确: {symbol}"
            }
        
        # 保存原始数据
        raw_file_path = None
        if save_raw:
            # 确保raw_data目录存在
            raw_data_dir = Path(RAW_DATA_DIR)
            raw_data_dir.mkdir(parents=True, exist_ok=True)
            
            # 生成文件名：stock_zh_index_daily_{symbol}.csv
            filename = f"stock_zh_index_daily_{symbol}.csv"
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


def fetch_multiple_stock_zh_index_daily(
    symbols: List[str] = None,
    save_raw: bool = True
) -> Dict[str, Any]:
    """
    批量获取多个股票指数的历史行情数据
    
    Args:
        symbols: 指数代码列表，如果为None则默认下载["sh000001", "sz399006", "sh000300", "sz399303"]
        save_raw: 是否保存原始数据到raw_data目录，默认True
        
    Returns:
        包含所有结果的字典:
        {
            'timestamp': 执行时间戳,
            'success': 是否全部成功,
            'results': {
                'sh000001': {
                    'success': 是否成功,
                    'data': pandas DataFrame,
                    'raw_file_path': 原始数据文件路径,
                    'error': 错误信息（如果有）
                },
                # 其他指数的结果...
            },
            'summary': {
                'total_count': 总数量,
                'success_count': 成功数量,
                'failed_count': 失败数量
            }
        }
    """
    if symbols is None:
        symbols = ["sh000001", "sz399006", "sh000300", "sz399303"]
    
    timestamp = datetime.now()
    results = {}
    success_count = 0
    failed_count = 0
    
    for symbol in symbols:
        result = fetch_stock_zh_index_daily(symbol=symbol, save_raw=save_raw)
        results[symbol] = result
        
        if result['success']:
            success_count += 1
        else:
            failed_count += 1
    
    return {
        'timestamp': timestamp.isoformat(),
        'success': failed_count == 0,
        'results': results,
        'summary': {
            'total_count': len(symbols),
            'success_count': success_count,
            'failed_count': failed_count
        }
    }


if __name__ == "__main__":
    # 测试：下载指定的四个指数数据
    symbols = ["sh000001", "sz399006", "sh000300", "sz399303"]
    
    print("=" * 60)
    print("开始下载股票指数历史数据")
    print("=" * 60)
    print(f"目标指数: {', '.join(symbols)}")
    print(f"  - sh000001: 上证指数")
    print(f"  - sz399006: 创业板指")
    print(f"  - sh000300: 沪深300")
    print(f"  - sz399303: 国证2000")
    print("=" * 60)
    print()
    
    result = fetch_multiple_stock_zh_index_daily(
        symbols=symbols,
        save_raw=True
    )
    
    print("=" * 60)
    print("下载结果汇总")
    print("=" * 60)
    print(f"执行时间: {result['timestamp']}")
    print(f"全部成功: {'是' if result['success'] else '否'}")
    print(f"总数量: {result['summary']['total_count']}")
    print(f"成功数量: {result['summary']['success_count']}")
    print(f"失败数量: {result['summary']['failed_count']}")
    print()
    
    print("详细结果:")
    print("-" * 60)
    for symbol, data_result in result['results'].items():
        index_name = SYMBOL_NAME_MAP.get(symbol, symbol)
        
        print(f"\n{index_name} ({symbol}):")
        if data_result['success']:
            print(f"  [成功]")
            print(f"  数据条数: {len(data_result['data'])}")
            print(f"  数据范围: {data_result['data']['date'].min()} 至 {data_result['data']['date'].max()}")
            print(f"  数据列: {', '.join(data_result['data'].columns)}")
            print(f"  保存路径: {data_result['raw_file_path']}")
        else:
            print(f"  [失败]")
            print(f"  错误信息: {data_result['error']}")
    
    print()
    print("=" * 60)
    print("下载完成！")
    print("=" * 60)


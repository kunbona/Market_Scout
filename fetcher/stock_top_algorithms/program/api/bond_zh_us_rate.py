"""
中美国债收益率数据获取模块

使用akshare库获取中美国债收益率数据。
"""

import pandas as pd
from pathlib import Path
from typing import Dict, Any, Optional
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


def fetch_bond_zh_us_rate(
    start_date: str = "20050101",
    save_raw: bool = True
) -> Dict[str, Any]:
    """
    获取中美国债收益率数据
    
    Args:
        start_date: 开始日期，格式为"YYYYMMDD"，例如"20050101"
            数据从1990-12-19开始，默认从2005-01-01开始
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
        # 验证日期格式
        if len(start_date) != 8 or not start_date.isdigit():
            error_msg = f"日期格式错误: {start_date}，应为YYYYMMDD格式，例如20050101"
            return {
                'success': False,
                'data': None,
                'raw_file_path': None,
                'error': error_msg
            }
        
        # 调用akshare获取数据
        df = ak.bond_zh_us_rate(start_date=start_date)
        
        # 保存原始数据
        raw_file_path = None
        if save_raw:
            # 确保raw_data目录存在
            raw_data_dir = Path(RAW_DATA_DIR)
            raw_data_dir.mkdir(parents=True, exist_ok=True)
            
            # 生成文件名：bond_zh_us_rate_{start_date}.csv
            filename = f"bond_zh_us_rate_{start_date}.csv"
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
    # 测试：下载中美国债收益率数据
    print("开始下载中美国债收益率数据...")
    result = fetch_bond_zh_us_rate(
        start_date="20050101",
        save_raw=True
    )
    
    if result['success']:
        print(f"\n成功！")
        print(f"数据条数: {len(result['data'])}")
        print(f"原始数据文件: {result['raw_file_path']}")
        print(f"数据列: {list(result['data'].columns)}")
        print(f"\n前5条数据:")
        print(result['data'].head())
    else:
        print(f"\n失败: {result['error']}")


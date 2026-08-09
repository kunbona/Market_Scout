"""
两融账户信息数据获取模块

使用akshare库获取两融账户信息数据。
"""

import pandas as pd
from pathlib import Path
from typing import Dict, Any, Optional

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


def fetch_stock_margin_account_info(
    save_raw: bool = True
) -> Dict[str, Any]:
    """
    获取两融账户信息数据
    
    Args:
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
        # 接口: stock_margin_account_info()
        df = ak.stock_margin_account_info()
        
        # 验证数据是否为空
        if df is None or df.empty:
            return {
                'success': False,
                'data': None,
                'raw_file_path': None,
                'error': "获取的数据为空"
            }
        
        # 保存原始数据
        raw_file_path = None
        if save_raw:
            # 确保raw_data目录存在
            raw_data_dir = Path(RAW_DATA_DIR)
            raw_data_dir.mkdir(parents=True, exist_ok=True)
            
            # 生成文件名：stock_margin_account_info.csv
            filename = "stock_margin_account_info.csv"
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
    # 测试：获取两融账户信息数据
    
    print("=" * 60)
    print("开始获取两融账户信息数据")
    print("=" * 60)
    
    result = fetch_stock_margin_account_info(
        save_raw=True
    )
    
    print("=" * 60)
    print("获取结果")
    print("=" * 60)
    
    if result['success']:
        print(f"  [成功]")
        print(f"  数据条数: {len(result['data'])}")
        print(f"  数据列: {', '.join(result['data'].columns)}")
        print(f"  保存路径: {result['raw_file_path']}")
        print("\n数据前5行:")
        print(result['data'].head())
    else:
        print(f"  [失败]")
        print(f"  错误信息: {result['error']}")
    
    print()
    print("=" * 60)
    print("测试完成！")
    print("=" * 60)


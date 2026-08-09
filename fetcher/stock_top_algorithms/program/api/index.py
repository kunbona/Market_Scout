"""
API聚合模块

提供一键调用所有API的聚合函数，用于准备所有数据。
"""

from typing import Dict, Any, List, Optional
from datetime import datetime
import sys
from pathlib import Path

# 支持直接运行和作为包导入
if __package__ is None or __package__ == '':
    # 直接运行时，添加路径并使用绝对导入
    parent_dir = Path(__file__).parent.parent.parent
    sys.path.insert(0, str(parent_dir))
    from program.api.stock_index_pe import fetch_stock_index_pe, fetch_multiple_stock_index_pe
    from program.api.bond_zh_us_rate import fetch_bond_zh_us_rate
    from program.api.stock_zh_index_daily import fetch_stock_zh_index_daily, fetch_multiple_stock_zh_index_daily
    from program.api.load_xbx_data import load_xbx_data
    from program.api.stock_ggcg_em import fetch_stock_ggcg_em
    from program.api.stock_margin_account import fetch_stock_margin_account_info
    from program.api.stock_a_below_net_asset_statistics import fetch_stock_a_below_net_asset_statistics
else:
    # 作为包导入时使用相对导入
    from .stock_index_pe import fetch_stock_index_pe, fetch_multiple_stock_index_pe
    from .bond_zh_us_rate import fetch_bond_zh_us_rate
    from .stock_zh_index_daily import fetch_stock_zh_index_daily, fetch_multiple_stock_zh_index_daily
    from .load_xbx_data import load_xbx_data
    from .stock_ggcg_em import fetch_stock_ggcg_em
    from .stock_margin_account import fetch_stock_margin_account_info
    from .stock_a_below_net_asset_statistics import fetch_stock_a_below_net_asset_statistics



def fetch_all_data(
    save_raw: bool = True,
    save_processed: bool = True,
    equity_bond_spread_code: str = "000300.SH",
    stock_index_pe_symbols: Optional[List[str]] = None,
    bond_zh_us_rate_start_date: str = "20050101",
    stock_zh_index_daily_symbols: Optional[List[str]] = None,
    load_xbx_data_enabled: bool = True,
    xbx_stock_data_path: Optional[str] = None,
    xbx_n_jobs: Optional[int] = None,
    xbx_filters: Optional[List[str]] = None,
    xbx_filename: Optional[str] = None,
    stock_ggcg_em_symbol: str = "全部",
    stock_a_below_net_asset_symbol: str = "全部A股"
) -> Dict[str, Any]:
    """
    一键获取所有API数据
    
    调用所有已注册的API接口，获取并保存数据。
    
    Args:
        save_raw: 是否保存原始数据，默认True
        save_processed: 是否保存处理后的数据，默认True
        equity_bond_spread_code: 股权溢价指数股票代码，默认沪深300（000300.SH）
        stock_index_pe_symbols: 股票指数市盈率数据要下载的指数列表，
            如果为None则默认下载["上证50", "沪深300", "中证500", "中证1000"]
        bond_zh_us_rate_start_date: 中美国债收益率数据开始日期，格式为"YYYYMMDD"，
            默认"20050101"，数据从1990-12-19开始
        stock_zh_index_daily_symbols: 股票指数历史数据要下载的指数代码列表，
            如果为None则默认下载["sh000001", "sz399006", "sh000300", "sz399303"]
        load_xbx_data_enabled: 是否加载XBX本地股票交易数据，默认True
        xbx_stock_data_path: XBX股票数据目录路径，如果为None则使用config.XBX_data_path下的stock-trading-data-pro目录
        xbx_n_jobs: XBX数据加载的并行任务数，如果为None则使用config.XBX_DATA_N_JOBS，-1表示使用所有CPU核心
        xbx_filters: XBX数据文件过滤条件列表，默认['bj']（排除包含'bj'的文件）
        xbx_filename: XBX数据保存的文件名（不含路径），如果为None则使用默认文件名"xbx_stock_data"
        stock_ggcg_em_symbol: 股东增减持数据类型，默认"全部"，可选{"全部", "股东增持", "股东减持"}
        stock_a_below_net_asset_symbol: A股破净股统计范围，默认"全部A股"，可选{"全部A股", "沪深300", "上证50", "中证500"}
        
    Returns:
        包含所有API调用结果的字典：
        {
            'timestamp': 执行时间戳,
            'success': 是否全部成功,
            'results': {
                'equity_bond_spread': {
                    'success': 是否成功,
                    'data': API返回的数据,
                    'raw_file_path': 原始数据文件路径,
                    'processed_file_path': 处理后数据文件路径,
                    'error': 错误信息（如果有）
                },
                'xbx_stock_data': {
                    'success': 是否成功,
                    'data': DataFrame数据,
                    'raw_file_path': 保存的文件路径,
                    'processed_file_path': None,
                    'file_count': 加载的文件数量,
                    'error': 错误信息（如果有）
                },
                # 其他API的结果...
            },
            'summary': {
                'total_apis': 总API数量,
                'success_count': 成功数量,
                'failed_count': 失败数量
            }
        }
    """
    timestamp = datetime.now()
    results = {}
    success_count = 0
    failed_count = 0
    xbx_stock_data = None
    
    # 调用股票指数市盈率API
    if fetch_multiple_stock_index_pe is not None:
        try:
            if stock_index_pe_symbols is None:
                stock_index_pe_symbols = ["上证50", "沪深300", "中证500", "中证1000"]
            
            stock_index_pe_result = fetch_multiple_stock_index_pe(
                symbols=stock_index_pe_symbols,
                save_raw=save_raw
            )
            
            # 将每个指数的结果添加到results中
            for symbol, symbol_result in stock_index_pe_result['results'].items():
                results[f'stock_index_pe_{symbol}'] = {
                    'success': symbol_result['success'],
                    'data': symbol_result['data'],
                    'raw_file_path': symbol_result.get('raw_file_path'),
                    'processed_file_path': None,  # 股票指数市盈率API目前不提供处理后数据
                    'error': symbol_result.get('error')
                }
                if symbol_result['success']:
                    success_count += 1
                else:
                    failed_count += 1
        except Exception as e:
            # 如果批量获取失败，尝试单独获取
            if stock_index_pe_symbols is None:
                stock_index_pe_symbols = ["上证50", "沪深300", "中证500", "中证1000"]
            
            for symbol in stock_index_pe_symbols:
                try:
                    symbol_result = fetch_stock_index_pe(symbol=symbol, save_raw=save_raw)
                    results[f'stock_index_pe_{symbol}'] = {
                        'success': symbol_result['success'],
                        'data': symbol_result['data'],
                        'raw_file_path': symbol_result.get('raw_file_path'),
                        'processed_file_path': None,
                        'error': symbol_result.get('error')
                    }
                    if symbol_result['success']:
                        success_count += 1
                    else:
                        failed_count += 1
                except Exception as inner_e:
                    results[f'stock_index_pe_{symbol}'] = {
                        'success': False,
                        'data': None,
                        'raw_file_path': None,
                        'processed_file_path': None,
                        'error': str(inner_e)
                    }
                    failed_count += 1
    
    # 调用中美国债收益率API
    if fetch_bond_zh_us_rate is not None:
        try:
            bond_zh_us_rate_result = fetch_bond_zh_us_rate(
                start_date=bond_zh_us_rate_start_date,
                save_raw=save_raw
            )
            results['bond_zh_us_rate'] = {
                'success': bond_zh_us_rate_result['success'],
                'data': bond_zh_us_rate_result['data'],
                'raw_file_path': bond_zh_us_rate_result.get('raw_file_path'),
                'processed_file_path': None,  # 中美国债收益率API目前不提供处理后数据
                'error': bond_zh_us_rate_result.get('error')
            }
            if bond_zh_us_rate_result['success']:
                success_count += 1
            else:
                failed_count += 1
        except Exception as e:
            results['bond_zh_us_rate'] = {
                'success': False,
                'data': None,
                'raw_file_path': None,
                'processed_file_path': None,
                'error': str(e)
            }
            failed_count += 1
    
    # 调用股票指数历史数据API
    if fetch_multiple_stock_zh_index_daily is not None:
        try:
            if stock_zh_index_daily_symbols is None:
                stock_zh_index_daily_symbols = ["sh000001", "sz399006", "sh000300", "sz399303"]
            
            stock_zh_index_daily_result = fetch_multiple_stock_zh_index_daily(
                symbols=stock_zh_index_daily_symbols,
                save_raw=save_raw
            )
            
            # 将每个指数的结果添加到results中
            for symbol, symbol_result in stock_zh_index_daily_result['results'].items():
                results[f'stock_zh_index_daily_{symbol}'] = {
                    'success': symbol_result['success'],
                    'data': symbol_result['data'],
                    'raw_file_path': symbol_result.get('raw_file_path'),
                    'processed_file_path': None,  # 股票指数历史数据API目前不提供处理后数据
                    'error': symbol_result.get('error')
                }
                if symbol_result['success']:
                    success_count += 1
                else:
                    failed_count += 1
        except Exception as e:
            # 如果批量获取失败，尝试单独获取
            if stock_zh_index_daily_symbols is None:
                stock_zh_index_daily_symbols = ["sh000001", "sz399006", "sh000300", "sz399303"]
            
            for symbol in stock_zh_index_daily_symbols:
                try:
                    symbol_result = fetch_stock_zh_index_daily(symbol=symbol, save_raw=save_raw)
                    results[f'stock_zh_index_daily_{symbol}'] = {
                        'success': symbol_result['success'],
                        'data': symbol_result['data'],
                        'raw_file_path': symbol_result.get('raw_file_path'),
                        'processed_file_path': None,
                        'error': symbol_result.get('error')
                    }
                    if symbol_result['success']:
                        success_count += 1
                    else:
                        failed_count += 1
                except Exception as inner_e:
                    results[f'stock_zh_index_daily_{symbol}'] = {
                        'success': False,
                        'data': None,
                        'raw_file_path': None,
                        'processed_file_path': None,
                        'error': str(inner_e)
                    }
                    failed_count += 1
    
    # 调用XBX本地股票交易数据加载API
    if load_xbx_data_enabled and load_xbx_data is not None:
        try:
            xbx_result = load_xbx_data(
                stock_data_path=xbx_stock_data_path,
                n_jobs=xbx_n_jobs,
                filters=xbx_filters,
                save_to_file=save_raw,  # 使用save_raw参数控制是否保存
                filename=xbx_filename
            )
            results['xbx_stock_data'] = {
                'success': xbx_result['success'],
                'data': xbx_result.get('data'),
                'raw_file_path': xbx_result.get('saved_file_path'),  # 映射saved_file_path到raw_file_path
                'processed_file_path': None,  # XBX数据加载API目前不提供处理后数据
                'file_count': xbx_result.get('file_count', 0),
                'error': xbx_result.get('error')
            }
            if xbx_result['success']:
                xbx_stock_data = xbx_result.get('data')
                success_count += 1
            else:
                failed_count += 1
        except Exception as e:
            results['xbx_stock_data'] = {
                'success': False,
                'data': None,
                'raw_file_path': None,
                'processed_file_path': None,
                'file_count': 0,
                'error': str(e)
            }
            failed_count += 1
            
    # 生成A股破净股统计
    if fetch_stock_a_below_net_asset_statistics is not None:
        try:
            stock_a_below_net_asset_result = fetch_stock_a_below_net_asset_statistics(
                symbol=stock_a_below_net_asset_symbol,
                save_raw=save_raw,
                stock_data=xbx_stock_data
            )
            results['stock_a_below_net_asset_statistics'] = {
                'success': stock_a_below_net_asset_result['success'],
                'data': stock_a_below_net_asset_result['data'],
                'raw_file_path': stock_a_below_net_asset_result.get('raw_file_path'),
                'processed_file_path': None,
                'error': stock_a_below_net_asset_result.get('error')
            }
            if stock_a_below_net_asset_result['success']:
                success_count += 1
            else:
                failed_count += 1
        except Exception as e:
            results['stock_a_below_net_asset_statistics'] = {
                'success': False,
                'data': None,
                'raw_file_path': None,
                'processed_file_path': None,
                'error': str(e)
            }
            failed_count += 1

    # 调用股东增减持API
    # if fetch_stock_ggcg_em is not None:
    #     try:
    #         stock_ggcg_em_result = fetch_stock_ggcg_em(
    #             symbol=stock_ggcg_em_symbol,
    #             save_raw=save_raw
    #         )
    #         results['stock_ggcg_em'] = {
    #             'success': stock_ggcg_em_result['success'],
    #             'data': stock_ggcg_em_result['data'],
    #             'raw_file_path': stock_ggcg_em_result.get('raw_file_path'),
    #             'processed_file_path': None,
    #             'error': stock_ggcg_em_result.get('error')
    #         }
    #         if stock_ggcg_em_result['success']:
    #             success_count += 1
    #         else:
    #             failed_count += 1
    #     except Exception as e:
    #         results['stock_ggcg_em'] = {
    #             'success': False,
    #             'data': None,
    #             'raw_file_path': None,
    #             'processed_file_path': None,
    #             'error': str(e)
    #         }
    #         failed_count += 1
    
    # # 调用两融账户信息API
    # if fetch_stock_margin_account_info is not None:
    #     try:
    #         stock_margin_account_result = fetch_stock_margin_account_info(
    #             save_raw=save_raw
    #         )
    #         results['stock_margin_account_info'] = {
    #             'success': stock_margin_account_result['success'],
    #             'data': stock_margin_account_result['data'],
    #             'raw_file_path': stock_margin_account_result.get('raw_file_path'),
    #             'processed_file_path': None,
    #             'error': stock_margin_account_result.get('error')
    #         }
    #         if stock_margin_account_result['success']:
    #             success_count += 1
    #         else:
    #             failed_count += 1
    #     except Exception as e:
    #         results['stock_margin_account_info'] = {
    #             'success': False,
    #             'data': None,
    #             'raw_file_path': None,
    #             'processed_file_path': None,
    #             'error': str(e)
    #         }
    #         failed_count += 1

    # TODO: 在这里添加其他API的调用
    
    # 汇总结果
    total_apis = success_count + failed_count
    all_success = failed_count == 0
    
    return {
        'timestamp': timestamp.isoformat(),
        'success': all_success,
        'results': results,
        'summary': {
            'total_apis': total_apis,
            'success_count': success_count,
            'failed_count': failed_count
        }
    }


def list_available_apis() -> List[str]:
    """
    列出所有可用的API
    
    Returns:
        API名称列表
    """
    apis = ['equity_bond_spread']  # 股权溢价指数
    
    # 添加股票指数市盈率API（如果可用）
    if fetch_stock_index_pe is not None:
        apis.append('stock_index_pe')  # 股票指数市盈率
    
    # 添加中美国债收益率API（如果可用）
    if fetch_bond_zh_us_rate is not None:
        apis.append('bond_zh_us_rate')  # 中美国债收益率
    
    # 添加股票指数历史数据API（如果可用）
    if fetch_stock_zh_index_daily is not None:
        apis.append('stock_zh_index_daily')  # 股票指数历史数据
    
    # 添加XBX本地股票交易数据加载API（如果可用）
    if load_xbx_data is not None:
        apis.append('xbx_stock_data')  # XBX本地股票交易数据
        
    # 添加A股破净股统计模块（如果可用）
    if fetch_stock_a_below_net_asset_statistics is not None:
        apis.append('stock_a_below_net_asset_statistics')  # A股破净股统计
        
    # 添加股东增减持API（如果可用）
    if fetch_stock_ggcg_em is not None:
        apis.append('stock_ggcg_em')  # 股东增减持数据
    
    # 添加两融账户信息API（如果可用）
    if fetch_stock_margin_account_info is not None:
        apis.append('stock_margin_account_info')  # 两融账户信息
    
    return apis


def print_visual_result(result: Dict[str, Any]) -> None:
    """
    可视化打印API调用结果
    
    使用颜色高亮显示成功和失败的结果。
    
    Args:
        result: fetch_all_data返回的结果字典
    """
    # ANSI颜色代码
    GREEN = '\033[92m'
    RED = '\033[91m'
    BG_GREEN = '\033[42m'
    BG_RED = '\033[41m'
    RESET = '\033[0m'
    
    summary = result.get('summary', {})
    results = result.get('results', {})
    all_success = result.get('success', False)
    
    # 总体状态 - 高亮显示
    total_apis = summary.get('total_apis', 0)
    success_count = summary.get('success_count', 0)
    failed_count = summary.get('failed_count', 0)
    
    if all_success:
        # 全部通过 - 绿色背景高亮
        print(f"\n{BG_GREEN} 全部通过: {success_count}/{total_apis} 个API成功 {RESET}\n")
    else:
        # 有失败 - 红色背景高亮
        print(f"\n{BG_RED} 部分失败: {success_count}/{total_apis} 成功, {failed_count} 失败 {RESET}\n")
    
    # 详细结果
    for api_name, api_result in results.items():
        success = api_result.get('success', False)
        error = api_result.get('error')
        
        if success:
            print(f"{GREEN}✓{RESET} {api_name}")
        else:
            print(f"{RED}✗{RESET} {api_name}")
            if error:
                print(f"  {RED}错误: {error}{RESET}")
    
    print()


if __name__ == "__main__":
    result = fetch_all_data(
        save_raw=True, 
        save_processed=True, 
        equity_bond_spread_code="000300.SH",
        bond_zh_us_rate_start_date="20050101"
    )
    print_visual_result(result)

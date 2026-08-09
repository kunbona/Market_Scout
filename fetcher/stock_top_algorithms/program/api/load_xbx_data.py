"""
XBX本地数据加载模块

从本地目录加载XBX股票交易数据，支持并行加载多个CSV文件。
"""

import os
import pandas as pd
from pathlib import Path
from typing import Dict, Any, List, Optional
from joblib import Parallel, delayed
from tqdm import tqdm
from decimal import ROUND_UP


# 支持直接运行和作为包导入
try:
    from config import XBX_data_path, DATA_START_DATE, INDICATOR_START_DATE, RAW_DATA_DIR, XBX_DATA_N_JOBS
except ImportError:
    # 如果直接运行，尝试从上级目录导入
    import sys
    from pathlib import Path
    parent_dir = Path(__file__).parent.parent.parent
    sys.path.insert(0, str(parent_dir))
    from config import XBX_data_path, DATA_START_DATE, INDICATOR_START_DATE, RAW_DATA_DIR, XBX_DATA_N_JOBS


# 定义股票数据所需的列
DATA_COLS = [
    "股票代码",
    "股票名称",
    "交易日期",
    "开盘价",
    "最高价",
    "最低价",
    "收盘价",
    "前收盘价",
    "成交量",
    "成交额",
    "流通市值",
    "总市值",
]


def get_file_in_folder(folder_path: str, extension: str = '.csv', filters: Optional[List[str]] = None) -> List[str]:
    """
    获取文件夹中指定扩展名的文件列表，支持过滤
    
    Args:
        folder_path: 文件夹路径
        extension: 文件扩展名，如'.csv'
        filters: 过滤条件列表，如果文件名包含列表中的任何字符串，则排除该文件
        
    Returns:
        文件路径列表（完整路径）
    """
    folder = Path(folder_path)
    if not folder.exists():
        return []
    
    files = []
    for file_path in folder.iterdir():
        if file_path.is_file() and file_path.suffix.lower() == extension.lower():
            # 检查是否需要过滤
            if filters:
                should_exclude = any(filter_str in file_path.name for filter_str in filters)
                if should_exclude:
                    continue
            files.append(str(file_path))
    
    return files


def read_local_data_from_csv(code_path: str, encoding: str = 'gbk') -> Optional[pd.DataFrame]:
    """
    从CSV文件读取本地数据，兼容各种表头格式
    
    Args:
        code_path: CSV文件路径
        encoding: 文件编码，默认'gbk'
        
    Returns:
        pandas DataFrame，如果读取失败返回None
    """
    try:
        # 尝试直接读取
        df = pd.read_csv(code_path, encoding=encoding, parse_dates=['交易日期'])
    except Exception:
        try:
            # 如果失败，尝试跳过第一行（兼容某些格式）
            df = pd.read_csv(code_path, encoding=encoding, skiprows=1, parse_dates=['交易日期'])
        except Exception as e:
            print(f"读取文件失败 {code_path}: {str(e)}")
            return None
    
    return df


def cal_fuquan_price(df, fuquan_type="后复权", method=None):
    """
    用于计算复权价格

    参数:
    df (DataFrame): 必须包含的字段：收盘价，前收盘价，开盘价，最高价，最低价
    fuquan_type (str, optional): 复权类型，可选值为 '前复权' 或 '后复权'，默认为 '后复权'
    method (str, optional): 额外计算复权价格的方法，如 '开盘'，默认为 None

    返回:
    DataFrame: 最终输出的df中，新增字段：收盘价_复权，开盘价_复权，最高价_复权，最低价_复权
    """

    # 计算复权因子
    fq_factor = (df["收盘价"] / df["前收盘价"]).cumprod()

    # 计算前复权或后复权收盘价
    if fuquan_type == "后复权":  # 如果使用后复权方法
        fq_close = fq_factor * (df.iloc[0]["收盘价"] / fq_factor.iloc[0])
    elif fuquan_type == "前复权":  # 如果使用前复权方法
        fq_close = fq_factor * (df.iloc[-1]["收盘价"] / fq_factor.iloc[-1])
    else:  # 如果给的复权方法非上述两种标准方法会报错
        raise ValueError(f"计算复权价时，出现未知的复权类型：{fuquan_type}")

    # 计算其他价格的复权值
    fq_open = df["开盘价"] / df["收盘价"] * fq_close
    fq_high = df["最高价"] / df["收盘价"] * fq_close
    fq_low = df["最低价"] / df["收盘价"] * fq_close

    # 一次性赋值，提高计算效率
    df = df.assign(
        复权因子=fq_factor, 收盘价_复权=fq_close, 开盘价_复权=fq_open, 最高价_复权=fq_high, 最低价_复权=fq_low
    )

    # 如果指定了额外的方法，计算该方法的复权价格
    if method and method != "开盘":
        df[f"{method}_复权"] = df[method] / df["收盘价"] * fq_close

    # 删除中间变量复权因子
    # df.drop(columns=['复权因子'], inplace=True)

    return df


def cal_zdt_price(df):
    """
    计算股票当天的涨跌停价格。在计算涨跌停价格的时候，按照严格的四舍五入。
    包含ST股，但是不包含新股。

    涨跌停制度规则:
        ---2020年8月23日
        非ST股票 10%
        ST股票 5%

        ---2020年8月24日至今
        普通非ST股票 10%
        普通ST股票 5%

        科创板（sh68） 20%（一直是20%，不受时间限制）
        创业板（sz3） 20%
        科创板和创业板即使ST，涨跌幅限制也是20%

        北交所（bj） 30%

    参数:
    df (DataFrame): 必须得是日线数据。必须包含的字段：前收盘价，开盘价，最高价，最低价

    返回:
    DataFrame: 包含涨停价、跌停价、一字涨停、一字跌停、开盘涨停、开盘跌停等字段的DataFrame
    """
    from decimal import Decimal, ROUND_HALF_UP, ROUND_DOWN

    # 计算普通股票的涨停价和跌停价
    cond = df["股票名称"].str.contains("ST")
    df["涨停价"] = df["前收盘价"] * 1.1
    df["跌停价"] = df["前收盘价"] * 0.9
    df.loc[cond, "涨停价"] = df["前收盘价"] * 1.05
    df.loc[cond, "跌停价"] = df["前收盘价"] * 0.95

    # 计算科创板和新规后的创业板的涨停价和跌停价
    rule_kcb = df["股票代码"].str.contains("sh68")  # 科创板
    new_rule_cyb = (df["交易日期"] > pd.to_datetime("2020-08-23")) & df["股票代码"].str.contains(
        "sz3"
    )  # 新规后的创业板
    merge_rule = rule_kcb | new_rule_cyb
    df.loc[merge_rule, "涨停价"] = df["前收盘价"] * 1.2
    df.loc[merge_rule, "跌停价"] = df["前收盘价"] * 0.8

    # 计算北交所的涨停价和跌停价
    cond_bj = df["股票代码"].str.contains("bj")
    df.loc[cond_bj, "涨停价"] = df["前收盘价"] * 1.3
    df.loc[cond_bj, "跌停价"] = df["前收盘价"] * 0.7

    # 感谢郭毅老板提供的代码，https://bbs.quantclass.cn/thread/55667
    def price_round(number: float, *, ndigits: int = 2, rounding: str = ROUND_HALF_UP) -> float:
        """对价格进行凑整处理

        北交所规定“超过涨跌幅限制的申报为无效申报”，因此需要对涨跌停价采取截断操作，
        其余市场的涨跌停价及常规价格均采取四舍五入方式凑整。

        Args:
            number (float): 价格（非负数）
            ndigits (int, optional): 价格精度（非负数），默认为2
            rounding (str, optional): 凑整方式，支持如下：
                ROUND_HALF_UP - 默认，四舍五入
                ROUND_UP - 向上取整，用于北交所跌停价计算
                ROUND_DOWN - 向下取整，用于北交所涨停价计算

        Returns:
            float: 凑整后的结果
        """
        return float(
            Decimal(number + (-1e-7 if rounding == ROUND_UP else 1e-7)).quantize(
                Decimal(f"0.{'0' * ndigits}"), rounding
            )
        )

    # 涨跌停价格凑整，北交所截断，其他市场四舍五入
    # apply太慢了，改用np+for循环
    # df["涨停价"] = np.where(
    #     cond_bj, df["涨停价"].apply(lambda x: price_round(x, rounding=ROUND_DOWN)), df["涨停价"].apply(price_round)
    # )
    # df["跌停价"] = np.where(
    #     cond_bj, df["跌停价"].apply(lambda x: price_round(x, rounding=ROUND_UP)), df["跌停价"].apply(price_round)
    # )

    zt_price = df["涨停价"].values
    dt_price = df["跌停价"].values
    bj_mask = cond_bj.values

    # 批量处理
    results = [
        (
            (price_round(zt_price[i], rounding=ROUND_DOWN), price_round(dt_price[i], rounding=ROUND_UP))
            if bj_mask[i]
            else (price_round(zt_price[i]), price_round(dt_price[i]))
        )
        for i in range(len(df))
    ]
    zhang_results, die_results = zip(*results)
    # 一次性赋值
    df["涨停价"] = zhang_results
    df["跌停价"] = die_results

    # 判断是否一字涨停
    df["一字涨停"] = False
    df.loc[df["最低价"] >= df["涨停价"], "一字涨停"] = True

    # 判断是否一字跌停
    df["一字跌停"] = False
    df.loc[df["最高价"] <= df["跌停价"], "一字跌停"] = True

    # 判断是否开盘涨停
    df["开盘涨停"] = False
    df.loc[df["开盘价"] >= df["涨停价"], "开盘涨停"] = True

    # 判断是否开盘跌停
    df["开盘跌停"] = False
    df.loc[df["开盘价"] <= df["跌停价"], "开盘跌停"] = True

    return df


def _process_stock_data(code: str) -> pd.DataFrame:
    """
    处理单个股票数据文件
    
    Args:
        code: 股票数据文件路径
        
    Returns:
        处理后的DataFrame，如果失败返回空DataFrame
    """
    # 读取股票数据
    df = read_local_data_from_csv(code)
    
    # 股票退市时间小于指数开始时间，就会出现空值
    if df is None or df.empty:
        # 如果出现这种情况，返回空的DataFrame用于后续操作
        return pd.DataFrame(columns=[*DATA_COLS])

    # 按交易日期排序
    df = df.sort_values(by='交易日期', ascending=True)
    
    pct_change = df["收盘价"] / df["前收盘价"] - 1
    turnover_rate = df["成交额"] / df["流通市值"]
    trading_days = df.index.astype("int") + 1
    avg_price = df["成交额"] / df["成交量"]

    # 一次性赋值提高性能
    df = df.assign(涨跌幅=pct_change, 换手率=turnover_rate, 上市至今交易天数=trading_days, 均价=avg_price)
    # 复权价计算及涨跌停价格计算
    df = cal_fuquan_price(df, fuquan_type="后复权")
    df = cal_zdt_price(df)
    
    return df


def save_xbx_data_to_file(df: pd.DataFrame, filename: Optional[str] = None) -> Optional[str]:
    """
    保存XBX数据到文件，优先使用Parquet格式（速度快、压缩率高），如果不可用则使用Pickle格式
    
    Args:
        df: 要保存的DataFrame
        filename: 文件名（不含路径），如果为None则使用默认文件名"xbx_stock_data"
        
    Returns:
        保存的文件路径（字符串），如果保存失败返回None
    """
    try:
        # 确保raw_data目录存在
        raw_data_dir = Path(RAW_DATA_DIR)
        raw_data_dir.mkdir(parents=True, exist_ok=True)
        
        # 确定文件名
        if filename is None:
            filename = "xbx_stock_data"
        
        # 移除文件扩展名（如果有）
        base_name = Path(filename).stem
        
        # 优先尝试使用Parquet格式（速度快、压缩率高、保持数据类型）
        try:
            import pyarrow
            file_path = raw_data_dir / f"{base_name}.parquet"
            df.to_parquet(file_path, engine='pyarrow', compression='snappy', index=False)
            print(f"数据已保存为Parquet格式: {file_path}")
            return str(file_path)
        except ImportError:
            # 如果没有pyarrow，使用Pickle格式（速度快、完全保留DataFrame信息）
            file_path = raw_data_dir / f"{base_name}.pkl"
            df.to_pickle(file_path)
            print(f"数据已保存为Pickle格式: {file_path}")
            return str(file_path)
        except Exception as e:
            # Parquet保存失败，fallback到Pickle
            print(f"Parquet格式保存失败，改用Pickle格式: {str(e)}")
            file_path = raw_data_dir / f"{base_name}.pkl"
            df.to_pickle(file_path)
            print(f"数据已保存为Pickle格式: {file_path}")
            return str(file_path)
            
    except Exception as e:
        print(f"保存数据失败: {str(e)}")
        return None


def load_xbx_data(
    stock_data_path: Optional[str] = None,
    n_jobs: Optional[int] = None,
    filters: Optional[List[str]] = None,
    start_date: Optional[str] = None,
    save_to_file: bool = True,
    filename: Optional[str] = None
) -> Dict[str, Any]:
    """
    加载XBX本地股票交易数据
    
    Args:
        stock_data_path: 股票数据目录路径，如果为None则使用config.XBX_data_path下的stock-trading-data-pro目录
        n_jobs: 并行任务数，如果为None则使用config.XBX_DATA_N_JOBS，-1表示使用所有CPU核心
        filters: 文件过滤条件列表，默认['bj']（排除包含'bj'的文件）
        start_date: 交易日期过滤的开始日期（格式：YYYY-MM-DD），如果为None则使用config.DATA_START_DATE
        save_to_file: 是否保存数据到文件，默认True
        filename: 保存的文件名（不含路径），如果为None则使用默认文件名"xbx_stock_data"
        
    Returns:
        包含数据的字典:
        {
            'success': 是否成功,
            'data': pandas DataFrame,
            'file_count': 加载的文件数量,
            'saved_file_path': 保存的文件路径（如果保存成功）,
            'error': 错误信息（如果有）
        }
    """
    try:
        # 确定数据路径
        if stock_data_path is None:
            base_path = Path(XBX_data_path)
            stock_data_path = str(base_path / "stock-trading-data-pro")
        else:
            stock_data_path = str(Path(stock_data_path))
        
        # 确定并行任务数
        if n_jobs is None:
            n_jobs = XBX_DATA_N_JOBS
        
        # 检查路径是否存在
        if not os.path.exists(stock_data_path):
            warning_msg = f"⚠ 警告: XBX数据路径不存在: {stock_data_path}\n" \
                          f"  请确保 config.py 中的 XBX_data_path 配置正确，且包含 stock-trading-data-pro 目录。\n" \
                          f"  相关依赖指标 (如价格分位数、MA250 Bias、拥挤度) 将被跳过。"
            print(warning_msg)
            return {
                'success': False,
                'data': None,
                'file_count': 0,
                'saved_file_path': None,
                'error': f"数据路径不存在: {stock_data_path}"
            }
        
        # 设置默认过滤条件
        if filters is None:
            filters = ['bj']
        
        # 获取所有CSV文件列表
        stock_code_list = get_file_in_folder(stock_data_path, '.csv', filters=filters)
        
        if not stock_code_list:
            return {
                'success': False,
                'data': None,
                'file_count': 0,
                'saved_file_path': None,
                'error': f"在路径 {stock_data_path} 中未找到符合条件的CSV文件"
            }
        
        # 使用并行处理加载数据
        print(f"开始并行加载 {len(stock_code_list)} 个文件...")
        df_list = Parallel(n_jobs=n_jobs)(
            delayed(_process_stock_data)(code) 
            for code in tqdm(stock_code_list, desc="加载数据")
        )
        
        # 过滤掉空DataFrame
        df_list = [df for df in df_list if not df.empty]
        
        if not df_list:
            return {
                'success': False,
                'data': None,
                'file_count': 0,
                'error': "所有文件读取后都为空"
            }
        
        # 合并所有数据
        df = pd.concat(df_list, ignore_index=True)
        
        # 按交易日期排序
        df.sort_values(by='交易日期', ascending=True, inplace=True)
        
        # 使用config.DATA_START_DATE过滤交易日期，用于加载足够的历史数据支持指标计算
        if start_date is None:
            start_date = DATA_START_DATE
        
        if start_date:
            start_datetime = pd.to_datetime(start_date)
            original_count = len(df)
            df = df[df['交易日期'] >= start_datetime].copy()
            filtered_count = len(df)
            
            if filtered_count < original_count:
                print(f"已过滤交易日期：保留 {start_date} 之后的数据（过滤前: {original_count} 行，过滤后: {filtered_count} 行）")
        
        # 如果成功，保存数据到文件
        saved_file_path = None
        if save_to_file:
            saved_file_path = save_xbx_data_to_file(df, filename)
        
        return {
            'success': True,
            'data': df,
            'file_count': len(df_list),
            'saved_file_path': saved_file_path,
            'error': None
        }
        
    except Exception as e:
        return {
            'success': False,
            'data': None,
            'file_count': 0,
            'saved_file_path': None,
            'error': f"加载数据失败: {str(e)}"
        }


if __name__ == "__main__":
    # 测试：加载XBX数据
    print("=" * 60)
    print("开始加载XBX本地股票交易数据")
    print("=" * 60)
    
    result = load_xbx_data(
        stock_data_path=None,  # 使用config中的路径
        n_jobs=None,  # 使用config中的配置
        filters=['bj']  # 排除包含'bj'的文件
    )
    
    print("=" * 60)
    print("加载结果")
    print("=" * 60)
    
    if result['success']:
        print(f"[成功]")
        print(f"加载文件数量: {result['file_count']}")
        print(f"数据总行数: {len(result['data'])}")
        print(f"数据列: {', '.join(result['data'].columns)}")
        
        if '交易日期' in result['data'].columns:
            print(f"数据日期范围: {result['data']['交易日期'].min()} 至 {result['data']['交易日期'].max()}")
        
        if result.get('saved_file_path'):
            print(f"数据已保存到: {result['saved_file_path']}")
        
        print(f"\n数据预览（前5行）:")
        print(result['data'].head())
        
        print(f"\n数据统计信息:")
        print(result['data'].describe())
    else:
        print(f"[失败]")
        print(f"错误信息: {result['error']}")
    
    print()
    print("=" * 60)
    print("加载完成！")
    print("=" * 60)


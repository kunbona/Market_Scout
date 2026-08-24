"""
股票指数市盈率数据获取模块

使用akshare库获取股票指数的市盈率数据。
包含兼容性修复：当akshare因日期格式变化导致失败时，使用自定义实现作为fallback。
"""

import pandas as pd
import requests
import importlib
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

# 支持的指数列表
SUPPORTED_SYMBOLS = [
    "上证50", "沪深300", "上证380", "创业板50", "中证500", 
    "上证180", "深证红利", "深证100", "中证1000", 
    "上证红利", "中证100", "中证800"
]

# 指数代码映射（与akshare保持一致）
_SYMBOL_CODE_MAP = {
    "上证50": "000016.SH",
    "沪深300": "000300.SH",
    "上证380": "000009.SH",
    "创业板50": "399673.SZ",
    "中证500": "000905.SH",
    "上证180": "000010.SH",
    "深证红利": "399324.SZ",
    "深证100": "399330.SZ",
    "中证1000": "000852.SH",
    "上证红利": "000015.SH",
    "中证100": "000903.SH",
    "中证800": "000906.SH",
}


def _parse_date_column(series: pd.Series) -> pd.Series:
    """兼容性日期解析：自动处理毫秒时间戳和日期字符串两种格式"""
    if series.empty:
        return series
    sample = series.iloc[0]
    # 如果是数字类型，按毫秒时间戳解析
    if isinstance(sample, (int, float)):
        try:
            return pd.to_datetime(series, unit="ms", utc=True).dt.tz_convert("Asia/Shanghai").dt.date
        except Exception:
            pass
    # 否则按字符串解析
    return pd.to_datetime(series, errors="coerce").dt.date


# legulegu 请求所用 UA（akshare 内部 headers 仅含 User-Agent，这里自带一份，
# 避免依赖 akshare 内部模块的私有变量）
_LEGULEGU_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}

# legulegu 熔断器 (进程内): 站点挂掉/反爬时连续失败计数达到阈值,
# 后续请求 (含 akshare 原生兜底) 快速失败, 避免 4 个指数 × 多轮超时白等 15+ 分钟。
# 成功一次即复位。每个刷新 subprocess 是新进程, 熔断状态不跨次保留。
_LEGU_BREAKER = {"fails": 0, "threshold": 3}


def _get_cookie_csrf(url: str) -> dict:
    """自实现版 get_cookie_csrf。

    替代 akshare.stock_feature.stock_a_indicator.get_cookie_csrf：原版在被
    legulegu 反爬限流时，soup.find('_csrf') 返回 None，再取 .attrs 会抛出
    无意义的 "'NoneType' object has no attribute 'attrs'"。这里显式判 None
    并抛出可读错误，便于上层重试与日志定位。
    """
    from bs4 import BeautifulSoup

    session = requests.Session()
    session.headers.update(_LEGULEGU_HEADERS)
    r = session.get(url, timeout=15)
    soup = BeautifulSoup(r.text, features="lxml")
    csrf_tag = soup.find(name="meta", attrs={"name": "_csrf"})
    if csrf_tag is None or not csrf_tag.get("content"):
        # 反爬/限流时页面不含 _csrf token，给出明确原因
        raise RuntimeError(
            f"未在页面中找到 _csrf token（疑似被 legulegu 反爬限流，HTTP {r.status_code}）"
        )
    csrf_token = csrf_tag["content"]
    local_headers = dict(_LEGULEGU_HEADERS)
    local_headers["X-CSRF-Token"] = csrf_token
    return {"cookies": r.cookies, "headers": local_headers}


def _fetch_stock_index_pe_lg_fallback(symbol: str) -> pd.DataFrame:
    """
    直接从 legulegu API 获取指数市盈率数据（项目主用实现）。

    akshare 1.18.5 的 stock_index_pe_lg 硬编码用 unit='ms' 解析日期，但 legulegu
    现在返回日期字符串，导致每次调用必然失败。本实现复用 akshare 的 token 生成
    （hash_code），但自带日期兼容解析与 CSRF 获取，并包含带抖动的重试以应对反爬限流。
    """
    import time
    import random

    # legulegu 熔断器: 站点整体挂掉时 (连接错误/反爬), 每个指数要 3 次重试
    # + akshare 原生兜底 (内部也请求 legulegu, 无超时控制), 4 个指数白等 15+ 分钟。
    # 连续失败达到阈值后, 本进程内后续请求直接快速失败, 复用磁盘旧数据。
    if _LEGU_BREAKER["fails"] >= _LEGU_BREAKER["threshold"]:
        raise ConnectionError(
            f"legulegu 熔断中 (前面已连续失败 {_LEGU_BREAKER['fails']} 次), "
            f"跳过 {symbol} 的请求, 复用磁盘旧数据"
        )

    try:
        import py_mini_racer
        # hash_code 在不同版本的akshare中位置不同，逐一尝试
        hash_code = None
        for _mod_name in [
            "akshare.stock_feature.stock_a_pe_and_pb",
            "akshare.stock_feature.stock_a_indicator",
            "akshare.stock_feature.stock_a_below_net_asset_statistics",
        ]:
            try:
                _mod = importlib.import_module(_mod_name)
                if hasattr(_mod, 'hash_code'):
                    hash_code = _mod.hash_code
                    break
            except Exception:
                continue
        if hash_code is None:
            raise RuntimeError("无法在akshare中找到hash_code")
    except ImportError as e:
        raise RuntimeError(f"无法导入akshare内部模块: {e}")

    js_functions = py_mini_racer.MiniRacer()
    js_functions.eval(hash_code)
    token = js_functions.call("hex", datetime.now().date().isoformat()).lower()

    index_code = _SYMBOL_CODE_MAP.get(symbol)
    if index_code is None:
        raise ValueError(f"不支持的指数: {symbol}")

    url = "https://legulegu.com/api/stockdata/index-basic-pe"
    params = {"token": token, "indexCode": index_code}

    # 带抖动指数退避的请求，应对网站反爬/限流导致CSRF获取失败
    last_error = None
    data_json = None
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            if attempt > 0:
                # 指数退避 + 随机抖动，避免连续请求撞在同一反爬窗口
                backoff = 2 ** attempt + random.uniform(0, 1.5)
                time.sleep(backoff)
            cookie_csrf = _get_cookie_csrf(url="https://legulegu.com/stockdata/sz50-ttm-lyr")
            r = requests.get(url, params=params, timeout=15, **cookie_csrf)
            data_json = r.json()
            last_error = None
            _LEGU_BREAKER["fails"] = 0  # 成功即复位熔断计数
            break
        except Exception as e:
            last_error = e
            _LEGU_BREAKER["fails"] += 1
            print(f"[WARNING] legulegu 请求失败 (尝试 {attempt+1}/{max_attempts}, symbol={symbol}): {e}")

    if last_error is not None:
        raise last_error

    if data_json is None or "data" not in data_json or data_json["data"] is None:
        raise ValueError(f"API返回数据为空 (symbol={symbol})")

    temp_df = pd.DataFrame(data_json["data"])

    if temp_df.empty:
        raise ValueError(f"API返回空数据集 (symbol={symbol})")

    # 兼容性日期解析
    temp_df["date"] = _parse_date_column(temp_df["date"])

    expected_cols = ["date", "close", "lyrPe", "addLyrPe", "middleLyrPe",
                     "ttmPe", "addTtmPe", "middleTtmPe"]
    available_cols = [c for c in expected_cols if c in temp_df.columns]
    temp_df = temp_df[available_cols]

    col_rename = {
        "date": "日期", "close": "指数",
        "lyrPe": "等权静态市盈率", "addLyrPe": "静态市盈率", "middleLyrPe": "静态市盈率中位数",
        "ttmPe": "等权滚动市盈率", "addTtmPe": "滚动市盈率", "middleTtmPe": "滚动市盈率中位数",
    }
    temp_df.columns = [col_rename.get(c, c) for c in temp_df.columns]
    return temp_df


def fetch_stock_index_pe(
    symbol: str = "沪深300",
    save_raw: bool = True
) -> Dict[str, Any]:
    """
    获取股票指数的市盈率数据
    
    Args:
        symbol: 指数名称，可选值：
            "上证50", "沪深300", "上证380", "创业板50", "中证500",
            "上证180", "深证红利", "深证100", "中证1000",
            "上证红利", "中证100", "中证800"
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
    # 验证symbol是否支持
    if symbol not in SUPPORTED_SYMBOLS:
        error_msg = f"不支持的指数名称: {symbol}。支持的指数: {', '.join(SUPPORTED_SYMBOLS)}"
        return {
            'success': False,
            'data': None,
            'raw_file_path': None,
            'error': error_msg
        }
    
    try:
        # 主用 legulegu 实现：akshare 1.18.5 的 stock_index_pe_lg 因日期格式不兼容
        # 每次必然失败，故不再先试它（避免无意义 WARNING + 减少一次反爬请求）。
        # 仅当本实现异常时，才反向兜底尝试 akshare 原生接口。
        try:
            df = _fetch_stock_index_pe_lg_fallback(symbol=symbol)
        except Exception as e:
            if _LEGU_BREAKER["fails"] >= _LEGU_BREAKER["threshold"]:
                # 熔断打开: akshare 原生 stock_index_pe_lg 内部同样请求 legulegu
                # 且无超时控制 (会挂 2-3 分钟), 熔断时直接放弃, 复用磁盘旧数据
                print(f"[WARNING] legulegu 熔断中, 跳过 akshare 原生兜底 ('{symbol}'): {e}")
                return {
                    'success': False,
                    'data': None,
                    'raw_file_path': None,
                    'error': f"legulegu 熔断: {e}",
                }
            print(f"[WARNING] legulegu 直连实现失败 ('{symbol}'): {e}")
            print(f"[INFO] 反向兜底：尝试 akshare 原生 stock_index_pe_lg ...")
            df = ak.stock_index_pe_lg(symbol=symbol)
        
        # 验证数据
        if df is None or df.empty:
            return {
                'success': False,
                'data': None,
                'raw_file_path': None,
                'error': f"获取的数据为空 (symbol={symbol})"
            }
        
        # 保存原始数据
        raw_file_path = None
        if save_raw:
            # 确保raw_data目录存在
            raw_data_dir = Path(RAW_DATA_DIR)
            raw_data_dir.mkdir(parents=True, exist_ok=True)
            
            # 生成文件名：stock_index_pe_{symbol}.csv（不包含时间戳，每次覆盖）
            # 文件名中不能包含特殊字符，替换掉
            safe_symbol = symbol.replace("/", "_").replace("\\", "_")
            filename = f"stock_index_pe_{safe_symbol}.csv"
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


def fetch_multiple_stock_index_pe(
    symbols: List[str] = None,
    save_raw: bool = True
) -> Dict[str, Any]:
    """
    批量获取多个股票指数的市盈率数据
    
    Args:
        symbols: 指数名称列表，如果为None则默认获取"上证50"、"沪深300"、"中证500"、"中证1000"
        save_raw: 是否保存原始数据到raw_data目录，默认True
        
    Returns:
        包含所有结果的字典:
        {
            'timestamp': 执行时间戳,
            'success': 是否全部成功,
            'results': {
                '沪深300': {
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
        symbols = ["上证50", "沪深300", "中证500", "中证1000"]
    
    timestamp = datetime.now()
    results = {}
    success_count = 0
    failed_count = 0
    
    for symbol in symbols:
        result = fetch_stock_index_pe(symbol=symbol, save_raw=save_raw)
        results[symbol] = result
        
        if result['success']:
            success_count += 1
        else:
            failed_count += 1
        
        # 请求间增加延迟，避免触发网站反爬限流
        import time
        time.sleep(1)
    
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
    # 测试：下载指定的四个指数数据（上证50、沪深300、中证500、中证1000）
    print("=" * 60)
    print("开始下载股票指数市盈率数据")
    print("=" * 60)
    print("目标指数: 上证50、沪深300、中证500、中证1000")
    print("=" * 60)
    print()
    
    result = fetch_multiple_stock_index_pe(
        symbols=["上证50", "沪深300", "中证500", "中证1000"],
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
        print(f"\n{symbol}:")
        if data_result['success']:
            print(f"  [成功]")
            print(f"  数据条数: {len(data_result['data'])}")
            if '日期' in data_result['data'].columns:
                print(f"  数据范围: {data_result['data']['日期'].min()} 至 {data_result['data']['日期'].max()}")
            print(f"  数据列: {', '.join(data_result['data'].columns)}")
            print(f"  保存路径: {data_result['raw_file_path']}")
        else:
            print(f"  [失败]")
            print(f"  错误信息: {data_result['error']}")
    
    print()
    print("=" * 60)
    print("下载完成！")
    print("=" * 60)


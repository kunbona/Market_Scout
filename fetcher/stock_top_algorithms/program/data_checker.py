"""
数据新鲜度检查模块

检查各数据源的CSV/Parquet文件是否包含目标交易日的数据。
用于 auto_runner 判断数据是否已就绪。
"""

import pandas as pd
from pathlib import Path
from typing import Dict, Tuple, Optional
from datetime import datetime

from config import RAW_DATA_DIR


# 数据源定义：(文件名, 日期列名, 描述, 是否必需)
DATA_SOURCES = [
    ("stock_index_pe_上证50.csv", "日期", "上证50 PE估值", True),
    ("stock_index_pe_沪深300.csv", "日期", "沪深300 PE估值", True),
    ("stock_index_pe_中证500.csv", "日期", "中证500 PE估值", True),
    ("stock_index_pe_中证1000.csv", "日期", "中证1000 PE估值", True),
    ("bond_zh_us_rate.csv", "日期", "中美国债收益率", True),
    ("stock_zh_index_daily_sh000001.csv", "date", "上证指数日线", True),
    ("stock_zh_index_daily_sz399006.csv", "date", "创业板指日线", False),
    ("stock_zh_index_daily_sh000300.csv", "date", "沪深300日线", False),
    ("stock_zh_index_daily_sz399303.csv", "date", "国证2000日线", False),
    ("stock_a_below_net_asset_statistics_全部A股.csv", "date", "破净股统计", True),
    ("xbx_stock_data.parquet", "交易日期", "XBX股票数据", True),
]


def _resolve_data_file_path(filename: str) -> Path:
    """兼容部分数据源的版本化文件名。"""
    file_path = RAW_DATA_DIR / filename
    if file_path.exists():
        return file_path

    if filename == "bond_zh_us_rate.csv":
        candidates = sorted(RAW_DATA_DIR.glob("bond_zh_us_rate_*.csv"))
        if candidates:
            return candidates[-1]

    return file_path


def _check_file_freshness(
    filename: str,
    date_col: str,
    target_date: str,
) -> Tuple[bool, str]:
    """
    检查单个数据文件是否包含目标日期的数据

    Args:
        filename: 数据文件名
        date_col: 日期列名
        target_date: 目标日期字符串 (YYYY-MM-DD 格式)

    Returns:
        (is_fresh, message): 是否新鲜, 说明信息
    """
    file_path = _resolve_data_file_path(filename)

    if not file_path.exists():
        return False, f"文件不存在: {filename}"

    try:
        # 根据文件扩展名选择加载方式
        if filename.endswith('.parquet'):
            df = pd.read_parquet(file_path, columns=[date_col])
        else:
            # 只读取日期列，提高性能
            df = pd.read_csv(file_path, usecols=[date_col], encoding='utf-8-sig')

        if df.empty:
            return False, f"文件为空: {filename}"

        # 转换日期列
        df[date_col] = pd.to_datetime(df[date_col], errors='coerce')

        # 获取最新日期
        latest_date = df[date_col].max()

        if pd.isna(latest_date):
            return False, f"无法解析日期列: {filename}"

        target_dt = pd.to_datetime(target_date)
        latest_date_str = latest_date.strftime('%Y-%m-%d')

        if latest_date >= target_dt:
            return True, f"✅ {filename}: 最新日期 {latest_date_str}"
        else:
            return False, f"❌ {filename}: 最新日期 {latest_date_str}, 目标 {target_date}"

    except Exception as e:
        return False, f"检查失败 {filename}: {str(e)}"


def _latest_date_of_source(filename: str, date_col: str) -> Optional[pd.Timestamp]:
    """读取单个数据源文件的最新日期，文件缺失或无法解析时返回 None。"""
    file_path = _resolve_data_file_path(filename)
    if not file_path.exists():
        return None
    try:
        if filename.endswith('.parquet'):
            df = pd.read_parquet(file_path, columns=[date_col])
        else:
            df = pd.read_csv(file_path, usecols=[date_col], encoding='utf-8-sig')
        if df.empty:
            return None
        latest = pd.to_datetime(df[date_col], errors='coerce').max()
        return None if pd.isna(latest) else latest
    except Exception:
        return None


def get_source_latest_dates(required_only: bool = True) -> Dict[str, pd.Timestamp]:
    """返回每个数据源的最新日期 {描述: Timestamp}，无法读取的源被跳过。"""
    latest = {}
    for filename, date_col, description, required in DATA_SOURCES:
        if required_only and not required:
            continue
        d = _latest_date_of_source(filename, date_col)
        if d is not None:
            latest[description] = d
    return latest


def get_indicator_baseline_date(required_only: bool = True) -> Optional[str]:
    """指标基准日：所有（必需）数据源最新日期的最小值，格式 YYYY-MM-DD。

    这是所有指标都齐全的最后一个共同日期。分数停在这里，保证每个分数
    都用完整指标集算出，绝不用残缺数据硬凑。无可用源时返回 None。
    """
    latest = get_source_latest_dates(required_only=required_only)
    if not latest:
        return None
    return min(latest.values()).strftime('%Y-%m-%d')


def check_data_consistency(required_only: bool = True) -> Dict:
    """检测各数据源最新日期是否一致。

    Returns:
        {
            'consistent': bool,            # 所有源最新日是否相同
            'baseline': 'YYYY-MM-DD',      # 基准日（min），None 表示无可用源
            'newest': 'YYYY-MM-DD',        # 最新源日期（max）
            'ahead': [{'source','latest','lead_days'}],  # 超前于基准日的源
            'latest_per_source': {描述: 'YYYY-MM-DD'},
        }
    """
    latest = get_source_latest_dates(required_only=required_only)
    if not latest:
        return {
            'consistent': True, 'baseline': None, 'newest': None,
            'ahead': [], 'latest_per_source': {},
        }

    baseline = min(latest.values())
    newest = max(latest.values())
    ahead = []
    for desc, d in latest.items():
        lead = (d - baseline).days
        if lead > 0:
            ahead.append({
                'source': desc,
                'latest': d.strftime('%Y-%m-%d'),
                'lead_days': int(lead),
            })
    ahead.sort(key=lambda x: x['lead_days'], reverse=True)

    return {
        'consistent': baseline == newest,
        'baseline': baseline.strftime('%Y-%m-%d'),
        'newest': newest.strftime('%Y-%m-%d'),
        'ahead': ahead,
        'latest_per_source': {k: v.strftime('%Y-%m-%d') for k, v in latest.items()},
    }


def check_data_freshness(target_date: str, verbose: bool = True) -> Tuple[bool, Dict]:
    """
    检查所有数据源的新鲜度

    Args:
        target_date: 目标交易日期 (YYYY-MM-DD 格式)
        verbose: 是否打印详细信息

    Returns:
        (all_ready, details): 是否全部就绪, 每个数据源的检查结果
    """
    if verbose:
        print(f"\n检查数据新鲜度 (目标日期: {target_date})")
        print("-" * 60)

    details = {}
    all_required_ready = True

    for filename, date_col, description, required in DATA_SOURCES:
        is_fresh, message = _check_file_freshness(filename, date_col, target_date)
        details[description] = {
            'filename': filename,
            'is_fresh': is_fresh,
            'required': required,
            'message': message,
        }

        if verbose:
            status = "✅" if is_fresh else ("❌" if required else "⚠️")
            print(f"  {status} {description}: {message}")

        if required and not is_fresh:
            all_required_ready = False

    if verbose:
        print("-" * 60)
        if all_required_ready:
            print(f"  数据就绪: 所有必需数据源已包含 {target_date} 的数据")
        else:
            missing = [desc for desc, info in details.items() if info['required'] and not info['is_fresh']]
            print(f"  数据未就绪: 缺失 {len(missing)} 个必需数据源: {', '.join(missing)}")
        print()

    return all_required_ready, details


def get_target_trading_date(now: Optional[datetime] = None) -> str:
    """
    获取目标交易日期 (YYYY-MM-DD 格式)

    简单模式：周末回退到周五

    Args:
        now: 当前时间，默认使用 datetime.now()

    Returns:
        目标交易日期字符串 (YYYY-MM-DD)
    """
    from datetime import timedelta

    if now is None:
        now = datetime.now()

    d = now
    # 周末回退到周五
    while d.weekday() >= 5:  # 5=Saturday, 6=Sunday
        d -= timedelta(days=1)

    return d.strftime('%Y-%m-%d')

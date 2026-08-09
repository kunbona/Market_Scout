"""
quant/loader.py — 本地量价数据读取工具

数据路径: DATA_ROOT (QUANT_DATA_ROOT 环境变量)
唯一数据源: stock-trading-data-pro/{code}.csv（GBK 编码, skiprows=1, 38 列）
  包含: 价格 / 成交量 / 市值 / 资金流 / 行业
  不含: up_limit / down_limit（由本模块自行计算）

不依赖 stg_cache / factors 目录，这些目录不保证在用户机器上存在。

并发控制: QUANT_WORKERS 环境变量控制并行工作数（默认 CPU 核数的一半，最少 1）。
  设为 0 或 1 则退化为单线程/单进程串行模式。
  并发后端自动选择:
    - WSL 环境（检测 /proc/version 含 microsoft/WSL）: 使用 ThreadPoolExecutor（多线程），
      避免 fork-after-threads 死锁（Flask 多线程服务器中 fork 子进程会继承锁状态导致死锁）。
    - 其他系统（原生 Linux/macOS）: 使用 ProcessPoolExecutor（多进程），充分利用多核 CPU。
  读取 CSV 属于 I/O 密集型操作，两种后端性能相近。
"""

import logging
import os
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from decimal import Decimal, ROUND_HALF_UP, ROUND_DOWN as _ROUND_DOWN
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

_data_root_env = os.environ.get("QUANT_DATA_ROOT", "").strip()
DATA_ROOT = Path(_data_root_env) if _data_root_env else None


def _is_wsl() -> bool:
    """检测当前是否运行在 WSL（Windows Subsystem for Linux）环境中。"""
    try:
        with open("/proc/version", "r") as f:
            content = f.read().lower()
            return "microsoft" in content or "wsl" in content
    except OSError:
        return False


# 模块级缓存，避免重复读取 /proc/version
_RUNNING_IN_WSL: bool = _is_wsl()


def _get_workers() -> int:
    """从 QUANT_WORKERS 环境变量读取并发工作数，默认 CPU 核数的一半（最少 1）。
    设为 0 或 1 时退化为串行模式（返回 0 表示串行）。
    """
    val = os.environ.get("QUANT_WORKERS", "").strip()
    if val.isdigit():
        n = int(val)
        if n == 0 or n == 1:
            return 0  # 串行模式
        return n
    cpu = os.cpu_count() or 2
    return max(1, cpu // 2)

# ── CSV 列名映射 ─────────────────────────────────────────────────────────────
_TRADING_COL_MAP = {
    "股票代码": "code",
    "股票名称": "name",
    "交易日期": "trade_date",
    "开盘价": "open",
    "最高价": "high",
    "最低价": "low",
    "收盘价": "close",
    "前收盘价": "pre_close",
    "成交量": "vol",
    "成交额": "amount",
    "流通市值": "circ_mv",
    "总市值": "total_mv",
    "新版申万一级行业名称": "industry_l1",
    "新版申万二级行业名称": "industry_l2",
    "新版申万三级行业名称": "industry_l3",
    "机构资金买入额": "inst_buy",
    "机构资金卖出额": "inst_sell",
    "大户资金买入额": "big_buy",
    "大户资金卖出额": "big_sell",
    "中户资金买入额": "mid_buy",
    "中户资金卖出额": "mid_sell",
    "散户资金买入额": "small_buy",
    "散户资金卖出额": "small_sell",
}

_USECOLS = list(_TRADING_COL_MAP.keys())


def _normalize_code(code: str) -> str:
    """将股票代码标准化为带前缀格式 (sh/sz/bj)。"""
    code = code.strip().lower()
    if code.startswith(("sh", "sz", "bj")):
        return code
    num = code[-6:] if len(code) >= 6 else code
    if num.startswith("6"):
        return "sh" + num
    elif num.startswith(("0", "3")):
        return "sz" + num
    elif num.startswith(("4", "8", "2")):
        return "bj" + num
    else:
        return "sh" + num


# ── 核心函数：日截面快照 ──────────────────────────────────────────────────────

def load_daily_snapshot(trade_date: str, lookback: int = 30) -> pd.DataFrame:
    """
    加载指定交易日的全市场截面数据，返回当日一行一股的 DataFrame。

    数据源: stock-trading-data-pro/{code}.csv
    lookback: 向前取多少个交易日（含当日），用于计算连板次数/滚动均值，建议 >= 30。

    返回列:
        code, name, trade_date, open, high, low, close, pre_close,
        vol, amount, circ_mv, total_mv,
        inst_buy, inst_sell, big_buy, big_sell, mid_buy, mid_sell, small_buy, small_sell,
        industry_l1, industry_l2, industry_l3,
        pct_chg, up_limit, down_limit,
        is_zt, is_zb, is_dt,
        inst_net_pct,
        lianzban_cnt,
        amount_mean_5, amount_mean_20
    """
    try:
        df_window = _load_window(trade_date, lookback)
        if df_window.empty:
            logger.warning("[loader] load_daily_snapshot: %s 无数据", trade_date)
            return pd.DataFrame()

        df_window = _enrich(df_window)

        result = df_window[df_window["trade_date"] == trade_date].copy()
        result = result.reset_index(drop=True)

        _output_cols = [
            "code", "name", "trade_date", "open", "high", "low", "close", "pre_close",
            "vol", "amount", "circ_mv", "total_mv",
            "inst_buy", "inst_sell", "big_buy", "big_sell",
            "mid_buy", "mid_sell", "small_buy", "small_sell",
            "industry_l1", "industry_l2", "industry_l3",
            "pct_chg", "up_limit", "down_limit",
            "is_zt", "is_zb", "is_dt",
            "inst_net_pct",
            "lianzban_cnt",
            "amount_mean_5", "amount_mean_20",
        ]
        existing = [c for c in _output_cols if c in result.columns]
        return result[existing]

    except Exception as e:
        logger.error("[loader] load_daily_snapshot 失败 %s: %s", trade_date, e)
        return pd.DataFrame()


def load_daily_range(trade_date: str, days: int = 25) -> pd.DataFrame:
    """
    返回过去 days 个交易日（含当日）的完整多行 DataFrame。
    用于计算需要时间序列的指标（sector_flow_acceleration、advance_decline MA20 等）。
    """
    try:
        lookback = max(days + 5, 30)
        df_window = _load_window(trade_date, lookback)
        if df_window.empty:
            return pd.DataFrame()

        df_window = _enrich(df_window)

        sorted_dates = sorted(df_window["trade_date"].unique())
        if trade_date in sorted_dates:
            td_idx = sorted_dates.index(trade_date)
            keep_dates = set(sorted_dates[max(0, td_idx - days + 1): td_idx + 1])
            df_window = df_window[df_window["trade_date"].isin(keep_dates)].copy()

        return df_window.reset_index(drop=True)

    except Exception as e:
        logger.error("[loader] load_daily_range 失败 %s: %s", trade_date, e)
        return pd.DataFrame()


# ── 内部辅助 ─────────────────────────────────────────────────────────────────

def _read_one_csv(args: tuple) -> "pd.DataFrame | None":
    """线程任务入口：读取单个 CSV，过滤到 trade_date <= cutoff 的 lookback 行。"""
    csv_path, trade_date, lookback, usecols, col_map = args
    try:
        df = pd.read_csv(
            csv_path,
            encoding="GBK",
            skiprows=1,
            usecols=usecols,
            dtype={"股票代码": str},
        )
        df = df.rename(columns=col_map)
        df["trade_date"] = df["trade_date"].astype(str)
        df = df[df["trade_date"] <= trade_date]
        if df.empty:
            return None
        keep_dates = set(sorted(df["trade_date"].unique())[-lookback:])
        df = df[df["trade_date"].isin(keep_dates)]
        return df if not df.empty else None
    except Exception:
        return None


def _load_window(trade_date: str, lookback: int) -> pd.DataFrame:
    """从 stock-trading-data-pro 并行读取 lookback 天窗口数据。

    并发后端选择策略:
      - WSL 环境: ThreadPoolExecutor（多线程），避免 fork-after-threads 死锁
      - 其他系统: ProcessPoolExecutor（多进程），充分利用多核 CPU
      - workers == 0: 串行模式（单线程）
    """
    trading_dir = DATA_ROOT / "stock-trading-data-pro" if DATA_ROOT else None
    if not trading_dir or not trading_dir.exists():
        logger.warning("[loader] stock-trading-data-pro 目录不存在: %s", trading_dir)
        return pd.DataFrame()

    csv_files = list(trading_dir.glob("*.csv"))
    workers = _get_workers()
    use_threads = _RUNNING_IN_WSL
    backend = "thread" if use_threads else "process"
    logger.info("[loader] 读取 %d 个 CSV，窗口: %s 前 %d 天，workers=%d，backend=%s%s",
                len(csv_files), trade_date, lookback, workers, backend,
                "（WSL 检测到，使用多线程避免 fork 死锁）" if use_threads else "")

    task_args = [(str(p), trade_date, lookback, _USECOLS, _TRADING_COL_MAP) for p in csv_files]

    all_dfs = []
    if workers == 0:
        # 串行模式：直接逐个处理
        for args in task_args:
            result = _read_one_csv(args)
            if result is not None:
                all_dfs.append(result)
    elif use_threads:
        # WSL 环境：使用多线程，避免 fork-after-threads 死锁
        with ThreadPoolExecutor(max_workers=workers) as exe:
            try:
                for result in exe.map(_read_one_csv, task_args, chunksize=64):
                    if result is not None:
                        all_dfs.append(result)
            except BaseException:
                exe.shutdown(wait=False, cancel_futures=True)
                raise
    else:
        # 原生 Linux/macOS：使用多进程，充分利用多核 CPU
        with ProcessPoolExecutor(max_workers=workers) as exe:
            try:
                for result in exe.map(_read_one_csv, task_args, chunksize=64):
                    if result is not None:
                        all_dfs.append(result)
            except BaseException:
                exe.shutdown(wait=False, cancel_futures=True)
                raise

    if not all_dfs:
        logger.warning("[loader] stock-trading-data-pro: %s 前无数据", trade_date)
        return pd.DataFrame()

    df = pd.concat(all_dfs, ignore_index=True)
    for col in ["open", "high", "low", "close", "pre_close", "amount", "circ_mv", "total_mv"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _enrich(df: pd.DataFrame) -> pd.DataFrame:
    """在窗口 DataFrame 上计算所有衍生列。"""
    # pct_chg
    mask_pc = df["pre_close"] > 0
    df["pct_chg"] = 0.0
    df.loc[mask_pc, "pct_chg"] = (
        df.loc[mask_pc, "close"] / df.loc[mask_pc, "pre_close"] - 1
    )

    # up_limit / down_limit（CSV 无此列，需计算）
    df = _calc_zdt_price_vectorized(df)

    # is_zt / is_zb / is_dt
    df["is_zt"] = (df["close"] >= df["up_limit"]).astype(int)
    df["is_zb"] = (
        (df["high"] >= df["up_limit"]) & (df["close"] < df["up_limit"])
    ).astype(int)
    df["is_dt"] = (df["close"] <= df["down_limit"]).astype(int)

    # inst_buy / inst_sell 单位为万元（与 amount 的元不同，切勿直接与 amount 混算）
    # inst_net_pct
    inst_sum = df["inst_buy"].fillna(0) + df["inst_sell"].fillna(0)
    inst_net = df["inst_buy"].fillna(0) - df["inst_sell"].fillna(0)
    df["inst_net_pct"] = inst_net.where(inst_sum > 0, 0.0) / inst_sum.where(inst_sum > 0, 1.0)
    df["inst_net_pct"] = df["inst_net_pct"].fillna(0.0)

    # amount_mean_5 / amount_mean_20
    df = df.sort_values(["code", "trade_date"])
    grp = df.groupby("code", sort=False)["amount"]
    df["amount_mean_5"] = grp.transform(lambda s: s.rolling(5, min_periods=1).mean())
    df["amount_mean_20"] = grp.transform(lambda s: s.rolling(20, min_periods=1).mean())

    # lianzban_cnt
    df = _compute_lianzban_cnt(df)

    return df


def _calc_zdt_price_vectorized(df: pd.DataFrame) -> pd.DataFrame:
    """向量化计算涨跌停价格。规则与 market_essentials.cal_zdt_price 一致。"""
    pre = df["pre_close"].fillna(0)
    code = df["code"].fillna("").astype(str)
    name = df["name"].fillna("").astype(str)
    trade_date_s = df["trade_date"].astype(str)

    is_st = name.str.contains("ST", regex=False)
    is_kcb = code.str.contains("sh68", regex=False)
    is_cyb_new = (trade_date_s > "2020-08-23") & code.str.contains("sz3", regex=False)
    is_bj = code.str.startswith("bj")

    zt_ratio = pd.Series(1.1, index=df.index)
    dt_ratio = pd.Series(0.9, index=df.index)

    zt_ratio[is_st] = 1.05
    dt_ratio[is_st] = 0.95

    merge_rule = (is_kcb | is_cyb_new) & ~is_st
    zt_ratio[merge_rule] = 1.2
    dt_ratio[merge_rule] = 0.8

    zt_ratio[is_bj] = 1.3
    dt_ratio[is_bj] = 0.7

    zt_raw = pre * zt_ratio
    dt_raw = pre * dt_ratio

    def _round_price(val: float, bj: bool, is_dt_price: bool) -> float:
        if bj:
            rounding = _ROUND_DOWN if is_dt_price else ROUND_HALF_UP
            return float(Decimal(str(round(val, 6))).quantize(Decimal("0.01"), rounding))
        return round(val, 2)

    zt_arr = zt_raw.values
    dt_arr = dt_raw.values
    bj_arr = is_bj.values
    n = len(df)
    zt_prices = [_round_price(float(zt_arr[i]), bool(bj_arr[i]), False) for i in range(n)]
    dt_prices = [_round_price(float(dt_arr[i]), bool(bj_arr[i]), True) for i in range(n)]

    df = df.copy()
    df["up_limit"] = zt_prices
    df["down_limit"] = dt_prices
    # pre_close 无效（0 或 NaN）时，up_limit/down_limit 置 NaN，避免后续误判涨跌停
    invalid_mask = df["pre_close"].isna() | (df["pre_close"] <= 0)
    df.loc[invalid_mask, "up_limit"] = float("nan")
    df.loc[invalid_mask, "down_limit"] = float("nan")
    return df


def _compute_lianzban_cnt(df: pd.DataFrame) -> pd.DataFrame:
    """状态机推导连板次数（lianzban_cnt）。"""
    df = df.sort_values(["code", "trade_date"])

    def _cnt_per_stock(s: pd.Series) -> pd.Series:
        cnt = 0
        result = []
        for v in s:
            cnt = cnt + 1 if v == 1 else 0
            result.append(cnt)
        return pd.Series(result, index=s.index)

    df["lianzban_cnt"] = df.groupby("code", sort=False)["is_zt"].transform(_cnt_per_stock)
    df.loc[df["lianzban_cnt"] > 30, "lianzban_cnt"] = 0
    return df


# ── 对外接口 ──────────────────────────────────────────────────────────────────

def get_latest_trade_date() -> str:
    """从 stock-trading-data-pro 抽样获取最新交易日期。"""
    if not DATA_ROOT:
        return ""
    trading_dir = DATA_ROOT / "stock-trading-data-pro"
    if not trading_dir.exists():
        return ""
    try:
        sample_files = list(trading_dir.glob("sh6*.csv"))[:20]
        max_date = ""
        for fp in sample_files:
            try:
                df_tmp = pd.read_csv(fp, encoding="GBK", skiprows=1, usecols=["交易日期"])
                latest = df_tmp["交易日期"].astype(str).max()
                if latest > max_date:
                    max_date = latest
            except Exception:
                pass
        return max_date
    except Exception as e:
        logger.error("[loader] get_latest_trade_date 失败: %s", e)
        return ""


def get_trading_data(code: str) -> pd.DataFrame:
    """读取单股日K数据。code 格式: 'sh600000' 或 '600000'（自动补前缀）。"""
    if not DATA_ROOT:
        return pd.DataFrame()
    normalized = _normalize_code(code)
    csv_path = DATA_ROOT / "stock-trading-data-pro" / f"{normalized}.csv"
    if not csv_path.exists():
        logger.warning("[loader] 文件不存在: %s", csv_path)
        return pd.DataFrame()
    try:
        # 显式 with open() 避免大批量调用（>2000 只）触发 macOS 默认 ulimit 256
        with open(csv_path, "r", encoding="GBK") as f:
            df = pd.read_csv(f, skiprows=1)
        df = df.rename(columns=_TRADING_COL_MAP)
        if "trade_date" in df.columns:
            df["trade_date"] = df["trade_date"].astype(str)
        return df
    except Exception as e:
        logger.error("[loader] 读取 %s 失败: %s", csv_path, e)
        return pd.DataFrame()


def get_sector_stocks(industry: str) -> list:
    """获取申万一级行业下的所有股票代码列表（从最新截面抽样）。"""
    if not DATA_ROOT:
        return []
    trading_dir = DATA_ROOT / "stock-trading-data-pro"
    if not trading_dir.exists():
        return []
    try:
        all_dfs = []
        for fp in list(trading_dir.glob("*.csv"))[:200]:
            try:
                df_tmp = pd.read_csv(
                    fp, encoding="GBK", skiprows=1,
                    usecols=["股票代码", "交易日期", "新版申万一级行业名称"],
                    dtype={"股票代码": str},
                )
                df_tmp = df_tmp.rename(columns={"股票代码": "code", "交易日期": "trade_date", "新版申万一级行业名称": "industry_l1"})
                df_tmp["trade_date"] = df_tmp["trade_date"].astype(str)
                latest = df_tmp["trade_date"].max()
                df_tmp = df_tmp[df_tmp["trade_date"] == latest]
                all_dfs.append(df_tmp)
            except Exception:
                pass
        if not all_dfs:
            return []
        df = pd.concat(all_dfs, ignore_index=True)
        return df[df["industry_l1"] == industry]["code"].tolist()
    except Exception as e:
        logger.error("[loader] get_sector_stocks(%s) 失败: %s", industry, e)
        return []


def get_factor_slice(factor_name: str, trade_date: str) -> pd.DataFrame:
    """[已废弃] 不再依赖 factors 目录，始终返回空 DataFrame。"""
    import warnings
    warnings.warn("get_factor_slice is deprecated", DeprecationWarning, stacklevel=2)
    logger.warning("[loader] get_factor_slice 已废弃，请改用 load_daily_snapshot()")
    return pd.DataFrame()

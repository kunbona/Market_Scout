"""
XBX 数据自管 (market-radar 自己拉取 XBX, 完全写到自管目录)

策略:
- 调自管算法 (fetcher/stock_top_algorithms/) 的 load_xbx_data() 复用其复权/涨跌停/并行逻辑
- 让 load_xbx_data 默认行为写盘到 config.RAW_DATA_DIR (self_runner.install() 后
  这个常量已指向 market-radar/data/raw/)
- **完全不写** 原 stock-top-and-bottom-analysis/data/raw_data/
- Phase 2: 算法根 = fetcher/stock_top_algorithms/, 取代原 stock-top-and-bottom-analysis

注意: 必须在调用本模块之前先 import cycle_self_runner 并调 install(),
否则 config.RAW_DATA_DIR 还是 stock_top_algorithms 路径, 写盘会写到那。
self_runner 的 install() 在 market-radar server 启动时已经执行一次。
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# 触发 self_runner.install(): 改 config 路径 + reload 自管算法模块
# 这一步必须在 load_xbx_data import 之前完成
from fetcher.cycle_self_runner import install, DATA_ROOT  # noqa: F401

import pandas as pd

logger = logging.getLogger(__name__)


# XBX 本地 CSV 目录 (用户用 XBX 工具登录券商导出的数据)
DEFAULT_XBX_CSV_DIR = "/Users/kun/Desktop/AGdata/stock-trading-data-pro"

# 自管 XBX parquet 路径 (config.RAW_DATA_DIR / "xbx_stock_data.parquet")
DEFAULT_XBX_PARQUET = DATA_ROOT / "raw" / "xbx_stock_data.parquet"


def _ensure_sys_path() -> None:
    """确保自管算法根在 sys.path, 让 load_xbx_data 可 import。"""
    algo_dir = Path(__file__).resolve().parent / "stock_top_algorithms"
    if str(algo_dir) not in sys.path:
        sys.path.insert(0, str(algo_dir))


def _ensure_self_paths() -> None:
    """再次确保 config 路径已重写 (server 启动后 self_runner.install() 已做过, 这里是双保险)。"""
    install()


def fetch_xbx(
    csv_dir: str = DEFAULT_XBX_CSV_DIR,
    n_jobs: int = 8,
) -> Dict[str, Any]:
    """
    拉 XBX 数据 (从 csv_dir 读 sh/sz*.csv) → 让 load_xbx_data 默认行为写
    config.RAW_DATA_DIR / "xbx_stock_data.parquet" (即 market-radar/data/raw/ 下)

    Returns:
        {
            "success": bool,
            "data": DataFrame | None,
            "file_count": int,
            "row_count": int,
            "csv_dir": str,
            "output_parquet": str,  # 写到自管目录的 parquet 路径
            "error": str | None,
        }
    """
    if not Path(csv_dir).exists():
        return {
            "success": False,
            "data": None,
            "file_count": 0,
            "row_count": 0,
            "csv_dir": csv_dir,
            "output_parquet": str(DEFAULT_XBX_PARQUET),
            "error": f"XBX CSV 目录不存在: {csv_dir}",
        }

    _ensure_sys_path()
    _ensure_self_paths()  # 再次确认 config 已 override

    from program.api.load_xbx_data import load_xbx_data

    # save_to_file=True 让自管算法默认行为写 parquet 到 config.RAW_DATA_DIR
    # (config.RAW_DATA_DIR 此时已被 self_runner 改成自管 market-radar/data/raw/)
    result = load_xbx_data(
        stock_data_path=csv_dir,
        n_jobs=n_jobs,
        filters=["bj"],
        save_to_file=True,
    )

    df = result.get("data")
    saved_path = str(DEFAULT_XBX_PARQUET) if DEFAULT_XBX_PARQUET.exists() else None

    return {
        "success": result.get("success", False) and saved_path is not None,
        "data": df,
        "file_count": result.get("file_count", 0),
        "row_count": len(df) if df is not None else 0,
        "csv_dir": csv_dir,
        "output_parquet": saved_path,
        "error": result.get("error"),
    }


def latest_xbx_parquet() -> Optional[Path]:
    """返回自管 XBX parquet 路径, 不存在返回 None。"""
    return DEFAULT_XBX_PARQUET if DEFAULT_XBX_PARQUET.exists() else None


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--csv-dir", default=DEFAULT_XBX_CSV_DIR)
    p.add_argument("--jobs", type=int, default=8)
    args = p.parse_args()
    r = fetch_xbx(args.csv_dir, args.jobs)
    summary = {k: v for k, v in r.items() if k != "data"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))

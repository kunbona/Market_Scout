"""
akshare 数据自管 (market-radar 自己拉取 akshare, 完全写到自管目录)

策略:
- 调自管算法 (fetcher/stock_top_algorithms/) 的 fetch_all_data() 复用其 akshare 集成
- 让 fetch_all_data 默认行为写盘到 config.RAW_DATA_DIR / PROCESSED_DATA_DIR
  (self_runner.install() 后这两个常量已指向 market-radar/data/raw/ + /processed/)
- **完全不写** 原 stock-top-and-bottom-analysis/data/
- Phase 2: 算法根 = fetcher/stock_top_algorithms/, 取代原 stock-top-and-bottom-analysis
- 不再 monkey-patch, 因为 self_runner.install() 在 import 时已永久改写 config

注意: 必须在调用本模块之前先 import cycle_self_runner 并调 install(),
否则 config.RAW_DATA_DIR 还是 stock_top_algorithms 路径。self_runner 已在 cycle_xbx_data.py
顶部 import 时触发, 这里也再次 import 触发 (幂等)。
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict

# 触发 self_runner.install(): 改 config 路径 + reload 自管算法模块
# 这一步必须在 fetch_all_data import 之前完成
from fetcher.cycle_self_runner import install, DATA_ROOT  # noqa: F401

logger = logging.getLogger(__name__)


def _ensure_sys_path() -> None:
    """确保自管算法根在 sys.path, 让 fetch_all_data 可 import。"""
    algo_dir = Path(__file__).resolve().parent / "stock_top_algorithms"
    if str(algo_dir) not in sys.path:
        sys.path.insert(0, str(algo_dir))


def fetch_akshare(load_xbx_data_enabled: bool = True) -> Dict[str, Any]:
    """
    拉 6 个 akshare 源 (股权溢价 / 4 PE / 中美国债 / 4 指数日线 / 破净 / 股东增减)
    + 通过 load_xbx_data 拉 XBX, 全部写到自管目录:

    - config.RAW_DATA_DIR (= self/data/raw/) 写 xbx parquet + akshare raw csv
    - config.PROCESSED_DATA_DIR (= self/data/processed/) 写 processed csv

    Args:
        load_xbx_data_enabled: 是否在 fetch_all_data 内部再扫一遍 XBX CSV 目录。
            run_full_refresh 的 step1 已经单独跑过 fetch_xbx, 传 False 避免
            重复全量重扫 (~5500 CSV × 2 遍)。

    Returns:
        {
            "success": bool,
            "summary": {success_count, failed_count, ...},
            "written_files": [...],
            "failed_apis": [失败 API 名列表],
            "raw_dir": str,
            "processed_dir": str,
            "error": str | None,   # 部分失败时是汇总字符串, 全成功为 None
        }
    """
    _ensure_sys_path()
    install()  # 再次确认 config 已 override (幂等, 第二次是 no-op)

    # 调自管 fetch_all_data: 写盘路径由 config.RAW_DATA_DIR / PROCESSED_DATA_DIR 决定
    # 此时这两个常量已被 self_runner 改成自管目录
    from run_indicators import fetch_all_data

    result = fetch_all_data(
        save_raw=True,
        save_processed=True,
        equity_bond_spread_code="000300.SH",
        stock_index_pe_symbols=["上证50", "沪深300", "中证500", "中证1000"],
        bond_zh_us_rate_start_date="20050101",
        stock_zh_index_daily_symbols=["sh000001", "sz399006", "sh000300", "sz399303", "sh000852", "sh000688"],
        load_xbx_data_enabled=load_xbx_data_enabled,
    )

    summary = result.get("summary", {})
    written = []
    failed_apis = []
    for name, r in (result.get("results") or {}).items():
        if not r.get("success"):
            failed_apis.append(name)
        for key in ("raw_file_path", "processed_file_path"):
            p = r.get(key)
            if p:
                written.append(p)

    error = f"部分 akshare 源失败: {', '.join(failed_apis)}" if failed_apis else None

    return {
        "success": bool(result.get("success")),
        "summary": summary,
        "written_files": written,
        "failed_apis": failed_apis,
        "raw_dir": str(DATA_ROOT / "raw"),
        "processed_dir": str(DATA_ROOT / "processed"),
        "error": error,
    }


if __name__ == "__main__":
    r = fetch_akshare()
    print(json.dumps({k: v for k, v in r.items()}, ensure_ascii=False, indent=2)[:2000])

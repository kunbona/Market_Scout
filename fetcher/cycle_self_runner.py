"""
市场周期自管 runner — Phase 2: 0 依赖原 stock-top-and-bottom-analysis 文件夹。

历史: Phase 1 永久改写对方 config 内存状态 + reload 对方模块, 实现 0 写对方 disk。
      但仍 import 对方 30+ 个 Python 模块 (复用算法), stock-top-and-bottom-analysis
      文件夹不能删。
Phase 2: 把对方核心算法 (config.py + program/ + run_indicators.py) 完整复制到
      `market-radar/fetcher/stock_top_algorithms/`, 改 self_runner 等改 import 路径
      走自己维护的代码, 0 import 对方, 0 读对方 disk, 0 写对方 disk。
      原 stock-top-and-bottom-analysis 文件夹可安全移除。

数据流: install() 时永久改写自己 config module 的路径常量指自管目录,
      reload 自己所有模块让 `from config import RAW_DATA_DIR` 重新求值,
      后续所有指标计算 + 周期信号代码自然写盘到 market-radar/data/。
"""
from __future__ import annotations

import importlib
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# market-radar 自管数据根 (绝对路径, 跨 cwd 也能找到)
DATA_ROOT = Path("/Users/kun/Documents/market-radar/data")
RAW_DIR = DATA_ROOT / "raw"
PROCESSED_DIR = DATA_ROOT / "processed"
RESULTS_DIR = DATA_ROOT / "results"
PIC_DIR = DATA_ROOT / "pic"
COMPLETION_FLAGS_DIR = DATA_ROOT / "completion_flags"
AUTO_RUNNER_LOG_DIR = DATA_ROOT / "auto_runner_logs"

# 自管算法根 (market-radar/fetcher/stock_top_algorithms/)
# Phase 2: 取代原 stock-top-and-bottom-analysis
ALGO_DIR = Path(__file__).resolve().parent / "stock_top_algorithms"


# 创建自管目录
for _d in (RAW_DIR, PROCESSED_DIR, RESULTS_DIR, PIC_DIR,
           COMPLETION_FLAGS_DIR, AUTO_RUNNER_LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)


# 已经 install 过就不重复 reload
_INSTALLED = False


def _ensure_sys_path() -> None:
    """确保 ALGO_DIR 在 sys.path, 这样 `import config` / `import program` 能找到。"""
    if str(ALGO_DIR) not in sys.path:
        sys.path.insert(0, str(ALGO_DIR))


def _override_config_paths() -> Dict[str, str]:
    """永久改写自管 config module 的 6 个路径常量指自管目录。

    config.py 磁盘文件**不动** (我们 copy 的版本, 路径常量是 Path(__file__).parent
    算出来的, 默认也是 ALGO_DIR / data, 跟我们的自管目录不一致, 所以要 override)。

    Returns:
        {原值 → 新值} 映射, 给日志看
    """
    _ensure_sys_path()
    import config

    changes: Dict[str, str] = {}

    pairs = [
        ("RAW_DATA_DIR", RAW_DIR),
        ("PROCESSED_DATA_DIR", PROCESSED_DIR),
        ("RESULTS_DIR", RESULTS_DIR),
        ("PIC_DIR", PIC_DIR),
        ("COMPLETION_FLAGS_DIR", COMPLETION_FLAGS_DIR),
        ("AUTO_RUNNER_LOG_DIR", AUTO_RUNNER_LOG_DIR),
    ]
    for attr, new_val in pairs:
        old = getattr(config, attr)
        if old != new_val:
            changes[attr] = f"{old} → {new_val}"
        setattr(config, attr, new_val)

    # DATA_DIR 也覆盖, 避免漏网
    if config.DATA_DIR != DATA_ROOT:
        changes["DATA_DIR"] = f"{config.DATA_DIR} → {DATA_ROOT}"
    config.DATA_DIR = DATA_ROOT

    return changes


# 列出所有"用了 from config import" 的自管模块 — reload 时让它们的局部变量重新求值
_RELOAD_TARGETS = [
    # 基础
    "config",
    # 顶层入口
    "run_indicators",
    "auto_runner",
    # 周期信号
    "program.cycle_signal.runner",
    # 指标
    "program.indicators",
    "program.indicators.indicator_manager",
    "program.indicators.equity_premium_indicator",
    "program.indicators.pe_valuation_indicator",
    "program.indicators.price_percentile_indicator",
    "program.indicators.market_turnover_percentile_indicator",
    "program.indicators.ma250_bias_indicator",
    "program.indicators.market_crowdedness_indicator",
    "program.indicators.below_net_asset_indicator",
    "program.indicators.herding_rate_indicator",
    "program.indicators.herding_rate_source",
    "program.indicators.breadth_above_ma_indicator",
    "program.indicators.limit_up_ratio_indicator",
    "program.indicators.market_breadth_indicator",
    "program.indicators.macd_divergence_indicator",
    # 周期信号 / 回测
    "program.weight_optimizer.indicator_preprocessor",
    "program.weight_optimizer.label_generator",
    "program.weight_optimizer.visualizer",
    "program.weight_optimizer.cycle_position",
    "program.weight_optimizer.backtester",
    # API
    "program.api",
    "program.api.load_xbx_data",
    "program.api.stock_index_pe",
    "program.api.bond_zh_us_rate",
    "program.api.stock_zh_index_daily",
    "program.api.stock_a_below_net_asset_statistics",
    "program.api.stock_margin_account",
    "program.api.stock_ggcg_em",
    # utils
    "program.utils.http_client",
    "program.utils.notifier",
    # data checker
    "program.data_checker",
]


def _reload_all_targets() -> List[str]:
    """reload 所有已加载的对方模块, 让 `from config import X` 重新求值。

    关键: 对方模块顶部 `from config import RAW_DATA_DIR` 在 import 时**冻结**局部变量,
    reload 会重新执行模块顶部代码, 包括重新 import config, 拿到我们 override 后的新值。

    注意: **不 reload config 本身** — reload config 会重新执行 config.py 顶部代码,
    把 RAW_DATA_DIR 重置回对方 disk 路径! 必须保持 config module 内存状态为自管路径。
    """
    _ensure_sys_path()

    # 先确保 config module 已 import 且被 override
    _override_config_paths()

    reloaded: List[str] = []
    for name in _RELOAD_TARGETS:
        if name == "config":
            # 跳过 config: reload config 会重置路径常量, 破坏 override
            continue
        mod = sys.modules.get(name)
        if mod is None:
            # 还没 import 过, 跳过 (下次 import 时会自然读到新 config)
            continue
        try:
            importlib.reload(mod)
            reloaded.append(name)
        except Exception as e:
            logger.warning("reload %s 失败: %s", name, e)

    return reloaded


def install() -> Dict[str, Any]:
    """公共入口: 安装自管路径。

    - 永久改写对方 config module 的 6 个路径常量指自管
    - reload 所有已加载的对方模块, 让 `from config import` 重新求值

    只在第一次调用时执行实际工作, 后续调用直接返回 (避免重复 reload 浪费)。

    Returns:
        {"changes": {attr: "old → new"}, "reloaded": [mod_name, ...]}
    """
    global _INSTALLED
    if _INSTALLED:
        return {"changes": {}, "reloaded": [], "already_installed": True}

    changes = _override_config_paths()
    reloaded = _reload_all_targets()
    _INSTALLED = True

    logger.info(
        "[cycle_self_runner] installed: %d path overrides, %d modules reloaded",
        len(changes), len(reloaded),
    )
    for attr, change in changes.items():
        logger.info("  config.%s: %s", attr, change)
    for mod in reloaded[:5]:
        logger.info("  reloaded: %s", mod)
    if len(reloaded) > 5:
        logger.info("  ... and %d more", len(reloaded) - 5)

    return {"changes": changes, "reloaded": reloaded}


def get_config_paths() -> Dict[str, str]:
    """返回当前 config 4 个路径常量的当前值 (用于诊断/暴露给前端)。"""
    _ensure_sys_path()
    import config
    return {
        "RAW_DATA_DIR": str(config.RAW_DATA_DIR),
        "PROCESSED_DATA_DIR": str(config.PROCESSED_DATA_DIR),
        "RESULTS_DIR": str(config.RESULTS_DIR),
        "PIC_DIR": str(config.PIC_DIR),
    }


def run_full_refresh() -> Dict[str, Any]:
    """完整 in-process 自管刷新 (XBX + akshare + 指标 + 周期信号)。

    流程:
    1. install() — 改 config 路径 + reload 对方模块
    2. fetch_xbx (调 load_xbx_data → 写到 self/raw/xbx_stock_data.parquet)
    3. fetch_akshare (调 fetch_all_data → 写到 self/raw/akshare/*)
    4. build_and_calculate (调对方指标函数 → 写到 self/processed/*.csv)
    5. run_cycle_signal_step (调对方周期信号函数 → 写到 self/results/ + self/pic/)

    整个流程 0 写对方 disk, 全部写到 market-radar/data/。
    """
    _ensure_sys_path()
    install_info = install()

    log_lines: List[str] = []
    def log(msg: str) -> None:
        print(msg, flush=True)
        log_lines.append(msg)

    log(f"[self_runner] data_root = {DATA_ROOT}")
    log(f"[self_runner] config paths 已指向自管目录")

    # step 1: XBX
    log("=" * 70)
    log("步骤 1/4: 拉取 XBX 数据 (本地 CSV → self/raw/xbx_stock_data.parquet)")
    log("=" * 70)
    from fetcher.cycle_xbx_data import fetch_xbx
    _qdr = os.environ.get("QUANT_DATA_ROOT", "").strip()
    if not _qdr or not Path(_qdr).exists():
        raise FileNotFoundError(f"QUANT_DATA_ROOT 未配置或路径不存在: {_qdr!r}")
    xbx_r = fetch_xbx(
        csv_dir=f"{_qdr}/stock-trading-data-pro",
    )
    log(f"  XBX success={xbx_r.get('success')}")
    log(f"  XBX saved_file_path={xbx_r.get('saved_file_path')}")
    log(f"  XBX file_count={xbx_r.get('file_count')}, row_count={xbx_r.get('row_count')}")
    if not xbx_r.get("success"):
        return {
            "success": False,
            "step": "fetch_xbx",
            "error": xbx_r.get("error"),
            "log": log_lines,
        }

    # step 2: akshare
    log("=" * 70)
    log("步骤 2/4: 拉取 akshare 6 源 (→ self/raw/akshare/)")
    log("=" * 70)
    from fetcher.cycle_akshare_data import fetch_akshare
    ak_r = fetch_akshare()
    log(f"  akshare success={ak_r.get('success')}")
    log(f"  akshare summary={ak_r.get('summary')}")
    log(f"  akshare written_files={len(ak_r.get('written_files', []))}")
    if not ak_r.get("success"):
        return {
            "success": False,
            "step": "fetch_akshare",
            "error": ak_r.get("error"),
            "log": log_lines,
        }

    # step 3: 指标
    log("=" * 70)
    log("步骤 3/4: 计算 11 个指标 (→ self/processed/*.csv)")
    log("=" * 70)
    from run_indicators import build_and_calculate
    manager, results = build_and_calculate()
    log(f"  计算了 {len(results)} 个指标")
    for ind_name, ind_result in (results or {}).items():
        if hasattr(ind_result, "risk_percentage"):
            log(f"  - {ind_name}: risk_pct={ind_result.risk_percentage:.2f}%")

    if not results:
        return {
            "success": False,
            "step": "build_and_calculate",
            "error": "没有计算出任何指标",
            "log": log_lines,
        }

    # step 4: 周期信号
    log("=" * 70)
    log("步骤 4/4: 周期信号 (→ self/results/cycle_signal_latest.json + self/pic/cycle_position.png)")
    log("=" * 70)
    from program.cycle_signal.runner import run_cycle_signal_step
    report = run_cycle_signal_step()
    log(f"  cycle_score={report.get('cycle_score')}, phase={report.get('phase')}")

    return {
        "success": True,
        "install_info": install_info,
        "report": {
            "date": report.get("date"),
            "cycle_score": report.get("cycle_score"),
            "phase": report.get("phase"),
            "signal_state": report.get("signal_state"),
            "current_event": report.get("current_event"),
            "freshness": report.get("freshness"),
            "consistency": report.get("consistency"),
        },
        "config_paths": get_config_paths(),
        "data_root": str(DATA_ROOT),
        "log": log_lines,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    result = run_full_refresh()
    # 打印除 log/report 外的字段
    summary = {k: v for k, v in result.items() if k not in ("log", "report")}
    print("\n" + json.dumps(summary, ensure_ascii=False, indent=2, default=str))

"""
周期信号（顶底量化）数据 fetcher

读取 market-radar 自管数据目录 (默认 data/, 通过 CYCLE_SIGNAL_DATA_PATH 可覆盖)：
- data/raw/xbx_stock_data.parquet  XBX 个股日线 (2.2GB)
- data/raw/  akshare 6 源 CSV (PE / 国债利差 / 指数日线 / 破净)
- data/processed/  11 指标 processed CSV (8 周期 + 4 PE 衍生)
- data/results/cycle_signal_latest.json + data/pic/cycle_position.png

**自管模式 (Phase 2)**:
- 完全自管: 算法代码已 copy 到 fetcher/stock_top_algorithms/, 不再 import 原 stock-top-and-bottom-analysis
- 0 写对方 disk, 0 依赖对方进程, 原 stock-top-and-bottom-analysis 文件夹可安全移除
- 数据自拉 (fetcher/cycle_xbx_data.py + fetcher/cycle_akshare_data.py)
- 计算产物 (11 指标 + 周期信号) 走自管算法, 写盘路径全在 market-radar/data/
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

# ---------------------------------------------------------------------------
# 自管路径: import 即触发 install() — 永久改写对方 config 路径指自管,
#           reload 所有用 `from config import` 的对方模块
# 必须在任何对方模块 (run_indicators / cycle_signal.runner) import 之前
# ---------------------------------------------------------------------------
from fetcher.cycle_self_runner import (  # noqa: E402
    install as _install_self_runner,
    get_config_paths as _get_self_config_paths,
    DATA_ROOT as _SELF_DATA_ROOT,
)

# ---------------------------------------------------------------------------
# 路径解析
# ---------------------------------------------------------------------------

# market-radar 自管数据根目录（指向 market-radar/，所有"data/xxx"路径都解析到 market-radar/data/xxx）
# 设 CYCLE_SIGNAL_DATA_PATH 可覆盖
_DEFAULT_DATA_PATH = Path(__file__).resolve().parent.parent  # 即 market-radar/


def _get_data_root() -> Path:
    """解析 CYCLE_SIGNAL_DATA_PATH；未配置则用兄弟目录默认路径。"""
    env = os.environ.get("CYCLE_SIGNAL_DATA_PATH")
    if env:
        return Path(env).expanduser().resolve()
    return _DEFAULT_DATA_PATH


def _safe_resolve(sub: str) -> Path:
    """防止路径穿越：sub 必须在 data_root 之下。"""
    root = _get_data_root()
    p = (root / sub).resolve()
    if not str(p).startswith(str(root.resolve())):
        raise ValueError(f"path escape: {sub}")
    return p


# ---------------------------------------------------------------------------
# 指标元信息
# ---------------------------------------------------------------------------

# 8 个核心指标 + 元数据
# (key, display_name, category, file, value_col, percentile_col, risk_col, status_col)
# value_col / risk_col / status_col 可为 None（部分指标没有 risk_percentage）
INDICATOR_META: List[Dict[str, Any]] = [
    {
        "key": "pe_composite",
        "name": "PE 估值（综合）",
        "category": "估值",
        "file": None,  # composite_pe 是从 4 个 PE 指标聚合而来，下面单独处理
        "value_col": "composite_pe",
        "percentile_col": None,
        "risk_col": "risk_percentage",
        "status_col": "valuation_status",
        "group": "pe_valuation",
        "use_composite": True,
    },
    {
        "key": "hs300_equity_premium",
        "name": "股权风险溢价（沪深300）",
        "category": "宏观",
        "file": "equity_premium_沪深300.csv",
        "value_col": "equity_premium",
        "percentile_col": None,
        "risk_col": "risk_percentage",
        "status_col": "market_status",
    },
    {
        "key": "ma250_bias",
        "name": "年线偏离率（MA250）",
        "category": "趋势",
        "file": "ma250_bias.csv",
        "value_col": "avg_bias",
        "percentile_col": "percentile",
        "risk_col": None,
        "status_col": None,
    },
    {
        "key": "below_net_asset",
        "name": "破净率（全部A股）",
        "category": "估值",
        "file": "below_net_asset_全部A股.csv",
        "value_col": "below_net_asset_ratio",
        "percentile_col": "percentile",
        "risk_col": "risk_percentage",
        "status_col": None,
    },
    {
        "key": "market_crowdedness",
        "name": "市场拥挤度",
        "category": "微观结构",
        "file": "market_crowdedness.csv",
        "value_col": "crowdedness_ratio",
        "percentile_col": "percentile",
        "risk_col": None,
        "status_col": None,
    },
    {
        "key": "herding_rate",
        "name": "抱团率",
        "category": "微观结构",
        "file": "herding_rate.csv",
        "value_col": "raw_value",
        "percentile_col": "percentile",
        "risk_col": None,
        "status_col": None,
    },
    {
        "key": "market_turnover_percentile",
        "name": "换手率分位",
        "category": "情绪",
        "file": "market_turnover_percentile.csv",
        "value_col": "turnover_rate",
        "percentile_col": "percentile",
        "risk_col": None,
        "status_col": None,
    },
    {
        "key": "price_percentile",
        "name": "价格分位（市场广度）",
        "category": "市场广度",
        "file": "price_percentile.csv",
        "value_col": "avg_percentile",
        "percentile_col": None,
        "risk_col": None,
        "status_col": None,
    },
]


# ---------------------------------------------------------------------------
# 缓存（按文件 mtime）— 统一走 core/cache.py 的 MtimeCache
# ---------------------------------------------------------------------------

from core.cache import MtimeCache

_csv_cache = MtimeCache()
_json_cache = MtimeCache()


def _load_csv_with_fallback(path: Path) -> Optional[pd.DataFrame]:
    """utf-8-sig 优先, 失败回落默认编码 (与原实现一致)。"""
    try:
        return pd.read_csv(path, encoding="utf-8-sig")
    except Exception:
        try:
            return pd.read_csv(path)
        except Exception:
            return None


def _read_csv_cached(rel_path: str) -> Optional[pd.DataFrame]:
    """读 CSV，按 mtime 缓存。文件不存在返回 None。"""
    try:
        path = _safe_resolve(f"data/processed/{rel_path}")
    except ValueError:
        return None
    return _csv_cache.get_or_load(path, _load_csv_with_fallback, clone=lambda d: d.copy())


def _read_index_csv() -> Optional[pd.DataFrame]:
    """上证指数日线。"""
    # Phase 2: 只走自管路径
    path = None
    for rel in (
        "data/raw/stock_zh_index_daily_sh000001.csv",
        "data/raw/akshare/stock_zh_index_daily_sh000001.csv",  # 兼容旧自管子目录
    ):
        try:
            p = _safe_resolve(rel)
        except ValueError:
            continue
        if p.exists():
            path = p
            break
    if path is None:
        return None

    def _load_index_csv(p: Path) -> Optional[pd.DataFrame]:
        df = _load_csv_with_fallback(p)
        if df is None:
            return None
        if "date" not in df.columns and "日期" in df.columns:
            df = df.rename(columns={"日期": "date"})
        return df

    return _csv_cache.get_or_load(
        path, _load_index_csv, clone=lambda d: d.copy(), key="_index_sh000001"
    )


def _load_json_file(path: Path) -> Optional[Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _read_json(rel_path: str) -> Optional[Any]:
    try:
        path = _safe_resolve(rel_path)
    except ValueError:
        return None
    return _json_cache.get_or_load(path, _load_json_file)


# ---------------------------------------------------------------------------
# Schema 规范化
# ---------------------------------------------------------------------------

def _df_to_records(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """DataFrame → JSON-friendly list of dicts；NaN → None。"""
    if df is None or df.empty:
        return []
    out = df.copy()
    out = out.where(pd.notnull(out), None)
    return out.to_dict(orient="records")


def _normalize_indicator_df(
    df: pd.DataFrame, meta: Dict[str, Any]
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """把单个指标 CSV 规整为 {series, latest} 形态。

    返回：
      series: [{"date": "2026-08-01", "value": 0.5, "percentile": 51.2, "risk_percentage": 67.0, "status": "正常"}, ...]
      latest: 末行（同 schema 单条）
    """
    if df is None or df.empty:
        return [], {}
    df = df.copy()
    # 找日期列
    date_col = None
    for c in ["交易日期", "日期", "目标日期", "date"]:
        if c in df.columns:
            date_col = c
            break
    if not date_col:
        return [], {}
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df = df.dropna(subset=[date_col]).sort_values(date_col)

    def _coerce_col(c: Optional[str]) -> Optional[str]:
        return c if c and c in df.columns else None

    value_col = _coerce_col(meta.get("value_col"))
    pct_col = _coerce_col(meta.get("percentile_col"))
    risk_col = _coerce_col(meta.get("risk_col"))
    status_col = _coerce_col(meta.get("status_col"))

    out_rows: List[Dict[str, Any]] = []
    # 向量化构造 (原 iterrows 每行构造 Series, 3000 行级别时慢一个数量级)
    n = len(df)
    dates = df[date_col].dt.strftime("%Y-%m-%d").tolist()
    values = df[value_col].tolist() if value_col else [None] * n
    pcts = df[pct_col].tolist() if pct_col else [None] * n
    risks = df[risk_col].tolist() if risk_col else [None] * n
    statuses = df[status_col].tolist() if status_col else [None] * n
    for i in range(n):
        v, p, r = values[i], pcts[i], risks[i]
        out_rows.append(
            {
                "date": dates[i],
                "value": float(v) if v is not None and pd.notnull(v) else None,
                "percentile": float(p) if p is not None and pd.notnull(p) else None,
                "risk_percentage": float(r) if r is not None and pd.notnull(r) else None,
                "status": str(statuses[i]) if statuses[i] is not None and pd.notnull(statuses[i]) else None,
            }
        )

    latest = out_rows[-1] if out_rows else {}
    return out_rows, latest


# composite PE 结果缓存: 按 4 个 PE CSV 的 mtime 组合键失效 (join+ffill 全史计算 ~秒级)
_composite_pe_cache: Dict[str, Any] = {"key": None, "series": None, "latest": None}

# unified score 结果缓存: 按 7 指标 CSV mtime 组合键失效
_unified_score_cache: Dict[str, Any] = {"key": None, "rows": None}


def _composite_pe_series() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Composite PE = 4 个 PE 指数的 mean(滚动市盈率)。"""
    pe_files = [
        "pe_valuation_沪深300_滚动市盈率.csv",
        "pe_valuation_上证50_滚动市盈率.csv",
        "pe_valuation_中证500_滚动市盈率.csv",
        "pe_valuation_中证1000_滚动市盈率.csv",
    ]

    # mtime 组合键: 任一文件更新则重算
    try:
        cache_key = ",".join(
            str((_safe_resolve(f"data/processed/{f}")).stat().st_mtime)
            for f in pe_files
            if (_safe_resolve(f"data/processed/{f}")).exists()
        )
    except ValueError:
        cache_key = None
    if cache_key and _composite_pe_cache["key"] == cache_key and _composite_pe_cache["series"] is not None:
        return _composite_pe_cache["series"], _composite_pe_cache["latest"]

    dfs = []
    for f in pe_files:
        d = _read_csv_cached(f)
        if d is not None and not d.empty:
            dfs.append(d)
    if not dfs:
        return [], {}

    # 找日期列与 PE 列
    merged = None
    for f, d in zip(pe_files, dfs):
        d = d.copy()
        date_col = next((c for c in ["日期", "交易日期", "date"] if c in d.columns), None)
        pe_col = "滚动市盈率" if "滚动市盈率" in d.columns else None
        if not date_col or not pe_col:
            continue
        d[date_col] = pd.to_datetime(d[date_col], errors="coerce")
        d = d.dropna(subset=[date_col, pe_col])[[date_col, pe_col]].rename(
            columns={pe_col: f}
        ).set_index(date_col)
        merged = d if merged is None else merged.join(d, how="outer")

    if merged is None or merged.empty:
        return [], {}

    merged = merged.sort_index().ffill().dropna(how="all")
    merged["composite_pe"] = merged[pe_files].mean(axis=1)

    # risk_percentage: 用沪深300 的 risk_percentage 作为代表（如果存在）
    hs300 = _read_csv_cached("pe_valuation_沪深300_滚动市盈率.csv")
    risk_map: Dict[str, float] = {}
    status_map: Dict[str, str] = {}
    if hs300 is not None and not hs300.empty and "日期" in hs300.columns:
        hs300 = hs300.copy()
        hs300["日期"] = pd.to_datetime(hs300["日期"], errors="coerce")
        hs300 = hs300.dropna(subset=["日期"]).set_index("日期").sort_index()
        if "risk_percentage" in hs300.columns:
            risk_map = hs300["risk_percentage"].to_dict()
        if "valuation_status" in hs300.columns:
            status_map = hs300["valuation_status"].to_dict()

    out_rows: List[Dict[str, Any]] = []
    # 向量化构造 (原 iterrows 慢)
    ts_list = [pd.Timestamp(i) for i in merged.index]
    d_strs = [ts.strftime("%Y-%m-%d") for ts in ts_list]
    cp_list = merged["composite_pe"].tolist()
    risk_keys = set(risk_map.keys())
    status_keys = set(status_map.keys())
    for i, ts in enumerate(ts_list):
        cp = cp_list[i]
        out_rows.append(
            {
                "date": d_strs[i],
                "value": float(cp) if cp is not None and pd.notnull(cp) else None,
                "percentile": None,
                "risk_percentage": float(risk_map[ts]) if ts in risk_keys else None,
                "status": str(status_map[ts]) if ts in status_keys else None,
            }
        )
    latest = out_rows[-1] if out_rows else {}
    if cache_key:
        _composite_pe_cache.update(key=cache_key, series=out_rows, latest=latest)
    return out_rows, latest


# ---------------------------------------------------------------------------
# 公开 payload 组装
# ---------------------------------------------------------------------------

# XBX 数据新鲜度阈值（分钟）：< 30 算 fresh，< 180 算 stale，否则算 old
XBX_FRESH_MINUTES = 30
XBX_STALE_MINUTES = 180

# XBX parquet mtime 缓存：避免每次都读 parquet 取 max date (core/cache MtimeCache)
_xbx_freshness_cache = MtimeCache()


def _xbx_data_freshness() -> Dict[str, Any]:
    """检查 XBX parquet 数据的"新鲜度"（mtime 推断，不读 parquet 内容）。

    返回:
        {
          "available": bool,                  # parquet 存在
          "path": str,                        # parquet 路径
          "mtime": str (ISO),                 # 文件最后修改时间
          "mtime_ago_minutes": int,           # 距今多少分钟
          "freshness": "fresh" | "stale" | "old" | "missing",
        }

    设计:
    - 用 parquet mtime 推断 XBX 拉数据时间（XBX 工具拉完会写这个文件）
    - 不读 parquet 内容（2GB 太大，且 venv 没装 pyarrow）
    - freshness 阈值: <30min fresh, <3h stale, ≥3h old
    - mtime 没变就返回缓存（避免 stat 重复 IO）
    """
    root = _get_data_root()
    # Phase 2: 只走自管路径, 不再兼容老 stock-top-and-bottom-analysis/data/raw_data/
    parquet = root / "data" / "raw" / "xbx_stock_data.parquet"
    if not parquet.exists():
        return {
            "available": False,
            "path": str(parquet),
            "mtime": "",
            "mtime_ago_minutes": -1,
            "freshness": "missing",
        }

    def _compute_freshness(p: Path) -> Optional[Dict[str, Any]]:
        try:
            mtime = p.stat().st_mtime
        except Exception:
            return None
        from datetime import datetime as _dt
        now = _dt.now().timestamp()
        minutes = int((now - mtime) / 60)
        if minutes < XBX_FRESH_MINUTES:
            freshness = "fresh"
        elif minutes < XBX_STALE_MINUTES:
            freshness = "stale"
        else:
            freshness = "old"
        return {
            "available": True,
            "path": str(p),
            "mtime": _dt.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "mtime_ago_minutes": minutes,
            "freshness": freshness,
        }

    data = _xbx_freshness_cache.get_or_load(parquet, _compute_freshness)
    if data is None:
        return {
            "available": False,
            "path": str(parquet),
            "mtime": "",
            "mtime_ago_minutes": -1,
            "freshness": "missing",
        }
    return data


def build_cycle_summary() -> Dict[str, Any]:
    """周期信号摘要（顶层卡片 + 当前定位）。"""
    raw = _read_json("data/results/cycle_signal_latest.json")
    if not raw:
        return {
            "available": False,
            "reason": "cycle_signal_latest.json 不存在（auto_runner 还没跑过）",
            "data_root": str(_get_data_root()),
        }
    # 加个 available 标记，前端好做空态
    out = dict(raw)
    out["available"] = True
    out["data_root"] = str(_get_data_root())
    out["xbx_freshness"] = _xbx_data_freshness()
    return out


def build_indicator_meta() -> Dict[str, Any]:
    """8 指标的最新值（轻量，用于卡片）。

    性能: 卡片只需要 latest 一行, 普通指标只 normalize 末尾几行
    (normalize 内部会按日期排序, tail 后最后一行即最新), 不做全史转换。
    """
    items: List[Dict[str, Any]] = []
    for meta in INDICATOR_META:
        if meta.get("use_composite"):
            series, latest = _composite_pe_series()
            series_len = len(series)
        else:
            df = _read_csv_cached(meta["file"])
            if df is None or df.empty:
                series, latest, series_len = [], {}, 0
            else:
                series, latest = _normalize_indicator_df(df.tail(3), meta)
                series_len = len(df)
        # 截断 series 不返回，前端用单独 endpoint 拉
        items.append(
            {
                "key": meta["key"],
                "name": meta["name"],
                "category": meta["category"],
                "latest": latest,
                "has_series": bool(series),
                "series_length": series_len,
            }
        )
    return {
        "items": items,
        "data_root": str(_get_data_root()),
    }


def build_indicator_series(key: str, max_points: int = 3000) -> Dict[str, Any]:
    """单个指标的时间序列（用于画 Plotly 图）。数据量大时降采样到 max_points。"""
    meta = next((m for m in INDICATOR_META if m["key"] == key), None)
    if not meta:
        return {"available": False, "reason": f"未知指标 key: {key}"}
    if meta.get("use_composite"):
        series, latest = _composite_pe_series()
    else:
        df = _read_csv_cached(meta["file"])
        series, latest = _normalize_indicator_df(df, meta)

    if not series:
        return {
            "available": False,
            "reason": f"{meta['name']} 数据文件不存在或为空（{meta.get('file')}）",
            "meta": {
                "key": meta["key"],
                "name": meta["name"],
                "category": meta["category"],
            },
        }

    # 降采样：保留首尾
    if len(series) > max_points:
        step = max(1, len(series) // max_points)
        sampled = series[::step]
        if sampled[-1] != series[-1]:
            sampled.append(series[-1])
        series = sampled

    return {
        "available": True,
        "meta": {
            "key": meta["key"],
            "name": meta["name"],
            "category": meta["category"],
            "value_label": _value_label_for(meta),
        },
        "latest": latest,
        "series": series,
    }


def _value_label_for(meta: Dict[str, Any]) -> str:
    """人类可读的单位/标签。"""
    v = meta.get("value_col")
    mapping = {
        "composite_pe": "PE",
        "equity_premium": "风险溢价(%)",
        "avg_bias": "偏离率(%)",
        "below_net_asset_ratio": "破净率(%)",
        "crowdedness_ratio": "拥挤度",
        "raw_value": "抱团率(%)",
        "turnover_rate": "换手率(%)",
        "avg_percentile": "分位",
    }
    return mapping.get(v, v or "value")


def build_equity_curve(max_points: int = 3000) -> Dict[str, Any]:
    """上证指数时间序列。"""
    df = _read_index_csv()
    if df is None or df.empty:
        return {
            "available": False,
            "reason": "上证指数日线 CSV 不存在 (data/raw/stock_zh_index_daily_sh000001.csv)",
        }
    if "date" not in df.columns and "日期" in df.columns:
        df = df.rename(columns={"日期": "date"})
    if "date" not in df.columns or "close" not in df.columns:
        return {"available": False, "reason": "上证指数 CSV 缺少 date/close 列"}
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "close"]).sort_values("date")

    rows = [
        {"date": d.strftime("%Y-%m-%d"), "close": float(c)}
        for d, c in zip(df["date"], df["close"])
    ]
    if len(rows) > max_points:
        step = max(1, len(rows) // max_points)
        sampled = rows[::step]
        if sampled[-1] != rows[-1]:
            sampled.append(rows[-1])
        rows = sampled
    return {
        "available": True,
        "series": rows,
        "first_date": rows[0]["date"] if rows else None,
        "last_date": rows[-1]["date"] if rows else None,
        "last_close": rows[-1]["close"] if rows else None,
    }


def build_unified_score_series(max_points: int = 3000) -> Dict[str, Any]:
    """从 processed CSV 聚合：unified_score = 7 指标 risk_percentage 等权 mean（与 runner 等权打分一致）。

    指标列表与 fetcher/stock_top_algorithms/program/cycle_signal/runner.py 里的
    CYCLE_INDICATOR_KEYS 保持一致 (Phase 2: 算法已自管)。
    """
    keys = [
        "hs300_equity_premium",
        "ma250_bias",
        "below_net_asset",
        "market_crowdedness",
        "herding_rate",
        "market_turnover_percentile",
        "price_percentile",
    ]
    meta_by_key = {m["key"]: m for m in INDICATOR_META}

    # mtime 组合键结果缓存 (7 CSV 全量聚合 ~1.4s, 刷新不频繁)
    try:
        ukey = ",".join(
            str((_safe_resolve(f"data/processed/{meta_by_key[k]['file']}")).stat().st_mtime)
            for k in keys
            if (_safe_resolve(f"data/processed/{meta_by_key[k]['file']}")).exists()
        )
    except (ValueError, KeyError):
        ukey = None
    if ukey and _unified_score_cache.get("key") == ukey and _unified_score_cache.get("rows"):
        rows = _unified_score_cache["rows"]
        if len(rows) > max_points:
            step = max(1, len(rows) // max_points)
            sampled = rows[::step]
            if sampled[-1] != rows[-1]:
                sampled.append(rows[-1])
            rows = sampled
        return {
            "available": True,
            "series": rows,
            "last_date": rows[-1]["date"] if rows else None,
            "last_score": rows[-1]["score"] if rows else None,
        }

    series_by_date: Dict[str, List[float]] = {}

    for k in keys:
        meta = meta_by_key[k]
        df = _read_csv_cached(meta["file"])
        if df is None or df.empty:
            continue
        df = df.copy()
        date_col = next((c for c in ["交易日期", "日期", "date"] if c in df.columns), None)
        if not date_col:
            continue
        risk_col = meta.get("risk_col") or ("percentile" if k != "price_percentile" else None)
        if k == "ma250_bias":
            risk_col = "percentile"
        elif k == "market_crowdedness":
            risk_col = "percentile"
        elif k == "herding_rate":
            risk_col = "percentile"
        elif k == "market_turnover_percentile":
            risk_col = "percentile"
        if not risk_col or risk_col not in df.columns:
            continue
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        df = df.dropna(subset=[date_col, risk_col])
        for d, v in zip(df[date_col], df[risk_col]):
            d_str = d.strftime("%Y-%m-%d")
            series_by_date.setdefault(d_str, []).append(float(v))

    if not series_by_date:
        return {"available": False, "reason": "unified score 聚合数据为空"}

    # 加 composite PE（4 指数 PE 风险分均值；用 hs300_equity_premium 风险分作为代表）
    pe_series, _ = _composite_pe_series()
    for rec in pe_series:
        d_str = rec.get("date")
        r = rec.get("risk_percentage")
        if d_str and r is not None:
            series_by_date.setdefault(d_str, []).append(float(r))

    rows = []
    for d_str in sorted(series_by_date.keys()):
        vals = series_by_date[d_str]
        if vals:
            rows.append({"date": d_str, "score": sum(vals) / len(vals)})

    if not rows:
        return {"available": False, "reason": "无可用行"}

    if ukey:
        _unified_score_cache.update(key=ukey, rows=rows)

    if len(rows) > max_points:
        step = max(1, len(rows) // max_points)
        sampled = rows[::step]
        if sampled[-1] != rows[-1]:
            sampled.append(rows[-1])
        rows = sampled

    return {
        "available": True,
        "series": rows,
        "first_date": rows[0]["date"],
        "last_date": rows[-1]["date"],
        "last_score": rows[-1]["score"],
    }


def get_pic_path(name: str) -> Optional[Path]:
    """取 data/pic/ 下的 PNG（用于 proxy 静态图）。"""
    if "/" in name or ".." in name or not name.lower().endswith(".png"):
        return None
    try:
        return _safe_resolve(f"data/pic/{name}")
    except ValueError:
        return None


def data_status() -> Dict[str, Any]:
    """数据状态总览（用于顶部 banner：哪些源就绪/缺失）。"""
    root = _get_data_root()
    # Phase 2: 只走自管路径
    def _sh000001():
        for path in [
            "data/raw/stock_zh_index_daily_sh000001.csv",
            "data/raw/akshare/stock_zh_index_daily_sh000001.csv",  # 兼容旧自管子目录
        ]:
            try:
                p = _safe_resolve(path)
                if p.exists():
                    return p
            except ValueError:
                continue
        return None

    sh_p = _sh000001()
    required = [
        ("data/results/cycle_signal_latest.json", "周期信号"),
    ]
    indicators_present = []
    for meta in INDICATOR_META:
        if meta.get("use_composite"):
            continue
        try:
            p = _safe_resolve(f"data/processed/{meta['file']}")
            ok = p.exists()
        except ValueError:
            ok = False
        indicators_present.append(
            {
                "key": meta["key"],
                "name": meta["name"],
                "ok": ok,
                "path": str(_safe_resolve(f"data/processed/{meta['file']}")) if ok else None,
            }
        )
    return {
        "data_root": str(root),
        "data_root_exists": root.exists(),
        "summary_ok": _safe_resolve("data/results/cycle_signal_latest.json").exists(),
        "index_ok": sh_p is not None,
        "sh000001_path": str(sh_p) if sh_p else None,
        "indicators": indicators_present,
        "xbx_freshness": _xbx_data_freshness(),
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# ---------------------------------------------------------------------------
# 手动刷新：in-process 调自管算法 (fetcher/stock_top_algorithms/) 的核心函数
# 设计：单飞（已有任务在跑就不接新任务）+ 状态机 + contextlib.redirect_stdout
#       抓内部 print 当流式进度。不调 auto_runner、不发子进程、不写 step flag
#       （step flag 由自管 COMPLETION_FLAGS_DIR 自己管, Phase 2 已统一指自管目录）
# ---------------------------------------------------------------------------

_cycle_refresh_lock = threading.Lock()
_cycle_refresh_state: Dict[str, Any] = {
    "status": "idle",          # idle | running | done | error
    "mode": "quick",           # quick（仅 step2+3）| full（step1+2+3）
    "started_at": "",
    "finished_at": "",
    "exit_code": None,
    "step": "",                # 解析自 stdout 的 "步骤 1/4" 类
    "step_label": "",          # 人类可读
    "last_log": "",            # 最近一行 stdout（截断 500 字）
    "log_tail": [],            # 最近 30 行（前端可显示滚动日志）
    "error": "",
    "target_date": "",         # 传给 auto_runner 的 --date 参数
}


def _resolve_python() -> str:
    """找跑自管算法 (fetcher/stock_top_algorithms/) 的 python 解释器（fallback 模式用）。

    优先级：
    1. CYCLE_REFRESH_PYTHON 环境变量
    2. /opt/anaconda3/envs/Kun/bin/python（user 之前给算法配的 conda env, 兼容旧 fallback）
    3. /opt/anaconda3/bin/python（base env，也有 joblib）
    4. python3 / python（系统 PATH）
    """
    env = os.environ.get("CYCLE_REFRESH_PYTHON")
    if env and Path(env).exists():
        return env
    for cand in [
        "/opt/anaconda3/envs/Kun/bin/python",
        "/opt/anaconda3/bin/python",
        shutil.which("python3") or "",
        shutil.which("python") or "",
    ]:
        if cand and Path(cand).exists():
            return cand
    raise FileNotFoundError("找不到可用的 Python 解释器（CYCLE_REFRESH_PYTHON 也未配置）")


def _compute_target_date_str() -> str:
    """算出合理的 target_date（YYYYMMDD），传 --date 给 auto_runner。

    规则（参考 auto_runner.main 的 18:00 触发逻辑）：
    - 现在 ≥ 18:00 → target = 今天（盘已收，akshare 应该能拉到）
    - 现在 < 18:00 → target = 昨天（今天还没收盘，目标就是最近一个已收盘日）

    这样能避免 auto_runner 内部用"今天"但 akshare 拉不到"今天"的尴尬。
    """
    now = datetime.now()
    if now.hour >= 18:
        return now.strftime("%Y%m%d")
    yesterday = now - timedelta(days=1)
    return yesterday.strftime("%Y%m%d")


def _step_from_line(line: str) -> Tuple[str, str]:
    """从 stdout 一行解析当前 step。返回 (key, label)。"""
    line = line.strip()
    if not line:
        return ("", "")
    # auto_runner.py 步骤识别（基于 _run_pipeline 中的 print）
    if "步骤 1/" in line or "Step 1" in line or "1. 拉取" in line:
        return ("step1", "① 拉取 akshare 数据")
    if "步骤 2/" in line or "Step 2" in line or "2. 计算" in line:
        return ("step2", "② 计算 8 指标")
    if "步骤 3/" in line or "Step 3" in line or "3. 生成" in line:
        return ("step3", "③ 生成周期信号")
    if "步骤 4/" in line or "Step 4" in line or "4. 验证" in line or "产出验证" in line:
        return ("step4", "④ 轻量级产出验证")
    # run_indicators.py 三个阶段
    if "步骤 1/3" in line or "调用API拉取" in line:
        return ("step1", "① 拉取 akshare 数据")
    if "步骤 2/3" in line or "注册并计算" in line:
        return ("step2", "② 计算 8 指标")
    if "步骤 3/3" in line or "生成周期信号图" in line:
        return ("step3", "③ 生成周期信号")
    if "✓" in line and "周期信号" in line:
        return ("step3", "③ 生成周期信号")
    if "完成" in line or "执行完成" in line:
        return ("done", "✓ 已完成")
    return ("", "")


class _RefreshStream:
    """contextlib.redirect_stdout 用的 stream。

    把对方模块里的 print() 写到这里，实时转发到 _cycle_refresh_state。
    保留原始 stdout（server 控制台仍然能看到日志）。
    """

    def __init__(self, state: Dict[str, Any], lock: threading.Lock) -> None:
        self._state = state
        self._lock = lock
        self._buffer: List[str] = []

    def write(self, s: str) -> int:
        if not s:
            return 0
        # 多行同时进来（带 '\n'）按行切
        for line in s.splitlines():
            if not line:
                continue
            self._buffer.append(line[:200])
            # 保留 200 行: 30 行太短, 出错时关键上下文 (Traceback 起点等) 常被冲掉
            if len(self._buffer) > 200:
                self._buffer = self._buffer[-200:]
            with self._lock:
                self._state["last_log"] = line[:500]
                self._state["log_tail"] = list(self._buffer)
                step_key, step_label = _step_from_line(line)
                if step_key:
                    self._state["step"] = step_key
                    self._state["step_label"] = step_label
        # 原始 stdout 仍然打，方便 server 控制台/日志
        try:
            sys.__stdout__.write(s)
        except Exception:
            pass
        return len(s)

    def flush(self) -> None:  # noqa: D401
        try:
            sys.__stdout__.flush()
        except Exception:
            pass


def _log_refresh(msg: str, stream: Optional[_RefreshStream] = None) -> None:
    """refresh 线程内打一条结构化日志，同时通过 stream 推到前端。"""
    with _cycle_refresh_lock:
        _cycle_refresh_state["last_log"] = msg[:500]
        tail = list(_cycle_refresh_state.get("log_tail", []))
        tail.append(msg[:200])
        _cycle_refresh_state["log_tail"] = tail[-30:]
    if stream is not None:
        stream.write(msg + "\n")


# 全局：in-process 是否可用（ModuleNotFoundError 时设为 True，后续直接走 subprocess）
_in_process_unavailable: bool = False


def _probe_in_process_available(stream: _RefreshStream) -> bool:
    """探测自管算法 (fetcher/stock_top_algorithms/) 的依赖在当前 venv 是否齐。

    失败时把缺的包列出来，提示用户装到 venv 或设 CYCLE_REFRESH_PYTHON 用别的解释器。
    """
    global _in_process_unavailable
    if _in_process_unavailable:
        return False
    try:
        import joblib  # noqa: F401
        import matplotlib  # noqa: F401
        import scipy  # noqa: F401
    except ModuleNotFoundError as e:
        _in_process_unavailable = True
        _log_refresh(
            f"⚠ in-process 不可用（缺 {e.name}），自动 fallback 到 subprocess 调 auto_runner",
            stream,
        )
        return False
    return True


def _run_in_process(stream: _RefreshStream, data_root: Path, target_date: str) -> bool:
    """in-process 重算：import run_indicators + run_cycle_signal_runner。

    成功返回 True；任何异常都返回 False（不抛），让上层决定是否 fallback。
    """
    try:
        # Phase 2: 算法根目录改用自管的 fetcher/stock_top_algorithms/
        # (取代原 stock-top-and-bottom-analysis, 已不再依赖)
        algo_dir = str(Path(__file__).resolve().parent / "stock_top_algorithms")
        if algo_dir not in sys.path:
            sys.path.insert(0, algo_dir)

        from run_indicators import build_and_calculate  # noqa: WPS433

        with _cycle_refresh_lock:
            _cycle_refresh_state["step"] = "step2"
            _cycle_refresh_state["step_label"] = "② 计算 8 指标"
        _log_refresh("→ 开始计算 8 指标 (in-process)...", stream)

        with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
            manager, results = build_and_calculate()
        _log_refresh(f"✓ 指标计算完成: {len(results)} 个 (不写 flag)", stream)

        from program.cycle_signal.runner import run_cycle_signal_step  # noqa: WPS433

        with _cycle_refresh_lock:
            _cycle_refresh_state["step"] = "step3"
            _cycle_refresh_state["step_label"] = "③ 生成周期信号"
        _log_refresh("→ 开始生成周期信号 (in-process)...", stream)

        with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
            report = run_cycle_signal_step()

        _log_refresh(
            f"✓ 周期信号完成: date={report.get('date')} "
            f"score={report.get('cycle_score')} phase={report.get('phase')}",
            stream,
        )
        return True
    except ModuleNotFoundError as e:
        # 探测时漏掉的深层 import 错误（懒加载等）
        global _in_process_unavailable
        _in_process_unavailable = True
        _log_refresh(f"⚠ in-process 运行时发现缺 {e.name}，fallback 到 subprocess", stream)
        return False
    except Exception as e:
        # 其他异常：in-process 整体失败，fallback
        import traceback

        _log_refresh(f"⚠ in-process 失败: {type(e).__name__}: {e}", stream)
        _log_refresh(traceback.format_exc(limit=3)[:500], stream)
        return False


def _run_subprocess_fallback(
    stream: _RefreshStream,
    data_root: Path,
    target_date: str,
) -> bool:
    """subprocess fallback：用 Kun env python 跑两个 mini 步骤。

    只跑 step2+3（build_and_calculate + run_cycle_signal_step），
    不调 auto_runner.py（避免它写 flag、推企微、跑 step1 拉数据）。

    自管策略: _run_subprocess_step 的 bootstrap 会在 subprocess 进程内
    永久改写对方 config 路径指自管 + reload 对方模块, 跑完直接写自管,
    **不写对方 disk, 不需要 copy 逻辑**。
    """
    try:
        py = _resolve_python()
    except FileNotFoundError as e:
        _log_refresh(f"✗ fallback 也找不到 python: {e}", stream)
        return False

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    # step2: 算指标
    with _cycle_refresh_lock:
        _cycle_refresh_state["step"] = "step2"
        _cycle_refresh_state["step_label"] = "② 计算 8 指标 (subprocess)"
    _log_refresh(f"→ fallback subprocess step2: {py} build_and_calculate (自管路径)", stream)

    step2_code = (
        "import sys; "
        "sys.path.insert(0, '.'); "
        "from run_indicators import build_and_calculate; "
        "manager, results = build_and_calculate(); "
        "print(f'[fallback] step2 done: {len(results)} indicators', flush=True); "
        "import config as _cfg; "
        "print(f'[fallback]   config.PROCESSED_DATA_DIR = {_cfg.PROCESSED_DATA_DIR}', flush=True); "
    )
    ok = _run_subprocess_step(py, step2_code, data_root, env, stream)
    if not ok:
        return False

    # step3: 算周期信号
    with _cycle_refresh_lock:
        _cycle_refresh_state["step"] = "step3"
        _cycle_refresh_state["step_label"] = "③ 生成周期信号 (subprocess)"
    _log_refresh(f"→ fallback subprocess step3: {py} run_cycle_signal_step (自管路径)", stream)

    step3_code = (
        "import sys; "
        "sys.path.insert(0, '.'); "
        "from program.cycle_signal.runner import run_cycle_signal_step; "
        "report = run_cycle_signal_step(); "
        "print(f'[fallback] step3 done: date={report.get(\"date\")} score={report.get(\"cycle_score\")} phase={report.get(\"phase\")}', flush=True); "
    )
    ok = _run_subprocess_step(py, step3_code, data_root, env, stream)
    if not ok:
        return False

    # 验证: Phase 2 后 stock-top-and-bottom-analysis 已移除, 这里跳过老目录检查
    legacy_dir = Path(__file__).resolve().parent.parent / "stock-top-and-bottom-analysis"
    if legacy_dir.exists():
        st_results = legacy_dir / "data" / "results" / "cycle_signal_latest.json"
        if st_results.exists():
            _log_refresh(f"⚠ legacy {st_results.name} 仍存在 (Phase 2 移除后应该消失)", stream)
    _log_refresh("✓ fallback 产物已写到自管目录 (market-radar/data/)", stream)

    return True


def _run_subprocess_full(
    stream: _RefreshStream,
    data_root: Path,
) -> bool:
    """subprocess 跑完整 self_full_refresh (XBX+akshare+11指标+周期信号)。

    之前 mode='full' 走 in-process _run_self_full_refresh(), 5min+ 占满
    waitress 8 worker (XBX 扫 5544 CSV 大量 pd.read_csv), 让前端进 CyclePage
    卡 13s+。现在改成 subprocess 完全隔离, server 进程无感知。

    返回 bool (跟 _run_subprocess_fallback 接口一致)。
    """
    try:
        py = _resolve_python()
    except FileNotFoundError as e:
        _log_refresh(f"✗ full subprocess 找不到 python: {e}", stream)
        return False

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    with _cycle_refresh_lock:
        _cycle_refresh_state["step"] = "self_full_subprocess"
        _cycle_refresh_state["step_label"] = "完整刷新 (subprocess 隔离)"
    _log_refresh(
        f"→ full mode: subprocess 跑 self_full_refresh (XBX+akshare+11指标+周期信号, 完全隔离)",
        stream,
    )

    # 跟 _run_subprocess_fallback 同样一行 code (subprocess -c 单行), 调顶层 run_full_refresh
    # run_full_refresh 已含 install() + 4 步全流程, 输出逐行 flush
    # 注意 (r.get('error') or ''): error 键可能存在但值为 None, 直接 [:300] 会 TypeError
    # 末尾按 success 设 exit code: _run_subprocess_step 只认 returncode,
    # 不显式退出的话优雅失败 (return dict) 也会 exit 0 被误判成功
    # (注意是 sys.exit 不是 os.exit — os 模块没有 exit 属性)
    full_code = (
        "import sys; "
        "sys.path.insert(0, '.'); "
        "from fetcher.cycle_self_runner import run_full_refresh; "
        "r = run_full_refresh(); "
        "import json as _j; "
        "print('[full_subprocess]', _j.dumps({'success': r.get('success'), 'step': r.get('step'), 'error': (r.get('error') or '')[:300]}, ensure_ascii=False), flush=True); "
        "sys.exit(0 if r.get('success') else 1)"
    )
    ok = _run_subprocess_step(py, full_code, data_root, env, stream)
    if not ok:
        _log_refresh("✗ self_full_refresh subprocess 失败", stream)
        return False
    _log_refresh("✓ self_full_refresh subprocess 完成 (写自管目录, 不抢 waitress worker)", stream)
    return True


def _run_subprocess_step(
    py: str,
    code: str,
    data_root: Path,
    env: Dict[str, str],
    stream: _RefreshStream,
) -> bool:
    """subprocess 跑一段 python 代码，行级 stdout 推到 stream。

    自动在 code 前 prepend sys.path 注入自管算法目录 fetcher/stock_top_algorithms/,
    让 subprocess 进程能找到 run_indicators / program.cycle_signal.runner 等模块
    (Phase 2: 不再用原 stock-top-and-bottom-analysis)。
    """
    # 注入自管算法目录到 sys.path (Phase 2: 不再用原 stock-top-and-bottom-analysis)
    algo_dir = str(Path(__file__).resolve().parent / "stock_top_algorithms")
    # 自管路径 (market-radar/data/) — fallback 也要写自管
    self_raw = str(data_root / "data" / "raw")
    self_processed = str(data_root / "data" / "processed")
    self_results = str(data_root / "data" / "results")
    self_pic = str(data_root / "data" / "pic")
    self_data = str(data_root / "data")
    # 注意: 全部用一行 (subprocess -c 只能单行), 不能用 for ... : ...; 之后还有 statement (SyntaxError)
    # mkdir 用列表推导式代替 for 循环
    bootstrap = (
        "import sys as _sys, os as _os, importlib as _imp; "
        f"_sys.path.insert(0, '{algo_dir}'); "
        f"_sys.path.insert(0, '.'); "
        # mkdir 自管目录 (列表推导式代替 for: 避免 for 同行多 statement 语法错)
        f"[_os.makedirs(_d, exist_ok=True) for _d in ['{self_raw}', '{self_processed}', '{self_results}', '{self_pic}']]; "
        # install: 永久改写自管 config 路径指自管 (subprocess 进程内的 config module 状态)
        # 注意: 改的必须是 Path 对象 (代码大量用 RAW_DATA_DIR / "..." 拼路径, 字符串会 TypeError)
        "from pathlib import Path as _P; "
        "import config as _cfg; "
        f"_cfg.RAW_DATA_DIR = _P('{self_raw}').resolve(); "
        f"_cfg.PROCESSED_DATA_DIR = _P('{self_processed}').resolve(); "
        f"_cfg.RESULTS_DIR = _P('{self_results}').resolve(); "
        f"_cfg.PIC_DIR = _P('{self_pic}').resolve(); "
        f"_cfg.DATA_DIR = _P('{self_data}').resolve(); "
        # reload 任何已 import 的自管模块, 让 `from config import` 重新求值
        # (注意: 跳过 'config' 本身, reload config 会重置路径常量)
        # 用 exec 包裹 reload 循环避免 for 同行问题
        "_exec_reload = '[__import__(\"importlib\").reload(__import__(\"sys\").modules[n]) for n in [\"run_indicators\", \"program.cycle_signal.runner\", \"program.indicators\", \"program.api.load_xbx_data\", \"program.weight_optimizer.indicator_preprocessor\", \"program.weight_optimizer.label_generator\", \"program.weight_optimizer.visualizer\", \"program.weight_optimizer.cycle_position\", \"program.api.stock_index_pe\", \"program.api.bond_zh_us_rate\", \"program.api.stock_zh_index_daily\", \"program.api.stock_a_below_net_asset_statistics\", \"program.indicators.equity_premium_indicator\", \"program.indicators.pe_valuation_indicator\", \"program.indicators.price_percentile_indicator\", \"program.indicators.market_turnover_percentile_indicator\", \"program.indicators.ma250_bias_indicator\", \"program.indicators.market_crowdedness_indicator\", \"program.indicators.below_net_asset_indicator\", \"program.indicators.herding_rate_indicator\", \"program.indicators.breadth_above_ma_indicator\", \"program.indicators.indicator_manager\"] if n in __import__(\"sys\").modules]'; "
        f"exec(_exec_reload); "
    )
    code = bootstrap + code

    try:
        proc = subprocess.Popen(
            [py, "-c", code],
            cwd=str(data_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
    except Exception as e:
        _log_refresh(f"✗ 启动 subprocess 失败: {e}", stream)
        return False

    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            stream.write(line + "\n")
        proc.wait()
        if proc.returncode != 0:
            _log_refresh(f"✗ subprocess exit code {proc.returncode}", stream)
            return False
        return True
    except Exception as e:
        _log_refresh(f"✗ 读 stdout 失败: {e}", stream)
        return False


def _run_cycle_refresh() -> None:
    """后台线程：刷新 cycle signal (按 _cycle_refresh_state["mode"] 走 quick 或 full)"""
    global _cycle_refresh_state
    # 同步读 mode（trigger 时已写）
    with _cycle_refresh_lock:
        mode = _cycle_refresh_state.get("mode", "quick")
    return _do_run_cycle_refresh(mode)


def _do_run_cycle_refresh(mode: str = "quick") -> None:
    """后台线程：刷新 cycle signal。

    mode:
      - "quick" (默认): 假设 raw_data 已齐，只跑 step2 + step3（in-process → fallback subprocess）
      - "full": 先拉 XBX + akshare, 再算 11 指标 + 周期信号（全 in-process，自管路径）

    策略：
    0. install() — 永久改写自管 config 路径指自管 + reload 自管算法模块
       （原 stock-top-and-bottom-analysis disk 不会被写, 数据全写到 market-radar/data/）
    1. full 模式: 调 cycle_self_runner.run_full_refresh() (XBX+akshare+指标+周期信号 一气呵成)
    2. quick 模式: 调 _run_in_process (自管算法路径, 自动写自管)
    3. in-process 失败 → fallback subprocess (subprocess bootstrap 也内置 install, 仍写自管)
    4. 清 target_date 的旧 flag (自管 COMPLETION_FLAGS_DIR 下的 flag, 避免锁死结果)
    5. 跑完清本模块 mtime 缓存, 下次 /api/cycle/* 自动 reload
    """
    global _cycle_refresh_state
    data_root = _get_data_root()
    if not data_root.exists():
        with _cycle_refresh_lock:
            _cycle_refresh_state.update({
                "status": "error",
                "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "error": f"data_root 不存在: {data_root}",
            })
        return

    # 0. install 自管路径 (幂等, 第一次执行真正工作)
    install_info = _install_self_runner()
    if install_info.get("changes"):
        # 第一次 install 才打印详细信息
        n_changes = len(install_info["changes"])
        n_reloaded = len(install_info["reloaded"])
        print(f"[cycle_signal] self_runner installed: {n_changes} path overrides, "
              f"{n_reloaded} modules reloaded, 数据全写到自管目录", flush=True)

    stream = _RefreshStream(_cycle_refresh_state, _cycle_refresh_lock)

    with _cycle_refresh_lock:
        _cycle_refresh_state.update({
            "status": "running",
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": "",
            "exit_code": None,
            "step": "",
            "step_label": "",
            "last_log": "",
            "log_tail": [],
            "error": "",
        })

    target_date = _compute_target_date_str()
    with _cycle_refresh_lock:
        _cycle_refresh_state["target_date"] = target_date
    _log_refresh(f"刷新开始: target_date={target_date}, mode={mode}, data_root={data_root}", stream)

    # 打印当前 config 路径 (确认已指自管)
    _log_refresh(f"config 路径: {json.dumps(_get_self_config_paths(), ensure_ascii=False)}", stream)

    original_cwd: Optional[str] = None
    success = False
    try:
        # 切 cwd（auto_runner 用 cwd 找 config.py）
        original_cwd = os.getcwd()
        os.chdir(data_root)

        # 1. 清旧 flag
        flags_dir = data_root / "data" / "completion_flags"
        removed = 0
        if flags_dir.exists():
            for flag in flags_dir.glob(f"{target_date}_*.flag"):
                try:
                    flag.unlink()
                    removed += 1
                except Exception as e:
                    _log_refresh(f"[warn] 删 flag 失败 {flag.name}: {e}", stream)
        _log_refresh(f"清理旧 flag: {removed} 个", stream)

        # 2. full 模式：subprocess 跑 self_runner (XBX+akshare+指标+周期信号)
        #    之前 in-process 跑 5min+ 占满 waitress 8 worker 让 CyclePage 进页卡 13s+
        #    现在 subprocess 独立进程, server 进程无感知
        if mode == "full":
            with _cycle_refresh_lock:
                _cycle_refresh_state["step"] = "self_full_subprocess"
                _cycle_refresh_state["step_label"] = "完整刷新 (subprocess 隔离)"
            _log_refresh("→ full 模式: subprocess 跑 self_full_refresh (XBX+akshare+11 指标+周期信号, 隔离)", stream)
            if _run_subprocess_full(stream, data_root):
                success = True
                _log_refresh("✓ self_full_refresh subprocess 完成", stream)
            else:
                _log_refresh("⚠ self_full_refresh subprocess 失败, 尝试 fallback", stream)

        # 3. quick 模式: 跑 step2+3 (in-process, 已 self-managed 因为 install 改了 config)
        if not success and mode == "quick":
            if _probe_in_process_available(stream):
                _log_refresh("→ quick 模式: 走 in-process (config 已自管)", stream)
                success = _run_in_process(stream, data_root, target_date)

        # 4. fallback subprocess (全模式都兜底)
        if not success:
            if _in_process_unavailable:
                _log_refresh("→ in-process 不可用，走 subprocess fallback", stream)
            else:
                _log_refresh("→ in-process 失败，走 subprocess fallback", stream)
            success = _run_subprocess_fallback(stream, data_root, target_date)

        # 5. notify: 跑完推企微 (可选, 默认不推)
        if success and _cycle_refresh_state.get("notify"):
            notify_addr = _cycle_refresh_state.get("notify_address", "info")
            _log_refresh(f"→ 推送企微机器人 (notify=1, address={notify_addr})...", stream)
            try:
                # 走自管 notifier 推送, 从 disk 读最新 json + png
                from fetcher.cycle_notifier import push_cycle_from_disk
                push_result = push_cycle_from_disk(address=notify_addr)
                if push_result.get("skipped_reason"):
                    _log_refresh(f"⚠ 推送跳过: {push_result['skipped_reason']}", stream)
                else:
                    _log_refresh(
                        f"{'✓' if push_result.get('text_ok') else '✗'} 文本推送, "
                        f"{'✓' if push_result.get('image_ok') else '✗'} 图片推送 "
                        f"(address={push_result.get('address')})",
                        stream,
                    )
                with _cycle_refresh_lock:
                    _cycle_refresh_state["notify_result"] = push_result
            except Exception as e:
                _log_refresh(f"⚠ 推送异常 (不影响主流程): {type(e).__name__}: {e}", stream)

        if success:
            with _cycle_refresh_lock:
                _cycle_refresh_state.update({
                    "status": "done",
                    "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "step": "done",
                    "step_label": "✓ 已完成",
                    "exit_code": 0,
                })
        else:
            err = "in-process 和 subprocess 都失败，详见 log_tail"
            with _cycle_refresh_lock:
                _cycle_refresh_state.update({
                    "status": "error",
                    "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "step_label": "✗ 失败",
                    "error": err,
                })
    except Exception as e:
        import traceback

        err = f"{type(e).__name__}: {e}"
        _log_refresh(f"[error] {err}", stream)
        _log_refresh(traceback.format_exc(limit=3)[:500], stream)
        with _cycle_refresh_lock:
            _cycle_refresh_state.update({
                "status": "error",
                "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "step_label": "✗ 失败",
                "error": err,
            })
    finally:
        if original_cwd is not None:
            try:
                os.chdir(original_cwd)
            except Exception:
                pass
        # 强制清空本模块 mtime 缓存，下次 /api/cycle/* 重新读 CSV/JSON
        _csv_cache.clear()
        _json_cache.clear()


def trigger_cycle_refresh(
    mode: str = "quick",
    notify: bool = False,
    notify_address: str = "info",
) -> Dict[str, Any]:
    """启动后台刷新任务（单飞）。返回当前状态。

    mode:
      - "quick" (默认): 只跑 step2+3，假设 raw_data 已齐
      - "full": 跑 step1+2+3（先 akshare 拉数据,再算指标）
    notify:
      - True: 跑完成功后推送到企业微信机器人
      - False (默认): 不推
    notify_address:
      - 推送地址 (默认 'info', 可选从 list_addresses() 看)
      - 跟 notify=True 配合用, notify=False 时忽略
    """
    if mode not in ("quick", "full"):
        mode = "quick"
    with _cycle_refresh_lock:
        if _cycle_refresh_state["status"] == "running":
            return {
                "started": False,
                "reason": "已有任务在跑",
                "state": dict(_cycle_refresh_state),
            }
        # 先把 mode + notify + notify_address 写进 state,后台线程会读
        _cycle_refresh_state["mode"] = mode
        _cycle_refresh_state["notify"] = bool(notify)
        _cycle_refresh_state["notify_address"] = notify_address or "info"
    t = threading.Thread(target=_run_cycle_refresh, daemon=True, name="cycle-refresh")
    t.start()
    return {
        "started": True,
        "state": dict(_cycle_refresh_state),
    }


def cycle_refresh_status() -> Dict[str, Any]:
    """返回当前刷新状态（前端轮询）。"""
    with _cycle_refresh_lock:
        state = dict(_cycle_refresh_state)
    state["data_root"] = str(_get_data_root())
    return state

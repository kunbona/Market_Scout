"""quant/snapshot.py — 全市场日线日快照 (2026-08-18)

为什么存在:
    一次完整复盘重算 = L4 review_compute + 6 个 DM-kun 子进程, 7 个消费方
    各自 pd.read_csv 直读全部 5885 个 gbk CSV (~4.6GB), 合计约 6.5 遍全市场
    扫描。本模块把"读一遍 CSV"收敛成一次性动作: 并行读全部 CSV 一遍, 落一个
    本地 parquet 快照, 之后所有消费方按需读列 (列式存储, 不用的列不读)。

快照规格:
    - 全部 38 列原样保留 (消费方各自做单位换算, 快照存原始值)
    - 每股保留尾部 KEEP_ROWS=1900 行 (盖住 industry_crowding 的 tail(1800),
      margin 100; 其余消费方窗口 ≤250)
    - 北交所 (bj*) 一并收入 —— sentiment_cycle / market_regime 的现役行为
      包含 bj; 其余消费方按各自原有逻辑在消费端过滤 (快照不动数据)
    - 按 交易日期 全局升序排序 → parquet row group 日期连续,
      read_parquet(filters=日期) 能做行组裁剪
    - 落盘: data/snapshot/stocks.parquet + meta.json (本地 SSD, 不占 Vesta)

新鲜度:
    - meta.json 记录构建时的源目录最大 mtime; 消费时扫一遍源目录 mtime
      (~5885 次 stat, 毫秒级), 源文件有更新 (fetcher 凌晨写入) → 重建
    - 重建用 O_EXCL 锁文件互斥; 复盘链是串行的, 锁主要防 server 进程
      (L4) 与手动跑脚本撞车。持锁者崩溃留下的陈锁 30 分钟后可抢。

行为变化说明 (相对直读 CSV):
    - 上市超过 1900 交易日的股票, 看不到 1900 行之前的历史 (没有任何
      现役消费方需要那么远: 最深的 industry_crowding 只要 tail(1800))
    - 长期停牌股的陈旧行若落在 1900 行窗口外会被丢弃 (旧行不影响
      近端日期的聚合指标)
"""

from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# 每股保留的尾部行数: industry_crowding 需要 tail(1800), 留 100 行 margin
KEEP_ROWS = 1900

_REPO_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_DIR = _REPO_ROOT / "data" / "snapshot"
_PARQUET = SNAPSHOT_DIR / "stocks.parquet"
_META = SNAPSHOT_DIR / "meta.json"
_LOCK = SNAPSHOT_DIR / ".build.lock"

_LOCK_STALE_SEC = 1800   # 陈锁可抢时间
_WAIT_TIMEOUT_SEC = 1800  # 等他人构建的超时
_META_VERSION = 1


# ── 源目录工具 ───────────────────────────────────────────────────────────────

def _source_dir() -> Path:
    root = os.environ.get("QUANT_DATA_ROOT", "").strip()
    if not root:
        raise FileNotFoundError("QUANT_DATA_ROOT 未设置 (检查 .env.local / plist 注入)")
    p = Path(root) / "stock-trading-data-pro"
    if not p.exists():
        raise FileNotFoundError(f"源目录不存在: {p}")
    return p


def _source_max_mtime() -> float:
    """源目录全部 CSV 的最大 mtime (os.scandir 一次遍历, 毫秒级)。"""
    t = 0.0
    with os.scandir(_source_dir()) as it:
        for e in it:
            if e.name.endswith(".csv"):
                m = e.stat().st_mtime
                if m > t:
                    t = m
    return t


# ── 构建 ─────────────────────────────────────────────────────────────────────

def _read_one_tail(fp: str, n_rows: int) -> pd.DataFrame | None:
    """工作进程: 读单只股票 CSV, 返回尾部 n_rows 行 (None = 跳过/空)。"""
    try:
        df = pd.read_csv(fp, encoding="gbk", skiprows=1)
    except Exception:
        return None
    if df.empty or "交易日期" not in df.columns:
        return None
    try:
        df["交易日期"] = pd.to_datetime(df["交易日期"], errors="coerce")
    except Exception:
        return None
    df = df.dropna(subset=["交易日期"])
    if df.empty:
        return None
    if len(df) > n_rows:
        df = df.tail(n_rows)
    return df


def build_snapshot(n_jobs: int | None = None) -> Path:
    """并行读全部源 CSV 一遍 → 写 parquet + meta。返回 parquet 路径。"""
    t0 = time.time()
    src = _source_dir()
    # 竞态修复 (2026-09-02): mtime 必须在读盘前记录。
    # 旧实现读完盘才记 mtime, 若构建期间 fetcher 正在写新数据, meta 会记到
    # 新 mtime 但 parquet 里是旧数据 → _is_fresh 永远判新鲜, 新数据永不入库
    # (事故: 09-01 20:30 构建读到 08-31 仅 63 只的半成品截面)。
    # 记读前 mtime 后: 构建期间源有更新 → 当前 mtime > meta → 判陈旧 → 下次消费自动重建。
    source_mtime_before = _source_max_mtime()
    files = sorted(str(p) for p in src.glob("*.csv"))
    if not files:
        raise RuntimeError(f"源目录没有 CSV: {src}")
    logger.info("[snapshot] 开始构建: %d 个源文件, 每股尾部 %d 行", len(files), KEEP_ROWS)

    if n_jobs is None:
        n_jobs = max(1, (os.cpu_count() or 4) // 2)

    frames: list[pd.DataFrame] = []
    n_fail = 0
    with ProcessPoolExecutor(max_workers=n_jobs) as ex:
        futs = {ex.submit(_read_one_tail, f, KEEP_ROWS): f for f in files}
        for fut in as_completed(futs):
            try:
                r = fut.result()
            except Exception:
                r = None
            if r is None:
                n_fail += 1
                continue
            frames.append(r)

    if not frames:
        raise RuntimeError("快照构建: 没有成功读到任何股票数据")

    full = pd.concat(frames, ignore_index=True)
    del frames
    full = full.sort_values(["交易日期", "股票代码"], kind="stable").reset_index(drop=True)

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _PARQUET.with_suffix(".parquet.tmp")
    full.to_parquet(tmp, engine="pyarrow", compression="snappy",
                    index=False, row_group_size=250_000)
    os.replace(tmp, _PARQUET)

    meta = {
        "version": _META_VERSION,
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "data_date": str(full["交易日期"].max().date()),
        "n_stocks": int(full["股票代码"].nunique()),
        "n_rows": int(len(full)),
        "keep_rows": KEEP_ROWS,
        "source_max_mtime": source_mtime_before,
    }
    _META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("[snapshot] 构建完成: %d 只 / %d 行 / 数据日期 %s / 耗时 %.1fs%s",
                meta["n_stocks"], meta["n_rows"], meta["data_date"],
                time.time() - t0, f" / {n_fail} 个文件读取失败" if n_fail else "")
    return _PARQUET


# ── 新鲜度与锁 ───────────────────────────────────────────────────────────────

def _read_meta() -> dict | None:
    try:
        m = json.loads(_META.read_text(encoding="utf-8"))
        if m.get("version") == _META_VERSION and _PARQUET.exists():
            return m
    except Exception:
        pass
    return None


def _is_fresh(meta: dict) -> bool:
    """快照构建时的源 mtime >= 当前源 mtime → 源没动过, 快照可用。"""
    try:
        return meta.get("source_max_mtime", 0) >= _source_max_mtime()
    except Exception:
        return False


def _try_lock() -> bool:
    try:
        fd = os.open(_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        return False


def _release_lock() -> None:
    try:
        _LOCK.unlink()
    except FileNotFoundError:
        pass


def _steal_stale_lock() -> None:
    """持锁者崩溃留下的陈锁 (超过 _LOCK_STALE_SEC) 直接抢走。"""
    try:
        age = time.time() - _LOCK.stat().st_mtime
        if age > _LOCK_STALE_SEC:
            logger.warning("[snapshot] 发现陈锁 (%.0fs), 抢占重建", age)
            _LOCK.unlink()
    except FileNotFoundError:
        pass


def ensure_snapshot() -> Path:
    """快照新鲜则返回路径; 否则重建 (或等正持有锁的进程建完)。"""
    meta = _read_meta()
    if meta and _is_fresh(meta):
        return _PARQUET

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    _steal_stale_lock()
    if _try_lock():
        try:
            return build_snapshot()
        finally:
            _release_lock()
    # 锁被别人持有 → 等它建完 (复盘链串行, 通常是 server 进程 L4 先建)
    t0 = time.time()
    while time.time() - t0 < _WAIT_TIMEOUT_SEC:
        time.sleep(5)
        meta = _read_meta()
        if meta and _is_fresh(meta):
            logger.info("[snapshot] 等到他人构建完成 (%.0fs)", time.time() - t0)
            return _PARQUET
        if not _LOCK.exists():
            # 持锁者退出但快照仍不新鲜 → 自己接手
            if _try_lock():
                try:
                    return build_snapshot()
                finally:
                    _release_lock()
    raise TimeoutError("等待快照构建超时 (30 分钟)")


# ── 消费接口 ─────────────────────────────────────────────────────────────────

def snapshot_meta() -> dict:
    """快照元信息 (data_date 可当最新交易日用)。必要时先 ensure。"""
    meta = _read_meta()
    if meta and _is_fresh(meta):
        return meta
    ensure_snapshot()
    return _read_meta() or {}


def load_snapshot(columns: list[str] | None = None) -> pd.DataFrame:
    """读快照 (可指定列; 不指定的消费方注意内存 ~全列×1900 行)。"""
    ensure_snapshot()
    return pd.read_parquet(_PARQUET, columns=columns)


def iter_stock_frames(columns: list[str], tail_rows: int | None = None,
                      min_rows: int = 1, end_date: str | None = None):
    """按股票切好片的生成器: yield (code, df)。

    - df 按交易日期升序, 只含 columns 列
    - end_date: 先按 交易日期 <= end_date 过滤再取尾部 (与旧"整文件读入
      → 过滤 → tail"语义一致, 支持历史日期回看)
    - tail_rows: 每股只保留尾部 N 行 (None = 快照全部 1900 行)
    - min_rows: 少于该行数的股票跳过
    - 北交所包含在快照里 (sentiment_cycle / market_regime 需要);
      排除 bj 的消费方自行跳过 code 以 "bj" 开头的切片
    适合 DM-kun 脚本替换原来的 "遍历文件 + 进程池逐文件读" 模式:
    父进程读一次快照, 切片 pickle 给工作进程。
    """
    need = [c for c in dict.fromkeys(list(columns) + ["股票代码", "交易日期"])]
    df = load_snapshot(need)
    if end_date:
        df = df[df["交易日期"] <= pd.Timestamp(end_date)]
    if tail_rows is not None and tail_rows < KEEP_ROWS:
        # 全局按 (code, date) 排序后 groupby tail, 与逐文件 tail 语义一致
        df = df.sort_values(["股票代码", "交易日期"], kind="stable")
        df = df.groupby("股票代码", sort=False).tail(tail_rows)
    extra = [c for c in ("股票代码", "交易日期") if c not in columns]
    for code, g in df.groupby("股票代码", sort=False):
        if len(g) < min_rows:
            continue
        if extra:
            g = g.drop(columns=extra)
        yield code, g

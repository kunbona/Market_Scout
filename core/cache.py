"""core/cache.py — 通用线程安全缓存原语。

背景: 项目里散落着多种手写的 "dict + 时间戳 + Lock" 缓存模式
(每处各自实现过期判断/加锁), 语义重复且容易写错。本模块提供两种
覆盖绝大多数场景的原语, 各调用方统一改用它们:

1. TTLCache   — 按条目过期时间: get_or_set(key, loader), TTL 秒后失效。
                 典型: server.py 股池体检缓存 (原 _checkup_cache + _checkup_cache_ts)。
2. MtimeCache — 按文件 mtime 判新鲜: 文件没改就复用缓存, 改了重读。
                 典型: fetcher/cycle_signal.py 的 CSV/JSON 文件缓存
                 (原 _csv_cache/_json_cache + _cache_lock)。

设计约定 (与项目既有行为对齐):
- loader 在锁外执行, 不阻塞其他线程读已缓存条目;
- loader 返回 None 视为"无数据", 不写缓存(下次重试), 直接返回 None;
- clock 默认 time.monotonic (不受系统时间回拨影响)。

不收口的情况 (保留各模块自管): 有特殊淘汰/比较语义的缓存
(如 quant/loader.py 的窗口 lookback 比较 + FIFO 淘汰), 以及
"任务锁"(只防并发重入、不存数据) — 它们不是通用缓存。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

__all__ = ["TTLCache", "MtimeCache"]


class TTLCache:
    """线程安全的 TTL 缓存。

    用法:
        cache = TTLCache(ttl=300)
        value = cache.get_or_set("key", loader_fn)   # 过期或未命中自动调 loader

    - 每个条目独立计时: set 后超过 ttl 秒过期。
    - loader 在锁外执行 (慢计算不阻塞并发读); 并发下同一 key 可能
      重复计算一次, 与项目原手写实现行为一致 (checkup 缓存也是如此)。
    """

    def __init__(self, ttl: float, clock: Callable[[], float] = time.monotonic):
        if ttl <= 0:
            raise ValueError("ttl 必须为正数")
        self._ttl = float(ttl)
        self._clock = clock
        self._lock = threading.Lock()
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            ts, value = entry
            if self._clock() - ts > self._ttl:
                self._store.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._store[key] = (self._clock(), value)

    def get_or_set(self, key: str, loader: Callable[[], Any]) -> Optional[Any]:
        hit = self.get(key)
        if hit is not None:
            return hit
        value = loader()
        if value is None:
            return None
        self.set(key, value)
        return value

    def clear(self) -> int:
        with self._lock:
            n = len(self._store)
            self._store.clear()
            return n

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)


class MtimeCache:
    """按文件 mtime 判新鲜的文件内容缓存。

    用法:
        cache = MtimeCache()
        df = cache.get_or_load(path, loader=pd.read_csv, clone=lambda d: d.copy())

    - mtime 相同 → 命中, 返回缓存 (有 clone 则返回克隆, 保护缓存本体);
    - mtime 变化或首次 → 调 loader 重读并写入缓存;
    - loader 返回 None / 抛异常 → 不缓存, 返回 None (异常被吞掉,
      与 cycle_signal 原实现 "读失败返回 None" 行为一致);
    - 可传 key 覆盖默认的文件名键 (如同一文件想按不同语义缓存多份)。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._store: dict[str, tuple[float, Any]] = {}

    def get_or_load(
        self,
        path: Path,
        loader: Callable[[Path], Any],
        clone: Optional[Callable[[Any], Any]] = None,
        key: Optional[str] = None,
    ) -> Optional[Any]:
        path = Path(path)
        if not path.exists():
            return None
        mtime = path.stat().st_mtime
        cache_key = key if key is not None else str(path)
        with self._lock:
            cached = self._store.get(cache_key)
            if cached is not None and cached[0] == mtime:
                return clone(cached[1]) if clone else cached[1]
        try:
            data = loader(path)
        except Exception:
            return None
        if data is None:
            return None
        with self._lock:
            self._store[cache_key] = (mtime, data)
        return clone(data) if clone else data

    def clear(self) -> int:
        with self._lock:
            n = len(self._store)
            self._store.clear()
            return n

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)

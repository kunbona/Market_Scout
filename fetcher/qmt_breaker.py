"""
QMT Bridge 熔断器 (Circuit Breaker)

背景：QMT 桥跑在 Windows VM (10.211.55.4:5001)。VM 关机 / 网络断开 / 桥进程挂
都会让 Mac 端 fetcher 卡 30s 超时。持续重试会把内存涨到 14GB，waitress 线程
全堵死，dashboard 看似"崩了"。

熔断策略：
- 连续 FAIL_THRESHOLD 次失败 → 熔断 OPEN
- OPEN 状态持续 OPEN_SECS 秒 → 进入 HALF_OPEN（试探一次）
- HALF_OPEN 成功 → 关闭（CLOSED，恢复正常）
- HALF_OPEN 失败 → 重新 OPEN

设计取舍：
- 失败阈值 3（避免一抖动就熔断）
- 打开时长 60s（VM 拉起 + 桥自检通常 30-60s，太长影响恢复感知）
- HALF_OPEN 只放过 1 个请求（在 open_until 后由首次调用触发），避免一窝蜂

用法（外部）：
    from fetcher.qmt_breaker import is_open, record_success, record_failure, snapshot

    if is_open():
        # 短路，不发请求
        return fallback
    try:
        result = call_bridge(...)
        record_success()
        return result
    except Exception:
        record_failure()
        raise

snapshot() 给前端做 banner（"QMT 桥离线，重试倒计时 N 秒"）。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict


# ─── 阈值（可按需调） ─────────────────────────────────────────────────────────

FAIL_THRESHOLD = 3      # 连续失败 N 次 → 打开
OPEN_SECS = 60.0        # OPEN 状态持续秒数（到点进入 HALF_OPEN 试探一次）
# 失败"窗口"：超过 WINDOW_SECS 没失败 → 失败计数重置为 0（避免老失败污染）
WINDOW_SECS = 120.0


# ─── 状态机 ─────────────────────────────────────────────────────────────────

STATE_CLOSED = "closed"        # 正常
STATE_OPEN = "open"            # 熔断中（短路所有调用）
STATE_HALF_OPEN = "half_open"  # 试探中（只放行 1 个请求验证）


_state_lock = threading.Lock()
_state: Dict[str, Any] = {
    "state": STATE_CLOSED,
    "fail_count": 0,
    "fail_count_window_start": time.time(),  # 当前失败计数窗口起点
    "first_fail_at": 0.0,                    # 当前连续失败首次时间（用于 "已离线 X 秒"）
    "opened_at": 0.0,                        # 最近一次进入 OPEN 的时间
    "open_until": 0.0,                       # 预计从 OPEN → HALF_OPEN 的时间
    "last_error": "",                        # 最近一次失败的简短原因（前端展示）
    "last_success_at": 0.0,                  # 最近一次成功时间
    "half_open_inflight": False,             # HALF_OPEN 试探中是否已有 in-flight 请求
}


def _now() -> float:
    return time.time()


def record_success() -> None:
    """调用成功。CLOSED 下重置失败计数；HALF_OPEN 下关闭熔断。"""
    with _state_lock:
        _state["fail_count"] = 0
        _state["fail_count_window_start"] = _now()
        _state["last_success_at"] = _now()
        if _state["state"] in (STATE_HALF_OPEN, STATE_OPEN):
            _state["state"] = STATE_CLOSED
            _state["open_until"] = 0.0
            _state["opened_at"] = 0.0
            _state["half_open_inflight"] = False


def record_failure(error: str = "") -> None:
    """调用失败。累计失败次数，达到阈值就开熔断。"""
    with _state_lock:
        now = _now()
        # 失败窗口外：重置计数
        if now - _state["fail_count_window_start"] > WINDOW_SECS:
            _state["fail_count"] = 0
            _state["fail_count_window_start"] = now
        _state["fail_count"] += 1
        if _state["fail_count"] == 1:
            _state["first_fail_at"] = now
        _state["last_error"] = (error or "")[:200]

        if _state["state"] == STATE_HALF_OPEN:
            # 试探失败：重新 OPEN
            _state["state"] = STATE_OPEN
            _state["opened_at"] = now
            _state["open_until"] = now + OPEN_SECS
            _state["half_open_inflight"] = False
            return

        if _state["fail_count"] >= FAIL_THRESHOLD and _state["state"] == STATE_CLOSED:
            _state["state"] = STATE_OPEN
            _state["opened_at"] = now
            _state["open_until"] = now + OPEN_SECS


def is_open() -> bool:
    """是否当前应短路（True = 不要发请求）。HALF_OPEN 已 in-flight 也算 open。"""
    with _state_lock:
        return _should_shortcut_locked(_now())


def _should_shortcut_locked(now: float) -> bool:
    state = _state["state"]
    if state == STATE_CLOSED:
        return False
    if state == STATE_OPEN:
        if now >= _state["open_until"]:
            # 到点：进入 HALF_OPEN，但 in-flight 已有就继续 short
            if not _state["half_open_inflight"]:
                _state["state"] = STATE_HALF_OPEN
                _state["half_open_inflight"] = True
                return False  # 放这一次
            return True
        return True
    if state == STATE_HALF_OPEN:
        # in-flight 中不再放新的
        if _state["half_open_inflight"]:
            return True
        _state["half_open_inflight"] = True
        return False
    return False


def try_acquire() -> bool:
    """调用方入口：返回 True 表示允许发请求，False 表示被熔断短路。

    用法：
        if not try_acquire():
            return None  # 熔断中，不发请求
        try:
            r = requests.post(...)
            record_success()
        except Exception as e:
            record_failure(str(e))
            raise
    """
    with _state_lock:
        return not _should_shortcut_locked(_now())


def snapshot() -> Dict[str, Any]:
    """前端展示用的状态快照。"""
    with _state_lock:
        now = _now()
        st = _state["state"]
        open_until = _state["open_until"]
        opened_at = _state["opened_at"]

        # 离 HALF_OPEN 还有几秒
        retry_in_secs = 0
        if st == STATE_OPEN:
            retry_in_secs = max(0, int(open_until - now))
        elif st == STATE_HALF_OPEN:
            retry_in_secs = 0  # 正在试探

        # 已离线多久（自 first_fail 或 opened_at）
        since = 0
        if st == STATE_OPEN and opened_at:
            since = int(now - opened_at)
        elif st == STATE_HALF_OPEN and opened_at:
            since = int(now - opened_at)

        return {
            "state": st,
            "fail_count": _state["fail_count"],
            "fail_threshold": FAIL_THRESHOLD,
            "retry_in_secs": retry_in_secs,
            "offline_secs": since,
            "last_error": _state["last_error"],
            "last_success_at": _state["last_success_at"],
            "open_until_ts": open_until,
        }


def reset() -> None:
    """手动重置（测试 / 运维用）。"""
    with _state_lock:
        _state["state"] = STATE_CLOSED
        _state["fail_count"] = 0
        _state["fail_count_window_start"] = _now()
        _state["first_fail_at"] = 0.0
        _state["opened_at"] = 0.0
        _state["open_until"] = 0.0
        _state["last_error"] = ""
        _state["half_open_inflight"] = False

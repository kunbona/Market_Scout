"""
QMT Bridge 客户端（Mac 端）。

通过 HTTP 调用运行在 Windows VM 上的 qmt-bridge 服务，间接使用 xtquant。
跟 fetcher.qmt_data_api 配合：qmt_data_api 内部检测 QMT_BRIDGE_URL，
配了就走这个客户端，没配就降级用本地 xtquant（如果装了的话）。

接口：
    _call(method, *args, **kwargs) → Any | None
        通用代理：调白名单内任何 xtdata 方法。
        失败（网络/鉴权/白名单/超时）一律返回 None，让调用方自己降级。

    is_available() → bool
        探测 bridge 是否就绪（不抛异常，懒探测）。

    health() / version() / methods() / connect_test()
        调试用接口，调用方一般不用。

环境变量：
    QMT_BRIDGE_URL     bridge 服务的 base URL（例 http://192.168.1.100:5001）
    QMT_BRIDGE_TOKEN   共享 token，对应 bridge 端 QMT_BRIDGE_TOKEN
    QMT_BRIDGE_TIMEOUT 单次请求超时（秒，默认 30；get_full_tick 类重调用 60）

设计取舍：
- 用 requests（项目已在用 http_util），不引入新依赖
- 失败一律返回 None 而不是抛异常，跟 xtquant 不可用时现有调用方行为一致
- 不缓存连接：bridge VM 可能重启，每次重新建立 + 短超时更安全
- 留 _log_failure 开关：第一次失败后短时间静默，避免日志爆炸
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover - 跟项目其它模块同假设
    requests = None  # type: ignore

try:
    from fetcher import qmt_breaker
except ImportError:  # pragma: no cover
    qmt_breaker = None  # type: ignore

logger = logging.getLogger(__name__)


# ─── 失败日志去重：短时间内同 method 重复失败只打一次 ────────────────────────

_log_lock = threading.Lock()
_log_last: dict[str, float] = {}
_LOG_COOLDOWN = 60.0  # 秒


def _log_failure_once(method: str, exc: Exception) -> None:
    now = time.time()
    with _log_lock:
        last = _log_last.get(method, 0.0)
        if now - last < _LOG_COOLDOWN:
            return
        _log_last[method] = now
    logger.warning("[qmt-client] %s 调用失败（后续同类错误 %ds 内静默）: %s",
                   method, int(_LOG_COOLDOWN), exc)


# ─── 基础探测 ────────────────────────────────────────────────────────────────

def _bridge_url() -> str:
    return os.environ.get("QMT_BRIDGE_URL", "").strip().rstrip("/")


def _bridge_token() -> str:
    return os.environ.get("QMT_BRIDGE_TOKEN", "").strip()


def _timeout_for(method: str) -> float:
    """
    大数据量方法（get_full_tick / get_market_data 全市场）放宽到 60s，
    其余默认 30s。可通过 QMT_BRIDGE_TIMEOUT 整体覆盖。
    """
    base = float(os.environ.get("QMT_BRIDGE_TIMEOUT", "30") or "30")
    heavy = {"get_full_tick", "get_market_data", "get_market_data_ex",
             "download_history_data", "get_quote_history"}
    if method in heavy:
        return max(base, 60.0)
    return base


def is_configured() -> bool:
    """是否配置了 bridge URL（不一定就绪，只是配置存在）。"""
    return bool(_bridge_url() and _bridge_token())


def is_available() -> bool:
    """
    探测 bridge 是否就绪：调 /health，xtquant 也得可用。
    用于开机时 / 设置页显示「已连接 / 未连接」徽章。
    失败不抛异常。
    """
    if not is_configured():
        return False
    if requests is None:
        return False
    try:
        r = requests.get(f"{_bridge_url()}/health", timeout=5)
        if r.status_code != 200:
            return False
        body = r.json()
        return bool(body.get("ok") and body.get("data", {}).get("xtdata_available"))
    except Exception:
        return False


def health() -> dict | None:
    if not is_configured() or requests is None:
        return None
    try:
        r = requests.get(f"{_bridge_url()}/health", timeout=5)
        if r.status_code == 200:
            return r.json().get("data")
    except Exception:
        pass
    return None


def version() -> str | None:
    if not is_configured() or requests is None:
        return None
    try:
        r = requests.get(
            f"{_bridge_url()}/qmt/version",
            headers=_auth_headers(),
            timeout=5,
        )
        if r.status_code == 200:
            data = r.json().get("data", {})
            return data.get("xtquant_version")
    except Exception:
        pass
    return None


def methods() -> list[str] | None:
    if not is_configured() or requests is None:
        return None
    try:
        r = requests.get(
            f"{_bridge_url()}/qmt/methods",
            headers=_auth_headers(),
            timeout=5,
        )
        if r.status_code == 200:
            return r.json().get("data", {}).get("allowed", [])
    except Exception:
        pass
    return None


def connect_test() -> bool:
    """主动尝试连接 miniQMT。返回 True/False。"""
    if not is_configured() or requests is None:
        return False
    try:
        r = requests.post(
            f"{_bridge_url()}/qmt/connect",
            headers=_auth_headers(),
            timeout=15,
        )
        if r.status_code == 200:
            return bool(r.json().get("data", {}).get("connected"))
    except Exception:
        pass
    return False


# ─── 核心：通用代理调用 ──────────────────────────────────────────────────────

def _auth_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_bridge_token()}",
        "Content-Type":  "application/json",
    }


def _call(method: str, *args: Any, **kwargs: Any) -> Any:
    """
    通过 bridge 调 xtdata 上指定方法。

    返回：xtdata 方法的原始返回值（已经被 bridge 端 JSON 化）
          任何失败（未配置 / 网络 / 鉴权 / 白名单 / 超时 / 5xx）→ None

    调用方按本地 xtquant 不可用的方式处理即可，零感知远端/本地差别。

    熔断：连续 3 次失败（默认）→ 短路 60s，避免 VM 离线时持续 30s 重试耗光资源。
    """
    if not is_configured() or requests is None:
        return None
    # 熔断检查：VM 不通时直接返回，不发请求
    if qmt_breaker is not None and not qmt_breaker.try_acquire():
        return None
    url = f"{_bridge_url()}/qmt/call"
    payload = {"method": method, "args": list(args), "kwargs": kwargs}
    try:
        r = requests.post(
            url,
            headers=_auth_headers(),
            json=payload,
            timeout=_timeout_for(method),
        )
    except requests.exceptions.Timeout as exc:
        _log_failure_once(method, exc)
        if qmt_breaker is not None:
            qmt_breaker.record_failure(f"Timeout: {exc}")
        return None
    except requests.exceptions.ConnectionError as exc:
        _log_failure_once(method, exc)
        if qmt_breaker is not None:
            qmt_breaker.record_failure(f"ConnectionError: {exc}")
        return None
    except Exception as exc:
        _log_failure_once(method, exc)
        if qmt_breaker is not None:
            qmt_breaker.record_failure(str(exc))
        return None

    if r.status_code != 200:
        # 401/403/404/500/503 一律返回 None；不打 ERROR（避免每分钟刷屏）
        _log_failure_once(method, Exception(f"HTTP {r.status_code}: {r.text[:120]}"))
        if qmt_breaker is not None:
            qmt_breaker.record_failure(f"HTTP {r.status_code}")
        return None

    try:
        body = r.json()
    except Exception as exc:
        _log_failure_once(method, exc)
        if qmt_breaker is not None:
            qmt_breaker.record_failure(f"JSONDecode: {exc}")
        return None

    if not body.get("ok"):
        # bridge 返回 ok:false 通常是参数错或白名单拒绝——这种是 bug 不是网络问题，记 ERROR
        logger.error("[qmt-client] %s 业务错误：%s", method, body.get("error"))
        # 业务错误不算网络问题，不计入熔断失败计数
        return None

    # 成功：通知熔断器
    if qmt_breaker is not None:
        qmt_breaker.record_success()
    return body.get("data", {}).get("result")

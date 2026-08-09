"""
QMT Bridge —— 把 Windows VM 上的 miniQMT / xtquant 能力通过 HTTP 暴露给 Mac。

设计要点：
- JSON-RPC 风格：所有数据方法都走 POST /qmt/call，body = {method, args, kwargs}
- 方法白名单：allowlist.ALLOWED_METHODS 决定能调啥，加新能力只动白名单
- 鉴权：共享 token（QMT_BRIDGE_TOKEN），HTTP 头 Authorization: Bearer ...
- 错误统一：xtdata 抛异常时返回 {ok: false, error: "..."}，调用方据此降级
- JSON 清洗：xtdata 返回的 date / DataFrame / numpy 类型全部转成原生类型
- 日志：每次 call 记录 method + 耗时 + 是否成功，方便排查

端点：
    GET  /health               健康检查
    GET  /qmt/version          xtquant 版本
    GET  /qmt/methods          列出白名单（调试用）
    POST /qmt/connect          尝试连接 miniQMT（单独走，状态性）
    POST /qmt/call             通用代理（核心端点）
"""
from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from flask import Flask, jsonify, request

from allowlist import ALLOWED_METHODS, is_allowed

# ─── 日志 ─────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [bridge] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("qmt-bridge")

# ─── xtquant 延迟导入（安装了就用，没装也不阻断启动） ────────────────────────

try:
    from xtquant import xtdata
    _XTDATA_AVAILABLE = True
    _XTDATA_IMPORT_ERROR: str | None = None
except Exception as exc:  # pragma: no cover - 运行时依赖
    xtdata = None  # type: ignore
    _XTDATA_AVAILABLE = False
    _XTDATA_IMPORT_ERROR = repr(exc)


# ─── JSON 清洗：xtdata 返回值常常夹带 date / DataFrame / numpy ────────────────

def _sanitize(obj: Any) -> Any:
    """递归把 xtdata 返回值转成 JSON 可序列化对象。"""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {str(k): _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [_sanitize(v) for v in obj]
    # pandas DataFrame / Series / numpy ndarray
    if _hasattr(obj, "to_dict"):
        try:
            return _sanitize(obj.to_dict(orient="records"))
        except Exception:
            pass
    if _hasattr(obj, "tolist"):
        try:
            return _sanitize(obj.tolist())
        except Exception:
            pass
    # numpy 标量
    if _hasattr(obj, "item"):
        try:
            return _sanitize(obj.item())
        except Exception:
            pass
    # numpy 数组
    if _hasattr(obj, "shape") and _hasattr(obj, "dtype"):
        try:
            import numpy as np  # noqa: F401
            return _sanitize(obj.tolist())
        except Exception:
            pass
    # bytes
    if isinstance(obj, (bytes, bytearray)):
        try:
            return obj.decode("utf-8", errors="replace")
        except Exception:
            return repr(obj)
    # 兜底：走 repr
    try:
        return repr(obj)
    except Exception:
        return None


def _hasattr(obj: Any, name: str) -> bool:
    try:
        return hasattr(obj, name)
    except Exception:
        return False


# ─── 鉴权 ─────────────────────────────────────────────────────────────────────

def _check_auth() -> bool:
    expected = os.environ.get("QMT_BRIDGE_TOKEN", "").strip()
    if not expected:
        # 未配置 token → 拒绝所有请求。配 token 是强制项，避免误开端口泄漏数据。
        return False
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return False
    return header[len("Bearer "):].strip() == expected


# ─── 响应辅助 ────────────────────────────────────────────────────────────────

def _ok(data: Any):
    return jsonify({"ok": True, "data": data})


def _err(message: str, code: int = 400, **extra: Any):
    payload: dict[str, Any] = {"ok": False, "error": message}
    payload.update(extra)
    return jsonify(payload), code


# ─── Flask app ────────────────────────────────────────────────────────────────

app = Flask(__name__)


@app.errorhandler(Exception)
def _on_exception(exc: Exception):  # pragma: no cover - 兜底
    logger.exception("[bridge] unhandled exception: %s", exc)
    return _err(f"internal error: {exc}", 500)


@app.get("/health")
def health():
    return _ok({
        "status": "ok",
        "xtdata_available": _XTDATA_AVAILABLE,
        "xtdata_error": _XTDATA_IMPORT_ERROR,
        "timestamp": datetime.now().isoformat(),
    })


@app.get("/qmt/version")
def version():
    if not _XTDATA_AVAILABLE:
        return _err(f"xtquant 未安装：{_XTDATA_IMPORT_ERROR}", 503)
    try:
        import xtquant
        v = getattr(xtquant, "__version__", "已安装（无版本号）")
        return _ok({"xtquant_version": v})
    except Exception as exc:
        return _err(f"读取版本失败：{exc}", 500)


@app.get("/qmt/methods")
def methods():
    if not _check_auth():
        return _err("未授权：请在 Authorization 头提供 Bearer token", 401)
    return _ok({
        "allowed": sorted(ALLOWED_METHODS),
        "count": len(ALLOWED_METHODS),
    })


@app.post("/qmt/connect")
def connect():
    if not _check_auth():
        return _err("未授权：请在 Authorization 头提供 Bearer token", 401)
    if not _XTDATA_AVAILABLE:
        return _err(f"xtquant 未安装：{_XTDATA_IMPORT_ERROR}", 503)
    try:
        xtdata.connect()
        return _ok({"connected": True})
    except Exception as exc:
        logger.warning("[bridge] connect failed: %s", exc)
        return _err(f"connect 失败：{exc}", 500, connected=False)


@app.post("/qmt/call")
def call():
    if not _check_auth():
        return _err("未授权：请在 Authorization 头提供 Bearer token", 401)
    if not _XTDATA_AVAILABLE:
        return _err(f"xtquant 未安装：{_XTDATA_IMPORT_ERROR}", 503)

    body = request.get_json(silent=True) or {}
    method = body.get("method")
    args = body.get("args", [])
    kwargs = body.get("kwargs", {})

    if not isinstance(method, str) or not method:
        return _err("body 缺少 method 字段（字符串）")
    if not isinstance(args, list):
        return _err("body 的 args 必须是 list")
    if not isinstance(kwargs, dict):
        return _err("body 的 kwargs 必须是 dict")

    if not is_allowed(method):
        return _err(
            f"方法「{method}」不在白名单内。可调方法见 GET /qmt/methods",
            403,
        )

    fn = getattr(xtdata, method, None)
    if fn is None or not callable(fn):
        return _err(f"xtdata 上不存在方法：{method}", 404)

    t0 = time.time()
    try:
        result = fn(*args, **kwargs)
    except TypeError as exc:
        # 参数不匹配是常见的客户端 bug，不要 5xx
        logger.warning("[bridge] %s TypeError: %s", method, exc)
        return _err(f"参数错误：{exc}", 400)
    except Exception as exc:
        logger.exception("[bridge] %s failed: %s", method, exc)
        return _err(f"xtdata.{method} 失败：{exc}", 500)
    elapsed_ms = int((time.time() - t0) * 1000)

    sanitized = _sanitize(result)
    logger.info(
        "[bridge] %s ok in %dms (args=%d, kwargs=%d, result_size=%s)",
        method, elapsed_ms, len(args), len(kwargs),
        _size_hint(sanitized),
    )
    return _ok({
        "result": sanitized,
        "elapsed_ms": elapsed_ms,
        "method": method,
    })


def _size_hint(obj: Any) -> str:
    """粗略描述结果大小，用于日志。"""
    try:
        if isinstance(obj, dict):
            return f"dict({len(obj)})"
        if isinstance(obj, list):
            return f"list({len(obj)})"
        if isinstance(obj, str):
            return f"str({len(obj)})"
    except Exception:
        pass
    return type(obj).__name__


# ─── 入口 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="QMT Bridge —— xtquant HTTP 代理")
    parser.add_argument("--host", default=os.environ.get("BRIDGE_HOST", "0.0.0.0"),
                        help="监听地址（默认 0.0.0.0；只本机访问用 127.0.0.1）")
    parser.add_argument("--port", type=int, default=int(os.environ.get("BRIDGE_PORT", "5001")),
                        help="监听端口（默认 5001）")
    args = parser.parse_args()

    if not os.environ.get("QMT_BRIDGE_TOKEN", "").strip():
        logger.warning("⚠ QMT_BRIDGE_TOKEN 未设置，所有请求将被拒绝。建议先 set QMT_BRIDGE_TOKEN=<随机长串> 再启动。")

    if not _XTDATA_AVAILABLE:
        logger.warning("⚠ xtquant 导入失败：%s", _XTDATA_IMPORT_ERROR)
        logger.warning("  bridge 仍会启动（健康检查可通），但 /qmt/call 和 /qmt/connect 会返回 503。")

    logger.info("启动 QMT Bridge：%s:%d", args.host, args.port)
    logger.info("白名单方法数：%d", len(ALLOWED_METHODS))
    # 用 waitress 跟主项目保持一致（生产可用）；没装就退化到 Flask 自带
    try:
        from waitress import serve
        serve(app, host=args.host, port=args.port)
    except ImportError:
        logger.warning("waitress 未安装，退化到 Flask 开发服务器（仅本地调试用）")
        app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()

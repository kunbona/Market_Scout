"""api/common.py — Blueprint 共享响应层 (从 server.py Helpers 段抽出)。

所有域 Blueprint 统一从这里导入 _ok/_err/_date_param/_today,
server.py 保留同名函数供未拆分的域继续用 (后续域全部拆完后可收敛到一份)。
"""
from datetime import datetime

from flask import jsonify, request


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _ok(data):
    return jsonify({"success": True, "data": data, "ts": datetime.now().isoformat()})


def _err(msg, code: int = 500):
    return jsonify({"success": False, "error": str(msg)}), code


def _date_param(key: str = "date") -> str:
    """Return the query-string date param, falling back to today."""
    val = request.args.get(key, "").strip()
    return val if val else _today()


def _date_or_none(key: str = "date"):
    """Return date param if provided, else None (let storage pick latest)."""
    val = request.args.get(key, "").strip()
    return val if val else None


def _computed_date(key: str = "date") -> str:
    """用于历史计算型接口：有参数用参数，无参数回落到最新已计算日期。"""
    val = request.args.get(key, "").strip()
    if val:
        return val
    from db.storage import get_latest_emotion_date
    return get_latest_emotion_date() or _today()

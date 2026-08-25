"""api/config.py — 运行时配置域 (自 server.py 拆出)。

6 路由: GET/POST /api/config (读/写运行时配置并持久化 .env.local) +
test-data-root / test-rsshub / test-qmt / test-qmt-bridge 四个探测端点。

_save_env_local 整体搬入 (仅 config POST 使用); _read_qmt_runtime_status
与 server.py QMT 枢纽共享, 函数体内懒导入自 server。

url_prefix="/api/config"。
"""
import logging
import os

from flask import Blueprint, request

from api.common import _err, _ok

logger = logging.getLogger(__name__)

bp = Blueprint("config", __name__, url_prefix="/api/config")

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _save_env_local(updates: dict) -> None:
    """将 key=value 写入 .env.local，已有的 key 更新，不存在的追加。"""
    env_path = os.path.join(_PROJECT_ROOT, ".env.local")
    lines = []
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

    written = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue
        key = stripped.partition("=")[0].strip()
        if key in updates:
            new_lines.append(f"{key}={updates[key]}\n")
            written.add(key)
        else:
            new_lines.append(line)

    # 追加未出现过的 key
    for key, val in updates.items():
        if key not in written:
            new_lines.append(f"{key}={val}\n")

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)


@bp.route("", methods=["GET"])
def api_config_get():
    """返回当前运行时配置值。"""
    import quant.loader as loader
    from core.qmt_hub import _read_qmt_runtime_status
    rsshub_global = os.environ.get("RSSHUB_BASE_URL", "")
    data_root = str(loader.DATA_ROOT) if loader.DATA_ROOT else ""
    flask_port = os.environ.get("FLASK_PORT", "20026")
    quant_workers = os.environ.get("QUANT_WORKERS", "")
    agent_enabled = os.environ.get("AGENT_ENABLED", "true")
    compute_enabled = os.environ.get("COMPUTE_ENABLED", "true")
    qmt_path = os.environ.get("QMT_PATH", "")
    qmt_bridge_url = os.environ.get("QMT_BRIDGE_URL", "")
    qmt_bridge_token = os.environ.get("QMT_BRIDGE_TOKEN", "")
    qmt_status = _read_qmt_runtime_status()
    return _ok({
        "data_root": data_root,
        "rsshub_url": rsshub_global,
        "flask_port": flask_port,
        "quant_workers": quant_workers,
        "agent_enabled": agent_enabled,
        "compute_enabled": compute_enabled,
        "qmt_enabled": "true" if qmt_status["enabled"] else "false",
        "qmt_path": qmt_path,
        "qmt_bridge_url": qmt_bridge_url,
        "qmt_bridge_token": qmt_bridge_token,
        "qmt_connected": qmt_status["connected"],
        "qmt_version": qmt_status["version"],
    })


@bp.route("", methods=["POST"])
def api_config_set():
    """更新运行时配置，并持久化到 .env.local。"""
    import quant.loader as loader
    from pathlib import Path
    body = request.get_json(silent=True) or {}
    changed = []
    env_updates = {}

    if "data_root" in body:
        new_path = body["data_root"].strip()
        if new_path and Path(new_path).exists():
            loader.DATA_ROOT = Path(new_path)
            os.environ["QUANT_DATA_ROOT"] = new_path
            env_updates["QUANT_DATA_ROOT"] = new_path
            changed.append(f"QUANT_DATA_ROOT → {new_path}")
        elif new_path:
            return _err(f"路径不存在: {new_path}", 400)
        else:
            # 清空路径
            loader.DATA_ROOT = None
            os.environ.pop("QUANT_DATA_ROOT", None)
            env_updates["QUANT_DATA_ROOT"] = ""
            changed.append("QUANT_DATA_ROOT 已清空")

    if "rsshub_url" in body:
        new_url = body["rsshub_url"].strip().rstrip("/")
        if new_url:
            os.environ["RSSHUB_BASE_URL"] = new_url
            env_updates["RSSHUB_BASE_URL"] = new_url
            import sys
            for mod_name in ("fetcher.global_news", "fetcher.policy_rss"):
                mod = sys.modules.get(mod_name)
                if mod:
                    mod.RSSHUB = new_url
            changed.append(f"RSSHUB_BASE_URL → {new_url}")

    if "flask_port" in body:
        new_port = body["flask_port"].strip()
        if new_port.isdigit() and 1024 <= int(new_port) <= 65535:
            env_updates["FLASK_PORT"] = new_port
            changed.append(f"FLASK_PORT → {new_port}（重启后生效）")
        elif new_port:
            return _err(f"端口无效: {new_port}，需为 1024-65535 之间的数字", 400)

    if "quant_workers" in body:
        val = body["quant_workers"].strip()
        if val == "":
            os.environ.pop("QUANT_WORKERS", None)
            env_updates["QUANT_WORKERS"] = ""
            changed.append("QUANT_WORKERS 已清空（自动检测并发数）")
        elif val == "0" or val == "1":
            # 0 或 1 均表示串行模式（单线程/单进程）
            os.environ["QUANT_WORKERS"] = val
            env_updates["QUANT_WORKERS"] = val
            changed.append(f"QUANT_WORKERS → {val}（串行模式，立即生效）")
        elif val.isdigit() and 2 <= int(val) <= 64:
            os.environ["QUANT_WORKERS"] = val
            env_updates["QUANT_WORKERS"] = val
            changed.append(f"QUANT_WORKERS → {val}（立即生效）")
        else:
            return _err(f"并发数无效: {val}，需为 0-64 之间的整数（0 或 1 表示串行）", 400)

    if "agent_enabled" in body:
        raw = body["agent_enabled"]
        if isinstance(raw, bool):
            val = "true" if raw else "false"
        else:
            val = "true" if str(raw).strip().lower() in ("true", "1", "yes") else "false"
        os.environ["AGENT_ENABLED"] = val
        env_updates["AGENT_ENABLED"] = val
        changed.append(f"AGENT_ENABLED → {val}（立即生效）")

    if "compute_enabled" in body:
        raw = body["compute_enabled"]
        if isinstance(raw, bool):
            val = "true" if raw else "false"
        else:
            val = "true" if str(raw).strip().lower() in ("true", "1", "yes") else "false"
        os.environ["COMPUTE_ENABLED"] = val
        env_updates["COMPUTE_ENABLED"] = val
        changed.append(f"COMPUTE_ENABLED → {val}（重启后生效）")

    if "qmt_enabled" in body:
        raw = body["qmt_enabled"]
        if isinstance(raw, bool):
            val = "true" if raw else "false"
        else:
            val = "true" if str(raw).strip().lower() in ("true", "1", "yes") else "false"
        os.environ["QMT_ENABLED"] = val
        # 同步到 xtquant_breadth 模块（如果已加载）
        import sys
        mod = sys.modules.get("fetcher.xtquant_breadth")
        if mod:
            pass  # xtquant_breadth 每次调用时读 os.environ，无需额外同步
        env_updates["QMT_ENABLED"] = val
        changed.append(f"QMT_ENABLED → {val}（立即生效）")

    if "qmt_path" in body:
        new_path = body["qmt_path"].strip()
        os.environ["QMT_PATH"] = new_path
        env_updates["QMT_PATH"] = new_path
        if new_path:
            changed.append(f"QMT_PATH → {new_path}")
        else:
            changed.append("QMT_PATH 已清空")

    if "qmt_bridge_url" in body:
        new_url = body["qmt_bridge_url"].strip().rstrip("/")
        if new_url:
            # 简单格式校验：必须以 http:// 或 https:// 开头
            if not (new_url.startswith("http://") or new_url.startswith("https://")):
                return _err(f"bridge URL 必须以 http:// 或 https:// 开头：{new_url}", 400)
            os.environ["QMT_BRIDGE_URL"] = new_url
            env_updates["QMT_BRIDGE_URL"] = new_url
            changed.append(f"QMT_BRIDGE_URL → {new_url}（立即生效）")
        else:
            os.environ.pop("QMT_BRIDGE_URL", None)
            env_updates["QMT_BRIDGE_URL"] = ""
            changed.append("QMT_BRIDGE_URL 已清空（QMT 数据源回退到本地 xtquant）")

    if "qmt_bridge_token" in body:
        new_token = body["qmt_bridge_token"].strip()
        if new_token:
            os.environ["QMT_BRIDGE_TOKEN"] = new_token
            env_updates["QMT_BRIDGE_TOKEN"] = new_token
            changed.append("QMT_BRIDGE_TOKEN → ******（已更新，立即生效）")
        else:
            os.environ.pop("QMT_BRIDGE_TOKEN", None)
            env_updates["QMT_BRIDGE_TOKEN"] = ""
            changed.append("QMT_BRIDGE_TOKEN 已清空")

    if env_updates:
        try:
            _save_env_local(env_updates)
        except Exception as e:
            logger.warning("写入 .env.local 失败: %s", e)

    return _ok({"changed": changed})


@bp.route("/test-data-root")
def api_test_data_root():
    """检查 DATA_ROOT 路径是否存在且包含必要的 parquet 文件。"""
    import quant.loader as loader
    from pathlib import Path
    path_str = request.args.get("path", "").strip()
    check_path = Path(path_str) if path_str else loader.DATA_ROOT
    if not check_path:
        return _ok({"ok": False, "reason": "未配置路径"})
    if not check_path.exists():
        return _ok({"ok": False, "reason": f"路径不存在: {check_path}"})
    # 检查关键文件
    key_file = check_path / "factors" / "stock" / "daily" / "涨停相关因子.parquet"
    if not key_file.exists():
        return _ok({"ok": False, "reason": f"未找到涨停因子文件，请确认路径正确"})
    return _ok({"ok": True, "reason": f"路径有效: {check_path}"})


@bp.route("/test-rsshub")
def api_test_rsshub():
    """检查 RSSHub 服务是否可达。"""
    import requests as _req
    url_param = request.args.get("url", "").strip().rstrip("/")
    test_url = url_param or os.environ.get("RSSHUB_BASE_URL", "")
    if not test_url:
        return _ok({"ok": False, "reason": "未配置 RSSHub 地址"})
    try:
        r = _req.get(f"{test_url}/", timeout=4)
        if r.status_code < 500:
            return _ok({"ok": True, "reason": f"连通（HTTP {r.status_code}）"})
        return _ok({"ok": False, "reason": f"服务异常（HTTP {r.status_code}）"})
    except _req.exceptions.ConnectionError:
        return _ok({"ok": False, "reason": "连接被拒绝，请确认 RSSHub 已启动"})
    except _req.exceptions.Timeout:
        return _ok({"ok": False, "reason": "连接超时（>4s）"})
    except Exception as e:
        return _ok({"ok": False, "reason": str(e)})


@bp.route("/test-qmt")
def api_test_qmt():
    """检测 miniQMT 是否可达，返回连接状态和 xtquant 版本。"""
    from pathlib import Path

    # 检查 xtquant 是否安装
    try:
        from fetcher.xtquant_breadth import connect as _qmt_connect, get_version as _qmt_ver
    except Exception as e:
        return _ok({"ok": False, "reason": f"xtquant 模块加载失败: {e}", "version": None})

    version = _qmt_ver()
    if version is None:
        return _ok({"ok": False, "reason": "xtquant 未安装（pip install xtquant）", "version": None})

    # 检查 QMT 路径（可选验证）
    qmt_path = request.args.get("path", "").strip() or os.environ.get("QMT_PATH", "")
    if qmt_path:
        p = Path(qmt_path)
        if not p.exists():
            return _ok({"ok": False, "reason": f"QMT 路径不存在: {qmt_path}", "version": version})

    # 尝试连接
    # 临时强制 QMT_ENABLED=true 以便 connect() 不因开关而短路
    _orig = os.environ.get("QMT_ENABLED", "false")
    os.environ["QMT_ENABLED"] = "true"
    try:
        connected = _qmt_connect()
    finally:
        os.environ["QMT_ENABLED"] = _orig

    if connected:
        return _ok({"ok": True, "reason": f"miniQMT 连接成功（xtquant {version}）", "version": version})
    else:
        return _ok({"ok": False, "reason": "miniQMT 连接失败，请确认客户端已启动并登录", "version": version})


@bp.route("/test-qmt-bridge")
def api_test_qmt_bridge():
    """
    探测 QMT bridge（远端 VM）是否可达。
    优先用 URL 查询参数里给的值（未保存也能测），否则读环境变量。
    """
    from fetcher import qmt_client

    url = request.args.get("url", "").strip() or os.environ.get("QMT_BRIDGE_URL", "")
    token = request.args.get("token", "").strip() or os.environ.get("QMT_BRIDGE_TOKEN", "")

    if not url or not token:
        return _ok({
            "ok": False,
            "reason": "未配置 bridge URL 或 token",
            "xtquant_version": None,
        })

    # 临时覆盖模块的 env 探测（用完恢复）
    import fetcher.qmt_client as _qc
    orig_url = os.environ.get("QMT_BRIDGE_URL", "")
    orig_token = os.environ.get("QMT_BRIDGE_TOKEN", "")
    os.environ["QMT_BRIDGE_URL"] = url
    os.environ["QMT_BRIDGE_TOKEN"] = token
    try:
        # 1) /health 检查网络 + xtquant 可用性
        health = _qc.health()
        if health is None:
            return _ok({
                "ok": False,
                "reason": f"无法连接 {url}（网络不通 / 服务未启动 / 防火墙挡）",
                "xtquant_version": None,
            })
        if not health.get("xtdata_available"):
            err = health.get("xtdata_error", "xtquant 未就绪")
            return _ok({
                "ok": False,
                "reason": f"bridge 在线但 xtquant 不可用：{err}",
                "xtquant_version": None,
            })
        # 2) /qmt/version 拿版本号
        v = _qc.version() or "已安装（无版本号）"
        # 3) 探测一个白名单方法（get_trading_dates 轻量）
        result = _qc._call("get_trading_dates", "SH", "20240101", "20240131")
        sample_ok = isinstance(result, list)
        return _ok({
            "ok": True,
            "reason": f"bridge 连接成功（xtquant {v}）" + ("" if sample_ok else "；样例调用未返回 list，请查日志"),
            "xtquant_version": v,
            "sample_call_ok": sample_ok,
        })
    except Exception as e:
        return _ok({
            "ok": False,
            "reason": f"探测异常：{e}",
            "xtquant_version": None,
        })
    finally:
        # 恢复
        if orig_url:
            os.environ["QMT_BRIDGE_URL"] = orig_url
        else:
            os.environ.pop("QMT_BRIDGE_URL", None)
        if orig_token:
            os.environ["QMT_BRIDGE_TOKEN"] = orig_token
        else:
            os.environ.pop("QMT_BRIDGE_TOKEN", None)

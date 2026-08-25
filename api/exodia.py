"""api/exodia.py — Exodia 数据中心域 Blueprint (自 server.py 拆出)。

Vesta 外置盘 AGdata_exodia 日线增量更新: 产品状态查询 (/api/exodia-status)、
手动触发更新 (/api/exodia/run)、日志尾部轮询 (/api/exodia/log)。
对外入口 exodia_update_all_and_wait 供 core/scheduler.py 复盘定时链用
(数据落盘后再算复盘, 避免复盘拿到旧数据)。

注意: /api/exodia-status 是顶层路径 (不在 /api/exodia/ 下), 故 url_prefix="/api"。
"""
import logging
import os
import time

from flask import Blueprint, request

from api.common import _err, _ok

logger = logging.getLogger(__name__)

bp = Blueprint("exodia", __name__, url_prefix="/api")

EXODIA_DATA_DIR = os.environ.get("EXODIA_DATA_DIR", "/Volumes/Vesta/AGdata_exodia")
EXODIA_CODE_DIR = os.path.join(EXODIA_DATA_DIR, "code")
EXODIA_DATA_SUBDIR = os.path.join(EXODIA_CODE_DIR, "data")
EXODIA_BIN = os.path.join(EXODIA_CODE_DIR, "exodia")
EXODIA_LOG_DIR = os.path.join(EXODIA_DATA_DIR, "logs")

# exodia 支持的命令白名单：key = 子命令，needs_product = 是否必须带产品名
# （all_data 即"增量更新全部"，每天跑它自动只拉新增/变更数据）
EXODIA_CMDS = {
    "all_data":       {"needs_product": False, "label": "增量更新全部"},
    "one_data":       {"needs_product": True,  "label": "单个产品增量更新"},
    "full_data":      {"needs_product": True,  "label": "全量恢复(从ZIP)"},
    "init":           {"needs_product": False, "label": "同步产品元数据"},
    "min_data":       {"needs_product": False, "label": "分钟线(5m)"},
    "min_data_fuzzy": {"needs_product": False, "label": "分钟线(tick)"},
}


def _exodia_running_cmd() -> str | None:
    """exodia 正在运行的子命令名（如 all_data / one_data），没在跑返回 None。

    用 pgrep 查二进制路径拿 pid，再 ps 解析命令行里的子命令，
    跨 server 重启仍有效。"""
    try:
        import re
        import subprocess as _sp
        r = _sp.run(["pgrep", "-f", re.escape(EXODIA_BIN)],
                    capture_output=True, text=True, timeout=3)
        for pid in r.stdout.split():
            if not pid or pid == str(os.getpid()):
                continue
            ps = _sp.run(["ps", "-o", "command=", "-p", pid],
                         capture_output=True, text=True, timeout=3)
            m = re.search(r"/exodia\s+(\S+)", ps.stdout.strip())
            if m:
                return m.group(1)
        return None
    except Exception:
        return None


def _exodia_managed_products() -> list[str]:
    """当前已管理（白名单）产品名列表，用于校验 one_data / full_data 参数。"""
    import json as _json
    try:
        with open(os.path.join(EXODIA_DATA_SUBDIR, "products-status.json"),
                  "r", encoding="utf-8") as f:
            return sorted(_json.load(f).keys())
    except Exception:
        return []


@bp.route("/exodia-status")
def api_exodia_status():
    """返回 Exodia 数据中心各产品的更新状态（读 products-status.json + update_success.json）。"""
    import json as _json
    from datetime import date, datetime as _dt

    status_path = os.path.join(EXODIA_DATA_SUBDIR, "products-status.json")
    success_path = os.path.join(EXODIA_DATA_SUBDIR, "update_success.json")

    def _read(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return _json.load(f)
        except Exception:
            return {}

    status = _read(status_path) or {}
    success = _read(success_path) or {}
    success_products = set(success.get("products") or [])
    today = date.today()

    products = []
    for name, p in status.items():
        content_time = p.get("dataContentTime")
        lag_days = None
        if content_time:
            try:
                lag_days = (today - date.fromisoformat(str(content_time))).days
            except ValueError:
                lag_days = None
        products.append({
            "name": name,
            "displayName": p.get("displayName") or name,
            "dataContentTime": content_time,
            "dataTime": p.get("dataTime"),
            "lastUpdateTime": p.get("lastUpdateTime"),
            "nextUpdateTime": p.get("nextUpdateTime"),
            "lastErrTime": p.get("lastErrTime"),
            "lag_days": lag_days,
            "success": name in success_products,
            "has_error": bool(p.get("lastErrTime")),
        })
    # 错误优先 → 数据滞后大优先 → 名字
    products.sort(key=lambda x: (
        0 if x["has_error"] else 1,
        -(x["lag_days"] if x["lag_days"] is not None else 0),
        x["name"],
    ))

    status_mtime = None
    try:
        status_mtime = _dt.fromtimestamp(os.path.getmtime(status_path)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass

    running_cmd = _exodia_running_cmd()
    return _ok({
        "base_dir": EXODIA_DATA_DIR,
        "status_file_mtime": status_mtime,
        "update_success": success,
        "running": bool(running_cmd),
        "running_cmd": running_cmd,
        "products": products,
        "summary": {
            "total": len(products),
            "success": sum(1 for x in products if x["success"]),
            "error": sum(1 for x in products if x["has_error"]),
            "stale": sum(1 for x in products if x["lag_days"] is not None and x["lag_days"] > 3),
            "latest_data_date": max((x["dataContentTime"] for x in products if x["dataContentTime"]), default=None),
        },
    })


def exodia_update_all_and_wait(timeout_sec: int = 1500) -> str:
    """触发 exodia all_data (增量更新全部) 并阻塞等待完成。

    给复盘定时任务链用: 数据落盘后再算复盘, 避免复盘拿到旧数据。
    返回: "ok" (更新完成) | "timeout" (等超时, 仍建议继续) | "no_bin"
    已有更新在跑时不重复启动, 等它跑完视同 ok。
    """
    import subprocess as _sp

    if not os.path.exists(EXODIA_BIN):
        logger.warning("[exodia-chain] 二进制不存在: %s", EXODIA_BIN)
        return "no_bin"

    already = _exodia_running_cmd()
    if not already:
        os.makedirs(EXODIA_LOG_DIR, exist_ok=True)
        log_path = os.path.join(EXODIA_LOG_DIR, "scheduled-all_data.log")
        try:
            with open(log_path, "ab") as logf:
                _sp.Popen(
                    [EXODIA_BIN, "all_data"],
                    cwd=EXODIA_CODE_DIR,
                    stdout=logf, stderr=_sp.STDOUT,
                    start_new_session=True,
                )
            logger.info("[exodia-chain] all_data 已启动, 日志: %s", log_path)
        except Exception as exc:
            logger.warning("[exodia-chain] all_data 启动失败: %s", exc)
            return "no_bin"
    else:
        logger.info("[exodia-chain] 已有 exodia 更新在跑 (%s), 直接等它完成", already)

    # 等待完成 (先睡 5s 让进程注册到 pgrep 可见)
    deadline = time.time() + timeout_sec
    time.sleep(5)
    while time.time() < deadline:
        if not _exodia_running_cmd():
            logger.info("[exodia-chain] all_data 完成, 用时约 %.0fs, 清空 loader 窗口缓存",
                        timeout_sec - (deadline - time.time()))
            try:
                from quant.loader import clear_loader_cache
                clear_loader_cache()
            except Exception as exc:
                logger.warning("[exodia-chain] 清 loader 缓存失败(忽略): %s", exc)
            return "ok"
        time.sleep(15)
    logger.warning("[exodia-chain] all_data 等待超时 (%ds), 继续后续任务", timeout_sec)
    return "timeout"


@bp.route("/exodia/run", methods=["POST"])
def api_exodia_run():
    """手动触发一次 exodia 更新（后台执行，日志落盘）。

    body: {"cmd": "all_data"|"one_data"|"full_data"|"init"|"min_data"|"min_data_fuzzy",
           "product": "stock-xxx-daily"}   # product 仅 one_data / full_data 需要
    """
    import subprocess as _sp

    body = request.get_json(silent=True) or {}
    cmd = body.get("cmd", "all_data")
    product = body.get("product") or ""
    spec = EXODIA_CMDS.get(cmd)
    if spec is None:
        return _err(f"未知命令: {cmd}（支持: {', '.join(EXODIA_CMDS)}）", 400)
    if spec["needs_product"]:
        if not product:
            return _err(f"命令 {cmd} 需要指定产品名", 400)
        if product not in _exodia_managed_products():
            return _err(f"产品 {product} 不在已管理列表", 400)

    if _exodia_running_cmd():
        return _err("已有 exodia 更新进程在运行", 409)
    if not os.path.exists(EXODIA_BIN):
        return _err(f"exodia 二进制不存在: {EXODIA_BIN}", 500)
    os.makedirs(EXODIA_LOG_DIR, exist_ok=True)

    argv = [EXODIA_BIN, cmd] + ([product] if spec["needs_product"] else [])
    log_path = os.path.join(EXODIA_LOG_DIR, f"manual-{cmd}.log")
    try:
        with open(log_path, "ab") as logf:
            _sp.Popen(
                argv,
                cwd=EXODIA_CODE_DIR,
                stdout=logf,
                stderr=_sp.STDOUT,
                start_new_session=True,
            )
    except Exception as exc:
        return _err(f"启动失败: {exc}", 500)
    return _ok({"started": True, "cmd": cmd, "product": product, "log": log_path})


@bp.route("/exodia/log")
def api_exodia_log():
    """读取某命令日志文件的尾部（终端窗口轮询用）。

    query: cmd=all_data&n=200   # n 最大 500
    """
    import os as _os
    from datetime import datetime as _dt

    cmd = request.args.get("cmd", "all_data")
    try:
        n = min(max(int(request.args.get("n", "200")), 1), 500)
    except (TypeError, ValueError):
        n = 200
    if cmd not in EXODIA_CMDS:
        return _err(f"未知命令: {cmd}（支持: {', '.join(EXODIA_CMDS)}）", 400)

    log_path = _os.path.join(EXODIA_LOG_DIR, f"manual-{cmd}.log")
    lines: list[str] = []
    size = 0
    mtime = None
    if _os.path.exists(log_path):
        size = _os.path.getsize(log_path)
        try:
            mtime = _dt.fromtimestamp(_os.path.getmtime(log_path)).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                # 从尾部回读至多 64KB，再取最后 n 行（避免超大文件整读）
                f.seek(0, _os.SEEK_END)
                end = f.tell()
                f.seek(max(0, end - 64 * 1024))
                lines = f.read().splitlines()[-n:]
        except Exception:
            lines = []

    return _ok({
        "cmd": cmd,
        "log_path": log_path,
        "exists": _os.path.exists(log_path),
        "size": size,
        "mtime": mtime,
        "running": cmd == _exodia_running_cmd(),
        "lines": lines,
    })

"""
Agent 编排层（薄封装）。

真正的分析由 Claude CLI subprocess 完成：
  claude -p "/market-radar-analysis --run-type <run_type>"

本模块只负责：
1. 维护运行状态（供 /api/agent/status 查询）
2. 提供 run_agent_analysis() 供 server.py trigger 端点调用
"""
import logging
import os
import shutil
import subprocess
import threading
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJ_ROOT = Path(__file__).resolve().parent.parent

_agent_state = {
    "running": False,
    "last_run": None,
    "last_run_type": None,
    "last_error": None,
    "pid": None,
}
_state_lock = threading.Lock()


def get_agent_state() -> dict:
    with _state_lock:
        return dict(_agent_state)


def _find_claude() -> str:
    custom = os.environ.get("CLAUDE_BIN", "")
    if custom and Path(custom).is_file():
        return custom
    found = shutil.which("claude")
    if found:
        return found
    fallback = Path.home() / ".nvm/versions/node/v20.20.2/bin/claude"
    return str(fallback)


def run_agent_analysis(run_type: str) -> dict:
    """
    启动 Claude CLI subprocess（非阻塞），立即返回 {"status": "started", "pid": ...}。
    状态更新由后台监控线程完成，前端轮询 /api/agent/status 或 /api/agent/latest 感知结果。
    """
    with _state_lock:
        if _agent_state["running"]:
            return {"status": "already_running", "run_type": _agent_state["last_run_type"]}
        _agent_state["running"] = True
        _agent_state["last_run_type"] = run_type
        _agent_state["last_error"] = None
        _agent_state["pid"] = None

    claude_bin = _find_claude()
    try:
        proc = subprocess.Popen(
            [claude_bin, "-p",
             f"/market-radar-analysis --run-type {run_type}",
             "--verbose",
             "--output-format", "stream-json",
             "--dangerously-skip-permissions"],
            cwd=str(_PROJ_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except Exception as exc:
        with _state_lock:
            _agent_state["running"] = False
            _agent_state["last_error"] = str(exc)
        raise

    with _state_lock:
        _agent_state["pid"] = proc.pid

    # 后台线程等待进程结束，更新状态
    def _monitor():
        try:
            _, stderr = proc.communicate(timeout=2700)
            rc = proc.returncode
            with _state_lock:
                if rc != 0:
                    err = (stderr or b"").decode(errors="replace")[:500]
                    logger.error("Agent CLI failed run_type=%s rc=%d: %s", run_type, rc, err)
                    _agent_state["last_error"] = err
                else:
                    _agent_state["last_run"] = datetime.now().strftime("%H:%M:%S")
                    _agent_state["last_error"] = None
        except subprocess.TimeoutExpired:
            proc.kill()
            with _state_lock:
                _agent_state["last_error"] = f"Agent timed out after 2700s (run_type={run_type})"
            logger.error(_agent_state["last_error"])
        except Exception as exc:
            with _state_lock:
                _agent_state["last_error"] = str(exc)
            logger.exception("Agent monitor thread failed: run_type=%s", run_type)
        finally:
            with _state_lock:
                _agent_state["running"] = False
                _agent_state["pid"] = None

    threading.Thread(target=_monitor, daemon=True, name=f"agent-monitor-{run_type}").start()

    return {"status": "started", "run_type": run_type, "pid": proc.pid}

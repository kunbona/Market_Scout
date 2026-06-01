"""
多 Agent 编排层（三阶段）。

第一阶段（并行）：6个分析师各自读数据，写中间结果到 /tmp/mra-{run_id}/
  mra-emotion / mra-sector / mra-news / mra-lhb / mra-momentum / mra-risk

第二阶段（串行）：多空辩论
  mra-bull → mra-bear（各自独立读原始分析结果）

第三阶段：首席裁决
  mra-chief → 读全部结果 → 调用 write_result 落库

前端轮询 /api/agent/status 感知进度，/api/agent/latest 获取最终结果。
"""
import logging
import os
import signal
import shutil
import subprocess
import threading
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJ_ROOT = Path(__file__).resolve().parent.parent

_agent_state = {
    "running": False,
    "phase": None,          # "analysts" | "debate" | "chief" | None
    "phase_detail": None,   # 当前子阶段描述
    "last_run": None,
    "last_run_type": None,
    "last_error": None,
    "pids": set(),
}
_state_lock = threading.Lock()
_stop_requested = False


def get_agent_state() -> dict:
    with _state_lock:
        state = dict(_agent_state)
        state["pids"] = list(_agent_state.get("pids", set()))
        state["pid"] = state["pids"]   # 兼容前端原有 "pid" 字段，值改为 list
        state["stop_requested"] = _stop_requested
        return state


def stop_agent_analysis() -> None:
    """请求停止当前正在运行的 Agent 分析。"""
    global _stop_requested
    with _state_lock:
        _stop_requested = True
        pids = set(_agent_state.get("pids", set()))
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def _find_claude() -> str:
    custom = os.environ.get("CLAUDE_BIN", "")
    if custom and Path(custom).is_file():
        return custom
    found = shutil.which("claude")
    if found:
        return found
    fallback = Path.home() / ".nvm/versions/node/v20.20.2/bin/claude"
    return str(fallback)


def _run_skill(skill_name: str, run_id: str, run_type: str, timeout: int = 600) -> bool:
    """
    同步运行一个 claude skill，返回是否成功。
    调用者负责在后台线程里执行，不要在主线程调用。
    """
    claude_bin = _find_claude()
    env = os.environ.copy()
    env["MRA_RUN_ID"] = run_id
    env["MRA_RUN_TYPE"] = run_type

    proc = None
    try:
        proc = subprocess.Popen(
            [claude_bin, "-p", f"/{skill_name}",
             "--verbose",
             "--output-format", "stream-json",
             "--dangerously-skip-permissions"],
            cwd=str(_PROJ_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=env,
        )
        # Bug2 修复：Popen 返回后立刻加锁，若 stop 已被请求则直接 kill 新进程
        with _state_lock:
            if _stop_requested:
                proc.kill()
                proc.communicate()
                return False
            _agent_state["pids"].add(proc.pid)

        _, stderr = proc.communicate(timeout=timeout)
        rc = proc.returncode

        # 检查 stop flag，若已请求停止则直接中断管道
        with _state_lock:
            if _stop_requested:
                logger.info("[orchestrator] stop requested, aborting after %s", skill_name)
                return False

        if rc != 0:
            err = (stderr or b"").decode(errors="replace")[:300]
            logger.warning("[orchestrator] %s failed rc=%d: %s", skill_name, rc, err)
            return False
        return True
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()   # Bug3 修复：drain pipe，防止下一个 Popen 阻塞
        logger.error("[orchestrator] %s timed out after %ds", skill_name, timeout)
        return False
    except Exception as exc:
        logger.exception("[orchestrator] %s exception: %s", skill_name, exc)
        return False
    finally:
        if proc is not None:
            with _state_lock:
                _agent_state["pids"].discard(proc.pid)


def _write_data_health(run_id: str) -> None:
    """
    在管道启动时生成 data_health.json 并写入 run 目录，供所有 skill 共享。
    即使失败也不阻断管道（静默忽略异常）。
    """
    import subprocess as _sp
    tmp_dir = Path(f"/tmp/mra-{run_id}")
    out_path = tmp_dir / "data_health.json"
    try:
        result = _sp.run(
            ["python", "agent/query.py", "data_health"],
            cwd=str(_PROJ_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            out_path.write_text(result.stdout.strip(), encoding="utf-8")
            logger.info("[orchestrator] data_health written to %s", out_path)
        else:
            err = result.stderr[:200] if result.stderr else "no output"
            logger.warning("[orchestrator] data_health query failed: %s", err)
    except Exception as exc:
        logger.warning("[orchestrator] data_health generation failed (non-fatal): %s", exc)


def _run_pipeline(run_type: str, run_id: str) -> None:
    """
    三阶段完整管道（morning / evening）或轻量盘中管道（intraday）。

    Step 0（同步）：生成 data_health.json 写入 /tmp/mra-{run_id}/，供所有 skill 读取。
    intraday：只跑 emotion + news + momentum，跳过辩论，mra-intraday 直接汇总。
    其他：6位分析师并行 → 侦察师 → 多空辩论 → 首席裁决。
    """
    is_intraday = (run_type == "intraday")

    # Step 0：Pre-flight data health check（同步，30秒内完成，非阻塞管道）
    with _state_lock:
        _agent_state["phase"] = "preflight"
        _agent_state["phase_detail"] = "数据健康检查中"
    _write_data_health(run_id)

    try:
        if is_intraday:
            _run_intraday(run_type, run_id)
        else:
            _run_full(run_type, run_id)
    finally:
        global _stop_requested
        with _state_lock:
            _agent_state["running"] = False
            _agent_state["phase"] = None
            _agent_state["phase_detail"] = None
            _agent_state["pids"] = set()
            _stop_requested = False

        tmp_dir = Path(f"/tmp/mra-{run_id}")
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _run_intraday(run_type: str, run_id: str) -> None:
    """轻量盘中管道：3个分析师并行 → intraday 汇总。"""
    with _state_lock:
        _agent_state["phase"] = "analysts"
        _agent_state["phase_detail"] = "盘中快速分析（情绪/新闻/动量）"

    analysts = ["mra-emotion", "mra-news", "mra-momentum"]
    _run_parallel(analysts, run_id, run_type, timeout=600)

    with _state_lock:
        _agent_state["phase"] = "chief"
        _agent_state["phase_detail"] = "生成盘中盘感摘要"

    ok = _run_skill("mra-intraday", run_id, run_type, timeout=600)

    with _state_lock:
        if ok:
            _agent_state["last_run"] = datetime.now().strftime("%H:%M:%S")
            _agent_state["last_error"] = None
        else:
            _agent_state["last_error"] = "盘中汇总失败"


def _run_full(run_type: str, run_id: str) -> None:
    """完整三阶段管道：5位分析师并行 → 侦察 → 多空辩论 → 首席裁决。"""
    with _state_lock:
        _agent_state["phase"] = "analysts"
        _agent_state["phase_detail"] = "6位分析师并行分析中"

    analysts = ["mra-emotion", "mra-sector", "mra-news", "mra-lhb", "mra-risk"]
    failed = _run_parallel(analysts, run_id, run_type, timeout=600)
    if failed:
        logger.warning("[orchestrator] 分析师失败: %s，继续后续阶段", failed)

    # 新增：侦察师（串行，依赖 sector.json）
    with _state_lock:
        _agent_state["phase"] = "analysts"
        _agent_state["phase_detail"] = "侦察师分析子链轮动机会"
    _run_skill("mra-scout", run_id, run_type, timeout=300)

    with _state_lock:
        _agent_state["phase"] = "debate"
        _agent_state["phase_detail"] = "多空辩论中"

    _run_skill("mra-bull", run_id, run_type, timeout=600)
    _run_skill("mra-bear", run_id, run_type, timeout=600)

    with _state_lock:
        _agent_state["phase"] = "chief"
        _agent_state["phase_detail"] = "首席裁决，生成最终报告"

    ok = _run_skill("mra-chief", run_id, run_type, timeout=900)

    with _state_lock:
        if ok:
            _agent_state["last_run"] = datetime.now().strftime("%H:%M:%S")
            _agent_state["last_error"] = None
        else:
            _agent_state["last_error"] = "首席裁决阶段失败"


def _run_parallel(skills: list, run_id: str, run_type: str, timeout: int) -> list:
    """并行运行多个 skill，返回失败的 skill 名称列表。"""
    results = [None] * len(skills)

    def _run_one(i, skill):
        results[i] = _run_skill(skill, run_id, run_type, timeout=timeout)

    threads = [
        threading.Thread(target=_run_one, args=(i, s), daemon=True)
        for i, s in enumerate(skills)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    return [skills[i] for i, ok in enumerate(results) if not ok]


def run_agent_analysis(run_type: str) -> dict:
    """
    启动三阶段 multi-agent 管道（非阻塞），立即返回 {"status": "started", ...}。
    """
    with _state_lock:
        if _agent_state["running"]:
            return {
                "status": "already_running",
                "run_type": _agent_state["last_run_type"],
                "phase": _agent_state["phase"],
            }
        _agent_state["running"] = True
        _agent_state["last_run_type"] = run_type
        _agent_state["last_error"] = None
        _agent_state["phase"] = None
        _agent_state["pids"] = set()

    run_id = uuid.uuid4().hex[:8]
    # 预建临时目录
    Path(f"/tmp/mra-{run_id}").mkdir(parents=True, exist_ok=True)

    t = threading.Thread(
        target=_run_pipeline,
        args=(run_type, run_id),
        daemon=True,
        name=f"mra-pipeline-{run_type}-{run_id}",
    )
    t.start()

    return {"status": "started", "run_type": run_type, "run_id": run_id}

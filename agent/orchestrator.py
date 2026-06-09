"""
多 Agent 编排层（两阶段）。

第一阶段（并行）：4个分析师各自读数据，写中间结果到 /tmp/mra-{run_id}/
  mra-emotion / mra-sector / mra-news / mra-risk

  串行追加：mra-scout（依赖 sector.json）

第二阶段：首席裁决
  mra-chief-morning（盘前）或 mra-chief-evening（盘后）→ 读全部结果 → 裁决 → write_result 落库

前端轮询 /api/agent/status 感知进度，/api/agent/latest 获取最终结果。
"""
import json
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
_TMP_ROOT = _PROJ_ROOT / "tmp"

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

# 基本面分析独立状态（与日常 pipeline 完全隔离）
_fundamental_state = {
    "running": False,
    "last_run": None,
    "last_error": None,
}
_fundamental_lock = threading.Lock()

# run_type → chief skill。auction/closing fallback to evening（无专属 skill）
_CHIEF_SKILL_MAP = {
    "morning": "mra-chief-morning",
    "evening": "mra-chief-evening",
    "auction": "mra-chief-evening",
    "closing": "mra-chief-evening",
}


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
        pids = set(_agent_state["pids"])
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
    return str(Path.home() / ".nvm/versions/node/v20.20.2/bin/claude")


def _run_skill(skill_name: str, run_id: str, run_type: str, timeout: int = 600) -> bool:
    """
    同步运行一个 claude skill，返回是否成功。
    调用者负责在后台线程里执行，不要在主线程调用。
    """
    env = os.environ.copy()
    env["MRA_RUN_ID"] = run_id
    env["MRA_RUN_TYPE"] = run_type
    env["MRA_TMP_DIR"] = str(_TMP_ROOT / f"mra-{run_id}")

    proc = None
    try:
        proc = subprocess.Popen(
            [_find_claude(), "-p", f"/{skill_name}",
             "--verbose",
             "--output-format", "stream-json",
             "--dangerously-skip-permissions"],
            cwd=str(_PROJ_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=env,
        )
        # Popen 返回后立刻加锁，若 stop 已被请求则直接 kill 新进程（Bug2 fix）
        with _state_lock:
            if _stop_requested:
                proc.kill()
                proc.communicate()
                return False
            _agent_state["pids"].add(proc.pid)

        _, stderr = proc.communicate(timeout=timeout)

        with _state_lock:
            if _stop_requested:
                logger.info("[orchestrator] stop requested, aborting after %s", skill_name)
                return False

        if proc.returncode != 0:
            err = (stderr or b"").decode(errors="replace")[:300]
            logger.warning("[orchestrator] %s failed rc=%d: %s", skill_name, proc.returncode, err)
            return False
        return True
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()   # drain pipe，防止下一个 Popen 阻塞（Bug3 fix）
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
    生成 data_health.json 写入 run 目录，供所有 skill 共享。
    即使失败也不阻断管道。
    """
    out_path = _TMP_ROOT / f"mra-{run_id}" / "data_health.json"
    try:
        result = subprocess.run(
            ["python", "agent/query.py", "data_health"],
            cwd=str(_PROJ_ROOT),
            capture_output=True,
            timeout=30,
        )
        stdout = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
        if result.returncode == 0 and stdout.strip():
            out_path.write_text(stdout.strip(), encoding="utf-8")
            logger.info("[orchestrator] data_health written to %s", out_path)
        else:
            err = result.stderr.decode("utf-8", errors="replace")[:200] if result.stderr else "no output"
            logger.warning("[orchestrator] data_health query failed: %s", err)
    except Exception as exc:
        logger.warning("[orchestrator] data_health generation failed (non-fatal): %s", exc)


def _check_data_gate(run_id: str, run_type: str) -> bool:
    """
    读取 data_health.json 的 abort_reason 字段，决定是否跳过本次分析。

    abort_reason 由 cmd_data_health 统一计算，触发条件：
    - STATIC_DATA_STALE：静态数据滞后超过1个交易日（T-2），且已过09:30
    - REALTIME_SNAPSHOT_STALE：盘中实时快照超过5分钟未更新（接口可能故障）

    非交易时段（pre_open/call_auction/weekend/holiday）不触发以上检查，直接放行。
    data_health.json 不存在或解析失败时保守放行。
    """
    health_path = _TMP_ROOT / f"mra-{run_id}" / "data_health.json"
    if not health_path.exists():
        return True

    try:
        health = json.loads(health_path.read_text())
    except Exception:
        return True

    abort_reason = health.get("abort_reason")
    if abort_reason:
        _write_abort_summary(run_type, abort_reason)
        return False

    return True


def _write_abort_summary(run_type: str, reason: str) -> None:
    """写入一条 abort 记录到 agent_summary 表。"""
    try:
        from db.storage import insert_agent_summary
        data = {
            "run_type": run_type,
            "market_status": {"mode": "不操作", "reason": reason},
            "summary_text": f"跳过分析：{reason}",
        }
        insert_agent_summary(
            content=f"跳过分析：{reason}",
            data_snapshot_json=json.dumps(data, ensure_ascii=False),
            run_type=run_type,
        )
    except Exception as exc:
        logger.warning("[orchestrator] abort summary write failed: %s", exc)


def _set_phase(phase: str, detail: str) -> None:
    with _state_lock:
        _agent_state["phase"] = phase
        _agent_state["phase_detail"] = detail


def _finish_pipeline(ok: bool, error_msg: str) -> None:
    with _state_lock:
        if ok:
            _agent_state["last_run"] = datetime.now().strftime("%H:%M:%S")
            _agent_state["last_error"] = None
        else:
            _agent_state["last_error"] = error_msg


def _run_pipeline(run_type: str, run_id: str) -> None:
    """
    完整管道（morning / evening）或轻量盘中管道（intraday）。

    intraday：只跑 emotion + news，mra-intraday 直接汇总。
    其他：4位分析师并行 → 侦察师 → 首席裁决。
    """
    _set_phase("preflight", "数据健康检查中")
    _write_data_health(run_id)

    try:
        if not _check_data_gate(run_id, run_type):
            logger.info("[orchestrator] data gate blocked run_type=%s, aborting", run_type)
            with _state_lock:
                _agent_state["last_run"] = datetime.now().strftime("%H:%M:%S")
                _agent_state["last_run_type"] = run_type
                _agent_state["last_error"] = "数据条件不满足，跳过本次分析"
            return

        if run_type == "intraday":
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

        shutil.rmtree(str(_TMP_ROOT / f"mra-{run_id}"), ignore_errors=True)


def _run_intraday(run_type: str, run_id: str) -> None:
    """轻量盘中管道：2个分析师并行 → intraday 汇总。"""
    _set_phase("analysts", "盘中快速分析（情绪/新闻）")
    _run_parallel(["mra-emotion", "mra-news"], run_id, run_type, timeout=600)

    _set_phase("chief", "生成盘中盘感摘要")
    ok = _run_skill("mra-intraday", run_id, run_type, timeout=600)
    _finish_pipeline(ok, "盘中汇总失败")


def _run_full(run_type: str, run_id: str) -> None:
    """完整管道：4位分析师并行 → 侦察 → 首席裁决。"""
    assert run_type != "intraday", f"[orchestrator] intraday should use _run_intraday (run_type={run_type!r})"

    _set_phase("analysts", "4位分析师并行分析中")
    failed = _run_parallel(["mra-emotion", "mra-sector", "mra-news"], run_id, run_type, timeout=600)
    failed += _run_parallel(["mra-risk"], run_id, run_type, timeout=1200)
    if failed:
        logger.warning("[orchestrator] 分析师失败: %s，继续后续阶段", failed)

    # 侦察师串行运行，依赖 sector.json
    _set_phase("analysts", "侦察师分析子链轮动机会")
    _run_skill("mra-scout", run_id, run_type, timeout=600)

    chief_skill = _CHIEF_SKILL_MAP.get(run_type, "mra-chief-evening")
    if run_type not in _CHIEF_SKILL_MAP or run_type not in ("morning", "evening"):
        logger.warning("[orchestrator] run_type=%s has no dedicated chief skill, using %s", run_type, chief_skill)

    _set_phase("chief", "首席裁决，生成最终报告")
    ok = _run_skill(chief_skill, run_id, run_type, timeout=900)
    _finish_pipeline(ok, "首席裁决阶段失败")


def _run_parallel(skills: list, run_id: str, run_type: str, timeout: int) -> list:
    """并行运行多个 skill，返回失败的 skill 名称列表。"""
    outcomes: dict[str, bool] = {}

    def _run_one(skill):
        outcomes[skill] = _run_skill(skill, run_id, run_type, timeout=timeout)

    threads = [threading.Thread(target=_run_one, args=(s,), daemon=True) for s in skills]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    return [s for s in skills if not outcomes.get(s)]


def get_fundamental_state() -> dict:
    with _fundamental_lock:
        return dict(_fundamental_state)


def _run_fundamental_pipeline(run_id: str) -> None:
    """基本面分析流水线：单个 skill，独立运行，结果持久化到 DB。"""
    try:
        with _fundamental_lock:
            _fundamental_state["running"] = True
            _fundamental_state["last_error"] = None

        # preflight data health，让 skill 也能读到数据状态
        _write_data_health(run_id)

        ok = _run_skill("mra-fundamental", run_id, "fundamental", timeout=1200)

        with _fundamental_lock:
            _fundamental_state["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _fundamental_state["last_error"] = None if ok else "基本面分析 skill 执行失败"
    except Exception as exc:
        logger.exception("[orchestrator] fundamental pipeline exception: %s", exc)
        with _fundamental_lock:
            _fundamental_state["last_error"] = str(exc)
    finally:
        with _fundamental_lock:
            _fundamental_state["running"] = False
        shutil.rmtree(str(_TMP_ROOT / f"mra-{run_id}"), ignore_errors=True)


def run_fundamental_analysis() -> dict:
    """
    手动触发基本面分析（非阻塞），立即返回状态。
    与日常 pipeline 完全独立，不互相阻塞。
    """
    with _fundamental_lock:
        if _fundamental_state["running"]:
            return {"status": "already_running"}
        _fundamental_state["running"] = True

    run_id = f"fundamental-{uuid.uuid4().hex[:8]}"
    Path(str(_TMP_ROOT / f"mra-{run_id}")).mkdir(parents=True, exist_ok=True)

    threading.Thread(
        target=_run_fundamental_pipeline,
        args=(run_id,),
        daemon=True,
        name=f"mra-fundamental-{run_id}",
    ).start()

    return {"status": "started", "run_id": run_id}


def run_agent_analysis(run_type: str) -> dict:
    """启动 multi-agent 管道（非阻塞），立即返回 {"status": "started", ...}。"""
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
    Path(str(_TMP_ROOT / f"mra-{run_id}")).mkdir(parents=True, exist_ok=True)

    threading.Thread(
        target=_run_pipeline,
        args=(run_type, run_id),
        daemon=True,
        name=f"mra-pipeline-{run_type}-{run_id}",
    ).start()

    return {"status": "started", "run_type": run_type, "run_id": run_id}

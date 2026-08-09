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
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from core.python_runtime import get_python_executable

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
        # 子进程以独立进程组启动（pgid == pid），连组杀才能清掉 MCP 孙进程
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def _find_claude() -> str:
    custom = os.environ.get("CLAUDE_BIN", "")
    if custom and Path(custom).is_file():
        return custom
    found = shutil.which("claude")
    if found:
        return found
    # launchd 等精简 PATH 环境（/usr/bin:/bin:...）下，补充常见安装位置
    for candidate in (
        Path("/opt/homebrew/bin/claude"),            # Homebrew (Apple Silicon)
        Path("/usr/local/bin/claude"),               # Homebrew (Intel) / npm global
        Path.home() / ".nvm/versions/node/v20.20.2/bin/claude",
    ):
        if candidate.is_file():
            return str(candidate)
    return str(Path.home() / ".nvm/versions/node/v20.20.2/bin/claude")


def _wait_wallclock(proc: subprocess.Popen, timeout: float) -> None:
    """
    墙钟超时等待进程退出，超时抛 subprocess.TimeoutExpired。

    不能用 proc.wait(timeout=...)：其内部用 time.monotonic()，
    而 macOS 的 CLOCK_MONOTONIC 在系统睡眠期间停摆——笔记本合盖后
    300s 超时会拖到 70+ 分钟（墙钟）才触发（生产日志实测）。
    墙钟在睡眠唤醒后跳变，超时会立即触发。
    """
    deadline = time.time() + timeout
    while True:
        if proc.poll() is not None:
            return
        remaining = deadline - time.time()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(proc.args, timeout)
        time.sleep(min(0.5, remaining))


def _kill_proc_group(proc: subprocess.Popen) -> None:
    """
    杀掉整个进程组（含 claude 派生的 MCP 服务器等孙进程）。
    进程以 start_new_session=True 启动，pgid == 子进程 pid。
    """
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except ProcessLookupError:
        pass
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    try:
        _wait_wallclock(proc, 10)
    except Exception:
        pass


def _run_skill(skill_name: str, run_id: str, run_type: str, timeout: int = 600,
               pool: str | None = None, extra_env: dict | None = None) -> bool:
    """
    同步运行一个 claude skill，返回是否成功。
    调用者负责在后台线程里执行，不要在主线程调用。

    注意：stderr 重定向到临时文件而非 PIPE，且进程独立成组。
    原因：claude 派生的孙进程（MCP 服务器）会继承 stderr 管道写端，
    kill 父进程后管道永不 EOF，communicate() 将永久阻塞（实测复现）。
    """
    env = os.environ.copy()
    env["MRA_RUN_ID"] = run_id
    env["MRA_RUN_TYPE"] = run_type
    env["MRA_TMP_DIR"] = str(_TMP_ROOT / f"mra-{run_id}")
    if pool:
        env["MRA_POOL"] = pool
    if extra_env:
        for k, v in extra_env.items():
            if v is not None:
                env[k] = str(v)

    proc = None
    try:
        err_file = tempfile.TemporaryFile()
        proc = subprocess.Popen(
            [_find_claude(), "-p", f"/{skill_name}",
             "--verbose",
             "--output-format", "stream-json",
             "--dangerously-skip-permissions"],
            cwd=str(_PROJ_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=err_file,
            env=env,
            start_new_session=True,
        )
        # Popen 返回后立刻加锁，若 stop 已被请求则直接 kill 新进程（Bug2 fix）
        with _state_lock:
            if _stop_requested:
                _kill_proc_group(proc)
                return False
            _agent_state["pids"].add(proc.pid)

        try:
            _wait_wallclock(proc, timeout)
        except subprocess.TimeoutExpired:
            _kill_proc_group(proc)
            logger.error("[orchestrator] %s timed out after %ds", skill_name, timeout)
            return False

        with _state_lock:
            if _stop_requested:
                logger.info("[orchestrator] stop requested, aborting after %s", skill_name)
                return False

        if proc.returncode != 0:
            err_file.seek(0)
            err = err_file.read(2000).decode(errors="replace")[:300]
            logger.warning("[orchestrator] %s failed rc=%d: %s", skill_name, proc.returncode, err)
            return False
        return True
    except Exception as exc:
        logger.exception("[orchestrator] %s exception: %s", skill_name, exc)
        if proc is not None and proc.poll() is None:
            _kill_proc_group(proc)
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
            [get_python_executable(), "agent/query.py", "data_health"],
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


# run_type → (skill 名, 阶段描述)。单 skill 轻量专项管道，跳过数据健康门禁（不依赖行情数据）。
_SINGLE_SKILL_PIPELINES = {
    "policy":    ("mra-policy",    "政策解读分析中"),
    "research":  ("mra-research",  "研报解读分析中"),
    "notice":    ("mra-notice",    "公告解读分析中"),
    "watchlist": ("mra-watchlist", "关注股池动态分析中"),
}


def _run_pipeline(run_type: str, run_id: str, pool: str | None = None) -> None:
    """
    完整管道（morning / evening）、轻量盘中管道（intraday）或单 skill 专项管道（policy / research）。

    intraday：只跑 emotion + news，mra-intraday 直接汇总。
    policy / research：只跑对应单 skill，跳过数据健康门禁。
    watchlist：单 skill，pool 非空时通过 MRA_POOL 环境变量限定分析池。
    其他：4位分析师并行 → 侦察师 → 首席裁决。
    """
    single = _SINGLE_SKILL_PIPELINES.get(run_type)
    if single:
        try:
            if run_type == "watchlist":
                _run_watchlist(run_type, run_id, pool)
                return
            skill_name, phase_detail = single
            if pool:
                phase_detail = f"{phase_detail}（{pool}）"
            _set_phase("analysts", phase_detail)
            ok = _run_skill(skill_name, run_id, run_type, timeout=900, pool=pool)
            _finish_pipeline(ok, f"{phase_detail.replace('中', '')}失败")
        finally:
            _cleanup_after_pipeline(run_id)
        return

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
        _cleanup_after_pipeline(run_id)


def _cleanup_after_pipeline(run_id: str) -> None:
    """管道结束后的状态复位与临时目录清理。"""
    global _stop_requested
    with _state_lock:
        _agent_state["running"] = False
        _agent_state["phase"] = None
        _agent_state["phase_detail"] = None
        _agent_state["pids"] = set()
        _stop_requested = False

    # 管道结束后自动推企微 (opt-in, 默认 off)
    # 配置: AGENT_AUTO_NOTIFY=1 开启, AGENT_AUTO_NOTIFY_ADDRESS=info (默认) / 任意已配地址
    # 注意: 必须在 rmtree 之前调, auto-notify 要读 _TMP_ROOT/mra-{run_id}/last_row_id
    if os.environ.get("AGENT_AUTO_NOTIFY", "0") == "1":
        _auto_notify_latest(run_type)

    shutil.rmtree(str(_TMP_ROOT / f"mra-{run_id}"), ignore_errors=True)


def _auto_notify_latest(run_type: str) -> None:
    """跑完 agent pipeline 自动推本次 run 写到 DB 的那一行 (opt-in).

    关键设计: 不查"最新一行" (可能错推上次的), 而是读 write_result 落地的
    `last_row_id` 文件, 拿本次 run 真正写入的 row_id。文件不存在 = chief 失败 /
    异常退出, 这种情况下**不推任何东西**, 避免错推陈旧报告。
    """
    try:
        from db.storage import get_agent_summary_by_id
        from fetcher.wecom_notifier import send_ai_report, is_configured, send_text, _strip_html

        address = os.environ.get("AGENT_AUTO_NOTIFY_ADDRESS", "info").strip() or "info"
        if not is_configured(address):
            logger.warning("[orchestrator] auto-notify skipped: address='%s' 未配 key", address)
            return

        # 从 write_result 落地的文件读本次 run 的 row_id
        # 注意: 临时目录在 _cleanup_after_pipeline 开头才被清理, 但 _auto_notify_latest
        # 是从 _cleanup 末尾调的, 所以这里文件还在
        last_row_file = _TMP_ROOT / "last_row_id_global"
        # 优先用本次 run 的临时目录; 兜底用全局文件 (watchlist 等可能写到不同位置)
        row = None
        # 查 mra-* 目录下所有 last_row_id (支持多 skill 写入)
        candidate_files = sorted(_TMP_ROOT.glob("mra-*/last_row_id"), key=lambda p: p.stat().st_mtime, reverse=True)
        for f in candidate_files:
            try:
                rid = int(f.read_text().strip())
                if rid > 0:
                    row = get_agent_summary_by_id(rid)
                    if row:
                        logger.info("[orchestrator] auto-notify: 找到本次 run row_id=%d via %s", rid, f.name)
                        break
            except Exception as e:
                logger.debug("[orchestrator] 读 %s 失败: %s", f, e)

        if not row:
            # 兜底: 看是否有 abort 摘要
            abort_row = None
            try:
                from db.storage import get_agent_summary_history
                rows = get_agent_summary_history(limit=5, today_only=True)
                for r in rows:
                    if r.get("run_type") == run_type and r.get("content", "").startswith("跳过分析"):
                        abort_row = r
                        break
            except Exception:
                pass
            if abort_row:
                run_type_cn = {
                    "morning": "盘前", "intraday": "盘中", "evening": "盘后",
                    "policy": "政策解读", "research": "研报解读", "notice": "公告解读",
                    "watchlist": "股池", "auction": "集合竞价", "closing": "盘后速递",
                }.get(run_type, run_type)
                send_text(
                    f"⚠️ {run_type_cn} · 跳过本次分析\n{abort_row.get('content', '')}",
                    address=address,
                )
                logger.info("[orchestrator] auto-notify: 本次 run 被跳过, 推 abort 通知")
            else:
                logger.warning("[orchestrator] auto-notify skipped: 找不到本次 run 写入的行 (chief 可能失败), 避免错推上次报告")
            return

        # 拼 markdown 摘要
        import json as _json
        snap = row.get("data_snapshot_json")
        summary = ""
        if snap:
            try:
                d = _json.loads(snap)
                summary = d.get("summary_text") or ""
            except Exception:
                pass
        if not summary:
            summary = (row.get("content") or "")[:500]

        run_type_cn = {
            "morning": "盘前首席", "intraday": "盘中解说",
            "evening": "盘后首席", "policy": "政策解读",
            "research": "研报展望", "notice": "公告解读",
            "watchlist": "关注股池", "auction": "集合竞价",
            "closing": "盘后速递",
        }.get(run_type, run_type)
        title = f"🤖 {run_type_cn} · {(row.get('summary_time') or '')[:16]}"

        # 完整报告: 默认开 (跟手动 push 一致), 用 AGENT_AUTO_NOTIFY_FULL=0 关闭
        include_full = os.environ.get("AGENT_AUTO_NOTIFY_FULL", "1") == "1"
        full_text = None
        if include_full:
            html = row.get("report_html") or ""
            if html:
                full_text = _strip_html(html)
            else:
                full_text = (row.get("content") or "").strip() or None

        ok = send_ai_report(
            title=title,
            summary=summary[:2000],
            full_text=full_text,
            address=address,
        )
        logger.info("[orchestrator] auto-notify run_type=%s row_id=%d address=%s ok=%s full=%s",
                    run_type, row.get("id"), address, ok, bool(full_text))
    except Exception as exc:
        logger.warning("[orchestrator] auto-notify failed: %s", exc)


# ── 股池动态分析（两段式：单股并行体检 → 汇总落库） ────────────────────────────

_WATCHLIST_STOCK_CONCURRENCY = 5    # 进程级并发上限；超时已修为进程组连杀，卡住的股票 300s 后必被杀并标注失败
_WATCHLIST_STOCK_TIMEOUT = 300      # 单只超时（秒，墙钟），失败不阻塞其他
_WATCHLIST_MAX_STOCKS = 20          # 与 skill 约定一致，超出截断
_WATCHLIST_AGG_TIMEOUT = 600        # 汇总 skill 超时


def _query_watchlist_items(pool: str | None) -> dict:
    """直接调 query.py 拿股池列表（不经 claude），失败返回空结构。"""
    cmd = [get_python_executable(), "agent/query.py", "watchlist"]
    if pool:
        cmd += ["--pool", pool]
    try:
        result = subprocess.run(cmd, cwd=str(_PROJ_ROOT), capture_output=True, timeout=30)
        if result.returncode == 0 and result.stdout:
            return json.loads(result.stdout.decode("utf-8", errors="replace"))
        err = result.stderr.decode("utf-8", errors="replace")[:200] if result.stderr else "no output"
        logger.warning("[orchestrator] watchlist query failed: %s", err)
    except Exception as exc:
        logger.warning("[orchestrator] watchlist query exception: %s", exc)
    return {"items": [], "truncated": False}


def _run_watchlist(run_type: str, run_id: str, pool: str | None) -> None:
    """
    股池动态分析两段式管道：
      并行段：每只股票一个 mra-watchlist-stock 子进程（并发上限 5，单只 300s 墙钟超时），
              各自写 ${MRA_TMP_DIR}/watchlist_stock_<code>.json；失败的记录到 meta.failed。
      汇总段：mra-watchlist 读 meta + 全部单股 JSON，归纳生成报告并 write_result 落库。
    股池为空时跳过并行段，汇总 skill 会写"股池为空"提示报告。
    """
    tmp_dir = _TMP_ROOT / f"mra-{run_id}"

    payload = _query_watchlist_items(pool)
    items = (payload.get("items") or [])[:_WATCHLIST_MAX_STOCKS]
    truncated = bool(payload.get("truncated"))
    total = len(items)

    failed: list[str] = []
    if total:
        done = {"n": 0}
        lock = threading.Lock()
        sem = threading.Semaphore(_WATCHLIST_STOCK_CONCURRENCY)
        _set_phase("analysts", f"股池并行分析中 0/{total} 只")

        def _run_one(item: dict) -> None:
            code = item.get("code", "")
            with sem:
                if _stop_requested:
                    return
                ok = _run_skill(
                    "mra-watchlist-stock", run_id, run_type,
                    timeout=_WATCHLIST_STOCK_TIMEOUT, pool=pool,
                    extra_env={
                        "MRA_STOCK_CODE": code,
                        "MRA_STOCK_NAME": item.get("name", ""),
                        "MRA_STOCK_NOTE": item.get("note", ""),
                    },
                )
                produced = (tmp_dir / f"watchlist_stock_{code}.json").exists()
            with lock:
                done["n"] += 1
                if not ok or not produced:
                    failed.append(code)
                progress = f"股池并行分析中 {done['n']}/{total} 只"
                if failed:
                    progress += f"（失败 {len(failed)}）"
                _set_phase("analysts", progress)

        threads = [threading.Thread(target=_run_one, args=(it,), daemon=True) for it in items]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    # 写运行元数据，供汇总 skill 核对完整性（含失败清单）
    meta = {
        "run_type": run_type,
        "pool": pool or "全部",
        "query_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total": total,
        "truncated": truncated,
        "items": items,
        "failed": failed,
    }
    try:
        (tmp_dir / "watchlist_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, default=str), encoding="utf-8"
        )
    except Exception as exc:
        logger.warning("[orchestrator] watchlist meta write failed: %s", exc)

    _set_phase("chief", "汇总股池报告中")
    ok = _run_skill("mra-watchlist", run_id, run_type, timeout=_WATCHLIST_AGG_TIMEOUT, pool=pool)
    _finish_pipeline(ok, "股池汇总失败")


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


def run_agent_analysis(run_type: str, pool: str | None = None) -> dict:
    """启动 multi-agent 管道（非阻塞），立即返回 {"status": "started", ...}。
    pool 仅对 run_type=watchlist 有意义：限定只分析某个股池。"""
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
        args=(run_type, run_id, pool),
        daemon=True,
        name=f"mra-pipeline-{run_type}-{run_id}",
    ).start()

    return {"status": "started", "run_type": run_type, "run_id": run_id, "pool": pool}

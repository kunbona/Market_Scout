"""
research_board — Claude CLI / Kimi 调用封装

所有 AI 调用统一走 claude -p --dangerously-skip-permissions 子进程。
Claude CLI（OAuth 登录）作为顶层 agent，通过 Bash tool 运行
`codex exec --profile research` 来驱动 Kimi K2.6 subagent。

run_claude_pipeline()：整个分析 pipeline 的入口。
  启动单个 claude -p，claude 内部用 Task tool 并行 dispatch N 个
  subagent（每个 subagent 调一个维度的 Kimi），所有 subagent 共享
  同一 claude session 上下文，包含验收和重试逻辑。
  以 stream-json 流式读取进度，最终返回结构化 JSON。

_call_llm()：单次 Kimi 调用（用于单 tab 重生成、修复等场景）。
call_claude_text()：纯文字 Claude 调用（decompose 等）。
screenshot_html()：playwright 截图工具（维度验收时由 cli 内部 agent 调用）。
"""

import glob
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time as _time

logger = logging.getLogger(__name__)

# claude -p 总超时（秒）；两步续写时会 ×2
LLM_TIMEOUT = 720

_CLAUDE_BIN = "claude"


def _get_env() -> dict:
    """
    合并 .env.local 到当前环境变量，确保 HOME 被传递。
    claude CLI 走 OAuth（~/.claude），OPENROUTER_API_KEY 传给 codex 子进程。
    """
    env = os.environ.copy()
    env_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env.local")
    if os.path.exists(env_file):
        with open(env_file) as _f:
            lines = _f.readlines()
        for line in lines:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env.setdefault(k.strip(), v.strip())
    env.setdefault("HOME", os.path.expanduser("~"))
    return env


def _run_claude(prompt: str, timeout: int, env: dict) -> str:
    """
    运行 `claude -p --dangerously-skip-permissions <prompt>`，返回文本输出。
    使用 stream-json 格式（--output-format text 在当前 CLI 版本下 stdout 为空）。
    失败抛 RuntimeError。
    """
    result = subprocess.run(
        [
            _CLAUDE_BIN, "-p",
            "--dangerously-skip-permissions",
            "--output-format", "stream-json",
            "--verbose",
            prompt,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        cwd=os.path.dirname(os.path.dirname(__file__)),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"claude -p 退出码 {result.returncode}: "
            f"{(result.stderr or result.stdout)[:400]}"
        )
    # 从 stream-json 事件流中提取顶层 assistant text blocks
    content_parts = []
    for raw_line in (result.stdout or "").splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            event = json.loads(raw_line)
        except Exception:
            continue
        if event.get("type") == "assistant" and event.get("parent_tool_use_id") is None:
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "text":
                    txt = block.get("text", "").strip()
                    if txt:
                        content_parts.append(txt)
    content = "\n".join(content_parts).strip()
    if not content:
        detail = (result.stderr or "").strip()[:400]
        raise RuntimeError(f"claude -p 输出为空{(': ' + detail) if detail else ''}")
    return content


# ── Kimi 调用（通过 claude orchestrator + codex subagent） ────────────────────

def _call_llm(prompt: str, timeout: int = LLM_TIMEOUT, _retries: int = 2) -> str:
    """
    通过 claude CLI agent 驱动 Kimi K2.6 完成任务，返回 Kimi 的输出。

    claude -p 作为顶层 agent，用 Bash tool 运行 codex exec --profile research，
    codex 管理 Kimi K2.6 subagent。claude 自主决定是否重试。

    失败自动重试，最终失败抛 RuntimeError。
    """
    env = _get_env()
    last_err: Exception = RuntimeError("Kimi 调用失败（未知原因）")

    for attempt in range(1 + _retries):
        if attempt > 0:
            wait = 15 * attempt
            logger.warning(
                f"[cli_runner] 第 {attempt} 次重试，{wait}s 后… 上次错误: {last_err}"
            )
            _time.sleep(wait)

        rand = hashlib.md5(
            f"{attempt}{_time.time()}{id(prompt)}".encode()
        ).hexdigest()[:8]

        # 把任务交给 claude agent，不写死 shell 脚本
        # claude 自主用 Bash tool 运行 codex exec --profile research
        # 并通过 codex 的 subagent 能力驱动 Kimi K2.6
        orchestrator_prompt = (
            f"使用 codex exec --profile research 执行以下分析任务，"
            f"把 Kimi 的完整输出返回给我，不要添加任何说明或包装。\n"
            f"输出文件路径：/tmp/kimi_out_{rand}.txt\n"
            f"如果输出为空或 codex 调用失败，重试一次。\n\n"
            f"## 任务内容\n\n"
            f"{prompt}"
        )

        try:
            content = _run_claude(orchestrator_prompt, timeout=timeout, env=env)
            content = _unwrap_html(content)
            if not content:
                last_err = RuntimeError("claude 输出为空")
                continue
            return content
        except subprocess.TimeoutExpired:
            last_err = RuntimeError(f"claude -p timeout {timeout}s")
            continue
        except Exception as e:
            last_err = RuntimeError(f"claude -p 异常: {e}")
            continue

    raise last_err



def _unwrap_html(text: str) -> str:
    """如果输出是 HTML，从 <!DOCTYPE 或 <html 开始截取。"""
    if not text:
        return text
    for marker in ["<!DOCTYPE", "<!doctype", "<html", "<HTML"]:
        idx = text.find(marker)
        if idx > 0:
            return text[idx:].strip()
    return text.strip()


# ── Claude 纯文字调用（用于 decompose / 文字 fallback） ──────────────────────

def call_claude_text(system: str, user: str, timeout: int = 120) -> str:
    """通过 claude -p 做纯文字调用（decompose、文字 fallback review 等）。"""
    env = _get_env()
    return _run_claude(f"{system}\n\n---\n\n{user}", timeout=timeout, env=env)


# ── 单 claude agent 全流程 pipeline ──────────────────────────────────────────

def run_claude_pipeline(
    prompt: str,
    progress_cb=None,
    timeout: int = 3600,
) -> dict:
    """
    启动单个 claude -p --dangerously-skip-permissions 子进程执行完整分析 pipeline。

    Claude 内部用 Task tool 并行 dispatch subagent，每个 subagent 通过
    codex exec --profile research 驱动 Kimi K2.6。所有 subagent 在同一
    claude session 内，共享上下文，claude 汇总后返回结构化 JSON。

    进度通过 stream-json 流式读取，每条 assistant 文字消息触发 progress_cb。

    返回 claude 输出里的 JSON 对象（包含 tabs、messages 等）。
    失败抛 RuntimeError。
    """
    env = _get_env()
    cmd = [
        _CLAUDE_BIN, "-p",
        "--dangerously-skip-permissions",
        "--output-format", "stream-json",
        "--verbose",
        prompt,
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=os.path.dirname(os.path.dirname(__file__)),
    )

    # stream-json: result.result 始终为空，实际输出在 assistant 事件的 content[].text 里
    # 收集顶层（非 subagent）assistant 文字块，最后一段即为最终输出
    text_blocks: list[str] = []
    is_error = False
    start = _time.time()

    # 并发消耗 stderr，防止管道写满导致子进程阻塞
    import threading as _threading
    stderr_chunks: list[str] = []

    def _drain_stderr():
        try:
            stderr_chunks.append(proc.stderr.read())
        except Exception:
            pass

    _stderr_thread = _threading.Thread(target=_drain_stderr, daemon=True)
    _stderr_thread.start()

    try:
        for raw_line in proc.stdout:
            if _time.time() - start > timeout:
                proc.kill()
                raise RuntimeError(f"run_claude_pipeline timeout after {timeout}s")

            raw_line = raw_line.strip()
            if not raw_line:
                continue

            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                continue

            etype = event.get("type", "")

            if etype == "result":
                is_error = event.get("is_error", False)
                if is_error:
                    err_detail = event.get("result", "") or event.get("error", "")
                    logger.error(f"[pipeline] result is_error=True detail={repr(err_detail[:500])}")
                    logger.error(f"[pipeline] full result event={repr(str(event)[:800])}")

            elif etype == "assistant":
                # parent_tool_use_id 非 None 时是 subagent 内部消息，忽略
                if event.get("parent_tool_use_id") is not None:
                    continue
                for block in event.get("message", {}).get("content", []):
                    if block.get("type") == "text":
                        txt = block.get("text", "").strip()
                        if txt:
                            text_blocks.append(txt)
                            if progress_cb:
                                progress_cb(txt)

    finally:
        proc.stdout.close()
        _stderr_thread.join(timeout=5)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        stderr_out = "".join(stderr_chunks)
        if stderr_out:
            logger.error(f"[pipeline] stderr: {stderr_out[:1000]}")

    final_text = "\n".join(text_blocks)
    logger.info(f"[pipeline] text_blocks={len(text_blocks)} final_text_len={len(final_text)}")
    logger.info(f"[pipeline] final_text tail: {repr(final_text[-300:])}")
    if not final_text or is_error:
        raise RuntimeError("claude pipeline 输出为空" if not final_text else "claude pipeline 返回错误")

    # 尝试从输出解析 JSON
    result = _extract_json(final_text)
    logger.info(f"[pipeline] _extract_json result keys={list(result.keys()) if isinstance(result, dict) else type(result)}")
    if isinstance(result, dict) and result.get("tabs"):
        logger.info(f"[pipeline] tabs={[t.get('name') for t in result['tabs']]}")
    if isinstance(result, dict) and "raw_text" in result and len(result) == 1:
        result = {"raw_text": final_text}
    return result


# ── playwright 截图 ───────────────────────────────────────────────────────────

_playwright_ready = False
_playwright_lock = threading.Lock()


def _ensure_nss_in_path():
    """playwright chromium 需要 libnspr4/libnss3，自动从 conda pkgs 注入 LD_LIBRARY_PATH。"""
    if os.environ.get("_MARKET_RADAR_NSS_PATCHED"):
        return
    found_dirs = set()
    home = os.path.expanduser("~")
    search_roots = []
    conda_prefix = os.environ.get("CONDA_PREFIX", "")
    if conda_prefix:
        search_roots.append(os.path.dirname(conda_prefix))
    for base in [f"{home}/miniconda3", f"{home}/anaconda3", f"{home}/miniforge3"]:
        if os.path.isdir(base):
            search_roots.append(f"{base}/pkgs")
            search_roots.append(f"{base}/envs")
    for root in search_roots:
        for path in glob.glob(f"{root}/**/libnspr4.so", recursive=True):
            found_dirs.add(os.path.dirname(path))
    if found_dirs:
        extra = ":".join(sorted(found_dirs))
        current = os.environ.get("LD_LIBRARY_PATH", "")
        os.environ["LD_LIBRARY_PATH"] = f"{extra}:{current}" if current else extra
        logger.info(f"[screenshot] 注入 NSS 库路径: {extra}")
    os.environ["_MARKET_RADAR_NSS_PATCHED"] = "1"


def _ensure_playwright():
    """首次调用时安装 playwright + chromium（只装一次）。"""
    global _playwright_ready

    with _playwright_lock:
        if _playwright_ready:
            return

        _ensure_nss_in_path()

        try:
            import playwright  # noqa: F401
        except ImportError:
            logger.info("[screenshot] playwright 未安装，正在安装…")
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "playwright", "--quiet"],
                stdout=subprocess.DEVNULL,
            )

        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                browser.close()
        except Exception:
            logger.info("[screenshot] chromium 未安装，正在安装系统依赖…")
            subprocess.call(
                [sys.executable, "-m", "playwright", "install", "chromium", "--with-deps"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        _playwright_ready = True


_ECHARTS_WAIT_SCRIPT = """
() => new Promise((resolve) => {
    const MAX_WAIT = 3000;
    const start = Date.now();
    function check() {
        if (typeof echarts === 'undefined') { resolve(true); return; }
        const els = document.querySelectorAll('[_echarts_instance_]');
        if (els.length === 0) { resolve(true); return; }
        let allDone = true;
        els.forEach(el => {
            const inst = echarts.getInstanceByDom(el);
            if (inst && inst._model && !inst._model.getOption) allDone = false;
        });
        if (allDone || Date.now() - start > MAX_WAIT) { resolve(true); return; }
        setTimeout(check, 100);
    }
    setTimeout(check, 300);
})
"""


def screenshot_html(html: str) -> list[bytes]:
    """
    渲染 HTML（含 ECharts），等待图表完成，按滚动位置截 1-3 张图（各 1200×1400px）。
    返回 PNG bytes 列表，最多 3 张。
    """
    _ensure_playwright()
    from playwright.sync_api import sync_playwright

    shots: list[bytes] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1200, "height": 1400})

        page.set_content(html, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        try:
            page.evaluate(_ECHARTS_WAIT_SCRIPT)
        except Exception:
            pass

        page_height = page.evaluate("() => document.body.scrollHeight")

        offsets = [0]
        if page_height > 1400:
            offsets.append(1300)
        if page_height > 2700:
            offsets.append(2600)

        for offset in offsets:
            page.evaluate(f"window.scrollTo(0, {offset})")
            _time.sleep(0.15)
            shot = page.screenshot(
                clip={"x": 0, "y": offset, "width": 1200,
                      "height": min(1400, page_height - offset)},
                type="png",
            )
            shots.append(shot)

        browser.close()

    return shots


def _extract_json(text: str) -> dict | list:
    """从输出中提取 JSON。"""
    match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\}|\[[\s\S]*?\])\s*```",
                      text, re.IGNORECASE)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            pass

    for start_char, end_char in [("{", "}"), ("[", "]")]:
        idx = text.find(start_char)
        if idx >= 0:
            last_idx = text.rfind(end_char)
            if last_idx > idx:
                try:
                    return json.loads(text[idx:last_idx + 1])
                except Exception:
                    pass

    return {"raw_text": text}



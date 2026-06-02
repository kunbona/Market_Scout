"""
research_board — Kimi CLI subprocess 封装
调用方式：kimi -y -p "prompt"
支持并发多路（ThreadPoolExecutor）
"""

import hashlib
import json
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

# 单次 Kimi 调用超时（秒）
KIMI_TIMEOUT = 300

# 并发路数上限
MAX_WORKERS = 4


def _call_kimi(prompt: str, timeout: int = KIMI_TIMEOUT) -> str:
    """
    用 kimi -y -p <prompt> 非交互调用，返回 stdout 文字。
    -y: 自动批准所有操作（等同 --yolo）
    -p: 单次 prompt 模式，执行完即退出
    失败时抛出 RuntimeError。
    """
    try:
        result = subprocess.run(
            ["kimi", "-y", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            err = result.stderr[:500] if result.stderr else "unknown error"
            raise RuntimeError(f"kimi exit {result.returncode}: {err}")
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"kimi timeout after {timeout}s")
    except FileNotFoundError:
        raise RuntimeError("kimi CLI not found, please run: npm install -g @moonshot-ai/kimi-code")


def _extract_json(text: str) -> dict | list:
    """
    从 Kimi 输出中提取 JSON。
    优先匹配 ```json ... ``` 代码块，其次尝试直接 json.loads。
    """
    # 尝试提取 ```json 代码块
    import re
    match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\}|\[[\s\S]*?\])\s*```", text, re.IGNORECASE)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            pass

    # 尝试找第一个 { 或 [ 开始的 JSON
    for start_char, end_char in [("{", "}"), ("[", "]")]:
        idx = text.find(start_char)
        if idx >= 0:
            # 找到最后一个匹配的结束符
            last_idx = text.rfind(end_char)
            if last_idx > idx:
                try:
                    return json.loads(text[idx:last_idx + 1])
                except Exception:
                    pass

    # 返回原始文字作为 fallback
    return {"raw_text": text}


def prompt_hash(prompt: str) -> str:
    return hashlib.md5(prompt.encode()).hexdigest()[:12]


def run_dimension_analysis(
    project_name: str,
    dimension: str,
    dimension_prompt_template: str,
    report_batches: list[str],
    progress_cb=None,
) -> list[dict]:
    """
    对一个分析维度，并发处理多个研报批次，返回各批次的分析结果列表。
    每个结果是 dict（从 Kimi JSON 输出解析）。
    """
    def _log(msg: str):
        logger.info(msg)
        if progress_cb:
            progress_cb(msg)

    results = [None] * len(report_batches)

    def _run_one(idx: int, batch_text: str) -> tuple[int, dict]:
        prompt = dimension_prompt_template.format(
            project_name=project_name,
            dimension=dimension,
            report_text=batch_text,
            batch_index=idx + 1,
            total_batches=len(report_batches),
        )
        _log(f"[kimi] {dimension} batch {idx+1}/{len(report_batches)} 开始...")
        raw = _call_kimi(prompt)
        parsed = _extract_json(raw)
        _log(f"[kimi] {dimension} batch {idx+1}/{len(report_batches)} 完成")
        return idx, parsed

    workers = min(MAX_WORKERS, len(report_batches))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_run_one, i, batch): i
            for i, batch in enumerate(report_batches)
        }
        for future in as_completed(futures):
            try:
                idx, parsed = future.result()
                results[idx] = parsed
            except Exception as e:
                i = futures[future]
                _log(f"[kimi] {dimension} batch {i+1} 失败: {e}")
                results[i] = {"error": str(e)}

    return [r for r in results if r is not None]


def run_merge_analysis(
    project_name: str,
    dimension: str,
    batch_results: list[dict],
    merge_prompt_template: str,
    progress_cb=None,
) -> dict:
    """
    当一个维度有多个批次时，用 Kimi 再做一次合并汇总。
    单批次直接返回，无需二次合并。
    """
    if len(batch_results) <= 1:
        return batch_results[0] if batch_results else {}

    def _log(msg: str):
        logger.info(msg)
        if progress_cb:
            progress_cb(msg)

    batch_text = json.dumps(batch_results, ensure_ascii=False, indent=2)
    prompt = merge_prompt_template.format(
        project_name=project_name,
        dimension=dimension,
        batch_results=batch_text,
    )
    _log(f"[kimi] {dimension} 合并 {len(batch_results)} 个批次...")
    raw = _call_kimi(prompt)
    return _extract_json(raw)

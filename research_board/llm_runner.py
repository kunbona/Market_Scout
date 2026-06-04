"""
research_board — LLM 调用封装（直接调用 OpenRouter REST API）
使用 requests 直接发 HTTP 请求，避免 codex exec 子进程的启动开销和超时问题。
模型：moonshotai/kimi-k2.6（与 codex profile research 保持一致）
支持并发多路（ThreadPoolExecutor）
"""

import hashlib
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests as _requests

logger = logging.getLogger(__name__)

# 单次调用超时（秒）：Kimi K2.6 大上下文响应较慢，给足时间
LLM_TIMEOUT = 300

# 并发路数：避免 OpenRouter 限流
MAX_WORKERS = 3

# Kimi 模型配置
_KIMI_MODEL = "moonshotai/kimi-k2.6"
_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# Kimi K2.6 的最大输出 token（留余量，避免 reasoning 占满后 content 为 null）
_KIMI_MAX_TOKENS = 16000


def _call_llm(prompt: str, timeout: int = LLM_TIMEOUT) -> str:
    """
    直接调用 OpenRouter REST API（moonshotai/kimi-k2.6）。
    返回模型的纯文字输出。失败时抛出 RuntimeError。
    """
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY 未设置")

    payload = {
        "model": _KIMI_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": _KIMI_MAX_TOKENS,
    }

    try:
        resp = _requests.post(
            _OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
    except _requests.exceptions.Timeout:
        raise RuntimeError(f"Kimi API timeout after {timeout}s")
    except _requests.exceptions.RequestException as e:
        raise RuntimeError(f"Kimi API request error: {e}")

    if resp.status_code != 200:
        raise RuntimeError(f"Kimi API HTTP {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    choices = data.get("choices", [])
    if not choices:
        raise RuntimeError(f"Kimi API 返回空 choices: {json.dumps(data)[:300]}")

    msg = choices[0].get("message", {})
    content = msg.get("content") or ""
    # Kimi K2.6 是推理模型，content 有时为 null（max_tokens 被 reasoning 耗尽）
    # 此时降级到 reasoning 字段的最后部分（一般包含最终输出）
    if not content.strip():
        reasoning = msg.get("reasoning") or ""
        if reasoning:
            logger.warning("[llm_runner] Kimi content 为空，降级使用 reasoning 末段")
            content = reasoning[-8000:]  # 取最后 8000 字，通常是最终答案
        else:
            raise RuntimeError("Kimi 返回 content 和 reasoning 均为空")

    return content.strip()


def _extract_json(text: str) -> dict | list:
    """
    从输出中提取 JSON。
    优先匹配 ```json 代码块，其次尝试直接 json.loads。
    """
    import re

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


def prompt_hash(prompt: str) -> str:
    return hashlib.md5(prompt.encode()).hexdigest()[:12]


def run_dimension_analysis(
    project_name: str,
    dimension: str,
    dimension_prompt_template: str,
    report_batches: list[str],
    progress_cb=None,
) -> list[dict]:
    """并发处理一个维度的多个研报批次，返回各批次分析结果列表。"""

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
        _log(f"[llm] {dimension} batch {idx+1}/{len(report_batches)} 开始...")
        raw = _call_llm(prompt)
        parsed = _extract_json(raw)
        _log(f"[llm] {dimension} batch {idx+1}/{len(report_batches)} 完成")
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
                _log(f"[llm] {dimension} batch {i+1} 失败: {e}")
                results[i] = {"error": str(e)}

    return [r for r in results if r is not None]


def run_merge_analysis(
    project_name: str,
    dimension: str,
    batch_results: list[dict],
    merge_prompt_template: str,
    progress_cb=None,
) -> dict:
    """多批次时用 LLM 合并汇总；单批次直接返回。"""
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
    _log(f"[llm] {dimension} 合并 {len(batch_results)} 个批次...")
    raw = _call_llm(prompt)
    return _extract_json(raw)

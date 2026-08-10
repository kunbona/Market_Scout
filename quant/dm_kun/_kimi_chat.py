"""[AI-GENERATED] Kimi（Moonshot）OpenAI 兼容 chat 调用助手。

替代原 Anthropic SDK 调用，供 tools/ 下脚本共用（惯例同 _kernel_activate.py）。

环境变量（.env 配置，dm exec 自动注入）：
    KIMI_API_KEY     Kimi 开放平台 API Key（https://platform.moonshot.cn/）
                     也兼容读 MOONSHOT_API_KEY
    KIMI_BASE_URL    可选，默认 https://api.moonshot.cn/v1
    KIMI_MODEL       可选，默认 kimi-k2-0905-preview
"""

from __future__ import annotations

import os

import httpx

DEFAULT_BASE_URL = "https://api.moonshot.cn/v1"
DEFAULT_MODEL = "kimi-k2-0905-preview"


def kimi_api_key() -> str | None:
    """读取 Kimi API Key（KIMI_API_KEY 优先，兼容 MOONSHOT_API_KEY）。"""
    return os.environ.get("KIMI_API_KEY") or os.environ.get("MOONSHOT_API_KEY")


def require_kimi_key() -> str:
    """取 key 或抛出带配置指引的异常。"""
    key = kimi_api_key()
    if not key:
        raise RuntimeError(
            "请设置环境变量 KIMI_API_KEY\n"
            "   获取地址: https://platform.moonshot.cn/console/api-keys\n"
            "   在 .env 中添加: KIMI_API_KEY=你的key"
        )
    return key


def chat(
    system: str,
    user: str,
    *,
    model: str | None = None,
    max_tokens: int = 4096,
    temperature: float | None = None,
    json_mode: bool = False,
    timeout: float = 120,
) -> str:
    """单次 chat 调用，返回助手文本内容。

    json_mode=True 时启用 response_format=json_object（prompt 中需含 JSON 说明）。
    temperature 缺省不发送（部分模型如 kimi-for-coding 仅允许 temperature=1）。
    """
    key = require_kimi_key()
    base_url = os.environ.get("KIMI_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    payload: dict = {
        "model": model or os.environ.get("KIMI_MODEL", DEFAULT_MODEL),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    resp = httpx.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json=payload,
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    choice = data["choices"][0]
    content = choice["message"].get("content") or ""
    if not content.strip():
        # thinking 模型的 reasoning_content 也计入 completion tokens：
        # max_tokens 被思考耗尽时 content 为空，抛带诊断信息的异常
        raise RuntimeError(
            f"Kimi 返回空内容（finish_reason={choice.get('finish_reason')}, "
            f"usage={data.get('usage')}）—— thinking 可能耗尽 max_tokens，请调大"
        )
    return content

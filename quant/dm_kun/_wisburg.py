"""[AI-GENERATED] 智堡 API 通用客户端 — 覆盖 open-docs 全部 9 个资源端点。

文档: https://open-docs.wisburg.com/docs/getting-started/first-call
鉴权: Authorization: Bearer <WISBURG_API_KEY>（.env 配置，dm exec 自动注入）

端点总览（列表参数完全一致：first/after/query/startTime/endTime）：
    资源              列表                      详情
    feed              GET /api/feed             —（资讯流无详情页）
    articles          GET /api/articles         GET /api/articles/:id        （body 为 HTML）
    market-daily      GET /api/market-daily     —（文档未给详情）
    reports           GET /api/reports          GET /api/reports/:id         （summary=markdown）
    archives          GET /api/archives         GET /api/archives/:id        （url+summary+meta）
    company-reports   GET /api/company-reports  GET /api/company-reports/:id （url+summary+meta）
    earningscalls     GET /api/earningscalls    GET /api/earningscalls/:id   （url+summary）
    images            GET /api/images           —（图片流无详情页）
    am-reports        GET /api/am-reports       GET /api/am-reports/:id      （url+summary+meta）
"""

from __future__ import annotations

import os
from typing import Any

import httpx

WISBURG_BASE = "https://api-omen.wisburg.com"

# 全部资源（列表接口）
RESOURCES = (
    "feed",
    "articles",
    "market-daily",
    "reports",
    "archives",
    "company-reports",
    "earningscalls",
    "images",
    "am-reports",
)

# 有详情接口（GET /api/<resource>/:id）的资源
DETAIL_RESOURCES = (
    "articles",
    "reports",
    "archives",
    "company-reports",
    "earningscalls",
    "am-reports",
)


def wisburg_api_key() -> str | None:
    return os.environ.get("WISBURG_API_KEY")


def require_wisburg_key() -> str:
    key = wisburg_api_key()
    if not key:
        raise RuntimeError(
            "请设置环境变量 WISBURG_API_KEY\n"
            "   获取地址: https://www.wisburg.com/user/developer?tab=apikeys\n"
            "   在 .env 中添加: WISBURG_API_KEY=你的key"
        )
    return key


def _check(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("status") != 0:
        raise RuntimeError(f"智堡 API 错误: {data.get('message', '未知错误')}")
    return data["data"]


def list_items(
    resource: str,
    *,
    first: int = 20,
    query: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    after: str | None = None,
    timeout: float = 30,
) -> tuple[list[dict[str, Any]], str | None]:
    """拉取任一资源列表，返回 (items, 下一页游标)。

    query 为空时按时间倒序，有关键词时按相关性排序。
    """
    if resource not in RESOURCES:
        raise ValueError(f"未知资源 {resource!r}，可选: {', '.join(RESOURCES)}")
    params: dict[str, Any] = {"first": min(first, 100)}
    if query:
        params["query"] = query
    if start_time:
        params["startTime"] = start_time
    if end_time:
        params["endTime"] = end_time
    if after:
        params["after"] = after

    resp = httpx.get(
        f"{WISBURG_BASE}/api/{resource}",
        params=params,
        headers={"Authorization": f"Bearer {require_wisburg_key()}"},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = _check(resp.json())
    items = data["items"]
    cursor = data.get("page_info", {}).get("end_cursor")
    return items, cursor


def get_detail(resource: str, item_id: int, *, timeout: float = 30) -> dict[str, Any]:
    """拉取单条详情（reports/archives/company-reports/earningscalls/articles/am-reports）。"""
    if resource not in DETAIL_RESOURCES:
        raise ValueError(f"资源 {resource!r} 无详情接口，可选: {', '.join(DETAIL_RESOURCES)}")
    resp = httpx.get(
        f"{WISBURG_BASE}/api/{resource}/{item_id}",
        headers={"Authorization": f"Bearer {require_wisburg_key()}"},
        timeout=timeout,
    )
    resp.raise_for_status()
    return _check(resp.json())

"""
通用反爬工具：UA 轮换、headers 构造、带退避重试的 session 工厂。
所有 fetcher 通过此模块获取 session/headers，避免固定指纹被识别。
"""
import random
import time
import logging
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# ── User-Agent 池 ─────────────────────────────────────────────────────────────
_UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]

# 各域名专属 session（复用 TCP 连接，减少握手开销和被识别风险）
_sessions: dict[str, requests.Session] = {}


def random_ua() -> str:
    return random.choice(_UA_POOL)


def make_headers(referer: str = "", extra: dict | None = None) -> dict:
    """生成带随机 UA 的完整 headers，模拟真实浏览器请求特征。"""
    h = {
        "User-Agent": random_ua(),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    if referer:
        h["Referer"] = referer
    if extra:
        h.update(extra)
    return h


def get_session(domain: str = "default") -> requests.Session:
    """
    按域名获取复用 session，内置 urllib3 级别的重试（网络错误自动重连）。
    注意：urllib3 重试只处理连接层错误，业务层重试仍需调用方自己实现。
    """
    if domain not in _sessions:
        s = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=1.0,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
        )
        adapter = HTTPAdapter(max_retries=retry)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        _sessions[domain] = s
    return _sessions[domain]


def jitter_sleep(base: float = 1.0, jitter: float = 2.0) -> None:
    """随机延迟，base ~ base+jitter 秒，避免固定频率被识别。"""
    time.sleep(base + random.uniform(0, jitter))


def fetch_with_retry(url: str, *, domain: str = "default", referer: str = "",
                     params: dict | None = None, extra_headers: dict | None = None,
                     retries: int = 3, base_delay: float = 1.5,
                     timeout: int = 15) -> requests.Response:
    """
    带随机 UA + 退避重试的统一 GET 请求入口。
    首次请求不延迟，重试时指数退避 + 随机抖动。
    """
    session = get_session(domain)
    headers = make_headers(referer=referer, extra=extra_headers)
    last_exc = None
    for i in range(retries):
        if i > 0:
            delay = base_delay * (2 ** (i - 1)) + random.uniform(0.5, 2.0)
            time.sleep(delay)
            # 重试时换一个 UA
            headers["User-Agent"] = random_ua()
        try:
            resp = session.get(url, params=params, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp
        except Exception as e:
            last_exc = e
            logger.debug("[http_util] %s retry %d/%d: %s", url[:60], i + 1, retries, e)
    raise last_exc

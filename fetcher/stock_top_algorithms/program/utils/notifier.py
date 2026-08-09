"""企业微信群机器人推送。

借鉴 rocket_2025/Fuel/common.py 的 send_message 方案，封装两种推送：
  - send_message(text)  发送文本摘要
  - send_image(path)    发送图片（周期定位图等）

webhook key 从 config.WECOM_ROBOT_KEYS 读取（建议用环境变量 WECOM_ROBOT_KEY 注入，
不要把真实 key 写进仓库）。未配置 key 时不会抛错，只打印提示并返回 False，
这样推送失败不会影响 auto_runner 主流程。

robot_type:
  'info'  常规消息推送
  'warn'  异常告警推送
"""
import base64
import hashlib
import json
from pathlib import Path
from typing import Union

try:
    import requests
except ImportError:  # pragma: no cover - requests 缺失时优雅降级
    requests = None

from config import NOTIFY_PROXIES, WECOM_ROBOT_KEYS

_WEBHOOK_URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key="
# 企业微信图片消息上限 2MB（base64 前的原图字节数）
_MAX_IMAGE_BYTES = 2 * 1024 * 1024


def _resolve_key(robot_type: str) -> str:
    """取出对应机器人的 webhook key，未配置时返回空串。"""
    key = (WECOM_ROBOT_KEYS or {}).get(robot_type, "")
    if not key:
        # 退回到 info，方便只配了一个机器人的场景
        key = (WECOM_ROBOT_KEYS or {}).get("info", "")
    return (key or "").strip()


def _post(payload: dict, robot_type: str) -> bool:
    if requests is None:
        print("[notifier] 未安装 requests，无法推送")
        return False
    key = _resolve_key(robot_type)
    if not key:
        print("[notifier] 未配置企业微信 webhook key（设置环境变量 WECOM_ROBOT_KEY），跳过推送")
        return False
    headers = {"Content-Type": "application/json;charset=utf-8"}
    try:
        resp = requests.post(
            _WEBHOOK_URL + key,
            data=json.dumps(payload),
            headers=headers,
            timeout=10,
            proxies=NOTIFY_PROXIES,
        )
        result = resp.json()
        if result.get("errcode", 0) != 0:
            print(f"[notifier] 企业微信返回错误: {result}")
            return False
        return True
    except Exception as err:  # pragma: no cover - 网络异常
        print(f"[notifier] 推送失败: {err}")
        return False


def send_message(content: str, robot_type: str = "info") -> bool:
    """发送文本消息到企业微信机器人。"""
    print(content)
    payload = {"msgtype": "text", "text": {"content": content}}
    return _post(payload, robot_type)


def send_image(image_path: Union[str, Path], robot_type: str = "info") -> bool:
    """发送图片到企业微信机器人（按官方 image 消息：base64 + md5）。"""
    path = Path(image_path)
    if not path.exists():
        print(f"[notifier] 图片不存在，跳过: {path}")
        return False
    data = path.read_bytes()
    if len(data) > _MAX_IMAGE_BYTES:
        print(f"[notifier] 图片 {len(data) // 1024}KB 超过 2MB 上限，跳过: {path.name}")
        return False
    payload = {
        "msgtype": "image",
        "image": {
            "base64": base64.b64encode(data).decode("utf-8"),
            "md5": hashlib.md5(data).hexdigest(),
        },
    }
    return _post(payload, robot_type)

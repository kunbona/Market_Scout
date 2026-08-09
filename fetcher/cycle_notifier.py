"""
市场周期企微推送 (cycle 专用 wrapper, 内部走通用 fetcher.wecom_notifier)

历史: 这文件原本是 cycle 推送的实现 + 通用 WECOM_DRY_RUN 安全机制。
      现在通用部分已经抽到 fetcher/wecom_notifier.py, 本文件变成 cycle 专用
      包装层, 保持原 API 接口 (push_text / push_image / push_cycle_result /
      push_cycle_from_disk / is_configured) 不破坏现有调用方。

      实际推送逻辑全部走 wecom_notifier, 享受通用多地址支持。
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Union

# 触发 self_runner.install() (跟其他 cycle_* 文件一致)
from fetcher.cycle_self_runner import install, DATA_ROOT, ALGO_DIR  # noqa: F401

logger = logging.getLogger(__name__)


# ==================== Re-exports from wecom_notifier ====================
# 让 cycle 老调用方继续 work, 但底层是通用 notifier
from fetcher.wecom_notifier import (  # noqa: F401
    is_dry_run,
    is_configured,
    list_addresses,
    send_text as push_text,
    send_image as push_image,
    send_cycle_result,
)


# ==================== 兼容老 API (单 address "info") ====================
def push_cycle_result(
    report: Dict[str, Any],
    image_path: Optional[Union[str, Path]] = None,
    address: str = "info",
) -> Dict[str, Any]:
    """推 cycle 报告 — 现在支持多地址 (address 参数)."""
    from fetcher.wecom_notifier import send_cycle_result as _send
    return _send(report, image_path=image_path, address=address)


def push_cycle_from_disk(address: str = "info") -> Dict[str, Any]:
    """从自管 disk 读 cycle_signal_latest.json + cycle_position.png, 推企微。"""
    from fetcher.wecom_notifier import send_cycle_result as _send
    json_path = DATA_ROOT / "results" / "cycle_signal_latest.json"
    pic_path = DATA_ROOT / "pic" / "cycle_position.png"
    if not json_path.exists():
        return {
            "configured": is_configured(address),
            "dry_run": is_dry_run(),
            "text_ok": False,
            "image_ok": False,
            "skipped_reason": f"找不到 {json_path}",
        }
    try:
        report = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {
            "configured": is_configured(address),
            "dry_run": is_dry_run(),
            "text_ok": False,
            "image_ok": False,
            "skipped_reason": f"读 JSON 失败: {e}",
        }
    return _send(report, image_path=pic_path, address=address)


if __name__ == "__main__":
    # CLI 仍然支持 (兼容老使用方式)
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--address", default="info", help="推送地址")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force-send", action="store_true")
    args = p.parse_args()

    if args.dry_run:
        import os as _os
        _os.environ["WECOM_DRY_RUN"] = "1"
    elif args.force_send:
        import os as _os
        _os.environ["WECOM_DRY_RUN"] = "0"

    print(f"沙盒模式: {is_dry_run()}")
    print(f"可用地址: {list_addresses()}")
    result = push_cycle_from_disk(address=args.address)
    print(f"\n推送结果: {result}")

"""dm_kun 脚本统一的数据路径管理。

所有 dm_kun 脚本通过 `from ._paths import require_quant_data_root` 获取 QUANT_DATA_ROOT。
env 缺失或路径不存在时直接 raise，**不再回落硬编码路径**——
历史教训：硬编码 fallback 会让 plist/env 注入失败时静默读到旧/错的库，
改了 .env.local 之后还以为是新库，实际跑的还是老数据（典型 silent drift bug）。
"""
from __future__ import annotations

import os
from pathlib import Path


def require_quant_data_root() -> str:
    """读 QUANT_DATA_ROOT 环境变量。

    - 缺失 → raise FileNotFoundError
    - 路径不存在 → raise FileNotFoundError
    - 正常 → 返回绝对路径字符串（供字符串拼接或 Path() 构造）
    """
    root = os.environ.get("QUANT_DATA_ROOT", "").strip()
    if not root:
        raise FileNotFoundError(
            "QUANT_DATA_ROOT 环境变量未设置，请检查 .env.local 或 launchd plist 注入"
        )
    p = Path(root)
    if not p.exists():
        raise FileNotFoundError(f"QUANT_DATA_ROOT 指向路径不存在: {root}")
    return root

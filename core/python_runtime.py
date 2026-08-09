"""Helpers for reusing the current Python interpreter across subprocesses."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def get_python_executable() -> str:
    """Return the Python executable that child processes should reuse.

    PYTHON_EXECUTABLE 配置项可能残留其他机器的路径（如从 Windows 迁移过来的
    .env.local），不存在时回落到当前解释器，避免子进程启动失败。
    """
    configured = os.environ.get("PYTHON_EXECUTABLE", "").strip()
    if configured and Path(configured).is_file():
        return configured
    return sys.executable

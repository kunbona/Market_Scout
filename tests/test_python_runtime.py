import os
import sys
import unittest
from unittest import mock

from core.python_runtime import get_python_executable


class PythonRuntimeTests(unittest.TestCase):
    def test_prefers_explicit_python_executable_env_when_path_exists(self) -> None:
        # PYTHON_EXECUTABLE 指向真实存在的解释器时优先使用
        with mock.patch.dict(os.environ, {"PYTHON_EXECUTABLE": sys.executable}, clear=False):
            self.assertEqual(get_python_executable(), sys.executable)

    def test_falls_back_when_configured_path_is_stale(self) -> None:
        # 实现语义 (docstring): 配置可能是别的机器残留 (如 Windows 迁移的 .env.local),
        # 路径不存在时回落到当前解释器, 避免子进程启动失败
        with mock.patch.dict(os.environ, {"PYTHON_EXECUTABLE": r"D:\Miniconda3\envs\mr\python.exe"}, clear=False):
            self.assertEqual(get_python_executable(), sys.executable)

    def test_falls_back_to_current_interpreter(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_python_executable(), sys.executable)


if __name__ == "__main__":
    unittest.main()

import os
import sys
import unittest
from unittest import mock

from core.python_runtime import get_python_executable


class PythonRuntimeTests(unittest.TestCase):
    def test_prefers_explicit_python_executable_env(self) -> None:
        with mock.patch.dict(os.environ, {"PYTHON_EXECUTABLE": r"D:\Miniconda3\envs\mr\python.exe"}, clear=False):
            self.assertEqual(get_python_executable(), r"D:\Miniconda3\envs\mr\python.exe")

    def test_falls_back_to_current_interpreter(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_python_executable(), sys.executable)


if __name__ == "__main__":
    unittest.main()

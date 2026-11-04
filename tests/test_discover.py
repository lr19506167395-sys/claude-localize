"""进程检测的回归测试。

历史 bug：用 ``pgrep -x Claude`` 判断 Claude 是否在运行，但 macOS 的
``comm`` 回的是完整路径 ``/Applications/Claude.app/Contents/MacOS/Claude``，
精确匹配永远失败。后果不是报错，而是**静默跳过**「退出 Claude」——
磁盘上的 asar 被换掉，运行中的进程却还抱着内存里的旧版本。
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from helpers import ROOT  # noqa: F401

from claude_localize import discover

PS_OUTPUT = """\
  67333 /Applications/Claude.app/Contents/MacOS/Claude
  67337 /Applications/Claude.app/Contents/Frameworks/Claude Helper.app/Contents/MacOS/Claude Helper
  67339 /Applications/Claude.app/Contents/Frameworks/Claude Helper.app/Contents/MacOS/Claude Helper
  12345 /usr/bin/some-other
"""


class ProcessDetectionTests(unittest.TestCase):
    def setUp(self):
        self.inst = discover.ClaudeInstall(Path("/Applications/Claude.app"), "4.4212.0", "en-US")

    def _run_with(self, stdout: str):
        return mock.Mock(returncode=0, stdout=stdout, stderr="")

    def test_finds_main_process_only(self):
        """Helper 子进程不能被当成主进程。"""
        with mock.patch.object(discover.subprocess, "run", return_value=self._run_with(PS_OUTPUT)):
            self.assertEqual(self.inst.main_pids(), [67333])
            self.assertTrue(self.inst.is_running())

    def test_not_running_when_no_main_process(self):
        only_helpers = "\n".join(
            line for line in PS_OUTPUT.splitlines() if "MacOS/Claude " in line
        )
        with mock.patch.object(discover.subprocess, "run", return_value=self._run_with(only_helpers)):
            self.assertEqual(self.inst.main_pids(), [])
            self.assertFalse(self.inst.is_running())

    def test_ps_failure_counts_as_not_running(self):
        with mock.patch.object(discover.subprocess, "run", side_effect=OSError("no ps")):
            self.assertFalse(self.inst.is_running())

    def test_matches_custom_app_path(self):
        """同一个 .app 换了个安装路径，仍要能认出来。"""
        other = discover.ClaudeInstall(Path("/Volumes/Test/Claude.app"), "4.4212.0", "en-US")
        out = "  999 /Volumes/Test/Claude.app/Contents/MacOS/Claude\n"
        with mock.patch.object(discover.subprocess, "run", return_value=self._run_with(out)):
            self.assertEqual(other.main_pids(), [999])

    def test_ignores_a_different_claude_app(self):
        """机器上还有别的 Claude 副本时，不能误判成本体在运行。"""
        out = "  999 /Applications/Claude Beta.app/Contents/MacOS/Claude\n"
        with mock.patch.object(discover.subprocess, "run", return_value=self._run_with(out)):
            self.assertEqual(self.inst.main_pids(), [])


if __name__ == "__main__":
    unittest.main()

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

# 真实现场：汉化 app 自己在跑时，ps 的 comm 列里带中文路径。
# GUI 进程的 locale 是 ASCII，旧实现用 text=True 解码，直接抛
# UnicodeDecodeError，菜单栏「安装补丁」在第一步 is_running() 就崩了。
PS_OUTPUT_WITH_CJK = """\
  50837 /Applications/Claude汉化.app/Contents/MacOS/Claude汉化
  67333 /Applications/Claude.app/Contents/MacOS/Claude
  12345 /usr/bin/some-other
"""


class ProcessDetectionTests(unittest.TestCase):
    def setUp(self):
        self.inst = discover.ClaudeInstall(Path("/Applications/Claude.app"), "4.4212.0", "en-US")

    def _run_with(self, stdout: str):
        # main_pids 现在拿 bytes 自己按 UTF-8 解码（text=True 在 GUI 的
        # ASCII locale 下会崩），mock 也要按真实形态给 bytes。
        return mock.Mock(returncode=0, stdout=stdout.encode("utf-8"), stderr=b"")

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

    def test_non_ascii_process_path_does_not_crash(self):
        """回归：ps 输出里带中文路径时（汉化 app 自己在跑），必须正常解析。

        旧实现用 text=True，按 locale 解码；py2app 从 Finder 启动的进程
        locale 是 ASCII，于是抛
        ``'ascii' codec can't decode byte 0xe6 ... in position 45763``，
        菜单栏「安装补丁」在 is_running() 第一步就失败。
        """
        with mock.patch.object(
            discover.subprocess, "run", return_value=self._run_with(PS_OUTPUT_WITH_CJK)
        ):
            self.assertEqual(self.inst.main_pids(), [67333])
            self.assertTrue(self.inst.is_running())

    def test_undecodable_bytes_are_replaced_not_raised(self):
        """真出现坏字节时也不能抛——进程名解错一行，好过整个安装崩掉。"""
        broken = mock.Mock(
            returncode=0,
            stdout=b"  1 /Applications/Claude.app/Contents/MacOS/Claude\n  2 /\xff\xfe\n",
            stderr=b"",
        )
        with mock.patch.object(discover.subprocess, "run", return_value=broken):
            self.assertEqual(self.inst.main_pids(), [1])

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

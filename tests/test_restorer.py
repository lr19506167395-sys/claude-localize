from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import make_app

from claude_localize import backup, discover, integrity, restorer, signer


class RestoreTests(unittest.TestCase):
    def test_install_prerestore_keeps_backup(self):
        """安装前还原也必须走不消耗备份的路径（曾是第二条危险入口）。"""
        import inspect

        from claude_localize import installer

        source = inspect.getsource(installer.install)
        self.assertIn("restore_backup", source)
        self.assertNotIn("b.rename(inst.app)", source)
        self.assertNotIn("b.rename(app)", source)

    def test_strip_patch_preflights_itself(self):
        """strip_patch 是公开函数，必须自己预检——不能指望调用方记得。"""
        from claude_localize import installer

        with mock.patch.object(integrity, "probe_layout", return_value=["no slot"]), \
                mock.patch.object(installer, "shell") as shell_mod, \
                mock.patch.object(installer.frontend_bundle, "AppAsarTarget") as target:
            shell_mod.has_artifacts.return_value = True
            self.app.joinpath("Contents", "Resources", "app.asar").write_bytes(b"x")
            with self.assertRaises(SystemExit):
                installer.strip_patch(self.app, "zh-CN", log=lambda *a: None)
            target.assert_not_called()

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.base = Path(self._td.name)
        self.app = make_app(self.base)
        self.inst = discover.ClaudeInstall(self.app, "4.29.0", "zh-CN")
        self.inst.write_locale = mock.Mock()
        self.inst.launch = mock.Mock()
        self.inst.is_running = mock.Mock(return_value=False)
        self.patches = [
            mock.patch.object(discover, "detect", return_value=self.inst),
            mock.patch.object(discover, "is_patched", return_value=True),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self._td.cleanup()

    def _backup(self):
        b = self.base / "Claude.backup-before-zh-test.app"
        b.mkdir()
        (b / "Contents" / "Resources").mkdir(parents=True)
        (b / "Contents" / "Frameworks").mkdir()
        (b / "Contents" / "Info.plist").write_bytes(
            (self.app / "Contents" / "Info.plist").read_bytes()
        )
        (b / "Contents" / "Resources" / "app.asar").write_bytes(b"")
        backup.mark_pristine(b)
        return b

    def test_pristine_backup_is_not_consumed(self):
        b = self._backup()
        writes = []

        def finish(app, prepared, fallback):
            self.assertEqual(fallback, b)
            self.assertTrue(b.exists())
            self.assertFalse((prepared / backup.PRISTINE_MARKER).exists())
            self.assertFalse(backup._marker_path(prepared).exists())
            self.assertTrue(app.exists())

        with mock.patch.object(backup, "swap_in", side_effect=finish), \
                mock.patch.object(integrity, "verify_app", return_value=[]), \
                mock.patch.object(signer, "team_identifier", return_value="Q6L2SF6YDW"), \
                mock.patch.object(signer, "verify", return_value=(True, "")):
            self.assertEqual(restorer.restore(), "restored")
        self.assertTrue(b.exists())
        self.inst.write_locale.assert_called_once_with("en-US")

    def test_failed_verification_does_not_touch_app_or_backup(self):
        b = self._backup()
        with mock.patch.object(integrity, "verify_app", return_value=["bad slot"]), \
                mock.patch.object(backup, "swap_in") as swap:
            with self.assertRaises(SystemExit):
                restorer.restore()
        swap.assert_not_called()
        self.assertTrue(self.app.exists())
        self.assertTrue(b.exists())
        self.inst.write_locale.assert_not_called()

    def test_no_backup_never_strips_live_app(self):
        with mock.patch.object(backup, "best_backup", return_value=None), \
                mock.patch("claude_localize.installer.strip_patch") as strip:
            with self.assertRaises(SystemExit) as cm:
                restorer.restore()
        strip.assert_not_called()
        self.assertIn("拒绝直接修改", str(cm.exception))

    def test_non_pristine_backup_uses_temporary_strip(self):
        b = self._backup()
        backup._drop_pristine_marker(b)
        with mock.patch.object(backup, "is_pristine", return_value=False), \
                mock.patch.object(integrity, "probe_layout", return_value=[]), \
                mock.patch.object(integrity, "verify_app", return_value=[]), \
                mock.patch.object(signer, "verify", return_value=(True, "")), \
                mock.patch("claude_localize.installer.strip_patch") as strip, \
                mock.patch("claude_localize.native_strings.remove"), \
                mock.patch.object(backup, "swap_in") as swap:
            self.assertEqual(restorer.restore(), "restored")
        strip.assert_called_once()
        work = strip.call_args.args[0]
        self.assertNotEqual(work, self.app)
        self.assertNotEqual(work, b)
        self.assertTrue(b.exists())

    def test_strip_failure_aborts_before_swap(self):
        b = self._backup()
        backup._drop_pristine_marker(b)
        with mock.patch.object(backup, "is_pristine", return_value=False), \
                mock.patch.object(integrity, "probe_layout", return_value=[]), \
                mock.patch("claude_localize.installer.strip_patch", side_effect=SystemExit("bad")), \
                mock.patch.object(backup, "swap_in") as swap:
            with self.assertRaises(SystemExit):
                restorer.restore()
        swap.assert_not_called()
        self.assertTrue(b.exists())
        self.inst.write_locale.assert_not_called()


if __name__ == "__main__":
    unittest.main()

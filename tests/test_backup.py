from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from helpers import make_app

from claude_localize import backup, integrity, signer


def _touch_backup(base: Path, stamp: str, version: str, complete: bool = True) -> Path:
    app = make_app(base, version=version, name=f"Claude.backup-before-zh-{stamp}.app")
    if not complete:
        (app / "Contents" / "Frameworks").rmdir()
        (app / "Contents" / "Resources" / "app.asar").unlink()
    return app


class ListTests(unittest.TestCase):
    def test_sorted_by_mtime_not_name(self):
        """「zh-…」字典序在「zh-CN-…」之前，按名字排序会颠倒新旧。"""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            app = make_app(base)
            old = _touch_backup(base, "zh-CN-20260101-000000", "4.20.0")
            new = _touch_backup(base, "zh-20260102-000000", "4.29.0")
            os.utime(old, (1000, 1000))
            os.utime(new, (2000, 2000))
            self.assertEqual(backup.list_backups(app), [old, new])

    def test_completeness(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            make_app(base)
            good = _touch_backup(base, "zh-1", "4.29.0")
            bad = _touch_backup(base, "zh-2", "4.29.0", complete=False)
            self.assertTrue(backup.is_complete_backup(good))
            self.assertFalse(backup.is_complete_backup(bad))


class LatestTests(unittest.TestCase):
    def test_picks_highest_version(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            app = make_app(base)
            _touch_backup(base, "zh-1", "4.20.0")
            newer = _touch_backup(base, "zh-2", "4.29.0")
            self.assertEqual(backup.latest_backup(app), newer)

    def test_same_version_prefers_newest_mtime(self):
        """同名版本时不能按文件名挑：「zh-…」字典序在「zh-CN-…」之前。"""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            app = make_app(base)
            old = _touch_backup(base, "zh-CN-20261028-222406", "4.29.0")
            new = _touch_backup(base, "zh-20261029-000716", "4.29.0")
            os.utime(old, (1000, 1000))
            os.utime(new, (2000, 2000))
            self.assertEqual(backup.latest_backup(app), new)

    def test_skips_incomplete(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            app = make_app(base)
            good = _touch_backup(base, "zh-1", "4.20.0")
            _touch_backup(base, "zh-2", "4.29.0", complete=False)
            self.assertEqual(backup.latest_backup(app), good)

    def test_none_when_empty(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(Path(td))
            self.assertIsNone(backup.latest_backup(app))


class PruneTests(unittest.TestCase):
    def test_keeps_newest_and_removes_broken(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            app = make_app(base)
            old = _touch_backup(base, "zh-1", "4.20.0")
            broken = _touch_backup(base, "zh-2", "4.25.0", complete=False)
            newest = _touch_backup(base, "zh-3", "4.29.0")
            for p, t in ((old, 1000), (broken, 2000), (newest, 3000)):
                os.utime(p, (t, t))

            backup.prune_old_backups(app, keep=1)
            self.assertFalse(old.exists())
            self.assertFalse(broken.exists())
            self.assertTrue(newest.exists())

    def test_never_deletes_latest_even_if_older_mtime(self):
        """备份被手动搬动过、mtime 变旧时，也不能删掉它——否则无法还原。"""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            app = make_app(base)
            latest = _touch_backup(base, "zh-1", "4.29.0")
            other = _touch_backup(base, "zh-2", "4.20.0")
            os.utime(latest, (1000, 1000))
            os.utime(other, (3000, 3000))

            backup.prune_old_backups(app, keep=1)
            self.assertTrue(latest.exists())
            self.assertFalse(other.exists())

    def test_removes_backups_of_other_versions(self):
        """旧版本备份必须删：既不能还原（会降级），也不能拿来重打补丁。"""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            app = make_app(base, version="4.4212.0")
            stale = _touch_backup(base, "zh-1", "4.29.0")
            backup.mark_pristine(stale)
            current = _touch_backup(base, "zh-2", "4.4212.0")
            os.utime(stale, (3000, 3000))     # 比当前还新，也不能留

            backup.prune_old_backups(app, keep=1)
            self.assertFalse(stale.exists(), "旧版本原版备份不该永久保留")
            self.assertTrue(current.exists())

    def test_does_not_wipe_everything_when_app_is_missing(self):
        """安装中途 app 被改名时，版本号会读成 "0"，绝不能因此把所有备份删光。"""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            app = make_app(base, version="4.4212.0")
            a = _touch_backup(base, "zh-1", "4.29.0")
            b = _touch_backup(base, "zh-2", "4.4212.0")
            app.rename(base / "Claude.app.moved")

            backup.prune_old_backups(app, keep=1)
            self.assertTrue(a.exists() or b.exists(), "至少得留下能恢复 Claude 的备份")

    def test_does_not_wipe_everything_when_version_unreadable(self):
        """plist 在但读不出版本时，不能把所有备份判成版本不符全删。

        读不出来 != 版本是 0。若把 "0" 当基准，所有备份都对不上，会被删光。
        """
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            app = make_app(base, version="4.4212.0")
            (app / "Contents" / "Info.plist").write_text(
                '<?xml version="1.0"?><plist version="1.0"><dict></dict></plist>',
                encoding="utf-8",
            )
            a = _touch_backup(base, "zh-1", "4.29.0")
            b = _touch_backup(base, "zh-2", "4.4212.0")
            self.assertIsNone(backup._current_key(app))

            backup.prune_old_backups(app, keep=1)
            self.assertTrue(backup.list_backups(app), "不能把备份删光")
            self.assertEqual(backup.best_backup(app), b)   # 退回版本最高的那份
            self.assertTrue(a.exists() or b.exists())

    def test_best_backup_falls_back_when_version_unreadable(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            app = make_app(base, version="4.4212.0")
            (app / "Contents" / "Info.plist").write_text(
                '<?xml version="1.0"?><plist version="1.0"><dict></dict></plist>',
                encoding="utf-8",
            )
            old = _touch_backup(base, "zh-1", "4.29.0")
            new = _touch_backup(base, "zh-2", "4.4212.0")
            self.assertEqual(backup.best_backup(app), new)
            self.assertIsNot(backup.best_backup(app), old)

class PruneSafetyTests(unittest.TestCase):
    """只留「能救回 Claude」的备份；任何输入组合都不能删光。"""

    def test_keeps_complete_backup_with_unreadable_version(self):
        """备份自己的版本读不出来时，不能当成版本不符删掉。

        PlistBuddy 失败时把报错文本写进 stdout，旧实现把它当版本号，
        解析成一个天文数字；这里同时验证它不会再污染排序。
        """
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            app = make_app(base, version="4.4212.0")
            broken = _touch_backup(base, "zh-1", "")      # 无版本号
            (broken / "Contents" / "Info.plist").write_text(
                '<?xml version="1.0"?><plist version="1.0"><dict></dict></plist>',
                encoding="utf-8",
            )
            self.assertTrue(backup.is_complete_backup(broken))
            self.assertEqual(backup.bundle_version(broken), "0")

            backup.prune_old_backups(app, keep=1)
            self.assertTrue(broken.exists(), "唯一可恢复的备份不能被删")

    def test_bundle_version_never_returns_error_text(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(Path(td), version="4.4212.0")
            (app / "Contents" / "Info.plist").write_text(
                '<?xml version="1.0"?><plist version="1.0"><dict></dict></plist>',
                encoding="utf-8",
            )
            version = backup.bundle_version(app)
            self.assertEqual(version, "0")
            self.assertEqual(backup.version_key(version), (0,))


class SwapInTests(unittest.TestCase):
    """rename 失败时必须把旧版本顶回去，且备份不能因此消失。"""

    def _make(self, base: Path, name: str, tag: str) -> Path:
        app = base / name
        (app / "Contents").mkdir(parents=True)
        (app / "Contents" / "Info.plist").write_text(f"<plist><dict/><!--{tag}--></plist>")
        return app

    def test_keeps_only_incomplete_backup(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            app = make_app(base)
            broken = _touch_backup(base, "zh-1", "4.29.0", complete=False)
            backup.prune_old_backups(app, keep=1)
            self.assertTrue(broken.exists(), "唯一不完整副本也不能自动删除")

    def test_restores_old_app_when_swap_fails(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            app = self._make(base, "Claude.app", "LIVE")
            patched = self._make(base, "patched.app", "PATCHED")
            b = backup.create_backup(app, pristine=True)

            real = Path.rename

            def flaky(self, target):
                if self.name == "patched.app" and Path(target).name == "Claude.app":
                    raise OSError("模拟改名失败")
                return real(self, target)

            with mock.patch.object(Path, "rename", flaky):
                with self.assertRaises(OSError):
                    backup.swap_in(app, patched, b)

            self.assertTrue(app.exists(), "Claude.app 不能凭空消失")
            self.assertIn("LIVE", (app / "Contents" / "Info.plist").read_text())
            self.assertTrue(b.exists(), "回滚不能把备份消耗掉")

    def test_restores_from_fallback_when_trash_restore_fails(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            app = self._make(base, "Claude.app", "LIVE")
            patched = self._make(base, "patched.app", "PATCHED")
            fallback = self._make(base, "fallback.app", "FALLBACK")
            real = Path.rename

            def flaky(self, target):
                if Path(target).name == "Claude.app" and self.name in ("patched.app", "Claude.app.replaced"):
                    raise OSError("模拟改名失败")
                return real(self, target)

            with mock.patch.object(Path, "rename", flaky):
                with self.assertRaises(OSError):
                    backup.swap_in(app, patched, fallback)
            self.assertTrue(app.exists())
            self.assertIn("FALLBACK", (app / "Contents" / "Info.plist").read_text())
            self.assertTrue(fallback.exists())

    def test_normal_swap_replaces_app(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            app = self._make(base, "Claude.app", "LIVE")
            patched = self._make(base, "patched.app", "PATCHED")
            b = backup.create_backup(app, pristine=True)

            backup.swap_in(app, patched, b)
            self.assertTrue(app.exists())
            self.assertIn("PATCHED", (app / "Contents" / "Info.plist").read_text())
            self.assertTrue(b.exists())


class PristineTests(unittest.TestCase):
    """备份性质：官方原版 vs 已汉化。"""

    def test_create_backup_marks_pristine(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            app = make_app(base)
            b = backup.create_backup(app, pristine=True)
            self.assertTrue(backup.is_pristine(b))
            self.assertEqual(backup.backup_kind(app), "pristine")

    def test_create_backup_without_mark(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            app = make_app(base)
            b = backup.create_backup(app, pristine=False)
            self.assertFalse(backup.is_pristine(b))
            self.assertEqual(backup.backup_kind(app), "patched")

    def test_best_backup_ignores_pristine_of_other_version(self):
        """回归：Claude 自动更新后，旧版本原版备份不能压住新版本的干净备份。

        实测现场：4.29.0 的原版备份带着 pristine 标记，4.4212.0 的干净备份
        没有标记。旧实现只判「是不是原版」，永远选中 4.29 那份，
        还原被降级保护拦下、重装补丁也报错——用户明明有可用备份却用不了。
        """
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            app = make_app(base, version="4.4212.0")
            stale = _touch_backup(base, "zh-1", "4.29.0")
            backup.mark_pristine(stale)
            current = _touch_backup(base, "zh-2", "4.4212.0")
            self.assertEqual(backup.best_backup(app), current)

    def test_best_backup_none_when_only_other_versions(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            app = make_app(base, version="4.4212.0")
            _touch_backup(base, "zh-1", "4.29.0")
            self.assertIsNone(backup.best_backup(app))

    def test_best_backup_prefers_pristine_within_same_version(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            app = make_app(base, version="4.29.0")
            dirty = _touch_backup(base, "zh-1", "4.29.0")
            clean = _touch_backup(base, "zh-2", "4.29.0")
            backup.mark_pristine(clean)
            os.utime(dirty, (3000, 3000))      # 已汉化的那份更新
            os.utime(clean, (1000, 1000))
            self.assertEqual(backup.best_backup(app), clean)

    def test_prune_never_deletes_pristine(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            app = make_app(base)
            clean = _touch_backup(base, "zh-1", "4.29.0")
            backup.mark_pristine(clean)
            for i, stamp in enumerate(("zh-2", "zh-3", "zh-4"), start=2):
                p = _touch_backup(base, stamp, "4.29.0")
                os.utime(p, (1000 * i, 1000 * i))
            os.utime(clean, (10, 10))     # 最旧
            backup.prune_old_backups(app, keep=1)
            self.assertTrue(clean.exists(), "原版备份不能被删")

    def test_restore_backup_drops_marker(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            app = make_app(base)
            b = backup.create_backup(app, pristine=True)
            self.assertTrue(backup.is_pristine(b))
            with mock.patch.object(integrity, "verify_app", return_value=[]), \
                    mock.patch.object(signer, "verify", return_value=(True, "")):
                backup.restore_backup(app, b)
            self.assertFalse((app / backup.PRISTINE_MARKER).exists(), "标记不能带进 Claude.app")
            self.assertTrue(b.exists(), "换回后原备份仍保留")
            self.assertTrue(backup.is_pristine(b), "原备份的标记也不能被消费")

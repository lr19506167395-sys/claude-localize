from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from helpers import make_app

from claude_localize import backup


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


if __name__ == "__main__":
    unittest.main()

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

    def test_best_backup_prefers_pristine(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            app = make_app(base)
            dirty = _touch_backup(base, "zh-1", "4.29.0")
            clean = _touch_backup(base, "zh-2", "4.20.0")   # 版本更低
            backup.mark_pristine(clean)
            os.utime(dirty, (3000, 3000))
            os.utime(clean, (1000, 1000))
            self.assertEqual(backup.best_backup(app), clean)
            self.assertEqual(backup.latest_backup(app), dirty)

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
            backup.restore_backup(app, b)
            self.assertFalse((app / backup.PRISTINE_MARKER).exists(), "标记不能带进 Claude.app")

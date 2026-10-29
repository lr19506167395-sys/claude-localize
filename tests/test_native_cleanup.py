from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from helpers import make_app

from claude_localize import cleanup, native_strings


class NativeStringsTests(unittest.TestCase):
    def test_install_and_remove(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(Path(td))
            written = native_strings.install(app, log=lambda *a: None)
            self.assertEqual(set(written), set(native_strings.LPROJ_NAMES))
            self.assertEqual(
                set(native_strings.installed(app)), set(native_strings.LPROJ_NAMES)
            )

            removed = native_strings.remove(app, log=lambda *a: None)
            self.assertEqual(set(removed), set(native_strings.LPROJ_NAMES))
            self.assertEqual(native_strings.installed(app), [])
            # 目录也要清掉
            self.assertFalse((app / "Contents/Resources/zh-Hans.lproj").exists())

    def test_remove_leaves_english_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(Path(td))
            en = app / "Contents/Resources/en.lproj"
            en.mkdir()
            (en / "Localizable.strings").write_bytes('"Cancel" = "Cancel";'.encode("utf-16-le"))
            native_strings.remove(app, log=lambda *a: None)
            self.assertTrue((en / "Localizable.strings").exists())

    def test_pack_file_is_chinese_utf16(self):
        pack = native_strings.pack_file()
        self.assertTrue(pack.exists(), "随包 Localizable.strings 缺失")
        text = pack.read_bytes().decode("utf-16-le")
        self.assertIn("取消", text)


class CleanupTests(unittest.TestCase):
    def _fake_asar_layout(self, app: Path) -> None:
        res = app / "Contents" / "Resources"
        (res / "ion-dist.asar").write_bytes(b"")
        (res / "zh-CN.json").write_text("{}", encoding="utf-8")
        (res / "ion-dist").mkdir()          # 与 asar 冲突的中断残留
        (res / ".app_asar_work").mkdir()

    def test_scan_finds_residue(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(Path(td))
            self._fake_asar_layout(app)
            kinds = {r.kind for r in cleanup.scan(app)}
            self.assertIn("dead-locale", kinds)
            self.assertIn("work-dir", kinds)

    def test_clean_removes_them(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(Path(td))
            self._fake_asar_layout(app)
            cleanup.clean(app, log=lambda *a: None)
            res = app / "Contents" / "Resources"
            self.assertFalse((res / "zh-CN.json").exists())
            self.assertFalse((res / "ion-dist").exists())
            self.assertFalse((res / ".app_asar_work").exists())
            self.assertEqual(cleanup.scan(app), [])

    def test_clean_keeps_legit_ion_dist_in_old_layout(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(Path(td))
            res = app / "Contents" / "Resources"
            (res / "ion-dist" / "i18n").mkdir(parents=True)
            (res / "en-US.json").write_text("{}", encoding="utf-8")
            cleanup.clean(app, log=lambda *a: None)
            self.assertTrue((res / "ion-dist").exists())
            self.assertTrue((res / "en-US.json").exists())

    def test_dry_run_changes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(Path(td))
            self._fake_asar_layout(app)
            items = cleanup.clean(app, dry_run=True, log=lambda *a: None)
            self.assertTrue(items)
            self.assertFalse(any(i.removed for i in items))
            self.assertTrue((app / "Contents/Resources/zh-CN.json").exists())


class ResignTests(unittest.TestCase):
    """删除包内文件会让签名失效——必须重新签名，否则 Claude 启动即 SIGTRAP。"""

    def _fake_asar_layout(self, app: Path) -> None:
        res = app / "Contents" / "Resources"
        (res / "ion-dist.asar").write_bytes(b"")
        (res / "zh-CN.json").write_text("{}", encoding="utf-8")

    def test_touched_bundle_true_for_inner_file(self):
        with tempfile.TemporaryDirectory() as td:
            app = make_app(Path(td))
            self._fake_asar_layout(app)
            items = cleanup.clean(app, log=lambda *a: None)
            self.assertTrue(cleanup.touched_bundle(items, app))

    def test_backup_outside_bundle_not_counted(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            app = make_app(base)
            broken = make_app(base, name="Claude.backup-before-zh-x.app")
            (broken / "Contents" / "Frameworks").rmdir()
            items = cleanup.clean(app, log=lambda *a: None)
            self.assertTrue(items)
            self.assertFalse(cleanup.touched_bundle(items, app))

    def test_clean_and_resign_resigns_when_needed(self):
        from unittest import mock

        from claude_localize import signer

        with tempfile.TemporaryDirectory() as td:
            app = make_app(Path(td))
            self._fake_asar_layout(app)
            with mock.patch.object(signer, "resign", return_value={"signed": 1}) as r, \
                 mock.patch.object(signer, "verify", return_value=(True, "")), \
                 mock.patch.object(signer, "clear_quarantine"):
                cleanup.clean_and_resign(app, log=lambda *a: None)
            r.assert_called_once()

    def test_clean_and_resign_skips_when_only_backups(self):
        from unittest import mock

        from claude_localize import signer

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            app = make_app(base)
            broken = make_app(base, name="Claude.backup-before-zh-x.app")
            (broken / "Contents" / "Frameworks").rmdir()
            with mock.patch.object(signer, "resign", return_value={"signed": 1}) as r:
                cleanup.clean_and_resign(app, log=lambda *a: None)
            r.assert_not_called()


if __name__ == "__main__":
    unittest.main()
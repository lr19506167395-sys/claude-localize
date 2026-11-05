"""词表版本记录的读写（含打包兼容性回归）。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from claude_localize import paths, upstream


class LocalVersionTests(unittest.TestCase):
    def _packs(self, td: str) -> Path:
        d = Path(td) / "packs" / paths.TARGET_LOCALE
        d.mkdir(parents=True)
        return d

    def test_reads_release_json(self):
        with tempfile.TemporaryDirectory() as td:
            packs = self._packs(td)
            (packs / "release.json").write_text(
                '{"repo": "x", "release": "1.4.21"}', encoding="utf-8"
            )
            with mock.patch.object(upstream, "_pack_dir", return_value=packs):
                self.assertEqual(upstream.local_version(), "1.4.21")

    def test_reads_legacy_dotfile(self):
        """旧版写的是 .upstream-version，升级后必须还能读出来。"""
        with tempfile.TemporaryDirectory() as td:
            packs = self._packs(td)
            (packs / ".upstream-version").write_text("9.9.9\n", encoding="utf-8")
            with mock.patch.object(upstream, "_pack_dir", return_value=packs):
                self.assertEqual(upstream.local_version(), "9.9.9")

    def test_release_json_wins_over_legacy(self):
        with tempfile.TemporaryDirectory() as td:
            packs = self._packs(td)
            (packs / ".upstream-version").write_text("9.9.9\n", encoding="utf-8")
            (packs / "release.json").write_text('{"release": "1.4.21"}', encoding="utf-8")
            with mock.patch.object(upstream, "_pack_dir", return_value=packs):
                self.assertEqual(upstream.local_version(), "1.4.21")

    def test_corrupt_json_is_not_returned_as_text(self):
        """坏 JSON 不能让状态栏显示 `{bad` 这种垃圾值。"""
        with tempfile.TemporaryDirectory() as td:
            packs = self._packs(td)
            (packs / "release.json").write_text("{bad", encoding="utf-8")
            with mock.patch.object(upstream, "_pack_dir", return_value=packs):
                self.assertIsNone(upstream.local_version())

    def test_plain_text_still_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            packs = self._packs(td)
            (packs / "release.json").write_text("2.0.0\n", encoding="utf-8")
            with mock.patch.object(upstream, "_pack_dir", return_value=packs):
                self.assertEqual(upstream.local_version(), "2.0.0")

    def test_missing_everything(self):
        with tempfile.TemporaryDirectory() as td:
            packs = self._packs(td)
            with mock.patch.object(upstream, "_pack_dir", return_value=packs):
                self.assertIsNone(upstream.local_version())


class PackagingTests(unittest.TestCase):
    def test_version_file_is_not_a_dotfile(self):
        """打包工具（pipx/py2app）默认跳过点开头文件。

        `.upstream-version` 时代装出来的包永远显示「词表版本: 未记录」，
        因为文件根本没被打进去。
        """
        self.assertFalse(upstream.VERSION_LOCAL_FILE.startswith("."))

    def test_pull_writes_outside_the_app_bundle(self):
        """拉取必须写用户目录，不能写进 app 包内部。

        包内的 packs/ 在代码签名封条范围内，写一个字就让
        ``codesign --verify`` 报 "a sealed resource is missing or invalid"；
        源码运行时写到仓库里，下次 build 还会被旧词表盖掉。
        """
        write_dir = paths.pack_write_dir()
        self.assertEqual(write_dir.parent.parent, paths.USER_DIR)
        self.assertNotIn("Contents/Resources", str(write_dir))

    def test_pack_dir_falls_back_to_bundle_copy(self):
        """用户目录没有词表时，读的应该是随包那份。"""
        with mock.patch.object(paths, "USER_PACKS", Path("/nonexistent/packs")):
            self.assertEqual(
                paths.pack_dir(),
                paths.PROJECT_ROOT / "packs" / paths.TARGET_LOCALE,
            )

    def test_pack_dir_prefers_user_copy(self):
        """用户目录有词表时以它为准，这样拉取结果能一直生效。"""
        with tempfile.TemporaryDirectory() as td:
            user = Path(td)
            pack = user / paths.TARGET_LOCALE
            pack.mkdir(parents=True)
            (pack / "frontend.json").write_text("{}", encoding="utf-8")
            with mock.patch.object(paths, "USER_PACKS", user):
                self.assertEqual(paths.pack_dir(), pack)

    def test_repo_ships_release_json(self):
        pack = paths.PROJECT_ROOT / "packs" / paths.TARGET_LOCALE / "release.json"
        self.assertTrue(pack.is_file(), f"缺少 {pack}")
        data = json.loads(pack.read_text(encoding="utf-8"))
        self.assertTrue(data.get("release"))


if __name__ == "__main__":
    unittest.main()

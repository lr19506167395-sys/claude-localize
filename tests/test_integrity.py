"""完整性链的静态检查：结构不对时必须拒绝，而不是静默放行。

背景：摘要槽没同步时 Electron 直接 SIGTRAP，没有任何可读错误。
所以「找不到槽」绝不能当成「校验通过」——Electron 收紧校验的特征
恰恰是 sentinel 或槽布局变了。
"""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from helpers import ROOT  # noqa: F401

from claude_localize import integrity

PLIST_HEAD = b'<?xml version="1.0" encoding="UTF-8"?>'


def _plist(body: bytes) -> bytes:
    return PLIST_HEAD + b'<plist version="1.0"><dict>' + body + b"</dict></plist>"


def _dict_entry(rel: str, value: str = "aa") -> bytes:
    return (
        b"<key>" + rel.encode() + b"</key><dict>"
        b"<key>hash</key><string>" + value.encode() + b"</string>"
        b"<key>algorithm</key><string>SHA256</string></dict>"
    )


def _with_integrity(*entries: bytes) -> bytes:
    """把若干条 asar 条目包进 ElectronAsarIntegrity 字典。"""
    return _plist(
        b"<key>ElectronAsarIntegrity</key><dict>" + b"".join(entries) + b"</dict>"
    )


def _full_plist() -> bytes:
    return _with_integrity(
        _dict_entry("Resources/app.asar"), _dict_entry("Resources/ion-dist.asar")
    )


class ProbeLayoutTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        base = Path(self._td.name)
        self.app = base / "Claude.app"
        (self.app / "Contents" / "Resources").mkdir(parents=True)
        (self.app / "Contents" / "Resources" / "app.asar").write_bytes(b"")
        (self.app / "Contents" / "Resources" / "ion-dist.asar").write_bytes(b"")
        self.framework = self.app / integrity.ELECTRON_FRAMEWORK_REL
        self.framework.parent.mkdir(parents=True)
        self.plist = self.app / "Contents" / "Info.plist"

    def tearDown(self):
        self._td.cleanup()

    def _write_slot(self, used: int) -> None:
        self.framework.write_bytes(
            integrity.INTEGRITY_DIGEST_SENTINEL + bytes([used, 0]) + b"\x00" * 32
        )

    def test_ok_when_structure_matches(self):
        self.plist.write_bytes(_full_plist())
        self._write_slot(used=1)
        self.assertEqual(integrity.probe_layout(self.app), [])

    def test_rejects_missing_dictionary(self):
        self.plist.write_bytes(_plist(b""))
        self._write_slot(used=1)
        problems = integrity.probe_layout(self.app)
        self.assertTrue(any("ElectronAsarIntegrity" in p for p in problems), problems)

    def test_rejects_asar_not_covered(self):
        self.plist.write_bytes(_with_integrity(_dict_entry("Resources/app.asar")))
        self._write_slot(used=1)
        problems = integrity.probe_layout(self.app)
        self.assertTrue(any("ion-dist.asar" in p for p in problems), problems)

    def test_rejects_missing_sentinel(self):
        """Electron 换了 sentinel：改 asar 前必须拒绝，不能等启动崩。"""
        self.plist.write_bytes(_full_plist())
        self.framework.write_bytes(b"no sentinel")
        problems = integrity.probe_layout(self.app)
        self.assertTrue(any("sentinel" in p for p in problems), problems)

    def test_rejects_all_slots_unused(self):
        """used=0 时 sync_digest_slot 什么都不写，改 asar 必然崩。"""
        self.plist.write_bytes(_full_plist())
        self._write_slot(used=0)
        problems = integrity.probe_layout(self.app)
        self.assertTrue(any("未启用" in p for p in problems), problems)


class VerifyAppTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.app = Path(self._td.name) / "Claude.app"
        (self.app / "Contents" / "Resources").mkdir(parents=True)

    def tearDown(self):
        self._td.cleanup()

    def test_missing_dictionary_is_a_problem_not_pass(self):
        """原先返回 [] 会被还原流程当成「完整性没问题」。"""
        (self.app / "Contents" / "Info.plist").write_bytes(_plist(b""))
        self.assertTrue(integrity.verify_app(self.app))


class BlockIntegrityTests(unittest.TestCase):
    def test_first_bad_block_detected(self):
        body = b"x" * 10
        good = integrity.compute_integrity(body, block_size=4)
        self.assertIsNone(integrity._first_bad_block(body, good))

        broken = integrity.compute_integrity(b"y" * 10, block_size=4)
        self.assertEqual(integrity._first_bad_block(body, broken), 0)

    def test_block_count_mismatch_detected(self):
        """文件长度变了但 blocks 没重算，也要算不符。"""
        integrity_info = integrity.compute_integrity(b"x" * 4, block_size=4)
        self.assertIsNotNone(integrity._first_bad_block(b"x" * 8, integrity_info))


if __name__ == "__main__":
    unittest.main()

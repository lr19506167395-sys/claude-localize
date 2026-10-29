from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from helpers import ROOT  # noqa: F401

from claude_localize import asar, frontend_bundle


class HeaderTests(unittest.TestCase):
    def test_roundtrip(self):
        header = {"files": {"a.txt": {"size": 3, "offset": "0"}}}
        import json

        s = json.dumps(header, separators=(",", ":"))
        blob, _off = asar.header_area(s)
        _hs, back, parsed = asar.read_header(blob + b"\x00" * 8)
        self.assertEqual(back, s)
        self.assertEqual(parsed, header)

    def test_align4(self):
        for n in (0, 1, 3, 4, 5, 8):
            self.assertEqual(asar.align4(n) % 4, 0)
            self.assertGreaterEqual(asar.align4(n), n)


class BuildExtractTests(unittest.TestCase):
    def _make_source(self, root: Path) -> None:
        (root / "i18n").mkdir(parents=True)
        (root / "i18n" / "en-US.json").write_text('{"a":"Hello"}', encoding="utf-8")
        (root / "assets" / "v1").mkdir(parents=True)
        (root / "assets" / "v1" / "index-abc.js").write_text("console.log(1)", encoding="utf-8")
        # 两个内容完全相同的文件（asar 里会共享同一段数据）
        (root / "assets" / "v1" / "same1.bin").write_bytes(b"X" * 4096)
        (root / "assets" / "v1" / "same2.bin").write_bytes(b"X" * 4096)
        # unpacked 条目
        (root / "native.node").write_bytes(b"\x7fELF")

    def test_build_extract_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            src = base / "src"
            self._make_source(src)
            out = base / "ion-dist.asar"
            frontend_bundle.build(src, out, unpacked={"native.node"})

            hs, _s, header = asar.read_header_file(out)
            entries = {p: e for p, e in asar.walk(header)}
            self.assertIn("i18n/en-US.json", entries)
            self.assertTrue(entries["native.node"].get("unpacked"))
            # 同内容共享 offset
            self.assertEqual(entries["assets/v1/same1.bin"]["offset"],
                             entries["assets/v1/same2.bin"]["offset"])
            # 每个非 unpacked 条目都有正确的 integrity
            data, _hs, _h = asar.load(out)
            for p, e in entries.items():
                if e.get("unpacked"):
                    continue
                body = asar.read_file(data, hs, e)
                self.assertEqual(e["integrity"]["hash"], hashlib.sha256(body).hexdigest(), p)

            dest = base / "back"
            unpacked = frontend_bundle.extract(out, dest)
            self.assertEqual(unpacked, {"native.node"})
            self.assertEqual((dest / "i18n" / "en-US.json").read_text(), '{"a":"Hello"}')
            self.assertEqual((dest / "assets/v1/same2.bin").read_bytes(), b"X" * 4096)

    def test_read_entry_file_only_reads_slice(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            src = base / "src"
            self._make_source(src)
            out = base / "x.asar"
            frontend_bundle.build(src, out)
            self.assertEqual(
                asar.read_entry_file(out, "i18n/en-US.json"), b'{"a":"Hello"}'
            )
            self.assertIsNone(asar.read_entry_file(out, "nope"))
            self.assertTrue(asar.has_entry(out, "assets/v1/index-abc.js"))

    def test_verify_structure_rejects_missing_index(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            src = base / "src"
            (src / "i18n").mkdir(parents=True)
            (src / "i18n" / "en-US.json").write_text("{}", encoding="utf-8")
            out = base / "x.asar"
            frontend_bundle.build(src, out)
            with self.assertRaises(SystemExit):
                frontend_bundle.verify_structure(out)


if __name__ == "__main__":
    unittest.main()
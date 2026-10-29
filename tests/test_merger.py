from __future__ import annotations

import unittest

from helpers import ROOT  # noqa: F401

from claude_localize import merger


EN = {"a": "Hello", "b": "World", "c": "Cancel", "d": "Bye"}


class PriorityTests(unittest.TestCase):
    def test_locked_beats_everything(self):
        custom = {"a": merger.CustomEntry("锁定值", locked=True)}
        out, _st, origin = merger.merge(
            EN, {"a": "词表值"}, official={"a": "官方值"}, custom=custom
        )
        self.assertEqual(out["a"], "锁定值")
        self.assertEqual(origin["a"], "locked")

    def test_official_beats_pack(self):
        out, _st, origin = merger.merge(EN, {"a": "词表值"}, official={"a": "官方值"})
        self.assertEqual(out["a"], "官方值")
        self.assertEqual(origin["a"], "official")

    def test_pack_beats_custom(self):
        custom = {"a": merger.CustomEntry("自有值", locked=False)}
        out, _st, origin = merger.merge(EN, {"a": "词表值"}, custom=custom)
        self.assertEqual(out["a"], "词表值")
        self.assertEqual(origin["a"], "pack")

    def test_custom_when_nothing_else(self):
        custom = {"a": merger.CustomEntry("自有值", locked=False)}
        out, _st, origin = merger.merge(EN, {}, custom=custom)
        self.assertEqual(out["a"], "自有值")
        self.assertEqual(origin["a"], "custom")

    def test_english_fallback(self):
        out, _st, origin = merger.merge(EN, {})
        self.assertEqual(out["a"], "Hello")
        self.assertEqual(origin["a"], "english")

    def test_new_key_in_new_version_falls_back_not_break(self):
        # 新版多出来的 key 必须在结果里（用英文），否则界面会缺文本
        out, st, _ = merger.merge(EN, {"a": "你好"})
        self.assertEqual(set(out), set(EN))
        self.assertEqual(out["b"], "World")
        self.assertEqual(st.total, len(EN))


class ValidationTests(unittest.TestCase):
    def test_placeholder_mismatch_rejected_and_downgraded(self):
        out, st, origin = merger.merge(
            {"a": "Hello {name}"}, {"a": "你好"}, custom={"a": merger.CustomEntry("你好 {name}")}
        )
        self.assertEqual(out["a"], "你好 {name}")
        self.assertEqual(origin["a"], "custom")
        self.assertTrue(any(r[1] == "pack" for r in st.rejected))

    def test_official_untranslated_falls_through(self):
        out, _st, origin = merger.merge(EN, {"a": "词表值"}, official={"a": "Hello"})
        self.assertEqual(out["a"], "词表值")
        self.assertEqual(origin["a"], "pack")


class CustomFileTests(unittest.TestCase):
    def test_load_accepts_string_and_object(self):
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "c.json"
            p.write_text(
                json.dumps({"x": "甲", "y": {"value": "乙", "locked": True}}),
                encoding="utf-8",
            )
            entries = merger.load_custom(p)
            self.assertFalse(entries["x"].locked)
            self.assertTrue(entries["y"].locked)

    def test_load_missing_file(self):
        from pathlib import Path

        self.assertEqual(merger.load_custom(Path("/nonexistent/x.json")), {})


if __name__ == "__main__":
    unittest.main()
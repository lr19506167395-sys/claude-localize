from __future__ import annotations

import unittest

from helpers import ROOT  # noqa: F401  (负责把项目根目录加进 sys.path)

from claude_localize import validator


class PlaceholderTests(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(
            validator.placeholders("Hello {name}"), validator.placeholders("你好 {name}")
        )

    def test_nested_icu_counts_outer_only(self):
        en = "{count, plural, one {# item} other {# items}}"
        zh = "{count, plural, other {# 项}}"
        self.assertEqual(validator.placeholders(en), validator.placeholders(zh))
        self.assertEqual(validator.placeholders(en)["count"], 1)

    def test_mismatch_detected(self):
        self.assertNotEqual(
            validator.placeholders("{a} and {b}"), validator.placeholders("只有 {a}")
        )


class IcuTests(unittest.TestCase):
    def test_plural_branch_simplification_allowed(self):
        en = "{n, plural, one {# file} other {# files}}"
        zh = "{n, plural, other {# 个文件}}"
        self.assertEqual(validator.validate_entry(en, zh), [])

    def test_semantic_branch_loss_detected(self):
        en = "{n, plural, =0 {none} one {one} other {many}}"
        zh = "{n, plural, other {很多}}"
        self.assertIn("ICU 结构不匹配", validator.validate_entry(en, zh))


class TagTests(unittest.TestCase):
    def test_tags_must_match(self):
        self.assertEqual(
            validator.validate_entry("Click <b>here</b>", "点击<b>这里</b>"), []
        )
        self.assertIn(
            "HTML 标签不匹配", validator.validate_entry("Click <b>here</b>", "点击这里")
        )

    def test_newlines_must_match(self):
        self.assertIn(
            "换行数不一致", validator.validate_entry("a\nb", "甲乙")
        )


class UntranslatedTests(unittest.TestCase):
    def test_empty(self):
        self.assertTrue(validator.is_untranslated("", "Hello"))

    def test_identical(self):
        self.assertTrue(validator.is_untranslated("Hello", "Hello"))

    def test_identical_but_keep_english(self):
        self.assertFalse(validator.is_untranslated("Claude", "Claude"))

    def test_real_translation(self):
        self.assertFalse(validator.is_untranslated("你好", "Hello"))


if __name__ == "__main__":
    unittest.main()
from __future__ import annotations

import unittest

from helpers import ROOT  # noqa: F401

from claude_localize import frontend_locale as locale


class WhitelistTests(unittest.TestCase):
    LIST = 'var L=["en-US","de-DE","fr-FR","ko-KR","ja-JP"];'

    def test_register(self):
        out, n = locale.patch_whitelist(self.LIST, "zh-CN")
        self.assertEqual(n, 1)
        self.assertIn('"zh-CN"', out)

    def test_idempotent(self):
        once, _ = locale.patch_whitelist(self.LIST, "zh-CN")
        twice, n = locale.patch_whitelist(once, "zh-CN")
        self.assertEqual(n, 0)
        self.assertEqual(once, twice)

    def test_extract(self):
        self.assertEqual(
            locale.extract_lang_list(self.LIST),
            ["en-US", "de-DE", "fr-FR", "ko-KR", "ja-JP"],
        )

    def test_is_already_patched(self):
        patched, _ = locale.patch_whitelist(self.LIST, "zh-CN")
        self.assertTrue(locale.is_already_patched(patched, "zh-CN"))
        self.assertFalse(locale.is_already_patched(self.LIST, "zh-CN"))


class DisplayNameTests(unittest.TestCase):
    def test_append_once(self):
        out, changed = locale.patch_display_names("var a=1;")
        self.assertTrue(changed)
        again, changed2 = locale.patch_display_names(out)
        self.assertFalse(changed2)
        self.assertEqual(out, again)

    def test_skips_when_foreign_patch_present(self):
        text = "var a=1;/*__claudeZhLabelPatch*/"
        out, changed = locale.patch_display_names(text)
        self.assertFalse(changed)
        self.assertEqual(out, text)


class HardcodedTests(unittest.TestCase):
    RULES = [
        ["Attach a file", "添加文件"],
        ["Settings", "设置"],
        ["Settings and more", "设置及其他"],
        ["foo", "bar"],
    ]

    def test_replaces_quoted_literals(self):
        # 纯标识符（Settings）默认被 skip_structural 过滤，不会替换
        rules = [["Attach a file", "添加文件"], ["Settings and more", "设置及其他"]]
        text = 'x="Attach a file";y=\'Settings\';z="Settings and more";'
        out, n = locale.HardcodedReplacer(rules).apply(text)
        self.assertEqual(n, 2)
        self.assertIn('"添加文件"', out)
        self.assertIn("'Settings'", out)
        self.assertIn('"设置及其他"', out)

    def test_does_not_touch_code_identifiers(self):
        # 只有字符串字面量会被替换，属性名/变量名不动
        text = "obj.Settings = Settings;"
        out, n = locale.HardcodedReplacer(self.RULES).apply(text)
        self.assertEqual(n, 0)
        self.assertEqual(out, text)

    def test_escaped_quotes_survive(self):
        # 规则里的原文按 JS 源码原样写（含反斜杠转义），整串匹配
        text = r'msg = "He said \"Settings\" loudly";'
        rules = [[r'He said \"Settings\" loudly', "他大声说设置"]]
        out, n = locale.HardcodedReplacer(rules, skip_structural=False).apply(text)
        self.assertEqual(n, 1)
        self.assertIn("他大声说设置", out)
        self.assertTrue(out.startswith('msg = "'))

    def test_structural_identifiers_skipped(self):
        replacer = locale.HardcodedReplacer([["Settings", "设置"]])
        # 纯标识符规则默认被过滤掉
        out, n = replacer.apply('"Settings"')
        self.assertEqual(n, 0)

    def test_empty_literal_map(self):
        out, n = locale.HardcodedReplacer([]).apply('"x"')
        self.assertEqual((out, n), ('"x"', 0))

    def test_no_false_positive_on_longer_text(self):
        # 只做整串匹配，不做部分替换
        out, n = locale.HardcodedReplacer([["Settings", "设置"]]).apply('"Settings page"')
        self.assertEqual(n, 0)
        self.assertEqual(out, '"Settings page"')


if __name__ == "__main__":
    unittest.main()
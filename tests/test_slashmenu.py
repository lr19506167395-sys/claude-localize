"""斜杠命令双语菜单：正向打补丁 + 精确撤回。"""
from __future__ import annotations

import unittest

from helpers import ROOT  # noqa: F401

from claude_localize import slashmenu

# 用真实 profile 的锚点形状，造一个最小可匹配的文件
SAMPLE = (
    'export {x as SlashCommandMenuPopupBody};\n'
    'var a=s("div",{children:s(T,{text:i.label,query:j,'
    'highlightClassName:"font-semibold"})});'
    'if(i.type==="skill"){q(a)}\n'
)

LABELS = {"help": "帮助", "plugin": "管理插件"}


class SlashMenuTests(unittest.TestCase):
    def setUp(self):
        self.profile = slashmenu.load_profile()
        self.assertTrue(self.profile, "缺少 packs/zh-CN/slash-menu-patch.json")

    def test_apply_adds_gloss_and_marker(self):
        out, status = slashmenu.apply_text(SAMPLE, self.profile, LABELS)
        self.assertEqual(status, "patched")
        self.assertIn("__claudeZhSlashGloss(", out)
        self.assertTrue(slashmenu.has_marker(out))
        self.assertIn('"plugin":"管理插件"', out)

    def test_apply_is_idempotent(self):
        once, _ = slashmenu.apply_text(SAMPLE, self.profile, LABELS)
        twice, status = slashmenu.apply_text(once, self.profile, LABELS)
        self.assertEqual(status, "already")
        self.assertEqual(once, twice)

    def test_revert_restores_original_exactly(self):
        patched, _ = slashmenu.apply_text(SAMPLE, self.profile, LABELS)
        back, changed = slashmenu.revert_text(patched, self.profile)
        self.assertTrue(changed)
        self.assertEqual(back, SAMPLE)

    def test_revert_without_patch_is_noop(self):
        back, changed = slashmenu.revert_text(SAMPLE, self.profile)
        self.assertFalse(changed)
        self.assertEqual(back, SAMPLE)

    def test_apply_skips_when_anchor_missing(self):
        out, status = slashmenu.apply_text("var x=1;", self.profile, LABELS)
        self.assertEqual(status, "skip")
        self.assertEqual(out, "var x=1;")

    def test_apply_skips_when_no_labels(self):
        out, status = slashmenu.apply_text(SAMPLE, self.profile, {})
        self.assertEqual(status, "skip")
        self.assertEqual(out, SAMPLE)

    def test_labels_catalog(self):
        labels = slashmenu.load_labels("zh-CN")
        self.assertGreater(len(labels), 20)
        self.assertEqual(labels.get("help"), "帮助与支持")


if __name__ == "__main__":
    unittest.main()
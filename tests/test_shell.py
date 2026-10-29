from __future__ import annotations

import unittest

from helpers import ROOT  # noqa: F401

from claude_localize import shell

# 与 Claude 4.29 主进程 bundle 里完全一致的片段
REAL = (
    'var x=1;zCt={en:"en-US",de:"de-DE",fr:"fr-FR",ko:"ko-KR",ja:"ja-JP",'
    '"es-419":"es-419",es:"es-ES",it:"it-IT",hi:"hi-IN",pt:"pt-BR",id:"id-ID"};'
    'var qCt=new Set(Object.values(zCt));'
)


class WhitelistTests(unittest.TestCase):
    def test_patch_inserts_locale(self):
        out, n = shell.patch_locale_whitelist(REAL, "zh-CN")
        self.assertEqual(n, 1)
        self.assertIn('"zh-CN":"zh-CN"', out)
        # 原有内容一字不动
        self.assertIn('id:"id-ID"', out)
        self.assertIn("var qCt=new Set", out)

    def test_idempotent(self):
        once, _ = shell.patch_locale_whitelist(REAL, "zh-CN")
        twice, n = shell.patch_locale_whitelist(once, "zh-CN")
        self.assertEqual(n, 0)
        self.assertEqual(once, twice)

    def test_is_whitelisted(self):
        self.assertFalse(shell.is_whitelisted(REAL, "zh-CN"))
        patched, _ = shell.patch_locale_whitelist(REAL, "zh-CN")
        self.assertTrue(shell.is_whitelisted(patched, "zh-CN"))
        self.assertTrue(shell.is_whitelisted(REAL, "ja-JP"))

    def test_no_whitelist_untouched(self):
        text = "var a={foo:1};"
        out, n = shell.patch_locale_whitelist(text, "zh-CN")
        self.assertEqual(n, 0)
        self.assertEqual(out, text)

    def test_patched_object_still_valid_js_object(self):
        patched, _ = shell.patch_locale_whitelist(REAL, "zh-CN")
        body = shell.LOCALE_MAP_RE.search(patched).group(0)
        self.assertTrue(body.startswith("{"))
        self.assertTrue(body.endswith("}"))
        self.assertEqual(body.count("{"), body.count("}"))


class UnpatchTests(unittest.TestCase):
    def test_roundtrip_restores_original(self):
        patched, n = shell.patch_locale_whitelist(REAL, "zh-CN")
        self.assertEqual(n, 1)
        back, removed = shell.unpatch_locale_whitelist(patched, "zh-CN")
        self.assertEqual(removed, 1)
        self.assertEqual(back, REAL)

    def test_unpatch_idempotent(self):
        back, removed = shell.unpatch_locale_whitelist(REAL, "zh-CN")
        self.assertEqual(removed, 0)
        self.assertEqual(back, REAL)

    def test_unpatch_only_touches_our_locale(self):
        text = 'z={en:"en-US","zh-Hans":"zh-Hans"};'
        out, removed = shell.unpatch_locale_whitelist(text, "zh-CN")
        self.assertEqual(removed, 0)
        self.assertEqual(out, text)


if __name__ == "__main__":
    unittest.main()
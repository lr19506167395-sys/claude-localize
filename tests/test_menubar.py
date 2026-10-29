"""菜单栏逻辑的回归测试。

用替身模块模拟 rumps 的两条关键语义：

* ``MenuItem`` 继承自 ``OrderedDict``，**不可哈希**（真实 rumps 就是这样）
* ``set_callback(None)`` 会禁用项，``set_callback(cb)`` 恢复

历史 bug：把 MenuItem 当 dict 的键 → TypeError 被菜单回调吞掉 → 点菜单没反应。
"""
from __future__ import annotations

import sys
import types
import unittest
from unittest import mock

from helpers import ROOT  # noqa: F401


def _install_rumps_stub() -> types.ModuleType:
    rumps = types.ModuleType("rumps")

    class MenuItem(dict):  # dict 子类 ⇒ 和真实 rumps 一样不可哈希
        __hash__ = None

        def __init__(self, title="", callback=None):
            super().__init__()
            self.title = title
            self.callback = callback
            self.hidden = False

        def add(self, item):
            self[item.title] = item

        def set_callback(self, cb):
            self.callback = cb

        def hide(self):
            self.hidden = True

        def show(self):
            self.hidden = False

        def clear(self):
            dict.clear(self)

    class App:
        def __init__(self, name="", icon=None, template=False, quit_button=None):
            self.name = name
            self.menu = []

        def run(self):
            pass

    rumps.App = App
    rumps.MenuItem = MenuItem
    rumps.notification = lambda *a, **k: None
    rumps.alert = lambda *a, **k: None
    rumps.quit_application = lambda: None
    return rumps


class MenuBarTests(unittest.TestCase):
    def setUp(self):
        self.rumps = _install_rumps_stub()
        sys.modules["rumps"] = self.rumps
        for mod in ("claude_localize.menubar",):
            sys.modules.pop(mod, None)

        from claude_localize import menubar

        self.mb = menubar
        self.fake_install = types.SimpleNamespace(version="4.29.0", app=None)
        self.shell = {"locale_file": False, "registered": False, "chinese": 0}

        self.patches = [
            mock.patch.object(menubar, "_load_settings", return_value={"shell_enhance": False}),
            mock.patch.object(menubar, "_save_settings", lambda d: None),
            mock.patch.object(menubar.discover, "detect", lambda app=None: self.fake_install),
            mock.patch.object(menubar.discover, "is_patched", lambda inst: True),
        ]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])

        # checks 在 _refresh 里是延迟导入的，直接替换模块内函数
        from claude_localize import checks

        self.checks_patch = mock.patch.object(
            checks, "shell_state", lambda inst: self.shell
        )
        self.checks_patch.start()
        self.addCleanup(self.checks_patch.stop)

        self.app = menubar.ClaudeLocalizeMenu()
        self.app._ui = lambda fn: fn()  # 立即执行，便于断言

    def _refresh(self):
        self.app._refresh()

    def test_set_busy_does_not_throw(self):
        """MenuItem 不可哈希——曾经在这里 TypeError，导致点菜单没反应。"""
        self.app._set_busy(True)
        self.assertTrue(self.app.busy)
        self.app._set_busy(False)
        self.assertFalse(self.app.busy)

    def test_busy_disables_and_restores_callbacks(self):
        self.app._set_busy(True)
        self.assertIsNone(self.app.mode_simple.callback)
        self.app._set_busy(False)
        self.assertIsNotNone(self.app.mode_simple.callback)

    def test_submenu_shows_only_the_other_mode_when_installed(self):
        self.shell["locale_file"] = False  # 当前是「仅主界面」
        self._refresh()
        self.assertEqual(self.app.install_item.title, "1. 中文补丁：主界面")
        self.assertTrue(self.app.mode_simple.hidden)
        self.assertFalse(self.app.mode_shell.hidden)
        self.assertIn("切换为「" + self.mb.MODE_SHELL, self.app.mode_shell.title)

    def test_submenu_switches_direction(self):
        self.shell["locale_file"] = True  # 当前是「主界面 + 外壳」
        self._refresh()
        self.assertEqual(self.app.install_item.title, "1. 中文补丁：主界面 + 外壳")
        self.assertTrue(self.app.mode_shell.hidden)
        self.assertFalse(self.app.mode_simple.hidden)
        self.assertIn("切换为「" + self.mb.MODE_SIMPLE, self.app.mode_simple.title)

    def test_not_installed_shows_both_install_options(self):
        with mock.patch.object(self.mb.discover, "is_patched", lambda inst: False):
            self._refresh()
        self.assertEqual(self.app.install_item.title, "1. 安装中文补丁")
        # 两个选项时加序号
        self.assertEqual(self.app.mode_simple.title, "1. " + self.mb.MODE_SIMPLE)
        self.assertEqual(self.app.mode_shell.title, "2. " + self.mb.MODE_SHELL)
        self.assertFalse(self.app.mode_simple.hidden)
        self.assertFalse(self.app.mode_shell.hidden)

    def test_apply_mode_starts_install(self):
        self.app.settings["shell_risk_ack"] = True  # 跳过风险弹窗
        calls = []
        self.app._background = lambda label, fn, on_success=None, **kw: calls.append(label)
        self.app._apply_mode(True)
        self.assertEqual(calls, ["1. 中文补丁：主界面 + 外壳"])
        self.assertTrue(self.app.settings["shell_enhance"])

    def test_apply_mode_asks_risk_once(self):
        self.app.settings["shell_risk_ack"] = False
        asked = []

        def fake_confirm():
            asked.append(1)
            return True, True  # 同意 + 记住

        self.app._confirm_shell_risk = fake_confirm
        self.app._background = lambda *a, **k: None
        self.app._apply_mode(True)
        self.assertEqual(len(asked), 1)
        self.assertTrue(self.app.settings["shell_risk_ack"])

        self.app._apply_mode(True)
        self.assertEqual(len(asked), 1, "记住之后不应再弹")

    def test_apply_mode_cancelled_does_nothing(self):
        self.app.settings["shell_risk_ack"] = False
        self.app._confirm_shell_risk = lambda: (False, False)
        calls = []
        self.app._background = lambda *a, **k: calls.append(1)
        self.app._apply_mode(True)
        self.assertEqual(calls, [])
        self.assertFalse(self.app.settings.get("shell_enhance"))

    def test_reapply_hidden_when_packs_unchanged(self):
        self.app.settings["installed_pack_stamp"] = self.app._pack_stamp()
        self._refresh()
        self.assertTrue(self.app.reapply_item.hidden)

    def test_reapply_shown_after_pack_change(self):
        self.app.settings["installed_pack_stamp"] = "stale999"
        self._refresh()
        self.assertFalse(self.app.reapply_item.hidden)
        self.assertEqual(self.app.reapply_item.title, "2. 安装更新补丁")

    def test_reapply_hidden_when_not_installed(self):
        with mock.patch.object(self.mb.discover, "is_patched", lambda inst: False):
            self.app.settings["installed_pack_stamp"] = "stale999"
            self._refresh()
        self.assertTrue(self.app.reapply_item.hidden)

    def test_single_option_has_no_number(self):
        self.shell["locale_file"] = False      # 已装「仅主界面」→ 只剩一个切换项
        self.app.settings.pop("installed_pack_stamp", None)
        self._refresh()
        self.assertFalse(self.app.mode_shell.title.startswith("1."))
        self.assertFalse(self.app.mode_shell.title.startswith("2."))

    def test_two_options_both_numbered(self):
        """切换项 + 安装更新补丁 同时出现时，两项都要编号。"""
        self.shell["locale_file"] = False
        self.app.settings["installed_pack_stamp"] = "stale999"
        self._refresh()
        self.assertEqual(self.app.mode_shell.title, f"1. 切换为「{self.mb.MODE_SHELL}」")
        self.assertEqual(self.app.reapply_item.title, "2. 安装更新补丁")

    def test_numbers_are_stable_across_refresh(self):
        """反复刷新不能把序号叠加成 1. 1. 1.。"""
        self.shell["locale_file"] = False
        self.app.settings["installed_pack_stamp"] = "stale999"
        for _ in range(3):
            self._refresh()
        self.assertEqual(self.app.mode_shell.title, f"1. 切换为「{self.mb.MODE_SHELL}」")

    def test_settings_item_indented(self):
        self.assertTrue(self.app.settings_item.title.startswith("  "))

    def test_last_action_line_updates(self):
        self.app._set_last("✓ 测试完成")
        self.assertEqual(self.app.last_item.title, "✓ 测试完成")

    def test_restore_wires_progress_item(self):
        from claude_localize import restorer

        captured = {}

        def fake_bg(label, fn, on_success=None, **kw):
            captured.update({"label": label, **kw})
            fn()

        self.app._background = fake_bg
        with mock.patch.object(restorer, "restore", lambda **k: None):
            self.app._restore(None)

        self.assertEqual(captured["item"], self.app.restore_item)
        self.assertEqual(captured["base_title"], "3. 恢复原样 / 卸载补丁")
        self.assertTrue(captured.get("spinner", True), "恢复没有真实进度，应该用转圈动画")

    def test_clean_wires_progress_item(self):
        from claude_localize import cleanup

        captured = {}
        self.app._background = lambda label, fn, on_success=None, **kw: captured.update(
            {"label": label, **kw}
        )
        with mock.patch.object(cleanup, "clean_and_resign", lambda *a, **k: []):
            self.app._clean(None)
        self.assertEqual(captured["item"], self.app.clean_item)

    def test_toggle_to_simple_needs_no_risk_prompt(self):
        self.app.settings["shell_risk_ack"] = False
        self.app._confirm_shell_risk = lambda: self.fail("关闭方向不该弹风险")
        calls = []
        self.app._background = lambda label, fn, on_success=None, **kw: calls.append(label)
        self.app._apply_mode(False)
        self.assertEqual(calls, ["1. 中文补丁：主界面"])


if __name__ == "__main__":
    unittest.main()
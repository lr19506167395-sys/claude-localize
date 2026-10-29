"""菜单栏小工具：安装补丁 / 更新翻译 / 恢复原样，后台线程跑任务，不阻塞 rumps 主循环。"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

from . import backup, discover, paths, translator, upstream

try:
    import rumps
except ImportError as exc:
    raise SystemExit("菜单栏模式需要 rumps：pipx inject claude-localize rumps") from exc


LOG_DIR = Path.home() / "Library/Logs/Claude汉化"
SETTINGS_PATH = Path.home() / ".claude-localize" / "gui-config.json"

MODE_SIMPLE = "仅主界面中文（不改 app.asar）"
MODE_SHELL = "主界面 + 桌面壳层中文（改 app.asar）"
SHELL_RISK_TEXT = (
    "• 会改写 Claude.app 内的 app.asar（约 60MB），写入中文语言文件（80KB）"
    "和在线页面翻译脚本（3.2MB）。\n"
    "• Electron 有三重完整性校验（文件条目哈希 / Info.plist 哈希 / "
    "Framework 二进制摘要槽），本工具会同步全部三项；漏掉任何一项 Claude 会直接打不开。\n"
    "• 重签名后官方开发者签名变成本地 ad-hoc 签名，自动更新仍可用，"
    "但 Claude 每次升级后都要重新装一次补丁。\n"
    "• 安装时 Claude 会退出并重新打开，未发送的输入会丢失。\n"
    "• 全程在临时副本上完成，验证不通过会自动放弃，不会破坏现有安装。"
)


def _load_settings() -> dict:
    if SETTINGS_PATH.exists():
        try:
            return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "api_key": "",
        "base_url": "",
        "model": "",
        "provider": "",
        "check_update_on_launch": True,
        "shell_enhance": True,
    }


def _save_settings(data: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class ClaudeLocalizeMenu(rumps.App):
    def __init__(self):
        icon = None
        candidates = [
            paths.PROJECT_ROOT / "resources/icons/menubar_44_template.png",
            Path(sys.executable).resolve().parent.parent / "Resources/menubar_icon/menubar_44_template.png",
        ]
        for path in candidates:
            if path.exists():
                icon = str(path)
                break
        # quit_button=None：不要再让 rumps 自动加一个 Quit，否则和我们自己的「退出」重复
        super().__init__("Claude 汉化", icon=icon, template=True, quit_button=None)
        self.icon = icon
        self.settings = _load_settings()
        self.busy = False
        self.status_item = rumps.MenuItem("状态：检查中…", callback=self._refresh)
        # 1. 安装/切换：两种模式放在二级菜单里，点哪个就切成哪个
        self.mode_simple = rumps.MenuItem(
            MODE_SIMPLE, callback=lambda _s: self._apply_mode(False)
        )
        self.mode_shell = rumps.MenuItem(
            MODE_SHELL, callback=lambda _s: self._apply_mode(True)
        )
        self.install_item = rumps.MenuItem("1. 安装中文补丁")
        # 词表更新后才出现的第二项
        self.reapply_item = rumps.MenuItem(
            "2. 安装更新补丁", callback=self._reapply
        )
        self.install_item.add(self.mode_simple)
        self.install_item.add(self.mode_shell)
        self.install_item.add(self.reapply_item)
        self.upstream_item = rumps.MenuItem("1. 从 javaht 获取中文更新", callback=self._pull_upstream)
        self.api_item = rumps.MenuItem("2. 使用翻译 agent 获取中文更新", callback=self._api_translate)
        self.settings_item = rumps.MenuItem("  * 翻译 agent 配置…", callback=self._open_settings)
        self.restore_item = rumps.MenuItem("3. 恢复原样 / 卸载补丁", callback=self._restore)
        self.clean_item = rumps.MenuItem("* 清理安装残留", callback=self._clean)
        self.last_item = rumps.MenuItem("上次操作：—")

        update_menu = rumps.MenuItem("2. 更新中文翻译")
        update_menu.add(self.upstream_item)
        update_menu.add(self.api_item)
        update_menu.add(self.settings_item)

        self.menu = [
            self.status_item,
            None,
            self.install_item,
            update_menu,
            self.restore_item,
            None,
            self.clean_item,
            None,
            self.last_item,
            None,
            rumps.MenuItem("退出", callback=rumps.quit_application),
        ]
        self._refresh()
        if self.settings.get("check_update_on_launch"):
            threading.Thread(target=self._silent_check, daemon=True).start()

    def _ui(self, fn) -> None:
        """把 UI 修改调度到主线程。

        AppKit 严禁在后台线程改 UI（菜单标题、窗口等），
        否则抛 NSInternalInconsistencyException 直接崩掉整个进程。
        """
        try:
            from PyObjCTools import AppHelper

            AppHelper.callAfter(fn)
        except Exception:
            pass

    def _set_busy(self, busy: bool):
        self.busy = busy
        # ⚠ rumps.MenuItem 继承自 OrderedDict，是 **不可哈希** 的，
        # 绝对不能拿它当 dict 的键（会 TypeError，而且菜单回调里的异常会被吞掉，
        # 表现为「点了没反应」）。用 (item, callback) 元组列表。
        pairs = [
            (self.mode_simple, lambda _s: self._apply_mode(False)),
            (self.mode_shell, lambda _s: self._apply_mode(True)),
            (self.upstream_item, self._pull_upstream),
            (self.api_item, self._api_translate),
            (self.restore_item, self._restore),
            (self.clean_item, self._clean),
        ]

        def apply():
            for item, cb in pairs:
                item.set_callback(None if busy else cb)

        self._ui(apply)

    def _apply_mode(self, with_asar: bool):
        """点二级菜单里的某个模式 = 立刻切成那种模式，不用再点安装。"""
        try:
            if self.busy:
                rumps.notification("Claude 汉化", "请稍候", "已有任务进行中")
                return
            if with_asar and not self.settings.get("shell_risk_ack"):
                ok, remember = self._confirm_shell_risk()
                if not ok:
                    return
                if remember:
                    self.settings["shell_risk_ack"] = True
                    _save_settings(self.settings)

            self.settings["shell_enhance"] = with_asar
            _save_settings(self.settings)
            base = "1. 中文补丁：主界面 + 外壳" if with_asar else "1. 中文补丁：主界面"
            # 风险弹窗是在菜单回调里跑的，直接接着做重活容易卡在菜单跟踪里；
            # 丢到下一个 runloop 再开始，稳妥得多。
            self._ui(lambda: self._run_install(with_asar, self.install_item, base))
        except BaseException as exc:
            self._log_exc("_apply_mode", exc)
            try:
                rumps.alert("切换失败", f"{type(exc).__name__}: {exc}"[:400])
            except Exception:
                pass

    def _log_exc(self, where: str, exc: BaseException | None = None):
        """把菜单回调里的异常写到文件——打包后的 app stderr 看不到。"""
        import time as _time
        import traceback

        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            with (LOG_DIR / "menu-error.log").open("a", encoding="utf-8") as f:
                f.write(f"\n[{_time.strftime('%Y-%m-%d %H:%M:%S')}] {where}\n")
                if exc is None:
                    traceback.print_exc(file=f)
                else:
                    f.write(f"{type(exc).__name__}: {exc}\n")
                    traceback.print_exception(type(exc), exc, exc.__traceback__, file=f)
        except Exception:
            pass

    def _confirm_shell_risk(self) -> tuple[bool, bool]:
        """开启前提示风险，返回 (是否继续, 是否不再提示)。"""
        from AppKit import NSAlert, NSButton, NSMakeRect, NSView

        alert = NSAlert.alloc().init()
        alert.setMessageText_("开启桌面壳层增强？")
        alert.setInformativeText_(SHELL_RISK_TEXT)
        alert.addButtonWithTitle_("开启")
        alert.addButtonWithTitle_("取消")

        box = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 460, 24))
        check = NSButton.alloc().initWithFrame_(NSMakeRect(0, 2, 460, 20))
        check.setButtonType_(3)  # NSSwitchButton
        check.setTitle_("以后不再提示")
        check.setState_(0)
        box.addSubview_(check)
        alert.setAccessoryView_(box)

        accepted = alert.runModal() == 1000
        return accepted, bool(check.state())

    def _clean(self, sender):
        from . import cleanup, discover

        def run():
            inst = discover.detect()
            items = cleanup.clean_and_resign(inst.app)
            return [i for i in items if i.removed]

        self._background(
            "清理安装残留", run,
            lambda items: ("清理完成", f"已清理 {len(items)} 项" if items else "没有可清理的残留"),
            item=self.clean_item, base_title="* 清理安装残留",
        )

    def _refresh(self, sender=None):
        title = "状态：检查中…"
        shell_on = None
        patched: bool | None = None
        try:
            inst = discover.detect()
            patched = discover.is_patched(inst)
            tail = "已汉化" if patched else "未汉化"
            try:
                from . import checks

                st = checks.shell_state(inst)
                shell_on = bool(st["locale_file"])
                if shell_on:
                    tail += " · 外壳中文" if st["registered"] else " · 外壳待注册"
            except Exception:
                pass
            kind = backup.backup_kind(inst.app)
            if kind == "pristine":
                tail += " · 备份:原版"
            elif kind == "patched":
                tail += " · 备份:已汉化"
            title = f"状态：{inst.version} · {tail}"
        except Exception as exc:
            title = f"状态：{exc}"

        def apply():
            self.status_item.title = title
            if patched is None:
                return
            stale = patched and self._packs_changed()

            if not patched:
                self.install_item.title = "1. 安装中文补丁"
                self.mode_simple.title = MODE_SIMPLE
                self.mode_simple.show()
                self.mode_shell.title = MODE_SHELL
                self.mode_shell.show()
                self.reapply_item.hide()
                visible = [self.mode_simple, self.mode_shell]
            else:
                on = bool(shell_on)
                self.install_item.title = (
                    "1. 中文补丁：主界面 + 外壳" if on else "1. 中文补丁：主界面"
                )
                if on:
                    self.mode_simple.title = f"切换为「{MODE_SIMPLE}」"
                    self.mode_simple.show()
                    self.mode_shell.hide()
                    visible = [self.mode_simple]
                else:
                    self.mode_shell.title = f"切换为「{MODE_SHELL}」"
                    self.mode_shell.show()
                    self.mode_simple.hide()
                    visible = [self.mode_shell]
                if stale:
                    self.reapply_item.title = "安装更新补丁"
                    self.reapply_item.show()
                    visible.append(self.reapply_item)
                else:
                    self.reapply_item.hide()

            # 规则：二级菜单里两项及以上才编号（1. 2. 3.），单项不编号
            if len(visible) >= 2:
                for i, mi in enumerate(visible, 1):
                    base = mi.title
                    for prefix in ("1. ", "2. ", "3. "):
                        if base.startswith(prefix):
                            base = base[len(prefix):]
                            break
                    mi.title = f"{i}. {base}"

        self._ui(apply)

    def _notify(self, title: str, message: str):
        self._ui(lambda: rumps.notification("Claude 汉化", title, message))
        self._refresh()

    def _background(
        self, label: str, fn, on_success=None, *,
        item=None, base_title: str | None = None, spinner: bool = True,
    ):
        """后台跑任务；运行中在菜单项上显示进度，结束不管成败都留一条记录。"""
        if self.busy:
            rumps.notification("Claude 汉化", "请稍候", "已有任务进行中")
            return
        self._set_busy(True)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log = LOG_DIR / "latest.log"
        log.write_text(f"开始：{label}\n", encoding="utf-8")

        base = base_title or label
        stop = threading.Event()
        self._set_last(f"运行中：{label}")

        if item is not None and spinner:
            def spin():
                frames = "◐◓◑◒"
                i = 0
                while not stop.wait(0.4):
                    frame = frames[i % 4]
                    i += 1
                    self._ui(lambda t=f"{base} {frame}": setattr(item, "title", t))

            threading.Thread(target=spin, daemon=True).start()

        class Writer:
            def write(self, text):
                with log.open("a", encoding="utf-8") as f:
                    f.write(str(text))
            def flush(self):
                pass

        def run():
            old_out, old_err = sys.stdout, sys.stderr
            sys.stdout = sys.stderr = Writer()
            try:
                result = fn()
                if on_success:
                    title, msg = on_success(result)
                else:
                    title, msg = label + "完成", "已完成"
                self._set_last(f"✓ {label} · {msg[:48]}")
                self._notify(title, msg)
            except BaseException as exc:
                with log.open("a", encoding="utf-8") as f:
                    f.write(f"\n错误：{exc}\n")
                brief = str(exc).strip().replace("\n", " ")[:60]
                self._set_last(f"✗ {label}：{brief}")
                try:
                    self._notify(label + "失败", str(exc)[:220])
                except Exception:
                    pass
            finally:
                stop.set()
                sys.stdout, sys.stderr = old_out, old_err
                try:
                    self._set_busy(False)
                    if item is not None:
                        self._ui(lambda: setattr(item, "title", base))
                    self._refresh()
                except Exception:
                    pass

        threading.Thread(target=run, daemon=True).start()

    def _pack_stamp(self) -> str:
        """当前词表指纹：内置词表 + 自有译文的内容哈希。

        安装成功后把指纹记到配置里；之后它一变（拉取了上游 / 补翻了 / 改了译文），
        二级菜单就会多出「安装更新补丁」。
        """
        import hashlib

        h = hashlib.sha256()
        for path in (
            paths.PROJECT_ROOT / "packs" / paths.TARGET_LOCALE / "frontend.json",
            paths.PROJECT_ROOT / "packs" / paths.TARGET_LOCALE / "desktop.json",
            paths.custom_path(),
        ):
            try:
                h.update(path.read_bytes())
            except OSError:
                h.update(b"<missing>")
        return h.hexdigest()[:16]

    def _packs_changed(self) -> bool:
        installed = self.settings.get("installed_pack_stamp")
        if not installed:
            return False          # 旧版本没记录过，不提示，避免误报
        return installed != self._pack_stamp()

    def _reapply(self, sender=None):
        """词表更新后，用当前模式重装一次。"""
        with_asar = bool(self.settings.get("shell_enhance", True))
        base = "1. 中文补丁：主界面 + 外壳" if with_asar else "1. 中文补丁：主界面"
        self._run_install(with_asar, self.install_item, base, mark_stamp=True)

    def _set_last(self, text: str) -> None:
        self._ui(lambda: setattr(self.last_item, "title", text))

    def _run_install(self, with_asar: bool, item, base_title: str, *, mark_stamp: bool = True):
        """安装流程。两种模式共用这一条已验证的路径。"""
        from . import discover, installer

        import time as _time

        last_update = [0.0]

        def progress(pct: int, msg: str) -> None:
            now = _time.monotonic()
            if now - last_update[0] < 1.0 and pct < 100:
                return  # 限流：每秒最多刷一次标题
            last_update[0] = now
            bar_len = 10
            filled = int(bar_len * pct / 100)
            bar = "█" * filled + "░" * (bar_len - filled)
            label = f"{base_title} {bar} {pct}%"
            if msg:
                label += f" · {msg}"
            self._ui(lambda: setattr(item, "title", label))

        def run():
            try:
                report = installer.install(
                    with_asar=with_asar, launch=True, progress=progress
                )
                if mark_stamp:
                    self.settings["installed_pack_stamp"] = self._pack_stamp()
                    _save_settings(self.settings)
                return report
            except BaseException:
                # 安装会把 Claude 退掉；失败时不能让它就这么关着
                try:
                    discover.detect().launch()
                except Exception:
                    pass
                raise
            finally:
                self._ui(lambda: setattr(item, "title", base_title))

        def finish(r):
            msg = f"主界面覆盖率 {r.frontend_translated / r.frontend_total * 100:.1f}%"
            if r.shell_enabled and r.shell_registered:
                msg += "，桌面壳层中文 + 白名单已注册"
            elif r.shell_enabled:
                msg += "，桌面壳层已写入"
            else:
                msg += "，桌面壳层保持英文"
            if r.cleaned:
                msg += f"，清理残留 {r.cleaned} 项"
            msg += "，正在打开 Claude"
            return ("中文补丁已安装", msg)

        self._background(
            base_title.lstrip("* "), run, finish,
            item=item, base_title=base_title, spinner=False,
        )

    def _restore(self, sender):
        from . import restorer
        self._background(
            "恢复原样 / 卸载补丁",
            lambda: restorer.restore(launch=True),
            lambda status: (
                ("当前已是官方原版", "没有需要还原的补丁，已重新打开 Claude")
                if status == "already"
                else ("已还原为英文", "Claude 正在重新打开")
            ),
            item=self.restore_item, base_title="3. 恢复原样 / 卸载补丁",
        )

    def _pull_upstream(self, sender):
        self._background(
            "从 javaht 获取中文更新",
            lambda: upstream.pull(),
            lambda counts: ("中文翻译已更新", f"共 {sum(counts.values())} 条，点击「安装中文补丁」应用"),
            item=self.upstream_item, base_title="1. 从 javaht 获取中文更新",
        )

    def _api_translate(self, sender):
        missing = [
            label for key, label in (
                ("api_key", "API Key"), ("base_url", "API 地址"), ("model", "模型"),
            ) if not str(self.settings.get(key) or "").strip()
        ]
        if missing:
            rumps.notification(
                "Claude 汉化", "请先配置",
                f"缺少 {'、'.join(missing)}：点击「* 翻译 agent 配置…」填写",
            )
            return

        cfg = self.settings
        def progress(done, total, size):
            label = f"2. 使用翻译 agent 获取中文更新 · {done}/{total} 批"
            self._ui(lambda: setattr(self.api_item, "title", label))

        def run():
            return translator.translate_missing(
                api_key=cfg["api_key"],
                base_url=cfg.get("base_url") or translator.DEFAULT_BASE_URL,
                model=cfg.get("model") or translator.DEFAULT_MODEL,
                provider=cfg.get("provider", ""),
                progress=progress,
            )

        def finish(stats):
            self._ui(lambda: setattr(self.api_item, "title", "2. 使用翻译 agent 获取中文更新"))
            return ("补翻完成", f"{stats.summary()}，点击「安装中文补丁」应用")

        self._background(
            "使用翻译 agent 获取中文更新", run, finish,
            item=self.api_item,
            base_title="2. 使用翻译 agent 获取中文更新",
            spinner=False,
        )

    def _silent_check(self):
        try:
            remote = upstream.check_remote()
            local = upstream.local_version()
            if remote.get("release") != local:
                rumps.notification("Claude 汉化", "上游有新词表", f"上游 {remote.get('release')}，可从「更新词表」获取")
        except Exception:
            pass

    def _open_settings(self, sender):
        """翻译 agent 配置：用 NSAlert + 附件视图，最稳定的原生表单方式。"""
        try:
            self._show_settings_alert()
        except Exception as exc:
            import traceback
            detail = traceback.format_exc()
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            (LOG_DIR / "settings-error.log").write_text(detail, encoding="utf-8")
            try:
                from AppKit import NSAlert
                a = NSAlert.alloc().init()
                a.setMessageText_("无法打开配置窗口")
                a.setInformativeText_(str(exc)[:400])
                a.addButtonWithTitle_("好")
                a.runModal()
            except Exception:
                rumps.alert("无法打开配置窗口", str(exc)[:400])

    def _show_settings_alert(self):
        from AppKit import (
            NSAlert, NSButton, NSFont, NSMakeRect, NSPopUpButton,
            NSSecureTextField, NSTextField, NSView,
        )

        alert = NSAlert.alloc().init()
        alert.setMessageText_("翻译 agent 配置")
        alert.setInformativeText_(
            "「使用翻译 agent 获取中文更新」需要你自己的 API。\n"
            "支持任意 OpenAI 兼容（/chat/completions）或 Anthropic（/v1/messages）端点，"
            "本项目不提供也不代理任何服务。"
        )
        alert.addButtonWithTitle_("保存")
        alert.addButtonWithTitle_("取消")

        container = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 460, 214))
        fields = {}
        rows = [
            ("api_key", "API Key", True),
            ("base_url", "API 地址", False),
            ("model", "模型", False),
        ]
        y = 150
        for key, label, secret in rows:
            lbl = NSTextField.labelWithString_(label)
            lbl.setFrame_(NSMakeRect(0, y + 4, 90, 20))
            lbl.setFont_(NSFont.systemFontOfSize_(13))
            container.addSubview_(lbl)

            if secret:
                field = NSSecureTextField.alloc().initWithFrame_(NSMakeRect(96, y, 356, 24))
            else:
                field = NSTextField.alloc().initWithFrame_(NSMakeRect(96, y, 356, 24))
            field.setStringValue_(self.settings.get(key, "") or "")
            field.setFont_(NSFont.systemFontOfSize_(13))
            placeholder = {
                "api_key": "你自己的 API Key",
                "base_url": "例如 https://你的端点/v1",
                "model": "例如 claude-haiku-5-5",
            }.get(key, "")
            if placeholder:
                field.setPlaceholderString_(placeholder)
            container.addSubview_(field)
            fields[key] = field
            y -= 40

        lbl = NSTextField.labelWithString_("协议")
        lbl.setFrame_(NSMakeRect(0, y + 4, 90, 20))
        lbl.setFont_(NSFont.systemFontOfSize_(13))
        container.addSubview_(lbl)

        popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(96, y, 180, 26), False)
        popup.addItemsWithTitles_(["自动推断", "openai", "anthropic"])
        current = (self.settings.get("provider") or "").strip()
        popup.selectItemWithTitle_(
            {"": "自动推断", "openai": "openai", "anthropic": "anthropic"}.get(current, "自动推断")
        )
        container.addSubview_(popup)

        ack = NSButton.alloc().initWithFrame_(NSMakeRect(0, 6, 460, 20))
        ack.setButtonType_(3)  # NSSwitchButton
        ack.setTitle_("切换桌面壳层时不再提示风险")
        ack.setState_(1 if self.settings.get("shell_risk_ack") else 0)
        container.addSubview_(ack)

        alert.setAccessoryView_(container)

        if alert.runModal() != 1000:  # 1000 = 第一个按钮（保存）
            return

        self.settings["shell_risk_ack"] = bool(ack.state())

        for key, field in fields.items():
            self.settings[key] = str(field.stringValue()).strip()
        chosen = str(popup.titleOfSelectedItem())
        self.settings["provider"] = "" if chosen == "自动推断" else chosen
        _save_settings(self.settings)
        rumps.notification(
            "Claude 汉化", "配置已保存",
            "可使用「更新中文翻译 → 使用翻译 agent 获取中文更新」"
        )

def main():
    app = ClaudeLocalizeMenu()
    app.run()


if __name__ == "__main__":
    main()

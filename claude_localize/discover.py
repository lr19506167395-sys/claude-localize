"""检测 Claude 安装、版本、语言与资源路径。"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import asar, paths


@dataclass
class ClaudeInstall:
    app: Path
    version: str
    locale: str

    @property
    def resources(self) -> Path:
        return paths.resources_of(self.app)

    def frontend(self, locale: str | None = None) -> Path:
        return paths.frontend_json(self.app, locale or self.locale)

    def frontend_en(self) -> Path:
        return paths.frontend_json(self.app, "en-US")

    def frontend_dynamic(self, locale: str) -> Path:
        return paths.frontend_dynamic_json(self.app, locale)

    def desktop(self, locale: str | None = None) -> Path:
        return paths.desktop_json(self.app, locale or self.locale)

    def desktop_en(self) -> Path:
        return paths.desktop_json(self.app, "en-US")

    def assets(self) -> Path:
        return paths.assets_dir(self.app)

    def asar(self) -> Path:
        return paths.asar_path(self.app)

    def is_running(self) -> bool:
        r = subprocess.run(["pgrep", "-x", "Claude"], capture_output=True)
        return r.returncode == 0

    def quit(self, wait: bool = True, timeout: float = 30.0) -> bool:
        """退出 Claude，并等待进程真正消失。

        只发退出信号不等待是危险的：旧进程可能在安装完成后才真正退出，
        退出时把内存里的旧配置刷盘，覆盖掉刚写入的 locale。
        """
        import time

        if not self.is_running():
            return True

        subprocess.run(
            ["osascript", "-e", 'tell application "Claude" to quit'],
            capture_output=True, timeout=15,
        )
        if not wait:
            return True

        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self.is_running():
                # 多等一拍，确保它把配置写完再返回
                time.sleep(1.0)
                return True
            time.sleep(0.3)

        # 宽限期过了还在跑，强制结束
        subprocess.run(["pkill", "-x", "Claude"], capture_output=True)
        deadline = time.time() + 10
        while time.time() < deadline:
            if not self.is_running():
                time.sleep(1.0)
                return True
            time.sleep(0.3)
        return False

    def launch(self) -> None:
        subprocess.run(["open", "-a", str(self.app)], capture_output=True)

    def read_locale_from_config(self) -> str:
        """读取 Claude 实际使用的 locale（优先 Claude-3p，其次 Claude）。"""
        p = active_config_path()
        if p is None:
            return self.locale
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
            if cfg.get("locale"):
                return str(cfg["locale"])
        except Exception:
            pass
        return self.locale

    def write_locale(self, locale: str) -> None:
        """把 locale 写进所有 Claude 用户配置目录。

        Claude 有多套配置目录（Claude / Claude-3p 等），
        实际读哪个取决于启动参数 --user-data-dir。
        只写其中一个会导致首次启动语言不对，必须全部覆盖。
        """
        app_support = Path.home() / "Library" / "Application Support"
        targets = [
            app_support / "Claude" / "config.json",
            app_support / "Claude-3p" / "config.json",
        ]
        # 顺带覆盖任何现存的 Claude-<suffix> 目录
        for p in app_support.glob("Claude-*/config.json"):
            if p not in targets:
                targets.append(p)

        for cfg_path in targets:
            # 目录不存在就跳过，不主动创建（避免凭空造出配置目录）
            if not cfg_path.parent.exists():
                continue
            cfg = {}
            if cfg_path.exists():
                try:
                    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                    if not isinstance(cfg, dict):
                        cfg = {}
                except Exception:
                    cfg = {}
            if cfg.get("locale") == locale:
                continue
            cfg["locale"] = locale
            cfg_path.write_text(
                json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            try:
                cfg_path.chmod(0o600)
            except OSError:
                pass


def active_config_path() -> Path | None:
    """Claude 实际在用的配置文件。

    4.x 用 ``--user-data-dir=.../Claude-3p`` 启动，所以优先读 Claude-3p；
    只读 Claude/config.json 会读到很久以前残留的 locale。
    """
    base = Path.home() / "Library" / "Application Support"
    for name in ("Claude-3p", "Claude"):
        p = base / name / "config.json"
        if p.exists():
            return p
    return None


def detect(app: Path | None = None) -> ClaudeInstall:
    app = app or paths.APP_PATH
    if not app.exists():
        raise SystemExit(f"找不到 Claude.app：{app}")

    plist = app / "Contents" / "Info.plist"
    version = subprocess.check_output(
        ["/usr/libexec/PlistBuddy", "-c", "Print :CFBundleShortVersionString", str(plist)]
    ).decode().strip()

    cfg_path = active_config_path()
    locale = "en-US"
    if cfg_path is not None and cfg_path.exists():
        try:
            locale = json.loads(cfg_path.read_text(encoding="utf-8")).get("locale", "en-US")
        except Exception:
            pass

    return ClaudeInstall(app=app, version=version, locale=locale)


def is_patched(inst: ClaudeInstall) -> bool:
    """判断当前 app 上是否**还有**我们的汉化内容。

    判据只看内容，不看签名：还原之后我们会重新签名，签名一定是 ad-hoc，
    拿它当判据会导致「已经还原成英文了，界面却显示已汉化」。
    """
    res = paths.resources_of(inst.app)

    # 1. 桌面壳层：app.asar 里的语言文件 / 白名单
    try:
        from . import shell

        if shell.has_artifacts(inst.asar(), "zh-CN"):
            return True
    except Exception:
        pass

    # 2. 主界面语言包（4.29 在 ion-dist.asar 里，3.x 是散装目录）
    try:
        frontend_asar = res / "ion-dist.asar"
        if frontend_asar.is_file():
            if asar.has_entry(frontend_asar, "i18n/zh-CN.json"):
                return True
        elif inst.frontend("zh-CN").exists():
            return True
    except Exception:
        pass

    # 3. 原生字符串
    try:
        from . import native_strings

        if native_strings.installed(inst.app):
            return True
    except Exception:
        pass

    return False

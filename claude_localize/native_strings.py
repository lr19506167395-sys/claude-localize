"""macOS 原生字符串（.lproj/Localizable.strings）汉化。

Electron 外壳里还有一小撮由 Swift/ObjC 负责的界面：辅助功能/录屏权限提示、
「N 秒后停止」之类。它们走 NSLocalizedString，只认 bundle 里的
``<lang>.lproj/Localizable.strings``，i18n JSON 管不到。

官方 4.29 只发了 ``en.lproj``，没有中文 lproj（``zh_TW.lproj`` 是空目录）。
这里把我们随包的中文 .strings 写进三个候选目录：

* ``zh-Hans.lproj`` —— 系统语言 zh-Hans-* 的标准匹配名
* ``zh_CN.lproj``   —— 旧式匹配名，兼容老系统
* ``zh-CN.lproj``   —— 非标准，但 javaht 也写，保持互操作
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

from . import paths

LPROJ_NAMES = ("zh-Hans.lproj", "zh_CN.lproj", "zh-CN.lproj")


def _force_writable(path: Path) -> None:
    """javaht 会给 .lproj 打 uchg 标记，直接覆盖会 Permission denied。"""
    if not path.exists():
        return
    subprocess.run(["chflags", "nouchg", str(path)], capture_output=True)
    try:
        os.chmod(path, os.stat(path).st_mode | stat.S_IWUSR)
    except OSError:
        pass


def pack_file() -> Path:
    return paths.PROJECT_ROOT / "packs" / paths.TARGET_LOCALE / "Localizable.strings"


def _resources(app: Path) -> Path:
    return app / "Contents" / "Resources"


def _looks_chinese(path: Path) -> bool:
    try:
        text = path.read_bytes().decode("utf-16-le", "ignore")
    except OSError:
        return False
    return any("一" <= ch <= "鿿" for ch in text)


def install(app: Path, log=print) -> list[str]:
    """写入中文 .strings，返回写过的 lproj 目录名。"""
    src = pack_file()
    if not src.exists():
        return []
    if not _looks_chinese(src):
        log(f"警告：{src} 看起来不是中文 UTF-16 文件，跳过原生字符串")
        return []

    res = _resources(app)
    written = []
    for name in LPROJ_NAMES:
        out_dir = res / name
        _force_writable(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / "Localizable.strings"
        _force_writable(target)
        shutil.copy2(src, target)
        written.append(name)
    if written:
        log(f"原生字符串已写入：{', '.join(written)}")
    return written


def remove(app: Path, log=print) -> list[str]:
    """删除我们写入的 lproj（还原时调用）。"""
    res = _resources(app)
    removed = []
    for name in LPROJ_NAMES:
        d = res / name
        if not d.is_dir():
            continue
        strings = d / "Localizable.strings"
        if not strings.exists():
            continue
        if not _looks_chinese(strings):
            continue  # 不是我们的，别动
        _force_writable(strings)
        strings.unlink()
        try:
            d.rmdir()  # 只在空目录时成功
            removed.append(name)
        except OSError:
            removed.append(name)
    if removed:
        log(f"已移除原生字符串：{', '.join(removed)}")
    return removed


def installed(app: Path) -> list[str]:
    res = _resources(app)
    return [
        name for name in LPROJ_NAMES
        if (res / name / "Localizable.strings").exists()
        and _looks_chinese(res / name / "Localizable.strings")
    ]
"""py2app 打包菜单栏 App；运行：uv run --python 3.14 setup.py build"""
import shutil
from pathlib import Path
from setuptools import setup, Command

APP = ["menubar_app.py"]
PACK_FILES = [str(p) for p in sorted(Path("packs/zh-CN").glob("*.json"))]
PACK_FILES += [str(p) for p in sorted(Path("packs/zh-CN").glob("*.strings"))]
# 自有译文（agent 补翻成果）也要随包分发，否则安装时用不上
CUSTOM_FILES = [str(p) for p in sorted(Path("sources/custom").glob("*.json"))]
ICONS = [str(p) for p in Path("resources/icons").glob("*.png")]

OPTIONS = {
    "argv_emulation": False,
    "plist": {
        "CFBundleName": "Claude汉化",
        "CFBundleDisplayName": "Claude汉化",
        "CFBundleIdentifier": "com.claude-localize.app",
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "1",
        "CFBundleIconFile": "AppIcon.icns",
        "CFBundleDevelopmentRegion": "zh_CN",
        "LSUIElement": True,
        "NSUserNotificationAlertStyle": "alert",
    },
    "iconfile": "resources/icons/AppIcon.icns",
    "packages": ["rumps", "claude_localize"],
    "site_packages": True,
    "excludes": ["PyQt5", "PySide2", "numpy", "pandas", "matplotlib"],
}


class postbuild(Command):
    user_options = []

    def initialize_options(self):
        pass

    def finalize_options(self):
        pass

    def run(self):
        self.run_command("py2app")
        app = Path("dist/Claude汉化.app")
        if not app.exists():
            return
        version = f"{__import__('sys').version_info.major}.{__import__('sys').version_info.minor}"
        lib = app / "Contents/Resources/lib" / f"python{version}"
        site = lib / "site-packages"
        site.mkdir(parents=True, exist_ok=True)
        src = Path(f"build/venv-pack/lib/python{version}/site-packages")
        if src.is_dir():
            for name in [
                "rumps", "objc", "AppKit", "Cocoa", "CoreFoundation",
                "Foundation", "PyObjCTest", "PyObjCTools",
            ]:
                if (src / name).exists() and not (site / name).exists():
                    shutil.copytree(src / name, site / name)
            for info in src.glob("*.dist-info"):
                if info.name.startswith(("pyobjc", "rumps", "objc")) and not (site / info.name).exists():
                    shutil.copytree(info, site / info.name)

        boot = app / "Contents/Resources/__boot__.py"
        text = boot.read_text(encoding="utf-8")
        marker = "\n_site_packages("
        pos = text.find(marker)
        if pos >= 0:
            patch = (
                "\n# claude-localize bundle imports\n"
                "import sys as _sys, os as _os\n"
                "_lib = _os.path.join(_os.environ.get('RESOURCEPATH',''), 'lib', 'python%d.%d' % _sys.version_info[:2])\n"
                "_sp = _os.path.join(_lib, 'site-packages')\n"
                "for _p in (_lib, _sp):\n"
                "    if _os.path.isdir(_p) and _p not in _sys.path: _sys.path.insert(0, _p)\n"
            )
            boot.write_text(text[:pos] + patch + text[pos:], encoding="utf-8")


setup(
    name="Claude汉化",
    app=APP,
    data_files=[
        ("packs/zh-CN", PACK_FILES),
        ("sources/custom", CUSTOM_FILES),
        ("menubar_icon", ICONS),
        ("", ["resources/icons/AppIcon.icns"]),
    ],
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
    cmdclass={"postbuild": postbuild},
)

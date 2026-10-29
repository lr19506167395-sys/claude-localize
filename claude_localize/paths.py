"""路径与常量定义。"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_PATH = Path("/Applications/Claude.app")
RESOURCES = APP_PATH / "Contents" / "Resources"
CONFIG_PATH = Path.home() / "Library" / "Application Support" / "Claude" / "config.json"

TARGET_LOCALE = "zh-CN"

# 前端 i18n 相对路径
FRONTEND_I18N = Path("ion-dist") / "i18n"
FRONTEND_ASSETS = Path("ion-dist") / "assets" / "v1"
APP_ASAR = Path("app.asar")

def _project_root() -> Path:
    """定位词表所在的项目根目录，兼容源码运行与 py2app 打包。"""
    import claude_localize

    src = Path(claude_localize.__file__).resolve().parent.parent
    if (src / "packs").is_dir():
        return src
    resources = Path(sys.executable).resolve().parent.parent / "Resources"
    if (resources / "packs").is_dir():
        return resources
    home = Path(os.environ.get("CLAUDE_LOCALIZE_HOME", Path.home() / ".claude-localize"))
    if (home / "packs").is_dir():
        return home
    return src


# 项目根目录：只读资源（词表、图标）。py2app 打包后指向 app 包内部。
PROJECT_ROOT = _project_root()

# 用户可写目录：配置、自有译文、缓存。重装/升级不会丢。
USER_DIR = Path(os.environ.get("CLAUDE_LOCALIZE_HOME", Path.home() / ".claude-localize"))
CONFIG_FILE = USER_DIR / "config.json"

BACKUPS_DIR = USER_DIR / "backups"
CACHE_DIR = USER_DIR / "cache"

# 自有译文启动时要放在这里；也可以在用户目录写，见 custom_path()
CUSTOM_DIR = USER_DIR
SEED_CUSTOM = PROJECT_ROOT / "sources" / "custom" / "translated.json"


def custom_path() -> Path:
    """自有译文的读写位置（用户目录，永远可写）。

    首次运行时如果用户目录还没有，就从随包分发的种子文件复制一份。
    这样：用户补翻的结果不会被 app 升级覆盖，也不会写进只读的 app 包。
    """
    target = CUSTOM_DIR / "translated.json"
    if not target.exists() and SEED_CUSTOM.exists():
        try:
            CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SEED_CUSTOM, target)
        except OSError:
            return SEED_CUSTOM
    return target


def cache_path(name: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / name


def resources_of(app: Path) -> Path:
    return app / "Contents" / "Resources"


def frontend_json(app: Path, locale: str) -> Path:
    return resources_of(app) / FRONTEND_I18N / f"{locale}.json"


def frontend_dynamic_json(app: Path, locale: str) -> Path:
    return resources_of(app) / FRONTEND_I18N / "dynamic" / f"{locale}.json"


def desktop_json(app: Path, locale: str) -> Path:
    return resources_of(app) / f"{locale}.json"


def assets_dir(app: Path) -> Path:
    return resources_of(app) / FRONTEND_ASSETS


def asar_path(app: Path) -> Path:
    return resources_of(app) / APP_ASAR

"""桌面壳层（app.asar 主进程）汉化。

Claude 4.29 的外壳语言有两层筛选：

1. 启动时 ``twt()`` 读配置里的 ``locale``：只要 ``resources/i18n/<locale>.json``
   存在就加载 —— 所以我们写进去的 ``zh-CN.json`` 本身就能生效。
2. 配置里没有 locale 时退回 ``YCt()``：按系统语言在**硬编码白名单**里找，
   白名单只有 11 种语言（en/de/fr/ko/ja/es-419/es/it/hi/pt/id），
   中文不在其中 —— 官方随包的 ``zh-Hans.json`` 因此永远不被加载。

这里做两件事：写入语言文件 + 把 ``zh-CN`` 补进那份白名单，
这样无论配置有没有被重置，外壳都会解析到中文。

整个操作在 app.asar 的解包目录里进行，由 ``frontend_bundle.AppAsarTarget``
统一重打包并同步完整性哈希。
"""
from __future__ import annotations

import re
from pathlib import Path

from . import jsonutil

# 白名单对象形如：{en:"en-US",de:"de-DE",...,id:"id-ID"}
# 对象内部没有嵌套花括号，用前后断言把整段抠出来。
LOCALE_MAP_RE = re.compile(r"\{(?=[^{}]*\ben:\"en-US\")[^{}]*\}")

MARKER = "__claudeZhShell"


def patch_locale_whitelist(text: str, lang: str = "zh-CN") -> tuple[str, int]:
    """把 lang 加进外壳的语言白名单。幂等。"""
    count = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal count
        body = match.group(0)
        if f'"{lang}"' in body or f"{lang}:" in body:
            return body
        count += 1
        return body[:-1] + f',"{lang}":"{lang}"}}'

    return LOCALE_MAP_RE.sub(repl, text), count


def is_whitelisted(text: str, lang: str = "zh-CN") -> bool:
    m = LOCALE_MAP_RE.search(text)
    if m is None:
        return False
    body = m.group(0)
    return f'"{lang}"' in body or f"{lang}:" in body


def unpatch_locale_whitelist(text: str, lang: str = "zh-CN") -> tuple[str, int]:
    """把 lang 从白名单里摘掉（关闭桌面壳层增强时用）。幂等。"""
    count = 0
    token_re = re.compile(r',?"%s":"%s",?' % (re.escape(lang), re.escape(lang)))

    def repl(match: re.Match[str]) -> str:
        nonlocal count
        body = match.group(0)
        new = token_re.sub("", body)
        new = new.replace(",}", "}").replace("{,", "{")
        if new != body:
            count += 1
        return new

    return LOCALE_MAP_RE.sub(repl, text), count


def has_artifacts(app_asar: Path, lang: str = "zh-CN") -> bool:
    """app.asar 里有没有我们留下的东西（语言文件或白名单项）。

    关闭增强时需要据此判断要不要重打包——什么都没有就别碰它。
    """
    from . import asar

    if not app_asar.is_file():
        return False
    try:
        _hs, _s, header = asar.read_header_file(app_asar)
    except Exception:
        return False
    if asar.find_entry(header, f"resources/i18n/{lang}.json") is not None:
        return True
    for rel, _entry in asar.walk(header):
        if not rel.endswith(".js"):
            continue
        try:
            body = asar.read_entry_file(app_asar, rel) or b""
        except Exception:
            continue
        text = body.decode("utf-8", "ignore")
        if "getPreferredSystemLanguages" in text:
            return is_whitelisted(text, lang)
    return False


def uninstall(work_dir: Path, lang: str, log=print) -> dict:
    """在解包目录里撤掉外壳汉化：删语言文件、摘白名单项。"""
    removed: list[str] = []
    locale_file = work_dir / "resources" / "i18n" / f"{lang}.json"
    if locale_file.exists():
        locale_file.unlink()
        removed.append(str(locale_file))

    unpatched: list[str] = []
    for js in _main_bundles(work_dir):
        text = js.read_text(encoding="utf-8")
        updated, n = unpatch_locale_whitelist(text, lang)
        if n:
            js.write_text(updated, encoding="utf-8")
            unpatched.append(js.name)

    if removed or unpatched:
        log(f"已移除桌面壳层汉化：语言文件 {len(removed)} 个，白名单 {len(unpatched)} 个 bundle")
    return {"removed": removed, "unpatched": unpatched}


def _main_bundles(work_dir: Path) -> list[Path]:
    """可能含外壳语言逻辑的主进程 bundle。"""
    out = []
    for js in (work_dir / ".vite" / "build").glob("*.js"):
        try:
            head = js.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "getPreferredSystemLanguages" in head or LOCALE_MAP_RE.search(head):
            out.append(js)
    return out


def install(work_dir: Path, lang: str, merged: dict[str, str], log=print) -> dict:
    """在 app.asar 解包目录里写入语言文件并注册白名单。"""
    i18n_dir = work_dir / "resources" / "i18n"
    i18n_dir.mkdir(parents=True, exist_ok=True)
    jsonutil.save_json(i18n_dir / f"{lang}.json", merged)

    patched: list[str] = []
    registered = False
    for js in _main_bundles(work_dir):
        text = js.read_text(encoding="utf-8")
        updated, n = patch_locale_whitelist(text, lang)
        if n:
            js.write_text(updated, encoding="utf-8")
            patched.append(js.name)
        if is_whitelisted(updated, lang):
            registered = True

    if patched:
        log(f"外壳语言白名单已注册 {lang}：{', '.join(patched)}")
    elif registered:
        log(f"外壳语言白名单已含 {lang}，跳过")
    else:
        log(f"警告：没找到外壳语言白名单，{lang} 只能靠配置里的 locale 生效")
    return {
        "locale_file": str(i18n_dir / f"{lang}.json"),
        "patched": patched,
        "registered": registered,
    }
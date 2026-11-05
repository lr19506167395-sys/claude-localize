"""斜杠命令双语菜单：``/help 帮助``。

移植自 javaht/claude-desktop-zh-cn（MIT）。改动只在显示层：

* 不碰命令 ID、搜索、点击回调
* 只给 ``type === "skill"`` 且 ``anthropicDefined === true`` 的行追加一个灰色中文标签

实现方式是在 ``SlashCommandMenuPopupBody`` 的渲染行里插一个条件节点，
再在文件末尾追加一张 49 条的 label 表 + 一个查表函数（用 marker 标记，可完整撤除）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import jsonutil, paths

MARKER = "/*__claudeZhSlashGloss_v1__*/"


def _pack_dir() -> Path:
    return paths.pack_dir()


def load_profile() -> dict:
    p = _pack_dir() / "slash-menu-patch.json"
    return jsonutil.load_json(p) if p.exists() else {}


def load_labels(lang: str) -> dict[str, str]:
    p = _pack_dir() / "slash-glosses.json"
    if not p.exists():
        return {}
    data = jsonutil.load_json(p)
    labels = data.get(lang) if isinstance(data, dict) else None
    return labels if isinstance(labels, dict) else {}


def _replacement(match: re.Match[str], profile: dict, *, patched: bool = False) -> str:
    original = match.group(0).split(":", 1)[1].lstrip()
    if patched:
        original = original[1:]
    values = {
        "__ORIGINAL__": original,
        "__ITEM__": match.group(3),
        "__JSX__": match.group(1),
    }
    return re.sub(
        r"__ORIGINAL__|__ITEM__|__JSX__",
        lambda token: values[token.group(0)],
        profile["replacement"],
    )


def _suffix(profile: dict, labels: dict[str, str]) -> str:
    encoded = json.dumps(
        labels, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    encoded = encoded.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return "\n" + MARKER + profile["suffix"].replace("__LABELS__", encoded)


def apply_text(
    text: str, profile: dict, labels: dict[str, str], lang: str = "zh-CN"
) -> tuple[str, str]:
    """返回 (新文本, 状态)。状态：already / patched / skip。"""
    if not profile or not labels:
        return text, "skip"
    if not re.search(profile["module_pattern"], text):
        return text, "skip"
    if MARKER in text:
        return text, "already"

    matches = list(re.finditer(profile["anchor_pattern"], text))
    if len(matches) != 1:
        return text, "skip"
    match = matches[0]
    if not re.search(re.escape(match.group(3)) + r'\.type\s*===\s*"skill"', text):
        return text, "skip"

    updated = text[: match.start()] + _replacement(match, profile) + text[match.end():]
    return updated + _suffix(profile, labels), "patched"


def revert_text(text: str, profile: dict) -> tuple[str, bool]:
    """撤掉补丁：去掉末尾的 label 表，把渲染行还原成原始调用。

    正向替换的尾巴是固定形状 ``<ITEM>.type==="skill"&&<ITEM>.anthropicDefined===!0&&…``，
    先定位它，再往回找最近的 ``children:[``，中间那段就是原始调用。
    不用「从模板反编正则」——``.*?`` 会一口气跨过整个文件匹配到别处。
    """
    if MARKER not in text:
        return text, False
    body, _tail = text.split("\n" + MARKER, 1)

    tail_re = re.compile(
        r'([A-Za-z_$][\w$]*)\.type==="skill"&&\1\.anthropicDefined===!0&&'
    )
    m = tail_re.search(body)
    if m is None:
        return body, False

    start = m.start()
    open_idx = body.rfind("children:[", 0, start)
    if open_idx < 0 or start == 0 or body[start - 1] != ",":
        return body, False
    close_idx = body.find("]", start)
    if close_idx < 0:
        return body, False

    original = body[open_idx + len("children:[") : start - 1]
    return body[:open_idx] + "children:" + original + body[close_idx + 1 :], True


def has_marker(text: str) -> bool:
    return MARKER in text


def has_marker_in_asar(asar_path: Path) -> bool:
    """asar 里有没有我们的斜杠菜单补丁（决定「恢复原样」要不要重打包）。

    **只解析一次 header**，然后按 offset 逐条 seek 读取。

    早期实现用的是 asar.read_entry_file()，它每次都会重新解析整个 header
    （ion-dist.asar 的 header 有 1.28MB）。4000 多个 .js 条目 = O(n²)，
    实测 12–16 秒。这个函数在状态栏刷新里被调用，直接导致菜单栏图标
    几十秒不出现，所以必须保持线性。
    """
    from . import asar

    if not asar_path.is_file():
        return False
    try:
        header_size, _s, header = asar.read_header_file(asar_path)
    except Exception:
        return False

    marker = MARKER.encode()
    unpacked_root = Path(str(asar_path) + ".unpacked")
    try:
        with asar_path.open("rb") as f:
            for rel, entry in asar.walk(header):
                if not rel.endswith(".js"):
                    continue
                if entry.get("unpacked"):
                    try:
                        body = (unpacked_root / Path(*rel.split("/"))).read_bytes()
                    except OSError:
                        continue
                else:
                    f.seek(asar.file_offset(header_size, entry))
                    body = f.read(int(entry["size"]))
                if marker in body:
                    return True
    except OSError:
        return False
    return False


def patch_dir(assets_dir: Path, lang: str, log=print) -> int:
    """在解包后的 assets/v1 目录里打补丁，返回改动文件数。"""
    profile = load_profile()
    labels = load_labels(lang)
    if not profile or not labels:
        log("跳过斜杠命令双语菜单：缺少词表")
        return 0
    if not assets_dir.is_dir():
        return 0

    candidates = []
    for js in sorted(assets_dir.glob("*.js")):
        try:
            text = js.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if re.search(profile["module_pattern"], text):
            candidates.append((js, text))
    if len(candidates) != 1:
        log(f"跳过斜杠命令双语菜单：找到 {len(candidates)} 个候选文件")
        return 0

    js, text = candidates[0]
    updated, status = apply_text(text, profile, labels, lang)
    if status == "already":
        log("斜杠命令双语菜单已是最新")
        return 0
    if status != "patched":
        log("跳过斜杠命令双语菜单：锚点结构已变化")
        return 0
    js.write_text(updated, encoding="utf-8")
    log(f"斜杠命令双语菜单已应用：{js.name}（{len(labels)} 条）")
    return 1


def revert_dir(assets_dir: Path, log=print) -> int:
    profile = load_profile()
    if not assets_dir.is_dir():
        return 0
    done = 0
    for js in sorted(assets_dir.glob("*.js")):
        try:
            text = js.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if not has_marker(text):
            continue
        updated, changed = revert_text(text, profile)
        if changed:
            js.write_text(updated, encoding="utf-8")
            done += 1
    if done:
        log(f"已撤除斜杠命令双语菜单（{done} 个文件）")
    return done
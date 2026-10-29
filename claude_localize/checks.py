"""状态查看与自检（4.29+ asar 布局感知）。"""
from __future__ import annotations

import json
from pathlib import Path

from . import (
    asar, backup, cleanup, discover, jsonutil, merger, native_strings, paths,
    shell, signer,
)


def _pack_dir() -> Path:
    return paths.PROJECT_ROOT / "packs" / paths.TARGET_LOCALE


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def current_frontend_en(inst: discover.ClaudeInstall) -> dict[str, str]:
    """当前版本的主界面英文词表（4.29 从 ion-dist.asar 里读）。"""
    loose = inst.frontend_en()
    if loose.exists():
        return jsonutil.load_dict(loose)
    data = asar.read_entry_file(
        paths.resources_of(inst.app) / "ion-dist.asar", "i18n/en-US.json"
    )
    return json.loads(data.decode("utf-8")) if data else {}


def current_desktop_en(inst: discover.ClaudeInstall) -> dict[str, str]:
    """桌面壳层英文词表（3.x 散装 / 4.29 在 app.asar 内）。"""
    loose = inst.desktop_en()
    if loose.exists():
        return jsonutil.load_dict(loose)
    data = asar.read_entry_file(inst.asar(), "resources/i18n/en-US.json")
    return json.loads(data.decode("utf-8")) if data else {}


def shell_state(inst: discover.ClaudeInstall) -> dict:
    """外壳汉化状态：语言文件是否在 asar 内、白名单是否注册。"""
    state = {"locale_file": False, "registered": False, "chinese": 0}
    app_asar = inst.asar()
    if not app_asar.is_file():
        return state
    try:
        _hs, _s, header = asar.read_header_file(app_asar)
    except Exception:
        return state

    lang = paths.TARGET_LOCALE
    if asar.find_entry(header, f"resources/i18n/{lang}.json") is not None:
        state["locale_file"] = True
        raw = asar.read_entry_file(app_asar, f"resources/i18n/{lang}.json")
        if raw:
            try:
                data = json.loads(raw.decode("utf-8"))
                state["chinese"] = sum(
                    1 for v in data.values()
                    if isinstance(v, str) and any("一" <= ch <= "鿿" for ch in v)
                )
            except Exception:
                pass

    for rel, entry in asar.walk(header):
        if not rel.endswith(".js"):
            continue
        try:
            body = asar.read_entry_file(app_asar, rel) or b""
        except Exception:
            continue
        text = body.decode("utf-8", "ignore")
        if "getPreferredSystemLanguages" in text:
            state["registered"] = shell.is_whitelisted(text, lang)
            break
    return state


def status(verbose: bool = True) -> dict:
    inst = discover.detect()
    patched = discover.is_patched(inst)
    b = backup.latest_backup(inst.app)
    version = None
    vf = _pack_dir() / ".upstream-version"
    if vf.exists():
        version = vf.read_text(encoding="utf-8").strip().strip('"')

    frontend_gap = desktop_gap = None
    try:
        en_f = current_frontend_en(inst)
        merged_f, st_f, _ = merger.merge(
            en_f,
            jsonutil.load_dict(_pack_dir() / "frontend.json"),
            custom=merger.load_custom(
                paths.custom_path()
            ),
        )
        frontend_gap = (st_f.total - st_f.counts.get("english", 0), st_f.total)

        en_d = current_desktop_en(inst)
        if en_d:
            _, st_d, _ = merger.merge(
                en_d, jsonutil.load_dict(_pack_dir() / "desktop.json")
            )
            desktop_gap = (st_d.total - st_d.counts.get("english", 0), st_d.total)
    except Exception:
        pass

    info = {
        "version": inst.version,
        "locale": inst.locale,
        "app": str(inst.app),
        "patched": patched,
        "backup": str(b) if b else None,
        "backup_kind": backup.backup_kind(inst.app),
        "pack_version": version,
        "frontend": frontend_gap,
        "desktop": desktop_gap,
        "shell": shell_state(inst),
        "native_strings": native_strings.installed(inst.app),
        "residue": [str(r.path) for r in cleanup.scan(inst.app)],
    }

    if verbose:
        print(f"Claude 版本    : {info['version']}")
        print(f"当前语言       : {info['locale']}")
        print(f"补丁状态       : {'已打补丁' if patched else '未打补丁'}")
        if info["backup"]:
            kind = {"pristine": "官方原版", "patched": "已汉化"}.get(
                info["backup_kind"], "未知"
            )
            print(f"备份           : {info['backup']}（{kind}）")
        else:
            print("备份           : 无")
        print(f"词表版本       : {version or '未记录'}")
        if frontend_gap:
            a, bb = frontend_gap
            print(f"主界面覆盖率   : {a}/{bb} ({a / bb * 100:.1f}%)")
        if desktop_gap:
            a, bb = desktop_gap
            print(f"桌面层覆盖率   : {a}/{bb} ({a / bb * 100:.1f}%)")
        st = info["shell"]
        if st["locale_file"]:
            reg = "已注册" if st["registered"] else "未注册（配置重置后会退回英文）"
            print(f"桌面壳层       : {st['chinese']} 条中文，语言白名单{reg}")
        else:
            print("桌面壳层       : 未汉化（未写入语言文件）")
        print(f"原生字符串     : {', '.join(info['native_strings']) or '未安装'}")
        if info["residue"]:
            print(f"可清理残留     : {len(info['residue'])} 项（运行 clean 清理）")

    return info


def verify(verbose: bool = True) -> bool:
    """安装后自检（4.29+ 从 asar 内部读，不再看散装文件）。"""
    inst = discover.detect()
    checks: list[tuple[str, bool]] = []

    checks.append(("Claude.app 存在", inst.app.exists()))

    frontend_asar = paths.resources_of(inst.app) / "ion-dist.asar"
    checks.append(("主界面中文词表存在",
                   frontend_asar.is_file() and asar.has_entry(frontend_asar, "i18n/zh-CN.json")))

    # 桌面壳层：安装时开没开增强，用「有没有语言文件」判断
    st = shell_state(inst)
    if st["locale_file"]:
        checks.append(("桌面壳层中文词表存在", st["chinese"] > 0))
        checks.append(("外壳语言白名单已注册", st["registered"]))
    else:
        checks.append(("桌面壳层保持英文（未启用增强）", True))

    checks.append(("原生字符串已安装", bool(native_strings.installed(inst.app))
                   or not native_strings.pack_file().exists()))

    checks.append(("语言白名单已注册（前端）", _frontend_whitelist_ok(inst)))

    ok, _ = signer.verify(inst.app)
    checks.append(("签名有效", ok))

    if verbose:
        for desc, passed in checks:
            print(f"  [{'通过' if passed else '失败'}] {desc}")
    return all(p for _, p in checks)


def _frontend_whitelist_ok(inst: discover.ClaudeInstall) -> bool:
    from . import frontend_locale as locale

    frontend_asar = paths.resources_of(inst.app) / "ion-dist.asar"
    if frontend_asar.is_file():
        try:
            _hs, _s, header = asar.read_header_file(frontend_asar)
        except Exception:
            return False
        entries = [(r, e) for r, e in asar.walk(header) if r.endswith(".js")]
        # 语言数组通常在体积大的 shared chunk 里；先按体积从大到小找，
        # 命不中再全量兜底（3935 个文件全读一遍要上百 MB）。
        entries.sort(key=lambda it: int(it[1].get("size", 0)), reverse=True)
        for rel, _entry in entries:
            try:
                body = asar.read_entry_file(frontend_asar, rel) or b""
            except Exception:
                continue
            if b'"en-US"' not in body:
                continue
            text = body.decode("utf-8", "ignore")
            lst = locale.extract_lang_list(text)
            if lst:
                return "zh-CN" in lst
        return False

    for js in inst.assets().glob("*.js"):
        try:
            text = js.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        lst = locale.extract_lang_list(text)
        if lst:
            return "zh-CN" in lst
    return False
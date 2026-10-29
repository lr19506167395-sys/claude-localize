#!/usr/bin/env python3
"""在临时目录里完整跑一遍安装流程并逐项验证——不碰 /Applications。

用法：
    env -u PYTHONHOME -u PYTHONPATH .venv/bin/python tools/isolated_verify.py [--no-shell]

流程：ditto 官方 app → 清理残留 → 打补丁 → 校验 asar/plist/摘要槽/签名/语言文件
→ 用 node --check 校验被改过的 JS 语法。
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from claude_localize import (  # noqa: E402
    asar, checks, cleanup, discover, installer, integrity, native_strings, paths,
    shell, signer,
)


def node_check(bundle: bytes, label: str) -> bool:
    node = shutil.which("node")
    if not node:
        print(f"  [跳过] 没有 node，无法语法检查 {label}")
        return True
    with tempfile.NamedTemporaryFile("wb", suffix=".js", delete=False) as f:
        f.write(bundle)
        tmp = Path(f.name)
    try:
        r = subprocess.run([node, "--check", str(tmp)], capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  [失败] {label} 语法错误：{r.stderr.strip()[:300]}")
        return r.returncode == 0
    finally:
        tmp.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-shell", action="store_true")
    ap.add_argument("--keep", action="store_true", help="保留临时目录便于排查")
    args = ap.parse_args()

    with_asar = not args.no_shell
    src = paths.APP_PATH
    base = Path(tempfile.mkdtemp(prefix="claude-verify."))
    tmp = base / "Claude.app"
    problems: list[str] = []

    print(f"源 app : {src}")
    print(f"临时区 : {base}")
    print(f"增强   : {'开（会改 app.asar）' if with_asar else '关（不改 app.asar，除非要撤掉旧的外壳汉化）'}")

    print("\n[1/6] ditto 复制…")
    subprocess.run(["ditto", str(src), str(tmp)], check=True)
    orig_size = {
        n: (tmp / "Contents/Resources" / n).stat().st_size
        for n in ("app.asar", "ion-dist.asar")
    }
    orig_app_asar_mtime = (tmp / "Contents/Resources/app.asar").stat().st_mtime
    had_shell = shell.has_artifacts(tmp / "Contents/Resources/app.asar")
    print(f"  源 app.asar 里已有外壳汉化：{'是' if had_shell else '否'}")

    print("\n[2/6] 清理历史残留…")
    for item in cleanup.clean(tmp):
        pass

    print("\n[3/6] 打补丁…")
    report = installer.patch_tree(tmp, paths.TARGET_LOCALE, with_asar=with_asar)
    print(report.render())

    print("\n[4/6] 结构校验…")
    try:
        installer._verify_tree(tmp, paths.TARGET_LOCALE, with_asar=with_asar)
    except SystemExit as exc:
        problems.append(str(exc))

    res = tmp / "Contents/Resources"

    # asar 体积不能暴涨（共享数据块去重生效）；
    # 关闭增强时变小是正常的——撤掉语言文件和 DOM 脚本
    for name, before in orig_size.items():
        after = (res / name).stat().st_size
        delta = after - before
        shrink_ok = (not with_asar) and delta < 0
        flag = "OK" if (abs(delta) < before * 0.05 or shrink_ok) else "异常"
        print(f"  {name:14} {before/1e6:8.1f}MB → {after/1e6:8.1f}MB ({delta/1e6:+.1f}MB) {flag}")
        if flag == "异常":
            problems.append(f"{name} 体积变化过大：{delta/1e6:+.1f}MB")

    # 每个条目 integrity + plist 哈希 + 摘要槽
    for name in ("app.asar", "ion-dist.asar"):
        p = res / name
        _hs, _s, header = asar.read_header_file(p)
        bad = 0
        data, hs, _h = asar.load(p)
        import hashlib

        for rel, entry in asar.walk(header):
            if entry.get("unpacked") or "integrity" not in entry:
                continue
            body = asar.read_file(data, hs, entry)
            if entry["integrity"]["hash"] != hashlib.sha256(body).hexdigest():
                bad += 1
                if bad <= 3:
                    print(f"  integrity 不符：{name}:{rel}")
        print(f"  {name:14} integrity 全量校验：{'通过' if not bad else f'{bad} 条不符'}")
        if bad:
            problems.append(f"{name} 有 {bad} 条 integrity 不符")

    ok, msg = integrity.verify_digest_slot(tmp)
    print(f"  摘要槽            : {'通过' if ok else '失败'} {msg}")
    if not ok:
        problems.append(msg)

    ok, err = signer.verify(tmp)
    print(f"  签名              : {'通过' if ok else '失败 ' + err[:200]}")
    if not ok:
        problems.append("签名校验失败")
    if not signer.has_library_validation_disabled(tmp):
        problems.append("entitlements 丢失")
    print(f"  entitlements      : {'通过' if signer.has_library_validation_disabled(tmp) else '丢失'}")

    # 语言文件
    front = asar.read_entry_file(res / "ion-dist.asar", "i18n/zh-CN.json")
    if front:
        d = json.loads(front)
        cn = sum(1 for v in d.values() if isinstance(v, str) and any("一" <= c <= "鿿" for c in v))
        print(f"  主界面 zh-CN      : {len(d)} 条 / 中文 {cn}")
        if cn < 1000:
            problems.append("主界面中文条目过少")
    else:
        problems.append("ion-dist.asar 缺少 i18n/zh-CN.json")

    dyn = asar.read_entry_file(res / "ion-dist.asar", "i18n/dynamic/zh-CN.json")
    print(f"  dynamic zh-CN     : {'有' if dyn else '缺失'}")
    if dyn is None:
        problems.append("缺少 dynamic/zh-CN.json")

    if with_asar:
        app_after = (res / "app.asar").stat().st_mtime
        if app_after == orig_app_asar_mtime:
            problems.append("开了增强但 app.asar 没被改")
        st = checks.shell_state(discover.ClaudeInstall(tmp, "4.29.0", "zh-CN"))
        print(f"  外壳语言文件      : {'有' if st['locale_file'] else '缺失'}，中文 {st['chinese']} 条")
        print(f"  外壳白名单        : {'已注册' if st['registered'] else '未注册'}")
        if not st["locale_file"]:
            problems.append("外壳语言文件缺失")
        if not st["registered"]:
            problems.append("外壳白名单未注册")

        # 语法检查改过的 JS
        print("\n[5/6] 语法检查…")
        _hs, _s, header = asar.read_header_file(res / "app.asar")
        checked = 0
        for rel, entry in asar.walk(header):
            if not rel.endswith(".js"):
                continue
            body = asar.read_entry_file(res / "app.asar", rel)
            if body and b'getPreferredSystemLanguages' in body:
                if not node_check(body, f"app.asar:{rel}"):
                    problems.append(f"{rel} 语法错误")
                checked += 1
        print(f"  检查了 {checked} 个主进程 bundle")
    else:
        st = checks.shell_state(discover.ClaudeInstall(tmp, "4.29.0", "zh-CN"))
        print(f"  外壳语言文件      : {'还在' if st['locale_file'] else '已移除'}")
        print(f"  外壳白名单        : {'还注册着' if st['registered'] else '已摘除'}")
        if st["locale_file"] or st["registered"]:
            problems.append("关了增强，但外壳汉化没撤干净")
        if not had_shell:
            app_after = (res / "app.asar").stat().st_mtime
            if app_after != orig_app_asar_mtime:
                problems.append("源里没有外壳汉化，却还是改了 app.asar")
            else:
                print("  app.asar 未被修改（符合预期）")

    lproj = native_strings.installed(tmp)
    print(f"  原生 lproj        : {', '.join(lproj) or '无'}")
    if not lproj:
        problems.append("原生字符串未安装")

    print("\n[6/6] 结论")
    if problems:
        for p in problems:
            print(f"  ✗ {p}")
        print(f"\n临时副本保留在：{base}")
        return 1

    print("  ✓ 全部通过")
    if not args.keep:
        shutil.rmtree(base, ignore_errors=True)
        print("  临时副本已删除")
    else:
        print(f"  临时副本保留在：{base}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
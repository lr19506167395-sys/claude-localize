"""还原原样：优先用备份换回，然后把我们打进去的东西撤干净。

两条现实约束（2026-10-29 实测）：

1. 备份不一定干净——它可能是上一轮汉化后的状态。只做「整体换回」，
   Claude 打开还是中文。
2. 撤补丁会改包内文件（app.asar 重打包、删 .lproj），
   所以改完必须同步 plist 哈希 / 摘要槽并重新签名，否则启动即 SIGTRAP。
"""
from __future__ import annotations

from . import backup, cleanup, discover, integrity, native_strings, paths, signer


def restore(
    *, dry_run: bool = False, verbose: bool = True, launch: bool = False,
    clean: bool = False,
) -> str:
    """返回 "already"（本来就是原版）/ "dry-run" / "restored"。"""
    log = print if verbose else (lambda *a, **k: None)
    inst = discover.detect()
    lang = paths.TARGET_LOCALE

    b = backup.best_backup(inst.app)
    patched = discover.is_patched(inst)

    if b is None and not patched:
        log("当前 Claude 是官方原版：既没有补丁，也没有备份，无需还原。")
        if launch:
            inst.launch()
        return "already"

    cur_ver = backup.bundle_version(inst.app)
    bak_ver = backup.bundle_version(b) if b is not None else cur_ver
    if b is not None:
        if backup.version_key(bak_ver) < backup.version_key(cur_ver):
            raise SystemExit(
                f"备份版本 {bak_ver} 低于当前版本 {cur_ver}，恢复会导致降级。\n"
                "请改为重新安装 Claude，或使用对应版本的备份。"
            )
        if not backup.is_complete_backup(b):
            raise SystemExit(f"备份不完整，无法安全还原：{b}")

    if dry_run:
        log(f"[dry-run] 备份：{b or '无（直接就地撤补丁）'}")
        log("[dry-run] 将换回备份、撤掉外壳语言文件/DOM 注入/原生字符串，重签名，locale 改回 en-US")
        return "dry-run"

    if inst.is_running():
        log("退出 Claude…")
        inst.quit()

    if b is not None:
        if not backup.is_pristine(b):
            log("注意：这份备份本身是汉化状态，还原后可能仍有残留（javaht 硬编码替换无法还原）")
        log(f"从备份恢复：{b}（版本 {bak_ver}）")
        backup.restore_backup(inst.app, b)
    else:
        log("没有可用备份：直接在现有 app 上撤掉补丁")

    # 备份本身可能就是汉化状态，必须主动撤掉我们打进去的东西
    changed = False
    try:
        from . import installer

        changed = installer.strip_patch(inst.app, lang, log=log, pin_locale="en-US")
    except SystemExit as exc:
        log(f"警告：撤除 app.asar 补丁失败（{exc}）")

    removed_native = native_strings.remove(inst.app, log=log)
    if clean:
        cleanup.clean(inst.app, log=log)

    signer.clear_quarantine(inst.app)
    if changed or removed_native:
        log("包内容有改动，重新签名…")
        signer.resign(inst.app)

    problems = integrity.verify_app(inst.app)
    if problems:
        for item in problems[:8]:
            log(f"  ✗ {item}")
        raise SystemExit(
            "还原后的 app 完整性校验未通过，Claude 会启动崩溃。\n"
            "建议重装官方 Claude。"
        )
    ok, err = signer.verify(inst.app)
    if not ok:
        raise SystemExit(f"还原后签名校验失败：{err}\n建议重装官方 Claude。")

    log("完整性 + 签名校验：通过")
    inst.write_locale("en-US")
    log("已还原为英文。重新打开 Claude 生效。")
    if launch:
        inst.launch()
    return "restored"
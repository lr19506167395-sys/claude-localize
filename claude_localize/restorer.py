"""还原英文原版：优先整体换回原版备份，必要时只修改临时副本。

普通还原采用 javaht 的思路：原版备份换回后只改 locale，不再撤补丁、
删 lproj 或重签。不同的是先复制、验证再交换，原备份始终保留。
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from . import backup, cleanup, discover, integrity, native_strings, paths, signer


def _verify_problems(app: Path) -> list[str]:
    problems = integrity.verify_app(app)
    ok, err = signer.verify(app)
    if not ok:
        problems.append(f"签名校验失败：{err}")
    return problems


def _restore_patched_backup(
    inst, b: Path, *, clean: bool, log
) -> None:
    """只有非原版备份时，在副本撤补丁；原版备份走 backup.restore_backup。"""
    from . import installer

    with tempfile.TemporaryDirectory(prefix="claude-restore.") as td:
        tmp = Path(td) / "Claude.app"
        backup._copy_tree(b, tmp)
        backup._drop_pristine_marker(tmp)
        layout = integrity.probe_layout(tmp)
        if layout:
            raise SystemExit(
                "撤补丁前的完整性预检未通过，未修改真实 app：\n"
                + "\n".join(f"  ✗ {p}" for p in layout)
                + "\n请重装官方 Claude。"
            )
        installer.strip_patch(tmp, paths.TARGET_LOCALE, log=log, pin_locale="en-US")
        native_strings.remove(tmp, log=log)
        if clean:
            cleanup.clean(tmp, log=log)
        signer.clear_quarantine(tmp)
        signer.resign(tmp)
        problems = _verify_problems(tmp)
        if problems:
            raise SystemExit(
                "还原副本未通过验证，真实 app 未修改：\n"
                + "\n".join(f"  ✗ {p}" for p in problems[:8])
            )
        backup.swap_in(inst.app, tmp, b)


def restore(
    *, dry_run: bool = False, verbose: bool = True, launch: bool = False,
    clean: bool = False,
) -> str:
    """返回 "already"（本来就是原版）/ "dry-run" / "restored"。"""
    log = print if verbose else (lambda *a, **k: None)
    inst = discover.detect()
    b = backup.best_backup(inst.app)
    patched = discover.is_patched(inst)

    if b is None and not patched:
        log("当前 Claude 是官方原版：既没有补丁，也没有备份，无需还原。")
        if launch:
            inst.launch()
        return "already"

    cur_ver = backup.bundle_version(inst.app)
    if b is not None:
        bak_ver = backup.bundle_version(b)
        if backup.version_key(bak_ver) < backup.version_key(cur_ver):
            raise SystemExit(
                f"备份版本 {bak_ver} 低于当前版本 {cur_ver}，恢复会导致降级。\n"
                "请重新安装官方 Claude。"
            )
        if not backup.is_complete_backup(b):
            raise SystemExit(f"备份不完整，无法安全还原：{b}")
        pristine = backup.is_pristine(b)
        if not pristine:
            log("这份备份含汉化内容，将只在临时副本撤补丁，原备份保留。")
    else:
        if not patched:
            log("当前已是官方原版，没有需要还原的补丁。")
            if launch:
                inst.launch()
            return "already"
        raise SystemExit(
            "没有同版本的可用备份，拒绝直接修改当前 Claude。\n"
            "请重新安装官方 Claude；它同时会移除本工具及历史补丁。"
        )

    if dry_run:
        log(f"[dry-run] 备份：{b}")
        log("[dry-run] 将复制到临时目录验证后交换，原备份保留；成功后才写 locale en-US")
        return "dry-run"

    if inst.is_running():
        log("退出 Claude…")
        if not inst.quit(wait=True):
            raise SystemExit("Claude 未能退出，请手动退出后重试")

    log(f"从备份恢复：{b}")
    if backup.is_pristine(b):
        backup.restore_backup(inst.app, b)
    else:
        _restore_patched_backup(inst, b, clean=clean, log=log)

    inst.write_locale("en-US")
    log("已还原为英文。原备份仍保留。")
    if launch:
        inst.launch()
    return "restored"

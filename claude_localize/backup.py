"""整个 .app 的备份与恢复（全部用重命名，原子且秒级）。"""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path


BACKUP_GLOB = "Claude.backup-before-zh-*.app"
# 备份里放一个标记文件，说明「这份备份是官方原版」。
# 还原前会把它删掉，所以不会污染包内容、也不会影响签名封条。
PRISTINE_MARKER = ".claude-localize-pristine"


def _rmtree(path: Path) -> None:
    """删除目录；/Applications 下常有 root 属主或 uchg 标记的残留文件。"""
    subprocess.run(["chflags", "-R", "nouchg", str(path)], capture_output=True)
    try:
        shutil.rmtree(path)
        return
    except (PermissionError, OSError):
        pass
    result = subprocess.run(
        ["sudo", "-n", "rm", "-rf", str(path)], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise SystemExit(
            f"无法删除 {path}（{result.stderr.strip()}）。\n"
            "请手动执行：sudo rm -rf " + str(path)
        )


def list_backups(app: Path) -> list[Path]:
    """按修改时间升序返回备份（最旧的在前）。

    不能按文件名排序：「zh-2026…」字典序在「zh-CN-2026…」之前，
    会把新备份当成旧的删掉。
    """
    items = list(app.parent.glob(BACKUP_GLOB))
    return sorted(items, key=lambda p: p.stat().st_mtime)


def is_complete_backup(path: Path) -> bool:
    """判断备份是否完整（必须含 Info.plist、Frameworks、app.asar）。"""
    contents = path / "Contents"
    return (
        (contents / "Info.plist").is_file()
        and (contents / "Frameworks").is_dir()
        and (contents / "Resources" / "app.asar").is_file()
    )


def is_pristine(path: Path) -> bool:
    """这份备份是不是官方原版（打补丁之前的状态）。"""
    return (path / PRISTINE_MARKER).is_file()


def mark_pristine(path: Path, note: str = "official") -> None:
    try:
        (path / PRISTINE_MARKER).write_text(note + "\n", encoding="utf-8")
    except OSError:
        pass


def _drop_pristine_marker(path: Path) -> None:
    """换回原位前去掉标记，否则它会被带进 Claude.app 里。"""
    marker = path / PRISTINE_MARKER
    if marker.exists():
        try:
            marker.unlink()
        except OSError:
            pass


def _free_trash_name(app: Path) -> Path:
    """为要被替换掉的旧 app 找一个不冲突的临时名字。"""
    base = app.with_name(app.name + ".replaced")
    if not base.exists():
        return base
    # 先尝试删掉旧的残留
    try:
        _rmtree(base)
        return base
    except SystemExit:
        pass
    # 删不掉就换个带时间戳的名字，绝不中断安装
    alt = app.with_name(f"{app.name}.replaced-{time.strftime('%Y%m%d-%H%M%S')}")
    return alt


def create_backup(app: Path, *, pristine: bool = False) -> Path:
    """把当前 Claude.app 重命名为备份（原子、秒级，不复制 620MB）。

    ``pristine=True`` 表示「正准备打补丁的这份是官方原版」，会写一个标记文件，
    这样「恢复原样」能挑到真正干净的备份，状态栏也能显示出来。

    这会暂时让 app 路径消失，调用方必须紧接着 put_in_place()。
    """
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = app.parent / f"Claude.backup-before-zh-{stamp}.app"
    if dest.exists():
        raise SystemExit(f"备份目标已存在：{dest}")
    app.rename(dest)
    if pristine:
        mark_pristine(dest)
    return dest


def put_in_place(app: Path, source: Path) -> None:
    """把打好补丁的 source 重命名到 app 路径（原子）。"""
    if app.exists():
        trash = _free_trash_name(app)
        app.rename(trash)
        source.rename(app)
        try:
            shutil.rmtree(trash)
        except (PermissionError, OSError):
            print(f"提示：临时目录未能自动删除，可稍后执行 sudo rm -rf {trash}")
    else:
        source.rename(app)


def restore_backup(app: Path, backup: Path) -> None:
    """用备份恢复 app（两次重命名，原子）。"""
    if not backup.exists():
        raise SystemExit(f"备份不存在：{backup}")

    # 标记文件是我们加的，不能带进 Claude.app
    _drop_pristine_marker(backup)

    trash: Path | None = None
    if app.exists():                     # 上一次安装失败时 app 可能不在了
        trash = _free_trash_name(app)
        app.rename(trash)
    backup.rename(app)
    if trash is not None:
        try:
            shutil.rmtree(trash)
        except (PermissionError, OSError):
            print(f"提示：临时目录未能自动删除，可稍后执行 sudo rm -rf {trash}")


def latest_pristine(app: Path) -> Path | None:
    """最新的一份**官方原版**备份（还原时优先用它）。"""
    items = [p for p in list_backups(app) if is_complete_backup(p) and is_pristine(p)]
    if not items:
        return None
    return max(items, key=lambda p: (version_key(bundle_version(p)), p.stat().st_mtime))


def best_backup(app: Path) -> Path | None:
    """优先返回原版备份；没有原版才退回最新备份。"""
    return latest_pristine(app) or latest_backup(app)


def backup_kind(app: Path) -> str:
    """给界面用：pristine / patched / none。"""
    b = best_backup(app)
    if b is None:
        return "none"
    return "pristine" if is_pristine(b) else "patched"


def latest_backup(app: Path) -> Path | None:
    """返回版本号最高的**完整**备份（不用 mtime，避免把 Claude 降级）。

    跳过不完整备份：以前 prune 中途失败会留下缺 Frameworks 的半截目录，
    拿它去还原等于把 Claude 弄坏。

    版本相同时按 **mtime** 取最新：不能退回按文件名比较——
    「zh-2026…」字典序在「zh-CN-2026…」之前，会选中旧的。
    """
    items = [p for p in list_backups(app) if is_complete_backup(p)]
    if not items:
        return None
    return max(items, key=lambda p: (version_key(bundle_version(p)), p.stat().st_mtime))


def bundle_version(app: Path) -> str:
    plist = app / "Contents" / "Info.plist"
    if not plist.exists():
        return "0"
    result = subprocess.run(
        ["/usr/libexec/PlistBuddy", "-c",
         "Print :CFBundleShortVersionString", str(plist)],
        capture_output=True, text=True,
    )
    return result.stdout.strip() or "0"


def version_key(version: str) -> tuple[int, ...]:
    parts = []
    for chunk in version.split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def is_officially_signed(app: Path) -> bool:
    """判断 app 是否仍带官方 Developer ID 签名。"""
    from . import signer

    return bool(signer.team_identifier(app))


def prune_old_backups(app: Path, keep: int = 1) -> list[Path]:
    """清理旧备份，返回被删除的列表。

    规则：
      * 不完整的备份**一律删**（留着只会在还原时坑人）
      * ``latest_backup()`` 会选中的那个**永不删**（否则还原会降级）
      * 其余按 mtime 从旧到新删，只保留最新 keep 个
    """
    items = list_backups(app)                      # 旧 → 新
    if not items:
        return []

    keeper = latest_backup(app)
    pristine = latest_pristine(app)
    removed: list[Path] = []

    def drop(path: Path) -> bool:
        try:
            shutil.rmtree(path)
            removed.append(path)
            return True
        except (PermissionError, OSError) as exc:
            print(f"警告：备份未删干净 {path}（{exc}）")
            return False

    # 1. 先清不完整的
    complete = []
    for item in items:
        if is_complete_backup(item):
            complete.append(item)
        elif item != keeper:
            drop(item)

    # 2. 完整备份里最多留 keep 个，且必须包含 keeper（版本最高的那个）
    survivors: set[Path] = set()
    if keeper is not None:
        survivors.add(keeper)
    if pristine is not None:
        survivors.add(pristine)   # 原版备份是无价的，任何情况下都不删
    for item in reversed(complete):            # 新 → 旧
        if len(survivors) >= keep:
            break
        survivors.add(item)
    for item in complete:
        if item not in survivors:
            drop(item)

    return removed
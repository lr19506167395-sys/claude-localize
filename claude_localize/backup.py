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

_SIGNED_CACHE: dict[tuple[str, int], bool] = {}
_VERSION_CACHE: dict[tuple[str, int], str] = {}


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
    """这份备份是不是官方原版（打补丁之前的状态）。

    标记文件之外，还认「仍带官方 Developer ID 签名」这一条：
    我们每次打完补丁都会 ad-hoc 重签（TeamIdentifier 变成 not set），
    所以带着真实 TeamIdentifier 的备份必然是没被我们碰过的。
    少了这条，标记文件万一没写成功，原版备份就会被误当成汉化备份。
    """
    if (path / PRISTINE_MARKER).is_file():
        return True
    return is_officially_signed(path)


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
    """版本最高的**官方原版**备份（不看当前 app 版本）。"""
    items = [p for p in list_backups(app) if is_complete_backup(p) and is_pristine(p)]
    if not items:
        return None
    return max(items, key=lambda p: (version_key(bundle_version(p)), p.stat().st_mtime))


def _at_version(items: list[Path], version: str) -> list[Path]:
    """筛出与 ``version`` 版本号相同的备份。

    还原/重打补丁都必须用同版本备份：拿旧版本顶上等于把 Claude 降级，
    新版布局和旧备份对不上时还会启动崩溃。宁可换不了，也不能换错。
    """
    want = version_key(version)
    return [p for p in items if version_key(bundle_version(p)) == want]


def best_backup(app: Path) -> Path | None:
    """还原/重打补丁该用哪份备份：同版本里优先原版，其次最新。

    早期实现只判「是不是原版」不看版本，于是 Claude 自动更新后，
    旧版本的原版备份永远压着新版本的干净备份，还原被降级保护拦下、
    重装补丁也报错——用户明明有可用备份却用不了。
    """
    same = _at_version(
        [p for p in list_backups(app) if is_complete_backup(p)],
        bundle_version(app),
    )
    if not same and not (app / "Contents" / "Info.plist").exists():
        # app 本身不在（安装过程中被临时改名，或替换失败）：
        # 这时没有「当前版本」可言，退回版本最高的完整备份，先让 Claude 回来。
        complete = [p for p in list_backups(app) if is_complete_backup(p)]
        if complete:
            newest_version = max(
                (bundle_version(p) for p in complete), key=version_key
            )
            same = _at_version(complete, newest_version)
    if not same:
        return None
    pristine = [p for p in same if is_pristine(p)]
    pool = pristine or same

    def newest(p: Path) -> tuple[tuple[int, ...], float]:
        return (version_key(bundle_version(p)), p.stat().st_mtime)

    return max(pool, key=newest)


def backup_kind(app: Path) -> str:
    """给界面用：pristine / patched / none（指 ``best_backup()`` 那一份）。"""
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
    try:
        stamp = plist.stat().st_mtime_ns
    except OSError:
        stamp = 0
    key = (str(app), stamp)
    hit = _VERSION_CACHE.get(key)
    if hit is not None:
        return hit
    result = subprocess.run(
        ["/usr/libexec/PlistBuddy", "-c",
         "Print :CFBundleShortVersionString", str(plist)],
        capture_output=True, text=True,
    )
    version = result.stdout.strip() or "0"
    _VERSION_CACHE[key] = version
    return version


def version_key(version: str) -> tuple[int, ...]:
    parts = []
    for chunk in version.split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def is_officially_signed(app: Path) -> bool:
    """判断 app 是否仍带官方 Developer ID 签名。

    ``codesign`` 扫一个 600MB 的 bundle 要将近一秒，而 ``is_pristine()``
    会在遍历备份时反复问同一个路径。缓存键带上 ``_CodeSignature`` 的 mtime，
    重签名后自动失效，不会把刚被我们 ad-hoc 重签的包读成官方版。
    读不到签名文件时不缓存（多半是路径还不存在，下次状态就变了）。
    """
    from . import signer

    app = Path(app)
    try:
        stamp = (app / "Contents" / "_CodeSignature" / "CodeResources").stat().st_mtime_ns
    except OSError:
        return bool(signer.team_identifier(app))
    key = (str(app), stamp)
    hit = _SIGNED_CACHE.get(key)
    if hit is None:
        hit = _SIGNED_CACHE[key] = bool(signer.team_identifier(app))
    return hit


def prune_old_backups(app: Path, keep: int = 1) -> list[Path]:
    """清理旧备份，返回被删除的列表。

    规则：
      * 不完整的备份**一律删**（留着只会在还原时坑人）
      * ``best_backup()`` 会选中的那个永不删（否则还原没得用）
      * **跟当前 Claude 版本不同的备份一律删**——包括原版备份。
        旧版本原版备份看着「无价」，实际既不能还原（会降级）
        也不能拿来重打补丁，只占几百 MB。这条缺失曾让 4.29 的原版备份
        在 4.4212 上长期压着新备份，还把「恢复原样」直接搞成报错。
      * 同版本的其余备份里，最多再留 keep 个最新的
    """
    items = list_backups(app)                      # 旧 → 新
    if not items:
        return []

    keeper = best_backup(app)
    removed: list[Path] = []

    def drop(path: Path) -> bool:
        try:
            shutil.rmtree(path)
            removed.append(path)
            return True
        except (PermissionError, OSError) as exc:
            print(f"警告：备份未删干净 {path}（{exc}）")
            return False

    # app 不在时（安装中途被改名）bundle_version 会返回 "0"，
    # 拿它当基准会把所有备份判成版本不符全部删光——这种情况下不按版本删。
    has_app = (app / "Contents" / "Info.plist").is_file()
    cur_key = version_key(bundle_version(app)) if has_app else None

    # 1. 不完整的、以及版本对不上的，全删（keeper 除外）
    complete: list[Path] = []
    for item in items:
        if item == keeper:
            complete.append(item)
        elif not is_complete_backup(item):
            drop(item)
        elif cur_key is not None and version_key(bundle_version(item)) != cur_key:
            drop(item)
        else:
            complete.append(item)

    # 2. 同版本完整备份最多留 keep 个，keeper 必留
    survivors: set[Path] = {keeper} if keeper is not None else set()
    for item in reversed(complete):            # 新 → 旧
        if len(survivors) >= max(keep, 1):
            break
        survivors.add(item)
    for item in complete:
        if item not in survivors:
            drop(item)

    return removed
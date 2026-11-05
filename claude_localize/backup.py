"""整个 .app 的备份与恢复（全部用重命名，原子且秒级）。"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from . import procutil


BACKUP_GLOB = "Claude.backup-before-zh-*.app"
# 标记写在与 .app **同级**的旁挂文件里，不能写进包内：
# .app 顶层也在代码签名封条范围内，往里面加文件会让备份变成
# "unsealed contents present in the bundle root"，封条一旦破了，
# 还原时就没法用 codesign --verify 证明这个包是完整的。
PRISTINE_MARKER = ".claude-localize-pristine"
PRISTINE_SUFFIX = ".claude-localize-pristine"


def _marker_path(app: Path) -> Path:
    """标记文件的真实位置：`Claude.app` → `Claude.app.claude-localize-pristine`。"""
    return app.with_name(app.name + PRISTINE_SUFFIX)


def _legacy_marker(app: Path) -> Path:
    """旧版写在包内的标记（会破坏封条），仅用于兼容读取与清理。"""
    return app / PRISTINE_MARKER

_SIGNED_CACHE: dict[tuple[str, int], tuple[bool, bool]] = {}
_VERSION_CACHE: dict[tuple[str, int], str] = {}
_PRISTINE_CACHE: dict[tuple[str, int], bool] = {}


def _rmtree(path: Path) -> None:
    """删除目录；/Applications 下常有 root 属主或 uchg 标记的残留文件。"""
    subprocess.run(["chflags", "-R", "nouchg", str(path)], capture_output=True)
    try:
        shutil.rmtree(path)
        return
    except (PermissionError, OSError):
        pass
    result = procutil.run(["sudo", "-n", "rm", "-rf", str(path)])
    if result.returncode != 0:
        raise SystemExit(
            f"无法删除 {path}（{procutil.decode(result.stderr).strip()}）。\n"
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

    判据：带原版标记文件，**或**「签名封条完好且仍是官方 Developer ID」，
    两者都还要通过内容检查（没有我们写进去的中文资源）。

    「带 TeamIdentifier」单独不够：封条被破坏时 ``codesign -dv`` 照样
    输出 ``TeamIdentifier=Q6L2SF6YDW``（实测：往 Resources 里加一个文件，
    ``--verify`` 报 "a sealed resource is missing or invalid"，``-dv`` 仍报官方
    Team ID）。汉化写到一半中断——语言文件已写入、还没走到 resign——
    留下的包正好是这个形态。

    结果按「路径 + 关键文件 mtime」缓存：状态栏每次刷新都会问，而一次
    完整检查要跑两次 codesign 加一遍 asar 扫描，不缓存会让菜单卡住。
    """
    path = Path(path)
    key = (str(path), _pristine_stamp(path))
    hit = _PRISTINE_CACHE.get(key)
    if hit is None:
        hit = _PRISTINE_CACHE[key] = _pristine_uncached(path)
    return hit


def _pristine_stamp(path: Path) -> int:
    """用于缓存失效的指纹：标记文件与几个关键资源的 mtime。"""
    stamp = 0
    for p in (
        _marker_path(path),
        _legacy_marker(path),
        path / "Contents/Info.plist",
        path / "Contents/_CodeSignature/CodeResources",
        path / "Contents/Resources/app.asar",
        path / "Contents/Resources/ion-dist.asar",
    ):
        try:
            stamp ^= p.stat().st_mtime_ns
        except OSError:
            continue
    return stamp


def _pristine_uncached(path: Path) -> bool:
    # 旁挂标记：信任它，但仍要求内容干净（防止上一个版本写坏的标记）
    if _marker_path(path).is_file() or _legacy_marker(path).is_file():
        try:
            return _has_no_patch_content(path)
        except (OSError, ValueError, SystemExit):
            return False
    signed, sealed = _cached_signature(path)
    if not (sealed and signed):
        return False
    try:
        return _has_no_patch_content(path)
    except (OSError, ValueError, SystemExit):
        return False


def _has_no_patch_content(path: Path) -> bool:
    from . import asar, native_strings, shell, slashmenu

    app_asar = path / "Contents" / "Resources" / "app.asar"
    frontend_asar = path / "Contents" / "Resources" / "ion-dist.asar"
    return not (
        shell.has_artifacts(app_asar, "zh-CN")
        or native_strings.installed(path)
        or asar.has_entry(frontend_asar, "i18n/zh-CN.json")
        or slashmenu.has_marker_in_asar(frontend_asar)
    )


def mark_pristine(path: Path, note: str = "official") -> None:
    """在 .app **外面**写一个旁挂标记。

    绝不写进包内：那会破坏代码签名封条，让备份变成「无法验证」的包。
    """
    try:
        _marker_path(path).write_text(note + "\n", encoding="utf-8")
    except OSError:
        pass


def _drop_pristine_marker(path: Path) -> None:
    """清理标记：包内的旧标记和包外的旁挂标记都删。

    包内那份还会破坏封条，换回原位前必须清掉，否则会跟着进 Claude.app。
    """
    for marker in (_marker_path(path), _legacy_marker(path)):
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

    这会暂时让 app 路径消失，调用方必须紧接着 swap_in()。
    """
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = app.parent / f"Claude.backup-before-zh-{stamp}.app"
    if dest.exists():
        raise SystemExit(f"备份目标已存在：{dest}")
    app.rename(dest)
    if pristine:
        mark_pristine(dest)
    return dest


def _copy_tree(src: Path, dst: Path) -> None:
    """用 ditto 复制 app 目录。

    不能用 ``shutil.copytree``：会丢扩展属性、ACL、资源分支，
    复制出来的 Claude 签名和权限都不对。
    """
    if dst.exists():
        raise OSError(f"目标已存在：{dst}")
    result = procutil.run(["ditto", str(src), str(dst)])
    if result.returncode != 0:
        raise OSError(f"ditto 失败：{procutil.decode(result.stderr).strip()}")


def swap_in(app: Path, source: Path, fallback: Path) -> None:
    """用已验证副本替换 app，失败时恢复旧版本且不消耗 fallback。

    交换前先把旧 app 留在同目录的 trash 中；若替换失败而 trash 恢复也失败，
    再用 ditto 从 fallback 复制，不能把它 rename 走。
    """
    trash: Path | None = None
    if app.exists():
        trash = _free_trash_name(app)
        app.rename(trash)
    try:
        source.rename(app)
    except BaseException:
        if app.exists():
            raise
        restored = False
        if trash is not None:
            try:
                trash.rename(app)
                restored = True
            except OSError:
                pass
        if not restored and fallback.exists():
            try:
                _copy_tree(fallback, app)
                restored = True
                print(f"替换失败，已把原版本复制回 {app}（备份保留在 {fallback.name}）")
            except OSError as exc:
                print(f"严重：无法从备份恢复：{exc}")
        if not restored:
            print(
                f"严重：Claude.app 未能自动放回。\n"
                f"  请手动执行：ditto {fallback!s} {app!s}"
            )
        raise
    if trash is not None:
        try:
            shutil.rmtree(trash)
        except (PermissionError, OSError):
            print(f"提示：临时目录未能自动删除，可稍后执行 sudo rm -rf {trash}")


def restore_backup(app: Path, backup: Path) -> Path:
    """整体换回原版备份，不修改其包内文件；成功后仍保留原备份。

    与 javaht 一样，原版还原不撤补丁、不重签。区别是先复制到临时目录
    验证再交换，避免 javaht 直接 move 唯一备份留下的失败窗口。

    换回前必须证明这份包是完整的：封条有效 + 官方签名 + 内容干净。
    任何一项不成立就拒绝——换回一个坏包比不换回更糟。
    """
    from . import integrity, signer

    if not is_complete_backup(backup):
        raise SystemExit(f"备份不完整，拒绝换回：{backup}")
    if not is_pristine(backup):
        raise SystemExit(f"备份无法确认为官方原版，拒绝直接换回：{backup}")
    signed, sealed = _cached_signature(backup)
    if not (signed and sealed):
        raise SystemExit(
            f"备份的代码签名封条不完整，无法证明它是干净的官方包：{backup}\n"
            "请重新安装官方 Claude，或改用「恢复原样」的撤补丁路径。"
        )

    with tempfile.TemporaryDirectory(prefix="claude-restore.") as td:
        prepared = Path(td) / "Claude.app"
        _copy_tree(backup, prepared)
        _drop_pristine_marker(prepared)
        problems = integrity.verify_app(prepared)
        if problems:
            raise SystemExit("原版备份完整性校验失败：\n" + "\n".join(problems[:8]))
        ok, err = signer.verify(prepared)
        if not ok:
            raise SystemExit(f"原版备份签名校验失败：{err}")
        swap_in(app, prepared, backup)
    return backup


def _at_version(items: list[Path], version: str) -> list[Path]:
    """筛出与 ``version`` 版本号相同的备份。

    还原/重打补丁都必须用同版本备份：拿旧版本顶上等于把 Claude 降级，
    新版布局和旧备份对不上时还会启动崩溃。宁可换不了，也不能换错。
    """
    want = version_key(version)
    return [p for p in items if version_key(bundle_version(p)) == want]


def _current_key(app: Path) -> tuple[int, ...] | None:
    """当前 Claude 的版本 key；无从判断时返回 None。

    两种读不到的情况要一起处理：app 根本不在了，或者 plist 在但没有
    ``CFBundleShortVersionString``（PlistBuddy 读失败也返回 "0"）。
    调用方拿到 None 必须放宽判断，不能把「读不出来」当成「版本是 0」——
    否则会认为所有备份都版本不符，进而把它们全部删光。
    """
    key = version_key(bundle_version(app))
    return None if key == (0,) else key


def best_backup(app: Path) -> Path | None:
    """还原/重打补丁该用哪份备份：同版本里优先原版，其次最新。

    早期实现只判「是不是原版」不看版本，于是 Claude 自动更新后，
    旧版本的原版备份永远压着新版本的干净备份，还原被降级保护拦下、
    重装补丁也报错——用户明明有可用备份却用不了。
    """
    complete = [p for p in list_backups(app) if is_complete_backup(p)]
    if not complete:
        return None

    cur = _current_key(app)
    if cur is None:
        # 版本无从判断（app 不在，或 plist 读不出来）：退回版本最高的完整备份，
        # 先让 Claude 回来。这时没有「降级」可言，能启动比版本对更重要。
        newest_version = max((bundle_version(p) for p in complete), key=version_key)
        same = _at_version(complete, newest_version)
    else:
        same = [p for p in complete if version_key(bundle_version(p)) == cur]
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
    try:
        return "pristine" if is_pristine(b) else "patched"
    except (OSError, ValueError, SystemExit):
        # 备份读不出来时按「无法确认」处理，不能让状态查询崩掉
        return "unknown"


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
    """读 app 的版本号；读不出来返回 ``"0"``。

    必须检查 returncode：PlistBuddy 失败时把错误信息写进 ``stdout``
    （``Error Reading File: …``），旧实现直接 strip 后返回，于是那段文字
    被 ``version_key`` 解析成一个天文数字，看起来像个极高的版本号，
    进而影响备份排序和版本过滤。
    """
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
    result = procutil.run(["/usr/libexec/PlistBuddy", "-c",
                           "Print :CFBundleShortVersionString", str(plist)])
    raw = procutil.decode(result.stdout).strip() if result.returncode == 0 else ""
    # 只接受 3.4.29 这种形态；PlistBuddy 的报错文本一律当读不出来
    version = raw if raw and all(
        ch.isdigit() or ch == "." for ch in raw
    ) else "0"
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
    """
    return _cached_signature(app)[0]


def is_sealed(app: Path) -> bool:
    """代码签名封条是否完好（``codesign --verify --deep --strict``）。

    与 ``is_officially_signed`` 共用一个缓存条目：两者都要跑 codesign，
    合并成一次读取（--verify 与 -dv 是两条命令，但仍按同一 mtime 缓存）。
    """
    return _cached_signature(app)[1]


def _cached_signature(app: Path) -> tuple[bool, bool]:
    """返回 ``(是否官方签名, 封条是否完好)``，按路径 + 封条 mtime 缓存。

    读不到 ``CodeResources`` 时不缓存：路径可能还不存在，也可能是个
    还没签过名的包，下次再问状态就不一样了。
    """
    from . import signer

    app = Path(app)
    try:
        stamp = (app / "Contents" / "_CodeSignature" / "CodeResources").stat().st_mtime_ns
    except OSError:
        return bool(signer.team_identifier(app)), signer.verify(app)[0]
    key = (str(app), stamp)
    hit = _SIGNED_CACHE.get(key)
    if hit is None:
        hit = _SIGNED_CACHE[key] = (
            bool(signer.team_identifier(app)),
            signer.verify(app)[0],
        )
    return hit


def _version_mismatch(item: Path, cur_key: tuple[int, ...]) -> bool:
    """备份版本与当前版本不同？**读不出来的备份不算不符**。

    完整备份但 plist 读不出版本（被别的工具写坏、从老版本手工拷来），
    它仍可能是唯一的回滚副本。这里返回 False 让它留到下一轮判断，
    而不是当成「版本不符」删掉——宁可多留几百 MB，不能删掉唯一副本。
    """
    key = version_key(bundle_version(item))
    if key == (0,):
        return False
    return key != cur_key


def prune_old_backups(app: Path, keep: int = 1) -> list[Path]:
    """清理旧备份，返回被删除的列表。

    规则：
      * 不完整的备份在其他完整候选存在时才删；否则保留一份供人工恢复
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

    # app 不在、或 plist 读不出版本时不能按版本删：那会把所有备份判成
    # 版本不符全部删光，真出问题时就没有可恢复的 Claude 了。
    cur_key = _current_key(app)

    # 1. 版本不符的完整备份清掉；不完整备份只在有其他完整候选时清理。
    complete = [p for p in items if is_complete_backup(p)]
    incomplete = [p for p in items if not is_complete_backup(p)]
    if not complete:
        if incomplete:
            print(f"警告：没有完整备份，保留不完整备份供人工恢复：{incomplete[-1]}")
        for item in incomplete[:-1]:
            drop(item)
        return removed

    same_version: list[Path] = []
    for item in complete:
        if item == keeper or cur_key is None or not _version_mismatch(item, cur_key):
            same_version.append(item)
        else:
            drop(item)
    for item in incomplete:
        drop(item)

    # 2. 同版本完整备份最多留 keep 个，keeper 必留
    survivors: set[Path] = {keeper} if keeper is not None else set()
    for item in reversed(same_version):
        if len(survivors) >= max(keep, 1):
            break
        survivors.add(item)
    for item in same_version:
        if item not in survivors:
            drop(item)

    return removed
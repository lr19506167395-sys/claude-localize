"""清理历史残留。

装过 javaht 或旧版本补丁的机器上，常见这些垃圾：

* ``Contents/Resources/zh-CN.json`` —— 4.29 之前的外壳语言文件。
  现在外壳只从 ``app.asar`` 内部读，散装文件是死数据，留着会误导排查。
* ``Contents/Resources/.app_asar_work`` / ``ion-dist`` —— 上次安装中断
  留下的解包目录；``FrontendTarget`` 看到目录和 asar 同时存在会直接报错，
  所以必须清掉。
* 缺 ``Info.plist`` / ``Frameworks`` / ``app.asar`` 的半截备份 —— 那是
  旧版 prune 按文件名排序误删留下的，拿它还原等于把 Claude 弄坏。
* ``Claude.app.replaced*`` —— 替换 app 时删不掉的临时目录。
* ``*.asar.new`` —— 打包中断留下的临时文件。
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from . import backup

# 4.29+ 只从 app.asar 内读 i18n，散装文件不再被加载
DEAD_LOOSE_LOCALES = ("zh-CN.json", "zh-Hans.json", "zh-TW.json", "zh-HK.json")


@dataclass
class Residue:
    path: Path
    kind: str          # dead-locale / work-dir / temp / broken-backup / replaced
    detail: str
    removed: bool = False


def _is_asar_layout(app: Path) -> bool:
    return (app / "Contents" / "Resources" / "ion-dist.asar").is_file()


def scan(app: Path) -> list[Residue]:
    """列出可清理项，不做任何修改。"""
    items: list[Residue] = []
    res = app / "Contents" / "Resources"
    asar_layout = _is_asar_layout(app)

    if asar_layout:
        for name in DEAD_LOOSE_LOCALES:
            p = res / name
            if p.is_file():
                items.append(Residue(
                    p, "dead-locale",
                    "4.29 只从 app.asar 内读语言文件，这个散装文件不会被加载",
                ))

    for name, detail in (
        (".app_asar_work", "上次安装中断留下的解包目录（会让下次安装直接失败）"),
        ("ion-dist", "与新布局的 ion-dist.asar 冲突的解包目录（上次安装中断留下）"),
    ):
        p = res / name
        if p.is_dir() and (name != "ion-dist" or asar_layout):
            items.append(Residue(p, "work-dir", detail))

    for p in res.glob("*.asar.new"):
        items.append(Residue(p, "temp", "打包中断留下的临时文件"))

    for b in backup.list_backups(app):
        if not backup.is_complete_backup(b):
            items.append(Residue(
                b, "broken-backup",
                "备份不完整（缺 Info.plist / Frameworks / app.asar），无法用于还原",
            ))

    for p in app.parent.glob(app.name + ".replaced*"):
        items.append(Residue(p, "replaced", "替换 app 时未删掉的临时目录"))

    return items


def _remove(path: Path) -> bool:
    """删除文件/目录，尽力清理（含不可变标记与权限问题）。"""
    import subprocess

    # javaht 会在 .lproj 上打 uchg（不可变）标记，不清掉 rm 也会失败
    subprocess.run(["chflags", "-R", "nouchg", str(path)], capture_output=True)
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
        return True
    except (PermissionError, OSError):
        pass
    result = subprocess.run(
        ["sudo", "-n", "rm", "-rf", str(path)], capture_output=True, text=True
    )
    return result.returncode == 0 and not path.exists()


def touched_bundle(items: list[Residue], app: Path) -> bool:
    """清理项里是否有落在 .app 包内的（动了包内文件就必须重新签名）。"""
    for item in items:
        if not item.removed:
            continue
        try:
            item.path.relative_to(app)
        except ValueError:
            continue
        return True
    return False


def clean_and_resign(app: Path, *, dry_run: bool = False, log=print) -> list[Residue]:
    """清理残留；如果动到了 app 包内部，自动重新签名。

    删掉包里任何一个被签名封住的资源（散装语言文件、.lproj）都会让
    code signature 失效——Claude 下次启动会直接 SIGTRAP。
    """
    items = clean(app, dry_run=dry_run, log=log)
    if dry_run or not touched_bundle(items, app):
        return items

    from . import signer

    log("清理动到了 app 包内文件，重新签名…")
    signer.clear_quarantine(app)
    signer.resign(app)
    ok, err = signer.verify(app)
    if not ok:
        raise SystemExit(f"清理后重签名失败：{err}")
    log("重签名完成")
    return items


def clean(app: Path, *, dry_run: bool = False, log=print) -> list[Residue]:
    """清理残留，返回清理项（removed 标记是否成功）。"""
    items = scan(app)
    if not items:
        return []
    for item in items:
        if dry_run:
            log(f"[dry-run] 将清理 {item.path}（{item.detail}）")
            continue
        if _remove(item.path):
            item.removed = True
            log(f"已清理 {item.path}（{item.detail}）")
        else:
            log(f"警告：无法清理 {item.path}，请手动删除")
    return items
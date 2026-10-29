"""从上游拉取词表（jsDelivr CDN → GitHub zip → 本地目录 三级降级）。

上游 javaht/claude-desktop-zh-cn（MIT）提供这些资源：

* ``frontend-zh-CN.json``           主界面词表
* ``frontend-hardcoded-zh-CN.json`` 硬编码字符串替换表
* ``desktop-zh-CN.json``            桌面壳层词表
* ``dynamic-zh-CN.json``            动态词表（模型描述等）
* ``statsig-zh-CN.json``            功能开关文案
* ``Localizable.strings``           macOS 原生字符串（UTF-16LE）

上游的桌面壳层词表在 4.29 上是死数据（外壳只读 app.asar 内部），
但 3.x 和将来可能的回退仍会用上，保留。
"""
from __future__ import annotations

import json
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from . import jsonutil, paths

REPO = "javaht/claude-desktop-zh-cn"
JSDELIVR = "https://cdn.jsdelivr.net/gh/{repo}@{ref}/"
CODELOAD = "https://codeload.github.com/{repo}/zip/refs/heads/{branch}"

PACK_FILES = {
    "frontend.json": "resources/frontend-zh-CN.json",
    "desktop.json": "resources/desktop-zh-CN.json",
    "hardcoded.json": "resources/frontend-hardcoded-zh-CN.json",
    "dynamic.json": "resources/dynamic-zh-CN.json",
    "statsig.json": "resources/statsig-zh-CN.json",
    "slash-menu-patch.json": "resources/slash-command-menu-patch.json",
    "slash-glosses.json": "resources/slash-command-glosses.json",
}
# 非 JSON 资源（原样复制）
BINARY_FILES = {"Localizable.strings": "resources/Localizable.strings"}
VERSION_FILE = "resources/release.json"

# 合理性阈值：防止上游结构大改时把坏数据写进来
MIN_COUNTS = {
    "frontend.json": 20000,
    "desktop.json": 500,
    "hardcoded.json": 1000,
    "dynamic.json": 20,
    "statsig.json": 20,
}


def _fetch(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "claude-localize"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _pack_dir() -> Path:
    return paths.PROJECT_ROOT / "packs" / paths.TARGET_LOCALE


def local_version() -> str | None:
    p = _pack_dir() / ".upstream-version"
    return p.read_text(encoding="utf-8").strip() if p.exists() else None


def check_remote(ref: str = "main") -> dict[str, Any]:
    """只取 release.json，检查上游版本。"""
    url = JSDELIVR.format(repo=REPO, ref=ref) + VERSION_FILE
    try:
        return json.loads(_fetch(url, timeout=20))
    except Exception as exc:
        raise SystemExit(f"检查上游版本失败：{exc}") from exc


def _validate(name: str, data: Any) -> None:
    if not isinstance(data, dict):
        raise SystemExit(f"{name} 不是 JSON 对象")
    need = MIN_COUNTS.get(name)
    if need and len(data) < need:
        raise SystemExit(f"{name} 条目数 {len(data)} 低于预期 {need}，拒绝写入")


def _validate_binary(name: str, raw: bytes) -> None:
    if name.endswith(".strings"):
        try:
            text = raw.decode("utf-16-le")
        except UnicodeDecodeError as exc:
            raise SystemExit(f"{name} 不是 UTF-16LE：{exc}") from exc
        if not any("一" <= ch <= "鿿" for ch in text):
            raise SystemExit(f"{name} 里没有中文，拒绝写入")
        if text.count("=") < 20:
            raise SystemExit(f"{name} 条目过少，拒绝写入")


def pull_via_jsdelivr(ref: str = "main", verbose: bool = True) -> dict[str, int]:
    """从 jsDelivr CDN 拉取。"""
    log = print if verbose else (lambda *a, **k: None)
    downloaded: dict[str, Any] = {}
    binaries: dict[str, bytes] = {}
    for local_name, remote_path in PACK_FILES.items():
        log(f"  下载 {local_name} …")
        data = json.loads(_fetch(JSDELIVR.format(repo=REPO, ref=ref) + remote_path, 90))
        _validate(local_name, data)
        downloaded[local_name] = data
    for local_name, remote_path in BINARY_FILES.items():
        log(f"  下载 {local_name} …")
        raw = _fetch(JSDELIVR.format(repo=REPO, ref=ref) + remote_path, 60)
        _validate_binary(local_name, raw)
        binaries[local_name] = raw
    return _write(downloaded, binaries, log, ref=ref)


def pull_via_zip(branch: str = "main", verbose: bool = True) -> dict[str, int]:
    """从 GitHub codeload 下载整个 zip（jsDelivr 失败时降级）。"""
    log = print if verbose else (lambda *a, **k: None)
    log("  下载仓库 zip …")
    raw = _fetch(CODELOAD.format(repo=REPO, branch=branch), timeout=180)

    downloaded: dict[str, Any] = {}
    binaries: dict[str, bytes] = {}
    with tempfile.TemporaryDirectory() as td:
        zpath = Path(td) / "repo.zip"
        zpath.write_bytes(raw)
        with zipfile.ZipFile(zpath) as zf:
            names = zf.namelist()
            root = names[0].split("/")[0] if names else ""
            for local_name, remote_path in PACK_FILES.items():
                member = f"{root}/{remote_path}"
                if member not in names:
                    raise SystemExit(f"zip 中找不到 {member}")
                data = json.loads(zf.read(member))
                _validate(local_name, data)
                downloaded[local_name] = data
            for local_name, remote_path in BINARY_FILES.items():
                member = f"{root}/{remote_path}"
                if member not in names:
                    raise SystemExit(f"zip 中找不到 {member}")
                blob = zf.read(member)
                _validate_binary(local_name, blob)
                binaries[local_name] = blob
    return _write(downloaded, binaries, log, ref=branch)


def pull_from_local(source: Path, verbose: bool = True) -> dict[str, int]:
    """从本地 javaht 目录复制。"""
    log = print if verbose else (lambda *a, **k: None)
    res = source / "resources" if (source / "resources").is_dir() else source
    downloaded: dict[str, Any] = {}
    binaries: dict[str, bytes] = {}
    for local_name, remote_path in PACK_FILES.items():
        p = res / Path(remote_path).name
        if not p.exists():
            raise SystemExit(f"本地目录缺少 {p}")
        data = jsonutil.load_json(p)
        _validate(local_name, data)
        downloaded[local_name] = data
    for local_name, remote_path in BINARY_FILES.items():
        p = res / Path(remote_path).name
        if not p.exists():
            raise SystemExit(f"本地目录缺少 {p}")
        raw = p.read_bytes()
        _validate_binary(local_name, raw)
        binaries[local_name] = raw
    return _write(downloaded, binaries, log)


def _write(
    downloaded: dict[str, Any], binaries: dict[str, bytes], log, ref: str | None = None
) -> dict[str, int]:
    out_dir = _pack_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    # 备份旧词表
    backup_dir = paths.USER_DIR / "sources-backup"
    if out_dir.exists() and any(out_dir.glob("*.json")):
        backup_dir.mkdir(parents=True, exist_ok=True)
        for f in out_dir.glob("*.json"):
            shutil.copy2(f, backup_dir / f.name)
        log(f"  旧词表已备份到 {backup_dir}")

    counts: dict[str, int] = {}
    for name, data in downloaded.items():
        jsonutil.save_json(out_dir / name, data)
        counts[name] = len(data)
    for name, raw in binaries.items():
        (out_dir / name).write_bytes(raw)
        counts[name] = raw.decode("utf-16-le", "ignore").count("=")

    # 记录版本号（纯文本，不带引号）
    if ref:
        try:
            remote = json.loads(_fetch(
                JSDELIVR.format(repo=REPO, ref=ref) + VERSION_FILE, timeout=20
            ))
            (out_dir / ".upstream-version").write_text(
                str(remote.get("release", "")), encoding="utf-8"
            )
        except Exception:
            pass
    return counts


def pull(
    method: str = "auto",
    ref: str = "main",
    local_path: Path | None = None,
    verbose: bool = True,
) -> dict[str, int]:
    """按降级链拉取上游词表。"""
    log = print if verbose else (lambda *a, **k: None)

    if method == "local" or (method == "auto" and local_path):
        if local_path is None:
            raise SystemExit("未指定 --local 路径")
        log(f"从本地目录拉取：{local_path}")
        return pull_from_local(local_path, verbose)

    if method in ("auto", "jsdelivr"):
        log("尝试 jsDelivr CDN…")
        try:
            return pull_via_jsdelivr(ref, verbose)
        except Exception as exc:
            if method == "jsdelivr":
                raise SystemExit(f"jsDelivr 拉取失败：{exc}") from exc
            log(f"  jsDelivr 失败（{exc}），改用 zip 下载")

    if method in ("auto", "zip"):
        log("尝试 GitHub zip…")
        try:
            return pull_via_zip(ref, verbose)
        except Exception as exc:
            if method == "zip":
                raise SystemExit(f"zip 拉取失败：{exc}") from exc
            raise SystemExit(
                f"所有在线方式均失败（{exc}）。\n"
                "可以手动下载后使用：claude-localize pull-upstream --local <目录>"
            ) from exc

    raise SystemExit(f"未知拉取方式：{method}")
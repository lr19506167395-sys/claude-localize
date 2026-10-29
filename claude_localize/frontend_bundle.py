"""前端资源包适配层。

Claude 4.29 起，前端整体打包成 ``Contents/Resources/ion-dist.asar``（约 190MB），
不再有散装的 ``ion-dist/`` 目录。为了复用现有的目录级补丁逻辑，
这里在打补丁前把 asar 解包成临时目录，打完再重新打包回去，
并同步 Info.plist 里记录的 header 哈希。
"""
from __future__ import annotations

import json
import os
import shutil
import struct
from pathlib import Path
from typing import Any

from . import asar, integrity

FRONTEND_ASAR = Path("Contents/Resources/ion-dist.asar")
FRONTEND_DIR = Path("Contents/Resources/ion-dist")
PLIST_KEY = "Resources/ion-dist.asar"


def detect_mode(app: Path) -> str:
    """返回 'asar'（Claude 4.29+）或 'directory'（更早版本）。"""
    if (app / FRONTEND_ASAR).is_file():
        return "asar"
    if (app / FRONTEND_DIR).is_dir():
        return "directory"
    raise SystemExit(f"找不到前端资源：{app / FRONTEND_DIR} 或 {app / FRONTEND_ASAR}")


def _is_unsafe(relative: str) -> bool:
    return any(p in ("", ".", "..") or "\\" in p for p in relative.split("/"))


def extract(asar_path: Path, destination: Path) -> set[str]:
    """把 asar 解包到 destination，返回 unpacked 条目集合。"""
    data = asar_path.read_bytes()
    header_size, _hstr, header = asar.read_header(data)
    data_start = 8 + header_size
    unpacked_root = Path(str(asar_path) + ".unpacked")
    unpacked: set[str] = set()

    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()

    for relative, entry in asar.walk(header):
        if _is_unsafe(relative):
            raise SystemExit(f"asar 含不安全路径：{relative}")
        if entry.get("link"):
            raise SystemExit(f"暂不支持含软链接的 asar：{relative}")

        target = (destination / Path(*relative.split("/"))).resolve()
        if target != root and root not in target.parents:
            raise SystemExit(f"asar 路径越界：{relative}")
        target.parent.mkdir(parents=True, exist_ok=True)

        if entry.get("unpacked"):
            src = unpacked_root / Path(*relative.split("/"))
            if not src.is_file():
                raise SystemExit(f"asar unpacked 文件缺失：{relative}")
            shutil.copy2(src, target)
            unpacked.add(relative)
            continue

        body = asar.read_file(data, header_size, entry)
        target.write_bytes(body)

    return unpacked


def _content_digest(body: bytes) -> str:
    import hashlib

    return hashlib.sha256(body).hexdigest()


def build(source_dir: Path, destination: Path, unpacked: set[str] | None = None) -> None:
    """把 source_dir 重新打包成 Electron 兼容的 asar。"""
    unpacked = unpacked or set()
    file_entries: list[tuple[Path, str, dict[str, Any]]] = []

    def walk(directory: Path, node: dict[str, Any]) -> None:
        for item in sorted(directory.iterdir(), key=lambda p: p.name):
            if item.is_symlink():
                raise SystemExit(f"源目录含不支持的软链接：{item}")
            if item.is_dir():
                child: dict[str, Any] = {"files": {}}
                node["files"][item.name] = child
                walk(item, child)
            elif item.is_file():
                rel = str(item.relative_to(source_dir)).replace(os.sep, "/")
                entry: dict[str, Any] = {"size": item.stat().st_size}
                node["files"][item.name] = entry
                file_entries.append((item, rel, entry))
            else:
                raise SystemExit(f"源目录含不支持的文件类型：{item}")

    header: dict[str, Any] = {"files": {}}
    walk(source_dir, header)

    # asar 内多个条目可能共享同一段数据（硬链接/去重），
    # 按内容哈希复用偏移，否则重打包后体积会凭空翻倍。
    # 每个文件只读一次：内容哈希、integrity、写盘都复用同一份字节。
    offset = 0
    seen: dict[str, int] = {}
    digests: dict[str, str] = {}
    for path, rel, entry in file_entries:
        body = path.read_bytes()
        entry["integrity"] = integrity.compute_integrity(body)
        digest = _content_digest(body)
        digests[rel] = digest
        if rel in unpacked:
            entry["offset"] = str(offset)
            entry["unpacked"] = True
            continue
        if digest in seen:
            entry["offset"] = str(seen[digest])
            continue
        seen[digest] = offset
        entry["offset"] = str(offset)
        offset += int(entry["size"])

    header_string = json.dumps(header, separators=(",", ":"), ensure_ascii=False)
    body = header_string.encode("utf-8")
    payload_size = asar.align4(4 + len(body))
    header_size = 4 + payload_size
    prefix = struct.pack("<II", 4, header_size)
    payload = (
        struct.pack("<II", payload_size, len(body))
        + body
        + b"\x00" * (payload_size - 4 - len(body))
    )

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".new")
    unpacked_root = Path(str(destination) + ".unpacked")

    try:
        written: set[str] = set()
        with temporary.open("wb") as out:
            out.write(prefix)
            out.write(payload)
            for path, rel, entry in file_entries:
                if rel in unpacked:
                    continue
                digest = digests[rel]
                if digest in written:
                    continue
                written.add(digest)
                with path.open("rb") as src:
                    shutil.copyfileobj(src, out)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)

    if unpacked:
        unpacked_root.mkdir(parents=True, exist_ok=True)
        for rel in sorted(unpacked):
            src = source_dir / Path(*rel.split("/"))
            dst = unpacked_root / Path(*rel.split("/"))
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def verify_structure(asar_path: Path) -> None:
    """打包后做基本结构校验，避免静默产出坏包。"""
    import re

    data, header_size, header = asar.load(asar_path)
    paths = {p for p, _e in asar.walk(header)}
    if "i18n/en-US.json" not in paths:
        raise SystemExit("重新打包后的 ion-dist.asar 缺少 i18n/en-US.json")
    if not any(re.search(r"(^|/)assets/(?:[^/]+/)?index-[^/]+\.js$", p) for p in paths):
        raise SystemExit("重新打包后的 ion-dist.asar 缺少 index bundle")
    if header_size <= 0:
        raise SystemExit("重新打包后的 ion-dist.asar header 异常")


class FrontendTarget:
    """上下文管理器：解包（如需）→ 交给调用方打补丁 → 重打包。"""

    def __init__(self, app: Path):
        self.app = app
        self.mode = detect_mode(app)
        self.unpacked: set[str] = set()
        self.asar_path: Path | None = None
        self.work_dir: Path = app / FRONTEND_DIR

    def __enter__(self) -> Path:
        if self.mode == "directory":
            return self.work_dir
        self.asar_path = self.app / FRONTEND_ASAR
        if self.work_dir.exists():
            raise SystemExit(f"前端目录与 asar 同时存在，无法判定布局：{self.work_dir}")
        self.unpacked = extract(self.asar_path, self.work_dir)
        return self.work_dir

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self.mode == "directory":
            return False
        if exc_type is not None:
            # 打补丁失败：清掉临时目录即可，ap.asar 原样未动
            shutil.rmtree(self.work_dir, ignore_errors=True)
            return False
        assert self.asar_path is not None
        build(self.work_dir, self.asar_path, self.unpacked)
        verify_structure(self.asar_path)
        shutil.rmtree(self.work_dir, ignore_errors=True)

        data = self.asar_path.read_bytes()
        _hs, header_string, _h = asar.read_header(data)
        integrity.sync_plist(self.app, header_string, PLIST_KEY)
        return False

# ── app.asar（桌面壳层 + 主进程）────────────────────────────────
# Claude 4.29 起桌面壳层语言文件也从 Resources/en-US.json 移到了
# app.asar 的 resources/i18n/en-US.json。

APP_ASAR = Path("Contents/Resources/app.asar")
APP_PLIST_KEY = "Resources/app.asar"


class AppAsarTarget:
    """把 app.asar 解包到 app 内的临时目录，退出时重打包并同步 plist 哈希。"""

    def __init__(self, app: Path):
        self.app = app
        self.asar_path = app / APP_ASAR
        self.work_dir = app / "Contents" / "Resources" / ".app_asar_work"
        self.unpacked: set[str] = set()
        self.active = self.asar_path.is_file()

    def __enter__(self) -> Path | None:
        if not self.active:
            return None
        if self.work_dir.exists():
            shutil.rmtree(self.work_dir, ignore_errors=True)
        self.unpacked = extract(self.asar_path, self.work_dir)
        return self.work_dir

    def __exit__(self, exc_type, exc, tb) -> bool:
        if not self.active:
            return False
        if exc_type is not None:
            shutil.rmtree(self.work_dir, ignore_errors=True)
            return False
        build(self.work_dir, self.asar_path, self.unpacked)
        shutil.rmtree(self.work_dir, ignore_errors=True)
        data = self.asar_path.read_bytes()
        _hs, header_string, _h = asar.read_header(data)
        integrity.sync_plist(self.app, header_string, APP_PLIST_KEY)
        return False


def desktop_i18n_dir(app: Path) -> Path | None:
    """返回桌面层语言文件所在目录（3.x 是 Resources/，4.29 在 app.asar 里）。"""
    res = app / "Contents" / "Resources"
    work = res / ".app_asar_work" / "resources" / "i18n"
    if work.is_dir():
        return work
    if (res / "en-US.json").exists():
        return res
    return None

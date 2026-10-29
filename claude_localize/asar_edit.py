"""在 app.asar 中原地替换文件内容。

严格照搬 javaht 的做法，不做整体重建：
  1. 直接在原字节数组里替换目标文件的内容（长度可变）
  2. 只调整排在目标之后的其他文件的 offset（加/减 delta）
  3. 重新编码 header，写回「新 header + 原有 body」
  4. 更新每个条目的 integrity + Info.plist 的 header 哈希

为什么不能重建：asar 里多个条目会共享同一段数据（各 renderer 复用同一份字体），
按条目逐个复制会让文件体积翻倍。
"""
from __future__ import annotations

import hashlib
import json
import os
import plistlib
import struct
from pathlib import Path
from typing import Any

from . import asar

ASAR_INTEGRITY_BLOCK_SIZE = 4194304


def calculate_integrity(data: bytes) -> dict[str, Any]:
    blocks = [
        hashlib.sha256(data[o : o + ASAR_INTEGRITY_BLOCK_SIZE]).hexdigest()
        for o in range(0, len(data), ASAR_INTEGRITY_BLOCK_SIZE)
    ]
    if not blocks:
        blocks = [hashlib.sha256(data).hexdigest()]
    return {
        "algorithm": "SHA256",
        "hash": hashlib.sha256(data).hexdigest(),
        "blockSize": ASAR_INTEGRITY_BLOCK_SIZE,
        "blocks": blocks,
    }


def encode_header_dynamic(header_string: str) -> bytes:
    """按 javaht 的 encode_asar_header_dynamic 编码。

    注意 header_size 字段写的是 len(header_pickle)，即 payload_size + 4。
    """
    body = header_string.encode("utf-8")
    payload_size = asar.align4(4 + len(body))
    pickle = (
        struct.pack("<I", payload_size)
        + struct.pack("<i", len(body))
        + body
        + b"\0" * (payload_size - 4 - len(body))
    )
    return struct.pack("<I", 4) + struct.pack("<I", len(pickle)) + pickle


def set_offset(entry: dict[str, Any], offset: int) -> None:
    entry["offset"] = str(offset) if isinstance(entry.get("offset"), str) else offset


def update_plist_integrity(app: Path, header_string: str) -> None:
    """更新 Info.plist 里记录的 asar header 哈希。"""
    plist = app / "Contents" / "Info.plist"
    if not plist.exists():
        return
    with plist.open("rb") as f:
        info = plistlib.load(f)

    integrity = info.get("ElectronAsarIntegrity")
    if not isinstance(integrity, dict):
        return
    entry = integrity.get("Resources/app.asar")
    if not isinstance(entry, dict) or entry.get("algorithm") != "SHA256":
        raise SystemExit("Info.plist 的 ElectronAsarIntegrity 格式不支持")

    entry["hash"] = hashlib.sha256(header_string.encode("utf-8")).hexdigest()
    tmp = plist.with_suffix(plist.suffix + ".tmp")
    with tmp.open("wb") as f:
        plistlib.dump(info, f, fmt=plistlib.FMT_XML)
    os.replace(tmp, plist)


def replace_file_content(app: Path, file_path: str, patched: bytes) -> bool:
    """把 asar 里 file_path 的内容换成 patched。返回是否发生修改。"""
    path = app / "Contents" / "Resources" / "app.asar"
    data = bytearray(path.read_bytes())
    header_size, _hstr, header = asar.read_header(bytes(data))

    entry = asar.find_entry(header, file_path)
    if entry is None:
        raise SystemExit(f"asar 中找不到 {file_path}")

    start = 8 + header_size + int(entry["offset"])
    size = int(entry["size"])
    end = start + size
    if start < 0 or end > len(data):
        raise SystemExit(f"{file_path} 的 bounds 越界")

    old = bytes(data[start:end])
    if old == patched:
        return False

    target_offset = int(entry["offset"])
    delta = len(patched) - size

    data[start:end] = patched

    entry["size"] = len(patched)
    entry["integrity"] = calculate_integrity(patched)

    if delta:
        for _p, other in asar.walk(header):
            if other is entry or "offset" not in other or "unpacked" in other:
                continue
            if int(other["offset"]) > target_offset:
                set_offset(other, int(other["offset"]) + delta)

    header_string = json.dumps(header, ensure_ascii=False, separators=(",", ":"))
    new_header = encode_header_dynamic(header_string)
    body = bytes(data[8 + header_size :])
    path.write_bytes(new_header + body)

    update_plist_integrity(app, header_string)
    return True


def list_js_files(header: dict[str, Any], prefix: str = ".vite/build/") -> list[str]:
    return [
        p for p, e in asar.walk(header)
        if p.startswith(prefix) and p.endswith(".js") and "unpacked" not in e
    ]


def find_by_marker(
    data: bytes, header_size: int, header: dict[str, Any], marker: bytes,
    prefix: str = ".vite/build/",
) -> list[str]:
    hits = []
    for p in list_js_files(header, prefix):
        e = asar.find_entry(header, p)
        if e is None:
            continue
        if marker in asar.read_file(data, header_size, e):
            hits.append(p)
    return hits

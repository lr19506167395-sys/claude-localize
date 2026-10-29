"""Electron app.asar 读写。

asar 格式（所有整数小端）：
  [0:4]   UInt32  size_pickle_payload  固定为 4
  [4:8]   UInt32  header_size          pickle 总长（含自身 4 字节长度字段）
  [8:8+header_size]  pickle:
        [+0:4]  UInt32  header_payload_size = align4(4 + len(json))
        [+4:8]  Int32   header_string_size  = len(json)
        [+8:8+n]        header JSON (utf-8)
        [余下]          0 填充到 align4
  随后 [align4(8+header_size)] 起是各文件内容。
"""
from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any


def align4(value: int) -> int:
    return value + ((4 - (value % 4)) % 4)


def read_header(data: bytes) -> tuple[int, str, dict[str, Any]]:
    """返回 (header_size, header_string, header_dict)。"""
    if len(data) < 16:
        raise SystemExit("app.asar 文件过小，无法解析")

    size_pickle_payload = struct.unpack_from("<I", data, 0)[0]
    header_size = struct.unpack_from("<I", data, 4)[0]
    if size_pickle_payload != 4 or header_size <= 0 or len(data) < 8 + header_size:
        raise SystemExit("app.asar size pickle 不支持")

    header_pickle = data[8 : 8 + header_size]
    header_payload_size = struct.unpack_from("<I", header_pickle, 0)[0]
    header_string_size = struct.unpack_from("<i", header_pickle, 4)[0]

    if header_payload_size != align4(4 + header_string_size):
        raise SystemExit("app.asar header pickle 长度不一致")
    if header_size != 4 + header_payload_size:
        raise SystemExit("app.asar header_size 与 payload 不匹配")

    header_string = header_pickle[8 : 8 + header_string_size].decode("utf-8")
    header = json.loads(header_string)
    if not isinstance(header, dict):
        raise SystemExit("app.asar header 不是 JSON 对象")
    return header_size, header_string, header


def encode_header(header_string: str) -> bytes:
    """把 header JSON 编码成完整的 header 区（含最外层 8 字节）。

    对照真实 Claude app.asar 的字节：
      [0:4]   固定为 4
      [4:8]   header_size = align4(4 + len(json))
      [8:12]  payload_size = align4(4 + len(json))   ← 与 header_size 相同
      [12:16] len(json)
      [16:..] json + 零填充至 align4
    """
    body = header_string.encode("utf-8")
    payload_size = align4(4 + len(body))
    header_size = payload_size + 4          # 实测：header_size 比 payload_size 多 4
    return (
        (4).to_bytes(4, "little")
        + header_size.to_bytes(4, "little")
        + payload_size.to_bytes(4, "little")
        + len(body).to_bytes(4, "little")
        + body
        + b"\0" * (payload_size - 4 - len(body))
    )


def header_area(header_string: str) -> tuple[bytes, int]:
    """返回 (完整 header 区字节, payload 起始偏移)。"""
    blob = encode_header(header_string)
    return blob, align4(len(blob))


def file_offset(header_size: int, entry: dict[str, Any]) -> int:
    """文件内容在 asar 中的绝对偏移。"""
    return align4(8 + header_size) + int(entry["offset"])


def walk(header: dict[str, Any], prefix: str = "") -> list[tuple[str, dict[str, Any]]]:
    """遍历 header，产出 (路径, 条目)。"""
    out: list[tuple[str, dict[str, Any]]] = []
    for name, entry in header.get("files", {}).items():
        path = f"{prefix}{name}"
        if "files" in entry:
            out.extend(walk(entry, path + "/"))
        else:
            out.append((path, entry))
    return out


def find_entry(header: dict[str, Any], target: str) -> dict[str, Any] | None:
    node: Any = header
    for part in target.split("/"):
        if not isinstance(node, dict):
            return None
        files = node.get("files", {})
        if part not in files:
            return None
        node = files[part]
    return node if isinstance(node, dict) else None


def read_file(data: bytes, header_size: int, entry: dict[str, Any]) -> bytes:
    start = file_offset(header_size, entry)
    return data[start : start + int(entry["size"])]


def load(path: Path) -> tuple[bytes, int, dict[str, Any]]:
    data = path.read_bytes()
    header_size, _s, header = read_header(data)
    return data, header_size, header


def read_header_file(path: Path) -> tuple[int, str, dict[str, Any]]:
    """只读头部 header 区，不把整个 asar（约 200MB）读进内存。

    状态检查、条目存在性判断都用这个，避免每次打开菜单都跑几百 MB IO。
    """
    with path.open("rb") as f:
        head = f.read(8)
        if len(head) < 8:
            raise SystemExit(f"{path.name} 文件过小，无法解析")
        header_size = struct.unpack_from("<I", head, 4)[0]
        if header_size <= 0 or header_size > (1 << 30):
            raise SystemExit(f"{path.name} header 长度异常：{header_size}")
        f.seek(0)
        data = f.read(8 + header_size)
    return read_header(data)


def read_entry_file(path: Path, rel: str) -> bytes | None:
    """按路径读单个条目，只 seek 到对应位置，不加载整个文件。"""
    header_size, _s, header = read_header_file(path)
    entry = find_entry(header, rel)
    if entry is None:
        return None
    if entry.get("unpacked"):
        unpacked = Path(str(path) + ".unpacked") / Path(*rel.split("/"))
        return unpacked.read_bytes() if unpacked.is_file() else None
    with path.open("rb") as f:
        f.seek(file_offset(header_size, entry))
        return f.read(int(entry["size"]))


def has_entry(path: Path, rel: str) -> bool:
    try:
        _hs, _s, header = read_header_file(path)
    except Exception:
        return False
    return find_entry(header, rel) is not None


def write(path: Path, content: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(content)
    tmp.replace(path)

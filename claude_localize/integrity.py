"""Electron asar 完整性校验值的重算。

新版本 Claude 在 asar header 的每个文件条目里记录了 integrity：
  {
    "algorithm": "SHA256",
    "hash": "<整个文件的 sha256>",
    "blockSize": 4194304,
    "blocks": ["<第1块 sha256>", "<第2块 sha256>", ...]
  }

改过内容却不更新这些值，Claude 启动时会直接崩：
  FATAL: Integrity check failed for asar archive entry
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


def compute_integrity(content: bytes, block_size: int = 4194304) -> dict[str, Any]:
    """按 Electron 的规则计算完整性信息。"""
    blocks = [
        hashlib.sha256(content[i : i + block_size]).hexdigest()
        for i in range(0, len(content), block_size)
    ]
    return {
        "algorithm": "SHA256",
        "hash": hashlib.sha256(content).hexdigest(),
        "blockSize": block_size,
        "blocks": blocks,
    }


def update_entry(entry: dict[str, Any], content: bytes) -> bool:
    """更新条目里的 integrity（若原本存在）。返回是否更新。"""
    if "integrity" not in entry:
        return False
    block_size = int(entry["integrity"].get("blockSize", 4194304))
    entry["integrity"] = compute_integrity(content, block_size)
    return True


def has_integrity(header: dict[str, Any]) -> bool:
    """判断 asar 是否启用完整性校验。"""
    from . import asar

    return any("integrity" in e for _, e in asar.walk(header))


# ── Info.plist 的 header 哈希 ──────────────────────────────────────
# 除了每个文件条目里的 integrity，Claude 的 Info.plist 还记录了一份
# 「header 字符串的 sha256」，启动时 Electron 会先验这个。
# 改了 asar 却不更新它，同样会崩。

PLIST_KEY = "ElectronAsarIntegrity:Resources/app.asar:hash"


def _plist_key(rel: str) -> str:
    # plist 里的键本身带斜杠（如 "Resources/app.asar"），
    # 不能把斜杠换成冒号，否则 PlistBuddy 找不到条目。
    return f"ElectronAsarIntegrity:{rel}:hash"


def read_plist_hash(plist: Path, rel: str = "Resources/app.asar") -> str | None:
    import subprocess

    result = subprocess.run(
        ["/usr/libexec/PlistBuddy", "-c", f"Print :{_plist_key(rel)}", str(plist)],
        capture_output=True, text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def write_plist_hash(plist: Path, value: str, rel: str = "Resources/app.asar") -> None:
    import subprocess

    result = subprocess.run(
        ["/usr/libexec/PlistBuddy", "-c", f"Set :{_plist_key(rel)} {value}", str(plist)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"更新 Info.plist 的 asar 哈希失败：{result.stderr.strip()}")


def header_hash(header_string: str) -> str:
    """asar header 字符串的 sha256。"""
    return hashlib.sha256(header_string.encode("utf-8")).hexdigest()


def sync_plist(app: Path, header_string: str, rel: str = "Resources/app.asar") -> bool:
    """把新的 header 哈希写回 Info.plist。返回是否修改。"""
    plist = app / "Contents" / "Info.plist"
    if not plist.exists():
        return False
    current = read_plist_hash(plist, rel)
    if current is None:
        return False
    expected = header_hash(header_string)
    if current == expected:
        return False
    write_plist_hash(plist, expected, rel)
    return True


# ── Electron Framework 内的完整性字典摘要槽 ─────────────────────────
# Electron 37+ 在 Electron Framework 二进制里预埋了一个槽位
# （sentinel + used + version + SHA256），运行时用它校验整个
# ElectronAsarIntegrity 字典未被篡改。
# 只改 Info.plist 而不改这个槽位，Claude 启动时会直接 SIGTRAP 崩溃。

INTEGRITY_DIGEST_SENTINEL = b"AGbevlPCksUGKNL8TSn7wGmJEuJsXb2A"
ELECTRON_FRAMEWORK_REL = Path(
    "Contents/Frameworks/Electron Framework.framework/Versions/A/Electron Framework"
)


def compute_dictionary_digest(integrity: dict) -> str:
    """按 Electron 的规则计算字典摘要：键排序 + algorithm + hash。"""
    hasher = hashlib.sha256()
    for key in sorted(integrity):
        entry = integrity[key]
        hasher.update(key.encode("utf-8"))
        hasher.update(str(entry.get("algorithm", "")).encode("utf-8"))
        hasher.update(str(entry.get("hash", "")).encode("utf-8"))
    return hasher.hexdigest()


def read_dictionary(app: Path) -> dict | None:
    import plistlib

    plist = app / "Contents" / "Info.plist"
    if not plist.exists():
        return None
    with plist.open("rb") as f:
        info = plistlib.load(f)
    integ = info.get("ElectronAsarIntegrity")
    return integ if isinstance(integ, dict) and integ else None


def sync_digest_slot(app: Path, integrity: dict | None = None) -> int:
    """把字典摘要写进 Electron Framework 的槽位。返回修补的槽数。"""
    if integrity is None:
        integrity = read_dictionary(app)
    if not integrity:
        return 0

    framework = app / ELECTRON_FRAMEWORK_REL
    if not framework.is_file():
        return 0

    data = bytearray(framework.read_bytes())
    digest = bytes.fromhex(compute_dictionary_digest(integrity))
    updated = 0
    start = 0
    while True:
        index = data.find(INTEGRITY_DIGEST_SENTINEL, start)
        if index < 0:
            break
        slot = index + len(INTEGRITY_DIGEST_SENTINEL)
        if slot + 34 > len(data):
            break
        # used=0 表示打包时未启用字典摘要，保持原样（fail-open）
        if data[slot] == 1:
            data[slot + 1] = 1
            data[slot + 2 : slot + 34] = digest
            updated += 1
        start = index + 1

    if updated:
        framework.write_bytes(data)
    return updated


def verify_digest_slot(app: Path, integrity: dict | None = None) -> tuple[bool, str]:
    """校验二进制槽位与 plist 字典是否一致。没有槽位时视为通过。"""
    if integrity is None:
        integrity = read_dictionary(app)
    if not integrity:
        return True, ""

    framework = app / ELECTRON_FRAMEWORK_REL
    if not framework.is_file():
        return True, ""

    expected = compute_dictionary_digest(integrity)
    data = framework.read_bytes()
    start = 0
    checked = 0
    while True:
        index = data.find(INTEGRITY_DIGEST_SENTINEL, start)
        if index < 0:
            break
        slot = index + len(INTEGRITY_DIGEST_SENTINEL)
        if slot + 34 > len(data):
            break
        if data[slot] == 1:
            actual = data[slot + 2 : slot + 34].hex()
            if actual != expected:
                return False, f"Framework 摘要槽不匹配（{actual[:12]}… ≠ {expected[:12]}…）"
            checked += 1
        start = index + 1
    return True, f"已校验 {checked} 个槽" if checked else "无槽位"


def verify_app(app: Path, *, deep: bool = True) -> list[str]:
    """完整校验一个 app 的 asar 完整性链，返回问题列表（空 = 通过）。

    ``codesign --verify`` 查不到这些：摘要槽没同步时签名照样有效，
    但 Electron 启动会直接 SIGTRAP。备份/还原路径必须用它兜底。
    """
    import plistlib

    from . import asar

    plist = app / "Contents" / "Info.plist"
    if not plist.is_file():
        return ["缺少 Info.plist"]
    try:
        info = plistlib.loads(plist.read_bytes())
    except Exception as exc:
        return [f"Info.plist 无法解析：{exc}"]

    integ = info.get("ElectronAsarIntegrity") or {}
    if not isinstance(integ, dict) or not integ:
        return []

    problems: list[str] = []
    for rel, entry in integ.items():
        path = app / "Contents" / rel
        if not path.is_file():
            problems.append(f"Info.plist 声明了 {rel}，但文件不存在")
            continue
        try:
            data, header_size, header = asar.load(path)
        except Exception as exc:
            problems.append(f"{rel} 无法解析：{exc}")
            continue

        recorded = (entry or {}).get("hash")
        if recorded:
            actual = header_hash(asar.read_header(data)[1])
            if recorded != actual:
                problems.append(f"{rel} 的 header 哈希与 Info.plist 不一致")

        if deep:
            for rel_path, e in asar.walk(header):
                if e.get("unpacked") or "integrity" not in e:
                    continue
                body = asar.read_file(data, header_size, e)
                if e["integrity"]["hash"] != hashlib.sha256(body).hexdigest():
                    problems.append(f"{rel} 条目 integrity 不符：{rel_path}")
                    break

    ok, msg = verify_digest_slot(app, integ)
    if not ok:
        problems.append(msg)
    return problems

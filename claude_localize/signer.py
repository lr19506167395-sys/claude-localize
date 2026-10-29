"""macOS ad-hoc 重签名。

改过 Resources 后原签名失效，必须重签，否则：
  - Claude 无法启动（签名校验失败）
  - 自动更新会卡住

⚠ 必须**保留原有 entitlements**。官方 Claude 带 20+ 项权限
（摄像头、麦克风、蓝牙、USB、AppleEvents、JIT 等），
重签时若丢掉，摄像头/麦克风/Cowork 等功能会直接失效。

⚠ 必须指定 identifier 级 designated requirement，
否则 Claude 自动更新会卡在「下载完成但版本不变」。
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import tempfile
from pathlib import Path

# ad-hoc 签名没有真实 Team ID，必须显式关闭库校验，
# 否则 Electron 主进程加载自带 framework 时会失败
FORCE_ENTITLEMENTS = {
    "com.apple.security.cs.disable-library-validation": True,
}

# ad-hoc 签名下这些字段无意义，必须移除，否则签名无效
DROP_KEYS = {
    "com.apple.application-identifier",
    "com.apple.developer.team-identifier",
    "keychain-access-groups",
}


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True)


def load_entitlements(path: Path) -> dict | None:
    """读取目标原有的 entitlements。"""
    result = _run(["codesign", "-d", "--entitlements", ":-", str(path)])
    blob = result.stdout.strip()
    if not blob or "<?xml" not in blob and "<plist" not in blob:
        return None
    try:
        data = plistlib.loads(blob.encode("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def signable_files(app: Path) -> tuple[list[Path], list[Path]]:
    """返回 (可执行文件列表, bundle 列表)。"""
    contents = app / "Contents"
    files: list[Path] = []
    bundles: list[Path] = []

    for root, dirs, names in os.walk(contents):
        root_path = Path(root)
        for d in dirs:
            p = root_path / d
            if p.suffix in {".app", ".framework"}:
                bundles.append(p)
        for n in names:
            p = root_path / n
            if p.is_symlink() or not p.is_file():
                continue
            if p.suffix in {".dylib", ".node", ".so"} or os.access(p, os.X_OK):
                files.append(p)

    return files, bundles


def signed_info(app: Path) -> dict[str, str]:
    out = {}
    r = _run(["codesign", "-dv", "--verbose=2", str(app)])
    for line in r.stderr.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def team_identifier(app: Path) -> str:
    """真实 Team ID；ad-hoc 签名时 codesign 会输出 ``TeamIdentifier=not set``。

    这个字符串非空，直接当布尔值用会把「我们自己重签的 app」误判成官方版，
    导致安装时跳过「先恢复原版」这一步。
    """
    value = signed_info(app).get("TeamIdentifier", "").strip()
    return "" if value.lower() in ("", "not set", "notset", "none") else value


def bundle_identifier(app: Path) -> str | None:
    return signed_info(app).get("Identifier")


def _sign_one(
    path: Path,
    ent_dir: Path,
    *,
    designated: str | None = None,
) -> bool:
    ents = load_entitlements(path) or {}
    if ents:
        for k in DROP_KEYS:
            ents.pop(k, None)
        ents.update(FORCE_ENTITLEMENTS)

    cmd = ["codesign", "--force", "--sign", "-", "--timestamp=none",
           "--options", "runtime", "--preserve-metadata=identifier,flags"]

    if ents:
        ent_file = ent_dir / f"{abs(hash(path.as_posix()))}.plist"
        ent_file.write_bytes(plistlib.dumps(ents, fmt=plistlib.FMT_XML))
        cmd += ["--entitlements", str(ent_file)]

    if designated:
        cmd += ["-r", f"=designated => {designated}"]

    cmd.append(str(path))
    return _run(cmd).returncode == 0


def resign(app: Path) -> dict[str, int]:
    """对整个 app 做 ad-hoc 重签名，保留 entitlements。"""
    ent_dir = Path(tempfile.mkdtemp(prefix="claude-zh-ent."))
    files, bundles = signable_files(app)
    stats = {"signed": 0, "failed": 0}

    # 先签最深的文件
    for p in sorted(files, key=lambda x: len(x.parts), reverse=True):
        if _sign_one(p, ent_dir):
            stats["signed"] += 1
        else:
            stats["failed"] += 1

    # 再签内层 bundle，由深到浅
    for p in sorted(bundles, key=lambda x: len(x.parts), reverse=True):
        if _sign_one(p, ent_dir):
            stats["signed"] += 1
        else:
            stats["failed"] += 1

    # 最后签外层 app，写 identifier 级 requirement 保证自动更新可用
    bid = bundle_identifier(app)
    designated = f'identifier "{bid}"' if bid else None
    if not _sign_one(app, ent_dir, designated=designated):
        raise SystemExit("重签名 app 失败")
    stats["signed"] += 1

    return stats


def clear_quarantine(app: Path) -> None:
    _run(["xattr", "-cr", str(app)])


def verify(app: Path) -> tuple[bool, str]:
    r = _run(["codesign", "--verify", "--deep", "--strict", str(app)])
    return r.returncode == 0, r.stderr.strip()


def has_library_validation_disabled(app: Path) -> bool:
    ents = load_entitlements(app) or {}
    return bool(ents.get("com.apple.security.cs.disable-library-validation"))


def signature_info(app: Path) -> dict[str, str]:
    return signed_info(app)

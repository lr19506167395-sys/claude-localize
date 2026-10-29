"""共享测试工具：造一个假的 Claude.app 目录树。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def make_app(base: Path, version: str = "4.29.0", name: str = "Claude.app") -> Path:
    """建一个最小可用的 bundle 骨架（不含 asar 内容）。"""
    app = base / name
    contents = app / "Contents"
    (contents / "Resources").mkdir(parents=True)
    (contents / "Frameworks").mkdir(parents=True)
    (contents / "Info.plist").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>'
        f"<key>CFBundleShortVersionString</key><string>{version}</string>"
        "<key>CFBundleIdentifier</key><string>com.anthropic.claudefordesktop</string>"
        "</dict></plist>\n",
        encoding="utf-8",
    )
    (contents / "Resources" / "app.asar").write_bytes(b"")
    return app
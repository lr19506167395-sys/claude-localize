"""JSON 读写与校验工具。"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    """原子写入：先写临时文件再替换。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        Path(tmp).replace(path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def load_dict(path: Path) -> dict[str, str]:
    """加载 JSON 并断言是 str->str 字典。"""
    data = load_json(path)
    if not isinstance(data, dict):
        raise SystemExit(f"{path} 不是 JSON 对象")
    return data

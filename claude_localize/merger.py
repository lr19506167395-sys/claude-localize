"""四源合并：官方 → 内置词表 → 自有翻译 → 英文兜底。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import jsonutil, validator

SOURCE_LABELS = {
    "locked": "手动锁定",
    "official": "官方 zh-Hans",
    "pack": "内置词表",
    "custom": "自有翻译",
    "english": "英文兜底",
}


@dataclass
class CustomEntry:
    value: str
    locked: bool = False


@dataclass
class MergeStats:
    counts: dict[str, int] = field(default_factory=dict)
    rejected: list[tuple[str, str, str]] = field(default_factory=list)

    def add(self, source: str) -> None:
        self.counts[source] = self.counts.get(source, 0) + 1

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def coverage(self) -> float:
        translated = self.total - self.counts.get("english", 0)
        return translated / self.total if self.total else 0.0

    def report(self) -> str:
        lines = [f"共 {self.total} 条，覆盖率 {self.coverage() * 100:.1f}%"]
        for src, label in SOURCE_LABELS.items():
            n = self.counts.get(src, 0)
            if n:
                lines.append(f"  {label:12} {n:6} 条 ({n / self.total * 100:5.1f}%)")
        return "\n".join(lines)


def load_custom(path: Path) -> dict[str, CustomEntry]:
    """加载自有翻译。格式：{key: {"value": "...", "locked": true}}"""
    if not path.exists():
        return {}
    raw = jsonutil.load_json(path)
    out = {}
    for key, item in raw.items():
        if isinstance(item, str):
            out[key] = CustomEntry(value=item, locked=False)
        elif isinstance(item, dict):
            out[key] = CustomEntry(
                value=str(item.get("value", "")), locked=bool(item.get("locked", False))
            )
    return out


def save_custom(path: Path, entries: dict[str, CustomEntry]) -> None:
    data = {
        k: {"value": v.value, "locked": v.locked} for k, v in entries.items()
    }
    jsonutil.save_json(path, data)


def merge(
    en: dict[str, str],
    pack: dict[str, str],
    official: dict[str, str] | None = None,
    custom: dict[str, CustomEntry] | None = None,
) -> tuple[dict[str, str], MergeStats, dict[str, str]]:
    """按优先级合并，返回 (结果词表, 统计, 每条来源)。"""
    official = official or {}
    custom = custom or {}
    stats = MergeStats()
    result: dict[str, str] = {}
    origin: dict[str, str] = {}

    for key, en_val in en.items():
        chosen = None

        # ① 手动锁定
        ce = custom.get(key)
        if ce and ce.locked and ce.value.strip():
            chosen = ("locked", ce.value, False)

        # ② 官方
        if chosen is None and key in official:
            val = official[key]
            if not validator.is_untranslated(val, en_val):
                chosen = ("official", val, True)
            else:
                stats.rejected.append((key, "official", "未翻译"))

        # ③ 内置词表
        if chosen is None and key in pack:
            val = pack[key]
            if not validator.is_untranslated(val, en_val):
                chosen = ("pack", val, True)
            else:
                stats.rejected.append((key, "pack", "未翻译"))

        # ④ 自有翻译（未锁定）
        if chosen is None and ce and not ce.locked and ce.value.strip():
            if not validator.is_untranslated(ce.value, en_val):
                chosen = ("custom", ce.value, True)

        # 校验 + 降级
        while chosen is not None:
            source, val, need_check = chosen
            if not need_check:
                break
            errs = validator.validate_entry(en_val, val)
            if errs:
                stats.rejected.append((key, source, "；".join(errs)))
                chosen = None
                # 降级到下一源
                if source == "official" and key in pack:
                    pv = pack[key]
                    if not validator.is_untranslated(pv, en_val):
                        chosen = ("pack", pv, True)
                elif source == "pack" and ce and not ce.locked and ce.value.strip():
                    if not validator.is_untranslated(ce.value, en_val):
                        chosen = ("custom", ce.value, True)
                continue
            break

        if chosen:
            result[key] = chosen[1]
            origin[key] = chosen[0]
            stats.add(chosen[0])
        else:
            result[key] = en_val
            origin[key] = "english"
            stats.add("english")

    return result, stats, origin


def merge_pack_dir(
    en: dict[str, str],
    pack_path: Path,
    official_path: Path | None = None,
    custom_path: Path | None = None,
) -> tuple[dict[str, str], MergeStats, dict[str, str]]:
    pack = jsonutil.load_dict(pack_path) if pack_path.exists() else {}
    official = (
        jsonutil.load_dict(official_path)
        if official_path and official_path.exists()
        else {}
    )
    custom = load_custom(custom_path) if custom_path else {}
    return merge(en, pack, official, custom)

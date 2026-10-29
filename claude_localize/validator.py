"""词表校验：占位符、ICU 结构、HTML 标签。"""
from __future__ import annotations

import re
from collections import Counter


_PLACEHOLDER = re.compile(r"\{[^{}]+\}")
_ICU = re.compile(r"\{\s*([A-Za-z_][\w.-]*)\s*,\s*(plural|select|selectordinal)\s*,")
_TAG = re.compile(r"</?[A-Za-z][^>]*>")

# 这些英文保留原文，不视为「未翻译」
KEEP_ENGLISH = {
    "CI", "API", "MCP", "SSO", "SCIM", "SAML", "OAuth", "CLI", "IDE",
    "USB", "CPU", "GPU", "RAM", "SSL", "TLS", "JSON", "YAML", "HTML",
    "Sonnet", "Opus", "Haiku", "Pro", "Max", "Team", "Enterprise",
    "Claude", "Anthropic", "Cowork", "GitHub", "Google", "Slack",
    "Windows", "Linux", "macOS", "Python", "Node", "Docker",
}


def placeholders(text: str) -> Counter:
    """提取占位符签名（嵌套 ICU 里只取最外层变量的名字）。"""
    out: Counter = Counter()
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                inner = text[start + 1 : i].strip()
                var = re.match(r"[A-Za-z_][\w.-]*", inner)
                if var:
                    out[var.group(0)] += 1
                start = -1
    return out


def icu_signatures(text: str) -> list[tuple[str, str, frozenset[str]]]:
    """提取 plural/select 的结构签名。

    中文没有单复数，译文常把 one/other 合并成单个 other 分支——
    这属于合法翻译，因此分支集合只比对「显式枚举值」（=0/=1/yes/no），
    对 one/other 这类语法分支持宽松态度。
    """
    sigs = []
    for m in _ICU.finditer(text):
        start = m.end()
        depth = 1
        pos = start
        while pos < len(text) and depth:
            if text[pos] == "{":
                depth += 1
            elif text[pos] == "}":
                depth -= 1
            pos += 1
        body = text[start : max(start, pos - 1)]
        branches = set(re.findall(r"(?:^|[\s}])([=\w-]+)\s*\{", body))
        # 只保留语义分支（=0/=1/yes/no 等），语法单复数分支忽略
        semantic = frozenset(b for b in branches if b.startswith("=") or b in {"yes", "no"})
        sigs.append((m.group(1), m.group(2), semantic))
    return sigs


def is_untranslated(zh: str, en: str) -> bool:
    """判断 zh 是否等于没翻译。"""
    if not zh or not zh.strip():
        return True
    if zh == en:
        return en.strip() not in KEEP_ENGLISH
    return False


def validate_entry(en: str, zh: str) -> list[str]:
    """校验单条译文，返回错误列表（空表示通过）。"""
    errors: list[str] = []

    if placeholders(en) != placeholders(zh):
        errors.append("占位符不匹配")

    if icu_signatures(en) != icu_signatures(zh):
        errors.append("ICU 结构不匹配")

    if Counter(_TAG.findall(en)) != Counter(_TAG.findall(zh)):
        errors.append("HTML 标签不匹配")

    if en.count("\n") != zh.count("\n"):
        errors.append("换行数不一致")

    return errors


def validate_dict(en: dict[str, str], zh: dict[str, str], name: str = "") -> list[str]:
    """校验整份词表，只检查两边都有的 key。"""
    out = []
    prefix = f"[{name}] " if name else ""
    for key, zh_val in zh.items():
        if key not in en:
            continue
        if is_untranslated(zh_val, en[key]):
            continue
        for err in validate_entry(en[key], zh_val):
            out.append(f"{prefix}{key}: {err}")
    return out

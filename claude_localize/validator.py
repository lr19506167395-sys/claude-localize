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

    返回的列表**按变量名排序**：中文语序常与英文不同，两个 select 块
    对调位置是合法翻译。比较的是「存在哪些 ICU 构造」，不是它们在
    句子里的先后。
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
    return sorted(sigs)


def _strip_code_spans(text: str) -> str:
    """去掉反引号包裹的行内代码，避免把 `--flag <value>` 当成 HTML 标签。"""
    return re.sub(r"`[^`]*`", "", text)


def _tag_balance(text: str) -> list[str]:
    """返回这段文本里「开合不成对」的标签列表（空 = 平衡）。

    只关心结构是否合法，不关心出现次数：中文把 one/other 两个分支
    合并成一个 other 分支后，标签总数自然减半，但每段内部仍然平衡。
    拿总数相比会把合法译文判成错误。
    """
    text = _strip_code_spans(text)
    stack: list[str] = []
    broken: list[str] = []
    for m in _TAG.finditer(text):
        tag = m.group(0)
        name = re.match(r"</?([A-Za-z][\w-]*)", tag)
        if not name:
            continue
        tag_name = name.group(1).lower()
        if tag.startswith("</"):
            if stack and stack[-1] == tag_name:
                stack.pop()
            else:
                broken.append(tag)
        elif tag.rstrip().endswith("/>"):
            continue          # 自闭合标签不参与配对
        else:
            stack.append(tag_name)
    broken.extend(f"<{t}>" for t in stack)
    return broken


def _icu_branches(text: str) -> list[str]:
    """把 ICU 消息拆成各分支的正文片段；非 ICU 文本原样返回一段。"""
    pieces: list[str] = []
    pos = 0
    for m in _ICU.finditer(text):
        # 分支体里的每个 {…} 都算一段
        start = m.end()
        depth = 1
        i = start
        branch_start = None
        while i < len(text) and depth:
            ch = text[i]
            if ch == "{":
                if depth == 1:
                    branch_start = i + 1
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 1 and branch_start is not None:
                    pieces.append(text[branch_start:i])
                    branch_start = None
                elif depth == 0 and branch_start is not None:
                    pieces.append(text[branch_start:i])
                    branch_start = None
            i += 1
        pos = i
    if not pieces:
        return [text]
    return pieces


def tags_balanced(text: str) -> bool:
    """ICU 各分支内部标签各自平衡。"""
    return not any(_tag_balance(piece) for piece in _icu_branches(text))


def tags_match(en: str, zh: str) -> bool:
    """译文与原文的 HTML 结构是否相容。

    判据（任一成立即通过）：
      1. 译文与原文出现完全相同的标签集合——说明两边都是正文里的
         ``<test name>`` 这类占位符，或结构本来就对等；
      2. 译文自身各分支内标签平衡，且没有引入原文没有的标签。

    第 1 条是为了放过「双方都在正文里写了类似标签的东西」的情形：
    它们看起来像标签，但两边一致，不是译文引入的问题。
    """
    en_tags = {(t.lower(), t.startswith("</")) for t in _TAG.findall(_strip_code_spans(en))}
    zh_tags = {(t.lower(), t.startswith("</")) for t in _TAG.findall(_strip_code_spans(zh))}
    if en_tags == zh_tags:
        return True
    if not tags_balanced(zh):
        return False
    # 原文有的标签，译文不能丢：丢了 <b> 会让文案变纯文本，
    # 丢掉 </b> 更会让整段后续文本都被加粗。
    return en_tags <= zh_tags


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

    if not tags_match(en, zh):
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

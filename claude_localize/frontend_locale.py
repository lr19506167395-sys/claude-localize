"""语言白名单注册与硬编码文本替换。"""
from __future__ import annotations

import re

# Claude 前端语言数组，zh-CN 要注册进去。
# 只匹配「以 en-US 开头、后面全是语言代码」的数组，避免误伤 ["GET","POST"] 这类
# 普通字符串数组；同时不依赖具体顺序，上游增删语言也能命中。
LANG_LIST_RE = re.compile(r'\["en-US"(?:,"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?")+\]')

DISPLAY_NAME_MARKER = "__claudeZhLabel"
# javaht 以前装过会留下同名补丁，重复追加只会多包一层，没有意义
DISPLAY_NAME_FOREIGN = "__claudeZhLabelPatch"
DISPLAY_NAME_PATCH = (
    ';(()=>{const e=Intl.DisplayNames&&Intl.DisplayNames.prototype;'
    'if(!e||e.__claudeZhLabel)return;const n=e.of;'
    'e.of=function(e){const t=String(e);'
    'return t==="zh-CN"?"简体中文":t==="zh-TW"?"繁體中文（台灣）":'
    't==="zh-HK"?"繁體中文（香港）":n.call(this,e)},'
    'Object.defineProperty(e,"__claudeZhLabel",{value:!0})})();'
)


def patch_whitelist(text: str, lang: str) -> tuple[str, int]:
    """把 lang 加进所有语言数组，返回**实际修改**的数组个数。

    注意不能用 re.subn 的返回值当计数：它数的是「匹配到几处」，
    已经注册过的数组也会被算进去，导致报告虚高、也无法判断是否幂等。
    """
    changed = 0

    def repl(m: re.Match[str]) -> str:
        nonlocal changed
        body = m.group(0)
        codes = re.findall(r'"([\w-]+)"', body)
        if lang in codes:
            return body
        changed += 1
        return body[:-1] + f',"{lang}"]'

    new = LANG_LIST_RE.sub(repl, text)
    return new, changed


def patch_display_names(text: str) -> tuple[str, bool]:
    """让语言选择器显示「简体中文」而不是 zh-CN。"""
    if DISPLAY_NAME_MARKER in text or DISPLAY_NAME_FOREIGN in text:
        return text, False
    return text + DISPLAY_NAME_PATCH, True


# 匹配 "..." / '...' 字符串字面量（含反斜杠转义），单个字面量不跨行。
#
# ⚠ 不要写成 (["'])((?:\\.|(?!\1).)*)\1：反查 + 嵌套量词会灾难性回溯，
# 1MB 的 JS 能卡上几十秒。下面的写法两个分支互斥（字符类已排除反斜杠），
# 是线性的，200MB 也就几秒。
STRING_RE = re.compile(
    r'"(?P<dq>(?:[^"\\\n]|\\.)*)"'
    r"|'(?P<sq>(?:[^'\\\n]|\\.)*)'"
)


class HardcodedReplacer:
    """一次性编译全部替换规则，每个文件只扫一遍。

    早期实现是「用 N 条规则逐个 ``in text`` 预筛」，规则上万条、文件近 4000 个，
    等于几十亿次子串搜索。现在改成**先找出文件里所有字符串字面量**，
    再逐个查 dict：一次正则扫描 + 每个字面量一次哈希查找，快两个数量级。
    """

    STRING_RE = STRING_RE

    def __init__(self, rules: list[list[str]], skip_structural: bool = True):
        pairs = []
        for item in rules:
            if len(item) != 2:
                continue
            src, dst = item
            if not src or src == dst:
                continue
            if skip_structural and re.fullmatch(r"[A-Za-z_$][\w$]*", src):
                continue
            pairs.append((src, dst))

        self._mapping = dict(pairs)
        self.found = 0

    def apply(self, text: str) -> tuple[str, int]:
        if not self._mapping:
            return text, 0

        count = 0
        mapping = self._mapping

        def repl(m: re.Match[str]) -> str:
            nonlocal count
            if m.group("dq") is not None:
                src, quote = m.group("dq"), '"'
            else:
                src, quote = m.group("sq"), "'"
            dst = mapping.get(src)
            if dst is None:
                return m.group(0)
            count += 1
            return f"{quote}{dst}{quote}"

        new = self.STRING_RE.sub(repl, text)
        self.found += count
        return new, count


def apply_hardcoded(
    text: str, rules: list[list[str]], skip_structural: bool = True
) -> tuple[str, int]:
    """兼容旧接口：单次调用。"""
    return HardcodedReplacer(rules, skip_structural).apply(text)


def is_already_patched(text: str, lang: str) -> bool:
    m = LANG_LIST_RE.search(text)
    if not m:
        return False
    return f'"{lang}"' in m.group(0)


def extract_lang_list(text: str) -> list[str] | None:
    m = LANG_LIST_RE.search(text)
    return re.findall(r'"([\w-]+)"', m.group(0)) if m else None

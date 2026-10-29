"""API 补翻：只处理多源合并后仍未覆盖的文案，结果只写入词表。"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from . import discover, jsonutil, merger, paths, validator

# 不预置任何服务商：留空，由用户在自己的配置里填写。
# 支持任意 OpenAI 兼容（/chat/completions）或 Anthropic（/v1/messages）端点。
DEFAULT_MODEL = ""
DEFAULT_BASE_URL = ""
BATCH_CHARS = 3000
BATCH_ITEMS = 60

_URL = re.compile(r"(?:https?://|~/|/[A-Za-z_.-]+/|[A-Za-z]:\\)")
_SHORTCUT = re.compile(r"^(?:Alt|Ctrl|Cmd|Command|Shift|Option|⌘|⌥|⇧)[+ ]", re.I)
_CODE_TOKEN = re.compile(r"^[\w./:+*%<>\[\]{}$#@~`'\"|=-]+$")
_URLISH = re.compile(r"(?:https?://|\.\w{2,4}(?:/|$)|@[\w.]+|[\w.-]+\.[\w.-]+\.[\w.-]+)")
_PLACEHOLDER_ONLY = re.compile(r"^[\s{}a-zA-Z0-9_.:,+/#%=~\-]+$")
_NON_UI_TAGS = re.compile(
    r"^(?:CI|API|MCP|IDE|CLI|SDK|SSO|SAML|OAuth|OIDC|SSH|JSON|CSV|PDF|DOCX|"
    r"DNS|GCP|AWS|GCP|KMS|GHE|JWT|WSL|SCIM|PIN|DNA|FCF|JCT|CWE|GHE|5xx|4xx|"
    r"p50|p95|p99|SHA-256|HTTP|HTTPS|URL|URI|ID|UID|UUID|Node\.js|OpenDyslexic|"
    r"Anthropic Sans|Anthropic Serif|Atkinson Hyperlegible Next|Google Play|"
    r"Google Cloud(?: Platform| KMS)?|Google Vertex AI|Google Pay|Microsoft 365|"
    r"Microsoft Azure(?: Key Vault)?|Microsoft AppSource|Amazon (?:Bedrock|Web Services)|"
    r"Amazon Bedrock|Apple Pay|GitHub(?: App| Enterprise)?|GitLab|Slack|Linear|"
    r"Outlook|Gmail|Instagram|LinkedIn|Reddit|TikTok|YouTube|X / Twitter|"
    r"JetBrains|Node\.js|PowerShell|Bash|Markdown|JavaScript|JSON|YAML|XML|"
    r"Windows|macOS|Linux|WSL|BedrockInference|OpenTelemetry|AWS KMS|GCP)$",
    re.I,
)


@dataclass
class TranslateStats:
    total_candidates: int = 0
    adopted: int = 0
    skipped: int = 0
    failed: int = 0
    batches: int = 0
    rejects: dict = field(default_factory=dict)

    def summary(self) -> str:
        return (
            f"候选 {self.total_candidates} 条；采用 {self.adopted}，"
            f"跳过 {self.skipped}，失败 {self.failed}"
        )


def candidate_filter(text: str) -> tuple[bool, str]:
    """排除明显不应翻译的串；其余交给模型判断。"""
    s = str(text).strip()
    if not s:
        return False, "空串"
    if _URL.search(s) or _URLISH.search(s):
        return False, "URL/路径/邮箱"
    if _SHORTCUT.match(s):
        return False, "快捷键"
    if s.startswith("{") and s.endswith("}") and not re.search(r"[A-Za-z]{3,}\s+[A-Za-z]{3,}", s):
        return False, "纯变量模板"
    if _CODE_TOKEN.fullmatch(s) and ("{" in s or "/" in s or "-" in s):
        return False, "标识符/模板"
    if re.fullmatch(r"[A-Za-z][\w.-]*(?:-[\w.-]+)+", s):
        return False, "标识符"
    if _NON_UI_TAGS.match(s):
        return False, "品牌/技术标签"
    if len(re.sub(r"\{[^}]+\}", "", s).strip()) <= 1:
        return False, "过短"
    if re.fullmatch(r"[0-9a-fA-F-]{12,}", s):
        return False, "UUID/编号"
    return True, ""


def collect_candidates(inst=None) -> list[tuple[str, str]]:
    """按当前三源合并结果，只挑仍显示英文且通过粗筛的条目。"""
    from . import checks

    inst = inst or discover.detect()
    # 4.29 起主界面词表在 ion-dist.asar 里，不能再用散装路径
    en = checks.current_frontend_en(inst)
    pack = jsonutil.load_dict(paths.PROJECT_ROOT / "packs/zh-CN/frontend.json")
    custom_path = paths.custom_path()
    custom = merger.load_custom(custom_path)
    _, _, origin = merger.merge(en, pack, custom=custom)

    out = []
    for key, en_val in en.items():
        if origin.get(key) != "english":
            continue
        # 已有 API 译文不重复翻译，避免反复请求
        ce = custom.get(key)
        if ce and not ce.locked and ce.value.strip() and ce.value != en_val:
            continue
        ok, _reason = candidate_filter(en_val)
        if ok:
            out.append((key, en_val))
    return out


def infer_provider(base_url: str) -> str:
    u = (base_url or "").lower()
    return "anthropic" if "/anthropic" in u or "/messages" in u else "openai"


def _extract_json(text: str) -> dict:
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    a, b = s.find("{"), s.rfind("}")
    if a < 0 or b < a:
        raise ValueError("API 返回中没有 JSON 对象")
    return json.loads(s[a : b + 1])


def _require_config(base_url: str, model: str) -> None:
    if not (base_url or "").strip():
        raise ValueError("未配置 API 地址：打开「* 翻译 agent 配置…」填写你自己的端点")
    if not (model or "").strip():
        raise ValueError("未配置模型：打开「* 翻译 agent 配置…」填写模型名")


def _endpoint(base_url: str, provider: str) -> str:
    suffix = "/v1/messages" if provider == "anthropic" else "/chat/completions"
    base = base_url.rstrip("/")
    return base if base.endswith(suffix) else base + suffix


def translate_batch(
    items: dict[str, str],
    *,
    api_key: str,
    base_url: str = DEFAULT_BASE_URL,
    model: str = DEFAULT_MODEL,
    provider: str = "",
    timeout: int = 120,
) -> dict[str, str]:
    _require_config(base_url, model)
    provider = provider or infer_provider(base_url)
    system = (
        "你是 Claude Desktop 的 UI 本地化译者。把英文界面文案译成自然、简洁的简体中文，"
        "使用「你」而不是「您」。"
        "Claude、Anthropic、产品名、品牌名、代码、URL、路径、快捷键、技术标识符保持原文。"
        "完整保留 {变量}、ICU plural/select 结构、HTML/XML 标签与换行。"
        "只翻译可读文字，不改变量名或分支选择器。"
        "若该串整体不需要翻译，原样返回。"
        "仅返回与输入 key 完全相同的 JSON 对象。"
    )
    user = (
        "Translate this JSON object. Keep the exact same keys and return only valid JSON:\n"
        + json.dumps(items, ensure_ascii=False, indent=2)
    )
    if provider == "anthropic":
        payload = {
            "model": model,
            "max_tokens": 12000,
            "temperature": 0,
            "thinking": {"type": "disabled"},
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        headers = {
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
    else:
        payload = {
            "model": model,
            "max_tokens": 12000,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {
            "content-type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

    req = urllib.request.Request(
        _endpoint(base_url, provider),
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
    if provider == "anthropic":
        text = "".join(
            b.get("text", "") for b in raw.get("content", []) if b.get("type") == "text"
        )
    else:
        text = raw["choices"][0]["message"]["content"]
    return _extract_json(text)


def _batches(items: list[tuple[str, str]]) -> list[list[tuple[str, str]]]:
    result, batch, size = [], [], 0
    for item in items:
        cost = len(item[0]) + len(item[1]) + 14
        if batch and (len(batch) >= BATCH_ITEMS or size + cost > BATCH_CHARS):
            result.append(batch)
            batch, size = [], 0
        batch.append(item)
        size += cost
    if batch:
        result.append(batch)
    return result


def translate_missing(
    *,
    api_key: str,
    base_url: str = DEFAULT_BASE_URL,
    model: str = DEFAULT_MODEL,
    provider: str = "",
    max_batches: int = 0,
    progress=None,
) -> TranslateStats:
    """只补当前合并结果的缺口，只更新自有词表，绝不自动安装补丁。"""
    _require_config(base_url, model)
    stats = TranslateStats()
    items = collect_candidates()
    stats.total_candidates = len(items)
    stats.rejects = {}
    if not items:
        return stats

    batches = _batches(items)
    if max_batches:
        batches = batches[:max_batches]
    stats.batches = len(batches)
    custom_path = paths.custom_path()
    custom = merger.load_custom(custom_path)
    rejects_path = paths.cache_path("translation-rejects.json")

    for i, batch in enumerate(batches, 1):
        if progress:
            progress(i, len(batches), len(batch))
        try:
            translated = translate_batch(
                dict(batch), api_key=api_key, base_url=base_url, model=model, provider=provider
            )
        except Exception as exc:
            stats.failed += len(batch)
            for key, val in batch:
                stats.rejects[key] = {"source": val, "error": str(exc)}
            continue

        for key, src in batch:
            # 手动锁定过的译文永不覆盖（用户核对过的成果比模型可靠）
            existing = custom.get(key)
            if existing is not None and existing.locked:
                stats.skipped += 1
                continue
            val = translated.get(key)
            if val is None or val.strip() == src.strip():
                stats.skipped += 1
                continue
            if validator.validate_entry(src, val):
                stats.failed += 1
                stats.rejects[key] = {"source": src, "translation": val, "error": "结构校验失败"}
                continue
            custom[key] = merger.CustomEntry(value=val, locked=False)
            stats.adopted += 1

        merger.save_custom(custom_path, custom)
        jsonutil.save_json(rejects_path, stats.rejects)

    merger.save_custom(custom_path, custom)
    jsonutil.save_json(rejects_path, stats.rejects)
    return stats

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any

from .models import EvidenceCard
from .story_model import FactClaim, StorySpec, build_story_spec, has_affirmed_action
from .util import clean_text


def _bounded_complete_sentence(value: str, limit: int) -> str:
    """Keep a complete clause; never slice a Chinese sentence or ASCII word."""
    text = clean_text(value).strip("。；;，, ")
    if not text:
        return ""
    if len(text) + 1 <= limit:
        return text + "。"
    window = text[: max(1, limit - 1)]
    boundaries = [match.end() for match in re.finditer(r"[。！？!?；;，,]", window)]
    boundary = next((position for position in reversed(boundaries) if position >= min(28, limit // 2)), 0)
    if not boundary:
        if re.search(r"[\u4e00-\u9fff]", window):
            return ""
        spaces = [match.start() for match in re.finditer(r"\s+", window)]
        boundary = next((position for position in reversed(spaces) if position >= min(28, limit // 2)), 0)
    if not boundary:
        return ""
    result = window[:boundary].rstrip("。！？!?；;，, ")
    if result.count("(") != result.count(")") or result.count("（") != result.count("）"):
        return ""
    if result.count("“") != result.count("”"):
        return ""
    return result + "。"


_ATTRIBUTION_PREFIX_RE = re.compile(
    r"^([^，。；;：:]{2,24}?(?:官方账号表示|开发者账号表示|回应称|表示|获悉|报道|介绍|宣布|称))[，,]"
)

_BALANCED_PAIRS = [("（", "）"), ("(", ")"), ("“", "”"), ("《", "》"), ("「", "」")]


def _dedupe_attribution_prefixes(sentences: list[str]) -> list[str]:
    """Broadcast-style attribution: state the attributor once, then continue.

    Only *consecutive* sentences with the *identical* attribution prefix lose
    the repeat ("Anthropic 表示，A。Anthropic 表示，B。" → "Anthropic 表示，A。
    B。"). A different attributor always keeps its prefix, so sourcing stays
    exact; the on-screen cards keep the full attribution regardless.
    """
    result: list[str] = []
    last_prefix = ""
    for sentence in sentences:
        match = _ATTRIBUTION_PREFIX_RE.match(sentence)
        if match and match.group(1) == last_prefix:
            remainder = sentence[match.end():].lstrip()
            if len(remainder) >= 8:
                sentence = remainder
        elif match:
            last_prefix = match.group(1)
        else:
            last_prefix = ""
        result.append(sentence)
    return result


def _breathe_semicolons(text: str) -> str:
    """Split narration at top-level Chinese semicolons so TTS can breathe.

    Only semicolons outside every bracket/quote pair become full stops;
    anything nested stays untouched, and balance is never broken.
    """
    # clean_text normalizes fullwidth punctuation, so both variants appear.
    if "；" not in text and ";" not in text:
        return text
    out: list[str] = []
    depth = {opener: 0 for opener, _closer in _BALANCED_PAIRS}
    closers = {closer: opener for opener, closer in _BALANCED_PAIRS}
    for char in text:
        if char in depth:
            depth[char] += 1
        elif char in closers:
            depth[closers[char]] = max(0, depth[closers[char]] - 1)
        if char in {"；", ";"} and not any(depth.values()):
            out.append("。")
        else:
            out.append(char)
    return "".join(out)


@dataclass(slots=True)
class EditorialPlan:
    story_id: str
    topic_key: str
    kind: str
    title: str
    lead: str
    facts: list[str]
    claim_ids: list[str]
    impact: str
    caution: str
    audience: list[str] = field(default_factory=list)
    source_status: str = ""
    editorial_tier: str = "headline"
    narration_override: str = ""

    def narration(self) -> str:
        if self.narration_override:
            return clean_text(self.narration_override)
        if self.editorial_tier == "brief":
            # A brief is a factual headline plus the attributable claims that
            # survive the dedup filters.  Published agent-reviewed editions
            # consistently narrate two to three approved facts per brief, so
            # the frozen draft speaks the same density by default; agent
            # review may still trim, and the impact analysis plus generic
            # caveats stay in the full explainer.
            facts = list(self.facts[:3])
            if len(facts) > 1:
                # The title/lead already carries the main event. Prefer the
                # next concrete detail over repeating the same launch or
                # departure in slightly different words.
                facts = [fact for fact in facts if not _facts_too_similar(self.lead, fact)] or facts[:1]
            if self.kind == "industry":
                facts = facts[:2]
            parts = [self.lead, *facts[:3]]
            result: list[str] = []
            for raw in parts:
                remaining = 210 - len("".join(result))
                if remaining < 24:
                    break
                sentence = _bounded_complete_sentence(raw, remaining)
                normalized = sentence.rstrip("。")
                if sentence and normalized not in "".join(result):
                    result.append(sentence)
            return _breathe_semicolons("".join(_dedupe_attribution_prefixes(result)))
        fact_budget = 240
        selected_facts: list[str] = []
        used = 0
        for fact in self.facts[:3]:
            if used + len(fact) > fact_budget and selected_facts:
                continue
            selected_facts.append(fact)
            used += len(fact)
        parts = [self.lead, *selected_facts, self.impact, self.caution]
        seen: set[str] = set()
        sentences: list[str] = []
        for raw in parts:
            text = clean_text(raw).strip("。；;，, ")
            if not text:
                continue
            norm = re.sub(r"\W+", "", text.lower())
            if not norm or norm in seen:
                continue
            if any(norm in old and min(len(norm), len(old)) >= 14 for old in seen):
                continue
            if any(
                old in norm
                and min(len(norm), len(old)) >= 14
                and len(norm) - len(old) < 10
                for old in seen
            ):
                continue
            seen.add(norm)
            sentences.append(text + "。")
        return _breathe_semicolons("".join(_dedupe_attribution_prefixes(sentences)))

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["narration"] = self.narration()
        return payload


BRAND_NAMES = {
    "OpenAI": "OpenAI",
    "Anthropic": "Anthropic",
    "CircleCI": "CircleCI",
    "Google": "Google",
    "GitHub": "GitHub",
    "Qwen": "阿里千问",
    "DeepSeek": "DeepSeek",
    "Kimi": "Kimi",
    "NVIDIA": "NVIDIA",
}

SENSATIONAL_TITLE_PATTERNS = [
    r"跑路", r"狂喜", r"炸裂", r"刚刚", r"重磅", r"突发", r"刷屏",
    r"消失.{0,12}(?:后|发帖)", r"只为(?:它|他|她|这)", r"最强", r"卖[“”\"']?低价",
    r"看了.{0,24}机器人.{0,8}悟了",
    r"[?？]{2,}", r"[!！]{3,}",
]

ORGANIZATION_ACTION_TERMS = [
    "离职", "辞职", "卸任", "离开", "任命", "接任", "就任", "重组", "并入", "调任",
    "resign", "depart", "step down", "appoint", "reorgan", "merge into",
]


def _clean_subject(subject: str) -> str:
    subject = clean_text(subject).strip(" /-")
    return BRAND_NAMES.get(subject, subject or "AI")


def _mentions_product(text: str, token: str) -> bool:
    """Word-bounded match for short tokens like "bun" (substring would hit "bundles"/"bunch")."""
    if token == "bun":
        return re.search(r"(?<![a-z0-9])bun(?![a-z0-9])", text) is not None
    return token in text


def _headline_object(spec: StorySpec) -> str:
    title = clean_text(spec.headline)
    replacements = [
        r"^openai['’]s\s+",
        r"^openai\s+",
        r"^github\s+",
        r"^anthropic\s+",
    ]
    for pattern in replacements:
        title = re.sub(pattern, "", title, flags=re.I).strip()
    for raw in ["正式发布", "发布", "推出", "上线", "接入", "新增", "更新", "available in", "is now"]:
        if raw.lower() in title.lower():
            parts = re.split(re.escape(raw), title, maxsplit=1, flags=re.I)
            if len(parts) == 2 and clean_text(parts[1]):
                return clean_text(parts[1])
    return title


def _product_from_claims(spec: StorySpec) -> str:
    headline = spec.headline.lower()
    claim_texts = [claim.text.lower() for claim in spec.claims if claim.renderable]
    patterns = [
        (r"\bchatgpt\s+work\b", "ChatGPT Work"),
        (r"\bgithub\s+copilot\b", "GitHub Copilot"),
        (r"\bmicrosoft\s+365\s+copilot\b", "Microsoft 365 Copilot"),
        (r"\bcodex\b", "Codex"),
        (r"\bclaude\s+code\b", "Claude Code"),
    ]
    for pattern, label in patterns:
        if re.search(pattern, headline, flags=re.I):
            return label
        # A single background comparison in a long article must not rename the
        # selected event.  Claim-only inference is reserved for a product that
        # recurs across at least two independently extracted facts.
        if sum(bool(re.search(pattern, text, flags=re.I)) for text in claim_texts) >= 2:
            return label
    return ""


def _named_model_from_headline(headline: str) -> str:
    match = re.search(
        r"(?<![A-Za-z0-9])(?:gpt|claude|gemini|gemma|qwen|deepseek|kimi|llama|mistral|grok|minimax|glm|hunyuan|nova)"
        r"[-‐‑–—_\s]?[a-z]*\d[A-Za-z0-9_.‐‑–—-]*(?=$|[^A-Za-z0-9_.‐‑–—-])",
        headline,
        flags=re.I,
    )
    return clean_text(match.group(0)) if match else ""


def _subscription_change_title(spec: StorySpec) -> str:
    """把暂停订阅和会员拆分压缩成当前运营事件标题。"""
    claims = [claim.text for claim in spec.claims if claim.renderable and claim.verifiable]
    paused = False
    adjusted = False
    for text in claims:
        lowered = clean_text(text).lower()
        # 每条 claim 独立判断，防止把一条的动作和另一条的订阅对象拼成新事实。
        if any(term in lowered for term in ["订阅", "subscription"]):
            paused = paused or any(
                has_affirmed_action(lowered, term) for term in ["暂停", "停止", "pause", "suspend"]
            )
        if any(term in lowered for term in ["会员", "membership"]):
            adjusted = adjusted or any(
                has_affirmed_action(lowered, term) for term in ["拆分", "调整", "分为", "split"]
            )
    if not paused and not adjusted:
        return ""
    subject = _clean_subject(spec.subject)
    if paused and adjusted:
        return f"{subject} 暂停新订阅并调整会员方案"
    if paused:
        return f"{subject} 暂停新订阅"
    return f"{subject} 调整会员方案"


_PRERELEASE_VERSION_PATTERN = re.compile(
    r"\bv?\d+(?:\.\d+){1,4}-(?:alpha|beta|rc)(?:[.-]\d+)*\b",
    flags=re.I,
)
_STABLE_VERSION_PATTERN = re.compile(
    r"\bv?\d+(?:\.\d+){1,4}(?!\.\d)(?!-(?:alpha|beta|rc))(?=$|[^A-Za-z0-9_.-])",
    flags=re.I,
)


def _version_has_release_binding(text: str, match: re.Match[str]) -> bool:
    """要求发布动作紧邻版本号，避免同句中的历史版本借用别处动作。"""
    prefix = text[max(0, match.start() - 40) : match.start()]
    suffix = text[match.end() : match.end() + 24]
    if re.search(
        r"(?:替代|取代|替换|升级自|停止维护|弃用|淘汰|replaces?|supersedes?|from|deprecat(?:e|ed|ing))\s*$",
        prefix,
        flags=re.I,
    ):
        return False
    return bool(
        re.search(
            r"(?:预发布|正式发布|发布|推出|上线|release(?:d)?|tag)"
            r"(?:的)?(?:版本号)?(?:为|是)?\s*[:：-]?\s*$",
            prefix,
            flags=re.I,
        )
        or re.search(r"^\s*(?:预发布|正式发布|发布|推出|上线|release(?:d)?)\b", suffix, flags=re.I)
    )


def _current_prerelease_version(spec: StorySpec) -> str:
    """返回当前发布动作绑定的预发布版本，忽略被替代的历史版本。"""
    headline = clean_text(spec.headline)
    matches = list(_PRERELEASE_VERSION_PATTERN.finditer(headline))
    for match in matches:
        suffix = headline[match.end() : match.end() + 20]
        stable_versions = _STABLE_VERSION_PATTERN.findall(headline)
        exact_tag = (
            not stable_versions
            and not clean_text(suffix)
            and spec.kind in {"developer_release", "model_release", "model_update"}
        )
        if _version_has_release_binding(headline, match) or exact_tag:
            return match.group(0).lstrip("vV")
    for claim in spec.claims:
        if not claim.renderable or not claim.verifiable:
            continue
        for claim_match in _PRERELEASE_VERSION_PATTERN.finditer(claim.text):
            if _version_has_release_binding(claim.text, claim_match):
                return claim_match.group(0).lstrip("vV")
    return ""


def _current_stable_release_version(spec: StorySpec) -> str:
    """只从“正式发布、升级至正式版、正式版替代”结构提取当前稳定版本。"""
    texts = [
        claim.text
        for claim in spec.claims
        if claim.renderable and claim.verifiable
    ] + [clean_text(spec.headline)]
    version_pattern = _STABLE_VERSION_PATTERN.pattern
    for text in texts:
        upgrade = re.search(
            rf"(?:升级至|更新至|升至)\s*(?P<version>{version_pattern})\s*(?:的)?正式版",
            text,
            flags=re.I,
        )
        if upgrade:
            return upgrade.group("version").lstrip("vV")
        direct = re.search(
            rf"(?:正式发布|正式上线|发布正式版)\s*[:：-]?\s*(?P<version>{version_pattern})",
            text,
            flags=re.I,
        )
        if direct:
            return direct.group("version").lstrip("vV")
        for match in _STABLE_VERSION_PATTERN.finditer(text):
            suffix = text[match.end() : match.end() + 24]
            prefix = text[max(0, match.start() - 12) : match.start()]
            if re.match(r"^\s*正式版(?:\s*(?:发布|上线|替代|取代))?", suffix) and not re.search(
                r"(?:基于|来自|兼容)\s*$",
                prefix,
            ):
                return match.group(0).lstrip("vV")
    return ""


def _release_title_prefix(spec: StorySpec) -> str:
    """组合去重后的厂商与产品前缀。"""
    subject = _clean_subject(spec.subject)
    product = _product_from_claims(spec)
    return f"{subject} {product}" if product and product.lower() not in subject.lower() else subject


def _stable_release_title(spec: StorySpec) -> str:
    """稳定版本存在时优先保留正式版号，避免退化成泛产品标题。"""
    if spec.kind not in {"developer_release", "model_release", "model_update", "official_update"}:
        return ""
    version = _current_stable_release_version(spec)
    if not version:
        return ""
    return f"{_release_title_prefix(spec)} 正式发布 {version}"


def _prerelease_title(spec: StorySpec) -> str:
    """为 Alpha、Beta 和 RC 版本明确标注预发布属性。"""
    if spec.kind not in {"developer_release", "model_release", "model_update", "official_update"}:
        return ""
    version = _current_prerelease_version(spec)
    if not version:
        return ""
    return f"{_release_title_prefix(spec)} 预发布 {version}"


def _title(spec: StorySpec) -> str:
    raw_headline = clean_text(spec.headline)
    media_tail = re.search(
        r"(?:[|｜/]\s*|[-—]\s*)(36\s*氪|InfoQ|量子位)(?:独家|首发)?(?:\.cn)?\s*$",
        raw_headline,
        flags=re.I,
    )
    localized_headline = raw_headline
    localized_headline = re.sub(r"(?<![A-Za-z0-9])Circle\s+CI(?![A-Za-z0-9])", "CircleCI", localized_headline, flags=re.I)
    localized_headline = re.sub(
        r"\s*(?:[|｜/]\s*|[-—]\s*)?(?:36\s*氪|InfoQ)(?:独家|首发)?(?:\.cn)?\s*$",
        "",
        localized_headline,
        flags=re.I,
    ).strip(" |｜/-—")
    subscription_title = _subscription_change_title(spec)
    if subscription_title and spec.kind != "model_release":
        return subscription_title
    prerelease_title = _prerelease_title(spec)
    if prerelease_title:
        return prerelease_title
    stable_release_title = _stable_release_title(spec)
    if stable_release_title:
        return stable_release_title
    if any(re.search(pattern, localized_headline, flags=re.I) for pattern in SENSATIONAL_TITLE_PATTERNS):
        neutral_title = _neutral_title_from_claims(spec)
        if neutral_title:
            return neutral_title
    personnel_evidence = any(
        str(reliability or "").lower() in {"official_personnel", "official_staff"}
        for claim in spec.claims
        for reliability in claim.evidence_reliability
    )
    if personnel_evidence and re.search(r"[\u4e00-\u9fff]", localized_headline) and len(localized_headline) <= 72:
        # A verified staff post is an attributed signal, not proof of a fresh
        # product launch.  Preserve its factual source headline instead of
        # synthesizing misleading titles such as "OpenAI launches ChatGPT Work".
        return localized_headline.rstrip(" \uff1a:\uff1b;\uff0c,")
    if spec.source_status == "single_media" and media_tail and "探索自动驾驶" in localized_headline:
        source = re.sub(r"\s+", "", media_tail.group(1))
        subject = clean_text(localized_headline.split("探索自动驾驶", 1)[0]).strip(" ，,：:")
        if subject:
            return f"{source}称{subject}正探索自动驾驶"
    if (
        re.search(r"[\u4e00-\u9fff]", localized_headline)
        and any(action in localized_headline for action in ["发布", "推出", "上线", "接入", "新增", "更新", "升级", "支持", "开放"])
        and len(localized_headline) <= 72
    ):
        return localized_headline.rstrip(" ：:；;，,")
    subject = _clean_subject(spec.subject)
    model = _named_model_from_headline(spec.headline)
    headline_product = next((label for token, label in [("chatgpt work", "ChatGPT Work"), ("github copilot", "GitHub Copilot"), ("microsoft 365 copilot", "Microsoft 365 Copilot"), ("claude code", "Claude Code"), ("bun", "Bun")] if _mentions_product(spec.headline.lower(), token)), "")
    product = headline_product or ("" if model else _product_from_claims(spec))
    if headline_product:
        product_at = spec.headline.lower().find(headline_product.lower())
        candidate = clean_text(spec.headline[product_at:]) if product_at >= 0 else ""
        if re.search(r"[\u4e00-\u9fff]", candidate) and any(
            action in candidate for action in ["发布", "推出", "上线", "接入", "新增", "更新", "升级", "支持"]
        ):
            return candidate[:72].rstrip(" ：:；;，,")
    obj = model or product or _headline_object(spec)
    action = spec.action
    if spec.kind == "model_release" and action in {"动态", "新增", "更新"}:
        action = "发布"
    elif spec.kind == "product_update" and action in {"动态", "推出"}:
        action = "上线"
    elif product == "ChatGPT Work" and action in {"动态", "推出"}:
        action = "上线"
    if product == "ChatGPT Work" and action == "上线":
        title = f"{subject} 上线 {obj}"
    elif action != "动态" and action not in obj:
        title = f"{subject}{action}{obj}"
    else:
        title = localized_headline
    title = re.sub(r"\s+", " ", title).strip(" ：:；;，,")
    return title[:72].rstrip(" ：:；;，,")


def _normalize_public_fact(text: str) -> str:
    text = clean_text(text)
    text = re.sub(r"^#+\s*", "", text)
    text = re.sub(r"…+|\.{3,}", "", text)
    text = re.sub(r"将\s+把", "将把", text)
    text = re.sub(r"^针对以上消息\s*[,，]\s*", "", text)
    response = re.match(
        r"^(?P<source>[^,，]{1,20})向(?P<org>[^,，]{1,16})求证\s*[,，]\s*"
        r"(?P=org)回应\s*[:：]?\s*[“\"']?(?P<body>.+)$",
        text,
    )
    if response:
        source = clean_text(response.group("source"))
        org = clean_text(response.group("org"))
        body = clean_text(response.group("body")).strip(" ”\"'。")
        body = re.sub(rf"^{re.escape(org)}在", "其在", body)
        body = re.sub(rf"^{re.escape(org)}(?=大模型|AI|物理\s*AI)", "其在", body, flags=re.I)
        body = body.replace("但并没有做智能驾驶业务的计划", "但没有开展智能驾驶业务的计划")
        body = body.replace("并没有做智能驾驶业务的计划", "没有开展智能驾驶业务的计划")
        text = f"{org}向 {source} 回应称，{body}"
    if "工具调用" in text and "计算机操作" in text and any(
        term in text.lower() for term in ["agentic coding", "智能体编程", "智能体和编程模型"]
    ):
        return "该模型面向智能体编程，重点覆盖工具调用和计算机操作"
    text = re.sub(r"(?:并将|以及|和|与)\s*$", "", text).rstrip(" ，,；;")
    replacements = [
        (r"请通过插件连接你已经开展工作的工具和上下文", "可通过插件连接应用和文件"),
        (r"从今天起在网页端和移动端推出", "已在网页端和移动端推出"),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.I)
    if len(text) <= 92:
        return text

    # Long source excerpts often join the useful fact, an article headline and
    # navigation debris with commas. Keep a dense, complete clause window
    # instead of cutting a model name in half.
    clauses = [clean_text(part) for part in re.split(r"[。！？!?；;，,]+", text) if clean_text(part)]
    candidates: list[str] = []
    for start in range(len(clauses)):
        current: list[str] = []
        for end in range(start, min(len(clauses), start + 4)):
            current.append(clauses[end])
            candidate = "，".join(current)
            if len(candidate) > 92:
                break
            if len(candidate) >= 20:
                candidates.append(candidate)

    def score(candidate: str) -> tuple[int, int]:
        lowered = candidate.lower()
        concrete = sum(
            term in lowered
            for term in [
                "发布", "推出", "上线", "开放", "支持", "新增", "提升", "降低", "得分", "调用",
                "agent", "api", "benchmark", "模型", "工作流", "开源", "价格", "延迟", "吞吐",
            ]
        )
        digits = len(re.findall(r"\d", candidate))
        debris = sum(term in lowered for term in ["公众号", "发自", "首页", "热门文章", "听雨", "凹非寺", "utm_"])
        dangling = int(bool(re.search(r"(?:比|与|和|并|以及|包括|例如|fable|programmatic tool)$", lowered)))
        unbalanced = candidate.count("(") != candidate.count(")") or candidate.count("（") != candidate.count("）")
        return (
            concrete * 12 + digits * 3 + min(len(candidate), 72) - debris * 45 - dangling * 40 - (60 if unbalanced else 0),
            len(candidate),
        )

    if candidates:
        result = max(candidates, key=score).rstrip(" ，,；;")
        result = re.sub(r"\s+[A-Za-z][A-Za-z0-9_.-]*一发布.*$", "", result)
        result = re.sub(r"\s*[”\"']?\s*三大维度.*$", "", result)
        if result.count("(") != result.count(")") or result.count("（") != result.count("）"):
            return ""
        return result.rstrip(" ，,；;")
    return _bounded_complete_sentence(text, 92).rstrip("。")


def _fact_signature(text: str) -> set[str]:
    lowered = clean_text(text).lower()
    tokens = set(re.findall(r"[a-z][a-z0-9_.-]{2,}|\d+(?:\.\d+)?%?", lowered))
    cjk = "".join(re.findall(r"[\u4e00-\u9fff]", lowered))
    tokens.update(cjk[index : index + 2] for index in range(max(0, len(cjk) - 1)))
    return tokens


def _facts_too_similar(left: str, right: str) -> bool:
    ascii_pattern = r"[a-z][a-z0-9_.-]{2,}|\d+(?:\.\d+)?%?"
    ascii_left = set(re.findall(ascii_pattern, clean_text(left).lower()))
    ascii_right = set(re.findall(ascii_pattern, clean_text(right).lower()))
    ascii_shared = ascii_left & ascii_right
    if (
        len(ascii_shared) >= 3
        and any(re.search(r"\d", token) for token in ascii_shared)
        and len(ascii_shared) / max(1, min(len(ascii_left), len(ascii_right))) >= 0.7
    ):
        return True
    a = _fact_signature(left)
    b = _fact_signature(right)
    if not a or not b:
        return False
    return len(a & b) / max(1, min(len(a), len(b))) >= 0.58


def _is_time_bound_detail(text: str) -> bool:
    value = clean_text(text)
    return bool(
        re.search(r"(?:截止(?:日期|时间)?|提交|投稿|报名|申请).{0,24}(?:\d{1,2}\s*月\s*\d{1,2}\s*日|\d{4}[-/.]\d{1,2}[-/.]\d{1,2})", value)
        or re.search(r"(?:\d{1,2}\s*月\s*\d{1,2}\s*日|\d{4}[-/.]\d{1,2}[-/.]\d{1,2}).{0,24}(?:截止|提交|投稿|报名|申请)", value)
    )


def _personnel_claims_too_similar(left: str, right: str) -> bool:
    """Deduplicate restatements without erasing a different numeric update."""
    number_pattern = r"\d+(?:\.\d+)?%?"
    left_numbers = set(re.findall(number_pattern, clean_text(left)))
    right_numbers = set(re.findall(number_pattern, clean_text(right)))
    if left_numbers != right_numbers:
        return False
    return _facts_too_similar(left, right)


def _editorial_claim_rank(claim: FactClaim, spec: StorySpec) -> tuple[int, int, int]:
    priority = {"change": 5, "availability": 5, "pricing": 5, "metric": 5, "legal": 5, "research": 3, "statement": 1}.get(claim.claim_type, 1)
    if spec.kind == "case_study" and claim.claim_type == "metric":
        priority += 4
    lowered = claim.text.lower()
    organization_action = _is_organization_action_claim(claim.text)
    if organization_action:
        priority += _organization_action_priority(claim.text)
    if any(term in lowered for term in ["新增", "支持", "开放", "可用", "推出", "上线", "调用", "入口", "上下文"]):
        priority += 2
    if _is_time_bound_detail(claim.text):
        # A deadline is a separate actionable fact, not a restatement of the
        # surrounding launch or submission announcement.
        priority += 7
    if "工具调用" in lowered and "计算机操作" in lowered:
        priority += 6
    if "获悉" in lowered:
        priority += 6
    if "回应" in lowered:
        priority += 5
    if "没有" in lowered and "计划" in lowered:
        priority += 2
    if re.search(r"(?:项目|业务).{0,32}(?:由|隶属于).{0,36}(?:团队|部门|业务线).{0,12}负责", claim.text):
        priority += 10
    if any(term in lowered for term in ["搜狐网", "新浪网", "utm_source=", "utm_medium=", "案例"]):
        priority -= 5
    if not organization_action and any(term in lowered for term in ["客户", "经理", "负责人", "准备工作"]):
        priority -= 5
    if claim.claim_type in {"availability", "pricing"} and any(term in lowered for term in ["面向", "推出", "开放", "plus", "pro", "enterprise"]):
        priority += 4
    headline_products = {product for product in ["chatgpt work", "github copilot", "microsoft 365 copilot", "codex", "claude code", "bun"] if _mentions_product(spec.headline.lower(), product)}
    if not headline_products:
        inferred = _product_from_claims(spec).lower()
        if inferred:
            headline_products.add(inferred)
    claim_products = {product for product in ["chatgpt work", "github copilot", "microsoft 365 copilot", "codex", "claude code", "bun"] if _mentions_product(lowered, product)}
    if headline_products and claim_products and not (headline_products & claim_products):
        priority -= 6
    # Long media articles often contain several adjacent stories.  Prefer
    # claims that actually overlap the selected headline, rather than a more
    # numeric but unrelated Siri, data-leak or benchmark paragraph.  English
    # product tokens and Chinese bigrams make this generic across unseen news.
    def focus_terms(value: str) -> set[str]:
        lowered_value = clean_text(value).lower()
        terms = set(re.findall(r"[a-z][a-z0-9_.-]{2,}|\d+(?:\.\d+)?[a-z]*", lowered_value))
        for run in re.findall(r"[\u4e00-\u9fff]{2,}", lowered_value):
            terms.update(run[index:index + 2] for index in range(len(run) - 1))
        return terms - {
            "网友", "公司", "模型", "发布", "更新", "相关", "目前", "表示", "可以", "已经",
            "一个", "这个", "以及", "进行", "通过", "来自", "正式", "其中", "他们", "我们",
        }

    headline_terms = focus_terms(spec.headline)
    shared_terms = headline_terms & focus_terms(claim.text)
    priority += min(8, len(shared_terms) * 2)
    if len(headline_terms) >= 4 and len(shared_terms) <= 1:
        priority -= 3
    return priority, claim.specificity, -len(claim.text)


def _best_claims(spec: StorySpec, limit: int = 2) -> list[FactClaim]:
    claims = [claim for claim in spec.claims if claim.renderable and claim.verifiable and claim.specificity >= 28]
    official_personnel_signal = any("X 官方人员" in warning for warning in spec.warnings)
    target_product = _product_from_claims(spec).lower()
    if target_product and not official_personnel_signal:
        focused = [claim for claim in claims if target_product in claim.text.lower()]
        # Product stories should not drift to sibling products merely because a
        # long launch page also contains their changelog. A concrete personnel
        # or organization action is the exception: it is an event, not a
        # testimonial, and must not disappear behind a repeated product token.
        if focused:
            organization_actions = [claim for claim in claims if _is_organization_action_claim(claim.text)]
            claims = list({claim.claim_id: claim for claim in [*focused, *organization_actions]}.values())
    if official_personnel_signal:
        # A same-account personnel series often contains the concrete scope,
        # usage delta and milestone in separate posts. Keep those attributable
        # facts instead of collapsing the story to a generic "负责人说明调整"
        # synopsis merely because every useful sentence names the speaker.
        concrete = [
            claim
            for claim in claims
            if re.search(r"\d|%|将继续|开放|加入|回退|修复|达到|可用|订阅", claim.text)
        ]
        if len(concrete) >= 2:
            claims = concrete
        headline_products = {
            product
            for product in ["chatgpt work", "github copilot", "microsoft 365 copilot", "codex", "claude code", "bun"]
            if _mentions_product(spec.headline.lower(), product)
        }
        claims.sort(
            key=lambda claim: (
                sum(_mentions_product(claim.text.lower(), product) for product in headline_products),
                claim.claim_type == "metric",
                claim.specificity,
                _editorial_claim_rank(claim, spec),
            ),
            reverse=True,
        )
    else:
        non_testimonial = [
            claim
            for claim in claims
            if _is_organization_action_claim(claim.text)
            or not any(term in claim.text for term in ["负责人", "经理", "客户", "准备工作"])
        ]
        if non_testimonial:
            claims = non_testimonial
        claims.sort(key=lambda claim: _editorial_claim_rank(claim, spec), reverse=True)
    result: list[FactClaim] = []
    for claim in claims:
        norm = re.sub(r"\W+", "", claim.text.lower())
        old_norms = [re.sub(r"\W+", "", old.text.lower()) for old in result]
        if any(norm == old_norm for old_norm in old_norms):
            continue
        time_bound = _is_time_bound_detail(claim.text)
        if time_bound:
            if any(_is_time_bound_detail(old.text) and _facts_too_similar(claim.text, old.text) for old in result):
                continue
        elif any(norm in old_norm or old_norm in norm for old_norm in old_norms):
            continue
        if official_personnel_signal and any(_personnel_claims_too_similar(claim.text, old.text) for old in result):
            continue
        claim_terms = set(re.findall(r"[a-z][a-z0-9_.-]{2,}|[\u4e00-\u9fff]{2,}", claim.text.lower()))
        if not time_bound and not official_personnel_signal and any(
            len(claim_terms & set(re.findall(r"[a-z][a-z0-9_.-]{2,}|[\u4e00-\u9fff]{2,}", old.text.lower()))) / max(1, min(len(claim_terms), len(set(re.findall(r"[a-z][a-z0-9_.-]{2,}|[\u4e00-\u9fff]{2,}", old.text.lower()))))) >= 0.55
            for old in result
        ):
            continue
        result.append(claim)
        if len(result) >= limit:
            break
    return result


def _is_organization_action_claim(text: str) -> bool:
    lowered = clean_text(text).lower()
    return any(term in lowered for term in ORGANIZATION_ACTION_TERMS)


def _organization_action_priority(text: str) -> int:
    lowered = clean_text(text).lower()
    if any(term in lowered for term in ["离职", "辞职", "卸任", "离开", "resign", "depart", "step down"]):
        score = 14
    elif any(term in lowered for term in ["任命", "接任", "就任", "调任", "appoint"]):
        score = 10
    elif any(term in lowered for term in ["重组", "并入", "reorgan", "merge into"]):
        score = 5
    else:
        score = 4
    if any(term in lowered for term in ["负责人", "主管", "ceo", "chief", "head of"]):
        score += 3
    if re.search(r"\b[A-Z][A-Za-z.'-]+\s+[A-Z][A-Za-z.'-]+\b", clean_text(text)):
        score += 2
    if re.match(r"^(?:这是|这已经是|也是)", clean_text(text)) and re.search(r"第.{0,8}位", text):
        score -= 12
    if re.match(r"^20\d{2}年", clean_text(text)):
        score -= 7
    return score


def _release_event_title(text: str) -> str:
    """Extract a neutral subject-action-object title from a factual claim."""
    candidate = clean_text(text)
    candidate = re.sub(r"^(?:近日|日前|今天|昨日|本周|当地时间\s*[^,，]{0,16})\s*[,，:：]?\s*", "", candidate)
    candidate = re.sub(
        r"^(?:据|根据)\s*[^，,:：]{1,36}?(?:消息|报道|披露|称)?\s*[，,:：]\s*",
        "",
        candidate,
        flags=re.I,
    )
    match = re.match(
        r"^(?P<subject>.{2,30}?)\s*(?P<action>正式发布了|正式发布|发布了|发布|推出了|推出|上线了|上线|"
        r"开源了|开源|更新了|更新|升级了|升级|开放了|开放)\s*(?P<object>[^，,。；;!?！？]{2,42})",
        candidate,
        flags=re.I,
    )
    if not match:
        return ""
    subject = clean_text(match.group("subject")).strip(" ，,：:")
    obj = clean_text(match.group("object")).strip(" ，,：:")
    if not subject or not obj:
        return ""
    action = match.group("action").replace("了", "")
    spacer = " " if re.search(r"[A-Za-z0-9]$", subject) else ""
    return f"{subject}{spacer}{action}{obj}"


def _neutral_claim_title(text: str) -> str:
    event_title = _release_event_title(text)
    if event_title:
        return event_title[:72].rstrip(" ：:；;，,。")
    title = _normalize_public_fact(text)
    title = re.sub(
        r"\b(OpenAI|Anthropic|Google|Microsoft|Meta|xAI)(?=[\u4e00-\u9fff])",
        r"\1 ",
        title,
        flags=re.I,
    )
    title = re.sub(
        r"^(?:据|根据)\s*[^，,:：]{1,36}?(?:消息|报道|披露|称)?\s*[，,:：]\s*",
        "",
        title,
        flags=re.I,
    )
    title = re.sub(r"^(?:媒体|公开资料)(?:消息|报道|显示)?\s*[，,:：]\s*", "", title)
    title = re.sub(
        r"(?:已|已经)?\s*(?:告知|通知)\s*(?:公司内部|内部|员工|团队|同事)?\s*(?:自己|称自己)?\s*(?:将|会|准备)?\s*(离职|辞职|卸任|离开)",
        lambda match: f"将{match.group(1)}",
        title,
    )
    title = re.sub(
        r"(?:已|已经)?\s*(?:确认|宣布)\s*(?:自己)?\s*(?:将|会|准备)?\s*(离职|辞职|卸任|离开)",
        lambda match: f"将{match.group(1)}",
        title,
    )
    replacements = {"跑路": "离职", "狂喜": "", "炸裂": "", "刚刚": "", "重磅": "", "突发": "", "刷屏": ""}
    for old, new in replacements.items():
        title = title.replace(old, new)
    title = re.sub(r"[?？!！]+", "", title)
    title = re.sub(r"\s+", " ", title).strip(" ：:；;，,。-—|｜")
    if _is_organization_action_claim(title):
        action_clause = re.match(
            r"^(.{2,68}?(?:将)?(?:离职|辞职|卸任|离开))"
            r"(?:(?:[，,；;。].*)|(?:\s+(?:这是|也是|同时|并且|此外|随后|而).*))$",
            title,
        )
        if action_clause:
            title = action_clause.group(1).strip(" ：:；;，,。")
    if len(title) <= 72:
        return title
    clauses = [clean_text(part) for part in re.split(r"[。！？!?；;]+", title) if clean_text(part)]
    action_clause = next((part for part in clauses if _is_organization_action_claim(part) and len(part) <= 72), "")
    if action_clause:
        return action_clause.rstrip(" ：:；;，,。")
    return _bounded_complete_sentence(title, 72).rstrip("。")


def _neutral_title_from_claims(spec: StorySpec) -> str:
    claims = [
        claim
        for claim in spec.claims
        if claim.renderable and claim.verifiable and claim.specificity >= 28
    ]
    def title_rank(claim: FactClaim) -> tuple[int, int, int, int, int]:
        organization = _organization_action_priority(claim.text) if _is_organization_action_claim(claim.text) else 0
        release = 18 if _release_event_title(claim.text) else 0
        editorial = _editorial_claim_rank(claim, spec)
        return organization, release, *editorial

    claims.sort(key=title_rank, reverse=True)
    for claim in claims:
        title = _neutral_claim_title(claim.text)
        if title and not any(re.search(pattern, title, flags=re.I) for pattern in SENSATIONAL_TITLE_PATTERNS):
            return title
    return _neutral_claim_title(spec.headline)


def _impact(spec: StorySpec) -> str:
    claim_text = " ".join(claim.text for claim in spec.claims if claim.renderable).lower()
    if any(term in claim_text for term in ["apps and files", "插件连接", "跨应用", "长任务"]):
        return "对用户和团队来说，关键变化是智能体从回答问题走向跨应用执行工作。"
    if spec.kind == "case_study":
        return "它的参考价值在工程方法、协作规模和结果指标，而不是厂商宣传。"
    if spec.kind == "benchmark":
        return "它适合帮助开发者做模型或部署选型，但不同测试口径不能直接横比。"
    if spec.kind == "research":
        return "它目前首先影响研究判断，是否能进入产品还要看复现和工程成本。"
    # Do not fill a news page with generic "why it matters" copy.  Product,
    # model and developer updates should earn their density from attributable
    # facts (scope, availability, numbers and deltas).  Codex may keep or drop
    # the verified blocks later, but the frozen draft must not manufacture an
    # analysis card merely to make the layout look full.
    return ""


def _caution(spec: StorySpec) -> str:
    if spec.source_status == "single_media":
        return "目前只有一家媒体给出相关信息，数字和过程尚未获得当事方确认。"
    if spec.source_status == "media_cross_checked":
        return "多家媒体均有报道，但当事方尚未公开的细节仍需谨慎理解。"
    if spec.source_status == "community":
        return "这是社区观察，不等同于官方结论。"
    if spec.kind == "benchmark":
        return "性能结论需结合模型版本、硬件和测试条件理解。"
    return ""


def build_editorial_plan(card: EvidenceCard) -> EditorialPlan:
    spec = build_story_spec(card)
    title = _title(spec)
    # Keep up to four independently verifiable details for the on-screen fact
    # board.  Narration still uses at most two, so density rises without
    # turning every fast item into a long monologue.
    # Headline stories carry the deep-dive treatment: up to six verified
    # claims feed the card board (the reference series shows six facet cards
    # per lead story), while briefs stay lean at four. Card count still only
    # grows when that many distinct verified claims actually exist.
    claim_budget = 6 if str(card.editorial_tier or "headline") == "headline" else 4
    selected_claims = _best_claims(spec, limit=claim_budget)
    first_party_supported = bool(selected_claims) and all(claim.first_party_supported for claim in selected_claims)
    official_only = card.official_count > 0 and card.media_count == 0 and card.community_count == 0
    source_label = clean_text(str((card.evidence_links[0] if card.evidence_links else {}).get("source") or ""))
    source_label = re.sub(r"\s*(?:中文|官方)$", "", source_label, flags=re.I)
    if first_party_supported or official_only:
        # Official stories can lead with the change itself. Repeating
        # "官方信息显示" before every item makes the edition sound templated;
        # attribution remains visible in the source/time block and evidence.
        lead = title
    elif re.match(r"^(?:36\s*氪|InfoQ|量子位)(?:称|报道)", title, flags=re.I):
        lead = title
    elif spec.source_status in {"single_media", "media_cross_checked"} and source_label:
        upstream = next(
            (
                match.group(1)
                for claim in selected_claims
                if (match := re.match(r"^据\s*([^,，]{1,24}?)(?:消息|报道)\s*[,，]", clean_text(claim.text)))
            ),
            "",
        )
        if upstream and upstream.lower() not in source_label.lower():
            lead = f"{source_label}援引 {upstream} 报道，{title}"
        else:
            lead = f"据 {source_label} 报道，{title}"
    else:
        source_prefix = "公开资料显示"
        if spec.kind == "case_study" and spec.source_status == "single_media":
            source_prefix = "媒体案例显示"
        lead = f"{source_prefix}，{title}"
    public_claims: list[tuple[FactClaim, str]] = []
    for claim in selected_claims:
        public_fact = _normalize_public_fact(claim.text)
        if (
            spec.kind != "industry"
            and len(selected_claims) > 1
            and not _is_time_bound_detail(public_fact)
            and _facts_too_similar(title, public_fact)
        ):
            continue
        if not public_fact or any(_facts_too_similar(public_fact, old_fact) for _old, old_fact in public_claims):
            continue
        public_claims.append((claim, public_fact))
    return EditorialPlan(
        story_id=spec.story_id,
        topic_key=spec.topic_key,
        kind=spec.kind,
        title=title,
        lead=lead,
        facts=[fact for _claim, fact in public_claims],
        claim_ids=[claim.claim_id for claim, _fact in public_claims],
        impact=_impact(spec),
        caution=_caution(spec),
        audience=spec.audience,
        source_status=spec.source_status,
        editorial_tier=card.editorial_tier or "headline",
    )

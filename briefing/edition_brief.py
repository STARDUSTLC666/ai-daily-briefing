from __future__ import annotations

"""Deterministic, auditable direction for a complete briefing edition.

The process-local copy is only a convenience for the existing writer API.  The
same value is persisted in ``manifest.json`` so a render never depends on an
unrecorded model response.
"""

from copy import deepcopy
import re
from typing import Any

from .models import EvidenceCard
from .social_signals import card_is_community_signal, card_is_official_personnel_signal
from .story_model import build_story_spec

_current: dict[str, Any] = {}

_BRANDS = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "google": "Google",
    "deepmind": "Google DeepMind",
    "github": "GitHub",
    "nvidia": "NVIDIA",
    "meta": "Meta",
    "xai": "xAI",
    "qwen": "阿里千问",
    "通义": "阿里千问",
    "deepseek": "DeepSeek",
    "kimi": "Kimi",
    "minimax": "MiniMax",
    "腾讯": "腾讯",
    "混元": "腾讯混元",
    "百度": "百度",
    "字节": "字节跳动",
}

_KIND_LABELS = {
    "model_release": "发布模型更新",
    "model_update": "更新模型能力",
    "developer_release": "更新开发者工具",
    "product_update": "更新产品功能",
    "research": "公布研究进展",
    "benchmark": "更新评测结果",
    "case_study": "披露应用案例",
    "industry": "出现行业变化",
}


def install_edition_brief(value: dict[str, Any] | None) -> None:
    global _current
    _current = deepcopy(value or {})


def get_edition_brief(cards: list[EvidenceCard] | None = None) -> dict[str, Any]:
    cards = cards or []
    current_ids = {build_story_spec(card).story_id for card in cards}
    brief_ids = set((_current.get("story_roles") or {}).keys())
    if _current and (not cards or brief_ids == current_ids):
        return deepcopy(_current)
    return deterministic_edition_brief(cards)


def _safe_subject(card: EvidenceCard) -> str:
    raw = str(card.entity or "").strip()
    semantic = str(build_story_spec(card).entity or "").strip()
    haystack = f"{semantic} {raw} {card.event_title}".lower()
    for token, label in _BRANDS.items():
        if token in haystack:
            product = ""
            # Canonical casing per product: .title() mangles brand names ("Chatgpt").
            product_labels = {
                "codex": "Codex",
                "copilot": "Copilot",
                "claude code": "Claude Code",
                "chatgpt": "ChatGPT",
                "hy3": "Hy3",
                "flare auto-fl": "Flare Auto-FL",
            }
            for candidate in re.findall(r"(?i)(?:codex|copilot|claude code|chatgpt|hy3|flare auto-fl)", haystack):
                product = product_labels.get(candidate.lower(), candidate)
                break
            return f"{label} {product}".strip()
    raw = re.sub(r"\s+", " ", semantic or raw).strip(" /-：:")
    # Long English source headlines are not public copy.  A generic Chinese
    # subject is safer than leaking them into the opening hook.
    if not raw or len(raw) > 24 or len(re.findall(r"[A-Za-z][A-Za-z0-9.-]*", raw)) > 3:
        return "一项 AI 项目"
    return raw


def public_story_label(card: EvidenceCard) -> str:
    spec = build_story_spec(card)
    label = f"{_safe_subject(card)}{_KIND_LABELS.get(spec.kind, '出现新动态')}"
    if card_is_official_personnel_signal(card):
        return f"一线消息：{label}"
    if card_is_community_signal(card):
        return f"传闻/风向：{label}"
    return label


def deterministic_edition_brief(cards: list[EvidenceCard]) -> dict[str, Any]:
    specs = [build_story_spec(card) for card in cards]
    ids = [spec.story_id for spec in specs]
    local_headline = next((spec.story_id for card, spec in zip(cards, specs) if card.editorial_tier == "headline"), "")
    lead = local_headline or (ids[0] if ids else "")
    secondary = next((story_id for story_id in ids if story_id != lead), "")
    labels = [public_story_label(card) for card in cards[:2]]
    if labels:
        hook = "今天先看" + "，再看".join(labels)
    else:
        hook = "今天先看最值得关注的 AI 变化"
    roles = {story_id: ("lead" if local_headline and story_id == local_headline else "brief") for story_id in ids}
    budget = {story_id: (70 if local_headline and story_id == local_headline else 25) for story_id in ids}
    budget.update({"intro": 12, "outro": 5})
    budget["total"] = sum(float(value) for value in budget.values())
    subjects: list[str] = []
    for card in cards:
        subject = _safe_subject(card)
        if subject and subject not in subjects:
            subjects.append(subject)
    if len(subjects) >= 2:
        comment_question = f"你会优先关注 {subjects[0]}，还是 {subjects[1]}？"
    elif subjects:
        comment_question = f"你更关心 {subjects[0]} 的实际效果，还是使用门槛？"
    else:
        comment_question = "你更关心实际效果，还是使用门槛？"
    return {
        "schema_version": 2,
        "source": "deterministic",
        "theme": "今日 AI 产品与技术变化",
        "audience_promise": "用高信息密度短句讲清事实、影响和不确定性",
        "hook": hook,
        "lead_story_id": lead,
        "secondary_story_id": secondary,
        "story_roles": roles,
        "duration_budget": budget,
        "cut_reasons": [],
        "tomorrow_followup": "继续追踪模型能力、价格、开放范围和真实使用反馈",
        "comment_question": comment_question,
    }

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..core import (
    EvidenceCard, card_is_community_signal, card_is_official_personnel_signal,
    card_is_official_social_signal, clean_text, extract_model_names,
    has_arxiv_evidence, is_ai_relevant, is_mainstream_ai, is_paper_entity,
)
from .claims import StorySpec, build_story_spec, is_renderable_cn

if TYPE_CHECKING:
    from .plan import EditorialPlan

STORY_CATEGORIES = ("模型发布", "产品与工具", "开源与生态", "研究与评测", "产业与政策")
SOURCE_STATUSES = ("official", "multi_media", "single_media", "official_personnel", "community")
CORE_EVENT_KEYWORDS = (
    "发布", "上线", "推出", "更新", "升级", "开源", "开放", "权重",
    "release", "changelog", "api", "sdk", "模型", "model", "benchmark",
    "榜单", "评测", "性能", "agent", "智能体", "产品", "价格", "上下文", "多模态",
)
SPECIFICITY_KEYWORDS = (
    "benchmark", "leaderboard", "eval", "aime", "gpqa", "livecodebench", "swe-bench",
    "价格", "定价", "api", "上下文", "参数", "显存", "下载", "许可", "license",
    "版本", "修复", "新增", "性能", "榜单", "评测", "权重", "延迟", "吞吐",
)
_NUMBER_OR_VERSION_RE = re.compile(
    r"\bv?\d+(?:\.\d+){1,4}[A-Za-z0-9_.-]*\b|\d+(?:\.\d+)?\s*(?:%|k|m|b|万|亿|分|项|个|次|ms|秒|gb|tokens?)",
    flags=re.I,
)
_CJK_LATIN_RE = re.compile(r"([\u4e00-\u9fff])([A-Za-z0-9])|([A-Za-z0-9])([\u4e00-\u9fff])")


def _missing_required_screenshot(card: EvidenceCard) -> bool:
    for ev in card.evidence_links:
        if str(ev.get("screenshot_required") or "").strip().lower() in {"1", "true", "yes", "y"}:
            if str(ev.get("screenshot_status") or "") != "captured" or not ev.get("screenshot_path"):
                return True
    return False


def _card_blob(card: EvidenceCard) -> str:
    return clean_text(" ".join([
        card.entity, card.event_title, card.reason, *card.key_facts,
        *(str(ev.get("source") or "") for ev in card.evidence_links),
        *(str(ev.get("title") or "") for ev in card.evidence_links),
        *(str(ev.get("url") or ev.get("final_url") or "") for ev in card.evidence_links),
    ]))


def story_category(card: EvidenceCard) -> str:
    headline = clean_text(card.event_title).lower()
    spaced = _CJK_LATIN_RE.sub(lambda m: f"{m.group(1)} {m.group(2)}" if m.group(1) else f"{m.group(3)} {m.group(4)}", headline)
    urls = " ".join(str(ev.get("url") or ev.get("final_url") or "").lower() for ev in card.evidence_links)
    sources = " ".join(str(ev.get("source") or "").lower() for ev in card.evidence_links)
    blob = _card_blob(card).lower()

    if has_arxiv_evidence(card) or is_paper_entity(card.entity):
        return "研究与评测"
    if any(t in headline for t in ("benchmark", "评测", "榜单", "跑分", "aime", "gpqa", "swe-bench", "livecodebench", "论文")):
        if not any(t in headline for t in ("正式发布", "发布", "推出", "上线", "开源")):
            return "研究与评测"
    if (
        "huggingface.co/" in urls or "modelscope.cn/" in urls or "hugging face" in sources
        or "/releases/tag/" in urls or "github release" in sources
        or any(t in headline for t in ("开源", "open source", "open weights", "apache", "mit 许可", "模型仓库", "ollama", "vllm", "llama.cpp", "sglang"))
    ):
        return "开源与生态"
    if any(t in headline for t in (
        "离职", "辞职", "卸任", "任命", "接任", "重组", "并入", "调任",
        "融资", "收购", "投资", "估值", "起诉", "诉讼", "法院", "监管", "法案", "政策", "合规", "合作",
        "resign", "depart", "step down", "appoint", "lawsuit", "regulation",
    )):
        return "产业与政策"

    has_model = bool(extract_model_names(spaced)) or any(t in headline for t in ("大模型", "基座模型", "多模态模型", "推理模型", "语音模型", "视频模型", "模型"))
    is_surface = any(t in headline for t in ("copilot", "claude code", "codex", "chatgpt work", "客户端", "插件", "订阅", "会员", "sdk"))
    if has_model and not is_surface and any(t in blob for t in ("发布", "推出", "上线", "更新", "升级", "预览", "preview", "release", "launch")):
        return "模型发布"
    return "产品与工具"


def public_source_status(card: EvidenceCard) -> str:
    if card_is_official_personnel_signal(card):
        return "official_personnel"
    if card_is_official_social_signal(card) or card.official_count > 0:
        return "official"
    if card_is_community_signal(card):
        return "community"
    if card.media_count >= 2:
        return "multi_media"
    if card.media_count == 1:
        return "single_media"
    if card.community_count > 0:
        return "community"
    tiers = [str(ev.get("tier") or "").upper() for ev in card.evidence_links]
    rels = [str(ev.get("reliability") or "").lower() for ev in card.evidence_links]
    if "A" in tiers or "official" in rels:
        return "official"
    media_hits = sum(1 for t, r in zip(tiers, rels) if t == "B" or "media" in r)
    return "multi_media" if media_hits >= 2 else ("single_media" if media_hits == 1 else "community")


def mainstream_priority_rank(card: EvidenceCard) -> int:
    blob = _card_blob(card)
    if not is_mainstream_ai(blob, card.entity):
        return 0
    rank = 2
    if card.official_count > 0 or card_is_official_social_signal(card):
        rank += 2
    elif card.media_count >= 2 or card_is_official_personnel_signal(card):
        rank += 1
    if any(kw in blob.lower() for kw in CORE_EVENT_KEYWORDS):
        rank += 1
    if card_is_community_signal(card):
        rank -= 1
    return max(1, min(5, rank))


def content_quality_profile(
    card: EvidenceCard,
    spec: StorySpec | None = None,
    plan: EditorialPlan | None = None,
) -> dict[str, Any]:
    spec = spec or build_story_spec(card)
    blob = _card_blob(card)
    status = public_source_status(card)
    category = plan.category if plan is not None else story_category(card)
    public_claims = [c for c in spec.claims if c.renderable and c.verifiable]
    missing_shot = _missing_required_screenshot(card)

    ev_score = 12 + {"official": 34, "multi_media": 24, "official_personnel": 18, "single_media": 14, "community": 8}[status]
    ev_score += min(12, max(0, card.source_count - 1) * 5) + min(16, len(public_claims) * 6)
    ev_score += 8 if card.risk == "green" else (-18 if card.risk == "red" else 0)
    if missing_shot:
        ev_score -= 25

    relevant, rel_pts = is_ai_relevant(card.event_title, " ".join(card.key_facts))
    rel_score = (15 + min(30, rel_pts * 5) if relevant else 8) + (25 if is_mainstream_ai(blob, card.entity) else 0)
    if any(kw in blob.lower() for kw in CORE_EVENT_KEYWORDS):
        rel_score += 14

    spec_score = 10 + (18 if _NUMBER_OR_VERSION_RE.search(blob) else 0)
    if any(kw in blob.lower() for kw in SPECIFICITY_KEYWORDS):
        spec_score += 16
    if public_claims:
        spec_score += min(32, round(max(c.score for c in public_claims) * 0.35) + len(public_claims) * 4)
    if plan is not None and len(plan.facts) >= 2:
        spec_score += 10

    story_score = 14 + (24 if is_renderable_cn(plan.title if plan is not None else spec.semantic_headline) else -12)
    if public_claims:
        story_score += 22
    if plan is not None and plan.facts:
        story_score += min(20, len(plan.facts) * 6)

    ev_c, rel_c = max(0, min(100, ev_score)), max(0, min(100, rel_score))
    sp_c, st_c = max(0, min(100, spec_score)), max(0, min(100, story_score))
    total = round(ev_c * 0.34 + rel_c * 0.26 + sp_c * 0.24 + st_c * 0.16)
    notes = [
        status,
        *(["specific"] if _NUMBER_OR_VERSION_RE.search(blob) else []),
        *(["performance"] if any(c.kind == "metric" for c in public_claims) else []),
        *(["missing_required_screenshot"] if missing_shot else []),
        *(["needs_confirmation"] if card.risk == "yellow" else []),
    ]
    return {
        "score": int(max(0, min(100, total))), "evidence": int(ev_c), "relevance": int(rel_c),
        "specificity": int(sp_c), "story": int(st_c), "public_claims": len(public_claims),
        "source_status": status, "category": category, "notes": notes,
    }


def selection_quality_score(
    card: EvidenceCard,
    spec: StorySpec | None = None,
    plan: EditorialPlan | None = None,
) -> float:
    spec = spec or build_story_spec(card)
    profile = content_quality_profile(card, spec=spec, plan=plan)
    confidence = max(0, min(100, int(card.confidence or 0)))
    bonus = {"official": 18.0, "multi_media": 12.0, "official_personnel": 9.0, "single_media": 6.0, "community": 3.0}.get(str(profile["source_status"]), 0.0)
    if card.source_count >= 2:
        bonus += 4.0
    prio = mainstream_priority_rank(card)
    if prio > 0:
        bonus += 6.0 + prio * 2.0
    if card.selected:
        bonus += 3.0
    bonus += -4.0 if card.risk == "yellow" else (-26.0 if card.risk == "red" else 0.0)
    if _missing_required_screenshot(card):
        bonus -= 32.0
    return round(max(0.0, min(100.0, float(profile["score"]) * 0.50 + confidence * 0.32 + bonus)), 2)

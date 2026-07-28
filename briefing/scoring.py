from __future__ import annotations

import re
from typing import Any

from .editor import (
    community_observation_summary,
    content_quality_profile,
    official_performance_caution,
    official_performance_summary,
)
from .models import EvidenceCard
from .presentation import has_arxiv_evidence
from .social_signals import card_is_community_signal, card_is_official_personnel_signal, card_is_official_social_signal
from .util import clean_text

SELECTION_CATEGORY_LABELS = {
    "official_release": "官方发布",
    "official_update": "官方动态",
    "official_personnel_signal": "X 官方人员动态",
    "model_repository": "模型仓库",
    "research": "研究论文",
    "media_report": "媒体报道",
    "community_signal": "传闻/风向",
    "other": "其他",
}

MAINSTREAM_AI_PRIORITY_KEYWORDS = [
    "openai",
    "chatgpt",
    "gpt",
    "codex",
    "sora",
    "anthropic",
    "claude",
    "google",
    "gemini",
    "deepmind",
    "gemma",
    "qwen",
    "通义",
    "千问",
    "阿里",
    "modelscope",
    "deepseek",
    "kimi",
    "moonshot",
    "豆包",
    "doubao",
    "bytedance",
    "字节",
    "腾讯",
    "混元",
    "hunyuan",
    "hy3",
    "智谱",
    "zhipu",
    "glm",
    "minimax",
    "mistral",
    "meta",
    "llama",
    "xai",
    "grok",
    "hugging face",
    "huggingface",
    "github copilot",
    "copilot",
    "nvidia",
    "英伟达",
    "ollama",
    "vllm",
]

MAINSTREAM_AI_CORE_EVENT_KEYWORDS = [
    "发布",
    "上线",
    "推出",
    "更新",
    "release",
    "changelog",
    "api",
    "模型",
    "model",
    "benchmark",
    "榜单",
    "评测",
    "性能",
    "开源",
    "权重",
    "agent",
    "智能体",
    "产品",
    "价格",
    "上下文",
    "多模态",
    "语音",
    "视频",
]


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _card_text(card: EvidenceCard) -> str:
    return clean_text(
        " ".join(
            [
                card.entity,
                card.event_title,
                card.reason,
                *card.key_facts,
                *card.uncertainty,
                *[str(e.get("source") or "") for e in card.evidence_links],
                *[str(e.get("title") or "") for e in card.evidence_links],
                *[str(e.get("url") or "") for e in card.evidence_links],
            ]
        )
    ).lower()


def is_mainstream_ai_card(card: EvidenceCard) -> bool:
    text = _card_text(card)
    return any(keyword.lower() in text for keyword in MAINSTREAM_AI_PRIORITY_KEYWORDS)


def mainstream_priority_rank(card: EvidenceCard) -> int:
    """Internal morning-selection priority. Larger means earlier in the main video candidate queue."""
    if not is_mainstream_ai_card(card):
        return 0
    text = _card_text(card)
    rank = 2
    if card.official_count > 0:
        rank += 2
    elif card.media_count >= 2:
        rank += 1
    if any(keyword.lower() in text for keyword in MAINSTREAM_AI_CORE_EVENT_KEYWORDS):
        rank += 1
    if card_is_official_social_signal(card):
        rank += 2
    elif card_is_official_personnel_signal(card):
        rank += 1
    elif card_is_community_signal(card):
        rank -= 1
    return max(1, min(5, rank))


def evidence_needs_screenshot(card: EvidenceCard) -> bool:
    return any(_truthy(evidence.get("screenshot_required")) for evidence in card.evidence_links)


def evidence_has_required_screenshot(card: EvidenceCard) -> bool:
    required = False
    for evidence in card.evidence_links:
        if not _truthy(evidence.get("screenshot_required")):
            continue
        required = True
        if str(evidence.get("screenshot_status") or "") != "captured" or not evidence.get("screenshot_path"):
            return False
    return required


def evidence_missing_required_screenshot(card: EvidenceCard) -> bool:
    return evidence_needs_screenshot(card) and not evidence_has_required_screenshot(card)


def card_has_arxiv_evidence(card: EvidenceCard) -> bool:
    """Backward-compatible public name for the shared evidence predicate."""
    return has_arxiv_evidence(card)


def story_category(card: EvidenceCard) -> str:
    text = clean_text(
        " ".join(
            [
                card.entity,
                card.event_title,
                card.reason,
                *card.key_facts,
                *[str(e.get("source") or "") for e in card.evidence_links],
                *[str(e.get("url") or "") for e in card.evidence_links],
            ]
        )
    ).lower()
    if card_is_official_personnel_signal(card):
        return "official_personnel_signal"
    if card_is_community_signal(card):
        return "community_signal"
    if "huggingface.co" in text or "hugging face" in text or "模型仓库" in text or "模型页" in text:
        return "model_repository"
    if card_has_arxiv_evidence(card):
        return "research"
    if card.official_count > 0 and any(k in text for k in ["release", "发布", "版本", "changelog"]):
        return "official_release"
    if card.official_count > 0:
        return "official_update"
    if card.media_count > 0:
        return "media_report"
    return "other"


def story_category_label(card: EvidenceCard) -> str:
    return SELECTION_CATEGORY_LABELS.get(story_category(card), "其他")


def public_source_status(card: EvidenceCard) -> str:
    if evidence_missing_required_screenshot(card):
        return "来源页待补"
    if card_is_official_social_signal(card):
        return "X 官方账号"
    if card_is_official_personnel_signal(card):
        return "X 官方人员动态"
    if card.official_count > 0 and card.risk == "green":
        return "官方来源"
    if card.official_count > 0:
        return "官方信息"
    if card.media_count >= 2:
        return "多方报道"
    if card.media_count == 1:
        return "媒体报道"
    if card.community_count > 0:
        return "传闻待证实"
    return "来源待确认"


def selection_quality_score(card: EvidenceCard) -> int:
    profile = content_quality_profile(card)
    quality = int(profile.get("score") or 0)
    confidence = max(0, min(100, int(card.confidence or 0)))
    source_bonus = 0
    if card.official_count > 0:
        source_bonus += 18
    if card.media_count >= 2:
        source_bonus += 12
    elif card.media_count == 1:
        source_bonus += 6
    if card.community_count > 0:
        source_bonus += 3
    if card_is_official_personnel_signal(card):
        source_bonus += 10
    if card_is_official_social_signal(card):
        source_bonus += 16
    if card.source_count >= 2:
        source_bonus += 5
    if official_performance_summary(card):
        source_bonus += 5
    if is_mainstream_ai_card(card):
        source_bonus += 8 + mainstream_priority_rank(card) * 2
    if card.selected:
        source_bonus += 4
    if card.risk == "yellow":
        source_bonus -= 4
    elif card.risk == "red":
        source_bonus -= 24
    if evidence_missing_required_screenshot(card):
        source_bonus -= 32
    score = round(quality * 0.48 + confidence * 0.34 + source_bonus)
    return max(0, min(100, score))


def _first_fact(card: EvidenceCard) -> str:
    for fact in card.key_facts:
        text = clean_text(fact)
        if not text:
            continue
        text = re.sub(r"^首要来源[：:]\s*", "", text)
        text = re.sub(r"^来源摘要[：:]\s*", "", text)
        text = re.sub(r"^.+?相关事件[：:]\s*", "", text)
        text = text.strip("。；; ")
        if text:
            return text
    return clean_text(card.event_title)


def _source_names(card: EvidenceCard) -> list[str]:
    names: list[str] = []
    for evidence in card.evidence_links:
        name = clean_text(str(evidence.get("source") or ""))
        if name and name not in names:
            names.append(name)
    return names


def _source_note(card: EvidenceCard) -> str:
    names = _source_names(card)
    source = "、".join(names[:2]) if names else "来源"
    if len(names) > 2:
        source += f" 等 {len(names)} 个来源"
    status = public_source_status(card)
    return f"{source}；{status}。"


def _impact_line(card: EvidenceCard) -> str:
    text = clean_text(" ".join([card.entity, card.event_title, *card.key_facts])).lower()
    if card_is_official_personnel_signal(card):
        return "这类信息适合当一线动态看：能提示产品方向，但不等同于正式发布时间或开放入口。"
    if card.community_count > 0 and card.official_count == 0:
        return "这类信息适合当作用户反馈或性能线索观察，不能直接当成官方结论。"
    if any(k in text for k in ["价格", "pricing", "api", "sdk", "codex", "github", "vllm"]):
        return "影响主要落在开发者调用、工具链适配和后续升级判断上。"
    if any(k in text for k in ["benchmark", "评测", "榜单", "aime", "gpqa", "swe-bench", "livecodebench"]):
        return "影响主要在模型能力对比和选型参考上，但要看测试口径是否一致。"
    if any(k in text for k in ["app", "chatgpt", "claude", "gemini", "客户端", "搜索"]):
        return "影响更偏向普通用户体验、功能入口和可用范围。"
    if any(k in text for k in ["模型", "model", "权重", "开源", "hugging face"]):
        return "影响主要在模型可获得性、部署方式和生态跟进速度上。"
    return "它可能改变相关产品、模型或行业节奏，适合放进早报快速判断。"


def _detail_line(card: EvidenceCard) -> str:
    performance = official_performance_summary(card)
    if performance:
        caution = official_performance_caution(card)
        return clean_text(f"{performance}{'；' + caution if caution else ''}")
    facts = [clean_text(fact) for fact in card.key_facts if clean_text(fact)]
    for fact in facts[1:]:
        text = re.sub(r"^来源摘要[：:]\s*", "", fact).strip("。；; ")
        if len(text) >= 12:
            return text
    return _first_fact(card)


def _background_line(card: EvidenceCard) -> str:
    category = story_category(card)
    if category == "official_personnel_signal":
        return "官方人员在 X 上的发言常常早于官网发布；早报只截原帖、说清含义，不延伸成确定发布时间。"
    if category == "model_repository":
        return "模型仓库更新只说明页面或文件出现变化，权重、许可、调用方式仍要看原页面。"
    if category == "research":
        return "论文和 benchmark 更适合作为研究线索，落地影响需要后续复现或产品化验证。"
    if category == "community_signal":
        return "社区讨论能提示真实使用感受，但样本环境、测试条件和发布时间都要保守看待。"
    if category == "media_report" and card.official_count == 0:
        return "媒体报道适合提示方向，涉及公司内部决策或未公开产品时仍需等官方确认。"
    if category == "official_release":
        return "官方发布记录能确认事件存在；真正影响还要看功能入口、版本范围、价格和使用限制。"
    return ""


def _community_discussion_line(card: EvidenceCard) -> str:
    text = clean_text(" ".join([card.event_title, *card.key_facts])).lower()
    if card.community_count <= 0:
        return ""
    if card_is_official_personnel_signal(card):
        return "重点看发言人身份、原帖上下文和是否指向具体产品；没有官网时，短讯处理即可。"
    if any(k in text for k in ["benchmark", "评测", "跑分", "throughput", "latency", "fp8", "bf16", "nvfp4"]):
        return "社区讨论集中在实测性能、吞吐/延迟和测试环境；这类反馈适合提示趋势，但不能替代官方 benchmark。"
    if any(k in text for k in ["open weights", "开源", "权重", "released", "模型"]):
        return "社区关注点主要是权重是否真的开放、许可是否清楚、能否在常见硬件上跑起来。"
    if any(k in text for k in ["degraded", "bug", "issue", "用户反馈", "体验", "works pretty well"]):
        return "社区反馈更像早期体验样本，需要看后续是否有更多用户复现或官方回应。"
    return "目前只有社区源头，适合当作线索观察；发布前最好保留原帖截图和关键上下文。"


def horizon_enrichment(card: EvidenceCard) -> dict[str, Any]:
    community = community_observation_summary(card, card.event_title)
    whats_new = str(community["lead"]) if community else _first_fact(card)
    why = str(community["impact"]) if community else _impact_line(card)
    details = str(community["discussion"]) if community else _detail_line(card)
    background = _background_line(card)
    community_discussion = _community_discussion_line(card)
    caution = "；".join(clean_text(x).strip("。；; ") for x in card.uncertainty if clean_text(x))
    if not caution and public_source_status(card) in {"待确认", "媒体线索", "需补源头截图"}:
        caution = "相关信息仍需后续补证或等待官方确认"
    return {
        "score": selection_quality_score(card),
        "status": public_source_status(card),
        "category": story_category(card),
        "category_label": story_category_label(card),
        "whats_new": whats_new,
        "why_it_matters": why,
        "key_details": details,
        "background": background,
        "community_discussion": community_discussion,
        "caution": caution,
        "source_note": _source_note(card),
    }

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any

from .editorial_plan import EditorialPlan, build_editorial_plan
from .models import EvidenceCard
from .story_model import StorySpec, build_story_spec


@dataclass(slots=True)
class VisualBeat:
    intent: str
    title: str
    body: str
    evidence_url: str = ""
    emphasis: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Storyboard:
    story_id: str
    template: str
    beats: list[VisualBeat]
    source_status: str
    needs_evidence_frame: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _template(spec: StorySpec) -> str:
    public_claims = [claim for claim in spec.claims if claim.renderable and claim.verifiable]
    metric_claims = [claim for claim in public_claims if claim.claim_type == "metric"]
    if spec.kind == "case_study":
        return "case_timeline"
    if spec.kind == "benchmark" or any(len(re.findall(r"\d+(?:\.\d+)?", claim.text)) >= 2 for claim in metric_claims):
        return "metric_comparison"
    if spec.kind == "research":
        return "method_result"
    if spec.kind in {"model_release", "model_update", "product_update", "official_update"}:
        return "change_impact"
    if spec.kind == "developer_release":
        return "changelog"
    if spec.kind == "industry":
        return "event_context"
    return "fact_stack"


# Presentation-layer attribution trimming for on-screen cards only.  A card
# repeats the story subject's own announcement, so "OpenAI 表示，" ahead of
# every card line is noise; the narration and the 原文与时间 card keep the
# sourcing.  Hedged relays ("据 36氪 报道"、"消息人士"、"回应称") are risk
# labels, not noise — the pattern deliberately cannot match them.
_OFFICIAL_ATTRIBUTION_RE = re.compile(
    r"^((?:[A-Za-z][A-Za-z0-9 .\-/&]{1,20}|[一-鿿·]{2,10})\s*"
    r"(?:官方账号表示|开发者账号表示|官方表示|官方称|表示))[，,]\s*"
)

_NUMBER_KERNEL_RE = re.compile(
    r"(?:约|超过|近|逾)?\d+(?:\.\d+)?(?:\s*[-–~至]\s*\d+(?:\.\d+)?)?\s*"
    r"(?:万亿|亿|万|%|倍|条|个|款|种|项|次|支|美元|元|天|小时|分钟|周|年|GB|TB|MB|[BMK]\b|tokens?|token)"
    r"[^，。;；：:]{0,8}",
    flags=re.I,
)

_DEADLINE_KERNEL_RE = re.compile(
    r"\d{1,2}\s*月\s*\d{1,2}\s*日[^，。;；]{0,6}?(?:截止|停用|上线|发布|生效|开源)"
)

_GENERIC_BEAT_TITLES = {"核心信息", "方法与结果", "具体变化", "关键指标"}


_HEDGE_ATTRIBUTORS = ("消息人士", "知情人士", "内部人士", "接近", "匿名", "网友", "爆料", "传闻")


def strip_card_attribution(text: str) -> str:
    """Drop a leading first-party attribution from card copy when safe."""
    value = str(text or "")
    match = _OFFICIAL_ATTRIBUTION_RE.match(value)
    if not match:
        return value
    prefix = match.group(1)
    # An anonymous or hedged attributor is an epistemic label, never noise.
    if any(marker in prefix for marker in _HEDGE_ATTRIBUTORS):
        return value
    remainder = value[match.end():].strip()
    return remainder if len(remainder) >= 10 else value


def _specific_beat_title(fallback: str, body: str) -> str:
    """Replace a generic label with a fact kernel when one is extractable.

    Precision over cleverness: only high-confidence kernels (a number with a
    unit, or a dated deadline) may take over the title; anything fuzzier keeps
    the generic label rather than risking a mangled headline.
    """
    if fallback not in _GENERIC_BEAT_TITLES:
        return fallback
    deadline = _DEADLINE_KERNEL_RE.search(body)
    if deadline:
        kernel = re.sub(r"\s+", " ", deadline.group(0)).strip()
        if 4 <= len(kernel) <= 16:
            return kernel
    number = _NUMBER_KERNEL_RE.search(body)
    if number:
        kernel = re.sub(r"\s+", " ", number.group(0)).strip(" ，,。")
        if 4 <= len(kernel) <= 16:
            return kernel
    return fallback


def _beat_intent(claim_type: str) -> str:
    return {
        "metric": "metric",
        "pricing": "price",
        "availability": "availability",
        "change": "change",
        "research": "method",
    }.get(claim_type, "fact")


def _beat_title(intent: str, text: str) -> str:
    lowered = text.lower()
    if any(term in lowered for term in ["诉讼", "起诉", "法院", "案件编号", "指控"]):
        return "诉讼进展"
    # Keep the claim's semantic role ahead of incidental words in the same
    # sentence. A benchmark sentence can mention cost without becoming a
    # pricing card, and an availability claim can mention an API endpoint.
    if intent == "metric" or any(term in lowered for term in ["benchmark", "评测", "得分", "延迟", "吞吐", "性能", "提速"]):
        return "评测与性能"
    if intent == "price" or any(term in lowered for term in ["价格", "定价", "美元", "免费额度", "成本"]):
        return "价格与额度"
    if intent == "availability" or any(term in lowered for term in ["开放", "可用", "面向", "入口", "地区"]) or re.search(
        r"\b(?:plus|pro|enterprise)\b", lowered
    ):
        return "开放范围"
    if any(term in lowered for term in ["安全", "隐私", "数据保留", "限制", "许可"]):
        return "安全与限制"
    if any(term in lowered for term in ["agent", "智能体", "工具调用", "跨应用"]):
        return "Agent 能力"
    if any(term in lowered for term in ["api", "sdk", "接口", "开发者"]):
        return "API 与接口"
    if any(term in lowered for term in ["上下文", "token", "参数", "多模态"]):
        return "模型能力"
    return {
        "metric": "关键指标",
        "price": "价格与额度",
        "availability": "开放范围",
        "change": "具体变化",
        "method": "方法与结果",
        "fact": "核心信息",
    }[intent]


def build_storyboard(card: EvidenceCard, plan: EditorialPlan | None = None, spec: StorySpec | None = None) -> Storyboard:
    spec = spec or build_story_spec(card)
    plan = plan or build_editorial_plan(card)
    beats: list[VisualBeat] = []
    claim_by_id = {claim.claim_id: claim for claim in spec.claims}
    claim_by_text = {claim.text: claim for claim in spec.claims}
    for index, fact in enumerate(plan.facts[:6]):
        claim_id = plan.claim_ids[index] if index < len(plan.claim_ids) else ""
        claim = claim_by_id.get(claim_id) or claim_by_text.get(fact)
        if claim is None:
            claim = next((candidate for candidate in spec.claims if candidate.text in fact or fact in candidate.text), None)
        evidence_url = claim.evidence_urls[0] if claim and claim.evidence_urls else ""
        intent = _beat_intent(claim.claim_type if claim else "statement")
        card_body = strip_card_attribution(fact)
        beats.append(
            VisualBeat(
                intent=intent,
                title=_specific_beat_title(_beat_title(intent, fact), card_body),
                body=card_body,
                evidence_url=evidence_url,
                emphasis=[],
            )
        )
    if plan.impact:
        beats.append(VisualBeat(intent="impact", title="影响对象", body=plan.impact))
    if plan.caution:
        caution_title = "评测说明" if spec.kind == "benchmark" else "来源说明"
        beats.append(VisualBeat(intent="caution", title=caution_title, body=plan.caution))
    needs_evidence = spec.source_status in {"single_media", "media_cross_checked", "community"}
    return Storyboard(
        story_id=spec.story_id,
        template="brief_strip" if card.editorial_tier == "brief" else _template(spec),
        beats=beats,
        source_status=spec.source_status,
        needs_evidence_frame=needs_evidence,
    )

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from ..core import (
    EvidenceCard, card_display_entity, clause_truncate, clean_text,
    extract_model_names, has_cjk, looks_raw_english, normalize_cn_punctuation,
)
from .claims import SIGNAL_DISCLAIMERS, StorySpec, build_story_spec, contains_sensational_wording, is_renderable_cn
from .quality import story_category

FORBIDDEN_META_WORDS = (
    "RSSHub", "RSS", "Atom", "爬虫", "官网抓取", "编辑判断", "我的判断",
    "聚合源", "订阅源", "核验流程", "核验摘要", "来源可查", "具体以官方为准",
    "值得期待", "持续关注", "如有更新第一时间通知",
)
MEDIA_TAIL_RE = re.compile(r"\s*(?:[|｜/]\s*|[-—]\s*)?(?:36\s*氪|InfoQ|量子位|机器之心|极客公园|新智元|钛媒体)(?:独家|首发)?(?:\.cn)?\s*$", flags=re.I)
BARE_DOMAIN_RE = re.compile(r"(?<![A-Za-z0-9._@-])[A-Za-z0-9-]+\.(?:com|net|org|io|ai|co|cn|dev|app)\b", flags=re.I)
ATTRIBUTION_PREFIX_RE = re.compile(r"^([^，。；;：:]{2,24}?(?:官方账号表示|开发者账号表示|回应称|表示|获悉|报道|介绍|宣布|称))[，,]")
OFFICIAL_CARD_ATTR_RE = re.compile(r"^((?:[A-Za-z][A-Za-z0-9 .\-/&]{1,20}|[\u4e00-\u9fff·]{2,10})\s*(?:官方账号表示|开发者账号表示|官方表示|官方称|表示))[，,]\s*")
HEDGE_MARKERS = ("消息人士", "知情人士", "内部人士", "接近", "匿名", "网友", "爆料", "传闻")
BALANCED_PAIRS = (("（", "）"), ("(", ")"), ("“", "”"), ("《", "》"), ("「", "」"))
CJK_LATIN_RE = re.compile(r"([\u4e00-\u9fff])([A-Za-z0-9])|([A-Za-z0-9])([\u4e00-\u9fff])")
KIND_CN_FALLBACK = {
    "model_release": "发布模型更新", "model_update": "更新模型能力", "developer_release": "发布开发工具更新",
    "product_update": "更新产品功能", "model_repository": "更新开源模型仓库", "research": "公布最新研究进展",
    "benchmark": "更新评测结果", "industry": "披露行业与组织动态", "community_signal": "出现社区实测线索",
}


def _sanitize_public_copy(text: str, *, max_chars: int = 0) -> str:
    cleaned = re.sub(r"\.{3,}|…+|https?://\S+", "", clean_text(text))
    for word in FORBIDDEN_META_WORDS:
        cleaned = re.sub(re.escape(word), "", cleaned, flags=re.I)
    cleaned = cleaned.replace("跑路", "离职")
    for pat in (r"王炸|杀疯|刷爆|震撼|狂喜|炸裂|刚刚|重磅|突发|刷屏", r"[?？]{2,}|[!！]{2,}"):
        cleaned = re.sub(pat, "", cleaned, flags=re.I)
    cleaned = re.sub(r"(?:并将|以及|包括|例如|和|与)\s*$", "", normalize_cn_punctuation(cleaned)).strip(" ：:；;，,。-—|｜")
    if max_chars > 0 and len(cleaned) > max_chars:
        cleaned = clause_truncate(cleaned, max_chars).rstrip("….").strip(" ：:；;，,")
    return cleaned


def _breathe_semicolons(text: str) -> str:
    if "；" not in text and ";" not in text:
        return text
    depth, closers, out = {op: 0 for op, _cl in BALANCED_PAIRS}, {cl: op for op, cl in BALANCED_PAIRS}, []
    for ch in text:
        if ch in depth:
            depth[ch] += 1
        elif ch in closers:
            depth[closers[ch]] = max(0, depth[closers[ch]] - 1)
        out.append("。" if ch in {"；", ";"} and not any(depth.values()) else ch)
    return "".join(out)


def _dedupe_attribution(sentences: list[str]) -> list[str]:
    result, last_prefix = [], ""
    for sent in sentences:
        match = ATTRIBUTION_PREFIX_RE.match(sent)
        if match and match.group(1) == last_prefix and len(rem := sent[match.end() :].lstrip()) >= 8:
            sent = rem
        else:
            last_prefix = match.group(1) if match else ""
        result.append(sent)
    return result


def _too_similar(left: str, right: str) -> bool:
    def sig(val: str) -> set[str]:
        low = clean_text(val).lower()
        cjk = "".join(re.findall(r"[\u4e00-\u9fff]", low))
        return set(re.findall(r"[a-z][a-z0-9_.-]{2,}|\d+(?:\.\d+)?%?", low)) | {cjk[i : i + 2] for i in range(max(0, len(cjk) - 1))}

    a, b = sig(left), sig(right)
    return bool(a and b) and len(a & b) / max(1, min(len(a), len(b))) >= 0.58


@dataclass(slots=True)
class EditorialPlan:
    story_id: str
    title: str
    lead: str
    facts: list[str] = field(default_factory=list)
    claim_ids: list[str] = field(default_factory=list)
    impact: str = ""
    caution: str = ""
    category: str = "产品与工具"
    generation_path: str = "structured_editorial_plan"
    editorial_tier: str = "headline"
    signal_kind: str = "confirmed"
    disclaimer: str = ""
    source_status: str = ""
    narration_override: str = ""

    def narration(self) -> str:
        if self.narration_override:
            return _sanitize_public_copy(self.narration_override)
        signal_note = SIGNAL_DISCLAIMERS.get(self.signal_kind, "") or self.disclaimer
        if self.editorial_tier == "brief":
            facts = [f for f in self.facts[:3] if not _too_similar(self.lead, f)] or self.facts[:1]
            raw_parts, budget = [self.lead, *facts[:2], *([signal_note] if signal_note else [])], 220
        else:
            raw_parts, budget = [self.lead, *self.facts[:3], self.impact, self.caution or signal_note], 340
        seen, sentences, used = set(), [], 0
        for part in raw_parts:
            cleaned = _sanitize_public_copy(part, max_chars=96).strip("。")
            norm = re.sub(r"\W+", "", cleaned.lower()) if cleaned and has_cjk(cleaned) else ""
            if not norm or norm in seen:
                continue
            is_disc = bool(signal_note and norm == re.sub(r"\W+", "", signal_note.lower()))
            if not is_disc and ((used + len(cleaned) > budget and sentences) or any((norm in old or old in norm) and min(len(norm), len(old)) >= 14 for old in seen)):
                continue
            seen.add(norm)
            sentences.append(cleaned + "。")
            used += len(cleaned)
        return re.sub(r"\.{3,}|…+", "", _breathe_semicolons("".join(_dedupe_attribution(sentences))))

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "narration": self.narration()}


@dataclass(slots=True)
class VisualBeat:
    title: str
    body: str
    kind: str = "fact"
    evidence_url: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"title": self.title, "body": self.body, "kind": self.kind, **({"evidence_url": self.evidence_url} if self.evidence_url else {})}


@dataclass(slots=True)
class Storyboard:
    template: str
    beats: list[VisualBeat] = field(default_factory=list)
    story_id: str = ""
    source_status: str = ""
    needs_evidence_frame: bool = False

    def to_cards(self) -> list[dict[str, str]]:
        return [beat.to_dict() for beat in self.beats]

    def to_dict(self) -> dict[str, Any]:
        return {"story_id": self.story_id, "template": self.template, "source_status": self.source_status, "needs_evidence_frame": self.needs_evidence_frame, "beats": self.to_cards()}


@dataclass(slots=True)
class EditionBrief:
    run_date: str
    hook: str
    tagline: str
    lead_story_id: str
    lead_title: str
    story_count: int
    comment_question: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _build_cn_title(card: EvidenceCard, spec: StorySpec) -> str:
    candidate = BARE_DOMAIN_RE.sub("", MEDIA_TAIL_RE.sub("", spec.semantic_headline or card.event_title))
    for pfx in ("Hugging Face 模型仓库更新：", "Hugging Face 模型仓库更新:", "GitHub Releases 发现", "GitHub Releases"):
        candidate = candidate.replace(pfx, "")
    candidate = _sanitize_public_copy(candidate, max_chars=64)
    if is_renderable_cn(candidate) and not contains_sensational_wording(candidate) and len(candidate) <= 64:
        return candidate
    for claim in spec.claims:
        if claim.renderable and claim.verifiable and not contains_sensational_wording(claim.text):
            if is_renderable_cn(short := _sanitize_public_copy(claim.text, max_chars=48)):
                return short
    subject = clean_text(spec.subject or card_display_entity(card, fallback="AI")).split("/", 1)[0].strip() or "AI"
    models = extract_model_names(CJK_LATIN_RE.sub(lambda m: f"{m.group(1)} {m.group(2)}" if m.group(1) else f"{m.group(3)} {m.group(4)}", spec.headline))
    spacer = " " if re.search(r"[A-Za-z0-9]$", subject) else ""
    return _sanitize_public_copy(f"{subject}{spacer}{spec.action} {models[0]}", max_chars=56) if models and spec.action != "动态" else f"{subject}{spacer}{KIND_CN_FALLBACK.get(spec.kind, '发布新动态')}"


def build_editorial_plan(card: EvidenceCard, spec: StorySpec | None = None) -> EditorialPlan:
    spec = spec or build_story_spec(card)
    title, tier = _build_cn_title(card, spec), str(card.editorial_tier or "headline")
    budget = 6 if tier == "headline" else 4
    eligible = sorted(
        (c for c in spec.claims if c.renderable and c.verifiable and c.score >= 24),
        key=lambda c: (c.kind in {"metric", "pricing", "availability", "change", "organization", "legal"}, c.score, -len(c.text)),
        reverse=True,
    )
    facts, claim_ids = [], []
    for claim in eligible:
        cleaned = _sanitize_public_copy(claim.text, max_chars=88)
        if not cleaned or not is_renderable_cn(cleaned):
            continue
        if (len(eligible) > 1 and spec.kind != "industry" and _too_similar(title, cleaned)) or any(_too_similar(cleaned, ex) for ex in facts):
            continue
        facts.append(cleaned)
        claim_ids.append(claim.claim_id)
        if len(facts) >= budget:
            break
    if not facts:
        for raw_fact in card.key_facts:
            if (cleaned := _sanitize_public_copy(raw_fact, max_chars=88)) and is_renderable_cn(cleaned) and not _too_similar(title, cleaned):
                facts.append(cleaned)
                break

    src = re.sub(r"\s*(?:中文|官方|RSS|Feed)$", "", clean_text(str((card.evidence_links[0] if card.evidence_links else {}).get("source") or "")), flags=re.I).strip()
    signal_note = SIGNAL_DISCLAIMERS.get(spec.signal_kind, "") or spec.disclaimer
    lead = (
        f"一线消息显示，{title}" if spec.signal_kind == "official_personnel"
        else f"社区消息称，{title}" if spec.signal_kind == "community_rumour"
        else title if spec.first_party
        else f"据 {src} 报道，{title}" if spec.source_status in {"single_media", "media_cross_checked"} and src and not looks_raw_english(src, min_letters=12)
        else f"公开资料显示，{title}"
    )
    impact = (
        "该结果可作为模型与部署选型参考，不同测试口径不宜直接横比" if spec.kind == "benchmark"
        else ("该进展主要提供算法与实验参考，工程落地仍取决于复现与推理成本" if spec.kind == "research" else "")
    )
    caution = signal_note or {
        "single_media": "目前仅单一媒体披露相关信息，具体细节尚未获当事方确认",
        "media_cross_checked": "多家媒体已有交叉报道，未公开细节仍需结合当事方后续说明理解",
        "community": "该内容来自社区观察，不等同于官方正式结论",
    }.get(spec.source_status, "性能数据需结合模型版本、硬件环境与评测集条件综合理解" if spec.kind == "benchmark" else "")
    return EditorialPlan(
        story_id=spec.story_id, title=title, lead=_sanitize_public_copy(lead, max_chars=86),
        facts=facts, claim_ids=claim_ids, impact=impact, caution=caution,
        category=story_category(card), generation_path="structured_editorial_plan",
        editorial_tier=tier, signal_kind=spec.signal_kind, disclaimer=signal_note, source_status=spec.source_status,
    )


def _beat_title_and_kind(claim_kind: str, body: str) -> tuple[str, str]:
    low = body.lower()
    b_kind = {"metric": "metric", "pricing": "price", "availability": "availability", "change": "change", "research": "method"}.get(claim_kind, "fact")
    if any(t in low for t in ("诉讼", "起诉", "法院", "监管", "合规")):
        return "合规与进展", b_kind
    if b_kind == "metric" or any(t in low for t in ("benchmark", "评测", "得分", "延迟", "吞吐", "性能")):
        return "评测与性能", "metric"
    if b_kind == "price" or any(t in low for t in ("价格", "定价", "美元", "免费", "订阅")):
        return "价格与额度", "price"
    if b_kind == "availability" or any(t in low for t in ("开放", "可用", "上线", "接入", "入口")):
        return "开放范围", "availability"
    if any(t in low for t in ("api", "sdk", "接口", "开发者")):
        return "开发接口", b_kind
    if any(t in low for t in ("agent", "智能体", "工具调用")):
        return "智能体能力", b_kind
    if any(t in low for t in ("上下文", "token", "参数", "多模态", "权重", "许可", "license")):
        return "模型规格", b_kind
    return {"metric": "关键指标", "price": "价格与额度", "availability": "开放范围", "change": "具体变化", "method": "研究方法", "fact": "核心事实"}[b_kind], b_kind


def build_storyboard(card: EvidenceCard, spec: StorySpec | None = None, plan: EditorialPlan | None = None) -> Storyboard:
    spec = spec or build_story_spec(card)
    plan = plan or build_editorial_plan(card, spec=spec)
    claim_by_id = {c.claim_id: c for c in spec.claims}
    beats: list[VisualBeat] = []
    for idx, fact in enumerate(plan.facts[:6]):
        claim = claim_by_id.get(plan.claim_ids[idx] if idx < len(plan.claim_ids) else "")
        m = OFFICIAL_CARD_ATTR_RE.match(fact)
        body = fact[m.end() :].strip() if (m and not any(h in m.group(1) for h in HEDGE_MARKERS) and len(fact[m.end() :].strip()) >= 10) else fact
        b_title, b_kind = _beat_title_and_kind(claim.kind if claim else "statement", body)
        beats.append(VisualBeat(title=b_title, body=body, kind=b_kind, evidence_url=claim.evidence_urls[0] if claim and claim.evidence_urls else ""))
    if plan.impact:
        beats.append(VisualBeat(title="影响范围", body=plan.impact, kind="impact"))
    if plan.caution:
        beats.append(VisualBeat(title="评测说明" if spec.kind == "benchmark" else "来源说明", body=plan.caution, kind="caution"))
    template = "brief_strip" if card.editorial_tier == "brief" else {"benchmark": "metric_comparison", "research": "method_result", "developer_release": "changelog", "industry": "event_context"}.get(
        spec.kind, "change_impact" if spec.kind in {"model_release", "model_update", "product_update", "official_update"} else "fact_stack"
    )
    return Storyboard(
        template=template, beats=beats, story_id=spec.story_id, source_status=spec.source_status,
        needs_evidence_frame=spec.source_status in {"single_media", "media_cross_checked", "community", "official_personnel"},
    )


def build_edition_brief(cards: list[EvidenceCard], *, run_date: str = "") -> EditionBrief:
    if not cards:
        return EditionBrief(run_date=run_date, hook="今天先看最值得关注的 AI 产品与技术变化", tagline="高信息密度梳理今日 AI 核心事实与边界", lead_story_id="", lead_title="AI 日报", story_count=0, comment_question="你今天最关注哪项 AI 技术或产品进展？")
    specs = [build_story_spec(c) for c in cards]
    plans = [build_editorial_plan(c, spec=s) for c, s in zip(cards, specs)]
    lead_idx = next((i for i, (c, s) in enumerate(zip(cards, specs)) if c.editorial_tier == "headline" and s.signal_kind == "confirmed"), next((i for i, s in enumerate(specs) if s.signal_kind == "confirmed"), 0))
    hook = "今天先看" + "，再看".join(f"{'一线消息：' if s.signal_kind == 'official_personnel' else ('传闻/风向：' if s.signal_kind == 'community_rumour' else '')}{p.title}" for p, s in zip(plans[:2], specs[:2]))
    subjects = list(dict.fromkeys(
        subj for c, s in zip(cards, specs)
        if (subj := clean_text(s.subject or card_display_entity(c, fallback="")).split("/", 1)[0].strip()) and subj != "AI" and len(subj) <= 24 and len(subj.split()) <= 3
    ))
    question = f"你会优先关注 {subjects[0]}，还是 {subjects[1]} 的新变化？" if len(subjects) >= 2 else (f"你更关心 {subjects[0]} 的实际效果，还是使用门槛？" if subjects else "你更关心模型实际效果，还是调用成本与开放范围？")
    cats = list(dict.fromkeys(p.category for p in plans))
    tagline = f"聚焦{'、'.join(cats[:3])}等 {len(cards)} 条经核验 AI 动态" if cats else f"今日共 {len(cards)} 条 AI 动态"
    return EditionBrief(run_date=run_date, hook=_sanitize_public_copy(hook, max_chars=96), tagline=_sanitize_public_copy(tagline, max_chars=64), lead_story_id=specs[lead_idx].story_id, lead_title=plans[lead_idx].title, story_count=len(cards), comment_question=question)

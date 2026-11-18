from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from ..core import (
    EvidenceCard, card_display_entity, card_is_community_signal, card_is_official_personnel_signal,
    clean_text, extract_hf_model_id, extract_model_names, has_arxiv_evidence, has_cjk,
    infer_brand, looks_raw_english, normalize_cn_punctuation, split_sentences, stable_hash,
)

SIGNAL_DISCLAIMERS: dict[str, str] = {
    "official_personnel": "一线消息，未获官方公告确认",
    "community_rumour": "传闻/风向，未获官方公告确认",
}
SOURCE_PREFIXES = ("首要来源发布时间", "首要来源", "来源摘要", "变更摘要", "修复/维护线索", "相关事件")
META_PHRASES = (
    "模型仓库更新", "模型页更新", "真正影响还要看", "后续复现", "需要人工确认",
    "性能榜单要看测试口径", "影响主要落在", "影响更偏向", "编辑判断", "我的判断",
    "官网抓取", "订阅源", "聚合源", "爬虫",
)
SOURCE_BRAND_TERMS = frozenset({
    "openai", "anthropic", "google", "deepmind", "nvidia", "microsoft", "meta",
    "xai", "alibaba", "mistral", "minimax", "moonshot", "huggingface", "modelscope",
    "github", "腾讯", "阿里", "百度", "字节", "智谱", "英伟达", "月之暗面",
})
PRODUCT_TERMS = (
    "chatgpt", "codex", "copilot", "claude", "gemini", "qwen", "deepseek",
    "kimi", "minimax", "mistral", "llama", "grok", "vllm", "ollama",
)
ORGANIZATION_ACTION_TERMS = frozenset({
    "离职", "辞职", "卸任", "离开", "任命", "接任", "就任", "重组", "并入", "调任",
    "resign", "depart", "step down", "appoint", "reorgan", "merge into",
})
ACTION_TERMS: tuple[tuple[str, str], ...] = (
    ("正式发布", "发布"), ("发布", "发布"), ("推出", "推出"), ("上线", "上线"), ("公布", "公布"),
    ("宣布", "宣布"), ("披露", "披露"), ("接入", "接入"), ("新增", "新增"), ("开放", "开放"),
    ("开源", "开源"), ("重写", "重写"), ("迁移", "迁移"), ("修复", "修复"), ("升级", "升级"),
    ("更新", "更新"), ("暂停", "暂停"), ("调整", "调整"), ("拆分", "调整"), ("回应", "回应"),
    ("离职", "离职"), ("辞职", "辞职"), ("卸任", "卸任"), ("任命", "任命"), ("接任", "接任"),
    ("重组", "重组"), ("并入", "并入"), ("调任", "调任"), ("available", "接入"),
    ("launch", "发布"), ("release", "发布"), ("adds", "新增"), ("introduces", "推出"),
)
SENSATIONAL_PATTERNS = tuple(re.compile(pat, flags=re.I) for pat in (
    r"王炸|杀疯|刷爆|震撼|自曝|颠覆|彻底取代|程序员失业", r"跑路|狂喜|炸裂|刚刚|重磅|突发|刷屏",
    r"消失.{0,12}(?:后|发帖)", r"只为(?:它|他|她|这)", r"最强\s*(?:agent|智能体|模型)?", r"[?？]{2,}|[!！]{3,}",
))
MEASURE_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:%|倍|万|亿|ms|秒|gb|mb|tb|token|tokens|美元|元|分|b(?![a-z0-9])|k(?![a-z0-9]))", flags=re.I)
VERSIONED_PRODUCT_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9]*(?:[-_.][A-Za-z0-9]+)+(?![A-Za-z0-9])")
NUMERIC_TOKEN_RE = re.compile(r"\d+(?:\.\d+)?")
CJK_LATIN_RE = re.compile(r"([\u4e00-\u9fff])([A-Za-z0-9])|([A-Za-z0-9])([\u4e00-\u9fff])")
RAW_EN_CLAUSE_RE = re.compile(r"\b[a-zA-Z]{2,}(?:\s+[a-zA-Z]{2,}){3,}\b")
EN_FUNCTION_WORDS = frozenset({"the", "and", "for", "with", "from", "into", "that", "this", "will", "are", "is", "was", "have", "has", "about"})
CLAUSE_SPLIT_RE = re.compile(
    r"\s+(?=(?:同时|此外|其中|按照|根据|针对|另有|据了解|目前|近日|日前|\d{4}年[,，]?|"
    r"[A-Z][A-Za-z.'-]+\s*(?:离职|辞职|卸任|将离职|已离职)))"
)
NOISE_MARKERS = ("utm_source=", "utm_medium=", "点击查看原文", "加载更多", "免责声明", "责任编辑", "版权所有", "粤icp", "appeared first on", "搜狐网", "新浪网", "一键直达")


@dataclass(slots=True)
class FactClaim:
    claim_id: str
    text: str
    kind: str
    evidence_urls: list[str] = field(default_factory=list)
    verifiable: bool = False
    renderable: bool = False
    numeric_tokens: list[str] = field(default_factory=list)
    score: int = 0
    source_tier: str = "unknown"
    evidence_reliability: list[str] = field(default_factory=list)
    first_party_supported: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class StorySpec:
    story_id: str
    cluster_key: str
    entity: str
    subject: str
    kind: str
    action: str
    source_status: str
    signal_kind: str = "confirmed"
    semantic_headline: str = ""
    claims: list[FactClaim] = field(default_factory=list)
    topic_key: str = ""
    family_key: str = ""
    disclaimer: str = ""
    headline: str = ""
    first_party: bool = False
    evidence_count: int = 0
    audience: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _space_cjk_latin(text: str) -> str:
    return CJK_LATIN_RE.sub(lambda m: f"{m.group(1)} {m.group(2)}" if m.group(1) else f"{m.group(3)} {m.group(4)}", text)


def _models_in_text(text: str) -> list[str]:
    return [m.lower() for m in extract_model_names(_space_cjk_latin(clean_text(text).replace("千问", "Qwen")))]


def is_renderable_cn(text: str) -> bool:
    cleaned = clean_text(text)
    if not cleaned or not has_cjk(cleaned) or looks_raw_english(cleaned, threshold=0.72, min_letters=24):
        return False
    return not any(len({w.lower() for w in m.group(0).split()} & EN_FUNCTION_WORDS) >= 2 for m in RAW_EN_CLAUSE_RE.finditer(cleaned))


def contains_sensational_wording(text: str) -> bool:
    cleaned = clean_text(text)
    return any(pat.search(cleaned) for pat in SENSATIONAL_PATTERNS)


def has_affirmed_action(text: str, action: str) -> bool:
    lowered = clean_text(text).lower()
    seps = ("，", ",", "。", "；", ";", "！", "!", "？", "?")
    for match in re.finditer(re.escape(action.lower()), lowered):
        clause_start = max(lowered.rfind(sep, 0, match.start()) for sep in seps)
        clause_end = min((pos for sep in seps if (pos := lowered.find(sep, match.end())) >= 0), default=len(lowered))
        prefix, suffix = lowered[clause_start + 1 : match.start()], lowered[match.end() : clause_end]
        if contrasts := list(re.finditer(r"(?:但|不过|然而|而是|but|however)\s*", prefix, flags=re.I)):
            prefix = prefix[contrasts[-1].end() :]
        if re.search(r"(?:不会|不再|不打算|并非|不是|并未|尚未|没有|从未|否认|will\s+not|not|never)", prefix, flags=re.I):
            continue
        if re.search(r"(?:传闻|消息|说法|rumou?r).{0,12}(?:不实|错误|被否认|false|denied)", suffix, flags=re.I):
            continue
        return True
    return False


def _atomic_claim_parts(raw: str) -> list[str]:
    shielded = re.sub(r"(?<=[A-Za-z0-9])\.(?=[A-Za-z0-9])", "\u0000", raw or "")
    parts: list[str] = []
    for sentence in split_sentences(shielded):
        for piece in CLAUSE_SPLIT_RE.split(sentence.replace("\u0000", ".")):
            text = re.sub(r"https?://\S+", "", clean_text(piece))
            for pfx in SOURCE_PREFIXES:
                text = re.sub(rf"^{re.escape(pfx)}\s*[：:]\s*", "", text, flags=re.I)
            cleaned = normalize_cn_punctuation(re.sub(r"^.{1,36}\s+相关事件\s*[：:]\s*", "", text)).strip(" ：:；;，,。")
            if 10 <= len(cleaned) <= 280:
                parts.append(cleaned)
    return parts


def _claim_kind(text: str) -> str:
    low = text.lower()
    if any(t in low for t in ("起诉", "诉讼", "指控", "法院", "监管", "法案", "合规", "细则", "条例", "禁令")):
        return "legal"
    if any(t in low for t in ORGANIZATION_ACTION_TERMS):
        return "organization"
    if MEASURE_RE.search(text) or any(t in low for t in ("benchmark", "评测", "得分", "延迟", "吞吐")):
        return "metric"
    if any(t in low for t in ("定价", "美元", "免费", "订阅", "会员")) or ("价格" in low and NUMERIC_TOKEN_RE.search(text)):
        return "pricing"
    if any(t in low for t in ("可用", "开放", "上线", "接入", "入口", "available", "rollout")):
        return "availability"
    if any(t in low for t in ("新增", "支持", "修复", "重写", "迁移", "调用", "能力", "开源", "feature")):
        return "change"
    if any(t in low for t in ("方法", "提出", "实验", "数据集", "论文", "研究")):
        return "research"
    return "statement"


def _claim_terms(text: str) -> set[str]:
    stop = {"相关事件", "来源摘要", "首要来源", "发布", "推出", "上线", "更新", "新增", "模型", "官方"}
    low = _space_cjk_latin(clean_text(text)).lower()
    terms = {tok for tok in re.findall(r"[a-z][a-z0-9_.-]{2,}|\d+(?:\.\d+)?", low) if tok not in stop}
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", low):
        if len(run) <= 4 and run not in stop:
            terms.add(run)
        terms.update(bg for i in range(len(run) - 1) if (bg := run[i : i + 2]) not in stop)
    return terms


def _supporting_evidence(text: str, numeric_tokens: list[str], evidence: list[dict[str, str]]) -> list[dict[str, str]]:
    claim_terms, numeric_set = _claim_terms(text), set(numeric_tokens)
    matches: list[tuple[int, dict[str, str]]] = []
    for row in evidence:
        if not str(row.get("url") or row.get("final_url") or "").strip():
            continue
        primary, p_prov = clean_text(str(row.get("excerpt") or "")), clean_text(str(row.get("excerpt_provenance") or ""))
        summary, s_prov = clean_text(str(row.get("source_summary") or "")), clean_text(str(row.get("source_summary_provenance") or ""))
        excerpt = clean_text(" ".join([
            *([primary] if primary and (not numeric_set or p_prov != "feed_summary") else []),
            *([summary] if summary and (not numeric_set or s_prov != "feed_summary") else []),
        ]))
        if not excerpt:
            continue
        overlap_terms = claim_terms & _claim_terms(excerpt)
        overlap = len(overlap_terms)
        numeric_match = bool(numeric_set) and numeric_set.issubset(set(NUMERIC_TOKEN_RE.findall(excerpt)))
        if (numeric_match and overlap >= 1) if numeric_set else (overlap >= 2 and bool(overlap_terms - SOURCE_BRAND_TERMS)):
            matches.append((overlap + (4 if numeric_match else 0), row))
    matches.sort(key=lambda pair: pair[0], reverse=True)
    return [row for _s, row in matches[:3]]


def _score_claim(text: str, kind: str, card: EvidenceCard) -> int:
    low = text.lower()
    score = (18 if has_cjk(text) else 0) + min(24, len(MEASURE_RE.findall(text)) * 8)
    if _models_in_text(text) or any(p in low for p in PRODUCT_TERMS) or VERSIONED_PRODUCT_RE.search(text):
        score += 14
    if kind in {"metric", "pricing", "availability", "change", "legal", "organization"}:
        score += 14
    if any(act in low for act, _lbl in ACTION_TERMS):
        score += 8
    if 18 <= len(text) <= 220:
        score += 8
    h_models, c_models = set(_models_in_text(card.event_title)), set(_models_in_text(text))
    score += 10 if (h_models and (h_models & c_models)) else (-16 if (h_models and c_models) else 0)
    if any(m.lower() in low for m in META_PHRASES):
        score -= 28
    if any(n in low for n in NOISE_MARKERS):
        score -= 50
    if contains_sensational_wording(text):
        score -= 40
    return max(0, min(100, score))


def extract_claims(card: EvidenceCard, *, limit: int = 8) -> list[FactClaim]:
    raw_sources = [*card.key_facts, *(str(r["excerpt"]) for r in card.evidence_links if r.get("excerpt"))]
    has_excerpt_field = any("excerpt" in r or "source_summary" in r for r in card.evidence_links)
    seen: set[str] = set()
    claims: list[FactClaim] = []
    for raw in raw_sources:
        for text in _atomic_claim_parts(raw):
            norm = re.sub(r"\W+", "", text.lower())
            if not norm or norm in seen:
                continue
            seen.add(norm)
            kind = _claim_kind(text)
            numeric_tokens = list(dict.fromkeys(NUMERIC_TOKEN_RE.findall(text)))
            score = _score_claim(text, kind, card)
            supporting = _supporting_evidence(text, numeric_tokens, card.evidence_links)
            if not supporting and not has_excerpt_field and card.evidence_links:
                first_url = str(card.evidence_links[0].get("url") or card.evidence_links[0].get("final_url") or "").strip()
                supporting = [card.evidence_links[0]] if first_url else []
            urls = [u for r in supporting if (u := str(r.get("url") or r.get("final_url") or "").strip())]
            rels = [clean_text(str(r.get("reliability") or r.get("tier") or "unknown")) for r in supporting]
            tier = clean_text(str(supporting[0].get("tier") or supporting[0].get("reliability") or "unknown")) if supporting else "unknown"
            first_party = any(str(r.get("reliability") or "").lower() in {"official", "official_social"} or str(r.get("tier") or "").upper() == "A" for r in supporting)
            claims.append(FactClaim(
                claim_id=stable_hash([card.cluster_key, text, *urls], length=20),
                text=text, kind=kind, evidence_urls=urls, verifiable=bool(urls) and score >= 28,
                renderable=is_renderable_cn(text), numeric_tokens=numeric_tokens, score=score,
                source_tier=tier, evidence_reliability=rels, first_party_supported=first_party,
            ))
    claims.sort(key=lambda c: (c.renderable, c.verifiable, c.score, len(c.text)), reverse=True)
    return claims[:limit]


def _neutralize_headline(headline: str, claims: list[FactClaim]) -> str:
    verified = [c for c in claims if c.renderable and c.verifiable]
    org_claims = [c for c in verified if c.kind == "organization"]
    if org_claims and len(org_claims) * 5 >= len(verified) * 3:
        return org_claims[0].text.split("，", 1)[0]
    cleaned = clean_text(headline).replace("跑路", "离职")
    cleaned = re.sub(r"\s*(?:[|｜/]\s*|[-—]\s*)?(?:36\s*氪|InfoQ|量子位|机器之心|极客公园|新智元|钛媒体)(?:独家|首发)?(?:\.cn)?\s*$", "", cleaned, flags=re.I)
    for pat in (r"王炸|杀疯|刷爆|震撼|自曝|颠覆|彻底取代|程序员失业", r"狂喜|炸裂|刚刚|重磅|突发|刷屏", r"[?？!！]+"):
        cleaned = re.sub(pat, "", cleaned, flags=re.I)
    cleaned = normalize_cn_punctuation(cleaned).strip(" ：:；;，,。-—|｜")
    if is_renderable_cn(cleaned) and not contains_sensational_wording(cleaned) and len(cleaned) >= 6:
        return cleaned
    rel = [c for c in verified if not contains_sensational_wording(c.text) and any(has_affirmed_action(c.text, a) for a in ("正式发布", "发布", "推出", "上线", "开源", "更新", "升级"))]
    return rel[0].text.split("，", 1)[0] if rel else (cleaned or clean_text(headline))


def _classify_story_kind(card: EvidenceCard, claims: list[FactClaim], semantic_headline: str, signal_kind: str) -> str:
    low = semantic_headline.lower()
    urls = " ".join(str(r.get("url") or r.get("final_url") or "").lower() for r in card.evidence_links)
    sources = " ".join(str(r.get("source") or "").lower() for r in card.evidence_links)
    if has_arxiv_evidence(card):
        return "research"
    if "huggingface.co/" in urls or "modelscope.cn/" in urls or "hugging face" in sources:
        return "model_repository"
    if "/releases/tag/" in urls or "github release" in sources:
        return "developer_release"
    if signal_kind == "community_rumour":
        return "community_signal"
    if any(c.kind in {"organization", "legal"} and c.verifiable for c in claims) or any(t in low for t in ("融资", "收购", "监管", "合规", "起诉", "诉讼", "离职", "任命", "重组")):
        return "industry"
    if any(t in low for t in ("benchmark", "performance", "评测", "榜单", "跑分")):
        return "benchmark"
    has_model = bool(_models_in_text(semantic_headline)) or "模型" in low
    if has_model and any(has_affirmed_action(low, t) for t in ("发布", "推出", "上线", "开源", "release", "launch")):
        return "model_release"
    if has_model and any(has_affirmed_action(low, t) for t in ("更新", "升级", "增强", "新增")):
        return "model_update"
    if any(p in low for p in PRODUCT_TERMS) or any(c.kind in {"change", "availability", "pricing"} for c in claims):
        return "product_update"
    return "official_update" if card.official_count > 0 else ("media_report" if card.media_count > 0 else "other")


def build_story_spec(card: EvidenceCard) -> StorySpec:
    headline = clean_text(card.event_title)
    claims = extract_claims(card)
    signal_kind = "official_personnel" if card_is_official_personnel_signal(card) else ("community_rumour" if card_is_community_signal(card) else "confirmed")
    disclaimer = SIGNAL_DISCLAIMERS.get(signal_kind, "")
    source_status = (
        "official_personnel" if signal_kind == "official_personnel"
        else "community" if signal_kind == "community_rumour"
        else ("official_confirmed" if card.risk == "green" else "official_signal") if card.official_count > 0
        else "media_cross_checked" if card.media_count >= 2
        else "single_media" if card.media_count == 1
        else ("community" if card.community_count > 0 else "unverified")
    )
    semantic_headline = _neutralize_headline(headline, claims)
    kind = _classify_story_kind(card, claims, semantic_headline, signal_kind)
    brand = infer_brand(semantic_headline) or card_display_entity(card, fallback="")
    models = extract_model_names(_space_cjk_latin(semantic_headline))
    subject = brand if (brand and brand != "AI") else (models[0] if models else card_display_entity(card, fallback="AI"))
    entity = subject if subject != "AI" and (semantic_headline != headline or not card.entity or card.entity == "AI") else (card.entity or subject)
    action = next((lbl for raw, lbl in ACTION_TERMS if has_affirmed_action(semantic_headline, raw)), "") or next(
        (lbl for c in claims for raw, lbl in ACTION_TERMS if has_affirmed_action(c.text, raw)), "动态"
    )
    model_keys, hf_id = _models_in_text(semantic_headline), extract_hf_model_id(semantic_headline)
    anchors = model_keys[:2] or ([hf_id.lower()] if hf_id else []) or [p for p in PRODUCT_TERMS if p in semantic_headline.lower()][:2]
    if not anchors:
        stop = {"发布", "推出", "上线", "更新", "新增", "正式", "release", "with", "from"}
        anchors = [w for w in re.findall(r"[a-z][a-z0-9_.-]{2,}|[\u4e00-\u9fff]{2,}", _space_cjk_latin(semantic_headline).lower()) if w not in stop][:3]
    topic_key = re.sub(r"\s+", "-", "|".join([(entity or "ai").lower(), kind, *anchors]))[:160]
    family_key = (
        "" if kind == "industry"
        else f"model|{'benchmark' if kind == 'benchmark' else ('integration' if action == '接入' else 'release')}|{model_keys[0]}"[:140] if model_keys
        else (f"{(entity or 'ai').lower()}|{anchors[0]}"[:120] if anchors else "")
    )
    warnings = [*card.uncertainty, *([] if any(c.renderable and c.verifiable for c in claims) else ["no_renderable_verifiable_claim"])]
    audience = [
        *(["developers"] if kind in {"developer_release", "benchmark", "model_repository"} else []),
        *(["users"] if kind in {"product_update", "model_release", "model_update"} else []),
        *(["industry"] if kind in {"industry", "research"} else []),
    ] or ["general"]
    ev_count = len({str(r.get("url") or r.get("source") or "") for r in card.evidence_links if r.get("url") or r.get("source")})
    return StorySpec(
        story_id=stable_hash([card.cluster_key, topic_key], length=20), cluster_key=card.cluster_key,
        entity=entity or "AI", subject=subject, kind=kind, action=action, source_status=source_status,
        signal_kind=signal_kind, semantic_headline=semantic_headline, claims=claims, topic_key=topic_key,
        family_key=family_key, disclaimer=disclaimer, headline=headline, first_party=card.official_count > 0,
        evidence_count=ev_count, audience=audience, warnings=warnings,
    )

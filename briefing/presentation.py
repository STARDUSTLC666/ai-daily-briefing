from __future__ import annotations

"""Safe, audience-facing identity helpers for verified news cards.

The verification layer keeps its original entity label for traceability.  That
label can occasionally be a broad fallback (for example ``论文 / arXiv`` from a
benchmark keyword) even though the first-party evidence clearly belongs to a
vendor.  Public copy must prefer the evidence-backed identity, otherwise a
technical blog can be narrated as a paper.
"""

from .models import EvidenceCard
from .util import clean_text


PAPER_ENTITY_LABELS = {"论文 / arxiv", "论文/arxiv", "论文", "arxiv"}
GENERIC_ENTITY_LABELS = {"", "ai", "人工智能"}

# Keep this intentionally conservative: these names are only used to repair a
# generic or paper fallback entity, never to override a concrete verified
# entity selected by the clustering layer.
DISPLAY_BRAND_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("NVIDIA", ("nvidia", "英伟达")),
    ("OpenAI", ("openai", "chatgpt", "codex", "sora")),
    ("Anthropic", ("anthropic", "claude")),
    ("Google", ("google", "gemini", "deepmind", "gemma")),
    ("阿里千问", ("qwen", "通义", "千问")),
    ("DeepSeek", ("deepseek", "深度求索")),
    ("Kimi", ("kimi", "moonshot", "月之暗面")),
    ("MiniMax", ("minimax", "海螺")),
    ("Mistral AI", ("mistral", "mixtral", "codestral")),
    ("Meta", ("meta ai", "llama")),
    ("xAI", ("xai", "x.ai", "grok")),
    ("智谱", ("zhipu", "glm", "智谱")),
    ("字节跳动", ("bytedance", "doubao", "豆包", "字节")),
    ("腾讯混元", ("tencent", "hunyuan", "混元", "腾讯")),
    ("百度文心", ("baidu", "ernie", "文心", "百度")),
    ("GitHub", ("github", "copilot")),
    ("Hugging Face", ("hugging face", "huggingface")),
    ("ModelScope", ("modelscope", "魔搭")),
)


def has_arxiv_evidence(card: EvidenceCard) -> bool:
    """Return true only for an actual arXiv source or URL."""
    return any(
        str(evidence.get("source") or "").lower().startswith("arxiv")
        or "arxiv.org/" in str(evidence.get("url") or evidence.get("final_url") or "").lower()
        for evidence in card.evidence_links
    )


def is_paper_entity(value: str) -> bool:
    normalized = clean_text(value or "").lower().replace(" ", "")
    return normalized in {label.replace(" ", "") for label in PAPER_ENTITY_LABELS}


def _brand_from_text(text: str) -> str:
    lowered = clean_text(text or "").lower()
    for brand, patterns in DISPLAY_BRAND_PATTERNS:
        if any(pattern in lowered for pattern in patterns):
            return brand
    return ""


def card_display_entity(card: EvidenceCard, fallback: str = "AI") -> str:
    """Return a conservative, public-facing entity for a card.

    A concrete entity remains authoritative.  Evidence/title inference is only
    used when clustering left a generic or non-arXiv paper fallback behind.
    """
    raw_entity = clean_text(card.entity or "")
    normalized = raw_entity.lower().replace(" ", "")
    if normalized not in {label.replace(" ", "") for label in GENERIC_ENTITY_LABELS} and not is_paper_entity(raw_entity):
        return raw_entity.split("/", 1)[0].strip() or fallback

    evidence_text = " ".join(
        " ".join(
            [
                str(evidence.get("source") or ""),
                str(evidence.get("title") or ""),
                str(evidence.get("url") or evidence.get("final_url") or ""),
            ]
        )
        for evidence in card.evidence_links
    )
    inferred = _brand_from_text(evidence_text)
    if not inferred:
        inferred = _brand_from_text(" ".join([card.event_title, *card.key_facts]))
    if inferred:
        return inferred
    if is_paper_entity(raw_entity) and has_arxiv_evidence(card):
        return "论文"
    return fallback

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re

from .content_enrichment import ArticleResult
from .models import Document, EvidenceExcerpt, FeedItem
from .util import clean_text, stable_hash

UTC = timezone.utc
EXTRACTOR_VERSION = "document-store/v1"


def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def item_unique_key(item: FeedItem) -> str:
    return f"{item.source_id}:{item.guid or item.link or item.title}"


def _language(text: str) -> str:
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text or ""))
    letters = len(re.findall(r"[A-Za-z]", text or ""))
    if cjk >= 12 and cjk >= letters * 0.15:
        return "zh"
    if letters >= 20:
        return "en"
    return "unknown"


def _quality(article: ArticleResult) -> int:
    text = clean_text(article.text or article.description)
    score = 0
    if len(text) >= 300:
        score += 30
    elif len(text) >= 100:
        score += 18
    if article.title:
        score += 10
    if article.description:
        score += 8
    if article.published_at:
        score += 12
    if article.facts:
        score += min(36, len(article.facts) * 12)
    noise = sum(text.lower().count(marker) for marker in ["cookie", "加载更多", "隐私", "javascript", "utm_source="])
    score -= min(30, noise * 6)
    return max(0, min(100, score))


def build_document(item: FeedItem, article: ArticleResult, *, fetched_at: datetime | None = None) -> tuple[Document, list[EvidenceExcerpt]]:
    content = clean_text(article.text or article.description)
    content_hash = _sha256(content)
    unique_key = item_unique_key(item)
    document_id = stable_hash([unique_key, article.final_url or article.url, content_hash])[:32]
    document = Document(
        document_id=document_id,
        item_unique_key=unique_key,
        source_id=item.source_id,
        url=article.url or item.link,
        final_url=article.final_url or article.url or item.link,
        title=clean_text(article.title or item.title),
        description=clean_text(article.description),
        content=content,
        content_hash=content_hash,
        extractor=article.crawler,
        extractor_version=EXTRACTOR_VERSION,
        fetched_at=fetched_at or datetime.now(UTC),
        published_at=article.published_at,
        language=_language(content),
        quality_score=_quality(article),
        metadata={
            "status": article.status,
            "status_code": article.status_code,
            "images": article.images[:10],
            "links": article.links[:80],
            "screenshot_path": article.screenshot_path,
            "repair": article.repair,
            "error": article.error[:500],
        },
    )
    excerpts: list[EvidenceExcerpt] = []
    cursor = 0
    for index, raw in enumerate(article.facts or []):
        text = clean_text(raw)
        if len(text) < 12:
            continue
        start = content.find(text, cursor) if content else -1
        if start < 0:
            start = content.find(text) if content else -1
        end = start + len(text) if start >= 0 else None
        if start >= 0:
            cursor = start + len(text)
        text_hash = _sha256(text)
        excerpts.append(
            EvidenceExcerpt(
                excerpt_id=stable_hash([document_id, text_hash])[:32],
                document_id=document_id,
                text=text,
                text_hash=text_hash,
                start_offset=start if start >= 0 else None,
                end_offset=end,
                extractor=f"{article.crawler}:key_fact",
                score=max(0, 100 - index * 3),
                metadata={"claim_candidate": True},
            )
        )
    return document, excerpts

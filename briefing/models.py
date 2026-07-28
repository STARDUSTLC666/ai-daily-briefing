from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

UTC = timezone.utc


@dataclass(slots=True)
class Source:
    id: str
    name: str
    tier: str
    type: str
    region: str
    url: str
    enabled: bool = True
    reliability: str = "unknown"
    topics: list[str] = field(default_factory=list)
    timeout_seconds: int = 18

    @property
    def is_official(self) -> bool:
        return self.tier.upper() == "A" or self.reliability in {"official", "official_social"}

    @property
    def is_community(self) -> bool:
        return self.tier.upper() == "C" or self.reliability == "community"


@dataclass(slots=True)
class FeedItem:
    source_id: str
    source_name: str
    source_tier: str
    source_reliability: str
    title: str
    link: str
    guid: str
    summary: str = ""
    published_at: datetime | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Document:
    document_id: str
    item_unique_key: str
    source_id: str
    url: str
    final_url: str
    title: str
    description: str
    content: str
    content_hash: str
    extractor: str
    extractor_version: str
    fetched_at: datetime
    published_at: datetime | None = None
    language: str = ""
    quality_score: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EvidenceExcerpt:
    excerpt_id: str
    document_id: str
    text: str
    text_hash: str
    start_offset: int | None = None
    end_offset: int | None = None
    extractor: str = ""
    score: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SourceHealth:
    source_id: str
    source_name: str
    tier: str
    enabled: bool
    status: str
    status_code: int | None = None
    item_count: int = 0
    latest_item_at: datetime | None = None
    latency_ms: int | None = None
    error: str = ""
    final_url: str = ""
    stale: bool = False
    no_date_count: int = 0


@dataclass(slots=True)
class Cluster:
    key: str
    title: str
    items: list[FeedItem]
    entity: str = "AI"
    published_at: datetime | None = None
    score: float = 0.0


@dataclass(slots=True)
class EvidenceCard:
    cluster_key: str
    event_title: str
    entity: str
    risk: str
    confidence: int
    selected: bool
    reason: str
    source_count: int
    official_count: int
    media_count: int
    community_count: int
    first_seen_at: datetime
    latest_published_at: datetime | None
    key_facts: list[str]
    evidence_links: list[dict[str, str]]
    uncertainty: list[str]
    score: float = 0.0
    # Editorial depth is assigned after verification. ``headline`` stories get
    # a full explainer; ``brief`` stories retain the same evidence contract but
    # use a compact, high-density treatment with a wider real-TTS budget.
    editorial_tier: str = "headline"
    # A portfolio card can represent several raw clusters after same-story
    # merging. Keep those keys so automatic quality repair can exclude the
    # whole failed story rather than selecting it again under a merged key.
    source_cluster_keys: list[str] = field(default_factory=list)


def iso(dt: datetime | None) -> str:
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()

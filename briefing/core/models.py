from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
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
    editorial_tier: str = "headline"
    source_cluster_keys: list[str] = field(default_factory=list)


@dataclass(slots=True)
class StageRecord:
    name: str
    status: str
    started_at: str
    finished_at: str = ""
    duration_ms: int = 0
    input_count: int | None = None
    output_count: int | None = None
    fingerprint: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass(slots=True)
class RunResult:
    run_id: str
    run_date: str
    out_dir: Path
    source_health: list[SourceHealth]
    all_cards: list[EvidenceCard]
    selected_cards: list[EvidenceCard]
    render_info: dict[str, Any] = field(default_factory=dict)
    quality: dict[str, Any] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)


def iso(dt: datetime | None) -> str:
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()

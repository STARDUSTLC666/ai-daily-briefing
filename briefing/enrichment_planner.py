from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from urllib.parse import urlparse

from .cluster import cluster_items
from .models import Cluster, FeedItem
from .policy_rules import is_concrete_public_ai_policy
from .util import clean_text

UTC = timezone.utc

EVENT_TERMS = (
    "发布", "推出", "上线", "开放", "开源", "新增", "升级", "更新", "接入", "可用",
    "release", "launch", "introduc", "announce", "available", "open source", "update",
)
NOISE_TERMS = (
    "融资", "股价", "招聘", "课程", "峰会", "会议议程", "信用卡", "人才观",
    "roundup", "weekly digest", "每日早报", "一周回顾",
)
MODEL_OR_VERSION = re.compile(
    r"\b(?:gpt|claude|gemini|gemma|qwen|deepseek|kimi|llama|mistral|grok|minimax|glm|hunyuan|muse|kat|codex)"
    r"[-‐‑–—_\s]?[a-z]*\d[A-Za-z0-9_.‐‑–—-]*|\bv?\d+(?:\.\d+){1,4}(?:[-._a-z0-9]+)?\b",
    flags=re.I,
)


@dataclass(frozen=True, slots=True)
class PlannedItem:
    item: FeedItem
    cluster_key: str
    entity: str
    score: float
    reason: tuple[str, ...]


def _domain(item: FeedItem) -> str:
    return urlparse(item.link or "").netloc.lower().removeprefix("www.")


def _is_high_volume_artifact(item: FeedItem) -> bool:
    source_id = item.source_id.lower()
    return source_id.endswith("_hf_models") or source_id.startswith("arxiv_")


def _cluster_score(cluster: Cluster, *, now: datetime) -> tuple[float, tuple[str, ...]]:
    text = clean_text(" ".join([cluster.title, *[item.title for item in cluster.items[:4]], *[item.summary for item in cluster.items[:2]]])).lower()
    reasons: list[str] = []
    score = 0.0
    policy_context = [value for item in cluster.items[:3] for value in (item.title, item.summary)]
    concrete_public_policy = is_concrete_public_ai_policy(cluster.title, policy_context)
    official = sum(1 for item in cluster.items if item.source_tier.upper() == "A" or item.source_reliability == "official")
    media = sum(1 for item in cluster.items if item.source_tier.upper() == "B" or item.source_reliability == "media")
    domains = {_domain(item) for item in cluster.items if _domain(item)}
    if any(term in text for term in NOISE_TERMS) and not concrete_public_policy:
        # A weekly digest, financing item, course, or conference should not
        # consume a costly article-crawl slot merely because it is official.
        return -1000.0, ("noise_rejected",)
    if cluster.published_at and cluster.published_at.astimezone(UTC) > now + timedelta(hours=2):
        return -1000.0, ("future_dated",)
    if official:
        score += 32
        reasons.append("official")
    if media:
        score += min(12, media * 4)
        reasons.append("media")
    if len(domains) >= 2:
        score += min(18, len(domains) * 5)
        reasons.append("cross_source")
    if any(term in text for term in EVENT_TERMS):
        score += 20
        reasons.append("event_action")
    if MODEL_OR_VERSION.search(text):
        score += 18
        reasons.append("named_product_or_version")
    if cluster.published_at is not None:
        score += 16
        reasons.append("dated")
    if cluster.entity and cluster.entity not in {"AI", "论文 / arXiv"}:
        score += 8
        reasons.append("named_entity")
    if concrete_public_policy:
        # 完整公共政策需要正文核验数字与任务，优先占用一次抓取预算。
        score += 80
        reasons.append("concrete_public_ai_policy")
    high_volume_only = all(_is_high_volume_artifact(item) for item in cluster.items)
    if high_volume_only:
        score -= 10
        reasons.append("artifact_penalty")
    return score, tuple(reasons)


def _representatives(cluster: Cluster, max_per_cluster: int = 2) -> list[FeedItem]:
    def rank(item: FeedItem) -> tuple[int, int, int, float, str]:
        official = int(item.source_tier.upper() == "A" or item.source_reliability == "official")
        editorial = int(item.source_reliability == "media" or item.source_tier.upper() == "B")
        dated = int(item.published_at is not None)
        timestamp = item.published_at.timestamp() if item.published_at else 0.0
        # A dated source is more useful for time-sensitive enrichment than an
        # undated listing page, even if the listing belongs to an official site.
        return dated, official, editorial, timestamp, item.guid or item.link

    rows = sorted(cluster.items, key=rank, reverse=True)
    result: list[FeedItem] = []
    domains: set[str] = set()
    for item in rows:
        domain = _domain(item)
        if result and domain and domain in domains:
            continue
        result.append(item)
        if domain:
            domains.add(domain)
        if len(result) >= max_per_cluster:
            break
    return result


def plan_enrichment_items(
    items: list[FeedItem],
    *,
    target_stories: int = 10,
    multiplier: int = 5,
    max_items: int = 0,
    max_per_entity: int = 0,
    max_per_source: int = 8,
    max_per_domain: int = 0,
    max_per_high_volume_source: int = 0,
    now: datetime | None = None,
) -> tuple[list[FeedItem], dict[str, object]]:
    """Plan a diversified *URL* crawl budget before expensive fetching.

    This does not select publishable news.  It only ranks already-eligible
    page candidates; all evidence, screenshot, Chinese-copy, and publication
    rules remain enforced by the later verification and editing stages.
    """
    reference_time = (now or datetime.now(UTC)).astimezone(UTC)
    desired = max(1, int(target_stories)) * max(1, int(multiplier))
    if max_items > 0:
        desired = min(desired, int(max_items))
    clusters = cluster_items(items)
    ranked: list[tuple[float, Cluster, tuple[str, ...]]] = []
    for cluster in clusters:
        score, reasons = _cluster_score(cluster, now=reference_time)
        ranked.append((score, cluster, reasons))
    ranked.sort(
        key=lambda row: (
            -row[0],
            -(row[1].published_at.timestamp() if row[1].published_at else 0.0),
            row[1].key,
        )
    )

    planned: list[PlannedItem] = []
    seen: set[str] = set()
    entity_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    domain_counts: dict[str, int] = {}
    high_volume_counts: dict[str, int] = {}
    rejected: dict[str, int] = {}
    for score, cluster, reasons in ranked:
        if len(planned) >= desired:
            break
        if score <= 0:
            rejected["non_positive_score"] = rejected.get("non_positive_score", 0) + 1
            continue
        entity = cluster.entity or "AI"
        entity_is_generic = entity in {"AI", "论文 / arXiv"}
        if not entity_is_generic and max_per_entity > 0 and entity_counts.get(entity, 0) >= max_per_entity:
            rejected["entity_cap"] = rejected.get("entity_cap", 0) + 1
            continue
        for item in _representatives(cluster):
            if len(planned) >= desired:
                break
            key = item.guid or item.link
            if not key or key in seen:
                continue
            if max_per_source > 0 and source_counts.get(item.source_id, 0) >= max_per_source:
                rejected["source_cap"] = rejected.get("source_cap", 0) + 1
                continue
            domain = _domain(item)
            if max_per_domain > 0 and domain_counts.get(domain, 0) >= max_per_domain:
                rejected["domain_cap"] = rejected.get("domain_cap", 0) + 1
                continue
            if _is_high_volume_artifact(item) and max_per_high_volume_source > 0 and high_volume_counts.get(item.source_id, 0) >= max_per_high_volume_source:
                rejected["high_volume_source_cap"] = rejected.get("high_volume_source_cap", 0) + 1
                continue
            seen.add(key)
            source_counts[item.source_id] = source_counts.get(item.source_id, 0) + 1
            domain_counts[domain] = domain_counts.get(domain, 0) + 1
            if _is_high_volume_artifact(item):
                high_volume_counts[item.source_id] = high_volume_counts.get(item.source_id, 0) + 1
            if not entity_is_generic:
                entity_counts[entity] = entity_counts.get(entity, 0) + 1
            planned.append(PlannedItem(item=item, cluster_key=cluster.key, entity=entity, score=score, reason=reasons))

    selected = [row.item for row in planned]
    diagnostics: dict[str, object] = {
        "mode": "cluster_first_candidate_enrichment",
        "input_items": len(items),
        "clusters": len(clusters),
        "target_stories": max(1, int(target_stories)),
        "multiplier": max(1, int(multiplier)),
        "budget": desired,
        "budget_unit": "candidate_urls",
        "reference_time_utc": reference_time.isoformat(),
        "selected_items": len(selected),
        "selected_clusters": len({row.cluster_key for row in planned}),
        "selected_entities": entity_counts,
        "selected_sources": source_counts,
        "selected_domains": domain_counts,
        "rejections": rejected,
        "top_candidates": [
            {
                "cluster_key": row.cluster_key,
                "entity": row.entity,
                "score": round(row.score, 2),
                "title": row.item.title,
                "source": row.item.source_name,
                "reasons": list(row.reason),
            }
            for row in planned[:20]
        ],
    }
    return selected, diagnostics

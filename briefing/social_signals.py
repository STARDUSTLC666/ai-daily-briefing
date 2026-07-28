from __future__ import annotations

from urllib.parse import urlparse

from .models import EvidenceCard, FeedItem

OFFICIAL_PERSONNEL_RELIABILITIES = {"official_personnel", "official_staff"}
OFFICIAL_SOCIAL_RELIABILITIES = {"official_social"}
COMMUNITY_RELIABILITIES = {"community", "aggregator"}
X_DOMAINS = {"x.com", "twitter.com", "mobile.twitter.com", "www.x.com", "www.twitter.com"}


def is_official_personnel_reliability(value: object) -> bool:
    return str(value or "").strip().lower() in OFFICIAL_PERSONNEL_RELIABILITIES


def is_official_social_reliability(value: object) -> bool:
    return str(value or "").strip().lower() in OFFICIAL_SOCIAL_RELIABILITIES


def is_x_url(url: object) -> bool:
    try:
        parsed = urlparse(str(url or ""))
    except Exception:
        return False
    host = parsed.netloc.lower().removeprefix("www.")
    return host in {"x.com", "twitter.com", "mobile.twitter.com"} or host.endswith(".twitter.com")


def feed_item_is_official_personnel(item: FeedItem) -> bool:
    if is_official_personnel_reliability(item.source_reliability):
        return True
    if is_x_url(item.link) and "official" in str(item.source_name or "").lower() and "person" in str(item.source_name or "").lower():
        return True
    return False


def evidence_is_official_personnel(evidence: dict[str, object]) -> bool:
    """Recognize personnel signals only from explicit source provenance.

    ``X / <account>`` is merely a display convention used by both staff and
    community feeds.  Treating that prefix as official would turn a community
    post into an "insider" item, so the configured reliability is the sole
    authority here.
    """
    return is_official_personnel_reliability(evidence.get("reliability"))


def evidence_is_official_social(evidence: dict[str, object]) -> bool:
    return is_official_social_reliability(evidence.get("reliability")) and evidence_is_x_post(evidence)


def evidence_is_x_post(evidence: dict[str, object]) -> bool:
    """Whether an evidence record points to the original X/Twitter post."""
    return is_x_url(evidence.get("url")) or is_x_url(evidence.get("final_url"))


def card_has_x_post(card: EvidenceCard) -> bool:
    return any(evidence_is_x_post(evidence) for evidence in card.evidence_links)


def card_is_official_personnel_signal(card: EvidenceCard) -> bool:
    return any(evidence_is_official_personnel(evidence) for evidence in card.evidence_links)


def card_is_official_social_signal(card: EvidenceCard) -> bool:
    """Whether an original X post comes from a verified company/product account."""
    return any(evidence_is_official_social(evidence) for evidence in card.evidence_links)


def card_is_community_signal(card: EvidenceCard) -> bool:
    """Whether a card remains an unconfirmed community-origin signal.

    A single media relay does not turn an original community/X post into a
    confirmed report.  Keep the rumour label and require the source-post
    screenshot until an official source or multiple media confirmations make
    the event independently attributable.
    """
    if card_is_official_social_signal(card) or card_is_official_personnel_signal(card) or card.official_count > 0:
        return False
    if card.community_count <= 0 or card.media_count > 1:
        return False
    return any(
        str(evidence.get("reliability") or "").strip().lower() in COMMUNITY_RELIABILITIES
        for evidence in card.evidence_links
    )


def social_origin_evidence_indexes(card: EvidenceCard) -> list[int]:
    """Return evidence indexes that must be visible for a social-signal story.

    For an official-person X signal, the original post is mandatory. For a
    community signal, prefer an original X post when one exists; otherwise the
    first community/aggregator evidence is the traceable source post.
    """
    if card_is_official_social_signal(card) or card_is_official_personnel_signal(card):
        return [index for index, evidence in enumerate(card.evidence_links) if evidence_is_x_post(evidence)]
    if not card_is_community_signal(card):
        return []
    x_indexes = [index for index, evidence in enumerate(card.evidence_links) if evidence_is_x_post(evidence)]
    if x_indexes:
        return x_indexes
    community_indexes = [
        index
        for index, evidence in enumerate(card.evidence_links)
        if str(evidence.get("reliability") or "").strip().lower() in COMMUNITY_RELIABILITIES
        or str(evidence.get("tier") or "").strip().upper() in {"C", "D"}
    ]
    return community_indexes[:1] or ([0] if card.evidence_links else [])

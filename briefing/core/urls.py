from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .models import EvidenceCard, FeedItem

UTC = timezone.utc

OFFICIAL_PERSONNEL_RELIABILITIES = {"official_personnel", "official_staff"}
OFFICIAL_SOCIAL_RELIABILITIES = {"official_social"}
COMMUNITY_RELIABILITIES = {"community", "aggregator"}
X_HOSTS = {"x.com", "twitter.com", "mobile.twitter.com"}

_TRACKING_QUERY_PREFIXES = ("utm_", "spm", "from", "ref", "fbclid", "gclid", "mkt_tok", "mc_")
_X_STATUS_RE = re.compile(
    r"^https?://(?:www\.|mobile\.)?(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,30})/status/(\d{10,25})\b",
    re.I,
)
_TWITTER_EPOCH_MS = 1288834974657


def url_host(url: object) -> str:
    try:
        parsed = urlparse(str(url or "").strip())
    except Exception:
        return ""
    host = parsed.netloc.lower().split(":", 1)[0]
    return host.removeprefix("www.")


def is_x_url(url: object) -> bool:
    host = url_host(url)
    return host in X_HOSTS or host.endswith(".twitter.com")


def parse_x_status(url: object) -> tuple[str, str] | None:
    """Return `(handle_lower, status_id)` if `url` is an X/Twitter status URL."""
    match = _X_STATUS_RE.match(str(url or "").strip())
    if not match:
        return None
    return match.group(1).lower(), match.group(2)


def decode_x_snowflake_utc(status_id: str | int) -> datetime | None:
    try:
        sid = int(status_id)
    except (TypeError, ValueError):
        return None
    if sid < (1 << 22):
        return None
    ts_ms = (sid >> 22) + _TWITTER_EPOCH_MS
    try:
        return datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC)
    except (OSError, OverflowError, ValueError):
        return None


def canonical_url(url: object) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    x_post = parse_x_status(raw)
    if x_post:
        handle, status_id = x_post
        return f"https://x.com/{handle}/status/{status_id}"
    try:
        parsed = urlparse(raw)
    except Exception:
        return raw
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return raw
    host = parsed.netloc.lower().removeprefix("www.")
    path = re.sub(r"/+", "/", parsed.path or "/")
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    kept_query = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=False)
        if not k.lower().startswith(_TRACKING_QUERY_PREFIXES)
    ]
    return urlunparse(("https" if parsed.scheme == "https" else "http", host, path, "", urlencode(kept_query), ""))


def is_official_personnel_reliability(value: object) -> bool:
    return str(value or "").strip().lower() in OFFICIAL_PERSONNEL_RELIABILITIES


def is_official_social_reliability(value: object) -> bool:
    return str(value or "").strip().lower() in OFFICIAL_SOCIAL_RELIABILITIES


def feed_item_is_official_personnel(item: FeedItem) -> bool:
    if is_official_personnel_reliability(item.source_reliability):
        return True
    lowered_name = str(item.source_name or "").lower()
    return is_x_url(item.link) and "official" in lowered_name and "person" in lowered_name


def evidence_is_official_personnel(evidence: dict[str, object]) -> bool:
    return is_official_personnel_reliability(evidence.get("reliability"))


def evidence_is_x_post(evidence: dict[str, object]) -> bool:
    return is_x_url(evidence.get("url")) or is_x_url(evidence.get("final_url"))


def evidence_is_official_social(evidence: dict[str, object]) -> bool:
    return is_official_social_reliability(evidence.get("reliability")) and evidence_is_x_post(evidence)


def card_has_x_post(card: EvidenceCard) -> bool:
    return any(evidence_is_x_post(ev) for ev in card.evidence_links)


def card_is_official_personnel_signal(card: EvidenceCard) -> bool:
    return any(evidence_is_official_personnel(ev) for ev in card.evidence_links)


def card_is_official_social_signal(card: EvidenceCard) -> bool:
    return any(evidence_is_official_social(ev) for ev in card.evidence_links)


def card_is_community_signal(card: EvidenceCard) -> bool:
    if card_is_official_social_signal(card) or card_is_official_personnel_signal(card) or card.official_count > 0:
        return False
    if card.community_count <= 0 or card.media_count > 1:
        return False
    return any(str(ev.get("reliability") or "").strip().lower() in COMMUNITY_RELIABILITIES for ev in card.evidence_links)


def social_origin_evidence_indexes(card: EvidenceCard) -> list[int]:
    if card_is_official_social_signal(card) or card_is_official_personnel_signal(card):
        return [idx for idx, ev in enumerate(card.evidence_links) if evidence_is_x_post(ev)]
    if not card_is_community_signal(card):
        return []
    x_indexes = [idx for idx, ev in enumerate(card.evidence_links) if evidence_is_x_post(ev)]
    if x_indexes:
        return x_indexes
    community_indexes = [
        idx
        for idx, ev in enumerate(card.evidence_links)
        if str(ev.get("reliability") or "").strip().lower() in COMMUNITY_RELIABILITIES
        or str(ev.get("tier") or "").strip().upper() in {"C", "D"}
    ]
    return community_indexes[:1] or ([0] if card.evidence_links else [])

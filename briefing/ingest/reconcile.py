from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from ..core import (
    UTC,
    FeedItem,
    Source,
    all_official_domains,
    canonical_url,
    clean_text,
    decode_x_snowflake_utc,
    load_sources,
    now_utc,
    parse_x_status,
    url_host,
)
from .enrich import DEFAULT_USER_AGENT, _fetch_article_http

GENERIC_PLATFORM_HOSTS = {
    "github.com",
    "huggingface.co",
    "modelscope.cn",
    "x.com",
    "twitter.com",
    "mobile.twitter.com",
}

TRUSTED_SECOND_HOP_MEDIA_HOSTS = {
    "arstechnica.com",
    "bloomberg.com",
    "reuters.com",
    "techcrunch.com",
    "theverge.com",
    "wired.com",
}

EVENT_TOKEN_STOPWORDS = {
    "about", "adds", "after", "agent", "agents", "announces", "available", "bring", "brings",
    "coding", "directly", "from", "into", "introduces", "launch", "launches", "model", "models",
    "news", "official", "release", "releases", "released", "support", "supports", "that", "their",
    "this", "update", "updates", "using", "with", "workflow", "workflows", "人工智能", "发布", "推出",
    "上线", "更新", "新增", "支持", "官方", "消息", "工作流",
}

SPECIFIC_PATH_TERMS = {
    "announcement", "announcements", "blog", "changelog", "developer", "docs", "news", "product",
    "release", "releases", "research", "updates",
}


@dataclass(slots=True)
class _OfficialCandidate:
    url: str
    label: str
    host: str
    score: int
    matched_tokens: list[str]
    reason: str
    social_reliability: str = ""


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", clean_text(value).lower())


def _domain_brand(host: str) -> str:
    parts = [part for part in host.lower().split(".") if part and part not in {"www", "m", "mobile"}]
    if not parts:
        return ""
    if len(parts) >= 3 and parts[-2:] == ["github", "io"]:
        return _compact(parts[-3])
    if host == "x.ai":
        return "xai"
    if len(parts) >= 2:
        return _compact(parts[-2])
    return _compact(parts[0])


def _event_tokens(item: FeedItem) -> list[str]:
    result: list[str] = []
    for value in (clean_text(item.title), clean_text(item.summary, 600)):
        lowered = value.lower()
        raw_tokens = re.findall(
            r"[a-z][a-z0-9_.-]{2,}|\b[a-z]{1,3}\d[\w.-]*\b|\bv?\d+(?:\.\d+)+\b|[\u4e00-\u9fff]{2,}",
            lowered,
        )
        for raw in raw_tokens:
            token = raw.strip("-_.")
            is_version = bool(re.fullmatch(r"[a-z]{1,3}\d[\w.-]*|v?\d+(?:\.\d+)+", token))
            if (len(token) < 2 or (len(token) < 3 and not is_version)) or token in EVENT_TOKEN_STOPWORDS:
                continue
            if token not in result:
                result.append(token)
            if "-" in token:
                for part in token.split("-"):
                    part = part.strip("_.")
                    part_ver = bool(re.fullmatch(r"[a-z]{1,3}\d[\w.]*|v?\d+(?:\.\d+)+", part))
                    if (len(part) >= 3 or part_ver) and part not in EVENT_TOKEN_STOPWORDS and part not in result:
                        result.append(part)
    return result[:18]


def _resolve_official_registry(sources: list[Source] | None) -> tuple[set[str], dict[str, str]]:
    known_hosts = {dom for dom in all_official_domains() if dom not in GENERIC_PLATFORM_HOSTS}
    social_handles: dict[str, str] = {}
    effective_sources = sources
    if effective_sources is None:
        try:
            effective_sources = load_sources(include_disabled=True)
        except Exception:
            effective_sources = []
    for src in effective_sources:
        if src.is_official and src.type != "agent_social":
            h = url_host(src.url)
            if h and h not in GENERIC_PLATFORM_HOSTS and not h.endswith("nitter.net"):
                known_hosts.add(h)
        rel = str(src.reliability or "").lower()
        if rel in {"official_social", "official_personnel", "official_staff"}:
            fragment = urlparse(src.url).fragment
            handles = [v.strip().lstrip("@").lower() for v in fragment.split(",") if v.strip()]
            path_match = re.search(r"/(?:twitter/user/)?([A-Za-z0-9_]{1,32})(?:/rss)?$", urlparse(src.url).path)
            if path_match:
                handles.append(path_match.group(1).lower())
            for handle in handles:
                social_handles[handle] = rel
    return known_hosts, social_handles


def _host_is_known_official(host: str, known_hosts: set[str]) -> bool:
    return any(host == known or host.endswith("." + known) for known in known_hosts)


def _x_status_published_at(url: str, *, now: datetime) -> datetime | None:
    parsed = parse_x_status(url)
    if not parsed:
        return None
    published_at = decode_x_snowflake_utc(parsed[1])
    if published_at is None:
        return None
    ref = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
    if published_at > ref + timedelta(minutes=10):
        return None
    return published_at


def _meaningful_social_label(label: str) -> str:
    cleaned = clean_text(label, 240)
    normalized = cleaned.lower().strip().rstrip("/")
    if normalized in {"x", "x.com", "www.x.com", "twitter", "twitter.com", "www.twitter.com"}:
        return ""
    probe = f"https://{cleaned}" if re.match(r"^(?:www\.)?(?:x\.com|twitter\.com)/", cleaned, flags=re.I) else cleaned
    if parse_x_status(probe):
        return ""
    return cleaned


def _score_candidate(
    item: FeedItem,
    link: dict[str, Any],
    *,
    known_hosts: set[str],
    social_handles: dict[str, str],
) -> _OfficialCandidate | None:
    raw_url = str(link.get("url") or "").strip()
    if not raw_url.startswith(("http://", "https://")):
        return None
    url = canonical_url(raw_url)
    if not url or url == canonical_url(item.link):
        return None
    host = url_host(url)
    if not host or host == url_host(item.link):
        return None
    label = clean_text(str(link.get("text") or link.get("title") or ""), 180)
    social = parse_x_status(raw_url)
    if social:
        reliability = social_handles.get(social[0], "")
        if not reliability:
            return None
        return _OfficialCandidate(
            url=url,
            label=label,
            host=host,
            score=120,
            matched_tokens=[social[0]],
            reason="configured_official_x_account",
            social_reliability=reliability,
        )

    title_compact = _compact(item.title)
    brand = _domain_brand(host)
    known = _host_is_known_official(host, known_hosts)
    brand_match = len(brand) >= 4 and brand in title_compact
    parsed = urlparse(url)
    candidate_blob = clean_text(f"{label} {parsed.path.replace('/', ' ')} {parsed.query}").lower()
    candidate_compact = _compact(candidate_blob)
    event_tokens = _event_tokens(item)
    matched = [tok for tok in event_tokens if tok in candidate_blob or _compact(tok) in candidate_compact]

    platform_match = False
    if host in {"github.com", "huggingface.co", "modelscope.cn"}:
        segs = [_compact(part) for part in parsed.path.split("/") if part]
        platform_match = any(len(part) >= 4 and part in title_compact for part in segs[:3])

    path_parts = {part for part in re.split(r"[/_.-]+", parsed.path.lower()) if part}
    homepage = parsed.path in {"", "/"}
    non_brand = [tok for tok in matched if _compact(tok) not in {brand, "openai", "anthropic", "google", "github"}]
    if homepage and not non_brand:
        return None
    if not (known or brand_match or platform_match):
        return None

    score = 0
    reasons: list[str] = []
    if known:
        score += 55
        reasons.append("configured_official_domain")
    if brand_match:
        score += 50
        reasons.append("domain_matches_headline_brand")
    if platform_match:
        score += 42
        reasons.append("platform_owner_matches_headline")
    score += min(48, len(matched) * 16)
    if matched:
        reasons.append("event_tokens:" + ",".join(matched[:4]))
    if path_parts.intersection(SPECIFIC_PATH_TERMS):
        score += 12
    if homepage:
        score -= 25
    if len(label) < 3:
        score -= 6
    if score < 70 or (not matched and not platform_match):
        return None
    return _OfficialCandidate(url=url, label=label, host=host, score=score, matched_tokens=matched, reason=";".join(reasons))


def _candidates_from_links(
    item: FeedItem,
    links: list[dict[str, Any]],
    *,
    known_hosts: set[str],
    social_handles: dict[str, str],
) -> list[_OfficialCandidate]:
    found = [
        cand
        for row in links
        if isinstance(row, dict)
        if (cand := _score_candidate(item, row, known_hosts=known_hosts, social_handles=social_handles)) is not None
    ]
    found.sort(key=lambda c: (c.score, len(c.matched_tokens), len(c.label)), reverse=True)
    seen: set[str] = set()
    result: list[_OfficialCandidate] = []
    for cand in found:
        if cand.url in seen:
            continue
        seen.add(cand.url)
        result.append(cand)
    return result[:5]


def _secondary_media_links(item: FeedItem, links: list[dict[str, Any]]) -> list[str]:
    event_tokens = _event_tokens(item)
    seen: set[str] = set()
    result: list[str] = []
    item_host = url_host(item.link)
    for row in links:
        if not isinstance(row, dict):
            continue
        raw_url = str(row.get("url") or "").strip()
        if not raw_url.startswith(("http://", "https://")):
            continue
        url = canonical_url(raw_url)
        host = url_host(url)
        if not url or host == item_host:
            continue
        if not any(host == known or host.endswith("." + known) for known in TRUSTED_SECOND_HOP_MEDIA_HOSTS):
            continue
        label = clean_text(str(row.get("text") or row.get("title") or ""), 240).lower()
        blob = clean_text(f"{label} {urlparse(url).path.replace('/', ' ')}").lower()
        compact_blob = _compact(blob)
        matched = [tok for tok in event_tokens if tok in blob or _compact(tok) in compact_blob]
        if not any(len(_compact(tok)) >= 4 for tok in matched):
            continue
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result[:3]


def _coerce_article_dict(raw: Any, fallback_url: str) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    return {
        "status": getattr(raw, "status", "error"),
        "url": getattr(raw, "url", fallback_url),
        "final_url": getattr(raw, "final_url", fallback_url),
        "status_code": getattr(raw, "status_code", None),
        "title": getattr(raw, "title", ""),
        "description": getattr(raw, "description", ""),
        "text": getattr(raw, "text", ""),
        "facts": list(getattr(raw, "facts", None) or []),
        "links": list(getattr(raw, "links", None) or []),
        "images": list(getattr(raw, "images", None) or []),
        "published_at": getattr(raw, "published_at", None),
        "crawler": getattr(raw, "crawler", "requests"),
    }


def _article_matches_event(item: FeedItem, candidate: _OfficialCandidate, article: dict[str, Any]) -> bool:
    if article.get("status") != "ok":
        return False
    text = clean_text(
        " ".join(
            [
                str(article.get("title") or ""),
                str(article.get("description") or ""),
                str(article.get("text") or ""),
                *(str(f) for f in (article.get("facts") or [])),
            ]
        )
    ).lower()
    compact_text = _compact(text)
    event_tokens = _event_tokens(item)
    matched = [tok for tok in event_tokens if tok in text or _compact(tok) in compact_text]
    if len(matched) >= 2 or any(len(_compact(tok)) >= 5 for tok in matched):
        return True
    return candidate.score >= 100 and bool(candidate.matched_tokens) and bool(clean_text(str(article.get("title") or "")))


def _reference_record(
    item: FeedItem,
    candidate: _OfficialCandidate,
    article: dict[str, Any] | None,
    *,
    now: datetime,
    lookback_hours: int,
) -> dict[str, Any]:
    if article is None:
        social_dt = _x_status_published_at(candidate.url, now=now)
        social_label = _meaningful_social_label(candidate.label)
        freshness = "undated"
        if social_dt:
            freshness = "fresh" if social_dt >= now - timedelta(hours=lookback_hours) else "stale"
        return {
            "status": "matched_social",
            "url": candidate.url,
            "final_url": candidate.url,
            "title": social_label or item.title,
            "facts": [social_label] if social_label else [],
            "excerpt": social_label or item.title,
            "published_at": social_dt.isoformat() if social_dt else "",
            "published_at_provenance": "x_status_snowflake" if social_dt else "unavailable",
            "freshness": freshness,
            "domain": candidate.host,
            "link_text": candidate.label,
            "candidate_score": candidate.score,
            "match_reason": candidate.reason,
            "reliability": candidate.social_reliability or "official_social",
            "discovered_from": item.link,
        }

    published_at: datetime | None = article.get("published_at")
    freshness = "undated"
    if published_at:
        pub_utc = published_at.replace(tzinfo=UTC) if published_at.tzinfo is None else published_at.astimezone(UTC)
        freshness = "fresh" if pub_utc >= now - timedelta(hours=lookback_hours) else "stale"
    else:
        pub_utc = None
    facts = [clean_text(str(f)) for f in (article.get("facts") or []) if clean_text(str(f))][:8]
    excerpt = clean_text("；".join(facts) or str(article.get("description") or "") or str(article.get("text") or ""), 1800)
    final_url = str(article.get("final_url") or candidate.url)
    return {
        "status": "matched" if pub_utc else "matched_undated",
        "url": candidate.url,
        "final_url": final_url,
        "title": clean_text(str(article.get("title") or "") or candidate.label or item.title),
        "description": clean_text(str(article.get("description") or ""), 500),
        "facts": facts,
        "excerpt": excerpt,
        "published_at": pub_utc.isoformat() if pub_utc else "",
        "freshness": freshness,
        "domain": url_host(final_url),
        "link_text": candidate.label,
        "candidate_score": candidate.score,
        "match_reason": candidate.reason,
        "reliability": "official",
        "discovered_from": item.link,
        "crawler": str(article.get("crawler") or "requests"),
        "status_code": article.get("status_code"),
        "images": list(article.get("images") or [])[:5],
    }


def reconcile_official_sources(
    items: list[FeedItem],
    *,
    defaults: dict[str, Any] | None = None,
    sources: list[Source] | None = None,
    now: datetime | None = None,
    fetcher: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Bind media/aggregator FeedItems to 1st-hop or trusted-media 2nd-hop official pages & X posts."""
    cfg = defaults or {}
    lookback_hours = max(1, int(cfg.get("lookback_hours") or 24))
    max_items = max(0, int(cfg.get("official_reconciliation_max_items", 12)))
    timeout = max(5, int(cfg.get("timeout_seconds") or 15))
    user_agent = str(cfg.get("user_agent") or DEFAULT_USER_AGENT)
    ref_now = (now or now_utc())
    ref_now = ref_now.replace(tzinfo=UTC) if ref_now.tzinfo is None else ref_now.astimezone(UTC)

    known_hosts, social_handles = _resolve_official_registry(sources)

    def _fetch(target_url: str) -> dict[str, Any]:
        if fetcher is not None:
            return _coerce_article_dict(fetcher(target_url, user_agent=user_agent), target_url)
        return _fetch_article_http(target_url, user_agent=user_agent, timeout=timeout)

    candidates: list[tuple[FeedItem, list[_OfficialCandidate], list[str]]] = []
    for item in items:
        if item.source_reliability not in {"media", "aggregator"} and item.source_tier.upper() != "B":
            continue
        enrichment = item.raw.get("article_enrichment") if isinstance(item.raw, dict) else None
        raw_links = enrichment.get("links") if isinstance(enrichment, dict) else None
        if not isinstance(raw_links, list) or not raw_links:
            continue
        found = _candidates_from_links(item, raw_links, known_hosts=known_hosts, social_handles=social_handles)
        secondary = _secondary_media_links(item, raw_links)
        if found or secondary:
            candidates.append((item, found, secondary))

    candidates.sort(
        key=lambda row: (row[0].published_at or row[0].fetched_at, row[1][0].score if row[1] else 0),
        reverse=True,
    )

    attempted = matched = matched_undated = matched_social = stale = failed = 0
    secondary_attempted = secondary_resolved = 0
    secondary_budget = min(4, max_items) if max_items > 0 else 4

    for item, official_candidates, secondary_urls in candidates:
        if max_items > 0 and attempted >= max_items:
            break
        item.raw = dict(item.raw or {})
        existing = item.raw.get("official_reconciliation")
        if isinstance(existing, dict) and str(existing.get("status") or "").startswith("matched"):
            existing_status = str(existing.get("status") or "")
            provenance = str(existing.get("published_at_provenance") or "")
            if not (existing_status == "matched_social" and provenance not in {"x_status_snowflake", "unavailable"}):
                continue

        record: dict[str, Any] | None = None
        diagnostics: list[str] = []

        for cand in official_candidates[:3]:
            if cand.social_reliability:
                record = _reference_record(item, cand, None, now=ref_now, lookback_hours=lookback_hours)
                matched_social += 1
                break
            attempted += 1
            try:
                article = _fetch(cand.url)
            except Exception as exc:
                diagnostics.append(f"{cand.host}:{type(exc).__name__}")
                continue
            if not _article_matches_event(item, cand, article):
                diagnostics.append(f"{cand.host}:{article.get('status')}:event_mismatch")
                continue
            record = _reference_record(item, cand, article, now=ref_now, lookback_hours=lookback_hours)
            break

        if record is None:
            for secondary_url in secondary_urls[:2]:
                if secondary_attempted >= secondary_budget:
                    break
                secondary_attempted += 1
                sec_host = url_host(secondary_url)
                sec_cand = _OfficialCandidate(
                    url=secondary_url,
                    label="",
                    host=sec_host,
                    score=90,
                    matched_tokens=_event_tokens(item),
                    reason="trusted_original_media",
                )
                try:
                    sec_article = _fetch(secondary_url)
                except Exception as exc:
                    diagnostics.append(f"{sec_host}:{type(exc).__name__}:second_hop")
                    continue
                if not _article_matches_event(item, sec_cand, sec_article):
                    diagnostics.append(f"{sec_host}:{sec_article.get('status')}:second_hop_event_mismatch")
                    continue

                origin_record = _reference_record(item, sec_cand, sec_article, now=ref_now, lookback_hours=lookback_hours)
                origin_record["status"] = "matched_origin_media" if sec_article.get("published_at") else "matched_origin_media_undated"
                origin_record["reliability"] = "primary_media"
                origin_record["match_reason"] = "trusted_original_media"

                if origin_record.get("freshness") == "stale":
                    record = origin_record
                    secondary_resolved += 1
                    break

                nested_links = [r for r in (sec_article.get("links") or []) if isinstance(r, dict)]
                nested_cands = _candidates_from_links(item, nested_links, known_hosts=known_hosts, social_handles=social_handles)
                for cand in nested_cands[:3]:
                    if cand.social_reliability:
                        record = _reference_record(item, cand, None, now=ref_now, lookback_hours=lookback_hours)
                        matched_social += 1
                        break
                    attempted += 1
                    try:
                        article = _fetch(cand.url)
                    except Exception as exc:
                        diagnostics.append(f"{cand.host}:{type(exc).__name__}:nested_official")
                        continue
                    if not _article_matches_event(item, cand, article):
                        diagnostics.append(f"{cand.host}:{article.get('status')}:nested_event_mismatch")
                        continue
                    record = _reference_record(item, cand, article, now=ref_now, lookback_hours=lookback_hours)
                    break
                if record is None:
                    record = origin_record
                secondary_resolved += 1
                break

        if record is not None:
            item.raw["official_reconciliation"] = record
            matched += 1
            if record["status"] in {"matched_undated", "matched_origin_media_undated"}:
                matched_undated += 1
            if record.get("freshness") == "stale":
                stale += 1
        else:
            failed += 1
            item.raw["official_reconciliation"] = {
                "status": "no_verified_match",
                "attempted_urls": [*secondary_urls[:2], *(c.url for c in official_candidates[:3])],
                "diagnostics": diagnostics[-3:],
            }

    return {
        "status": "ok",
        "candidate_items": len(candidates),
        "attempted": attempted,
        "matched": matched,
        "matched_undated": matched_undated,
        "matched_social": matched_social,
        "stale": stale,
        "failed": failed,
        "secondary_attempted": secondary_attempted,
        "secondary_resolved": secondary_resolved,
    }

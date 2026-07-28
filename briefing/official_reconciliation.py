from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Any, Callable
from urllib.parse import urlparse, urlunparse

from .content_enrichment import ArticleResult, fetch_article
from .models import FeedItem, Source
from .util import clean_text

UTC = timezone.utc
X_SNOWFLAKE_EPOCH_MS = 1_288_834_974_657

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
class OfficialCandidate:
    url: str
    label: str
    host: str
    score: int
    matched_tokens: list[str]
    reason: str
    social_reliability: str = ""


def _host(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().split("@")[-1].split(":")[0].removeprefix("www.")
    except Exception:
        return ""


def _canonical_url(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    host = parsed.netloc.lower().removeprefix("www.")
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    return urlunparse(("https", host, path, "", parsed.query, ""))


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
    # A clickbait media headline may omit the actual model/product name while
    # the feed summary contains it. Keep the title first, then add only a short
    # summary window so long roundup articles cannot dominate reconciliation.
    for value in (clean_text(item.title), clean_text(item.summary, 600)):
        for raw in re.findall(r"[a-z][a-z0-9_.-]{2,}|[\u4e00-\u9fff]{2,}", value.lower()):
            token = raw.strip("-_.")
            if len(token) < 3 or token in EVENT_TOKEN_STOPWORDS or token in result:
                continue
            result.append(token)
    return result[:16]


def _known_official_hosts(sources: list[Source]) -> set[str]:
    hosts: set[str] = set()
    for source in sources:
        if not source.is_official or source.type == "agent_social":
            continue
        host = _host(source.url)
        if host and host not in GENERIC_PLATFORM_HOSTS and not host.endswith("nitter.net"):
            hosts.add(host)
    return hosts


def _known_social_handles(sources: list[Source]) -> dict[str, str]:
    handles: dict[str, str] = {}
    for source in sources:
        reliability = str(source.reliability or "").lower()
        if reliability not in {"official_social", "official_personnel", "official_staff"}:
            continue
        fragment = urlparse(source.url).fragment
        values = [value.strip().lstrip("@").lower() for value in fragment.split(",") if value.strip()]
        match = re.search(r"/(?:twitter/user/)?([A-Za-z0-9_]{1,32})(?:/rss)?$", urlparse(source.url).path)
        if match:
            values.append(match.group(1).lower())
        for handle in values:
            handles[handle] = reliability
    return handles


def _host_is_known_official(host: str, known_hosts: set[str]) -> bool:
    return any(host == known or host.endswith("." + known) for known in known_hosts)


def _social_identity(url: str) -> tuple[str, str] | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    if host not in {"x.com", "twitter.com", "mobile.twitter.com"}:
        return None
    match = re.search(r"/([A-Za-z0-9_]{1,32})/status/(\d{3,})", parsed.path)
    return (match.group(1).lower(), match.group(2)) if match else None


def _x_status_published_at(url: str, *, now: datetime) -> datetime | None:
    """从 X status Snowflake 提取真实发帖时间，避免借用转载媒体时间。"""
    identity = _social_identity(url)
    if not identity:
        return None
    try:
        status_id = int(identity[1])
        timestamp_ms = (status_id >> 22) + X_SNOWFLAKE_EPOCH_MS
        published_at = datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)
    except (OSError, OverflowError, ValueError):
        return None
    reference = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
    # 未来 Snowflake 通常是伪造或解析异常，按无日期处理，不能据此通过新鲜度门。
    if published_at > reference + timedelta(minutes=10):
        return None
    return published_at


def _meaningful_social_label(label: str) -> str:
    """过滤正文里仅写成平台域名的链接文字，防止卡片标题退化为 x.com。"""
    cleaned = clean_text(label, 240)
    normalized = cleaned.lower().strip().rstrip("/")
    if normalized in {"x", "x.com", "www.x.com", "twitter", "twitter.com", "www.twitter.com"}:
        return ""
    candidate_url = cleaned
    if re.match(r"^(?:www\.)?(?:x\.com|twitter\.com)/", cleaned, flags=re.I):
        candidate_url = "https://" + cleaned
    if _social_identity(candidate_url):
        return ""
    return cleaned


def _score_candidate(
    item: FeedItem,
    link: dict[str, Any],
    *,
    known_hosts: set[str],
    social_handles: dict[str, str],
) -> OfficialCandidate | None:
    url = _canonical_url(str(link.get("url") or ""))
    if not url or url == _canonical_url(item.link):
        return None
    host = _host(url)
    if not host or host == _host(item.link):
        return None
    label = clean_text(str(link.get("text") or link.get("title") or ""), 180)
    identity = _social_identity(url)
    if identity:
        reliability = social_handles.get(identity[0], "")
        if not reliability:
            return None
        return OfficialCandidate(
            url=url,
            label=label,
            host=host,
            score=120,
            matched_tokens=[identity[0]],
            reason="configured_official_x_account",
            social_reliability=reliability,
        )

    title_compact = _compact(item.title)
    brand = _domain_brand(host)
    known = _host_is_known_official(host, known_hosts)
    brand_match = len(brand) >= 4 and brand in title_compact
    parsed = urlparse(url)
    candidate_blob = clean_text(" ".join([label, parsed.path.replace("/", " "), parsed.query])).lower()
    candidate_compact = _compact(candidate_blob)
    event_tokens = _event_tokens(item)
    matched = [token for token in event_tokens if token in candidate_blob or _compact(token) in candidate_compact]
    platform_match = False
    if host in {"github.com", "huggingface.co", "modelscope.cn"}:
        path_segments = [_compact(part) for part in parsed.path.split("/") if part]
        platform_match = any(len(part) >= 4 and part in title_compact for part in path_segments[:3])

    path_parts = {part for part in re.split(r"[/_.-]+", parsed.path.lower()) if part}
    homepage = parsed.path in {"", "/"}
    non_brand_matches = [token for token in matched if _compact(token) not in {brand, "openai", "anthropic", "google", "github"}]
    if homepage and not non_brand_matches:
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
    return OfficialCandidate(url=url, label=label, host=host, score=score, matched_tokens=matched, reason=";".join(reasons))


def official_link_candidates(item: FeedItem, sources: list[Source]) -> list[OfficialCandidate]:
    enrichment = item.raw.get("article_enrichment") if isinstance(item.raw, dict) else None
    links = enrichment.get("links") if isinstance(enrichment, dict) else None
    if not isinstance(links, list):
        return []
    return _official_candidates_from_links(item, sources, links)


def _official_candidates_from_links(
    item: FeedItem,
    sources: list[Source],
    links: list[dict[str, Any]],
) -> list[OfficialCandidate]:
    known_hosts = _known_official_hosts(sources)
    social_handles = _known_social_handles(sources)
    candidates = [
        candidate
        for row in links
        if isinstance(row, dict)
        for candidate in [_score_candidate(item, row, known_hosts=known_hosts, social_handles=social_handles)]
        if candidate is not None
    ]
    candidates.sort(key=lambda candidate: (candidate.score, len(candidate.matched_tokens), len(candidate.label)), reverse=True)
    seen: set[str] = set()
    result: list[OfficialCandidate] = []
    for candidate in candidates:
        if candidate.url in seen:
            continue
        seen.add(candidate.url)
        result.append(candidate)
    return result[:5]


def _secondary_media_links(item: FeedItem) -> list[str]:
    enrichment = item.raw.get("article_enrichment") if isinstance(item.raw, dict) else None
    links = enrichment.get("links") if isinstance(enrichment, dict) else None
    if not isinstance(links, list):
        return []
    event_tokens = _event_tokens(item)
    seen: set[str] = set()
    result: list[str] = []
    for row in links:
        if not isinstance(row, dict):
            continue
        url = _canonical_url(str(row.get("url") or ""))
        host = _host(url)
        if not url or host == _host(item.link):
            continue
        if not any(host == known or host.endswith("." + known) for known in TRUSTED_SECOND_HOP_MEDIA_HOSTS):
            continue
        label = clean_text(str(row.get("text") or row.get("title") or ""), 240).lower()
        parsed = urlparse(url)
        blob = clean_text(f"{label} {parsed.path.replace('/', ' ')}").lower()
        compact = _compact(blob)
        matched = [token for token in event_tokens if token in blob or _compact(token) in compact]
        if not any(len(_compact(token)) >= 4 for token in matched):
            continue
        if url in seen:
            continue
        seen.add(url)
        result.append(url)
    return result[:3]


def _article_matches_event(item: FeedItem, candidate: OfficialCandidate, article: ArticleResult) -> bool:
    if article.status != "ok":
        return False
    text = clean_text(" ".join([article.title, article.description, article.text, *(article.facts or [])])).lower()
    compact = _compact(text)
    event_tokens = _event_tokens(item)
    matched = [token for token in event_tokens if token in text or _compact(token) in compact]
    distinctive = [token for token in matched if len(_compact(token)) >= 5]
    if len(matched) >= 2 or distinctive:
        return True
    # The outbound link itself may carry the only product label on a terse
    # changelog page.  Require a high-confidence, event-specific candidate.
    return candidate.score >= 100 and bool(candidate.matched_tokens) and bool(clean_text(article.title))


def _reference_record(
    item: FeedItem,
    candidate: OfficialCandidate,
    article: ArticleResult | None,
    *,
    now: datetime,
    lookback_hours: int,
) -> dict[str, Any]:
    if article is None:
        # 社交平台链接未抓正文时，唯一可验证的发布时间来自 status Snowflake；
        # 媒体文章的时间只能描述转载，绝不能冒充官方原帖时间。
        social_published_at = _x_status_published_at(candidate.url, now=now)
        social_label = _meaningful_social_label(candidate.label)
        freshness = "undated"
        if social_published_at:
            freshness = "fresh" if social_published_at >= now - timedelta(hours=lookback_hours) else "stale"
        return {
            "status": "matched_social",
            "url": candidate.url,
            "final_url": candidate.url,
            "title": social_label or item.title,
            "facts": [social_label] if social_label else [],
            "excerpt": social_label or item.title,
            "published_at": social_published_at.isoformat() if social_published_at else "",
            "published_at_provenance": "x_status_snowflake" if social_published_at else "unavailable",
            "freshness": freshness,
            "domain": candidate.host,
            "link_text": candidate.label,
            "candidate_score": candidate.score,
            "match_reason": candidate.reason,
            "reliability": candidate.social_reliability or "official_social",
            "discovered_from": item.link,
        }
    published_at = article.published_at
    freshness = "undated"
    if published_at:
        freshness = "fresh" if published_at >= now - timedelta(hours=lookback_hours) else "stale"
    facts = [clean_text(fact) for fact in article.facts or [] if clean_text(fact)][:8]
    excerpt = clean_text("；".join(facts) or article.description or article.text, 1800)
    return {
        "status": "matched" if published_at else "matched_undated",
        "url": candidate.url,
        "final_url": article.final_url or candidate.url,
        "title": clean_text(article.title or candidate.label or item.title),
        "description": clean_text(article.description, 500),
        "facts": facts,
        "excerpt": excerpt,
        "published_at": published_at.isoformat() if published_at else "",
        "freshness": freshness,
        "domain": _host(article.final_url or candidate.url),
        "link_text": candidate.label,
        "candidate_score": candidate.score,
        "match_reason": candidate.reason,
        "reliability": "official",
        "discovered_from": item.link,
        "crawler": article.crawler,
        "status_code": article.status_code,
        "images": article.images[:5],
    }


def _origin_media_reference_record(
    item: FeedItem,
    url: str,
    article: ArticleResult,
    *,
    now: datetime,
    lookback_hours: int,
) -> dict[str, Any]:
    """Bind a translated/reposted lead to the trusted report it cites.

    This is intentionally not promoted to an official source.  Its main job is
    to recover the original publication time so a fresh repost cannot make an
    old event pass the rolling-news window.
    """
    candidate = OfficialCandidate(
        url=url,
        label=clean_text(article.title or item.title, 240),
        host=_host(article.final_url or url),
        score=90,
        matched_tokens=_event_tokens(item),
        reason="trusted_original_media",
    )
    record = _reference_record(item, candidate, article, now=now, lookback_hours=lookback_hours)
    record["status"] = "matched_origin_media" if article.published_at else "matched_origin_media_undated"
    record["reliability"] = "primary_media"
    record["match_reason"] = "trusted_original_media"
    return record


def reconcile_official_sources(
    items: list[FeedItem],
    sources: list[Source],
    *,
    user_agent: str | None = None,
    lookback_hours: int = 24,
    max_items: int = 12,
    now: datetime | None = None,
    fetcher: Callable[..., ArticleResult] = fetch_article,
) -> dict[str, int | str]:
    """Follow event-specific outbound links and bind media leads to first party.

    At most one canonical official reference is kept per media lead.  This is
    deliberately bounded: the wide source collection discovers events, while
    only final high-value candidates pay for an extra first-party fetch.
    """
    now = (now or datetime.now(UTC)).astimezone(UTC)
    candidates: list[tuple[FeedItem, list[OfficialCandidate], list[str]]] = []
    for item in items:
        if item.source_reliability not in {"media", "aggregator"} and item.source_tier.upper() != "B":
            continue
        found = official_link_candidates(item, sources)
        # Keep trusted original-media links even when the article also contains
        # a plausible official URL.  The official candidate may concern a
        # different fact mentioned by a clickbait headline and fail matching;
        # the original report is then still needed to recover the event date.
        secondary = _secondary_media_links(item)
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
            time_provenance = str(existing.get("published_at_provenance") or "")
            # 旧版 matched_social 曾把媒体时间写成官方帖时间；同日重跑时只刷新
            # 这类无版本标记的缓存，避免已修正记录或网页补证失去幂等性。
            legacy_social_time = existing_status == "matched_social" and time_provenance not in {
                "x_status_snowflake",
                "unavailable",
            }
            if not legacy_social_time:
                continue
        record: dict[str, Any] | None = None
        diagnostics: list[str] = []
        for candidate in official_candidates[:3]:
            if candidate.social_reliability:
                record = _reference_record(item, candidate, None, now=now, lookback_hours=lookback_hours)
                matched_social += 1
                break
            attempted += 1
            try:
                article = fetcher(
                    candidate.url,
                    user_agent=user_agent,
                    use_crawl4ai=False,
                    auto_repair=False,
                    capture_screenshot=False,
                )
            except Exception as exc:
                diagnostics.append(f"{candidate.host}:{type(exc).__name__}")
                continue
            if not _article_matches_event(item, candidate, article):
                diagnostics.append(f"{candidate.host}:{article.status}:event_mismatch")
                continue
            record = _reference_record(item, candidate, article, now=now, lookback_hours=lookback_hours)
            break
        # A clickbait or translated article may contain an unrelated official
        # link (for example a model safety PDF) as well as the actual original
        # report.  Only falling back when *no* official candidate existed lets
        # that unrelated link suppress stale-event detection.  Try trusted
        # media after official candidates fail, and retain its real date even
        # when it has no further first-party link.
        if record is None:
            for secondary_url in secondary_urls[:2]:
                if secondary_attempted >= secondary_budget:
                    break
                secondary_attempted += 1
                secondary_candidate = OfficialCandidate(
                    url=secondary_url,
                    label="",
                    host=_host(secondary_url),
                    score=90,
                    matched_tokens=_event_tokens(item),
                    reason="trusted_original_media",
                )
                try:
                    secondary_article = fetcher(
                        secondary_url,
                        user_agent=user_agent,
                        use_crawl4ai=False,
                        auto_repair=False,
                        capture_screenshot=False,
                    )
                except Exception as exc:
                    diagnostics.append(f"{_host(secondary_url)}:{type(exc).__name__}:second_hop")
                    continue
                if not _article_matches_event(item, secondary_candidate, secondary_article):
                    diagnostics.append(f"{_host(secondary_url)}:{secondary_article.status}:second_hop_event_mismatch")
                    continue

                origin_record = _origin_media_reference_record(
                    item,
                    secondary_url,
                    secondary_article,
                    now=now,
                    lookback_hours=lookback_hours,
                )
                # The directly cited report is authoritative for when this
                # reported event entered the public record.  Once that date is
                # already outside the window, background links inside the
                # report must not freshen it again.
                if origin_record.get("freshness") == "stale":
                    record = origin_record
                    secondary_resolved += 1
                    break

                nested_links = [row for row in secondary_article.links or [] if isinstance(row, dict)]
                nested_candidates = _official_candidates_from_links(item, sources, nested_links)
                for candidate in nested_candidates[:3]:
                    if candidate.social_reliability:
                        record = _reference_record(item, candidate, None, now=now, lookback_hours=lookback_hours)
                        matched_social += 1
                        break
                    attempted += 1
                    try:
                        article = fetcher(
                            candidate.url,
                            user_agent=user_agent,
                            use_crawl4ai=False,
                            auto_repair=False,
                            capture_screenshot=False,
                        )
                    except Exception as exc:
                        diagnostics.append(f"{candidate.host}:{type(exc).__name__}:nested_official")
                        continue
                    if not _article_matches_event(item, candidate, article):
                        diagnostics.append(f"{candidate.host}:{article.status}:nested_event_mismatch")
                        continue
                    record = _reference_record(item, candidate, article, now=now, lookback_hours=lookback_hours)
                    break
                if record is None:
                    record = origin_record
                secondary_resolved += 1
                break
        if record:
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
                "attempted_urls": [*secondary_urls[:2], *[candidate.url for candidate in official_candidates[:3]]],
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

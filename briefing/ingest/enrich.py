from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import json
import mimetypes
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from ..core import (
    MODEL_TOKEN_RE,
    UTC,
    FeedItem,
    clean_text,
    detect_entity,
    ensure_dir,
    extract_hf_model_id,
    extract_model_names,
    is_ai_relevant,
    is_mainstream_ai,
    is_x_url,
    model_family_key,
    now_utc,
    parse_date,
    stable_hash,
    url_host,
)
from .browser import browser_channel

DEFAULT_USER_AGENT = "DailyBilibiliBriefing/0.1 (+local; article enrichment)"
ARTICLE_TIMEOUT_SECONDS = 18
ARTICLE_TEXT_LIMIT = 9000
SUMMARY_LIMIT = 1800
ARTICLE_SOURCE_DATE_CONFLICT_HOURS = 12
DEFAULT_IMAGE_LIMIT = 5

EVENT_TERMS = (
    "发布", "推出", "上线", "开放", "开源", "新增", "升级", "更新", "接入", "可用",
    "release", "launch", "introduc", "announce", "available", "open source", "update",
)
NOISE_TERMS = (
    "融资", "股价", "招聘", "课程", "峰会", "会议议程", "信用卡", "人才观",
    "roundup", "weekly digest", "每日早报", "一周回顾",
)
SKIP_URL_EXTENSIONS = {
    ".zip", ".gz", ".tar", ".tgz", ".7z", ".rar",
    ".mp4", ".m4s", ".mp3", ".wav", ".exe", ".dmg", ".pdf",
}
SPA_THIN_MARKERS = (
    "loading", "loading...", "加载中", "请稍候",
    "enable javascript", "javascript is required", "app-root", "__vite",
)

FACT_KEYWORDS = (
    "发布", "上线", "推出", "更新", "新增", "修复", "API", "价格", "免费", "参数",
    "上下文", "多模态", "Agent", "智能体", "benchmark", "榜单", "评测", "性能",
    "开源", "权重", "TokenHub", "Hugging Face", "ModelScope", "GitHub", "license",
    "download", "downloads", "tokens", "context", "降低", "提升", "成功率", "延迟",
    "时长", "工作流", "MCP", "离职", "辞职", "任命", "接任", "重组", "并入",
    "负责人", "主管", "诉讼", "起诉", "收购", "监管",
)
HIGH_SIGNAL_FACT_KEYWORDS = (
    "离职", "辞职", "任命", "接任", "重组", "并入", "诉讼", "起诉", "收购", "监管",
)
FACT_ROLE_KEYWORDS = ("负责人", "主管")

ROUNDUP_CONTAMINATION_MARKERS = (
    "](", "相关阅读", "延伸阅读", "更多新闻", "上一篇", "下一篇",
    "热门文章", "量子位的朋友们", "更多精彩内容", "阅读完需", "本文来自微信公众号",
)

URL_TRAILING_PUNCTUATION = ".,;:!?，。；：！？、…”’》〉」』"
URL_CLOSING_PAIRS = {")": "(", "]": "[", "}": "{", "）": "（", "】": "【"}

_POLICY_DOC_TERMS = ("行动方案", "指导意见", "管理办法", "实施方案", "监管规则", "国家标准")
_POLICY_OWNER_PATTERNS = (
    r"(?:国务院|最高人民法院|最高人民检察院)",
    r"(?:中国|国家|中央)[\u4e00-\u9fff]{0,16}(?:委员会|局|办公室|法院|检察院|监管机构)",
    r"(?:科技|教育|工业和信息化|工信|公安|民政|司法|财政|自然资源|生态环境|交通运输|水利|农业农村|商务|应急管理|审计)部",
    r"(?:中央宣传部|中央组织部|中央统战部|中央社会工作部)",
    r"[\u4e00-\u9fff]{2,12}(?:省|市|自治区|自治州|县|区)(?:人民)?政府",
)
_POLICY_PERIOD_RE = re.compile(r"20\d{2}\s*[—–-]\s*20\d{2}|未来\s*[一二三四五六七八九十百\d]+\s*年|(?:到|至|截至)\s*20\d{2}\s*年")
_POLICY_TASK_RE = re.compile(r"[一二三四五六七八九十百\d]+\s*(?:项|方面)\s*(?:重点|主要|具体)?任务|(?:重点|主要|具体)?任务\s*(?:包括|涵盖|围绕|聚焦)")


def _is_concrete_public_ai_policy(text: str) -> bool:
    cleaned = clean_text(text).lower()
    if not any(term in cleaned for term in ("人工智能", "ai+", "ai +", "大模型", "智能监测", "智能预警")):
        return False
    if not any(term in cleaned for term in _POLICY_DOC_TERMS):
        return False
    if not (_POLICY_PERIOD_RE.search(cleaned) and _POLICY_TASK_RE.search(cleaned)):
        return False
    return any(
        re.search(rf"(?:{owner})[^。；;]{{0,24}}(?:发布|印发|出台|施行)", cleaned)
        for owner in _POLICY_OWNER_PATTERNS
    )


def _has_skip_extension(url: str) -> bool:
    path = urlparse(str(url or "")).path.lower()
    return any(path.endswith(ext) for ext in SKIP_URL_EXTENSIONS)


def _is_high_volume_artifact(item: FeedItem) -> bool:
    sid = item.source_id.lower()
    return sid.endswith("_hf_models") or sid.startswith("arxiv_")


def should_enrich_item(item: FeedItem) -> bool:
    link = str(item.link or "")
    if item.source_id.startswith("x_") or is_x_url(link):
        return False
    if not link.startswith(("http://", "https://")) or _has_skip_extension(link):
        return False
    haystack = f"{item.title} {item.summary} {item.source_name} {item.source_id} {link}"
    if item.source_tier.upper() == "A" or item.source_reliability == "official":
        return is_mainstream_ai(haystack) or any(
            tok in haystack.lower() for tok in ("hf_models", "github", "modelscope", "hugging")
        )
    if is_mainstream_ai(haystack):
        return True
    relevant, _ = is_ai_relevant(item.title, item.summary)
    return relevant and (
        item.source_tier.upper() in {"B", "C"}
        or item.source_reliability in {"media", "community", "aggregator"}
    )


def _enrichment_bucket_key(item: FeedItem) -> tuple[str, str]:
    entity = detect_entity(f"{item.title} {item.summary}", source_id=item.source_id)
    if item.source_id.endswith("_hf_models"):
        hf_id = str((item.raw or {}).get("model_id") or "") or extract_hf_model_id(f"{item.title} {item.link}")
        fam = model_family_key(hf_id)
        if fam:
            return f"{entity}:hf:{fam}", entity
    models = extract_model_names(item.title)
    title_low = clean_text(item.title).lower()
    org_event = any(term in title_low for term in ("离职", "辞职", "卸任", "任命", "接任", "重组", "resign", "depart"))
    if models and not org_event:
        return f"{entity}:model:{models[0].lower()}", entity
    tokens = [
        tok
        for tok in re.findall(r"[a-zA-Z][a-zA-Z0-9_.-]{2,}|[\u4e00-\u9fff]{2,}", title_low)
        if tok not in {"the", "and", "for", "with", "new", "release", "ai", "llm", "model", "发布", "推出", "上线", "更新"}
    ]
    stem = "-".join(tokens[:5]) if tokens else stable_hash([item.title], length=12)
    return f"{entity}:{stem}", entity


def plan_enrichment_items(
    items: list[FeedItem],
    *,
    defaults: dict[str, Any] | None = None,
) -> list[FeedItem]:
    """Rank and diversify eligible FeedItems within the crawl budget."""
    cfg = defaults or {}
    reference_now = now_utc()
    max_items = int(cfg.get("content_enrichment_max_items") or 32)
    target_stories = int(cfg.get("selection_target_items") or cfg.get("selection_max_items") or 10) or 10
    multiplier = max(1, int(cfg.get("content_enrichment_candidate_multiplier") or 5))
    max_per_entity = int(cfg.get("content_enrichment_candidate_max_per_entity") or 8)
    max_per_source = int(cfg.get("content_enrichment_candidate_max_per_source") or 8)
    max_per_domain = int(cfg.get("content_enrichment_per_domain_limit") or 6)
    max_per_hv_source = int(cfg.get("content_enrichment_max_per_high_volume_source") or (1 if 0 < max_items <= 40 else 0))

    desired = max(1, target_stories) * multiplier
    if max_items > 0:
        desired = min(desired, max_items)

    eligible = [it for it in items if should_enrich_item(it)]
    buckets: dict[str, list[FeedItem]] = {}
    bucket_entities: dict[str, str] = {}
    for item in eligible:
        key, entity = _enrichment_bucket_key(item)
        buckets.setdefault(key, []).append(item)
        bucket_entities[key] = entity

    scored_buckets: list[tuple[float, float, str, str, list[FeedItem]]] = []
    for key, group in buckets.items():
        entity = bucket_entities[key]
        latest = max((it.published_at for it in group if it.published_at), default=None)
        if latest and latest.astimezone(UTC) > reference_now + timedelta(hours=2):
            continue
        blob = clean_text(" ".join([*(it.title for it in group[:4]), *(it.summary for it in group[:2])])).lower()
        public_policy = _is_concrete_public_ai_policy(blob)
        if any(term in blob for term in NOISE_TERMS) and not public_policy:
            continue

        score = 0.0
        official = sum(1 for it in group if it.source_tier.upper() == "A" or it.source_reliability == "official")
        media = sum(1 for it in group if it.source_tier.upper() == "B" or it.source_reliability == "media")
        domains = {url_host(it.link) for it in group if url_host(it.link)}
        if official:
            score += 32
        if media:
            score += min(12, media * 4)
        if len(domains) >= 2:
            score += min(18, len(domains) * 5)
        if any(term in blob for term in EVENT_TERMS):
            score += 20
        if MODEL_TOKEN_RE.search(blob) or re.search(r"\bv?\d+(?:\.\d+){1,4}\b", blob):
            score += 18
        if latest is not None:
            score += 16
        if entity not in {"AI", "论文 / arXiv"}:
            score += 8
        if public_policy:
            score += 80
        if all(_is_high_volume_artifact(it) for it in group):
            score -= 10
        if score <= 0:
            continue
        scored_buckets.append((score, latest.timestamp() if latest else 0.0, key, entity, group))

    scored_buckets.sort(key=lambda row: (-row[0], -row[1], row[2]))

    selected: list[FeedItem] = []
    seen_keys: set[str] = set()
    entity_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    domain_counts: dict[str, int] = {}
    hv_counts: dict[str, int] = {}

    for _score, _ts, _bkey, entity, group in scored_buckets:
        if len(selected) >= desired:
            break
        generic_entity = entity in {"AI", "论文 / arXiv"}
        if not generic_entity and max_per_entity > 0 and entity_counts.get(entity, 0) >= max_per_entity:
            continue
        reps = sorted(
            group,
            key=lambda it: (
                int(it.published_at is not None),
                int(it.source_tier.upper() == "A" or it.source_reliability == "official"),
                int(it.source_tier.upper() == "B" or it.source_reliability == "media"),
                it.published_at.timestamp() if it.published_at else 0.0,
                it.guid or it.link,
            ),
            reverse=True,
        )
        rep_domains: set[str] = set()
        kept_in_cluster = 0
        for item in reps:
            if len(selected) >= desired or kept_in_cluster >= 2:
                break
            dom = url_host(item.link)
            if kept_in_cluster > 0 and dom and dom in rep_domains:
                continue
            ukey = item.guid or item.link
            if not ukey or ukey in seen_keys:
                continue
            if max_per_source > 0 and source_counts.get(item.source_id, 0) >= max_per_source:
                continue
            if max_per_domain > 0 and dom and domain_counts.get(dom, 0) >= max_per_domain:
                continue
            if _is_high_volume_artifact(item) and max_per_hv_source > 0 and hv_counts.get(item.source_id, 0) >= max_per_hv_source:
                continue
            seen_keys.add(ukey)
            if dom:
                rep_domains.add(dom)
                domain_counts[dom] = domain_counts.get(dom, 0) + 1
            source_counts[item.source_id] = source_counts.get(item.source_id, 0) + 1
            if _is_high_volume_artifact(item):
                hv_counts[item.source_id] = hv_counts.get(item.source_id, 0) + 1
            if not generic_entity:
                entity_counts[entity] = entity_counts.get(entity, 0) + 1
            selected.append(item)
            kept_in_cluster += 1

    return selected


def _meta_contents(html_text: str, names: Iterable[str]) -> list[str]:
    values: list[str] = []
    for name in names:
        escaped = re.escape(name)
        patterns = (
            rf'<meta[^>]+(?:name|property)=["\']{escaped}["\'][^>]+content=["\']([^"\']+)["\']',
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:name|property)=["\']{escaped}["\']',
        )
        for pattern in patterns:
            for match in re.finditer(pattern, html_text, flags=re.I | re.S):
                val = clean_text(match.group(1))
                if val and val not in values:
                    values.append(val)
    return values


def _meta_content(html_text: str, names: Iterable[str]) -> str:
    vals = _meta_contents(html_text, names)
    return vals[0] if vals else ""


def _json_ld_values(html_text: str) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for match in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html_text or "",
        flags=re.I | re.S,
    ):
        raw = html.unescape(match.group(1)).strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        stack = list(data) if isinstance(data, list) else [data]
        while stack:
            node = stack.pop(0)
            if isinstance(node, list):
                stack.extend(node)
                continue
            if not isinstance(node, dict):
                continue
            for key in ("headline", "name", "description", "articleBody", "datePublished", "image"):
                if key in node and key not in values:
                    values[key] = node[key]
            for child_key in ("@graph", "mainEntity", "itemListElement"):
                child = node.get(child_key)
                if isinstance(child, list):
                    stack.extend(child)
                elif isinstance(child, dict):
                    stack.append(child)
    return values


def _earliest_published_at(html_text: str, json_ld: dict[str, Any]) -> datetime | None:
    json_ld_published = parse_date(str(json_ld.get("datePublished") or ""))
    raw_candidates = _meta_contents(
        html_text,
        ("article:published_time", "article:published", "pubdate", "publishdate", "publish_date", "datePublished"),
    )
    inferred_tz = None
    for raw in raw_candidates:
        try:
            explicit = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if explicit.tzinfo is not None:
            inferred_tz = explicit.tzinfo
            break

    candidates: list[datetime] = [json_ld_published] if json_ld_published else []
    for raw in raw_candidates:
        parsed = None
        if inferred_tz is not None:
            try:
                local_val = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                local_val = None
            if local_val is not None and local_val.tzinfo is None:
                parsed = local_val.replace(tzinfo=inferred_tz).astimezone(UTC)
        parsed = parsed or parse_date(raw)
        if parsed and parsed not in candidates:
            candidates.append(parsed)
    return min(candidates) if candidates else None


def _normalise_extracted_url(raw_url: str) -> str:
    candidate = html.unescape(str(raw_url or "")).strip()
    while candidate:
        trailing = candidate[-1]
        if trailing in URL_TRAILING_PUNCTUATION:
            candidate = candidate[:-1]
            continue
        opener = URL_CLOSING_PAIRS.get(trailing)
        if opener and candidate.count(trailing) > candidate.count(opener):
            candidate = candidate[:-1]
            continue
        break
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return parsed._replace(fragment="").geturl()


def _bare_url_label(line: str, start: int, url: str) -> str:
    prefix = line[max(0, start - 120) : start]
    prefix = re.split(r"[。！？!?；;，,\r\n]", prefix)[-1]
    prefix = re.sub(r"(?:\[\d+\]|\(\d+\))\s*$", "", prefix)
    label = clean_text(prefix, 120).strip(" \t:：-—–[]()（）")
    return label or urlparse(url).netloc


def _extract_links(html_text: str, base_url: str, body_lines: Iterable[str], limit: int = 80) -> list[dict[str, str]]:
    max_links = max(1, int(limit))
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in re.finditer(r"(?is)<a\b[^>]*?href\s*=\s*([\"'])(.*?)\1[^>]*>(.*?)</a>", html_text or ""):
        href = html.unescape(match.group(2)).strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
            continue
        resolved = urljoin(base_url, href) if base_url else href
        parsed = urlparse(resolved)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        url = parsed._replace(fragment="").geturl()
        if url in seen:
            continue
        label_html = re.sub(r"(?is)<(?:script|style|svg)\b.*?</(?:script|style|svg)>", " ", match.group(3))
        label = clean_text(html.unescape(re.sub(r"(?s)<[^>]+>", " ", label_html)), 180)
        seen.add(url)
        links.append({"url": url, "text": label})
        if len(links) >= max_links:
            break

    bare_links: list[dict[str, str]] = []
    for line in body_lines:
        for match in re.finditer(r"https?://[^\s<>\"'`]+", line, flags=re.I):
            url = _normalise_extracted_url(match.group(0))
            if not url or url in seen:
                continue
            seen.add(url)
            bare_links.append({"url": url, "text": _bare_url_label(line, match.start(), url)})
            if len(bare_links) >= max_links:
                break
        if len(bare_links) >= max_links:
            break

    if len(links) + len(bare_links) <= max_links:
        return [*links, *bare_links]
    kept_bare = bare_links[:max_links]
    return [*links[: max_links - len(kept_bare)], *kept_bare]


def _extract_images(html_text: str, base_url: str, title: str, limit: int = DEFAULT_IMAGE_LIMIT) -> list[dict[str, str]]:
    images: list[dict[str, str]] = []
    seen: set[str] = set()

    def _add(raw_src: str, alt: str = "") -> None:
        src = html.unescape(str(raw_src or "")).strip()
        if not src or src.startswith("data:"):
            return
        resolved = urljoin(base_url, src) if base_url else src
        parsed = urlparse(resolved)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return
        if resolved in seen:
            return
        seen.add(resolved)
        images.append({"url": resolved, "alt": clean_text(alt, 120)})

    meta_img = _meta_content(html_text, ("og:image", "twitter:image", "image"))
    if meta_img:
        _add(meta_img, title)

    for match in re.finditer(r"<img\b([^>]*)>", html_text or "", flags=re.I | re.S):
        if len(images) >= limit:
            break
        attrs = match.group(1) or ""
        src_match = re.search(r"\b(?:src|data-src|data-original)=[\"']([^\"']+)[\"']", attrs, flags=re.I)
        if not src_match:
            continue
        alt_match = re.search(r"\balt=[\"']([^\"']*)[\"']", attrs, flags=re.I)
        _add(src_match.group(1), alt_match.group(1) if alt_match else "")

    return images[:limit]


def _is_noisy_fact_sentence(sentence: str) -> bool:
    lowered = sentence.lower()
    if len(sentence) > 220 or lowered.startswith(("http://", "https://")):
        return True
    url_count = lowered.count("http://") + lowered.count("https://")
    if url_count > 1 or (url_count and len(sentence) > 120):
        return True
    if any(marker.lower() in lowered for marker in ROUNDUP_CONTAMINATION_MARKERS):
        return True
    if re.search(r"###\s*step", lowered):
        return True
    date_hits = len(re.findall(r"20\d{2}[-/年]\d{1,2}(?:[-/月]\d{1,2})?", sentence))
    linkish_hits = len(re.findall(r"(?:https?://|www\.|\]\(|!\[)", lowered))
    return date_hits >= 2 or linkish_hits >= 2


def _extract_fact_sentences(paragraphs: Iterable[str], limit: int = 8) -> list[str]:
    sentences: list[str] = []
    for raw_line in paragraphs:
        for sub_line in re.split(r"[\r\n]+", str(raw_line or "")):
            line = clean_text(sub_line)
            if not line:
                continue
            sentences.extend(
                part
                for part in re.split(r"(?<=[。！？!?])\s*|(?<!\d)\.(?=\s+|$)", line)
                if clean_text(part)
            )
    scored: list[tuple[int, str]] = []
    for raw_sentence in sentences:
        s = clean_text(raw_sentence).strip("。；; ")
        if len(s) < 12 or _is_noisy_fact_sentence(s):
            continue
        lowered = s.lower()
        score = sum(3 for kw in FACT_KEYWORDS if kw.lower() in lowered)
        score += sum(9 for kw in HIGH_SIGNAL_FACT_KEYWORDS if kw.lower() in lowered)
        if any(kw.lower() in lowered for kw in HIGH_SIGNAL_FACT_KEYWORDS) and any(
            role.lower() in lowered for role in FACT_ROLE_KEYWORDS
        ):
            score += 6
        if any(ch.isdigit() for ch in s):
            score += 2
        if is_mainstream_ai(s):
            score += 2
        if score <= 0:
            continue
        scored.append((score, s))

    scored.sort(key=lambda row: (row[0], len(row[1])), reverse=True)
    facts: list[str] = []
    seen: set[str] = set()
    for _score, sentence in scored:
        norm = re.sub(r"\W+", "", sentence.lower())
        if not norm or norm in seen:
            continue
        seen.add(norm)
        facts.append(sentence)
        if len(facts) >= limit:
            break
    return facts


def extract_article_text(html_text: str, *, base_url: str = "") -> dict[str, Any]:
    """Extract clean article metadata, body paragraphs, fact sentences, outbound links, and images."""
    raw_html = html_text or ""
    json_ld = _json_ld_values(raw_html)
    meta_title = _meta_content(raw_html, ("og:title", "twitter:title"))
    if not meta_title:
        title_match = re.search(r"<title[^>]*>(.*?)</title>", raw_html, flags=re.I | re.S)
        meta_title = clean_text(title_match.group(1)) if title_match else ""
    title = clean_text(str(json_ld.get("headline") or json_ld.get("name") or meta_title))
    description = clean_text(
        str(json_ld.get("description") or _meta_content(raw_html, ("description", "og:description", "twitter:description")))
    )
    published_at = _earliest_published_at(raw_html, json_ld)

    paragraphs: list[str] = []
    article_body = str(json_ld.get("articleBody") or "").strip()
    if article_body and len(clean_text(article_body)) >= 80:
        for chunk in re.split(r"[\r\n]+", article_body):
            cleaned = clean_text(chunk)
            if cleaned:
                paragraphs.append(cleaned)
    else:
        body = re.sub(r"(?is)<(script|style|noscript|svg|canvas|iframe)\b.*?</\1>", " ", raw_html)
        body = re.sub(r"(?i)</(p|div|section|article|li|h1|h2|h3|br)>", "\n", body)
        body = html.unescape(re.sub(r"(?is)<[^>]+>", " ", body))
        seen_norms: set[str] = set()
        total_chars = 0
        for raw_line in body.splitlines():
            line = clean_text(raw_line)
            if len(line) < 12:
                continue
            if any(noise in line.lower() for noise in ("cookie", "javascript", "copyright", "隐私", "登录", "注册")):
                continue
            norm = re.sub(r"\W+", "", line.lower())
            if not norm or norm in seen_norms:
                continue
            seen_norms.add(norm)
            paragraphs.append(line)
            total_chars += len(line)
            if total_chars >= ARTICLE_TEXT_LIMIT:
                break

    text = "\n".join(paragraphs)[:ARTICLE_TEXT_LIMIT]
    facts = _extract_fact_sentences([title, description, *paragraphs])
    links = _extract_links(raw_html, base_url, [description, *paragraphs])
    images = _extract_images(raw_html, base_url, title)
    return {
        "title": title,
        "description": description,
        "published_at": published_at,
        "paragraphs": paragraphs,
        "text": text,
        "facts": facts,
        "links": links,
        "images": images,
    }


def _download_images(
    images: list[dict[str, str]],
    asset_dir: Path | None,
    *,
    user_agent: str,
    limit: int = DEFAULT_IMAGE_LIMIT,
) -> list[dict[str, str]]:
    if not asset_dir:
        return images[:limit]
    ensure_dir(asset_dir)
    headers = {
        "User-Agent": user_agent,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    saved: list[dict[str, str]] = []
    seen: set[str] = set()
    for idx, img in enumerate(images):
        url = str(img.get("url") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        rec = dict(img)
        try:
            with requests.get(url, headers=headers, timeout=12, stream=True) as resp:
                ctype = resp.headers.get("content-type", "")
                if resp.status_code < 400 and (
                    "image" in ctype or urlparse(url).path.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif"))
                ):
                    chunks: list[bytes] = []
                    received = 0
                    for chunk in resp.iter_content(chunk_size=65536):
                        if not chunk:
                            continue
                        received += len(chunk)
                        if received > 6_000_000:
                            chunks = []
                            rec["error"] = "image exceeded 6MB"
                            break
                        chunks.append(chunk)
                    if chunks:
                        ext = Path(urlparse(url).path).suffix.lower()
                        if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
                            guessed = mimetypes.guess_extension(ctype.split(";")[0].strip()) if ctype else ""
                            ext = guessed if guessed in {".jpg", ".jpeg", ".png", ".webp", ".gif"} else ".jpg"
                        out_path = asset_dir / f"image-{idx + 1:02d}{ext}"
                        out_path.write_bytes(b"".join(chunks))
                        rec["path"] = str(out_path)
                        rec["content_type"] = ctype
        except Exception as exc:
            rec["error"] = f"{type(exc).__name__}: {exc}"[:200]
        saved.append(rec)
        if len(saved) >= limit:
            break
    return saved


def _fetch_article_http(
    url: str,
    *,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: int = ARTICLE_TIMEOUT_SECONDS,
    asset_dir: Path | None = None,
) -> dict[str, Any]:
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
    except requests.RequestException as exc:
        return {
            "status": "error",
            "url": url,
            "final_url": "",
            "status_code": None,
            "title": "",
            "description": "",
            "text": "",
            "facts": [],
            "links": [],
            "images": [],
            "screenshot_path": "",
            "published_at": None,
            "crawler": "requests",
            "error": f"{type(exc).__name__}: {exc}"[:300],
        }

    ctype = resp.headers.get("content-type", "")
    if resp.status_code >= 400:
        return {
            "status": "http_error",
            "url": url,
            "final_url": resp.url,
            "status_code": resp.status_code,
            "title": "",
            "description": "",
            "text": "",
            "facts": [],
            "links": [],
            "images": [],
            "screenshot_path": "",
            "published_at": None,
            "crawler": "requests",
            "error": resp.text[:200],
        }
    if "text/html" not in ctype and "application/xhtml" not in ctype and not resp.text.lstrip().startswith("<"):
        return {
            "status": "unsupported_content",
            "url": url,
            "final_url": resp.url,
            "status_code": resp.status_code,
            "title": "",
            "description": "",
            "text": "",
            "facts": [],
            "links": [],
            "images": [],
            "screenshot_path": "",
            "published_at": None,
            "crawler": "requests",
            "error": ctype[:120],
        }
    encoding = getattr(resp, "encoding", None)
    if encoding is None or str(encoding).lower() == "iso-8859-1":
        resp.encoding = getattr(resp, "apparent_encoding", None) or "utf-8"

    parsed = extract_article_text(resp.text, base_url=resp.url or url)
    text = parsed["text"]
    if not text and (parsed["title"] or parsed["description"]):
        text = clean_text("\n".join(x for x in (parsed["title"], parsed["description"]) if x), ARTICLE_TEXT_LIMIT)
    images = _download_images(parsed["images"], asset_dir, user_agent=user_agent)
    status = "ok" if (text or parsed["description"] or parsed["facts"]) else "empty"
    return {
        "status": status,
        "url": url,
        "final_url": resp.url or url,
        "status_code": resp.status_code,
        "title": parsed["title"],
        "description": parsed["description"],
        "text": text,
        "facts": parsed["facts"],
        "links": parsed["links"],
        "images": images,
        "screenshot_path": "",
        "published_at": parsed["published_at"],
        "crawler": "requests",
        "error": "",
    }


def _needs_crawl4ai(url: str, article: dict[str, Any]) -> bool:
    host = url_host(article.get("final_url") or url)
    if host.endswith("news.google.com") or article.get("status_code") in {401, 403, 404, 410, 429}:
        return False
    text = clean_text(f"{article.get('title', '')} {article.get('description', '')} {article.get('text', '')}")
    if article.get("status") == "ok" and (len(text) >= 800 or (len(text) >= 400 and len(article.get("facts") or []) >= 2)):
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in SPA_THIN_MARKERS) or (article.get("status") in {"empty", "error"} and not text)


async def _crawl4ai_fetch(
    url: str,
    *,
    user_agent: str,
    timeout: int,
    asset_dir: Path | None,
    capture_screenshot: bool,
) -> dict[str, Any]:
    import crawl4ai.async_webcrawler as async_webcrawler_module
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
    from crawl4ai.async_database import async_db_manager
    from crawl4ai.content_filter_strategy import PruningContentFilter
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

    channel = browser_channel()
    browser_cfg = BrowserConfig(
        headless=True,
        browser_type="chromium",
        chrome_channel=channel,
        channel=channel,
        user_agent=user_agent,
        viewport_width=1360,
        viewport_height=900,
        device_scale_factor=1.0,
        verbose=False,
    )
    md_gen = DefaultMarkdownGenerator(content_filter=PruningContentFilter(threshold=0.35, threshold_type="fixed"))
    run_cfg = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        markdown_generator=md_gen,
        screenshot=capture_screenshot,
        scan_full_page=True,
        scroll_delay=0.15,
        wait_until="domcontentloaded",
        delay_before_return_html=0.8,
        page_timeout=max(5, timeout) * 1000,
        verbose=False,
    )

    class _AllowRobots:
        async def can_fetch(self, _url: str, _ua: str = "*") -> bool:
            return True

    orig_robots = async_webcrawler_module.RobotsParser
    async_webcrawler_module.RobotsParser = _AllowRobots
    try:
        async with AsyncWebCrawler(config=browser_cfg) as crawler:
            result = await crawler.arun(url=url, config=run_cfg)
    finally:
        async_webcrawler_module.RobotsParser = orig_robots
        await async_db_manager.cleanup()

    if not getattr(result, "success", False):
        raise RuntimeError(str(getattr(result, "error_message", "crawl4ai failed")))

    final_url = str(getattr(result, "redirected_url", "") or getattr(result, "url", "") or url)
    html_text = str(getattr(result, "html", "") or "")
    parsed = extract_article_text(html_text, base_url=final_url)

    md_obj = getattr(result, "markdown", None)
    md_text = ""
    if isinstance(md_obj, str):
        md_text = md_obj
    elif md_obj is not None:
        for attr in ("fit_markdown", "markdown_with_citations", "raw_markdown"):
            val = getattr(md_obj, attr, None)
            if val:
                md_text = str(val)
                break
    text = clean_text(md_text, ARTICLE_TEXT_LIMIT) if len(clean_text(md_text)) > len(clean_text(parsed["text"])) else parsed["text"]
    facts = _extract_fact_sentences([parsed["title"], parsed["description"], text]) or parsed["facts"]
    images = _download_images(parsed["images"], asset_dir, user_agent=user_agent)

    screenshot_path = ""
    b64_shot = str(getattr(result, "screenshot", "") or "")
    if b64_shot and asset_dir:
        try:
            ensure_dir(asset_dir)
            shot_file = asset_dir / "page.png"
            shot_file.write_bytes(base64.b64decode(b64_shot.split(",", 1)[-1]))
            screenshot_path = str(shot_file)
        except Exception:
            screenshot_path = ""

    status_code = getattr(result, "status_code", None) or getattr(result, "redirected_status_code", None)
    return {
        "status": "ok" if (text or parsed["description"] or facts) else "empty",
        "url": url,
        "final_url": final_url,
        "status_code": int(status_code) if status_code else None,
        "title": parsed["title"],
        "description": parsed["description"],
        "text": text,
        "facts": facts,
        "links": parsed["links"],
        "images": images,
        "screenshot_path": screenshot_path,
        "published_at": parsed["published_at"],
        "crawler": "crawl4ai",
        "error": "",
    }


def _fetch_article(
    url: str,
    *,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: int = ARTICLE_TIMEOUT_SECONDS,
    use_crawl4ai: bool = True,
    asset_dir: Path | None = None,
    capture_screenshot: bool = False,
) -> dict[str, Any]:
    http_res = _fetch_article_http(url, user_agent=user_agent, timeout=timeout, asset_dir=asset_dir)
    if not use_crawl4ai or not _needs_crawl4ai(url, http_res):
        return http_res
    try:
        import crawl4ai as _c4a  # noqa: F401
    except ImportError:
        return http_res
    try:
        browser_res = asyncio.run(
            asyncio.wait_for(
                _crawl4ai_fetch(
                    url,
                    user_agent=user_agent,
                    timeout=timeout,
                    asset_dir=asset_dir,
                    capture_screenshot=capture_screenshot,
                ),
                timeout=max(12, timeout + 8),
            )
        )
        if browser_res["status"] == "ok" and len(browser_res["text"]) >= len(http_res["text"]):
            return browser_res
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        http_res["error"] = f"{err}; http={http_res['error']}".strip("; ")[:500]
    return http_res


def _apply_article_published_at(item: FeedItem, published_at: datetime) -> None:
    item.raw = dict(item.raw or {})
    article_time = published_at.replace(tzinfo=UTC) if published_at.tzinfo is None else published_at.astimezone(UTC)
    item.raw["article_published_at"] = article_time.isoformat()
    item.raw["article_published_at_provenance"] = "article_metadata"
    if item.published_at is None:
        if item.raw.get("kind") != "official_web_discovery":
            item.published_at = article_time
            item.raw["published_at_provenance"] = "article_metadata"
        return

    source_time = item.published_at.replace(tzinfo=UTC) if item.published_at.tzinfo is None else item.published_at.astimezone(UTC)
    if article_time <= source_time - timedelta(hours=ARTICLE_SOURCE_DATE_CONFLICT_HOURS):
        item.raw["source_published_at"] = source_time.isoformat()
        item.raw["published_at_conflict_hours"] = round((source_time - article_time).total_seconds() / 3600, 3)
        item.published_at = article_time
        item.raw["published_at_provenance"] = "article_metadata_overrode_source"
        return
    item.raw.setdefault("published_at_provenance", "source")


def _build_evidence_excerpts(item: FeedItem, article: dict[str, Any]) -> tuple[str, str, list[dict[str, Any]]]:
    content = clean_text(article.get("text") or article.get("description") or "")
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    unique_key = f"{item.source_id}:{item.guid or item.link or item.title}"
    doc_id = stable_hash([unique_key, str(article.get("final_url") or article.get("url") or ""), content_hash], length=32)
    excerpts: list[dict[str, Any]] = []
    for idx, raw_fact in enumerate(article.get("facts") or []):
        fact = clean_text(str(raw_fact))
        if len(fact) < 12:
            continue
        fact_hash = hashlib.sha256(fact.encode("utf-8")).hexdigest()
        excerpts.append(
            {
                "excerpt_id": stable_hash([doc_id, fact_hash], length=32),
                "text": fact,
                "score": max(0, 100 - idx * 3),
            }
        )
        if len(excerpts) >= 8:
            break
    return doc_id, content_hash, excerpts


def enrich_feed_items(
    items: list[FeedItem],
    *,
    defaults: dict[str, Any] | None = None,
    asset_dir: Path | None = None,
    progress_sink: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Fetch article pages for FeedItems, attaching `article_enrichment` and `evidence_excerpts`."""
    cfg = defaults or {}
    max_items = max(0, int(cfg.get("content_enrichment_max_items") or 32))
    per_domain_limit = max(0, int(cfg.get("content_enrichment_per_domain_limit") or 6))
    timeout = max(5, int(cfg.get("timeout_seconds") or ARTICLE_TIMEOUT_SECONDS))
    user_agent = str(cfg.get("user_agent") or DEFAULT_USER_AGENT)
    use_crawl4ai = bool(cfg.get("content_enrichment_use_crawl4ai", True))
    capture_assets = bool(cfg.get("content_enrichment_capture_assets", False))

    candidates = [it for it in items if should_enrich_item(it)]
    candidates.sort(
        key=lambda it: (
            int(is_mainstream_ai(f"{it.title} {it.summary} {it.source_name}")),
            int(it.published_at is not None),
            int(it.source_tier.upper() == "A" or it.source_reliability == "official"),
            int(it.source_tier.upper() == "B" or it.source_reliability == "media"),
            -int(_is_high_volume_artifact(it)),
            it.published_at.isoformat() if it.published_at else "",
        ),
        reverse=True,
    )

    attempted = ok = failed = skipped = crawl4ai_ok = requests_ok = image_count = screenshot_count = 0
    domain_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}

    for item in candidates:
        if max_items > 0 and attempted >= max_items:
            skipped += 1
            continue
        dom = url_host(item.link)
        if per_domain_limit > 0 and dom and domain_counts.get(dom, 0) >= per_domain_limit:
            skipped += 1
            continue
        source_cap = 1 if _is_high_volume_artifact(item) and 0 < max_items <= 40 else max_items
        if source_cap > 0 and source_counts.get(item.source_id, 0) >= source_cap:
            skipped += 1
            continue

        if dom:
            domain_counts[dom] = domain_counts.get(dom, 0) + 1
        source_counts[item.source_id] = source_counts.get(item.source_id, 0) + 1
        attempted += 1

        if progress_sink is not None:
            progress_sink({"attempted": attempted, "ok": ok, "failed": failed, "skipped": skipped, "current_url": item.link})

        item_asset_dir = None
        if asset_dir is not None and capture_assets:
            item_asset_dir = ensure_dir(asset_dir / stable_hash([item.source_id, item.guid, item.link, item.title]))

        article = _fetch_article(
            item.link,
            user_agent=user_agent,
            timeout=timeout,
            use_crawl4ai=use_crawl4ai,
            asset_dir=item_asset_dir,
            capture_screenshot=capture_assets,
        )

        published_dt: datetime | None = article.get("published_at")
        item.raw = dict(item.raw or {})
        item.raw["article_enrichment"] = {
            "status": article["status"],
            "url": article["url"],
            "final_url": article["final_url"],
            "status_code": article["status_code"],
            "title": article["title"],
            "facts": list(article["facts"]),
            "images": list(article["images"][:DEFAULT_IMAGE_LIMIT]),
            "links": list(article["links"][:80]),
            "screenshot_path": article["screenshot_path"],
            "crawler": article["crawler"],
            "error": str(article["error"])[:500],
            "published_at": published_dt.isoformat() if published_dt else "",
            "published_at_provenance": "article_metadata" if published_dt else "",
            "fetched_at": now_utc().isoformat(),
        }

        image_count += len([img for img in article["images"] if img.get("path") or img.get("url")])
        if article["screenshot_path"]:
            screenshot_count += 1
        if article["status"] == "ok" and article["crawler"] == "crawl4ai":
            crawl4ai_ok += 1
        elif article["status"] == "ok" and article["crawler"] == "requests":
            requests_ok += 1

        if article["status"] == "ok" and (article["facts"] or article["description"] or article["text"]):
            doc_id, content_hash, excerpts = _build_evidence_excerpts(item, article)
            item.raw["document_id"] = doc_id
            item.raw["document_content_hash"] = content_hash
            item.raw["evidence_excerpts"] = excerpts

            pieces = [clean_text(item.summary)]
            for fact in article["facts"]:
                if fact and fact not in pieces:
                    pieces.append(fact)
            if len(pieces) == 1 and article["description"]:
                pieces.append(article["description"])
            item.summary = clean_text("；".join(p for p in pieces if p), SUMMARY_LIMIT)

            if published_dt is not None:
                _apply_article_published_at(item, published_dt)
            ok += 1
        else:
            failed += 1

    skipped += max(0, len(items) - len(candidates))
    return {
        "attempted": attempted,
        "ok": ok,
        "failed": failed,
        "skipped": skipped,
        "crawl4ai_ok": crawl4ai_ok,
        "requests_fallback_ok": requests_ok,
        "images": image_count,
        "screenshots": screenshot_count,
    }

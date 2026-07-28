from __future__ import annotations

import concurrent.futures as cf
import html as html_lib
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urldefrag, urlencode, urljoin, urlparse

import requests

from .github_release import enrich_github_release_items
from .config import project_root
from .models import FeedItem, Source, SourceHealth
from .util import clean_text, parse_date, stable_hash

UTC = timezone.utc

LOCAL_TIME_AS_GMT_SOURCE_IDS = {"infoq_cn"}
GOOGLE_NEWS_SEARCH_URL = "https://news.google.com/rss/search"


WEB_DISCOVERY_TYPES = {"web", "web_discovery", "official_site"}
SITEMAP_TYPES = {"sitemap", "official_sitemap"}
AGENT_SOCIAL_TYPE = "agent_social"
WEB_DISCOVERY_ITEM_LIMIT = 12
WEB_DISCOVERY_RENDER_TIMEOUT_MS = 18_000
WEB_DISCOVERY_GENERIC_LABELS = {
    "\u4e86\u89e3\u66f4\u591a",
    "\u67e5\u770b\u66f4\u591a",
    "\u67e5\u770b\u5168\u90e8",
    "\u9605\u8bfb\u539f\u6587",
    "\u9605\u8bfb\u66f4\u591a",
    "\u6587\u7ae0",
    "learn more",
    "read more",
    "more details",
    "this documentation",
    "\u7acb\u5373\u8bd5\u7528",
    "\u8bd5\u7528",
    "\u767b\u5f55",
    "login",
    "sign in",
    "signup",
    "sign up",
    "\u6ce8\u518c",
    "\u4e2d\u6587",
    "en",
    "\u6a21\u578b",
    "\u7814\u7a76",
    "\u4ea7\u54c1",
    "pricing",
    "docs",
    "blog",
    "news",
    "release notes",
    "announcement",
    "announcements",
    "release",
    "developers",
    "research",
    "models",
    "model",
    "products",
    "product",
    "company",
    "safety",
    "policy",
    "research index",
    "careers",
    "career",
    "jobs",
    "join us",
    "view jobs",
    "open roles",
    "\u5f00\u59cb\u5bf9\u8bdd",
    "\u6280\u672f\u535a\u5ba2",
    "\u8054\u7cfb\u6211\u4eec",
    "\u7528\u6237\u534f\u8bae",
    "\u9690\u79c1\u653f\u7b56",
    "\u670d\u52a1\u6761\u6b3e",
    "\u6a21\u578b\u53d1\u5e03",
    "\u7814\u7a76\u6210\u679c",
    "\u53d1\u5e03",
    "\u4e0a\u7ebf",
    "\u66f4\u65b0",
    "contact",
    "contact us",
    "\u62db\u8058",
    "\u52a0\u5165\u6211\u4eec",
    "\u9690\u79c1",
    "\u6761\u6b3e",
    "skip to main content",
    "skip to content",
    "skip navigation",
    "main content",
    "\u8df3\u5230\u4e3b\u8981\u5185\u5bb9",
    "\u8df3\u81f3\u4e3b\u8981\u5185\u5bb9",
}
WEB_DISCOVERY_EVENT_KEYWORDS = [
    "\u53d1\u5e03",
    "\u4e0a\u7ebf",
    "\u63a8\u51fa",
    "\u66f4\u65b0",
    "release",
    "launch",
    "announc",
    "pricing",
    "\u4ef7\u683c",
    "benchmark",
    "\u8bc4\u6d4b",
    "\u699c\u5355",
    "\u5f00\u6e90",
    "open source",
    "agent",
    "\u667a\u80fd\u4f53",
    "context",
    "\u4e0a\u4e0b\u6587",
]
WEB_DISCOVERY_AI_KEYWORDS = [
    "openai",
    "chatgpt",
    "gpt",
    "sora",
    "anthropic",
    "claude",
    "gemini",
    "deepmind",
    "gemma",
    "deepseek",
    "qwen",
    "\u901a\u4e49",
    "\u5343\u95ee",
    "kimi",
    "moonshot",
    "\u8c46\u5305",
    "doubao",
    "seed",
    "seedance",
    "seedream",
    "seed3d",
    "zhipu",
    "\u667a\u8c31",
    "glm",
    "minimax",
    "mistral",
    "llama",
    "meta ai",
    "xai",
    "grok",
    "hunyuan",
    "\u6df7\u5143",
    "hy",
]
WEB_DISCOVERY_DATE_PATTERN = (
    r"20\d{2}[-/.\u5e74]\d{1,2}[-/.\u6708]\d{1,2}"
    r"|(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
    r"sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2},\s+20\d{2}"
)



def _tag_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(el: ET.Element, names: Iterable[str]) -> str:
    wanted = {n.lower() for n in names}
    for child in list(el):
        name = _tag_name(child.tag)
        if name in wanted:
            if name == "link" and child.attrib.get("href"):
                return child.attrib.get("href", "")
            return "".join(child.itertext()) or child.attrib.get("href", "") or ""
    return ""


def _all_items(root: ET.Element) -> list[ET.Element]:
    items: list[ET.Element] = []
    for el in root.iter():
        if _tag_name(el.tag) in {"item", "entry"}:
            items.append(el)
    return items


def _regex_tag(chunk: str, tag: str) -> str:
    m = re.search(fr"<[^>/]*{re.escape(tag)}[^>]*>(.*?)</[^>]*{re.escape(tag)}>", chunk, re.I | re.S)
    if m:
        return m.group(1)
    if tag == "link":
        m = re.search(r"<link[^>]+href=['\"]([^'\"]+)", chunk, re.I)
        if m:
            return m.group(1)
    return ""


def _canonical_social_link(source: Source, link: str) -> str | None:
    if not source.id.startswith("x_"):
        return link
    match = re.search(r"(?i)https?://[^/]+/([A-Za-z0-9_]+)/status/(\d+)", link)
    if not match:
        return None
    expected = re.search(r"(?i)/(?:twitter/user/)?([A-Za-z0-9_]+)/rss(?:$|[?#])", source.url)
    if not expected:
        expected = re.search(r"(?i)/twitter/user/([A-Za-z0-9_]+)(?:$|[?#])", source.url)
    if not expected or match.group(1).lower() != expected.group(1).lower():
        return None
    return f"https://x.com/{match.group(1)}/status/{match.group(2)}"


def _parse_chunk(chunk: str, source: Source, final_url: str) -> FeedItem | None:
    title = clean_text(_regex_tag(chunk, "title"), 240)
    raw_link = clean_text(_regex_tag(chunk, "link"))
    link = _canonical_social_link(source, raw_link)
    if link is None:
        return None
    guid = clean_text(_regex_tag(chunk, "guid") or _regex_tag(chunk, "id")) or link or stable_hash([source.id, title])
    summary = clean_text(_regex_tag(chunk, "description") or _regex_tag(chunk, "summary"), 1200)
    date = _normalize_published_at(source, parse_date(_regex_tag(chunk, "pubDate") or _regex_tag(chunk, "published") or _regex_tag(chunk, "updated")))
    return FeedItem(source.id, source.name, source.tier, source.reliability, title, link or final_url, guid, summary, date)


def _normalize_published_at(source: Source, published: datetime | None) -> datetime | None:
    if not published:
        return None
    if source.id in LOCAL_TIME_AS_GMT_SOURCE_IDS:
        # These feeds stamp Beijing local time while labelling it GMT, so every
        # timestamp is inflated by 8h — not only the ones that land in the future.
        # Correcting unconditionally keeps day-old articles from re-entering the
        # freshness window looking 8h younger than they are.
        return published - timedelta(hours=8)
    return published


def parse_feed(text: str, source: Source, final_url: str = "") -> list[FeedItem]:
    start = text.find("<")
    if start > 0:
        text = text[start:]
    try:
        root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError:
        chunks = re.findall(r"<item\b.*?</item>|<entry\b.*?</entry>", text, re.I | re.S)
        return [x for x in (_parse_chunk(ch, source, final_url) for ch in chunks) if x is not None and x.title]
    result: list[FeedItem] = []
    for el in _all_items(root):
        title = clean_text(_child_text(el, ["title"]), 240)
        discovery_link = _child_text(el, ["link"]).strip()
        link = _canonical_social_link(source, discovery_link)
        if link is None:
            continue
        guid = clean_text(_child_text(el, ["guid", "id"])) or link or stable_hash([source.id, title])
        summary = clean_text(_child_text(el, ["description", "summary", "content", "encoded"]), 1200)
        published = _normalize_published_at(source, parse_date(_child_text(el, ["pubDate", "published", "updated", "date"])))
        if not title:
            continue
        result.append(
            FeedItem(
                source_id=source.id,
                source_name=source.name,
                source_tier=source.tier,
                source_reliability=source.reliability,
                title=title,
                link=link or final_url or source.url,
                guid=guid,
                summary=summary,
                published_at=published,
                fetched_at=datetime.now(UTC),
                raw={"final_url": final_url, "discovery_url": discovery_link},
            )
        )
    return result


def parse_hf_models(text: str, source: Source, final_url: str = "") -> list[FeedItem]:
    """Parse Hugging Face model list API into FeedItem rows.

    Hugging Face does not expose a stable RSS feed per organization. The public
    model-list API is a better official signal for model repository updates:
    `lastModified` becomes the freshness timestamp, and the model card URL is
    the evidence link.
    """
    data = json.loads(text)
    if not isinstance(data, list):
        return []
    result: list[FeedItem] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        model_id = str(row.get("modelId") or row.get("id") or "").strip()
        if not model_id:
            continue
        last_modified = parse_date(str(row.get("lastModified") or row.get("createdAt") or ""))
        tags = row.get("tags") or []
        if not isinstance(tags, list):
            tags = []
        siblings = row.get("siblings") or []
        summary_bits = []
        pipeline_tag = row.get("pipeline_tag") or row.get("pipelineTag")
        if pipeline_tag:
            summary_bits.append(f"pipeline={pipeline_tag}")
        if tags:
            summary_bits.append("tags=" + ", ".join(str(x) for x in tags[:8]))
        if row.get("downloads") is not None:
            summary_bits.append(f"downloads={row.get('downloads')}")
        if row.get("likes") is not None:
            summary_bits.append(f"likes={row.get('likes')}")
        if isinstance(siblings, list) and siblings:
            names = []
            for sib in siblings[:4]:
                if isinstance(sib, dict) and sib.get("rfilename"):
                    names.append(str(sib["rfilename"]))
            if names:
                summary_bits.append("files=" + ", ".join(names))
        title = f"Hugging Face 模型仓库更新：{model_id}"
        link = f"https://huggingface.co/{model_id}"
        guid = f"{model_id}:{last_modified.isoformat() if last_modified else row.get('sha') or ''}"
        result.append(
            FeedItem(
                source_id=source.id,
                source_name=source.name,
                source_tier=source.tier,
                source_reliability=source.reliability,
                title=title,
                link=link,
                guid=guid,
                summary=clean_text("；".join(summary_bits), 1200),
                published_at=last_modified,
                fetched_at=datetime.now(UTC),
                raw={"final_url": final_url, "kind": "hf_models", "model_id": model_id},
            )
        )
    return result


def _google_news_locale(source: Source) -> tuple[str, str, str]:
    if source.region.lower() in {"cn", "zh", "china"}:
        return "zh-CN", "CN", "CN:zh-Hans"
    return "en-US", "US", "US:en"


def _google_news_query(query: str, lookback_hours: int | None = None) -> str:
    query = clean_text(query)
    if re.search(r"\b(when:\d+[hdwmy]|after:\d{4}-\d{2}-\d{2}|before:\d{4}-\d{2}-\d{2})\b", query, re.I):
        return query
    hours = max(1, int(lookback_hours or 24))
    if hours <= 100:
        return f"{query} when:{hours}h"
    since = datetime.now(UTC) - timedelta(hours=hours)
    return f"{query} after:{since:%Y-%m-%d}"


def _google_news_url(source: Source, lookback_hours: int | None = None) -> str:
    language, country, ceid = _google_news_locale(source)
    params = {
        "q": _google_news_query(source.url, lookback_hours),
        "hl": language,
        "gl": country,
        "ceid": ceid,
    }
    return GOOGLE_NEWS_SEARCH_URL + "?" + urlencode(params)


def _source_child(el: ET.Element) -> tuple[str, str]:
    for child in list(el):
        if _tag_name(child.tag) == "source":
            return clean_text("".join(child.itertext()), 120), str(child.attrib.get("url") or "")
    return "", ""


def parse_google_news(text: str, source: Source, final_url: str = "") -> list[FeedItem]:
    start = text.find("<")
    if start > 0:
        text = text[start:]
    try:
        root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError:
        return parse_feed(text, source, final_url)
    result: list[FeedItem] = []
    for el in _all_items(root):
        title = clean_text(_child_text(el, ["title"]), 240)
        link = _child_text(el, ["link"]).strip()
        guid = clean_text(_child_text(el, ["guid", "id"])) or link or stable_hash([source.id, title])
        summary = clean_text(_child_text(el, ["description", "summary", "content", "encoded"]), 1200)
        published = _normalize_published_at(source, parse_date(_child_text(el, ["pubDate", "published", "updated", "date"])))
        publisher, publisher_url = _source_child(el)
        if not title or not link or not published:
            continue
        result.append(
            FeedItem(
                source_id=source.id,
                source_name=publisher or source.name,
                source_tier=source.tier,
                source_reliability=source.reliability,
                title=title,
                link=link,
                guid=guid,
                summary=summary,
                published_at=published,
                fetched_at=datetime.now(UTC),
                raw={"final_url": final_url, "kind": "google_news", "query": source.url, "publisher": publisher, "publisher_url": publisher_url},
            )
        )
    return result


def _netloc(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def _same_site(base_url: str, candidate_url: str) -> bool:
    base = _netloc(base_url)
    candidate = _netloc(candidate_url)
    if not base or not candidate:
        return False
    return candidate == base or candidate.endswith("." + base) or base.endswith("." + candidate)


def _html_link_records(html_text: str, base_url: str) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for match in re.finditer(r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", html_text, flags=re.I | re.S):
        attrs = match.group("attrs") or ""
        href_match = re.search(r"href=[\"']([^\"']+)[\"']", attrs, flags=re.I)
        if not href_match:
            continue
        href = urljoin(base_url, href_match.group(1).strip())
        text = _strip_html_text(match.group("body") or "", 500)
        context = _html_link_context(html_text, match.start(), match.end(), text)
        records.append({"href": href, "text": text, "context": context})
    return records


def _html_link_context(html_text: str, start: int, end: int, own_text: str) -> str:
    """Return nearby card/paragraph context for generic links.

    Many official sites use blue links whose visible text is only "Learn more",
    a generic docs label, or "this documentation". The surrounding card/section is the real
    news signal, so static HTML parsing should keep that context too.
    """
    left = max(0, start - 5000)
    right = min(len(html_text), end + 5000)
    window = html_text[left:right]
    relative_start = start - left
    own = clean_text(own_text)
    candidates: list[tuple[int, int, str]] = []
    tag_priority = {
        "article": 70,
        "li": 60,
        "section": 54,
        "tr": 48,
        "div": 42,
        "td": 34,
        "p": 12,
    }
    semantic_attr_pattern = re.compile(
        r"(?:card|post|article|news|blog|item|entry|changelog|release|update|announcement)",
        flags=re.I,
    )
    for tag, base_score in tag_priority.items():
        open_pattern = re.compile(fr"<{tag}\b(?P<attrs>[^>]*)>", flags=re.I | re.S)
        close_pattern = re.compile(fr"</{tag}>", flags=re.I)
        for open_match in reversed(list(open_pattern.finditer(window[:relative_start]))):
            close_match = close_pattern.search(window[relative_start:])
            block_end = relative_start + close_match.end() if close_match else min(len(window), relative_start + 1800)
            context_html = window[open_match.start() : block_end]
            context = _strip_html_text(context_html, 1400)
            if not context or (own and len(context) <= len(own) + 8):
                continue
            score = base_score
            attrs = open_match.group("attrs") or ""
            if semantic_attr_pattern.search(attrs):
                score += 28
            if _line_looks_like_date(context):
                score += 18
            if _has_model_version_signal(context):
                score += 16
            lowered = context.lower()
            if any(keyword.lower() in lowered for keyword in WEB_DISCOVERY_AI_KEYWORDS):
                score += 10
            if any(keyword.lower() in lowered for keyword in WEB_DISCOVERY_EVENT_KEYWORDS):
                score += 10
            line_count = len([line for line in re.split(r"[\n\r]+", context) if clean_text(line)])
            if line_count >= 3:
                score += 14
            elif line_count >= 2:
                score += 8
            if 40 <= len(context) <= 900:
                score += 8
            elif len(context) > 1200:
                score -= 25
            distance = max(0, relative_start - open_match.end())
            score += max(0, 24 - distance // 220)
            candidates.append((score, -distance, context))
    if candidates:
        candidates.sort(reverse=True, key=lambda item: (item[0], item[1], len(item[2])))
        return candidates[0][2]
    context = _strip_html_text(window, 1400)
    if own and len(context) <= len(own) + 8:
        return ""
    return context


def _strip_html_text(value: str, limit: int | None = None) -> str:
    text = re.sub(r"(?is)<(script|style|svg|noscript)\b.*?</\1>", " ", value or "")
    text = re.sub(r"(?i)</(p|li|h1|h2|h3|h4|h5|h6|br)>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = text.replace("\u200b", " ").replace("\ufeff", " ").replace("\u2060", " ")
    text = re.sub(r"(?<=\s)\?(?=\s|$)", " ", text)
    lines = [clean_text(line) for line in re.split(r"[\n\r]+", html_lib.unescape(text))]
    cleaned = "\n".join(line for line in lines if line)
    if limit and len(cleaned) > limit:
        return cleaned[: limit - 1].rstrip() + "…"
    return cleaned


def _attr_value(attrs: str, name: str) -> str:
    match = re.search(fr"\b{re.escape(name)}=[\"']([^\"']+)[\"']", attrs or "", flags=re.I)
    return html_lib.unescape(match.group(1).strip()) if match else ""


def _docusaurus_changelog_records(html_text: str, base_url: str) -> list[dict[str, str]]:
    """Extract concrete changelog entries from Docusaurus-style updates pages.

    DeepSeek keeps important model updates as heading sections on `/updates`.
    The details link text is often just "this documentation", so normal link
    parsing loses the surrounding model name and facts. This parser turns each
    Date/H3 block into a real discovery record pointing at the details page
    when one exists.
    """
    records: list[dict[str, str]] = []
    h2_pattern = re.compile(r"<h2\b(?P<attrs>[^>]*)>(?P<title>.*?)</h2>", flags=re.I | re.S)
    h3_pattern = re.compile(r"<h3\b(?P<attrs>[^>]*)>(?P<title>.*?)</h3>", flags=re.I | re.S)
    h2_matches = list(h2_pattern.finditer(html_text or ""))
    for idx, h2 in enumerate(h2_matches):
        section_end = h2_matches[idx + 1].start() if idx + 1 < len(h2_matches) else len(html_text)
        section = html_text[h2.end() : section_end]
        date_text = _strip_html_text(h2.group("title"), 80)
        date_match = re.search(WEB_DISCOVERY_DATE_PATTERN, date_text, flags=re.I)
        if not date_match:
            continue
        h3_matches = list(h3_pattern.finditer(section))
        for h3_idx, h3 in enumerate(h3_matches):
            item_end = h3_matches[h3_idx + 1].start() if h3_idx + 1 < len(h3_matches) else len(section)
            item_html = section[h3.start() : item_end]
            title = _strip_html_text(h3.group("title"), 120)
            if not title or _line_is_generic(title):
                continue
            href = ""
            news_candidates: list[tuple[str, str]] = []
            for link in re.finditer(r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", item_html, flags=re.I | re.S):
                body = _strip_html_text(link.group("body"), 120).lower()
                candidate = _attr_value(link.group("attrs"), "href")
                if not candidate:
                    continue
                candidate_url = urljoin(base_url, candidate)
                candidate_path = urlparse(candidate_url).path.lower()
                if "documentation" in body or "\u6587\u6863" in body:
                    href = candidate_url
                    break
                if "/news/" in candidate_path:
                    news_candidates.append((candidate_url, body))
            if not href:
                normalized_title = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", title.lower())
                for candidate_url, body in news_candidates:
                    normalized_body = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", body)
                    if normalized_title and normalized_body and (
                        normalized_title in normalized_body or normalized_body in normalized_title
                    ):
                        href = candidate_url
                        break
            if not href:
                anchor = _attr_value(h3.group("attrs"), "id")
                href = urljoin(base_url, "#" + anchor) if anchor else base_url
            summary = _strip_html_text(item_html, 1200)
            if title not in summary:
                summary = clean_text(title + "\n" + summary, 1200)
            records.append(
                {
                    "href": href,
                    "text": f"{date_match.group(0)}\n{title}\n{summary}",
                    "context": summary,
                    "date_text": date_match.group(0),
                    "date_provenance": "changelog_heading",
                }
            )
    return records


def _rendered_link_records(source: Source, user_agent: str | None = None) -> tuple[list[dict[str, str]], str, str, str]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return [], "", source.url, f"playwright_unavailable: {type(exc).__name__}: {exc}"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                viewport={"width": 1360, "height": 900},
                user_agent=user_agent or "DailyBilibiliBriefing/0.1 (+local; official web discovery)",
                locale="zh-CN",
            )
            page.goto(source.url, wait_until="domcontentloaded", timeout=WEB_DISCOVERY_RENDER_TIMEOUT_MS)
            try:
                page.wait_for_load_state("networkidle", timeout=7_000)
            except Exception:
                pass
            page.wait_for_timeout(1_200)
            final_url = page.url
            body_text = clean_text(page.locator("body").inner_text(timeout=8_000), 4000)
            records = page.evaluate(
                r"""() => Array.from(document.querySelectorAll('a')).map((el) => {
                    const ownText = (el.innerText || el.textContent || '').trim();
                    let context = '';
                    let node = el.parentElement;
                    for (let depth = 0; node && depth < 7; depth += 1, node = node.parentElement) {
                        const txt = (node.innerText || node.textContent || '').trim();
                        if (txt && txt.length > ownText.length && txt.length <= 1400) {
                            context = txt;
                            if (/20\d{2}|release|launch|model|Hy\d|GPT|Claude|Gemini|Qwen|DeepSeek|OpenAI|Anthropic|Mistral|Llama|Grok/i.test(txt)) break;
                        }
                    }
                    return {
                        href: el.href || el.getAttribute('href') || '',
                        text: ownText,
                        context,
                    };
                })"""
            )
            browser.close()
            return [dict(x) for x in records if isinstance(x, dict)], body_text, final_url, ""
    except Exception as exc:
        return [], "", source.url, f"render_error: {type(exc).__name__}: {exc}"


def _line_is_generic(line: str) -> bool:
    text = clean_text(line).strip(" \t\r\n?:|\uFF5C-/\uFF1A")
    lowered = text.lower()
    if len(text) < 2:
        return True
    if lowered.startswith("skip to "):
        return True
    if re.search(r"icp\u5907|\u516c\u7f51\u5b89\u5907|copyright|all rights reserved", text, flags=re.I):
        return True
    without_boilerplate = re.sub(WEB_DISCOVERY_DATE_PATTERN, "", text, flags=re.I)
    without_boilerplate = re.sub(r"[\s·|｜:：/\-_.]+", "", without_boilerplate)
    for label in ["\u4e86\u89e3\u66f4\u591a", "\u67e5\u770b\u66f4\u591a", "\u67e5\u770b\u5168\u90e8"]:
        without_boilerplate = without_boilerplate.replace(label, "")
    if _line_looks_like_date(text) and not without_boilerplate:
        return True
    if without_boilerplate and (
        without_boilerplate.lower() in WEB_DISCOVERY_GENERIC_LABELS or without_boilerplate in WEB_DISCOVERY_GENERIC_LABELS
    ):
        return True
    return not text or lowered in WEB_DISCOVERY_GENERIC_LABELS or text in WEB_DISCOVERY_GENERIC_LABELS


def _line_looks_like_date(line: str) -> bool:
    return re.search(WEB_DISCOVERY_DATE_PATTERN, line or "", flags=re.I) is not None


def _is_same_page_fragment(base_url: str, candidate_url: str) -> bool:
    base_clean, _base_frag = urldefrag(base_url or "")
    candidate_clean, candidate_frag = urldefrag(candidate_url or "")
    return bool(candidate_frag) and bool(base_clean) and base_clean.rstrip("/") == candidate_clean.rstrip("/")


def _is_non_news_destination(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if host.startswith(("talent.", "careers.", "jobs.", "chat.", "platform.", "console.", "api.", "agent.", "app.")):
        return True
    normalized = path.rstrip("/")
    if normalized in {
        "/api",
        "/blog",
        "/blogs",
        "/developers",
        "/developer",
        "/platform",
        "/console",
        "/chat",
        "/news",
        "/updates",
        "/claude",
        "/models",
        "/model",
        "/pricing",
        "/products",
        "/product",
        "/contact",
        "/about",
    }:
        return True
    if re.fullmatch(r"/(?:zh|en|zh-cn|en-us)/(?:models?|products?|product|about|contact|pricing)", normalized):
        return True
    if normalized.startswith(("/pricing/", "/contact/", "/about/")):
        return True
    if "pricing" in path and not any(token in path for token in ["/news/", "/blog/", "/research/", "/release"]):
        return True
    return any(
        token in path
        for token in [
            "/careers",
            "/career",
            "/jobs",
            "/job",
            "/talent",
            "/hiring",
            "/login",
            "/auth",
            "/signin",
            "/signup",
            "/privacy",
            "/policies",
            "/terms",
            "/policy",
            "privacy-policy",
            "/legal",
        ]
    )


def _title_score(line: str, source: Source) -> int:
    lowered = line.lower()
    score = 0
    if _line_looks_like_date(line):
        score -= 2
    if any(k.lower() in lowered for k in WEB_DISCOVERY_AI_KEYWORDS):
        score += 8
    if any(k.lower() in lowered for k in WEB_DISCOVERY_EVENT_KEYWORDS):
        score += 6
    for topic in source.topics:
        topic = str(topic or "").strip().lower()
        if len(topic) >= 2 and topic in lowered:
            score += 5
    if any(ch.isdigit() for ch in line):
        score += 2
    if 6 <= len(line) <= 80:
        score += 3
    if len(line) > 120:
        score -= 8
    return score


def _title_candidate_rank(line: str, source: Source) -> int:
    score = _title_score(line, source)
    lowered = line.lower()
    if len(line) > 90:
        score -= 10
    if len(line) > 120:
        score -= 12
    if re.search(r"^(?:github|hugging face|modelscope|discord|demo|api|tech report)\b", line, flags=re.I):
        score -= 12
    if re.search(r"\b(?:we are|we're|here we|introduction|to try|feel free to|built upon)\b", lowered, flags=re.I):
        score -= 8
    if ":" in line[:80] and len(line) <= 110:
        score += 5
    return score


def _has_model_version_signal(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:gpt|claude|gemini|gemma|qwen|deepseek|hunyuan|hy|kimi|llama|mistral|glm|grok|minimax|doubao|seed|seedance|seedream|seed3d)[-_\s]?v?\d[\w.-]*\b",
            text,
            re.I,
        )
    )


def _title_from_url_slug(href: str) -> str:
    try:
        parsed = urlparse(href)
    except Exception:
        return ""
    parts = [unquote(part).strip() for part in parsed.path.split("/") if part.strip()]
    generic_parts = {
        "ai",
        "blog",
        "blogs",
        "news",
        "research",
        "models",
        "model",
        "products",
        "product",
        "developer",
        "developers",
        "docs",
        "articles",
        "post",
        "posts",
        "technology",
    }
    candidates = [part for part in parts if part.lower() not in generic_parts]
    if not candidates:
        return ""
    slug = candidates[-1]
    slug = re.sub(r"\.(?:html?|mdx?|php)$", "", slug, flags=re.I)
    if not slug or _line_is_generic(slug):
        return ""
    word_stop = {"official", "launch", "introducing", "introduction", "release", "released", "of", "the", "a", "an"}
    words = [part for part in re.split(r"[-_]+", slug) if part and part.lower() not in word_stop]
    if not words:
        return ""

    def pretty_word(word: str) -> str:
        lowered = word.lower()
        aliases = {
            "gpt": "GPT",
            "api": "API",
            "ai": "AI",
            "glm": "GLM",
            "qwen": "Qwen",
            "kimi": "Kimi",
            "llama": "Llama",
            "mistral": "Mistral",
            "hy": "Hy",
            "hunyuan": "Hunyuan",
            "deepseek": "DeepSeek",
            "gemini": "Gemini",
            "gemma": "Gemma",
            "grok": "Grok",
            "minimax": "MiniMax",
            "doubao": "Doubao",
            "seed": "Seed",
            "seedance": "Seedance",
            "seedream": "Seedream",
            "seed3d": "Seed3D",
        }
        if lowered in aliases:
            return aliases[lowered]
        if re.fullmatch(r"[a-z]{1,4}\d[\w.]*", lowered):
            return word.upper()
        return word.upper() if word.isupper() else word.capitalize()

    title = " ".join(pretty_word(word) for word in words)
    title = re.sub(r"\b([A-Za-z]+[0-9]) ([0-9])\b", r"\1.\2", title)
    title = re.sub(r"\b(\d+) (\d+)(?=\b| )", r"\1.\2", title)
    if not _has_model_version_signal(title) and not any(ch.isdigit() for ch in title):
        return ""
    return clean_text(title, 120)


def _clean_discovery_title_candidate(line: str) -> str:
    text = clean_text(line, 160)
    if not text:
        return ""
    text = re.sub(rf"^\s*(?:{WEB_DISCOVERY_DATE_PATTERN})\s*", "", text, flags=re.I).strip()
    text = re.sub(rf"\s+(?:{WEB_DISCOVERY_DATE_PATTERN})(?:\s+.*)?$", "", text, flags=re.I).strip()
    text = re.sub(r"^(?:product|products|announcement|announcements|company|research|news|blog)\s+", "", text, flags=re.I).strip()
    text = re.sub(r"\s+(?:product|products|announcement|announcements|company|research|news|blog)$", "", text, flags=re.I).strip()
    text = re.sub(r"\s+(?:\d+\s+min|\d+\s+words|[A-Za-z]+\s+Team)\b.*$", "", text, flags=re.I).strip()
    return clean_text(text, 120)


def _discovery_title(text: str, source: Source) -> str:
    raw_lines = [clean_text(x, 140) for x in re.split(r"[\n\r]+", text or "")]
    for idx, raw_line in enumerate(raw_lines):
        if not raw_line or not _line_looks_like_date(raw_line):
            continue
        previous_candidates: list[tuple[int, int, str]] = []
        for prev_line in raw_lines[max(0, idx - 6) : idx]:
            candidate = _clean_discovery_title_candidate(prev_line)
            if candidate and not _line_is_generic(candidate):
                rank = _title_candidate_rank(candidate, source)
                if rank >= 5:
                    previous_candidates.append((rank, -len(candidate), candidate))
        if previous_candidates:
            previous_candidates.sort(reverse=True)
            return previous_candidates[0][2]
        same_line_candidate = _clean_discovery_title_candidate(raw_line)
        date_prefix = re.match(WEB_DISCOVERY_DATE_PATTERN, raw_line, flags=re.I)
        post_date = raw_line[date_prefix.end() :] if date_prefix else ""
        same_line_is_section_label = post_date.lstrip().startswith(("|", "\uFF5C", "-", "\u2014", "\u00B7")) and not (
            _has_model_version_signal(same_line_candidate)
            or any(keyword.lower() in same_line_candidate.lower() for keyword in WEB_DISCOVERY_EVENT_KEYWORDS)
        )
        if same_line_candidate and not _line_is_generic(same_line_candidate) and not same_line_is_section_label:
            return same_line_candidate
        next_candidates: list[tuple[int, int, str]] = []
        for next_line in raw_lines[idx + 1 : idx + 5]:
            candidate = _clean_discovery_title_candidate(next_line)
            if candidate and not _line_is_generic(candidate):
                if len(candidate) <= 110 or _has_model_version_signal(candidate):
                    return candidate
                next_candidates.append((_title_candidate_rank(candidate, source), -len(candidate), candidate))
        if next_candidates:
            next_candidates.sort(reverse=True)
            return next_candidates[0][2]
    lines = [_clean_discovery_title_candidate(x) for x in raw_lines if x and not _line_is_generic(x)]
    lines = [x for x in lines if x and not _line_is_generic(x)]
    if not lines:
        return ""
    if len(lines[0]) <= 100 and _title_candidate_rank(lines[0], source) >= 5:
        return clean_text(lines[0], 120)
    lines.sort(key=lambda line: _title_candidate_rank(line, source), reverse=True)
    return clean_text(lines[0], 120)


def _discovery_summary(text: str, title: str) -> str:
    lines = [clean_text(x, 220) for x in re.split(r"[\n\r]+", text or "")]
    result: list[str] = []
    seen: set[str] = set()
    for line in lines:
        if not line or line == title or _line_is_generic(line):
            continue
        if _line_looks_like_date(line):
            continue
        norm = re.sub(r"\W+", "", line.lower())
        if not norm or norm in seen:
            continue
        seen.add(norm)
        result.append(line)
        if len("\uFF1B".join(result)) >= 520:
            break
    return clean_text("\uFF1B".join(result), 600)


def _discovery_relevant(title: str, summary: str, href: str, source: Source) -> bool:
    content_haystack = clean_text(" ".join([title, summary, href])).lower()
    source_haystack = clean_text(" ".join([source.name, " ".join(source.topics)])).lower()
    if not title or _line_is_generic(title):
        return False
    generic_topic_labels = {"ai", "api", "model", "models", "\u6a21\u578b", "\u5f00\u6e90", "open models", "open source"}
    topic_hit = any(
        topic in content_haystack
        for topic in (str(topic or "").strip().lower() for topic in source.topics)
        if len(topic) >= 2 and topic not in generic_topic_labels
    )
    ai_hit = any(keyword.lower() in content_haystack for keyword in WEB_DISCOVERY_AI_KEYWORDS)
    event_hit = any(keyword.lower() in content_haystack for keyword in WEB_DISCOVERY_EVENT_KEYWORDS)
    version_hit = _has_model_version_signal(content_haystack)
    date_hit = re.search(WEB_DISCOVERY_DATE_PATTERN, content_haystack, flags=re.I) is not None
    path = urlparse(href).path.lower()
    if path.startswith(("/guides/", "/quick_start/")) and not (date_hit or version_hit):
        return False
    source_is_ai = any(keyword.lower() in source_haystack for keyword in WEB_DISCOVERY_AI_KEYWORDS)
    return (topic_hit or ai_hit or (source_is_ai and version_hit)) and (event_hit or version_hit or date_hit)


def _merge_discovery_records(records: list[dict[str, str]]) -> list[dict[str, str]]:
    merged: dict[str, dict[str, str]] = {}
    order: list[str] = []
    for record in records:
        href = str(record.get("href") or "").strip()
        if not href:
            continue
        key = re.sub(r"[#?].*$", "", href)
        if key not in merged:
            merged[key] = {
                "href": href,
                "text": "",
                "context": "",
                "date_text": "",
                "date_provenance": "",
                "_date_conflict": "",
            }
            order.append(key)
        for field in ["text", "context"]:
            value = str(record.get(field) or "").strip()
            if value and value not in merged[key][field]:
                merged[key][field] = (merged[key][field] + "\n" + value).strip()
        date_text = str(record.get("date_text") or "").strip()
        if date_text and not merged[key]["_date_conflict"]:
            if not merged[key]["date_text"]:
                merged[key]["date_text"] = date_text
                merged[key]["date_provenance"] = str(record.get("date_provenance") or "").strip()
            elif merged[key]["date_text"] != date_text:
                # Conflicting explicit dates for one URL are unsafe regardless
                # of DOM order.  Keep the record undated so the verification
                # and downstream freshness gates fail closed.
                merged[key]["date_text"] = ""
                merged[key]["date_provenance"] = "ambiguous_duplicate_date"
                merged[key]["_date_conflict"] = "1"
    result: list[dict[str, str]] = []
    for key in order:
        row = dict(merged[key])
        row.pop("_date_conflict", None)
        result.append(row)
    return result


def _record_published_at(raw_text: str, title: str, source: Source) -> tuple[datetime | None, str]:
    """Use only an unambiguous date attached to this discovery record.

    Listing pages frequently contain a page-level "today" date plus dates from
    neighbouring cards. Taking the first date silently turns old evergreen
    pages into today's news, which is worse than keeping the item undated.
    """
    matches = list(re.finditer(WEB_DISCOVERY_DATE_PATTERN, raw_text or "", flags=re.I))
    first_line = next((line.strip() for line in (raw_text or "").splitlines() if line.strip()), "")
    first_line_match = re.search(WEB_DISCOVERY_DATE_PATTERN, first_line, flags=re.I)
    if first_line_match and first_line_match.start() <= 8:
        date_text = first_line_match.group(0).replace("年", "-").replace("月", "-").replace("日", "")
        return _normalize_published_at(source, parse_date(date_text)), "record_heading"
    normalized: list[tuple[str, int, int]] = []
    for match in matches:
        value = match.group(0).replace("年", "-").replace("月", "-").replace("日", "")
        parsed = parse_date(value)
        if not parsed:
            continue
        key = parsed.date().isoformat()
        if not any(existing[0] == key for existing in normalized):
            normalized.append((key, match.start(), match.end()))
    if len(normalized) != 1:
        return None, "ambiguous_record_date" if normalized else ""
    _key, start, end = normalized[0]
    compact_title = clean_text(title)
    # Very large contexts are normally section/page containers. Require the
    # title to be reasonably close to the sole date before trusting it.
    if compact_title and len(raw_text) > 420:
        title_pos = raw_text.lower().find(compact_title.lower())
        if title_pos < 0 or min(abs(title_pos - start), abs(title_pos - end)) > 240:
            return None, "unanchored_record_date"
    date_text = raw_text[start:end].replace("年", "-").replace("月", "-").replace("日", "")
    return _normalize_published_at(source, parse_date(date_text)), "record_context"


def parse_web_discovery_records(records: list[dict[str, str]], source: Source, final_url: str = "") -> list[FeedItem]:
    result: list[FeedItem] = []
    seen: set[str] = set()
    for record in _merge_discovery_records(records):
        href = str(record.get("href") or "").strip()
        if not href.startswith(("http://", "https://")):
            continue
        if _is_same_page_fragment(final_url or source.url, href) or _is_same_page_fragment(source.url, href):
            continue
        if _is_non_news_destination(href):
            continue
        if not (_same_site(source.url, href) or (bool(final_url) and _same_site(final_url, href))):
            continue
        raw_text = str(record.get("text") or "")
        href_path = urlparse(href).path.rstrip("/") or "/"
        if href_path in {"/", "/research", "/model", "/model/hy-model", "/solutions", "/blog", "/news"} and _line_is_generic(raw_text):
            continue
        context_text = str(record.get("context") or "")
        title = _discovery_title(raw_text, source)
        if context_text and (not title or _line_is_generic(title)):
            raw_text = raw_text + "\n" + context_text
            title = _discovery_title(raw_text, source)
        slug_title = _title_from_url_slug(href)
        if (not title or _line_is_generic(title)) and slug_title:
            title = slug_title
        summary = _discovery_summary(raw_text, title)
        if not _discovery_relevant(title, summary, href, source):
            continue
        key = re.sub(r"[#?].*$", "", href)
        if key in seen:
            continue
        seen.add(key)
        explicit_date = str(record.get("date_text") or "").strip()
        explicit_provenance = str(record.get("date_provenance") or "").strip()
        if explicit_provenance == "ambiguous_duplicate_date":
            published = None
            published_provenance = explicit_provenance
        elif explicit_date and explicit_provenance:
            date_text = explicit_date.replace("年", "-").replace("月", "-").replace("日", "")
            published = _normalize_published_at(source, parse_date(date_text))
            published_provenance = explicit_provenance
        else:
            published, published_provenance = _record_published_at(raw_text, title, source)
        result.append(
            FeedItem(
                source_id=source.id,
                source_name=source.name,
                source_tier=source.tier,
                source_reliability=source.reliability,
                title=title,
                link=href,
                # Identity must come from the canonical URL only: heuristic titles and
                # parsed dates differ between static-HTML and Playwright-rendered parses
                # of the same page, and hashing them made the same announcement re-enter
                # as a "new" item (duplicate cards, wasted crawl budget).
                guid=stable_hash([source.id, key]),
                summary=summary,
                published_at=published,
                fetched_at=datetime.now(UTC),
                raw={
                    "final_url": final_url or source.url,
                    "kind": "official_web_discovery",
                    "published_at_provenance": published_provenance,
                },
            )
        )
    result.sort(
        key=lambda item: (
            1 if item.published_at else 0,
            item.published_at.timestamp() if item.published_at else 0,
            _title_score(" ".join([item.title, item.summary, item.link]), source),
        ),
        reverse=True,
    )
    return result[:WEB_DISCOVERY_ITEM_LIMIT]


def _web_discovery_items_need_render(items: list[FeedItem], source: Source) -> bool:
    if not items:
        return True
    for item in items:
        title = clean_text(item.title)
        if _line_is_generic(title):
            return True
        if len(title) > 110:
            return True
        if _title_candidate_rank(title, source) < 5:
            return True
    return False


def _sitemap_slug_title(url: str) -> str:
    slug = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    return clean_text(re.sub(r"[-_]+", " ", unquote(slug))).title()


def parse_sitemap_records(xml_text: str, source: Source, final_url: str, limit: int = 40) -> list[FeedItem]:
    """Convert one URL-set sitemap into dated official discovery items."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    rows: list[FeedItem] = []
    for node in root.findall("{*}url"):
        loc = clean_text(node.findtext("{*}loc") or "")
        lastmod = clean_text(node.findtext("{*}lastmod") or "")
        if not loc or not urlparse(loc).scheme:
            continue
        # WordPress indexes also list category/tag/archive URLs with the latest
        # child modification time. Those are navigation pages, not events.
        if source.reliability == "official" and not re.search(r"/news/\d{4}/\d{2}/[^/]+/?$", urlparse(loc).path, flags=re.I):
            continue
        title = _sitemap_slug_title(loc)
        if not title or title.lower() in WEB_DISCOVERY_GENERIC_LABELS:
            continue
        published_at = parse_date(lastmod)
        rows.append(
            FeedItem(
                source_id=source.id,
                source_name=source.name,
                source_tier=source.tier,
                source_reliability=source.reliability,
                title=title,
                link=loc,
                guid=loc,
                summary=f"官方站点更新：{title}",
                published_at=published_at,
                raw={
                    "kind": "official_sitemap",
                    "sitemap_url": final_url,
                    "published_at_provenance": "sitemap_lastmod" if published_at else "unanchored_sitemap_date",
                },
            )
        )
    rows.sort(key=lambda item: item.published_at or item.fetched_at, reverse=True)
    return rows[: max(1, int(limit))]


def fetch_sitemap_source(source: Source, user_agent: str | None = None) -> tuple[SourceHealth, list[FeedItem]]:
    started = time.perf_counter()
    headers = {"User-Agent": user_agent or "DailyBilibiliBriefing/0.1 (+local)", "Accept": "application/xml,text/xml,*/*;q=0.5"}
    try:
        root_response = requests.get(source.url, headers=headers, timeout=source.timeout_seconds, allow_redirects=True)
        if not root_response.ok:
            latency = int((time.perf_counter() - started) * 1000)
            return SourceHealth(source.id, source.name, source.tier, source.enabled, "http_error", root_response.status_code, 0, None, latency, root_response.text[:300], root_response.url, False, 0), []
        texts: list[tuple[str, str]] = [(root_response.text, root_response.url)]
        try:
            root = ET.fromstring(root_response.text)
            child_urls = [clean_text(node.text or "") for node in root.findall("{*}sitemap/{*}loc") if clean_text(node.text or "")]
        except ET.ParseError:
            child_urls = []
        # Indexes are cheap and deterministic; cap recursion to avoid crawling
        # unrelated giant archives forever.
        child_errors: list[str] = []
        for child_url in child_urls[:12]:
            try:
                child = requests.get(child_url, headers=headers, timeout=source.timeout_seconds, allow_redirects=True)
                if child.ok:
                    texts.append((child.text, child.url))
                else:
                    child_errors.append(f"{child_url}: http {child.status_code}")
            except Exception as exc:
                # One failing child sitemap must not blank the whole source.
                child_errors.append(f"{child_url}: {type(exc).__name__}: {exc}")
        items: list[FeedItem] = []
        seen: set[str] = set()
        for text, final_url in texts:
            for item in parse_sitemap_records(text, source, final_url, limit=120):
                if item.link not in seen:
                    seen.add(item.link)
                    items.append(item)
        items.sort(key=lambda item: item.published_at or item.fetched_at, reverse=True)
        items = items[:120]
        latest = max([item.published_at for item in items if item.published_at], default=None)
        latency = int((time.perf_counter() - started) * 1000)
        return SourceHealth(source.id, source.name, source.tier, source.enabled, "ok" if items else "no_items", root_response.status_code, len(items), latest, latency, "; ".join(child_errors)[:300], root_response.url, False, sum(1 for item in items if not item.published_at)), items
    except Exception as exc:
        latency = int((time.perf_counter() - started) * 1000)
        return SourceHealth(source.id, source.name, source.tier, source.enabled, "error", None, 0, None, latency, f"{type(exc).__name__}: {exc}", source.url, False, 0), []


def fetch_web_discovery_source(source: Source, user_agent: str | None = None) -> tuple[SourceHealth, list[FeedItem]]:
    started = time.perf_counter()
    headers = {
        "User-Agent": user_agent or "DailyBilibiliBriefing/0.1 (+local)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    status_code: int | None = None
    final_url = source.url
    error = ""
    records: list[dict[str, str]] = []
    try:
        r = requests.get(source.url, headers=headers, timeout=source.timeout_seconds, allow_redirects=True)
        status_code = r.status_code
        final_url = r.url
        if r.encoding is None or r.encoding.lower() == "iso-8859-1":
            r.encoding = r.apparent_encoding or "utf-8"
        if r.ok:
            records = _html_link_records(r.text, r.url)
            records.extend(_docusaurus_changelog_records(r.text, r.url))
        else:
            error = r.text[:300]
    except Exception as exc:
        error = f"requests: {type(exc).__name__}: {exc}"
    items = parse_web_discovery_records(records, source, final_url)
    rendered_error = ""
    needs_render = _web_discovery_items_need_render(items, source)
    if needs_render:
        rendered_records, _body_text, rendered_final_url, rendered_error = _rendered_link_records(source, user_agent=user_agent)
        if rendered_final_url:
            final_url = rendered_final_url
        rendered_items = parse_web_discovery_records(rendered_records, source, final_url)
        if rendered_items:
            items = rendered_items
            error = ""
        elif rendered_error:
            error = (error + "; " + rendered_error).strip("; ")
    latest = max([x.published_at for x in items if x.published_at], default=None)
    no_date = sum(1 for x in items if not x.published_at)
    latency = int((time.perf_counter() - started) * 1000)
    status = "ok" if items else ("no_items" if not error else "error")
    return SourceHealth(source.id, source.name, source.tier, source.enabled, status, status_code, len(items), latest, latency, error[:300], final_url, False, no_date), items


def fetch_agent_social_source(source: Source, lookback_hours: int | None = None) -> tuple[SourceHealth, list[FeedItem]]:
    """Read fresh, schema-checked X leads produced by the Codex automation."""
    started = time.perf_counter()
    raw_path, _, fragment = source.url.partition("#")
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = project_root() / path
    allowed_accounts = {value.strip().lstrip("@").lower() for value in fragment.split(",") if value.strip()}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict) or payload.get("agent") != "codex" or int(payload.get("version") or 0) != 1:
            raise ValueError("lead file must be version 1 and agent=codex")
        generated_at = parse_date(str(payload.get("generated_at") or ""))
        if generated_at is None:
            raise ValueError("lead file has no generated_at")
        now = datetime.now(UTC)
        max_file_age = max(12, int(lookback_hours or 24))
        if generated_at > now + timedelta(minutes=10) or now - generated_at > timedelta(hours=max_file_age):
            raise ValueError("lead file is stale or future-dated")
        lane_audits = payload.get("lane_audits") or {}
        lane_audit = lane_audits.get(source.id) if isinstance(lane_audits, dict) else None
        if not isinstance(lane_audit, dict) or lane_audit.get("status") != "checked":
            raise ValueError(f"missing fresh checked lane audit for {source.id}")
        checked_at = parse_date(str(lane_audit.get("checked_at") or ""))
        if (
            checked_at is None
            or checked_at > now + timedelta(minutes=10)
            or now - checked_at > timedelta(hours=max_file_age)
        ):
            raise ValueError(f"lane audit for {source.id} is stale or future-dated")
        accounts_checked = {
            str(value or "").strip().lstrip("@").lower()
            for value in lane_audit.get("accounts_checked") or []
            if str(value or "").strip()
        }
        missing_accounts = sorted(allowed_accounts - accounts_checked)
        if missing_accounts:
            raise ValueError(f"lane audit did not check configured accounts: {', '.join(missing_accounts)}")
        window = max(12, int(lookback_hours or 24))
        # Anchor the acceptance window to the file's generation time: qualifying_items was
        # counted then, so re-counting against a later "now" would reject the entire lane
        # whenever a boundary-aged post drifted out of the window between generation and use.
        cutoff = generated_at - timedelta(hours=window)
        items: list[FeedItem] = []
        for row in payload.get("items") or []:
            if not isinstance(row, dict) or str(row.get("source_id") or "") != source.id:
                continue
            link = str(row.get("url") or "").strip()
            match = re.fullmatch(r"https://(?:www\.)?(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,32})/status/(\d{3,})(?:[/?#].*)?", link, flags=re.I)
            if not match:
                continue
            account = match.group(1).lower()
            if allowed_accounts and account not in allowed_accounts:
                continue
            title = clean_text(str(row.get("title") or ""), 180)
            summary = clean_text(str(row.get("text") or row.get("summary") or ""), 1600)
            published_at = parse_date(str(row.get("published_at") or ""))
            checks = row.get("checks") or {}
            checked_urls = {_normalize for value in row.get("checked_urls") or [] if (_normalize := str(value or "").strip())}
            if (
                not title
                or len(summary) < 24
                or published_at is None
                or published_at < cutoff
                or published_at > now + timedelta(minutes=10)
                or checks.get("account_verified") is not True
                or checks.get("post_text_verified") is not True
                or link not in checked_urls
            ):
                continue
            discovery_url = str(row.get("discovery_url") or "").strip()
            items.append(
                FeedItem(
                    source_id=source.id,
                    source_name=source.name,
                    source_tier=source.tier,
                    source_reliability=source.reliability,
                    title=title,
                    link=f"https://x.com/{match.group(1)}/status/{match.group(2)}",
                    guid=f"codex-social:{source.id}:{match.group(2)}",
                    summary=summary,
                    published_at=published_at,
                    raw={
                        "kind": "codex_agent_social_lead",
                        "agent_generated_at": generated_at.isoformat(),
                        "discovery_url": discovery_url if discovery_url and discovery_url != link else "",
                        "account": match.group(1),
                        "status_id": match.group(2),
                        "checked_urls": sorted(checked_urls),
                    },
                )
            )
        declared_count = lane_audit.get("qualifying_items")
        if not isinstance(declared_count, int) or isinstance(declared_count, bool) or declared_count != len(items):
            raise ValueError(
                f"lane audit qualifying_items mismatch for {source.id}: declared {declared_count!r}, accepted {len(items)}"
            )
        latest = max((item.published_at for item in items if item.published_at), default=checked_at)
        latency = int((time.perf_counter() - started) * 1000)
        return SourceHealth(source.id, source.name, source.tier, source.enabled, "ok", None, len(items), latest, latency, "", str(path), False, 0), items
    except Exception as exc:
        latency = int((time.perf_counter() - started) * 1000)
        return SourceHealth(
            source.id,
            source.name,
            source.tier,
            source.enabled,
            "missing" if isinstance(exc, FileNotFoundError) else "error",
            None,
            0,
            None,
            latency,
            f"{type(exc).__name__}: {exc}"[:300],
            str(path),
            False,
            0,
        ), []


def fetch_source(source: Source, user_agent: str | None = None, lookback_hours: int | None = None) -> tuple[SourceHealth, list[FeedItem]]:
    started = time.perf_counter()
    headers = {
        "User-Agent": user_agent or "DailyBilibiliBriefing/0.1 (+local)",
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, text/html;q=0.8, */*;q=0.5",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    try:
        if not source.enabled:
            return SourceHealth(source.id, source.name, source.tier, False, "disabled", final_url=source.url), []
        if source.type == AGENT_SOCIAL_TYPE:
            return fetch_agent_social_source(source, lookback_hours=lookback_hours)
        if source.type in SITEMAP_TYPES:
            return fetch_sitemap_source(source, user_agent=user_agent)
        if source.type == "hf_models":
            r = requests.get(source.url, headers={**headers, "Accept": "application/json"}, timeout=source.timeout_seconds, allow_redirects=True)
            latency = int((time.perf_counter() - started) * 1000)
            if not r.ok:
                return SourceHealth(source.id, source.name, source.tier, source.enabled, "http_error", r.status_code, 0, None, latency, r.text[:300], r.url, False, 0), []
            items = parse_hf_models(r.text, source, r.url)
            latest = max([x.published_at for x in items if x.published_at], default=None)
            no_date = sum(1 for x in items if not x.published_at)
            stale = False
            if latest:
                stale = (datetime.now(UTC) - latest).total_seconds() / 3600 > 24 * 14
            status = "ok" if items else "no_items"
            return SourceHealth(source.id, source.name, source.tier, source.enabled, status, r.status_code, len(items), latest, latency, "", r.url, stale, no_date), items
        if source.type == "google_news":
            url = _google_news_url(source, lookback_hours=lookback_hours)
            r = requests.get(url, headers=headers, timeout=source.timeout_seconds, allow_redirects=True)
            latency = int((time.perf_counter() - started) * 1000)
            if not r.ok:
                return SourceHealth(source.id, source.name, source.tier, source.enabled, "http_error", r.status_code, 0, None, latency, r.text[:300], r.url, False, 0), []
            items = parse_google_news(r.text, source, r.url)
            latest = max([x.published_at for x in items if x.published_at], default=None)
            no_date = sum(1 for x in items if not x.published_at)
            status = "ok" if items else "no_items"
            return SourceHealth(source.id, source.name, source.tier, source.enabled, status, r.status_code, len(items), latest, latency, "", r.url, False, no_date), items
        if source.type in WEB_DISCOVERY_TYPES:
            return fetch_web_discovery_source(source, user_agent=user_agent)
        if source.type not in {"rss", "atom", "rsshub"}:
            r = requests.get(source.url, headers=headers, timeout=source.timeout_seconds, allow_redirects=True)
            latency = int((time.perf_counter() - started) * 1000)
            status = "ok_web_pending_adapter" if r.ok else "http_error"
            return SourceHealth(source.id, source.name, source.tier, source.enabled, status, r.status_code, 0, None, latency, "", r.url, False, 0), []
        r = requests.get(source.url, headers=headers, timeout=source.timeout_seconds, allow_redirects=True)
        latency = int((time.perf_counter() - started) * 1000)
        if r.encoding is None or r.encoding.lower() == "iso-8859-1":
            # requests defaults text/* without charset to ISO-8859-1, which turns CJK
            # feeds into mojibake; trust the sniffed encoding instead.
            r.encoding = r.apparent_encoding or "utf-8"
        if not r.ok:
            return SourceHealth(source.id, source.name, source.tier, source.enabled, "http_error", r.status_code, 0, None, latency, r.text[:300], r.url, False, 0), []
        items = parse_feed(r.text, source, r.url)
        if "github.com" in source.url.lower() and "releases" in source.url.lower():
            items = enrich_github_release_items(items, user_agent=headers["User-Agent"])
        latest = max([x.published_at for x in items if x.published_at], default=None)
        no_date = sum(1 for x in items if not x.published_at)
        stale = False
        if latest:
            stale = (datetime.now(UTC) - latest).total_seconds() / 3600 > 24 * 14
        status = "ok" if items else "no_items"
        return SourceHealth(source.id, source.name, source.tier, source.enabled, status, r.status_code, len(items), latest, latency, "", r.url, stale, no_date), items
    except Exception as exc:
        latency = int((time.perf_counter() - started) * 1000)
        return SourceHealth(source.id, source.name, source.tier, source.enabled, "error", None, 0, None, latency, f"{type(exc).__name__}: {exc}", source.url, False, 0), []


def collect_sources(sources: list[Source], user_agent: str | None = None, workers: int = 8, lookback_hours: int | None = None) -> tuple[list[SourceHealth], list[FeedItem]]:
    health: list[SourceHealth] = []
    items: list[FeedItem] = []
    if not sources:
        return health, items
    web_sources = [s for s in sources if s.type in WEB_DISCOVERY_TYPES]
    x_sources = [s for s in sources if s.id.startswith("x_") and s.type not in WEB_DISCOVERY_TYPES]
    normal_sources = [s for s in sources if s.type not in WEB_DISCOVERY_TYPES and not s.id.startswith("x_")]
    with cf.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = [pool.submit(fetch_source, s, user_agent, lookback_hours) for s in normal_sources]
        for fut in cf.as_completed(futs):
            h, got = fut.result()
            health.append(h)
            items.extend(got)
    # Public X RSS mirrors rate-limit bursts aggressively.  Fetch this small
    # lane sequentially and retry one transient failure rather than firing all
    # accounts at the same host in parallel.
    for index, source in enumerate(x_sources):
        h, got = fetch_source(source, user_agent, lookback_hours)
        if h.status in {"error", "http_error"}:
            time.sleep(1.5)
            retry_health, retry_items = fetch_source(source, user_agent, lookback_hours)
            if retry_health.status == "ok":
                h, got = retry_health, retry_items
            elif retry_health.error:
                h.error = f"{h.error}; retry={retry_health.error}"[:300]
        health.append(h)
        items.extend(got)
        if index < len(x_sources) - 1:
            time.sleep(0.6)
    # Browser-rendered official-site discovery is intentionally sequential: a few
    # main AI homepages are worth crawling, but launching many Chromium instances
    # in parallel is slower and flakier on Windows.
    for source in web_sources:
        h, got = fetch_source(source, user_agent, lookback_hours)
        health.append(h)
        items.extend(got)
    health.sort(key=lambda h: (h.tier, h.source_name))
    items.sort(key=lambda it: it.published_at or it.fetched_at, reverse=True)
    return health, items

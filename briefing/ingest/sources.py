from __future__ import annotations

import concurrent.futures as cf
import html as html_lib
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urldefrag, urlencode, urljoin, urlparse

import requests

from ..core import (
    AI_BRANDS,
    MODEL_TOKEN_RE,
    UTC,
    FeedItem,
    Source,
    SourceHealth,
    canonical_url,
    clean_text,
    now_utc,
    parse_date,
    parse_x_status,
    project_root,
    read_json,
    stable_hash,
    url_host,
)
from .browser import find_browser_executable
from .github import enrich_github_release_items

DEFAULT_USER_AGENT = "DailyBilibiliBriefing/0.1 (+local)"
LOCAL_TIME_AS_GMT_SOURCE_IDS = {"infoq_cn"}
GOOGLE_NEWS_SEARCH_URL = "https://news.google.com/rss/search"
WEB_DISCOVERY_TYPES = {"web", "web_discovery", "official_site"}
AGENT_SOCIAL_TYPE = "agent_social"
WEB_DISCOVERY_ITEM_LIMIT = 12
WEB_DISCOVERY_RENDER_TIMEOUT_MS = 18_000

WEB_DISCOVERY_GENERIC_LABELS = {
    "了解更多", "查看更多", "查看全部", "阅读原文", "阅读更多", "文章",
    "learn more", "read more", "more details", "this documentation",
    "立即试用", "试用", "登录", "login", "sign in", "signup", "sign up", "注册",
    "中文", "en", "模型", "研究", "产品", "pricing", "docs", "blog", "news",
    "release notes", "announcement", "announcements", "release", "developers",
    "research", "models", "model", "products", "product", "company", "safety",
    "policy", "research index", "careers", "career", "jobs", "join us",
    "view jobs", "open roles", "开始对话", "技术博客", "联系我们", "用户协议",
    "隐私政策", "服务条款", "模型发布", "研究成果", "发布", "上线", "更新",
    "contact", "contact us", "招聘", "加入我们", "隐私", "条款",
    "skip to main content", "skip to content", "skip navigation", "main content",
    "跳到主要内容", "跳至主要内容",
}

WEB_DISCOVERY_EVENT_KEYWORDS = (
    "发布", "上线", "推出", "更新", "release", "launch", "announc", "pricing",
    "价格", "benchmark", "评测", "榜单", "开源", "open source", "agent",
    "智能体", "context", "上下文",
)

WEB_DISCOVERY_AI_KEYWORDS = tuple(
    dict.fromkeys(
        [
            *(alias.strip() for b in AI_BRANDS if b.mainstream for alias in b.aliases if len(alias.strip()) >= 2),
            "hy",
            "seed",
            "seedream",
            "seed3d",
        ]
    )
)

WEB_DISCOVERY_DATE_PATTERN = (
    r"20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}"
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
    return [el for el in root.iter() if _tag_name(el.tag) in {"item", "entry"}]


def _regex_tag(chunk: str, tag: str) -> str:
    match = re.search(rf"<[^>/]*{re.escape(tag)}[^>]*>(.*?)</[^>]*{re.escape(tag)}>", chunk, re.I | re.S)
    if match:
        return match.group(1)
    if tag == "link":
        href_match = re.search(r"<link[^>]+href=['\"]([^'\"]+)", chunk, re.I)
        if href_match:
            return href_match.group(1)
    return ""


def _normalize_published_at(source: Source, published: datetime | None) -> datetime | None:
    if not published:
        return None
    if source.id in LOCAL_TIME_AS_GMT_SOURCE_IDS:
        return published - timedelta(hours=8)
    return published


def _parse_chunk(chunk: str, source: Source, final_url: str) -> FeedItem | None:
    title = clean_text(_regex_tag(chunk, "title"), 240)
    if not title:
        return None
    link = clean_text(_regex_tag(chunk, "link"))
    guid = clean_text(_regex_tag(chunk, "guid") or _regex_tag(chunk, "id")) or link or stable_hash([source.id, title])
    summary = clean_text(_regex_tag(chunk, "description") or _regex_tag(chunk, "summary"), 1200)
    published = _normalize_published_at(
        source,
        parse_date(_regex_tag(chunk, "pubDate") or _regex_tag(chunk, "published") or _regex_tag(chunk, "updated")),
    )
    return FeedItem(
        source_id=source.id,
        source_name=source.name,
        source_tier=source.tier,
        source_reliability=source.reliability,
        title=title,
        link=link or final_url or source.url,
        guid=guid,
        summary=summary,
        published_at=published,
    )


def parse_feed(xml_text: str, source: Source, final_url: str = "") -> list[FeedItem]:
    """Parse an RSS or Atom XML feed into FeedItem records."""
    text = xml_text or ""
    start = text.find("<")
    if start > 0:
        text = text[start:]
    try:
        root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError:
        chunks = re.findall(r"<item\b.*?</item>|<entry\b.*?</entry>", text, re.I | re.S)
        return [item for item in (_parse_chunk(ch, source, final_url) for ch in chunks) if item is not None]

    result: list[FeedItem] = []
    fetched = now_utc()
    for el in _all_items(root):
        title = clean_text(_child_text(el, ["title"]), 240)
        if not title:
            continue
        discovery_link = _child_text(el, ["link"]).strip()
        guid = clean_text(_child_text(el, ["guid", "id"])) or discovery_link or stable_hash([source.id, title])
        summary = clean_text(_child_text(el, ["description", "summary", "content", "encoded"]), 1200)
        published = _normalize_published_at(
            source,
            parse_date(_child_text(el, ["pubDate", "published", "updated", "date"])),
        )
        result.append(
            FeedItem(
                source_id=source.id,
                source_name=source.name,
                source_tier=source.tier,
                source_reliability=source.reliability,
                title=title,
                link=discovery_link or final_url or source.url,
                guid=guid,
                summary=summary,
                published_at=published,
                fetched_at=fetched,
                raw={"final_url": final_url, "discovery_url": discovery_link},
            )
        )
    return result


def parse_hf_models(payload: Any, source: Source, final_url: str = "") -> list[FeedItem]:
    """Parse Hugging Face model-list API payload (JSON string or list) into FeedItems."""
    if isinstance(payload, (str, bytes, bytearray)):
        data = json.loads(payload)
    else:
        data = payload
    if not isinstance(data, list):
        return []

    result: list[FeedItem] = []
    fetched = now_utc()
    for row in data:
        if not isinstance(row, dict):
            continue
        model_id = str(row.get("modelId") or row.get("id") or "").strip()
        if not model_id:
            continue
        last_modified = parse_date(str(row.get("lastModified") or row.get("createdAt") or ""))
        tags = row.get("tags") if isinstance(row.get("tags"), list) else []
        siblings = row.get("siblings") if isinstance(row.get("siblings"), list) else []
        summary_bits: list[str] = []
        pipeline_tag = row.get("pipeline_tag") or row.get("pipelineTag")
        if pipeline_tag:
            summary_bits.append(f"pipeline={pipeline_tag}")
        if tags:
            summary_bits.append("tags=" + ", ".join(str(x) for x in tags[:8]))
        if row.get("downloads") is not None:
            summary_bits.append(f"downloads={row.get('downloads')}")
        if row.get("likes") is not None:
            summary_bits.append(f"likes={row.get('likes')}")
        if siblings:
            names = [str(sib["rfilename"]) for sib in siblings[:4] if isinstance(sib, dict) and sib.get("rfilename")]
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
                fetched_at=fetched,
                raw={"final_url": final_url, "kind": "hf_models", "model_id": model_id},
            )
        )
    return result


def _google_news_locale(source: Source) -> tuple[str, str, str]:
    if source.region.lower() in {"cn", "zh", "china"}:
        return "zh-CN", "CN", "CN:zh-Hans"
    return "en-US", "US", "US:en"


def _google_news_query(query: str, lookback_hours: int | None = None) -> str:
    cleaned = clean_text(query)
    if re.search(r"\b(when:\d+[hdwmy]|after:\d{4}-\d{2}-\d{2}|before:\d{4}-\d{2}-\d{2})\b", cleaned, re.I):
        return cleaned
    hours = max(1, int(lookback_hours or 24))
    if hours <= 100:
        return f"{cleaned} when:{hours}h"
    since = now_utc() - timedelta(hours=hours)
    return f"{cleaned} after:{since:%Y-%m-%d}"


def _google_news_url(source: Source, lookback_hours: int | None = None) -> str:
    language, country, ceid = _google_news_locale(source)
    params = {
        "q": _google_news_query(source.url, lookback_hours),
        "hl": language,
        "gl": country,
        "ceid": ceid,
    }
    return f"{GOOGLE_NEWS_SEARCH_URL}?{urlencode(params)}"


def _source_child(el: ET.Element) -> tuple[str, str]:
    for child in list(el):
        if _tag_name(child.tag) == "source":
            return clean_text("".join(child.itertext()), 120), str(child.attrib.get("url") or "")
    return "", ""


def parse_google_news(xml_text: str, source: Source, final_url: str = "") -> list[FeedItem]:
    """Parse Google News RSS search XML into FeedItems attributed to each publisher."""
    text = xml_text or ""
    start = text.find("<")
    if start > 0:
        text = text[start:]
    try:
        root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError:
        return parse_feed(text, source, final_url)

    result: list[FeedItem] = []
    fetched = now_utc()
    for el in _all_items(root):
        title = clean_text(_child_text(el, ["title"]), 240)
        link = _child_text(el, ["link"]).strip()
        guid = clean_text(_child_text(el, ["guid", "id"])) or link or stable_hash([source.id, title])
        summary = clean_text(_child_text(el, ["description", "summary", "content", "encoded"]), 1200)
        published = _normalize_published_at(
            source,
            parse_date(_child_text(el, ["pubDate", "published", "updated", "date"])),
        )
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
                fetched_at=fetched,
                raw={
                    "final_url": final_url,
                    "kind": "google_news",
                    "query": source.url,
                    "publisher": publisher,
                    "publisher_url": publisher_url,
                },
            )
        )
    return result


def _same_site(base_url: str, candidate_url: str) -> bool:
    base = url_host(base_url)
    candidate = url_host(candidate_url)
    if not base or not candidate:
        return False
    return candidate == base or candidate.endswith("." + base) or base.endswith("." + candidate)


def _strip_html_lines(value: str, limit: int | None = None) -> str:
    text = re.sub(r"(?is)<(script|style|svg|noscript)\b.*?</\1>", " ", value or "")
    text = re.sub(r"(?i)</(p|li|h1|h2|h3|h4|h5|h6|br)>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = text.replace("\u200b", " ").replace("\ufeff", " ").replace("\u2060", " ")
    text = re.sub(r"(?<=\s)\?(?=\s|$)", " ", text)
    lines = [clean_text(line) for line in re.split(r"[\n\r]+", html_lib.unescape(text))]
    cleaned = "\n".join(line for line in lines if line)
    if limit and len(cleaned) > limit:
        return cleaned[:limit].rstrip()
    return cleaned


def _attr_value(attrs: str, name: str) -> str:
    match = re.search(rf"\b{re.escape(name)}=[\"']([^\"']+)[\"']", attrs or "", flags=re.I)
    return html_lib.unescape(match.group(1).strip()) if match else ""


def _line_looks_like_date(line: str) -> bool:
    return re.search(WEB_DISCOVERY_DATE_PATTERN, line or "", flags=re.I) is not None


def _has_model_version_signal(text: str) -> bool:
    if MODEL_TOKEN_RE.search(text or ""):
        return True
    return bool(
        re.search(
            r"\b(?:hy|seed|seedance|seedream|seed3d)[-_\s]?v?\d[\w.-]*\b",
            text or "",
            re.I,
        )
    )


def _line_is_generic(line: str) -> bool:
    text = clean_text(line).strip(" \t\r\n?:|｜-/：")
    lowered = text.lower()
    if len(text) < 2 or lowered.startswith("skip to "):
        return True
    if re.search(r"icp备|公网安备|copyright|all rights reserved", text, flags=re.I):
        return True
    without_boilerplate = re.sub(WEB_DISCOVERY_DATE_PATTERN, "", text, flags=re.I)
    without_boilerplate = re.sub(r"[\s·|｜:：/\-_.]+", "", without_boilerplate)
    for label in ("了解更多", "查看更多", "查看全部"):
        without_boilerplate = without_boilerplate.replace(label, "")
    if _line_looks_like_date(text) and not without_boilerplate:
        return True
    if without_boilerplate and (
        without_boilerplate.lower() in WEB_DISCOVERY_GENERIC_LABELS
        or without_boilerplate in WEB_DISCOVERY_GENERIC_LABELS
    ):
        return True
    return not text or lowered in WEB_DISCOVERY_GENERIC_LABELS or text in WEB_DISCOVERY_GENERIC_LABELS


def _html_link_context(html_text: str, start: int, end: int, own_text: str) -> str:
    left = max(0, start - 5000)
    right = min(len(html_text), end + 5000)
    window = html_text[left:right]
    relative_start = start - left
    own = clean_text(own_text)
    candidates: list[tuple[int, int, str]] = []
    tag_priority = {"article": 70, "li": 60, "section": 54, "tr": 48, "div": 42, "td": 34, "p": 12}
    semantic_attr_re = re.compile(
        r"(?:card|post|article|news|blog|item|entry|changelog|release|update|announcement)",
        flags=re.I,
    )
    for tag, base_score in tag_priority.items():
        open_re = re.compile(rf"<{tag}\b(?P<attrs>[^>]*)>", flags=re.I | re.S)
        close_re = re.compile(rf"</{tag}>", flags=re.I)
        for open_match in reversed(list(open_re.finditer(window[:relative_start]))):
            close_match = close_re.search(window[relative_start:])
            block_end = relative_start + close_match.end() if close_match else min(len(window), relative_start + 1800)
            context = _strip_html_lines(window[open_match.start() : block_end], 1400)
            if not context or (own and len(context) <= len(own) + 8):
                continue
            score = base_score
            if semantic_attr_re.search(open_match.group("attrs") or ""):
                score += 28
            if _line_looks_like_date(context):
                score += 18
            if _has_model_version_signal(context):
                score += 16
            lowered = context.lower()
            if any(kw.lower() in lowered for kw in WEB_DISCOVERY_AI_KEYWORDS):
                score += 10
            if any(kw.lower() in lowered for kw in WEB_DISCOVERY_EVENT_KEYWORDS):
                score += 10
            line_count = len([ln for ln in re.split(r"[\n\r]+", context) if clean_text(ln)])
            score += 14 if line_count >= 3 else (8 if line_count >= 2 else 0)
            score += 8 if 40 <= len(context) <= 900 else (-25 if len(context) > 1200 else 0)
            distance = max(0, relative_start - open_match.end())
            score += max(0, 24 - distance // 220)
            candidates.append((score, -distance, context))
    if candidates:
        candidates.sort(reverse=True, key=lambda item: (item[0], item[1], len(item[2])))
        return candidates[0][2]
    context = _strip_html_lines(window, 1400)
    return "" if (own and len(context) <= len(own) + 8) else context


def _html_link_records(html_text: str, base_url: str) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for match in re.finditer(r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", html_text, flags=re.I | re.S):
        href_raw = _attr_value(match.group("attrs") or "", "href")
        if not href_raw:
            continue
        href = urljoin(base_url, href_raw)
        text = _strip_html_lines(match.group("body") or "", 500)
        context = _html_link_context(html_text, match.start(), match.end(), text)
        records.append({"href": href, "text": text, "context": context})
    return records


def _docusaurus_changelog_records(html_text: str, base_url: str) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    h2_re = re.compile(r"<h2\b(?P<attrs>[^>]*)>(?P<title>.*?)</h2>", flags=re.I | re.S)
    h3_re = re.compile(r"<h3\b(?P<attrs>[^>]*)>(?P<title>.*?)</h3>", flags=re.I | re.S)
    h2_matches = list(h2_re.finditer(html_text or ""))
    for idx, h2 in enumerate(h2_matches):
        section_end = h2_matches[idx + 1].start() if idx + 1 < len(h2_matches) else len(html_text)
        section = html_text[h2.end() : section_end]
        date_text = _strip_html_lines(h2.group("title"), 80)
        date_match = re.search(WEB_DISCOVERY_DATE_PATTERN, date_text, flags=re.I)
        if not date_match:
            continue
        h3_matches = list(h3_re.finditer(section))
        for h3_idx, h3 in enumerate(h3_matches):
            item_end = h3_matches[h3_idx + 1].start() if h3_idx + 1 < len(h3_matches) else len(section)
            item_html = section[h3.start() : item_end]
            title = _strip_html_lines(h3.group("title"), 120)
            if not title or _line_is_generic(title):
                continue
            href = ""
            news_candidates: list[tuple[str, str]] = []
            for link in re.finditer(r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", item_html, flags=re.I | re.S):
                body = _strip_html_lines(link.group("body"), 120).lower()
                candidate = _attr_value(link.group("attrs"), "href")
                if not candidate:
                    continue
                candidate_url = urljoin(base_url, candidate)
                if "documentation" in body or "文档" in body:
                    href = candidate_url
                    break
                if "/news/" in urlparse(candidate_url).path.lower():
                    news_candidates.append((candidate_url, body))
            if not href:
                norm_title = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", title.lower())
                for candidate_url, body in news_candidates:
                    norm_body = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", body)
                    if norm_title and norm_body and (norm_title in norm_body or norm_body in norm_title):
                        href = candidate_url
                        break
            if not href:
                anchor = _attr_value(h3.group("attrs"), "id")
                href = urljoin(base_url, "#" + anchor) if anchor else base_url
            summary = _strip_html_lines(item_html, 1200)
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


def _rendered_link_records(source: Source, user_agent: str | None = None) -> tuple[list[dict[str, str]], str, str]:
    try:
        from .browser import load_sync_playwright

        sync_playwright, _ = load_sync_playwright()
    except Exception as exc:
        return [], source.url, f"playwright_unavailable: {type(exc).__name__}: {exc}"

    exe = find_browser_executable()
    try:
        with sync_playwright() as p:
            launch_kwargs: dict[str, Any] = {"headless": True}
            if exe is not None:
                launch_kwargs["executable_path"] = str(exe)
            browser = p.chromium.launch(**launch_kwargs)
            page = browser.new_page(
                viewport={"width": 1360, "height": 900},
                user_agent=user_agent or f"{DEFAULT_USER_AGENT} (official web discovery)",
                locale="zh-CN",
            )
            page.goto(source.url, wait_until="domcontentloaded", timeout=WEB_DISCOVERY_RENDER_TIMEOUT_MS)
            try:
                page.wait_for_load_state("networkidle", timeout=7_000)
            except Exception:
                pass
            page.wait_for_timeout(1_200)
            final_url = page.url
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
            return [dict(x) for x in records if isinstance(x, dict)], final_url, ""
    except Exception as exc:
        return [], source.url, f"render_error: {type(exc).__name__}: {exc}"


def _is_same_page_fragment(base_url: str, candidate_url: str) -> bool:
    base_clean, _ = urldefrag(base_url or "")
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
        "/api", "/blog", "/blogs", "/developers", "/developer", "/platform", "/console",
        "/chat", "/news", "/updates", "/claude", "/models", "/model", "/pricing",
        "/products", "/product", "/contact", "/about",
    }:
        return True
    if re.fullmatch(r"/(?:zh|en|zh-cn|en-us)/(?:models?|products?|product|about|contact|pricing)", normalized):
        return True
    if normalized.startswith(("/pricing/", "/contact/", "/about/")):
        return True
    if "pricing" in path and not any(tok in path for tok in ("/news/", "/blog/", "/research/", "/release")):
        return True
    return any(
        tok in path
        for tok in (
            "/careers", "/career", "/jobs", "/job", "/talent", "/hiring",
            "/login", "/auth", "/signin", "/signup", "/privacy", "/policies",
            "/terms", "/policy", "privacy-policy", "/legal",
        )
    )


def _title_score(line: str, source: Source) -> int:
    lowered = line.lower()
    score = -2 if _line_looks_like_date(line) else 0
    if any(k.lower() in lowered for k in WEB_DISCOVERY_AI_KEYWORDS):
        score += 8
    if any(k.lower() in lowered for k in WEB_DISCOVERY_EVENT_KEYWORDS):
        score += 6
    for topic in source.topics:
        t = str(topic or "").strip().lower()
        if len(t) >= 2 and t in lowered:
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


def _title_from_url_slug(href: str) -> str:
    try:
        parsed = urlparse(href)
    except Exception:
        return ""
    generic_parts = {
        "ai", "blog", "blogs", "news", "research", "models", "model", "products",
        "product", "developer", "developers", "docs", "articles", "post", "posts", "technology",
    }
    parts = [unquote(part).strip() for part in parsed.path.split("/") if part.strip()]
    candidates = [part for part in parts if part.lower() not in generic_parts]
    if not candidates:
        return ""
    slug = re.sub(r"\.(?:html?|mdx?|php)$", "", candidates[-1], flags=re.I)
    if not slug or _line_is_generic(slug):
        return ""
    word_stop = {"official", "launch", "introducing", "introduction", "release", "released", "of", "the", "a", "an"}
    words = [part for part in re.split(r"[-_]+", slug) if part and part.lower() not in word_stop]
    if not words:
        return ""
    aliases = {
        "gpt": "GPT", "api": "API", "ai": "AI", "glm": "GLM", "qwen": "Qwen",
        "kimi": "Kimi", "llama": "Llama", "mistral": "Mistral", "hy": "Hy",
        "hunyuan": "Hunyuan", "deepseek": "DeepSeek", "gemini": "Gemini",
        "gemma": "Gemma", "grok": "Grok", "minimax": "MiniMax", "doubao": "Doubao",
        "seed": "Seed", "seedance": "Seedance", "seedream": "Seedream", "seed3d": "Seed3D",
    }

    def _pretty(word: str) -> str:
        low = word.lower()
        if low in aliases:
            return aliases[low]
        if re.fullmatch(r"[a-z]{1,4}\d[\w.]*", low):
            return word.upper()
        return word.upper() if word.isupper() else word.capitalize()

    title = " ".join(_pretty(w) for w in words)
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
        prev_candidates = [
            (rank, -len(cand), cand)
            for prev in raw_lines[max(0, idx - 6) : idx]
            if (cand := _clean_discovery_title_candidate(prev)) and not _line_is_generic(cand)
            and (rank := _title_candidate_rank(cand, source)) >= 5
        ]
        if prev_candidates:
            prev_candidates.sort(reverse=True)
            return prev_candidates[0][2]

        same_cand = _clean_discovery_title_candidate(raw_line)
        date_prefix = re.match(WEB_DISCOVERY_DATE_PATTERN, raw_line, flags=re.I)
        post_date = raw_line[date_prefix.end() :] if date_prefix else ""
        is_section = post_date.lstrip().startswith(("|", "｜", "-", "—", "·")) and not (
            _has_model_version_signal(same_cand)
            or any(kw.lower() in same_cand.lower() for kw in WEB_DISCOVERY_EVENT_KEYWORDS)
        )
        if same_cand and not _line_is_generic(same_cand) and not is_section:
            return same_cand

        next_candidates: list[tuple[int, int, str]] = []
        for next_line in raw_lines[idx + 1 : idx + 5]:
            cand = _clean_discovery_title_candidate(next_line)
            if cand and not _line_is_generic(cand):
                if len(cand) <= 110 or _has_model_version_signal(cand):
                    return cand
                next_candidates.append((_title_candidate_rank(cand, source), -len(cand), cand))
        if next_candidates:
            next_candidates.sort(reverse=True)
            return next_candidates[0][2]

    lines = [
        cand
        for x in raw_lines
        if x and not _line_is_generic(x) and (cand := _clean_discovery_title_candidate(x)) and not _line_is_generic(cand)
    ]
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
        if not line or line == title or _line_is_generic(line) or _line_looks_like_date(line):
            continue
        norm = re.sub(r"\W+", "", line.lower())
        if not norm or norm in seen:
            continue
        seen.add(norm)
        result.append(line)
        if len("；".join(result)) >= 520:
            break
    return clean_text("；".join(result), 600)


def _discovery_relevant(title: str, summary: str, href: str, source: Source) -> bool:
    if not title or _line_is_generic(title):
        return False
    content_haystack = clean_text(f"{title} {summary} {href}").lower()
    source_haystack = clean_text(f"{source.name} {' '.join(source.topics)}").lower()
    generic_topics = {"ai", "api", "model", "models", "模型", "开源", "open models", "open source"}
    topic_hit = any(
        t in content_haystack
        for t in (str(topic or "").strip().lower() for topic in source.topics)
        if len(t) >= 2 and t not in generic_topics
    )
    ai_hit = any(kw.lower() in content_haystack for kw in WEB_DISCOVERY_AI_KEYWORDS)
    event_hit = any(kw.lower() in content_haystack for kw in WEB_DISCOVERY_EVENT_KEYWORDS)
    version_hit = _has_model_version_signal(content_haystack)
    date_hit = re.search(WEB_DISCOVERY_DATE_PATTERN, content_haystack, flags=re.I) is not None
    if urlparse(href).path.lower().startswith(("/guides/", "/quick_start/")) and not (date_hit or version_hit):
        return False
    source_is_ai = any(kw.lower() in source_haystack for kw in WEB_DISCOVERY_AI_KEYWORDS)
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
        for field in ("text", "context"):
            value = str(record.get(field) or "").strip()
            if value and value not in merged[key][field]:
                merged[key][field] = (merged[key][field] + "\n" + value).strip()
        date_text = str(record.get("date_text") or "").strip()
        if date_text and not merged[key]["_date_conflict"]:
            if not merged[key]["date_text"]:
                merged[key]["date_text"] = date_text
                merged[key]["date_provenance"] = str(record.get("date_provenance") or "").strip()
            elif merged[key]["date_text"] != date_text:
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
    matches = list(re.finditer(WEB_DISCOVERY_DATE_PATTERN, raw_text or "", flags=re.I))
    first_line = next((line.strip() for line in (raw_text or "").splitlines() if line.strip()), "")
    first_line_match = re.search(WEB_DISCOVERY_DATE_PATTERN, first_line, flags=re.I)
    if first_line_match and first_line_match.start() <= 8:
        date_text = first_line_match.group(0).replace("年", "-").replace("月", "-").replace("日", "")
        return _normalize_published_at(source, parse_date(date_text)), "record_heading"
    normalized: list[tuple[str, int, int]] = []
    for match in matches:
        val = match.group(0).replace("年", "-").replace("月", "-").replace("日", "")
        parsed = parse_date(val)
        if not parsed:
            continue
        key = parsed.date().isoformat()
        if not any(existing[0] == key for existing in normalized):
            normalized.append((key, match.start(), match.end()))
    if len(normalized) != 1:
        return None, "ambiguous_record_date" if normalized else ""
    _key, start, end = normalized[0]
    compact_title = clean_text(title)
    if compact_title and len(raw_text) > 420:
        title_pos = raw_text.lower().find(compact_title.lower())
        if title_pos < 0 or min(abs(title_pos - start), abs(title_pos - end)) > 240:
            return None, "unanchored_record_date"
    date_text = raw_text[start:end].replace("年", "-").replace("月", "-").replace("日", "")
    return _normalize_published_at(source, parse_date(date_text)), "record_context"


def _parse_web_discovery_records(records: list[dict[str, str]], source: Source, final_url: str = "") -> list[FeedItem]:
    result: list[FeedItem] = []
    seen: set[str] = set()
    fetched = now_utc()
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
            raw_text = f"{raw_text}\n{context_text}"
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
                guid=stable_hash([source.id, key]),
                summary=summary,
                published_at=published,
                fetched_at=fetched,
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
            _title_score(f"{item.title} {item.summary} {item.link}", source),
        ),
        reverse=True,
    )
    return result[:WEB_DISCOVERY_ITEM_LIMIT]


def _web_discovery_items_need_render(items: list[FeedItem], source: Source) -> bool:
    if not items:
        return True
    for item in items:
        title = clean_text(item.title)
        if _line_is_generic(title) or len(title) > 110 or _title_candidate_rank(title, source) < 5:
            return True
    return False


def discover_web_source(
    source: Source,
    *,
    defaults: dict[str, Any] | None = None,
) -> tuple[SourceHealth, list[FeedItem]]:
    """Discover announcements from an official website (static HTML + Docusaurus + Playwright fallback)."""
    started = time.perf_counter()
    cfg_defaults = defaults or {}
    user_agent = str(cfg_defaults.get("user_agent") or DEFAULT_USER_AGENT)
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    status_code: int | None = None
    final_url = source.url
    error = ""
    records: list[dict[str, str]] = []
    try:
        resp = requests.get(source.url, headers=headers, timeout=source.timeout_seconds, allow_redirects=True)
        status_code = resp.status_code
        final_url = resp.url
        if resp.encoding is None or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding or "utf-8"
        if resp.ok:
            records = _html_link_records(resp.text, resp.url)
            records.extend(_docusaurus_changelog_records(resp.text, resp.url))
        else:
            error = resp.text[:300]
    except Exception as exc:
        error = f"requests: {type(exc).__name__}: {exc}"

    items = _parse_web_discovery_records(records, source, final_url)
    if _web_discovery_items_need_render(items, source):
        rendered_records, rendered_final_url, rendered_error = _rendered_link_records(source, user_agent=user_agent)
        if rendered_final_url:
            final_url = rendered_final_url
        rendered_items = _parse_web_discovery_records(rendered_records, source, final_url)
        if rendered_items:
            items = rendered_items
            error = ""
        elif rendered_error:
            error = f"{error}; {rendered_error}".strip("; ")

    latest = max([x.published_at for x in items if x.published_at], default=None)
    no_date = sum(1 for x in items if not x.published_at)
    latency = int((time.perf_counter() - started) * 1000)
    status = "ok" if items else ("no_items" if not error else "error")
    return (
        SourceHealth(
            source_id=source.id,
            source_name=source.name,
            tier=source.tier,
            enabled=source.enabled,
            status=status,
            status_code=status_code,
            item_count=len(items),
            latest_item_at=latest,
            latency_ms=latency,
            error=error[:300],
            final_url=final_url,
            stale=False,
            no_date_count=no_date,
        ),
        items,
    )


def load_agent_social_source(
    source: Source,
    *,
    defaults: dict[str, Any] | None = None,
) -> tuple[SourceHealth, list[FeedItem]]:
    """Load fresh, schema- and lane-audit-verified X leads produced by Codex."""
    started = time.perf_counter()
    cfg_defaults = defaults or {}
    lookback_hours = int(cfg_defaults.get("lookback_hours") or 24)
    raw_path, _, fragment = source.url.partition("#")
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = project_root() / path
    allowed_accounts = {val.strip().lstrip("@").lower() for val in fragment.split(",") if val.strip()}

    try:
        if not path.exists():
            raise FileNotFoundError(str(path))
        payload = read_json(path)
        if not isinstance(payload, dict) or payload.get("agent") != "codex" or int(payload.get("version") or 0) != 1:
            raise ValueError("lead file must be version 1 and agent=codex")
        generated_at = parse_date(str(payload.get("generated_at") or ""))
        if generated_at is None:
            raise ValueError("lead file has no generated_at")
        now = now_utc()
        max_file_age = max(12, lookback_hours)
        if generated_at > now + timedelta(minutes=10) or now - generated_at > timedelta(hours=max_file_age):
            raise ValueError("lead file is stale or future-dated")
        lane_audits = payload.get("lane_audits") or {}
        lane_audit = lane_audits.get(source.id) if isinstance(lane_audits, dict) else None
        if not isinstance(lane_audit, dict) or lane_audit.get("status") != "checked":
            raise ValueError(f"missing fresh checked lane audit for {source.id}")
        checked_at = parse_date(str(lane_audit.get("checked_at") or ""))
        if checked_at is None or checked_at > now + timedelta(minutes=10) or now - checked_at > timedelta(hours=max_file_age):
            raise ValueError(f"lane audit for {source.id} is stale or future-dated")
        accounts_checked = {
            str(val or "").strip().lstrip("@").lower()
            for val in (lane_audit.get("accounts_checked") or [])
            if str(val or "").strip()
        }
        missing_accounts = sorted(allowed_accounts - accounts_checked)
        if missing_accounts:
            raise ValueError(f"lane audit did not check configured accounts: {', '.join(missing_accounts)}")

        cutoff = generated_at - timedelta(hours=max_file_age)
        items: list[FeedItem] = []
        for row in payload.get("items") or []:
            if not isinstance(row, dict) or str(row.get("source_id") or "") != source.id:
                continue
            link = str(row.get("url") or "").strip()
            parsed_status = parse_x_status(link)
            if parsed_status is None:
                continue
            account, status_id = parsed_status
            if allowed_accounts and account not in allowed_accounts:
                continue
            title = clean_text(str(row.get("title") or ""), 180)
            summary = clean_text(str(row.get("text") or row.get("summary") or ""), 1600)
            published_at = parse_date(str(row.get("published_at") or ""))
            checks = row.get("checks") or {}
            checked_urls = {s for val in (row.get("checked_urls") or []) if (s := str(val or "").strip())}
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
                    link=canonical_url(link),
                    guid=f"codex-social:{source.id}:{status_id}",
                    summary=summary,
                    published_at=published_at,
                    raw={
                        "kind": "codex_agent_social_lead",
                        "agent_generated_at": generated_at.isoformat(),
                        "discovery_url": discovery_url if discovery_url and discovery_url != link else "",
                        "account": account,
                        "status_id": status_id,
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
        return (
            SourceHealth(source.id, source.name, source.tier, source.enabled, "ok", None, len(items), latest, latency, "", str(path), False, 0),
            items,
        )
    except Exception as exc:
        latency = int((time.perf_counter() - started) * 1000)
        return (
            SourceHealth(
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
            ),
            [],
        )


def _fetch_single_source(
    source: Source,
    *,
    defaults: dict[str, Any] | None = None,
) -> tuple[SourceHealth, list[FeedItem]]:
    started = time.perf_counter()
    cfg_defaults = defaults or {}
    user_agent = str(cfg_defaults.get("user_agent") or DEFAULT_USER_AGENT)
    lookback_hours = int(cfg_defaults.get("lookback_hours") or 24)
    headers = {
        "User-Agent": user_agent,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, text/html;q=0.8, */*;q=0.5",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    try:
        if not source.enabled:
            return SourceHealth(source.id, source.name, source.tier, False, "disabled", final_url=source.url), []
        if source.type == AGENT_SOCIAL_TYPE:
            return load_agent_social_source(source, defaults=cfg_defaults)
        if source.type in WEB_DISCOVERY_TYPES:
            return discover_web_source(source, defaults=cfg_defaults)
        if source.type == "hf_models":
            resp = requests.get(
                source.url,
                headers={**headers, "Accept": "application/json"},
                timeout=source.timeout_seconds,
                allow_redirects=True,
            )
            latency = int((time.perf_counter() - started) * 1000)
            if not resp.ok:
                return SourceHealth(source.id, source.name, source.tier, source.enabled, "http_error", resp.status_code, 0, None, latency, resp.text[:300], resp.url, False, 0), []
            items = parse_hf_models(resp.json(), source, resp.url)
            latest = max([x.published_at for x in items if x.published_at], default=None)
            no_date = sum(1 for x in items if not x.published_at)
            stale = bool(latest and (now_utc() - latest).total_seconds() / 3600 > 24 * 14)
            return SourceHealth(source.id, source.name, source.tier, source.enabled, "ok" if items else "no_items", resp.status_code, len(items), latest, latency, "", resp.url, stale, no_date), items
        if source.type == "google_news":
            url = _google_news_url(source, lookback_hours=lookback_hours)
            resp = requests.get(url, headers=headers, timeout=source.timeout_seconds, allow_redirects=True)
            latency = int((time.perf_counter() - started) * 1000)
            if not resp.ok:
                return SourceHealth(source.id, source.name, source.tier, source.enabled, "http_error", resp.status_code, 0, None, latency, resp.text[:300], resp.url, False, 0), []
            items = parse_google_news(resp.text, source, resp.url)
            latest = max([x.published_at for x in items if x.published_at], default=None)
            no_date = sum(1 for x in items if not x.published_at)
            return SourceHealth(source.id, source.name, source.tier, source.enabled, "ok" if items else "no_items", resp.status_code, len(items), latest, latency, "", resp.url, False, no_date), items
        if source.type not in {"rss", "atom", "rsshub"}:
            return SourceHealth(source.id, source.name, source.tier, source.enabled, "unsupported_type", None, 0, None, 0, f"unsupported source type: {source.type}", source.url, False, 0), []

        resp = requests.get(source.url, headers=headers, timeout=source.timeout_seconds, allow_redirects=True)
        latency = int((time.perf_counter() - started) * 1000)
        if resp.encoding is None or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding or "utf-8"
        if not resp.ok:
            return SourceHealth(source.id, source.name, source.tier, source.enabled, "http_error", resp.status_code, 0, None, latency, resp.text[:300], resp.url, False, 0), []
        items = parse_feed(resp.text, source, resp.url)
        if "github.com" in source.url.lower() and "releases" in source.url.lower():
            enrich_github_release_items(items, timeout=min(source.timeout_seconds, 10), user_agent=user_agent)
        latest = max([x.published_at for x in items if x.published_at], default=None)
        no_date = sum(1 for x in items if not x.published_at)
        stale = bool(latest and (now_utc() - latest).total_seconds() / 3600 > 24 * 14)
        return SourceHealth(source.id, source.name, source.tier, source.enabled, "ok" if items else "no_items", resp.status_code, len(items), latest, latency, "", resp.url, stale, no_date), items
    except Exception as exc:
        latency = int((time.perf_counter() - started) * 1000)
        return SourceHealth(source.id, source.name, source.tier, source.enabled, "error", None, 0, None, latency, f"{type(exc).__name__}: {exc}"[:300], source.url, False, 0), []


def collect_sources(
    sources: list[Source],
    *,
    max_workers: int = 12,
    defaults: dict[str, Any] | None = None,
) -> tuple[list[SourceHealth], list[FeedItem]]:
    """Fetch all enabled sources concurrently (with sequential web-discovery fallback)."""
    health: list[SourceHealth] = []
    items: list[FeedItem] = []
    if not sources:
        return health, items

    web_sources = [s for s in sources if s.type in WEB_DISCOVERY_TYPES]
    normal_sources = [s for s in sources if s.type not in WEB_DISCOVERY_TYPES]

    if normal_sources:
        with cf.ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as pool:
            futures = [pool.submit(_fetch_single_source, s, defaults=defaults) for s in normal_sources]
            for fut in cf.as_completed(futures):
                h, got = fut.result()
                health.append(h)
                items.extend(got)

    for source in web_sources:
        h, got = _fetch_single_source(source, defaults=defaults)
        health.append(h)
        items.extend(got)

    health.sort(key=lambda h: (h.tier, h.source_name))
    items.sort(key=lambda it: it.published_at or it.fetched_at, reverse=True)
    return health, items

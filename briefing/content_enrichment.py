from __future__ import annotations

import asyncio
import base64
import html
import json
import mimetypes
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

import requests

from .models import FeedItem
from .util import clean_text, ensure_dir, parse_date, stable_hash

UTC = timezone.utc

ARTICLE_TIMEOUT_SECONDS = 18
ARTICLE_TEXT_LIMIT = 9000
SUMMARY_LIMIT = 1800
ARTICLE_SOURCE_DATE_CONFLICT_HOURS = 12
DEFAULT_IMAGE_LIMIT = 5
ADAPTER_TEXT_MIN_CHARS = 1200
ADAPTER_FACT_MIN_COUNT = 3
SPA_JS_FETCH_LIMIT = 14
SPA_JSON_FETCH_LIMIT = 8
SPA_ASSET_BYTES_LIMIT = 1_600_000

# 不把“腾讯/Google/Meta”这类泛公司名单独当作 AI 信号，避免财经新闻混入。
MAINSTREAM_AI_KEYWORDS = [
    "openai",
    "chatgpt",
    "gpt-",
    "codex",
    "sora",
    "anthropic",
    "claude",
    "gemini",
    "gemma",
    "deepmind",
    "google ai",
    "qwen",
    "通义",
    "千问",
    "deepseek",
    "kimi",
    "moonshot",
    "豆包",
    "doubao",
    "bytedance seed",
    "字节 seed",
    "hunyuan",
    "混元",
    "hy3",
    "zhipu",
    "智谱",
    "glm",
    "minimax",
    "mistral",
    "llama",
    "grok",
    "xai",
    "hugging face",
    "huggingface",
    "modelscope",
    "nvidia",
    "blackwell",
    "copilot",
    "vllm",
    "ollama",
    "llama.cpp",
]

AI_CONTEXT_KEYWORDS = [
    "ai",
    "人工智能",
    "大模型",
    "模型",
    "agent",
    "智能体",
    "推理",
    "多模态",
    "语音",
    "视频生成",
    "开源",
    "权重",
    "benchmark",
    "榜单",
    "评测",
    "api",
    "token",
]

FACT_KEYWORDS = [
    "\u53d1\u5e03",
    "\u4e0a\u7ebf",
    "\u63a8\u51fa",
    "\u66f4\u65b0",
    "\u65b0\u589e",
    "\u4fee\u590d",
    "API",
    "\u4ef7\u683c",
    "\u514d\u8d39",
    "\u53c2\u6570",
    "\u4e0a\u4e0b\u6587",
    "\u591a\u6a21\u6001",
    "Agent",
    "\u667a\u80fd\u4f53",
    "benchmark",
    "\u699c\u5355",
    "\u8bc4\u6d4b",
    "\u6027\u80fd",
    "\u5f00\u6e90",
    "\u6743\u91cd",
    "TokenHub",
    "Hugging Face",
    "ModelScope",
    "GitHub",
    "license",
    "download",
    "downloads",
    "tokens",
    "context",
    "\u964d\u4f4e",
    "\u63d0\u5347",
    "\u6210\u529f\u7387",
    "\u5ef6\u8fdf",
    "\u65f6\u957f",
    "\u5de5\u4f5c\u6d41",
    "MCP",
    "\u79bb\u804c",
    "\u8f9e\u804c",
    "\u4efb\u547d",
    "\u63a5\u4efb",
    "\u91cd\u7ec4",
    "\u5e76\u5165",
    "\u8d1f\u8d23\u4eba",
    "\u4e3b\u7ba1",
    "\u8bc9\u8bbc",
    "\u8d77\u8bc9",
    "\u6536\u8d2d",
    "\u76d1\u7ba1",
]

# Personnel, corporate-structure and legal actions are often the actual news
# in a product-heavy article. Give them extra weight so a concrete departure
# or lawsuit is not buried below generic release commentary.
HIGH_SIGNAL_FACT_KEYWORDS = [
    "\u79bb\u804c",
    "\u8f9e\u804c",
    "\u4efb\u547d",
    "\u63a5\u4efb",
    "\u91cd\u7ec4",
    "\u5e76\u5165",
    "\u8bc9\u8bbc",
    "\u8d77\u8bc9",
    "\u6536\u8d2d",
    "\u76d1\u7ba1",
]
FACT_ROLE_KEYWORDS = ["\u8d1f\u8d23\u4eba", "\u4e3b\u7ba1"]

SKIP_URL_EXTENSIONS = {
    ".zip",
    ".gz",
    ".tar",
    ".tgz",
    ".7z",
    ".rar",
    ".mp4",
    ".m4s",
    ".mp3",
    ".wav",
    ".exe",
    ".dmg",
}

CRAWL4AI_INSTALL_SPEC = "crawl4ai>=0.9,<1.0"

SPA_THIN_MARKERS = [
    "loading",
    "loading...",
    "加载中",
    "请稍候",
    "enable javascript",
    "javascript is required",
    "app-root",
    "__vite",
]

HY3_CONFIG_URLS = [
    "https://cdn-portal.hunyuan.tencent.com/commonAssets/hy3/hy3-config-zh.json",
    "https://hunyuan-portal-prod-1258344703.cos.ap-guangzhou.myqcloud.com/commonAssets/hy3/pre/hy3-config-zh.json",
]

IMAGE_PRIORITY_TERMS = [
    "gpqa",
    "swebench",
    "swe-bench",
    "agentbench",
    "benchmark",
    "bench",
    "api",
    "price",
    "token",
    "chart",
    "榜",
    "评测",
    "能力",
    "agent",
    "codebuddy",
    "workbuddy",
    "yuanbao",
    "preview",
]


@dataclass(slots=True)
class RuntimeRepairResult:
    attempted: bool = False
    ok: bool = False
    actions: list[str] = field(default_factory=list)
    error: str = ""


@dataclass(slots=True)
class ArticleResult:
    status: str
    url: str
    final_url: str = ""
    status_code: int | None = None
    title: str = ""
    description: str = ""
    text: str = ""
    markdown: str = ""
    facts: list[str] | None = None
    images: list[dict[str, str]] = field(default_factory=list)
    links: list[dict[str, str]] = field(default_factory=list)
    screenshot_path: str = ""
    published_at: datetime | None = None
    error: str = ""
    crawler: str = "requests"
    repair: dict[str, Any] = field(default_factory=dict)


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _has_skip_extension(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in SKIP_URL_EXTENSIONS)


def _looks_mainstream(text: str) -> bool:
    lowered = clean_text(text).lower()
    return any(keyword.lower() in lowered for keyword in MAINSTREAM_AI_KEYWORDS)


def _has_ai_context(text: str) -> bool:
    lowered = clean_text(text).lower()
    return any(keyword.lower() in lowered for keyword in AI_CONTEXT_KEYWORDS)


def _is_official_ai_source(item: FeedItem) -> bool:
    source = " ".join([item.source_id, item.source_name, item.source_reliability, item.source_tier]).lower()
    if item.source_tier.upper() == "A" or item.source_reliability == "official":
        return _looks_mainstream(" ".join([source, item.title, item.summary, item.link])) or any(
            token in source for token in ["openai", "qwen", "deepseek", "hunyuan", "hf_models", "github", "nvidia", "modelscope", "hugging"]
        )
    return False


def should_enrich_item(item: FeedItem) -> bool:
    """Return whether an item deserves article/page crawling.

    RSS/Folo/Google News 只负责发现；进入候选池的网页类条目默认要爬正文、图片和截图。
    这里仍保留一个轻量 AI 相关性门槛，避免通用财经/社会新闻把爬虫额度耗光。
    """
    link = str(item.link or "")
    # X items already contain the post body in the RSS discovery payload.  The
    # original/mirror screenshot is captured after portfolio selection; trying
    # to crawl x.com here wastes the scarce article-enrichment budget.
    if item.source_id.startswith("x_"):
        return False
    if not link.startswith(("http://", "https://")) or _has_skip_extension(link):
        return False
    haystack = " ".join([item.title, item.summary, item.source_name, item.source_id, link])
    if _is_official_ai_source(item):
        return True
    if _looks_mainstream(haystack):
        return True
    if (item.source_tier.upper() in {"B", "C"} or item.source_reliability in {"media", "community", "aggregator"}) and _has_ai_context(haystack):
        return True
    return False


def enrichment_priority(item: FeedItem) -> tuple[int, int, int, int, int, str]:
    haystack = " ".join([item.title, item.summary, item.source_name, item.source_id, item.link])
    mainstream = 1 if _looks_mainstream(haystack) else 0
    official = 1 if _is_official_ai_source(item) else 0
    media = 1 if item.source_tier.upper() == "B" or item.source_reliability == "media" else 0
    community = 1 if item.source_tier.upper() == "C" or item.source_reliability in {"community", "aggregator"} else 0
    source_id = item.source_id.lower()
    high_volume_artifact = 1 if source_id.endswith("_hf_models") or source_id.startswith("arxiv_") else 0
    dated = 1 if item.published_at else 0
    # Prioritise dated, mainstream editorial/product pages. High-volume paper
    # and model-repository feeds cannot consume the whole crawl budget before
    # actual launch articles are enriched.
    return (
        mainstream,
        dated,
        official,
        media,
        -high_volume_artifact - community,
        item.published_at.isoformat() if item.published_at else "",
    )


def _meta_content(html_text: str, names: list[str]) -> str:
    for name in names:
        patterns = [
            rf'<meta[^>]+name=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)["\']',
            rf'<meta[^>]+property=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)["\']',
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']{re.escape(name)}["\']',
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']{re.escape(name)}["\']',
        ]
        for pattern in patterns:
            match = re.search(pattern, html_text, flags=re.I | re.S)
            if match:
                return clean_text(match.group(1))
    return ""


def _meta_contents(html_text: str, names: list[str]) -> list[str]:
    """收集同名元数据的全部值，供发布时间冲突判定使用。"""
    values: list[str] = []
    for name in names:
        patterns = [
            rf'<meta[^>]+name=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)["\']',
            rf'<meta[^>]+property=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)["\']',
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']{re.escape(name)}["\']',
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']{re.escape(name)}["\']',
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, html_text, flags=re.I | re.S):
                value = clean_text(match.group(1))
                if value and value not in values:
                    values.append(value)
    return values


def _published_at_from_html(html_text: str, json_ld: dict[str, Any]) -> datetime | None:
    """汇总 JSON-LD 与页面 meta，冲突时选择最早的真实发布时间。"""
    json_ld_published = parse_date(str(json_ld.get("datePublished") or ""))
    raw_candidates = _meta_contents(
        html_text,
        ["article:published_time", "article:published", "pubdate", "publishdate", "publish_date", "datePublished"],
    )
    inferred_timezone = None
    for raw in raw_candidates:
        try:
            explicit = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if explicit.tzinfo is not None:
            inferred_timezone = explicit.tzinfo
            break
    candidates: list[datetime] = [json_ld_published] if json_ld_published else []
    for raw in raw_candidates:
        parsed = None
        if inferred_timezone is not None:
            try:
                local_value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                local_value = None
            if local_value is not None and local_value.tzinfo is None:
                parsed = local_value.replace(tzinfo=inferred_timezone).astimezone(UTC)
        parsed = parsed or parse_date(raw)
        if parsed and parsed not in candidates:
            candidates.append(parsed)
    # 36氪等页面可能在 JSON-LD 或 meta 中同时输出“当前渲染时间”和真正的
    # 文章时间。滚动早报按最早公开时间判定，避免 SSR 把旧内容重新洗新。
    return min(candidates) if candidates else None


def _title_from_html(html_text: str) -> str:
    meta = _meta_content(html_text, ["og:title", "twitter:title"])
    if meta:
        return meta
    match = re.search(r"<title[^>]*>(.*?)</title>", html_text, flags=re.I | re.S)
    return clean_text(match.group(1)) if match else ""


URL_TRAILING_PUNCTUATION = ".,;:!?\uff0c\u3002\uff1b\uff1a\uff01\uff1f\u3001\u2026\u201d\u2019\u300b\u3009\u300d\u300f"
URL_CLOSING_PAIRS = {")": "(", "]": "[", "}": "{", "\uff09": "\uff08", "\u3011": "\u3010"}


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
    prefix = line[max(0, start - 120):start]
    prefix = re.split(r"[\u3002\uff01\uff1f!?\uff1b;\uff0c,\r\n]", prefix)[-1]
    prefix = re.sub(r"(?:\[\d+\]|\(\d+\))\s*$", "", prefix)
    label = clean_text(prefix, 120).strip(" \t:\uff1a-\u2014\u2013[]()\uff08\uff09")
    return label or urlparse(url).netloc


def extract_outbound_links(html_text: str, base_url: str, limit: int = 80) -> list[dict[str, str]]:
    """Extract traceable HTTP links for later first-party reconciliation.

    Media feeds are discovery leads.  Keeping the article's labelled outbound
    links lets the pipeline follow an event-specific vendor announcement
    without guessing from a broad web search.
    """
    max_links = max(1, limit)
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in re.finditer(
        r"(?is)<a\b[^>]*?href\s*=\s*([\"'])(.*?)\1[^>]*>(.*?)</a>",
        html_text or "",
    ):
        href = html.unescape(match.group(2)).strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
            continue
        url = urljoin(base_url, href)
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        # Fragments do not identify a different source document and make
        # cross-source deduplication unnecessarily brittle.
        url = parsed._replace(fragment="").geturl()
        if url in seen:
            continue
        label_html = re.sub(r"(?is)<(?:script|style|svg)\b.*?</(?:script|style|svg)>", " ", match.group(3))
        label = clean_text(html.unescape(re.sub(r"(?s)<[^>]+>", " ", label_html)), 180)
        seen.add(url)
        links.append({"url": url, "text": label})
        if len(links) >= max_links:
            break

    # Some publishers print source references as plain text (for example,
    # ``[1]https://www.wired.com/...``) rather than as anchors. Scan only the
    # extracted article text so scripts, trackers and unrelated asset URLs do
    # not pollute reconciliation candidates.
    _title, description, article_text, _published_at = extract_article_text(html_text)
    bare_links: list[dict[str, str]] = []
    for line in re.split(r"[\r\n]+", "\n".join([description, article_text])):
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
    # Always reserve room for explicit source URLs even on navigation-heavy
    # pages; those references are more useful than late-page menu anchors.
    kept_bare = bare_links[:max_links]
    return [*links[: max_links - len(kept_bare)], *kept_bare]


def _json_ld_values(html_text: str) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for match in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html_text, flags=re.I | re.S):
        raw = html.unescape(match.group(1)).strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop(0)
            if isinstance(node, list):
                stack.extend(node)
                continue
            if not isinstance(node, dict):
                continue
            for key in ["headline", "name", "description", "articleBody", "datePublished", "dateModified"]:
                if key in node and key not in values:
                    values[key] = node[key]
            for child_key in ["@graph", "mainEntity", "itemListElement"]:
                child = node.get(child_key)
                if isinstance(child, list):
                    stack.extend(child)
                elif isinstance(child, dict):
                    stack.append(child)
    return values


def extract_article_text(html_text: str) -> tuple[str, str, str, datetime | None]:
    json_ld = _json_ld_values(html_text)
    title = clean_text(str(json_ld.get("headline") or json_ld.get("name") or _title_from_html(html_text)))
    description = clean_text(str(json_ld.get("description") or _meta_content(html_text, ["description", "og:description", "twitter:description"])))
    article_body = clean_text(str(json_ld.get("articleBody") or ""))
    published_at = _published_at_from_html(html_text, json_ld)
    if article_body and len(article_body) >= 80:
        return title, description, article_body[:ARTICLE_TEXT_LIMIT], published_at

    body = re.sub(r"(?is)<(script|style|noscript|svg|canvas|iframe)\b.*?</\1>", " ", html_text)
    body = re.sub(r"(?i)</(p|div|section|article|li|h1|h2|h3|br)>", "\n", body)
    body = re.sub(r"(?is)<[^>]+>", " ", body)
    body = html.unescape(body)
    lines: list[str] = []
    seen: set[str] = set()
    for raw in body.splitlines():
        line = clean_text(raw)
        if len(line) < 12:
            continue
        if any(noise in line.lower() for noise in ["cookie", "javascript", "copyright", "隐私", "登录", "注册"]):
            continue
        norm = re.sub(r"\W+", "", line.lower())
        if not norm or norm in seen:
            continue
        seen.add(norm)
        lines.append(line)
        if sum(len(x) for x in lines) >= ARTICLE_TEXT_LIMIT:
            break
    return title, description, "\n".join(lines)[:ARTICLE_TEXT_LIMIT], published_at


def _markdown_to_text(markdown: Any) -> str:
    if not markdown:
        return ""
    if isinstance(markdown, str):
        return markdown
    for attr in ["fit_markdown", "markdown_with_citations", "raw_markdown"]:
        value = getattr(markdown, attr, None)
        if value:
            return str(value)
    return str(markdown)


FACT_NOISE_PATTERNS = [
    r"###\s*step",
    r"\bcall numbers?\b",
    r"\bjuvenile\b",
    r"\byoung adult\b",
    r"\blone wolf\b",
    r"\bdaughters?\b",
    r"\bbooks? belong\b",
    r"reasoning\(.*api",
    r"composite\s*=",
    r"coding\(.*swe",
    r"\bsnowflake\s+new\b",
    r"记录自己日常工作的实践",
    r"个人博客.*公众号.*搬到这里",
    r"阅读完需\s*[:：]",
    r"网友\s*[:：]\s*早知道",
    r"本文来自微信公众号",
]


ROUNDUP_CONTAMINATION_MARKERS = [
    "](",
    "相关阅读",
    "延伸阅读",
    "更多新闻",
    "上一篇",
    "下一篇",
    "热门文章",
    "量子位的朋友们",
    "更多精彩内容",
]


def _is_noisy_fact_sentence(sentence: str) -> bool:
    lowered = sentence.lower()
    if len(sentence) > 220:
        return True
    url_count = lowered.count("http://") + lowered.count("https://")
    if lowered.startswith(("http://", "https://")):
        return True
    if url_count > 1 or (url_count and len(sentence) > 120):
        return True
    if any(marker.lower() in lowered for marker in ROUNDUP_CONTAMINATION_MARKERS):
        return True
    # Navigation/card debris often glues several unrelated headlines into one
    # sentence. It is discovery context, not a fact that may enter narration.
    date_hits = len(re.findall(r"20\d{2}[-/年]\d{1,2}(?:[-/月]\d{1,2})?", sentence))
    linkish_hits = len(re.findall(r"(?:https?://|www\.|\]\(|!\[)", lowered))
    if date_hits >= 2 or linkish_hits >= 2:
        return True
    return any(re.search(pattern, lowered, flags=re.I) for pattern in FACT_NOISE_PATTERNS)


def extract_key_facts(text: str, limit: int = 8) -> list[str]:
    # Preserve paragraph boundaries before ``clean_text`` collapses all
    # whitespace.  Chinese publishers frequently put one complete sentence
    # per HTML block but use ASCII full stops (or no terminal punctuation), so
    # flattening the page first can turn an entire article into a 2,000-char
    # blob and leave navigation debris as the only selectable "facts".
    sentences: list[str] = []
    for raw_line in re.split(r"[\r\n]+", str(text or "")):
        line = clean_text(raw_line)
        if not line:
            continue
        sentences.extend(
            part
            for part in re.split(r"(?<=[。！？!?])\s*|(?<!\d)\.(?=\s+|$)", line)
            if clean_text(part)
        )
    scored: list[tuple[int, str]] = []
    for sentence in sentences:
        s = clean_text(sentence).strip("。；; ")
        if len(s) < 12 or _is_noisy_fact_sentence(s):
            continue
        lowered = s.lower()
        score = sum(3 for keyword in FACT_KEYWORDS if keyword.lower() in lowered)
        score += sum(9 for keyword in HIGH_SIGNAL_FACT_KEYWORDS if keyword.lower() in lowered)
        if any(keyword.lower() in lowered for keyword in HIGH_SIGNAL_FACT_KEYWORDS) and any(
            keyword.lower() in lowered for keyword in FACT_ROLE_KEYWORDS
        ):
            score += 6
        if any(ch.isdigit() for ch in s):
            score += 2
        if _looks_mainstream(s):
            score += 2
        if score <= 0:
            continue
        scored.append((score, s))
    scored.sort(key=lambda row: (row[0], len(row[1])), reverse=True)
    facts: list[str] = []
    seen: set[str] = set()
    for _score, sentence in scored:
        norm = re.sub(r"\W+", "", sentence.lower())
        if norm in seen:
            continue
        seen.add(norm)
        facts.append(sentence)
        if len(facts) >= limit:
            break
    return facts


def _repair_crawl4ai_runtime(reason: str = "") -> RuntimeRepairResult:
    result = RuntimeRepairResult(attempted=True, actions=[])
    commands = [
        [sys.executable, "-m", "pip", "install", CRAWL4AI_INSTALL_SPEC],
        [sys.executable, "-m", "playwright", "install", "chromium"],
    ]
    for cmd in commands:
        label = " ".join(cmd[1:])
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=900)
            tail = (proc.stdout or "")[-500:]
            result.actions.append(f"{label}: exit={proc.returncode}; {tail}")
            if proc.returncode != 0:
                result.error = tail
                result.ok = False
                return result
        except Exception as exc:  # pragma: no cover - defensive repair path
            result.actions.append(f"{label}: {type(exc).__name__}: {exc}")
            result.error = f"{type(exc).__name__}: {exc}"
            result.ok = False
            return result
    result.ok = True
    if reason:
        result.actions.insert(0, f"trigger: {reason[:180]}")
    return result


def _crawl_error_needs_repair(message: str) -> bool:
    lowered = (message or "").lower()
    return any(token in lowered for token in ["browser", "chromium", "executable", "playwright", "install", "not found", "missing"])


def _image_url_from_media(raw: dict[str, Any], base_url: str) -> str:
    for key in ["src", "url", "href", "data-src", "data-original"]:
        value = str(raw.get(key) or "").strip()
        if value and not value.startswith("data:"):
            return urljoin(base_url, value)
    return ""


def _image_ext(url: str, content_type: str = "") -> str:
    ext = Path(urlparse(url).path).suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return ext
    guessed = mimetypes.guess_extension(content_type.split(";")[0].strip()) if content_type else ""
    return guessed if guessed in {".jpg", ".jpeg", ".png", ".webp", ".gif"} else ".jpg"


def _download_images(images: list[dict[str, str]], asset_dir: Path | None, user_agent: str | None = None, limit: int = DEFAULT_IMAGE_LIMIT) -> list[dict[str, str]]:
    if not asset_dir:
        return images[:limit]
    ensure_dir(asset_dir)
    headers = {"User-Agent": user_agent or "DailyBilibiliBriefing/0.1 (+local; image fetch)", "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"}
    saved: list[dict[str, str]] = []
    seen: set[str] = set()
    for idx, image in enumerate(images):
        url = image.get("url") or ""
        if not url or url in seen:
            continue
        seen.add(url)
        record = dict(image)
        try:
            with requests.get(url, headers=headers, timeout=12, stream=True) as resp:
                ctype = resp.headers.get("content-type", "")
                if resp.status_code < 400 and ("image" in ctype or urlparse(url).path.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif"))):
                    max_bytes = 6_000_000
                    declared = resp.headers.get("content-length") or ""
                    if declared.isdigit() and int(declared) > max_bytes:
                        record["error"] = f"image too large: {declared} bytes"
                    else:
                        chunks: list[bytes] = []
                        received = 0
                        truncated = False
                        for chunk in resp.iter_content(chunk_size=65536):
                            if not chunk:
                                continue
                            received += len(chunk)
                            if received > max_bytes:
                                truncated = True
                                break
                            chunks.append(chunk)
                        if truncated:
                            record["error"] = f"image too large: exceeded {max_bytes} bytes"
                        elif chunks:
                            ext = _image_ext(url, ctype)
                            path = asset_dir / f"image-{idx + 1:02d}{ext}"
                            path.write_bytes(b"".join(chunks))
                            record["path"] = str(path)
                            record["content_type"] = ctype
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"[:200]
        saved.append(record)
        if len(saved) >= limit:
            break
    return saved


def _save_screenshot(b64_value: str, asset_dir: Path | None) -> str:
    if not b64_value or not asset_dir:
        return ""
    ensure_dir(asset_dir)
    try:
        raw = b64_value.split(",", 1)[-1]
        data = base64.b64decode(raw)
        path = asset_dir / "page.png"
        path.write_bytes(data)
        return str(path)
    except Exception:
        return ""


def _image_url_candidate(value: str) -> str:
    value = str(value or "").strip()
    if not value or value.startswith("data:"):
        return ""
    if re.search(r"\.(?:png|jpe?g|webp|gif|svg)(?:\?|$)", value, flags=re.I):
        return value
    return ""


def _iter_json_strings(value: Any, path: str = ""):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _iter_json_strings(child, f"{path}/{key}")
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            yield from _iter_json_strings(child, f"{path}[{idx}]")
    elif isinstance(value, str):
        yield path, value


def _string_score(path: str, value: str) -> int:
    text = clean_text(value)
    lowered = text.lower()
    score = 0
    if any(keyword.lower() in lowered for keyword in MAINSTREAM_AI_KEYWORDS):
        score += 8
    score += sum(5 for keyword in FACT_KEYWORDS if keyword.lower() in lowered)
    if any(ch.isdigit() for ch in text):
        score += 4
    if any(token in lowered for token in ["api", "price", "pricing", "tokenhub", "github", "hugging face", "modelscope", "license", "benchmark"]):
        score += 5
    if any(token in path.lower() for token in ["title", "desc", "content", "introduction", "chart", "cardlist", "feedback", "link"]):
        score += 3
    if len(text) >= 28:
        score += 2
    if len(text) > 260:
        score -= 2
    return score


def _dedupe_lines(values: list[str], *, limit: int = 120) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        line = clean_text(value).strip("\u3002\uff1b; ")
        if not line:
            continue
        norm = re.sub(r"\W+", "", line.lower())
        if not norm or norm in seen:
            continue
        seen.add(norm)
        result.append(line)
        if len(result) >= limit:
            break
    return result


def _title_from_json(data: Any) -> str:
    preferred_keys = {"title", "headline", "name", "modelname", "model_name"}
    fallback: list[str] = []
    if isinstance(data, dict):
        stack: list[Any] = [data]
        while stack:
            node = stack.pop(0)
            if isinstance(node, dict):
                for key, value in node.items():
                    if isinstance(value, str):
                        cleaned = clean_text(value, 160)
                        if key.lower() in preferred_keys and 3 <= len(cleaned) <= 120:
                            return cleaned
                        if 6 <= len(cleaned) <= 80 and any(k in key.lower() for k in ["title", "name"]):
                            fallback.append(cleaned)
                    elif isinstance(value, (dict, list)):
                        stack.append(value)
            elif isinstance(node, list):
                stack.extend(node)
    return fallback[0] if fallback else ""


def _rank_images_from_json(data: Any, base_url: str = "") -> list[dict[str, str]]:
    ranked: list[tuple[int, dict[str, str]]] = []
    for path, raw in _iter_json_strings(data):
        candidate = _image_url_candidate(raw)
        if not candidate:
            continue
        image_url = urljoin(base_url, candidate)
        lowered = (path + " " + image_url).lower()
        score = 0
        for term in IMAGE_PRIORITY_TERMS:
            if term.lower() in lowered:
                score += 4
        if any(term in lowered for term in ["logo", "icon", "arrow", "decoration", "bg", "background"]):
            score -= 3
        alt = clean_text(path.rsplit("/", 1)[-1].strip("[]0123456789"), 80)
        ranked.append((score, {"url": image_url, "alt": alt, "score": str(score)}))
    ranked.sort(key=lambda row: row[0], reverse=True)
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for _score, image in ranked:
        url = image.get("url") or ""
        if not url or url in seen:
            continue
        seen.add(url)
        result.append(image)
    return result


def _article_from_json_payload(
    *,
    original_url: str,
    final_url: str,
    data: Any,
    crawler: str,
    user_agent: str | None,
    asset_dir: Path | None,
    status_code: int | None = 200,
) -> ArticleResult:
    scored: list[tuple[int, str]] = []
    for path, raw in _iter_json_strings(data):
        if _image_url_candidate(raw):
            continue
        cleaned = clean_text(raw, 900)
        if len(cleaned) < 5:
            continue
        score = _string_score(path, cleaned)
        if score <= 0 and len(cleaned) < 34:
            continue
        scored.append((score, cleaned))
    scored.sort(key=lambda row: (row[0], len(row[1])), reverse=True)
    lines = _dedupe_lines([line for _score, line in scored])
    title = _title_from_json(data)
    if title:
        lines = _dedupe_lines([title, *lines])
    text = "\n".join(lines)[:ARTICLE_TEXT_LIMIT]
    facts = extract_key_facts(text, limit=12)
    images = _download_images(_rank_images_from_json(data, final_url), asset_dir, user_agent=user_agent, limit=DEFAULT_IMAGE_LIMIT)
    description = clean_text("；".join([x for x in lines if x != title][:3]), 500)
    json_links: list[dict[str, str]] = []
    seen_links: set[str] = set()
    for path, raw in _iter_json_strings(data):
        candidate = str(raw or "").strip()
        if not candidate.startswith(("http://", "https://")):
            continue
        if not any(token in path.lower() for token in ["url", "href", "link", "source", "github", "model"]):
            continue
        candidate = urljoin(final_url, candidate)
        if candidate in seen_links:
            continue
        seen_links.add(candidate)
        json_links.append({"url": candidate, "text": clean_text(path.rsplit("/", 1)[-1], 80)})
        if len(json_links) >= 40:
            break
    return ArticleResult(
        status="ok" if text or facts or images else "empty",
        url=original_url,
        final_url=final_url,
        status_code=status_code,
        title=title,
        description=description,
        text=text,
        markdown=text,
        facts=facts,
        images=images,
        links=json_links,
        crawler=crawler,
    )


def _default_headers(user_agent: str | None = None) -> dict[str, str]:
    return {
        "User-Agent": user_agent or "DailyBilibiliBriefing/0.1 (+local; article enrichment)",
        "Accept": "text/html,application/xhtml+xml,application/xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }


def _get_json(url: str, *, user_agent: str | None, timeout: int) -> tuple[Any, int | None, str]:
    resp = requests.get(url, headers={**_default_headers(user_agent), "Accept": "application/json,text/plain,*/*"}, timeout=timeout, allow_redirects=True)
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}")
    return resp.json(), resp.status_code, resp.url


def _hunyuan_slugs(url: str) -> list[str]:
    parsed = urlparse(url)
    haystack = " ".join([parsed.path, parsed.query, url]).lower()
    slugs = re.findall(r"\bhy\d+(?:[-_]preview)?\b", haystack, flags=re.I)
    normalized: list[str] = []
    for slug in slugs:
        s = slug.lower().replace("_", "-")
        if s not in normalized:
            normalized.append(s)
    return normalized


def _hunyuan_config_urls(url: str) -> list[str]:
    slugs = _hunyuan_slugs(url)
    urls: list[str] = []
    if any(slug.startswith("hy3") for slug in slugs):
        urls.extend(HY3_CONFIG_URLS)
    for slug in slugs:
        base_slug = slug.replace("-preview", "")
        candidates = [slug]
        if base_slug not in candidates:
            candidates.append(base_slug)
        for candidate in candidates:
            urls.extend(
                [
                    f"https://cdn-portal.hunyuan.tencent.com/commonAssets/{candidate}/{candidate}-config-zh.json",
                    f"https://hunyuan-portal-prod-1258344703.cos.ap-guangzhou.myqcloud.com/commonAssets/{candidate}/{candidate}-config-zh.json",
                    f"https://hunyuan-portal-prod-1258344703.cos.ap-guangzhou.myqcloud.com/commonAssets/{candidate}/pre/{candidate}-config-zh.json",
                ]
            )
    deduped: list[str] = []
    for candidate in urls:
        if candidate not in deduped:
            deduped.append(candidate)
    return deduped


def _fetch_hunyuan_config_adapter(url: str, *, user_agent: str | None, timeout: int, asset_dir: Path | None) -> ArticleResult | None:
    domain = _domain(url)
    if not any(token in domain for token in ["hy.tencent.com", "hunyuan.tencent.com", "hunyuan"]):
        return None
    for config_url in _hunyuan_config_urls(url):
        try:
            data, status_code, final_url = _get_json(config_url, user_agent=user_agent, timeout=timeout)
        except Exception:
            continue
        article = _article_from_json_payload(
            original_url=url,
            final_url=final_url or config_url,
            data=data,
            crawler="hunyuan_config_adapter",
            user_agent=user_agent,
            asset_dir=asset_dir,
            status_code=status_code,
        )
        if article.status == "ok" and (article.facts or len(article.text) >= 200 or article.images):
            return article
    return None


def _extract_script_urls(html_text: str, base_url: str) -> list[str]:
    urls: list[str] = []
    for match in re.finditer(r"<script[^>]+src=[\"']([^\"']+)[\"']", html_text, flags=re.I):
        src = match.group(1).strip()
        if not src:
            continue
        urls.append(urljoin(base_url, src))
    return urls[:SPA_JS_FETCH_LIMIT]


def _discover_json_urls_from_text(text: str, base_url: str) -> list[str]:
    candidates: list[str] = []
    patterns = [
        r"https?://[^\"'`\\\s<>]+?\.json(?:\?[^\"'`\\\s<>]*)?",
        r"[\"']([^\"']+?\.json(?:\?[^\"']*)?)[\"']",
    ]
    for pattern in patterns:
        for raw in re.findall(pattern, text, flags=re.I):
            value = raw if isinstance(raw, str) else raw[0]
            value = value.strip()
            if not value:
                continue
            if value.startswith(("http://", "https://", "/", "./", "../")):
                candidates.append(urljoin(base_url, value))
    deduped: list[str] = []
    for candidate in candidates:
        if candidate not in deduped:
            deduped.append(candidate)
        if len(deduped) >= SPA_JSON_FETCH_LIMIT:
            break
    return deduped


def _fetch_frontend_json_adapter(url: str, *, user_agent: str | None, timeout: int, asset_dir: Path | None) -> ArticleResult | None:
    try:
        resp = requests.get(url, headers=_default_headers(user_agent), timeout=timeout, allow_redirects=True)
    except requests.RequestException:
        return None
    if resp.status_code >= 400:
        return None
    base_url = resp.url or url
    texts = [resp.text[:SPA_ASSET_BYTES_LIMIT]]
    json_urls = _discover_json_urls_from_text(resp.text, base_url)
    for script_url in _extract_script_urls(resp.text, base_url):
        try:
            js_resp = requests.get(script_url, headers=_default_headers(user_agent), timeout=min(timeout, 12), allow_redirects=True)
        except requests.RequestException:
            continue
        if js_resp.status_code >= 400:
            continue
        js_text = js_resp.text[:SPA_ASSET_BYTES_LIMIT]
        texts.append(js_text)
        json_urls.extend(_discover_json_urls_from_text(js_text, js_resp.url or script_url))
    best: ArticleResult | None = None
    seen: set[str] = set()
    for json_url in json_urls:
        if json_url in seen:
            continue
        seen.add(json_url)
        try:
            data, status_code, final_url = _get_json(json_url, user_agent=user_agent, timeout=min(timeout, 12))
        except Exception:
            continue
        article = _article_from_json_payload(
            original_url=url,
            final_url=final_url or json_url,
            data=data,
            crawler="spa_json_adapter",
            user_agent=user_agent,
            asset_dir=asset_dir,
            status_code=status_code,
        )
        if best is None or _article_richness(article) > _article_richness(best):
            best = article
    return best if best and best.status == "ok" else None


def _fetch_meta_tags_adapter(url: str, *, user_agent: str | None, timeout: int, asset_dir: Path | None) -> ArticleResult | None:
    try:
        resp = requests.get(url, headers=_default_headers(user_agent), timeout=timeout, allow_redirects=True)
    except requests.RequestException:
        return None
    if resp.status_code >= 400:
        return None
    ctype = resp.headers.get("content-type", "")
    if "text/html" not in ctype and "application/xhtml" not in ctype and not resp.text.lstrip().startswith("<"):
        return None
    encoding = getattr(resp, "encoding", None)
    if encoding is None or str(encoding).lower() == "iso-8859-1":
        resp.encoding = getattr(resp, "apparent_encoding", None) or "utf-8"
    title, description, article_text, published_at = extract_article_text(resp.text)
    text = clean_text("\n".join(x for x in [title, description, article_text] if x), ARTICLE_TEXT_LIMIT)
    image_url = _meta_content(resp.text, ["og:image", "twitter:image", "image"])
    images = _download_images(
        [{"url": urljoin(resp.url or url, image_url), "alt": title or description, "score": "3"}] if image_url else [],
        asset_dir,
        user_agent=user_agent,
        limit=1,
    )
    facts = extract_key_facts(text)
    if not text and not images:
        return None
    return ArticleResult(
        status="ok",
        url=url,
        final_url=resp.url,
        status_code=resp.status_code,
        title=title,
        description=description,
        text=text,
        markdown=text,
        facts=facts,
        images=images,
        links=extract_outbound_links(resp.text, resp.url or url),
        published_at=published_at,
        crawler="meta_tags_adapter",
    )


def _article_richness(article: ArticleResult) -> int:
    return len(clean_text(article.text or article.markdown)) + len(article.facts or []) * 220 + len(article.images or []) * 120 + (120 if article.title else 0)


def _article_is_too_thin(article: ArticleResult) -> bool:
    text = clean_text("\n".join([article.title, article.description, article.text, article.markdown])).lower()
    text_len = len(clean_text(article.text or article.markdown))
    if article.status != "ok":
        return True
    if any(marker in text for marker in SPA_THIN_MARKERS) and text_len < 2500:
        return True
    return text_len < ADAPTER_TEXT_MIN_CHARS and len(article.facts or []) < ADAPTER_FACT_MIN_COUNT


def _maybe_adapt_article(url: str, article: ArticleResult, *, user_agent: str | None, timeout: int, asset_dir: Path | None) -> ArticleResult:
    if not _article_is_too_thin(article):
        return article
    adapters: list[Callable[..., ArticleResult | None]] = [_fetch_hunyuan_config_adapter, _fetch_frontend_json_adapter, _fetch_meta_tags_adapter]
    best = article
    adapter_errors: list[str] = []
    for adapter in adapters:
        try:
            candidate = adapter(url, user_agent=user_agent, timeout=timeout, asset_dir=asset_dir)
        except Exception as exc:  # pragma: no cover - adapter hardening
            adapter_errors.append(f"{adapter.__name__}: {type(exc).__name__}: {exc}"[:180])
            continue
        if not candidate or candidate.status != "ok":
            continue
        if _article_richness(candidate) > _article_richness(best):
            if not candidate.screenshot_path:
                candidate.screenshot_path = article.screenshot_path
            candidate.repair = dict(article.repair or {})
            candidate.repair["adapter_trigger"] = {"primary_crawler": article.crawler, "primary_status": article.status, "primary_text_chars": len(article.text or article.markdown)}
            if adapter_errors:
                candidate.repair["adapter_errors"] = adapter_errors[-3:]
            best = candidate
    return best


async def _crawl4ai_once(url: str, *, user_agent: str | None, timeout: int, asset_dir: Path | None, capture_screenshot: bool) -> ArticleResult:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
    import crawl4ai.async_webcrawler as async_webcrawler_module
    from crawl4ai.async_database import async_db_manager
    from crawl4ai.content_filter_strategy import PruningContentFilter
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

    ua = user_agent or "DailyBilibiliBriefing/0.1 (+local; crawl4ai)"
    from .browser_runtime import crawl_channel

    channel = crawl_channel()
    browser_config = BrowserConfig(
        headless=True,
        browser_type="chromium",
        chrome_channel=channel,
        channel=channel,
        user_agent=ua,
        viewport_width=1360,
        viewport_height=900,
        device_scale_factor=1.0,
        verbose=False,
    )
    markdown_generator = DefaultMarkdownGenerator(content_filter=PruningContentFilter(threshold=0.35, threshold_type="fixed"))
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        markdown_generator=markdown_generator,
        screenshot=capture_screenshot,
        scan_full_page=True,
        scroll_delay=0.15,
        wait_until="domcontentloaded",
        delay_before_return_html=0.8,
        page_timeout=max(5, timeout) * 1000,
        verbose=False,
    )
    class _DisabledRobotsParser:
        async def can_fetch(self, _url: str, _user_agent: str = "*") -> bool:
            return True

    original_robots_parser = async_webcrawler_module.RobotsParser
    async_webcrawler_module.RobotsParser = _DisabledRobotsParser
    try:
        async with AsyncWebCrawler(config=browser_config) as crawler:
            result = await crawler.arun(url=url, config=run_config)
    finally:
        async_webcrawler_module.RobotsParser = original_robots_parser
        # Crawl4AI keeps a module-level aiosqlite pool. Our synchronous caller
        # creates one event loop per page via asyncio.run(), so pooled handles
        # must be closed before that loop is destroyed.
        await async_db_manager.cleanup()
    if not getattr(result, "success", False):
        raise RuntimeError(str(getattr(result, "error_message", "crawl4ai failed")))

    final_url = str(getattr(result, "redirected_url", "") or getattr(result, "url", "") or url)
    html_text = str(getattr(result, "html", "") or "")
    title, description, html_article_text, published_at = extract_article_text(html_text)
    metadata = getattr(result, "metadata", None) or {}
    if isinstance(metadata, dict):
        title = clean_text(str(metadata.get("title") or metadata.get("og:title") or title))
        description = clean_text(str(metadata.get("description") or metadata.get("og:description") or description))
        published_at = published_at or parse_date(
            str(
                metadata.get("article:published_time")
                or metadata.get("article:published")
                or metadata.get("datePublished")
                or metadata.get("pubdate")
                or metadata.get("publishdate")
                or metadata.get("publish_date")
                or ""
            )
        )
    markdown = _markdown_to_text(getattr(result, "markdown", None))
    text = markdown if len(clean_text(markdown)) >= len(clean_text(html_article_text)) else html_article_text
    facts = extract_key_facts("\n".join([title, description, text]))

    raw_images = []
    media = getattr(result, "media", None) or {}
    if isinstance(media, dict):
        raw_images = list(media.get("images") or [])
    images: list[dict[str, str]] = []
    for raw in raw_images:
        if not isinstance(raw, dict):
            continue
        image_url = _image_url_from_media(raw, final_url)
        if not image_url:
            continue
        alt = clean_text(str(raw.get("alt") or raw.get("desc") or raw.get("title") or ""), 120)
        score = str(raw.get("score") or raw.get("relevance_score") or "")
        images.append({"url": image_url, "alt": alt, "score": score})
    images = _download_images(images, asset_dir, user_agent=ua)
    links = extract_outbound_links(html_text, final_url)
    screenshot_path = _save_screenshot(str(getattr(result, "screenshot", "") or ""), asset_dir)
    status_code = getattr(result, "status_code", None) or getattr(result, "redirected_status_code", None)
    status = "ok" if text or description or facts else "empty"
    return ArticleResult(
        status=status,
        url=url,
        final_url=final_url,
        status_code=int(status_code) if status_code else None,
        title=title,
        description=description,
        text=clean_text(text, ARTICLE_TEXT_LIMIT),
        markdown=clean_text(markdown, ARTICLE_TEXT_LIMIT),
        facts=facts,
        images=images,
        links=links,
        screenshot_path=screenshot_path,
        published_at=published_at,
        crawler="crawl4ai",
    )


def fetch_article_with_requests(url: str, user_agent: str | None = None, timeout: int = ARTICLE_TIMEOUT_SECONDS) -> ArticleResult:
    headers = {
        "User-Agent": user_agent or "DailyBilibiliBriefing/0.1 (+local; article enrichment)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
    except requests.RequestException as exc:
        return ArticleResult(status="error", url=url, error=f"{type(exc).__name__}: {exc}", crawler="requests")
    ctype = resp.headers.get("content-type", "")
    if resp.status_code >= 400:
        return ArticleResult(status="http_error", url=url, final_url=resp.url, status_code=resp.status_code, error=resp.text[:200], crawler="requests")
    if "text/html" not in ctype and "application/xhtml" not in ctype and not resp.text.lstrip().startswith("<"):
        return ArticleResult(status="unsupported_content", url=url, final_url=resp.url, status_code=resp.status_code, error=ctype, crawler="requests")
    encoding = getattr(resp, "encoding", None)
    if encoding is None or str(encoding).lower() == "iso-8859-1":
        resp.encoding = getattr(resp, "apparent_encoding", None) or "utf-8"
    title, description, article_text, published_at = extract_article_text(resp.text)
    facts = extract_key_facts("\n".join([title, description, article_text]))
    links = extract_outbound_links(resp.text, resp.url or url)
    status = "ok" if article_text or description else "empty"
    return ArticleResult(
        status=status,
        url=url,
        final_url=resp.url,
        status_code=resp.status_code,
        title=title,
        description=description,
        text=article_text,
        facts=facts,
        links=links,
        published_at=published_at,
        crawler="requests",
    )


def _article_is_substantive(article: ArticleResult) -> bool:
    text = clean_text(article.text or article.description)
    return article.status == "ok" and (len(text) >= 800 or (len(text) >= 500 and len(article.facts or []) >= 2))


def _article_needs_browser(url: str, article: ArticleResult) -> bool:
    domain = _domain(article.final_url or url)
    if domain.endswith("news.google.com") or article.status_code in {401, 403, 404, 410, 429}:
        return False
    text = clean_text(" ".join([article.title, article.description, article.text])).lower()
    explicit_spa = any(marker in text for marker in SPA_THIN_MARKERS)
    empty_success = article.status in {"empty", "error"} and not text
    return explicit_spa or empty_success


def fetch_article(
    url: str,
    user_agent: str | None = None,
    timeout: int = ARTICLE_TIMEOUT_SECONDS,
    *,
    use_crawl4ai: bool = True,
    auto_repair: bool = True,
    asset_dir: Path | None = None,
    capture_screenshot: bool = True,
) -> ArticleResult:
    """Fetch with HTTP/adapters first; use the browser only for genuinely thin pages.

    This avoids launching Chromium for ordinary server-rendered articles. Browser
    failures never discard a usable HTTP result and never block the whole run.
    """
    http_article = _maybe_adapt_article(
        url,
        fetch_article_with_requests(url, user_agent=user_agent, timeout=timeout),
        user_agent=user_agent,
        timeout=timeout,
        asset_dir=asset_dir,
    )
    if not use_crawl4ai or _article_is_substantive(http_article) or not _article_needs_browser(url, http_article):
        return http_article

    repair_payload: dict[str, Any] = {}
    for attempt in ["initial", "after_repair"]:
        try:
            browser_article = asyncio.run(
                asyncio.wait_for(
                    _crawl4ai_once(
                        url,
                        user_agent=user_agent,
                        timeout=timeout,
                        asset_dir=asset_dir,
                        capture_screenshot=capture_screenshot,
                    ),
                    timeout=max(12, timeout + 8),
                )
            )
            if repair_payload:
                browser_article.repair = repair_payload
            adapted = _maybe_adapt_article(url, browser_article, user_agent=user_agent, timeout=timeout, asset_dir=asset_dir)
            return adapted if _article_is_substantive(adapted) or len(adapted.text) > len(http_article.text) else http_article
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            if attempt == "initial" and auto_repair and _crawl_error_needs_repair(message):
                repaired = _repair_crawl4ai_runtime(message)
                repair_payload = {
                    "attempted": repaired.attempted,
                    "ok": repaired.ok,
                    "actions": repaired.actions[-3:],
                    "error": repaired.error,
                }
                if repaired.ok:
                    continue
            http_article.error = (message + ("; http=" + http_article.error if http_article.error else ""))[:500]
            http_article.repair = repair_payload
            return http_article
    return http_article


def _merge_summary(original: str, article: ArticleResult) -> str:
    pieces = [clean_text(original)]
    for fact in article.facts or []:
        if fact and fact not in pieces:
            pieces.append(fact)
    if len(pieces) == 1 and article.description:
        pieces.append(article.description)
    return clean_text("；".join(p for p in pieces if p), SUMMARY_LIMIT)


def _asset_dir_for_item(base: Path | None, item: FeedItem) -> Path | None:
    if not base:
        return None
    key = stable_hash([item.source_id, item.guid, item.link, item.title])
    return ensure_dir(base / key)


def _enrichment_record(article: ArticleResult) -> dict[str, Any]:
    return {
        "status": article.status,
        "url": article.url,
        "final_url": article.final_url,
        "status_code": article.status_code,
        "title": article.title,
        "facts": article.facts or [],
        "images": article.images[:DEFAULT_IMAGE_LIMIT],
        "links": article.links[:80],
        "screenshot_path": article.screenshot_path,
        "crawler": article.crawler,
        "repair": article.repair,
        "error": article.error[:500],
        "published_at": article.published_at.isoformat() if article.published_at else "",
        "published_at_provenance": "article_metadata" if article.published_at else "",
        "fetched_at": datetime.now(UTC).isoformat(),
    }


def _apply_article_published_at(item: FeedItem, published_at: datetime) -> None:
    """用正文原始发布时间纠正 RSS 重浮现，同时保留原始来源时间供审计。"""
    item.raw = dict(item.raw or {})
    article_time = published_at.replace(tzinfo=UTC) if published_at.tzinfo is None else published_at.astimezone(UTC)
    item.raw["article_published_at"] = article_time.isoformat()
    item.raw["article_published_at_provenance"] = "article_metadata"
    if item.published_at is None:
        # 持续更新的官网发现页不能仅凭正文日期晋级为当日新闻。
        if item.raw.get("kind") != "official_web_discovery":
            item.published_at = article_time
            item.raw["published_at_provenance"] = "article_metadata"
        return

    source_time = item.published_at.replace(tzinfo=UTC) if item.published_at.tzinfo is None else item.published_at.astimezone(UTC)
    if article_time <= source_time - timedelta(hours=ARTICLE_SOURCE_DATE_CONFLICT_HOURS):
        # RSS pubDate/lastmod 可能在旧文章重新进入 feed 时变化。正文 datePublished
        # 明显更早时按原始公开时间判新鲜度，避免 NVIDIA 旧文一类误入。
        item.raw["source_published_at"] = source_time.isoformat()
        item.raw["published_at_conflict_hours"] = round((source_time - article_time).total_seconds() / 3600, 3)
        item.published_at = article_time
        item.raw["published_at_provenance"] = "article_metadata_overrode_source"
        return
    item.raw.setdefault("published_at_provenance", "source")


def enrich_feed_items(
    items: list[FeedItem],
    *,
    max_items: int = 120,
    per_domain_limit: int = 12,
    user_agent: str | None = None,
    use_crawl4ai: bool = True,
    auto_repair: bool = True,
    asset_dir: Path | None = None,
    capture_screenshots: bool = True,
    document_sink: Callable[[FeedItem, ArticleResult], None] | None = None,
    progress_sink: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, int]:
    if max_items is not None and max_items < 0:
        max_items = 0
    attempted = ok = failed = skipped = crawl4ai_ok = fallback_ok = repaired = image_count = screenshot_count = documents_persisted = 0
    domain_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    candidates = [item for item in items if should_enrich_item(item)]
    candidates.sort(key=enrichment_priority, reverse=True)
    for item in candidates:
        if max_items and attempted >= max_items:
            skipped += 1
            continue
        domain = _domain(item.link)
        if per_domain_limit > 0 and domain_counts.get(domain, 0) >= per_domain_limit:
            skipped += 1
            continue
        high_volume_source = item.source_id.lower().endswith("_hf_models") or item.source_id.lower().startswith("arxiv_")
        source_cap = 1 if high_volume_source and max_items and max_items <= 40 else max_items
        if source_cap and source_counts.get(item.source_id, 0) >= source_cap:
            skipped += 1
            continue
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
        source_counts[item.source_id] = source_counts.get(item.source_id, 0) + 1
        attempted += 1
        if progress_sink is not None:
            progress_sink({"attempted": attempted, "ok": ok, "failed": failed, "skipped": skipped, "current_url": item.link, "current_title": item.title})
        article = fetch_article(
            item.link,
            user_agent=user_agent,
            use_crawl4ai=use_crawl4ai,
            auto_repair=auto_repair,
            asset_dir=_asset_dir_for_item(asset_dir, item),
            capture_screenshot=capture_screenshots,
        )
        item.raw = dict(item.raw or {})
        item.raw["article_enrichment"] = _enrichment_record(article)
        if document_sink is not None and article.status == "ok" and (article.text or article.description):
            document_sink(item, article)
            documents_persisted += 1
        image_count += len([img for img in article.images if img.get("path") or img.get("url")])
        if article.screenshot_path:
            screenshot_count += 1
        if article.repair.get("attempted"):
            repaired += 1
        if article.crawler == "crawl4ai" and article.status == "ok":
            crawl4ai_ok += 1
        if article.crawler == "requests" and article.status == "ok":
            fallback_ok += 1
        if article.status == "ok" and (article.facts or article.description or article.text):
            item.summary = _merge_summary(item.summary, article)
            if article.published_at:
                _apply_article_published_at(item, article.published_at)
            ok += 1
        else:
            failed += 1
        if progress_sink is not None:
            progress_sink({"attempted": attempted, "ok": ok, "failed": failed, "skipped": skipped, "current_url": "", "current_title": "", "last_crawler": article.crawler})
    skipped += max(0, len(items) - len(candidates))
    return {
        "attempted": attempted,
        "ok": ok,
        "failed": failed,
        "skipped": skipped,
        "crawl4ai_ok": crawl4ai_ok,
        "requests_fallback_ok": fallback_ok,
        "auto_repair_attempts": repaired,
        "images": image_count,
        "screenshots": screenshot_count,
        "documents_persisted": documents_persisted,
    }

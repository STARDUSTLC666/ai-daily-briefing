from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime

from ..core import (
    UTC, Cluster, FeedItem, clean_text, detect_entity,
    extract_hf_model_id, extract_model_names, model_family_key, stable_hash,
)

STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "that", "this", "into", "about", "new",
    "release", "released", "ai", "llm", "model", "models", "official", "blog",
    "news", "update", "updates", "announces", "introducing", "introduces",
    "发布", "推出", "上线", "更新", "正式", "最新", "宣布", "今日", "消息",
})
LAUNCH_ACTION_TERMS = ("发布", "推出", "上线", "开源", "开放", "正式", "launch", "launches", "release", "releases", "introduces", "announces")
SUBEVENT_TERMS = (
    "copilot", "microsoft 365", "system card", "bug bounty", "价格", "pricing",
    "api", "sdk", "接入", "available in", "available on", "preferred model",
    "集成", "integration", "benchmark", "评测", "榜单", "跑分",
    "proof", "conjecture", "theorem", "数学证明", "猜想", "定理",
)
ORGANIZATION_EVENT_TERMS = (
    "离职", "辞职", "卸任", "离开", "跑路", "任命", "接任", "调任", "重组", "并入",
    "安全主管", "安全负责人", "高管", "诉讼", "起诉", "收购", "融资",
    "resign", "depart", "step down", "appointed", "reorgan", "lawsuit", "acquir",
)
NAMED_PRODUCTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("chatgpt-work", ("chatgpt work",)),
    ("github-copilot", ("github copilot",)),
    ("microsoft-365-copilot", ("microsoft 365 copilot",)),
    ("claude-code", ("claude code",)),
)
PRODUCT_ACTION_TERMS = ("发布", "推出", "上线", "新增", "更新", "开放", "launch", "release", "introduces", "adds")

_VERSION_TAG_RE = re.compile(r"^v?\d+(?:\.\d+){1,4}(?:[-._a-zA-Z0-9]+)?$")
_CJK_LATIN_RE = re.compile(r"([\u4e00-\u9fff])([A-Za-z0-9])|([A-Za-z0-9])([\u4e00-\u9fff])")


def _space_cjk_latin(text: str) -> str:
    return _CJK_LATIN_RE.sub(lambda m: f"{m.group(1)} {m.group(2)}" if m.group(1) else f"{m.group(3)} {m.group(4)}", text)


def _tokens(text: str) -> list[str]:
    lowered = _space_cjk_latin(clean_text(text)).lower()
    raw = re.findall(r"[a-z][a-z0-9_.-]{2,}|[\u4e00-\u9fff]{2,}|\d{2,}", lowered)
    return [tok for tok in raw if tok not in STOPWORDS and len(tok) > 1][:10]


def _item_hf_family(item: FeedItem) -> str:
    raw_model_id = str((item.raw or {}).get("model_id") or "").strip().strip("/") if isinstance(item.raw, dict) else ""
    model_id = raw_model_id or extract_hf_model_id(item.link) or extract_hf_model_id(item.title)
    return model_family_key(model_id) if model_id else ""


def _resolve_entity(item: FeedItem) -> str:
    by_source = detect_entity(item.title, source_id=item.source_id)
    if by_source != "AI":
        return by_source
    by_title_source = detect_entity(f"{item.title} {item.source_name}")
    if by_title_source != "AI":
        return by_title_source
    broad = detect_entity(f"{item.title} {item.summary} {item.source_name}")
    return broad if broad in {"具身智能 / 机器人", "论文 / arXiv"} else "AI"


def _event_family_key(item: FeedItem, entity: str) -> str:
    title = clean_text(item.title).lower().replace("‐", "-").replace("‑", "-").replace("–", "-").replace("—", "-")
    if any(term in title for term in ORGANIZATION_EVENT_TERMS):
        return ""
    for slug, aliases in NAMED_PRODUCTS:
        if any(a in title for a in aliases) and any(t in title for t in PRODUCT_ACTION_TERMS):
            return f"{entity}:event-product-{slug}"
    names = extract_model_names(_space_cjk_latin(title))
    if not names:
        return ""
    model = names[0].lower()
    is_subevent = any(term in title for term in SUBEVENT_TERMS)
    is_launch = any(term in title for term in LAUNCH_ACTION_TERMS)
    pos = _space_cjk_latin(title).find(model)
    if not is_subevent and (is_launch or pos == 0 or (0 < pos <= 18)):
        return f"{entity}:event-model-launch-{model_family_key(model) or model}"
    return ""


def cluster_key(item: FeedItem) -> tuple[str, str]:
    entity = _resolve_entity(item)
    if item.source_id.endswith("_hf_models") or "huggingface.co/" in (item.link or "").lower():
        hf_family = _item_hf_family(item)
        if hf_family:
            return f"{entity}:hf-{hf_family.replace('/', '-')}", entity
    event_key = _event_family_key(item, entity)
    if event_key:
        return event_key, entity
    tokens = _tokens(item.title) or _tokens(item.summary)
    token_part = "-".join(tokens[:5]) if tokens else stable_hash([item.title], length=12)
    return f"{entity}:{token_part}", entity


def cluster_items(items: list[FeedItem]) -> list[Cluster]:
    buckets: dict[str, list[FeedItem]] = defaultdict(list)
    entities: dict[str, str] = {}
    for item in items:
        key, entity = cluster_key(item)
        buckets[key].append(item)
        entities[key] = entity

    epoch = datetime.min.replace(tzinfo=UTC)
    clusters: list[Cluster] = []
    for key, grouped in buckets.items():
        grouped.sort(
            key=lambda it: (
                1 if it.published_at and it.source_reliability == "official" else 0,
                1 if it.published_at else 0,
                it.published_at or epoch,
                0 if str((it.raw or {}).get("kind") or "") == "official_web_discovery" else 1,
                it.fetched_at or epoch,
            ),
            reverse=True,
        )
        latest = max((it.published_at for it in grouped if it.published_at), default=None)
        lead_title = clean_text(grouped[0].title)
        if _VERSION_TAG_RE.fullmatch(lead_title):
            lead_title = f"{grouped[0].source_name} 发布 {lead_title}"
        clusters.append(Cluster(key=key, title=lead_title, items=grouped, entity=entities.get(key, "AI"), published_at=latest))
    clusters.sort(key=lambda c: c.published_at or epoch, reverse=True)
    return clusters

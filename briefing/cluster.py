from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone

from .models import Cluster, FeedItem
from .util import clean_text, stable_hash

UTC = timezone.utc

ENTITY_PATTERNS: dict[str, list[str]] = {
    "OpenAI": ["openai", "chatgpt", "gpt-", "gpt ", "sora", "codex"],
    "Google / Gemini": ["google", "gemini", "deepmind", "gemma"],
    "Anthropic / Claude": ["anthropic", "claude"],
    "CircleCI": ["circleci", "circle ci", "chunk sidecars"],
    "Hugging Face": ["hugging face", "huggingface", "hf "],
    "Qwen / 阿里": ["qwen", "通义", "千问", "阿里", "modelscope", "魔搭"],
    "DeepSeek": ["deepseek", "深度求索"],
    "Kimi / 月之暗面": ["kimi", "moonshot", "月之暗面"],
    "MiniMax": ["minimax", "海螺", "abab", "speech-"],
    "Mistral": ["mistral", "mixtral", "codestral", "le chat"],
    "Meta / Llama": ["meta ai", "llama", "llama.cpp"],
    "xAI / Grok": ["xai", "x.ai", "grok"],
    "智谱 / GLM": ["智谱", "zhipu", "glm", "z.ai"],
    "字节 / 豆包": ["豆包", "doubao", "bytedance", "字节"],
    "腾讯混元": ["混元", "hunyuan", "tencent", "腾讯"],
    "百度文心": ["文心", "ernie", "baidu", "百度"],
    "GitHub / Copilot": ["github", "copilot"],
    "NVIDIA": ["nvidia", "英伟达"],
    "蚂蚁 / LingBot": ["lingbot", "蚂蚁灵波", "蚂蚁集团"],
    "本地模型 / 开源": ["ollama", "llama.cpp", "local llm", "open weights", "开源", "本地部署"],
    "论文 / arXiv": ["arxiv", "paper", "benchmark", "bench"],
    "具身智能 / 机器人": ["具身智能", "人形机器人", "机器人"],
}

SOURCE_ENTITY_OVERRIDES = {
    "modelscope_github_releases": "ModelScope / 阿里",
    "qwen_github_releases": "Qwen / 阿里",
    "deepseek_v3_releases": "DeepSeek",
    "deepseek_r1_releases": "DeepSeek",
    "kimi_k2_releases": "Kimi / 月之暗面",
    "zai_glm45_releases": "智谱 / GLM",
    "tencent_hunyuan_releases": "腾讯混元",
    "baidu_ernie_releases": "百度文心",
    "bytedance_seed_releases": "字节 / 豆包",
    "openai_news": "OpenAI",
    "openai_codex_releases": "OpenAI",
    "google_research": "Google / Gemini",
    "google_ai_blog": "Google / Gemini",
    "google_hf_models": "Google / Gemini",
    "huggingface_blog": "Hugging Face",
    "github_changelog": "GitHub / Copilot",
    "nvidia_developer_blog": "NVIDIA",
    "qwen_hf_models": "Qwen / 阿里",
    "deepseek_hf_models": "DeepSeek",
    "moonshot_hf_models": "Kimi / 月之暗面",
    "zai_hf_models": "智谱 / GLM",
    "minimax_hf_models": "MiniMax",
    "bytedance_seed_hf_models": "字节 / 豆包",
    "paddlepaddle_hf_models": "百度文心",
    "mistral_hf_models": "Mistral",
}

STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "into",
    "about",
    "new",
    "release",
    "released",
    "ai",
    "llm",
    "model",
    "models",
    "official",
    "blog",
    "news",
    "update",
    "updates",
    "发布",
    "推出",
    "上线",
    "更新",
}

MODEL_NAME_PATTERN = re.compile(
    r"\b(?:gpt|claude|gemini|gemma|qwen|deepseek|kimi|llama|mistral|grok|minimax|glm|hunyuan|nova)"
    r"[-‐‑–—_\s]?[a-z]*\d[A-Za-z0-9_.‐‑–—-]*(?=$|[^A-Za-z0-9_.‐‑–—-])",
    flags=re.I,
)

LAUNCH_ACTION_TERMS = ["发布", "推出", "上线", "正式", "launch", "release", "introduces", "announces"]
SUBEVENT_TERMS = [
    "copilot", "microsoft 365", "system card", "bug bounty", "价格", "pricing", "api", "sdk",
    "接入", "available in", "available on", "preferred model", "集成", "integration",
    "proof", "conjecture", "theorem", "数学证明", "猜想", "定理",
]

# A model name in a headline is sometimes only timing context for a separate
# personnel or organization story (for example, "GPT-5.6 ... safety lead
# departs").  Those stories must never be folded into the model launch wave.
# Unicode escapes keep these literals stable on Windows regardless of the
# active console code page.
ORGANIZATION_EVENT_TERMS = [
    "\u79bb\u804c",  # 离职
    "\u8f9e\u804c",  # 辞职
    "\u5378\u4efb",  # 卸任
    "\u79bb\u5f00",  # 离开
    "\u8dd1\u8def",  # 跑路 (common clickbait wording)
    "\u4efb\u547d",  # 任命
    "\u63a5\u4efb",  # 接任
    "\u91cd\u7ec4",  # 重组
    "\u5e76\u5165",  # 并入
    "\u5b89\u5168\u4e3b\u7ba1",  # 安全主管
    "\u5b89\u5168\u8d1f\u8d23\u4eba",  # 安全负责人
    "resign",
    "depart",
    "step down",
    "appointed",
    "reorgan",
]


HF_VARIANT_TOKENS = {
    "flash",
    "pro",
    "lite",
    "turbo",
    "jax",
    "pytorch",
    "torch",
    "tensorflow",
    "tf",
    "onnx",
    "gguf",
    "mlx",
    "hf",
    "fp8",
    "fp16",
    "bf16",
    "int4",
    "int8",
}


def detect_entity(text: str) -> str:
    low = text.lower()
    for entity, pats in ENTITY_PATTERNS.items():
        if any(p in low for p in pats):
            return entity
    return "AI"


def _tokens(text: str) -> list[str]:
    text = clean_text(text).lower()
    raw = re.findall(r"[a-zA-Z][a-zA-Z0-9_.-]{2,}|[\u4e00-\u9fff]{2,}|\d{2,}", text)
    tokens = [t for t in raw if t not in STOPWORDS and len(t) > 1]
    return tokens[:10]


def _hf_model_id(item: FeedItem) -> str:
    raw_model_id = item.raw.get("model_id") if isinstance(item.raw, dict) else ""
    model_id = str(raw_model_id or "").strip().strip("/")
    if model_id:
        return model_id
    match = re.search(r"huggingface\.co/([^/?#]+/[^/?#]+)", item.link, re.I)
    if match:
        return match.group(1).strip("/")
    title = clean_text(item.title).replace("：", ":")
    match = re.search(r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)", title)
    return match.group(1).strip("/") if match else ""


def _hf_model_family_key(item: FeedItem) -> str:
    model_id = _hf_model_id(item)
    if not model_id:
        return ""
    parts = model_id.split("/")
    org = parts[-2].lower() if len(parts) >= 2 else ""
    name = parts[-1]
    family_tokens = [token for token in re.split(r"[-_]+", name) if token and token.lower() not in HF_VARIANT_TOKENS]
    family = "-".join(family_tokens) or name
    family = re.sub(r"-{2,}", "-", family).strip("-").lower()
    return f"{org}/{family}" if org else family


def _normalized_model_name(text: str) -> str:
    match = MODEL_NAME_PATTERN.search(clean_text(text).lower().replace("‐", "-").replace("‑", "-").replace("–", "-").replace("—", "-"))
    return re.sub(r"\s+", "-", match.group(0)).strip("-") if match else ""


def _named_product(text: str) -> str:
    lowered = clean_text(text).lower()
    products = [
        ("chatgpt work", ["chatgpt work", "most ambitious work"]),
        ("github copilot", ["github copilot"]),
        ("microsoft 365 copilot", ["microsoft 365 copilot"]),
        ("claude code", ["claude code"]),
    ]
    for product, aliases in products:
        if any(alias in lowered for alias in aliases):
            return product.replace(" ", "-")
    return ""


def _event_family_key(item: FeedItem) -> tuple[str, str] | None:
    text = clean_text(f"{item.title} {item.summary} {item.source_name}")
    title = clean_text(item.title).lower().replace("‐", "-").replace("‑", "-")
    title_entity = detect_entity(title)
    entity = SOURCE_ENTITY_OVERRIDES.get(item.source_id) or (title_entity if title_entity != "AI" else detect_entity(text))
    # A product mentioned only in background copy must not redefine the event.
    # Event-family grouping is therefore anchored to the headline, while the
    # summary remains available later as supporting evidence.
    product = _named_product(title)
    model = _normalized_model_name(title)
    if any(term in title for term in ORGANIZATION_EVENT_TERMS):
        return None
    # Named products are independent subevents even when their body mentions
    # the model that powers them.
    if product and any(term in title for term in ["发布", "推出", "上线", "新增", "更新", "most ambitious work"]):
        return f"{entity}:event-product-{product}", entity
    if not model:
        return None
    is_subevent = any(term in title for term in SUBEVENT_TERMS)
    is_launch = any(term in title for term in LAUNCH_ACTION_TERMS)
    # Media deep-dives often avoid the literal word "发布". If the headline is
    # model-centric and not a named subevent, group it with the launch wave.
    model_centric = title.startswith(model) or title.find(model) <= 18
    if not is_subevent and (is_launch or model_centric):
        return f"{entity}:event-model-launch-{model}", entity
    return None


def cluster_key(item: FeedItem) -> tuple[str, str]:
    event_family = _event_family_key(item)
    if event_family:
        return event_family
    if item.source_id in SOURCE_ENTITY_OVERRIDES:
        entity = SOURCE_ENTITY_OVERRIDES[item.source_id]
        family_key = _hf_model_family_key(item) if item.source_id.endswith("_hf_models") else ""
        if family_key:
            token_part = "hf-" + family_key.replace("/", "-")
        else:
            tokens = _tokens(item.title) or _tokens(item.summary)
            token_part = "-".join(tokens[:5]) if tokens else stable_hash([item.title])[:12]
        return f"{entity}:{token_part}", entity
    entity = detect_entity(item.title)
    if entity == "AI":
        entity = detect_entity(f"{item.title} {item.source_name}")
    if entity == "AI":
        body = f"{item.title} {item.summary} {item.source_name}"
        broad_entity = detect_entity(body)
        if broad_entity in {"具身智能 / 机器人", "论文 / arXiv"}:
            entity = broad_entity
    tokens = _tokens(item.title) or _tokens(item.summary)
    token_part = "-".join(tokens[:5]) if tokens else stable_hash([item.title])[:12]
    return f"{entity}:{token_part}", entity


def cluster_items(items: list[FeedItem]) -> list[Cluster]:
    buckets: dict[str, list[FeedItem]] = defaultdict(list)
    entities: dict[str, str] = {}
    for item in items:
        key, entity = cluster_key(item)
        buckets[key].append(item)
        entities[key] = entity
    clusters: list[Cluster] = []
    for key, grouped in buckets.items():
        # A freshly fetched undated listing page must never outrank a dated
        # article. Otherwise navigation copy becomes the lead fact for a real
        # release and contaminates the entire card.
        grouped.sort(
            key=lambda it: (
                # A dated first-party article defines the event; later media
                # coverage corroborates it but must not rename or reroute it.
                1 if it.published_at and it.source_reliability == "official" else 0,
                1 if it.published_at else 0,
                it.published_at or datetime.min.replace(tzinfo=UTC),
                0 if str((it.raw or {}).get("kind") or "") == "official_web_discovery" else 1,
                it.fetched_at,
            ),
            reverse=True,
        )
        latest = max([x.published_at for x in grouped if x.published_at], default=None)
        lead_title = grouped[0].title
        if re.fullmatch(r"v?\d+(?:\.\d+){1,4}(?:[-._a-zA-Z0-9]+)?", lead_title.strip()):
            lead_title = f"{grouped[0].source_name} 发布 {lead_title}"
        clusters.append(Cluster(key=key, title=lead_title, items=grouped, entity=entities.get(key, "AI"), published_at=latest))
    clusters.sort(key=lambda c: (c.published_at or datetime.min.replace(tzinfo=UTC)), reverse=True)
    return clusters

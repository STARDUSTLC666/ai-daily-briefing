from __future__ import annotations

import re
from dataclasses import dataclass

from .models import EvidenceCard
from .text import clean_text


@dataclass(frozen=True, slots=True)
class BrandEntry:
    canonical: str
    cluster_label: str
    aliases: tuple[str, ...]
    official_domains: tuple[str, ...] = ()
    mainstream: bool = True


# Canonical AI entity & vendor registry used across ingest, clustering, verification,
# editorial planning, and viewer-facing presentation.
AI_BRANDS: tuple[BrandEntry, ...] = (
    BrandEntry("OpenAI", "OpenAI", ("openai", "chatgpt", "gpt-", "gpt ", "sora", "codex", "o1", "o3", "o4"), ("openai.com", "chatgpt.com")),
    BrandEntry("Anthropic", "Anthropic / Claude", ("anthropic", "claude"), ("anthropic.com", "claude.ai")),
    BrandEntry("Google", "Google / Gemini", ("google", "gemini", "deepmind", "gemma", "notebooklm", "veo"), ("blog.google", "deepmind.google", "ai.google.dev", "developers.googleblog.com")),
    BrandEntry("阿里千问", "Qwen / 阿里", ("qwen", "通义", "千问", "alibaba", "modelscope", "魔搭"), ("qwenlm.github.io", "qwen.ai", "modelscope.cn", "alibabagroup.com")),
    BrandEntry("DeepSeek", "DeepSeek", ("deepseek", "深度求索"), ("deepseek.com", "api-docs.deepseek.com")),
    BrandEntry("Kimi", "Kimi / 月之暗面", ("kimi", "moonshot", "月之暗面"), ("moonshot.cn", "kimi.ai", "kimi.moonshot.cn")),
    BrandEntry("MiniMax", "MiniMax", ("minimax", "海螺", "hailuo", "abab"), ("minimaxi.com", "minimax.io")),
    BrandEntry("智谱", "智谱 / GLM", ("智谱", "zhipu", "glm", "chatglm", "z.ai", "autoglm"), ("zhipuai.cn", "bigmodel.cn", "z.ai")),
    BrandEntry("字节跳动", "字节 / 豆包", ("豆包", "doubao", "bytedance", "字节", "seedance", "trae", "coze"), ("seed.bytedance.com", "volcengine.com", "doubao.com")),
    BrandEntry("腾讯混元", "腾讯混元", ("混元", "hunyuan", "tencent", "腾讯元宝"), ("hunyuan.tencent.com", "cloud.tencent.com")),
    BrandEntry("百度文心", "百度文心", ("文心", "ernie", "baidu", "飞桨", "paddlepaddle"), ("yiyan.baidu.com", "paddlepaddle.org.cn")),
    BrandEntry("Mistral AI", "Mistral", ("mistral", "mixtral", "codestral", "pixtral", "le chat"), ("mistral.ai",)),
    BrandEntry("Meta", "Meta / Llama", ("meta ai", "llama"), ("ai.meta.com", "llama.com")),
    BrandEntry("xAI", "xAI / Grok", ("xai", "x.ai", "grok"), ("x.ai",)),
    BrandEntry("NVIDIA", "NVIDIA", ("nvidia", "英伟达", "tensorrt", "nim", "blackwell", "cuda"), ("nvidia.com", "developer.nvidia.com", "blogs.nvidia.com")),
    BrandEntry("Microsoft", "Microsoft / Copilot", ("microsoft", "azure ai", "phi-"), ("microsoft.com", "blogs.microsoft.com", "azure.microsoft.com")),
    BrandEntry("GitHub", "GitHub / Copilot", ("github", "copilot"), ("github.blog", "github.com")),
    BrandEntry("Apple", "Apple", ("apple intelligence", "mlx"), ("machinelearning.apple.com", "apple.com")),
    BrandEntry("Amazon", "Amazon / AWS", ("bedrock", "amazon nova", "aws ai"), ("aws.amazon.com", "amazon.science")),
    BrandEntry("Hugging Face", "Hugging Face", ("hugging face", "huggingface", "transformers", "smollm"), ("huggingface.co",)),
    BrandEntry("蚂蚁集团", "蚂蚁 / LingBot", ("lingbot", "蚂蚁灵波", "蚂蚁集团", "inclusionai"), ("antgroup.com",), mainstream=False),
    BrandEntry("开源生态", "本地模型 / 开源", ("ollama", "llama.cpp", "vllm", "sglang", "local llm", "open weights", "开源模型", "本地部署"), ("ollama.com", "vllm.ai"), mainstream=False),
    BrandEntry("具身智能", "具身智能 / 机器人", ("具身智能", "人形机器人", "unitree", "宇树", "figure ai", "智元"), (), mainstream=False),
    BrandEntry("论文", "论文 / arXiv", ("arxiv", "paper", "论文"), ("arxiv.org",), mainstream=False),
)

SOURCE_ENTITY_OVERRIDES: dict[str, str] = {
    "modelscope_github_releases": "Qwen / 阿里",
    "qwen_github_releases": "Qwen / 阿里",
    "qwen_hf_models": "Qwen / 阿里",
    "deepseek_v3_releases": "DeepSeek",
    "deepseek_r1_releases": "DeepSeek",
    "deepseek_hf_models": "DeepSeek",
    "kimi_k2_releases": "Kimi / 月之暗面",
    "moonshot_hf_models": "Kimi / 月之暗面",
    "zai_glm45_releases": "智谱 / GLM",
    "zai_hf_models": "智谱 / GLM",
    "tencent_hunyuan_releases": "腾讯混元",
    "baidu_ernie_releases": "百度文心",
    "paddlepaddle_hf_models": "百度文心",
    "bytedance_seed_releases": "字节 / 豆包",
    "bytedance_seed_hf_models": "字节 / 豆包",
    "minimax_hf_models": "MiniMax",
    "mistral_hf_models": "Mistral",
    "openai_news": "OpenAI",
    "openai_codex_releases": "OpenAI",
    "google_research": "Google / Gemini",
    "google_ai_blog": "Google / Gemini",
    "google_hf_models": "Google / Gemini",
    "huggingface_blog": "Hugging Face",
    "github_changelog": "GitHub / Copilot",
    "nvidia_developer_blog": "NVIDIA",
}

MODEL_TOKEN_RE = re.compile(
    r"\b(?:gpt|claude|gemini|gemma|qwen|deepseek|kimi|llama|mistral|mixtral|codestral|grok|minimax|glm|hunyuan|ernie|doubao|phi|o)"
    r"[-‐‑–—_\s]?[a-z]*\d[A-Za-z0-9_.‐‑–—-]*(?=$|[^A-Za-z0-9_.‐‑–—-])",
    flags=re.I,
)

HF_MODEL_ID_RE = re.compile(r"\b([A-Za-z0-9][\w.-]{1,40}/[A-Za-z0-9][\w.-]{1,80})\b")
HF_VARIANT_TOKENS = frozenset({
    "instruct", "chat", "base", "it", "pt", "gguf", "awq", "gptq", "fp8", "fp4", "bf16", "int4", "int8",
    "mlx", "bnb", "preview", "exp", "experimental", "beta", "alpha", "turbo", "flash", "pro", "mini", "lite",
    "nano", "plus", "max", "ultra", "coder", "vl", "vision", "audio", "math", "reasoning", "thinking",
})

STRONG_AI_KEYWORDS: tuple[str, ...] = (
    "大模型", "生成式ai", "生成式 ai", "llm", "aigc", "智能体", "agent", "具身智能", "世界模型",
    "基座模型", "多模态", "开源模型", "ai 编程", "ai编程", "推理模型", "微调", "上下文窗口",
    "token", "rag", "mcp", "vlm", "diffusion", "tts", "asr",
    *(alias for b in AI_BRANDS if b.mainstream for alias in b.aliases if len(alias) >= 3),
)

WEAK_AI_KEYWORDS: tuple[str, ...] = (
    "ai", "人工智能", "模型", "机器人", "人形机器人", "算法", "算力", "芯片", "gpu", "benchmark", "评测",
)

FINANCE_NOISE_KEYWORDS: tuple[str, ...] = (
    "股价", "涨停", "跌停", "概念股", "财报", "季度营收", "减持", "分红", "etf", "目标价", "券商评级",
)

PAPER_ENTITY_LABELS = {"论文 / arxiv", "论文/arxiv", "论文", "arxiv"}
GENERIC_ENTITY_LABELS = {"", "ai", "人工智能"}


def detect_entity(text: str, *, source_id: str = "") -> str:
    if source_id and source_id in SOURCE_ENTITY_OVERRIDES:
        return SOURCE_ENTITY_OVERRIDES[source_id]
    lowered = clean_text(text).lower()
    for entry in AI_BRANDS:
        if any(alias in lowered for alias in entry.aliases):
            return entry.cluster_label
    return "AI"


def infer_brand(text: str) -> str:
    lowered = clean_text(text).lower()
    for entry in AI_BRANDS:
        if entry.canonical in {"开源生态", "具身智能", "论文"}:
            continue
        if any(alias in lowered for alias in entry.aliases):
            return entry.canonical
        if any(dom in lowered for dom in entry.official_domains):
            return entry.canonical
    return ""


def extract_model_names(text: str) -> list[str]:
    seen: list[str] = []
    for match in MODEL_TOKEN_RE.finditer(clean_text(text)):
        token = re.sub(r"[\s‐‑–—_]+", "-", match.group(0).strip(".-_ "))
        key = token.lower()
        if key not in {x.lower() for x in seen}:
            seen.append(token)
    return seen


def extract_hf_model_id(text: str) -> str:
    for match in HF_MODEL_ID_RE.finditer(text or ""):
        candidate = match.group(1)
        org, repo = candidate.split("/", 1)
        if org.lower() in {"http", "https", "github.com", "huggingface.co", "x.com", "arxiv.org", "models", "datasets"}:
            continue
        if re.search(r"\d", repo) or any(alias in candidate.lower() for b in AI_BRANDS for alias in b.aliases):
            return candidate
    return ""


def model_family_key(model_id_or_name: str) -> str:
    raw = (model_id_or_name or "").strip().lower()
    if not raw:
        return ""
    org, _, name = raw.partition("/")
    if not name:
        name, org = org, ""
    tokens = [tok for tok in re.split(r"[-_.\s]+", name) if tok]
    core: list[str] = []
    for tok in tokens:
        if re.fullmatch(r"\d+(?:\.\d+)?b", tok):  # parameter size e.g. 7b, 72b, 0.6b
            continue
        if tok in HF_VARIANT_TOKENS:
            continue
        core.append(tok)
    stem = "-".join(core[:3] or tokens[:2])
    return f"{org}/{stem}" if org else stem


def is_ai_relevant(title: str, summary: str = "") -> tuple[bool, int]:
    """Return `(is_relevant, relevance_score)` using unified AI signal scoring."""
    combined = f"{clean_text(title)} {clean_text(summary)}".lower()
    title_low = clean_text(title).lower()
    score = 0
    if any(kw in title_low for kw in STRONG_AI_KEYWORDS):
        score += 4
    elif any(kw in combined for kw in STRONG_AI_KEYWORDS):
        score += 2
    if MODEL_TOKEN_RE.search(combined):
        score += 3
    if any(kw in title_low for kw in WEAK_AI_KEYWORDS):
        score += 1
    if any(noise in title_low for noise in FINANCE_NOISE_KEYWORDS) and score < 4:
        return False, 0
    return score >= 2, score


def is_mainstream_ai(text: str, entity: str = "") -> bool:
    lowered = f"{entity} {clean_text(text)}".lower()
    if MODEL_TOKEN_RE.search(lowered):
        return True
    return any(
        entry.mainstream and (entry.canonical.lower() in lowered or any(alias in lowered for alias in entry.aliases))
        for entry in AI_BRANDS
    )


def all_official_domains() -> frozenset[str]:
    return frozenset(dom for entry in AI_BRANDS for dom in entry.official_domains)


def has_arxiv_evidence(card: EvidenceCard) -> bool:
    return any(
        str(ev.get("source") or "").lower().startswith("arxiv")
        or "arxiv.org/" in str(ev.get("url") or ev.get("final_url") or "").lower()
        for ev in card.evidence_links
    )


def is_paper_entity(value: str) -> bool:
    normalized = clean_text(value or "").lower().replace(" ", "")
    return normalized in {label.replace(" ", "") for label in PAPER_ENTITY_LABELS}


def card_display_entity(card: EvidenceCard, fallback: str = "AI") -> str:
    raw_entity = clean_text(card.entity or "")
    normalized = raw_entity.lower().replace(" ", "")
    if normalized not in {label.replace(" ", "") for label in GENERIC_ENTITY_LABELS} and not is_paper_entity(raw_entity):
        return raw_entity.split("/", 1)[0].strip() or fallback

    evidence_text = " ".join(
        f"{ev.get('source', '')} {ev.get('title', '')} {ev.get('url', '') or ev.get('final_url', '')}"
        for ev in card.evidence_links
    )
    inferred = infer_brand(evidence_text) or infer_brand(" ".join([card.event_title, *card.key_facts]))
    if inferred:
        return inferred
    if is_paper_entity(raw_entity) and has_arxiv_evidence(card):
        return "论文"
    return fallback

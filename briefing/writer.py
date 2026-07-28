from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from .editor import community_observation_summary, content_quality_profile, official_personnel_signal_summary, official_performance_summary
from .editorial_plan import build_editorial_plan
from .models import EvidenceCard, SourceHealth
from .presentation import card_display_entity
from .selection_optimizer import CandidateProfile, optimize_portfolio
from .story_model import build_story_spec
from .storyboard import build_storyboard
from .scoring import horizon_enrichment, mainstream_priority_rank, public_source_status, selection_quality_score, story_category, story_category_label
from .social_signals import card_has_x_post, card_is_community_signal, card_is_official_personnel_signal
from .util import clean_text, ensure_dir, fmt_dt, format_mmss

UTC = timezone.utc

INTRO_SECONDS = 11
OUTRO_SECONDS = 5
NEWS_SECONDS_DEFAULT = 30
NEWS_SECONDS_CONFIRMED = 32
NEWS_SECONDS_SIGNAL = 28
NEWS_SECONDS_MAX = 35
BILI_DESC_LIMIT = 250


def _public_card_entity(card: EvidenceCard) -> str:
    """Prefer repaired story semantics over a stale discovery bucket."""
    spec_entity = clean_text(build_story_spec(card).entity)
    display_entity = clean_text(card_display_entity(card))
    if spec_entity and spec_entity != clean_text(card.entity):
        return spec_entity
    return display_entity or spec_entity or "AI"

REJECTED_REVIEW_SIGNAL_KEYWORDS = [
    "非主流 AI",
    "不属于主流 AI",
    "社区/聚合单源",
    "benchmark",
    "performance",
    "eval",
    "leaderboard",
    "arena",
    "degraded performance",
    "works pretty well",
    "open weights",
    "weights are now open",
    "released",
    "实测",
    "测评",
    "评测",
    "性能",
    "跑分",
    "榜单",
    "用户反馈",
    "实际使用",
    "开源",
    "权重",
]

REJECTED_REVIEW_NOISE_KEYWORDS = [
    "best choice",
    "considering buying",
    "need help",
    "help with",
    "how a ",
    "trying to make",
    "who has the",
    "fun contest",
    "no prizes",
    "appreciation post",
    "using llama.cpp with pi",
    "setup",
    "alternatives to",
]

MODEL_VARIANT_LABELS = {
    "flash": "Flash",
    "pro": "Pro",
    "lite": "Lite",
    "turbo": "Turbo",
    "jax": "JAX",
    "pytorch": "PyTorch",
    "torch": "Torch",
    "tensorflow": "TensorFlow",
    "tf": "TF",
    "onnx": "ONNX",
    "gguf": "GGUF",
    "mlx": "MLX",
    "hf": "HF",
    "fp8": "FP8",
    "fp16": "FP16",
    "bf16": "BF16",
    "int4": "INT4",
    "int8": "INT8",
}

SELECTION_CATEGORY_SOFT_LIMITS = {
    "model_repository": 3,
    "research": 1,
    "community_signal": 1,
    "official_personnel_signal": 1,
    "media_report": 3,
}

GENERIC_DISCOVERY_TITLES = {
    "news",
    "新闻",
    "blog",
    "博客",
    "research",
    "研究",
    "models",
    "model",
    "模型",
    "docs",
    "documentation",
    "文档",
    "pricing",
    "价格",
    "首页",
    "home",
    "updates",
    "动态",
}

GENERIC_DISCOVERY_PATHS = {
    "",
    "/",
    "/news",
    "/blog",
    "/blogs",
    "/research",
    "/model",
    "/models",
    "/docs",
    "/documentation",
    "/pricing",
    "/updates",
}

THIN_DISCOVERY_PHRASES = [
    "官网新闻页",
    "新闻页",
    "页面有更新",
    "页面更新",
    "信息不够",
    "信息不足",
    "了解更多",
    "查看更多",
]

PUBLISHABLE_DETAIL_KEYWORDS = [
    "api",
    "sdk",
    "release",
    "changelog",
    "benchmark",
    "leaderboard",
    "aime",
    "gpqa",
    "swe-bench",
    "livecodebench",
    "license",
    "download",
    "weights",
    "tokenhub",
    "workbuddy",
    "codebuddy",
    "docker",
    "hubapi",
    "session",
    "发布",
    "上线",
    "推出",
    "更新",
    "升级",
    "新增",
    "修复",
    "接入",
    "覆盖",
    "支持",
    "开放",
    "开源",
    "权重",
    "参数",
    "上下文",
    "多模态",
    "语音",
    "视频",
    "价格",
    "入口",
    "榜单",
    "评测",
    "性能",
    "许可",
    "下载",
    "免费",
]

THIN_SPECIAL_SOURCE_PHRASES = [
    "release metadata",
    "release 未写变更",
    "release没有变更",
    "未写变更",
    "页面出现更新",
    "页面有更新",
    "模型页出现更新",
    "模型仓库更新",
    "last modified",
    "lastmodified",
    "仅显示版本号",
    "只给出版本号",
    "未写明具体变更",
    "未写明变更说明",
    "release 页面未写明变更",
    "需要查看 github compare",
    "需要查看 compare",
    "commit 记录",
    "没有说明",
]

GITHUB_RELEASE_CHANGE_KEYWORDS = [
    "新增",
    "修复",
    "改进",
    "变更",
    "移除",
    "弃用",
    "支持",
    "兼容",
    "breaking",
    "fix",
    "feat",
    "improve",
    "deprecat",
    "commit",
]

MODEL_ARTIFACT_KEYWORDS = [
    "权重",
    "下载",
    "许可",
    "license",
    "参数",
    "上下文",
    "token",
    "api",
    "模型卡",
    "safetensors",
    "gguf",
    "onnx",
    "pytorch",
    "open weights",
    "开源",
    "benchmark",
    "榜单",
    "性能",
]

RESEARCH_DETAIL_KEYWORDS = [
    "提出",
    "方法",
    "实验",
    "数据集",
    "benchmark",
    "基准",
    "准确",
    "提升",
    "性能",
    "推理",
    "评测",
    "结果",
    "参数",
    "token",
    "%",
]

SOCIAL_PRODUCT_HINTS = [
    "chatgpt",
    "codex",
    "claude",
    "gemini",
    "grok",
    "qwen",
    "deepseek",
    "kimi",
    "glm",
    "minimax",
    "sora",
]


def _write_md(path: Path, text: str) -> None:
    # UTF-8 with BOM makes PowerShell 5.1 / Windows Notepad display Chinese reliably.
    path.write_text(text, encoding="utf-8-sig")


def _date_suffix(run_date: str) -> str:
    date_part = (run_date or "")[:10]
    try:
        dt = datetime.fromisoformat(date_part)
        stamp = dt.strftime("%Y-%m-%d")
    except ValueError:
        stamp = run_date or datetime.now().strftime("%Y-%m-%d")
    return f"【AI 日报 {stamp}】"


def _clean_title_entity(value: str) -> str:
    return " ".join((value or "").replace("/", " ").split())


def _clean_title_text(value: str) -> str:
    text = " ".join((value or "").split())
    replacements = {
        "Hugging Face 模型仓库更新：": "",
        "Hugging Face 模型仓库更新:": "",
        "Hugging Face 模型仓库更新": "",
        "Hugging Face 模型更新": "",
        "Hugging Face Models": "",
        "GitHub Releases 发现": "",
        "GitHub Releases": "",
        "GitHub Release": "",
    }
    for old, new in replacements.items():
        text = text.replace(old, new).strip(" -｜|:")
    text = re.sub(
        r"\s*(?:[|｜/]\s*|[-—]\s*)?(?:36\s*氪|InfoQ)(?:独家|首发)?(?:\.cn)?\s*$",
        "",
        text,
        flags=re.I,
    ).strip(" -｜|:")
    return " ".join(text.split())


def _brand_name(value: str) -> str:
    raw = (value or "").strip()
    lowered = raw.lower()
    mapping = {
        "mistralai": "Mistral AI",
        "mistral": "Mistral AI",
        "qwen": "Qwen",
        "openai": "OpenAI",
        "google": "Google",
        "deepseek": "DeepSeek",
        "deepseek-ai": "DeepSeek",
        "moonshotai": "Kimi",
        "zhipuai": "智谱",
        "bytedance-seed": "豆包 Seed",
        "baidu": "百度文心",
        "tencent": "腾讯混元",
        "modelscope": "ModelScope",
        "huggingface": "Hugging Face",
    }
    return mapping.get(lowered, raw)


def _is_model_page_update(card: EvidenceCard) -> bool:
    raw = " ".join(
        [
            card.event_title,
            card.entity,
            " ".join(str(e.get("source") or "") for e in card.evidence_links),
            " ".join(str(e.get("url") or "") for e in card.evidence_links),
        ]
    ).lower()
    return "hugging face" in raw or "huggingface.co" in raw or "模型仓库更新" in raw or "模型页更新" in raw


def _human_headline(card: EvidenceCard | None) -> str:
    if not card:
        return "AI 日报"
    community = community_observation_summary(card, card.event_title)
    if community:
        return str(community["title"])
    model_page_update = _is_model_page_update(card)
    title = _clean_title_text(card.event_title)
    if "/" in title and len(title) < 90:
        org, name = title.split("/", 1)
        org = _brand_name(org)
        verb = "模型页更新" if model_page_update else "发布"
        title = f"{org} {verb} {name.strip()}"
    elif model_page_update and "发布" in title:
        title = title.replace("发布", "模型页更新")
    if not title:
        entity = _clean_title_entity(_public_card_entity(card)) or "AI"
        title = f"{entity} 今日动态"
    return title


PUBLICATION_NEUTRALIZE_PATTERNS = [
    r"跑路", r"狂喜", r"炸裂", r"刚刚", r"重磅", r"突发", r"刷屏",
    r"消失.{0,12}(?:后|发帖)", r"只为(?:它|他|她|这)", r"最强", r"卖[“”\"']?低价",
    r"[?？]{2,}", r"[!！]{3,}",
]


def _publication_headline(card: EvidenceCard | None) -> str:
    if not card:
        return _human_headline(card)
    human = _human_headline(card)
    raw = clean_text(card.event_title)
    plan = build_editorial_plan(card)
    organization_story = any(
        claim.claim_type == "organization" and claim.renderable and claim.verifiable
        for claim in build_story_spec(card).claims
    )
    if "预发布" in plan.title:
        return plan.title
    if any(re.search(pattern, raw, flags=re.I) for pattern in PUBLICATION_NEUTRALIZE_PATTERNS):
        return plan.title or human
    if organization_story and plan.title:
        return plan.title
    spec = build_story_spec(card)
    if spec.source_status in {"single_media", "media_cross_checked", "community"} and plan.title:
        # Media discovery headlines often carry a publisher suffix or state an
        # unconfirmed report as a direct fact. Public metadata must match the
        # same attributable title used by narration and rendered cards.
        return plan.title
    return human


def _fit_title(base: str, suffix: str, max_len: int = 72) -> str:
    base = " ".join((base or "AI 日报").split()).strip(" ,，。:：|-｜")
    base = base.replace("震惊", "").replace("炸裂", "").strip()
    room = max(12, max_len - len(suffix))
    if len(base) > room:
        base = base[: max(1, room)].rstrip(" ,，。:：|-｜")
    return base + suffix


def _title_candidates(cards: list[EvidenceCard], run_date: str) -> list[str]:
    from .edition_brief import get_edition_brief
    brief = get_edition_brief(cards)
    suffix = _date_suffix(run_date)
    # Do not let an unannounced X/community signal become the video headline.
    by_id = {build_story_spec(card).story_id: card for card in cards}
    lead_card = by_id.get(str(brief.get("lead_story_id"))) or next(
        (card for card in cards if not card_is_official_personnel_signal(card) and not card_is_community_signal(card)),
        cards[0] if cards else None,
    )
    lead_headline = _publication_headline(lead_card)
    secondary_card = by_id.get(str(brief.get("secondary_story_id")))
    if secondary_card and (card_is_official_personnel_signal(secondary_card) or card_is_community_signal(secondary_card)):
        secondary_card = next(
            (
                card
                for card in cards
                if card is not lead_card
                and not card_is_official_personnel_signal(card)
                and not card_is_community_signal(card)
            ),
            None,
        )
    secondary_headline = _publication_headline(secondary_card) if secondary_card else ""
    entities: list[str] = []
    for c in cards:
        entity = _brand_name(_clean_title_entity(_public_card_entity(c)))
        if entity and entity != "AI" and entity not in entities:
            entities.append(entity)
    lead_entities = "、".join(entities[:2]) if entities else "AI"
    count = len(cards)
    lead_is_fallback = not _has_cjk(lead_headline) or _looks_raw_long_english(lead_headline)
    if lead_is_fallback:
        lead_headline = f"AI 日报：{lead_entities} 等 {count} 条动态"
    bases = [
        lead_headline,
        f"{lead_headline}；{secondary_headline}" if secondary_headline else lead_headline,
        f"{lead_entities}：{brief.get('theme') or '今日 AI 变化'}",
        f"AI 日报：{lead_entities} 等 {count} 条动态",
        f"{lead_entities} 领衔，AI 圈今日更新",
        "AI 模型、工具与行业动态速览",
        "今天值得看的 AI 新闻汇总",
    ]
    if secondary_headline and not lead_is_fallback:
        # Two real hooks beat one: agent-reviewed editions consistently pick
        # the double-headline form as the upload title, so the automatic
        # default matches it — but only when the pair fits without truncation.
        combined = f"{lead_headline}；{secondary_headline}"
        if len(combined) <= max(12, 72 - len(suffix)):
            bases.insert(0, combined)
    seen: set[str] = set()
    titles: list[str] = []
    for base in bases:
        title = _fit_title(base, suffix)
        if title not in seen:
            titles.append(title)
            seen.add(title)
    return titles[:5]


def _primary_source(card: EvidenceCard) -> str:
    if not card.evidence_links:
        return "unknown"
    return str(card.evidence_links[0].get("source") or "unknown")


def _is_arxiv(card: EvidenceCard) -> bool:
    for evidence in card.evidence_links:
        source = str(evidence.get("source") or "").lower()
        url = str(evidence.get("url") or evidence.get("final_url") or "").lower()
        if source.startswith("arxiv") or "arxiv.org/" in url:
            return True
    return False


def _is_github_release(card: EvidenceCard) -> bool:
    raw = " ".join(
        [
            card.event_title,
            " ".join(str(e.get("source") or "") for e in card.evidence_links),
            " ".join(str(e.get("url") or "") for e in card.evidence_links),
        ]
    ).lower()
    return "github releases" in raw or "github release" in raw or "/releases/tag/" in raw


def _model_id_from_card(card: EvidenceCard) -> str:
    for evidence in card.evidence_links:
        url = str(evidence.get("url") or "")
        match = re.search(r"huggingface\.co/([^/?#]+/[^/?#]+)", url, re.I)
        if match:
            return match.group(1).strip("/")
    values = [card.event_title]
    values.extend(str(e.get("title") or "") for e in card.evidence_links)
    for value in values:
        text = _clean_title_text(value).strip().strip("/")
        match = re.search(r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)", text)
        if match:
            return match.group(1).strip("/")
    return ""


def _model_family_parts(model_id: str) -> tuple[str, str, list[str]]:
    parts = model_id.strip().strip("/").split("/")
    org = parts[-2] if len(parts) >= 2 else ""
    name = parts[-1] if parts else model_id
    family_tokens: list[str] = []
    variant_labels: list[str] = []
    for token in re.split(r"[-_]+", name):
        if not token:
            continue
        label = MODEL_VARIANT_LABELS.get(token.lower())
        if label:
            if label not in variant_labels:
                variant_labels.append(label)
            continue
        family_tokens.append(token)
    family = "-".join(family_tokens) or name
    family = re.sub(r"-{2,}", "-", family).strip("-")
    return org, family, variant_labels


def _model_family_key(card: EvidenceCard) -> str:
    if not _is_model_page_update(card):
        return ""
    model_id = _model_id_from_card(card)
    if not model_id:
        return ""
    org, family, _variants = _model_family_parts(model_id)
    entity = _clean_title_entity(card.entity).lower()
    return f"model-page|{entity}|{org.lower()}|{family.lower()}"


def _event_story_key(card: EvidenceCard) -> str:
    text = clean_text(
        " ".join(
            [
                card.entity,
                card.event_title,
                " ".join(card.key_facts),
                " ".join(str(e.get("title") or "") for e in card.evidence_links),
            ]
        )
    ).lower()
    if ("hy3" in text or "混元hy3" in text) and ("腾讯" in text or "混元" in text):
        return "event|tencent-hunyuan-hy3-release"
    if "fun-asr-realtime" in text or ("实时语音识别" in text and ("千问" in text or "qwen" in text)):
        return "event|qwen-fun-asr-realtime"
    return ""


def _daily_topic_family(card: EvidenceCard) -> str:
    """Generic editorial family produced by the structured story model."""
    spec = build_story_spec(card)
    family = spec.family_key or spec.topic_key
    if not family:
        return ""
    # Product names such as ChatGPT, Codex, Claude, or Gemini are far too broad
    # to prove event identity.  Grouping on those names alone merged unrelated
    # availability, quota, and personnel updates into one story.  Exact
    # official URLs, model families, and explicit event keys are handled by the
    # stronger branches above; keep generic product events separate here.
    broad_product = family.rsplit("|", 1)[-1]
    if not family.startswith("model|") and broad_product in {
        "chatgpt",
        "codex",
        "copilot",
        "claude",
        "gemini",
        "qwen",
        "deepseek",
        "kimi",
        "minimax",
        "mistral",
        "llama",
        "grok",
        "vllm",
        "ollama",
        "bun",
    }:
        return ""
    return family


def _official_personnel_topic_family(card: EvidenceCard) -> str:
    """按精确账号和主题合并官方人员短帖，避免仅凭产品名串线。"""
    if not card_is_official_personnel_signal(card):
        return ""
    handles: set[str] = set()
    for evidence in card.evidence_links:
        url = str(evidence.get("url") or evidence.get("final_url") or "")
        match = re.search(r"(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,32})/status/", url, flags=re.I)
        if match:
            handles.add(match.group(1).lower())
    if len(handles) != 1:
        return ""
    blob = clean_text(
        " ".join(
            [card.event_title, *card.key_facts]
            + [str(evidence.get("title") or "") for evidence in card.evidence_links]
            + [str(evidence.get("excerpt") or "") for evidence in card.evidence_links]
        )
    ).lower()
    product_terms = {"codex", "chatgpt work", "gpt-5.6 sol"}
    usage_terms = {
        "usage",
        "quota",
        "reserve",
        "reset",
        "subscription",
        "inference optimization",
        "context size",
        "\u7528\u91cf",  # 用量
        "\u989d\u5ea6",  # 额度
        "\u50a8\u5907",  # 储备
        "\u91cd\u7f6e",  # 重置
        "\u8ba2\u9605",  # 订阅
        "\u63a8\u7406\u4f18\u5316",  # 推理优化
        "\u4e0a\u4e0b\u6587",  # 上下文
        "\u9650\u5236",  # 限制
    }
    if not any(term in blob for term in product_terms):
        return ""
    handle = next(iter(handles))
    if any(term in blob for term in usage_terms):
        return f"personnel|{handle}|usage-subscription"
    if "chatgpt work" not in blob:
        return ""
    demo_terms = {"workflow demo", "demo video", "工作流演示", "演示视频", "实测演示"}
    if any(term in blob for term in demo_terms):
        return ""
    early_access_terms = {
        "early access", "early-access", "preview group", "recruit", "招募", "征集", "早期体验", "体验组",
    }
    capability_scope_terms = {
        "web and mobile", "web、app", "网页与移动端", "网页端", "移动端", "create and host", "托管网站",
        "manage email", "管理电子邮件", "汇总大量文档", "文档、表格和幻灯片",
    }
    if any(term in blob for term in early_access_terms | capability_scope_terms):
        return f"personnel|{handle}|chatgpt-work|rollout-early-access"
    return ""


def _story_key(card: EvidenceCard) -> str:
    family_key = _model_family_key(card)
    if family_key:
        return family_key
    event_key = _event_story_key(card)
    if event_key:
        return event_key
    title = _clean_title_text(card.event_title).lower()
    source = _primary_source(card).lower()
    for suffix in ["-pytorch", "-torch", "-jax", "-tf", "-tensorflow", "-onnx", "-gguf", "-hf"]:
        if title.endswith(suffix):
            title = title[: -len(suffix)]
            break
    title = title.replace("_", "-").replace("/", "-")
    while "--" in title:
        title = title.replace("--", "-")
    return f"{card.entity.lower()}|{source}|{title.strip(' -:：')}"


def _unique_evidence(cards: list[EvidenceCard]) -> list[dict[str, str]]:
    seen: set[str] = set()
    merged: list[dict[str, str]] = []
    for card in cards:
        for evidence in card.evidence_links:
            key = str(evidence.get("url") or evidence.get("title") or evidence.get("source") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(dict(evidence))
    return merged[:8]


def _canonical_official_evidence_url(card: EvidenceCard) -> str:
    candidates: list[tuple[int, str]] = []
    for evidence in card.evidence_links:
        reliability = str(evidence.get("reliability") or "").lower()
        tier = str(evidence.get("tier") or "").upper()
        if reliability not in {"official", "official_social"} and tier != "A":
            continue
        raw = str(evidence.get("final_url") or evidence.get("url") or "").strip()
        if not raw:
            continue
        try:
            parsed = urlparse(raw)
        except Exception:
            continue
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        query = "&".join(
            part for part in parsed.query.split("&")
            if part and not part.lower().startswith(("utm_", "ref=", "source="))
        )
        path = (parsed.path or "/").rstrip("/") or "/"
        if path == "/":
            continue
        normalized = parsed._replace(
            scheme="https",
            netloc=parsed.netloc.lower().removeprefix("www."),
            path=path,
            params="",
            query=query,
            fragment="",
        ).geturl()
        priority = 2 if _truthy(evidence.get("reconciled_official")) else 1
        candidates.append((priority, normalized))
    candidates.sort(reverse=True)
    return candidates[0][1] if candidates else ""


def _primary_evidence_is_official(card: EvidenceCard) -> bool:
    if not card.evidence_links:
        return False
    evidence = card.evidence_links[0]
    return str(evidence.get("reliability") or "").lower() in {"official", "official_social"} or str(evidence.get("tier") or "").upper() == "A"


def _unique_text(values: list[str], limit: int = 12) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = " ".join(str(value or "").split())
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _has_unusable_required_screenshot(card: EvidenceCard) -> bool:
    for evidence in card.evidence_links:
        if not _truthy(evidence.get("screenshot_required")):
            continue
        status = str(evidence.get("screenshot_status") or "")
        path = str(evidence.get("screenshot_path") or "")
        # A replacement selected during automatic quality retry may not have
        # been captured in the first screenshot pass.  Missing status is not
        # evidence; strict automation must treat it as unavailable.
        if status != "captured" or not path:
            return True
    return False


def _evidence_screenshot_counts(cards: list[EvidenceCard]) -> dict[str, int]:
    required = captured = missing_required = 0
    for card in cards:
        for evidence in card.evidence_links:
            needs = _truthy(evidence.get("screenshot_required"))
            has_capture = str(evidence.get("screenshot_status") or "") == "captured" and bool(evidence.get("screenshot_path"))
            if needs:
                required += 1
                if not has_capture:
                    missing_required += 1
            if has_capture:
                captured += 1
    return {"required": required, "captured": captured, "missing_required": missing_required}


def _variant_labels(cards: list[EvidenceCard]) -> list[str]:
    labels: list[str] = []
    for card in cards:
        model_id = _model_id_from_card(card)
        if not model_id:
            continue
        _org, _family, variants = _model_family_parts(model_id)
        for label in variants:
            if label not in labels:
                labels.append(label)
    return labels


def _merged_model_title(cards: list[EvidenceCard]) -> str:
    lead = cards[0]
    model_id = _model_id_from_card(lead)
    if not model_id:
        return lead.event_title
    org, family, _variants = _model_family_parts(model_id)
    variants = _variant_labels(cards)
    model_name = f"{org}/{family}" if org else family
    if len(cards) > 1 and variants:
        return f"Hugging Face 模型仓库更新：{model_name} 系列（{' / '.join(variants)}）"
    if len(cards) > 1:
        return f"Hugging Face 模型仓库更新：{model_name} 系列"
    return lead.event_title


def _merged_editorial_title(cards: list[EvidenceCard]) -> str:
    """为确实覆盖两个子事件的人员短帖生成完整合并标题。"""
    lead = cards[0]
    family = _official_personnel_topic_family(lead)
    if family.endswith("|chatgpt-work|rollout-early-access"):
        blob = clean_text(
            " ".join(text for card in cards for text in [card.event_title, *card.key_facts])
        ).lower()
        has_scope = any(
            term in blob
            for term in ["网页与移动端", "移动端", "托管网站", "管理电子邮件", "汇总大量文档"]
        )
        has_recruitment = any(term in blob for term in ["招募", "征集", "早期体验", "体验组"])
        if has_scope and has_recruitment:
            return "OpenAI 产品人员介绍 ChatGPT Work 能力范围并招募早期体验组"
    return _merged_model_title(cards)


def _evidence_publisher_identity(row: dict) -> str:
    """Return the publisher/account behind an evidence URL, not the page URL.

    Merged story cards can contain an article plus a newsflash from the same
    outlet, or several posts from one X account.  Those are useful supporting
    documents, but they are not independent publishers and must not be exposed
    as cross-corroboration.
    """
    raw_url = str(row.get("final_url") or row.get("url") or "").strip()
    parsed = urlparse(raw_url)
    host = parsed.netloc.lower().removeprefix("www.")
    path_parts = [part for part in parsed.path.split("/") if part]
    if host in {"x.com", "twitter.com", "mobile.twitter.com"} and path_parts:
        return f"x.com/{path_parts[0].lower()}"
    if host == "github.com" and path_parts:
        return f"github.com/{path_parts[0].lower()}"
    if host in {"huggingface.co", "modelscope.cn"} and path_parts:
        return f"{host}/{path_parts[0].lower()}"
    if host and host not in {"news.google.com"}:
        return host
    source = clean_text(str(row.get("source") or "")).lower()
    return re.sub(r"\s+", "", source) or raw_url.lower()


def _merged_source_counts(evidence: list[dict]) -> tuple[int, int, int, int]:
    official: set[str] = set()
    media: set[str] = set()
    community: set[str] = set()
    for row in evidence:
        identity = _evidence_publisher_identity(row)
        if not identity:
            continue
        reliability = str(row.get("reliability") or "").lower()
        tier = str(row.get("tier") or "").upper()
        if reliability in {"official_personnel", "official_staff"}:
            community.add(identity)
        elif reliability in {"official", "official_social"} or tier == "A":
            official.add(identity)
        elif reliability in {"media", "primary_media"} or tier == "B":
            media.add(identity)
        else:
            community.add(identity)
    publishers = official | media | community
    return len(publishers), len(official), len(media), len(community)


def _merge_card_group(cards: list[EvidenceCard]) -> EvidenceCard:
    if len(cards) == 1:
        return cards[0]
    cards = sorted(
        cards,
        key=lambda c: (c.risk == "green", _primary_evidence_is_official(c), c.official_count, c.score, c.confidence),
        reverse=True,
    )
    lead = cards[0]
    source_cluster_keys = list(
        dict.fromkeys(
            key
            for card in cards
            for key in (card.source_cluster_keys or [card.cluster_key])
            if key
        )
    )
    evidence = _unique_evidence(cards)
    source_count, official_count, media_count, community_count = _merged_source_counts(evidence)
    variants = _variant_labels(cards)
    facts = _unique_text([fact for card in cards for fact in card.key_facts], limit=10)
    if len(variants) > 1:
        variant_fact = f"同一模型系列同时出现 {' / '.join(variants)} 变体。"
        facts = [variant_fact] + [fact for fact in facts if fact != variant_fact]
    latest_values = [c.latest_published_at for c in cards if c.latest_published_at]
    risk = "green" if any(c.risk == "green" for c in cards) else "yellow" if any(c.risk == "yellow" for c in cards) else lead.risk
    family_key = _model_family_key(lead) or _event_story_key(lead) or _daily_topic_family(lead) or lead.cluster_key
    return replace(
        lead,
        cluster_key=f"merged:{family_key}",
        event_title=_merged_editorial_title(cards),
        risk=risk,
        confidence=max(c.confidence for c in cards),
        selected=any(c.selected for c in cards),
        source_count=source_count or max(c.source_count for c in cards),
        official_count=official_count,
        media_count=media_count,
        community_count=community_count,
        first_seen_at=min(c.first_seen_at for c in cards),
        latest_published_at=max(latest_values) if latest_values else None,
        key_facts=facts,
        evidence_links=evidence,
        uncertainty=_unique_text([u for card in cards for u in card.uncertainty], limit=8),
        score=max(c.score for c in cards),
        source_cluster_keys=source_cluster_keys,
    )


def _merge_related_cards(cards: list[EvidenceCard]) -> list[EvidenceCard]:
    groups: dict[str, list[EvidenceCard]] = {}
    order: list[tuple[str, str]] = []
    reconciled_urls = {
        _canonical_official_evidence_url(card)
        for card in cards
        if any(_truthy(evidence.get("reconciled_official")) for evidence in card.evidence_links)
    }
    reconciled_urls.discard("")
    for card in cards:
        official_url = _canonical_official_evidence_url(card)
        structured_family = _daily_topic_family(card)
        key = (
            (f"official-url|{official_url}" if official_url in reconciled_urls else "")
            or _model_family_key(card)
            or _event_story_key(card)
            or _official_personnel_topic_family(card)
            or (f"story|{structured_family}" if structured_family else "")
        )
        if not key:
            order.append(("single", card.cluster_key))
            groups.setdefault(card.cluster_key, [card])
            continue
        if key not in groups:
            order.append(("group", key))
            groups[key] = []
        groups[key].append(card)
    result: list[EvidenceCard] = []
    emitted: set[str] = set()
    for kind, key in order:
        if key in emitted:
            continue
        emitted.add(key)
        result.append(_merge_card_group(groups[key]) if kind == "group" else groups[key][0])
    return result


def _normalized_category_limits(category_limits: dict[str, int] | None = None) -> dict[str, int]:
    limits = dict(SELECTION_CATEGORY_SOFT_LIMITS)
    for key, value in (category_limits or {}).items():
        try:
            limits[str(key)] = max(0, int(value))
        except (TypeError, ValueError):
            continue
    return limits


def _normalized_title_for_publishability(value: str) -> str:
    text = clean_text(value or "").lower()
    text = re.sub(r"^(?:.+?\s+official\s+)?(?:web|site|homepage)\s*[:：-]\s*", "", text)
    text = re.sub(r"^(?:官网|官方网站|官方)\s*", "", text)
    return text.strip(" \t\r\n-_:：/｜|。.!！?")


UNSPLIT_DIGEST_TITLE_PATTERNS = [
    r"(?:^|[|｜/·\s])ai\s*(?:周报|早报)(?:$|[|｜/·\s-])",
    r"(?:^|[|｜/·\s])(?:科技|大模型|人工智能)(?:周报|早报)(?:$|[|｜/·\s-])",
    r"\bweekly\s+(?:digest|roundup|brief(?:ing)?)\b",
    r"\bdaily\s+(?:digest|roundup|brief(?:ing)?)\b",
    r"\bmorning\s+(?:digest|roundup|brief(?:ing)?)\b",
    r"(?:^|[|｜/·\s])8\s*点\s*[1一]\s*氪(?:$|[|｜/·\s-])",
    r"(?:^|[|｜/·\s])氪星(?:早报|晚报)(?:$|[|｜/·\s-])",
    r"(?:^|[|｜/·\s])(?:科技|ai|人工智能)(?:日报|晚报)(?:$|[|｜/·\s-])",
]

MULTI_EVENT_ACTIONS = [
    "发布", "推出", "上线", "开放", "更新", "升级", "宣布", "回应", "称", "收购",
    "起诉", "被诉", "融资", "离职", "辞职", "任命", "接任", "重组", "并入", "登上",
]


def _unsplit_segment_subject(segment: str) -> str:
    """Return an explicit subject before an event verb, not a verb-led clause."""
    text = clean_text(segment).strip(" -—|｜:：")
    if not text:
        return ""
    actions = "|".join(re.escape(action) for action in MULTI_EVENT_ACTIONS)
    match = re.match(rf"^(.{{2,32}}?)(?:{actions})", text, flags=re.I)
    if not match:
        return ""
    subject = clean_text(match.group(1)).strip(" ，,：:；;")
    if not subject or subject.lower() in {"同时", "此外", "并", "还", "随后", "官方"}:
        return ""
    return re.sub(r"\W+", "", subject.lower())


def _looks_like_unsplit_digest_title(value: str) -> bool:
    """Detect roundup headlines without inspecting a single-story article body."""
    title = clean_text(value)
    if not title:
        return False
    lowered = title.lower()
    if any(re.search(pattern, lowered, flags=re.I) for pattern in UNSPLIT_DIGEST_TITLE_PATTERNS):
        return True

    # Two or more semicolons normally mean three headline-sized clauses. Only
    # reject when at least two clauses introduce their own subject; a title
    # such as "Nova 新增 API；支持批处理；覆盖三个区域" stays a single event.
    if len(re.findall(r"[;；]", title)) < 2:
        return False
    segments = [clean_text(part) for part in re.split(r"[;；]+", title) if len(clean_text(part)) >= 4]
    subjects = {_unsplit_segment_subject(segment) for segment in segments}
    subjects.discard("")
    return len(segments) >= 3 and len(subjects) >= 2


def _card_urls(card: EvidenceCard) -> list[str]:
    urls: list[str] = []
    for evidence in card.evidence_links:
        url = str(evidence.get("url") or "").strip()
        if url and url not in urls:
            urls.append(url)
    return urls


def _is_generic_listing_url(url: str) -> bool:
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    path = (parsed.path or "/").rstrip("/").lower()
    if path in GENERIC_DISCOVERY_PATHS:
        return True
    segments = [seg for seg in path.split("/") if seg]
    return len(segments) <= 1 and (segments[0] if segments else "") in {p.strip("/") for p in GENERIC_DISCOVERY_PATHS if p.strip("/")}


def _has_specific_model_or_version(text: str) -> bool:
    lowered = text.lower()
    if re.search(r"\bv?\d+(?:\.\d+){1,}(?:[-._a-z0-9]*)?\b", lowered):
        return True
    if re.search(r"(?<![a-z0-9])(?:gpt|claude|gemini|qwen|deepseek|hunyuan|hy|kimi|llama|mistral|glm|grok|minimax|doubao)[-_\s]?\d[\w.-]*", lowered):
        return True
    if re.search(r"\b\d+(?:\.\d+)?\s*(?:b|m|k|tokens?|上下文|bit|gb|倍|%|个提交|个文件)(?![a-z0-9])", lowered):
        return True
    return False


FACT_META_ONLY_PHRASES = [
    "首要来源发布时间",
    "首要来源",
    "性能榜单要看测试口径",
    "影响更偏向",
    "影响主要在",
    "真正影响还要看",
    "落地影响需要",
    "后续复现",
    "背景",
]


def _substantive_facts(card: EvidenceCard) -> list[str]:
    facts: list[str] = []
    for raw in card.key_facts:
        text = clean_text(raw)
        text = re.sub(r"https?://\S+", "", text).strip(" ：:；;，,。")
        text = re.sub(r"^(?:来源摘要|变更摘要|修复/维护线索)[：:]\s*", "", text)
        lowered = text.lower()
        if re.match(r"^.{1,30}\s+相关事件[：:]", text):
            continue
        if any(phrase.lower() in lowered for phrase in FACT_META_ONLY_PHRASES):
            continue
        if re.search(r"(?:url=|%2f|width=|height=|q=\d+)", lowered):
            continue
        if len(text) >= 18 and text not in facts:
            facts.append(text)
    return facts


def _has_special_source_detail(card: EvidenceCard, keywords: list[str], min_length: int) -> bool:
    for fact in _substantive_facts(card):
        lowered = fact.lower()
        if len(fact) < min_length or any(phrase in lowered for phrase in THIN_SPECIAL_SOURCE_PHRASES):
            continue
        if any(keyword.lower() in lowered for keyword in keywords):
            return True
    return False


def _is_high_density_media_case(card: EvidenceCard) -> bool:
    if card.official_count > 0 or card.media_count != 1 or card.risk != "yellow":
        return False
    text = clean_text(" ".join([card.event_title, *_substantive_facts(card)]))
    lowered = text.lower()
    named_subject = any(term in lowered for term in ["claude", "openai", "deepseek", "qwen", "gemini", "nvidia", "vllm"]) or bool(
        re.search(r"(?<![a-z0-9])bun(?![a-z0-9])", lowered)
    )
    engineering_action = any(term in lowered for term in ["重写", "迁移", "修复", "部署", "推理优化", "工作流", "性能", "内存", "启动时间"])
    measurements = re.findall(r"\d+(?:\.\d+)?\s*(?:万行|次提交|个工作流|个 claude|ms|gb|mb|%|万美元|亿个|token|tokens)", lowered, flags=re.I)
    noise = any(term in lowered for term in ["融资", "信用卡", "股价", "解禁", "招聘", "人才观", "课程", "aicon深圳"])
    return named_subject and engineering_action and len(measurements) >= 2 and not noise


def _has_safe_title_fact(card: EvidenceCard) -> bool:
    """Allow official headlines that are already complete, localizable facts."""
    title = clean_text(card.event_title).lower()
    if card.official_count <= 0:
        return False
    return any(
        pattern in title
        for pattern in [
            "gpt-5.6 sol, terra, and luna are now available in github copilot",
            "ask copilot for a repository overview",
        ]
    )


def _has_automatic_substantive_detail(card: EvidenceCard) -> bool:
    if _has_safe_title_fact(card):
        return True
    action_keywords = [
        keyword for keyword in PUBLISHABLE_DETAIL_KEYWORDS
        if keyword not in {"更新", "发布", "上线", "推出", "性能", "评测", "榜单", "参数", "版本"}
    ]
    for fact in _substantive_facts(card):
        lowered = fact.lower()
        if any(phrase in lowered for phrase in THIN_SPECIAL_SOURCE_PHRASES):
            continue
        has_measure = bool(
            re.search(
                r"(?:上下文|延迟|吞吐|速度|准确率|得分|显存|参数|价格|成本|支持|新增|修复|开放|权重)[^。；;]{0,28}\d+(?:\.\d+)?\s*(?:b|m|k|%|倍|ms|秒|gb|token|tokens)?\b|"
                r"\d+(?:\.\d+)?\s*(?:b|m|k|%|倍|ms|秒|gb|token|tokens)\b[^。；;]{0,28}(?:上下文|延迟|吞吐|速度|准确率|得分|显存|参数|价格|成本)",
                fact,
                flags=re.I,
            )
        )
        has_detail = any(keyword.lower() in lowered for keyword in action_keywords)
        # Chinese automation cannot safely narrate a long untranslated source
        # sentence. Keep product/model names, but require the actual change to
        # have a Chinese rendering before selection.
        renderable = _has_cjk(fact) or not _looks_raw_long_english(fact)
        if renderable and (has_measure or has_detail):
            return True
    return False


def _has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def _looks_raw_long_english(text: str, min_len: int = 34) -> bool:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) < min_len or _has_cjk(text):
        return False
    letters = sum(1 for char in text if char.isascii() and char.isalpha())
    visible = sum(1 for char in text if not char.isspace())
    return bool(visible) and letters / visible > 0.58


def _has_named_social_product(text: str) -> bool:
    lowered = clean_text(text).lower()
    return _has_specific_model_or_version(lowered) or any(hint in lowered for hint in SOCIAL_PRODUCT_HINTS)


def _signal_summary_is_renderable(summary: dict[str, str | bool] | None) -> bool:
    if not summary:
        return False
    visible = [
        clean_text(str(summary.get(key) or ""))
        for key in ["title", "lead", "fact", "detail", "impact", "discussion"]
    ]
    meaningful = [text for text in visible if text]
    primary = [clean_text(str(summary.get(key) or "")) for key in ["title", "fact"]]
    return (
        bool(meaningful)
        and all(not _looks_raw_long_english(text) for text in meaningful)
        and all(_has_cjk(text) for text in primary if text)
        and any(_has_cjk(text) for text in primary)
    )


def _has_official_personnel_signal_detail(card: EvidenceCard) -> bool:
    """Only auto-air a first-hand X post that can be rendered in Chinese safely."""
    if not card_has_x_post(card):
        return False
    summary = official_personnel_signal_summary(card, card.event_title)
    if not _signal_summary_is_renderable(summary):
        return False
    text = clean_text(" ".join([card.event_title, *card.key_facts]))
    return _has_named_social_product(text) or "产品方向" in str(summary.get("impact") or "")


def _has_community_signal_detail(card: EvidenceCard) -> bool:
    """Community rumours need a named product plus a concrete, renderable observation."""
    summary = community_observation_summary(card, card.event_title)
    if not _signal_summary_is_renderable(summary):
        return False
    text = clean_text(" ".join([card.event_title, *card.key_facts]))
    return _has_named_social_product(text) and _has_automatic_substantive_detail(card)


def _has_research_detail(card: EvidenceCard) -> bool:
    """Require a Chinese method plus a result/metric, not a translated paper title."""
    method_terms = ["提出", "方法", "架构", "算法", "数据集", "训练", "推理方案", "实验设置"]
    result_terms = ["结果", "提升", "降低", "超过", "优于", "准确率", "得分", "延迟", "吞吐", "成本", "显存", "%", "倍"]
    for fact in _substantive_facts(card):
        lowered = fact.lower()
        if any(phrase in lowered for phrase in THIN_SPECIAL_SOURCE_PHRASES):
            continue
        if not _has_cjk(fact):
            continue
        has_method = any(term in fact for term in method_terms)
        has_result = any(term in fact for term in result_terms) or bool(re.search(r"\d+(?:\.\d+)?\s*(?:%|倍|ms|秒|gb|token)", fact, flags=re.I))
        if has_method and has_result:
            return True
    return False


def _has_publishable_detail(card: EvidenceCard) -> bool:
    text = clean_text(
        " ".join(
            [
                card.event_title,
                card.entity,
                *card.key_facts,
                *[str(e.get("title") or "") for e in card.evidence_links],
                *[str(e.get("url") or "") for e in card.evidence_links],
            ]
        )
    )
    lowered = text.lower()
    if _is_github_release(card):
        return _has_special_source_detail(card, GITHUB_RELEASE_CHANGE_KEYWORDS, 20)
    if _is_model_page_update(card):
        return _has_special_source_detail(card, MODEL_ARTIFACT_KEYWORDS, 20)
    if _is_arxiv(card):
        return _has_research_detail(card)
    if official_performance_summary(card):
        return True
    if any(keyword.lower() in lowered for keyword in PUBLISHABLE_DETAIL_KEYWORDS):
        if not any(phrase in lowered for phrase in THIN_DISCOVERY_PHRASES):
            return True
        # Thin discovery wording is acceptable only when another concrete fact is present.
        detail_facts = [
            clean_text(fact)
            for fact in card.key_facts
            if clean_text(fact) and not any(phrase in clean_text(fact).lower() for phrase in THIN_DISCOVERY_PHRASES)
        ]
        return any(_has_specific_model_or_version(fact) or any(k.lower() in fact.lower() for k in PUBLISHABLE_DETAIL_KEYWORDS) for fact in detail_facts)
    profile = content_quality_profile(card)
    return int(profile.get("specificity") or 0) >= 35 and int(profile.get("story") or 0) >= 28


def _production_semantic_gate(card: EvidenceCard) -> bool | None:
    """Return semantic readiness for production cards, or None for legacy fixtures.

    verify.py attaches source excerpts to every real evidence record. Older unit
    fixtures without excerpts keep exercising the legacy gate until migrated.
    """
    has_excerpts = any(clean_text(str(row.get("excerpt") or "")) for row in card.evidence_links)
    if not has_excerpts:
        return None
    spec = build_story_spec(card)
    public_claims = [claim for claim in spec.claims if claim.renderable and claim.verifiable and claim.specificity >= 28]
    if spec.kind == "model_repository":
        return any(claim.specificity >= 40 and any(term in claim.text.lower() for term in ["license", "许可", "权重", "weights", "safetensors", "gguf", "mlx", "参数", "上下文", "context", "下载", "download", "api"]) for claim in public_claims)
    return bool(public_claims)


def is_publishable_card(card: EvidenceCard, *, strict_auto: bool = False) -> bool:
    """Headline gate: require enough verified detail for a full explainer."""
    if strict_auto and _looks_like_unsplit_digest_title(card.event_title):
        return False
    title_values = [card.event_title, *[str(e.get("title") or "") for e in card.evidence_links]]
    generic_title = any(_normalized_title_for_publishability(title) in GENERIC_DISCOVERY_TITLES for title in title_values if clean_text(title))
    listing_url = any(_is_generic_listing_url(url) for url in _card_urls(card))
    thin_text = clean_text(" ".join([card.event_title, *card.key_facts, *[str(e.get("title") or "") for e in card.evidence_links]])).lower()
    thin_discovery = any(phrase in thin_text for phrase in THIN_DISCOVERY_PHRASES)
    special_source = _is_github_release(card) or _is_model_page_update(card) or _is_arxiv(card)
    semantic_ready = _production_semantic_gate(card) if strict_auto else None
    if semantic_ready is False:
        return False
    if strict_auto and card_is_official_personnel_signal(card):
        return _has_official_personnel_signal_detail(card)
    if strict_auto and card_is_community_signal(card):
        return _has_community_signal_detail(card)
    if strict_auto and _is_high_density_media_case(card):
        return True
    if strict_auto and special_source and not _has_publishable_detail(card):
        return False
    if strict_auto and not special_source and not _has_automatic_substantive_detail(card):
        return False
    if (generic_title or thin_discovery or (listing_url and generic_title)) and not _has_publishable_detail(card):
        return False
    return True


BRIEF_NEWS_NOISE = [
    "aicon深圳", "会议议程", "演讲嘉宾", "峰会报名", "课程", "招聘", "人才观",
    "信用卡", "融资", "股价", "解禁", "估值", "合作方敲定",
    "权威认证", "类人意识", "脑内小剧场", "首曝光", "全面反攻", "硝烟弥漫",
]


def is_briefable_card(card: EvidenceCard, *, strict_auto: bool = False) -> bool:
    """Compact-news gate: one attributable claim is enough, but noise is not.

    Briefs deliberately use a lower *depth* requirement than headlines. They do
    not weaken provenance: a production brief still needs a source URL, a stored
    excerpt and a renderable/verifiable claim. Generic listings, undated items,
    red-risk cards and raw discovery fragments remain excluded.
    """
    if not card.selected or card.risk not in {"green", "yellow"}:
        return False
    if strict_auto and _looks_like_unsplit_digest_title(card.event_title):
        return False
    evidence = [
        row for row in card.evidence_links
        if clean_text(str(row.get("url") or row.get("final_url") or ""))
        and (not strict_auto or clean_text(str(row.get("excerpt") or "")))
    ]
    if not evidence:
        return False
    title = clean_text(card.event_title)
    if not title or _normalized_title_for_publishability(title) in GENERIC_DISCOVERY_TITLES:
        return False
    lowered = clean_text(" ".join([title, *card.key_facts])).lower()
    if any(phrase.lower() in lowered for phrase in BRIEF_NEWS_NOISE):
        return False
    if any(phrase in lowered for phrase in THIN_DISCOVERY_PHRASES) and not _has_publishable_detail(card):
        return False
    spec = build_story_spec(card)
    claims = [
        claim for claim in spec.claims
        if claim.renderable and claim.verifiable and claim.specificity >= 20 and claim.evidence_urls
    ]
    if strict_auto and card.official_count <= 0:
        title_values = [card.event_title, *[str(row.get("title") or "") for row in card.evidence_links]]

        def restates_title(claim_text: str) -> bool:
            claim_norm = re.sub(r"\W+", "", clean_text(claim_text).lower())
            for raw_title in title_values:
                title_norm = re.sub(r"\W+", "", clean_text(raw_title).lower())
                if not title_norm:
                    continue
                if claim_norm == title_norm:
                    return True
                if claim_norm.startswith(title_norm) and len(claim_norm) - len(title_norm) <= 12:
                    return True
                if title_norm.startswith(claim_norm) and len(title_norm) - len(claim_norm) <= 12:
                    return True
            return False

        claims = [claim for claim in claims if not restates_title(claim.text)]
    return bool(claims)


def select_card_portfolio(
    cards: list[EvidenceCard],
    max_items: int = 0,
    category_limits: dict[str, int] | None = None,
    min_score: int = 0,
    strict_auto: bool = False,
    story_history: dict[str, int] | None = None,
):
    threshold = max(0, min(100, int(min_score or 0)))
    eligible: list[EvidenceCard] = []
    for card in cards:
        if not card.selected or card.risk not in {"green", "yellow"} or _has_unusable_required_screenshot(card):
            continue
        headline = (
            (selection_quality_score(card) >= threshold or (strict_auto and _is_high_density_media_case(card)))
            and is_publishable_card(card, strict_auto=strict_auto)
        )
        brief = not headline and is_briefable_card(card, strict_auto=strict_auto)
        if not headline and not brief:
            continue
        card.editorial_tier = "headline" if headline else "brief"
        eligible.append(card)
    eligible.sort(key=lambda c: (mainstream_priority_rank(c), selection_quality_score(c), c.risk == "green", c.score, c.confidence), reverse=True)
    candidates = _merge_related_cards(eligible)
    candidates.sort(key=lambda c: (mainstream_priority_rank(c), selection_quality_score(c), c.risk == "green", c.score, c.confidence), reverse=True)

    limits = _normalized_category_limits(category_limits)
    profiles: list[CandidateProfile[EvidenceCard]] = []
    for card in candidates:
        spec = build_story_spec(card)
        public_claims = sum(1 for claim in spec.claims if claim.renderable and claim.verifiable)
        base_score = (
            selection_quality_score(card)
            + mainstream_priority_rank(card) * 20
            + (3 if card.risk == "green" else 0)
            + min(6, int(card.score or 0) / 20)
            + (12 if card.editorial_tier == "headline" else 0)
        )
        profiles.append(
            CandidateProfile(
                item=card,
                key=_story_key(card),
                source=_primary_source(card),
                entity=card.entity,
                category=story_category(card),
                topic=_daily_topic_family(card),
                kind=spec.kind,
                base_score=base_score,
                is_paper=_is_arxiv(card),
                is_maintenance=_is_github_release(card),
                first_party=spec.first_party,
                evidence_count=spec.evidence_count,
                public_claims=public_claims,
                audience=tuple(spec.audience),
                # Merged cards carry cluster_key="merged:<family>", which never appears in
                # the persisted history; take the max over the underlying source clusters
                # so repetition penalties still apply to merged stories.
                history_count=max(
                    0,
                    max(
                        (
                            int((story_history or {}).get(str(key), 0))
                            for key in [*(card.source_cluster_keys or []), card.cluster_key]
                        ),
                        default=0,
                    ),
                ),
            )
        )
    portfolio = optimize_portfolio(
        profiles,
        max_items=max_items,
        category_limits=limits,
        strict_auto=strict_auto,
    )
    if max_items > 0:
        portfolio.items = portfolio.items[:max_items]
    # Keep at most one full explainer, but never promote a compact brief merely
    # because it is the only non-social item in the edition.  A sparse day may
    # legitimately contain only briefs; stretching a shallow update into the
    # lead story would lower the evidence-density standard.
    headline = next(
        (
            card
            for card in portfolio.items
            if card.editorial_tier == "headline"
            and not card_is_official_personnel_signal(card)
            and not card_is_community_signal(card)
        ),
        None,
    )
    for card in portfolio.items:
        card.editorial_tier = "headline" if card is headline else "brief"
    tier_counts: dict[str, int] = {}
    for card in portfolio.items:
        tier_counts[card.editorial_tier] = tier_counts.get(card.editorial_tier, 0) + 1
    portfolio.diagnostics["tier_counts"] = tier_counts
    portfolio.diagnostics["headline_candidates"] = sum(1 for card in candidates if card.editorial_tier == "headline")
    portfolio.diagnostics["brief_candidates"] = sum(1 for card in candidates if card.editorial_tier == "brief")
    return portfolio


def select_cards(
    cards: list[EvidenceCard],
    max_items: int = 0,
    category_limits: dict[str, int] | None = None,
    min_score: int = 0,
    strict_auto: bool = False,
    story_history: dict[str, int] | None = None,
) -> list[EvidenceCard]:
    return select_card_portfolio(
        cards,
        max_items=max_items,
        category_limits=category_limits,
        min_score=min_score,
        strict_auto=strict_auto,
        story_history=story_history,
    ).items


def is_rejected_review_candidate(card: EvidenceCard) -> bool:
    if card.selected or card.risk != "red":
        return False
    text = clean_text(
        " ".join(
            [
                card.event_title,
                card.entity,
                card.reason,
                *card.key_facts,
                *card.uncertainty,
                *[str(e.get("title") or "") for e in card.evidence_links],
            ]
        )
    ).lower()
    if any(keyword.lower() in text for keyword in REJECTED_REVIEW_NOISE_KEYWORDS):
        return False
    if card.media_count > 0 and any(keyword.lower() in text for keyword in ["非主流 ai", "不属于主流 ai", "具身智能", "机器人", "融资"]):
        return True
    if card.community_count > 0 and any(keyword.lower() in text for keyword in REJECTED_REVIEW_SIGNAL_KEYWORDS):
        return True
    return False


def select_rejected_review_candidates(cards: list[EvidenceCard], max_items: int = 20) -> list[EvidenceCard]:
    candidates = [c for c in cards if is_rejected_review_candidate(c)]
    candidates.sort(key=lambda c: (c.score, c.confidence), reverse=True)
    return candidates[:max_items] if max_items > 0 else candidates


def is_midday_candidate(card: EvidenceCard) -> bool:
    # Backward-compatible alias; the product no longer has a midday lane.
    return is_rejected_review_candidate(card)


def select_midday_candidates(cards: list[EvidenceCard], max_items: int = 8) -> list[EvidenceCard]:
    # Backward-compatible alias for older scripts/tests.
    return select_rejected_review_candidates(cards, max_items=max_items)


def timeline_for_cards(cards: list[EvidenceCard]) -> list[tuple[int, EvidenceCard, int]]:
    current = INTRO_SECONDS
    rows: list[tuple[int, EvidenceCard, int]] = []
    for c in cards:
        if c.editorial_tier == "brief":
            # Actual render timing comes from TTS; this estimate keeps package
            # timelines and pre-render duration budgets honest.
            dur = 20
        elif c.risk == "green" and c.confidence >= 85:
            dur = NEWS_SECONDS_CONFIRMED
        elif c.risk == "green":
            dur = NEWS_SECONDS_DEFAULT
        else:
            dur = NEWS_SECONDS_SIGNAL
        if c.editorial_tier != "brief" and c.score > 70:
            dur += 2
        dur = min(24 if c.editorial_tier == "brief" else 45, max(16 if c.editorial_tier == "brief" else 28, dur))
        rows.append((current, c, dur))
        current += dur
    return rows


def timeline_from_script(script_path: Path, cards: list[EvidenceCard]) -> list[tuple[int, EvidenceCard, int]]:
    if not script_path.exists():
        return timeline_for_cards(cards)
    try:
        segments = json.loads(script_path.read_text(encoding="utf-8-sig"))
    except Exception:
        return timeline_for_cards(cards)
    if not isinstance(segments, list):
        return timeline_for_cards(cards)
    news_segments = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        if seg.get("kind") == "news":
            news_segments.append(seg)
            continue
        title = str(seg.get("title") or "")
        if title and title[0].isdigit():
            news_segments.append(seg)
    if len(news_segments) < len(cards):
        return timeline_for_cards(cards)
    rows: list[tuple[int, EvidenceCard, int]] = []
    for card, seg in zip(cards, news_segments):
        start = int(round(float(seg.get("start") or 0)))
        dur = int(round(float(seg.get("duration") or 0)))
        rendered_title = clean_text(str(seg.get("title") or ""))
        rendered_card = replace(card, event_title=rendered_title) if rendered_title else card
        rows.append((start, rendered_card, max(1, dur)))
    return rows


def write_sources_md(path: Path, health: list[SourceHealth]) -> None:
    now = datetime.now(UTC)
    lines = ["# 新闻源健康报告", "", f"生成时间：{datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}", ""]
    lines.append("| Tier | 来源 | 状态 | HTTP | 条目 | 最新时间 | 延迟 | 备注 |")
    lines.append("|---|---|---:|---:|---:|---|---:|---|")
    for h in sorted(health, key=lambda x: (x.tier, x.source_name)):
        note = h.error.replace("|", "/")[:80]
        if h.stale:
            note = (note + " stale").strip()
        if h.no_date_count:
            note = (note + f" no_date={h.no_date_count}").strip()
        if h.latest_item_at and (h.latest_item_at.astimezone(UTC) - now).total_seconds() > 2 * 3600:
            note = (note + " future_time").strip()
        lines.append(
            f"| {h.tier} | {h.source_name} | {h.status} | {h.status_code or ''} | {h.item_count} | {fmt_dt(h.latest_item_at)} | {h.latency_ms or ''}ms | {note} |"
        )
    _write_md(path, "\n".join(lines) + "\n")


def write_fact_check_md(
    path: Path,
    cards: list[EvidenceCard],
    selected: list[EvidenceCard] | None = None,
    lookback_hours: int | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> None:
    eligible_count = sum(1 for c in cards if c.selected)
    portfolio = list(selected or [])
    selected_keys = {c.cluster_key for c in portfolio}
    status_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    for c in cards:
        status = public_source_status(c)
        status_counts[status] = status_counts.get(status, 0) + 1
        category = story_category_label(c)
        category_counts[category] = category_counts.get(category, 0) + 1
    lines = ["# 事实核查报告", "", f"生成时间：{datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}", ""]
    if lookback_hours:
        lines.append(f"筛选时间范围：当前运行时间往前 {lookback_hours} 小时。")
        if window_start and window_end:
            lines.append(f"窗口：{fmt_dt(window_start)} 至 {fmt_dt(window_end)}。")
        lines.append("")
    status_text = "；".join(f"{name} {count}" for name, count in sorted(status_counts.items()))
    category_text = "；".join(f"{name} {count}" for name, count in sorted(category_counts.items()))
    lines.append(f"今日候选：{len(cards)} 条；验证可入选 {eligible_count} 条；最终进入主视频 {len(portfolio)} 条。")
    if status_text:
        lines.append(f"证据状态：{status_text}。")
    if category_text:
        lines.append(f"类型分布：{category_text}。")
    lines.append("")
    for c in cards:
        enrich = horizon_enrichment(c)
        lines.append(f"## {c.event_title}")
        lines.append("")
        lines.append(f"- 实体：{c.entity}")
        portfolio_status = "最终进入主视频" if c.cluster_key in selected_keys else "未进入最终组合"
        eligibility_status = "验证可入选" if c.selected else "验证未通过"
        lines.append(f"- 类型：{enrich['category_label']} / 证据状态：{enrich['status']} / {eligibility_status} / {portfolio_status}")
        lines.append(f"- 判断：{c.reason}")
        lines.append(f"- 最新发布时间：{fmt_dt(c.latest_published_at)}")
        lines.append(f"- 来源数量：{c.source_count}（官方 {c.official_count} / 媒体 {c.media_count} / 社区聚合 {c.community_count}）")
        lines.append("- 内容富化：")
        lines.append(f"  - 发生了什么：{enrich['whats_new']}")
        lines.append(f"  - 为什么重要：{enrich['why_it_matters']}")
        lines.append(f"  - 关键细节：{enrich['key_details']}")
        if enrich.get("background"):
            lines.append(f"  - 背景：{enrich['background']}")
        if enrich.get("community_discussion"):
            lines.append(f"  - 社区讨论：{enrich['community_discussion']}")
        if enrich.get("caution"):
            lines.append(f"  - 待确认：{enrich['caution']}")
        lines.append("- 可播事实：")
        for fact in c.key_facts:
            lines.append(f"  - {fact}")
        if c.uncertainty:
            lines.append("- 不确定点：")
            for u in c.uncertainty:
                lines.append(f"  - {u}")
        lines.append("- 证据链接：")
        for e in c.evidence_links:
            title = str(e.get("title", "source")).replace("|", "/")
            lines.append(f"  - [{e.get('source')} / {e.get('tier')}] [{title}]({e.get('url')})")
            if _truthy(e.get("screenshot_required")):
                status = str(e.get("screenshot_status") or "待捕获")
                screenshot = str(e.get("screenshot_path") or "")
                if screenshot:
                    lines.append(f"    - 源头截图：{screenshot}")
                else:
                    lines.append(f"    - 源头截图：{status}（反馈/性能/评测类素材发布前需补证）")
            elif e.get("screenshot_path"):
                lines.append(f"    - 源头截图：{e.get('screenshot_path')}")
        lines.append("")
    _write_md(path, "\n".join(lines))


def write_rejected_candidates_md(path: Path, cards: list[EvidenceCard]) -> None:
    lines = ["# 剔除/待复核候选", "", f"生成时间：{datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}", ""]
    lines.append("这些条目当前不进入本期：要么证据不足，要么跑题，要么需要补官方/原文证据；不再做中午分流。")
    lines.append("")
    if not cards:
        lines.append("暂无剔除/待复核候选。")
    for idx, c in enumerate(cards, 1):
        lines.append(f"## {idx}. {c.event_title}")
        lines.append("")
        lines.append(f"- 实体：{c.entity}")
        lines.append(f"- 类型：{story_category_label(c)}")
        lines.append(f"- 证据状态：{public_source_status(c)}")
        lines.append(f"- 判断：{c.reason}")
        lines.append(f"- 最新发布时间：{fmt_dt(c.latest_published_at)}")
        if c.key_facts:
            lines.append("- 可用素材：")
            for fact in c.key_facts[:4]:
                lines.append(f"  - {fact}")
        if c.uncertainty:
            lines.append("- 注意：")
            for item in c.uncertainty[:3]:
                lines.append(f"  - {item}")
        if c.evidence_links:
            lines.append("- 来源：")
            for e in c.evidence_links[:3]:
                title = str(e.get("title", "source")).replace("|", "/")
                lines.append(f"  - [{e.get('source')} / {e.get('tier')}] [{title}]({e.get('url')})")
        lines.append("")
    _write_md(path, "\n".join(lines).rstrip() + "\n")

def _summary_line(cards: list[EvidenceCard]) -> str:
    if not cards:
        return "今天的AI 日报：模型、工具、开源与产业动态，一次看完。"
    entities: list[str] = []
    for c in cards:
        name = _brand_name(_clean_title_entity(_public_card_entity(c)))
        if name and name != "AI" and name not in entities:
            entities.append(name)
    if entities:
        return f"今天的AI 日报：{'、'.join(entities[:4])} 等动态，一次看完。"
    return "今天的AI 日报：模型、工具、开源与产业动态，一次看完。"


def _content_quality_summary(cards: list[EvidenceCard]) -> dict[str, int | float]:
    profiles = [content_quality_profile(card) for card in cards]
    if not profiles:
        return {"average_score": 0, "min_score": 0, "specific_items": 0, "performance_items": 0}
    scores = [int(profile["score"]) for profile in profiles]
    return {
        "average_score": round(sum(scores) / len(scores), 1),
        "min_score": min(scores),
        "specific_items": sum(1 for profile in profiles if "specific" in profile.get("notes", [])),
        "performance_items": sum(1 for profile in profiles if "performance" in profile.get("notes", [])),
    }


def _timeline_title(card: EvidenceCard) -> str:
    # Publication metadata must use the same neutral, evidence-backed title
    # as the narration and rendered scene. The raw cluster headline can be a
    # media discovery title and may still contain clickbait wording.
    title = _publication_headline(card)
    # Keep public-facing signal labels typographically consistent even when a
    # source headline used an ASCII separator.
    title = re.sub(r"^一线消息\s*[|:：]\s*", "一线消息｜", title)
    title = re.sub(r"^传闻/风向\s*[|:：]\s*", "传闻/风向｜", title)
    if card_is_official_personnel_signal(card) and not title.startswith("一线消息"):
        title = f"一线消息｜{title}"
    elif card_is_community_signal(card) and not title.startswith("传闻/风向"):
        title = f"传闻/风向｜{title}"
    return title


def _timeline_label(card: EvidenceCard) -> str:
    title = _timeline_title(card)
    entity = _public_card_entity(card)
    entity_key = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", entity.lower())
    title_key = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", title.lower())
    if entity_key:
        nonduplicating_prefixes = (
            entity_key,
            f"一线消息{entity_key}",
            f"传闻风向{entity_key}",
        )
        if title_key.startswith(nonduplicating_prefixes):
            return title
    return f"{entity}｜{title}"


def _fit_plain_text(text: str, limit: int) -> str:
    text = "\n".join(line.rstrip() for line in text.strip().splitlines())
    if len(text) < limit:
        return text
    return text[: limit - 1].rstrip()


def _upload_desc(cards: list[EvidenceCard]) -> str:
    from .edition_brief import get_edition_brief
    question = get_edition_brief(cards).get("comment_question", "")
    return _fit_plain_text(_summary_line(cards) + f"\n\n{question}\n完整时间轴见置顶评论。", BILI_DESC_LIMIT)


def write_bilibili_md(path: Path, cards: list[EvidenceCard], quality: str, run_date: str, timeline: list[tuple[int, EvidenceCard, int]] | None = None) -> tuple[list[tuple[int, EvidenceCard, int]], list[str]]:
    timeline = timeline or timeline_for_cards(cards)
    titles = _title_candidates(cards, run_date)
    tags = ["AI 日报", "AI", "人工智能", "大模型", "科技早报", "ChatGPT", "开源模型", "AIGC"]
    lines = ["# B站投稿草稿", ""]
    lines.append("## 标题候选")
    for t in titles:
        lines.append(f"- {t}")
    lines.append("")
    lines.append("## 简介")
    lines.append(_summary_line(cards))
    lines.append("")
    lines.append("时间轴：")
    lines.append("00:00 开场｜AI 日报")
    for start, card, _dur in timeline:
        lines.append(f"{format_mmss(start)} {_timeline_label(card)}")
    lines.append("")
    lines.append("本次的播报完毕。")
    lines.append("")
    lines.append("## 标签")
    lines.append(",".join(tags))
    lines.append("")
    lines.append("## 视频参数")
    lines.append(f"- quality: {quality}")
    lines.append("- copyright: 原创")
    lines.append("- no_reprint: 1")
    _write_md(path, "\n".join(lines) + "\n")
    return timeline, titles


def write_bilibili_json(path: Path, out_dir: Path, timeline: list[tuple[int, EvidenceCard, int]], titles: list[str]) -> None:
    from .edition_brief import get_edition_brief

    cards = [card for _start, card, _dur in timeline]
    desc = _upload_desc(cards)
    payload = {
        "title": titles[0] if titles else "AI 日报【AI 日报】",
        "title_candidates": titles,
        "desc": desc,
        "cover_hook": get_edition_brief(cards).get("hook", ""),
        "tag": "AI 日报,AI,人工智能,大模型,科技早报,ChatGPT,开源模型,AIGC",
        "copyright": 1,
        "no_reprint": 1,
        "video": str(out_dir / "final.mp4"),
        "cover": str(out_dir / "cover.png"),
        "subtitle": str(out_dir / "subtitles.srt"),
        "pinned_comment": str(out_dir / "pinned-comment.md"),
        "tid": None,
        "tid_note": "B站分区 ID 后续通过开放平台分区接口刷新后填入。",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8-sig")


def write_pinned_comment(path: Path, timeline: list[tuple[int, EvidenceCard, int]]) -> None:
    lines = ["今日时间轴："]
    lines.append("00:00 开场｜AI 日报")
    for start, card, _dur in timeline:
        lines.append(f"{format_mmss(start)} {_timeline_label(card)}")
    lines.append("")
    from .edition_brief import get_edition_brief
    lines.append(str(get_edition_brief([row[1] for row in timeline]).get("comment_question") or "本次的播报完毕。"))
    _write_md(path, "\n".join(lines) + "\n")


def _semantic_layer_summary(cards: list[EvidenceCard]) -> dict[str, object]:
    specs = [build_story_spec(card) for card in cards]
    plans = [build_editorial_plan(card) for card in cards]
    boards = [build_storyboard(card, plan=plan, spec=spec) for card, plan, spec in zip(cards, plans, specs)]
    claims = [claim for spec in specs for claim in spec.claims]
    template_counts: dict[str, int] = {}
    kind_counts: dict[str, int] = {}
    for spec, board in zip(specs, boards):
        kind_counts[spec.kind] = kind_counts.get(spec.kind, 0) + 1
        template_counts[board.template] = template_counts.get(board.template, 0) + 1
    ready_plans = sum(1 for plan, spec in zip(plans, specs) if plan.facts and any(claim.renderable and claim.verifiable for claim in spec.claims))
    return {
        "stories": len(specs),
        "claims": len(claims),
        "renderable_claims": sum(1 for claim in claims if claim.renderable),
        "verifiable_claims": sum(1 for claim in claims if claim.verifiable),
        "public_claims": sum(1 for claim in claims if claim.renderable and claim.verifiable),
        "unsupported_claims": sum(1 for claim in claims if claim.renderable and not claim.verifiable),
        "structured_plan_ready": ready_plans,
        "structured_plan_coverage": round(ready_plans / len(specs), 3) if specs else 0.0,
        "story_kinds": kind_counts,
        "storyboard_templates": template_counts,
    }


def write_manifest(
    path: Path,
    run_date: str,
    out_dir: Path,
    cards: list[EvidenceCard],
    selected: list[EvidenceCard],
    health: list[SourceHealth],
    titles: list[str],
    quality: str,
    lookback_hours: int | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    category_limits: dict[str, int] | None = None,
    min_score: int = 0,
    selection_diagnostics: dict[str, object] | None = None,
) -> None:
    rejected_review = select_rejected_review_candidates(cards)
    screenshot_counts = _evidence_screenshot_counts(selected)
    category_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    for card in selected:
        category = story_category(card)
        category_counts[category] = category_counts.get(category, 0) + 1
        status = public_source_status(card)
        status_counts[status] = status_counts.get(status, 0) + 1
    payload = {
        "run_date": run_date,
        "generated_at": datetime.now(UTC).isoformat(),
        "quality": quality,
        "freshness_window": {
            "lookback_hours": lookback_hours,
            "start_utc": window_start.astimezone(UTC).isoformat() if window_start else "",
            "end_utc": window_end.astimezone(UTC).isoformat() if window_end else "",
        },
        "counts": {
            "sources": len(health),
            "healthy_sources": sum(1 for h in health if h.status in {"ok", "ok_web_pending_adapter"}),
            "candidate_cards": len(cards),
            "selected_cards": len(selected),
            "rejected_review_candidates": len(rejected_review),
            "evidence_screenshots_required": screenshot_counts["required"],
            "evidence_screenshots_captured": screenshot_counts["captured"],
            "evidence_screenshots_missing_required": screenshot_counts["missing_required"],
        },
        "internal_risk_breakdown": {
            "green": sum(1 for c in cards if c.risk == "green"),
            "yellow": sum(1 for c in cards if c.risk == "yellow"),
            "red": sum(1 for c in cards if c.risk == "red"),
        },
        "content_quality": _content_quality_summary(selected),
        "semantic_layer": _semantic_layer_summary(selected),
        "editorial_mix": {
            "headline": sum(1 for c in selected if c.editorial_tier == "headline"),
            "brief": sum(1 for c in selected if c.editorial_tier == "brief"),
        },
        "selection_balance": {
            "optimizer": selection_diagnostics or {"mode": "not_recorded"},
            "category_limits": _normalized_category_limits(category_limits),
            "min_score": max(0, min(100, int(min_score or 0))),
            "selected_categories": category_counts,
            "selected_source_status": status_counts,
        },
        "title_candidates": titles,
        "outputs": {
            "final_video": str(out_dir / "final.mp4"),
            "cover": str(out_dir / "cover.png"),
            "subtitles": str(out_dir / "subtitles.srt"),
            "script": str(out_dir / "script.json"),
            "bilibili": str(out_dir / "bilibili.md"),
            "bilibili_json": str(out_dir / "bilibili.json"),
            "pinned_comment": str(out_dir / "pinned-comment.md"),
            "sources": str(out_dir / "sources.md"),
            "fact_check": str(out_dir / "fact-check.md"),
            "rejected_review_candidates": str(out_dir / "rejected-candidates.md"),
            "render_info": str(out_dir / "render-info.json"),
        },
        "selected": [
            {
                "title": _publication_headline(c),
                # Public manifest metadata is consumed by the final verifier
                # and publishing tools, so use the same evidence-backed label
                # as narration/timeline rather than a broad cluster fallback.
                "entity": _public_card_entity(c),
                "editorial_tier": c.editorial_tier,
                "risk": c.risk,
                "confidence": c.confidence,
                "source_status": public_source_status(c),
                "category": story_category(c),
                "category_label": story_category_label(c),
                "selection_quality": {"score": selection_quality_score(c)},
                "enrichment": horizon_enrichment(c),
                "content_quality": content_quality_profile(c),
                "story_spec": build_story_spec(c).as_dict(),
                "editorial_plan": build_editorial_plan(c).as_dict(),
                "storyboard": build_storyboard(c, plan=build_editorial_plan(c), spec=build_story_spec(c)).as_dict(),
                "evidence": c.evidence_links[:3],
            }
            for c in selected
        ],
        "selection_audit": [
            {
                "cluster_key": c.cluster_key,
                "title": c.event_title,
                "verify_eligible": bool(c.selected),
                "decision": c.editorial_tier if c in selected else "rejected",
                "reason": (
                    "selected_for_full_explainer" if c in selected and c.editorial_tier == "headline"
                    else "selected_for_compact_brief" if c in selected
                    else c.reason if not c.selected
                    else "not_selected_by_editorial_portfolio"
                ),
                "selection_quality": selection_quality_score(c),
            }
            for c in cards
        ],
        "rejected_review_candidates": [
            {
                "title": c.event_title,
                "entity": c.entity,
                "risk": c.risk,
                "confidence": c.confidence,
                "source_status": public_source_status(c),
                "category": story_category(c),
                "selection_quality": {"score": selection_quality_score(c)},
                "reason": c.reason,
                "evidence": c.evidence_links[:2],
            }
            for c in rejected_review
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_package(
    out_dir: Path,
    run_date: str,
    cards: list[EvidenceCard],
    health: list[SourceHealth],
    quality: str = "1080p",
    max_items: int = 0,
    lookback_hours: int | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    category_limits: dict[str, int] | None = None,
    min_score: int = 0,
    strict_auto: bool = False,
    selected_override: list[EvidenceCard] | None = None,
    story_history: dict[str, int] | None = None,
    selection_diagnostics: dict[str, object] | None = None,
) -> dict[str, int]:
    ensure_dir(out_dir)
    if selected_override is not None:
        selected = list(selected_override)
        diagnostics = selection_diagnostics or {"mode": "selected_override", "selected_count": len(selected)}
    else:
        portfolio = select_card_portfolio(
            cards,
            max_items=max_items,
            category_limits=category_limits,
            min_score=min_score,
            strict_auto=strict_auto,
            story_history=story_history,
        )
        selected = portfolio.items
        diagnostics = portfolio.diagnostics
    # The local draft is always deterministic.  A Codex automation may edit a
    # later review/state.json artifact, but can never mutate this evidence plan.
    from .edition_brief import deterministic_edition_brief, install_edition_brief

    install_edition_brief(deterministic_edition_brief(selected))
    rejected_review = select_rejected_review_candidates(cards)
    write_sources_md(out_dir / "sources.md", health)
    write_fact_check_md(
        out_dir / "fact-check.md",
        cards,
        selected=selected,
        lookback_hours=lookback_hours,
        window_start=window_start,
        window_end=window_end,
    )
    write_rejected_candidates_md(out_dir / "rejected-candidates.md", rejected_review)
    timeline, titles = write_bilibili_md(out_dir / "bilibili.md", selected, quality, run_date)
    write_bilibili_json(out_dir / "bilibili.json", out_dir, timeline, titles)
    write_pinned_comment(out_dir / "pinned-comment.md", timeline)
    write_manifest(
        out_dir / "manifest.json",
        run_date,
        out_dir,
        cards,
        selected,
        health,
        titles,
        quality,
        lookback_hours=lookback_hours,
        window_start=window_start,
        window_end=window_end,
        category_limits=category_limits,
        min_score=min_score,
        selection_diagnostics=diagnostics,
    )
    manifest_path = out_dir / "manifest.json"
    if manifest_path.exists():
        from .edition_brief import get_edition_brief

        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        manifest["edition_brief"] = get_edition_brief(selected)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"selected_count": len(selected), "duration_seconds": INTRO_SECONDS + sum(d for _s, _c, d in timeline) + OUTRO_SECONDS}


def refresh_bilibili_timeline_from_script(out_dir: Path, selected: list[EvidenceCard], quality: str, run_date: str) -> None:
    timeline = timeline_from_script(out_dir / "script.json", selected)
    _timeline, titles = write_bilibili_md(out_dir / "bilibili.md", selected, quality, run_date, timeline=timeline)
    write_bilibili_json(out_dir / "bilibili.json", out_dir, timeline, titles)
    write_pinned_comment(out_dir / "pinned-comment.md", timeline)

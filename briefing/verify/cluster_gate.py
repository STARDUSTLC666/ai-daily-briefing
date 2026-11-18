from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
import json
import re
from typing import Any

from briefing.core import (
    MODEL_TOKEN_RE,
    UTC,
    Cluster,
    EvidenceCard,
    FeedItem,
    clean_text,
    feed_item_is_official_personnel,
    has_cjk,
    is_ai_relevant,
    is_mainstream_ai,
    now_utc,
    parse_date,
    url_host,
)

AI_POLICY_PUBLIC_SERVICE_TERMS: tuple[str, ...] = (
    "行动方案",
    "指导意见",
    "管理办法",
    "实施方案",
    "监管规则",
    "国家标准",
)

AI_POLICY_PUBLIC_OWNER_PATTERNS: tuple[str, ...] = (
    r"(?:国务院|最高人民法院|最高人民检察院)",
    r"(?:中国|国家|中央)[\u4e00-\u9fff]{0,16}(?:委员会|局|办公室|法院|检察院|监管机构)",
    r"(?:科技|教育|工业和信息化|工信|公安|民政|司法|财政|自然资源|生态环境|交通运输|水利|农业农村|商务|应急管理|审计)部",
    r"(?:中央宣传部|中央组织部|中央统战部|中央社会工作部)",
    r"[\u4e00-\u9fff]{2,12}(?:省|市|自治区|自治州|县|区)(?:人民)?政府",
)

AI_POLICY_PERIOD_RE = re.compile(
    r"20\d{2}\s*[—–-]\s*20\d{2}|未来\s*[一二三四五六七八九十百\d]+\s*年|(?:到|至|截至)\s*20\d{2}\s*年"
)
AI_POLICY_TASK_RE = re.compile(
    r"[一二三四五六七八九十百\d]+\s*(?:项|方面)\s*(?:重点|主要|具体)?任务|(?:重点|主要|具体)?任务\s*(?:包括|涵盖|围绕|聚焦)"
)
AI_POLICY_RELEASE_TERMS: tuple[str, ...] = ("发布", "印发", "出台", "施行")

VALUE_KEYWORDS: tuple[str, ...] = (
    "发布",
    "推出",
    "上线",
    "开源",
    "open source",
    "release",
    "released",
    "launch",
    "model",
    "模型",
    "agent",
    "benchmark",
    "榜单",
    "价格",
    "降价",
    "api",
    "本地",
    "local",
    "推理",
    "训练",
    "多模态",
    "语音",
    "tts",
    "融资",
    "收购",
    "安全",
    "漏洞",
    "政策",
    "下架",
    "更新",
    "copilot",
    "codex",
    "qwen",
    "deepseek",
    "kimi",
    "glm",
)

WEAK_MEDIA_CONTEXT_KEYWORDS: tuple[str, ...] = (
    "股票",
    "股价",
    "交易异常",
    "营收",
    "销售额",
    "财报",
    "研报",
    "材料",
    "化学品",
    "论文造假",
    "指控论文",
)

NON_MAINSTREAM_INDUSTRY_KEYWORDS: tuple[str, ...] = (
    "融资",
    "天使轮",
    "a轮",
    "b轮",
    "创业",
    "初创",
    "首发",
)

LOCAL_OPEN_RUNTIME_KEYWORDS: tuple[str, ...] = (
    "ollama",
    "llama.cpp",
    "vllm",
    "sglang",
    "开源模型",
    "本地部署",
)

COMMUNITY_HUB_GENERIC_KEYWORDS: frozenset[str] = frozenset(
    {"hugging face", "huggingface", "modelscope", "vllm", "ollama", "llama.cpp", "sglang", "llama"}
)

COMMUNITY_YELLOW_SIGNAL_KEYWORDS: tuple[str, ...] = (
    "benchmark",
    "benchmarked",
    "performance",
    "perf",
    "eval",
    "evaluation",
    "leaderboard",
    "arena",
    "swe-bench",
    "livecodebench",
    "aider",
    "score",
    "latency",
    "throughput",
    "works pretty well",
    "degraded performance",
    "weights are now open",
    "open weights",
    "open-weight",
    "released",
    "release]",
    "model viable",
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
)

COMMUNITY_NOISE_KEYWORDS: tuple[str, ...] = (
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
    "setup",
    "alternatives to",
)

PAPER_SOURCE_IDS: frozenset[str] = frozenset({"arxiv_cs_ai", "arxiv_cs_cl", "arxiv_cs_lg"})
SPECIALIST_AI_MEDIA_SOURCE_IDS: frozenset[str] = frozenset({"qbitai", "infoq_cn"})
DISCOVERY_SOURCE_IDS: frozenset[str] = frozenset({"google_news_ai_cn", "google_news_ai_global"})
MODEL_REPO_SOURCE_IDS: frozenset[str] = frozenset(
    {
        "qwen_hf_models",
        "deepseek_hf_models",
        "moonshot_hf_models",
        "zai_hf_models",
        "minimax_hf_models",
        "bytedance_seed_hf_models",
        "paddlepaddle_hf_models",
        "google_hf_models",
        "mistral_hf_models",
    }
)

OFFICIAL_RECONCILIATION_STATUSES: frozenset[str] = frozenset(
    {
        "matched",
        "matched_undated",
        "matched_social",
        "matched_origin_media",
        "matched_origin_media_undated",
    }
)


def is_concrete_public_ai_policy(title: str, text: str | Iterable[str] = "") -> bool:
    """Identify public-sector AI policy documents with explicit issuer, period, and tasks."""
    if isinstance(text, str):
        combined = clean_text(f"{title} {text}").lower()
    else:
        combined = clean_text(" ".join([title, *(str(part) for part in text)])).lower()

    has_ai = any(term in combined for term in ("人工智能", "ai+", "ai +", "大模型", "智能监测", "智能预警"))
    has_policy = any(term in combined for term in AI_POLICY_PUBLIC_SERVICE_TERMS)
    has_period = bool(AI_POLICY_PERIOD_RE.search(combined))
    has_tasks = bool(AI_POLICY_TASK_RE.search(combined))
    release_pattern = "|".join(re.escape(term) for term in AI_POLICY_RELEASE_TERMS)
    has_public_release = any(
        re.search(rf"(?:{owner})[^。；;]{{0,24}}(?:{release_pattern})", combined)
        for owner in AI_POLICY_PUBLIC_OWNER_PATTERNS
    )
    return has_ai and has_policy and has_period and has_tasks and has_public_release


def _official_reference(item: FeedItem) -> dict[str, Any] | None:
    record = item.raw.get("official_reconciliation") if isinstance(item.raw, dict) else None
    if not isinstance(record, dict) or str(record.get("status") or "") not in OFFICIAL_RECONCILIATION_STATUSES:
        return None
    if not str(record.get("url") or record.get("final_url") or "").startswith(("http://", "https://")):
        return None
    return record


def _official_references(items: list[FeedItem]) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        record = _official_reference(item)
        if not record:
            continue
        url = str(record.get("final_url") or record.get("url") or "").rstrip("/")
        if not url or url in seen:
            continue
        seen.add(url)
        references.append(record)
    return references


def _reference_published_at(record: dict[str, Any] | None) -> datetime | None:
    if not record:
        return None
    return parse_date(str(record.get("published_at") or "").strip())


def _canonical_official_reference(cluster: Cluster) -> dict[str, Any] | None:
    references = _official_references(cluster.items)
    if not references:
        return None
    references.sort(
        key=lambda row: (
            str(row.get("freshness") or "") == "fresh",
            str(row.get("status") or "") == "matched",
            _reference_published_at(row) or datetime.min.replace(tzinfo=UTC),
            int(row.get("candidate_score") or 0),
        ),
        reverse=True,
    )
    return references[0]


def _official_reference_state(cluster: Cluster) -> str:
    direct_official = any(
        not feed_item_is_official_personnel(item)
        and (item.source_tier.upper() == "A" or item.source_reliability in {"official", "official_social"})
        for item in cluster.items
    )
    if direct_official:
        return "direct"
    references = _official_references(cluster.items)
    if not references:
        return "none"
    freshness = {str(record.get("freshness") or "undated") for record in references}
    if "fresh" in freshness:
        return "fresh"
    if freshness == {"stale"}:
        return "stale"
    if any(str(record.get("status") or "") == "matched_social" for record in references):
        return "social"
    return "undated"


def _source_counts(items: list[FeedItem]) -> tuple[int, int, int, int, set[str]]:
    domains: set[str] = set()
    official_sources: set[str] = set()
    media_sources: set[str] = set()
    community_sources: set[str] = set()
    source_ids: set[str] = set()

    for item in items:
        source_ids.add(item.source_id)
        enriched_url = ""
        if isinstance(item.raw, dict):
            article = item.raw.get("article_enrichment")
            if isinstance(article, dict):
                enriched_url = str(article.get("final_url") or "")
            enriched_url = enriched_url or str(item.raw.get("final_url") or "")
        domain = url_host(enriched_url or item.link)
        if domain:
            domains.add(domain)
        identity = domain or str(item.source_id or item.source_name or item.link).strip().lower()
        if feed_item_is_official_personnel(item):
            community_sources.add(identity)
        elif item.source_tier.upper() == "A" or item.source_reliability in {"official", "official_social"}:
            official_sources.add(identity)
        elif item.source_tier.upper() == "B" or item.source_reliability == "media":
            media_sources.add(identity)
        else:
            community_sources.add(identity)

    existing_urls = {str(item.link or "").rstrip("/") for item in items}
    for record in _official_references(items):
        url = str(record.get("final_url") or record.get("url") or "").rstrip("/")
        if not url or url in existing_urls:
            continue
        source_ids.add("official_reference:" + url)
        domain = url_host(url)
        if domain:
            domains.add(domain)
        identity = domain or url.lower()
        reliability = str(record.get("reliability") or "official")
        if reliability in {"official_personnel", "official_staff"}:
            community_sources.add(identity)
        elif reliability == "primary_media":
            media_sources.add(identity)
        else:
            official_sources.add(identity)

    return len(source_ids), len(official_sources), len(media_sources), len(community_sources), domains


def _all_from_source_ids(items: list[FeedItem], source_ids: set[str] | frozenset[str]) -> bool:
    return bool(items) and all(item.source_id in source_ids for item in items)


def _any_from_source_ids(items: list[FeedItem], source_ids: set[str] | frozenset[str]) -> bool:
    return any(item.source_id in source_ids for item in items)


def event_freshness_state(
    latest: datetime | None,
    lookback_hours: int,
    *,
    current: datetime | None = None,
) -> tuple[str, float | None, str]:
    """Classify event timestamp into fresh / future / undated / outside_window / stale."""
    if not latest:
        return "undated", None, "缺少发布时间"
    reference = current or now_utc()
    reference = reference.replace(tzinfo=UTC) if reference.tzinfo is None else reference.astimezone(UTC)
    published = latest.replace(tzinfo=UTC) if latest.tzinfo is None else latest.astimezone(UTC)
    age_hours = (reference - published).total_seconds() / 3600.0
    if age_hours < -2.0:
        return "future", age_hours, "发布时间在未来超过 2 小时，疑似源时间异常"
    if age_hours <= lookback_hours:
        return "fresh", age_hours, "发布时间在窗口内"
    if age_hours <= 72.0:
        return "outside_window", age_hours, "超过默认窗口但仍在 72 小时内"
    return "stale", age_hours, "疑似旧闻"


def _freshness_score(
    latest: datetime | None,
    lookback_hours: int,
    *,
    current: datetime | None = None,
) -> tuple[float, bool, str]:
    state, age_hours, reason = event_freshness_state(latest, lookback_hours, current=current)
    if state == "fresh" and age_hours is not None:
        return max(0.0, 25.0 - age_hours / max(lookback_hours, 1) * 10.0), True, reason
    if state == "outside_window":
        return 8.0, False, reason
    if state == "stale":
        return -10.0, False, reason
    return 0.0, False, reason


def _value_score(cluster: Cluster) -> float:
    text = clean_text(" ".join([cluster.title] + [x.summary for x in cluster.items[:3]])).lower()
    score = 0.0
    for kw in VALUE_KEYWORDS:
        if kw.lower() in text:
            score += 3.0
    if cluster.entity != "AI":
        score += 8.0
    if any(x.source_tier.upper() == "A" for x in cluster.items):
        score += 8.0
    if any(x.source_tier.upper() == "B" and x.source_reliability == "media" for x in cluster.items):
        score += 3.0
    if _any_from_source_ids(cluster.items, MODEL_REPO_SOURCE_IDS):
        score += 10.0
    if _all_from_source_ids(cluster.items, PAPER_SOURCE_IDS):
        return min(score, 18.0)
    return min(score, 35.0)


def _is_concrete_ai_policy_story(cluster: Cluster) -> bool:
    context = " ".join(f"{item.title} {item.summary}" for item in cluster.items[:3])
    return is_concrete_public_ai_policy(cluster.title, context)


def _cluster_relevance(cluster: Cluster) -> tuple[bool, int]:
    summaries = " ".join(x.summary for x in cluster.items[:3])
    relevant, score = is_ai_relevant(cluster.title, summaries)
    combined = clean_text(f"{cluster.title} {summaries}").lower()
    for kw in WEAK_MEDIA_CONTEXT_KEYWORDS:
        if kw in combined:
            score -= 2
    if cluster.entity not in {"", "AI"}:
        score += 2
    if _any_from_source_ids(cluster.items, MODEL_REPO_SOURCE_IDS) or _all_from_source_ids(cluster.items, PAPER_SOURCE_IDS):
        return True, max(score + 4, 4)
    if _is_concrete_ai_policy_story(cluster):
        return True, max(score, 4)
    has_media = any(x.source_tier.upper() == "B" or x.source_reliability == "media" for x in cluster.items)
    threshold = 3 if has_media else 2
    return (relevant or score >= threshold) and score >= threshold, score


def _is_non_mainstream_industry_story(cluster: Cluster) -> bool:
    title = clean_text(cluster.title).lower()
    if is_mainstream_ai(title, cluster.entity) or any(kw in title for kw in LOCAL_OPEN_RUNTIME_KEYWORDS):
        return False
    combined = clean_text(" ".join([cluster.title] + [x.summary for x in cluster.items[:3]])).lower()
    return any(kw in combined for kw in NON_MAINSTREAM_INDUSTRY_KEYWORDS)


def _has_mainstream_ai_signal(cluster: Cluster) -> bool:
    title = clean_text(cluster.title).lower()
    if is_mainstream_ai(title, cluster.entity) or cluster.entity == "本地模型 / 开源":
        return True
    if any(kw in title for kw in LOCAL_OPEN_RUNTIME_KEYWORDS):
        return True
    if _is_concrete_ai_policy_story(cluster):
        return True
    if _any_from_source_ids(cluster.items, MODEL_REPO_SOURCE_IDS):
        return True
    _, rel_score = _cluster_relevance(cluster)
    if _any_from_source_ids(cluster.items, SPECIALIST_AI_MEDIA_SOURCE_IDS) and rel_score >= 4 and not _is_non_mainstream_industry_story(cluster):
        return True
    return False


def _is_official_personnel_signal(cluster: Cluster) -> bool:
    return any(feed_item_is_official_personnel(item) for item in cluster.items) and _has_mainstream_ai_signal(cluster)


def _has_named_mainstream_ai_signal(cluster: Cluster) -> bool:
    title = clean_text(cluster.title).lower()
    if MODEL_TOKEN_RE.search(title):
        return True
    scrubbed = title
    for generic in COMMUNITY_HUB_GENERIC_KEYWORDS:
        scrubbed = scrubbed.replace(generic, " ")
    return is_mainstream_ai(scrubbed)


def _is_mainstream_media_signal(cluster: Cluster) -> bool:
    if not _has_mainstream_ai_signal(cluster):
        return False
    if _is_concrete_ai_policy_story(cluster):
        return True
    _, rel_score = _cluster_relevance(cluster)
    if _any_from_source_ids(cluster.items, SPECIALIST_AI_MEDIA_SOURCE_IDS) and rel_score >= 4:
        return True
    combined = clean_text(" ".join([cluster.title] + [x.summary for x in cluster.items[:3]])).lower()
    return is_mainstream_ai(combined, cluster.entity) or any(kw in combined for kw in LOCAL_OPEN_RUNTIME_KEYWORDS)


def _is_community_yellow_signal(cluster: Cluster) -> bool:
    if not _has_named_mainstream_ai_signal(cluster):
        return False
    combined = clean_text(" ".join([cluster.title] + [x.summary for x in cluster.items[:3]])).lower()
    if any(kw in combined for kw in COMMUNITY_NOISE_KEYWORDS):
        return False
    return any(kw in combined for kw in COMMUNITY_YELLOW_SIGNAL_KEYWORDS)


def _is_ai_relevant_cluster(cluster: Cluster) -> bool:
    if _is_official_personnel_signal(cluster):
        return True
    relevant, _ = _cluster_relevance(cluster)
    return relevant


def _has_missing_github_release(cluster: Cluster) -> bool:
    return any(isinstance(item.raw, dict) and item.raw.get("github_release_missing") for item in cluster.items)


def _explicit_deadline_conflict(item: FeedItem) -> str:
    """Detect a relisted evergreen page whose explicit deadline predates its feed timestamp."""
    if not item.published_at:
        return ""
    text = clean_text(item.summary)
    patterns = (
        r"(?:截止日期|申请截止|报名截止|截止时间)[^。；;]{0,28}?((?:20\d{2}\s*年\s*)?\d{1,2}\s*月\s*\d{1,2}\s*日)",
        r"(?:deadline|applications? close)[^。；;]{0,28}?((?:20\d{2})[-/]\d{1,2}[-/]\d{1,2})",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if not match:
            continue
        raw = match.group(1)
        year_match = re.search(r"20\d{2}", raw)
        year = int(year_match.group(0)) if year_match else item.published_at.year
        month_day = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})", raw)
        iso_day = re.search(r"20\d{2}[-/](\d{1,2})[-/](\d{1,2})", raw)
        if month_day:
            month, day = int(month_day.group(1)), int(month_day.group(2))
        elif iso_day:
            month, day = int(iso_day.group(1)), int(iso_day.group(2))
        else:
            continue
        try:
            deadline = datetime(year, month, day, tzinfo=UTC)
        except ValueError:
            continue
        if item.published_at.astimezone(UTC) - deadline > timedelta(days=2):
            return deadline.date().isoformat()
    return ""


def _cluster_deadline_conflict(cluster: Cluster) -> str:
    values = [val for item in cluster.items if (val := _explicit_deadline_conflict(item))]
    return max(values) if values else ""


def _source_summary_provenance(item: FeedItem) -> str:
    kind = str(item.raw.get("kind") or "") if isinstance(item.raw, dict) else ""
    if kind == "codex_agent_social_lead":
        return "verified_social_post"
    if item.source_tier.upper() == "A" or item.source_reliability in {"official", "official_social"}:
        return "official_source_summary"
    return "feed_summary"


def _is_navigation_summary(text: str) -> bool:
    lowered = clean_text(text).lower()
    markers = ("加载更多", "切换卡片", "全部;筛选", "all;company", "openai 新闻动态", "新闻页")
    return sum(1 for marker in markers if marker in lowered) >= 2


def _summary_fact_score(summary: str) -> tuple[int, int]:
    text = clean_text(summary)
    lowered = text.lower()
    score = 12 if has_cjk(text) else 0
    score += min(
        12,
        sum(
            2
            for term in ("新增", "支持", "开放", "上线", "提升", "降低", "价格", "上下文", "工具调用", "可用性", "用户")
            if term in text
        ),
    )
    if re.search(r"\d+(?:\.\d+)?\s*(?:%|倍|分|美元|元|token|tokens|ms|秒)", text, flags=re.I):
        score += 8
    if len(text) >= 80:
        score += 4
    if any(marker in lowered for marker in ("google news", "点击查看原文", "utm_source=", "v=ext&blog=", "w=3840&q=")):
        score -= 8
    return score, min(len(text), 500)


def _key_facts(cluster: Cluster) -> list[str]:
    lead = cluster.items[0]
    reference = _canonical_official_reference(cluster)
    reference_title = clean_text(str(reference.get("title") or "")) if reference else ""
    facts = [f"{cluster.entity} 相关事件：{reference_title or lead.title}"]
    primary_date = _reference_published_at(reference) or lead.published_at
    if primary_date:
        facts.append(f"首要来源发布时间：{primary_date.astimezone(UTC).strftime('%Y-%m-%d %H:%M UTC')}")
    if reference:
        domain = str(reference.get("domain") or url_host(reference.get("url")))
        facts.append(f"首要来源：{domain} 官方页面")
        for raw in reference.get("facts") or []:
            text = clean_text(str(raw), 520)
            if text and f"官方信息：{text}" not in facts:
                facts.append(f"官方信息：{text}")
            if len(facts) >= 5:
                return facts[:5]
    elif lead.source_name:
        facts.append(f"首要来源：{lead.source_name}")

    ranked_summaries: list[str] = []
    for item in cluster.items:
        summary = clean_text(item.summary, 520)
        if not summary or _is_navigation_summary(summary):
            continue
        normalized = re.sub(r"\W+", "", summary.lower())
        if any(normalized == re.sub(r"\W+", "", existing.lower()) for existing in ranked_summaries):
            continue
        ranked_summaries.append(summary)
    ranked_summaries.sort(key=_summary_fact_score, reverse=True)
    for summary in ranked_summaries[:2]:
        facts.append(f"来源摘要：{summary}")
    return facts[:5]


def _risk_and_confidence(
    cluster: Cluster,
    lookback_hours: int,
    *,
    current: datetime | None = None,
) -> tuple[str, int, bool, str, float, list[str]]:
    source_count, official, media, community, domains = _source_counts(cluster.items)
    freshness, in_window, freshness_reason = _freshness_score(cluster.published_at, lookback_hours, current=current)
    value = _value_score(cluster)
    corroboration = 0.0
    uncertainty: list[str] = []

    if not _is_ai_relevant_cluster(cluster):
        uncertainty.append("通用新闻源中未检测到足够 AI 相关信号。")
        return "red", 25, False, "AI 相关性不足，默认不进主视频。", -50.0, uncertainty

    deadline_conflict = _cluster_deadline_conflict(cluster)
    if deadline_conflict:
        uncertainty.append(f"正文明确截止于 {deadline_conflict}，但来源发布时间更晚，疑似旧页面被重新收录。")
        return "red", 20, False, "正文截止日期与来源发布时间冲突，按旧页面处理。", -40.0, uncertainty

    official_reference_state = _official_reference_state(cluster)
    if official_reference_state == "stale":
        uncertainty.append("媒体线索已追到对应官方页面，但官方发布时间早于本次新闻窗口。")
        return "red", 30, False, "官方原始消息已过时，属于旧事件再次被转载，不进入本期。", -20.0, uncertainty

    _, relevance = _cluster_relevance(cluster)
    if _has_missing_github_release(cluster):
        uncertainty.append("公开发布页暂不可访问；只按官方源记录保留，细节待确认。")

    if _all_from_source_ids(cluster.items, DISCOVERY_SOURCE_IDS) and official == 0:
        uncertainty.append("来自 Google News 搜索聚合，需找到原始媒体、官方发布或第二来源补证。")
        return "red", min(58, int(42 + value * 0.20)), False, "搜索聚合单源只作线索，默认不进主视频。", freshness + value, uncertainty

    if official > 0:
        corroboration += 35.0
    if source_count >= 2 or len(domains) >= 2:
        corroboration += 18.0
    if media >= 2:
        corroboration += 10.0
    if community > 0 and official == 0 and media == 0:
        uncertainty.append("目前只有社区/聚合来源，不能写成官方确认。")
    if cluster.published_at is None:
        uncertainty.append("缺少可靠发布时间，需要人工确认是否旧闻。")
    if not in_window:
        uncertainty.append(freshness_reason)

    score = freshness + value + corroboration
    if cluster.published_at is None:
        return (
            "red",
            min(58, int(38 + value * 0.20)),
            False,
            f"缺少可靠发布时间，不进入本次 {lookback_hours} 小时主视频；只能作为有日期新闻的补充证据。",
            score,
            uncertainty,
        )

    paper_only = _all_from_source_ids(cluster.items, PAPER_SOURCE_IDS)
    if paper_only and official > 0:
        uncertainty.append("arXiv 只能确认论文/预印本存在，行业影响未交叉验证。")
        if in_window:
            return "yellow", min(78, int(58 + value * 0.45)), True, "arXiv 官方记录确认论文存在；作为研究动态可播，影响力需保守表述。", score, uncertainty
        return "red", min(60, int(45 + value * 0.20)), False, "arXiv 论文已过新鲜窗口，默认不进主视频。", score, uncertainty

    if official_reference_state in {"undated", "social"} and media >= 1 and in_window:
        uncertainty.append("已找到与事件匹配的官方页面，但页面未标可靠发布时间；以媒体发布时间确认本期时效。")
        return "yellow", min(80, int(60 + value * 0.35)), True, "事件已与官方页面核对；官方页无日期，按媒体报道时间和官方事实保守播报。", score, uncertainty

    if official > 0 and in_window:
        return "green", min(96, int(72 + value * 0.40 + min(source_count, 3) * 4)), True, "官方来源确认，且发布时间在窗口内。", score, uncertainty
    if official > 0:
        return (
            "red",
            min(62, int(42 + value * 0.25)),
            False,
            f"官方来源确认，但发布时间不在本次 {lookback_hours} 小时窗口内。",
            score,
            uncertainty,
        )
    if community >= 1 and _is_official_personnel_signal(cluster):
        if in_window:
            uncertainty.append("X 官方人员动态；用原帖截图呈现，不等同于正式发布页。")
            return "yellow", min(76, int(56 + value * 0.30)), True, "X 官方人员动态，可作为短讯入选；必须保留原帖截图并避免写成正式发布。", score, uncertainty
        return "red", min(56, int(38 + value * 0.20)), False, "X 官方人员动态已过新鲜窗口，默认不进主视频。", score, uncertainty
    if media >= 2 and in_window:
        return "yellow", min(82, int(58 + value * 0.35 + media * 3)), True, "多个媒体来源交叉出现，建议保守措辞。", score, uncertainty
    if media >= 1 and in_window:
        uncertainty.append("待确认。")
        if not _has_mainstream_ai_signal(cluster):
            uncertainty.append("单一媒体来源且不属于主流 AI 公司、模型或工具链，不进入主视频。")
            return "red", min(58, int(42 + value * 0.20)), False, "非主流 AI 单一媒体线索，默认不进主视频。", score, uncertainty
        if relevance < 4 and not _is_mainstream_media_signal(cluster):
            uncertainty.append("单一媒体来源且 AI 相关性偏弱，不进入主视频。")
            return "red", min(58, int(42 + value * 0.20)), False, "单一媒体来源的 AI 相关性不足，默认不进主视频。", score, uncertainty
        if _is_mainstream_media_signal(cluster):
            uncertainty.append("官方未明确，按媒体线索保守表述。")
            return "yellow", min(74, int(52 + value * 0.30)), True, "主流 AI 媒体线索，官方未明确，需保守措辞。", score, uncertainty
        return "yellow", min(74, int(52 + value * 0.30)), True, "单一可信媒体来源，自动采用明确归因和保守措辞。", score, uncertainty
    if community >= 1:
        if in_window and _is_community_yellow_signal(cluster):
            uncertainty.append("目前只有社区/聚合源头，必须保留源头截图，不能写成官方确认。")
            uncertainty.append("官方未明确，按用户反馈、性能或开源线索保守表述。")
            return "yellow", min(68, int(48 + value * 0.25)), True, "社区源头的主流 AI 观察线索，可进主视频但必须截图并保守措辞。", score, uncertainty
        return "red", min(55, int(35 + value * 0.20)), False, "社区/聚合单源只作线索，默认不进主视频。", score, uncertainty
    return "red", 35, False, "证据不足。", score, uncertainty


def verify_clusters(
    clusters: list[Cluster],
    *,
    now: datetime | None = None,
    lookback_hours: int = 24,
    defaults: dict[str, Any] | None = None,
) -> list[EvidenceCard]:
    """Assign publication risk (`green`/`yellow`/`red`), isolate provenance, and cap paper/repo dominance."""
    cfg = defaults or {}
    current = now or now_utc()
    max_cards = int(cfg.get("verify_max_cards", cfg.get("max_cards", 80)))
    cards: list[EvidenceCard] = []

    for cluster in clusters:
        source_count, official, media, community, _domains = _source_counts(cluster.items)
        risk, confidence, selected, reason, score, uncertainty = _risk_and_confidence(
            cluster,
            lookback_hours,
            current=current,
        )
        evidence: list[dict[str, str]] = []
        seen: set[str] = set()
        canonical_reference = _canonical_official_reference(cluster)
        if canonical_reference:
            reference_url = str(canonical_reference.get("final_url") or canonical_reference.get("url") or "")
            reference_reliability = str(canonical_reference.get("reliability") or "official")
            reference_host = url_host(reference_url)
            source_label = f"{reference_host} 官方"
            if reference_reliability == "official_social":
                identity = re.search(r"/(?:x\.com/|twitter\.com/)?([A-Za-z0-9_]{1,32})/status/", reference_url)
                source_label = f"X / @{identity.group(1)}" if identity else "X 官方账号"
            evidence.append(
                {
                    "source": source_label,
                    "tier": "A",
                    "reliability": reference_reliability,
                    "title": clean_text(str(canonical_reference.get("title") or cluster.title)),
                    "url": reference_url,
                    "published_at": str(canonical_reference.get("published_at") or ""),
                    "excerpt": clean_text(str(canonical_reference.get("excerpt") or ""), 1800),
                    "reconciled_official": "true",
                    "discovery_url": str(canonical_reference.get("discovered_from") or ""),
                    "official_match_reason": str(canonical_reference.get("match_reason") or ""),
                    "official_freshness": str(canonical_reference.get("freshness") or ""),
                }
            )
            seen.add(reference_url)

        for item in cluster.items:
            key = item.link or item.guid
            if key in seen:
                continue
            seen.add(key)
            stored_excerpts = item.raw.get("evidence_excerpts") if isinstance(item.raw, dict) else None
            stored_excerpt_text = "\n".join(
                clean_text(str(row.get("text") or ""))
                for row in stored_excerpts or []
                if isinstance(row, dict) and clean_text(str(row.get("text") or ""))
            )
            summary_text = clean_text(item.summary, 1800)
            summary_provenance = _source_summary_provenance(item)
            excerpt_text = clean_text(stored_excerpt_text or summary_text, 1800)
            excerpt_provenance = "article_excerpts" if stored_excerpt_text else summary_provenance

            evidence_record: dict[str, str] = {
                "source": item.source_name,
                "tier": item.source_tier,
                "reliability": item.source_reliability,
                "title": item.title,
                "url": item.link,
                "published_at": item.published_at.isoformat() if item.published_at else "",
                "excerpt": excerpt_text,
                "excerpt_provenance": excerpt_provenance,
                "document_id": str(item.raw.get("document_id") or "") if isinstance(item.raw, dict) else "",
                "document_content_hash": str(item.raw.get("document_content_hash") or "") if isinstance(item.raw, dict) else "",
            }
            if stored_excerpt_text and summary_text:
                evidence_record["source_summary"] = summary_text
                evidence_record["source_summary_provenance"] = summary_provenance
            if isinstance(item.raw, dict):
                provenance = str(item.raw.get("published_at_provenance") or "")
                article_published_at = str(item.raw.get("article_published_at") or "")
                discovery_url = str(item.raw.get("discovery_url") or "")
                if provenance:
                    evidence_record["published_at_provenance"] = provenance
                if discovery_url and discovery_url != item.link:
                    evidence_record["discovery_url"] = discovery_url
                if article_published_at:
                    evidence_record["article_published_at"] = article_published_at
                    evidence_record["article_published_at_provenance"] = str(
                        item.raw.get("article_published_at_provenance") or "article_metadata"
                    )
                if item.raw.get("github_release_enriched") is True and item.raw.get("github_compare_verified") is True:
                    evidence_record["github_compare_verified"] = "true"
                    evidence_record["github_compare_url"] = str(item.raw.get("github_compare_url") or "")
                    evidence_record["github_compare_summary"] = str(item.raw.get("github_compare_summary") or "")

            article_enrichment = item.raw.get("article_enrichment") if isinstance(item.raw, dict) else None
            if isinstance(article_enrichment, dict):
                evidence_record["article_crawler"] = str(article_enrichment.get("crawler") or "")
                evidence_record["article_status"] = str(article_enrichment.get("status") or "")
                if article_enrichment.get("final_url"):
                    evidence_record["final_url"] = str(article_enrichment.get("final_url") or "")
                screenshot_path = str(article_enrichment.get("screenshot_path") or "")
                if screenshot_path:
                    evidence_record["screenshot_candidate_status"] = "unvalidated"
                    evidence_record["screenshot_candidate_path"] = screenshot_path
                images = article_enrichment.get("images")
                if isinstance(images, list) and images:
                    evidence_record["article_images"] = json.dumps(images[:5], ensure_ascii=False)
                repair = article_enrichment.get("repair")
                if isinstance(repair, dict) and repair.get("attempted"):
                    evidence_record["auto_repair"] = json.dumps(repair, ensure_ascii=False)

            evidence.append(evidence_record)
            if len(evidence) >= 5:
                break

        first_seen = min((x.fetched_at for x in cluster.items), default=current)
        reference_title = clean_text(str(canonical_reference.get("title") or "")) if canonical_reference else ""
        reference_date = _reference_published_at(canonical_reference)
        cards.append(
            EvidenceCard(
                cluster_key=cluster.key,
                event_title=reference_title or cluster.title,
                entity=cluster.entity,
                risk=risk,
                confidence=confidence,
                selected=selected,
                reason=reason,
                source_count=source_count,
                official_count=official,
                media_count=media,
                community_count=community,
                first_seen_at=first_seen,
                latest_published_at=reference_date or cluster.published_at,
                key_facts=_key_facts(cluster),
                evidence_links=evidence,
                uncertainty=uncertainty,
                score=score,
            )
        )

    cards.sort(key=lambda c: (c.selected, c.risk == "green", c.score, c.confidence), reverse=True)
    if max_cards <= 0:
        return cards

    result: list[EvidenceCard] = []
    deferred: list[EvidenceCard] = []
    paper_count = 0
    repository_count = 0
    paper_cap = max(3, max_cards // 8)
    repository_cap = max(3, max_cards // 8)

    for card in cards:
        sources = {str(e.get("source") or "").lower() for e in card.evidence_links}
        urls = [str(e.get("url") or "").lower() for e in card.evidence_links]
        is_paper = any("arxiv" in src for src in sources) or any("arxiv.org/" in url for url in urls)
        is_repository = any("huggingface" in src or "modelscope" in src for src in sources) or any(
            "huggingface.co/" in url or "modelscope.cn/" in url for url in urls
        )
        if is_paper and paper_count >= paper_cap:
            deferred.append(card)
            continue
        if is_repository and repository_count >= repository_cap:
            deferred.append(card)
            continue
        result.append(card)
        paper_count += int(is_paper)
        repository_count += int(is_repository)
        if len(result) >= max_cards:
            break

    if len(result) < max_cards:
        result.extend(deferred[: max_cards - len(result)])
    result.sort(key=lambda c: (c.selected, c.risk == "green", c.score, c.confidence), reverse=True)
    return result[:max_cards]

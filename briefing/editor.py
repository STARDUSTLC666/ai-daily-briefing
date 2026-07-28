from __future__ import annotations

import re
from typing import Any


from .models import EvidenceCard
from .presentation import card_display_entity, has_arxiv_evidence
from .social_signals import card_is_community_signal, card_is_official_personnel_signal
from .util import clean_text


LEAD_LIMIT = 86
CARD_TITLE_LIMIT = 10
CARD_BODY_LIMIT = 42
CARD_ICONS = {
    "发布信息": "⚗",
    "参数规格": "▦",
    "能力变化": "↗",
    "对比上一版": "⇄",
    "使用入口": "▣",
    "价格限制": "¥",
    "待确认": "⏱",
    "仓库状态": "▣",
    "模型信息": "◈",
    "可用性": "◇",
    "下一步": "⏱",
    "版本变化": "⇄",
    "修复重点": "●",
    "使用影响": "◇",
    "后续确认": "⏱",
    "研究主题": "↗",
    "方法线索": "◈",
    "适用场景": "▣",
    "复现观察": "⏱",
    "发生了什么": "▣",
    "核心数据": "▦",
    "行业含义": "◇",
    "我的判断": "✦",
    "用户反馈": "◎",
    "测试条件": "◈",
    "初步结果": "↗",
    "注意事项": "⏱",
    "功能方法": "⚙",
    "实测结论": "✓",
    "适合谁用": "◇",
    "落地场景": "▣",
    "成本效率": "¥",
    "核心看点": "✦",
    "核心能力": "🧠",
    "参数": "▦",
    "上下文": "↔",
    "模态": "◐",
    "API入口": "▣",
    "开放情况": "◇",
    "编辑判断": "✦",
    "新增功能": "＋",
    "入口": "▣",
    "免费收费": "¥",
    "支持平台": "▦",
    "Version": "⚙",
    "What's New": "＋",
    "Bug Fix": "✓",
    "Breaking": "!",
    "升级建议": "✦",
    "模型名称": "🧠",
    "参数规模": "▦",
    "License": "◇",
    "下载方式": "⬇",
    "支持任务": "✓",
    "排名": "📈",
    "领先幅度": "↗",
    "测试内容": "▣",
    "测试口径": "✓",
    "公司": "🏢",
    "事件": "▣",
    "行业信号": "◇",
    "普通用户": "👥",
    "影响对象": "👥",
    "行业意义": "🏢",
    "原帖内容": "▣",
    "产品方向": "◇",
}
BANNED_CARD_TITLES = {"预览", "版本变化", "使用影响", "后续观察", "仓库状态", "发生了什么", "一句话", "可信度"}
DESIGN_CARD_TITLES = [
    "核心看点",
    "核心能力",
    "参数",
    "上下文",
    "模态",
    "API入口",
    "开放情况",
    "新增功能",
    "入口",
    "免费收费",
    "支持平台",
    "Version",
    "What's New",
    "Bug Fix",
    "Breaking",
    "升级建议",
    "模型名称",
    "参数规模",
    "License",
    "下载方式",
    "支持任务",
    "排名",
    "领先幅度",
    "测试内容",
    "测试口径",
    "公司",
    "事件",
    "行业信号",
    "普通用户",
    "影响对象",
    "行业意义",
    "原帖内容",
    "产品方向",
    "编辑判断",
]
CARD_TITLES = DESIGN_CARD_TITLES

FORBIDDEN_PUBLIC_PHRASES = [
    "补充信息",
    "信息要点",
    "页面已经出现更新",
    "官方页面已更新这个模型条目",
    "可信度",
    "confidence",
    "观察口径",
    "完整来源",
    "自动生成",
    "全网热议",
    "炸裂",
    "震惊",
    "碾压",
    "吊打",
    "史上最强",
    "工作流",
    "升级成本",
    "兼容性",
    "维护负担",
    "主要更新或修复",
    "修复/维护线索",
    "预览",
    "版本变化",
    "使用影响",
    "后续观察",
    "仓库状态",
    "发生了什么",
    "来源可查",
    "后续继续观察",
    "官方补证",
    "值得期待",
    "重点关注",
    "持续关注",
    "具体以官方为准",
    "等待更多消息",
    "如有更新第一时间通知",
    "一句话",
    "信息不够",
    "不能直接当成正式新闻",
    "暂时剔除",
    "爬虫",
    "筛选",
    "官网抓取",
    "后面需要继续优化",
    "是否影响",
]

MODEL_REPO_UNSUPPORTED = [
    "正式发布",
    "发布会",
    "已经开源",
    "开放下载",
    "开放使用",
    "可直接使用",
    "权重已放出",
    "用户实测",
    "性能领先",
    "价格公布",
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
    "onnx": "ONNX",
    "gguf": "GGUF",
    "mlx": "MLX",
}

BENCHMARK_NAMES = [
    "AIME",
    "GPQA",
    "MMLU",
    "MMLU-Pro",
    "LiveCodeBench",
    "SWE-bench",
    "HumanEval",
    "MBPP",
    "GSM8K",
    "MATH",
    "IFEval",
    "BFCL",
    "Arena",
    "LMArena",
    "Chatbot Arena",
    "MMMU",
    "MathVista",
]

PERFORMANCE_KEYWORDS = [
    "benchmark",
    "benchmarks",
    "leaderboard",
    "eval",
    "evaluation",
    "score",
    "sota",
    "state-of-the-art",
    "outperform",
    "outperforms",
    "performance",
    "accuracy",
    "pass@",
    "win rate",
    "评测",
    "榜单",
    "跑分",
    "得分",
    "性能",
    "准确率",
    "胜率",
    "领先",
    "超过",
    "优于",
    "对比",
]

CORE_DIRECTIVE_PHRASES = [
    "先看",
    "要看",
    "看点在",
    "继续看",
    "可以重点问",
    "重点问",
    "后续看",
    "值得关注",
    "重点关注",
    "真正要看",
]

INTERNAL_QUALITY_PHRASES = [
    "只采用已核对来源里的确定信息",
    "这条值得关注的是它可能带来的实际使用变化",
    "最终要看它是不是真有用",
    "影响要落到真实使用、产品策略或后续选择上",
    "后续继续观察",
    "来源可查",
    "具体以官方为准",
]

SPECIFICITY_KEYWORDS = [
    "benchmark",
    "leaderboard",
    "eval",
    "score",
    "AIME",
    "GPQA",
    "LiveCodeBench",
    "SWE-bench",
    "价格",
    "API",
    "上下文",
    "参数",
    "显存",
    "下载",
    "许可",
    "版本",
    "修复",
    "新增",
    "性能",
    "榜单",
    "评测",
]

MAINSTREAM_AI_ENTITIES = [
    "openai",
    "anthropic",
    "claude",
    "google",
    "deepmind",
    "gemini",
    "qwen",
    "通义",
    "阿里",
    "deepseek",
    "kimi",
    "moonshot",
    "字节",
    "豆包",
    "百度",
    "文心",
    "腾讯",
    "混元",
    "智谱",
    "zhipu",
    "minimax",
    "mistral",
    "meta",
    "llama",
    "hugging face",
    "modelscope",
    "nvidia",
    "英伟达",
]

NEWS_JUNK_PHRASES = [
    "点击查看原文",
    "查看原文",
    "阅读全文",
    "阅读原文",
    "更多内容",
    "欢迎关注",
    "微信公众号",
    "更多精彩内容",
]

NEWS_FACT_LABELS = [
    "相关事件",
    "来源摘要",
    "摘要",
    "核心信息",
    "关键信息",
    "关键事实",
    "已确认内容",
    "可以确认",
    "可以确认的是",
    "新闻内容",
    "事件",
]

ROUNDUP_PREFIX_MARKERS = [
    "早报",
    "晚报",
    "日报",
    "快讯",
    "播报",
    "点1氪",
    "点氪",
    "一周",
]

AI_NEWS_KEYWORDS = [
    "ai",
    "人工智能",
    "大模型",
    "模型",
    "agent",
    "智能体",
    "openai",
    "chatgpt",
    "codex",
    "anthropic",
    "claude",
    "claude code",
    "gemini",
    "deepseek",
    "qwen",
    "通义",
    "豆包",
    "kimi",
    "智谱",
    "minimax",
    "微软",
    "google",
    "阿里",
    "百度",
    "英伟达",
    "机器人",
    "具身智能",
    "自动驾驶",
]

STRONG_AI_RELEVANCE_KEYWORDS = [
    "大模型",
    "生成式ai",
    "生成式 AI",
    "llm",
    "aigc",
    "openai",
    "anthropic",
    "claude",
    "chatgpt",
    "gemini",
    "deepmind",
    "qwen",
    "通义",
    "kimi",
    "deepseek",
    "minimax",
    "智谱",
    "glm",
    "豆包",
    "混元",
    "文心",
    "llama",
    "mistral",
    "hugging face",
    "modelscope",
    "copilot",
    "codex",
    "智能体",
    "agent",
    "多模态",
    "开源模型",
    "ai 编程",
    "ai编程",
]

NEWS_ACTION_KEYWORDS = [
    "发布",
    "上线",
    "推出",
    "更新",
    "开源",
    "禁用",
    "限制",
    "内测",
    "开放",
    "融资",
    "投资",
    "收购",
    "组建",
    "合作",
    "调整",
    "回应",
]

RAW_ENGLISH_RE = re.compile(r"[A-Za-z][A-Za-z0-9_./+-]*")


def _soft_limit(text: str, limit: int) -> str:
    text = clean_text(text or "")
    if len(text) <= limit:
        return text
    # Prefer a clause boundary and never leave half an ASCII word ("LiveCodeBenc")
    # on public card copy; fall back to the hard slice only when no boundary is usable.
    window = text[: limit + 1]
    boundary = max(window.rfind(mark) for mark in ["。", "！", "？", "；", ";", "，", ",", "、", " "])
    cut = boundary + 1 if boundary >= max(12, int(limit * 0.6)) else limit
    result = text[:cut]
    if cut < len(text) and result[-1:].isascii() and result[-1:].isalnum() and text[cut : cut + 1].isascii() and text[cut : cut + 1].isalnum():
        trimmed = re.sub(r"[A-Za-z0-9_.+-]+$", "", result)
        if len(trimmed) >= max(8, int(limit * 0.4)):
            result = trimmed
    return result.rstrip(" ，。,.:-_、;；/")


def _has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def _looks_raw_english(text: str, min_len: int = 34) -> bool:
    text = clean_text(text or "")
    if len(text) < min_len or _has_cjk(text):
        return False
    letters = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    visible = sum(1 for ch in text if not ch.isspace())
    return visible > 0 and letters / visible > 0.58


def _fit_text(candidates: list[str], limit: int) -> str:
    fallback = ""
    for candidate in candidates:
        text = clean_text(candidate)
        if not text:
            continue
        fallback = text
        if len(text) <= limit:
            return text
    return _soft_limit(fallback, limit)


def _norm_text(text: str) -> str:
    return re.sub(r"[\W_]+", "", clean_text(text or "").lower())


def _strip_news_label(text: str) -> str:
    text = clean_text(text or "")
    if not text:
        return ""
    label_pattern = "|".join(re.escape(label) for label in NEWS_FACT_LABELS)
    text = re.sub(rf"^(?:[^：:]{{1,40}}\s*)?(?:{label_pattern})\s*[:：]\s*", "", text)
    return text.strip()


def _remove_roundup_prefix(text: str) -> str:
    text = clean_text(text or "")
    match = re.match(r"^([^|｜]{1,18})[|｜]\s*(.+)$", text)
    if not match:
        return text
    prefix, rest = match.groups()
    # Only strip prefixes that look like roundup/section labels; a bare short prefix
    # such as "OpenAI|GPT-6 正式上线" is subject attribution, not a roundup marker.
    if any(marker in prefix for marker in ROUNDUP_PREFIX_MARKERS) or re.search(
        r"\d{1,2}[:：]\d{2}|\d{1,2}月\d{1,2}日", prefix
    ):
        return rest.strip()
    return text


def _clean_news_fragment(text: str) -> str:
    text = _remove_roundup_prefix(_strip_news_label(text))
    text = re.sub(r"^作者\s*[|｜].*?硬氪获悉[,，]\s*", "", text)
    text = re.sub(r"^.*?编辑\s*[|｜].*?硬氪获悉[,，]\s*", "", text)
    for phrase in NEWS_JUNK_PHRASES:
        text = text.replace(phrase, "")
    text = re.sub(r"\s*[>＞]\s*$", "", text)
    text = re.sub(r"\s+", " ", text).strip(" ，。；;、")
    return text


def _split_news_fragments(text: str) -> list[str]:
    text = _clean_news_fragment(text)
    if not text:
        return []
    parts = re.split(r"[。！？!?；;\n]+", text)
    fragments: list[str] = []
    seen: set[str] = set()
    for part in parts:
        cleaned = _clean_news_fragment(part)
        if len(cleaned) < 6:
            continue
        if any(phrase in cleaned for phrase in NEWS_JUNK_PHRASES):
            continue
        norm = _norm_text(cleaned)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        fragments.append(cleaned)
    return fragments or ([text] if len(text) >= 6 else [])


def _entity_terms(card: EvidenceCard) -> list[str]:
    raw = clean_text(card.entity or "")
    terms: list[str] = []
    for piece in re.split(r"[/|｜,，、&\s]+", raw):
        piece = piece.strip()
        if len(piece) < 2:
            continue
        if piece.lower() in {"ai", "人工智能", "模型"}:
            continue
        terms.append(piece)
    combined = " ".join([card.event_title, card.entity, " ".join(card.key_facts)]).lower()
    for keyword in AI_NEWS_KEYWORDS:
        if keyword.lower() in combined and keyword not in terms:
            terms.append(keyword)
    return terms


def _segment_score(segment: str, terms: list[str]) -> int:
    lowered = segment.lower()
    score = 0
    if any(phrase in segment for phrase in NEWS_JUNK_PHRASES):
        score -= 30
    for term in terms:
        term_lower = term.lower()
        if term_lower and term_lower in lowered:
            score += 8 + min(len(term_lower), 8)
    for keyword in AI_NEWS_KEYWORDS:
        if keyword.lower() in lowered:
            score += 3
    if any(keyword in segment for keyword in NEWS_ACTION_KEYWORDS):
        score += 2
    if len(segment) < 8:
        score -= 8
    if re.match(r"^\d{1,2}点", segment):
        score -= 6
    return score


def _best_scored_fragment(fragments: list[str], terms: list[str]) -> str:
    best = ""
    best_score = 0
    for fragment in fragments:
        score = _segment_score(fragment, terms)
        if score > best_score:
            best = fragment
            best_score = score
    return best


def _title_fragments(card: EvidenceCard, display_title: str = "") -> list[str]:
    candidates: list[str] = []
    for raw in [display_title, card.event_title, *(str(e.get("title") or "") for e in card.evidence_links)]:
        for fragment in _split_news_fragments(raw):
            if _looks_raw_english(fragment):
                continue
            if fragment not in candidates:
                candidates.append(fragment)
    return candidates


def _fact_fragments(card: EvidenceCard) -> list[str]:
    candidates: list[str] = []
    for raw in card.key_facts:
        for fragment in _split_news_fragments(raw):
            if _looks_raw_english(fragment):
                continue
            if fragment not in candidates:
                candidates.append(fragment)
    return candidates


def _is_related_fragment(fragment: str, focus_title: str, terms: list[str]) -> bool:
    lowered = fragment.lower()
    if any(term.lower() in lowered for term in terms if term):
        return True
    focus_tokens = [token for token in re.split(r"[\W_]+", focus_title.lower()) if len(token) >= 3]
    return any(token in lowered for token in focus_tokens)


def _news_impact_line(title: str, detail: str) -> str:
    text = f"{title} {detail}".lower()
    if "禁用" in text and any(k in text for k in ["claude", "codex", "code", "代码", "开发"]):
        return "影响主要在企业代码安全、数据合规和开发工具选型。"
    if any(k in text for k in ["agent", "智能体", "工具链", "开发"]):
        return "它会影响团队怎样给 AI 工具设权限、审计和责任边界。"
    if any(k in text for k in ["融资", "投资", "收购"]):
        return "它说明资本仍在押注这个方向，但落地要看客户和交付。"
    if any(k in text for k in ["政策", "监管", "合规", "禁用", "限制"]):
        return "重点是合规边界会不会改变企业和个人的使用方式。"
    if any(k in text for k in ["产品", "app", "客户端", "上线", "内测"]):
        return "影响要看入口、覆盖范围和普通用户能不能稳定用到。"
    return "价值取决于它能不能改变真实使用、产品入口或选择成本。"


def _news_discussion_line(title: str, detail: str) -> str:
    text = f"{title} {detail}".lower()
    if "禁用" in text and any(k in text for k in ["claude", "codex", "code", "代码", "开发"]):
        return "核心不是某个工具突然不能用，而是企业开始给 AI 编程工具划权限边界。"
    if any(k in text for k in ["agent", "智能体"]):
        return "关键不在概念热不热，而是它能不能稳定帮人完成任务。"
    if any(k in text for k in ["融资", "投资", "收购"]):
        return "产业类消息要看钱投向哪里，也要看产品和客户是否跟得上。"
    return "先把事实和判断分开，只保留来源里能说明的内容。"


def focused_news_summary(card: EvidenceCard, display_title: str = "") -> dict[str, str | bool]:
    official_personnel = official_personnel_signal_summary(card, display_title)
    if official_personnel:
        return official_personnel
    community = community_observation_summary(card, display_title)
    if community:
        return community
    media = media_observation_summary(card, display_title)
    if media:
        return media
    if _is_model_repo_update(card) or _is_github_release(card) or _is_paper(card):
        title = _clean_news_fragment(display_title or card.event_title) or f"{card.entity} 今日动态"
        return {
            "focused": False,
            "title": _soft_limit(title, 64),
            "lead": _soft_limit(title, LEAD_LIMIT),
            "fact": _soft_limit(title, CARD_BODY_LIMIT),
            "detail": "",
            "impact": "",
            "discussion": "",
            "followup": "",
        }
    terms = _entity_terms(card)
    raw_title = clean_text(display_title or card.event_title)
    title_candidates = _title_fragments(card, raw_title)
    focus_title = _best_scored_fragment(title_candidates, terms) or _clean_news_fragment(raw_title)
    focus_title = focus_title or raw_title or f"{card.entity} 今日动态"

    fact_candidates = _fact_fragments(card)
    related_facts = [
        fragment
        for fragment in fact_candidates
        if _norm_text(fragment) != _norm_text(focus_title) and _is_related_fragment(fragment, focus_title, terms)
    ]
    detail = _best_scored_fragment(related_facts, terms)
    if not detail and related_facts:
        detail = related_facts[0]

    source = _short_source(_source_name(card))
    original_title = clean_text(card.event_title or raw_title)
    focused = (
        _norm_text(focus_title) != _norm_text(_clean_news_fragment(raw_title))
        or _norm_text(focus_title) != _norm_text(_clean_news_fragment(original_title))
        or len(_split_news_fragments(original_title)) > 1
    )
    if card.risk == "yellow" or card.media_count > 0:
        lead = f"{source}提到{focus_title}；目前按媒体报道处理。"
    else:
        lead = f"{source}记录{focus_title}；按已核对来源处理。"
    uncertainty = _first_uncertainty(card)
    return {
        "focused": focused,
        "title": _soft_limit(focus_title, 64),
        "lead": _soft_limit(lead, LEAD_LIMIT),
        "fact": _soft_limit(detail or f"{source}提到：{focus_title}。", CARD_BODY_LIMIT),
        "detail": _soft_limit(detail, CARD_BODY_LIMIT) if detail else "",
        "impact": _news_impact_line(focus_title, detail),
        "discussion": _news_discussion_line(focus_title, detail),
        "followup": _soft_limit(uncertainty or "等更多来源、官方说明和实际反馈交叉验证。", CARD_BODY_LIMIT),
    }


def _source_name(card: EvidenceCard) -> str:
    if card.evidence_links:
        return str(card.evidence_links[0].get("source") or "资料来源")
    return "资料来源"


def _is_model_repo_update(card: EvidenceCard) -> bool:
    raw = " ".join(
        [
            card.event_title,
            _source_name(card),
            " ".join(card.key_facts),
            " ".join(str(e.get("url") or "") for e in card.evidence_links),
        ]
    ).lower()
    return "hugging face" in raw or "huggingface.co" in raw or "模型仓库" in raw


def _is_github_release(card: EvidenceCard) -> bool:
    raw = " ".join([card.event_title, _source_name(card), " ".join(str(e.get("url") or "") for e in card.evidence_links)]).lower()
    return "github releases" in raw or "github release" in raw or "/releases/tag/" in raw


def _is_paper(card: EvidenceCard) -> bool:
    return has_arxiv_evidence(card)


def _is_community_observation(card: EvidenceCard) -> bool:
    return card_is_community_signal(card)


def _official_person_name(source: str) -> str:
    source = clean_text(source)
    if "/" in source:
        source = source.split("/", 1)[1].strip()
    source = re.sub(r"\s*@\w+.*$", "", source).strip()
    source = re.sub(r"^(?:X|Twitter)\s*[:：-]\s*", "", source, flags=re.I).strip()
    return source or "官方人员"


def _official_signal_text(card: EvidenceCard, display_title: str = "") -> str:
    raw = clean_text(" ".join([display_title, card.event_title, _source_summary(card), " ".join(card.key_facts)]))
    lowered = raw.lower()
    if "ultra will be in codex" in lowered:
        return "Ultra 会进入 Codex"
    quote_match = re.search(r"[“\"']([^“”\"']{6,80})[”\"']", raw)
    if quote_match:
        quote = clean_text(quote_match.group(1))
        if quote:
            return _soft_limit(quote, CARD_BODY_LIMIT)
    title = clean_text(display_title or card.event_title)
    title = re.sub(r"^.*?(?:says|said|posts?|posted|透露|暗示|称)\s*[:：,-]?\s*", "", title, flags=re.I).strip()
    return _soft_limit(title or "原帖释放产品方向信号", CARD_BODY_LIMIT)


def official_personnel_signal_summary(card: EvidenceCard, display_title: str = "") -> dict[str, str | bool] | None:
    if not card_is_official_personnel_signal(card):
        return None
    source = _short_source(_source_name(card))
    person = _official_person_name(source)
    signal = _official_signal_text(card, display_title)
    source_label = source if source.lower().startswith(("x", "twitter")) else f"X / {person}"
    lead = f"{person} 在 X 上透露 {signal}。"
    fact = signal
    return {
        "focused": True,
        "title": _soft_limit(signal, 64),
        "lead": _soft_limit(lead, LEAD_LIMIT),
        "fact": _soft_limit(fact, CARD_BODY_LIMIT),
        "detail": _soft_limit(f"{source_label} 原帖直接提到这一信息", CARD_BODY_LIMIT),
        "impact": "这是产品方向信号。不是上线公告。",
        "discussion": "这是个人公开表态，不等同于产品公告。",
        "followup": "",
    }


def _model_hint(text: str, fallback: str) -> str:
    patterns = [
        r"qwen\s*3\.6[-\s]*27b",
        r"qwen3\.6[-\s]*27",
        r"qwen\s*3\.5[-\s]*122b",
        r"gemma\s*4\s*26b",
        r"claude\s*sonnet\s*5",
        r"codex",
        r"llama\.cpp",
        r"vllm",
    ]
    lowered = text.lower()
    for pattern in patterns:
        match = re.search(pattern, lowered, flags=re.I)
        if match:
            raw = match.group(0)
            return (
                raw.replace("qwen", "Qwen")
                .replace("gemma", "Gemma")
                .replace("codex", "Codex")
                .replace("vllm", "VLLM")
                .replace("llama.cpp", "llama.cpp")
            )
    entity = clean_text(fallback).split("/", 1)[0].strip()
    return entity or "AI 模型"


def community_observation_summary(card: EvidenceCard, display_title: str = "") -> dict[str, str | bool] | None:
    if not _is_community_observation(card):
        return None
    official_personnel = official_personnel_signal_summary(card, display_title)
    if official_personnel:
        return official_personnel
    source = _short_source(_source_name(card))
    title_raw = clean_text(display_title or card.event_title)
    summary = _source_summary(card)
    text = clean_text(" ".join([title_raw, summary, " ".join(card.key_facts)])).lower()
    model = _model_hint(text, card.entity)
    followup = _soft_limit(_first_uncertainty(card) or "需要更多硬件环境、复现实测或官方说明交叉确认。", CARD_BODY_LIMIT)

    if "vllm" in text and any(k in text for k in ["benchmark", "performance", "throughput", "latency", "bf16", "fp8", "nvfp4"]):
        detail = "原帖比较 BF16、FP8、NVFP4 等配置下的推理表现。"
        if "fp8 seems to be the right choice" in text:
            detail = "发帖者认为 FP8 更折中；NVFP4 虽快，但提到循环和回答变浅的问题。"
        return {
            "focused": True,
            "title": f"社区实测 {model} 的 VLLM 推理表现",
            "lead": _soft_limit(f"{source} 社区实测 {model} 在 VLLM 下的性能测试；只代表该用户环境。", LEAD_LIMIT),
            "fact": _soft_limit(detail, CARD_BODY_LIMIT),
            "detail": _soft_limit(detail, CARD_BODY_LIMIT),
            "impact": "对本地部署和编码 agent 用户，价值在于选择精度、量化和推理后端。",
            "discussion": "这不是官方 benchmark，不能直接推出模型整体性能变化。",
            "followup": followup,
        }

    if any(k in text for k in ["100k context", "context", "vram"]) and any(k in text for k in ["32gb", "q8", "qwen"]):
        detail = f"原帖围绕 {model}、Q8 和 32GB 显存，尝试接近 100K 上下文。"
        return {
            "focused": True,
            "title": "社区尝试在 32GB 显存上跑近 100K 上下文",
            "lead": _soft_limit(f"{source} 出现一条本地长上下文尝试；核心是显存、量化和上下文长度的取舍。", LEAD_LIMIT),
            "fact": _soft_limit(detail, CARD_BODY_LIMIT),
            "detail": _soft_limit(detail, CARD_BODY_LIMIT),
            "impact": "对本地模型用户，参考点是长上下文能跑到哪里、代价有多大。",
            "discussion": "单个配置不能代表普遍体验，尤其要看显存占用、速度和稳定性。",
            "followup": followup,
        }

    if "geniex" in text or ("qualcomm" in text and "windows" in text):
        detail = "原帖称高通 GenieX 可在 Windows 笔记本上调度 CPU、GPU 或 NPU 跑本地模型。"
        return {
            "focused": True,
            "title": "社区关注高通 GenieX 的本地 LLM 运行体验",
            "lead": _soft_limit(f"{source} 用户分享 GenieX 运行本地模型的体验；仍按社区反馈处理。", LEAD_LIMIT),
            "fact": _soft_limit(detail, CARD_BODY_LIMIT),
            "detail": _soft_limit(detail, CARD_BODY_LIMIT),
            "impact": "它关系到普通 Windows 笔记本能否更顺地跑本地大模型。",
            "discussion": "关键要看更多机型、驱动版本和模型配置能否复现。",
            "followup": followup,
        }

    if "65k" in text or "128k" in text or "agentic workloads" in text:
        detail = "原帖把多款模型放到 65K 到 128K 长上下文场景中比较。"
        return {
            "focused": True,
            "title": "社区长上下文实测指向 agent 长任务瓶颈",
            "lead": _soft_limit(f"{source} 出现长上下文 benchmark；先当作社区实测线索。", LEAD_LIMIT),
            "fact": _soft_limit(detail, CARD_BODY_LIMIT),
            "detail": _soft_limit(detail, CARD_BODY_LIMIT),
            "impact": "对 RAG、代码 agent 和工具调用场景，prefill 成本可能比生成速度更关键。",
            "discussion": "这类测试强依赖硬件、后端和参数，不能直接等同于所有用户体验。",
            "followup": followup,
        }

    title = f"{source} 出现关于 {clean_text(card.entity) or 'AI'} 的社区观察"
    fact = "原帖标题为英文社区讨论，暂不把它改写成确定新闻。" if _looks_raw_english(title_raw) else title_raw or "社区源头出现新的 AI 相关讨论。"
    return {
        "focused": True,
        "title": _soft_limit(title, 64),
        "lead": _soft_limit(f"{source} 有单源社区线索；只保留可核对事实，不写成官方结论。", LEAD_LIMIT),
        "fact": _soft_limit(fact, CARD_BODY_LIMIT),
        "detail": _soft_limit(fact, CARD_BODY_LIMIT),
        "impact": "它只能作为观察信号，价值取决于更多复现或官方回应。",
        "discussion": "先把用户反馈和官方结论分开，避免把个例说成趋势。",
        "followup": followup,
    }


def media_observation_summary(card: EvidenceCard, display_title: str = "") -> dict[str, str | bool] | None:
    if card.media_count <= 0 or card.official_count > 0:
        return None
    source = _short_source(_source_name(card))
    title_raw = _clean_news_fragment(display_title or card.event_title)
    summary = _source_summary(card)
    text = clean_text(" ".join([title_raw, summary, " ".join(card.key_facts)])).lower()
    followup = _soft_limit(_first_uncertainty(card) or "等官方说明、更多媒体和实际反馈交叉验证。", CARD_BODY_LIMIT)

    def payload(title: str, fact: str, impact: str, discussion: str) -> dict[str, str | bool]:
        return {
            "focused": True,
            "title": _soft_limit(title, 64),
            "lead": _soft_limit(f"{source}提到{title}；目前按媒体报道处理。", LEAD_LIMIT),
            "fact": _soft_limit(fact, CARD_BODY_LIMIT),
            "detail": _soft_limit(fact, CARD_BODY_LIMIT),
            "impact": _soft_limit(impact, CARD_BODY_LIMIT),
            "discussion": _soft_limit(discussion, CARD_BODY_LIMIT),
            "followup": followup,
        }

    if "codex" in text and "token" in text:
        fact = "媒体测试 Codex 省 Token 方法，结论是能省，但幅度有限。"
        return payload(
            "Codex 省 Token 方法被媒体实测",
            fact,
            "对开发者的价值在于控制调用成本，但不能写成官方降价。",
            "先小范围试用；确认输出质量不下降，再放进日常流程。",
        )

    if "deepseek" in text and ("招聘帖" in text or "填志愿" in text):
        fact = "媒体借 DeepSeek 招聘帖讨论 AI 人才方向和职业选择。"
        return payload(
            "DeepSeek 招聘帖引出 AI 人才方向讨论",
            fact,
            "它更像人才和行业观察，不是 DeepSeek 产品发布。",
            "适合关注 AI 岗位变化的人看，但结论要回到招聘原文。",
        )

    if "claude code" in text and "spotify" in text and ("73%" in text or "2900" in text):
        fact = "InfoQ 标题提到 Spotify 工程团队访谈：AI 生成 PR 占比、部署频率和 ROI 口径成为焦点。"
        return payload(
            "Spotify 团队谈 Claude Code 落地",
            fact,
            "这类案例能说明 AI 编程工具进入大团队后，重点会转向评审、部署和成本核算。",
            "它是媒体访谈线索，数字口径和适用范围仍要回到原文逐项核对。",
        )

    if (source.lower().startswith("infoq") or "周报" in title_raw) and "豆包" in text and "千问" in text and ("关停智能体" in text or "下线智能体" in text):
        fact = "InfoQ 周报提到豆包、千问将调整智能体功能，并汇总企业限制 AI 工具的线索。"
        return payload(
            "豆包、千问智能体功能调整被媒体周报提及",
            fact,
            "普通用户要看智能体入口是否调整；企业侧还要看工具权限和研发额度。",
            "周报里混有媒体线索和传闻，必须回到原文逐条核对。",
        )

    if "阿里" in text and "禁用" in text and "claude" in text:
        focused_title = _best_scored_fragment(_title_fragments(card, title_raw), _entity_terms(card))
        focused_title = _soft_limit(focused_title or title_raw or "阿里禁用 Claude 相关工具", 64)
        if "claude code" in text and "全面禁用" in text:
            focused_title = "阿里内部全面禁用Claude Code"
        fact = "媒体称阿里限制 Claude 相关工具使用；原因指向代码安全和数据合规，暂未看到官方确认。"
        return payload(
            focused_title,
            fact,
            "影响主要在企业代码安全、数据合规和开发工具选型。",
            "核心不是某个工具突然不能用，而是企业开始划 AI 工具权限边界。",
        )

    if "waic" in text and "世界模型" in text:
        fact = "量子位报道 WAIC 2026 上关于 VLA、世界模型和具身路线的讨论。"
        return payload(
            "WAIC 2026 讨论世界模型路线",
            fact,
            "这关系到具身智能接下来押注感知、规划还是交互闭环。",
            "它是论坛观点和路线判断，不等同于单个产品发布。",
        )

    if "waic" in text and ("智能体" in text or "scaling" in text):
        fact = "量子位报道 WAIC 2026 上关于后 Scaling、模型和智能体生产力的讨论。"
        return payload(
            "WAIC 2026 聚焦模型与智能体生产力",
            fact,
            "重点在大模型能力如何进入可交付的产品和应用场景。",
            "还要看是否有产品、案例和真实部署数据支撑。",
        )

    return None


def _model_name(title: str) -> str:
    text = clean_text(title)
    if "：" in text:
        text = text.split("：", 1)[1]
    elif ":" in text:
        text = text.split(":", 1)[1]
    if "/" in text and " / " not in text and len(text) < 90:
        text = text.split("/", 1)[-1]
    text = text.replace("（", "(").replace("）", ")").replace("Flash / Pro", "Flash/Pro")
    variant_match = re.search(r"(DeepSeek[-A-Za-z0-9_.]+).*?(Flash/Pro)", text, flags=re.I)
    if variant_match:
        return f"{variant_match.group(1)} {variant_match.group(2)}"
    return clean_text(text).strip(" -｜|:：") or "这条动态"


def _fact_value(card: EvidenceCard, label: str) -> str:
    prefix = f"{label}："
    alt_prefix = f"{label}:"
    for fact in card.key_facts:
        text = clean_text(fact)
        if text.startswith(prefix):
            return clean_text(text[len(prefix) :])
        if text.startswith(alt_prefix):
            return clean_text(text[len(alt_prefix) :])
    return ""


def _short_source(source: str) -> str:
    text = clean_text(source)
    replacements = {
        "Hugging Face Models": "Hugging Face",
        "Google Hugging Face Models": "Google/Hugging Face",
        "GitHub Releases": "GitHub",
    }
    if text.endswith(" Hugging Face Models"):
        org = text[: -len(" Hugging Face Models")].strip()
        return f"{org}/Hugging Face" if org else "Hugging Face"
    return replacements.get(text, text or "资料来源")


def _published_time_cn(card: EvidenceCard) -> str:
    raw = _fact_value(card, "首要来源发布时间")
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})", raw)
    if match:
        _, month, day, hour, minute = match.groups()
        return f"{month}-{day} {hour}:{minute} UTC"
    if card.latest_published_at:
        return card.latest_published_at.strftime("%m-%d %H:%M UTC")
    return ""


def _source_summary(card: EvidenceCard) -> str:
    raw = _fact_value(card, "来源摘要")
    if not raw:
        return ""
    raw = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", raw)
    raw = re.sub(r"\[([^\]]*)\]\(https?://[^)]*\)", r"\1", raw)
    raw = re.sub(r"https?://\S+", "", raw)
    raw = clean_text(raw).strip(" ，。；;()[]")
    raw_context = clean_text(" ".join([card.event_title, card.entity, _source_name(card), raw])).lower()
    if _looks_raw_english(raw):
        if "nvidia" in raw_context and any(k in raw_context for k in ["federated learning", "nvflare", "flare auto-fl", "auto-fl"]):
            return "NVIDIA 介绍 FLARE Auto-FL，用 AI Agent 辅助联邦学习实验。"
        if "nvidia" in raw_context and "blackwell" in raw_context and "dflash" in raw_context:
            return "NVIDIA 介绍 Blackwell 上的 DFlash 推理加速方案。"
        if "nvidia" in raw_context:
            return "NVIDIA 技术博客记录了一项 AI 开发者技术更新。"
    pipeline = ""
    tags = ""
    downloads = ""
    likes = ""
    files = ""
    pipeline_match = re.search(r"pipeline\s*=\s*([^；;]+)", raw, flags=re.I)
    tags_match = re.search(r"tags\s*=\s*([^；;]+)", raw, flags=re.I)
    downloads_match = re.search(r"downloads\s*=\s*([^；;]+)", raw, flags=re.I)
    likes_match = re.search(r"likes\s*=\s*([^；;]+)", raw, flags=re.I)
    files_match = re.search(r"files\s*=\s*([^；;]+)", raw, flags=re.I)
    if pipeline_match:
        pipeline = clean_text(pipeline_match.group(1)).strip(" ,，")
    if tags_match:
        tags = clean_text(tags_match.group(1)).strip(" ,，").replace(", ", "、").replace(",", "、")
    if downloads_match:
        downloads = clean_text(downloads_match.group(1)).strip(" ,，")
    if likes_match:
        likes = clean_text(likes_match.group(1)).strip(" ,，")
    if files_match:
        files = clean_text(files_match.group(1)).strip(" ,，").replace(", ", "、").replace(",", "、")
    parts: list[str] = []
    if pipeline:
        parts.append(f"任务类型标为 {pipeline}")
    if tags:
        parts.append(f"标签包含 {tags}")
    if files:
        parts.append(f"文件列表含 {files}")
    if downloads:
        parts.append(f"下载数记录为 {downloads}")
    if likes:
        parts.append(f"点赞数记录为 {likes}")
    return "，".join(parts) if parts else raw


PERFORMANCE_META_ONLY_PHRASES = [
    "性能榜单要看测试口径",
    "具体结论要看测试口径",
    "性能或榜单对比",
    "后续复现",
    "首要来源发布时间",
]


def official_performance_summary(card: EvidenceCard) -> str:
    if card.official_count <= 0:
        return ""
    raw = clean_text(" ".join([_source_summary(card), " ".join(card.key_facts)]))
    if not raw:
        return ""
    lowered = raw.lower()
    if any(phrase.lower() in lowered for phrase in PERFORMANCE_META_ONLY_PHRASES) and not re.search(
        r"(?:AIME|GPQA|MMLU|LiveCodeBench|SWE-bench|HumanEval|MMMU|MathVista|BFCL)[^0-9%]{0,18}\d+(?:\.\d+)?%?|\d+(?:\.\d+)?\s*(?:%|倍|x\b)",
        raw,
        flags=re.I,
    ):
        return ""
    if not any(keyword.lower() in lowered for keyword in PERFORMANCE_KEYWORDS) and not any(name.lower() in lowered for name in BENCHMARK_NAMES):
        return ""
    chunks = []
    for part in re.split(r"[；;\n。！？!?]+", raw):
        text = clean_text(part).strip(" ，,：:")
        if len(text) < 8:
            continue
        part_lower = text.lower()
        if any(keyword.lower() in part_lower for keyword in PERFORMANCE_KEYWORDS) or any(name.lower() in part_lower for name in BENCHMARK_NAMES):
            chunks.append(text)
    source = chunks[0] if chunks else raw
    names = []
    for name in BENCHMARK_NAMES:
        if re.search(re.escape(name), source, flags=re.I) and name not in names:
            names.append(name)
    metric_bits = []
    metric_re = re.compile(
        r"(AIME(?:\s*20\d{2})?|GPQA(?:\s*Diamond)?|MMLU(?:-Pro)?|LiveCodeBench|SWE-bench|HumanEval|MBPP|GSM8K|MMMU|MathVista|BFCL)[^0-9%]{0,18}([0-9]+(?:\.[0-9]+)?%?)",
        flags=re.I,
    )
    for match in metric_re.finditer(source):
        label = clean_text(match.group(1))
        value = clean_text(match.group(2))
        if label.lower().startswith("aime") and value in {"2024", "2025", "2026"}:
            continue
        bit = f"{label} {value}"
        if bit not in metric_bits:
            metric_bits.append(bit)
        if len(metric_bits) >= 3:
            break
    if metric_bits:
        return _soft_limit("官方性能说明列出：" + "、".join(metric_bits), CARD_BODY_LIMIT)
    if names:
        compact_names = "/".join(name.split()[0] if name.lower().startswith("gpqa") else name for name in names[:4])
        return _soft_limit(f"{compact_names} 等评测对比提升", CARD_BODY_LIMIT)
    if _has_cjk(source):
        return _soft_limit(source, CARD_BODY_LIMIT)
    if any(word in lowered for word in ["outperform", "state-of-the-art", "sota"]):
        return "官方称部分评测表现领先，需结合测试口径看。"
    return "官方资料提到性能或榜单对比，具体结论要看测试口径。"


def official_performance_caution(card: EvidenceCard) -> str:
    if not official_performance_summary(card):
        return ""
    return "性能榜单要看测试口径、对手版本、上下文长度和推理成本。"


def _quality_text(card: EvidenceCard) -> str:
    evidence_text = " ".join(
        [
            str(e.get("source") or "")
            + " "
            + str(e.get("title") or "")
            + " "
            + str(e.get("url") or "")
            for e in card.evidence_links
        ]
    )
    return clean_text(
        " ".join(
            [
                card.event_title,
                card.entity,
                card.reason,
                *card.key_facts,
                *card.uncertainty,
                evidence_text,
            ]
        )
    )


def _contains_any(text: str, keywords: list[str]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def _has_numbers_or_versions(text: str) -> bool:
    return bool(re.search(r"\bv?\d+(?:\.\d+){1,4}[A-Za-z0-9_.-]*\b|\d+(?:\.\d+)?\s*(?:%|k|K|M|B|万|亿|分|项|个|次|小时|tokens?)", text))


def _has_captured_required_screenshot(card: EvidenceCard) -> bool:
    for evidence in card.evidence_links:
        required = str(evidence.get("screenshot_required") or "").lower() == "true"
        if not required:
            continue
        if str(evidence.get("screenshot_status") or "") == "captured" and evidence.get("screenshot_path"):
            return True
        return False
    return True


def content_quality_profile(card: EvidenceCard) -> dict[str, Any]:
    """Background-only score for selection/debugging; never shown as a public module."""
    text = _quality_text(card)
    evidence_score = 8
    if card.official_count > 0:
        evidence_score += 30
    if card.media_count > 0:
        evidence_score += 16
    if card.community_count > 0:
        evidence_score += 7
    evidence_score += min(10, max(0, card.source_count - 1) * 5)
    if card.risk == "green":
        evidence_score += 8
    elif card.risk == "red":
        evidence_score -= 8
    if not _has_captured_required_screenshot(card):
        evidence_score -= 20

    relevance_score = 10
    if _contains_any(text, MAINSTREAM_AI_ENTITIES):
        relevance_score += 22
    if _contains_any(text, STRONG_AI_RELEVANCE_KEYWORDS):
        relevance_score += 16
    elif _contains_any(text, AI_NEWS_KEYWORDS):
        relevance_score += 8
    if _contains_any(text, ["融资", "投资", "机器人", "具身智能"]) and not _contains_any(text, MAINSTREAM_AI_ENTITIES):
        relevance_score -= 8

    specificity_score = 8
    if _has_numbers_or_versions(text):
        specificity_score += 16
    if _contains_any(text, SPECIFICITY_KEYWORDS):
        specificity_score += 14
    if official_performance_summary(card):
        specificity_score += 16
    if _source_summary(card):
        specificity_score += 8
    if len([fact for fact in card.key_facts if clean_text(fact)]) >= 4:
        specificity_score += 5

    story_score = 8
    if _contains_any(text, NEWS_ACTION_KEYWORDS):
        story_score += 14
    if _is_model_repo_update(card) or _is_github_release(card) or _is_paper(card):
        story_score += 10
    if focused_news_summary(card, card.event_title).get("focused"):
        story_score += 10
    if _looks_raw_english(card.event_title):
        story_score -= 12
    if any(phrase in text for phrase in INTERNAL_QUALITY_PHRASES):
        story_score -= 16

    total = round(
        min(100, max(0, evidence_score) * 0.34 + max(0, relevance_score) * 0.28 + max(0, specificity_score) * 0.24 + max(0, story_score) * 0.14)
    )
    notes: list[str] = []
    if card.official_count > 0:
        notes.append("official")
    elif card.media_count > 0:
        notes.append("media")
    elif card.community_count > 0:
        notes.append("community")
    if official_performance_summary(card):
        notes.append("performance")
    if _has_numbers_or_versions(text):
        notes.append("specific")
    if not _has_captured_required_screenshot(card):
        notes.append("missing_required_screenshot")
    if card.risk == "yellow":
        notes.append("needs_confirmation")

    return {
        "score": int(total),
        "evidence": int(min(100, max(0, evidence_score))),
        "relevance": int(min(100, max(0, relevance_score))),
        "specificity": int(min(100, max(0, specificity_score))),
        "story": int(min(100, max(0, story_score))),
        "notes": notes,
    }


def content_quality_score(card: EvidenceCard) -> int:
    return int(content_quality_profile(card)["score"])


def _top_entities_for_intro(cards: list[EvidenceCard], limit: int = 3) -> list[str]:
    names: list[str] = []
    for card in cards:
        raw = card_display_entity(card)
        if not raw or raw.lower() in {"ai", "人工智能"}:
            continue
        name = raw.split("/", 1)[0].strip()
        if name and name not in names:
            names.append(name)
        if len(names) >= limit:
            break
    return names


def daily_trend_cards(cards: list[EvidenceCard]) -> list[dict[str, str]]:
    if not cards:
        return [
            {"icon": "◇", "title": "今日主线", "body": "今天先等可靠来源更新，再把模型、产品和工具动态讲清楚。"},
            {"icon": "✓", "title": "确定消息", "body": "优先看官方公告和可核对来源，单源线索先不说满。"},
        ]
    model_count = sum(1 for card in cards if _is_model_repo_update(card) or "模型" in _quality_text(card))
    tool_count = sum(1 for card in cards if _contains_any(_quality_text(card), ["github", "api", "sdk", "codex", "vllm", "agent", "开发"]))
    product_count = sum(1 for card in cards if _contains_any(_quality_text(card), ["app", "客户端", "产品", "chatgpt", "claude", "gemini", "搜索"]))
    performance_count = sum(1 for card in cards if official_performance_summary(card) or _contains_any(_quality_text(card), ["benchmark", "评测", "榜单", "性能"]))
    yellow_count = sum(1 for card in cards if card.risk == "yellow")
    official_count = sum(1 for card in cards if card.official_count > 0)
    entities = _top_entities_for_intro(cards)

    if model_count >= max(tool_count, product_count):
        main = "今天先看模型能力、开放入口和使用门槛。"
    elif tool_count >= product_count:
        main = "今天开发者工具和模型调用侧更活跃。"
    else:
        main = "今天产品体验侧更值得看。"

    cards_out = [
        {"icon": "◇", "title": "今日主线", "body": _soft_limit(main, 58)},
        {
            "icon": "✦",
            "title": "重点对象",
            "body": _soft_limit("、".join(entities) + " 等更新进入今天早报。" if entities else "今天重点看模型、工具和产品变化。", 58),
        },
    ]
    if performance_count:
        cards_out.append(
            {
                "icon": "▦",
                "title": "性能线索",
                "body": _soft_limit(f"{performance_count} 条涉及榜单、评测或性能，关键在具体指标和测试口径。", 58),
            }
        )
    else:
        cards_out.append(
            {
                "icon": "▣",
                "title": "具体变化",
                "body": "发布内容、能力变化和实际入口会放在前面。",
            }
        )
    cards_out.append(
        {
            "icon": "✓" if yellow_count == 0 else "◌",
            "title": "确定消息" if yellow_count == 0 else "待确认",
            "body": _soft_limit(
                f"{official_count} 条有官方来源；{yellow_count} 条按媒体线索谨慎表述。"
                if yellow_count
                else f"{official_count} 条有官方来源，优先讲已能核对的变化。",
                58,
            ),
        }
    )
    return cards_out[:4]


def github_release_change_parts(card: EvidenceCard) -> dict[str, str]:
    raw = _source_summary(card)
    if not raw:
        return {}
    compare = ""
    changes = ""
    source = ""
    match = re.search(r"版本对比[:：]\s*([^；;。]+)", raw)
    if match:
        compare = clean_text(match.group(1))
    match = re.search(r"变更摘要[:：]\s*(.+?)(?:[；;]修复/维护线索[:：]|[；;]变更来源[:：]|[；;]Release 说明[:：]|$)", raw)
    if match:
        changes = clean_text(match.group(1).replace("release 元数据", "release 元数据").replace("release 的", "release 的"))
    maintenance = ""
    match = re.search(r"修复/维护线索[:：]\s*(.+?)(?:[；;]变更来源[:：]|[；;]Release 说明[:：]|$)", raw)
    if match:
        maintenance = clean_text(match.group(1))
    match = re.search(r"变更来源[:：]\s*(https?://\S+)", raw)
    if match:
        source = match.group(1).strip()
    if not compare and not changes and not maintenance:
        return {}
    return {"compare": compare, "changes": changes, "maintenance": maintenance, "source": source}


def _github_release_compare_text(compare: str) -> str:
    text = clean_text(compare)
    text = re.sub(r"^较\s+", "较上一版 ", text)
    text = text.replace("GitHub compare 显示 ", "")
    text = text.replace(",GitHub compare 显示", "：")
    text = text.replace("，GitHub compare 显示", "：")
    counts = re.search(r"(\d+\s*个提交).*?(\d+\s*个文件变更)", text)
    if counts:
        return f"较上一版：{counts.group(1)} / {counts.group(2)}"
    return text


def _release_list_items(text: str) -> list[str]:
    items: list[str] = []
    for raw in re.split(r"[；;]+", clean_text(text)):
        item = raw.strip(" ，。")
        if item and item not in items:
            items.append(item)
    return items


def _release_fallback_summary(text: str) -> str:
    raw = clean_text(text or "")
    if not raw:
        return "只确认版本号出现，具体功能、性能或修复点待确认。"
    lowered = raw.lower()
    points: list[str] = []
    if "docker" in lowered and ("fix" in lowered or "fixes" in lowered or "backport" in lowered):
        points.append("包含 Docker 相关修复回合并。")
    if "hubapi" in lowered and "session" in lowered:
        points.append("HubApi 新增 session 属性。")
    if "release workflow" in lowered and "depend" in lowered:
        points.append("发布流程的 hub 依赖有修复。")
    if "install" in lowered and ("test" in lowered or "script" in lowered):
        points.append("补充安装脚本相关维护。")
    if points:
        return _soft_limit("；".join(point.rstrip("。") for point in points), CARD_BODY_LIMIT)
    if _looks_raw_english(raw):
        return "发布说明提到若干代码维护项，具体影响待确认。"
    return raw


def _github_release_point_text(parts: dict[str, str], fallback: str) -> str:
    changes = clean_text(parts.get("changes", ""))
    maintenance = clean_text(parts.get("maintenance", ""))
    change_items = _release_list_items(changes)
    maintenance_items = [item for item in _release_list_items(maintenance) if item not in change_items[:1]]
    candidates: list[str] = []
    if change_items:
        candidates.append(change_items[0])
    if change_items and maintenance_items:
        candidates.append(f"更新：{change_items[0]}；维护：{maintenance_items[0]}")
    if changes:
        candidates.append("更新：" + changes)
    if maintenance:
        candidates.append("维护：" + maintenance)
    candidates.append(_release_fallback_summary(fallback))
    return _fit_text(candidates, CARD_BODY_LIMIT)


def _github_release_impact(parts: dict[str, str]) -> str:
    text = f"{parts.get('compare','')} {parts.get('changes','')} {parts.get('maintenance','')}".lower()
    if any(k in text for k in ["install", "安装", "asset", "元数据", "checksum", "digest"]):
        return "主要影响安装和更新是否更稳定，普通用户可等工具提示。"
    if any(k in text for k in ["plugin", "插件"]):
        return "主要影响插件信息展示，普通用户只需等客户端更新。"
    if any(k in text for k in ["config", "schema", "协议", "配置"]):
        return "主要影响配置和部署细节，普通用户感知不强。"
    return "若只是维护版，对普通用户影响有限；重点看功能或性能是否变化。"


def _variant_labels(card: EvidenceCard) -> list[str]:
    raw = " ".join(
        [
            card.event_title,
            " ".join(card.key_facts),
            " ".join(str(e.get("title") or "") for e in card.evidence_links),
            " ".join(str(e.get("url") or "") for e in card.evidence_links),
        ]
    )
    labels: list[str] = []
    for token in re.split(r"[^A-Za-z0-9]+", raw):
        label = MODEL_VARIANT_LABELS.get(token.lower())
        if label and label not in labels:
            labels.append(label)
    return labels


def _strip_variant_suffix(name: str) -> str:
    text = clean_text(name)
    return re.sub(r"\s*[（(][^）)]*[）)]\s*$", "", text).strip() or text


def _model_repo_point(card: EvidenceCard, summary: str) -> str:
    variants = _variant_labels(card)
    candidates: list[str] = []
    if len(variants) > 1:
        candidates.append(f"同一系列同时出现 {' / '.join(variants)} 变体。")
    if summary:
        candidates.append(summary)
    candidates.append("模型页更新只能确认条目变化，不能推出已开源或可直接使用。")
    return _fit_text(candidates, CARD_BODY_LIMIT)


def _model_repo_followup(card: EvidenceCard) -> str:
    uncertainty = _first_uncertainty(card)
    if uncertainty:
        return _soft_limit(uncertainty, CARD_BODY_LIMIT)
    return "只核对模型卡、文件列表、许可和下载方式。"


def _first_specific_fact(card: EvidenceCard, fallback: str) -> str:
    skip_prefixes = ("首要来源发布时间", "首要来源", "来源摘要")
    for fact in card.key_facts:
        text = clean_text(fact)
        if not text or text.startswith(skip_prefixes):
            continue
        text = re.sub(r"^[^：:]{1,16}[：:]\s*", "", text).strip()
        if _looks_raw_english(text):
            continue
        if text:
            return text
    return fallback


def _compact_title(title: str) -> str:
    return _soft_limit(clean_text(title), CARD_TITLE_LIMIT)


def _compact_body(body: str, limit: int = CARD_BODY_LIMIT) -> str:
    text = clean_text(body)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]*)\]\(https?://[^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"^(?:" + "|".join(re.escape(x) for x in BANNED_CARD_TITLES) + r")\s*[:：]\s*", "", text)
    text = text.replace("；目前按媒体报道处理。", "")
    text = text.replace("；按已核对来源处理。", "")
    text = text.strip(" ，。；;()[]")
    return _soft_limit(text, limit)


def _card(icon: str, title: str, body: str, component: str = "") -> dict[str, str]:
    title = _compact_title(title)
    body = _compact_body(body)
    return {
        "icon": icon or CARD_ICONS.get(title, "·"),
        "title": title,
        "body": body,
        "content": body,
        "component": component or title,
    }


DESIGN_THEME = {
    "model_release": ("模型发布", "blue", "🧠"),
    "model_update": ("模型更新", "blue", "🧠"),
    "product_update": ("产品更新", "cyan", "📦"),
    "github_release": ("GitHub", "orange", "⚙️"),
    "model_repository": ("模型仓库", "blue", "🧠"),
    "benchmark": ("Benchmark", "green", "📈"),
    "paper": ("论文", "gray", "📄"),
    "industry": ("行业新闻", "purple", "🏢"),
    "hiring_funding": ("招聘融资", "yellow", "👥"),
    "developer_tool": ("开发工具", "orange", "⚙️"),
    "community": ("社区反馈", "green", "📈"),
    "official_personnel_signal": ("X 官方人员动态", "purple", "👥"),
    "media": ("媒体线索", "purple", "🏢"),
}


def _design_news_type(card: EvidenceCard, display_title: str, news: dict[str, str | bool] | None = None) -> str:
    text = clean_text(
        " ".join(
            [
                display_title,
                card.event_title,
                card.entity,
                " ".join(card.key_facts),
                " ".join(str(e.get("source") or "") for e in card.evidence_links),
                " ".join(str(e.get("url") or "") for e in card.evidence_links),
            ]
        )
    ).lower()
    headline_text = clean_text(" ".join([display_title, card.event_title, card.entity])).lower()
    if card_is_official_personnel_signal(card):
        return "official_personnel_signal"
    if card.community_count > 0 and card.official_count == 0:
        return "community"
    if _is_github_release(card):
        return "github_release"
    if _is_model_repo_update(card):
        return "model_repository"
    if _is_paper(card):
        return "paper"
    if any(k in text for k in ["招聘", "岗位", "人才", "融资", "投资"]):
        return "hiring_funding"
    release_blocked = any(k in headline_text for k in ["禁用", "限制", "招聘", "访谈", "讨论", "解说"])
    if not release_blocked and any(k in headline_text for k in ["发布", "推出", "上线", "正式版"]):
        return "model_release"
    if any(k in text for k in ["claude code", "codex", "vllm", "代码安全", "数据合规", "禁用", "部署", " pr ", "生成 pr"]):
        return "developer_tool"
    if any(k in text for k in ["chatgpt", "claude", "gemini", "cursor", "copilot", "perplexity", "notion ai", "app", "客户端", "产品"]):
        return "product_update"
    if any(k in text for k in ["升级", "更新", "多模态", "语音", "视频", "上下文", "推理"]):
        return "model_update"
    if any(k in text for k in ["benchmark", "评测", "榜单", "跑分", "性能", "aime", "gpqa", "swe-bench", "livecodebench", "blackwell"]):
        return "benchmark"
    if any(k in text for k in ["api", "sdk", "github", "vllm", "codex", "agent", "工具链", "开发者"]):
        return "developer_tool"
    if card.media_count > 0:
        return "media"
    return "industry"


def _editor_stars(card: EvidenceCard, news_type: str) -> int:
    if news_type in {"model_release", "product_update"}:
        return 5 if card.official_count > 0 else 4
    if news_type == "github_release":
        return 4
    if news_type in {"benchmark", "developer_tool"}:
        return 4 if card.score >= 70 or card.official_count > 0 else 3
    if news_type == "model_repository":
        return 3
    if news_type == "official_personnel_signal":
        return 2
    if news_type == "community":
        return 3 if card.score >= 60 else 2
    if news_type == "hiring_funding":
        return 3
    if news_type in {"industry", "media"}:
        return 3 if card.score >= 55 else 2
    if news_type in {"paper", "community"}:
        return 2 if card.official_count == 0 else 3
    return 3


def _card_count_for_stars(stars: int) -> int:
    return {5: 5, 4: 4, 3: 3, 2: 2, 1: 1}.get(max(1, min(5, stars)), 3)


def _rating_text(stars: int) -> str:
    return "★" * stars + "☆" * (5 - stars)


def _editor_comment(stars: int, news_type: str) -> str:
    if stars >= 5:
        return "推荐立即体验"
    if stars == 4:
        return "开发者关注" if news_type in {"github_release", "benchmark", "developer_tool"} else "优先了解"
    if stars == 3:
        return "普通用户可了解"
    if stars == 2:
        return "快速了解即可" if news_type == "official_personnel_signal" else "可暂时观望"
    return "快速了解即可"


def _design_package_meta(card: EvidenceCard, display_title: str, news_type: str) -> dict[str, Any]:
    theme, color, icon = DESIGN_THEME.get(news_type, DESIGN_THEME["media"])
    stars = _editor_stars(card, news_type)
    rating = _rating_text(stars)
    comment = _editor_comment(stars, news_type)
    return {
        "card_count": _card_count_for_stars(stars),
        "theme": theme,
        "color": color,
        "icon": icon,
        "editor_rating": rating,
        "editor_comment": comment,
        "animation": [
            "Logo 缩放进入",
            "重点区域高亮",
            "信息卡依次浮入",
        ],
        "news_type": news_type,
    }


def _first_match(patterns: list[str], text: str) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return clean_text(match.group(1))
    return ""


def _model_param_hint(card: EvidenceCard, display_title: str) -> str:
    raw = clean_text(" ".join([display_title, card.event_title, *card.key_facts]))
    values: list[str] = []
    for pattern in [r"\b(\d+(?:\.\d+)?\s*[BMK])\b", r"\b(A\d+B)\b", r"\b(\d+\s*[kK]\s*上下文)\b", r"\b(\d+\s*bit)\b"]:
        for match in re.finditer(pattern, raw, flags=re.I):
            value = clean_text(match.group(1)).replace(" ", "")
            if value and value not in values:
                values.append(value)
    return " / ".join(values[:3]) if values else "参数未公开"


def _license_hint(card: EvidenceCard) -> str:
    raw = clean_text(" ".join(card.key_facts))
    value = _first_match([r"license\s*=\s*([^；;，,]+)", r"许可[：:]\s*([^；;，,]+)"], raw)
    return value or "看模型卡"


def _task_hint(card: EvidenceCard, summary: str) -> str:
    raw = clean_text(" ".join([summary, *card.key_facts]))
    pipeline = _first_match([r"任务类型标为\s*([^，。；;]+)", r"pipeline\s*=\s*([^；;，,]+)"], raw)
    if pipeline:
        return pipeline
    if "语音" in raw or "asr" in raw.lower():
        return "实时语音识别"
    if "推理" in raw or "inference" in raw.lower():
        return "推理加速"
    return "看模型卡"


def _availability_hint(card: EvidenceCard, news_type: str) -> str:
    raw = clean_text(" ".join([card.event_title, *card.key_facts, *[str(e.get("title") or "") for e in card.evidence_links]])).lower()
    if news_type == "model_repository":
        return "仅确认页面更新"
    if "tokenhub" in raw:
        return "API 已上腾讯云 TokenHub"
    if "元宝" in raw and ("agent" in raw or "免费" in raw):
        return "元宝 Hy3 Agent 免费开放"
    if card.official_count > 0:
        return "官方已发布"
    if card.media_count > 0:
        return "入口待确认"
    return "仅作线索"


CONCRETE_MODEL_DETAIL_KEYWORDS = [
    "api",
    "tokenhub",
    "agent",
    "元宝",
    "腾讯云",
    "workbuddy",
    "codebuddy",
    "marvis",
    "ima",
    "免费",
    "价格",
    "preview",
    "上下文",
    "多模态",
    "语音",
    "视频",
    "benchmark",
    "aime",
    "gpqa",
    "livecodebench",
    "swe-bench",
]


def _concrete_model_detail(card: EvidenceCard, *preferred: str) -> str:
    raw_all = clean_text(" ".join([card.event_title, *preferred, *card.key_facts, *[str(e.get("title") or "") for e in card.evidence_links]])).lower()
    if "hy3" in raw_all and ("workbuddy" in raw_all or "codebuddy" in raw_all or "元宝" in raw_all or "tokenhub" in raw_all):
        if "workbuddy" in raw_all or "codebuddy" in raw_all:
            return "元宝 Agent，WorkBuddy/CodeBuddy 接入"
        if "元宝" in raw_all and "agent" in raw_all:
            return "元宝上线 Hy3 Agent 能力"
        if "tokenhub" in raw_all:
            return "API 已上腾讯云 TokenHub"
    candidates: list[str] = []
    for value in [*preferred, *card.key_facts]:
        text = clean_text(value)
        if not text:
            continue
        text = re.sub(r"^来源摘要[：:]\s*", "", text)
        text = re.sub(r"^鏉ユ簮鎽樿[锛?:]\s*", "", text)
        text = re.sub(r"^.+?相关事件[：:]\s*", "", text)
        text = re.sub(r"^.+?鐩稿叧浜嬩欢[锛?:]\s*", "", text)
        if text and text not in candidates:
            candidates.append(text)
    best = ""
    best_score = -1
    for text in candidates:
        lowered = text.lower()
        keyword_score = sum(3 for keyword in CONCRETE_MODEL_DETAIL_KEYWORDS if keyword.lower() in lowered)
        if keyword_score <= 0:
            continue
        score = keyword_score
        if any(ch.isdigit() for ch in text):
            score += 1
        score += min(len(text), 80) // 24
        if score > best_score:
            best = text
            best_score = score
    if best_score <= 0:
        return ""
    return _soft_limit(best, CARD_BODY_LIMIT)


def _fit_design_cards(cards: list[dict[str, str]], meta: dict[str, Any]) -> list[dict[str, str]]:
    target = int(meta.get("card_count") or 3)
    cleaned: list[dict[str, str]] = []
    seen_titles: set[str] = set()
    for card in cards:
        title = clean_text(str(card.get("title") or ""))
        body = clean_text(str(card.get("body") or card.get("content") or ""))
        if not title or not body or title in seen_titles:
            continue
        if title in BANNED_CARD_TITLES:
            continue
        seen_titles.add(title)
        cleaned.append(card)
    editor = _card(str(meta.get("icon") or "✦"), "编辑判断", f"{meta.get('editor_rating')} {meta.get('editor_comment')}", "EditorCard")
    non_editor = [c for c in cleaned if c.get("component") != "EditorCard" and c.get("title") != "编辑判断"]
    if target <= 1:
        return [editor]
    return [*non_editor[: target - 1], editor]


def _too_similar(left: str, right: str) -> bool:
    a = _norm_text(left)
    b = _norm_text(right)
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = sorted([a, b], key=len)
    return len(shorter) >= 8 and shorter in longer


def _dedupe_editorial_cards(cards: list[dict[str, str]], fallbacks: list[tuple[str, str]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    used_titles: set[str] = set()
    fallback_iter = iter(fallbacks)
    for raw in cards:
        title = clean_text(str(raw.get("title") or "信息要点"))
        body = clean_text(str(raw.get("body") or ""))
        while any(_too_similar(body, existing["body"]) for existing in result):
            try:
                title, body = next(fallback_iter)
            except StopIteration:
                break
        if title in used_titles:
            try:
                title, body = next(fallback_iter)
            except StopIteration:
                title = f"{title}补充"
        used_titles.add(title)
        result.append(_card(str(raw.get("icon") or CARD_ICONS.get(title, "·")), title, body))
    return result


def _generic_news_type(card: EvidenceCard, display_title: str, news: dict[str, str | bool]) -> str:
    text = clean_text(" ".join([display_title, card.event_title, card.entity, " ".join(card.key_facts), str(news.get("fact") or ""), str(news.get("detail") or "")])).lower()
    if card.community_count > 0 and card.official_count == 0:
        return "community"
    if any(k in text for k in ["招聘", "人才", "融资", "投资", "监管", "政策", "行业", "岗位"]):
        return "industry"
    if any(k in text for k in ["发布", "升级", "上线", "推出", "模型", "语音识别", "asr", "hunyuan", "混元", "qwen", "千问"]):
        return "model_release"
    if any(k in text for k in ["codex", "token", "api", "sdk", "github", "vllm", "agent", "工具", "开发"]):
        return "tool"
    return "media"


def _generic_news_cards(
    card: EvidenceCard,
    news: dict[str, str | bool],
    *,
    source_short: str,
    title: str,
    content: str,
    point: str,
    impact: str,
    followup: str,
) -> list[dict[str, str]]:
    kind = _generic_news_type(card, title, news)
    detail = clean_text(str(news.get("detail") or ""))
    discussion = clean_text(str(news.get("discussion") or ""))
    summary = _source_summary(card)
    if _too_similar(content, point):
        if kind == "model_release":
            point = "参数、入口、价格和能力变化还没补齐，不能当成完整发布解读。"
        elif kind == "tool":
            point = "目前只看到方法或案例，真实节省幅度和副作用还要看原文。"
        elif kind == "industry":
            point = "来源没有给出更多可核数字，先按行业观察处理。"
        else:
            point = "来源细节有限，先保留已知事实，不扩展成确定结论。"
    if "影响要落到真实使用" in impact:
        if kind == "model_release":
            impact = "普通用户主要看能否实际调用，以及价格、速度或门槛是否变化。"
        elif kind == "tool":
            impact = "对开发者来说，重点是能不能稳定省时间、省成本。"
        elif kind == "industry":
            impact = "它影响的是人才、公司策略或行业预期，不是马上改变产品体验。"
        else:
            impact = "先看它会不会改变入口、价格、功能或普通用户的选择。"
    audience = ""
    if kind == "tool":
        audience = "开发者和团队最关心调用成本、权限边界和工作流是否真的省事。"
        cards = [
            _card("⚙", "功能方法", content),
            _card("✓", "实测结论", point),
            _card("◇", "适合谁用", audience),
            _card("⏱", "注意事项", followup),
        ]
    elif kind == "industry":
        cards = [
            _card("▣", "发生了什么", content),
            _card("▦", "核心数据", detail or point),
            _card("◇", "行业含义", impact),
            _card("✦", "我的判断", discussion or "它更像行业观察，不等同于产品发布或能力升级。"),
        ]
    elif kind == "community":
        cards = [
            _card("◎", "用户反馈", content),
            _card("◈", "测试条件", detail or summary or "测试条件和样本仍需回到源头截图核对。"),
            _card("↗", "初步结果", point),
            _card("⏱", "注意事项", followup),
        ]
    elif kind == "model_release":
        cards = [
            _card("⚗", "发布信息", content),
            _card("↗", "能力变化", point),
            _card("◇", "使用影响", impact),
            _card("⏱", "待确认", followup),
        ]
    else:
        cards = [
            _card("▣", "发生了什么", content),
            _card("▦", "核心数据", point),
            _card("◇", "使用影响", impact),
            _card("⏱", "待确认", followup),
        ]
    fallbacks = [
        ("下一步", followup),
        ("我的判断", discussion or impact),
        ("适合谁用", summary or title),
        ("待确认", f"{source_short} 是当前主要来源，等官方或更多来源交叉验证。"),
    ]
    return _dedupe_editorial_cards(cards, fallbacks)


def _evidence_bundle(card: EvidenceCard) -> dict[str, Any]:
    bundle = {
        "event_title": card.event_title,
        "entity": card.entity,
        "risk": card.risk,
        "source_count": card.source_count,
        "official_count": card.official_count,
        "media_count": card.media_count,
        "community_count": card.community_count,
        "key_facts": card.key_facts,
        "uncertainty": card.uncertainty,
        "evidence": card.evidence_links[:5],
    }
    if not (_is_model_repo_update(card) or _is_github_release(card) or _is_paper(card)):
        bundle["focused_news"] = focused_news_summary(card)
    return bundle


def _has_forbidden_text(text: str, card: EvidenceCard) -> bool:
    compact = clean_text(text).lower()
    if _looks_raw_english(compact):
        return True
    if any(phrase.lower() in compact for phrase in FORBIDDEN_PUBLIC_PHRASES):
        return True
    if _is_model_repo_update(card):
        if any(phrase.lower() in compact for phrase in MODEL_REPO_UNSUPPORTED):
            return True
        if re.search(r"(发布了|发布新|正式发布|宣布发布)", compact):
            return True
    if card.risk != "green" and re.search(r"(确认|已证实|确定|官方发布)", compact):
        return True
    return False


def _validate_payload(payload: dict[str, Any], card: EvidenceCard) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    lead = clean_text(str(payload.get("lead") or ""))
    raw_cards = payload.get("cards")
    if not lead or _has_forbidden_text(lead, card) or not isinstance(raw_cards, list):
        return None
    if not (1 <= len(raw_cards) <= 6):
        return None
    cards: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_cards:
        if not isinstance(item, dict):
            return None
        title = clean_text(str(item.get("title") or ""))
        body = clean_text(str(item.get("body") or ""))
        if title not in CARD_TITLES or title in seen:
            return None
        if title in BANNED_CARD_TITLES:
            return None
        if len(body) < 2 or len(body) > CARD_BODY_LIMIT or _has_forbidden_text(body, card):
            return None
        if any(_too_similar(body, existing["body"]) for existing in cards):
            return None
        if any(phrase in body for phrase in CORE_DIRECTIVE_PHRASES):
            return None
        seen.add(title)
        cards.append(_card(CARD_ICONS[title], title, body, str(item.get("component") or title)))
    if "编辑判断" not in seen:
        return None
    return {"lead": _soft_limit(lead, LEAD_LIMIT), "cards": cards}


def _try_llm_editor(card: EvidenceCard, display_title: str, confirmed_line: str) -> dict[str, Any] | None:
    """Legacy compatibility hook; per-card model calls are permanently disabled."""
    return None


def _fallback_editorial(card: EvidenceCard, display_title: str, confirmed_line: str) -> dict[str, Any]:
    source = _source_name(card)
    source_short = _short_source(source)
    name = _model_name(display_title)
    summary = _source_summary(card)
    performance = official_performance_summary(card)
    news = focused_news_summary(card, display_title)
    news_type = _design_news_type(card, display_title, news)
    meta = _design_package_meta(card, display_title, news_type)
    title = clean_text(str(news.get("title") or display_title or card.event_title))
    fact = clean_text(str(news.get("fact") or confirmed_line))
    detail = clean_text(str(news.get("detail") or summary or fact))
    impact = clean_text(str(news.get("impact") or ""))
    discussion = clean_text(str(news.get("discussion") or ""))
    lead = _soft_limit(str(news.get("lead") or confirmed_line or title), LEAD_LIMIT)
    cards: list[dict[str, str]] = []

    if news_type in {"model_release", "model_update"}:
        concrete_detail = "" if card.official_count > 0 else _concrete_model_detail(card, performance, detail, summary, fact)
        capability = performance or concrete_detail or detail or summary or "先看能力、入口和限制"
        if card.official_count == 0 and not concrete_detail and (news_type == "model_release" or _too_similar(capability, title)):
            capability = "参数、入口、价格未公开"
        if card.official_count == 0:
            summary_line = "元宝/API 入口已有线索" if concrete_detail else "媒体称发布，入口未公开"
        elif performance:
            summary_line = "官方发布，含评测对比"
        elif news_type == "model_update":
            summary_line = "能力更新，先看实际入口"
        else:
            summary_line = "官方发布，先看入口限制"
        specs = _model_param_hint(card, display_title)
        availability = _availability_hint(card, news_type)
        cards = [
            _card("✦", "核心看点", summary_line, "SummaryCard"),
            _card("🧠", "核心能力", capability, "CapabilityCard"),
            _card("◇", "开放情况", availability, "AvailabilityCard"),
            _card("▦", "参数", specs, "SpecCard"),
            _card(str(meta["icon"]), "编辑判断", f"{meta['editor_rating']} {meta['editor_comment']}", "EditorCard"),
        ]
    elif news_type == "product_update":
        entry = "看官方入口" if card.official_count > 0 else "入口未公开"
        price = "价格未公布" if card.official_count == 0 else "看套餐限制"
        platform = "以产品页为准" if source_short == "资料来源" else source_short
        cards = [
            _card("＋", "新增功能", title, "FeatureCard"),
            _card("▣", "入口", entry, "AvailabilityCard"),
            _card("¥", "免费收费", price, "PriceCard"),
            _card("▦", "支持平台", platform, "PlatformCard"),
            _card(str(meta["icon"]), "编辑判断", f"{meta['editor_rating']} {meta['editor_comment']}", "EditorCard"),
        ]
    elif news_type == "github_release":
        parts = github_release_change_parts(card)
        version = _first_specific_fact(card, display_title)
        if parts.get("compare"):
            version = _github_release_compare_text(parts["compare"])
        changes = _github_release_point_text(parts, summary)
        maintenance = clean_text(str(parts.get("maintenance") or ""))
        breaking = ""
        raw = clean_text(" ".join([summary, *card.key_facts])).lower()
        if "breaking" in raw or "破坏性" in raw or "不兼容" in raw:
            breaking = "有不兼容改动"
        cards = [
            _card("⚙", "Version", version, "VersionCard"),
            _card("＋", "What's New", changes, "ChangeCard"),
        ]
        if maintenance:
            cards.append(_card("✓", "Bug Fix", f"维护：{maintenance}", "FixCard"))
        if breaking:
            cards.append(_card("!", "Breaking", breaking, "BreakingCard"))
        cards.append(_card("✦", "升级建议", _editor_comment(meta["editor_rating"].count("★"), news_type), "EditorCard"))
    elif news_type == "model_repository":
        cards = [
            _card("🧠", "模型名称", name, "SummaryCard"),
            _card("✓", "支持任务", _task_hint(card, summary), "TaskCard"),
            _card("◇", "License", _license_hint(card), "LicenseCard"),
            _card("⬇", "下载方式", _availability_hint(card, news_type), "AvailabilityCard"),
            _card("▦", "参数规模", _model_param_hint(card, display_title), "SpecCard"),
            _card("✦", "编辑判断", f"{meta['editor_rating']} {meta['editor_comment']}", "EditorCard"),
        ]
    elif news_type == "benchmark":
        metric = performance or detail or title
        cards = [
            _card("📈", "排名", "看原榜单口径", "RankingCard"),
            _card("↗", "领先幅度", metric, "ComparisonCard"),
            _card("▣", "测试内容", _task_hint(card, summary), "BenchmarkCard"),
            _card("✓", "测试口径", "官方口径" if card.official_count > 0 else "社区样本", "CredibilityCard"),
            _card("✦", "编辑判断", f"{meta['editor_rating']} {meta['editor_comment']}", "EditorCard"),
        ]
    elif news_type == "developer_tool":
        specific_fact = _first_specific_fact(card, title)
        if any(k in specific_fact.lower() for k in [" pr", "roi", "部署", "token", "multi-agent"]):
            specific = specific_fact
        else:
            specific = detail or specific_fact or summary or fact
        if "73% pr" in specific.lower() and "4500" in specific:
            specific = "73% PR、2900工程师、4500次部署"
        raw_specific = clean_text(" ".join([specific, specific_fact, detail, summary, fact, *card.key_facts]))
        if "代码安全" in raw_specific and "数据合规" in raw_specific:
            specific = "原因指向代码安全和数据合规"
        cards = [
            _card("▣", "测试内容", specific, "DetailCard"),
            _card("⚙", "事件", title, "EventCard"),
            _card("👥", "普通用户", impact or discussion or "主要影响开发者", "AudienceCard"),
            _card("✦", "编辑判断", f"{meta['editor_rating']} {meta['editor_comment']}", "EditorCard"),
        ]
    elif news_type == "paper":
        cards = [
            _card("📄", "核心看点", title, "SummaryCard"),
            _card("◈", "核心能力", summary or detail or "看方法和代码", "MethodCard"),
            _card("▣", "行业意义", impact or "短期看工程落地", "ImpactCard"),
            _card("✦", "编辑判断", f"{meta['editor_rating']} {meta['editor_comment']}", "EditorCard"),
        ]
    elif news_type == "hiring_funding":
        company = clean_text(card.entity or source_short or "AI 公司")
        cards = [
            _card("🏢", "公司", company, "CompanyCard"),
            _card("▣", "事件", title, "EventCard"),
            _card("◇", "行业信号", detail or impact or "看人才和业务方向", "SignalCard"),
            _card("👥", "普通用户", "短期影响有限", "AudienceCard"),
            _card("✦", "编辑判断", f"{meta['editor_rating']} {meta['editor_comment']}", "EditorCard"),
        ]
    elif news_type == "official_personnel_signal":
        cards = [
            _card("▣", "原帖内容", fact, "SourcePostCard"),
            _card("◇", "产品方向", impact or discussion or "只作一线信号", "SignalCard"),
            _card("✦", "编辑判断", f"{meta['editor_rating']} {meta['editor_comment']}", "EditorCard"),
        ]
    elif news_type in {"industry", "media"}:
        cards = [
            _card("▣", "事件", title, "EventCard"),
            _card("👥", "影响对象", impact or "看公司和用户侧", "AudienceCard"),
            _card("🏢", "行业意义", discussion or detail or "偏行业观察", "ImpactCard"),
            _card("✦", "编辑判断", f"{meta['editor_rating']} {meta['editor_comment']}", "EditorCard"),
        ]
    elif news_type == "community":
        result = detail or summary or title
        cards = [
            _card("📈", "测试内容", result, "FeedbackCard"),
            _card("▣", "测试条件", "只代表该用户环境", "ConditionCard"),
            _card("↗", "领先幅度", detail or summary or "看源头截图", "ResultCard"),
            _card("✦", "编辑判断", f"{meta['editor_rating']} {meta['editor_comment']}", "EditorCard"),
        ]
    else:
        cards = [
            _card("✦", "核心看点", title, "SummaryCard"),
            _card("▣", "事件", detail or summary or fact, "KeywordCard"),
            _card("✦", "编辑判断", f"{meta['editor_rating']} {meta['editor_comment']}", "EditorCard"),
        ]

    cards = _fit_design_cards(cards, meta)
    return {"lead": lead, "cards": cards, **meta}

def _first_uncertainty(card: EvidenceCard) -> str:
    for item in card.uncertainty:
        text = clean_text(item)
        if text:
            if "官方补证" in text or "官方未明确" in text or "未看到" in text:
                return "待确认。"
            if "不能写成官方确认" in text:
                return "仅作线索，不写成官方结论。"
            return text
    return ""


def build_card_design_system(
    card: EvidenceCard,
    tab_icon: str,
    risk_label: str,
    published_time: str,
    display_title: str,
    confirmed_line: str,
) -> dict[str, Any]:
    # Optional edition-level AI editing is applied after the final portfolio is known.
    # Card design must remain deterministic and must never trigger per-card AI.
    edited = _fallback_editorial(card, display_title, confirmed_line)
    lead = _soft_limit(str(edited["lead"]), LEAD_LIMIT)
    fallback_meta = _design_package_meta(card, display_title, _design_news_type(card, display_title, focused_news_summary(card, display_title)))
    package = {**fallback_meta, **{k: v for k, v in edited.items() if k != "cards"}}
    package["lead"] = lead
    cards = _fit_design_cards(list(edited["cards"]), package)
    package["cards"] = cards
    package["card_count"] = len(cards)
    package["source_status"] = risk_label
    package["published_time"] = published_time
    return package


def build_editorial_cards(
    card: EvidenceCard,
    tab_icon: str,
    risk_label: str,
    published_time: str,
    display_title: str,
    confirmed_line: str,
) -> list[dict[str, str]]:
    return list(
        build_card_design_system(
            card,
            tab_icon=tab_icon,
            risk_label=risk_label,
            published_time=published_time,
            display_title=display_title,
            confirmed_line=confirmed_line,
        ).get("cards", [])
    )

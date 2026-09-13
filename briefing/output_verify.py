from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from .render_contract import verify_review_render_contract
from .util import clean_text


REQUIRED = [
    "final.mp4",
    "cover.png",
    "bilibili.md",
    "bilibili.json",
    "pinned-comment.md",
    "sources.md",
    "fact-check.md",
    "manifest.json",
    "subtitles.srt",
    "script.json",
    "news-script.md",
    "news-script.json",
]

GENERIC_SCRIPT_PHRASES = [
    "这条值得关注的是它可能带来的实际使用变化",
    "这条值得关注的是它可能改变相关产品、模型或行业节奏",
    "最终要看它是不是真有用",
    "只采用已核对来源里的确定信息",
    "影响要落到真实使用、产品策略或后续选择上",
    "它是否重要，要看能否改变真实使用、成本或工作流程",
    "对开发者来说，重点是接口、兼容性和迁移成本是否发生变化",
    "对用户来说，重点是功能入口、覆盖范围、价格和实际可用性",
    "AI 正在快速发展",
    "引发广泛热议",
    "引发行业热议",
    "未来可期",
    "具有重大意义",
    "推动行业发展",
]

FORBIDDEN_SCRIPT_PHRASES = [
    "版本变化",
    "使用影响",
    "后续观察",
    "仓库状态",
    "发生了什么",
    "后续继续观察",
    "来源可查",
    "值得期待",
    "官方补证",
    "重点关注",
    "持续关注",
    "具体以官方为准",
    "等待更多消息",
    "如有更新第一时间通知",
    "欢迎关注",
    "微信公众号",
    "更多精彩内容",
    "一句话",
    "信息不够",
    "不能直接当成正式新闻",
    "暂时剔除",
    "爬虫",
    "筛选",
    "官网抓取",
    "RSS",
    "RSSHub",
    "订阅源",
    "聚合源",
    "Atom",
    "Feed",
    "Google News",
    "Google 新闻",
    "截图已核验",
    "绑定本条新闻",
    "核验流程",
    "编辑判断",
    "我的判断",
    "来源口径",
    "来源状态",
    "证据边界",
    "按媒体报道处理",
    "按单一媒体报道处理",
    "截图作为来源",
    "画面保留原帖截图",
    "适合短讯处理",
    "核验摘要",
    "按已核对来源处理",
    "后面需要继续优化",
    "是否影响",
    "问题是刚发布啊",
    "跑路",
    "狂喜",
    "刚刚",
    "重磅",
    "突发",
    "最强",
    "震撼",
    "炸裂",
    "王炸",
    "杀疯了",
    "颠覆",
    "彻底取代",
    "程序员失业",
]

FORBIDDEN_SCRIPT_PATTERNS = [
    re.compile(r"可以关注[^。！？!?；;\n]{0,40}是否"),
    re.compile(r"后面需要[^。！？!?；;\n]{0,40}优化"),
    re.compile(r"[?？]{2,}"),
]

MOJIBAKE_STRONG_MARKERS = [
    "\ufffd",
    "鍙戝",
    "涓",
    "浠婃",
    "鏃╂",
    "绠€",
    "鍗曡",
    "鏉″",
    "寰佺",
    "妯″",
    "闃块",
    "鐨勬",
    "鈥?",
    "濯掍",
    "閲嶅",
    "妗堜",
    "緥鏄",
    "剧ず",
    "鐢ㄧ",
    "Ã",
    "Â",
    "â€™",
    "â€œ",
    "â€",
]

PUBLIC_COPY_DANGLING_END_RE = re.compile(r"(?:以及|包括|例如|但是|但|因此|从而|并且|同时|其中)\s*[，,：:；;、-]*$")
PUBLIC_COPY_PAIRS = (("“", "”"), ("‘", "’"), ("（", "）"), ("【", "】"), ("《", "》"))

MOJIBAKE_WEAK_MARKERS = [
    "銆",
    "锛",
    "鐨",
    "绋",
    "绾",
    "缁",
    "鏂",
    "浠",
    "璇",
    "鍊",
]

RAW_ENGLISH_CLAUSE_RE = re.compile(r"[A-Za-z][A-Za-z0-9'’&/._-]*(?:\s+[A-Za-z][A-Za-z0-9'’&/._-]*){2,}")
RAW_ENGLISH_FUNCTION_WORDS = {
    "a",
    "an",
    "the",
    "about",
    "and",
    "or",
    "for",
    "with",
    "from",
    "into",
    "to",
    "of",
    "on",
    "in",
    "is",
    "are",
    "will",
    "can",
    "this",
    "that",
    "details",
}

NEWS_SEGMENT_TARGET_SECONDS = 32.0
NEWS_SEGMENT_MIN_SECONDS = 8.0
NEWS_SEGMENT_MAX_SECONDS = 65.0
BRIEF_SEGMENT_MIN_SECONDS = 6.0
# Required source screenshots and the approved local voice can make a dense
# brief slightly longer without turning it into a feature story. Keep a hard
# ceiling, but do not reject an otherwise clean 31-36 second evidence-backed
# item merely because its narration cadence is slower.
BRIEF_SEGMENT_MAX_SECONDS = 36.0
ROLLING_EDITION_OVERHEAD_MAX_SECONDS = 75.0
ROLLING_EDITION_OVERHEAD_RECOMMENDED_SECONDS = 45.0
MIN_NEWS_NARRATION_CHARS = 48
MIN_BRIEF_NARRATION_CHARS = 24
APPROVED_TTS_VOICES = {"zh-CN-XiaoxiaoNeural", "zh-CN-XiaoyiNeural"}

AUTOMATION_LOW_VALUE_PHRASES = [
    "入口待确认",
    "参数待公开",
    "页面变化",
    "模型页出现更新",
    "模型仓库更新",
    "只当模型页变动",
    "release metadata",
    "未写明具体变更",
    "只给出版本号",
]

# Colloquial fragments are the fingerprint of community/blog text leaking
# verbatim into broadcast narration (observed in the 2026-07-20 run: the
# copy gates passed "实话实说啊,这个价格…" untouched).  The list is kept
# conservative on purpose: a hit resamples one story, it never fails the run.
AUTOMATION_COLLOQUIAL_MARKERS = [
    "实话实说",
    "讲真",
    "说白了",
    "老实说",
    "咱就是说",
    "不得不说",
    "家人们",
    "绝绝子",
    "yyds",
    "牛逼",
    "太顶了",
    "无脑冲",
    "哈哈哈",
]

# A headline that ends up carrying a bare domain ("Kimi开源x.com") is a
# title-extraction failure, not an editorial choice; broadcast titles name
# products and entities, never hostnames.
# The lookbehind excludes ASCII word characters only: \w would also match
# CJK, and the observed failure is precisely a domain glued to Chinese text
# ("Kimi开源x.com").
TITLE_BARE_DOMAIN_RE = re.compile(
    r"(?<![A-Za-z0-9._@-])[A-Za-z0-9-]+\.(?:com|net|org|io|ai|co|cn|dev|app|me|xyz)\b",
    flags=re.I,
)

NARRATION_COLLOQUIAL_PARTICLE_RE = re.compile(r"[啊呀嘛][,，]")

AUTOMATION_FACTUAL_SIGNALS = [
    "api",
    "sdk",
    "权重",
    "下载",
    "许可",
    "license",
    "参数",
    "上下文",
    "价格",
    "免费",
    "新增",
    "发布",
    "支持",
    "升级",
    "加速",
    "修复",
    "改进",
    "上线",
    "开放",
    "开源",
    "agent",
    "语音",
    "视频",
    "benchmark",
    "评测",
    "性能",
    "数据集",
    "实验",
]

SOCIAL_SIGNAL_DISCLAIMERS = {
    "official_personnel": "一线消息，未获官方公告确认",
    "community_rumour": "传闻/风向，未获官方公告确认",
}

SOCIAL_SIGNAL_FORBIDDEN_CLAIMS = [
    "已正式发布",
    "已经正式发布",
    "正式上线",
    "已经上线",
    "现已上线",
    "已开放使用",
]


def _ffprobe_json(args: list[str]) -> dict:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {}
    out = subprocess.check_output([ffprobe, "-v", "error", *args], text=True, stderr=subprocess.DEVNULL)
    return json.loads(out or "{}")


def _srt_seconds(value: str) -> float:
    m = re.match(r"(\d+):(\d+):(\d+),(\d+)", value.strip())
    if not m:
        return 0.0
    h, minute, sec, ms = [int(x) for x in m.groups()]
    return h * 3600 + minute * 60 + sec + ms / 1000


def parse_srt(path: Path) -> list[tuple[float, float, str]]:
    text = path.read_text(encoding="utf-8-sig")
    blocks = re.split(r"\n\s*\n", text.strip())
    rows: list[tuple[float, float, str]] = []
    for block in blocks:
        lines = [x.strip("\ufeff") for x in block.splitlines() if x.strip()]
        if len(lines) < 3:
            continue
        if "-->" not in lines[1]:
            continue
        start_raw, end_raw = [x.strip() for x in lines[1].split("-->", 1)]
        rows.append((_srt_seconds(start_raw), _srt_seconds(end_raw), "\n".join(lines[2:])))
    return rows


def _parse_mmss_lines(path: Path) -> list[tuple[float, str]]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    rows: list[tuple[float, str]] = []
    for line in text.splitlines():
        m = re.match(r"\s*(\d{2,3}):(\d{2})\s+(.+)", line)
        if not m:
            continue
        minute, sec = int(m.group(1)), int(m.group(2))
        rows.append((minute * 60 + sec, m.group(3).strip()))
    return rows


def _navigation_label_key(value: str) -> str:
    text = str(value or "").lower().strip()
    text = re.sub(r"^(?:开场|intro)\s*[｜|:：-]*\s*", "", text)
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)


def _expected_navigation(script_segments: list[dict]) -> list[tuple[float, str]]:
    rows: list[tuple[float, str]] = []
    for segment in script_segments:
        kind = str(segment.get("kind") or "news")
        if kind not in {"intro", "news"}:
            continue
        title = str(segment.get("title") or segment.get("caption") or "AI 动态")
        if kind == "intro":
            title = "开场｜" + title
        rows.append((float(segment.get("start") or 0), title))
    return rows


def _navigation_contract_errors(path: Path, expected: list[tuple[float, str]]) -> list[str]:
    if not expected:
        return []
    actual = _parse_mmss_lines(path)
    if len(actual) != len(expected):
        return [f"{path.name} navigation has {len(actual)} entries, expected {len(expected)} from script.json"]
    for index, ((expected_time, expected_title), (actual_time, actual_title)) in enumerate(zip(expected, actual), 1):
        if abs(expected_time - actual_time) > 1.1:
            return [f"{path.name} navigation time {index} is {actual_time:.0f}s, expected {expected_time:.0f}s from script.json"]
        expected_key = _navigation_label_key(expected_title)
        actual_key = _navigation_label_key(actual_title)
        if expected_key and actual_key and expected_key not in actual_key and actual_key not in expected_key:
            return [f"{path.name} navigation label {index} does not match script.json"]
    return []


def _has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def _looks_raw_english(text: str, min_len: int = 34) -> bool:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) < min_len or _has_cjk(text):
        return False
    letters = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    visible = sum(1 for ch in text if not ch.isspace())
    return visible > 0 and letters / visible > 0.58


def _raw_english_clause(text: str) -> str:
    """Find an untranslated English sentence embedded in otherwise Chinese copy."""
    for match in RAW_ENGLISH_CLAUSE_RE.finditer(str(text or "")):
        fragment = match.group(0).strip()
        raw_tokens = [token.strip("'’&/._-") for token in fragment.split()]
        tokens = [token.lower() for token in raw_tokens]
        function_words = sum(token in RAW_ENGLISH_FUNCTION_WORDS for token in tokens)
        if len(tokens) >= 4 and function_words >= 2:
            return fragment
        lowercase_terms = [token for token in raw_tokens if len(token) >= 3 and token[:1].islower()]
        if len(tokens) >= 3 and lowercase_terms:
            return fragment
    return ""


def _looks_mojibake(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact:
        return False
    if re.search(r"(?:\?{3,}|？{3,})", compact):
        return True
    if any(marker in compact for marker in MOJIBAKE_STRONG_MARKERS):
        return True
    weak_hits = sum(1 for marker in MOJIBAKE_WEAK_MARKERS if marker in compact)
    return weak_hits >= 3


def _iter_visible_script_text(script_path: Path) -> list[tuple[str, str]]:
    if not script_path.exists():
        return []
    try:
        payload = json.loads(script_path.read_text(encoding="utf-8-sig"))
    except Exception:
        return []
    rows: list[tuple[str, str]] = []
    for seg_index, seg in enumerate(payload if isinstance(payload, list) else []):
        if not isinstance(seg, dict):
            continue
        for key in ["title", "caption", "text"]:
            rows.append((f"segment {seg_index + 1} {key}", str(seg.get(key) or "")))
        for card_index, card in enumerate(seg.get("cards") or [], 1):
            if isinstance(card, dict):
                rows.append((f"segment {seg_index + 1} card {card_index} title", str(card.get("title") or "")))
                rows.append((f"segment {seg_index + 1} card {card_index} body", str(card.get("body") or "")))
        for page_index, page in enumerate(seg.get("visual_pages") or [], 1):
            if not isinstance(page, dict):
                continue
            for key in ["title", "lead"]:
                rows.append((f"segment {seg_index + 1} page {page_index} {key}", str(page.get(key) or "")))
            for card_index, card in enumerate(page.get("cards") or [], 1):
                if isinstance(card, dict):
                    rows.append((f"segment {seg_index + 1} page {page_index} card {card_index} title", str(card.get("title") or "")))
                    rows.append((f"segment {seg_index + 1} page {page_index} card {card_index} body", str(card.get("body") or "")))
    return rows


def _iter_public_text(run_dir: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for name in ["bilibili.md", "pinned-comment.md", "news-script.md"]:
        path = run_dir / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        for line_no, line in enumerate(text.splitlines(), 1):
            if line.strip():
                rows.append((f"{name}:{line_no}", line.strip()))
    srt_path = run_dir / "subtitles.srt"
    if srt_path.exists():
        for idx, (_start, _end, text) in enumerate(parse_srt(srt_path), 1):
            rows.append((f"subtitles.srt cue {idx}", text))
    return rows


def _load_script_segments(script_path: Path) -> list[dict]:
    if not script_path.exists():
        return []
    try:
        payload = json.loads(script_path.read_text(encoding="utf-8-sig"))
    except Exception:
        return []
    return [seg for seg in payload if isinstance(seg, dict)] if isinstance(payload, list) else []


def _script_load_errors(script_path: Path) -> list[str]:
    if not script_path.exists():
        return []
    try:
        payload = json.loads(script_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return [f"script.json parse failed: {exc}"]
    if not isinstance(payload, list):
        return ["script.json root must be a list"]
    invalid = [index for index, segment in enumerate(payload, 1) if not isinstance(segment, dict)]
    if invalid:
        return [f"script.json segment {invalid[0]} must be an object"]
    return []


def _editorial_consistency_errors(script_segments: list[dict]) -> list[str]:
    errors: list[str] = []
    visible_rows = [text for _label, text in _visible_text_rows(script_segments)]
    for text in visible_rows:
        match = re.search(r"\b([A-Za-z][A-Za-z0-9._+-]{1,30}):(?=[\u4e00-\u9fff])", text)
        if match:
            errors.append(f"visible copy contains dangling colon fragment: {match.group(0)}")
            break

    intros = [segment for segment in script_segments if str(segment.get("kind") or "") == "intro"]
    news = [segment for segment in script_segments if str(segment.get("kind") or "news") == "news"]
    news_text = " ".join(
        str(segment.get(key) or "")
        for segment in news
        for key in ["title", "caption", "text"]
    ).upper()
    for intro in intros:
        intro_text = " ".join(str(intro.get(key) or "") for key in ["title", "caption", "text"])
        if not re.search(r"重点看|发布|推出|上线", intro_text):
            continue
        for match in re.finditer(r"\bGPT[- ]?[A-Za-z0-9]+(?:\.[A-Za-z0-9]+)*\b", intro_text, flags=re.I):
            model = re.sub(r"\s+", "-", match.group(0)).upper()
            if model not in news_text:
                errors.append(f"intro mentions {model} but no news segment covers it")
    return errors


def _visible_text_rows(script_segments: list[dict]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for seg_index, segment in enumerate(script_segments, 1):
        for key in ["title", "caption", "text"]:
            rows.append((f"segment {seg_index} {key}", str(segment.get(key) or "")))
        for card_index, card in enumerate(segment.get("cards") or [], 1):
            if isinstance(card, dict):
                rows.append((f"segment {seg_index} card {card_index} title", str(card.get("title") or "")))
                rows.append((f"segment {seg_index} card {card_index} body", str(card.get("body") or "")))
        for page_index, page in enumerate(segment.get("visual_pages") or [], 1):
            if not isinstance(page, dict):
                continue
            for key in ["title", "lead"]:
                rows.append((f"segment {seg_index} page {page_index} {key}", str(page.get(key) or "")))
            for card_index, card in enumerate(page.get("cards") or [], 1):
                if isinstance(card, dict):
                    rows.append((f"segment {seg_index} page {page_index} card {card_index} title", str(card.get("title") or "")))
                    rows.append((f"segment {seg_index} page {page_index} card {card_index} body", str(card.get("body") or "")))
    return rows


def _public_copy_integrity_errors(rows: list[tuple[str, str]]) -> list[str]:
    errors: list[str] = []
    for label, raw in rows:
        text = clean_text(str(raw or ""))
        if not text:
            continue
        for opening, closing in PUBLIC_COPY_PAIRS:
            if text.count(opening) != text.count(closing):
                errors.append(f"visible/public text has unclosed punctuation at {label}: {text[:80]}")
                break
        if PUBLIC_COPY_DANGLING_END_RE.search(text):
            errors.append(f"visible/public text ends with a dangling connector at {label}: {text[-40:]}")
    return errors


def _load_manuscript_segments(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return []
    segments = payload.get("segments") if isinstance(payload, dict) else []
    return [seg for seg in segments if isinstance(seg, dict)] if isinstance(segments, list) else []


def _automatic_news_content_errors(script_segments: list[dict]) -> list[str]:
    errors: list[str] = []
    for index, segment in enumerate(script_segments, 1):
        if str(segment.get("kind") or "news") != "news":
            continue
        title = str(segment.get("title") or "").strip()
        narration = str(segment.get("text") or "").strip()
        visible = " ".join(
            [
                title,
                str(segment.get("caption") or ""),
                narration,
                *[
                    " ".join([str(card.get("title") or ""), str(card.get("body") or "")])
                    for card in segment.get("cards") or []
                    if isinstance(card, dict)
                ],
            ]
        )
        compact_narration = re.sub(r"\s+", "", narration)
        sensational = re.search(r"王炸|杀疯了|刷爆|震撼|炸裂|AGI\s*真的来了|程序员失业|彻底取代|颠覆", visible, flags=re.I)
        if sensational:
            errors.append(f"news segment {index} contains sensational copy requiring editorial review: {sensational.group(0)}")
        tier = str(segment.get("editorial_tier") or "headline")
        minimum_chars = MIN_BRIEF_NARRATION_CHARS if tier == "brief" else MIN_NEWS_NARRATION_CHARS
        if len(compact_narration) < minimum_chars:
            errors.append(f"news segment {index} narration is too short for automatic publishing: {title[:60]}")
        if narration and narration[-1] not in "。！？!?」』”’\"":
            errors.append(f"news segment {index} narration ends with a truncated sentence fragment: {narration[-24:]}")
        if re.search(r"https?://\S+|www\.\S+", visible, flags=re.I):
            errors.append(f"news segment {index} exposes a raw URL in video copy: {title[:60]}")
        compact_title = re.sub(r"\s+", "", re.sub(r"^一线消息[|｜]|^传闻/风向[|｜]", "", title))
        if compact_title and len(compact_title) < 6:
            errors.append(f"news segment {index} title is too short to be a publishable headline: {title[:60]}")
        domain_match = TITLE_BARE_DOMAIN_RE.search(title)
        if domain_match:
            errors.append(f"news segment {index} title contains a bare domain: {domain_match.group(0)}")
        lowered = visible.lower()
        colloquial = next((marker for marker in AUTOMATION_COLLOQUIAL_MARKERS if marker.lower() in lowered), "")
        if not colloquial and NARRATION_COLLOQUIAL_PARTICLE_RE.search(narration):
            colloquial = NARRATION_COLLOQUIAL_PARTICLE_RE.search(narration).group(0)
        if colloquial:
            errors.append(f"news segment {index} contains colloquial scraped copy: {colloquial}")
        low_value = next((phrase for phrase in AUTOMATION_LOW_VALUE_PHRASES if phrase.lower() in lowered), "")
        if low_value:
            errors.append(f"news segment {index} contains low-information copy: {low_value}")
        has_signal = bool(re.search(r"\d+(?:\.\d+)?\s*(?:b|m|k|%|倍|项|个|token|tokens|版本)?\b", visible, flags=re.I))
        has_signal = has_signal or any(signal.lower() in lowered for signal in AUTOMATION_FACTUAL_SIGNALS)
        if not has_signal:
            errors.append(f"news segment {index} has no concrete capability, change, or user-impact fact: {title[:60]}")
    return errors


def _signal_segment_errors(script_segments: list[dict]) -> list[str]:
    """Enforce visible labelling and screenshot proof for X/community signals."""
    errors: list[str] = []
    for index, segment in enumerate(script_segments, 1):
        signal_kind = str(segment.get("signal_kind") or "")
        if not signal_kind:
            continue
        expected = SOCIAL_SIGNAL_DISCLAIMERS.get(signal_kind)
        if not expected:
            errors.append(f"news segment {index} has an unknown social signal kind: {signal_kind}")
            continue
        visible_parts = [
            str(segment.get(key) or "")
            for key in ["title", "caption", "text", "body", "disclaimer"]
        ]
        for card in segment.get("cards") or []:
            if isinstance(card, dict):
                visible_parts.extend([str(card.get("title") or ""), str(card.get("body") or "")])
        for page in segment.get("visual_pages") or []:
            if not isinstance(page, dict):
                continue
            visible_parts.extend([str(page.get("title") or ""), str(page.get("lead") or "")])
            for card in page.get("cards") or []:
                if isinstance(card, dict):
                    visible_parts.extend([str(card.get("title") or ""), str(card.get("body") or "")])
        visible = " ".join(visible_parts)
        if expected not in visible:
            errors.append(f"news segment {index} {signal_kind} is missing the required visible disclaimer")
        claim = next((phrase for phrase in SOCIAL_SIGNAL_FORBIDDEN_CLAIMS if phrase in visible), "")
        if claim:
            errors.append(f"news segment {index} {signal_kind} states an unconfirmed signal as fact: {claim}")
        evidence = [item for item in segment.get("evidence") or [] if isinstance(item, dict)]
        has_screenshot = any(
            str(item.get("screenshot_required") or "").lower() == "true"
            and str(item.get("screenshot_status") or "") == "captured"
            and str(item.get("screenshot_path") or "")
            and str(item.get("screenshot_kind") or "") != "source_excerpt_card"
            for item in evidence
        )
        if not has_screenshot:
            errors.append(f"news segment {index} {signal_kind} has no captured source-post screenshot")
    return errors


def _evidence_visual_coverage_errors(script_segments: list[dict]) -> list[str]:
    """Require recurring source pop-ups without demanding one for every story."""
    news = [segment for segment in script_segments if str(segment.get("kind") or "news") == "news"]
    if not news:
        return []
    covered = 0
    for segment in news:
        pages = [page for page in segment.get("visual_pages") or [] if isinstance(page, dict)]
        if any(
            str(page.get("kind") or "") == "evidence"
            and isinstance(page.get("evidenceVisual"), dict)
            and bool(page["evidenceVisual"].get("asset") or page["evidenceVisual"].get("image"))
            for page in pages
        ):
            covered += 1
    minimum = max(1, (len(news) + 5) // 6)
    if covered < minimum:
        return [
            f"evidence screenshot coverage is {covered}/{len(news)} stories; "
            f"rolling news video requires at least {minimum} source pop-up(s)"
        ]
    return []


def repairable_news_positions(run_dir: Path, errors: list[str]) -> list[int]:
    """Map content-only quality failures back to selectable news positions.

    The automatic pipeline may safely remove a bad card and render again only
    for copy-quality failures. It must not try to repair video, timing, or
    evidence failures by silently changing the selected news.
    """
    repairable_markers = (
        "narration is too short for automatic publishing",
        "exposes a raw URL in video copy",
        "contains low-information copy",
        "title contains a bare domain",
        "title is too short to be a publishable headline",
        "contains colloquial scraped copy",
        "has no concrete capability, change, or user-impact fact",
        # These checks point to visible copy belonging to one news segment.
        # It is safe to resample that card; video/evidence failures are not.
        "visible script text contains",
        "visible/public text contains",
        "contains an ellipsis",
    )
    indexes: set[int] = set()
    for error in errors:
        if not any(marker in error for marker in repairable_markers):
            continue
        match = re.search(r"\bsegment\s+(\d+)", error)
        if match:
            indexes.add(int(match.group(1)))

    segments = _load_script_segments(Path(run_dir) / "script.json")
    positions: list[int] = []
    for index in sorted(indexes):
        if index < 1 or index > len(segments):
            continue
        segment = segments[index - 1]
        if str(segment.get("kind") or "news") != "news":
            continue
        position = segment.get("position")
        try:
            position_value = int(position)
        except (TypeError, ValueError):
            position_value = sum(1 for item in segments[:index] if str(item.get("kind") or "news") == "news")
        if position_value > 0 and position_value not in positions:
            positions.append(position_value)
    return positions


def _is_paper_entity_label(value: object) -> bool:
    normalized = clean_text(str(value or "")).lower().replace(" ", "")
    return normalized in {"论文/arxiv", "论文", "arxiv"}


def _evidence_is_arxiv(evidence: dict) -> bool:
    source = str(evidence.get("source") or "").lower()
    url = str(evidence.get("url") or evidence.get("final_url") or "").lower()
    return source.startswith("arxiv") or "arxiv.org/" in url


def _run_integrity_errors(run_dir: Path, manifest: dict, *, allow_in_progress: bool) -> tuple[list[str], bool]:
    """Bind a publishable package to the run that produced it.

    Older fixture packages intentionally have neither a run state nor a run id,
    so they stay readable.  Every current automatic run writes both and is
    therefore held to the stricter no-human-review contract.
    """
    errors: list[str] = []
    state_path = run_dir / "run-state.json"
    # A package that contains a rendered final video is a real run, not a legacy
    # fixture — losing run-state.json (crash, partial copy, manual assembly) must
    # fail loudly instead of silently downgrading every strict gate.
    looks_rendered = (run_dir / "final.mp4").exists() and bool(manifest)
    strict_run = state_path.exists() or bool(manifest.get("run_id")) or looks_rendered
    if not strict_run:
        return errors, False
    if not state_path.exists():
        return ["automatic package is missing run-state.json"], True
    try:
        state = json.loads(state_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return [f"run-state parse failed: {exc}"], True
    run_id = str(state.get("run_id") or "")
    manifest_run_id = str(manifest.get("run_id") or "")
    if not run_id or not manifest_run_id or run_id != manifest_run_id:
        errors.append("manifest run_id does not match run-state.json")
    if not allow_in_progress and str(state.get("status") or "") != "QA_PASSED":
        errors.append(f"run-state status is {state.get('status') or 'missing'}, expected QA_PASSED")
    if not allow_in_progress:
        package_quality = manifest.get("package_quality") or {}
        if not package_quality or not package_quality.get("ok"):
            errors.append("automatic package quality gate is not passed")
        quality_gate = manifest.get("automatic_quality_gate") or {}
        if not quality_gate or quality_gate.get("ok") is not True:
            errors.append("automatic render quality gate is not passed")
    return errors, True


def verify_run_dir(run_dir: Path, *, allow_in_progress: bool = False) -> dict:
    run_dir = run_dir.resolve()
    errors: list[str] = []
    warnings: list[str] = []
    for name in REQUIRED:
        path = run_dir / name
        if not path.exists():
            errors.append(f"missing {name}")
        elif path.stat().st_size == 0:
            errors.append(f"empty {name}")

    manifest = {}
    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            errors.append(f"manifest parse failed: {exc}")
    integrity_errors, strict_run = _run_integrity_errors(run_dir, manifest if isinstance(manifest, dict) else {}, allow_in_progress=allow_in_progress)
    errors.extend(integrity_errors)
    audio_quality_path = run_dir / "audio-quality.json"
    video_path = run_dir / "final.mp4"
    if strict_run and video_path.exists():
        if not audio_quality_path.exists():
            errors.append("audio-quality.json is missing")
        else:
            try:
                audio_quality = json.loads(audio_quality_path.read_text(encoding="utf-8-sig"))
                from .audio_quality import media_sha256

                expected_media_hash = media_sha256(video_path)
                if audio_quality.get("media_sha256") != expected_media_hash:
                    errors.append("audio quality report is not bound to final.mp4")
                if audio_quality.get("ok") is not True:
                    errors.append("audio quality gate failed")
            except Exception as exc:
                errors.append(f"audio-quality.json parse failed: {exc}")
    counts = manifest.get("counts", {}) if isinstance(manifest, dict) else {}
    if counts:
        missing_screenshots = int(counts.get("evidence_screenshots_missing_required") or 0)
        if missing_screenshots:
            errors.append(f"{missing_screenshots} selected evidence item(s) require source screenshots but have none captured")

    selected = manifest.get("selected", []) if isinstance(manifest, dict) else []
    agent_policy = manifest.get("agent_policy") or {} if isinstance(manifest, dict) else {}
    if isinstance(agent_policy, dict) and agent_policy.get("required") is True:
        from .agent_workflow import verify_agent_edit_contract, verify_visual_agent_audit

        errors.extend(verify_agent_edit_contract(run_dir))
        errors.extend(verify_visual_agent_audit(run_dir))
    required_screenshot_hashes: set[str] = set()
    if isinstance(selected, list):
        for card_idx, card in enumerate(selected, 1):
            if not isinstance(card, dict):
                continue
            evidence_rows = [row for row in card.get("evidence") or [] if isinstance(row, dict)]
            if _is_paper_entity_label(card.get("entity")) and not any(_evidence_is_arxiv(row) for row in evidence_rows):
                errors.append(f"selected card {card_idx} labels a non-arXiv story as paper")
            for evidence_idx, evidence in enumerate(card.get("evidence") or [], 1):
                if not isinstance(evidence, dict):
                    continue
                required = str(evidence.get("screenshot_required") or "").lower() == "true"
                status = str(evidence.get("screenshot_status") or "")
                path = str(evidence.get("screenshot_path") or "")
                if required and (status != "captured" or not path):
                    errors.append(f"selected card {card_idx} evidence {evidence_idx} requires screenshot but status is {status or 'missing'}")
                    continue
                if required and str(evidence.get("screenshot_kind") or "") == "source_excerpt_card":
                    # A labelled excerpt card is an honest, on-screen-declared degraded
                    # visual, but it is not a genuine capture — keep it visible in the
                    # verify report so reviewers see which required proofs degraded.
                    failure = str(evidence.get("screenshot_capture_failure") or "unknown capture failure")
                    warnings.append(
                        f"selected card {card_idx} evidence {evidence_idx}: required screenshot degraded to a labelled excerpt card ({failure[:120]})"
                    )
                if required:
                    screenshot = Path(path)
                    if not screenshot.is_absolute():
                        screenshot = run_dir / screenshot
                    if not screenshot.exists() or not screenshot.is_file() or screenshot.stat().st_size == 0:
                        errors.append(f"selected card {card_idx} evidence {evidence_idx} screenshot file is missing or empty")
                    else:
                        from .audio_quality import media_sha256

                        required_screenshot_hashes.add(media_sha256(screenshot))
                        try:
                            screenshot.resolve().relative_to(run_dir)
                        except ValueError:
                            errors.append(f"selected card {card_idx} evidence {evidence_idx} screenshot is outside this run directory")

    if strict_run and required_screenshot_hashes:
        contract_path = run_dir / "render" / "evidence-visual-contract.json"
        if not contract_path.exists():
            errors.append("required evidence screenshots have no Remotion visibility contract")
        else:
            try:
                contract = json.loads(contract_path.read_text(encoding="utf-8-sig"))
                rendered_hashes = {
                    str(item.get("source_sha256") or "")
                    for item in contract.get("items") or []
                    if isinstance(item, dict) and item.get("required") is True and item.get("rendered") is True and item.get("asset")
                }
                missing_visible = required_screenshot_hashes - rendered_hashes
                if missing_visible:
                    errors.append(f"{len(missing_visible)} required evidence screenshot(s) were not rendered into a video scene")
                if not contract.get("visual_sha256"):
                    errors.append("evidence visibility contract is not bound to the rendered visual video")
                from .audio_quality import media_sha256

                if contract.get("final_media_sha256") != media_sha256(video_path):
                    errors.append("evidence visibility contract is not bound to final.mp4")
            except Exception as exc:
                errors.append(f"evidence visibility contract parse failed: {exc}")

    video = run_dir / "final.mp4"
    video_info: dict = {}
    duration = 0.0
    if video.exists() and shutil.which("ffprobe"):
        try:
            video_info = _ffprobe_json(["-show_streams", "-show_format", "-of", "json", str(video)])
            duration = float(video_info.get("format", {}).get("duration") or 0)
            streams = video_info.get("streams", [])
            v = next((x for x in streams if x.get("codec_type") == "video"), {})
            a = next((x for x in streams if x.get("codec_type") == "audio"), {})
            if v.get("pix_fmt") != "yuv420p":
                errors.append(f"video pix_fmt is {v.get('pix_fmt')}, expected yuv420p")
            if int(v.get("width") or 0) not in {1920, 3840}:
                errors.append(f"unexpected video width {v.get('width')}")
            if int(a.get("sample_rate") or 0) != 48000:
                errors.append(f"audio sample_rate is {a.get('sample_rate')}, expected 48000")
            if int(a.get("channels") or 0) != 2:
                errors.append(f"audio channels is {a.get('channels')}, expected 2")
        except Exception as exc:
            errors.append(f"ffprobe failed: {exc}")
    elif not shutil.which("ffprobe"):
        message = "ffprobe not found; skipped video stream verification"
        if strict_run:
            errors.append(message)
        else:
            warnings.append(message)

    srt = run_dir / "subtitles.srt"
    rows: list[tuple[float, float, str]] = []
    if srt.exists():
        rows = parse_srt(srt)
        if not rows:
            errors.append("subtitles.srt has no cues")
        selected_cards = int(counts.get("selected_cards") or 0) if counts else 0
        if selected_cards and len(rows) <= selected_cards + 2:
            errors.append("subtitles.srt is too sparse; expected sentence-level subtitles, not only segment captions")
        long_cues = [end - start for start, end, _text in rows if end - start > 8.0]
        if long_cues:
            errors.append(f"subtitles.srt has {len(long_cues)} cue(s) longer than 8 seconds")
        for idx, (start, end, _text) in enumerate(rows, 1):
            if end <= start:
                errors.append(f"srt cue {idx} has non-positive duration")
            if idx > 1 and start < rows[idx - 2][1] - 0.05:
                errors.append(f"srt cue {idx} overlaps previous cue")
        if duration and rows and rows[-1][1] > duration + 1.0:
            errors.append(f"srt ends at {rows[-1][1]:.2f}s but video duration is {duration:.2f}s")

    nav_rows = _parse_mmss_lines(run_dir / "pinned-comment.md")
    if nav_rows:
        for idx, (start, _label) in enumerate(nav_rows, 1):
            if idx > 1 and start < nav_rows[idx - 2][0]:
                errors.append(f"navigation time {idx} is not monotonic")
            if duration and start > duration + 1.0:
                errors.append(f"navigation time {idx} starts at {start:.2f}s but video duration is {duration:.2f}s")

    script_path = run_dir / "script.json"
    errors.extend(_script_load_errors(script_path))
    script_segments = _load_script_segments(script_path)
    manuscript_segments = _load_manuscript_segments(run_dir / "news-script.json")
    news_segments = [segment for segment in script_segments if str(segment.get("kind") or "news") == "news"]
    manifest_stories = {
        str(card.get("story_spec", {}).get("story_id") or ""): card
        for card in selected
        if isinstance(card, dict) and isinstance(card.get("story_spec"), dict)
    }
    agent_required = isinstance(agent_policy, dict) and agent_policy.get("required") is True
    for index, segment in enumerate(news_segments, 1):
        generation_path = str(segment.get("generation_path") or "")
        if generation_path != "structured_editorial_plan":
            errors.append(f"news segment {index} uses {generation_path or 'unknown'} instead of structured claim-evidence plan")
            continue
        story_id = str(segment.get("story_id") or "")
        claim_ids = [str(value) for value in segment.get("claim_ids") or [] if str(value)]
        card = manifest_stories.get(story_id)
        if not story_id or card is None:
            errors.append(f"news segment {index} story_id is missing from manifest")
            continue
        claims = {
            str(claim.get("claim_id") or ""): claim
            for claim in card.get("story_spec", {}).get("claims") or []
            if isinstance(claim, dict)
        }
        if not agent_required:
            plan = card.get("editorial_plan") or {}
            expected_narration = clean_text(str(plan.get("narration") or ""))
            expected_title = clean_text(str(plan.get("title") or ""))
            if expected_narration and clean_text(str(segment.get("text") or "")) != expected_narration:
                errors.append(f"news segment {index} narration does not match its manifest editorial plan")
            if expected_title and clean_text(str(segment.get("title") or "")) != expected_title:
                errors.append(f"news segment {index} title does not match its manifest editorial plan")
        if not claim_ids:
            errors.append(f"news segment {index} has no narrated claim_ids")
            continue
        for claim_id in claim_ids:
            claim = claims.get(claim_id)
            if claim is None:
                errors.append(f"news segment {index} claim_id {claim_id} is missing from manifest")
            elif not claim.get("renderable") or not claim.get("verifiable") or not claim.get("evidence_urls"):
                errors.append(f"news segment {index} claim_id {claim_id} has no public evidence support")
    if script_segments and not news_segments:
        errors.append("automatic briefing has no eligible news segments; do not publish an empty digest")
    render_info: dict = {}
    render_info_path = run_dir / "render-info.json"
    if strict_run and not render_info_path.exists():
        errors.append("automatic package is missing render-info.json")
    if render_info_path.exists():
        try:
            render_info = json.loads(render_info_path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            errors.append(f"render-info parse failed: {exc}")
            render_info = {}
        if render_info.get("renderer") == "remotion" and str(render_info.get("subtitle_burned") or "").lower() != "true":
            errors.append("remotion output did not report burned-in subtitles")
        if strict_run:
            tts_status = str(render_info.get("tts") or "")
            approved_voice = os.environ.get("BRIEFING_TTS_VOICE", "zh-CN-XiaoxiaoNeural")
            allowed_statuses = {f"edge-tts:{voice}" for voice in APPROVED_TTS_VOICES}
            if approved_voice in APPROVED_TTS_VOICES:
                allowed_statuses.add(f"edge-tts:{approved_voice}")
            if tts_status.startswith("indextts2:"):
                profile_id = tts_status.partition(":")[2].strip()
                if not profile_id:
                    errors.append("automatic package used an empty IndexTTS2 profile id")
                if str(render_info.get("tts_backend") or "") != "indextts2":
                    errors.append("automatic package IndexTTS2 status does not match its renderer backend")
                if str(render_info.get("tts_profile_id") or "") != profile_id:
                    errors.append("automatic package IndexTTS2 profile metadata does not match its TTS status")
                if str(render_info.get("tts_profile_publishable") or "").lower() != "true":
                    errors.append("automatic package used an IndexTTS2 profile that is not approved for publication")
            elif tts_status not in allowed_statuses:
                errors.append(f"automatic package used an unapproved TTS fallback: {tts_status or 'missing'}")
        if render_info.get("renderer") == "remotion" and rows:
            subtitle_contract = "\n".join(f"{start:.3f}|{end:.3f}|{text}" for start, end, text in rows)
            expected_hash = hashlib.sha256(subtitle_contract.encode("utf-8")).hexdigest()
            if str(render_info.get("subtitle_contract_sha256") or "") != expected_hash:
                errors.append("burned subtitle contract hash does not match subtitles.srt")
            if int(render_info.get("subtitle_cue_count") or 0) != len(rows):
                errors.append("burned subtitle cue count does not match subtitles.srt")
    expected_navigation = _expected_navigation(script_segments)
    errors.extend(_navigation_contract_errors(run_dir / "pinned-comment.md", expected_navigation))
    errors.extend(_navigation_contract_errors(run_dir / "bilibili.md", expected_navigation))
    review_mode = str(render_info.get("manuscript_mode") or "")
    errors.extend(
        verify_review_render_contract(
            run_dir,
            run_dir / "script.json",
            require_active_review=not render_info or review_mode in {"reviewed", "morning-reviewed"},
        )
    )
    if script_segments and manuscript_segments:
        if len(script_segments) != len(manuscript_segments):
            errors.append(f"news-script.json segment count {len(manuscript_segments)} does not match script.json {len(script_segments)}")
        else:
            for idx, (script_seg, manuscript_seg) in enumerate(zip(script_segments, manuscript_segments), 1):
                script_text = str(script_seg.get("text") or "")
                manuscript_text = str(manuscript_seg.get("text") or "")
                if script_text != manuscript_text:
                    errors.append(f"script segment {idx} text does not match news-script.json")
                    break
    for idx, seg in enumerate(script_segments, 1):
        if seg.get("kind") != "news":
            continue
        seg_duration = float(seg.get("duration") or 0)
        title = str(seg.get("title") or f"news {idx}")
        tier = str(seg.get("editorial_tier") or "headline")
        if tier == "brief" and seg_duration and not (BRIEF_SEGMENT_MIN_SECONDS <= seg_duration <= BRIEF_SEGMENT_MAX_SECONDS):
            errors.append(
                f"brief segment {idx} is {seg_duration:.1f}s, expected {BRIEF_SEGMENT_MIN_SECONDS:.0f}-{BRIEF_SEGMENT_MAX_SECONDS:.0f}s: {title[:60]}"
            )
        elif tier != "brief" and seg_duration and not (NEWS_SEGMENT_MIN_SECONDS <= seg_duration <= NEWS_SEGMENT_MAX_SECONDS):
            errors.append(
                f"news segment {idx} is {seg_duration:.1f}s, expected {NEWS_SEGMENT_MIN_SECONDS:.0f}-{NEWS_SEGMENT_MAX_SECONDS:.0f}s around {NEWS_SEGMENT_TARGET_SECONDS:.0f}s: {title[:60]}"
            )
    if strict_run and news_segments and str((manifest.get("package_quality") or {}).get("edition_mode") or manifest.get("edition_mode")) == "rolling_24h":
        edition_duration = max(float(segment.get("end") or 0) for segment in script_segments)
        brief_count = sum(str(segment.get("editorial_tier") or "headline") == "brief" for segment in news_segments)
        headline_count = len(news_segments) - brief_count
        hard_max = (
            ROLLING_EDITION_OVERHEAD_MAX_SECONDS
            + headline_count * NEWS_SEGMENT_MAX_SECONDS
            + brief_count * BRIEF_SEGMENT_MAX_SECONDS
        )
        recommended_max = (
            ROLLING_EDITION_OVERHEAD_RECOMMENDED_SECONDS
            + headline_count * 42.0
            + brief_count * 20.0
        )
        if edition_duration > hard_max:
            errors.append(
                f"rolling 24h edition is {edition_duration:.1f}s, above the dynamic hard maximum {hard_max:.0f}s for {len(news_segments)} stories"
            )
        elif edition_duration > recommended_max:
            warnings.append(
                f"rolling 24h edition is {edition_duration:.1f}s, above the density target {recommended_max:.0f}s for {len(news_segments)} stories; shorten copy instead of dropping qualified news"
            )
    errors.extend(_automatic_news_content_errors(script_segments))
    errors.extend(_signal_segment_errors(script_segments))
    errors.extend(_editorial_consistency_errors(script_segments))
    if strict_run:
        errors.extend(_evidence_visual_coverage_errors(script_segments))

    raw_english = [(label, text) for label, text in _iter_visible_script_text(run_dir / "script.json") if _looks_raw_english(text)]
    if raw_english:
        label, text = raw_english[0]
        errors.append(f"visible script text contains raw long English at {label}: {text[:80]}")

    raw_english_clause = [
        (label, clause)
        for label, text in _iter_visible_script_text(run_dir / "script.json") + _iter_public_text(run_dir)
        if (clause := _raw_english_clause(text))
    ]
    if raw_english_clause:
        label, clause = raw_english_clause[0]
        errors.append(f"visible/public text contains untranslated English clause at {label}: {clause[:80]}")

    visible_text = _iter_visible_script_text(run_dir / "script.json") + _iter_public_text(run_dir)
    errors.extend(_public_copy_integrity_errors(visible_text))
    ellipsis_hits = [(label, text) for label, text in visible_text if re.search(r"…|\.{3,}", text)]
    if ellipsis_hits:
        label, text = ellipsis_hits[0]
        errors.append(f"visible/public text contains an ellipsis at {label}: {text[:80]}")

    mojibake_hits = [(label, text) for label, text in visible_text if _looks_mojibake(text)]
    if mojibake_hits:
        label, text = mojibake_hits[0]
        errors.append(f"visible/public text contains mojibake at {label}: {text[:80]}")

    generic_hits = [
        (label, phrase)
        for label, text in visible_text
        for phrase in GENERIC_SCRIPT_PHRASES
        if phrase in text
    ]
    if generic_hits:
        label, phrase = generic_hits[0]
        errors.append(f"visible script text contains generic filler at {label}: {phrase}")

    forbidden_hits = [
        (label, phrase)
        for label, text in visible_text
        for phrase in FORBIDDEN_SCRIPT_PHRASES
        if phrase.lower() in text.lower()
    ]
    if forbidden_hits:
        label, phrase = forbidden_hits[0]
        errors.append(f"visible script text contains banned editorial phrase at {label}: {phrase}")

    pattern_hits = [
        (label, pattern.pattern)
        for label, text in visible_text
        for pattern in FORBIDDEN_SCRIPT_PATTERNS
        if pattern.search(text)
    ]
    if pattern_hits:
        label, pattern = pattern_hits[0]
        errors.append(f"visible script text contains banned editorial pattern at {label}: {pattern}")

    return {
        "ok": not errors,
        "run_dir": str(run_dir),
        "errors": errors,
        "warnings": warnings,
        "duration": duration,
        "manifest_counts": counts,
        "freshness_window": manifest.get("freshness_window", {}) if isinstance(manifest, dict) else {},
    }

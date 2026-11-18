from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

from briefing.core import (
    clean_text,
    file_sha256,
    has_cjk,
    is_paper_entity,
    looks_raw_english,
    read_json,
)

from .contract import (
    verify_agent_edit_contract,
    verify_review_render_contract,
    verify_visual_agent_audit,
)

REQUIRED_RUN_FILES: tuple[str, ...] = (
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
)

GENERIC_SCRIPT_PHRASES: tuple[str, ...] = (
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
)

FORBIDDEN_SCRIPT_PHRASES: tuple[str, ...] = (
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
)

FORBIDDEN_SCRIPT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"可以关注[^。！？!?；;\n]{0,40}是否"),
    re.compile(r"后面需要[^。！？!?；;\n]{0,40}优化"),
    re.compile(r"[?？]{2,}"),
)

MOJIBAKE_STRONG_MARKERS: tuple[str, ...] = (
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
)

MOJIBAKE_WEAK_MARKERS: tuple[str, ...] = ("銆", "锛", "鐨", "绋", "绾", "缁", "鏂", "浠", "璇", "鍊")

PUBLIC_COPY_DANGLING_END_RE = re.compile(r"(?:以及|包括|例如|但是|但|因此|从而|并且|同时|其中)\s*[，,：:；;、-]*$")
PUBLIC_COPY_PAIRS: tuple[tuple[str, str], ...] = (("“", "”"), ("‘", "’"), ("（", "）"), ("【", "】"), ("《", "》"))

RAW_ENGLISH_CLAUSE_RE = re.compile(r"[A-Za-z][A-Za-z0-9'’&/._-]*(?:\s+[A-Za-z][A-Za-z0-9'’&/._-]*){2,}")
RAW_ENGLISH_FUNCTION_WORDS: frozenset[str] = frozenset(
    {
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
)

NEWS_SEGMENT_TARGET_SECONDS = 32.0
NEWS_SEGMENT_MIN_SECONDS = 8.0
NEWS_SEGMENT_MAX_SECONDS = 65.0
BRIEF_SEGMENT_MIN_SECONDS = 6.0
BRIEF_SEGMENT_MAX_SECONDS = 36.0
ROLLING_EDITION_OVERHEAD_MAX_SECONDS = 75.0
ROLLING_EDITION_OVERHEAD_RECOMMENDED_SECONDS = 45.0
CHAPTER_SYNC_TOLERANCE_SECONDS = 1.2
MIN_NEWS_NARRATION_CHARS = 48
MIN_BRIEF_NARRATION_CHARS = 24
APPROVED_TTS_VOICES: frozenset[str] = frozenset({"zh-CN-XiaoxiaoNeural", "zh-CN-XiaoyiNeural"})

AUTOMATION_LOW_VALUE_PHRASES: tuple[str, ...] = (
    "入口待确认",
    "参数待公开",
    "页面变化",
    "模型页出现更新",
    "模型仓库更新",
    "只当模型页变动",
    "release metadata",
    "未写明具体变更",
    "只给出版本号",
)

AUTOMATION_COLLOQUIAL_MARKERS: tuple[str, ...] = (
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
)

TITLE_BARE_DOMAIN_RE = re.compile(
    r"(?<![A-Za-z0-9._@-])[A-Za-z0-9-]+\.(?:com|net|org|io|ai|co|cn|dev|app|me|xyz)\b",
    flags=re.I,
)
NARRATION_COLLOQUIAL_PARTICLE_RE = re.compile(r"[啊呀嘛][,，]")

AUTOMATION_FACTUAL_SIGNALS: tuple[str, ...] = (
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
)

SOCIAL_SIGNAL_DISCLAIMERS: dict[str, str] = {
    "official_personnel": "一线消息，未获官方公告确认",
    "community_rumour": "传闻/风向，未获官方公告确认",
}

SOCIAL_SIGNAL_FORBIDDEN_CLAIMS: tuple[str, ...] = (
    "已正式发布",
    "已经正式发布",
    "正式上线",
    "已经上线",
    "现已上线",
    "已开放使用",
)


def _srt_seconds(value: str) -> float:
    match = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)", value.strip())
    if not match:
        return 0.0
    hour, minute, sec, ms = (int(part) for part in match.groups())
    return hour * 3600 + minute * 60 + sec + ms / 1000.0


def parse_srt(text: str) -> list[dict[str, Any]]:
    """Parse SRT content into structured cue dicts `{index, start, end, duration, text}`."""
    raw = text.read_text(encoding="utf-8-sig") if isinstance(text, Path) else str(text or "")
    blocks = re.split(r"\r?\n\s*\r?\n", raw.lstrip("\ufeff").strip())
    cues: list[dict[str, Any]] = []
    for seq, block in enumerate(blocks, 1):
        lines = [line.strip("\ufeff").strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        timing_idx = 1 if len(lines) >= 3 and "-->" in lines[1] else (0 if "-->" in lines[0] else -1)
        if timing_idx < 0 or len(lines) <= timing_idx + 1:
            continue
        start_raw, end_raw = (part.strip() for part in lines[timing_idx].split("-->", 1))
        start = _srt_seconds(start_raw)
        end = _srt_seconds(end_raw)
        try:
            cue_index = int(lines[0]) if timing_idx == 1 else seq
        except ValueError:
            cue_index = seq
        cues.append(
            {
                "index": cue_index,
                "start": start,
                "end": end,
                "duration": round(end - start, 3),
                "text": "\n".join(lines[timing_idx + 1 :]),
            }
        )
    return cues


def repairable_news_positions(
    errors: list[str],
    script_payload: dict[str, Any] | list[dict[str, Any]] | None = None,
) -> set[int]:
    """Map copy-only quality failures back to 1-based selectable news positions."""
    repairable_markers = (
        "narration is too short for automatic publishing",
        "exposes a raw URL in video copy",
        "contains low-information copy",
        "title contains a bare domain",
        "title is too short to be a publishable headline",
        "contains colloquial scraped copy",
        "has no concrete capability, change, or user-impact fact",
        "visible script text contains",
        "visible/public text contains",
        "contains an ellipsis",
    )
    segment_indexes: set[int] = set()
    for error in errors:
        if not any(marker in error for marker in repairable_markers):
            continue
        match = re.search(r"\bsegment\s+(\d+)", error)
        if match:
            segment_indexes.add(int(match.group(1)))

    if not segment_indexes:
        return set()

    if script_payload is None:
        return set(segment_indexes)

    raw_segments = script_payload.get("segments") if isinstance(script_payload, dict) else script_payload
    segments = [seg for seg in raw_segments if isinstance(seg, dict)] if isinstance(raw_segments, list) else []
    if not segments:
        return set()

    positions: set[int] = set()
    for seg_idx in sorted(segment_indexes):
        if seg_idx < 1 or seg_idx > len(segments):
            continue
        seg = segments[seg_idx - 1]
        if str(seg.get("kind") or "news") != "news":
            continue
        try:
            pos = int(seg["position"])
        except (KeyError, TypeError, ValueError):
            pos = sum(1 for item in segments[:seg_idx] if str(item.get("kind") or "news") == "news")
        if pos > 0:
            positions.add(pos)
    return positions


def _ffprobe_json(args: list[str]) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {}
    out = subprocess.check_output([ffprobe, "-v", "error", *args], text=True, stderr=subprocess.DEVNULL)
    return json.loads(out or "{}")


def _parse_mmss_lines(path: Path) -> list[tuple[float, str]]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    rows: list[tuple[float, str]] = []
    for line in text.splitlines():
        match = re.match(r"\s*(\d{2,3}):(\d{2})\s+(.+)", line)
        if match:
            rows.append((int(match.group(1)) * 60 + int(match.group(2)), match.group(3).strip()))
    return rows


def _navigation_label_key(value: str) -> str:
    text = re.sub(r"^(?:开场|intro)\s*[｜|:：-]*\s*", "", str(value or "").lower().strip())
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)


def _expected_navigation(script_segments: list[dict[str, Any]]) -> list[tuple[float, str]]:
    rows: list[tuple[float, str]] = []
    for seg in script_segments:
        kind = str(seg.get("kind") or "news")
        if kind not in {"intro", "news"}:
            continue
        title = str(seg.get("title") or seg.get("caption") or "AI 动态")
        if kind == "intro":
            title = "开场｜" + title
        rows.append((float(seg.get("start") or 0.0), title))
    return rows


def _navigation_contract_errors(path: Path, expected: list[tuple[float, str]]) -> list[str]:
    if not expected:
        return []
    actual = _parse_mmss_lines(path)
    if len(actual) != len(expected):
        return [f"{path.name} navigation has {len(actual)} entries, expected {len(expected)} from script.json"]
    for idx, ((exp_time, exp_title), (act_time, act_title)) in enumerate(zip(expected, actual), 1):
        if abs(exp_time - act_time) > CHAPTER_SYNC_TOLERANCE_SECONDS:
            return [f"{path.name} navigation time {idx} is {act_time:.0f}s, expected {exp_time:.0f}s from script.json"]
        exp_key = _navigation_label_key(exp_title)
        act_key = _navigation_label_key(act_title)
        if exp_key and act_key and exp_key not in act_key and act_key not in exp_key:
            return [f"{path.name} navigation label {idx} does not match script.json"]
    return []


def _looks_raw_long_english(text: str, min_len: int = 34) -> bool:
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(compact) < min_len or has_cjk(compact):
        return False
    return looks_raw_english(compact)


def _raw_english_clause(text: str) -> str:
    for match in RAW_ENGLISH_CLAUSE_RE.finditer(str(text or "")):
        fragment = match.group(0).strip()
        raw_tokens = [tok.strip("'’&/._-") for tok in fragment.split()]
        tokens = [tok.lower() for tok in raw_tokens]
        fn_words = sum(tok in RAW_ENGLISH_FUNCTION_WORDS for tok in tokens)
        if len(tokens) >= 4 and fn_words >= 2:
            return fragment
        lowercase_terms = [tok for tok in raw_tokens if len(tok) >= 3 and tok[:1].islower()]
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
    return sum(1 for marker in MOJIBAKE_WEAK_MARKERS if marker in compact) >= 3


def _visible_text_rows(script_segments: list[dict[str, Any]]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for seg_idx, seg in enumerate(script_segments, 1):
        for key in ("title", "caption", "text"):
            rows.append((f"segment {seg_idx} {key}", str(seg.get(key) or "")))
        for card_idx, card in enumerate(seg.get("cards") or [], 1):
            if isinstance(card, dict):
                rows.append((f"segment {seg_idx} card {card_idx} title", str(card.get("title") or "")))
                rows.append((f"segment {seg_idx} card {card_idx} body", str(card.get("body") or "")))
        for page_idx, page in enumerate(seg.get("visual_pages") or [], 1):
            if not isinstance(page, dict):
                continue
            for key in ("title", "lead"):
                rows.append((f"segment {seg_idx} page {page_idx} {key}", str(page.get(key) or "")))
            for card_idx, card in enumerate(page.get("cards") or [], 1):
                if isinstance(card, dict):
                    rows.append((f"segment {seg_idx} page {page_idx} card {card_idx} title", str(card.get("title") or "")))
                    rows.append((f"segment {seg_idx} page {page_idx} card {card_idx} body", str(card.get("body") or "")))
    return rows


def _iter_public_text(run_dir: Path, cues: list[dict[str, Any]]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for name in ("bilibili.md", "pinned-comment.md", "news-script.md"):
        path = run_dir / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        for line_no, line in enumerate(text.splitlines(), 1):
            if line.strip():
                rows.append((f"{name}:{line_no}", line.strip()))
    for idx, cue in enumerate(cues, 1):
        rows.append((f"subtitles.srt cue {idx}", str(cue.get("text") or "")))
    return rows


def _script_load_result(script_path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    if not script_path.exists():
        return [], []
    try:
        payload = read_json(script_path)
    except Exception as exc:
        return [], [f"script.json parse failed: {exc}"]
    if not isinstance(payload, list):
        return [], ["script.json root must be a list"]
    invalid = [idx for idx, seg in enumerate(payload, 1) if not isinstance(seg, dict)]
    if invalid:
        return [seg for seg in payload if isinstance(seg, dict)], [f"script.json segment {invalid[0]} must be an object"]
    return payload, []


def _load_manuscript_segments(path: Path) -> list[dict[str, Any]]:
    payload = read_json(path, default=None)
    if not isinstance(payload, dict):
        return []
    segs = payload.get("segments")
    return [seg for seg in segs if isinstance(seg, dict)] if isinstance(segs, list) else []


def _editorial_consistency_errors(script_segments: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for _label, text in _visible_text_rows(script_segments):
        match = re.search(r"\b([A-Za-z][A-Za-z0-9._+-]{1,30}):(?=[\u4e00-\u9fff])", text)
        if match:
            errors.append(f"visible copy contains dangling colon fragment: {match.group(0)}")
            break

    intros = [seg for seg in script_segments if str(seg.get("kind") or "") == "intro"]
    news = [seg for seg in script_segments if str(seg.get("kind") or "news") == "news"]
    news_text = " ".join(str(seg.get(key) or "") for seg in news for key in ("title", "caption", "text")).upper()
    for intro in intros:
        intro_text = " ".join(str(intro.get(key) or "") for key in ("title", "caption", "text"))
        if not re.search(r"重点看|发布|推出|上线", intro_text):
            continue
        for match in re.finditer(r"\bGPT[- ]?[A-Za-z0-9]+(?:\.[A-Za-z0-9]+)*\b", intro_text, flags=re.I):
            model = re.sub(r"\s+", "-", match.group(0)).upper()
            if model not in news_text:
                errors.append(f"intro mentions {model} but no news segment covers it")
    return errors


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


def _automatic_news_content_errors(script_segments: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for idx, seg in enumerate(script_segments, 1):
        if str(seg.get("kind") or "news") != "news":
            continue
        title = str(seg.get("title") or "").strip()
        narration = str(seg.get("text") or "").strip()
        visible = " ".join(
            [
                title,
                str(seg.get("caption") or ""),
                narration,
                *(
                    f"{card.get('title') or ''} {card.get('body') or ''}"
                    for card in seg.get("cards") or []
                    if isinstance(card, dict)
                ),
            ]
        )
        compact_narration = re.sub(r"\s+", "", narration)
        sensational = re.search(r"王炸|杀疯了|刷爆|震撼|炸裂|AGI\s*真的来了|程序员失业|彻底取代|颠覆", visible, flags=re.I)
        if sensational:
            errors.append(f"news segment {idx} contains sensational copy requiring editorial review: {sensational.group(0)}")
        tier = str(seg.get("editorial_tier") or "headline")
        min_chars = MIN_BRIEF_NARRATION_CHARS if tier == "brief" else MIN_NEWS_NARRATION_CHARS
        if len(compact_narration) < min_chars:
            errors.append(f"news segment {idx} narration is too short for automatic publishing: {title[:60]}")
        if narration and narration[-1] not in "。！？!?」』”’\"":
            errors.append(f"news segment {idx} narration ends with a truncated sentence fragment: {narration[-24:]}")
        if re.search(r"https?://\S+|www\.\S+", visible, flags=re.I):
            errors.append(f"news segment {idx} exposes a raw URL in video copy: {title[:60]}")
        compact_title = re.sub(r"\s+", "", re.sub(r"^一线消息[|｜]|^传闻/风向[|｜]", "", title))
        if compact_title and len(compact_title) < 6:
            errors.append(f"news segment {idx} title is too short to be a publishable headline: {title[:60]}")
        if domain_match := TITLE_BARE_DOMAIN_RE.search(title):
            errors.append(f"news segment {idx} title contains a bare domain: {domain_match.group(0)}")
        lowered = visible.lower()
        colloquial = next((m for m in AUTOMATION_COLLOQUIAL_MARKERS if m.lower() in lowered), "")
        if not colloquial and (particle_match := NARRATION_COLLOQUIAL_PARTICLE_RE.search(narration)):
            colloquial = particle_match.group(0)
        if colloquial:
            errors.append(f"news segment {idx} contains colloquial scraped copy: {colloquial}")
        if low_val := next((p for p in AUTOMATION_LOW_VALUE_PHRASES if p.lower() in lowered), ""):
            errors.append(f"news segment {idx} contains low-information copy: {low_val}")
        has_signal = bool(re.search(r"\d+(?:\.\d+)?\s*(?:b|m|k|%|倍|项|个|token|tokens|版本)?\b", visible, flags=re.I))
        has_signal = has_signal or any(sig.lower() in lowered for sig in AUTOMATION_FACTUAL_SIGNALS)
        if not has_signal:
            errors.append(f"news segment {idx} has no concrete capability, change, or user-impact fact: {title[:60]}")
    return errors


def _signal_segment_errors(script_segments: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for idx, seg in enumerate(script_segments, 1):
        signal_kind = str(seg.get("signal_kind") or "")
        if not signal_kind:
            continue
        expected = SOCIAL_SIGNAL_DISCLAIMERS.get(signal_kind)
        if not expected:
            errors.append(f"news segment {idx} has an unknown social signal kind: {signal_kind}")
            continue
        parts = [str(seg.get(k) or "") for k in ("title", "caption", "text", "body", "disclaimer")]
        for card in seg.get("cards") or []:
            if isinstance(card, dict):
                parts.extend([str(card.get("title") or ""), str(card.get("body") or "")])
        for page in seg.get("visual_pages") or []:
            if not isinstance(page, dict):
                continue
            parts.extend([str(page.get("title") or ""), str(page.get("lead") or "")])
            for card in page.get("cards") or []:
                if isinstance(card, dict):
                    parts.extend([str(card.get("title") or ""), str(card.get("body") or "")])
        visible = " ".join(parts)
        if expected not in visible:
            errors.append(f"news segment {idx} {signal_kind} is missing the required visible disclaimer")
        if claim := next((p for p in SOCIAL_SIGNAL_FORBIDDEN_CLAIMS if p in visible), ""):
            errors.append(f"news segment {idx} {signal_kind} states an unconfirmed signal as fact: {claim}")
        evidence = [item for item in seg.get("evidence") or [] if isinstance(item, dict)]
        has_screenshot = any(
            str(item.get("screenshot_required") or "").lower() == "true"
            and str(item.get("screenshot_status") or "") == "captured"
            and str(item.get("screenshot_path") or "")
            and str(item.get("screenshot_kind") or "") != "source_excerpt_card"
            for item in evidence
        )
        if not has_screenshot:
            errors.append(f"news segment {idx} {signal_kind} has no captured source-post screenshot")
    return errors


def _evidence_visual_coverage_errors(script_segments: list[dict[str, Any]]) -> list[str]:
    news = [seg for seg in script_segments if str(seg.get("kind") or "news") == "news"]
    if not news:
        return []
    covered = sum(
        1
        for seg in news
        if any(
            isinstance(page, dict)
            and str(page.get("kind") or "") == "evidence"
            and isinstance(page.get("evidenceVisual"), dict)
            and bool(page["evidenceVisual"].get("asset") or page["evidenceVisual"].get("image"))
            for page in seg.get("visual_pages") or []
        )
    )
    minimum = max(1, (len(news) + 5) // 6)
    if covered < minimum:
        return [
            f"evidence screenshot coverage is {covered}/{len(news)} stories; "
            f"rolling news video requires at least {minimum} source pop-up(s)"
        ]
    return []


def _evidence_is_arxiv(evidence: dict[str, Any]) -> bool:
    source = str(evidence.get("source") or "").lower()
    url = str(evidence.get("url") or evidence.get("final_url") or "").lower()
    return source.startswith("arxiv") or "arxiv.org/" in url


def _run_integrity_errors(run_dir: Path, manifest: dict[str, Any], *, allow_in_progress: bool) -> tuple[list[str], bool]:
    errors: list[str] = []
    state_path = run_dir / "run-state.json"
    looks_rendered = (run_dir / "final.mp4").exists() and bool(manifest)
    strict_run = state_path.exists() or bool(manifest.get("run_id")) or looks_rendered
    if not strict_run:
        return errors, False
    if not state_path.exists():
        return ["automatic package is missing run-state.json"], True
    try:
        state = read_json(state_path)
    except Exception as exc:
        return [f"run-state parse failed: {exc}"], True
    if not isinstance(state, dict):
        return ["run-state root must be an object"], True

    run_id = str(state.get("run_id") or "")
    manifest_run_id = str(manifest.get("run_id") or "")
    if not run_id or not manifest_run_id or run_id != manifest_run_id:
        errors.append("manifest run_id does not match run-state.json")
    if not allow_in_progress and str(state.get("status") or "") != "QA_PASSED":
        errors.append(f"run-state status is {state.get('status') or 'missing'}, expected QA_PASSED")
    if not allow_in_progress:
        pkg_quality = manifest.get("package_quality") or {}
        if not pkg_quality or not pkg_quality.get("ok"):
            errors.append("automatic package quality gate is not passed")
        quality_gate = manifest.get("automatic_quality_gate") or {}
        if not quality_gate or quality_gate.get("ok") is not True:
            errors.append("automatic render quality gate is not passed")
    return errors, True


def verify_run_dir(run_dir: Path, *, allow_in_progress: bool = False) -> dict[str, Any]:
    """Run all 6 core QA gates on a briefing output directory."""
    run_dir = Path(run_dir).resolve()
    errors: list[str] = []
    warnings: list[str] = []

    # Gate 1: 12 required non-empty run files
    for name in REQUIRED_RUN_FILES:
        path = run_dir / name
        if not path.exists():
            errors.append(f"missing {name}")
        elif path.stat().st_size == 0:
            errors.append(f"empty {name}")

    # Gate 2: run-state.json & manifest.json integrity + optional agent_policy contract checks
    manifest: dict[str, Any] = {}
    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists():
        try:
            loaded_manifest = read_json(manifest_path)
            manifest = loaded_manifest if isinstance(loaded_manifest, dict) else {}
        except Exception as exc:
            errors.append(f"manifest parse failed: {exc}")

    integrity_errors, strict_run = _run_integrity_errors(run_dir, manifest, allow_in_progress=allow_in_progress)
    errors.extend(integrity_errors)

    agent_policy = manifest.get("agent_policy") or {}
    agent_required = isinstance(agent_policy, dict) and agent_policy.get("required") is True
    if agent_required:
        errors.extend(verify_agent_edit_contract(run_dir))
        errors.extend(verify_visual_agent_audit(run_dir))

    # Gate 3 (Part A): audio-quality.json & ffprobe stream validation
    video_path = run_dir / "final.mp4"
    audio_quality_path = run_dir / "audio-quality.json"
    if strict_run and video_path.exists():
        if not audio_quality_path.exists():
            errors.append("audio-quality.json is missing")
        else:
            try:
                audio_quality = read_json(audio_quality_path)
                if not isinstance(audio_quality, dict):
                    raise ValueError("audio-quality.json root must be an object")
                if audio_quality.get("media_sha256") != file_sha256(video_path):
                    errors.append("audio quality report is not bound to final.mp4")
                if audio_quality.get("ok") is not True:
                    errors.append("audio quality gate failed")
            except Exception as exc:
                errors.append(f"audio-quality.json parse failed: {exc}")

    duration = 0.0
    if video_path.exists() and shutil.which("ffprobe"):
        try:
            video_info = _ffprobe_json(["-show_streams", "-show_format", "-of", "json", str(video_path)])
            duration = float(video_info.get("format", {}).get("duration") or 0.0)
            streams = video_info.get("streams", [])
            v_stream = next((x for x in streams if x.get("codec_type") == "video"), {})
            a_stream = next((x for x in streams if x.get("codec_type") == "audio"), {})
            if v_stream.get("pix_fmt") != "yuv420p":
                errors.append(f"video pix_fmt is {v_stream.get('pix_fmt')}, expected yuv420p")
            if int(v_stream.get("width") or 0) not in {1920, 3840}:
                errors.append(f"unexpected video width {v_stream.get('width')}")
            if int(a_stream.get("sample_rate") or 0) != 48000:
                errors.append(f"audio sample_rate is {a_stream.get('sample_rate')}, expected 48000")
            if int(a_stream.get("channels") or 0) != 2:
                errors.append(f"audio channels is {a_stream.get('channels')}, expected 2")
        except Exception as exc:
            errors.append(f"ffprobe failed: {exc}")
    elif not shutil.which("ffprobe"):
        msg = "ffprobe not found; skipped video stream verification"
        if strict_run:
            errors.append(msg)
        else:
            warnings.append(msg)

    # Gate 4: Evidence screenshot & social-signal visibility contract
    counts = manifest.get("counts") if isinstance(manifest.get("counts"), dict) else {}
    if counts:
        missing_screenshots = int(counts.get("evidence_screenshots_missing_required") or 0)
        if missing_screenshots:
            errors.append(f"{missing_screenshots} selected evidence item(s) require source screenshots but have none captured")

    selected = manifest.get("selected") if isinstance(manifest.get("selected"), list) else []
    required_screenshot_hashes: set[str] = set()
    for card_idx, card in enumerate(selected, 1):
        if not isinstance(card, dict):
            continue
        ev_rows = [row for row in card.get("evidence") or [] if isinstance(row, dict)]
        if is_paper_entity(str(card.get("entity") or "")) and not any(_evidence_is_arxiv(row) for row in ev_rows):
            errors.append(f"selected card {card_idx} labels a non-arXiv story as paper")
        for ev_idx, ev in enumerate(card.get("evidence") or [], 1):
            if not isinstance(ev, dict):
                continue
            required = str(ev.get("screenshot_required") or "").lower() == "true"
            status = str(ev.get("screenshot_status") or "")
            raw_path = str(ev.get("screenshot_path") or "")
            if required and (status != "captured" or not raw_path):
                errors.append(f"selected card {card_idx} evidence {ev_idx} requires screenshot but status is {status or 'missing'}")
                continue
            if required and str(ev.get("screenshot_kind") or "") == "source_excerpt_card":
                failure = str(ev.get("screenshot_capture_failure") or "unknown capture failure")
                warnings.append(
                    f"selected card {card_idx} evidence {ev_idx}: required screenshot degraded to a labelled excerpt card ({failure[:120]})"
                )
            if required:
                screenshot = Path(raw_path)
                if not screenshot.is_absolute():
                    screenshot = run_dir / screenshot
                if not screenshot.is_file() or screenshot.stat().st_size == 0:
                    errors.append(f"selected card {card_idx} evidence {ev_idx} screenshot file is missing or empty")
                else:
                    required_screenshot_hashes.add(file_sha256(screenshot))
                    try:
                        screenshot.resolve().relative_to(run_dir)
                    except ValueError:
                        errors.append(f"selected card {card_idx} evidence {ev_idx} screenshot is outside this run directory")

    if strict_run and required_screenshot_hashes:
        vis_contract_path = run_dir / "render" / "evidence-visual-contract.json"
        if not vis_contract_path.exists():
            errors.append("required evidence screenshots have no Remotion visibility contract")
        else:
            try:
                vis_contract = read_json(vis_contract_path)
                if not isinstance(vis_contract, dict):
                    raise ValueError("evidence-visual-contract.json root must be an object")
                rendered_hashes = {
                    str(item.get("source_sha256") or "")
                    for item in vis_contract.get("items") or []
                    if isinstance(item, dict) and item.get("required") is True and item.get("rendered") is True and item.get("asset")
                }
                missing_vis = required_screenshot_hashes - rendered_hashes
                if missing_vis:
                    errors.append(f"{len(missing_vis)} required evidence screenshot(s) were not rendered into a video scene")
                if not vis_contract.get("visual_sha256"):
                    errors.append("evidence visibility contract is not bound to the rendered visual video")
                if video_path.exists() and vis_contract.get("final_media_sha256") != file_sha256(video_path):
                    errors.append("evidence visibility contract is not bound to final.mp4")
            except Exception as exc:
                errors.append(f"evidence visibility contract parse failed: {exc}")

    # Gate 5 (Part A): SRT density & non-overlap + navigation monotonicity
    srt_path = run_dir / "subtitles.srt"
    cues: list[dict[str, Any]] = []
    if srt_path.exists():
        cues = parse_srt(srt_path.read_text(encoding="utf-8-sig"))
        if not cues:
            errors.append("subtitles.srt has no cues")
        selected_cards = int(counts.get("selected_cards") or 0) if counts else 0
        if selected_cards and len(cues) <= selected_cards + 2:
            errors.append("subtitles.srt is too sparse; expected sentence-level subtitles, not only segment captions")
        long_cues = [cue["end"] - cue["start"] for cue in cues if cue["end"] - cue["start"] > 8.0]
        if long_cues:
            errors.append(f"subtitles.srt has {len(long_cues)} cue(s) longer than 8 seconds")
        for idx, cue in enumerate(cues, 1):
            if cue["end"] <= cue["start"]:
                errors.append(f"srt cue {idx} has non-positive duration")
            if idx > 1 and cue["start"] < cues[idx - 2]["end"] - 0.05:
                errors.append(f"srt cue {idx} overlaps previous cue")
        if duration and cues and cues[-1]["end"] > duration + 1.0:
            errors.append(f"srt ends at {cues[-1]['end']:.2f}s but video duration is {duration:.2f}s")

    nav_rows = _parse_mmss_lines(run_dir / "pinned-comment.md")
    for idx, (start, _label) in enumerate(nav_rows, 1):
        if idx > 1 and start < nav_rows[idx - 2][0]:
            errors.append(f"navigation time {idx} is not monotonic")
        if duration and start > duration + 1.0:
            errors.append(f"navigation time {idx} starts at {start:.2f}s but video duration is {duration:.2f}s")

    # Gate 5 (Part B): Claim-evidence, manuscript & chapter timeline sync
    script_path = run_dir / "script.json"
    script_segments, script_errors = _script_load_result(script_path)
    errors.extend(script_errors)
    manuscript_segments = _load_manuscript_segments(run_dir / "news-script.json")
    news_segments = [seg for seg in script_segments if str(seg.get("kind") or "news") == "news"]
    manifest_stories = {
        str(card.get("story_spec", {}).get("story_id") or ""): card
        for card in selected
        if isinstance(card, dict) and isinstance(card.get("story_spec"), dict)
    }

    for idx, seg in enumerate(news_segments, 1):
        gen_path = str(seg.get("generation_path") or "")
        if gen_path != "structured_editorial_plan":
            errors.append(f"news segment {idx} uses {gen_path or 'unknown'} instead of structured claim-evidence plan")
            continue
        story_id = str(seg.get("story_id") or "")
        claim_ids = [str(val) for val in seg.get("claim_ids") or [] if str(val)]
        card = manifest_stories.get(story_id)
        if not story_id or card is None:
            errors.append(f"news segment {idx} story_id is missing from manifest")
            continue
        claims = {
            str(claim.get("claim_id") or ""): claim
            for claim in card.get("story_spec", {}).get("claims") or []
            if isinstance(claim, dict)
        }
        if not agent_required:
            plan = card.get("editorial_plan") or {}
            exp_narration = clean_text(str(plan.get("narration") or ""))
            exp_title = clean_text(str(plan.get("title") or ""))
            if exp_narration and clean_text(str(seg.get("text") or "")) != exp_narration:
                errors.append(f"news segment {idx} narration does not match its manifest editorial plan")
            if exp_title and clean_text(str(seg.get("title") or "")) != exp_title:
                errors.append(f"news segment {idx} title does not match its manifest editorial plan")
        if not claim_ids:
            errors.append(f"news segment {idx} has no narrated claim_ids")
            continue
        for cid in claim_ids:
            claim = claims.get(cid)
            if claim is None:
                errors.append(f"news segment {idx} claim_id {cid} is missing from manifest")
            elif not claim.get("renderable") or not claim.get("verifiable") or not claim.get("evidence_urls"):
                errors.append(f"news segment {idx} claim_id {cid} has no public evidence support")

    if script_segments and not news_segments:
        errors.append("automatic briefing has no eligible news segments; do not publish an empty digest")

    # Gate 3 (Part B): render-info.json Remotion burned subtitles & publishable TTS backend
    render_info: dict[str, Any] = {}
    render_info_path = run_dir / "render-info.json"
    if strict_run and not render_info_path.exists():
        errors.append("automatic package is missing render-info.json")
    if render_info_path.exists():
        try:
            loaded_render_info = read_json(render_info_path)
            render_info = loaded_render_info if isinstance(loaded_render_info, dict) else {}
        except Exception as exc:
            errors.append(f"render-info parse failed: {exc}")
            render_info = {}

        if render_info.get("renderer") == "remotion" and str(render_info.get("subtitle_burned") or "").lower() != "true":
            errors.append("remotion output did not report burned-in subtitles")
        if strict_run:
            tts_status = str(render_info.get("tts") or "")
            approved_voice = os.environ.get("BRIEFING_TTS_VOICE", "zh-CN-XiaoxiaoNeural")
            allowed_statuses = {f"edge-tts:{v}" for v in APPROVED_TTS_VOICES}
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

        if render_info.get("renderer") == "remotion" and cues:
            subtitle_contract = "\n".join(f"{cue['start']:.3f}|{cue['end']:.3f}|{cue['text']}" for cue in cues)
            expected_hash = hashlib.sha256(subtitle_contract.encode("utf-8")).hexdigest()
            if str(render_info.get("subtitle_contract_sha256") or "") != expected_hash:
                errors.append("burned subtitle contract hash does not match subtitles.srt")
            if int(render_info.get("subtitle_cue_count") or 0) != len(cues):
                errors.append("burned subtitle cue count does not match subtitles.srt")

    expected_nav = _expected_navigation(script_segments)
    errors.extend(_navigation_contract_errors(run_dir / "pinned-comment.md", expected_nav))
    errors.extend(_navigation_contract_errors(run_dir / "bilibili.md", expected_nav))

    review_mode = str(render_info.get("manuscript_mode") or "")
    errors.extend(
        verify_review_render_contract(
            run_dir,
            script_segments,
            require_active_review=not render_info or review_mode in {"reviewed", "morning-reviewed"},
        )
    )

    if script_segments and manuscript_segments:
        if len(script_segments) != len(manuscript_segments):
            errors.append(
                f"news-script.json segment count {len(manuscript_segments)} does not match script.json {len(script_segments)}"
            )
        else:
            for idx, (s_seg, m_seg) in enumerate(zip(script_segments, manuscript_segments), 1):
                if str(s_seg.get("text") or "") != str(m_seg.get("text") or ""):
                    errors.append(f"script segment {idx} text does not match news-script.json")
                    break

    for idx, seg in enumerate(script_segments, 1):
        if seg.get("kind") != "news":
            continue
        seg_dur = float(seg.get("duration") or 0.0)
        title = str(seg.get("title") or f"news {idx}")
        tier = str(seg.get("editorial_tier") or "headline")
        if tier == "brief" and seg_dur and not (BRIEF_SEGMENT_MIN_SECONDS <= seg_dur <= BRIEF_SEGMENT_MAX_SECONDS):
            errors.append(
                f"brief segment {idx} is {seg_dur:.1f}s, expected {BRIEF_SEGMENT_MIN_SECONDS:.0f}-{BRIEF_SEGMENT_MAX_SECONDS:.0f}s: {title[:60]}"
            )
        elif tier != "brief" and seg_dur and not (NEWS_SEGMENT_MIN_SECONDS <= seg_dur <= NEWS_SEGMENT_MAX_SECONDS):
            errors.append(
                f"news segment {idx} is {seg_dur:.1f}s, expected {NEWS_SEGMENT_MIN_SECONDS:.0f}-{NEWS_SEGMENT_MAX_SECONDS:.0f}s around {NEWS_SEGMENT_TARGET_SECONDS:.0f}s: {title[:60]}"
            )

    edition_mode = str((manifest.get("package_quality") or {}).get("edition_mode") or manifest.get("edition_mode") or "")
    if strict_run and news_segments and edition_mode == "rolling_24h":
        edition_dur = max(float(seg.get("end") or 0.0) for seg in script_segments)
        brief_count = sum(str(seg.get("editorial_tier") or "headline") == "brief" for seg in news_segments)
        headline_count = len(news_segments) - brief_count
        hard_max = ROLLING_EDITION_OVERHEAD_MAX_SECONDS + headline_count * NEWS_SEGMENT_MAX_SECONDS + brief_count * BRIEF_SEGMENT_MAX_SECONDS
        rec_max = ROLLING_EDITION_OVERHEAD_RECOMMENDED_SECONDS + headline_count * 42.0 + brief_count * 20.0
        if edition_dur > hard_max:
            errors.append(
                f"rolling 24h edition is {edition_dur:.1f}s, above the dynamic hard maximum {hard_max:.0f}s for {len(news_segments)} stories"
            )
        elif edition_dur > rec_max:
            warnings.append(
                f"rolling 24h edition is {edition_dur:.1f}s, above the density target {rec_max:.0f}s for {len(news_segments)} stories; shorten copy instead of dropping qualified news"
            )

    # Gate 6: Copy hygiene
    errors.extend(_automatic_news_content_errors(script_segments))
    errors.extend(_signal_segment_errors(script_segments))
    errors.extend(_editorial_consistency_errors(script_segments))
    if strict_run:
        errors.extend(_evidence_visual_coverage_errors(script_segments))

    script_text_rows = _visible_text_rows(script_segments)
    public_text_rows = _iter_public_text(run_dir, cues)
    all_visible_rows = script_text_rows + public_text_rows

    if raw_eng := [(lbl, txt) for lbl, txt in script_text_rows if _looks_raw_long_english(txt)]:
        lbl, txt = raw_eng[0]
        errors.append(f"visible script text contains raw long English at {lbl}: {txt[:80]}")

    if raw_clause := [(lbl, clause) for lbl, txt in all_visible_rows if (clause := _raw_english_clause(txt))]:
        lbl, clause = raw_clause[0]
        errors.append(f"visible/public text contains untranslated English clause at {lbl}: {clause[:80]}")

    errors.extend(_public_copy_integrity_errors(all_visible_rows))

    if ellipsis_hits := [(lbl, txt) for lbl, txt in all_visible_rows if re.search(r"…|\.{3,}", txt)]:
        lbl, txt = ellipsis_hits[0]
        errors.append(f"visible/public text contains an ellipsis at {lbl}: {txt[:80]}")

    if mojibake_hits := [(lbl, txt) for lbl, txt in all_visible_rows if _looks_mojibake(txt)]:
        lbl, txt = mojibake_hits[0]
        errors.append(f"visible/public text contains mojibake at {lbl}: {txt[:80]}")

    if generic_hits := [(lbl, phr) for lbl, txt in all_visible_rows for phr in GENERIC_SCRIPT_PHRASES if phr in txt]:
        lbl, phr = generic_hits[0]
        errors.append(f"visible script text contains generic filler at {lbl}: {phr}")

    if forbidden_hits := [
        (lbl, phr) for lbl, txt in all_visible_rows for phr in FORBIDDEN_SCRIPT_PHRASES if phr.lower() in txt.lower()
    ]:
        lbl, phr = forbidden_hits[0]
        errors.append(f"visible script text contains banned editorial phrase at {lbl}: {phr}")

    if pattern_hits := [
        (lbl, pat.pattern) for lbl, txt in all_visible_rows for pat in FORBIDDEN_SCRIPT_PATTERNS if pat.search(txt)
    ]:
        lbl, pat = pattern_hits[0]
        errors.append(f"visible script text contains banned editorial pattern at {lbl}: {pat}")

    return {
        "ok": not errors,
        "run_dir": str(run_dir),
        "errors": errors,
        "warnings": warnings,
        "duration": duration,
        "manifest_counts": counts,
        "freshness_window": manifest.get("freshness_window", {}) if isinstance(manifest.get("freshness_window"), dict) else {},
    }

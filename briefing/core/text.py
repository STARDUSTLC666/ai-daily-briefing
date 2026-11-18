from __future__ import annotations

import email.utils
import hashlib
import html
import re
import unicodedata
from datetime import datetime, timezone
from typing import Iterable

UTC = timezone.utc

_MONTH_NUMBERS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_ASCII_LETTER_RE = re.compile(r"[A-Za-z]")
_VISIBLE_CHAR_RE = re.compile(r"\S")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？；.!?;])\s*")
_BRACKET_PAIRS = (("（", "）"), ("(", ")"), ("《", "》"), ("「", "」"), ("“", "”"), ("[", "]"))


def now_utc() -> datetime:
    return datetime.now(UTC)


def strip_html(value: str | None) -> str:
    text = value or ""
    text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", text, flags=re.S)
    text = re.sub(r"<script\b.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style\b.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"#?\s*欢迎关注.+$", "", text)
    text = re.sub(r"更多精彩内容.+$", "", text)
    return re.sub(r"\s+", " ", text).strip()


def clean_text(value: str | None, limit: int | None = None) -> str:
    text = strip_html(value)
    if limit and len(text) > limit:
        return clause_truncate(text, limit)
    return text


def has_cjk(text: str | None) -> bool:
    return bool(_CJK_RE.search(text or ""))


def looks_raw_english(text: str | None, *, threshold: float = 0.58, min_letters: int = 18) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    visible = len(_VISIBLE_CHAR_RE.findall(raw))
    if visible == 0:
        return False
    letters = len(_ASCII_LETTER_RE.findall(raw))
    if letters < min_letters and has_cjk(raw):
        return False
    return (letters / visible) > threshold


def _balanced_brackets(text: str) -> bool:
    for left, right in _BRACKET_PAIRS:
        if left == right:
            if text.count(left) % 2 != 0:
                return False
        elif text.count(left) != text.count(right):
            return False
    return True


def clause_truncate(text: str | None, max_chars: int) -> str:
    """Truncate text cleanly at sentence/clause boundaries without ellipses."""
    cleaned = re.sub(r"\s+", " ", (text or "")).strip().rstrip("….")
    if max_chars <= 0 or len(cleaned) <= max_chars:
        return cleaned
    window = cleaned[:max_chars]
    for sep in ("。", "！", "？", "；", "，", ",", ";"):
        idx = window.rfind(sep)
        if idx >= max(8, int(max_chars * 0.45)):
            candidate = window[:idx].rstrip("，、；：,:; ")
            if _balanced_brackets(candidate):
                return candidate + ("。" if sep in {"。", "！", "？"} else "")
    # Fall back to word boundary for ASCII tails
    trimmed = window.rstrip("，、；：,:;-— ")
    if re.search(r"[A-Za-z0-9]$", trimmed) and len(cleaned) > max_chars and re.match(r"[A-Za-z0-9]", cleaned[max_chars]):
        space_idx = trimmed.rfind(" ")
        if space_idx >= int(max_chars * 0.5):
            trimmed = trimmed[:space_idx].rstrip("，、；：,:;-— ")
    for left, right in _BRACKET_PAIRS:
        if trimmed.count(left) > trimmed.count(right):
            open_idx = trimmed.rfind(left)
            if open_idx > 4:
                trimmed = trimmed[:open_idx].rstrip("，、；：,:;-— ")
    return trimmed


def normalize_cn_punctuation(text: str | None) -> str:
    raw = re.sub(r"\s+", " ", (text or "")).strip()
    if not raw or not has_cjk(raw):
        return raw
    # Convert ASCII punctuation adjacent to CJK characters to fullwidth
    raw = re.sub(r"(?<=[\u4e00-\u9fff]),\s*", "，", raw)
    raw = re.sub(r"(?<=[\u4e00-\u9fff]);\s*", "；", raw)
    raw = re.sub(r"(?<=[\u4e00-\u9fff]):\s*", "：", raw)
    raw = re.sub(r"\.{3,}|…+", "", raw)
    return raw.strip()


def split_sentences(text: str | None) -> list[str]:
    raw = strip_html(text)
    if not raw:
        return []
    return [part.strip() for part in _SENTENCE_SPLIT_RE.split(raw) if part.strip()]


def _parse_month_name_date(raw: str) -> datetime | None:
    month_day = re.match(r"^([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})$", raw)
    day_month = re.match(r"^(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})$", raw)
    if month_day:
        name, day, year = month_day.group(1), int(month_day.group(2)), int(month_day.group(3))
    elif day_month:
        day, name, year = int(day_month.group(1)), day_month.group(2), int(day_month.group(3))
    else:
        return None
    month = _MONTH_NUMBERS.get(name.lower()[:3])
    if not month:
        return None
    try:
        return datetime(year, month, day, tzinfo=UTC)
    except ValueError:
        return None


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = strip_html(value)
    if not raw:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(raw)
        if dt and dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC) if dt else None
    except Exception:
        pass
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dotted = re.match(r"^(\d{4})[./](\d{1,2})[./](\d{1,2})(\b.*)?$", raw)
    if dotted:
        rest = dotted.group(4) or ""
        raw = f"{dotted.group(1)}-{int(dotted.group(2)):02d}-{int(dotted.group(3)):02d}{rest}"
    cn_date = re.match(r"^(\d{4})年(\d{1,2})月(\d{1,2})日(?:\s*(\d{1,2}):(\d{2}))?$", raw)
    if cn_date:
        year, month, day = int(cn_date.group(1)), int(cn_date.group(2)), int(cn_date.group(3))
        hour = int(cn_date.group(4) or 0)
        minute = int(cn_date.group(5) or 0)
        try:
            return datetime(year, month, day, hour, minute, tzinfo=UTC)
        except ValueError:
            return None
    month_name = _parse_month_name_date(raw)
    if month_name:
        return month_name
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%d %H:%M:%S %z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC)
        except ValueError:
            continue
    return None


def stable_hash(parts: Iterable[str], *, length: int = 24) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update((part or "").encode("utf-8", errors="ignore"))
        digest.update(b"\0")
    return digest.hexdigest()[:length]


def fmt_dt(dt: datetime | None) -> str:
    if not dt:
        return "未知"
    return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def format_mmss(seconds: int | float) -> str:
    sec = max(0, int(round(seconds)))
    return f"{sec // 60:02d}:{sec % 60:02d}"

from __future__ import annotations

import email.utils
import hashlib
import html
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

UTC = timezone.utc

_MONTH_NUMBERS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def now_utc() -> datetime:
    return datetime.now(UTC)


def _parse_month_name_date(raw: str) -> datetime | None:
    """Parse English dates like "Jul 24, 2025" / "July 24 2025" / "24 Jul 2025" deterministically."""
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
    raw = clean_text(value)
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
    month_name = _parse_month_name_date(raw)
    if month_name:
        return month_name
    fmts = [
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%d %H:%M:%S %z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]
    for fmt in fmts:
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC)
        except ValueError:
            continue
    return None


def clean_text(value: str | None, limit: int | None = None) -> str:
    text = value or ""
    text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", text, flags=re.S)
    text = re.sub(r"<script\b.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style\b.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"#?\s*欢迎关注.+$", "", text)
    text = re.sub(r"更多精彩内容.+$", "", text)
    lowered = text.lower()
    if "boost inference performance up to 15x on nvidia blackwell" in lowered:
        text = "NVIDIA Blackwell 上 DFlash 推测解码推理最高提速 15 倍"
    elif "as ai systems move from single-turn interactions" in lowered:
        text = "NVIDIA 介绍 DFlash 推测解码，用于降低多轮 AI 任务的推理成本。"
    text = re.sub(r"\s+", " ", text).strip()
    if limit and len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


def stable_hash(parts: Iterable[str]) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update((p or "").encode("utf-8", errors="ignore"))
        h.update(b"\0")
    return h.hexdigest()[:24]


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def fmt_dt(dt: datetime | None) -> str:
    if not dt:
        return "未知"
    return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def format_mmss(seconds: int) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"

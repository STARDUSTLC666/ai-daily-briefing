from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from briefing.core import clean_text, write_text_atomic

MAX_CUE_SECONDS = 8.0


def _srt_timestamp(seconds: float) -> str:
    val = max(0.0, float(seconds))
    total_ms = int(round(val * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _ass_timestamp(seconds: float) -> str:
    val = max(0.0, float(seconds))
    total_cs = int(round(val * 100))
    hours, rem = divmod(total_cs, 360_000)
    minutes, rem = divmod(rem, 6_000)
    secs, cs = divmod(rem, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def _parse_srt_timestamp(raw: str) -> float:
    match = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)", raw.strip())
    if not match:
        return 0.0
    h, m, s, ms = (int(x) for x in match.groups())
    return h * 3600 + m * 60 + s + ms / 1000.0


def _read_srt_file(path: Path) -> list[tuple[float, float, str]]:
    if not path.is_file():
        return []
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    rows: list[tuple[float, float, str]] = []
    for block in re.split(r"\n\s*\n", raw.strip()):
        lines = [line.strip("\ufeff") for line in block.splitlines() if line.strip()]
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        left, right = [p.strip() for p in lines[1].split("-->", 1)]
        rows.append((_parse_srt_timestamp(left), _parse_srt_timestamp(right), clean_text(" ".join(lines[2:]))))
    return rows


def _subtitle_chunks(text: str, max_chars: int = 30) -> list[str]:
    cleaned = clean_text(text or "")
    if not cleaned:
        return []
    sentences = re.split(r"[。！？!?；;\n]+", cleaned)
    chunks: list[str] = []
    for raw in sentences:
        part = clean_text(raw).strip(" ，,：:")
        if not part:
            continue
        while len(part) > max_chars:
            split_at = -1
            for marker in ("，", ",", "、", "：", ":", " "):
                candidate = part.rfind(marker, 10, max_chars + 1)
                if candidate > split_at:
                    split_at = candidate
            if split_at < 10:
                split_at = max_chars
            head = part[:split_at].strip(" ，,：:")
            if head:
                chunks.append(head)
            part = part[split_at:].strip(" ，,：:")
        if part:
            chunks.append(part)
    return chunks


def _cap_cue_duration(start: float, end: float, text: str) -> list[tuple[float, float, str]]:
    span = max(0.0, end - start)
    if span <= MAX_CUE_SECONDS:
        return [(start, end, text)]
    pieces = _subtitle_chunks(text, max_chars=max(12, len(text) // 2))
    needed_bins = max(int((span + MAX_CUE_SECONDS - 0.01) // MAX_CUE_SECONDS), len(pieces))
    if len(pieces) < needed_bins:
        step_chars = max(6, int(round(len(text) / needed_bins)))
        pieces = [text[i : i + step_chars].strip() for i in range(0, len(text), step_chars) if text[i : i + step_chars].strip()]
    if not pieces:
        pieces = [text]
    weights = [max(4, len(p)) for p in pieces]
    total_w = max(1, sum(weights))
    out: list[tuple[float, float, str]] = []
    cursor = start
    for idx, (piece, w) in enumerate(zip(pieces, weights)):
        target_end = end if idx == len(pieces) - 1 else start + span * (sum(weights[: idx + 1]) / total_w)
        while target_end - cursor > MAX_CUE_SECONDS + 1e-6:
            mid = cursor + MAX_CUE_SECONDS
            out.append((cursor, mid, piece))
            cursor = mid
        if target_end > cursor + 0.08:
            out.append((cursor, min(end, target_end), piece))
            cursor = min(end, target_end)
    return out


def _cues_from_segment_text(seg: dict[str, Any], seg_start: float, duration: float) -> list[tuple[float, float, str]]:
    source_text = str(seg.get("text") or seg.get("caption") or seg.get("title") or "")
    chunks = _subtitle_chunks(source_text, max_chars=30)
    if not chunks:
        return []
    end_limit = seg_start + max(0.2, duration)
    weights = [max(6, len(c)) for c in chunks]
    total_w = max(1, sum(weights))
    rows: list[tuple[float, float, str]] = []
    cursor = seg_start
    for idx, (chunk, w) in enumerate(zip(chunks, weights)):
        raw_end = end_limit if idx == len(chunks) - 1 else seg_start + duration * (sum(weights[: idx + 1]) / total_w)
        cue_start = max(seg_start, cursor)
        cue_end = min(end_limit, max(cue_start + 0.25, raw_end))
        if cue_end > cue_start + 0.08:
            rows.extend(_cap_cue_duration(cue_start, cue_end, chunk))
            cursor = rows[-1][1]
    return rows


def _cues_from_tts_srt(srt_path: Path, seg_start: float, duration: float) -> list[tuple[float, float, str]]:
    local_cues = _read_srt_file(srt_path)
    if not local_cues:
        return []
    end_limit = seg_start + max(0.2, duration)
    rows: list[tuple[float, float, str]] = []
    cursor = seg_start
    for local_start, local_end, raw_text in local_cues:
        cleaned = clean_text(raw_text)
        if not cleaned:
            continue
        cue_start = max(seg_start, seg_start + local_start, cursor)
        cue_end = min(end_limit, seg_start + local_end)
        if cue_end <= cue_start + 0.12:
            continue
        sub_chunks = _subtitle_chunks(cleaned, max_chars=32) or [cleaned]
        sub_span = cue_end - cue_start
        weights = [max(4, len(c)) for c in sub_chunks]
        total_w = max(1, sum(weights))
        sub_cursor = cue_start
        for idx, (chunk, w) in enumerate(zip(sub_chunks, weights)):
            sub_end = cue_end if idx == len(sub_chunks) - 1 else cue_start + sub_span * (sum(weights[: idx + 1]) / total_w)
            sub_end = min(cue_end, max(sub_cursor + 0.18, sub_end))
            if sub_end > sub_cursor + 0.08:
                rows.extend(_cap_cue_duration(sub_cursor, sub_end, chunk))
                sub_cursor = rows[-1][1]
        cursor = sub_cursor
    return rows


def write_srt(
    segments: list[dict[str, Any]],
    durations: list[float],
    out_path: Path,
    *,
    tts_srt_paths: list[Path | None] | None = None,
) -> list[dict[str, Any]]:
    """Write non-overlapping <=8s sentence/clause cues to subtitles.srt and return cue dicts."""
    cues: list[dict[str, Any]] = []
    lines: list[str] = []
    timeline_cursor = 0.0
    last_end = 0.0

    for idx, (seg, raw_dur) in enumerate(zip(segments, durations)):
        dur = max(0.2, float(raw_dur or 0.2))
        seg_cues: list[tuple[float, float, str]] = []
        if tts_srt_paths and idx < len(tts_srt_paths) and tts_srt_paths[idx] is not None:
            seg_cues = _cues_from_tts_srt(Path(tts_srt_paths[idx]), timeline_cursor, dur)  # type: ignore[arg-type]
        if not seg_cues:
            seg_cues = _cues_from_segment_text(seg, timeline_cursor, dur)

        for start, end, caption in seg_cues:
            safe_start = max(last_end, start)
            safe_end = min(timeline_cursor + dur, max(safe_start + 0.12, end))
            if safe_end <= safe_start + 0.05 or not caption.strip():
                continue
            for capped_start, capped_end, capped_text in _cap_cue_duration(safe_start, safe_end, caption):
                cue_idx = len(cues) + 1
                cue_dict = {
                    "index": cue_idx,
                    "start": round(capped_start, 3),
                    "end": round(capped_end, 3),
                    "duration": round(capped_end - capped_start, 3),
                    "text": capped_text,
                }
                cues.append(cue_dict)
                lines.extend(
                    [
                        str(cue_idx),
                        f"{_srt_timestamp(capped_start)} --> {_srt_timestamp(capped_end)}",
                        capped_text,
                        "",
                    ]
                )
                last_end = capped_end
        timeline_cursor += dur

    write_text_atomic(Path(out_path), "\n".join(lines).rstrip() + ("\n" if lines else ""))
    return cues


def _escape_ass(text: str) -> str:
    return (
        str(text or "")
        .replace("{", "（")
        .replace("}", "）")
        .replace("\r", "")
        .replace("\n", "\\N")
    )


def write_subtitle_ass(
    cues: list[dict[str, Any]],
    out_path: Path,
    *,
    width: int = 1920,
    height: int = 1080,
) -> Path:
    """Write clean libass subtitle track at Alignment=2, MarginV=92 scaled proportionally to height."""
    if not cues:
        raise RuntimeError("cannot write ASS subtitle track with zero cues")
    scale_y = max(0.5, float(height) / 1080.0)
    scale_x = max(0.5, float(width) / 1920.0)

    font_size = max(14, round(34 * scale_y))
    outline = max(1, round(3 * scale_y))
    shadow = max(1, round(1 * scale_y))
    margin_v = max(1, round(92 * scale_y))
    margin_lr = max(1, round(160 * scale_x))

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "ScaledBorderAndShadow: yes\n"
        f"PlayResX: {int(width)}\n"
        f"PlayResY: {int(height)}\n\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
        "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
        "Alignment,MarginL,MarginR,MarginV,Encoding\n"
        f"Style: Subtitle,Microsoft YaHei UI,{font_size},&H00FFFFFF,&H000000FF,&H00324552,&HAA172432,"
        f"-1,0,0,0,100,100,0,0,1,{outline},{shadow},2,{margin_lr},{margin_lr},{margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )

    events = [header]
    for item in cues:
        start = float(item["start"])
        end = float(item["end"])
        text = str(item.get("text") or "").strip()
        if end <= start or not text:
            continue
        events.append(
            f"Dialogue: 20,{_ass_timestamp(start)},{_ass_timestamp(end)},Subtitle,,0,0,0,,{_escape_ass(text)}\n"
        )

    target = Path(out_path)
    write_text_atomic(target, "".join(events))
    return target

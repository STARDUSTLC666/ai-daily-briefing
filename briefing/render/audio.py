from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from briefing.core import ensure_dir, file_sha256, iso, now_utc, write_json_atomic

TARGET_I = -15.0
TARGET_TP = -2.0
TARGET_LRA = 7.0


def ffmpeg_bin() -> str:
    configured = os.environ.get("BRIEFING_FFMPEG", "").strip()
    if configured and Path(configured).is_file():
        return str(Path(configured).resolve())
    found = shutil.which("ffmpeg")
    if not found:
        raise RuntimeError("ffmpeg not found on PATH")
    return found


def ffprobe_bin() -> str:
    configured = os.environ.get("BRIEFING_FFPROBE", "").strip()
    if configured and Path(configured).is_file():
        return str(Path(configured).resolve())
    found = shutil.which("ffprobe")
    if not found:
        raise RuntimeError(
            "ffprobe not found on PATH: narration timing cannot be measured accurately"
        )
    return found


def _subprocess_timeout(name: str, default: float) -> float | None:
    raw = os.environ.get(f"BRIEFING_{name}_TIMEOUT", "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError:
        value = default
    return value if value > 0 else None


def probe_duration(path: Path) -> float:
    target = Path(path).resolve()
    if not target.is_file():
        raise FileNotFoundError(f"media file not found: {target}")
    ffprobe = ffprobe_bin()
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(target),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_subprocess_timeout("FFPROBE", 60.0),
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"ffprobe duration failed for {target.name}: {completed.stderr[-300:]}")
    raw = completed.stdout.strip()
    try:
        return max(0.1, float(raw))
    except ValueError as exc:
        raise RuntimeError(f"ffprobe returned unparsable duration for {target.name}: {raw!r}") from exc


def probe_media_streams(path: Path) -> dict[str, Any]:
    target = Path(path).resolve()
    if not target.is_file():
        raise FileNotFoundError(f"media file not found: {target}")
    ffprobe = ffprobe_bin()
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(target),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_subprocess_timeout("FFPROBE", 60.0),
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"ffprobe streams failed for {target.name}: {completed.stderr[-300:]}")
    payload = json.loads(completed.stdout or "{}")
    streams = [s for s in list(payload.get("streams") or []) if isinstance(s, dict)]
    fmt = payload.get("format") if isinstance(payload.get("format"), dict) else {}
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float(fmt.get("duration") or 0.0)
    return {
        "path": str(target),
        "duration": duration,
        "format_name": str(fmt.get("format_name") or ""),
        "bit_rate": int(fmt.get("bit_rate") or 0) if str(fmt.get("bit_rate") or "").isdigit() else 0,
        "has_video": video_stream is not None,
        "has_audio": audio_stream is not None,
        "video": {
            "codec_name": str(video_stream.get("codec_name") or ""),
            "width": int(video_stream.get("width") or 0),
            "height": int(video_stream.get("height") or 0),
            "pix_fmt": str(video_stream.get("pix_fmt") or ""),
            "r_frame_rate": str(video_stream.get("r_frame_rate") or ""),
        }
        if video_stream
        else None,
        "audio": {
            "codec_name": str(audio_stream.get("codec_name") or ""),
            "sample_rate": int(audio_stream.get("sample_rate") or 0)
            if str(audio_stream.get("sample_rate") or "").isdigit()
            else 0,
            "channels": int(audio_stream.get("channels") or 0),
        }
        if audio_stream
        else None,
        "streams": streams,
        "format": fmt,
    }


def _last_json_block(text: str) -> dict[str, Any]:
    blocks = re.findall(r"\{[\s\S]*?\}", text)
    for block in reversed(blocks):
        try:
            parsed = json.loads(block)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    return {}


def normalize_audio(src: Path, dst: Path, *, report_path: Path | None = None) -> dict[str, Any]:
    """Two-pass EBU R128 loudness normalization to -15 LUFS, -2.0 dBTP, LRA 7, 48kHz stereo PCM WAV."""
    ffmpeg = ffmpeg_bin()
    source = Path(src).resolve()
    target = Path(dst).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"audio normalization source missing: {source}")
    if source == target:
        raise ValueError("normalization source and target must differ")
    ensure_dir(target.parent)

    measure_filter = f"loudnorm=I={TARGET_I:g}:TP={TARGET_TP:g}:LRA={TARGET_LRA:g}:print_format=json"
    measured = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostats", "-i", str(source), "-vn", "-af", measure_filter, "-f", "null", "-"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_subprocess_timeout("FFMPEG", 180.0),
        check=False,
    )
    if measured.returncode != 0:
        raise RuntimeError(f"loudnorm pass-1 failed: {measured.stderr[-500:]}")
    values = _last_json_block(measured.stderr)
    required = ("input_i", "input_lra", "input_tp", "input_thresh", "target_offset")
    if not all(key in values for key in required):
        raise RuntimeError("loudnorm pass-1 did not return complete JSON metrics")

    apply_filter = (
        f"loudnorm=I={TARGET_I:g}:TP={TARGET_TP:g}:LRA={TARGET_LRA:g}"
        f":measured_I={values['input_i']}:measured_LRA={values['input_lra']}"
        f":measured_TP={values['input_tp']}:measured_thresh={values['input_thresh']}"
        f":offset={values['target_offset']}:linear=true:print_format=summary"
    )
    applied = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-nostats",
            "-i",
            str(source),
            "-vn",
            "-af",
            apply_filter,
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(target),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_subprocess_timeout("FFMPEG", 180.0),
        check=False,
    )
    if applied.returncode != 0 or not target.is_file() or target.stat().st_size == 0:
        raise RuntimeError(f"loudnorm pass-2 failed: {applied.stderr[-500:]}")

    result: dict[str, Any] = {
        "schema_version": 1,
        "method": "ebu_r128_two_pass",
        "target_i_lufs": TARGET_I,
        "target_tp_dbtp": TARGET_TP,
        "target_lra": TARGET_LRA,
        "source_sha256": file_sha256(source),
        "output_sha256": file_sha256(target),
        "measurement": {key: values.get(key) for key in required},
    }
    if report_path is not None:
        write_json_atomic(Path(report_path), result)
    return result


def analyze_audio(media_path: Path, out_path: Path | None = None) -> dict[str, Any]:
    """Measure final media loudness & silence ratio and write audio-quality.json."""
    media = Path(media_path).resolve()
    report_target = Path(out_path).resolve() if out_path is not None else media.parent / "audio-quality.json"
    result: dict[str, Any] = {
        "schema_version": 2,
        "measured_at": iso(now_utc()),
        "media_path": str(media),
        "media_sha256": file_sha256(media) if media.is_file() else "",
        "media_size": media.stat().st_size if media.is_file() else 0,
        "integrated_lufs": -99.0,
        "true_peak_dbtp": 99.0,
        "long_silence_ratio": 1.0,
        "duration_seconds": 0.0,
        "ok": False,
    }
    try:
        if not media.is_file():
            raise FileNotFoundError(f"media file missing: {media}")
        ffmpeg = ffmpeg_bin()
        duration = probe_duration(media)
        probe = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-nostats",
                "-i",
                str(media),
                "-vn",
                "-af",
                "loudnorm=I=-15:TP=-1.5:LRA=7:print_format=json,silencedetect=noise=-45dB:d=0.7",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_subprocess_timeout("FFMPEG", 300.0),
            check=False,
        )
        if probe.returncode != 0:
            raise RuntimeError(f"audio analysis failed: {probe.stderr[-500:]}")
        loudness = _last_json_block(probe.stderr)
        silence = sum(float(v) for v in re.findall(r"silence_duration:\s*([0-9.]+)", probe.stderr))
        integrated_lufs = float(loudness.get("input_i", -99.0))
        true_peak_dbtp = float(loudness.get("input_tp", 99.0))
        long_silence_ratio = round(silence / duration, 4) if duration > 0 else 1.0
        result.update(
            {
                "integrated_lufs": integrated_lufs,
                "true_peak_dbtp": true_peak_dbtp,
                "long_silence_ratio": long_silence_ratio,
                "duration_seconds": duration,
                "ok": (
                    -16.0 <= integrated_lufs <= -14.0
                    and true_peak_dbtp <= -1.5
                    and long_silence_ratio <= 0.08
                ),
            }
        )
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"[:800]

    if media.parent.exists() or out_path is not None:
        write_json_atomic(report_target, result)
    return result

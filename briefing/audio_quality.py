from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
TARGET_I = -15.0
TARGET_TP = -2.0
TARGET_LRA = 7.0


def media_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _last_json_block(text: str) -> dict[str, Any]:
    blocks = re.findall(r"\{[\s\S]*?\}", text)
    for block in reversed(blocks):
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            continue
    return {}


def normalize_audio(source: Path, target: Path, report: Path | None = None) -> dict[str, Any]:
    """Apply measured two-pass EBU R128 normalization to narration audio."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found")
    source, target = Path(source).resolve(), Path(target).resolve()
    if source == target:
        raise ValueError("normalization source and target must differ")
    target.parent.mkdir(parents=True, exist_ok=True)
    base_filter = f"loudnorm=I={TARGET_I:g}:TP={TARGET_TP:g}:LRA={TARGET_LRA:g}:print_format=json"
    measured = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostats", "-i", str(source), "-vn", "-af", base_filter, "-f", "null", "-"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    if measured.returncode != 0:
        raise RuntimeError(f"loudnorm measurement failed: {measured.stderr[-500:]}")
    values = _last_json_block(measured.stderr)
    required = ["input_i", "input_lra", "input_tp", "input_thresh", "target_offset"]
    if not all(key in values for key in required):
        raise RuntimeError("loudnorm measurement did not return complete JSON")
    second_filter = (
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
            second_filter,
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
        timeout=180,
    )
    if applied.returncode != 0 or not target.exists() or target.stat().st_size == 0:
        raise RuntimeError(f"loudnorm apply failed: {applied.stderr[-500:]}")
    result = {
        "schema_version": 1,
        "method": "ebu_r128_two_pass",
        "target_i_lufs": TARGET_I,
        "target_tp_dbtp": TARGET_TP,
        "target_lra": TARGET_LRA,
        "source_sha256": media_sha256(source),
        "output_sha256": media_sha256(target),
        "measurement": {key: values.get(key) for key in required},
    }
    if report:
        Path(report).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def analyze_audio(video: Path, output: Path | None = None) -> dict[str, Any]:
    """Measure final-video loudness and bind the report to that exact MP4."""
    video = Path(video).resolve()
    result: dict[str, Any] = {
        "schema_version": 2,
        "measured_at": datetime.now(UTC).isoformat(),
        "media_path": str(video),
        "media_sha256": media_sha256(video) if video.exists() else "",
        "media_size": video.stat().st_size if video.exists() else 0,
        "integrated_lufs": -99.0,
        "true_peak_dbtp": 99.0,
        "long_silence_ratio": 1.0,
        "duration_seconds": 0.0,
        "ok": False,
    }
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    try:
        if not video.exists() or not ffmpeg or not ffprobe:
            raise RuntimeError("video, ffmpeg or ffprobe is missing")
        duration_probe = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(video)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        if duration_probe.returncode != 0:
            raise RuntimeError("ffprobe duration failed")
        duration = float((json.loads(duration_probe.stdout).get("format") or {}).get("duration") or 0)
        probe = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-nostats",
                "-i",
                str(video),
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
            timeout=300,
        )
        if probe.returncode != 0:
            raise RuntimeError(f"audio analysis failed: {probe.stderr[-500:]}")
        loudness = _last_json_block(probe.stderr)
        silence = sum(float(value) for value in re.findall(r"silence_duration:\s*([0-9.]+)", probe.stderr))
        result.update(
            {
                "integrated_lufs": float(loudness.get("input_i", -99)),
                "true_peak_dbtp": float(loudness.get("input_tp", 99)),
                "long_silence_ratio": round(silence / duration, 4) if duration else 1.0,
                "duration_seconds": duration,
            }
        )
        result["ok"] = (
            -16 <= result["integrated_lufs"] <= -14
            and result["true_peak_dbtp"] <= -1.5
            and result["long_silence_ratio"] <= 0.08
        )
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"[:800]
    if output:
        Path(output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result

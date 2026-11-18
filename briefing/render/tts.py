from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any
import wave

from briefing.core import ensure_dir, file_sha256, read_json, write_json_atomic, write_text_atomic
from .audio import ffmpeg_bin, normalize_audio, probe_duration

PROFILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
TTS_SENTENCE_RE = re.compile(r".+?(?:[。！？!?；;]+|$)")
CACHE_PLAN_VERSION = 1


@dataclass(frozen=True, slots=True)
class IndexTTS2Profile:
    profile_id: str
    reference_audio: Path
    emotion_vector: tuple[float, ...] | None
    emotion_text: str | None
    emotion_audio: Path | None
    emotion_weight: float
    publishable: bool


def _env_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _subprocess_timeout(name: str, default: float) -> float | None:
    raw = os.environ.get(f"BRIEFING_{name}_TIMEOUT", "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError:
        value = default
    return value if value > 0 else None


def split_tts_text(text: str, *, max_chars: int = 72) -> list[str]:
    """Split long narration at spoken punctuation while merging short adjacent clauses."""
    cleaned = str(text or "").strip()
    if not cleaned:
        return []
    budget = max(32, min(160, int(max_chars)))
    units = [m.group(0).strip() for m in TTS_SENTENCE_RE.finditer(cleaned) if m.group(0).strip()]
    pieces: list[str] = []
    soft_breaks = set("，,：:、")
    for unit in units or [cleaned]:
        remaining = unit
        while len(remaining) > budget:
            lower_bound = max(18, budget // 2)
            cut = next(
                (idx + 1 for idx in range(budget - 1, lower_bound - 1, -1) if remaining[idx] in soft_breaks),
                0,
            )
            if not cut:
                cut = next(
                    (idx + 1 for idx in range(budget - 1, lower_bound - 1, -1) if remaining[idx].isspace()),
                    budget,
                )
            pieces.append(remaining[:cut].strip())
            remaining = remaining[cut:].strip()
        if remaining:
            pieces.append(remaining)

    merged: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) > budget:
            merged.append(current)
            current = piece
        else:
            current += piece
    if current:
        merged.append(current)
    return merged


def _resolve_profile_asset(base: Path, value: str, field_name: str) -> Path:
    raw = Path(value).expanduser()
    resolved = (raw if raw.is_absolute() else base / raw).resolve()
    if not resolved.is_file():
        raise RuntimeError(f"IndexTTS2 profile {field_name} not found: {resolved}")
    return resolved


def _parse_emotion_vector(value: object) -> tuple[float, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 8:
        raise RuntimeError("IndexTTS2 profile emotion_vector must contain 8 numbers")
    try:
        vec = tuple(float(v) for v in value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("IndexTTS2 profile emotion_vector contains non-numeric entry") from exc
    if any(v < 0.0 or v > 1.0 for v in vec) or sum(vec) > 0.800001:
        raise RuntimeError("IndexTTS2 profile emotion_vector must have values in 0..1 and sum <= 0.8")
    return vec


def _load_indextts2_profile(profile_id: str = "", profile_root: Path | None = None) -> IndexTTS2Profile:
    pid = (profile_id or os.environ.get("BRIEFING_TTS_PROFILE", "")).strip()
    if not pid:
        raise RuntimeError("BRIEFING_TTS_PROFILE or profile is required for indextts2")
    if not PROFILE_ID_RE.fullmatch(pid):
        raise RuntimeError(f"invalid IndexTTS2 profile id: {pid!r}")

    if profile_root is None:
        configured_dir = os.environ.get("BRIEFING_VOICE_PROFILE_DIR", "").strip()
        if configured_dir:
            profile_root = Path(configured_dir)
        else:
            root = os.environ.get("BRIEFING_INDEXTTS2_ROOT", "").strip()
            if not root:
                raise RuntimeError("BRIEFING_INDEXTTS2_ROOT or BRIEFING_VOICE_PROFILE_DIR is required")
            profile_root = Path(root) / "voice_profiles"

    profile_dir = (profile_root / pid).resolve()
    profile_path = profile_dir / "profile.json"
    if not profile_path.is_file():
        raise RuntimeError(f"IndexTTS2 profile not found: {profile_path}")

    payload = read_json(profile_path)
    if not isinstance(payload, dict):
        raise RuntimeError(f"IndexTTS2 profile must be a JSON object: {profile_path}")

    ref_raw = str(payload.get("reference_audio") or "").strip()
    if not ref_raw:
        raise RuntimeError(f"IndexTTS2 profile reference_audio is required: {profile_path}")
    reference_audio = _resolve_profile_asset(profile_dir, ref_raw, "reference_audio")
    emotion_vector = _parse_emotion_vector(payload.get("emotion_vector"))
    emotion_text_raw = payload.get("emotion_text")
    emotion_text = str(emotion_text_raw).strip() if emotion_text_raw is not None else None
    if emotion_text == "":
        emotion_text = None
    emotion_audio_raw = str(payload.get("emotion_audio") or "").strip()
    emotion_audio = _resolve_profile_asset(profile_dir, emotion_audio_raw, "emotion_audio") if emotion_audio_raw else None
    if sum(x is not None for x in (emotion_vector, emotion_text, emotion_audio)) > 1:
        raise RuntimeError("IndexTTS2 profile may define only one emotion source")

    emotion_weight = float(payload.get("emotion_weight", 1.0))
    if emotion_weight < 0.0 or emotion_weight > 1.0:
        raise RuntimeError("IndexTTS2 profile emotion_weight must be in 0.0..1.0")

    publishable = bool(payload.get("publishable", False))
    if not publishable and not _env_enabled("BRIEFING_ALLOW_NONPUBLISHABLE_PROFILE"):
        raise RuntimeError(
            f"IndexTTS2 profile {pid!r} has publishable=false; "
            "set BRIEFING_ALLOW_NONPUBLISHABLE_PROFILE=1 only for local non-publish tests"
        )
    return IndexTTS2Profile(
        profile_id=pid,
        reference_audio=reference_audio,
        emotion_vector=emotion_vector,
        emotion_text=emotion_text,
        emotion_audio=emotion_audio,
        emotion_weight=emotion_weight,
        publishable=publishable and not _env_enabled("BRIEFING_ALLOW_NONPUBLISHABLE_PROFILE"),
    )


def _wav_is_usable(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size <= 44:
        return False
    try:
        with wave.open(str(path), "rb") as stream:
            return stream.getnchannels() > 0 and stream.getframerate() > 0 and stream.getnframes() > 0
    except (OSError, wave.Error):
        return False


def _concat_wav_fragments(fragments: list[Path], output: Path, *, silence_ms: int = 80) -> None:
    if len(fragments) == 1:
        if fragments[0].resolve() != output.resolve():
            shutil.copy2(fragments[0], output)
        return
    audio_frames: list[bytes] = []
    fmt_key: tuple[int, int, int, str] | None = None
    for frag in fragments:
        with wave.open(str(frag), "rb") as stream:
            cur = (stream.getnchannels(), stream.getsampwidth(), stream.getframerate(), stream.getcomptype())
            if fmt_key is None:
                fmt_key = cur
            elif cur != fmt_key:
                raise RuntimeError(f"IndexTTS2 WAV format mismatch in fragment: {frag}")
            audio_frames.append(stream.readframes(stream.getnframes()))
    assert fmt_key is not None
    channels, sampwidth, framerate, comptype = fmt_key
    if comptype != "NONE":
        raise RuntimeError("IndexTTS2 WAV fragments must be uncompressed PCM")
    pause_bytes = bytes(max(0, round(framerate * silence_ms / 1000)) * channels * sampwidth)
    ensure_dir(output.parent)
    with wave.open(str(output), "wb") as out_stream:
        out_stream.setnchannels(channels)
        out_stream.setsampwidth(sampwidth)
        out_stream.setframerate(framerate)
        for idx, data in enumerate(audio_frames):
            if idx:
                out_stream.writeframes(pause_bytes)
            out_stream.writeframes(data)


def _sha256_json(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def synthesize_indextts2_segments(
    segments: list[dict[str, Any]],
    render_dir: Path,
    *,
    profile: str = "",
) -> dict[str, Any]:
    """Synthesize narration segments with local IndexTTS2 using SHA-256 chunk cache + 80ms pauses."""
    prof = _load_indextts2_profile(profile_id=profile)
    root_str = os.environ.get("BRIEFING_INDEXTTS2_ROOT", "").strip()
    if not root_str:
        raise RuntimeError("BRIEFING_INDEXTTS2_ROOT is required for IndexTTS2 synthesis")
    root = Path(root_str).expanduser().resolve()
    cli_candidates = [
        root / ".venv" / "Scripts" / "indextts2.exe",
        root / ".venv" / "Scripts" / "indextts2",
        root / ".venv" / "bin" / "indextts2",
    ]
    cli = next((c for c in cli_candidates if c.is_file()), cli_candidates[0])
    if not cli.is_file():
        raise RuntimeError(f"IndexTTS2 CLI not found: {cli}")
    model_dir = Path(os.environ.get("BRIEFING_INDEXTTS2_MODEL_DIR", str(root / "checkpoints"))).expanduser().resolve()
    if not model_dir.is_dir():
        raise RuntimeError(f"IndexTTS2 model directory not found: {model_dir}")
    if not segments:
        raise RuntimeError("IndexTTS2 received empty segments list")

    render_dir = ensure_dir(Path(render_dir).resolve())
    max_chars = int(os.environ.get("BRIEFING_INDEXTTS2_MAX_CHARS", "72") or 72)
    device = os.environ.get("BRIEFING_INDEXTTS2_DEVICE", "cuda").strip() or "cuda"
    config_identity = {
        "version": CACHE_PLAN_VERSION,
        "profile_id": prof.profile_id,
        "reference_sha256": file_sha256(prof.reference_audio),
        "emotion_vector": prof.emotion_vector,
        "emotion_text": prof.emotion_text,
        "emotion_audio_sha256": file_sha256(prof.emotion_audio) if prof.emotion_audio else None,
        "emotion_weight": prof.emotion_weight,
        "model_dir": str(model_dir),
        "device": device,
        "max_chars": max_chars,
    }
    config_digest = _sha256_json(config_identity)

    cache_plan_path = render_dir / "indextts2-cache-plan.json"
    old_plan = read_json(cache_plan_path, default={}) if cache_plan_path.is_file() else {}
    old_tasks = old_plan.get("tasks") if isinstance(old_plan, dict) and old_plan.get("config_digest") == config_digest else {}
    if not isinstance(old_tasks, dict):
        old_tasks = {}

    wav_paths: list[Path] = []
    fragment_groups: list[list[Path]] = []
    all_rows: list[dict[str, str]] = []
    for idx, seg in enumerate(segments):
        text = str(seg.get("text") or "").strip()
        if not text:
            raise RuntimeError(f"IndexTTS2 segment {idx} has empty text")
        seg_out = render_dir / f"indextts2_{idx:03d}.wav"
        wav_paths.append(seg_out)
        parts = split_tts_text(text, max_chars=max_chars)
        frags = [
            seg_out if len(parts) == 1 else render_dir / f"indextts2_{idx:03d}_part_{p_idx:02d}.wav"
            for p_idx in range(len(parts))
        ]
        fragment_groups.append(frags)
        for part, frag in zip(parts, frags, strict=True):
            all_rows.append({"text": part, "output": frag.name})

    pending_rows: list[dict[str, str]] = []
    task_plan: dict[str, dict[str, str]] = {}
    reused = 0
    for row in all_rows:
        out_file = render_dir / row["output"]
        sig = _sha256_json({"config_digest": config_digest, "text": row["text"], "output": row["output"]})
        prev = old_tasks.get(row["output"])
        if isinstance(prev, dict) and prev.get("signature") == sig and _wav_is_usable(out_file):
            reused += 1
            task_plan[row["output"]] = {"signature": sig, "status": "complete"}
        else:
            out_file.unlink(missing_ok=True)
            pending_rows.append(row)
            task_plan[row["output"]] = {"signature": sig, "status": "pending"}

    write_json_atomic(
        cache_plan_path,
        {"version": CACHE_PLAN_VERSION, "config_digest": config_digest, "tasks": task_plan},
    )
    pending_path = render_dir / "indextts2-pending.jsonl"
    write_text_atomic(
        pending_path,
        "\n".join(json.dumps(r, ensure_ascii=False) for r in pending_rows) + ("\n" if pending_rows else ""),
    )

    if pending_rows:
        cmd = [
            str(cli),
            "batch",
            "--batch-file",
            str(pending_path),
            "--voice",
            str(prof.reference_audio),
            "--force",
            "--model-dir",
            str(model_dir),
            "--device",
            device,
            "--fp16",
            "--no-deepspeed",
            "--no-cuda-kernel",
            "--no-accel",
            "--no-torch-compile",
        ]
        if prof.emotion_vector is not None:
            cmd.extend(["--emotion-vector", ",".join(f"{v:g}" for v in prof.emotion_vector)])
        elif prof.emotion_text is not None:
            cmd.extend(["--emotion-text", prof.emotion_text])
        elif prof.emotion_audio is not None:
            cmd.extend(["--emotion-audio", str(prof.emotion_audio)])
        if any(x is not None for x in (prof.emotion_vector, prof.emotion_text, prof.emotion_audio)):
            cmd.extend(["--emotion-weight", f"{prof.emotion_weight:g}"])

        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        completed = subprocess.run(
            cmd,
            cwd=str(root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_subprocess_timeout("INDEXTTS2", 3600.0),
            check=False,
        )
        write_text_atomic(
            render_dir / "indextts2.log",
            f"Cache reused={reused}, generated={len(pending_rows)}\n{completed.stdout or ''}",
        )
        if completed.returncode != 0:
            raise RuntimeError(f"IndexTTS2 batch failed with exit code {completed.returncode}")

    missing = [str(p) for group in fragment_groups for p in group if not _wav_is_usable(p)]
    if missing:
        raise RuntimeError(f"IndexTTS2 missing output fragments: {', '.join(missing)}")

    for item in task_plan.values():
        item["status"] = "complete"
    write_json_atomic(
        cache_plan_path,
        {"version": CACHE_PLAN_VERSION, "config_digest": config_digest, "tasks": task_plan},
    )
    for frags, seg_out in zip(fragment_groups, wav_paths, strict=True):
        if len(frags) > 1:
            _concat_wav_fragments(frags, seg_out, silence_ms=80)

    return {
        "wav_paths": wav_paths,
        "profile_id": prof.profile_id,
        "publishable": prof.publishable,
        "tts_backend_id": f"indextts2:{prof.profile_id}",
        "cache_reused": reused,
        "cache_generated": len(pending_rows),
    }


def _edge_tts_prefix() -> list[str]:
    if sys.executable:
        return [sys.executable, "-m", "edge_tts"]
    py = shutil.which("py")
    if py:
        return [py, "-3", "-m", "edge_tts"]
    raise RuntimeError("no Python interpreter found for edge_tts")


def _synthesize_edge_segments(
    segments: list[dict[str, Any]],
    render_dir: Path,
    *,
    voice: str,
) -> tuple[list[Path], list[Path | None], str]:
    prefix = _edge_tts_prefix()
    requested = (voice or os.environ.get("BRIEFING_TTS_VOICE", "zh-CN-XiaoxiaoNeural")).strip()
    voices = [requested]
    if requested == "zh-CN-XiaoxiaoNeural":
        voices.append("zh-CN-XiaoyiNeural")
    elif requested == "zh-CN-XiaoyiNeural":
        voices.append("zh-CN-XiaoxiaoNeural")

    rate = os.environ.get("BRIEFING_TTS_RATE", "+8%")
    pitch = os.environ.get("BRIEFING_TTS_PITCH", "+0Hz")
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""
    attempts = max(1, min(4, int(os.environ.get("BRIEFING_EDGE_TTS_ATTEMPTS", "2") or 2)))
    failures: list[str] = []

    for candidate_voice in voices:
        media_files: list[Path] = []
        srt_files: list[Path | None] = []
        try:
            for idx, seg in enumerate(segments):
                txt_path = render_dir / f"seg_{idx:03d}.txt"
                mp3_path = render_dir / f"seg_{idx:03d}.mp3"
                srt_path = render_dir / f"seg_{idx:03d}.srt"
                write_text_atomic(txt_path, str(seg.get("text") or seg.get("title") or " "))
                cmd = [
                    *prefix,
                    "--voice",
                    candidate_voice,
                    f"--rate={rate}",
                    f"--pitch={pitch}",
                    "--file",
                    str(txt_path),
                    "--write-media",
                    str(mp3_path),
                    "--write-subtitles",
                    str(srt_path),
                ]
                if proxy:
                    cmd.extend(["--proxy", proxy])
                last_exc: Exception | None = None
                for attempt in range(1, attempts + 1):
                    mp3_path.unlink(missing_ok=True)
                    srt_path.unlink(missing_ok=True)
                    try:
                        subprocess.run(
                            cmd,
                            check=True,
                            capture_output=True,
                            timeout=_subprocess_timeout("EDGE_TTS", 300.0),
                        )
                        last_exc = None
                        break
                    except Exception as exc:
                        last_exc = exc
                        if attempt < attempts:
                            time.sleep(0.7 * attempt)
                if last_exc is not None:
                    raise last_exc
                media_files.append(mp3_path)
                srt_files.append(srt_path if srt_path.is_file() else None)
            return media_files, srt_files, candidate_voice
        except Exception as exc:
            failures.append(f"{candidate_voice}: {type(exc).__name__}: {exc}")
            for p in [*media_files, *(s for s in srt_files if s is not None)]:
                p.unlink(missing_ok=True)

    raise RuntimeError("; ".join(failures) or "edge-tts synthesis failed")


def _concat_and_normalize(render_dir: Path, audio_files: list[Path]) -> Path:
    ffmpeg = ffmpeg_bin()
    list_file = render_dir / "audio-list.txt"
    write_text_atomic(
        list_file,
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in audio_files) + "\n",
    )
    raw_wav = render_dir / "narration-raw.wav"
    narration_wav = render_dir / "narration.wav"
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(raw_wav),
        ],
        cwd=str(render_dir),
        check=True,
        capture_output=True,
        timeout=_subprocess_timeout("FFMPEG", 1800.0),
    )
    try:
        normalize_audio(raw_wav, narration_wav, report_path=render_dir / "audio-normalization.json")
    except Exception as exc:
        shutil.copyfile(raw_wav, narration_wav)
        write_text_atomic(
            render_dir / "audio-normalization-warning.txt",
            f"normalize_audio fallback to raw concat: {type(exc).__name__}: {exc}\n",
        )
    return narration_wav


def _synthesize_silent_narration(
    segments: list[dict[str, Any]],
    render_dir: Path,
    *,
    fallback_duration: float,
) -> tuple[list[float], Path]:
    ffmpeg = ffmpeg_bin()
    per_default = max(2.5, float(fallback_duration or 8.0) / max(1, len(segments)))
    durations = [
        round(
            max(2.5, min(24.0, len(str(seg.get("text") or "").strip()) / 4.3))
            if str(seg.get("text") or "").strip()
            else max(2.5, per_default),
            3,
        )
        for seg in segments
    ]
    total = max(2.5, sum(durations))
    narration_wav = render_dir / "narration.wav"
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-t",
            f"{total:.3f}",
            "-c:a",
            "pcm_s16le",
            str(narration_wav),
        ],
        check=True,
        capture_output=True,
        timeout=_subprocess_timeout("FFMPEG", 600.0),
    )
    return durations, narration_wav


def synthesize_narration(
    segments: list[dict[str, Any]],
    render_dir: Path,
    *,
    backend: str = "edge",
    voice: str = "zh-CN-XiaoxiaoNeural",
    profile: str = "",
    fallback_duration: float = 8.0,
) -> dict[str, Any]:
    """Synthesize narration via edge-tts, opt-in IndexTTS2, or non-publishable silent fixture."""
    render_dir = ensure_dir(Path(render_dir).resolve())
    chosen = (backend or os.environ.get("BRIEFING_TTS_BACKEND", "edge")).strip().lower() or "edge"
    if chosen not in {"edge", "indextts2", "silent"}:
        raise ValueError(f"unsupported TTS backend: {chosen!r}")

    if chosen == "silent":
        durations, narration_wav = _synthesize_silent_narration(
            segments, render_dir, fallback_duration=fallback_duration
        )
        return {
            "durations": durations,
            "srt_paths": [None] * len(segments),
            "narration_wav": narration_wav,
            "tts_backend_id": "silent",
            "tts_backend": "silent",
            "publishable": False,
        }

    if chosen == "indextts2":
        try:
            idx_result = synthesize_indextts2_segments(segments, render_dir, profile=profile)
        except Exception as exc:
            write_text_atomic(render_dir / "indextts2-error.txt", f"{type(exc).__name__}: {exc}\n")
            raise
        wav_paths = idx_result["wav_paths"]
        durations = [probe_duration(p) for p in wav_paths]
        narration_wav = _concat_and_normalize(render_dir, wav_paths)
        return {
            "durations": durations,
            "srt_paths": [None] * len(segments),
            "narration_wav": narration_wav,
            "tts_backend_id": str(idx_result["tts_backend_id"]),
            "tts_backend": "indextts2",
            "profile_id": str(idx_result["profile_id"]),
            "publishable": bool(idx_result["publishable"]),
        }

    try:
        media_files, srt_paths, actual_voice = _synthesize_edge_segments(segments, render_dir, voice=voice)
        durations = [probe_duration(p) for p in media_files]
        narration_wav = _concat_and_normalize(render_dir, media_files)
        return {
            "durations": durations,
            "srt_paths": srt_paths,
            "narration_wav": narration_wav,
            "tts_backend_id": f"edge-tts:{actual_voice}",
            "tts_backend": "edge",
            "voice": actual_voice,
            "publishable": True,
        }
    except Exception as exc:
        write_text_atomic(render_dir / "edge-tts-error.txt", f"{type(exc).__name__}: {exc}\n")
        if _env_enabled("BRIEFING_TTS_STRICT"):
            raise
        durations, narration_wav = _synthesize_silent_narration(
            segments, render_dir, fallback_duration=fallback_duration
        )
        return {
            "durations": durations,
            "srt_paths": [None] * len(segments),
            "narration_wav": narration_wav,
            "tts_backend_id": f"silent_fallback:{type(exc).__name__}",
            "tts_backend": "silent",
            "publishable": False,
        }

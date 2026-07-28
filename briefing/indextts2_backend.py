from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import wave


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


def _resolve_relative(base: Path, value: str, field: str) -> Path:
    raw = Path(value).expanduser()
    path = raw if raw.is_absolute() else base / raw
    path = path.resolve()
    if not path.is_file():
        raise RuntimeError(f"IndexTTS2 profile {field} not found: {path}")
    return path


def _parse_emotion_vector(value: object) -> tuple[float, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 8:
        raise RuntimeError("IndexTTS2 profile emotion_vector must contain exactly 8 numbers")
    try:
        vector = tuple(float(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("IndexTTS2 profile emotion_vector contains a non-number") from exc
    if any(item < 0.0 or item > 1.0 for item in vector):
        raise RuntimeError("IndexTTS2 profile emotion_vector values must be in 0.0..1.0")
    if sum(vector) > 0.800001:
        raise RuntimeError("IndexTTS2 profile emotion_vector sum must be <= 0.8")
    return vector


def load_profile(profile_id: str | None = None, profile_root: Path | None = None) -> IndexTTS2Profile:
    profile_id = (profile_id or os.environ.get("BRIEFING_TTS_PROFILE", "")).strip()
    if not profile_id:
        raise RuntimeError("BRIEFING_TTS_PROFILE is required for the IndexTTS2 backend")
    if not PROFILE_ID_RE.fullmatch(profile_id):
        raise RuntimeError(f"invalid IndexTTS2 profile id: {profile_id!r}")

    if profile_root is None:
        configured = os.environ.get("BRIEFING_VOICE_PROFILE_DIR", "").strip()
        if configured:
            profile_root = Path(configured)
        else:
            configured_root = os.environ.get("BRIEFING_INDEXTTS2_ROOT", "").strip()
            if not configured_root:
                raise RuntimeError(
                    "BRIEFING_INDEXTTS2_ROOT or BRIEFING_VOICE_PROFILE_DIR is required "
                    "for the optional IndexTTS2 backend"
                )
            index_root = Path(configured_root)
            profile_root = index_root / "voice_profiles"
    profile_dir = (profile_root / profile_id).resolve()
    profile_path = profile_dir / "profile.json"
    if not profile_path.is_file():
        raise RuntimeError(f"IndexTTS2 profile not found: {profile_path}")

    try:
        payload = json.loads(profile_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read IndexTTS2 profile: {profile_path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"IndexTTS2 profile must be a JSON object: {profile_path}")

    reference_value = str(payload.get("reference_audio", "")).strip()
    if not reference_value:
        raise RuntimeError(f"IndexTTS2 profile reference_audio is required: {profile_path}")
    reference_audio = _resolve_relative(profile_dir, reference_value, "reference_audio")

    emotion_vector = _parse_emotion_vector(payload.get("emotion_vector"))
    emotion_text_value = payload.get("emotion_text")
    emotion_text = str(emotion_text_value).strip() if emotion_text_value is not None else None
    if emotion_text == "":
        emotion_text = None
    emotion_audio_value = str(payload.get("emotion_audio", "")).strip()
    emotion_audio = _resolve_relative(profile_dir, emotion_audio_value, "emotion_audio") if emotion_audio_value else None
    if sum(source is not None for source in (emotion_vector, emotion_text, emotion_audio)) > 1:
        raise RuntimeError("IndexTTS2 profile may define only one emotion source")

    try:
        emotion_weight = float(payload.get("emotion_weight", 1.0))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("IndexTTS2 profile emotion_weight must be a number") from exc
    if emotion_weight < 0.0 or emotion_weight > 1.0:
        raise RuntimeError("IndexTTS2 profile emotion_weight must be in 0.0..1.0")

    publishable = bool(payload.get("publishable", False))
    if not publishable and not _env_enabled("BRIEFING_ALLOW_NONPUBLISHABLE_PROFILE"):
        raise RuntimeError(
            f"IndexTTS2 profile {profile_id!r} is marked publishable=false; "
            "set BRIEFING_ALLOW_NONPUBLISHABLE_PROFILE=1 only for a local test"
        )

    return IndexTTS2Profile(
        profile_id=profile_id,
        reference_audio=reference_audio,
        emotion_vector=emotion_vector,
        emotion_text=emotion_text,
        emotion_audio=emotion_audio,
        emotion_weight=emotion_weight,
        publishable=publishable,
    )


def _split_tts_text(text: str, max_chars: int = 72) -> list[str]:
    """Split long narration at spoken punctuation without changing its text."""
    text = str(text or "").strip()
    if not text:
        return []
    max_chars = max(32, min(160, int(max_chars)))
    units = [match.group(0).strip() for match in TTS_SENTENCE_RE.finditer(text) if match.group(0).strip()]
    pieces: list[str] = []
    soft_breaks = set("，,：:、")
    for unit in units or [text]:
        remaining = unit
        while len(remaining) > max_chars:
            lower_bound = max(18, max_chars // 2)
            cut = next(
                (index + 1 for index in range(max_chars - 1, lower_bound - 1, -1) if remaining[index] in soft_breaks),
                0,
            )
            if not cut:
                cut = next(
                    (index + 1 for index in range(max_chars - 1, lower_bound - 1, -1) if remaining[index].isspace()),
                    max_chars,
                )
            pieces.append(remaining[:cut].strip())
            remaining = remaining[cut:].strip()
        if remaining:
            pieces.append(remaining)
    # Keep short adjacent sentences in one inference request. This preserves
    # natural paragraph prosody and avoids paying per-task decoder overhead,
    # while still preventing the pathological long-sequence slowdown.
    result: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) > max_chars:
            result.append(current)
            current = piece
        else:
            current += piece
    if current:
        result.append(current)
    return result


def _concatenate_wav_fragments(fragments: list[Path], output: Path, silence_ms: int = 80) -> None:
    if len(fragments) < 2:
        raise RuntimeError("IndexTTS2 WAV concatenation requires at least two fragments")
    audio: list[bytes] = []
    format_key: tuple[int, int, int, str] | None = None
    for fragment in fragments:
        try:
            with wave.open(str(fragment), "rb") as stream:
                current = (stream.getnchannels(), stream.getsampwidth(), stream.getframerate(), stream.getcomptype())
                if format_key is None:
                    format_key = current
                elif current != format_key:
                    raise RuntimeError(f"IndexTTS2 fragment format mismatch: {fragment}")
                audio.append(stream.readframes(stream.getnframes()))
        except (OSError, wave.Error) as exc:
            raise RuntimeError(f"cannot read IndexTTS2 WAV fragment: {fragment}") from exc
    assert format_key is not None
    channels, sample_width, frame_rate, compression = format_key
    if compression != "NONE":
        raise RuntimeError("IndexTTS2 WAV fragments must use uncompressed PCM")
    silence_frames = max(0, round(frame_rate * max(0, silence_ms) / 1000))
    silence = bytes(silence_frames * channels * sample_width)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with wave.open(str(output), "wb") as stream:
            stream.setnchannels(channels)
            stream.setsampwidth(sample_width)
            stream.setframerate(frame_rate)
            for index, payload in enumerate(audio):
                if index:
                    stream.writeframes(silence)
                stream.writeframes(payload)
    except (OSError, wave.Error) as exc:
        raise RuntimeError(f"cannot write concatenated IndexTTS2 WAV: {output}") from exc


def _file_cache_identity(path: Path | None) -> dict[str, object] | None:
    if path is None:
        return None
    resolved = path.resolve()
    stat = resolved.stat()
    return {
        "path": str(resolved),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _model_cache_identity(model_dir: Path) -> list[dict[str, object]]:
    return [
        _file_cache_identity(path)
        for path in sorted(model_dir.iterdir(), key=lambda item: item.name.lower())
        if path.is_file()
    ]


def _cache_digest(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _wav_is_usable(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size <= 44:
        return False
    try:
        with wave.open(str(path), "rb") as stream:
            return stream.getnchannels() > 0 and stream.getframerate() > 0 and stream.getnframes() > 0
    except (OSError, wave.Error):
        return False


def _read_cache_plan(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_cache_plan(path: Path, payload: dict[str, object]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def synthesize_segments(
    render_dir: Path,
    segments: list[dict[str, str]],
    profile_id: str | None = None,
) -> tuple[list[Path], str]:
    profile = load_profile(profile_id=profile_id)
    configured_root = os.environ.get("BRIEFING_INDEXTTS2_ROOT", "").strip()
    if not configured_root:
        raise RuntimeError("BRIEFING_INDEXTTS2_ROOT is required for the optional IndexTTS2 backend")
    root = Path(configured_root).expanduser().resolve()
    cli_candidates = [
        root / ".venv" / "Scripts" / "indextts2.exe",
        root / ".venv" / "Scripts" / "indextts2",
        root / ".venv" / "bin" / "indextts2",
    ]
    cli = next((candidate for candidate in cli_candidates if candidate.is_file()), cli_candidates[0])
    model_dir = Path(os.environ.get("BRIEFING_INDEXTTS2_MODEL_DIR", str(root / "checkpoints"))).expanduser().resolve()
    if not cli.is_file():
        raise RuntimeError(
            f"IndexTTS2 CLI not found: {cli} (set BRIEFING_INDEXTTS2_ROOT to the IndexTTS2 checkout, "
            "or BRIEFING_INDEXTTS2_MODEL_DIR / BRIEFING_VOICE_PROFILE_DIR for custom layouts)"
        )
    if not model_dir.is_dir():
        raise RuntimeError(f"IndexTTS2 model directory not found: {model_dir}")
    if not segments:
        raise RuntimeError("IndexTTS2 received no narration segments")

    render_dir.mkdir(parents=True, exist_ok=True)
    batch_path = render_dir / "indextts2-batch.jsonl"
    pending_batch_path = render_dir / "indextts2-pending.jsonl"
    cache_plan_path = render_dir / "indextts2-cache-plan.json"
    outputs: list[Path] = []
    fragment_groups: list[list[Path]] = []
    rows: list[dict[str, str]] = []
    try:
        max_chars = int(os.environ.get("BRIEFING_INDEXTTS2_MAX_CHARS", "72") or 72)
    except ValueError as exc:
        raise RuntimeError("BRIEFING_INDEXTTS2_MAX_CHARS must be an integer") from exc
    for index, segment in enumerate(segments):
        text = str(segment.get("text", "")).strip()
        if not text:
            raise RuntimeError(f"IndexTTS2 segment {index} has empty text")
        output = render_dir / f"indextts2_{index:03d}.wav"
        outputs.append(output)
        parts = _split_tts_text(text, max_chars=max_chars)
        fragments = [
            output if len(parts) == 1 else render_dir / f"indextts2_{index:03d}_part_{part_index:02d}.wav"
            for part_index in range(len(parts))
        ]
        fragment_groups.append(fragments)
        rows.extend(
            {"text": part, "output": fragment.name}
            for part, fragment in zip(parts, fragments, strict=True)
        )
    batch_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )

    device = os.environ.get("BRIEFING_INDEXTTS2_DEVICE", "cuda").strip() or "cuda"
    cache_config = {
        "version": CACHE_PLAN_VERSION,
        "profile_id": profile.profile_id,
        "reference_audio": _file_cache_identity(profile.reference_audio),
        "emotion_vector": profile.emotion_vector,
        "emotion_text": profile.emotion_text,
        "emotion_audio": _file_cache_identity(profile.emotion_audio),
        "emotion_weight": profile.emotion_weight,
        "model_dir": str(model_dir),
        "model_files": _model_cache_identity(model_dir),
        "device": device,
        "fp16": True,
        "max_chars": max_chars,
    }
    config_digest = _cache_digest(cache_config)
    old_plan = _read_cache_plan(cache_plan_path)
    old_tasks = old_plan.get("tasks") if old_plan.get("config_digest") == config_digest else None
    if not isinstance(old_tasks, dict):
        old_tasks = {}
    old_started_at_ns = int(old_plan.get("started_at_ns") or 0)
    pending_rows: list[dict[str, str]] = []
    task_plan: dict[str, dict[str, object]] = {}
    reused = 0
    run_started_at_ns = time.time_ns()
    for row in rows:
        output_path = render_dir / row["output"]
        signature = _cache_digest(
            {"config_digest": config_digest, "text": row["text"], "output": row["output"]}
        )
        old_task = old_tasks.get(row["output"])
        old_status = str(old_task.get("status") or "") if isinstance(old_task, dict) else ""
        reusable = (
            isinstance(old_task, dict)
            and old_task.get("signature") == signature
            and old_status in {"complete", "pending"}
            and _wav_is_usable(output_path)
            and (old_status == "complete" or output_path.stat().st_mtime_ns >= old_started_at_ns)
        )
        if reusable:
            reused += 1
            status = "complete"
        else:
            output_path.unlink(missing_ok=True)
            pending_rows.append(row)
            status = "pending"
        task_plan[row["output"]] = {"signature": signature, "status": status}
    plan: dict[str, object] = {
        "version": CACHE_PLAN_VERSION,
        "config_digest": config_digest,
        "config": cache_config,
        "started_at_ns": run_started_at_ns,
        "tasks": task_plan,
    }
    _write_cache_plan(cache_plan_path, plan)
    pending_batch_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in pending_rows)
        + ("\n" if pending_rows else ""),
        encoding="utf-8",
    )
    cmd = [
        str(cli),
        "batch",
        "--batch-file",
        str(pending_batch_path),
        "--voice",
        str(profile.reference_audio),
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
    if profile.emotion_vector is not None:
        cmd.extend(["--emotion-vector", ",".join(f"{item:g}" for item in profile.emotion_vector)])
    elif profile.emotion_text is not None:
        cmd.extend(["--emotion-text", profile.emotion_text])
    elif profile.emotion_audio is not None:
        cmd.extend(["--emotion-audio", str(profile.emotion_audio)])
    if any(source is not None for source in (profile.emotion_vector, profile.emotion_text, profile.emotion_audio)):
        cmd.extend(["--emotion-weight", f"{profile.emotion_weight:g}"])

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    log_path = render_dir / "indextts2.log"
    if pending_rows:
        timeout_raw = os.environ.get("BRIEFING_INDEXTTS2_TIMEOUT", "").strip()
        try:
            batch_timeout: float | None = float(timeout_raw) if timeout_raw else 3600.0
        except ValueError:
            batch_timeout = 3600.0
        if batch_timeout is not None and batch_timeout <= 0:
            batch_timeout = None
        completed = subprocess.run(
            cmd,
            cwd=root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=batch_timeout,
        )
        log_text = f"Cache: reused {reused}, generated {len(pending_rows)}\n{completed.stdout or ''}"
        log_path.write_text(log_text, encoding="utf-8-sig")
        if completed.returncode != 0:
            raise RuntimeError(f"IndexTTS2 batch failed with exit code {completed.returncode}; see {log_path}")
    else:
        log_path.write_text(f"Cache hit: reused all {reused} tasks\n", encoding="utf-8-sig")
    expected_fragments = [path for group in fragment_groups for path in group]
    missing = [str(path) for path in expected_fragments if not _wav_is_usable(path)]
    if missing:
        raise RuntimeError(f"IndexTTS2 did not create expected audio files: {', '.join(missing)}")
    for task in task_plan.values():
        task["status"] = "complete"
    plan["tasks"] = task_plan
    _write_cache_plan(cache_plan_path, plan)
    for fragments, output in zip(fragment_groups, outputs, strict=True):
        if len(fragments) > 1:
            _concatenate_wav_fragments(fragments, output)
    return outputs, profile.profile_id

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any


REVIEW_DIR_NAME = "review"
PENDING_RENDER_NAME = "render-required.json"
RENDER_CONTRACT_NAME = "render-contract.json"
TIMING_FIELDS = {"start", "end", "duration"}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8-sig")


def _segments(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        payload = payload.get("segments")
    return [segment for segment in payload if isinstance(segment, dict)] if isinstance(payload, list) else []


def load_manuscript_segments(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        return _segments(_read_json(path))
    except Exception:
        return []


def load_script_segments(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        return _segments(_read_json(path))
    except Exception:
        return []


def segments_fingerprint(segments: list[dict[str, Any]]) -> str:
    """Fingerprint visible/rendered segment data while ignoring generated timings."""
    normalized = [{key: value for key, value in segment.items() if key not in TIMING_FIELDS} for segment in segments]
    encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def active_review_manuscript_path(run_dir: Path) -> Path | None:
    root = Path(run_dir)
    for name in ["morning-final-script.json", "final-script.json"]:
        path = root / REVIEW_DIR_NAME / name
        if path.exists():
            return path
    return None


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


def _is_review_manuscript(path: Path, run_dir: Path) -> bool:
    try:
        path.resolve().relative_to((Path(run_dir) / REVIEW_DIR_NAME).resolve())
        return True
    except ValueError:
        return False


def mark_render_required(run_dir: Path, manuscript_path: Path | None = None) -> dict[str, str] | None:
    """Mark a reviewed manuscript as requiring a fresh video render."""
    root = Path(run_dir).resolve()
    manuscript = Path(manuscript_path) if manuscript_path else active_review_manuscript_path(root)
    if not manuscript or not manuscript.exists():
        return None
    segments = load_manuscript_segments(manuscript)
    payload = {
        "version": 1,
        "manuscript": _relative(manuscript, root),
        "fingerprint": segments_fingerprint(segments),
        "marked_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_json(root / REVIEW_DIR_NAME / PENDING_RENDER_NAME, payload)
    return {key: str(value) for key, value in payload.items()}


def record_rendered_manuscript(run_dir: Path, manuscript_path: Path, script_path: Path) -> dict[str, str] | None:
    """Record a successful reviewed render and clear only its matching stale marker."""
    root = Path(run_dir).resolve()
    manuscript = Path(manuscript_path)
    if not _is_review_manuscript(manuscript, root):
        return None
    video = root / "final.mp4"
    if not video.exists() or video.stat().st_size == 0:
        return None

    manuscript_segments = load_manuscript_segments(manuscript)
    script_segments = load_script_segments(Path(script_path))
    source_fingerprint = segments_fingerprint(manuscript_segments)
    script_fingerprint = segments_fingerprint(script_segments)
    payload = {
        "version": 1,
        "manuscript": _relative(manuscript, root),
        "manuscript_fingerprint": source_fingerprint,
        "script": _relative(Path(script_path), root),
        "script_fingerprint": script_fingerprint,
        "rendered_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_json(root / REVIEW_DIR_NAME / RENDER_CONTRACT_NAME, payload)

    pending_path = root / REVIEW_DIR_NAME / PENDING_RENDER_NAME
    if pending_path.exists() and source_fingerprint == script_fingerprint:
        try:
            pending = _read_json(pending_path)
        except Exception:
            pending = {}
        if isinstance(pending, dict) and pending.get("fingerprint") == source_fingerprint:
            pending_path.unlink()
    return {key: str(value) for key, value in payload.items()}


def verify_review_render_contract(
    run_dir: Path,
    script_path: Path | None = None,
    *,
    require_active_review: bool = True,
) -> list[str]:
    """Return publish-blocking errors when a reviewed manuscript is not the rendered script."""
    root = Path(run_dir).resolve()
    errors: list[str] = []
    active = active_review_manuscript_path(root)
    pending_path = root / REVIEW_DIR_NAME / PENDING_RENDER_NAME
    video = root / "final.mp4"

    pending_exists = pending_path.exists()
    if pending_exists:
        errors.append("reviewed manuscript is pending a fresh render; run render-reviewed before publishing")
    if not require_active_review and not pending_exists:
        return errors
    if not active:
        return errors

    rendered_script = Path(script_path) if script_path else root / "script.json"
    active_segments = load_manuscript_segments(active)
    script_segments = load_script_segments(rendered_script)
    active_fingerprint = segments_fingerprint(active_segments)
    script_fingerprint = segments_fingerprint(script_segments)
    if not script_segments:
        errors.append("reviewed manuscript exists but script.json is missing or unreadable")
    elif active_fingerprint != script_fingerprint:
        errors.append("review final-script does not match script.json; run render-reviewed before publishing")

    contract_path = root / REVIEW_DIR_NAME / RENDER_CONTRACT_NAME
    if not contract_path.exists():
        errors.append("reviewed manuscript has no successful render contract; run render-reviewed before publishing")
    else:
        try:
            contract = _read_json(contract_path)
        except Exception:
            contract = {}
            errors.append("review render contract is unreadable; run render-reviewed before publishing")
        if isinstance(contract, dict):
            if contract.get("manuscript_fingerprint") != active_fingerprint:
                errors.append("review render contract does not match the active reviewed manuscript")
            if script_segments and contract.get("script_fingerprint") != script_fingerprint:
                errors.append("review render contract does not match script.json")

    if video.exists() and active.stat().st_mtime > video.stat().st_mtime + 0.01:
        errors.append("reviewed manuscript is newer than final.mp4; run render-reviewed before publishing")
    return errors

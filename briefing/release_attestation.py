from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RELEASE_ATTESTATION_VERSION = 1
ATTESTATION_KEY_RELATIVE = ".local/release-attestation.key"
RELEASE_FILE_PATHS = {
    "video": "final.mp4",
    "cover": "cover.png",
    "script": "script.json",
    "subtitles": "subtitles.srt",
    "audio_quality": "audio-quality.json",
    "bilibili_json": "bilibili.json",
    "bilibili_markdown": "bilibili.md",
    "pinned_comment": "pinned-comment.md",
    "render_info": "render-info.json",
    "auto_quality": "auto-quality.json",
    "visual_qa_input": "review/visual-qa/visual-qa-input.json",
    "visual_agent_audit": "review/visual-qa/visual-agent-audit.json",
    "cover_copy": "review/cover.json",
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _attestation_key(repo_root: Path, *, create: bool) -> bytes:
    path = repo_root / ATTESTATION_KEY_RELATIVE
    if path.exists():
        key = path.read_bytes()
        if len(key) < 32:
            raise ValueError(f"release attestation key is invalid: {path}")
        return key
    if not create:
        raise ValueError(f"release attestation key is missing: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return _attestation_key(repo_root, create=False)
    with os.fdopen(fd, "wb") as handle:
        handle.write(key)
    return key


def _signed_payload(payload: dict[str, Any]) -> bytes:
    unsigned = {key: value for key, value in payload.items() if key != "signature"}
    return json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _config_path(manifest: dict[str, Any], repo_root: Path) -> Path:
    provenance = manifest.get("provenance") or {}
    configured = str(provenance.get("config_path") or "").strip()
    path = Path(configured) if configured else repo_root / "sources.yaml"
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve()


def capture_clean_render_provenance(
    run_dir: Path,
    manifest: dict[str, Any],
    *,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    root = (repo_root or Path(__file__).resolve().parents[1]).resolve()
    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True, stderr=subprocess.DEVNULL
        ).strip()
        status = subprocess.check_output(
            ["git", "status", "--porcelain=v1"], cwd=root, text=True, stderr=subprocess.DEVNULL
        )
    except Exception as exc:
        raise ValueError("clean release render requires a readable Git repository") from exc
    if not revision:
        raise ValueError("clean release render requires a Git commit")
    if status.strip():
        raise ValueError("clean release render requires a clean Git worktree")

    config_path = _config_path(manifest, root)
    if not config_path.exists() or not config_path.is_file():
        raise ValueError(f"release render config is missing: {config_path}")
    return {
        "version": RELEASE_ATTESTATION_VERSION,
        "run_id": str(manifest.get("run_id") or ""),
        "git_sha": revision,
        "git_dirty": False,
        "config_path": str(config_path),
        "config_sha256": file_sha256(config_path),
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "run_dir": str(Path(run_dir).resolve()),
    }


def build_release_attestation(
    run_dir: Path,
    manifest: dict[str, Any],
    *,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    run_dir = Path(run_dir).resolve()
    render_provenance = manifest.get("render_provenance") or {}
    if not isinstance(render_provenance, dict) or not render_provenance.get("git_sha"):
        raise ValueError("release attestation requires render_provenance")

    root = (repo_root or Path(__file__).resolve().parents[1]).resolve()
    current = capture_clean_render_provenance(run_dir, manifest, repo_root=root)
    for key in ("run_id", "git_sha", "config_sha256"):
        if str(current.get(key) or "") != str(render_provenance.get(key) or ""):
            raise ValueError(f"release render provenance changed before quality completion: {key}")

    files: dict[str, dict[str, str]] = {}
    for label, relative in RELEASE_FILE_PATHS.items():
        path = run_dir / relative
        if not path.exists() or not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"release attestation input is missing: {path}")
        files[label] = {"path": relative, "sha256": file_sha256(path)}

    generation = manifest.get("provenance") or {}
    attestation = {
        "version": RELEASE_ATTESTATION_VERSION,
        "status": "verified_clean_render",
        "run_id": str(manifest.get("run_id") or ""),
        "git_sha": str(render_provenance.get("git_sha") or ""),
        "git_dirty": False,
        "config_sha256": str(render_provenance.get("config_sha256") or ""),
        "render_started_at": str(render_provenance.get("captured_at") or ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "generation_git_sha": str(generation.get("git_sha") or ""),
        "generation_git_dirty": generation.get("git_dirty"),
        "files": files,
    }
    key = _attestation_key(root, create=True)
    attestation["key_id"] = hashlib.sha256(key).hexdigest()[:16]
    attestation["signature"] = hmac.new(key, _signed_payload(attestation), hashlib.sha256).hexdigest()
    return attestation


def verify_release_attestation(
    run_dir: Path,
    manifest: dict[str, Any],
    *,
    repo_root: Path | None = None,
) -> list[str]:
    run_dir = Path(run_dir).resolve()
    root = (repo_root or Path(__file__).resolve().parents[1]).resolve()
    attestation = manifest.get("release_attestation")
    if not isinstance(attestation, dict):
        return ["clean release attestation is missing"]

    errors: list[str] = []
    render_provenance = manifest.get("render_provenance") or {}
    try:
        key = _attestation_key(root, create=False)
        expected_key_id = hashlib.sha256(key).hexdigest()[:16]
        expected_signature = hmac.new(key, _signed_payload(attestation), hashlib.sha256).hexdigest()
        if str(attestation.get("key_id") or "") != expected_key_id or not hmac.compare_digest(
            str(attestation.get("signature") or ""), expected_signature
        ):
            errors.append("clean release attestation signature is invalid")
    except ValueError as exc:
        errors.append(str(exc))
    if attestation.get("version") != RELEASE_ATTESTATION_VERSION:
        errors.append("clean release attestation version is invalid")
    if attestation.get("status") != "verified_clean_render" or attestation.get("git_dirty") is not False:
        errors.append("clean release attestation is not approved")
    if str(attestation.get("run_id") or "") != str(manifest.get("run_id") or ""):
        errors.append("clean release attestation run_id does not match manifest")
    if not isinstance(render_provenance, dict) or render_provenance.get("git_dirty") is not False:
        errors.append("clean release render provenance is missing or dirty")
    else:
        for key in ("git_sha", "config_sha256"):
            if str(attestation.get(key) or "") != str(render_provenance.get(key) or ""):
                errors.append(f"clean release attestation {key} does not match render provenance")

    try:
        current = capture_clean_render_provenance(run_dir, manifest, repo_root=root)
        for key in ("run_id", "git_sha", "config_sha256"):
            if str(attestation.get(key) or "") != str(current.get(key) or ""):
                errors.append(f"clean release attestation {key} does not match current clean checkout")
    except ValueError as exc:
        errors.append(str(exc))

    files = attestation.get("files") or {}
    if not isinstance(files, dict):
        files = {}
    for label, relative in RELEASE_FILE_PATHS.items():
        row = files.get(label)
        if not isinstance(row, dict) or str(row.get("path") or "") != relative:
            errors.append(f"clean release attestation is missing file binding: {label}")
            continue
        path = run_dir / relative
        if not path.exists() or not path.is_file() or file_sha256(path) != str(row.get("sha256") or ""):
            errors.append(f"clean release attestation file changed: {label}")
    return errors

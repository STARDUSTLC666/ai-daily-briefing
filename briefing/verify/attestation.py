from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import subprocess
from typing import Any

from briefing.core import (
    effective_config_sha256,
    file_sha256,
    iso,
    now_utc,
    project_root,
    read_json,
    write_json_atomic,
)

RELEASE_ATTESTATION_VERSION = 1
ATTESTATION_KEY_RELATIVE = ".local/release-attestation.key"
ATTESTATION_FILE_NAME = "release-attestation.json"

RELEASE_FILE_PATHS: tuple[str, ...] = (
    "final.mp4",
    "cover.png",
    "script.json",
    "subtitles.srt",
    "audio-quality.json",
    "bilibili.json",
    "bilibili.md",
    "pinned-comment.md",
    "render-info.json",
    "auto-quality.json",
)

_AGENT_VISUAL_QA_PATHS: tuple[str, ...] = (
    "review/visual-qa/visual-qa-input.json",
    "review/visual-qa/visual-agent-audit.json",
)

_OPTIONAL_RELEASE_PATHS: tuple[str, ...] = (
    "review/cover.json",
    "review/agent-contract.json",
)


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


def _resolve_config_path(config_path: Path | None, repo_root: Path, manifest: dict[str, Any] | None = None) -> Path:
    if config_path is not None:
        candidate = Path(config_path)
    else:
        provenance = (manifest or {}).get("render_provenance") or (manifest or {}).get("provenance") or {}
        configured = str(provenance.get("config_path") or "").strip() if isinstance(provenance, dict) else ""
        candidate = Path(configured) if configured else (repo_root / "sources.yaml")
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    return candidate.resolve()


def capture_clean_render_provenance(config_path: Path | None = None) -> dict[str, Any]:
    """Capture clean Git worktree and configuration hashes before rendering a publishable release."""
    root = project_root().resolve()
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

    resolved_cfg = _resolve_config_path(config_path, root)
    if not resolved_cfg.is_file():
        raise ValueError(f"release render config is missing: {resolved_cfg}")

    return {
        "version": RELEASE_ATTESTATION_VERSION,
        "git_sha": revision,
        "git_dirty": False,
        "config_path": str(resolved_cfg),
        "config_sha256": file_sha256(resolved_cfg),
        "effective_config_sha256": effective_config_sha256(resolved_cfg),
        "captured_at": iso(now_utc()),
    }


def build_release_attestation(
    run_dir: Path,
    *,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build and HMAC-sign a release attestation binding every release artifact in `run_dir`."""
    run_dir = Path(run_dir).resolve()
    root = project_root().resolve()
    manifest_raw = read_json(run_dir / "manifest.json", default={})
    manifest = manifest_raw if isinstance(manifest_raw, dict) else {}

    render_prov = provenance or manifest.get("render_provenance")
    if not isinstance(render_prov, dict) or not render_prov.get("git_sha"):
        render_prov = capture_clean_render_provenance(_resolve_config_path(None, root, manifest))
    else:
        cfg_candidate = Path(str(render_prov["config_path"])) if render_prov.get("config_path") else None
        current = capture_clean_render_provenance(cfg_candidate)
        for key in ("git_sha", "config_sha256"):
            if str(current.get(key) or "") != str(render_prov.get(key) or ""):
                raise ValueError(f"release render provenance changed before quality completion: {key}")

    required_paths = list(RELEASE_FILE_PATHS)
    if (manifest.get("agent_policy") or {}).get("required") is True:
        required_paths.extend(_AGENT_VISUAL_QA_PATHS)

    files: dict[str, str] = {}
    for relative in required_paths:
        path = run_dir / relative
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"release attestation input is missing: {path}")
        files[relative] = file_sha256(path)

    for optional_rel in (*_AGENT_VISUAL_QA_PATHS, *_OPTIONAL_RELEASE_PATHS):
        if optional_rel in files:
            continue
        opt_path = run_dir / optional_rel
        if opt_path.is_file() and opt_path.stat().st_size > 0:
            files[optional_rel] = file_sha256(opt_path)

    generation = manifest.get("provenance") or {}
    attestation: dict[str, Any] = {
        "version": RELEASE_ATTESTATION_VERSION,
        "status": "verified_clean_render",
        "run_id": str(manifest.get("run_id") or render_prov.get("run_id") or ""),
        "git_sha": str(render_prov.get("git_sha") or ""),
        "git_dirty": False,
        "config_path": str(render_prov.get("config_path") or ""),
        "config_sha256": str(render_prov.get("config_sha256") or ""),
        "effective_config_sha256": str(render_prov.get("effective_config_sha256") or ""),
        "render_started_at": str(render_prov.get("captured_at") or ""),
        "created_at": iso(now_utc()),
        "generation_git_sha": str(generation.get("git_sha") or "") if isinstance(generation, dict) else "",
        "generation_git_dirty": generation.get("git_dirty") if isinstance(generation, dict) else None,
        "files": files,
    }
    key = _attestation_key(root, create=True)
    attestation["key_id"] = hashlib.sha256(key).hexdigest()[:16]
    attestation["signature"] = hmac.new(key, _signed_payload(attestation), hashlib.sha256).hexdigest()
    write_json_atomic(run_dir / ATTESTATION_FILE_NAME, attestation)
    return attestation


def _normalize_attested_files(raw_files: Any) -> dict[str, str]:
    normalized: dict[str, str] = {}
    if not isinstance(raw_files, dict):
        return normalized
    for key, val in raw_files.items():
        if isinstance(val, str):
            normalized[str(key)] = val
        elif isinstance(val, dict) and val.get("path") and val.get("sha256"):
            normalized[str(val["path"])] = str(val["sha256"])
    return normalized


def verify_release_attestation(
    run_dir: Path,
    *,
    config_path: Path | None = None,
) -> list[str]:
    """Verify HMAC signature, clean worktree provenance, and file SHA-256 bindings of a release."""
    run_dir = Path(run_dir).resolve()
    root = project_root().resolve()
    manifest_raw = read_json(run_dir / "manifest.json", default={})
    manifest = manifest_raw if isinstance(manifest_raw, dict) else {}

    attestation = (
        manifest.get("release_attestation")
        if "release_attestation" in manifest
        else read_json(run_dir / ATTESTATION_FILE_NAME, default=None)
    )
    if not isinstance(attestation, dict):
        return ["clean release attestation is missing"]

    errors: list[str] = []
    try:
        key = _attestation_key(root, create=False)
        expected_key_id = hashlib.sha256(key).hexdigest()[:16]
        expected_sig = hmac.new(key, _signed_payload(attestation), hashlib.sha256).hexdigest()
        if str(attestation.get("key_id") or "") != expected_key_id or not hmac.compare_digest(
            str(attestation.get("signature") or ""), expected_sig
        ):
            errors.append("clean release attestation signature is invalid")
    except ValueError as exc:
        errors.append(str(exc))

    if attestation.get("version") != RELEASE_ATTESTATION_VERSION:
        errors.append("clean release attestation version is invalid")
    if attestation.get("status") != "verified_clean_render" or attestation.get("git_dirty") is not False:
        errors.append("clean release attestation is not approved")

    manifest_run_id = str(manifest.get("run_id") or "")
    if manifest_run_id and str(attestation.get("run_id") or "") != manifest_run_id:
        errors.append("clean release attestation run_id does not match manifest")

    render_prov = manifest.get("render_provenance")
    if render_prov is not None:
        if not isinstance(render_prov, dict) or render_prov.get("git_dirty") is not False:
            errors.append("clean release render provenance is missing or dirty")
        else:
            for key_name in ("git_sha", "config_sha256"):
                if str(attestation.get(key_name) or "") != str(render_prov.get(key_name) or ""):
                    errors.append(f"clean release attestation {key_name} does not match render provenance")

    try:
        resolved_cfg = config_path or (
            Path(str(attestation["config_path"])) if attestation.get("config_path") else _resolve_config_path(None, root, manifest)
        )
        current = capture_clean_render_provenance(resolved_cfg)
        for key_name in ("git_sha", "config_sha256"):
            if str(attestation.get(key_name) or "") != str(current.get(key_name) or ""):
                errors.append(f"clean release attestation {key_name} does not match current clean checkout")
    except ValueError as exc:
        errors.append(str(exc))

    bound_files = _normalize_attested_files(attestation.get("files"))
    required_paths = list(RELEASE_FILE_PATHS)
    if (manifest.get("agent_policy") or {}).get("required") is True:
        for rel in _AGENT_VISUAL_QA_PATHS:
            if rel not in required_paths:
                required_paths.append(rel)

    for rel in required_paths:
        if rel not in bound_files:
            errors.append(f"clean release attestation is missing file binding: {rel}")

    for rel, expected_hash in bound_files.items():
        path = run_dir / rel
        if not path.is_file() or file_sha256(path) != expected_hash:
            errors.append(f"clean release attestation file changed: {rel}")

    return errors

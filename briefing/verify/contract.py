from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import quote, urlparse, urlsplit, urlunsplit

from briefing.core import (
    clean_text,
    file_sha256,
    iso,
    now_utc,
    read_json,
    write_json_atomic,
    write_text_atomic,
)

REVIEW_DIR_NAME = "review"
PENDING_RENDER_NAME = "render-required.json"
RENDER_CONTRACT_NAME = "render-contract.json"
TIMING_FIELDS: frozenset[str] = frozenset({"start", "end", "duration"})
AGENT_NAME = "codex"

_CRITICAL_TOKEN_RE = re.compile(
    r"(?i)(?:\d+(?:\.\d+)?(?:%|ms|s|x|b|m|k|天|小时|分钟|次|个|行|倍)?|"
    r"(?:gpt|claude|gemini|gemma|qwen|deepseek|kimi|llama|mistral|grok|minimax|glm|hunyuan|nova)"
    r"[-‐‑–—_\s]?[a-z]*\d[a-z0-9_.‐‑–—-]*)"
)

_REQUIRED_COPY_CHECKS: frozenset[str] = frozenset(
    {
        "source_opened",
        "facts_bound",
        "uncertainty_preserved",
        "chinese_copy",
        "sentence_complete",
        "no_generic_filler",
        "impact_language_specific",
        "no_clickbait_overreach",
    }
)

_REQUIRED_VISUAL_CHECKS: frozenset[str] = frozenset(
    {
        "layout_no_clipping",
        "text_legible",
        "evidence_readable",
        "motion_coherent",
        "cover_truthful",
        "subtitles_safe_area",
        "no_broken_frames",
        "narration_complete",
        "subtitle_sync",
        "audio_no_artifacts",
    }
)

_OVERCLAIM_TERMS: frozenset[str] = frozenset(
    {
        "全面开放",
        "永久免费",
        "完全免费",
        "已收购",
        "正式上线",
        "正式发布",
        "支持视频生成",
        "开放权重",
        "已经开源",
    }
)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _extract_segments(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        payload = payload.get("segments")
    return [seg for seg in payload if isinstance(seg, dict)] if isinstance(payload, list) else []


def _load_segments_file(path: Path) -> list[dict[str, Any]]:
    payload = read_json(path, default=None)
    return _extract_segments(payload) if payload is not None else []


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


def _active_review_manuscript_path(run_dir: Path) -> Path | None:
    root = Path(run_dir)
    for name in ("morning-final-script.json", "final-script.json"):
        path = root / REVIEW_DIR_NAME / name
        if path.exists():
            return path
    return None


def segments_fingerprint(segments: list[dict[str, Any]]) -> str:
    """Fingerprint visible/rendered segment data while ignoring generated timings."""
    normalized = [{key: val for key, val in seg.items() if key not in TIMING_FIELDS} for seg in segments]
    return _fingerprint(normalized)


def mark_render_required(
    run_dir: Path,
    *,
    reason: str = "manuscript_updated",
    source_path: Path | None = None,
) -> Path:
    """Mark a reviewed manuscript as requiring a fresh video render."""
    root = Path(run_dir).resolve()
    manuscript = Path(source_path) if source_path else _active_review_manuscript_path(root)
    segments = _load_segments_file(manuscript) if manuscript and manuscript.exists() else []
    payload = {
        "version": 1,
        "reason": reason,
        "manuscript": _relative(manuscript, root) if manuscript else "",
        "fingerprint": segments_fingerprint(segments) if segments else "",
        "marked_at": iso(now_utc()),
    }
    return write_json_atomic(root / REVIEW_DIR_NAME / PENDING_RENDER_NAME, payload)


def record_render_contract(
    run_dir: Path,
    segments: list[dict[str, Any]],
    *,
    manuscript_path: Path | None = None,
) -> Path:
    """Record a successful render contract and clear matching stale render-required markers."""
    root = Path(run_dir).resolve()
    manuscript = Path(manuscript_path) if manuscript_path else _active_review_manuscript_path(root)
    script_fp = segments_fingerprint(segments)
    manuscript_segments = _load_segments_file(manuscript) if manuscript and manuscript.exists() else []
    manuscript_fp = segments_fingerprint(manuscript_segments) if manuscript_segments else script_fp
    payload = {
        "version": 1,
        "manuscript": _relative(manuscript, root) if manuscript else "",
        "manuscript_fingerprint": manuscript_fp,
        "script": "script.json",
        "script_fingerprint": script_fp,
        "rendered_at": iso(now_utc()),
    }
    contract_path = write_json_atomic(root / REVIEW_DIR_NAME / RENDER_CONTRACT_NAME, payload)

    pending_path = root / REVIEW_DIR_NAME / PENDING_RENDER_NAME
    if pending_path.exists() and manuscript_fp == script_fp:
        pending = read_json(pending_path, default={})
        pending_fp = str(pending.get("fingerprint") or "") if isinstance(pending, dict) else ""
        if not pending_fp or pending_fp == manuscript_fp:
            pending_path.unlink(missing_ok=True)
    return contract_path


def verify_review_render_contract(
    run_dir: Path,
    script_segments: list[dict[str, Any]] | None = None,
    *,
    require_active_review: bool = True,
) -> list[str]:
    """Return publish-blocking errors when a reviewed manuscript is out of sync with `script.json`."""
    root = Path(run_dir).resolve()
    errors: list[str] = []
    active = _active_review_manuscript_path(root)
    pending_path = root / REVIEW_DIR_NAME / PENDING_RENDER_NAME
    video = root / "final.mp4"

    pending_exists = pending_path.exists()
    if pending_exists:
        errors.append("reviewed manuscript is pending a fresh render; run render before publishing")
    if not require_active_review and not pending_exists:
        return errors
    if not active:
        return errors

    active_segments = _load_segments_file(active)
    rendered_segments = (
        _load_segments_file(script_segments)
        if isinstance(script_segments, Path)
        else (script_segments if script_segments is not None else _load_segments_file(root / "script.json"))
    )
    active_fp = segments_fingerprint(active_segments)
    script_fp = segments_fingerprint(rendered_segments)

    if not rendered_segments:
        errors.append("reviewed manuscript exists but script.json is missing or unreadable")
    elif active_fp != script_fp:
        errors.append("review final-script does not match script.json; run render before publishing")

    contract_path = root / REVIEW_DIR_NAME / RENDER_CONTRACT_NAME
    if not contract_path.exists():
        errors.append("reviewed manuscript has no successful render contract; run render before publishing")
    else:
        try:
            contract = read_json(contract_path)
        except Exception:
            contract = None
            errors.append("review render contract is unreadable; run render before publishing")
        if isinstance(contract, dict):
            if contract.get("manuscript_fingerprint") != active_fp:
                errors.append("review render contract does not match the active reviewed manuscript")
            if rendered_segments and contract.get("script_fingerprint") != script_fp:
                errors.append("review render contract does not match script.json")

    if video.exists() and active.stat().st_mtime > video.stat().st_mtime + 0.01:
        errors.append("reviewed manuscript is newer than final.mp4; run render before publishing")
    return errors


def _read_object(path: Path, label: str) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"missing {label}: {path}")
    try:
        value = read_json(path)
    except Exception as exc:
        raise ValueError(f"{label} parse failed: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} root must be an object")
    return value


def _manifest_semantic_fingerprint(manifest: dict[str, Any]) -> str:
    return _fingerprint(
        {
            "run_id": manifest.get("run_id"),
            "run_date": manifest.get("run_date"),
            "package_quality": manifest.get("package_quality"),
            "source_coverage": manifest.get("source_coverage"),
            "selected": manifest.get("selected"),
        }
    )


def _selected_story_map(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for card in manifest.get("selected") or []:
        if not isinstance(card, dict) or not isinstance(card.get("story_spec"), dict):
            continue
        story_id = str(card["story_spec"].get("story_id") or "")
        if story_id:
            result[story_id] = card
    return result


def _approved_claims(card: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(claim.get("claim_id") or ""): claim
        for claim in card.get("story_spec", {}).get("claims") or []
        if isinstance(claim, dict)
        and claim.get("renderable") is True
        and claim.get("verifiable") is True
        and claim.get("evidence_urls")
        and str(claim.get("claim_id") or "")
    }


def _news_segments(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in state.get("segments") or []
        if isinstance(row, dict) and str(row.get("kind") or "news") == "news" and row.get("enabled") is not False
    ]


def _normalize_url(value: Any) -> str:
    text = str(value or "").strip()
    try:
        parts = urlsplit(text)
    except Exception:
        return ""
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def _critical_tokens(value: str) -> set[str]:
    return {re.sub(r"\s+", "-", token.lower()) for token in _CRITICAL_TOKEN_RE.findall(value)}


def _public_segment_text(segment: dict[str, Any]) -> str:
    parts: list[Any] = [segment.get(key) for key in ("title", "headline", "caption", "text")]
    for card in segment.get("cards") or []:
        if isinstance(card, dict):
            parts.extend([card.get("title"), card.get("body")])
    for page in segment.get("visual_pages") or []:
        if not isinstance(page, dict):
            continue
        parts.extend([page.get("title"), page.get("lead")])
        for card in page.get("cards") or []:
            if isinstance(card, dict):
                parts.extend([card.get("title"), card.get("body")])
    return " ".join(str(val or "") for val in parts)


def _copy_norm(value: Any) -> str:
    return re.sub(r"[\s。！？!?；;，,：:'\"“”‘’（）()【】\[\]]+", "", clean_text(str(value or "")).lower())


def _segment_copy_blocks(segment: dict[str, Any]) -> set[str]:
    text = clean_text(str(segment.get("text") or ""))
    sentences = [clean_text(row) for row in re.findall(r".+?(?:[。！？!?]|$)", text)]
    values: list[Any] = [segment.get("title"), segment.get("headline"), segment.get("caption"), *sentences]
    for card in segment.get("cards") or []:
        if isinstance(card, dict):
            values.extend([card.get("title"), card.get("body")])
    for page in segment.get("visual_pages") or []:
        if not isinstance(page, dict):
            continue
        values.extend([page.get("title"), page.get("lead"), page.get("source")])
        for card in page.get("cards") or []:
            if isinstance(card, dict):
                values.extend([card.get("title"), card.get("body")])
    return {norm for val in values if (norm := _copy_norm(val))}


def _compact_agent_evidence(rows: Any) -> list[dict[str, Any]]:
    fields = (
        "source",
        "tier",
        "reliability",
        "title",
        "url",
        "final_url",
        "published_at",
        "discovery_url",
        "screenshot_status",
        "screenshot_path",
        "screenshot_capture_url",
        "screenshot_identity",
        "screenshot_kind",
    )
    compact: list[dict[str, Any]] = []
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        row = {key: raw[key] for key in fields if raw.get(key) not in (None, "", [], {})}
        excerpt = clean_text(str(raw.get("excerpt") or ""), 1200)
        if excerpt:
            row["excerpt"] = excerpt
        compact.append(row)
    return compact


def create_agent_package(run_dir: Path) -> dict[str, Any]:
    """Freeze the local review draft into a bounded work order for agent editing."""
    root = Path(run_dir).resolve()
    rdir = root / REVIEW_DIR_NAME
    manifest_path = root / "manifest.json"
    state_path = rdir / "state.json"
    draft_path = rdir / "draft-state.json"
    brief_path = rdir / "agent-brief.json"
    audit_path = rdir / "agent-audit.json"
    template_path = rdir / "agent-audit.template.json"

    manifest = _read_object(manifest_path, "manifest.json")
    state = _read_object(state_path, "review/state.json")
    draft = _read_object(draft_path, "review/draft-state.json")
    if _fingerprint(state) != _fingerprint(draft):
        raise ValueError("agent package requires a fresh unedited review state; rerun prepare --force")

    stories = _selected_story_map(manifest)
    state_rows = {str(row.get("story_id") or ""): row for row in _news_segments(state)}
    rows: list[dict[str, Any]] = []
    for story_id, card in stories.items():
        spec = card["story_spec"]
        current = state_rows.get(story_id) or {}
        claims = list(_approved_claims(card).values())
        rows.append(
            {
                "story_id": story_id,
                "locked_role": card.get("editorial_tier"),
                "risk": card.get("risk"),
                "source_status": card.get("source_status"),
                "event_title": card.get("title"),
                "approved_claims": claims,
                "evidence": _compact_agent_evidence(card.get("evidence") or []),
                "current_copy": {
                    "title": current.get("title"),
                    "text": current.get("text"),
                    "cards": current.get("cards") or [],
                    "claim_ids": current.get("claim_ids") or [],
                },
                "warnings": spec.get("warnings") or [],
            }
        )

    manifest_fp = _manifest_semantic_fingerprint(manifest)
    draft_fp = _fingerprint(state)
    x_required = ((manifest.get("source_coverage") or {}).get("x") or {}).get("required") is not False
    brief = {
        "version": 1,
        "agent": AGENT_NAME,
        "created_at": iso(now_utc()),
        "run_dir": str(root),
        "run_id": manifest.get("run_id"),
        "manifest_fingerprint": manifest_fp,
        "draft_state_fingerprint": draft_fp,
        "editable_state": str(state_path),
        "audit_output": str(audit_path),
        "editable_fields": ["intro/outro text", "news sentence order", "approved copy-block selection"],
        "immutable_fields": [
            "segment order",
            "kind",
            "story_id",
            "claim_ids",
            "generation_path",
            "editorial_tier",
            "position",
            "total",
        ],
        "rules": [
            (
                "先检查 X 官方公司、官方产品和关键负责人原帖，再核对官网；RSS、Google News 和镜像只作后台线索，不能出现在视频文案。"
                if x_required
                else "本期为公开网页来源版，逐条核对官网原文；未检查 X，不得声称覆盖 X 或采用未审计的社交传闻。"
            ),
            "不得新增、删除或替换 story_id；不得把一线消息或传闻写成正式公告。",
            "新闻段只能重排或删减冻结稿中的 approved copy blocks，不能自由改写事实句。",
            "标题、口播和卡片不得补造数字、版本、价格、动作或可用范围。",
            "标题、口播、卡片和字幕不得出现省略号；每个事实句必须完整。",
            "只编辑运行产物，不修改仓库源码，不上传 B 站。",
        ],
        "stories": rows,
    }
    write_json_atomic(brief_path, brief)

    template = {
        "version": 1,
        "agent": AGENT_NAME,
        "manifest_fingerprint": manifest_fp,
        "draft_state_fingerprint": draft_fp,
        "source_review_passed": False,
        "copy_review_passed": False,
        "no_upload": True,
        "stories": [
            {
                "story_id": row["story_id"],
                "verdict": "pending",
                "selected_claim_ids": row["current_copy"]["claim_ids"],
                "checked_urls": [],
                "copy_checks": {key: False for key in sorted(_REQUIRED_COPY_CHECKS)},
                "notes": "",
            }
            for row in rows
        ],
    }
    write_json_atomic(template_path, template)

    manifest["agent_policy"] = {
        "required": True,
        "agent": AGENT_NAME,
        "workflow": "codex_source_copy_visual_v1",
        "brief": str(brief_path),
        "draft_state_fingerprint": draft_fp,
    }
    write_json_atomic(manifest_path, manifest)
    return {
        "ok": True,
        "run_dir": str(root),
        "agent_brief": str(brief_path),
        "editable_state": str(state_path),
        "audit_template": str(template_path),
        "audit_output": str(audit_path),
        "story_count": len(rows),
    }


def _validate_agent_edits(run_dir: Path) -> list[str]:
    root = Path(run_dir).resolve()
    rdir = root / REVIEW_DIR_NAME
    errors: list[str] = []
    try:
        manifest = _read_object(root / "manifest.json", "manifest.json")
        state = _read_object(rdir / "state.json", "review/state.json")
        draft = _read_object(rdir / "draft-state.json", "review/draft-state.json")
        audit = _read_object(rdir / "agent-audit.json", "review/agent-audit.json")
    except ValueError as exc:
        return [str(exc)]

    stories = _selected_story_map(manifest)
    news = _news_segments(state)
    draft_news = _news_segments(draft)
    state_segments = [row for row in state.get("segments") or [] if isinstance(row, dict) and row.get("enabled") is not False]
    draft_segments = [row for row in draft.get("segments") or [] if isinstance(row, dict) and row.get("enabled") is not False]

    if len(state_segments) != len(draft_segments) or [str(r.get("kind") or "news") for r in state_segments] != [
        str(r.get("kind") or "news") for r in draft_segments
    ]:
        errors.append("agent state added, removed, disabled, or reordered segment kinds")

    story_ids = [str(row.get("story_id") or "") for row in news]
    draft_story_ids = [str(row.get("story_id") or "") for row in draft_news]
    if len(story_ids) != len(set(story_ids)):
        errors.append("agent state contains duplicate story_id values")
    if set(story_ids) != set(stories):
        errors.append("agent state story set does not exactly match manifest selection")
    if story_ids != draft_story_ids:
        errors.append("agent state changed the frozen story order")
    if any(str(row.get("generation_path") or "") != "structured_editorial_plan" for row in news):
        errors.append("agent state changed an immutable generation_path")

    audit_rows = [row for row in audit.get("stories") or [] if isinstance(row, dict)]
    audit_by_id = {str(row.get("story_id") or ""): row for row in audit_rows}
    if len(audit_by_id) != len(audit_rows) or set(audit_by_id) != set(stories):
        errors.append("agent audit story set does not exactly match manifest selection")
    if audit.get("agent") != AGENT_NAME:
        errors.append("agent audit was not produced by codex")
    if audit.get("manifest_fingerprint") != _manifest_semantic_fingerprint(manifest):
        errors.append("agent audit is not bound to the current manifest selection")
    expected_draft_fp = str((manifest.get("agent_policy") or {}).get("draft_state_fingerprint") or "")
    if not expected_draft_fp or _fingerprint(draft) != expected_draft_fp:
        errors.append("frozen agent draft does not match the manifest policy")
    if audit.get("draft_state_fingerprint") != expected_draft_fp:
        errors.append("agent audit is not bound to the frozen draft")
    if audit.get("source_review_passed") is not True or audit.get("copy_review_passed") is not True:
        errors.append("agent source/copy review is not approved")
    if audit.get("no_upload") is not True:
        errors.append("agent audit must preserve the no-upload boundary")

    manifest_text = _canonical(manifest.get("selected") or [])
    allowed_intro_tokens = _critical_tokens(manifest_text + f" {len(stories)}")
    for segment in state_segments:
        if str(segment.get("kind") or "news") == "news":
            continue
        public_text = _public_segment_text(segment)
        extra_tokens = _critical_tokens(public_text) - allowed_intro_tokens
        if extra_tokens:
            errors.append(f"agent intro/outro introduces unapproved critical tokens: {sorted(extra_tokens)}")
        for term in _OVERCLAIM_TERMS:
            if term in public_text and term not in manifest_text:
                errors.append(f"agent intro/outro introduces an unsupported claim: {term}")

    for position, segment in enumerate(news, 1):
        story_id = str(segment.get("story_id") or "")
        card = stories.get(story_id)
        draft_segment = next((row for row in draft_news if str(row.get("story_id") or "") == story_id), None)
        if card is None:
            continue
        if draft_segment is None:
            errors.append(f"agent story {position} is missing from frozen draft")
            continue
        for key in ("kind", "story_id", "claim_ids", "generation_path", "editorial_tier", "position", "total"):
            if segment.get(key) != draft_segment.get(key):
                errors.append(f"agent story {position} changed immutable field {key}")
        claims = _approved_claims(card)
        selected_ids = [str(val) for val in segment.get("claim_ids") or [] if str(val)]
        if not selected_ids or len(selected_ids) != len(set(selected_ids)):
            errors.append(f"agent story {position} has no unique selected claim_ids")
        if any(cid not in claims for cid in selected_ids):
            errors.append(f"agent story {position} selected an unapproved claim_id")

        public_text = _public_segment_text(segment)
        if not clean_text(str(segment.get("title") or "")) or not clean_text(str(segment.get("text") or "")):
            errors.append(f"agent story {position} has empty public copy")
        if "http://" in public_text.lower() or "https://" in public_text.lower() or "```" in public_text:
            errors.append(f"agent story {position} leaks a URL or code fence into public copy")
        if re.search(r"…|\.{3,}", public_text):
            errors.append(f"agent story {position} contains an ellipsis in public copy")
        extra_copy_blocks = _segment_copy_blocks(segment) - _segment_copy_blocks(draft_segment)
        if extra_copy_blocks:
            errors.append(f"agent story {position} contains freeform copy outside frozen approved blocks")

        allowed_text = " ".join(
            [
                str(card.get("title") or ""),
                str(card.get("entity") or ""),
                _public_segment_text(draft_segment),
                *(str(claims[cid].get("text") or "") for cid in selected_ids if cid in claims),
            ]
        )
        extra_tokens = _critical_tokens(public_text) - _critical_tokens(allowed_text)
        if extra_tokens:
            errors.append(f"agent story {position} introduces unapproved critical tokens: {sorted(extra_tokens)}")

        audit_row = audit_by_id.get(story_id) or {}
        if audit_row.get("verdict") != "approved":
            errors.append(f"agent story {position} verdict is not approved")
        if [str(val) for val in audit_row.get("selected_claim_ids") or []] != selected_ids:
            errors.append(f"agent story {position} audit claim_ids do not match edited state")
        checks = audit_row.get("copy_checks") or {}
        if any(checks.get(key) is not True for key in _REQUIRED_COPY_CHECKS):
            errors.append(f"agent story {position} copy checks are incomplete")
        checked = {_normalize_url(val) for val in audit_row.get("checked_urls") or []}
        checked.discard("")
        for cid in selected_ids:
            if cid not in claims:
                continue
            supporting = {_normalize_url(url) for url in claims[cid].get("evidence_urls") or []}
            supporting.discard("")
            if not supporting or not checked.intersection(supporting):
                errors.append(f"agent story {position} did not open supporting evidence for claim_id {cid}")
            if claims[cid].get("first_party_supported") is True:
                first_party = {
                    _normalize_url(row.get("url"))
                    for row in card.get("evidence") or []
                    if isinstance(row, dict) and str(row.get("reliability") or "").startswith("official")
                }
                first_party.discard("")
                if first_party and not checked.intersection(first_party):
                    errors.append(f"agent story {position} did not open available first-party evidence")
    return errors


def _safe_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\x00", "").split())


def _plain_cards(cards: Any) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if not isinstance(cards, list):
        return rows
    for card in cards:
        if not isinstance(card, dict):
            continue
        title = _safe_text(card.get("title"))
        body = _safe_text(card.get("body") or card.get("content"))
        if title or body:
            rows.append({"title": title, "body": body})
    return rows


def _public_evidence_source(evidence: dict[str, Any], fallback: str = "") -> str:
    source_url = _safe_text(evidence.get("url") or evidence.get("screenshot_capture_url"))
    try:
        parsed = urlparse(source_url)
    except ValueError:
        parsed = None
    if parsed and parsed.hostname and parsed.hostname.lower() in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}:
        handle = next((part for part in parsed.path.split("/") if part), "")
        if handle:
            return f"X / @{handle}"
    source = _safe_text(evidence.get("source"))
    if source.lower().startswith("codex verified"):
        source = ""
    return source or fallback or (parsed.hostname if parsed and parsed.hostname else "原始来源")


def _merge_required_evidence_images(run_dir: Path, manifest: dict[str, Any], state: dict[str, Any]) -> None:
    required_by_story: dict[str, list[dict[str, Any]]] = {}
    for card in manifest.get("selected") or []:
        if not isinstance(card, dict) or not isinstance(card.get("story_spec"), dict):
            continue
        story_id = _safe_text(card["story_spec"].get("story_id"))
        if not story_id:
            continue
        rows: list[dict[str, Any]] = []
        for ev_idx, ev in enumerate(card.get("evidence") or []):
            if not isinstance(ev, dict):
                continue
            if str(ev.get("screenshot_required") or "").strip().lower() not in {"1", "true", "yes", "on"}:
                continue
            if _safe_text(ev.get("screenshot_status")).lower() != "captured":
                continue
            raw_path = _safe_text(ev.get("screenshot_path"))
            if not raw_path:
                continue
            path = Path(raw_path)
            path = (path if path.is_absolute() else (run_dir / path)).resolve()
            if not path.is_file():
                continue
            source_url = _safe_text(ev.get("url") or ev.get("screenshot_capture_url"))
            rows.append(
                {
                    "id": f"required-{story_id[:10]}-{ev_idx:02d}",
                    "label": _safe_text(ev.get("title") or card.get("title") or "证据截图"),
                    "path": str(path),
                    "url": "/media?path=" + quote(str(path), safe=""),
                    "source": _public_evidence_source(ev),
                    "source_url": source_url,
                    "enabled": True,
                    "required": True,
                }
            )
        if rows:
            required_by_story[story_id] = rows

    for seg in state.get("segments") or []:
        if not isinstance(seg, dict):
            continue
        required = required_by_story.get(_safe_text(seg.get("story_id"))) or []
        if not required:
            continue
        images = [img for img in seg.get("images") or [] if isinstance(img, dict)]
        fallback_src = next((_safe_text(img.get("source")) for img in images if _safe_text(img.get("source"))), "")
        seen_paths = {str(Path(_safe_text(img["path"])).resolve()).lower() for img in images if _safe_text(img.get("path"))}
        for img in required:
            norm = str(Path(str(img["path"])).resolve()).lower()
            if norm in seen_paths:
                continue
            if not _safe_text(img.get("source")) and fallback_src:
                img["source"] = fallback_src
            images.append(img)
            seen_paths.add(norm)
        seg["images"] = images


def _unique_labels(segments: list[dict[str, Any]]) -> list[str]:
    labels: list[str] = []
    seen: dict[str, int] = {}
    news_idx = 0
    for seg in segments:
        kind = str(seg.get("kind") or "news")
        if kind == "intro":
            raw = "开场"
        elif kind == "outro":
            raw = "收尾"
        else:
            news_idx += 1
            raw = _safe_text(seg.get("bottom_active")) or f"{news_idx:02d} {_safe_text(seg.get('title'))[:10]}"
        raw = raw or f"{len(labels) + 1:02d}"
        count = seen.get(raw, 0) + 1
        seen[raw] = count
        labels.append(raw if count == 1 else f"{raw}-{count}")
    return labels


def _final_visual_pages(seg: dict[str, Any], cards: list[dict[str, str]]) -> list[dict[str, Any]]:
    title = _safe_text(seg.get("title") or seg.get("caption") or "新闻")
    pages = [deepcopy(p) for p in seg.get("visual_pages") or [] if isinstance(p, dict) and "evidenceVisual" not in p]
    if pages:
        pages[0]["title"] = pages[0].get("title") or title
        pages[0]["cards"] = cards
    else:
        pages.append({"kind": "cards", "title": title, "cards": cards})
    if str(seg.get("kind") or "news") == "news":
        pages[0]["kind"] = "brief" if str(seg.get("editorial_tier") or "headline") == "brief" else "cards"
    for image in seg.get("images") or []:
        if not isinstance(image, dict) or image.get("enabled") is False or not image.get("path"):
            continue
        label = _safe_text(image.get("label") or "证据截图")
        source = _safe_text(image.get("source") or label)
        source_url = _safe_text(image.get("source_url"))
        pages.append(
            {
                "kind": "evidence",
                "title": label,
                "lead": _safe_text(seg.get("caption") or title),
                "source": source,
                "source_url": source_url,
                "evidenceVisual": {
                    "source": source,
                    "title": label,
                    "url": source_url,
                    "image": str(image["path"]),
                    "status": "captured",
                    "required": True,
                },
                "cards": [],
                "review_generated": True,
            }
        )
    return pages


def _state_to_final_script(state: dict[str, Any]) -> dict[str, Any]:
    enabled = [seg for seg in state.get("segments") or [] if isinstance(seg, dict) and seg.get("enabled") is not False]
    labels = _unique_labels(enabled)
    segments: list[dict[str, Any]] = []
    news_idx = 0
    for idx, (seg, label) in enumerate(zip(enabled, labels)):
        kind = _safe_text(seg.get("kind") or "news")
        if kind == "news":
            news_idx += 1
        title = _safe_text(seg.get("title") or seg.get("headline") or f"新闻 {news_idx or idx + 1}")
        cards = _plain_cards(seg.get("cards"))
        out: dict[str, Any] = {
            "index": idx,
            "kind": kind,
            "position": news_idx if kind == "news" else 0,
            "total": int(seg.get("total") or 0),
            "story_id": _safe_text(seg.get("story_id")),
            "claim_ids": [_safe_text(val) for val in seg.get("claim_ids") or [] if _safe_text(val)],
            "generation_path": _safe_text(seg.get("generation_path")),
            "editorial_tier": _safe_text(seg.get("editorial_tier") or "headline"),
            "title": title,
            "headline": _safe_text(seg.get("headline") or title),
            "caption": _safe_text(seg.get("caption") or title),
            "text": str(seg.get("text") or ""),
            "active_tab": _safe_text(
                seg.get("active_tab") or ("Intro" if kind == "intro" else "Outro" if kind == "outro" else "AI 动态")
            ),
            "bottom_tabs": labels,
            "bottom_active": label,
            "cards": cards,
            "visual_pages": _final_visual_pages(seg, cards),
        }
        if signal_kind := _safe_text(seg.get("signal_kind")):
            out["signal_kind"] = signal_kind
        if disclaimer := _safe_text(seg.get("disclaimer")):
            out["disclaimer"] = disclaimer
        evidence_rows = [row for row in seg.get("evidence") or [] if isinstance(row, dict)]
        if evidence_rows:
            out["evidence"] = evidence_rows
        segments.append(out)
    return {
        "version": 1,
        "reviewed_at": iso(now_utc()),
        "run_date": state.get("run_date") or "",
        "segments": segments,
    }


def _format_final_md(payload: dict[str, Any]) -> str:
    lines = ["# AI 日报最终审稿", "", f"保存时间：{payload.get('reviewed_at') or ''}", ""]
    for seg in payload.get("segments") or []:
        if not isinstance(seg, dict):
            continue
        kind = seg.get("kind") or "news"
        label = "开场" if kind == "intro" else "收尾" if kind == "outro" else f"{int(seg.get('position') or 0):02d}"
        lines.append(f"## {label} {seg.get('title') or ''}")
        lines.append(f"- 屏幕标题：{seg.get('caption') or ''}")
        lines.append(f"- 解说：{seg.get('text') or ''}")
        cards = [c for c in seg.get("cards") or [] if isinstance(c, dict)]
        if cards:
            lines.append("- 画面卡片：")
            for card in cards:
                lines.append(f"  - {card.get('title') or ''}：{card.get('body') or ''}")
        image_count = sum(1 for p in seg.get("visual_pages") or [] if isinstance(p, dict) and p.get("kind") == "evidence")
        if image_count:
            lines.append(f"- 图片素材：{image_count} 张")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def finalize_agent_edits(run_dir: Path) -> dict[str, Any]:
    """Validate agent source/copy audit, freeze `review/final-script.json`, and bind `agent-contract.json`."""
    root = Path(run_dir).resolve()
    rdir = root / REVIEW_DIR_NAME
    errors = _validate_agent_edits(root)
    if errors:
        return {"ok": False, "run_dir": str(root), "errors": errors}

    manifest = _read_object(root / "manifest.json", "manifest.json")
    state_path = rdir / "state.json"
    draft_path = rdir / "draft-state.json"
    audit_path = rdir / "agent-audit.json"
    final_path = rdir / "final-script.json"
    final_md_path = rdir / "final-script.md"
    contract_path = rdir / "agent-contract.json"

    state = _read_object(state_path, "review/state.json")
    audit = _read_object(audit_path, "review/agent-audit.json")

    _merge_required_evidence_images(root, manifest, state)
    state["run_dir"] = str(root)
    state["updated_at"] = iso(now_utc())
    write_json_atomic(state_path, state)

    final = _state_to_final_script(state)
    final["agent_edit"] = {
        "agent": AGENT_NAME,
        "workflow": "codex_source_copy_visual_v1",
        "manifest_fingerprint": _manifest_semantic_fingerprint(manifest),
        "audit": str(audit_path),
        "finalized_at": iso(now_utc()),
    }
    write_json_atomic(final_path, final)
    write_text_atomic(final_md_path, _format_final_md(final))

    cover_path = rdir / "cover.json"
    if cover_path.exists():
        cover_payload = read_json(cover_path, default=None)
        news_count = sum(1 for seg in final["segments"] if seg.get("kind") == "news")
        if isinstance(cover_payload, dict) and cover_payload.get("storyCount") != news_count:
            cover_payload["storyCount"] = news_count
            write_json_atomic(cover_path, cover_payload)

    for stale_name in ("morning-final-script.json", "morning-final-script.md"):
        (rdir / stale_name).unlink(missing_ok=True)

    mark_render_required(root, reason="agent_edits_finalized", source_path=final_path)

    draft = _read_object(draft_path, "review/draft-state.json")
    contract = {
        "version": 1,
        "status": "validated",
        "agent": AGENT_NAME,
        "created_at": iso(now_utc()),
        "manifest_fingerprint": _manifest_semantic_fingerprint(manifest),
        "state_fingerprint": _fingerprint(state),
        "draft_state_fingerprint": _fingerprint(draft),
        "audit_fingerprint": _fingerprint(audit),
        "final_script_fingerprint": _fingerprint(final),
        "story_ids": [str(row.get("story_id") or "") for row in _news_segments(state)],
    }
    write_json_atomic(contract_path, contract)
    return {
        "ok": True,
        "run_dir": str(root),
        "final_script": str(final_path),
        "contract": str(contract_path),
        "story_count": len(contract["story_ids"]),
    }


def verify_agent_edit_contract(run_dir: Path) -> list[str]:
    """Verify hash-bound `review/agent-contract.json` against manifest, state, audit, and final script."""
    root = Path(run_dir).resolve()
    rdir = root / REVIEW_DIR_NAME
    try:
        manifest = _read_object(root / "manifest.json", "manifest.json")
        state = _read_object(rdir / "state.json", "review/state.json")
        draft = _read_object(rdir / "draft-state.json", "review/draft-state.json")
        audit = _read_object(rdir / "agent-audit.json", "review/agent-audit.json")
        final = _read_object(rdir / "final-script.json", "review/final-script.json")
        contract = _read_object(rdir / "agent-contract.json", "review/agent-contract.json")
    except ValueError as exc:
        return [str(exc)]

    errors = _validate_agent_edits(root)
    if contract.get("status") != "validated" or contract.get("agent") != AGENT_NAME:
        errors.append("agent edit contract is not validated by codex")

    expected = {
        "manifest_fingerprint": _manifest_semantic_fingerprint(manifest),
        "state_fingerprint": _fingerprint(state),
        "draft_state_fingerprint": _fingerprint(draft),
        "audit_fingerprint": _fingerprint(audit),
        "final_script_fingerprint": _fingerprint(final),
    }
    for key, val in expected.items():
        if contract.get(key) != val:
            errors.append(f"agent edit contract {key} does not match current artifacts")
    return errors


def verify_visual_agent_audit(run_dir: Path) -> list[str]:
    """Verify post-render visual QA audit bindings against video, cover, frames, and contact sheets."""
    root = Path(run_dir).resolve()
    vdir = root / REVIEW_DIR_NAME / "visual-qa"
    try:
        visual_input = _read_object(vdir / "visual-qa-input.json", "visual-qa-input.json")
        audit = _read_object(vdir / "visual-agent-audit.json", "visual-agent-audit.json")
    except ValueError as exc:
        return [str(exc)]

    errors: list[str] = []
    if audit.get("agent") != AGENT_NAME or audit.get("verdict") != "approved":
        errors.append("Codex visual audit is not approved")
    checks = audit.get("checks") or {}
    if any(checks.get(key) is not True for key in _REQUIRED_VISUAL_CHECKS):
        errors.append("Codex visual audit checks are incomplete")

    frame_hashes = [str(row.get("sha256") or "") for row in visual_input.get("frames") or [] if isinstance(row, dict)]
    reviewed_hashes = [str(val) for val in audit.get("reviewed_frame_sha256") or []]
    if not frame_hashes or set(reviewed_hashes) != set(frame_hashes) or len(reviewed_hashes) != len(frame_hashes):
        errors.append("Codex visual audit is not bound to every required frame")

    contact_hashes = [str(row.get("sha256") or "") for row in visual_input.get("contact_sheets") or [] if isinstance(row, dict)]
    if [str(val) for val in audit.get("contact_sheet_sha256") or []] != contact_hashes:
        errors.append("Codex visual audit is not bound to every contact sheet")

    files = {
        "video_sha256": Path(str(visual_input.get("video") or "")),
        "cover_sha256": Path(str(visual_input.get("cover") or "")),
        "script_sha256": Path(str(visual_input.get("script") or "")),
        "subtitles_sha256": Path(str(visual_input.get("subtitles") or "")),
        "audio_quality_sha256": Path(str(visual_input.get("audio_quality") or "")),
    }
    for key, path in files.items():
        if not path.is_file():
            errors.append(f"Codex visual audit input is missing: {path}")
            continue
        current = file_sha256(path)
        if visual_input.get(key) != current or audit.get(key) != current:
            errors.append(f"Codex visual audit {key} is not bound to current media")

    for frame in visual_input.get("frames") or []:
        if not isinstance(frame, dict):
            continue
        path = Path(str(frame.get("path") or ""))
        if not path.is_file() or file_sha256(path) != frame.get("sha256"):
            errors.append(f"Codex visual QA frame is missing or changed: {path}")

    for contact in visual_input.get("contact_sheets") or []:
        if not isinstance(contact, dict):
            continue
        path = Path(str(contact.get("path") or ""))
        if not path.is_file() or file_sha256(path) != contact.get("sha256"):
            errors.append(f"Codex visual QA contact sheet is missing or changed: {path}")

    return errors

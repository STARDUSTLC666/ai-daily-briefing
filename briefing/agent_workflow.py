from __future__ import annotations

"""Contracts for a Codex-reviewed edition and post-render visual inspection.

Codex is the high-judgement step.  Local code still owns the immutable story
set, approved claims, evidence URLs, rendering and publish gates.
"""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .review import (
    _write_final_md,
    mark_render_required,
    review_draft_state_path,
    review_final_md_path,
    review_final_script_path,
    review_state_path,
    write_final_script_from_state,
)
from .util import clean_text


UTC = timezone.utc
AGENT_NAME = "codex"
_CRITICAL_TOKEN_RE = re.compile(
    r"(?i)(?:\d+(?:\.\d+)?(?:%|ms|s|x|b|m|k|天|小时|分钟|次|个|行|倍)?|"
    r"(?:gpt|claude|gemini|gemma|qwen|deepseek|kimi|llama|mistral|grok|minimax|glm|hunyuan|nova)"
    r"[-‐‑–—_\s]?[a-z]*\d[a-z0-9_.‐‑–—-]*)"
)
_REQUIRED_COPY_CHECKS = {
    "source_opened",
    "facts_bound",
    "uncertainty_preserved",
    "chinese_copy",
    "sentence_complete",
    "no_generic_filler",
    "impact_language_specific",
    "no_clickbait_overreach",
}
_REQUIRED_VISUAL_CHECKS = {
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
_OVERCLAIM_TERMS = {
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


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ValueError(f"missing {label}: {path}") from exc
    except Exception as exc:
        raise ValueError(f"{label} parse failed: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} root must be an object")
    return value


def _write_object(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8-sig")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_semantic_payload(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": manifest.get("run_id"),
        "run_date": manifest.get("run_date"),
        "package_quality": manifest.get("package_quality"),
        "source_coverage": manifest.get("source_coverage"),
        "selected": manifest.get("selected"),
    }


def manifest_semantic_fingerprint(manifest: dict[str, Any]) -> str:
    return _fingerprint(_manifest_semantic_payload(manifest))


def agent_dir(run_dir: Path) -> Path:
    return Path(run_dir) / "review"


def agent_brief_path(run_dir: Path) -> Path:
    return agent_dir(run_dir) / "agent-brief.json"


def agent_audit_path(run_dir: Path) -> Path:
    return agent_dir(run_dir) / "agent-audit.json"


def agent_audit_template_path(run_dir: Path) -> Path:
    return agent_dir(run_dir) / "agent-audit.template.json"


def agent_contract_path(run_dir: Path) -> Path:
    return agent_dir(run_dir) / "agent-contract.json"


def visual_qa_dir(run_dir: Path) -> Path:
    return agent_dir(run_dir) / "visual-qa"


def visual_qa_input_path(run_dir: Path) -> Path:
    return visual_qa_dir(run_dir) / "visual-qa-input.json"


def visual_audit_path(run_dir: Path) -> Path:
    return visual_qa_dir(run_dir) / "visual-agent-audit.json"


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
    parts = [segment.get(key) for key in ("title", "headline", "caption", "text")]
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
    return " ".join(str(value or "") for value in parts)


def _copy_norm(value: Any) -> str:
    return re.sub(r"[\s。！？!?；;，,：:'\"“”‘’（）()【】\[\]]+", "", clean_text(str(value or "")).lower())


def _sentence_blocks(value: Any) -> list[str]:
    text = clean_text(str(value or ""))
    return [part for part in (clean_text(row) for row in re.findall(r".+?(?:[。！？!?]|$)", text)) if part]


def _segment_copy_blocks(segment: dict[str, Any]) -> set[str]:
    values: list[Any] = [segment.get("title"), segment.get("headline"), segment.get("caption")]
    values.extend(_sentence_blocks(segment.get("text")))
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
    return {_copy_norm(value) for value in values if _copy_norm(value)}


def _compact_agent_evidence(rows: Any) -> list[dict[str, Any]]:
    """Keep the agent work order small without removing review evidence."""
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
        row = {
            key: raw.get(key)
            for key in fields
            if raw.get(key) is not None and raw.get(key) != "" and raw.get(key) != [] and raw.get(key) != {}
        }
        excerpt = clean_text(str(raw.get("excerpt") or ""), 1200)
        if excerpt:
            row["excerpt"] = excerpt
        compact.append(row)
    return compact


def create_agent_package(run_dir: Path) -> dict[str, Any]:
    """Freeze the local draft into a bounded work order for a Codex automation."""
    run_dir = Path(run_dir).resolve()
    manifest_path = run_dir / "manifest.json"
    manifest = _read_object(manifest_path, "manifest.json")
    state = _read_object(review_state_path(run_dir), "review/state.json")
    draft = _read_object(review_draft_state_path(run_dir), "review/draft-state.json")
    if _fingerprint(state) != _fingerprint(draft):
        raise ValueError("agent package requires a fresh unedited review state; rerun prepare-agent --force")
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
    manifest_fp = manifest_semantic_fingerprint(manifest)
    draft_fp = _fingerprint(state)
    brief = {
        "version": 1,
        "agent": AGENT_NAME,
        "created_at": datetime.now(UTC).isoformat(),
        "run_dir": str(run_dir),
        "run_id": manifest.get("run_id"),
        "manifest_fingerprint": manifest_fp,
        "draft_state_fingerprint": draft_fp,
        "editable_state": str(review_state_path(run_dir)),
        "audit_output": str(agent_audit_path(run_dir)),
        "editable_fields": ["intro/outro text", "news sentence order", "approved copy-block selection"],
        "immutable_fields": ["segment order", "kind", "story_id", "claim_ids", "generation_path", "editorial_tier", "position", "total"],
        "rules": [
            "先检查 X 官方公司、官方产品和关键负责人原帖，再核对官网；RSS、Google News 和镜像只作后台线索，不能出现在视频文案。",
            "不得新增、删除或替换 story_id；不得把一线消息或传闻写成正式公告。",
            "新闻段只能重排或删减冻结稿中的 approved copy blocks，不能自由改写事实句。",
            "标题、口播和卡片不得补造数字、版本、价格、动作或可用范围。",
            "每条新闻按事实、关键变化、具体用途或影响、必要边界组织；这些是内部信息语法，不得作为制作流程标签出现在成片。",
            "用途或影响只能来自 approved claims；没有证据支持时宁可删去，不得用值得关注、未来可期或推动行业发展等空话补位。",
            "测试、预览、灰度、计划和爆料不得写成正式发布；开放权重不等于完全开源，可申请体验不等于所有人可用，厂商自测必须保留口径。",
            "标题和卡片不得使用震撼、炸裂、王炸、杀疯了、颠覆、彻底取代或程序员失业等无法由 approved claims 直接证明的措辞。",
            "标题、口播、卡片和字幕不得出现省略号；每个事实句必须完整。",
            "只编辑运行产物，不修改仓库源码，不上传 B 站。",
        ],
        "stories": rows,
    }
    _write_object(agent_brief_path(run_dir), brief)
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
    _write_object(agent_audit_template_path(run_dir), template)
    manifest["agent_policy"] = {
        "required": True,
        "agent": AGENT_NAME,
        "workflow": "codex_source_copy_visual_v1",
        "brief": str(agent_brief_path(run_dir)),
        "draft_state_fingerprint": draft_fp,
    }
    _write_object(manifest_path, manifest)
    return {
        "ok": True,
        "run_dir": str(run_dir),
        "agent_brief": str(agent_brief_path(run_dir)),
        "editable_state": str(review_state_path(run_dir)),
        "audit_template": str(agent_audit_template_path(run_dir)),
        "audit_output": str(agent_audit_path(run_dir)),
        "story_count": len(rows),
    }


def validate_agent_edit(run_dir: Path) -> list[str]:
    run_dir = Path(run_dir).resolve()
    errors: list[str] = []
    try:
        manifest = _read_object(run_dir / "manifest.json", "manifest.json")
        state = _read_object(review_state_path(run_dir), "review/state.json")
        draft = _read_object(review_draft_state_path(run_dir), "review/draft-state.json")
        audit = _read_object(agent_audit_path(run_dir), "review/agent-audit.json")
    except ValueError as exc:
        return [str(exc)]
    stories = _selected_story_map(manifest)
    news = _news_segments(state)
    draft_news = _news_segments(draft)
    state_segments = [row for row in state.get("segments") or [] if isinstance(row, dict) and row.get("enabled") is not False]
    draft_segments = [row for row in draft.get("segments") or [] if isinstance(row, dict) and row.get("enabled") is not False]
    if len(state_segments) != len(draft_segments) or [str(row.get("kind") or "news") for row in state_segments] != [
        str(row.get("kind") or "news") for row in draft_segments
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
    if audit.get("manifest_fingerprint") != manifest_semantic_fingerprint(manifest):
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
        selected_ids = [str(value) for value in segment.get("claim_ids") or [] if str(value)]
        if not selected_ids or len(selected_ids) != len(set(selected_ids)):
            errors.append(f"agent story {position} has no unique selected claim_ids")
        if any(claim_id not in claims for claim_id in selected_ids):
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
                # Source/time cards and other immutable presentation blocks
                # can contain dates or counters that are not claim prose. They
                # are already protected by the frozen-copy subset check above.
                _public_segment_text(draft_segment),
            ]
            + [str(claims[claim_id].get("text") or "") for claim_id in selected_ids if claim_id in claims]
        )
        extra_tokens = _critical_tokens(public_text) - _critical_tokens(allowed_text)
        if extra_tokens:
            errors.append(f"agent story {position} introduces unapproved critical tokens: {sorted(extra_tokens)}")

        audit_row = audit_by_id.get(story_id) or {}
        if audit_row.get("verdict") != "approved":
            errors.append(f"agent story {position} verdict is not approved")
        if [str(value) for value in audit_row.get("selected_claim_ids") or []] != selected_ids:
            errors.append(f"agent story {position} audit claim_ids do not match edited state")
        checks = audit_row.get("copy_checks") or {}
        if any(checks.get(key) is not True for key in _REQUIRED_COPY_CHECKS):
            errors.append(f"agent story {position} copy checks are incomplete")
        checked = {_normalize_url(value) for value in audit_row.get("checked_urls") or []}
        checked.discard("")
        for claim_id in selected_ids:
            if claim_id not in claims:
                continue
            supporting_urls = {_normalize_url(url) for url in claims[claim_id].get("evidence_urls") or []}
            supporting_urls.discard("")
            if not supporting_urls or not checked.intersection(supporting_urls):
                errors.append(f"agent story {position} did not open supporting evidence for claim_id {claim_id}")
            if claims[claim_id].get("first_party_supported") is True:
                first_party_urls = {
                    _normalize_url(row.get("url"))
                    for row in card.get("evidence") or []
                    if isinstance(row, dict) and str(row.get("reliability") or "").startswith("official")
                }
                first_party_urls.discard("")
                if first_party_urls and not checked.intersection(first_party_urls):
                    errors.append(f"agent story {position} did not open available first-party evidence")
    return errors


def finalize_agent_edit(run_dir: Path) -> dict[str, Any]:
    run_dir = Path(run_dir).resolve()
    errors = validate_agent_edit(run_dir)
    if errors:
        return {"ok": False, "run_dir": str(run_dir), "errors": errors}
    manifest = _read_object(run_dir / "manifest.json", "manifest.json")
    state = _read_object(review_state_path(run_dir), "review/state.json")
    audit = _read_object(agent_audit_path(run_dir), "review/agent-audit.json")
    write_final_script_from_state(run_dir, state)
    final_path = review_final_script_path(run_dir)
    final = _read_object(final_path, "review/final-script.json")
    final["agent_edit"] = {
        "agent": AGENT_NAME,
        "workflow": "codex_source_copy_visual_v1",
        "manifest_fingerprint": manifest_semantic_fingerprint(manifest),
        "audit": str(agent_audit_path(run_dir)),
        "finalized_at": datetime.now(UTC).isoformat(),
    }
    _write_object(final_path, final)
    _write_final_md(review_final_md_path(run_dir), final)
    mark_render_required(run_dir, final_path)
    contract = {
        "version": 1,
        "status": "validated",
        "agent": AGENT_NAME,
        "created_at": datetime.now(UTC).isoformat(),
        "manifest_fingerprint": manifest_semantic_fingerprint(manifest),
        "state_fingerprint": _fingerprint(state),
        "draft_state_fingerprint": _fingerprint(_read_object(review_draft_state_path(run_dir), "review/draft-state.json")),
        "audit_fingerprint": _fingerprint(audit),
        "final_script_fingerprint": _fingerprint(final),
        "story_ids": [str(row.get("story_id") or "") for row in _news_segments(state)],
    }
    _write_object(agent_contract_path(run_dir), contract)
    return {
        "ok": True,
        "run_dir": str(run_dir),
        "final_script": str(final_path),
        "contract": str(agent_contract_path(run_dir)),
        "story_count": len(contract["story_ids"]),
    }


def verify_agent_edit_contract(run_dir: Path) -> list[str]:
    run_dir = Path(run_dir).resolve()
    try:
        manifest = _read_object(run_dir / "manifest.json", "manifest.json")
        state = _read_object(review_state_path(run_dir), "review/state.json")
        audit = _read_object(agent_audit_path(run_dir), "review/agent-audit.json")
        final = _read_object(review_final_script_path(run_dir), "review/final-script.json")
        contract = _read_object(agent_contract_path(run_dir), "review/agent-contract.json")
    except ValueError as exc:
        return [str(exc)]
    errors = validate_agent_edit(run_dir)
    if contract.get("status") != "validated" or contract.get("agent") != AGENT_NAME:
        errors.append("agent edit contract is not validated by codex")
    expected = {
        "manifest_fingerprint": manifest_semantic_fingerprint(manifest),
        "state_fingerprint": _fingerprint(state),
        "draft_state_fingerprint": _fingerprint(_read_object(review_draft_state_path(run_dir), "review/draft-state.json")),
        "audit_fingerprint": _fingerprint(audit),
        "final_script_fingerprint": _fingerprint(final),
    }
    for key, value in expected.items():
        if contract.get(key) != value:
            errors.append(f"agent edit contract {key} does not match current artifacts")
    return errors


def _visual_sample_rows(
    script: list[dict[str, Any]],
    duration: float,
    remotion_slides: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    sample_rows: list[dict[str, Any]] = []
    actual_slides = [slide for slide in remotion_slides or [] if isinstance(slide, dict)]
    if actual_slides:
        for slide in actual_slides:
            segment_index = int(slide.get("segmentIndex") or 0)
            segment = script[segment_index] if 0 <= segment_index < len(script) else {}
            start = max(0.0, float(slide.get("start") or 0.0))
            end = min(duration, float(slide.get("end") or start))
            page = slide.get("page") if isinstance(slide.get("page"), dict) else {}
            sample_rows.append(
                {
                    "segment_index": segment_index,
                    "kind": str(slide.get("kind") or segment.get("kind") or "news"),
                    "story_id": str(segment.get("story_id") or ""),
                    "page_index": int(slide.get("pageIndex") or 0),
                    "page_kind": str(page.get("kind") or "default"),
                    "sample_kind": "page_midpoint",
                    "timestamp": min(max(0.05, start + max(0.1, end - start) / 2), max(0.05, duration - 0.05)),
                }
            )
    else:
        for segment_index, segment in enumerate(script):
            if not isinstance(segment, dict):
                continue
            start = max(0.0, float(segment.get("start") or 0.0))
            end = min(duration, float(segment.get("end") or start))
            span = max(0.1, end - start)
            pages = [page for page in segment.get("visual_pages") or [] if isinstance(page, dict)] or [{}]
            for page_index, page in enumerate(pages):
                timestamp = start + span * (page_index + 0.5) / len(pages)
                sample_rows.append(
                    {
                        "segment_index": segment_index,
                        "kind": str(segment.get("kind") or "news"),
                        "story_id": str(segment.get("story_id") or ""),
                        "page_index": page_index,
                        "page_kind": str(page.get("kind") or "default"),
                        "sample_kind": "page_midpoint",
                        "timestamp": min(max(0.05, timestamp), max(0.05, duration - 0.05)),
                    }
                )
    for segment_index, segment in enumerate(script):
        if not isinstance(segment, dict) or str(segment.get("kind") or "") != "news":
            continue
        start = max(0.0, float(segment.get("start") or 0.0))
        end = min(duration, float(segment.get("end") or start))
        if end - start < 0.8:
            continue
        for label, timestamp in (("transition_in", start + 0.18), ("transition_out", end - 0.18)):
            sample_rows.append(
                {
                    "segment_index": segment_index,
                    "kind": "news",
                    "story_id": str(segment.get("story_id") or ""),
                    "page_index": -1,
                    "page_kind": "transition",
                    "sample_kind": label,
                    "timestamp": min(max(0.05, timestamp), max(0.05, duration - 0.05)),
                }
            )
    return sample_rows


def create_visual_qa_package(run_dir: Path, frame_count: int = 12) -> dict[str, Any]:
    """Extract every segment/page plus transition edges at source resolution."""
    del frame_count  # Kept for CLI/API compatibility; coverage is script-driven.
    run_dir = Path(run_dir).resolve()
    video = run_dir / "final.mp4"
    cover = run_dir / "cover.png"
    script_path = run_dir / "script.json"
    subtitles = run_dir / "subtitles.srt"
    audio_quality = run_dir / "audio-quality.json"
    required = [video, cover, script_path, subtitles, audio_quality]
    missing = [str(path) for path in required if not path.exists() or not path.is_file() or path.stat().st_size == 0]
    if missing:
        raise ValueError(f"visual QA inputs are missing: {missing}")
    from .render import _ffmpeg

    ffmpeg = _ffmpeg()
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("ffmpeg and ffprobe are required for visual QA")
    probe = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nk=1:nw=1", str(video)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    duration = float(probe.stdout.strip())
    script = json.loads(script_path.read_text(encoding="utf-8-sig"))
    if not isinstance(script, list) or not script:
        raise ValueError("script.json has no rendered segments")
    remotion_slides: list[dict[str, Any]] = []
    remotion_input = run_dir / "render" / "remotion-input.json"
    if remotion_input.is_file():
        try:
            remotion_payload = json.loads(remotion_input.read_text(encoding="utf-8-sig"))
            candidate_slides = remotion_payload.get("slides") if isinstance(remotion_payload, dict) else remotion_payload
            if isinstance(candidate_slides, list):
                remotion_slides = [row for row in candidate_slides if isinstance(row, dict)]
        except (OSError, json.JSONDecodeError):
            remotion_slides = []
    sample_rows = _visual_sample_rows(script, duration, remotion_slides)
    # Remove accidental duplicate timestamps while keeping semantic coverage.
    unique_rows: list[dict[str, Any]] = []
    seen_times: set[int] = set()
    for row in sample_rows:
        key = round(float(row["timestamp"]) * 1000)
        if key in seen_times:
            continue
        seen_times.add(key)
        unique_rows.append(row)
    folder = visual_qa_dir(run_dir)
    folder.mkdir(parents=True, exist_ok=True)
    for pattern in ("frame-*.png", "contact-sheet-*.png"):
        for old in folder.glob(pattern):
            old.unlink()
    frames: list[dict[str, Any]] = []
    for index, row in enumerate(unique_rows):
        frame = folder / f"frame-{index:03d}.png"
        subprocess.run(
            [ffmpeg, "-y", "-ss", f"{float(row['timestamp']):.3f}", "-i", str(video), "-frames:v", "1", str(frame)],
            check=True,
            capture_output=True,
        )
        frames.append({**row, "path": str(frame), "sha256": _file_sha256(frame)})

    from PIL import Image, ImageDraw, ImageOps

    contacts: list[dict[str, str]] = []
    for sheet_index, offset in enumerate(range(0, len(frames), 12), 1):
        group = frames[offset : offset + 12]
        sheet = Image.new("RGB", (4 * 496 + 8, 3 * 302 + 8), "#EAE7DF")
        draw = ImageDraw.Draw(sheet)
        for cell, frame_row in enumerate(group):
            with Image.open(frame_row["path"]) as source:
                thumb = ImageOps.contain(source.convert("RGB"), (480, 270))
            x = 8 + (cell % 4) * 496
            y = 8 + (cell // 4) * 302
            sheet.paste(thumb, (x, y))
            draw.text((x, y + 274), f"{offset + cell + 1:02d} {frame_row['kind']} {frame_row['sample_kind']}", fill="#28312D")
        contact = folder / f"contact-sheet-{sheet_index:02d}.png"
        sheet.save(contact)
        contacts.append({"path": str(contact), "sha256": _file_sha256(contact)})
    payload = {
        "version": 2,
        "video": str(video),
        "video_sha256": _file_sha256(video),
        "cover": str(cover),
        "cover_sha256": _file_sha256(cover),
        "script": str(script_path),
        "script_sha256": _file_sha256(script_path),
        "subtitles": str(subtitles),
        "subtitles_sha256": _file_sha256(subtitles),
        "audio_quality": str(audio_quality),
        "audio_quality_sha256": _file_sha256(audio_quality),
        "contact_sheet": contacts[0]["path"] if contacts else "",
        "contact_sheets": contacts,
        "duration_seconds": duration,
        "frame_count": len(frames),
        "frames": frames,
    }
    _write_object(visual_qa_input_path(run_dir), payload)
    template = {
        "version": 2,
        "agent": AGENT_NAME,
        "verdict": "pending",
        "video_sha256": payload["video_sha256"],
        "cover_sha256": payload["cover_sha256"],
        "script_sha256": payload["script_sha256"],
        "subtitles_sha256": payload["subtitles_sha256"],
        "audio_quality_sha256": payload["audio_quality_sha256"],
        "contact_sheet_sha256": [row["sha256"] for row in contacts],
        "reviewed_frame_sha256": [],
        "checks": {key: False for key in sorted(_REQUIRED_VISUAL_CHECKS)},
        "notes": "",
    }
    _write_object(folder / "visual-agent-audit.template.json", template)
    return {"ok": True, **payload, "audit_output": str(visual_audit_path(run_dir))}


def verify_visual_agent_audit(run_dir: Path) -> list[str]:
    run_dir = Path(run_dir).resolve()
    try:
        visual_input = _read_object(visual_qa_input_path(run_dir), "visual-qa-input.json")
        audit = _read_object(visual_audit_path(run_dir), "visual-agent-audit.json")
    except ValueError as exc:
        return [str(exc)]
    errors: list[str] = []
    if audit.get("agent") != AGENT_NAME or audit.get("verdict") != "approved":
        errors.append("Codex visual audit is not approved")
    checks = audit.get("checks") or {}
    if any(checks.get(key) is not True for key in _REQUIRED_VISUAL_CHECKS):
        errors.append("Codex visual audit checks are incomplete")
    frame_hashes = [str(row.get("sha256") or "") for row in visual_input.get("frames") or [] if isinstance(row, dict)]
    reviewed_hashes = [str(value) for value in audit.get("reviewed_frame_sha256") or []]
    if not frame_hashes or set(reviewed_hashes) != set(frame_hashes) or len(reviewed_hashes) != len(frame_hashes):
        errors.append("Codex visual audit is not bound to every required frame")
    contact_hashes = [str(row.get("sha256") or "") for row in visual_input.get("contact_sheets") or [] if isinstance(row, dict)]
    if [str(value) for value in audit.get("contact_sheet_sha256") or []] != contact_hashes:
        errors.append("Codex visual audit is not bound to every contact sheet")
    files = {
        "video_sha256": Path(str(visual_input.get("video") or "")),
        "cover_sha256": Path(str(visual_input.get("cover") or "")),
        "script_sha256": Path(str(visual_input.get("script") or "")),
        "subtitles_sha256": Path(str(visual_input.get("subtitles") or "")),
        "audio_quality_sha256": Path(str(visual_input.get("audio_quality") or "")),
    }
    for key, path in files.items():
        if not path.exists() or not path.is_file():
            errors.append(f"Codex visual audit input is missing: {path}")
            continue
        current = _file_sha256(path)
        if visual_input.get(key) != current or audit.get(key) != current:
            errors.append(f"Codex visual audit {key} is not bound to current media")
    for frame in visual_input.get("frames") or []:
        if not isinstance(frame, dict):
            continue
        path = Path(str(frame.get("path") or ""))
        if not path.exists() or _file_sha256(path) != frame.get("sha256"):
            errors.append(f"Codex visual QA frame is missing or changed: {path}")
    for contact in visual_input.get("contact_sheets") or []:
        if not isinstance(contact, dict):
            continue
        path = Path(str(contact.get("path") or ""))
        if not path.exists() or _file_sha256(path) != contact.get("sha256"):
            errors.append(f"Codex visual QA contact sheet is missing or changed: {path}")
    return errors

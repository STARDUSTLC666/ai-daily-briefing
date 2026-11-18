from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
from typing import Any
from urllib.parse import quote, urlparse
import uuid

from briefing.core import (
    EvidenceCard,
    clean_text,
    ensure_dir,
    format_mmss,
    is_x_url,
    iso,
    now_utc,
    read_json,
    url_host,
    write_json_atomic,
    write_text_atomic,
)
from briefing.render.manuscript import build_segments
from briefing.verify import mark_render_required

COVER_SCHEMA_VERSION = 1
COVER_TEXT_FIELDS = ("eyebrow", "headline", "subheadline", "badge", "date")
COVER_LIST_FIELDS = ("highlights", "entities")


def review_dir(run_dir: Path) -> Path:
    return Path(run_dir).resolve() / "review"


def review_state_path(run_dir: Path) -> Path:
    return review_dir(run_dir) / "state.json"


def review_draft_state_path(run_dir: Path) -> Path:
    return review_dir(run_dir) / "draft-state.json"


def review_final_script_path(run_dir: Path) -> Path:
    return review_dir(run_dir) / "final-script.json"


def review_final_md_path(run_dir: Path) -> Path:
    return review_dir(run_dir) / "final-script.md"


def review_morning_script_path(run_dir: Path) -> Path:
    return review_dir(run_dir) / "morning-final-script.json"


def review_morning_md_path(run_dir: Path) -> Path:
    return review_dir(run_dir) / "morning-final-script.md"


def review_cover_path(run_dir: Path) -> Path:
    return review_dir(run_dir) / "cover.json"


def _mark_render_required(run_dir: Path, manuscript_path: Path | None = None, *, reason: str = "manuscript_updated") -> Path:
    return mark_render_required(run_dir, reason=reason, source_path=manuscript_path)


def _media_url(path: str | Path) -> str:
    return "/media?path=" + quote(str(path), safe="")


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _plain_cards(cards: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if not isinstance(cards, list):
        return out
    for item in cards:
        if not isinstance(item, dict):
            continue
        title = clean_text(str(item.get("title") or ""))
        body = clean_text(str(item.get("body") or item.get("content") or ""))
        icon = clean_text(str(item.get("icon") or ""))
        if title or body:
            row: dict[str, str] = {"title": title, "body": body}
            if icon:
                row["icon"] = icon[:4]
            out.append(row)
    return out


def _public_evidence_source(evidence: dict[str, Any], fallback: str = "") -> str:
    source_url = clean_text(str(evidence.get("url") or evidence.get("screenshot_capture_url") or ""))
    if is_x_url(source_url):
        parsed = urlparse(source_url)
        handle = next((part for part in parsed.path.split("/") if part), "")
        if handle:
            return f"X / @{handle}"
    source = clean_text(str(evidence.get("source") or ""))
    if source.lower().startswith("codex verified"):
        source = ""
    return source or fallback or url_host(source_url) or "原始来源"


def _extract_images_from_pages(
    pages: list[dict[str, Any]],
    run_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    clean_pages: list[dict[str, Any]] = []
    images: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for idx, page in enumerate(pages):
        if not isinstance(page, dict):
            continue
        ev = page.get("evidenceVisual")
        if isinstance(ev, dict):
            raw_path = ev.get("image") or ev.get("path") or ev.get("screenshot_path")
            src_url = clean_text(str(page.get("source_url") or ev.get("url") or ""))
            key = (str(raw_path or ""), "" if raw_path else src_url)
            if raw_path and key not in seen:
                p = Path(str(raw_path))
                if not p.is_absolute():
                    p = (run_dir / p).resolve()
                seen.add(key)
                required = bool(ev.get("required"))
                images.append(
                    {
                        "id": f"img-{idx}-{uuid.uuid4().hex[:8]}",
                        "label": clean_text(str(page.get("title") or ev.get("title") or "证据截图")),
                        "path": str(p),
                        "url": _media_url(p),
                        "source": clean_text(str(page.get("source") or ev.get("source") or "")),
                        "source_url": src_url,
                        "enabled": True,
                        "required": required,
                    }
                )
            if str(page.get("kind") or "").lower() == "evidence":
                continue
            page_copy = deepcopy(page)
            page_copy.pop("evidenceVisual", None)
            clean_pages.append(page_copy)
            continue
        clean_pages.append(deepcopy(page))
    return clean_pages, images


def _merge_required_evidence_images(run_dir: Path, state: dict[str, Any]) -> None:
    manifest_path = Path(run_dir).resolve() / "manifest.json"
    if not manifest_path.is_file():
        return
    manifest = read_json(manifest_path, default={})
    if not isinstance(manifest, dict):
        return

    required_by_story: dict[str, list[dict[str, Any]]] = {}
    for card in list(manifest.get("selected") or []):
        if not isinstance(card, dict):
            continue
        spec = card.get("story_spec") if isinstance(card.get("story_spec"), dict) else {}
        story_id = clean_text(str(spec.get("story_id") or ""))
        if not story_id:
            continue
        rows: list[dict[str, Any]] = []
        for ev_idx, ev in enumerate(list(card.get("evidence") or [])):
            if not isinstance(ev, dict):
                continue
            if not _truthy(ev.get("screenshot_required")):
                continue
            if clean_text(str(ev.get("screenshot_status") or "")).lower() != "captured":
                continue
            raw_path = clean_text(str(ev.get("screenshot_path") or ""))
            if not raw_path:
                continue
            p = Path(raw_path)
            if not p.is_absolute():
                p = Path(run_dir).resolve() / p
            p = p.resolve()
            if not p.is_file():
                continue
            src_url = clean_text(str(ev.get("url") or ev.get("screenshot_capture_url") or ""))
            rows.append(
                {
                    "id": f"required-{story_id[:10]}-{ev_idx:02d}",
                    "label": clean_text(str(ev.get("title") or card.get("title") or "证据截图")),
                    "path": str(p),
                    "url": _media_url(p),
                    "source": _public_evidence_source(ev),
                    "source_url": src_url,
                    "enabled": True,
                    "required": True,
                }
            )
        if rows:
            required_by_story[story_id] = rows

    for seg in list(state.get("segments") or []):
        if not isinstance(seg, dict):
            continue
        story_id = clean_text(str(seg.get("story_id") or ""))
        req_list = required_by_story.get(story_id) or []
        if not req_list:
            continue
        images = [img for img in list(seg.get("images") or []) if isinstance(img, dict)]
        fallback_src = next((clean_text(str(i.get("source") or "")) for i in images if clean_text(str(i.get("source") or ""))), "")
        by_norm_path: dict[str, dict[str, Any]] = {}
        for img in images:
            rp = clean_text(str(img.get("path") or ""))
            if rp:
                by_norm_path[str(Path(rp).resolve()).lower()] = img
        for req in req_list:
            norm = str(Path(str(req["path"])).resolve()).lower()
            if norm in by_norm_path:
                by_norm_path[norm]["enabled"] = True
                by_norm_path[norm]["required"] = True
                continue
            item = deepcopy(req)
            if not clean_text(str(item.get("source") or "")) and fallback_src:
                item["source"] = fallback_src
            images.append(item)
            by_norm_path[norm] = item
        seg["images"] = images


def _segment_to_state_row(seg: dict[str, Any], idx: int, run_dir: Path) -> dict[str, Any]:
    pages = [p for p in list(seg.get("visual_pages") or []) if isinstance(p, dict)]
    clean_pages, images = _extract_images_from_pages(pages, run_dir)
    return {
        "id": str(seg.get("id") or f"seg-{idx:03d}-{uuid.uuid4().hex[:8]}"),
        "index": int(seg.get("index") if seg.get("index") is not None else idx),
        "kind": clean_text(str(seg.get("kind") or "news")),
        "position": int(seg.get("position") or 0),
        "total": int(seg.get("total") or 0),
        "story_id": clean_text(str(seg.get("story_id") or "")),
        "claim_ids": [clean_text(str(v)) for v in list(seg.get("claim_ids") or []) if clean_text(str(v))],
        "generation_path": clean_text(str(seg.get("generation_path") or "structured_editorial_plan")),
        "editorial_tier": clean_text(str(seg.get("editorial_tier") or "headline")),
        "enabled": bool(seg.get("enabled", True)),
        "title": clean_text(str(seg.get("title") or seg.get("headline") or f"新闻 {idx + 1}")),
        "headline": clean_text(str(seg.get("headline") or seg.get("title") or "")),
        "caption": clean_text(str(seg.get("caption") or seg.get("title") or "")),
        "text": str(seg.get("text") or ""),
        "active_tab": clean_text(str(seg.get("active_tab") or "")),
        "bottom_active": clean_text(str(seg.get("bottom_active") or "")),
        "cards": _plain_cards(seg.get("cards")),
        "images": images,
        "visual_pages": clean_pages,
        "notes": clean_text(str(seg.get("notes") or "")),
        "signal_kind": clean_text(str(seg.get("signal_kind") or "")),
        "disclaimer": clean_text(str(seg.get("disclaimer") or "")),
        "evidence": [r for r in list(seg.get("evidence") or []) if isinstance(r, dict)],
    }


def _load_existing_segments(run_dir: Path) -> list[dict[str, Any]]:
    for cand in (
        review_final_script_path(run_dir),
        run_dir / "news-script.json",
        run_dir / "script.json",
    ):
        if not cand.is_file():
            continue
        payload = read_json(cand, default=None)
        raw = payload.get("segments") if isinstance(payload, dict) else payload
        if isinstance(raw, list):
            return [s for s in raw if isinstance(s, dict)]
    return []


def create_review_package(
    run_dir: Path,
    cards: list[EvidenceCard] | None = None,
    *,
    run_date: str = "",
    force: bool = False,
) -> dict[str, Any]:
    """Initialize review/state.json and review/draft-state.json for local or agent editorial review."""
    root = Path(run_dir).resolve()
    rdir = ensure_dir(review_dir(root))
    state_file = review_state_path(root)
    draft_file = review_draft_state_path(root)

    if state_file.is_file() and not force:
        existing_state = read_json(state_file, default={})
        if isinstance(existing_state, dict):
            _merge_required_evidence_images(root, existing_state)
            write_json_atomic(state_file, existing_state)
        return {
            "status": "exists",
            "review_dir": str(rdir),
            "state": str(state_file),
            "draft_state": str(draft_file),
            "final_script": str(review_final_script_path(root)),
        }

    effective_date = run_date or root.name
    manifest_path = root / "manifest.json"
    if not run_date and manifest_path.is_file():
        manifest = read_json(manifest_path, default={})
        if isinstance(manifest, dict) and clean_text(str(manifest.get("run_date") or "")):
            effective_date = clean_text(str(manifest["run_date"]))

    if cards is not None:
        segments = build_segments(cards, run_date=effective_date)
    else:
        segments = _load_existing_segments(root)
        if not segments:
            segments = build_segments([], run_date=effective_date)

    timestamp = iso(now_utc())
    state: dict[str, Any] = {
        "version": 1,
        "run_dir": str(root),
        "run_date": effective_date,
        "created_at": timestamp,
        "updated_at": timestamp,
        "segments": [_segment_to_state_row(seg, idx, root) for idx, seg in enumerate(segments)],
    }
    _merge_required_evidence_images(root, state)
    write_json_atomic(draft_file, state)
    write_json_atomic(state_file, state)
    return {
        "status": "created",
        "review_dir": str(rdir),
        "state": str(state_file),
        "draft_state": str(draft_file),
        "final_script": str(review_final_script_path(root)),
    }


def _unique_bottom_labels(segments: list[dict[str, Any]]) -> list[str]:
    labels: list[str] = []
    seen: dict[str, int] = {}
    news_idx = 0
    for seg in segments:
        kind = str(seg.get("kind") or "news")
        if kind == "intro":
            raw = "开场"
        elif kind == "outro":
            raw = "收尾"
        elif kind == "ticker":
            raw = "速览"
        else:
            news_idx += 1
            raw = clean_text(str(seg.get("bottom_active") or "")) or f"{news_idx:02d} {clean_text(str(seg.get('title') or ''))[:10]}"
        raw = raw or f"{len(labels) + 1:02d}"
        count = seen.get(raw, 0) + 1
        seen[raw] = count
        labels.append(raw if count == 1 else f"{raw}-{count}")
    return labels


def _rebuild_visual_pages(seg: dict[str, Any], cards: list[dict[str, str]]) -> list[dict[str, Any]]:
    title = clean_text(str(seg.get("title") or seg.get("caption") or "新闻"))
    pages = [deepcopy(p) for p in list(seg.get("visual_pages") or []) if isinstance(p, dict) and "evidenceVisual" not in p]
    if pages:
        pages[0]["title"] = pages[0].get("title") or title
        pages[0]["cards"] = cards
    else:
        pages.append({"kind": "cards", "title": title, "cards": cards})
    if str(seg.get("kind") or "news") == "news":
        pages[0]["kind"] = "brief" if str(seg.get("editorial_tier") or "headline") == "brief" else "cards"

    enabled_images = [
        img
        for img in list(seg.get("images") or [])
        if isinstance(img, dict) and (bool(img.get("required")) or img.get("enabled") is not False) and img.get("path")
    ]
    if enabled_images and pages:
        first_img = enabled_images[0]
        pages[0]["evidenceVisual"] = {
            "source": clean_text(str(first_img.get("source") or first_img.get("label") or "证据截图")),
            "title": clean_text(str(first_img.get("label") or title)),
            "url": clean_text(str(first_img.get("source_url") or "")),
            "image": str(first_img["path"]),
            "status": "captured",
            "required": bool(first_img.get("required", True)),
        }

    for img in enabled_images:
        label = clean_text(str(img.get("label") or "证据截图"))
        source = clean_text(str(img.get("source") or label))
        source_url = clean_text(str(img.get("source_url") or ""))
        pages.append(
            {
                "kind": "evidence",
                "title": label,
                "lead": clean_text(str(seg.get("caption") or title)),
                "source": source,
                "source_url": source_url,
                "evidenceVisual": {
                    "source": source,
                    "title": label,
                    "url": source_url,
                    "image": str(img["path"]),
                    "status": "captured",
                    "required": bool(img.get("required", True)),
                },
                "background_cards": cards,
                "cards": [],
                "review_generated": True,
            }
        )
    return pages


def state_to_final_script(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert review state payload into ordered enabled final-script segments."""
    enabled = [
        seg
        for seg in list(state.get("segments") or [])
        if isinstance(seg, dict) and seg.get("enabled") is not False
    ]
    labels = _unique_bottom_labels(enabled)
    news_total = sum(1 for seg in enabled if clean_text(str(seg.get("kind") or "news")) == "news")
    segments: list[dict[str, Any]] = []
    news_idx = 0

    for idx, (seg, label) in enumerate(zip(enabled, labels)):
        kind = clean_text(str(seg.get("kind") or "news"))
        if kind == "news":
            news_idx += 1
        title = clean_text(str(seg.get("title") or seg.get("headline") or f"新闻 {news_idx or idx + 1}"))
        caption = clean_text(str(seg.get("caption") or title))
        cards = _plain_cards(seg.get("cards"))
        default_tab = "Intro" if kind == "intro" else "Outro" if kind == "outro" else "速览" if kind == "ticker" else "模型更新"
        out: dict[str, Any] = {
            "index": idx,
            "kind": kind,
            "position": news_idx if kind == "news" else 0,
            "total": news_total if kind == "news" else 0,
            "story_id": clean_text(str(seg.get("story_id") or "")),
            "claim_ids": [clean_text(str(v)) for v in list(seg.get("claim_ids") or []) if clean_text(str(v))],
            "generation_path": clean_text(str(seg.get("generation_path") or "structured_editorial_plan")),
            "editorial_tier": clean_text(str(seg.get("editorial_tier") or "headline")),
            "title": title,
            "headline": clean_text(str(seg.get("headline") or title)),
            "caption": caption,
            "text": str(seg.get("text") or "").strip(),
            "active_tab": clean_text(str(seg.get("active_tab") or default_tab)),
            "bottom_tabs": labels,
            "bottom_active": label,
            "cards": cards,
            "visual_pages": _rebuild_visual_pages(seg, cards),
        }
        for opt_key in ("signal_kind", "disclaimer"):
            val = clean_text(str(seg.get(opt_key) or ""))
            if val:
                out[opt_key] = val
        ev_rows = [r for r in list(seg.get("evidence") or []) if isinstance(r, dict)]
        if ev_rows:
            out["evidence"] = ev_rows
        segments.append(out)
    return segments


def _write_final_markdown(path: Path, reviewed_at: str, segments: list[dict[str, Any]]) -> None:
    lines = ["# AI 日报最终审稿", "", f"保存时间：{reviewed_at}", ""]
    for seg in segments:
        kind = str(seg.get("kind") or "news")
        label = "开场" if kind == "intro" else "收尾" if kind == "outro" else "速览" if kind == "ticker" else f"{int(seg.get('position') or 0):02d}"
        lines.append(f"## {label} {seg.get('title') or ''}")
        lines.append(f"- 屏幕标题：{seg.get('caption') or ''}")
        lines.append(f"- 解说：{seg.get('text') or ''}")
        cards = [c for c in list(seg.get("cards") or []) if isinstance(c, dict)]
        if cards:
            lines.append("- 画面卡片：")
            for card in cards:
                lines.append(f"  - {card.get('title') or ''}：{card.get('body') or ''}")
        ev_count = sum(1 for p in list(seg.get("visual_pages") or []) if isinstance(p, dict) and p.get("kind") == "evidence")
        if ev_count:
            lines.append(f"- 图片素材：{ev_count} 张")
        lines.append("")
    write_text_atomic(path, "\n".join(lines).rstrip() + "\n")


def _sync_cover_story_count(run_dir: Path, news_count: int) -> None:
    cover_file = review_cover_path(run_dir)
    if not cover_file.is_file():
        return
    payload = read_json(cover_file, default=None)
    if not isinstance(payload, dict) or payload.get("storyCount") == news_count:
        return
    payload["storyCount"] = news_count
    if re.fullmatch(r"\d+\s*条\s*AI\s*动态", str(payload.get("badge") or "").strip()):
        payload["badge"] = f"{news_count:02d} 条 AI 动态" if news_count else "AI 动态"
    write_json_atomic(cover_file, payload)


def write_final_script_from_state(
    run_dir: Path,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist reviewed state, re-inject required evidence screenshots, emit final-script.{json,md}, and mark render required."""
    root = Path(run_dir).resolve()
    state = deepcopy(payload) if payload is not None else read_json(review_state_path(root))
    if not isinstance(state, dict):
        raise ValueError("review state payload must be a JSON object")

    _merge_required_evidence_images(root, state)
    reviewed_at = iso(now_utc())
    state["run_dir"] = str(root)
    state["run_date"] = str(state.get("run_date") or root.name)
    state["updated_at"] = reviewed_at
    write_json_atomic(review_state_path(root), state)

    segments = state_to_final_script(state)
    final_doc = {
        "version": 1,
        "reviewed_at": reviewed_at,
        "run_date": state["run_date"],
        "segments": segments,
    }
    final_json = review_final_script_path(root)
    final_md = review_final_md_path(root)
    write_json_atomic(final_json, final_doc)
    _write_final_markdown(final_md, reviewed_at, segments)

    news_count = sum(1 for seg in segments if seg.get("kind") == "news")
    _sync_cover_story_count(root, news_count)

    for stale in (review_morning_script_path(root), review_morning_md_path(root)):
        stale.unlink(missing_ok=True)

    _mark_render_required(root, final_json)
    return {
        "final_script": str(final_json),
        "final_md": str(final_md),
        "state_path": str(review_state_path(root)),
        "segments_count": len(segments),
        "news_count": news_count,
    }


def _default_cover_from_state(run_dir: Path) -> dict[str, Any]:
    root = Path(run_dir).resolve()
    segments: list[dict[str, Any]] = []
    if review_state_path(root).is_file():
        st = read_json(review_state_path(root), default={})
        if isinstance(st, dict):
            segments = state_to_final_script(st)
    if not segments:
        segments = _load_existing_segments(root)

    news = [s for s in segments if isinstance(s, dict) and str(s.get("kind") or "") == "news"]
    titles = [clean_text(str(s.get("title") or "")) for s in news if clean_text(str(s.get("title") or ""))]
    entities: list[str] = []
    for seg in news:
        raw_label = re.sub(r"^\s*\d{1,2}\s*", "", clean_text(str(seg.get("bottom_active") or ""))).strip(" /·|-")
        if raw_label and raw_label not in entities:
            entities.append(raw_label)
    return {
        "schema_version": COVER_SCHEMA_VERSION,
        "eyebrow": "AI DAILY BRIEF / AI 日报",
        "headline": titles[0] if titles else "今天的 AI 变化",
        "subheadline": titles[1] if len(titles) > 1 else (titles[0] if titles else "模型、产品与开发者动态"),
        "badge": f"{len(news):02d} 条 AI 动态" if news else "AI 动态",
        "date": root.name,
        "storyCount": len(news),
        "highlights": titles[:3],
        "entities": entities[:6],
    }


def load_cover_state(run_dir: Path) -> dict[str, Any]:
    """Load cover copy state for the review workbench, defaulting headline to lead story (titles[0])."""
    root = Path(run_dir).resolve()
    defaults = _default_cover_from_state(root)
    cover_file = review_cover_path(root)
    if not cover_file.is_file():
        return defaults
    raw = read_json(cover_file, default={})
    if not isinstance(raw, dict):
        return defaults
    merged = dict(defaults)
    for field in COVER_TEXT_FIELDS:
        val = raw.get(field)
        if isinstance(val, str) and val.strip():
            merged[field] = val.strip()
    for field in COVER_LIST_FIELDS:
        val = raw.get(field)
        if isinstance(val, list):
            cleaned = [str(x).strip() for x in val if isinstance(x, str) and str(x).strip()]
            if cleaned:
                merged[field] = cleaned
    merged["storyCount"] = defaults["storyCount"]
    return merged


def save_cover_state(run_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and persist review/cover.json, then mark render required."""
    if not isinstance(payload, dict):
        raise ValueError("cover payload must be a JSON object")
    root = Path(run_dir).resolve()
    defaults = _default_cover_from_state(root)
    saved = dict(defaults)

    for field in COVER_TEXT_FIELDS:
        if field in payload:
            val = payload[field]
            if not isinstance(val, str) or not val.strip():
                raise ValueError(f"cover {field} must be a non-empty string")
            saved[field] = val.strip()

    for field in COVER_LIST_FIELDS:
        if field in payload:
            val = payload[field]
            if not isinstance(val, list):
                raise ValueError(f"cover {field} must be a list of strings")
            cleaned = [str(x).strip() for x in val if str(x).strip()]
            saved[field] = cleaned

    saved["schema_version"] = COVER_SCHEMA_VERSION
    saved["storyCount"] = defaults["storyCount"]
    write_json_atomic(review_cover_path(root), saved)
    _mark_render_required(root, reason="cover_updated")
    return saved


def refresh_reviewed_bilibili_outputs(
    run_dir: Path,
    *,
    quality: str = "1080p",
    run_date: str = "",
) -> dict[str, str]:
    """Regenerate bilibili.{md,json} and pinned-comment.md from timed script.json."""
    root = Path(run_dir).resolve()
    effective_date = run_date or root.name
    raw = read_json(root / "script.json", default=[])
    segments = [s for s in raw if isinstance(s, dict)] if isinstance(raw, list) else []
    news = [s for s in segments if s.get("kind") == "news"]
    leads: list[str] = []
    for seg in news:
        title = clean_text(str(seg.get("title") or seg.get("caption") or ""))
        head = re.split(r"[：:，,｜|\s]", title)[0].strip()
        if head and head not in leads:
            leads.append(head)
        if len(leads) >= 2:
            break
    base = ("、".join(leads) + f" 等 {len(news)} 条 AI 动态") if leads else "AI 日报"
    suffix = f"【AI 日报 {effective_date}】"
    candidates = [f"{base}{suffix}"[:78], f"今天值得看的 AI 新闻汇总{suffix}"[:78]]
    summary = f"AI 日报，精选 {len(news)} 条模型、产品与开发者一手动态。完整时间轴见置顶评论。"

    intro = next((s for s in segments if s.get("kind") == "intro"), None)
    timeline_lines = [f"{format_mmss(float((intro or {}).get('start') or 0.0))} 开场｜AI 日报"]
    for seg in news:
        timeline_lines.append(
            f"{format_mmss(float(seg.get('start') or 0.0))} {clean_text(str(seg.get('title') or 'AI 动态'))[:56]}"
        )

    md_lines = [
        "# B站投稿草稿",
        "",
        "## 标题候选",
        *[f"- {t}" for t in candidates],
        "",
        "## 简介",
        summary,
        "",
        "时间轴：",
        *timeline_lines,
        "",
        "本次的播报完毕。",
        "",
        "## 标签",
        "AI,人工智能,大模型,科技早报,ChatGPT,开源模型,AIGC",
        "",
        "## 视频参数",
        f"- quality: {quality}",
        "- copyright: 原创",
        "- no_reprint: 1",
    ]
    write_text_atomic(root / "bilibili.md", "\n".join(md_lines) + "\n")
    write_text_atomic(root / "pinned-comment.md", "\n".join(["今日时间轴：", *timeline_lines, "", "本次的播报完毕。"]) + "\n")
    write_json_atomic(
        root / "bilibili.json",
        {
            "title": candidates[0],
            "title_candidates": candidates,
            "desc": summary,
            "tag": "AI,人工智能,大模型,科技早报,ChatGPT,开源模型,AIGC",
            "copyright": 1,
            "no_reprint": 1,
            "video": str(root / "final.mp4"),
            "cover": str(root / "cover.png"),
            "subtitle": str(root / "subtitles.srt"),
            "pinned_comment": str(root / "pinned-comment.md"),
            "tid": None,
        },
    )
    return {
        "bilibili": str(root / "bilibili.md"),
        "pinned_comment": str(root / "pinned-comment.md"),
        "bilibili_json": str(root / "bilibili.json"),
    }

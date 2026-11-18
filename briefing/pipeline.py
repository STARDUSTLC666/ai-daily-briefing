from __future__ import annotations

import hashlib
import subprocess
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .core import (
    EvidenceCard,
    FeedItem,
    PipelineLock,
    RunResult,
    RunState,
    Source,
    SourceHealth,
    card_is_community_signal,
    card_is_official_personnel_signal,
    connect,
    default_db_path,
    default_runs_dir,
    effective_config_sha256,
    ensure_dir,
    insert_health,
    insert_items,
    load_config,
    load_recent_items,
    load_sources,
    load_story_history,
    now_utc,
    project_root,
    read_json,
    save_evidence_cards,
    upsert_sources,
    url_host,
    write_json_atomic,
    write_text_atomic,
)
from .editorial import (
    build_edition_brief,
    cluster_items,
    refresh_bilibili_outputs,
    select_card_portfolio,
    write_package,
)
from .ingest import (
    attach_evidence_screenshots,
    attach_github_compare_evidence,
    collect_sources,
    enrich_feed_items,
    plan_enrichment_items,
    prune_evidence_screenshots,
    reconcile_official_sources,
)
from .render import build_segments, create_visual_qa_package, render_briefing_video
from .review import (
    create_review_package,
    refresh_reviewed_bilibili_outputs,
    review_final_script_path,
    review_morning_md_path,
    review_morning_script_path,
)
from .verify import (
    build_release_attestation,
    capture_clean_render_provenance,
    create_agent_package,
    mark_render_required,
    repairable_news_positions,
    verify_clusters,
    verify_run_dir,
)


def resolve_run_date(value: str) -> str:
    if value in {"today", ""}:
        return datetime.now().strftime("%Y-%m-%d")
    return value


def compute_source_coverage(
    sources: list[Source],
    health: list[SourceHealth],
    *,
    required: bool = True,
) -> dict[str, Any]:
    configured = [s for s in sources if s.id.startswith("x_")]
    enabled = [s for s in configured if s.enabled]
    by_id = {s.id: s for s in configured}
    required_lanes = {
        s.id
        for s in configured
        if s.type == "agent_social"
        and s.id in {"x_codex_official_leads", "x_codex_personnel_leads", "x_codex_community_leads"}
    }
    healthy_ids = {
        row.source_id
        for row in health
        if row.source_id.startswith("x_") and row.status == "ok" and not row.stale
    }
    healthy_official = {
        sid for sid in healthy_ids if str(by_id[sid].reliability if sid in by_id else "").startswith("official")
    }
    healthy_community = {
        sid for sid in healthy_ids if str(by_id[sid].reliability if sid in by_id else "") in {"community", "aggregator"}
    }
    expected = {"x_codex_official_leads", "x_codex_personnel_leads", "x_codex_community_leads"}
    lane_ok = (
        required_lanes == expected
        and (required_lanes & healthy_ids) == required_lanes
        and bool(healthy_official & required_lanes)
        and bool(healthy_community & required_lanes)
    )
    return {
        "x": {
            "required": required,
            "configured_sources": len(configured),
            "enabled_sources": len(enabled),
            "healthy_sources": len(healthy_ids),
            "healthy_official_sources": len(healthy_official),
            "healthy_community_sources": len(healthy_community),
            "required_agent_lanes": sorted(required_lanes),
            "healthy_agent_lanes": sorted(required_lanes & healthy_ids),
            "status": ("healthy" if lane_ok else "coverage_gap") if required else "not_requested",
            "reason": (
                ""
                if lane_ok
                else "X coverage requires fresh Codex lane_audits for official, personnel, and community leads"
            )
            if required
            else "Public-web edition; social accounts were not included or audited",
        }
    }


def compute_package_quality(
    selected: list[EvidenceCard],
    *,
    minimum_publish_items: int,
    source_coverage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headline_count = sum(c.editorial_tier == "headline" for c in selected)
    entities = {c.entity.strip().lower() for c in selected if c.entity.strip()}
    sources = {
        (str(link.get("source_name") or link.get("source") or "").strip().lower() or url_host(link.get("url")))
        for card in selected
        for link in card.evidence_links
    } - {""}
    headline = next((c for c in selected if c.editorial_tier == "headline"), None)
    headline_safe = headline is None or (
        not card_is_community_signal(headline) and not card_is_official_personnel_signal(headline)
    )
    reliable = [
        c for c in selected if not card_is_community_signal(c) and not card_is_official_personnel_signal(c)
    ]
    issues: list[str] = []
    if len(selected) < minimum_publish_items:
        issues.append(f"selected {len(selected)}, below minimum {minimum_publish_items}")
    if headline_count > 1:
        issues.append(f"expected at most one headline, got {headline_count}")
    if not headline_safe:
        issues.append("headline is an X/community signal")
    if not reliable:
        issues.append("at least one non-X/community verified story is required")
    content_ok = not issues
    x_cov = (source_coverage or {}).get("x") or {}
    coverage_ok = not bool(x_cov.get("required")) or x_cov.get("status") == "healthy"
    publish_issues = list(issues)
    if not coverage_ok:
        publish_issues.append(str(x_cov.get("reason") or "required source coverage is unavailable"))
    return {
        "ok": content_ok,
        "publish_allowed": content_ok and coverage_ok,
        "edition_mode": "rolling_24h" if content_ok else "insufficient_content",
        "story_count_policy": "all_qualified_in_window",
        "minimum_publish_items": minimum_publish_items,
        "selected_items": len(selected),
        "headline_items": headline_count,
        "headline_safe": headline_safe,
        "reliable_items": len(reliable),
        "distinct_entities": len(entities),
        "distinct_primary_sources": len(sources),
        "content_issues": issues,
        "publish_issues": publish_issues,
        "reason": "; ".join(publish_issues),
    }


def build_provenance(config_path: Path | None = None) -> dict[str, Any]:
    root = project_root()
    config_file = (config_path or (root / "sources.yaml")).resolve()
    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True, stderr=subprocess.DEVNULL
        ).strip()
        status = subprocess.check_output(
            ["git", "status", "--porcelain=v1"], cwd=root, text=True, stderr=subprocess.DEVNULL
        )
        diff = subprocess.check_output(["git", "diff", "--binary", "HEAD"], cwd=root, stderr=subprocess.DEVNULL)
    except Exception:
        revision, status, diff = "", "", b""
    digest = hashlib.sha256()
    digest.update(status.encode("utf-8"))
    digest.update(diff)
    cfg_bytes = config_file.read_bytes() if config_file.exists() else b""
    return {
        "git_sha": revision,
        "git_dirty": bool(status.strip()),
        "working_tree_fingerprint": digest.hexdigest(),
        "dirty_file_count": len([line for line in status.splitlines() if line.strip()]),
        "config_path": str(config_file),
        "config_sha256": hashlib.sha256(cfg_bytes).hexdigest(),
        "effective_config_sha256": effective_config_sha256(config_file) if config_file.exists() else "",
    }


def _ticker_items_from_cards(
    all_cards: list[EvidenceCard],
    selected_cards: list[EvidenceCard],
    max_items: int,
) -> list[dict[str, Any]]:
    if max_items <= 0:
        return []
    chosen = {c.cluster_key for c in selected_cards}
    for card in selected_cards:
        chosen.update(str(k) for k in card.source_cluster_keys if str(k).strip())
    candidates = [
        c
        for c in all_cards
        if c.selected
        and c.cluster_key not in chosen
        and c.risk == "green"
        and c.official_count >= 1
        and not card_is_community_signal(c)
        and not card_is_official_personnel_signal(c)
        and (c.key_facts or c.event_title)
    ]
    candidates.sort(key=lambda c: c.score, reverse=True)
    return [
        {
            "cluster_key": c.cluster_key,
            "entity": c.entity,
            "title": c.event_title,
            "body": c.key_facts[0] if c.key_facts else c.event_title,
            "score": c.score,
        }
        for c in candidates[:max_items]
    ]


def _card_exclusion_keys(card: EvidenceCard) -> set[str]:
    keys = {str(k).strip() for k in card.source_cluster_keys if str(k).strip()}
    return keys or {card.cluster_key}


def _render_outputs_ready(render_info: dict[str, Any]) -> bool:
    if str(render_info.get("status") or "") != "ok":
        return False
    for key in ("video", "cover", "subtitles"):
        target = Path(str(render_info.get(key) or ""))
        if not target.is_file() or target.stat().st_size == 0:
            return False
    return True


def append_morning_stories(
    run_dir: Path,
    fresh_cards: list[EvidenceCard],
    *,
    max_new_items: int = 3,
) -> dict[str, Any]:
    """Append non-duplicate verified morning cards onto an existing reviewed script."""
    run_dir = Path(run_dir).resolve()
    base_path = review_final_script_path(run_dir)
    if not base_path.exists():
        return {"added": 0, "status": "no_reviewed_script"}
    base_payload = read_json(base_path, default={})
    segments = list(base_payload.get("segments") if isinstance(base_payload, dict) else base_payload or [])
    existing_urls = {
        str(ev.get("url") or "").strip()
        for seg in segments
        for ev in (seg.get("evidence") or [])
        if isinstance(ev, dict) and str(ev.get("url") or "").strip()
    }
    existing_titles = {str(seg.get("title") or "").strip().lower() for seg in segments if seg.get("kind") == "news"}

    novel: list[EvidenceCard] = []
    for card in fresh_cards:
        urls = {str(ev.get("url") or "").strip() for ev in card.evidence_links if str(ev.get("url") or "").strip()}
        if urls & existing_urls or card.event_title.strip().lower() in existing_titles:
            continue
        novel.append(card)
        if len(novel) >= max(0, int(max_new_items)):
            break

    if not novel:
        write_json_atomic(review_morning_script_path(run_dir), {"segments": segments})
        return {"added": 0, "status": "unchanged", "path": str(review_morning_script_path(run_dir))}

    run_date = run_dir.name
    fresh_segments = [s for s in build_segments(novel, run_date=run_date) if s.get("kind") == "news"]
    intro = [s for s in segments if s.get("kind") == "intro"]
    news = [s for s in segments if s.get("kind") == "news"] + fresh_segments
    tail = [s for s in segments if s.get("kind") not in {"intro", "news"}]
    merged = intro + news + tail
    news_total = len(news)
    pos = 0
    for idx, seg in enumerate(merged):
        seg["index"] = idx
        if seg.get("kind") == "news":
            pos += 1
            seg["position"] = pos
            seg["total"] = news_total

    morning_json = review_morning_script_path(run_dir)
    morning_md = review_morning_md_path(run_dir)
    write_json_atomic(morning_json, {"segments": merged})
    md_lines = [f"# AI 日报早间合并定稿（{run_date}）", ""]
    for seg in merged:
        md_lines.append(f"## [{seg.get('kind')}] {seg.get('title', '')}")
        md_lines.append("")
        md_lines.append(str(seg.get("text") or ""))
        md_lines.append("")
    write_text_atomic(morning_md, "\n".join(md_lines))
    mark_render_required(run_dir, reason="morning_append", source_path=morning_json)
    return {"added": len(novel), "status": "merged", "path": str(morning_json)}


def execute_render_gate(
    run_dir: Path,
    *,
    cards: list[EvidenceCard] | None = None,
    run_date: str = "",
    quality: str = "1080p",
    tts_backend: str = "edge",
    tts_profile: str = "",
    fallback_duration: float = 8.0,
    ticker_items: list[dict[str, Any]] | None = None,
    edition_brief: dict[str, Any] | None = None,
    visual_qa: bool = False,
    require_clean_release: bool = False,
    leave_open_for_visual_qa: bool = False,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Unified render + QA gate lifecycle used by `run` and `render` commands."""
    run_dir = Path(run_dir).resolve()
    manifest_path = run_dir / "manifest.json"
    manifest = read_json(manifest_path, default={})
    resolved_date = run_date or str(manifest.get("run_date") or run_dir.name)
    run_id = str(manifest.get("run_id") or f"{resolved_date}-{uuid.uuid4().hex[:8]}")
    run_state = RunState.load_or_create(run_dir / "run-state.json", run_id, resolved_date)

    run_state.start("RENDER", input_count=len(cards) if cards is not None else len(manifest.get("selected") or []))
    manifest["automatic_quality_gate"] = {
        "ok": False,
        "status": "pending",
        "report": str(run_dir / "auto-quality.json"),
        "attempts": 1,
        "automatic_exclusions": [],
        "automatic_retry_enabled": False,
    }
    manifest.pop("release_attestation", None)
    if require_clean_release:
        manifest["render_provenance"] = capture_clean_render_provenance(config_path)
    write_json_atomic(manifest_path, manifest)

    render_info: dict[str, Any] = {}
    try:
        render_info = render_briefing_video(
            run_dir,
            cards,
            run_date=resolved_date,
            quality=quality,
            tts_backend=tts_backend,
            tts_profile=tts_profile,
            fallback_duration=fallback_duration,
            ticker_items=ticker_items if ticker_items is not None else manifest.get("ticker_items"),
            edition_brief=edition_brief if edition_brief is not None else manifest.get("edition_brief"),
            visual_qa=visual_qa,
        )
        if not _render_outputs_ready(render_info):
            raise RuntimeError(str(render_info.get("error") or "render did not produce publishable media files"))

        script_payload = read_json(run_dir / "script.json", default=None)
        if cards:
            refresh_bilibili_outputs(run_dir, script_payload)
        else:
            refresh_reviewed_bilibili_outputs(run_dir, quality=quality, run_date=resolved_date)

        visual_qa_info = render_info.get("visual_qa") or (create_visual_qa_package(run_dir) if visual_qa else {})
        manifest = read_json(manifest_path, default=manifest)
        manifest["render"] = render_info
        write_json_atomic(manifest_path, manifest)

        if leave_open_for_visual_qa:
            return {
                "ok": True,
                "status": "visual_review_pending",
                "render": render_info,
                "visual_qa": visual_qa_info,
            }

        quality_report = verify_run_dir(run_dir, allow_in_progress=True)
        quality_ok = bool(quality_report.get("ok"))
        write_json_atomic(run_dir / "auto-quality.json", quality_report)

        manifest = read_json(manifest_path, default=manifest)
        manifest["render"] = render_info
        manifest["automatic_quality_gate"] = {
            "ok": quality_ok,
            "status": "passed" if quality_ok else "failed",
            "report": str(run_dir / "auto-quality.json"),
            "attempts": 1,
            "automatic_exclusions": [],
            "automatic_retry_enabled": False,
            "errors": list(quality_report.get("errors") or []),
        }
        if quality_ok and manifest.get("render_provenance"):
            manifest["release_attestation"] = build_release_attestation(
                run_dir, provenance=manifest["render_provenance"]
            )
        write_json_atomic(manifest_path, manifest)

        if quality_ok:
            run_state.finish("RENDER", metrics={"render_status": "ok", "quality_ok": True})
            run_state.complete("QA_PASSED")
        else:
            run_state.fail("RENDER", "; ".join(quality_report.get("errors") or ["post-render quality gate failed"]))
            run_state.complete("RENDER_FAILED")

        return {
            "ok": quality_ok,
            "status": "ok" if quality_ok else "quality_failed",
            "render": render_info,
            "quality": quality_report,
            "visual_qa": visual_qa_info,
        }
    except Exception as exc:
        manifest = read_json(manifest_path, default={})
        if manifest:
            manifest["automatic_quality_gate"] = {
                "ok": False,
                "status": "failed",
                "report": str(run_dir / "auto-quality.json"),
                "attempts": 1,
                "automatic_exclusions": [],
                "automatic_retry_enabled": False,
                "errors": [f"{type(exc).__name__}: {exc}"],
            }
            if render_info:
                manifest["render"] = render_info
            manifest.pop("release_attestation", None)
            write_json_atomic(manifest_path, manifest)
        run_state.fail("RENDER", exc)
        raise


def _run_pipeline_unlocked(
    date: str = "today",
    *,
    quality: str = "1080p",
    config_path: Path | None = None,
    db_path: Path | None = None,
    runs_dir: Path | None = None,
    lookback_hours: int | None = None,
    max_items: int | None = None,
    workers: int = 8,
    mode: str = "auto",
    tts_backend: str = "edge",
    tts_profile: str = "",
    force_review: bool = False,
) -> RunResult:
    run_date = resolve_run_date(date)
    cfg = load_config(config_path)
    defaults = dict(cfg.get("defaults") or {})
    lookback = int(lookback_hours or defaults.get("lookback_hours", 24))
    defaults["lookback_hours"] = lookback
    strict_auto = bool(defaults.get("selection_strict_auto", True))
    effective_max_items = int(defaults.get("selection_max_items", 0) or 0) if max_items is None else int(max_items)
    min_publish_items = max(1, int(defaults.get("selection_min_publish_items", 1) or 1))
    auto_repair = bool(defaults.get("automatic_quality_repair", defaults.get("auto_repair", True)))
    ticker_enabled = bool(defaults.get("ticker_enabled", False))
    ticker_max_items = max(0, int(defaults.get("ticker_max_items", 10) or 0))

    all_sources = load_sources(config_path, include_disabled=True)
    enabled_sources = [s for s in all_sources if s.enabled]
    provenance = build_provenance(config_path)

    resolved_db = (db_path or default_db_path()).resolve()
    resolved_runs = (runs_dir or default_runs_dir()).resolve()
    out_dir = ensure_dir(resolved_runs / run_date)
    run_id = f"{run_date}-{uuid.uuid4().hex[:10]}"
    run_state = RunState(out_dir / "run-state.json", run_id, run_date)

    skip_render = mode != "auto"
    need_screenshots = mode in {"auto", "review", "agent"}

    conn = connect(resolved_db)
    try:
        upsert_sources(conn, all_sources)
        run_state.start("COLLECT", input_count=len(enabled_sources), fingerprint_input=[s.id for s in enabled_sources])
        health, fetched = collect_sources(enabled_sources, max_workers=workers, defaults=defaults)
        source_coverage = compute_source_coverage(
            all_sources,
            health,
            required=defaults.get("require_x_coverage") is not False,
        )
        run_state.finish(
            "COLLECT",
            output_count=len(fetched),
            metrics={"healthy_sources": sum(1 for row in health if row.status == "ok")},
        )
        for h in health:
            insert_health(conn, h)
        insert_items(conn, fetched)

        window_end = now_utc()
        window_start = window_end - timedelta(hours=lookback)
        enabled_ids = {s.id for s in enabled_sources}
        recent_items: list[FeedItem] = [
            it for it in load_recent_items(conn, since=window_start, limit=1500) if it.source_id in enabled_ids
        ]

        run_state.start("ENRICH", input_count=len(recent_items))
        planned_items = plan_enrichment_items(recent_items, defaults=defaults)
        enrichment_info = enrich_feed_items(
            planned_items,
            defaults=defaults,
            asset_dir=out_dir / "source-assets" if defaults.get("content_enrichment_capture_assets") else None,
            progress_sink=lambda m: run_state.progress("ENRICH", m),
        )
        run_state.finish("ENRICH", output_count=int(enrichment_info.get("ok", 0)), metrics=enrichment_info)

        run_state.start("RECONCILE", input_count=len(recent_items))
        reconcile_info = reconcile_official_sources(
            recent_items,
            defaults=defaults,
            sources=all_sources,
            now=window_end,
        )
        run_state.finish("RECONCILE", output_count=int(reconcile_info.get("matched", 0)), metrics=reconcile_info)

        run_state.start("VERIFY", input_count=len(recent_items), fingerprint_input={"lookback_hours": lookback})
        clusters = cluster_items(recent_items)
        cards = verify_clusters(clusters, now=window_end, lookback_hours=lookback, defaults=defaults)
        gh_attached = attach_github_compare_evidence(cards)
        run_state.finish(
            "VERIFY",
            output_count=len(cards),
            metrics={
                "clusters": len(clusters),
                "eligible_cards": sum(1 for c in cards if c.selected),
                "github_compare_evidence": gh_attached,
            },
        )

        story_history = load_story_history(conn, before_run_date=run_date)
        screenshot_info: dict[str, Any] = {"status": "skipped"}
        if need_screenshots:
            provisional = select_card_portfolio(
                cards,
                max_items=effective_max_items,
                min_items=min_publish_items,
                strict_auto=strict_auto,
                story_history=story_history,
            )["selected"]
            if provisional:
                screenshot_info = attach_evidence_screenshots(provisional, out_dir, defaults=defaults)

        insert_items(conn, recent_items)
        save_evidence_cards(conn, run_date, cards)
    except Exception as exc:
        active = next((s.name for s in reversed(run_state.stages) if s.status == "RUNNING"), "PIPELINE")
        run_state.fail(active, exc)
        raise
    finally:
        conn.close()

    run_state.start("EDIT", input_count=len(cards))
    portfolio = select_card_portfolio(
        cards,
        max_items=effective_max_items,
        min_items=min_publish_items,
        strict_auto=strict_auto,
        story_history=story_history,
    )
    selected_cards: list[EvidenceCard] = portfolio["selected"]
    removed_pngs = prune_evidence_screenshots(out_dir, selected_cards) if need_screenshots else []
    screenshot_info = {**screenshot_info, "pruned_files": len(removed_pngs)}

    ticker_items = _ticker_items_from_cards(cards, selected_cards, ticker_max_items) if ticker_enabled else []
    package_quality = compute_package_quality(
        selected_cards,
        minimum_publish_items=min_publish_items,
        source_coverage=source_coverage,
    )
    extra_manifest: dict[str, Any] = {
        "run_state": str(out_dir / "run-state.json"),
        "content_enrichment": enrichment_info,
        "official_reconciliation": reconcile_info,
        "evidence_screenshots": screenshot_info,
        "package_quality": package_quality,
        "edition_mode": package_quality["edition_mode"],
        "publish_allowed": package_quality["publish_allowed"],
        "selection_metrics": portfolio.get("metrics", {}),
    }
    try:
        written_files = write_package(
            out_dir,
            run_date=run_date,
            run_id=run_id,
            health=health,
            all_cards=cards,
            selected_cards=selected_cards,
            provenance=provenance,
            source_coverage=source_coverage,
            ticker_items=ticker_items,
            extra_manifest=extra_manifest,
        )
    except Exception as exc:
        run_state.fail("EDIT", exc)
        raise
    run_state.finish("EDIT", output_count=len(selected_cards), metrics={"package_quality": package_quality})

    if mode in {"review", "agent"}:
        create_review_package(out_dir, selected_cards, run_date=run_date, force=force_review)
        if mode == "agent":
            create_agent_package(out_dir)

    if skip_render:
        run_state.complete("EDITED")
        return RunResult(
            run_id=run_id,
            run_date=run_date,
            out_dir=out_dir,
            source_health=health,
            all_cards=cards,
            selected_cards=selected_cards,
            render_info={"status": "skipped"},
            quality=package_quality,
            files={k: str(v) for k, v in written_files.items()},
        )

    if not package_quality["ok"]:
        run_state.complete("INSUFFICIENT_CONTENT")
        return RunResult(
            run_id=run_id,
            run_date=run_date,
            out_dir=out_dir,
            source_health=health,
            all_cards=cards,
            selected_cards=selected_cards,
            render_info={"status": "insufficient_content"},
            quality={"ok": False, "errors": package_quality["content_issues"]},
            files={k: str(v) for k, v in written_files.items()},
        )

    # Automatic render + copy-hygiene exclusion loop
    excluded_keys: set[str] = set()
    exclusions_log: list[dict[str, Any]] = []
    max_attempts = max(1, min(6, len(selected_cards)))
    render_info: dict[str, Any] = {}
    quality_report: dict[str, Any] = {"ok": False, "errors": []}

    run_state.start("RENDER", input_count=len(selected_cards))
    for attempt in range(1, max_attempts + 1):
        brief = build_edition_brief(selected_cards, run_date=run_date).to_dict()
        try:
            render_info = render_briefing_video(
                out_dir,
                selected_cards,
                run_date=run_date,
                quality=quality,
                tts_backend=tts_backend,
                tts_profile=tts_profile,
                ticker_items=ticker_items,
                edition_brief=brief,
            )
        except Exception as exc:
            run_state.fail("RENDER", exc)
            raise

        if not _render_outputs_ready(render_info):
            quality_report = {"ok": False, "errors": ["render did not produce publishable media files"]}
            break

        refresh_bilibili_outputs(out_dir, read_json(out_dir / "script.json", default=None))
        quality_report = verify_run_dir(out_dir, allow_in_progress=True)
        write_json_atomic(out_dir / f"auto-quality-attempt-{attempt}.json", quality_report)
        if quality_report.get("ok"):
            break

        bad_positions = (
            repairable_news_positions(
                list(quality_report.get("errors") or []),
                read_json(out_dir / "script.json", default=None),
            )
            if auto_repair
            else set()
        )
        bad_cards = [selected_cards[pos - 1] for pos in sorted(bad_positions) if 0 < pos <= len(selected_cards)]
        if not bad_cards:
            break
        for bad in bad_cards:
            excluded_keys.update(_card_exclusion_keys(bad))
        exclusions_log.append(
            {
                "attempt": attempt,
                "excluded_positions": sorted(bad_positions),
                "excluded_titles": [c.event_title for c in bad_cards],
            }
        )
        next_portfolio = select_card_portfolio(
            cards,
            max_items=effective_max_items,
            min_items=min_publish_items,
            strict_auto=strict_auto,
            story_history=story_history,
            excluded_keys=excluded_keys,
        )
        selected_cards = next_portfolio["selected"]
        package_quality = compute_package_quality(
            selected_cards,
            minimum_publish_items=min_publish_items,
            source_coverage=source_coverage,
        )
        if not selected_cards or not package_quality["ok"]:
            quality_report = {"ok": False, "errors": package_quality["content_issues"]}
            break
        prune_evidence_screenshots(out_dir, selected_cards)
        ticker_items = _ticker_items_from_cards(cards, selected_cards, ticker_max_items) if ticker_enabled else []
        extra_manifest["package_quality"] = package_quality
        extra_manifest["edition_mode"] = package_quality["edition_mode"]
        extra_manifest["publish_allowed"] = package_quality["publish_allowed"]
        written_files = write_package(
            out_dir,
            run_date=run_date,
            run_id=run_id,
            health=health,
            all_cards=cards,
            selected_cards=selected_cards,
            provenance=provenance,
            source_coverage=source_coverage,
            ticker_items=ticker_items,
            extra_manifest=extra_manifest,
        )

    quality_ok = bool(quality_report.get("ok"))
    write_json_atomic(out_dir / "auto-quality.json", quality_report)
    auto_gate = {
        "ok": quality_ok,
        "status": "passed" if quality_ok else "failed",
        "report": str(out_dir / "auto-quality.json"),
        "attempts": len(exclusions_log) + 1,
        "automatic_exclusions": exclusions_log,
        "automatic_retry_enabled": auto_repair,
    }
    manifest = read_json(out_dir / "manifest.json", default={})
    manifest["render"] = render_info
    manifest["automatic_quality_gate"] = auto_gate
    write_json_atomic(out_dir / "manifest.json", manifest)

    run_state.finish("RENDER", output_count=len(selected_cards), metrics={"quality_ok": quality_ok})
    run_state.complete("QA_PASSED" if quality_ok else "RENDER_FAILED")

    return RunResult(
        run_id=run_id,
        run_date=run_date,
        out_dir=out_dir,
        source_health=health,
        all_cards=cards,
        selected_cards=selected_cards,
        render_info=render_info,
        quality=quality_report,
        files={k: str(v) for k, v in written_files.items()},
    )


def run_pipeline(date: str = "today", **kwargs: Any) -> RunResult:
    """Serialize manual and scheduled runs for the same briefing date."""
    run_date = resolve_run_date(date)
    runs_dir = Path(kwargs.get("runs_dir") or default_runs_dir()).resolve()
    with PipelineLock(runs_dir / ".locks" / f"{run_date}.lock"):
        return _run_pipeline_unlocked(date=date, **kwargs)

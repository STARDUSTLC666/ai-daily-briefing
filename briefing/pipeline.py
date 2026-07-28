from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from urllib.parse import urlparse
from pathlib import Path
import subprocess
from typing import Any
import uuid

from .cluster import cluster_items
from .collect import collect_sources
from .config import default_db_path, default_runs_dir, load_config, load_sources
from .content_enrichment import enrich_feed_items, should_enrich_item
from .db import connect, insert_health, insert_items, load_recent_items, load_story_history, save_document, save_evidence_cards, upsert_sources
from .document_store import build_document
from .enrichment_planner import plan_enrichment_items
from .evidence_screenshots import attach_evidence_screenshots, prune_evidence_screenshots
from .github_release import attach_github_compare_evidence
from .output_verify import repairable_news_positions, verify_run_dir
from .render import render_briefing_video
from .util import ensure_dir
from . import verify as verify_module
from .verify import verify_clusters
from .writer import refresh_bilibili_timeline_from_script, select_card_portfolio, write_package
from .models import EvidenceCard, Source, SourceHealth
from .official_reconciliation import reconcile_official_sources
from .run_state import RunState
from .social_signals import card_is_community_signal, card_is_official_personnel_signal

UTC = timezone.utc


@dataclass(slots=True)
class RunResult:
    run_date: str
    out_dir: Path
    sources_count: int
    fetched_items: int
    inserted_items: int
    clusters_count: int
    cards_count: int
    selected_count: int
    render_status: str
    selected_cards: list[EvidenceCard] | None = None
    quality_ok: bool | None = None


def resolve_run_date(value: str) -> str:
    if value in {"today", ""}:
        return datetime.now().strftime("%Y-%m-%d")
    return value


def _coverage_gap_health(sources: list[Source]) -> list[SourceHealth]:
    if os.environ.get("RSSHUB_BASE_URL", "").strip():
        return []
    return [
        SourceHealth(
            source_id=source.id,
            source_name=source.name,
            tier=source.tier,
            enabled=False,
            status="coverage_gap",
            error="RSSHUB_BASE_URL is not configured; X discovery lane is unavailable",
        )
        for source in sources
        if source.type == "rsshub" and source.id.startswith("x_") and not source.enabled
    ]


def _source_coverage(sources: list[Source], health: list[SourceHealth]) -> dict[str, Any]:
    configured = [source for source in sources if source.id.startswith("x_")]
    enabled = [source for source in configured if source.enabled]
    by_id = {source.id: source for source in configured}
    required_agent_lanes = {
        source.id
        for source in configured
        if source.type == "agent_social"
        and source.id in {"x_codex_official_leads", "x_codex_personnel_leads", "x_codex_community_leads"}
    }
    healthy_ids = {
        row.source_id
        for row in health
        if row.source_id.startswith("x_")
        and row.status == "ok"
        and not row.stale
    }
    healthy_official = {
        source_id
        for source_id in healthy_ids
        if str(by_id.get(source_id).reliability if by_id.get(source_id) else "").startswith("official")
    }
    healthy_community = {
        source_id
        for source_id in healthy_ids
        if str(by_id.get(source_id).reliability if by_id.get(source_id) else "") in {"community", "aggregator"}
    }
    healthy_agent_lanes = required_agent_lanes & healthy_ids
    lane_ok = (
        required_agent_lanes == {"x_codex_official_leads", "x_codex_personnel_leads", "x_codex_community_leads"}
        and healthy_agent_lanes == required_agent_lanes
        and bool(healthy_official & required_agent_lanes)
        and bool(healthy_community & required_agent_lanes)
    )
    return {
        "x": {
            "required": True,
            "configured_sources": len(configured),
            "enabled_sources": len(enabled),
            "healthy_sources": len(healthy_ids),
            "healthy_official_sources": len(healthy_official),
            "healthy_community_sources": len(healthy_community),
            "required_agent_lanes": sorted(required_agent_lanes),
            "healthy_agent_lanes": sorted(healthy_agent_lanes),
            "status": "healthy" if lane_ok else "coverage_gap",
            "reason": "" if lane_ok else "X coverage requires fresh Codex lane_audits for official accounts, official personnel, and community leads; feed mirrors cannot satisfy this gate",
        }
    }


def _ticker_candidates(
    cards: list[EvidenceCard],
    selected_cards: list[EvidenceCard],
    max_items: int,
) -> list[EvidenceCard]:
    """Verified green official stories that missed the main portfolio.

    Same evidence bar as everything else — these cards already passed claim
    verification; the portfolio simply had no seat for them. Signals and
    non-official sources never enter the digest.
    """
    if max_items <= 0:
        return []
    chosen = {card.cluster_key for card in selected_cards}
    for card in selected_cards:
        chosen.update(str(key) for key in card.source_cluster_keys if str(key).strip())
    candidates = [
        card
        for card in cards
        if card.selected
        and card.cluster_key not in chosen
        and card.risk == "green"
        and card.official_count >= 1
        and not card_is_community_signal(card)
        and not card_is_official_personnel_signal(card)
        and (card.key_facts or card.event_title)
    ]
    candidates.sort(key=lambda card: card.score, reverse=True)
    return candidates[:max_items]


def _items_from_enabled_sources(items: list[Any], enabled_sources: list[Source]) -> list[Any]:
    """Keep cached rows from disabled deep-scan sources out of a daily run."""
    enabled_ids = {source.id for source in enabled_sources if source.enabled}
    return [item for item in items if item.source_id in enabled_ids]


def _run_provenance(config_path: Path | None) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    config_file = (config_path or (root / "sources.yaml")).resolve()
    try:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()
        status = subprocess.check_output(["git", "status", "--porcelain=v1"], cwd=root, text=True, stderr=subprocess.DEVNULL)
        diff = subprocess.check_output(["git", "diff", "--binary", "HEAD"], cwd=root, stderr=subprocess.DEVNULL)
        untracked = subprocess.check_output(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=root, stderr=subprocess.DEVNULL
        ).decode("utf-8", errors="replace").split("\0")
    except Exception:
        revision, status, diff, untracked = "", "", b"", []
    worktree_digest = hashlib.sha256()
    worktree_digest.update(status.encode("utf-8"))
    worktree_digest.update(diff)
    for relative in sorted(path for path in untracked if path):
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
            if candidate.is_file():
                worktree_digest.update(relative.encode("utf-8"))
                worktree_digest.update(candidate.read_bytes())
        except (OSError, ValueError):
            continue
    config_bytes = config_file.read_bytes() if config_file.exists() else b""
    return {
        "git_sha": revision,
        "git_dirty": bool(status.strip()),
        "working_tree_fingerprint": worktree_digest.hexdigest(),
        "dirty_file_count": len([line for line in status.splitlines() if line.strip()]),
        "config_path": str(config_file),
        "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
    }


def _package_quality(
    selected_count: int,
    minimum_publish_items: int,
    selected: list[EvidenceCard] | None = None,
    *,
    source_coverage: dict[str, Any] | None = None,
) -> dict[str, object]:
    selected = selected or []
    headline_count = sum(card.editorial_tier == "headline" for card in selected)
    entities = {card.entity.strip().lower() for card in selected if card.entity.strip()}
    sources = {
        (str(link.get("source_name") or link.get("source") or "").strip().lower() or urlparse(str(link.get("url") or "")).hostname or "")
        for card in selected for link in card.evidence_links
    } - {""}
    headline = next((card for card in selected if card.editorial_tier == "headline"), None)
    headline_safe = headline is None or (
        not card_is_community_signal(headline) and not card_is_official_personnel_signal(headline)
    )
    reliable_items = [
        card
        for card in selected
        if not card_is_community_signal(card) and not card_is_official_personnel_signal(card)
    ]
    issues: list[str] = []
    if selected_count < minimum_publish_items:
        issues.append(f"selected {selected_count}, below minimum {minimum_publish_items}")
    if headline_count > 1:
        issues.append(f"expected at most one headline, got {headline_count}")
    if not headline_safe:
        issues.append("headline is an X/community signal")
    if not reliable_items:
        issues.append("at least one non-X/community verified story is required")
    content_ok = not issues
    x_coverage = (source_coverage or {}).get("x") or {}
    coverage_ok = not bool(x_coverage.get("required")) or x_coverage.get("status") == "healthy"
    publish_issues = list(issues)
    if not coverage_ok:
        publish_issues.append(str(x_coverage.get("reason") or "required source coverage is unavailable"))
    return {
        "ok": content_ok,
        "publish_allowed": content_ok and coverage_ok,
        "edition_mode": "rolling_24h" if content_ok else "insufficient_content",
        "story_count_policy": "all_qualified_in_window",
        "minimum_publish_items": minimum_publish_items,
        "selected_items": selected_count,
        "headline_items": headline_count,
        "headline_safe": headline_safe,
        "reliable_items": len(reliable_items),
        "distinct_entities": len(entities),
        "distinct_primary_sources": len(sources),
        "content_issues": issues,
        "publish_issues": publish_issues,
        "reason": "; ".join(publish_issues),
    }


def _card_exclusion_keys(card: EvidenceCard) -> set[str]:
    """Return every raw cluster represented by a selected portfolio card."""
    keys = {str(key).strip() for key in card.source_cluster_keys if str(key).strip()}
    return keys or {card.cluster_key}


def _update_manifest_metadata(
    out_dir: Path,
    *,
    run_id: str,
    screenshot_info: dict[str, Any],
    enrichment_info: dict[str, Any],
    enrichment_plan: dict[str, Any],
    package_quality: dict[str, object],
    source_coverage: dict[str, Any],
    provenance: dict[str, Any],
    ticker: list[dict[str, Any]] | None = None,
    automatic_quality_gate: dict[str, Any] | None = None,
    render_info: dict[str, Any] | None = None,
) -> None:
    """Restore run-level metadata after every package rewrite.

    ``write_package`` deliberately rewrites the content manifest.  Automatic
    quality retry also rewrites it, so run identity and publish gates must be
    applied in one place instead of being silently lost on a replacement pass.
    """
    manifest_path = out_dir / "manifest.json"
    if not manifest_path.exists():
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    manifest["evidence_screenshots"] = screenshot_info
    manifest["content_enrichment"] = enrichment_info
    manifest["enrichment_plan"] = enrichment_plan
    manifest["package_quality"] = package_quality
    manifest["source_coverage"] = source_coverage
    manifest["provenance"] = provenance
    manifest["edition_mode"] = package_quality.get("edition_mode", "insufficient_content")
    manifest["publish_allowed"] = bool(package_quality.get("publish_allowed"))
    manifest["run_id"] = run_id
    if ticker is not None:
        manifest["ticker"] = ticker
    manifest["run_state"] = str(out_dir / "run-state.json")
    if automatic_quality_gate is not None:
        manifest["automatic_quality_gate"] = automatic_quality_gate
    if render_info is not None:
        manifest["render"] = render_info
    manifest_tmp = manifest_path.with_name(manifest_path.name + ".tmp")
    manifest_tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(manifest_tmp, manifest_path)


def _run_pipeline_unlocked(
    date: str = "today",
    target: str = "bilibili",
    quality: str = "1080p",
    config_path: Path | None = None,
    db_path: Path | None = None,
    runs_dir: Path | None = None,
    lookback_hours: int | None = None,
    max_items: int | None = None,
    workers: int = 8,
    skip_render: bool = False,
    capture_evidence_screenshots: bool = False,
    prepare_review: bool = False,
    force_review: bool = False,
) -> RunResult:
    run_date = resolve_run_date(date)
    cfg = load_config(config_path)
    defaults = cfg.get("defaults", {}) or {}
    lookback = int(lookback_hours or defaults.get("lookback_hours", 24))
    category_limits = defaults.get("selection_category_limits", {}) or {}
    min_score = int(defaults.get("selection_quality_min_score", 0) or 0)
    strict_auto = bool(defaults.get("selection_strict_auto", False))
    configured_max_items = int(defaults.get("selection_max_items", 0) or 0)
    effective_max_items = configured_max_items if max_items is None else int(max_items)
    minimum_publish_items = max(1, int(defaults.get("selection_min_publish_items", 1) or 1))
    enrichment_max_items = int(defaults.get("content_enrichment_max_items", 0) or 0)
    enrichment_per_domain_limit = int(defaults.get("content_enrichment_per_domain_limit", 12) or 0)
    capture_enrichment_assets = bool(defaults.get("content_enrichment_capture_assets", False))
    use_crawl4ai = bool(defaults.get("content_enrichment_use_crawl4ai", True))
    enrichment_planning_enabled = bool(defaults.get("content_enrichment_cluster_first", True))
    enrichment_candidate_multiplier = max(1, int(defaults.get("content_enrichment_candidate_multiplier", 5) or 5))
    enrichment_candidate_entity_limit = int(defaults.get("content_enrichment_candidate_max_per_entity", 8) or 0)
    enrichment_candidate_source_limit = int(defaults.get("content_enrichment_candidate_max_per_source", 8) or 0)
    # Runtime repair can mutate the crawler environment, so it stays opt-in.
    # Copy-quality recovery is independent and defaults on for unattended runs.
    enrichment_runtime_repair = bool(defaults.get("content_enrichment_auto_runtime_repair", False))
    reconciliation_max_items = max(0, int(defaults.get("official_reconciliation_max_items", 12) or 0))
    quality_auto_repair = bool(defaults.get("automatic_quality_repair", defaults.get("auto_repair", True)))
    ticker_enabled = bool(defaults.get("ticker_enabled", False))
    ticker_max_items = max(0, int(defaults.get("ticker_max_items", 12) or 0))
    user_agent = str(defaults.get("user_agent", "DailyBilibiliBriefing/0.1 (+local)"))
    sources = load_sources(config_path, include_disabled=True)
    enabled_sources = [s for s in sources if s.enabled]
    provenance = _run_provenance(config_path)

    db_path = (db_path or default_db_path()).resolve()
    runs_dir = (runs_dir or default_runs_dir()).resolve()
    out_dir = ensure_dir(runs_dir / run_date)
    run_id = f"{run_date}-{uuid.uuid4().hex[:10]}"
    run_state = RunState(out_dir / "run-state.json", run_id, run_date)

    conn = connect(db_path)
    try:
        upsert_sources(conn, sources)
        run_state.start("COLLECT", input_count=len(enabled_sources), fingerprint_input=[s.id for s in enabled_sources])
        health, fetched = collect_sources(enabled_sources, user_agent=user_agent, workers=workers, lookback_hours=lookback)
        health.extend(_coverage_gap_health(sources))
        source_coverage = _source_coverage(sources, health)
        run_state.finish("COLLECT", output_count=len(fetched), metrics={"healthy_sources": sum(1 for row in health if row.status in {"ok", "ok_web_pending_adapter"})})
        for h in health:
            insert_health(conn, h)
        inserted = insert_items(conn, fetched)

        window_end = verify_module.now_utc()
        window_start = window_end - timedelta(hours=lookback)
        recent_items = _items_from_enabled_sources(
            load_recent_items(conn, since=window_start, limit=1500),
            enabled_sources,
        )
        def persist_document(item, article) -> None:
            document, excerpts = build_document(item, article)
            save_document(conn, document, excerpts)
            item.raw = dict(item.raw or {})
            item.raw["document_id"] = document.document_id
            item.raw["document_content_hash"] = document.content_hash
            item.raw["document_quality_score"] = document.quality_score
            item.raw["evidence_excerpts"] = [
                {"excerpt_id": excerpt.excerpt_id, "text": excerpt.text, "score": excerpt.score}
                for excerpt in excerpts[:8]
            ]

        enrichable_items = [item for item in recent_items if should_enrich_item(item)]
        enrichment_plan: dict[str, Any] = {
            "mode": "all_eligible_candidates",
            "input_items": len(recent_items),
            "eligible_items": len(enrichable_items),
            "selected_items": len(enrichable_items),
            "budget_unit": "candidate_urls",
        }
        enrichment_candidates = enrichable_items
        if enrichment_planning_enabled and enrichment_max_items > 0:
            run_state.start(
                "ENRICH_PLAN",
                input_count=len(enrichable_items),
                fingerprint_input={
                    "max_items": enrichment_max_items,
                    "multiplier": enrichment_candidate_multiplier,
                    "per_domain": enrichment_per_domain_limit,
                    "per_entity": enrichment_candidate_entity_limit,
                    "per_source": enrichment_candidate_source_limit,
                },
            )
            enrichment_candidates, enrichment_plan = plan_enrichment_items(
                enrichable_items,
                target_stories=int(defaults.get("selection_target_items", effective_max_items or 10) or 10),
                multiplier=enrichment_candidate_multiplier,
                max_items=enrichment_max_items,
                max_per_entity=enrichment_candidate_entity_limit,
                max_per_source=enrichment_candidate_source_limit,
                max_per_domain=enrichment_per_domain_limit,
                max_per_high_volume_source=1 if enrichment_max_items <= 40 else 0,
                now=window_end,
            )
            run_state.finish("ENRICH_PLAN", output_count=len(enrichment_candidates), metrics=enrichment_plan)
        run_state.start(
            "ENRICH",
            input_count=len(enrichment_candidates),
            fingerprint_input={
                "max_items": enrichment_max_items,
                "per_domain": enrichment_per_domain_limit,
                "planned": enrichment_planning_enabled,
                "runtime_repair": enrichment_runtime_repair,
            },
        )
        enrichment_info = enrich_feed_items(
            enrichment_candidates,
            # The planner already applies the crawl budget.  Keep the legacy
            # cap for unplanned/zero-limit configuration.
            max_items=0 if enrichment_planning_enabled and enrichment_max_items > 0 else enrichment_max_items,
            per_domain_limit=enrichment_per_domain_limit,
            user_agent=user_agent,
            use_crawl4ai=use_crawl4ai,
            auto_repair=enrichment_runtime_repair,
            asset_dir=out_dir / "source-assets" if capture_enrichment_assets else None,
            capture_screenshots=capture_enrichment_assets,
            document_sink=persist_document,
            progress_sink=lambda metrics: run_state.progress("ENRICH", metrics),
        )
        enrichment_info = dict(enrichment_info)
        enrichment_info["planner_selected_items"] = len(enrichment_candidates)
        enrichment_info["eligible_items"] = len(enrichable_items)
        run_state.finish("ENRICH", output_count=int(enrichment_info.get("ok", 0)), metrics=enrichment_info)
        run_state.start(
            "RECONCILE",
            input_count=len(enrichment_candidates),
            fingerprint_input={"max_items": reconciliation_max_items, "strategy": "outbound_first_party"},
        )
        reconciliation_info = reconcile_official_sources(
            recent_items,
            sources,
            user_agent=user_agent,
            lookback_hours=lookback,
            max_items=reconciliation_max_items,
            now=window_end,
        )
        enrichment_info["official_reconciliation"] = reconciliation_info
        run_state.finish("RECONCILE", output_count=int(reconciliation_info.get("matched", 0)), metrics=reconciliation_info)
        run_state.start("VERIFY", input_count=len(recent_items), fingerprint_input={"lookback_hours": lookback})
        clusters = cluster_items(recent_items)
        cards = verify_clusters(clusters, lookback_hours=lookback)
        # GitHub API 已生成中文变更摘要时，把同仓 compare URL 补回证据合同；
        # 后续 strict_auto 仍按可渲染、可核验 claim 逐条判断，不降低英文门槛。
        github_compare_evidence = attach_github_compare_evidence(cards)
        run_state.finish(
            "VERIFY",
            output_count=len(cards),
            metrics={
                "clusters": len(clusters),
                "eligible_cards": sum(1 for card in cards if card.selected),
                "github_compare_evidence": github_compare_evidence,
            },
        )
        story_history = load_story_history(conn, before_run_date=run_date)
        screenshot_info: dict[str, int | str] = {"status": "skipped"}
        if not skip_render or capture_evidence_screenshots or prepare_review:
            screenshot_info = {
                "status": "ok",
                "rounds": 0,
                "required_cards": 0,
                "attempted": 0,
                "captured": 0,
                "skipped": 0,
                "failed": 0,
            }
            seen_portfolios: set[tuple[str, ...]] = set()
            for _round in range(3):
                provisional = select_card_portfolio(
                    cards,
                    max_items=effective_max_items,
                    category_limits=category_limits,
                    min_score=min_score,
                    strict_auto=strict_auto,
                    story_history=story_history,
                ).items
                portfolio_key = tuple(card.cluster_key for card in provisional)
                if not provisional or portfolio_key in seen_portfolios:
                    break
                seen_portfolios.add(portfolio_key)
                round_info = attach_evidence_screenshots(
                    out_dir,
                    cards,
                    max_items=effective_max_items,
                    category_limits=category_limits,
                    min_score=min_score,
                    strict_auto=strict_auto,
                    selected_override=provisional,
                )
                screenshot_info["rounds"] = int(screenshot_info["rounds"]) + 1
                screenshot_info["status"] = str(round_info.get("status") or screenshot_info["status"])
                screenshot_info["required_cards"] = max(
                    int(screenshot_info["required_cards"]), int(round_info.get("required_cards") or 0)
                )
                for key in ["attempted", "captured", "skipped", "failed"]:
                    screenshot_info[key] = int(screenshot_info[key]) + int(round_info.get(key) or 0)
        # Enrichment mutates in-memory FeedItems after they have already been
        # inserted. Persist the enriched summary/raw payload so reruns and
        # diagnostics observe the same facts used by this run.
        insert_items(conn, recent_items)
        save_evidence_cards(conn, run_date, cards)
    except Exception as exc:
        active_stage = next((stage.name for stage in reversed(run_state.stages) if stage.status == "RUNNING"), "PIPELINE")
        run_state.fail(active_stage, exc)
        raise
    finally:
        conn.close()

    run_state.start("EDIT", input_count=len(cards), fingerprint_input={"max_items": effective_max_items, "min_score": min_score, "strict_auto": strict_auto})
    selection_portfolio = select_card_portfolio(
        cards,
        max_items=effective_max_items,
        category_limits=category_limits,
        min_score=min_score,
        strict_auto=strict_auto,
        story_history=story_history,
    )
    selected_cards = selection_portfolio.items
    cleanup_info = prune_evidence_screenshots(out_dir, selected_cards)
    screenshot_info["cleanup_status"] = str(cleanup_info.get("status") or "")
    screenshot_info["cleanup_kept"] = int(cleanup_info.get("kept") or 0)
    screenshot_info["cleanup_removed"] = int(cleanup_info.get("removed") or 0)
    screenshot_info["cleanup_failed"] = int(cleanup_info.get("failed") or 0)
    try:
        pkg = write_package(
            out_dir,
            run_date,
            cards,
            health,
            quality=quality,
            max_items=effective_max_items,
            lookback_hours=lookback,
            window_start=window_start,
            window_end=window_end,
            category_limits=category_limits,
            min_score=min_score,
            strict_auto=strict_auto,
            selected_override=selected_cards,
            story_history=story_history,
            selection_diagnostics=selection_portfolio.diagnostics,
        )
    except Exception as exc:
        run_state.fail("EDIT", exc)
        raise
    render_status = "skipped"
    quality_ok: bool | None = None
    package_quality = _package_quality(
        len(selected_cards),
        minimum_publish_items,
        selected_cards,
        source_coverage=source_coverage,
    )
    run_state.finish("EDIT", output_count=len(selected_cards), metrics={"package_quality": package_quality, "duration_seconds": int(pkg.get("duration_seconds", 0))})
    _update_manifest_metadata(
        out_dir,
        run_id=run_id,
        screenshot_info=dict(screenshot_info),
        enrichment_info=enrichment_info,
        enrichment_plan=enrichment_plan,
        package_quality=package_quality,
        source_coverage=source_coverage,
        provenance=provenance,
    )
    if prepare_review:
        from .review import create_review_package

        create_review_package(out_dir, cards=selected_cards, force=force_review)
    if not skip_render and not package_quality["ok"]:
        render_status = "insufficient_content"
        quality_ok = False
        run_state.complete("INSUFFICIENT_CONTENT")
    elif not skip_render:
        run_state.start("RENDER", input_count=len(selected_cards), fingerprint_input=[card.cluster_key for card in selected_cards])
        ticker_cards = _ticker_candidates(cards, selected_cards, ticker_max_items) if ticker_enabled else []
        render_info: dict[str, object] = {}
        quality_result: dict[str, object] = {}
        quality_repairs: list[dict[str, object]] = []
        auto_excluded_cluster_keys: set[str] = set()
        max_quality_attempts = max(1, min(8, effective_max_items if effective_max_items > 0 else len(selected_cards)))
        for attempt in range(1, max_quality_attempts + 1):
            try:
                render_info = render_briefing_video(
                    out_dir,
                    selected_cards,
                    quality=quality,
                    fallback_duration=int(pkg.get("duration_seconds", 60)),
                    prefer_reviewed_manuscript=False,
                    ticker_cards=ticker_cards,
                )
            except Exception as exc:
                run_state.fail("RENDER", exc)
                raise
            render_status = str(render_info.get("status", "unknown"))
            rendered_paths = [str(render_info.get(key) or "").strip() for key in ["video", "cover", "subtitles"]]
            rendered_ok = render_status == "ok" and all(path and Path(path).exists() and Path(path).stat().st_size > 0 for path in rendered_paths)
            if not rendered_ok:
                quality_ok = False
                break

            refresh_bilibili_timeline_from_script(out_dir, selected_cards, quality, run_date)
            # This is the same strict verifier used by preflight, but the
            # current run has not yet transitioned from RENDER to QA_PASSED.
            quality_result = verify_run_dir(out_dir, allow_in_progress=True)
            quality_ok = bool(quality_result.get("ok"))
            (out_dir / f"auto-quality-attempt-{attempt}.json").write_text(
                json.dumps(quality_result, ensure_ascii=False, indent=2), encoding="utf-8-sig"
            )
            if quality_ok:
                break

            excluded_positions = repairable_news_positions(out_dir, list(quality_result.get("errors") or [])) if quality_auto_repair else []
            excluded_cards = [selected_cards[position - 1] for position in excluded_positions if 0 < position <= len(selected_cards)]
            for card in excluded_cards:
                auto_excluded_cluster_keys.update(_card_exclusion_keys(card))
            next_portfolio = select_card_portfolio(
                [card for card in cards if card.cluster_key not in auto_excluded_cluster_keys],
                max_items=effective_max_items,
                category_limits=category_limits,
                min_score=min_score,
                strict_auto=strict_auto,
                story_history=story_history,
            )
            next_selected = next_portfolio.items
            if not excluded_cards or not next_selected:
                render_status = "quality_failed"
                break

            quality_repairs.append(
                {
                    "attempt": attempt,
                    "excluded_positions": excluded_positions,
                    "excluded_titles": [card.event_title for card in excluded_cards],
                    "reason": "automatic copy-quality gate rejected these rendered news segments",
                }
            )
            selected_cards = next_selected
            ticker_cards = _ticker_candidates(cards, selected_cards, ticker_max_items) if ticker_enabled else []
            cleanup_info = prune_evidence_screenshots(out_dir, selected_cards)
            screenshot_info["cleanup_status"] = str(cleanup_info.get("status") or "")
            screenshot_info["cleanup_kept"] = int(cleanup_info.get("kept") or 0)
            screenshot_info["cleanup_removed"] = int(screenshot_info.get("cleanup_removed") or 0) + int(
                cleanup_info.get("removed") or 0
            )
            screenshot_info["cleanup_failed"] = int(screenshot_info.get("cleanup_failed") or 0) + int(
                cleanup_info.get("failed") or 0
            )
            package_quality = _package_quality(
                len(selected_cards),
                minimum_publish_items,
                selected_cards,
                source_coverage=source_coverage,
            )
            pkg = write_package(
                out_dir,
                run_date,
                cards,
                health,
                quality=quality,
                max_items=effective_max_items,
                lookback_hours=lookback,
                window_start=window_start,
                window_end=window_end,
                category_limits=category_limits,
                min_score=min_score,
                strict_auto=strict_auto,
                selected_override=selected_cards,
                story_history=story_history,
                selection_diagnostics=next_portfolio.diagnostics,
            )
            _update_manifest_metadata(
                out_dir,
                run_id=run_id,
                screenshot_info=dict(screenshot_info),
                enrichment_info=enrichment_info,
                enrichment_plan=enrichment_plan,
                package_quality=package_quality,
                source_coverage=source_coverage,
                provenance=provenance,
            )
            if not package_quality["ok"]:
                render_status = "insufficient_content_after_quality_repair"
                quality_ok = False
                break
        if quality_ok is not None:
            (out_dir / "auto-quality.json").write_text(json.dumps(quality_result, ensure_ascii=False, indent=2), encoding="utf-8-sig")
        if quality_ok is False and render_status == "ok":
            render_status = "quality_failed"
        automatic_quality_gate = {
            "ok": quality_ok,
            "report": str(out_dir / "auto-quality.json") if quality_ok is not None else "",
            "attempts": len(quality_repairs) + (1 if render_info else 0),
            "automatic_exclusions": quality_repairs,
            "automatic_retry_enabled": quality_auto_repair,
        }
        _update_manifest_metadata(
            out_dir,
            run_id=run_id,
            screenshot_info=dict(screenshot_info),
            enrichment_info=enrichment_info,
            enrichment_plan=enrichment_plan,
            package_quality=package_quality,
            source_coverage=source_coverage,
            provenance=provenance,
            ticker=[
                {
                    "cluster_key": card.cluster_key,
                    "entity": card.entity,
                    "title": card.event_title,
                    "score": card.score,
                }
                for card in ticker_cards
            ],
            automatic_quality_gate=automatic_quality_gate,
            render_info=render_info,
        )
        run_state.finish("RENDER", output_count=len(selected_cards), metrics={"render_status": render_status, "quality_ok": quality_ok})
        run_state.complete("QA_PASSED" if quality_ok else "RENDER_FAILED")
    else:
        run_state.complete("EDITED")

    return RunResult(
        run_date=run_date,
        out_dir=out_dir,
        sources_count=len(enabled_sources),
        fetched_items=len(fetched),
        inserted_items=inserted,
        clusters_count=len(clusters),
        cards_count=len(cards),
        selected_count=int(pkg.get("selected_count", 0)),
        render_status=render_status,
        selected_cards=selected_cards,
        quality_ok=quality_ok,
    )


def run_pipeline(*args: Any, **kwargs: Any) -> RunResult:
    """Serialize manual and scheduled runs for the same briefing date."""
    date_value = kwargs.get("date", args[0] if args else "today")
    run_date = resolve_run_date(str(date_value))
    positional_runs_dir = args[5] if len(args) > 5 else None
    runs_dir = Path(kwargs.get("runs_dir") or positional_runs_dir or default_runs_dir()).resolve()
    from .run_lock import PipelineLock

    with PipelineLock(runs_dir / ".locks" / f"{run_date}.lock"):
        return _run_pipeline_unlocked(*args, **kwargs)

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .core import RunState, default_runs_dir, ensure_dir, prune_runs, read_json, write_json_atomic
from .ingest import collect_x_candidates
from .pipeline import append_morning_stories, execute_render_gate, run_pipeline
from .publish import (
    audit_web_sources,
    automation_status,
    check_bilibili_auth,
    preflight_bilibili,
    publish_bilibili,
    runtime_doctor,
)
from .render import render_briefing_video, repurpose_run, write_manuscript
from .review import review_final_script_path, serve_review, state_to_final_script, write_final_script_from_state
from .verify import (
    build_release_attestation,
    finalize_agent_edits,
    verify_agent_edit_contract,
    verify_run_dir,
    verify_visual_agent_audit,
)


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _add_common_pipeline_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--date", default="today", help="YYYY-MM-DD or today")
    parser.add_argument("--quality", default="1080p", choices=["1080p", "4k"], help="target video resolution")
    parser.add_argument("--config", type=Path, default=None, help="path to sources.yaml or derived edition json")
    parser.add_argument("--db", type=Path, default=None, help="path to sqlite database")
    parser.add_argument("--runs-dir", type=Path, default=None, help="output runs directory")
    parser.add_argument("--lookback-hours", type=int, default=None, help="freshness window in hours")
    parser.add_argument("--max-items", type=int, default=None, help="max selected stories (0 = unlimited)")
    parser.add_argument("--workers", type=int, default=8, help="parallel source fetch workers")


def _render_fixture(fixture_path: Path, out_dir: Path, *, quality: str = "1080p", tts: str = "edge") -> dict[str, Any]:
    """Render an offline/design sample JSON without generating publication attestations."""
    payload = read_json(fixture_path)
    if not isinstance(payload, dict) or not isinstance(payload.get("segments"), list):
        raise ValueError(f"fixture must contain a segments array: {fixture_path}")
    out_dir = ensure_dir(out_dir.resolve())
    write_manuscript(out_dir, list(payload["segments"]))
    if isinstance(payload.get("cover"), dict):
        ensure_dir(out_dir / "review")
        write_json_atomic(out_dir / "review" / "cover.json", payload["cover"])
    resolved_date = str(payload.get("run_date") or payload.get("edition_date") or out_dir.name)
    write_json_atomic(
        out_dir / "manifest.json",
        {
            "run_id": f"fixture-{out_dir.name}",
            "run_date": resolved_date,
            "qa_fixture": True,
            "publish_allowed": False,
            "selected": [],
        },
    )
    render_info = render_briefing_video(
        out_dir,
        None,
        run_date=resolved_date,
        quality=quality,
        tts_backend=tts,
    )
    return {"ok": render_info.get("status") == "ok", "qa_fixture": True, "out_dir": str(out_dir), "render": render_info}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m briefing",
        description="Evidence-first AI Daily Briefing video pipeline",
    )
    sub = parser.add_subparsers(dest="command")

    # 1. run — end-to-end daily pipeline
    run_cmd = sub.add_parser("run", help="run end-to-end collect -> verify -> render -> QA pipeline")
    _add_common_pipeline_args(run_cmd)
    run_cmd.add_argument("--tts", default="edge", choices=["edge", "indextts2", "silent"], help="TTS synthesis backend")
    run_cmd.add_argument("--profile", default="", help="IndexTTS2 authorized voice profile id")
    run_cmd.add_argument("--repurpose", action="store_true", help="also export 9:16 vertical video and WeChat draft")
    run_cmd.add_argument("--publish", action="store_true", help="upload to Bilibili after passing all gates")
    run_cmd.add_argument("--tid", type=int, default=None, help="Bilibili archive type id")
    run_cmd.add_argument("--schedule-at", default=None, help="Bilibili scheduled publish time")

    # 2. prepare — collect/verify and stage draft, human review, or agent work order
    prep_cmd = sub.add_parser("prepare", help="collect, verify, and prepare draft/review/agent workspace")
    _add_common_pipeline_args(prep_cmd)
    prep_cmd.add_argument(
        "--mode",
        default="review",
        choices=["review", "agent", "draft"],
        help="workspace target: human review app, bounded agent audit, or manifest draft only",
    )
    prep_cmd.add_argument("--append-morning", action="store_true", help="append fresh stories onto existing reviewed script")
    prep_cmd.add_argument("--max-new-items", type=int, default=3, help="max stories added with --append-morning")
    prep_cmd.add_argument("--force", action="store_true", help="overwrite existing editable review state")

    # 3. review — serve local web workbench or finalize reviewed manuscript
    rev_cmd = sub.add_parser("review", help="launch the local review web workbench or finalize edits")
    rev_cmd.add_argument("--run-dir", type=Path, required=True, help="runs/YYYY-MM-DD directory")
    rev_cmd.add_argument("--host", default="127.0.0.1", help="bind host (loopback by default)")
    rev_cmd.add_argument("--port", type=int, default=8765, help="HTTP server port")
    rev_cmd.add_argument("--finalize", action="store_true", help="validate & freeze state.json / agent-audit.json without serving UI")

    # 4. render — unified Remotion + TTS + QA gate renderer
    ren_cmd = sub.add_parser("render", help="render reviewed/draft manuscript or design fixture via Remotion")
    ren_cmd.add_argument("--run-dir", type=Path, required=True, help="target runs/YYYY-MM-DD directory")
    ren_cmd.add_argument("--quality", default="1080p", choices=["1080p", "4k"], help="video resolution")
    ren_cmd.add_argument("--tts", default="edge", choices=["edge", "indextts2", "silent"], help="TTS backend")
    ren_cmd.add_argument("--profile", default="", help="IndexTTS2 voice profile id")
    ren_cmd.add_argument("--fallback-duration", type=float, default=8.0, help="per-segment fallback seconds")
    ren_cmd.add_argument("--visual-qa", action="store_true", help="extract frame contact sheets for visual QA")
    ren_cmd.add_argument("--require-clean", action="store_true", help="require clean Git checkout & sign release attestation")
    ren_cmd.add_argument("--allow-draft", action="store_true", help="allow rendering news-script.json when review/final-script.json is absent")
    ren_cmd.add_argument("--fixture", type=Path, default=None, help="render a non-publishable sample JSON into --run-dir")

    # 5. verify — full post-render QA gate (+ optional visual-agent completion)
    ver_cmd = sub.add_parser("verify", help="verify all 6 output, media, provenance, and copy quality gates")
    ver_cmd.add_argument("--run-dir", type=Path, required=True, help="runs/YYYY-MM-DD directory")
    ver_cmd.add_argument("--complete-visual-qa", action="store_true", help="validate visual-agent-audit.json, close RENDER gate & sign attestation")

    # 6. publish — Bilibili preflight, auth probe, and upload
    pub_cmd = sub.add_parser("publish", help="run Bilibili preflight (default dry-run), auth probe, or live upload")
    pub_cmd.add_argument("--run-dir", type=Path, default=None, help="runs/YYYY-MM-DD directory")
    pub_cmd.add_argument("--tid", type=int, default=None, help="Bilibili archive type id")
    pub_cmd.add_argument("--schedule-at", default=None, help="scheduled publish time (Asia/Shanghai)")
    pub_cmd.add_argument("--execute", action="store_true", help="perform live upload & archive submission")
    pub_cmd.add_argument("--force", action="store_true", help="allow intentional re-upload after prior submission")
    pub_cmd.add_argument("--check-auth", action="store_true", help="probe Bilibili Open Platform token health")

    # 7. repurpose — vertical 9:16 video, WeChat draft, and disk retention
    rep_cmd = sub.add_parser("repurpose", help="export 9:16 vertical video + WeChat article, or prune old heavy media")
    rep_cmd.add_argument("--run-dir", type=Path, default=None, help="runs/YYYY-MM-DD directory to repurpose")
    rep_cmd.add_argument("--skip-vertical", action="store_true", help="skip 9:16 vertical video/cover")
    rep_cmd.add_argument("--skip-wechat", action="store_true", help="skip WeChat article Markdown")
    rep_cmd.add_argument("--prune-days", type=int, default=None, help="remove heavy media older than N days")
    rep_cmd.add_argument("--runs-dir", type=Path, default=None, help="runs directory for --prune-days")
    rep_cmd.add_argument("--execute", action="store_true", help="execute deletion when --prune-days is set")

    # 8. doctor — environment health, automation state machine, crawler audit, X fetch
    doc_cmd = sub.add_parser("doctor", help="check runtime prerequisites, automation stage, or source crawler health")
    doc_cmd.add_argument("--config", type=Path, default=None, help="path to sources.yaml")
    doc_cmd.add_argument("--status", type=Path, default=None, metavar="RUN_DIR", help="report read-only automation stage for RUN_DIR")
    doc_cmd.add_argument("--audit-sources", action="store_true", help="audit official web discovery & enrichment adapters")
    doc_cmd.add_argument("--out-dir", type=Path, default=None, help="optional report output directory")
    doc_cmd.add_argument("--collect-x", action="store_true", help="collect raw X candidates via OpenCLI for agent verification")
    doc_cmd.add_argument("--limit", type=int, default=8, help="per-account tweet limit for --collect-x")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cmd = args.command or "run"

    if cmd == "doctor":
        if args.status is not None:
            payload = automation_status(args.status, config_path=args.config)
        elif args.audit_sources:
            payload = audit_web_sources(args.config, out_dir=args.out_dir)
        elif args.collect_x:
            payload = collect_x_candidates(args.config, out_path=args.out_dir, limit_per_account=args.limit)
        else:
            payload = runtime_doctor(args.config)
        _print_json(payload)
        return 0 if payload.get("ok", True) else 1

    if cmd == "run":
        result = run_pipeline(
            date=getattr(args, "date", "today"),
            quality=getattr(args, "quality", "1080p"),
            config_path=getattr(args, "config", None),
            db_path=getattr(args, "db", None),
            runs_dir=getattr(args, "runs_dir", None),
            lookback_hours=getattr(args, "lookback_hours", None),
            max_items=getattr(args, "max_items", None),
            workers=getattr(args, "workers", 8),
            mode="auto",
            tts_backend=getattr(args, "tts", "edge"),
            tts_profile=getattr(args, "profile", ""),
        )
        summary: dict[str, Any] = {
            "ok": bool(result.quality.get("ok")),
            "run_id": result.run_id,
            "run_date": result.run_date,
            "out_dir": str(result.out_dir),
            "selected_count": len(result.selected_cards),
            "render_status": result.render_info.get("status", "unknown"),
            "quality": result.quality,
        }
        if summary["ok"] and getattr(args, "repurpose", False):
            summary["repurpose"] = repurpose_run(result.out_dir)
        if summary["ok"] and getattr(args, "publish", False):
            pub_res = publish_bilibili(
                result.out_dir,
                tid=getattr(args, "tid", None),
                schedule_at=getattr(args, "schedule_at", None),
                dry_run=False,
                verified_report=result.quality,
            )
            summary["publish"] = pub_res
            summary["ok"] = bool(pub_res.get("ok", pub_res.get("status") == "submitted"))
        _print_json(summary)
        return 0 if summary["ok"] else 1

    if cmd == "prepare":
        prep_mode = "prepare" if args.mode == "draft" else args.mode
        result = run_pipeline(
            date=args.date,
            quality=args.quality,
            config_path=args.config,
            db_path=args.db,
            runs_dir=args.runs_dir,
            lookback_hours=args.lookback_hours or (6 if args.append_morning else None),
            max_items=args.max_items,
            workers=args.workers,
            mode=prep_mode,
            force_review=args.force,
        )
        payload: dict[str, Any] = {
            "ok": True,
            "mode": args.mode,
            "run_id": result.run_id,
            "run_date": result.run_date,
            "out_dir": str(result.out_dir),
            "selected_count": len(result.selected_cards),
            "quality": result.quality,
        }
        if args.append_morning:
            payload["morning_merge"] = append_morning_stories(
                result.out_dir,
                result.selected_cards,
                max_new_items=args.max_new_items,
            )
        _print_json(payload)
        return 0

    if cmd == "review":
        if args.finalize:
            manifest = read_json(args.run_dir / "manifest.json", default={})
            agent_req = bool((manifest.get("agent_policy") or {}).get("required"))
            if agent_req or (args.run_dir / "review" / "agent-audit.json").exists():
                payload = finalize_agent_edits(args.run_dir)
            else:
                state = read_json(args.run_dir / "review" / "state.json", default=None)
                if not isinstance(state, dict):
                    _print_json({"ok": False, "error": "missing review/state.json"})
                    return 1
                saved = write_final_script_from_state(args.run_dir, state)
                payload = {"ok": True, "segments": len(state_to_final_script(state)), **saved}
            _print_json(payload)
            return 0 if payload.get("ok") else 1
        serve_review(args.run_dir, host=args.host, port=args.port)
        return 0

    if cmd == "render":
        if args.fixture is not None:
            payload = _render_fixture(args.fixture, args.run_dir, quality=args.quality, tts=args.tts)
            _print_json(payload)
            return 0 if payload["ok"] else 1

        manifest = read_json(args.run_dir / "manifest.json", default={})
        agent_req = bool((manifest.get("agent_policy") or {}).get("required"))
        if agent_req:
            contract_errors = verify_agent_edit_contract(args.run_dir)
            if contract_errors:
                _print_json({"ok": False, "errors": contract_errors})
                return 1
        elif not review_final_script_path(args.run_dir).exists() and not args.allow_draft:
            if not (args.run_dir / "news-script.json").exists():
                _print_json({"ok": False, "error": f"missing manuscript in {args.run_dir}"})
                return 1

        payload = execute_render_gate(
            args.run_dir,
            quality=args.quality,
            tts_backend=args.tts,
            tts_profile=args.profile,
            fallback_duration=args.fallback_duration,
            visual_qa=args.visual_qa or agent_req,
            require_clean_release=args.require_clean or agent_req,
            leave_open_for_visual_qa=agent_req,
        )
        _print_json(payload)
        return 0 if payload.get("ok") else 1

    if cmd == "verify":
        if args.complete_visual_qa:
            vis_errors = verify_visual_agent_audit(args.run_dir)
            if vis_errors:
                _print_json({"ok": False, "status": "visual_review_pending", "errors": vis_errors})
                return 1
            report = verify_run_dir(args.run_dir, allow_in_progress=True)
            write_json_atomic(args.run_dir / "auto-quality.json", report)
            ok = bool(report.get("ok"))
            manifest = read_json(args.run_dir / "manifest.json", default={})
            manifest["automatic_quality_gate"] = {
                "ok": ok,
                "status": "passed" if ok else "failed",
                "report": str(args.run_dir / "auto-quality.json"),
                "attempts": 1,
                "automatic_exclusions": [],
                "automatic_retry_enabled": False,
            }
            if ok and manifest.get("render_provenance"):
                manifest["release_attestation"] = build_release_attestation(
                    args.run_dir, provenance=manifest["render_provenance"]
                )
            write_json_atomic(args.run_dir / "manifest.json", manifest)
            state = RunState.load_or_create(
                args.run_dir / "run-state.json",
                str(manifest.get("run_id") or args.run_dir.name),
                str(manifest.get("run_date") or args.run_dir.name),
            )
            if ok:
                state.finish("RENDER", metrics={"render_status": "ok", "quality_ok": True})
                state.complete("QA_PASSED")
            else:
                state.fail("RENDER", "; ".join(report.get("errors") or ["visual QA verification failed"]))
                state.complete("RENDER_FAILED")
            _print_json({"ok": ok, "quality": report})
            return 0 if ok else 1

        report = verify_run_dir(args.run_dir)
        _print_json(report)
        return 0 if report.get("ok") else 1

    if cmd == "publish":
        if args.check_auth:
            auth = check_bilibili_auth()
            _print_json(auth)
            if auth.get("ok"):
                return 0
            return 2 if auth.get("reason") == "missing_credentials" else 1
        if args.run_dir is None:
            parser.error("publish requires --run-dir (unless --check-auth is used)")
        if not args.execute:
            preflight = preflight_bilibili(args.run_dir, tid=args.tid, schedule_at=args.schedule_at)
            _print_json(preflight)
            return 0 if preflight.get("ok") else 1
        result_payload = publish_bilibili(
            args.run_dir,
            tid=args.tid,
            schedule_at=args.schedule_at,
            dry_run=False,
            force=args.force,
        )
        _print_json(result_payload)
        return 0 if result_payload.get("ok", result_payload.get("status") == "submitted") else 1

    if cmd == "repurpose":
        if args.prune_days is not None:
            report = prune_runs(args.runs_dir or default_runs_dir(), args.prune_days, execute=args.execute)
            _print_json(report)
            return 0 if not report.get("errors") else 1
        if args.run_dir is None:
            parser.error("repurpose requires --run-dir (or --prune-days N)")
        report = repurpose_run(
            args.run_dir,
            include_vertical=not args.skip_vertical,
            include_wechat=not args.skip_wechat,
        )
        _print_json(report)
        return 0 if report.get("ok") else 1

    parser.error(f"unknown command: {cmd}")
    return 2

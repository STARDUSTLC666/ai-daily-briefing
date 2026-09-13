from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import json

from .agent_workflow import (
    create_agent_package,
    create_visual_qa_package,
    finalize_agent_edit,
    verify_agent_edit_contract,
    verify_visual_agent_audit,
)
from .bilibili_publish import publish_bilibili, preflight_bilibili
from .output_verify import verify_run_dir
from .pipeline import run_pipeline
from .release_attestation import build_release_attestation, capture_clean_render_provenance
from .render import render_briefing_video
from .review import append_morning_updates_to_reviewed_script, refresh_reviewed_bilibili_outputs, review_final_script_path, serve_review


def _render_is_publishable(render_info: dict) -> bool:
    if str(render_info.get("status") or "") != "ok":
        return False
    for key in ["video", "cover", "subtitles"]:
        path = Path(str(render_info.get(key) or ""))
        if not path.exists() or path.stat().st_size == 0:
            return False
    return True


def _read_json_object(path: Path, label: str) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} root must be an object")
    return payload


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _close_running_render_stage(state: dict, *, quality_ok: bool, render_info: dict, errors: list[str]) -> None:
    stages = state.get("stages")
    if not isinstance(stages, list):
        raise ValueError("run-state.json stages must be a list")
    stage = next(
        (item for item in reversed(stages) if isinstance(item, dict) and item.get("name") == "RENDER" and item.get("status") == "RUNNING"),
        None,
    )
    if stage is None:
        raise ValueError("run-state.json has no RUNNING RENDER stage")
    now = datetime.now(timezone.utc)
    try:
        started = datetime.fromisoformat(str(stage.get("started_at") or ""))
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        duration_ms = max(1, round((now - started).total_seconds() * 1000))
    except (TypeError, ValueError):
        duration_ms = 1
    stage.update(
        {
            "status": "OK" if quality_ok else "FAILED",
            "finished_at": now.isoformat(),
            "duration_ms": duration_ms,
            "metrics": {
                "render_status": str(render_info.get("status") or "unknown"),
                "quality_ok": quality_ok,
                "quality_errors": list(errors),
            },
            "error": "" if quality_ok else "post-render quality gate failed",
        }
    )
    state["status"] = "QA_PASSED" if quality_ok else "RENDER_FAILED"
    state["finished_at"] = now.isoformat()


def _force_render_failed(out_dir: Path, error: Exception | str, render_info: dict | None = None) -> None:
    """Best-effort failure persistence; never restores an earlier green gate."""
    message = f"{type(error).__name__}: {error}" if isinstance(error, Exception) else str(error)
    state_path = out_dir / "run-state.json"
    try:
        state = _read_json_object(state_path, "run-state.json")
        stages = state.get("stages")
        if isinstance(stages, list):
            running = next(
                (item for item in reversed(stages) if isinstance(item, dict) and item.get("name") == "RENDER" and item.get("status") == "RUNNING"),
                None,
            )
            if running is not None:
                _close_running_render_stage(
                    state,
                    quality_ok=False,
                    render_info=render_info or {"status": "error"},
                    errors=[message],
                )
            else:
                state["status"] = "RENDER_FAILED"
                state["finished_at"] = datetime.now(timezone.utc).isoformat()
        else:
            state["status"] = "RENDER_FAILED"
            state["finished_at"] = datetime.now(timezone.utc).isoformat()
        _write_json(state_path, state)
    except Exception:
        pass
    manifest_path = out_dir / "manifest.json"
    try:
        manifest = _read_json_object(manifest_path, "manifest.json")
        manifest["automatic_quality_gate"] = {
            "ok": False,
            "status": "failed",
            "report": str(out_dir / "auto-quality.json"),
            "attempts": 1,
            "automatic_exclusions": [],
            "automatic_retry_enabled": False,
            "errors": [message],
        }
        manifest.pop("release_attestation", None)
        if render_info is not None:
            manifest["render"] = render_info
        _write_json(manifest_path, manifest)
    except Exception:
        pass


def _begin_render_quality_gate(out_dir: Path, *, require_clean_release: bool = False) -> None:
    """Invalidate an old QA pass before a replacement render can start."""
    out_dir = Path(out_dir).resolve()
    state_path = out_dir / "run-state.json"
    manifest_path = out_dir / "manifest.json"
    try:
        state = _read_json_object(state_path, "run-state.json")
        stages = state.get("stages")
        if not isinstance(stages, list):
            raise ValueError("run-state.json stages must be a list")
        now = datetime.now(timezone.utc).isoformat()
        state["status"] = "RENDERING"
        state["finished_at"] = ""
        stages.append(
            {
                "name": "RENDER",
                "status": "RUNNING",
                "started_at": now,
                "finished_at": "",
                "duration_ms": 0,
                "input_count": None,
                "output_count": None,
                "fingerprint": "",
                "metrics": {},
                "error": "",
            }
        )
        _write_json(state_path, state)

        manifest = _read_json_object(manifest_path, "manifest.json")
        manifest["automatic_quality_gate"] = {
            "ok": False,
            "status": "pending",
            "report": str(out_dir / "auto-quality.json"),
            "attempts": 1,
            "automatic_exclusions": [],
            "automatic_retry_enabled": False,
        }
        manifest.pop("release_attestation", None)
        manifest.pop("render_provenance", None)
        if require_clean_release:
            manifest["render_provenance"] = capture_clean_render_provenance(out_dir, manifest)
        _write_json(manifest_path, manifest)
    except Exception as exc:
        _force_render_failed(out_dir, exc)
        raise


def _finish_render_quality_gate(out_dir: Path, render_info: dict) -> dict:
    """Persist post-render QA and close the RUNNING RENDER stage."""
    out_dir = Path(out_dir).resolve()
    try:
        rendered_ok = _render_is_publishable(render_info)
        quality_result = (
            verify_run_dir(out_dir, allow_in_progress=True)
            if rendered_ok
            else {
                "ok": False,
                "run_dir": str(out_dir),
                "errors": [str(render_info.get("error") or "render did not produce a publishable video")],
                "warnings": [],
            }
        )
        quality_ok = rendered_ok and bool(quality_result.get("ok"))
        (out_dir / "auto-quality.json").write_text(
            json.dumps(quality_result, ensure_ascii=False, indent=2), encoding="utf-8-sig"
        )

        manifest_path = out_dir / "manifest.json"
        manifest = _read_json_object(manifest_path, "manifest.json")
        manifest["render"] = render_info
        manifest["automatic_quality_gate"] = {
            "ok": quality_ok,
            "status": "passed" if quality_ok else "failed",
            "report": str(out_dir / "auto-quality.json"),
            "attempts": 1,
            "automatic_exclusions": [],
            "automatic_retry_enabled": False,
        }
        if quality_ok and manifest.get("render_provenance"):
            manifest["release_attestation"] = build_release_attestation(out_dir, manifest)
        else:
            manifest.pop("release_attestation", None)
        _write_json(manifest_path, manifest)

        state_path = out_dir / "run-state.json"
        state = _read_json_object(state_path, "run-state.json")
        _close_running_render_stage(
            state,
            quality_ok=quality_ok,
            render_info=render_info,
            errors=list(quality_result.get("errors") or []),
        )
        _write_json(state_path, state)
        return quality_result
    except Exception as exc:
        _force_render_failed(out_dir, exc, render_info)
        raise

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m briefing", description="AI daily briefing and Bilibili package generator")
    sub = p.add_subparsers(dest="command")

    doctor = sub.add_parser('doctor', help='check the project runtime and production prerequisites')
    doctor.add_argument('--config', type=Path, default=None)
    automation = sub.add_parser('automation-status', help='find the next safe step of an agent-audited daily run')
    automation.add_argument('--run-dir', type=Path, required=True)
    automation.add_argument('--config', type=Path, default=None)

    run = sub.add_parser("run", help="run full collect -> verify -> package pipeline")
    run.add_argument("--date", default="today", help="YYYY-MM-DD or today")
    run.add_argument("--target", default="bilibili", choices=["bilibili"], help="output target")
    run.add_argument("--quality", default="1080p", choices=["1080p", "4k"], help="placeholder render quality")
    run.add_argument("--config", type=Path, default=None, help="path to sources.yaml")
    run.add_argument("--db", type=Path, default=None, help="path to sqlite db")
    run.add_argument("--runs-dir", type=Path, default=None, help="output runs directory")
    run.add_argument("--lookback-hours", type=int, default=None, help="news freshness window")
    run.add_argument("--max-items", type=int, default=None, help="max selected cards; omit to use config default, 0 means unlimited")
    run.add_argument("--workers", type=int, default=8, help="parallel source fetch workers")
    run.add_argument("--skip-render", action="store_true", help="skip placeholder cover/video rendering")

    prep = sub.add_parser("prepare-review", help="collect/enrich news and prepare a local review desk without rendering")
    prep.add_argument("--date", default="today", help="YYYY-MM-DD or today")
    prep.add_argument("--target", default="bilibili", choices=["bilibili"], help="output target")
    prep.add_argument("--quality", default="1080p", choices=["1080p", "4k"], help="render quality metadata")
    prep.add_argument("--config", type=Path, default=None, help="path to sources.yaml")
    prep.add_argument("--db", type=Path, default=None, help="path to sqlite db")
    prep.add_argument("--runs-dir", type=Path, default=None, help="output runs directory")
    prep.add_argument("--lookback-hours", type=int, default=None, help="news freshness window")
    prep.add_argument("--max-items", type=int, default=None, help="max selected cards; omit to use config default")
    prep.add_argument("--workers", type=int, default=8, help="parallel source fetch workers")
    prep.add_argument("--force", action="store_true", help="overwrite existing review/state.json with a fresh draft")

    agent_prep = sub.add_parser("prepare-agent", help="prepare a bounded source/copy work order for a Codex automation")
    agent_prep.add_argument("--date", default="today", help="YYYY-MM-DD or today")
    agent_prep.add_argument("--target", default="bilibili", choices=["bilibili"], help="output target")
    agent_prep.add_argument("--quality", default="1080p", choices=["1080p", "4k"], help="render quality metadata")
    agent_prep.add_argument("--config", type=Path, default=None, help="path to sources.yaml")
    agent_prep.add_argument("--db", type=Path, default=None, help="path to sqlite db")
    agent_prep.add_argument("--runs-dir", type=Path, default=None, help="output runs directory")
    agent_prep.add_argument("--lookback-hours", type=int, default=None, help="news freshness window")
    agent_prep.add_argument("--max-items", type=int, default=None, help="max selected cards; omit to use config default")
    agent_prep.add_argument("--workers", type=int, default=8, help="parallel source fetch workers")
    agent_prep.add_argument("--force", action="store_true", help="overwrite the current agent draft")

    agent_finalize = sub.add_parser("finalize-agent", help="validate Codex source/copy audit and freeze the reviewed manuscript")
    agent_finalize.add_argument("--run-dir", type=Path, required=True, help="runs/YYYY-MM-DD directory")

    agent_render = sub.add_parser("render-agent", help="render the validated Codex manuscript and prepare visual QA frames")
    agent_render.add_argument("--run-dir", type=Path, required=True, help="runs/YYYY-MM-DD directory")
    agent_render.add_argument("--quality", default="1080p", choices=["1080p", "4k"], help="render quality")
    agent_render.add_argument("--fallback-duration", type=int, default=60, help="fallback duration when TTS is unavailable")

    agent_complete = sub.add_parser("complete-agent-run", help="validate Codex visual audit and close the automatic quality gate")
    agent_complete.add_argument("--run-dir", type=Path, required=True, help="runs/YYYY-MM-DD directory")

    review = sub.add_parser("review", help="serve the local review desk for an existing run")
    review.add_argument("--run-dir", type=Path, required=True, help="runs/YYYY-MM-DD directory")
    review.add_argument("--host", default="127.0.0.1", help="bind host; keep 127.0.0.1 for local use")
    review.add_argument("--port", type=int, default=8765, help="local review server port")

    reviewed = sub.add_parser("render-reviewed", help="render an existing run using review/final-script.json")
    reviewed.add_argument("--run-dir", type=Path, required=True, help="runs/YYYY-MM-DD directory")
    reviewed.add_argument("--quality", default="1080p", choices=["1080p", "4k"], help="render quality")
    reviewed.add_argument("--fallback-duration", type=int, default=60, help="fallback duration when TTS is unavailable")
    reviewed.add_argument("--allow-draft", action="store_true", help="allow rendering without review/final-script.json")

    morning = sub.add_parser("morning-render", help="at 06:00, collect the last 6 hours, append new items to the reviewed script, then render")
    morning.add_argument("--date", default="today", help="YYYY-MM-DD or today")
    morning.add_argument("--target", default="bilibili", choices=["bilibili"], help="output target")
    morning.add_argument("--quality", default="1080p", choices=["1080p", "4k"], help="render quality")
    morning.add_argument("--config", type=Path, default=None, help="path to sources.yaml")
    morning.add_argument("--db", type=Path, default=None, help="path to sqlite db")
    morning.add_argument("--runs-dir", type=Path, default=None, help="output runs directory")
    morning.add_argument("--lookback-hours", type=int, default=6, help="second-pass freshness window")
    morning.add_argument("--max-items", type=int, default=None, help="max candidates in the 6-hour pass")
    morning.add_argument("--max-new-items", type=int, default=3, help="max new stories appended to the frozen morning script")
    morning.add_argument("--workers", type=int, default=8, help="parallel source fetch workers")
    morning.add_argument("--fallback-duration", type=int, default=60, help="fallback duration when TTS is unavailable")

    audit = sub.add_parser("audit-sources", help="run source collection and health report only")
    audit.add_argument("--date", default="today")
    audit.add_argument("--config", type=Path, default=None)
    audit.add_argument("--db", type=Path, default=None)
    audit.add_argument("--runs-dir", type=Path, default=None)
    audit.add_argument("--workers", type=int, default=8)

    verify = sub.add_parser("verify-run", help="verify an existing run output directory")
    verify.add_argument("--run-dir", type=Path, required=True, help="runs/YYYY-MM-DD directory")

    bili = sub.add_parser("bilibili-preflight", help="check whether a run package is ready for Bilibili upload")
    bili.add_argument("--run-dir", type=Path, required=True, help="runs/YYYY-MM-DD directory")
    bili.add_argument("--tid", type=int, default=None, help="Bilibili archive type id override")
    bili.add_argument("--schedule-at", default=None, help="scheduled publish time, e.g. 08:00 or 2026-07-06 08:00")

    pub = sub.add_parser("bilibili-publish", help="upload a run package to Bilibili; dry-run by default")
    pub.add_argument("--run-dir", type=Path, required=True, help="runs/YYYY-MM-DD directory")
    pub.add_argument("--tid", type=int, default=None, help="Bilibili archive type id override")
    pub.add_argument("--schedule-at", default=None, help="scheduled publish time, e.g. 08:00 or 2026-07-06 08:00")
    pub.add_argument("--execute", action="store_true", help="actually upload and submit; without this only dry-runs")
    pub.add_argument("--force", action="store_true", help="allow an intentional resubmission after a recorded successful upload")

    auth = sub.add_parser("bilibili-check-auth", help="probe the Bilibili open-platform token with one cheap authenticated call")
    auth.add_argument("--json", action="store_true", help="kept for compatibility; output is always JSON")

    prune = sub.add_parser("prune-runs", help="reclaim disk by removing heavy media from old run directories (dry-run by default)")
    prune.add_argument("--runs-dir", type=Path, default=None, help="runs directory; defaults to the repository runs/")
    prune.add_argument("--keep-days", type=int, required=True, help="protect runs from the last N days")
    prune.add_argument("--execute", action="store_true", help="actually delete; without this only report what would be reclaimed")

    repurp = sub.add_parser("repurpose", help="derive vertical video, vertical cover, and a WeChat article draft from a finished run")
    repurp.add_argument("--run-dir", type=Path, required=True, help="runs/YYYY-MM-DD directory")
    repurp.add_argument("--skip-vertical", action="store_true", help="skip the 9:16 video/cover")
    repurp.add_argument("--skip-wechat", action="store_true", help="skip the WeChat article draft")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in {'doctor', 'automation-status'}:
        from .automation import automation_status, runtime_doctor
        payload = runtime_doctor(args.config) if args.command == 'doctor' else automation_status(args.run_dir, config=args.config)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload['ok'] else 1
    if args.command in {None, "run"}:
        result = run_pipeline(
            date=getattr(args, "date", "today"),
            target=getattr(args, "target", "bilibili"),
            quality=getattr(args, "quality", "1080p"),
            config_path=getattr(args, "config", None),
            db_path=getattr(args, "db", None),
            runs_dir=getattr(args, "runs_dir", None),
            lookback_hours=getattr(args, "lookback_hours", None),
            max_items=getattr(args, "max_items", None),
            workers=getattr(args, "workers", 8),
            skip_render=getattr(args, "skip_render", False),
        )
    elif args.command == "prepare-review":
        result = run_pipeline(
            date=args.date,
            target=args.target,
            quality=args.quality,
            config_path=args.config,
            db_path=args.db,
            runs_dir=args.runs_dir,
            lookback_hours=args.lookback_hours,
            max_items=args.max_items,
            workers=args.workers,
            skip_render=True,
            capture_evidence_screenshots=True,
            prepare_review=True,
            force_review=args.force,
        )
    elif args.command == "prepare-agent":
        result = run_pipeline(
            date=args.date,
            target=args.target,
            quality=args.quality,
            config_path=args.config,
            db_path=args.db,
            runs_dir=args.runs_dir,
            lookback_hours=args.lookback_hours,
            max_items=args.max_items,
            workers=args.workers,
            skip_render=True,
            capture_evidence_screenshots=True,
            prepare_review=True,
            force_review=args.force,
        )
        agent_package = create_agent_package(result.out_dir)
    elif args.command == "audit-sources":
        result = run_pipeline(
            date=args.date,
            config_path=args.config,
            db_path=args.db,
            runs_dir=args.runs_dir,
            workers=args.workers,
            skip_render=True,
            max_items=0,
        )
    elif args.command == "review":
        serve_review(args.run_dir, host=args.host, port=args.port)
        return 0
    elif args.command == "render-reviewed":
        final_script = review_final_script_path(args.run_dir)
        if not final_script.exists() and not args.allow_draft:
            print(json.dumps({"ok": False, "error": f"missing reviewed manuscript: {final_script}"}, ensure_ascii=False, indent=2))
            return 1
        result_payload = render_briefing_video(args.run_dir, [], quality=args.quality, fallback_duration=args.fallback_duration)
        if _render_is_publishable(result_payload):
            refresh_reviewed_bilibili_outputs(args.run_dir, quality=args.quality)
        else:
            result_payload["publish_files_skipped"] = "render did not produce a publishable video"
        print(json.dumps(result_payload, ensure_ascii=False, indent=2))
        return 0 if _render_is_publishable(result_payload) else 1
    elif args.command == "finalize-agent":
        payload = finalize_agent_edit(args.run_dir)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload.get("ok") else 1
    elif args.command == "render-agent":
        contract_errors = verify_agent_edit_contract(args.run_dir)
        if contract_errors:
            print(json.dumps({"ok": False, "errors": contract_errors}, ensure_ascii=False, indent=2))
            return 1
        try:
            _begin_render_quality_gate(args.run_dir, require_clean_release=True)
            render_info = render_briefing_video(
                args.run_dir,
                [],
                quality=args.quality,
                fallback_duration=args.fallback_duration,
            )
            if not _render_is_publishable(render_info):
                raise RuntimeError(str(render_info.get("error") or "render did not produce a publishable video"))
            publish_files = refresh_reviewed_bilibili_outputs(args.run_dir, quality=args.quality)
            visual_qa = create_visual_qa_package(args.run_dir)
        except Exception as exc:
            _force_render_failed(args.run_dir, exc, locals().get("render_info"))
            print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False, indent=2))
            return 1
        payload = {
            "ok": True,
            "status": "visual_review_pending",
            "render": render_info,
            "publish_files": publish_files,
            "visual_qa": visual_qa,
            "next": f"inspect cover/contact sheet, write visual-agent-audit.json, then run complete-agent-run --run-dir {args.run_dir}",
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    elif args.command == "complete-agent-run":
        visual_errors = verify_visual_agent_audit(args.run_dir)
        if visual_errors:
            print(json.dumps({"ok": False, "status": "visual_review_pending", "errors": visual_errors}, ensure_ascii=False, indent=2))
            return 1
        render_info = _read_json_object(args.run_dir / "render-info.json", "render-info.json")
        quality_result = _finish_render_quality_gate(args.run_dir, render_info)
        payload = {"ok": bool(quality_result.get("ok")), "quality": quality_result, "render": render_info}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["ok"] else 1
    elif args.command == "morning-render":
        result = run_pipeline(
            date=args.date,
            target=args.target,
            quality=args.quality,
            config_path=args.config,
            db_path=args.db,
            runs_dir=args.runs_dir,
            lookback_hours=args.lookback_hours,
            max_items=args.max_items,
            workers=args.workers,
            skip_render=True,
            capture_evidence_screenshots=True,
        )
        merge_info = append_morning_updates_to_reviewed_script(result.out_dir, result.selected_cards or [], max_new_items=args.max_new_items)
        try:
            _begin_render_quality_gate(result.out_dir)
            render_info = render_briefing_video(result.out_dir, [], quality=args.quality, fallback_duration=args.fallback_duration)
            publish_files = (
                refresh_reviewed_bilibili_outputs(result.out_dir, quality=args.quality, run_date=result.run_date)
                if _render_is_publishable(render_info)
                else {}
            )
            quality_result = _finish_render_quality_gate(result.out_dir, render_info)
        except Exception as exc:
            _force_render_failed(result.out_dir, exc, locals().get("render_info"))
            payload = {
                "ok": False,
                "run_date": result.run_date,
                "out_dir": str(result.out_dir),
                "lookback_hours": args.lookback_hours,
                "candidate_selected": result.selected_count,
                "morning_merge": merge_info,
                "error": f"{type(exc).__name__}: {exc}",
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 1
        payload = {
            "ok": bool(quality_result.get("ok")),
            "run_date": result.run_date,
            "out_dir": str(result.out_dir),
            "lookback_hours": args.lookback_hours,
            "candidate_selected": result.selected_count,
            "morning_merge": merge_info,
            "render": render_info,
            "publish_files": publish_files,
            "quality": quality_result,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["ok"] else 1
    elif args.command == "verify-run":
        result_payload = verify_run_dir(args.run_dir)
        print(json.dumps(result_payload, ensure_ascii=False, indent=2))
        return 0 if result_payload["ok"] else 1
    elif args.command == "bilibili-preflight":
        result_payload = preflight_bilibili(args.run_dir, tid=args.tid, schedule_at=args.schedule_at)
        print(json.dumps(result_payload, ensure_ascii=False, indent=2))
        return 0 if result_payload["ok"] else 1
    elif args.command == "bilibili-publish":
        result_payload = publish_bilibili(
            args.run_dir,
            dry_run=not args.execute,
            tid=args.tid,
            schedule_at=args.schedule_at,
            force=args.force,
        )
        print(json.dumps(result_payload, ensure_ascii=False, indent=2))
        if result_payload.get("dry_run"):
            return 0 if result_payload.get("preflight", {}).get("ok") else 1
        return 0
    elif args.command == "bilibili-check-auth":
        from .bilibili_publish import check_auth

        result_payload = check_auth()
        print(json.dumps(result_payload, ensure_ascii=False, indent=2))
        if result_payload.get("ok"):
            return 0
        return 2 if result_payload.get("reason") == "missing_credentials" else 1
    elif args.command == "prune-runs":
        from .config import default_runs_dir
        from .housekeeping import prune_runs

        result_payload = prune_runs(
            args.runs_dir or default_runs_dir(),
            args.keep_days,
            execute=args.execute,
        )
        print(json.dumps(result_payload, ensure_ascii=False, indent=2))
        return 0 if not result_payload.get("errors") else 1
    elif args.command == "repurpose":
        from .repurpose import repurpose_run

        result_payload = repurpose_run(
            args.run_dir,
            skip_vertical=args.skip_vertical,
            skip_wechat=args.skip_wechat,
        )
        print(json.dumps(result_payload, ensure_ascii=False, indent=2))
        return 0 if result_payload.get("ok") else 1
    else:
        parser.error(f"unknown command: {args.command}")
        return 2

    print(f"run_date={result.run_date}")
    print(f"out_dir={result.out_dir}")
    print(f"sources={result.sources_count} fetched_items={result.fetched_items} inserted_items={result.inserted_items}")
    print(f"clusters={result.clusters_count} cards={result.cards_count} selected={result.selected_count}")
    print(f"render={result.render_status}")
    if result.quality_ok is not None:
        print(f"automatic_quality_gate={'ok' if result.quality_ok else 'failed'}")
    if getattr(args, "command", "") == "prepare-review":
        print(f"review_dir={result.out_dir / 'review'}")
        print(f"review_index={result.out_dir / 'review' / 'index.html'}")
        print(f"next=py -3 -m briefing review --run-dir {result.out_dir}")
    elif getattr(args, "command", "") == "prepare-agent":
        print(f"agent_brief={agent_package['agent_brief']}")
        print(f"editable_state={agent_package['editable_state']}")
        print(f"audit_template={agent_package['audit_template']}")
        print(f"next=Codex reviews sources and copy, then runs finalize-agent --run-dir {result.out_dir}")
    return 0 if result.quality_ok is not False else 1

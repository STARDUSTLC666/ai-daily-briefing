from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from datetime import datetime, timedelta
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from briefing.cli import build_parser, main
from briefing.core import (
    UTC,
    EvidenceCard,
    FeedItem,
    RunResult,
    Source,
    SourceHealth,
    now_utc,
    read_json,
    write_json_atomic,
)
from briefing.pipeline import (
    append_morning_stories,
    compute_package_quality,
    compute_source_coverage,
    resolve_run_date,
    run_pipeline,
)
from briefing.render import build_segments
from briefing.review import create_review_package, write_final_script_from_state


def _make_x_sources() -> list[Source]:
    return [
        Source(
            id="x_codex_official_leads",
            name="X Official Leads",
            tier="A",
            type="agent_social",
            region="global",
            url="https://x.com",
            enabled=True,
            reliability="official_social",
        ),
        Source(
            id="x_codex_personnel_leads",
            name="X Personnel Leads",
            tier="C",
            type="agent_social",
            region="global",
            url="https://x.com",
            enabled=True,
            reliability="official_personnel",
        ),
        Source(
            id="x_codex_community_leads",
            name="X Community Leads",
            tier="C",
            type="agent_social",
            region="global",
            url="https://x.com",
            enabled=True,
            reliability="community",
        ),
    ]


def _make_card(
    *,
    cluster_key: str,
    title: str,
    entity: str,
    url: str,
    tier: str = "headline",
    reliability: str = "official",
    source_tier: str = "A",
    official_count: int = 1,
    community_count: int = 0,
) -> EvidenceCard:
    ts = now_utc() - timedelta(hours=2)
    fact = f"{entity} 正式发布新一代模型更新，支持 128k token 上下文窗口并降低 30% API 延迟。"
    return EvidenceCard(
        cluster_key=cluster_key,
        event_title=title,
        entity=entity,
        risk="green" if official_count > 0 else "yellow",
        confidence=90 if official_count > 0 else 70,
        selected=True,
        reason="verified",
        source_count=1,
        official_count=official_count,
        media_count=0,
        community_count=community_count,
        first_seen_at=ts,
        latest_published_at=ts,
        key_facts=[fact],
        evidence_links=[
            {
                "source": entity,
                "tier": source_tier,
                "reliability": reliability,
                "title": title,
                "url": url,
                "excerpt": fact,
                "excerpt_provenance": "official_source_summary" if official_count > 0 else "verified_social_post",
            }
        ],
        uncertainty=[],
        score=86.0,
        editorial_tier=tier,
    )


def _write_test_config(path: Path) -> Path:
    return write_json_atomic(
        path,
        {
            "profile": "public_web",
            "defaults": {
                "lookback_hours": 24,
                "require_x_coverage": False,
                "selection_min_publish_items": 1,
                "automatic_quality_repair": True,
            },
            "sources": [
                {
                    "id": "openai_news",
                    "name": "OpenAI",
                    "tier": "A",
                    "type": "rss",
                    "region": "global",
                    "url": "https://openai.com/news/rss.xml",
                    "enabled": True,
                    "reliability": "official",
                },
                {
                    "id": "anthropic_news",
                    "name": "Anthropic",
                    "tier": "A",
                    "type": "rss",
                    "region": "global",
                    "url": "https://www.anthropic.com/rss.xml",
                    "enabled": True,
                    "reliability": "official",
                },
            ],
        },
    )


def _sample_pipeline_feed() -> tuple[list[SourceHealth], list[FeedItem]]:
    ts = now_utc() - timedelta(hours=2)
    health = [
        SourceHealth(
            source_id="openai_news",
            source_name="OpenAI",
            tier="A",
            enabled=True,
            status="ok",
            status_code=200,
            item_count=1,
            latest_item_at=ts,
            latency_ms=42,
        ),
        SourceHealth(
            source_id="anthropic_news",
            source_name="Anthropic",
            tier="A",
            enabled=True,
            status="ok",
            status_code=200,
            item_count=1,
            latest_item_at=ts,
            latency_ms=55,
        ),
    ]
    items = [
        FeedItem(
            source_id="openai_news",
            source_name="OpenAI",
            source_tier="A",
            source_reliability="official",
            title="OpenAI 发布 GPT-5.2 模型并开放 API 工具调用",
            link="https://openai.com/index/gpt-5-2",
            guid="guid-openai-52",
            summary="OpenAI 正式发布 GPT-5.2 模型，支持 128k token 上下文窗口，API 推理延迟降低 35%，向开发者开放工具调用接口。",
            published_at=ts,
            fetched_at=ts,
        ),
        FeedItem(
            source_id="anthropic_news",
            source_name="Anthropic",
            source_tier="A",
            source_reliability="official",
            title="Anthropic 推出 Claude 4.5 模型升级代码生成能力",
            link="https://www.anthropic.com/news/claude-4-5",
            guid="guid-claude-45",
            summary="Anthropic 宣布推出 Claude 4.5 模型，新增 200k token 上下文支持，SWE-bench 评测得分提升 18%，现已上线 API 平台。",
            published_at=ts - timedelta(minutes=20),
            fetched_at=ts,
        ),
    ]
    return health, items


class PipelineModuleTests(unittest.TestCase):
    def test_resolve_run_date(self) -> None:
        self.assertRegex(resolve_run_date("today"), r"^\d{4}-\d{2}-\d{2}$")
        self.assertRegex(resolve_run_date(""), r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(resolve_run_date("2026-11-18"), "2026-11-18")

    def test_compute_source_coverage_required_true_vs_false(self) -> None:
        sources = _make_x_sources()
        all_ok_health = [
            SourceHealth(source_id=s.id, source_name=s.name, tier=s.tier, enabled=True, status="ok", stale=False)
            for s in sources
        ]
        cov_ok = compute_source_coverage(sources, all_ok_health, required=True)
        self.assertTrue(cov_ok["x"]["required"])
        self.assertEqual(cov_ok["x"]["status"], "healthy")
        self.assertEqual(cov_ok["x"]["reason"], "")

        partial_health = all_ok_health[:2]
        cov_gap = compute_source_coverage(sources, partial_health, required=True)
        self.assertEqual(cov_gap["x"]["status"], "coverage_gap")
        self.assertIn("lane_audits", cov_gap["x"]["reason"])

        cov_public = compute_source_coverage(sources, [], required=False)
        self.assertFalse(cov_public["x"]["required"])
        self.assertEqual(cov_public["x"]["status"], "not_requested")

    def test_compute_package_quality_rejects_x_or_community_headline(self) -> None:
        x_headline = _make_card(
            cluster_key="c-x-head",
            title="X 社区热议某模型传闻",
            entity="OpenAI",
            url="https://x.com/user123/status/1880000000000000099",
            tier="headline",
            reliability="community",
            source_tier="C",
            official_count=0,
            community_count=1,
        )
        official_brief = _make_card(
            cluster_key="c-off-brief",
            title="OpenAI 发布 GPT-5.2 模型",
            entity="OpenAI",
            url="https://openai.com/index/gpt-5-2",
            tier="brief",
        )
        bad_pkg = compute_package_quality(
            [x_headline, official_brief],
            minimum_publish_items=1,
            source_coverage={"x": {"required": False, "status": "not_requested"}},
        )
        self.assertFalse(bad_pkg["headline_safe"])
        self.assertFalse(bad_pkg["ok"])
        self.assertFalse(bad_pkg["publish_allowed"])
        self.assertIn("headline is an X/community signal", bad_pkg["content_issues"])

        good_headline = _make_card(
            cluster_key="c-off-head",
            title="OpenAI 发布 GPT-5.2 模型",
            entity="OpenAI",
            url="https://openai.com/index/gpt-5-2",
            tier="headline",
        )
        good_pkg = compute_package_quality(
            [good_headline],
            minimum_publish_items=1,
            source_coverage={"x": {"required": False, "status": "not_requested"}},
        )
        self.assertTrue(good_pkg["ok"])
        self.assertTrue(good_pkg["publish_allowed"])
        self.assertEqual(good_pkg["edition_mode"], "rolling_24h")

    def test_append_morning_stories_dedupes_existing_and_appends_novel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "2026-11-18"
            run_dir.mkdir(parents=True, exist_ok=True)
            self.assertEqual(append_morning_stories(run_dir, [])["status"], "no_reviewed_script")

            existing_card = _make_card(
                cluster_key="c-openai",
                title="OpenAI 发布 GPT-5.2 模型",
                entity="OpenAI",
                url="https://openai.com/index/gpt-5-2",
                tier="headline",
            )
            create_review_package(run_dir, [existing_card], run_date="2026-11-18")
            write_final_script_from_state(run_dir)

            duplicate_by_url = _make_card(
                cluster_key="c-dup",
                title="不同标题但同一链接",
                entity="OpenAI",
                url="https://openai.com/index/gpt-5-2",
                tier="brief",
            )
            novel_card = _make_card(
                cluster_key="c-anthropic",
                title="Anthropic 推出 Claude 4.5 模型接口",
                entity="Anthropic",
                url="https://www.anthropic.com/news/claude-4-5",
                tier="brief",
            )
            merged_res = append_morning_stories(run_dir, [duplicate_by_url, novel_card], max_new_items=3)
            self.assertEqual(merged_res["status"], "merged")
            self.assertEqual(merged_res["added"], 1)

            morning_doc = read_json(Path(merged_res["path"]))
            news_rows = [s for s in morning_doc["segments"] if s.get("kind") == "news"]
            self.assertEqual(len(news_rows), 2)
            self.assertEqual([s["position"] for s in news_rows], [1, 2])
            self.assertEqual([s["total"] for s in news_rows], [2, 2])
            self.assertTrue((run_dir / "review" / "morning-final-script.md").is_file())
            marker = read_json(run_dir / "review" / "render-required.json")
            self.assertEqual(marker["reason"], "morning_append")

    def test_run_pipeline_review_agent_and_auto_with_copy_quality_repair_loop(self) -> None:
        health, items = _sample_pipeline_feed()

        # 1. mode="review" and mode="agent"
        for mode_name in ("review", "agent"):
            with tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                cfg_path = _write_test_config(base / "sources.public-web.json")
                db_path = base / "briefing.sqlite3"
                runs_dir = base / "runs"

                with (
                    patch("briefing.pipeline.collect_sources", return_value=(health, items)),
                    patch("briefing.pipeline.enrich_feed_items", return_value={"ok": 2, "attempted": 2}),
                    patch("briefing.pipeline.reconcile_official_sources", return_value={"matched": 0}),
                    patch("briefing.pipeline.attach_github_compare_evidence", return_value=0),
                    patch("briefing.pipeline.attach_evidence_screenshots", return_value={"status": "ok", "captured": 0}),
                    patch(
                        "briefing.pipeline.build_provenance",
                        return_value={"git_sha": "abc1234", "git_dirty": False, "config_path": str(cfg_path)},
                    ),
                ):
                    result = run_pipeline(
                        date="2026-11-18",
                        config_path=cfg_path,
                        db_path=db_path,
                        runs_dir=runs_dir,
                        mode=mode_name,
                    )

                self.assertEqual(result.render_info["status"], "skipped")
                self.assertEqual(len(result.selected_cards), 2)
                self.assertTrue((result.out_dir / "review" / "state.json").is_file())
                self.assertTrue((result.out_dir / "review" / "draft-state.json").is_file())
                run_state = read_json(result.out_dir / "run-state.json")
                self.assertEqual(run_state["status"], "EDITED")
                if mode_name == "agent":
                    self.assertTrue((result.out_dir / "review" / "agent-brief.json").is_file())
                    self.assertTrue((result.out_dir / "review" / "agent-audit.template.json").is_file())
                    manifest = read_json(result.out_dir / "manifest.json")
                    self.assertTrue(manifest["agent_policy"]["required"])

        # 2. mode="auto" with automatic copy-quality repair loop excluding bad story #1 and re-selecting story #2
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            cfg_path = _write_test_config(base / "sources.public-web.json")
            db_path = base / "briefing.sqlite3"
            runs_dir = base / "runs"
            rendered_story_counts: list[int] = []

            def fake_render(out_dir: Path, selected_cards: list[EvidenceCard], *, run_date: str = "", **_kwargs) -> dict:
                rendered_story_counts.append(len(selected_cards))
                segs = build_segments(selected_cards, run_date=run_date)
                timed = [
                    {**seg, "start": float(i * 15), "end": float((i + 1) * 15), "duration": 15.0}
                    for i, seg in enumerate(segs)
                ]
                write_json_atomic(out_dir / "script.json", timed)
                video = out_dir / "final.mp4"
                cover = out_dir / "cover.png"
                srt = out_dir / "subtitles.srt"
                video.write_bytes(b"mp4")
                cover.write_bytes(b"png")
                srt.write_text("1\n00:00:00,000 --> 00:00:03,000\nok\n", encoding="utf-8")
                return {
                    "status": "ok",
                    "video": str(video),
                    "cover": str(cover),
                    "subtitles": str(srt),
                }

            verify_calls = 0

            def fake_verify(_out_dir: Path, *, allow_in_progress: bool = False) -> dict:
                nonlocal verify_calls
                verify_calls += 1
                if verify_calls == 1:
                    # Segment 1 is intro; segment 2 is 1st news story (position=1)
                    return {"ok": False, "errors": ["news segment 2 contains low-information copy: 页面变化"]}
                return {"ok": True, "errors": []}

            with (
                patch("briefing.pipeline.collect_sources", return_value=(health, items)),
                patch("briefing.pipeline.enrich_feed_items", return_value={"ok": 2, "attempted": 2}),
                patch("briefing.pipeline.reconcile_official_sources", return_value={"matched": 0}),
                patch("briefing.pipeline.attach_github_compare_evidence", return_value=0),
                patch("briefing.pipeline.attach_evidence_screenshots", return_value={"status": "ok", "captured": 0}),
                patch(
                    "briefing.pipeline.build_provenance",
                    return_value={"git_sha": "abc1234", "git_dirty": False, "config_path": str(config_path := cfg_path)},
                ),
                patch("briefing.pipeline.render_briefing_video", side_effect=fake_render),
                patch("briefing.pipeline.verify_run_dir", side_effect=fake_verify),
            ):
                auto_res = run_pipeline(
                    date="2026-11-18",
                    config_path=config_path,
                    db_path=db_path,
                    runs_dir=runs_dir,
                    mode="auto",
                )

            self.assertEqual(rendered_story_counts, [2, 1])
            self.assertTrue(auto_res.quality["ok"])
            self.assertEqual(len(auto_res.selected_cards), 1)
            manifest = read_json(auto_res.out_dir / "manifest.json")
            gate = manifest["automatic_quality_gate"]
            self.assertTrue(gate["ok"])
            self.assertEqual(gate["attempts"], 2)
            self.assertEqual(len(gate["automatic_exclusions"]), 1)
            self.assertEqual(gate["automatic_exclusions"][0]["excluded_positions"], [1])
            run_state = read_json(auto_res.out_dir / "run-state.json")
            self.assertEqual(run_state["status"], "QA_PASSED")


class CliModuleTests(unittest.TestCase):
    def test_build_parser_registers_exact_eight_unified_subcommands(self) -> None:
        parser = build_parser()
        subparsers_actions = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
        self.assertEqual(len(subparsers_actions), 1)
        commands = list(subparsers_actions[0].choices.keys())
        self.assertEqual(
            commands,
            ["run", "prepare", "review", "render", "verify", "publish", "repurpose", "doctor"],
        )

    def test_main_dispatch_doctor_status_prepare_review_publish_and_repurpose(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            run_dir = base / "runs" / "2026-11-18"
            run_dir.mkdir(parents=True, exist_ok=True)

            # 1. doctor --status
            buf = io.StringIO()
            with (
                patch(
                    "briefing.cli.automation_status",
                    return_value={"ok": True, "blocked": False, "stage": "new", "next_command": "py -3 -m briefing prepare"},
                ) as mock_status,
                redirect_stdout(buf),
            ):
                rc = main(["doctor", "--status", str(run_dir)])
            self.assertEqual(rc, 0)
            mock_status.assert_called_once_with(run_dir, config_path=None)
            self.assertEqual(json.loads(buf.getvalue())["stage"], "new")

            # 2. prepare --mode review
            fake_run_result = RunResult(
                run_id="2026-11-18-prep01",
                run_date="2026-11-18",
                out_dir=run_dir,
                source_health=[],
                all_cards=[],
                selected_cards=[],
                render_info={"status": "skipped"},
                quality={"ok": True},
                files={},
            )
            buf = io.StringIO()
            with (
                patch("briefing.cli.run_pipeline", return_value=fake_run_result) as mock_pipe,
                redirect_stdout(buf),
            ):
                rc = main(["prepare", "--mode", "review", "--date", "2026-11-18", "--runs-dir", str(base / "runs")])
            self.assertEqual(rc, 0)
            self.assertEqual(mock_pipe.call_args.kwargs["mode"], "review")
            prep_out = json.loads(buf.getvalue())
            self.assertEqual(prep_out["mode"], "review")
            self.assertEqual(prep_out["run_id"], "2026-11-18-prep01")

            # 3. review --finalize
            card = _make_card(
                cluster_key="c-rev",
                title="OpenAI 发布 GPT-5.2 模型",
                entity="OpenAI",
                url="https://openai.com/index/gpt-5-2",
            )
            create_review_package(run_dir, [card], run_date="2026-11-18")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["review", "--finalize", "--run-dir", str(run_dir)])
            self.assertEqual(rc, 0)
            rev_out = json.loads(buf.getvalue())
            self.assertTrue(rev_out["ok"])
            self.assertTrue((run_dir / "review" / "final-script.json").is_file())

            # 4. publish --check-auth (ok -> 0, missing_credentials -> 2, api_error -> 1)
            for auth_payload, expected_rc in (
                ({"ok": True, "expiry": {"known": False}}, 0),
                ({"ok": False, "reason": "missing_credentials"}, 2),
                ({"ok": False, "reason": "api_error"}, 1),
            ):
                buf = io.StringIO()
                with (
                    patch("briefing.cli.check_bilibili_auth", return_value=auth_payload),
                    redirect_stdout(buf),
                ):
                    rc = main(["publish", "--check-auth"])
                self.assertEqual(rc, expected_rc)

            # 5. repurpose --prune-days
            old_run = base / "runs" / "2020-01-01"
            old_run.mkdir(parents=True, exist_ok=True)
            old_mp4 = old_run / "final.mp4"
            old_mp4.write_bytes(b"heavy-video-bytes")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = main(["repurpose", "--prune-days", "7", "--runs-dir", str(base / "runs"), "--execute"])
            self.assertEqual(rc, 0)
            prune_out = json.loads(buf.getvalue())
            self.assertTrue(prune_out["execute"])
            self.assertGreater(prune_out["reclaimed_bytes"], 0)
            self.assertFalse(old_mp4.exists())


if __name__ == "__main__":
    unittest.main()

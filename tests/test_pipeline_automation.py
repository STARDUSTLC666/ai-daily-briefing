import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from briefing.models import EvidenceCard, FeedItem, SourceHealth
from briefing.pipeline import _card_exclusion_keys, _items_from_enabled_sources, _package_quality, _source_coverage, run_pipeline
from briefing.selection_optimizer import PortfolioResult


UTC = timezone.utc


def card(key: str) -> EvidenceCard:
    return EvidenceCard(
        cluster_key=key,
        event_title=f"{key} 发布可验证更新",
        entity=f"Entity {key}",
        risk="green",
        confidence=90,
        selected=True,
        reason="官方来源确认。",
        source_count=1,
        official_count=1,
        media_count=0,
        community_count=0,
        first_seen_at=datetime(2026, 7, 10, 8, tzinfo=UTC),
        latest_published_at=datetime(2026, 7, 10, 8, tzinfo=UTC),
        key_facts=[f"{key} 已公开一个具体能力更新。"],
        evidence_links=[
            {
                "source": f"Source {key}",
                "source_name": f"Source {key}",
                "tier": "A",
                "reliability": "official",
                "title": f"{key} update",
                "url": f"https://example.com/{key}",
                "excerpt": f"{key} 已公开一个具体能力更新。",
            }
        ],
        uncertainty=[],
        score=90,
    )


class PipelineAutomationTests(unittest.TestCase):
    def test_disabled_source_cache_cannot_reenter_daily_candidates(self):
        from briefing.models import Source

        now = datetime(2026, 7, 13, 6, tzinfo=UTC)
        enabled = Source("official", "Official", "A", "rss", "global", "https://example.com/feed", enabled=True)
        _disabled = Source("deep-scan", "Deep scan", "C", "rss", "global", "https://example.com/deep", enabled=False)
        rows = [
            FeedItem("official", "Official", "A", "official", "Official update", "https://example.com/1", "1", "fact", now),
            FeedItem("deep-scan", "Deep scan", "C", "community", "Cached rumour", "https://example.com/2", "2", "lead", now),
        ]

        filtered = _items_from_enabled_sources(rows, [enabled])

        self.assertEqual([item.source_id for item in filtered], ["official"])

    def test_x_coverage_gap_blocks_publish_but_not_verified_preview(self):
        items = [card(f"story-{index}") for index in range(5)]
        for index, item in enumerate(items):
            item.editorial_tier = "headline" if index == 0 else "brief"
        coverage = _source_coverage([], [])

        quality = _package_quality(5, 1, items, source_coverage=coverage)

        self.assertTrue(quality["ok"])
        self.assertFalse(quality["publish_allowed"])
        self.assertIn("X coverage", quality["reason"])

    def test_rolling_24h_package_accepts_any_positive_qualified_story_count(self):
        for count in (1, 3, 12):
            with self.subTest(count=count):
                items = [card(f"story-{index}") for index in range(count)]
                for index, item in enumerate(items):
                    item.editorial_tier = "headline" if index == 0 else "brief"
                coverage = {
                    "x": {
                        "required": True,
                        "status": "healthy",
                    }
                }

                quality = _package_quality(count, 1, items, source_coverage=coverage)

                self.assertTrue(quality["ok"])
                self.assertTrue(quality["publish_allowed"])
                self.assertEqual(quality["edition_mode"], "rolling_24h")
                self.assertEqual(quality["story_count_policy"], "all_qualified_in_window")

    def test_reliable_all_brief_edition_is_not_forced_into_a_headline(self):
        item = card("compact-update")
        item.editorial_tier = "brief"
        coverage = {"x": {"required": True, "status": "healthy"}}

        quality = _package_quality(1, 1, [item], source_coverage=coverage)

        self.assertTrue(quality["ok"])
        self.assertTrue(quality["publish_allowed"])
        self.assertEqual(quality["headline_items"], 0)
        self.assertTrue(quality["headline_safe"])
        self.assertEqual(quality["reliable_items"], 1)

    def test_empty_audited_x_lanes_count_as_coverage(self):
        from briefing.models import Source

        sources = [
            Source("x_codex_official_leads", "Official", "A", "agent_social", "global", "x", reliability="official_social"),
            Source("x_codex_personnel_leads", "Personnel", "C", "agent_social", "global", "x", reliability="official_personnel"),
            Source("x_codex_community_leads", "Community", "C", "agent_social", "global", "x", reliability="community"),
        ]
        health = [
            SourceHealth("x_codex_official_leads", "Official", "A", True, "ok", item_count=0, stale=False),
            SourceHealth("x_codex_personnel_leads", "Personnel", "C", True, "ok", item_count=0, stale=False),
            SourceHealth("x_codex_community_leads", "Community", "C", True, "ok", item_count=0, stale=False),
        ]

        coverage = _source_coverage(sources, health)

        self.assertEqual(coverage["x"]["status"], "healthy")

    def test_x_rss_health_cannot_replace_codex_lane_audits(self):
        from briefing.models import Source

        sources = [
            Source("x_openai_staff", "Staff RSS", "C", "rss", "global", "https://example.com/staff", reliability="official_personnel"),
            Source("x_community", "Community RSS", "C", "rss", "global", "https://example.com/community", reliability="community"),
        ]
        health = [
            SourceHealth("x_openai_staff", "Staff RSS", "C", True, "ok", item_count=1, stale=False),
            SourceHealth("x_community", "Community RSS", "C", True, "ok", item_count=1, stale=False),
        ]

        coverage = _source_coverage(sources, health)

        self.assertEqual(coverage["x"]["status"], "coverage_gap")
        self.assertIn("feed mirrors cannot satisfy", coverage["x"]["reason"])

    def test_quality_failure_is_resampled_and_manifest_metadata_survives_rewrite(self):
        first, alias, retained, replacement = card("first"), card("alias"), card("retained"), card("replacement")
        stable_1, stable_2, stable_3 = card("stable-1"), card("stable-2"), card("stable-3")
        merged_first = card("merged-first")
        merged_first.cluster_key = "merged:first-family"
        merged_first.source_cluster_keys = ["first", "alias"]
        cards = [first, alias, retained, replacement, stable_1, stable_2, stable_3]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = root / "sources.yaml"
            config.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "defaults": {
                            "lookback_hours": 24,
                            "selection_max_items": 5,
                            "selection_min_publish_items": 5,
                            "automatic_quality_repair": True,
                            "content_enrichment_max_items": 0,
                        },
                        "sources": [
                            {
                                "id": "sample",
                                "name": "Sample",
                                "tier": "A",
                                "type": "rss",
                                "region": "global",
                                "url": "https://example.com/feed",
                                "enabled": True,
                                "reliability": "official",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            def fake_collect(*_args, **_kwargs):
                item = FeedItem(
                    "sample", "Sample", "A", "official", "OpenAI 发布更新", "https://example.com/item", "sample-1", "具体能力更新",
                    datetime(2026, 7, 10, 8, tzinfo=UTC),
                )
                health = SourceHealth("sample", "Sample", "A", True, "ok", 200, 1, datetime(2026, 7, 10, 8, tzinfo=UTC), 1)
                return [health], [item]

            portfolio_calls = []

            def fake_portfolio(candidates, **_kwargs):
                keys = {entry.cluster_key for entry in candidates}
                portfolio_calls.append(keys)
                items = (
                    [merged_first, retained, stable_1, stable_2, stable_3]
                    if "first" in keys
                    else [retained, replacement, stable_1, stable_2, stable_3]
                )
                for index, item in enumerate(items):
                    item.editorial_tier = "headline" if index == 0 else "brief"
                return PortfolioResult(items=items, score=1.0, diagnostics={"mode": "test"})

            def fake_write_package(out_dir, _run_date, _cards, _health, *, selected_override=None, **_kwargs):
                selected = selected_override or []
                (out_dir / "manifest.json").write_text(
                    json.dumps({"counts": {"selected_cards": len(selected)}, "selected": []}),
                    encoding="utf-8",
                )
                return {"duration_seconds": 214, "selected_count": len(selected)}

            render_calls = []
            prune_calls = []

            def fake_prune(_out_dir, selected):
                prune_calls.append([entry.cluster_key for entry in selected])
                return {"status": "ok", "kept": 0, "removed": 0, "failed": 0}

            def fake_render(out_dir, selected, **_kwargs):
                render_calls.append([entry.cluster_key for entry in selected])
                paths = {}
                for key, name in [("video", "final.mp4"), ("cover", "cover.png"), ("subtitles", "subtitles.srt")]:
                    path = out_dir / name
                    path.write_text("x", encoding="utf-8")
                    paths[key] = str(path)
                return {"status": "ok", **paths}

            with patch("briefing.pipeline.collect_sources", side_effect=fake_collect), \
                patch("briefing.pipeline.enrich_feed_items", return_value={"ok": 1, "failed": 0}), \
                patch("briefing.pipeline.verify_clusters", return_value=cards), \
                patch("briefing.pipeline.attach_evidence_screenshots", return_value={"status": "ok", "captured": 0}), \
                patch("briefing.pipeline.prune_evidence_screenshots", side_effect=fake_prune), \
                patch("briefing.pipeline.select_card_portfolio", side_effect=fake_portfolio), \
                patch("briefing.pipeline.write_package", side_effect=fake_write_package), \
                patch("briefing.pipeline.render_briefing_video", side_effect=fake_render), \
                patch("briefing.pipeline.refresh_bilibili_timeline_from_script"), \
                patch("briefing.pipeline.repairable_news_positions", return_value=[1]), \
                patch("briefing.pipeline.verify_run_dir", side_effect=[
                    {"ok": False, "errors": ["news segment 1 contains low-information copy"]},
                    {"ok": True, "errors": []},
                ]), \
                patch("briefing.verify.now_utc", return_value=datetime(2026, 7, 10, 12, tzinfo=UTC)):
                result = run_pipeline(
                    date="2026-07-10",
                    config_path=config,
                    db_path=root / "db.sqlite3",
                    runs_dir=root / "runs",
                )

            self.assertTrue(result.quality_ok)
            self.assertEqual(
                render_calls,
                [
                    ["merged:first-family", "retained", "stable-1", "stable-2", "stable-3"],
                    ["retained", "replacement", "stable-1", "stable-2", "stable-3"],
                ],
            )
            self.assertEqual(prune_calls, render_calls)
            # The first two portfolio evaluations freeze and screenshot the
            # same provisional set.  The quality-repair evaluation must then
            # exclude every constituent key from the failed merged story.
            self.assertNotIn("first", portfolio_calls[-1])
            self.assertNotIn("alias", portfolio_calls[-1])
            manifest = json.loads((result.out_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["package_quality"]["ok"])
            self.assertTrue(manifest["automatic_quality_gate"]["ok"])
            self.assertTrue(manifest["automatic_quality_gate"]["automatic_retry_enabled"])
            self.assertEqual(manifest["content_enrichment"]["eligible_items"], 1)
            self.assertEqual(manifest["enrichment_plan"]["mode"], "all_eligible_candidates")
            run_state = json.loads((result.out_dir / "run-state.json").read_text(encoding="utf-8"))
            self.assertEqual(run_state["status"], "QA_PASSED")
            self.assertEqual(manifest["run_id"], run_state["run_id"])

    def test_exclusion_keys_expand_a_merged_portfolio_card(self):
        merged = card("merged")
        merged.cluster_key = "merged:model-family"
        merged.source_cluster_keys = ["first", "alias"]

        self.assertEqual(_card_exclusion_keys(merged), {"first", "alias"})


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from pathlib import Path

from briefing.pipeline import run_pipeline


class PipelineOutputTests(unittest.TestCase):
    def test_pipeline_outputs_with_fixture(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            cfg = tmp_path / "sources.yaml"
            cfg.write_text(
                '{"version":1,"defaults":{"lookback_hours":36},"sources":[{"id":"sample","name":"Sample","tier":"A","type":"rss","region":"global","url":"https://example.com/feed","enabled":true,"reliability":"official"}]}',
                encoding="utf-8",
            )

            from datetime import datetime, timezone

            import briefing.pipeline as pipeline
            import briefing.verify as verify
            from briefing.models import FeedItem, SourceHealth

            def fake_collect(sources, user_agent=None, workers=8, lookback_hours=None):
                h = SourceHealth("sample", "Sample", "A", True, "ok", 200, 1, datetime(2026, 7, 3, 8, 0, tzinfo=timezone.utc), 1, "", "https://example.com/feed", False, 0)
                it = FeedItem("sample", "Sample", "A", "official", "Qwen releases a new open model", "https://example.com/qwen", "1", "local deployment", datetime(2026, 7, 3, 8, 0, tzinfo=timezone.utc))
                return [h], [it]

            original_collect = pipeline.collect_sources
            original_now = verify.now_utc
            try:
                pipeline.collect_sources = fake_collect
                verify.now_utc = lambda: datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
                result = run_pipeline(date="2026-07-03", config_path=cfg, db_path=tmp_path / "db.sqlite3", runs_dir=tmp_path / "runs", skip_render=True)
            finally:
                pipeline.collect_sources = original_collect
                verify.now_utc = original_now
            out = result.out_dir
            for name in ["sources.md", "fact-check.md", "bilibili.md", "pinned-comment.md", "manifest.json"]:
                self.assertTrue((out / name).exists(), name)
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            self.assertGreaterEqual(manifest["counts"]["selected_cards"], 1)
            self.assertTrue(manifest["package_quality"]["ok"])
            self.assertEqual(manifest["package_quality"]["minimum_publish_items"], 1)
            self.assertEqual(manifest["package_quality"]["selected_items"], 1)
            self.assertEqual(manifest["package_quality"]["edition_mode"], "rolling_24h")
            self.assertEqual(manifest["freshness_window"]["lookback_hours"], 36)
            self.assertEqual(manifest["freshness_window"]["start_utc"], "2026-07-02T00:00:00+00:00")
            self.assertEqual(manifest["freshness_window"]["end_utc"], "2026-07-03T12:00:00+00:00")
            self.assertEqual(manifest["evidence_screenshots"]["status"], "skipped")
            self.assertTrue(manifest["run_id"].startswith("2026-07-03-"))
            run_state = json.loads((out / "run-state.json").read_text(encoding="utf-8"))
            self.assertEqual(run_state["status"], "EDITED")
            self.assertEqual(
                [stage["name"] for stage in run_state["stages"]],
                ["COLLECT", "ENRICH", "RECONCILE", "VERIFY", "EDIT"],
            )
            self.assertTrue(all(stage["status"] == "OK" for stage in run_state["stages"]))
            self.assertEqual(manifest["semantic_layer"]["stories"], 1)
            self.assertIn("structured_plan_coverage", manifest["semantic_layer"])
            self.assertIn("storyboard_templates", manifest["semantic_layer"])
            optimizer = manifest["selection_balance"]["optimizer"]
            self.assertEqual(optimizer["mode"], "unbounded")
            semantic = manifest["selected"][0]
            self.assertIn("story_spec", semantic)
            self.assertIn("editorial_plan", semantic)
            self.assertIn("storyboard", semantic)
            self.assertEqual(semantic["story_spec"]["story_id"], semantic["editorial_plan"]["story_id"])
            self.assertEqual(semantic["story_spec"]["story_id"], semantic["storyboard"]["story_id"])
            fact_check = (out / "fact-check.md").read_text(encoding="utf-8-sig")
            self.assertIn("筛选时间范围：当前运行时间往前 36 小时", fact_check)


if __name__ == "__main__":
    unittest.main()

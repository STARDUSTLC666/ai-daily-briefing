import json
import tempfile
import unittest
from pathlib import Path

from briefing.render import _read_news_manuscript, _select_news_manuscript, _write_news_manuscript
from briefing.render_contract import mark_render_required, record_rendered_manuscript, verify_review_render_contract


class RenderManuscriptTests(unittest.TestCase):
    def test_news_manuscript_preserves_claim_contract_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            segments = [{"kind": "news", "position": 1, "total": 1, "story_id": "story-1", "claim_ids": ["claim-1"], "generation_path": "structured_editorial_plan", "title": "标题", "caption": "标题", "text": "正文", "cards": []}]

            _md, manuscript = _write_news_manuscript(out, segments)
            loaded = _read_news_manuscript(manuscript)

            self.assertEqual(loaded[0]["story_id"], "story-1")
            self.assertEqual(loaded[0]["claim_ids"], ["claim-1"])
            self.assertEqual(loaded[0]["generation_path"], "structured_editorial_plan")
            self.assertEqual(loaded[0]["position"], 1)

    def test_news_manuscript_round_trips_segments(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            segments = [
                {
                    "kind": "news",
                    "position": 1,
                    "title": "腾讯混元Hy3正式发布",
                    "caption": "腾讯混元Hy3正式发布",
                    "text": "这条主要是：腾讯混元Hy3正式发布。可以确认的是：来源是 36氪。",
                    "cards": [{"title": "内容", "body": "来源是 36氪。"}],
                    "visual_pages": [{"kind": "cards", "title": "腾讯混元Hy3正式发布", "cards": []}],
                }
            ]

            md_path, json_path = _write_news_manuscript(root, segments)
            loaded = _read_news_manuscript(json_path)

            self.assertTrue(md_path.exists())
            self.assertTrue(json_path.exists())
            self.assertIn("这条主要是", loaded[0]["text"])
            self.assertIn("腾讯混元Hy3正式发布", loaded[0]["text"])
            self.assertIn("可以确认的是", loaded[0]["text"])
            self.assertIn("视频、配音和字幕的共同来源", md_path.read_text(encoding="utf-8-sig"))
            self.assertIn("可以确认的是", md_path.read_text(encoding="utf-8-sig"))

    def test_reviewed_manuscript_wins_over_draft(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            review_dir = root / "review"
            review_dir.mkdir()
            final_json = review_dir / "final-script.json"
            final_md = review_dir / "final-script.md"
            final_md.write_text("# final\n", encoding="utf-8-sig")
            final_json.write_text(
                '{"version":1,"segments":[{"kind":"news","title":"审核稿","text":"使用审核后的最终稿。","cards":[]}]}',
                encoding="utf-8-sig",
            )

            md_path, json_path, segments, mode = _select_news_manuscript(
                root,
                [{"kind": "news", "title": "草稿", "text": "自动草稿。", "cards": []}],
            )

            self.assertEqual(mode, "reviewed")
            self.assertEqual(md_path, final_md)
            self.assertEqual(json_path, final_json)
            self.assertEqual(segments[0]["title"], "审核稿")
            synced = _read_news_manuscript(root / "news-script.json")
            self.assertEqual(synced[0]["title"], "审核稿")

    def test_morning_manuscript_wins_over_reviewed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            review_dir = root / "review"
            review_dir.mkdir()
            (review_dir / "final-script.json").write_text(
                '{"version":1,"segments":[{"kind":"news","title":"人工稿","text":"人工审核。","cards":[]}]}',
                encoding="utf-8-sig",
            )
            morning_json = review_dir / "morning-final-script.json"
            morning_md = review_dir / "morning-final-script.md"
            morning_md.write_text("# morning\n", encoding="utf-8-sig")
            morning_json.write_text(
                '{"version":1,"segments":[{"kind":"news","title":"六点成品稿","text":"人工稿加新增。","cards":[]}]}',
                encoding="utf-8-sig",
            )

            md_path, json_path, segments, mode = _select_news_manuscript(
                root,
                [{"kind": "news", "title": "草稿", "text": "自动草稿。", "cards": []}],
            )

            self.assertEqual(mode, "morning-reviewed")
            self.assertEqual(md_path, morning_md)
            self.assertEqual(json_path, morning_json)
            self.assertEqual(segments[0]["title"], "六点成品稿")
            synced = _read_news_manuscript(root / "news-script.json")
            self.assertEqual(synced[0]["title"], "六点成品稿")

    def test_draft_manuscript_is_created_without_review(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _md_path, json_path, segments, mode = _select_news_manuscript(
                root,
                [{"kind": "news", "title": "草稿", "text": "自动草稿。", "cards": []}],
            )

            self.assertEqual(mode, "draft")
            self.assertTrue(json_path.exists())
            self.assertEqual(segments[0]["title"], "草稿")

    def test_automatic_render_ignores_stale_reviewed_manuscript(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            review_dir = root / "review"
            review_dir.mkdir()
            (review_dir / "final-script.json").write_text(
                '{"version":1,"segments":[{"kind":"news","title":"旧人工稿","text":"不应被自动流程复用。","cards":[]}]}',
                encoding="utf-8-sig",
            )

            _md_path, _json_path, segments, mode = _select_news_manuscript(
                root,
                [{"kind": "news", "title": "自动严选稿", "text": "自动流程必须使用当前候选。", "cards": []}],
                prefer_reviewed=False,
            )

            self.assertEqual(mode, "draft")
            self.assertEqual(segments[0]["title"], "自动严选稿")

    def test_successful_reviewed_render_clears_matching_stale_marker(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            review_dir = root / "review"
            review_dir.mkdir()
            final = review_dir / "final-script.json"
            segments = [{"kind": "news", "title": "审核稿", "caption": "审核稿", "text": "使用审核后的最终稿。", "cards": []}]
            final.write_text(json.dumps({"version": 1, "segments": segments}, ensure_ascii=False), encoding="utf-8-sig")
            script = root / "script.json"
            script.write_text(json.dumps([{**segments[0], "start": 0, "end": 8, "duration": 8}], ensure_ascii=False), encoding="utf-8-sig")
            (root / "final.mp4").write_bytes(b"video")
            mark_render_required(root, final)

            contract = record_rendered_manuscript(root, final, script)

            self.assertIsNotNone(contract)
            self.assertFalse((review_dir / "render-required.json").exists())
            self.assertEqual(verify_review_render_contract(root), [])

if __name__ == "__main__":
    unittest.main()

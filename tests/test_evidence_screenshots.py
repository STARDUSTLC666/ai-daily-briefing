import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from briefing.evidence_screenshots import (
    _blocked_page_reason,
    _http_unusable_reason,
    _page_story_error,
    _capture_social_with_opencli,
    _social_cache_path,
    _social_embed_url,
    _social_dom_error,
    _social_post_identity,
    attach_evidence_screenshots,
    prune_evidence_screenshots,
)
from tests.test_pipeline_automation import card


class EvidenceScreenshotPriorityTests(unittest.TestCase):
    def test_social_embed_url_is_bound_to_exact_status(self):
        url = _social_embed_url("2076719546954825769")
        self.assertEqual(
            url,
            "https://platform.twitter.com/embed/Tweet.html?id=2076719546954825769&dnt=true&theme=light&lang=zh-cn",
        )

    def test_social_cache_is_versioned_and_bound_to_status(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {"BRIEFING_SOCIAL_SCREENSHOT_CACHE_DIR": td},
        ):
            path = _social_cache_path(("AnthropicAI", "2076719546954825769"))
        self.assertEqual(path, Path(td) / "anthropicai-2076719546954825769.png")

    def test_unbounded_selection_captures_every_required_social_post(self):
        first = card("thread")
        second = card("later")
        for story in (first, second):
            story.official_count = 1
            story.media_count = 0
            story.community_count = 0
        first.evidence_links = [
            {
                "source": "Official X",
                "tier": "A",
                "reliability": "official_social",
                "title": "Thread update",
                "url": f"https://x.com/official/status/{index}",
            }
            for index in (101, 102, 103)
        ]
        second.evidence_links = [
            {
                "source": "Official X",
                "tier": "A",
                "reliability": "official_social",
                "title": "Later update",
                "url": "https://x.com/official/status/201",
            }
        ]
        captured_urls: list[str] = []

        def capture(_browser, url, output, *_args):
            captured_urls.append(url)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"png")
            return True, "captured"

        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {"BRIEFING_EVIDENCE_SCREENSHOTS": "auto"},
        ), patch("briefing.evidence_screenshots._browser_exe", return_value="chrome"), patch(
            "briefing.evidence_screenshots._capture_url", side_effect=capture
        ), patch("briefing.evidence_screenshots._image_is_nonblank", return_value=True):
            os.environ.pop("BRIEFING_EVIDENCE_SCREENSHOT_LIMIT", None)
            result = attach_evidence_screenshots(
                Path(td),
                [first, second],
                max_items=0,
                selected_override=[first, second],
            )

        self.assertEqual(result["attempted"], 4)
        self.assertEqual(len(captured_urls), 4)
        self.assertEqual(second.evidence_links[0]["screenshot_status"], "captured")

    def test_opencli_social_capture_isolates_article_before_screenshot(self):
        commands: list[list[str]] = []

        def run(command: list[str], _timeout: int):
            commands.append(command)
            if "get" in command and "url" in command:
                return Mock(returncode=0, stdout="https://x.com/source_account/status/123\n", stderr="")
            if "eval" in command:
                return Mock(returncode=0, stdout='{"found":true,"text_length":120,"exact":true,"height":640}', stderr="")
            return Mock(returncode=0, stdout="{}", stderr="")

        with tempfile.TemporaryDirectory() as td, patch(
            "briefing.evidence_screenshots.shutil.which", return_value="opencli.cmd"
        ), patch("briefing.evidence_screenshots._run_opencli", side_effect=run), patch(
            "briefing.evidence_screenshots._image_is_nonblank", return_value=True
        ):
            ok, detail = _capture_social_with_opencli(
                "https://x.com/source_account/status/123",
                Path(td) / "shot.png",
                20,
                "https://x.com/source_account/status/123",
            )

        self.assertTrue(ok)
        self.assertEqual(detail, "captured_logged_social_post")
        eval_command = next(command for command in commands if "eval" in command)
        eval_script = eval_command[-1]
        self.assertIn("document.body.replaceChildren(clone)", eval_script)
        self.assertIn("article[data-testid=tweet]", eval_script)
        self.assertIn("pbs.twimg.com/media", eval_script)
        self.assertIn("original.searchParams.set('name', 'orig')", eval_script)
        self.assertIn("image.removeAttribute('srcset')", eval_script)
        screenshot_command = next(command for command in commands if "screenshot" in command)
        self.assertEqual(screenshot_command[-4:], ["--width", "1200", "--height", "640"])

    def test_chrome_451_error_page_cannot_count_as_evidence(self):
        self.assertEqual(_blocked_page_reason("该网页无法正常运作 HTTP ERROR 451"), "http error 451")
        response = Mock(status_code=451)
        with patch("briefing.evidence_screenshots.requests.get", return_value=response):
            self.assertEqual(_http_unusable_reason("https://example.com/article", 10), "http_451")
        response.close.assert_called_once()

    def test_required_x_source_post_uses_budget_before_optional_media_link(self):
        normal = [card(f"normal-{index}") for index in range(4)]
        signal = card("signal")
        signal.risk = "yellow"
        signal.official_count = 0
        signal.media_count = 1
        signal.community_count = 1
        signal.evidence_links = [
            {
                "source": "Media relay",
                "reliability": "media",
                "tier": "B",
                "url": "https://example.com/relay",
                "excerpt": "媒体转述一条具体 AI 产品线索。",
            },
            {
                "source": "X / source account",
                "reliability": "official_personnel",
                "tier": "C",
                "url": "https://x.com/source_account/status/123",
                "excerpt": "原帖披露一项具体 AI 产品变化。",
            },
        ]
        cards = [*normal, signal]
        captured_urls: list[str] = []

        def capture(_browser, url, output, _timeout, _expected_identity_url="", _expected_text=""):
            captured_urls.append(url)
            Path(output).parent.mkdir(parents=True, exist_ok=True)
            Path(output).write_bytes(b"image")
            return True, "captured"

        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {"BRIEFING_EVIDENCE_SCREENSHOT_LIMIT": "1", "BRIEFING_EVIDENCE_SCREENSHOTS": "auto"},
        ), patch("briefing.evidence_screenshots.select_cards", return_value=cards), patch(
            "briefing.evidence_screenshots._browser_exe", return_value="chrome"
        ), patch("briefing.evidence_screenshots._capture_url", side_effect=capture):
            result = attach_evidence_screenshots(Path(td), cards, max_items=5, strict_auto=True)

        self.assertEqual(result["attempted"], 1)
        self.assertEqual(captured_urls, ["https://x.com/source_account/status/123"])
        self.assertEqual(signal.evidence_links[1]["screenshot_status"], "captured")
        self.assertEqual(signal.evidence_links[0]["screenshot_required"], "false")

    def test_social_screenshot_dom_must_match_account_status_and_content(self):
        url = "https://x.com/source_account/status/123456"
        valid = "<html><body><a href='/source_account/status/123456'>@source_account</a><p>" + ("具体产品变化与上下文。" * 10) + "</p></body></html>"

        self.assertEqual(_social_post_identity(url), ("source_account", "123456"))
        self.assertEqual(_social_dom_error(valid, url), "")
        self.assertEqual(_social_dom_error(valid.replace("123456", "999999"), url), "social_status_id_missing")
        self.assertEqual(_social_dom_error(valid.replace("source_account", "other"), url), "social_username_missing")

    def test_normal_page_dom_must_match_story_identity(self):
        expected = "CircleCI Introducing Chunk Sidecars"
        matching = "<main><h1>Introducing Chunk Sidecars</h1><p>CircleCI brings validation into coding agents.</p></main>"
        unrelated = "<main><h1>Claude Code update</h1><p>Anthropic publishes a different product story.</p></main>"

        self.assertEqual(_page_story_error(matching, expected), "")
        self.assertEqual(_page_story_error(unrelated, expected), "story_identity_mismatch")

    def test_official_evidence_gets_the_single_story_screenshot_slot(self):
        story = card("official-priority")
        story.event_title = "Introducing Chunk Sidecars"
        story.evidence_links = [
            {"source": "InfoQ", "tier": "B", "reliability": "media", "url": "https://infoq.example/relay", "title": story.event_title},
            {"source": "CircleCI", "tier": "A", "reliability": "official", "url": "https://circleci.com/blog/chunk-sidecars", "title": story.event_title},
        ]
        captured_urls: list[str] = []

        def capture(_browser, url, output, *_args):
            captured_urls.append(url)
            Path(output).parent.mkdir(parents=True, exist_ok=True)
            Path(output).write_bytes(b"image")
            return True, "captured"

        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {"BRIEFING_EVIDENCE_SCREENSHOT_LIMIT": "1", "BRIEFING_EVIDENCE_SCREENSHOTS": "auto"},
        ), patch("briefing.evidence_screenshots.select_cards", return_value=[story]), patch(
            "briefing.evidence_screenshots._browser_exe", return_value="chrome"
        ), patch("briefing.evidence_screenshots._capture_url", side_effect=capture):
            attach_evidence_screenshots(Path(td), [story], max_items=1)

        self.assertEqual(captured_urls, ["https://circleci.com/blog/chunk-sidecars"])
        self.assertEqual(story.evidence_links[1]["screenshot_kind"], "web_page")
        self.assertNotIn("screenshot_status", story.evidence_links[0])

    def test_enriched_original_media_precedes_plain_media_and_google_news(self):
        story = card("enriched-media-priority")
        story.official_count = 0
        story.media_count = 3
        story.evidence_links = [
            {
                "source": "Google News AI 中文搜索",
                "tier": "B",
                "reliability": "media",
                "url": "https://news.google.com/rss/articles/example",
                "title": story.event_title,
            },
            {
                "source": "普通媒体",
                "tier": "B",
                "reliability": "media",
                "url": "https://media.example/relay",
                "title": story.event_title,
            },
            {
                "source": "InfoQ 中文",
                "tier": "B",
                "reliability": "media",
                "url": "https://www.infoq.cn/article/original",
                "final_url": "https://www.infoq.cn/article/original",
                "article_status": "ok",
                "title": story.event_title,
            },
        ]
        captured_urls: list[str] = []

        def capture(_browser, url, output, *_args):
            captured_urls.append(url)
            Path(output).parent.mkdir(parents=True, exist_ok=True)
            Path(output).write_bytes(b"image")
            return True, "captured"

        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {"BRIEFING_EVIDENCE_SCREENSHOT_LIMIT": "1", "BRIEFING_EVIDENCE_SCREENSHOTS": "auto"},
        ), patch("briefing.evidence_screenshots.select_cards", return_value=[story]), patch(
            "briefing.evidence_screenshots._browser_exe", return_value="chrome"
        ), patch("briefing.evidence_screenshots._capture_url", side_effect=capture):
            attach_evidence_screenshots(Path(td), [story], max_items=1)

        self.assertEqual(captured_urls, ["https://www.infoq.cn/article/original"])
        self.assertEqual(story.evidence_links[2]["screenshot_status"], "captured")
        self.assertNotIn("screenshot_status", story.evidence_links[0])

    def test_cleanup_only_removes_unreferenced_pngs_from_current_run(self):
        story = card("final-story")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_dir = root / "runs" / "2026-07-13"
            screenshot_dir = run_dir / "evidence-screenshots"
            screenshot_dir.mkdir(parents=True)
            kept = screenshot_dir / "kept.png"
            stale = screenshot_dir / "stale.png"
            note = screenshot_dir / "README.txt"
            historical = root / "runs" / "2026-07-12" / "evidence-screenshots" / "history.png"
            kept.write_bytes(b"kept")
            stale.write_bytes(b"stale")
            note.write_text("not a generated screenshot", encoding="utf-8")
            historical.parent.mkdir(parents=True)
            historical.write_bytes(b"history")
            story.evidence_links[0]["screenshot_path"] = str(kept)

            result = prune_evidence_screenshots(run_dir, [story])

            self.assertEqual(result, {"status": "ok", "kept": 1, "removed": 1, "failed": 0})
            self.assertTrue(kept.exists())
            self.assertFalse(stale.exists())
            self.assertTrue(note.exists())
            self.assertTrue(historical.exists())

    def test_credible_media_can_fall_back_to_labelled_excerpt_visual(self):
        story = card("excerpt-fallback")
        story.risk = "yellow"
        story.official_count = 0
        story.media_count = 1
        story.evidence_links = [
            {
                "source": "InfoQ 中文",
                "tier": "B",
                "reliability": "media",
                "url": "https://www.infoq.cn/article/example",
                "title": "CircleCI推出Chunk Sidecars",
                "excerpt": "CircleCI 发布 Chunk Sidecars，将 CI 级校验带入 AI 编码智能体的内部开发循环，并允许提交前执行测试、lint 与格式化。" * 2,
            }
        ]

        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {"BRIEFING_EVIDENCE_SCREENSHOT_LIMIT": "1", "BRIEFING_EVIDENCE_SCREENSHOTS": "auto"},
        ), patch("briefing.evidence_screenshots.select_cards", return_value=[story]), patch(
            "briefing.evidence_screenshots._browser_exe", return_value="chrome"
        ), patch("briefing.evidence_screenshots._capture_url", return_value=(False, "http_451")):
            result = attach_evidence_screenshots(Path(td), [story], max_items=1)
            fallback_exists = Path(story.evidence_links[0]["screenshot_path"]).exists()

        evidence = story.evidence_links[0]
        self.assertEqual(result["captured"], 1)
        self.assertEqual(evidence["screenshot_status"], "captured")
        self.assertEqual(evidence["screenshot_kind"], "source_excerpt_card")
        self.assertTrue(fallback_exists)


if __name__ == "__main__":
    unittest.main()

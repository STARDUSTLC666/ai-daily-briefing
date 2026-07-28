from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from briefing.opencli_social import collect_x_candidates, fetch_account_tweets, normalize_account_tweets, parse_x_datetime, social_lanes


class OpenCliSocialTests(unittest.TestCase):
    @patch("briefing.opencli_social._opencli_executable", return_value="opencli.cmd")
    @patch("briefing.opencli_social.subprocess.run")
    def test_empty_result_is_a_successful_zero_item_account_check(self, run, _executable) -> None:
        run.return_value.returncode = 66
        run.return_value.stdout = "ok: false\nerror:\n  code: EMPTY_RESULT\n  message: no recent tweets returned no data\n"
        run.return_value.stderr = ""

        self.assertEqual(fetch_account_tweets("xai", profile="daily-briefing", limit=8, timeout_seconds=30), [])

    def test_parses_x_and_iso_timestamps(self) -> None:
        self.assertEqual(
            parse_x_datetime("Sat Jul 11 02:27:18 +0000 2026"),
            datetime(2026, 7, 11, 2, 27, 18, tzinfo=timezone.utc),
        )
        self.assertEqual(
            parse_x_datetime("2026-07-11T02:27:18Z"),
            datetime(2026, 7, 11, 2, 27, 18, tzinfo=timezone.utc),
        )

    def test_normalizes_only_fresh_statuses_owned_by_requested_account(self) -> None:
        now = datetime(2026, 7, 13, 0, 0, tzinfo=timezone.utc)
        rows = [
            {
                "id": "100",
                "text": "  New model   is available. ",
                "created_at": "Sun Jul 12 23:00:00 +0000 2026",
                "url": "https://x.com/OpenAI/status/100?ref=home",
                "likes": 12,
                "media_urls": ["https://pbs.twimg.com/media/test.jpg"],
            },
            {
                "id": "101",
                "text": "retweet",
                "created_at": "Sun Jul 12 23:00:00 +0000 2026",
                "url": "https://x.com/another/status/101",
            },
            {
                "id": "99",
                "text": "old",
                "created_at": "Sat Jul 11 20:00:00 +0000 2026",
                "url": "https://x.com/OpenAI/status/99",
            },
        ]

        result = normalize_account_tweets("x_codex_official_leads", "OpenAI", rows, now=now, lookback_hours=24)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["status_id"], "100")
        self.assertEqual(result[0]["text"], "New model is available.")
        self.assertEqual(result[0]["engagement"]["likes"], 12)

    def test_reads_only_enabled_agent_social_lanes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sources.yaml"
            path.write_text(
                json.dumps(
                    {
                        "sources": [
                            {"id": "official", "type": "agent_social", "enabled": True, "url": "data/x.json#A,@B,A"},
                            {"id": "disabled", "type": "agent_social", "enabled": False, "url": "data/x.json#C"},
                            {"id": "rss", "type": "rss", "enabled": True, "url": "https://example.com/rss"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(social_lanes(path), {"official": ["A", "B"]})

    def test_candidate_file_is_discovery_only_and_records_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = root / "sources.yaml"
            output = root / "candidates.json"
            config.write_text(
                json.dumps(
                    {
                        "sources": [
                            {"id": "official", "type": "agent_social", "enabled": True, "url": "data/x.json#OpenAI,Broken"}
                        ]
                    }
                ),
                encoding="utf-8",
            )

            def fake_fetcher(account: str, **_: object) -> list[dict[str, object]]:
                if account == "Broken":
                    raise RuntimeError("profile disconnected")
                return [
                    {
                        "text": "Release",
                        "created_at": "Sun Jul 12 23:00:00 +0000 2026",
                        "url": "https://x.com/OpenAI/status/100",
                    }
                ]

            payload = collect_x_candidates(
                config_path=config,
                output_path=output,
                now=datetime(2026, 7, 13, 0, 0, tzinfo=timezone.utc),
                fetcher=fake_fetcher,
            )

            self.assertEqual(payload["producer"], "opencli")
            self.assertNotIn("agent", payload)
            self.assertEqual(payload["lanes"]["official"]["accounts_failed"], ["Broken"])
            self.assertEqual(len(payload["items"]), 1)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["items"][0]["status_id"], "100")

    def test_browser_bridge_failure_short_circuits_remaining_accounts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = root / "sources.yaml"
            output = root / "candidates.json"
            config.write_text(
                json.dumps(
                    {
                        "sources": [
                            {"id": "official", "type": "agent_social", "enabled": True, "url": "data/x.json#OpenAI,AnthropicAI"},
                            {"id": "community", "type": "agent_social", "enabled": True, "url": "data/x.json#testingcatalog"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            calls: list[str] = []
            messages: list[str] = []

            def disconnected_fetcher(account: str, **_: object) -> list[dict[str, object]]:
                calls.append(account)
                raise RuntimeError('code: BROWSER_CONNECT\nmessage: Browser profile "daily" is not connected')

            payload = collect_x_candidates(
                config_path=config,
                output_path=output,
                now=datetime(2026, 7, 13, 0, 0, tzinfo=timezone.utc),
                fetcher=disconnected_fetcher,
                progress=messages.append,
            )

            self.assertEqual(calls, ["OpenAI"])
            self.assertEqual(payload["lanes"]["official"]["accounts_failed"], ["OpenAI", "AnthropicAI"])
            self.assertEqual(payload["lanes"]["community"]["accounts_failed"], ["testingcatalog"])
            self.assertTrue(any("browser_unavailable" in message for message in messages))


if __name__ == "__main__":
    unittest.main()

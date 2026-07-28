import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from briefing.collect import _google_news_url, _normalize_published_at, fetch_agent_social_source, parse_feed, parse_google_news
from briefing.config import load_sources
from briefing.models import Source


class FeedParseTests(unittest.TestCase):
    def test_codex_social_lead_source_is_fresh_allowlisted_and_verified(self):
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "leads.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "agent": "codex",
                        "generated_at": now.isoformat(),
                        "lane_audits": {
                            "x_codex_personnel_leads": {
                                "status": "checked",
                                "checked_at": now.isoformat(),
                                "accounts_checked": ["sama", "gdb"],
                                "qualifying_items": 1,
                            }
                        },
                        "items": [
                            {
                                "source_id": "x_codex_personnel_leads",
                                "url": "https://x.com/sama/status/1234567890",
                                "title": "OpenAI 团队成员提到一项新的 Codex 实验",
                                "text": "原帖明确提到 Codex 正在测试一项新的团队工作流，但尚未发布正式公告。",
                                "published_at": now.isoformat(),
                                "checked_urls": ["https://x.com/sama/status/1234567890"],
                                "checks": {"account_verified": True, "post_text_verified": True},
                            },
                            {
                                "source_id": "x_codex_personnel_leads",
                                "url": "https://x.com/forged/status/9999999999",
                                "title": "伪造账号",
                                "text": "这一条即使文本足够长，也不在配置允许的账号白名单中。",
                                "published_at": now.isoformat(),
                                "checked_urls": ["https://x.com/forged/status/9999999999"],
                                "checks": {"account_verified": True, "post_text_verified": True},
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            source = Source(
                id="x_codex_personnel_leads",
                name="Codex X leads",
                tier="C",
                type="agent_social",
                region="global",
                url=f"{path}#sama,gdb",
                reliability="official_personnel",
            )

            health, items = fetch_agent_social_source(source, lookback_hours=24)

        self.assertEqual(health.status, "ok")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].link, "https://x.com/sama/status/1234567890")
        self.assertEqual(items[0].source_reliability, "official_personnel")

    def test_codex_social_lane_can_be_freshly_audited_with_no_qualifying_posts(self):
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "leads.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "agent": "codex",
                        "generated_at": now.isoformat(),
                        "lane_audits": {
                            "x_codex_community_leads": {
                                "status": "checked",
                                "checked_at": now.isoformat(),
                                "accounts_checked": ["testingcatalog"],
                                "qualifying_items": 0,
                            }
                        },
                        "items": [],
                    }
                ),
                encoding="utf-8",
            )
            source = Source(
                id="x_codex_community_leads",
                name="Codex community X leads",
                tier="C",
                type="agent_social",
                region="global",
                url=f"{path}#testingcatalog",
                reliability="community",
            )

            health, items = fetch_agent_social_source(source, lookback_hours=24)

        self.assertEqual(health.status, "ok")
        self.assertEqual(health.item_count, 0)
        self.assertEqual(items, [])

    def test_x_feed_mirror_is_enabled_and_links_back_to_original_post(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"RSSHUB_BASE_URL": "", "X_RSS_BASE_URL": "https://nitter.net"}):
            config = Path(td) / "sources.json"
            config.write_text(
                json.dumps({"sources": [{"id": "x_openai_sama", "name": "X / Sam", "type": "rsshub", "url": "/twitter/user/sama", "enabled": True, "tier": "C", "reliability": "official_personnel"}]}),
                encoding="utf-8",
            )
            source = load_sources(config)[0]
            self.assertEqual(source.type, "rss")
            self.assertEqual(source.url, "https://nitter.net/sama/rss")
            rss = """<rss><channel><item><title>Update</title><link>https://nitter.net/sama/status/123456#m</link><guid>123456</guid><description>AI update</description></item></channel></rss>"""
            item = parse_feed(rss, source, source.url)[0]
            self.assertEqual(item.link, "https://x.com/sama/status/123456")
            self.assertEqual(item.raw["discovery_url"], "https://nitter.net/sama/status/123456#m")

            forged = rss.replace("/sama/status/", "/not_sama/status/")
            self.assertEqual(parse_feed(forged, source, source.url), [])

    def test_parse_rss_fixture(self):
        src = Source(id="sample", name="Sample", tier="A", type="rss", region="global", url="https://example.com/feed", reliability="official")
        text = Path("tests/fixtures/sample_feed.xml").read_text(encoding="utf-8")
        items = parse_feed(text, src, src.url)
        self.assertEqual(len(items), 2)
        self.assertTrue(items[0].title.startswith("Qwen releases"))
        self.assertIsNotNone(items[0].published_at)
        self.assertIsNone(items[1].published_at)

    def test_infoq_future_gmt_timestamp_is_treated_as_local_time(self):
        src = Source(id="infoq_cn", name="InfoQ 中文", tier="B", type="rss", region="cn", url="https://www.infoq.cn/feed", reliability="media")
        future = datetime.now(timezone.utc) + timedelta(hours=6)

        normalized = _normalize_published_at(src, future)

        self.assertAlmostEqual((future - normalized).total_seconds(), 8 * 3600, delta=1)

    def test_non_infoq_future_timestamp_is_not_adjusted(self):
        src = Source(id="sample", name="Sample", tier="B", type="rss", region="cn", url="https://example.com/feed", reliability="media")
        future = datetime.now(timezone.utc) + timedelta(hours=6)

        self.assertEqual(_normalize_published_at(src, future), future)

    def test_google_news_url_uses_lookback_window(self):
        src = Source(id="gn", name="Google News", tier="B", type="google_news", region="cn", url="OpenAI OR DeepSeek", reliability="media")

        url = _google_news_url(src, lookback_hours=24)

        self.assertIn("news.google.com/rss/search", url)
        self.assertIn("when%3A24h", url)
        self.assertIn("hl=zh-CN", url)

    def test_parse_google_news_uses_publisher_as_source_name(self):
        src = Source(id="gn", name="Google News", tier="B", type="google_news", region="global", url="AI", reliability="media")
        text = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel>
          <item>
            <title>OpenAI ships new feature - The Verge</title>
            <link>https://news.google.com/rss/articles/example</link>
            <guid>https://news.google.com/rss/articles/example</guid>
            <pubDate>Sat, 27 Jun 2026 12:00:00 GMT</pubDate>
            <description>Summary text</description>
            <source url="https://www.theverge.com">The Verge</source>
          </item>
        </channel></rss>
        """

        items = parse_google_news(text, src, "https://news.google.com/rss/search?q=AI")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].source_name, "The Verge")
        self.assertEqual(items[0].source_id, "gn")
        self.assertEqual(items[0].raw["publisher"], "The Verge")
        self.assertEqual(items[0].published_at, datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()

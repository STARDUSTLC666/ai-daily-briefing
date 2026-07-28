import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from briefing.cluster import cluster_items
from briefing.content_enrichment import ArticleResult
from briefing.models import FeedItem, Source
from briefing.official_reconciliation import official_link_candidates, reconcile_official_sources
from briefing.verify import verify_clusters


UTC = timezone.utc


def source(*, source_id: str, url: str, source_type: str = "web", reliability: str = "official") -> Source:
    return Source(
        id=source_id,
        name=source_id,
        tier="A",
        type=source_type,
        region="global",
        url=url,
        reliability=reliability,
    )


def media_item(links: list[dict[str, str]]) -> FeedItem:
    return FeedItem(
        source_id="infoq_cn",
        source_name="InfoQ 中文",
        source_tier="B",
        source_reliability="media",
        title="Circle CI推出Chunk Sidecars，将CI校验直接引入AI编码工作流",
        link="https://www.infoq.cn/article/circleci",
        guid="circleci",
        summary="CircleCI 发布 Chunk Sidecars。",
        published_at=datetime(2026, 7, 13, 3, tzinfo=UTC),
        raw={"article_enrichment": {"status": "ok", "links": links}},
    )


class OfficialReconciliationTests(unittest.TestCase):
    def test_event_specific_vendor_page_beats_homepage_and_background_brand(self):
        item = media_item(
            [
                {"url": "https://circleci.com/", "text": "CircleCI"},
                {"url": "https://www.anthropic.com/news/claude-code", "text": "Claude Code"},
                {"url": "https://circleci.com/blog/chunk-sidecars/", "text": "Chunk Sidecars"},
            ]
        )
        sources = [source(source_id="anthropic_official_web", url="https://www.anthropic.com/news")]

        candidates = official_link_candidates(item, sources)

        self.assertTrue(candidates)
        self.assertEqual(candidates[0].url, "https://circleci.com/blog/chunk-sidecars")
        self.assertFalse(any("anthropic.com" in candidate.url for candidate in candidates))

    def test_reconciliation_records_official_date_and_stale_event(self):
        item = media_item([{"url": "https://circleci.com/blog/chunk-sidecars/", "text": "Chunk Sidecars"}])

        def fake_fetcher(_url: str, **_kwargs) -> ArticleResult:
            return ArticleResult(
                status="ok",
                url=_url,
                final_url=_url,
                title="Introducing Chunk Sidecars",
                text="CircleCI introduces Chunk Sidecars for inner-loop CI validation in AI coding workflows.",
                facts=["Chunk Sidecars bring CI validation into AI coding workflows."],
                published_at=datetime(2026, 6, 20, tzinfo=UTC),
            )

        stats = reconcile_official_sources(
            [item],
            [],
            lookback_hours=24,
            now=datetime(2026, 7, 13, 6, tzinfo=UTC),
            fetcher=fake_fetcher,
        )

        record = item.raw["official_reconciliation"]
        self.assertEqual(stats["matched"], 1)
        self.assertEqual(stats["stale"], 1)
        self.assertEqual(record["freshness"], "stale")
        self.assertEqual(record["title"], "Introducing Chunk Sidecars")

    def test_configured_official_x_post_is_bound_without_page_crawl(self):
        item = media_item([{"url": "https://x.com/OpenAI/status/1234567890", "text": "OpenAI product update"}])
        item.title = "OpenAI发布新的产品更新"
        sources = [
            source(
                source_id="x_codex_official_leads",
                url="data/codex-social-leads.json#OpenAI,AnthropicAI",
                source_type="agent_social",
                reliability="official_social",
            )
        ]

        def should_not_fetch(*_args, **_kwargs):
            raise AssertionError("official X binding should defer page proof to screenshot validation")

        stats = reconcile_official_sources([item], sources, fetcher=should_not_fetch)

        record = item.raw["official_reconciliation"]
        self.assertEqual(stats["matched_social"], 1)
        self.assertEqual(record["status"], "matched_social")
        self.assertEqual(record["reliability"], "official_social")

    def test_full_x_status_url_label_does_not_replace_story_title(self):
        """完整 status URL 只是退化链接文字，不能进入官方标题或事实。"""
        status_url = "https://x.com/Alibaba_Qwen/status/2078759124914098291"
        item = media_item([{"url": status_url, "text": status_url}])
        item.title = "Qwen3.8 Max 预览版开放测试"
        sources = [
            source(
                source_id="x_codex_official_leads",
                url="data/codex-social-leads.json#Alibaba_Qwen",
                source_type="agent_social",
                reliability="official_social",
            )
        ]

        reconcile_official_sources(
            [item],
            sources,
            now=datetime(2026, 7, 19, 17, 0, tzinfo=UTC),
            fetcher=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("X 原帖不应走网页抓取")),
        )

        record = item.raw["official_reconciliation"]
        self.assertEqual(record["title"], item.title)
        self.assertEqual(record["excerpt"], item.title)
        self.assertEqual(record["facts"], [])

    def test_fresh_kimi_media_cannot_freshen_stale_official_x_post(self):
        """媒体新稿只能发现旧官方帖，不能篡改旧帖时间或把事件判成 green。"""
        status_url = "https://x.com/Kimi_Moonshot/status/2077830229968683203"
        infoq = media_item([{"url": status_url, "text": "x.com"}])
        infoq.title = "Kimi K3 当日登陆模型广场"
        infoq.summary = "InfoQ 报道 Kimi K3 的参数、定价和模型广场上架信息。"
        infoq.published_at = datetime(2026, 7, 18, 17, 14, 5, tzinfo=UTC)
        # 模拟当前 SQLite 中由旧实现留下的污染记录，确保同日重跑会自动纠正。
        infoq.raw["official_reconciliation"] = {
            "status": "matched_social",
            "url": status_url,
            "final_url": status_url,
            "title": "x.com",
            "published_at": infoq.published_at.isoformat(),
            "freshness": "fresh",
            "reliability": "official_social",
        }
        google_news = FeedItem(
            source_id="google_news_ai_cn",
            source_name="Google News AI 中文搜索",
            source_tier="B",
            source_reliability="media",
            title="Kimi K3 引发市场关注",
            link="https://news.example/kimi-k3",
            guid="kimi-k3-google-news",
            summary="媒体在 7 月 19 日继续报道 Kimi K3，但官方原帖发布于更早时间。",
            published_at=datetime(2026, 7, 19, 12, 46, 31, tzinfo=UTC),
        )
        sources = [
            source(
                source_id="x_codex_official_leads",
                url="data/codex-social-leads.json#Kimi_Moonshot",
                source_type="agent_social",
                reliability="official_social",
            )
        ]
        now = datetime(2026, 7, 19, 16, 11, tzinfo=UTC)

        stats = reconcile_official_sources(
            [infoq, google_news],
            sources,
            lookback_hours=24,
            now=now,
            fetcher=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("X 原帖不应走网页抓取")),
        )

        record = infoq.raw["official_reconciliation"]
        self.assertEqual(stats["stale"], 1)
        self.assertEqual(record["published_at"], "2026-07-16T18:58:25.700000+00:00")
        self.assertEqual(record["published_at_provenance"], "x_status_snowflake")
        self.assertEqual(record["freshness"], "stale")
        self.assertNotEqual(record["title"].lower(), "x.com")
        self.assertEqual(record["facts"], [])

        with patch("briefing.verify.now_utc", return_value=now):
            card = verify_clusters(cluster_items([infoq, google_news]), lookback_hours=24)[0]

        self.assertEqual(card.risk, "red")
        self.assertFalse(card.selected)
        self.assertEqual(card.latest_published_at, datetime(2026, 7, 16, 18, 58, 25, 700000, tzinfo=UTC))
        self.assertNotEqual(card.event_title.lower(), "x.com")

    def test_undated_reconciled_social_post_stays_conservative(self):
        """异常的未来 Snowflake 按无日期处理，fresh media 也只能得到 yellow。"""
        future_time = datetime(2030, 1, 1, tzinfo=UTC)
        future_id = ((int(future_time.timestamp() * 1000) - 1_288_834_974_657) << 22)
        item = media_item([{"url": f"https://x.com/OpenAI/status/{future_id}", "text": "OpenAI update"}])
        item.title = "OpenAI 发布新的 AI 产品更新"
        item.published_at = datetime(2026, 7, 19, 12, 46, 31, tzinfo=UTC)
        sources = [
            source(
                source_id="x_codex_official_leads",
                url="data/codex-social-leads.json#OpenAI",
                source_type="agent_social",
                reliability="official_social",
            )
        ]
        now = datetime(2026, 7, 19, 16, 11, tzinfo=UTC)

        reconcile_official_sources([item], sources, lookback_hours=24, now=now)
        record = item.raw["official_reconciliation"]
        self.assertEqual(record["published_at"], "")
        self.assertEqual(record["freshness"], "undated")

        with patch("briefing.verify.now_utc", return_value=now):
            card = verify_clusters(cluster_items([item]), lookback_hours=24)[0]

        self.assertEqual(card.risk, "yellow")
        self.assertTrue(card.selected)

    def test_trusted_media_second_hop_finds_stale_first_party_event(self):
        item = media_item(
            [
                {
                    "url": "https://techcrunch.com/2026/07/09/meta-muse-spark-1-1/",
                    "text": "Meta launches Muse Spark 1.1",
                }
            ]
        )
        item.title = "Meta 发布新版 Muse Spark"
        item.summary = "Muse Spark 1.1 是面向智能体编程的多模态模型。"
        sources = [source(source_id="meta_official", url="https://about.fb.com/news/")]

        def fake_fetcher(url: str, **_kwargs) -> ArticleResult:
            if "techcrunch.com" in url:
                return ArticleResult(
                    status="ok",
                    url=url,
                    final_url=url,
                    title="Meta launches Muse Spark 1.1",
                    text="Meta launched Muse Spark 1.1 for agentic coding.",
                    links=[
                        {
                            "url": "https://about.fb.com/news/2026/04/introducing-muse-spark-meta-superintelligence-labs/",
                            "text": "Introducing Muse Spark",
                        }
                    ],
                )
            return ArticleResult(
                status="ok",
                url=url,
                final_url=url,
                title="Introducing Muse Spark",
                text="Meta introduces the Muse Spark model for agentic work.",
                facts=["Muse Spark is a Meta model for agentic work."],
                published_at=datetime(2026, 4, 15, tzinfo=UTC),
            )

        stats = reconcile_official_sources(
            [item],
            sources,
            lookback_hours=24,
            now=datetime(2026, 7, 13, 6, tzinfo=UTC),
            fetcher=fake_fetcher,
        )

        record = item.raw["official_reconciliation"]
        self.assertEqual(stats["secondary_attempted"], 1)
        self.assertEqual(stats["secondary_resolved"], 1)
        self.assertEqual(stats["stale"], 1)
        self.assertEqual(record["freshness"], "stale")
        self.assertIn("about.fb.com", record["url"])

    def test_failed_unrelated_official_link_falls_back_to_stale_original_report(self):
        item = media_item(
            [
                {
                    "url": "https://deploymentsafety.openai.com/gpt-5-6/gpt-5-6.pdf",
                    "text": "GPT-5.6 safety report",
                },
                {
                    "url": "https://www.wired.com/story/openai-head-of-safety-leaving/",
                    "text": "WIRED original report",
                },
            ]
        )
        item.title = "GPT-5.6刚发布，OpenAI安全主管将离职"
        item.summary = "Johannes Heidecke 将离开 OpenAI，WIRED 报道了安全团队重组。"
        sources = [source(source_id="openai_official", url="https://openai.com/news/")]

        def fake_fetcher(url: str, **_kwargs) -> ArticleResult:
            if "deploymentsafety.openai.com" in url:
                return ArticleResult(
                    status="unsupported_content",
                    url=url,
                    final_url=url,
                    title="GPT-5.6 safety report",
                    text="",
                )
            return ArticleResult(
                status="ok",
                url=url,
                final_url=url,
                title="OpenAI's Head of Safety Is Leaving the Company",
                text="Johannes Heidecke is leaving OpenAI as its safety and research teams are reorganized.",
                facts=["Johannes Heidecke is leaving OpenAI."],
                links=[
                    {
                        "url": "https://deploymentsafety.openai.com/gpt-5-6/background",
                        "text": "GPT-5.6 background",
                    }
                ],
                published_at=datetime(2026, 7, 11, 1, 7, tzinfo=UTC),
            )

        stats = reconcile_official_sources(
            [item],
            sources,
            lookback_hours=24,
            now=datetime(2026, 7, 13, 20, 37, tzinfo=UTC),
            fetcher=fake_fetcher,
        )

        record = item.raw["official_reconciliation"]
        self.assertEqual(stats["secondary_attempted"], 1)
        self.assertEqual(stats["secondary_resolved"], 1)
        self.assertEqual(stats["stale"], 1)
        self.assertEqual(record["status"], "matched_origin_media")
        self.assertEqual(record["reliability"], "primary_media")
        self.assertEqual(record["freshness"], "stale")
        self.assertIn("wired.com", record["url"])


if __name__ == "__main__":
    unittest.main()

import unittest
from datetime import datetime, timezone

from briefing.cluster import cluster_items
from briefing.models import FeedItem


UTC = timezone.utc


def item(*, source_id: str, title: str, summary: str, published_at, fetched_at: datetime, kind: str = "") -> FeedItem:
    return FeedItem(
        source_id=source_id,
        source_name="OpenAI News",
        source_tier="A",
        source_reliability="official",
        title=title,
        link=f"https://openai.com/{source_id}",
        guid=source_id,
        summary=summary,
        published_at=published_at,
        fetched_at=fetched_at,
        raw={"kind": kind} if kind else {},
    )


class ClusterPriorityTests(unittest.TestCase):
    def test_circleci_headline_is_not_relabelled_by_background_claude_mentions(self):
        article = item(
            source_id="infoq-circleci",
            title="Circle CI推出Chunk Sidecars,将CI校验直接引入AI编码工作流",
            summary="文章对比 GitHub Copilot 与 Anthropic Claude Code 的工具校验流程。",
            published_at=datetime(2026, 7, 13, 3, tzinfo=UTC),
            fetched_at=datetime(2026, 7, 13, 6, tzinfo=UTC),
        )

        cluster = cluster_items([article])[0]

        self.assertEqual(cluster.entity, "CircleCI")

    def test_dated_article_leads_over_freshly_fetched_undated_listing(self):
        listing = item(
            source_id="openai_official_web",
            title="GPT-5.6 is now the preferred model in Microsoft 365 Copilot",
            summary="OpenAI 新闻动态；全部；筛选；切换卡片；加载更多",
            published_at=None,
            fetched_at=datetime(2026, 7, 10, 8, 0, tzinfo=UTC),
            kind="official_web_discovery",
        )
        article = item(
            source_id="openai_news",
            title="GPT-5.6 is now the preferred model in Microsoft 365 Copilot",
            summary="GPT-5.6 powers Microsoft 365 Copilot across Word and Excel.",
            published_at=datetime(2026, 7, 9, 13, 0, tzinfo=UTC),
            fetched_at=datetime(2026, 7, 10, 7, 0, tzinfo=UTC),
        )

        clusters = cluster_items([listing, article])

        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].items[0].source_id, "openai_news")
        self.assertEqual(clusters[0].items[0].summary, article.summary)

    def test_gpt_56_cross_source_reports_merge_into_one_event(self):
        official = item(
            source_id="openai_news",
            title="GPT-5.6: Frontier intelligence that scales with your ambition",
            summary="Stronger performance per dollar.",
            published_at=datetime(2026, 7, 9, 10, 0, tzinfo=UTC),
            fetched_at=datetime(2026, 7, 10, 7, 0, tzinfo=UTC),
        )
        media = item(
            source_id="infoq_cn",
            title="GPT-5.6 全面发布，程序化工具调用降低 Token 消耗",
            summary="Responses API 新增程序化工具调用。",
            published_at=datetime(2026, 7, 10, 6, 0, tzinfo=UTC),
            fetched_at=datetime(2026, 7, 10, 7, 0, tzinfo=UTC),
        )
        media.source_name = "InfoQ 中文"
        media.source_tier = "B"
        media.source_reliability = "media"

        clusters = cluster_items([official, media])

        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].entity, "OpenAI")
        self.assertEqual({x.source_id for x in clusters[0].items}, {"openai_news", "infoq_cn"})
        self.assertEqual(clusters[0].items[0].source_id, "openai_news")
        self.assertEqual(clusters[0].title, official.title)

    def test_media_deep_dive_merges_into_gpt_56_launch(self):
        official = item(
            source_id="openai_news",
            title="GPT-5.6: Frontier intelligence that scales with your ambition",
            summary="Stronger performance per dollar.",
            published_at=datetime(2026, 7, 9, 10, 0, tzinfo=UTC),
            fetched_at=datetime(2026, 7, 10, 7, 0, tzinfo=UTC),
        )
        media = item(
            source_id="infoq_cn",
            title="GPT-5.6全面围剿Claude Fable 5，程序化工具调用成为重点",
            summary="GPT-5.6 新增程序化工具调用，可减少 Token 消耗。",
            published_at=datetime(2026, 7, 10, 6, 0, tzinfo=UTC),
            fetched_at=datetime(2026, 7, 10, 7, 0, tzinfo=UTC),
        )
        media.source_name = "InfoQ 中文"
        media.source_tier = "B"
        media.source_reliability = "media"

        clusters = cluster_items([official, media])

        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].items[0].source_id, "openai_news")

    def test_gpt_56_product_integrations_are_not_folded_into_model_launch(self):
        launch = item(
            source_id="openai_news",
            title="GPT-5.6: Frontier intelligence that scales with your ambition",
            summary="Stronger performance per dollar.",
            published_at=datetime(2026, 7, 9, 10, 0, tzinfo=UTC),
            fetched_at=datetime(2026, 7, 10, 7, 0, tzinfo=UTC),
        )
        copilot = item(
            source_id="github_changelog",
            title="OpenAI’s GPT-5.6 Sol, Terra, and Luna are now available in GitHub Copilot",
            summary="Three GPT-5.6 variants are rolling out in Copilot.",
            published_at=datetime(2026, 7, 9, 16, 0, tzinfo=UTC),
            fetched_at=datetime(2026, 7, 10, 7, 0, tzinfo=UTC),
        )

        self.assertEqual(len(cluster_items([launch, copilot])), 2)

    def test_model_generated_math_proof_is_not_folded_into_model_launch(self):
        launch = item(
            source_id="openai_news",
            title="GPT-5.6: Frontier intelligence that scales with your ambition",
            summary="GPT-5.6 adds programmatic tool calling.",
            published_at=datetime(2026, 7, 9, 10, 0, tzinfo=UTC),
            fetched_at=datetime(2026, 7, 10, 7, 0, tzinfo=UTC),
        )
        proof = item(
            source_id="hackernews_frontpage",
            title="GPT-5.6 Sol Ultra produces proof of the Cycle Double Cover Conjecture [pdf]",
            summary="A generated proof is discussed as a separate research result.",
            published_at=datetime(2026, 7, 10, 6, 0, tzinfo=UTC),
            fetched_at=datetime(2026, 7, 10, 7, 0, tzinfo=UTC),
        )
        proof.source_name = "Hacker News Frontpage"
        proof.source_tier = "C"
        proof.source_reliability = "community"

        self.assertEqual(len(cluster_items([launch, proof])), 2)

    def test_chatgpt_work_is_not_merged_into_generic_gpt_56_launch(self):
        work = item(
            source_id="openai_news",
            title="ChatGPT is now a partner for your most ambitious work",
            summary="ChatGPT Work uses GPT-5.6 to execute workflows.",
            published_at=datetime(2026, 7, 9, 10, 0, tzinfo=UTC),
            fetched_at=datetime(2026, 7, 10, 7, 0, tzinfo=UTC),
        )
        launch = item(
            source_id="infoq_cn",
            title="OpenAI 发布 GPT-5.6 系列模型",
            summary="GPT-5.6 新增程序化工具调用。",
            published_at=datetime(2026, 7, 10, 6, 0, tzinfo=UTC),
            fetched_at=datetime(2026, 7, 10, 7, 0, tzinfo=UTC),
        )

        self.assertEqual(len(cluster_items([work, launch])), 2)

    def test_personnel_story_is_not_folded_into_model_launch_context(self):
        personnel = item(
            source_id="qbitai",
            title="GPT-5.6 \u521a\u53d1\u5e03,OpenAI \u5b89\u5168\u7cfb\u7edf\u8d1f\u8d23\u4eba Johannes Heidecke \u5c06\u79bb\u804c",
            summary="OpenAI \u5b89\u5168\u56e2\u961f\u6b63\u5728\u91cd\u7ec4\u3002",
            published_at=datetime(2026, 7, 13, 6, 0, tzinfo=UTC),
            fetched_at=datetime(2026, 7, 13, 7, 0, tzinfo=UTC),
        )
        product = item(
            source_id="codex-personnel",
            title="GPT-5.6 Sol \u5c06\u7ee7\u7eed\u5305\u542b\u5728\u4ed8\u8d39 ChatGPT \u8ba2\u9605\u4e2d",
            summary="GPT-5.6 Sol remains available to paid subscribers.",
            published_at=datetime(2026, 7, 13, 0, 0, tzinfo=UTC),
            fetched_at=datetime(2026, 7, 13, 7, 0, tzinfo=UTC),
        )

        self.assertEqual(len(cluster_items([personnel, product])), 2)

    def test_generic_nova_model_launch_merges_without_product_hardcoding(self):
        official = item(
            source_id="acme_official",
            title="Acme launches Nova-7 frontier model",
            summary="Nova-7 adds 256K context.",
            published_at=datetime(2026, 7, 9, 10, 0, tzinfo=UTC),
            fetched_at=datetime(2026, 7, 10, 7, 0, tzinfo=UTC),
        )
        media = item(
            source_id="media",
            title="Nova-7 正式发布，新增 256K 上下文",
            summary="Acme 的 Nova-7 已发布。",
            published_at=datetime(2026, 7, 9, 12, 0, tzinfo=UTC),
            fetched_at=datetime(2026, 7, 10, 7, 0, tzinfo=UTC),
        )
        official.source_name = "Acme"
        media.source_name = "Media"
        media.source_tier = "B"
        media.source_reliability = "media"

        clusters = cluster_items([official, media])

        self.assertEqual(len(clusters), 1)
        self.assertIn("nova-7", clusters[0].key.lower())

    def test_generic_nova_integration_stays_separate_from_launch(self):
        launch = item(
            source_id="acme_official", title="Acme launches Nova-7 frontier model", summary="Nova-7 adds 256K context.",
            published_at=datetime(2026, 7, 9, 10, 0, tzinfo=UTC), fetched_at=datetime(2026, 7, 10, 7, 0, tzinfo=UTC),
        )
        integration = item(
            source_id="partner", title="Nova-7 接入 Office Suite", summary="Nova-7 is now available in Office Suite.",
            published_at=datetime(2026, 7, 9, 13, 0, tzinfo=UTC), fetched_at=datetime(2026, 7, 10, 7, 0, tzinfo=UTC),
        )

        self.assertEqual(len(cluster_items([launch, integration])), 2)

    def test_nvidia_developer_blog_gets_nvidia_entity(self):
        article = item(
            source_id="nvidia_developer_blog",
            title="Build an AI Scientist with BioNeMo Agent Toolkit",
            summary="Agents can call scientific APIs.",
            published_at=datetime(2026, 7, 9, 18, 0, tzinfo=UTC),
            fetched_at=datetime(2026, 7, 10, 7, 0, tzinfo=UTC),
        )
        article.source_name = "NVIDIA Developer Blog"
        article.link = "https://developer.nvidia.com/blog/benchmark-update"

        self.assertEqual(cluster_items([article])[0].entity, "NVIDIA")

    def test_generic_nvidia_benchmark_is_not_mislabeled_as_arxiv(self):
        article = item(
            source_id="generic_official_feed",
            title="NVIDIA Blackwell benchmark update for inference",
            summary="NVIDIA reports an inference benchmark update.",
            published_at=datetime(2026, 7, 9, 18, 0, tzinfo=UTC),
            fetched_at=datetime(2026, 7, 10, 7, 0, tzinfo=UTC),
        )
        article.source_name = "NVIDIA Developer Blog"
        article.link = "https://developer.nvidia.com/blog/benchmark-update"

        self.assertEqual(cluster_items([article])[0].entity, "NVIDIA")


if __name__ == "__main__":
    unittest.main()

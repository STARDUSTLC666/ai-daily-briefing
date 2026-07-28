import unittest
from datetime import datetime, timezone

from briefing.enrichment_planner import plan_enrichment_items
from briefing.models import FeedItem


UTC = timezone.utc


def item(idx: int, *, source_id: str, source_name: str, tier: str, reliability: str, title: str, link: str, summary: str = "") -> FeedItem:
    return FeedItem(
        source_id=source_id,
        source_name=source_name,
        source_tier=tier,
        source_reliability=reliability,
        title=title,
        link=link,
        guid=f"g-{idx}",
        summary=summary,
        published_at=datetime(2026, 7, 10, idx % 20, tzinfo=UTC),
    )


class EnrichmentPlannerTests(unittest.TestCase):
    def test_cluster_first_plan_prefers_launches_and_cross_source_events(self):
        rows = [
            item(1, source_id="openai", source_name="OpenAI", tier="A", reliability="official", title="OpenAI 发布 GPT-5.6", link="https://openai.com/gpt-5-6"),
            item(2, source_id="infoq", source_name="InfoQ", tier="B", reliability="media", title="GPT-5.6 正式发布", link="https://infoq.cn/gpt-5-6"),
            item(3, source_id="generic", source_name="Generic", tier="B", reliability="media", title="AI 公司完成融资", link="https://news.example/funding"),
            item(4, source_id="github", source_name="GitHub", tier="A", reliability="official", title="GitHub Copilot 新增仓库概览", link="https://github.blog/repo-overview"),
        ]

        selected, diagnostics = plan_enrichment_items(rows, target_stories=1, multiplier=2, max_items=2)

        titles = [row.title for row in selected]
        self.assertEqual(len(selected), 2)
        self.assertIn("OpenAI 发布 GPT-5.6", titles)
        self.assertIn("GPT-5.6 正式发布", titles)
        self.assertNotIn("AI 公司完成融资", titles)
        self.assertEqual(diagnostics["mode"], "cluster_first_candidate_enrichment")
        self.assertEqual(diagnostics["selected_clusters"], 1)

    def test_entity_cap_prevents_one_vendor_from_consuming_budget(self):
        rows = []
        for idx in range(1, 8):
            rows.append(item(idx, source_id=f"openai-{idx}", source_name="OpenAI", tier="A", reliability="official", title=f"OpenAI 发布 GPT-{idx}.1", link=f"https://openai.com/gpt-{idx}"))
        rows.append(item(10, source_id="qwen", source_name="Qwen", tier="A", reliability="official", title="Qwen 发布 Qwen4", link="https://qwen.ai/qwen4"))

        selected, diagnostics = plan_enrichment_items(rows, target_stories=2, multiplier=3, max_items=6, max_per_entity=2)

        self.assertLessEqual(sum("OpenAI" in row.title for row in selected), 2)
        self.assertTrue(any("Qwen" in row.title for row in selected))
        self.assertGreaterEqual(diagnostics["rejections"].get("entity_cap", 0), 1)

    def test_budget_is_target_times_multiplier(self):
        rows = [item(idx, source_id=f"s{idx}", source_name=f"S{idx}", tier="A", reliability="official", title=f"Vendor{idx} 发布 Model-{idx}.1", link=f"https://v{idx}.example/model") for idx in range(1, 30)]

        selected, diagnostics = plan_enrichment_items(rows, target_stories=3, multiplier=4)

        self.assertEqual(len(selected), 12)
        self.assertEqual(diagnostics["budget"], 12)

    def test_noise_and_future_dates_do_not_consume_crawl_budget(self):
        rows = [
            item(1, source_id="official", source_name="Official", tier="A", reliability="official", title="OpenAI weekly digest GPT-5.6", link="https://openai.com/digest"),
            item(2, source_id="future", source_name="Future", tier="A", reliability="official", title="Qwen 发布 Qwen4", link="https://qwen.ai/qwen4"),
            item(3, source_id="usable", source_name="Usable", tier="A", reliability="official", title="DeepSeek 发布 DeepSeek-V4", link="https://deepseek.com/v4"),
        ]
        rows[1].published_at = datetime(2026, 7, 11, 12, tzinfo=UTC)

        selected, diagnostics = plan_enrichment_items(
            rows,
            target_stories=2,
            multiplier=1,
            now=datetime(2026, 7, 10, 9, tzinfo=UTC),
        )

        self.assertEqual([row.source_id for row in selected], ["usable"])
        self.assertGreaterEqual(diagnostics["rejections"].get("non_positive_score", 0), 2)

    def test_domain_and_high_volume_caps_leave_room_for_other_candidates(self):
        rows = [
            item(1, source_id="arxiv_cs_ai", source_name="arXiv", tier="A", reliability="official", title="Qwen 发布 Qwen4", link="https://arxiv.org/a"),
            item(2, source_id="arxiv_cs_ai", source_name="arXiv", tier="A", reliability="official", title="DeepSeek 发布 DeepSeek-V4", link="https://arxiv.org/b"),
            item(3, source_id="official", source_name="Official", tier="A", reliability="official", title="MiniMax 发布 MiniMax-M3", link="https://example.com/a"),
            item(4, source_id="official-2", source_name="Official 2", tier="A", reliability="official", title="Kimi 发布 Kimi-K3", link="https://another.example/b"),
        ]

        selected, diagnostics = plan_enrichment_items(
            rows,
            target_stories=3,
            multiplier=1,
            max_per_high_volume_source=1,
            max_per_domain=1,
        )

        self.assertLessEqual(sum(row.source_id == "arxiv_cs_ai" for row in selected), 1)
        self.assertLessEqual(sum("example.com" in row.link for row in selected), 1)
        self.assertIn("candidate_urls", diagnostics["budget_unit"])

    def test_concrete_public_ai_policy_wins_a_tight_32_item_crawl_budget(self):
        """完整公共 AI 政策应先抓正文，避免只剩 RSS 数字摘要。"""
        policy = item(
            0,
            source_id="36kr",
            source_name="36氪",
            tier="B",
            reliability="media",
            title="“人工智能+防震减灾”行动方案发布",
            link="https://36kr.com/newsflashes/plan",
            summary=(
                "中国地震局发布《人工智能+防震减灾行动方案(2026—2028年)》。"
                "未来3年将围绕智能监测处理、智能预警研发与示范等九方面任务展开。"
            ),
        )
        competitors = [
            item(
                idx,
                source_id=f"official-{idx}",
                source_name=f"Official {idx}",
                tier="A",
                reliability="official",
                title=f"Vendor{idx} 发布 Model-{idx}.1",
                link=f"https://vendor{idx}.example/model",
            )
            for idx in range(1, 33)
        ]

        selected, diagnostics = plan_enrichment_items(
            [*competitors, policy],
            target_stories=32,
            multiplier=1,
            max_items=32,
            now=datetime(2026, 7, 11, tzinfo=UTC),
        )

        self.assertEqual(len(selected), 32)
        self.assertIn(policy, selected)
        policy_audit = next(row for row in diagnostics["top_candidates"] if row["title"] == policy.title)
        self.assertIn("concrete_public_ai_policy", policy_audit["reasons"])

    def test_company_action_plan_does_not_receive_public_policy_priority(self):
        """企业局部行动方案即使有周期和任务，也不属于公共政策。"""
        company_plan = item(
            1,
            source_id="media",
            source_name="Media",
            tier="B",
            reliability="media",
            title="某公司中国总部发布人工智能行动方案",
            link="https://media.example/company-plan",
            summary=(
                "未来3年将在部分业务推进智能监测、模型训练和智能预警等3方面任务，"
                "并与中国地震局合作开展示范应用。"
            ),
        )
        official_release = item(
            2,
            source_id="official",
            source_name="Official",
            tier="A",
            reliability="official",
            title="Qwen 发布 Qwen4.1",
            link="https://qwen.example/qwen4-1",
        )

        selected, diagnostics = plan_enrichment_items(
            [company_plan, official_release],
            target_stories=1,
            multiplier=1,
            max_items=1,
            now=datetime(2026, 7, 11, tzinfo=UTC),
        )

        self.assertEqual(selected, [official_release])
        reasons = [reason for row in diagnostics["top_candidates"] for reason in row["reasons"]]
        self.assertNotIn("concrete_public_ai_policy", reasons)


if __name__ == "__main__":
    unittest.main()

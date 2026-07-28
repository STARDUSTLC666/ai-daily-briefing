import unittest
from datetime import datetime, timezone

from briefing.models import EvidenceCard
from briefing.pipeline import _ticker_candidates
from briefing.render import _segments
from briefing.storyboard import _specific_beat_title, strip_card_attribution


UTC = timezone.utc


def card(key: str, *, entity: str = "OpenAI", risk: str = "green", official: int = 1, score: float = 80.0, facts: list[str] | None = None, selected: bool = True) -> EvidenceCard:
    return EvidenceCard(
        cluster_key=key,
        event_title=f"{entity} 发布 {key} 更新",
        entity=entity,
        risk=risk,
        confidence=90,
        selected=selected,
        reason="官方来源确认。",
        source_count=1,
        official_count=official,
        media_count=0 if official else 1,
        community_count=0,
        first_seen_at=datetime(2026, 7, 25, 8, tzinfo=UTC),
        latest_published_at=datetime(2026, 7, 25, 8, tzinfo=UTC),
        key_facts=facts if facts is not None else [f"{entity} 表示，{key} 已面向全部用户开放。"],
        evidence_links=[{"source": f"{entity} News", "source_name": f"{entity} News", "tier": "A", "reliability": "official", "title": key, "url": f"https://example.com/{key}", "excerpt": "官方更新。"}],
        uncertainty=[],
        score=score,
    )


class CardAttributionStripTests(unittest.TestCase):
    def test_first_party_attribution_is_stripped_from_card_copy(self):
        self.assertEqual(
            strip_card_attribution("Anthropic 表示，新研究分析了超过 30 万条匿名对话。"),
            "新研究分析了超过 30 万条匿名对话。",
        )
        self.assertEqual(
            strip_card_attribution("OpenAI 官方账号表示，ChatGPT 已重新在欧洲经济区可用。"),
            "ChatGPT 已重新在欧洲经济区可用。",
        )

    def test_hedged_relays_keep_their_risk_labels(self):
        for text in [
            "据 36氪 报道，字节正探索自动驾驶。",
            "36氪获悉，千问3.8即将发布并开源。",
            "字节向 36氪 回应称，公司没有相关计划。",
            "消息人士表示，该项目仍在早期阶段。",
        ]:
            with self.subTest(text=text):
                self.assertEqual(strip_card_attribution(text), text)

    def test_short_remainders_are_never_stripped(self):
        text = "OpenAI 表示，已修复。"
        self.assertEqual(strip_card_attribution(text), text)


class SpecificBeatTitleTests(unittest.TestCase):
    def test_number_kernel_replaces_generic_label(self):
        title = _specific_beat_title("核心信息", "新研究分析了超过 30 万条匿名对话，用于比较价值表达。")
        self.assertIn("30 万条", title)
        self.assertNotEqual(title, "核心信息")

    def test_deadline_kernel_replaces_generic_label(self):
        title = _specific_beat_title("方法与结果", "OpenAI Build Week 的项目投稿 7 月 21 日截止。")
        self.assertIn("7 月 21 日", title)

    def test_specific_labels_and_fuzzy_bodies_stay_unchanged(self):
        self.assertEqual(_specific_beat_title("开放范围", "已面向 100 万用户开放。"), "开放范围")
        self.assertEqual(_specific_beat_title("核心信息", "价值表达会随对话语言变化。"), "核心信息")


class HeadlineDepthTests(unittest.TestCase):
    """Headline stories may surface up to six verified claims; briefs stay lean."""

    def _rich_card(self, tier: str) -> EvidenceCard:
        rich = card(
            "deep-story",
            facts=[
                "OpenAI 表示，GPT-5.6 Sol 在编码基准测试中拿到 53.6 分。",
                "OpenAI 表示，新增 Ultra 模式默认协调 4 个 Agent 并行处理复杂任务。",
                "OpenAI 表示，上下文窗口扩展到 100 万 token，支持全仓库检索。",
                "OpenAI 表示，API 价格下调 30%，输入每百万 token 收费 1.2 美元。",
                "OpenAI 表示，桌面端向全部 Plus 用户开放，移动端下周上线。",
                "OpenAI 表示，旧版模型将保留 90 天迁移窗口，之后自动升级。",
            ],
        )
        rich.editorial_tier = tier
        # A single excerpt-free link engages the single-source evidence
        # fallback, so every synthetic fact becomes a verifiable claim.
        rich.evidence_links = [
            {key: value for key, value in rich.evidence_links[0].items() if key != "excerpt"}
        ]
        return rich

    def test_headline_tier_feeds_up_to_six_claims_into_cards(self):
        from briefing.editorial_plan import build_editorial_plan
        from briefing.storyboard import build_storyboard

        headline_plan = build_editorial_plan(self._rich_card("headline"))
        brief_plan = build_editorial_plan(self._rich_card("brief"))

        self.assertGreaterEqual(len(headline_plan.facts), 5)
        self.assertLessEqual(len(brief_plan.facts), 4)
        self.assertGreater(len(headline_plan.facts), len(brief_plan.facts))

        board = build_storyboard(self._rich_card("headline"), plan=headline_plan)
        fact_beats = [beat for beat in board.beats if beat.intent not in {"impact", "caution"}]
        self.assertGreaterEqual(len(fact_beats), 5)


class TickerCandidateTests(unittest.TestCase):
    def test_only_green_official_unselected_cards_enter_the_digest(self):
        chosen = card("main-1", score=95)
        green_official = card("digest-1", score=90)
        yellow = card("digest-2", risk="yellow", score=88)
        media_only = card("digest-3", official=0, score=86)
        unverified = card("digest-4", selected=False, score=84)

        picked = _ticker_candidates(
            [chosen, green_official, yellow, media_only, unverified],
            [chosen],
            max_items=10,
        )

        self.assertEqual([entry.cluster_key for entry in picked], ["digest-1"])

    def test_merged_portfolio_members_never_reappear(self):
        merged = card("merged", score=95)
        merged.cluster_key = "merged:family"
        merged.source_cluster_keys = ["digest-1"]
        shadow = card("digest-1", score=90)

        picked = _ticker_candidates([merged, shadow], [merged], max_items=10)

        self.assertEqual(picked, [])

    def test_cap_and_ordering(self):
        selected = card("main", score=99)
        pool = [card(f"d{i}", score=50 + i) for i in range(6)]

        picked = _ticker_candidates([selected, *pool], [selected], max_items=3)

        self.assertEqual([entry.cluster_key for entry in picked], ["d5", "d4", "d3"])


class TickerSegmentTests(unittest.TestCase):
    def test_ticker_segment_sits_before_outro_and_reuses_overview_pages(self):
        main = card("main-story", score=95)
        digest = [card(f"d{i}", score=70 + i, facts=[f"OpenAI 表示，功能 {i} 已面向全部用户开放。"]) for i in range(8)]

        rows = _segments([main], ticker_cards=digest)

        kinds = [row["kind"] for row in rows]
        self.assertIn("ticker", kinds)
        self.assertEqual(kinds[-1], "outro")
        self.assertEqual(kinds[-2], "ticker")
        ticker = rows[kinds.index("ticker")]
        self.assertTrue(ticker["text"].startswith("最后是快讯速览。"))
        # De-attributed one-liners, dispatch pages of up to ten rows.
        self.assertNotIn("表示", ticker["text"])
        self.assertEqual([page["kind"] for page in ticker["visual_pages"]], ["ticker"])
        self.assertEqual(len(ticker["visual_pages"][0]["cards"]), 8)

    def test_no_ticker_cards_means_no_ticker_segment(self):
        rows = _segments([card("solo")])

        self.assertNotIn("ticker", [row["kind"] for row in rows])


if __name__ == "__main__":
    unittest.main()

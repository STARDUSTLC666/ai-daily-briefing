import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from briefing.editorial_plan import EditorialPlan
from briefing.models import EvidenceCard
from briefing.output_verify import _automatic_news_content_errors, repairable_news_positions
from briefing.writer import write_bilibili_md


UTC = timezone.utc


def news_segment(index: int, title: str, narration: str, tier: str = "brief") -> dict:
    return {
        "kind": "news",
        "position": index,
        "title": title,
        "caption": title,
        "text": narration,
        "editorial_tier": tier,
        "cards": [{"title": "核心信息", "body": narration[:60]}],
    }


def card(idx: int, source: str, entity: str, title: str, facts: list[str]) -> EvidenceCard:
    return EvidenceCard(
        cluster_key=f"k{idx}",
        event_title=title,
        entity=entity,
        risk="green",
        confidence=90,
        selected=True,
        reason="官方来源确认。",
        source_count=1,
        official_count=1,
        media_count=0,
        community_count=0,
        first_seen_at=datetime(2026, 7, 19, tzinfo=UTC),
        latest_published_at=datetime(2026, 7, 19, tzinfo=UTC),
        key_facts=facts,
        evidence_links=[{"source": source, "source_name": source, "tier": "A", "reliability": "official", "title": title, "url": f"https://example.com/{idx}", "published_at": ""}],
        uncertainty=[],
        score=90,
    )


class CopyHygieneGateTests(unittest.TestCase):
    """Regression tests built from the real 2026-07-20 unattended incident."""

    def test_bare_domain_headline_from_2026_07_20_is_rejected(self):
        segment = news_segment(
            1,
            "Kimi开源x.com",
            "公开资料显示,Kimi开源x.com。不过还是比 Anthropic 家的模型便宜很多,差不多是三成价格。",
        )

        errors = _automatic_news_content_errors([segment])

        self.assertTrue(any("title contains a bare domain: x.com" in e for e in errors))

    def test_colloquial_scraped_copy_from_2026_07_20_is_rejected(self):
        segment = news_segment(
            1,
            "Kimi 发布 K3 模型价格调整",
            "实话实说啊,这个价格,确实比上一代翻了快一倍。对用户和团队来说,关键变化是智能体走向跨应用执行工作。",
        )

        errors = _automatic_news_content_errors([segment])

        self.assertTrue(any("contains colloquial scraped copy" in e for e in errors))

    def test_colloquial_particle_alone_is_rejected(self):
        segment = news_segment(
            1,
            "Qwen 更新价格方案",
            "这个方案便宜啊,新增了 200 万 token 的免费额度,面向所有注册用户开放。",
        )

        errors = _automatic_news_content_errors([segment])

        self.assertTrue(any("contains colloquial scraped copy" in e for e in errors))

    def test_too_short_title_is_rejected_but_signal_prefix_is_not_counted(self):
        short = news_segment(1, "上新了", "官方宣布新增 3 项能力,均已开放使用,覆盖全部付费用户。")
        signal = news_segment(
            2,
            "一线消息|Codex 重置全体账户储备用量",
            "一线消息,未获官方公告确认。公开资料显示,Codex 向全体账户发放一次储备用量重置,活跃用户达到 700 万。",
        )

        errors = _automatic_news_content_errors([short, signal])

        self.assertTrue(any("news segment 1 title is too short" in e for e in errors))
        self.assertFalse(any("news segment 2 title is too short" in e for e in errors))

    def test_published_2026_07_14_copy_still_passes(self):
        segments = [
            news_segment(
                1,
                "Anthropic 公布 Claude 价值表达跨模型与语言研究",
                "Anthropic 公布 Claude 价值表达跨模型与语言研究。Anthropic 表示,新研究分析了超过 30 万条匿名对话,用于比较不同 Claude 模型和不同语言中的价值表达。",
            ),
            news_segment(
                2,
                "ChatGPT 在欧洲经济区重新接入 WhatsApp,并新增 Kakao 与 Viber",
                "ChatGPT 在欧洲经济区重新接入 WhatsApp,并新增 Kakao 与 Viber。用户可向经验证的 1-800-CHATGPT 联系人提问、上传图片、发送语音、生成图片并使用多语言。",
            ),
        ]

        errors = _automatic_news_content_errors(segments)

        self.assertEqual(errors, [])

    def test_hygiene_failures_are_repairable_positions(self):
        bad = news_segment(
            1,
            "Kimi开源x.com",
            "实话实说啊,这个价格,确实比上一代翻了快一倍,新增了 3 个模型版本。",
        )
        good = news_segment(
            2,
            "阿里最新一代大模型千问3.8将至",
            "据 36氪 报道,阿里最新一代大模型千问3.8即将发布并开源,预览版已率先上线阿里云。",
        )
        errors = _automatic_news_content_errors([bad, good])
        self.assertTrue(errors)
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td)
            (run_dir / "script.json").write_text(
                json.dumps([bad, good], ensure_ascii=False), encoding="utf-8"
            )

            positions = repairable_news_positions(run_dir, errors)

        self.assertEqual(positions, [1])


class NarrationDensityTests(unittest.TestCase):
    def test_brief_narration_speaks_multiple_approved_facts(self):
        plan = EditorialPlan(
            story_id="s1",
            topic_key="t1",
            kind="product",
            title="Anthropic 公布 Claude 价值表达跨模型与语言研究",
            lead="Anthropic 公布 Claude 价值表达跨模型与语言研究。",
            facts=[
                "Anthropic 表示,新研究分析了超过 30 万条匿名对话,用于比较不同 Claude 模型和不同语言中的价值表达。",
                "Anthropic 表示,Claude 的价值表达会随对话语言变化,其中温暖与严谨轴最明显,俄语更偏严谨。",
                "Anthropic 表示,目前仍不清楚 Claude 的价值表达为何变化、这些变化是否符合预期。",
            ],
            claim_ids=["c1", "c2", "c3"],
            impact="",
            caution="",
            editorial_tier="brief",
        )

        narration = plan.narration()

        # The old 118-char budget could only speak the lead plus one clause;
        # the published (agent-reviewed) editions narrate 2-3 facts per brief.
        self.assertGreater(len(narration), 130)
        self.assertIn("30 万条匿名对话", narration)
        self.assertIn("会随对话语言变化", narration)

    def test_brief_narration_still_dedupes_and_terminates_cleanly(self):
        plan = EditorialPlan(
            story_id="s2",
            topic_key="t2",
            kind="product",
            title="OpenAI Build Week 开放项目投稿",
            lead="OpenAI Build Week 开放项目投稿。",
            facts=["OpenAI Build Week 开放项目投稿。", "OpenAI Build Week 的项目投稿截止日期为 7 月 21 日。"],
            claim_ids=["c1", "c2"],
            impact="",
            caution="",
            editorial_tier="brief",
        )

        narration = plan.narration()

        self.assertEqual(narration.count("OpenAI Build Week 开放项目投稿。"), 1)
        self.assertTrue(narration.endswith("。"))


class NarrationPolishTests(unittest.TestCase):
    def test_consecutive_same_attribution_is_spoken_once(self):
        plan = EditorialPlan(
            story_id="s3",
            topic_key="t3",
            kind="product",
            title="Anthropic 公布跨语言研究",
            lead="Anthropic 公布 Claude 价值表达跨模型与语言研究。",
            facts=[
                "Anthropic 表示，新研究分析了超过 30 万条匿名对话，用于比较不同模型的价值表达。",
                "Anthropic 表示，Claude 的价值表达会随对话语言变化，俄语更偏严谨。",
            ],
            claim_ids=["c1", "c2"],
            impact="",
            caution="",
            editorial_tier="brief",
        )

        narration = plan.narration()

        self.assertEqual(narration.count("Anthropic 表示"), 1)
        self.assertIn("Claude 的价值表达会随对话语言变化", narration)

    def test_different_attributors_keep_their_prefixes(self):
        plan = EditorialPlan(
            story_id="s4",
            topic_key="t4",
            kind="industry",
            title="36氪称字节正探索自动驾驶",
            lead="36氪称字节正探索自动驾驶。",
            facts=[
                "这一项目由世界模型团队负责，方向包括无人物流。",
                "字节回应称，公司没有开展智能驾驶业务的计划。",
            ],
            claim_ids=["c1", "c2"],
            impact="",
            caution="",
            editorial_tier="brief",
        )

        narration = plan.narration()

        self.assertIn("字节回应称", narration)

    def test_top_level_semicolons_become_breathing_stops(self):
        plan = EditorialPlan(
            story_id="s5",
            topic_key="t5",
            kind="product",
            title="双平台更新",
            lead="官方发布双平台更新。",
            facts=["桌面端新增批量导出；移动端支持“离线；同步”模式，均已上线。"],
            claim_ids=["c1"],
            impact="",
            caution="",
            editorial_tier="brief",
        )

        narration = plan.narration()

        self.assertIn("桌面端新增批量导出。移动端支持", narration)
        # clean_text normalizes the fullwidth semicolon; the quoted one must
        # survive as a semicolon either way instead of becoming a full stop.
        self.assertIn("“离线;同步”", narration)


class TitleCandidatePreferenceTests(unittest.TestCase):
    def test_double_headline_leads_when_both_hooks_are_real(self):
        lead = card(1, "ChatGPT official", "OpenAI", "ChatGPT 重返欧洲 WhatsApp", ["ChatGPT 已重新在欧洲经济区的 WhatsApp 上可用。"])
        lead.editorial_tier = "headline"
        second = card(2, "Anthropic News", "Anthropic", "Claude 公布跨模型价值研究", ["新研究分析了超过 30 万条匿名对话。"])
        second.editorial_tier = "brief"

        with tempfile.TemporaryDirectory() as td:
            _timeline, titles = write_bilibili_md(
                Path(td) / "bilibili.md", [lead, second], "1080p", "2026-07-14"
            )

        self.assertTrue(titles)
        self.assertIn("；", titles[0])
        self.assertIn("ChatGPT 重返欧洲 WhatsApp", titles[0])

    def test_fallback_lead_never_promotes_a_combined_title(self):
        lead = card(1, "HF Models", "Kimi", "Moonshot AI releases an updated open weight coding model on the hub", [])
        second = card(2, "36氪", "Qwen", "阿里千问3.8将至", ["7月19日,阿里最新一代大模型千问3.8即将发布并开源。"])

        with tempfile.TemporaryDirectory() as td:
            _timeline, titles = write_bilibili_md(
                Path(td) / "bilibili.md", [lead, second], "1080p", "2026-07-20"
            )

        self.assertTrue(titles)
        self.assertTrue(titles[0].startswith("AI 日报："))


if __name__ == "__main__":
    unittest.main()

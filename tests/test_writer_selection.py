import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from briefing.edition_brief import deterministic_edition_brief
from briefing.editorial_plan import build_editorial_plan
from briefing.models import EvidenceCard
from briefing.render import _segments, _signal_disclaimer
from briefing.scoring import mainstream_priority_rank, public_source_status, story_category
from briefing.social_signals import card_is_community_signal, card_is_official_personnel_signal
from briefing.story_model import build_story_spec
from briefing.writer import _merge_card_group, _merge_related_cards, is_briefable_card, is_publishable_card, select_card_portfolio, select_cards, select_rejected_review_candidates, timeline_for_cards, timeline_from_script, write_bilibili_json, write_bilibili_md, write_fact_check_md, write_rejected_candidates_md, write_package, write_pinned_comment

DATE_SUFFIX = "【AI 日报 2026-07-04】"
SHOCK_1 = "\u70b8"
SHOCK_2 = "\u9707\u60ca"
BANNED_PUBLIC = ["自动生成", "完整来源", "本地", "sources.md", "fact-check.md", "选题分", "绿色", "黄色", "红色"]


def card(idx: int, source: str, entity: str = "AI", risk: str = "green", score: float = 80.0) -> EvidenceCard:
    return EvidenceCard(
        cluster_key=f"k{idx}",
        event_title=f"Event {idx}",
        entity=entity,
        risk=risk,
        confidence=85,
        selected=True,
        reason="test",
        source_count=1,
        official_count=1 if risk == "green" else 0,
        media_count=0 if risk == "green" else 1,
        community_count=0,
        first_seen_at=datetime(2026, 7, 3, tzinfo=timezone.utc),
        latest_published_at=datetime(2026, 7, 3, tzinfo=timezone.utc),
        key_facts=[],
        evidence_links=[{"source": source, "tier": "A", "reliability": "official", "title": f"Event {idx}", "url": "https://example.com", "published_at": ""}],
        uncertainty=[],
        score=score,
    )


class WriterSelectionTests(unittest.TestCase):
    def test_fact_check_distinguishes_eligible_from_final_portfolio(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            eligible_a = card(1, "Source A")
            eligible_b = card(2, "Source B")
            ineligible = card(3, "Source C")
            ineligible.selected = False

            write_fact_check_md(root / "fact-check.md", [eligible_a, eligible_b, ineligible], selected=[eligible_b])

            text = (root / "fact-check.md").read_text(encoding="utf-8-sig")
            self.assertIn("今日候选：3 条；验证可入选 2 条；最终进入主视频 1 条。", text)
            self.assertIn("验证可入选 / 最终进入主视频", text)
            self.assertIn("验证可入选 / 未进入最终组合", text)
            self.assertIn("验证未通过 / 未进入最终组合", text)

    def test_merged_card_retains_every_constituent_cluster_key(self):
        merged = _merge_card_group([card(1, "Source A"), card(2, "Source B")])

        self.assertTrue(merged.cluster_key.startswith("merged:"))
        self.assertEqual(merged.source_cluster_keys, ["k1", "k2"])

    def test_distinct_chatgpt_and_codex_events_do_not_merge_by_product_name(self):
        whatsapp = card(101, "ChatGPT official", "OpenAI")
        whatsapp.event_title = "ChatGPT returns to WhatsApp in the EEA and adds Kakao and Viber"
        whatsapp.evidence_links[0]["url"] = "https://x.com/ChatGPTapp/status/101"
        reset = card(102, "OpenAI product lead", "OpenAI", "yellow")
        reset.event_title = "Codex and ChatGPT Work grant one reserve usage reset to all accounts"
        reset.evidence_links[0]["url"] = "https://x.com/thsottiaux/status/102"
        reset.evidence_links[0]["reliability"] = "official_personnel"
        optimization = card(103, "OpenAI product lead", "OpenAI", "yellow")
        optimization.event_title = "Codex and ChatGPT Work usage adjustment"
        optimization.key_facts = ["Inference optimization gives GPT-5.6 Sol subscriptions about 10% more usage"]
        optimization.evidence_links[0].update(
            {"url": "https://x.com/thsottiaux/status/103", "reliability": "official_personnel"}
        )
        subscription = card(104, "OpenAI product lead", "OpenAI", "yellow")
        subscription.event_title = "GPT-5.6 Sol remains in paid ChatGPT subscriptions"
        subscription.evidence_links[0].update(
            {"url": "https://x.com/thsottiaux/status/104", "reliability": "official_personnel"}
        )

        merged = _merge_related_cards([whatsapp, reset, optimization, subscription])

        self.assertEqual(len(merged), 2)
        self.assertIn("k101", {row.cluster_key for row in merged})
        personnel = next(row for row in merged if row.cluster_key != "k101")
        self.assertEqual(personnel.source_cluster_keys, ["k102", "k103", "k104"])
        self.assertEqual(len(personnel.evidence_links), 3)
        self.assertEqual(personnel.source_count, 1)
        self.assertEqual(personnel.community_count, 1)
        self.assertEqual(public_source_status(personnel), "X 官方人员动态")

    def test_chatgpt_work_scope_and_early_access_merge_only_for_same_account(self):
        scope = card(111, "OpenAI product lead", "OpenAI", "yellow")
        scope.event_title = "OpenAI 产品人员称 ChatGPT Work 已覆盖网页与移动端"
        scope.key_facts = [
            "Tibo Sottiaux 表示,ChatGPT Work 可创建和托管网站、管理电子邮件并汇总大量文档。",
        ]
        scope.evidence_links[0].update(
            {
                "url": "https://x.com/thsottiaux/status/111",
                "reliability": "official_personnel",
                "excerpt": scope.key_facts[0],
            }
        )
        recruit = card(112, "OpenAI product lead", "OpenAI", "yellow")
        recruit.event_title = "OpenAI 产品人员为 ChatGPT Work 招募 50 至 100 人早期体验组"
        recruit.key_facts = ["Tibo Sottiaux 征集 50 至 100 名 ChatGPT Work 用户组成早期体验组。"]
        recruit.evidence_links[0].update(
            {
                "url": "https://x.com/thsottiaux/status/112",
                "reliability": "official_personnel",
                "excerpt": recruit.key_facts[0],
            }
        )
        usage = card(113, "OpenAI product lead", "OpenAI", "yellow")
        usage.event_title = "ChatGPT Work 向全体账户发放一次储备用量重置"
        usage.key_facts = ["ChatGPT Work 用户获得一次 reserve usage reset。"]
        usage.evidence_links[0].update(
            {"url": "https://x.com/thsottiaux/status/113", "reliability": "official_personnel"}
        )
        demo = card(114, "OpenAI product lead", "OpenAI", "yellow")
        demo.event_title = "ChatGPT Work workflow demo"
        demo.key_facts = ["Tibo Sottiaux 展示 ChatGPT Work 自动整理邮箱的工作流演示。"]
        demo.evidence_links[0].update(
            {"url": "https://x.com/thsottiaux/status/114", "reliability": "official_personnel"}
        )
        other_account = card(115, "OpenAI staff", "OpenAI", "yellow")
        other_account.event_title = "ChatGPT Work early access group expands"
        other_account.key_facts = ["另一名团队成员为 ChatGPT Work 征集早期体验用户。"]
        other_account.evidence_links[0].update(
            {"url": "https://x.com/otherstaff/status/115", "reliability": "official_personnel"}
        )

        merged = _merge_related_cards([scope, recruit, usage, demo, other_account])

        access_story = next(row for row in merged if set(row.source_cluster_keys) == {"k111", "k112"})
        self.assertEqual(len(access_story.evidence_links), 2)
        self.assertEqual(
            access_story.event_title,
            "OpenAI 产品人员介绍 ChatGPT Work 能力范围并招募早期体验组",
        )
        self.assertEqual(len(merged), 4)
        remaining_keys = {row.cluster_key for row in merged if row is not access_story}
        self.assertEqual(remaining_keys, {"k113", "k114", "k115"})

    def test_limits_repeated_sources_and_arxiv(self):
        cards = [
            card(1, "arXiv cs.CL", "Paper / arXiv", "yellow", 99),
            card(2, "arXiv cs.AI", "Paper / arXiv", "yellow", 98),
            card(3, "InfoQ", "OpenAI", "yellow", 90),
            card(4, "InfoQ", "Anthropic / Claude", "yellow", 89),
            card(5, "InfoQ", "AI", "yellow", 88),
            card(6, "ModelScope GitHub Releases", "ModelScope", "green", 87),
            card(7, "OpenAI Codex GitHub Releases", "OpenAI", "green", 86),
        ]
        cards[5].key_facts = ["ModelScope v1.38.1 修复 Docker 发布流程并改进 HubApi 兼容性。"]
        cards[6].key_facts = ["Codex 本次修复插件加载问题，并新增命令行会话恢复支持。"]
        picked = select_cards(cards, max_items=5)
        sources = [c.evidence_links[0]["source"] for c in picked]
        self.assertLessEqual(sum(s.startswith("arXiv") for s in sources), 1)
        self.assertLessEqual(sources.count("InfoQ"), 2)
        self.assertIn("ModelScope GitHub Releases", sources)

    def test_does_not_fill_with_extra_same_source_items(self):
        entities = ["OpenAI", "Anthropic / Claude", "DeepSeek", "Qwen / 阿里", "MiniMax", "AI"]
        cards = [card(i, "36氪", entity, "yellow", 80 - i) for i, entity in enumerate(entities, 1)]

        picked = select_cards(cards, max_items=5)

        self.assertEqual(len(picked), 2)
        self.assertTrue(all(c.evidence_links[0]["source"] == "36氪" for c in picked))

    def test_non_mainstream_media_goes_to_rejected_review_pool(self):
        mainstream = card(1, "OpenAI News", "OpenAI", "green", 95)
        lunch = card(2, "36氪", "具身智能 / 机器人", "red", 66)
        lunch.selected = False
        lunch.reason = "非主流 AI 单一媒体线索，默认不进主视频。"
        lunch.official_count = 0
        lunch.media_count = 1
        lunch.evidence_links[0].update({"source": "36氪", "tier": "B", "reliability": "media"})

        picked = select_cards([mainstream, lunch], max_items=5)
        rejected = select_rejected_review_candidates([mainstream, lunch])

        self.assertEqual(picked, [mainstream])
        self.assertEqual(rejected, [lunch])

    def test_mainstream_ai_has_highest_morning_priority(self):
        mainstream = card(1, "OpenAI News", "OpenAI", "green", 55)
        mainstream.confidence = 45
        mainstream.event_title = "OpenAI 发布 ChatGPT 产品更新"
        mainstream.key_facts = ["OpenAI 官方发布 ChatGPT 产品更新，涉及模型入口和 API。"]

        generic = card(2, "Startup Official Blog", "AI Startup", "green", 99)
        generic.confidence = 99
        generic.event_title = "AI startup raises new funding"
        generic.key_facts = ["A smaller AI startup announced a new funding round."]

        picked = select_cards([generic, mainstream], max_items=1)

        self.assertGreater(mainstream_priority_rank(mainstream), mainstream_priority_rank(generic))
        self.assertEqual(picked, [mainstream])

    def test_mainstream_media_model_news_beats_generic_high_score_news(self):
        hy3 = card(1, "36氪", "腾讯混元", "yellow", 60)
        hy3.confidence = 55
        hy3.official_count = 0
        hy3.media_count = 1
        hy3.event_title = "腾讯混元 Hy3 正式发布"
        hy3.key_facts = ["媒体报道腾讯混元 Hy3 正式发布，涉及 Agent 能力、元宝和 API 入口。"]
        hy3.evidence_links[0].update({"source": "36氪", "tier": "B", "reliability": "media", "title": hy3.event_title})

        generic = card(2, "Generic AI Blog", "AI", "green", 99)
        generic.confidence = 99
        generic.event_title = "Generic AI industry partnership update"
        generic.key_facts = ["A generic AI industry partnership update with limited user-facing impact."]

        picked = select_cards([generic, hy3], max_items=1)

        self.assertGreater(mainstream_priority_rank(hy3), mainstream_priority_rank(generic))
        self.assertEqual(picked, [hy3])

    def test_rejected_candidates_file_is_written(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lunch = card(2, "36氪", "具身智能 / 机器人", "red", 66)
            lunch.selected = False
            lunch.reason = "非主流 AI 单一媒体线索，默认不进主视频。"
            lunch.official_count = 0
            lunch.media_count = 1
            lunch.evidence_links[0].update({"source": "36氪", "tier": "B", "reliability": "media"})

            write_rejected_candidates_md(root / "rejected-candidates.md", [lunch])

            text = (root / "rejected-candidates.md").read_text(encoding="utf-8-sig")
            self.assertIn("剔除/待复核候选", text)
            self.assertIn("非主流 AI", text)

    def test_red_community_signal_goes_to_rejected_review_pool(self):
        signal = card(2, "reddit", "Qwen / 阿里", "red", 70)
        signal.selected = False
        signal.reason = "社区/聚合单源只作线索，默认不进主视频。"
        signal.official_count = 0
        signal.media_count = 0
        signal.community_count = 1
        signal.event_title = "Qwen 3.6 27B - VLLM Performance Benchmark Results"
        signal.key_facts = ["社区源头提到 Qwen 3.6 benchmark 和 throughput 表现。"]
        signal.evidence_links[0].update({"source": "reddit", "tier": "C", "reliability": "community", "title": signal.event_title})

        self.assertEqual(select_rejected_review_candidates([signal]), [signal])

    def test_failed_required_screenshot_is_not_selected_for_main_video(self):
        main = card(1, "OpenAI News", "OpenAI", "green", 95)
        blocked = card(2, "reddit", "Qwen / 阿里", "yellow", 94)
        blocked.official_count = 0
        blocked.media_count = 0
        blocked.community_count = 1
        blocked.evidence_links[0].update(
            {
                "source": "Reddit LocalLLaMA",
                "tier": "C",
                "reliability": "community",
                "screenshot_required": "true",
                "screenshot_status": "failed",
                "screenshot_error": "blocked_page:you've been blocked by network security",
            }
        )

        picked = select_cards([blocked, main], max_items=5)

        self.assertEqual(picked, [main])

    def test_official_personnel_signal_with_screenshot_can_be_selected(self):
        signal = card(2, "X / Tibo", "OpenAI", "yellow", 86)
        signal.official_count = 0
        signal.media_count = 0
        signal.community_count = 1
        signal.event_title = "Tibo says Ultra will be in Codex"
        signal.key_facts = [
            "OpenAI 相关事件：Tibo says Ultra will be in Codex",
            "来源摘要：OpenAI Codex 相关成员 Tibo 在 X 上转发称：Ultra will be in Codex。",
        ]
        signal.evidence_links[0].update(
            {
                "source": "X / Tibo",
                "tier": "C",
                "reliability": "official_personnel",
                "title": signal.event_title,
                "url": "https://x.com/thsottiaux/status/1940000000000000000",
                "screenshot_required": "true",
                "screenshot_status": "captured",
                "screenshot_path": __file__,
            }
        )

        picked = select_cards([signal], max_items=5)

        self.assertEqual(picked, [signal])
        self.assertTrue(is_publishable_card(signal, strict_auto=True))

    def test_untranslated_x_signal_is_not_selected_by_strict_automation(self):
        signal = card(2, "X / Researcher", "OpenAI", "yellow", 86)
        signal.official_count = 0
        signal.media_count = 0
        signal.community_count = 1
        signal.event_title = "A brand-new product is coming very soon with new capabilities"
        signal.key_facts = ["来源摘要：A brand-new product is coming very soon with new capabilities."]
        signal.evidence_links[0].update(
            {
                "source": "X / Researcher",
                "tier": "C",
                "reliability": "official_personnel",
                "url": "https://x.com/researcher/status/1940000000000000001",
                "screenshot_required": "true",
                "screenshot_status": "captured",
                "screenshot_path": __file__,
            }
        )

        self.assertFalse(is_publishable_card(signal, strict_auto=True))

    def test_community_x_rumour_needs_named_concrete_observation(self):
        signal = card(2, "X / Community", "Qwen / 阿里", "yellow", 86)
        signal.official_count = 0
        signal.media_count = 0
        signal.community_count = 1
        signal.event_title = "社区实测 Qwen 3.6 27B 在 VLLM 下的 FP8 吞吐"
        signal.key_facts = ["来源摘要：社区帖子比较 Qwen 3.6 27B 在 VLLM、BF16 与 FP8 配置下的吞吐表现。"]
        signal.evidence_links[0].update(
            {
                "source": "X / Community",
                "tier": "C",
                "reliability": "community",
                "url": "https://x.com/community/status/1940000000000000002",
                "screenshot_required": "true",
                "screenshot_status": "captured",
                "screenshot_path": __file__,
            }
        )

        self.assertTrue(is_publishable_card(signal, strict_auto=True))
        self.assertEqual(select_cards([signal], max_items=5, strict_auto=True), [signal])
        self.assertFalse(card_is_official_personnel_signal(signal))
        self.assertTrue(card_is_community_signal(signal))
        self.assertEqual(story_category(signal), "community_signal")
        self.assertEqual(_signal_disclaimer(signal), ("community_rumour", "传闻/风向，未获官方公告确认。"))
        rendered = next(segment for segment in _segments([signal]) if segment.get("kind") == "news")
        self.assertEqual(rendered["signal_kind"], "community_rumour")
        self.assertEqual(rendered["disclaimer"], "传闻/风向，未获官方公告确认。")
        self.assertTrue(rendered["title"].startswith("传闻/风向｜"))

    def test_single_media_relay_keeps_community_x_rumour_label_and_source_post(self):
        signal = card(3, "Tech Media", "OpenAI", "yellow", 86)
        signal.official_count = 0
        signal.media_count = 1
        signal.community_count = 1
        signal.event_title = "社区称 OpenAI 正测试新功能"
        signal.key_facts = ["社区原帖称 OpenAI 正测试名为 Workspace 的新功能，但尚无官方公告。"]
        signal.evidence_links = [
            {
                "source": "Tech Media",
                "tier": "B",
                "reliability": "media",
                "title": signal.event_title,
                "url": "https://media.example/openai-workspace",
            },
            {
                "source": "X / Community",
                "tier": "C",
                "reliability": "community",
                "title": "OpenAI Workspace 测试线索",
                "url": "https://x.com/community/status/1940000000000000003",
                "screenshot_required": "true",
                "screenshot_status": "captured",
                "screenshot_path": __file__,
            },
        ]

        self.assertFalse(card_is_official_personnel_signal(signal))
        self.assertTrue(card_is_community_signal(signal))
        self.assertEqual(story_category(signal), "community_signal")
        self.assertEqual(_signal_disclaimer(signal), ("community_rumour", "传闻/风向，未获官方公告确认。"))
        rendered = next(segment for segment in _segments([signal]) if segment.get("kind") == "news")
        self.assertEqual(rendered["signal_kind"], "community_rumour")
        source_post = next(row for row in rendered["evidence"] if row["screenshot_required"] == "true")
        self.assertEqual(source_post["url"], "https://x.com/community/status/1940000000000000003")

    def test_generic_official_news_listing_is_not_publishable(self):
        generic = card(1, "MiniMax Official Web", "MiniMax", "green", 95)
        generic.event_title = "新闻"
        generic.key_facts = ["MiniMax 官网新闻页有更新，但信息还不够。"]
        generic.evidence_links[0].update(
            {
                "source": "MiniMax Official Web",
                "tier": "A",
                "reliability": "official",
                "title": "新闻",
                "url": "https://www.minimax.io/news",
            }
        )

        picked = select_cards([generic], max_items=5)

        self.assertFalse(is_publishable_card(generic))
        self.assertEqual(picked, [])

    def test_empty_github_release_is_not_publishable_without_change_details(self):
        release = card(1, "OpenAI Codex GitHub Releases", "OpenAI", "green", 95)
        release.event_title = "OpenAI Codex 发布 0.143.0-alpha.38"
        release.key_facts = ["Release 只给出版本号，未写明具体变更。"]
        release.evidence_links[0].update(
            {
                "source": "OpenAI Codex GitHub Releases",
                "title": "0.143.0-alpha.38",
                "url": "https://github.com/openai/codex/releases/tag/rust-v0.143.0-alpha.38",
            }
        )

        self.assertFalse(is_publishable_card(release, strict_auto=True))
        self.assertEqual(select_cards([release], max_items=5, strict_auto=True), [])

    def test_model_page_needs_download_or_capability_detail(self):
        model = card(1, "MiniMax Hugging Face Models", "MiniMax", "green", 95)
        model.event_title = "Hugging Face 模型仓库更新：MiniMaxAI/MiniMax-M3"
        model.key_facts = ["模型页出现更新，但没有说明权重、许可或调用入口。"]
        model.evidence_links[0].update(
            {
                "source": "MiniMax Hugging Face Models",
                "title": "MiniMaxAI/MiniMax-M3",
                "url": "https://huggingface.co/MiniMaxAI/MiniMax-M3",
            }
        )

        self.assertFalse(is_publishable_card(model, strict_auto=True))
        self.assertEqual(select_cards([model], max_items=5, strict_auto=True), [])

    def test_arxiv_with_raw_english_abstract_is_not_publishable_in_strict_auto(self):
        paper = card(1, "arXiv cs.AI", "论文 / arXiv", "yellow", 95)
        paper.official_count = 1
        paper.media_count = 0
        paper.event_title = "A Reliability Assessment of LALM Audio Judges"
        paper.key_facts = [
            "来源摘要：arXiv:2607.07985v1 Abstract: We report empirical reliability across multiple Gemini models and benchmark settings.",
        ]
        paper.evidence_links[0].update(
            {
                "source": "arXiv cs.AI",
                "title": paper.event_title,
                "url": "https://arxiv.org/abs/2607.07985",
            }
        )
        paper.evidence_links.append(
            {
                "source": "Reddit discussion",
                "tier": "C",
                "reliability": "community",
                "title": "discussion",
                "url": "https://reddit.example/paper",
            }
        )

        self.assertFalse(is_publishable_card(paper, strict_auto=True))
        self.assertEqual(select_cards([paper], max_items=5, strict_auto=True), [])

    def test_high_density_single_media_engineering_case_can_fill_media_slot(self):
        story = card(1, "InfoQ 中文", "Anthropic / Claude", "yellow", 58)
        story.event_title = "史上最高调的AI重写:Claude花11天搞定Bun"
        story.official_count = 0
        story.media_count = 1
        story.key_facts = [
            "来源摘要：Claude 将 Bun 重写为 Rust,涉及超过 100 万行代码、6778 次提交和 50 个动态工作流；峰值并行运行 64 个 Claude；Linux 启动时间从 517ms 降至 464ms。"
        ]
        story.evidence_links[0].update({"source": "InfoQ 中文", "tier": "B", "reliability": "media", "url": "https://infoq.cn/bun"})

        picked = select_cards([story], max_items=5, min_score=68, strict_auto=True)

        self.assertEqual(picked, [story])

    def test_low_density_single_media_story_does_not_bypass_threshold(self):
        story = card(1, "36氪", "Kimi / 月之暗面", "yellow", 58)
        story.event_title = "Kimi 信用卡合作方敲定"
        story.official_count = 0
        story.media_count = 1
        story.key_facts = ["来源摘要：Kimi 信用卡将上线,合作方已经敲定。"]
        story.evidence_links[0].update({"source": "36氪", "tier": "B", "reliability": "media", "url": "https://36kr.com/kimi-card"})

        self.assertEqual(select_cards([story], max_items=5, min_score=68, strict_auto=True), [])

    def test_strict_automation_rejects_unsplit_weekly_digest_as_headline_or_brief(self):
        roundup = card(1, "InfoQ 中文", "DeepSeek", "yellow", 95)
        roundup.event_title = (
            "MiniMax CEO称不再领取薪酬；吐槽DeepSeek面试的华为天才少年回应；"
            "宇树机器人登上Nature｜AI周报"
        )
        roundup.official_count = 0
        roundup.media_count = 1
        roundup.key_facts = ["OpenAI 发布 GPT-5.6，并新增程序化工具调用与零数据保留支持。"]
        roundup.evidence_links[0].update(
            {
                "source": "InfoQ 中文",
                "tier": "B",
                "reliability": "media",
                "url": "https://www.infoq.cn/article/weekly",
                "excerpt": roundup.key_facts[0],
            }
        )

        self.assertFalse(is_publishable_card(roundup, strict_auto=True))
        self.assertFalse(is_briefable_card(roundup, strict_auto=True))
        self.assertEqual(select_cards([roundup], max_items=5, min_score=0, strict_auto=True), [])

    def test_strict_automation_rejects_real_krypton_evening_digest(self):
        roundup = card(1, "36氪", "字节 / 豆包", "yellow", 95)
        roundup.event_title = (
            "氪星晚报 |Meta宣布将追加400亿美元投资路易斯安那州数据中心;"
            "字节探索自动驾驶,Seed世界模型团队负责;"
            "《扩大消费‘十五五’规划》:优化入境消费环境"
        )
        roundup.official_count = 0
        roundup.media_count = 1
        roundup.key_facts = ["36氪称字节正探索自动驾驶，但字节回应暂无智能驾驶业务计划。"]
        roundup.evidence_links[0].update(
            {
                "source": "36氪",
                "tier": "B",
                "reliability": "media",
                "url": "https://36kr.com/p/evening-digest",
                "excerpt": roundup.key_facts[0],
            }
        )

        self.assertFalse(is_publishable_card(roundup, strict_auto=True))
        self.assertFalse(is_briefable_card(roundup, strict_auto=True))
        self.assertEqual(select_cards([roundup], max_items=5, min_score=0, strict_auto=True), [])

    def test_single_media_title_restatement_is_not_a_strict_brief(self):
        story = card(1, "InfoQ 中文", "Meta", "yellow", 95)
        story.event_title = "Meta 推出一款新的智能体模型"
        story.official_count = 0
        story.media_count = 1
        story.key_facts = [story.event_title]
        story.evidence_links[0].update(
            {
                "source": "InfoQ 中文",
                "tier": "B",
                "reliability": "media",
                "url": "https://www.infoq.cn/article/title-only",
                "title": story.event_title,
                "excerpt": story.event_title,
            }
        )

        self.assertFalse(is_briefable_card(story, strict_auto=True))

    def test_strict_automation_rejects_obvious_multi_event_semicolon_title(self):
        roundup = card(1, "媒体", "AI", "yellow", 95)
        roundup.event_title = "OpenAI发布新模型；Anthropic任命新主管；Google收购推理团队"
        roundup.official_count = 0
        roundup.media_count = 1
        roundup.key_facts = ["OpenAI 新模型新增程序化工具调用，并开放 API。"]
        roundup.evidence_links[0].update(
            {"tier": "B", "reliability": "media", "excerpt": roundup.key_facts[0]}
        )

        self.assertFalse(is_publishable_card(roundup, strict_auto=True))
        self.assertFalse(is_briefable_card(roundup, strict_auto=True))

    def test_digest_words_in_body_do_not_block_a_single_topic_story(self):
        release = card(1, "OpenAI News", "OpenAI", "green", 95)
        release.event_title = "OpenAI 发布 Codex 2.0"
        release.key_facts = [
            "Codex 2.0 新增 256K 上下文并开放 API；本周报还回顾了此前版本，但这条正文只描述 Codex 2.0。"
        ]

        self.assertTrue(is_publishable_card(release, strict_auto=True))

    def test_single_event_semicolon_clauses_are_not_mistaken_for_a_roundup(self):
        release = card(1, "OpenAI News", "OpenAI", "green", 95)
        release.event_title = "OpenAI 发布 Codex 2.0；支持批处理；覆盖三个区域"
        release.key_facts = ["Codex 2.0 新增 256K 上下文并开放 API。"]

        self.assertTrue(is_publishable_card(release, strict_auto=True))

    def test_marketing_and_conference_items_are_not_briefs(self):
        for idx, title in enumerate(["Kimi 信用卡合作方敲定", "vLLM 推理优化实践｜AICon深圳"], 1):
            item = card(idx, "媒体", "AI", "yellow", 58)
            item.event_title = title
            item.official_count = 0
            item.media_count = 1
            item.key_facts = [f"来源摘要：{title}，页面提供具体介绍。"]
            item.evidence_links[0].update({"excerpt": item.key_facts[0], "reliability": "media"})
            self.assertFalse(is_briefable_card(item, strict_auto=True))

    def test_specific_source_linked_update_can_be_brief_without_becoming_headline(self):
        update = card(1, "GitHub Changelog", "GitHub / Copilot", "green", 58)
        update.event_title = "GitHub Copilot 新增仓库概览"
        update.key_facts = ["GitHub Copilot 现在可以为首次打开的仓库生成高层概览。"]
        update.evidence_links[0].update(
            {
                "source": "GitHub Changelog",
                "title": update.event_title,
                "url": "https://github.blog/changelog/repository-overview",
                "excerpt": "GitHub Copilot 现在可以为首次打开的仓库生成高层概览。",
            }
        )

        self.assertTrue(is_briefable_card(update, strict_auto=True))
        portfolio = select_card_portfolio([update], max_items=12, min_score=68, strict_auto=True)
        self.assertEqual(portfolio.items, [update])
        self.assertEqual(update.editorial_tier, "brief")
        self.assertEqual(portfolio.diagnostics["tier_counts"], {"brief": 1})
        edition = deterministic_edition_brief(portfolio.items)
        story_id = next(iter(edition["story_roles"]))
        self.assertEqual(edition["story_roles"], {story_id: "brief"})
        self.assertEqual(edition["duration_budget"][story_id], 25)
        self.assertEqual(edition["comment_question"], "你更关心 GitHub Copilot 的实际效果，还是使用门槛？")

    def test_headline_and_briefs_share_one_portfolio(self):
        headline = card(1, "OpenAI News", "OpenAI", "green", 95)
        headline.event_title = "OpenAI 发布 GPT-5.6"
        headline.key_facts = ["GPT-5.6 新增程序化工具调用，并在 Agents’ Last Exam 获得 53.6 分。"]
        headline.evidence_links[0]["excerpt"] = headline.key_facts[0]
        briefs = []
        for idx, (entity, title) in enumerate(
            [
                ("Meta", "Meta 发布 Muse Spark 1.1"),
                ("Google", "Google Cloud 上线 AlphaEvolve"),
                ("Ollama", "Ollama 模型库突破 8800 个模型"),
            ],
            2,
        ):
            item = card(idx, f"Source {idx}", entity, "green", 20)
            item.confidence = 20
            item.event_title = title
            item.key_facts = [f"{title}，官方页面已给出可用入口。"]
            item.evidence_links[0].update({"title": title, "excerpt": item.key_facts[0]})
            briefs.append(item)

        portfolio = select_card_portfolio([headline, *briefs], max_items=12, min_score=68, strict_auto=True)

        self.assertEqual(len(portfolio.items), 4)
        self.assertEqual(headline.editorial_tier, "headline")
        self.assertTrue(all(item.editorial_tier == "brief" for item in briefs))

    def test_same_model_launch_and_integration_get_separate_daily_slots(self):
        launch = card(1, "OpenAI News", "OpenAI", "green", 98)
        launch.event_title = "GPT-5.6: Frontier intelligence that scales with your ambition"
        launch.key_facts = ["GPT-5.6 新增程序化工具调用,可用轻量程序协调工具,并支持零数据保留。"]
        integration = card(2, "GitHub Changelog", "GitHub / Copilot", "green", 96)
        integration.event_title = "OpenAI’s GPT-5.6 Sol, Terra, and Luna are now available in GitHub Copilot"
        integration.key_facts = ["GitHub Copilot 正在推送 Sol、Terra 和 Luna 三种 GPT-5.6 变体。"]
        integration.evidence_links[0].update({"source": "GitHub Changelog", "url": "https://github.blog/changelog/gpt-5-6"})
        independent = card(3, "GitHub Changelog", "GitHub / Copilot", "green", 90)
        independent.event_title = "Ask Copilot for a repository overview"
        independent.key_facts = ["用户首次进入陌生仓库时,可以让 Copilot 生成高层概览。"]
        independent.evidence_links[0].update({"source": "GitHub Changelog", "url": "https://github.blog/changelog/repository-overview"})

        picked = select_cards([launch, integration, independent], max_items=3, min_score=0, strict_auto=True)

        self.assertEqual(len([item for item in picked if "gpt-5.6" in item.event_title.lower()]), 2)
        self.assertIn(independent, picked)

    def test_known_official_product_headline_is_publishable_and_localizable(self):
        release = card(1, "GitHub Changelog", "GitHub / Copilot", "green", 95)
        release.event_title = "Ask Copilot for a repository overview"
        release.key_facts = [
            "GitHub / Copilot 相关事件：Ask Copilot for a repository overview",
            "首要来源发布时间：2026-07-09 14:25 UTC",
        ]
        release.evidence_links[0].update(
            {"source": "GitHub Changelog", "title": release.event_title, "url": "https://github.blog/changelog/repository-overview"}
        )

        self.assertTrue(is_publishable_card(release, strict_auto=True))

    def test_english_model_name_alone_is_not_a_publishable_fact(self):
        release = card(1, "OpenAI News", "OpenAI", "green", 95)
        release.event_title = "GPT-5.6 is now the preferred model in Microsoft 365 Copilot"
        release.key_facts = [
            "OpenAI 相关事件：GPT-5.6 is now the preferred model in Microsoft 365 Copilot",
            "首要来源发布时间：2026-07-09 13:00 UTC",
        ]
        release.evidence_links[0].update(
            {"source": "OpenAI News", "title": release.event_title, "url": "https://openai.com/index/gpt-5-6"}
        )

        self.assertFalse(is_publishable_card(release, strict_auto=True))

    def test_chinese_product_change_with_named_model_is_publishable(self):
        release = card(1, "OpenAI News", "OpenAI", "green", 95)
        release.event_title = "GPT-5.6 接入 Microsoft 365 Copilot"
        release.key_facts = ["GPT-5.6 已成为 Microsoft 365 Copilot 默认模型，并覆盖 Word、Excel 与 PowerPoint。"]
        release.evidence_links[0].update(
            {"source": "OpenAI News", "title": release.event_title, "url": "https://openai.com/index/gpt-5-6"}
        )

        self.assertTrue(is_publishable_card(release, strict_auto=True))

    def test_arxiv_title_and_generic_research_comment_are_not_enough(self):
        paper = card(1, "arXiv cs.CL", "Google / Gemini", "green", 95)
        paper.event_title = "Gemma 4 Technical Report"
        paper.key_facts = [
            "Gemma 4 Technical Report",
            "首要来源发布时间：2026-07-07 10:51 UTC",
            "论文和 benchmark 更适合作为研究线索，落地影响需要后续复现。",
        ]
        paper.evidence_links[0].update(
            {"source": "arXiv cs.CL", "title": paper.event_title, "url": "https://arxiv.org/abs/2607.02770"}
        )

        self.assertFalse(is_publishable_card(paper, strict_auto=True))
        self.assertEqual(select_cards([paper], max_items=5, strict_auto=True), [])

    def test_research_method_and_measured_result_are_publishable(self):
        paper = card(1, "arXiv cs.CL", "Google / Gemini", "green", 95)
        paper.event_title = "Gemma 4 Technical Report"
        paper.key_facts = ["论文提出新的稀疏注意力训练方法，实验结果显示长上下文延迟降低 28%。"]
        paper.evidence_links[0].update(
            {"source": "arXiv cs.CL", "title": paper.event_title, "url": "https://arxiv.org/abs/2607.02770"}
        )

        self.assertTrue(is_publishable_card(paper, strict_auto=True))

    def test_specific_official_release_page_stays_publishable(self):
        hy3 = card(1, "Tencent Hunyuan Official Web", "腾讯混元", "green", 95)
        hy3.event_title = "Hy3 正式发布"
        hy3.key_facts = [
            "腾讯混元 Hy3 正式发布。",
            "来源摘要：元宝上线 Hy3 Agent 能力；API 已在腾讯云 TokenHub 上线；WorkBuddy/CodeBuddy 接入。",
        ]
        hy3.evidence_links[0].update(
            {
                "source": "Tencent Hunyuan Official Web",
                "tier": "A",
                "reliability": "official",
                "title": "Hy3 正式发布",
                "url": "https://hy.tencent.com/research/hy3",
            }
        )

        picked = select_cards([hy3], max_items=5)

        self.assertTrue(is_publishable_card(hy3))
        self.assertEqual(picked, [hy3])

    def test_merges_hf_model_variants_before_selection(self):
        flash = card(1, "DeepSeek Hugging Face Models", "DeepSeek", "green", 95)
        flash.cluster_key = "deepseek-flash"
        flash.event_title = "Hugging Face 模型仓库更新：deepseek-ai/DeepSeek-V4-Flash-DSpark"
        flash.key_facts = ["DeepSeek-V4-Flash-DSpark 已公开 GGUF 权重与 128K 上下文配置。"]
        flash.evidence_links[0].update(
            {
                "title": flash.event_title,
                "url": "https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-DSpark",
            }
        )
        pro = card(2, "DeepSeek Hugging Face Models", "DeepSeek", "green", 94)
        pro.cluster_key = "deepseek-pro"
        pro.event_title = "Hugging Face 模型仓库更新：deepseek-ai/DeepSeek-V4-Pro-DSpark"
        pro.key_facts = ["DeepSeek-V4-Pro-DSpark 同步开放 GGUF 权重与 128K 上下文配置。"]
        pro.evidence_links[0].update(
            {
                "title": pro.event_title,
                "url": "https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro-DSpark",
            }
        )

        picked = select_cards([flash, pro], max_items=5)

        self.assertEqual(len(picked), 1)
        merged = picked[0]
        self.assertIn("DeepSeek-V4-DSpark", merged.event_title)
        self.assertIn("Flash / Pro", merged.event_title)
        self.assertEqual(len(merged.evidence_links), 2)
        self.assertTrue(any("Flash / Pro" in fact for fact in merged.key_facts))

    def test_does_not_merge_unrelated_same_entity_updates(self):
        codex = card(1, "OpenAI News", "OpenAI", "green", 95)
        codex.event_title = "OpenAI 发布 Codex CLI 更新"
        codex.evidence_links[0].update({"title": codex.event_title, "url": "https://openai.example/codex"})
        pricing = card(2, "OpenAI News", "OpenAI", "green", 94)
        pricing.event_title = "OpenAI 调整 API 价格"
        pricing.evidence_links[0].update({"title": pricing.event_title, "url": "https://openai.example/pricing"})

        picked = select_cards([codex, pricing], max_items=9)

        self.assertEqual(len([c for c in picked if c.entity == "OpenAI"]), 2)

    def test_dedupes_same_model_release_across_media_sources(self):
        first = card(1, "36氪", "腾讯混元", "yellow", 82)
        first.event_title = "腾讯混元Hy3正式发布"
        first.key_facts = ["36氪获悉，腾讯混元Hy3正式发布"]
        first.evidence_links[0].update({"source": "36氪", "tier": "B", "reliability": "media", "title": first.event_title})
        second = card(2, "爱范儿", "腾讯混元", "yellow", 80)
        second.event_title = "实测腾讯 Hy3 正式版，这次终于赶上了「AI 下半场」"
        second.key_facts = ["爱范儿提到实测腾讯 Hy3 正式版"]
        second.evidence_links[0].update({"source": "爱范儿", "tier": "B", "reliability": "media", "title": second.event_title})

        picked = select_cards([first, second], max_items=9)

        self.assertEqual(len(picked), 1)
        self.assertEqual(picked[0].evidence_links[0]["source"], "36氪")

    def test_qwen_release_family_merges_official_media_and_community_evidence(self):
        official = card(211, "Qwen official X", "Qwen / 阿里", "green", 96)
        official.event_title = "Qwen3.8 预告:2.4T 参数,Max 预览版已上线"
        official.key_facts = [
            "Qwen 官方预告 Qwen3.8 即将发布,并将于近期开放权重。",
            "Qwen3.8-Max-Preview 已在阿里 Token Plan、Qoder 和 QoderWork 上线供测试。",
        ]
        official.evidence_links[0].update(
            {
                "source": "Qwen official X",
                "tier": "A",
                "reliability": "official_social",
                "title": official.event_title,
                "url": "https://x.com/Alibaba_Qwen/status/211",
                "excerpt": " ".join(official.key_facts),
            }
        )
        media = card(212, "36氪", "Qwen / 阿里", "yellow", 82)
        media.event_title = "阿里最新一代大模型千问3.8将至,正式版预计近期开源"
        media.key_facts = [
            "36氪获悉,Qwen3.8-Max 预览版已率先上线阿里云 Token Plan。",
            "Qwen3.8-Max 正式版将于近期推出并开源。",
        ]
        media.evidence_links[0].update(
            {
                "source": "36氪",
                "tier": "B",
                "reliability": "media",
                "title": media.event_title,
                "url": "https://36kr.com/newsflashes/212",
                "excerpt": " ".join(media.key_facts),
            }
        )
        community = card(213, "TestingCatalog", "Qwen / 阿里", "yellow", 70)
        community.official_count = 0
        community.media_count = 0
        community.community_count = 1
        community.event_title = "社区风向:TestingCatalog 称 Qwen3.8-Max-Preview 已出现测试入口"
        community.key_facts = ["TestingCatalog 称,Qwen3.8-Max-Preview 已在阿里云和 Qwen Chat 出现测试入口。"]
        community.evidence_links[0].update(
            {
                "source": "TestingCatalog",
                "tier": "C",
                "reliability": "community",
                "title": community.event_title,
                "url": "https://x.com/testingcatalog/status/213",
                "excerpt": community.key_facts[0],
            }
        )

        merged = _merge_related_cards([official, media, community])

        self.assertEqual(len(merged), 1)
        story = merged[0]
        self.assertEqual(story.source_cluster_keys, ["k211", "k212", "k213"])
        self.assertEqual(len(story.evidence_links), 3)
        self.assertEqual((story.official_count, story.media_count, story.community_count), (1, 1, 1))
        self.assertEqual(story.event_title, official.event_title)

    def test_qwen_release_family_keeps_branches_and_event_scopes_separate(self):
        release = card(221, "Qwen official", "Qwen / 阿里")
        release.event_title = "Qwen3.8-Max-Preview 正式发布"
        release.key_facts = ["Qwen3.8-Max-Preview 正式发布并开放 API。"]
        release.evidence_links[0]["excerpt"] = release.key_facts[0]
        coder = card(222, "Qwen official", "Qwen / 阿里")
        coder.event_title = "Qwen3.8-Coder 正式发布"
        coder.key_facts = ["Qwen3.8-Coder 正式发布并开放 API。"]
        coder.evidence_links[0]["excerpt"] = coder.key_facts[0]
        vision = card(223, "Qwen official", "Qwen / 阿里")
        vision.event_title = "Qwen3.8-VL 正式发布"
        vision.key_facts = ["Qwen3.8-VL 正式发布并开放 API。"]
        vision.evidence_links[0]["excerpt"] = vision.key_facts[0]
        benchmark = card(224, "Benchmark Lab", "Qwen / 阿里", "yellow")
        benchmark.event_title = "Qwen3.8-Max 基准评测公布"
        benchmark.key_facts = ["Qwen3.8-Max 在统一硬件上的推理基准得分为 82 分。"]
        benchmark.evidence_links[0]["excerpt"] = benchmark.key_facts[0]
        integration = card(225, "GitHub", "Qwen / 阿里")
        integration.event_title = "Qwen3.8-Max 接入 GitHub Copilot"
        integration.key_facts = ["Qwen3.8-Max 已接入 GitHub Copilot。"]
        integration.evidence_links[0]["excerpt"] = integration.key_facts[0]
        personnel = card(226, "36氪", "Qwen / 阿里", "yellow")
        personnel.event_title = "Qwen3.8-Max 发布后阿里模型负责人将离职"
        personnel.key_facts = ["据 36氪 报道,阿里模型负责人 Zhang San 已告知团队自己将离职。"]
        personnel.evidence_links[0]["excerpt"] = personnel.key_facts[0]

        merged = _merge_related_cards([release, coder, vision, benchmark, integration, personnel])

        self.assertEqual(len(merged), 6)
        self.assertEqual({row.cluster_key for row in merged}, {f"k{idx}" for idx in range(221, 227)})

    def test_same_model_integrations_on_different_platforms_do_not_merge(self):
        copilot = card(231, "GitHub", "OpenAI")
        copilot.event_title = "GPT-5.6 available in GitHub Copilot"
        copilot.key_facts = ["GPT-5.6 is available in GitHub Copilot."]
        copilot.evidence_links[0]["excerpt"] = copilot.key_facts[0]
        databricks = card(232, "Databricks", "OpenAI")
        databricks.event_title = "GPT-5.6 available on Databricks Agent Bricks"
        databricks.key_facts = ["GPT-5.6 is available on Databricks Agent Bricks."]
        databricks.evidence_links[0]["excerpt"] = databricks.key_facts[0]

        merged = _merge_related_cards([copilot, databricks])

        self.assertEqual(len(merged), 2)
        self.assertEqual({row.cluster_key for row in merged}, {"k231", "k232"})

    def test_official_url_collapses_media_relay_into_one_official_story(self):
        official = card(31, "CircleCI Official", "CircleCI", "green", 86)
        official.event_title = "Introducing Chunk Sidecars"
        official.key_facts = ["CircleCI introduces Chunk Sidecars for inner-loop CI validation."]
        official.evidence_links[0].update(
            {
                "source": "CircleCI Official",
                "tier": "A",
                "reliability": "official",
                "title": official.event_title,
                "url": "https://circleci.com/blog/chunk-sidecars/",
            }
        )
        relay = card(32, "InfoQ 中文", "CircleCI", "yellow", 83)
        relay.event_title = "CircleCI推出Chunk Sidecars，将CI校验引入AI编码工作流"
        relay.official_count = 1
        relay.media_count = 1
        relay.key_facts = ["CircleCI 发布 Chunk Sidecars，让 AI 智能体在提交前运行测试和校验。"]
        relay.evidence_links = [
            {
                "source": "circleci.com 官方",
                "tier": "A",
                "reliability": "official",
                "title": "Introducing Chunk Sidecars",
                "url": "https://circleci.com/blog/chunk-sidecars",
                "reconciled_official": "true",
                "published_at": "",
            },
            {
                "source": "InfoQ 中文",
                "tier": "B",
                "reliability": "media",
                "title": relay.event_title,
                "url": "https://www.infoq.cn/article/circleci",
                "published_at": "",
            },
        ]

        picked = select_cards([relay, official], max_items=9)

        self.assertEqual(len(picked), 1)
        self.assertEqual(picked[0].event_title, "Introducing Chunk Sidecars")
        self.assertEqual(picked[0].evidence_links[0]["reliability"], "official")

    def test_article_and_newsflash_for_same_media_event_merge_once(self):
        excerpt = (
            "36氪从多位产业人士处获悉,字节跳动正探索进入自动驾驶领域。"
            "这一项目目前由Seed旗下周畅的世界模型团队负责。"
            "字节向36氪回应,公司在物理AI方向有早期研究,但没有开展智能驾驶业务的计划。"
        )
        article = card(41, "36氪", "字节 / 豆包", "yellow", 88)
        article.event_title = "字节探索自动驾驶,Seed世界模型团队负责|36氪独家"
        article.official_count = 0
        article.media_count = 1
        article.key_facts = [excerpt]
        article.evidence_links[0].update(
            {
                "source": "36氪",
                "tier": "B",
                "reliability": "media",
                "title": article.event_title,
                "url": "https://36kr.com/p/3893815451417347",
                "excerpt": excerpt,
            }
        )
        flash = card(42, "36氪", "字节 / 豆包", "yellow", 86)
        flash.event_title = "字节探索自动驾驶,Seed世界模型团队负责"
        flash.official_count = 0
        flash.media_count = 1
        flash.key_facts = [excerpt]
        flash.evidence_links[0].update(
            {
                "source": "36氪",
                "tier": "B",
                "reliability": "media",
                "title": flash.event_title,
                "url": "https://36kr.com/newsflashes/3893818392492550",
                "excerpt": excerpt,
            }
        )

        picked = select_cards([article, flash], max_items=9, min_score=0, strict_auto=True)

        self.assertEqual(len(picked), 1)
        self.assertEqual(len(picked[0].evidence_links), 2)
        self.assertEqual(picked[0].source_count, 1)
        self.assertEqual(picked[0].media_count, 1)
        self.assertEqual(public_source_status(picked[0]), "媒体报道")
        self.assertEqual(build_story_spec(picked[0]).source_status, "single_media")
        self.assertTrue(build_editorial_plan(picked[0]).title.startswith("36氪称"))
        self.assertIn("自动驾驶", picked[0].event_title)


    def test_merges_same_mainstream_event_and_keeps_richer_facts(self):
        sparse = card(1, "36氪", "腾讯混元", "yellow", 82)
        sparse.official_count = 0
        sparse.media_count = 1
        sparse.event_title = "腾讯混元Hy3正式发布"
        sparse.key_facts = ["来源摘要：腾讯混元Hy3正式发布。"]
        sparse.evidence_links[0].update({"source": "36氪", "tier": "B", "reliability": "media", "title": sparse.event_title, "url": "https://36kr.com/hy3"})

        rich = card(2, "InfoQ 中文", "腾讯混元", "yellow", 80)
        rich.official_count = 0
        rich.media_count = 1
        rich.event_title = "腾讯混元Hy3正式发布，元宝同步上线Hy3 Agent能力、免费开放"
        rich.key_facts = [
            "来源摘要：Hy3 已在 WorkBuddy/CodeBuddy、元宝、Marvis、ima 等业务接入；API 已在腾讯云 TokenHub 上线；元宝上线 Hy3 Agent 能力并免费开放。"
        ]
        rich.evidence_links[0].update({"source": "InfoQ 中文", "tier": "B", "reliability": "media", "title": rich.event_title, "url": "https://www.infoq.cn/hy3"})

        picked = select_cards([sparse, rich], max_items=9)

        self.assertEqual(len(picked), 1)
        merged = picked[0]
        joined = "\n".join(merged.key_facts)
        self.assertEqual(len(merged.evidence_links), 2)
        self.assertIn("TokenHub", joined)
        self.assertIn("WorkBuddy", joined)
        self.assertIn("元宝", joined)

    def test_category_limits_keep_morning_digest_balanced(self):
        model_a = card(1, "HF A", "DeepSeek", "green", 99)
        model_a.event_title = "Hugging Face 模型仓库更新：deepseek-ai/DeepSeek-V4-Flash"
        model_a.evidence_links[0]["url"] = "https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash"
        model_a.key_facts = ["DeepSeek-V4-Flash 已提供 GGUF 下载和 128K 上下文说明。"]
        model_b = card(2, "HF B", "Qwen / 阿里", "green", 98)
        model_b.event_title = "Hugging Face 模型仓库更新：Qwen/Qwen3-Next"
        model_b.evidence_links[0]["url"] = "https://huggingface.co/Qwen/Qwen3-Next"
        model_b.key_facts = ["Qwen3-Next 已更新模型卡，列出 API 调用和许可信息。"]
        product = card(3, "OpenAI News", "OpenAI", "green", 80)
        product.event_title = "OpenAI 发布 ChatGPT 产品更新"
        product.evidence_links[0]["url"] = "https://openai.example/chatgpt"

        picked = select_cards([model_a, model_b, product], max_items=2, category_limits={"model_repository": 1})

        self.assertEqual(len(picked), 2)
        self.assertEqual(sum("huggingface.co" in c.evidence_links[0]["url"] for c in picked), 1)
        self.assertIn(product, picked)

    def test_category_limits_are_not_relaxed_to_fill_digest(self):
        first = card(1, "HF A", "DeepSeek", "green", 99)
        first.event_title = "Hugging Face 模型仓库更新：deepseek-ai/DeepSeek-V4"
        first.evidence_links[0]["url"] = "https://huggingface.co/deepseek-ai/DeepSeek-V4"
        first.key_facts = ["DeepSeek-V4 已开放 GGUF 下载和许可说明。"]
        second = card(2, "HF B", "Qwen / 阿里", "green", 98)
        second.event_title = "Hugging Face 模型仓库更新：Qwen/Qwen3-Next"
        second.evidence_links[0]["url"] = "https://huggingface.co/Qwen/Qwen3-Next"
        second.key_facts = ["Qwen3-Next 已公开模型卡和 API 入口。"]

        picked = select_cards([first, second], max_items=2, category_limits={"model_repository": 1})

        self.assertEqual(len(picked), 1)
        self.assertEqual(picked[0], first)

    def test_estimated_news_timeline_matches_product_budget(self):
        headline = card(1, "OpenAI News", "OpenAI", "green", 95)
        brief = card(2, "InfoQ", "Anthropic / Claude", "yellow", 80)
        brief.editorial_tier = "brief"
        rows = timeline_for_cards([headline, brief])

        durations = [dur for _start, _card, dur in rows]
        self.assertTrue(28 <= durations[0] <= 45)
        self.assertTrue(16 <= durations[1] <= 24)

    def test_portfolio_has_exactly_one_headline_and_four_briefs(self):
        items = [card(i, f"Source {i}", f"Entity {i}", "green", 100-i) for i in range(1, 6)]
        for item in items:
            item.key_facts = [f"{item.entity} 已公开具体产品能力和可用入口。"]
        portfolio = select_card_portfolio(items, max_items=5, min_score=0)
        self.assertEqual(len(portfolio.items), 5)
        self.assertEqual(sum(x.editorial_tier == "headline" for x in portfolio.items), 1)
        self.assertEqual(sum(x.editorial_tier == "brief" for x in portfolio.items), 4)
        total = 11 + sum(d for _, _, d in timeline_for_cards(portfolio.items)) + 20
        self.assertTrue(125 <= total <= 165)

    def test_timeline_uses_rendered_script_title(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            original = card(1, "OpenAI News", "OpenAI")
            original.event_title = "采集阶段标题"
            (root / "script.json").write_text(
                json.dumps(
                    [
                        {"kind": "intro", "title": "AI 日报", "start": 0, "duration": 5},
                        {"kind": "news", "title": "成片真实标题", "start": 5, "duration": 22},
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8-sig",
            )

            rows = timeline_from_script(root / "script.json", [original])

            self.assertEqual(rows[0][0], 5)
            self.assertEqual(rows[0][1].event_title, "成片真实标题")


class WriterPackagingTests(unittest.TestCase):
    def test_clickbait_discovery_title_never_reaches_bilibili_timeline_or_manifest(self):
        item = card(1, "量子位", "OpenAI", "yellow", 90)
        item.event_title = "GPT-5.6刚发布，OpenAI安全主管就跑路了??"
        item.key_facts = ["据 WIRED 消息，OpenAI 安全系统负责人 Johannes Heidecke 已告知员工自己将离职。"]
        item.evidence_links[0].update(
            {
                "source": "WIRED",
                "tier": "B",
                "reliability": "media",
                "url": "https://www.wired.com/story/openai-safety-leader-departure/",
                "excerpt": item.key_facts[0],
            }
        )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_package(root, "2026-07-13", [item], [], selected_override=[item])
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            public_text = "\n".join(
                [
                    (root / "bilibili.md").read_text(encoding="utf-8-sig"),
                    (root / "pinned-comment.md").read_text(encoding="utf-8-sig"),
                    manifest["selected"][0]["title"],
                ]
            )

        self.assertIn("OpenAI 安全系统负责人 Johannes Heidecke 将离职", public_text)
        self.assertNotIn("跑路", public_text)
        self.assertNotIn("??", public_text)

    def test_unconfirmed_secondary_signal_never_enters_title_candidates(self):
        lead = card(1, "OpenAI News", "OpenAI", "green", 95)
        lead.event_title = "OpenAI 发布可靠产品更新"
        lead.key_facts = ["OpenAI 官方页面确认新增一项开发者功能。"]
        lead.editorial_tier = "headline"
        signal = card(2, "X / Staff", "OpenAI", "yellow", 90)
        signal.event_title = "团队成员暗示秘密模型即将上线"
        signal.key_facts = ["团队成员在 X 提到一项仍未正式公告的实验。"]
        signal.evidence_links[0].update(
            {"url": "https://x.com/staff/status/123", "reliability": "official_personnel"}
        )
        signal.editorial_tier = "brief"
        reliable = card(3, "GitHub Changelog", "GitHub", "green", 85)
        reliable.event_title = "GitHub 新增仓库概览"
        reliable.key_facts = ["GitHub 官方更新新增仓库概览入口。"]
        reliable.editorial_tier = "brief"

        with tempfile.TemporaryDirectory() as td:
            _timeline, titles = write_bilibili_md(
                Path(td) / "bilibili.md",
                [lead, signal, reliable],
                "1080p",
                "2026-07-11",
            )

        self.assertTrue(titles)
        self.assertTrue(all("秘密模型" not in title for title in titles))

    def test_timeline_does_not_repeat_entity_already_in_rendered_title(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            item = card(1, "GitHub Changelog", "GitHub")
            item.event_title = "GitHub新增仓库概览能力并开放配置入口"

            timeline, _titles = write_bilibili_md(root / "bilibili.md", [item], "1080p", "2026-07-04")
            write_pinned_comment(root / "pinned-comment.md", timeline)

            public_text = (root / "bilibili.md").read_text(encoding="utf-8-sig") + (root / "pinned-comment.md").read_text(encoding="utf-8-sig")
            self.assertIn("GitHub新增仓库概览能力", public_text)
            self.assertNotIn("GitHub｜GitHub", public_text)

    def test_timeline_keeps_complete_news_title(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            item = card(1, "ChatGPT official", "OpenAI")
            item.event_title = "ChatGPT 在欧洲经济区恢复 WhatsApp 接入，并新增 Kakao 与 Viber"

            timeline, _titles = write_bilibili_md(root / "bilibili.md", [item], "1080p", "2026-07-04")
            write_pinned_comment(root / "pinned-comment.md", timeline)

            public_text = (root / "bilibili.md").read_text(encoding="utf-8-sig") + (root / "pinned-comment.md").read_text(encoding="utf-8-sig")
            self.assertIn("Kakao 与 Viber", public_text)
            self.assertNotIn("Kakao 与 Vi\n", public_text)

    def test_timeline_does_not_repeat_entity_after_signal_label(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            item = card(1, "X / Staff", "OpenAI", "yellow")
            item.event_title = "一线消息｜OpenAI 团队成员展示多智能体实验"
            item.evidence_links[0].update(
                {
                    "url": "https://x.com/staff/status/123",
                    "reliability": "official_personnel",
                }
            )

            timeline, _titles = write_bilibili_md(root / "bilibili.md", [item], "1080p", "2026-07-04")
            write_pinned_comment(root / "pinned-comment.md", timeline)

            public_text = (root / "bilibili.md").read_text(encoding="utf-8-sig") + (root / "pinned-comment.md").read_text(encoding="utf-8-sig")
            self.assertIn("一线消息｜OpenAI", public_text)
            self.assertNotIn("OpenAI｜一线消息｜OpenAI", public_text)

    def test_timeline_prefers_repaired_story_entity_over_stale_bucket(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            item = card(1, "InfoQ 中文", "本地模型", "yellow")
            item.event_title = "小扎“消失”三年后发帖,只为它:Meta最强Agent模型进军编程"
            item.key_facts = [
                "近日,Meta 正式发布了新版 Muse Spark,这是一款面向智能体编程的多模态 AI 模型。",
                "Muse Spark 1.1 重点覆盖工具调用和计算机操作。",
            ]
            item.evidence_links[0].update(
                {
                    "source": "InfoQ 中文",
                    "tier": "B",
                    "reliability": "media",
                    "url": "https://www.infoq.cn/article/muse-spark",
                    "title": item.event_title,
                    "excerpt": " ".join(item.key_facts),
                }
            )

            timeline, _titles = write_bilibili_md(root / "bilibili.md", [item], "1080p", "2026-07-04")
            write_pinned_comment(root / "pinned-comment.md", timeline)

            public_text = (root / "bilibili.md").read_text(encoding="utf-8-sig") + (root / "pinned-comment.md").read_text(encoding="utf-8-sig")
            self.assertIn("00:11 Meta 正式发布新版 Muse Spark", public_text)
            self.assertNotIn("本地模型｜", public_text)
            self.assertNotIn("本地模型 的实际效果", public_text)

    def test_titles_are_plain_and_date_suffixed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            timeline, titles = write_bilibili_md(root / "bilibili.md", [card(1, "ModelScope GitHub Releases", "ModelScope")], "1080p", "2026-07-04")
            self.assertIn(DATE_SUFFIX, titles[0])
            self.assertNotIn(SHOCK_1, titles[0])
            self.assertNotIn(SHOCK_2, titles[0])
            self.assertTrue(timeline)

    def test_raw_english_lead_does_not_become_bilibili_title(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            paper = card(1, "NVIDIA Developer Blog", "论文 / arXiv", "yellow")
            paper.event_title = "Build an AI Scientist for Life Science Discovery with NVIDIA BioNeMo Agent Toolkit"
            _timeline, titles = write_bilibili_md(root / "bilibili.md", [paper], "1080p", "2026-07-04")

            self.assertNotIn("Build an AI Scientist", titles[0])
            self.assertIn("AI 日报", titles[0])

    def test_github_releases_title_does_not_leave_trailing_s(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            release = card(1, "OpenAI Codex GitHub Releases", "OpenAI")
            release.event_title = "OpenAI Codex GitHub Releases 发布 0.143.0-alpha.36"
            _timeline, titles = write_bilibili_md(root / "bilibili.md", [release], "1080p", "2026-07-04")
            public_text = "\n".join(titles) + "\n" + (root / "bilibili.md").read_text(encoding="utf-8-sig")
            self.assertIn("OpenAI Codex 预发布 0.143.0-alpha.36", public_text)
            self.assertNotIn("Codex s 发布", public_text)
            self.assertNotIn("Codex  发布", public_text)

    def test_hugging_face_model_update_is_not_called_publish(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            hf = card(1, "Google Hugging Face Models", "Google")
            hf.event_title = "Hugging Face 模型仓库更新：google/tabfm-1.0.0-jax"
            hf.evidence_links[0]["url"] = "https://huggingface.co/google/tabfm-1.0.0-jax"
            timeline, titles = write_bilibili_md(root / "bilibili.md", [hf], "1080p", "2026-07-04")
            write_pinned_comment(root / "pinned-comment.md", timeline)
            public_text = "\n".join(
                [
                    "\n".join(titles),
                    (root / "bilibili.md").read_text(encoding="utf-8-sig"),
                    (root / "pinned-comment.md").read_text(encoding="utf-8-sig"),
                ]
            )
            self.assertIn("Google 模型页更新 tabfm-1.0.0-jax", public_text)
            self.assertNotIn("Google 发布 tabfm-1.0.0-jax", public_text)

    def test_bilibili_json_desc_stays_under_open_platform_limit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            timeline = [(0, card(1, "A", "OpenAI"), 10), (12, card(2, "B", "Mistral"), 10), (24, card(3, "C", "GitHub / Copilot"), 10)]
            write_bilibili_json(root / "bilibili.json", root, timeline, ["AI Daily" + DATE_SUFFIX])
            write_pinned_comment(root / "pinned-comment.md", timeline)
            payload = json.loads((root / "bilibili.json").read_text(encoding="utf-8-sig"))
            pinned = (root / "pinned-comment.md").read_text(encoding="utf-8-sig")
            self.assertLess(len(payload["desc"]), 250)
            self.assertIn("置顶评论", payload["desc"])
            self.assertNotIn("00:24", payload["desc"])
            self.assertIn("00:24", pinned)

    def test_public_copy_has_no_internal_markers(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            timeline = [(0, card(1, "A", "OpenAI"), 10), (12, card(2, "B", "Mistral"), 10)]
            write_bilibili_md(root / "bilibili.md", [row[1] for row in timeline], "1080p", "2026-07-04", timeline=timeline)
            write_bilibili_json(root / "bilibili.json", root, timeline, ["AI Daily" + DATE_SUFFIX])
            write_pinned_comment(root / "pinned-comment.md", timeline)
            payload = json.loads((root / "bilibili.json").read_text(encoding="utf-8-sig"))
            public_text = "\n".join(
                [
                    (root / "bilibili.md").read_text(encoding="utf-8-sig"),
                    payload["desc"],
                    (root / "pinned-comment.md").read_text(encoding="utf-8-sig"),
                ]
            )
            for banned in BANNED_PUBLIC:
                self.assertNotIn(banned, public_text)

    def test_manifest_records_background_content_quality(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sample = card(1, "OpenAI News", "OpenAI", "green", 90)
            sample.event_title = "OpenAI 发布 Codex CLI 版本 v1.2.3"
            sample.key_facts = [
                "OpenAI 相关事件：Codex CLI 发布 v1.2.3",
                "来源摘要：新增配置项，修复安装问题，官方发布记录给出版本号。",
            ]

            write_package(root, "2026-07-04", [sample], [], max_items=1)
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))

            self.assertIn("content_quality", manifest)
            self.assertIn("selection_balance", manifest)
            self.assertGreater(manifest["content_quality"]["average_score"], 0)
            self.assertIn("content_quality", manifest["selected"][0])
            self.assertIn("score", manifest["selected"][0]["content_quality"])
            self.assertIn("selection_quality", manifest["selected"][0])
            self.assertIn("enrichment", manifest["selected"][0])
            self.assertIn("whats_new", manifest["selected"][0]["enrichment"])


if __name__ == "__main__":
    unittest.main()

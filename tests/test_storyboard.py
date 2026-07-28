import unittest
from datetime import datetime, timezone
from pathlib import Path

from briefing.models import EvidenceCard
from briefing.render import _browser_slides, _browser_timeline_items, _segments, _visual_pages_for_browser
from briefing.editorial_plan import _facts_too_similar, _normalize_public_fact
from briefing.storyboard import build_storyboard


class StoryboardTests(unittest.TestCase):
    def card(self, title: str, fact: str, *, official: int = 1, media: int = 0) -> EvidenceCard:
        return EvidenceCard(
            cluster_key="storyboard-1", event_title=title, entity="Acme", risk="green" if official else "yellow",
            confidence=90, selected=True, reason="", source_count=1, official_count=official, media_count=media,
            community_count=0, first_seen_at=datetime.now(timezone.utc), latest_published_at=datetime.now(timezone.utc),
            key_facts=[f"来源摘要：{fact}"], evidence_links=[{"source": "Source", "url": "https://example.com"}],
            uncertainty=[] if official else ["单一媒体"], score=80,
        )

    def test_metric_story_chooses_metric_comparison_without_brand_rules(self):
        board = build_storyboard(self.card("Acme 发布 Nova-7", "Nova-7 延迟从 520ms 降至 460ms，成本降低 18%。"))

        self.assertEqual(board.template, "metric_comparison")
        self.assertEqual(board.beats[0].intent, "metric")
        self.assertIn("520ms", board.beats[0].body)

    def test_long_source_fact_becomes_a_complete_dense_card(self):
        raw = (
            "OpenAI表示,在 Agents’ Last Exam 覆盖55个专业领域的长周期工作流测试上,"
            "Sol以53.6分刷新纪录,比 Fable 5 高出13.1分 GPT-5.6一发布,"
            "量子位公众号与导航文字继续堆在后面"
        )

        fact = _normalize_public_fact(raw)

        self.assertLessEqual(len(fact), 92)
        self.assertIn("53.6", fact)
        self.assertNotRegex(fact, r"(?:比|与|和|Fable)$")
        self.assertNotIn("公众号", fact)
        self.assertNotIn("…", fact)

    def test_overlapping_benchmark_facts_are_recognized(self):
        first = "OpenAI 在 Agents Last Exam 上表示，GPT-5.6 Sol 以 53.6 分刷新纪录"
        second = "GPT-5.6 Sol 在内部 Agents Last Exam 长周期工作流测试中取得 53.6 分"

        self.assertTrue(_facts_too_similar(first, second))

    def test_programmatic_tool_calling_is_not_mistaken_for_pro_availability(self):
        board = build_storyboard(
            self.card(
                "Acme 更新 Agent 工具",
                "Acme 引入程序化工具调用 Programmatic Tool Calling，Agent 可以过滤中间结果。",
            )
        )

        self.assertEqual(board.beats[0].title, "Agent 能力")

    def test_structured_render_uses_storyboard_cards(self):
        sample = self.card("Acme 发布 Nova-7", "Nova-7 延迟从 520ms 降至 460ms，成本降低 18%。")

        row = [item for item in _segments([sample]) if item.get("kind") == "news"][0]

        self.assertEqual(row["generation_path"], "structured_editorial_plan")
        self.assertEqual(row["card_design"]["theme"], "metric_comparison")
        self.assertEqual(row["cards"][0]["title"], "评测与性能")
        self.assertIn("520ms", row["cards"][0]["body"])

    def test_normalized_fact_keeps_its_supporting_url(self):
        sample = self.card("Acme 发布 Nova-7", "Nova-7 新增 128K 上下文，并开放 API。")
        sample.evidence_links[0]["excerpt"] = sample.key_facts[0]

        board = build_storyboard(sample)

        self.assertEqual(board.beats[0].evidence_url, "https://example.com")

    def test_product_news_does_not_get_generic_impact_filler(self):
        sample = self.card("GitHub Copilot 新增仓库概览", "GitHub Copilot 可为首次打开的仓库生成高层概览。")
        sample.editorial_tier = "brief"
        sample.evidence_links[0]["excerpt"] = sample.key_facts[0]

        board = build_storyboard(sample)
        row = [item for item in _segments([sample]) if item.get("kind") == "news"][0]

        self.assertNotIn("impact", {beat.intent for beat in board.beats})
        self.assertNotIn("影响对象", {card["title"] for card in row["cards"]})
        self.assertIn("原文与时间", {card["title"] for card in row["cards"]})

    def test_single_media_story_requires_evidence_frame(self):
        board = build_storyboard(
            self.card("某团队用智能体重写运行时", "迁移涉及 120 万行代码和 6800 次提交。", official=0, media=1)
        )

        self.assertTrue(board.needs_evidence_frame)
        self.assertTrue(any(beat.intent == "caution" for beat in board.beats))
        caution = next(beat for beat in board.beats if beat.intent == "caution")
        self.assertEqual(caution.title, "来源说明")
        self.assertNotIn("处理", caution.body)
        self.assertNotIn("第二来源", caution.body)

    def test_brief_story_uses_compact_visual_and_short_narration(self):
        sample = self.card("GitHub Copilot 新增仓库概览", "GitHub Copilot 现在可以为首次打开的仓库生成高层概览。")
        sample.editorial_tier = "brief"

        row = [item for item in _segments([sample]) if item.get("kind") == "news"][0]

        self.assertEqual(row["editorial_tier"], "brief")
        self.assertEqual(row["card_design"]["theme"], "brief_strip")
        self.assertLessEqual(len(row["text"]), 140)
        self.assertEqual(len(row["visual_pages"]), 1)

    def test_browser_slide_keeps_story_position_separate_from_page_index(self):
        segment = {
            "kind": "news",
            "position": 2,
            "total": 5,
            "title": "第二条快讯",
            "caption": "第二条快讯",
            "active_tab": "开发者工具",
            "visual_pages": [
                {"kind": "brief", "title": "第二条快讯", "cards": []},
                {"kind": "evidence", "title": "证据页", "cards": []},
            ],
        }

        slides = _browser_slides(Path("visual-qa"), [segment], [24.0], "1080p")

        self.assertEqual([slide["index"] for slide in slides], [0, 1])
        self.assertEqual([slide["storyPosition"] for slide in slides], [2, 2])
        self.assertEqual([slide["storyTotal"] for slide in slides], [5, 5])

    def test_signoff_is_short_and_has_no_standalone_visual_content(self):
        outro = _segments([self.card("Acme 发布 Nova-7", "Nova-7 已开放 API。")])[-1]

        self.assertEqual(outro["kind"], "outro")
        self.assertEqual(outro["text"], "今天的新闻播完了。置顶评论有个问题等你聊，觉得有用就点个关注，我们明天早上见。")
        self.assertEqual(outro["cards"], [])
        self.assertEqual(outro["visual_pages"][0]["kind"], "closing_continuation")

    def test_timeline_separates_entities_and_keeps_signoff_as_its_own_stage(self):
        segments = [
            {"kind": "intro", "bottom_active": "开场", "active_tab": "Intro"},
            {"kind": "news", "bottom_active": "01 Codex", "active_tab": "开发者工具"},
            {"kind": "news", "bottom_active": "02 Grok", "active_tab": "模型更新"},
            {"kind": "outro", "bottom_active": "收尾", "active_tab": "Outro"},
        ]

        items = _browser_timeline_items(segments, [6.0, 12.0, 10.0, 4.0])

        self.assertEqual([item["entity"] for item in items], ["开场", "Codex", "Grok", "收尾"])
        self.assertEqual(items[-1]["kind"], "outro")
        self.assertEqual(items[-1]["start"], 28.0)
        self.assertEqual(items[-1]["end"], 32.0)
        self.assertEqual(items[-1]["duration"], 4.0)

    def test_timeline_uses_product_names_for_adjacent_openai_stories(self):
        segments = [
            {"kind": "intro", "bottom_active": "开场", "active_tab": "Intro"},
            {
                "kind": "news",
                "title": "OpenAI Build Week 开放项目投稿",
                "bottom_active": "01 OpenAI",
                "active_tab": "开发者工具",
            },
            {
                "kind": "news",
                "title": "ChatGPT 在欧洲经济区重新接入 WhatsApp",
                "bottom_active": "02 OpenAI",
                "active_tab": "开发者工具",
            },
            {
                "kind": "news",
                "title": "一线消息｜Codex 与 ChatGPT Work 获得储备用量重置",
                "bottom_active": "03 OpenAI",
                "active_tab": "开发者工具",
            },
        ]

        items = _browser_timeline_items(segments, [3.0, 6.0, 7.0, 8.0])

        self.assertEqual([item["entity"] for item in items], ["开场", "OpenAI", "ChatGPT", "Codex"])

    def test_short_story_keeps_required_evidence_page(self):
        pages = [
            {"kind": "cards", "title": "OpenAI Build Week", "cards": []},
            {
                "kind": "evidence",
                "title": "原文画面",
                "evidenceVisual": {"required": True, "status": "captured", "image": "source.png"},
            },
            {
                "kind": "evidence",
                "title": "补充原帖",
                "evidenceVisual": {"required": True, "status": "captured", "image": "source-2.png"},
            },
            {
                "kind": "evidence",
                "title": "可选画面",
                "evidenceVisual": {"required": False, "status": "captured", "image": "optional.png"},
            },
        ]

        selected = _visual_pages_for_browser({"visual_pages": pages}, 8.5)

        self.assertEqual([page.get("title") for page in selected], ["OpenAI Build Week", "原文画面", "补充原帖"])

    def test_browser_slides_keep_all_required_evidence_without_changing_story_window(self):
        pages = [
            {"kind": "brief", "title": "主画面", "cards": []},
            {"kind": "cards", "title": "补充卡片", "cards": []},
            {
                "kind": "evidence",
                "title": "证据一",
                "evidenceVisual": {"required": True, "status": "captured", "image": "one.png"},
            },
            {
                "kind": "evidence",
                "title": "证据二",
                "evidenceVisual": {"required": True, "status": "captured", "image": "two.png"},
            },
        ]
        segment = {
            "kind": "news",
            "position": 1,
            "total": 1,
            "title": "多证据新闻",
            "caption": "多证据新闻",
            "active_tab": "模型更新",
            "bottom_active": "01 多证据",
            "visual_pages": pages,
        }

        slides = _browser_slides(Path("visual-qa"), [segment], [12.0], "1080p")

        self.assertEqual([slide["page"]["title"] for slide in slides], ["主画面", "补充卡片", "证据一", "证据二"])
        self.assertEqual(slides[0]["start"], 0.0)
        self.assertEqual(slides[-1]["end"], 12.0)
        self.assertEqual(sum(slide["duration"] for slide in slides), 12.0)
        self.assertTrue(all(slide["timelineTotal"] == 12.0 for slide in slides))


if __name__ == "__main__":
    unittest.main()

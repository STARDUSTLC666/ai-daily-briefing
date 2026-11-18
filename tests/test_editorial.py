from __future__ import annotations

import copy
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from briefing.core import UTC, EvidenceCard, FeedItem, SourceHealth, read_json, write_json_atomic
from briefing.editorial import (
    SIGNAL_DISCLAIMERS,
    SOURCE_STATUSES,
    STORY_CATEGORIES,
    EditionBrief,
    FactClaim,
    build_edition_brief,
    build_editorial_plan,
    build_story_spec,
    build_storyboard,
    cluster_items,
    contains_sensational_wording,
    merge_related_cards,
    public_source_status,
    refresh_bilibili_outputs,
    select_card_portfolio,
    story_category,
    write_package,
)


def _make_publishable_card(
    cluster_key: str,
    *,
    entity: str = "OpenAI",
    event_title: str = "OpenAI 正式发布 GPT-5 多模态推理模型",
    risk: str = "green",
    confidence: int = 92,
    official_count: int = 1,
    media_count: int = 0,
    community_count: int = 0,
    key_facts: list[str] | None = None,
    evidence_links: list[dict[str, str]] | None = None,
    editorial_tier: str = "headline",
    source_cluster_keys: list[str] | None = None,
) -> EvidenceCard:
    now = datetime(2026, 7, 3, 9, 0, tzinfo=UTC)
    facts = key_facts if key_facts is not None else [
        "OpenAI 正式发布 GPT-5 多模态推理模型，上下文窗口扩展至 400K tokens",
        "GPT-5 API 输入调用价格降低 50%，现已向全球开发者开放接入",
    ]
    links = evidence_links if evidence_links is not None else [
        {
            "source": "OpenAI News",
            "tier": "A",
            "reliability": "official",
            "title": event_title,
            "url": f"https://openai.com/index/{cluster_key.replace(':', '-')}",
            "excerpt": "；".join(facts),
            "excerpt_provenance": "article_excerpts",
        }
    ]
    return EvidenceCard(
        cluster_key=cluster_key,
        event_title=event_title,
        entity=entity,
        risk=risk,
        confidence=confidence,
        selected=True,
        reason="verified",
        source_count=max(1, len(links)),
        official_count=official_count,
        media_count=media_count,
        community_count=community_count,
        first_seen_at=now,
        latest_published_at=now,
        key_facts=facts,
        evidence_links=links,
        uncertainty=[],
        score=90.0,
        editorial_tier=editorial_tier,
        source_cluster_keys=list(source_cluster_keys or []),
    )


class ClusterTests(unittest.TestCase):
    def test_cluster_items_launch_wave_personnel_separation_and_hf_family(self) -> None:
        now = datetime(2026, 7, 3, 10, 0, tzinfo=UTC)
        official_launch = FeedItem(
            source_id="openai_news",
            source_name="OpenAI News",
            source_tier="A",
            source_reliability="official",
            title="OpenAI 正式发布 GPT-5 多模态模型",
            link="https://openai.com/index/gpt-5",
            guid="oa-1",
            summary="GPT-5 released.",
            published_at=now - timedelta(minutes=30),
        )
        media_launch = FeedItem(
            source_id="36kr_ai",
            source_name="36氪",
            source_tier="B",
            source_reliability="media",
            title="OpenAI 推出 GPT-5，上下文与编码能力升级",
            link="https://36kr.com/p/101",
            guid="kr-101",
            summary="Media report on GPT-5.",
            published_at=now,
        )
        personnel_depart = FeedItem(
            source_id="36kr_ai",
            source_name="36氪",
            source_tier="B",
            source_reliability="media",
            title="OpenAI 安全负责人宣布离职，曾参与 GPT-5 安全评估",
            link="https://36kr.com/p/102",
            guid="kr-102",
            summary="Safety lead departs OpenAI.",
            published_at=now - timedelta(minutes=10),
        )
        hf_7b = FeedItem(
            source_id="qwen_hf_models",
            source_name="Qwen HF",
            source_tier="A",
            source_reliability="official",
            title="Hugging Face 模型仓库更新：Qwen/Qwen3-7B-Instruct",
            link="https://huggingface.co/Qwen/Qwen3-7B-Instruct",
            guid="hf-7b",
            published_at=now - timedelta(hours=1),
            raw={"model_id": "Qwen/Qwen3-7B-Instruct"},
        )
        hf_72b_fp8 = FeedItem(
            source_id="qwen_hf_models",
            source_name="Qwen HF",
            source_tier="A",
            source_reliability="official",
            title="Hugging Face 模型仓库更新：Qwen/Qwen3-72B-FP8",
            link="https://huggingface.co/Qwen/Qwen3-72B-FP8",
            guid="hf-72b",
            published_at=now - timedelta(minutes=45),
            raw={"model_id": "Qwen/Qwen3-72B-FP8"},
        )

        clusters = cluster_items([media_launch, official_launch, personnel_depart, hf_7b, hf_72b_fp8])
        by_key = {c.key: c for c in clusters}
        self.assertEqual(len(clusters), 3)

        launch_cluster = by_key["OpenAI:event-model-launch-gpt-5"]
        self.assertEqual(len(launch_cluster.items), 2)
        # Official dated item leads the cluster even though media item was published 30m later
        self.assertEqual(launch_cluster.items[0].guid, "oa-1")

        hf_cluster = by_key["Qwen / 阿里:hf-qwen-qwen3"]
        self.assertEqual({it.guid for it in hf_cluster.items}, {"hf-7b", "hf-72b"})

        personnel_clusters = [c for c in clusters if any(it.guid == "kr-102" for it in c.items)]
        self.assertEqual(len(personnel_clusters), 1)
        self.assertNotEqual(personnel_clusters[0].key, launch_cluster.key)


class ClaimsTests(unittest.TestCase):
    def test_build_story_spec_numeric_provenance_clickbait_and_disclaimers(self) -> None:
        card = _make_publishable_card(
            "openai:gpt-5",
            event_title="重磅突发！王炸！！！OpenAI 正式发布 GPT-5 多模态模型 | 量子位首发",
            key_facts=[
                "来源摘要：OpenAI 正式发布 GPT-5 多模态模型，上下文窗口提升至 400K tokens。同时推理吞吐速度提升 85%。",
            ],
            evidence_links=[
                {
                    "source": "OpenAI News",
                    "tier": "A",
                    "reliability": "official",
                    "title": "GPT-5 Release",
                    "url": "https://openai.com/index/gpt-5",
                    "excerpt": "OpenAI 正式发布 GPT-5 多模态模型，上下文窗口提升至 400K tokens。",
                    "excerpt_provenance": "article_excerpts",
                },
                {
                    "source": "Media RSS",
                    "tier": "B",
                    "reliability": "media",
                    "title": "GPT-5 Feed Summary",
                    "url": "https://media.example.com/gpt-5",
                    "excerpt": "推理吞吐速度提升 85%。",
                    "excerpt_provenance": "feed_summary",
                },
            ],
        )
        self.assertTrue(contains_sensational_wording(card.event_title))
        spec = build_story_spec(card)
        self.assertFalse(contains_sensational_wording(spec.semantic_headline))
        self.assertNotIn("量子位", spec.semantic_headline)
        self.assertEqual(spec.semantic_headline, "OpenAI 正式发布 GPT-5 多模态模型")

        claims_by_num: dict[str, FactClaim] = {}
        for claim in spec.claims:
            for tok in claim.numeric_tokens:
                claims_by_num[tok] = claim

        # 400 is backed by article_excerpts -> verifiable=True
        self.assertIn("400", claims_by_num)
        self.assertTrue(claims_by_num["400"].verifiable)
        self.assertEqual(claims_by_num["400"].evidence_urls, ["https://openai.com/index/gpt-5"])

        # 85 only appears in feed_summary -> rejected for numeric verification
        self.assertIn("85", claims_by_num)
        self.assertFalse(claims_by_num["85"].verifiable)
        self.assertEqual(claims_by_num["85"].evidence_urls, [])

        # Official personnel disclaimer + "跑路" neutralization
        personnel_card = _make_publishable_card(
            "openai:staff-note",
            event_title="突发！OpenAI 研究员跑路后回应团队调整",
            official_count=0,
            community_count=1,
            risk="yellow",
            key_facts=["OpenAI 研究员在个人账号宣布离职并加入新实验室"],
            evidence_links=[
                {
                    "source": "X Staff",
                    "tier": "B",
                    "reliability": "official_personnel",
                    "title": "Staff post",
                    "url": "https://x.com/researcher/status/1940685355315200000",
                    "excerpt": "OpenAI 研究员在个人账号宣布离职并加入新实验室",
                    "excerpt_provenance": "social_post",
                }
            ],
        )
        p_spec = build_story_spec(personnel_card)
        self.assertEqual(p_spec.signal_kind, "official_personnel")
        self.assertEqual(p_spec.disclaimer, SIGNAL_DISCLAIMERS["official_personnel"])
        self.assertNotIn("跑路", p_spec.semantic_headline)

        # Community rumour disclaimer
        rumour_card = _make_publishable_card(
            "community:leak",
            entity="Anthropic / Claude",
            event_title="社区实测新模型推理表现",
            official_count=0,
            media_count=0,
            community_count=1,
            risk="yellow",
            key_facts=["开发者社区实测 Claude 新快照在代码任务中延迟降低 20%"],
            evidence_links=[
                {
                    "source": "Reddit LocalLLaMA",
                    "tier": "C",
                    "reliability": "community",
                    "title": "Community test",
                    "url": "https://reddit.com/r/LocalLLaMA/comments/xyz",
                    "excerpt": "开发者社区实测 Claude 新快照在代码任务中延迟降低 20%",
                    "excerpt_provenance": "article_excerpts",
                }
            ],
        )
        r_spec = build_story_spec(rumour_card)
        self.assertEqual(r_spec.signal_kind, "community_rumour")
        self.assertEqual(r_spec.disclaimer, SIGNAL_DISCLAIMERS["community_rumour"])


class PlanTests(unittest.TestCase):
    def test_editorial_plan_no_ellipses_disclaimer_storyboard_and_edition_brief(self) -> None:
        official_card = _make_publishable_card(
            "openai:gpt-5",
            event_title="OpenAI 正式发布 GPT-5 多模态推理模型……...",
            key_facts=[
                "上下文窗口扩展至 400K tokens，支持长文档与跨模态联合推理……",
                "API 输入调用价格下调 50%，现已面向全球开发者开放工具调用接口...",
            ],
        )
        plan = build_editorial_plan(official_card)
        narration = plan.narration()
        for field_text in [plan.title, plan.lead, *plan.facts, narration]:
            self.assertNotIn("…", field_text)
            self.assertNotIn("...", field_text)
        self.assertTrue(plan.facts)
        self.assertIn("400K", " ".join(plan.facts))

        board = build_storyboard(official_card, plan=plan)
        self.assertEqual(board.template, "change_impact")
        self.assertFalse(board.needs_evidence_frame)
        self.assertTrue(board.to_cards())

        # Personnel signal must include disclaimer in narration across both headline and brief tiers
        personnel_card = _make_publishable_card(
            "anthropic:personnel",
            entity="Anthropic / Claude",
            event_title="Anthropic 核心安全主管宣布卸任",
            official_count=0,
            community_count=1,
            risk="yellow",
            editorial_tier="brief",
            key_facts=["Anthropic 核心安全主管宣布卸任现职，团队职责由工程副总裁接任"],
            evidence_links=[
                {
                    "source": "X Staff",
                    "tier": "B",
                    "reliability": "official_personnel",
                    "title": "Post",
                    "url": "https://x.com/staff/status/1940685355315200001",
                    "excerpt": "Anthropic 核心安全主管宣布卸任现职，团队职责由工程副总裁接任",
                    "excerpt_provenance": "social_post",
                    "screenshot_required": "true",
                    "screenshot_status": "captured",
                    "screenshot_path": "C:/tmp/shot.png",
                }
            ],
        )
        p_plan = build_editorial_plan(personnel_card)
        self.assertIn(SIGNAL_DISCLAIMERS["official_personnel"], p_plan.narration())
        p_board = build_storyboard(personnel_card, plan=p_plan)
        self.assertEqual(p_board.template, "brief_strip")
        self.assertTrue(p_board.needs_evidence_frame)

        rumour_card = _make_publishable_card(
            "community:rumour-plan",
            entity="Mistral",
            event_title="社区曝出新模型本地量化推理测试线索",
            official_count=0,
            media_count=0,
            community_count=1,
            risk="yellow",
            editorial_tier="headline",
            key_facts=["社区开发者实测新快照在单卡显存占用降低 30%"],
            evidence_links=[
                {
                    "source": "Reddit",
                    "tier": "C",
                    "reliability": "community",
                    "title": "Community quantization test",
                    "url": "https://reddit.com/r/LocalLLaMA/comments/q1",
                    "excerpt": "社区开发者实测新快照在单卡显存占用降低 30%",
                    "excerpt_provenance": "article_excerpts",
                }
            ],
        )
        r_plan = build_editorial_plan(rumour_card)
        self.assertIn(SIGNAL_DISCLAIMERS["community_rumour"], r_plan.narration())

        # Pure EditionBrief check (does not mutate input cards; prefers confirmed headline over personnel card)
        cards_input = [personnel_card, official_card]
        snapshot_before = copy.deepcopy(cards_input)
        brief = build_edition_brief(cards_input, run_date="2026-07-03")
        self.assertIsInstance(brief, EditionBrief)
        self.assertEqual(cards_input, snapshot_before)
        self.assertEqual(brief.run_date, "2026-07-03")
        self.assertEqual(brief.story_count, 2)
        self.assertEqual(brief.lead_story_id, plan.story_id)
        self.assertIn("一线消息：", brief.hook)

        empty_brief = build_edition_brief([], run_date="2026-07-03")
        self.assertEqual(empty_brief.story_count, 0)
        self.assertEqual(empty_brief.lead_story_id, "")


class QualityAndSelectorTests(unittest.TestCase):
    def test_story_category_and_public_source_status_contracts(self) -> None:
        model_card = _make_publishable_card("m1", event_title="OpenAI 正式发布 GPT-5 大模型")
        tool_card = _make_publishable_card("t1", event_title="OpenAI 更新 Codex 客户端插件功能")
        oss_card = _make_publishable_card(
            "o1",
            entity="Qwen / 阿里",
            event_title="Hugging Face 模型仓库更新：Qwen/Qwen3-8B",
            evidence_links=[{"source": "Hugging Face", "tier": "A", "reliability": "official", "url": "https://huggingface.co/Qwen/Qwen3-8B"}],
        )
        research_card = _make_publishable_card(
            "r1",
            entity="论文 / arXiv",
            event_title="arXiv 论文提出稀疏注意力架构",
            evidence_links=[{"source": "arXiv", "tier": "A", "reliability": "official", "url": "https://arxiv.org/abs/2607.00001"}],
        )
        policy_card = _make_publishable_card("p1", event_title="Anthropic 安全负责人宣布离职")

        cats = [
            story_category(model_card),
            story_category(tool_card),
            story_category(oss_card),
            story_category(research_card),
            story_category(policy_card),
        ]
        self.assertEqual(cats, ["模型发布", "产品与工具", "开源与生态", "研究与评测", "产业与政策"])
        self.assertTrue(all(c in STORY_CATEGORIES for c in cats))

        multi_media = _make_publishable_card(
            "mm",
            official_count=0,
            media_count=2,
            evidence_links=[
                {"source": "36Kr", "tier": "B", "reliability": "media", "url": "https://36kr.com/p/1"},
                {"source": "InfoQ", "tier": "B", "reliability": "media", "url": "https://infoq.cn/article/2"},
            ],
        )
        single_media = _make_publishable_card(
            "sm",
            official_count=0,
            media_count=1,
            evidence_links=[{"source": "36Kr", "tier": "B", "reliability": "media", "url": "https://36kr.com/p/1"}],
        )
        personnel = _make_publishable_card(
            "ps",
            official_count=0,
            community_count=1,
            evidence_links=[{"source": "X Staff", "tier": "B", "reliability": "official_personnel", "url": "https://x.com/sama/status/1940685355315200000"}],
        )
        community = _make_publishable_card(
            "cm",
            official_count=0,
            media_count=0,
            community_count=1,
            evidence_links=[{"source": "Reddit", "tier": "C", "reliability": "community", "url": "https://reddit.com/r/LocalLLaMA/1"}],
        )
        statuses = [
            public_source_status(model_card),
            public_source_status(multi_media),
            public_source_status(single_media),
            public_source_status(personnel),
            public_source_status(community),
        ]
        self.assertEqual(statuses, ["official", "multi_media", "single_media", "official_personnel", "community"])
        self.assertTrue(all(s in SOURCE_STATUSES for s in statuses))

    def test_merge_related_cards_and_select_card_portfolio(self) -> None:
        shared_url = "https://openai.com/index/gpt-5-release"
        card_official = _make_publishable_card(
            "openai:official-gpt5",
            event_title="OpenAI 正式发布 GPT-5 多模态模型",
            risk="green",
            evidence_links=[
                {
                    "source": "OpenAI News",
                    "tier": "A",
                    "reliability": "official",
                    "title": "GPT-5 Release",
                    "url": shared_url,
                    "excerpt": "OpenAI 正式发布 GPT-5 多模态推理模型，上下文窗口扩展至 400K tokens",
                    "excerpt_provenance": "article_excerpts",
                }
            ],
        )
        card_reconciled_media = _make_publishable_card(
            "media:gpt5-report",
            event_title="多家媒体跟进报道 GPT-5 发布细节",
            risk="yellow",
            official_count=1,
            media_count=1,
            key_facts=["GPT-5 API 输入调用价格降低 50%，支持实时工具调用"],
            evidence_links=[
                {
                    "source": "OpenAI Official",
                    "tier": "A",
                    "reliability": "official",
                    "title": "GPT-5 Release",
                    "url": shared_url,
                    "reconciled_official": "true",
                    "excerpt": "GPT-5 API 输入调用价格降低 50%，支持实时工具调用",
                    "excerpt_provenance": "official_reconciliation",
                },
                {
                    "source": "量子位",
                    "tier": "B",
                    "reliability": "media",
                    "title": "GPT-5 报道",
                    "url": "https://qbitai.com/2026/07/gpt-5.html",
                    "excerpt": "GPT-5 API 输入调用价格降低 50%，支持实时工具调用",
                    "excerpt_provenance": "article_excerpts",
                },
            ],
        )
        merged = merge_related_cards([card_reconciled_media, card_official])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].risk, "green")
        self.assertEqual(set(merged[0].source_cluster_keys), {"openai:official-gpt5", "media:gpt5-report"})

        qwen_card = _make_publishable_card(
            "qwen:qwen3-launch",
            entity="Qwen / 阿里",
            event_title="阿里千问正式开源 Qwen3 旗舰推理模型",
            key_facts=[
                "阿里千问正式开源 Qwen3 旗舰推理模型，采用 Apache 2.0 许可协议开放权重",
                "Qwen3 支持 128K 上下文窗口与混合思考推理模式",
            ],
            evidence_links=[
                {
                    "source": "Qwen Blog",
                    "tier": "A",
                    "reliability": "official",
                    "title": "Qwen3 Open Source",
                    "url": "https://qwenlm.github.io/blog/qwen3",
                    "excerpt": "阿里千问正式开源 Qwen3 旗舰推理模型，采用 Apache 2.0 许可协议开放权重；Qwen3 支持 128K 上下文窗口与混合思考推理模式",
                    "excerpt_provenance": "article_excerpts",
                }
            ],
        )
        excluded_card = _make_publishable_card(
            "deepseek:excluded-story",
            entity="DeepSeek",
            event_title="DeepSeek 更新 API 缓存计费规则",
            key_facts=["DeepSeek 宣布上下文硬盘缓存命中价格下调 60%，即日起向开发者生效"],
            evidence_links=[
                {
                    "source": "DeepSeek Docs",
                    "tier": "A",
                    "reliability": "official",
                    "title": "API Pricing",
                    "url": "https://api-docs.deepseek.com/news/pricing",
                    "excerpt": "DeepSeek 宣布上下文硬盘缓存命中价格下调 60%，即日起向开发者生效",
                    "excerpt_provenance": "article_excerpts",
                }
            ],
        )

        # Penalize OpenAI story via story_history so fresh Qwen story takes the single headline lead
        portfolio = select_card_portfolio(
            [card_official, card_reconciled_media, qwen_card, excluded_card],
            max_items=3,
            min_items=2,
            strict_auto=True,
            story_history={"openai:official-gpt5": 3},
            excluded_keys={"deepseek:excluded-story"},
        )
        selected = portfolio["selected"]
        rejected = portfolio["rejected"]
        metrics = portfolio["metrics"]

        self.assertEqual(metrics["excluded_count"], 1)
        self.assertEqual(len(selected), 2)
        self.assertEqual(selected[0].cluster_key, "qwen:qwen3-launch")
        self.assertEqual(selected[0].editorial_tier, "headline")
        self.assertEqual(selected[1].editorial_tier, "brief")
        self.assertEqual(metrics["tier_counts"], {"headline": 1, "brief": 1})
        self.assertTrue(metrics["history_penalties"])
        self.assertIn("deepseek:excluded-story", {c.cluster_key for c in rejected})


class PackageTests(unittest.TestCase):
    def test_write_package_and_refresh_bilibili_outputs_chapter_sync(self) -> None:
        lead_card = _make_publishable_card(
            "openai:gpt-5",
            entity="OpenAI",
            event_title="OpenAI 正式发布 GPT-5 多模态推理模型",
            editorial_tier="headline",
        )
        brief_card = _make_publishable_card(
            "qwen:qwen3",
            entity="Qwen / 阿里",
            event_title="阿里千问开源 Qwen3 推理模型",
            editorial_tier="brief",
            key_facts=[
                "阿里千问正式开源 Qwen3 推理模型，支持 128K 上下文窗口",
                "同步上线 ModelScope 与 Hugging Face 权重下载",
            ],
            evidence_links=[
                {
                    "source": "Qwen Blog",
                    "tier": "A",
                    "reliability": "official",
                    "title": "Qwen3",
                    "url": "https://qwenlm.github.io/blog/qwen3",
                    "excerpt": "阿里千问正式开源 Qwen3 推理模型，支持 128K 上下文窗口；同步上线 ModelScope 与 Hugging Face 权重下载",
                    "excerpt_provenance": "article_excerpts",
                }
            ],
        )
        unselected_card = _make_publishable_card(
            "misc:unselected",
            entity="Mistral",
            event_title="Mistral 发布 SDK 小版本维护说明",
        )
        unselected_card.selected = False
        health = [
            SourceHealth(
                source_id="openai_news",
                source_name="OpenAI News",
                tier="A",
                enabled=True,
                status="ok",
                status_code=200,
                item_count=4,
                latest_item_at=datetime(2026, 7, 3, 9, 0, tzinfo=UTC),
                latency_ms=110,
            )
        ]

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "2026-07-03"
            paths = write_package(
                out_dir,
                run_date="2026-07-03",
                run_id="run-20260703",
                health=health,
                all_cards=[lead_card, brief_card, unselected_card],
                selected_cards=[lead_card, brief_card],
                provenance={"config_sha256": "deadbeef"},
                source_coverage={"x": {"required": False, "status": "not_required"}},
            )
            for key in ("manifest", "sources", "fact_check", "bilibili_json", "bilibili_md", "pinned_comment"):
                self.assertTrue(paths[key].is_file(), f"missing output {key}")
            self.assertEqual(list(out_dir.glob("*.tmp")), [])

            manifest = read_json(paths["manifest"])
            self.assertEqual(manifest["run_id"], "run-20260703")
            self.assertTrue(manifest["publish_allowed"])
            self.assertEqual(manifest["counts"]["selected_cards"], 2)
            self.assertIn("OpenAI News", paths["sources"].read_text(encoding="utf-8"))
            self.assertIn("入选本期", paths["fact_check"].read_text(encoding="utf-8"))
            self.assertIn("未入选", paths["fact_check"].read_text(encoding="utf-8"))

            # Write rendered script.json with exact audio segment timings and sync Bilibili outputs
            story_ids = [row["story_id"] for row in manifest["selected"]]
            write_json_atomic(
                out_dir / "script.json",
                {
                    "segments": [
                        {"kind": "intro", "start_seconds": 0, "duration_seconds": 14},
                        {"kind": "news", "story_id": story_ids[0], "cluster_key": "openai:gpt-5", "start_seconds": 14, "duration_seconds": 51},
                        {"kind": "news", "story_id": story_ids[1], "cluster_key": "qwen:qwen3", "start_seconds": 65, "duration_seconds": 28},
                    ]
                },
            )
            refreshed = refresh_bilibili_outputs(out_dir)
            bili_json = read_json(refreshed["bilibili_json"])
            self.assertEqual([ch["timestamp"] for ch in bili_json["chapters"]], ["00:14", "01:05"])
            self.assertEqual(bili_json["duration_seconds"], 93)

            bili_md = refreshed["bilibili_md"].read_text(encoding="utf-8")
            pinned_md = refreshed["pinned_comment"].read_text(encoding="utf-8")
            self.assertIn("00:14 ", bili_md)
            self.assertIn("01:05 ", bili_md)
            self.assertIn("00:14 ", pinned_md)
            self.assertIn("01:05 ", pinned_md)


if __name__ == "__main__":
    unittest.main()

import unittest
from datetime import datetime, timezone

from briefing.cluster import cluster_items
from briefing.editorial_plan import build_editorial_plan
from briefing.evidence_screenshots import evidence_requires_screenshot
from briefing.models import FeedItem
from briefing.story_model import build_story_spec
from briefing.verify import verify_clusters
from briefing.writer import select_cards


def item(source_id, tier, reliability, title, link, summary="Qwen releases a new model for local deployment"):
    return FeedItem(
        source_id=source_id,
        source_name=source_id,
        source_tier=tier,
        source_reliability=reliability,
        title=title,
        link=link,
        guid=link,
        summary=summary,
        published_at=datetime(2026, 7, 3, 8, 0, tzinfo=timezone.utc),
    )


class VerifyRuleTests(unittest.TestCase):
    def setUp(self):
        import briefing.verify as verify

        self.verify = verify
        self.original_now_utc = verify.now_utc
        verify.now_utc = lambda: datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.verify.now_utc = self.original_now_utc

    def test_official_source_can_be_green(self):
        cards = verify_clusters(cluster_items([item("qwen", "A", "official", "Qwen releases model", "https://qwen.example/a")]), lookback_hours=36)
        self.assertEqual(cards[0].risk, "green")
        self.assertTrue(cards[0].selected)

    def test_concrete_ai_public_service_plan_is_kept_as_attributed_brief(self):
        """有主体、周期和任务的 AI 行动方案不应被当作泛会议新闻丢弃。"""
        plan = item(
            "36kr",
            "B",
            "media",
            "“人工智能+防震减灾”行动方案发布",
            "https://36kr.com/newsflashes/plan",
            summary=(
                "中国地震局发布《人工智能+防震减灾行动方案(2026—2028年)》。"
                "未来3年将围绕智能监测处理、智能预警研发与示范等九方面任务展开。"
            ),
        )
        plan.raw["evidence_excerpts"] = [{"text": plan.summary}]

        cards = verify_clusters(cluster_items([plan]), lookback_hours=36)

        self.assertEqual(cards[0].risk, "yellow")
        self.assertTrue(cards[0].selected)
        picked = select_cards(cards, max_items=0, min_score=68, strict_auto=True)
        self.assertEqual(picked, [cards[0]])
        self.assertEqual(picked[0].editorial_tier, "brief")

    def test_policy_feed_summary_still_requires_article_evidence_for_numeric_claims(self):
        """政策获得富化优先级，不代表 RSS 摘要可以直接证明数字。"""
        plan = item(
            "36kr",
            "B",
            "media",
            "“人工智能+防震减灾”行动方案发布",
            "https://36kr.com/newsflashes/feed-only-plan",
            summary=(
                "中国地震局发布《人工智能+防震减灾行动方案(2026—2028年)》。"
                "未来3年将围绕智能监测处理、智能预警研发与示范等九方面任务展开。"
            ),
        )

        card = verify_clusters(cluster_items([plan]), lookback_hours=36)[0]
        claims = build_story_spec(card).claims

        self.assertEqual(card.risk, "yellow")
        self.assertTrue(card.selected)
        self.assertEqual(card.evidence_links[0]["excerpt_provenance"], "feed_summary")
        self.assertFalse(any(claim.verifiable for claim in claims))
        self.assertEqual(select_cards([card], max_items=0, min_score=68, strict_auto=True), [])

    def test_company_plan_with_partial_business_is_not_public_policy(self):
        """“部分业务”中的单字“部”不能伪造政府发布主体。"""
        company_plan = item(
            "36kr",
            "B",
            "media",
            "某公司发布人工智能行动方案",
            "https://36kr.com/newsflashes/company-plan",
            summary=(
                "某公司发布人工智能行动方案，未来3年将在部分业务推进智能监测、"
                "模型训练和智能预警等3方面任务，并与中国地震局合作开展示范应用。"
            ),
        )

        cards = verify_clusters(cluster_items([company_plan]), lookback_hours=36)

        self.assertEqual(cards[0].risk, "red")
        self.assertFalse(cards[0].selected)
        self.assertEqual(select_cards(cards, max_items=0, min_score=68, strict_auto=True), [])

    def test_company_china_headquarters_is_not_public_policy_owner(self):
        """“中国总部”属于企业组织，不得命中政府发布主体。"""
        company_plan = item(
            "36kr",
            "B",
            "media",
            "某公司中国总部发布人工智能行动方案",
            "https://36kr.com/newsflashes/company-china-plan",
            summary="某公司中国总部发布人工智能行动方案，未来3年推进智能预警等3方面任务。",
        )

        cards = verify_clusters(cluster_items([company_plan]), lookback_hours=36)

        self.assertEqual(cards[0].risk, "red")
        self.assertFalse(cards[0].selected)
        self.assertEqual(select_cards(cards, max_items=0, min_score=68, strict_auto=True), [])

    def test_media_summary_and_stored_excerpt_keep_separate_provenance(self):
        """RSS 主句可补事件语境，但数字只能由正文摘录验证。"""
        riemann = item(
            "qbitai",
            "B",
            "media",
            "看了20万小时人类干活实录,机器人悟了",
            "https://www.qbitai.com/riemann-1",
            summary=(
                "黎曼动力正式发布Riemann-1.0;"
                "Riemann-1.0平均成功率85.00%,过程完成度94.43%。"
            ),
        )
        riemann.raw["evidence_excerpts"] = [
            {"text": "LIBERO评测中,Riemann-1.0成功率99.0%。"}
        ]

        card = verify_clusters(cluster_items([riemann]), lookback_hours=36)[0]
        excerpt = card.evidence_links[0]["excerpt"]
        source_summary = card.evidence_links[0]["source_summary"]
        spec = build_story_spec(card)
        plan = build_editorial_plan(card)

        self.assertIn("LIBERO评测", excerpt)
        self.assertNotIn("85.00%", excerpt)
        self.assertIn("黎曼动力正式发布Riemann-1.0", source_summary)
        self.assertEqual(card.evidence_links[0]["excerpt_provenance"], "article_excerpts")
        self.assertEqual(card.evidence_links[0]["source_summary_provenance"], "feed_summary")
        self.assertTrue(any("正式发布Riemann-1.0" in claim.text and claim.verifiable for claim in spec.claims))
        self.assertTrue(any("99.0%" in claim.text and claim.verifiable for claim in spec.claims))
        self.assertFalse(any("85.00%" in claim.text and claim.verifiable for claim in spec.claims))
        self.assertIn("Riemann-1.0", plan.title)
        self.assertNotIn("看了20万小时", plan.title)

    def test_media_feed_only_metric_is_not_bound_to_article_url(self):
        """媒体 RSS 独有数字在正文缺失时必须保持不可验证。"""
        riemann = item(
            "qbitai",
            "B",
            "media",
            "黎曼动力发布 Riemann-1.0",
            "https://www.qbitai.com/riemann-1",
            summary="黎曼动力发布 Riemann-1.0，平均成功率达到77.7%。",
        )
        riemann.raw["evidence_excerpts"] = [
            {"text": "黎曼动力介绍 Riemann-1.0 的训练数据与产品定位，未给出平均成功率。"}
        ]

        card = verify_clusters(cluster_items([riemann]), lookback_hours=36)[0]
        claims = build_story_spec(card).claims

        self.assertFalse(any("77.7%" in claim.text and claim.verifiable for claim in claims))

    def test_two_pages_from_one_media_publisher_are_not_cross_corroboration(self):
        title = "字节探索自动驾驶，Seed世界模型团队负责｜36氪独家"
        summary = "36氪从多位产业人士处获悉，字节正探索自动驾驶；字节回应并没有做智能驾驶业务的计划。"
        cards = verify_clusters(
            cluster_items(
                [
                    item("36kr_article", "B", "media", title, "https://36kr.com/p/100", summary),
                    item("36kr_flash", "B", "media", title, "https://36kr.com/newsflashes/101", summary),
                ]
            ),
            lookback_hours=36,
        )

        self.assertEqual(cards[0].media_count, 1)
        self.assertEqual(build_story_spec(cards[0]).source_status, "single_media")

    def test_media_relay_of_stale_official_event_is_rejected_as_old_news(self):
        lead = item(
            "infoq_cn",
            "B",
            "media",
            "CircleCI推出Chunk Sidecars，将CI校验引入AI编码工作流",
            "https://www.infoq.cn/article/circleci",
            summary="CircleCI 发布 Chunk Sidecars，让 AI 智能体在提交前运行测试和校验。",
        )
        lead.raw["official_reconciliation"] = {
            "status": "matched",
            "url": "https://circleci.com/blog/chunk-sidecars",
            "final_url": "https://circleci.com/blog/chunk-sidecars",
            "title": "Introducing Chunk Sidecars",
            "facts": ["Chunk Sidecars bring CI validation into AI coding workflows."],
            "excerpt": "Chunk Sidecars bring CI validation into AI coding workflows.",
            "published_at": "2026-06-01T08:00:00+00:00",
            "freshness": "stale",
            "domain": "circleci.com",
            "reliability": "official",
        }

        card = verify_clusters(cluster_items([lead]), lookback_hours=24)[0]

        self.assertEqual(card.risk, "red")
        self.assertFalse(card.selected)
        self.assertIn("旧事件", card.reason)

    def test_undated_official_match_keeps_media_story_as_conservative_brief(self):
        lead = item(
            "infoq_cn",
            "B",
            "media",
            "CircleCI推出Chunk Sidecars，将CI校验引入AI编码工作流",
            "https://www.infoq.cn/article/circleci",
            summary="CircleCI 发布 Chunk Sidecars，让 AI 智能体在提交前运行测试和校验。",
        )
        lead.raw["official_reconciliation"] = {
            "status": "matched_undated",
            "url": "https://circleci.com/blog/chunk-sidecars",
            "final_url": "https://circleci.com/blog/chunk-sidecars",
            "title": "Introducing Chunk Sidecars",
            "facts": ["Chunk Sidecars bring CI validation into AI coding workflows."],
            "excerpt": "Chunk Sidecars bring CI validation into AI coding workflows.",
            "published_at": "",
            "freshness": "undated",
            "domain": "circleci.com",
            "reliability": "official",
            "discovered_from": lead.link,
        }

        card = verify_clusters(cluster_items([lead]), lookback_hours=24)[0]

        self.assertEqual(card.risk, "yellow")
        self.assertTrue(card.selected)
        self.assertEqual(card.event_title, "Introducing Chunk Sidecars")
        self.assertEqual(card.evidence_links[0]["url"], "https://circleci.com/blog/chunk-sidecars")
        self.assertEqual(card.evidence_links[0]["reconciled_official"], "true")

    def test_no_date_official_web_page_is_not_selected(self):
        source_item = FeedItem(
            source_id="minimax_official_web",
            source_name="MiniMax Official Site",
            source_tier="B",
            source_reliability="official",
            title="MiniMax Hailuo 2.3 / 2.3 Fast",
            link="https://www.minimaxi.com/news/minimax-hailuo-23",
            guid="https://www.minimaxi.com/news/minimax-hailuo-23",
            summary="MiniMax AI video model official page with model and API details.",
            published_at=None,
            fetched_at=datetime(2026, 7, 3, 11, 30, tzinfo=timezone.utc),
            raw={"kind": "official_web_discovery"},
        )

        cards = verify_clusters(cluster_items([source_item]), lookback_hours=24)

        self.assertEqual(cards[0].risk, "red")
        self.assertFalse(cards[0].selected)
        self.assertIn("缺少可靠发布时间", cards[0].reason)

    def test_article_metadata_date_does_not_freshen_undated_official_web_discovery(self):
        source_item = FeedItem(
            source_id="minimax_official_web",
            source_name="MiniMax Official Site",
            source_tier="A",
            source_reliability="official",
            title="MiniMax Hailuo 2.3 / 2.3 Fast",
            link="https://www.minimaxi.com/news/minimax-hailuo-23",
            guid="https://www.minimaxi.com/news/minimax-hailuo-23",
            summary="MiniMax AI video model official page with model and API details.",
            published_at=None,
            fetched_at=datetime(2026, 7, 3, 11, 30, tzinfo=timezone.utc),
            raw={
                "kind": "official_web_discovery",
                "article_published_at": "2026-07-03T10:00:00+00:00",
                "article_published_at_provenance": "article_metadata",
            },
        )

        cards = verify_clusters(cluster_items([source_item]), lookback_hours=24)

        self.assertEqual(cards[0].risk, "red")
        self.assertFalse(cards[0].selected)
        self.assertIn("缺少可靠发布时间", cards[0].reason)

    def test_old_official_source_is_not_selected_by_fresh_fetch(self):
        source_item = item("openai", "A", "official", "OpenAI releases old model page", "https://openai.example/old")
        source_item.published_at = datetime(2026, 6, 1, 8, 0, tzinfo=timezone.utc)
        source_item.fetched_at = datetime(2026, 7, 3, 11, 30, tzinfo=timezone.utc)

        cards = verify_clusters(cluster_items([source_item]), lookback_hours=24)

        self.assertEqual(cards[0].risk, "red")
        self.assertFalse(cards[0].selected)
        self.assertIn("不在本次 24 小时窗口", cards[0].reason)

    def test_arxiv_single_paper_is_yellow_not_green(self):
        cards = verify_clusters(
            cluster_items([item("arxiv_cs_cl", "A", "official", "A New LLM Benchmark", "https://arxiv.org/abs/2607.00001")]),
            lookback_hours=36,
        )
        self.assertEqual(cards[0].risk, "yellow")
        self.assertTrue(cards[0].selected)

    def test_single_community_source_is_red(self):
        cards = verify_clusters(cluster_items([item("reddit", "C", "community", "Qwen releases model", "https://reddit.example/a")]), lookback_hours=36)
        self.assertEqual(cards[0].risk, "red")
        self.assertFalse(cards[0].selected)

    def test_community_mainstream_benchmark_can_be_yellow_with_screenshot(self):
        cards = verify_clusters(
            cluster_items(
                [
                    item(
                        "reddit",
                        "C",
                        "community",
                        "Qwen 3.6 27B - VLLM Performance Benchmark Results",
                        "https://reddit.example/qwen-benchmark",
                        summary="Community benchmark results compare BF16, FP8 and NVFP4 throughput and latency for Qwen 3.6.",
                    )
                ]
            ),
            lookback_hours=36,
        )

        self.assertEqual(cards[0].risk, "yellow")
        self.assertTrue(cards[0].selected)
        self.assertTrue(evidence_requires_screenshot(cards[0]))
        self.assertIn("社区", cards[0].reason)

    def test_x_official_personnel_signal_can_be_yellow_with_screenshot(self):
        source_item = FeedItem(
            source_id="x_openai_staff_tibo",
            source_name="X / Tibo",
            source_tier="C",
            source_reliability="official_personnel",
            title="Tibo says Ultra will be in Codex",
            link="https://x.com/thsottiaux/status/1940000000000000000",
            guid="https://x.com/thsottiaux/status/1940000000000000000",
            summary="OpenAI Codex staff quote-posted: Ultra will be in Codex.",
            published_at=datetime(2026, 7, 3, 8, 0, tzinfo=timezone.utc),
        )

        cards = verify_clusters(cluster_items([source_item]), lookback_hours=36)

        self.assertEqual(cards[0].risk, "yellow")
        self.assertTrue(cards[0].selected)
        self.assertEqual(cards[0].official_count, 0)
        self.assertEqual(cards[0].community_count, 1)
        self.assertTrue(evidence_requires_screenshot(cards[0]))
        self.assertIn("X 官方人员动态", cards[0].reason)

    def test_community_non_mainstream_release_stays_red(self):
        cards = verify_clusters(
            cluster_items(
                [
                    item(
                        "reddit",
                        "C",
                        "community",
                        "[RELEASE] Supra-Router-51M - a tiny prompt routing model/orchestrator",
                        "https://reddit.example/supra-router",
                        summary="A small prompt routing model release from a community author.",
                    )
                ]
            ),
            lookback_hours=36,
        )

        self.assertEqual(cards[0].risk, "red")
        self.assertFalse(cards[0].selected)

    def test_official_item_not_lost_behind_recent_noise(self):
        noisy = [item("hn", "C", "community", f"General link {i}", f"https://news.example/{i}", summary="not enough AI signal") for i in range(60)]
        official = item("modelscope_github_releases", "A", "official", "ModelScope 发布 v1.38.0", "https://github.com/modelscope/modelscope/releases/tag/v1.38.0")
        cards = verify_clusters(cluster_items(noisy + [official]), lookback_hours=36, max_cards=5)
        self.assertTrue(any(c.event_title.startswith("ModelScope") and c.risk == "green" for c in cards))

    def test_official_release_feed_with_public_404_can_still_be_selected(self):
        official = item(
            "modelscope_github_releases",
            "A",
            "official",
            "ModelScope 发布 v1.38.1",
            "https://github.com/modelscope/modelscope/releases/tag/v1.38.1",
            summary="GitHub Release 公开页面返回 404；如果来源 feed 已记录，只按官方发布线索保留，细节待确认。",
        )
        official.raw["github_release_missing"] = True
        cards = verify_clusters(cluster_items([official]), lookback_hours=36)

        self.assertEqual(cards[0].risk, "green")
        self.assertTrue(cards[0].selected)
        self.assertIn("公开发布页暂不可访问", "\n".join(cards[0].uncertainty))

    def test_hf_model_variants_share_cluster(self):
        flash = item(
            "deepseek_hf_models",
            "A",
            "official",
            "Hugging Face 模型仓库更新：deepseek-ai/DeepSeek-V4-Flash-DSpark",
            "https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-DSpark",
        )
        flash.raw["model_id"] = "deepseek-ai/DeepSeek-V4-Flash-DSpark"
        pro = item(
            "deepseek_hf_models",
            "A",
            "official",
            "Hugging Face 模型仓库更新：deepseek-ai/DeepSeek-V4-Pro-DSpark",
            "https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro-DSpark",
        )
        pro.raw["model_id"] = "deepseek-ai/DeepSeek-V4-Pro-DSpark"

        clusters = cluster_items([flash, pro])

        self.assertEqual(len(clusters), 1)
        self.assertEqual(len(clusters[0].items), 2)

    def test_general_media_without_ai_signal_is_red(self):
        cards = verify_clusters(
            cluster_items([item("36kr", "B", "media", "某公司终止控制权变更事项", "https://36kr.example/a", summary="公司公告称相关交易已终止")]),
            lookback_hours=36,
        )
        self.assertEqual(cards[0].risk, "red")
        self.assertFalse(cards[0].selected)

    def test_general_paper_dispute_is_not_ai_research(self):
        cards = verify_clusters(
            cluster_items(
                [
                    item(
                        "36kr",
                        "B",
                        "media",
                        "蒋方舟再回应被清华教授指控论文造假",
                        "https://36kr.example/paper-dispute",
                        summary="作者回应文学论文争议，并称将用法律手段维护权益。",
                    )
                ]
            ),
            lookback_hours=36,
        )
        self.assertEqual(cards[0].risk, "red")
        self.assertFalse(cards[0].selected)

    def test_single_media_weak_ai_stock_item_is_not_selected(self):
        cards = verify_clusters(
            cluster_items(
                [
                    item(
                        "36kr",
                        "B",
                        "media",
                        "贝斯特:人形机器人业务仍处样品阶段",
                        "https://36kr.example/robot-stock",
                        summary="公司股票交易异常波动，相关样品营收占比很小，对公司业绩不产生重大影响。",
                    )
                ]
            ),
            lookback_hours=36,
        )
        self.assertEqual(cards[0].risk, "red")
        self.assertFalse(cards[0].selected)

    def test_single_media_non_mainstream_ai_startup_is_not_selected(self):
        cards = verify_clusters(
            cluster_items(
                [
                    item(
                        "36kr",
                        "B",
                        "media",
                        "具身智能公司光象科技累计完成数亿元天使轮融资",
                        "https://36kr.example/embodied-ai",
                        summary="本轮资金将重点投入物理原生基座模型研发，并推进具身智能机器人产品商业化交付。",
                    )
                ]
            ),
            lookback_hours=36,
        )
        self.assertEqual(cards[0].risk, "red")
        self.assertFalse(cards[0].selected)

    def test_specialist_media_non_mainstream_ai_startup_is_not_selected(self):
        cards = verify_clusters(
            cluster_items(
                [
                    item(
                        "infoq_cn",
                        "B",
                        "media",
                        "数亿元融资落地!光象科技自研物理原生基座模型,跳出 VLA 与世界模型路线",
                        "https://infoq.example/startup",
                        summary="公司完成数亿元融资，资金将投入物理原生基座模型研发和具身智能商业化交付。",
                    )
                ]
            ),
            lookback_hours=36,
        )
        self.assertEqual(cards[0].risk, "red")
        self.assertFalse(cards[0].selected)

    def test_single_media_mainstream_ai_tool_story_can_be_yellow(self):
        cards = verify_clusters(
            cluster_items(
                [
                    item(
                        "36kr",
                        "B",
                        "media",
                        "阿里内部全面禁用Claude Code",
                        "https://36kr.example/claude-code",
                        summary="媒体称阿里内部已全面禁用 Claude Code，原因指向代码安全和数据合规；相关说法尚未看到官方确认。",
                    )
                ]
            ),
            lookback_hours=36,
        )
        self.assertEqual(cards[0].risk, "yellow")
        self.assertTrue(cards[0].selected)

    def test_google_news_only_story_is_discovery_not_main_video(self):
        cards = verify_clusters(
            cluster_items(
                [
                    item(
                        "google_news_ai_global",
                        "B",
                        "media",
                        "OpenAI launches GPT-6 model",
                        "https://news.google.com/rss/articles/example",
                        summary="OpenAI released a new GPT-6 model according to a search result from Google News.",
                    )
                ]
            ),
            lookback_hours=36,
        )

        self.assertEqual(cards[0].risk, "red")
        self.assertFalse(cards[0].selected)
        self.assertIn("搜索聚合", cards[0].reason)

    def test_specialist_ai_media_strong_story_can_be_yellow(self):
        cards = verify_clusters(
            cluster_items(
                [
                    item(
                        "qbitai",
                        "B",
                        "media",
                        "WAIC 2026模型与智能体:后Scaling时代范式重构,迈入智能体生产力时代",
                        "https://qbitai.example/waic-agent",
                        summary="量子位报道 WAIC 2026 上关于大模型、智能体和后 Scaling 范式的讨论。",
                    )
                ]
            ),
            lookback_hours=36,
        )
        self.assertEqual(cards[0].risk, "yellow")
        self.assertTrue(cards[0].selected)
        self.assertIn("媒体", cards[0].reason)

    def test_expired_evergreen_page_relisted_as_today_is_rejected(self):
        bounty = item(
            "openai_news", "A", "official", "GPT-5.5 Bio Bug Bounty", "https://openai.example/bounty",
            summary="OpenAI Codex GPT-5.5 安全测试阶段将于 2026 年 4 月 28 日开始,7 月 27 日结束;截止日期为 6 月 22 日。",
        )
        bounty.published_at = datetime(2026, 7, 9, 10, 0, tzinfo=timezone.utc)

        card = verify_clusters(cluster_items([bounty]), lookback_hours=36)[0]

        self.assertFalse(card.selected)
        self.assertEqual(card.risk, "red")
        self.assertIn("截止日期", card.reason)
        self.assertTrue(any("2026-06-22" in note for note in card.uncertainty))

    def test_key_facts_prefer_specific_chinese_summary_over_short_english_teaser(self):
        official = item(
            "openai_news", "A", "official", "GPT-5.6: Frontier intelligence", "https://openai.example/gpt-5-6",
            summary="More intelligence from every token.",
        )
        media = item(
            "infoq_cn", "B", "media", "GPT-5.6 全面发布", "https://infoq.example/gpt-5-6",
            summary="GPT-5.6 新增程序化工具调用，可自动过滤中间数据并减少 Token 消耗；内部评测得分达到 53.6 分。",
        )
        cluster = cluster_items([official, media])[0]

        card = verify_clusters([cluster], lookback_hours=36)[0]

        summaries = [fact for fact in card.key_facts if fact.startswith("来源摘要")]
        self.assertTrue(summaries)
        self.assertIn("程序化工具调用", summaries[0])
        self.assertIn("53.6 分", summaries[0])

    def test_future_dated_item_is_not_green(self):
        future = item("qwen", "A", "official", "Qwen releases future model", "https://qwen.example/future")
        future.published_at = datetime(2026, 7, 4, 20, 0, tzinfo=timezone.utc)
        cards = verify_clusters(cluster_items([future]), lookback_hours=36)
        self.assertEqual(cards[0].risk, "red")
        self.assertFalse(cards[0].selected)
        self.assertIn("未来", " ".join(cards[0].uncertainty))


if __name__ == "__main__":
    unittest.main()

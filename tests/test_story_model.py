import unittest
from datetime import datetime, timezone

from briefing.editorial_plan import _best_claims, build_editorial_plan
from briefing.models import EvidenceCard
from briefing.render import _segments
from briefing.story_model import FactClaim, StorySpec, build_story_spec, extract_claims


UTC = timezone.utc


def card(title: str, facts: list[str], *, entity: str = "OpenAI", official: int = 1, media: int = 0) -> EvidenceCard:
    return EvidenceCard(
        cluster_key="story-1",
        event_title=title,
        entity=entity,
        risk="green" if official else "yellow",
        confidence=90 if official else 64,
        selected=True,
        reason="",
        source_count=1,
        official_count=official,
        media_count=media,
        community_count=0,
        first_seen_at=datetime(2026, 7, 10, tzinfo=UTC),
        latest_published_at=datetime(2026, 7, 10, tzinfo=UTC),
        key_facts=facts,
        evidence_links=[{"source": "Official" if official else "InfoQ 中文", "url": "https://example.com/story"}],
        uncertainty=[] if official else ["官方未明确"],
        score=80,
    )


class StoryModelTests(unittest.TestCase):
    def test_submission_deadline_is_kept_as_a_separate_actionable_claim(self):
        claims = [
            FactClaim(
                claim_id="open",
                text="OpenAI 开发者账号宣布,OpenAI Build Week 已开放项目投稿",
                claim_type="availability",
                specificity=52,
                renderable=True,
                verifiable=True,
                evidence_urls=["https://x.com/OpenAIDevs/status/1"],
                evidence_reliability=["official_social"],
                first_party_supported=True,
            ),
            FactClaim(
                claim_id="title",
                text="OpenAI Build Week 开放项目投稿",
                claim_type="availability",
                specificity=46,
                renderable=True,
                verifiable=True,
                evidence_urls=["https://x.com/OpenAIDevs/status/1"],
                evidence_reliability=["official_social"],
                first_party_supported=True,
            ),
            FactClaim(
                claim_id="deadline",
                text="OpenAI 开发者账号表示,OpenAI Build Week 的项目投稿截止日期为 7 月 21 日",
                claim_type="statement",
                specificity=32,
                renderable=True,
                verifiable=True,
                evidence_urls=["https://x.com/OpenAIDevs/status/2"],
                evidence_reliability=["official_social"],
                first_party_supported=True,
            ),
        ]
        spec = StorySpec(
            story_id="build-week",
            entity="OpenAI",
            kind="official_update",
            headline="OpenAI Build Week 开放项目投稿",
            subject="OpenAI",
            action="开放",
            object="Build Week 项目投稿",
            topic_key="openai|build-week",
            family_key="openai|build-week",
            source_status="official_confirmed",
            first_party=True,
            evidence_count=2,
            claims=claims,
            audience=["developers"],
            warnings=[],
        )

        selected = _best_claims(spec, limit=4)

        self.assertEqual([claim.claim_id for claim in selected], ["open", "deadline"])

    def test_official_personnel_series_keeps_specific_usage_facts(self):
        claims = [
            FactClaim(
                claim_id="users",
                text="OpenAI 产品负责人 Tibo 表示,Codex 与 ChatGPT Work 活跃用户达到 700 万",
                claim_type="statement",
                specificity=44,
                renderable=True,
                verifiable=True,
                evidence_urls=["https://x.com/thsottiaux/status/1"],
                evidence_reliability=["official_personnel"],
                first_party_supported=False,
            ),
            FactClaim(
                claim_id="usage",
                text="OpenAI 产品负责人 Tibo 表示,GPT-5.6 Sol 的推理优化预计让各付费订阅约多获得 10% 用量",
                claim_type="metric",
                specificity=44,
                renderable=True,
                verifiable=True,
                evidence_urls=["https://x.com/thsottiaux/status/2"],
                evidence_reliability=["official_personnel"],
                first_party_supported=False,
            ),
            FactClaim(
                claim_id="subscription",
                text="OpenAI 产品负责人 Tibo 表示,GPT-5.6 Sol 将继续包含在 Go、Plus 和 Pro 等付费 ChatGPT 订阅中",
                claim_type="availability",
                specificity=52,
                renderable=True,
                verifiable=True,
                evidence_urls=["https://x.com/thsottiaux/status/3"],
                evidence_reliability=["official_personnel"],
                first_party_supported=False,
            ),
            FactClaim(
                claim_id="generic",
                text="OpenAI 产品负责人说明 Codex 与 ChatGPT Work 用量调整",
                claim_type="statement",
                specificity=38,
                renderable=True,
                verifiable=True,
                evidence_urls=["https://x.com/thsottiaux/status/1"],
                evidence_reliability=["official_personnel"],
                first_party_supported=False,
            ),
        ]
        spec = StorySpec(
            story_id="usage-series",
            entity="OpenAI",
            kind="other",
            headline="Codex 与 ChatGPT Work 向全体账户发放一次储备用量重置",
            subject="OpenAI",
            action="调整",
            object="Codex 与 ChatGPT Work 用量",
            topic_key="openai|usage",
            family_key="openai|chatgpt",
            source_status="community",
            first_party=False,
            evidence_count=3,
            claims=claims,
            audience=["developers", "users"],
            warnings=["X 官方人员动态；用原帖截图呈现，不等同于正式发布页。"],
        )

        selected = _best_claims(spec, limit=4)

        self.assertEqual([claim.claim_id for claim in selected], ["users", "usage", "subscription"])

    def test_official_personnel_usage_title_does_not_invent_a_product_launch(self):
        sample = card(
            "OpenAI \u4ea7\u54c1\u8d1f\u8d23\u4eba\u8bf4\u660e Codex \u4e0e ChatGPT Work \u7528\u91cf\u8c03\u6574",
            ["OpenAI \u4ea7\u54c1\u8d1f\u8d23\u4eba Tibo \u8868\u793a,GPT-5.6 Sol \u7684\u63a8\u7406\u4f18\u5316\u9884\u8ba1\u8ba9\u5404\u4ed8\u8d39\u8ba2\u9605\u7ea6\u591a\u83b7\u5f97 10% \u7528\u91cf"],
            official=0,
            media=0,
        )
        sample.community_count = 1
        sample.evidence_links = [
            {
                "source": "X / OpenAI product lead",
                "tier": "C",
                "reliability": "official_personnel",
                "url": "https://x.com/thsottiaux/status/103",
                "excerpt": sample.key_facts[0],
            }
        ]

        plan = build_editorial_plan(sample)

        self.assertEqual(plan.title, sample.event_title)
        self.assertNotIn("\u4e0a\u7ebf ChatGPT Work", plan.title)

    def test_model_version_subject_is_not_split_before_location_clause(self):
        sample = card(
            "社区线索｜Grok 4.5 在 WANDR 测试中暂列最高",
            ["TestingCatalog 称 Grok 4.5 在 WANDR 的 Perplexity Computer 测试中得分最高，价格区间与 GLM-5.2 Advisor 接近。"],
            entity="xAI / Grok",
            official=0,
        )
        sample.community_count = 1
        sample.evidence_links = [
            {
                "source": "X / TestingCatalog",
                "tier": "C",
                "reliability": "community",
                "url": "https://x.com/testingcatalog/status/123",
                "excerpt": sample.key_facts[0],
            }
        ]

        claims = extract_claims(sample)

        self.assertTrue(any("Grok 4.5 在 WANDR" in claim.text and "得分最高" in claim.text for claim in claims))

    def test_media_roundup_prefers_claims_matching_the_selected_legal_story(self):
        sample = card(
            "苹果甩出 41 页 PDF 怒告 OpenAI 偷师核心机密",
            [
                "苹果在 2026 年推出的新一代 Siri AI 已转向 Gemini。",
                "勒索组织泄露了苹果供应商超过 20 万份文件，总计约 630GB。",
                "当地时间 7 月 10 日，苹果向联邦地区法院提起诉讼，指控 OpenAI 盗用商业秘密。",
                "案件编号为 5:26-cv-07078，被告包括 OpenAI Group PBC 与 io Products。",
            ],
            entity="OpenAI",
            official=0,
            media=1,
        )

        plan = build_editorial_plan(sample)

        self.assertTrue(any("提起诉讼" in fact for fact in plan.facts))
        self.assertTrue(any("5:26-cv-07078" in fact for fact in plan.facts))
        self.assertFalse(any("630GB" in fact or "Siri" in fact for fact in plan.facts))

    def test_background_copilot_and_claude_mentions_cannot_rename_circleci_story(self):
        sample = card(
            "Circle CI推出Chunk Sidecars,将CI校验直接引入AI编码工作流",
            [
                "GitHub 通过 Copilot 与基于 MCP 的工具继续扩展对 AI 辅助开发的支持，而 Anthropic 的 Claude Code 强调迭代检验。",
                "CircleCI 所称的内循环校验，使 AI 智能体在仍保有修复上下文时获得 CI 级反馈。",
                "CircleCI 发布 Chunk Sidecars，目标是把 CI 级校验带入 AI 编码智能体的内部开发循环。",
            ],
            entity="CircleCI",
            official=0,
            media=1,
        )
        sample.evidence_links[0]["excerpt"] = "；".join(sample.key_facts)

        plan = build_editorial_plan(sample)

        self.assertTrue(plan.title.startswith("CircleCI推出Chunk Sidecars"), plan.title)
        self.assertTrue(plan.facts)
        self.assertTrue("CircleCI" in plan.facts[0] or "Chunk Sidecars" in plan.facts[0])
        self.assertNotIn("Anthropic推出Claude Code", plan.narration())

    def test_product_title_keeps_specific_feature_without_repeating_brand(self):
        sample = card(
            "GitHub Copilot 新增仓库概览能力",
            ["来源摘要：GitHub Copilot 新增仓库概览能力，可以生成高层结构概览。"],
            entity="GitHub / Copilot",
        )

        plan = build_editorial_plan(sample)

        self.assertEqual(plan.title, "GitHub Copilot 新增仓库概览能力")
        self.assertNotIn("GitHub新增GitHub", plan.title)
        self.assertEqual(plan.lead, plan.title)
        self.assertIn("生成高层结构概览", plan.narration())

    def test_extracts_renderable_verifiable_claims_without_meta_lines(self):
        sample = card(
            "OpenAI 发布 GPT-5.6 系列模型",
            [
                "OpenAI 相关事件：OpenAI 发布 GPT-5.6 系列模型",
                "首要来源发布时间：2026-07-10 10:00 UTC",
                "来源摘要：GPT-5.6 新增程序化工具调用，可用轻量程序协调多步工具，并支持零数据保留。",
            ],
        )

        claims = extract_claims(sample)

        self.assertTrue(claims)
        self.assertIn("程序化工具调用", claims[0].text)
        self.assertTrue(claims[0].renderable)
        self.assertTrue(claims[0].verifiable)
        self.assertFalse(any("相关事件" in claim.text for claim in claims))
        self.assertFalse(any("发布时间" in claim.text for claim in claims))

    def test_repository_title_alone_is_not_a_public_artifact_claim(self):
        sample = card(
            "Hugging Face 模型仓库更新：acme/Nova-7",
            ["来源摘要：Hugging Face 模型仓库更新：acme/Nova-7"],
            entity="Acme",
        )
        sample.evidence_links = [{"source": "Hugging Face", "url": "https://huggingface.co/acme/Nova-7", "excerpt": sample.key_facts[0]}]

        spec = build_story_spec(sample)

        self.assertEqual(spec.kind, "model_repository")
        self.assertFalse(any(claim.renderable and claim.verifiable and claim.specificity >= 28 for claim in spec.claims))

    def test_repository_license_and_weights_are_public_claims(self):
        sample = card(
            "Hugging Face 模型仓库更新：acme/Nova-7",
            ["来源摘要：Nova-7 已上传 safetensors 权重，采用 Apache-2.0 许可并支持下载。"],
            entity="Acme",
        )
        sample.evidence_links = [{"source": "Hugging Face", "url": "https://huggingface.co/acme/Nova-7", "excerpt": sample.key_facts[0]}]

        spec = build_story_spec(sample)

        self.assertTrue(any(claim.renderable and claim.verifiable and claim.specificity >= 40 for claim in spec.claims))

    def test_unrelated_roundup_fragment_is_not_a_story_claim(self):
        sample = card(
            "OpenAI 发布 GPT-5.6 系列模型",
            [
                "来源摘要：GPT-5.6 新增程序化工具调用并支持零数据保留；今年第 9 号台风进入警戒线,博主用 AI 气象模型发布预测。"
            ],
        )
        sample.evidence_links[0]["excerpt"] = sample.key_facts[0]

        claims = extract_claims(sample)

        self.assertTrue(any("程序化工具调用" in claim.text for claim in claims))
        self.assertFalse(any("台风" in claim.text for claim in claims))

    def test_repeated_versioned_model_in_excerpt_repairs_clickbait_title_anchor(self):
        """标题未写模型名时，正文反复出现的 Riemann-1.0 仍可绑定量化事实。"""
        sample = card(
            "看了20万小时人类干活实录,机器人悟了",
            [
                "来源摘要：Riemann-1.0平均成功率85.00%,过程完成度94.43%;"
                "Riemann-1.0在LIBERO评测成功率为99.0%。"
            ],
            entity="具身智能 / 机器人",
            official=0,
            media=1,
        )
        sample.evidence_links = [
            {
                "source": "量子位",
                "tier": "B",
                "reliability": "media",
                "url": "https://www.qbitai.com/riemann-1",
                "excerpt": sample.key_facts[0],
            }
        ]

        claims = extract_claims(sample)

        self.assertTrue(
            any("Riemann-1.0平均成功率85.00%" in claim.text and claim.verifiable for claim in claims)
        )

    def test_media_hype_sentence_is_not_promoted_to_public_claim(self):
        """“显而易见、第一梯队”属于媒体判断，不进入事实卡。"""
        sample = card(
            "黎曼动力发布Riemann-1.0",
            ["来源摘要：显而易见,Riemann-1.0稳居第一梯队。"],
            entity="具身智能 / 机器人",
            official=0,
            media=1,
        )
        sample.evidence_links = [
            {
                "source": "量子位",
                "tier": "B",
                "reliability": "media",
                "url": "https://www.qbitai.com/riemann-1",
                "excerpt": sample.key_facts[0],
            }
        ]

        claims = extract_claims(sample)

        self.assertFalse(any("第一梯队" in claim.text and claim.verifiable for claim in claims))

    def test_untranslated_teaser_and_title_only_are_not_public_claims(self):
        sample = card("Ask Copilot for a repository overview", ["来源摘要：You can now ask GitHub Copilot for a high-level overview."], entity="GitHub / Copilot")
        sample.evidence_links[0]["excerpt"] = sample.key_facts[0]

        spec = build_story_spec(sample)

        self.assertFalse(any(claim.renderable and claim.verifiable for claim in spec.claims))

    def test_claim_only_links_to_evidence_with_matching_excerpt(self):
        sample = card(
            "Acme 发布 Nova-7",
            ["来源摘要：Nova-7 新增 256K 上下文并开放 API。"],
            entity="Acme",
        )
        sample.evidence_links = [
            {"source": "Acme", "url": "https://example.com/nova", "excerpt": "Nova-7 新增 256K 上下文并开放 API。"},
            {"source": "Other", "url": "https://example.com/unrelated", "excerpt": "另一个产品调整了图片生成功能。"},
        ]

        claim = extract_claims(sample)[0]

        self.assertEqual(claim.evidence_urls, ["https://example.com/nova"])
        self.assertTrue(claim.verifiable)

    def test_roundup_blob_is_split_and_navigation_pollution_is_rejected(self):
        sample = card(
            "OpenAI 发布 GPT-5.6",
            ["GPT-5.6 新增程序化工具调用,可减少 Token 消耗 同时支持零数据保留。早报|微信红包一键直达/小米汽车更新 粤ICP备 版权所有。"],
        )
        sample.evidence_links = [{"source": "媒体", "tier": "B", "reliability": "media", "url": "https://example.com/gpt", "excerpt": sample.key_facts[0]}]

        spec = build_story_spec(sample)

        self.assertTrue(any("程序化工具调用" in claim.text for claim in spec.claims))
        self.assertFalse(any("微信红包" in claim.text or "版权所有" in claim.text for claim in spec.claims if claim.specificity >= 28))
        self.assertTrue(all(len(claim.text) <= 280 for claim in spec.claims))

    def test_media_supported_claim_is_not_presented_as_official(self):
        sample = card("Acme 发布 Nova-7", ["Nova-7 延迟降低 18%。"], entity="Acme", official=1, media=1)
        sample.evidence_links = [
            {"source": "Acme Official", "tier": "A", "reliability": "official", "url": "https://acme.example/nova", "excerpt": "Acme 发布 Nova-7 并开放 API。"},
            {"source": "InfoQ", "tier": "B", "reliability": "media", "url": "https://infoq.example/nova", "excerpt": "媒体测试显示 Nova-7 延迟降低 18%。"},
        ]

        spec = build_story_spec(sample)
        metric = next(claim for claim in spec.claims if "18%" in claim.text)
        plan = build_editorial_plan(sample)

        self.assertFalse(metric.first_party_supported)
        self.assertEqual(metric.evidence_reliability, ["media"])
        self.assertTrue(metric.claim_id)
        self.assertTrue(plan.lead.startswith("公开资料显示"))
        selected_claims = [claim for claim in spec.claims if claim.claim_id in plan.claim_ids]
        self.assertTrue(selected_claims)
        self.assertTrue(any(not claim.first_party_supported for claim in selected_claims))

    def test_unmatched_claim_is_not_marked_verifiable(self):
        sample = card("Acme 发布 Nova-7", ["来源摘要：Nova-7 价格下降 90%。"], entity="Acme")
        sample.evidence_links = [
            {"source": "Acme", "url": "https://example.com/nova", "excerpt": "Nova-7 新增 API 入口，没有公布价格。"}
        ]

        claim = extract_claims(sample)[0]

        self.assertFalse(claim.verifiable)
        self.assertEqual(claim.evidence_urls, [])

    def test_brand_overlap_cannot_support_an_unrelated_availability_claim(self):
        sample = card("OpenAI ChatGPT 产品更新", ["OpenAI 将免费开放 Workspace 新功能。"])
        sample.evidence_links = [
            {
                "source": "OpenAI",
                "tier": "A",
                "reliability": "official",
                "url": "https://example.com/chatgpt-update",
                "excerpt": "OpenAI 今天发布了 ChatGPT 产品更新。",
            }
        ]

        claim = next(item for item in extract_claims(sample) if "免费开放" in item.text)

        self.assertFalse(claim.verifiable)
        self.assertEqual(claim.evidence_urls, [])

    def test_classifies_case_study_from_structure_not_named_event_branch(self):
        sample = card(
            "某团队用多智能体重写大型运行时",
            ["来源摘要：项目迁移涉及 120 万行代码、6800 次提交，启动时间从 520ms 降至 460ms。"],
            entity="Anthropic / Claude",
            official=0,
            media=1,
        )

        spec = build_story_spec(sample)

        self.assertEqual(spec.kind, "case_study")
        self.assertIn("developers", spec.audience)
        self.assertEqual(spec.source_status, "single_media")
        self.assertGreaterEqual(spec.best_claim.specificity, 50)

    def test_editorial_plan_prefers_capability_over_customer_testimonial(self):
        sample = card(
            "Acme 上线 Nova Work",
            [
                "来源摘要：某客户负责人表示团队已经开始使用 Nova Work。",
                "来源摘要：Nova Work 可连接应用和文件，支持跨应用执行长任务，并已向 Pro 用户开放。",
            ],
            entity="Acme",
        )
        sample.evidence_links[0]["excerpt"] = "；".join(sample.key_facts)

        plan = build_editorial_plan(sample)

        self.assertTrue(plan.facts)
        self.assertIn("连接应用和文件", plan.facts[0])
        self.assertNotIn("负责人", plan.facts[0])

    def test_editorial_plan_neutralizes_clickbait_with_verified_personnel_action(self):
        departure = (
            "据 WIRED 消息，OpenAI 安全系统负责人 Johannes Heidecke 已告知员工自己将离职，"
            "并曾负责 GPT-5.6 安全评估。"
        )
        sample = card(
            "GPT-5.6刚发布，OpenAI安全主管就跑路了??",
            [
                departure,
                "GPT-5.6 已上线 ChatGPT、API 和 Codex。",
            ],
            entity="OpenAI",
            official=0,
            media=1,
        )
        sample.evidence_links[0].update(
            {
                "source": "量子位",
                "tier": "B",
                "reliability": "media",
                "excerpt": "；".join(sample.key_facts),
            }
        )

        plan = build_editorial_plan(sample)

        self.assertEqual(plan.title, "OpenAI 安全系统负责人 Johannes Heidecke 将离职")
        self.assertTrue(any("Johannes Heidecke" in fact and "将离职" in fact for fact in plan.facts))
        self.assertNotIn("跑路", plan.narration())
        self.assertNotIn("??", plan.narration())

    def test_short_personnel_action_is_a_verifiable_structured_claim(self):
        departure = "据 WIRED 消息，OpenAI 安全系统负责人 Johannes Heidecke 已告知员工自己将离职。"
        sample = card(
            "GPT-5.6刚发布，OpenAI安全主管就跑路了??",
            [departure],
            entity="OpenAI",
            official=0,
            media=1,
        )
        sample.evidence_links[0].update(
            {
                "source": "WIRED",
                "tier": "B",
                "reliability": "media",
                "url": "https://www.wired.com/story/openai-safety-leader-departure/",
                "excerpt": departure,
            }
        )

        claim = next(item for item in extract_claims(sample) if "Johannes Heidecke" in item.text)
        spec = build_story_spec(sample)
        plan = build_editorial_plan(sample)

        self.assertEqual(claim.claim_type, "organization")
        self.assertGreaterEqual(claim.specificity, 28)
        self.assertTrue(claim.verifiable)
        self.assertEqual(spec.kind, "industry")
        self.assertNotIn("gpt-5.6", spec.topic_key)
        self.assertEqual(spec.family_key, "")
        self.assertEqual(plan.title, "OpenAI 安全系统负责人 Johannes Heidecke 将离职")

    def test_named_departure_outranks_generic_reorganization_in_clickbait_story(self):
        departure = "据 WIRED 消息，OpenAI 安全系统负责人 Johannes Heidecke 已告知员工自己将离职。"
        reorganization = "GPT-5.6 上线了，OpenAI 也开始重组自己的安全团队了。"
        sample = card(
            "GPT-5.6刚发布，OpenAI安全主管就跑路了??",
            [reorganization, departure, "这是两年内第六位离职的安全负责人。"],
            entity="OpenAI",
            official=0,
            media=1,
        )
        sample.evidence_links[0].update(
            {
                "source": "WIRED",
                "tier": "B",
                "reliability": "media",
                "url": "https://www.wired.com/story/openai-safety-leader-departure/",
                "excerpt": "；".join(sample.key_facts),
            }
        )

        plan = build_editorial_plan(sample)

        self.assertEqual(plan.title, "OpenAI 安全系统负责人 Johannes Heidecke 将离职")
        self.assertIn("Johannes Heidecke", plan.facts[0])

    def test_real_infoq_muse_spark_excerpt_is_neutral_and_dense(self):
        excerpt = (
            "近日,Meta 正式发布了新版 Muse Spark,这是一款面向智能体编程(agentic coding)的多模态 AI 模型,"
            "目标是与 OpenAI 和 Anthropic 等公司的同类产品展开竞争 "
            "他在此次帖子中称,Spark 是“一款价格极低但能力强大的智能体和编程模型”,"
            "并指出该模型“最擅长智能体性能、工具调用以及计算机操作” "
            "小扎“消失”三年后发帖,只为它:Meta最强Agent模型进军编程,从免费开源到卖“低价”模型 - InfoQ "
            "据其介绍,Wang 领导的 Meta 超级智能实验室(Meta Superintelligence Labs,MSL)"
            "针对编程相关任务对 Muse Spark 1.1 进行了训练,因为这最终能够提升 AI 智能体的整体能力 "
            "在感知和多模态推理方面,Muse Spark 1.1 能够检查视觉和音频输入,并在工作流程中保留细节 "
            "在博客中,Meta 写道,Muse Spark 1.1 适用于跨多个外部应用和服务进行规划与协调的场景"
        )
        sample = card(
            "小扎“消失”三年后发帖,只为它:Meta最强Agent模型进军编程,从免费开源到卖“低价”模型",
            [],
            entity="本地模型 / 开源",
            official=0,
            media=1,
        )
        sample.evidence_links[0].update(
            {
                "source": "InfoQ 中文",
                "tier": "B",
                "reliability": "media",
                "url": "https://www.infoq.cn/article/muse-spark",
                "excerpt": excerpt,
            }
        )

        spec = build_story_spec(sample)
        plan = build_editorial_plan(sample)
        public_text = "\n".join([plan.title, *plan.facts, plan.narration()])

        self.assertEqual(plan.title, "Meta 正式发布新版 Muse Spark")
        self.assertEqual(spec.entity, "Meta")
        self.assertEqual(spec.kind, "model_release")
        self.assertEqual(spec.family_key, "meta|muse-spark")
        self.assertIn("工具调用", plan.narration())
        self.assertIn("计算机操作", plan.narration())
        for forbidden in ["小扎", "消失", "只为它", "最强Agent", "低价"]:
            self.assertNotIn(forbidden, public_text)
        for fact in plan.facts:
            self.assertEqual(fact.count("("), fact.count(")"), fact)
            self.assertEqual(fact.count("（"), fact.count("）"), fact)

    def test_real_36kr_autonomous_driving_keeps_company_denial(self):
        excerpt = (
            "36氪从多位产业人士处获悉,字节跳动正探索进入自动驾驶领域。"
            "这一项目目前由Seed旗下周畅的世界模型团队负责。"
            "另有消息人士告诉36氪,字节有意布局的自动驾驶场景包括无人物流。"
            "部分接近字节的知情人士表示,项目仍处于早期筹备状态。"
            "针对以上消息,36氪向字节求证,字节回应:‘字节在AI大模型前沿探索领域,包括物理AI领域,"
            "有很多早期研究和探索,但并没有做智能驾驶业务的计划。’"
            "Seed是字节旗下的大模型基础研究团队,其研究范围包括多模态模型、世界模型与具身智能。"
            "周畅负责多模态大模型和世界模型等方向,相关探索仍处于研究阶段。"
            "在布局自动驾驶之前,字节已通过豆包大模型切入汽车座舱领域。"
            "今年6月,赛力斯与火山引擎合作的汽车品牌AIVA正式发布,搭载豆包座舱的AIVA ME7预计年内上市。"
        )
        sample = card(
            "字节探索自动驾驶,Seed世界模型团队负责|36氪独家",
            [],
            entity="字节 / 豆包",
            official=0,
            media=1,
        )
        sample.evidence_links[0].update(
            {
                "source": "36氪",
                "tier": "B",
                "reliability": "media",
                "url": "https://36kr.com/p/autonomous-driving",
                "excerpt": excerpt,
            }
        )

        plan = build_editorial_plan(sample)
        narration = plan.narration()

        self.assertEqual(plan.title, "36氪称字节正探索自动驾驶")
        self.assertIn("Seed旗下周畅的世界模型团队负责", narration)
        self.assertIn("没有开展智能驾驶业务的计划", narration)
        for forbidden in ["AIVA", "ME7", "豆包座舱", "36氪独家"]:
            self.assertNotIn(forbidden, "\n".join([plan.title, *plan.facts, narration]))

    def test_joined_heidecke_excerpt_stops_title_after_departure(self):
        excerpt = (
            "Heidecke离职后,OpenAI也在对安全团队做重组,将 把安全并入研究体系 "
            "2024年,在翁荔离职后,他接任Safety Systems负责人,成为OpenAI安全团队中的核心人物之一 "
            "这已经是不到两年内,OpenAI第二次把独立的安全组织并入某个研究负责人麾下 "
            "据WIRED消息,OpenAI安全系统负责人 Johannes Heidecke 已告知员工自己将离职 "
            "这是两年内第六位离职的安全负责人,也是OpenAI一周之内离职的第三位高管 "
            "此前OpenAI曾调整多个安全组织的汇报关系,相关职能随后分散到不同研究团队 "
            "量子位称此次变化与安全团队重组同期发生,具体安排仍以公司后续公开信息为准 "
            "GPT-5.6上线了,OpenAI也开始重组自己的安全团队了"
        )
        sample = card(
            "GPT-5.6刚发布,OpenAI安全主管就跑路了??",
            [],
            entity="OpenAI",
            official=0,
            media=1,
        )
        sample.evidence_links[0].update(
            {
                "source": "量子位",
                "tier": "B",
                "reliability": "media",
                "url": "https://www.qbitai.com/2026/07/example.html",
                "excerpt": excerpt,
            }
        )

        spec = build_story_spec(sample)
        plan = build_editorial_plan(sample)

        self.assertEqual(plan.title, "OpenAI 安全系统负责人 Johannes Heidecke 将离职")
        self.assertEqual(spec.kind, "industry")
        self.assertEqual(spec.family_key, "")
        self.assertNotIn("这是两年内第六位", plan.title)
        self.assertNotIn("GPT-5.6", plan.title)
        self.assertNotIn("将 把", plan.narration())
        self.assertNotIn("第六位", plan.narration())

    def test_best_claims_keeps_manager_claim_when_it_describes_an_organization_action(self):
        departure = FactClaim(
            claim_id="departure",
            text="OpenAI 安全系统负责人 Johannes Heidecke 将离职",
            claim_type="statement",
            specificity=42,
            renderable=True,
            verifiable=True,
            evidence_urls=["https://www.wired.com/story/openai-safety-leader-departure"],
        )
        capability = FactClaim(
            claim_id="capability",
            text="GPT-5.6 已上线 ChatGPT、API 和 Codex",
            claim_type="availability",
            specificity=58,
            renderable=True,
            verifiable=True,
            evidence_urls=["https://openai.com/index/gpt-5-6"],
        )
        second_capability = FactClaim(
            claim_id="second-capability",
            text="Codex 同步支持 GPT-5.6 的新工具调用模式",
            claim_type="change",
            specificity=46,
            renderable=True,
            verifiable=True,
            evidence_urls=["https://openai.com/index/gpt-5-6"],
        )
        spec = StorySpec(
            story_id="story",
            entity="OpenAI",
            kind="industry",
            headline="OpenAI 安全主管将离职",
            subject="OpenAI",
            action="动态",
            object="安全主管将离职",
            topic_key="openai|industry|departure",
            family_key="openai|departure",
            source_status="media_cross_checked",
            first_party=False,
            evidence_count=2,
            claims=[capability, second_capability, departure],
        )

        selected = _best_claims(spec, limit=4)

        self.assertIn(departure, selected)

    def test_model_launch_claims_downrank_unrelated_subproduct_details(self):
        sample = card(
            "Acme 发布 Nova-7 模型",
            [
                "来源摘要：Nova Work 由 Nova-7 提供支持，可执行跨应用任务。",
                "来源摘要：Nova-7 新增程序化工具调用并支持零数据保留。",
            ],
            entity="Acme",
        )
        sample.evidence_links[0]["excerpt"] = "；".join(sample.key_facts)

        plan = build_editorial_plan(sample)

        self.assertIn("Nova-7 新增程序化工具调用", plan.facts[0])

    def test_editorial_plan_separates_fact_impact_and_caution(self):
        sample = card(
            "某团队用多智能体重写大型运行时",
            ["来源摘要：项目迁移涉及 120 万行代码、6800 次提交，启动时间从 520ms 降至 460ms。"],
            entity="Anthropic / Claude",
            official=0,
            media=1,
        )

        plan = build_editorial_plan(sample)
        narration = plan.narration()

        self.assertEqual(plan.kind, "case_study")
        self.assertTrue(plan.facts)
        self.assertIn("工程方法", plan.impact)
        self.assertIn("一家媒体", plan.caution)
        self.assertNotIn("处理", plan.caution)
        self.assertIn("120 万行", narration)

    def test_editorial_narration_stays_within_short_news_budget(self):
        sample = card("OpenAI 发布 GPT-5.6", ["GPT-5.6 新增程序化工具调用,可自动过滤中间数据并减少 Token 消耗。", "GPT-5.6 支持零数据保留并开放 API。"])
        sample.evidence_links = [{"source": "Official", "tier": "A", "reliability": "official", "url": "https://example.com/gpt", "excerpt": " ".join(sample.key_facts)}]

        plan = build_editorial_plan(sample)

        self.assertLessEqual(len(plan.narration()), 300)

    def test_brief_narration_never_cuts_a_sentence_or_ascii_word(self):
        sample = card(
            "OpenAI 更新 Codex Groups 工作流",
            [
                "Codex Groups 新增多人任务编排与状态同步，并允许开发者在同一工作区追踪长任务进度，切换到 Groups 后仍保留原有上下文。",
                "该功能已经在桌面端开放测试，并提供新的团队入口与权限说明。",
            ],
        )
        sample.editorial_tier = "brief"
        sample.evidence_links = [
            {
                "source": "Official",
                "tier": "A",
                "reliability": "official",
                "url": "https://example.com/codex-groups",
                "excerpt": " ".join(sample.key_facts),
            }
        ]

        narration = build_editorial_plan(sample).narration()

        self.assertLessEqual(len(narration), 160)
        self.assertTrue(narration.endswith("。"), narration)
        self.assertFalse(narration.endswith(("Gro", "扮", "Cha")), narration)

    def test_render_uses_structured_plan_when_claims_are_ready(self):
        sample = card(
            "Acme 发布 Nova-7 模型",
            ["来源摘要：Nova-7 新增 256K 上下文并开放 API，推理延迟降低 18%。"],
            entity="Acme",
        )

        segment = [row for row in _segments([sample]) if row.get("kind") == "news"][0]

        self.assertEqual(segment["generation_path"], "structured_editorial_plan")
        self.assertIn("256K", segment["text"])
        self.assertNotIn("story_spec", segment)
        self.assertNotIn("editorial_plan", segment)

    def test_render_falls_back_when_no_public_claim_is_ready(self):
        sample = card("NVIDIA technical blog update", ["来源摘要：Generic English source sentence without a concrete public fact."], entity="AI")

        segment = [row for row in _segments([sample]) if row.get("kind") == "news"][0]

        self.assertEqual(segment["generation_path"], "legacy_fallback")

    def test_topic_key_is_generic_for_new_model_names(self):
        first = card("Acme 发布 Nova-7 模型", ["Nova-7 新增 256K 上下文并开放 API。"], entity="Acme")
        second = card("Acme 推出 Nova-7 API", ["Nova-7 API 已开放 256K 上下文。"], entity="Acme")

        a = build_story_spec(first)
        b = build_story_spec(second)

        self.assertTrue(a.topic_key.startswith("acme|"))
        self.assertTrue(b.topic_key.startswith("acme|"))
        self.assertNotEqual(a.story_id, "")

    def test_qwen_release_family_normalizes_alias_and_lifecycle_suffix(self):
        official = card(
            "Qwen3.8 预告:Max 预览版已上线",
            [
                "Qwen 官方预告 Qwen3.8 即将发布。",
                "Qwen3.8-Max-Preview 已上线供测试。",
            ],
            entity="Qwen / 阿里",
        )
        official.evidence_links[0].update(
            {"tier": "A", "reliability": "official_social", "excerpt": " ".join(official.key_facts)}
        )
        media = card(
            "阿里最新一代大模型千问3.8将至,正式版预计近期开源",
            [
                "据媒体报道,Qwen3.8-Max 已上线测试入口。",
                "Qwen3.8-Max 正式版预计近期开源。",
            ],
            entity="Qwen / 阿里",
            official=0,
            media=1,
        )
        media.evidence_links[0].update(
            {"tier": "B", "reliability": "media", "excerpt": " ".join(media.key_facts)}
        )
        community = card(
            "社区风向:Qwen3.8-Max-RC 已出现测试入口",
            ["社区帖子称 Qwen3.8-Max-RC 已出现测试入口并等待官方说明。"],
            entity="Qwen / 阿里",
            official=0,
        )
        community.community_count = 1
        community.evidence_links[0].update(
            {"tier": "C", "reliability": "community", "excerpt": community.key_facts[0]}
        )

        families = {build_story_spec(item).family_key for item in [official, media, community]}

        self.assertEqual(families, {"model|release|qwen3.8-max"})

    def test_qwen_space_separated_branches_remain_distinct(self):
        """空格分隔的 Max、Coder、VL 仍是不同产品分支。"""
        families = set()
        for model_prefix in ["Qwen3.8", "千问 3.8"]:
            for branch in ["Max", "Coder", "VL"]:
                sample = card(
                    f"{model_prefix} {branch} 正式发布",
                    [f"{model_prefix} {branch} 正式发布并开放 API。"],
                    entity="Qwen / 阿里",
                )
                sample.evidence_links[0].update(
                    {"tier": "A", "reliability": "official", "excerpt": sample.key_facts[0]}
                )
                families.add(build_story_spec(sample).family_key)

        self.assertEqual(
            families,
            {
                "model|release|qwen3.8-max",
                "model|release|qwen3.8-coder",
                "model|release|qwen3.8-vl",
            },
        )

    def test_model_integrations_include_the_target_platform_in_family(self):
        copilot = card(
            "GPT-5.6 available in GitHub Copilot",
            ["GPT-5.6 is available in GitHub Copilot."],
        )
        copilot.evidence_links[0].update(
            {"tier": "A", "reliability": "official", "excerpt": copilot.key_facts[0]}
        )
        databricks = card(
            "GPT-5.6 available on Databricks Agent Bricks",
            ["GPT-5.6 is available on Databricks Agent Bricks."],
        )
        databricks.evidence_links[0].update(
            {"tier": "A", "reliability": "official", "excerpt": databricks.key_facts[0]}
        )

        self.assertEqual(
            build_story_spec(copilot).family_key,
            "model|integration|github-copilot|gpt-5.6",
        )
        self.assertEqual(
            build_story_spec(databricks).family_key,
            "model|integration|databricks-agent-bricks|gpt-5.6",
        )

    def test_negated_or_multi_platform_integration_does_not_share_a_single_target_family(self):
        negative = card(
            "GPT-5.6 is not currently available in GitHub Copilot",
            ["GPT-5.6 is not currently available in GitHub Copilot."],
        )
        positive = card(
            "GPT-5.6 available in GitHub Copilot",
            ["GPT-5.6 is available in GitHub Copilot."],
        )
        multi = card(
            "GPT-5.6 available in GitHub Copilot and Databricks Agent Bricks",
            ["GPT-5.6 is available in GitHub Copilot and Databricks Agent Bricks."],
        )
        for sample in [negative, positive, multi]:
            sample.evidence_links[0].update(
                {"tier": "A", "reliability": "official", "excerpt": sample.key_facts[0]}
            )

        negative_spec = build_story_spec(negative)
        positive_spec = build_story_spec(positive)
        multi_spec = build_story_spec(multi)

        self.assertEqual(negative_spec.action, "动态")
        self.assertNotEqual(negative_spec.family_key, positive_spec.family_key)
        self.assertEqual(
            multi_spec.family_key,
            "model|integration|databricks-agent-bricks+github-copilot|gpt-5.6",
        )

    def test_model_alias_spacing_and_branch_word_boundary_are_conservative(self):
        compact = card("Qwen3.8 Max 正式发布", ["Qwen3.8 Max 正式发布。"], entity="Qwen / 阿里")
        spaced = card("Qwen 3.8 Max 正式发布", ["Qwen 3.8 Max 正式发布。"], entity="Qwen / 阿里")
        maximum = card("Qwen3.8 Maximum 正式发布", ["Qwen3.8 Maximum 正式发布。"], entity="Qwen / 阿里")
        for sample in [compact, spaced, maximum]:
            sample.evidence_links[0].update(
                {"tier": "A", "reliability": "official", "excerpt": sample.key_facts[0]}
            )

        self.assertEqual(build_story_spec(compact).family_key, build_story_spec(spaced).family_key)
        self.assertEqual(build_story_spec(compact).family_key, "model|release|qwen3.8-max")
        self.assertEqual(build_story_spec(maximum).family_key, "model|release|qwen3.8")

    def test_kimi_subscription_change_is_not_presented_as_k3_release(self):
        sample = card(
            "彭博社:Kimi K3正打破美国AI领先中国的固有认知 - 新浪财经",
            [
                "Kimi 官方称,过去 48 小时需求逼近容量上限,因此暂时暂停新订阅并优先保障现有会员。",
                "会员将拆分为面向 Web、App 和 Work 的 Kimi Membership,以及 Kimi Code Membership。",
            ],
            entity="Kimi / 月之暗面",
        )
        sample.evidence_links[0].update(
            {
                "tier": "A",
                "reliability": "official_social",
                "url": "https://x.com/Kimi_Moonshot/status/1",
                "excerpt": " ".join(sample.key_facts),
            }
        )

        spec = build_story_spec(sample)
        plan = build_editorial_plan(sample)

        self.assertEqual(spec.kind, "product_update")
        self.assertEqual(plan.title, "Kimi 暂停新订阅并调整会员方案")
        self.assertNotIn("K3", plan.title)
        self.assertNotIn("发布", plan.title)

    def test_subscription_title_respects_negation_and_claim_boundaries(self):
        negated = card(
            "Kimi 官方澄清订阅服务正常",
            ["Kimi 官方表示不会暂停新订阅，也不会拆分会员方案。"],
            entity="Kimi / 月之暗面",
        )
        negated.evidence_links[0].update(
            {"tier": "A", "reliability": "official", "excerpt": negated.key_facts[0]}
        )
        split_claims = card(
            "OpenAI 服务与订阅状态说明",
            ["服务编号 123 暂停服务。", "Codex 订阅用户不受影响。"],
        )
        split_claims.evidence_links[0].update(
            {"tier": "A", "reliability": "official", "excerpt": " ".join(split_claims.key_facts)}
        )
        extended_negation = card(
            "Kimi 官方澄清订阅服务正常",
            ["Kimi 官方否认将暂停新订阅，并表示会员无需调整。"],
            entity="Kimi / 月之暗面",
        )
        extended_negation.evidence_links[0].update(
            {"tier": "A", "reliability": "official", "excerpt": extended_negation.key_facts[0]}
        )
        coordinated_negation = card(
            "Kimi 官方澄清订阅服务正常",
            ["Kimi 不会暂停新订阅或拆分会员方案。"],
            entity="Kimi / 月之暗面",
        )
        no_plan = card(
            "Kimi 官方澄清订阅服务正常",
            ["Kimi 没有计划暂停新订阅。"],
            entity="Kimi / 月之暗面",
        )
        denied_rumour = card(
            "Kimi 官方澄清订阅服务正常",
            ["Kimi 暂停新订阅的传闻不实。"],
            entity="Kimi / 月之暗面",
        )
        for sample in [coordinated_negation, no_plan, denied_rumour]:
            sample.evidence_links[0].update(
                {"tier": "A", "reliability": "official", "excerpt": sample.key_facts[0]}
            )

        self.assertEqual(build_editorial_plan(negated).title, "Kimi 官方澄清订阅服务正常")
        self.assertEqual(build_story_spec(negated).action, "动态")
        self.assertEqual(build_editorial_plan(extended_negation).title, "Kimi 官方澄清订阅服务正常")
        self.assertEqual(build_story_spec(extended_negation).action, "动态")
        for sample in [coordinated_negation, no_plan, denied_rumour]:
            self.assertEqual(build_editorial_plan(sample).title, "Kimi 官方澄清订阅服务正常")
            self.assertEqual(build_story_spec(sample).action, "动态")
        self.assertEqual(build_editorial_plan(split_claims).title, "OpenAI 服务与订阅状态说明")

    def test_negated_model_release_is_not_classified_as_a_launch(self):
        sample = card(
            "Qwen3.8 尚未正式发布",
            ["Qwen 官方表示 Qwen3.8 尚未正式发布。"],
            entity="Qwen / 阿里",
        )
        sample.evidence_links[0].update(
            {"tier": "A", "reliability": "official", "excerpt": sample.key_facts[0]}
        )

        spec = build_story_spec(sample)

        self.assertEqual(spec.action, "动态")
        self.assertNotEqual(spec.kind, "model_release")

    def test_codex_alpha_release_title_explicitly_says_prerelease(self):
        sample = card(
            "OpenAI Codex GitHub Releases 发布 0.145.0-alpha.24",
            ["OpenAI Codex 修复插件加载问题并更新配置 schema。"],
        )
        sample.evidence_links[0].update(
            {
                "tier": "A",
                "reliability": "official",
                "url": "https://github.com/openai/codex/releases/tag/rust-v0.145.0-alpha.24",
                "excerpt": sample.key_facts[0],
            }
        )

        plan = build_editorial_plan(sample)

        self.assertEqual(plan.title, "OpenAI Codex 预发布 0.145.0-alpha.24")

    def test_prerelease_title_is_bound_to_the_current_release_version(self):
        stable = card(
            "OpenAI Codex 0.145.0 正式版替代 0.145.0-alpha.24",
            ["OpenAI Codex 正式发布 0.145.0，并替代此前的 alpha 版本。"],
        )
        stable.evidence_links[0].update(
            {"tier": "A", "reliability": "official", "excerpt": stable.key_facts[0]}
        )
        claim_only = card(
            "OpenAI Codex 发布新版本",
            ["OpenAI Codex 发布 0.146.0-beta.2，修复插件加载问题。"],
        )
        claim_only.evidence_links[0].update(
            {
                "tier": "A",
                "reliability": "official",
                "url": "https://github.com/openai/codex/releases/tag/rust-v0.146.0-beta.2",
                "excerpt": claim_only.key_facts[0],
            }
        )

        self.assertEqual(build_editorial_plan(stable).title, "OpenAI Codex 正式发布 0.145.0")
        self.assertEqual(
            build_editorial_plan(claim_only).title,
            "OpenAI Codex 预发布 0.146.0-beta.2",
        )

    def test_historical_beta_in_same_claim_does_not_replace_stable_version(self):
        sample = card(
            "OpenAI Codex 发布版本更新",
            ["OpenAI Codex 正式发布 0.146.0，并停止维护 0.146.0-beta.2。"],
        )
        sample.evidence_links[0].update(
            {
                "tier": "A",
                "reliability": "official",
                "url": "https://github.com/openai/codex/releases/tag/rust-v0.146.0",
                "excerpt": sample.key_facts[0],
            }
        )

        self.assertEqual(build_editorial_plan(sample).title, "OpenAI Codex 正式发布 0.146.0")

    def test_current_prerelease_beats_base_stable_and_upgrade_uses_target_stable(self):
        beta = card(
            "OpenAI Codex 预发布 0.146.0-beta.2",
            ["OpenAI Codex 预发布 0.146.0-beta.2，基于 0.145.0 正式版。"],
        )
        beta.evidence_links[0].update(
            {
                "tier": "A",
                "reliability": "official",
                "url": "https://github.com/openai/codex/releases/tag/rust-v0.146.0-beta.2",
                "excerpt": beta.key_facts[0],
            }
        )
        stable = card(
            "OpenAI Codex 发布 0.145.0 升级至 0.146.0 的正式版",
            ["OpenAI Codex 发布 0.145.0 升级至 0.146.0 的正式版。"],
        )
        stable.evidence_links[0].update(
            {
                "tier": "A",
                "reliability": "official",
                "url": "https://github.com/openai/codex/releases/tag/rust-v0.146.0",
                "excerpt": stable.key_facts[0],
            }
        )

        self.assertEqual(build_editorial_plan(beta).title, "OpenAI Codex 预发布 0.146.0-beta.2")
        self.assertEqual(build_editorial_plan(stable).title, "OpenAI Codex 正式发布 0.146.0")


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import patch

from briefing.collect import (
    _docusaurus_changelog_records,
    _merge_discovery_records,
    fetch_web_discovery_source,
    parse_sitemap_records,
    parse_web_discovery_records,
)
from briefing.models import Source


class FakeResponse:
    def __init__(self, text: str, status_code: int = 200, url: str = "https://example.com/", encoding: str = "utf-8") -> None:
        self.text = text
        self.status_code = status_code
        self.url = url
        self.encoding = encoding
        self.apparent_encoding = encoding

    @property
    def ok(self):
        return self.status_code < 400


class WebDiscoveryTests(unittest.TestCase):
    def test_duplicate_changelog_link_with_conflicting_dates_fails_closed(self):
        records = [
            {
                "href": "https://api-docs.deepseek.com/news/news0725",
                "text": "New API Features",
                "context": "Date: 2024-07-25",
                "date_text": "2024-07-25",
                "date_provenance": "changelog_heading",
            },
            {
                "href": "https://api-docs.deepseek.com/news/news0725",
                "text": "New API Features",
                "context": "Footer navigation under Date: 2024-05-17",
                "date_text": "2024-05-17",
                "date_provenance": "changelog_heading",
            },
        ]

        merged = _merge_discovery_records(records)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["date_text"], "")
        self.assertEqual(merged[0]["date_provenance"], "ambiguous_duplicate_date")
        self.assertIn("2024-05-17", merged[0]["context"])

    def test_conflicting_duplicate_date_cannot_fall_back_to_merged_text(self):
        source = Source(
            id="deepseek_official_web",
            name="DeepSeek Official Updates Web",
            tier="A",
            type="web",
            region="cn",
            url="https://api-docs.deepseek.com/updates",
            reliability="official",
            topics=["DeepSeek", "API", "model"],
        )
        shared_text = "2026-07-15\nDeepSeek-V4 API release\nDeepSeek launches a new model endpoint."
        records = [
            {
                "href": "https://api-docs.deepseek.com/news/example",
                "text": shared_text,
                "date_text": "2026-07-15",
                "date_provenance": "changelog_heading",
            },
            {
                "href": "https://api-docs.deepseek.com/news/example",
                "text": shared_text,
                "date_text": "2024-07-15",
                "date_provenance": "changelog_heading",
            },
        ]

        items = parse_web_discovery_records(records, source, final_url=source.url)

        self.assertEqual(len(items), 1)
        self.assertIsNone(items[0].published_at)
        self.assertEqual(items[0].raw["published_at_provenance"], "ambiguous_duplicate_date")

    def test_docusaurus_footer_navigation_does_not_reassign_news_link(self):
        html = """
        <html><body>
          <h2>Date: 2024-07-25</h2>
          <h3 id="new-api-features">New API Features</h3>
          <p>New API capabilities.</p>
          <a href="/news/news0725">this documentation</a>
          <h2>Date: 2024-05-17</h2>
          <h3 id="deepseek-chat">deepseek-chat</h3>
          <p>The model has been upgraded.</p>
          <nav>Previous DeepSeek API Upgrade
            <a href="/news/news0725">New API Features 2024/07/25</a>
          </nav>
        </body></html>
        """

        records = _docusaurus_changelog_records(html, "https://api-docs.deepseek.com/updates")

        linked = [row for row in records if row["href"].endswith("/news/news0725")]
        self.assertEqual(len(linked), 1)
        self.assertEqual(linked[0]["date_text"], "2024-07-25")
        deepseek_chat = next(row for row in records if "deepseek-chat" in row["text"])
        self.assertTrue(deepseek_chat["href"].endswith("/updates#deepseek-chat"))

    def test_official_sitemap_preserves_loc_lastmod_and_slug_title(self):
        source = Source(
            id="meta_ai_official_sitemap",
            name="Meta AI Official Sitemap",
            tier="A",
            type="sitemap",
            region="global",
            url="https://about.fb.com/post-sitemap3.xml",
            reliability="official",
            topics=["Meta", "Muse", "AI"],
        )
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://about.fb.com/news/2026/04/introducing-muse-spark-meta-superintelligence-labs/</loc><lastmod>2026-07-10T14:00:03+00:00</lastmod></url>
          <url><loc>https://about.fb.com/news/2024/01/old-company-update/</loc><lastmod>2024-01-02T00:00:00+00:00</lastmod></url>
          <url><loc>https://about.fb.com/news/category/technologies/meta/</loc><lastmod>2026-07-10T14:00:03+00:00</lastmod></url>
        </urlset>"""

        items = parse_sitemap_records(xml, source, source.url, limit=20)

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].title, "Introducing Muse Spark Meta Superintelligence Labs")
        self.assertEqual(items[0].published_at.isoformat(), "2026-07-10T14:00:03+00:00")
        self.assertEqual(items[0].raw["published_at_provenance"], "sitemap_lastmod")
        self.assertEqual(items[0].raw["kind"], "official_sitemap")

    def test_rendered_homepage_link_becomes_official_news_item(self):
        source = Source(
            id="tencent_hunyuan_official_web",
            name="Tencent Hunyuan Official Web",
            tier="B",
            type="web",
            region="cn",
            url="https://hy.tencent.com/",
            reliability="official",
            topics=["Tencent Hunyuan", "Hunyuan", "Hy", "model"],
        )
        records = [
            {
                "href": "https://hy.tencent.com/research/hy3",
                "text": "2026-07-06 | Hy LLM\nHy3 official release\nLearn more",
            },
            {"href": "https://hy.tencent.com/login", "text": "Login"},
        ]

        items = parse_web_discovery_records(records, source, final_url=source.url)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Hy3 official release")
        self.assertEqual(items[0].link, "https://hy.tencent.com/research/hy3")
        self.assertEqual(items[0].published_at.isoformat(), "2026-07-06T00:00:00+00:00")
        self.assertEqual(items[0].raw["kind"], "official_web_discovery")

    def test_ambiguous_page_dates_do_not_refresh_an_old_product_link(self):
        source = Source(
            id="minimax_official_web",
            name="MiniMax Official Site",
            tier="A",
            type="web",
            region="cn",
            url="https://www.minimaxi.com/news",
            reliability="official",
            topics=["MiniMax", "模型", "API"],
        )
        records = [
            {
                "href": "https://www.minimaxi.com/news/minimax-m3",
                "text": "MiniMax M3 正式发布\n2026-07-07 今日更新\n常驻模型 API 页面\n此前发布于 2026-06-16",
            }
        ]

        items = parse_web_discovery_records(records, source, final_url=source.url)

        self.assertEqual(len(items), 1)
        self.assertIsNone(items[0].published_at)
        self.assertEqual(items[0].raw["published_at_provenance"], "ambiguous_record_date")

    def test_record_date_provenance_is_kept_for_unambiguous_card(self):
        source = Source(
            id="openai_official_web",
            name="OpenAI Official News Web",
            tier="A",
            type="web",
            region="global",
            url="https://openai.com/news/",
            reliability="official",
            topics=["OpenAI", "ChatGPT", "GPT"],
        )
        items = parse_web_discovery_records(
            [{"href": "https://openai.com/news/gpt-5", "text": "2026-07-07\nOpenAI releases GPT-5\nAPI and product access"}],
            source,
            final_url=source.url,
        )

        self.assertEqual(items[0].raw["published_at_provenance"], "record_heading")
        self.assertEqual(items[0].published_at.isoformat(), "2026-07-07T00:00:00+00:00")

    def test_skip_links_and_same_page_fragments_are_not_news(self):
        source = Source(
            id="openai_official_web",
            name="OpenAI Official News Web",
            tier="A",
            type="web",
            region="global",
            url="https://openai.com/news/",
            reliability="official",
            topics=["OpenAI", "ChatGPT", "GPT"],
        )
        records = [
            {"href": "https://openai.com/news/#main", "text": "Skip to main content"},
            {"href": "https://openai.com/news/", "text": "News"},
            {"href": "https://openai.com/research/index/", "text": "Research"},
            {"href": "https://openai.com/research/index/", "text": "Research Index"},
            {"href": "https://openai.com/policy", "text": "Policy"},
            {"href": "https://talent.openai.com/jobs", "text": "Join us"},
            {
                "href": "https://openai.com/news/gpt-5-system-card/",
                "text": "2026-07-07\nOpenAI releases GPT-5 system card\nLearn more",
            },
        ]

        items = parse_web_discovery_records(records, source, final_url=source.url)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "OpenAI releases GPT-5 system card")
        self.assertEqual(items[0].link, "https://openai.com/news/gpt-5-system-card/")

    def test_final_redirect_domain_is_allowed_for_official_site(self):
        source = Source(
            id="meta_llama_official_web",
            name="Meta Llama Official Web",
            tier="A",
            type="web",
            region="global",
            url="https://www.llama.com/",
            reliability="official",
            topics=["Meta", "Llama", "model", "open source"],
        )
        records = [
            {"href": "https://developer.meta.com/ai/", "text": "Models"},
            {"href": "https://developer.meta.com/ai/models/llama-4/", "text": "Llama 4"},
        ]

        items = parse_web_discovery_records(records, source, final_url="https://developer.meta.com/ai/")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Llama 4")
        self.assertEqual(items[0].link, "https://developer.meta.com/ai/models/llama-4/")

    def test_generic_link_text_can_use_model_version_slug(self):
        source = Source(
            id="zhipu_official_web",
            name="Z.ai / GLM Official Site",
            tier="B",
            type="web",
            region="cn",
            url="https://z.ai/",
            reliability="official",
            topics=["Z.ai", "智谱", "GLM", "模型", "API"],
        )
        records = [
            {"href": "https://z.ai/blog/glm-5.2", "text": "技术博客"},
            {"href": "https://chat.z.ai/", "text": "开始对话"},
        ]

        items = parse_web_discovery_records(records, source, final_url="https://chat.z.ai/")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "GLM 5.2")
        self.assertEqual(items[0].link, "https://z.ai/blog/glm-5.2")

    def test_product_pricing_entry_is_not_news(self):
        source = Source(
            id="mistral_official_web",
            name="Mistral AI Official News Web",
            tier="A",
            type="web",
            region="global",
            url="https://mistral.ai/news/",
            reliability="official",
            topics=["Mistral", "model", "API", "open source"],
        )
        records = [
            {"href": "https://mistral.ai/pricing/api/", "text": "API pricing"},
            {
                "href": "https://mistral.ai/news/mistral-small-4-release/",
                "text": "2026-07-07\nMistral Small 4 release",
            },
        ]

        items = parse_web_discovery_records(records, source, final_url=source.url)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Mistral Small 4 release")
        self.assertEqual(items[0].link, "https://mistral.ai/news/mistral-small-4-release/")

    def test_date_plus_generic_button_uses_specific_model_slug(self):
        source = Source(
            id="doubao_official_web",
            name="ByteDance Seed Official Web",
            tier="B",
            type="web",
            region="cn",
            url="https://seed.bytedance.com/",
            reliability="official",
            topics=["豆包", "Doubao", "字节", "Seed", "模型"],
        )
        records = [
            {
                "href": "https://seed.bytedance.com/zh/blog/official-launch-of-seedance-2-0",
                "text": "2026.02.12模型发布了解更多",
            },
            {"href": "https://seed.bytedance.com/zh/about", "text": "联系我们"},
        ]

        items = parse_web_discovery_records(records, source, final_url="https://seed.bytedance.com/zh/")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Seedance 2.0")
        self.assertEqual(items[0].link, "https://seed.bytedance.com/zh/blog/official-launch-of-seedance-2-0")

    def test_policy_and_language_prefixed_model_index_are_not_news(self):
        deepseek = Source(
            id="deepseek_official_web",
            name="DeepSeek Official Site",
            tier="A",
            type="web",
            region="cn",
            url="https://www.deepseek.com/",
            reliability="official",
            topics=["DeepSeek", "深度求索", "模型", "API"],
        )
        seed = Source(
            id="doubao_official_web",
            name="ByteDance Seed Official Web",
            tier="B",
            type="web",
            region="cn",
            url="https://seed.bytedance.com/",
            reliability="official",
            topics=["豆包", "Doubao", "字节", "Seed", "模型"],
        )

        self.assertEqual(
            parse_web_discovery_records(
                [
                    {
                        "href": "https://cdn.deepseek.com/policies/zh-CN/deepseek-privacy-policy.html",
                        "text": "DeepSeek R1",
                    }
                ],
                deepseek,
                final_url=deepseek.url,
            ),
            [],
        )
        self.assertEqual(
            parse_web_discovery_records(
                [{"href": "https://seed.bytedance.com/zh/models?view_from=homepage_tab", "text": "Seed3D 2.0"}],
                seed,
                final_url="https://seed.bytedance.com/zh/",
            ),
            [],
        )

    def test_docusaurus_changelog_documentation_link_uses_surrounding_news_context(self):
        source = Source(
            id="deepseek_official_web",
            name="DeepSeek Official Updates Web",
            tier="A",
            type="web",
            region="cn",
            url="https://api-docs.deepseek.com/updates",
            reliability="official",
            topics=["DeepSeek", "深度求索", "模型", "API"],
        )
        html = """
        <html><body>
          <h1>Change Log</h1>
          <h2 id="date-2026-04-24">Date: 2026-04-24<a href="#date-2026-04-24">​</a></h2>
          <h3 id="deepseek-v4">DeepSeek-V4<a href="#deepseek-v4">​</a></h3>
          <p>The DeepSeek API now supports V4-Pro and V4-Flash, available via both the OpenAI ChatCompletions interface and the Anthropic interface. To access the new models, the model parameter should be set to <code>deepseek-v4-pro</code> or <code>deepseek-v4-flash</code>.</p>
          <p>The two legacy API model names, <code>deepseek-chat</code> and <code>deepseek-reasoner</code>, will be discontinued in three months (2026-07-24).</p>
          <p>For more details, please refer to <a href="/news/news260424">this documentation</a>.</p>
        </body></html>
        """

        with patch("briefing.collect.requests.get", return_value=FakeResponse(html, url=source.url)):
            health, items = fetch_web_discovery_source(source)

        self.assertEqual(health.status, "ok")
        self.assertEqual(items[0].title, "DeepSeek-V4")
        self.assertEqual(items[0].link, "https://api-docs.deepseek.com/news/news260424")
        self.assertIn("V4-Pro", items[0].summary)
        self.assertIn("V4-Flash", items[0].summary)
        self.assertEqual(items[0].published_at.isoformat(), "2026-04-24T00:00:00+00:00")

    def test_static_official_card_generic_link_uses_parent_context(self):
        source = Source(
            id="qwen_official_web",
            name="Qwen Official Blog Web",
            tier="A",
            type="web",
            region="cn",
            url="https://qwenlm.github.io/blog/",
            reliability="official",
            topics=["Qwen", "通义", "千问", "模型", "开源"],
        )
        html = """
        <html><body>
          <article class="post-card">
            <h2>Qwen3-Coder Flash</h2>
            <p>2026-07-07</p>
            <p>Qwen updates the coding model with faster agent workflows and new API access.</p>
            <a href="/blog/qwen3-coder-flash/">Learn more</a>
          </article>
        </body></html>
        """

        with patch("briefing.collect.requests.get", return_value=FakeResponse(html, url=source.url)):
            health, items = fetch_web_discovery_source(source)

        self.assertEqual(health.status, "ok")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Qwen3-Coder Flash")
        self.assertEqual(items[0].link, "https://qwenlm.github.io/blog/qwen3-coder-flash/")
        self.assertIn("agent workflows", items[0].summary)

    def test_static_chinese_official_card_generic_link_uses_parent_context(self):
        source = Source(
            id="doubao_official_web",
            name="ByteDance Seed Official Web",
            tier="B",
            type="web",
            region="cn",
            url="https://seed.bytedance.com/zh/",
            reliability="official",
            topics=["豆包", "Doubao", "字节", "Seed", "模型"],
        )
        html = """
        <html><body>
          <div class="news-card">
            <h3>Seedream 4.0 正式发布</h3>
            <span>2026年07月07日</span>
            <p>支持图像生成和编辑，开放 API 与创作工具入口。</p>
            <a href="/zh/blog/seedream-4-0">了解更多</a>
          </div>
        </body></html>
        """

        with patch("briefing.collect.requests.get", return_value=FakeResponse(html, url=source.url)):
            health, items = fetch_web_discovery_source(source)

        self.assertEqual(health.status, "ok")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Seedream 4.0 正式发布")
        self.assertEqual(items[0].link, "https://seed.bytedance.com/zh/blog/seedream-4-0")
        self.assertIn("开放 API", items[0].summary)

    def test_date_card_skips_section_label_before_title(self):
        source = Source(
            id="minimax_official_web",
            name="MiniMax Official Site",
            tier="B",
            type="web",
            region="cn",
            url="https://www.minimaxi.com/",
            reliability="official",
            topics=["MiniMax", "模型", "视频", "语音"],
        )
        records = [
            {
                "href": "https://www.minimaxi.com/blog/minimax-maxproof-math-proof-evolution",
                "text": "文章\n2026-06-09\nMaxProof: 生成式验证强化学习驱动的数学证明进化系统\nM3 模型发布推文显示，IMO 与 USAMO 测试中超过人类金牌线。\n阅读更多",
            }
        ]

        items = parse_web_discovery_records(records, source, final_url=source.url)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "MaxProof: 生成式验证强化学习驱动的数学证明进化系统")
        self.assertIn("M3 模型", items[0].summary)

    def test_bad_static_title_triggers_rendered_official_card_fallback(self):
        source = Source(
            id="minimax_official_web",
            name="MiniMax Official Site",
            tier="B",
            type="web",
            region="cn",
            url="https://www.minimaxi.com/",
            reliability="official",
            topics=["MiniMax", "模型", "视频", "语音"],
        )
        html = """
        <html><body>
          <a href="/blog/minimax-maxproof-math-proof-evolution">
            在 M3 发布推文中，我们汇报了 M3 模型在 IMO 2025 与 USAMO 2026 两组国际数学竞赛真题上的表现：在搭配 MaxProof 框架后，M3 均超过了人类金牌线。本文将进一步展开我们在推进数学证明能力过程中的技术路径。
          </a>
        </body></html>
        """
        rendered_records = [
            {
                "href": "https://www.minimaxi.com/blog/minimax-maxproof-math-proof-evolution",
                "text": "文章\n2026-06-09\nMaxProof: 生成式验证强化学习驱动的数学证明进化系统\nM3 模型发布推文显示，IMO 与 USAMO 测试中超过人类金牌线。\n阅读更多",
                "context": "",
            }
        ]

        with (
            patch("briefing.collect.requests.get", return_value=FakeResponse(html, url=source.url)),
            patch("briefing.collect._rendered_link_records", return_value=(rendered_records, "", source.url, "")),
        ):
            health, items = fetch_web_discovery_source(source)

        self.assertEqual(health.status, "ok")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "MaxProof: 生成式验证强化学习驱动的数学证明进化系统")

    def test_official_listing_page_is_not_treated_as_news_item(self):
        source = Source(
            id="minimax_official_web",
            name="MiniMax Official Site",
            tier="B",
            type="web",
            region="cn",
            url="https://www.minimaxi.com/",
            reliability="official",
            topics=["MiniMax", "模型", "视频", "语音"],
        )
        records = [
            {
                "href": "https://www.minimaxi.com/blog",
                "text": "最新研究\nMaxProof: 生成式验证强化学习驱动的数学证明进化系统\nM3 模型发布推文显示，IMO 与 USAMO 测试中超过人类金牌线。",
            },
            {
                "href": "https://www.minimaxi.com/blog/minimax-maxproof-math-proof-evolution",
                "text": "文章\n2026-06-09\nMaxProof: 生成式验证强化学习驱动的数学证明进化系统\nM3 模型发布推文显示，IMO 与 USAMO 测试中超过人类金牌线。\n阅读更多",
            },
        ]

        items = parse_web_discovery_records(records, source, final_url=source.url)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].link, "https://www.minimaxi.com/blog/minimax-maxproof-math-proof-evolution")


if __name__ == "__main__":
    unittest.main()

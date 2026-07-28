import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from briefing.content_enrichment import ArticleResult, enrich_feed_items, enrichment_priority, extract_article_text, extract_key_facts, extract_outbound_links, fetch_article, should_enrich_item
from briefing.models import FeedItem


class FakeResponse:
    def __init__(self, text: str, status_code: int = 200, url: str = "https://example.com/article", content_type: str = "text/html") -> None:
        self.text = text
        self.status_code = status_code
        self.url = url
        self.headers = {"content-type": content_type}
        self.content = text.encode("utf-8")

    def json(self):
        import json
        return json.loads(self.text)


def media_item() -> FeedItem:
    return FeedItem(
        source_id="infoq_cn",
        source_name="InfoQ 中文",
        source_tier="B",
        source_reliability="media",
        title="腾讯混元 Hy3 正式发布",
        link="https://www.infoq.cn/hy3",
        guid="hy3",
        summary="腾讯混元 Hy3 正式发布。",
        published_at=datetime(2026, 7, 6, 2, 0, tzinfo=timezone.utc),
    )


class ContentEnrichmentTests(unittest.TestCase):
    def test_duplicate_published_meta_prefers_real_earlier_article_time(self):
        """动态 SSR 时间不能覆盖页面中同时存在的真实发布时间。"""
        html = """
        <html><head>
          <meta property="article:published_time" content="2026-07-20T00:52:05+08:00">
          <meta data-react-helmet="true" property="article:published_time" content="2026-07-19 13:00:07">
        </head><body><article>中国地震局发布人工智能防震减灾行动方案，并公布九方面任务。</article></body></html>
        """

        _title, _description, _body, published_at = extract_article_text(html)

        self.assertIsNotNone(published_at)
        self.assertEqual(published_at.isoformat(), "2026-07-19T05:00:07+00:00")

    def test_json_ld_render_time_does_not_override_older_published_meta(self):
        """JSON-LD 里的 SSR 当前时间也必须与真实 meta 发布时间比较。"""
        html = """
        <html><head>
          <script type="application/ld+json">
            {"@type":"NewsArticle","datePublished":"2026-07-20T00:52:05+08:00"}
          </script>
          <meta property="article:published_time" content="2026-05-19T23:40:45+00:00">
        </head><body><article>NVIDIA published this article in May.</article></body></html>
        """

        _title, _description, _body, published_at = extract_article_text(html)

        self.assertIsNotNone(published_at)
        self.assertEqual(published_at.isoformat(), "2026-05-19T23:40:45+00:00")

    def test_outbound_links_keep_labelled_first_party_candidates(self):
        html = """
        <article>
          <a href="https://circleci.com/">CircleCI</a>
          <a href="https://circleci.com/blog/chunk-sidecars/#demo"><strong>Chunk Sidecars</strong></a>
          <a href="javascript:void(0)">ignore</a>
        </article>
        """

        links = extract_outbound_links(html, "https://www.infoq.cn/article/example")

        self.assertEqual(links[1]["url"], "https://circleci.com/blog/chunk-sidecars/")
        self.assertEqual(links[1]["text"], "Chunk Sidecars")

    def test_outbound_links_capture_bare_article_urls_and_trim_prose_punctuation(self):
        html = """
        <article>
          <p>参考资料：</p>
          <p>[1]https://www.wired.com/story/openai-head-of-safety-leaving/）。</p>
          <p>系统卡：https://deploymentsafety.openai.com/gpt-5-6/gpt-5-6.pdf。</p>
        </article>
        """

        links = extract_outbound_links(html, "https://www.qbitai.com/2026/07/448825.html")

        self.assertEqual(
            [link["url"] for link in links],
            [
                "https://www.wired.com/story/openai-head-of-safety-leaving/",
                "https://deploymentsafety.openai.com/gpt-5-6/gpt-5-6.pdf",
            ],
        )

    def test_organizational_action_fact_outranks_generic_product_commentary(self):
        text = "\n".join(
            [
                "OpenAI 发布 GPT-5.6，更新推理能力，并新增多项 API 功能。",
                "GPT-5.6 上线了，OpenAI 也开始重组自己的安全团队了。",
                "据 WIRED 消息，OpenAI 安全系统负责人 Johannes Heidecke 已告知员工自己将离职。",
            ]
        )

        facts = extract_key_facts(text, limit=1)

        self.assertEqual(len(facts), 1)
        self.assertIn("Johannes Heidecke 已告知员工自己将离职", facts[0])

    def test_fact_extraction_preserves_paragraphs_and_filters_publisher_navigation(self):
        text = "\n".join(
            [
                "Snowflake new",
                "记录自己日常工作的实践、心得",
                "2026-07-11 北京",
                "阅读完需：约 14 分钟",
                "当地时间 7 月 10 日，苹果向联邦地区法院提起诉讼，指控 OpenAI 盗用商业秘密。",
                "案件编号为 5:26-cv-07078，被告包括 OpenAI Group PBC 与 io Products。",
            ]
        )

        facts = extract_key_facts(text)

        self.assertTrue(any("提起诉讼" in fact for fact in facts))
        self.assertTrue(any("5:26-cv-07078" in fact for fact in facts))
        self.assertFalse(any("Snowflake" in fact or "阅读完需" in fact for fact in facts))

    def test_x_rss_post_uses_selection_stage_screenshot_not_article_crawl(self):
        item = media_item()
        item.source_id = "x_openai_sama"
        item.source_name = "X / OpenAI: Sam Altman"
        item.source_tier = "C"
        item.source_reliability = "official_personnel"
        item.link = "https://x.com/sama/status/123"

        self.assertFalse(should_enrich_item(item))

    def test_http_result_skips_browser_when_content_is_substantive(self):
        article = ArticleResult(
            status="ok", url="https://example.com/news", final_url="https://example.com/news",
            title="完整新闻", description="摘要", text="正文内容。" * 150,
            facts=["产品新增 API 支持。", "延迟降低 20%。"], crawler="requests",
        )
        with patch("briefing.content_enrichment.fetch_article_with_requests", return_value=article), patch("briefing.content_enrichment._crawl4ai_once") as browser:
            result = fetch_article("https://example.com/news", use_crawl4ai=True)

        self.assertIs(result, article)
        browser.assert_not_called()

    def test_google_news_wrapper_does_not_start_browser(self):
        article = ArticleResult(status="empty", url="https://news.google.com/rss/articles/x", final_url="https://news.google.com/rss/articles/x", status_code=200, crawler="requests")
        with patch("briefing.content_enrichment.fetch_article_with_requests", return_value=article), patch("briefing.content_enrichment._crawl4ai_once") as browser:
            result = fetch_article(article.url, use_crawl4ai=True)

        self.assertIs(result, article)
        browser.assert_not_called()

    def test_forbidden_page_does_not_start_browser(self):
        article = ArticleResult(status="error", url="https://example.com/blocked", final_url="https://example.com/blocked", status_code=403, crawler="requests")
        with patch("briefing.content_enrichment.fetch_article_with_requests", return_value=article), patch("briefing.content_enrichment._crawl4ai_once") as browser:
            result = fetch_article(article.url, use_crawl4ai=True)

        self.assertIs(result, article)
        browser.assert_not_called()

    def test_progress_sink_reports_current_and_completed_item(self):
        item = media_item()
        article = ArticleResult(status="ok", url=item.link, final_url=item.link, text="正文" * 100, facts=["新增 API 支持。"], crawler="requests")
        progress = []
        with patch("briefing.content_enrichment.fetch_article", return_value=article):
            enrich_feed_items([item], max_items=1, progress_sink=progress.append)

        self.assertEqual(progress[0]["current_url"], item.link)
        self.assertEqual(progress[-1]["current_url"], "")
        self.assertEqual(progress[-1]["ok"], 1)

    def test_document_sink_receives_successful_article(self):
        item = media_item()
        article = ArticleResult(
            status="ok", url=item.link, final_url=item.link, title=item.title,
            description="description", text="腾讯混元发布 Hy3 并开放 API。" * 20,
            facts=["腾讯混元发布 Hy3 并开放 API。"], crawler="crawl4ai",
        )
        captured = []

        with patch("briefing.content_enrichment.fetch_article", return_value=article):
            enrich_feed_items([item], max_items=1, document_sink=lambda feed, result: captured.append((feed, result)))

        self.assertEqual(len(captured), 1)
        self.assertIs(captured[0][0], item)
        self.assertIs(captured[0][1], article)

    def test_should_enrich_mainstream_media_and_official_ai_pages(self):
        self.assertTrue(should_enrich_item(media_item()))
        official = FeedItem(
            source_id="sample",
            source_name="Sample",
            source_tier="A",
            source_reliability="official",
            title="Qwen releases a new open model",
            link="https://example.com/qwen",
            guid="1",
            summary="local deployment",
        )
        self.assertTrue(should_enrich_item(official))

    def test_date_modified_is_not_treated_as_publish_time(self):
        html = """
        <html><head>
          <title>Old product page</title>
          <script type="application/ld+json">
            {"headline":"Old product page","dateModified":"2026-07-03T10:00:00Z"}
          </script>
        </head><body><article><p>This is an old product page that was only modified recently.</p></article></body></html>
        """

        _title, _description, _body, published_at = extract_article_text(html)

        self.assertIsNone(published_at)

    def test_dated_product_article_outranks_undated_high_volume_artifact(self):
        product = FeedItem(
            source_id="openai_news", source_name="OpenAI News", source_tier="A", source_reliability="official",
            title="ChatGPT adds project actions", link="https://openai.com/index/project-actions", guid="product",
            summary="ChatGPT adds actions across files and apps.", published_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
        )
        paper = FeedItem(
            source_id="arxiv_cs_cl", source_name="arXiv", source_tier="A", source_reliability="official",
            title="A benchmark paper", link="https://arxiv.org/abs/2607.00001", guid="paper",
            summary="A benchmark for language models.", published_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
        )

        self.assertGreater(enrichment_priority(product), enrichment_priority(paper))

    def test_roundup_navigation_debris_is_not_extracted_as_fact(self):
        text = (
            "字节 Seedance 正在进入影视制作。\n"
            "让 Agent 越用越强：AReaL 2.0 开源 ]( https://example.com/other ) 2026-07-02。\n"
            "量子位的朋友们 2026-07-06 17:28:52 更多精彩内容。"
        )

        facts = extract_key_facts(text)

        joined = "\n".join(facts)
        self.assertIn("Seedance", joined)
        self.assertNotIn("AReaL", joined)
        self.assertNotIn("量子位的朋友们", joined)

    def test_fetch_article_extracts_key_facts(self):
        html = """
        <html><head>
          <title>腾讯混元 Hy3 正式发布</title>
          <meta name="description" content="元宝同步上线 Hy3 Agent 能力。">
          <script type="application/ld+json">
            {"headline":"腾讯混元 Hy3 正式发布","datePublished":"2026-07-06T10:00:00+08:00"}
          </script>
        </head><body>
          <article>
            <p>腾讯混元 Hy3 正式发布，元宝同步上线 Hy3 Agent 能力并免费开放。</p>
            <p>Hy3 已在 WorkBuddy/CodeBuddy、元宝、Marvis、ima 等多个业务接入。</p>
            <p>API 已在腾讯云 TokenHub 上线，多个海外 API 平台也将陆续接入。</p>
          </article>
        </body></html>
        """
        with patch("briefing.content_enrichment.requests.get", return_value=FakeResponse(html)):
            article = fetch_article("https://www.infoq.cn/hy3", use_crawl4ai=False)

        self.assertEqual(article.status, "ok")
        joined = "\n".join(article.facts or [])
        self.assertIn("tokenhub", joined.lower())
        self.assertIn("WorkBuddy/CodeBuddy", joined)
        self.assertEqual(article.published_at.isoformat(), "2026-07-06T02:00:00+00:00")

    def test_enrich_feed_items_merges_article_facts_into_summary(self):
        html = """
        <html><body>
          <p>腾讯混元 Hy3 正式发布，元宝同步上线 Hy3 Agent 能力并免费开放。</p>
          <p>API 已在腾讯云 TokenHub 上线。</p>
        </body></html>
        """
        item = media_item()
        with patch("briefing.content_enrichment.requests.get", return_value=FakeResponse(html)):
            stats = enrich_feed_items([item], max_items=5, use_crawl4ai=False)

        self.assertEqual(stats["attempted"], 1)
        self.assertEqual(stats["ok"], 1)
        self.assertIn("TokenHub", item.summary)
        self.assertEqual(item.raw["article_enrichment"]["status"], "ok")

    def test_undated_official_web_discovery_keeps_article_date_as_evidence_only(self):
        item = FeedItem(
            source_id="minimax_official_web",
            source_name="MiniMax Official Site",
            source_tier="A",
            source_reliability="official",
            title="MiniMax Hailuo 2.3",
            link="https://www.minimaxi.com/news/hailuo-23",
            guid="minimax-hailuo-23",
            summary="Official model page.",
            published_at=None,
            raw={"kind": "official_web_discovery"},
        )
        discovered_date = datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc)
        article = ArticleResult(
            status="ok",
            url=item.link,
            text="MiniMax Hailuo 2.3 provides new video generation capabilities.",
            facts=["MiniMax Hailuo 2.3 details."],
            published_at=discovered_date,
        )

        with patch("briefing.content_enrichment.fetch_article", return_value=article):
            enrich_feed_items([item], max_items=5, use_crawl4ai=False)

        self.assertIsNone(item.published_at)
        self.assertEqual(item.raw["article_enrichment"]["published_at"], discovered_date.isoformat())
        self.assertEqual(item.raw["article_enrichment"]["published_at_provenance"], "article_metadata")
        self.assertEqual(item.raw["article_published_at"], discovered_date.isoformat())

    def test_article_date_demotes_old_story_relisted_by_rss(self):
        """正文日期明显早于 RSS 时，按原始公开时间判定滚动窗口。"""
        item = FeedItem(
            source_id="nvidia_developer_blog",
            source_name="NVIDIA Developer Blog",
            source_tier="A",
            source_reliability="official",
            title="NVIDIA-Verified Agent Skills",
            link="https://developer.nvidia.com/blog/nvidia-verified-agent-skills/",
            guid="nvidia-verified-agent-skills",
            summary="NVIDIA verified agent skills.",
            published_at=datetime(2026, 7, 19, 15, 29, 53, tzinfo=timezone.utc),
        )
        article_time = datetime(2026, 5, 19, 23, 40, 45, tzinfo=timezone.utc)
        article = ArticleResult(
            status="ok",
            url=item.link,
            text="NVIDIA-verified agent skills provide capability governance for AI agents.",
            facts=["NVIDIA-verified agent skills provide capability governance for AI agents."],
            published_at=article_time,
        )

        with patch("briefing.content_enrichment.fetch_article", return_value=article):
            enrich_feed_items([item], max_items=5, use_crawl4ai=False)

        self.assertEqual(item.published_at, article_time)
        self.assertEqual(item.raw["source_published_at"], "2026-07-19T15:29:53+00:00")
        self.assertEqual(item.raw["published_at_provenance"], "article_metadata_overrode_source")
        self.assertGreater(item.raw["published_at_conflict_hours"], 24)

    def test_hunyuan_hy3_spa_config_adapter_enriches_thin_page(self):
        import json

        html = """
        <html><head><title>Tencent Hy</title></head>
        <body><div id="root">Loading...</div></body></html>
        """
        config = {
            "hy3PageData": {
                "title": "Hy3 preview : ???????????",
                "introduction": [
                    "4 ? 23 ?????? Hy3 preview ????????????? 295B????? 21B????? 256K ??????"
                ],
                "links": [
                    {"url": "https://console.cloud.tencent.com/tokenhub/models/detail?modelId=hy3-preview&regionId=1"},
                    {"url": "https://github.com/Tencent-Hunyuan/Hy3-preview"},
                    {"url": "https://huggingface.co/tencent/Hy3-preview"},
                ],
                "FeedbackContent": {
                    "cardList": [
                        {
                            "descTitle": "CodeBuddy",
                            "descContent": "?CodeBuddy????Hy3 preview ? token ???? 54%???????? 47%??????? 99.99%+?????????Hy3 preview ??????? 495 ???? Agent ????",
                        }
                    ]
                },
            }
        }

        def fake_get(url, **kwargs):
            if url == "https://hy.tencent.com/research/hy3":
                return FakeResponse(html, url=url)
            if "hy3-config-zh.json" in url:
                return FakeResponse(json.dumps(config, ensure_ascii=False), url=url, content_type="application/json")
            raise AssertionError(f"unexpected url: {url}")

        with patch("briefing.content_enrichment.requests.get", side_effect=fake_get):
            article = fetch_article("https://hy.tencent.com/research/hy3", use_crawl4ai=False)

        self.assertEqual(article.status, "ok")
        self.assertEqual(article.crawler, "hunyuan_config_adapter")
        joined = "\n".join(article.facts or []) + "\n" + article.text
        self.assertIn("54%", joined)
        self.assertIn("47%", joined)
        self.assertIn("99.99%", joined)
        self.assertIn("495", joined)
        self.assertIn("tokenhub", joined.lower())

    def test_meta_tags_adapter_enriches_thin_model_page(self):
        html = """
        <html><head>
          <title>Unmatched Performance and Efficiency | Llama 4</title>
          <meta name="description" content="Meet Llama 4, the latest multimodal AI model offering cost efficiency, 10M context window and easy deployment.">
          <meta property="og:image" content="https://example.com/llama4.png">
        </head><body><div id="root">More details</div></body></html>
        """

        def fake_get(url, **kwargs):
            if url == "https://developer.meta.com/ai/models/llama-4/":
                return FakeResponse(html, url=url)
            if url == "https://example.com/llama4.png":
                return FakeResponse("PNG", url=url, content_type="image/png")
            raise AssertionError(f"unexpected url: {url}")

        with TemporaryDirectory() as tmp, patch("briefing.content_enrichment.requests.get", side_effect=fake_get):
            article = fetch_article("https://developer.meta.com/ai/models/llama-4/", use_crawl4ai=False, asset_dir=Path(tmp))

        self.assertEqual(article.status, "ok")
        self.assertEqual(article.crawler, "meta_tags_adapter")
        self.assertIn("10M context", article.text)
        self.assertTrue(article.facts)
        self.assertEqual(len(article.images), 1)


if __name__ == "__main__":
    unittest.main()

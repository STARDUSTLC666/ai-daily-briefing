from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import requests
from PIL import Image, ImageDraw

from briefing.core import UTC, EvidenceCard, FeedItem, Source, now_utc, project_root, read_json
from briefing.ingest import (
    attach_evidence_screenshots,
    attach_github_compare_evidence,
    collect_sources,
    collect_x_candidates,
    discover_web_source,
    enrich_feed_items,
    enrich_github_release_items,
    evidence_requires_screenshot,
    extract_article_text,
    load_agent_social_source,
    parse_feed,
    parse_google_news,
    parse_hf_models,
    plan_enrichment_items,
    prune_evidence_screenshots,
    reconcile_official_sources,
)


class _MockResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        text: str = "",
        json_data: Any = None,
        url: str = "https://example.com",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.text = text if text or json_data is None else json.dumps(json_data, ensure_ascii=False)
        self._json_data = json_data
        self.url = url
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"
        self.headers = headers or {"content-type": "text/html; charset=utf-8"}

    @property
    def ok(self) -> bool:
        return self.status_code < 400

    def json(self) -> Any:
        if self._json_data is not None:
            return self._json_data
        return json.loads(self.text)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            err = requests.HTTPError(f"HTTP {self.status_code}")
            err.response = self  # type: ignore[assignment]
            raise err

    def close(self) -> None:
        return None

    def __enter__(self) -> _MockResponse:
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        return None


class SourcesTests(unittest.TestCase):
    def test_parse_feed_sample_fixture_hf_models_and_google_news(self) -> None:
        fixture_xml = (project_root() / "tests" / "fixtures" / "sample_feed.xml").read_text(encoding="utf-8")
        rss_src = Source(
            id="qwen_blog",
            name="Qwen Blog",
            tier="A",
            type="rss",
            region="cn",
            url="https://example.com/rss.xml",
            reliability="official",
        )
        rss_items = parse_feed(fixture_xml, rss_src, final_url="https://example.com/rss.xml")
        self.assertEqual(len(rss_items), 2)
        self.assertEqual(rss_items[0].title, "Qwen releases a new open model for local deployment")
        self.assertEqual(rss_items[0].link, "https://example.com/qwen-release")
        self.assertEqual(rss_items[0].guid, "qwen-release-1")
        self.assertEqual(rss_items[0].published_at, datetime(2026, 7, 3, 8, 0, tzinfo=UTC))
        self.assertIsNone(rss_items[1].published_at)

        hf_src = Source(
            id="qwen_hf_models",
            name="Qwen HF",
            tier="A",
            type="hf_models",
            region="cn",
            url="https://huggingface.co/api/models?author=Qwen",
            reliability="official",
        )
        hf_payload = json.dumps(
            [
                {
                    "modelId": "Qwen/Qwen3-8B-Instruct",
                    "lastModified": "2026-07-03T06:30:00Z",
                    "pipeline_tag": "text-generation",
                    "tags": ["transformers", "safetensors"],
                    "downloads": 4200,
                    "likes": 128,
                    "siblings": [{"rfilename": "config.json"}, {"rfilename": "model.safetensors"}],
                }
            ]
        )
        hf_items = parse_hf_models(hf_payload, hf_src, final_url=hf_src.url)
        self.assertEqual(len(hf_items), 1)
        self.assertEqual(hf_items[0].title, "Hugging Face 模型仓库更新：Qwen/Qwen3-8B-Instruct")
        self.assertEqual(hf_items[0].link, "https://huggingface.co/Qwen/Qwen3-8B-Instruct")
        self.assertEqual(hf_items[0].raw["model_id"], "Qwen/Qwen3-8B-Instruct")
        self.assertIn("pipeline=text-generation", hf_items[0].summary)
        self.assertIn("downloads=4200", hf_items[0].summary)

        gn_src = Source(
            id="gn_ai",
            name="Google News AI",
            tier="B",
            type="google_news",
            region="global",
            url="OpenAI GPT-5",
            reliability="aggregator",
        )
        gn_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel>
          <item>
            <title>OpenAI launches GPT-5 API - TechCrunch</title>
            <link>https://news.google.com/rss/articles/abc</link>
            <guid>gn-1</guid>
            <pubDate>Fri, 03 Jul 2026 09:00:00 GMT</pubDate>
            <description>OpenAI released GPT-5 today.</description>
            <source url="https://techcrunch.com">TechCrunch</source>
          </item>
          <item>
            <title>Undated item should be skipped</title>
            <link>https://news.google.com/rss/articles/def</link>
          </item>
        </channel></rss>"""
        gn_items = parse_google_news(gn_xml, gn_src, final_url="https://news.google.com/rss/search")
        self.assertEqual(len(gn_items), 1)
        self.assertEqual(gn_items[0].source_name, "TechCrunch")
        self.assertEqual(gn_items[0].raw["publisher"], "TechCrunch")
        self.assertEqual(gn_items[0].raw["publisher_url"], "https://techcrunch.com")

    def test_discover_web_source_static_and_docusaurus_updates(self) -> None:
        static_src = Source(
            id="openai_web",
            name="OpenAI News",
            tier="A",
            type="web",
            region="global",
            url="https://openai.com/news/",
            reliability="official",
            topics=["OpenAI", "GPT"],
        )
        static_html = """
        <html><body>
          <article class="news-card">
            <a href="/index/introducing-gpt-5/">
              <div>2026-07-03</div>
              <h3>OpenAI launches GPT-5 for complex coding</h3>
              <p>GPT-5 improves agentic coding and API reasoning reliability.</p>
            </a>
          </article>
          <a href="/careers">Join us</a>
        </body></html>
        """
        with (
            patch("briefing.ingest.sources.requests.get", return_value=_MockResponse(text=static_html, url=static_src.url)),
            patch("briefing.ingest.sources._rendered_link_records", return_value=([], static_src.url, "")),
        ):
            health, items = discover_web_source(static_src)
        self.assertEqual(health.status, "ok")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].link, "https://openai.com/index/introducing-gpt-5/")
        self.assertIn("GPT-5", items[0].title)
        self.assertEqual(items[0].published_at, datetime(2026, 7, 3, 0, 0, tzinfo=UTC))
        self.assertEqual(items[0].raw["kind"], "official_web_discovery")

        docusaurus_src = Source(
            id="claude_updates",
            name="Anthropic Docs Updates",
            tier="A",
            type="official_site",
            region="global",
            url="https://docs.anthropic.com/updates",
            reliability="official",
            topics=["Anthropic", "Claude"],
        )
        docusaurus_html = """
        <html><body>
          <h2>July 3, 2026</h2>
          <h3 id="claude-4-5-release">Claude 4.5 API release</h3>
          <p>Anthropic launches Claude 4.5 with 200K context support. Read <a href="/en/docs/models/claude-4-5">documentation</a>.</p>
        </body></html>
        """
        with (
            patch("briefing.ingest.sources.requests.get", return_value=_MockResponse(text=docusaurus_html, url=docusaurus_src.url)),
            patch("briefing.ingest.sources._rendered_link_records", return_value=([], docusaurus_src.url, "")),
        ):
            doc_health, doc_items = discover_web_source(docusaurus_src)
        self.assertEqual(doc_health.status, "ok")
        self.assertEqual(len(doc_items), 1)
        self.assertEqual(doc_items[0].link, "https://docs.anthropic.com/en/docs/models/claude-4-5")
        self.assertEqual(doc_items[0].published_at, datetime(2026, 7, 3, 0, 0, tzinfo=UTC))
        self.assertEqual(doc_items[0].raw["published_at_provenance"], "changelog_heading")

    def test_load_agent_social_source_valid_stale_and_missing_and_collect_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            leads_path = root / "codex-social-leads.json"
            missing_src = Source(
                id="x_official",
                name="X Official",
                tier="A",
                type="agent_social",
                region="global",
                url=f"{leads_path}#OpenAI,AnthropicAI",
                reliability="official_social",
            )
            missing_health, missing_items = load_agent_social_source(missing_src)
            self.assertEqual(missing_health.status, "missing")
            self.assertEqual(missing_items, [])

            now = now_utc()
            post_url = "https://x.com/OpenAI/status/1940685355315200000"
            valid_payload = {
                "version": 1,
                "agent": "codex",
                "generated_at": now.isoformat(),
                "lane_audits": {
                    "x_official": {
                        "status": "checked",
                        "checked_at": now.isoformat(),
                        "accounts_checked": ["OpenAI", "AnthropicAI"],
                        "qualifying_items": 1,
                    }
                },
                "items": [
                    {
                        "source_id": "x_official",
                        "url": post_url,
                        "title": "OpenAI 宣布 GPT-5 API 正式开放",
                        "text": "OpenAI officially announced that GPT-5 is now available in the developer API with lower latency.",
                        "published_at": (now - timedelta(hours=1)).isoformat(),
                        "checks": {"account_verified": True, "post_text_verified": True},
                        "checked_urls": [post_url],
                    }
                ],
            }
            leads_path.write_text(json.dumps(valid_payload), encoding="utf-8")
            ok_health, ok_items = load_agent_social_source(missing_src)
            self.assertEqual(ok_health.status, "ok")
            self.assertEqual(len(ok_items), 1)
            self.assertEqual(ok_items[0].link, "https://x.com/openai/status/1940685355315200000")
            self.assertEqual(ok_items[0].raw["kind"], "codex_agent_social_lead")

            stale_payload = {**valid_payload, "generated_at": (now - timedelta(hours=72)).isoformat()}
            leads_path.write_text(json.dumps(stale_payload), encoding="utf-8")
            stale_health, stale_items = load_agent_social_source(missing_src)
            self.assertEqual(stale_health.status, "error")
            self.assertIn("stale", stale_health.error)
            self.assertEqual(stale_items, [])

            unchecked_payload = {
                **valid_payload,
                "lane_audits": {
                    "x_official": {
                        "status": "checked",
                        "checked_at": now.isoformat(),
                        "accounts_checked": ["OpenAI"],  # Missing AnthropicAI
                        "qualifying_items": 1,
                    }
                },
            }
            leads_path.write_text(json.dumps(unchecked_payload), encoding="utf-8")
            unchecked_health, unchecked_items = load_agent_social_source(missing_src)
            self.assertEqual(unchecked_health.status, "error")
            self.assertIn("did not check configured accounts", unchecked_health.error)
            self.assertEqual(unchecked_items, [])

            # Restore valid social file and verify collect_sources across multiple source types
            leads_path.write_text(json.dumps(valid_payload), encoding="utf-8")
            fixture_xml = (project_root() / "tests" / "fixtures" / "sample_feed.xml").read_text(encoding="utf-8")
            rss_src = Source(
                id="qwen_blog",
                name="Qwen Blog",
                tier="A",
                type="rss",
                region="cn",
                url="https://example.com/rss.xml",
                reliability="official",
            )
            disabled_src = Source(
                id="disabled_feed",
                name="Disabled Feed",
                tier="C",
                type="rss",
                region="global",
                url="https://example.com/off.xml",
                enabled=False,
            )
            with patch("briefing.ingest.sources.requests.get", return_value=_MockResponse(text=fixture_xml, url=rss_src.url)):
                health_list, collected_items = collect_sources([missing_src, rss_src, disabled_src], max_workers=2)
            statuses = {h.source_id: h.status for h in health_list}
            self.assertEqual(statuses, {"x_official": "ok", "qwen_blog": "ok", "disabled_feed": "disabled"})
            self.assertEqual(len(collected_items), 3)


class GitHubAndSocialTests(unittest.TestCase):
    def test_github_release_enrichment_and_compare_attachment(self) -> None:
        item = FeedItem(
            source_id="qwen_github_releases",
            source_name="Qwen GitHub Releases",
            source_tier="A",
            source_reliability="official",
            title="v1.2.0",
            link="https://github.com/QwenLM/Qwen3/releases/tag/v1.2.0",
            guid="qwen3-v1.2.0",
            summary="Release v1.2.0",
            published_at=datetime(2026, 7, 3, 7, 0, tzinfo=UTC),
        )

        def _fake_github_get(url: str, **_kwargs: Any) -> _MockResponse:
            if url.endswith("/releases/tags/v1.2.0"):
                return _MockResponse(json_data={"body": "新增 FP8 量化推理支持；修复流式超时问题"}, url=url)
            if "releases?per_page=20" in url:
                return _MockResponse(json_data=[{"tag_name": "v1.2.0"}, {"tag_name": "v1.1.0"}], url=url)
            if "/compare/v1.1.0...v1.2.0" in url:
                return _MockResponse(
                    json_data={
                        "html_url": "https://github.com/QwenLM/Qwen3/compare/v1.1.0...v1.2.0",
                        "total_commits": 3,
                        "files": [{"filename": "infer.py"}, {"filename": "schema.json"}],
                        "commits": [
                            {"commit": {"message": "fix: streaming timeout bug (#10)"}},
                            {"commit": {"message": "feat: add fp8 quantization"}},
                        ],
                    },
                    url=url,
                )
            raise AssertionError(f"unexpected GitHub API URL: {url}")

        with patch("briefing.ingest.github.requests.get", side_effect=_fake_github_get):
            enriched_count = enrich_github_release_items([item])

        self.assertEqual(enriched_count, 1)
        self.assertIn("GitHub compare 显示 3 个提交、2 个文件变更", item.summary)
        self.assertIn("修复一项代码问题", item.summary)
        self.assertIs(item.raw["github_compare_verified"], True)
        self.assertEqual(item.raw["github_compare_url"], "https://github.com/QwenLM/Qwen3/compare/v1.1.0...v1.2.0")

        now = datetime(2026, 7, 3, 8, 0, tzinfo=UTC)
        card = EvidenceCard(
            cluster_key="Qwen / 阿里:v1.2.0",
            event_title="Qwen 发布 v1.2.0",
            entity="Qwen / 阿里",
            risk="green",
            confidence=90,
            selected=True,
            reason="official_release",
            source_count=1,
            official_count=1,
            media_count=0,
            community_count=0,
            first_seen_at=now,
            latest_published_at=now,
            key_facts=["Qwen 发布 v1.2.0"],
            evidence_links=[
                {
                    "source": "Qwen GitHub Releases",
                    "tier": "A",
                    "reliability": "official",
                    "title": "v1.2.0",
                    "url": item.link,
                    "published_at": now.isoformat(),
                    "excerpt": item.summary,
                    "github_compare_verified": "true",
                    "github_compare_url": str(item.raw["github_compare_url"]),
                    "github_compare_summary": str(item.raw["github_compare_summary"]),
                }
            ],
            uncertainty=[],
            score=86.0,
        )
        self.assertEqual(attach_github_compare_evidence([card]), 1)
        self.assertEqual(attach_github_compare_evidence([card]), 0)  # idempotent
        compare_rows = [ev for ev in card.evidence_links if ev.get("derived_evidence") == "github_api_compare"]
        self.assertEqual(len(compare_rows), 1)
        self.assertEqual(compare_rows[0]["tier"], "A")
        self.assertEqual(compare_rows[0]["reliability"], "official")
        self.assertEqual(compare_rows[0]["url"], "https://github.com/QwenLM/Qwen3/compare/v1.1.0...v1.2.0")
        self.assertTrue(any("版本对比" in fact for fact in card.key_facts))

    def test_collect_x_candidates_mocked_fetcher(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg_path = root / "sources.json"
            out_path = root / "candidates.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "sources": [
                            {
                                "id": "x_official",
                                "name": "X Official",
                                "tier": "A",
                                "type": "agent_social",
                                "url": "data/leads.json#OpenAI,AnthropicAI",
                                "enabled": True,
                                "reliability": "official_social",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            ref_now = datetime(2026, 7, 3, 10, 0, tzinfo=UTC)

            def _mock_fetcher(account: str, **_kwargs: Any) -> list[dict[str, Any]]:
                if account == "OpenAI":
                    return [
                        {
                            "url": "https://twitter.com/OpenAI/status/1940685355315200000?s=20",
                            "text": "GPT-5 API is live today.",
                            "created_at": "2026-07-03T08:00:00Z",
                            "likes": "1200",
                            "retweets": 300,
                            "media_urls": ["https://pbs.twimg.com/media/sample.jpg"],
                        },
                        {
                            "url": "https://x.com/OpenAI/status/1800000000000000000",
                            "text": "Too old tweet outside lookback window",
                            "created_at": "2026-06-01T08:00:00Z",
                        },
                        {
                            "url": "https://x.com/SomeoneElse/status/1940685355315299999",
                            "text": "Retweet from another handle should be filtered",
                            "created_at": "2026-07-03T08:30:00Z",
                        },
                    ]
                return []

            payload = collect_x_candidates(cfg_path, out_path=out_path, now=ref_now, lookback_hours=24, fetcher=_mock_fetcher)
            self.assertEqual(payload["producer"], "opencli")
            self.assertEqual(len(payload["items"]), 1)
            self.assertEqual(payload["items"][0]["url"], "https://x.com/openai/status/1940685355315200000")
            self.assertEqual(payload["items"][0]["engagement"]["likes"], 1200)
            self.assertEqual(payload["lanes"]["x_official"]["accounts_checked"], ["OpenAI", "AnthropicAI"])
            self.assertEqual(read_json(out_path)["items"][0]["status_id"], "1940685355315200000")


class EnrichAndReconcileTests(unittest.TestCase):
    def test_extract_article_text_plan_and_enrich_feed_items(self) -> None:
        article_html = """
        <html>
        <head>
          <meta property="og:title" content="Fallback Meta Title" />
          <meta property="article:published_time" content="2026-07-03T04:00:00Z" />
          <meta property="og:image" content="/assets/hero.png" />
          <script type="application/ld+json">
          {
            "@context": "https://schema.org",
            "headline": "OpenAI 正式发布 GPT-5 推理模型",
            "description": "GPT-5 上下文窗口扩展至 400K tokens 并降低 API 价格。",
            "datePublished": "2026-07-03T08:00:00Z"
          }
          </script>
        </head>
        <body>
          <p>OpenAI 今日正式发布 GPT-5 多模态推理模型，上下文窗口达到 400K tokens，API 调用价格降低 50%。</p>
          <p>新模型在 SWE-bench 编程评测榜单取得 78.5% 成功率，详情见官方公告 <a href="https://openai.com/index/gpt-5">OpenAI Release</a>。</p>
          <p>开源工具仓库地址：https://github.com/openai/codex。</p>
          <img src="/assets/chart.jpg" alt="Benchmark chart" />
        </body>
        </html>
        """
        parsed = extract_article_text(article_html, base_url="https://media.example.com/posts/1")
        self.assertEqual(parsed["title"], "OpenAI 正式发布 GPT-5 推理模型")
        # Earliest published_at (04:00 UTC from meta vs 08:00 UTC from JSON-LD) wins
        self.assertEqual(parsed["published_at"], datetime(2026, 7, 3, 4, 0, tzinfo=UTC))

        # Conversely, when JSON-LD datePublished is earlier than meta article:published_time, JSON-LD wins
        json_ld_earlier_html = article_html.replace("2026-07-03T04:00:00Z", "2026-07-03T11:00:00Z")
        self.assertEqual(
            extract_article_text(json_ld_earlier_html, base_url="https://media.example.com/posts/1")["published_at"],
            datetime(2026, 7, 3, 8, 0, tzinfo=UTC),
        )
        self.assertGreaterEqual(len(parsed["paragraphs"]), 2)
        self.assertTrue(any("400K tokens" in f for f in parsed["facts"]))
        link_urls = {row["url"] for row in parsed["links"]}
        self.assertIn("https://openai.com/index/gpt-5", link_urls)
        self.assertIn("https://github.com/openai/codex", link_urls)
        img_urls = [row["url"] for row in parsed["images"]]
        self.assertIn("https://media.example.com/assets/hero.png", img_urls)
        self.assertIn("https://media.example.com/assets/chart.jpg", img_urls)

        now = now_utc()
        good_item = FeedItem(
            source_id="media_cn",
            source_name="量子位",
            source_tier="B",
            source_reliability="media",
            title="OpenAI 发布 GPT-5 模型",
            link="https://media.example.com/posts/1",
            guid="m-1",
            summary="简短摘要",
            published_at=datetime(2026, 7, 3, 20, 0, tzinfo=UTC),
            fetched_at=now,
        )
        x_item = FeedItem(
            source_id="x_official",
            source_name="X Official",
            source_tier="A",
            source_reliability="official_social",
            title="OpenAI post",
            link="https://x.com/openai/status/1940685355315200000",
            guid="x-1",
            published_at=now,
        )
        noise_item = FeedItem(
            source_id="media_cn",
            source_name="Media",
            source_tier="B",
            source_reliability="media",
            title="OpenAI 每日早报 weekly digest",
            link="https://media.example.com/posts/roundup",
            guid="m-noise",
            published_at=now,
        )
        planned = plan_enrichment_items([good_item, x_item, noise_item])
        self.assertEqual([it.guid for it in planned], ["m-1"])

        with patch("briefing.ingest.enrich.requests.get", return_value=_MockResponse(text=article_html, url=good_item.link)):
            stats = enrich_feed_items([good_item, x_item], defaults={"content_enrichment_use_crawl4ai": False})

        self.assertEqual(stats["attempted"], 1)
        self.assertEqual(stats["ok"], 1)
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(good_item.raw["article_enrichment"]["status"], "ok")
        self.assertTrue(good_item.raw["evidence_excerpts"])
        # Article published_at (04:00) is 16h earlier than source published_at (20:00) -> overrides source
        self.assertEqual(good_item.published_at, datetime(2026, 7, 3, 4, 0, tzinfo=UTC))
        self.assertEqual(good_item.raw["published_at_provenance"], "article_metadata_overrode_source")

    def test_reconcile_official_sources_first_hop_and_x_snowflake_stale(self) -> None:
        ref_now = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)
        media_item = FeedItem(
            source_id="36kr_ai",
            source_name="36Kr",
            source_tier="B",
            source_reliability="media",
            title="OpenAI launches Codex CLI 2.0 for local coding",
            link="https://36kr.com/p/999",
            guid="kr-999",
            summary="OpenAI released Codex CLI 2.0.",
            published_at=ref_now - timedelta(hours=2),
            raw={
                "article_enrichment": {
                    "status": "ok",
                    "links": [{"url": "https://openai.com/index/codex-cli-2-0/", "text": "OpenAI Codex CLI 2.0 release"}],
                }
            },
        )

        stale_dt = ref_now - timedelta(days=6)
        stale_sid = (int(stale_dt.timestamp() * 1000) - 1288834974657) << 22
        social_media_item = FeedItem(
            source_id="36kr_ai",
            source_name="36Kr",
            source_tier="B",
            source_reliability="media",
            title="OpenAI hints at older model note on X",
            link="https://36kr.com/p/1000",
            guid="kr-1000",
            summary="Post on X from OpenAI.",
            published_at=ref_now - timedelta(hours=1),
            raw={
                "article_enrichment": {
                    "status": "ok",
                    "links": [{"url": f"https://twitter.com/OpenAI/status/{stale_sid}", "text": "OpenAI X announcement"}],
                }
            },
        )
        configured_sources = [
            Source(id="openai_news", name="OpenAI", tier="A", type="rss", region="global", url="https://openai.com/news/rss.xml", reliability="official"),
            Source(id="x_official", name="X Official", tier="A", type="agent_social", region="global", url="data/leads.json#OpenAI", reliability="official_social"),
        ]

        def _mock_article_fetch(url: str, **_kwargs: Any) -> dict[str, Any]:
            return {
                "status": "ok",
                "url": url,
                "final_url": url,
                "status_code": 200,
                "title": "Introducing Codex CLI 2.0",
                "description": "OpenAI releases Codex CLI 2.0 for local coding workflows.",
                "text": "Codex CLI 2.0 supports faster local sandbox execution.",
                "facts": ["OpenAI releases Codex CLI 2.0 for local coding workflows"],
                "published_at": ref_now - timedelta(hours=3),
                "crawler": "requests",
            }

        summary = reconcile_official_sources(
            [media_item, social_media_item],
            sources=configured_sources,
            now=ref_now,
            defaults={"lookback_hours": 24},
            fetcher=_mock_article_fetch,
        )
        self.assertEqual(summary["matched"], 2)
        self.assertEqual(summary["matched_social"], 1)
        self.assertEqual(summary["stale"], 1)

        rec1 = media_item.raw["official_reconciliation"]
        self.assertEqual(rec1["status"], "matched")
        self.assertEqual(rec1["domain"], "openai.com")
        self.assertEqual(rec1["freshness"], "fresh")
        self.assertEqual(rec1["reliability"], "official")

        rec2 = social_media_item.raw["official_reconciliation"]
        self.assertEqual(rec2["status"], "matched_social")
        self.assertEqual(rec2["published_at_provenance"], "x_status_snowflake")
        self.assertEqual(rec2["freshness"], "stale")


class ScreenshotsTests(unittest.TestCase):
    def test_requires_screenshot_pillow_excerpt_fallback_and_prune(self) -> None:
        now = datetime(2026, 7, 3, 9, 0, tzinfo=UTC)
        pure_official = EvidenceCard(
            cluster_key="openai:api-note",
            event_title="OpenAI 更新开发者文档目录",
            entity="OpenAI",
            risk="green",
            confidence=92,
            selected=True,
            reason="official",
            source_count=1,
            official_count=1,
            media_count=0,
            community_count=0,
            first_seen_at=now,
            latest_published_at=now,
            key_facts=["OpenAI 更新开发者文档目录结构"],
            evidence_links=[{"source": "OpenAI", "tier": "A", "reliability": "official", "title": "Docs", "url": "https://openai.com/docs"}],
            uncertainty=[],
        )
        self.assertFalse(evidence_requires_screenshot(pure_official, 0))

        bench_card = EvidenceCard(
            cluster_key="deepseek:bench",
            event_title="DeepSeek R2 编程评测跑分公布",
            entity="DeepSeek",
            risk="yellow",
            confidence=78,
            selected=True,
            reason="benchmark_media",
            source_count=2,
            official_count=0,
            media_count=1,
            community_count=0,
            first_seen_at=now,
            latest_published_at=now,
            key_facts=["DeepSeek R2 在 LiveCodeBench 编程评测取得 82.4% 得分，推理延迟降低 35%"],
            evidence_links=[
                {
                    "source": "机器之心",
                    "tier": "B",
                    "reliability": "media",
                    "title": "DeepSeek R2 编程评测跑分与延迟实测",
                    "url": "https://jiqizhixin.com/articles/deepseek-r2-eval",
                    "excerpt": "机器之心实测显示，DeepSeek R2 在 LiveCodeBench 编程基准测试取得 82.4% 得分，同时端到端推理延迟较上一代降低 35%，支持本地量化部署。",
                },
                {
                    "source": "DeepSeek Compare",
                    "tier": "A",
                    "reliability": "official",
                    "title": "GitHub compare",
                    "url": "https://github.com/deepseek-ai/DeepSeek-R2/compare/v1.0...v1.1",
                    "derived_evidence": "github_api_compare",
                },
            ],
            uncertainty=[],
        )
        self.assertTrue(evidence_requires_screenshot(bench_card, 0))
        self.assertFalse(evidence_requires_screenshot(bench_card, 1))

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            drawn_texts: list[str] = []
            real_draw_text = ImageDraw.ImageDraw.text

            def _record_text(draw_self: Any, xy: Any, text: Any, *args: Any, **kwargs: Any) -> Any:
                drawn_texts.append(str(text))
                return real_draw_text(draw_self, xy, text, *args, **kwargs)

            with (
                patch.dict(os.environ, {"BRIEFING_EVIDENCE_SCREENSHOTS": "auto"}, clear=False),
                patch("briefing.ingest.screenshots.find_browser_executable", return_value=Path("C:/mock/chrome.exe")),
                patch("briefing.ingest.screenshots._capture_url", return_value=(False, "playwright_unavailable:ImportError")),
                patch.object(ImageDraw.ImageDraw, "text", _record_text),
            ):
                shot_stats = attach_evidence_screenshots([bench_card], out_dir)

            self.assertEqual(shot_stats["status"], "ok")
            self.assertEqual(shot_stats["captured"], 1)
            ev0 = bench_card.evidence_links[0]
            self.assertEqual(ev0["screenshot_status"], "captured")
            self.assertEqual(ev0["screenshot_kind"], "source_excerpt_card")
            self.assertIn("原文摘录 · 非网页截图", drawn_texts)

            shot_path = Path(ev0["screenshot_path"])
            self.assertTrue(shot_path.is_file())
            with Image.open(shot_path) as img:
                self.assertEqual(img.size, (1360, 900))

            # Add an unreferenced PNG and verify prune_evidence_screenshots removes only the orphan
            orphan_png = out_dir / "evidence-screenshots" / "orphan-unused.png"
            orphan_png.write_bytes(b"unused-png")
            keep_note = out_dir / "evidence-screenshots" / "notes.txt"
            keep_note.write_text("ignore non-png", encoding="utf-8")

            removed = prune_evidence_screenshots(out_dir, [bench_card])
            self.assertEqual(removed, [str(orphan_png.resolve())] if orphan_png.resolve() != orphan_png else [str(orphan_png)])
            self.assertFalse(orphan_png.exists())
            self.assertTrue(shot_path.is_file())
            self.assertTrue(keep_note.is_file())


if __name__ == "__main__":
    unittest.main()

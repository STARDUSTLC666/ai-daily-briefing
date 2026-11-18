from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import hmac
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from briefing.core import UTC, Cluster, FeedItem, file_sha256, read_json, write_json_atomic, write_text_atomic
from briefing.publish import (
    BiliCredentials,
    BilibiliClient,
    BilibiliError,
    automation_status,
    check_bilibili_auth,
    preflight_bilibili,
    publish_bilibili,
    runtime_doctor,
)
from briefing.publish.bilibili import canonical_signing_string, sign_headers
from briefing.verify import (
    RELEASE_FILE_PATHS,
    build_release_attestation,
    create_agent_package,
    finalize_agent_edits,
    is_concrete_public_ai_policy,
    mark_render_required,
    parse_srt,
    record_render_contract,
    repairable_news_positions,
    segments_fingerprint,
    verify_agent_edit_contract,
    verify_clusters,
    verify_release_attestation,
    verify_review_render_contract,
    verify_run_dir,
)

FIXED_NOW = datetime(2026, 11, 18, 2, 0, 0, tzinfo=UTC)


def _make_feed_item(
    *,
    source_id: str = "openai_news",
    source_name: str = "OpenAI",
    source_tier: str = "A",
    source_reliability: str = "official",
    title: str = "OpenAI 发布 GPT-5.2 模型并开放 API",
    link: str = "https://openai.com/index/gpt-5-2",
    guid: str = "guid-1",
    summary: str = "OpenAI 正式发布 GPT-5.2 模型，支持 128k token 上下文窗口并降低 30% API 调用价格。",
    published_at: datetime | None = FIXED_NOW - timedelta(hours=3),
    raw: dict | None = None,
) -> FeedItem:
    return FeedItem(
        source_id=source_id,
        source_name=source_name,
        source_tier=source_tier,
        source_reliability=source_reliability,
        title=title,
        link=link,
        guid=guid,
        summary=summary,
        published_at=published_at,
        fetched_at=FIXED_NOW,
        raw=dict(raw or {}),
    )


def _populate_valid_run_dir(run_dir: Path, *, run_id: str = "2026-11-18-clean01") -> dict:
    """Create a complete, self-consistent run directory that passes all 6 gates in verify_run_dir."""
    run_dir.mkdir(parents=True, exist_ok=True)
    screenshots_dir = run_dir / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    render_dir = run_dir / "render"
    render_dir.mkdir(parents=True, exist_ok=True)

    shot_path = screenshots_dir / "ev1.png"
    shot_path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR-valid-evidence-png")

    video_path = run_dir / "final.mp4"
    video_path.write_bytes(b"\x00\x00\x00\x18ftypmp42-synthetic-video-payload")
    cover_path = run_dir / "cover.png"
    cover_path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR-valid-cover-png")

    news_title = "OpenAI 发布 GPT-5.2 模型接口升级"
    news_narration = (
        "OpenAI 发布 GPT-5.2 模型接口升级，新增 128k token 上下文窗口，"
        "API 调用延迟降低 30%，并面向开发者开放多步工具调用能力。"
    )

    base_segments = [
        {
            "index": 0,
            "kind": "intro",
            "position": None,
            "total": None,
            "story_id": "",
            "claim_ids": [],
            "generation_path": "structured_editorial_plan",
            "editorial_tier": "headline",
            "title": "AI 日报",
            "headline": "AI 日报",
            "caption": "聚焦模型更新等 1 条经核验 AI 动态",
            "text": "早上好，这里是 AI 日报，今天 1 条，马上开始。",
            "active_tab": "Intro",
            "bottom_tabs": ["开场", "01 OpenAI", "收尾"],
            "bottom_active": "开场",
            "cards": [{"title": "今日看点", "body": news_title}],
            "visual_pages": [{"kind": "overview", "title": "资讯概览", "lead": "OpenAI", "cards": []}],
        },
        {
            "index": 1,
            "kind": "news",
            "position": 1,
            "total": 1,
            "story_id": "story-openai-52",
            "claim_ids": ["claim-openai-52"],
            "generation_path": "structured_editorial_plan",
            "editorial_tier": "headline",
            "title": news_title,
            "headline": news_title,
            "caption": news_title,
            "text": news_narration,
            "active_tab": "模型更新",
            "bottom_tabs": ["开场", "01 OpenAI", "收尾"],
            "bottom_active": "01 OpenAI",
            "cards": [{"title": "开发接口", "body": "API 调用延迟降低 30%"}],
            "visual_pages": [
                {
                    "kind": "cards",
                    "title": news_title,
                    "lead": news_title,
                    "cards": [{"title": "开发接口", "body": "API 调用延迟降低 30%"}],
                },
                {
                    "kind": "evidence",
                    "title": "原文画面",
                    "lead": news_title,
                    "source": "OpenAI",
                    "source_url": "https://openai.com/index/gpt-5-2",
                    "evidenceVisual": {
                        "source": "OpenAI",
                        "title": news_title,
                        "url": "https://openai.com/index/gpt-5-2",
                        "image": "screenshots/ev1.png",
                        "asset": "evidence/ev1.png",
                        "status": "captured",
                        "required": True,
                    },
                    "cards": [],
                },
            ],
        },
        {
            "index": 2,
            "kind": "outro",
            "position": None,
            "total": None,
            "story_id": "",
            "claim_ids": [],
            "generation_path": "structured_editorial_plan",
            "editorial_tier": "headline",
            "title": "播送完毕",
            "headline": "播送完毕",
            "caption": "我们明天见",
            "text": "今天的新闻播完了，我们明天早上见。",
            "active_tab": "Outro",
            "bottom_tabs": ["开场", "01 OpenAI", "收尾"],
            "bottom_active": "收尾",
            "cards": [],
            "visual_pages": [{"kind": "closing_continuation", "title": "播送完毕", "cards": []}],
        },
    ]

    timings = [(0.0, 8.0, 8.0), (8.0, 40.0, 32.0), (40.0, 46.0, 6.0)]
    timed_segments = [
        {**seg, "start": start, "end": end, "duration": dur}
        for seg, (start, end, dur) in zip(base_segments, timings)
    ]

    write_json_atomic(run_dir / "news-script.json", {"version": 1, "segments": base_segments})
    write_text_atomic(
        run_dir / "news-script.md",
        f"# AI 日报新闻稿\n\n## 开场 AI 日报\n- 解说：{base_segments[0]['text']}\n\n"
        f"## 01 {news_title}\n- 解说：{news_narration}\n\n"
        f"## 收尾 播送完毕\n- 解说：{base_segments[2]['text']}\n",
    )
    write_json_atomic(run_dir / "script.json", timed_segments)

    srt_content = (
        "1\n00:00:00,000 --> 00:00:07,500\n早上好，这里是 AI 日报，今天 1 条，马上开始。\n\n"
        "2\n00:00:08,000 --> 00:00:15,500\nOpenAI 发布 GPT-5.2 模型接口升级，新增 128k token 上下文窗口。\n\n"
        "3\n00:00:15,600 --> 00:00:23,000\nAPI 调用延迟降低 30%，并面向开发者开放多步工具调用能力。\n\n"
        "4\n00:00:40,000 --> 00:00:45,500\n今天的新闻播完了，我们明天早上见。\n"
    )
    write_text_atomic(run_dir / "subtitles.srt", srt_content)
    cues = parse_srt(srt_content)
    subtitle_contract = "\n".join(f"{c['start']:.3f}|{c['end']:.3f}|{c['text']}" for c in cues)
    subtitle_sha = hashlib.sha256(subtitle_contract.encode("utf-8")).hexdigest()

    timeline_block = f"00:00 开场｜AI 日报\n00:08 {news_title}\n"
    write_text_atomic(
        run_dir / "bilibili.md",
        f"# B站投稿草稿\n\n## 简介\n今天的 AI 日报精选 1 条动态。\n\n时间轴：\n{timeline_block}\n本次播报完毕。\n",
    )
    write_text_atomic(
        run_dir / "pinned-comment.md",
        f"今日时间轴：\n{timeline_block}\n你更关心模型实际效果还是调用成本？\n",
    )
    write_json_atomic(
        run_dir / "bilibili.json",
        {
            "title": f"{news_title}【AI 日报 2026-11-18】",
            "desc": "今天的 AI 日报：OpenAI 动态，一次看完。",
            "tag": "AI,人工智能,大模型,科技早报",
            "copyright": 1,
            "no_reprint": 1,
            "video": str(video_path.resolve()),
            "cover": str(cover_path.resolve()),
            "subtitle": str((run_dir / "subtitles.srt").resolve()),
            "pinned_comment": str((run_dir / "pinned-comment.md").resolve()),
            "tid": 188,
        },
    )
    write_text_atomic(run_dir / "sources.md", "# 新闻源健康报告\n\n- OpenAI: ok\n")
    write_text_atomic(run_dir / "fact-check.md", f"# 事实核查报告\n\n## {news_title}\n")

    video_sha = file_sha256(video_path)
    shot_sha = file_sha256(shot_path)
    write_json_atomic(
        run_dir / "audio-quality.json",
        {
            "schema_version": 2,
            "media_path": str(video_path.resolve()),
            "media_sha256": video_sha,
            "integrated_lufs": -15.0,
            "true_peak_dbtp": -2.1,
            "long_silence_ratio": 0.01,
            "duration_seconds": 46.0,
            "ok": True,
        },
    )
    write_json_atomic(
        render_dir / "evidence-visual-contract.json",
        {
            "schema_version": 1,
            "renderer": "remotion",
            "visual_sha256": "visual-sha-placeholder",
            "final_media_sha256": video_sha,
            "items": [
                {
                    "slide_index": 2,
                    "segment_index": 1,
                    "required": True,
                    "source": "OpenAI",
                    "source_path": str(shot_path.resolve()),
                    "source_sha256": shot_sha,
                    "asset": "evidence/ev1.png",
                    "rendered": True,
                }
            ],
        },
    )
    write_json_atomic(
        run_dir / "render-info.json",
        {
            "status": "ok",
            "engine": "remotion",
            "renderer": "remotion",
            "subtitle_burned": "true",
            "subtitle_cue_count": str(len(cues)),
            "subtitle_contract_sha256": subtitle_sha,
            "tts": "edge-tts:zh-CN-XiaoxiaoNeural",
            "tts_backend": "edge",
            "tts_profile_publishable": "true",
            "manuscript_mode": "draft",
            "video": str(video_path.resolve()),
            "cover": str(cover_path.resolve()),
            "subtitles": str((run_dir / "subtitles.srt").resolve()),
        },
    )
    write_json_atomic(
        run_dir / "run-state.json",
        {
            "run_id": run_id,
            "run_date": "2026-11-18",
            "status": "QA_PASSED",
            "stages": [],
        },
    )
    manifest = {
        "run_id": run_id,
        "run_date": "2026-11-18",
        "publish_allowed": True,
        "provenance": {
            "git_sha": "deadbeef1234567890abcdef1234567890abcdef",
            "git_dirty": False,
        },
        "package_quality": {"ok": True, "edition_mode": "rolling_24h"},
        "automatic_quality_gate": {"ok": True, "status": "passed"},
        "counts": {
            "selected_cards": 1,
            "evidence_screenshots_missing_required": 0,
        },
        "selected": [
            {
                "entity": "OpenAI",
                "title": news_title,
                "editorial_tier": "headline",
                "risk": "green",
                "source_status": "official_confirmed",
                "story_spec": {
                    "story_id": "story-openai-52",
                    "claims": [
                        {
                            "claim_id": "claim-openai-52",
                            "text": news_narration,
                            "renderable": True,
                            "verifiable": True,
                            "evidence_urls": ["https://openai.com/index/gpt-5-2"],
                            "first_party_supported": True,
                        }
                    ],
                    "warnings": [],
                },
                "editorial_plan": {
                    "title": news_title,
                    "narration": news_narration,
                },
                "evidence": [
                    {
                        "source": "OpenAI",
                        "tier": "A",
                        "reliability": "official",
                        "title": news_title,
                        "url": "https://openai.com/index/gpt-5-2",
                        "screenshot_required": "true",
                        "screenshot_status": "captured",
                        "screenshot_path": "screenshots/ev1.png",
                        "screenshot_kind": "web_page",
                    }
                ],
            }
        ],
    }
    write_json_atomic(run_dir / "manifest.json", manifest)
    write_json_atomic(run_dir / "auto-quality.json", {"ok": True, "errors": []})
    record_render_contract(run_dir, timed_segments, manuscript_path=run_dir / "news-script.json")
    return manifest


class ClusterGateTests(unittest.TestCase):
    def test_is_concrete_public_ai_policy_positive_vs_corporate_and_partial(self) -> None:
        self.assertTrue(
            is_concrete_public_ai_policy(
                "工业和信息化部印发《人工智能赋能新型工业化行动方案（2026—2028）》",
                ["明确到 2028 年围绕行业大模型部署 8 项重点任务。"],
            )
        )
        # Corporate actor without public-sector agency issuer must be rejected
        self.assertFalse(
            is_concrete_public_ai_policy(
                "某科技公司发布企业内部人工智能治理行动方案（2026—2028）",
                "围绕大模型应用提出 6 项重点任务。",
            )
        )
        # Public issuer + AI term, but missing explicit period and concrete task structure
        self.assertFalse(
            is_concrete_public_ai_policy(
                "教育部发布人工智能通识教育指导意见",
                "鼓励高校持续探索大模型教学改革。",
            )
        )

    def test_verify_clusters_fresh_official_green_and_rejection_gates(self) -> None:
        fresh_item = _make_feed_item(published_at=FIXED_NOW - timedelta(hours=4))
        fresh_cluster = Cluster(
            key="c-fresh",
            title=fresh_item.title,
            items=[fresh_item],
            entity="OpenAI",
            published_at=fresh_item.published_at,
        )

        future_item = _make_feed_item(
            guid="guid-future",
            link="https://openai.com/index/future-post",
            published_at=FIXED_NOW + timedelta(hours=4),
        )
        future_cluster = Cluster(
            key="c-future",
            title=future_item.title,
            items=[future_item],
            entity="OpenAI",
            published_at=future_item.published_at,
        )

        undated_item = _make_feed_item(
            guid="guid-undated",
            link="https://openai.com/index/undated-post",
            published_at=None,
        )
        undated_cluster = Cluster(
            key="c-undated",
            title=undated_item.title,
            items=[undated_item],
            entity="OpenAI",
            published_at=None,
        )

        stale_reconciled_item = _make_feed_item(
            source_id="qbitai",
            source_name="量子位",
            source_tier="B",
            source_reliability="media",
            guid="guid-stale-rec",
            link="https://www.qbitai.com/2026/11/repost-openai.html",
            published_at=FIXED_NOW - timedelta(hours=2),
            raw={
                "official_reconciliation": {
                    "status": "matched",
                    "url": "https://openai.com/index/old-2025-release",
                    "freshness": "stale",
                    "published_at": (FIXED_NOW - timedelta(days=30)).isoformat(),
                }
            },
        )
        stale_reconciled_cluster = Cluster(
            key="c-stale-rec",
            title="OpenAI 发布旧版模型更新",
            items=[stale_reconciled_item],
            entity="OpenAI",
            published_at=stale_reconciled_item.published_at,
        )

        deadline_item = _make_feed_item(
            guid="guid-deadline",
            link="https://openai.com/index/grant-program",
            title="OpenAI 开放大模型安全研究基金 API 申请",
            summary="OpenAI 宣布开放大模型安全资助计划，报名截止 2026年5月1日，入选团队可获 API 额度。",
            published_at=FIXED_NOW - timedelta(hours=1),
        )
        deadline_cluster = Cluster(
            key="c-deadline",
            title=deadline_item.title,
            items=[deadline_item],
            entity="OpenAI",
            published_at=deadline_item.published_at,
        )

        cards = verify_clusters(
            [fresh_cluster, future_cluster, undated_cluster, stale_reconciled_cluster, deadline_cluster],
            now=FIXED_NOW,
            lookback_hours=24,
        )
        by_key = {card.cluster_key: card for card in cards}

        self.assertEqual(by_key["c-fresh"].risk, "green")
        self.assertTrue(by_key["c-fresh"].selected)
        self.assertEqual(by_key["c-fresh"].official_count, 1)

        self.assertEqual(by_key["c-future"].risk, "red")
        self.assertFalse(by_key["c-future"].selected)
        self.assertTrue(any("未来超过 2 小时" in u for u in by_key["c-future"].uncertainty))

        self.assertEqual(by_key["c-undated"].risk, "red")
        self.assertFalse(by_key["c-undated"].selected)
        self.assertIn("缺少可靠发布时间", by_key["c-undated"].reason)

        self.assertEqual(by_key["c-stale-rec"].risk, "red")
        self.assertFalse(by_key["c-stale-rec"].selected)
        self.assertIn("官方原始消息已过时", by_key["c-stale-rec"].reason)

        self.assertEqual(by_key["c-deadline"].risk, "red")
        self.assertFalse(by_key["c-deadline"].selected)
        self.assertIn("截止日期与来源发布时间冲突", by_key["c-deadline"].reason)

    def test_verify_clusters_isolates_article_excerpts_from_feed_summary(self) -> None:
        enriched_media = _make_feed_item(
            source_id="qbitai",
            source_name="量子位",
            source_tier="B",
            source_reliability="media",
            guid="guid-enriched",
            link="https://www.qbitai.com/2026/11/deepseek-v4.html",
            title="DeepSeek 更新 API 推理服务",
            summary="RSS 列表摘要：DeepSeek 今日宣布调整接口。",
            raw={
                "evidence_excerpts": [
                    {"text": "正文核验段落：DeepSeek API 缓存命中价格下调 50%，支持 128k 上下文。"}
                ]
            },
        )
        plain_media = _make_feed_item(
            source_id="infoq_cn",
            source_name="InfoQ",
            source_tier="B",
            source_reliability="media",
            guid="guid-plain",
            link="https://www.infoq.cn/article/deepseek-v4",
            title="DeepSeek 更新 API 推理服务",
            summary="仅来自 RSS 摘要的简短描述。",
            raw={},
        )
        cluster = Cluster(
            key="c-prov",
            title="DeepSeek 更新 API 推理服务",
            items=[enriched_media, plain_media],
            entity="DeepSeek",
            published_at=FIXED_NOW - timedelta(hours=2),
        )
        cards = verify_clusters([cluster], now=FIXED_NOW, lookback_hours=24)
        self.assertEqual(len(cards), 1)
        by_url = {ev["url"]: ev for ev in cards[0].evidence_links}

        ev_enriched = by_url["https://www.qbitai.com/2026/11/deepseek-v4.html"]
        self.assertEqual(ev_enriched["excerpt_provenance"], "article_excerpts")
        self.assertIn("缓存命中价格下调 50%", ev_enriched["excerpt"])
        self.assertEqual(ev_enriched["source_summary"], "RSS 列表摘要:DeepSeek 今日宣布调整接口。")
        self.assertEqual(ev_enriched["source_summary_provenance"], "feed_summary")

        ev_plain = by_url["https://www.infoq.cn/article/deepseek-v4"]
        self.assertEqual(ev_plain["excerpt_provenance"], "feed_summary")
        self.assertEqual(ev_plain["excerpt"], "仅来自 RSS 摘要的简短描述。")
        self.assertNotIn("source_summary", ev_plain)


class ContractAndAttestationTests(unittest.TestCase):
    def test_segments_fingerprint_is_timing_insensitive(self) -> None:
        untimed = [{"kind": "news", "story_id": "s1", "title": "标题", "text": "正文句。"}]
        timed = [
            {
                "kind": "news",
                "story_id": "s1",
                "title": "标题",
                "text": "正文句。",
                "start": 8.2,
                "end": 39.7,
                "duration": 31.5,
            }
        ]
        edited = [{"kind": "news", "story_id": "s1", "title": "标题", "text": "修改后的正文句。"}]

        self.assertEqual(segments_fingerprint(untimed), segments_fingerprint(timed))
        self.assertNotEqual(segments_fingerprint(untimed), segments_fingerprint(edited))

    def test_render_contract_lifecycle_mark_record_verify(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "2026-11-18"
            final_script_path = run_dir / "review" / "final-script.json"
            segments = [{"kind": "news", "story_id": "s1", "title": "OpenAI 更新", "text": "OpenAI 发布新接口。"}]
            write_json_atomic(final_script_path, {"version": 1, "segments": segments})

            pending = mark_render_required(run_dir, reason="manuscript_updated")
            self.assertTrue(pending.is_file())
            pre_errors = verify_review_render_contract(run_dir)
            self.assertTrue(any("pending a fresh render" in err for err in pre_errors))

            timed_script = [{**segments[0], "start": 0.0, "end": 18.0, "duration": 18.0}]
            write_json_atomic(run_dir / "script.json", timed_script)
            video_path = run_dir / "final.mp4"
            video_path.write_bytes(b"mp4-bytes")
            now_ts = time.time() + 1.0
            os.utime(video_path, (now_ts, now_ts))

            contract_path = record_render_contract(run_dir, timed_script)
            self.assertTrue(contract_path.is_file())
            self.assertFalse(pending.exists())
            self.assertEqual(verify_review_render_contract(run_dir), [])

    def test_agent_package_finalize_and_verify_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "2026-11-18"
            manifest = {
                "run_id": "2026-11-18-agent01",
                "run_date": "2026-11-18",
                "package_quality": {"ok": True},
                "source_coverage": {"x": {"required": False, "status": "not_requested"}},
                "selected": [
                    {
                        "entity": "OpenAI",
                        "title": "OpenAI 发布 GPT-5.2 模型",
                        "editorial_tier": "headline",
                        "risk": "green",
                        "source_status": "official_confirmed",
                        "story_spec": {
                            "story_id": "story-1",
                            "claims": [
                                {
                                    "claim_id": "claim-1",
                                    "text": "OpenAI 发布 GPT-5.2 模型，支持 128k token 上下文。",
                                    "renderable": True,
                                    "verifiable": True,
                                    "evidence_urls": ["https://openai.com/index/gpt-5-2"],
                                    "first_party_supported": True,
                                }
                            ],
                            "warnings": [],
                        },
                        "evidence": [
                            {
                                "source": "OpenAI",
                                "tier": "A",
                                "reliability": "official",
                                "title": "Introducing GPT-5.2",
                                "url": "https://openai.com/index/gpt-5-2",
                                "excerpt": "GPT-5.2 supports 128k token context.",
                            }
                        ],
                    }
                ],
            }
            write_json_atomic(run_dir / "manifest.json", manifest)

            initial_state = {
                "version": 1,
                "run_dir": str(run_dir.resolve()),
                "run_date": "2026-11-18",
                "segments": [
                    {
                        "kind": "intro",
                        "enabled": True,
                        "title": "AI 日报",
                        "headline": "AI 日报",
                        "caption": "今日共 1 条动态",
                        "text": "早上好，今天共 1 条 AI 动态。",
                        "cards": [],
                        "visual_pages": [],
                    },
                    {
                        "kind": "news",
                        "enabled": True,
                        "position": 1,
                        "total": 1,
                        "story_id": "story-1",
                        "claim_ids": ["claim-1"],
                        "generation_path": "structured_editorial_plan",
                        "editorial_tier": "headline",
                        "title": "OpenAI 发布 GPT-5.2 模型",
                        "headline": "OpenAI 发布 GPT-5.2 模型",
                        "caption": "OpenAI 发布 GPT-5.2 模型",
                        "text": "OpenAI 发布 GPT-5.2 模型，支持 128k token 上下文。",
                        "cards": [{"title": "模型规格", "body": "支持 128k token 上下文"}],
                        "visual_pages": [],
                    },
                    {
                        "kind": "outro",
                        "enabled": True,
                        "title": "播送完毕",
                        "headline": "播送完毕",
                        "caption": "明天见",
                        "text": "今天的新闻播完了，明天见。",
                        "cards": [],
                        "visual_pages": [],
                    },
                ],
            }
            write_json_atomic(run_dir / "review" / "draft-state.json", initial_state)
            write_json_atomic(run_dir / "review" / "state.json", initial_state)

            pkg = create_agent_package(run_dir)
            self.assertTrue(pkg["ok"])
            self.assertEqual(pkg["story_count"], 1)
            self.assertTrue(Path(pkg["agent_brief"]).is_file())

            audit = read_json(Path(pkg["audit_template"]))
            audit["source_review_passed"] = True
            audit["copy_review_passed"] = True
            audit["no_upload"] = True
            audit["stories"][0]["verdict"] = "approved"
            audit["stories"][0]["checked_urls"] = ["https://openai.com/index/gpt-5-2"]
            audit["stories"][0]["copy_checks"] = {k: True for k in audit["stories"][0]["copy_checks"]}
            write_json_atomic(Path(pkg["audit_output"]), audit)

            finalized = finalize_agent_edits(run_dir)
            self.assertTrue(finalized["ok"], finalized)
            self.assertEqual(verify_agent_edit_contract(run_dir), [])

            # Tampering with state.json after finalize must invalidate agent-contract.json
            tampered_state = read_json(run_dir / "review" / "state.json")
            tampered_state["segments"][0]["text"] = "早上好，未经重新签发定稿的改动。"
            write_json_atomic(run_dir / "review" / "state.json", tampered_state)
            tamper_errors = verify_agent_edit_contract(run_dir)
            self.assertTrue(any("state_fingerprint" in err for err in tamper_errors))

    def test_release_attestation_build_verify_and_tamper_detection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_repo = Path(tmp) / "repo"
            fake_repo.mkdir(parents=True, exist_ok=True)
            write_json_atomic(fake_repo / "sources.yaml", {"defaults": {"lookback_hours": 24}, "sources": []})

            run_dir = Path(tmp) / "runs" / "2026-11-18"
            _populate_valid_run_dir(run_dir)
            for rel in RELEASE_FILE_PATHS:
                self.assertTrue((run_dir / rel).is_file(), f"missing {rel}")

            def fake_git_output(cmd: list[str], **_kwargs) -> str:
                if "rev-parse" in cmd:
                    return "deadbeef1234567890abcdef1234567890abcdef\n"
                if "status" in cmd:
                    return ""
                return ""

            with (
                patch("briefing.verify.attestation.project_root", return_value=fake_repo),
                patch("briefing.verify.attestation.subprocess.check_output", side_effect=fake_git_output),
            ):
                attestation = build_release_attestation(run_dir)
                self.assertEqual(attestation["status"], "verified_clean_render")
                self.assertTrue(attestation["signature"])
                self.assertEqual(verify_release_attestation(run_dir), [])

                # Mutate a bound release artifact and confirm tamper detection triggers
                write_text_atomic(run_dir / "subtitles.srt", "1\n00:00:00,000 --> 00:00:02,000\ntampered\n")
                errors = verify_release_attestation(run_dir)
                self.assertIn("clean release attestation file changed: subtitles.srt", errors)


class OutputGateTests(unittest.TestCase):
    def test_parse_srt_and_repairable_news_positions(self) -> None:
        raw_srt = (
            "\ufeff1\n"
            "00:00:01,250 --> 00:00:04,750\n"
            "第一条字幕内容\n\n"
            "2\n"
            "00:00:05.000 --> 00:00:09.500\n"
            "第二条第一行\n第二条第二行\n"
        )
        cues = parse_srt(raw_srt)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0], {"index": 1, "start": 1.25, "end": 4.75, "duration": 3.5, "text": "第一条字幕内容"})
        self.assertEqual(cues[1]["duration"], 4.5)
        self.assertEqual(cues[1]["text"], "第二条第一行\n第二条第二行")

        script_payload = [
            {"kind": "intro", "position": 0},
            {"kind": "news", "position": 1},
            {"kind": "news", "position": 2},
            {"kind": "outro", "position": 0},
        ]
        errors = [
            "visible/public text contains an ellipsis at segment 1 text: ...",
            "news segment 2 contains low-information copy: 页面变化",
            "visible script text contains banned editorial phrase at segment 3 title: 重磅",
            "audio quality gate failed",
        ]
        self.assertEqual(repairable_news_positions(errors, script_payload), {1, 2})

    def _mock_ffprobe_ok(self, _args: list[str]) -> dict:
        return {
            "format": {"duration": "46.0"},
            "streams": [
                {"codec_type": "video", "pix_fmt": "yuv420p", "width": 1920, "height": 1080},
                {"codec_type": "audio", "sample_rate": "48000", "channels": 2},
            ],
        }

    def test_verify_run_dir_passes_clean_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "2026-11-18"
            _populate_valid_run_dir(run_dir)
            with (
                patch("briefing.verify.output_gate.shutil.which", return_value="ffprobe"),
                patch("briefing.verify.output_gate._ffprobe_json", side_effect=self._mock_ffprobe_ok),
            ):
                report = verify_run_dir(run_dir)
            self.assertTrue(report["ok"], report["errors"])
            self.assertEqual(report["errors"], [])

    def test_verify_run_dir_catches_copy_hygiene_screenshot_review_and_silent_tts_failures(self) -> None:
        # 1. Clickbait & ellipsis copy hygiene
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "2026-11-18"
            _populate_valid_run_dir(run_dir)
            bad_text = "重磅王炸！OpenAI 发布 GPT-5.2 模型接口升级，支持 128k token 上下文……。"
            script = read_json(run_dir / "script.json")
            script[1]["text"] = bad_text
            write_json_atomic(run_dir / "script.json", script)
            manuscript = read_json(run_dir / "news-script.json")
            manuscript["segments"][1]["text"] = bad_text
            write_json_atomic(run_dir / "news-script.json", manuscript)
            manifest = read_json(run_dir / "manifest.json")
            manifest["selected"][0]["editorial_plan"]["narration"] = bad_text
            write_json_atomic(run_dir / "manifest.json", manifest)

            with (
                patch("briefing.verify.output_gate.shutil.which", return_value="ffprobe"),
                patch("briefing.verify.output_gate._ffprobe_json", side_effect=self._mock_ffprobe_ok),
            ):
                report = verify_run_dir(run_dir)
            self.assertFalse(report["ok"])
            self.assertTrue(any("sensational copy" in e or "banned editorial phrase" in e for e in report["errors"]))
            self.assertTrue(any("ellipsis" in e for e in report["errors"]))

        # 2. Missing required screenshot
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "2026-11-18"
            _populate_valid_run_dir(run_dir)
            (run_dir / "screenshots" / "ev1.png").unlink()
            manifest = read_json(run_dir / "manifest.json")
            manifest["counts"]["evidence_screenshots_missing_required"] = 1
            manifest["selected"][0]["evidence"][0]["screenshot_status"] = "failed"
            write_json_atomic(run_dir / "manifest.json", manifest)

            with (
                patch("briefing.verify.output_gate.shutil.which", return_value="ffprobe"),
                patch("briefing.verify.output_gate._ffprobe_json", side_effect=self._mock_ffprobe_ok),
            ):
                report = verify_run_dir(run_dir)
            self.assertFalse(report["ok"])
            self.assertTrue(any("require source screenshots" in e for e in report["errors"]))
            self.assertTrue(any("requires screenshot but status is failed" in e for e in report["errors"]))

        # 3. Unrendered review changes
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "2026-11-18"
            _populate_valid_run_dir(run_dir)
            manuscript = read_json(run_dir / "news-script.json")
            manuscript["segments"][1]["text"] = "审稿台修改了口播正文，但尚未重新执行渲染。"
            reviewed_path = run_dir / "review" / "final-script.json"
            write_json_atomic(reviewed_path, manuscript)
            mark_render_required(run_dir, source_path=reviewed_path)

            with (
                patch("briefing.verify.output_gate.shutil.which", return_value="ffprobe"),
                patch("briefing.verify.output_gate._ffprobe_json", side_effect=self._mock_ffprobe_ok),
            ):
                report = verify_run_dir(run_dir)
            self.assertFalse(report["ok"])
            self.assertTrue(any("pending a fresh render" in e for e in report["errors"]))
            self.assertTrue(any("does not match script.json" in e for e in report["errors"]))

        # 4. Non-publishable silent TTS
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "2026-11-18"
            _populate_valid_run_dir(run_dir)
            render_info = read_json(run_dir / "render-info.json")
            render_info["tts"] = "silent"
            render_info["tts_backend"] = "silent"
            render_info["tts_profile_publishable"] = "false"
            write_json_atomic(run_dir / "render-info.json", render_info)

            with (
                patch("briefing.verify.output_gate.shutil.which", return_value="ffprobe"),
                patch("briefing.verify.output_gate._ffprobe_json", side_effect=self._mock_ffprobe_ok),
            ):
                report = verify_run_dir(run_dir)
            self.assertFalse(report["ok"])
            self.assertTrue(any("unapproved TTS fallback: silent" in e for e in report["errors"]))


class BilibiliAndDoctorTests(unittest.TestCase):
    def test_bilibili_client_hmac_sha256_v2_signing(self) -> None:
        creds = BiliCredentials(
            client_id="demo-id",
            client_secret="demo-secret",
            access_token="demo-token",
        )
        body = b'{"name":"final.mp4","utype":"1"}'
        headers = sign_headers(creds, body, timestamp=1700000000, nonce="nonce-001")

        expected_md5 = hashlib.md5(body).hexdigest()
        expected_canonical = (
            "x-bili-accesskeyid:demo-id\n"
            f"x-bili-content-md5:{expected_md5}\n"
            "x-bili-signature-method:HMAC-SHA256\n"
            "x-bili-signature-nonce:nonce-001\n"
            "x-bili-signature-version:2.0\n"
            "x-bili-timestamp:1700000000"
        )
        self.assertEqual(canonical_signing_string(headers), expected_canonical)
        expected_sig = hmac.new(b"demo-secret", expected_canonical.encode("utf-8"), hashlib.sha256).hexdigest()
        self.assertEqual(headers["Authorization"], expected_sig)
        self.assertEqual(headers["access-token"], "demo-token")

        session = MagicMock()
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"code": 0, "data": {"types": [{"id": 188}]}}
        session.request.return_value = mock_resp

        client = BilibiliClient(creds=creds, session=session)
        res = client.list_archive_types()
        self.assertEqual(res["code"], 0)
        sent_headers = session.request.call_args.kwargs["headers"]
        self.assertEqual(sent_headers["x-bili-signature-version"], "2.0")
        self.assertEqual(sent_headers["x-bili-signature-method"], "HMAC-SHA256")
        self.assertEqual(sent_headers["access-token"], "demo-token")

    def test_check_bilibili_auth_missing_credentials_success_and_api_error(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            missing = check_bilibili_auth()
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["reason"], "missing_credentials")

        ok_client = MagicMock()
        ok_client.list_archive_types.return_value = {"code": 0}
        with patch.dict(
            os.environ,
            {"BILI_TOKEN_ISSUED_AT": "2026-11-10T08:00:00+08:00", "BILI_TOKEN_TTL_DAYS": "30"},
            clear=False,
        ):
            authed = check_bilibili_auth(client=ok_client, now=FIXED_NOW)
        self.assertTrue(authed["ok"])
        self.assertTrue(authed["expiry"]["known"])

        bad_client = MagicMock()
        bad_client.list_archive_types.side_effect = BilibiliError("token expired")
        failed = check_bilibili_auth(client=bad_client, now=FIXED_NOW)
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["reason"], "api_error")

    def test_preflight_bilibili_reuses_verified_report_and_blocks_fixture_or_disallowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "2026-11-18"
            _populate_valid_run_dir(run_dir)
            verified_stub = {"ok": True, "errors": [], "warnings": []}

            with (
                patch.dict(
                    os.environ,
                    {
                        "BILI_CLIENT_ID": "id",
                        "BILI_CLIENT_SECRET": "sec",
                        "BILI_ACCESS_TOKEN": "tok",
                    },
                    clear=False,
                ),
                patch("briefing.publish.bilibili.verify_run_dir") as mock_verify,
            ):
                preflight = preflight_bilibili(run_dir, tid=188, verified_report=verified_stub)
                mock_verify.assert_not_called()

            self.assertTrue(preflight["ok"], preflight["errors"])
            self.assertTrue(preflight["ready_to_upload"])

            # Block QA fixtures and publish_allowed=False
            manifest = read_json(run_dir / "manifest.json")
            manifest["qa_fixture"] = True
            manifest["publish_allowed"] = False
            write_json_atomic(run_dir / "manifest.json", manifest)

            blocked = preflight_bilibili(run_dir, tid=188, verified_report=verified_stub)
            self.assertFalse(blocked["ok"])
            self.assertIn("QA fixture packages are never publishable", blocked["errors"])
            self.assertIn(
                "manifest publish_allowed is false; the rolling 24h package cannot be published",
                blocked["errors"],
            )

    def test_publish_bilibili_dry_run_and_interrupted_submitting_hard_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "2026-11-18"
            _populate_valid_run_dir(run_dir)
            dry = publish_bilibili(
                run_dir,
                tid=188,
                dry_run=True,
                verified_report={"ok": True, "errors": [], "warnings": []},
            )
            self.assertTrue(dry["dry_run"])
            self.assertTrue(dry["preflight"]["ok"])
            self.assertEqual(
                dry["planned_steps"],
                ["upload_cover", "init_video_upload", "upload_video", "submit_archive"],
            )

            # Simulate interrupted archive submission
            write_json_atomic(
                run_dir / "bilibili-upload-result.json",
                {"dry_run": False, "status": "submitting", "submit_response": None},
            )
            with self.assertRaises(BilibiliError) as ctx:
                publish_bilibili(
                    run_dir,
                    tid=188,
                    dry_run=False,
                    verified_report={"ok": True, "errors": [], "warnings": []},
                )
            self.assertIn("interrupted during archive submission", str(ctx.exception))

    def test_runtime_doctor_reports_prerequisites(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_repo = Path(tmp) / "repo"
            remotion_bin = fake_repo / "remotion" / "node_modules" / ".bin" / ("remotion.cmd" if os.name == "nt" else "remotion")
            remotion_bin.parent.mkdir(parents=True, exist_ok=True)
            remotion_bin.write_text("echo ok\n", encoding="utf-8")
            browser_bin = fake_repo / "chrome.exe"
            browser_bin.write_bytes(b"exe")
            cfg_path = fake_repo / "sources.public-web.json"
            write_json_atomic(
                cfg_path,
                {
                    "profile": "public_web",
                    "sources": [
                        {
                            "id": "openai_news",
                            "name": "OpenAI",
                            "tier": "A",
                            "type": "rss",
                            "region": "global",
                            "url": "https://openai.com/news/rss.xml",
                            "enabled": True,
                            "reliability": "official",
                        }
                    ],
                },
            )

            with (
                patch("briefing.publish.doctor.project_root", return_value=fake_repo),
                patch("briefing.publish.doctor.shutil.which", return_value="mock-tool"),
                patch("briefing.publish.doctor._probe", return_value=True),
                patch("briefing.publish.doctor._find_browser_executable", return_value=str(browser_bin)),
                patch("briefing.publish.doctor.importlib.util.find_spec", return_value=object()),
                patch.dict(os.environ, {"BRIEFING_AUTO_PUBLISH": "0"}, clear=False),
            ):
                report = runtime_doctor(cfg_path)

            self.assertTrue(report["ok"], report["checks"])
            self.assertEqual(report["profile"], "public_web")
            self.assertEqual(report["source_count"], 1)
            self.assertEqual(report["crawl_channel"], "chrome")
            self.assertFalse(report["publishing_enabled"])

    def test_automation_status_stages_and_unified_cli_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_repo = Path(tmp) / "repo"
            cfg_path = fake_repo / "sources.public-web.json"
            write_json_atomic(cfg_path, {"profile": "public_web", "sources": []})
            runs_root = Path(tmp) / "runs"

            with patch("briefing.publish.doctor.project_root", return_value=fake_repo):
                # 1. new
                today_dir = runs_root / "2026-11-18"
                st_new = automation_status(today_dir, config_path=cfg_path, today="2026-11-18")
                self.assertEqual(st_new["stage"], "new")
                self.assertTrue(st_new["ok"])
                self.assertFalse(st_new["blocked"])
                self.assertIn("py -3 -m briefing prepare --date 2026-11-18", st_new["next_command"])

                # 2. wrong-date blocked
                wrong_dir = runs_root / "2026-11-17"
                st_wrong = automation_status(wrong_dir, config_path=cfg_path, today="2026-11-18")
                self.assertEqual(st_wrong["stage"], "blocked")
                self.assertFalse(st_wrong["ok"])
                self.assertTrue(st_wrong["blocked"])

                # 3. submission_unknown
                today_dir.mkdir(parents=True, exist_ok=True)
                write_json_atomic(
                    today_dir / "bilibili-upload-result.json",
                    {"dry_run": False, "status": "submitting"},
                )
                st_unknown = automation_status(today_dir, config_path=cfg_path, today="2026-11-18")
                self.assertEqual(st_unknown["stage"], "submission_unknown")
                self.assertFalse(st_unknown["ok"])
                self.assertTrue(st_unknown["blocked"])

                # 4. submitted
                write_json_atomic(
                    today_dir / "bilibili-upload-result.json",
                    {"dry_run": False, "status": "submitted"},
                )
                st_done = automation_status(today_dir, config_path=cfg_path, today="2026-11-18")
                self.assertEqual(st_done["stage"], "submitted")
                self.assertTrue(st_done["ok"])
                self.assertFalse(st_done["blocked"])
                self.assertEqual(st_done["next_command"], "")

                # 5. ready -> unified publish command
                (today_dir / "bilibili-upload-result.json").unlink()
                _populate_valid_run_dir(today_dir)
                with patch("briefing.publish.doctor.verify_run_dir", return_value={"ok": True, "errors": []}):
                    st_ready = automation_status(today_dir, config_path=cfg_path, today="2026-11-18")
                self.assertEqual(st_ready["stage"], "ready")
                self.assertIn("py -3 -m briefing publish --run-dir", st_ready["next_command"])


if __name__ == "__main__":
    unittest.main()

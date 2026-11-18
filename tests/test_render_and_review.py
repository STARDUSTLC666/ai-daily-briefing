from __future__ import annotations

import base64
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from briefing.core import UTC, EvidenceCard, file_sha256, read_json, write_json_atomic
from briefing.render import (
    build_remotion_slides,
    build_segments,
    copy_evidence_assets,
    load_cover_copy,
    split_tts_text,
    write_srt,
    write_subtitle_ass,
    write_wechat_article,
)
from briefing.review import (
    create_review_package,
    create_review_server,
    load_cover_state,
    review_cover_path,
    review_dir,
    save_cover_state,
    write_final_script_from_state,
)

FIXED_NOW = datetime(2026, 11, 18, 2, 0, 0, tzinfo=UTC)

# Minimal valid 1x1 transparent PNG
ONE_BY_ONE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _make_official_card(
    cluster_key: str = "c-openai",
    title: str = "OpenAI 发布 GPT-5.2 模型并开放 API 工具调用",
    entity: str = "OpenAI",
    url: str = "https://openai.com/index/gpt-5-2",
    tier: str = "headline",
) -> EvidenceCard:
    fact = "OpenAI 正式发布 GPT-5.2 模型，支持 128k token 上下文窗口，API 调用延迟降低 30%。"
    return EvidenceCard(
        cluster_key=cluster_key,
        event_title=title,
        entity=entity,
        risk="green",
        confidence=92,
        selected=True,
        reason="官方来源确认",
        source_count=1,
        official_count=1,
        media_count=0,
        community_count=0,
        first_seen_at=FIXED_NOW - timedelta(hours=2),
        latest_published_at=FIXED_NOW - timedelta(hours=2),
        key_facts=[fact],
        evidence_links=[
            {
                "source": entity,
                "tier": "A",
                "reliability": "official",
                "title": title,
                "url": url,
                "excerpt": fact,
                "excerpt_provenance": "official_source_summary",
            }
        ],
        uncertainty=[],
        score=88.0,
        editorial_tier=tier,
    )


def _make_personnel_signal_card(shot_path: Path) -> EvidenceCard:
    fact = "Sam Altman 在 X 发帖透露 Codex 智能体即将支持并行沙箱调试能力。"
    return EvidenceCard(
        cluster_key="c-sama-signal",
        event_title="Sam Altman 透露 Codex 智能体并行调试能力",
        entity="OpenAI",
        risk="yellow",
        confidence=72,
        selected=True,
        reason="X 官方人员动态",
        source_count=1,
        official_count=0,
        media_count=0,
        community_count=1,
        first_seen_at=FIXED_NOW - timedelta(hours=1),
        latest_published_at=FIXED_NOW - timedelta(hours=1),
        key_facts=[fact],
        evidence_links=[
            {
                "source": "X / @sama",
                "tier": "C",
                "reliability": "official_personnel",
                "title": "Codex update thread",
                "url": "https://x.com/sama/status/1880000000000000001",
                "excerpt": fact,
                "excerpt_provenance": "verified_social_post",
                "screenshot_required": "true",
                "screenshot_status": "captured",
                "screenshot_path": str(shot_path),
                "screenshot_kind": "x_post",
            }
        ],
        uncertainty=["一线消息，不等同于正式发布。"],
        score=68.0,
        editorial_tier="brief",
    )


class RenderModuleTests(unittest.TestCase):
    def test_build_segments_real_tagline_structured_plan_and_disclaimer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            shot = Path(tmp) / "sama.png"
            shot.write_bytes(ONE_BY_ONE_PNG)

            lead = _make_official_card()
            signal = _make_personnel_signal_card(shot)
            segments = build_segments([lead, signal], run_date="2026-11-18")

            intro = segments[0]
            self.assertEqual(intro["kind"], "intro")
            self.assertNotEqual(intro["caption"], "AI 日报")
            self.assertIn("经核验 AI 动态", intro["caption"])

            # Even if an upstream brief passes literal "AI 日报", _resolve_tagline must reject it
            fallback_segs = build_segments([lead], run_date="2026-11-18", edition_brief={"tagline": "AI 日报"})
            self.assertNotEqual(fallback_segs[0]["caption"], "AI 日报")
            self.assertTrue(len(fallback_segs[0]["caption"]) > 6)

            news_segs = [s for s in segments if s["kind"] == "news"]
            self.assertEqual(len(news_segs), 2)
            for seg in news_segs:
                self.assertEqual(seg["generation_path"], "structured_editorial_plan")
                self.assertTrue(seg["story_id"])
                self.assertTrue(seg["claim_ids"])

            personnel_seg = news_segs[1]
            self.assertEqual(personnel_seg["signal_kind"], "official_personnel")
            self.assertIn("一线消息", personnel_seg["disclaimer"])
            self.assertIn("未获官方公告确认", personnel_seg["disclaimer"])
            self.assertIn("一线消息", personnel_seg["text"])
            self.assertIn("未获官方公告确认", personnel_seg["text"])
            card_bodies = " ".join(c.get("body", "") for c in personnel_seg["cards"])
            self.assertIn("未获官方公告确认", card_bodies)

    def test_build_remotion_slides_maps_timings_and_retains_required_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            shot = Path(tmp) / "sama.png"
            shot.write_bytes(ONE_BY_ONE_PNG)
            segments = build_segments([_make_official_card(), _make_personnel_signal_card(shot)], run_date="2026-11-18")
            # Give the personnel segment a short duration (<10.5s) to verify required evidence page is still kept
            durations = [6.0, 20.0, 8.0, 5.0]
            slides = build_remotion_slides(segments, durations, width=1920, height=1080, run_label="2026-11-18")

            self.assertGreaterEqual(len(slides), 5)
            self.assertEqual(slides[0]["kind"], "intro")
            self.assertEqual(slides[0]["timelineTotal"], 39.0)
            self.assertEqual(len(slides[0]["timelineItems"]), 4)

            seg2_slides = [s for s in slides if s["segmentIndex"] == 2]
            self.assertTrue(any(s["page"].get("kind") == "evidence" for s in seg2_slides))

    def test_write_srt_enforces_max_8s_and_non_overlapping_cues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            srt_path = Path(tmp) / "subtitles.srt"
            segments = [
                {"kind": "intro", "text": "早上好，这里是 AI 日报，今天我们来看两条最新的一手技术动态。"},
                {
                    "kind": "news",
                    "text": (
                        "OpenAI 正式发布 GPT-5.2 模型并开放多步工具调用接口，上下文扩展到十二万八千词元且推理延迟降低百分之三十"
                    ),
                },
            ]
            # Segment 2 spans 26s without sentence-ending punctuation so _cap_cue_duration must split <=8s
            cues = write_srt(segments, [7.0, 26.0], srt_path)
            self.assertTrue(srt_path.is_file())
            self.assertGreaterEqual(len(cues), 4)

            for idx, cue in enumerate(cues):
                self.assertGreater(cue["end"], cue["start"])
                self.assertLessEqual(cue["duration"], 8.0)
                if idx > 0:
                    self.assertGreaterEqual(cue["start"], cues[idx - 1]["end"] - 1e-6)

    def test_write_subtitle_ass_scales_margin_v_92_at_1080p_and_184_at_4k(self) -> None:
        cues = [{"index": 1, "start": 0.0, "end": 3.5, "duration": 3.5, "text": "第一条测试字幕"}]
        with tempfile.TemporaryDirectory() as tmp:
            ass_1080 = write_subtitle_ass(cues, Path(tmp) / "sub-1080p.ass", width=1920, height=1080)
            text_1080 = ass_1080.read_text(encoding="utf-8")
            self.assertIn("PlayResX: 1920", text_1080)
            self.assertIn("PlayResY: 1080", text_1080)
            style_1080 = next(line for line in text_1080.splitlines() if line.startswith("Style: Subtitle,"))
            # Last 5 fields: Alignment,MarginL,MarginR,MarginV,Encoding -> 2,160,160,92,1
            self.assertTrue(style_1080.endswith(",2,160,160,92,1"), style_1080)

            ass_4k = write_subtitle_ass(cues, Path(tmp) / "sub-4k.ass", width=3840, height=2160)
            text_4k = ass_4k.read_text(encoding="utf-8")
            self.assertIn("PlayResX: 3840", text_4k)
            self.assertIn("PlayResY: 2160", text_4k)
            style_4k = next(line for line in text_4k.splitlines() if line.startswith("Style: Subtitle,"))
            self.assertTrue(style_4k.endswith(",2,320,320,184,1"), style_4k)

    def test_split_tts_text_merges_short_sentences_and_splits_long_clauses(self) -> None:
        self.assertEqual(split_tts_text(""), [])
        short_merged = split_tts_text("早上好。今天看三条动态。", max_chars=48)
        self.assertEqual(short_merged, ["早上好。今天看三条动态。"])

        long_clause = (
            "OpenAI 今日面向全球开发者正式推出新一代多模态推理模型，"
            "支持十二万八千词元上下文输入，同时将接口响应延迟降低了百分之三十五，"
            "并同步更新了批处理工具链与实时函数调用规范。"
        )
        chunks = split_tts_text(long_clause, max_chars=45)
        self.assertGreaterEqual(len(chunks), 2)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 45)
        self.assertEqual("".join(chunks), long_clause)

    def test_load_cover_copy_uses_lead_story_titles_zero_and_enforces_story_count(self) -> None:
        slides = [
            {
                "index": 0,
                "kind": "intro",
                "title": "AI 日报",
                "timelineItems": [
                    {"kind": "news", "entity": "OpenAI"},
                    {"kind": "news", "entity": "Anthropic"},
                    {"kind": "news", "entity": "DeepSeek"},
                ],
            },
            {"index": 1, "kind": "news", "storyPosition": 1, "title": "首条重磅：OpenAI 发布 GPT-5.2"},
            {"index": 2, "kind": "news", "storyPosition": 2, "title": "第二条：Anthropic 更新 Claude 接口"},
            {"index": 3, "kind": "news", "storyPosition": 3, "title": "尾条动态：DeepSeek 下调缓存价格"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "2026-11-18"
            cover = load_cover_copy(run_dir, slides)
            self.assertEqual(cover["headline"], "首条重磅：OpenAI 发布 GPT-5.2")
            self.assertNotEqual(cover["headline"], "尾条动态：DeepSeek 下调缓存价格")
            self.assertEqual(cover["subheadline"], "第二条：Anthropic 更新 Claude 接口")
            self.assertEqual(cover["storyCount"], 3)
            self.assertTrue((run_dir / "review" / "cover.json").is_file())

            # Mismatched storyCount in review/cover.json must raise ValueError
            bad_cover = read_json(run_dir / "review" / "cover.json")
            bad_cover["storyCount"] = 9
            write_json_atomic(run_dir / "review" / "cover.json", bad_cover)
            with self.assertRaises(ValueError) as ctx:
                load_cover_copy(run_dir, slides)
            self.assertIn("storyCount", str(ctx.exception))

    def test_copy_evidence_assets_stages_hashed_png_and_writes_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "2026-11-18"
            render_dir = run_dir / "render"
            shot = run_dir / "screenshots" / "ev1.png"
            shot.parent.mkdir(parents=True, exist_ok=True)
            shot.write_bytes(ONE_BY_ONE_PNG)
            expected_sha = file_sha256(shot)

            slides = [
                {
                    "index": 2,
                    "segmentIndex": 1,
                    "kind": "news",
                    "page": {
                        "kind": "evidence",
                        "evidenceVisual": {
                            "source": "OpenAI",
                            "image": "screenshots/ev1.png",
                            "required": True,
                        },
                    },
                }
            ]
            contract = copy_evidence_assets(run_dir, render_dir, slides)
            expected_rel = f"evidence/{expected_sha[:16]}.png"
            self.assertEqual(slides[0]["page"]["evidenceVisual"]["asset"], expected_rel)
            self.assertTrue((render_dir / "remotion-public" / expected_rel).is_file())
            self.assertEqual(len(contract["items"]), 1)
            self.assertEqual(contract["items"][0]["source_sha256"], expected_sha)
            self.assertTrue((render_dir / "evidence-visual-contract.json").is_file())

            # Missing required screenshot must raise RuntimeError
            missing_slides = [
                {
                    "index": 3,
                    "segmentIndex": 1,
                    "kind": "news",
                    "page": {
                        "kind": "evidence",
                        "evidenceVisual": {"source": "OpenAI", "image": "screenshots/missing.png", "required": True},
                    },
                }
            ]
            with self.assertRaises(RuntimeError):
                copy_evidence_assets(run_dir, render_dir, missing_slides)

    def test_write_wechat_article_renders_markdown_with_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "2026-11-18"
            write_json_atomic(run_dir / "bilibili.json", {"title": "OpenAI 等 1 条动态【AI 日报 2026-11-18】"})
            write_json_atomic(
                run_dir / "script.json",
                [
                    {"kind": "intro", "text": "早上好，今天共 1 条 AI 动态。"},
                    {
                        "kind": "news",
                        "title": "OpenAI 发布 GPT-5.2 接口升级",
                        "text": "OpenAI 发布 GPT-5.2 接口升级，支持 128k 上下文。",
                        "cards": [{"title": "原文与时间", "body": "OpenAI｜11月18日 10:00 发布｜官方来源"}],
                    },
                    {"kind": "outro", "text": "我们明天见。"},
                ],
            )
            article_path = write_wechat_article(run_dir)
            content = article_path.read_text(encoding="utf-8")
            self.assertIn("# OpenAI 等 1 条动态【AI 日报 2026-11-18】", content)
            self.assertIn("## 01 OpenAI 发布 GPT-5.2 接口升级", content)
            self.assertIn("> 来源：OpenAI|11月18日 10:00 发布|官方来源", content)


class ReviewModuleAndServerTests(unittest.TestCase):
    def test_create_review_package_and_write_final_script_reinjects_required_screenshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "2026-11-18"
            shot = run_dir / "screenshots" / "mandatory.png"
            shot.parent.mkdir(parents=True, exist_ok=True)
            shot.write_bytes(ONE_BY_ONE_PNG)

            card = _make_official_card()
            pkg = create_review_package(run_dir, [card], run_date="2026-11-18")
            self.assertEqual(pkg["status"], "created")
            pkg_again = create_review_package(run_dir, [card], run_date="2026-11-18", force=False)
            self.assertEqual(pkg_again["status"], "exists")

            state = read_json(Path(pkg["state"]))
            news_row = next(s for s in state["segments"] if s["kind"] == "news")
            story_id = news_row["story_id"]

            # Declare a required captured screenshot in manifest.json, then clear images in edited state
            write_json_atomic(
                run_dir / "manifest.json",
                {
                    "run_date": "2026-11-18",
                    "selected": [
                        {
                            "title": news_row["title"],
                            "story_spec": {"story_id": story_id},
                            "evidence": [
                                {
                                    "source": "OpenAI",
                                    "title": "Mandatory Official Capture",
                                    "url": "https://openai.com/index/gpt-5-2",
                                    "screenshot_required": "true",
                                    "screenshot_status": "captured",
                                    "screenshot_path": "screenshots/mandatory.png",
                                }
                            ],
                        }
                    ],
                },
            )
            news_row["images"] = []

            saved = write_final_script_from_state(run_dir, state)
            self.assertEqual(saved["news_count"], 1)
            self.assertTrue((review_dir(run_dir) / "render-required.json").is_file())

            persisted_state = read_json(Path(saved["state_path"]))
            persisted_news = next(s for s in persisted_state["segments"] if s["kind"] == "news")
            self.assertEqual(len(persisted_news["images"]), 1)
            self.assertTrue(persisted_news["images"][0]["required"])
            self.assertTrue(persisted_news["images"][0]["enabled"])

            final_doc = read_json(Path(saved["final_script"]))
            final_news = next(s for s in final_doc["segments"] if s["kind"] == "news")
            ev_pages = [p for p in final_news["visual_pages"] if p.get("kind") == "evidence"]
            self.assertEqual(len(ev_pages), 1)
            self.assertEqual(Path(ev_pages[0]["evidenceVisual"]["image"]).resolve(), shot.resolve())

    def test_load_and_save_cover_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "2026-11-18"
            c1 = _make_official_card(cluster_key="c1", title="首条新闻：OpenAI 发布 GPT-5.2", entity="OpenAI")
            c2 = _make_official_card(
                cluster_key="c2",
                title="第二条新闻：Anthropic 发布 Claude 4.5",
                entity="Anthropic",
                url="https://www.anthropic.com/news/claude-4-5",
                tier="brief",
            )
            create_review_package(run_dir, [c1, c2], run_date="2026-11-18")

            default_cover = load_cover_state(run_dir)
            self.assertEqual(default_cover["storyCount"], 2)
            self.assertIn("OpenAI", default_cover["headline"])
            self.assertNotIn("Anthropic", default_cover["headline"])

            with self.assertRaises(ValueError):
                save_cover_state(run_dir, {"headline": "   "})

            updated = save_cover_state(
                run_dir,
                {
                    "headline": "自定义主标题：GPT-5.2 深度拆解",
                    "highlights": ["128k 上下文", "API 降价 30%"],
                },
            )
            self.assertEqual(updated["headline"], "自定义主标题：GPT-5.2 深度拆解")
            self.assertEqual(updated["storyCount"], 2)
            self.assertTrue(review_cover_path(run_dir).is_file())
            marker = read_json(review_dir(run_dir) / "render-required.json")
            self.assertEqual(marker["reason"], "cover_updated")

    def test_ephemeral_review_http_server_endpoints_csrf_origin_and_media_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            run_dir = base / "2026-11-18"
            outside_secret = base / "outside-secret.png"
            outside_secret.write_bytes(ONE_BY_ONE_PNG)

            inside_img = run_dir / "screenshots" / "inside.png"
            inside_img.parent.mkdir(parents=True, exist_ok=True)
            inside_img.write_bytes(ONE_BY_ONE_PNG)

            create_review_package(run_dir, [_make_official_card()], run_date="2026-11-18")

            with patch.dict(os.environ, {"BRIEFING_REVIEW_QUIET": "1"}, clear=False):
                server = create_review_server(run_dir, host="127.0.0.1", port=0)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    host, port = server.server_address[:2]
                    base_url = f"http://{host}:{port}"

                    # GET / -> semantic HTML + injected token
                    with urlopen(f"{base_url}/", timeout=5) as resp:
                        self.assertEqual(resp.status, 200)
                        html = resp.read().decode("utf-8")
                    self.assertIn('<meta name="color-scheme" content="light dark">', html)
                    self.assertIn("<search", html)
                    self.assertIn("<dialog", html)
                    self.assertIn("window.__REVIEW_TOKEN__", html)
                    token_match = re.search(r'window\.__REVIEW_TOKEN__\s*=\s*"([0-9a-f]+)"', html)
                    self.assertIsNotNone(token_match)
                    token = token_match.group(1)
                    self.assertEqual(token, server.auth_token)

                    # GET /assets/styles.css & /assets/app.js
                    with urlopen(f"{base_url}/assets/styles.css", timeout=5) as resp:
                        self.assertEqual(resp.status, 200)
                        self.assertIn("text/css", resp.headers.get("Content-Type", ""))
                    with urlopen(f"{base_url}/assets/app.js", timeout=5) as resp:
                        self.assertEqual(resp.status, 200)
                        self.assertIn("javascript", resp.headers.get("Content-Type", ""))

                    # GET /api/state
                    with urlopen(f"{base_url}/api/state", timeout=5) as resp:
                        self.assertEqual(resp.status, 200)
                        state_payload = json.loads(resp.read().decode("utf-8"))
                    self.assertEqual(state_payload["run_date"], "2026-11-18")

                    body_bytes = json.dumps(state_payload, ensure_ascii=False).encode("utf-8")

                    # POST /api/save without X-Review-Token -> 403
                    req_no_tok = Request(
                        f"{base_url}/api/save",
                        data=body_bytes,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with self.assertRaises(HTTPError) as err_no_tok:
                        urlopen(req_no_tok, timeout=5)
                    self.assertEqual(err_no_tok.exception.code, 403)
                    err_no_tok.exception.close()

                    # POST /api/save with valid token + non-loopback Origin -> 403
                    req_bad_origin = Request(
                        f"{base_url}/api/save",
                        data=body_bytes,
                        headers={
                            "Content-Type": "application/json",
                            "X-Review-Token": token,
                            "Origin": "https://attacker.example.com",
                        },
                        method="POST",
                    )
                    with self.assertRaises(HTTPError) as err_origin:
                        urlopen(req_bad_origin, timeout=5)
                    self.assertEqual(err_origin.exception.code, 403)
                    err_origin.exception.close()

                    # POST /api/save with valid token + loopback Origin -> 200
                    req_valid = Request(
                        f"{base_url}/api/save",
                        data=body_bytes,
                        headers={
                            "Content-Type": "application/json",
                            "X-Review-Token": token,
                            "Origin": base_url,
                        },
                        method="POST",
                    )
                    with urlopen(req_valid, timeout=5) as resp:
                        self.assertEqual(resp.status, 200)
                        save_res = json.loads(resp.read().decode("utf-8"))
                    self.assertEqual(save_res["news_count"], 1)
                    self.assertTrue((run_dir / "review" / "final-script.json").is_file())

                    # POST /api/cover with valid token -> 200
                    cover_req = Request(
                        f"{base_url}/api/cover",
                        data=json.dumps({"headline": "工作台保存的封面主标题"}, ensure_ascii=False).encode("utf-8"),
                        headers={
                            "Content-Type": "application/json",
                            "X-Review-Token": token,
                            "Origin": base_url,
                        },
                        method="POST",
                    )
                    with urlopen(cover_req, timeout=5) as resp:
                        self.assertEqual(resp.status, 200)
                        cover_res = json.loads(resp.read().decode("utf-8"))
                    self.assertTrue(cover_res["ok"])
                    self.assertEqual(cover_res["cover"]["headline"], "工作台保存的封面主标题")

                    # GET /media inside run_dir -> 200, path traversal outside run_dir -> 404
                    with urlopen(f"{base_url}/media?path={quote('screenshots/inside.png')}", timeout=5) as resp:
                        self.assertEqual(resp.status, 200)
                        self.assertEqual(resp.read(), ONE_BY_ONE_PNG)

                    for blocked_param in ("../outside-secret.png", str(outside_secret)):
                        with self.assertRaises(HTTPError) as err_media:
                            urlopen(f"{base_url}/media?path={quote(blocked_param, safe='')}", timeout=5)
                        self.assertEqual(err_media.exception.code, 404)
                        err_media.exception.close()
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()

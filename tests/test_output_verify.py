import tempfile
import unittest
import json
from pathlib import Path

from briefing.output_verify import FORBIDDEN_SCRIPT_PHRASES, _automatic_news_content_errors, _evidence_visual_coverage_errors, _looks_mojibake, _parse_mmss_lines, _signal_segment_errors, parse_srt, repairable_news_positions, verify_run_dir


class OutputVerifyTests(unittest.TestCase):
    def test_preview_version_is_a_valid_public_fact(self):
        self.assertNotIn("预览", FORBIDDEN_SCRIPT_PHRASES)

    def test_parse_srt(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "subtitles.srt"
            p.write_text(
                "1\n00:00:00,000 --> 00:00:01,500\n你好\n\n2\n00:00:01,500 --> 00:00:03,000\n世界\n",
                encoding="utf-8-sig",
            )
            rows = parse_srt(p)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0][0], 0)
            self.assertAlmostEqual(rows[1][1], 3.0)

    def test_rolling_video_requires_recurring_evidence_popups(self):
        news = [
            {
                "kind": "news",
                "visual_pages": [
                    {
                        "kind": "evidence",
                        "evidenceVisual": {"image": f"source-{index}.png"},
                    }
                ] if index < 2 else [],
            }
            for index in range(12)
        ]

        self.assertEqual(_evidence_visual_coverage_errors(news), [])
        news[1]["visual_pages"] = []
        self.assertIn("requires at least 2", _evidence_visual_coverage_errors(news)[0])

    def test_parse_navigation_times(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "pinned-comment.md"
            p.write_text("今日时间导航：\n00:00 开场\n01:23 新闻\n", encoding="utf-8-sig")
            rows = _parse_mmss_lines(p)
            self.assertEqual(rows[0][0], 0)
            self.assertEqual(rows[1][0], 83)

    def test_manifest_quality_checks_do_not_enforce_count(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "counts": {
                            "selected_cards": 1,
                            "candidate_cards": 4,
                            "evidence_screenshots_missing_required": 1,
                        },
                        "freshness_window": {"lookback_hours": 24},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8-sig",
            )
            result = verify_run_dir(root)
            self.assertNotIn("selected main video cards below 4", "\n".join(result["errors"]))
            self.assertIn("require source screenshots", "\n".join(result["errors"]))
            self.assertEqual(result["freshness_window"]["lookback_hours"], 24)

    def test_remotion_subtitle_hash_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "subtitles.srt").write_text("1\n00:00:00,000 --> 00:00:02,000\n完整字幕\n", encoding="utf-8-sig")
            (root / "render-info.json").write_text(
                json.dumps({"renderer": "remotion", "subtitle_burned": "true", "subtitle_cue_count": "1", "subtitle_contract_sha256": "wrong"}),
                encoding="utf-8-sig",
            )

            result = verify_run_dir(root)

            self.assertIn("subtitle contract hash", "\n".join(result["errors"]))

    def test_claim_contract_rejects_legacy_or_missing_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "manifest.json").write_text(
                json.dumps({"selected": [{"story_spec": {"story_id": "story-1", "claims": []}}]}, ensure_ascii=False),
                encoding="utf-8-sig",
            )
            (root / "script.json").write_text(
                json.dumps([{"kind": "news", "story_id": "story-1", "claim_ids": [], "generation_path": "legacy_fallback", "text": "旁白"}], ensure_ascii=False),
                encoding="utf-8-sig",
            )

            result = verify_run_dir(root)

            self.assertIn("legacy_fallback", "\n".join(result["errors"]))

    def test_claim_contract_accepts_manifest_backed_claim(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            claim = {"claim_id": "claim-1", "renderable": True, "verifiable": True, "evidence_urls": ["https://example.com/source"]}
            (root / "manifest.json").write_text(
                json.dumps({"selected": [{"story_spec": {"story_id": "story-1", "claims": [claim]}}]}, ensure_ascii=False),
                encoding="utf-8-sig",
            )
            (root / "script.json").write_text(
                json.dumps([{"kind": "news", "story_id": "story-1", "claim_ids": ["claim-1"], "generation_path": "structured_editorial_plan", "text": "旁白"}], ensure_ascii=False),
                encoding="utf-8-sig",
            )

            result = verify_run_dir(root)

            self.assertNotIn("claim_id", "\n".join(result["errors"]))
            self.assertNotIn("structured claim-evidence", "\n".join(result["errors"]))

    def test_local_script_copy_must_match_manifest_editorial_plan(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            claim = {
                "claim_id": "claim-1",
                "renderable": True,
                "verifiable": True,
                "evidence_urls": ["https://example.com/source"],
            }
            manifest = {
                "selected": [
                    {
                        "story_spec": {"story_id": "story-1", "claims": [claim]},
                        "editorial_plan": {"title": "已核实标题", "narration": "已核实旁白。"},
                    }
                ]
            }
            (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            (root / "script.json").write_text(
                json.dumps(
                    [
                        {
                            "kind": "news",
                            "story_id": "story-1",
                            "claim_ids": ["claim-1"],
                            "generation_path": "structured_editorial_plan",
                            "title": "被篡改标题",
                            "text": "被篡改旁白。",
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            errors = "\n".join(verify_run_dir(root)["errors"])

        self.assertIn("narration does not match", errors)
        self.assertIn("title does not match", errors)

    def test_sparse_subtitles_fail_publish_verification(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "manifest.json").write_text(
                json.dumps({"counts": {"selected_cards": 3, "candidate_cards": 3}}, ensure_ascii=False),
                encoding="utf-8-sig",
            )
            (root / "subtitles.srt").write_text(
                "1\n00:00:00,000 --> 00:00:20,000\n第一条\n\n2\n00:00:20,000 --> 00:00:40,000\n第二条\n",
                encoding="utf-8-sig",
            )
            result = verify_run_dir(root)
            joined = "\n".join(result["errors"])
            self.assertIn("too sparse", joined)
            self.assertIn("longer than 8 seconds", joined)

    def test_raw_english_visible_script_text_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "manifest.json").write_text(json.dumps({"counts": {"selected_cards": 1}}, ensure_ascii=False), encoding="utf-8-sig")
            (root / "script.json").write_text(
                json.dumps(
                    [
                        {
                            "kind": "news",
                            "title": "Qwen 3.6 27B - VLLM Performance Benchmark Results (BF16, FP8, NVFP4)",
                            "caption": "中文",
                            "text": "中文旁白。",
                            "cards": [],
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8-sig",
            )
            result = verify_run_dir(root)
            self.assertIn("raw long English", "\n".join(result["errors"]))

    def test_embedded_untranslated_english_clause_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "manifest.json").write_text(json.dumps({"counts": {"selected_cards": 1}}, ensure_ascii=False), encoding="utf-8-sig")
            (root / "script.json").write_text(
                json.dumps(
                    [
                        {
                            "kind": "news",
                            "title": "OpenAI 动态",
                            "caption": "OpenAI 动态",
                            "text": "OpenAI 有一条新动态。Details about the OpenAI Bio Bounty program。",
                            "cards": [],
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8-sig",
            )

            result = verify_run_dir(root)

            self.assertIn("untranslated English clause", "\n".join(result["errors"]))

    def test_embedded_untranslated_technical_phrase_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "manifest.json").write_text(json.dumps({"counts": {"selected_cards": 1}}, ensure_ascii=False), encoding="utf-8-sig")
            (root / "script.json").write_text(
                json.dumps(
                    [
                        {
                            "kind": "news",
                            "title": "NVIDIA 动态",
                            "caption": "NVIDIA 动态",
                            "text": "NVIDIA 介绍 DFlash speculative decoding，称性能提升。",
                            "cards": [],
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8-sig",
            )

            result = verify_run_dir(root)

            self.assertIn("untranslated English clause", "\n".join(result["errors"]))

    def test_generic_filler_visible_script_text_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "manifest.json").write_text(json.dumps({"counts": {"selected_cards": 1}}, ensure_ascii=False), encoding="utf-8-sig")
            (root / "script.json").write_text(
                json.dumps(
                    [
                        {
                            "kind": "news",
                            "title": "中文标题",
                            "caption": "中文",
                            "text": "简单说，这条值得关注的是它可能带来的实际使用变化。",
                            "cards": [{"title": "要点", "body": "只采用已核对来源里的确定信息，不扩展成未经证实的结论。"}],
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8-sig",
            )
            result = verify_run_dir(root)
            self.assertIn("generic filler", "\n".join(result["errors"]))

    def test_macro_fluff_and_clickbait_visible_copy_fail(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "manifest.json").write_text(json.dumps({"counts": {"selected_cards": 1}}, ensure_ascii=False), encoding="utf-8-sig")
            (root / "script.json").write_text(
                json.dumps(
                    [
                        {
                            "kind": "news",
                            "title": "王炸更新彻底取代旧工具",
                            "caption": "王炸更新",
                            "text": "AI 正在快速发展，这次更新未来可期。",
                            "cards": [],
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8-sig",
            )

            errors = "\n".join(verify_run_dir(root)["errors"])

            self.assertIn("generic filler", errors)
            self.assertIn("banned editorial phrase", errors)

    def test_clickbait_discovery_title_in_public_navigation_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "pinned-comment.md").write_text(
                "00:00 开场｜AI 日报\n00:11 OpenAI｜安全主管就跑路了??\n",
                encoding="utf-8-sig",
            )

            errors = "\n".join(verify_run_dir(root)["errors"])

            self.assertIn("banned editorial phrase", errors)
            self.assertIn("banned editorial pattern", errors)
            self.assertIn("pinned-comment.md", errors)

    def test_public_copy_rejects_dangling_connector_and_unclosed_punctuation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "script.json").write_text(
                json.dumps(
                    [
                        {
                            "kind": "news",
                            "position": 1,
                            "title": "OpenAI 公布产品更新",
                            "caption": "OpenAI 公布产品更新",
                            "text": "OpenAI 公布多项产品变化，包括",
                            "cards": [{"title": "能力变化", "body": "官方称“响应速度已经提升。"}],
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8-sig",
            )

            errors = "\n".join(verify_run_dir(root)["errors"])

            self.assertIn("dangling connector", errors)
            self.assertIn("unclosed punctuation", errors)

    def test_banned_editorial_phrase_visible_script_text_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "manifest.json").write_text(json.dumps({"counts": {"selected_cards": 1}}, ensure_ascii=False), encoding="utf-8-sig")
            (root / "script.json").write_text(
                json.dumps(
                    [
                        {
                            "kind": "news",
                            "title": "中文标题",
                            "caption": "中文",
                            "text": "这条后续继续观察。",
                            "cards": [],
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8-sig",
            )
            result = verify_run_dir(root)
            self.assertIn("banned editorial phrase", "\n".join(result["errors"]))

    def test_final_output_rejects_template_and_workflow_words(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "manifest.json").write_text(json.dumps({"counts": {"selected_cards": 1}}, ensure_ascii=False), encoding="utf-8-sig")
            (root / "script.json").write_text(
                json.dumps(
                    [
                        {
                            "kind": "news",
                            "title": "中文标题",
                            "caption": "中文",
                            "text": "这条信息不够，后面需要继续优化官网抓取。",
                            "cards": [{"title": "一句话", "body": "可以关注这次更新是否影响脚本。"}],
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8-sig",
            )

            result = verify_run_dir(root)
            joined = "\n".join(result["errors"])
            self.assertIn("banned editorial phrase", joined)
            self.assertIn("banned editorial pattern", joined)

    def test_compact_brief_has_its_own_copy_contract(self):
        segment = {
            "kind": "news",
            "editorial_tier": "brief",
            "title": "GitHub Copilot 新增仓库概览",
            "caption": "GitHub Copilot 新增仓库概览",
            "text": "官方信息显示，GitHub Copilot 现在可以为首次打开的仓库生成高层概览。",
            "duration": 9.5,
            "cards": [{"title": "具体变化", "body": "首次打开仓库时可生成高层概览。"}],
        }

        self.assertEqual(_automatic_news_content_errors([segment]), [])

    def test_truncated_narration_fragment_is_blocked(self):
        segment = {
            "kind": "news",
            "editorial_tier": "brief",
            "title": "Codex 更新团队工作流",
            "caption": "Codex 更新团队工作流",
            "text": "官方信息显示，Codex 新增团队任务编排，切换到 Gro",
            "cards": [{"title": "具体变化", "body": "新增团队任务编排与状态同步。"}],
        }

        errors = _automatic_news_content_errors([segment])

        self.assertIn("truncated sentence fragment", "\n".join(errors))

    def test_concrete_release_and_acceleration_copy_passes_signal_gate(self):
        segment = {
            "kind": "news",
            "editorial_tier": "brief",
            "title": "NVIDIA 发布 DFlash 推测解码方案",
            "caption": "NVIDIA 发布 DFlash 推测解码方案",
            "text": "NVIDIA 开发者博客发布 DFlash 推测解码方案，用于 Blackwell 平台上的大模型推理加速。",
            "duration": 12.0,
            "cards": [{"title": "核心事实", "body": "该方案用于 Blackwell 平台推理加速。"}],
        }

        self.assertEqual(_automatic_news_content_errors([segment]), [])

    def test_news_segment_outside_dense_format_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "manifest.json").write_text(json.dumps({"counts": {"selected_cards": 1}}, ensure_ascii=False), encoding="utf-8-sig")
            (root / "script.json").write_text(
                json.dumps(
                    [
                        {"kind": "intro", "title": "AI 日报", "duration": 12},
                        {"kind": "news", "title": "某条新闻", "caption": "某条新闻", "text": "短句。", "duration": 95},
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8-sig",
            )

            result = verify_run_dir(root)

            self.assertIn("expected 8-65s", "\n".join(result["errors"]))

    def test_evidence_backed_brief_can_run_for_35_seconds(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "manifest.json").write_text(
                json.dumps({"counts": {"selected_cards": 1}}, ensure_ascii=False),
                encoding="utf-8-sig",
            )
            (root / "script.json").write_text(
                json.dumps(
                    [
                        {
                            "kind": "news",
                            "editorial_tier": "brief",
                            "title": "Anthropic 公布研究结果",
                            "caption": "Anthropic 公布研究结果",
                            "text": "官方原帖给出研究范围、方法、结果与限制。",
                            "duration": 35,
                            "cards": [{"title": "方法与结果", "body": "原帖截图与关键数字均已保留。"}],
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8-sig",
            )

            result = verify_run_dir(root)

            self.assertNotIn("brief segment", "\n".join(result["errors"]))

    def test_mojibake_visible_text_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "manifest.json").write_text(json.dumps({"counts": {"selected_cards": 1}}, ensure_ascii=False), encoding="utf-8-sig")
            (root / "script.json").write_text(
                json.dumps(
                    [
                        {
                            "kind": "news",
                            "title": "ModelScope 鍙戝竷 v1.38.1",
                            "caption": "AI 日报",
                            "text": "这是一条正常中文，但标题已经坏了。",
                            "cards": [],
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8-sig",
            )
            (root / "bilibili.md").write_text("00:00 ModelScope 鍙戝竷 v1.38.1\n", encoding="utf-8-sig")

            result = verify_run_dir(root)

            self.assertIn("mojibake", "\n".join(result["errors"]))

    def test_real_world_gbk_mojibake_visible_text_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "script.json").write_text(
                json.dumps(
                    [{"kind": "news", "title": "Anthropic閲嶅啓Bun", "caption": "Anthropic閲嶅啓Bun", "text": "濯掍綋妗堜緥鏄剧ず，Anthropic閲嶅啓Bun。", "cards": []}],
                    ensure_ascii=False,
                ),
                encoding="utf-8-sig",
            )

            result = verify_run_dir(root)

            self.assertIn("mojibake", "\n".join(result["errors"]))

    def test_invalid_script_json_is_publish_blocking(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "script.json").write_text("[{broken", encoding="utf-8-sig")

            result = verify_run_dir(root)

            self.assertIn("script.json parse failed", "\n".join(result["errors"]))

    def test_script_json_root_must_be_a_list(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "script.json").write_text(json.dumps({"segments": []}), encoding="utf-8-sig")

            result = verify_run_dir(root)

            self.assertIn("script.json root must be a list", "\n".join(result["errors"]))

    def test_script_json_segments_must_be_objects(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "script.json").write_text(json.dumps([{"kind": "intro"}, "broken"]), encoding="utf-8-sig")

            result = verify_run_dir(root)

            self.assertIn("script.json segment 2 must be an object", "\n".join(result["errors"]))

    def test_western_utf8_mojibake_and_replacement_character_fail(self):
        self.assertTrue(_looks_mojibake("OpenAI said itâ€™s ready"))
        self.assertTrue(_looks_mojibake("模型名称包含�替换字符"))
        self.assertTrue(_looks_mojibake("来源链标题变成???"))

    def test_normal_chinese_and_brand_copy_is_not_mojibake(self):
        self.assertFalse(_looks_mojibake("OpenAI 发布 GPT-5.6，并改进了工具调用能力。"))
        self.assertFalse(_looks_mojibake("Claude 使用 Rust 重写部分组件，启动速度提升约 10%。"))

    def test_visible_copy_rejects_dangling_colon_fragment(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "script.json").write_text(
                json.dumps(
                    [{"kind": "news", "title": "Claude 用 11 天重写 Bun:百万行代码工程实测", "caption": "Claude 重写 Bun", "text": "公开案例显示，Claude 用 11 天完成百万行代码迁移。", "cards": []}],
                    ensure_ascii=False,
                ),
                encoding="utf-8-sig",
            )

            result = verify_run_dir(root)

            self.assertIn("dangling colon fragment", "\n".join(result["errors"]))

    def test_intro_model_name_must_match_news_copy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "script.json").write_text(
                json.dumps(
                    [
                        {"kind": "intro", "title": "AI 日报", "text": "今天重点看 OpenAI 发布 GPT-Live 语音模型系列。"},
                        {"kind": "news", "title": "OpenAI 发布 GPT-5.6", "caption": "GPT-5.6 发布", "text": "OpenAI 发布 GPT-5.6，并更新了工具调用能力。", "cards": []},
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8-sig",
            )

            result = verify_run_dir(root)

            self.assertIn("intro mentions GPT-LIVE but no news segment covers it", "\n".join(result["errors"]))

    def test_script_must_match_news_manuscript(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "manifest.json").write_text(json.dumps({"counts": {"selected_cards": 1}}, ensure_ascii=False), encoding="utf-8-sig")
            (root / "script.json").write_text(
                json.dumps([{"kind": "news", "title": "新闻", "text": "视频实际念这一版。", "duration": 8}], ensure_ascii=False),
                encoding="utf-8-sig",
            )
            (root / "news-script.json").write_text(
                json.dumps({"version": 1, "segments": [{"kind": "news", "title": "新闻", "text": "稿件里是另一版。"}]}, ensure_ascii=False),
                encoding="utf-8-sig",
            )

            result = verify_run_dir(root)

            self.assertIn("does not match news-script.json", "\n".join(result["errors"]))

    def test_active_review_manuscript_must_match_rendered_script(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            script = [{"kind": "news", "title": "成片新闻", "caption": "成片新闻", "text": "视频实际念这一版。", "cards": []}]
            (root / "script.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8-sig")
            (root / "news-script.json").write_text(json.dumps({"version": 1, "segments": script}, ensure_ascii=False), encoding="utf-8-sig")
            review = root / "review"
            review.mkdir()
            (review / "final-script.json").write_text(
                json.dumps({"version": 1, "segments": [{"kind": "news", "title": "审稿新闻", "caption": "审稿新闻", "text": "人工改过这一版。", "cards": []}]}, ensure_ascii=False),
                encoding="utf-8-sig",
            )

            result = verify_run_dir(root)

            self.assertIn("review final-script does not match script.json", "\n".join(result["errors"]))

    def test_navigation_labels_must_match_script(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            script = [
                {"kind": "intro", "title": "AI 日报", "caption": "AI 日报", "text": "开场。", "start": 0, "cards": []},
                {"kind": "news", "title": "正确新闻标题", "caption": "正确新闻标题", "text": "正文。", "start": 18, "cards": []},
            ]
            (root / "script.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8-sig")
            (root / "pinned-comment.md").write_text("00:00 开场｜AI 日报\n00:18 错误新闻标题\n", encoding="utf-8-sig")

            result = verify_run_dir(root)

            self.assertIn("pinned-comment.md navigation label 2 does not match script.json", result["errors"])

    def test_automatic_quality_gate_rejects_low_information_video_copy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            script = [
                {
                    "kind": "news",
                    "title": "模型页更新",
                    "caption": "模型页更新",
                    "text": "模型页出现更新，入口待确认。",
                    "cards": [],
                }
            ]
            (root / "script.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8-sig")

            result = verify_run_dir(root)

            joined = "\n".join(result["errors"])
            self.assertIn("narration is too short", joined)
            self.assertIn("contains low-information copy", joined)

    def test_automatic_quality_gate_rejects_empty_digest(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "script.json").write_text(
                json.dumps([{"kind": "intro", "title": "AI 日报", "text": "早上好。"}], ensure_ascii=False),
                encoding="utf-8-sig",
            )

            result = verify_run_dir(root)

            self.assertTrue(any("automatic briefing has no eligible news segments" in error for error in result["errors"]))

    def test_social_signal_requires_visible_disclaimer_and_source_screenshot(self):
        segment = {
            "kind": "news",
            "signal_kind": "official_personnel",
            "title": "一线消息｜Codex 动态",
            "caption": "一线消息｜Codex 动态",
            "text": "一线消息，未获官方公告确认。原帖提到 Codex 的后续方向。",
            "cards": [{"title": "消息性质", "body": "一线消息，未获官方公告确认。"}],
            "evidence": [
                {
                    "screenshot_required": "true",
                    "screenshot_status": "captured",
                    "screenshot_path": "C:/tmp/source.png",
                }
            ],
        }

        self.assertEqual(_signal_segment_errors([segment]), [])

    def test_social_signal_cannot_bypass_disclaimer_or_claim_release(self):
        segment = {
            "kind": "news",
            "signal_kind": "community_rumour",
            "title": "社区消息",
            "text": "这个功能已经上线。",
            "cards": [],
            "evidence": [],
        }

        errors = "\n".join(_signal_segment_errors([segment]))

        self.assertIn("missing the required visible disclaimer", errors)
        self.assertIn("states an unconfirmed signal as fact", errors)
        self.assertIn("no captured source-post screenshot", errors)

    def test_copy_quality_error_maps_to_rendered_news_position_for_auto_repair(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "script.json").write_text(
                json.dumps(
                    [
                        {"kind": "intro", "title": "开场"},
                        {"kind": "news", "position": 1, "title": "第一条"},
                        {"kind": "news", "position": 2, "title": "第二条"},
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8-sig",
            )

            positions = repairable_news_positions(
                root,
                ["visible/public text contains untranslated English clause at segment 2 page 3 card 4 body: Details about the program"],
            )

            self.assertEqual(positions, [1])

    def test_public_copy_rejects_ellipsis_and_backend_discovery_terms(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "script.json").write_text(
                json.dumps(
                    [
                        {
                            "kind": "news",
                            "position": 1,
                            "title": "OpenAI 发布产品更新",
                            "caption": "OpenAI 发布产品更新",
                            "text": "OpenAI 公布了完整的产品更新信息。",
                            "cards": [{"title": "开放范围", "body": "更多地区将在本周开放…"}],
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8-sig",
            )
            (root / "bilibili.md").write_text(
                "消息来自 rssHub Feed 与 Google 新闻订阅源。\n",
                encoding="utf-8-sig",
            )

            errors = "\n".join(verify_run_dir(root)["errors"])

            self.assertIn("contains an ellipsis", errors)
            self.assertIn("banned editorial phrase", errors)
            self.assertRegex(errors, r"(?i)rss|feed|Google 新闻|订阅源")

    def test_required_screenshot_must_exist_on_disk(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "selected": [
                            {
                                "entity": "OpenAI",
                                "evidence": [
                                    {
                                        "screenshot_required": "true",
                                        "screenshot_status": "captured",
                                        "screenshot_path": str(root / "evidence-screenshots" / "missing.png"),
                                    }
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8-sig",
            )

            result = verify_run_dir(root)

            self.assertIn("screenshot file is missing or empty", "\n".join(result["errors"]))

    def test_current_run_requires_qa_passed_and_matching_manifest_id(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "manifest.json").write_text(
                json.dumps({"run_id": "stale-run", "package_quality": {"ok": True}, "automatic_quality_gate": {"ok": True}}),
                encoding="utf-8-sig",
            )
            (root / "run-state.json").write_text(
                json.dumps({"run_id": "current-run", "status": "RENDER_FAILED"}),
                encoding="utf-8-sig",
            )

            result = verify_run_dir(root)

            errors = "\n".join(result["errors"])
            self.assertIn("manifest run_id does not match", errors)
            self.assertIn("expected QA_PASSED", errors)

    def test_automatic_package_rejects_unapproved_tts_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "manifest.json").write_text(
                json.dumps({"run_id": "run-1", "package_quality": {"ok": True}, "automatic_quality_gate": {"ok": True}}),
                encoding="utf-8-sig",
            )
            (root / "run-state.json").write_text(json.dumps({"run_id": "run-1", "status": "QA_PASSED"}), encoding="utf-8-sig")
            (root / "render-info.json").write_text(json.dumps({"tts": "sapi_fallback"}), encoding="utf-8-sig")

            result = verify_run_dir(root)

            self.assertIn("unapproved TTS fallback", "\n".join(result["errors"]))

    def test_automatic_package_accepts_publishable_indextts2_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "manifest.json").write_text(
                json.dumps({"run_id": "run-1", "package_quality": {"ok": True}, "automatic_quality_gate": {"ok": True}}),
                encoding="utf-8-sig",
            )
            (root / "run-state.json").write_text(json.dumps({"run_id": "run-1", "status": "QA_PASSED"}), encoding="utf-8-sig")
            (root / "render-info.json").write_text(
                json.dumps(
                    {
                        "tts": "indextts2:roxy",
                        "tts_backend": "indextts2",
                        "tts_profile_id": "roxy",
                        "tts_profile_publishable": "true",
                    }
                ),
                encoding="utf-8-sig",
            )

            errors = "\n".join(verify_run_dir(root)["errors"])

            self.assertNotIn("IndexTTS2", errors)
            self.assertNotIn("unapproved TTS fallback", errors)

    def test_automatic_package_rejects_nonpublishable_indextts2_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "manifest.json").write_text(
                json.dumps({"run_id": "run-1", "package_quality": {"ok": True}, "automatic_quality_gate": {"ok": True}}),
                encoding="utf-8-sig",
            )
            (root / "run-state.json").write_text(json.dumps({"run_id": "run-1", "status": "QA_PASSED"}), encoding="utf-8-sig")
            (root / "render-info.json").write_text(
                json.dumps(
                    {
                        "tts": "indextts2:roxy",
                        "tts_backend": "indextts2",
                        "tts_profile_id": "roxy",
                        "tts_profile_publishable": "false",
                    }
                ),
                encoding="utf-8-sig",
            )

            errors = "\n".join(verify_run_dir(root)["errors"])

            self.assertIn("not approved for publication", errors)


if __name__ == "__main__":
    unittest.main()

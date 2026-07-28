import json
import tempfile
import unittest
from pathlib import Path

from briefing.agent_workflow import (
    _file_sha256,
    _visual_sample_rows,
    agent_audit_path,
    create_agent_package,
    finalize_agent_edit,
    validate_agent_edit,
    verify_agent_edit_contract,
    verify_visual_agent_audit,
    visual_audit_path,
    visual_qa_input_path,
)
from briefing.review import review_draft_state_path, review_state_path


class AgentWorkflowTests(unittest.TestCase):
    def test_visual_samples_follow_actual_remotion_slides(self) -> None:
        script = [
            {"kind": "intro", "start": 0.0, "end": 3.0, "visual_pages": [{"kind": "overview"}]},
            {
                "kind": "news",
                "story_id": "story-1",
                "start": 3.0,
                "end": 11.0,
                "visual_pages": [{"kind": "cards"}, {"kind": "evidence"}],
            },
        ]
        slides = [
            {"segmentIndex": 0, "pageIndex": 0, "kind": "intro", "start": 0.0, "end": 3.0, "page": {"kind": "overview"}},
            {"segmentIndex": 1, "pageIndex": 0, "kind": "news", "start": 3.0, "end": 11.0, "page": {"kind": "cards"}},
        ]

        rows = _visual_sample_rows(script, 11.0, slides)

        page_rows = [row for row in rows if row["sample_kind"] == "page_midpoint"]
        self.assertEqual([row["page_kind"] for row in page_rows], ["overview", "cards"])
        self.assertNotIn("evidence", [row["page_kind"] for row in page_rows])
    def fixture(self, root: Path) -> tuple[dict, dict, dict]:
        story_id = "story-1"
        claim_id = "claim-1"
        manifest = {
            "run_id": "run-1",
            "run_date": "2026-07-11",
            "package_quality": {"ok": True},
            "source_coverage": {"publish_allowed": True},
            "agent_policy": {"required": True, "agent": "codex"},
            "selected": [
                {
                    "title": "Nova-7 发布并支持 128K 上下文",
                    "entity": "Nova",
                    "editorial_tier": "headline",
                    "story_spec": {
                        "story_id": story_id,
                        "claims": [
                            {
                                "claim_id": claim_id,
                                "text": "Nova-7 已发布，并支持 128K 上下文。",
                                "renderable": True,
                                "verifiable": True,
                                "evidence_urls": ["https://official.example/nova7"],
                            }
                        ],
                    },
                    "evidence": [{"url": "https://official.example/nova7", "reliability": "official"}],
                }
            ],
        }
        state = {
            "version": 1,
            "run_dir": str(root),
            "run_date": "2026-07-11",
            "segments": [
                {
                    "kind": "intro",
                    "enabled": True,
                    "title": "AI 日报",
                    "text": "今天先看一条已经核实的模型更新。",
                    "cards": [],
                    "visual_pages": [],
                },
                {
                    "kind": "news",
                    "enabled": True,
                    "position": 1,
                    "total": 1,
                    "story_id": story_id,
                    "claim_ids": [claim_id],
                    "generation_path": "structured_editorial_plan",
                    "editorial_tier": "headline",
                    "title": "Nova-7 发布并支持 128K 上下文",
                    "headline": "Nova-7 发布并支持 128K 上下文",
                    "caption": "Nova-7 发布并支持 128K 上下文",
                    "text": "官方信息显示，Nova-7 已发布，并支持 128K 上下文。",
                    "cards": [{"title": "能力变化", "body": "Nova-7 已发布，并支持 128K 上下文。"}],
                    "visual_pages": [],
                },
                {
                    "kind": "outro",
                    "enabled": True,
                    "title": "明天继续",
                    "text": "继续追踪真实使用反馈。",
                    "cards": [],
                    "visual_pages": [],
                },
            ],
        }
        from briefing.agent_workflow import _fingerprint, manifest_semantic_fingerprint

        manifest["agent_policy"]["draft_state_fingerprint"] = _fingerprint(state)
        audit = {
            "version": 1,
            "agent": "codex",
            "manifest_fingerprint": manifest_semantic_fingerprint(manifest),
            "draft_state_fingerprint": _fingerprint(state),
            "source_review_passed": True,
            "copy_review_passed": True,
            "no_upload": True,
            "stories": [
                {
                    "story_id": story_id,
                    "verdict": "approved",
                    "selected_claim_ids": [claim_id],
                    "checked_urls": ["https://official.example/nova7"],
                    "copy_checks": {
                        "source_opened": True,
                        "facts_bound": True,
                        "uncertainty_preserved": True,
                        "chinese_copy": True,
                        "sentence_complete": True,
                        "no_generic_filler": True,
                        "impact_language_specific": True,
                        "no_clickbait_overreach": True,
                    },
                    "notes": "已打开第一方页面。",
                }
            ],
        }
        (root / "review").mkdir(parents=True)
        (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        review_state_path(root).write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        review_draft_state_path(root).write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        agent_audit_path(root).write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")
        return manifest, state, audit

    def test_valid_agent_edit_preserves_claim_contract_and_is_hash_bound(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.fixture(root)

            result = finalize_agent_edit(root)

            self.assertTrue(result["ok"], result)
            final = json.loads((root / "review" / "final-script.json").read_text(encoding="utf-8-sig"))
            news = next(row for row in final["segments"] if row["kind"] == "news")
            self.assertEqual(news["story_id"], "story-1")
            self.assertEqual(news["claim_ids"], ["claim-1"])
            self.assertEqual(news["generation_path"], "structured_editorial_plan")
            self.assertEqual(verify_agent_edit_contract(root), [])

            final["segments"][1]["text"] += " 被篡改。"
            (root / "review" / "final-script.json").write_text(json.dumps(final, ensure_ascii=False), encoding="utf-8")
            self.assertIn("final_script_fingerprint", "\n".join(verify_agent_edit_contract(root)))

    def test_agent_package_compacts_evidence_without_dropping_review_fields(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest, _state, _audit = self.fixture(root)
            evidence = manifest["selected"][0]["evidence"][0]
            evidence.update(
                {
                    "source": "Nova Official",
                    "title": "Nova-7 发布",
                    "published_at": "2026-07-11T00:00:00+00:00",
                    "excerpt": "证据" * 2000,
                    "screenshot_status": "captured",
                    "screenshot_path": str(root / "evidence.png"),
                    "screenshot_capture_failure": "large internal browser trace",
                    "document_content_hash": "internal-only-hash",
                }
            )
            (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

            result = create_agent_package(root)

            self.assertTrue(result["ok"])
            brief = json.loads((root / "review" / "agent-brief.json").read_text(encoding="utf-8-sig"))
            compact = brief["stories"][0]["evidence"][0]
            self.assertEqual(compact["url"], "https://official.example/nova7")
            self.assertEqual(compact["screenshot_status"], "captured")
            self.assertLessEqual(len(compact["excerpt"]), 1200)
            self.assertNotIn("screenshot_capture_failure", compact)
            self.assertNotIn("document_content_hash", compact)

    def test_unknown_claim_new_number_and_missing_source_check_are_rejected(self):
        mutations = ["claim", "number", "url", "freeform_fact", "role"]
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                _manifest, state, audit = self.fixture(root)
                if mutation == "claim":
                    state["segments"][1]["claim_ids"] = ["unknown"]
                    audit["stories"][0]["selected_claim_ids"] = ["unknown"]
                elif mutation == "number":
                    state["segments"][1]["text"] += " 性能提升 999 倍。"
                elif mutation == "url":
                    audit["stories"][0]["checked_urls"] = ["https://unrelated.example/story"]
                elif mutation == "freeform_fact":
                    state["segments"][1]["text"] += " 该产品已经全面免费开放并支持视频生成。"
                else:
                    state["segments"][1]["editorial_tier"] = "brief"
                review_state_path(root).write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
                agent_audit_path(root).write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")

                errors = validate_agent_edit(root)

                self.assertTrue(errors)

    def test_frozen_source_time_numbers_are_allowed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest, state, audit = self.fixture(root)
            source_card = {"title": "原文与时间", "body": "X / @Nova|7月14日 01:24 发布|X 官方账号"}
            state["segments"][1]["cards"].append(source_card)
            draft = json.loads(json.dumps(state, ensure_ascii=False))

            from briefing.agent_workflow import _fingerprint, manifest_semantic_fingerprint

            draft_fp = _fingerprint(draft)
            manifest["agent_policy"]["draft_state_fingerprint"] = draft_fp
            audit["draft_state_fingerprint"] = draft_fp
            audit["manifest_fingerprint"] = manifest_semantic_fingerprint(manifest)
            (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            review_state_path(root).write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            review_draft_state_path(root).write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
            agent_audit_path(root).write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")

            self.assertEqual(validate_agent_edit(root), [])

    def test_visual_audit_must_bind_current_video_cover_and_contact_sheet(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            folder = root / "review" / "visual-qa"
            folder.mkdir(parents=True)
            video = root / "final.mp4"
            cover = root / "cover.png"
            contact = folder / "contact-sheet.png"
            script = root / "script.json"
            subtitles = root / "subtitles.srt"
            audio_quality = root / "audio-quality.json"
            frame = folder / "frame-000.png"
            video.write_bytes(b"video")
            cover.write_bytes(b"cover")
            contact.write_bytes(b"contact")
            script.write_bytes(b"script")
            subtitles.write_bytes(b"subtitles")
            audio_quality.write_bytes(b"audio")
            frame.write_bytes(b"frame")
            visual_input = {
                "video": str(video),
                "video_sha256": _file_sha256(video),
                "cover": str(cover),
                "cover_sha256": _file_sha256(cover),
                "contact_sheet": str(contact),
                "contact_sheets": [{"path": str(contact), "sha256": _file_sha256(contact)}],
                "script": str(script),
                "script_sha256": _file_sha256(script),
                "subtitles": str(subtitles),
                "subtitles_sha256": _file_sha256(subtitles),
                "audio_quality": str(audio_quality),
                "audio_quality_sha256": _file_sha256(audio_quality),
                "frames": [{"path": str(frame), "sha256": _file_sha256(frame)}],
            }
            visual_qa_input_path(root).write_text(json.dumps(visual_input), encoding="utf-8")
            audit = {
                "agent": "codex",
                "verdict": "approved",
                "video_sha256": visual_input["video_sha256"],
                "cover_sha256": visual_input["cover_sha256"],
                "script_sha256": visual_input["script_sha256"],
                "subtitles_sha256": visual_input["subtitles_sha256"],
                "audio_quality_sha256": visual_input["audio_quality_sha256"],
                "contact_sheet_sha256": [visual_input["contact_sheets"][0]["sha256"]],
                "reviewed_frame_sha256": [visual_input["frames"][0]["sha256"]],
                "checks": {
                    "layout_no_clipping": True,
                    "text_legible": True,
                    "evidence_readable": True,
                    "motion_coherent": True,
                    "cover_truthful": True,
                    "subtitles_safe_area": True,
                    "no_broken_frames": True,
                    "narration_complete": True,
                    "subtitle_sync": True,
                    "audio_no_artifacts": True,
                },
            }
            visual_audit_path(root).write_text(json.dumps(audit), encoding="utf-8")

            self.assertEqual(verify_visual_agent_audit(root), [])
            cover.write_bytes(b"changed")
            self.assertIn("cover_sha256", "\n".join(verify_visual_agent_audit(root)))


if __name__ == "__main__":
    unittest.main()

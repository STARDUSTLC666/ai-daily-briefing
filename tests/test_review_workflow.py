import json
import tempfile
import unittest
from pathlib import Path

from briefing.review import (
    append_morning_updates_to_reviewed_script,
    create_review_package,
    review_final_script_path,
    review_morning_script_path,
    review_state_path,
    write_final_script_from_state,
)


class ReviewWorkflowTests(unittest.TestCase):
    def test_final_script_includes_every_required_captured_evidence_image(self):
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td)
            first = run_dir / "evidence-screenshots" / "first.png"
            second = run_dir / "evidence-screenshots" / "second.png"
            optional = run_dir / "evidence-screenshots" / "optional.png"
            first.parent.mkdir(parents=True)
            for path in (first, second, optional):
                path.write_bytes(b"\x89PNG\r\n\x1a\n")
            (run_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "selected": [
                            {
                                "title": "多帖联合发布",
                                "story_spec": {"story_id": "story-1"},
                                "evidence": [
                                    {
                                        "title": "第一帖",
                                        "source": "Codex verified X official account leads",
                                        "url": "https://x.com/example/status/1",
                                        "screenshot_required": "true",
                                        "screenshot_status": "captured",
                                        "screenshot_path": str(first),
                                    },
                                    {
                                        "title": "第二帖",
                                        "source": "Codex verified X official account leads",
                                        "url": "https://x.com/example/status/2",
                                        "screenshot_required": "true",
                                        "screenshot_status": "captured",
                                        "screenshot_path": str(second),
                                    },
                                    {
                                        "title": "可选图片",
                                        "source": "官网",
                                        "url": "https://example.com/optional",
                                        "screenshot_required": "false",
                                        "screenshot_status": "captured",
                                        "screenshot_path": str(optional),
                                    },
                                ],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            state = {
                "run_date": "2026-07-14",
                "segments": [
                    {
                        "kind": "news",
                        "enabled": True,
                        "story_id": "story-1",
                        "title": "多帖联合发布",
                        "caption": "多帖联合发布",
                        "text": "官方连续发布两条说明。",
                        "cards": [],
                        "images": [
                            {
                                "id": "primary",
                                "label": "第一帖",
                                "path": str(first),
                                "source": "X / @example",
                                "source_url": "https://x.com/example/status/1",
                                "enabled": True,
                            }
                        ],
                        "visual_pages": [],
                    }
                ],
            }

            write_final_script_from_state(run_dir, state)
            write_final_script_from_state(run_dir, state)

            final_payload = json.loads(review_final_script_path(run_dir).read_text(encoding="utf-8-sig"))
            evidence_pages = [
                page for page in final_payload["segments"][0]["visual_pages"] if page.get("kind") == "evidence"
            ]
            self.assertEqual(
                [str(Path(page["evidenceVisual"]["image"]).resolve()) for page in evidence_pages],
                [str(first.resolve()), str(second.resolve())],
            )
            self.assertTrue(all(page["evidenceVisual"]["required"] for page in evidence_pages))
            self.assertEqual(evidence_pages[1]["source"], "X / @example")
            saved_state = json.loads(review_state_path(run_dir).read_text(encoding="utf-8-sig"))
            self.assertEqual(len(saved_state["segments"][0]["images"]), 2)

    def test_review_package_exports_edited_final_script_with_images(self):
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td)
            image = run_dir / "source-assets" / "source.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"\x89PNG\r\n\x1a\n")
            (run_dir / "news-script.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "segments": [
                            {
                                "kind": "news",
                                "editorial_tier": "brief",
                                "title": "腾讯混元 Hy3 发布",
                                "caption": "混元 Hy3",
                                "text": "腾讯发布混元 Hy3。",
                                "active_tab": "模型发布",
                                "bottom_active": "01 腾讯",
                                "cards": [{"title": "核心能力", "body": "视频生成能力升级"}],
                                "visual_pages": [
                                    {
                                        "kind": "brief",
                                        "title": "混元 Hy3",
                                        "cards": [{"title": "能力", "body": "文生视频"}],
                                        "evidenceVisual": {"image": str(image), "title": "官网截图", "source": "腾讯混元官网"},
                                    },
                                    {
                                        "kind": "evidence",
                                        "title": "官网截图",
                                        "source": "腾讯混元官网",
                                        "source_url": "https://hy.tencent.com/research/hy3",
                                        "evidenceVisual": {"image": str(image), "title": "官网截图", "source": "腾讯混元官网"},
                                    },
                                ],
                            },
                            {
                                "kind": "news",
                                "title": "弱新闻",
                                "caption": "弱新闻",
                                "text": "这条不进成片。",
                                "cards": [],
                                "visual_pages": [],
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8-sig",
            )

            result = create_review_package(run_dir)
            self.assertEqual(result["status"], "created")
            self.assertTrue((run_dir / "review" / "index.html").exists())

            state = json.loads(review_state_path(run_dir).read_text(encoding="utf-8-sig"))
            self.assertEqual(len(state["segments"]), 2)
            self.assertEqual(len(state["segments"][0]["images"]), 1)
            self.assertEqual(state["segments"][0]["visual_pages"][0]["kind"], "brief")
            self.assertNotIn("evidenceVisual", state["segments"][0]["visual_pages"][0])
            state["segments"][0]["text"] = "腾讯这次发布的是混元 Hy3，重点看开放入口和视频能力。"
            state["segments"][0]["cards"].append({"title": "编辑建议", "body": "模型用户可以关注"})
            state["segments"][1]["enabled"] = False

            write_final_script_from_state(run_dir, state)
            final_payload = json.loads(review_final_script_path(run_dir).read_text(encoding="utf-8-sig"))
            render_required = json.loads((run_dir / "review" / "render-required.json").read_text(encoding="utf-8-sig"))
            self.assertEqual(len(final_payload["segments"]), 1)
            final_seg = final_payload["segments"][0]
            self.assertIn("混元 Hy3", final_seg["text"])
            self.assertEqual(final_seg["cards"][-1]["title"], "编辑建议")
            evidence_pages = [page for page in final_seg["visual_pages"] if page.get("kind") == "evidence"]
            self.assertEqual(len(evidence_pages), 1)
            self.assertEqual(evidence_pages[0]["evidenceVisual"]["image"], str(image))
            self.assertEqual(final_seg["visual_pages"][0]["kind"], "brief")
            self.assertIn("01 腾讯", final_seg["bottom_tabs"])
            self.assertEqual(render_required["manuscript"], "review/final-script.json")
            self.assertTrue(render_required["fingerprint"])

    def test_morning_updates_do_not_overwrite_human_reviewed_script(self):
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td)
            state = {
                "run_date": "2026-07-07",
                "segments": [
                    {
                        "kind": "intro",
                        "enabled": True,
                        "title": "AI 日报",
                        "caption": "AI 日报",
                        "text": "下面请看具体内容。",
                        "cards": [],
                        "images": [],
                        "visual_pages": [],
                    },
                    {
                        "kind": "news",
                        "enabled": True,
                        "title": "已审新闻",
                        "caption": "已审新闻",
                        "text": "这条是人工审核过的内容。",
                        "active_tab": "模型发布",
                        "bottom_active": "01 已审",
                        "cards": [{"title": "重点", "body": "人工稿"}],
                        "images": [],
                        "visual_pages": [{"kind": "cards", "title": "已审新闻", "source_url": "https://example.com/old", "cards": []}],
                    },
                    {
                        "kind": "outro",
                        "enabled": True,
                        "title": "今天先看到这里",
                        "caption": "收尾",
                        "text": "本次的播报完毕。",
                        "cards": [],
                        "images": [],
                        "visual_pages": [],
                    },
                ],
            }
            write_final_script_from_state(run_dir, state)
            reviewed_before = review_final_script_path(run_dir).read_text(encoding="utf-8-sig")

            new_segments = [
                {
                    "kind": "news",
                    "title": "新增新闻",
                    "caption": "新增新闻",
                    "text": "这是 6 点自动补进来的新内容。",
                    "active_tab": "产品更新",
                    "bottom_active": "01 新增",
                    "cards": [{"title": "新增", "body": "过去 6 小时"}],
                    "visual_pages": [{"kind": "cards", "title": "新增新闻", "source_url": "https://example.com/new", "cards": []}],
                }
            ]

            from unittest.mock import patch

            with patch("briefing.render._segments", return_value=new_segments):
                info = append_morning_updates_to_reviewed_script(run_dir, cards=[object()], max_new_items=3)

            self.assertEqual(review_final_script_path(run_dir).read_text(encoding="utf-8-sig"), reviewed_before)
            self.assertEqual(info["auto_appended_count"], 1)
            morning_payload = json.loads(review_morning_script_path(run_dir).read_text(encoding="utf-8-sig"))
            titles = [seg["title"] for seg in morning_payload["segments"]]
            self.assertEqual(titles, ["AI 日报", "已审新闻", "新增新闻", "今天先看到这里"])


if __name__ == "__main__":
    unittest.main()

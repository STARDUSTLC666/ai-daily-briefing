import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from briefing.models import EvidenceCard
from briefing.output_verify import verify_run_dir
from briefing.presentation import card_display_entity, has_arxiv_evidence
from briefing.render import _build_script, _script_subject, _segments
from briefing.story_model import build_story_spec
from briefing.writer import write_bilibili_md, write_pinned_comment


UTC = timezone.utc


def nvidia_card(entity: str = "论文 / arXiv") -> EvidenceCard:
    return EvidenceCard(
        cluster_key="nvidia-dflash",
        event_title="NVIDIA Blackwell 上 DFlash 推测解码推理最高提速 15 倍",
        entity=entity,
        risk="green",
        confidence=86,
        selected=True,
        reason="官方来源确认，且发布时间在窗口内。",
        source_count=1,
        official_count=1,
        media_count=0,
        community_count=0,
        first_seen_at=datetime(2026, 7, 10, 8, tzinfo=UTC),
        latest_published_at=datetime(2026, 7, 10, 8, tzinfo=UTC),
        key_facts=["NVIDIA 介绍 DFlash 推测解码，称 Blackwell 推理性能最高可提升到 15 倍。"],
        evidence_links=[
            {
                "source": "NVIDIA Developer Blog",
                "tier": "A",
                "reliability": "official",
                "title": "Boost Inference Performance up to 15x on NVIDIA Blackwell Using DFlash",
                "url": "https://developer.nvidia.com/blog/dflash/",
                "excerpt": "NVIDIA 介绍 DFlash 推测解码，称 Blackwell 推理性能最高可提升到 15 倍。",
            }
        ],
        uncertainty=[],
        score=88,
    )


class PresentationIdentityTests(unittest.TestCase):
    def test_non_arxiv_nvidia_evidence_repairs_stale_paper_entity_everywhere(self):
        card = nvidia_card()

        self.assertFalse(has_arxiv_evidence(card))
        self.assertEqual(card_display_entity(card), "NVIDIA")
        self.assertEqual(_script_subject(card), "NVIDIA")
        self.assertEqual(build_story_spec(card).subject, "NVIDIA")

        script = _build_script(card)
        segments = _segments([card])
        visible = script + "\n" + json.dumps(segments, ensure_ascii=False)
        self.assertIn("NVIDIA", visible)
        self.assertNotIn("论文更新", visible)
        self.assertNotIn("01 论文", visible)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            timeline = [(5, card, 20)]
            write_bilibili_md(root / "bilibili.md", [card], "1080p", "2026-07-10", timeline=timeline)
            write_pinned_comment(root / "pinned-comment.md", timeline)
            public_text = (root / "bilibili.md").read_text(encoding="utf-8-sig") + (root / "pinned-comment.md").read_text(encoding="utf-8-sig")
            self.assertIn("NVIDIA Blackwell", public_text)
            self.assertNotIn("NVIDIA｜NVIDIA", public_text)
            self.assertNotIn("论文 / arXiv｜", public_text)

    def test_verifier_blocks_non_arxiv_card_labeled_as_paper(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "selected": [
                            {
                                "entity": "论文 / arXiv",
                                "evidence": nvidia_card().evidence_links,
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8-sig",
            )

            result = verify_run_dir(root)

            self.assertIn("labels a non-arXiv story as paper", "\n".join(result["errors"]))


if __name__ == "__main__":
    unittest.main()

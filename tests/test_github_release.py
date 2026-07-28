import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import requests

from briefing.editorial_plan import build_editorial_plan
from briefing.github_release import (
    attach_github_compare_evidence,
    build_release_delta_summary,
    enrich_github_release_item,
    github_compare_parts,
    github_release_parts,
    is_sparse_release_summary,
    previous_numeric_tag,
)
from briefing.models import EvidenceCard, FeedItem
from briefing.writer import is_publishable_card


class GithubReleaseTests(unittest.TestCase):
    def test_release_parts_parse_tag_url(self):
        self.assertEqual(
            github_release_parts("https://github.com/openai/codex/releases/tag/rust-v0.143.0-alpha.36"),
            ("openai", "codex", "rust-v0.143.0-alpha.36"),
        )

    def test_compare_parts_parse_compare_url(self):
        self.assertEqual(
            github_compare_parts("https://github.com/openai/codex/compare/rust-v0.145.0-alpha.23...rust-v0.145.0-alpha.24"),
            ("openai", "codex", "rust-v0.145.0-alpha.23...rust-v0.145.0-alpha.24"),
        )

    def test_sparse_release_summary_is_detected(self):
        self.assertTrue(is_sparse_release_summary("Release 0.143.0-alpha.36", "0.143.0-alpha.36"))
        self.assertFalse(is_sparse_release_summary("Fix installer metadata lookup and add tests", "0.143.0-alpha.36"))

    def test_previous_numeric_tag(self):
        self.assertEqual(previous_numeric_tag("rust-v0.143.0-alpha.36"), "rust-v0.143.0-alpha.35")

    def test_build_release_delta_summary_from_compare(self):
        summary = build_release_delta_summary(
            "rust-v0.143.0-alpha.36",
            "rust-v0.143.0-alpha.35",
            "Release 0.143.0-alpha.36",
            {
                "total_commits": 17,
                "html_url": "https://github.com/openai/codex/compare/a...b",
                "files": [{"filename": "scripts/install/install.sh"}, {"filename": "codex-rs/tui/src/app/plugin_mentions.rs"}],
                "commits": [
                    {"commit": {"message": "Release 0.143.0-alpha.36"}},
                    {"commit": {"message": "[codex] Add configurable multi-agent mode hint text (#30493)"}},
                    {"commit": {"message": "Update install release metadata lookup (#30500)"}},
                    {"commit": {"message": "Add install script tests (#30501)"}},
                ],
            },
        )

        self.assertIn("较 rust-v0.143.0-alpha.35", summary)
        self.assertIn("17 个提交", summary)
        self.assertIn("新增 multi-agent 模式提示配置", summary)
        self.assertIn("安装脚本", summary)
        self.assertIn("修复/维护线索", summary)
        self.assertIn("补充安装脚本测试", summary)

    def test_known_english_commit_subject_is_localized_without_raw_text_leak(self):
        summary = build_release_delta_summary(
            "rust-v0.145.0-alpha.24",
            "rust-v0.145.0-alpha.23",
            "",
            {
                "total_commits": 1,
                "html_url": "https://github.com/openai/codex/compare/a...b",
                "files": [{"filename": "codex-rs/hooks.rs"}],
                "commits": [{"commit": {"message": "Fix quoted hook commands on Windows (#123)"}}],
            },
        )

        self.assertIn("修复 Windows 引号 Hook 命令解析", summary)
        self.assertNotIn("quoted hook commands", summary)

    def test_compare_summary_is_bound_as_official_evidence_before_selection(self):
        """官方 compare 的中文事实必须先进入证据合同，才允许 strict_auto 选中。"""
        release_url = "https://github.com/openai/codex/releases/tag/rust-v0.145.0-alpha.24"
        compare_url = "https://github.com/openai/codex/compare/rust-v0.145.0-alpha.23...rust-v0.145.0-alpha.24"
        card = EvidenceCard(
            cluster_key="OpenAI:145-alpha.24",
            event_title="OpenAI Codex GitHub Releases 发布 0.145.0-alpha.24",
            entity="OpenAI",
            risk="green",
            confidence=92,
            selected=True,
            reason="官方来源确认，且发布时间在窗口内。",
            source_count=1,
            official_count=1,
            media_count=0,
            community_count=0,
            first_seen_at=datetime(2026, 7, 18, 22, 29, tzinfo=timezone.utc),
            latest_published_at=datetime(2026, 7, 18, 22, 29, tzinfo=timezone.utc),
            key_facts=[
                "OpenAI 相关事件：0.145.0-alpha.24",
                "来源摘要：版本对比：较 rust-v0.145.0-alpha.23，GitHub compare 显示 20 个提交、209 个文件变更；"
                "变更摘要：调整登录与鉴权流程；更新配置或协议 schema；"
                f"修复/维护线索：修复一项代码问题；变更来源：{compare_url}",
            ],
            evidence_links=[
                {
                    "source": "OpenAI Codex GitHub Releases",
                    "tier": "A",
                    "reliability": "official",
                    "title": "0.145.0-alpha.24",
                    "url": release_url,
                    "published_at": "2026-07-18T22:29:36+00:00",
                    "excerpt": "Release 0.145.0-alpha.24 · openai/codex · GitHub",
                    "github_compare_verified": "true",
                    "github_compare_url": compare_url,
                    "github_compare_summary": (
                        "版本对比：较 rust-v0.145.0-alpha.23，GitHub compare 显示 20 个提交、209 个文件变更；"
                        "变更摘要：调整登录与鉴权流程；更新配置或协议 schema；"
                        f"修复/维护线索：修复一项代码问题；变更来源：{compare_url}"
                    ),
                }
            ],
            uncertainty=[],
            score=86.0,
        )

        self.assertFalse(is_publishable_card(card, strict_auto=True))
        self.assertEqual(attach_github_compare_evidence([card]), 1)
        self.assertEqual(attach_github_compare_evidence([card]), 0)
        self.assertEqual(card.evidence_links[-1]["url"], compare_url)
        self.assertEqual(card.evidence_links[-1]["derived_evidence"], "github_api_compare")
        self.assertTrue(is_publishable_card(card, strict_auto=True))
        plan = build_editorial_plan(card)
        public_text = " ".join([plan.title, plan.narration(), *plan.facts])
        self.assertIn("20 个提交", public_text)
        self.assertIn("登录与鉴权", public_text)
        self.assertNotIn("Release 0.145", public_text)

    def test_compare_evidence_rejects_cross_repository_url(self):
        """摘要里的 compare URL 必须与 Release 同仓，防止错误链接获得官方身份。"""
        card = EvidenceCard(
            cluster_key="OpenAI:145-alpha.24",
            event_title="OpenAI Codex GitHub Releases 发布 0.145.0-alpha.24",
            entity="OpenAI",
            risk="green",
            confidence=92,
            selected=True,
            reason="官方来源确认。",
            source_count=1,
            official_count=1,
            media_count=0,
            community_count=0,
            first_seen_at=datetime(2026, 7, 18, tzinfo=timezone.utc),
            latest_published_at=datetime(2026, 7, 18, tzinfo=timezone.utc),
            key_facts=["来源摘要：版本对比：20 个提交；变更来源：https://github.com/other/repo/compare/a...b"],
            evidence_links=[
                {
                    "source": "OpenAI Codex GitHub Releases",
                    "url": "https://github.com/openai/codex/releases/tag/rust-v0.145.0-alpha.24",
                    "excerpt": "Release 0.145.0-alpha.24",
                }
            ],
            uncertainty=[],
            score=86.0,
        )

        self.assertEqual(attach_github_compare_evidence([card]), 0)
        self.assertEqual(len(card.evidence_links), 1)

    def test_same_repository_compare_text_without_api_marker_is_rejected(self):
        """同仓 URL 和相似中文摘要也不能替代 GitHub API 成功标记。"""
        compare_url = "https://github.com/openai/codex/compare/a...b"
        card = EvidenceCard(
            cluster_key="OpenAI:alpha",
            event_title="OpenAI Codex alpha 更新",
            entity="OpenAI",
            risk="green",
            confidence=90,
            selected=True,
            reason="官方来源确认。",
            source_count=1,
            official_count=1,
            media_count=0,
            community_count=0,
            first_seen_at=datetime(2026, 7, 18, tzinfo=timezone.utc),
            latest_published_at=datetime(2026, 7, 18, tzinfo=timezone.utc),
            key_facts=[f"来源摘要：版本对比：20 个提交；变更来源：{compare_url}"],
            evidence_links=[
                {
                    "source": "OpenAI Codex GitHub Releases",
                    "url": "https://github.com/openai/codex/releases/tag/b",
                    "excerpt": f"版本对比：20 个提交；变更来源：{compare_url}",
                }
            ],
            uncertainty=[],
            score=86.0,
        )

        self.assertEqual(attach_github_compare_evidence([card]), 0)
        self.assertEqual(len(card.evidence_links), 1)

    def test_enrich_records_verified_compare_contract(self):
        """只有 GitHub compare API 响应成功时才写入结构化补证字段。"""
        compare_url = "https://github.com/openai/codex/compare/a...b"
        item = FeedItem(
            "openai_codex_releases",
            "OpenAI Codex GitHub Releases",
            "A",
            "official",
            "b",
            "https://github.com/openai/codex/releases/tag/b",
            "codex-b",
            "Release b",
        )
        responses = [
            {"body": ""},
            [{"tag_name": "b"}, {"tag_name": "a"}],
            {
                "html_url": compare_url,
                "total_commits": 1,
                "files": [{"filename": "codex-rs/hooks.rs"}],
                "commits": [{"commit": {"message": "Fix quoted hook commands on Windows"}}],
            },
        ]

        with patch("briefing.github_release._get_json", side_effect=responses):
            updated = enrich_github_release_item(item)

        self.assertTrue(updated.raw["github_release_enriched"])
        self.assertTrue(updated.raw["github_compare_verified"])
        self.assertEqual(updated.raw["github_compare_url"], compare_url)
        self.assertIn("修复 Windows 引号 Hook 命令解析", updated.raw["github_compare_summary"])

    def test_enrich_marks_missing_release_404(self):
        response = Mock()
        response.status_code = 404
        error = requests.HTTPError(response=response)
        item = FeedItem(
            "modelscope_github_releases",
            "ModelScope GitHub Releases",
            "A",
            "official",
            "v1.38.1",
            "https://github.com/modelscope/modelscope/releases/tag/v1.38.1",
            "https://github.com/modelscope/modelscope/releases/tag/v1.38.1",
            "Release v1.38.1",
        )

        with patch("briefing.github_release._get_json", side_effect=error):
            updated = enrich_github_release_item(item)

        self.assertTrue(updated.raw["github_release_missing"])
        self.assertIn("404", updated.summary)


if __name__ == "__main__":
    unittest.main()

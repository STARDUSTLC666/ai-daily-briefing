import json
import unittest
from datetime import datetime, timezone
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from briefing.models import EvidenceCard
from briefing.editor import content_quality_profile, daily_trend_cards
from briefing.evidence_screenshots import _blocked_page_reason, _http_unusable_reason, evidence_requires_screenshot
from briefing.render import _build_script, _context_cards, _display_title, _fact_cards, _primary_evidence_visual, _script_news_type, _segments
from briefing.scoring import public_source_status, story_category
from briefing.social_signals import card_is_official_personnel_signal, card_is_official_social_signal


def hf_card() -> EvidenceCard:
    return EvidenceCard(
        cluster_key="hf-google-tabfm",
        event_title="Hugging Face 模型仓库更新：google/tabfm-1.0.0-jax",
        entity="Google",
        risk="green",
        confidence=88,
        selected=True,
        reason="官方来源确认，且发布时间在窗口内。",
        source_count=1,
        official_count=1,
        media_count=0,
        community_count=0,
        first_seen_at=datetime(2026, 7, 4, 7, 9, tzinfo=timezone.utc),
        latest_published_at=datetime(2026, 7, 4, 7, 9, tzinfo=timezone.utc),
        key_facts=[
            "Google 相关事件：Hugging Face 模型仓库更新：google/tabfm-1.0.0-jax",
            "首要来源发布时间：2026-07-04 07:09 UTC",
            "首要来源：Google Hugging Face Models",
            "来源摘要：pipeline=tabular-classification；tags=jax, tabular",
        ],
        evidence_links=[
            {
                "source": "Google Hugging Face Models",
                "tier": "A",
                "reliability": "official",
                "title": "Hugging Face 模型仓库更新：google/tabfm-1.0.0-jax",
                "url": "https://huggingface.co/google/tabfm-1.0.0-jax",
                "published_at": "2026-07-04T07:09:00+00:00",
            }
        ],
        uncertainty=[],
        score=75.0,
    )


def official_model_benchmark_card() -> EvidenceCard:
    card = hf_card()
    card.cluster_key = "qwen-official-benchmark"
    card.event_title = "Qwen3-Next 模型发布"
    card.entity = "Qwen / 阿里"
    card.key_facts = [
        "Qwen / 阿里 相关事件：Qwen3-Next 模型发布",
        "首要来源发布时间：2026-07-04 07:09 UTC",
        "首要来源：Qwen Official Blog",
        "来源摘要：官方文档称 Qwen3-Next 在 AIME 2025、GPQA Diamond 和 LiveCodeBench 等 benchmark 中对比上一代提升，部分指标超过 GPT-4.1。",
    ]
    card.evidence_links = [
        {
            "source": "Qwen Official Blog",
            "tier": "A",
            "reliability": "official",
            "title": "Qwen3-Next 模型发布",
            "url": "https://qwen.example/blog/qwen3-next",
            "published_at": "2026-07-04T07:09:00+00:00",
        }
    ]
    return card


def merged_hf_card() -> EvidenceCard:
    card = hf_card()
    card.cluster_key = "merged:deepseek-dspark"
    card.event_title = "Hugging Face 模型仓库更新：deepseek-ai/DeepSeek-V4-DSpark 系列（Flash / Pro）"
    card.entity = "DeepSeek"
    card.key_facts = [
        "同一模型系列同时出现 Flash / Pro 变体。",
        "DeepSeek 相关事件：Hugging Face 模型仓库更新：deepseek-ai/DeepSeek-V4-Flash-DSpark",
        "DeepSeek 相关事件：Hugging Face 模型仓库更新：deepseek-ai/DeepSeek-V4-Pro-DSpark",
        "首要来源：DeepSeek Hugging Face Models",
    ]
    card.evidence_links = [
        {
            "source": "DeepSeek Hugging Face Models",
            "tier": "A",
            "reliability": "official",
            "title": "Hugging Face 模型仓库更新：deepseek-ai/DeepSeek-V4-Flash-DSpark",
            "url": "https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-DSpark",
            "published_at": "2026-07-04T03:15:00+00:00",
        },
        {
            "source": "DeepSeek Hugging Face Models",
            "tier": "A",
            "reliability": "official",
            "title": "Hugging Face 模型仓库更新：deepseek-ai/DeepSeek-V4-Pro-DSpark",
            "url": "https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro-DSpark",
            "published_at": "2026-07-04T03:14:00+00:00",
        },
    ]
    return card


def long_media_roundup_card() -> EvidenceCard:
    return EvidenceCard(
        cluster_key="media-36kr-claude-code-roundup",
        event_title="9点1氪|阿里内部全面禁用Claude Code;FF洛杉矶总部人去楼空?公司回应:不实;微软砸25亿美元组建6000人AI新公司",
        entity="Anthropic / Claude",
        risk="yellow",
        confidence=72,
        selected=True,
        reason="媒体来源提到，需保守表述。",
        source_count=1,
        official_count=0,
        media_count=1,
        community_count=0,
        first_seen_at=datetime(2026, 7, 5, 1, 30, tzinfo=timezone.utc),
        latest_published_at=datetime(2026, 7, 5, 0, 15, tzinfo=timezone.utc),
        key_facts=[
            "Anthropic / Claude 相关事件：9点1氪|阿里内部全面禁用Claude Code;FF洛杉矶总部人去楼空?公司回应:不实;微软砸25亿美元组建6000人AI新公司",
            "首要来源发布时间：2026-07-05 00:15 UTC",
            "首要来源：36氪",
            "来源摘要：阿里内部全面禁用Claude Code；FF洛杉矶总部人去楼空？公司回应：不实；微软砸25亿美元组建6000人AI新公司；点击查看原文>",
            "关键信息：媒体称阿里内部已全面禁用 Claude Code，原因指向代码安全和数据合规；相关说法尚未看到阿里或 Anthropic 官方确认。",
        ],
        evidence_links=[
            {
                "source": "36氪",
                "tier": "B",
                "reliability": "media",
                "title": "9点1氪|阿里内部全面禁用Claude Code;FF洛杉矶总部人去楼空?公司回应:不实;微软砸25亿美元组建6000人AI新公司",
                "url": "https://www.36kr.com/",
                "published_at": "2026-07-05T00:15:00+00:00",
            }
        ],
        uncertainty=["目前只有媒体来源，未看到阿里或 Anthropic 官方确认。"],
        score=64.0,
    )


def sspai_claude_roundup_card() -> EvidenceCard:
    return EvidenceCard(
        cluster_key="sspai-claude-roundup",
        event_title="派早报:阿里禁用 Claude 模型",
        entity="Anthropic / Claude",
        risk="yellow",
        confidence=56,
        selected=True,
        reason="主流 AI 媒体线索，官方未明确，需保守措辞。",
        source_count=1,
        official_count=0,
        media_count=1,
        community_count=0,
        first_seen_at=datetime(2026, 7, 5, 23, 40, tzinfo=timezone.utc),
        latest_published_at=datetime(2026, 7, 5, 23, 40, tzinfo=timezone.utc),
        key_facts=[
            "Anthropic / Claude 相关事件：派早报:阿里禁用 Claude 模型",
            "首要来源发布时间：2026-07-05 23:40 UTC",
            "首要来源：少数派",
            "来源摘要：阿里禁用 Claude 模型 索尼调整计划,2028 年前发售游戏可继续生产光盘 千问、豆包将下线智能体功能 Android 反垄断案欧洲终审败诉",
        ],
        evidence_links=[
            {
                "source": "少数派",
                "tier": "B",
                "reliability": "media",
                "title": "派早报:阿里禁用 Claude 模型",
                "url": "https://sspai.com/post/111973",
                "published_at": "2026-07-05T23:40:00+00:00",
            }
        ],
        uncertainty=["尚未找到官方补证。"],
        score=58.0,
    )


def infoq_claude_spotify_card() -> EvidenceCard:
    return EvidenceCard(
        cluster_key="infoq-claude-spotify",
        event_title="73% PR 由AI生成！Claude Code之父对话Spotify：2900名工程师每天部署4500次，坐地铁都能提交代码",
        entity="Anthropic / Claude",
        risk="yellow",
        confidence=58,
        selected=True,
        reason="主流 AI 媒体线索，官方未明确，需保守措辞。",
        source_count=1,
        official_count=0,
        media_count=1,
        community_count=0,
        first_seen_at=datetime(2026, 7, 6, 2, 27, tzinfo=timezone.utc),
        latest_published_at=datetime(2026, 7, 6, 2, 27, tzinfo=timezone.utc),
        key_facts=[
            "Anthropic / Claude 相关事件：73% PR 由AI生成！Claude Code之父对话Spotify：2900名工程师每天部署4500次，坐地铁都能提交代码",
            "首要来源发布时间：2026-07-06 02:27 UTC",
            "首要来源：InfoQ 中文",
            "来源摘要：点击查看原文>",
        ],
        evidence_links=[
            {
                "source": "InfoQ 中文",
                "tier": "B",
                "reliability": "media",
                "title": "73% PR 由AI生成！Claude Code之父对话Spotify：2900名工程师每天部署4500次，坐地铁都能提交代码",
                "url": "https://www.infoq.cn/article/e6J4FbXQj28CiziktLuS",
                "published_at": "2026-07-06T02:27:32+00:00",
            }
        ],
        uncertainty=["尚未找到官方补证。"],
        score=59.0,
    )


def tencent_hy3_card() -> EvidenceCard:
    return EvidenceCard(
        cluster_key="tencent-hy3-release",
        event_title="腾讯混元Hy3正式发布",
        entity="腾讯混元",
        risk="yellow",
        confidence=58,
        selected=True,
        reason="主流 AI 媒体线索，官方未明确，需保守措辞。",
        source_count=1,
        official_count=0,
        media_count=1,
        community_count=0,
        first_seen_at=datetime(2026, 7, 6, 2, 27, tzinfo=timezone.utc),
        latest_published_at=datetime(2026, 7, 6, 2, 27, tzinfo=timezone.utc),
        key_facts=[
            "腾讯混元 相关事件: 腾讯混元Hy3正式发布",
            "首要来源发布时间：2026-07-06 02:27 UTC",
            "首要来源：36氪",
            "来源摘要：腾讯混元Hy3正式发布，来源是 36氪。",
        ],
        evidence_links=[
            {
                "source": "36氪",
                "tier": "B",
                "reliability": "media",
                "title": "腾讯混元Hy3正式发布",
                "url": "https://36kr.com/example",
                "published_at": "2026-07-06T02:27:32+00:00",
            }
        ],
        uncertainty=["尚未找到官方补证。"],
        score=58.0,
    )


def github_release_card() -> EvidenceCard:
    return EvidenceCard(
        cluster_key="openai-codex-release",
        event_title="OpenAI Codex GitHub Releases 发布 0.143.0-alpha.36",
        entity="OpenAI",
        risk="green",
        confidence=86,
        selected=True,
        reason="官方来源确认，且发布时间在窗口内。",
        source_count=1,
        official_count=1,
        media_count=0,
        community_count=0,
        first_seen_at=datetime(2026, 7, 5, 1, 5, tzinfo=timezone.utc),
        latest_published_at=datetime(2026, 7, 5, 1, 5, tzinfo=timezone.utc),
        key_facts=[
            "OpenAI 相关事件：0.143.0-alpha.36",
            "首要来源发布时间：2026-07-05 01:05 UTC",
            "首要来源：OpenAI Codex GitHub Releases",
            "来源摘要：版本对比：较 rust-v0.143.0-alpha.35，GitHub compare 显示 17 个提交、39 个文件变更；变更摘要：新增 multi-agent 模式提示配置；调整安装脚本的 release 元数据获取；补充安装脚本测试；修复/维护线索：调整安装脚本的 release 元数据获取；补充安装脚本测试；变更来源：https://github.com/openai/codex/compare/rust-v0.143.0-alpha.35...rust-v0.143.0-alpha.36",
        ],
        evidence_links=[
            {
                "source": "OpenAI Codex GitHub Releases",
                "tier": "A",
                "reliability": "official",
                "title": "0.143.0-alpha.36",
                "url": "https://github.com/openai/codex/releases/tag/rust-v0.143.0-alpha.36",
                "published_at": "2026-07-05T01:05:00+00:00",
            }
        ],
        uncertainty=[],
        score=72.0,
    )


def github_issue_feedback_card() -> EvidenceCard:
    card = github_release_card()
    card.cluster_key = "codex-issue-feedback"
    card.event_title = "GPT-5.5 Codex reasoning-token clustering may be leading to degraded performance"
    card.risk = "yellow"
    card.confidence = 50
    card.reason = "社区源头的主流 AI 观察线索，可进主视频但必须截图并保守措辞。"
    card.official_count = 0
    card.media_count = 0
    card.community_count = 1
    card.key_facts = [
        "OpenAI 相关事件：GPT-5.5 Codex reasoning-token clustering may be leading to degraded performance",
        "首要来源发布时间：2026-07-04 21:51 UTC",
        "首要来源：Hacker News Frontpage",
        "来源摘要：社区线索称 Codex reasoning-token clustering 可能带来 degraded performance。",
    ]
    card.evidence_links = [
        {
            "source": "Hacker News Frontpage",
            "tier": "C",
            "reliability": "community",
            "title": card.event_title,
            "url": "https://github.com/openai/codex/issues/30364",
            "published_at": "2026-07-04T21:51:09+00:00",
        }
    ]
    card.uncertainty = ["目前只有社区/聚合来源，不能写成官方确认。"]
    return card


def x_official_personnel_signal_card() -> EvidenceCard:
    return EvidenceCard(
        cluster_key="x-openai-codex-ultra",
        event_title="Tibo says Ultra will be in Codex",
        entity="OpenAI",
        risk="yellow",
        confidence=72,
        selected=True,
        reason="X 官方人员动态，可作为短讯入选；必须保留原帖截图并避免写成正式发布。",
        source_count=1,
        official_count=0,
        media_count=0,
        community_count=1,
        first_seen_at=datetime(2026, 7, 7, 1, 0, tzinfo=timezone.utc),
        latest_published_at=datetime(2026, 7, 7, 1, 0, tzinfo=timezone.utc),
        key_facts=[
            "OpenAI 相关事件：Tibo says Ultra will be in Codex",
            "首要来源发布时间：2026-07-07 01:00 UTC",
            "首要来源：X / Tibo",
            "来源摘要：OpenAI Codex 相关成员 Tibo 在 X 上转发称：Ultra will be in Codex。",
        ],
        evidence_links=[
            {
                "source": "X / Tibo",
                "tier": "C",
                "reliability": "official_personnel",
                "title": "Tibo says Ultra will be in Codex",
                "url": "https://x.com/thsottiaux/status/1940000000000000000",
                "published_at": "2026-07-07T01:00:00+00:00",
                "screenshot_required": "true",
                "screenshot_status": "captured",
                "screenshot_path": __file__,
            }
        ],
        uncertainty=["X 官方人员动态；用原帖截图呈现，不等同于正式发布页。"],
        score=66.0,
    )


def x_official_account_card() -> EvidenceCard:
    return EvidenceCard(
        cluster_key="x-openai-product-release",
        event_title="OpenAI 官方账号发布产品更新",
        entity="OpenAI",
        risk="green",
        confidence=94,
        selected=True,
        reason="OpenAI 官方 X 原帖确认。",
        source_count=2,
        official_count=1,
        media_count=1,
        community_count=0,
        first_seen_at=datetime(2026, 7, 13, 0, 30, tzinfo=timezone.utc),
        latest_published_at=datetime(2026, 7, 13, 0, 30, tzinfo=timezone.utc),
        key_facts=[
            "OpenAI 官方账号公布一项产品更新。",
            "该更新从今天起面向部分用户开放。",
        ],
        evidence_links=[
            {
                "source": "Industry News RSS Feed",
                "tier": "B",
                "reliability": "media",
                "title": "媒体转述 OpenAI 更新",
                "url": "https://media.example/openai-update",
            },
            {
                "source": "Codex verified X official account leads",
                "tier": "A",
                "reliability": "official_social",
                "title": "OpenAI 官方账号发布产品更新",
                "url": "https://x.com/OpenAI/status/1234567890123456789",
                "published_at": "2026-07-13T00:30:00+00:00",
                "screenshot_required": "true",
                "screenshot_status": "captured",
                "screenshot_path": __file__,
            },
        ],
        uncertainty=[],
        score=92.0,
    )


def nvidia_blackwell_card() -> EvidenceCard:
    return EvidenceCard(
        cluster_key="nvidia-blackwell-dflash",
        event_title="Boost Inference Performance up to 15x on NVIDIA Blackwell Using DFlash Speculative Decoding",
        entity="AI",
        risk="green",
        confidence=80,
        selected=True,
        reason="官方来源确认，且发布时间在窗口内。",
        source_count=1,
        official_count=1,
        media_count=0,
        community_count=0,
        first_seen_at=datetime(2026, 7, 6, 16, 15, tzinfo=timezone.utc),
        latest_published_at=datetime(2026, 7, 6, 16, 15, tzinfo=timezone.utc),
        key_facts=[
            "AI 相关事件：Boost Inference Performance up to 15x on NVIDIA Blackwell Using DFlash Speculative Decoding",
            "首要来源：NVIDIA Developer Blog",
            "来源摘要：As AI systems move from single-turn interactions to coordinated agent workloads, NVIDIA describes DFlash speculative decoding for Blackwell inference.",
        ],
        evidence_links=[
            {
                "source": "NVIDIA Developer Blog",
                "tier": "A",
                "reliability": "official",
                "title": "Boost Inference Performance up to 15x on NVIDIA Blackwell Using DFlash Speculative Decoding",
                "url": "https://developer.nvidia.com/blog/boost-inference-performance-up-to-15x-on-nvidia-blackwell-using-dflash-speculative-decoding/",
                "published_at": "2026-07-06T16:15:53+00:00",
            }
        ],
        uncertainty=[],
        score=73.0,
    )


def nvidia_flare_card() -> EvidenceCard:
    return EvidenceCard(
        cluster_key="nvidia-flare-auto-fl",
        event_title="Accelerating Federated Learning Research with AI Agents and NVIDIA FLARE Auto-FL",
        entity="AI",
        risk="green",
        confidence=82,
        selected=True,
        reason="官方来源确认，且发布时间在窗口内。",
        source_count=1,
        official_count=1,
        media_count=0,
        community_count=0,
        first_seen_at=datetime(2026, 7, 6, 18, 27, tzinfo=timezone.utc),
        latest_published_at=datetime(2026, 7, 6, 18, 27, tzinfo=timezone.utc),
        key_facts=[
            "AI 相关事件：Accelerating Federated Learning Research with AI Agents and NVIDIA FLARE Auto-FL",
            "首要来源：NVIDIA Developer Blog",
            "来源摘要：Federated learning (FL) research often begins with a deceptively simple question about how to train across distributed private datasets.",
        ],
        evidence_links=[
            {
                "source": "NVIDIA Developer Blog",
                "tier": "A",
                "reliability": "official",
                "title": "Accelerating Federated Learning Research with AI Agents and NVIDIA FLARE Auto-FL",
                "url": "https://developer.nvidia.com/blog/accelerating-federated-learning-research-with-ai-agents-and-nvidia-flare-auto-fl/",
                "published_at": "2026-07-06T18:27:00+00:00",
            }
        ],
        uncertainty=[],
        score=73.0,
    )


def media_markdown_image_card() -> EvidenceCard:
    return EvidenceCard(
        cluster_key="qbit-seedance-media",
        event_title="字节Seedance，继续占据榜首",
        entity="字节跳动",
        risk="yellow",
        confidence=68,
        selected=True,
        reason="主流 AI 媒体线索，可进主视频但需保守措辞。",
        source_count=1,
        official_count=0,
        media_count=1,
        community_count=0,
        first_seen_at=datetime(2026, 7, 6, 18, 27, tzinfo=timezone.utc),
        latest_published_at=datetime(2026, 7, 6, 18, 27, tzinfo=timezone.utc),
        key_facts=[
            "字节跳动 相关事件：字节Seedance，继续占据榜首",
            "首要来源：量子位",
            "来源摘要：该动态样本充足；[](https://i.qbitai.com/wp-content/uploads/2026/07/example.png)",
        ],
        evidence_links=[
            {
                "source": "量子位",
                "tier": "B",
                "reliability": "media",
                "title": "字节Seedance，继续占据榜首",
                "url": "https://www.qbitai.com/example",
                "published_at": "2026-07-06T18:27:00+00:00",
                "screenshot_status": "captured",
                "screenshot_path": __file__,
            }
        ],
        uncertainty=[],
        score=66.0,
    )


def qwen_vllm_community_card() -> EvidenceCard:
    return EvidenceCard(
        cluster_key="qwen-vllm-community",
        event_title="Qwen 3.6 27B - VLLM Performance Benchmark Results (BF16, FP8, NVFP4)",
        entity="Qwen / 阿里",
        risk="yellow",
        confidence=53,
        selected=True,
        reason="社区源头的主流 AI 观察线索，可进主视频但必须截图并保守措辞。",
        source_count=1,
        official_count=0,
        media_count=0,
        community_count=1,
        first_seen_at=datetime(2026, 7, 5, 14, 6, tzinfo=timezone.utc),
        latest_published_at=datetime(2026, 7, 5, 14, 6, tzinfo=timezone.utc),
        key_facts=[
            "Qwen / 阿里 相关事件：Qwen 3.6 27B - VLLM Performance Benchmark Results (BF16, FP8, NVFP4)",
            "首要来源发布时间：2026-07-05 14:06 UTC",
            "首要来源：Reddit LocalLLaMA",
            "来源摘要：Sharing some testing of Qwen 3.6 27B using VLLM across BF16, FP8 and NVFP4. While NVFP4 is blazing fast, have had looping issues. Based on these results, FP8 seems to be the right choice.",
        ],
        evidence_links=[
            {
                "source": "Reddit LocalLLaMA",
                "tier": "C",
                "reliability": "community",
                "title": "Qwen 3.6 27B - VLLM Performance Benchmark Results (BF16, FP8, NVFP4)",
                "url": "https://www.reddit.com/r/LocalLLaMA/comments/example/qwen_36/",
                "published_at": "2026-07-05T14:06:23+00:00",
            }
        ],
        uncertainty=["目前只有社区/聚合来源，不能写成官方确认。"],
        score=62.0,
    )


class EditorCardTests(unittest.TestCase):
    def test_hf_title_does_not_overstate_publish(self):
        title = _display_title(hf_card())
        self.assertIn("模型页更新", title)
        self.assertNotIn("发布", title)

    def test_news_cards_use_reader_facing_structure(self):
        cards = _fact_cards(hf_card())
        self.assertEqual([c["title"] for c in cards], ["模型名称", "支持任务", "编辑判断"])
        joined = "\n".join(c["body"] for c in cards)
        self.assertNotIn("页面已经出现更新", joined)
        self.assertNotIn("官方页面已更新这个模型条目", joined)
        self.assertNotIn("补充信息", joined)
        self.assertNotIn("正式发布", joined)
        self.assertNotIn("已经开源", joined)
        self.assertIn("tabular-classification", joined)
        self.assertNotIn("可以重点问", joined)
        self.assertNotIn("重点是能否", joined)

    def test_media_release_cards_do_not_repeat_content_and_point(self):
        cards = _fact_cards(tencent_hy3_card())
        titles = [c["title"] for c in cards]
        self.assertEqual(titles, ["核心看点", "核心能力", "开放情况", "编辑判断"])
        self.assertNotIn("内容", titles)
        self.assertNotIn("要点", titles)
        self.assertNotEqual(cards[1]["body"], cards[2]["body"])
        joined = "\n".join(c["body"] for c in cards)
        self.assertIn("参数", joined)
        self.assertIn("入口", joined)
        self.assertIn("价格", joined)


    def test_media_model_release_keeps_concrete_cross_source_details(self):
        card = tencent_hy3_card()
        card.source_count = 2
        card.media_count = 2
        card.key_facts.append(
            "来源摘要：Hy3 已在 WorkBuddy/CodeBuddy、元宝、Marvis、ima 等业务接入；API 已在腾讯云 TokenHub 上线；元宝上线 Hy3 Agent 能力并免费开放。"
        )
        card.evidence_links.append(
            {
                "source": "InfoQ 中文",
                "tier": "B",
                "reliability": "media",
                "title": "腾讯混元Hy3正式发布，元宝同步上线Hy3 Agent能力、免费开放",
                "url": "https://www.infoq.cn/hy3",
                "published_at": "",
            }
        )

        cards = _fact_cards(card)
        joined = "\n".join(c["body"] for c in cards)
        script = _build_script(card)

        self.assertIn("元宝/API", joined)
        self.assertIn("WorkBuddy/CodeBuddy", joined)
        self.assertIn("TokenHub", joined)
        self.assertIn("元宝上线 Hy3 Agent", script)
        self.assertIn("API 已在腾讯云 TokenHub 上线", script)
        self.assertNotIn("参数、入口、价格未公开", joined)

    def test_model_release_keeps_release_slots_even_with_developer_terms(self):
        card = tencent_hy3_card()
        card.key_facts[-1] = "来源摘要：腾讯混元Hy3正式发布，面向游戏开发者和 3D 工作流。"
        cards = _fact_cards(card)
        self.assertEqual([c["title"] for c in cards], ["核心看点", "核心能力", "开放情况", "编辑判断"])
        self.assertNotIn("功能方法", [c["title"] for c in cards])

    def test_talent_story_uses_industry_slots_not_model_release(self):
        card = tencent_hy3_card()
        card.cluster_key = "deepseek-talent"
        card.event_title = "高考生填志愿前，都该读一遍DeepSeek的招聘帖"
        card.entity = "DeepSeek"
        card.key_facts = [
            "DeepSeek 相关事件：高考生填志愿前，都该读一遍DeepSeek的招聘帖",
            "首要来源：爱范儿",
            "来源摘要：媒体借 DeepSeek 招聘帖讨论 AI 人才方向和职业选择。",
        ]
        card.evidence_links[0]["source"] = "爱范儿"
        card.evidence_links[0]["title"] = card.event_title

        titles = [c["title"] for c in _fact_cards(card)]

        self.assertIn("公司", titles)
        self.assertIn("事件", titles)
        self.assertIn("编辑判断", titles)
        self.assertNotIn("一句话", titles)

    def test_merged_model_variants_are_explained_as_one_story(self):
        cards = _fact_cards(merged_hf_card())
        joined = "\n".join(c["body"] for c in cards)
        self.assertIn("DeepSeek-V4-DSpark", joined)
        self.assertIn("Flash/Pro", joined)
        self.assertNotIn("正式发布", joined)
        self.assertNotIn("记录 Pro)", joined)

    def test_script_uses_news_analysis_not_homework_prompt(self):
        script = _build_script(hf_card())
        self.assertNotIn("\u53ef\u4ee5\u91cd\u70b9\u95ee", script)
        self.assertNotIn("\u63a5\u4e0b\u6765\u8fd9\u6761\u770b", script)
        self.assertNotIn("\u8fd9\u6761\u4e3b\u8981\u662f", script)
        self.assertNotIn("\u771f\u6b63\u8981\u770b", script)
        self.assertIn("模型已经开放下载", script)
        self.assertNotIn("编辑判断", script)
        self.assertLessEqual(script.count("。"), 3)

    def test_script_confirmation_avoids_repeating_title(self):
        script = _build_script(tencent_hy3_card())

        self.assertIn("36\u6c2a\u79f0", script)
        self.assertIn("\u817e\u8baf\u6df7\u5143Hy3\u6b63\u5f0f\u53d1\u5e03", script)
        self.assertIn("开放入口、价格和完整参数", script)
        self.assertIn("入口、价格和完整参数", script)
        self.assertNotIn("编辑判断", script)
        self.assertNotIn("\u63a5\u4e0b\u6765\u8fd9\u6761\u770b", script)
        self.assertNotIn("\u8fd9\u6761\u4e3b\u8981\u662f", script)
        self.assertNotIn("\u53ef\u4ee5\u786e\u8ba4\u7684\u662f", script)
        self.assertNotIn("\u771f\u6b63\u8981\u770b", script)
        self.assertNotIn("官方补证", script)
        self.assertNotIn("待确认线索", script)
        self.assertLessEqual(script.count("。"), 4)
        self.assertNotIn("\u5df2\u786e\u8ba4\uff1a", script)
        self.assertNotIn("\u5df2\u786e\u8ba4:", script)
        self.assertNotIn("\u53ef\u4ee5\u786e\u8ba4\u7684\u662f\uff1a\u817e\u8baf\u6df7\u5143Hy3\u6b63\u5f0f\u53d1\u5e03", script)
        self.assertNotIn("\u53ef\u4ee5\u786e\u8ba4\u7684\u662f:\u817e\u8baf\u6df7\u5143Hy3\u6b63\u5f0f\u53d1\u5e03", script)
        self.assertNotIn("\u7b80\u5355\u8bf4\uff0c", script)
        self.assertNotIn("\u7b80\u5355\u8bf4,", script)

    def test_scripts_follow_editor_in_chief_rules(self):
        scripts = [
            _build_script(card)
            for card in [
                hf_card(),
                tencent_hy3_card(),
                github_release_card(),
                infoq_claude_spotify_card(),
                qwen_vllm_community_card(),
            ]
        ]
        joined = "\n".join(scripts)
        forbidden = [
            "后续继续观察",
            "来源可查",
            "值得期待",
            "官方补证",
            "重点关注",
            "持续关注",
            "具体以官方为准",
            "等待更多消息",
            "如有更新第一时间通知",
            "欢迎关注",
            "微信公众号",
            "更多精彩内容",
        ]
        for phrase in forbidden:
            self.assertNotIn(phrase, joined)
        self.assertTrue(all("编辑判断" not in script for script in scripts))
        self.assertLessEqual(joined.count("对用户来说"), 2)
        self.assertLessEqual(joined.count("值得关注"), 2)
        self.assertGreater(max(len(script) for script in scripts) - min(len(script) for script in scripts), 40)

    def test_script_strips_media_promo_tail(self):
        card = tencent_hy3_card()
        card.cluster_key = "ifanr-codex-token"
        card.event_title = "Codex 省 Token 方法被媒体实测"
        card.entity = "OpenAI / Codex"
        card.key_facts = [
            "OpenAI / Codex 相关事件：Codex 省 Token 方法被媒体实测",
            "首要来源：爱范儿",
            "来源摘要：能省，但只能省一点点 #欢迎关注爱范儿官方微信公众号：爱范儿（微信号：ifanr），更多精彩内容第一时间为您奉上。",
        ]
        card.evidence_links[0].update({"source": "爱范儿", "title": card.event_title})

        script = _build_script(card)

        self.assertIn("幅度有限", script)
        self.assertIn("开发者", script)
        self.assertNotIn("欢迎关注", script)
        self.assertNotIn("微信公众号", script)
        self.assertNotIn("更多精彩内容", script)

    def test_nvidia_english_blog_is_localized_for_chinese_audience(self):
        script = _build_script(nvidia_blackwell_card())
        cards = _fact_cards(nvidia_blackwell_card())
        visible = script + "\n" + "\n".join(card["body"] for card in cards)

        self.assertIn("NVIDIA Blackwell", script)
        self.assertIn("DFlash", script)
        self.assertIn("推理", script)
        self.assertNotIn("Boost Inference Performance", visible)
        self.assertNotIn("As AI systems move", visible)
        self.assertNotIn("speculative decoding", visible)

    def test_non_arxiv_blog_is_not_mislabeled_as_paper_from_entity_text(self):
        card = nvidia_blackwell_card()
        card.entity = "论文 / arXiv"

        self.assertNotEqual(story_category(card), "research")
        self.assertNotEqual(_script_news_type(card, _display_title(card)), "paper")

    def test_nvidia_flare_auto_fl_title_does_not_leak_raw_english(self):
        card = nvidia_flare_card()
        script = _build_script(card)
        rows = _segments([card])
        visible = json.dumps(rows, ensure_ascii=False) + "\n" + script

        self.assertIn("FLARE Auto-FL", _display_title(card))
        self.assertIn("联邦学习", _display_title(card))
        self.assertIn("AI Agent", script)
        self.assertIn("联邦学习", script)
        self.assertNotIn("Accelerating Federated Learning Research", visible)
        self.assertNotIn("Federated learning (FL) research", visible)

    def test_markdown_image_links_are_not_used_as_visible_card_text(self):
        card = media_markdown_image_card()
        script = _build_script(card)
        cards = _fact_cards(card)
        rows = _segments([card])
        visible = json.dumps(rows, ensure_ascii=False) + "\n" + script + "\n" + json.dumps(cards, ensure_ascii=False)

        self.assertIn("样本充足", visible)
        self.assertNotIn("[](https://", visible)
        self.assertNotIn("wp-content/uploads", visible)

    def test_spoken_copy_never_ends_with_source_ellipsis_shard(self):
        card = github_release_card()
        card.key_facts = [
            "来源摘要：Make plugin guidance react...；修复 cancelled review leaving stale state；新增 interleaved response handling。"
        ]

        script = _build_script(card)

        self.assertNotIn("...", script)
        self.assertNotRegex(script, r"[A-Za-z]{2,}\.$")

    def test_intro_previews_actual_selected_stories_without_workflow_copy(self):
        rows = _segments([tencent_hy3_card(), github_release_card()])

        intro_cards = "\n".join(item["title"] + item["body"] for item in rows[0]["cards"])
        self.assertIn("Hy3", intro_cards)
        self.assertIn("Codex", intro_cards)
        # Cold open: the first spoken sentence is the strongest verified story,
        # then the greeting plus story count.
        intro_text = rows[0]["text"]
        self.assertFalse(intro_text.startswith("各位观众"))
        self.assertIn("——早上好，这里是AI 日报，今天 2 条，马上开始。", intro_text)
        self.assertNotIn("来源", intro_cards)
        self.assertNotIn("核验", intro_cards)
        self.assertNotIn("下面请看具体内容", intro_text)

    def test_github_copilot_model_rollout_uses_product_subject(self):
        card = github_release_card()
        card.event_title = "OpenAI’s GPT-5.6 Sol, Terra, and Luna are now available in GitHub Copilot"
        card.entity = "GitHub / Copilot"
        card.evidence_links = [
            {"source": "GitHub Changelog", "tier": "A", "reliability": "official", "title": card.event_title, "url": "https://github.blog/changelog/gpt-5-6-copilot"}
        ]
        card.key_facts = ["GitHub / Copilot 相关事件：OpenAI’s GPT-5.6 Sol, Terra, and Luna are now available in GitHub Copilot"]

        script = _build_script(card)

        self.assertTrue(script.startswith("GitHub Copilot 接入 GPT-5.6"))
        self.assertNotIn("OpenAI有一条新动态", script)

    def test_chatgpt_work_story_uses_event_specific_chinese_copy(self):
        card = github_release_card()
        card.event_title = "ChatGPT is now a partner for your most ambitious work"
        card.entity = "OpenAI"
        card.evidence_links = [
            {"source": "OpenAI News", "tier": "A", "reliability": "official", "title": card.event_title, "url": "https://openai.com/index/chatgpt-work"}
        ]
        card.key_facts = [
            "OpenAI 相关事件：ChatGPT is now a partner for your most ambitious work",
            "来源摘要：ChatGPT Work is an agent that can take action across your apps and files；要开始使用 ChatGPT Work,请通过插件连接你已经开展工作的工具和上下文；ChatGPT Work 从今天起在网页端和移动端推出,首先面向 Pro、Enterprise 和 Edu 用户。",
        ]

        rows = _segments([card])
        script = rows[1]["text"]

        self.assertIn("OpenAI 上线 ChatGPT Work", rows[1]["title"])
        self.assertIn("通过插件连接应用和文件", script)
        self.assertIn("跨应用执行工作", script)
        self.assertNotIn("ChatGPT Work is an agent", script)
        self.assertNotIn("NVIDIA AI Agent", script)

    def test_soft_limit_does_not_cut_ascii_word_in_half(self):
        from briefing.render import _soft_limit

        result = _soft_limit("GPT-5.6 引入程序化工具调用 Programmatic Tool Calling 并支持复杂任务", 45)

        self.assertNotIn("Programmatic Tool Cal…", result)
        self.assertNotIn("…", result)
        self.assertNotIn("...", result)
        self.assertNotRegex(result, r"Programmatic Tool Cal$")

    def test_intro_does_not_cut_an_english_word_in_half(self):
        card = github_release_card()
        card.event_title = "Gemma 4 Technical Report With Long Context Results"
        rows = _segments([card])

        self.assertNotIn("Repo、", rows[0]["text"])
        self.assertNotIn("Repo。", rows[0]["text"])

    def test_generic_performance_caution_is_not_misread_as_a_benchmark(self):
        card = tencent_hy3_card()
        card.key_facts = [
            "MiniMax M3",
            "性能榜单要看测试口径、对手版本、上下文长度和推理成本。",
        ]

        script = _build_script(card)

        self.assertNotIn("官方性能说明", script)
        self.assertLessEqual(script.count("性能榜单要看测试口径"), 1)

    def test_github_release_script_leads_with_change(self):
        script = _build_script(github_release_card())

        self.assertIn("\u66f4\u65b0\u4e86", script)
        self.assertIn("主要改动", script)
        self.assertIn("multi-agent", script)
        self.assertIn("开发者", script)
        self.assertIn("测试环境", script)
        self.assertNotIn("GitHub c", script)
        self.assertNotIn("变更摘要", script)
        self.assertNotIn("来源可查", script)
        self.assertNotIn("后续看实际入口和使用反馈", script)
        self.assertNotIn("\u63a5\u4e0b\u6765\u8fd9\u6761\u770b", script)
        self.assertNotIn("\u8fd9\u6761\u4e3b\u8981\u662f", script)
        self.assertNotIn("\u53ef\u4ee5\u786e\u8ba4\u7684\u662f", script)
        self.assertNotIn("\u771f\u6b63\u8981\u770b", script)

    def test_official_model_benchmark_is_summarized(self):
        cards = _fact_cards(official_model_benchmark_card())
        joined = "\n".join(c["body"] for c in cards)

        self.assertIn("AIME", joined)
        self.assertIn("GPQA", joined)
        self.assertIn("LiveCodeBench", joined)
        self.assertNotIn("只说明模型条目活跃", joined)

        script = _build_script(official_model_benchmark_card())
        self.assertIn("官方文档称", script)
        self.assertIn("benchmark", script)
        self.assertFalse(evidence_requires_screenshot(official_model_benchmark_card()))

    def test_content_quality_profile_tracks_official_specific_news(self):
        profile = content_quality_profile(official_model_benchmark_card())

        self.assertGreaterEqual(profile["score"], 45)
        self.assertIn("official", profile["notes"])
        self.assertIn("performance", profile["notes"])
        self.assertIn("specific", profile["notes"])
        self.assertGreaterEqual(profile["specificity"], 40)

    def test_daily_trend_cards_show_news_context_not_backend_scoring(self):
        cards = daily_trend_cards([official_model_benchmark_card(), infoq_claude_spotify_card()])
        titles = [card["title"] for card in cards]
        joined = "\n".join(card["title"] + card["body"] for card in cards)

        self.assertIn("今日主线", titles)
        self.assertIn("重点对象", titles)
        self.assertTrue("性能线索" in titles or "具体变化" in titles)
        self.assertNotIn("可信度", joined)
        self.assertNotIn("评分", joined)
        self.assertNotIn("工作流", joined)

    def test_github_release_explains_delta_not_only_publish(self):
        cards = _fact_cards(github_release_card())
        joined = "\n".join(c["body"] for c in cards)
        self.assertIn("较上一版", joined)
        self.assertIn("17 个提交", joined)
        self.assertIn("multi-agent", joined)
        self.assertIn("安装脚本", joined)
        self.assertIn("维护", joined)
        self.assertNotIn("升级成本", joined)
        self.assertNotIn("兼容性", joined)
        self.assertNotIn("维护负担", joined)
        self.assertNotIn("changelog", joined.lower())
        self.assertNotIn("issue", joined.lower())

        script = _build_script(github_release_card())
        self.assertIn("较上一版", script)
        self.assertIn("multi-agent", script)
        self.assertIn("主要改动", script)
        self.assertNotIn("主要更新或修复", script)
        self.assertNotIn("修复/维护线索", script)
        self.assertNotIn("工作流", script)

    def test_github_release_raw_english_changelog_gets_chinese_point(self):
        card = github_release_card()
        card.event_title = "ModelScope GitHub Releases 发布 v1.38.1"
        card.entity = "ModelScope / 阿里"
        card.key_facts = [
            "ModelScope / 阿里 相关事件：v1.38.1",
            "首要来源发布时间：2026-07-06 03:41 UTC",
            "首要来源：ModelScope GitHub Releases",
            "来源摘要：What's Changed [Backport] Docker fixes from release/1.38 by @a in #1 add property session in HubApi by @b in #2 Fix release workflow hub dependency by @c in #3 Full Changelog: v1.38.0...v1.38.1",
        ]

        joined = "\n".join(c["body"] for c in _fact_cards(card))

        self.assertIn("Docker", joined)
        self.assertIn("HubApi", joined)
        self.assertNotIn("What's Changed", joined)
        self.assertNotIn("workflow", joined.lower())
        self.assertNotIn("changelog", joined.lower())

    def test_github_issue_is_not_treated_as_release(self):
        cards = _fact_cards(github_issue_feedback_card())
        joined = "\n".join(c["body"] for c in cards)
        self.assertNotIn("较上一版", joined)
        self.assertNotIn("发布记录可回溯", joined)

        script = _build_script(github_issue_feedback_card())
        self.assertNotIn("GitHub 发布记录", script)
        self.assertIn("Hacker News Frontpage", script)

    def test_performance_feedback_story_requires_source_screenshot(self):
        card = hf_card()
        card.official_count = 0
        card.community_count = 1
        card.evidence_links[0]["tier"] = "C"
        card.evidence_links[0]["reliability"] = "community"
        card.event_title = "LMArena 榜单显示某模型性能提升"
        card.key_facts = ["用户反馈称新模型响应更快，榜单得分也有变化。"]

        self.assertTrue(evidence_requires_screenshot(card))

    def test_yellow_nonofficial_story_requires_source_screenshot(self):
        card = hf_card()
        card.risk = "yellow"
        card.official_count = 0
        card.media_count = 0
        card.community_count = 1

        self.assertTrue(evidence_requires_screenshot(card))

    def test_blocked_evidence_pages_are_not_valid_screenshots(self):
        self.assertIn("blocked", _blocked_page_reason("You've been blocked by network security."))
        self.assertIn("page not found", _blocked_page_reason("Page not found · GitHub"))
        self.assertIn("web page", _blocked_page_reason("404 This is not the web page you are looking for."))

    def test_http_404_evidence_pages_are_not_valid_screenshots(self):
        response = Mock()
        response.status_code = 404
        response.close = Mock()

        with patch("briefing.evidence_screenshots.requests.get", return_value=response):
            self.assertEqual(_http_unusable_reason("https://github.com/modelscope/modelscope/releases/tag/v1.38.1", 8), "http_404")

        response.close.assert_called_once()

    def test_primary_evidence_visual_uses_captured_screenshot(self):
        with tempfile.TemporaryDirectory() as td:
            screenshot = Path(td) / "source.png"
            screenshot.write_bytes(b"fake-image")
            card = hf_card()
            card.evidence_links[0]["screenshot_required"] = "true"
            card.evidence_links[0]["screenshot_status"] = "captured"
            card.evidence_links[0]["screenshot_path"] = str(screenshot)

            visual = _primary_evidence_visual(card)

            self.assertIsNotNone(visual)
            self.assertEqual(visual["image"], str(screenshot))
            self.assertTrue(visual["required"])

    def test_missing_screenshot_does_not_create_evidence_page(self):
        card = hf_card()
        card.evidence_links[0]["screenshot_status"] = "failed"
        card.evidence_links[0]["screenshot_path"] = ""

        rows = _segments([card])
        news_rows = [row for row in rows if row.get("kind") == "news"]
        self.assertTrue(news_rows)
        page_kinds = [page.get("kind") for page in news_rows[0].get("visual_pages", [])]
        self.assertNotIn("evidence", page_kinds)

    def test_context_page_adds_background_without_score(self):
        rows = _segments([hf_card()])
        news_rows = [row for row in rows if row.get("kind") == "news"]
        pages = news_rows[0].get("visual_pages", [])
        context = next(page for page in pages if page.get("title") == "背景与讨论")
        joined = "\n".join(card.get("title", "") + card.get("body", "") for card in context.get("cards", []))

        self.assertIn("背景解释", joined)
        self.assertIn("模型仓库更新只说明页面或文件出现变化", joined)
        self.assertNotIn("选题分", joined)

    def test_generic_media_process_context_does_not_create_a_video_page(self):
        with patch(
            "briefing.render.horizon_enrichment",
            return_value={
                "background": "媒体报道适合提示方向，涉及公司内部决策或未公开产品时仍需等官方确认。",
                "community_discussion": "",
                "source_note": "某媒体；媒体线索。",
            },
        ):
            cards = _context_cards(hf_card())

        self.assertEqual(cards, [])

    def test_community_context_page_adds_discussion(self):
        rows = _segments([qwen_vllm_community_card()])
        news_rows = [row for row in rows if row.get("kind") == "news"]
        pages = news_rows[0].get("visual_pages", [])
        context = next(page for page in pages if page.get("title") == "背景与讨论")
        joined = "\n".join(card.get("title", "") + card.get("body", "") for card in context.get("cards", []))

        self.assertIn("社区讨论", joined)
        self.assertIn("实测性能", joined)
        self.assertNotIn("选题分", joined)

    def test_long_media_roundup_focuses_on_ai_story(self):
        card = long_media_roundup_card()
        self.assertEqual(_display_title(card), "阿里内部全面禁用Claude Code")

        cards = _fact_cards(card)
        joined = "\n".join(c["body"] for c in cards)
        self.assertIn("代码安全", joined)
        self.assertIn("数据合规", joined)
        self.assertIn("编辑判断", "\n".join(c["title"] for c in cards))
        self.assertNotIn("FF洛杉矶", joined)
        self.assertNotIn("微软砸25亿美元", joined)
        self.assertNotIn("点击查看原文", joined)
        self.assertNotIn("9点1氪", joined)

        script = _build_script(card)
        self.assertIn("阿里内部全面禁用Claude Code", script)
        self.assertIn("代码安全", script)
        self.assertNotIn("FF洛杉矶", script)
        self.assertNotIn("微软砸25亿美元", script)
        self.assertNotIn("可以重点问", script)

    def test_media_roundup_does_not_drag_unrelated_news_into_cards(self):
        cards = _fact_cards(sspai_claude_roundup_card())
        joined = "\n".join(c["body"] for c in cards)
        self.assertIn("阿里", joined)
        self.assertIn("Claude", joined)
        self.assertNotIn("索尼", joined)
        self.assertNotIn("Android", joined)

    def test_infoq_claude_case_uses_specific_news_cards(self):
        card = infoq_claude_spotify_card()
        self.assertEqual(_display_title(card), "Spotify 团队谈 Claude Code 落地")

        cards = _fact_cards(card)
        joined = "\n".join(c["body"] for c in cards)
        self.assertIn("Spotify", joined)
        self.assertIn("PR", joined)
        self.assertIn("4500", joined)
        self.assertNotIn("只采用已核对来源里的确定信息", joined)

        script = _build_script(card)
        self.assertIn("Claude Code", script)
        self.assertIn("部署", script)
        self.assertNotIn("这条值得关注的是它可能带来的实际使用变化", script)

    def test_community_benchmark_is_rewritten_for_chinese_audience(self):
        card = qwen_vllm_community_card()
        self.assertIn("社区实测", _display_title(card))

        cards = _fact_cards(card)
        joined = "\n".join(c["body"] for c in cards)
        self.assertIn("FP8", joined)
        self.assertIn("NVFP4", joined)
        self.assertIn("只代表该用户环境", joined)
        self.assertNotIn("Sharing some testing", joined)

        script = _build_script(card)
        self.assertIn("社区实测", script)
        self.assertIn("不是官方 benchmark", script)
        self.assertNotIn("Sharing some testing", script)

    def test_x_official_personnel_signal_is_short_screenshot_news(self):
        card = x_official_personnel_signal_card()
        card.editorial_tier = "brief"

        self.assertTrue(evidence_requires_screenshot(card))
        self.assertEqual(story_category(card), "official_personnel_signal")
        self.assertEqual(public_source_status(card), "X 官方人员动态")

        cards = _fact_cards(card)
        titles = [item["title"] for item in cards]
        joined_cards = "\n".join(item["title"] + item["body"] for item in cards)
        self.assertIn("原帖内容", titles)
        self.assertIn("编辑判断", titles)
        self.assertIn("Ultra 会进入 Codex", joined_cards)
        self.assertNotIn("测试条件", joined_cards)
        self.assertNotIn("爬虫", joined_cards)
        self.assertNotIn("筛选", joined_cards)

        script = _build_script(card)
        self.assertIn("Tibo 在 X 上透露", script)
        self.assertIn("Ultra 会进入 Codex", script)
        self.assertLessEqual(script.count("。"), 3)
        for forbidden in ["官网", "爬虫", "筛选", "正式发布", "官方补证", "待确认线索处理"]:
            self.assertNotIn(forbidden, script)

        segment = next(row for row in _segments([card]) if row.get("kind") == "news")
        self.assertEqual(segment["active_tab"], "一线消息")
        self.assertTrue(segment["visual_pages"][0]["evidenceVisual"]["required"])
        self.assertGreaterEqual(len(segment["visual_pages"]), 2)
        public_cards = "\n".join(
            item.get("title", "") + item.get("body", "")
            for page in segment["visual_pages"]
            for item in page.get("cards", [])
        )
        self.assertNotIn("编辑判断", public_cards)
        self.assertNotIn("截图作为来源", public_cards)
        self.assertNotIn("画面保留", public_cards)

    def test_x_official_account_is_first_party_and_uses_original_post_visual(self):
        card = x_official_account_card()

        self.assertTrue(card_is_official_social_signal(card))
        self.assertFalse(card_is_official_personnel_signal(card))
        self.assertTrue(evidence_requires_screenshot(card))
        self.assertEqual(public_source_status(card), "X 官方账号")

        visual = _primary_evidence_visual(card)
        self.assertIsNotNone(visual)
        self.assertEqual(visual["source"], "X / @OpenAI")
        self.assertEqual(visual["url"], "https://x.com/OpenAI/status/1234567890123456789")

        segment = next(row for row in _segments([card]) if row.get("kind") == "news")
        self.assertEqual(segment["visual_pages"][0]["source"], "X / @OpenAI")
        self.assertTrue(segment["visual_pages"][0]["evidenceVisual"]["required"])


if __name__ == "__main__":
    unittest.main()

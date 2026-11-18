from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from briefing.core import (
    UTC,
    EvidenceCard,
    FeedItem,
    PipelineLock,
    RunState,
    Source,
    SourceHealth,
    canonical_url,
    card_display_entity,
    card_is_community_signal,
    card_is_official_personnel_signal,
    card_is_official_social_signal,
    clause_truncate,
    clean_text,
    connect,
    decode_x_snowflake_utc,
    detect_entity,
    effective_config_sha256,
    extract_hf_model_id,
    extract_model_names,
    format_mmss,
    has_cjk,
    infer_brand,
    insert_health,
    insert_items,
    is_ai_relevant,
    load_config,
    load_recent_items,
    load_sources,
    load_story_history,
    looks_raw_english,
    model_family_key,
    normalize_cn_punctuation,
    parse_date,
    parse_x_status,
    pid_alive,
    project_root,
    prune_runs,
    read_json,
    save_evidence_cards,
    social_origin_evidence_indexes,
    strip_html,
    upsert_sources,
    write_json_atomic,
)


def _make_card(
    cluster_key: str = "openai:gpt-5",
    *,
    event_title: str = "OpenAI 发布 GPT-5 模型",
    entity: str = "OpenAI",
    risk: str = "green",
    confidence: int = 90,
    selected: bool = True,
    official_count: int = 1,
    media_count: int = 0,
    community_count: int = 0,
    key_facts: list[str] | None = None,
    evidence_links: list[dict[str, str]] | None = None,
) -> EvidenceCard:
    now = datetime(2026, 7, 3, 8, 0, tzinfo=UTC)
    links = evidence_links if evidence_links is not None else [
        {
            "source": "OpenAI News",
            "tier": "A",
            "reliability": "official",
            "title": event_title,
            "url": "https://openai.com/index/gpt-5",
        }
    ]
    return EvidenceCard(
        cluster_key=cluster_key,
        event_title=event_title,
        entity=entity,
        risk=risk,
        confidence=confidence,
        selected=selected,
        reason="unit_test",
        source_count=max(1, len(links)),
        official_count=official_count,
        media_count=media_count,
        community_count=community_count,
        first_seen_at=now,
        latest_published_at=now,
        key_facts=key_facts if key_facts is not None else ["OpenAI 正式发布 GPT-5 模型"],
        evidence_links=links,
        uncertainty=[],
        score=88.5,
    )


class ConfigTests(unittest.TestCase):
    def test_load_default_and_public_web_profile_inheritance(self) -> None:
        base_cfg = load_config(project_root() / "sources.yaml")
        self.assertIsInstance(base_cfg, dict)
        self.assertTrue(base_cfg.get("sources"))

        pub_path = project_root() / "sources.public-web.json"
        pub_cfg = load_config(pub_path)
        self.assertEqual(pub_cfg.get("profile"), "public-web")
        self.assertIs(pub_cfg["defaults"].get("require_x_coverage"), False)
        self.assertEqual(pub_cfg["defaults"].get("timeout_seconds"), base_cfg["defaults"].get("timeout_seconds"))

        social_rows = [
            row
            for row in pub_cfg["sources"]
            if row.get("type") == "agent_social" or str(row.get("id", "")).startswith("x_")
        ]
        self.assertTrue(social_rows, "expected social rows in inherited sources list")
        self.assertTrue(all(row.get("enabled") is False for row in social_rows))

        enabled_sources = load_sources(pub_path, include_disabled=False)
        self.assertTrue(enabled_sources)
        self.assertTrue(all(src.type != "agent_social" and not src.id.startswith("x_") for src in enabled_sources))

        all_sources = load_sources(pub_path, include_disabled=True)
        self.assertGreater(len(all_sources), len(enabled_sources))

    def test_circular_inheritance_raises_value_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a_path = root / "a.json"
            b_path = root / "b.json"
            self_path = root / "self.json"
            a_path.write_text(json.dumps({"extends": "b.json"}), encoding="utf-8")
            b_path.write_text(json.dumps({"extends": "a.json"}), encoding="utf-8")
            self_path.write_text(json.dumps({"extends": "self.json"}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "circular configuration inheritance"):
                load_config(a_path)
            with self.assertRaisesRegex(ValueError, "circular configuration inheritance"):
                load_config(self_path)

    def test_effective_config_sha256_and_load_sources_filtering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_file = root / "base.json"
            child_file = root / "child.json"
            base_file.write_text(
                json.dumps(
                    {
                        "defaults": {"timeout_seconds": 22, "require_x_coverage": True},
                        "sources": [
                            {"id": "openai_news", "name": "OpenAI", "tier": "a", "type": "rss", "url": "https://openai.com/rss.xml", "enabled": True, "reliability": "official"},
                            {"id": "x_official", "name": "X Official", "tier": "A", "type": "agent_social", "url": "data/leads.json#OpenAI", "enabled": True, "reliability": "official_social"},
                            {"id": "rel_rsshub", "name": "Relative RSSHub", "tier": "C", "type": "rsshub", "url": "/weibo/user/1", "enabled": True},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            child_file.write_text(
                json.dumps(
                    {
                        "extends": "base.json",
                        "defaults": {"require_x_coverage": False},
                        "source_filter": {"exclude_types": ["agent_social"], "exclude_id_prefixes": ["x_"]},
                    }
                ),
                encoding="utf-8",
            )

            cfg = load_config(child_file)
            digest_from_path = effective_config_sha256(child_file)
            digest_from_dict = effective_config_sha256(cfg)
            self.assertEqual(len(digest_from_path), 64)
            self.assertEqual(digest_from_path, digest_from_dict)
            self.assertEqual(
                effective_config_sha256({"b": 1, "a": 2}),
                effective_config_sha256({"a": 2, "b": 1}),
            )
            self.assertNotEqual(digest_from_dict, effective_config_sha256(base_file))

            active = load_sources(child_file, include_disabled=False)
            self.assertEqual([s.id for s in active], ["openai_news"])
            self.assertEqual(active[0].tier, "A")
            self.assertEqual(active[0].timeout_seconds, 22)
            self.assertTrue(active[0].is_official)
            self.assertFalse(active[0].is_community)

            with_disabled = load_sources(child_file, include_disabled=True)
            by_id = {s.id: s for s in with_disabled}
            self.assertFalse(by_id["x_official"].enabled)
            self.assertFalse(by_id["rel_rsshub"].enabled)


class DatabaseTests(unittest.TestCase):
    def test_sqlite_crud_and_story_history_14_run_lookback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "nested" / "briefing.sqlite3"
            with contextlib.closing(connect(db_path)) as conn:
                src = Source(
                    id="openai_news",
                    name="OpenAI Blog",
                    tier="A",
                    type="rss",
                    region="global",
                    url="https://openai.com/news/rss.xml",
                    enabled=True,
                    reliability="official",
                    topics=["OpenAI", "GPT"],
                )
                upsert_sources(conn, [src])
                # Upsert update on conflict
                updated_src = Source(
                    id="openai_news",
                    name="OpenAI Official News",
                    tier="A",
                    type="rss",
                    region="global",
                    url="https://openai.com/news/rss.xml",
                    enabled=True,
                    reliability="official",
                    topics=["OpenAI", "GPT-5"],
                )
                upsert_sources(conn, [updated_src])

                now = datetime(2026, 7, 20, 10, 0, tzinfo=UTC)
                insert_health(
                    conn,
                    SourceHealth(
                        source_id="openai_news",
                        source_name="OpenAI Official News",
                        tier="A",
                        enabled=True,
                        status="ok",
                        status_code=200,
                        item_count=2,
                        latest_item_at=now,
                        latency_ms=125,
                        final_url="https://openai.com/news/rss.xml",
                    ),
                )
                health_row = conn.execute("SELECT * FROM source_health WHERE source_id = ?", ("openai_news",)).fetchone()
                self.assertEqual(health_row["status"], "ok")
                self.assertEqual(health_row["status_code"], 200)
                self.assertEqual(health_row["item_count"], 2)

                recent_item = FeedItem(
                    source_id="openai_news",
                    source_name="OpenAI Official News",
                    source_tier="A",
                    source_reliability="official",
                    title="Initial GPT-5 release note",
                    link="https://openai.com/index/gpt-5",
                    guid="gpt-5-1",
                    summary="Initial summary",
                    published_at=now - timedelta(hours=2),
                    fetched_at=now,
                    raw={"kind": "rss"},
                )
                undated_recent = FeedItem(
                    source_id="openai_news",
                    source_name="OpenAI Official News",
                    source_tier="A",
                    source_reliability="official",
                    title="Undated developer update",
                    link="https://openai.com/index/dev-update",
                    guid="dev-update-1",
                    summary="Fetched within window",
                    published_at=None,
                    fetched_at=now - timedelta(hours=1),
                )
                old_item = FeedItem(
                    source_id="openai_news",
                    source_name="OpenAI Official News",
                    source_tier="A",
                    source_reliability="official",
                    title="Old GPT-4 note",
                    link="https://openai.com/index/gpt-4",
                    guid="gpt-4-old",
                    summary="Old",
                    published_at=now - timedelta(days=5),
                    fetched_at=now - timedelta(days=5),
                )
                self.assertEqual(insert_items(conn, [recent_item, undated_recent, old_item]), 3)

                # Re-inserting existing unique_key updates content without incrementing new count
                updated_item = FeedItem(
                    source_id="openai_news",
                    source_name="OpenAI Official News",
                    source_tier="A",
                    source_reliability="official",
                    title="Updated GPT-5 release note",
                    link="https://openai.com/index/gpt-5",
                    guid="gpt-5-1",
                    summary="Enriched summary",
                    published_at=now - timedelta(hours=2),
                    fetched_at=now,
                    raw={"kind": "rss", "enriched": True},
                )
                self.assertEqual(insert_items(conn, [updated_item]), 0)

                loaded = load_recent_items(conn, since=now - timedelta(hours=24))
                self.assertEqual([it.guid for it in loaded], ["dev-update-1", "gpt-5-1"])
                by_guid = {it.guid: it for it in loaded}
                self.assertEqual(by_guid["gpt-5-1"].title, "Updated GPT-5 release note")
                self.assertEqual(by_guid["gpt-5-1"].source_name, "OpenAI Official News")
                self.assertIs(by_guid["gpt-5-1"].raw.get("enriched"), True)

                # Populate 16 historical runs (2026-07-01 .. 2026-07-16) plus current run 2026-07-20.
                # Lookback=14 before 2026-07-20 covers 2026-07-03 .. 2026-07-16 only.
                for day in range(1, 17):
                    run_date = f"2026-07-{day:02d}"
                    cards: list[EvidenceCard] = [_make_card(f"filler:{day}", selected=True)]
                    if day in {1, 2, 5, 10, 16}:
                        cards.append(_make_card("recurring:story", selected=True))
                    if day in {1, 2}:
                        cards.append(_make_card("ancient:only", selected=True))
                    if day == 12:
                        cards.append(_make_card("rejected:story", selected=False))
                    save_evidence_cards(conn, run_date, cards)

                # Saving twice on same run_date replaces rather than duplicates
                save_evidence_cards(
                    conn,
                    "2026-07-20",
                    [_make_card("recurring:story", selected=True), _make_card("same_day:only", selected=True)],
                )

                history = load_story_history(conn, before_run_date="2026-07-20", lookback_runs=14)
                self.assertEqual(history.get("recurring:story"), 3)  # days 5, 10, 16; days 1, 2 & 20 excluded
                self.assertNotIn("ancient:only", history)
                self.assertNotIn("rejected:story", history)
                self.assertNotIn("same_day:only", history)


class TextTests(unittest.TestCase):
    def test_strip_html_and_clean_text(self) -> None:
        raw = (
            "<![CDATA[<p>OpenAI &amp; DeepMind</p>]]>"
            "<script>alert(1)</script><style>.x{color:red}</style>"
            "<div>发布 <b>GPT-5</b> 模型   #欢迎关注公众号获取更多资讯</div>"
        )
        stripped = strip_html(raw)
        self.assertEqual(stripped, "OpenAI & DeepMind 发布 GPT-5 模型")

        truncated = clean_text("<p>OpenAI 发布 GPT-5 模型，支持多模态推理能力，上下文窗口翻倍。</p>", limit=20)
        self.assertLessEqual(len(truncated), 20)
        self.assertNotIn("…", truncated)
        self.assertNotIn("...", truncated)

    def test_clause_truncate_no_ellipses_and_balanced_brackets(self) -> None:
        with_ellipses = clause_truncate("OpenAI 发布新一代推理模型……...", 50)
        self.assertEqual(with_ellipses, "OpenAI 发布新一代推理模型")
        self.assertNotIn("…", with_ellipses)

        sentence_cut = clause_truncate("阿里开源 Qwen3 模型。该模型支持本地量化部署与高速工具调用能力。", 18)
        self.assertEqual(sentence_cut, "阿里开源 Qwen3 模型。")

        unclosed_cn_paren = clause_truncate("DeepSeek 发布新版本（包含 FP8 推理加速与超长上下文窗口优化）", 20)
        self.assertNotIn("…", unclosed_cn_paren)
        self.assertEqual(unclosed_cn_paren.count("（"), unclosed_cn_paren.count("）"))
        self.assertEqual(unclosed_cn_paren, "DeepSeek 发布新版本")

        unclosed_book = clause_truncate("欧盟正式实施《人工智能监管与合规实施细则最新修订案》", 15)
        self.assertEqual(unclosed_book.count("《"), unclosed_book.count("》"))
        self.assertNotIn("…", unclosed_book)

    def test_normalize_cn_punctuation_cjk_and_raw_english(self) -> None:
        self.assertEqual(
            normalize_cn_punctuation("官方说明: 支持推理, 工具调用; 现已开放...…"),
            "官方说明：支持推理，工具调用；现已开放",
        )
        self.assertEqual(normalize_cn_punctuation("Release: v1.0, fast; clean"), "Release: v1.0, fast; clean")

        self.assertTrue(has_cjk("GPT-5 正式上线"))
        self.assertFalse(has_cjk("GPT-5 launches today"))
        self.assertFalse(has_cjk(None))

        self.assertTrue(looks_raw_english("OpenAI announces a major update to ChatGPT desktop workflows today"))
        self.assertFalse(looks_raw_english("OpenAI 正式发布 GPT-5 模型"))
        self.assertFalse(looks_raw_english(""))

    def test_parse_date_formats_and_format_mmss(self) -> None:
        self.assertEqual(parse_date("Fri, 03 Jul 2026 08:00:00 GMT"), datetime(2026, 7, 3, 8, 0, tzinfo=UTC))
        self.assertEqual(parse_date("2026-07-03T16:30:00+08:00"), datetime(2026, 7, 3, 8, 30, tzinfo=UTC))
        self.assertEqual(parse_date("2026-07-03T08:30:00Z"), datetime(2026, 7, 3, 8, 30, tzinfo=UTC))
        self.assertEqual(parse_date("2026.7.3"), datetime(2026, 7, 3, 0, 0, tzinfo=UTC))
        self.assertEqual(parse_date("2026年7月3日 14:05"), datetime(2026, 7, 3, 14, 5, tzinfo=UTC))
        self.assertEqual(parse_date("July 3, 2026"), datetime(2026, 7, 3, 0, 0, tzinfo=UTC))
        self.assertEqual(parse_date("3rd Jul. 2026"), datetime(2026, 7, 3, 0, 0, tzinfo=UTC))
        self.assertIsNone(parse_date("not-a-date"))
        self.assertIsNone(parse_date(""))

        self.assertEqual(format_mmss(0), "00:00")
        self.assertEqual(format_mmss(-5), "00:00")
        self.assertEqual(format_mmss(65), "01:05")
        self.assertEqual(format_mmss(125.6), "02:06")


class UrlsTests(unittest.TestCase):
    def test_canonical_url_and_x_snowflake(self) -> None:
        self.assertEqual(
            canonical_url("https://www.openai.com//index/gpt-5/?utm_source=x&utm_medium=social&lang=zh&fbclid=123#top"),
            "https://openai.com/index/gpt-5?lang=zh",
        )
        self.assertEqual(
            canonical_url("https://mobile.twitter.com/OpenAI/status/1940685355315200000?s=20&t=xyz"),
            "https://x.com/openai/status/1940685355315200000",
        )
        self.assertEqual(
            parse_x_status("https://twitter.com/Sama/status/1940685355315200000/photo/1"),
            ("sama", "1940685355315200000"),
        )
        self.assertIsNone(parse_x_status("https://x.com/OpenAI"))

        expected_dt = datetime(2026, 7, 3, 8, 0, 0, tzinfo=UTC)
        snowflake = (int(expected_dt.timestamp() * 1000) - 1288834974657) << 22
        self.assertEqual(decode_x_snowflake_utc(str(snowflake)), expected_dt)
        self.assertIsNone(decode_x_snowflake_utc("invalid"))
        self.assertIsNone(decode_x_snowflake_utc(42))

    def test_social_signal_predicates_and_origin_indexes(self) -> None:
        personnel_card = _make_card(
            official_count=0,
            community_count=1,
            evidence_links=[
                {"source": "Media", "tier": "B", "reliability": "media", "url": "https://36kr.com/p/1"},
                {"source": "X Staff", "tier": "B", "reliability": "official_personnel", "url": "https://x.com/sama/status/1940685355315200000"},
            ],
        )
        self.assertTrue(card_is_official_personnel_signal(personnel_card))
        self.assertFalse(card_is_official_social_signal(personnel_card))
        self.assertFalse(card_is_community_signal(personnel_card))
        self.assertEqual(social_origin_evidence_indexes(personnel_card), [1])

        official_social_card = _make_card(
            official_count=1,
            evidence_links=[
                {"source": "X Official", "tier": "A", "reliability": "official_social", "url": "https://x.com/OpenAI/status/1940685355315200000"},
            ],
        )
        self.assertTrue(card_is_official_social_signal(official_social_card))
        self.assertFalse(card_is_community_signal(official_social_card))
        self.assertEqual(social_origin_evidence_indexes(official_social_card), [0])

        community_card = _make_card(
            official_count=0,
            media_count=1,
            community_count=1,
            evidence_links=[
                {"source": "36Kr", "tier": "B", "reliability": "media", "url": "https://36kr.com/p/2"},
                {"source": "Reddit", "tier": "C", "reliability": "community", "url": "https://reddit.com/r/LocalLLaMA/comments/abc"},
            ],
        )
        self.assertTrue(card_is_community_signal(community_card))
        self.assertEqual(social_origin_evidence_indexes(community_card), [1])

        normal_card = _make_card()
        self.assertFalse(card_is_community_signal(normal_card))
        self.assertEqual(social_origin_evidence_indexes(normal_card), [])


class EntitiesTests(unittest.TestCase):
    def test_entity_brand_and_model_helpers(self) -> None:
        self.assertEqual(detect_entity("Introducing Claude Sonnet 4"), "Anthropic / Claude")
        self.assertEqual(detect_entity(" Generic release note ", source_id="qwen_hf_models"), "Qwen / 阿里")
        self.assertEqual(detect_entity("Unrelated chip packaging"), "AI")

        self.assertEqual(infer_brand("Read more at https://qwen.ai/blog"), "阿里千问")
        self.assertEqual(infer_brand("arXiv paper 2607.00001"), "")

        models = extract_model_names("OpenAI GPT-5 vs Qwen3-72B and gpt 5 benchmark")
        self.assertEqual(models, ["GPT-5", "Qwen3-72B"])

        self.assertEqual(extract_hf_model_id("Hugging Face 模型仓库更新：Qwen/Qwen3-7B-Instruct"), "Qwen/Qwen3-7B-Instruct")
        self.assertEqual(extract_hf_model_id("models/unknown"), "")

        self.assertEqual(model_family_key("Qwen/Qwen3-7B-Instruct-FP8"), "qwen/qwen3")
        self.assertEqual(model_family_key("Qwen3-72B-GGUF"), "qwen3")

    def test_is_ai_relevant_and_card_display_entity_repair(self) -> None:
        relevant, score = is_ai_relevant("OpenAI 正式发布 GPT-5 推理大模型", "上下文窗口提升并支持智能体工具调用")
        self.assertTrue(relevant)
        self.assertGreaterEqual(score, 4)

        noisy, noisy_score = is_ai_relevant("某公司 AI 概念股今日涨停，披露一季度营收财报与目标价", "券商评级上调")
        self.assertFalse(noisy)
        self.assertEqual(noisy_score, 0)

        generic_card = _make_card(
            entity="AI",
            event_title="新模型正式开放 API",
            evidence_links=[{"source": "DeepSeek Official", "url": "https://api-docs.deepseek.com/news/v3", "title": "DeepSeek API Update"}],
        )
        self.assertEqual(card_display_entity(generic_card), "DeepSeek")

        false_paper_card = _make_card(
            entity="论文 / arXiv",
            event_title="Claude 发布最新技术研究报告",
            evidence_links=[{"source": "Anthropic", "url": "https://www.anthropic.com/research/claude-paper", "title": "Claude Research"}],
        )
        self.assertEqual(card_display_entity(false_paper_card), "Anthropic")

        real_paper_card = _make_card(
            entity="论文 / arXiv",
            event_title="稀疏注意力训练方法新论文",
            key_facts=["提出一种新的稀疏注意力训练方法"],
            evidence_links=[{"source": "arXiv CS.CL", "url": "https://arxiv.org/abs/2607.01234", "title": "Sparse Attention"}],
        )
        self.assertEqual(card_display_entity(real_paper_card), "论文")


class IoTests(unittest.TestCase):
    def test_atomic_json_and_pipeline_lock_liveness(self) -> None:
        self.assertTrue(pid_alive(os.getpid()))
        self.assertFalse(pid_alive(-1))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            json_path = root / "nested" / "payload.json"
            write_json_atomic(json_path, {"ok": True, "label": "日报"})
            self.assertEqual(read_json(json_path), {"ok": True, "label": "日报"})
            self.assertEqual(list(json_path.parent.glob("*.tmp")), [])
            self.assertEqual(read_json(root / "missing.json", default={"fallback": 1}), {"fallback": 1})

            lock_path = root / "run.lock"
            with PipelineLock(lock_path, stale_after_seconds=5):
                self.assertTrue(lock_path.is_file())
                # Backdate lock mtime to simulate a multi-hour 4K render while current PID is still alive
                old_ts = time.time() - 86_400
                os.utime(lock_path, (old_ts, old_ts))
                with self.assertRaisesRegex(RuntimeError, "already active"):
                    with PipelineLock(lock_path, stale_after_seconds=5):
                        pass
                self.assertTrue(lock_path.is_file())
            self.assertFalse(lock_path.exists())

            # Dead PID lock recovery
            write_json_atomic(lock_path, {"pid": 999999, "created_at": "2026-07-01T00:00:00+00:00"})
            with patch("briefing.core.io.pid_alive", return_value=False):
                with PipelineLock(lock_path) as recovered:
                    self.assertTrue(recovered.acquired)
                    self.assertEqual(read_json(lock_path)["pid"], os.getpid())
            self.assertFalse(lock_path.exists())

    def test_run_state_lifecycle_and_prune_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / "run-state.json"
            state = RunState(state_path, run_id="r-1", run_date="2026-07-03")
            state.start("ingest", input_count=12, fingerprint_input={"cfg": "v1"})
            state.progress("ingest", {"fetched": 8})
            state.finish("ingest", output_count=40, metrics={"healthy": 11})
            state.complete("QA_PASSED")

            reloaded = RunState.load_or_create(state_path, run_id="ignored", run_date="2026-07-03")
            self.assertEqual(reloaded.run_id, "r-1")
            self.assertEqual(reloaded.status, "QA_PASSED")
            self.assertEqual(len(reloaded.stages), 1)
            self.assertEqual(reloaded.stages[0].status, "OK")
            self.assertEqual(reloaded.stages[0].output_count, 40)
            self.assertEqual(reloaded.stages[0].metrics["healthy"], 11)
            self.assertTrue(reloaded.stages[0].fingerprint)

            reloaded.start("render")
            reloaded.fail("render", ValueError("remotion crashed"))
            failed_payload = read_json(state_path)
            self.assertEqual(failed_payload["status"], "FAILED")
            self.assertIn("ValueError: remotion crashed", failed_payload["stages"][-1]["error"])

            # Prune runs: old date vs recent date vs non-date directory
            runs_dir = root / "runs"
            old_run = runs_dir / "2026-06-10"
            recent_run = runs_dir / "2026-07-02"
            non_date = runs_dir / "shared-assets"

            for d in (old_run, recent_run, non_date):
                (d / "render").mkdir(parents=True, exist_ok=True)
                (d / "manifest.json").write_text("{}", encoding="utf-8")
                (d / "cover.png").write_bytes(b"cover")
                (d / "final.mp4").write_bytes(b"x" * 1024)
                (d / "render" / "chunk.mp4").write_bytes(b"y" * 512)

            ref_now = datetime(2026, 7, 3, 12, 0)
            dry = prune_runs(runs_dir, keep_days=7, execute=False, now=ref_now)
            self.assertFalse(dry["execute"])
            self.assertEqual([row["run"] for row in dry["pruned"]], ["2026-06-10"])
            self.assertEqual(dry["kept"], ["2026-07-02"])
            self.assertGreater(dry["reclaimed_bytes"], 0)
            self.assertTrue((old_run / "final.mp4").is_file())
            self.assertTrue((old_run / "render").is_dir())

            executed = prune_runs(runs_dir, keep_days=7, execute=True, now=ref_now)
            self.assertTrue(executed["execute"])
            self.assertFalse((old_run / "final.mp4").exists())
            self.assertFalse((old_run / "render").exists())
            self.assertTrue((old_run / "manifest.json").is_file())
            self.assertTrue((old_run / "cover.png").is_file())
            self.assertTrue((recent_run / "final.mp4").is_file())
            self.assertTrue((non_date / "final.mp4").is_file())


if __name__ == "__main__":
    unittest.main()

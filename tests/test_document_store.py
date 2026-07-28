import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from briefing.content_enrichment import ArticleResult
from briefing.db import connect, load_latest_document, save_document
from briefing.document_store import build_document
from briefing.models import FeedItem


class DocumentStoreTests(unittest.TestCase):
    def item(self) -> FeedItem:
        return FeedItem(
            source_id="acme", source_name="Acme", source_tier="A", source_reliability="official",
            title="Acme 发布 Nova-7", link="https://example.com/nova", guid="nova-7", summary="RSS teaser",
            published_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
        )

    def article(self) -> ArticleResult:
        fact = "Nova-7 新增 256K 上下文并开放 API，推理延迟降低 18%。"
        return ArticleResult(
            status="ok", url="https://example.com/nova", final_url="https://example.com/nova-7",
            title="Nova-7 发布", description="Nova-7 产品发布说明", text=f"产品说明。{fact}更多背景。",
            facts=[fact], published_at=datetime(2026, 7, 10, tzinfo=timezone.utc), crawler="crawl4ai",
        )

    def test_builds_versioned_document_and_located_excerpt(self):
        document, excerpts = build_document(self.item(), self.article(), fetched_at=datetime(2026, 7, 10, 8, tzinfo=timezone.utc))

        self.assertEqual(document.extractor_version, "document-store/v1")
        self.assertEqual(document.language, "zh")
        self.assertEqual(len(document.content_hash), 64)
        self.assertGreater(document.quality_score, 40)
        self.assertEqual(len(excerpts), 1)
        self.assertIsNotNone(excerpts[0].start_offset)
        self.assertEqual(document.content[excerpts[0].start_offset:excerpts[0].end_offset], excerpts[0].text)

    def test_persists_and_loads_document_with_excerpts(self):
        with tempfile.TemporaryDirectory() as td:
            conn = connect(Path(td) / "briefing.sqlite3")
            document, excerpts = build_document(self.item(), self.article())

            save_document(conn, document, excerpts)
            loaded = load_latest_document(conn, document.item_unique_key)

            self.assertIsNotNone(loaded)
            loaded_document, loaded_excerpts = loaded
            self.assertEqual(loaded_document.content_hash, document.content_hash)
            self.assertEqual(loaded_document.extractor, "crawl4ai")
            self.assertEqual(loaded_excerpts[0].text, excerpts[0].text)
            conn.close()

    def test_same_content_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            conn = connect(Path(td) / "briefing.sqlite3")
            document, excerpts = build_document(self.item(), self.article())
            save_document(conn, document, excerpts)
            save_document(conn, document, excerpts)

            self.assertEqual(conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM evidence_excerpts").fetchone()[0], 1)
            conn.close()


if __name__ == "__main__":
    unittest.main()

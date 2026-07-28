import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from briefing.db import connect, insert_items, load_recent_items, upsert_sources
from briefing.models import FeedItem, Source


class RecentItemsTests(unittest.TestCase):
    def test_no_date_items_expire_by_fetch_time(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "db.sqlite3"
            conn = connect(db)
            src = Source("trend", "Trend", "C", "rss", "global", "https://example.com", True, "community", [])
            upsert_sources(conn, [src])
            now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
            old = FeedItem("trend", "Trend", "C", "community", "old no date", "https://example.com/old", "old", fetched_at=now - timedelta(days=10))
            recent = FeedItem("trend", "Trend", "C", "community", "recent no date", "https://example.com/recent", "recent", fetched_at=now)
            insert_items(conn, [old, recent])
            rows = load_recent_items(conn, since=now - timedelta(hours=36), limit=10)
            self.assertEqual([r.title for r in rows], ["recent no date"])
            conn.close()


if __name__ == "__main__":
    unittest.main()

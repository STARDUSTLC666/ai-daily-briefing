import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from briefing.db import connect, load_story_history, save_evidence_cards
from briefing.models import EvidenceCard


def card(key: str, *, selected: bool = True) -> EvidenceCard:
    now = datetime(2026, 7, 7, tzinfo=timezone.utc)
    return EvidenceCard(
        cluster_key=key, event_title=key, entity="Entity", risk="green", confidence=90,
        selected=selected, reason="", source_count=1, official_count=1, media_count=0,
        community_count=0, first_seen_at=now, latest_published_at=now,
        key_facts=["具体事实"], evidence_links=[], uncertainty=[], score=90,
    )


class StoryHistoryTests(unittest.TestCase):
    def test_counts_only_prior_selected_runs(self):
        with tempfile.TemporaryDirectory() as td:
            conn = connect(Path(td) / "briefing.sqlite3")
            save_evidence_cards(conn, "2026-07-07", [card("repeat"), card("ignored", selected=False)])
            save_evidence_cards(conn, "2026-07-08", [card("repeat")])
            save_evidence_cards(conn, "2026-07-10", [card("repeat")])

            history = load_story_history(conn, before_run_date="2026-07-10")

            self.assertEqual(history, {"repeat": 2})
            conn.close()


if __name__ == "__main__":
    unittest.main()

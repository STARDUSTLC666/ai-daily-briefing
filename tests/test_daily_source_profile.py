import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DailySourceProfileTests(unittest.TestCase):
    def test_daily_fast_lane_has_a_bounded_enrichment_budget(self):
        config = json.loads((ROOT / "sources.yaml").read_text(encoding="utf-8-sig"))
        enabled = [row for row in config["sources"] if row.get("enabled", True)]
        enabled_ids = {row["id"] for row in enabled}
        budget = int(config["defaults"].get("content_enrichment_max_items") or 0)

        self.assertLessEqual(len(enabled), 40)
        self.assertGreaterEqual(len(enabled), 24)
        self.assertGreater(budget, 0)
        self.assertLessEqual(budget, 40)
        self.assertTrue(
            {
                "x_codex_official_leads",
                "x_codex_personnel_leads",
                "x_codex_community_leads",
            }.issubset(enabled_ids)
        )
        self.assertFalse(any(row.get("type") == "rsshub" for row in enabled))
        self.assertLessEqual(sum(row.get("type") == "google_news" for row in enabled), 1)


if __name__ == "__main__":
    unittest.main()

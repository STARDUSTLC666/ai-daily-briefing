import json
import tempfile
import unittest
from pathlib import Path

from briefing.run_state import RunState


class RunStateTests(unittest.TestCase):
    def test_records_stage_counts_metrics_and_fingerprint(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "run-state.json"
            state = RunState(path, "run-1", "2026-07-10")
            state.start("COLLECT", input_count=52, fingerprint_input={"sources": ["a", "b"]})
            state.finish("COLLECT", output_count=3279, metrics={"healthy_sources": 49})
            state.complete()

            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "COMPLETED")
            stage = payload["stages"][0]
            self.assertEqual(stage["status"], "OK")
            self.assertEqual(stage["input_count"], 52)
            self.assertEqual(stage["output_count"], 3279)
            self.assertEqual(stage["metrics"]["healthy_sources"], 49)
            self.assertEqual(len(stage["fingerprint"]), 20)

    def test_running_stage_progress_is_persisted(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "run-state.json"
            state = RunState(path, "run-progress", "2026-07-10")
            state.start("ENRICH", input_count=40)
            state.progress("ENRICH", {"attempted": 7, "current_url": "https://example.com/7"})

            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["stages"][0]["metrics"]["attempted"], 7)
            self.assertEqual(payload["stages"][0]["metrics"]["current_url"], "https://example.com/7")

    def test_failure_is_persisted(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "run-state.json"
            state = RunState(path, "run-2", "2026-07-10")
            state.start("ENRICH")
            state.fail("ENRICH", ValueError("bad document"))

            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "FAILED")
            self.assertIn("ValueError: bad document", payload["stages"][0]["error"])


if __name__ == "__main__":
    unittest.main()

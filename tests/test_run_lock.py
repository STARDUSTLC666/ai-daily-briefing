import json
import os
import tempfile
import unittest
from pathlib import Path

from briefing.run_lock import PipelineLock, _pid_alive


class PipelineLockTests(unittest.TestCase):
    def test_current_process_probe_is_non_destructive(self):
        self.assertTrue(_pid_alive(os.getpid()))

    def test_concurrent_same_date_run_is_rejected_and_lock_is_released(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "2026-07-11.lock"
            with PipelineLock(path):
                self.assertTrue(path.exists())
                with self.assertRaisesRegex(RuntimeError, "already active"):
                    with PipelineLock(path):
                        pass
            self.assertFalse(path.exists())

    def test_dead_owner_lock_is_recovered_without_six_hour_delay(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "2026-07-11.lock"
            path.write_text(json.dumps({"pid": 99999999}), encoding="utf-8")

            with PipelineLock(path):
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(payload["pid"], os.getpid())


if __name__ == "__main__":
    unittest.main()

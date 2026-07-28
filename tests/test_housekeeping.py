import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from briefing.housekeeping import prune_runs


def _make_run(root: Path, name: str, *, heavy: bool = True) -> Path:
    run = root / name
    (run / "render").mkdir(parents=True)
    (run / "evidence-screenshots").mkdir()
    (run / "manifest.json").write_text("{}", encoding="utf-8")
    (run / "script.json").write_text("[]", encoding="utf-8")
    (run / "evidence-screenshots" / "proof.png").write_bytes(b"p" * 32)
    if heavy:
        (run / "final.mp4").write_bytes(b"v" * 1024)
        (run / "render" / "chunk.bin").write_bytes(b"r" * 512)
        (run / "source-assets").mkdir()
        (run / "source-assets" / "page.png").write_bytes(b"s" * 256)
    return run


class PruneRunsTests(unittest.TestCase):
    def test_dry_run_reports_without_deleting(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            old = _make_run(root, "2026-07-01")

            report = prune_runs(root, 14, execute=False, now=datetime(2026, 7, 26))

            self.assertEqual([row["run"] for row in report["pruned"]], ["2026-07-01"])
            self.assertGreater(report["reclaimed_bytes"], 0)
            self.assertTrue((old / "final.mp4").exists())
            self.assertTrue((old / "render" / "chunk.bin").exists())

    def test_execute_removes_heavy_media_but_keeps_audit_trail(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            old = _make_run(root, "2026-07-01")
            recent = _make_run(root, "2026-07-20")
            diag = _make_run(root, "_crawl4ai-debug")
            named = _make_run(root, "design-system-qa")

            report = prune_runs(root, 14, execute=True, now=datetime(2026, 7, 26))

            self.assertFalse((old / "final.mp4").exists())
            self.assertFalse((old / "render").exists())
            self.assertFalse((old / "source-assets").exists())
            # The audit trail of the pruned run must survive.
            self.assertTrue((old / "manifest.json").exists())
            self.assertTrue((old / "script.json").exists())
            self.assertTrue((old / "evidence-screenshots" / "proof.png").exists())
            # Recent runs and non-date directories are untouched.
            self.assertTrue((recent / "final.mp4").exists())
            self.assertTrue((diag / "final.mp4").exists())
            self.assertTrue((named / "final.mp4").exists())
            self.assertIn("2026-07-20", report["kept"])
            self.assertEqual(report["errors"], [])

    def test_cutoff_boundary_protects_the_keep_window(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            boundary = _make_run(root, "2026-07-12")

            report = prune_runs(root, 14, execute=True, now=datetime(2026, 7, 26))

            self.assertTrue((boundary / "final.mp4").exists())
            self.assertIn("2026-07-12", report["kept"])

    def test_missing_runs_dir_is_a_clean_noop(self):
        report = prune_runs(Path("/nonexistent/briefing-runs"), 7, execute=True)

        self.assertEqual(report["pruned"], [])
        self.assertEqual(report["errors"], [])


if __name__ == "__main__":
    unittest.main()

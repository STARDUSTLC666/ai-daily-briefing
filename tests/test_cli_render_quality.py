import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from briefing.cli import _begin_render_quality_gate, _finish_render_quality_gate, main


class RenderQualityClosureTests(unittest.TestCase):
    def _run_dir(self, root: Path, *, status: str = "QA_PASSED") -> dict:
        paths = {}
        for key, name in [("video", "final.mp4"), ("cover", "cover.png"), ("subtitles", "subtitles.srt")]:
            path = root / name
            path.write_text("content", encoding="utf-8")
            paths[key] = str(path)
        (root / "manifest.json").write_text(
            json.dumps(
                {
                    "run_id": "run-1",
                    "package_quality": {"ok": True},
                    "automatic_quality_gate": {"ok": True, "status": "passed"},
                }
            ),
            encoding="utf-8",
        )
        (root / "run-state.json").write_text(
            json.dumps({"run_id": "run-1", "status": status, "finished_at": "old", "stages": []}),
            encoding="utf-8",
        )
        return {"status": "ok", **paths}

    def test_begin_invalidates_old_green_gate_and_finish_closes_same_stage(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            render_info = self._run_dir(root)

            _begin_render_quality_gate(root)

            pending_manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            pending_state = json.loads((root / "run-state.json").read_text(encoding="utf-8"))
            self.assertFalse(pending_manifest["automatic_quality_gate"]["ok"])
            self.assertEqual(pending_manifest["automatic_quality_gate"]["status"], "pending")
            self.assertEqual(pending_state["status"], "RENDERING")
            self.assertEqual(pending_state["finished_at"], "")
            self.assertEqual(len(pending_state["stages"]), 1)
            self.assertEqual(pending_state["stages"][-1]["status"], "RUNNING")
            started_at = pending_state["stages"][-1]["started_at"]

            with patch("briefing.cli.verify_run_dir", return_value={"ok": True, "errors": [], "warnings": []}) as verify:
                result = _finish_render_quality_gate(root, render_info)

            verify.assert_called_once_with(root.resolve(), allow_in_progress=True)
            self.assertTrue(result["ok"])
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            state = json.loads((root / "run-state.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["automatic_quality_gate"]["ok"])
            self.assertEqual(manifest["render"], render_info)
            self.assertEqual(state["status"], "QA_PASSED")
            self.assertEqual(len(state["stages"]), 1)
            self.assertEqual(state["stages"][-1]["started_at"], started_at)
            self.assertEqual(state["stages"][-1]["status"], "OK")
            self.assertGreaterEqual(state["stages"][-1]["duration_ms"], 1)

    def test_clean_release_render_is_attested_after_quality_passes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            render_info = self._run_dir(root)
            render_provenance = {
                "version": 1,
                "run_id": "run-1",
                "git_sha": "clean-sha",
                "git_dirty": False,
                "config_sha256": "config-sha",
            }
            attestation = {"version": 1, "status": "verified_clean_render", "git_sha": "clean-sha"}

            with patch("briefing.cli.capture_clean_render_provenance", return_value=render_provenance):
                _begin_render_quality_gate(root, require_clean_release=True)
            with patch("briefing.cli.verify_run_dir", return_value={"ok": True, "errors": [], "warnings": []}), \
                patch("briefing.cli.build_release_attestation", return_value=attestation):
                result = _finish_render_quality_gate(root, render_info)

            self.assertTrue(result["ok"])
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["render_provenance"], render_provenance)
            self.assertEqual(manifest["release_attestation"], attestation)

    def test_quality_failure_closes_running_stage_as_render_failed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            render_info = self._run_dir(root, status="EDITED")
            _begin_render_quality_gate(root)
            with patch("briefing.cli.verify_run_dir", return_value={"ok": False, "errors": ["bad copy"], "warnings": []}):
                result = _finish_render_quality_gate(root, render_info)

            self.assertFalse(result["ok"])
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            state = json.loads((root / "run-state.json").read_text(encoding="utf-8"))
            self.assertFalse(manifest["automatic_quality_gate"]["ok"])
            self.assertEqual(state["status"], "RENDER_FAILED")
            self.assertEqual(len(state["stages"]), 1)
            self.assertEqual(state["stages"][-1]["status"], "FAILED")
            self.assertIn("bad copy", state["stages"][-1]["metrics"]["quality_errors"])

    def test_begin_manifest_parse_failure_marks_state_render_failed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._run_dir(root)
            (root / "manifest.json").write_text("{broken", encoding="utf-8")

            with self.assertRaises(json.JSONDecodeError):
                _begin_render_quality_gate(root)

            state = json.loads((root / "run-state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "RENDER_FAILED")
            self.assertEqual(len(state["stages"]), 1)
            self.assertEqual(state["stages"][-1]["status"], "FAILED")
            self.assertGreaterEqual(state["stages"][-1]["duration_ms"], 1)

    def test_morning_render_observes_pending_gate_during_fake_render(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            render_info = self._run_dir(root)
            result = SimpleNamespace(
                out_dir=root,
                selected_cards=[],
                run_date="2026-07-11",
                selected_count=2,
            )

            def fake_render(*_args, **_kwargs):
                state = json.loads((root / "run-state.json").read_text(encoding="utf-8"))
                manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
                self.assertEqual(state["status"], "RENDERING")
                self.assertEqual(state["stages"][-1]["status"], "RUNNING")
                self.assertFalse(manifest["automatic_quality_gate"]["ok"])
                self.assertEqual(manifest["automatic_quality_gate"]["status"], "pending")
                return render_info

            with patch("briefing.cli.run_pipeline", return_value=result), \
                patch("briefing.cli.append_morning_updates_to_reviewed_script", return_value={"added": 0}), \
                patch("briefing.cli.render_briefing_video", side_effect=fake_render), \
                patch("briefing.cli.refresh_reviewed_bilibili_outputs", return_value={}), \
                patch("briefing.cli.verify_run_dir", return_value={"ok": True, "errors": [], "warnings": []}):
                code = main(["morning-render", "--date", "2026-07-11"])

            self.assertEqual(code, 0)
            state = json.loads((root / "run-state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "QA_PASSED")
            self.assertEqual(len(state["stages"]), 1)
            self.assertEqual(state["stages"][-1]["status"], "OK")

    def test_morning_render_exception_never_restores_old_green_gate(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._run_dir(root)
            result = SimpleNamespace(
                out_dir=root,
                selected_cards=[],
                run_date="2026-07-11",
                selected_count=2,
            )

            with patch("briefing.cli.run_pipeline", return_value=result), \
                patch("briefing.cli.append_morning_updates_to_reviewed_script", return_value={"added": 0}), \
                patch("briefing.cli.render_briefing_video", side_effect=RuntimeError("render exploded")):
                code = main(["morning-render", "--date", "2026-07-11"])

            self.assertEqual(code, 1)
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            state = json.loads((root / "run-state.json").read_text(encoding="utf-8"))
            self.assertFalse(manifest["automatic_quality_gate"]["ok"])
            self.assertEqual(manifest["automatic_quality_gate"]["status"], "failed")
            self.assertEqual(state["status"], "RENDER_FAILED")
            self.assertEqual(len(state["stages"]), 1)
            self.assertEqual(state["stages"][-1]["status"], "FAILED")
            self.assertIn("render exploded", state["stages"][-1]["metrics"]["quality_errors"][0])


if __name__ == "__main__":
    unittest.main()

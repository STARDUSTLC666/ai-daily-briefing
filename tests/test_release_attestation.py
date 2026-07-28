import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from briefing.release_attestation import (
    RELEASE_FILE_PATHS,
    build_release_attestation,
    verify_release_attestation,
)


class ReleaseAttestationTests(unittest.TestCase):
    def _package(self, root: Path) -> dict:
        for relative in RELEASE_FILE_PATHS.values():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(relative, encoding="utf-8")
        provenance = {
            "version": 1,
            "run_id": "run-1",
            "git_sha": "clean-sha",
            "git_dirty": False,
            "config_sha256": "config-sha",
            "captured_at": "2026-07-15T00:00:00+00:00",
        }
        return {
            "run_id": "run-1",
            "provenance": {"git_sha": "old-sha", "git_dirty": True},
            "render_provenance": provenance,
        }

    def test_signed_attestation_binds_every_release_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_dir = root / "run"
            run_dir.mkdir()
            manifest = self._package(run_dir)
            current = dict(manifest["render_provenance"])
            with patch("briefing.release_attestation.capture_clean_render_provenance", return_value=current):
                manifest["release_attestation"] = build_release_attestation(run_dir, manifest, repo_root=root)
                self.assertEqual(verify_release_attestation(run_dir, manifest, repo_root=root), [])

                (run_dir / "final.mp4").write_text("changed", encoding="utf-8")
                errors = verify_release_attestation(run_dir, manifest, repo_root=root)
                self.assertTrue(any("file changed: video" in error for error in errors))

    def test_attestation_signature_rejects_manifest_forgery(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run_dir = root / "run"
            run_dir.mkdir()
            manifest = self._package(run_dir)
            current = dict(manifest["render_provenance"])
            with patch("briefing.release_attestation.capture_clean_render_provenance", return_value=current):
                manifest["release_attestation"] = build_release_attestation(run_dir, manifest, repo_root=root)
                manifest["release_attestation"]["git_sha"] = "forged-sha"
                errors = verify_release_attestation(run_dir, manifest, repo_root=root)

            self.assertTrue(any("signature is invalid" in error for error in errors))


if __name__ == "__main__":
    unittest.main()

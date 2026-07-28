import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from briefing.bilibili_publish import (
    BilibiliError,
    BiliCredentials,
    BilibiliClient,
    _assert_upload_snapshot_attested,
    _assert_upload_snapshot_unchanged,
    _acquire_upload_lock,
    _upload_snapshot,
    canonical_signing_string,
    parse_schedule_at,
    preflight_bilibili,
    publish_bilibili,
    sign_headers,
)


class BilibiliPreflightTests(unittest.TestCase):
    def test_dead_upload_owner_lock_is_recovered(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            stale = root / ".bilibili-upload.lock"
            stale.write_text(json.dumps({"pid": 99999999}), encoding="utf-8")

            lock = _acquire_upload_lock(root)

            self.assertTrue(lock.exists())
            lock.unlink()

    def setUp(self):
        self.verify_run = patch(
            "briefing.bilibili_publish.verify_run_dir",
            return_value={"ok": True, "errors": [], "warnings": []},
        )
        self.verify_run.start()
        self.addCleanup(self.verify_run.stop)

    def write_payload(self, root: Path, tid: int | None = None) -> None:
        for name in ["final.mp4", "cover.png", "subtitles.srt", "pinned-comment.md"]:
            (root / name).write_text("x", encoding="utf-8")
        payload = {
            "title": "今天 AI 圈有三件事速看",
            "desc": "简介",
            "tag": "AI,人工智能",
            "copyright": 1,
            "video": str(root / "final.mp4"),
            "cover": str(root / "cover.png"),
            "subtitle": str(root / "subtitles.srt"),
            "pinned_comment": str(root / "pinned-comment.md"),
            "tid": tid,
        }
        (root / "bilibili.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8-sig")

    def test_preflight_warns_missing_credentials(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.write_payload(root)
            with patch.dict(os.environ, {}, clear=True):
                result = preflight_bilibili(root)
            self.assertTrue(result["ok"])
            self.assertFalse(result["ready_to_upload"])
            self.assertTrue(any("credentials" in w for w in result["warnings"]))

    def test_preflight_blocks_insufficient_content_package(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.write_payload(root, tid=231)
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "package_quality": {
                            "ok": False,
                            "selected_items": 1,
                            "minimum_publish_items": 3,
                            "reason": "only 1 publishable stories; require at least 3",
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                result = preflight_bilibili(root)

            self.assertFalse(result["ok"])
            self.assertFalse(result["ready_to_upload"])
            self.assertTrue(any("package quality failed" in error for error in result["errors"]))

    def test_preflight_always_blocks_qa_fixture(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.write_payload(root, tid=231)
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "qa_fixture": True,
                        "publish_allowed": True,
                        "package_quality": {"ok": True, "edition_mode": "rolling_24h"},
                    }
                ),
                encoding="utf-8",
            )

            result = preflight_bilibili(root)

            self.assertFalse(result["ok"])
            self.assertTrue(any("QA fixture" in error for error in result["errors"]))

    def test_preflight_blocks_failed_output_verification(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.write_payload(root, tid=231)
            with patch(
                "briefing.bilibili_publish.verify_run_dir",
                return_value={"ok": False, "errors": ["script.json is missing"], "warnings": []},
            ), patch.dict(os.environ, {}, clear=True):
                result = preflight_bilibili(root)

            self.assertFalse(result["ok"])
            self.assertFalse(result["ready_to_upload"])
            self.assertIn("output verification failed: script.json is missing", result["errors"])

    def test_preflight_blocks_dirty_automatic_provenance(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.write_payload(root, tid=231)
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-1",
                        "publish_allowed": True,
                        "package_quality": {"ok": True},
                        "provenance": {"git_sha": "abc", "git_dirty": True},
                    }
                ),
                encoding="utf-8",
            )

            result = preflight_bilibili(root)

            self.assertFalse(result["ok"])
            self.assertTrue(any("dirty Git" in error for error in result["errors"]))

    def test_preflight_accepts_dirty_generation_with_hash_bound_clean_release(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.write_payload(root, tid=231)
            manifest = {
                "run_id": "run-1",
                "publish_allowed": True,
                "package_quality": {"ok": True},
                "provenance": {"git_sha": "old-sha", "git_dirty": True},
                "release_attestation": {"version": 1},
            }
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            with patch("briefing.bilibili_publish.verify_release_attestation", return_value=[]), \
                patch.dict(os.environ, {}, clear=True):
                result = preflight_bilibili(root)

            self.assertTrue(result["ok"])
            self.assertFalse(any("dirty Git" in error for error in result["errors"]))
            self.assertFalse(result["ready_to_upload"])

    def test_preflight_rejects_payload_paths_outside_run_dir(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "run"
            root.mkdir()
            self.write_payload(root, tid=231)
            external = Path(td) / "external.mp4"
            external.write_text("external", encoding="utf-8")
            payload_path = root / "bilibili.json"
            payload = json.loads(payload_path.read_text(encoding="utf-8-sig"))
            payload["video"] = str(external)
            payload_path.write_text(json.dumps(payload), encoding="utf-8-sig")

            result = preflight_bilibili(root)

            self.assertFalse(result["ok"])
            self.assertTrue(any("attested run file" in error for error in result["errors"]))

    def test_upload_snapshot_detects_media_or_metadata_changes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.write_payload(root, tid=231)
            payload = json.loads((root / "bilibili.json").read_text(encoding="utf-8-sig"))
            snapshot = _upload_snapshot(root, payload)

            (root / "final.mp4").write_text("changed", encoding="utf-8")

            with self.assertRaisesRegex(BilibiliError, "video"):
                _assert_upload_snapshot_unchanged(root, payload, snapshot)

    def test_upload_snapshot_must_match_signed_release_hashes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.write_payload(root, tid=231)
            payload = json.loads((root / "bilibili.json").read_text(encoding="utf-8-sig"))
            snapshot = _upload_snapshot(root, payload)
            manifest = {
                "release_attestation": {
                    "files": {
                        "bilibili_json": {"sha256": snapshot["bilibili_json"]},
                        "video": {"sha256": "wrong-video-hash"},
                        "cover": {"sha256": snapshot["cover"]},
                    }
                }
            }
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(BilibiliError, "video"):
                _assert_upload_snapshot_attested(root, snapshot)

    def test_sign_headers_matches_document_shape(self):
        creds = BiliCredentials("client-id", "secret", "token")
        headers = sign_headers(creds, b'{"x":1}', timestamp=1234567890, nonce="nonce-1")
        signing = canonical_signing_string(headers)
        self.assertIn("x-bili-accesskeyid:client-id", signing)
        self.assertIn("x-bili-content-md5:ac3ef48caa08fa3ed5e025da69edc645", signing)
        self.assertIn("x-bili-signature-method:HMAC-SHA256", signing)
        self.assertIn("x-bili-signature-version:2.0", signing)
        self.assertEqual(headers["access-token"], "token")
        self.assertRegex(headers["Authorization"], r"^[0-9a-f]{64}$")

    def test_publish_dry_run_does_not_require_credentials(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.write_payload(root, tid=231)
            with patch.dict(os.environ, {}, clear=True):
                result = publish_bilibili(root, dry_run=True)
            self.assertTrue(result["dry_run"])
            self.assertTrue(result["preflight"]["ok"])
            self.assertFalse(result["preflight"]["ready_to_upload"])

    def test_execute_refuses_duplicate_successful_submission_without_force(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.write_payload(root, tid=231)
            (root / "bilibili-upload-result.json").write_text(
                json.dumps({"dry_run": False, "submit_response": {"code": 0}}),
                encoding="utf-8-sig",
            )
            with patch(
                "briefing.bilibili_publish.preflight_bilibili",
                return_value={"ready_to_upload": True, "payload": {"schedule": None}},
            ):
                with self.assertRaisesRegex(BilibiliError, "already has a successful"):
                    publish_bilibili(root, dry_run=False)

    def test_schedule_at_time_uses_run_dir_date(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "2099-01-02"
            root.mkdir()
            parsed = parse_schedule_at("08:00", root)
            self.assertEqual(parsed["local_time"], "2099-01-02T08:00:00+08:00")
            self.assertEqual(parsed["field"], "dtime")

    def test_preflight_includes_schedule_in_payload(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "2099-01-02"
            root.mkdir()
            self.write_payload(root, tid=231)
            with patch.dict(os.environ, {}, clear=True):
                result = preflight_bilibili(root, schedule_at="08:00")
            self.assertTrue(result["ok"])
            self.assertEqual(result["payload"]["schedule"]["local_time"], "2099-01-02T08:00:00+08:00")
            self.assertTrue(any("credentials" in w for w in result["warnings"]))

    def test_preflight_rejects_open_platform_desc_over_limit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.write_payload(root, tid=231)
            payload_path = root / "bilibili.json"
            payload = json.loads(payload_path.read_text(encoding="utf-8-sig"))
            payload["desc"] = "x" * 250
            payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8-sig")
            with patch.dict(os.environ, {}, clear=True):
                result = preflight_bilibili(root)
            self.assertFalse(result["ok"])
            self.assertTrue(any("desc length" in error for error in result["errors"]))

    def test_publish_dry_run_shows_scheduled_submit_step(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "2099-01-02"
            root.mkdir()
            self.write_payload(root, tid=231)
            with patch.dict(os.environ, {}, clear=True):
                result = publish_bilibili(root, dry_run=True, schedule_at="08:00")
            self.assertIn("submit_archive(schedule_at=2099-01-02T08:00:00+08:00)", result["planned_steps"])

    def test_submit_archive_adds_schedule_field(self):
        class CaptureClient(BilibiliClient):
            def __init__(self):
                self.last_payload = None

            def signed_json(self, method, url, payload=None, params=None):
                self.last_payload = payload
                return {"code": 0}

        client = CaptureClient()
        client.submit_archive(
            "token",
            {"title": "t", "tid": 231, "tag": "AI", "copyright": 1},
            schedule_at_ts=4070908800,
            schedule_field="dtime",
        )
        self.assertEqual(client.last_payload["dtime"], 4070908800)

    def test_preflight_rejects_past_schedule(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "2000-01-02"
            root.mkdir()
            self.write_payload(root, tid=231)
            with patch.dict(os.environ, {}, clear=True):
                result = preflight_bilibili(root, schedule_at="08:00")
            self.assertFalse(result["ok"])
            self.assertTrue(any("schedule_at" in error for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()

import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from briefing.bilibili_publish import SHANGHAI_TZ, check_auth, token_expiry_status


class _OkClient:
    def list_archive_types(self):
        return {"code": 0}


class _DeadClient:
    def list_archive_types(self):
        raise RuntimeError("access token expired")


class TokenExpiryStatusTests(unittest.TestCase):
    def test_unknown_without_bookkeeping_env(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BILI_TOKEN_ISSUED_AT", None)
            os.environ.pop("BILI_TOKEN_TTL_DAYS", None)

            self.assertFalse(token_expiry_status()["known"])

    def test_days_left_math(self):
        now = datetime(2026, 7, 26, 8, 0, tzinfo=SHANGHAI_TZ)
        issued = (now - timedelta(days=28)).isoformat()
        with patch.dict(os.environ, {"BILI_TOKEN_ISSUED_AT": issued, "BILI_TOKEN_TTL_DAYS": "30"}):
            status = token_expiry_status(now=now)

        self.assertTrue(status["known"])
        self.assertAlmostEqual(status["days_left"], 2.0, places=1)

    def test_naive_timestamp_is_treated_as_shanghai(self):
        now = datetime(2026, 7, 26, 8, 0, tzinfo=SHANGHAI_TZ)
        issued = (now - timedelta(days=1)).replace(tzinfo=None).isoformat()
        with patch.dict(os.environ, {"BILI_TOKEN_ISSUED_AT": issued, "BILI_TOKEN_TTL_DAYS": "30"}):
            status = token_expiry_status(now=now)

        self.assertTrue(status["known"])
        self.assertAlmostEqual(status["days_left"], 29.0, places=1)


class CheckAuthTests(unittest.TestCase):
    def test_live_probe_passes_with_working_client(self):
        result = check_auth(client=_OkClient())

        self.assertTrue(result["ok"])

    def test_rejected_token_reports_api_error(self):
        result = check_auth(client=_DeadClient())

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "api_error")
        self.assertIn("access token expired", result["error"])

    def test_missing_credentials_are_reported_not_raised(self):
        with patch.dict(os.environ, {}, clear=False):
            for name in ["BILI_CLIENT_ID", "BILI_CLIENT_SECRET", "BILI_ACCESS_TOKEN"]:
                os.environ.pop(name, None)

            result = check_auth()

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "missing_credentials")


class OpsWiringTests(unittest.TestCase):
    """The scheduled scripts must actually invoke the new commands."""

    def test_run_daily_wires_probe_notice_repurpose_and_prune(self):
        repo = Path(__file__).resolve().parents[1]
        script = (repo / "scripts" / "run_daily.ps1").read_text(encoding="utf-8-sig")

        self.assertIn("bilibili-check-auth", script)
        self.assertIn("Send-AutomationNotice", script)
        self.assertIn('"repurpose", "--run-dir"', script)
        self.assertIn('"prune-runs", "--keep-days"', script)

    def test_credential_scripts_track_token_issue_time(self):
        repo = Path(__file__).resolve().parents[1]
        configure = (repo / "scripts" / "configure_bilibili.ps1").read_text(encoding="utf-8-sig")
        loader = (repo / "scripts" / "load_bilibili_credentials.ps1").read_text(encoding="utf-8-sig")

        self.assertIn("TokenIssuedAt", configure)
        self.assertIn("TokenTtlDays", configure)
        self.assertIn("BILI_TOKEN_ISSUED_AT", loader)
        self.assertIn("BILI_TOKEN_TTL_DAYS", loader)


if __name__ == "__main__":
    unittest.main()

import unittest
from pathlib import Path


class DailyAutomationDefaultsTests(unittest.TestCase):
    def test_daily_script_defaults_to_xiaoxiao_without_implicit_roxy(self):
        repo = Path(__file__).resolve().parents[1]
        script = (repo / "scripts" / "run_daily.ps1").read_text(encoding="utf-8-sig")

        self.assertIn('$env:BRIEFING_TTS_BACKEND = "edge"', script)
        self.assertIn('$env:BRIEFING_TTS_VOICE = "zh-CN-XiaoxiaoNeural"', script)
        self.assertIn('$env:BRIEFING_TTS_RATE = "+8%"', script)
        self.assertIn('$env:BRIEFING_TTS_PITCH = "+0Hz"', script)
        self.assertNotIn('$env:BRIEFING_TTS_PROFILE = "roxy"', script)


if __name__ == "__main__":
    unittest.main()

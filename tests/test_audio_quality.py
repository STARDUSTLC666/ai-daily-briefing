import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from briefing.audio_quality import analyze_audio, media_sha256, normalize_audio


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg is required")
class AudioQualityTests(unittest.TestCase):
    def test_two_pass_normalization_and_report_hash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source, normalized, report = root / "low.wav", root / "normalized.wav", root / "audio-quality.json"
            subprocess.run(
                ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-af", "volume=0.08", "-ar", "48000", "-ac", "2", str(source)],
                check=True,
            )

            normalization = normalize_audio(source, normalized)
            quality = analyze_audio(normalized, report)

            self.assertEqual(normalization["method"], "ebu_r128_two_pass")
            self.assertTrue(quality["ok"])
            self.assertEqual(quality["media_sha256"], media_sha256(normalized))
            self.assertEqual(json.loads(report.read_text(encoding="utf-8"))["media_sha256"], media_sha256(normalized))

    def test_long_silence_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            silent = Path(td) / "silent.wav"
            subprocess.run(
                ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000", "-t", "3", str(silent)],
                check=True,
            )

            quality = analyze_audio(silent)

            self.assertFalse(quality["ok"])
            self.assertGreater(quality["long_silence_ratio"], 0.8)


if __name__ == "__main__":
    unittest.main()

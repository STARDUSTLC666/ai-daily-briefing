import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from briefing.render import _edge_tts


class EdgeTtsTests(unittest.TestCase):
    def test_defaults_to_xiaoxiao_news_voice_at_eight_percent(self):
        with tempfile.TemporaryDirectory() as td:
            render_dir = Path(td)
            calls: list[list[str]] = []

            def fake_run(command, **_kwargs):
                calls.append(command)
                media = Path(command[command.index("--write-media") + 1])
                subtitles = Path(command[command.index("--write-subtitles") + 1])
                media.write_bytes(b"audio")
                subtitles.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8")

            with patch("briefing.render.shutil.which", return_value="py"), \
                patch("briefing.render._run", side_effect=fake_run), \
                patch.dict(os.environ, {"BRIEFING_EDGE_TTS_ATTEMPTS": "1"}, clear=True):
                _edge_tts(render_dir, [{"text": "news"}])

            self.assertEqual(len(calls), 1)
            command = calls[0]
            self.assertEqual(command[command.index("--voice") + 1], "zh-CN-XiaoxiaoNeural")
            self.assertIn("--rate=+8%", command)
            self.assertIn("--pitch=+0Hz", command)

    def test_retries_with_second_approved_female_voice_before_sapi_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            render_dir = Path(td)
            calls: list[str] = []

            def fake_run(command, **_kwargs):
                voice = command[command.index("--voice") + 1]
                calls.append(voice)
                if voice == "zh-CN-XiaoxiaoNeural":
                    raise RuntimeError("temporary edge outage")
                media = Path(command[command.index("--write-media") + 1])
                subtitles = Path(command[command.index("--write-subtitles") + 1])
                media.write_bytes(b"audio")
                subtitles.write_text("1\n00:00:00,000 --> 00:00:01,000\n你好\n", encoding="utf-8")

            with patch("briefing.render.shutil.which", return_value="py"), \
                patch("briefing.render._run", side_effect=fake_run), \
                patch("briefing.render.time.sleep"), \
                patch.dict(
                    os.environ,
                    {"BRIEFING_TTS_VOICE": "zh-CN-XiaoxiaoNeural", "BRIEFING_EDGE_TTS_ATTEMPTS": "1"},
                    clear=False,
                ):
                audios, subtitles, voice = _edge_tts(render_dir, [{"text": "第一段"}, {"text": "第二段"}])

            self.assertEqual(voice, "zh-CN-XiaoyiNeural")
            self.assertEqual(calls, ["zh-CN-XiaoxiaoNeural", "zh-CN-XiaoyiNeural", "zh-CN-XiaoyiNeural"])
            self.assertTrue(all(path.exists() for path in audios + subtitles))


if __name__ == "__main__":
    unittest.main()

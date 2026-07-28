import tempfile
import unittest
from pathlib import Path

from briefing.output_verify import parse_srt
from briefing.render import _write_srt


class RenderSubtitleTests(unittest.TestCase):
    def test_write_srt_prefers_edge_tts_subtitle_timing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            tts_srt = root / "seg_000.srt"
            tts_srt.write_text(
                "\n".join(
                    [
                        "1",
                        "00:00:00,100 --> 00:00:02,857",
                        "接下来这条看ModelScope / 阿里。",
                        "",
                        "2",
                        "00:00:02,807 --> 00:00:06,589",
                        "简单说，ModelScope 发布 v1.38.1。",
                        "",
                    ]
                ),
                encoding="utf-8-sig",
            )

            srt = _write_srt(
                root,
                [{"text": "接下来这条看ModelScope / 阿里。简单说，ModelScope 发布 v1.38.1。"}],
                [8.0],
                subtitle_paths=[tts_srt],
            )

            rows = parse_srt(srt)

            self.assertEqual(len(rows), 2)
            self.assertAlmostEqual(rows[0][0], 0.1, places=3)
            self.assertAlmostEqual(rows[0][1], 2.857, places=3)
            self.assertAlmostEqual(rows[1][0], 2.857, places=3)
            self.assertIn("ModelScope 发布", rows[1][2])

    def test_write_srt_falls_back_when_tts_subtitles_are_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            srt = _write_srt(
                root,
                [{"text": "第一句。第二句。"}],
                [4.0],
                subtitle_paths=[root / "missing.srt"],
            )

            rows = parse_srt(srt)

            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0][0], 0.0)
            self.assertAlmostEqual(rows[-1][1], 4.0, places=3)


if __name__ == "__main__":
    unittest.main()

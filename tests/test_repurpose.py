import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from briefing.repurpose import build_vertical_video, repurpose_run, write_wechat_article


FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def _write_script(run_dir: Path) -> None:
    segments = [
        {"kind": "intro", "title": "AI 日报", "text": "ChatGPT 重返欧洲 WhatsApp——早上好，这里是AI 日报，今天 2 条，马上开始。"},
        {
            "kind": "news",
            "title": "ChatGPT 在欧洲经济区重新接入 WhatsApp",
            "text": "ChatGPT 在欧洲经济区重新接入 WhatsApp。用户可向经验证的联系人提问、上传图片并使用多语言。",
            "cards": [
                {"title": "开放范围", "body": "ChatGPT 已重新在欧洲经济区的 WhatsApp 上可用"},
                {"title": "原文与时间", "body": "X / @ChatGPTapp|7月13日 21:05 发布|X 官方账号"},
            ],
        },
        {
            "kind": "news",
            "title": "阿里最新一代大模型千问3.8将至",
            "text": "据 36氪 报道，阿里最新一代大模型千问3.8即将发布并开源。",
            "cards": [
                {"title": "核心信息", "body": "预览版已率先上线阿里云"},
                {"title": "原文与时间", "body": "36氪|7月19日 16:43 发布|媒体报道"},
            ],
        },
        {"kind": "outro", "title": "播送完毕", "text": "今天的新闻播送完毕，我们明天见。"},
    ]
    (run_dir / "script.json").write_text(json.dumps(segments, ensure_ascii=False), encoding="utf-8")
    (run_dir / "bilibili.json").write_text(
        json.dumps({"title": "ChatGPT 重返欧洲 WhatsApp；千问3.8将至【AI 日报 2026-07-26】"}, ensure_ascii=False),
        encoding="utf-8",
    )


class WeChatArticleTests(unittest.TestCase):
    def test_article_reuses_gated_copy_with_sources(self):
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td)
            _write_script(run_dir)

            report = write_wechat_article(run_dir)

            self.assertEqual(report["status"], "ok")
            self.assertEqual(report["stories"], 2)
            text = (run_dir / "wechat-article.md").read_text(encoding="utf-8")
            self.assertIn("# ChatGPT 重返欧洲 WhatsApp；千问3.8将至【AI 日报 2026-07-26】", text)
            self.assertIn("## 01 ChatGPT 在欧洲经济区重新接入 WhatsApp", text)
            self.assertIn("> 来源：X / @ChatGPTapp|7月13日 21:05 发布|X 官方账号", text)
            self.assertIn("> 来源：36氪|7月19日 16:43 发布|媒体报道", text)
            self.assertIn("马上开始", text)

    def test_missing_script_is_a_clean_skip(self):
        with tempfile.TemporaryDirectory() as td:
            report = write_wechat_article(Path(td))

        self.assertEqual(report["status"], "skipped")


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe are required for vertical export tests")
class VerticalVideoTests(unittest.TestCase):
    def _make_master(self, run_dir: Path) -> None:
        subprocess.run(
            [
                FFMPEG, "-y",
                "-f", "lavfi", "-i", "color=c=steelblue:s=320x180:d=1",
                "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                str(run_dir / "final.mp4"),
            ],
            check=True, capture_output=True,
        )
        subprocess.run(
            [FFMPEG, "-y", "-f", "lavfi", "-i", "color=c=white:s=320x180:d=0.1", "-frames:v", "1", str(run_dir / "cover-16x9.png")],
            check=True, capture_output=True,
        )

    def _probe_dims(self, path: Path) -> tuple[int, int]:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json", str(path)],
            check=True, capture_output=True, text=True,
        ).stdout
        stream = json.loads(out)["streams"][0]
        return int(stream["width"]), int(stream["height"])

    def test_vertical_output_is_1080x1920_with_cover(self):
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td)
            self._make_master(run_dir)

            report = build_vertical_video(run_dir)

            self.assertEqual(report["status"], "ok")
            self.assertEqual(self._probe_dims(run_dir / "final-vertical.mp4"), (1080, 1920))
            self.assertEqual(report["cover_status"], "ok")
            self.assertEqual(self._probe_dims(run_dir / "cover-vertical.png"), (1080, 1920))

    def test_second_call_is_cached(self):
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td)
            self._make_master(run_dir)
            first = build_vertical_video(run_dir)
            second = build_vertical_video(run_dir)

            self.assertEqual(first["status"], "ok")
            self.assertEqual(second["status"], "cached")

    def test_repurpose_run_combines_both_outputs(self):
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td)
            self._make_master(run_dir)
            _write_script(run_dir)

            report = repurpose_run(run_dir)

            self.assertTrue(report["ok"])
            self.assertEqual(report["vertical"]["status"], "ok")
            self.assertEqual(report["wechat"]["status"], "ok")


class MissingMasterTests(unittest.TestCase):
    def test_vertical_skips_cleanly_without_master(self):
        with tempfile.TemporaryDirectory() as td:
            report = build_vertical_video(Path(td))

        self.assertEqual(report["status"], "skipped")


if __name__ == "__main__":
    unittest.main()

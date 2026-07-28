import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from briefing.remotion_renderer import _copy_evidence_assets, _load_cover_copy, _render_cover, _render_visual


class RemotionRendererTests(unittest.TestCase):
    def test_cover_is_rendered_from_dedicated_remotion_composition(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            project = root / "remotion"
            bin_dir = project / "node_modules" / ".bin"
            bin_dir.mkdir(parents=True)
            (bin_dir / "remotion.cmd").write_text("", encoding="utf-8")
            (project / "src").mkdir()
            (project / "src" / "index.ts").write_text("", encoding="utf-8")
            props = root / "run" / "render" / "props.json"
            props.parent.mkdir(parents=True)
            props.write_text("{}", encoding="utf-8")
            cover = root / "cover.png"

            with patch("briefing.remotion_renderer._browser_exe", return_value=None), patch("briefing.remotion_renderer._run") as run:
                run.return_value.stdout = b""
                run.return_value.stderr = b""
                _render_cover(project, props, cover, props.parent)

            command = run.call_args.args[0]
            self.assertEqual(command[1], "still")
            self.assertIn("BriefingCover43", command)
            self.assertIn(str(cover.resolve()), command)
            self.assertIn(f"--props={props.resolve()}", command)
            self.assertNotIn("ffmpeg", " ".join(command).lower())

    def test_cover_copy_is_normalized_and_keeps_dynamic_story_count(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            review = root / "review"
            review.mkdir()
            (review / "cover.json").write_text(
                '{"schema_version":1,"headline":"主标题","subheadline":"副标题","storyCount":2,"highlights":["一","二"],"entities":["OpenAI","字节"]}',
                encoding="utf-8",
            )
            slides = [
                {"kind": "news", "storyPosition": 1, "title": "一"},
                {"kind": "news", "storyPosition": 2, "title": "二"},
            ]

            cover, path = _load_cover_copy(root, slides)

            self.assertEqual(cover["storyCount"], 2)
            self.assertEqual(cover["headline"], "主标题")
            self.assertEqual(path, (review / "cover.json").resolve())
            self.assertIn('"storyCount": 2', path.read_text(encoding="utf-8"))

    def test_cover_renderer_can_emit_the_16x9_alternate(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            project = root / "remotion"
            bin_dir = project / "node_modules" / ".bin"
            bin_dir.mkdir(parents=True)
            (bin_dir / "remotion.cmd").write_text("", encoding="utf-8")
            (project / "src").mkdir()
            (project / "src" / "index.ts").write_text("", encoding="utf-8")
            props = root / "run" / "render" / "props.json"
            props.parent.mkdir(parents=True)
            props.write_text("{}", encoding="utf-8")
            cover = root / "cover-16x9.png"

            with patch("briefing.remotion_renderer._browser_exe", return_value=None), patch("briefing.remotion_renderer._run") as run:
                run.return_value.stdout = b""
                run.return_value.stderr = b""
                _render_cover(
                    project,
                    props,
                    cover,
                    props.parent,
                    composition="BriefingCover",
                    log_name="remotion-cover-16x9.log",
                )

            command = run.call_args.args[0]
            self.assertIn("BriefingCover", command)
            self.assertNotIn("BriefingCover43", command)
            self.assertIn(str(cover.resolve()), command)

    def test_cover_copy_rejects_hardcoded_invalid_story_count(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            review = root / "review"
            review.mkdir()
            (review / "cover.json").write_text('{"storyCount":100}', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "storyCount"):
                _load_cover_copy(root, [])

    def test_cover_copy_rejects_story_count_that_does_not_match_video(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            review = root / "review"
            review.mkdir()
            (review / "cover.json").write_text('{"storyCount":1}', encoding="utf-8")
            slides = [
                {"kind": "news", "storyPosition": 1, "title": "一"},
                {"kind": "news", "storyPosition": 2, "title": "二"},
            ]

            with self.assertRaisesRegex(ValueError, "match rendered news count: 2"):
                _load_cover_copy(root, slides)

    def test_visual_render_uses_absolute_props_file_on_windows(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            project = root / "remotion"
            bin_dir = project / "node_modules" / ".bin"
            bin_dir.mkdir(parents=True)
            (bin_dir / "remotion.cmd").write_text("", encoding="utf-8")
            (project / "src").mkdir()
            (project / "src" / "index.ts").write_text("", encoding="utf-8")
            props = root / "run" / "render" / "props.json"
            props.parent.mkdir(parents=True)
            props.write_text("{}", encoding="utf-8")
            output = root / "run" / "render" / "visual.mp4"

            with patch("briefing.remotion_renderer._browser_exe", return_value=None), patch("briefing.remotion_renderer._run") as run:
                run.return_value.stdout = b""
                run.return_value.stderr = b""
                _render_visual(project, props, output, props.parent)

            command = run.call_args.args[0]
            self.assertIn(f"--props={props.resolve()}", command)
            self.assertIn(str(output.resolve()), command)
            public_dir_index = command.index("--public-dir") + 1
            self.assertEqual(Path(command[public_dir_index]), props.parent.resolve() / "remotion-public")

    def test_evidence_assets_are_scoped_to_run_render_dir(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            render_dir = root / "run" / "render"
            source = root / "source.png"
            source.write_bytes(b"fresh-image")
            stale = render_dir / "remotion-public" / "evidence" / "stale.png"
            stale.parent.mkdir(parents=True)
            stale.write_bytes(b"stale-image")
            slides = [
                {
                    "index": 3,
                    "segmentIndex": 2,
                    "page": {
                        "kind": "cards",
                        "evidenceVisual": {
                            "image": str(source),
                            "source": "preview only",
                            "required": True,
                        },
                    },
                },
                {
                    "index": 4,
                    "segmentIndex": 2,
                    "page": {
                        "kind": "evidence",
                        "evidenceVisual": {
                            "image": str(source),
                            "source": "X / source",
                            "required": True,
                        }
                    },
                }
            ]

            contract = _copy_evidence_assets(render_dir, slides)

            self.assertFalse(stale.exists())
            self.assertEqual(len(contract), 1)
            self.assertEqual(contract[0]["slide_index"], 4)
            copied = render_dir / "remotion-public" / contract[0]["asset"]
            self.assertEqual(copied.read_bytes(), b"fresh-image")


if __name__ == "__main__":
    unittest.main()

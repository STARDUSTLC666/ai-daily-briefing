from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import wave
from unittest.mock import patch

from briefing.indextts2_backend import _split_tts_text, load_profile, synthesize_segments


class IndexTTS2BackendTests(unittest.TestCase):
    def _write_wav(self, path: Path, frames: int = 800) -> None:
        with wave.open(str(path), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(24000)
            stream.writeframes(bytes(frames * 2))

    def _profile(self, root: Path, profile_id: str, *, publishable: bool) -> Path:
        profile_dir = root / profile_id
        profile_dir.mkdir(parents=True)
        (profile_dir / "reference.wav").write_bytes(b"RIFF-test")
        (profile_dir / "profile.json").write_text(
            json.dumps(
                {
                    "reference_audio": "reference.wav",
                    "emotion_vector": [0.05, 0, 0, 0, 0, 0, 0.01, 0.7],
                    "emotion_weight": 0.45,
                    "publishable": publishable,
                }
            ),
            encoding="utf-8",
        )
        return profile_dir

    def test_nonpublishable_profile_requires_explicit_test_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile_root = Path(tmp)
            self._profile(profile_root, "smoke", publishable=False)
            env = {
                "BRIEFING_TTS_PROFILE": "smoke",
                "BRIEFING_VOICE_PROFILE_DIR": str(profile_root),
                "BRIEFING_ALLOW_NONPUBLISHABLE_PROFILE": "",
            }
            with patch.dict(os.environ, env, clear=False):
                with self.assertRaisesRegex(RuntimeError, "publishable=false"):
                    load_profile()

    def test_short_adjacent_sentences_share_one_inference_task(self) -> None:
        text = "第一句很短。第二句也很短。"
        self.assertEqual(_split_tts_text(text, max_chars=32), [text])

    def test_batch_uses_one_cli_process_and_expected_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            profile_root = base / "profiles"
            self._profile(profile_root, "daily_voice", publishable=True)
            index_root = base / "IndexTTS2"
            cli = index_root / ".venv" / "Scripts" / "indextts2.exe"
            cli.parent.mkdir(parents=True)
            cli.write_bytes(b"fake")
            (index_root / "checkpoints").mkdir()
            render_dir = base / "render"
            seen: list[list[str]] = []

            def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                seen.append(cmd)
                batch_path = Path(cmd[cmd.index("--batch-file") + 1])
                for line in batch_path.read_text(encoding="utf-8").splitlines():
                    row = json.loads(line)
                    self._write_wav(batch_path.parent / row["output"])
                return subprocess.CompletedProcess(cmd, 0, stdout="Batch complete: 2 tasks generated\n")

            env = {
                "BRIEFING_TTS_PROFILE": "daily_voice",
                "BRIEFING_VOICE_PROFILE_DIR": str(profile_root),
                "BRIEFING_INDEXTTS2_ROOT": str(index_root),
                "BRIEFING_ALLOW_NONPUBLISHABLE_PROFILE": "",
            }
            with patch.dict(os.environ, env, clear=False), patch(
                "briefing.indextts2_backend.subprocess.run", side_effect=fake_run
            ):
                outputs, profile_id = synthesize_segments(
                    render_dir,
                    [{"text": "第一条。"}, {"text": "第二条。"}],
                )

            self.assertEqual(profile_id, "daily_voice")
            self.assertEqual(len(seen), 1)
            self.assertEqual([path.name for path in outputs], ["indextts2_000.wav", "indextts2_001.wav"])
            self.assertTrue(all(path.exists() for path in outputs))
            self.assertIn("--fp16", seen[0])
            self.assertIn("--no-deepspeed", seen[0])
            self.assertTrue((render_dir / "indextts2.log").exists())

    def test_identical_second_run_reuses_valid_audio_without_loading_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            profile_root = base / "profiles"
            self._profile(profile_root, "daily_voice", publishable=True)
            index_root = base / "IndexTTS2"
            cli = index_root / ".venv" / "Scripts" / "indextts2.exe"
            cli.parent.mkdir(parents=True)
            cli.write_bytes(b"fake")
            (index_root / "checkpoints").mkdir()
            render_dir = base / "render"
            seen: list[list[str]] = []

            def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                seen.append(cmd)
                batch_path = Path(cmd[cmd.index("--batch-file") + 1])
                for line in batch_path.read_text(encoding="utf-8").splitlines():
                    row = json.loads(line)
                    self._write_wav(batch_path.parent / row["output"])
                return subprocess.CompletedProcess(cmd, 0, stdout="Batch complete\n")

            env = {
                "BRIEFING_TTS_PROFILE": "daily_voice",
                "BRIEFING_VOICE_PROFILE_DIR": str(profile_root),
                "BRIEFING_INDEXTTS2_ROOT": str(index_root),
                "BRIEFING_ALLOW_NONPUBLISHABLE_PROFILE": "",
            }
            segments = [{"text": "第一条。"}, {"text": "第二条。"}]
            with patch.dict(os.environ, env, clear=False), patch(
                "briefing.indextts2_backend.subprocess.run", side_effect=fake_run
            ):
                first, _ = synthesize_segments(render_dir, segments)
                second, _ = synthesize_segments(render_dir, segments)

            self.assertEqual(len(seen), 1)
            self.assertEqual(first, second)
            self.assertIn("Cache hit: reused all 2 tasks", (render_dir / "indextts2.log").read_text(encoding="utf-8-sig"))

    def test_long_segment_is_sentence_split_and_losslessly_concatenated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            profile_root = base / "profiles"
            self._profile(profile_root, "daily_voice", publishable=True)
            index_root = base / "IndexTTS2"
            cli = index_root / ".venv" / "Scripts" / "indextts2.exe"
            cli.parent.mkdir(parents=True)
            cli.write_bytes(b"fake")
            (index_root / "checkpoints").mkdir()
            render_dir = base / "render"
            batch_rows: list[dict[str, str]] = []

            def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                batch_path = Path(cmd[cmd.index("--batch-file") + 1])
                for line in batch_path.read_text(encoding="utf-8").splitlines():
                    row = json.loads(line)
                    batch_rows.append(row)
                    with wave.open(str(batch_path.parent / row["output"]), "wb") as stream:
                        stream.setnchannels(1)
                        stream.setsampwidth(2)
                        stream.setframerate(24000)
                        stream.writeframes(bytes(1600))
                return subprocess.CompletedProcess(cmd, 0, stdout="Batch complete\n")

            env = {
                "BRIEFING_TTS_PROFILE": "daily_voice",
                "BRIEFING_VOICE_PROFILE_DIR": str(profile_root),
                "BRIEFING_INDEXTTS2_ROOT": str(index_root),
                "BRIEFING_INDEXTTS2_MAX_CHARS": "32",
            }
            with patch.dict(os.environ, env, clear=False), patch(
                "briefing.indextts2_backend.subprocess.run", side_effect=fake_run
            ):
                outputs, _profile_id = synthesize_segments(
                    render_dir,
                    [{"text": "第一句。" + "这是一段需要拆分的较长新闻内容，" * 8 + "最后一句。"}],
                )

            self.assertGreater(len(batch_rows), 2)
            self.assertTrue(all(len(row["text"]) <= 32 for row in batch_rows))
            with wave.open(str(outputs[0]), "rb") as stream:
                self.assertEqual(stream.getframerate(), 24000)
                self.assertGreater(stream.getnframes(), 800 * len(batch_rows))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any

from briefing.core import clean_text, read_json, write_text_atomic
from .audio import ffmpeg_bin

VERTICAL_VIDEO_NAME = "final-vertical.mp4"
VERTICAL_COVER_NAME = "cover-vertical.png"
WECHAT_ARTICLE_NAME = "wechat-article.md"

_VERTICAL_FILTER = (
    "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
    "crop=1080:1920,boxblur=luma_radius=32:luma_power=2[bg];"
    "[0:v]scale=1080:-2[fg];"
    "[bg][fg]overlay=(W-w)/2:(H-h)/2"
)


def build_vertical_video(run_dir: Path) -> dict[str, str]:
    """Compose a 1080x1920 blur-padded vertical variant of final.mp4 + vertical cover."""
    root = Path(run_dir).resolve()
    source = root / "final.mp4"
    if not source.is_file() or source.stat().st_size == 0:
        return {"status": "skipped", "reason": "final.mp4 is missing"}
    try:
        ffmpeg = ffmpeg_bin()
    except RuntimeError:
        return {"status": "skipped", "reason": "ffmpeg not found"}

    video_out = root / VERTICAL_VIDEO_NAME
    cover_out = root / VERTICAL_COVER_NAME
    if (
        video_out.is_file()
        and video_out.stat().st_size > 0
        and video_out.stat().st_mtime >= source.stat().st_mtime
    ):
        return {
            "status": "cached",
            "video": str(video_out),
            "cover": str(cover_out) if cover_out.is_file() else "",
        }

    try:
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(source),
                "-filter_complex",
                _VERTICAL_FILTER,
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                "-c:a",
                "copy",
                str(video_out),
            ],
            check=True,
            capture_output=True,
            timeout=1800,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return {"status": "error", "reason": f"vertical video encode failed: {exc}"}

    cover_source = next(
        (p for p in (root / "cover-16x9.png", root / "cover.png") if p.is_file()),
        None,
    )
    cover_status = "skipped"
    if cover_source is not None:
        try:
            subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-i",
                    str(cover_source),
                    "-filter_complex",
                    _VERTICAL_FILTER,
                    "-frames:v",
                    "1",
                    str(cover_out),
                ],
                check=True,
                capture_output=True,
                timeout=300,
            )
            cover_status = "ok"
        except (subprocess.SubprocessError, OSError):
            cover_status = "error"

    return {
        "status": "ok",
        "video": str(video_out),
        "video_bytes": str(video_out.stat().st_size),
        "cover": str(cover_out) if cover_out.is_file() else "",
        "cover_status": cover_status,
    }


def _provenance_line(segment: dict[str, Any]) -> str:
    for card in list(segment.get("cards") or []):
        if isinstance(card, dict) and clean_text(str(card.get("title") or "")) == "原文与时间":
            return clean_text(str(card.get("body") or ""))
    return ""


def write_wechat_article(run_dir: Path) -> Path:
    """Render script.json into a WeChat-article Markdown draft and return its Path."""
    root = Path(run_dir).resolve()
    script_path = root / "script.json"
    if not script_path.is_file():
        raise FileNotFoundError(f"script.json is missing: {script_path}")

    raw = read_json(script_path)
    segments = raw if isinstance(raw, list) else list((raw or {}).get("segments") or [])
    segments = [seg for seg in segments if isinstance(seg, dict)]
    news = [seg for seg in segments if str(seg.get("kind") or "") == "news"]
    if not news:
        raise ValueError(f"script.json has no news segments: {script_path}")

    title = f"AI 日报 {root.name}"
    bili_path = root / "bilibili.json"
    if bili_path.is_file():
        bili = read_json(bili_path, default={})
        if isinstance(bili, dict) and clean_text(str(bili.get("title") or "")):
            title = clean_text(str(bili["title"]))

    intro = next((seg for seg in segments if str(seg.get("kind") or "") == "intro"), None)
    lines: list[str] = [f"# {title}", ""]
    if intro and clean_text(str(intro.get("text") or "")):
        lines.extend([clean_text(str(intro["text"])), ""])

    for idx, seg in enumerate(news, 1):
        heading = clean_text(str(seg.get("title") or f"新闻 {idx}"))
        lines.extend([f"## {idx:02d} {heading}", ""])
        narration = clean_text(str(seg.get("text") or ""))
        if narration:
            lines.extend([narration, ""])
        source_line = _provenance_line(seg)
        if source_line:
            lines.extend([f"> 来源：{source_line}", ""])

    lines.extend(
        [
            "---",
            "",
            "本文与视频版由同一条自动化流水线生成；证据截图与来源核验记录见视频版。",
        ]
    )
    out_path = root / WECHAT_ARTICLE_NAME
    write_text_atomic(out_path, "\n".join(lines) + "\n")
    return out_path


def repurpose_run(
    run_dir: Path,
    *,
    include_vertical: bool = True,
    include_wechat: bool = True,
) -> dict[str, Any]:
    """Derive vertical video and WeChat Markdown article from a finished run."""
    root = Path(run_dir).resolve()
    report: dict[str, Any] = {"run_dir": str(root)}
    report["vertical"] = (
        build_vertical_video(root)
        if include_vertical
        else {"status": "skipped", "reason": "disabled"}
    )
    if include_wechat:
        try:
            article_path = write_wechat_article(root)
            report["wechat"] = {"status": "ok", "article": str(article_path)}
        except FileNotFoundError as exc:
            report["wechat"] = {"status": "skipped", "reason": str(exc)}
        except Exception as exc:
            report["wechat"] = {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}
    else:
        report["wechat"] = {"status": "skipped", "reason": "disabled"}

    statuses = {report["vertical"]["status"], report["wechat"]["status"]}
    report["ok"] = "error" not in statuses
    return report

from __future__ import annotations

"""Derive multi-platform outputs from a finished, QA-passed run.

Everything here is downstream of the publish gates: the derived files are
convenience exports for Douyin/视频号 (vertical video), vertical cover, and a
WeChat-article draft. They never modify the attested Bilibili payload files,
so hash contracts and upload snapshots are unaffected.
"""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

VERTICAL_VIDEO_NAME = "final-vertical.mp4"
VERTICAL_COVER_NAME = "cover-vertical.png"
WECHAT_ARTICLE_NAME = "wechat-article.md"

_VERTICAL_FILTER = (
    "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
    "crop=1080:1920,boxblur=luma_radius=32:luma_power=2[bg];"
    "[0:v]scale=1080:-2[fg];"
    "[bg][fg]overlay=(W-w)/2:(H-h)/2"
)


def _ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def _run(cmd: list[str], timeout: int) -> None:
    subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)


def build_vertical_video(run_dir: Path, *, timeout_seconds: int = 1800) -> dict[str, Any]:
    """Compose a 1080x1920 blur-pad vertical variant of final.mp4.

    The standard "横屏内容竖屏壳" treatment: the 16:9 master centered over a
    blurred, filled copy of itself. Skipped when the master is missing, cached
    when the existing vertical is newer than the master.
    """
    run_dir = Path(run_dir)
    source = run_dir / "final.mp4"
    if not source.exists() or source.stat().st_size == 0:
        return {"status": "skipped", "reason": "final.mp4 is missing"}
    ffmpeg = _ffmpeg()
    if not ffmpeg:
        return {"status": "skipped", "reason": "ffmpeg not found"}
    video_out = run_dir / VERTICAL_VIDEO_NAME
    cover_out = run_dir / VERTICAL_COVER_NAME
    if (
        video_out.exists()
        and video_out.stat().st_size > 0
        and video_out.stat().st_mtime >= source.stat().st_mtime
    ):
        return {"status": "cached", "video": str(video_out), "cover": str(cover_out) if cover_out.exists() else ""}
    try:
        _run(
            [
                ffmpeg, "-y", "-i", str(source),
                "-filter_complex", _VERTICAL_FILTER,
                "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                "-c:a", "copy",
                str(video_out),
            ],
            timeout=timeout_seconds,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return {"status": "error", "reason": f"vertical video encode failed: {exc}"}
    cover_source = next(
        (path for path in [run_dir / "cover-16x9.png", run_dir / "cover.png"] if path.exists()),
        None,
    )
    cover_status = "skipped"
    if cover_source is not None:
        try:
            _run(
                [
                    ffmpeg, "-y", "-i", str(cover_source),
                    "-filter_complex", _VERTICAL_FILTER,
                    "-frames:v", "1",
                    str(cover_out),
                ],
                timeout=300,
            )
            cover_status = "ok"
        except (subprocess.SubprocessError, OSError):
            cover_status = "error"
    return {
        "status": "ok",
        "video": str(video_out),
        "video_bytes": video_out.stat().st_size,
        "cover": str(cover_out) if cover_out.exists() else "",
        "cover_status": cover_status,
    }


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _source_line(segment: dict[str, Any]) -> str:
    for card in segment.get("cards") or []:
        if isinstance(card, dict) and str(card.get("title") or "").strip() == "原文与时间":
            return str(card.get("body") or "").strip()
    return ""


def write_wechat_article(run_dir: Path) -> dict[str, Any]:
    """Render script.json into a WeChat-article Markdown draft.

    Same facts, same disclaimers, article form. Only spoken/visible copy that
    already passed the video gates is reused; no URLs are injected beyond the
    source labels that the video itself displays.
    """
    run_dir = Path(run_dir)
    script_path = run_dir / "script.json"
    if not script_path.exists():
        return {"status": "skipped", "reason": "script.json is missing"}
    try:
        raw = _load_json(script_path)
    except (OSError, ValueError) as exc:
        return {"status": "error", "reason": f"script.json parse failed: {exc}"}
    segments = raw if isinstance(raw, list) else raw.get("segments", [])
    segments = [seg for seg in segments if isinstance(seg, dict)]
    news = [seg for seg in segments if str(seg.get("kind") or "") == "news"]
    if not news:
        return {"status": "skipped", "reason": "no news segments"}
    title = f"AI 日报 {run_dir.name}"
    try:
        payload = _load_json(run_dir / "bilibili.json")
        if str(payload.get("title") or "").strip():
            title = str(payload["title"]).strip()
    except (OSError, ValueError):
        pass
    intro = next((seg for seg in segments if str(seg.get("kind") or "") == "intro"), None)
    lines: list[str] = [f"# {title}", ""]
    if intro and str(intro.get("text") or "").strip():
        lines.extend([str(intro["text"]).strip(), ""])
    for index, segment in enumerate(news, 1):
        heading = str(segment.get("title") or f"新闻 {index}").strip()
        lines.append(f"## {index:02d} {heading}")
        lines.append("")
        narration = str(segment.get("text") or "").strip()
        if narration:
            lines.append(narration)
            lines.append("")
        source_line = _source_line(segment)
        if source_line:
            lines.append(f"> 来源：{source_line}")
            lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("本文与视频版由同一条自动化流水线生成；证据截图与来源核验记录见视频版。")
    out_path = run_dir / WECHAT_ARTICLE_NAME
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"status": "ok", "article": str(out_path), "stories": len(news)}


def repurpose_run(run_dir: Path, *, skip_vertical: bool = False, skip_wechat: bool = False) -> dict[str, Any]:
    run_dir = Path(run_dir)
    report: dict[str, Any] = {"run_dir": str(run_dir)}
    report["vertical"] = {"status": "skipped", "reason": "disabled"} if skip_vertical else build_vertical_video(run_dir)
    report["wechat"] = {"status": "skipped", "reason": "disabled"} if skip_wechat else write_wechat_article(run_dir)
    statuses = {report["vertical"]["status"], report["wechat"]["status"]}
    report["ok"] = "error" not in statuses
    return report

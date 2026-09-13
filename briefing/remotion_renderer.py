from __future__ import annotations

import json
import os
import shutil
import subprocess
import hashlib
import re
from pathlib import Path
from typing import Any


FPS = 30
DEFAULT_RENDER_TIMEOUT_SECONDS = 7200
COVER_SCHEMA_VERSION = 1
COVER_TEXT_FIELDS = ("eyebrow", "headline", "subheadline", "badge", "date")
COVER_LIST_FIELDS = ("highlights", "entities")


def _quality_size(quality: str) -> tuple[int, int]:
    return (3840, 2160) if quality == "4k" else (1920, 1080)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _remotion_dir() -> Path:
    return _repo_root() / "remotion"


def _npm() -> str:
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        raise RuntimeError("npm was not found; Remotion renderer requires Node.js/npm")
    return npm


def _ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg was not found; Remotion video needs audio muxing")
    return ffmpeg


def _remotion_bin(project_dir: Path) -> Path:
    suffix = ".cmd" if os.name == "nt" else ""
    return project_dir / "node_modules" / ".bin" / f"remotion{suffix}"


def _remotion_timeout_seconds() -> int:
    raw = os.environ.get("BRIEFING_REMOTION_TIMEOUT_SECONDS", "").strip()
    if raw:
        try:
            return max(300, int(raw))
        except ValueError:
            pass
    return DEFAULT_RENDER_TIMEOUT_SECONDS


def _postprocess_timeout_seconds() -> int:
    """Budget for the full-video ffmpeg passes (mux, subtitle burn).

    These re-encode the entire video, so a 4K or long edition can legitimately take far
    longer than a few minutes; a too-small cap throws away an already-successful render.
    """
    raw = os.environ.get("BRIEFING_REMOTION_POSTPROCESS_TIMEOUT_SECONDS", "").strip()
    if raw:
        try:
            return max(120, int(raw))
        except ValueError:
            pass
    return 1800


def _run(cmd: list[str], cwd: Path | None = None, timeout: int | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.setdefault("CI", "1")
    env.setdefault("NO_UPDATE_NOTIFIER", "1")
    env.setdefault("REMOTION_DISABLE_UPDATE_CHECK", "1")
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        env=env,
    )


def _browser_exe() -> str | None:
    configured = os.environ.get("BRIEFING_CHROME") or os.environ.get("CHROME")
    candidates = [
        configured,
        shutil.which("chrome"),
        shutil.which("chrome.exe"),
        shutil.which("msedge"),
        shutil.which("msedge.exe"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(candidate)
    return None


def _ensure_remotion_dependencies(project_dir: Path, render_dir: Path) -> None:
    if _remotion_bin(project_dir).exists():
        return
    log = render_dir / "remotion-install.log"
    cmd = [_npm(), "ci"] if (project_dir / "package-lock.json").exists() else [_npm(), "install"]
    try:
        completed = _run(cmd, cwd=project_dir, timeout=240)
        log.write_bytes((completed.stdout or b"") + b"\n--- STDERR ---\n" + (completed.stderr or b""))
    except subprocess.CalledProcessError as exc:
        log.write_bytes((exc.stdout or b"") + b"\n--- STDERR ---\n" + (exc.stderr or b""))
        raise


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _render_public_dir(render_dir: Path) -> Path:
    return Path(render_dir).resolve() / "remotion-public"


def _copy_evidence_assets(render_dir: Path, slides: list[dict[str, Any]]) -> list[dict[str, Any]]:
    public_root = _render_public_dir(render_dir)
    if public_root.exists():
        if public_root.parent != Path(render_dir).resolve():
            raise RuntimeError(f"refusing to reset unexpected Remotion public dir: {public_root}")
        shutil.rmtree(public_root)
    public_dir = public_root / "evidence"
    public_dir.mkdir(parents=True, exist_ok=True)
    contract: list[dict[str, Any]] = []
    for slide in slides:
        page = slide.get("page") if isinstance(slide.get("page"), dict) else {}
        if str(page.get("kind") or "") != "evidence":
            continue
        visual = page.get("evidenceVisual") if isinstance(page.get("evidenceVisual"), dict) else None
        if not visual:
            continue
        source = Path(str(visual.get("image") or ""))
        if not source.exists() or not source.is_file():
            if bool(visual.get("required")):
                raise RuntimeError(f"required evidence screenshot is missing: {source}")
            continue
        source_hash = _file_sha256(source)
        target = public_dir / f"{source_hash[:16]}{source.suffix.lower() or '.png'}"
        if not target.exists() or target.stat().st_size != source.stat().st_size:
            shutil.copy2(source, target)
        visual["asset"] = f"evidence/{target.name}"
        contract.append(
            {
                "slide_index": int(slide.get("index") or 0),
                "segment_index": int(slide.get("segmentIndex") or 0),
                "required": bool(visual.get("required")),
                "source": str(visual.get("source") or ""),
                "source_path": str(source.resolve()),
                "source_sha256": source_hash,
                "asset": visual["asset"],
                "rendered": False,
            }
        )
    return contract


def _unique_news_slides(slides: list[dict[str, Any]]) -> list[dict[str, Any]]:
    news: list[dict[str, Any]] = []
    seen: set[str] = set()
    for slide in slides:
        if str(slide.get("kind") or "") != "news":
            continue
        key = str(
            slide.get("storyPosition")
            or slide.get("segmentIndex")
            or slide.get("title")
            or slide.get("index")
        )
        if key in seen:
            continue
        seen.add(key)
        news.append(slide)
    return news


def _default_cover_copy(out_dir: Path, slides: list[dict[str, Any]]) -> dict[str, Any]:
    news = _unique_news_slides(slides)
    timeline_items = list((slides[0].get("timelineItems") if slides else None) or [])
    entities = [
        str(item.get("entity") or item.get("label") or "").strip()
        for item in timeline_items
        if isinstance(item, dict) and str(item.get("kind") or "") == "news"
    ]
    titles = [str(slide.get("title") or "").strip() for slide in news]
    return {
        "eyebrow": "AI DAILY BRIEF / AI 日报",
        "headline": titles[-1] if titles else "今天的 AI 变化",
        "subheadline": titles[min(3, len(titles) - 1)] if titles else "模型、产品与开发者动态",
        "badge": f"{len(news):02d} 条 AI 动态" if news else "AI 动态",
        "date": out_dir.name,
        "storyCount": len(news),
        "highlights": titles[:3],
        "entities": [value for value in entities if value][:6],
    }


def _load_cover_copy(out_dir: Path, slides: list[dict[str, Any]]) -> tuple[dict[str, Any], Path]:
    path = Path(out_dir).resolve() / "review" / "cover.json"
    defaults = _default_cover_copy(Path(out_dir).resolve(), slides)
    raw: dict[str, Any] = {}
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ValueError(f"cover copy is not valid JSON ({exc}): {path}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"cover copy must be a JSON object: {path}")
        if payload.get("schema_version", COVER_SCHEMA_VERSION) != COVER_SCHEMA_VERSION:
            raise ValueError(f"unsupported cover copy schema_version: {payload.get('schema_version')}")
        raw = payload

    cover = dict(defaults)
    for field in COVER_TEXT_FIELDS:
        if field in raw:
            value = raw[field]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"cover copy {field} must be a non-empty string")
            cover[field] = value.strip()
    if "storyCount" in raw:
        value = raw["storyCount"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 99:
            raise ValueError("cover copy storyCount must be an integer from 0 to 99")
        if value != defaults["storyCount"]:
            raise ValueError(
                f"cover copy storyCount must match rendered news count: {defaults['storyCount']}"
            )
        cover["storyCount"] = value
    for field in COVER_LIST_FIELDS:
        if field in raw:
            value = raw[field]
            if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
                raise ValueError(f"cover copy {field} must be a list of non-empty strings")
            cover[field] = [item.strip() for item in value]

    path.parent.mkdir(parents=True, exist_ok=True)
    document = {"schema_version": COVER_SCHEMA_VERSION, **cover}
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    return cover, path


def _write_remotion_input(
    render_dir: Path,
    slides: list[dict[str, Any]],
    quality: str,
    cover_copy: dict[str, Any] | None = None,
) -> Path:
    from .render_layers import attach_render_layers, write_render_layer_manifest

    width, height = _quality_size(quality)
    evidence_contract = _copy_evidence_assets(render_dir, slides)
    (render_dir / "evidence-visual-contract.json").write_text(
        json.dumps({"schema_version": 1, "renderer": "remotion", "items": evidence_contract}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    layered_slides = attach_render_layers(slides, render_dir, _repo_root())
    layer_manifest = write_render_layer_manifest(render_dir, slides, quality, _repo_root())
    payload = {
        "visualStyle": os.environ.get("BRIEFING_VISUAL_STYLE", "newsroom"),
        "fps": FPS,
        "width": width,
        "height": height,
        "renderLayers": str(layer_manifest),
        "slides": layered_slides,
    }
    if cover_copy is not None:
        payload["cover"] = cover_copy
    path = render_dir / "remotion-input.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _render_visual(project_dir: Path, props: Path, output: Path, render_dir: Path) -> None:
    project_dir = Path(project_dir).resolve()
    entry = (project_dir / "src" / "index.ts").resolve()
    bin_path = _remotion_bin(project_dir).resolve()
    props = Path(props).resolve()
    output = Path(output).resolve()
    cmd = [
        str(bin_path),
        "render",
        str(entry),
        "BriefingVideo",
        str(output),
        f"--props={props}",
        "--codec",
        "h264",
        "--pixel-format",
        "yuv420p",
        "--crf",
        "20",
        "--overwrite",
        "--public-dir",
        str(_render_public_dir(render_dir)),
    ]
    browser = _browser_exe()
    if browser:
        cmd.extend(["--browser-executable", browser])
    try:
        completed = _run(cmd, cwd=project_dir, timeout=_remotion_timeout_seconds())
        (render_dir / "remotion-render.log").write_bytes((completed.stdout or b"") + b"\n--- STDERR ---\n" + (completed.stderr or b""))
    except subprocess.CalledProcessError as exc:
        (render_dir / "remotion-render.log").write_bytes((exc.stdout or b"") + b"\n--- STDERR ---\n" + (exc.stderr or b""))
        raise


def _render_cover(
    project_dir: Path,
    props: Path,
    output: Path,
    render_dir: Path,
    *,
    composition: str = "BriefingCover43",
    log_name: str = "remotion-cover.log",
) -> None:
    project_dir = Path(project_dir).resolve()
    entry = (project_dir / "src" / "index.ts").resolve()
    bin_path = _remotion_bin(project_dir).resolve()
    props = Path(props).resolve()
    output = Path(output).resolve()
    cmd = [
        str(bin_path),
        "still",
        str(entry),
        composition,
        str(output),
        f"--props={props}",
        "--overwrite",
        "--public-dir",
        str(_render_public_dir(render_dir)),
    ]
    browser = _browser_exe()
    if browser:
        cmd.extend(["--browser-executable", browser])
    try:
        completed = _run(cmd, cwd=project_dir, timeout=_remotion_timeout_seconds())
        (render_dir / log_name).write_bytes(
            (completed.stdout or b"") + b"\n--- STDERR ---\n" + (completed.stderr or b"")
        )
    except subprocess.CalledProcessError as exc:
        (render_dir / log_name).write_bytes(
            (exc.stdout or b"") + b"\n--- STDERR ---\n" + (exc.stderr or b"")
        )
        raise


def _mux_audio(ffmpeg: str, visual: Path, narration: Path, final_video: Path) -> None:
    _run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(visual),
            "-i",
            str(narration),
            "-vf",
            "scale=in_range=pc:out_range=tv,format=yuv420p",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-b:a",
            "160k",
            str(final_video),
        ],
        cwd=visual.parent,
        timeout=_postprocess_timeout_seconds(),
    )


def _srt_seconds(value: str) -> float:
    match = re.match(r"(\d+):(\d+):(\d+),(\d+)", value.strip())
    if not match:
        return 0.0
    hour, minute, second, ms = [int(x) for x in match.groups()]
    return hour * 3600 + minute * 60 + second + ms / 1000


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hour = int(seconds // 3600)
    minute = int((seconds % 3600) // 60)
    second = int(seconds % 60)
    centisecond = int(round((seconds - int(seconds)) * 100))
    return f"{hour}:{minute:02d}:{second:02d}.{centisecond:02d}"


def _escape_ass(text: str) -> str:
    return (
        str(text or "")
        .replace("{", "（")
        .replace("}", "）")
        .replace("\r", "")
        .replace("\n", "\\N")
    )


def _read_srt(path: Path) -> list[tuple[float, float, str]]:
    text = path.read_text(encoding="utf-8-sig")
    rows: list[tuple[float, float, str]] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [line.strip("\ufeff") for line in block.splitlines() if line.strip()]
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        start_raw, end_raw = [x.strip() for x in lines[1].split("-->", 1)]
        rows.append((_srt_seconds(start_raw), _srt_seconds(end_raw), "\n".join(lines[2:])))
    return rows


def _write_subtitle_ass(render_dir: Path, srt: Path, quality: str) -> Path:
    width, height = _quality_size(quality)
    scale = width / 1920

    def sz(value: int) -> int:
        return max(1, int(round(value * scale)))

    cues = _read_srt(srt)
    if not cues:
        raise RuntimeError("subtitles.srt has no cues to burn")
    ass = render_dir / "remotion-subtitles.ass"
    header = f"""[Script Info]
ScriptType: v4.00+
ScaledBorderAndShadow: yes
PlayResX: {width}
PlayResY: {height}

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Subtitle,Microsoft YaHei UI,{sz(34)},&H00FFFFFF,&H000000FF,&H00324552,&HAA172432,-1,0,0,0,100,100,0,0,1,{sz(3)},{sz(1)},2,{sz(180)},{sz(180)},{sz(118)},1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    events = [header]
    for start, end, text in cues:
        if end <= start:
            continue
        events.append(f"Dialogue: 20,{_ass_time(start)},{_ass_time(end)},Subtitle,,0,0,0,,{_escape_ass(text)}\n")
    ass.write_text("".join(events), encoding="utf-8-sig")
    return ass


def _burn_subtitles(ffmpeg: str, render_dir: Path, input_video: Path, subtitle_ass: Path, final_video: Path) -> None:
    _run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(input_video),
            "-vf",
            f"ass={subtitle_ass.name}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-c:a",
            "copy",
            str(final_video),
        ],
        cwd=render_dir,
        timeout=_postprocess_timeout_seconds(),
    )


def render_remotion_video(
    out_dir: Path,
    render_dir: Path,
    slides: list[dict[str, Any]],
    durations: list[float],
    narration: Path,
    srt: Path,
    script_json: Path,
    quality: str,
    tts_status: str,
) -> dict[str, str]:
    out_dir = Path(out_dir).resolve()
    render_dir = Path(render_dir).resolve()
    narration = Path(narration).resolve()
    srt = Path(srt).resolve()
    script_json = Path(script_json).resolve()
    project_dir = _remotion_dir()
    if not (project_dir / "package.json").exists():
        raise RuntimeError(f"Remotion project is missing: {project_dir}")
    render_dir.mkdir(parents=True, exist_ok=True)
    _ensure_remotion_dependencies(project_dir, render_dir)

    cover_copy, cover_copy_path = _load_cover_copy(out_dir, slides)
    props = _write_remotion_input(render_dir, slides, quality, cover_copy=cover_copy)
    visual = render_dir / "remotion-visual.mp4"
    with_audio = render_dir / "remotion-with-audio.mp4"
    final_video = out_dir / "final.mp4"
    cover = out_dir / "cover.png"
    cover_16x9 = out_dir / "cover-16x9.png"
    ffmpeg = _ffmpeg()

    _render_visual(project_dir, props, visual, render_dir)
    _render_cover(project_dir, props, cover, render_dir)
    _render_cover(
        project_dir,
        props,
        cover_16x9,
        render_dir,
        composition="BriefingCover",
        log_name="remotion-cover-16x9.log",
    )
    evidence_contract_path = render_dir / "evidence-visual-contract.json"
    if evidence_contract_path.exists():
        evidence_contract = json.loads(evidence_contract_path.read_text(encoding="utf-8"))
        for item in evidence_contract.get("items") or []:
            if isinstance(item, dict):
                item["rendered"] = True
        evidence_contract["visual_sha256"] = _file_sha256(visual)
        evidence_contract_path.write_text(json.dumps(evidence_contract, ensure_ascii=False, indent=2), encoding="utf-8")
    _mux_audio(ffmpeg, visual, narration, with_audio)
    subtitle_ass = _write_subtitle_ass(render_dir, srt, quality)
    _burn_subtitles(ffmpeg, render_dir, with_audio, subtitle_ass, final_video)
    if evidence_contract_path.exists():
        evidence_contract = json.loads(evidence_contract_path.read_text(encoding="utf-8"))
        evidence_contract["final_media_sha256"] = _file_sha256(final_video)
        evidence_contract_path.write_text(json.dumps(evidence_contract, ensure_ascii=False, indent=2), encoding="utf-8")
    width, height = _quality_size(quality)
    subtitle_cues = _read_srt(srt)
    subtitle_contract = "\n".join(f"{start:.3f}|{end:.3f}|{text}" for start, end, text in subtitle_cues)
    info = {
        "status": "ok",
        "renderer": "remotion",
        "transition_renderer": "@remotion/transitions",
        "subtitle_renderer": "ass-libass",
        "subtitle_burned": "true",
        "tts": tts_status,
        "segments": str(len({str(slide.get("segmentIndex", slide.get("index", 0))) for slide in slides})),
        "slides": str(len(slides)),
        "duration_seconds": f"{sum(durations):.2f}",
        "resolution": f"{width}x{height}",
        "video": str(final_video),
        "cover": str(cover),
        "cover_renderer": "remotion-still",
        "cover_composition": "BriefingCover43",
        "cover_16x9": str(cover_16x9),
        "cover_16x9_composition": "BriefingCover",
        "cover_copy": str(cover_copy_path),
        "subtitles": str(srt),
        "subtitle_ass": str(subtitle_ass),
        "subtitle_cue_count": str(len(subtitle_cues)),
        "subtitle_contract_sha256": hashlib.sha256(subtitle_contract.encode("utf-8")).hexdigest(),
        "script": str(script_json),
        "remotion_input": str(props),
        "remotion_visual": str(visual),
        "render_layers": str(render_dir / "render-layers.json"),
        "evidence_visual_contract": str(evidence_contract_path),
    }
    (out_dir / "render-info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8-sig")
    return info

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

from briefing.core import (
    EvidenceCard,
    ensure_dir,
    file_sha256,
    project_root,
    read_json,
    write_json_atomic,
    write_text_atomic,
)
from briefing.verify import record_render_contract
from .audio import analyze_audio, ffmpeg_bin, probe_duration
from .manuscript import build_remotion_slides, build_segments, select_manuscript
from .subtitles import write_srt, write_subtitle_ass
from .tts import synthesize_narration

COVER_SCHEMA_VERSION = 1
COVER_TEXT_FIELDS = ("eyebrow", "headline", "subheadline", "badge", "date")
COVER_LIST_FIELDS = ("highlights", "entities")
DEFAULT_REMOTION_TIMEOUT = 7200
DEFAULT_POSTPROCESS_TIMEOUT = 1800

_VISUAL_QA_CHECKS = (
    "audio_no_artifacts",
    "cover_truthful",
    "evidence_readable",
    "layout_no_clipping",
    "motion_coherent",
    "narration_complete",
    "no_broken_frames",
    "subtitle_sync",
    "subtitles_safe_area",
    "text_legible",
)


def _quality_dims(quality: str) -> tuple[int, int]:
    return (3840, 2160) if str(quality).lower() == "4k" else (1920, 1080)


def _remotion_dir() -> Path:
    return project_root() / "remotion"


def _remotion_bin(remotion_dir: Path) -> Path:
    suffix = ".cmd" if os.name == "nt" else ""
    return remotion_dir / "node_modules" / ".bin" / f"remotion{suffix}"


def _npm_bin() -> str:
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        raise RuntimeError("npm not found; Remotion renderer requires Node.js/npm")
    return npm


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
    for cand in candidates:
        if cand and Path(cand).is_file():
            return str(cand)
    return None


def _run_remotion_cmd(cmd: list[str], *, cwd: Path, timeout: int) -> subprocess.CompletedProcess[bytes]:
    env = os.environ.copy()
    env.setdefault("CI", "1")
    env.setdefault("NO_UPDATE_NOTIFIER", "1")
    env.setdefault("REMOTION_DISABLE_UPDATE_CHECK", "1")
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        env=env,
    )


def _ensure_remotion_deps(remotion_dir: Path, render_dir: Path, *, force: bool = False) -> None:
    if not force and _remotion_bin(remotion_dir).is_file():
        return
    npm = _npm_bin()
    cmd = [npm, "ci"] if (remotion_dir / "package-lock.json").is_file() else [npm, "install"]
    log_path = render_dir / "remotion-install.log"
    try:
        completed = _run_remotion_cmd(cmd, cwd=remotion_dir, timeout=600)
        log_path.write_bytes((completed.stdout or b"") + b"\n--- STDERR ---\n" + (completed.stderr or b""))
    except subprocess.CalledProcessError as exc:
        log_path.write_bytes((exc.stdout or b"") + b"\n--- STDERR ---\n" + (exc.stderr or b""))
        raise


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


def _default_cover_copy(run_dir: Path, slides: list[dict[str, Any]]) -> dict[str, Any]:
    news = _unique_news_slides(slides)
    timeline_items = list((slides[0].get("timelineItems") if slides else None) or [])
    entities = [
        str(item.get("entity") or item.get("label") or "").strip()
        for item in timeline_items
        if isinstance(item, dict) and str(item.get("kind") or "") == "news"
    ]
    titles = [str(slide.get("title") or "").strip() for slide in news if str(slide.get("title") or "").strip()]
    return {
        "eyebrow": "AI DAILY BRIEF / AI 日报",
        "headline": titles[0] if titles else "今天的 AI 变化",
        "subheadline": titles[1] if len(titles) > 1 else (titles[0] if titles else "模型、产品与开发者动态"),
        "badge": f"{len(news):02d} 条 AI 动态" if news else "AI 动态",
        "date": run_dir.name,
        "storyCount": len(news),
        "highlights": titles[:3],
        "entities": [e for e in entities if e][:6],
    }


def load_cover_copy(run_dir: Path, slides: list[dict[str, Any]]) -> dict[str, Any]:
    """Load, validate, and normalize review/cover.json with lead-story headline default (titles[0])."""
    root = Path(run_dir).resolve()
    cover_path = root / "review" / "cover.json"
    defaults = _default_cover_copy(root, slides)
    raw: dict[str, Any] = {}
    if cover_path.is_file():
        payload = read_json(cover_path)
        if not isinstance(payload, dict):
            raise ValueError(f"cover copy must be a JSON object: {cover_path}")
        if payload.get("schema_version", COVER_SCHEMA_VERSION) != COVER_SCHEMA_VERSION:
            raise ValueError(f"unsupported cover copy schema_version: {payload.get('schema_version')}")
        raw = payload

    cover = dict(defaults)
    for field in COVER_TEXT_FIELDS:
        if field in raw:
            val = raw[field]
            if not isinstance(val, str) or not val.strip():
                raise ValueError(f"cover copy {field} must be a non-empty string")
            cover[field] = val.strip()

    if "storyCount" in raw:
        val = raw["storyCount"]
        if isinstance(val, bool) or not isinstance(val, int) or val < 0 or val > 99:
            raise ValueError("cover copy storyCount must be an integer in 0..99")
        if val != defaults["storyCount"]:
            raise ValueError(
                f"cover copy storyCount ({val}) must match unique news count ({defaults['storyCount']})"
            )
        cover["storyCount"] = val

    for field in COVER_LIST_FIELDS:
        if field in raw:
            val = raw[field]
            if not isinstance(val, list) or not all(isinstance(x, str) and x.strip() for x in val):
                raise ValueError(f"cover copy {field} must be a list of non-empty strings")
            cover[field] = [x.strip() for x in val]

    write_json_atomic(cover_path, {"schema_version": COVER_SCHEMA_VERSION, **cover})
    return cover


def copy_evidence_assets(run_dir: Path, render_dir: Path, slides: list[dict[str, Any]]) -> dict[str, Any]:
    """Copy verified evidence images into remotion-public/evidence/<sha256[:16]>.png and write contract."""
    root = Path(run_dir).resolve()
    rdir = ensure_dir(Path(render_dir).resolve())
    public_root = rdir / "remotion-public"
    if public_root.exists():
        if public_root.parent != rdir:
            raise RuntimeError(f"refusing to reset unexpected public dir: {public_root}")
        shutil.rmtree(public_root)
    evidence_dir = ensure_dir(public_root / "evidence")

    items: list[dict[str, Any]] = []
    for slide in slides:
        page = slide.get("page") if isinstance(slide.get("page"), dict) else {}
        visual = page.get("evidenceVisual") if isinstance(page.get("evidenceVisual"), dict) else None
        if not visual:
            continue
        raw_img = str(visual.get("image") or "").strip()
        if not raw_img:
            if bool(visual.get("required")):
                raise RuntimeError(f"required evidence screenshot path is empty on slide {slide.get('index')}")
            continue
        src = Path(raw_img)
        if not src.is_absolute():
            src = (root / src).resolve()
        if not src.is_file():
            if bool(visual.get("required")):
                raise RuntimeError(f"required evidence screenshot is missing: {src}")
            continue

        src_sha = file_sha256(src)
        dst = evidence_dir / f"{src_sha[:16]}.png"
        if not dst.is_file():
            if src.suffix.lower() == ".png":
                shutil.copy2(src, dst)
            else:
                from PIL import Image

                with Image.open(src) as im:
                    im.save(dst, format="PNG")
        visual["asset"] = f"evidence/{dst.name}"
        if str(page.get("kind") or "") == "evidence":
            items.append(
                {
                    "slide_index": int(slide.get("index") or 0),
                    "segment_index": int(slide.get("segmentIndex") or 0),
                    "required": bool(visual.get("required")),
                    "source": str(visual.get("source") or ""),
                    "source_path": str(src),
                    "source_sha256": src_sha,
                    "asset": visual["asset"],
                    "rendered": False,
                }
            )

    contract: dict[str, Any] = {
        "schema_version": 1,
        "renderer": "remotion",
        "items": items,
    }
    write_json_atomic(rdir / "evidence-visual-contract.json", contract)
    return contract


def _render_remotion_target(
    remotion_dir: Path,
    render_dir: Path,
    *,
    mode: str,
    composition: str,
    props_path: Path,
    output_path: Path,
    log_name: str,
) -> None:
    entry = (remotion_dir / "src" / "index.ts").resolve()
    bin_path = _remotion_bin(remotion_dir).resolve()
    public_dir = (render_dir / "remotion-public").resolve()
    cmd = [
        str(bin_path),
        mode,
        str(entry),
        composition,
        str(output_path.resolve()),
        f"--props={props_path.resolve()}",
        "--overwrite",
        "--public-dir",
        str(public_dir),
    ]
    if mode == "render":
        cmd.extend(["--codec", "h264", "--pixel-format", "yuv420p", "--crf", "20"])
    browser = _browser_exe()
    if browser:
        cmd.extend(["--browser-executable", browser])
    timeout = int(os.environ.get("BRIEFING_REMOTION_TIMEOUT_SECONDS", "") or DEFAULT_REMOTION_TIMEOUT)
    try:
        completed = _run_remotion_cmd(cmd, cwd=remotion_dir, timeout=max(300, timeout))
        (render_dir / log_name).write_bytes((completed.stdout or b"") + b"\n--- STDERR ---\n" + (completed.stderr or b""))
    except subprocess.CalledProcessError as exc:
        (render_dir / log_name).write_bytes((exc.stdout or b"") + b"\n--- STDERR ---\n" + (exc.stderr or b""))
        raise


def render_remotion_bundle(
    run_dir: Path,
    *,
    slides: list[dict[str, Any]],
    narration_wav: Path,
    subtitles_srt: Path,
    cues: list[dict[str, Any]],
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    tts_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Render Remotion video + 4:3/16:9 covers and mux audio + burn ASS subtitles in a single FFmpeg pass."""
    root = ensure_dir(Path(run_dir).resolve())
    render_dir = ensure_dir(root / "render")
    remotion_dir = _remotion_dir()
    if not (remotion_dir / "package.json").is_file():
        raise RuntimeError(f"Remotion project missing at {remotion_dir}")
    _ensure_remotion_deps(remotion_dir, render_dir)

    cover_copy = load_cover_copy(root, slides)
    evidence_contract = copy_evidence_assets(root, render_dir, slides)
    props_path = render_dir / "remotion-input.json"
    write_json_atomic(
        props_path,
        {
            "visualStyle": os.environ.get("BRIEFING_VISUAL_STYLE", "newsroom"),
            "fps": int(fps),
            "width": int(width),
            "height": int(height),
            "slides": slides,
            "cover": cover_copy,
        },
    )

    visual_mp4 = render_dir / "remotion-visual.mp4"
    cover_png = root / "cover.png"
    cover_16x9_png = root / "cover-16x9.png"
    final_mp4 = root / "final.mp4"

    _render_remotion_target(
        remotion_dir,
        render_dir,
        mode="render",
        composition="BriefingVideo",
        props_path=props_path,
        output_path=visual_mp4,
        log_name="remotion-render.log",
    )
    _render_remotion_target(
        remotion_dir,
        render_dir,
        mode="still",
        composition="BriefingCover43",
        props_path=props_path,
        output_path=cover_png,
        log_name="remotion-cover.log",
    )
    _render_remotion_target(
        remotion_dir,
        render_dir,
        mode="still",
        composition="BriefingCover",
        props_path=props_path,
        output_path=cover_16x9_png,
        log_name="remotion-cover-16x9.log",
    )

    ass_path = write_subtitle_ass(cues, render_dir / "remotion-subtitles.ass", width=width, height=height)
    ffmpeg = ffmpeg_bin()
    post_timeout = int(os.environ.get("BRIEFING_REMOTION_POSTPROCESS_TIMEOUT_SECONDS", "") or DEFAULT_POSTPROCESS_TIMEOUT)
    vf_chain = f"scale=in_range=pc:out_range=tv,format=yuv420p,subtitles={ass_path.name}"
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(visual_mp4.resolve()),
            "-i",
            str(Path(narration_wav).resolve()),
            "-vf",
            vf_chain,
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
            "-movflags",
            "+faststart",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-b:a",
            "160k",
            str(final_mp4.resolve()),
        ],
        cwd=str(render_dir),
        check=True,
        capture_output=True,
        timeout=max(120, post_timeout),
    )

    for item in evidence_contract.get("items") or []:
        if isinstance(item, dict):
            item["rendered"] = True
    evidence_contract["visual_sha256"] = file_sha256(visual_mp4)
    evidence_contract["final_media_sha256"] = file_sha256(final_mp4)
    contract_path = render_dir / "evidence-visual-contract.json"
    write_json_atomic(contract_path, evidence_contract)

    cue_contract_str = "\n".join(f"{float(c['start']):.3f}|{float(c['end']):.3f}|{c['text']}" for c in cues)
    cue_contract_sha = hashlib.sha256(cue_contract_str.encode("utf-8")).hexdigest()
    tts_meta = dict(tts_info or {})
    total_duration = sum(float(s.get("duration") or 0.0) for s in slides)
    seg_count = len({int(s.get("segmentIndex") or 0) for s in slides})

    info: dict[str, Any] = {
        "status": "ok",
        "engine": "remotion",
        "renderer": "remotion",
        "transition_renderer": "@remotion/transitions",
        "subtitle_renderer": "ass-libass",
        "subtitle_burned": "true",
        "subtitle_cue_count": str(len(cues)),
        "subtitle_contract_sha256": cue_contract_sha,
        "tts": str(tts_meta.get("tts_backend_id") or "edge"),
        "tts_backend": str(tts_meta.get("tts_backend") or "edge"),
        "tts_profile_publishable": str(bool(tts_meta.get("publishable", False))).lower(),
        "segments": str(seg_count),
        "slides": str(len(slides)),
        "duration_seconds": f"{total_duration:.2f}",
        "resolution": f"{width}x{height}",
        "video": str(final_mp4),
        "cover": str(cover_png),
        "cover_16x9": str(cover_16x9_png),
        "cover_copy": str(root / "review" / "cover.json"),
        "subtitles": str(Path(subtitles_srt).resolve()),
        "subtitle_ass": str(ass_path),
        "remotion_input": str(props_path),
        "remotion_visual": str(visual_mp4),
        "evidence_visual_contract": str(contract_path),
    }
    if tts_meta.get("profile_id"):
        info["tts_profile_id"] = str(tts_meta["profile_id"])
    if tts_meta.get("voice"):
        info["tts_voice"] = str(tts_meta["voice"])
    write_json_atomic(root / "render-info.json", info)
    return info


def _visual_qa_samples(
    script: list[dict[str, Any]],
    duration: float,
    slides: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if slides:
        for slide in slides:
            seg_idx = int(slide.get("segmentIndex") or 0)
            seg = script[seg_idx] if 0 <= seg_idx < len(script) else {}
            start = max(0.0, float(slide.get("start") or 0.0))
            end = min(duration, float(slide.get("end") or start))
            page = slide.get("page") if isinstance(slide.get("page"), dict) else {}
            ts = min(max(0.05, start + max(0.1, end - start) / 2.0), max(0.05, duration - 0.05))
            rows.append(
                {
                    "segment_index": seg_idx,
                    "kind": str(slide.get("kind") or seg.get("kind") or "news"),
                    "story_id": str(seg.get("story_id") or ""),
                    "page_index": int(slide.get("pageIndex") or 0),
                    "page_kind": str(page.get("kind") or "cards"),
                    "sample_kind": "page_midpoint",
                    "timestamp": round(ts, 3),
                }
            )
    else:
        for seg_idx, seg in enumerate(script):
            start = max(0.0, float(seg.get("start") or 0.0))
            end = min(duration, float(seg.get("end") or start))
            span = max(0.1, end - start)
            pages = [p for p in list(seg.get("visual_pages") or []) if isinstance(p, dict)] or [{}]
            for page_idx, page in enumerate(pages):
                ts = min(max(0.05, start + span * (page_idx + 0.5) / len(pages)), max(0.05, duration - 0.05))
                rows.append(
                    {
                        "segment_index": seg_idx,
                        "kind": str(seg.get("kind") or "news"),
                        "story_id": str(seg.get("story_id") or ""),
                        "page_index": page_idx,
                        "page_kind": str(page.get("kind") or "cards"),
                        "sample_kind": "page_midpoint",
                        "timestamp": round(ts, 3),
                    }
                )

    for seg_idx, seg in enumerate(script):
        if str(seg.get("kind") or "") != "news":
            continue
        start = max(0.0, float(seg.get("start") or 0.0))
        end = min(duration, float(seg.get("end") or start))
        if end - start < 0.8:
            continue
        for label, ts in (("transition_in", start + 0.18), ("transition_out", end - 0.18)):
            rows.append(
                {
                    "segment_index": seg_idx,
                    "kind": "news",
                    "story_id": str(seg.get("story_id") or ""),
                    "page_index": -1,
                    "page_kind": "transition",
                    "sample_kind": label,
                    "timestamp": round(min(max(0.05, ts), max(0.05, duration - 0.05)), 3),
                }
            )

    deduped: list[dict[str, Any]] = []
    seen_ms: set[int] = set()
    for row in rows:
        ms = round(float(row["timestamp"]) * 1000)
        if ms not in seen_ms:
            seen_ms.add(ms)
            deduped.append(row)
    return deduped


def create_visual_qa_package(run_dir: Path, *, slides: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Extract midpoint and transition frames via FFmpeg and tile 4x3 Pillow contact sheets in review/visual-qa/."""
    root = Path(run_dir).resolve()
    video = root / "final.mp4"
    cover = root / "cover.png"
    script_path = root / "script.json"
    subtitles = root / "subtitles.srt"
    audio_quality = root / "audio-quality.json"
    required = [video, cover, script_path, subtitles, audio_quality]
    missing = [str(p) for p in required if not p.is_file() or p.stat().st_size == 0]
    if missing:
        raise ValueError(f"visual QA inputs missing: {missing}")

    ffmpeg = ffmpeg_bin()
    duration = probe_duration(video)
    raw_script = read_json(script_path)
    script = raw_script if isinstance(raw_script, list) else list((raw_script or {}).get("segments") or [])
    if not script:
        raise ValueError("script.json has no rendered segments")

    resolved_slides = list(slides or [])
    if not resolved_slides and (root / "render" / "remotion-input.json").is_file():
        remotion_payload = read_json(root / "render" / "remotion-input.json", default={})
        if isinstance(remotion_payload, dict) and isinstance(remotion_payload.get("slides"), list):
            resolved_slides = [s for s in remotion_payload["slides"] if isinstance(s, dict)]

    samples = _visual_qa_samples(script, duration, resolved_slides)
    qa_dir = ensure_dir(root / "review" / "visual-qa")
    for pat in ("frame-*.png", "contact-sheet-*.png"):
        for old in qa_dir.glob(pat):
            old.unlink(missing_ok=True)

    frames: list[dict[str, Any]] = []
    for idx, sample in enumerate(samples):
        frame_path = qa_dir / f"frame-{idx:03d}.png"
        subprocess.run(
            [ffmpeg, "-y", "-ss", f"{float(sample['timestamp']):.3f}", "-i", str(video), "-frames:v", "1", str(frame_path)],
            check=True,
            capture_output=True,
        )
        frames.append({**sample, "path": str(frame_path), "sha256": file_sha256(frame_path)})

    from PIL import Image, ImageDraw, ImageOps

    contacts: list[dict[str, str]] = []
    for sheet_idx, offset in enumerate(range(0, len(frames), 12), 1):
        batch = frames[offset : offset + 12]
        sheet = Image.new("RGB", (4 * 496 + 8, 3 * 302 + 8), "#EAE7DF")
        draw = ImageDraw.Draw(sheet)
        for cell_idx, frame_row in enumerate(batch):
            with Image.open(frame_row["path"]) as im:
                thumb = ImageOps.contain(im.convert("RGB"), (480, 270))
            x = 8 + (cell_idx % 4) * 496
            y = 8 + (cell_idx // 4) * 302
            sheet.paste(thumb, (x, y))
            draw.text(
                (x, y + 274),
                f"{offset + cell_idx + 1:02d} {frame_row['kind']} {frame_row['sample_kind']}",
                fill="#28312D",
            )
        sheet_path = qa_dir / f"contact-sheet-{sheet_idx:02d}.png"
        sheet.save(sheet_path)
        contacts.append({"path": str(sheet_path), "sha256": file_sha256(sheet_path)})

    qa_payload: dict[str, Any] = {
        "version": 2,
        "video": str(video),
        "video_sha256": file_sha256(video),
        "cover": str(cover),
        "cover_sha256": file_sha256(cover),
        "script": str(script_path),
        "script_sha256": file_sha256(script_path),
        "subtitles": str(subtitles),
        "subtitles_sha256": file_sha256(subtitles),
        "audio_quality": str(audio_quality),
        "audio_quality_sha256": file_sha256(audio_quality),
        "contact_sheet": contacts[0]["path"] if contacts else "",
        "contact_sheets": contacts,
        "duration_seconds": duration,
        "frame_count": len(frames),
        "frames": frames,
    }
    write_json_atomic(qa_dir / "visual-qa-input.json", qa_payload)
    write_json_atomic(
        qa_dir / "visual-agent-audit.template.json",
        {
            "version": 2,
            "agent": "codex",
            "verdict": "pending",
            "video_sha256": qa_payload["video_sha256"],
            "cover_sha256": qa_payload["cover_sha256"],
            "script_sha256": qa_payload["script_sha256"],
            "subtitles_sha256": qa_payload["subtitles_sha256"],
            "audio_quality_sha256": qa_payload["audio_quality_sha256"],
            "contact_sheet_sha256": [c["sha256"] for c in contacts],
            "reviewed_frame_sha256": [],
            "checks": {k: False for k in _VISUAL_QA_CHECKS},
            "notes": "",
        },
    )
    return {"ok": True, **qa_payload}


def render_briefing_video(
    run_dir: Path,
    cards: list[EvidenceCard] | None = None,
    *,
    run_date: str = "",
    quality: str = "1080p",
    tts_backend: str = "edge",
    tts_profile: str = "",
    fallback_duration: float = 8.0,
    ticker_items: list[dict[str, Any]] | None = None,
    edition_brief: dict[str, Any] | None = None,
    visual_qa: bool = False,
) -> dict[str, Any]:
    """End-to-end Remotion render orchestration from manuscript selection through audio analysis & visual QA."""
    root = ensure_dir(Path(run_dir).resolve())
    render_dir = ensure_dir(root / "render")
    effective_date = run_date or root.name
    width, height = _quality_dims(quality)

    draft_segments = (
        build_segments(
            cards,
            run_date=effective_date,
            ticker_items=ticker_items,
            edition_brief=edition_brief,
        )
        if cards is not None or not (root / "news-script.json").is_file()
        else None
    )
    segments, manuscript_meta = select_manuscript(root, draft_segments)
    tts_info = synthesize_narration(
        segments,
        render_dir,
        backend=tts_backend,
        profile=tts_profile,
        fallback_duration=fallback_duration,
    )
    durations = list(tts_info["durations"])
    srt_path = root / "subtitles.srt"
    cues = write_srt(segments, durations, srt_path, tts_srt_paths=tts_info["srt_paths"])

    timed_script: list[dict[str, Any]] = []
    cursor = 0.0
    for seg, dur in zip(segments, durations):
        d = float(dur)
        timed_script.append(
            {
                **seg,
                "start": round(cursor, 3),
                "end": round(cursor + d, 3),
                "duration": round(d, 3),
            }
        )
        cursor += d
    script_path = root / "script.json"
    write_json_atomic(script_path, timed_script)

    slides = build_remotion_slides(
        segments,
        durations,
        width=width,
        height=height,
        run_label=effective_date,
    )
    try:
        info = render_remotion_bundle(
            root,
            slides=slides,
            narration_wav=tts_info["narration_wav"],
            subtitles_srt=srt_path,
            cues=cues,
            width=width,
            height=height,
            fps=30,
            tts_info=tts_info,
        )
    except Exception as first_exc:
        write_text_atomic(render_dir / "remotion-render-error.txt", f"{type(first_exc).__name__}: {first_exc}\n")
        _ensure_remotion_deps(_remotion_dir(), render_dir, force=True)
        info = render_remotion_bundle(
            root,
            slides=slides,
            narration_wav=tts_info["narration_wav"],
            subtitles_srt=srt_path,
            cues=cues,
            width=width,
            height=height,
            fps=30,
            tts_info=tts_info,
        )

    audio_report = analyze_audio(root / "final.mp4", root / "audio-quality.json")
    contract_path = record_render_contract(
        root,
        timed_script,
        manuscript_path=Path(manuscript_meta["manuscript_json"]),
    )

    info.update(
        {
            "script": str(script_path),
            "manuscript": str(manuscript_meta["manuscript_md"]),
            "manuscript_json": str(manuscript_meta["manuscript_json"]),
            "manuscript_mode": str(manuscript_meta["mode"]),
            "audio_quality": str(root / "audio-quality.json"),
            "audio_quality_ok": str(bool(audio_report.get("ok"))).lower(),
            "render_contract": str(contract_path),
        }
    )
    if visual_qa:
        qa_info = create_visual_qa_package(root, slides=slides)
        info["visual_qa"] = str(root / "review" / "visual-qa" / "visual-qa-input.json")
        info["visual_qa_frames"] = str(qa_info.get("frame_count", 0))

    write_json_atomic(root / "render-info.json", info)
    return info

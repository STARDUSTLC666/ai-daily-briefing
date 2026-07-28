from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


LAYER_MANIFEST_VERSION = 1


@dataclass(frozen=True, slots=True)
class RenderLayer:
    id: str
    tool: str
    role: str
    mode: str
    required: bool
    output_dir: str
    note: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tool": self.tool,
            "role": self.role,
            "mode": self.mode,
            "required": self.required,
            "output_dir": self.output_dir,
            "note": self.note,
        }


BASE_LAYER_STACK: list[RenderLayer] = [
    RenderLayer(
        id="remotion_assembly",
        tool="Remotion",
        role="timeline, templates, captions, final composition",
        mode="primary",
        required=True,
        output_dir="render",
        note="Main renderer stays in charge of timing and final video structure.",
    ),
    RenderLayer(
        id="threejs_starfield",
        tool="Three.js",
        role="procedural starfield background and spatial accents",
        mode="inline_or_prebaked",
        required=False,
        output_dir="render/assets/threejs",
        note="Use for reusable brand atmosphere; Remotion can also inline the lightweight version.",
    ),
    RenderLayer(
        id="motion_canvas_explainers",
        tool="Motion Canvas",
        role="charts, timelines, callouts, step-by-step explainers",
        mode="prebaked_asset",
        required=False,
        output_dir="render/assets/motion-canvas",
        note="Use when a news item needs animated explanation instead of static cards.",
    ),
    RenderLayer(
        id="blender_brand_motion",
        tool="Blender",
        role="intro, outro, logo and heavy 3D brand motion",
        mode="prebaked_asset",
        required=False,
        output_dir="render/assets/blender",
        note="Use sparingly for brand pieces; daily news content should not depend on Blender.",
    ),
    RenderLayer(
        id="ffmpeg_post",
        tool="FFmpeg",
        role="mux audio, encode, extract cover and verify media",
        mode="postprocess",
        required=True,
        output_dir="render",
        note="Final assembly and codec checks remain local and scriptable.",
    ),
]


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _slide_asset_slots(slide: dict[str, Any], render_dir: Path, repo_root: Path) -> list[dict[str, Any]]:
    page = slide.get("page") if isinstance(slide.get("page"), dict) else {}
    kind = str(page.get("kind") or slide.get("kind") or "cards").lower()
    index = int(slide.get("index") or 0)
    slots: list[dict[str, Any]] = [
        {
            "id": f"slide-{index:03d}-starfield",
            "layer": "threejs_starfield",
            "purpose": "brand background",
            "optional": True,
            "expected": _rel(render_dir / "assets" / "threejs" / f"slide-{index:03d}-starfield.webm", repo_root),
        }
    ]
    if kind in {"cards", "evidence", "overview"}:
        slots.append(
            {
                "id": f"slide-{index:03d}-explainer",
                "layer": "motion_canvas_explainers",
                "purpose": "news explainer overlay",
                "optional": True,
                "expected": _rel(render_dir / "assets" / "motion-canvas" / f"slide-{index:03d}-explainer.webm", repo_root),
            }
        )
    if str(slide.get("kind") or "").lower() in {"intro", "outro"}:
        slots.append(
            {
                "id": f"slide-{index:03d}-brand-motion",
                "layer": "blender_brand_motion",
                "purpose": "brand intro/outro plate",
                "optional": True,
                "expected": _rel(render_dir / "assets" / "blender" / f"slide-{index:03d}-brand.webm", repo_root),
            }
        )
    return slots


def attach_render_layers(slides: list[dict[str, Any]], render_dir: Path, repo_root: Path | None = None) -> list[dict[str, Any]]:
    repo_root = repo_root or render_dir.parent.parent
    layered: list[dict[str, Any]] = []
    for slide in slides:
        copied = dict(slide)
        copied["assetSlots"] = _slide_asset_slots(copied, render_dir, repo_root)
        layered.append(copied)
    return layered


def write_render_layer_manifest(render_dir: Path, slides: list[dict[str, Any]], quality: str, repo_root: Path | None = None) -> Path:
    repo_root = repo_root or render_dir.parent.parent
    for layer in BASE_LAYER_STACK:
        (render_dir.parent / layer.output_dir).mkdir(parents=True, exist_ok=True)
    asset_root = render_dir / "assets"
    for child in ["threejs", "motion-canvas", "blender"]:
        (asset_root / child).mkdir(parents=True, exist_ok=True)

    layered_slides = attach_render_layers(slides, render_dir, repo_root)
    manifest = {
        "version": LAYER_MANIFEST_VERSION,
        "quality": quality,
        "strategy": "Remotion orchestrates the full video; specialist tools provide optional local assets.",
        "cost_model": "local_free_first",
        "layers": [layer.as_dict() for layer in BASE_LAYER_STACK],
        "slides": [
            {
                "index": slide.get("index"),
                "kind": slide.get("kind"),
                "page_kind": (slide.get("page") or {}).get("kind") if isinstance(slide.get("page"), dict) else None,
                "assetSlots": slide.get("assetSlots") or [],
            }
            for slide in layered_slides
        ],
    }
    path = render_dir / "render-layers.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path

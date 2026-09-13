from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .editor import (
    build_card_design_system,
    build_editorial_cards,
    community_observation_summary,
    focused_news_summary,
    github_release_change_parts,
    official_performance_caution,
    official_performance_summary,
)
from .editorial_plan import EditorialPlan, build_editorial_plan
from .models import EvidenceCard
from .presentation import card_display_entity
from .render_contract import record_rendered_manuscript
from .story_model import StorySpec, build_story_spec
from .storyboard import Storyboard, build_storyboard
from .scoring import card_has_arxiv_evidence, horizon_enrichment, public_source_status
from .social_signals import card_is_community_signal, card_is_official_personnel_signal
from .util import clean_text

CN_TZ = timezone(timedelta(hours=8))

TOP_TABS = ["开场", "模型更新", "开发者工具", "产品体验", "研究观察", "产业信号", "一线消息", "传闻/风向"]
NAV_TABS = [
    ("Intro", "早报总览"),
    ("模型更新", "模型开源"),
    ("开发者工具", "工具链"),
    ("产品体验", "产品现场"),
    ("研究观察", "论文评测"),
    ("产业信号", "行业动态"),
    ("一线消息", "一线消息"),
    ("传闻/风向", "传闻风向"),
    ("Outro", "收束"),
]
TAB_COLORS = {
    "Intro": "4E7C68",
    "开场": "4E7C68",
    "模型更新": "C75B3C",
    "开发者工具": "2F7B7B",
    "产品体验": "6F5E9A",
    "研究观察": "D08A24",
    "产业信号": "3D668C",
    "一线消息": "9A4C55",
    "传闻/风向": "9A4C55",
    "Outro": "4E7C68",
}

DESIGN_COLOR_HEX = {
    "blue": "3D668C",
    "cyan": "2F7B7B",
    "orange": "D08A24",
    "green": "4E7C68",
    "purple": "6F5E9A",
    "gray": "6B7280",
    "yellow": "D99A2B",
}


def _design_accent(color: Any, fallback: str = "4E7C68") -> str:
    raw = clean_text(str(color or "")).lower().strip("#")
    if re.fullmatch(r"[0-9a-f]{6}", raw):
        return raw.upper()
    return DESIGN_COLOR_HEX.get(raw, fallback)


def _subprocess_timeout(name: str, default: float) -> float | None:
    """Per-class subprocess timeout so a wedged child can never hang the nightly run forever."""
    raw = os.environ.get(f"BRIEFING_{name}_TIMEOUT", "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError:
        value = default
    return value if value > 0 else None


def _run(cmd: list[str], cwd: Path | None = None, timeout: float | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def _ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def _ffprobe() -> str | None:
    return shutil.which("ffprobe")


def _repair_remotion_environment(render_dir: Path) -> dict[str, str]:
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    project_dir = Path(__file__).resolve().parent.parent / "remotion"
    log = render_dir / "remotion-auto-repair.log"
    if not npm:
        msg = "npm not found"
        log.write_text(msg, encoding="utf-8-sig")
        return {"attempted": "true", "ok": "false", "error": msg}
    cmd = [npm, "ci"] if (project_dir / "package-lock.json").exists() else [npm, "install"]
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(project_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=900,
            check=False,
        )
        log.write_bytes((completed.stdout or b"") + b"\n--- STDERR ---\n" + (completed.stderr or b""))
        return {"attempted": "true", "ok": str(completed.returncode == 0).lower(), "exit_code": str(completed.returncode), "log": str(log)}
    except Exception as exc:
        log.write_text(f"{type(exc).__name__}: {exc}", encoding="utf-8-sig")
        return {"attempted": "true", "ok": "false", "error": f"{type(exc).__name__}: {exc}", "log": str(log)}


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _srt_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _srt_seconds(value: str) -> float:
    m = re.match(r"(\d+):(\d+):(\d+),(\d+)", value.strip())
    if not m:
        return 0.0
    h, minute, sec, ms = [int(x) for x in m.groups()]
    return h * 3600 + minute * 60 + sec + ms / 1000


def _read_srt_cues(path: Path) -> list[tuple[float, float, str]]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    rows: list[tuple[float, float, str]] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [line.strip("\ufeff") for line in block.splitlines() if line.strip()]
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        start_raw, end_raw = [x.strip() for x in lines[1].split("-->", 1)]
        rows.append((_srt_seconds(start_raw), _srt_seconds(end_raw), clean_text("\n".join(lines[2:]))))
    return rows


def _escape_ass(text: Any) -> str:
    return (
        str(text or "")
        .replace("{", "（")
        .replace("}", "）")
        .replace("\r", "")
        .replace("\n", "\\N")
    )


def _ass_color(hex_rgb: str, alpha: str = "00") -> str:
    raw = hex_rgb.strip().lstrip("#")
    if len(raw) != 6:
        raw = "111827"
    rr, gg, bb = raw[0:2], raw[2:4], raw[4:6]
    return f"&H{alpha}{bb}{gg}{rr}&"


def _shape_event(start: str, end: str, path: str, color: str, alpha: str = "00", layer: int = 0, border: int = 0, border_color: str = "FFFFFF") -> str:
    return _shape_event_fx(start, end, path, color, alpha, layer, border, border_color)


def _shape_event_fx(
    start: str,
    end: str,
    path: str,
    color: str,
    alpha: str = "00",
    layer: int = 0,
    border: int = 0,
    border_color: str = "FFFFFF",
    extra: str = "",
) -> str:
    ass_color = _ass_color(color, alpha)
    border_ass = _ass_color(border_color, "00")
    return (
        f"Dialogue: {layer},{start},{end},Shape,,0,0,0,,"
        f"{{\\an7\\pos(0,0)\\p1\\c{ass_color}\\3c{border_ass}\\bord{border}\\shad0{extra}}}{path}\n"
    )


def _rect_event(start: str, end: str, x: int, y: int, w: int, h: int, color: str, alpha: str = "00", layer: int = 0) -> str:
    return _rect_event_fx(start, end, x, y, w, h, color, alpha, layer)


def _rect_event_fx(start: str, end: str, x: int, y: int, w: int, h: int, color: str, alpha: str = "00", layer: int = 0, extra: str = "") -> str:
    path = f"m {x} {y} l {x+w} {y} l {x+w} {y+h} l {x} {y+h} l {x} {y}"
    return _shape_event_fx(start, end, path, color, alpha, layer, extra=extra)


def _box_event(start: str, end: str, x: int, y: int, w: int, h: int, fill: str, border: str = "D8CEC0", alpha: str = "00", layer: int = 0, line: int = 2) -> str:
    return _box_event_fx(start, end, x, y, w, h, fill, border, alpha, layer, line)


def _box_event_fx(
    start: str,
    end: str,
    x: int,
    y: int,
    w: int,
    h: int,
    fill: str,
    border: str = "D8CEC0",
    alpha: str = "00",
    layer: int = 0,
    line: int = 2,
    extra: str = "",
) -> str:
    line = max(1, line)
    return (
        _rect_event_fx(start, end, x, y, w, h, border, "00", layer, extra)
        + _rect_event_fx(start, end, x + line, y + line, max(1, w - 2 * line), max(1, h - 2 * line), fill, alpha, layer + 1, extra)
    )


def _rounded_rect_event(start: str, end: str, x: int, y: int, w: int, h: int, r: int, color: str, alpha: str = "00", layer: int = 0, border: int = 0, border_color: str = "FFFFFF") -> str:
    # libass renders complex filled Bezier cards inconsistently on some Windows builds.
    # Use clean rectangular cards here; spacing, color, and shadow carry the PPT look.
    if border > 0:
        b = max(1, border)
        return _rect_event(start, end, x, y, w, h, border_color, "00", layer) + _rect_event(
            start, end, x + b, y + b, max(1, w - 2 * b), max(1, h - 2 * b), color, alpha, layer + 1
        )
    return _rect_event(start, end, x, y, w, h, color, alpha, layer)


def _wrap_cjk(text: str, width: int, max_lines: int = 4) -> str:
    text = clean_text(text or "")
    if not text:
        return ""
    lines: list[str] = []
    current = ""
    for ch in text:
        current += ch
        if len(current) >= width and ch not in " /-_":
            lines.append(current.strip())
            current = ""
            if len(lines) >= max_lines:
                break
    if current and len(lines) < max_lines:
        lines.append(current.strip())
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    if len(lines) == max_lines and len("".join(lines)) < len(text):
        lines[-1] = lines[-1].rstrip("，。,. ")
    return "\\N".join(lines)


def _quality_size(quality: str) -> tuple[int, int]:
    return (3840, 2160) if quality == "4k" else (1920, 1080)


def _soft_limit(text: str, limit: int = 58) -> str:
    text = re.sub(r"…+|\.{3,}", "", clean_text(text or ""))
    if len(text) <= limit:
        return text
    window = text[: limit + 1]
    boundary = max(window.rfind(mark) for mark in ["。", "！", "？", "；", ";", "，", ",", "、", " "])
    cut = boundary + 1 if boundary >= max(12, int(limit * 0.6)) else limit
    result = text[:cut]
    if cut < len(text) and result[-1:].isascii() and result[-1:].isalnum() and text[cut : cut + 1].isascii() and text[cut : cut + 1].isalnum():
        result = re.sub(r"[A-Za-z0-9_.+-]+$", "", result)
    return result.rstrip(" ，,.:-_")


def _spoken_limit(text: str, limit: int) -> str:
    """Shorten narration at a clause/word boundary, never with an ellipsis shard."""
    text = _strip_public_markup(text)
    if len(text) <= limit:
        return text.rstrip("。；;，, ")
    window = text[: limit + 1]
    boundary = max(window.rfind(mark) for mark in ["。", "！", "？", "；", ";", "，", ",", "、", " "])
    # A very early boundary loses the useful part, so fall back to a clean
    # character boundary. Either way, never expose source-side "...".
    cut = boundary if boundary >= max(12, int(limit * 0.55)) else limit
    return text[:cut].rstrip(" …。；;，,：:.-_")


def _single_line_title(text: str, limit: int = 34) -> str:
    text = clean_text(text or "").replace("\\N", " ").replace("\n", " ")
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    # Do not leave half an ASCII word such as "Repo" from "Report".
    if limit < len(text) and cut[-1:].isascii() and cut[-1:].isalnum() and text[limit : limit + 1].isascii() and text[limit : limit + 1].isalnum():
        cut = re.sub(r"[A-Za-z0-9_.+-]+$", "", cut)
    return cut.rstrip(" ，。,.:-_") or text[:limit].rstrip(" ，。,.:-_")


def _has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def _looks_raw_english(text: str, min_len: int = 34) -> bool:
    text = clean_text(text or "")
    if len(text) < min_len or _has_cjk(text):
        return False
    letters = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    visible = sum(1 for ch in text if not ch.isspace())
    return visible > 0 and letters / visible > 0.58


def _strip_public_markup(text: str) -> str:
    text = clean_text(text or "")
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]*)\]\(https?://[^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    return clean_text(text).strip(" ，。；;()[]")


def _evidence_is_x(evidence: dict[str, Any]) -> bool:
    try:
        host = urlparse(str(evidence.get("url") or evidence.get("final_url") or "")).netloc.lower().removeprefix("www.")
    except ValueError:
        return False
    return host in {"x.com", "twitter.com", "mobile.twitter.com"}


def _public_evidence_rank(evidence: dict[str, Any]) -> tuple[int, int]:
    reliability = clean_text(str(evidence.get("reliability") or "")).lower()
    tier = clean_text(str(evidence.get("tier") or "")).upper()
    is_x = _evidence_is_x(evidence)
    if is_x and reliability == "official_social":
        priority = 0
    elif is_x and reliability in {"official_personnel", "official_staff"}:
        priority = 1
    elif reliability in {"official", "official_social"} or tier == "A":
        priority = 2
    elif is_x:
        priority = 3
    elif reliability == "media" or tier == "B":
        priority = 4
    else:
        priority = 5
    captured = str(evidence.get("screenshot_status") or "") == "captured" and bool(evidence.get("screenshot_path"))
    return priority, 0 if captured else 1


def _preferred_evidence(card: EvidenceCard) -> dict[str, Any] | None:
    if not card.evidence_links:
        return None
    return min(card.evidence_links, key=_public_evidence_rank)


def _public_evidence_source(evidence: dict[str, Any] | None) -> str:
    evidence = evidence or {}
    url = str(evidence.get("url") or evidence.get("final_url") or "")
    if _evidence_is_x(evidence):
        parts = [part for part in urlparse(url).path.split("/") if part]
        if parts:
            return f"X / @{parts[0]}"
    source = clean_text(str(evidence.get("source") or evidence.get("source_name") or ""))
    source = re.sub(r"(?i)\b(?:rsshub|rss|atom|feed)\b", "", source)
    source = re.sub(r"(?:订阅源|聚合源)", "", source).strip(" /|-：:")
    if source:
        return source
    host = urlparse(url).netloc.lower().removeprefix("www.")
    known = {
        "openai.com": "OpenAI",
        "anthropic.com": "Anthropic",
        "deepmind.google": "Google DeepMind",
        "ai.google": "Google AI",
        "github.com": "GitHub",
        "huggingface.co": "Hugging Face",
        "nvidia.com": "NVIDIA",
        "qwenlm.ai": "Qwen",
        "deepseek.com": "DeepSeek",
        "kimi.com": "Kimi",
    }
    return next((label for domain, label in known.items() if host == domain or host.endswith(f".{domain}")), host or "资料来源")


def _source_name(card: EvidenceCard) -> str:
    return _public_evidence_source(_preferred_evidence(card))


def _published_time(card: EvidenceCard) -> str:
    if card.latest_published_at:
        return card.latest_published_at.astimezone(CN_TZ).strftime("%m-%d %H:%M")
    return "发布时间待核"


def _brand_name(value: str) -> str:
    raw = (value or "").strip()
    lowered = raw.lower()
    mapping = {
        "mistralai": "Mistral AI",
        "mistral": "Mistral AI",
        "deepseek-ai": "DeepSeek",
        "deepseek": "DeepSeek",
        "qwen": "Qwen",
        "openai": "OpenAI",
        "google": "Google",
        "modelscope": "ModelScope",
    }
    return mapping.get(lowered, raw)


def _model_name(title: str) -> str:
    title = clean_text(title or "")
    for prefix in ["Hugging Face 模型仓库更新：", "Hugging Face 模型仓库更新:", "Hugging Face 模型仓库更新", "Hugging Face 模型更新", "GitHub Releases", "GitHub Release"]:
        title = title.replace(prefix, "").strip(" -｜|:")
    if "/" in title and " / " not in title and len(title) < 90:
        return title.split("/", 1)[-1].strip()
    return title


def _localized_raw_english_title(card: EvidenceCard, raw_title: str) -> str:
    raw_title = clean_text(raw_title or "")
    if not _looks_raw_english(raw_title):
        return ""
    blob = clean_text(
        " ".join(
            [
                raw_title,
                card.event_title,
                card.entity,
                _source_name(card),
                _source_url(card),
                " ".join(card.key_facts),
                " ".join(str(e.get("title") or "") for e in card.evidence_links),
            ]
        )
    ).lower()
    # Event-specific localization must run before broad keyword matching. A
    # ChatGPT article can mention NVIDIA customers, but that does not make the
    # story an NVIDIA release.
    if "chatgpt is now a partner for your most ambitious work" in raw_title.lower():
        return "OpenAI 上线 ChatGPT Work：可跨应用执行长任务"
    if "preferred model in microsoft 365 copilot" in raw_title.lower():
        return "OpenAI 将 GPT-5.6 设为 Microsoft 365 Copilot 首选模型"
    if "sol, terra, and luna" in raw_title.lower() and "github copilot" in raw_title.lower():
        return "GPT-5.6 三款模型接入 GitHub Copilot"
    if "ask copilot for a repository overview" in raw_title.lower():
        return "GitHub Copilot 新增仓库概览"
    if "nvidia" in blob:
        if any(k in blob for k in ["federated learning", "nvflare", "flare auto-fl", "auto-fl"]):
            return "NVIDIA FLARE Auto-FL：AI Agent 加速联邦学习研究"
        if "blackwell" in blob and "dflash" in blob:
            return "NVIDIA Blackwell DFlash 推理加速"
        if "inference" in blob:
            return "NVIDIA 推理性能技术更新"
        if "agent" in blob:
            return "NVIDIA AI Agent 技术更新"
        return "NVIDIA 技术博客更新"
    if "federated learning" in blob:
        return "AI Agent 辅助联邦学习研究"
    brand = _brand_name(card.entity or _source_name(card))
    if brand and brand.lower() != "ai":
        return f"{brand} 技术动态"
    return "AI 技术动态"


def _display_title(card: EvidenceCard) -> str:
    raw_title = clean_text(card.event_title or "")
    lowered_raw = raw_title.lower()
    if "claude" in lowered_raw and "bun" in lowered_raw and "重写" in raw_title:
        return "Claude 用 11 天重写 Bun：百万行代码工程实测"
    if "codex" in lowered_raw and "deepseek" in lowered_raw and "mlx" in lowered_raw:
        return "Codex 优化 DeepSeek V4 Flash MLX 的社区反馈"
    if "chatgpt is now a partner for your most ambitious work" in lowered_raw:
        return "OpenAI 上线 ChatGPT Work：可跨应用执行长任务"
    if "gpt-5.6" in lowered_raw and "frontier intelligence" in lowered_raw:
        return "OpenAI 发布 GPT-5.6 系列模型"
    if "preferred model in microsoft 365 copilot" in lowered_raw:
        return "OpenAI 将 GPT-5.6 设为 Microsoft 365 Copilot 首选模型"
    if "sol, terra, and luna" in lowered_raw and "github copilot" in lowered_raw:
        return "GitHub Copilot 接入 GPT-5.6 Sol、Terra 和 Luna"
    if "ask copilot for a repository overview" in lowered_raw:
        return "GitHub Copilot 新增仓库概览"
    title = raw_title
    for prefix in ["Hugging Face 模型仓库更新：", "Hugging Face 模型仓库更新:", "Hugging Face 模型仓库更新", "Hugging Face 模型更新", "GitHub Releases", "GitHub Release"]:
        title = title.replace(prefix, "").strip(" -｜|:")
    if "/" in title and len(title) < 90:
        org, name = title.split("/", 1)
        if "hugging face" in raw_title.lower() or "模型仓库更新" in raw_title:
            return f"{_brand_name(org)} 模型页更新：{name.strip()}"
        return f"{_brand_name(org)} 发布 {name.strip()}"
    news = focused_news_summary(card, title)
    if news["focused"]:
        news_title = clean_text(str(news["title"]))
        if news_title and not _looks_raw_english(news_title):
            return news_title
    localized = _localized_raw_english_title(card, raw_title)
    if localized:
        return localized
    return title or f"{card.entity} 今日动态"


def _first_fact(card: EvidenceCard, fallback: str = "") -> str:
    for fact in card.key_facts:
        cleaned = clean_text(fact)
        if cleaned:
            return cleaned
    return fallback


def _sentence(text: str) -> str:
    text = clean_text(text or "").strip()
    if not text:
        return ""
    return text.rstrip("。！？!?；;，, ") + "。"


def _source_url(card: EvidenceCard) -> str:
    evidence = _preferred_evidence(card)
    return str((evidence or {}).get("url") or "")


def _primary_evidence_visual(card: EvidenceCard) -> dict[str, Any] | None:
    preferred = list(card.evidence_links)
    preferred.sort(
        key=lambda evidence: (
            not (str(evidence.get("screenshot_status") or "") == "captured" and bool(evidence.get("screenshot_path"))),
            _public_evidence_rank(evidence),
            str(evidence.get("screenshot_required") or "").lower() != "true",
        )
    )
    for evidence in preferred:
        source = _soft_limit(_public_evidence_source(evidence), 48)
        planned_title = build_editorial_plan(card).title
        title = _soft_limit(planned_title or _display_title(card) or str(evidence.get("title") or card.event_title), 120)
        url = str(evidence.get("url") or "")
        path = str(evidence.get("screenshot_path") or "")
        status = str(evidence.get("screenshot_status") or "")
        required = str(evidence.get("screenshot_required") or "").lower() == "true"
        if status == "captured" and path and Path(path).exists():
            return {
                "source": source,
                "title": title,
                "url": url,
                "image": path,
                "status": status or "captured",
                "required": required,
                "kind": str(evidence.get("screenshot_kind") or "web_page"),
            }
        raw_images = str(evidence.get("article_images") or "")
        if raw_images:
            try:
                images = json.loads(raw_images)
            except Exception:
                images = []
            if isinstance(images, list):
                for image in images:
                    if not isinstance(image, dict):
                        continue
                    image_path = str(image.get("path") or "")
                    if image_path and Path(image_path).exists():
                        return {
                            "source": source,
                            "title": title,
                            "url": str(image.get("url") or url),
                            "image": image_path,
                            "status": "image",
                            "required": required,
                        }
    return None


def _confirmed_line(card: EvidenceCard) -> str:
    title = _display_title(card)
    source = _source_name(card)
    url = _source_url(card).lower()
    raw = f"{card.event_title} {source} {url}".lower()
    if "huggingface.co" in raw or "hugging face" in raw:
        return f"{title}，证据来自 Hugging Face 模型页更新记录"
    is_github_release = "github releases" in raw or "github release" in raw or "/releases/tag/" in raw
    if is_github_release:
        parts = github_release_change_parts(card)
        if parts.get("compare") or parts.get("changes"):
            compare = clean_text(parts.get("compare", ""))
            compare = re.sub(r"^较\s+", "较上一版 ", compare)
            compare = compare.replace(",GitHub compare 显示", "：").replace("，GitHub compare 显示", "：")
            changes = clean_text(parts.get("changes", ""))
            maintenance = clean_text(parts.get("maintenance", ""))
            point = changes
            if maintenance:
                point = f"{changes}；维护项：{maintenance}" if changes else f"维护项：{maintenance}"
            if compare and point:
                return f"{title} 的发布记录显示，{compare}；公开变化包括：{point}"
            return f"{title} 的发布记录显示，{compare or point}"
        return f"{title} 已出现在官方发布源，具体功能、性能或修复点待确认"
    news = focused_news_summary(card, title)
    if news["focused"]:
        detail = _strip_public_markup(str(news.get("detail") or ""))
        if detail:
            return detail
        return f"{title}，来源是 {source}"
    fact = _strip_public_markup(_first_fact(card, title))
    if "相关事件:" in fact or "模型仓库更新:" in fact:
        return f"{title}，来源是 {source}"
    return fact or f"{title}，来源是 {source}"


def _human_impact(card: EvidenceCard) -> str:
    community = community_observation_summary(card, _display_title(card))
    if community:
        return str(community["impact"])
    performance = official_performance_summary(card)
    if performance:
        return f"{performance}；但要结合测试口径和实际成本看。"
    text = f"{card.entity} {card.event_title} {' '.join(card.key_facts)}".lower()
    if any(k in text for k in ["hugging face", "模型仓库", "模型页", "model page", "open weights", "开源模型"]):
        return "这类更新只说明模型条目活跃，真实影响取决于权重、许可和调用入口。"
    if any(k in text for k in ["api", "sdk", "github", "vllm", "agent", "codex", "开发"]):
        return "功能、速度、价格或限制有没有实际变化。"
    if any(k in text for k in ["benchmark", "评测", "sota", "paper", "arxiv", "研究"]):
        return "对普通观众来说，先把它当成能力信号，不急着等同于真实体验。"
    if any(k in text for k in ["app", "产品", "客户端", "search", "chatgpt", "gemini", "claude"]):
        return "对日常使用的影响，要看入口、地区限制和旧功能是否受影响。"
    if any(k in text for k in ["融资", "投资", "监管", "政策", "行业", "具身智能", "机器人"]):
        return "这类消息更像风向标，短期看变化，长期看它会影响哪些产品。"
    return "它是否带来明确功能、价格或使用门槛变化。"


def _editor_takeaway(card: EvidenceCard) -> str:
    text = f"{card.entity} {card.event_title} {' '.join(card.key_facts)}".lower()
    if any(k in text for k in ["hugging face", "模型仓库", "模型页", "model page", "open weights", "开源模型"]):
        return "关键不是模型名本身，而是它进入了可查发布页；开放方式和真实可用性还要核对。"
    if any(k in text for k in ["api", "sdk", "github", "vllm", "agent", "codex", "开发", "release", "changelog"]):
        return "这类更新要先看有没有明确功能变化；只有版本号不代表体验变了。"
    if any(k in text for k in ["benchmark", "评测", "sota", "paper", "arxiv", "研究"]):
        return "它先算一条能力信号，不等于马上改变体验；还要看复现和真实场景表现。"
    if any(k in text for k in ["app", "产品", "客户端", "search", "chatgpt", "gemini", "claude"]):
        return "这条更接近日常体验更新，但是否真的省事，要看上线范围和使用限制。"
    if any(k in text for k in ["融资", "投资", "监管", "政策", "行业", "具身智能", "机器人"]):
        return "这类消息更多反映行业风向，短期看动作，长期看它会不会影响产品和价格。"
    return "这条可以放进早报观察，但具体影响还需要更多公开信息支撑。"


def _discussion_focus(card: EvidenceCard) -> str:
    community = community_observation_summary(card, _display_title(card))
    if community:
        return str(community["discussion"])
    caution = official_performance_caution(card)
    if caution:
        return caution
    text = f"{card.entity} {card.event_title} {' '.join(card.key_facts)}".lower()
    if any(k in text for k in ["hugging face", "模型仓库", "模型页", "model page", "open weights", "开源模型"]):
        return "判断价值时，最关键的是权重、许可、调用方式和中文场景表现。"
    if any(k in text for k in ["api", "sdk", "github", "vllm", "agent", "codex", "开发", "release", "changelog"]):
        return "实际价值取决于它是否改变功能、速度、价格或使用限制。"
    if any(k in text for k in ["benchmark", "评测", "sota", "paper", "arxiv", "研究"]):
        return "研究类消息要看评测是否站得住、样本是否充分、离真实体验还有多远。"
    if any(k in text for k in ["app", "产品", "客户端", "search", "chatgpt", "gemini", "claude"]):
        return "产品类消息要看入口是否明确、限制多不多、老功能有没有变难用。"
    if any(k in text for k in ["融资", "投资", "监管", "政策", "行业", "具身智能", "机器人"]):
        return "产业类消息要分清短期动作，和真正会影响产品、价格或合规的变化。"
    return "要看公开入口、实际体验和更多来源是否补充细节。"


def _human_caution(card: EvidenceCard) -> str:
    uncertainty = [clean_text(x) for x in card.uncertainty if clean_text(x)]
    if uncertainty:
        text = uncertainty[0].replace("尚未找到官方补证。", "待确认。")
        return f"待确认：{text}"
    if card.risk == "yellow":
        return "先放在观察区，等更多来源确认后再讲细节。"
    return "接下来主要看实际体验、调用成本和用户反馈。"


def _audience_scope(card: EvidenceCard) -> str:
    text = f"{card.entity} {card.event_title} {' '.join(card.key_facts)}".lower()
    if any(k in text for k in ["hugging face", "模型仓库", "模型页", "model page", "open weights", "开源模型"]):
        return "想尝鲜模型、本地部署或关注开源进展的人。"
    if any(k in text for k in ["api", "sdk", "github", "vllm", "agent", "codex", "开发", "release", "changelog"]):
        return "开发者、工具链维护者，以及正在接入 AI 能力的团队。"
    if any(k in text for k in ["benchmark", "评测", "sota", "paper", "arxiv", "研究"]):
        return "关注模型能力边界、论文和评测进展的人。"
    if any(k in text for k in ["app", "产品", "客户端", "search", "chatgpt", "gemini", "claude"]):
        return "日常用 AI 搜索、写作、办公或学习的人。"
    if any(k in text for k in ["融资", "投资", "监管", "政策", "行业", "具身智能", "机器人"]):
        return "关注 AI 公司、产业变化和政策风险的人。"
    return "关注 AI 产品变化和实际可用性的人。"


def _source_hint(card: EvidenceCard) -> str:
    status = public_source_status(card)
    if status == "官方来源":
        return "有官方记录，按可核对更新处理。"
    if status in {"来源待确认", "媒体报道", "来源页待补", "传闻待证实"}:
        return "目前还需要更多来源交叉确认，不把它说成定论。"
    if card.source_count > 1:
        return "已有多个来源提到，但仍不做超出证据的判断。"
    return "来源还偏少，只保留事实本身，不扩展解读。"


def _norm_for_dedupe(text: str) -> str:
    return re.sub(r"[\W_]+", "", clean_text(text or "").lower())


def _strip_fact_label(text: str) -> str:
    text = clean_text(text or "")
    text = re.sub(r"^(预览|观察口径|消息时间|时间来源|相关事件|模型仓库更新|来源摘要|摘要|核心信息|关键看点|发生了什么|影响谁|可以追问|后续看点|为什么重要|已确认内容|可以确认的是|可以确认)\s*[:：]\s*", "", text)
    return text.strip()


def _split_fact_sentences(text: str) -> list[str]:
    text = _strip_fact_label(text)
    if not text:
        return []
    parts = re.split(r"[。！？!?；;\n]+", text)
    result: list[str] = []
    for part in parts:
        part = clean_text(part).strip(" ，,：:")
        if len(part) >= 8:
            result.append(part)
    return result


def _fact_sentence_candidates(card: EvidenceCard) -> list[str]:
    title_norm = _norm_for_dedupe(_display_title(card))
    candidates: list[str] = []
    raw_items = [_confirmed_line(card)]
    raw_items.extend(clean_text(x) for x in card.key_facts if clean_text(x))
    for raw in raw_items:
        if raw.startswith("首要来源发布时间") or raw.startswith("首要来源："):
            continue
        for sentence in _split_fact_sentences(raw):
            norm = _norm_for_dedupe(sentence)
            if not norm:
                continue
            if norm == title_norm:
                continue
            if title_norm and norm in title_norm and len(norm) < len(title_norm) + 6:
                continue
            if any(_norm_for_dedupe(x) == norm for x in candidates):
                continue
            candidates.append(sentence)
    return candidates


def _infer_news_point_title(text: str, used: set[str]) -> str:
    lowered = text.lower()
    rules = [
        ("关键数字", ["134", "38000", "38,000", "小时", "任务", "项", "分", "排名", "fps", "%", "万", "亿", "score", "benchmark"]),
        ("技术机制", ["docker", "容器", "隔离", "框架", "机制", "架构", "api", "sdk", "agent", "训练", "推理", "评估", "双引擎"]),
        ("发布内容", ["发布", "上线", "推出", "更新", "开源", "新增", "宣布", "内测", "开放", "页面"]),
        ("初步结果", ["排名", "得分", "居首", "第一", "次之", "超过", "发现", "规律", "表现", "领先"]),
        ("影响后续", ["用户", "开发者", "本地", "成本", "部署", "产品", "后续", "体验", "调用"]),
    ]
    for title, keys in rules:
        if title not in used and any(k in lowered for k in keys):
            return title
    for title in ["信息要点", "补充信息", "观察重点", "后续线索"]:
        if title not in used:
            return title
    return "信息要点"


def _icon_for_news_point(title: str) -> str:
    return {
        "发布内容": "⚗",
        "关键数字": "▦",
        "技术机制": "◈",
        "初步结果": "↗",
        "影响后续": "◇",
        "信息要点": "·",
        "补充信息": "＋",
        "观察重点": "◎",
        "后续线索": "⏱",
    }.get(title, "·")


def _news_point_cards(card: EvidenceCard) -> list[dict[str, str]]:
    cards: list[dict[str, str]] = []
    used_titles: set[str] = set()
    for sentence in _fact_sentence_candidates(card):
        if len(cards) >= 4:
            break
        title = _infer_news_point_title(sentence, used_titles)
        used_titles.add(title)
        cards.append({"icon": _icon_for_news_point(title), "title": title, "body": _soft_limit(sentence, 86)})
    return cards


def _classify_tab(card: EvidenceCard | None) -> str:
    if not card:
        return "Intro"
    spec = build_story_spec(card)
    text = f"{spec.entity} {card.event_title} {' '.join(card.key_facts)}".lower()
    if card_is_official_personnel_signal(card):
        return "一线消息"
    if card.risk == "yellow" and card.community_count > 0 and card.official_count == 0:
        return "传闻/风向"
    if spec.kind == "industry" or any(k in text for k in ["自动驾驶", "智能驾驶", "离职", "辞职", "卸任", "任命", "高管", "安全团队", "公司重组"]):
        return "产业信号"
    if any(k in text for k in ["hugging face", "模型仓库", "model", "模型"]):
        return "模型更新"
    if any(k in text for k in ["github", "api", "sdk", "codex", "vllm", "agent", "开发", "release", "changelog"]):
        return "开发者工具"
    if any(k in text for k in ["app", "应用", "chatgpt", "claude", "gemini", "产品", "客户端", "search"]):
        return "产品体验"
    if any(k in text for k in ["arxiv", "paper", "benchmark", "sota", "研究", "评测", "技术", "训练"]):
        return "研究观察"
    if any(k in text for k in ["融资", "投资", "监管", "政策", "公司", "行业", "合作"]):
        return "产业信号"
    return "模型更新"


def _card_title_for_tab(tab: str) -> str:
    if tab in {"模型更新", "开发者工具", "产品体验", "研究观察", "产业信号", "一线消息", "传闻/风向"}:
        return tab
    return "今日看点"


def _card_icon_for_tab(tab: str) -> str:
    return {
        "模型更新": "✦",
        "开发者工具": "⚙",
        "产品体验": "▣",
        "研究观察": "↗",
        "产业信号": "◆",
        "一线消息": "◉",
        "传闻/风向": "◌",
        "Intro": "◇",
        "Outro": "✓",
    }.get(tab, "◇")


def _fact_cards(card: EvidenceCard) -> list[dict[str, str]]:
    tab = _classify_tab(card)
    status = public_source_status(card)
    confirmed = _confirmed_line(card)
    return build_editorial_cards(
        card,
        tab_icon=_card_icon_for_tab(tab),
        risk_label=status,
        published_time=_published_time(card),
        display_title=_display_title(card),
        confirmed_line=confirmed,
    )


def _card_design(card: EvidenceCard) -> dict[str, Any]:
    tab = _classify_tab(card)
    status = public_source_status(card)
    confirmed = _confirmed_line(card)
    return build_card_design_system(
        card,
        tab_icon=_card_icon_for_tab(tab),
        risk_label=status,
        published_time=_published_time(card),
        display_title=_display_title(card),
        confirmed_line=confirmed,
    )


def _evidence_cards(card: EvidenceCard, plan: EditorialPlan | None = None) -> list[dict[str, str]]:
    status = public_source_status(card)
    source = _source_name(card)
    facts = (
        [_strip_public_markup(x) for x in plan.facts if _strip_public_markup(x)]
        if plan is not None
        else [_strip_public_markup(x) for x in card.key_facts if _strip_public_markup(x)]
    )
    known_fact = facts[0] if facts else (plan.title if plan is not None else _confirmed_line(card))
    return [
        {"title": "消息来源", "body": _soft_limit(source, 56)},
        {"title": "发布时间", "body": _published_time(card)},
        {"title": "来源说明", "body": _soft_limit(f"{status}，{_source_hint(card)}", 72)},
        {"title": "已知事实", "body": _soft_limit(known_fact, 72)},
        {"title": "关注点", "body": _soft_limit(_human_caution(card), 72)},
    ]


def _context_cards(card: EvidenceCard) -> list[dict[str, str]]:
    enrich = horizon_enrichment(card)
    rows: list[dict[str, str]] = []
    background = clean_text(str(enrich.get("background") or ""))
    community_discussion = clean_text(str(enrich.get("community_discussion") or ""))
    source_note = clean_text(str(enrich.get("source_note") or ""))
    if any(
        fragment in background
        for fragment in [
            "媒体报道适合提示方向",
            "涉及公司内部决策或未公开产品",
            "功能、速度、价格或限制有没有实际变化",
            "官方发布记录能确认事件存在",
            "真正影响还要看功能入口",
        ]
    ):
        background = ""
    if background:
        rows.append({"icon": "◇", "title": "背景解释", "body": _soft_limit(background, 86)})
    if community_discussion:
        rows.append({"icon": "◎", "title": "社区讨论", "body": _soft_limit(community_discussion, 86)})
    if source_note and rows:
        rows.append({"icon": "↗", "title": "消息来源", "body": _soft_limit(source_note, 72)})
    return rows[:3]


PUBLIC_CARD_TITLE_REPLACEMENTS = {
    "编辑判断": "简要结论",
    "我的判断": "简要结论",
    "证据边界": "来源说明",
    "来源口径": "消息来源",
    "来源状态": "来源说明",
    "核验摘要": "新闻摘要",
}


def _audience_copy(text: str) -> str:
    """Remove production-language fragments from viewer-facing copy."""
    result = re.sub(r"…+|\.{3,}", "", clean_text(text or ""))
    replacements = [
        ("目前按单一媒体报道处理", "目前只有一家媒体给出相关信息"),
        ("目前按媒体报道处理", "据公开报道"),
        ("数字和过程仍需等待当事方或第二来源确认", "数字和过程尚未获得当事方确认"),
        ("多家媒体交叉出现", "多家媒体均有报道"),
        ("没有第一方材料的部分仍需保守表述", "当事方尚未公开的细节仍需谨慎理解"),
        ("原帖截图作为来源", "原帖直接提到这一信息"),
        ("适合短讯处理；画面保留原帖截图即可", "这是个人公开表态，不等同于产品公告"),
        ("画面保留原帖截图即可", "原帖可供查阅"),
        ("按媒体线索谨慎表述", "部分消息尚待当事方确认"),
        ("；按已核对来源处理。", "。"),
        (";按已核对来源处理。", "。"),
        ("；按已核对来源处理", ""),
        (";按已核对来源处理", ""),
    ]
    for old, new in replacements:
        result = result.replace(old, new)
    return result


def _audience_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for card in cards:
        row = dict(card)
        title = _audience_copy(str(row.get("title") or ""))
        row["title"] = PUBLIC_CARD_TITLE_REPLACEMENTS.get(title, title)
        if "body" in row:
            row["body"] = _audience_copy(str(row.get("body") or ""))
        if row.get("title") == "测试内容" and str(row.get("body") or "").startswith("首要来源"):
            row["title"] = "消息来源"
            row["body"] = re.sub(r"^首要来源[:：]\s*", "", str(row.get("body") or ""))
        rows.append(row)
    return rows


def _intro_story_title(card: EvidenceCard) -> str:
    display_title = _display_title(card)
    planned_title = build_editorial_plan(card).title
    if planned_title and not _looks_raw_english(planned_title):
        return planned_title
    publisher_suffix = re.search(r"(?:\s[-–—]\s|\b)(?:[a-z0-9-]+\.)+(?:com|cn|org|net)\b", display_title, re.I)
    if _looks_raw_english(display_title) or publisher_suffix:
        if planned_title:
            return planned_title
    return display_title


def _brief_sentence(text: str, limit: int) -> str:
    cleaned = _strip_public_markup(text)
    if "multi-agent" in cleaned.lower() and "公开变化包括" in cleaned:
        compare_match = re.search(r"(较上一版[^；;。]*)", cleaned)
        change_match = re.search(r"公开变化包括[:：]?\s*([^；;。]*multi-agent[^；;。]*)", cleaned, flags=re.I)
        compare = clean_text(compare_match.group(1)) if compare_match else "较上一版有变化"
        change = clean_text(change_match.group(1)) if change_match else "新增 multi-agent 相关变化"
        cleaned = f"{compare}；公开变化包括：{change}"
    priority_terms = [
        "multi-agent",
        "权重、许可、调用方式",
        "权重、许可",
        "AIME",
        "GPQA",
        "LiveCodeBench",
        "SWE-bench",
        "FP8",
        "NVFP4",
    ]
    if len(cleaned) > limit:
        for term in priority_terms:
            if term.lower() not in cleaned.lower():
                continue
            for fragment in re.split(r"[。！？!?；;\n]+", cleaned):
                fragment = clean_text(fragment).strip(" ，,：:")
                if term.lower() in fragment.lower():
                    cleaned = fragment
                    break
            break
    return _spoken_limit(cleaned, limit)


def _confirmation_for_script(card: EvidenceCard, title: str, fact: str) -> str:
    cleaned = clean_text(fact)
    title_norm = _norm_for_dedupe(title)
    if not cleaned or not title_norm:
        return cleaned or _source_hint(card)
    for idx in range(1, len(cleaned) + 1):
        prefix_norm = _norm_for_dedupe(cleaned[:idx])
        if prefix_norm == title_norm:
            rest = clean_text(cleaned[idx:]).strip(" 的，,。:：；;、」』》）)]")
            if len(_norm_for_dedupe(rest)) >= 4:
                return rest
            break
        if len(prefix_norm) > len(title_norm) + 4:
            break
    if _norm_for_dedupe(cleaned) == title_norm:
        return _source_hint(card)
    return cleaned


def _script_subject(card: EvidenceCard) -> str:
    display_entity = card_display_entity(card)
    entity = clean_text(card.entity or "")
    title = clean_text(card.event_title or "")
    source = _source_name(card)
    raw = f"{entity} {title} {source}".lower()
    # A broad cluster fallback must never turn an NVIDIA first-party blog into
    # a "paper" in public narration.
    if display_entity == "NVIDIA" or "nvidia" in raw or "英伟达" in raw:
        return "NVIDIA"
    if "modelscope" in raw or "alibaba" in raw or "\u963f\u91cc" in raw:
        if any(k in raw for k in ["qwen", "\u901a\u4e49", "\u5343\u95ee"]):
            return "\u963f\u91cc\u5343\u95ee"
        return "\u963f\u91cc"
    if "deepseek" in raw:
        return "DeepSeek"
    if "tencent" in raw or "\u817e\u8baf" in raw or "hunyuan" in raw or "\u6df7\u5143" in raw:
        return "\u817e\u8baf\u6df7\u5143"
    if "anthropic" in raw or "claude" in raw:
        return "Anthropic"
    if "openai" in raw or "codex" in raw:
        return "OpenAI"
    if "google" in raw or "gemini" in raw or "gemma" in raw:
        return "Google"
    if "mistral" in raw:
        return "Mistral AI"
    if "/" in entity:
        for part in [p.strip() for p in entity.split("/") if p.strip()]:
            if any(ch >= "\u4e00" and ch <= "\u9fff" for ch in part):
                return _brand_name(part)
        return _brand_name(entity.split("/", 1)[0].strip())
    return _brand_name(display_entity or entity or "AI")


def _script_topic(card: EvidenceCard, title: str) -> str:
    topic = clean_text(title or card.event_title or "AI \u52a8\u6001")
    raw = f"{card.entity} {card.event_title} {_source_name(card)} {topic}".lower()
    version_match = re.search(r"\bv?\d+(?:\.\d+)+(?:[-\w.]*)?", topic)
    version = version_match.group(0) if version_match else ""
    if "modelscope" in raw:
        return f"ModelScope {version}".strip()
    if "codex" in raw and version:
        return f"Codex {version}"
    if "huggingface.co" in raw or "hugging face" in raw or "\u6a21\u578b\u9875" in topic:
        model = _model_name(topic)
        return model or topic
    topic = re.sub(r"\bGitHub\s+Releases?\s*\u53d1\u5e03\s*", "", topic, flags=re.I).strip(" \u3000\uff1a:\uff0c,")
    topic = re.sub(r"\bGitHub\s+Release\s*", "", topic, flags=re.I).strip(" \u3000\uff1a:\uff0c,")
    return topic or clean_text(card.event_title or "AI \u52a8\u6001")


def _strip_subject_from_topic(topic: str, subject: str) -> str:
    cleaned = clean_text(topic)
    subject = clean_text(subject)
    if subject and cleaned.lower().startswith(subject.lower()):
        cleaned = cleaned[len(subject):].strip(" /\uff5c|-:\uff1a\uff0c, ")
    return cleaned or topic


def _is_github_release_card(card: EvidenceCard) -> bool:
    raw = f"{card.event_title} {_source_name(card)} {_source_url(card)}".lower()
    return "github releases" in raw or "github release" in raw or "/releases/tag/" in raw


def _script_action(subject: str, action: str, topic: str) -> str:
    topic = clean_text(topic)
    left_sep = " " if subject and subject[-1].isascii() else ""
    right_sep = " " if topic and topic[0].isascii() else ""
    return f"{subject}{left_sep}{action}{right_sep}{topic}"


def _script_lead_sentence(card: EvidenceCard, title: str) -> str:
    subject = _script_subject(card)
    topic = _script_topic(card, title)
    raw = f"{card.entity} {card.event_title} {_source_name(card)} {_source_url(card)} {title}".lower()
    news_type = _script_news_type(card, title)
    if card_is_official_personnel_signal(card):
        news = focused_news_summary(card, title)
        return str(news.get("lead") or f"X 上出现一条官方人员动态：{_brief_sentence(topic, 44)}")
    if card.risk == "yellow" and card.official_count == 0 and card.media_count > 0:
        source = _source_name(card)
        short = _brief_sentence(title, 50)
        if source and source != "\u8d44\u6599\u6765\u6e90":
            if news_type == "case_study":
                return f"{source}带来一条团队落地案例：{short}"
            if news_type == "industry_news":
                return f"{source}提到一条行业线索：{short}"
            if news_type in {"model_update", "product_update"}:
                return f"{source}报道了一个产品变化：{short}"
            if news_type == "media_report":
                return f"{source}提到：{short}"
            return f"{source}\u79f0\uff0c{short}"
        return short
    if "huggingface.co" in raw or "hugging face" in raw or "\u6a21\u578b\u9875" in title:
        model = _brief_sentence(topic, 42)
        if subject and subject not in model:
            return f"{subject}的 {model} 模型页出现更新"
        return f"{model} 模型页出现更新"
    if _is_github_release_card(card):
        return _script_action(subject, "更新了", _brief_sentence(_strip_subject_from_topic(topic, subject), 42))
    if title.startswith(("GitHub Copilot 接入", "GitHub Copilot 新增")):
        return _brief_sentence(title, 56)
    if any(k in title for k in ["\u53d1\u5e03", "\u4e0a\u7ebf", "\u63a8\u51fa"]):
        if title.startswith(subject):
            return _brief_sentence(title, 56)
        return _script_action(subject, "发布了", _brief_sentence(_strip_subject_from_topic(topic, subject), 44))
    if any(k in title for k in ["\u66f4\u65b0", "\u5347\u7ea7", "\u65b0\u589e", "\u4fee\u590d"]):
        if title.startswith(subject):
            return _brief_sentence(title, 56)
        return _script_action(subject, "更新了", _brief_sentence(_strip_subject_from_topic(topic, subject), 44))
    return f"{subject}\u6709\u4e00\u6761\u65b0\u52a8\u6001\uff1a{_brief_sentence(topic, 44)}"


def _release_compare_from_card(card: EvidenceCard) -> str:
    if not _is_github_release_card(card):
        return ""
    compare = clean_text(str(github_release_change_parts(card).get("compare") or ""))
    if compare:
        compare = re.sub(r"^\u8f83\s+", "\u8f83\u4e0a\u4e00\u7248 ", compare)
        compare = compare.replace("，GitHub compare 显示", "：")
        compare = compare.replace(",GitHub compare 显示", "：")
        compare = compare.replace("GitHub compare 显示 ", "")
        counts = re.search(r"(\d+\s*个提交).*?(\d+\s*个文件变更)", compare)
        if counts:
            return f"较上一版有 {counts.group(1)}、{counts.group(2)}"
    return compare


def _changes_from_confirmation(card: EvidenceCard, confirmation: str) -> str:
    parts = github_release_change_parts(card) if _is_github_release_card(card) else {}
    changes = clean_text(str(parts.get("changes") or ""))
    maintenance = clean_text(str(parts.get("maintenance") or ""))
    if changes and maintenance and _norm_for_dedupe(maintenance) not in _norm_for_dedupe(changes):
        return f"{changes}\uff1b\u7ef4\u62a4\u9879\u5305\u62ec{maintenance}"
    if changes:
        return changes
    marker = "\u516c\u5f00\u53d8\u5316\u5305\u62ec"
    if marker in confirmation:
        tail = confirmation.split(marker, 1)[1].lstrip("\uff1a: \uff0c,")
        tail = re.split(r"(?:\uff1b|\u3002)\s*(?:\u53d8\u66f4\u6765\u6e90|\u6765\u6e90|\u8bc1\u636e)", tail, 1)[0]
        return clean_text(tail)
    return ""


def _script_change_sentence(card: EvidenceCard, confirmation: str, discussion: str) -> str:
    raw = f"{card.entity} {card.event_title} {_source_name(card)} {_source_url(card)}".lower()
    changes = _changes_from_confirmation(card, confirmation)
    if changes:
        compare = _release_compare_from_card(card)
        prefix = f"{_brief_sentence(compare, 34)}\uff0c" if compare else ""
        return f"{prefix}\u8fd9\u6b21\u6539\u52a8\u4e3b\u8981\u5305\u62ec{_brief_sentence(changes, 72)}"
    performance = "" if _is_github_release_card(card) else official_performance_summary(card)
    if performance:
        if "官方性能说明" not in performance:
            return _brief_sentence(f"官方性能说明：{performance}", 74)
        return _brief_sentence(performance, 74)
    if "huggingface.co" in raw or "hugging face" in raw or "\u6a21\u578b\u4ed3\u5e93" in confirmation:
        return "\u8fd9\u6b21\u80fd\u786e\u5b9a\u7684\u662f\u6a21\u578b\u9875\u6709\u66f4\u65b0\uff0c\u6743\u91cd\u3001\u8bb8\u53ef\u548c\u8c03\u7528\u5165\u53e3\u8fd8\u8981\u7ee7\u7eed\u6838\u5bf9"
    if card.risk == "yellow" and card.official_count == 0:
        source = _source_name(card)
        if source and source != "\u8d44\u6599\u6765\u6e90":
            return f"\u8fd9\u6761\u5148\u6309{source}\u7684\u5a92\u4f53\u7ebf\u7d22\u5904\u7406\uff0c\u5b98\u65b9\u53c2\u6570\u3001\u5165\u53e3\u548c\u4ef7\u683c\u8fd8\u5f85\u786e\u8ba4"
        return "\u8fd9\u6761\u5148\u6309\u5f85\u786e\u8ba4\u7ebf\u7d22\u5904\u7406\uff0c\u5b98\u65b9\u53c2\u6570\u3001\u5165\u53e3\u548c\u4ef7\u683c\u8fd8\u5f85\u786e\u8ba4"
    if confirmation:
        return f"\u516c\u5f00\u8bb0\u5f55\u663e\u793a\uff0c{_brief_sentence(confirmation, 68)}"
    return _brief_sentence(discussion, 68)


def _script_impact_sentence(card: EvidenceCard, impact: str) -> str:
    tab = _classify_tab(card)
    prefix = {
        "\u5f00\u53d1\u8005\u5de5\u5177": "\u5bf9\u5f00\u53d1\u8005\u6765\u8bf4",
        "\u6a21\u578b\u66f4\u65b0": "\u5bf9\u7528\u6237\u548c\u5f00\u53d1\u8005\u6765\u8bf4",
        "\u4ea7\u54c1\u4f53\u9a8c": "\u5bf9\u666e\u901a\u7528\u6237\u6765\u8bf4",
        "\u7814\u7a76\u89c2\u5bdf": "\u5b83\u7684\u4ef7\u503c\u5728\u4e8e",
        "\u4ea7\u4e1a\u4fe1\u53f7": "\u884c\u4e1a\u5c42\u9762\u8981\u770b",
        "\u5f85\u786e\u8ba4\u7ebf\u7d22": "\u73b0\u5728\u66f4\u9002\u5408\u5173\u6ce8",
    }.get(tab, "\u91cd\u70b9\u5728\u4e8e")
    return f"{prefix}\uff0c{_brief_sentence(impact, 58)}"


def _script_blob(card: EvidenceCard, title: str = "") -> str:
    return " ".join(
        [
            card.entity,
            card.event_title,
            title,
            _source_name(card),
            _source_url(card),
            " ".join(card.key_facts),
        ]
    ).lower()


SCRIPT_FORBIDDEN_FRAGMENTS = [
    "来源可查",
    "后续继续观察",
    "继续观察",
    "官方补证",
    "后续看实际入口和使用反馈",
    "等官方或更多来源补证",
    "这条先按",
    "待确认线索处理",
    "真正要看",
    "这条主要是",
    "可以确认的是",
    "值得期待",
    "重点关注",
    "持续关注",
    "具体以官方为准",
    "等待更多消息",
    "如有更新第一时间通知",
    "一句话",
    "信息不够",
    "不能直接当成正式新闻",
    "暂时剔除",
    "爬虫",
    "筛选",
    "官网抓取",
    "后面需要继续优化",
    "是否影响",
]


def _script_news_type(card: EvidenceCard, title: str) -> str:
    blob = _script_blob(card, title)
    headline = f"{card.event_title} {title}".lower()
    model_names = [
        "gpt",
        "claude",
        "gemini",
        "qwen",
        "千问",
        "deepseek",
        "hunyuan",
        "混元",
        "kimi",
        "llama",
        "mistral",
        "模型",
    ]
    product_names = ["chatgpt", "claude app", "gemini app", "cursor", "copilot", "perplexity", "notion ai", "客户端", "app"]
    benchmark_terms = ["benchmark", "评测", "榜单", "arena", "swe-bench", "aime", "gpqa", "livecodebench", "跑分"]
    if card_is_official_personnel_signal(card):
        return "official_personnel_signal"
    if _is_github_release_card(card):
        return "github_release"
    if any(k in headline for k in benchmark_terms):
        return "benchmark"
    if card_has_arxiv_evidence(card):
        return "paper"
    if "huggingface.co" in blob or "hugging face" in blob or "模型页" in blob or "模型仓库" in blob:
        if any(k in blob for k in ["open weights", "开源", "权重", "license", "许可"]):
            return "open_model"
        return "model_page"
    if any(k in blob for k in ["招聘", "人才", "岗位", "职业选择", "融资", "收购", "组织调整", "合作"]):
        return "industry_news"
    if any(k in blob for k in ["spotify", "roi", "pr 占比", "生成 pr", "团队落地"]) or ("团队" in blob and "落地" in blob):
        return "case_study"
    has_model = any(k in blob for k in model_names)
    if has_model and any(k in headline for k in ["发布", "上线", "推出", "开放", "正式版"]):
        return "model_release"
    if has_model and any(k in headline for k in ["更新", "升级", "增强", "上下文", "多模态", "语音", "视频", "推理"]):
        return "model_update"
    if any(k in blob for k in product_names):
        return "product_update"
    if any(k in blob for k in ["api", "sdk", "vllm", "ollama", "openwebui", "comfyui", "transformers", "codex", "开发者工具", "工具链"]):
        return "developer_tool"
    if card.media_count > 0:
        return "media_report"
    return "general"


def _script_kind(card: EvidenceCard, title: str) -> str:
    news_type = _script_news_type(card, title)
    if news_type == "official_personnel_signal":
        return "official_personnel_signal"
    if news_type in {"github_release", "developer_tool"}:
        return "developer_release"
    if news_type in {"model_page", "open_model"}:
        return "model_page"
    if news_type == "industry_news":
        return "industry_observation"
    if news_type in {"paper", "benchmark"}:
        return "research"
    if news_type == "case_study":
        return "case_study"
    if news_type in {"model_release", "model_update", "product_update"}:
        return "product_release"
    if card.media_count > 0:
        return "media_report"
    return "general"


def _script_value_stars(card: EvidenceCard, title: str) -> int:
    news_type = _script_news_type(card, title)
    blob = _script_blob(card, title)
    if news_type == "official_personnel_signal":
        return 2
    if news_type == "model_page":
        return 1
    if news_type == "industry_news":
        return 1 if card.official_count == 0 else 2
    if card.community_count > 0 and card.official_count == 0:
        return 2
    if news_type in {"model_release", "product_update"}:
        return 5 if card.official_count > 0 else 4
    if news_type == "model_update":
        return 4 if any(k in blob for k in ["语音", "多模态", "上下文", "推理", "实时"]) else 3
    if news_type == "open_model":
        return 4 if card.official_count > 0 else 3
    if news_type in {"github_release", "developer_tool"}:
        return 3 if card.score < 78 else 4
    if news_type in {"benchmark", "case_study"}:
        return 3
    if news_type == "paper":
        return 2
    if card.official_count > 0 and card.confidence >= 85:
        return 3
    return 2 if card.media_count > 0 else 1


def _script_value_tier(card: EvidenceCard, title: str) -> str:
    stars = _script_value_stars(card, title)
    kind = _script_kind(card, title)
    blob = _script_blob(card, title)
    if kind == "official_personnel_signal":
        return "low"
    if stars >= 4:
        return "high"
    if stars <= 2:
        return "low"
    if kind in {"model_page", "industry_observation"}:
        return "low"
    if "电动版f1" in blob or "gemini在线解说" in blob:
        return "low"
    if "禁用" in blob and ("claude code" in blob or "代码安全" in blob or "数据合规" in blob):
        return "medium"
    if card.community_count > 0 and card.official_count == 0:
        return "low"
    if kind in {"developer_release", "case_study"}:
        return "medium"
    if kind == "product_release":
        return "high"
    return "medium" if card.official_count > 0 else "low"


def _script_source_summary(card: EvidenceCard) -> str:
    topic_blob = clean_text(f"{card.event_title} {card.entity}").lower()
    topic_terms = [
        term for term in ["gpt-5.6", "chatgpt work", "copilot", "codex", "claude", "gemini", "qwen", "deepseek", "nvidia"]
        if term in topic_blob
    ]
    fallback = ""
    for fact in card.key_facts:
        text = clean_text(fact)
        if "来源摘要" in text:
            text = re.sub(r"^来源摘要\s*[:：]\s*", "", text)
            text = clean_text(text)
            if not text or "点击查看原文" in text:
                continue
            fragments = [clean_text(part) for part in re.split(r"[；;。！？!?]+", text) if clean_text(part)]
            # Prefer a complete Chinese fact that is actually about this event,
            # not the unrelated first bullet of a roundup page.
            for fragment in fragments:
                if not re.search(r"[\u4e00-\u9fff]", fragment) or _looks_raw_english(fragment):
                    continue
                if topic_terms and not any(term in fragment.lower() for term in topic_terms):
                    if not fallback:
                        fallback = fragment
                    continue
                return fragment
            if not _looks_raw_english(text) and not fallback:
                fallback = text
    return fallback


def _script_specific_detail(card: EvidenceCard, title: str) -> str:
    blob = _script_blob(card, title)
    summary = _script_source_summary(card)
    if card_is_official_personnel_signal(card):
        news = focused_news_summary(card, title)
        return _brief_sentence(str(news.get("impact") or "这更像产品方向信号，不等于上线公告。"), 56)
    if _is_github_release_card(card):
        changes = _changes_from_confirmation(card, _confirmed_line(card))
        if changes:
            compare = _release_compare_from_card(card)
            prefix = f"{_brief_sentence(compare, 36)}；" if compare else ""
            return f"{prefix}主要改动是{_brief_sentence(changes, 82)}"
        return "这次更像维护更新，重点看 Docker、HubApi 和发布流程相关变化。"
    if "huggingface.co" in blob or "hugging face" in blob or "模型页" in blob or "模型仓库" in blob:
        return "目前只是页面变化，还不能说明模型已经开放下载。"
    if "gpt-5.6" in blob and "frontier intelligence" in blob:
        facts = []
        if "programmatic tool calling" in blob or "程序化工具调用" in blob:
            facts.append("新增程序化工具调用,可用轻量程序协调多步工具")
        if all(name in blob for name in ["sol", "terra", "luna"]):
            facts.append("系列包含 Sol、Terra 和 Luna 三种定位")
        if "零数据保留" in blob or "zdr" in blob:
            facts.append("支持零数据保留")
        return "；".join(facts[:2]) + "。" if facts else "GPT-5.6 系列带来新的工具调用和多档模型选择。"
    if "preferred model in microsoft 365 copilot" in blob:
        return "GPT-5.6 已覆盖 Word、Excel、PowerPoint、Chat 和 Cowork。"
    if "sol, terra, and luna" in blob and "github copilot" in blob:
        return "GitHub Copilot 正在推送 Sol、Terra 和 Luna 三种 GPT-5.6 变体。"
    if "ask copilot for a repository overview" in blob:
        return "用户首次进入陌生仓库时,可以让 Copilot 先生成高层概览。"
    if "chatgpt work" in blob and any(k in blob for k in ["跨应用", "apps and files", "插件连接", "公开测试版", "pro、enterprise"]):
        details = []
        if any(k in blob for k in ["apps and files", "跨应用", "插件连接"]):
            details.append("可通过插件连接应用和文件，并持续执行长任务")
        if any(k in blob for k in ["pro、enterprise", "plus 和 business", "网页端和移动端"]):
            details.append("已先向 Pro、Enterprise 和 Edu 开放")
        if "站点" in blob or "sites" in blob:
            details.append("Sites 功能将以公开测试版推出")
        return "；".join(details[:2]) + "。" if details else "ChatGPT Work 可跨应用和文件执行长任务。"
    if "nvidia" in blob and any(k in blob for k in ["federated learning", "nvflare", "flare auto-fl", "auto-fl"]):
        return "NVIDIA 介绍了 FLARE Auto-FL：用 AI Agent 辅助配置、运行和评估联邦学习实验。"
    if "nvidia" in blob and "blackwell" in blob and "dflash" in blob:
        return "NVIDIA 介绍 DFlash 推测解码，称 Blackwell 推理性能最高可提升到 15 倍。"
    if "fun-asr-realtime" in blob or "实时语音识别" in blob:
        if "百毫秒" in blob:
            return "报道里最具体的信息，是实时语音识别和百毫秒级首字延迟。"
        return "看点在实时语音识别能力，以及稳定入口会不会开放。"
    if "hy3" in blob or "混元" in blob:
        lowered = blob.lower()
        details = []
        if "元宝" in blob and "agent" in lowered:
            details.append("元宝上线 Hy3 Agent 能力")
        if "workbuddy" in lowered or "codebuddy" in lowered:
            details.append("WorkBuddy/CodeBuddy 等业务接入")
        if "tokenhub" in lowered:
            details.append("API 已在腾讯云 TokenHub 上线")
        if details:
            return "具体变化是：" + "；".join(details[:3]) + "。"
        return "现在媒体已经开始报道和体验，真正影响用户的还是开放入口、价格和完整参数。"
    if "claude" in blob and "bun" in blob and "100 万行" in blob:
        details = []
        if "6778" in blob:
            details.append("改动超过 100 万行,包含 6778 次提交")
        if "64 个 claude" in blob:
            details.append("峰值并行运行 64 个 Claude")
        if "517ms" in blob and "464ms" in blob:
            details.append("Linux 启动时间从 517ms 降至 464ms")
        return "；".join(details[:2]) + "。" if details else "这次重写涉及百万行代码和数千次提交。"
    if "spotify" in blob:
        return "重点不是单个工具技巧，而是 AI 生成 PR 占比、部署频率和 ROI 这些工程指标。"
    if "codex" in blob and "token" in blob:
        return "媒体实测结论是能省一点，但幅度有限；这不是官方降价或新功能发布。"
    if "claude" in blob and "禁用" in blob and ("代码安全" in blob or "数据合规" in blob):
        return "媒体说法指向代码安全和数据合规，但暂未看到双方官方确认。"
    if "deepseek" in blob and "mlx" in blob and "codex" in blob:
        return "社区用户称让 Codex 优化 DeepSeek V4 Flash 8-bit MLX，本地推理速度有提升。"
    if "95%" in blob and "claude" in blob:
        return "重点是 Claude 已经被放进内部数据分析流程，而不是一次新模型发布。"
    if "论文" in blob or "icml" in blob or "扩散模型" in blob or "推理纪录" in blob:
        return "看点在扩散模型推理效率，不是新产品发布。"
    if "电动版f1" in blob or "gemini在线解说" in blob:
        return "这更像一次 AI 解说场景展示，说明 Gemini 被放进实时赛事内容里。"
    if "招聘" in blob or "人才" in blob:
        return "它不是产品发布，重点是 AI 岗位需要什么能力。"
    if summary:
        return _brief_sentence(summary, 82)
    return ""


def _script_why_watch(card: EvidenceCard, title: str) -> str:
    kind = _script_kind(card, title)
    blob = _script_blob(card, title)
    if kind == "official_personnel_signal":
        return "这类消息适合先知道方向，但不要按已经上线来规划使用。"
    if "nvidia" in blob and any(k in blob for k in ["federated learning", "nvflare", "flare auto-fl", "auto-fl"]):
        return "这类能力更适合医疗、金融等隐私数据协作训练场景，普通用户了解即可。"
    if "preferred model in microsoft 365 copilot" in blob:
        return "对 Microsoft 365 用户来说,这会直接影响办公场景里的默认模型体验。"
    if "github copilot" in blob and "gpt-5.6" in blob:
        return "对开发者来说,重点是可以按任务在三种模型变体之间选择。"
    if "repository overview" in blob or "仓库概览" in blob:
        return "它主要降低第一次阅读陌生代码库的理解成本。"
    if "claude" in blob and "bun" in blob:
        return "这条的价值不在宣传口号,而在展示多智能体如何进入百万行代码迁移。"
    if "chatgpt work" in blob:
        return "对普通用户和团队来说，关键变化是智能体从回答问题走向跨应用执行工作。"
    if "codex" in blob and "token" in blob:
        return "适合重度使用 Codex 的开发者试一下，普通用户不用专门跟。"
    if kind == "developer_release":
        return "已在用命令行、插件或自动化脚本的开发者，先在测试环境跑一次再升级；普通用户不用管。"
    if kind == "model_page":
        return "目前只是页面变化，还不能说明模型已经开放下载。"
    if "hy3" in blob or "混元" in blob:
        lowered = blob.lower()
        if "tokenhub" in lowered or "元宝" in blob:
            return "这次不只是发布消息，更关键是它已经进入元宝和腾讯云 API 入口，普通用户和开发者都会更容易接触到。"
        return "如果后面开放 API，它可能影响腾讯模型的入口、价格和调用成本。"
    if "fun-asr-realtime" in blob or "实时语音识别" in blob:
        return "语音助手、会议字幕和实时转写团队，会比普通用户更早受影响。"
    if kind == "case_study":
        return "参考点在团队如何把 AI 编程工具放进真实研发流程，而不是单个工具技巧。"
    if "deepseek" in blob and "mlx" in blob and "codex" in blob:
        return "把它当工程反馈看更合适，不能替代官方性能结论。"
    if kind == "research":
        return "如果方案能复现，影响会落到图像或视频生成的推理成本。"
    if kind == "industry_observation":
        return "适合关注 AI 岗位变化的人看，但不要把它当成 DeepSeek 产品更新。"
    if "电动版f1" in blob or "gemini在线解说" in blob:
        return "它的价值在场景尝试，不在模型参数。"
    return ""


def _script_editorial_judgment(card: EvidenceCard, title: str) -> str:
    kind = _script_kind(card, title)
    blob = _script_blob(card, title)
    if kind == "official_personnel_signal":
        return "这是一线消息，不等同于正式产品公告。"
    if "nvidia" in blob and any(k in blob for k in ["federated learning", "nvflare", "flare auto-fl", "auto-fl"]):
        return "这项变化主要影响研究团队和平台开发者。"
    if kind == "developer_release":
        return "这项更新主要影响开发者。"
    if kind == "model_page":
        return "目前只能确认模型页发生变化。"
    if "hy3" in blob or "混元" in blob:
        lowered = blob.lower()
        if "tokenhub" in lowered or "元宝" in blob:
            return "体验价值取决于正式入口、价格和使用限制。"
        return "是否值得体验，主要看入口、价格和使用限制。"
    if "fun-asr-realtime" in blob or "实时语音识别" in blob:
        return "这项更新主要影响语音应用开发者。"
    if kind == "case_study":
        return "这项变化主要影响团队管理者。"
    if "deepseek" in blob and "mlx" in blob and "codex" in blob:
        return "这项变化主要影响本地部署用户。"
    if kind == "research":
        return "这项变化短期主要影响开发者。"
    if kind == "industry_observation":
        return "这是一条行业动态。"
    if "电动版f1" in blob or "gemini在线解说" in blob:
        return "这不是大模型能力升级。"
    if card.official_count > 0 and kind == "product_release":
        return "体验前先确认入口和使用限制。"
    if card.official_count > 0:
        return "这项变化主要影响开发者。"
    if card.media_count > 0:
        return "目前只确认已公开事实，不延伸为确定结论。"
    return ""


def _script_takeaway(card: EvidenceCard, title: str) -> str:
    return _script_editorial_judgment(card, title)


def _script_join(sentences: list[str]) -> str:
    result: list[str] = []
    seen: set[str] = set()
    phrase_counts = {"对用户来说": 0, "值得关注": 0, "今天": 0}
    for sentence in sentences:
        sentence = _strip_public_markup(sentence)
        sentence = sentence.replace("；先当媒体线索看，不写成官方定论", "")
        sentence = sentence.replace("；按已核对来源处理", "")
        sentence = sentence.replace("，官方待确认", "")
        sentence = sentence.replace(";先当媒体线索看,不写成官方定论", "")
        sentence = sentence.replace(";按已核对来源处理", "")
        sentence = sentence.replace(",官方待确认", "")
        sentence = sentence.replace("对用户来说，", "")
        sentence = sentence.replace("对用户来说,", "")
        sentence = sentence.replace("值得关注", "值得看")
        if not sentence or any(fragment in sentence for fragment in SCRIPT_FORBIDDEN_FRAGMENTS):
            continue
        if any(phrase_counts[key] >= 1 and key in sentence for key in phrase_counts):
            continue
        for key in phrase_counts:
            if key in sentence:
                phrase_counts[key] += 1
        key = _norm_for_dedupe(sentence)
        if not key or key in seen:
            continue
        # Skip a generic caution already contained in another sentence instead
        # of reading the same warning twice.
        if any((key in previous or previous in key) and min(len(key), len(previous)) >= 12 for previous in seen):
            continue
        seen.add(key)
        result.append(_sentence(sentence))
    return "".join(result)


def _build_script(card: EvidenceCard) -> str:
    title = _display_title(card)
    kind = _script_kind(card, title)
    tier = _script_value_tier(card, title)
    lead = _script_lead_sentence(card, title)
    if card_is_official_personnel_signal(card):
        news = focused_news_summary(card, title)
        return _script_join(
            [
                str(news.get("lead") or lead),
                str(news.get("impact") or ""),
            ]
        )
    community = community_observation_summary(card, title)
    if community:
        blob = _script_blob(card, title)
        if "deepseek" in blob and "mlx" in blob and "codex" in blob:
            detail = _script_specific_detail(card, title)
            return _script_join(
                [
                    "Reddit 用户让 Codex 优化 DeepSeek V4 Flash 8-bit MLX",
                    detail,
                    _script_why_watch(card, title),
                ]
            )
        return _script_join(
            [
                str(community.get("lead") or lead),
                str(community.get("fact") or ""),
                str(community.get("impact") or ""),
                str(community.get("discussion") or ""),
            ]
        )
    performance = "" if _is_github_release_card(card) else official_performance_summary(card)
    headline_performance = any(
        term in clean_text(card.event_title).lower()
        for term in ["benchmark", "performance", "评测", "榜单", "跑分", "得分", "提速", "性能"]
    )
    if performance and headline_performance:
        performance_line = performance if "官方性能说明" in performance else f"官方性能说明：{performance}"
        return _script_join(
            [
                lead,
                _brief_sentence(performance_line, 72),
                official_performance_caution(card) or "",
            ]
        )
    news = focused_news_summary(card, title)
    if card.community_count > 0 and card.official_count == 0:
        detail = _script_specific_detail(card, title)
        why = _script_why_watch(card, title)
        return _script_join([lead, detail or str(news.get("detail") or news.get("fact") or ""), why])
    if news["focused"] and kind in {"media_report", "general"} and tier != "low":
        stars = _script_value_stars(card, title)
        sentences = [
            str(news.get("lead") or lead),
            str(news.get("detail") or news.get("fact") or ""),
            str(news.get("impact") or ""),
        ]
        if stars >= 4:
            sentences.append(str(news.get("discussion") or ""))
        return _script_join(sentences)
    detail = _script_specific_detail(card, title)
    why = _script_why_watch(card, title)

    if tier == "low":
        return _script_join([lead, detail or why])
    if kind == "product_release":
        return _script_join([lead, detail, why])
    if kind == "developer_release":
        return _script_join([lead, detail or _script_change_sentence(card, _confirmed_line(card), ""), why])
    if kind == "case_study":
        return _script_join([lead, detail, why])
    return _script_join([lead, detail or _script_change_sentence(card, _confirmed_line(card), ""), why])


def _unique_labels(cards: list[EvidenceCard]) -> list[str]:
    labels: list[str] = []
    for idx, card in enumerate(cards, 1):
        entity = _public_entity(card).replace(" / ", "/")
        brand = entity.split("/", 1)[0].strip()
        brand = _brand_name(brand) or "AI"
        label = f"{idx:02d} {brand}"
        n = 2
        while label in labels:
            label = f"{idx:02d} {brand} {n}"
            n += 1
        labels.append(label)
    return labels


def _public_entity(card: EvidenceCard) -> str:
    spec_entity = clean_text(build_story_spec(card).entity)
    display_entity = clean_text(card_display_entity(card))
    # Story semantics may repair a genuinely misclassified discovery entity
    # (for example a clickbait Meta story tagged as generic local/open source).
    # Otherwise keep presentation.py's evidence-based stale-label repair.
    if spec_entity and spec_entity != clean_text(card.entity):
        return spec_entity
    return display_entity or spec_entity or "AI"


def _visible_body(card: EvidenceCard, title: str) -> str:
    parts: list[str] = []
    for raw in [_confirmed_line(card), _script_specific_detail(card, title), _human_impact(card)]:
        text = _strip_public_markup(raw)
        if not text or _looks_raw_english(text):
            continue
        if any(_looks_raw_english(piece) for piece in re.split(r"[。！？!?；;\n]+", text) if piece.strip()):
            continue
        if text not in parts:
            parts.append(_brief_sentence(text, 64))
        if len(parts) >= 2:
            break
    return "。".join(parts) if parts else title


def _signal_disclaimer(card: EvidenceCard) -> tuple[str, str]:
    if card_is_official_personnel_signal(card):
        return "official_personnel", "一线消息，未获官方公告确认。"
    if card_is_community_signal(card):
        return "community_rumour", "传闻/风向，未获官方公告确认。"
    return "", ""


def _signal_title(title: str, signal_kind: str) -> str:
    if signal_kind == "official_personnel" and not title.startswith("一线消息"):
        return f"一线消息｜{title}"
    if signal_kind == "community_rumour" and not title.startswith("传闻/风向"):
        return f"传闻/风向｜{title}"
    return title


def _signal_evidence(card: EvidenceCard) -> list[dict[str, str]]:
    return [
        {
            "source": str(evidence.get("source") or ""),
            "url": str(evidence.get("url") or evidence.get("final_url") or ""),
            "screenshot_required": str(evidence.get("screenshot_required") or ""),
            "screenshot_status": str(evidence.get("screenshot_status") or ""),
            "screenshot_path": str(evidence.get("screenshot_path") or ""),
        }
        for evidence in card.evidence_links[:5]
    ]


def _storyboard_cards(board: Storyboard, *, exclude_intents: set[str] | None = None) -> list[dict[str, str]]:
    icons = {"metric": "▥", "price": "¥", "availability": "↗", "change": "+", "method": "◇", "fact": "▣", "impact": "◎", "caution": "!"}
    excluded = exclude_intents or set()
    return [
        {"icon": icons.get(beat.intent, "▣"), "title": beat.title, "body": _soft_limit(beat.body, 92)}
        for beat in board.beats
        if beat.body and beat.intent not in excluded
    ][:5]


def _source_timeline_card(card: EvidenceCard) -> dict[str, str]:
    """Show concrete provenance instead of a low-information source slogan."""
    source = _source_name(card)
    pieces = [source]
    published = card.latest_published_at
    if published is not None:
        local = published.astimezone(CN_TZ)
        pieces.append(f"{local.month}月{local.day}日 {local:%H:%M} 发布")
    elif card.first_seen_at is not None:
        local = card.first_seen_at.astimezone(CN_TZ)
        pieces.append(f"{local.month}月{local.day}日 {local:%H:%M} 首次发现")
    if card.source_count > 1:
        pieces.append(f"{card.source_count} 个来源交叉")
    else:
        pieces.append(public_source_status(card))
    return {
        "icon": "◇",
        "title": "原文与时间",
        "body": _soft_limit("｜".join(piece for piece in pieces if clean_text(piece)), 86),
    }


def _plan_is_renderable(plan: EditorialPlan, spec: StorySpec) -> bool:
    if not plan.title or not plan.facts:
        return False
    if any(marker in plan.narration() for marker in ["相关事件：", "首要来源发布时间", "来源摘要："]):
        return False
    return any(claim.renderable and claim.verifiable for claim in spec.claims)


def _weekday_phrase(run_date: str) -> str:
    """"今天是7月26日周日" — the daily-ritual anchor the reference series uses."""
    try:
        moment = datetime.strptime((run_date or "")[:10], "%Y-%m-%d")
    except ValueError:
        return ""
    weekday = "一二三四五六日"[moment.weekday()]
    return f"今天是{moment.month}月{moment.day}日周{weekday}"


def _ticker_rows(ticker_cards: list[EvidenceCard]) -> tuple[str, list[dict[str, str]]]:
    """One-liner digest items: entity + de-attributed fact kernel."""
    from .storyboard import strip_card_attribution

    spoken: list[str] = []
    cards_out: list[dict[str, str]] = []
    for card in ticker_cards:
        kernel_source = strip_card_attribution(card.key_facts[0] if card.key_facts else card.event_title)
        entity = _soft_limit(_public_entity(card), 14)
        spoken_kernel = _single_line_title(kernel_source, 24)
        if not spoken_kernel:
            continue
        spoken.append(f"{entity}，{spoken_kernel}。")
        cards_out.append(
            {
                "icon": "•",
                "title": entity,
                "body": _single_line_title(kernel_source, 42),
            }
        )
    return "".join(spoken), cards_out


def _segments(
    cards: list[EvidenceCard],
    run_date: str = "",
    ticker_cards: list[EvidenceCard] | None = None,
) -> list[dict[str, Any]]:
    from .edition_brief import get_edition_brief
    edition_brief = get_edition_brief(cards)
    selected = cards or []
    if not selected:
        return [
            {
                "kind": "intro",
                "title": "AI 日报",
                "caption": "今天的 AI 动态正在整理中",
                "text": "早上好，今天的 AI 动态正在整理中。等信息核对完成后，会用几分钟把重点讲清楚。",
                "active_tab": "Intro",
                "bottom_tabs": ["Intro", "Outro"],
                "bottom_active": "Intro",
                "cards": [
                    {"icon": "◇", "title": "今天看什么", "body": "模型、工具、开源与产业动态，尽量讲成能听懂的重点。"},
                    {"icon": "✓", "title": "怎么判断", "body": "优先看官方来源，单源线索会保守处理。"},
                ],
                "visual_pages": [
                    {
                        "kind": "cards",
                        "title": "AI 日报",
                        "cards": [
                            {"icon": "◇", "title": "今天看什么", "body": "模型、工具、开源与产业动态，尽量讲成能听懂的重点。"},
                            {"icon": "✓", "title": "怎么判断", "body": "优先看官方来源，单源线索会保守处理。"},
                        ],
                    }
                ],
            }
        ]
    labels = _unique_labels(selected)
    bottom_tabs = ["开场"] + labels + ["收尾"]
    rows: list[dict[str, Any]] = []
    intro_cards = [
        {
            "icon": str(index).zfill(2),
            "title": _soft_limit(_public_entity(card), 20),
            "body": _single_line_title(_intro_story_title(card), 58),
        }
        for index, card in enumerate(selected[:6], 1)
    ]
    cover_cards = [
        card
        for card in selected
        if not card_is_official_personnel_signal(card) and not card_is_community_signal(card)
    ]
    if not cover_cards:
        cover_cards = list(selected)
    intro_titles = [_single_line_title(_intro_story_title(card), 22) for card in cover_cards[:3]]
    intro_subjects = [_soft_limit(_public_entity(card), 20) for card in cover_cards[:3]]
    # Retention lives or dies in the first three seconds: open with the
    # strongest verified story instead of a generic greeting.  Only a
    # non-signal story may carry the cold open — an unconfirmed X signal must
    # never be the first spoken sentence, so a signal-only edition keeps the
    # plain greeting.
    hook_card = next(
        (
            card
            for card in selected
            if not card_is_official_personnel_signal(card) and not card_is_community_signal(card)
        ),
        None,
    )
    date_phrase = _weekday_phrase(run_date)
    date_clause = f"{date_phrase}，" if date_phrase else ""
    # BRIEFING_INTRO_STYLE=classic keeps the reference-series greeting-first
    # opening; the default "hook" style trades it for a cold open, which suits
    # a discovery-stage account better than an established daily ritual.
    intro_style = os.environ.get("BRIEFING_INTRO_STYLE", "hook").strip().lower() or "hook"
    if intro_titles and hook_card is not None and intro_style != "classic":
        hook_sentence = _single_line_title(_intro_story_title(hook_card), 42)
        intro_text = f"{hook_sentence}——早上好，{date_clause}这里是AI 日报，今天 {len(selected)} 条，马上开始。"
    elif intro_titles:
        # House voice, not the reference series' lines: the identity here is
        # the evidence-first newsroom — every story ships with its original
        # source on screen.
        intro_text = f"早上好，{date_clause}这里是AI 日报。过去 24 小时的 AI 新闻，今天 {len(selected)} 条，条条有出处，我们开始。"
    else:
        intro_text = f"早上好，{date_clause}这里是AI 日报。过去 24 小时没有够格的新消息，我们不拿旧闻和空话凑数，明天见。"
    rows.append(
        {
            "kind": "intro",
            "title": "AI 日报",
            "caption": "AI 日报",
            "hook": edition_brief["hook"],
            "text": intro_text,
            "active_tab": "Intro",
            "bottom_tabs": bottom_tabs,
            "bottom_active": "开场",
            "cards": intro_cards,
            "visual_pages": [
                {
                    "kind": "overview",
                    "title": "资讯概览",
                    "lead": " · ".join(intro_subjects),
                    "cards": intro_cards,
                }
            ],
        }
    )
    for idx, (card, label) in enumerate(zip(selected, labels), 1):
        story_spec = build_story_spec(card)
        editorial_plan = build_editorial_plan(card)
        storyboard = build_storyboard(card, plan=editorial_plan, spec=story_spec)
        use_structured_plan = _plan_is_renderable(editorial_plan, story_spec)
        tab = _classify_tab(card)
        signal_kind, signal_disclaimer = _signal_disclaimer(card)
        title_source = editorial_plan.title if use_structured_plan else _display_title(card)
        title = _signal_title(_soft_limit(title_source, 58), signal_kind)
        design = _card_design(card)
        fact_cards = (
            _storyboard_cards(
                storyboard,
                exclude_intents={"caution"} if signal_disclaimer else None,
            )
            if use_structured_plan
            else list(design.get("cards") or [])
        )
        if signal_disclaimer:
            fact_cards = [{"icon": "!", "title": "来源说明", "body": signal_disclaimer}] + fact_cards
        source_fact = _source_timeline_card(card)
        if not any(str(item.get("title") or "") == source_fact["title"] for item in fact_cards):
            fact_cards = [*fact_cards[:5], source_fact]
        fact_cards = _audience_cards(fact_cards)
        page_design = {k: design.get(k) for k in ["card_count", "theme", "color", "icon", "editor_rating", "editor_comment", "animation", "news_type"]}
        if use_structured_plan:
            page_design.update({"card_count": len(fact_cards), "theme": storyboard.template, "news_type": story_spec.kind})
        page_design["accent"] = _design_accent(page_design.get("color"), TAB_COLORS.get(tab, "C85F3C"))
        evidence_visual = _primary_evidence_visual(card)
        visual_pages = [
            {
                "kind": "brief" if card.editorial_tier == "brief" else "cards",
                "title": title,
                "lead": _soft_limit(
                    _audience_copy(
                        str(editorial_plan.lead if use_structured_plan else design.get("lead") or _confirmed_line(card))
                    ),
                    92,
                ),
                "source": _soft_limit(_source_name(card), 34),
                "source_url": _source_url(card),
                "evidenceVisual": evidence_visual,
                # Keep every bounded, evidence-backed beat visible.  The
                # The rolling 24h format gains density from concise verified
                # facts, not from padding every story to a fixed card count.
                "cards": fact_cards[:6],
                **page_design,
            }
        ]
        context_cards = [] if card.editorial_tier == "brief" else _audience_cards(_context_cards(card))
        if context_cards:
            visual_pages.append(
                {
                    "kind": "cards",
                    "title": "背景与讨论",
                    "lead": _soft_limit(_human_impact(card), 92),
                    "source": _soft_limit(_source_name(card), 34),
                    "source_url": _source_url(card),
                    "cards": context_cards,
                }
            )
        if evidence_visual:
            evidence_page_title = "报道摘要" if evidence_visual.get("kind") == "source_excerpt_card" else "原文画面"
            visual_pages.append(
                {
                    "kind": "evidence",
                    "title": evidence_page_title,
                    "lead": _soft_limit(
                        editorial_plan.lead if use_structured_plan else _confirmed_line(card),
                        92,
                    ),
                    "source": _soft_limit(_source_name(card), 34),
                    "source_url": _source_url(card),
                    "evidenceVisual": evidence_visual,
                    "background_cards": fact_cards,
                    "cards": _audience_cards(_evidence_cards(card, editorial_plan if use_structured_plan else None)),
                }
            )
        rows.append(
            {
                "kind": "news",
                "position": idx,
                "total": len(selected),
                "title": title,
                "headline": title,
                "risk": public_source_status(card),
                "caption": _soft_limit(title, 44),
                "body": _visible_body(card, title),
                "text": (
                    _script_join([signal_disclaimer, editorial_plan.narration()])
                    if signal_disclaimer and use_structured_plan
                    else editorial_plan.narration()
                    if use_structured_plan
                    else _script_join([signal_disclaimer, _build_script(card)])
                    if signal_disclaimer
                    else _build_script(card)
                ),
                # Full semantic objects live in manifest.json. Public script
                # payloads keep only the generation path so an untranslated
                # source headline or internal warning cannot leak on screen.
                "story_id": story_spec.story_id,
                "claim_ids": list(editorial_plan.claim_ids) if use_structured_plan else [],
                "generation_path": "structured_editorial_plan" if use_structured_plan else "legacy_fallback",
                "editorial_tier": card.editorial_tier or "headline",
                "card_design": page_design,
                "signal_kind": signal_kind,
                "disclaimer": signal_disclaimer,
                "evidence": _signal_evidence(card) if signal_kind else [],
                "active_tab": tab,
                "bottom_tabs": bottom_tabs,
                "bottom_active": label,
                "cards": fact_cards,
                "visual_pages": visual_pages,
            }
        )
    if ticker_cards:
        # Fast-lane digest: verified green official stories that did not make
        # the main portfolio, one sentence each, grid pages reuse the proven
        # "overview" page template. Config-gated (defaults.ticker_enabled).
        ticker_narration, ticker_card_rows = _ticker_rows(ticker_cards)
        if ticker_narration and ticker_card_rows:
            ticker_pages = [
                {
                    "kind": "ticker",
                    "title": "快讯速览",
                    "lead": "今天还有这些更新",
                    "cards": ticker_card_rows[start : start + 10],
                }
                for start in range(0, len(ticker_card_rows), 10)
            ]
            rows.append(
                {
                    "kind": "ticker",
                    "title": "快讯速览",
                    "caption": "快讯速览",
                    "text": f"最后是快讯速览。{ticker_narration}",
                    "active_tab": "速览",
                    "bottom_tabs": bottom_tabs,
                    "bottom_active": "速览",
                    "cards": ticker_card_rows[:6],
                    "visual_pages": ticker_pages,
                }
            )
    rows.append(
        {
            "kind": "outro",
            "title": "播送完毕",
            "caption": "我们明天见",
            # Close with a conversion beat: point at the pinned-comment
            # question and ask for the follow, then keep the sign-off promise.
            "text": "今天的新闻播完了。置顶评论有个问题等你聊，觉得有用就点个关注，我们明天早上见。",
            "active_tab": "Outro",
            "bottom_tabs": bottom_tabs,
            "bottom_active": "收尾",
            "cards": [],
            "visual_pages": [
                {
                    "kind": "closing_continuation",
                    "title": "播送完毕",
                    "cards": [],
                }
            ],
        }
    )
    return rows


def _powershell_tts(render_dir: Path, segments: list[dict[str, Any]]) -> list[Path]:
    ps = shutil.which("powershell")
    if not ps:
        raise RuntimeError("powershell not found")
    payload = []
    wavs: list[Path] = []
    for i, seg in enumerate(segments):
        wav = render_dir / f"seg_{i:03d}.wav"
        wavs.append(wav)
        payload.append({"path": str(wav), "text": str(seg.get("text", ""))})
    json_path = render_dir / "tts-segments.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8-sig")
    script = render_dir / "sapi-tts.ps1"
    script.write_text(
        r'''
param([string]$InputJson)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech
$items = Get-Content -LiteralPath $InputJson -Raw -Encoding UTF8 | ConvertFrom-Json
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
try { $s.SelectVoice("Microsoft Huihui Desktop") } catch {}
$s.Rate = 2
$s.Volume = 100
foreach ($item in $items) {
  $s.SetOutputToWaveFile([string]$item.path)
  [void]$s.Speak([string]$item.text)
  $s.SetOutputToNull()
}
$s.Dispose()
'''.strip()
        + "\n",
        encoding="utf-8-sig",
    )
    _run(
        [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-InputJson", str(json_path)],
        timeout=_subprocess_timeout("SAPI_TTS", 900),
    )
    return wavs


def _edge_tts_command_prefix() -> list[str]:
    """Run edge-tts with the interpreter this pipeline runs in (where edge-tts is pinned).

    Falling back to the global Windows ``py`` launcher only when needed avoids a silent,
    permanent degradation to SAPI when the scheduled-task account resolves ``py -3`` to an
    interpreter without edge-tts installed.
    """
    if sys.executable:
        return [sys.executable, "-m", "edge_tts"]
    py = shutil.which("py")
    if not py:
        raise RuntimeError("no python interpreter available for edge-tts")
    return [py, "-3", "-m", "edge_tts"]


def _edge_tts(render_dir: Path, segments: list[dict[str, Any]]) -> tuple[list[Path], list[Path], str]:
    command_prefix = _edge_tts_command_prefix()
    requested_voice = os.environ.get("BRIEFING_TTS_VOICE", "zh-CN-XiaoxiaoNeural")
    voices = [requested_voice]
    if requested_voice == "zh-CN-XiaoxiaoNeural":
        voices.append("zh-CN-XiaoyiNeural")
    elif requested_voice == "zh-CN-XiaoyiNeural":
        voices.append("zh-CN-XiaoxiaoNeural")
    rate = os.environ.get("BRIEFING_TTS_RATE", "+8%")
    pitch = os.environ.get("BRIEFING_TTS_PITCH", "+0Hz")
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""
    attempts = max(1, min(4, int(os.environ.get("BRIEFING_EDGE_TTS_ATTEMPTS", "2") or 2)))
    failures: list[str] = []
    for voice in voices:
        audios: list[Path] = []
        subtitles: list[Path] = []
        try:
            for i, seg in enumerate(segments):
                text_path = render_dir / f"seg_{i:03d}.txt"
                media_path = render_dir / f"seg_{i:03d}.mp3"
                subtitle_path = render_dir / f"seg_{i:03d}.srt"
                text_path.write_text(str(seg.get("text", "")), encoding="utf-8")
                cmd = [
                    *command_prefix,
                    "--voice",
                    voice,
                    f"--rate={rate}",
                    f"--pitch={pitch}",
                    "--file",
                    str(text_path),
                    "--write-media",
                    str(media_path),
                    "--write-subtitles",
                    str(subtitle_path),
                ]
                if proxy:
                    cmd.extend(["--proxy", proxy])
                last_error: Exception | None = None
                for attempt in range(1, attempts + 1):
                    for path in [media_path, subtitle_path]:
                        try:
                            path.unlink()
                        except FileNotFoundError:
                            pass
                    try:
                        _run(cmd, timeout=_subprocess_timeout("EDGE_TTS", 300))
                        last_error = None
                        break
                    except Exception as exc:
                        last_error = exc
                        if attempt < attempts:
                            time.sleep(0.8 * attempt)
                if last_error is not None:
                    raise last_error
                audios.append(media_path)
                subtitles.append(subtitle_path)
            return audios, subtitles, voice
        except Exception as exc:
            failures.append(f"{voice}: {type(exc).__name__}: {exc}")
            for path in [*audios, *subtitles]:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
    raise RuntimeError("; ".join(failures) or "edge tts failed")


def _indextts2_tts(render_dir: Path, segments: list[dict[str, Any]]) -> tuple[list[Path], str]:
    """Synthesize the complete edition with one local IndexTTS2 model load.

    IndexTTS2 is intentionally opt-in.  When selected, any profile or model
    failure is fatal so an unattended edition can never silently change voice.
    """
    from .indextts2_backend import synthesize_segments

    return synthesize_segments(render_dir, segments)


def _duration(path: Path) -> float:
    # Never guess a duration: a silent 8.0s substitute desynchronizes every subtitle
    # cue, slide window, and Remotion frame while the video still renders "ok".
    # Failing here is caught by the TTS fallback chain / publish gates instead.
    ffprobe = _ffprobe()
    if not ffprobe:
        raise RuntimeError(
            "ffprobe not found: narration timing cannot be measured and subtitles/scenes "
            "would go out of sync; install ffprobe alongside ffmpeg"
        )
    out = subprocess.check_output(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        text=True,
        stderr=subprocess.DEVNULL,
        timeout=_subprocess_timeout("FFPROBE", 60),
    ).strip()
    try:
        return max(0.1, float(out))
    except ValueError:
        raise RuntimeError(f"ffprobe returned an unparsable duration for {path.name}: {out!r}") from None


def _concat_audio(render_dir: Path, wavs: list[Path]) -> Path:
    ffmpeg = _ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found")
    list_file = render_dir / "audio-list.txt"
    lines = []
    for wav in wavs:
        lines.append(f"file '{wav.name}'")
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    raw_narration = render_dir / "narration-raw.wav"
    narration = render_dir / "narration.wav"
    _run(
        [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-ar", "48000", "-ac", "2", str(raw_narration)],
        cwd=render_dir,
        timeout=_subprocess_timeout("FFMPEG", 1800),
    )
    from .audio_quality import normalize_audio

    try:
        normalize_audio(raw_narration, narration, render_dir / "audio-normalization.json")
    except Exception as exc:
        # Never throw away good narration over a post-processing step: fall back to the
        # un-normalized concat output and leave an explicit warning marker for review.
        shutil.copyfile(raw_narration, narration)
        (render_dir / "audio-normalization-warning.txt").write_text(
            f"normalize_audio failed; using un-normalized narration. {type(exc).__name__}: {exc}\n",
            encoding="utf-8",
        )
    return narration


def _silent_audio(render_dir: Path, durations: list[float]) -> Path:
    ffmpeg = _ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found")
    total = max(8.0, sum(durations))
    wav = render_dir / "narration.wav"
    _run(
        [ffmpeg, "-y", "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000", "-t", f"{total:.3f}", str(wav)],
        timeout=_subprocess_timeout("FFMPEG", 1800),
    )
    return wav


def _dialogue(start: str, end: str, style: str, text: str, layer: int = 5, override: str = "") -> str:
    return f"Dialogue: {layer},{start},{end},{style},,0,0,0,,{override}{text}\n"


def _write_ass_legacy(render_dir: Path, segments: list[dict[str, Any]], durations: list[float], quality: str) -> Path:
    width, height = _quality_size(quality)
    scale = width / 1920

    def sz(v: int) -> int:
        return max(1, int(round(v * scale)))

    ass = render_dir / "cards.ass"
    header = f"""[Script Info]
ScriptType: v4.00+
ScaledBorderAndShadow: yes
PlayResX: {width}
PlayResY: {height}

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Shape,Arial,{sz(16)},&H00FFFFFF,&H000000FF,&H00FFFFFF,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: Brand,Microsoft YaHei UI,{sz(34)},&H00445FC8,&H000000FF,&H00FFFFFF,&H00000000,-1,0,0,0,100,100,1,0,1,0,0,7,0,0,0,1
Style: HeaderSmall,Microsoft YaHei UI,{sz(22)},&H00736858,&H000000FF,&H00FFFFFF,&H00000000,0,0,0,0,100,100,1,0,1,0,0,7,0,0,0,1
Style: Chip,Microsoft YaHei UI,{sz(23)},&H00FFFFFF,&H000000FF,&H00FFFFFF,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1
Style: HeroTitle,Microsoft YaHei UI,{sz(54)},&H00445FC8,&H000000FF,&H00FFFFFF,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: SideTitle,Microsoft YaHei UI,{sz(26)},&H00736858,&H000000FF,&H00FFFFFF,&H00000000,-1,0,0,0,100,100,1,0,1,0,0,7,0,0,0,1
Style: SideText,Microsoft YaHei UI,{sz(23)},&H00382D25,&H000000FF,&H00FFFFFF,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: CardIcon,Microsoft YaHei UI,{sz(35)},&H00445FC8,&H000000FF,&H00FFFFFF,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: CardTitle,Microsoft YaHei UI,{sz(30)},&H00445FC8,&H000000FF,&H00FFFFFF,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: CardBody,Microsoft YaHei UI,{sz(27)},&H00272117,&H000000FF,&H00FFFFFF,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: Subtitle,Microsoft YaHei UI,{sz(40)},&H00FFFFFF,&H000000FF,&H00666666,&H00666666,0,0,0,0,100,100,1,0,1,0,0,5,0,0,0,1
Style: Timeline,Microsoft YaHei UI,{sz(22)},&H00453A2E,&H000000FF,&H00FFFFFF,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1
Style: TimelineActive,Microsoft YaHei UI,{sz(22)},&H00445FC8,&H000000FF,&H00FFFFFF,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    events = [header]
    current = 0.0
    header_h = sz(96)
    panel_x, panel_y = sz(70), sz(128)
    panel_w, panel_h = width - sz(140), height - sz(255)
    rail_x, rail_y = panel_x + sz(34), panel_y + sz(38)
    rail_w, rail_h = sz(360), panel_h - sz(76)
    hero_x = rail_x + rail_w + sz(48)
    hero_y = panel_y + sz(58)
    hero_w = panel_x + panel_w - hero_x - sz(34)
    card_w = (hero_w - sz(34)) // 2
    card_h = sz(172)
    card_gap_y = sz(24)
    card_y = hero_y + sz(182)
    bottom_y = height - sz(72)

    for seg, dur in zip(segments, durations):
        start = _ass_time(current)
        end = _ass_time(current + dur)
        title = _escape_ass(_wrap_cjk(str(seg.get("title") or "AI 日报"), 42, 2))
        active_tab = str(seg.get("active_tab") or "Intro")
        bottom_tabs = [str(x) for x in (seg.get("bottom_tabs") or ["Intro", "Outro"])]
        bottom_active = str(seg.get("bottom_active") or active_tab)
        cards = list(seg.get("cards") or [])[:6]
        if not cards:
            cards = [{"icon": "✦", "title": "核心看点", "body": str(seg.get("caption") or seg.get("title") or "AI 日报")}]

        accent = TAB_COLORS.get(active_tab, "C85F3C")
        events.append(_rect_event(start, end, 0, 0, width, height, "F7F3EA", "00", 0))
        events.append(_rect_event(start, end, 0, 0, width, header_h, "FFFDF7", "00", 1))
        events.append(_rect_event(start, end, 0, header_h - sz(2), width, sz(2), "E6DDCF", "00", 2))
        events.append(_dialogue(start, end, "Brand", _escape_ass("AI 日报"), 4, f"{{\\pos({sz(72)},{sz(24)})}}"))
        events.append(_dialogue(start, end, "HeaderSmall", _escape_ass("AI 日报 · 来源优先 · 温和速览"), 4, f"{{\\pos({sz(72)},{sz(62)})}}"))

        active_chip = active_tab
        if active_chip == "Intro":
            active_chip = "今日总览"
        elif active_chip == "Outro":
            active_chip = "收尾"
        chip_specs = [active_chip, "来源提示", "3分钟速读"]
        chip_w, chip_h, chip_gap = sz(154), sz(40), sz(18)
        chip_total_w = chip_w * len(chip_specs) + chip_gap * (len(chip_specs) - 1)
        chip_x = width - sz(72) - chip_total_w
        chip_y = sz(28)
        chip_text_y = chip_y + chip_h // 2
        for ci, chip in enumerate(chip_specs):
            x = chip_x + ci * (chip_w + chip_gap)
            if ci == 0:
                events.append(_rect_event(start, end, x, chip_y, chip_w, chip_h, accent, "00", 2))
                override = f"{{\\pos({x + chip_w // 2},{chip_text_y})}}"
            else:
                override = f"{{\\pos({x + chip_w // 2},{chip_text_y})\\c{_ass_color('6B5547')}}}"
            events.append(_dialogue(start, end, "Chip", _escape_ass(chip), 5, override))

        # Large original information board: left evidence rail + right spotlight cards.
        events.append(_rect_event(start, end, panel_x + sz(8), panel_y + sz(10), panel_w, panel_h, "D9D0C2", "72", 1))
        events.append(_box_event(start, end, panel_x, panel_y, panel_w, panel_h, "FFFDF8", "D7CCBE", "00", 2, sz(2)))
        events.append(_box_event(start, end, rail_x, rail_y, rail_w, rail_h, "F4EEE3", "D0C4B4", "00", 4, sz(2)))
        side_title = active_tab if active_tab != "Intro" else "今日摘要"
        if active_tab == "Outro":
            side_title = "收尾"
        pos = seg.get("position")
        total = seg.get("total")
        if pos and total:
            side_meta = f"{int(pos):02d} / {int(total):02d}"
        elif active_tab == "Intro":
            side_meta = "开场"
        else:
            side_meta = "收尾"
        events.append(_dialogue(start, end, "SideTitle", _escape_ass(side_title), 5, f"{{\\pos({rail_x + sz(30)},{rail_y + sz(38)})}}"))
        events.append(_dialogue(start, end, "HeroTitle", _escape_ass(side_meta), 5, f"{{\\pos({rail_x + sz(30)},{rail_y + sz(108)})\\fs{sz(48)}\\c{_ass_color(accent)}}}"))
        events.append(_dialogue(start, end, "SideText", _escape_ass("证据优先 · 温和速览"), 5, f"{{\\pos({rail_x + sz(32)},{rail_y + sz(174)})}}"))
        events.append(_dialogue(start, end, "SideText", _escape_ass("只保留可确认信息"), 5, f"{{\\pos({rail_x + sz(32)},{rail_y + sz(214)})}}"))

        events.append(_box_event(start, end, hero_x, hero_y - sz(18), hero_w, sz(145), "FFF9EF", "DCD0BF", "00", 3, sz(2)))
        events.append(_rect_event(start, end, hero_x, hero_y + sz(125), hero_w, sz(4), accent, "00", 5))
        events.append(_dialogue(start, end, "HeroTitle", title, 6, f"{{\\pos({hero_x + sz(26)},{hero_y})\\fad(220,120)}}"))

        highlight_cards = cards[:4]
        for ci, raw_card in enumerate(highlight_cards):
            card = raw_card if isinstance(raw_card, dict) else {}
            col = ci % 2
            row = ci // 2
            x = hero_x + col * (card_w + sz(34))
            y = card_y + row * (card_h + card_gap_y)
            icon = _escape_ass(card.get("icon", "◇"))
            card_title = _escape_ass(_soft_limit(str(card.get("title") or "看点"), 16))
            body = _escape_ass(_wrap_cjk(str(card.get("body") or ""), 25, 3))
            card_accent = accent if ci in {0, 3} else ("D99A2B" if ci == 1 else "45636D")
            events.append(_rect_event(start, end, x + sz(6), y + sz(8), card_w, card_h, "D8D0C2", "76", 2))
            events.append(_box_event(start, end, x, y, card_w, card_h, "FFFFFF", "D5CABA", "00", 3, sz(2)))
            events.append(_rect_event(start, end, x, y, sz(7), card_h, card_accent, "00", 5))
            events.append(_rect_event(start, end, x, y + sz(62), card_w, sz(2), "EEE6DA", "00", 5))
            events.append(_dialogue(start, end, "CardIcon", icon, 5, f"{{\\pos({x + sz(30)},{y + sz(28)})\\c{_ass_color(card_accent)}}}"))
            events.append(_dialogue(start, end, "CardTitle", card_title, 5, f"{{\\pos({x + sz(82)},{y + sz(27)})\\c{_ass_color(card_accent)}}}"))
            if body:
                events.append(_dialogue(start, end, "CardBody", body, 5, f"{{\\pos({x + sz(30)},{y + sz(78)})}}"))

        if len(cards) > 4:
            note_src = cards[4]
            note_title = _escape_ass(str(note_src.get("title") or "编辑判断"))
            note_body = _escape_ass(_wrap_cjk(str(note_src.get("body") or ""), 58, 2))
            note_y = card_y + 2 * (card_h + card_gap_y) + sz(8)
            events.append(_box_event(start, end, hero_x, note_y, hero_w, sz(105), "F4EEE3", "D5CABA", "00", 3, sz(2)))
            events.append(_rect_event(start, end, hero_x, note_y, sz(6), sz(105), accent, "00", 4))
            events.append(_dialogue(start, end, "CardTitle", note_title, 5, f"{{\\pos({hero_x + sz(28)},{note_y + sz(20)})\\c{_ass_color(accent)}}}"))
            events.append(_dialogue(start, end, "CardBody", note_body, 5, f"{{\\pos({hero_x + sz(28)},{note_y + sz(60)})}}"))

        if bottom_tabs:
            n = max(1, len(bottom_tabs))
            axis_x = sz(110)
            axis_w = width - sz(220)
            axis_y = bottom_y
            active_idx = bottom_tabs.index(bottom_active) if bottom_active in bottom_tabs else 0
            events.append(_rect_event(start, end, axis_x, axis_y, axis_w, sz(2), "D9D0C2", "00", 2))
            if n > 1 and active_idx > 0:
                progress_w = int(axis_w * active_idx / (n - 1))
                events.append(_rect_event(start, end, axis_x, axis_y, progress_w, sz(3), accent, "00", 3))
            for bi, label in enumerate(bottom_tabs):
                if n == 1:
                    x = axis_x + axis_w // 2
                else:
                    x = axis_x + int(axis_w * bi / (n - 1))
                active = label == bottom_active
                dot = accent if active else "D7CCBE"
                dot_size = sz(16 if active else 11)
                events.append(_rect_event(start, end, x - dot_size // 2, axis_y - dot_size // 2, dot_size, dot_size, dot, "00", 5))
                style = "TimelineActive" if active else "Timeline"
                events.append(_dialogue(start, end, style, _escape_ass(label), 6, f"{{\\pos({x},{axis_y + sz(34)})}}"))

        current += dur
    ass.write_text("".join(events), encoding="utf-8-sig")
    return ass


def _write_ass(render_dir: Path, segments: list[dict[str, Any]], durations: list[float], quality: str) -> Path:
    width, height = _quality_size(quality)
    scale = width / 1920

    def sz(v: int) -> int:
        return max(1, int(round(v * scale)))

    def time_window(start_seconds: float, end_seconds: float) -> tuple[str, str]:
        return _ass_time(start_seconds), _ass_time(end_seconds)

    def fade(ms_in: int = 180, ms_out: int = 100) -> str:
        return f"\\fad({ms_in},{ms_out})"

    def fit_label(text: str, max_chars: int) -> str:
        return _single_line_title(text, max(2, max_chars)).replace(chr(8230), "")

    def text_at(
        events: list[str],
        start_seconds: float,
        end_seconds: float,
        style: str,
        text: str,
        x: int,
        y: int,
        layer: int = 8,
        color: str | None = None,
        extra: str = "",
    ) -> None:
        start, end = time_window(start_seconds, end_seconds)
        tags = f"\\pos({x},{y})"
        if color:
            tags += f"\\c{_ass_color(color)}"
        tags += extra
        events.append(_dialogue(start, end, style, _escape_ass(text), layer, f"{{{tags}}}"))

    def box_at(
        events: list[str],
        start_seconds: float,
        end_seconds: float,
        x: int,
        y: int,
        w: int,
        h: int,
        fill: str,
        border: str,
        layer: int = 3,
        alpha: str = "00",
        extra: str = "",
    ) -> None:
        start, end = time_window(start_seconds, end_seconds)
        events.append(_box_event_fx(start, end, x, y, w, h, fill, border, alpha, layer, sz(2), extra))

    def rect_at(
        events: list[str],
        start_seconds: float,
        end_seconds: float,
        x: int,
        y: int,
        w: int,
        h: int,
        color: str,
        layer: int = 3,
        alpha: str = "00",
        extra: str = "",
    ) -> None:
        start, end = time_window(start_seconds, end_seconds)
        events.append(_rect_event_fx(start, end, x, y, w, h, color, alpha, layer, extra))

    def visual_pages_for(seg: dict[str, Any], duration: float) -> list[dict[str, Any]]:
        pages = [p for p in list(seg.get("visual_pages") or []) if isinstance(p, dict)]
        if not pages:
            pages = [{"kind": "cards", "title": seg.get("title", ""), "cards": list(seg.get("cards") or [])}]
        if duration < 10.5:
            return pages[:1]
        return pages[:3]

    def page_windows(base: float, duration: float, count: int) -> list[tuple[float, float]]:
        count = max(1, count)
        if count == 1:
            return [(base, base + duration)]
        if count == 2:
            split = base + duration * 0.58
            return [(base, split + 0.12), (split - 0.12, base + duration)]
        step = duration / count
        windows: list[tuple[float, float]] = []
        for idx in range(count):
            start = base + idx * step - (0.10 if idx else 0.0)
            end = base + (idx + 1) * step + (0.10 if idx < count - 1 else 0.0)
            windows.append((max(base, start), min(base + duration, end)))
        return windows

    def grid_positions(count: int, card_w: int, card_h: int, gap_x: int, row_gap: int, grid_x: int, grid_y: int) -> list[tuple[int, int]]:
        count = max(1, min(6, count))
        if count == 1:
            return [(grid_x + card_w + gap_x, grid_y + sz(80))]
        if count == 2:
            start_x = grid_x + (card_w + gap_x) // 2
            return [(start_x, grid_y + sz(80)), (start_x + card_w + gap_x, grid_y + sz(80))]
        if count == 4:
            start_x = grid_x + (card_w + gap_x) // 2
            return [(start_x + (i % 2) * (card_w + gap_x), grid_y + (i // 2) * (card_h + row_gap)) for i in range(4)]
        positions: list[tuple[int, int]] = []
        for idx in range(min(count, 3)):
            positions.append((grid_x + idx * (card_w + gap_x), grid_y))
        if count > 3:
            lower_count = count - 3
            lower_w = lower_count * card_w + (lower_count - 1) * gap_x
            lower_x = grid_x + (3 * card_w + 2 * gap_x - lower_w) // 2
            for idx in range(lower_count):
                positions.append((lower_x + idx * (card_w + gap_x), grid_y + card_h + row_gap))
        return positions

    def draw_card(
        events: list[str],
        page_start: float,
        page_end: float,
        card: dict[str, Any],
        x: int,
        y: int,
        w: int,
        h: int,
        accent: str,
        index: int,
        compact: bool = False,
    ) -> None:
        start_seconds = max(page_start, min(page_start + 0.10 * index, page_end - 0.25))
        fx = fade(210, 120)
        card_accent = str(card.get("accent") or accent)
        title = fit_label(str(card.get("title") or "看点"), 12 if compact else 16)
        body_width = 19 if compact else 25
        body_lines = 2 if compact else 3
        body = _wrap_cjk(str(card.get("body") or ""), body_width, body_lines)
        icon = fit_label(str(card.get("icon") or "◇"), 2)
        rect_at(events, start_seconds, page_end, x + sz(7), y + sz(9), w, h, "CBD7D1", 2, "8A", fx)
        box_at(events, start_seconds, page_end, x, y, w, h, "FFFFFF", "D7E0DA", 4, "00", fx)
        rect_at(events, start_seconds, page_end, x + sz(24), y + sz(54), min(w - sz(48), sz(142)), sz(4), card_accent, 6, "00", fx)
        rect_at(events, start_seconds, page_end, x + w - sz(45), y + sz(18), sz(18), sz(18), card_accent, 6, "00", fx)
        text_at(events, start_seconds, page_end, "CardIcon", icon, x + sz(24), y + sz(22), 7, card_accent, fx)
        text_at(events, start_seconds, page_end, "CardTitle", title, x + sz(62), y + sz(22), 7, card_accent, fx)
        if body:
            text_at(events, start_seconds, page_end, "CardBody", body, x + sz(26), y + sz(70), 7, None, fx)

    def draw_feature_card(
        events: list[str],
        page_start: float,
        page_end: float,
        card: dict[str, Any],
        x: int,
        y: int,
        w: int,
        h: int,
        accent: str,
    ) -> None:
        fx = fade(220, 120)
        card_accent = str(card.get("accent") or accent)
        title = fit_label(str(card.get("title") or "核心信号"), 18)
        body = _wrap_cjk(str(card.get("body") or ""), 28, 5)
        icon = fit_label(str(card.get("icon") or "◇"), 2)
        rect_at(events, page_start, page_end, x + sz(9), y + sz(11), w, h, "C7D4CE", 2, "80", fx)
        box_at(events, page_start, page_end, x, y, w, h, "FFFFFF", "D5E0DA", 4, "00", fx)
        rect_at(events, page_start, page_end, x, y, w, sz(6), card_accent, 6, "00", fx)
        rect_at(events, page_start, page_end, x + sz(28), y + h - sz(54), w - sz(56), sz(2), "E0E8E3", 5, "00", fx)
        text_at(events, page_start, page_end, "CardIcon", icon, x + sz(34), y + sz(34), 7, card_accent, f"\\fs{sz(31)}{fx}")
        text_at(events, page_start, page_end, "CardTitle", title, x + sz(86), y + sz(35), 7, card_accent, f"\\fs{sz(31)}{fx}")
        text_at(events, page_start, page_end, "CardBody", body, x + sz(34), y + sz(104), 7, None, f"\\fs{sz(31)}{fx}")
        text_at(events, page_start, page_end, "VisualBody", "来源 / 时间 / 后续", x + sz(34), y + h - sz(33), 7, "65746A", fx)

    def draw_cards_page(events: list[str], page: dict[str, Any], page_start: float, page_end: float, accent: str, grid_x: int, grid_y: int, grid_w: int) -> None:
        cards = [c if isinstance(c, dict) else {} for c in list(page.get("cards") or [])[:6]]
        if not cards:
            cards = [{"icon": "✦", "title": "核心看点", "body": str(page.get("lead") or page.get("title") or "AI 日报")}]
        palette = [accent, "D99A2B", "45636D", "9B3E36", "8A6B43", "6D7F54"]
        cards = [{**card, "accent": card.get("accent") or palette[idx % len(palette)]} for idx, card in enumerate(cards)]
        if len(cards) >= 5:
            gap = sz(30)
            feature_w = int(grid_w * 0.42)
            feature_h = sz(390)
            side_w = grid_w - feature_w - gap
            side_card_w = (side_w - gap) // 2
            side_card_h = sz(174)
            draw_feature_card(events, page_start, page_end, cards[0], grid_x, grid_y, feature_w, feature_h, accent)
            for idx, card in enumerate(cards[1:5]):
                col = idx % 2
                row = idx // 2
                x = grid_x + feature_w + gap + col * (side_card_w + gap)
                y = grid_y + row * (side_card_h + gap)
                draw_card(events, page_start, page_end, card, x, y, side_card_w, side_card_h, accent, idx + 1)
            if len(cards) > 5:
                strip_y = grid_y + feature_h + gap
                strip_h = sz(110)
                draw_card(events, page_start, page_end, cards[5], grid_x, strip_y, grid_w, strip_h, accent, 5, compact=True)
            return

        gap_x = sz(34)
        row_gap = sz(30)
        card_w = (grid_w - 2 * gap_x) // 3
        card_h = sz(166 if len(cards) > 2 else 182)
        positions = grid_positions(len(cards), card_w, card_h, gap_x, row_gap, grid_x, grid_y)
        for idx, (card, (x, y)) in enumerate(zip(cards, positions)):
            draw_card(events, page_start, page_end, card, x, y, card_w, card_h, accent, idx)

    def draw_preview_panel(
        events: list[str],
        page_start: float,
        page_end: float,
        x: int,
        y: int,
        w: int,
        h: int,
        accent: str,
        label: str,
        body: str,
        index: int,
    ) -> None:
        start_seconds = max(page_start, min(page_start + 0.10 * index, page_end - 0.25))
        fx = fade(210, 120)
        rect_at(events, start_seconds, page_end, x + sz(7), y + sz(9), w, h, "D7D1C8", 2, "86", fx)
        box_at(events, start_seconds, page_end, x, y, w, h, "FBFAF5", "DDD4C8", 4, "00", fx)
        rect_at(events, start_seconds, page_end, x, y, w, sz(34), accent, 6, "00", fx)
        text_at(events, start_seconds, page_end, "VisualLabel", fit_label(label, 18), x + sz(22), y + sz(7), 7, "FFFFFF", fx)
        if h > sz(190):
            bar_y = y + sz(72)
            for bar_idx, bar_ratio in enumerate([0.78, 0.58, 0.70, 0.46]):
                rect_at(events, start_seconds, page_end, x + sz(34), bar_y + bar_idx * sz(34), int(w * bar_ratio), sz(9), "E7E0D4", 5, "00", fx)
            text_y = y + h - sz(90)
            lines = 3
        else:
            text_y = y + sz(50)
            lines = 2
        text_at(events, start_seconds, page_end, "VisualBody", _wrap_cjk(body, 28, lines), x + sz(34), text_y, 7, None, fx)

    def draw_evidence_page(events: list[str], page: dict[str, Any], page_start: float, page_end: float, raw_title: str, accent: str, grid_x: int, grid_y: int, grid_w: int) -> None:
        gap = sz(34)
        left_w = int(grid_w * 0.58)
        right_w = grid_w - left_w - gap
        lead = str(page.get("lead") or raw_title)
        source = str(page.get("source") or "资料来源")
        url = str(page.get("source_url") or "")
        visuals = [
            ("素材 01", source),
            ("素材 02", lead),
            ("素材 03", url or "后续可接入截图、图表或产品界面"),
        ]
        main_h = sz(286)
        small_h = sz(132)
        draw_preview_panel(events, page_start, page_end, grid_x, grid_y, left_w, main_h, accent, visuals[0][0], visuals[0][1], 0)
        small_w = (left_w - gap) // 2
        draw_preview_panel(events, page_start, page_end, grid_x, grid_y + main_h + gap, small_w, small_h, "D99A2B", visuals[1][0], visuals[1][1], 1)
        draw_preview_panel(events, page_start, page_end, grid_x + small_w + gap, grid_y + main_h + gap, small_w, small_h, "45636D", visuals[2][0], visuals[2][1], 2)
        cards = [c if isinstance(c, dict) else {} for c in list(page.get("cards") or [])[:4]]
        card_h = sz(132)
        for idx, card in enumerate(cards):
            y = grid_y + idx * (card_h + sz(18))
            draw_card(events, page_start, page_end, card, grid_x + left_w + gap, y, right_w, card_h, accent, idx + 2, compact=True)

    ass = render_dir / "cards.ass"
    header = f"""[Script Info]
ScriptType: v4.00+
ScaledBorderAndShadow: yes
PlayResX: {width}
PlayResY: {height}

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Shape,Arial,{sz(16)},&H00FFFFFF,&H000000FF,&H00FFFFFF,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: NavText,Microsoft YaHei UI,{sz(19)},&H00334338,&H000000FF,&H00FFFFFF,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1
Style: NavActive,Microsoft YaHei UI,{sz(19)},&H00FFFFFF,&H000000FF,&H00FFFFFF,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1
Style: HeroTitle,Microsoft YaHei UI,{sz(50)},&H00445FC8,&H000000FF,&H00FFFFFF,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,8,0,0,0,1
Style: PageKicker,Microsoft YaHei UI,{sz(21)},&H00617162,&H000000FF,&H00FFFFFF,&H00000000,0,0,0,0,100,100,1,0,1,0,0,8,0,0,0,1
Style: CardIcon,Microsoft YaHei UI,{sz(25)},&H00445FC8,&H000000FF,&H00FFFFFF,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: CardTitle,Microsoft YaHei UI,{sz(25)},&H00445FC8,&H000000FF,&H00FFFFFF,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: CardBody,Microsoft YaHei UI,{sz(23)},&H002B261F,&H000000FF,&H00FFFFFF,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: VisualLabel,Microsoft YaHei UI,{sz(20)},&H00FFFFFF,&H000000FF,&H00FFFFFF,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: VisualBody,Microsoft YaHei UI,{sz(21)},&H00423B33,&H000000FF,&H00FFFFFF,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: Subtitle,Microsoft YaHei UI,{sz(34)},&H0039453E,&H000000FF,&H00FFFFFF,&H00000000,0,0,0,0,100,100,1,0,1,0,0,5,0,0,0,1
Style: Timeline,Microsoft YaHei UI,{sz(18)},&H003B463F,&H000000FF,&H00FFFFFF,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1
Style: TimelineActive,Microsoft YaHei UI,{sz(18)},&H00FFFFFF,&H000000FF,&H00FFFFFF,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    events = [header]
    current = 0.0
    nav_x = sz(80)
    nav_y = sz(18)
    nav_w = width - sz(160)
    nav_h = sz(42)
    title_y = sz(104)
    kicker_y = sz(170)
    grid_w = min(width - sz(360), sz(1430))
    grid_x = (width - grid_w) // 2
    grid_y = sz(218)
    subtitle_y = height - sz(160)
    subtitle_h = sz(60)
    timeline_x = sz(88)
    timeline_y = height - sz(62)
    timeline_w = width - sz(176)
    timeline_h = sz(34)

    for seg, duration in zip(segments, durations):
        start_seconds = current
        end_seconds = current + duration
        start, end = time_window(start_seconds, end_seconds)
        raw_title = str(seg.get("title") or "AI 日报")
        active_tab = str(seg.get("active_tab") or "Intro")
        bottom_tabs = [str(x) for x in (seg.get("bottom_tabs") or ["Intro", "Outro"])]
        bottom_active = str(seg.get("bottom_active") or active_tab)
        accent = TAB_COLORS.get(active_tab, "C85F3C")
        caption = _escape_ass(_soft_limit(str(seg.get("caption") or raw_title), 44))

        events.append(_rect_event(start, end, 0, 0, width, height, "F4F7F5", "00", 0))
        events.append(_rect_event(start, end, 0, 0, width, sz(76), "EDF3EF", "00", 1))
        events.append(_rect_event(start, end, 0, sz(76), width, sz(2), "D9E3DD", "00", 1))
        cell_w = nav_w // len(NAV_TABS)
        for idx, (tab_key, label) in enumerate(NAV_TABS):
            x = nav_x + idx * cell_w
            w = cell_w if idx < len(NAV_TABS) - 1 else nav_x + nav_w - x
            active = tab_key == active_tab
            fill = accent if active else "F7FAF8"
            border = accent if active else "D9E3DD"
            events.append(_box_event(start, end, x, nav_y, w, nav_h, fill, border, "00", 2, sz(1)))
            text_at(
                events,
                start_seconds,
                end_seconds,
                "NavActive" if active else "NavText",
                label,
                x + w // 2,
                nav_y + nav_h // 2,
                5,
                None if active else "334338",
            )

        title = _wrap_cjk(raw_title, 36, 2)
        title_fs = sz(50 if len(raw_title) <= 28 else 43)
        text_at(events, start_seconds, end_seconds, "HeroTitle", title, width // 2, title_y, 6, accent, f"\\fs{title_fs}{fade(180,100)}")
        if seg.get("kind") == "news":
            pos = int(seg.get("position") or 0)
            total = int(seg.get("total") or 0)
            kicker = f"{pos:02d} / {total:02d} · {active_tab}" if pos and total else active_tab
        elif active_tab == "Intro":
            kicker = "今日早报"
        else:
            kicker = "收尾"
        text_at(events, start_seconds, end_seconds, "PageKicker", kicker, width // 2, kicker_y, 6, "617162", fade(180,100))

        pages = visual_pages_for(seg, duration)
        for page, (page_start, page_end) in zip(pages, page_windows(start_seconds, duration, len(pages))):
            page_accent = _design_accent(page.get("accent") or page.get("color"), accent)
            if str(page.get("kind") or "cards") == "evidence":
                draw_evidence_page(events, page, page_start, page_end, raw_title, page_accent, grid_x, grid_y, grid_w)
            else:
                draw_cards_page(events, page, page_start, page_end, page_accent, grid_x, grid_y, grid_w)

        rect_at(events, start_seconds, end_seconds, grid_x + sz(8), subtitle_y + sz(7), grid_w - sz(16), subtitle_h, "CCD8D1", 2, "80")
        box_at(events, start_seconds, end_seconds, grid_x, subtitle_y, grid_w, subtitle_h, "EFF5F1", "D6E1DA", 3)
        rect_at(events, start_seconds, end_seconds, grid_x, subtitle_y, sz(7), subtitle_h, accent, 5)
        events.append(_dialogue(start, end, "Subtitle", caption, 6, f"{{\\pos({width // 2},{subtitle_y + subtitle_h // 2})}}"))

        if bottom_tabs:
            n = max(1, len(bottom_tabs))
            active_idx = bottom_tabs.index(bottom_active) if bottom_active in bottom_tabs else 0
            cell = max(1, timeline_w // n)
            label_chars = max(4, int(cell / sz(14)))
            font_size = max(sz(12), sz(18 - max(0, n - 10)))
            for idx, label in enumerate(bottom_tabs):
                x = timeline_x + idx * cell
                w = cell if idx < n - 1 else timeline_x + timeline_w - x
                active = label == bottom_active
                fill = accent if active else "F7FAF8"
                border = accent if active else "D9E3DD"
                events.append(_box_event(start, end, x, timeline_y, w, timeline_h, fill, border, "00", 2, sz(1)))
                color_tag = "" if active else f"\\c{_ass_color('3B463F')}"
                events.append(
                    _dialogue(
                        start,
                        end,
                        "TimelineActive" if active else "Timeline",
                        _escape_ass(fit_label(label, label_chars)),
                        5,
                        f"{{\\pos({x + w // 2},{timeline_y + timeline_h // 2})\\fs{font_size}{color_tag}}}",
                    )
                )
            if n > 1 and active_idx > 0:
                progress_w = int(timeline_w * (active_idx + 1) / n)
                events.append(_rect_event(start, end, timeline_x, timeline_y - sz(6), progress_w, sz(4), accent, "00", 5))

        current += duration
    ass.write_text("".join(events), encoding="utf-8-sig")
    return ass


def _subtitle_chunks(text: str, max_chars: int = 30) -> list[str]:
    text = clean_text(text or "")
    if not text:
        return []
    raw_parts = re.split(r"[。！？!?；;\n]+", text)
    chunks: list[str] = []
    for raw in raw_parts:
        part = clean_text(raw).strip(" ，,：:")
        if not part:
            continue
        while len(part) > max_chars:
            split_at = -1
            for marker in ["，", ",", "、", " "]:
                candidate = part.rfind(marker, 12, max_chars + 1)
                if candidate > split_at:
                    split_at = candidate
            if split_at < 12:
                split_at = max_chars
            head = part[:split_at].strip(" ，,：:")
            if head:
                chunks.append(head)
            part = part[split_at:].strip(" ，,：:")
        if part:
            chunks.append(part)
    return chunks


def _subtitle_cues_for_segment(seg: dict[str, Any], start: float, duration: float) -> list[tuple[float, float, str]]:
    chunks = _subtitle_chunks(str(seg.get("text") or seg.get("caption") or seg.get("title") or ""))
    if not chunks:
        return []
    weights = [max(8, len(chunk)) for chunk in chunks]
    total = max(1, sum(weights))
    cues: list[tuple[float, float, str]] = []
    cursor = start
    end_limit = start + max(0.1, duration)
    for idx, (chunk, weight) in enumerate(zip(chunks, weights)):
        if idx == len(chunks) - 1:
            cue_end = end_limit
        else:
            cue_end = start + duration * (sum(weights[: idx + 1]) / total)
        cue_start = max(start, cursor)
        cue_end = max(cue_start + 0.35, min(end_limit, cue_end))
        cues.append((cue_start, cue_end, chunk))
        cursor = cue_end
    return cues


def _split_subtitle_cue(start: float, end: float, text: str, max_chars: int = 34) -> list[tuple[float, float, str]]:
    chunks = _subtitle_chunks(text, max_chars=max_chars)
    if not chunks:
        return []
    if len(chunks) == 1:
        return [(start, end, chunks[0])]
    duration = max(0.1, end - start)
    weights = [max(4, len(chunk)) for chunk in chunks]
    total = max(1, sum(weights))
    rows: list[tuple[float, float, str]] = []
    cursor = start
    for idx, (chunk, weight) in enumerate(zip(chunks, weights)):
        if cursor >= end:
            break
        cue_end = end if idx == len(chunks) - 1 else start + duration * (sum(weights[: idx + 1]) / total)
        cue_end = min(end, max(cursor + 0.25, min(end, cue_end)))
        if cue_end > cursor:
            rows.append((cursor, cue_end, chunk))
            cursor = cue_end
    return rows


def _subtitle_cues_from_tts_srt(subtitle_path: Path, segment_start: float, duration: float) -> list[tuple[float, float, str]]:
    local_cues = _read_srt_cues(subtitle_path)
    if not local_cues:
        return []
    end_limit = segment_start + max(0.1, duration)
    rows: list[tuple[float, float, str]] = []
    cursor = segment_start
    for local_start, local_end, text in local_cues:
        text = clean_text(text)
        if not text:
            continue
        cue_start = max(segment_start, segment_start + local_start, cursor)
        cue_end = min(end_limit, segment_start + local_end)
        if cue_end <= cue_start + 0.12:
            continue
        for split_start, split_end, split_text in _split_subtitle_cue(cue_start, cue_end, text):
            if split_end <= split_start + 0.12:
                continue
            rows.append((split_start, split_end, split_text))
            cursor = split_end
    return rows


def _write_srt(out_dir: Path, segments: list[dict[str, Any]], durations: list[float], subtitle_paths: list[Path] | None = None) -> Path:
    srt = out_dir / "subtitles.srt"
    lines: list[str] = []
    current = 0.0
    cue_index = 1
    for idx, (seg, dur) in enumerate(zip(segments, durations)):
        tts_cues: list[tuple[float, float, str]] = []
        if subtitle_paths and idx < len(subtitle_paths):
            tts_cues = _subtitle_cues_from_tts_srt(subtitle_paths[idx], current, dur)
        cues = tts_cues or _subtitle_cues_for_segment(seg, current, dur)
        for start, end, caption in cues:
            lines.append(str(cue_index))
            lines.append(f"{_srt_time(start)} --> {_srt_time(end)}")
            lines.append(caption)
            lines.append("")
            cue_index += 1
        current += dur
    srt.write_text("\n".join(lines), encoding="utf-8-sig")
    return srt


def _write_script_json(out_dir: Path, segments: list[dict[str, Any]], durations: list[float]) -> Path:
    path = out_dir / "script.json"
    current = 0.0
    payload = []
    for seg, dur in zip(segments, durations):
        payload.append(
            {
                **seg,
                "start": round(current, 3),
                "end": round(current + dur, 3),
                "duration": round(dur, 3),
            }
        )
        current += dur
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8-sig")
    return path


def _plain_manuscript_cards(cards: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        title = clean_text(str(card.get("title") or ""))
        body = clean_text(str(card.get("body") or ""))
        if title or body:
            rows.append({"title": title, "body": body})
    return rows


def _write_news_manuscript(out_dir: Path, segments: list[dict[str, Any]]) -> tuple[Path, Path]:
    json_path = out_dir / "news-script.json"
    md_path = out_dir / "news-script.md"
    payload_segments: list[dict[str, Any]] = []
    md_lines = ["# AI 日报新闻稿", "", "这份稿件是视频、配音和字幕的共同来源。", ""]
    for idx, seg in enumerate(segments):
        kind = str(seg.get("kind") or "news")
        title = clean_text(str(seg.get("title") or ""))
        caption = clean_text(str(seg.get("caption") or title))
        text = clean_text(str(seg.get("text") or ""))
        cards = _plain_manuscript_cards(list(seg.get("cards") or []))
        payload_segments.append(
            {
                "index": idx,
                "kind": kind,
                "position": seg.get("position"),
                "total": seg.get("total"),
                "story_id": clean_text(str(seg.get("story_id") or "")),
                "claim_ids": [clean_text(str(x)) for x in list(seg.get("claim_ids") or []) if clean_text(str(x))],
                "generation_path": clean_text(str(seg.get("generation_path") or "")),
                "editorial_tier": clean_text(str(seg.get("editorial_tier") or "headline")),
                "title": title,
                "headline": clean_text(str(seg.get("headline") or "")),
                "caption": caption,
                "text": text,
                "active_tab": clean_text(str(seg.get("active_tab") or "")),
                "bottom_tabs": [clean_text(str(x)) for x in list(seg.get("bottom_tabs") or [])],
                "bottom_active": clean_text(str(seg.get("bottom_active") or "")),
                "cards": cards,
                "visual_pages": seg.get("visual_pages") or [],
            }
        )
        label = "开场" if kind == "intro" else "收尾" if kind == "outro" else f"{int(seg.get('position') or idx):02d}"
        md_lines.append(f"## {label} {title}")
        if caption and caption != title:
            md_lines.append(f"- 屏幕标题：{caption}")
        md_lines.append(f"- 解说：{text}")
        if cards:
            md_lines.append("- 画面卡片：")
            for card in cards[:6]:
                card_title = card.get("title") or "卡片"
                card_body = card.get("body") or ""
                md_lines.append(f"  - {card_title}：{card_body}")
        md_lines.append("")
    json_path.write_text(json.dumps({"version": 1, "segments": payload_segments}, ensure_ascii=False, indent=2), encoding="utf-8-sig")
    md_path.write_text("\n".join(md_lines).rstrip() + "\n", encoding="utf-8-sig")
    return md_path, json_path


def _read_news_manuscript(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    segments = payload.get("segments") if isinstance(payload, dict) else None
    if not isinstance(segments, list):
        raise ValueError(f"news manuscript has no segments: {path}")
    return [seg for seg in segments if isinstance(seg, dict)]


def _select_news_manuscript(
    out_dir: Path,
    draft_segments: list[dict[str, Any]],
    *,
    prefer_reviewed: bool = True,
) -> tuple[Path, Path, list[dict[str, Any]], str]:
    root_json = out_dir / "news-script.json"
    root_md = out_dir / "news-script.md"
    review_candidates = [
        (out_dir / "review" / "morning-final-script.md", out_dir / "review" / "morning-final-script.json", "morning-reviewed"),
        (out_dir / "review" / "final-script.md", out_dir / "review" / "final-script.json", "reviewed"),
    ]
    if prefer_reviewed:
        for review_md, review_json, mode in review_candidates:
            if review_json.exists():
                segments = _read_news_manuscript(review_json)
                payload = json.loads(review_json.read_text(encoding="utf-8-sig"))
                root_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8-sig")
                if review_md.exists():
                    root_md.write_text(review_md.read_text(encoding="utf-8-sig"), encoding="utf-8-sig")
                else:
                    _write_news_manuscript(out_dir, segments)
                    review_md = root_md
                return review_md, review_json, segments, mode
    manuscript_md, manuscript_json = _write_news_manuscript(out_dir, draft_segments)
    return manuscript_md, manuscript_json, _read_news_manuscript(manuscript_json), "draft"


def _attach_manuscript_info(out_dir: Path, info: dict[str, str], manuscript_md: Path, manuscript_json: Path) -> dict[str, str]:
    info = {**info, "manuscript": str(manuscript_md), "manuscript_json": str(manuscript_json)}
    contract = record_rendered_manuscript(out_dir, manuscript_json, out_dir / "script.json")
    if contract:
        info["render_contract"] = str(out_dir / "review" / "render-contract.json")
    render_info = out_dir / "render-info.json"
    if render_info.exists():
        render_info.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8-sig")
    return info


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
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(candidate)
    return None


def _visual_pages_for_browser(seg: dict[str, Any], duration: float) -> list[dict[str, Any]]:
    pages = [p for p in list(seg.get("visual_pages") or []) if isinstance(p, dict)]
    if not pages:
        pages = [{"kind": "cards", "title": seg.get("title", ""), "cards": list(seg.get("cards") or [])}]
    required_pages = [
        page
        for page in pages
        if str(page.get("kind") or "") == "evidence"
        and isinstance(page.get("evidenceVisual"), dict)
        and page["evidenceVisual"].get("required") is True
    ]

    def with_required(selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = list(selected)
        result.extend(page for page in required_pages if page not in result)
        return result

    if duration < 10.5:
        # A short verified update still needs every required original post.
        # Keep one compact context page, then divide the same story window
        # across all proof pages without changing narration or timeline time.
        return with_required(pages[:1])
    return with_required(pages[:3])


def _browser_page_windows(duration: float, count: int) -> list[tuple[float, float]]:
    count = max(1, count)
    if count == 1:
        return [(0.0, duration)]
    if count == 2:
        split = duration * 0.58
        return [(0.0, split), (split, duration)]
    step = duration / count
    return [(idx * step, (idx + 1) * step) for idx in range(count)]


def _clean_slide_text(value: Any, limit: int | None = 120) -> str:
    text = clean_text(str(value or "")).replace("\\N", " ")
    text = " ".join(text.split())
    text = text.replace(",", "，").replace(";", "；")
    text = re.sub(r"(?<!\d):(?!\d)", "：", text)
    if limit is None or len(text) <= limit:
        return text
    return text[:limit].rstrip(" ，。,.:-_")


def _browser_timeline_items(segments: list[dict[str, Any]], durations: list[float]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    current = 0.0
    seen: dict[str, int] = {}
    for idx, (seg, duration) in enumerate(zip(segments, durations)):
        duration = max(0.8, float(duration or 0.8))
        kind = str(seg.get("kind") or "news")
        active_tab = str(seg.get("active_tab") or "Intro")
        if kind == "intro":
            label = "开场"
        elif kind == "outro":
            label = str(seg.get("bottom_active") or "收尾")
        else:
            label = str(seg.get("bottom_active") or seg.get("title") or f"{idx:02d}")
        if label in seen:
            seen[label] += 1
            label = f"{label} {seen[label]}"
        else:
            seen[label] = 1
        entity = "收尾" if kind == "outro" else _browser_timeline_entity(seg, label)
        items.append(
            {
                "label": _clean_slide_text(label, 18),
                "entity": _clean_slide_text(entity, 14),
                "headline": _clean_slide_text(seg.get("title") or label, None),
                "start": round(current, 3),
                "end": round(current + duration, 3),
                "duration": round(duration, 3),
                "kind": kind,
                "activeTab": active_tab,
            }
        )
        current += duration
    return items


def _browser_timeline_entity(segment: dict[str, Any], fallback_label: str) -> str:
    explicit = clean_text(str(segment.get("timeline_entity") or ""))
    if explicit:
        return explicit
    title_text = " ".join(
        clean_text(str(segment.get(key) or ""))
        for key in ("title", "headline", "caption")
    )
    # Prefer the product actually being reported over its parent company so
    # adjacent OpenAI stories remain distinct on the public news timeline.
    product_patterns = [
        ("Codex", r"\bCodex\b"),
        ("ChatGPT", r"\bChatGPT\b"),
        ("Grok", r"\bGrok\b"),
        ("Kimi", r"\bKimi\b|月之暗面"),
        ("DeepSeek", r"\bDeepSeek\b|深度求索"),
        ("Gemini", r"\bGemini\b"),
        ("Qwen", r"\bQwen\b|通义千问"),
    ]
    for entity, pattern in product_patterns:
        if re.search(pattern, title_text, flags=re.IGNORECASE):
            return entity
    company_patterns = [
        ("Anthropic", r"\bAnthropic\b"),
        ("OpenAI", r"\bOpenAI\b"),
        ("Google", r"\bGoogle(?:\s+DeepMind)?\b"),
        ("xAI", r"\bxAI\b"),
        ("Meta", r"\bMeta\b"),
        ("NVIDIA", r"\bNVIDIA\b|英伟达"),
        ("字节", r"字节(?:跳动)?|ByteDance"),
        ("阿里", r"阿里(?:巴巴)?|Alibaba"),
        ("腾讯", r"腾讯|Tencent"),
        ("百度", r"百度|Baidu"),
    ]
    for entity, pattern in company_patterns:
        if re.search(pattern, title_text, flags=re.IGNORECASE):
            return entity
    return re.sub(r"^\s*\d{1,2}\s*", "", fallback_label).strip(" /·|-") or fallback_label


def _browser_slides(out_dir: Path, segments: list[dict[str, Any]], durations: list[float], quality: str) -> list[dict[str, Any]]:
    width, height = _quality_size(quality)
    slides: list[dict[str, Any]] = []
    current = 0.0
    # The newsroom renderer paginates complete copy. Truncating here would
    # corrupt the approved manuscript before React ever receives it.
    newsroom = os.environ.get("BRIEFING_VISUAL_STYLE", "newsroom") == "newsroom"

    def visible_text(value: Any, legacy_limit: int) -> str:
        return _clean_slide_text(value, None if newsroom else legacy_limit)
    timeline_items = _browser_timeline_items(segments, durations)
    timeline_total = round(sum(max(0.8, float(x or 0.8)) for x in durations), 3)
    for seg_index, (seg, duration) in enumerate(zip(segments, durations)):
        active_tab = str(seg.get("active_tab") or "Intro")
        accent = TAB_COLORS.get(active_tab, "C85F3C")
        raw_title = str(seg.get("title") or "AI 日报")
        pages = _visual_pages_for_browser(seg, duration)
        windows = _browser_page_windows(duration, len(pages))
        for page_index, (page, (page_start, page_end)) in enumerate(zip(pages, windows)):
            slide_duration = max(0.8, page_end - page_start)
            page_payload = dict(page)
            page_accent = _design_accent(page_payload.get("accent") or page_payload.get("color"), accent)
            page_payload["title"] = visible_text(page_payload.get("title") or raw_title, 90)
            if "lead" in page_payload:
                page_payload["lead"] = visible_text(page_payload.get("lead"), 120)
            cards = []
            page_cards = list(page_payload.get("cards") or [])
            for card in (page_cards if newsroom else page_cards[:6]):
                if isinstance(card, dict):
                    cards.append(
                        {
                            "icon": _clean_slide_text(card.get("icon"), 3),
                            "title": visible_text(card.get("title") or "看点", 18),
                            "body": visible_text(card.get("body"), 120),
                            "meta": visible_text(card.get("meta"), 36),
                            "accent": _clean_slide_text(card.get("accent") or "", 8),
                            "component": _clean_slide_text(card.get("component") or "", 32),
                        }
                    )
            page_payload["cards"] = cards
            if not page_payload.get("source"):
                for card in cards:
                    title_text = str(card.get("title") or "").lower()
                    body_text = str(card.get("body") or "")
                    if "消息" in title_text or "时间" in title_text or "time" in title_text:
                        source = body_text.split(" · ", 1)[0].strip()
                        if source:
                            page_payload["source"] = _clean_slide_text(source, 34)
                            break
            if not page_payload.get("lead"):
                for card in cards:
                    title_text = str(card.get("title") or "")
                    if "发生" in title_text or "confirmed" in title_text.lower():
                        lead = str(card.get("body") or "").strip()
                        if lead:
                            page_payload["lead"] = _clean_slide_text(lead, 92)
                            break
            slides.append(
                {
                    "index": len(slides),
                    "segmentIndex": seg_index,
                    "pageIndex": page_index,
                    "duration": round(slide_duration, 3),
                    "start": round(current + page_start, 3),
                    "end": round(current + page_end, 3),
                    "width": width,
                    "height": height,
                    "runLabel": out_dir.name,
                    "kind": str(seg.get("kind") or "news"),
                    "storyPosition": int(seg.get("position") or 0),
                    "storyTotal": int(seg.get("total") or 0),
                    "activeTab": active_tab,
                    "accent": page_accent,
                    "title": visible_text(raw_title, 92),
                    "caption": visible_text(seg.get("caption") or raw_title, 70),
                    "kicker": _browser_kicker(seg, active_tab),
                    "bottomTabs": [str(x) for x in (seg.get("bottom_tabs") or ["开场", "收尾"])],
                    "bottomActive": str(seg.get("bottom_active") or active_tab),
                    "timelineItems": timeline_items,
                    "timelineTotal": timeline_total,
                    "page": page_payload,
                }
            )
        current += duration
    return slides


def _browser_kicker(seg: dict[str, Any], active_tab: str) -> str:
    if seg.get("kind") == "news":
        pos = int(seg.get("position") or 0)
        total = int(seg.get("total") or 0)
        if pos and total:
            return f"{pos:02d} / {total:02d}  {active_tab}"
    if active_tab == "Intro":
        return "今日早报"
    if active_tab == "Outro":
        return "收尾"
    return active_tab


def _write_visual_html(render_dir: Path, slides: list[dict[str, Any]]) -> Path:
    payload = json.dumps(slides, ensure_ascii=False).replace("</", "<\\/")
    html = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
* { box-sizing: border-box; }
html, body { margin: 0; width: 100%; height: 100%; overflow: hidden; background: #eef4ef; }
body { font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", Arial, sans-serif; color: #16231e; }
.stage {
  width: 1920px;
  height: 1080px;
  position: relative;
  transform: scale(var(--stage-scale, 1));
  transform-origin: 0 0;
  overflow: hidden;
  background:
    linear-gradient(135deg, rgba(54, 99, 82, 0.08), transparent 38%),
    linear-gradient(315deg, rgba(189, 86, 55, 0.10), transparent 34%),
    #f4f7f2;
}
.stage::before {
  content: "";
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(39, 57, 48, 0.045) 1px, transparent 1px),
    linear-gradient(90deg, rgba(39, 57, 48, 0.045) 1px, transparent 1px);
  background-size: 48px 48px;
  mask-image: linear-gradient(to bottom, rgba(0,0,0,0.76), rgba(0,0,0,0.18));
  pointer-events: none;
}
.topbar {
  position: absolute;
  left: 72px;
  right: 72px;
  top: 22px;
  height: 56px;
  display: grid;
  grid-template-columns: 230px 1fr 190px;
  gap: 28px;
  align-items: center;
}
.brand {
  height: 42px;
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 22px;
  font-weight: 800;
  letter-spacing: 0;
  color: #23362f;
}
.brand-mark {
  width: 34px;
  height: 34px;
  border: 5px solid var(--accent, #4e7c68);
  background: #ffffff;
  box-shadow: inset 0 0 0 5px #e8efe9;
}
.nav {
  display: grid;
  grid-template-columns: repeat(var(--nav-count), minmax(0, 1fr));
  gap: 8px;
}
.nav-item {
  min-width: 0;
  height: 42px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 10px;
  border: 1px solid #d6e0d9;
  background: rgba(255,255,255,0.70);
  color: #405149;
  font-size: 17px;
  font-weight: 650;
  line-height: 1.08;
  text-align: center;
}
.nav-item.active {
  background: var(--accent, #4e7c68);
  border-color: var(--accent, #4e7c68);
  color: #ffffff;
}
.run-label {
  text-align: right;
  color: #65756d;
  font-size: 20px;
  font-weight: 700;
}
.header {
  position: absolute;
  left: 116px;
  right: 116px;
  top: 104px;
  height: 108px;
  display: grid;
  grid-template-columns: 1fr 280px;
  column-gap: 42px;
  align-items: start;
}
.title {
  margin: 0;
  color: var(--accent, #4e7c68);
  font-size: var(--title-size, 55px);
  line-height: 1.08;
  font-weight: 900;
  letter-spacing: 0;
  max-height: 120px;
  overflow: hidden;
}
.kicker {
  align-self: start;
  justify-self: end;
  margin-top: 9px;
  padding: 10px 16px;
  border-left: 6px solid var(--accent, #4e7c68);
  background: rgba(255,255,255,0.72);
  color: #51645a;
  font-size: 23px;
  font-weight: 800;
  line-height: 1.12;
  text-align: right;
}
.content {
  position: absolute;
  left: 116px;
  right: 116px;
  top: 230px;
  bottom: 158px;
}
.board {
  width: 100%;
  height: 100%;
}
.cards-board {
  display: grid;
  grid-template-columns: minmax(0, 0.92fr) minmax(0, 1.08fr);
  grid-template-rows: 1fr 122px;
  gap: 28px;
}
.card {
  position: relative;
  overflow: hidden;
  border: 1px solid #d9e1dc;
  background: rgba(255,255,255,0.92);
  box-shadow: 12px 14px 0 rgba(72, 100, 88, 0.10);
}
.card::after {
  content: "";
  position: absolute;
  right: 24px;
  top: 22px;
  width: 18px;
  height: 18px;
  background: var(--card-accent, var(--accent, #4e7c68));
}
.feature-card {
  grid-row: 1 / 3;
  padding: 38px 42px;
  border-top: 8px solid var(--card-accent, var(--accent, #4e7c68));
}
.feature-card .icon {
  font-size: 42px;
  color: var(--card-accent, var(--accent, #4e7c68));
  font-weight: 900;
}
.feature-card .card-title {
  margin-top: 18px;
  color: var(--card-accent, var(--accent, #4e7c68));
  font-size: 38px;
  line-height: 1.1;
  font-weight: 900;
}
.feature-card .card-body {
  margin-top: 30px;
  color: #202d27;
  font-size: 34px;
  line-height: 1.32;
  font-weight: 750;
  max-height: 172px;
  overflow: hidden;
}
.signal-deck {
  position: absolute;
  left: 42px;
  right: 42px;
  bottom: 96px;
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
}
.signal-tile {
  min-width: 0;
  min-height: 86px;
  padding: 16px 18px;
  border: 1px solid #dce6df;
  background: #f3f8f4;
}
.signal-label {
  color: var(--tile-accent, var(--accent, #4e7c68));
  font-size: 18px;
  font-weight: 900;
  line-height: 1.1;
}
.signal-value {
  margin-top: 9px;
  color: #26332d;
  font-size: 21px;
  line-height: 1.22;
  font-weight: 740;
  max-height: 52px;
  overflow: hidden;
}
.confidence-note {
  position: absolute;
  right: 72px;
  top: 34px;
  color: #69786f;
  font-size: 18px;
  font-weight: 850;
}
.feature-card .meta-line {
  position: absolute;
  left: 42px;
  right: 42px;
  bottom: 36px;
  padding-top: 20px;
  border-top: 2px solid #e0e8e3;
  color: #67786f;
  font-size: 20px;
  font-weight: 800;
}
.support-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 24px;
}
.support-card {
  padding: 28px 30px;
}
.support-card .card-head {
  display: flex;
  align-items: center;
  gap: 14px;
  min-width: 0;
}
.support-card .icon {
  flex: 0 0 auto;
  color: var(--card-accent, var(--accent, #4e7c68));
  font-size: 28px;
  font-weight: 900;
}
.support-card .card-title {
  min-width: 0;
  color: var(--card-accent, var(--accent, #4e7c68));
  font-size: 26px;
  line-height: 1.1;
  font-weight: 900;
}
.support-card .card-body {
  margin-top: 20px;
  color: #26332d;
  font-size: 23px;
  line-height: 1.35;
  font-weight: 620;
  max-height: 104px;
  overflow: hidden;
}
.strip-card {
  grid-column: 2;
  padding: 24px 30px;
  display: grid;
  grid-template-columns: 180px 1fr;
  gap: 24px;
  align-items: center;
}
.strip-card .card-title {
  color: var(--card-accent, var(--accent, #4e7c68));
  font-size: 28px;
  font-weight: 900;
}
.strip-card .card-body {
  color: #26332d;
  font-size: 23px;
  line-height: 1.3;
  font-weight: 650;
  max-height: 62px;
  overflow: hidden;
}
.evidence-board {
  position: relative;
  overflow: visible;
}
.evidence-backdrop {
  position: absolute;
  inset: 38px 18px 88px 18px;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 28px;
  opacity: 0.52;
}
.backdrop-card {
  border: 1px solid #d5ddd7;
  background: rgba(255,255,255,0.72);
  box-shadow: 10px 12px 0 rgba(80, 95, 86, 0.08);
  padding: 30px 34px;
  overflow: hidden;
}
.backdrop-label {
  color: var(--accent, #4e7c68);
  font-size: 23px;
  font-weight: 900;
}
.backdrop-body {
  margin-top: 18px;
  color: #26332d;
  font-size: 27px;
  line-height: 1.28;
  font-weight: 760;
}
.popup-sheet {
  position: absolute;
  left: 190px;
  right: 190px;
  top: 20px;
  height: 468px;
  border: 1px solid #dfe6e1;
  background: #ffffff;
  box-shadow: 0 20px 42px rgba(58, 66, 61, 0.22);
  padding: 28px 38px 30px;
}
.popup-head {
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: center;
  gap: 28px;
}
.popup-title {
  color: #25322c;
  font-size: 31px;
  line-height: 1.12;
  font-weight: 900;
}
.popup-source {
  color: var(--accent, #4e7c68);
  font-size: 20px;
  font-weight: 900;
}
.chart {
  position: absolute;
  left: 58px;
  right: 58px;
  bottom: 74px;
  height: 260px;
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 32px;
  align-items: end;
  border-bottom: 2px solid #d8dfda;
}
.bar-group {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  align-items: end;
  height: 100%;
}
.bar {
  position: relative;
  background: var(--bar-color, var(--accent, #4e7c68));
  min-height: 34px;
}
.bar.alt {
  background: #2f3946;
}
.bar-value {
  position: absolute;
  top: -31px;
  left: 50%;
  transform: translateX(-50%);
  color: #26332d;
  font-size: 19px;
  font-weight: 900;
}
.chart-labels {
  position: absolute;
  left: 58px;
  right: 58px;
  bottom: 32px;
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 32px;
  color: #69776f;
  font-size: 18px;
  font-weight: 800;
  text-align: center;
}
.evidence-strip {
  position: absolute;
  left: 92px;
  right: 92px;
  bottom: 58px;
  min-height: 82px;
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 12px;
}
.evidence-chip {
  min-width: 0;
  padding: 16px 18px;
  border: 1px solid #d6e0da;
  background: rgba(255,255,255,0.90);
}
.chip-label {
  color: var(--accent, #4e7c68);
  font-size: 17px;
  font-weight: 900;
}
.chip-body {
  margin-top: 7px;
  color: #26332d;
  font-size: 20px;
  line-height: 1.18;
  font-weight: 720;
  max-height: 48px;
  overflow: hidden;
}
.timeline {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 56px;
  display: grid;
  grid-template-columns: repeat(var(--timeline-count), minmax(0, 1fr));
  gap: 0;
  border-top: 1px solid #bfcac2;
  background: rgba(244, 247, 242, 0.98);
}
.timeline-item {
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 8px;
  border: 0;
  border-right: 1px solid #c1cec6;
  background: rgba(255,255,255,0.72);
  color: #283932;
  font-size: var(--timeline-font, 17px);
  line-height: 1.05;
  font-weight: 750;
  text-align: center;
  overflow: hidden;
}
.timeline-item.active {
  border-color: var(--accent, #4e7c68);
  background: var(--accent, #4e7c68);
  color: #ffffff;
}
.progress {
  position: absolute;
  left: 0;
  bottom: 56px;
  height: 4px;
  background: var(--accent, #4e7c68);
}
</style>
</head>
<body>
<div id="stage" class="stage"></div>
<script id="slides-data" type="application/json">__SLIDES_JSON__</script>
<script>
const NAV_TABS = __NAV_JSON__;
const PALETTE = ["#4e7c68", "#c75b3c", "#2f7b7b", "#d08a24", "#3d668c", "#9a4c55"];
const slides = JSON.parse(document.getElementById("slides-data").textContent);
const params = new URLSearchParams(location.search);
const slide = slides[Math.max(0, Math.min(slides.length - 1, Number(params.get("slide") || 0)))] || slides[0];
const stage = document.getElementById("stage");
function setScale() {
  document.documentElement.style.setProperty("--stage-scale", String(Math.min(window.innerWidth / 1920, window.innerHeight / 1080)));
}
setScale();
document.documentElement.style.setProperty("--accent", "#" + (slide.accent || "4e7c68"));
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}
function colorFor(card, idx) {
  const raw = card && card.accent ? String(card.accent).replace("#", "") : "";
  if (/^[0-9a-fA-F]{6}$/.test(raw)) return "#" + raw;
  return PALETTE[idx % PALETTE.length];
}
function addTopbar() {
  const topbar = el("div", "topbar");
  const brand = el("div", "brand");
  brand.append(el("div", "brand-mark"));
  brand.append(el("div", "", "AI 日报"));
  topbar.append(brand);
  const nav = el("div", "nav");
  nav.style.setProperty("--nav-count", String(NAV_TABS.length));
  NAV_TABS.forEach(([key, label]) => {
    const item = el("div", "nav-item" + (key === slide.activeTab ? " active" : ""), label);
    nav.append(item);
  });
  topbar.append(nav);
  topbar.append(el("div", "run-label", slide.runLabel || ""));
  stage.append(topbar);
}
function addHeader() {
  const header = el("div", "header");
  const title = el("h1", "title", slide.title || "AI 日报");
  title.style.setProperty("--title-size", (String(slide.title || "").length > 30 ? "46px" : "55px"));
  header.append(title);
  header.append(el("div", "kicker", slide.kicker || ""));
  stage.append(header);
}
function cardNode(card, idx, className) {
  const node = el("div", "card " + className);
  node.style.setProperty("--card-accent", colorFor(card, idx));
  const icon = card && card.icon ? card.icon : "■";
  const title = card && card.title ? card.title : "看点";
  const body = card && card.body ? card.body : "";
  if (className.includes("feature-card")) {
    node.append(el("div", "icon", icon));
    node.append(el("div", "card-title", title));
    if (card && card.meta) node.append(el("div", "confidence-note", card.meta));
    node.append(el("div", "card-body", body));
    return node;
  }
  if (className.includes("strip-card")) {
    node.append(el("div", "card-title", title));
    node.append(el("div", "card-body", body));
    return node;
  }
  const head = el("div", "card-head");
  head.append(el("div", "icon", icon));
  head.append(el("div", "card-title", title));
  node.append(head);
  node.append(el("div", "card-body", body));
  return node;
}
function featureSignalDeck(cards) {
  const deck = el("div", "signal-deck");
  const picks = cards.slice(1, 5);
  picks.forEach((card, idx) => {
    const tile = el("div", "signal-tile");
    tile.style.setProperty("--tile-accent", colorFor(card, idx + 1));
    tile.append(el("div", "signal-label", card.title || "观察点"));
    tile.append(el("div", "signal-value", card.body || ""));
    deck.append(tile);
  });
  return deck;
}
function addCardsBoard(content, page) {
  const cards = (page.cards && page.cards.length ? page.cards : [{ icon: "■", title: "看点", body: page.title || slide.title }]).slice(0, 6);
  const board = el("div", "board cards-board");
  board.dataset.count = String(cards.length);
  const feature = cardNode(cards[0], 0, "feature-card");
  const signals = featureSignalDeck(cards);
  if (signals.children.length) feature.append(signals);
  board.append(feature);
  const supportCards = cards.slice(1, 5);
  if (supportCards.length) {
    const support = el("div", "support-grid");
    support.style.setProperty("--support-count", String(supportCards.length));
    supportCards.forEach((card, idx) => support.append(cardNode(card, idx + 1, "support-card")));
    board.append(support);
  }
  if (cards.length > 5) board.append(cardNode(cards[5], 5, "strip-card"));
  content.append(board);
}
function addEvidenceBoard(content, page) {
  const board = el("div", "board evidence-board");
  const background = el("div", "evidence-backdrop");
  const bgCards = (page.background_cards && page.background_cards.length ? page.background_cards : page.cards || []).slice(0, 4);
  bgCards.forEach((card, idx) => {
    const item = el("div", "backdrop-card");
    item.append(el("div", "backdrop-label", card.title || "观察"));
    item.append(el("div", "backdrop-body", card.body || ""));
    background.append(item);
  });
  board.append(background);

  const popup = el("div", "popup-sheet");
  const head = el("div", "popup-head");
  head.append(el("div", "popup-title", page.lead || page.title || slide.title || ""));
  head.append(el("div", "popup-source", page.source || "资料来源"));
  popup.append(head);
  const chart = el("div", "chart");
  const values = [88, 74, 61, 43, 32];
  values.forEach((value, idx) => {
    const group = el("div", "bar-group");
    const a = el("div", "bar");
    a.style.height = `${value}%`;
    a.style.setProperty("--bar-color", idx % 2 ? "#d08a24" : "#" + (slide.accent || "4e7c68"));
    a.append(el("div", "bar-value", String(value)));
    const b = el("div", "bar alt");
    b.style.height = `${Math.max(18, value - 12)}%`;
    b.append(el("div", "bar-value", String(Math.max(18, value - 12))));
    group.append(a);
    group.append(b);
    chart.append(group);
  });
  popup.append(chart);
  const labels = el("div", "chart-labels");
  ["来源", "发布时间", "来源状态", "影响", "后续"].forEach(label => labels.append(el("div", "", label)));
  popup.append(labels);
  board.append(popup);

  const strip = el("div", "evidence-strip");
  (page.cards || []).slice(0, 5).forEach((card, idx) => {
    const chip = el("div", "evidence-chip");
    chip.append(el("div", "chip-label", card.title || `信息 ${idx + 1}`));
    chip.append(el("div", "chip-body", card.body || ""));
    strip.append(chip);
  });
  board.append(strip);
  content.append(board);
}
function addContent() {
  const content = el("div", "content");
  const page = slide.page || {};
  if ((page.kind || "cards") === "evidence") addEvidenceBoard(content, page);
  else addCardsBoard(content, page);
  stage.append(content);
}
function addTimeline() {
  const tabs = slide.bottomTabs && slide.bottomTabs.length ? slide.bottomTabs : ["开场", "收尾"];
  const active = slide.bottomActive || tabs[0];
  const timeline = el("div", "timeline");
  timeline.style.setProperty("--timeline-count", String(tabs.length));
  timeline.style.setProperty("--timeline-font", (tabs.length > 10 ? "13px" : tabs.length > 8 ? "15px" : "17px"));
  tabs.forEach(label => timeline.append(el("div", "timeline-item" + (label === active ? " active" : ""), label)));
  stage.append(timeline);
  const activeIndex = Math.max(0, tabs.indexOf(active));
  const progress = el("div", "progress");
  progress.style.width = `${(1920 * (activeIndex + 1)) / tabs.length}px`;
  stage.append(progress);
}
addTopbar();
addHeader();
addContent();
addTimeline();
</script>
</body>
</html>
"""
    nav_json = json.dumps(NAV_TABS, ensure_ascii=False)
    html = html.replace("__SLIDES_JSON__", payload).replace("__NAV_JSON__", nav_json)
    path = render_dir / "visual-slides.html"
    path.write_text(html, encoding="utf-8")
    return path


def _capture_visual_slides(render_dir: Path, html_path: Path, slides: list[dict[str, Any]], quality: str) -> list[Path]:
    chrome = _browser_exe()
    if not chrome:
        raise RuntimeError("Chrome or Edge executable not found for browser rendering")
    width, height = _quality_size(quality)
    images: list[Path] = []
    for idx in range(len(slides)):
        image = render_dir / f"visual-slide-{idx:03d}.png"
        url = f"{html_path.as_uri()}?slide={idx}"
        cmd = [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--no-first-run",
            "--disable-background-networking",
            f"--window-size={width},{height}",
            f"--screenshot={image}",
            url,
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45)
        if not image.exists() or image.stat().st_size == 0:
            raise RuntimeError(f"visual slide screenshot was not created: {image}")
        images.append(image)
    return images


def _write_browser_subtitle_ass(render_dir: Path, slides: list[dict[str, Any]], quality: str) -> Path:
    width, height = _quality_size(quality)
    scale = width / 1920

    def sz(v: int) -> int:
        return max(1, int(round(v * scale)))

    ass = render_dir / "browser-subtitles.ass"
    header = f"""[Script Info]
ScriptType: v4.00+
ScaledBorderAndShadow: yes
PlayResX: {width}
PlayResY: {height}

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Shape,Arial,{sz(16)},&H00FFFFFF,&H000000FF,&H00FFFFFF,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: LowerThird,Microsoft YaHei UI,{sz(32)},&H0033423A,&H000000FF,&H00F7FBF8,&H00000000,-1,0,0,0,100,100,0,0,1,{sz(1)},0,5,0,0,0,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    events = [header]
    box_w = width - sz(720)
    box_x = (width - box_w) // 2
    box_y = height - sz(224)
    box_h = sz(84)
    accent_w = sz(8)
    text_y = box_y + box_h // 2
    last_caption = ""
    for slide in slides:
        caption = _clean_slide_text(slide.get("caption") or slide.get("title") or "", 72)
        if not caption:
            continue
        start = _ass_time(float(slide.get("start") or 0.0))
        end = _ass_time(float(slide.get("end") or 0.0))
        accent = str(slide.get("accent") or "4E7C68")
        text = _escape_ass(_wrap_cjk(caption, 42, 2))
        fade_tag = "\\fad(150,120)"
        events.append(_rect_event_fx(start, end, box_x + sz(8), box_y + sz(8), box_w, box_h, "CBD8D1", "7A", 2, fade_tag))
        events.append(_box_event_fx(start, end, box_x, box_y, box_w, box_h, "F4FAF6", "D8E3DC", "10", 3, sz(1), fade_tag))
        events.append(_rect_event_fx(start, end, box_x, box_y, accent_w, box_h, accent, "00", 5, fade_tag))
        if caption == last_caption:
            text_extra = "\\fad(90,90)"
        else:
            text_extra = fade_tag
        events.append(_dialogue(start, end, "LowerThird", text, 7, f"{{\\pos({width // 2},{text_y}){text_extra}}}"))
        last_caption = caption
    ass.write_text("".join(events), encoding="utf-8-sig")
    return ass


def _render_static_slides_video(
    ffmpeg: str,
    render_dir: Path,
    images: list[Path],
    durations: list[float],
    narration: Path,
    video: Path,
    width: int,
    height: int,
) -> None:
    list_file = render_dir / "visual-slides-concat.txt"
    lines: list[str] = []
    for image, duration in zip(images, durations):
        safe_path = str(image).replace("'", "'\\''")
        lines.append(f"file '{safe_path}'")
        lines.append(f"duration {max(0.8, duration):.3f}")
    if images:
        last_safe_path = str(images[-1]).replace("'", "'\\''")
        lines.append(f"file '{last_safe_path}'")
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _run(
        [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-i",
            str(narration),
            "-vf",
            f"scale={width}:{height},format=yuv420p",
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "22",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-b:a",
            "160k",
            str(video),
        ],
        cwd=render_dir,
        timeout=_subprocess_timeout("FFMPEG_ENCODE", 3600),
    )


def _burn_ass_subtitles(ffmpeg: str, render_dir: Path, base_video: Path, subtitle_ass: Path, final_video: Path) -> None:
    _run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(base_video),
            "-vf",
            f"ass={subtitle_ass.name}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "22",
            "-c:a",
            "copy",
            str(final_video),
        ],
        cwd=render_dir,
        timeout=_subprocess_timeout("FFMPEG_ENCODE", 3600),
    )


def _render_visual_slides_video(
    ffmpeg: str,
    render_dir: Path,
    images: list[Path],
    durations: list[float],
    narration: Path,
    video: Path,
    width: int,
    height: int,
) -> None:
    if not images:
        raise RuntimeError("no visual slides to render")
    transition = 0.28 if len(images) > 1 else 0.0
    cmd = [ffmpeg, "-y"]
    for idx, (image, duration) in enumerate(zip(images, durations)):
        clip_duration = max(0.8, duration) + (transition if idx < len(images) - 1 else 0.0)
        cmd.extend(["-loop", "1", "-t", f"{clip_duration:.3f}", "-i", str(image)])
    cmd.extend(["-i", str(narration)])
    filters: list[str] = []
    for idx, duration in enumerate(durations):
        clip_duration = max(0.8, duration) + (transition if idx < len(images) - 1 else 0.0)
        filters.append(
            f"[{idx}:v]scale={width}:{height},fps=30,settb=AVTB,format=yuv420p,"
            f"trim=duration={clip_duration:.3f},setpts=PTS-STARTPTS[v{idx}]"
        )
    if len(images) == 1:
        filters.append("[v0]format=yuv420p[v]")
    else:
        last = "v0"
        elapsed = max(0.8, durations[0])
        for idx in range(1, len(images)):
            offset = max(0.0, elapsed)
            out_label = f"x{idx}"
            filters.append(f"[{last}][v{idx}]xfade=transition=fade:duration={transition:.2f}:offset={offset:.3f}[{out_label}]")
            last = out_label
            elapsed += max(0.8, durations[idx])
        filters.append(f"[{last}]format=yuv420p[v]")
    audio_index = len(images)
    cmd.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[v]",
            "-map",
            f"{audio_index}:a",
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "22",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-b:a",
            "160k",
            str(video),
        ]
    )
    try:
        _run(cmd, cwd=render_dir, timeout=_subprocess_timeout("FFMPEG_ENCODE", 3600))
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        _render_static_slides_video(ffmpeg, render_dir, images, durations, narration, video, width, height)


def _render_browser_video(
    out_dir: Path,
    render_dir: Path,
    segments: list[dict[str, Any]],
    durations: list[float],
    narration: Path,
    srt: Path,
    script_json: Path,
    quality: str,
    tts_status: str,
) -> dict[str, str] | None:
    ffmpeg = _ffmpeg()
    if not ffmpeg or not _browser_exe():
        return None
    width, height = _quality_size(quality)
    slides = _browser_slides(out_dir, segments, durations, quality)
    html_path = _write_visual_html(render_dir, slides)
    images = _capture_visual_slides(render_dir, html_path, slides, quality)
    subtitle_ass = _write_browser_subtitle_ass(render_dir, slides, quality)
    slide_durations = [float(slide["duration"]) for slide in slides]
    video = out_dir / "final.mp4"
    base_video = render_dir / "visual-base.mp4"
    cover = out_dir / "cover.png"
    _render_visual_slides_video(ffmpeg, render_dir, images, slide_durations, narration, base_video, width, height)
    _burn_ass_subtitles(ffmpeg, render_dir, base_video, subtitle_ass, video)
    _run([ffmpeg, "-y", "-ss", "0.3", "-i", str(video), "-frames:v", "1", str(cover)], timeout=_subprocess_timeout("FFMPEG", 1800))
    info = {
        "status": "ok",
        "renderer": "browser-css",
        "subtitle_renderer": "ass-libass",
        "tts": tts_status,
        "segments": str(len(segments)),
        "slides": str(len(slides)),
        "duration_seconds": f"{sum(durations):.2f}",
        "resolution": f"{width}x{height}",
        "video": str(video),
        "cover": str(cover),
        "subtitles": str(srt),
        "script": str(script_json),
        "visual_html": str(html_path),
        "subtitle_ass": str(subtitle_ass),
    }
    (out_dir / "render-info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8-sig")
    return info


def render_placeholder(out_dir: Path, duration_seconds: int, quality: str = "1080p") -> dict[str, str]:
    """Backward-compatible silent placeholder used when no card data is available."""
    return render_briefing_video(out_dir, [], quality=quality, fallback_duration=duration_seconds)


def render_briefing_video(
    out_dir: Path,
    cards: list[EvidenceCard],
    quality: str = "1080p",
    fallback_duration: int = 60,
    *,
    prefer_reviewed_manuscript: bool = True,
    ticker_cards: list[EvidenceCard] | None = None,
) -> dict[str, str]:
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    render_dir = out_dir / "render"
    render_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg = _ffmpeg()
    if not ffmpeg:
        (out_dir / "final.mp4").write_bytes(b"NO_FFMPEG")
        (out_dir / "cover.png").write_bytes(b"NO_FFMPEG")
        return {"status": "no_ffmpeg"}

    draft_segments = _segments(cards, run_date=out_dir.name, ticker_cards=ticker_cards)
    manuscript_md, manuscript_json, segments, manuscript_mode = _select_news_manuscript(
        out_dir,
        draft_segments,
        prefer_reviewed=prefer_reviewed_manuscript,
    )
    tts_backend = os.environ.get("BRIEFING_TTS_BACKEND", "edge").strip().lower() or "edge"
    if tts_backend not in {"edge", "indextts2"}:
        raise RuntimeError(f"unsupported BRIEFING_TTS_BACKEND: {tts_backend}")
    voice = os.environ.get("BRIEFING_TTS_VOICE", "zh-CN-XiaoxiaoNeural")
    tts_rate = os.environ.get("BRIEFING_TTS_RATE", "+8%")
    tts_pitch = os.environ.get("BRIEFING_TTS_PITCH", "+0Hz")
    tts_status = f"edge-tts:{voice}"
    tts_profile_id = ""
    tts_profile_publishable = "false"
    subtitle_paths: list[Path] | None = None
    if tts_backend == "indextts2":
        try:
            wavs, tts_profile_id = _indextts2_tts(render_dir, segments)
        except Exception as exc:
            (render_dir / "indextts2-error.txt").write_text(str(exc), encoding="utf-8-sig")
            raise
        tts_status = f"indextts2:{tts_profile_id}"
        # The backend refuses publishable=false profiles unless a local-test
        # override is explicitly set.  That override must never satisfy the
        # automatic publication gate below.
        tts_profile_publishable = (
            "false" if os.environ.get("BRIEFING_ALLOW_NONPUBLISHABLE_PROFILE", "").strip() else "true"
        )
        durations = [_duration(w) for w in wavs]
        narration = _concat_audio(render_dir, wavs)
    else:
        try:
            wavs, subtitle_paths, actual_voice = _edge_tts(render_dir, segments)
            tts_status = f"edge-tts:{actual_voice}"
            durations = [_duration(w) for w in wavs]
            narration = _concat_audio(render_dir, wavs)
        except Exception as exc:
            (render_dir / "edge-tts-error.txt").write_text(str(exc), encoding="utf-8-sig")
            tts_status = "sapi_fallback"
            try:
                wavs = _powershell_tts(render_dir, segments)
                durations = [_duration(w) for w in wavs]
                narration = _concat_audio(render_dir, wavs)
            except Exception as sapi_exc:
                tts_status = f"silent_fallback:{type(sapi_exc).__name__}"
                durations = [max(7.0, min(24.0, len(str(seg.get("text", ""))) / 9.5)) for seg in segments]
                narration = _silent_audio(render_dir, durations)
                (render_dir / "tts-error.txt").write_text(str(sapi_exc), encoding="utf-8-sig")

    srt = _write_srt(out_dir, segments, durations, subtitle_paths=subtitle_paths)
    script_json = _write_script_json(out_dir, segments, durations)
    width, height = _quality_size(quality)
    video = out_dir / "final.mp4"
    cover = out_dir / "cover.png"
    def finish(info: dict[str, str]) -> dict[str, str]:
        info["tts_backend"] = tts_backend
        if tts_status.startswith("edge-tts:"):
            info["tts_voice"] = tts_status.partition(":")[2]
            info["tts_rate"] = tts_rate
            info["tts_pitch"] = tts_pitch
        if tts_profile_id:
            info["tts_profile_id"] = tts_profile_id
            info["tts_profile_publishable"] = tts_profile_publishable
        if video.exists():
            from .audio_quality import analyze_audio
            quality_report = analyze_audio(video, out_dir / "audio-quality.json")
            info["audio_quality"] = str(out_dir / "audio-quality.json")
            info["audio_quality_ok"] = str(bool(quality_report.get("ok"))).lower()
        return _attach_manuscript_info(out_dir, info, manuscript_md, manuscript_json)
    engine = os.environ.get("BRIEFING_RENDER_ENGINE", "remotion-only").strip().lower()
    if engine in {"remotion", "auto", "remotion-only"}:
        slides = _browser_slides(out_dir, segments, durations, quality)
        try:
            from .remotion_renderer import render_remotion_video

            info = render_remotion_video(out_dir, render_dir, slides, durations, narration, srt, script_json, quality, tts_status)
            info["manuscript_mode"] = manuscript_mode
            return finish(info)
        except Exception as exc:
            (render_dir / "remotion-render-error.txt").write_text(f"{type(exc).__name__}: {exc}", encoding="utf-8-sig")
            repair_info = _repair_remotion_environment(render_dir)
            if repair_info.get("ok") == "true":
                try:
                    from .remotion_renderer import render_remotion_video

                    info = render_remotion_video(out_dir, render_dir, slides, durations, narration, srt, script_json, quality, tts_status)
                    info["auto_repair"] = json.dumps({"remotion": repair_info}, ensure_ascii=False)
                    info["manuscript_mode"] = manuscript_mode
                    return finish(info)
                except Exception as retry_exc:
                    (render_dir / "remotion-render-retry-error.txt").write_text(f"{type(retry_exc).__name__}: {retry_exc}", encoding="utf-8-sig")
            # "remotion-only" is a hard product contract, not a preference.
            # Never silently downgrade a dynamic edition to the static ASS path.
            if engine == "remotion-only":
                return {"status": "remotion_error", "tts": tts_status}

    if engine not in {"ass", "legacy", "remotion-only"}:
        try:
            browser_info = _render_browser_video(out_dir, render_dir, segments, durations, narration, srt, script_json, quality, tts_status)
            if browser_info:
                browser_info["manuscript_mode"] = manuscript_mode
                return finish(browser_info)
        except Exception as exc:
            (render_dir / "browser-render-error.txt").write_text(f"{type(exc).__name__}: {exc}", encoding="utf-8-sig")

    ass = _write_ass(render_dir, segments, durations, quality)
    total = max(float(fallback_duration), sum(durations))
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=0xf8f6ef:s={width}x{height}:r=30",
        "-i",
        str(narration),
        "-vf",
        f"ass={ass.name}",
        "-t",
        f"{total:.3f}",
        "-shortest",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-b:a",
        "160k",
        str(video),
    ]
    try:
        _run(cmd, cwd=render_dir, timeout=_subprocess_timeout("FFMPEG_ENCODE", 3600))
        _run([ffmpeg, "-y", "-ss", "0.3", "-i", str(video), "-frames:v", "1", str(cover)], timeout=_subprocess_timeout("FFMPEG", 1800))
        info = {
            "status": "ok",
            "renderer": "ass",
            "tts": tts_status,
            "segments": str(len(segments)),
            "duration_seconds": f"{sum(durations):.2f}",
            "resolution": f"{width}x{height}",
            "video": str(video),
            "cover": str(cover),
            "subtitles": str(srt),
            "script": str(script_json),
            "manuscript": str(manuscript_md),
            "manuscript_json": str(manuscript_json),
            "manuscript_mode": manuscript_mode,
        }
        return finish(info)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        stderr = getattr(exc, "stderr", None)
        if isinstance(stderr, str):
            stderr = stderr.encode("utf-8", errors="ignore")
        (render_dir / "render-error.txt").write_bytes(stderr or str(exc).encode("utf-8", errors="ignore"))
        return {"status": "ffmpeg_error", "tts": tts_status}

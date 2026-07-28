from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .models import Source


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _strip_yaml_comments(text: str) -> str:
    # The default sources.yaml is JSON-compatible YAML. This helper lets us tolerate
    # simple full-line comments without requiring PyYAML.
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines)


def _coerce_scalar(value: str) -> Any:
    value = value.strip()
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"null", "None", ""}:
        return None
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [x.strip().strip('"\'') for x in inner.split(",")]
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        return value


def _parse_minimal_yaml(text: str) -> dict[str, Any]:
    """Parse a tiny subset of YAML used for source lists."""
    data: dict[str, Any] = {"sources": []}
    current: dict[str, Any] | None = None
    section: str | None = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if re.match(r"^[A-Za-z_][\w-]*:\s*$", raw):
            section = raw.split(":", 1)[0].strip()
            if section not in data:
                data[section] = [] if section == "sources" else {}
            continue
        if section == "sources" and raw.lstrip().startswith("- "):
            current = {}
            data["sources"].append(current)
            rest = raw.lstrip()[2:].strip()
            if rest and ":" in rest:
                k, v = rest.split(":", 1)
                current[k.strip()] = _coerce_scalar(v.strip())
            continue
        if section == "sources" and current is not None and ":" in raw:
            k, v = raw.strip().split(":", 1)
            current[k.strip()] = _coerce_scalar(v.strip())
            continue
    return data


def load_config(path: Path | None = None) -> dict[str, Any]:
    path = path or (project_root() / "sources.yaml")
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(_strip_yaml_comments(text))
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore

            parsed = yaml.safe_load(text)
            return parsed or {}
        except Exception:
            return _parse_minimal_yaml(text)


def load_sources(path: Path | None = None, include_disabled: bool = False) -> list[Source]:
    cfg = load_config(path)
    defaults = cfg.get("defaults", {}) or {}
    timeout = int(defaults.get("timeout_seconds", 18))
    rsshub_base = os.environ.get("RSSHUB_BASE_URL", "").rstrip("/")
    x_rss_base = os.environ.get("X_RSS_BASE_URL", str(defaults.get("x_rss_base_url", "https://nitter.net"))).rstrip("/")
    sources: list[Source] = []
    for row in cfg.get("sources", []) or []:
        if not include_disabled and not bool(row.get("enabled", True)):
            continue
        url = str(row.get("url", ""))
        typ = str(row.get("type", "rss"))
        if typ == "rsshub" and url.startswith("/") and rsshub_base:
            url = rsshub_base + url
        elif typ == "rsshub" and url.startswith("/") and not rsshub_base:
            match = re.fullmatch(r"/twitter/user/([A-Za-z0-9_]+)", url)
            if str(row.get("id") or "").startswith("x_") and match and x_rss_base:
                # RSSHub's public X route is often unavailable.  A read-only
                # Nitter-compatible RSS mirror keeps discovery alive; item
                # links are canonicalized back to x.com by the feed parser.
                url = f"{x_rss_base}/{match.group(1)}/rss"
                typ = "rss"
            else:
                row = {**row, "enabled": False}
                if not include_disabled:
                    continue
        sources.append(
            Source(
                id=str(row["id"]),
                name=str(row.get("name", row["id"])),
                tier=str(row.get("tier", "D")).upper(),
                type=typ,
                region=str(row.get("region", "global")),
                url=url,
                enabled=bool(row.get("enabled", True)),
                reliability=str(row.get("reliability", "unknown")),
                topics=[str(x) for x in (row.get("topics", []) or [])],
                timeout_seconds=int(row.get("timeout_seconds", timeout)),
            )
        )
    return sources


def default_db_path() -> Path:
    return project_root() / "data" / "briefing.sqlite3"


def default_runs_dir() -> Path:
    return project_root() / "runs"

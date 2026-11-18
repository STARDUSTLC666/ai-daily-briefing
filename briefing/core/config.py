from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .models import Source


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _strip_yaml_comments(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        if line.strip().startswith("#"):
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
        return [x.strip().strip("\"'") for x in inner.split(",")]
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        return value


def _parse_minimal_yaml(text: str) -> dict[str, Any]:
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
                key, val = rest.split(":", 1)
                current[key.strip()] = _coerce_scalar(val.strip())
            continue
        if section == "sources" and current is not None and ":" in raw:
            key, val = raw.strip().split(":", 1)
            current[key.strip()] = _coerce_scalar(val.strip())
            continue
        if section and section != "sources" and isinstance(data.get(section), dict) and ":" in raw:
            key, val = raw.strip().split(":", 1)
            data[section][key.strip()] = _coerce_scalar(val.strip())
    return data


def load_config(path: Path | None = None, *, _parents: frozenset[Path] = frozenset()) -> dict[str, Any]:
    resolved = (path or (project_root() / "sources.yaml")).resolve()
    if resolved in _parents:
        raise ValueError(f"circular configuration inheritance: {resolved.name}")
    text = resolved.read_text(encoding="utf-8")
    try:
        parsed = json.loads(_strip_yaml_comments(text))
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore

            parsed = yaml.safe_load(text)
        except Exception:
            parsed = _parse_minimal_yaml(text)
    if not isinstance(parsed, dict):
        raise ValueError(f"configuration must be an object: {resolved.name}")
    if parsed.get("extends"):
        base = load_config(resolved.parent / str(parsed["extends"]), _parents=_parents | {resolved})
        parsed = {**base, **parsed, "defaults": {**base.get("defaults", {}), **parsed.get("defaults", {})}}
    source_filter = parsed.get("source_filter") or {}
    excluded_types = set(source_filter.get("exclude_types") or [])
    excluded_prefixes = tuple(source_filter.get("exclude_id_prefixes") or [])
    if excluded_types or excluded_prefixes:
        parsed["sources"] = [
            {**row, "enabled": False}
            if row.get("type") in excluded_types or str(row.get("id", "")).startswith(excluded_prefixes)
            else row
            for row in parsed.get("sources", [])
        ]
    return parsed


def effective_config_sha256(cfg: dict[str, Any] | Path | None = None) -> str:
    payload = cfg if isinstance(cfg, dict) else load_config(cfg)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_sources(path: Path | None = None, include_disabled: bool = False) -> list[Source]:
    cfg = load_config(path)
    defaults = cfg.get("defaults", {}) or {}
    timeout = int(defaults.get("timeout_seconds", 18))
    sources: list[Source] = []
    for row in cfg.get("sources", []) or []:
        enabled = bool(row.get("enabled", True))
        typ = str(row.get("type", "rss"))
        url = str(row.get("url", ""))
        # Relative RSSHub routes without an explicit base URL are unsupported; treat as disabled
        if typ in {"rsshub", "sitemap"} and url.startswith("/"):
            enabled = False
        if not include_disabled and not enabled:
            continue
        sources.append(
            Source(
                id=str(row["id"]),
                name=str(row.get("name", row["id"])),
                tier=str(row.get("tier", "D")).upper(),
                type=typ,
                region=str(row.get("region", "global")),
                url=url,
                enabled=enabled,
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

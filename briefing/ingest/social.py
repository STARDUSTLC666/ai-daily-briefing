from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..core import (
    UTC,
    canonical_url,
    clean_text,
    load_config,
    now_utc,
    parse_date,
    parse_x_status,
    project_root,
    write_json_atomic,
)


def _social_lanes(config_path: Path | None = None) -> dict[str, list[str]]:
    cfg = load_config(config_path)
    lanes: dict[str, list[str]] = {}
    for row in cfg.get("sources", []) or []:
        if not isinstance(row, dict) or row.get("type") != "agent_social" or not row.get("enabled", True):
            continue
        source_id = str(row.get("id") or "").strip()
        _, _, fragment = str(row.get("url") or "").partition("#")
        accounts = list(dict.fromkeys(part.strip().lstrip("@") for part in fragment.split(",") if part.strip()))
        accounts = [acc for acc in accounts if re.fullmatch(r"[A-Za-z0-9_]{1,15}", acc)]
        if source_id and accounts:
            lanes[source_id] = accounts
    return lanes


def _opencli_executable() -> str:
    executable = shutil.which("opencli.cmd") or shutil.which("opencli")
    if not executable:
        raise FileNotFoundError("opencli was not found on PATH; install @jackwener/opencli first")
    return executable


def fetch_account_tweets(
    account: str,
    *,
    profile: str = "daily-briefing",
    limit: int = 8,
    timeout_seconds: int = 90,
) -> list[dict[str, Any]]:
    command = [
        _opencli_executable(),
        "--profile",
        profile,
        "twitter",
        "tweets",
        account,
        "--limit",
        str(max(1, int(limit))),
        "--page-delay",
        "0",
        "--site-session",
        "persistent",
        "--window",
        "background",
        "-f",
        "json",
    ]
    completed = subprocess.run(
        command,
        cwd=str(project_root()),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(15, int(timeout_seconds)),
        check=False,
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or f"exit {completed.returncode}").strip()
        if re.search(r"(?im)^\s*code:\s*EMPTY_RESULT\s*$", message):
            return []
        raise RuntimeError(message[:500])
    payload = json.loads(completed.stdout or "[]")
    if not isinstance(payload, list):
        raise ValueError("opencli twitter tweets returned a non-list JSON payload")
    return [row for row in payload if isinstance(row, dict)]


def _normalize_account_tweets(
    source_id: str,
    account: str,
    rows: list[dict[str, Any]],
    *,
    now: datetime,
    lookback_hours: int,
) -> list[dict[str, Any]]:
    ref_now = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
    cutoff = ref_now - timedelta(hours=max(1, int(lookback_hours)))
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        text = clean_text(str(row.get("text") or ""))
        published_at = parse_date(str(row.get("created_at") or ""))
        raw_url = str(row.get("url") or "").strip()
        parsed_status = parse_x_status(raw_url)
        if not text or published_at is None or parsed_status is None:
            continue
        if published_at < cutoff or published_at > ref_now + timedelta(minutes=5):
            continue
        handle_lower, status_id = parsed_status
        if handle_lower != account.lower() or status_id in seen:
            continue
        seen.add(status_id)
        media_urls = [
            str(val).strip()
            for val in (row.get("media_urls") or [])
            if str(val).strip().startswith(("https://", "http://"))
        ]
        normalized.append(
            {
                "source_id": source_id,
                "account": account,
                "status_id": status_id,
                "url": canonical_url(raw_url),
                "text": text,
                "published_at": published_at.isoformat(),
                "engagement": {
                    key: int(row.get(key) or 0)
                    for key in ("likes", "retweets", "replies", "views")
                    if str(row.get(key) or "").strip().lstrip("-").isdigit()
                },
                "media_urls": media_urls,
            }
        )
    normalized.sort(key=lambda item: str(item["published_at"]), reverse=True)
    return normalized


def collect_x_candidates(
    config_path: Path | None = None,
    *,
    out_path: Path | None = None,
    limit_per_account: int = 8,
    profile: str = "daily-briefing",
    lookback_hours: int = 24,
    timeout_seconds: int = 90,
    now: datetime | None = None,
    fetcher: Callable[..., list[dict[str, Any]]] | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Collect whitelisted X discovery candidates via OpenCLI."""
    generated_at = (now or now_utc()).astimezone(UTC) if (now and now.tzinfo) else (now.replace(tzinfo=UTC) if now else now_utc())
    lane_config = _social_lanes(config_path)
    active_fetcher = fetcher or fetch_account_tweets
    lane_results: dict[str, Any] = {}
    all_items: list[dict[str, Any]] = []
    global_error = ""

    def emit(msg: str) -> None:
        if progress is not None:
            progress(msg)

    for source_id, accounts in lane_config.items():
        account_results: list[dict[str, Any]] = []
        lane_items: list[dict[str, Any]] = []
        for account in accounts:
            if global_error:
                account_results.append({"account": account, "status": "error", "error": global_error, "candidates": 0})
                emit(f"skip source={source_id} account={account} reason=browser_unavailable")
                continue
            emit(f"check source={source_id} account={account}")
            try:
                rows = active_fetcher(
                    account,
                    profile=profile,
                    limit=limit_per_account,
                    timeout_seconds=timeout_seconds,
                )
                candidates = _normalize_account_tweets(
                    source_id,
                    account,
                    rows,
                    now=generated_at,
                    lookback_hours=lookback_hours,
                )
                account_results.append({"account": account, "status": "checked", "candidates": len(candidates)})
                lane_items.extend(candidates)
                emit(f"checked source={source_id} account={account} candidates={len(candidates)}")
            except Exception as exc:
                error = str(exc)[:300]
                account_results.append({"account": account, "status": "error", "error": error, "candidates": 0})
                emit(f"failed source={source_id} account={account} error={type(exc).__name__}")
                if re.search(r"BROWSER_CONNECT|browser profile .*not connected", error, flags=re.I):
                    global_error = error
        lane_items.sort(key=lambda item: str(item["published_at"]), reverse=True)
        lane_results[source_id] = {
            "accounts_expected": accounts,
            "accounts_checked": [row["account"] for row in account_results if row["status"] == "checked"],
            "accounts_failed": [row["account"] for row in account_results if row["status"] != "checked"],
            "account_results": account_results,
            "candidate_count": len(lane_items),
        }
        all_items.extend(lane_items)

    all_items.sort(key=lambda item: str(item["published_at"]), reverse=True)
    payload: dict[str, Any] = {
        "version": 1,
        "producer": "opencli",
        "generated_at": generated_at.isoformat(),
        "lookback_hours": lookback_hours,
        "profile_alias": profile,
        "notice": "Discovery candidates only. Codex must open and verify each status before writing codex-social-leads.json.",
        "lanes": lane_results,
        "items": all_items,
    }
    target_out = out_path or (project_root() / "data" / "opencli-x-candidates.json" if config_path is None else None)
    if target_out is not None:
        write_json_atomic(target_out, payload)
    return payload

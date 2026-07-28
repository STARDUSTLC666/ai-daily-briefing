from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from .config import load_config


UTC = timezone.utc
STATUS_URL_RE = re.compile(
    r"^https://(?:www\.)?(?:x\.com|twitter\.com)/([A-Za-z0-9_]+)/status/(\d+)(?:[/?#].*)?$",
    flags=re.IGNORECASE,
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def parse_x_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return _utc(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        pass
    try:
        return _utc(parsedate_to_datetime(text))
    except (TypeError, ValueError, OverflowError):
        return None


def social_lanes(config_path: Path) -> dict[str, list[str]]:
    config = load_config(config_path)
    lanes: dict[str, list[str]] = {}
    for row in config.get("sources", []) or []:
        if not isinstance(row, dict) or row.get("type") != "agent_social" or not row.get("enabled", True):
            continue
        source_id = str(row.get("id") or "").strip()
        _, _, fragment = str(row.get("url") or "").partition("#")
        accounts = list(dict.fromkeys(part.strip().lstrip("@") for part in fragment.split(",") if part.strip()))
        # X handles only: a config value starting with "-" would otherwise be consumed
        # as an option by the opencli argv on the unattended host.
        accounts = [account for account in accounts if re.fullmatch(r"[A-Za-z0-9_]{1,15}", account)]
        if source_id and accounts:
            lanes[source_id] = accounts
    return lanes


def normalize_account_tweets(
    source_id: str,
    account: str,
    rows: list[dict[str, Any]],
    *,
    now: datetime,
    lookback_hours: int,
) -> list[dict[str, Any]]:
    now = _utc(now)
    cutoff = now - timedelta(hours=max(1, lookback_hours))
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        text = " ".join(str(row.get("text") or "").split()).strip()
        published_at = parse_x_datetime(row.get("created_at"))
        url = str(row.get("url") or "").strip()
        match = STATUS_URL_RE.match(url)
        if not text or published_at is None or not match:
            continue
        if published_at < cutoff or published_at > now + timedelta(minutes=5):
            continue
        status_account, status_id = match.groups()
        # The per-account command can include retweets whose canonical status
        # belongs to somebody else. Keep the candidate lane identity strict;
        # Codex can discover the original author through its own whitelisted lane.
        if status_account.casefold() != account.casefold() or status_id in seen:
            continue
        seen.add(status_id)
        media_urls = [
            str(value).strip()
            for value in (row.get("media_urls") or [])
            if str(value).strip().startswith(("https://", "http://"))
        ]
        normalized.append(
            {
                "source_id": source_id,
                "account": account,
                "status_id": status_id,
                "url": f"https://x.com/{status_account}/status/{status_id}",
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


def _opencli_executable() -> str:
    executable = shutil.which("opencli.cmd") or shutil.which("opencli")
    if not executable:
        raise FileNotFoundError("opencli was not found on PATH; install @jackwener/opencli first")
    return executable


def fetch_account_tweets(account: str, *, profile: str, limit: int, timeout_seconds: int) -> list[dict[str, Any]]:
    command = [
        _opencli_executable(),
        "--profile",
        profile,
        "twitter",
        "tweets",
        account,
        "--limit",
        str(max(1, limit)),
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
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(15, timeout_seconds),
        check=False,
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or f"exit {completed.returncode}").strip()
        if re.search(r"(?im)^\s*code:\s*EMPTY_RESULT\s*$", message):
            return []
        # OpenCLI errors should never contain cookies, but cap the diagnostic so
        # unattended logs cannot accidentally retain a large browser payload.
        raise RuntimeError(message[:500])
    payload = json.loads(completed.stdout or "[]")
    if not isinstance(payload, list):
        raise ValueError("opencli twitter tweets returned a non-list JSON payload")
    return [row for row in payload if isinstance(row, dict)]


def collect_x_candidates(
    *,
    config_path: Path,
    output_path: Path,
    profile: str = "daily-briefing",
    lookback_hours: int = 24,
    per_account_limit: int = 8,
    timeout_seconds: int = 90,
    now: datetime | None = None,
    fetcher: Callable[..., list[dict[str, Any]]] = fetch_account_tweets,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    generated_at = _utc(now or datetime.now(UTC))
    lane_config = social_lanes(config_path)
    lane_results: dict[str, Any] = {}
    all_items: list[dict[str, Any]] = []
    global_error = ""

    def emit(message: str) -> None:
        if progress is not None:
            progress(message)

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
                rows = fetcher(
                    account,
                    profile=profile,
                    limit=per_account_limit,
                    timeout_seconds=timeout_seconds,
                )
                candidates = normalize_account_tweets(
                    source_id,
                    account,
                    rows,
                    now=generated_at,
                    lookback_hours=lookback_hours,
                )
                account_results.append({"account": account, "status": "checked", "candidates": len(candidates)})
                lane_items.extend(candidates)
                emit(f"checked source={source_id} account={account} candidates={len(candidates)}")
            except Exception as exc:  # one blocked account must not erase other lanes
                error = str(exc)[:300]
                account_results.append({"account": account, "status": "error", "error": error, "candidates": 0})
                emit(f"failed source={source_id} account={account} error={type(exc).__name__}")
                if re.search(r"BROWSER_CONNECT|browser profile .*not connected", error, flags=re.IGNORECASE):
                    # Browser Bridge availability is global for the selected
                    # profile. Repeating the same 30-90 second failure for all
                    # remaining accounts only hides the real fault and can
                    # consume most of the one-hour production window.
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
    payload = {
        "version": 1,
        "producer": "opencli",
        "generated_at": generated_at.isoformat(),
        "lookback_hours": lookback_hours,
        "profile_alias": profile,
        "notice": "Discovery candidates only. Codex must open and verify each status before writing codex-social-leads.json.",
        "lanes": lane_results,
        "items": all_items,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(output_path)
    return payload

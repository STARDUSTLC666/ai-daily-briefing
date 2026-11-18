from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

_EXECUTABLE_NAMES: tuple[str, ...] = (
    "google-chrome",
    "google-chrome-stable",
    "chrome",
    "chrome.exe",
    "msedge",
    "msedge.exe",
    "microsoft-edge",
    "chromium",
    "chromium-browser",
)


def _standard_browser_paths() -> list[Path]:
    if sys.platform == "win32":
        candidates = [
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        ]
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        if local_app_data:
            base = Path(local_app_data)
            candidates.extend(
                [
                    base / "Google" / "Chrome" / "Application" / "chrome.exe",
                    base / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                ]
            )
        return candidates
    if sys.platform == "darwin":
        return [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
        ]
    return [
        Path("/opt/google/chrome/chrome"),
        Path("/usr/bin/google-chrome"),
        Path("/usr/bin/google-chrome-stable"),
        Path("/opt/microsoft/msedge/msedge"),
        Path("/usr/bin/microsoft-edge"),
        Path("/usr/bin/chromium"),
        Path("/usr/bin/chromium-browser"),
    ]


def find_browser_executable() -> Path | None:
    """Discover a local Chrome, Edge, or Chromium executable."""
    for env_key in ("BRIEFING_BROWSER_EXE", "BRIEFING_CHROME", "CHROME"):
        configured = os.environ.get(env_key, "").strip()
        if configured:
            candidate = Path(configured).expanduser()
            if candidate.is_file():
                return candidate

    for candidate in _standard_browser_paths():
        if candidate.is_file():
            return candidate

    for name in _EXECUTABLE_NAMES:
        resolved = shutil.which(name)
        if resolved:
            candidate = Path(resolved)
            if candidate.is_file():
                return candidate

    return None


def browser_channel(executable: Path | None = None) -> str:
    """Return a Playwright/Crawl4AI channel hint ('chrome', 'msedge', or 'chromium')."""
    explicit = os.environ.get("BRIEFING_CRAWL_CHANNEL", "").strip().lower()
    if explicit in {"chrome", "msedge", "chromium"}:
        return explicit
    exe = executable or find_browser_executable()
    if exe is not None:
        lowered = exe.name.lower()
        if "edge" in lowered or "msedge" in lowered:
            return "msedge"
        if "chrome" in lowered and "chromium" not in lowered:
            return "chrome"
    return "chromium"


def load_sync_playwright() -> tuple[Any, type[Exception]]:
    """Import `(sync_playwright, TimeoutError)` from either `playwright` or `patchright`."""
    try:
        from playwright.sync_api import TimeoutError as PwTimeoutError
        from playwright.sync_api import sync_playwright

        return sync_playwright, PwTimeoutError
    except ImportError:
        from patchright.sync_api import TimeoutError as PrTimeoutError
        from patchright.sync_api import sync_playwright

        return sync_playwright, PrTimeoutError

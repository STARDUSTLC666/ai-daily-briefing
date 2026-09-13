"""Reuse a standard installed browser for Crawl4AI without a second download."""
from __future__ import annotations

import os
from pathlib import Path
import sys


def installed_crawl_channel() -> str | None:
    paths = {
        'chrome': [
            r'C:\Program Files\Google\Chrome\Application\chrome.exe',
            r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
            str(Path(os.environ.get('LOCALAPPDATA', '')) / 'Google/Chrome/Application/chrome.exe'),
        ],
        'msedge': [
            r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
            r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
        ],
    } if sys.platform == 'win32' else {
        'chrome': ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '/opt/google/chrome/chrome'],
        'msedge': ['/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge', '/opt/microsoft/msedge/msedge'],
    }
    return next((channel for channel, candidates in paths.items() if any(Path(path).is_file() for path in candidates)), None)


def crawl_channel() -> str:
    explicit = os.environ.get('BRIEFING_CRAWL_CHANNEL', '').strip()
    if explicit:
        if explicit not in {'chrome', 'msedge', 'chromium'}:
            raise ValueError('BRIEFING_CRAWL_CHANNEL must be chrome, msedge, or chromium')
        return explicit
    return installed_crawl_channel() or 'chromium'

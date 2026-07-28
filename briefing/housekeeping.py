from __future__ import annotations

"""Run-directory retention for long-running scheduled deployments.

A daily 1080p edition leaves ~60MB of reproducible artifacts behind; a few
months of unattended operation quietly fills the disk. Pruning removes only
heavy, regenerable media from old runs. Manifests, scripts, covers, audit
files and evidence screenshots are never touched: they are the run's audit
trail and stay forever.
"""

from datetime import datetime, timedelta
from pathlib import Path
import re
import shutil
from typing import Any

RUN_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Heavy and reproducible only. final.mp4 is already on Bilibili for published
# runs and can be re-rendered from the frozen script for unpublished ones.
PRUNABLE_FILES = ("final.mp4",)
PRUNABLE_DIRS = ("render", "source-assets")


def _tree_size(path: Path) -> int:
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            try:
                total += child.stat().st_size
            except OSError:
                continue
    return total


def prune_runs(
    runs_dir: Path,
    keep_days: int,
    *,
    execute: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Remove heavy media from run directories older than ``keep_days``.

    Dry-run by default: without ``execute`` the report only states what would
    be reclaimed. Directory names that are not plain run dates (diagnostic
    trees like ``_crawl4ai-debug`` or ``design-system-qa``) are never touched.
    """
    runs_dir = Path(runs_dir)
    keep_days = max(1, int(keep_days))
    current = now or datetime.now()
    cutoff = (current - timedelta(days=keep_days)).strftime("%Y-%m-%d")
    report: dict[str, Any] = {
        "runs_dir": str(runs_dir),
        "keep_days": keep_days,
        "cutoff": cutoff,
        "execute": bool(execute),
        "pruned": [],
        "kept": [],
        "reclaimed_bytes": 0,
        "errors": [],
    }
    if not runs_dir.exists():
        return report
    for entry in sorted(runs_dir.iterdir()):
        if not entry.is_dir() or not RUN_DIR_RE.match(entry.name):
            continue
        if entry.name >= cutoff:
            report["kept"].append(entry.name)
            continue
        removed_bytes = 0
        removed_names: list[str] = []
        targets = [entry / name for name in PRUNABLE_FILES] + [entry / name for name in PRUNABLE_DIRS]
        for target in targets:
            if not target.exists():
                continue
            size = _tree_size(target)
            if execute:
                try:
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                except OSError as exc:
                    report["errors"].append(f"{target}: {exc}")
                    continue
            removed_bytes += size
            removed_names.append(target.name)
        if removed_names:
            report["pruned"].append({"run": entry.name, "targets": removed_names, "bytes": removed_bytes})
            report["reclaimed_bytes"] += removed_bytes
        else:
            report["kept"].append(entry.name)
    return report

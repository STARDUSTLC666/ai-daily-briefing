from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import StageRecord

UTC = timezone.utc
RUN_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PRUNABLE_FILES = ("final.mp4", "final-vertical.mp4")
PRUNABLE_DIRS = ("render", "source-assets")


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        if default is not None:
            return default
        raise


def write_text_atomic(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
    return path


def write_json_atomic(path: Path, payload: Any) -> Path:
    return write_text_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class PipelineLock:
    """Cross-process lock with PID-liveness recovery (never evicts live 4K renders on age alone)."""

    def __init__(self, path: Path, *, stale_after_seconds: int = 6 * 3600) -> None:
        self.path = Path(path)
        self.stale_after_seconds = stale_after_seconds
        self.acquired = False

    def __enter__(self) -> PipelineLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(2):
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                payload = {
                    "pid": os.getpid(),
                    "created_at": datetime.now(UTC).isoformat(),
                    "monotonic": time.monotonic(),
                }
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, indent=2)
                self.acquired = True
                return self
            except FileExistsError:
                parsed = False
                try:
                    payload = json.loads(self.path.read_text(encoding="utf-8-sig"))
                    pid = int(payload.get("pid") or 0)
                    age = max(0.0, time.time() - self.path.stat().st_mtime)
                    parsed = pid > 0
                except Exception:
                    pid = 0
                    age = max(0.0, time.time() - self.path.stat().st_mtime)
                dead_owner = parsed and not pid_alive(pid)
                abandoned_malformed = not parsed and age > min(30, self.stale_after_seconds)
                if attempt == 0 and (dead_owner or abandoned_malformed):
                    self.path.unlink(missing_ok=True)
                    continue
                raise RuntimeError(
                    f"briefing run is already active for this date (pid={pid}, lock={self.path})"
                ) from None
        raise RuntimeError(f"could not acquire briefing run lock: {self.path}")

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        if self.acquired:
            try:
                payload = read_json(self.path, default={})
                if int(payload.get("pid") or 0) == os.getpid():
                    self.path.unlink(missing_ok=True)
            finally:
                self.acquired = False


class RunState:
    def __init__(
        self,
        path: Path,
        run_id: str,
        run_date: str,
        *,
        status: str = "RUNNING",
        started_at: str = "",
        finished_at: str = "",
        stages: list[StageRecord] | None = None,
    ) -> None:
        self.path = path
        self.run_id = run_id
        self.run_date = run_date
        self.status = status
        self.started_at = started_at or datetime.now(UTC).isoformat()
        self.finished_at = finished_at
        self.stages: list[StageRecord] = list(stages or [])
        self._active: dict[str, datetime] = {}
        self.write()

    @classmethod
    def load_or_create(cls, path: Path, run_id: str, run_date: str) -> RunState:
        existing = read_json(path, default=None)
        if isinstance(existing, dict) and existing.get("run_id"):
            stages = [StageRecord(**row) for row in existing.get("stages", []) if isinstance(row, dict)]
            return cls(
                path=path,
                run_id=str(existing["run_id"]),
                run_date=str(existing.get("run_date") or run_date),
                status=str(existing.get("status") or "RUNNING"),
                started_at=str(existing.get("started_at") or ""),
                finished_at=str(existing.get("finished_at") or ""),
                stages=stages,
            )
        return cls(path=path, run_id=run_id, run_date=run_date)

    def start(self, name: str, *, input_count: int | None = None, fingerprint_input: Any = None) -> None:
        now = datetime.now(UTC)
        fingerprint = ""
        if fingerprint_input is not None:
            encoded = json.dumps(fingerprint_input, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
            fingerprint = hashlib.sha256(encoded).hexdigest()[:20]
        self._active[name] = now
        self.status = "RUNNING"
        self.stages.append(
            StageRecord(
                name=name,
                status="RUNNING",
                started_at=now.isoformat(),
                input_count=input_count,
                fingerprint=fingerprint,
            )
        )
        self.write()

    def progress(self, name: str, metrics: dict[str, Any]) -> None:
        record = next((s for s in reversed(self.stages) if s.name == name and s.status == "RUNNING"), None)
        if record is None:
            return
        record.metrics = dict(metrics)
        self.write()

    def finish(self, name: str, *, output_count: int | None = None, metrics: dict[str, Any] | None = None) -> None:
        now = datetime.now(UTC)
        record = next((s for s in reversed(self.stages) if s.name == name and s.status == "RUNNING"), None)
        if record is None:
            return
        started = self._active.pop(name, datetime.fromisoformat(record.started_at))
        record.status = "OK"
        record.finished_at = now.isoformat()
        record.duration_ms = max(0, round((now - started).total_seconds() * 1000))
        record.output_count = output_count
        if metrics is not None:
            record.metrics = {**record.metrics, **metrics}
        self.write()

    def fail(self, name: str, error: Exception | str) -> None:
        now = datetime.now(UTC)
        record = next((s for s in reversed(self.stages) if s.name == name and s.status == "RUNNING"), None)
        if record is None:
            self.start(name)
            record = self.stages[-1]
        started = self._active.pop(name, datetime.fromisoformat(record.started_at))
        record.status = "FAILED"
        record.finished_at = now.isoformat()
        record.duration_ms = max(0, round((now - started).total_seconds() * 1000))
        record.error = f"{type(error).__name__}: {error}" if isinstance(error, Exception) else str(error)
        self.status = "FAILED"
        self.finished_at = now.isoformat()
        self.write()

    def complete(self, status: str = "QA_PASSED") -> None:
        self.status = status
        self.finished_at = datetime.now(UTC).isoformat()
        self.write()

    def payload(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_date": self.run_date,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "stages": [asdict(stage) for stage in self.stages],
        }

    def write(self) -> None:
        write_json_atomic(self.path, self.payload())


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

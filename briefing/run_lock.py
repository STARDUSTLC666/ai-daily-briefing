from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

UTC = timezone.utc


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # On Windows ``os.kill(pid, 0)`` is not a harmless probe: CPython maps
        # it to TerminateProcess.  Query the process handle without obtaining
        # terminate rights so stale-lock recovery can never kill a live run.
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
    """Small cross-process date lock with conservative stale recovery."""

    def __init__(self, path: Path, *, stale_after_seconds: int = 6 * 3600) -> None:
        self.path = Path(path)
        self.stale_after_seconds = stale_after_seconds
        self.acquired = False

    def __enter__(self) -> "PipelineLock":
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
                # Age is not evidence of death: a long 4K render can exceed the
                # stale timeout. Never allow a second producer beside a live one.
                dead_owner = parsed and not _pid_alive(pid)
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
                payload = json.loads(self.path.read_text(encoding="utf-8-sig"))
                if int(payload.get("pid") or 0) == os.getpid():
                    self.path.unlink(missing_ok=True)
            finally:
                self.acquired = False

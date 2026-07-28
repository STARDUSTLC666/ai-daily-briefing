from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any

UTC = timezone.utc


@dataclass(slots=True)
class StageRecord:
    name: str
    status: str
    started_at: str
    finished_at: str = ""
    duration_ms: int = 0
    input_count: int | None = None
    output_count: int | None = None
    fingerprint: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    error: str = ""


class RunState:
    def __init__(self, path: Path, run_id: str, run_date: str) -> None:
        self.path = path
        self.run_id = run_id
        self.run_date = run_date
        self.status = "RUNNING"
        self.started_at = datetime.now(UTC).isoformat()
        self.finished_at = ""
        self.stages: list[StageRecord] = []
        self._active: dict[str, datetime] = {}
        self.write()

    def start(self, name: str, *, input_count: int | None = None, fingerprint_input: Any = None) -> None:
        now = datetime.now(UTC)
        fingerprint = ""
        if fingerprint_input is not None:
            encoded = json.dumps(fingerprint_input, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
            fingerprint = hashlib.sha256(encoded).hexdigest()[:20]
        self._active[name] = now
        self.stages.append(StageRecord(name=name, status="RUNNING", started_at=now.isoformat(), input_count=input_count, fingerprint=fingerprint))
        self.write()

    def progress(self, name: str, metrics: dict[str, Any]) -> None:
        record = next((stage for stage in reversed(self.stages) if stage.name == name and stage.status == "RUNNING"), None)
        if record is None:
            return
        record.metrics = dict(metrics)
        self.write()

    def finish(self, name: str, *, output_count: int | None = None, metrics: dict[str, Any] | None = None) -> None:
        now = datetime.now(UTC)
        record = next((stage for stage in reversed(self.stages) if stage.name == name and stage.status == "RUNNING"), None)
        if record is None:
            return
        started = self._active.pop(name, datetime.fromisoformat(record.started_at))
        record.status = "OK"
        record.finished_at = now.isoformat()
        record.duration_ms = max(0, round((now - started).total_seconds() * 1000))
        record.output_count = output_count
        record.metrics = metrics or {}
        self.write()

    def fail(self, name: str, error: Exception | str) -> None:
        now = datetime.now(UTC)
        record = next((stage for stage in reversed(self.stages) if stage.name == name and stage.status == "RUNNING"), None)
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

    def complete(self, status: str = "COMPLETED") -> None:
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
        # Atomic replace: run-state.json is rewritten on every stage transition and read
        # concurrently by the review app / publish preflight; a hard kill mid-write must
        # never leave truncated JSON behind.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.payload(), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

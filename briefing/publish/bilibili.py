from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timedelta, timezone
import hashlib
import hmac
import json
import mimetypes
import os
from pathlib import Path
import re
import time
from typing import Any
from urllib.parse import urlencode
import uuid

import requests

from briefing.core import file_sha256, pid_alive, read_json, write_json_atomic
from briefing.verify import verify_release_attestation, verify_run_dir

REQUIRED_ENV: tuple[str, ...] = ("BILI_CLIENT_ID", "BILI_CLIENT_SECRET", "BILI_ACCESS_TOKEN")
MEMBER_HOST = "https://member.bilibili.com"
UPOS_HOST = "https://openupos.bilivideo.com"
SINGLE_UPLOAD_LIMIT = 100 * 1024 * 1024
DEFAULT_CHUNK_BYTES = 10 * 1024 * 1024
DEFAULT_SCHEDULE_FIELD = "dtime"
MIN_SCHEDULE_LEAD_SECONDS = 10 * 60
SHANGHAI_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
BILI_DESC_LIMIT = 250

EXPECTED_PAYLOAD_PATHS: dict[str, str] = {
    "video": "final.mp4",
    "cover": "cover.png",
    "subtitle": "subtitles.srt",
    "pinned_comment": "pinned-comment.md",
}


class BilibiliError(RuntimeError):
    pass


@dataclass(slots=True)
class BiliCredentials:
    client_id: str
    client_secret: str
    access_token: str

    @classmethod
    def from_env(cls) -> BiliCredentials:
        missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
        if missing:
            raise BilibiliError("Missing Bilibili env vars: " + ", ".join(missing))
        return cls(
            client_id=os.environ["BILI_CLIENT_ID"],
            client_secret=os.environ["BILI_CLIENT_SECRET"],
            access_token=os.environ["BILI_ACCESS_TOKEN"],
        )


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _md5_hex(body: bytes) -> str:
    return hashlib.md5(body or b"").hexdigest()


def _schedule_field() -> str:
    return os.environ.get("BILI_SCHEDULE_FIELD", DEFAULT_SCHEDULE_FIELD).strip() or DEFAULT_SCHEDULE_FIELD


def _run_date_from_dir(run_dir: Path) -> date | None:
    try:
        return datetime.strptime(run_dir.name, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_schedule_at(
    schedule_at: str | int | None,
    run_dir: Path,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    if schedule_at is None:
        return None
    raw = str(schedule_at).strip()
    if not raw:
        return None

    if re.fullmatch(r"\d{10,}", raw):
        scheduled = datetime.fromtimestamp(int(raw), tz=SHANGHAI_TZ)
    elif re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", raw):
        parts = [int(part) for part in raw.split(":")]
        if len(parts) == 2:
            parts.append(0)
        base_date = _run_date_from_dir(run_dir) or (
            now.astimezone(SHANGHAI_TZ).date() if now else datetime.now(SHANGHAI_TZ).date()
        )
        scheduled = datetime.combine(base_date, datetime_time(parts[0], parts[1], parts[2]), tzinfo=SHANGHAI_TZ)
    else:
        normalized = raw.replace("T", " ")
        scheduled = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                scheduled = datetime.strptime(normalized, fmt).replace(tzinfo=SHANGHAI_TZ)
                break
            except ValueError:
                continue
        if scheduled is None:
            try:
                scheduled = datetime.fromisoformat(raw)
            except ValueError as exc:
                raise ValueError("schedule_at must be HH:MM, YYYY-MM-DD HH:MM, ISO datetime, or Unix timestamp") from exc
            scheduled = scheduled.replace(tzinfo=SHANGHAI_TZ) if scheduled.tzinfo is None else scheduled.astimezone(SHANGHAI_TZ)

    return {
        "input": raw,
        "field": _schedule_field(),
        "timestamp": int(scheduled.timestamp()),
        "local_time": scheduled.isoformat(timespec="seconds"),
        "min_lead_seconds": MIN_SCHEDULE_LEAD_SECONDS,
    }


def canonical_signing_string(headers: dict[str, str]) -> str:
    normalized = {k.lower(): str(v) for k, v in headers.items() if k.lower().startswith("x-bili-")}
    return "\n".join(f"{key}:{normalized[key]}" for key in sorted(normalized))


def sign_headers(
    creds: BiliCredentials,
    body: bytes = b"",
    *,
    timestamp: int | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    headers = {
        "x-bili-accesskeyid": creds.client_id,
        "x-bili-content-md5": _md5_hex(body),
        "x-bili-signature-method": "HMAC-SHA256",
        "x-bili-signature-nonce": nonce or str(uuid.uuid4()),
        "x-bili-signature-version": "2.0",
        "x-bili-timestamp": str(timestamp or int(time.time())),
    }
    signing = canonical_signing_string(headers)
    headers["Authorization"] = hmac.new(
        creds.client_secret.encode("utf-8"), signing.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    headers["access-token"] = creds.access_token
    return headers


def _check_api_response(resp: requests.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except Exception as exc:
        raise BilibiliError(f"Bilibili API returned non-json HTTP {resp.status_code}: {resp.text[:300]}") from exc
    if resp.status_code >= 400:
        raise BilibiliError(f"Bilibili HTTP {resp.status_code}: {data}")
    if data.get("code") not in {0, "0"}:
        raise BilibiliError(f"Bilibili API error: {data}")
    return data


class BilibiliClient:
    """Bilibili Open Platform v2 signed client with UPOS chunk retry and archive submission."""

    def __init__(self, creds: BiliCredentials | None = None, session: requests.Session | None = None) -> None:
        self.creds = creds or BiliCredentials.from_env()
        self.session = session or requests.Session()

    def signed_json(
        self,
        method: str,
        url: str,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = _json_bytes(payload or {}) if method.upper() != "GET" and payload is not None else b""
        headers = {
            "Accept": "application/json",
            "User-Agent": "ai-daily-briefing/0.1",
            **sign_headers(self.creds, body),
        }
        if body:
            headers["Content-Type"] = "application/json; charset=utf-8"
        resp = self.session.request(
            method.upper(),
            url,
            params=params,
            data=body if body else None,
            headers=headers,
            timeout=60,
        )
        return _check_api_response(resp)

    def list_archive_types(self) -> dict[str, Any]:
        return self.signed_json("GET", f"{MEMBER_HOST}/arcopen/fn/archive/type/list")

    def init_video_upload(self, video_path: Path, multipart: bool) -> str:
        payload = {"name": video_path.name, "utype": "0" if multipart else "1"}
        data = self.signed_json("POST", f"{MEMBER_HOST}/arcopen/fn/archive/video/init", payload=payload)
        token = (data.get("data") or {}).get("upload_token")
        if not token:
            raise BilibiliError(f"video init did not return upload_token: {data}")
        return str(token)

    def _post_with_retry(self, url: str, data_factory: Any, *, attempts: int = 3) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                data = data_factory() if callable(data_factory) else data_factory
                try:
                    resp = self.session.post(
                        url,
                        data=data,
                        headers={"Content-Type": "application/octet-stream"},
                        timeout=600,
                    )
                finally:
                    close = getattr(data, "close", None)
                    if callable(close):
                        close()
                _check_api_response(resp)
                return resp
            except (requests.RequestException, BilibiliError) as exc:
                last_error = exc
                if attempt < attempts:
                    time.sleep(min(30.0, 2.0 * (2 ** (attempt - 1))))
        raise BilibiliError(f"upload request failed after {attempts} attempts: {last_error}") from last_error

    def upload_single_video(self, video_path: Path, upload_token: str) -> None:
        url = f"{UPOS_HOST}/video/v2/upload?{urlencode({'upload_token': upload_token})}"
        self._post_with_retry(url, lambda: video_path.open("rb"))

    def upload_video_parts(self, video_path: Path, upload_token: str, chunk_bytes: int = DEFAULT_CHUNK_BYTES) -> int:
        part_number = 1
        with video_path.open("rb") as fh:
            while True:
                chunk = fh.read(chunk_bytes)
                if not chunk:
                    break
                params = {"upload_token": upload_token, "part_number": part_number}
                url = f"{UPOS_HOST}/video/v2/part/upload?{urlencode(params)}"
                self._post_with_retry(url, chunk)
                part_number += 1
        return part_number - 1

    def complete_video_parts(self, upload_token: str) -> dict[str, Any]:
        return self.signed_json(
            "POST",
            f"{MEMBER_HOST}/arcopen/fn/archive/video/complete",
            payload={},
            params={"upload_token": upload_token},
        )

    def upload_cover(self, cover_path: Path) -> str:
        mime = mimetypes.guess_type(cover_path.name)[0] or "application/octet-stream"
        url = f"{MEMBER_HOST}/arcopen/fn/archive/cover/upload"
        req = requests.Request("POST", url, files={"file": (cover_path.name, cover_path.read_bytes(), mime)})
        prepped = self.session.prepare_request(req)
        body = prepped.body if isinstance(prepped.body, bytes) else (prepped.body or b"")
        prepped.headers.update(sign_headers(self.creds, body))
        prepped.headers["Accept"] = "application/json"
        resp = self.session.send(prepped, timeout=120)
        data = _check_api_response(resp)
        cover_url = (data.get("data") or {}).get("url")
        if not cover_url:
            raise BilibiliError(f"cover upload did not return url: {data}")
        return str(cover_url)

    def submit_archive(
        self,
        upload_token: str,
        payload: dict[str, Any],
        cover_url: str | None = None,
        tid: int | None = None,
        schedule_at_ts: int | None = None,
        schedule_field: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "title": payload["title"],
            "tid": int(tid if tid is not None else payload["tid"]),
            "tag": payload["tag"],
            "desc": payload.get("desc", ""),
            "copyright": int(payload.get("copyright", 1)),
            "no_reprint": int(payload.get("no_reprint", 1)),
        }
        if cover_url:
            body["cover"] = cover_url
        if schedule_at_ts is not None:
            body[schedule_field or _schedule_field()] = int(schedule_at_ts)
        return self.signed_json(
            "POST",
            f"{MEMBER_HOST}/arcopen/fn/archive/add-by-utoken",
            payload=body,
            params={"upload_token": upload_token},
        )


def token_expiry_status(*, now: datetime | None = None) -> dict[str, Any]:
    issued_raw = os.environ.get("BILI_TOKEN_ISSUED_AT", "").strip()
    ttl_raw = os.environ.get("BILI_TOKEN_TTL_DAYS", "").strip()
    if not issued_raw or not ttl_raw:
        return {"known": False}
    try:
        issued = datetime.fromisoformat(issued_raw)
        ttl_days = float(ttl_raw)
    except ValueError:
        return {"known": False}
    if issued.tzinfo is None:
        issued = issued.replace(tzinfo=SHANGHAI_TZ)
    current = now or datetime.now(SHANGHAI_TZ)
    expires_at = issued + timedelta(days=ttl_days)
    days_left = (expires_at - current).total_seconds() / 86400.0
    return {
        "known": True,
        "issued_at": issued.isoformat(),
        "ttl_days": ttl_days,
        "expires_at": expires_at.isoformat(),
        "days_left": round(days_left, 2),
    }


def check_bilibili_auth(client: Any = None, *, now: datetime | None = None) -> dict[str, Any]:
    """Probe the Bilibili Open Platform token with a lightweight authenticated call."""
    expiry = token_expiry_status(now=now)
    if client is None:
        try:
            client = BilibiliClient(BiliCredentials.from_env())
        except BilibiliError as exc:
            return {"ok": False, "reason": "missing_credentials", "error": str(exc), "expiry": expiry}
    try:
        client.list_archive_types()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": "api_error", "error": f"{type(exc).__name__}: {exc}", "expiry": expiry}
    return {"ok": True, "expiry": expiry}


def _load_payload(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "bilibili.json"
    if not path.exists():
        raise FileNotFoundError(f"missing bilibili.json: {path}")
    loaded = read_json(path)
    if not isinstance(loaded, dict):
        raise ValueError("bilibili.json root must be an object")
    return loaded


def _payload_with_overrides(payload: dict[str, Any], tid: int | None = None) -> dict[str, Any]:
    merged = dict(payload)
    env_tid = os.environ.get("BILI_TID", "").strip()
    if tid is not None:
        merged["tid"] = tid
    elif env_tid:
        merged["tid"] = int(env_tid)
    return merged


def preflight_bilibili(
    run_dir: Path,
    *,
    tid: int | None = None,
    schedule_at: str | None = None,
    verified_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate QA report, manifest release provenance, and Bilibili submission metadata."""
    run_dir = Path(run_dir).resolve()
    errors: list[str] = []
    warnings: list[str] = []

    verification = verified_report if verified_report is not None else verify_run_dir(run_dir)
    if not verification.get("ok"):
        errors.extend(f"output verification failed: {msg}" for msg in verification.get("errors", []))

    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = read_json(manifest_path)
            if not isinstance(manifest, dict):
                raise ValueError("manifest.json root must be an object")
            pkg_quality = manifest.get("package_quality") or {}
            provenance = manifest.get("provenance") or {}
            if manifest.get("qa_fixture") is True:
                errors.append("QA fixture packages are never publishable")
            if manifest.get("publish_allowed") is not True:
                errors.append("manifest publish_allowed is false; the rolling 24h package cannot be published")
            if pkg_quality and not pkg_quality.get("ok"):
                errors.append("package quality failed: " + str(pkg_quality.get("reason") or "not enough publishable stories"))
            if manifest.get("run_id"):
                if not provenance.get("git_sha"):
                    errors.append("automatic package provenance is missing git_sha")
                if provenance.get("git_dirty") is not False:
                    release_errors = verify_release_attestation(run_dir)
                    if release_errors:
                        errors.append(
                            "automatic package was generated from a dirty Git worktree without a valid clean release attestation"
                        )
                        errors.extend(release_errors)
                elif manifest.get("release_attestation") or (run_dir / "release-attestation.json").exists():
                    errors.extend(verify_release_attestation(run_dir))
        except Exception as exc:
            errors.append(f"manifest quality parse failed: {exc}")

    raw_payload: dict[str, Any] = {}
    bili_json_path = run_dir / "bilibili.json"
    if bili_json_path.exists():
        try:
            raw_payload = _load_payload(run_dir)
        except Exception as exc:
            errors.append(f"bilibili.json parse failed: {exc}")
    else:
        errors.append(f"missing bilibili.json: {bili_json_path}")

    payload = _payload_with_overrides(raw_payload, tid=tid)
    schedule = None
    try:
        schedule = parse_schedule_at(schedule_at or payload.get("schedule_at"), run_dir)
    except ValueError as exc:
        errors.append(str(exc))

    title = str(payload.get("title", ""))
    desc = str(payload.get("desc", ""))
    tag = str(payload.get("tag", ""))
    if not title:
        errors.append("title is empty")
    if len(title) >= 80:
        errors.append(f"title length is {len(title)}, expected < 80")
    if len(desc) >= BILI_DESC_LIMIT:
        errors.append(f"desc length is {len(desc)}, expected < {BILI_DESC_LIMIT}; move timeline/details to pinned comment")
    if len(tag) >= 200:
        errors.append(f"tag length is {len(tag)}, expected < 200")
    if payload.get("copyright") not in {1, "1"}:
        warnings.append("copyright is not original(1)")

    video_size = 0
    for key, relative in EXPECTED_PAYLOAD_PATHS.items():
        value = payload.get(key)
        if not value:
            errors.append(f"{key} path missing")
            continue
        path = Path(str(value))
        expected = (run_dir / relative).resolve()
        try:
            actual = path.resolve()
        except OSError:
            actual = path.absolute()
        if actual != expected:
            errors.append(f"{key} must point to the attested run file: {expected}")
        if not path.exists():
            errors.append(f"{key} not found: {path}")
        elif path.stat().st_size == 0:
            errors.append(f"{key} is empty: {path}")
        elif key == "video":
            video_size = path.stat().st_size

    missing_env = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing_env:
        warnings.append("Bilibili Open Platform credentials not set: " + ", ".join(missing_env))
    else:
        expiry = token_expiry_status()
        if expiry.get("known") and float(expiry.get("days_left") or 0) <= 3:
            warnings.append(
                f"Bilibili access token expires in ~{expiry.get('days_left')} days; "
                "re-run scripts/configure_bilibili.ps1 with a fresh token"
            )

    if payload.get("tid") in {None, "", 0}:
        warnings.append("tid is not set; set BILI_TID or pass --tid after querying Bilibili archive types")
    if schedule:
        min_publish_ts = int((datetime.now(SHANGHAI_TZ) + timedelta(seconds=MIN_SCHEDULE_LEAD_SECONDS)).timestamp())
        if int(schedule["timestamp"]) <= min_publish_ts:
            errors.append(
                f"schedule_at must be at least {MIN_SCHEDULE_LEAD_SECONDS // 60} minutes in the future; "
                f"got {schedule['local_time']}"
            )

    return {
        "ok": not errors,
        "ready_to_upload": not errors and not missing_env and payload.get("tid") not in {None, "", 0},
        "run_dir": str(run_dir),
        "errors": errors,
        "warnings": warnings,
        "verification": verification,
        "payload": {
            "title": title,
            "title_length": len(title),
            "desc_length": len(desc),
            "tag_length": len(tag),
            "video": payload.get("video"),
            "video_size": video_size,
            "cover": payload.get("cover"),
            "tid": payload.get("tid"),
            "multipart": video_size > SINGLE_UPLOAD_LIMIT,
            "schedule": schedule,
        },
    }


def _upload_snapshot(run_dir: Path, payload: dict[str, Any]) -> dict[str, str]:
    return {
        "payload": hashlib.sha256(_json_bytes(payload)).hexdigest(),
        "bilibili_json": file_sha256(run_dir / "bilibili.json"),
        "video": file_sha256(Path(str(payload["video"]))),
        "cover": file_sha256(Path(str(payload["cover"]))),
    }


def _assert_upload_snapshot_unchanged(run_dir: Path, payload: dict[str, Any], snapshot: dict[str, str]) -> None:
    current = _upload_snapshot(run_dir, payload)
    changed = sorted(key for key, val in snapshot.items() if current.get(key) != val)
    if changed:
        raise BilibiliError("attested upload inputs changed during upload: " + ", ".join(changed))


def _assert_upload_snapshot_attested(run_dir: Path, snapshot: dict[str, str]) -> None:
    manifest = read_json(run_dir / "manifest.json", default={})
    attestation = (
        manifest.get("release_attestation")
        if isinstance(manifest, dict) and "release_attestation" in manifest
        else read_json(run_dir / "release-attestation.json", default={})
    )
    if not isinstance(attestation, dict):
        return
    files = attestation.get("files")
    if not isinstance(files, dict) or not files:
        return

    bindings = {
        "bilibili_json": ("bilibili.json", "bilibili_json"),
        "video": ("final.mp4", "video"),
        "cover": ("cover.png", "cover"),
    }
    mismatched: list[str] = []
    for snap_key, (rel_key, legacy_key) in bindings.items():
        expected = ""
        if isinstance(files.get(rel_key), str):
            expected = str(files[rel_key])
        elif isinstance(files.get(legacy_key), dict):
            expected = str(files[legacy_key].get("sha256") or "")
        if snapshot.get(snap_key) != expected:
            mismatched.append(snap_key)
    if mismatched:
        raise BilibiliError("upload snapshot does not match signed release attestation: " + ", ".join(sorted(mismatched)))


def _completed_upload_result(run_dir: Path) -> dict[str, Any] | None:
    path = run_dir / "bilibili-upload-result.json"
    if not path.exists():
        return None
    try:
        result = read_json(path)
    except Exception as exc:
        raise BilibiliError(f"existing upload result is unreadable; refuse duplicate submission: {exc}") from exc
    if isinstance(result, dict) and result.get("dry_run") is False:
        if result.get("submit_response") is not None:
            return result
        if str(result.get("status") or "") == "submitting":
            raise BilibiliError(
                "a previous publish attempt was interrupted during archive submission; "
                "check the Bilibili creator center for the video, then delete bilibili-upload-result.json to retry"
            )
    return None


def _acquire_upload_lock(run_dir: Path) -> Path:
    lock = run_dir / ".bilibili-upload.lock"
    for attempt in range(2):
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError as exc:
            try:
                payload = read_json(lock)
                pid = int(payload.get("pid") or 0) if isinstance(payload, dict) else 0
            except Exception:
                pid = 0
            try:
                lock_age = max(0.0, time.time() - lock.stat().st_mtime)
            except OSError:
                lock_age = 0.0
            dead_owner = pid > 0 and not pid_alive(pid)
            abandoned_unreadable = pid <= 0 and lock_age > 300
            if attempt == 0 and (dead_owner or abandoned_unreadable):
                lock.unlink(missing_ok=True)
                continue
            raise BilibiliError(f"upload is already in progress or has an unreadable lock: {lock}") from exc
    else:
        raise BilibiliError(f"could not acquire upload lock: {lock}")

    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"pid": os.getpid(), "started_at": datetime.now(SHANGHAI_TZ).isoformat()}, handle, ensure_ascii=False)
    return lock


def publish_bilibili(
    run_dir: Path,
    *,
    tid: int | None = None,
    schedule_at: str | None = None,
    dry_run: bool = True,
    force: bool = False,
    verified_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Upload and submit a verified briefing package to Bilibili (dry-run by default)."""
    run_dir = Path(run_dir).resolve()
    if dry_run:
        preflight = preflight_bilibili(
            run_dir,
            tid=tid,
            schedule_at=schedule_at,
            verified_report=verified_report,
        )
        schedule = preflight.get("payload", {}).get("schedule")
        submit_step = f"submit_archive(schedule_at={schedule['local_time']})" if schedule else "submit_archive"
        return {
            "dry_run": True,
            "preflight": preflight,
            "planned_steps": ["upload_cover", "init_video_upload", "upload_video", submit_step],
        }

    previous_result = _completed_upload_result(run_dir)
    if previous_result is not None and not force:
        raise BilibiliError(
            "this run already has a successful Bilibili submission; pass force=True only for an intentional resubmission"
        )

    lock = _acquire_upload_lock(run_dir)
    try:
        preflight = preflight_bilibili(
            run_dir,
            tid=tid,
            schedule_at=schedule_at,
            verified_report=verified_report,
        )
        if not preflight["ready_to_upload"]:
            raise BilibiliError("preflight is not ready for upload: " + json.dumps(preflight, ensure_ascii=False))
        schedule = preflight.get("payload", {}).get("schedule")

        previous_result = _completed_upload_result(run_dir)
        if previous_result is not None and not force:
            raise BilibiliError("this run already has a successful Bilibili submission")

        payload = _payload_with_overrides(_load_payload(run_dir), tid=tid)
        video_path = Path(str(payload["video"]))
        cover_path = Path(str(payload["cover"]))
        multipart = video_path.stat().st_size > SINGLE_UPLOAD_LIMIT
        chunk_bytes = int(os.environ.get("BILI_UPLOAD_CHUNK_BYTES", DEFAULT_CHUNK_BYTES))
        client = BilibiliClient()

        snapshot = _upload_snapshot(run_dir, payload)
        recheck = preflight_bilibili(run_dir, tid=tid, schedule_at=schedule_at)
        if not recheck["ready_to_upload"]:
            raise BilibiliError("locked preflight is not ready for upload: " + json.dumps(recheck, ensure_ascii=False))
        _assert_upload_snapshot_unchanged(run_dir, payload, snapshot)
        _assert_upload_snapshot_attested(run_dir, snapshot)

        cover_url = client.upload_cover(cover_path)
        upload_token = client.init_video_upload(video_path, multipart=multipart)
        if multipart:
            parts = client.upload_video_parts(video_path, upload_token, chunk_bytes=chunk_bytes)
            _assert_upload_snapshot_unchanged(run_dir, payload, snapshot)
            client.complete_video_parts(upload_token)
        else:
            parts = 1
            client.upload_single_video(video_path, upload_token)
            _assert_upload_snapshot_unchanged(run_dir, payload, snapshot)

        result_path = run_dir / "bilibili-upload-result.json"
        marker = {
            "dry_run": False,
            "status": "submitting",
            "cover_url": cover_url,
            "upload_token": upload_token,
            "multipart": multipart,
            "parts": parts,
            "schedule": schedule,
            "submit_response": None,
            "submitting_at": datetime.now(SHANGHAI_TZ).isoformat(),
        }
        write_json_atomic(result_path, marker)

        submit = client.submit_archive(
            upload_token,
            payload,
            cover_url=cover_url,
            tid=int(payload["tid"]),
            schedule_at_ts=int(schedule["timestamp"]) if schedule else None,
            schedule_field=str(schedule["field"]) if schedule else None,
        )

        result = {
            "dry_run": False,
            "status": "submitted",
            "cover_url": cover_url,
            "upload_token": upload_token,
            "multipart": multipart,
            "parts": parts,
            "schedule": schedule,
            "submit_response": submit,
        }
        write_json_atomic(result_path, result)
        return result
    finally:
        lock.unlink(missing_ok=True)

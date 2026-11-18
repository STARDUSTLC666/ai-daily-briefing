from __future__ import annotations

import base64
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse
import uuid

from briefing.core import ensure_dir, read_json
from .state import (
    create_review_package,
    load_cover_state,
    review_dir,
    review_state_path,
    save_cover_state,
    write_final_script_from_state,
)

WEB_DIR = Path(__file__).resolve().parent / "web"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _safe_upload_name(name: str) -> str:
    suffix = Path(name).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        suffix = ".png"
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "-", Path(name).stem).strip("-")[:50] or "image"
    return f"{uuid.uuid4().hex[:12]}-{stem}{suffix}"


def _media_url(path: Path) -> str:
    return "/media?path=" + quote(str(path), safe="")


class _ReviewRequestHandler(BaseHTTPRequestHandler):
    run_dir: Path
    auth_token: str = ""
    allow_remote: bool = False

    def _post_authorized(self) -> bool:
        token = str(self.headers.get("X-Review-Token") or "")
        if not self.auth_token or not hmac.compare_digest(token, self.auth_token):
            return False
        origin = str(self.headers.get("Origin") or "").strip()
        if origin and not self.allow_remote:
            host = (urlparse(origin).hostname or "").lower()
            if host not in LOOPBACK_HOSTS:
                return False
        return True

    def _send_bytes(self, data: bytes, *, status: int = 200, content_type: str = "application/octet-stream") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_text(self, text: str, *, status: int = 200, content_type: str = "text/plain; charset=utf-8") -> None:
        self._send_bytes(text.encode("utf-8"), status=status, content_type=content_type)

    def _send_json(self, payload: Any, *, status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self._send_bytes(raw, status=status, content_type="application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path in {"/", "/index.html"}:
                template = (WEB_DIR / "index.html").read_text(encoding="utf-8")
                injected = template.replace(
                    "</head>",
                    f"  <script>window.__REVIEW_TOKEN__ = {json.dumps(self.auth_token)};</script>\n</head>",
                    1,
                )
                self._send_text(injected, content_type="text/html; charset=utf-8")
                return

            if parsed.path == "/assets/styles.css":
                css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")
                self._send_text(css, content_type="text/css; charset=utf-8")
                return

            if parsed.path == "/assets/app.js":
                js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
                self._send_text(js, content_type="text/javascript; charset=utf-8")
                return

            if parsed.path == "/api/state":
                self._send_json(read_json(review_state_path(self.run_dir)))
                return

            if parsed.path == "/api/cover":
                self._send_json(load_cover_state(self.run_dir))
                return

            if parsed.path == "/api/manifest":
                manifest_path = self.run_dir / "manifest.json"
                if manifest_path.is_file():
                    self._send_json(read_json(manifest_path, default={}))
                else:
                    self._send_json({"run_date": self.run_dir.name, "selected": [], "edition_brief": {}})
                return

            if parsed.path == "/media":
                qs = parse_qs(parsed.query)
                raw_param = (qs.get("path") or [""])[0]
                if not raw_param:
                    self._send_text("missing path", status=400)
                    return
                candidate = Path(unquote(raw_param))
                if not candidate.is_absolute():
                    candidate = self.run_dir / candidate
                resolved = candidate.resolve()
                if not _is_inside(resolved, self.run_dir) or not resolved.is_file():
                    self._send_text("not found", status=404)
                    return
                mime = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
                self._send_bytes(resolved.read_bytes(), content_type=mime)
                return

            self._send_text("not found", status=404)
        except Exception as exc:
            self._send_json({"error": f"{type(exc).__name__}: {exc}"}, status=500)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if not self._post_authorized():
                self._send_json({"error": "forbidden: invalid or missing review token / origin"}, status=403)
                return

            length = int(self.headers.get("Content-Length", "0") or 0)
            raw_body = self.rfile.read(length) if length > 0 else b"{}"
            payload = json.loads(raw_body.decode("utf-8") or "{}")

            if parsed.path == "/api/save":
                result = write_final_script_from_state(self.run_dir, payload)
                updated_state = read_json(review_state_path(self.run_dir))
                cover = load_cover_state(self.run_dir)
                self._send_json({**result, "state": updated_state, "cover": cover})
                return

            if parsed.path == "/api/cover":
                try:
                    saved_cover = save_cover_state(self.run_dir, payload)
                except ValueError as exc:
                    self._send_json({"error": str(exc)}, status=400)
                    return
                self._send_json({"ok": True, "cover": saved_cover})
                return

            if parsed.path == "/api/upload":
                filename = str(payload.get("filename") or "image.png")
                data_url = str(payload.get("dataUrl") or "")
                if "," in data_url:
                    data_url = data_url.split(",", 1)[1]
                binary = base64.b64decode(data_url)
                uploads_dir = ensure_dir(review_dir(self.run_dir) / "uploads")
                saved_path = uploads_dir / _safe_upload_name(filename)
                saved_path.write_bytes(binary)
                self._send_json(
                    {
                        "id": f"img-{uuid.uuid4().hex[:10]}",
                        "label": Path(filename).stem or "上传配图",
                        "path": str(saved_path),
                        "url": _media_url(saved_path),
                        "source": "手动上传",
                        "source_url": "",
                        "enabled": True,
                        "required": False,
                    }
                )
                return

            self._send_json({"error": "not found"}, status=404)
        except Exception as exc:
            self._send_json({"error": f"{type(exc).__name__}: {exc}"}, status=500)

    def log_message(self, fmt: str, *args: Any) -> None:
        if os.environ.get("BRIEFING_REVIEW_QUIET") == "1":
            return
        print(f"[review] {self.address_string()} - {fmt % args}")


def create_review_server(
    run_dir: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> ThreadingHTTPServer:
    """Create and bind a ThreadingHTTPServer serving the local review workbench."""
    root = Path(run_dir).resolve()
    allow_remote = os.environ.get("BRIEFING_REVIEW_ALLOW_REMOTE") == "1"
    if host.lower() not in LOOPBACK_HOSTS and not allow_remote:
        raise RuntimeError(
            f"refusing to bind review server to non-loopback host {host!r}; "
            "set BRIEFING_REVIEW_ALLOW_REMOTE=1 only on a trusted network"
        )
    create_review_package(root, force=False)
    token = secrets.token_hex(24)

    class BoundHandler(_ReviewRequestHandler):
        pass

    BoundHandler.run_dir = root
    BoundHandler.auth_token = token
    BoundHandler.allow_remote = allow_remote

    server = ThreadingHTTPServer((host, port), BoundHandler)
    server.auth_token = token  # type: ignore[attr-defined]
    server.run_dir = root  # type: ignore[attr-defined]
    return server


def serve_review(run_dir: Path, *, host: str = "127.0.0.1", port: int = 8765) -> None:
    """Run the local review workbench server until interrupted."""
    server = create_review_server(run_dir, host=host, port=port)
    bound_host, bound_port = server.server_address[:2]
    print(f"review_url=http://{bound_host}:{bound_port}/")
    print(f"run_dir={Path(run_dir).resolve()}")
    print("按 Ctrl+C 停止审稿工作台。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("审稿工作台已停止。")
    finally:
        server.server_close()

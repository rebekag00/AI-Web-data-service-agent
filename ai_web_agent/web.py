from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .discovery import PromptDiscoveryEngine
from .excel import ExcelExportError, export_run_excel
from .models import PromptJob
from .storage import AuditStore


STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_REQUEST_BYTES = 256 * 1024


class AppHandler(BaseHTTPRequestHandler):
    server_version = "AIWebDataService/0.1"

    @property
    def app(self) -> "AppServer":
        return self.server  # type: ignore[return-value]

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/api/health":
            self._json({"status": "ok"})
            return
        if parsed.path.startswith("/api/runs/"):
            run_id = parsed.path.removeprefix("/api/runs/")
            if not _valid_run_id(run_id):
                self._json({"error": "invalid run id"}, HTTPStatus.BAD_REQUEST)
                return
            report = self.app.store.run_report(run_id)
            if report is None:
                self._json({"error": "run not found"}, HTTPStatus.NOT_FOUND)
                return
            self._json(report)
            return
        if parsed.path.startswith("/downloads/") and parsed.path.endswith(".xlsx"):
            run_id = parsed.path.removeprefix("/downloads/").removesuffix(".xlsx")
            if not _valid_run_id(run_id):
                self._json({"error": "invalid run id"}, HTTPStatus.BAD_REQUEST)
                return
            path = self.app.data_dir / "exports" / f"{run_id}.xlsx"
            if not path.exists():
                self._json({"error": "Excel export not found"}, HTTPStatus.NOT_FOUND)
                return
            self._file(path, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            return
        self._static(parsed.path)

    def do_POST(self) -> None:
        if self.path != "/api/runs":
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        if not self._origin_allowed():
            self._json({"error": "origin is not allowed"}, HTTPStatus.FORBIDDEN)
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0
        if content_length <= 0 or content_length > MAX_REQUEST_BYTES:
            self._json({"error": "invalid request size"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            payload = json.loads(self.rfile.read(content_length))
            job = _job_from_payload(payload)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        manifest = self.app.engine.run(job)
        try:
            workbook = export_run_excel(
                manifest["run_id"], manifest, self.app.store, self.app.data_dir
            )
            manifest["excel_download"] = f"/downloads/{workbook.name}"
        except ExcelExportError as exc:
            manifest["excel_error"] = str(exc)
        self._json(manifest)

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}")

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parsed = urlsplit(origin)
        return parsed.hostname in {"127.0.0.1", "localhost", "::1"}

    def _static(self, request_path: str) -> None:
        relative = "index.html" if request_path == "/" else unquote(request_path).lstrip("/")
        candidate = (STATIC_DIR / relative).resolve()
        if STATIC_DIR.resolve() not in candidate.parents and candidate != STATIC_DIR.resolve():
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        if not candidate.is_file():
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self._file(candidate, content_type)

    def _file(self, path: Path, content_type: str) -> None:
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


class AppServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], data_dir: Path) -> None:
        super().__init__(address, AppHandler)
        self.data_dir = data_dir.resolve()
        self.store = AuditStore(self.data_dir / "audit.sqlite3")
        self.engine = PromptDiscoveryEngine(self.data_dir, self.store)


def _job_from_payload(payload: object) -> PromptJob:
    if not isinstance(payload, dict):
        raise ValueError("request must be a JSON object")
    prompt = payload.get("prompt")
    if not isinstance(prompt, str):
        raise ValueError("prompt must be text")
    return PromptJob(objective=prompt.strip())


def _valid_run_id(value: str) -> bool:
    return len(value) == 32 and all(char in "0123456789abcdef" for char in value)


def serve(host: str, port: int, data_dir: Path) -> None:
    server = AppServer((host, port), data_dir)
    print(f"AI Web Data Service running at http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

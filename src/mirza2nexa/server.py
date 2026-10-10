"""Local HTTP API + static UI.

Security model (the server listens on 127.0.0.1 only):

* **Host allow-list** — requests whose ``Host`` is not ``127.0.0.1:<port>``/``localhost:<port>``
  are rejected. This defeats DNS-rebinding attacks from web pages in the user's browser.
* **Origin check** — any request carrying an ``Origin`` header must come from the app's own
  origin.
* **Launch token → session cookie** — the launcher opens ``/?t=<random token>``; the page
  exchanges it once for an ``HttpOnly; SameSite=Strict`` cookie. Without it the API refuses.
* **CSRF header** — every state-changing request must carry ``X-CSRF-Token`` matching the
  session's token (obtainable only by same-origin script).
* Strict CSP (no external origins at all), ``no-store`` caching, no framing.

No endpoint ever transmits data off this machine; there is no telemetry.
"""

from __future__ import annotations

import hmac
import json
import secrets
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware

from . import __version__
from .archive import inspect_archive, verify_password
from .errors import MigrationError
from .jobs import Job, JobManager, clear_history, read_history
from .settings import Settings

STATIC_DIR = Path(__file__).parent / "web" / "static"
SESSION_COOKIE = "m2n_session"
ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06")

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "font-src 'self'; connect-src 'self'; form-action 'none'; frame-ancestors 'none'; base-uri 'none'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def _err(status: int, code: str, message: str, detail: str | None = None) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message, "detail": detail}}, status_code=status)


def _mig(exc: MigrationError, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": exc.to_dict()}, status_code=status)


class AppState:
    def __init__(self, manager: JobManager, port: int, launch_token: str):
        self.manager = manager
        self.port = port
        self.launch_token: str | None = launch_token
        self.sessions: dict[str, str] = {}  # session id -> csrf token

    def allowed_hosts(self) -> set[str]:
        return {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

    def allowed_origins(self) -> set[str]:
        return {f"http://{h}" for h in self.allowed_hosts()}


class GuardMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, state: AppState):
        super().__init__(app)
        self.state = state

    async def dispatch(self, request: Request, call_next):
        host = request.headers.get("host", "")
        if host not in self.state.allowed_hosts():
            return _err(421, "security.host", "درخواست از مبدأ غیرمجاز رد شد.")
        origin = request.headers.get("origin")
        if origin is not None and origin not in self.state.allowed_origins():
            return _err(403, "security.origin", "درخواست از مبدأ غیرمجاز رد شد.")
        path = request.url.path
        if path.startswith("/api/") and path not in {"/api/health", "/api/auth"}:
            sid = request.cookies.get(SESSION_COOKIE)
            csrf = self.state.sessions.get(sid or "")
            if csrf is None:
                return _err(401, "security.session", "نشست برنامه معتبر نیست؛ برنامه را دوباره از میانبر اجرا کنید.")
            if request.method not in {"GET", "HEAD"}:
                sent = request.headers.get("x-csrf-token", "")
                if not hmac.compare_digest(sent, csrf):
                    return _err(403, "security.csrf", "توکن امنیتی درخواست نامعتبر است.")
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        return response


def create_app(manager: JobManager, *, port: int, launch_token: str) -> FastAPI:
    app = FastAPI(title="Mirza → NEXA Migration Studio", docs_url=None, redoc_url=None, openapi_url=None)
    state = AppState(manager, port, launch_token)
    app.state.m2n = state
    app.add_middleware(GuardMiddleware, state=state)

    def job_or_404(job_id: str) -> Job | JSONResponse:
        job = manager.get(job_id)
        if job is None:
            return _err(404, "job.not_found", "عملیات موردنظر پیدا نشد.")
        return job

    # ---- static ------------------------------------------------------------------------
    @app.get("/")
    async def index() -> Response:
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html; charset=utf-8")

    @app.get("/static/{name}")
    async def static(name: str) -> Response:
        allowed = {p.name: p for p in STATIC_DIR.iterdir() if p.is_file()}
        if name not in allowed:
            return _err(404, "not_found", "پیدا نشد.")
        media = {
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".svg": "image/svg+xml",
            ".woff2": "font/woff2",
        }.get(allowed[name].suffix, "application/octet-stream")
        return FileResponse(allowed[name], media_type=media)

    # ---- session -----------------------------------------------------------------------
    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__}

    @app.post("/api/auth")
    async def auth(request: Request) -> Response:
        body = await request.json()
        token = str(body.get("token", ""))
        if state.launch_token is None or not hmac.compare_digest(token, state.launch_token):
            # Allow re-auth of an existing session (page reload) without the one-time token.
            sid = request.cookies.get(SESSION_COOKIE)
            if sid and sid in state.sessions:
                return JSONResponse({"csrf": state.sessions[sid]})
            return _err(401, "security.token", "توکن ورود نامعتبر یا منقضی است؛ برنامه را دوباره اجرا کنید.")
        state.launch_token = None  # one-time
        sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        state.sessions[sid] = csrf
        resp = JSONResponse({"csrf": csrf})
        resp.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="strict", secure=False, path="/")
        return resp

    @app.get("/api/session")
    async def session(request: Request) -> dict[str, Any]:
        sid = request.cookies.get(SESSION_COOKIE, "")
        return {"csrf": state.sessions[sid], "version": __version__}

    # ---- settings & history -------------------------------------------------------------
    @app.get("/api/settings")
    async def get_settings() -> dict[str, Any]:
        return asdict(manager.settings)

    @app.put("/api/settings")
    async def put_settings(request: Request) -> Response:
        body = await request.json()
        current = asdict(manager.settings)
        current.update({k: v for k, v in body.items() if k in current})
        try:
            s = Settings(**current)
        except TypeError:
            return _err(400, "settings.invalid", "تنظیمات نامعتبر است.")
        problems = s.validate()
        if problems:
            return _err(400, "settings.invalid", "برخی مقادیر تنظیمات خارج از محدوده مجاز است.", ",".join(problems))
        s.save()
        manager.settings = s
        return JSONResponse(asdict(s))

    @app.get("/api/history")
    async def history() -> dict[str, Any]:
        return {"items": read_history()}

    @app.delete("/api/history")
    async def delete_history() -> dict[str, Any]:
        clear_history()
        return {"ok": True}

    # ---- jobs --------------------------------------------------------------------------
    @app.get("/api/jobs")
    async def list_jobs() -> dict[str, Any]:
        return {"items": [j.public() for j in manager.list()]}

    @app.post("/api/jobs")
    async def upload(request: Request) -> Response:
        filename = request.headers.get("x-filename", "backup.zip")
        try:
            from urllib.parse import unquote

            filename = unquote(filename)
        except Exception:  # noqa: BLE001
            filename = "backup.zip"
        limit = manager.settings.archive_limits().max_archive_bytes
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            return _err(413, "archive.limit_exceeded", "حجم فایل ZIP از حد مجاز تنظیمات بیشتر است.")
        job = manager.create(filename)
        size = 0
        head = b""
        try:
            with open(job.archive_path, "wb") as fh:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > limit:
                        raise ValueError("too_large")
                    if len(head) < 4:
                        head += chunk[: 4 - len(head)]
                    fh.write(chunk)
        except ValueError:
            manager.delete(job)
            return _err(413, "archive.limit_exceeded", "حجم فایل ZIP از حد مجاز تنظیمات بیشتر است.")
        except Exception:  # noqa: BLE001 - client disconnect / disk errors
            manager.delete(job)
            return _err(400, "upload.failed", "دریافت فایل کامل نشد (اتصال قطع شد یا فضای دیسک کافی نیست).")
        if size == 0 or head not in ZIP_MAGIC:
            manager.delete(job)
            return _err(400, "archive.not_zip", "فایل انتخاب‌شده ZIP نیست.")
        job.archive_bytes = size
        job.set_stage("inspect", None)
        try:
            insp = await run_in_threadpool(inspect_archive, job.archive_path, manager.settings.archive_limits(),
                                           job.cancel)
        except MigrationError as exc:
            manager.delete(job)
            return _mig(exc)
        job.inspection = {
            "encrypted": insp.encrypted,
            "encryption": insp.encryption,
            "archive_sha256": insp.archive_sha256,
            "sql_entries": list(insp.sql_entries),
            "entries": [
                {"name": e.name, "size": e.size, "compressed_size": e.compressed_size,
                 "encrypted": e.encrypted, "method": e.method, "kind": e.kind}
                for e in insp.entries
            ],
        }
        job.status = "awaiting_password" if insp.encrypted else "uploaded"
        job.stage = None
        return JSONResponse(job.public(), status_code=201)

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str) -> Response:
        job = job_or_404(job_id)
        return job if isinstance(job, JSONResponse) else JSONResponse(job.public())

    @app.post("/api/jobs/{job_id}/start")
    async def start(job_id: str, request: Request) -> Response:
        job = job_or_404(job_id)
        if isinstance(job, JSONResponse):
            return job
        if job.status not in JobManager.STARTABLE:
            return _err(409, "job.bad_state", "این عملیات در وضعیت فعلی قابل شروع نیست.")
        body: dict[str, Any] = await request.json()
        password = body.get("password") or None
        if password is not None and not isinstance(password, str):
            return _err(400, "bad_request", "رمز نامعتبر است.")
        if job.inspection and job.inspection.get("encrypted"):
            try:
                await run_in_threadpool(verify_password, job.archive_path, password)
            except MigrationError as exc:
                return _mig(exc)
        options = {
            "password": password,
            "package_secret_mode": body.get("package_secret_mode", "generated"),  # generated|passphrase
            "package_passphrase": body.get("package_passphrase") or None,
            "money_unit": body.get("money_unit") or None,
            "previous_job_id": body.get("previous_job_id") or None,
        }
        if options["package_secret_mode"] == "passphrase":  # noqa: S105 - mode name, not a secret
            pp = options["package_passphrase"]
            if not isinstance(pp, str) or len(pp) < 12:
                return _err(400, "package.passphrase_weak", "رمز بسته باید حداقل ۱۲ کاراکتر باشد.")
        try:
            manager.start(job, options)
        except MigrationError as exc:
            return _mig(exc, 409)
        return JSONResponse(job.public(), status_code=202)

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel(job_id: str) -> Response:
        job = job_or_404(job_id)
        if isinstance(job, JSONResponse):
            return job
        manager.cancel(job)
        return JSONResponse(job.public())

    @app.delete("/api/jobs/{job_id}")
    async def delete(job_id: str) -> Response:
        job = job_or_404(job_id)
        if isinstance(job, JSONResponse):
            return job
        await run_in_threadpool(manager.delete, job)
        return JSONResponse({"ok": True})

    def _report_file(job: Job, name: str) -> Path | None:
        p = job.workspace / "out" / name
        return p if p.exists() else None

    @app.get("/api/jobs/{job_id}/report")
    async def report(job_id: str) -> Response:
        job = job_or_404(job_id)
        if isinstance(job, JSONResponse):
            return job
        p = _report_file(job, "report.json")
        if p is None:
            return _err(404, "report.not_ready", "گزارش هنوز آماده نیست.")
        return JSONResponse(json.loads(p.read_text(encoding="utf-8")))

    @app.get("/api/jobs/{job_id}/review")
    async def review(job_id: str, offset: int = 0, limit: int = 50, kind: str | None = None,
                     severity: str | None = None) -> Response:
        job = job_or_404(job_id)
        if isinstance(job, JSONResponse):
            return job
        p = job.workspace / "stage" / "reports" / "review-queue.jsonl"
        p = p if p.exists() and job.status == "completed" else None
        if p is None:
            return _err(404, "report.not_ready", "صف بررسی هنوز آماده نیست.")
        limit = max(1, min(limit, 500))
        items, total = [], 0
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                rec = json.loads(line)
                if kind and rec.get("kind") != kind:
                    continue
                if severity and rec.get("severity") != severity:
                    continue
                if offset <= total < offset + limit:
                    items.append(rec)
                total += 1
        return JSONResponse({"items": items, "total": total, "offset": offset, "limit": limit})

    @app.get("/api/jobs/{job_id}/preview/{entity}")
    async def preview(job_id: str, entity: str, limit: int = 20) -> Response:
        job = job_or_404(job_id)
        if isinstance(job, JSONResponse):
            return job
        if not entity.replace("_", "").replace("-", "").isalnum():
            return _err(400, "bad_request", "نوع داده نامعتبر است.")
        p = job.workspace / "stage" / "records" / f"{entity}.jsonl"
        if not p.exists():
            return _err(404, "preview.not_found", "برای این بخش داده‌ای وجود ندارد.")
        limit = max(1, min(limit, 200))
        items = []
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                items.append(json.loads(line))
                if len(items) >= limit:
                    break
        return JSONResponse({"entity": entity, "items": items})

    @app.get("/api/jobs/{job_id}/download")
    async def download(job_id: str) -> Response:
        job = job_or_404(job_id)
        if isinstance(job, JSONResponse):
            return job
        if job.package_path is None or not job.package_path.exists():
            return _err(404, "package.not_ready", "بسته خروجی هنوز آماده نیست.")
        short = (job.summary or {}).get("import_id", job.id)[:12]
        return FileResponse(job.package_path, media_type="application/octet-stream",
                            filename=f"nexa-migration-{short}.nxpkg")

    @app.get("/api/jobs/{job_id}/key")
    async def key(job_id: str) -> Response:
        job = job_or_404(job_id)
        if isinstance(job, JSONResponse):
            return job
        if job.key_text is None:
            return _err(404, "package.no_key", "کلید تولیدشده‌ای برای این عملیات وجود ندارد.")
        short = (job.summary or {}).get("import_id", job.id)[:12]
        return Response(job.key_text + "\n", media_type="text/plain; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="nexa-migration-{short}.nxkey"'})

    @app.get("/api/jobs/{job_id}/report/download")
    async def report_download(job_id: str) -> Response:
        job = job_or_404(job_id)
        if isinstance(job, JSONResponse):
            return job
        p = _report_file(job, "report.json")
        if p is None:
            return _err(404, "report.not_ready", "گزارش هنوز آماده نیست.")
        return FileResponse(p, media_type="application/json", filename=f"mirza2nexa-report-{job.id}.json")

    return app


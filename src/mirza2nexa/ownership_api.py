"""HTTP routes for the bulk ownership review (``/api/jobs/{job_id}/ownership/...``).

Registered by :func:`server.create_app`, so every route sits behind the same localhost
Host/Origin/session/CSRF guard. See ``docs/OWNERSHIP_REVIEW.md``.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from . import ownership_review as orv
from .errors import MigrationError
from .jobs import Job, JobManager
from .nxpkg.crypto import read_header_from
from .panel_probe import ProbeError, ReadOnlyViolation, make_probe
from .settings import app_dir

MAX_BODY = 2 * 1024 * 1024


def reviews_dir() -> Path:
    return app_dir() / "reviews"


@dataclass
class LiveCheck:
    running: bool = False
    panel_code: str | None = None
    done: int = 0
    total: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    error: dict[str, str] | None = None
    started_at: float | None = None
    finished_at: float | None = None
    cancel: threading.Event = field(default_factory=threading.Event)

    def public(self, enabled: bool) -> dict[str, Any]:
        return {"enabled": enabled, "running": self.running, "panel_code": self.panel_code, "done": self.done,
                "total": self.total, "by_status": dict(sorted(self.by_status.items())), "error": self.error,
                "started_at": self.started_at, "finished_at": self.finished_at}


@dataclass
class Session:
    store: orv.ReviewStore
    stage_dir: Path
    offsets: dict[str, int]
    panel_types: dict[str, str | None]
    live: LiveCheck = field(default_factory=LiveCheck)

    def record(self, key: str) -> dict[str, Any] | None:
        off = self.offsets.get(key)
        path = self.stage_dir / "records" / "service_ownership.jsonl"
        if off is None or not path.exists():
            return None
        with open(path, "rb") as fh:
            fh.seek(off)
            return json.loads(fh.readline())


class Sessions:
    """One session per job, but ONE shared store per import: two jobs converted from the same
    backup write to the same decisions, so they must share the same lock and plans."""

    def __init__(self) -> None:
        self._by_job: dict[str, Session] = {}
        self._stores: dict[str, orv.ReviewStore] = {}
        self._lock = threading.Lock()

    def get(self, job: Job) -> Session:
        with self._lock:
            s = self._by_job.get(job.id)
            if s is not None:
                return s
            s = _open_session(job, self._store_locked)
            self._by_job[job.id] = s
            return s

    def _store_locked(self, import_id: str, fingerprint: str | None = None) -> orv.ReviewStore:
        st = self._stores.get(import_id)
        if st is None:
            st = orv.ReviewStore.for_import(reviews_dir(), import_id, fingerprint)
            self._stores[import_id] = st
        return st

    def existing_store(self, import_id: str | None) -> orv.ReviewStore | None:
        """The shared store for ``import_id`` if a decisions file exists (never creates one)."""
        if not isinstance(import_id, str) or not import_id:
            return None
        with self._lock:
            if import_id in self._stores:
                return self._stores[import_id]
            if not (reviews_dir() / f"{import_id}.sqlite").exists():
                return None
            try:
                return self._store_locked(import_id)
            except MigrationError:
                return None


def _open_session(job: Job, store_for: Callable[..., orv.ReviewStore]) -> Session:
    if job.status != "completed" or not job.summary:
        raise orv.ReviewError("ابتدا تبدیل باید کامل شود.", code="ownership.not_ready", status=404)
    stage = job.workspace / "stage"
    path = stage / "records" / "service_ownership.jsonl"
    if not path.is_file() or path.is_symlink():
        raise orv.ReviewError("داده مالکیت سرویس‌ها در این تبدیل وجود ندارد.", code="ownership.not_ready", status=404)
    offsets: dict[str, int] = {}
    records: list[dict[str, Any]] = []
    with open(path, "rb") as fh:
        while True:
            off = fh.tell()
            line = fh.readline()
            if not line:
                break
            if not line.strip():
                continue
            rec = json.loads(line)
            key = rec.get("idempotency_key")
            if isinstance(key, str):
                offsets[key] = off
                records.append(rec)
    panel_types: dict[str, str | None] = {}
    for p in orv.iter_jsonl(stage / "records" / "panel_registry.jsonl"):
        if p.get("code_panel"):
            panel_types[p["code_panel"]] = (p.get("panel_type") or {}).get("normalized")
    store = store_for(job.summary["import_id"], job.summary.get("source_fingerprint"))
    store.sync(records, orv.load_invoice_index(stage))
    return Session(store=store, stage_dir=stage, offsets=offsets, panel_types=panel_types)


def _err(status: int, code: str, message: str, detail: str | None = None) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message, "detail": detail}}, status_code=status)


def _review_err(e: MigrationError) -> JSONResponse:
    return _err(getattr(e, "status", 400), e.code, e.message_fa, e.detail)


async def _json_body(request: Request) -> dict[str, Any] | JSONResponse:
    raw = await request.body()
    if len(raw) > MAX_BODY:
        return _err(413, "bad_request", "درخواست بیش از حد بزرگ است.")
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        return _err(400, "bad_request", "درخواست نامعتبر است.")
    return body if isinstance(body, dict) else _err(400, "bad_request", "درخواست نامعتبر است.")


def _filter_from_query(request: Request) -> dict[str, Any]:
    qp = request.query_params
    flt: dict[str, Any] = {k: qp.get(k) for k in orv.FILTER_FIELDS if qp.get(k)}
    if qp.get("q"):
        flt["q"] = qp.get("q")
    return flt


def _evidence(rec: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for e in (rec.get("ownership_evidence") or [])[:50]:
        out.append({k: e.get(k) for k in ("source_table", "pk", "role", "relation", "reason", "type_raw", "rule")})
    return out


def readiness_overlay(sessions: Sessions, data: dict[str, Any]) -> dict[str, Any]:
    """Add the administrator's review state to a readiness document, kept apart from proof."""
    store = sessions.existing_store(data.get("import_id"))
    if store is None:
        return data
    s = store.summary()
    own = dict(data.get("ownership") or {})
    own["admin_review"] = {
        "proven": s["totals"]["PROVEN"], "admin_attested": s["totals"]["ADMIN_ATTESTED"],
        "pending": s["totals"]["PENDING"], "rejected": s["totals"]["REJECTED"],
        "quarantined": s["totals"]["QUARANTINED"], "stale": s["stale"], "sealed": s["sealed"],
        "admin_attested_is_proof": False,
    }
    return {**data, "ownership": own}


def register(app: FastAPI, manager: JobManager, job_or_404: Callable[[str], Job | JSONResponse],
             sessions: Sessions) -> None:
    base = "/api/jobs/{job_id}/ownership"

    async def session_for(job_id: str) -> Session | JSONResponse:
        job = job_or_404(job_id)
        if isinstance(job, JSONResponse):
            return job
        try:
            return await run_in_threadpool(sessions.get, job)
        except MigrationError as e:
            return _review_err(e)

    async def call(fn: Callable[[], Any]) -> Response:
        try:
            return JSONResponse(await run_in_threadpool(fn))
        except MigrationError as e:
            return _review_err(e)

    @app.get(base + "/summary")
    async def own_summary(job_id: str) -> Response:
        s = await session_for(job_id)
        if isinstance(s, JSONResponse):
            return s
        def summary() -> dict[str, Any]:
            out = s.store.summary()
            out["live"] = {**out["live"], "enabled": manager.settings.panel_live_check_enabled}
            return out

        return await call(summary)

    @app.get(base + "/facets")
    async def own_facets(job_id: str) -> Response:
        s = await session_for(job_id)
        if isinstance(s, JSONResponse):
            return s
        return await call(s.store.facets)

    @app.get(base + "/items")
    async def own_items(job_id: str, request: Request, offset: int = 0, limit: int = 100) -> Response:
        s = await session_for(job_id)
        if isinstance(s, JSONResponse):
            return s
        flt = _filter_from_query(request)
        return await call(lambda: s.store.items(flt, offset=offset, limit=limit))

    @app.get(base + "/items/{key:path}")
    async def own_item(job_id: str, key: str) -> Response:
        s = await session_for(job_id)
        if isinstance(s, JSONResponse):
            return s

        def detail() -> dict[str, Any]:
            it = s.store.item(key)
            if it is None:
                raise orv.ReviewError("سرویس پیدا نشد.", code="ownership.item_not_found", status=404)
            s.store.mark_viewed(key)
            rec = s.record(key) or {}
            cand = rec.get("current_owner_candidate") or None
            orig = rec.get("original_owner") or None
            return {**it, "viewed": True,
                    "owner": {"telegram_user_id": cand.get("telegram_user_id"),
                              "in_user_table": cand.get("in_user_table"),
                              "customer_decision": cand.get("customer_decision")} if cand else None,
                    "original_owner": {"telegram_user_id": orig.get("telegram_user_id"),
                                       "basis": orig.get("basis")} if orig else None,
                    "panel_account_username": rec.get("panel_account_username"),
                    "evidence": _evidence(rec), "evidence_total": rec.get("ownership_evidence_total"),
                    "transfer_count": len(rec.get("transfer_evidence") or []),
                    "history": s.store.history(key)}

        return await call(detail)

    @app.post(base + "/plan")
    async def own_plan(job_id: str, request: Request) -> Response:
        s = await session_for(job_id)
        if isinstance(s, JSONResponse):
            return s
        body = await _json_body(request)
        if isinstance(body, JSONResponse):
            return body
        return await call(lambda: s.store.plan(str(body.get("action") or ""), body.get("selection") or {},
                                               body.get("note")))

    @app.post(base + "/commit")
    async def own_commit(job_id: str, request: Request) -> Response:
        s = await session_for(job_id)
        if isinstance(s, JSONResponse):
            return s
        body = await _json_body(request)
        if isinstance(body, JSONResponse):
            return body
        return await call(lambda: s.store.commit(body.get("plan_id"), confirm_count=body.get("confirm_count"),
                                                 acknowledge=body.get("acknowledge")))

    @app.get(base + "/batches")
    async def own_batches(job_id: str) -> Response:
        s = await session_for(job_id)
        if isinstance(s, JSONResponse):
            return s
        return await call(s.store.batches)

    @app.get(base + "/audit")
    async def own_audit(job_id: str, offset: int = 0, limit: int = 100) -> Response:
        s = await session_for(job_id)
        if isinstance(s, JSONResponse):
            return s
        return await call(lambda: s.store.audit(offset, limit))

    @app.get(base + "/audit/verify")
    async def own_audit_verify(job_id: str) -> Response:
        s = await session_for(job_id)
        if isinstance(s, JSONResponse):
            return s
        return await call(s.store.verify_audit)

    @app.post(base + "/seal")
    async def own_seal(job_id: str, request: Request) -> Response:
        s = await session_for(job_id)
        if isinstance(s, JSONResponse):
            return s
        body = await _json_body(request)
        if isinstance(body, JSONResponse):
            return body
        return await call(lambda: s.store.seal(confirm=body.get("confirm"), confirm_count=body.get("confirm_count")))

    def _export(job: Job, s: Session, *, passphrase: str | None) -> Response:
        if job.package_path is None or not job.package_path.exists():
            raise orv.ReviewError("بسته خروجی در دسترس نیست.", code="package.not_ready", status=404)
        if job.key_text is None and not passphrase:
            raise orv.ReviewError("این بسته با رمز ساخته شده است؛ برای امضای خروجی رمز بسته را وارد کنید.",
                                  code="ownership.export_needs_passphrase", status=404)
        try:
            key = orv.decisions_key(job.package_path, key_text=job.key_text if not passphrase else None,
                                    passphrase=passphrase)
        except MigrationError as e:
            raise orv.ReviewError(e.message_fa, code=e.code, status=403) from None
        with open(job.package_path, "rb") as fh:
            _, header_bytes = read_header_from(fh)
        doc = orv.sign_export(s.store.export_document(
            source_fingerprint=(job.summary or {}).get("source_fingerprint"),
            package_header_sha256=hashlib.sha256(header_bytes).hexdigest()), key)
        body = json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
        return Response(body.encode("utf-8"), media_type="application/json; charset=utf-8", headers={
            "Content-Disposition": f'attachment; filename="ownership-decisions-{s.store.import_id[:12]}.json"'})

    @app.get(base + "/export.json")
    async def own_export(job_id: str) -> Response:
        job = job_or_404(job_id)
        if isinstance(job, JSONResponse):
            return job
        s = await session_for(job_id)
        if isinstance(s, JSONResponse):
            return s
        try:
            return await run_in_threadpool(_export, job, s, passphrase=None)
        except MigrationError as e:
            return _review_err(e)

    @app.post(base + "/export")
    async def own_export_pp(job_id: str, request: Request) -> Response:
        job = job_or_404(job_id)
        if isinstance(job, JSONResponse):
            return job
        s = await session_for(job_id)
        if isinstance(s, JSONResponse):
            return s
        body = await _json_body(request)
        if isinstance(body, JSONResponse):
            return body
        pp = body.get("passphrase")
        if not isinstance(pp, str) or not pp:
            return _err(400, "bad_request", "رمز بسته را وارد کنید.")
        try:
            return await run_in_threadpool(_export, job, s, passphrase=pp)
        except MigrationError as e:
            return _review_err(e)
        finally:
            body.clear()

    # ---- read-only live check ------------------------------------------------------------
    @app.get(base + "/live-check")
    async def live_status(job_id: str) -> Response:
        s = await session_for(job_id)
        if isinstance(s, JSONResponse):
            return s
        return JSONResponse(s.live.public(manager.settings.panel_live_check_enabled))

    @app.post(base + "/live-check/cancel")
    async def live_cancel(job_id: str) -> Response:
        s = await session_for(job_id)
        if isinstance(s, JSONResponse):
            return s
        s.live.cancel.set()
        return JSONResponse(s.live.public(manager.settings.panel_live_check_enabled))

    @app.post(base + "/live-check")
    async def live_start(job_id: str, request: Request) -> Response:
        s = await session_for(job_id)
        if isinstance(s, JSONResponse):
            return s
        if not manager.settings.panel_live_check_enabled:
            return _err(403, "panel.live_check_disabled",
                        "بررسی زنده پنل غیرفعال است؛ در «امنیت و منابع» آن را فعال کنید (فقط خواندن).")
        body = await _json_body(request)
        if isinstance(body, JSONResponse):
            return body
        try:
            if s.live.running:
                return _err(409, "panel.live_check_running", "یک بررسی در حال اجراست.")
            code = body.get("panel_code")
            if not isinstance(code, str) or code not in s.panel_types:
                return _err(400, "panel.unknown_code", "کد پنل در این بکاپ وجود ندارد.")
            try:
                probe = make_probe(s.panel_types.get(code), str(body.get("base_url") or ""),
                                   str(body.get("username") or ""), str(body.get("password") or ""))
            except ProbeError as e:
                return _err(400, e.code, e.message_fa)
            try:
                keys = await run_in_threadpool(s.store.keys_for_live, code, body.get("selection") or None)
            except MigrationError as e:
                probe.close()
                return _review_err(e)
        finally:
            body.clear()  # drop the credentials from the request body
        s.live = LiveCheck(running=True, panel_code=code, total=len(keys), started_at=time.time())
        threading.Thread(target=_run_live, args=(s, probe, keys), name="m2n-live-check", daemon=True).start()
        return JSONResponse(s.live.public(True), status_code=202)


def _run_live(s: Session, probe: Any, keys: list[str]) -> None:
    live = s.live
    buf: list[tuple[str, str]] = []
    try:
        for key in keys:
            if live.cancel.is_set():
                live.error = {"code": "panel.cancelled", "message": "بررسی متوقف شد."}
                break
            rec = s.record(key) or {}
            status = probe.check(rec.get("panel_account_username") or "")
            buf.append((key, status))
            live.by_status[status] = live.by_status.get(status, 0) + 1
            live.done += 1
            if len(buf) >= 100:
                s.store.record_live(buf)
                buf.clear()
    except ReadOnlyViolation as e:
        live.error = {"code": e.code, "message": e.message_fa}
    except ProbeError as e:
        live.error = {"code": e.code, "message": e.message_fa}
    except Exception as e:  # noqa: BLE001 - never crash the server; never leak secrets
        live.error = {"code": "panel.check_failed", "message": "بررسی پنل با خطا متوقف شد.",
                      "detail": type(e).__name__}
    finally:
        if buf:
            s.store.record_live(buf)
        probe.close()
        live.running = False
        live.finished_at = time.time()

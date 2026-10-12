"""In-process job manager.

A job owns one isolated workspace. Processing runs on a worker thread so the HTTP server
(and therefore the UI) never blocks; the UI polls job state and can re-attach after a
reload or a dropped connection because state lives here, not in the browser.

Secrets (ZIP password, package passphrase) are passed to the worker as arguments and are
never stored on the Job object, written to disk or included in history.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .archive import cleanup_workspace, secure_workspace
from .errors import Cancelled, MigrationError
from .progress import CancelToken
from .settings import Settings, app_dir

# (stage key, weight in overall progress). Weights sum to 100.
STAGES: tuple[tuple[str, int], ...] = (
    ("inspect", 2),
    ("extract", 13),
    ("schema", 40),   # streaming SQL parse + staging + schema detection (dominant cost)
    ("convert", 30),
    ("reconcile", 3),
    ("package", 12),
)
_STAGE_INDEX = {k: i for i, (k, _) in enumerate(STAGES)}

TERMINAL = {"completed", "failed", "cancelled"}


@dataclass
class Job:
    id: str
    filename: str
    created_at: float
    workspace: Path
    archive_path: Path
    archive_bytes: int = 0
    status: str = "uploaded"  # uploaded|awaiting_password|queued|running|completed|failed|cancelled
    stage: str | None = None
    stage_done: int = 0
    stage_total: int | None = None
    stage_started_at: float | None = None
    finished_at: float | None = None
    inspection: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    summary: dict[str, Any] | None = None
    package_path: Path | None = None
    key_text: str | None = None  # generated key; in memory for this session only, never persisted
    deleted: bool = False
    cancel: CancelToken = field(default_factory=CancelToken)
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def percent(self) -> float:
        if self.status == "completed":
            return 100.0
        if self.stage is None:
            return 0.0
        idx = _STAGE_INDEX.get(self.stage, 0)
        before = sum(w for _, w in STAGES[:idx])
        weight = STAGES[idx][1]
        frac = 0.0
        if self.stage_total:
            frac = min(1.0, self.stage_done / self.stage_total)
        return round(before + weight * frac, 1)

    def set_stage(self, stage: str, total: int | None = None) -> None:
        with self.lock:
            self.stage = stage
            self.stage_done = 0
            self.stage_total = total
            self.stage_started_at = time.time()

    def progress(self, done: int, total: int | None) -> None:
        with self.lock:
            self.stage_done = done
            if total is not None:
                self.stage_total = total
        self.cancel.check()

    def public(self) -> dict[str, Any]:
        with self.lock:
            return {
                "id": self.id,
                "filename": self.filename,
                "created_at": self.created_at,
                "finished_at": self.finished_at,
                "archive_bytes": self.archive_bytes,
                "status": self.status,
                "stage": self.stage,
                "stage_done": self.stage_done,
                "stage_total": self.stage_total,
                "percent": self.percent,
                "stages": [k for k, _ in STAGES],
                "inspection": self.inspection,
                "error": self.error,
                "summary": self.summary,
                "has_package": self.package_path is not None and self.package_path.exists(),
                "has_key": self.key_text is not None,
            }


Runner = Callable[[Job, dict[str, Any]], None]


class JobManager:
    def __init__(self, settings: Settings, runner: Runner, *, max_workers: int = 1):
        self.settings = settings
        self._runner = runner
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="m2n-job")

    # ---- lifecycle -------------------------------------------------------------------
    def create(self, filename: str) -> Job:
        base = Path(self.settings.workspace_dir) if self.settings.workspace_dir else None
        ws = secure_workspace(base)
        job_id = secrets.token_hex(8)
        safe_name = "".join(c for c in Path(filename).name if c.isprintable())[:120] or "backup.zip"
        job = Job(
            id=job_id,
            filename=safe_name,
            created_at=time.time(),
            workspace=ws,
            archive_path=ws / "input.zip",
        )
        with self._lock:
            self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    STARTABLE = frozenset({"uploaded", "awaiting_password", "failed"})

    def start(self, job: Job, options: dict[str, Any]) -> None:
        # Validate everything before changing state, so a refused start never strands the job.
        prev_id = options.pop("previous_job_id", None)
        if prev_id:
            prev = self.get(prev_id)
            if prev is None or prev.status != "completed" or prev.id == job.id:
                raise MigrationError("عملیات قبلی برای مقایسه معتبر نیست.", code="job.bad_previous")
            options["previous_job"] = prev
        options["_settings"] = self.settings
        with job.lock:
            if job.status not in self.STARTABLE or job.deleted:
                raise MigrationError("این عملیات در وضعیت فعلی قابل شروع نیست.", code="job.bad_state")
            job.status = "queued"
            job.error = None
            job.cancel = CancelToken()
        self._pool.submit(self._run, job, options)

    def _run(self, job: Job, options: dict[str, Any]) -> None:
        with job.lock:
            if job.cancel.cancelled:
                job.status = "cancelled"
                return
            job.status = "running"
        try:
            self._runner(job, options)
            with job.lock:
                job.status = "completed"
        except Cancelled as exc:
            with job.lock:
                job.status = "cancelled"
                job.error = exc.to_dict()
        except MigrationError as exc:
            with job.lock:
                job.status = "awaiting_password" if exc.code in {
                    "archive.password_required", "archive.wrong_password"} else "failed"
                job.error = exc.to_dict()
        except Exception as exc:  # noqa: BLE001 - must never crash the worker or leak data
            with job.lock:
                job.status = "failed"
                job.error = {
                    "code": "internal",
                    "message": "خطای پیش‌بینی‌نشده در پردازش رخ داد. فایل‌های موقت پاک شد؛ دوباره تلاش کنید.",
                    "detail": type(exc).__name__,
                }
        finally:
            options.clear()  # drop secrets
            with job.lock:
                job.finished_at = time.time()
                deleted = job.deleted
            if deleted:
                # Deleted while running: the worker has stopped writing, so cleanup is final now.
                cleanup_workspace(job.workspace)
            else:
                self._record_history(job)

    def cancel(self, job: Job) -> None:
        job.cancel.cancel()
        with job.lock:
            if job.status in {"uploaded", "awaiting_password", "queued"}:
                job.status = "cancelled"

    def delete(self, job: Job) -> None:
        job.cancel.cancel()
        with self._lock:
            self._jobs.pop(job.id, None)
        with job.lock:
            job.deleted = True
            job.key_text = None
            busy = job.status in {"queued", "running"}
        if not busy:
            cleanup_workspace(job.workspace)
        # else: the worker's ``finally`` removes the workspace once it has stopped writing.

    def shutdown(self) -> None:
        for job in self.list():
            job.cancel.cancel()
        self._pool.shutdown(wait=True, cancel_futures=True)
        for job in self.list():
            cleanup_workspace(job.workspace)

    # ---- history (non-sensitive) ----------------------------------------------------
    def _record_history(self, job: Job) -> None:
        if self.settings.history_limit <= 0:
            return
        s = job.summary or {}
        entry = {
            "job_id": job.id,
            "started_at": job.created_at,
            "finished_at": job.finished_at,
            "status": job.status,
            "error_code": (job.error or {}).get("code"),
            "archive_bytes": job.archive_bytes,
            # Aggregate counts and a truncated fingerprint only — no names, ids or amounts.
            "source_fingerprint_prefix": (s.get("source_fingerprint") or "")[:12] or None,
            "schema_profile": s.get("schema_profile"),
            "readiness": s.get("readiness"),
            "counts": s.get("counts"),
            "blocker_count": s.get("blocker_count"),
            "review_count": s.get("review_count"),
        }
        append_history(entry, self.settings.history_limit)


def sweep_stale_workspaces(max_age_hours: float = 2.0) -> int:
    """Delete workspaces left behind by a crashed/killed process (e.g. the console window was
    closed). Only ``job-*`` folders under the default base whose contents have not changed for
    ``max_age_hours`` are removed, so a second running instance is not disturbed."""
    import tempfile

    base = Path(tempfile.gettempdir()) / "mirza2nexa-work"
    removed = 0
    if not base.is_dir() or base.is_symlink():
        return 0
    cutoff = time.time() - max_age_hours * 3600
    for d in base.glob("job-*"):
        try:
            if d.is_symlink() or not d.is_dir():
                continue
            newest = max([d.stat().st_mtime, *(p.stat().st_mtime for p in d.rglob("*"))])
        except OSError:
            continue
        if newest < cutoff:
            cleanup_workspace(d)
            removed += 1
    return removed


_history_lock = threading.Lock()


def history_path() -> Path:
    return app_dir() / "history.json"


def read_history() -> list[dict[str, Any]]:
    try:
        data = json.loads(history_path().read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def append_history(entry: dict[str, Any], limit: int) -> None:
    with _history_lock:
        items = [entry, *read_history()][:limit]
        tmp = history_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(history_path())


def clear_history() -> None:
    with _history_lock:
        try:
            history_path().unlink()
        except FileNotFoundError:
            pass

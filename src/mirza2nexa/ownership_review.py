"""Bulk ownership review: administrator decisions on service ownership before the NEXA import.

The converter decides ownership from evidence (``modules/ownership.py``). This module lets an
administrator settle the remaining ambiguous services in bulk, with four guarantees:

* An administrator can only accept the owner recorded on the Mirza invoice (``invoice.id_user``);
  no other account can ever be chosen here.
* Services without a valid owner, or with a serious conflict, stay quarantined (cannot be approved).
* Administrator decisions (``ADMIN_ATTESTED``) are kept apart from proven ownership in every
  summary and in the export, so nothing reports them as proven.
* Bulk changes are two-phase (plan → confirmed commit), idempotent, audited with a hash chain,
  reversible until the decisions are sealed for the final import.

Storage: one SQLite file per ``import_id`` in the app directory. It holds invoice keys, codes,
states and the audit chain only — no Telegram ids, usernames, credentials or amounts.
See ``docs/OWNERSHIP_REVIEW.md``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import MigrationError
from .modules.adoption import status_class
from .nxpkg.canonical import canonical_json

REVIEW_VERSION = "1.0.0"
EXPORT_SCHEMA = "m2n.ownership_decisions.v1"
HKDF_INFO_DECISIONS = b"nxpkg-v1/ownership-decisions"

PROVEN_DECISIONS = frozenset({"CONFIRMED_CURRENT_OWNER", "CONFIRMED_TRANSFER", "NO_CONFLICT"})
# Reasons that point at a different or invalid owner: never attestable here (docs §1).
SERIOUS_REASONS = frozenset({
    "OWNERSHIP_MULTIPLE_CLAIMANTS",
    "OWNERSHIP_USERNAME_SHARED_BY_INVOICES",
    "OWNERSHIP_TRANSFER_NOT_APPLIED_TO_INVOICE",
    "OWNERSHIP_TRANSFER_TARGET_INVALID",
    "OWNERSHIP_OWNER_ID_INVALID",
    "OWNERSHIP_OWNER_NOT_IN_USER_TABLE",
    "OWNERSHIP_NO_INVOICE_FOR_USERNAME",
})

PROVEN, ATTESTABLE, QUARANTINE_ONLY = "PROVEN", "ATTESTABLE", "QUARANTINE_ONLY"
PENDING, APPROVED, REJECTED = "PENDING", "APPROVED", "REJECTED"
FINAL_CLASSES = ("PROVEN", "ADMIN_ATTESTED", "PENDING", "REJECTED", "QUARANTINED")
BASIS = {"PROVEN": "EVIDENCE", "ADMIN_ATTESTED": "ADMIN_ATTESTATION", "PENDING": "NONE",
         "REJECTED": "ADMIN_REJECTION", "QUARANTINED": "SYSTEM_QUARANTINE"}
ACTIONS = ("APPROVE", "REJECT", "RESET", "UNDO")
LIVE_STATUSES = ("NOT_CHECKED", "LIVE_ACTIVE", "LIVE_DISABLED", "LIVE_LIMITED", "LIVE_EXPIRED", "LIVE_ON_HOLD",
                 "NOT_FOUND_ON_PANEL", "CHECK_ERROR", "UNSUPPORTED_PANEL")
FILTER_FIELDS = ("panel", "status_class", "reason", "review_state", "live_status", "eligibility", "final_class")

PLAN_TTL_SECONDS = 30 * 60
MAX_EXPLICIT_KEYS = 20_000
MAX_NOTE = 500
SAMPLE_SIZE = 10
MAX_PLANS = 50

DISCLAIMER_FA = ("«تأیید مدیر» یعنی پذیرش مالک ثبت‌شده در فاکتور میرزا با تصمیم صریح مدیر؛ این تأیید اثبات "
                 "مالکیت نیست و در همه گزارش‌ها جدا از «مالکیت اثبات‌شده» شمرده می‌شود. سرویس‌های بدون مالک "
                 "معتبر یا دارای تعارض جدی در قرنطینه می‌مانند و به هیچ حسابی واگذار نمی‌شوند.")


class ReviewError(MigrationError):
    """A refused review operation (bad request, stale plan, sealed store…)."""

    def __init__(self, message_fa: str, *, code: str, status: int = 400, detail: str | None = None):
        super().__init__(message_fa, code=code, detail=detail)
        self.status = status


# ---- classification ---------------------------------------------------------------------------

def record_reasons(rec: dict[str, Any]) -> list[str]:
    out = [str(c) for c in rec.get("reason_codes") or [] if c]
    mr = rec.get("manual_review_reason")
    if mr and mr not in out:
        out.insert(0, str(mr))
    return out


def eligibility(rec: dict[str, Any]) -> str:
    """PROVEN / ATTESTABLE / QUARANTINE_ONLY for one ``legacy_service_ownership`` record (docs §1)."""
    decision = rec.get("ownership_decision")
    if decision in PROVEN_DECISIONS:
        return PROVEN
    if decision != "AMBIGUOUS_OWNER" or not rec.get("invoice_key"):
        return QUARANTINE_ONLY
    cand = rec.get("current_owner_candidate") or {}
    if not (cand.get("telegram_user_id") and cand.get("in_user_table") is True
            and cand.get("customer_decision") == "IMPORT"):
        return QUARANTINE_ONLY
    if any(r in SERIOUS_REASONS for r in record_reasons(rec)):
        return QUARANTINE_ONLY
    return ATTESTABLE


def binding(rec: dict[str, Any]) -> str:
    """Decisions are bound to the exact record they were made on (stale if it changes)."""
    return hashlib.sha256(canonical_json(rec)).hexdigest()


# ---- store ------------------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS items (
  key TEXT PRIMARY KEY, invoice_key TEXT, decision TEXT, confidence TEXT, reasons TEXT,
  eligibility TEXT NOT NULL, panel_code TEXT, status_class TEXT, status_raw TEXT, binding TEXT NOT NULL,
  present INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS item_reasons (key TEXT NOT NULL, reason TEXT NOT NULL, PRIMARY KEY (key, reason));
CREATE INDEX IF NOT EXISTS item_reasons_reason ON item_reasons (reason);
CREATE INDEX IF NOT EXISTS items_panel ON items (panel_code);
CREATE TABLE IF NOT EXISTS review (key TEXT PRIMARY KEY, state TEXT NOT NULL, batch_id TEXT, binding TEXT NOT NULL,
  updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS viewed (key TEXT PRIMARY KEY, at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS live (key TEXT PRIMARY KEY, status TEXT NOT NULL, checked_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS batches (batch_id TEXT PRIMARY KEY, plan_id TEXT UNIQUE, action TEXT NOT NULL,
  note TEXT, created_at INTEGER NOT NULL, applied INTEGER, unchanged INTEGER, superseded INTEGER,
  undo_of TEXT, undone_by TEXT, selection TEXT);
CREATE TABLE IF NOT EXISTS batch_items (batch_id TEXT NOT NULL, key TEXT NOT NULL, from_state TEXT NOT NULL,
  to_state TEXT NOT NULL, PRIMARY KEY (batch_id, key));
CREATE TABLE IF NOT EXISTS audit (seq INTEGER PRIMARY KEY, at INTEGER NOT NULL, action TEXT NOT NULL,
  batch_id TEXT, key TEXT, from_state TEXT, to_state TEXT, binding TEXT, note TEXT, prev_hash TEXT NOT NULL,
  hash TEXT NOT NULL);
"""

# effective state / final class, computed in SQL so filters and counts never diverge
_VIEW = """
CREATE TEMP VIEW IF NOT EXISTS v AS
SELECT i.key, i.invoice_key, i.decision, i.confidence, i.reasons, i.eligibility, i.panel_code,
       i.status_class, i.status_raw, i.binding,
       r.batch_id AS batch_id,
       CASE WHEN r.binding = i.binding THEN r.state ELSE 'PENDING' END AS review_state,
       (r.key IS NOT NULL AND r.binding != i.binding) AS stale,
       COALESCE(l.status, 'NOT_CHECKED') AS live_status, l.checked_at AS live_checked_at,
       (w.key IS NOT NULL) AS viewed,
       CASE WHEN i.eligibility = 'QUARANTINE_ONLY' THEN 'QUARANTINED'
            WHEN r.binding = i.binding AND r.state = 'REJECTED' THEN 'REJECTED'
            WHEN i.eligibility = 'PROVEN' THEN 'PROVEN'
            WHEN r.binding = i.binding AND r.state = 'APPROVED' THEN 'ADMIN_ATTESTED'
            ELSE 'PENDING' END AS final_class
FROM items i LEFT JOIN review r ON r.key = i.key LEFT JOIN live l ON l.key = i.key
     LEFT JOIN viewed w ON w.key = i.key
WHERE i.present = 1
"""

_ITEM_COLS = ("key", "invoice_key", "decision", "confidence", "reasons", "eligibility", "panel_code",
              "status_class", "status_raw", "binding", "batch_id", "review_state", "stale", "live_status",
              "live_checked_at", "viewed", "final_class")


def _row_to_item(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(zip(_ITEM_COLS, row, strict=True))
    return {
        "key": d["key"], "invoice_key": d["invoice_key"], "ownership_decision": d["decision"],
        "confidence": d["confidence"], "reasons": json.loads(d["reasons"] or "[]"),
        "eligibility": d["eligibility"], "final_class": d["final_class"], "review_state": d["review_state"],
        "basis": BASIS[d["final_class"]], "batch_id": d["batch_id"] if d["review_state"] != PENDING else None,
        "panel_code": d["panel_code"], "status_class": d["status_class"], "status_raw": d["status_raw"],
        "live_status": d["live_status"], "live_checked_at": d["live_checked_at"], "stale": bool(d["stale"]),
        "viewed": bool(d["viewed"]),
    }


def _now() -> int:
    return int(time.time())


def _event_hash(prev: str, ev: dict[str, Any]) -> str:
    return hashlib.sha256(prev.encode("ascii") + canonical_json(ev)).hexdigest()


GENESIS = "0" * 64


@dataclass
class Plan:
    plan_id: str
    action: str
    note: str | None
    created_at: int
    expires_at: int
    selection: dict[str, Any]
    changes: list[tuple[str, str, str, str, str | None]]  # key, from_state, to_state, binding, batch before
    snapshot: str
    undo_of: str | None = None
    result: dict[str, Any] | None = None
    preview: dict[str, Any] = field(default_factory=dict)


class ReviewStore:
    """One import's review decisions. Thread-safe (single connection guarded by a lock)."""

    def __init__(self, path: Path, *, import_id: str, source_fingerprint: str | None = None):
        self.path = path
        self.import_id = import_id
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None, timeout=30)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.executescript(_SCHEMA)
        self._db.executescript(_VIEW)
        self._plans: dict[str, Plan] = {}
        stored = self._meta("import_id")
        if stored is None:
            self._set_meta("import_id", import_id)
            self._set_meta("source_fingerprint", source_fingerprint or "")
            self._set_meta("review_version", REVIEW_VERSION)
        elif stored != import_id:
            raise ReviewError("فایل تصمیم‌ها متعلق به بکاپ دیگری است.", code="ownership.store_mismatch", status=409)

    @classmethod
    def for_import(cls, base_dir: Path, import_id: str, source_fingerprint: str | None = None) -> ReviewStore:
        if not import_id or not all(c in "0123456789abcdef" for c in import_id) or len(import_id) > 128:
            raise ReviewError("شناسه بکاپ نامعتبر است.", code="ownership.bad_import_id")
        return cls(base_dir / f"{import_id}.sqlite", import_id=import_id, source_fingerprint=source_fingerprint)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---- meta ---------------------------------------------------------------------------
    def _meta(self, k: str) -> str | None:
        row = self._db.execute("SELECT v FROM meta WHERE k = ?", (k,)).fetchone()
        return row[0] if row else None

    def _set_meta(self, k: str, v: str) -> None:
        self._db.execute("INSERT INTO meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v", (k, v))

    @property
    def sealed(self) -> bool:
        return self._meta("sealed_at") is not None

    def _require_open(self) -> None:
        if self.sealed:
            raise ReviewError("تصمیم‌های مالکیت برای Import نهایی قفل شده‌اند و قابل تغییر نیستند.",
                              code="ownership.sealed", status=409)

    # ---- loading ------------------------------------------------------------------------
    def sync(self, ownership_records: Iterable[dict[str, Any]], invoices: dict[str, dict[str, Any]]) -> dict[str, int]:
        """Load (or refresh) the items of this import from the converter's records. Idempotent."""
        rows, reasons = [], []
        for rec in ownership_records:
            key = rec.get("idempotency_key")
            if not isinstance(key, str) or not key:
                continue
            inv = invoices.get(rec.get("invoice_key") or "") or {}
            sc = status_class(inv.get("status_raw"), "1" if inv.get("is_test") else None) if inv else "UNKNOWN"
            rs = record_reasons(rec)
            rows.append((key, rec.get("invoice_key"), rec.get("ownership_decision"), rec.get("ownership_confidence"),
                         json.dumps(rs), eligibility(rec), inv.get("code_panel"), sc, inv.get("status_raw"),
                         binding(rec)))
            reasons.extend((key, r) for r in rs)
        with self._lock:
            if self.sealed:
                # Sealed decisions are frozen: never rewrite items; report any divergence instead.
                current = dict(self._db.execute("SELECT key, binding FROM items WHERE present = 1").fetchall())
                fresh = {r[0]: r[9] for r in rows}
                diverged = sum(1 for k in current.keys() | fresh.keys() if current.get(k) != fresh.get(k))
                self._set_meta("sealed_divergence", str(diverged))
                return {"items": len(current), "stale": 0, "sealed_divergence": diverged}
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._db.execute("UPDATE items SET present = 0")
                self._db.executemany(
                    "INSERT INTO items (key, invoice_key, decision, confidence, reasons, eligibility, panel_code,"
                    " status_class, status_raw, binding, present) VALUES (?,?,?,?,?,?,?,?,?,?,1)"
                    " ON CONFLICT(key) DO UPDATE SET invoice_key=excluded.invoice_key, decision=excluded.decision,"
                    " confidence=excluded.confidence, reasons=excluded.reasons, eligibility=excluded.eligibility,"
                    " panel_code=excluded.panel_code, status_class=excluded.status_class,"
                    " status_raw=excluded.status_raw, binding=excluded.binding, present=1", rows)
                self._db.execute("DELETE FROM item_reasons")
                self._db.executemany("INSERT OR IGNORE INTO item_reasons (key, reason) VALUES (?, ?)", reasons)
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            stale = self._db.execute("SELECT COUNT(*) FROM v WHERE stale").fetchone()[0]
        return {"items": len(rows), "stale": stale}

    # ---- queries ------------------------------------------------------------------------
    def _where(self, flt: dict[str, Any] | None) -> tuple[str, list[Any]]:
        flt = flt or {}
        clauses, params = [], []
        col = {"panel": "panel_code", "status_class": "status_class", "review_state": "review_state",
               "live_status": "live_status", "eligibility": "eligibility", "final_class": "final_class"}
        for name in FILTER_FIELDS:
            vals = flt.get(name)
            if vals in (None, "", []):
                continue
            if isinstance(vals, str):
                vals = [v for v in vals.split(",") if v != ""]
            if not isinstance(vals, list) or not all(isinstance(v, str) for v in vals) or len(vals) > 200:
                raise ReviewError("فیلتر نامعتبر است.", code="ownership.bad_filter")
            ph = ",".join("?" * len(vals))
            if name == "reason":
                clauses.append(f"key IN (SELECT key FROM item_reasons WHERE reason IN ({ph}))")  # noqa: S608 - fixed columns, "?" placeholders only
            elif name == "panel" and "(none)" in vals:
                rest = [v for v in vals if v != "(none)"]
                sub = f" OR panel_code IN ({','.join('?' * len(rest))})" if rest else ""
                clauses.append(f"(panel_code IS NULL{sub})")
                params.extend(rest)
                continue
            else:
                clauses.append(f"{col[name]} IN ({ph})")
            params.extend(vals)
        q = flt.get("q")
        if q:
            if not isinstance(q, str) or len(q) > 64:
                raise ReviewError("عبارت جستجو نامعتبر است.", code="ownership.bad_filter")
            clauses.append("invoice_key LIKE ? ESCAPE '\\'")
            params.append(q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", params

    def items(self, flt: dict[str, Any] | None = None, *, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        where, params = self._where(flt)
        offset, limit = max(0, int(offset)), max(1, min(int(limit), 500))
        with self._lock:
            total = self._db.execute(f"SELECT COUNT(*) FROM v{where}", params).fetchone()[0]  # noqa: S608 - fixed columns, "?" placeholders only
            rows = self._db.execute(f"SELECT {', '.join(_ITEM_COLS)} FROM v{where} ORDER BY key LIMIT ? OFFSET ?",  # noqa: S608 - fixed columns, "?" placeholders only
                                    [*params, limit, offset]).fetchall()
        return {"total": total, "offset": offset, "limit": limit, "items": [_row_to_item(r) for r in rows]}

    def item(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(f"SELECT {', '.join(_ITEM_COLS)} FROM v WHERE key = ?", (key,)).fetchone()  # noqa: S608 - fixed columns, "?" placeholders only
        return _row_to_item(row) if row else None

    def mark_viewed(self, key: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR IGNORE INTO viewed (key, at) VALUES (?, ?)", (key, _now()))

    def history(self, key: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT seq, at, action, batch_id, key, from_state, to_state, note, hash FROM audit"
                                    " WHERE key = ? ORDER BY seq", (key,)).fetchall()
        return [_audit_row(r) for r in rows]

    def facets(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        with self._lock:
            for name, col in (("panel", "panel_code"), ("status_class", "status_class"),
                              ("review_state", "review_state"), ("live_status", "live_status"),
                              ("eligibility", "eligibility"), ("final_class", "final_class")):
                rows = self._db.execute(f"SELECT {col}, COUNT(*) FROM v GROUP BY {col} ORDER BY {col}").fetchall()  # noqa: S608 - fixed columns, "?" placeholders only
                out[name] = [{"value": v if v is not None else "(none)", "count": n} for v, n in rows]
            rows = self._db.execute("SELECT reason, COUNT(*) FROM item_reasons r JOIN v ON v.key = r.key"
                                    " GROUP BY reason ORDER BY reason").fetchall()
            out["reason"] = [{"value": v, "count": n} for v, n in rows]
        return out

    def summary(self) -> dict[str, Any]:
        with self._lock:
            totals = {c: 0 for c in FINAL_CLASSES}
            for c, n in self._db.execute("SELECT final_class, COUNT(*) FROM v GROUP BY final_class"):
                totals[c] = n
            n_items = sum(totals.values())
            stale = self._db.execute("SELECT COUNT(*) FROM v WHERE stale").fetchone()[0]
            viewed = self._db.execute("SELECT COUNT(*) FROM v WHERE viewed").fetchone()[0]
            live = dict(self._db.execute("SELECT live_status, COUNT(*) FROM v GROUP BY live_status").fetchall())
            attestable = self._db.execute("SELECT COUNT(*) FROM v WHERE eligibility = 'ATTESTABLE'").fetchone()[0]
            sealed_at = self._meta("sealed_at")
            head = self._head()
        return {
            "review_version": REVIEW_VERSION,
            "import_id": self.import_id,
            "sealed": sealed_at is not None,
            "sealed_at": int(sealed_at) if sealed_at else None,
            "totals": {"items": n_items, **totals},
            "proven": totals["PROVEN"],
            "admin_attested": totals["ADMIN_ATTESTED"],
            "admin_attested_is_proof": False,
            "attestable": attestable,
            "stale": stale,
            "sealed_divergence": int(self._meta("sealed_divergence") or 0),
            "viewed": viewed,
            "live": {"checked": n_items - live.get("NOT_CHECKED", 0), "by_status": dict(sorted(live.items()))},
            "audit_head": head[1],
            "audit_events": head[0],
            "disclaimer_fa": DISCLAIMER_FA,
        }

    # ---- planning -----------------------------------------------------------------------
    def _select(self, selection: dict[str, Any]) -> list[tuple]:
        if not isinstance(selection, dict):
            raise ReviewError("انتخاب نامعتبر است.", code="ownership.bad_selection")
        mode = selection.get("mode")
        cols = ("key, eligibility, review_state, binding, final_class, live_status, viewed, status_class, stale,"
                " reasons, batch_id")
        if mode == "keys":
            keys = selection.get("keys")
            if not isinstance(keys, list) or not keys or len(keys) > MAX_EXPLICIT_KEYS \
                    or not all(isinstance(k, str) and 0 < len(k) <= 200 for k in keys):
                raise ReviewError("فهرست سرویس‌های انتخاب‌شده نامعتبر است.", code="ownership.bad_selection")
            uniq = sorted(set(keys))
            out: list[tuple] = []
            for i in range(0, len(uniq), 500):
                part = uniq[i:i + 500]
                out.extend(self._db.execute(f"SELECT {cols} FROM v WHERE key IN ({','.join('?' * len(part))})",  # noqa: S608 - fixed columns, "?" placeholders only
                                            part).fetchall())
            if len(out) != len(uniq):
                raise ReviewError("برخی از سرویس‌های انتخاب‌شده در این بکاپ وجود ندارند.",
                                  code="ownership.unknown_keys")
            return sorted(out)
        if mode == "filter":
            where, params = self._where(selection.get("filter"))
            excl = selection.get("exclude_keys") or []
            if not isinstance(excl, list) or len(excl) > MAX_EXPLICIT_KEYS \
                    or not all(isinstance(k, str) for k in excl):
                raise ReviewError("فهرست استثناها نامعتبر است.", code="ownership.bad_selection")
            rows = self._db.execute(f"SELECT {cols} FROM v{where} ORDER BY key", params).fetchall()  # noqa: S608 - fixed columns, "?" placeholders only
            ex = set(excl)
            return [r for r in rows if r[0] not in ex]
        raise ReviewError("نوع انتخاب نامعتبر است.", code="ownership.bad_selection")

    def plan(self, action: str, selection: dict[str, Any], note: str | None = None) -> dict[str, Any]:
        if action not in ACTIONS:
            raise ReviewError("نوع عملیات نامعتبر است.", code="ownership.bad_action")
        if note is not None and (not isinstance(note, str) or len(note) > MAX_NOTE):
            raise ReviewError("یادداشت نامعتبر یا بیش از حد طولانی است.", code="ownership.bad_note")
        with self._lock:
            self._require_open()
            self._expire_plans()
            undo_of = None
            not_eligible: dict[str, int] = {}
            changes: list[tuple[str, str, str, str, str | None]] = []
            unchanged = 0
            superseded = 0
            risk_rows: list[tuple] = []
            if action == "UNDO":
                if not isinstance(selection, dict) or selection.get("mode") != "batch":
                    raise ReviewError("بازگرداندن فقط برای یک دسته ثبت‌شده ممکن است.", code="ownership.bad_selection")
                undo_of = str(selection.get("batch_id") or "")
                b = self._db.execute("SELECT undone_by, action FROM batches WHERE batch_id = ?", (undo_of,)).fetchone()
                if b is None:
                    raise ReviewError("دسته موردنظر پیدا نشد.", code="ownership.batch_not_found", status=404)
                if b[0]:
                    raise ReviewError("این دسته قبلاً بازگردانده شده است.", code="ownership.already_undone", status=409)
                if b[1] == "UNDO":
                    raise ReviewError("دسته بازگرداندن را نمی‌توان دوباره بازگرداند؛ تصمیم تازه ثبت کنید.",
                                      code="ownership.bad_selection")
                rows = self._db.execute(
                    "SELECT bi.key, bi.from_state, bi.to_state, v.review_state, v.binding, v.eligibility,"
                    " v.final_class, v.live_status, v.viewed, v.status_class, v.stale, v.reasons, v.batch_id"
                    " FROM batch_items bi LEFT JOIN v ON v.key = bi.key WHERE bi.batch_id = ? ORDER BY bi.key",
                    (undo_of,)).fetchall()
                selected = len(rows)
                for key, frm, to, cur, bind, elig, fc, live, viewed, sc, stale, rs, cur_batch in rows:
                    # superseded: item gone, decision made stale, or a later batch decided it since
                    if cur is None or stale or cur_batch != undo_of or cur != to:
                        superseded += 1
                        continue
                    changes.append((key, cur, frm, bind, cur_batch))
                    risk_rows.append((key, elig, cur, bind, fc, live, viewed, sc, stale, rs))
            else:
                rows = self._select(selection)
                selected = len(rows)
                for r in rows:
                    key, elig, cur, bind, fc, live, viewed, sc, stale, rs, cur_batch = r
                    if action == "APPROVE":
                        if elig == PROVEN:
                            not_eligible["ALREADY_PROVEN"] = not_eligible.get("ALREADY_PROVEN", 0) + 1
                            continue
                        if elig == QUARANTINE_ONLY:
                            k = _quarantine_reason(json.loads(rs or "[]"))
                            not_eligible[k] = not_eligible.get(k, 0) + 1
                            continue
                        target = APPROVED
                    elif action == "REJECT":
                        if elig == QUARANTINE_ONLY:
                            not_eligible["ALREADY_QUARANTINED"] = not_eligible.get("ALREADY_QUARANTINED", 0) + 1
                            continue
                        target = REJECTED
                    else:  # RESET
                        target = PENDING
                    if cur == target:
                        unchanged += 1
                        continue
                    changes.append((key, cur, target, bind, cur_batch))
                    risk_rows.append(r[:10])
            risks = _risks(action, risk_rows, sum(not_eligible.values()),
                           stale=sum(1 for r in risk_rows if r[8]))
            by_reason: dict[str, int] = {}
            for r in risk_rows:
                for code in json.loads(r[9] or "[]"):
                    by_reason[code] = by_reason.get(code, 0) + 1
            unviewed = [r[0] for r in risk_rows if not r[6]]
            samples = [self.item(k) for k in unviewed[:SAMPLE_SIZE]]
            snapshot = hashlib.sha256(canonical_json([list(c) for c in changes])).hexdigest()
            now = _now()
            plan = Plan(plan_id=secrets.token_hex(12), action=action, note=note or None, created_at=now,
                        expires_at=now + PLAN_TTL_SECONDS, selection=_selection_record(selection), changes=changes,
                        snapshot=snapshot, undo_of=undo_of)
            plan.preview = {
                "plan_id": plan.plan_id, "action": action, "expires_at": plan.expires_at, "selected": selected,
                "applicable": len(changes), "unchanged": unchanged, "superseded": superseded,
                "not_eligible": {"total": sum(not_eligible.values()), "by_reason": dict(sorted(not_eligible.items()))},
                "risks": risks,
                "applicable_by_reason": dict(sorted(by_reason.items())),
                "unreviewed": {"count": len(unviewed), "samples": [s for s in samples if s]},
                "confirm": {"required_count": len(changes)},
                "result_class": {"APPROVE": "ADMIN_ATTESTED", "REJECT": "REJECTED", "RESET": "PENDING",
                                 "UNDO": None}[action],
                "admin_attestation_is_proof": False,
            }
            self._plans[plan.plan_id] = plan
            return plan.preview

    def _expire_plans(self) -> None:
        now = _now()
        for pid in [p for p, pl in self._plans.items() if pl.expires_at < now]:
            self._plans.pop(pid, None)  # committed plans replay from the database (batches.plan_id)
        while len(self._plans) >= MAX_PLANS:
            self._plans.pop(min(self._plans, key=lambda p: self._plans[p].created_at))

    # ---- commit -------------------------------------------------------------------------
    def commit(self, plan_id: str, *, confirm_count: Any, acknowledge: Any) -> dict[str, Any]:
        with self._lock:
            plan = self._plans.get(plan_id) if isinstance(plan_id, str) else None
            if plan is not None and plan.result is not None:  # idempotent replay
                return {**plan.result, "idempotent_replay": True}
            row = self._db.execute("SELECT batch_id FROM batches WHERE plan_id = ?", (plan_id,)).fetchone() \
                if isinstance(plan_id, str) else None
            if row is not None:  # committed earlier (e.g. before a restart or by another session)
                return {**self._batch_result(row[0]), "idempotent_replay": True}
            if plan is None:
                self._require_open()
                raise ReviewError("برنامه این عملیات منقضی شده یا وجود ندارد؛ دوباره «پیش‌نمایش» بگیرید.",
                                  code="ownership.plan_expired", status=409)
            if plan.expires_at < _now():
                self._plans.pop(plan_id, None)
                raise ReviewError("برنامه این عملیات منقضی شده است؛ دوباره «پیش‌نمایش» بگیرید.",
                                  code="ownership.plan_expired", status=409)
            if acknowledge is not True:
                raise ReviewError("برای اجرا باید ریسک‌ها را تأیید کنید.", code="ownership.not_acknowledged")
            if isinstance(confirm_count, bool) or not isinstance(confirm_count, int) \
                    or confirm_count != len(plan.changes):
                raise ReviewError("تعداد واردشده با تعداد مواردی که تغییر می‌کنند برابر نیست.",
                                  code="ownership.confirm_mismatch")
            batch_id = "b-" + hashlib.sha256((self.import_id + plan.plan_id).encode()).hexdigest()[:16]
            now = _now()
            # All checks that depend on stored state run inside the write transaction, so a second
            # process or store on the same file cannot slip a decision or a seal in between.
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._require_open()
                current = self._current(k for k, *_ in plan.changes)
                for key, frm, _to, bind, before in plan.changes:
                    if current.get(key) != (frm, bind, before):
                        raise ReviewError("پس از پیش‌نمایش، وضعیت برخی موارد تغییر کرده است؛ دوباره پیش‌نمایش بگیرید.",
                                          code="ownership.plan_stale", status=409)
                if plan.undo_of and self._db.execute("SELECT undone_by FROM batches WHERE batch_id = ?",
                                                     (plan.undo_of,)).fetchone()[0]:
                    raise ReviewError("این دسته قبلاً بازگردانده شده است.", code="ownership.already_undone",
                                      status=409)
                seq, head = self._head()
                self._db.execute("INSERT INTO batches (batch_id, plan_id, action, note, created_at, applied, unchanged,"
                                 " superseded, undo_of, selection) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                 (batch_id, plan.plan_id, plan.action, plan.note, now, len(plan.changes),
                                  plan.preview["unchanged"], plan.preview["superseded"], plan.undo_of,
                                  json.dumps(plan.selection, ensure_ascii=False, sort_keys=True)))
                self._db.executemany(
                    "INSERT INTO review (key, state, batch_id, binding, updated_at) VALUES (?,?,?,?,?)"
                    " ON CONFLICT(key) DO UPDATE SET state=excluded.state, batch_id=excluded.batch_id,"
                    " binding=excluded.binding, updated_at=excluded.updated_at",
                    [(key, to, batch_id, bind, now) for key, _f, to, bind, _b in plan.changes])
                events = [{"action": plan.action, "batch_id": batch_id, "key": key, "from_state": frm,
                           "to_state": to, "binding": bind, "note": None}
                          for key, frm, to, bind, _b in plan.changes]
                events.append({"action": "BATCH_" + plan.action, "batch_id": batch_id, "key": None,
                               "from_state": None, "to_state": None, "binding": None, "note": plan.note})
                self._append_audit(seq, head, now, events)
                self._db.executemany("INSERT INTO batch_items (batch_id, key, from_state, to_state) VALUES (?,?,?,?)",
                                     [(batch_id, k, f, t) for k, f, t, _bind, _b in plan.changes])
                if plan.undo_of:
                    self._db.execute("UPDATE batches SET undone_by = ? WHERE batch_id = ?", (batch_id, plan.undo_of))
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            plan.result = self._batch_result(batch_id)
            plan.changes = []  # keep only the result for replay
            return {**plan.result, "idempotent_replay": False}

    def _append_audit(self, seq: int, head: str, now: int, events: list[dict[str, Any]]) -> str:
        rows = []
        for ev in events:
            seq += 1
            full = {"seq": seq, "at": now, **ev}
            h = _event_hash(head, full)
            rows.append((seq, now, ev["action"], ev["batch_id"], ev["key"], ev["from_state"], ev["to_state"],
                         ev["binding"], ev["note"], head, h))
            head = h
        self._db.executemany("INSERT INTO audit (seq, at, action, batch_id, key, from_state, to_state, binding, note,"
                             " prev_hash, hash) VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
        return head

    def _current(self, keys: Iterable[str]) -> dict[str, tuple[str, str, str | None]]:
        ks = list(keys)
        out: dict[str, tuple[str, str, str | None]] = {}
        for i in range(0, len(ks), 500):
            part = ks[i:i + 500]
            for k, st, b, batch in self._db.execute(
                    f"SELECT key, review_state, binding, batch_id FROM v WHERE key IN ({','.join('?' * len(part))})",  # noqa: S608 - placeholders only
                    part):
                out[k] = (st, b, batch)
        return out

    def _head(self) -> tuple[int, str]:
        row = self._db.execute("SELECT seq, hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
        return (row[0], row[1]) if row else (0, GENESIS)

    def _batch_result(self, batch_id: str) -> dict[str, Any]:
        r = self._db.execute("SELECT batch_id, action, created_at, applied, unchanged, superseded, note, undo_of,"
                             " undone_by FROM batches WHERE batch_id = ?", (batch_id,)).fetchone()
        return dict(zip(("batch_id", "action", "created_at", "applied", "unchanged", "superseded", "note", "undo_of",
                         "undone_by"), r, strict=True))

    def batches(self) -> dict[str, Any]:
        with self._lock:
            ids = [r[0] for r in self._db.execute("SELECT batch_id FROM batches ORDER BY created_at DESC, rowid DESC")]
            return {"items": [self._batch_result(b) for b in ids]}

    # ---- audit --------------------------------------------------------------------------
    def audit(self, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        offset, limit = max(0, int(offset)), max(1, min(int(limit), 500))
        with self._lock:
            total = self._db.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
            rows = self._db.execute("SELECT seq, at, action, batch_id, key, from_state, to_state, note, hash FROM audit"
                                    " ORDER BY seq DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        return {"total": total, "items": [_audit_row(r) for r in rows]}

    def verify_audit(self) -> dict[str, Any]:
        """Recompute the hash chain AND replay it: the stored decisions must be exactly what the
        audited events produced (a row written outside the chain is detected), and a sealed store must
        still end with its recorded SEAL event."""
        prev = GENESIS
        n = 0
        replay: dict[str, tuple[str, str | None, str | None]] = {}
        last_action = None
        with self._lock:
            for seq, at, action, batch_id, key, frm, to, bind, note, prev_hash, h in self._db.execute(
                    "SELECT seq, at, action, batch_id, key, from_state, to_state, binding, note, prev_hash, hash"
                    " FROM audit ORDER BY seq"):
                n += 1
                ev = {"seq": seq, "at": at, "action": action, "batch_id": batch_id, "key": key, "from_state": frm,
                      "to_state": to, "binding": bind, "note": note}
                if prev_hash != prev or _event_hash(prev, ev) != h or seq != n:
                    return {"ok": False, "events": n, "broken_at": seq, "head": prev, "reason": "chain"}
                if key is not None:
                    replay[key] = (to, batch_id, bind)
                prev = h
                last_action = action
            stored = {k: (st, b, bd) for k, st, b, bd in
                      self._db.execute("SELECT key, state, batch_id, binding FROM review")}
            if stored != replay:
                bad = sorted(k for k in stored.keys() | replay.keys() if stored.get(k) != replay.get(k))
                return {"ok": False, "events": n, "head": prev, "reason": "state_mismatch", "mismatched": len(bad)}
            sealed_head = self._meta("sealed_head")
            if (sealed_head is not None or self._meta("sealed_at") is not None) and \
                    (sealed_head != prev or last_action != "SEAL"):
                return {"ok": False, "events": n, "head": prev, "reason": "seal_anchor"}
        return {"ok": True, "events": n, "head": prev}

    # ---- seal ---------------------------------------------------------------------------
    def seal(self, *, confirm: Any, confirm_count: Any) -> dict[str, Any]:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._require_open()
                total = self._db.execute("SELECT COUNT(*) FROM v").fetchone()[0]
                if confirm != "SEAL" or confirm_count != total or isinstance(confirm_count, bool):
                    raise ReviewError("برای قفل‌کردن، عبارت SEAL و تعداد کل سرویس‌ها را دقیق وارد کنید.",
                                      code="ownership.confirm_mismatch")
                if not self.verify_audit()["ok"]:
                    raise ReviewError("زنجیره ممیزی معتبر نیست؛ قفل‌کردن ممکن نیست.", code="ownership.audit_broken",
                                      status=409)
                now = _now()
                seq, head = self._head()
                digest = _entries_digest(self._entries())
                head = self._append_audit(seq, head, now, [{"action": "SEAL", "batch_id": None, "key": None,
                                                            "from_state": None, "to_state": None, "binding": None,
                                                            "note": digest}])
                self._set_meta("sealed_at", str(now))
                self._set_meta("sealed_head", head)
                self._set_meta("sealed_digest", digest)
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._plans.clear()
        return self.summary()

    # ---- live status (read-only panel check results) -----------------------------------
    def record_live(self, results: Iterable[tuple[str, str]]) -> int:
        now = _now()
        rows = [(k, s, now) for k, s in results if s in LIVE_STATUSES]
        with self._lock:
            self._db.executemany("INSERT INTO live (key, status, checked_at) VALUES (?,?,?) ON CONFLICT(key)"
                                 " DO UPDATE SET status=excluded.status, checked_at=excluded.checked_at", rows)
        return len(rows)

    def keys_for_live(self, panel_code: str, selection: dict[str, Any] | None) -> list[str]:
        with self._lock:
            if selection:
                rows = self._select(selection)
                keys = {r[0] for r in rows}
                return [k for (k,) in self._db.execute("SELECT key FROM v WHERE panel_code = ? ORDER BY key",
                                                        (panel_code,)) if k in keys]
            return [k for (k,) in self._db.execute("SELECT key FROM v WHERE panel_code = ? ORDER BY key",
                                                    (panel_code,))]

    # ---- export -------------------------------------------------------------------------
    def _entries(self) -> list[dict[str, Any]]:
        rows = self._db.execute(f"SELECT {', '.join(_ITEM_COLS)} FROM v ORDER BY key").fetchall()  # noqa: S608 - fixed columns
        entries = []
        for r in rows:
            it = _row_to_item(r)
            entries.append({"key": it["key"], "invoice_key": it["invoice_key"], "class": it["final_class"],
                            "basis": it["basis"], "review_state": it["review_state"], "batch_id": it["batch_id"],
                            "binding": dict(zip(_ITEM_COLS, r, strict=True))["binding"], "stale": it["stale"]})
        return entries

    def export_document(self, *, source_fingerprint: str | None = None,
                        package_header_sha256: str | None = None) -> dict[str, Any]:
        with self._lock:
            summ = self.summary()
            entries = self._entries()
            ver = self.verify_audit()
            digest = _entries_digest(entries)
            sealed_digest = self._meta("sealed_digest")
        return {
            "schema": EXPORT_SCHEMA,
            "review_version": REVIEW_VERSION,
            "import_id": self.import_id,
            "source_fingerprint": source_fingerprint or self._meta("source_fingerprint") or None,
            "package_header_sha256": package_header_sha256,
            "sealed": summ["sealed"],
            "sealed_at": summ["sealed_at"],
            "sealed_digest": sealed_digest,
            "entries_digest": digest,
            "matches_seal": sealed_digest == digest if sealed_digest else None,
            "sealed_divergence": summ.get("sealed_divergence"),
            "audit": {"ok": ver["ok"], "events": ver["events"], "head": ver["head"], "reason": ver.get("reason")},
            "summary": {k: summ["totals"][k] for k in ("items", *FINAL_CLASSES)},
            "admin_attestation_is_proof": False,
            "rules": {"approve_target": "invoice.id_user only", "serious_reasons": sorted(SERIOUS_REASONS)},
            "entries": entries,
        }


# ---- helpers ----------------------------------------------------------------------------------

def _entries_digest(entries: list[dict[str, Any]]) -> str:
    return hashlib.sha256(canonical_json(entries)).hexdigest()


def _audit_row(r: tuple) -> dict[str, Any]:
    return dict(zip(("seq", "at", "action", "batch_id", "key", "from_state", "to_state", "note", "hash"), r,
                    strict=True))


def _quarantine_reason(reasons: list[str]) -> str:
    for r in reasons:
        if r in SERIOUS_REASONS:
            return r
    return "NO_VALID_INVOICE_OWNER"


def _selection_record(selection: dict[str, Any]) -> dict[str, Any]:
    """What is stored about a selection (keys are summarised by count to keep the batch row small)."""
    if not isinstance(selection, dict):
        return {}
    out: dict[str, Any] = {k: selection[k] for k in ("mode", "batch_id") if isinstance(selection.get(k), str)}
    flt = selection.get("filter")
    if isinstance(flt, dict):  # only the validated filter fields, never arbitrary client data
        out["filter"] = {k: flt[k] for k in (*FILTER_FIELDS, "q") if k in flt}
    if isinstance(selection.get("keys"), list):
        out["keys_count"] = len(selection["keys"])
    if isinstance(selection.get("exclude_keys"), list):
        out["excluded_count"] = len(selection["exclude_keys"])
    return out


def _risks(action: str, rows: list[tuple], not_eligible: int, *, stale: int) -> list[dict[str, Any]]:
    # rows: key, eligibility, review_state, binding, final_class, live_status, viewed, status_class, stale, reasons
    risks: list[dict[str, Any]] = []

    def add(code: str, count: int, severity: str) -> None:
        if count:
            risks.append({"code": code, "count": count, "severity": severity})

    if action == "APPROVE":
        add("ADMIN_ATTESTATION_NOT_PROOF", len(rows), "high")
    if action in {"REJECT"}:
        add("REJECT_PROVEN", sum(1 for r in rows if r[1] == PROVEN), "high")
    if action in {"APPROVE", "REJECT", "UNDO"}:
        add("LIVE_NOT_FOUND", sum(1 for r in rows if r[5] == "NOT_FOUND_ON_PANEL"), "high")
        add("UNVIEWED_ITEMS", sum(1 for r in rows if not r[6]), "medium")
        add("LIVE_NOT_CHECKED", sum(1 for r in rows if r[5] == "NOT_CHECKED"), "medium")
        add("LIVE_CHECK_ERROR", sum(1 for r in rows if r[5] in {"CHECK_ERROR", "UNSUPPORTED_PANEL"}), "medium")
        add("STATUS_REMOVED_OR_EXPIRED", sum(1 for r in rows if r[7] in {"REMOVED", "EXPIRED"}), "medium")
    add("STALE_BINDING", stale, "info")
    add("NOT_ELIGIBLE_EXCLUDED", not_eligible, "info")
    return risks


# ---- export authentication -------------------------------------------------------------------

def sign_export(doc: dict[str, Any], key: bytes) -> dict[str, Any]:
    body = {k: v for k, v in doc.items() if k != "authentication"}
    mac = hmac.new(key, canonical_json(body), hashlib.sha256).digest()
    return {**body, "authentication": {"alg": "HMAC-SHA256",
                                       "key": "HKDF-SHA256(package secret, info=nxpkg-v1/ownership-decisions)",
                                       "mac": base64.b64encode(mac).decode("ascii")}}


def verify_export(doc: dict[str, Any], key: bytes) -> bool:
    auth = doc.get("authentication") or {}
    if auth.get("alg") != "HMAC-SHA256" or not isinstance(auth.get("mac"), str):
        return False
    body = {k: v for k, v in doc.items() if k != "authentication"}
    try:
        mac = base64.b64decode(auth["mac"], validate=True)
    except ValueError:
        return False
    return hmac.compare_digest(mac, hmac.new(key, canonical_json(body), hashlib.sha256).digest())


def decisions_key(package_path: Path, *, key_text: str | None = None, passphrase: str | None = None) -> bytes:
    """Derive the export MAC key from the package secret (verified against the package header)."""
    from .nxpkg.crypto import derive_aux_key, parse_key_file, read_header_from

    with open(package_path, "rb") as fh:
        header, _ = read_header_from(fh)
    key = parse_key_file(key_text) if key_text else None
    return derive_aux_key(header, HKDF_INFO_DECISIONS, passphrase=passphrase, key=key)


# ---- stage readers ----------------------------------------------------------------------------

def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def load_invoice_index(stage_dir: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for rec in iter_jsonl(stage_dir / "records" / "invoices.jsonl"):
        k = rec.get("invoice_key")
        if k:
            out[k] = {"code_panel": rec.get("code_panel"), "status_raw": rec.get("status_raw"),
                      "is_test": bool(rec.get("is_test"))}
    return out

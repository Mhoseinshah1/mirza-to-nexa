"""Stage parsed dump rows into an on-disk SQLite database, then detect the schema profile.

Why SQLite: the conversion needs cross-table facts (orphans, duplicates, trial usage,
ownership collisions) over ~200k users and ~130k invoices. Holding those in Python dicts
would grow with the backup; an on-disk index keeps memory bounded and gives a
deterministic byte order (``ORDER BY`` on BINARY-collated UTF-8 text is ``memcmp`` order —
the same order NEXA reads with ``ORDER BY CAST(pk AS BINARY)``).

**The dump is never executed.** SQLite only receives *values* through parameterised
``INSERT`` statements whose SQL text is fixed by this module. Only columns in the NEXA read
set are stored (see :mod:`mirza2nexa.mapping.rules`); secret-bearing columns such as
``invoice.user_info`` / ``uuid`` / ``bottype`` are dropped at parse time and never touch disk.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from . import modules as _modules  # noqa: F401 - registers every TableSpec before any staging
from .catalogue import CATALOGUE, EXCLUDED
from .errors import SchemaUnknown
from .mapping import rules
from .progress import CancelToken, ProgressFn
from .sqldump import DumpReader, RowEvent, TableDef, TableEvent, WarningEvent

FINGERPRINT_VERSION = "mirza2nexa-source-fingerprint:v2"
_BATCH = 2000
_MASK = (1 << 256) - 1


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


@dataclass
class TableStats:
    name: str
    columns: tuple[str, ...]
    column_types: dict[str, str]
    rows: int = 0
    classified: bool = False
    stored_columns: tuple[str, ...] = ()
    missing_required: tuple[str, ...] = ()
    never_read_present: tuple[str, ...] = ()
    digest: int = 0  # order-independent multiset hash of stored rows
    spec_name: str | None = None  # catalogue spec this table resolved to
    klass: str | None = None  # migration class from the catalogue
    sql_name: str | None = None  # staging table name ("src_user", "t3", …) when rows are stored
    primary_key: tuple[str, ...] = ()
    dropped_columns: tuple[str, ...] = ()  # secret columns, never stored
    unmapped_columns: tuple[str, ...] = ()  # undeclared columns of a column-listed spec, not stored


@dataclass
class StagingResult:
    db_path: Path
    tables: dict[str, TableStats]
    warnings: dict[str, int] = field(default_factory=dict)
    unrepresentable_cells: int = 0
    bytes_parsed: int = 0
    files: list[str] = field(default_factory=list)


class Staging:
    """Accumulates rows from one or more dump files into ``db_path``."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=OFF")
        self.conn.execute("PRAGMA synchronous=OFF")
        self.conn.execute("PRAGMA temp_store=FILE")
        self.conn.execute("PRAGMA cache_size=-32000")  # ~32 MB page cache, bounded
        self.conn.execute("PRAGMA trusted_schema=OFF")
        self.tables: dict[str, TableStats] = {}
        self.warnings: dict[str, int] = {}
        self.unrepresentable = 0
        self.current_file = 0
        self._defined_in: dict[str, int] = {}
        self._pending: dict[str, list[tuple]] = {}
        self._kv: dict[str, tuple[int, int, frozenset[str]]] = {}
        self._colidx_cache: dict[tuple[str, tuple[str, ...]], list[int | None]] = {}
        self.conn.execute("CREATE TABLE trial_user (id TEXT PRIMARY KEY) WITHOUT ROWID")

    # ---- schema --------------------------------------------------------------------
    def on_table(self, t: TableDef) -> None:
        cols = t.column_names()
        types = {c.name: c.type for c in t.columns}
        existing = self.tables.get(t.name)
        first_file = self._defined_in.setdefault(t.name, self.current_file)
        if existing is not None and first_file != self.current_file:
            # Two dump files (e.g. an old partial export next to a full one) define the same
            # table: merging them would silently create duplicates. Refuse instead.
            raise SchemaUnknown("یک جدول در چند فایل SQL داخل ZIP تعریف شده است؛ ادغام خودکار انجام نمی‌شود. "
                                "فقط فایل بکاپ کامل را در ZIP قرار دهید.",
                                code="schema.table_defined_twice", detail=f"table {t.name}")
        if existing is not None:
            # phpMyAdmin re-emits keys via ALTER TABLE; columns cannot change mid-dump.
            if existing.columns != cols:
                raise SchemaUnknown("ساختار یک جدول در طول فایل SQL تغییر کرده است؛ پردازش متوقف شد.",
                                    code="schema.inconsistent", detail=f"table {t.name}")
            if t.primary_key and not existing.primary_key:  # phpMyAdmin: ALTER TABLE … ADD PRIMARY KEY
                existing.primary_key = tuple(t.primary_key)
            return
        stats = TableStats(name=t.name, columns=cols, column_types=types, primary_key=tuple(t.primary_key))
        spec = CATALOGUE.lookup(t.name)
        if spec is not None:
            stats.spec_name, stats.klass = spec.name, spec.klass
            if not stats.primary_key and spec.primary_key and all(c in cols for c in spec.primary_key):
                stats.primary_key = spec.primary_key
        if spec is not None and t.name not in rules.SOURCE_TABLES:
            stats.classified = True
            stored, dropped, unmapped = spec.storable_columns(cols)
            stats.stored_columns, stats.dropped_columns, stats.unmapped_columns = stored, dropped, unmapped
            if stored and spec.klass != EXCLUDED:
                stats.sql_name = f"t{len(self._pending)}"
                coldefs = ", ".join(f"{_q('c_' + c)} TEXT" for c in stored)
                self.conn.execute(f"CREATE TABLE {_q(stats.sql_name)} (rowno INTEGER PRIMARY KEY, {coldefs})")
                self._pending[t.name] = []
                if spec.kv_filter and spec.kv_filter[0] in stored and spec.kv_filter[1] in stored:
                    self._kv[t.name] = (stored.index(spec.kv_filter[0]), stored.index(spec.kv_filter[1]),
                                        spec.kv_filter[2])
        elif t.name in rules.SOURCE_TABLES:  # case-sensitive, like legacy-inventory.ts:52-55
            stats.classified = True
            stats.missing_required = tuple(c for c in rules.REQUIRED_COLUMNS[t.name] if c not in cols)
            stats.never_read_present = tuple(c for c in cols if c in rules.NEVER_READ[t.name])
            wanted = rules.REQUIRED_COLUMNS[t.name] + rules.OPTIONAL_COLUMNS[t.name]
            stats.stored_columns = tuple(c for c in wanted if c in cols)
            stats.dropped_columns = stats.never_read_present
            if not stats.missing_required:
                stats.sql_name = "src_" + t.name
                coldefs = ", ".join(f"{_q('c_' + c)} TEXT" for c in stats.stored_columns)
                self.conn.execute(f"CREATE TABLE {_q(stats.sql_name)} (rowno INTEGER PRIMARY KEY, {coldefs})")
                self._pending[t.name] = []
        self.tables[t.name] = stats

    # ---- rows ----------------------------------------------------------------------
    def on_row(self, ev: RowEvent) -> None:
        stats = self.tables.get(ev.table)
        if stats is None and ev.table in rules.SOURCE_TABLES:
            raise SchemaUnknown("داده جدول اصلی بدون تعریف ساختار (CREATE TABLE) آمده است؛ نسخه ساختار قابل تشخیص نیست.",
                                code="schema.insert_without_create", detail=f"table {ev.table}")
        if stats is None:
            # INSERT without a CREATE TABLE: count it under its name, never store it.
            stats = TableStats(name=ev.table, columns=ev.columns, column_types={})
            self.tables[ev.table] = stats
        stats.rows += 1
        if ev.table not in self._pending:
            return
        key = (ev.table, ev.columns)
        idx = self._colidx_cache.get(key)
        if idx is None:
            pos = {c: i for i, c in enumerate(ev.columns)}
            idx = [pos.get(c) for c in stats.stored_columns]
            self._colidx_cache[key] = idx
        values: list[str | None] = []
        kv = self._kv.get(ev.table)
        for i in idx:
            if i is None:
                values.append(None)
                continue
            raw = ev.values[i]
            text = rules.cell_text(raw)
            if text is None and raw is not None:
                self.unrepresentable += 1
                text = "\x00UNREPRESENTABLE"
            values.append(text)
        if kv is not None:
            key_i, value_i, allowed = kv
            if values[key_i] not in allowed:
                values[value_i] = None  # unknown/secret key: value never staged
        stats.digest = (stats.digest + int.from_bytes(
            hashlib.sha256(rules.js_json(values).encode("utf-8", "surrogatepass")).digest(), "big")) & _MASK
        self._pending[ev.table].append((stats.rows, *values))
        if ev.table == "invoice":
            self._note_trial(stats, values)
        if len(self._pending[ev.table]) >= _BATCH:
            self._flush(ev.table)

    def _note_trial(self, stats: TableStats, values: list[str | None]) -> None:
        # source-snapshot.ts:296 — any invoice with is_test '1' (trimmed), ANY status.
        cols = stats.stored_columns
        id_user = values[cols.index("id_user")]
        is_test = values[cols.index("is_test")]
        if id_user is not None and is_test is not None and rules.js_trim(is_test) == "1":
            self.conn.execute("INSERT OR IGNORE INTO trial_user(id) VALUES (?)", (id_user,))

    def _flush(self, table: str) -> None:
        rows = self._pending[table]
        if not rows:
            return
        stats = self.tables[table]
        placeholders = ", ".join("?" for _ in range(len(stats.stored_columns) + 1))
        self.conn.execute("BEGIN")
        self.conn.executemany(f"INSERT INTO {_q(stats.sql_name)} VALUES ({placeholders})", rows)  # noqa: S608 - staging-generated table name, values are bound parameters
        self.conn.execute("COMMIT")
        rows.clear()

    def on_warning(self, ev: WarningEvent) -> None:
        self.warnings[ev.code] = self.warnings.get(ev.code, 0) + 1

    def finish(self) -> None:
        for t in list(self._pending):
            self._flush(t)
        for t in self._pending:
            stats = self.tables[t]
            pk = rules.PRIMARY_KEYS.get(t) if t in rules.SOURCE_TABLES else (
                stats.primary_key[0] if stats.primary_key and stats.primary_key[0] in stats.stored_columns else None)
            if pk:
                self.conn.execute(
                    f"CREATE INDEX {_q('ix_' + stats.sql_name + '_pk')} ON {_q(stats.sql_name)} ({_q('c_' + pk)})")
            if t == "invoice":
                self.conn.execute(f"CREATE INDEX ix_invoice_user ON src_invoice ({_q('c_id_user')})")

    def close(self) -> None:
        self.conn.close()


def stage_dumps(sql_files: list[Path], db_path: Path, *, cancel: CancelToken | None = None,
                progress: ProgressFn | None = None) -> StagingResult:
    """Parse every dump file (streaming) into a fresh staging database."""
    if not CATALOGUE.specs:  # defence in depth: never stage with an empty catalogue
        raise RuntimeError("table catalogue is empty")
    staging = Staging(db_path)
    total = sum(p.stat().st_size for p in sql_files)
    done_before = 0
    result_files = []
    try:
        for index, path in enumerate(sql_files):
            size = path.stat().st_size
            staging.current_file = index

            def _p(done: int, _total: int | None, _base: int = done_before) -> None:
                if progress:
                    progress(_base + done, total)

            reader = DumpReader(path, cancel=cancel, progress=_p)
            for ev in reader.events():
                if isinstance(ev, RowEvent):
                    staging.on_row(ev)
                elif isinstance(ev, TableEvent):
                    staging.on_table(ev.table)
                elif isinstance(ev, WarningEvent):
                    staging.on_warning(ev)
            done_before += size
            result_files.append(path.name)
        staging.finish()
    finally:
        staging.close()
    return StagingResult(db_path=db_path, tables=staging.tables, warnings=staging.warnings,
                         unrepresentable_cells=staging.unrepresentable, bytes_parsed=total,
                         files=result_files)


# ---- schema profile ---------------------------------------------------------------------

@dataclass(frozen=True)
class SchemaProfile:
    id: str
    label: str
    confidence: str  # "exact" | "partial" | "none"
    matched_tables: tuple[str, ...]
    missing_tables: tuple[str, ...]
    unknown_tables: tuple[str, ...]
    missing_columns: dict[str, tuple[str, ...]]
    entity_blocked: dict[str, str]  # entity -> reason code
    schema_hash: str
    variant_hints: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "id": self.id, "label": self.label, "confidence": self.confidence,
            "matched_tables": list(self.matched_tables), "missing_tables": list(self.missing_tables),
            "unknown_tables": list(self.unknown_tables),
            "missing_columns": {k: list(v) for k, v in self.missing_columns.items()},
            "entity_blocked": self.entity_blocked, "schema_hash": self.schema_hash,
            "variant_hints": list(self.variant_hints),
        }


def schema_hash(tables: dict[str, TableStats]) -> str:
    lines = sorted(f"{t.name}.{c}:{t.column_types.get(c, '?')}" for t in tables.values() for c in t.columns)
    return hashlib.sha256("\n".join(lines).encode("utf-8", "surrogatepass")).hexdigest()


def detect_profile(tables: dict[str, TableStats]) -> SchemaProfile:
    """Classify the dump against the only evidenced contract (NEXA read set v1).

    * ``nexa-readset-v1`` — all three tables with every required column: fully supported.
    * ``nexa-readset-v1-partial`` — ``user`` is complete but ``invoice``/``product`` are not
      (e.g. the public botmirzapanel/mirza_pro revisions that lack ``code_panel`` etc.);
      the affected entities are blocked, never approximated.
    * no complete ``user`` table → :class:`SchemaUnknown` (nothing can be converted safely).
    """
    present = [t for t in rules.SOURCE_TABLES if t in tables and tables[t].columns]
    missing_tables = tuple(t for t in rules.SOURCE_TABLES if t not in present)
    unknown = tuple(sorted(n for n, t in tables.items() if not t.classified))
    missing_cols = {t: tables[t].missing_required for t in present if tables[t].missing_required}
    if "user" not in present or "user" in missing_cols:
        raise SchemaUnknown(
            detail=f"user table {'missing' if 'user' not in present else 'lacks required columns'}"
        )
    blocked: dict[str, str] = {}
    if "invoice" not in present or "invoice" in missing_cols:
        for e in ("invoices", "services", "panels"):
            blocked[e] = "SOURCE_SCHEMA_MISSING_TABLE" if "invoice" not in present else "SOURCE_SCHEMA_MISSING_COLUMN"
    if "product" not in present or "product" in missing_cols:
        for e in ("products", "categories"):
            blocked[e] = "SOURCE_SCHEMA_MISSING_TABLE" if "product" not in present else "SOURCE_SCHEMA_MISSING_COLUMN"
    hints = []
    inv = tables.get("invoice")
    if inv is not None:
        if {"notifctions", "note", "user_info"} & set(inv.columns):
            hints.append("invoice columns resemble the public mirza_pro fork")
        if {"code_panel", "is_test", "is_custom", "time_unit"} <= set(inv.columns):
            hints.append("invoice carries the deployed-fork columns NEXA's read set requires")
    exact = not blocked
    return SchemaProfile(
        id="nexa-readset-v1" if exact else "nexa-readset-v1-partial",
        label="Mirza (NEXA legacy read set v1)" if exact else "Mirza — ساختار ناقص نسبت به قرارداد NEXA",
        confidence="exact" if exact else "partial",
        matched_tables=tuple(t for t in present if t not in missing_cols),
        missing_tables=missing_tables,
        unknown_tables=unknown,
        missing_columns=missing_cols,
        entity_blocked=blocked,
        schema_hash=schema_hash(tables),
        variant_hints=tuple(hints),
    )


def source_fingerprint(tables: dict[str, TableStats], profile: SchemaProfile) -> str:
    """Deterministic fingerprint of the *content NEXA reads* plus the whole schema.

    Row digests are order-independent (sum of SHA-256 mod 2^256), so a re-dump of the same
    data in a different physical order fingerprints the same. This is **not** byte-equal to
    NEXA's MySQL-side fingerprint (that one hashes ``information_schema`` data types and PK
    order); the importer compares per-record row checksums, which *are* NEXA-compatible.
    """
    body = {
        "v": FINGERPRINT_VERSION,
        "schema": profile.schema_hash,
        "tables": {
            t: {"rows": tables[t].rows, "columns": list(tables[t].stored_columns),
                "digest": f"{tables[t].digest:064x}"}
            for t in sorted(tables) if tables[t].sql_name
        },
    }
    return hashlib.sha256(rules.js_json(body).encode()).hexdigest()

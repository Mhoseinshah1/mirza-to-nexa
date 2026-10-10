"""Raw source snapshot (package content contract 1.4.0): ``source/catalog.json`` + ``source/tables/*.jsonl``.

Purpose: NEXA's existing legacy importer reads Mirza through ``LegacySourceSession``
(``nexa-bot`` ``apps/api/.../legacy-importer/application/source-port.ts``): table/column
metadata from ``information_schema`` and rows of ``user`` / ``invoice`` / ``product`` as
``CAST(col AS CHAR)`` text in primary-key byte order. The snapshot carries exactly that, so the
importer can run unchanged over a package (``NxpkgLegacySourceConnector``) instead of a live
MySQL. Format: ``docs/PACKAGE_CONTRACT.md`` §"Source snapshot".

Fidelity rule: a cell is written only when its text provably equals what MySQL would return for
``CAST(col AS CHAR)`` of the dumped value under the column's declared type. Anything else is
refused (fail closed) and blocks the package — the value is never approximated:

* binary column types (``binary``, ``varbinary``, ``*blob``, ``bit``, spatial) among the
  allowlisted columns → ``SOURCE_SNAPSHOT_BINARY_COLUMN``;
* a value whose MySQL text is not provable (e.g. ``'12.5'`` in an ``int`` column, a decimal with
  more fraction digits than the column's scale, any ``float``/``double`` value, a non-canonical
  date, a ``json`` value on MySQL, a string longer than ``varchar(n)``) →
  ``SOURCE_SNAPSHOT_VALUE_UNREPRESENTABLE``;
* a NULL or duplicate primary key → ``SOURCE_SNAPSHOT_KEY_INVALID``.

Only the allowlisted columns of the three tables are read; every other value of the row event is
dropped immediately. Secret columns (``inbounds``, ``proxies``, ``user_info``, ``uuid``,
``bottype`` and every column the catalogue marks secret) can never be in the allowlist — this
module refuses to import otherwise.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .mapping import rules
from .sensitivity import column_name_is_secret
from .sqldump import Column, RowEvent, TableDef, _unescape_sq_str

SNAPSHOT_VERSION = 1
CATALOG_PATH = "source/catalog.json"
TABLE_FORMAT = "m2n.source-table.v1"
CATALOG_FORMAT = "m2n.source-catalog.v1"
#: NEXA's synthetic marker table (source-port.ts:121-122) and its fallback label (mysql-legacy-source.ts:359).
SYNTHETIC_MARKER_TABLE = "nexa_synthetic_fixture"
UNLABELLED_MARKER = "SYNTHETIC (unlabelled marker)"

#: Union of every column NEXA's legacy importer reads from each table, in this order:
#: IMPORT_READ_SET_V1 (source-port.ts:36-83), USER_STATUS_READ_SET (read-set.ts:395-399),
#: PRODUCTS_READ_SET (products-read-set.ts:40-62), INVOICE_ARCHIVE_READ_SET
#: (invoice-archive-read-set.ts:63-95). Exact, case-sensitive names; written when present.
ALLOWLIST: dict[str, tuple[str, ...]] = {
    "user": ("id", "Balance", "limit_usertest", "agent", "number", "username", "User_Status"),
    "invoice": ("id_invoice", "id_user", "username", "Status", "is_test", "code_panel", "code_product", "Volume",
                "Service_time", "time_unit", "is_custom", "price_product", "Service_location", "time_sell",
                "name_product", "note", "refral", "time_cron", "notifctions"),
    "product": ("id", "code_product", "name_product", "price_product", "Volume_constraint", "Service_time",
                "Location", "Category", "category", "agent", "note", "data_limit_reset", "one_buy_status",
                "hide_panel"),
}
PRIMARY_KEYS = dict(rules.PRIMARY_KEYS)
SNAPSHOT_TABLES = tuple(ALLOWLIST)

BINARY_COLUMN = "SOURCE_SNAPSHOT_BINARY_COLUMN"
VALUE_UNREPRESENTABLE = "SOURCE_SNAPSHOT_VALUE_UNREPRESENTABLE"
KEY_INVALID = "SOURCE_SNAPSHOT_KEY_INVALID"


def _secret_columns(table: str) -> set[str]:
    from . import modules  # noqa: F401 - registers the catalogue
    from .catalogue import CATALOGUE

    out = set(rules.NEVER_READ.get(table, ()))
    spec = CATALOGUE.lookup(table)
    if spec is not None and spec.columns is not None:
        out |= {c.name for c in spec.columns if not c.storable}
    return out


def assert_allowlist_safe() -> None:
    """Refuse (RuntimeError) if any allowlisted column is, or looks like, a secret column."""
    for table, cols in ALLOWLIST.items():
        bad = sorted(c for c in cols if c in _secret_columns(table) or column_name_is_secret(c))
        if bad or PRIMARY_KEYS[table] not in cols:
            raise RuntimeError(f"source snapshot allowlist of {table} is unsafe: {bad}")


# ---- information_schema emulation ------------------------------------------------------

_TYPE_ALIASES = {
    "integer": "int", "int4": "int", "int1": "tinyint", "int2": "smallint", "int3": "mediumint",
    "middleint": "mediumint", "int8": "bigint", "bool": "tinyint", "boolean": "tinyint", "dec": "decimal",
    "numeric": "decimal", "fixed": "decimal", "real": "double", "float8": "double", "float4": "float",
    "character": "char", "nchar": "char", "nvarchar": "varchar", "long": "mediumtext", "serial": "bigint",
}
TEXT_TYPES = frozenset({"char", "varchar", "tinytext", "text", "mediumtext", "longtext", "enum", "set"})
INT_TYPES = frozenset({"tinyint", "smallint", "mediumint", "int", "bigint"})
BINARY_TYPES = frozenset({"binary", "varbinary", "tinyblob", "blob", "mediumblob", "longblob", "bit", "geometry",
                          "point", "linestring", "polygon", "multipoint", "multilinestring", "multipolygon",
                          "geometrycollection", "geomcollection", "vector"})
_KNOWN_CHARSETS = ("utf8mb4", "utf8mb3", "utf8", "utf16le", "utf16", "utf32", "ucs2", "latin1", "latin2", "ascii",
                   "binary", "cp1256", "cp1251", "cp1250", "greek", "hebrew", "gbk", "big5", "sjis", "euckr",
                   "gb2312", "tis620", "koi8r")
_TYPE_RE = re.compile(r"^([a-z0-9_]+)\s*(?:\(([^)]*)\))?")


def data_type(col_type: str, engine_hint: str | None = None) -> str:
    """``information_schema.COLUMNS.DATA_TYPE`` (lowercase base type) for a dumped column type."""
    m = _TYPE_RE.match(col_type.strip().lower())
    base = m.group(1) if m else col_type.strip().lower()
    base = _TYPE_ALIASES.get(base, base)
    if base == "json" and engine_hint == "MARIADB":
        return "longtext"  # MariaDB: JSON is an alias of LONGTEXT
    return base


def charset_of_collation(collation: str | None) -> str | None:
    if not collation:
        return None
    if collation == "binary":
        return "binary"
    for cs in sorted(_KNOWN_CHARSETS, key=len, reverse=True):
        if collation.startswith(cs + "_"):
            return cs
    return None


def column_charset(col: Column, table: TableDef, engine_hint: str | None) -> str | None:
    if data_type(col.type, engine_hint) not in TEXT_TYPES:
        return None
    return (col.charset or charset_of_collation(col.collation) or table.charset
            or charset_of_collation(table.collation))


def engine_hint_from_header(head: bytes) -> str | None:
    """``MYSQL`` / ``MARIADB`` from the comment header of a dump (first few KiB), else None."""
    hint = None
    for raw in head.split(b"\n")[:60]:
        line = raw.strip().lower()
        if not line.startswith(b"--"):
            continue
        if b"mariadb" in line:
            return "MARIADB"
        if b"mysql dump" in line or b"server version" in line or (b"adminer" in line and b"mysql" in line):
            hint = "MYSQL"
    return hint


# ---- CAST(col AS CHAR) emulation -------------------------------------------------------

class Unrepresentable(Exception):
    pass


_INT_TEXT = re.compile(r"[+-]?[0-9]+")
_DEC_TEXT = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_DATETIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?")
_TIME = re.compile(r"-?[0-9]{2,3}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?")
_INT_RANGES = {"tinyint": 8, "smallint": 16, "mediumint": 24, "int": 32, "bigint": 64}


def _type_args(col_type: str) -> list[str]:
    m = _TYPE_RE.match(col_type.strip().lower())
    if not m or not m.group(2):
        return []
    return [a.strip() for a in m.group(2).split(",")]


def _as_decimal(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise Unrepresentable
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise Unrepresentable
        return value
    if isinstance(value, str) and _DEC_TEXT.fullmatch(value):
        try:
            return Decimal(value)
        except InvalidOperation:
            raise Unrepresentable from None
    raise Unrepresentable


def cast_char(value: Any, col: Column, engine_hint: str | None = None) -> str | None:
    """The text MySQL returns for ``CAST(col AS CHAR)`` of ``value`` stored in ``col``; raises
    :class:`Unrepresentable` when that text cannot be proven from the dump."""
    if value is None:
        return None
    dt = data_type(col.type, engine_hint)
    t = col.type.lower()
    if dt in BINARY_TYPES:
        raise Unrepresentable
    if dt in TEXT_TYPES:
        if isinstance(value, bool):
            raise Unrepresentable
        if isinstance(value, str):
            text = value
        elif isinstance(value, int):
            text = str(value)
        elif isinstance(value, Decimal):
            # An unquoted numeric literal into a text column; only the plain decimal form is
            # provable (an exponent literal is a DOUBLE whose text MySQL formats itself).
            if not value.is_finite() or value.as_tuple().exponent > 0:  # type: ignore[operator]
                raise Unrepresentable
            text = format(value, "f")
        elif isinstance(value, bytes | bytearray):
            try:
                text = bytes(value).decode("utf-8")
            except UnicodeDecodeError:
                raise Unrepresentable from None
        else:
            raise Unrepresentable
        if dt == "char":
            text = text.rstrip(" ")  # CHAR(n) trailing spaces are removed on retrieval
        if dt in ("char", "varchar"):
            args = _type_args(col.type)
            if args and args[0].isdigit() and len(text) > int(args[0]):
                raise Unrepresentable  # strict mode refuses it; non-strict truncates: not provable
        if dt in ("enum", "set") and text == "":
            raise Unrepresentable  # '' is the invalid-value index 0; not provable
        return text
    if dt in INT_TYPES:
        if "zerofill" in t:
            raise Unrepresentable
        if isinstance(value, bool):
            n = int(value)
        elif isinstance(value, int):
            n = value
        elif isinstance(value, str) and _INT_TEXT.fullmatch(value):
            n = int(value)
        elif isinstance(value, Decimal) and value.is_finite() and value == value.to_integral_value():
            n = int(value)
        else:
            raise Unrepresentable
        bits = _INT_RANGES[dt]
        lo, hi = (0, 2**bits - 1) if "unsigned" in t else (-(2 ** (bits - 1)), 2 ** (bits - 1) - 1)
        if not lo <= n <= hi:
            raise Unrepresentable
        return str(n)
    if dt == "decimal":
        args = _type_args(col.type)
        try:
            p = int(args[0]) if args else 10
            s = int(args[1]) if len(args) > 1 else 0
        except ValueError:
            raise Unrepresentable from None
        d = _as_decimal(value)
        q = d.quantize(Decimal(1).scaleb(-s)) if s else d.quantize(Decimal(1))
        if q != d or len(q.as_tuple().digits) > max(p, 1) or ("unsigned" in t and q < 0):
            raise Unrepresentable  # MySQL would round / refuse: not provable
        text = format(q, "f")
        return "0" + text[1:] if text.startswith("-") and q == 0 else text
    if dt in ("date", "datetime", "timestamp", "time", "year"):
        text = value if isinstance(value, str) else str(value) if isinstance(value, int) and dt == "year" else None
        if text is None:
            raise Unrepresentable
        args = _type_args(col.type)
        fsp = int(args[0]) if args and args[0].isdigit() else 0
        if dt == "date":
            ok = _DATE.fullmatch(text) is not None
        elif dt == "year":
            ok = re.fullmatch(r"[0-9]{4}", text) is not None
        else:
            m = (_TIME if dt == "time" else _DATETIME).fullmatch(text)
            frac = m.group(1) if m else None
            ok = m is not None and (len(frac) - 1 if frac else 0) == fsp
        if not ok:
            raise Unrepresentable
        return text
    # float/double (engine-formatted), json on MySQL (normalised), unknown types
    raise Unrepresentable


def default_value(col: Column) -> Any:
    """The value MySQL stores for a column an ``INSERT`` column list leaves out."""
    d = col.default
    if d is None:
        if not col.nullable:
            raise Unrepresentable  # implicit default ('' / 0) depends on sql_mode
        return None
    if len(d) >= 2 and d[0] == "'" and d[-1] == "'":
        return _unescape_sq_str(d[1:-1])
    if _DEC_TEXT.fullmatch(d):
        return Decimal(d) if ("." in d) else int(d)
    raise Unrepresentable  # CURRENT_TIMESTAMP, expressions, …


# ---- collector -------------------------------------------------------------------------

@dataclass
class _TableState:
    tdef: TableDef
    rows: int = 0


@dataclass
class SnapshotResult:
    catalog: dict[str, Any]
    table_files: dict[str, int]  # snapshot table -> rows written
    blockers: dict[str, int] = field(default_factory=dict)
    synthetic_marker: str | None = None


class SnapshotCollector:
    """Observer of the staging pass (``stage_dumps(..., observer=)``): one parse for both."""

    def __init__(self, db_path: Path) -> None:
        assert_allowlist_safe()
        self.db_path = db_path
        db_path.unlink(missing_ok=True)
        self.conn = sqlite3.connect(db_path, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=OFF")
        self.conn.execute("PRAGMA synchronous=OFF")
        self.conn.execute("PRAGMA trusted_schema=OFF")
        self.tables: dict[str, _TableState] = {}
        self.order: list[str] = []
        self.engine_hints: list[str | None] = []
        self.blockers: dict[str, int] = {}
        self.marker_seen = False
        self.marker_label: str | None = None
        self._plan: dict[str, tuple[tuple[str, ...], list[Column]]] = {}
        self._idx: dict[tuple[str, tuple[str, ...]], list[int | None]] = {}
        self._pending: dict[str, list[tuple[bytes, str]]] = {}

    def _block(self, code: str, n: int = 1) -> None:
        self.blockers[code] = self.blockers.get(code, 0) + n

        self.engine_hint: str | None = None

    def on_file(self, path: Path) -> None:
        """Called before each dump file is parsed: sniff its comment header for the engine."""
        with open(path, "rb") as fh:
            self.engine_hints.append(engine_hint_from_header(fh.read(8192)))
        hints = {h for h in self.engine_hints if h}
        self.engine_hint = hints.pop() if len(hints) == 1 else None  # conflicting files: unknown

    def on_table(self, tdef: TableDef) -> None:
        st = self.tables.get(tdef.name)
        if st is None:
            self.tables[tdef.name] = _TableState(tdef)
            self.order.append(tdef.name)
        else:
            st.tdef = tdef  # ALTER TABLE … ADD PRIMARY KEY: same columns, newer keys
        if tdef.name == SYNTHETIC_MARKER_TABLE:
            self.marker_seen = True
        if tdef.name in ALLOWLIST and tdef.name not in self._plan:
            by_name = {c.name: c for c in tdef.columns}
            present = tuple(c for c in ALLOWLIST[tdef.name] if c in by_name)
            if PRIMARY_KEYS[tdef.name] not in present:
                return  # NEXA refuses the table itself (SOURCE_SCHEMA_MISSING_COLUMN); nothing to snapshot
            cols = [by_name[c] for c in present]
            if any(data_type(c.type) in BINARY_TYPES for c in cols):
                self._block(BINARY_COLUMN, sum(1 for c in cols if data_type(c.type) in BINARY_TYPES))
            self._plan[tdef.name] = (present, cols)
            self._pending[tdef.name] = []
            self.conn.execute(f'CREATE TABLE "s_{tdef.name}" (pk BLOB PRIMARY KEY, cells TEXT NOT NULL) WITHOUT ROWID')

    def on_row(self, ev: RowEvent) -> None:
        st = self.tables.get(ev.table)
        if st is None:
            st = self.tables[ev.table] = _TableState(TableDef(ev.table, (), (), ()))
            self.order.append(ev.table)
        st.rows += 1
        if ev.table == SYNTHETIC_MARKER_TABLE:
            self.marker_seen = True
            if self.marker_label is None and "label" in ev.columns:
                v = ev.values[ev.columns.index("label")]
                self.marker_label = v if isinstance(v, str) else None
            return
        plan = self._plan.get(ev.table)
        if plan is None:
            return
        names, cols = plan
        key = (ev.table, ev.columns)
        idx = self._idx.get(key)
        if idx is None:
            pos = {c: i for i, c in enumerate(ev.columns)}
            idx = self._idx[key] = [pos.get(c) for c in names]
        hint = self.engine_hint
        cells: list[str | None] = []
        bad = False
        for i, col in zip(idx, cols, strict=True):
            try:
                raw = ev.values[i] if i is not None else default_value(col)
                cells.append(cast_char(raw, col, hint))
            except Unrepresentable:
                bad = True
                cells.append(None)
        if bad:
            self._block(VALUE_UNREPRESENTABLE)
        pk = cells[0] if names[0] == PRIMARY_KEYS[ev.table] else cells[names.index(PRIMARY_KEYS[ev.table])]
        if pk is None:
            if not bad:
                self._block(KEY_INVALID)
            return
        pend = self._pending[ev.table]
        pend.append((pk.encode("utf-8"), json.dumps(cells, ensure_ascii=False, separators=(",", ":"))))
        if len(pend) >= 2000:
            self._flush(ev.table)

    def _flush(self, table: str) -> None:
        pend = self._pending[table]
        if not pend:
            return
        self.conn.execute("BEGIN")
        for row in pend:
            try:
                self.conn.execute(f'INSERT INTO "s_{table}" VALUES (?, ?)', row)  # noqa: S608 - fixed table names
            except sqlite3.IntegrityError:
                self._block(KEY_INVALID)  # duplicate primary key bytes
        self.conn.execute("COMMIT")
        pend.clear()

    def finish(self) -> None:
        for t in self._pending:
            self._flush(t)

    def close(self) -> None:
        """Idempotent: closes the handle and removes the scratch file."""
        if self.conn is not None:
            self.conn.close()
            self.conn = None  # type: ignore[assignment]
        self.db_path.unlink(missing_ok=True)

    # ---- output --------------------------------------------------------------------
    def catalog(self) -> dict[str, Any]:
        hint = self.engine_hint
        tables = []
        for name in sorted(self.tables, key=lambda n: n.encode("utf-8")):
            st = self.tables[name]
            t = st.tdef
            tables.append({
                "name": name,
                "table_type": "BASE TABLE",
                "storage_engine": t.engine,
                "charset": t.charset or charset_of_collation(t.collation),
                "collation": t.collation,
                "rows": st.rows,
                "columns": [{"column": c.name, "data_type": data_type(c.type, hint), "ordinal": i,
                             "charset": column_charset(c, t, hint)} for i, c in enumerate(t.columns, 1)],
            })
        marker = None
        if self.marker_seen:
            marker = self.marker_label if self.marker_label is not None else UNLABELLED_MARKER
        return {
            "format": CATALOG_FORMAT,
            "snapshot_version": SNAPSHOT_VERSION,
            "synthetic_marker": marker,
            "engine_hint": hint,
            "tables": tables,
            "snapshot_tables": {
                t: {"file": f"source/tables/{t}.jsonl", "primary_key": PRIMARY_KEYS[t],
                    "columns": list(self._plan[t][0])}
                for t in SNAPSHOT_TABLES if t in self._plan
            },
        }

    def write(self, writer: Any) -> SnapshotResult:
        """Write ``source/catalog.json`` and one ``source/tables/<t>.jsonl`` per snapshot table."""
        self.finish()
        catalog = self.catalog()
        written: dict[str, int] = {}
        for t in SNAPSHOT_TABLES:
            if t not in self._plan:
                continue
            names = self._plan[t][0]
            with writer.open_jsonl(f"source/tables/{t}.jsonl") as j:
                j.write({"format": TABLE_FORMAT, "table": t, "primary_key": PRIMARY_KEYS[t], "columns": list(names)})
                n = 0
                for (cells,) in self.conn.execute(f'SELECT cells FROM "s_{t}" ORDER BY pk'):  # noqa: S608
                    j.write({"c": json.loads(cells)})
                    n += 1
            written[t] = n
            catalog["snapshot_tables"][t]["rows"] = n
        writer.write_json(CATALOG_PATH, catalog)
        return SnapshotResult(catalog=catalog, table_files=written, blockers=dict(self.blockers),
                              synthetic_marker=catalog["synthetic_marker"])

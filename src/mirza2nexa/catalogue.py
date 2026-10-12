"""Catalogue of every known Mirza table: what it is, how it migrates, which columns may leave.

Every table that appears in a dump is resolved against this catalogue (exact, case-sensitive
name first, then declared aliases). The result decides three things:

1. **Staging** — only the *storable* columns of a catalogued table are written to the
   staging database. Columns marked ``secret`` are dropped at parse time and never touch
   disk. For a *generic* spec (no column list, used where the only evidence is the runtime
   DDL) columns whose names look like credentials are dropped the same way.
2. **Migration class** — OPERATIONAL (feeds a live NEXA entity after import), ARCHIVE
   (read-only history), REVIEW (kept, but an operator/owner decision is pending),
   CONFIGURATION (non-secret settings for reference) or EXCLUDED (counted only).
3. **Coverage** — every table, catalogued or not, appears in ``reports/coverage.json`` with
   its decision and reason. A non-empty table that is not catalogued blocks the package:
   nothing may be silently ignored.

Specs are registered by the module that converts them (``mirza2nexa.modules.*``), so the
evidence, the column policy and the conversion code of one domain live in one file.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .sensitivity import column_name_is_secret

OPERATIONAL = "OPERATIONAL"
ARCHIVE = "ARCHIVE"
REVIEW = "REVIEW"
CONFIGURATION = "CONFIGURATION"
EXCLUDED = "EXCLUDED"
CLASSES = (OPERATIONAL, ARCHIVE, REVIEW, CONFIGURATION, EXCLUDED)

CLASS_FA = {
    OPERATIONAL: "عملیاتی (ورود به NEXA پس از تأیید)",
    ARCHIVE: "آرشیو فقط‌خواندنی",
    REVIEW: "نیازمند بررسی/تصمیم مالک",
    CONFIGURATION: "تنظیمات (فقط موارد غیرحساس)",
    EXCLUDED: "منتقل نمی‌شود",
}

PUBLIC, PERSONAL, FINANCIAL, SECRET = "public", "personal", "financial", "secret"


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    sensitivity: str = PUBLIC  # public | personal | financial | secret
    note: str = ""

    @property
    def storable(self) -> bool:
        return self.sensitivity != SECRET


@dataclass(frozen=True)
class TableSpec:
    name: str
    klass: str
    module: str  # converter module key: "core", "payments", "generic", ...
    purpose: str  # English, one line
    purpose_fa: str
    reason: str  # why this class (English, cites evidence ids)
    nexa_target: str
    evidence: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    primary_key: tuple[str, ...] = ()
    # None = generic: columns come from the runtime DDL, filtered by name heuristics.
    columns: tuple[ColumnSpec, ...] | None = None
    open_questions: tuple[str, ...] = ()
    sensitivity: str = PUBLIC  # overall, the strictest of its columns
    # Key-value tables (e.g. PaySetting NamePay/ValuePay): (key column, value column, allowed keys).
    # At staging time the value of any key not in the allow-list is replaced by NULL, so
    # gateway credentials never reach the staging database even transiently.
    kv_filter: tuple[str, str, frozenset[str]] | None = None

    def column(self, name: str) -> ColumnSpec | None:
        if self.columns is None:
            return None
        for c in self.columns:
            if c.name == name:
                return c
        return None

    def storable_columns(self, ddl_columns: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        """Split runtime DDL columns into (stored, dropped_secret, unmapped).

        * declared non-secret columns are stored;
        * declared secret columns are dropped;
        * undeclared columns of a column-listed spec are *not* stored (unknown sensitivity)
          and reported as ``unmapped`` so the catalogue can be extended with evidence;
        * a generic spec stores every column whose name does not look like a credential.
        """
        if self.klass == EXCLUDED:
            return (), tuple(c for c in ddl_columns if column_name_is_secret(c)), ()
        stored, dropped, unmapped = [], [], []
        for c in ddl_columns:
            if self.columns is None:
                # Generic (fork) tables: name heuristics, plus opaque value blobs of settings-like
                # tables, where credentials hide under innocuous names.
                settings_like = any(w in self.name.lower() for w in ("setting", "config", "option"))
                blob = c.lower() in {"value", "values", "data", "content", "json", "config", "setting", "settings"}
                (dropped if column_name_is_secret(c) or (settings_like and blob) else stored).append(c)
                continue
            spec = self.column(c)
            if spec is None:
                unmapped.append(c)
            elif spec.storable:
                stored.append(c)
            else:
                dropped.append(c)
        return tuple(stored), tuple(dropped), tuple(unmapped)


@dataclass
class Catalogue:
    specs: dict[str, TableSpec] = field(default_factory=dict)
    _alias: dict[str, str] = field(default_factory=dict)

    def register(self, spec: TableSpec) -> None:
        if spec.klass not in CLASSES:
            raise ValueError(f"bad class for {spec.name}")
        for n in (spec.name, *spec.aliases):
            if n in self.specs or n in self._alias:
                raise ValueError(f"table {n} registered twice")
        self.specs[spec.name] = spec
        for a in spec.aliases:
            self._alias[a] = spec.name

    def lookup(self, table: str) -> TableSpec | None:
        """Exact, case-sensitive match on the name, then on a declared alias."""
        spec = self.specs.get(table)
        if spec is not None:
            return spec
        canonical = self._alias.get(table)
        return self.specs.get(canonical) if canonical else None


CATALOGUE = Catalogue()

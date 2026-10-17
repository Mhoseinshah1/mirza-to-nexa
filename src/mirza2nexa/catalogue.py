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


#: Column dispositions reported per column in ``reports/coverage.json`` (``column_dispositions``).
STORED = "stored"  # staged for the table's own converter module
CARRIED = "carried"  # staged separately and written verbatim (value-redacted) by the ``carry`` module
MINIMISED = "minimised"  # only a derivative (see ColumnSpec.minimise) is staged; the raw value never is
SECRET_DROPPED = "secret_dropped"  # noqa: S105 - disposition name (never staged; manual-reconfiguration)
EXCLUDED_COLUMN = "excluded"  # column of an EXCLUDED table: counted only, reason in the catalogue
DISPOSITIONS = (STORED, CARRIED, MINIMISED, SECRET_DROPPED, EXCLUDED_COLUMN)


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    sensitivity: str = PUBLIC  # public | personal | financial | secret
    note: str = ""
    # Data minimisation applied at PARSE time (``sensitivity.MINIMISERS``): only the derivative is
    # staged, so the raw value never touches disk. Allowed on secret columns (e.g. a request body
    # reduced to its JSON key names).
    minimise: str | None = None

    @property
    def storable(self) -> bool:
        """The RAW value may be staged."""
        return self.sensitivity != SECRET

    @property
    def staged(self) -> bool:
        """Something (the raw value, or its minimised derivative) is staged."""
        return self.storable or self.minimise is not None


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
    # Columns carried as supplementary history by the ``carry`` module (contract 1.5.0): staged in a
    # separate table so the module that owns ``columns`` (its records and checksums) is unchanged;
    # the staged carry values are folded into the source fingerprint (v3). Secret entries are declared so they are dropped *explicitly*.
    carry: tuple[ColumnSpec, ...] = ()
    # Columns naming the Telegram user a row is about (first one present wins) and the invoice it
    # belongs to; the carry records link to the customer / invoice through them.
    customer_columns: tuple[str, ...] = ()
    invoice_column: str | None = None

    def column(self, name: str) -> ColumnSpec | None:
        for c in self.columns or ():
            if c.name == name:
                return c
        return self.carry_column(name)

    def main_column(self, name: str) -> ColumnSpec | None:
        for c in self.columns or ():
            if c.name == name:
                return c
        return None

    def carry_column(self, name: str) -> ColumnSpec | None:
        for c in self.carry:
            if c.name == name:
                return c
        return None

    def storable_columns(self, ddl_columns: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        """Split runtime DDL columns into (stored, dropped_secret, unmapped) for the OWNING module.

        * declared non-secret columns are stored (``minimise`` is honoured only on ``carry``
          columns, so a minimised entry in ``columns`` is simply dropped);
        * declared secret columns are dropped;
        * undeclared columns of a column-listed spec are *not* stored for the module; the
          :meth:`carry_plan` decides whether the ``carry`` module carries or drops them, so
          ``unmapped`` is always empty since contract 1.5.0 (kept for the report shape);
        * a generic spec stores every column whose name does not look like a credential.
        """
        if self.klass == EXCLUDED:
            return (), tuple(c for c in ddl_columns if column_name_is_secret(c)), ()
        stored, dropped = [], []
        for c in ddl_columns:
            if self.columns is None:
                (dropped if heuristic_secret(self.name, c) else stored).append(c)
                continue
            spec = self.main_column(c)
            if spec is None:
                continue
            if spec.storable and spec.minimise is None:
                stored.append(c)
            else:
                dropped.append(c)
        # Undeclared columns are the carry plan's (``carry_plan``); none is left "unmapped".
        return tuple(stored), tuple(dropped), ()

    def carry_plan(self, ddl_columns: tuple[str, ...], owned: tuple[str, ...]) -> CarryPlan:
        """What the ``carry`` module does with every column the owning module does not handle.

        A declared carry column follows its spec. An *undeclared* column (a newer Mirza fork) is
        carried unless its name looks like a credential (or it is an opaque value blob of a
        settings-like table): then it is dropped and listed for manual reconfiguration. Nothing is
        left unaccounted. EXCLUDED tables carry nothing.
        """
        if self.klass == EXCLUDED:
            return CarryPlan((), (), (), ())
        carried, minimised, dropped, heuristic = [], [], [], []
        for c in ddl_columns:
            if c in owned:
                continue
            spec = self.carry_column(c)
            if spec is None:
                heuristic.append(c)
                (dropped if heuristic_secret(self.name, c) else carried).append(c)
            elif spec.minimise is not None:
                minimised.append(c)
            elif spec.storable:
                carried.append(c)
            else:
                dropped.append(c)
        return CarryPlan(tuple(carried), tuple(minimised), tuple(dropped), tuple(heuristic))


def heuristic_secret(table: str, column: str) -> bool:
    """Name heuristics for a column no spec declares: credential-like names, plus opaque value
    blobs of settings-like tables, where credentials hide under innocuous names."""
    settings_like = any(w in table.lower() for w in ("setting", "config", "option"))
    blob = column.lower() in {"value", "values", "data", "content", "json", "config", "setting", "settings"}
    return column_name_is_secret(column) or (settings_like and blob)


@dataclass(frozen=True)
class CarryPlan:
    carried: tuple[str, ...]  # raw (value-redacted) in the carry record
    minimised: tuple[str, ...]  # derivative only, computed at parse time
    dropped: tuple[str, ...]  # secret: never staged
    heuristic: tuple[str, ...]  # of the above, the ones decided by name (no spec)
    unmapped: tuple[str, ...] = ()  # nothing is ever left unaccounted (kept for the report shape)


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

"""Module interface: how a domain converter plugs into the conversion run.

A module owns a set of :class:`~mirza2nexa.catalogue.TableSpec` entries (registered at import
time) and a ``run(ctx)`` function. Modules run in a fixed order *after* the core customer /
wallet / product / invoice / service conversion, so they can look up customer decisions.

Rules every module must follow (enforced by tests, see ``tests/test_modules_contract.py``):

* never write a wallet, ledger, payment or service mutation — only archive/review records
  whose flags (``affects_wallet``, ``creates_payment``, ``provision`` …) are ``False``;
* every record carries ``record_type``, ``schema``, ``idempotency_key`` (unique within the
  package) and ``provenance``;
* free text from unclassified columns goes through :meth:`ModuleContext.redact`;
* every staged row of every table the module owns is accounted for in coverage as
  exported/archived/review/skipped (``ctx.coverage(...)``).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..catalogue import CATALOGUE, TableSpec
from ..mapping import rules
from ..sensitivity import redact_text

if TYPE_CHECKING:  # pragma: no cover
    from ..convert import Converter
    from ..staging import TableStats


@dataclass(frozen=True)
class Module:
    key: str
    title_fa: str
    run: Callable[[ModuleContext], None]
    order: int = 100


_MODULES: dict[str, Module] = {}


def register_module(module: Module) -> None:
    if module.key in _MODULES:
        raise ValueError(f"module {module.key} registered twice")
    _MODULES[module.key] = module


def modules_in_order() -> list[Module]:
    return sorted(_MODULES.values(), key=lambda m: (m.order, m.key))


class ModuleContext:
    """The only surface a module uses. Wraps the running :class:`Converter`."""

    def __init__(self, conv: Converter):
        self._c = conv
        self.conn: sqlite3.Connection = conv.conn
        self.fingerprint: str = conv.fingerprint
        self.money_unit: str | None = conv.money_unit
        self.currency: str | None = conv.currency
        self.cancel = conv.cancel

    # ---- tables --------------------------------------------------------------------
    def tables_for(self, spec_name: str) -> list[TableStats]:
        """Staged tables (by real dump name) that resolve to catalogue spec ``spec_name``."""
        out = [t for t in self._c.tables.values() if t.spec_name == spec_name and t.sql_name]
        return sorted(out, key=lambda t: t.name)

    def all_tables(self) -> list[TableStats]:
        return sorted(self._c.tables.values(), key=lambda t: t.name)

    def spec(self, name: str) -> TableSpec | None:
        return CATALOGUE.lookup(name)

    def rows(self, stats: TableStats, order_by: list[str] | None = None) -> Iterator[tuple[int, dict[str, Any]]]:
        """Stream staged rows as ``(rowno, {column: text|None})`` in a deterministic order.

        Ordering defaults to the declared primary key (byte order), then dump order.
        """
        cols = list(stats.stored_columns)
        if not cols:
            return
        order = [c for c in (order_by or list(stats.primary_key)) if c in cols]
        q = lambda n: '"' + ("c_" + n).replace('"', '""') + '"'  # noqa: E731
        order_sql = ", ".join([q(c) for c in order] + ["rowno"])
        sql = (f'SELECT rowno, {", ".join(q(c) for c in cols)} FROM "{stats.sql_name}" '  # noqa: S608 - names from staging
               f"ORDER BY {order_sql}")
        cur = self.conn.execute(sql)
        while True:
            batch = cur.fetchmany(1000)
            if not batch:
                return
            for r in batch:
                yield r[0], dict(zip(cols, r[1:], strict=True))

    def count_rows(self, stats: TableStats) -> int:
        return stats.rows

    # ---- customers -----------------------------------------------------------------
    def customer_decision(self, raw_user_id: str | None) -> str | None:
        """``IMPORT`` / review code for a ``user.id`` present in the backup; ``None`` if absent."""
        if raw_user_id is None:
            return None
        row = self.conn.execute("SELECT decision FROM cust WHERE id = ?", (raw_user_id,)).fetchone()
        return row[0] if row else None

    def invoice_exists(self, key: str | None) -> bool:
        if key is None or "invoice" not in self._c.tables or not self._c.tables["invoice"].sql_name:
            return False
        return self.conn.execute("SELECT 1 FROM src_invoice WHERE c_id_invoice = ? LIMIT 1", (key,)).fetchone() \
            is not None

    # ---- output --------------------------------------------------------------------
    def open(self, relpath: str):
        return self._c.open(relpath)

    def review(self, *, kind: str, code: str, entity: str, table: str, pk: str | None, severity: str = "warning",
               data: dict[str, Any] | None = None, message: str | None = None) -> None:
        if message:
            self._c.messages.setdefault(code, message)
        self._c._review(kind=kind, code=code, entity=entity, table=table, pk=pk, severity=severity, data=data)

    def block(self, code: str, n: int = 1, message: str | None = None) -> None:
        if message:
            self._c.blocker_messages.setdefault(code, message)
        self._c.out.block(code, n)

    def warn(self, code: str, n: int = 1, message: str | None = None) -> None:
        if message:
            self._c.warning_messages.setdefault(code, message)
        self._c.out.warn(code, n)

    def count(self, entity: str, key: str, n: int = 1) -> None:
        self._c._count(entity, key, n)

    def tick(self, n: int = 1) -> None:
        self._c._tick(n)

    def provenance(self, table: str, pk: str | None, checksum: str | None = None) -> dict[str, Any]:
        return self._c._provenance(table, pk, checksum)

    def row_checksum(self, table: str, row: dict[str, Any]) -> str:
        return rules.row_checksum(f"m2n:{table}:v1", [row.get(c) for c in sorted(row)])

    def money(self, amount: int | None) -> dict[str, Any]:
        if amount is None:
            return {"amount_minor": None, "currency": None}
        return self._c._money(amount)

    def redact(self, value: str | None, table: str) -> str | None:
        if value is None:
            return None
        cov = self._c.coverage_for(table)

        def hit(kind: str) -> None:
            cov["redacted_cells"] = cov.get("redacted_cells", 0) + 1
            kinds = cov.setdefault("redacted_kinds", {})
            kinds[kind] = kinds.get(kind, 0) + 1

        return redact_text(value, hit)

    def coverage(self, table: str, **fields: Any) -> None:
        """Merge coverage facts for a real dump table name (counts are added)."""
        cov = self._c.coverage_for(table)
        for k, v in fields.items():
            if isinstance(v, int) and not isinstance(v, bool) and k in {"exported", "archived", "review", "skipped"}:
                cov[k] = cov.get(k, 0) + v
            else:
                cov[k] = v

    def reconciliation(self, section: str, data: dict[str, Any]) -> None:
        self._c.out.reconciliation[section] = data

    def mapping(self, entry: dict[str, Any]) -> None:
        self._c.out.mapping.append(entry)

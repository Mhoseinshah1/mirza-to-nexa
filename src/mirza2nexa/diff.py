"""Compare two conversions of (possibly different) backups of the same Mirza installation.

Inputs are two ``records/`` directories (a previous job's staging dir, or a decrypted
previous package). Keys are the records' idempotency keys, which are stable across backups,
so the diff answers: which customers/invoices/services appeared or disappeared, and whose
wallet figure changed. NEXA never applies a changed opening balance automatically
(OQ-LWD-02: a re-run with a different figure is ``idempotency_payload_mismatch``), so every
changed balance becomes a blocking review item rather than a silent top-up.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

ENTITY_FILES = {
    "customers": "customers.jsonl",
    "wallet_balances": "wallet_opening_balances.jsonl",
    "debts": "legacy_debts.jsonl",
    "invoices": "invoices.jsonl",
    "services": "services.jsonl",
    "products": "products.jsonl",
}


def _iter(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def _signed_balances(records_dir: Path) -> Iterator[tuple[str, int]]:
    for r in _iter(records_dir / "customers.jsonl"):
        yield r["telegram_user_id"], 0
    for r in _iter(records_dir / "wallet_opening_balances.jsonl"):
        yield r["telegram_user_id"], int(r["amount_minor"])
    for r in _iter(records_dir / "legacy_debts.jsonl"):
        yield r["legacy_user_id"], -int(r["amount_minor"])


def diff_records(prev_dir: Path, cur_dir: Path, work: Path, *, previous_import_id: str | None,
                 on_changed_balance: Callable[[str, int, int], None] | None = None) -> dict[str, Any]:
    db = sqlite3.connect(work / "diff.sqlite", isolation_level=None)
    try:
        db.execute("CREATE TABLE k (side INTEGER, entity TEXT, key TEXT, PRIMARY KEY (entity, key, side)) WITHOUT ROWID")
        db.execute("CREATE TABLE bal (side INTEGER, tg TEXT, amount INTEGER, PRIMARY KEY (tg, side)) WITHOUT ROWID")
        for side, d in ((0, prev_dir), (1, cur_dir)):
            for entity, fname in ENTITY_FILES.items():
                db.execute("BEGIN")
                db.executemany("INSERT OR IGNORE INTO k VALUES (?, ?, ?)",
                               ((side, entity, r["idempotency_key"]) for r in _iter(d / fname)))
                db.execute("COMMIT")
            db.execute("BEGIN")
            db.executemany(
                "INSERT INTO bal VALUES (?, ?, ?) ON CONFLICT(tg, side) DO UPDATE SET amount = amount + excluded.amount",
                ((side, tg, amt) for tg, amt in _signed_balances(d)))
            db.execute("COMMIT")
        added, removed = {}, {}
        for entity in ENTITY_FILES:
            added[entity] = db.execute(
                "SELECT COUNT(*) FROM k a WHERE a.side=1 AND a.entity=? AND NOT EXISTS "
                "(SELECT 1 FROM k b WHERE b.side=0 AND b.entity=a.entity AND b.key=a.key)", (entity,)).fetchone()[0]
            removed[entity] = db.execute(
                "SELECT COUNT(*) FROM k a WHERE a.side=0 AND a.entity=? AND NOT EXISTS "
                "(SELECT 1 FROM k b WHERE b.side=1 AND b.entity=a.entity AND b.key=a.key)", (entity,)).fetchone()[0]
        changed = 0
        delta_total = 0
        for tg, before, after in db.execute(
                "SELECT a.tg, a.amount, b.amount FROM bal a JOIN bal b ON a.tg=b.tg AND a.side=0 AND b.side=1 "
                "WHERE a.amount <> b.amount ORDER BY a.tg"):
            changed += 1
            delta_total += after - before
            if on_changed_balance:
                on_changed_balance(tg, before, after)
        return {
            "previous_import_id": previous_import_id,
            "added": added,
            "removed": removed,
            "changed": {"wallet_balances": changed},
            "balance_delta_total": str(delta_total),
            "notes": [
                "Changed balances are never applied automatically (NEXA OQ-LWD-02); each is a blocking review item.",
                "Removed records are not deleted in NEXA; an importer must treat absence as 'no new information'.",
            ],
        }
    finally:
        db.close()
        (work / "diff.sqlite").unlink(missing_ok=True)

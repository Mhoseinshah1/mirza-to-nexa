"""wallet_transaction → read-only wallet history + per-customer check against the snapshot.

``wallet_transaction`` exists **only in the deployed fork** (operator column list,
2026-10-10: ``id, id_user, username, service_username, type, amount, balance_before,
balance_after, ref_id, source, reason, created_at``). Neither public source has it
(botmirzapanel@92c0ed06 and mirza_pro@b645db74: ``grep -rn wallet_transaction`` → 0 hits;
``docs/SOURCE_TABLES.md`` Q3). Therefore:

* ``type``/``source`` vocabularies are UNKNOWN — every value normalises to ``UNKNOWN`` and
  is kept raw; the movement direction is *observed* arithmetically, never read from ``type``;
* the sign convention of ``amount`` is UNKNOWN — the arithmetic check accepts both
  ``after − before = +amount`` and ``= −amount`` and reports which one held;
* ``ref_id``'s target is UNKNOWN — ``ref_relation`` is ``UNPROVEN``; how many ``ref_id``
  values happen to equal a ``Payment_report.id``/``id_order`` is a labelled statistic only;
* ``created_at``'s format/zone is UNKNOWN — parsed only into a zone-less local value.

NEXA never replays this table (nexa-bot@9e604def ``docs/migration-opening-balance.md:19``,
``docs/legacy-migration/reconciliation.md:84-85``): the **snapshot ``user.Balance`` is the only
opening figure** and is never changed here. Many public-code Balance changes write no row
anywhere (gifts, commission, cashback, refunds — ``docs/SOURCE_TABLES.md`` Q3) and the table
was introduced late, so a history that differs from the snapshot is an expected evidence gap:
a warning, never a blocker. A duplicated transaction ``id`` is a contradiction and blocks.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from typing import Any

from ..catalogue import ARCHIVE, CATALOGUE, FINANCIAL, PERSONAL, PUBLIC, ColumnSpec, TableSpec
from ..mapping import rules
from ..synth import SynthState, register_synth, sql_str
from .base import Module, ModuleContext, register_module
from .payments import (
    FMT_PHP_SLASH,
    FMT_SQL_DATETIME,
    FMT_UNIX,
    METHOD_VOCAB,
    WALLET_CAUSE_LAST_TX,
    ZONE_NOTE,
    BoundedReviews,
    DistinctCounter,
    col_expr,
    customer_relation,
    parse_time,
    short_text,
    sql_ident,
    time_sort_key,
    wallet_cause_plan,
)
from .payments import TABLE as PAYMENT_TABLE

TABLE = "wallet_transaction"
SCHEMA = "mirza.wallet_transaction.v1"
CHECK_SCHEMA = "mirza.wallet_transaction.check.v1"
EVIDENCE_OPERATOR = "deployed backup column list (operator, 2026-10-10); no public source"

MESSAGES = {
    "WALLET_HISTORY_DIFFERS_FROM_SNAPSHOT": "آخرین مانده تاریخچه کیف پول با موجودی بکاپ (user.Balance) برابر نیست؛ موجودی بکاپ تنها مبنای مانده افتتاحیه است و تغییر نمی‌کند.",
    "WALLET_TX_ARITHMETIC_INCONSISTENT": "در یک تراکنش کیف پول، اختلاف مانده قبل و بعد با مبلغ تراکنش جور نیست.",
    "WALLET_HISTORY_CHAIN_GAP": "مانده قبل از این تراکنش با مانده بعد از تراکنش قبلی همان مشتری برابر نیست (شکاف در تاریخچه).",
    "WALLET_TX_DUPLICATE_SUSPECT": "چند تراکنش کیف پول با شناسه متفاوت ولی همه مقادیر یکسان؛ احتمال ثبت تکراری.",
    "WALLET_TX_CUSTOMER_UNKNOWN": "تراکنش کیف پول به کاربری تعلق دارد که در جدول کاربران بکاپ نیست (یا شناسه خالی است).",
    "WALLET_TX_TYPE_UNKNOWN": "معنای مقادیر ستون type جدول wallet_transaction در هیچ منبع عمومی تعریف نشده؛ همه UNKNOWN ثبت شدند.",
    "WALLET_TX_TIME_FORMAT_UNKNOWN": "قالب زمان برخی تراکنش‌های کیف پول شناخته‌شده نیست؛ مقدار خام حفظ شد.",
}
BLOCKERS = {
    "WALLET_TX_SOURCE_KEY_DUPLICATED": "شناسه (id) تکراری یا خالی در جدول wallet_transaction؛ تناقض مالی — بسته مسدود است.",
    "WALLET_HISTORY_ACCOUNTING_MISMATCH": "تعداد ردیف‌های خروجی تاریخچه کیف پول با ردیف‌های بکاپ برابر نیست؛ بسته مسدود است.",
}
WARNINGS = {
    "WALLET_HISTORY_DIFFERS_FROM_SNAPSHOT": "برای برخی مشتریان، تاریخچه کیف پول به موجودی بکاپ نمی‌رسد؛ تاریخچه فقط شاهد است و موجودی بکاپ تغییر نمی‌کند.",
    "WALLET_HISTORY_EVIDENCE_ONLY": "تاریخچه کیف پول (wallet_transaction) فقط آرشیو و شاهد است؛ هیچ تراکنشی در دفتر کیف پول NEXA ثبت نمی‌شود.",
}

EXPLANATION = (
    "wallet_transaction exists only in the deployed fork; its type/source vocabulary, amount sign convention, ref_id "
    "target and time zone are unproven. It was introduced after many balances already existed, and many Mirza "
    "balance changes (gifts, referral commission, cashback, refunds) are recorded nowhere, so the history may be "
    "incomplete. It is evidence only: the snapshot user.Balance is the only opening figure and is never changed.")
EXPLANATION_FA = (
    "جدول wallet_transaction فقط در نسخه مستقر وجود دارد و معنای type/source، علامت مبلغ، مقصد ref_id و منطقه زمانی آن "
    "اثبات نشده است. این جدول بعداً اضافه شده و بسیاری از تغییرات موجودی (هدیه، پورسانت، کش‌بک، بازپرداخت) در هیچ "
    "جدولی ثبت نمی‌شوند؛ پس تاریخچه ممکن است ناقص باشد. این داده فقط شاهد است: تنها مبنای مانده افتتاحیه، موجودی "
    "user.Balance بکاپ است و هرگز تغییر نمی‌کند.")

CATALOGUE.register(TableSpec(
    name=TABLE, klass=ARCHIVE, module="wallet_history",
    purpose="Per-user wallet movements with balance before/after (deployed fork only)",
    purpose_fa="تاریخچه تراکنش‌های کیف پول (فقط نسخه مستقر)",
    reason=("Evidence only: NEXA never replays wallet_transaction (nexa-bot docs/migration-opening-balance.md:19); "
            "semantics unproven (docs/SOURCE_TABLES.md Q3)"),
    nexa_target="legacy_wallet_history (read-only archive proposal; never ledger entries)",
    evidence=(EVIDENCE_OPERATOR,
              "botmirzapanel@92c0ed06 + mirza_pro@b645db74: grep wallet_transaction → 0 hits",
              "nexa-bot@9e604def docs/migration-opening-balance.md:19; docs/legacy-migration/reconciliation.md:84-85",
              "docs/SOURCE_TABLES.md#wallet_transaction; docs/evidence/mirza_tables.json"),
    primary_key=("id",),
    columns=(
        ColumnSpec("id", PUBLIC, "assumed row id (name only)"),
        ColumnSpec("id_user", PERSONAL, "user.id by name only — UNPROVEN"),
        ColumnSpec("username", PERSONAL),
        ColumnSpec("service_username", PERSONAL, "invoice.username by name only — UNPROVEN"),
        ColumnSpec("type", PUBLIC, "vocabulary UNKNOWN"),
        ColumnSpec("amount", FINANCIAL, "sign convention UNKNOWN"),
        ColumnSpec("balance_before", FINANCIAL),
        ColumnSpec("balance_after", FINANCIAL),
        ColumnSpec("ref_id", PUBLIC, "target UNKNOWN"),
        ColumnSpec("source", PUBLIC, "vocabulary UNKNOWN"),
        ColumnSpec("reason", PERSONAL, "free text, redacted"),
        ColumnSpec("created_at", PUBLIC, "format/zone UNKNOWN"),
    ),
    open_questions=(
        "type/source vocabularies and the sign convention of amount",
        "ref_id target (Payment_report.id? id_order? invoice?)",
        "created_at type and time zone",
        "when the table was introduced, i.e. which balances predate any history",
        "class: owner decision whether to keep as ARCHIVE (docs/SOURCE_TABLES.md proposes REVIEW/OWNER_DECISION)",
    ),
    sensitivity=FINANCIAL,
))


def _register_messages(ctx: ModuleContext) -> None:
    ctx.register_messages(MESSAGES, BLOCKERS, WARNINGS)


def _int(raw: str | None) -> int | None:
    """Signed whole number (``parseLegacyBalance`` grammar), else None. Never rounded."""
    v = rules.parse_balance(raw)
    return v if v is not None and abs(v) <= rules.MAX_MONEY_AMOUNT_MINOR else None


def _money(ctx: ModuleContext, raw: str | None) -> dict[str, Any]:
    v = _int(raw)
    note = "ABSENT" if raw is None else "OK" if v is not None else "NOT_A_SIGNED_INTEGER"
    return {"raw": raw, "parse_note": note, "value_minor": None if v is None else str(v), "currency": ctx.currency
            if v is not None else None}


def _arithmetic(amount: int | None, before: int | None, after: int | None) -> tuple[str, str | None]:
    """(verdict, amount sign convention). The direction is the *observed* movement
    ``after - before``; ``amount`` must equal it either signed (``+amount``) or as a magnitude
    with the opposite sign (``-amount``). The column's convention is UNKNOWN, so both count."""
    if amount is None or before is None or after is None:
        return "UNPARSEABLE", None
    diff = after - before
    if diff == 0 and amount == 0:
        return "ARITHMETIC_NO_CHANGE", None
    if diff not in (amount, -amount):
        return "INCONSISTENT", None
    convention = "AFTER_MINUS_BEFORE_EQUALS_PLUS_AMOUNT" if diff == amount else "AFTER_MINUS_BEFORE_EQUALS_MINUS_AMOUNT"
    return ("ARITHMETIC_CREDIT" if diff > 0 else "ARITHMETIC_DEBIT"), convention


def _time_allow(_stats) -> frozenset[str]:
    """Formats accepted for created_at. The dump's own DDL type is evidence for SQL DATETIME /
    epoch; Mirza's PHP ``date('Y/m/d H:i:s')`` convention is accepted for text columns but is
    reported as INFERRED (the table is in no public source)."""
    return frozenset({FMT_PHP_SLASH, FMT_SQL_DATETIME, FMT_UNIX})


def _format_evidence(stats, fmt: str | None) -> str | None:
    if fmt is None:
        return None
    ddl = (stats.column_types.get("created_at") or "").lower()
    if fmt == FMT_SQL_DATETIME and ddl.startswith(("datetime", "timestamp")):
        return "DDL_TYPE"
    if fmt == FMT_UNIX and ("int" in ddl):
        return "DDL_TYPE"
    return "INFERRED"


def _check_key(uid: str, tg: str | None) -> str:
    if tg is not None:
        return f"legacy:wallet-history-check:{tg}"
    return "legacy:wallet-history-check:raw:" + hashlib.sha256(uid.encode("utf-8", "surrogatepass")).hexdigest()[:32]


def _snapshot(ctx: ModuleContext, uid: str) -> tuple[int | None, str | None, str]:
    """(balance, raw, note) of ``user.Balance`` for ``uid`` — read only, never written."""
    rows = ctx.conn.execute("SELECT c_Balance FROM src_user WHERE c_id = ? LIMIT 2", (uid,)).fetchall()
    if not rows:
        return None, None, "CUSTOMER_NOT_IN_BACKUP"
    if len(rows) > 1:
        return None, None, "SNAPSHOT_AMBIGUOUS_DUPLICATE_USER"
    raw = rows[0][0]
    v = rules.parse_balance(raw)
    return (v, raw, "OK") if v is not None else (None, raw, "SNAPSHOT_UNREADABLE")


def _convert(ctx: ModuleContext, stats) -> None:
    conn = ctx.conn
    t = sql_ident(stats.sql_name)
    has_id = "id" in stats.stored_columns
    dup_ids: set[str] = set()
    null_ids = 0
    if has_id:
        dup_ids = {r[0] for r in conn.execute(
            f"SELECT c_id FROM {t} WHERE c_id IS NOT NULL GROUP BY c_id HAVING COUNT(*) > 1")}  # noqa: S608
        null_ids = conn.execute(f"SELECT COUNT(*) FROM {t} WHERE c_id IS NULL").fetchone()[0]  # noqa: S608
    allow = _time_allow(stats)
    reviews = BoundedReviews(ctx)
    types = DistinctCounter()
    sources = DistinctCounter()
    time_notes: dict[str, int] = {}
    conn.execute("DROP TABLE IF EXISTS m2n_wtx")
    conn.execute("CREATE TEMP TABLE m2n_wtx (rowno INTEGER PRIMARY KEY, uid TEXT, tkey TEXT, idnum INTEGER, "
                 "idtext TEXT, fmt TEXT, dkey TEXT, before_v TEXT, after_v TEXT, rec TEXT)")
    pending: list[tuple] = []
    dup_rows = 0
    for rowno, r in ctx.rows(stats):
        ctx.tick()
        tid = r.get("id")
        if not has_id or tid is None or tid in dup_ids:
            dup_rows += 1
            reviews.add(kind="wallet_history", code="SOURCE_KEY_DUPLICATED", entity="wallet_history", table=stats.name,
                        pk=tid, severity="blocker", data={"duplicate": tid is not None})
            continue
        amount, before, after = _int(r.get("amount")), _int(r.get("balance_before")), _int(r.get("balance_after"))
        arith, convention = _arithmetic(amount, before, after)
        created = parse_time(r.get("created_at"), allow)
        created["format_evidence"] = _format_evidence(stats, created["format"])
        time_notes[created["note"]] = time_notes.get(created["note"], 0) + 1
        type_raw = r.get("type")
        types.add("<NULL>" if type_raw is None else type_raw)
        src_raw = r.get("source")
        sources.add("<NULL>" if src_raw is None else src_raw)
        cust = customer_relation(ctx, r.get("id_user"))
        dkey = hashlib.sha256(rules.js_json([r.get(c) for c in (
            "id_user", "type", "amount", "balance_before", "balance_after", "ref_id", "created_at")]).encode(
            "utf-8", "surrogatepass")).hexdigest()
        rec = {
            "record_type": "legacy_wallet_transaction",
            "schema": SCHEMA,
            "idempotency_key": f"legacy:wallet-tx:{tid}",
            "transaction_id": tid,
            "customer": cust,
            "username": ctx.redact(r.get("username"), stats.name),
            "service_username": ctx.redact(r.get("service_username"), stats.name),
            "type": {"raw": type_raw, "normalized": "UNKNOWN"},  # no evidenced vocabulary
            "source": {"raw": ctx.redact(src_raw, stats.name), "normalized": "UNKNOWN"},
            "reason": ctx.redact(r.get("reason"), stats.name),
            "amount": _money(ctx, r.get("amount")),
            "balance_before": _money(ctx, r.get("balance_before")),
            "balance_after": _money(ctx, r.get("balance_after")),
            "arithmetic": arith,
            "amount_convention_observed": convention,
            "ref_id": {"raw": ctx.redact(r.get("ref_id"), stats.name), "ref_relation": "UNPROVEN"},
            "created_at": {**created, "zone": ZONE_NOTE},
            "affects_wallet": False,
            "applied_to_balance": False,
            "creates_payment": False,
            "applies_to_live_state": False,
            "provenance": ctx.provenance(stats.name, tid, ctx.row_checksum(stats.name, r)),
        }
        idnum = int(tid) if tid.isascii() and tid.isdigit() and len(tid) <= 18 else None
        pending.append((rowno, r.get("id_user"), time_sort_key(created), idnum, tid, created["format"], dkey,
                        None if before is None else str(before), None if after is None else str(after),
                        json.dumps(rec, ensure_ascii=False, sort_keys=True)))
        if len(pending) >= 1000:
            conn.executemany("INSERT INTO m2n_wtx VALUES (?,?,?,?,?,?,?,?,?,?)", pending)
            pending.clear()
    if pending:
        conn.executemany("INSERT INTO m2n_wtx VALUES (?,?,?,?,?,?,?,?,?,?)", pending)
    conn.execute("CREATE INDEX m2n_wtx_uid ON m2n_wtx (uid)")
    conn.execute("DROP TABLE IF EXISTS m2n_wtx_dup")
    conn.execute("CREATE TEMP TABLE m2n_wtx_dup AS SELECT dkey, COUNT(*) AS n, MIN(rowno) AS first_rowno "
                 "FROM m2n_wtx GROUP BY dkey HAVING COUNT(*) > 1")
    conn.execute("CREATE INDEX m2n_wtx_dup_k ON m2n_wtx_dup (dkey)")

    sink = ctx.open("records/wallet_history.jsonl")
    checks = ctx.open("records/wallet_history_checks.jsonl")
    credits = {"count": 0, "amount": 0}
    debits = {"count": 0, "amount": 0}
    arith_counts: dict[str, int] = {}
    conventions: dict[str, int] = {}
    chain_counts = {"FIRST": 0, "CHAIN_OK": 0, "CHAIN_GAP": 0, "CHAIN_UNPARSEABLE": 0}
    verdicts: dict[str, int] = {}
    total_delta = 0
    suspect_groups = suspect_rows = 0
    written = 0
    customers_with_history = 0
    mixed_formats = 0
    no_customer_rows = 0

    state: dict[str, Any] = {}
    diff_items: list[dict[str, Any]] = []

    def close_group() -> None:
        nonlocal customers_with_history, total_delta, mixed_formats
        if not state:
            return
        uid = state["uid"]
        if uid is None or rules.js_trim(uid) == "":
            return
        customers_with_history += 1
        if len(state["formats"]) > 1:
            mixed_formats += 1
        snap, snap_raw, snap_note = _snapshot(ctx, uid)
        last_after = state["last_after"]
        if snap is None:
            verdict = "NO_SNAPSHOT"
        elif state.get("order_unproven"):
            verdict = "ORDER_UNPROVEN"
        elif last_after is None:
            verdict = "HISTORY_UNPARSEABLE"
        elif last_after == snap:
            verdict = "MATCHES_SNAPSHOT"
        else:
            verdict = "DIFFERS_FROM_SNAPSHOT"
        verdicts[verdict] = verdicts.get(verdict, 0) + 1
        delta = (snap - last_after) if verdict == "DIFFERS_FROM_SNAPSHOT" else None
        if delta is not None:
            total_delta += delta
        cust = state["cust"]
        checks.write({
            "record_type": "legacy_wallet_history_check",
            "schema": CHECK_SCHEMA,
            "idempotency_key": _check_key(uid, cust["telegram_user_id"]),
            "customer": cust,
            "transactions": state["n"],
            "chain_gaps": state["gaps"],
            "inconsistent_rows": state["inconsistent"],
            "snapshot_balance": {"raw": snap_raw, "note": snap_note, **ctx.money(snap)},
            "history_last_balance_after": {"transaction_id": state["last_id"], **ctx.money(last_after)},
            "verdict": verdict,
            "delta_snapshot_minus_history_minor": None if delta is None else str(delta),
            "snapshot_is_the_opening_figure": True,
            "affects_wallet": False,
            "applied_to_balance": False,
            "applies_to_live_state": False,
            "derived_from": ["wallet_transaction (history)", "user.Balance (snapshot, read only)"],
            "provenance": ctx.provenance(stats.name, None, None),
        })
        if verdict in ("DIFFERS_FROM_SNAPSHOT", "ORDER_UNPROVEN") and snap is not None:
            diff_items.append(_difference_item(conn, uid, cust, verdict, snap, last_after, delta, state))
        if verdict == "DIFFERS_FROM_SNAPSHOT":
            reviews.add(kind="wallet_history", code="WALLET_HISTORY_DIFFERS_FROM_SNAPSHOT", entity="wallet_history",
                        table=stats.name, pk=cust["telegram_user_id"] or None, severity="warning",
                        data={"transactions": state["n"], "delta_minor": str(delta), "chain_gaps": state["gaps"]})

    # A customer whose times are not all parseable in ONE format cannot be ordered by time
    # (NULL keys and local-vs-epoch prefixes would interleave wrongly): order those by id and
    # mark the verdict ORDER_UNPROVEN instead of trusting a time-sorted "last" row.
    conn.execute("DROP TABLE IF EXISTS m2n_wtx_mixed")
    conn.execute("CREATE TEMP TABLE m2n_wtx_mixed AS SELECT uid FROM m2n_wtx GROUP BY uid "
                 "HAVING SUM(tkey IS NULL) > 0 OR COUNT(DISTINCT substr(tkey, 1, 1)) > 1")
    conn.execute("CREATE INDEX m2n_wtx_mixed_uid ON m2n_wtx_mixed (uid)")
    cur = conn.execute(
        "SELECT w.uid, w.idtext, w.before_v, w.after_v, w.fmt, w.rec, d.n, d.first_rowno, w.rowno, "
        "m.uid IS NOT NULL AS mixed, w.tkey FROM m2n_wtx w "
        "LEFT JOIN m2n_wtx_dup d ON d.dkey = w.dkey "
        "LEFT JOIN m2n_wtx_mixed m ON m.uid = w.uid "
        "ORDER BY w.uid IS NULL, w.uid, CASE WHEN m.uid IS NULL THEN w.tkey END, "
        "w.idnum IS NULL, w.idnum, w.idtext, w.rowno")
    for uid, tid, before_s, after_s, fmt, rec_json, dn, dfirst, rowno, mixed, tkey in cur:
        ctx.tick()
        if not state or state["uid"] != uid:
            close_group()
            state.clear()
            state.update({"uid": uid, "cust": customer_relation(ctx, uid), "n": 0, "gaps": 0, "inconsistent": 0,
                          "prev_after": None, "last_after": None, "last_id": None, "formats": set(), "first": True,
                          "order_unproven": bool(mixed)})
        rec = json.loads(rec_json)
        before = int(before_s) if before_s is not None else None
        after = int(after_s) if after_s is not None else None
        if state["first"]:
            chain = "FIRST"
        elif state["prev_after"] is None or before is None:
            chain = "CHAIN_UNPARSEABLE"
        elif state["prev_after"] == before:
            chain = "CHAIN_OK"
        else:
            chain = "CHAIN_GAP"
        state["first"] = False
        chain_counts[chain] += 1
        rec["chain"] = chain
        rec["chain_order"] = ("id (times missing or in mixed formats: ORDER_UNPROVEN)" if mixed
                              else "created_at (zone-less), then id")
        rec["duplicate_suspect"] = dn is not None
        if dn is not None:
            suspect_rows += 1
            if dfirst == rowno:
                suspect_groups += 1
                reviews.add(kind="wallet_history", code="WALLET_TX_DUPLICATE_SUSPECT", entity="wallet_history",
                            table=stats.name, pk=tid, severity="warning", data={"identical_rows": dn})
        a = rec["arithmetic"]
        arith_counts[a] = arith_counts.get(a, 0) + 1
        conv = rec["amount_convention_observed"]
        if conv is not None:
            conventions[conv] = conventions.get(conv, 0) + 1
        amount_v = rec["amount"]["value_minor"]
        if a == "ARITHMETIC_CREDIT":
            credits["count"] += 1
            credits["amount"] += abs(int(amount_v))
        elif a == "ARITHMETIC_DEBIT":
            debits["count"] += 1
            debits["amount"] += abs(int(amount_v))
        elif a == "INCONSISTENT":
            state["inconsistent"] += 1
            reviews.add(kind="wallet_history", code="WALLET_TX_ARITHMETIC_INCONSISTENT", entity="wallet_history",
                        table=stats.name, pk=tid, severity="warning")
        if chain == "CHAIN_GAP":
            state["gaps"] += 1
            reviews.add(kind="wallet_history", code="WALLET_HISTORY_CHAIN_GAP", entity="wallet_history",
                        table=stats.name, pk=tid, severity="warning")
        if rec["customer"]["relation"] in ("CUSTOMER_NOT_IN_BACKUP", "MISSING"):
            reviews.add(kind="wallet_history", code="WALLET_TX_CUSTOMER_UNKNOWN", entity="wallet_history",
                        table=stats.name, pk=tid, severity="warning", data={"relation": rec["customer"]["relation"]})
            if rec["customer"]["relation"] == "MISSING":
                no_customer_rows += 1
        if fmt is not None:
            state["formats"].add(fmt)
        state["n"] += 1
        state["prev_after"] = after
        state["last_after"] = after
        state["last_id"] = tid
        state["last_tkey"] = tkey
        sink.write(rec)
        written += 1
    close_group()
    reviews.flush()
    analyse_differences(ctx, diff_items)
    if types.rows:
        top = sorted(types.values.items(), key=lambda kv: (-kv[1], kv[0]))[:20]
        ctx.review(kind="wallet_history", code="WALLET_TX_TYPE_UNKNOWN", entity="wallet_history", table=stats.name,
                   pk=None, severity="warning",
                   data={"rows": types.rows, "distinct_values": len(types.values) + (1 if types.other else 0),
                         "top_values": [{"type_raw": short_text(ctx, v, stats.name), "rows": n} for v, n in top]})
    bad_time = time_notes.get("FORMAT_UNKNOWN", 0) + time_notes.get("INVALID_DATE", 0) + time_notes.get(
        "OUT_OF_RANGE", 0)
    if bad_time:
        ctx.review(kind="wallet_history", code="WALLET_TX_TIME_FORMAT_UNKNOWN", entity="wallet_history",
                   table=stats.name, pk=None, severity="warning", data={"rows": bad_time})

    # Customers (imported) without any history, and how many of them hold a non-zero snapshot.
    without = without_nonzero = 0
    for (bal,) in conn.execute(
            "SELECT u.c_Balance FROM cust c JOIN src_user u ON u.c_id = c.id WHERE c.decision = 'IMPORT' "
            "AND NOT EXISTS (SELECT 1 FROM m2n_wtx w WHERE w.uid = c.id)"):
        without += 1
        v = rules.parse_balance(bal)
        if v:
            without_nonzero += 1
    # ref_id statistics only (no relation is claimed).
    ref_rows = conn.execute(
        f"SELECT COUNT(*) FROM {t} WHERE {col_expr(stats, 'ref_id')} IS NOT NULL "  # noqa: S608
        f"AND trim({col_expr(stats, 'ref_id')}) <> ''").fetchone()[0]
    match_id = match_order = None
    pay = ctx.tables_for(PAYMENT_TABLE)
    if pay and "ref_id" in stats.stored_columns:
        p = pay[0]
        pt = sql_ident(p.sql_name)
        if "id" in p.stored_columns:
            match_id = conn.execute(
                f"SELECT COUNT(*) FROM {t} WHERE c_ref_id IN (SELECT c_id FROM {pt})").fetchone()[0]  # noqa: S608
        if "id_order" in p.stored_columns:
            match_order = conn.execute(
                # IN (subquery) → one pass with an ephemeral index (no correlated full scans).
                f"SELECT COUNT(*) FROM {t} WHERE trim(c_ref_id) <> '' AND c_ref_id IN "  # noqa: S608
                f"(SELECT c_id_order FROM {pt} WHERE c_id_order IS NOT NULL)").fetchone()[0]

    key_problems = len(dup_ids) + (1 if null_ids else 0) + (0 if has_id else 1)
    if key_problems:
        ctx.block("WALLET_TX_SOURCE_KEY_DUPLICATED", key_problems)
    balanced = written + dup_rows == stats.rows
    if not balanced:
        ctx.block("WALLET_HISTORY_ACCOUNTING_MISMATCH")
    differs = verdicts.get("DIFFERS_FROM_SNAPSHOT", 0)
    if differs:
        ctx.warn("WALLET_HISTORY_DIFFERS_FROM_SNAPSHOT", differs)
    ctx.warn("WALLET_HISTORY_EVIDENCE_ONLY")
    ctx.count("wallet_history", "source", stats.rows)
    ctx.count("wallet_history", "converted", written)
    ctx.coverage(stats.name, archived=written, review=dup_rows, status="archived")
    conn.execute("DROP TABLE IF EXISTS m2n_wtx_dup")
    conn.execute("DROP TABLE IF EXISTS m2n_wtx")
    ctx.reconciliation("wallet_history", {
        "source_table": stats.name,
        "source_rows": stats.rows,
        "archived": written,
        "duplicate_id_rows": dup_rows,
        "duplicate_ids": len(dup_ids),
        "null_ids": null_ids,
        "equation": "source_rows = archived + duplicate_id_rows",
        "balanced": balanced,
        "unit": ctx.money_unit, "currency": ctx.currency,
        "credits_observed": {"count": credits["count"], "amount_minor": str(credits["amount"])},
        "debits_observed": {"count": debits["count"], "amount_minor": str(debits["amount"])},
        "arithmetic": dict(sorted(arith_counts.items())),
        "arithmetic_equation": ("consistent iff after-before = +amount or -amount (sign convention UNKNOWN); "
                                "CREDIT if after > before, DEBIT if after < before"),
        "amount_conventions_observed": dict(sorted(conventions.items())),
        "inconsistent_rows": arith_counts.get("INCONSISTENT", 0),
        "unparseable_rows": arith_counts.get("UNPARSEABLE", 0),
        "chain": chain_counts,
        "chain_equation": "per customer ordered by (created_at, id): balance_before(n) = balance_after(n-1)",
        "chain_gaps": chain_counts["CHAIN_GAP"],
        "duplicate_suspects": {"groups": suspect_groups, "rows": suspect_rows},
        "types": {"normalized": "UNKNOWN (no evidenced vocabulary)", "rows": types.rows,
                  "distinct_values": len(types.values) + (1 if types.other else 0)},
        "sources": {"normalized": "UNKNOWN (no evidenced vocabulary)",
                    "distinct_values": len(sources.values) + (1 if sources.other else 0)},
        "ref_id": {"relation": "UNPROVEN", "rows_with_ref_id": ref_rows,
                   "statistic_only_equal_to_a_payment_report_id": match_id,
                   "statistic_only_equal_to_a_payment_report_id_order": match_order,
                   "note": "Coincidence counts of equal text; no relation is claimed or used."},
        "created_at": {"ddl_type": stats.column_types.get("created_at"), "notes": dict(sorted(time_notes.items())),
                       "customers_with_mixed_formats": mixed_formats},
        "customers": {"with_history": customers_with_history,
                      "verdicts": dict(sorted(verdicts.items())),
                      "matches_snapshot": verdicts.get("MATCHES_SNAPSHOT", 0),
                      "differs_from_snapshot": differs,
                      "imported_without_history": without,
                      "imported_without_history_nonzero_snapshot": without_nonzero,
                      "rows_without_customer_id": no_customer_rows},
        "total_delta_snapshot_minus_history_minor": str(total_delta),
        "delta_equation": "delta = snapshot user.Balance - last balance_after (DIFFERS_FROM_SNAPSHOT customers only)",
        "explanation": EXPLANATION,
        "explanation_fa": EXPLANATION_FA,
    })


# ---- Phase 3: why does a history differ from the snapshot? ------------------------------------
# Evidence-based only. A difference is put in a cause category with a certainty:
#   PROVEN      delta = snapshot - last balance_after equals EXACTLY the signed sum of the evidenced
#               balance-changing events placed after the last history row, and no event is uncertain
#               (in the time window, unplaceable, unquantified or with an unreadable amount);
#   PROBABLE    some evidence fits (an exact sum that needs uncertain events, a 10x unit pattern, a
#               tied last timestamp) or activity exists after the history but does not add up;
#   UNEXPLAINED no observable evidence at all.
# The opening balance (user.Balance) is never changed and no event is ever re-applied.
DIFF_SCHEMA = "mirza.wallet_transaction.difference.v1"
DIFF_RECORD_TYPE = "legacy_wallet_difference"
EVIDENCE_REFS_LIMIT = 50
# Local wall times have no proven zone (ZONE_NOTE). A local time L is some instant in
# [L - 14 h, L + 12 h] (every UTC offset in use is between -12:00 and +14:00), an epoch is exact.
# An event is AFTER the last history row only if its earliest instant is later than the row's
# latest one, BEFORE only if its latest is earlier, otherwise IN_WINDOW. No zone is guessed.
OFFSET_MIN_S, OFFSET_MAX_S = -12 * 3600, 14 * 3600
_EPOCH = datetime(1970, 1, 1)  # noqa: DTZ001 - naive arithmetic on zone-less wall times

# Quantified mechanisms: signed effect on user.Balance per evidenced Mirza code path.
GROUPS = ("TOPUP", "ADMIN_ADJUSTMENT", "PURCHASE", "SERVICE_OPERATION", "WHEEL_PRIZE")
GROUP_CAUSE = {"TOPUP": "UNLOGGED_TOPUPS", "ADMIN_ADJUSTMENT": "UNLOGGED_ADMIN_ADJUSTMENTS",
               "PURCHASE": "UNLOGGED_PURCHASES", "SERVICE_OPERATION": "UNLOGGED_SERVICE_OPERATIONS",
               "WHEEL_PRIZE": "UNLOGGED_WHEEL_PRIZES"}
# Rows that prove a Balance change happened but carry no amount (or no time).
UNQUANTIFIED = ("REFERRAL_GIFT", "GIFT_CODE", "PAYMENT_UNKNOWN_METHOD")
CAUSES = (*GROUP_CAUSE.values(), "UNLOGGED_MIXED_ACTIVITY", "ORDER_UNPROVEN", "UNIT_SUSPECT",
          "ACTIVITY_AFTER_HISTORY_INEXACT", "UNEXPLAINED")
CERTAINTIES = ("PROVEN", "PROBABLE", "UNEXPLAINED")
# service_other types that debit the wallet when paid from it (mirza_pro index.php:1821, 2093, 2294,
# 2635, 5973, 6688). Gifts (price 0), admin renewals and transfers do not touch Balance.
SERVICE_DEBIT_TYPES = ("extend_user", "extra_user", "extra_time_user", "extra_not_user", "extends_not_user",
                       "change_location")
_PK_SAFE = re.compile(r"[0-9A-Za-z_-]{1,64}")

DIFF_EXPLANATION = (
    "Each customer whose wallet_transaction history does not end at user.Balance is analysed against observable "
    "evidence after the last history row: paid Payment_report rows (top-ups, admin adjustments), invoices "
    "(purchases), service_other debits, wheel prizes, referral and gift-code rows. PROVEN means the difference "
    "equals exactly the signed sum of those events with nothing uncertain; it explains the gap, it does not change "
    "anything. user.Balance stays the only opening figure and no history is re-applied.")
DIFF_EXPLANATION_FA = (
    "برای هر مشتری که تاریخچه کیف پولش به موجودی بکاپ نمی‌رسد، شواهد بعد از آخرین تراکنش (پرداخت‌های موفق، تنظیم "
    "ادمین، خرید، تمدید، گردونه، معرفی، کد هدیه) بررسی می‌شود. «اثبات‌شده» یعنی اختلاف دقیقاً برابر جمع این رویدادهاست؛ "
    "این فقط توضیح است و موجودی بکاپ هرگز تغییر نمی‌کند.")


def _interval(tkey: str | None) -> tuple[int, int] | None:
    """Possible instants (seconds) of a parsed time key, without guessing a zone."""
    if tkey is None:
        return None
    if tkey[0] == "U":
        t = int(tkey[1:])
        return t, t
    local = int((datetime.fromisoformat(tkey[1:]) - _EPOCH).total_seconds())
    return local - OFFSET_MAX_S, local - OFFSET_MIN_S


def _place(ev: tuple[int, int] | None, last: tuple[int, int] | None) -> str:
    if ev is None or last is None:
        return "UNPLACEABLE"
    if ev[0] > last[1]:
        return "AFTER"
    if ev[1] < last[0]:
        return "BEFORE"
    return "IN_WINDOW"


def _difference_item(conn, uid: str, cust: dict[str, Any], verdict: str, snap: int, last_after: int | None,
                     delta: int | None, state: dict[str, Any]) -> dict[str, Any]:
    """What the analysis needs from one customer's history (read while the history is staged)."""
    tkey = state.get("last_tkey")
    if verdict == "ORDER_UNPROVEN":
        # Some row ends exactly at the snapshot → an order exists under which the history matches.
        match = conn.execute("SELECT 1 FROM m2n_wtx WHERE uid = ? AND after_v = ? LIMIT 1",
                             (uid, str(snap))).fetchone() is not None
    else:
        # Another row with the SAME (last) timestamp ends at the snapshot: only the id tie-break
        # made the history differ.
        match = tkey is not None and conn.execute(
            "SELECT 1 FROM m2n_wtx WHERE uid = ? AND tkey = ? AND after_v = ? LIMIT 1",
            (uid, tkey, str(snap))).fetchone() is not None
    return {"uid": uid, "cust": cust, "verdict": verdict, "snapshot": snap, "last_after": last_after,
            "delta": delta, "last_tkey": tkey, "last_id": state.get("last_id"), "transactions": state["n"],
            "order_match": match}


def _pk(ctx: ModuleContext, table: str, pk: str | None) -> dict[str, Any]:
    if pk is not None and _PK_SAFE.fullmatch(pk):
        return {"table": table, "pk": pk}
    return {"table": table, "pk": None, "pk_ref": ctx.secret_ref(pk, "pk")}


def _events(ctx: ModuleContext) -> tuple[dict[str, list[tuple]], dict[str, str]]:
    """uid → [(kind, table, pk, signed amount | None, time key | None)] for the customers in
    ``m2n_wd_uid``; plus which evidence sources were usable."""
    conn = ctx.conn
    ev: dict[str, list[tuple]] = {}
    sources: dict[str, str] = {}
    slash = frozenset({FMT_PHP_SLASH, FMT_SQL_DATETIME, FMT_UNIX})

    def table(spec: str, needed: set[str]):
        st = next(iter(ctx.tables_for(spec)), None)
        if st is None:
            sources[spec] = "ABSENT"
            return None
        if not needed <= set(st.stored_columns):
            sources[spec] = "COLUMNS_MISSING"
            return None
        sources[spec] = "USED"
        return st

    def add(uid: str, item: tuple) -> None:
        ctx.tick()
        ev.setdefault(uid, []).append(item)

    def tk(raw: str | None) -> str | None:
        return time_sort_key(parse_time(raw, slash))

    st = table(PAYMENT_TABLE, {"id", "id_user", "price", "payment_Status", "Payment_Method", "time"})
    if st:
        for pid, uid, price, m_raw, t in conn.execute(
                f"SELECT c_id, c_id_user, c_price, c_Payment_Method, c_time FROM {sql_ident(st.sql_name)} "  # noqa: S608
                "WHERE c_payment_Status = 'paid' AND c_id_user IN (SELECT uid FROM m2n_wd_uid) ORDER BY rowno"):
            method = METHOD_VOCAB[m_raw][0] if m_raw in METHOD_VOCAB else "UNKNOWN"
            amount = rules.parse_price(price)[0]
            if method == "UNKNOWN":
                add(uid, ("PAYMENT_UNKNOWN_METHOD", st.name, pid, None, tk(t)))
            elif method == "ADMIN_DEBIT_ADJUSTMENT":  # stored positive, debits (admin.php:3618)
                add(uid, ("ADMIN_ADJUSTMENT", st.name, pid, None if amount is None else -amount, tk(t)))
            elif method == "ADMIN_CREDIT_ADJUSTMENT":
                add(uid, ("ADMIN_ADJUSTMENT", st.name, pid, amount, tk(t)))
            else:
                add(uid, ("TOPUP", st.name, pid, amount, tk(t)))
    st = table("invoice", {"id_invoice", "id_user", "price_product", "time_sell"})
    if st:
        status = col_expr(st, "Status")
        for key, uid, price, ts in conn.execute(
                f"SELECT c_id_invoice, c_id_user, c_price_product, c_time_sell FROM {sql_ident(st.sql_name)} "  # noqa: S608
                f"WHERE c_id_user IN (SELECT uid FROM m2n_wd_uid) AND COALESCE({status}, '') <> 'unpaid' "
                "ORDER BY rowno"):
            amount = rules.parse_price(price)[0]
            if amount == 0:
                continue
            v, _note = rules.parse_time_sell(ts)
            add(uid, ("PURCHASE", st.name, key, None if amount is None else -amount,
                      None if v is None else f"U{v:012d}"))
    st = table("service_other", {"id", "id_user", "price", "type", "time"})
    if st:
        marks = ",".join("?" * len(SERVICE_DEBIT_TYPES))
        for sid, uid, price, t in conn.execute(
                f"SELECT c_id, c_id_user, c_price, c_time FROM {sql_ident(st.sql_name)} "  # noqa: S608
                f"WHERE c_id_user IN (SELECT uid FROM m2n_wd_uid) AND c_type IN ({marks}) "
                f"AND ({col_expr(st, 'status')} IS NULL OR {col_expr(st, 'status')} = 'paid') ORDER BY rowno",
                SERVICE_DEBIT_TYPES):
            amount = rules.parse_price(price)[0]
            if amount == 0:
                continue
            add(uid, ("SERVICE_OPERATION", st.name, sid, None if amount is None else -amount, tk(t)))
    st = table("wheel_list", {"id", "id_user", "price", "time"})
    if st:
        for wid, uid, price, t in conn.execute(
                f"SELECT c_id, c_id_user, c_price, c_time FROM {sql_ident(st.sql_name)} "  # noqa: S608
                "WHERE c_id_user IN (SELECT uid FROM m2n_wd_uid) ORDER BY rowno"):
            amount = rules.parse_price(price)[0]
            if amount == 0:
                continue  # a losing spin credits nothing (index.php:6266)
            add(uid, ("WHEEL_PRIZE", st.name, wid, amount, tk(t)))
    st = table("reagent_report", {"id", "user_id", "reagent", "time"})
    if st:
        for rid, a, b, t in conn.execute(
                f"SELECT c_id, c_user_id, c_reagent, c_time FROM {sql_ident(st.sql_name)} "  # noqa: S608
                "WHERE c_user_id IN (SELECT uid FROM m2n_wd_uid) OR c_reagent IN (SELECT uid FROM m2n_wd_uid) "
                "ORDER BY rowno"):
            for uid in dict.fromkeys(u for u in (a, b) if u is not None):
                if conn.execute("SELECT 1 FROM m2n_wd_uid WHERE uid = ?", (uid,)).fetchone():
                    # join gift: amount = affiliates config at the time, not stored (index.php:5866-5872)
                    add(uid, ("REFERRAL_GIFT", st.name, rid, None, tk(t)))
    st = table("Giftcodeconsumed", {"id", "id_user"})
    if st:
        for gid, uid in conn.execute(
                f"SELECT c_id, c_id_user FROM {sql_ident(st.sql_name)} "  # noqa: S608
                "WHERE c_id_user IN (SELECT uid FROM m2n_wd_uid) ORDER BY rowno"):
            add(uid, ("GIFT_CODE", st.name, gid, None, None))  # no amount and no time (Q5)
    return ev, sources


def _empty_bucket() -> dict[str, Any]:
    return {"count": 0, "sum": 0, "unparsed": 0}


def _cashback_hint(delta: int | None, topups: dict[str, Any]) -> str | None:
    t = topups["sum"]
    if delta is None or not topups["count"] or topups["unparsed"] or t <= 0 or delta <= t:
        return None
    q = ((delta - t) * 100 * 100 * 2 + t) // (2 * t)  # percent with two decimals, rounded half up
    return f"{q // 100}.{q % 100:02d}"


def _classify(item: dict[str, Any], events: list[tuple]) -> tuple[dict[str, Any], str, str, list[tuple], bool]:
    """(features, cause, certainty, evidence rows, uses window events)."""
    last = _interval(item["last_tkey"])
    places = ("AFTER", "IN_WINDOW", "UNPLACEABLE")
    groups = {g: {p: _empty_bucket() for p in places} for g in GROUPS}
    unq = {g: {p: 0 for p in places} for g in UNQUANTIFIED}
    refs: list[tuple] = []
    for kind, table, pk, amount, tkey in events:
        place = _place(_interval(tkey), last)
        if place == "BEFORE":
            continue
        refs.append((place, kind, table, pk))
        if kind in unq:
            unq[kind][place] += 1
            continue
        b = groups[kind][place]
        b["count"] += 1
        if amount is None:
            b["unparsed"] += 1
        else:
            b["sum"] += amount
    delta, snap, last_after = item["delta"], item["snapshot"], item["last_after"]
    unit = {"snapshot_is_10x_history": bool(last_after) and snap == 10 * last_after,
            "history_is_10x_snapshot": bool(snap) and last_after == 10 * snap}
    uncertain = any(groups[g][p]["count"] for g in GROUPS for p in ("IN_WINDOW", "UNPLACEABLE")) \
        or any(groups[g]["AFTER"]["unparsed"] for g in GROUPS) or any(n for g in unq.values() for n in g.values())
    after_groups = [g for g in GROUPS if groups[g]["AFTER"]["count"]]
    total_after = sum(groups[g]["AFTER"]["sum"] for g in GROUPS)
    matches: list[str] = []
    if delta is not None:
        for g in GROUPS:
            a, w = groups[g]["AFTER"], groups[g]["IN_WINDOW"]
            if a["count"] and not a["unparsed"] and a["sum"] == delta:
                matches.append(f"{g}_AFTER")
            if w["count"] and not (a["unparsed"] or w["unparsed"]) and a["sum"] + w["sum"] == delta:
                matches.append(f"{g}_AFTER_AND_WINDOW")
        if len(after_groups) > 1 and total_after == delta:
            matches.append("ALL_AFTER")
        window_sum = sum(groups[g]["IN_WINDOW"]["sum"] for g in GROUPS)
        window_n = sum(groups[g]["IN_WINDOW"]["count"] for g in GROUPS)
        active = sum(1 for g in GROUPS if groups[g]["AFTER"]["count"] + groups[g]["IN_WINDOW"]["count"])
        if window_n and active > 1 and total_after + window_sum == delta:
            matches.append("ALL_AFTER_AND_WINDOW")
    window_used = False
    any_evidence = bool(refs)
    if item["verdict"] == "ORDER_UNPROVEN":
        cause = "ORDER_UNPROVEN"
        certainty = "PROBABLE" if item["order_match"] else "UNEXPLAINED"
    elif item["order_match"]:
        cause, certainty = "ORDER_UNPROVEN", "PROBABLE"
    elif after_groups and total_after == delta and not uncertain:
        cause = GROUP_CAUSE[after_groups[0]] if len(after_groups) == 1 else "UNLOGGED_MIXED_ACTIVITY"
        certainty = "PROVEN"
    elif matches:
        m = matches[0]
        g = m.split("_AFTER")[0]
        cause = GROUP_CAUSE.get(g, "UNLOGGED_MIXED_ACTIVITY")
        certainty = "PROBABLE"
        window_used = "WINDOW" in m
    elif unit["snapshot_is_10x_history"] or unit["history_is_10x_snapshot"]:
        cause, certainty = "UNIT_SUSPECT", "PROBABLE"
    elif any_evidence:
        cause, certainty = "ACTIVITY_AFTER_HISTORY_INEXACT", "PROBABLE"
    else:
        cause, certainty = "UNEXPLAINED", "UNEXPLAINED"
    last_kind = None if item["last_tkey"] is None else ("LOCAL" if item["last_tkey"][0] == "L" else "UNIX")
    features = {
        "snapshot_minor": str(snap),
        "history_last_balance_after_minor": None if last_after is None else str(last_after),
        "delta_snapshot_minus_history_minor": None if delta is None else str(delta),
        "delta_sign": None if delta is None else ("POSITIVE" if delta > 0 else "NEGATIVE"),
        "abs_delta_minor": None if delta is None else str(abs(delta)),
        "transactions": item["transactions"],
        "last_transaction": {"transaction_id": item["last_id"], "time_kind": last_kind,
                             "time": None if item["last_tkey"] is None else item["last_tkey"][1:]},
        "groups": {g: {p: {"count": b["count"], "sum_minor": str(b["sum"]), "unparsed_amounts": b["unparsed"]}
                       for p, b in groups[g].items()} for g in GROUPS},
        "unquantified": unq,
        "sum_after_all_groups_minor": str(total_after),
        # Gateway/card cashback is credited as price * percent / 100 with no row (admin.php:1745-1750,
        # croncard.php:49-55, payment/zarinpal.php:74-79): the excess over the later top-ups, as a percent of
        # them, is shown for the owner to compare with PaySetting cashback values. A hint, never a cause.
        "cashback_percent_hint": _cashback_hint(delta, groups["TOPUP"]["AFTER"]),
        "exact_matches": matches,
        "unit_hint": unit,
        "order": {"verdict": item["verdict"], "a_row_with_the_last_time_or_any_order_matches_snapshot":
                  item["order_match"]},
    }
    return features, cause, certainty, refs, window_used or any(r[0] == "IN_WINDOW" for r in refs)


def analyse_differences(ctx: ModuleContext, items: list[dict[str, Any]]) -> None:
    """``records/wallet_difference_analysis.jsonl`` + reconciliation ``wallet_differences``."""
    conn = ctx.conn
    conn.execute("DROP TABLE IF EXISTS m2n_wd_uid")
    conn.execute("CREATE TEMP TABLE m2n_wd_uid (uid TEXT PRIMARY KEY)")
    conn.executemany("INSERT OR IGNORE INTO m2n_wd_uid VALUES (?)", [(i["uid"],) for i in items])
    events, sources = _events(ctx) if items else ({}, {})
    conn.execute("DROP TABLE IF EXISTS m2n_wd_uid")
    sink = ctx.open("records/wallet_difference_analysis.jsonl") if items else None
    by_cause = dict.fromkeys(CAUSES, 0)
    by_cert = dict.fromkeys(CERTAINTIES, 0)
    by_pair: dict[str, int] = {}
    actions = {"ARCHIVE_ONLY": 0, "OWNER_DECISION": 0, "NONE": 0}
    proven_abs: dict[str, int] = {}
    from_verdict: dict[str, int] = {}
    window_customers = 0
    for item in sorted(items, key=lambda i: (i["cust"]["telegram_user_id"] is None, i["cust"]["telegram_user_id"] or "",
                                             i["uid"])):
        ctx.tick()
        features, cause, certainty, refs, window = _classify(item, events.get(item["uid"], []))
        if certainty == "PROVEN":
            action = "ARCHIVE_ONLY"
            proven_abs[cause] = proven_abs.get(cause, 0) + abs(item["delta"])
        elif cause == "ORDER_UNPROVEN" and certainty == "PROBABLE":
            action = "NONE"  # an order of the same-time rows ends at the snapshot
        else:
            action = "OWNER_DECISION"
        by_cause[cause] += 1
        by_cert[certainty] += 1
        by_pair[f"{cause}/{certainty}"] = by_pair.get(f"{cause}/{certainty}", 0) + 1
        actions[action] += 1
        from_verdict[item["verdict"]] = from_verdict.get(item["verdict"], 0) + 1
        window_customers += 1 if window else 0
        ev_refs = [{**_pk(ctx, TABLE, item["last_id"]), "role": "LAST_HISTORY_ROW"}] if item["last_id"] else []
        for place, kind, table, pk in refs:
            ev_refs.append({**_pk(ctx, table, pk), "role": f"{kind}_{place}"})
        tg = item["cust"]["telegram_user_id"]
        key = (f"legacy:wallet-difference:{tg}" if tg is not None else "legacy:wallet-difference:raw:"
               + hashlib.sha256(item["uid"].encode("utf-8", "surrogatepass")).hexdigest()[:32])
        sink.write({
            "record_type": DIFF_RECORD_TYPE,
            "schema": DIFF_SCHEMA,
            "idempotency_key": key,
            "customer": item["cust"],
            "features": features,
            "cause": cause,
            "certainty": certainty,
            "proposed_action": action,
            "evidence_refs": ev_refs[:EVIDENCE_REFS_LIMIT],
            "evidence_refs_not_listed": max(0, len(ev_refs) - EVIDENCE_REFS_LIMIT),
            "snapshot_is_the_opening_figure": True,
            "opening_balance_changed": False,
            "affects_wallet": False,
            "applied_to_balance": False,
            "applies_to_live_state": False,
            "provenance": ctx.provenance(TABLE, None, None),
        })
    decisions: list[str] = []
    if by_cert["UNEXPLAINED"]:
        decisions.append("WALLET_DIFF_UNEXPLAINED_REVIEW")
    if any(n for k, n in by_pair.items() if k.endswith("/PROBABLE") and not k.startswith("ORDER_UNPROVEN")):
        decisions.append("WALLET_DIFF_PROBABLE_CONFIRM")
    if by_cause["UNIT_SUSPECT"]:
        decisions.append("WALLET_DIFF_UNIT_SUSPECT_CONFIRM_UNIT")
    if window_customers:
        decisions.append("WALLET_DIFF_PROVE_TIME_ZONES")
    ctx.reconciliation("wallet_differences", {
        "status": "ANALYSED" if items else "NO_DIFFERENCES",
        "total": len(items),
        "from_verdict": dict(sorted(from_verdict.items())),
        "by_cause": by_cause,
        "by_certainty": by_cert,
        "by_cause_and_certainty": dict(sorted(by_pair.items())),
        "proposed_actions": actions,
        "proven_abs_delta_by_cause_minor": {k: str(v) for k, v in sorted(proven_abs.items())},
        "proven_abs_delta_note": "summed |delta| per cause for PROVEN differences only",
        "unit": ctx.money_unit, "currency": ctx.currency,
        "owner_decisions": decisions,
        "customers_with_events_in_time_window": window_customers,
        "evidence_sources": dict(sorted(sources.items())),
        "time_comparison": {"local_time_offset_bounds_hours": [OFFSET_MIN_S // 3600, OFFSET_MAX_S // 3600],
                            "rule": "AFTER iff earliest possible instant of the event > latest possible instant of "
                                    "the last history row; local wall times span every UTC offset (zone unproven)"},
        "certainty_rules": {
            "PROVEN": "delta == signed sum of all quantified events AFTER the last history row; nothing in the "
                      "window, unplaceable, unquantified or unreadable",
            "PROBABLE": "an exact sum that needs window events or ignores another group, a 10x unit pattern, a tied "
                        "last timestamp, or activity after the history that does not add up",
            "UNEXPLAINED": "no observable evidence"},
        "opening_balance_changed": False,
        "history_reapplied": False,
        "explanation": DIFF_EXPLANATION,
        "explanation_fa": DIFF_EXPLANATION_FA,
    })


def run(ctx: ModuleContext) -> None:
    _register_messages(ctx)
    analysed = False
    for stats in ctx.tables_for(TABLE):
        if stats.rows == 0:
            ctx.coverage(stats.name, status="empty")
            continue
        _convert(ctx, stats)
        analysed = True
    if not analysed:
        analyse_differences(ctx, [])


register_module(Module(key="wallet_history", title_fa="تاریخچه کیف پول (آرشیو و تطبیق)", run=run, order=210))


# ---- synthetic data ------------------------------------------------------------------
DDL = """DROP TABLE IF EXISTS `wallet_transaction`;
CREATE TABLE `wallet_transaction` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `id_user` varchar(200) DEFAULT NULL,
  `username` varchar(200) DEFAULT NULL,
  `service_username` varchar(200) DEFAULT NULL,
  `type` varchar(50) DEFAULT NULL,
  `amount` bigint(20) DEFAULT NULL,
  `balance_before` bigint(20) DEFAULT NULL,
  `balance_after` bigint(20) DEFAULT NULL,
  `ref_id` varchar(200) DEFAULT NULL,
  `source` varchar(100) DEFAULT NULL,
  `reason` text DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""
_BASE = datetime(2025, 6, 1, 9, 0, 0)  # noqa: DTZ001 - synthetic local wall time


def _num(v: int | None) -> str:
    return "NULL" if v is None else str(v)


def synth_rows(state: SynthState) -> list[str]:
    """Synthetic ``wallet_transaction`` VALUES tuples. Most chains end exactly at the user's
    snapshot balance; anomalies (gap, mismatch, inconsistency, duplicate suspect, unknown type,
    orphan, missing time) are warnings only."""
    spec = state.spec
    rng = state.rng("wallet_history")
    users, balances = state.user_ids, state.balances
    target = int(len(users) * 0.09 * spec.module_scale) if users else 0
    rows: list[str] = []
    tid = 0
    order = list(range(len(users)))
    rng.shuffle(order)
    chains: list[list[list]] = []

    def tx(uid: str, typ: str, amount: int, before: int, after: int, ref: str | None, src: str, reason: str,
           ts: datetime | None, uname: str | None) -> list:
        nonlocal tid
        tid += 1
        return [tid, uid, uname, None, typ, amount, before, after, ref, src, reason,
                ts.strftime("%Y-%m-%d %H:%M:%S") if ts else None]

    plan = wallet_cause_plan(state)
    planned = {p["index"] for p in plan}
    for pos, idx in enumerate(order):
        if tid >= target:
            break
        if idx in planned:
            continue
        uid, final = users[idx], balances[idx]
        k = rng.randint(1, 4)
        signed = [rng.choice((1, 1, -1)) * rng.randrange(1, 300) * 1000 for _ in range(k)]
        start = final - sum(signed)
        bal = start
        ts = _BASE + timedelta(hours=pos * 3)
        chain = []
        for s in signed:
            typ = "deposit" if s > 0 else "purchase"
            reason = "شارژ کیف پول" if s > 0 else "خرید سرویس"
            chain.append(tx(uid, typ, abs(s), bal, bal + s, str(rng.randrange(1, 9999)), "bot", reason, ts,
                            f"synthetic_user_{idx}"))
            bal += s
            ts += timedelta(minutes=rng.randrange(1, 600))
        chains.append(chain)
    if spec.anomalies and len(chains) >= 4:
        # gap: the second chain's last row starts from a wrong balance (arithmetic still holds)
        c = chains[1]
        if len(c) >= 2:
            c[-1][6] += 500
            c[-1][7] += 500  # history now ends 500 above the snapshot → DIFFERS (warning)
        else:
            c[-1][7] += 500
            c[-1][6] += 500
        # arithmetic inconsistency on the third chain (its own balance_after is unchanged)
        chains[2][0][5] += 1
        # duplicate suspect: an identical copy of the fourth chain's first row with a new id
        dup = list(chains[3][0])
        tid += 1
        dup[0] = tid
        chains[3].insert(1, dup)
        # unknown type, orphan customer, missing time, Persian free text
        chains.append([tx("5000000003", "mystery", 1000, 0, 1000, None, "admin", "هدیه ادمین", None, None)])
    # customers whose history ends before later, unlogged activity (payments.wallet_cause_plan)
    for p in plan:
        end = p["history_end"]
        chains.append([
            tx(p["uid"], "deposit", 5000, end - 3000, end + 2000, None, "bot", "شارژ کیف پول",
               WALLET_CAUSE_LAST_TX - timedelta(days=5), None),
            tx(p["uid"], "purchase", 2000, end + 2000, end, None, "bot", "خرید سرویس", WALLET_CAUSE_LAST_TX, None),
        ])
    for c in chains:
        for v in c:
            rows.append(f"({v[0]},{sql_str(v[1])},{sql_str(v[2])},{sql_str(v[3])},{sql_str(v[4])},{_num(v[5])},"
                        f"{_num(v[6])},{_num(v[7])},{sql_str(v[8])},{sql_str(v[9])},{sql_str(v[10])},"
                        f"{sql_str(v[11])})")
    return rows


def _synth(out, state: SynthState) -> None:
    rows = synth_rows(state)
    out.write(DDL)
    out.write("LOCK TABLES `wallet_transaction` WRITE;\n")
    state.write_rows(out, TABLE, rows)
    out.write("UNLOCK TABLES;\n")
    state.exp["wallet_transactions"] = len(rows)


register_synth("wallet_history", _synth, order=210)

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
    ZONE_NOTE,
    BoundedReviews,
    DistinctCounter,
    col_expr,
    customer_relation,
    parse_time,
    short_text,
    sql_ident,
    time_sort_key,
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
        "m.uid IS NOT NULL AS mixed FROM m2n_wtx w "
        "LEFT JOIN m2n_wtx_dup d ON d.dkey = w.dkey "
        "LEFT JOIN m2n_wtx_mixed m ON m.uid = w.uid "
        "ORDER BY w.uid IS NULL, w.uid, CASE WHEN m.uid IS NULL THEN w.tkey END, "
        "w.idnum IS NULL, w.idnum, w.idtext, w.rowno")
    for uid, tid, before_s, after_s, fmt, rec_json, dn, dfirst, rowno, mixed in cur:
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
        sink.write(rec)
        written += 1
    close_group()
    reviews.flush()
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


def run(ctx: ModuleContext) -> None:
    _register_messages(ctx)
    for stats in ctx.tables_for(TABLE):
        if stats.rows == 0:
            ctx.coverage(stats.name, status="empty")
            continue
        _convert(ctx, stats)


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

    for pos, idx in enumerate(order):
        if tid >= target:
            break
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

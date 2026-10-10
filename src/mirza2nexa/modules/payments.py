"""Payment_report → read-only legacy payment history (``records/payments.jsonl``).

Mirza writes one ``Payment_report`` row per top-up / pay-to-buy attempt. The row is created
as ``Unpaid`` (or ``waiting`` for a card receipt) and later updated **by ``id_order``** to
``paid``, ``reject`` or ``expire``. This module archives every row as evidence of what the
old bot recorded. A record never creates a payment, never moves a wallet, never counts as
revenue, never enters NEXA's live payment state machine and never triggers a service: the
wallet's opening figure is ``user.Balance`` and nothing else (nexa-bot@9e604def
``docs/migration-opening-balance.md:19``, ``docs/legacy-migration/reconciliation.md:84``).

Evidence (public sources; the deployed fork is *not* public — ``receipt_trace`` exists only
there and its meaning is UNKNOWN):

* DDL — botmirzapanel@92c0ed06 ``table.php:389-399`` (``id``, ``id_user``, ``id_order``,
  ``time``, ``price``, ``dec_not_confirmed``, ``Payment_Method``, ``payment_Status``,
  ``invoice``); mirza_pro@b645db74 ``db/tables/Payment_report.php:6-18`` (adds
  ``at_updated``, ``bottype``, ``message_id``, ``id_invoice``). Deployed backup
  (operator, 2026-10-10): ``id, id_user, id_order, time, at_updated, price, dec_not_confirmed,
  Payment_Method, payment_Status, message_id, receipt_trace``.
* ``id_order`` is the payment's own random reference ``bin2hex(random_bytes(5))``
  (mirza_pro ``index.php:4690-4695``, ``admin.php:3564-3569``; botmirzapanel
  ``index.php:2040-2048``) and every status update is keyed by it (e.g. mirza_pro
  ``admin.php:1765``). It is **not** an invoice key: invoice keys are 4/8/11/15 characters
  (``mapping/rules.py`` ``INVOICE_KEY``), ``id_order`` is 10 hex characters. The purchase link
  lives in ``id_invoice`` / ``invoice`` (``"<Processing_value_tow>|<Processing_value_one>"``,
  mirza_pro ``index.php:4689``), a column the deployed backup does not have.
* ``time``/``at_updated`` are PHP ``date('Y/m/d H:i:s')`` (mirza_pro ``index.php:4689``,
  ``index.php:5620``) in the server's local zone — ``Asia/Tehran`` in mirza_pro
  (``index.php:3``), unset in botmirzapanel. The zone of the deployed fork is UNPROVEN, so no
  Unix time is derived from a local date-time.
* ``price`` is the Toman amount the customer asked to pay (``$user['Processing_value']``,
  mirza_pro ``index.php:4693``); the card-to-card text multiplies it by 10 only for display
  (``index.php:4686``).
* ``bottype`` holds the sub-bot's API token (mirza_pro ``vpnbot/Default/index.php:1292-1295``)
  and is declared SECRET: it is never staged.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ..catalogue import ARCHIVE, CATALOGUE, FINANCIAL, PERSONAL, PUBLIC, SECRET, ColumnSpec, TableSpec
from ..mapping import rules
from ..synth import SynthState, register_synth, sql_str
from .base import Module, ModuleContext, register_module

TABLE = "Payment_report"
SCHEMA = "mirza.payment_report.v1"
RECORD_TYPE = "legacy_payment_history"
REVIEW_LIMIT_PER_CODE = 200  # record-level reviews per code; the rest is one aggregated row
DISTINCT_LIMIT = 50  # distinct unknown values reviewed individually

BOTMIRZA = "botmirzapanel@92c0ed06"
MIRZA_PRO = "mirza_pro@b645db74"

# ---- vocabularies --------------------------------------------------------------------
# Exact, case-sensitive spellings only (PHP compares with ==/===). A variant such as 'Paid'
# is UNKNOWN even though MySQL's _ci collation would match it in the bot's own SQL.
STATUS_VOCAB: dict[str, tuple[str, str, str]] = {
    # created with a gateway link / card details, nothing paid yet
    "Unpaid": ("PENDING", "AWAITING_PAYMENT", f"{MIRZA_PRO} index.php:4692-4695; {BOTMIRZA} index.php:2041"),
    # card receipt sent, waiting for an admin (or the auto-confirm cron)
    "waiting": ("PENDING", "RECEIPT_AWAITING_REVIEW", f"{MIRZA_PRO} index.php:5618; {BOTMIRZA} index.php:2264"),
    # confirmed by admin / gateway callback / auto-confirm cron
    "paid": ("SUCCEEDED", "PAID", f"{MIRZA_PRO} admin.php:1730,1765, function.php:988-996, cronbot/croncard.php:43; "
                                   f"{BOTMIRZA} admin.php:787"),
    # rejected by an admin (reason then written to dec_not_confirmed, admin.php:1799)
    "reject": ("FAILED", "REJECTED_BY_ADMIN", f"{MIRZA_PRO} admin.php:1792; {BOTMIRZA} admin.php:810"),
    # 'Unpaid' older than 24h is expired by cron; admins may DELETE expire rows (admin.php:5361),
    # so the archive can be incomplete.
    "expire": ("CANCELLED", "EXPIRED_UNPAID", f"{MIRZA_PRO} cronbot/payment_expire.php:13-21, cronbot/plisio.php:46"),
}
# mirza_pro admin.php:1708 enumerates the same five values.

# Direction of the money movement the row describes (never applied): a customer payment is
# money in; admin adjustments are credits/debits; the DEBIT is stored with a POSITIVE price and
# only the method implies the sign (mirza_pro admin.php:3613,3618; docs/SOURCE_TABLES.md Q2).
METHOD_DIRECTION = {"ADMIN_CREDIT_ADJUSTMENT": "ADMIN_CREDIT", "ADMIN_DEBIT_ADJUSTMENT": "ADMIN_DEBIT_STORED_POSITIVE",
                    "UNKNOWN": "UNKNOWN"}

METHOD_VOCAB: dict[str, tuple[str, str]] = {
    "cart to cart": ("CARD_TO_CARD", f"{MIRZA_PRO} index.php:4694; {BOTMIRZA} index.php:2265"),
    "aqayepardakht": ("GATEWAY_AQAYEPARDAKHT", f"{MIRZA_PRO} index.php:4772; {BOTMIRZA} index.php:2042"),
    "zarinpal": ("GATEWAY_ZARINPAL", f"{MIRZA_PRO} index.php:4832"),
    "variza": ("GATEWAY_VARIZA", f"{MIRZA_PRO} index.php:4892"),
    "plisio": ("CRYPTO_PLISIO", f"{MIRZA_PRO} index.php:4947"),
    "nowpayment": ("CRYPTO_NOWPAYMENTS", f"{MIRZA_PRO} index.php:5014"),
    "Nowpayments": ("CRYPTO_NOWPAYMENTS", f"{BOTMIRZA} index.php:2077"),
    "Currency Rial 1": ("GATEWAY_RIAL_1", f"{MIRZA_PRO} index.php:5080"),
    "Currency Rial 2": ("GATEWAY_RIAL_2", f"{MIRZA_PRO} index.php:5137"),
    "Currency Rial 3": ("GATEWAY_RIAL_3", f"{MIRZA_PRO} index.php:5280"),
    "Currency Rial gateway": ("GATEWAY_RIAL", f"{BOTMIRZA} index.php:2124"),
    "AbanGateway": ("GATEWAY_ABAN", f"{MIRZA_PRO} index.php:5213"),
    "arze digital offline": ("CRYPTO_OFFLINE", f"{MIRZA_PRO} index.php:5352"),
    "Star Telegram": ("TELEGRAM_STARS", f"{MIRZA_PRO} index.php:5403"),
    # Admin balance adjustments are logged as 'paid' rows too. 'low balance by admin' is a
    # DEDUCTION (admin.php:3617-3618) although price is positive; mirza_pro's own revenue
    # report excludes both (admin.php:342).
    "add balance by admin": ("ADMIN_CREDIT_ADJUSTMENT", f"{MIRZA_PRO} admin.php:3565-3572"),
    "low balance by admin": ("ADMIN_DEBIT_ADJUSTMENT", f"{MIRZA_PRO} admin.php:3611-3618"),
}
ADMIN_METHODS = frozenset({"ADMIN_CREDIT_ADJUSTMENT", "ADMIN_DEBIT_ADJUSTMENT"})

# id_invoice / invoice = "<Processing_value_tow>|<Processing_value_one>" (mirza_pro index.php:4689).
PURPOSE_VOCAB: dict[str, str] = {
    "getconfigafterpay": "BUY_SERVICE_AFTER_PAYMENT",  # mirza_pro index.php:3945, admin.php:1708
    "getextenduser": "EXTEND_SERVICE",  # mirza_pro index.php:1807
    "getextravolumeuser": "EXTRA_VOLUME",  # mirza_pro index.php:2082
    "getextratimeuser": "EXTRA_TIME",  # mirza_pro index.php:2624
}
# DirectPayment(): any other tag (e.g. botmirzapanel "0|0", sub-bot "0 | 0", NULL for admin
# rows) takes the top-up branch Balance += price (mirza_pro function.php:1509-1512;
# botmirzapanel index.php:2046) — admin adjustments are told apart by Payment_Method.
PURPOSE_OTHER = "WALLET_TOP_UP_OR_ADMIN_ADJUSTMENT"

ORDER_REF = re.compile(r"[0-9a-f]{10}")  # bin2hex(random_bytes(5))
OUTCOMES = ("SUCCEEDED", "FAILED", "PENDING", "CANCELLED", "UNKNOWN")

MESSAGES = {
    "SOURCE_KEY_DUPLICATED": "کلید اصلی ردیف (شناسه فاکتور/محصول/پرداخت/تراکنش) تکراری یا خالی است؛ ردیف وارد نمی‌شود.",
    "DUPLICATE_RECEIPT": "یک کد پیگیری رسید روی چند پرداخت موفق ثبت شده؛ احتمال تأیید تکراری یک رسید — بررسی مالی لازم است.",
    "PAYMENT_CUSTOMER_UNKNOWN": "پرداخت به کاربری تعلق دارد که در جدول کاربران بکاپ نیست (یا شناسه کاربر خالی است).",
    "PAYMENT_AMOUNT_UNREADABLE": "مبلغ یک پرداخت موفق عدد صحیح خوانا نیست؛ مبلغ خام حفظ شد و تفسیر نشد.",
    "PAYMENT_STATUS_UNKNOWN": "وضعیت پرداخت جزو مقادیر شناخته‌شده میرزا نیست؛ نتیجه UNKNOWN ثبت شد.",
    "PAYMENT_METHOD_UNKNOWN": "روش پرداخت جزو مقادیر شناخته‌شده میرزا نیست؛ UNKNOWN ثبت شد.",
    "PAYMENT_ORDER_RELATION_UNPROVEN": "شناسه سفارش پرداخت با شناسه یک فاکتور برابر است، اما طبق کد میرزا این شناسه مرجع خود پرداخت است؛ رابطه اثبات نشده.",
    "PAYMENT_ORDER_REF_DUPLICATED": "یک شناسه سفارش (id_order) روی چند ردیف پرداخت تکرار شده؛ میرزا وضعیت را با همین شناسه به‌روز می‌کند.",
    "PAYMENT_TIME_FORMAT_UNKNOWN": "قالب زمان برخی پرداخت‌ها شناخته‌شده نیست؛ مقدار خام حفظ شد.",
}
BLOCKERS = {
    "PAYMENT_SOURCE_KEY_DUPLICATED": "شناسه (id) تکراری یا خالی در جدول Payment_report؛ تناقض مالی — بسته مسدود است.",
    "PAYMENT_ACCOUNTING_MISMATCH": "تعداد ردیف‌های خروجی پرداخت با ردیف‌های بکاپ برابر نیست؛ بسته مسدود است.",
}
WARNINGS = {
    "PAYMENTS_HISTORY_ONLY": "تاریخچه پرداخت‌ها فقط به‌صورت آرشیو منتقل می‌شود؛ هیچ پرداخت، درآمد یا تغییر کیف پولی ساخته نمی‌شود.",
}

# ---- catalogue -----------------------------------------------------------------------
CATALOGUE.register(TableSpec(
    name=TABLE, klass=ARCHIVE, module="payments",
    purpose="Payment attempts (top-ups, pay-to-buy, admin adjustments) with status history",
    purpose_fa="گزارش پرداخت‌ها و افزایش موجودی (تاریخچه)",
    reason=("History only: NEXA never replays legacy payments (nexa-bot docs/migration-opening-balance.md:19); "
            "the wallet opening figure is user.Balance"),
    nexa_target="legacy_payment_history (read-only archive proposal; never payments, ledger or revenue)",
    evidence=(
        f"{BOTMIRZA} table.php:389-399 (DDL)",
        f"{MIRZA_PRO} db/tables/Payment_report.php:6-18 (DDL)",
        f"{MIRZA_PRO} index.php:4689-4695 (insert: id_order=bin2hex(random_bytes(5)), time=date('Y/m/d H:i:s'))",
        f"{MIRZA_PRO} admin.php:1708,1765,1792 (status vocabulary, update by id_order)",
        f"{MIRZA_PRO} cronbot/payment_expire.php:13-20 (Unpaid → expire)",
        f"{MIRZA_PRO} vpnbot/Default/index.php:1292-1295 (bottype = bot API token)",
        "docs/SOURCE_TABLES.md#payment_report; docs/evidence/mirza_tables.json",
        "deployed backup column list (operator, 2026-10-10): adds receipt_trace (no public source)",
    ),
    primary_key=("id",),
    columns=(
        ColumnSpec("id", PUBLIC, "AUTO_INCREMENT primary key"),
        ColumnSpec("id_user", PERSONAL, "user.id (Telegram id)"),
        ColumnSpec("id_order", PUBLIC, "payment's own random reference, 10 hex chars"),
        ColumnSpec("time", PUBLIC, "created, PHP date('Y/m/d H:i:s'), server zone"),
        ColumnSpec("at_updated", PUBLIC, "receipt sent, PHP date('Y/m/d H:i:s')"),
        ColumnSpec("price", FINANCIAL, "Toman, text"),
        ColumnSpec("dec_not_confirmed", PERSONAL, "receipt text / reject reason / gateway reference (mixed)"),
        ColumnSpec("Payment_Method", PUBLIC),
        ColumnSpec("payment_Status", PUBLIC),
        ColumnSpec("message_id", PUBLIC, "Telegram message id of the payment prompt"),
        ColumnSpec("receipt_trace", FINANCIAL, "deployed fork only; meaning UNKNOWN"),
        ColumnSpec("invoice", PERSONAL, "botmirzapanel: '<purpose>|<value>'"),
        ColumnSpec("id_invoice", PERSONAL, "mirza_pro: '<purpose>|<value>'"),
        ColumnSpec("bottype", SECRET, "sub-bot API token (vpnbot/Default/index.php:1295)"),
    ),
    open_questions=(
        "receipt_trace: meaning, uniqueness and who writes it are UNKNOWN (deployed fork only)",
        "time zone of time/at_updated in the deployed fork (mirza_pro sets Asia/Tehran)",
        "payment_Status/Payment_Method values outside the public vocabulary",
        "which paid rows credited the wallet (top-up) vs bought a service directly (id_invoice absent in deployed backup)",
    ),
    sensitivity=FINANCIAL,
))


# ---- shared helpers (also used by wallet_history) -------------------------------------
_SLASH = re.compile(r"([0-9]{4})/([0-9]{2})/([0-9]{2}) ([0-9]{2}):([0-9]{2}):([0-9]{2})")
_DASH = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})[ T]([0-9]{2}):([0-9]{2}):([0-9]{2})")
_UNIX = re.compile(r"[1-9][0-9]{8,9}")
YEAR_MIN, YEAR_MAX = 2015, 2099
FMT_PHP_SLASH = "PHP_Y/m/d_H:i:s"
FMT_SQL_DATETIME = "SQL_DATETIME"
FMT_UNIX = "UNIX_SECONDS"
ZONE_NOTE = ("Local wall time without offset. mirza_pro sets Asia/Tehran (index.php:3); the deployed fork's zone is "
             "UNPROVEN, so no UTC/Unix value is derived.")


def parse_time(raw: str | None, allow: frozenset[str]) -> dict[str, Any]:
    """Parse a legacy time cell without guessing a time zone.

    Local date-times keep ``local`` (ISO, zone-less) and ``unix=None`` with note
    ``OK_LOCAL_TIME_ZONE_UNPROVEN``. Only a bare epoch yields ``unix``. A year outside
    2015..2099 below 1900 (e.g. a Jalali ``1403/…``) is ``FORMAT_UNKNOWN``; never converted.
    """
    out: dict[str, Any] = {"raw": raw, "format": None, "local": None, "unix": None}
    if raw is None:
        return {**out, "note": "ABSENT"}
    t = rules.js_trim(raw)
    if t == "":
        return {**out, "note": "EMPTY"}
    for fmt, pat in ((FMT_PHP_SLASH, _SLASH), (FMT_SQL_DATETIME, _DASH)):
        m = pat.fullmatch(t)
        if m is None:
            continue
        if fmt not in allow:
            return {**out, "note": "FORMAT_UNKNOWN"}
        y, mo, d, h, mi, s = (int(g) for g in m.groups())
        if y < 1900:
            return {**out, "note": "FORMAT_UNKNOWN"}  # calendar not evidenced (e.g. Jalali)
        try:
            datetime(y, mo, d, h, mi, s)  # noqa: DTZ001 - calendar validation only, no zone implied
        except ValueError:
            return {**out, "format": fmt, "note": "INVALID_DATE"}
        if not YEAR_MIN <= y <= YEAR_MAX:
            return {**out, "format": fmt, "note": "OUT_OF_RANGE"}
        return {**out, "format": fmt, "local": f"{y:04d}-{mo:02d}-{d:02d}T{h:02d}:{mi:02d}:{s:02d}",
                "note": "OK_LOCAL_TIME_ZONE_UNPROVEN"}
    if _UNIX.fullmatch(t):
        if FMT_UNIX not in allow:
            return {**out, "note": "FORMAT_UNKNOWN"}
        v = int(t)
        if not rules.TIME_SELL_MIN <= v < rules.TIME_SELL_MAX:
            return {**out, "format": FMT_UNIX, "note": "OUT_OF_RANGE"}
        return {**out, "format": FMT_UNIX, "unix": v, "note": "OK_UNIX_SECONDS"}
    return {**out, "note": "FORMAT_UNKNOWN"}


def time_sort_key(parsed: dict[str, Any]) -> str | None:
    if parsed["local"] is not None:
        return "L" + parsed["local"]
    if parsed["unix"] is not None:
        return f"U{parsed['unix']:012d}"
    return None


def customer_relation(ctx: ModuleContext, raw: str | None) -> dict[str, Any]:
    """``{source_user_id, telegram_user_id, relation, customer_decision}`` for a ``user.id`` reference."""
    tg = rules.telegram_id(raw)
    if raw is None or rules.js_trim(raw) == "":
        return {"source_user_id": raw, "telegram_user_id": None, "relation": "MISSING", "customer_decision": None}
    decision = ctx.customer_decision(raw)
    if decision is None:
        rel = "CUSTOMER_NOT_IN_BACKUP"
    elif decision == "IMPORT":
        rel = "CUSTOMER_IMPORTED"
    else:
        rel = "CUSTOMER_IN_REVIEW"
    return {"source_user_id": raw, "telegram_user_id": tg, "relation": rel, "customer_decision": decision}


def short_text(ctx: ModuleContext, value: str | None, table: str, limit: int = 64) -> str | None:
    """Redacted, truncated text safe for reports/review data (vocabulary values only)."""
    if value is None:
        return None
    red = ctx.redact(value, table) or ""
    return red if len(red) <= limit else red[:limit] + "…"


def sql_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def col_expr(stats, column: str) -> str:
    """SQL expression for a staged column, or NULL when the dump did not have it."""
    return sql_ident("c_" + column) if column in stats.stored_columns else "NULL"


@dataclass
class Sum:
    count: int = 0
    amount: int = 0
    unparsed: int = 0

    def add(self, amount: int | None) -> None:
        self.count += 1
        if amount is None:
            self.unparsed += 1
        else:
            self.amount += amount

    def to_dict(self) -> dict[str, Any]:
        return {"count": self.count, "amount_minor": str(self.amount), "unparsed_amounts": self.unparsed}


def sums_dict(d: dict[str, Sum]) -> dict[str, Any]:
    return {k: d[k].to_dict() for k in sorted(d)}


class BoundedReviews:
    """Record-level review rows, at most ``limit`` per code; the overflow becomes one row."""

    def __init__(self, ctx: ModuleContext, limit: int = REVIEW_LIMIT_PER_CODE):
        self.ctx = ctx
        self.limit = limit
        self.counts: dict[str, int] = {}
        self.meta: dict[str, tuple[str, str, str, str]] = {}

    def add(self, *, kind: str, code: str, entity: str, table: str, pk: str | None, severity: str,
            data: dict[str, Any] | None = None, message: str | None = None) -> None:
        n = self.counts.get(code, 0) + 1
        self.counts[code] = n
        self.meta.setdefault(code, (kind, entity, table, severity))
        if n <= self.limit:
            self.ctx.review(kind=kind, code=code, entity=entity, table=table, pk=pk, severity=severity, data=data,
                            message=message)

    def flush(self) -> None:
        for code in sorted(self.counts):
            n = self.counts[code]
            if n > self.limit:
                kind, entity, table, severity = self.meta[code]
                self.ctx.review(kind=kind, code=code, entity=entity, table=table, pk=None, severity=severity,
                                data={"aggregated": True, "total_rows": n, "listed_individually": self.limit,
                                      "not_listed": n - self.limit})


class DistinctCounter:
    """Bounded count of distinct raw values (memory stays bounded on hostile data)."""

    def __init__(self, cap: int = 1000):
        self.cap = cap
        self.values: dict[str, int] = {}
        self.other = 0
        self.rows = 0

    def add(self, value: str) -> None:
        self.rows += 1
        if value in self.values:
            self.values[value] += 1
        elif len(self.values) < self.cap:
            self.values[value] = 1
        else:
            self.other += 1


def emit_distinct_reviews(ctx: ModuleContext, counter: DistinctCounter, *, code: str, kind: str, entity: str,
                          table: str, field: str) -> None:
    """One warning per distinct raw value (bounded), never one per row."""
    items = sorted(counter.values.items(), key=lambda kv: (-kv[1], kv[0]))
    for raw, n in items[:DISTINCT_LIMIT]:
        ctx.review(kind=kind, code=code, entity=entity, table=table, pk=None, severity="warning",
                   data={field: short_text(ctx, raw, table), "rows": n})
    rest = items[DISTINCT_LIMIT:]
    if rest or counter.other:
        ctx.review(kind=kind, code=code, entity=entity, table=table, pk=None, severity="warning",
                   data={"aggregated": True, "distinct_values_not_listed": len(rest) + (1 if counter.other else 0),
                         "rows": sum(n for _, n in rest) + counter.other})


def _register_messages(ctx: ModuleContext) -> None:
    ctx.register_messages(MESSAGES, BLOCKERS, WARNINGS)


# ---- conversion ----------------------------------------------------------------------
def _purpose(r: dict[str, Any], stats) -> dict[str, Any]:
    col = "id_invoice" if "id_invoice" in stats.stored_columns else "invoice" if "invoice" in stats.stored_columns \
        else None
    if col is None:
        return {"source_column": None, "normalized": "NOT_IN_SOURCE"}
    raw = r.get(col)
    head = rules.js_trim(raw.split("|", 1)[0]) if raw is not None else ""
    return {"source_column": col, "normalized": PURPOSE_VOCAB.get(head, PURPOSE_OTHER)}


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
    # Same receipt_trace on >1 successful payments (compared after trimming spaces).
    receipt_group: dict[int, int] = {}
    receipt_groups: list[list[str]] = []
    if "receipt_trace" in stats.stored_columns and "payment_Status" in stats.stored_columns:
        cur = conn.execute(
            f"SELECT trim(c_receipt_trace) AS r, rowno, {col_expr(stats, 'id')} FROM {t} "  # noqa: S608
            "WHERE c_payment_Status = 'paid' AND trim(c_receipt_trace) IN ("
            f"  SELECT trim(c_receipt_trace) FROM {t} WHERE c_payment_Status = 'paid' "
            "   AND c_receipt_trace IS NOT NULL AND trim(c_receipt_trace) <> '' "
            "   GROUP BY trim(c_receipt_trace) HAVING COUNT(*) > 1) "
            "ORDER BY r, rowno")
        last = None
        for rec, rowno, pid in cur:
            if rec != last:
                receipt_groups.append([])
                last = rec
            receipt_groups[-1].append(pid)
            receipt_group[rowno] = len(receipt_groups) - 1
    dup_orders: set[str] = set()
    dup_order_rows = 0
    if "id_order" in stats.stored_columns:
        for oid, n in conn.execute(
                f"SELECT c_id_order, COUNT(*) FROM {t} WHERE c_id_order IS NOT NULL AND trim(c_id_order) <> '' "  # noqa: S608
                "GROUP BY c_id_order HAVING COUNT(*) > 1"):
            dup_orders.add(oid)
            dup_order_rows += n

    reviews = BoundedReviews(ctx)
    sink = ctx.open("records/payments.jsonl")
    by_outcome: dict[str, Sum] = {o: Sum() for o in OUTCOMES}
    by_method: dict[str, Sum] = {}
    by_relation: dict[str, Sum] = {}
    by_order: dict[str, Sum] = {}
    by_purpose: dict[str, int] = {}
    succeeded_ex_admin = Sum()
    unknown_status = DistinctCounter()
    unknown_method = DistinctCounter()
    time_notes: dict[str, dict[str, int]] = {"created": {}, "updated": {}}
    written = dup_rows = 0
    allow = frozenset({FMT_PHP_SLASH, FMT_UNIX})

    for rowno, r in ctx.rows(stats):
        ctx.tick()
        pid = r.get("id")
        if pid is None or pid in dup_ids:
            dup_rows += 1
            reviews.add(kind="payment", code="SOURCE_KEY_DUPLICATED", entity="payments", table=stats.name, pk=pid,
                        severity="blocker", data={"duplicate": pid is not None})
            continue
        cust = customer_relation(ctx, r.get("id_user"))
        # -- order reference
        oid = r.get("id_order")
        if oid is None or rules.js_trim(oid) == "":
            orel, proven = "EMPTY", True
        elif ctx.invoice_exists(oid):
            # Equal text only. id_order is NOT invoice.id_invoice (mirza_pro index.php:4691,
            # function.php:2590-2593): a statistic, never a relation.
            orel, proven = "TEXT_EQUALS_INVOICE_KEY_UNPROVEN", False
        elif ORDER_REF.fullmatch(oid):
            orel, proven = "PAYMENT_REFERENCE", True
        else:
            orel, proven = "NOT_FOUND", False
        # -- amount (never re-scaled)
        amount, note = rules.parse_price(r.get("price"))
        # -- method / status
        m_raw = r.get("Payment_Method")
        method = METHOD_VOCAB.get(m_raw)[0] if m_raw in METHOD_VOCAB else "UNKNOWN"
        if method == "UNKNOWN":
            unknown_method.add("<NULL>" if m_raw is None else m_raw)
        s_raw = r.get("payment_Status")
        if s_raw in STATUS_VOCAB:
            outcome, reason, _cite = STATUS_VOCAB[s_raw]
        else:
            outcome, reason = "UNKNOWN", "STATUS_NOT_IN_EVIDENCED_VOCABULARY"
            unknown_status.add("<NULL>" if s_raw is None else s_raw)
        created = parse_time(r.get("time"), allow)
        updated = parse_time(r.get("at_updated"), allow)
        time_notes["created"][created["note"]] = time_notes["created"].get(created["note"], 0) + 1
        time_notes["updated"][updated["note"]] = time_notes["updated"].get(updated["note"], 0) + 1
        purpose = _purpose(r, stats)
        by_purpose[purpose["normalized"]] = by_purpose.get(purpose["normalized"], 0) + 1
        msg_raw = r.get("message_id")
        msg_id = int(msg_raw) if msg_raw is not None and re.fullmatch(r"[0-9]{1,19}", msg_raw) else None
        rt_raw = r.get("receipt_trace")
        rt = ctx.redact(rt_raw, stats.name)
        group = receipt_group.get(rowno)

        by_outcome[outcome].add(amount)
        by_method.setdefault(method, Sum()).add(amount)
        by_relation.setdefault(cust["relation"], Sum()).add(amount)
        by_order.setdefault(orel, Sum()).add(amount)
        if outcome == "SUCCEEDED" and method not in ADMIN_METHODS:
            succeeded_ex_admin.add(amount)

        checksum = ctx.row_checksum(stats.name, r)
        sink.write({
            "record_type": RECORD_TYPE,
            "schema": SCHEMA,
            "idempotency_key": f"legacy:payment:{pid}",
            "payment_id": pid,
            "customer": cust,
            "order": {"source_order_id": oid, "relation": orel, "relation_proven": proven,
                      "order_ref_shared_with_other_rows": oid in dup_orders},
            "purpose": purpose,
            "amount": {"raw": r.get("price"), "parse_note": note, **ctx.money(amount)},
            "method": {"raw": m_raw, "normalized": method,
                       "direction": METHOD_DIRECTION.get(method, "CUSTOMER_PAYMENT_MONEY_IN")},
            "status": {"raw": s_raw, "outcome": outcome, "reason": reason},
            "times": {"created": created, "updated": updated, "zone": ZONE_NOTE},
            # Polymorphic (docs/SOURCE_TABLES.md Q2): receipt text, reject reason, gateway
            # reference/JSON, 'remove_all', auto-confirm label. Redacted; never in reports.
            "rejection_note": {"text": ctx.redact(r.get("dec_not_confirmed"), stats.name),
                               "semantics": "POLYMORPHIC_UNKNOWN_PER_ROW"},
            "telegram_message_id": {"raw": msg_raw, "value": msg_id},
            # Financial evidence: kept inside the encrypted package only, never in reports.
            "receipt_trace": {"raw": rt, "meaning": "UNKNOWN", "redacted": rt != rt_raw,
                              "shared_with_other_successful_payments": group is not None},
            "creates_payment": False,
            "affects_wallet": False,
            "counts_as_revenue": False,
            "enters_live_payment_state_machine": False,
            "triggers_service": False,
            "applies_to_live_state": False,
            "provenance": ctx.provenance(stats.name, pid, checksum),
        })
        written += 1
        if cust["relation"] in ("CUSTOMER_NOT_IN_BACKUP", "MISSING"):
            reviews.add(kind="payment", code="PAYMENT_CUSTOMER_UNKNOWN", entity="payments", table=stats.name, pk=pid,
                        severity="warning", data={"relation": cust["relation"], "outcome": outcome})
        if outcome == "SUCCEEDED" and amount is None:
            reviews.add(kind="payment", code="PAYMENT_AMOUNT_UNREADABLE", entity="payments", table=stats.name,
                        pk=pid, severity="warning", data={"parse_note": note, "method": method})

    for ids in receipt_groups:
        ids_sorted = sorted(i for i in ids if i is not None)
        # Never the receipt value itself: only payment ids and counts.
        reviews.add(kind="payment", code="DUPLICATE_RECEIPT", entity="payments", table=stats.name,
                    pk=ids_sorted[0] if ids_sorted else None, severity="blocker",
                    data={"successful_payments": len(ids), "payment_ids": ids_sorted[:20]})
    reviews.flush()
    emit_distinct_reviews(ctx, unknown_status, code="PAYMENT_STATUS_UNKNOWN", kind="payment", entity="payments",
                          table=stats.name, field="status_raw")
    emit_distinct_reviews(ctx, unknown_method, code="PAYMENT_METHOD_UNKNOWN", kind="payment", entity="payments",
                          table=stats.name, field="method_raw")
    if by_order.get("TEXT_EQUALS_INVOICE_KEY_UNPROVEN"):
        ctx.review(kind="payment", code="PAYMENT_ORDER_RELATION_UNPROVEN", entity="payments", table=stats.name,
                   pk=None, severity="warning", data={"rows": by_order["TEXT_EQUALS_INVOICE_KEY_UNPROVEN"].count})
    if dup_orders:
        ctx.review(kind="payment", code="PAYMENT_ORDER_REF_DUPLICATED", entity="payments", table=stats.name, pk=None,
                   severity="warning", data={"order_refs": len(dup_orders), "rows": dup_order_rows})
    unknown_times = {k: v.get("FORMAT_UNKNOWN", 0) + v.get("INVALID_DATE", 0) + v.get("OUT_OF_RANGE", 0)
                     for k, v in time_notes.items()}
    if any(unknown_times.values()):
        ctx.review(kind="payment", code="PAYMENT_TIME_FORMAT_UNKNOWN", entity="payments", table=stats.name, pk=None,
                   severity="warning", data={"rows_by_column": unknown_times})

    key_problems = len(dup_ids) + (1 if null_ids else 0) + (0 if has_id else 1)
    if key_problems:
        ctx.block("PAYMENT_SOURCE_KEY_DUPLICATED", key_problems)
    balanced = written + dup_rows == stats.rows
    if not balanced:
        ctx.block("PAYMENT_ACCOUNTING_MISMATCH")
    ctx.warn("PAYMENTS_HISTORY_ONLY")
    ctx.count("payments", "source", stats.rows)
    ctx.count("payments", "converted", written)
    ctx.coverage(stats.name, archived=written, review=dup_rows, status="archived")
    ctx.reconciliation("payments", {
        "source_table": stats.name,
        "source_rows": stats.rows,
        "archived": written,
        "duplicate_id_rows": dup_rows,
        "duplicate_ids": len(dup_ids),
        "null_ids": null_ids,
        "equation": "source_rows = archived + duplicate_id_rows",
        "balanced": balanced,
        "unit": ctx.money_unit, "currency": ctx.currency,
        "by_outcome": sums_dict(by_outcome),
        "by_method": sums_dict(by_method),
        "by_customer_relation": sums_dict(by_relation),
        "by_order_relation": sums_dict(by_order),
        "by_purpose": dict(sorted(by_purpose.items())),
        "succeeded_excluding_admin_adjustments": succeeded_ex_admin.to_dict(),
        "orphans": {"customer_not_in_backup": by_relation.get("CUSTOMER_NOT_IN_BACKUP", Sum()).count,
                    "customer_missing": by_relation.get("MISSING", Sum()).count},
        "duplicate_receipts": {"groups": len(receipt_groups), "successful_payments": len(receipt_group)},
        "duplicate_order_refs": {"order_refs": len(dup_orders), "rows": dup_order_rows},
        "unknown_status": {"rows": unknown_status.rows, "distinct_values": len(unknown_status.values)
                           + (1 if unknown_status.other else 0)},
        "unknown_method": {"rows": unknown_method.rows, "distinct_values": len(unknown_method.values)
                           + (1 if unknown_method.other else 0)},
        "time_notes": {k: dict(sorted(v.items())) for k, v in time_notes.items()},
        "note": ("Sums are historical evidence of what the old bot recorded. They are NOT revenue, NOT payments "
                 "and NOT wallet credit; the only opening figure is user.Balance. 'low balance by admin' rows are "
                 "deductions although their price is positive. For a direct purchase price is only the shortfall above the "
                 "wallet balance (mirza_pro index.php:3925-3927). Many Balance changes (gifts, commission, cashback, "
                 "refunds) write no Payment_report row and admins may delete 'expire' rows, so this table cannot "
                 "reconcile user.Balance (docs/SOURCE_TABLES.md Q2/Q3)."),
        "note_fa": ("این جمع‌ها فقط شاهد تاریخی هستند؛ درآمد، پرداخت یا اعتبار کیف پول نیستند و تنها مبنای مانده "
                    "افتتاحیه، موجودی user.Balance است."),
    })


def run(ctx: ModuleContext) -> None:
    _register_messages(ctx)
    for stats in ctx.tables_for(TABLE):
        if stats.rows == 0:
            ctx.coverage(stats.name, status="empty")
            continue
        _convert(ctx, stats)


register_module(Module(key="payments", title_fa="تاریخچه پرداخت‌ها (آرشیو)", run=run, order=200))


# ---- synthetic data ------------------------------------------------------------------
DDL = """DROP TABLE IF EXISTS `Payment_report`;
CREATE TABLE `Payment_report` (
  `id` int(11) unsigned NOT NULL AUTO_INCREMENT,
  `id_user` varchar(200) DEFAULT NULL,
  `id_order` varchar(2000) DEFAULT NULL,
  `time` varchar(200) DEFAULT NULL,
  `at_updated` varchar(200) DEFAULT NULL,
  `price` varchar(200) DEFAULT NULL,
  `dec_not_confirmed` text DEFAULT NULL,
  `Payment_Method` varchar(400) DEFAULT NULL,
  `payment_Status` varchar(100) DEFAULT NULL,
  `message_id` int(11) DEFAULT NULL,
  `receipt_trace` varchar(255) DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""

_SYN_STATUSES = ["paid"] * 12 + ["Unpaid"] * 4 + ["expire"] * 2 + ["reject", "waiting"]
_SYN_METHODS = ["cart to cart"] * 8 + ["zarinpal"] * 3 + ["nowpayment", "Currency Rial 1", "Star Telegram",
                                                          "add balance by admin", "low balance by admin"]
_SYN_BASE = datetime(2025, 1, 1, 8, 0, 0)  # noqa: DTZ001 - synthetic local wall time


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y/%m/%d %H:%M:%S")


def synth_rows(state: SynthState) -> list[str]:
    """``Payment_report`` VALUES tuples (exposed for tests that build custom dumps)."""
    spec = state.spec
    rng = state.rng("payments")
    users = state.user_ids
    n = int(len(users) * 1.07 * spec.module_scale) if users else 0
    rows: list[str] = []

    def row(pid: int, uid: str | None, oid: str | None, created: str | None, updated: str | None,
            price: str | None, dec: str | None, method: str | None, status: str | None, msg: int | None,
            trace: str | None) -> str:
        return (f"({pid},{sql_str(uid)},{sql_str(oid)},{sql_str(created)},{sql_str(updated)},{sql_str(price)},"
                f"{sql_str(dec)},{sql_str(method)},{sql_str(status)},{'NULL' if msg is None else msg},"
                f"{sql_str(trace)})")

    for i in range(1, n + 1):
        uid = users[rng.randrange(len(users))]
        status = rng.choice(_SYN_STATUSES)
        method = rng.choice(_SYN_METHODS)
        if method.endswith("by admin"):
            status = "paid"
        created = _SYN_BASE + timedelta(seconds=i * 137)
        card = method == "cart to cart"
        updated = _fmt(created + timedelta(minutes=5)) if card and status in ("paid", "waiting", "reject") else None
        dec = {"reject": "رسید نامعتبر است", "waiting": "واریز انجام شد"}.get(status) if card else None
        trace = f"{100000000000 + i}" if card and status in ("paid", "waiting") else None
        rows.append(row(i, uid, f"{rng.getrandbits(40):010x}", _fmt(created), updated,
                        str(rng.randrange(5, 400) * 1000), dec, method, status,
                        rng.randrange(1000, 900000) if rng.random() < 0.8 else None, trace))
    if spec.anomalies and users:
        k = n
        extra = [
            # unknown status (three rows → one review row for the distinct value)
            (users[0], "aaaaaaaaa1", "2025/03/01 10:00:00", None, "1000", None, "cart to cart", "refunded", None, None),
            (users[1 % len(users)], "aaaaaaaaa2", "2025/03/01 10:01:00", None, "2000", None, "zarinpal", "refunded",
             None, None),
            (users[2 % len(users)], "aaaaaaaaa3", "2025/03/01 10:02:00", None, "3000", None, "zarinpal", "refunded",
             None, None),
            # unknown method
            (users[0], "aaaaaaaaa4", "2025/03/01 10:03:00", None, "5000", None, "crypto-x", "paid", None, None),
            # orphan customer
            ("5000000002", "aaaaaaaaa5", "2025/03/01 10:04:00", None, "7000", None, "zarinpal", "paid", None, None),
            # customer held for review (status 'frozen' in the user anomalies)
            ("6999999001", "aaaaaaaaa6", "2025/03/01 10:05:00", None, "8000", None, "zarinpal", "Unpaid", None, None),
            # empty order reference
            (users[0], "", "2025/03/01 10:06:00", None, "9000", None, "zarinpal", "expire", None, None),
            # same receipt on two successful payments
            (users[3 % len(users)], "aaaaaaaaa7", "2025/03/02 09:00:00", "2025/03/02 09:05:00", "50000",
             "واریز شد", "cart to cart", "paid", 4242, "TRACE-DUP-0001"),
            (users[4 % len(users)], "aaaaaaaaa8", "2025/03/02 09:10:00", "2025/03/02 09:15:00", "50000",
             "واریز شد", "cart to cart", "paid", 4243, "TRACE-DUP-0001"),
            # successful payment with an unreadable amount (thousands separator)
            (users[5 % len(users)], "aaaaaaaaa9", "2025/03/03 11:00:00", None, "150,000", None, "zarinpal", "paid",
             None, None),
            # Persian receipt text holding a (synthetic, test-range) card number → redacted
            (users[6 % len(users)], "aaaaaaaab1", "2025/03/03 12:00:00", "2025/03/03 12:01:00", "20000",
             "پرداخت از کارت 4000 0000 0000 0002 انجام شد", "cart to cart", "waiting", None, None),
            # inconsistent date strings
            (users[7 % len(users)], "aaaaaaaab2", "2025-03-04 10:00:00", None, "1000", None, "zarinpal", "Unpaid", None,
             None),
            (users[7 % len(users)], "aaaaaaaab3", "1403/12/14 10:00:00", None, "1000", None, "zarinpal", "Unpaid", None,
             None),
            (users[7 % len(users)], "aaaaaaaab4", "1741082400", None, "1000", None, "zarinpal", "expire", None, None),
            (users[7 % len(users)], "aaaaaaaab5", "2025/02/30 10:00:00", None, "1000", None, "zarinpal", "expire", None,
             None),
        ]
        for e in extra:
            k += 1
            rows.append(row(k, *e))
    return rows


def _synth(out, state: SynthState) -> None:
    rows = synth_rows(state)
    out.write(DDL)
    out.write("LOCK TABLES `Payment_report` WRITE;\n")
    state.write_rows(out, TABLE, rows)
    out.write("UNLOCK TABLES;\n")
    state.exp["payments"] = len(rows)


register_synth("payments", _synth, order=200)


def stable_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()

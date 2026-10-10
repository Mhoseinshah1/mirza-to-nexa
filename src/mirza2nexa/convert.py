"""Conversion engine: staged Mirza rows → NEXA migration records + review queue + reconciliation.

Each entity is converted by its own function; all of them read the staging database in
primary-key byte order and stream records out, so output is deterministic and memory stays
bounded. The decisions are the NEXA legacy importer's own (see :mod:`mapping.rules` and
``docs/MAPPING.md``); where NEXA has no decision, the record goes to the review queue
instead of being guessed.

Financial invariants enforced here:

* Amounts are never re-scaled. A Mirza figure becomes ``amount_minor`` *unchanged*; the
  currency label comes only from the operator-confirmed unit (NEXA owner decision 7 says
  Toman → ``IRT``, exponent 0). Without confirmation, no currency is attached and the
  package is blocked.
* One opening credit per customer at most, keyed ``legacy:opening:<telegram id>`` — the
  exact idempotency key/reference NEXA's ledger uniquely indexes, so re-importing any backup
  can never credit twice.
* Negative balances become legacy **debts** (magnitude, ``PENDING_REVIEW``), never ledger
  entries and never auto-settled.
* Invoices are history only: ``creates_payment``/``creates_order`` are always false.
* Every Toman of the source is accounted for in the reconciliation; a mismatch blocks.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .mapping import rules
from .progress import CancelToken, ProgressFn
from .staging import SchemaProfile, TableStats

MAPPING_VERSION = "mirza2nexa-mapping:1.0.0"

# Persian, data-free explanations for every review/blocker code.
MESSAGES: dict[str, str] = {
    "INVALID_IDENTITY": "شناسه کاربر یک شناسه تلگرام معتبر نیست؛ مشتری ساخته نمی‌شود.",
    "DUPLICATE_SOURCE_ID": "شناسه تلگرام در چند ردیف کاربر تکرار شده؛ هیچ‌کدام وارد نمی‌شود تا بررسی شود.",
    "BALANCE_UNREADABLE": "موجودی کیف پول عدد صحیح خوانا نیست (اعشار، جداکننده یا متن)؛ تبدیل نشد.",
    "BALANCE_OUT_OF_RANGE": "موجودی کیف پول خارج از سقف مجاز NEXA است؛ تبدیل نشد.",
    "STATUS_UNKNOWN": "وضعیت کاربر نه Active است نه block؛ مشتری وارد نمی‌شود تا بررسی شود.",
    "NEGATIVE_BALANCE": "موجودی منفی به‌عنوان بدهی قدیمی ثبت شد (بدون کسر یا تسویه خودکار).",
    "CODE_DUPLICATED": "کد محصول در چند ردیف تکرار شده؛ هیچ مشخصه‌ای از آن برداشت نشد.",
    "SOURCE_KEY_DUPLICATED": "کلید اصلی ردیف (شناسه فاکتور/محصول) تکراری یا خالی است؛ ردیف وارد نمی‌شود.",
    "INVOICE_WITHOUT_CUSTOMER": "فاکتور مشتری مشخصی در جدول کاربران ندارد (یتیم).",
    "ORPHAN": "سرویس فعال بدون مالک مشخص؛ وارد صف بررسی شد و هرگز خودکار واگذار نمی‌شود.",
    "CUSTOMER_NOT_IMPORTED": "مالک سرویس خودش وارد نشده (در صف بررسی است)؛ سرویس هم منتظر می‌ماند.",
    "INVALID_USERNAME": "نام کاربری اکانت پنل قابل تطبیق نیست.",
    "NO_PANEL": "کد پنل سرویس خالی است؛ پنل سرویس نامشخص است.",
    "UNSUPPORTED_SHAPE": "حجم/مدت/نوع سرویس با قواعد NEXA قابل تفسیر نیست.",
    "AMBIGUOUS_OWNERSHIP": "یک اکانت پنل به چند فاکتور/مالک نسبت داده شده؛ مالکیت نامشخص است.",
    "INVALID_SOURCE_ROW": "پرچم is_test فاکتور نامعتبر است.",
    "INVOICE_KEY_INVALID": "شناسه فاکتور با الگوی شناخته‌شده میرزا مطابقت ندارد.",
    "BALANCE_CHANGED_SINCE_PREVIOUS": "موجودی نسبت به بکاپ قبلی تغییر کرده؛ NEXA تغییر را خودکار اعمال نمی‌کند.",
    "UNREPRESENTABLE_CELL": "مقدار یک خانه قابل نمایش متنی نیست (باینری نامعتبر).",
}

BLOCKER_MESSAGES: dict[str, str] = {
    "PANEL_TARGET_UNKNOWN_CODE": "برای کد پنلی که در این بکاپ وجود ندارد پنل مقصد انتخاب شده است؛ نگاشت پنل را اصلاح کنید.",
    "MONEY_UNIT_UNCONFIRMED": "واحد پول مبالغ میرزا (تومان/ریال) توسط اپراتور تأیید نشده؛ مبالغ بدون واحد خروجی گرفته شد و بسته مسدود است.",
    "MONEY_UNIT_NOT_IRT": "واحد اعلام‌شده ریال است، اما Importer فعلی NEXA فقط مانده تومانی (IRT) را می‌پذیرد؛ بدون تبدیل خودکار، بسته مسدود است.",
    "FINANCIAL_RECONCILIATION_MISMATCH": "جمع مبالغ خروجی با جمع مبالغ بکاپ برابر نیست؛ بسته مسدود شد.",
    "SOURCE_SCHEMA_MISSING_COLUMN": "برخی ستون‌های الزامی قرارداد NEXA در بکاپ وجود ندارد؛ بخش‌های وابسته مسدود شد.",
    "SOURCE_SCHEMA_MISSING_TABLE": "برخی جدول‌های الزامی در بکاپ وجود ندارد؛ بخش‌های وابسته مسدود شد.",
    "SOURCE_KEY_DUPLICATED": "کلید اصلی تکراری در بکاپ (فاکتور یا محصول)؛ بسته مسدود است.",
    "UNREPRESENTABLE_CELLS": "برخی مقادیر بکاپ قابل خواندن متنی نیستند.",
    "BALANCE_CHANGED_SINCE_PREVIOUS": "موجودی برخی مشتریان نسبت به بکاپ قبلی تغییر کرده است.",
    "DUPLICATE_SOURCE_ID": "شناسه تلگرام تکراری در جدول کاربران؛ تا بررسی، بسته مسدود است.",
    "SERVICE_ACCOUNTING_MISMATCH": "تعداد سرویس‌های خروجی با تعداد فاکتورهای فعال بکاپ برابر نیست.",
    "SOURCE_RECOUNT_MISMATCH": "بازشماری مستقل مبالغ از بکاپ با مبالغ خروجی برابر نیست.",
    "TABLE_DEFINED_TWICE": "یک جدول در چند فایل SQL تعریف شده است؛ ادغام خودکار انجام نمی‌شود.",
}

WARNING_MESSAGES: dict[str, str] = {
    "RESELLERS_AS_CUSTOMERS": "نمایندگان (agent) طبق تصمیم NEXA فقط به‌صورت مشتری عادی منتقل می‌شوند؛ نقش و اعتبار نمایندگی منتقل نمی‌شود.",
    "SECRET_COLUMNS_DROPPED": "ستون‌های حاوی اطلاعات حساس پنل/توکن (مثل user_info، uuid، bottype، inbounds) خوانده و منتقل نشدند.",
    "SERVICES_NEED_PANEL_MAPPING": "سرویس‌ها فقط کاندید پذیرش هستند؛ نگاشت پنل و تطبیق وضعیت واقعی در مرحله Adoption سمت NEXA انجام می‌شود.",
    "PRODUCTS_REVIEW_ONLY": "محصولات فقط برای بازبینی دستی منتقل می‌شوند و NEXA آن‌ها را خودکار نمی‌سازد.",
}


class JsonlSink(Protocol):
    def write(self, obj: Any) -> None: ...


OpenJsonl = Callable[[str], JsonlSink]


@dataclass
class Totals:
    count: int = 0
    amount: int = 0

    def add(self, amount: int) -> None:
        self.count += 1
        self.amount += amount

    def to_dict(self) -> dict[str, str | int]:
        return {"count": self.count, "amount_minor": str(self.amount)}


@dataclass
class ConversionOutcome:
    counts: dict[str, dict[str, int]] = field(default_factory=dict)
    reconciliation: dict[str, Any] = field(default_factory=dict)
    blockers: dict[str, int] = field(default_factory=dict)
    warnings: dict[str, int] = field(default_factory=dict)
    review_count: int = 0
    mapping: list[dict[str, Any]] = field(default_factory=list)
    resellers: dict[str, Any] = field(default_factory=dict)
    coverage: list[dict[str, Any]] = field(default_factory=list)
    blocker_messages: dict[str, str] = field(default_factory=dict)
    warning_messages: dict[str, str] = field(default_factory=dict)

    def block(self, code: str, n: int = 1) -> None:
        self.blockers[code] = self.blockers.get(code, 0) + n

    def warn(self, code: str, n: int = 1) -> None:
        self.warnings[code] = self.warnings.get(code, 0) + n


def _cols(stats: TableStats) -> list[str]:
    return ["c_" + c for c in stats.stored_columns]


class Converter:
    def __init__(self, db_path: Path, tables: dict[str, TableStats], profile: SchemaProfile,
                 fingerprint: str, open_jsonl: OpenJsonl, *, money_unit: str | None,
                 cancel: CancelToken | None = None, progress: ProgressFn | None = None,
                 ref_key: bytes | None = None):
        self.conn = sqlite3.connect(db_path, isolation_level=None)
        self.conn.execute("PRAGMA temp_store=FILE")
        self.conn.execute("PRAGMA cache_size=-32000")
        self.tables = tables
        self.profile = profile
        self.fingerprint = fingerprint
        self.open = open_jsonl
        self.money_unit = money_unit  # "toman" | "rial" | None
        # Secret key for references to secret values (codes, secret PKs). Derived from the raw dump
        # bytes, so it is deterministic per backup yet not computable from anything in report.json.
        self.ref_key = ref_key or b"m2n-ref-key-unset"
        self.currency = {"toman": "IRT", "rial": "IRR"}.get(money_unit or "")
        self.cancel = cancel or CancelToken()
        self.progress = progress
        self.out = ConversionOutcome()
        self.messages = dict(MESSAGES)
        self.blocker_messages = self.out.blocker_messages
        self.blocker_messages.update(BLOCKER_MESSAGES)
        self.warning_messages = self.out.warning_messages
        self.warning_messages.update(WARNING_MESSAGES)
        self._coverage: dict[str, dict[str, Any]] = {}
        self.review = open_jsonl("review-queue.jsonl")
        total = sum(tables[t].rows for t in rules.SOURCE_TABLES if t in tables)
        self._total = max(total * 2, 1)  # each staged row is visited about twice
        self._done = 0

    # ---- helpers -------------------------------------------------------------------
    def _tick(self, n: int = 1) -> None:
        self._done += n
        if self._done % 2000 < n:
            self.cancel.check()
            if self.progress:
                self.progress(min(self._done, self._total), self._total)

    def _provenance(self, table: str, pk: str | None, checksum: str | None = None) -> dict[str, Any]:
        p: dict[str, Any] = {"source_table": table, "source_pk": pk, "source_fingerprint": self.fingerprint}
        if checksum is not None:
            p["row_checksum"] = checksum
        return p

    def _review(self, *, kind: str, code: str, entity: str, table: str, pk: str | None,
                severity: str = "blocker", data: dict[str, Any] | None = None,
                record_also_exported: bool = False) -> None:
        self.review.write({
            "kind": kind, "severity": severity, "code": code, "message": self.messages.get(code, code),
            "entity": entity, "source_ref": {"table": table, "pk": pk}, "data": data or {},
        })
        self.out.review_count += 1
        if not record_also_exported:  # a flag on an exported record is not a separate row
            c = self.out.counts.setdefault(entity, {"source": 0, "converted": 0, "review": 0, "skipped": 0})
            c["review"] += 1

    def _count(self, entity: str, key: str, n: int = 1) -> None:
        c = self.out.counts.setdefault(entity, {"source": 0, "converted": 0, "review": 0, "skipped": 0})
        c[key] += n

    def _money(self, amount: int) -> dict[str, Any]:
        return {"amount_minor": str(amount), "currency": self.currency}

    def _rows(self, table: str, order_by: str):
        stats = self.tables[table]
        cols = _cols(stats)
        sql = f"SELECT rowno, {', '.join(cols)} FROM src_{table} ORDER BY c_{order_by}, rowno"  # noqa: S608
        cur = self.conn.execute(sql)
        names = list(stats.stored_columns)
        while True:
            batch = cur.fetchmany(1000)
            if not batch:
                return
            for r in batch:
                yield r[0], dict(zip(names, r[1:], strict=True))

    # ---- run -----------------------------------------------------------------------
    def run(self) -> ConversionOutcome:
        try:
            self._money_unit_check()
            self._customers_and_wallet()
            if "products" not in self.profile.entity_blocked:
                self._products_and_categories()
            if "invoices" not in self.profile.entity_blocked:
                self._invoices()
                self._services()
                self._panels()
            self._run_modules()
            self._schema_findings()
            self._mapping_table()
            self._finish_coverage()
            return self.out
        finally:
            self.conn.close()

    def _money_unit_check(self) -> None:
        if self.money_unit is None:
            self.out.block("MONEY_UNIT_UNCONFIRMED")
        elif self.money_unit == "rial":
            self.out.block("MONEY_UNIT_NOT_IRT")

    # ---- customers, wallet balances, debts -------------------------------------------
    def _customers_and_wallet(self) -> None:
        c = self.conn
        c.execute("CREATE TEMP TABLE cust (id TEXT PRIMARY KEY, decision TEXT) WITHOUT ROWID")
        dups = {r[0] for r in c.execute("SELECT c_id FROM src_user GROUP BY c_id HAVING COUNT(*) > 1")}
        has_trial = "SELECT 1 FROM trial_user WHERE id = ?"
        customers = self.open("records/customers.jsonl")
        openings = self.open("records/wallet_opening_balances.jsonl")
        debts = self.open("records/legacy_debts.jsonl")

        src_sum = Totals()          # every readable balance in the backup
        unreadable = 0
        credits, debits, zero = Totals(), Totals(), 0
        in_review = Totals()        # readable balances held back for review
        invalid_identity = Totals() # readable balances of rows that are not customers
        tiers: dict[str, int] = {}
        agents = 0
        seen_dup: set[str] = set()

        for rowno, r in self._rows("user", "id"):
            self._tick()
            self._count("customers", "source")
            raw_id = r["id"]
            balance = rules.parse_balance(r["Balance"])
            if balance is not None:
                src_sum.add(balance)
            else:
                unreadable += 1
            tg = rules.telegram_id(raw_id)
            if tg is None:
                c.execute("INSERT OR IGNORE INTO cust VALUES (?, 'INVALID_IDENTITY')", (raw_id or f"#row{rowno}",))
                if balance is not None:
                    invalid_identity.add(balance)
                self._review(kind="customer", code="INVALID_IDENTITY", entity="customers", table="user",
                             pk=raw_id, severity="warning")
                continue
            if tg in dups:
                c.execute("INSERT OR IGNORE INTO cust VALUES (?, 'DUPLICATE_SOURCE_ID')", (tg,))
                if balance is not None:
                    in_review.add(balance)
                if tg not in seen_dup:  # one review row stands for all of them (importer.md:327-332)
                    self.out.block("DUPLICATE_SOURCE_ID")  # CLAUDE.md: duplicate Telegram id is blocking
                    seen_dup.add(tg)
                    self._review(kind="customer", code="DUPLICATE_SOURCE_ID", entity="customers", table="user",
                                 pk=tg, data={"rows": c.execute(
                                     "SELECT COUNT(*) FROM src_user WHERE c_id = ?", (tg,)).fetchone()[0]})
                else:
                    self._count("customers", "review")
                continue
            if balance is None:
                c.execute("INSERT INTO cust VALUES (?, 'BALANCE_UNREADABLE')", (tg,))
                self._review(kind="wallet", code="BALANCE_UNREADABLE", entity="customers", table="user", pk=tg,
                             data={"balance_raw": r["Balance"]})
                continue
            if abs(balance) > rules.PAYMENT_AMOUNT_MAX_MINOR:
                c.execute("INSERT INTO cust VALUES (?, 'BALANCE_OUT_OF_RANGE')", (tg,))
                in_review.add(balance)
                self._review(kind="wallet", code="BALANCE_OUT_OF_RANGE", entity="customers", table="user", pk=tg,
                             data={"balance_raw": r["Balance"]})
                continue
            status = rules.user_status(r["User_Status"])
            if status == "UNKNOWN":
                c.execute("INSERT INTO cust VALUES (?, 'STATUS_UNKNOWN')", (tg,))
                in_review.add(balance)
                self._review(kind="customer", code="STATUS_UNKNOWN", entity="customers", table="user", pk=tg,
                             data={"status_raw": r.get("User_Status")})
                continue

            had_trial = c.execute(has_trial, (raw_id,)).fetchone() is not None
            checksum = rules.row_checksum("user:v1", [raw_id, r["Balance"], r["limit_usertest"],
                                                      "1" if had_trial else "0", r.get("username")])
            agent_raw = r.get("agent")
            agent = rules.is_agent(agent_raw)
            if agent:
                agents += 1
                tier = rules.js_trim(agent_raw or "").lower()
                tiers[tier if tier in {"n", "n2"} else "other"] = tiers.get(tier if tier in {"n", "n2"} else "other", 0) + 1
            c.execute("INSERT INTO cust VALUES (?, 'IMPORT')", (tg,))
            opening_kind = "POSITIVE" if balance > 0 else "NEGATIVE" if balance < 0 else "ZERO"
            customers.write({
                "record_type": "customer",
                "idempotency_key": f"legacy:customer:{tg}",
                "telegram_user_id": tg,
                "status": status,
                "username": rules.profile_username(r.get("username")),
                "legacy_is_agent": agent,
                "phone_class": rules.phone_class(r.get("number")),
                "trial_decision": rules.trial_decision(r["limit_usertest"], had_trial),
                "opening_kind": opening_kind,
                "provenance": self._provenance("user", raw_id, checksum),
            })
            self._count("customers", "converted")
            if balance > 0:
                credits.add(balance)
                openings.write({
                    "record_type": "wallet_opening_balance",
                    "idempotency_key": f"legacy:opening:{tg}",  # == NEXA ledger reference (0186_*.sql)
                    "telegram_user_id": tg,
                    "reason": "MIGRATION_OPENING_BALANCE",
                    **self._money(balance),
                    "source_amount_raw": r["Balance"],
                    "provenance": self._provenance("user", raw_id, checksum),
                })
                self._count("wallet_balances", "converted")
            elif balance < 0:
                debits.add(-balance)
                debts.write({
                    "record_type": "legacy_debt",
                    "idempotency_key": f"legacy:debt:{tg}",
                    "legacy_user_id": tg,
                    **self._money(-balance),  # magnitude, as legacy_wallet_debts stores it
                    "state": "PENDING_REVIEW",
                    "auto_settle": False,
                    "source_amount_raw": r["Balance"],
                    "provenance": self._provenance("user", raw_id, checksum),
                })
                self._count("debts", "converted")
            else:
                zero += 1
                self._count("wallet_balances", "skipped")

        # --- reconciliation: every readable Toman must be accounted for exactly once.
        accounted = credits.amount - debits.amount + in_review.amount + invalid_identity.amount
        users = self.tables["user"].rows
        count_accounted = (credits.count + debits.count + zero + in_review.count + invalid_identity.count
                           + unreadable)
        ok = accounted == src_sum.amount and count_accounted == users
        if not ok:
            self.out.block("FINANCIAL_RECONCILIATION_MISMATCH")
        self.out.reconciliation["wallet"] = {
            "unit": self.money_unit, "currency": self.currency,
            "source_users": users,
            "source_readable_balances": src_sum.to_dict(),
            "source_unreadable_balances": unreadable,
            "opening_credits": credits.to_dict(),
            "legacy_debts": debits.to_dict(),
            "zero_balances": zero,
            "held_for_review": in_review.to_dict(),
            "not_customers_invalid_identity": invalid_identity.to_dict(),
            "equation": "source_readable = credits - debts + held_for_review + invalid_identity",
            "accounted_amount_minor": str(accounted),
            "balanced": ok,
        }
        self._count("wallet_balances", "source", users)
        self._count("debts", "source", debits.count)
        if agents:
            self.out.warn("RESELLERS_AS_CUSTOMERS", agents)
        self.out.resellers = {"agents_total": agents, "by_tier": dict(sorted(tiers.items())),
                              "status": "blocked", "reason": "OQ-LWD-03: agents import as ordinary customers"}
        self._count("resellers", "source", agents)

    # ---- products & categories ----------------------------------------------------------
    def _products_and_categories(self) -> None:
        c = self.conn
        cols = self.tables["product"].stored_columns
        c.execute("CREATE TEMP TABLE pcode (code TEXT PRIMARY KEY, n INTEGER) WITHOUT ROWID")
        for _rowno, r in self._rows("product", "id"):
            code = rules.blank_to_none(r["code_product"])
            if code is not None:
                c.execute("INSERT INTO pcode VALUES (?, 1) ON CONFLICT(code) DO UPDATE SET n = n + 1", (code,))
        live_counts: dict[str, int] = {}
        if "invoices" not in self.profile.entity_blocked:
            ph = ",".join("?" for _ in rules.LIVE_STATUSES)
            for code, n in c.execute(
                f"SELECT c_code_product, COUNT(*) FROM src_invoice WHERE c_Status IN ({ph}) GROUP BY c_code_product",  # noqa: S608
                tuple(sorted(rules.LIVE_STATUSES)),
            ):
                k = rules.blank_to_none(code)
                if k is not None:
                    live_counts[k] = live_counts.get(k, 0) + n
        products = self.open("records/products.jsonl")
        categories: dict[str, int] = {}
        cat_col = "Category" if "Category" in cols else "category" if "category" in cols else None
        dup_ids = {r[0] for r in c.execute(
            "SELECT c_id FROM src_product GROUP BY c_id HAVING COUNT(*) > 1 OR c_id IS NULL")}
        if dup_ids:
            self.out.block("SOURCE_KEY_DUPLICATED", len(dup_ids))
        for _rowno, r in self._rows("product", "id"):
            self._tick()
            self._count("products", "source")
            pid = r["id"]
            if pid in dup_ids:
                self._review(kind="product", code="SOURCE_KEY_DUPLICATED", entity="products", table="product",
                             pk=pid)
                continue
            code = rules.blank_to_none(r["code_product"])
            if code is not None and c.execute("SELECT n FROM pcode WHERE code = ?", (code,)).fetchone()[0] > 1:
                self._review(kind="product", code="CODE_DUPLICATED", entity="products", table="product", pk=pid,
                             data={"code_product": code})
                continue
            price, price_note = rules.parse_price(r.get("price_product"))
            vol_raw = r.get("Volume_constraint")
            traffic = rules.parse_traffic_gb(vol_raw) if vol_raw is not None else None
            traffic_note = "ABSENT" if vol_raw is None else "NOT_A_NUMBER" if traffic is None else (
                "ZERO_MEANING_UNKNOWN" if traffic == 0 else "PROPOSED_GB")
            days_raw = r.get("Service_time")
            days_t = rules.js_trim(days_raw) if days_raw is not None else None
            days = int(days_t) if days_t is not None and days_t.isascii() and days_t.isdigit() and len(days_t) <= 6 else None
            days_note = "ABSENT" if days_raw is None else "NOT_A_NUMBER" if days is None else (
                "ZERO_MEANING_UNKNOWN" if days == 0 else "PROPOSED_DAYS")
            category = r.get(cat_col) if cat_col else None
            if category is not None and rules.js_trim(category):
                categories[rules.js_trim(category)] = categories.get(rules.js_trim(category), 0) + 1
            facts = [pid, r["code_product"], r.get("name_product"), r.get("price_product"), vol_raw, days_raw,
                     r.get("Location"), category, r.get("agent")]
            products.write({
                "record_type": "legacy_product_review",
                "idempotency_key": f"legacy:product:{pid}",
                "legacy_product_id": pid,
                "code_product": code,
                "title": r.get("name_product"),
                "historical_price": {"raw": r.get("price_product"), "note": price_note,
                                     **(self._money(price) if price is not None else {"amount_minor": None,
                                                                                       "currency": None})},
                "traffic": {"raw": vol_raw, "note": traffic_note,
                            "proposed_bytes": str(traffic) if traffic else None},
                "duration": {"raw": days_raw, "note": days_note, "proposed_days": days or None},
                "location_raw": r.get("Location"),
                "category_raw": category,
                "agent_tier_raw": r.get("agent"),
                "live_invoice_count": live_counts.get(code, 0) if code else 0,
                "review_state": "PENDING_REVIEW",
                "auto_create": False,
                "provenance": self._provenance("product", pid, rules.row_checksum("product:m2n-v1", facts)),
            })
            self._count("products", "converted")
        self.out.warn("PRODUCTS_REVIEW_ONLY")
        cats = self.open("records/categories.jsonl")
        for label in sorted(categories):
            cats.write({"record_type": "legacy_category_label", "idempotency_key": f"legacy:category:{label}",
                        "label": label, "product_count": categories[label], "source_column": f"product.{cat_col}",
                        "auto_create": False, "provenance": self._provenance("product", None)})
        self._count("categories", "source", len(categories))
        self._count("categories", "converted", len(categories))

    # ---- invoices (history archive) ----------------------------------------------------
    def _invoices(self) -> None:
        c = self.conn
        dup_keys = {r[0] for r in c.execute(
            "SELECT c_id_invoice FROM src_invoice WHERE c_id_invoice IS NOT NULL "
            "GROUP BY c_id_invoice HAVING COUNT(*) > 1")}
        if dup_keys:
            self.out.block("SOURCE_KEY_DUPLICATED", len(dup_keys))
        has_products = "products" not in self.profile.entity_blocked
        invoices = self.open("records/invoices.jsonl")
        price_total = Totals()
        classes: dict[str, int] = {}
        orphans = 0
        for _rowno, r in self._rows("invoice", "id_invoice"):
            self._tick()
            self._count("invoices", "source")
            key = r["id_invoice"]
            if key in dup_keys:
                self._review(kind="invoice", code="SOURCE_KEY_DUPLICATED", entity="invoices", table="invoice",
                             pk=key)
                continue
            is_test = rules.flag01(r["is_test"])
            owner = r["id_user"]
            owner_exists = owner is not None and c.execute(
                "SELECT 1 FROM src_user WHERE c_id = ? LIMIT 1", (owner,)).fetchone() is not None
            status = r["Status"]
            cp = rules.blank_to_none(r["code_panel"])
            if not rules.invoice_key_valid(key):
                cls = "KEY_SHAPE_UNRECOGNISED"
            elif is_test is True:
                cls = "TEST"
            elif is_test is None:
                cls = "TEST_FLAG_INVALID"
            elif not owner_exists:
                cls = "ORPHAN_OWNER"
            elif status not in rules.LIVE_STATUSES:
                cls = "NOT_LIVE"
            elif cp is None:
                cls = "NO_PANEL"
            else:
                cls = "LIVE_CANDIDATE"
            classes[cls] = classes.get(cls, 0) + 1
            code_product = rules.blank_to_none(r["code_product"])
            if code_product is None:
                pref = "NONE"
            elif has_products and c.execute("SELECT 1 FROM pcode WHERE code = ?", (code_product,)).fetchone():
                pref = "NAMED"
            else:
                pref = "NOT_IN_PRODUCT_TABLE"
            price, price_note = rules.parse_invoice_price(r["price_product"])
            if price is not None:
                price_total.add(price)
            sold_at, sold_note = rules.parse_time_sell(r.get("time_sell"))
            checksum = rules.row_checksum("invoice:v1", [key, owner, r["username"], status, r["is_test"],
                                                         r["code_panel"], r["code_product"], r["Volume"],
                                                         r["Service_time"], r["time_unit"], r["is_custom"]])
            invoices.write({
                "record_type": "legacy_invoice_archive",
                "idempotency_key": f"legacy:invoice:{key}" if rules.invoice_key_valid(key)
                else f"legacy:invoice-row:{checksum}",
                "invoice_key": key,
                "classification": cls,
                "owner_telegram_user_id": rules.telegram_id(owner) if owner_exists else None,
                "status_raw": status,
                "is_test": is_test,
                "code_panel": cp,
                "code_product": code_product,
                "product_ref": pref,
                "product_name": r.get("name_product"),
                "service_location": r.get("Service_location"),
                "volume_raw": r["Volume"],
                "service_time_raw": r["Service_time"],
                "time_unit_raw": r["time_unit"],
                "is_custom_raw": r["is_custom"],
                "historical_price": {"raw": r["price_product"], "note": price_note,
                                     **(self._money(price) if price is not None else {"amount_minor": None,
                                                                                       "currency": None})},
                "sold_at_unix": sold_at,
                "sold_at_note": sold_note,
                # History only — never a payment, order, revenue or wallet movement.
                "creates_payment": False,
                "creates_order": False,
                "provenance": self._provenance("invoice", key, checksum),
            })
            self._count("invoices", "converted")
            if cls == "ORPHAN_OWNER":
                orphans += 1
                self._review(kind="invoice", code="INVOICE_WITHOUT_CUSTOMER", entity="invoices", table="invoice",
                             pk=key, severity="warning", data={"status_raw": status}, record_also_exported=True)
        self.out.reconciliation["invoices"] = {
            "source_rows": self.tables["invoice"].rows,
            "archived": self.out.counts.get("invoices", {}).get("converted", 0),
            "duplicate_keys": len(dup_keys),
            "classifications": dict(sorted(classes.items())),
            "orphans": orphans,
            "historical_price_sum": price_total.to_dict(),
            "note": "Historical prices are evidence only; they are never revenue, payments or wallet movements.",
        }
        self._count("orders", "skipped", 0)

    # ---- services (adoption candidates) ------------------------------------------------
    def _services(self) -> None:
        c = self.conn
        # rowno, not the invoice key, is the primary key: a NULL or duplicated key must reach
        # the review queue, never be dropped by a uniqueness constraint.
        c.execute("CREATE TEMP TABLE svc (rowno INTEGER PRIMARY KEY, k TEXT, owner TEXT, panel TEXT, uname TEXT, "
                  "decision TEXT, rec TEXT)")
        dup_keys = {r[0] for r in c.execute(
            "SELECT c_id_invoice FROM src_invoice WHERE c_id_invoice IS NOT NULL "
            "GROUP BY c_id_invoice HAVING COUNT(*) > 1")}
        has_products = "products" not in self.profile.entity_blocked
        ph = ",".join("?" for _ in rules.LIVE_STATUSES)
        cur = c.execute(
            f"SELECT rowno, {', '.join(_cols(self.tables['invoice']))} FROM src_invoice "  # noqa: S608
            f"WHERE c_Status IN ({ph}) ORDER BY c_id_invoice, rowno", tuple(sorted(rules.LIVE_STATUSES)))
        names = list(self.tables["invoice"].stored_columns)
        import json as _json

        pending: list[tuple] = []
        for row in iter(lambda: cur.fetchmany(1000), []):
            for vals in row:
                self._tick()
                rowno, r = vals[0], dict(zip(names, vals[1:], strict=True))
                key, owner = r["id_invoice"], r["id_user"]
                self._count("services", "source")
                is_test = rules.flag01(r["is_test"])
                decision: str
                if key in dup_keys:
                    decision = "SOURCE_KEY_DUPLICATED"
                elif not rules.invoice_key_valid(key):
                    decision = "INVOICE_KEY_INVALID"
                elif is_test is True:
                    decision = "TEST_INVOICE_SKIPPED"
                elif is_test is None:
                    decision = "INVALID_SOURCE_ROW"
                else:
                    cust = c.execute("SELECT decision FROM cust WHERE id = ?", (owner,)).fetchone() \
                        if owner is not None else None
                    if owner is None or cust is None:
                        decision = "ORPHAN"
                    elif cust[0] != "IMPORT":
                        decision = "CUSTOMER_NOT_IMPORTED"
                    elif rules.panel_username(r["username"]) is None:
                        decision = "INVALID_USERNAME"
                    elif rules.blank_to_none(r["code_panel"]) is None:
                        decision = "NO_PANEL"
                    else:
                        decision = ""
                shape_key, shape_reason, shape = rules.legacy_shape(
                    r["code_panel"], r["Volume"], r["Service_time"], r["time_unit"], r["is_custom"])
                code_product = rules.blank_to_none(r["code_product"])
                named = (has_products and code_product is not None and r["is_custom"] == "0"
                         and c.execute("SELECT 1 FROM pcode WHERE code = ?", (code_product,)).fetchone() is not None)
                # decisions.ts:327-364 — the is_custom flag first; a NAMED product (non-custom,
                # code in the product table) is never shape-checked; only the hidden-shape path is.
                if decision == "":
                    if r["is_custom"] not in ("0", "1"):
                        decision, shape_reason = "UNSUPPORTED_SHAPE", "IS_CUSTOM_INVALID"
                    elif named:
                        decision = "NAMED_PRODUCT"
                    elif shape_key is None:
                        decision = "UNSUPPORTED_SHAPE"
                    else:
                        decision = "HIDDEN_SHAPE"
                rec = {
                    "record_type": "legacy_service_candidate",
                    "idempotency_key": f"legacy:service:{key}",
                    "invoice_key": key,
                    "owner_telegram_user_id": rules.telegram_id(owner),
                    "code_panel": rules.blank_to_none(r["code_panel"]),
                    "service_location": r.get("Service_location"),
                    "panel_account_username": r["username"],
                    "status_raw": r["Status"],
                    "shape_key": shape_key,
                    "shape_reason": shape_reason,
                    "shape": shape,
                    "product_path": "NAMED_PRODUCT" if named else "HIDDEN_SHAPE",
                    "code_product": code_product,
                    "decision": decision,
                    # Never provision/renew/delete on a panel. Live state is verified at adoption time.
                    "provision": False,
                    "requires_live_panel_check": True,
                    "provenance": self._provenance("invoice", key, rules.row_checksum("invoice:v1", [
                        key, owner, r["username"], r["Status"], r["is_test"], r["code_panel"], r["code_product"],
                        r["Volume"], r["Service_time"], r["time_unit"], r["is_custom"]])),
                }
                pending.append((rowno, key, owner, rules.blank_to_none(r["code_panel"]),
                                rules.panel_username(r["username"]), decision,
                                _json.dumps(rec, ensure_ascii=False, sort_keys=True)))
            if len(pending) >= 1000:
                c.executemany("INSERT INTO svc VALUES (?,?,?,?,?,?,?)", pending)
                pending.clear()
        if pending:
            c.executemany("INSERT INTO svc VALUES (?,?,?,?,?,?,?)", pending)

        # Ownership (plan.ts:311-342 withOwnershipRule): eligible candidates of two or more
        # DIFFERENT owners naming one panel account are all ambiguous. Several invoices of ONE
        # owner stay eligible; NEXA's adoption (P6) reports the later ones as conflicts.
        # Limitation: grouped by code_panel here; NEXA groups by the mapped panel id.
        eligible = ("NAMED_PRODUCT", "HIDDEN_SHAPE")
        c.execute("""CREATE TEMP TABLE ambiguous AS
            SELECT s.rowno FROM svc s JOIN (
                SELECT panel, uname, COUNT(DISTINCT owner) AS owners
                FROM svc WHERE decision IN (?, ?) GROUP BY panel, uname HAVING COUNT(DISTINCT owner) > 1
            ) g ON s.panel = g.panel AND s.uname = g.uname
            WHERE s.decision IN (?, ?)""", eligible + eligible)
        c.execute("UPDATE svc SET decision = 'AMBIGUOUS_OWNERSHIP' WHERE rowno IN (SELECT rowno FROM ambiguous)")

        services = self.open("records/services.jsonl")
        decisions: dict[str, int] = {}
        review_codes = {"ORPHAN", "CUSTOMER_NOT_IMPORTED", "INVALID_USERNAME", "NO_PANEL", "UNSUPPORTED_SHAPE",
                        "AMBIGUOUS_OWNERSHIP", "INVALID_SOURCE_ROW", "INVOICE_KEY_INVALID", "SOURCE_KEY_DUPLICATED"}
        for k, decision, rec_json in c.execute("SELECT k, decision, rec FROM svc ORDER BY k, rowno"):
            self._tick()
            rec = _json.loads(rec_json)
            rec["decision"] = decision
            decisions[decision] = decisions.get(decision, 0) + 1
            if decision == "TEST_INVOICE_SKIPPED":
                self._count("services", "skipped")
                continue
            if decision in review_codes:
                self._review(kind="service", code=decision, entity="services", table="invoice", pk=k,
                             severity="blocker" if decision in {"ORPHAN", "AMBIGUOUS_OWNERSHIP",
                                                                 "SOURCE_KEY_DUPLICATED"} else "warning",
                             data={"code_panel": rec["code_panel"], "status_raw": rec["status_raw"],
                                   "shape_reason": rec["shape_reason"]})
                continue
            rec["adoption_state"] = "PENDING_PANEL_MAPPING"
            services.write(rec)
            self._count("services", "converted")
        sc = self.out.counts.get("services", {})
        accounted = sc.get("converted", 0) + sc.get("review", 0) + sc.get("skipped", 0)
        if accounted != sc.get("source", 0):
            self.out.block("SERVICE_ACCOUNTING_MISMATCH")
        self.out.reconciliation["services"] = {"live_invoices": sum(decisions.values()),
                                               "decisions": dict(sorted(decisions.items())),
                                               "accounted": accounted == sc.get("source", 0)}
        self.out.warn("SERVICES_NEED_PANEL_MAPPING")

    def _panels(self) -> None:
        c = self.conn
        panels = self.open("records/panels.jsonl")
        ph = ",".join("?" for _ in rules.LIVE_STATUSES)
        has_loc = "Service_location" in self.tables["invoice"].stored_columns
        loc = "c_Service_location" if has_loc else "NULL"
        agg: dict[str, dict[str, Any]] = {}
        for code, location, total, live in c.execute(
            f"SELECT c_code_panel, {loc}, COUNT(*), SUM(CASE WHEN c_Status IN ({ph}) THEN 1 ELSE 0 END) "  # noqa: S608
            "FROM src_invoice GROUP BY c_code_panel, 2", tuple(sorted(rules.LIVE_STATUSES))):
            cp = rules.blank_to_none(code)
            if cp is None:
                continue
            a = agg.setdefault(cp, {"invoices": 0, "live_invoices": 0, "locations": set()})
            a["invoices"] += total
            a["live_invoices"] += live
            if location is not None and rules.js_trim(location):
                a["locations"].add(rules.js_trim(location))
        for cp in sorted(agg):
            a = agg[cp]
            panels.write({"record_type": "legacy_panel_code", "idempotency_key": f"legacy:panel:{cp}",
                          "code_panel": cp, "invoice_count": a["invoices"], "live_invoice_count": a["live_invoices"],
                          "service_locations": sorted(a["locations"]), "panel_id": None,
                          "mapping_state": "OPERATOR_MUST_MAP", "provenance": self._provenance("invoice", None)})
        self._count("panels", "source", len(agg))
        self._count("panels", "converted", len(agg))

    # ---- domain modules & coverage ----------------------------------------------------
    def coverage_for(self, table: str) -> dict[str, Any]:
        return self._coverage.setdefault(table, {})

    def _run_modules(self) -> None:
        from .modules import ModuleContext, modules_in_order

        ctx = ModuleContext(self)
        for module in modules_in_order():
            self.cancel.check()
            module.run(ctx)

    def _finish_coverage(self) -> None:
        """One coverage row per table in the dump. Nothing is silently ignored."""
        from .catalogue import CATALOGUE, CLASS_FA, EXCLUDED

        counts = self.out.counts
        core = {
            "user": {"exported": counts.get("customers", {}).get("converted", 0),
                     "review": counts.get("customers", {}).get("review", 0)},
            "invoice": {"archived": counts.get("invoices", {}).get("converted", 0),
                        "review": counts.get("invoices", {}).get("review", 0)},
            "product": {"exported": counts.get("products", {}).get("converted", 0),
                        "review": counts.get("products", {}).get("review", 0)},
        }
        unclassified = 0
        for name in sorted(self.tables):
            t = self.tables[name]
            spec = CATALOGUE.lookup(name)
            cov = {"table": name, "rows": t.rows, "columns": len(t.columns),
                   "stored_columns": list(t.stored_columns), "dropped_secret_columns": list(t.dropped_columns),
                   "unmapped_columns": list(t.unmapped_columns), "exported": 0, "archived": 0, "review": 0,
                   "skipped": 0, "redacted_cells": 0}
            if spec is not None:
                cov.update({"class": spec.klass, "class_fa": CLASS_FA[spec.klass], "module": spec.module,
                            "purpose_fa": spec.purpose_fa, "nexa_target": spec.nexa_target, "reason": spec.reason,
                            "sensitivity": spec.sensitivity, "evidence": list(spec.evidence),
                            "open_questions": list(spec.open_questions)})
            else:
                cov.update({"class": None, "class_fa": "طبقه‌بندی‌نشده", "module": None,
                            "reason": "No catalogue entry: meaning unknown, nothing migrated."})
            cov.update(core.get(name, {}) if name in core and t.sql_name else {})
            cov.update(self._coverage.get(name, {}))
            if t.rows == 0:
                cov["status"] = "empty"
            elif spec is None:
                cov["status"] = "blocked"
                unclassified += 1
            elif spec.klass == EXCLUDED:
                cov["status"] = "excluded"
                cov["skipped"] = t.rows
            elif name in core and not t.sql_name:
                cov["status"] = "blocked"
            elif name in core:
                cov.setdefault("status", "executed")
            elif "status" not in cov:
                cov["status"] = "blocked"
                cov.setdefault("reason_blocked", "no converter produced records for this table")
            accounted = cov["exported"] + cov["archived"] + cov["review"] + cov["skipped"]
            cov["accounted_rows"] = accounted
            self.out.coverage.append(cov)
        from .modules import registry_status

        if not registry_status()["ok"]:  # defence in depth; pipeline already refuses to start
            self.out.block("CATALOGUE_INCOMPLETE")
            self.blocker_messages.setdefault("CATALOGUE_INCOMPLETE",
                                             "کاتالوگ جدول‌ها کامل بارگذاری نشده؛ هیچ نتیجه‌ای قابل اتکا نیست.")
        incomplete = [c["table"] for c in self.out.coverage
                      if c["rows"] and c["class"] is not None and (
                          c["status"] == "blocked" or (c["status"] != "excluded" and c["accounted_rows"] != c["rows"]))]
        if incomplete:
            self.out.block("COVERAGE_INCOMPLETE", len(incomplete))
            self.blocker_messages.setdefault(
                "COVERAGE_INCOMPLETE",
                "برای برخی جدول‌های طبقه‌بندی‌شده همه ردیف‌ها پوشش داده نشدند (مثلاً ستون‌ها با قرارداد نمی‌خوانند)؛ "
                "بسته تا بررسی مسدود است.")
        if unclassified:
            self.out.block("UNCLASSIFIED_TABLE", unclassified)
            self.blocker_messages.setdefault(
                "UNCLASSIFIED_TABLE",
                "جدول غیرخالی بدون طبقه‌بندی در بکاپ وجود دارد؛ تا مستندسازی تصمیم آن، بسته مسدود است.")

    # ---- schema-level findings & mapping table -----------------------------------------
    def _schema_findings(self) -> None:
        p = self.profile
        for code in sorted(set(p.entity_blocked.values())):
            self.out.block(code)
        if any(t.never_read_present for t in self.tables.values()):
            self.out.warn("SECRET_COLUMNS_DROPPED")

    def _mapping_table(self) -> None:
        blocked = self.profile.entity_blocked

        def st(entity: str, default: str = "mapped") -> str:
            return "blocked" if entity in blocked else default

        module_entries = list(self.out.mapping)  # added by domain modules via ctx.mapping()
        self.out.mapping = [
            {"entity": "customers", "source_table": "user",
             "source_columns": ["id", "User_Status", "username", "agent", "number(class only)", "limit_usertest"],
             "target": "customer (legacy_import_map) — telegram_user_id", "status": "mapped",
             "note": "Rules from legacy-importer decisions.ts; invalid/duplicate/unknown-status rows → review."},
            {"entity": "wallet_balances", "source_table": "user", "source_columns": ["Balance"],
             "target": "wallet ledger CREDIT, reason MIGRATION_OPENING_BALANCE, ref legacy:opening:<tg>",
             "status": "mapped" if self.money_unit == "toman" else "blocked",
             "note": "Unit: NEXA owner decision 7 (Toman→IRT, 1 Toman = 1 minor). Operator must confirm."},
            {"entity": "debts", "source_table": "user", "source_columns": ["Balance < 0"],
             "target": "legacy_wallet_debts (PENDING_REVIEW, magnitude, never collected)",
             "status": "mapped" if self.money_unit == "toman" else "blocked", "note": "Owner decision 6."},
            {"entity": "products", "source_table": "product",
             "source_columns": ["id", "code_product", "name_product", "price_product", "Volume_constraint",
                                "Service_time", "Location", "Category", "agent"],
             "target": "legacy_product_reviews (review only)", "status": st("products", "partial"),
             "note": "Units of Volume_constraint/Service_time and meaning of 0 are open (OQ-LPR-02); proposals only."},
            {"entity": "categories", "source_table": "product", "source_columns": ["Category|category"],
             "target": "labels for operator review", "status": st("categories", "partial"),
             "note": "No Mirza category table is evidenced; labels only."},
            {"entity": "invoices", "source_table": "invoice",
             "source_columns": list(rules.REQUIRED_COLUMNS["invoice"]) + ["Service_location", "time_sell", "name_product"],
             "target": "legacy_invoice_archive (history only)", "status": st("invoices"),
             "note": "Classified with NEXA archive classes; never payments or revenue."},
            {"entity": "services", "source_table": "invoice (live statuses)",
             "source_columns": ["id_invoice", "id_user", "username", "code_panel", "Volume", "Service_time",
                                "time_unit", "is_custom", "code_product"],
             "target": "legacy_service_candidates (adoption via NEXA panel map)", "status": st("services", "partial"),
             "note": "No panel calls; ownership rules from importer.md:380-391."},
            {"entity": "panels", "source_table": "invoice.code_panel", "source_columns": ["code_panel", "Service_location"],
             "target": "nexa-legacy-panel-map/v1 (operator)", "status": st("panels", "partial"),
             "note": "Panel tables (marzban_panel…) are unclassified; codes only."},
            {"entity": "orders", "source_table": None, "source_columns": [],
             "target": "none — adoption creates LEGACY_ADOPTION orders inside NEXA", "status": "absent",
             "note": "By design, invoices never become orders/payments (migration-order-origin.md)."},
            {"entity": "payments", "source_table": "Payment_report",
             "source_columns": ["id", "id_user", "id_order", "time", "at_updated", "price", "dec_not_confirmed",
                                "Payment_Method", "payment_Status", "message_id", "receipt_trace"],
             "target": "read-only payment history archive (legacy_payment_history)", "status": "archived",
             "note": "History only: never a NEXA payment, revenue or wallet credit (modules/payments.py)."},
            {"entity": "wallet_history", "source_table": "wallet_transaction",
             "source_columns": ["id", "id_user", "type", "amount", "balance_before", "balance_after", "ref_id",
                                "source", "reason", "created_at"],
             "target": "read-only wallet history archive + per-customer check against the snapshot",
             "status": "archived",
             "note": "Never applied to balances; the snapshot user.Balance stays the only opening figure."},
            {"entity": "resellers", "source_table": "user.agent", "source_columns": ["agent"],
             "target": "none (agents import as ordinary customers)", "status": "blocked",
             "note": "OQ-LWD-03: reseller role/pricing requires an owner decision."},
        ]
        self.out.mapping.extend(module_entries)

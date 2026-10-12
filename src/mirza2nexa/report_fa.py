"""Persian labels and the downloadable, self-contained Persian reports.

* :data:`LABELS` — Persian labels for report keys, coverage statuses, classes, review/blocker
  codes and blocker categories. The web UI loads them from ``GET /api/labels`` so the browser
  and the downloadable HTML report use the same wording. Unknown keys fall back to a
  humanised form of the key; the key itself always stays visible (small) next to its label.
* :func:`render_report_html` — an RTL HTML report built from ``report.json`` only: inline CSS,
  **no scripts**, every value HTML-escaped. It contains aggregates only (counts, sums, codes,
  table names); ``report.json`` holds no record-level personal data and none is added here.
* :func:`render_coverage_csv` — the coverage table as UTF-8 CSV with a BOM (opens correctly in
  Excel) and spreadsheet-formula neutralisation for every cell.
"""

from __future__ import annotations

import csv
import html
import io
import re
from typing import Any

from .catalogue import CLASS_FA
from .review_taxonomy import GROUPS_FA as _TAXONOMY_GROUPS_FA

# ---------------------------------------------------------------------------------- labels
COVERAGE_STATUS_FA: dict[str, str] = {
    "executed": "اجرا شد",
    "archived": "آرشیو شد",
    "review": "نیازمند بررسی",
    "blocked": "مسدود",
    "empty": "بدون داده",
    "excluded": "غیرقابل انتقال",
}

COVERAGE_STATUS_HELP_FA: dict[str, str] = {
    "executed": "ردیف‌ها به رکوردهای عملیاتی بسته تبدیل شدند.",
    "archived": "ردیف‌ها فقط به‌صورت آرشیو فقط‌خواندنی منتقل شدند.",
    "review": "ردیف‌ها منتقل شدند اما پیش از استفاده نیاز به تصمیم/بررسی دارند.",
    "blocked": "این جدول پوشش داده نشده و بسته را مسدود می‌کند.",
    "empty": "جدول در بکاپ هست اما هیچ ردیفی ندارد.",
    "excluded": "طبق تصمیم مستند، این جدول منتقل نمی‌شود (فقط شمارش می‌شود).",
}

READINESS_FA: dict[str, str] = {"ready": "آماده بررسی Importer", "blocked": "مسدود"}

ENTITY_FA: dict[str, str] = {
    "customers": "مشتریان",
    "wallet": "کیف پول",
    "wallet_balances": "موجودی کیف پول",
    "debts": "بدهی‌ها",
    "products": "محصولات",
    "categories": "دسته‌بندی‌ها",
    "services": "سرویس‌ها",
    "orders": "سفارش‌ها",
    "invoices": "فاکتورها",
    "resellers": "نمایندگان",
    "panels": "پنل‌ها",
    "payments": "پرداخت‌ها",
    "wallet_history": "تاریخچه تراکنش‌های کیف پول",
    "wallet_history_checks": "کنترل زنجیره تراکنش‌های کیف پول",
    "service_operations": "عملیات سرویس‌ها",
    "panel_registry": "فهرست پنل‌ها",
    "panels_registry": "فهرست پنل‌ها",
    "manual_inventory": "موجودی دستی سرویس‌ها",
    "service_requests": "درخواست‌های سرویس",
    "categories_table": "جدول دسته‌بندی‌ها",
    "category_catalogue": "فهرست دسته‌بندی‌ها",
    "agents": "نمایندگی",
    "engagement": "تعامل و بازاریابی",
    "support": "پشتیبانی",
    "configuration": "تنظیمات",
    "archive": "آرشیو",
    "discounts": "کدهای تخفیف",
    "gift_codes": "کدهای هدیه",
    "tickets": "تیکت‌ها",
    "trials": "سرویس‌های تست",
}

# Persian labels for reconciliation / count keys. Unknown keys are humanised.
KEY_FA: dict[str, str] = {
    # generic counters
    "source": "مبدأ",
    "source_rows": "ردیف‌های مبدأ",
    "source_users": "کاربران مبدأ",
    "rows": "ردیف‌ها",
    "records": "رکوردها",
    "converted": "تبدیل‌شده",
    "exported": "خروجی‌گرفته",
    "archived": "آرشیوشده",
    "review": "نیازمند بررسی",
    "in_review": "در صف بررسی",
    "skipped": "کنارگذاشته",
    "excluded": "منتقل‌نشده",
    "blocked": "مسدود",
    "total": "مجموع",
    "count": "تعداد",
    "amount_minor": "مبلغ (کوچک‌ترین واحد)",
    "sum_minor": "جمع مبلغ",
    "amount": "مبلغ",
    "unit": "واحد",
    "currency": "ارز",
    "note": "توضیح",
    "notes": "توضیحات",
    "equation": "معادله تطبیق",
    "accounted": "همه ردیف‌ها حساب شدند",
    "accounted_rows": "ردیف‌های حساب‌شده",
    "accounted_amount_minor": "مبلغ حساب‌شده",
    "balanced": "تراز است",
    "matches": "مطابقت دارد",
    "match": "مطابقت",
    "mismatch": "مغایرت",
    "mismatches": "مغایرت‌ها",
    "ok": "درست",
    "decisions": "تصمیم‌ها",
    "classifications": "طبقه‌بندی‌ها",
    "by_status": "به تفکیک وضعیت",
    "by_method": "به تفکیک روش",
    "by_type": "به تفکیک نوع",
    "by_kind": "به تفکیک نوع",
    "by_tier": "به تفکیک سطح",
    "by_table": "به تفکیک جدول",
    "by_reason": "به تفکیک دلیل",
    "by_currency": "به تفکیک ارز",
    "by_panel": "به تفکیک پنل",
    "tables": "جدول‌ها",
    "orphans": "یتیم (بدون مالک)",
    "orphan": "یتیم",
    "duplicates": "تکراری",
    "duplicate_keys": "کلیدهای تکراری",
    "duplicate_ids": "شناسه‌های تکراری",
    "missing": "ناموجود",
    "missing_customer": "بدون مشتری",
    "missing_user": "کاربر ناموجود",
    "unknown_user": "کاربر ناشناخته",
    "without_customer": "بدون مشتری",
    "unparseable": "غیرقابل خواندن",
    "unparsable": "غیرقابل خواندن",
    "unreadable": "ناخوانا",
    "invalid": "نامعتبر",
    "redacted_cells": "خانه‌های پوشانده‌شده",
    # statuses
    "succeeded": "موفق",
    "success": "موفق",
    "paid": "پرداخت‌شده",
    "failed": "ناموفق",
    "pending": "در انتظار",
    "unknown": "نامعلوم",
    "UNKNOWN": "نامعلوم",
    "cancelled": "لغوشده",
    "canceled": "لغوشده",
    "expired": "منقضی",
    "refunded": "بازپرداخت‌شده",
    "active": "فعال",
    "inactive": "غیرفعال",
    "disabled": "غیرفعال",
    "open": "باز",
    "closed": "بسته",
    "answered": "پاسخ‌داده‌شده",
    "used": "استفاده‌شده",
    "unused": "استفاده‌نشده",
    # wallet (core)
    "source_readable_balances": "موجودی‌های خوانا در بکاپ",
    "source_unreadable_balances": "موجودی‌های ناخوانا",
    "opening_credits": "موجودی افتتاحیه (اعتبار)",
    "legacy_debts": "بدهی‌های قدیمی",
    "zero_balances": "موجودی صفر",
    "held_for_review": "نگه‌داشته برای بررسی",
    "not_customers_invalid_identity": "غیرمشتری (شناسه نامعتبر)",
    "independent_recount": "بازشماری مستقل",
    "independent_recount_matches": "بازشماری مستقل مطابق است",
    "source_recount": "بازشماری از بکاپ",
    "readable_count": "تعداد خوانا",
    "readable_sum_minor": "جمع خوانا",
    "credits_count": "تعداد اعتبارها",
    "credits_amount": "جمع اعتبارها",
    "debts_count": "تعداد بدهی‌ها",
    "debts_amount": "جمع بدهی‌ها",
    "customers_with_two_money_records": "مشتریان با دو رکورد مالی",
    # invoices / services (core)
    "historical_price_sum": "جمع قیمت‌های تاریخی",
    "live_invoices": "فاکتورهای فعال",
    "LIVE_CANDIDATE": "کاندید سرویس فعال",
    "NOT_LIVE": "غیرفعال",
    "NO_PANEL": "بدون پنل",
    "ORPHAN_OWNER": "مالک ناموجود",
    "TEST": "تست",
    "AMBIGUOUS_OWNERSHIP": "مالکیت مبهم",
    "NAMED_PRODUCT": "محصول مشخص",
    "ORPHAN": "یتیم",
    "TEST_INVOICE_SKIPPED": "فاکتور تست (کنار گذاشته شد)",
    "UNSUPPORTED_SHAPE": "شکل پشتیبانی‌نشده",
    "CUSTOM": "سفارشی",
    # resellers
    "agents_total": "تعداد نمایندگان",
    # payments / wallet history (domain modules)
    "historical_payments": "پرداخت‌های تاریخی",
    "payments_total": "مجموع پرداخت‌ها",
    "succeeded_amount_minor": "جمع پرداخت‌های موفق",
    "failed_amount_minor": "جمع پرداخت‌های ناموفق",
    "pending_amount_minor": "جمع پرداخت‌های در انتظار",
    "unknown_amount_minor": "جمع پرداخت‌های نامعلوم",
    "creates_payment": "ایجاد پرداخت",
    "affects_wallet": "تأثیر بر کیف پول",
    "transactions": "تراکنش‌ها",
    "wallet_transactions": "تراکنش‌های کیف پول",
    "credits": "واریزها",
    "debits": "برداشت‌ها",
    "refunds": "بازپرداخت‌ها",
    "chain_gaps": "شکاف در زنجیره موجودی",
    "chain_breaks": "گسست در زنجیره موجودی",
    "chains_checked": "زنجیره‌های بررسی‌شده",
    "matches_snapshot": "مطابق با موجودی فعلی",
    "differs_from_snapshot": "متفاوت با موجودی فعلی",
    "no_history": "بدون تاریخچه",
    "snapshot": "موجودی فعلی (Snapshot)",
    "balance_after": "موجودی پس از تراکنش",
    "net_minor": "خالص",
    # services ext / panels / agents / engagement / support
    "operations": "عملیات",
    "renewals": "تمدیدها",
    "extensions": "افزایش حجم/زمان",
    "panels_total": "تعداد پنل‌ها",
    "agents": "نمایندگان",
    "requests": "درخواست‌ها",
    "approved": "تأییدشده",
    "rejected": "ردشده",
    "tickets": "تیکت‌ها",
    "messages": "پیام‌ها",
    "discount_codes": "کدهای تخفیف",
    "gift_codes": "کدهای هدیه",
    "redemptions": "استفاده‌ها",
    "affiliates": "زیرمجموعه‌ها",
}

KEY_HELP_FA: dict[str, str] = {
    "equation": "رابطه‌ای که باید میان مبالغ مبدأ و خروجی برقرار باشد.",
    "balanced": "اگر «خیر» باشد بسته مسدود است.",
    "independent_recount": "شمارش دوباره رکوردهای نوشته‌شده با مسیر کد جداگانه.",
    "source_recount": "شمارش دوباره مستقیم از داده مبدأ، مستقل از مبدل.",
    "held_for_review": "مبالغی که به‌دلیل ابهام وارد نشدند و در صف بررسی هستند.",
    "historical_price_sum": "فقط شاهد تاریخی است؛ درآمد یا پرداخت محسوب نمی‌شود.",
    "chain_gaps": "تراکنش‌هایی که موجودی قبل/بعد آن‌ها با تراکنش قبلی پیوسته نیست.",
    "matches_snapshot": "کاربرانی که جمع تاریخچه با موجودی فعلی‌شان برابر است.",
    "differs_from_snapshot": "کاربرانی که جمع تاریخچه با موجودی فعلی‌شان برابر نیست (نیازمند بررسی).",
    "amount_minor": "مبلغ بدون هیچ تبدیل یا ضریب، با واحد اعلام‌شده.",
}

REVIEW_CODE_FA: dict[str, str] = {
    "INVALID_IDENTITY": "شناسه تلگرام نامعتبر",
    "DUPLICATE_SOURCE_ID": "شناسه تلگرام تکراری",
    "BALANCE_UNREADABLE": "موجودی ناخوانا",
    "BALANCE_OUT_OF_RANGE": "موجودی خارج از محدوده",
    "STATUS_UNKNOWN": "وضعیت کاربر نامعلوم",
    "NEGATIVE_BALANCE": "موجودی منفی (بدهی)",
    "CODE_DUPLICATED": "کد محصول تکراری",
    "SOURCE_KEY_DUPLICATED": "کلید اصلی تکراری",
    "INVOICE_WITHOUT_CUSTOMER": "فاکتور بدون مشتری",
    "ORPHAN": "سرویس بدون مالک",
    "CUSTOMER_NOT_IMPORTED": "مالک سرویس وارد نشده",
    "INVALID_USERNAME": "نام کاربری پنل نامعتبر",
    "NO_PANEL": "پنل نامشخص",
    "UNSUPPORTED_SHAPE": "شکل سرویس پشتیبانی‌نشده",
    "AMBIGUOUS_OWNERSHIP": "مالکیت مبهم",
    "INVALID_SOURCE_ROW": "ردیف مبدأ نامعتبر",
    "INVOICE_KEY_INVALID": "شناسه فاکتور نامعتبر",
    "BALANCE_CHANGED_SINCE_PREVIOUS": "تغییر موجودی نسبت به بکاپ قبلی",
    "UNREPRESENTABLE_CELL": "مقدار غیرقابل نمایش",
    "ARCHIVE_DUPLICATE_PK": "کلید تکراری در آرشیو",
    "TABLE_NEEDS_DECISION": "جدول نیازمند تصمیم مالک",
}

BLOCKER_CODE_FA: dict[str, str] = {
    "MONEY_UNIT_UNCONFIRMED": "واحد پول تأیید نشده",
    "MONEY_UNIT_NOT_IRT": "واحد پول غیرتومانی",
    "FINANCIAL_RECONCILIATION_MISMATCH": "مغایرت تطبیق مالی",
    "SOURCE_SCHEMA_MISSING_COLUMN": "ستون الزامی ناموجود",
    "SOURCE_SCHEMA_MISSING_TABLE": "جدول الزامی ناموجود",
    "SOURCE_KEY_DUPLICATED": "کلید اصلی تکراری",
    "UNREPRESENTABLE_CELLS": "مقادیر غیرقابل خواندن",
    "PANEL_TARGET_UNKNOWN_CODE": "پنل مقصد برای کد پنل ناموجود",
    "BALANCE_CHANGED_SINCE_PREVIOUS": "تغییر موجودی نسبت به بکاپ قبلی",
    "DUPLICATE_SOURCE_ID": "شناسه تلگرام تکراری",
    "SERVICE_ACCOUNTING_MISMATCH": "مغایرت شمارش سرویس‌ها",
    "SOURCE_RECOUNT_MISMATCH": "مغایرت بازشماری مستقل",
    "TABLE_DEFINED_TWICE": "تعریف تکراری جدول",
    "UNCLASSIFIED_TABLE": "جدول طبقه‌بندی‌نشده",
}

BLOCKER_CATEGORY_FA: dict[str, str] = {
    "financial": "مالی",
    "schema": "ساختار داده (Schema)",
    "data_quality": "کیفیت داده",
    "security": "امنیت",
}

BLOCKER_CATEGORY: dict[str, str] = {
    "MONEY_UNIT_UNCONFIRMED": "financial",
    "MONEY_UNIT_NOT_IRT": "financial",
    "FINANCIAL_RECONCILIATION_MISMATCH": "financial",
    "SOURCE_RECOUNT_MISMATCH": "financial",
    "BALANCE_CHANGED_SINCE_PREVIOUS": "financial",
    "SOURCE_SCHEMA_MISSING_COLUMN": "schema",
    "SOURCE_SCHEMA_MISSING_TABLE": "schema",
    "UNCLASSIFIED_TABLE": "schema",
    "TABLE_DEFINED_TWICE": "schema",
    "SOURCE_KEY_DUPLICATED": "data_quality",
    "DUPLICATE_SOURCE_ID": "data_quality",
    "UNREPRESENTABLE_CELLS": "data_quality",
    "PANEL_TARGET_UNKNOWN_CODE": "data_quality",
    "SERVICE_ACCOUNTING_MISMATCH": "data_quality",
}

_CAT_PATTERNS = (
    ("security", re.compile(r"SECRET|SECUR|TOKEN|CREDENTIAL|PASSWORD|PRIVATE")),
    ("financial", re.compile(r"MONEY|FINANC|BALANCE|WALLET|PAYMENT|LEDGER|RECONCIL|RECOUNT|AMOUNT|DEBT|CURRENCY")),
    ("schema", re.compile(r"SCHEMA|TABLE|COLUMN|UNCLASSIFIED|VERSION")),
)


def blocker_category(code: str) -> str:
    code = str(code or "")
    if code in BLOCKER_CATEGORY:
        return BLOCKER_CATEGORY[code]
    up = code.upper()
    for cat, rx in _CAT_PATTERNS:
        if rx.search(up):
            return cat
    return "data_quality"


# Persian reason for each mapping entity that can be blocked (mapping notes are English).
ENTITY_BLOCK_REASON_FA: dict[str, str] = {
    "customers": "ستون‌ها/جدول کاربران با قرارداد NEXA منطبق نیست.",
    "wallet_balances": "واحد پول توسط اپراتور تأیید نشده یا تومان نیست؛ موجودی‌ها وارد نمی‌شوند.",
    "debts": "واحد پول توسط اپراتور تأیید نشده یا تومان نیست؛ بدهی‌ها وارد نمی‌شوند.",
    "products": "ستون‌های الزامی جدول محصول موجود نیست.",
    "categories": "ستون دسته‌بندی محصول موجود نیست.",
    "invoices": "ستون‌های الزامی جدول فاکتور موجود نیست.",
    "services": "اطلاعات لازم برای تشخیص سرویس فعال موجود نیست.",
    "panels": "کد پنل در فاکتورها موجود نیست.",
    "payments": "پرداخت‌ها هنوز نگاشت تأییدشده به NEXA ندارند؛ فقط به‌صورت تاریخچه نگه داشته می‌شوند.",
    "resellers": "نقش و قیمت‌گذاری نمایندگی نیازمند تصمیم مالک NEXA است؛ نمایندگان فقط مشتری عادی می‌شوند.",
}

REVIEW_KIND_FA: dict[str, str] = {
    "customer": "مشتری", "wallet": "کیف پول", "service": "سرویس", "invoice": "فاکتور", "product": "محصول",
    "archive": "آرشیو", "table": "جدول", "payment": "پرداخت", "wallet_history": "تاریخچه کیف پول",
    "agent": "نمایندگی", "support": "پشتیبانی", "engagement": "تعامل", "configuration": "تنظیمات",
    "panel": "پنل", "category": "دسته‌بندی",
}

MAPPING_STATUS_FA: dict[str, str] = {
    "mapped": "نگاشت‌شده", "blocked": "مسدود", "absent": "در بکاپ نیست", "partial": "ناقص",
    "archived": "آرشیو", "review": "نیازمند بررسی",
}

IMPORTER_WARNING_FA = (
    "Importer نکسا هنوز ساخته نشده است. قالب این بسته یک قرارداد پیشنهادی است و قابلیت ورود (Import) آن "
    "به NEXA تأیید نشده است. از این بسته در سرور اصلی استفاده نکنید."
)


# ---- migration readiness (``migration-readiness.json``) ------------------------------------
READINESS_DISCLAIMER_FA = (
    "«آماده» فقط یعنی این بسته از نظر مبدل برای اجرای آزمایشی (Dry-run) یک Importer آینده مناسب است؛ "
    "این به معنی مجوز Import در NEXA نیست. Importer نکسا هنوز ساخته نشده و هیچ داده‌ای وارد NEXA نشده است."
)

READINESS_VERDICT_FA: dict[str, str] = {
    "NOT_READY_FOR_IMPORT": "آماده Import نیست",
    "READY_FOR_IMPORTER_DRY_RUN": "آماده اجرای آزمایشی Importer (نه Import واقعی)",
    "BLOCKED": "مسدود",
}

READINESS_SECTION_FA: dict[str, str] = {
    "verdict": "نتیجه کلی",
    "package_health": "سلامت بسته",
    "financial": "تطبیق مالی",
    "coverage": "پوشش جدول‌ها",
    "ownership": "مالکیت سرویس‌ها",
    "panels": "نگاشت پنل‌ها",
    "review": "موارد بررسی",
    "owner_decisions": "تصمیم‌های لازم مالک",
    "import_blockers": "موانع Import",
}

READINESS_KEY_FA: dict[str, str] = {
    "readiness_version": "نسخه گزارش آمادگی",
    "converter_version": "نسخه مبدل",
    "package_contract_version": "نسخه قرارداد بسته",
    "import_id": "شناسه Import",
    "generated_at": "زمان تولید",
    "readiness": "آمادگی بسته",
    "catalogue_ok": "فهرست جدول‌ها کامل است",
    "balanced": "تراز مالی برقرار است",
    "source_recount_matches": "بازشماری مستقل از بکاپ مطابق است",
    "positive_balances": "موجودی‌های مثبت (اعتبار)",
    "debts": "بدهی‌ها",
    "net_minor": "خالص (کوچک‌ترین واحد)",
    "money_unit": "واحد پول",
    "confirmed_by": "تأییدکننده",
    "history_differences": "اختلاف تاریخچه با موجودی فعلی",
    "by_cause": "بر اساس علت",
    "proven": "علت اثبات‌شده",
    "probable": "علت محتمل",
    "unexplained": "بدون توضیح",
    "payments_unknown_status": "پرداخت‌های با وضعیت نامعلوم",
    "cases_needing_review": "موارد مالی نیازمند بررسی",
    "tables_total": "کل جدول‌ها",
    "non_empty": "جدول‌های دارای داده",
    "by_status": "بر اساس وضعیت",
    "by_class": "بر اساس طبقه",
    "uncovered": "جدول‌های پوشش‌داده‌نشده",
    "total": "مجموع",
    "by_decision": "بر اساس تصمیم",
    "needs_manual_review": "نیازمند بررسی دستی",
    "confirmed_transfers": "انتقال مالکیت تأییدشده",
    "registry_rows": "ردیف‌های فهرست پنل",
    "codes_in_invoices": "کدهای پنل در فاکتورها",
    "mapped": "نگاشت‌شده",
    "pending_mapping": "در انتظار نگاشت",
    "status": "وضعیت",
    "total_items": "کل موارد",
    "distinct_records": "رکوردهای متمایز",
    "items": "موارد",
    "records": "رکوردها",
    "resolvable_by_code": "قابل حل با کد",
    "archive_only": "فقط آرشیو",
    "owner_decision": "تصمیم مالک",
    "adoption_time": "هنگام Adoption",
    "auto_resolved_with_evidence": "حل خودکار با شواهد",
    "blocker_items": "موارد مسدودکننده",
    "owner_decisions_needed": "تصمیم‌های لازم مالک",
    "adoption_stage": "موکول به مرحله Adoption",
    "count": "تعداد",
    "amount_minor": "مبلغ (کوچک‌ترین واحد)",
    "currency": "واحد",
    "blocking": "مسدودکننده",
    "independent_recount_matches": "بازشماری مستقل مطابق است",
    "items_per_record": "مورد به ازای هر رکورد",
    "owner_decision_items": "موارد نیازمند تصمیم مالک",
    "conflict_warnings_before": "هشدار تعارض (پیش از تحلیل)",
    "conflict_warnings_after": "هشدار تعارض (پس از تحلیل)",
    "proven_by_data": "اثبات‌شده با داده",
    "admin_review": "بررسی مدیر (جدا از اثبات)",
    "admin_approved_unverified": "تأیید مدیر (اثبات نشده)",
    "pending": "در انتظار تصمیم",
    "rejected": "رد شده — قرنطینه",
    "quarantined": "قرنطینه سیستمی",
    "stale": "تصمیم‌های کهنه",
    "sealed": "مهر و موم شده",
    "admin_approved_unverified_is_proof": "تأیید مدیر اثبات است؟",
    # panels.targets — operator-selected NEXA target panel type (panel_targets.py)
    "targets": "پنل مقصد در NEXA",
    "panels": "کدهای پنل",
    "selected": "دارای پنل مقصد انتخاب‌شده",
    "connect_existing": "اتصال به پنل موجود NEXA",
    "create_in_nexa_then_connect": "ایجاد در NEXA از مسیر امن و سپس اتصال",
    "unmapped": "بدون پنل مقصد",
    "by_provider": "نوع پنل مقصد",
    "unknown_codes": "کدهای انتخاب‌شده ناموجود در بکاپ",
}

# Ownership decisions for service candidates (``convert.py`` services decisions + readiness extras).
OWNERSHIP_DECISION_FA: dict[str, str] = {
    # modules/ownership.py DECISIONS
    "CONFIRMED_CURRENT_OWNER": "مالک فعلی تأییدشده",
    "NO_CONFLICT": "بدون تعارض",
    "AMBIGUOUS_OWNER": "مالک مبهم",
    "INVALID_OWNER": "مالک نامعتبر",
    "HIDDEN_SHAPE": "مالک اثبات‌شده — شکل سرویس پنهان",
    "NAMED_PRODUCT": "مالک اثبات‌شده — محصول مشخص",
    "PROVEN": "مالکیت اثبات‌شده",
    "PROVEN_OWNER": "مالکیت اثبات‌شده",
    "CONFIRMED_TRANSFER": "انتقال مالکیت تأییدشده",
    "TRANSFER_CONFIRMED": "انتقال مالکیت تأییدشده",
    "NEEDS_MANUAL_REVIEW": "نیازمند بررسی دستی",
    "MANUAL_REVIEW": "نیازمند بررسی دستی",
    "AMBIGUOUS_OWNERSHIP": "مالکیت مبهم",
    "ORPHAN": "سرویس بدون مالک",
    "ORPHAN_OWNER": "مالک ناموجود",
    "CUSTOMER_NOT_IMPORTED": "مالک سرویس وارد نشده",
    "INVALID_USERNAME": "نام کاربری پنل نامعتبر",
    "NO_PANEL": "پنل نامشخص",
    "UNSUPPORTED_SHAPE": "شکل سرویس پشتیبانی‌نشده",
    "INVALID_SOURCE_ROW": "ردیف مبدأ نامعتبر",
    "INVOICE_KEY_INVALID": "شناسه فاکتور نامعتبر",
    "SOURCE_KEY_DUPLICATED": "کلید اصلی تکراری",
    "TEST_INVOICE_SKIPPED": "فاکتور تست (کنار گذاشته شد)",
    "NOT_LIVE": "غیرفعال (فقط تاریخچه)",
}

# Why a wallet's history sum differs from its current balance snapshot.
HISTORY_CAUSE_FA: dict[str, str] = {
    # modules/wallet_history.py CAUSES
    "UNLOGGED_TOPUPS": "شارژهای ثبت‌نشده در تاریخچه",
    "UNLOGGED_ADMIN_ADJUSTMENTS": "تغییرات ثبت‌نشده مدیر",
    "UNLOGGED_PURCHASES": "خریدهای ثبت‌نشده در تاریخچه",
    "UNLOGGED_SERVICE_OPERATIONS": "عملیات سرویس ثبت‌نشده (تمدید/افزایش)",
    "UNLOGGED_WHEEL_PRIZES": "جایزه‌های گردونه ثبت‌نشده",
    "UNLOGGED_MIXED_ACTIVITY": "ترکیبی از فعالیت‌های ثبت‌نشده",
    "UNIT_SUSPECT": "احتمال ناهمخوانی واحد پول",
    "ACTIVITY_AFTER_HISTORY_INEXACT": "فعالیت پس از تاریخچه با زمان نادقیق",
    "MATCHES_SNAPSHOT": "مطابق با موجودی فعلی",
    "DIFFERS_FROM_SNAPSHOT": "متفاوت با موجودی فعلی",
    "NO_SNAPSHOT": "موجودی فعلی ندارد",
    "ORDER_UNPROVEN": "ترتیب تراکنش‌ها اثبات‌نشده",
    "HISTORY_UNPARSEABLE": "تاریخچه غیرقابل خواندن",
    "HISTORY_STARTED_LATE": "شروع دیرهنگام ثبت تاریخچه",
    "PRE_HISTORY_BALANCE": "موجودی پیش از شروع تاریخچه",
    "MANUAL_ADMIN_ADJUSTMENT": "تغییر دستی توسط مدیر",
    "ADMIN_ADJUSTMENT": "تغییر دستی توسط مدیر",
    "UNLOGGED_PURCHASE": "خرید ثبت‌نشده در تاریخچه",
    "UNLOGGED_CHANGE": "تغییر ثبت‌نشده در تاریخچه",
    "CHAIN_GAP": "شکاف در زنجیره موجودی",
    "CHAIN_BREAK": "گسست در زنجیره موجودی",
    "REFUND": "بازپرداخت",
    "ROUNDING": "گردکردن",
    "UNIT_MISMATCH": "ناهمخوانی واحد پول",
    "UNKNOWN": "نامعلوم",
    "UNEXPLAINED": "بدون توضیح",
}

REVIEW_GROUP_FA: dict[str, str] = {
    **_TAXONOMY_GROUPS_FA,
    "financial": "مالی",
    "wallet": "کیف پول",
    "wallet_history": "تاریخچه کیف پول",
    "payments": "پرداخت‌ها",
    "debts": "بدهی‌ها",
    "identity": "شناسه کاربران",
    "customers": "مشتریان",
    "services": "سرویس‌ها",
    "ownership": "مالکیت سرویس‌ها",
    "panels": "پنل‌ها",
    "products": "محصولات",
    "invoices": "فاکتورها",
    "agents": "نمایندگی",
    "engagement": "تعامل و بازاریابی",
    "support": "پشتیبانی",
    "configuration": "تنظیمات",
    "archive": "آرشیو",
    "tables": "جدول‌ها",
    "data_quality": "کیفیت داده",
    "other": "سایر",
}

PANEL_STATUS_FA: dict[str, str] = {
    "PENDING_OWNER_MAPPING": "در انتظار نگاشت توسط مالک",
    "MAPPED": "نگاشت کامل",
    "PARTIALLY_MAPPED": "نگاشت ناقص",
    "NO_PANELS": "پنلی وجود ندارد",
    "NOT_APPLICABLE": "موضوعیت ندارد",
}

PACKAGE_READINESS_FA: dict[str, str] = {"ready": "آماده", "blocked": "مسدود"}

MONEY_UNIT_FA: dict[str, str] = {"toman": "تومان", "rial": "ریال", "IRT": "تومان", "IRR": "ریال"}

CONFIRMED_BY_FA: dict[str, str] = {"operator": "اپراتور", "evidence": "شواهد داخل بکاپ"}

IMPORT_BLOCKER_FA: dict[str, str] = {
    "IMPORTER_NOT_BUILT": "Importer نکسا ساخته نشده",
    "PACKAGE_BLOCKED": "بسته مسدود است",
    "OWNER_DECISIONS_PENDING": "تصمیم‌های مالک در انتظار",
    "CATALOGUE_INCOMPLETE": "فهرست جدول‌ها ناقص",
    "REVIEW_BLOCKERS_OPEN": "موارد بررسی مسدودکننده باز",
    "AMOUNT_UNREADABLE": "مبلغ نامعتبر در گزارش",
}

REVIEW_ACTIONABILITY_FA: dict[str, str] = {
    "RESOLVABLE_BY_CODE": "قابل حل با کد",
    "ARCHIVE_ONLY": "فقط آرشیو",
    "OWNER_DECISION": "تصمیم مالک",
    "ADOPTION_TIME": "هنگام Adoption",
}


# Bulk ownership review (``ownership_review.py``, docs/OWNERSHIP_REVIEW.md). ADMIN_APPROVED_UNVERIFIED is never proof.
OWNERSHIP_FINAL_CLASS_FA: dict[str, str] = {
    "PROVEN": "مالکیت اثبات‌شده",
    "ADMIN_APPROVED_UNVERIFIED": "تأیید مدیر (اثبات نشده)",
    "PENDING": "در انتظار تصمیم",
    "REJECTED": "رد شده — قرنطینه",
    "QUARANTINED": "قرنطینه سیستمی",
}

OWNERSHIP_REVIEW_STATE_FA: dict[str, str] = {
    "PENDING": "در انتظار",
    "ADMIN_APPROVED_UNVERIFIED": "تأیید مدیر — اثبات نشده",
    "REJECTED": "ردشده توسط مدیر",
}

OWNERSHIP_ELIGIBILITY_FA: dict[str, str] = {
    "PROVEN": "اثبات‌شده با شواهد",
    "ATTESTABLE": "قابل تأیید مدیر",
    "ATTESTABLE_HIGH_RISK": "قابل تأیید مدیر — پرریسک (اقدام کاربر دیگر بدون ثبت انتقال)",
    "QUARANTINE_ONLY": "فقط قرنطینه",
}

OWNERSHIP_LIVE_STATUS_FA: dict[str, str] = {
    "NOT_CHECKED": "بررسی نشده",
    "LIVE_ACTIVE": "فعال روی پنل",
    "LIVE_DISABLED": "غیرفعال روی پنل",
    "LIVE_LIMITED": "محدودشده (حجم) روی پنل",
    "LIVE_EXPIRED": "منقضی روی پنل",
    "LIVE_ON_HOLD": "در انتظار شروع روی پنل",
    "NOT_FOUND_ON_PANEL": "روی پنل پیدا نشد",
    "CHECK_ERROR": "خطا در بررسی",
    "UNSUPPORTED_PANEL": "نوع پنل پشتیبانی نمی‌شود",
}

OWNERSHIP_RISK_FA: dict[str, str] = {
    "ADMIN_ATTESTATION_NOT_PROOF": "تأیید مدیر اثبات مالکیت نیست؛ این موارد جدا از موارد اثبات‌شده نگه داشته می‌شوند",
    "OWNER_MISMATCH_NO_TRANSFER": "کاربر دیگری بدون ثبت انتقال روی این سرویس‌ها اقدام کرده است؛ تأیید مالک فاکتور "
                                  "فقط با پذیرش جداگانه و صریح این ریسک ممکن است",
    "UNVIEWED_ITEMS": "مواردی که هرگز باز و بررسی نشده‌اند",
    "LIVE_NOT_CHECKED": "وضعیت روی پنل بررسی نشده است",
    "LIVE_NOT_FOUND": "سرویس روی پنل پیدا نشد",
    "LIVE_CHECK_ERROR": "بررسی وضعیت روی پنل با خطا مواجه شد",
    "STATUS_REMOVED_OR_EXPIRED": "فاکتور حذف‌شده یا منقضی است",
    "REJECT_PROVEN": "رد سرویسی که مالکیت آن اثبات شده است",
    "STALE_BINDING": "تصمیم قبلی مربوط به نسخه دیگری از رکورد است",
    "NOT_ELIGIBLE_EXCLUDED": "موارد غیرقابل اعمال کنار گذاشته می‌شوند",
}

OWNERSHIP_ACTION_FA: dict[str, str] = {
    "APPROVE": "تأیید مالک ثبت‌شده در فاکتور",
    "REJECT": "رد و انتقال به قرنطینه",
    "RESET": "بازگشت به در انتظار",
    "UNDO": "بازگرداندن دسته",
    "SEAL": "مهر و موم",
    "LIVE_CHECK": "بررسی فقط‌خواندنی پنل",
    "BATCH_APPROVE": "ثبت دسته تأیید",
    "BATCH_REJECT": "ثبت دسته رد",
    "BATCH_RESET": "ثبت دسته بازگشت به در انتظار",
    "BATCH_UNDO": "ثبت دسته بازگرداندن",
}

OWNERSHIP_BASIS_FA: dict[str, str] = {
    "EVIDENCE": "شواهد بکاپ",
    "ADMIN_ATTESTATION": "تأیید مدیر (اثبات نیست)",
    "NONE": "بدون تصمیم",
    "ADMIN_REJECTION": "رد توسط مدیر",
    "SYSTEM_QUARANTINE": "قرنطینه سیستمی",
}

# Short labels for modules/ownership.py reason codes (the long explanations live there).
OWNERSHIP_REASON_FA: dict[str, str] = {
    "OWNERSHIP_OWNER_ID_INVALID": "شناسه مالک فاکتور نامعتبر",
    "OWNERSHIP_OWNER_NOT_IN_USER_TABLE": "مالک فاکتور در جدول کاربران نیست",
    "OWNERSHIP_MULTIPLE_CLAIMANTS": "چند مدعی مالکیت",
    "OWNERSHIP_USERNAME_SHARED_BY_INVOICES": "نام کاربری مشترک بین چند فاکتور",
    "OWNERSHIP_TRANSFER_ORDER_UNPROVEN": "ترتیب انتقال‌ها اثبات‌نشده",
    "OWNERSHIP_TRANSFER_TARGET_INVALID": "طرف انتقال نامعتبر",
    "OWNERSHIP_TRANSFER_CHAIN_BROKEN": "زنجیره انتقال گسسته",
    "OWNERSHIP_TRANSFER_NOT_APPLIED_TO_INVOICE": "انتقال در فاکتور اعمال نشده",
    "OWNERSHIP_ACTION_OUTSIDE_HOLDING_WINDOW": "عملیات خارج از بازه مالکیت",
    "OWNERSHIP_ACTOR_NOT_IN_USER_TABLE": "کاربر عملیات در جدول کاربران نیست",
    "OWNERSHIP_OWNER_MISMATCH_NO_TRANSFER_RECORD": "عملیات کاربر دیگر بدون سابقه انتقال",
    "OWNERSHIP_NO_INVOICE_FOR_USERNAME": "فاکتوری برای این نام کاربری نیست",
    "OWNERSHIP_TRANSFER_CHAIN_PROVEN": "زنجیره انتقال اثبات‌شده",
    "OWNERSHIP_ADMIN_ACTOR_NOT_OWNER": "عملیات توسط مدیر (نه مالک)",
    "OWNERSHIP_PAYER_NOT_OWNER": "پرداخت‌کننده غیر از مالک",
    "NO_INVOICE": "بدون فاکتور",
    "OWNER_NOT_IMPORTABLE": "مالک فاکتور قابل انتقال نیست",
    "QUARANTINE_ONLY": "فقط قرنطینه",
    "ALREADY_SEALED": "مهر و موم شده",
    "ALREADY_PROVEN": "مالکیت از قبل اثبات شده است",
    "ALREADY_QUARANTINED": "از قبل در قرنطینه سیستمی است",
    "NO_VALID_INVOICE_OWNER": "مالک معتبری در فاکتور نیست",
}

# Invoice status classes (modules/adoption.py CLASSES).
STATUS_CLASS_FA: dict[str, str] = {
    "ACTIVE": "فعال",
    "TEST": "تست",
    "EXPIRED": "منقضی",
    "DISABLED": "غیرفعال",
    "REMOVED": "حذف‌شده",
    "NOT_PROVISIONED": "ساخته‌نشده روی پنل",
    "UNKNOWN": "نامشخص",
}


def readiness_label(table: dict[str, str], code: Any, fallback: Any = None) -> str:
    """Persian label for a readiness code, else the server-provided Persian label, else the code."""
    k = str(code if code is not None else "")
    return table.get(k) or (str(fallback) if fallback else "") or humanise(k) or "—"


def labels_payload() -> dict[str, Any]:
    """JSON served to the UI (``GET /api/labels``)."""
    return {
        "coverage_status": COVERAGE_STATUS_FA,
        "coverage_status_help": COVERAGE_STATUS_HELP_FA,
        "classes": dict(CLASS_FA),
        "readiness": READINESS_FA,
        "entities": ENTITY_FA,
        "keys": KEY_FA,
        "key_help": KEY_HELP_FA,
        "review_codes": REVIEW_CODE_FA,
        "review_kinds": REVIEW_KIND_FA,
        "blocker_codes": BLOCKER_CODE_FA,
        "blocker_categories": BLOCKER_CATEGORY_FA,
        "blocker_category": BLOCKER_CATEGORY,
        "entity_block_reason": ENTITY_BLOCK_REASON_FA,
        "mapping_status": MAPPING_STATUS_FA,
        "importer_warning": IMPORTER_WARNING_FA,
        "readiness_disclaimer": READINESS_DISCLAIMER_FA,
        "readiness_verdicts": READINESS_VERDICT_FA,
        "readiness_sections": READINESS_SECTION_FA,
        "readiness_keys": READINESS_KEY_FA,
        "ownership_decisions": OWNERSHIP_DECISION_FA,
        "history_causes": HISTORY_CAUSE_FA,
        "review_groups": REVIEW_GROUP_FA,
        "panel_status": PANEL_STATUS_FA,
        "package_readiness": PACKAGE_READINESS_FA,
        "money_units": MONEY_UNIT_FA,
        "confirmed_by": CONFIRMED_BY_FA,
        "import_blockers": IMPORT_BLOCKER_FA,
        "review_actionability": REVIEW_ACTIONABILITY_FA,
        "ownership_review": {
            "final_class": OWNERSHIP_FINAL_CLASS_FA,
            "review_state": OWNERSHIP_REVIEW_STATE_FA,
            "eligibility": OWNERSHIP_ELIGIBILITY_FA,
            "live_status": OWNERSHIP_LIVE_STATUS_FA,
            "risk": OWNERSHIP_RISK_FA,
            "action": OWNERSHIP_ACTION_FA,
            "reason": OWNERSHIP_REASON_FA,
            "status_class": STATUS_CLASS_FA,
            "basis": OWNERSHIP_BASIS_FA,
        },
    }


def humanise(key: Any) -> str:
    s = re.sub(r"[_.]+", " ", str(key)).strip()
    return s or str(key)


def key_label(key: Any) -> str:
    k = str(key)
    return KEY_FA.get(k) or ENTITY_FA.get(k) or humanise(k)


def entity_label(key: Any) -> str:
    k = str(key)
    return ENTITY_FA.get(k) or KEY_FA.get(k) or humanise(k)


# ---------------------------------------------------------------------------------- HTML
def _e(v: Any) -> str:
    return html.escape("" if v is None else str(v), quote=True)


_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def _num(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "بله" if v else "خیر"
    if isinstance(v, int):
        return f"{v:,}".replace(",", "٬").translate(_FA_DIGITS)
    if isinstance(v, str) and re.fullmatch(r"-?\d+", v):
        neg = v.startswith("-")
        body = f"{int(v.lstrip('-')):,}".replace(",", "٬").translate(_FA_DIGITS)
        return ("⁦−" + body + "⁩") if neg else body
    return str(v)


_CSS = """
*{box-sizing:border-box}body{font-family:Vazirmatn,"Segoe UI",Tahoma,sans-serif;margin:0;padding:24px;
background:#f4f6fa;color:#1a2133;line-height:1.7;font-size:14px}main{max-width:1200px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 8px;border-bottom:2px solid #e2e6ef;padding-bottom:4px}
h3{font-size:15px;margin:16px 0 6px}.muted{color:#5d6779}.small{font-size:12px}
.code{font-family:Consolas,"Courier New",monospace;font-size:11px;color:#5d6779;direction:ltr;unicode-bidi:isolate}
.ltr{direction:ltr;unicode-bidi:isolate}table{width:100%;border-collapse:collapse;background:#fff;margin:6px 0 12px}
th,td{border:1px solid #e2e6ef;padding:5px 8px;text-align:right;vertical-align:top}th{background:#f8f9fc;font-weight:600}
.box{border-radius:10px;padding:12px 16px;margin:12px 0;border:1px solid}.ok{background:#e3f6ec;border-color:#157a46;color:#0d5a33}
.bad{background:#fde8e8;border-color:#c0262d;color:#8e1a20}.warn{background:#fff4dd;border-color:#9a5b00;color:#6b3f00}
.badge{display:inline-block;border-radius:999px;padding:0 8px;font-size:12px;background:#eef1f6;border:1px solid #d5dae5}
.b-blocked{background:#fde8e8;color:#c0262d}.b-executed{background:#e3f6ec;color:#157a46}.b-archived{background:#e8eeff;color:#2f5bea}
.b-review{background:#fff4dd;color:#9a5b00}.cards{display:flex;flex-wrap:wrap;gap:8px}.card{background:#fff;border:1px solid #e2e6ef;
border-radius:10px;padding:8px 14px;min-width:150px}.card b{display:block;font-size:20px}
dl{display:grid;grid-template-columns:max-content 1fr;gap:4px 16px}dt{color:#5d6779}dd{margin:0;font-weight:600;word-break:break-all}
ul{margin:4px 0;padding-inline-start:20px}@media print{body{background:#fff;padding:0}}
"""


def _label_cell(key: Any) -> str:
    return f'{_e(key_label(key))} <span class="code">{_e(key)}</span>'


def _value_html(v: Any, depth: int = 0) -> str:
    if isinstance(v, dict):
        if depth >= 4:
            return f'<span class="code">{_e(str(v)[:300])}</span>'
        rows = "".join(f"<tr><th>{_label_cell(k)}</th><td>{_value_html(x, depth + 1)}</td></tr>"
                       for k, x in v.items())
        return f"<table>{rows}</table>" if rows else "—"
    if isinstance(v, list):
        if not v:
            return "—"
        return "<ul>" + "".join(f"<li>{_value_html(x, depth + 1)}</li>" for x in v[:200]) + "</ul>"
    return _e(_num(v))


def _coverage_rows(coverage: list[dict[str, Any]]) -> str:
    out = []
    for c in coverage:
        status = str(c.get("status") or "")
        qs = c.get("open_questions") or []
        out.append(
            "<tr>"
            f'<td class="ltr">{_e(c.get("table"))}</td>'
            f'<td>{_e(c.get("class_fa") or CLASS_FA.get(str(c.get("class")), "طبقه‌بندی‌نشده"))}</td>'
            f'<td><span class="badge b-{_e(status)}">{_e(COVERAGE_STATUS_FA.get(status, status))}</span></td>'
            f'<td>{_e(_num(c.get("rows")))}</td><td>{_e(_num(c.get("exported")))}</td>'
            f'<td>{_e(_num(c.get("archived")))}</td><td>{_e(_num(c.get("review")))}</td>'
            f'<td>{_e(_num(c.get("skipped")))}</td>'
            f'<td>{_e(_num(len(c.get("dropped_secret_columns") or [])))}</td>'
            f'<td class="ltr small">{_e(", ".join(map(str, c.get("unmapped_columns") or [])) or "—")}</td>'
            f'<td>{_e(_num(c.get("redacted_cells")))}</td>'
            f'<td>{_e(c.get("purpose_fa") or "—")}</td>'
            f'<td class="small ltr">{_e(c.get("reason") or "")}'
            + (f'<br>{_e(c.get("reason_blocked"))}' if c.get("reason_blocked") else "")
            + "</td>"
            f'<td class="small ltr">{"<br>".join(_e(q) for q in qs) or "—"}</td>'
            "</tr>")
    return "".join(out)


def render_report_html(report: dict[str, Any], *, manifest: dict[str, Any] | None = None) -> str:
    """Self-contained Persian RTL report. No scripts; every interpolated value is escaped."""
    r = report or {}
    readiness = str(r.get("readiness") or "")
    ready = readiness == "ready"
    conv = r.get("converter") or {}
    mu = r.get("money_unit") or {}
    sp = r.get("schema_profile") or {}
    pkg = r.get("package") or {}
    psv = (manifest or {}).get("package_schema_version")
    if not psv:
        from .nxpkg.manifest import PACKAGE_SCHEMA_VERSION

        psv = PACKAGE_SCHEMA_VERSION
    coverage = [c for c in (r.get("coverage") or []) if isinstance(c, dict)]
    parts: list[str] = []
    a = parts.append
    a('<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8">'
      '<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="referrer" content="no-referrer">'
      f"<title>گزارش مهاجرت میرزا به نکسا — {_e(str(r.get('import_id') or '')[:12])}</title>"
      f"<style>{_CSS}</style></head><body><main>")
    a("<h1>گزارش مهاجرت میرزا به نکسا</h1>")
    a('<p class="muted small">این گزارش فقط آمار کلی را دارد و هیچ اطلاعات شخصی، شناسه مشتری، رمز یا کلید در آن نیست.</p>')
    a(f'<div class="box bad" role="alert"><strong>هشدار:</strong> {_e(IMPORTER_WARNING_FA)}</div>')
    a(f'<div class="box {"ok" if ready else "bad"}"><strong>وضعیت آمادگی: '
      f'{_e(READINESS_FA.get(readiness, readiness or "—"))}</strong> <span class="code">{_e(readiness)}</span></div>')

    a("<h2>خلاصه</h2><dl>")
    for k, v, cls in (
        ("شناسه Import", r.get("import_id"), "code"),
        ("اثرانگشت منبع", r.get("source_fingerprint"), "code"),
        ("نسخه قرارداد بسته", psv, "code"),
        ("مبدل", f"{conv.get('name', '')} {conv.get('version', '')}", "code"),
        ("نسخه نگاشت", conv.get("mapping_version"), "code"),
        ("نسخه ساختار تشخیص‌داده‌شده", sp.get("label") or sp.get("id"), ""),
        ("واحد مبالغ", {"toman": "تومان", "rial": "ریال"}.get(str(mu.get("value")), "تعیین‌نشده — مبالغ مسدود هستند"), ""),
        ("تعداد موارد صف بررسی", _num(r.get("review_count")), ""),
        ("حجم بسته", _num(pkg.get("bytes")) + " بایت" if pkg.get("bytes") is not None else None, ""),
        ("زمان پردازش (ثانیه)", r.get("elapsed_seconds"), ""),
    ):
        if v is None:
            continue
        a(f"<dt>{_e(k)}</dt><dd class=\"{cls}\">{_e(v)}</dd>")
    a("</dl>")

    counts = r.get("counts") or {}
    if counts:
        a("<h3>تعداد رکوردها</h3><table><tr><th>بخش</th><th>مبدأ</th><th>تبدیل‌شده</th><th>بررسی</th><th>کنارگذاشته</th></tr>")
        for k, c in counts.items():
            c = c if isinstance(c, dict) else {"converted": c}
            a(f"<tr><td>{_e(entity_label(k))} <span class=\"code\">{_e(k)}</span></td>"
              f"<td>{_e(_num(c.get('source')))}</td><td>{_e(_num(c.get('converted')))}</td>"
              f"<td>{_e(_num(c.get('review')))}</td><td>{_e(_num(c.get('skipped')))}</td></tr>")
        a("</table>")

    # blockers
    blockers = [b for b in (r.get("blockers") or []) if isinstance(b, dict)]
    a(f"<h2>موارد مسدودکننده ({_e(_num(len(blockers)))})</h2>")
    if not blockers:
        a('<p class="muted">موردی وجود ندارد.</p>')
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for b in blockers:
        by_cat.setdefault(blocker_category(str(b.get("code"))), []).append(b)
    for cat in ("financial", "schema", "data_quality", "security"):
        items = by_cat.get(cat)
        if not items:
            continue
        a(f"<h3>{_e(BLOCKER_CATEGORY_FA[cat])}</h3><ul>")
        for b in items:
            code = str(b.get("code") or "")
            a(f"<li><strong>{_e(BLOCKER_CODE_FA.get(code, humanise(code)))}</strong> — {_e(b.get('message'))} "
              f"({_e(_num(b.get('count')))} مورد) <span class=\"code\">{_e(code)}</span></li>")
        a("</ul>")
    blocked_entities = [m for m in (r.get("mapping") or []) if isinstance(m, dict) and m.get("status") == "blocked"]
    if blocked_entities:
        a("<h3>بخش‌های مسدود و دلیل</h3><ul>")
        for m in blocked_entities:
            ent = str(m.get("entity") or "")
            a(f"<li><strong>{_e(entity_label(ent))}</strong>: "
              f"{_e(ENTITY_BLOCK_REASON_FA.get(ent) or m.get('note') or '—')} <span class=\"code\">{_e(ent)}</span></li>")
        a("</ul>")

    warnings = [w for w in (r.get("warnings") or []) if isinstance(w, dict)]
    if warnings:
        a(f"<h2>هشدارها ({_e(_num(len(warnings)))})</h2><ul>")
        for w in warnings:
            a(f"<li>{_e(w.get('message'))} ({_e(_num(w.get('count')))}) <span class=\"code\">{_e(w.get('code'))}</span></li>")
        a("</ul>")

    # coverage
    a(f"<h2>پوشش جدول‌ها ({_e(_num(len(coverage)))} جدول)</h2>")
    st_count: dict[str, int] = {}
    for c in coverage:
        st_count[str(c.get("status"))] = st_count.get(str(c.get("status")), 0) + 1
    a('<div class="cards">' + "".join(
        f'<div class="card">{_e(COVERAGE_STATUS_FA.get(s, s))}<b>{_e(_num(n))}</b></div>'
        for s, n in sorted(st_count.items())) + "</div>")
    uncovered = [c for c in coverage if c.get("status") == "blocked" or not c.get("class")]
    if uncovered:
        a('<div class="box bad"><strong>جدول‌های پوشش‌داده‌نشده</strong><ul>')
        for c in uncovered:
            a(f'<li><span class="ltr">{_e(c.get("table"))}</span> — {_e(_num(c.get("rows")))} ردیف — '
              f'<span class="ltr small">{_e(c.get("reason_blocked") or c.get("reason") or "")}</span></li>')
        a("</ul></div>")
    if coverage:
        a("<table><tr><th>جدول</th><th>طبقه</th><th>وضعیت</th><th>ردیف</th><th>خروجی</th><th>آرشیو</th>"
          "<th>بررسی</th><th>کنارگذاشته</th><th>ستون حساس حذف‌شده</th><th>ستون‌های نگاشت‌نشده</th>"
          "<th>خانه پوشانده</th><th>کاربرد</th><th>دلیل</th><th>پرسش‌های باز</th></tr>")
        a(_coverage_rows(coverage))
        a("</table>")

    # reconciliation
    recon = r.get("reconciliation") or {}
    a("<h2>تطبیق مالی و کنترل‌ها</h2>")
    if not recon:
        a('<p class="muted">اطلاعات تطبیق موجود نیست.</p>')
    for sec, val in recon.items():
        a(f"<h3>{_e(entity_label(sec))} <span class=\"code\">{_e(sec)}</span></h3>{_value_html(val)}")

    # mapping
    mapping = [m for m in (r.get("mapping") or []) if isinstance(m, dict)]
    if mapping:
        a("<h2>نگاشت به NEXA</h2><table><tr><th>بخش</th><th>جدول مبدأ</th><th>مقصد</th><th>وضعیت</th><th>توضیح</th></tr>")
        for m in mapping:
            st = str(m.get("status") or "")
            a(f"<tr><td>{_e(entity_label(m.get('entity')))}</td><td class=\"ltr\">{_e(m.get('source_table') or '—')}</td>"
              f"<td class=\"ltr small\">{_e(m.get('target') or '—')}</td>"
              f"<td>{_e(MAPPING_STATUS_FA.get(st, st))}</td><td class=\"ltr small\">{_e(m.get('note') or '')}</td></tr>")
        a("</table>")

    # open questions
    oq = [(c.get("table"), q) for c in coverage for q in (c.get("open_questions") or [])]
    a("<h2>پرسش‌های باز</h2>")
    if oq:
        a("<ul>" + "".join(f'<li><span class="ltr">{_e(t)}</span>: <span class="ltr">{_e(q)}</span></li>' for t, q in oq)
          + "</ul>")
    else:
        a('<p class="muted">پرسش بازی ثبت نشده است.</p>')

    a(f'<div class="box warn">{_e(IMPORTER_WARNING_FA)}</div>')
    a("</main></body></html>")
    return "".join(parts)


# ---------------------------------------------------------------------------------- CSV
CSV_COLUMNS = (
    ("table", "جدول"), ("class", "طبقه (کد)"), ("class_fa", "طبقه"), ("status", "وضعیت (کد)"),
    ("status_fa", "وضعیت"), ("rows", "ردیف"), ("exported", "خروجی"), ("archived", "آرشیو"),
    ("review", "بررسی"), ("skipped", "کنارگذاشته"), ("accounted_rows", "حساب‌شده"),
    ("dropped_secret_columns", "تعداد ستون حساس حذف‌شده"), ("unmapped_columns", "ستون‌های نگاشت‌نشده"),
    ("redacted_cells", "خانه پوشانده"), ("module", "ماژول"), ("nexa_target", "مقصد NEXA"),
    ("purpose_fa", "کاربرد"), ("reason", "دلیل"), ("open_questions", "پرسش‌های باز"),
)


def _csv_safe(v: Any) -> str:
    s = "" if v is None else str(v)
    # Neutralise spreadsheet formulas (a table name like "=HYPERLINK(...)" must stay text).
    if s and s[0] in "=+-@\t\r":
        s = "'" + s
    return s


def render_coverage_csv(report: dict[str, Any]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow([fa for _, fa in CSV_COLUMNS])
    for c in (report or {}).get("coverage") or []:
        if not isinstance(c, dict):
            continue
        row = []
        for key, _ in CSV_COLUMNS:
            if key == "status_fa":
                v: Any = COVERAGE_STATUS_FA.get(str(c.get("status")), c.get("status"))
            elif key == "dropped_secret_columns":
                v = len(c.get(key) or [])
            elif key in ("unmapped_columns", "open_questions"):
                v = " | ".join(map(str, c.get(key) or []))
            else:
                v = c.get(key)
            row.append(_csv_safe(v))
        w.writerow(row)
    return b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8")

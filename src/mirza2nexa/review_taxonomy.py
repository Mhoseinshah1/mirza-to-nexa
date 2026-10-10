"""Review-queue taxonomy: turn many review *items* into a few meaningful, actionable groups.

The review queue (``reports/review-queue.jsonl``) is a list of findings. Its length is not the
number of problems: one source record can carry several findings, and many findings are
history notes that need no action. This module classifies every finding code into

* a **group** (what it is about), and
* an **actionability** (who/what can close it):

  - ``RESOLVABLE_BY_CODE``  — the converter could close it with more evidence or a rule fix
  - ``ARCHIVE_ONLY``        — history is preserved as is; nothing to decide
  - ``OWNER_DECISION``      — needs a business decision by the owner
  - ``ADOPTION_TIME``       — can only be confirmed against the live panel during adoption

Nothing is removed from the queue; the taxonomy only *summarises* it. Unknown codes fall into
group ``uncategorized`` with ``OWNER_DECISION`` (conservative) and are listed explicitly.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

RESOLVABLE_BY_CODE = "RESOLVABLE_BY_CODE"
ARCHIVE_ONLY = "ARCHIVE_ONLY"
OWNER_DECISION = "OWNER_DECISION"
ADOPTION_TIME = "ADOPTION_TIME"
ACTIONABILITY = (RESOLVABLE_BY_CODE, ARCHIVE_ONLY, OWNER_DECISION, ADOPTION_TIME)

GROUPS_FA: dict[str, str] = {
    "ownership_transfer": "انتقال مالکیت سرویس",
    "invalid_owner": "مالک نامعتبر یا نامعلوم",
    "service_history": "تاریخچه عملیات سرویس",
    "service_status": "وضعیت سرویس نامعلوم",
    "panel_unknown": "پنل نامشخص",
    "financial_history": "اختلاف تاریخچه مالی",
    "payment_status": "پرداخت با وضعیت ناشناخته",
    "agents_pricing": "نمایندگی و قیمت‌گذاری",
    "discounts_gifts": "تخفیف، هدیه و ارجاع",
    "support_tickets": "پشتیبانی و تیکت",
    "incomplete_data": "داده ناقص یا تکراری",
    "settings_decision": "تنظیمات و جدول‌های نیازمند تصمیم",
    "uncategorized": "دسته‌بندی‌نشده",
}

# code -> (group, actionability). Explicit entries win over prefix rules.
CODES: dict[str, tuple[str, str]] = {
    # ownership
    "SERVICE_OWNER_CONFLICT": ("ownership_transfer", OWNER_DECISION),
    "AMBIGUOUS_OWNERSHIP": ("ownership_transfer", ADOPTION_TIME),
    # owners / identities
    "ORPHAN": ("invalid_owner", OWNER_DECISION),
    "CUSTOMER_NOT_IMPORTED": ("invalid_owner", RESOLVABLE_BY_CODE),
    "INVALID_IDENTITY": ("invalid_owner", ARCHIVE_ONLY),
    "DUPLICATE_SOURCE_ID": ("invalid_owner", OWNER_DECISION),
    "STATUS_UNKNOWN": ("invalid_owner", OWNER_DECISION),
    "INVOICE_WITHOUT_CUSTOMER": ("invalid_owner", ARCHIVE_ONLY),
    "SERVICE_OP_OWNER_NOT_IN_BACKUP": ("invalid_owner", ARCHIVE_ONLY),
    "AGENT_OWNER_UNKNOWN": ("agents_pricing", ARCHIVE_ONLY),
    "TICKET_OWNER_UNKNOWN": ("support_tickets", ARCHIVE_ONLY),
    # service history
    "SERVICE_OP_INVOICE_NOT_FOUND": ("service_history", ARCHIVE_ONLY),
    "SERVICE_OP_INVOICE_AMBIGUOUS": ("service_history", RESOLVABLE_BY_CODE),
    "SERVICE_OP_TYPE_UNKNOWN": ("service_history", RESOLVABLE_BY_CODE),
    "SERVICE_OP_AMOUNT_UNREADABLE": ("service_history", ARCHIVE_ONLY),
    "CANCELLED_SERVICE_STILL_LIVE": ("service_history", ADOPTION_TIME),
    # service status / shape
    "UNSUPPORTED_SHAPE": ("service_status", OWNER_DECISION),
    "INVALID_SOURCE_ROW": ("service_status", ARCHIVE_ONLY),
    "INVOICE_KEY_INVALID": ("service_status", ARCHIVE_ONLY),
    "INVALID_USERNAME": ("service_status", ADOPTION_TIME),
    # panels
    "NO_PANEL": ("panel_unknown", ADOPTION_TIME),
    "SERVICE_PANEL_CONFLICT": ("panel_unknown", ADOPTION_TIME),
    "PANEL_NOT_IN_REGISTRY": ("panel_unknown", OWNER_DECISION),
    "PANEL_WITHOUT_INVOICES": ("panel_unknown", ARCHIVE_ONLY),
    "PANEL_CODE_DUPLICATED": ("panel_unknown", OWNER_DECISION),
    # finance
    "BALANCE_UNREADABLE": ("financial_history", OWNER_DECISION),
    "BALANCE_OUT_OF_RANGE": ("financial_history", OWNER_DECISION),
    "BALANCE_CHANGED_SINCE_PREVIOUS": ("financial_history", OWNER_DECISION),
    "DUPLICATE_RECEIPT": ("financial_history", OWNER_DECISION),
    "PAYMENT_AMOUNT_UNREADABLE": ("financial_history", ARCHIVE_ONLY),
    "PAYMENT_CUSTOMER_UNKNOWN": ("financial_history", ARCHIVE_ONLY),
    "WALLET_HISTORY_DIFFERS_FROM_SNAPSHOT": ("financial_history", OWNER_DECISION),
    "WALLET_HISTORY_CHAIN_GAP": ("financial_history", ARCHIVE_ONLY),
    "WALLET_TX_ARITHMETIC_INCONSISTENT": ("financial_history", ARCHIVE_ONLY),
    "WALLET_TX_CUSTOMER_UNKNOWN": ("financial_history", ARCHIVE_ONLY),
    "WALLET_TX_DUPLICATE_SUSPECT": ("financial_history", OWNER_DECISION),
    "WALLET_TX_SOURCE_KEY_DUPLICATED": ("financial_history", OWNER_DECISION),
    "WALLET_TX_TYPE_UNKNOWN": ("financial_history", RESOLVABLE_BY_CODE),
    "WALLET_TX_TIME_FORMAT_UNKNOWN": ("financial_history", RESOLVABLE_BY_CODE),
    "PAYMENT_STATUS_UNKNOWN": ("payment_status", RESOLVABLE_BY_CODE),
    "PAYMENT_METHOD_UNKNOWN": ("payment_status", RESOLVABLE_BY_CODE),
    "PAYMENT_TIME_FORMAT_UNKNOWN": ("payment_status", RESOLVABLE_BY_CODE),
    "PAYMENT_ORDER_RELATION_UNPROVEN": ("payment_status", ARCHIVE_ONLY),
    "PAYMENT_ORDER_REF_DUPLICATED": ("payment_status", ARCHIVE_ONLY),
    # data quality
    "SOURCE_KEY_DUPLICATED": ("incomplete_data", OWNER_DECISION),
    "SOURCE_ROW_KEY_INVALID": ("incomplete_data", ARCHIVE_ONLY),
    "CODE_DUPLICATED": ("incomplete_data", OWNER_DECISION),
    "ARCHIVE_DUPLICATE_PK": ("incomplete_data", ARCHIVE_ONLY),
    "UNREPRESENTABLE_CELL": ("incomplete_data", ARCHIVE_ONLY),
    "TABLE_NOT_STAGED": ("incomplete_data", RESOLVABLE_BY_CODE),
    "TABLE_NO_STORABLE_COLUMNS": ("incomplete_data", RESOLVABLE_BY_CODE),
    "CATEGORY_LABEL_NOT_IN_TABLE": ("incomplete_data", OWNER_DECISION),
    "CATEGORY_REMARK_DUPLICATED": ("incomplete_data", ARCHIVE_ONLY),
    # settings / decisions
    "TABLE_NEEDS_DECISION": ("settings_decision", OWNER_DECISION),
    "CONFIG_NO_STORABLE_COLUMN": ("settings_decision", RESOLVABLE_BY_CODE),
    "SETTING_MULTIPLE_ROWS": ("settings_decision", ARCHIVE_ONLY),
    "CONFIG_UNVERIFIED_TABLE_EXCLUDED": ("settings_decision", OWNER_DECISION),
    # agents / engagement / support
    "AGENT_NOT_CUSTOMER": ("agents_pricing", ARCHIVE_ONLY),
    "AGENT_MULTIPLE_PRICE_LEVELS": ("agents_pricing", OWNER_DECISION),
    "AGENT_REQUEST_REQUESTER_UNKNOWN": ("agents_pricing", ARCHIVE_ONLY),
    "DISCOUNT_USAGE_TOTAL_MISMATCH": ("discounts_gifts", ARCHIVE_ONLY),
    "DISCOUNT_AMOUNT_UNREADABLE": ("discounts_gifts", ARCHIVE_ONLY),
    "DISCOUNT_USAGE_CUSTOMER_MISSING": ("discounts_gifts", ARCHIVE_ONLY),
    "DISCOUNT_USAGE_CODE_UNKNOWN": ("discounts_gifts", ARCHIVE_ONLY),
    "DISCOUNT_CODE_DUPLICATED": ("discounts_gifts", OWNER_DECISION),
    "REFERRAL_PARTY_MISSING": ("discounts_gifts", ARCHIVE_ONLY),
    "REFERRAL_SELF": ("discounts_gifts", ARCHIVE_ONLY),
    "WHEEL_CUSTOMER_MISSING": ("discounts_gifts", ARCHIVE_ONLY),
    "TICKET_MESSAGE_ORPHAN": ("support_tickets", ARCHIVE_ONLY),
    "SUPPORT_STATUS_UNKNOWN": ("support_tickets", RESOLVABLE_BY_CODE),
}

# Prefix fallbacks for codes added by domain modules (checked in order).
PREFIXES: tuple[tuple[str, str, str], ...] = (
    ("OWNERSHIP_", "ownership_transfer", OWNER_DECISION),
    ("ADOPTION_", "service_status", ADOPTION_TIME),
    ("PANEL_", "panel_unknown", OWNER_DECISION),
    ("PRODUCT_", "incomplete_data", OWNER_DECISION),
    ("SERVICE_OP_", "service_history", ARCHIVE_ONLY),
    ("WALLET_", "financial_history", OWNER_DECISION),
    ("PAYMENT_", "payment_status", OWNER_DECISION),
    ("AGENT_", "agents_pricing", OWNER_DECISION),
    ("DISCOUNT_", "discounts_gifts", OWNER_DECISION),
    ("GIFT_", "discounts_gifts", OWNER_DECISION),
    ("REFERRAL_", "discounts_gifts", ARCHIVE_ONLY),
    ("WHEEL_", "discounts_gifts", ARCHIVE_ONLY),
    ("TICKET_", "support_tickets", OWNER_DECISION),
    ("SUPPORT_", "support_tickets", OWNER_DECISION),
    ("CONFIG_", "settings_decision", OWNER_DECISION),
)


def classify(code: str) -> tuple[str, str, bool]:
    """(group, actionability, explicit?) for a review code."""
    if code in CODES:
        g, a = CODES[code]
        return g, a, True
    for prefix, g, a in PREFIXES:
        if code.startswith(prefix):
            return g, a, False
    return "uncategorized", OWNER_DECISION, False


def summarise(items: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate review items into groups. Only codes, counts and table names — no PII."""
    groups: dict[str, dict[str, Any]] = {}
    total = 0
    distinct_all: set[tuple] = set()
    unmapped: dict[str, int] = {}
    for it in items:
        total += 1
        code = str(it.get("code") or "UNKNOWN")
        group, act, explicit = classify(code)
        if not explicit:
            unmapped[code] = unmapped.get(code, 0) + 1
        ref = it.get("source_ref") or {}
        rec = (ref.get("table"), ref.get("pk")) if ref.get("pk") is not None else ("item", total)
        g = groups.setdefault(group, {"group": group, "label_fa": GROUPS_FA[group], "items": 0, "records": set(),
                                      "by_actionability": {a: 0 for a in ACTIONABILITY}, "codes": {},
                                      "severity": {"blocker": 0, "warning": 0}})
        g["items"] += 1
        g["records"].add(rec)
        distinct_all.add(rec)
        g["by_actionability"][act] += 1
        g["codes"][code] = g["codes"].get(code, 0) + 1
        sev = it.get("severity")
        if sev in g["severity"]:
            g["severity"][sev] += 1
    out_groups = []
    for name in GROUPS_FA:  # stable order
        if name not in groups:
            continue
        g = groups[name]
        recs = len(g["records"])
        out_groups.append({
            "group": name, "label_fa": g["label_fa"], "items": g["items"], "records": recs,
            "items_per_record": f"{g['items'] / recs:.2f}" if recs else "0",
            "resolvable_by_code": g["by_actionability"][RESOLVABLE_BY_CODE],
            "archive_only": g["by_actionability"][ARCHIVE_ONLY],
            "owner_decision": g["by_actionability"][OWNER_DECISION],
            "adoption_time": g["by_actionability"][ADOPTION_TIME],
            "severity": g["severity"],
            "codes": dict(sorted(g["codes"].items(), key=lambda kv: (-kv[1], kv[0]))),
        })
    totals = {a: sum(g["by_actionability"][a] for g in groups.values()) for a in ACTIONABILITY}
    return {
        "taxonomy_version": "1.0.0",
        "total_items": total,
        "distinct_records": len(distinct_all),
        "by_actionability": totals,
        "by_group": out_groups,
        "codes_without_explicit_rule": dict(sorted(unmapped.items())),
        "note": "Items are findings, not distinct problems; one record can carry several findings. "
                "Nothing was removed from the review queue.",
    }


def summarise_file(path: Path) -> dict[str, Any]:
    def items():
        if not path.exists():
            return
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    yield json.loads(line)

    return summarise(items())

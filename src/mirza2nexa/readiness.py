"""Migration readiness report: what an owner needs to decide whether the NEXA import can start.

Built only from aggregates already in ``report.json`` plus the review taxonomy, so it carries
no customer identifiers, usernames, codes, payment details or raw text. ``ready`` in the
package means *ready for importer review*; it is never an import permission — the verdict
here says so explicitly.
"""

from __future__ import annotations

from typing import Any

from . import __version__

READINESS_VERSION = "1.0.0"


def _sec(report: dict[str, Any], name: str) -> dict[str, Any]:
    v = (report.get("reconciliation") or {}).get(name)
    return v if isinstance(v, dict) else {}


def _int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def build(report: dict[str, Any], taxonomy: dict[str, Any], *, package_contract_version: str) -> dict[str, Any]:
    wallet = _sec(report, "wallet")
    diffs = _sec(report, "wallet_differences")
    wh = _sec(report, "wallet_history")
    payments = _sec(report, "payments")
    own = _sec(report, "service_ownership")
    pm = _sec(report, "panel_mapping")
    pr = _sec(report, "panel_registry")
    status_bd = _sec(report, "service_status_breakdown")
    agents = _sec(report, "agents_state") or _sec(report, "agents")
    discounts = _sec(report, "discounts_state")
    support = _sec(report, "support_state") or _sec(report, "support")
    coverage = report.get("coverage") or []
    money = report.get("money_unit") or {}

    by_status: dict[str, int] = {}
    by_class: dict[str, int] = {}
    for c in coverage:
        by_status[c.get("status") or "unknown"] = by_status.get(c.get("status") or "unknown", 0) + 1
        by_class[c.get("class") or "UNCLASSIFIED"] = by_class.get(c.get("class") or "UNCLASSIFIED", 0) + 1
    uncovered = sorted(c["table"] for c in coverage
                       if c.get("rows") and (c.get("status") == "blocked" or c.get("class") is None))

    by_cert = diffs.get("by_certainty") or {}
    hist_total = _int(diffs.get("total")) if diffs else _int((wh.get("customers") or {}).get("differs_from_snapshot"))
    financial_groups = {"financial_history", "payment_status"}
    fin_cases = sum(g["items"] for g in taxonomy.get("by_group", []) if g["group"] in financial_groups)

    ownership = {
        "total": _int(own.get("total")),
        "by_decision": own.get("by_decision") or {},
        "needs_manual_review": _int(own.get("needs_manual_review")),
        "confirmed_transfers": _int(own.get("confirmed_transfers")),
        "conflict_warnings_before": own.get("conflict_warnings_before"),
        "conflict_warnings_after": own.get("conflict_warnings_after"),
        "available": bool(own),
    }
    panels = {
        "registry_rows": _int(pm.get("registry_rows", pr.get("registry_rows", pr.get("panels")))),
        "codes_in_invoices": _int(pm.get("codes_in_invoices")),
        "mapped": _int(pm.get("mapped")),
        "pending_mapping": _int(pm.get("pending_mapping")),
        "status": "MAPPED" if pm and _int(pm.get("pending_mapping")) == 0 and _int(pm.get("mapped")) > 0
        else "PENDING_OWNER_MAPPING",
        "service_status": status_bd.get("by_class") or {},
    }

    # ---- owner decisions (dynamic: listed only when the data shows they matter) ----------
    decisions: list[dict[str, Any]] = []

    def decide(id_: str, title: str, detail: str, blocking: bool = True) -> None:
        decisions.append({"id": id_, "title_fa": title, "detail_fa": detail, "blocking": blocking})

    decide("OD-MONEY-UNIT", "تأیید واحد پول مبالغ میرزا",
           "واحد «تومان» انتخاب اپراتور است و هنوز با داده اثبات نشده؛ شواهد آماری در گزارش مالی آمده است.")
    decide("OD-EXISTING-NEXA-CUSTOMERS", "سیاست موجودی مشتریانی که از قبل در NEXA هستند",
           "Importer موجودی را خودکار ادغام نمی‌کند؛ باید مشخص شود مانده قدیمی چگونه با حساب موجود NEXA برخورد کند.")
    if panels["pending_mapping"] or panels["registry_rows"]:
        decide("OD-PANEL-MAPPING", "نگاشت پنل‌های میرزا به پنل‌های NEXA",
               "برای هر پنل قدیمی باید پنل متناظر در NEXA تعیین و اطلاعات ورود آن از مسیر امن NEXA وارد شود.")
    if ownership["needs_manual_review"]:
        decide("OD-OWNERSHIP", "تعیین مالک سرویس‌های با مالکیت مبهم",
               f"{ownership['needs_manual_review']} سرویس مالک قطعی ندارند و خودکار به کسی واگذار نمی‌شوند.")
    unexplained = _int(by_cert.get("UNEXPLAINED")) + _int(by_cert.get("PROBABLE"))
    if unexplained:
        decide("OD-WALLET-DIFFERENCES", "اختلاف تاریخچه کیف پول با موجودی",
               f"{unexplained} مورد اختلاف بدون اثبات کامل؛ موجودی بکاپ مبنای افتتاحیه می‌ماند مگر تصمیم دیگری گرفته شود.",
               blocking=False)
    if _int(agents.get("agents")):
        decide("OD-RESELLERS", "نقش نمایندگان در NEXA",
               f"{_int(agents.get('agents'))} نماینده فقط به‌عنوان مشتری عادی منتقل می‌شوند مگر نقش نمایندگی تأیید شود.")
    outstanding = discounts.get("outstanding_gift_value_minor")
    if (discounts.get("by_state") or {}).get("ACTIVE_UNUSED") or (outstanding not in (None, "0", 0)):
        decide("OD-UNREDEEMED-CODES", "کدهای تخفیف و هدیه استفاده‌نشده",
               "آیا اعتبار کدهای فعال در NEXA حفظ شود یا در زمان مهاجرت منقضی شوند؟", blocking=False)
    if _int(support.get("open_tickets")):
        decide("OD-OPEN-TICKETS", "تیکت‌های باز قدیمی",
               f"{_int(support.get('open_tickets'))} تیکت باز؛ هیچ پیام یا تیکت جدیدی خودکار ساخته نمی‌شود.",
               blocking=False)
    dup_receipts = _int((payments.get("duplicate_receipts") or {}).get("groups"))
    if dup_receipts:
        decide("OD-DUPLICATE-RECEIPTS", "رسیدهای تکراری در پرداخت‌های موفق",
               f"{dup_receipts} گروه رسید تکراری؛ معنای receipt_trace و شدت این هشدار باید تعیین شود.", blocking=False)
    review_tables = sorted(c["table"] for c in coverage if c.get("class") == "REVIEW" and c.get("rows"))
    if review_tables:
        decide("OD-REVIEW-TABLES", "جدول‌های بدون معنای قطعی",
               f"{len(review_tables)} جدول دارای داده فقط آرشیو شده‌اند و معنای آن‌ها باید تأیید شود.", blocking=False)

    blocking_decisions = sum(1 for d in decisions if d["blocking"])

    # ---- import blockers & verdict --------------------------------------------------------
    blockers = report.get("blockers") or []
    import_blockers = [{"code": "IMPORTER_NOT_BUILT",
                        "message_fa": "Importer سمت NEXA هنوز ساخته و تست نشده است؛ Import عملیاتی ممکن نیست."}]
    if report.get("readiness") != "ready":
        import_blockers.append({"code": "PACKAGE_BLOCKED",
                                "message_fa": "بسته دارای مسدودکننده است و باید ابتدا رفع شود."})
    if blocking_decisions:
        import_blockers.append({"code": "OWNER_DECISIONS_PENDING",
                                "message_fa": f"{blocking_decisions} تصمیم مسدودکننده مالک هنوز گرفته نشده است."})
    if not (report.get("catalogue") or {}).get("ok", True):
        import_blockers.append({"code": "CATALOGUE_INCOMPLETE", "message_fa": "کاتالوگ جدول‌ها ناقص است."})

    if report.get("readiness") != "ready":
        verdict = {"code": "BLOCKED", "label_fa": "مسدود — بسته باید اصلاح شود"}
    elif blocking_decisions:
        verdict = {"code": "NOT_READY_FOR_IMPORT",
                   "label_fa": "بسته برای بررسی Importer آماده است، اما برای Import عملیاتی آماده نیست"}
    else:
        verdict = {"code": "READY_FOR_IMPORTER_DRY_RUN",
                   "label_fa": "آماده اجرای آزمایشی (Dry Run) پس از ساخت Importer؛ Import عملیاتی نیاز به تأیید جداگانه دارد"}
    verdict["reasons"] = [{"code": b["code"], "message_fa": b["message_fa"]} for b in import_blockers]

    by_act = taxonomy.get("by_actionability") or {}
    return {
        "readiness_version": READINESS_VERSION,
        "converter_version": __version__,
        "package_contract_version": package_contract_version,
        "import_id": report.get("import_id"),
        "verdict": verdict,
        "package_health": {
            "readiness": report.get("readiness"),
            "blockers": [{"code": b.get("code"), "message_fa": b.get("message"), "count": b.get("count")}
                         for b in blockers],
            "catalogue_ok": bool((report.get("catalogue") or {}).get("ok", False)),
            "ready_means": "ready for importer review only — not an import permission",
        },
        "financial": {
            "balanced": bool(wallet.get("balanced")),
            "independent_recount_matches": bool(wallet.get("independent_recount_matches")),
            "source_recount_matches": bool((wallet.get("source_recount") or {}).get("matches")),
            "positive_balances": {**(wallet.get("opening_credits") or {}), "currency": wallet.get("currency")},
            "debts": {**(wallet.get("legacy_debts") or {}), "currency": wallet.get("currency")},
            "net_minor": str(_int((wallet.get("opening_credits") or {}).get("amount_minor"))
                             - _int((wallet.get("legacy_debts") or {}).get("amount_minor"))),
            "money_unit": {"value": money.get("value"), "confirmed_by": money.get("source"),
                           "proven_by_data": False},
            "history_differences": {"total": hist_total, "by_cause": diffs.get("by_cause") or {},
                                    "proven": _int(by_cert.get("PROVEN")), "probable": _int(by_cert.get("PROBABLE")),
                                    "unexplained": _int(by_cert.get("UNEXPLAINED"))},
            "payments_unknown_status": _int((payments.get("unknown_status") or {}).get("rows")),
            "cases_needing_review": fin_cases,
        },
        "coverage": {"tables_total": len(coverage), "non_empty": sum(1 for c in coverage if c.get("rows")),
                     "by_status": dict(sorted(by_status.items())), "by_class": dict(sorted(by_class.items())),
                     "uncovered": uncovered},
        "ownership": ownership,
        "panels": panels,
        "review": {
            "total_items": taxonomy.get("total_items", 0),
            "distinct_records": taxonomy.get("distinct_records", 0),
            "by_group": [{k: g[k] for k in ("group", "label_fa", "items", "records", "items_per_record",
                                            "resolvable_by_code", "archive_only", "owner_decision", "adoption_time")}
                         for g in taxonomy.get("by_group", [])],
            "auto_resolved_with_evidence": ownership["confirmed_transfers"] + _int(by_cert.get("PROVEN")),
            "resolvable_by_code": _int(by_act.get("RESOLVABLE_BY_CODE")),
            "archive_only": _int(by_act.get("ARCHIVE_ONLY")),
            "owner_decision_items": _int(by_act.get("OWNER_DECISION")),
            "adoption_stage": _int(by_act.get("ADOPTION_TIME")),
            "codes_without_explicit_rule": taxonomy.get("codes_without_explicit_rule") or {},
        },
        "owner_decisions": decisions,
        "owner_decisions_needed": len(decisions),
        "import_blockers": import_blockers,
    }

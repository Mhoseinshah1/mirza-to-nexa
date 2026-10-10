"""Engagement history: discount/gift codes, their usage, referrals, prize wheel, ad campaigns.

Nothing here is redeemable in NEXA and nothing credits a wallet. Records:

``records/engagement/discounts.jsonl``        ``legacy_discount_code`` (``Discount``, ``DiscountSell``,
                                              ``discount_sell``)
``records/engagement/discount_usage.jsonl``   ``legacy_discount_usage`` (``Giftcodeconsumed``,
                                              ``discount_sell_used``)
``records/engagement/referrals.jsonl``        ``legacy_referral`` (``reagent_report``)
``records/engagement/wheel_results.jsonl``    ``legacy_wheel_spin`` (``wheel_list``)
``records/engagement/ad_campaigns.jsonl``     ``legacy_ad_campaign`` (``ad_campaign``)
``records/engagement/program_settings.jsonl`` ``legacy_referral_program_settings`` (``affiliates``)

Every record: ``redeemable: false``, ``grants_credit: false``, ``affects_wallet: false``,
``applies_to_live_state: false``.

**Codes are bearer secrets.** A ``Discount.code`` credits ``price`` to whoever types it
(mirza_pro@b645db74 ``index.php:5773-5779`` ``addBalance``) and a ``DiscountSell.codeDiscount``
takes ``price``% off a purchase (``index.php:1698``). Until Mirza is switched off an unused code
is money to anybody who reads it, so ``Discount.code``, ``DiscountSell.codeDiscount`` and
``Giftcodeconsumed.code`` are SECRET (docs/evidence/mirza_tables.json) and **never staged**.
Consequence: a usage row cannot be joined to its code (``code_match:
UNAVAILABLE_CODE_SECRET``); code state comes from Mirza's own counters, and the only usage check
is aggregate (sum of use counters vs. Giftcodeconsumed rows, ``DISCOUNT_USAGE_TOTAL_MISMATCH``).
Fork tables (``discount_sell``/``discount_sell_used``, no public DDL) are staged by the generic
name filter; there a code-named column is removed from ``fields_raw`` and exported only as
``code_ref`` (``sha256("m2n:code:v1:" + source fingerprint + ":" + code)``, 24 hex). Reports and
review data never carry a code or a ``code_ref``.

Evidence:

* ``Discount`` — botmirzapanel@92c0ed06 ``table.php:426-429`` (id, code, price); mirza_pro
  ``db/tables/Discount.php:5-9`` (+ limituse, limitused); created ``admin.php:2330`` (limitused '0');
  redeemed ``index.php:5763-5785`` (one use per user via Giftcodeconsumed; limitused + 1).
* ``DiscountSell`` — ``table.php:547-553``; ``db/tables/DiscountSell.php:5-16``; created
  ``admin.php:3907-3920``; applied ``index.php:1646-1700`` (agent = tier or ``allusers``, ``time`` =
  expiry unix seconds with 0 = none, ``usefirst`` '1' = first purchase only, ``useuser`` per-user
  limit, ``type`` ``all``/``extend``); usage ``index.php:1826-1831`` writes Giftcodeconsumed too.
* ``Giftcodeconsumed`` — ``table.php:444-447``; ``db/tables/Giftcodeconsumed.php:5-7``.
* ``affiliates`` — a one-row *settings* table, not referral relations: ``table.php:573-585``;
  ``db/tables/affiliates.php:5-25``; ``price_Discount``/2 is the referrer gift (``index.php:5864``).
* ``reagent_report`` — ``db/tables/reagent_report.php:6-10``; written ``index.php:235-238``
  (user_id = referee, reagent = referrer, time ``Y/m/d H:i:s``, get_gift false) and
  ``index.php:5859`` (get_gift = 1 when the referrer gift was paid).
* ``wheel_list`` — ``db/tables/wheel_list.php:6-11``; written ``index.php:6269-6275`` (wheel_code =
  Telegram dice value, price = credited amount or 0 when lost).
* ``discount_sell``, ``discount_sell_used``, ``ad_campaign`` — deployed-fork tables, no public DDL.
"""

from __future__ import annotations

from typing import Any

from ..catalogue import (
    ARCHIVE,
    CATALOGUE,
    CONFIGURATION,
    FINANCIAL,
    PERSONAL,
    PUBLIC,
    REVIEW,
    SECRET,
    ColumnSpec,
    TableSpec,
)
from ..mapping import rules
from ..synth import SynthState, register_synth, sql_str
from .agents import (
    BOTMIRZA,
    MIRZA_PRO,
    NO_DDL,
    OWNER_COLUMNS,
    RowKeys,
    account_unstaged,
    code_ref,
    customer_ref,
    parse_time,
    pick_column,
    raw_fields,
    schema_unproven,
    status_for,
)
from .base import Module, ModuleContext, register_module

CODE_COLUMNS = ("code", "codeDiscount", "discount_code", "code_discount", "coupon", "gift_code")
FLAGS_OFF = {"redeemable": False, "grants_credit": False, "affects_wallet": False, "applies_to_live_state": False}
_LIABILITY_Q = ("OQ-M2N-EN-02: unredeemed codes are an outstanding bearer liability in Mirza; does the owner "
                "honour any of them in NEXA (as new NEXA codes), or let them lapse at cutover?")
_CODE_NOTE = "redeemable bearer code (docs/evidence/mirza_tables.json: secret): never staged"

CATALOGUE.register(TableSpec(
    name="Discount", klass=REVIEW, module="engagement", purpose="Gift codes that credit the wallet",
    purpose_fa="کدهای هدیه (افزایش موجودی)",
    reason="History only; redeeming credited Mirza wallets, never re-applied in NEXA.",
    nexa_target="none — records/engagement/discounts.jsonl (redeemable=false)",
    evidence=(f"{BOTMIRZA} table.php:426-429", f"{MIRZA_PRO} db/tables/Discount.php:5-9",
              f"{MIRZA_PRO} admin.php:2330", f"{MIRZA_PRO} index.php:5763-5785"),
    primary_key=("id",),
    columns=(ColumnSpec("id"), ColumnSpec("code", SECRET, _CODE_NOTE),
             ColumnSpec("price", FINANCIAL, "amount credited per redemption (addBalance, index.php:5776)"),
             ColumnSpec("limituse", PUBLIC, "max redemptions"), ColumnSpec("limitused", PUBLIC, "redemptions so far")),
    open_questions=(_LIABILITY_Q,),
    sensitivity=SECRET,
))
CATALOGUE.register(TableSpec(
    name="DiscountSell", klass=REVIEW, module="engagement", purpose="Percentage purchase discount codes",
    purpose_fa="کدهای تخفیف خرید (درصدی)",
    reason="History only; NEXA discounts are configured anew (promotions.ts), never imported.",
    nexa_target="none — records/engagement/discounts.jsonl (redeemable=false)",
    evidence=(f"{BOTMIRZA} table.php:547-562", f"{MIRZA_PRO} db/tables/DiscountSell.php:5-16",
              f"{MIRZA_PRO} admin.php:3907-3920", f"{MIRZA_PRO} index.php:1646-1700"),
    primary_key=("id",),
    columns=(ColumnSpec("id"), ColumnSpec("codeDiscount", SECRET, _CODE_NOTE),
             ColumnSpec("price", PUBLIC, "percent off (index.php:1698)"), ColumnSpec("limitDiscount"),
             ColumnSpec("usedDiscount"), ColumnSpec("agent", PUBLIC, "tier token or 'allusers' (index.php:1646)"),
             ColumnSpec("usefirst"), ColumnSpec("useuser"), ColumnSpec("code_product"), ColumnSpec("code_panel"),
             ColumnSpec("time", PUBLIC, "expiry unix seconds, 0 = none (index.php:1657)"),
             ColumnSpec("type", PUBLIC, "all | extend | … (index.php:1646)")),
    open_questions=(_LIABILITY_Q,),
    sensitivity=SECRET,
))
CATALOGUE.register(TableSpec(
    name="Giftcodeconsumed", klass=ARCHIVE, module="engagement", purpose="Who used which gift/discount code",
    purpose_fa="سابقه استفاده از کدها",
    reason="Usage history (customer per use); the code itself is SECRET, never staged, so rows are not joined to codes.",
    nexa_target="none — records/engagement/discount_usage.jsonl",
    evidence=(f"{BOTMIRZA} table.php:444-447", f"{MIRZA_PRO} db/tables/Giftcodeconsumed.php:5-7",
              f"{MIRZA_PRO} index.php:1830,5783"),
    primary_key=("id",),
    columns=(ColumnSpec("id"), ColumnSpec("code", SECRET, _CODE_NOTE), ColumnSpec("id_user", PERSONAL)),
    sensitivity=SECRET,
))
CATALOGUE.register(TableSpec(
    name="affiliates", klass=CONFIGURATION, module="engagement", purpose="Referral programme settings (one row)",
    purpose_fa="تنظیمات برنامه زیرمجموعه‌گیری",
    reason="Settings, not relations (seeded single row); kept for reference, never applied.",
    nexa_target="none — NEXA referral is configured anew (wp9-referral-audit.md)",
    evidence=(f"{BOTMIRZA} table.php:573-585", f"{MIRZA_PRO} db/tables/affiliates.php:5-25",
              f"{MIRZA_PRO} index.php:5864"),
    columns=(ColumnSpec("description", PUBLIC, "admin-authored promo text (redacted)"),
             ColumnSpec("status_commission"), ColumnSpec("Discount"),
             ColumnSpec("price_Discount", FINANCIAL, "start gift; referrer receives half (index.php:5864)"),
             ColumnSpec("porsant_one_buy"), ColumnSpec("id_media", PUBLIC, "Telegram file id"),
             ColumnSpec("affiliatesstatus"), ColumnSpec("affiliatespercentage")),
    sensitivity=FINANCIAL,
))
CATALOGUE.register(TableSpec(
    name="reagent_report", klass=ARCHIVE, module="engagement", purpose="Referral relations (referee → referrer)",
    purpose_fa="روابط معرف و زیرمجموعه",
    reason="Relation history; NEXA referral attribution is not imported (no reward is re-paid).",
    nexa_target="none — records/engagement/referrals.jsonl",
    evidence=(f"{MIRZA_PRO} db/tables/reagent_report.php:6-10", f"{MIRZA_PRO} index.php:235-238,5854-5865"),
    primary_key=("id",),
    columns=(ColumnSpec("id"), ColumnSpec("user_id", PERSONAL, "referee"),
             ColumnSpec("get_gift", PUBLIC, "referrer gift paid (index.php:5859)"),
             ColumnSpec("time", PUBLIC, "date('Y/m/d H:i:s'), server zone"),
             ColumnSpec("reagent", PERSONAL, "referrer")),
    sensitivity=PERSONAL,
))
CATALOGUE.register(TableSpec(
    name="wheel_list", klass=ARCHIVE, module="engagement", purpose="Prize wheel (dice) spins and prizes",
    purpose_fa="نتایج گردونه شانس",
    reason="History only; prizes were credited in Mirza and are part of the wallet balance already.",
    nexa_target="none — records/engagement/wheel_results.jsonl",
    evidence=(f"{MIRZA_PRO} db/tables/wheel_list.php:6-11", f"{MIRZA_PRO} index.php:6240-6275"),
    primary_key=("id",),
    columns=(ColumnSpec("id"), ColumnSpec("id_user", PERSONAL), ColumnSpec("time"),
             ColumnSpec("first_name", PERSONAL, "not exported (data minimisation)"),
             ColumnSpec("wheel_code", PUBLIC, "Telegram dice value"),
             ColumnSpec("price", FINANCIAL, "credited amount, 0 = lost (index.php:6254,6266)")),
    sensitivity=FINANCIAL,
))
for _name, _purpose, _fa in (
    ("discount_sell", "Deployed-fork discount codes (meaning unproven)", "کدهای تخفیف (فورک)"),
    ("discount_sell_used", "Deployed-fork discount usage (meaning unproven)", "استفاده از کد تخفیف (فورک)"),
    ("ad_campaign", "Deployed-fork advertising campaigns (meaning unproven)", "کمپین‌های تبلیغاتی (فورک)"),
):
    CATALOGUE.register(TableSpec(
        name=_name, klass=REVIEW, module="engagement", purpose=_purpose, purpose_fa=_fa,
        reason="Fork table without public DDL; archived raw (redacted, codes as code_ref) for an owner decision.",
        nexa_target="none — engagement archive", evidence=(NO_DDL,), columns=None,
        open_questions=("OQ-M2N-EN-01: column meaning/units unproven; is it a rename of DiscountSell/"
                        "Giftcodeconsumed or a separate feature?",),
        sensitivity=FINANCIAL,
    ))

ENGAGEMENT_TABLES = ["Discount", "DiscountSell", "discount_sell", "discount_sell_used", "Giftcodeconsumed",
                     "affiliates", "reagent_report", "wheel_list", "ad_campaign"]


def _int(raw: str | None) -> int | None:
    if raw is None:
        return None
    t = rules.js_trim(raw)
    return int(t) if t.isdigit() and len(t) <= 12 else None


def _rec(record_type: str, schema: str, key: str, table: str, pk: str | None) -> dict[str, Any]:
    return {"record_type": record_type, "schema": schema, "idempotency_key": key, "source_table": table,
            "source_pk": pk}


def _state(used: int | None, limit: int | None) -> str:
    """Code state from Mirza's own counters (the code text is secret, so usage rows cannot be joined)."""
    if used is None:
        return "UNKNOWN"
    if used == 0:
        return "UNUSED"
    return "EXHAUSTED" if limit is not None and 0 < limit <= used else "PARTIALLY_USED"


def _usage_index(ctx: ModuleContext) -> dict[str, int]:
    """Fork code → number of ``discount_sell_used`` rows (exact string; never exported).

    ``Giftcodeconsumed.code`` is SECRET and never staged, so documented codes cannot be indexed.
    """
    return _codes(ctx, "discount_sell_used", "code")


def _codes(ctx: ModuleContext, spec_name: str, col: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for stats in ctx.tables_for(spec_name):
        c = col if col in stats.stored_columns else pick_column(stats, CODE_COLUMNS)
        if c is None:
            continue
        for _rowno, row in ctx.rows(stats):
            v = row.get(c)
            if v is not None:
                out[v] = out.get(v, 0) + 1
    return out


def _discounts(ctx: ModuleContext, usage: dict[str, int], rec_out: dict[str, Any]) -> None:
    tables = ctx.tables_for("Discount") + ctx.tables_for("DiscountSell") + ctx.tables_for("discount_sell")
    if not tables:
        return
    sink = ctx.open("records/engagement/discounts.jsonl")
    for stats in tables:
        spec = ctx.spec(stats.name)
        kind = {"Discount": "GIFT_BALANCE_CODE", "DiscountSell": "PURCHASE_PERCENT_CODE"}.get(stats.spec_name or "",
                                                                                             "UNKNOWN")
        documented = stats.spec_name in {"Discount", "DiscountSell"}
        # documented code columns are SECRET (never staged); a fork table's code column is staged by the
        # generic name filter, so it is exported only as a salted digest
        code_col = None if documented else pick_column(stats, CODE_COLUMNS)
        seen_codes: dict[str, int] = {}
        keys = RowKeys(ctx, stats, "engagement")
        n = 0
        for _rowno, row in ctx.rows(stats):
            ctx.tick()
            key, pk, checksum = keys(row)
            code = row.get(code_col) if code_col else None
            consumed = usage.get(code, 0) if code is not None else None
            rec = _rec("legacy_discount_code", "m2n.engagement.discount.v1", key, stats.name, pk)
            rec.update({"kind": kind, "code_ref": None if documented else code_ref(ctx, code),
                        "code_staged": not documented, "code_plaintext_exported": False,
                        "consumed_rows": consumed,
                        "usage_link": "UNAVAILABLE_CODE_SECRET" if documented else "EXACT_STRING_DIGEST"})
            state = "UNKNOWN"
            if stats.spec_name == "Discount":
                amount, note = rules.parse_price(row.get("price"))
                limit, used = _int(row.get("limituse")), _int(row.get("limitused"))
                rec.update({"credit_amount": ctx.money(amount), "credit_amount_raw": row.get("price"),
                            "credit_amount_parse": note, "limit_total": limit, "limit_total_raw": row.get("limituse"),
                            "used_count": used, "used_count_raw": row.get("limitused")})
                state = _state(used, limit)
                rec_out["used_counter_sum"] += used or 0
                if note != "OK":
                    ctx.review(kind="engagement", code="DISCOUNT_AMOUNT_UNREADABLE", entity="engagement",
                               table=stats.name, pk=pk, severity="warning", data={"parse": note},
                               message="مبلغ کد هدیه قابل خواندن نیست؛ مقدار خام حفظ شد.")
            elif stats.spec_name == "DiscountSell":
                pct = _int(row.get("price"))
                limit, used = _int(row.get("limitDiscount")), _int(row.get("usedDiscount"))
                exp_raw = row.get("time")
                exp_iso, exp_note = (None, "NO_EXPIRY") if rules.js_trim(exp_raw or "") in {"", "0"} \
                    else parse_time(exp_raw)
                t = rules.js_trim(row.get("type") or "")
                rec.update({
                    "percent": pct if pct is not None and 0 <= pct <= 100 else None, "percent_raw": row.get("price"),
                    "limit_total": limit, "limit_total_raw": row.get("limitDiscount"),
                    "used_count": used, "used_count_raw": row.get("usedDiscount"),
                    "scope": {"agent_tier_raw": row.get("agent"), "code_product_raw": row.get("code_product"),
                              "code_panel_raw": row.get("code_panel"),
                              "type": t if t in {"all", "extend"} else "UNKNOWN", "type_raw": row.get("type"),
                              "first_purchase_only_raw": row.get("usefirst"),
                              "per_user_limit_raw": row.get("useuser")},
                    "expires_at": exp_iso, "expires_at_raw": exp_raw, "expires_at_note": exp_note,
                })
                state = _state(used, limit)
                rec_out["used_counter_sum"] += used or 0
            else:
                rec.update({"code_column": code_col,
                            "code_relation_basis": "COLUMN_NAME_CONVENTION_UNPROVEN" if code_col else "UNKNOWN",
                            "fields_raw": raw_fields(ctx, stats.name, row, drop=(code_col,) if code_col else ())})
            rec["state"] = state
            rec.update(FLAGS_OFF)
            rec["provenance"] = ctx.provenance(stats.name, pk, checksum)
            sink.write(rec)
            n += 1
            rec_out["codes_by_kind"][kind] = rec_out["codes_by_kind"].get(kind, 0) + 1
            rec_out["codes_by_state"][state] = rec_out["codes_by_state"].get(state, 0) + 1
            if code is not None:
                seen_codes[code] = seen_codes.get(code, 0) + 1
        dup = sum(1 for c in seen_codes.values() if c > 1)
        if dup:
            rec_out["duplicate_codes"] += dup
            ctx.review(kind="engagement", code="DISCOUNT_CODE_DUPLICATED", entity="engagement", table=stats.name,
                       pk=None, severity="warning", data={"codes": dup},
                       message="یک کد چند بار در جدول تعریف شده است؛ همه ردیف‌ها جداگانه آرشیو شدند.")
        keys.finish("engagement")
        ctx.coverage(stats.name, archived=n, status=status_for(spec))
        ctx.count("engagement", "converted", n)
        if spec is not None and spec.klass == REVIEW:
            schema_unproven(ctx, stats, "engagement", spec)


def _usage(ctx: ModuleContext, fork: dict[str, int], rec_out: dict[str, Any]) -> None:
    tables = ctx.tables_for("Giftcodeconsumed") + ctx.tables_for("discount_sell_used")
    if not tables:
        return
    sink = ctx.open("records/engagement/discount_usage.jsonl")
    for stats in tables:
        spec = ctx.spec(stats.name)
        documented = stats.spec_name == "Giftcodeconsumed"
        code_col = None if documented else pick_column(stats, CODE_COLUMNS)  # Giftcodeconsumed.code is SECRET
        user_col = "id_user" if documented else pick_column(stats, OWNER_COLUMNS)
        keys = RowKeys(ctx, stats, "engagement")
        n = 0
        for _rowno, row in ctx.rows(stats):
            ctx.tick()
            key, pk, checksum = keys(row)
            code = row.get(code_col) if code_col else None
            hits = [name for name, idx in (("discount_sell", fork),) if code is not None and code in idx]
            if documented:
                match = "UNAVAILABLE_CODE_SECRET"
            else:
                match = "NONE" if not hits else hits[0] if len(hits) == 1 else "AMBIGUOUS"
            cust = customer_ref(ctx, row.get(user_col)) if user_col else None
            rec = _rec("legacy_discount_usage", "m2n.engagement.discount_usage.v1", key, stats.name, pk)
            rec.update({"code_ref": None if documented else code_ref(ctx, code), "code_matches": hits,
                        "code_match": match, "match_basis": None if documented else "EXACT_STRING_DIGEST",
                        "customer": cust,
                        "relation_basis": "PROVEN (index.php:5783)" if documented else "COLUMN_NAME_CONVENTION_UNPROVEN"})
            if not documented:
                rec["fields_raw"] = raw_fields(ctx, stats.name, row, drop=(code_col,) if code_col else ())
            rec.update(FLAGS_OFF)
            rec["provenance"] = ctx.provenance(stats.name, pk, checksum)
            sink.write(rec)
            n += 1
            rec_out["usage_by_match"][match] = rec_out["usage_by_match"].get(match, 0) + 1
            if match == "NONE":
                ctx.review(kind="engagement", code="DISCOUNT_USAGE_CODE_UNKNOWN", entity="engagement",
                           table=stats.name, pk=pk, severity="warning", data={"code_match": match},
                           message="کد استفاده‌شده در جدول کدها پیدا نشد (احتمالاً حذف شده)؛ سابقه حفظ شد.")
            if cust is None or not cust["valid"]:
                rec_out["usage_customer_missing"] += 1
                ctx.review(kind="engagement", code="DISCOUNT_USAGE_CUSTOMER_MISSING", entity="engagement",
                           table=stats.name, pk=pk, severity="warning",
                           data={"decision": cust["decision"] if cust else "NO_OWNER_COLUMN"},
                           message="مصرف‌کننده کد مشتری معتبری نیست؛ سابقه فقط آرشیو شد.")
        keys.finish("engagement")
        ctx.coverage(stats.name, archived=n, status=status_for(spec))
        ctx.count("engagement", "converted", n)
        if not documented:
            schema_unproven(ctx, stats, "engagement", spec)
        rec_out["usage_rows"] += n


def _usage_totals(ctx: ModuleContext, out: dict[str, Any]) -> None:
    """Aggregate check without code text: Mirza's use counters vs. the Giftcodeconsumed log.

    Both Discount (``index.php:5779,5783``) and DiscountSell (``index.php:1826-1830``) bump a counter and
    log one Giftcodeconsumed row per use, so the totals should agree. Admins delete log rows for
    DiscountSell codes (``admin.php:3932``) and Discount rows can be deleted (``admin.php:2341``), so a
    difference is a warning for review, never a ledger blocker.
    """
    log_rows = sum(t.rows for t in ctx.all_tables() if t.spec_name == "Giftcodeconsumed")
    out["usage_log_rows"] = log_rows
    out["usage_totals_match"] = log_rows == out["used_counter_sum"]
    if not out["usage_totals_match"] and (log_rows or out["used_counter_sum"]):
        ctx.review(kind="engagement", code="DISCOUNT_USAGE_TOTAL_MISMATCH", entity="engagement",
                   table="Giftcodeconsumed", pk=None, severity="warning",
                   data={"used_counter_sum": out["used_counter_sum"], "consumed_rows": log_rows},
                   message="جمع شمارنده‌های استفاده کدها با تعداد سوابق مصرف برابر نیست؛ فقط برای بررسی ثبت شد.")


def _referrals(ctx: ModuleContext, rec_out: dict[str, Any]) -> None:
    tables = ctx.tables_for("reagent_report")
    if not tables:
        return
    sink = ctx.open("records/engagement/referrals.jsonl")
    r = rec_out["referrals"]
    for stats in tables:
        keys = RowKeys(ctx, stats, "engagement")
        n = 0
        for _rowno, row in ctx.rows(stats):
            ctx.tick()
            key, pk, checksum = keys(row)
            referee = customer_ref(ctx, row.get("user_id"))
            referrer = customer_ref(ctx, row.get("reagent"))
            gift = rules.flag01(row.get("get_gift"))
            iso, note = parse_time(row.get("time"))
            missing = [k for k, v in (("referrer", referrer), ("referee", referee)) if not v["valid"]]
            self_ref = referee["raw_id"] is not None and referee["raw_id"] == referrer["raw_id"]
            rec = _rec("legacy_referral", "m2n.engagement.referral.v1", key, stats.name, pk)
            rec.update({"referrer": referrer, "referee": referee, "referrer_gift_paid": gift,
                        "referrer_gift_paid_raw": row.get("get_gift"), "attributed_at": iso,
                        "attributed_at_raw": row.get("time"), "attributed_at_note": note,
                        "relation_valid": not missing and not self_ref, "pays_commission": False, **FLAGS_OFF,
                        "provenance": ctx.provenance(stats.name, pk, checksum)})
            sink.write(rec)
            n += 1
            r["total"] += 1
            r["gift_paid"] += 1 if gift else 0
            if missing:
                r["party_missing"] += 1
                ctx.review(kind="engagement", code="REFERRAL_PARTY_MISSING", entity="engagement", table=stats.name,
                           pk=pk, severity="warning", data={"missing": missing},
                           message="معرف یا زیرمجموعه در بکاپ به‌عنوان مشتری معتبر وجود ندارد؛ رابطه فقط آرشیو شد.")
            elif self_ref:
                r["self_referral"] += 1
                ctx.review(kind="engagement", code="REFERRAL_SELF", entity="engagement", table=stats.name, pk=pk,
                           severity="warning", data={},
                           message="کاربر معرف خودش ثبت شده است؛ رابطه فقط آرشیو شد.")
            else:
                r["both_customers"] += 1
        keys.finish("engagement")
        ctx.coverage(stats.name, archived=n, status="archived")
        ctx.count("engagement", "converted", n)


def _wheel(ctx: ModuleContext, rec_out: dict[str, Any]) -> None:
    tables = ctx.tables_for("wheel_list")
    if not tables:
        return
    sink = ctx.open("records/engagement/wheel_results.jsonl")
    w = rec_out["wheel"]
    total = 0
    for stats in tables:
        keys = RowKeys(ctx, stats, "engagement")
        n = 0
        for _rowno, row in ctx.rows(stats):
            ctx.tick()
            key, pk, checksum = keys(row)
            amount, note = rules.parse_price(row.get("price"))
            iso, tnote = parse_time(row.get("time"))
            cust = customer_ref(ctx, row.get("id_user"))
            rec = _rec("legacy_wheel_spin", "m2n.engagement.wheel_spin.v1", key, stats.name, pk)
            rec.update({"customer": cust, "dice_value_raw": row.get("wheel_code"),
                        "won": None if amount is None else amount > 0, "prize": ctx.money(amount),
                        "prize_raw": row.get("price"), "prize_parse": note, "spun_at": iso,
                        "spun_at_raw": row.get("time"), "spun_at_note": tnote,
                        "prize_already_in_legacy_balance": True, **FLAGS_OFF,
                        "provenance": ctx.provenance(stats.name, pk, checksum)})
            sink.write(rec)
            n += 1
            w["spins"] += 1
            if amount is None:
                w["prize_unreadable"] += 1
            elif amount > 0:
                w["wins"] += 1
                total += amount
            if not cust["valid"]:
                w["customer_missing"] += 1
                ctx.review(kind="engagement", code="WHEEL_CUSTOMER_MISSING", entity="engagement", table=stats.name,
                           pk=pk, severity="warning", data={"decision": cust["decision"]},
                           message="چرخاننده گردونه مشتری معتبری نیست؛ فقط آرشیو شد.")
        keys.finish("engagement")
        ctx.coverage(stats.name, archived=n, status="archived")
        ctx.count("engagement", "converted", n)
    w["prizes_total"] = {"amount_minor": str(total), "currency": ctx.currency, "credited_by_package": "0"}


def _raw_table(ctx: ModuleContext, spec_name: str, relpath: str, record_type: str, schema: str,
               extra_flags: dict[str, Any]) -> int:
    tables = ctx.tables_for(spec_name)
    if not tables:
        return 0
    sink = ctx.open(relpath)
    total = 0
    for stats in tables:
        spec = ctx.spec(stats.name)
        code_col = pick_column(stats, CODE_COLUMNS)
        keys = RowKeys(ctx, stats, "engagement")
        n = 0
        for _rowno, row in ctx.rows(stats):
            ctx.tick()
            key, pk, checksum = keys(row)
            rec = _rec(record_type, schema, key, stats.name, pk)
            if code_col is not None:
                rec["code_ref"] = code_ref(ctx, row.get(code_col))
            rec["fields_raw"] = raw_fields(ctx, stats.name, row, drop=(code_col,) if code_col else ())
            rec["semantics"] = "UNKNOWN" if spec is not None and spec.klass == REVIEW else "SETTINGS_REFERENCE"
            rec.update(extra_flags)
            rec.update(FLAGS_OFF)
            rec["provenance"] = ctx.provenance(stats.name, pk, checksum)
            sink.write(rec)
            n += 1
        keys.finish("engagement")
        ctx.coverage(stats.name, archived=n, status=status_for(spec))
        ctx.count("engagement", "converted" if spec is None or spec.klass != CONFIGURATION else "converted", n)
        if spec is not None and spec.klass == REVIEW:
            schema_unproven(ctx, stats, "engagement", spec)
        total += n
    return total


def run(ctx: ModuleContext) -> None:
    usage = _usage_index(ctx)  # only fork codes are staged; documented code columns are SECRET
    fork = _codes(ctx, "discount_sell", "code")
    out: dict[str, Any] = {
        "codes_by_kind": {}, "codes_by_state": {}, "duplicate_codes": 0, "used_counter_sum": 0,
        "usage_rows": 0, "usage_by_match": {}, "usage_customer_missing": 0,
        "referrals": {"total": 0, "both_customers": 0, "party_missing": 0, "self_referral": 0, "gift_paid": 0},
        "wheel": {"spins": 0, "wins": 0, "prize_unreadable": 0, "customer_missing": 0},
    }
    _discounts(ctx, usage, out)
    _usage(ctx, fork, out)
    _usage_totals(ctx, out)
    _referrals(ctx, out)
    _wheel(ctx, out)
    out["ad_campaigns"] = _raw_table(ctx, "ad_campaign", "records/engagement/ad_campaigns.jsonl",
                                     "legacy_ad_campaign", "m2n.engagement.ad_campaign.v1", {})
    out["program_settings_rows"] = _raw_table(ctx, "affiliates", "records/engagement/program_settings.jsonl",
                                              "legacy_referral_program_settings",
                                              "m2n.engagement.program_settings.v1", {"pays_commission": False})
    account_unstaged(ctx, ENGAGEMENT_TABLES, "engagement")
    for k in ("codes_by_kind", "codes_by_state", "usage_by_match"):
        out[k] = dict(sorted(out[k].items()))
    out["codes_exported_as_plaintext"] = 0
    out["redeemable_true"] = 0
    out["grants_credit_true"] = 0
    out["decision"] = "archive only: no code is redeemable, no referral reward or prize is credited"
    ctx.reconciliation("engagement", out)
    for entry in (
        {"entity": "discount_codes", "source_table": "Discount / DiscountSell / discount_sell",
         "source_columns": ["code→code_ref", "price", "limituse", "limitused", "…"],
         "target": "records/engagement/discounts.jsonl", "status": "archived",
         "note": "codes are bearer secrets: exported only as a salted digest"},
        {"entity": "referrals", "source_table": "reagent_report", "source_columns": ["user_id", "reagent", "get_gift", "time"],
         "target": "records/engagement/referrals.jsonl", "status": "archived",
         "note": "mirza_pro index.php:235-238; both parties validated against customers"},
    ):
        ctx.mapping(entry)


register_module(Module(key="engagement", title_fa="تخفیف، معرفی و گردونه (آرشیو)", run=run, order=410))


# ---------------------------------------------------------------------------------------
# synthetic tables (fabricated)
# ---------------------------------------------------------------------------------------

def _synth(out, state: SynthState) -> None:
    s = sql_str
    u = state.user_ids or ["7000000000"]

    def U(i: int) -> str:  # noqa: N802
        return u[i % len(u)]

    out.write("DROP TABLE IF EXISTS `Discount`;\nCREATE TABLE `Discount` (\n  `id` int(6) unsigned NOT NULL AUTO_INCREMENT,\n"
              "  `code` varchar(2000) DEFAULT NULL,\n  `price` varchar(200) DEFAULT NULL,\n"
              "  `limituse` varchar(200) DEFAULT NULL,\n  `limitused` varchar(200) DEFAULT NULL,\n  PRIMARY KEY (`id`)\n"
              ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n")
    state.write_rows(out, "Discount", [
        f"(1,{s('SYNGIFT-UNUSED')},{s('50000')},{s('10')},{s('0')})",
        f"(2,{s('SYNGIFT-USED')},{s('20000')},{s('5')},{s('3')})",
        f"(3,{s('SYNGIFT-MISMATCH')},{s('10000')},{s('3')},{s('3')})",
        f"(4,{s('SYNGIFT-BAD')},{s('12.5')},NULL,NULL)",
    ])
    out.write("DROP TABLE IF EXISTS `DiscountSell`;\nCREATE TABLE `DiscountSell` (\n  `id` int(6) unsigned NOT NULL AUTO_INCREMENT,\n"
              "  `codeDiscount` varchar(1000) NOT NULL,\n  `price` varchar(200) NOT NULL,\n"
              "  `limitDiscount` varchar(500) NOT NULL,\n  `agent` varchar(500) NOT NULL,\n"
              "  `usefirst` varchar(100) NOT NULL,\n  `useuser` varchar(100) NOT NULL,\n"
              "  `code_product` varchar(100) NOT NULL,\n  `code_panel` varchar(100) NOT NULL,\n"
              "  `time` varchar(100) NOT NULL,\n  `type` varchar(100) NOT NULL,\n  `usedDiscount` varchar(500) NOT NULL,\n"
              "  PRIMARY KEY (`id`)\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n")
    state.write_rows(out, "DiscountSell", [
        f"(1,{s('SYNSELL10')},{s('10')},{s('100')},{s('allusers')},{s('0')},{s('1')},{s('all')},{s('/all')},"
        f"{s('0')},{s('all')},{s('1')})",
        f"(2,{s('SYNSELL-AGENT')},{s('25')},{s('5')},{s('n')},{s('1')},{s('1')},{s('p001')},{s('syn-a')},"
        f"{s('1900000000')},{s('extend')},{s('0')})",
    ])
    out.write("DROP TABLE IF EXISTS `Giftcodeconsumed`;\nCREATE TABLE `Giftcodeconsumed` (\n"
              "  `id` int(6) unsigned NOT NULL AUTO_INCREMENT,\n  `code` varchar(2000) DEFAULT NULL,\n"
              "  `id_user` varchar(200) DEFAULT NULL,\n  PRIMARY KEY (`id`)\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n")
    state.write_rows(out, "Giftcodeconsumed", [
        f"(1,{s('SYNGIFT-USED')},{s(U(1))})", f"(2,{s('SYNGIFT-USED')},{s(U(2))})",
        f"(3,{s('SYNGIFT-USED')},{s('5000000500')})", f"(4,{s('SYNGIFT-MISMATCH')},{s(U(3))})",
        f"(5,{s('SYNSELL10')},{s(U(4))})", f"(6,{s('SYN-DELETED-CODE')},{s(U(5))})",
    ])
    out.write("DROP TABLE IF EXISTS `discount_sell`;\nCREATE TABLE `discount_sell` (\n  `id` int NOT NULL,\n"
              "  `code` varchar(100) DEFAULT NULL,\n  `percent` varchar(20) DEFAULT NULL,\n"
              "  `created_at` varchar(50) DEFAULT NULL,\n  PRIMARY KEY (`id`)\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n")
    state.write_rows(out, "discount_sell", [f"(1,{s('SYNFORK5')},{s('5')},{s('1700000000')})"])
    out.write("DROP TABLE IF EXISTS `discount_sell_used`;\nCREATE TABLE `discount_sell_used` (\n  `id` int NOT NULL,\n"
              "  `code` varchar(100) DEFAULT NULL,\n  `user_id` varchar(100) DEFAULT NULL,\n"
              "  `used_at` varchar(50) DEFAULT NULL,\n  PRIMARY KEY (`id`)\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n")
    state.write_rows(out, "discount_sell_used", [f"(1,{s('SYNFORK5')},{s(U(6))},{s('1700000500')})"])
    out.write("DROP TABLE IF EXISTS `affiliates`;\nCREATE TABLE `affiliates` (\n  `description` text,\n"
              "  `status_commission` varchar(200) DEFAULT NULL,\n  `Discount` varchar(200) DEFAULT NULL,\n"
              "  `price_Discount` varchar(200) DEFAULT NULL,\n  `porsant_one_buy` varchar(100) DEFAULT NULL,\n"
              "  `id_media` varchar(300) DEFAULT NULL\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n")
    state.write_rows(out, "affiliates", [f"({s('دوستانت را دعوت کن 🎁')},{s('oncommission')},"
                                         f"{s('onDiscountaffiliates')},{s('20000')},{s('off_buy_porsant')},{s('none')})"])
    out.write("DROP TABLE IF EXISTS `reagent_report`;\nCREATE TABLE `reagent_report` (\n"
              "  `id` int(6) unsigned NOT NULL AUTO_INCREMENT,\n  `user_id` bigint NOT NULL,\n  `get_gift` tinyint(1) NOT NULL,\n"
              "  `time` varchar(50) NOT NULL,\n  `reagent` varchar(30) NOT NULL,\n  PRIMARY KEY (`id`),\n"
              "  UNIQUE KEY `user_id` (`user_id`)\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;\n")
    state.write_rows(out, "reagent_report", [
        f"(1,{U(10)},0,{s('2024/05/01 10:00:00')},{s(U(11))})",
        f"(2,{U(12)},1,{s('2024/05/02 11:00:00')},{s('5000000600')})",  # referrer missing
        f"(3,5000000601,0,{s('1403/02/12 09:00:00')},{s(U(13))})",       # referee missing, Jalali-looking date
        f"(4,{U(14)},0,{s('2024/05/03 12:00:00')},{s(U(14))})",          # self referral
    ])
    out.write("DROP TABLE IF EXISTS `wheel_list`;\nCREATE TABLE `wheel_list` (\n  `id` int(6) unsigned NOT NULL AUTO_INCREMENT,\n"
              "  `id_user` varchar(200) NOT NULL,\n  `time` varchar(200) NOT NULL,\n  `first_name` varchar(200) NOT NULL,\n"
              "  `wheel_code` varchar(200) NOT NULL,\n  `price` varchar(200) NOT NULL,\n  PRIMARY KEY (`id`)\n"
              ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n")
    state.write_rows(out, "wheel_list", [
        f"(1,{s(U(20))},{s('2024/06/01 08:00:00')},{s('سارا 🌸')},{s('6')},{s('5000')})",
        f"(2,{s(U(21))},{s('2024/06/01 09:00:00')},{s('synthetic')},{s('3')},{s('0')})",
        f"(3,{s('5000000700')},{s('yesterday')},{s('ghost')},{s('1')},{s('5,000')})",
    ])
    out.write("DROP TABLE IF EXISTS `ad_campaign`;\nCREATE TABLE `ad_campaign` (\n  `id` int NOT NULL,\n"
              "  `title` varchar(200) DEFAULT NULL,\n  `ref_code` varchar(100) DEFAULT NULL,\n  `clicks` varchar(20) DEFAULT NULL,\n"
              "  `joins` varchar(20) DEFAULT NULL,\n  `created_at` varchar(50) DEFAULT NULL,\n  PRIMARY KEY (`id`)\n"
              ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n")
    state.write_rows(out, "ad_campaign", [f"(1,{s('کانال آزمایشی 📣')},{s('syn-ad-1')},{s('120')},{s('14')},"
                                          f"{s('1700000000')})"])


register_synth("engagement", _synth, order=410)

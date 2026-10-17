"""Engagement (codes, referrals, wheel) and support archive modules on synthetic backups."""

from __future__ import annotations

import pytest

from conftest import make_backup, read_jsonl, run
from mirza2nexa.modules.engagement import classify_gift, classify_percent
from mirza2nexa.modules.support import fake_bot_token
from mirza2nexa.nxpkg import PackageReader
from mirza2nexa.synth import TG_BASE

PLAINTEXT_CODES = ("SYNGIFT-UNUSED", "SYNGIFT-USED", "SYNGIFT-MISMATCH", "SYNGIFT-BAD", "SYNGIFT-ZERO", "SYNSELL10",
                   "SYNSELL-AGENT", "SYNSELL-OLD", "SYNSELL-ZERO", "SYNFORK5", "SYN-DELETED-CODE")
PERSONAL_TEXT = ("سرویسم وصل نمی‌شود", "پیام بدون صاحب", "کار نمی‌کند", "orphan message", "مشکل اتصال", "سارا")
OWNED = ("Discount", "DiscountSell", "discount_sell", "discount_sell_used", "Giftcodeconsumed", "affiliates",
         "reagent_report", "wheel_list", "ad_campaign", "support_message", "ticket", "ticket_message", "departman")


def recs(res, domain: str, name: str) -> list[dict]:
    return read_jsonl(res.stage_dir / "records" / domain / f"{name}.jsonl")


def review(res) -> list[dict]:
    return read_jsonl(res.stage_dir / "reports" / "review-queue.jsonl")


@pytest.fixture(scope="module")
def conv(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("engsup")
    zp, _ = make_backup(tmp, users=300, password="pw-Syn12345")
    return run(tmp, zp, password="pw-Syn12345"), tmp


def _report_texts(res) -> list[str]:
    rp = res.stage_dir / "reports"
    return [res.report_path.read_text(encoding="utf-8")] + [p.read_text(encoding="utf-8") for p in rp.rglob("*")
                                                            if p.is_file()]


def test_ready_without_blockers(conv):
    res, _ = conv
    assert res.report["readiness"] == "ready", res.report["blockers"]


# ---- engagement -------------------------------------------------------------------------

def test_discount_codes_never_exported_in_plaintext(conv):
    res, tmp = conv
    cov = {c["table"]: c for c in res.report["coverage"]}
    assert "code" in cov["Discount"]["dropped_secret_columns"]
    assert "codeDiscount" in cov["DiscountSell"]["dropped_secret_columns"]
    assert "code" in cov["Giftcodeconsumed"]["dropped_secret_columns"]
    blob = b"".join(p.read_bytes() for p in (res.stage_dir / "records").rglob("*") if p.is_file())
    reports = "\n".join(_report_texts(res))
    for code in PLAINTEXT_CODES:
        # the fork's discount_sell code travels as text in its own record (owner decision)
        if code != "SYNFORK5":
            assert code.encode() not in blob, code
        assert code not in reports, code
    discounts = recs(res, "engagement", "discounts")
    for d in discounts:
        # owner decision 2026-10-17: only the fork's discount_sell codes are carried as text
        assert d["code_plaintext_exported"] is (d["source_table"] == "discount_sell")
        if d["source_table"] in {"Discount", "DiscountSell"}:
            assert d["code_ref"] is None and d["usage_link"] == "UNAVAILABLE_CODE_SECRET"
        else:  # fork table: staged by the generic filter, exported only as a digest
            assert d["code_ref"].startswith("code:") and "code" not in d["fields_raw"]
    reports_refs = [d["code_ref"] for d in discounts if d["code_ref"]]
    assert all(r not in reports for r in reports_refs)
    # the decrypted package payload agrees
    reader = PackageReader(res.package_path, key=res.key_text, workdir=tmp / "verify-codes")
    reader.verify()
    for row in reader.iter_jsonl("records/engagement/discounts.jsonl"):
        carried = row.get("code_text")
        rest = {k: v for k, v in row.items() if k != "code_text"}
        assert not any(c in str(rest) for c in PLAINTEXT_CODES)
        assert carried is None or (row["source_table"] == "discount_sell" and carried in PLAINTEXT_CODES)


def test_discount_state_and_usage_history(conv):
    res, _ = conv
    discounts = {(d["source_table"], d["source_pk"]): d for d in recs(res, "engagement", "discounts")}
    usage = recs(res, "engagement", "discount_usage")
    used = discounts[("Discount", "2")]  # limitused 3 of 5
    assert used["used_count"] == 3 and used["state"] == "PARTIALLY_USED" and used["consumed_rows"] is None
    assert used["credit_amount"] == {"amount_minor": "20000", "currency": "IRT"}
    assert discounts[("Discount", "1")]["state"] == "UNUSED"
    assert discounts[("Discount", "3")]["state"] == "EXHAUSTED"
    bad = discounts[("Discount", "4")]
    assert bad["credit_amount_parse"] == "NOT_A_NUMBER" and bad["credit_amount"]["amount_minor"] is None
    assert discounts[("DiscountSell", "1")]["percent"] == 10
    assert discounts[("DiscountSell", "2")]["expires_at_note"] == "OK_UNIX"
    gift_usage = [u for u in usage if u["source_table"] == "Giftcodeconsumed"]
    assert len(gift_usage) == 6
    assert all(u["code_match"] == "UNAVAILABLE_CODE_SECRET" and u["code_ref"] is None for u in gift_usage)
    assert sum(1 for u in gift_usage if not u["customer"]["valid"]) == 1
    fork_usage = [u for u in usage if u["source_table"] == "discount_sell_used"]
    assert [u["code_match"] for u in fork_usage] == ["discount_sell"]
    assert discounts[("discount_sell", "1")]["consumed_rows"] == 1
    eng = res.report["reconciliation"]["engagement"]
    assert eng["used_counter_sum"] == 7 and eng["usage_log_rows"] == 6 and eng["usage_totals_match"] is False
    codes = {r["code"] for r in review(res)}
    assert {"DISCOUNT_USAGE_TOTAL_MISMATCH", "DISCOUNT_USAGE_CUSTOMER_MISSING", "DISCOUNT_AMOUNT_UNREADABLE"} <= codes
    for name in ("discounts", "discount_usage"):
        for r in recs(res, "engagement", name):
            assert r["redeemable"] is False and r["grants_credit"] is False and r["affects_wallet"] is False


def test_referral_to_missing_user_and_self_referral(conv):
    res, _ = conv
    refs = {r["source_pk"]: r for r in recs(res, "engagement", "referrals")}
    assert refs["1"]["relation_valid"] is True
    assert refs["2"]["referrer"]["decision"] == "NOT_IN_BACKUP" and refs["2"]["relation_valid"] is False
    assert refs["3"]["referee"]["decision"] == "NOT_IN_BACKUP" and refs["3"]["attributed_at_note"] == "JALALI_SUSPECTED"
    assert refs["4"]["relation_valid"] is False
    rq = [r for r in review(res) if r["code"] == "REFERRAL_PARTY_MISSING"]
    assert sorted(r["data"]["missing"] for r in rq) == [["referee"], ["referrer"]]
    assert any(r["code"] == "REFERRAL_SELF" for r in review(res))
    rec = res.report["reconciliation"]["engagement"]["referrals"]
    assert rec == {"total": 4, "both_customers": 1, "party_missing": 2, "self_referral": 1, "gift_paid": 1,
                   "gift_unclaimed": 3}
    assert all(r["pays_commission"] is False for r in refs.values())


def test_wheel_prizes_are_history_only(conv):
    res, _ = conv
    spins = recs(res, "engagement", "wheel_results")
    assert [s["won"] for s in sorted(spins, key=lambda s: s["source_pk"])] == [True, False, None]
    assert all("first_name" not in str(s) and "سارا" not in str(s) for s in spins)
    w = res.report["reconciliation"]["engagement"]["wheel"]
    assert w["prizes_total"]["credited_by_package"] == "0" and w["wins"] == 1


# ---- support -------------------------------------------------------------------------

def test_planted_secret_redacted_everywhere(conv):
    res, tmp = conv
    tok = fake_bot_token()
    blob = b"".join(p.read_bytes() for p in res.stage_dir.rglob("*") if p.is_file())
    assert tok.encode() not in blob
    assert tok.split(":")[1].encode() not in blob
    assert all(tok not in t for t in _report_texts(res))
    msgs = recs(res, "support", "support_messages")
    assert any("[REDACTED:bot_token]" in (m["customer_text"] or "") for m in msgs)
    tms = recs(res, "support", "ticket_messages")
    assert any("[REDACTED:bot_token]" in m["fields_raw"]["message"] for m in tms)
    assert any("[REDACTED:sub_url]" in m["fields_raw"]["message"] for m in tms)
    reader = PackageReader(res.package_path, key=res.key_text, workdir=tmp / "verify-tok")
    reader.verify()
    for rel in ("records/support/support_messages.jsonl", "records/support/ticket_messages.jsonl"):
        for row in reader.iter_jsonl(rel):
            assert tok not in str(row)
    cov = {c["table"]: c for c in res.report["coverage"]}
    assert cov["support_message"]["redacted_cells"] >= 1 and cov["ticket_message"]["redacted_cells"] >= 2


def test_personal_text_never_in_reports_or_review(conv):
    res, _ = conv
    texts = _report_texts(res)
    for t in PERSONAL_TEXT:
        assert all(t not in x for x in texts), t
    for r in review(res):
        if r["entity"] in {"support", "engagement", "agents"}:
            assert set(r["data"]) <= {"decision", "owner_column", "ticket_column", "messages", "rows",
                                      "open_questions", "missing", "code_match", "used_count", "consumed_rows",
                                      "parse", "codes", "duplicates", "tier", "owners",
                                      "used_counter_sum", "reason", "aggregated", "total_rows",
                                      "remaining_uses", "tickets", "sample_pks", "threads", "agents",
                                      "role_candidates", "columns"}
            assert r["data"].get("reason"), r["code"]


def test_ticket_without_valid_owner(conv):
    res, _ = conv
    tickets = {t["source_pk"]: t for t in recs(res, "support", "tickets")}
    assert tickets["1"]["customer"]["valid"] is True
    assert tickets["2"]["customer"]["decision"] == "NOT_IN_BACKUP"
    assert tickets["3"]["customer"]["decision"] == "ABSENT"
    bad = {r["source_ref"]["pk"] for r in review(res)
           if r["code"] == "TICKET_OWNER_UNKNOWN" and r["source_ref"]["table"] == "ticket"}
    assert bad == {"2", "3"}
    sm = [r for r in review(res) if r["code"] == "TICKET_OWNER_UNKNOWN" and r["source_ref"]["table"] == "support_message"]
    assert [r["source_ref"]["pk"] for r in sm] == ["12"]


def test_message_threads_are_ordered(conv):
    res, _ = conv
    msgs = recs(res, "support", "support_messages")
    owner = str(TG_BASE + 30)
    thread = [m for m in msgs if m["customer"]["raw_id"] == owner]
    assert len({m["thread_ref"] for m in thread}) == 1
    assert [m["source_pk"] for m in sorted(thread, key=lambda m: m["seq"])] == ["10", "9", "11"]
    by_pk = {m["source_pk"]: m for m in thread}
    assert by_pk["10"]["nexa_status_proposal"] == "WAITING_FOR_CUSTOMER"
    assert by_pk["9"]["nexa_status_proposal"] == "WAITING_FOR_SUPPORT"
    assert by_pk["11"]["nexa_status_proposal"] == "UNKNOWN" and by_pk["11"]["status_raw"] == "close"
    tms = sorted((m for m in recs(res, "support", "ticket_messages") if m["ticket_ref"]["raw"] == "1"),
                 key=lambda m: m["seq"])
    assert [m["source_pk"] for m in tms] == ["2", "1", "3"]
    orphan = [m for m in recs(res, "support", "ticket_messages") if m["ticket_ref"]["raw"] == "77"]
    assert orphan and orphan[0]["ticket_ref"]["ticket_exists"] is False
    assert any("🛠" in (m["department_name"] or "") for m in msgs)  # Unicode/emoji preserved


def test_coverage_accounts_every_row(conv):
    res, _ = conv
    cov = {c["table"]: c for c in res.report["coverage"]}
    for t in OWNED:
        assert cov[t]["rows"] > 0, t
        assert cov[t]["accounted_rows"] == cov[t]["rows"], t
        assert cov[t]["status"] in {"archived", "review"}, t


def test_idempotency_keys_unique_and_flags(conv):
    res, _ = conv
    keys: list[str] = []
    for domain in ("engagement", "support", "agents"):
        for p in sorted((res.stage_dir / "records" / domain).glob("*.jsonl")):
            for r in read_jsonl(p):
                assert r["idempotency_key"].startswith("legacy:")
                assert r["applies_to_live_state"] is False and r["affects_wallet"] is False
                assert r["provenance"]["source_table"]
                keys.append(r["idempotency_key"])
    assert len(keys) == len(set(keys))


def test_engagement_support_deterministic(tmp_path):
    zp, _ = make_backup(tmp_path, users=120)
    a = run(tmp_path, zp, ws_name="a")
    b = run(tmp_path, zp, ws_name="b")
    for domain in ("engagement", "support"):
        fa = sorted((a.stage_dir / "records" / domain).glob("*.jsonl"))
        assert fa
        for p in fa:
            assert p.read_bytes() == (b.stage_dir / "records" / domain / p.name).read_bytes(), p.name


# ---- discount lifecycle (discounts_state) ------------------------------------------------

def test_discount_lifecycle_per_code(conv):
    res, _ = conv
    d = {(x["source_table"], x["source_pk"]): x for x in recs(res, "engagement", "discounts")}
    expect = {
        ("Discount", "1"): ("HAS_FINANCIAL_OBLIGATION", 10, "500000"),
        ("Discount", "2"): ("HAS_FINANCIAL_OBLIGATION", 2, "40000"),
        ("Discount", "3"): ("USED", 0, "0"),
        ("Discount", "4"): ("UNKNOWN", None, None),
        ("Discount", "5"): ("ACTIVE_UNUSED", 2, "0"),
        ("DiscountSell", "1"): ("ACTIVE_UNUSED", 99, None),
        ("DiscountSell", "2"): ("ACTIVE_UNUSED", 5, None),
        ("DiscountSell", "3"): ("EXPIRED", 10, None),
        ("DiscountSell", "4"): ("USED", 0, None),
        ("discount_sell", "1"): ("UNKNOWN", None, None),
    }
    assert set(d) == set(expect)
    for k, (state, remaining, outstanding) in expect.items():
        rec = d[k]
        assert rec["lifecycle_state"] == state, k
        assert rec["remaining_uses"] == remaining, k
        assert rec["outstanding_gift_value"]["amount_minor"] == outstanding, k
        assert rec["lifecycle_reason"], k
        assert rec["issues_new_credit"] is False and rec["redeemable"] is False and rec["grants_credit"] is False
    assert d[("Discount", "4")]["lifecycle_reason"] == "COUNTERS_UNREADABLE"
    assert d[("DiscountSell", "3")]["lifecycle_reason"] == "EXPIRED_BEFORE_BACKUP"
    assert d[("DiscountSell", "3")]["lifecycle_as_of_basis"] == "LATEST_INVOICE_TIME_SELL"
    assert d[("Discount", "1")]["owner_decision"] == "HONOUR_OR_LAPSE"
    assert d[("Discount", "3")]["owner_decision"] is None


def test_discounts_state_and_outstanding_gift_sum(conv):
    res, _ = conv
    ds = res.report["reconciliation"]["discounts_state"]
    assert ds["by_state"] == {"USED": 2, "EXPIRED": 1, "ACTIVE_UNUSED": 3, "HAS_FINANCIAL_OBLIGATION": 2,
                              "UNKNOWN": 2}
    assert sum(ds["by_state"].values()) == len(recs(res, "engagement", "discounts"))
    # raw units, not re-scaled: 10 × 50000 + 2 × 20000
    assert ds["outstanding_gift_value_known_minor"] == "540000"
    # one gift code has unreadable counters, so the total is not complete → null for readiness
    assert ds["outstanding_gift_value_minor"] is None and ds["outstanding_gift_value_complete"] is False
    assert ds["gift_codes_value_unknown"] == 1 and ds["outstanding_gift_codes"] == 2
    assert ds["outstanding_remaining_uses"] == 12
    assert ds["issues_new_credit"] is False and ds["new_codes_created"] == 0 and ds["redeemable_true"] == 0
    codes = {r["code"]: r for r in review(res)}
    assert codes["GIFT_OUTSTANDING_OBLIGATION"]["data"]["codes"] == 2
    assert codes["GIFT_VALUE_UNKNOWN"]["data"]["codes"] == 1
    assert codes["DISCOUNT_ACTIVE_CODES"]["data"]["codes"] == 2
    assert codes["DISCOUNT_STATE_UNKNOWN"]["data"]["codes"] == 1
    # no code text, no code_ref and no per-code value in the state section or its reviews
    blob = str(ds) + str([r for r in review(res) if r["code"].startswith(("GIFT_", "DISCOUNT_"))])
    for code in PLAINTEXT_CODES:
        assert code not in blob
    assert "code:" not in blob


@pytest.mark.parametrize(("used", "limit", "amount", "expected"), [
    (0, 10, 50000, ("HAS_FINANCIAL_OBLIGATION", "REMAINING_USES_CREDIT_WALLET", 10, 500000)),
    (3, 5, 20000, ("HAS_FINANCIAL_OBLIGATION", "REMAINING_USES_CREDIT_WALLET", 2, 40000)),
    (3, 3, 10000, ("USED", "LIMIT_REACHED", 0, 0)),
    (4, 3, 10000, ("USED", "LIMIT_REACHED", 0, 0)),
    (0, 0, 10000, ("USED", "LIMIT_REACHED", 0, 0)),
    (0, 2, 0, ("ACTIVE_UNUSED", "REMAINING_USES_ZERO_VALUE", 2, 0)),
    (0, 2, None, ("UNKNOWN", "GIFT_AMOUNT_UNREADABLE", 2, None)),
    (None, 2, 100, ("UNKNOWN", "COUNTERS_UNREADABLE", None, None)),
])
def test_classify_gift(used, limit, amount, expected):
    assert classify_gift(used, limit, amount) == expected


@pytest.mark.parametrize(("used", "limit", "expiry", "as_of", "expected"), [
    (1, 100, "0", 1_700_000_000, ("ACTIVE_UNUSED", "REMAINING_USES_PERCENT_DISCOUNT", 99)),
    (0, 5, "1900000000", 1_700_000_000, ("ACTIVE_UNUSED", "REMAINING_USES_PERCENT_DISCOUNT", 5)),
    (0, 5, "1700000000", 1_700_000_000, ("EXPIRED", "EXPIRED_BEFORE_BACKUP", 5)),  # time() >= time
    (5, 5, "1600000000", 1_700_000_000, ("USED", "LIMIT_REACHED", 0)),
    (0, 5, "1600000000", None, ("UNKNOWN", "EXPIRY_REFERENCE_UNAVAILABLE", 5)),
    (0, 5, "soon", 1_700_000_000, ("UNKNOWN", "EXPIRY_UNREADABLE", 5)),
    (None, 5, "0", 1_700_000_000, ("UNKNOWN", "COUNTERS_UNREADABLE", None)),
])
def test_classify_percent(used, limit, expiry, as_of, expected):
    assert classify_percent(used, limit, expiry, as_of) == expected


def test_referrals_wheel_state(conv):
    res, _ = conv
    rw = res.report["reconciliation"]["referrals_wheel_state"]
    assert rw["referrals"] == 4 and rw["start_gift_claimed"] == 1 and rw["start_gift_unclaimed_contingent"] == 3
    assert rw["wheel_spins"] == 3 and rw["wheel_wins"] == 1 and rw["wheel_prizes_total_minor"] == "5000"
    assert rw["wheel_prize_already_in_legacy_balance"] is True and rw["credited_by_package"] == "0"
    assert rw["referral_attribution_imported"] is False


# ---- support state ------------------------------------------------------------------------

def test_ticket_status_classes_are_labelled_unproven(conv):
    res, _ = conv
    tickets = {t["source_pk"]: t for t in recs(res, "support", "tickets")}
    assert {k: t["status"] for k, t in tickets.items()} == {"1": "OPEN", "2": "CLOSED", "3": "OPEN",
                                                            "4": "ANSWERED", "5": "UNKNOWN"}
    assert tickets["5"]["status_raw"] == "7"
    for t in tickets.values():
        assert t["status_basis"] == "STATUS_TOKEN_UNPROVEN"
        assert t["nexa_view"] == "LEGACY_READ_ONLY_THREAD_UNDER_CUSTOMER_SUPPORT"
    assert tickets["1"]["owner_decision"] == "OPEN_TICKET_OWNER_DECISION" and tickets["2"]["owner_decision"] is None
    st = res.report["reconciliation"]["support_state"]
    assert st["tickets"] == 5 and st["open_tickets"] == 2
    assert st["tickets_by_status"] == {"OPEN": 2, "ANSWERED": 1, "CLOSED": 1, "UNKNOWN": 1}
    assert st["ticket_messages"] == 4 and st["ticket_status_basis"] == "STATUS_TOKEN_UNPROVEN"


def test_open_tickets_never_create_or_send_anything(conv):
    res, _ = conv
    st = res.report["reconciliation"]["support_state"]
    assert st["creates_ticket_true"] == 0 and st["sends_message_true"] == 0 and st["notifies_customer_true"] == 0
    assert st["imported_into_nexa_tickets"] is False
    for name in ("tickets", "ticket_messages", "support_messages", "departments"):
        for r in recs(res, "support", name):
            assert r["creates_ticket"] is False and r["sends_message"] is False
            assert r["notifies_customer"] is False and r["applies_to_live_state"] is False
    files = sorted(p.name for p in (res.stage_dir / "records" / "support").glob("*.jsonl"))
    assert files == ["departments.jsonl", "support_messages.jsonl", "ticket_messages.jsonl", "tickets.jsonl"]
    rq = [r for r in review(res) if r["code"] == "TICKET_OPEN_OWNER_DECISION"]
    assert len(rq) == 1 and rq[0]["data"]["tickets"] == 2 and rq[0]["data"]["sample_pks"] == ["1", "3"]


def test_support_thread_state_from_last_message(conv):
    res, _ = conv
    st = res.report["reconciliation"]["support_state"]
    assert st["support_threads_by_state"] == {"OPEN": 1, "UNKNOWN": 2} and st["support_threads_open"] == 1
    msgs = recs(res, "support", "support_messages")
    thread = [m for m in msgs if m["customer"]["raw_id"] == str(TG_BASE + 30)]
    assert {m["thread_state"] for m in thread} == {"UNKNOWN"}  # last message is the 'close' row
    orphan = next(m for m in msgs if m["source_pk"] == "12")
    assert orphan["thread_state"] == "OPEN" and orphan["message_state"] == "OPEN"
    rq = [r for r in review(res) if r["code"] == "SUPPORT_THREAD_OPEN_OWNER_DECISION"]
    assert len(rq) == 1 and rq[0]["data"]["threads"] == 1


def test_state_sections_deterministic(tmp_path):
    import json

    zp, _ = make_backup(tmp_path, users=120)
    a = run(tmp_path, zp, ws_name="a")
    b = run(tmp_path, zp, ws_name="b")
    for sec in ("agents_state", "discounts_state", "support_state", "referrals_wheel_state"):
        assert json.dumps(a.report["reconciliation"][sec], sort_keys=True) == \
            json.dumps(b.report["reconciliation"][sec], sort_keys=True), sec
    ra = (a.stage_dir / "reports" / "review-queue.jsonl").read_bytes()
    rb = (b.stage_dir / "reports" / "review-queue.jsonl").read_bytes()
    assert ra == rb


def test_fork_discount_codes_carried_as_text_by_owner_decision(conv):
    # Owner decision 2026-10-17: only the fork's `discount_sell` codes travel as text (NEXA
    # recreates them DISABLED); documented tables and the usage table keep code_ref only.
    res, _tmp = conv
    discounts = recs(res, "engagement", "discounts")
    fork = [d for d in discounts if d["source_table"] == "discount_sell"]
    assert fork and all(d["code_plaintext_exported"] and d.get("code_text") for d in fork)
    for d in discounts:
        if d["source_table"] != "discount_sell":
            assert "code_text" not in d and d["code_plaintext_exported"] is False
    for u in recs(res, "engagement", "discount_usage"):
        assert "code_text" not in u

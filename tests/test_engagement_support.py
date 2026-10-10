"""Engagement (codes, referrals, wheel) and support archive modules on synthetic backups."""

from __future__ import annotations

import pytest

from conftest import make_backup, read_jsonl, run
from mirza2nexa.modules.support import fake_bot_token
from mirza2nexa.nxpkg import PackageReader
from mirza2nexa.synth import TG_BASE

PLAINTEXT_CODES = ("SYNGIFT-UNUSED", "SYNGIFT-USED", "SYNGIFT-MISMATCH", "SYNGIFT-BAD", "SYNSELL10",
                   "SYNSELL-AGENT", "SYNFORK5", "SYN-DELETED-CODE")
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
        assert code.encode() not in blob, code
        assert code not in reports, code
    discounts = recs(res, "engagement", "discounts")
    for d in discounts:
        assert d["code_plaintext_exported"] is False
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
        assert not any(c in str(row) for c in PLAINTEXT_CODES)


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
    assert rec == {"total": 4, "both_customers": 1, "party_missing": 2, "self_referral": 1, "gift_paid": 1}
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
                                      "used_counter_sum"}


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

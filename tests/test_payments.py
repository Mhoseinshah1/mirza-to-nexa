"""Payment_report → read-only payment history (synthetic data only)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from conftest import make_backup, read_jsonl, run
from mirza2nexa.modules import payments
from mirza2nexa.synth import DDL_INVOICE, DDL_PRODUCT, sql_str

USER_DDL = ("CREATE TABLE `user` (`id` varchar(500) NOT NULL, `limit_usertest` int, `Balance` varchar(64), "
            "`User_Status` varchar(50), PRIMARY KEY (`id`));\n")
USERS = ("INSERT INTO `user` VALUES ('7100000001',1,'5000','Active'),('7100000002',1,'0','Active'),"
         "('7100000003',1,'-700','frozen'),('0123',1,'10','Active');\n")


def _zip_sql(tmp: Path, sql: str, name: str = "x.zip") -> Path:
    p = tmp / name
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("backup.sql", sql)
    return p


def pay_row(pid, uid="7100000001", oid="0123456789", time="2025/01/02 10:00:00", updated=None, price="10000",
            dec=None, method="zarinpal", status="paid", msg=None, trace=None) -> str:
    return (f"({pid},{sql_str(uid)},{sql_str(oid)},{sql_str(time)},{sql_str(updated)},{sql_str(price)},"
            f"{sql_str(dec)},{sql_str(method)},{sql_str(status)},{'NULL' if msg is None else msg},{sql_str(trace)})")


def convert(tmp: Path, rows: list[str], *, extra: str = "", name: str = "x.zip"):
    sql = USER_DDL + USERS + extra + payments.DDL
    if rows:
        sql += "INSERT INTO `Payment_report` VALUES " + ",".join(rows) + ";\n"
    return run(tmp, _zip_sql(tmp, sql, name), ws_name="ws-" + name)


def recs(res) -> list[dict]:
    return read_jsonl(res.stage_dir / "records" / "payments.jsonl")


def review(res, table: str = "Payment_report") -> list[dict]:
    return [r for r in read_jsonl(res.stage_dir / "reports" / "review-queue.jsonl")
            if r["source_ref"]["table"] == table]


def codes(res) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in review(res):
        out[r["code"]] = out.get(r["code"], 0) + 1
    return out


def blockers(res) -> set[str]:
    return {b["code"] for b in res.report["blockers"]}


def cov(res, table: str) -> dict:
    return next(c for c in res.report["coverage"] if c["table"] == table)


@pytest.fixture(scope="module")
def healthy(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("pay-healthy")
    zp, exp = make_backup(tmp, users=250)
    return run(tmp, zp), exp


# ---- healthy synthetic backup -----------------------------------------------------------

def test_healthy_backup_stays_ready_and_archives_every_row(healthy):
    res, exp = healthy
    assert res.report["readiness"] == "ready", res.report["blockers"]
    rs = recs(res)
    assert len(rs) == exp["payments"]
    rec = res.report["reconciliation"]["payments"]
    assert rec["balanced"] and rec["source_rows"] == rec["archived"] == exp["payments"]
    c = cov(res, "Payment_report")
    assert c["status"] == "archived" and c["accounted_rows"] == c["rows"] == exp["payments"]
    assert sum(v["count"] for v in rec["by_outcome"].values()) == exp["payments"]
    assert len({r["idempotency_key"] for r in rs}) == len(rs)


def test_records_are_history_only(healthy):
    res, _ = healthy
    for r in recs(res):
        assert r["record_type"] == "legacy_payment_history" and r["schema"] == "mirza.payment_report.v1"
        assert r["idempotency_key"] == f"legacy:payment:{r['payment_id']}"
        for flag in ("creates_payment", "affects_wallet", "counts_as_revenue", "enters_live_payment_state_machine",
                     "triggers_service", "applies_to_live_state"):
            assert r[flag] is False
        assert r["provenance"]["source_table"] == "Payment_report"
        if r["amount"]["parse_note"] == "OK":  # never re-scaled
            assert r["amount"]["amount_minor"] == r["amount"]["raw"].strip() and r["amount"]["currency"] == "IRT"


def test_healthy_anomalies_are_review_items_not_blockers(healthy):
    res, _ = healthy
    c = codes(res)
    assert c["PAYMENT_STATUS_UNKNOWN"] == 1  # three 'refunded' rows → one row per distinct value
    assert c["PAYMENT_METHOD_UNKNOWN"] == 1
    assert c["DUPLICATE_RECEIPT"] == 1
    assert c["PAYMENT_CUSTOMER_UNKNOWN"] == 1
    assert c["PAYMENT_AMOUNT_UNREADABLE"] == 1
    assert c["PAYMENT_TIME_FORMAT_UNKNOWN"] == 1
    assert not {b for b in blockers(res) if b.startswith("PAYMENT")}


def test_admin_debit_is_explicit_and_not_income(healthy):
    res, _ = healthy
    debits = [r for r in recs(res) if r["method"]["raw"] == "low balance by admin"]
    assert debits
    assert all(r["method"]["direction"] == "ADMIN_DEBIT_STORED_POSITIVE" for r in debits)
    rec = res.report["reconciliation"]["payments"]
    ex = int(rec["succeeded_excluding_admin_adjustments"]["amount_minor"])
    assert ex < int(rec["by_outcome"]["SUCCEEDED"]["amount_minor"])


# ---- statuses / methods ----------------------------------------------------------------

def test_successful_and_failed_historical_payments(tmp_path):
    rows = [pay_row(1, status="paid"), pay_row(2, status="reject", dec="رسید نامعتبر"), pay_row(3, status="expire"),
            pay_row(4, status="Unpaid"), pay_row(5, status="waiting", method="cart to cart")]
    res = convert(tmp_path, rows)
    out = {r["payment_id"]: r["status"] for r in recs(res)}
    assert out["1"] == {"raw": "paid", "outcome": "SUCCEEDED", "reason": "PAID"}
    assert out["2"]["outcome"] == "FAILED" and out["2"]["reason"] == "REJECTED_BY_ADMIN"
    assert out["3"]["outcome"] == "CANCELLED" and out["4"]["outcome"] == "PENDING"
    assert out["5"]["reason"] == "RECEIPT_AWAITING_REVIEW"
    by = res.report["reconciliation"]["payments"]["by_outcome"]
    assert by["SUCCEEDED"] == {"count": 1, "amount_minor": "10000", "unparsed_amounts": 0}
    assert by["FAILED"]["count"] == 1 and by["UNKNOWN"]["count"] == 0
    assert recs(res)[1]["rejection_note"]["text"] == "رسید نامعتبر"


def test_unknown_status_is_unknown_and_reviewed_once_per_value(tmp_path):
    rows = [pay_row(i, status="refunded") for i in range(1, 6)] + [pay_row(6, status="Paid"), pay_row(7, status=None)]
    res = convert(tmp_path, rows)
    assert {r["status"]["outcome"] for r in recs(res)} == {"UNKNOWN"}
    unk = [r for r in review(res) if r["code"] == "PAYMENT_STATUS_UNKNOWN"]
    assert sorted((r["data"]["status_raw"], r["data"]["rows"]) for r in unk) == [
        ("<NULL>", 1), ("Paid", 1), ("refunded", 5)]
    assert all(r["severity"] == "warning" for r in unk)
    assert res.report["reconciliation"]["payments"]["unknown_status"] == {
        "rows": 7, "distinct_values": 3,
        "top_values": [{"status_raw": "refunded", "rows": 5}, {"status_raw": "<NULL>", "rows": 1},
                       {"status_raw": "Paid", "rows": 1}]}


def test_unknown_status_top_values_are_bounded(tmp_path):
    rows = [pay_row(i, status=f"st-{i:03d}") for i in range(1, 31)]
    res = convert(tmp_path, rows)
    u = res.report["reconciliation"]["payments"]["unknown_status"]
    assert u["rows"] == 30 and u["distinct_values"] == 30
    assert len(u["top_values"]) == payments.TOP_VALUES_LIMIT
    assert u["top_values"][0] == {"status_raw": "st-001", "rows": 1}


def test_money_unit_evidence_is_statistics_only(tmp_path):
    product = (DDL_PRODUCT + "INSERT INTO `product` (`id`,`code_product`,`price_product`) VALUES "
               "(1,'p1','50000'),(2,'p2','100000'),(3,'p3','x');\n")
    rows = [pay_row(1, price="50000"), pay_row(2, price="100000"), pay_row(3, price="500000"),
            pay_row(4, price="5000"), pay_row(5, price="70000"), pay_row(6, price="1,000"),
            pay_row(7, price="9000", method="add balance by admin"), pay_row(8, price="50000", status="Unpaid")]
    res = convert(tmp_path, rows, extra=product)
    ev = res.report["reconciliation"]["money_unit_evidence"]
    assert ev["decision"] == "OWNER" and ev["configured_unit"] == "toman"
    d = ev["distributions"]
    # per-customer amounts: no min/max; too few values for even rounded quantiles
    assert d["payment_customer_paid"] == {
        "positive_values": 5, "zero_values": 0, "unparsed_values": 1, "p10": None, "p50": None, "p90": None,
        "per_customer_values": True, "quantiles": "WITHHELD_FEWER_THAN_20_VALUES", "min_max": "NOT_REPORTED",
        "share_multiple_of_10": "1.0000", "share_multiple_of_100": "1.0000", "share_multiple_of_1000": "1.0000"}
    assert d["product_price"]["min"] == "50000" and d["product_price"]["max"] == "100000"  # prices stay exact
    assert d["payment_admin_adjustment"]["positive_values"] == 1
    assert d["product_price"]["positive_values"] == 2 and d["product_price"]["unparsed_values"] == 1
    x = ev["cross_checks"]["payment_customer_paid_vs_product_price"]
    # 50000, 100000 equal; 500000 = 10 x 50000; 5000 = 50000 / 10; 70000 matches nothing
    assert (x["equal_to_a_reference_value"], x["equal_to_10x_a_reference_value"],
            x["equal_to_a_reference_value_div_10"]) == (2, 1, 1)
    assert x["share_equal"] == "0.4000" and x["median_ratio"] is None  # the payment median is withheld
    assert ev["cross_checks"]["invoice_price_vs_product_price"] is None  # no invoice table
    assert ev["code_evidence"] and "Toman" in ev["explanation"]
    # observations never change an amount
    assert {r["amount"]["amount_minor"] for r in recs(res) if r["payment_id"] == "3"} == {"500000"}


def test_round_sig2():
    assert [payments.round_sig2(v) for v in (7, 99, 100, 149, 150, 12345, 123456789, 995, 9950)] == \
        [7, 99, 100, 150, 150, 12000, 120000000, 1000, 10000]


def test_money_unit_evidence_has_no_exact_per_customer_value(tmp_path):
    # 25 customers with distinctive balances and wallet amounts, 25 distinctive payments
    users = ",".join(f"('71000001{i:02d}',1,'{123457 + i * 1013}','Active')" for i in range(25))
    wallet = ("CREATE TABLE `wallet_transaction` (`id` bigint NOT NULL, `id_user` varchar(200), `amount` bigint, "
              "`balance_before` bigint, `balance_after` bigint, `created_at` datetime, PRIMARY KEY (`id`));\n"
              "INSERT INTO `wallet_transaction` VALUES " + ",".join(
                  f"({i + 1},'71000001{i:02d}',{98761 + i * 777},0,{98761 + i * 777},'2025-01-01 10:00:00')"
                  for i in range(25)) + ";\n")
    rows = [pay_row(i + 1, uid=f"71000001{i:02d}", price=str(54321 + i * 919)) for i in range(25)]
    rows.append(pay_row(99, uid="7100000100", price="76543", method="add balance by admin"))
    res = convert(tmp_path, rows, extra="INSERT INTO `user` VALUES " + users + ";\n" + wallet)
    d = res.report["reconciliation"]["money_unit_evidence"]["distributions"]
    exact = {str(123457 + i * 1013) for i in range(25)} | {str(98761 + i * 777) for i in range(25)} \
        | {str(54321 + i * 919) for i in range(25)} | {"76543"}
    for src in ("user_balance_nonzero_abs", "wallet_transaction_amount_abs", "payment_customer_paid",
                "payment_admin_adjustment"):
        dd = d[src]
        assert "min" not in dd and "max" not in dd and dd["min_max"] == "NOT_REPORTED", src
        assert not exact & {str(v) for v in dd.values()}, src
    assert d["user_balance_nonzero_abs"]["positive_values"] == 28  # 25 + the 3 non-zero base users
    assert d["wallet_transaction_amount_abs"]["p50"] == "110000"   # 98761 + 12 * 777 = 108085 → 2 digits
    assert d["payment_admin_adjustment"]["p50"] is None             # one value: withheld
    text = json.dumps(res.report["reconciliation"]["money_unit_evidence"])
    for v in exact:
        assert f'"{v}"' not in text, v


def test_unknown_method_maps_to_unknown(tmp_path):
    res = convert(tmp_path, [pay_row(1, method="crypto-x"), pay_row(2, method="Nowpayments")])
    m = {r["payment_id"]: r["method"]["normalized"] for r in recs(res)}
    assert m == {"1": "UNKNOWN", "2": "CRYPTO_NOWPAYMENTS"}


# ---- keys, receipts, relations ------------------------------------------------------------

def test_duplicate_payment_id_blocks(tmp_path):
    res = convert(tmp_path, [pay_row(1), pay_row(1, price="20000"), pay_row(2)])
    assert "PAYMENT_SOURCE_KEY_DUPLICATED" in blockers(res)
    assert [r["payment_id"] for r in recs(res)] == ["2"]
    dup = [r for r in review(res) if r["code"] == "SOURCE_KEY_DUPLICATED"]
    assert len(dup) == 2 and all(r["severity"] == "blocker" for r in dup)
    c = cov(res, "Payment_report")
    assert c["archived"] == 1 and c["review"] == 2 and c["accounted_rows"] == c["rows"] == 3


def test_duplicate_receipt_among_successes(tmp_path):
    rows = [pay_row(1, trace="TRC-777"), pay_row(2, trace=" TRC-777"), pay_row(3, trace="TRC-777", status="reject"),
            pay_row(4, trace="TRC-888"), pay_row(5, trace=""), pay_row(6, trace="")]
    res = convert(tmp_path, rows)
    dup = [r for r in review(res) if r["code"] == "DUPLICATE_RECEIPT"]
    assert len(dup) == 1
    assert dup[0]["severity"] == "blocker" and dup[0]["data"]["payment_ids"] == ["1", "2"]
    assert "TRC-777" not in str(dup[0])
    assert "DUPLICATE_RECEIPT" not in blockers(res)  # history only: it cannot move money
    shared = {r["payment_id"]: r["receipt_trace"]["shared_with_other_successful_payments"] for r in recs(res)}
    assert shared == {"1": True, "2": True, "3": False, "4": False, "5": False, "6": False}


def test_customer_relations(tmp_path):
    rows = [pay_row(1, uid="7100000001"), pay_row(2, uid="7100000003"), pay_row(3, uid="5000000009"),
            pay_row(4, uid=""), pay_row(5, uid=None), pay_row(6, uid="0123")]
    res = convert(tmp_path, rows)
    rel = {r["payment_id"]: (r["customer"]["relation"], r["customer"]["telegram_user_id"]) for r in recs(res)}
    assert rel == {"1": ("CUSTOMER_IMPORTED", "7100000001"), "2": ("CUSTOMER_IN_REVIEW", "7100000003"),
                   "3": ("CUSTOMER_NOT_IN_BACKUP", "5000000009"), "4": ("MISSING", None), "5": ("MISSING", None),
                   "6": ("CUSTOMER_IN_REVIEW", None)}
    assert codes(res)["PAYMENT_CUSTOMER_UNKNOWN"] == 3
    orphans = res.report["reconciliation"]["payments"]["orphans"]
    assert orphans == {"customer_not_in_backup": 1, "customer_missing": 2}


def test_order_relation_never_claims_an_invoice(tmp_path):
    inv = DDL_INVOICE + ("INSERT INTO `invoice` VALUES ('1234abcd','7100000001','u1','x','1700000000','p','1000',"
                         "'10','30','active','syn-a','p001','0','0','',NULL,NULL);\n")
    rows = [pay_row(1, oid="0a1b2c3d4e"), pay_row(2, oid=""), pay_row(3, oid="order-77"), pay_row(4, oid="1234abcd"),
            pay_row(5, oid="0a1b2c3d4e")]
    res = convert(tmp_path, rows, extra=inv)
    o = {r["payment_id"]: (r["order"]["relation"], r["order"]["relation_proven"]) for r in recs(res)}
    assert o == {"1": ("PAYMENT_REFERENCE", True), "2": ("EMPTY", True), "3": ("NOT_FOUND", False),
                 "4": ("TEXT_EQUALS_INVOICE_KEY_UNPROVEN", False), "5": ("PAYMENT_REFERENCE", True)}
    c = codes(res)
    assert c["PAYMENT_ORDER_RELATION_UNPROVEN"] == 1 and c["PAYMENT_ORDER_REF_DUPLICATED"] == 1
    assert recs(res)[0]["order"]["order_ref_shared_with_other_rows"] is True


def test_purpose_only_from_evidenced_column(tmp_path):
    ddl = payments.DDL.replace("`receipt_trace` varchar(255) DEFAULT NULL,",
                               "`receipt_trace` varchar(255) DEFAULT NULL,\n  `id_invoice` varchar(400) DEFAULT NULL,"
                               "\n  `bottype` varchar(300) DEFAULT NULL,")
    tok = "123456789:" + "A" * 35
    sql = USER_DDL + USERS + ddl + (
        "INSERT INTO `Payment_report` VALUES "
        f"(1,'7100000001','0a1b2c3d4e','2025/01/02 10:00:00',NULL,'1000',NULL,'zarinpal','paid',NULL,NULL,'0|0','{tok}'),"
        "(2,'7100000001','0a1b2c3d4f','2025/01/02 10:00:00',NULL,'1000',NULL,'zarinpal','paid',NULL,NULL,"
        "'getconfigafterpay|syn1',NULL),"
        "(3,'7100000001','0a1b2c3d40','2025/01/02 10:00:00',NULL,'1000',NULL,'add balance by admin','paid',NULL,NULL,"
        "NULL,NULL);\n")
    res = run(tmp_path, _zip_sql(tmp_path, sql))
    p = {r["payment_id"]: r["purpose"]["normalized"] for r in recs(res)}
    assert p == {"1": "WALLET_TOP_UP_OR_ADMIN_ADJUSTMENT", "2": "BUY_SERVICE_AFTER_PAYMENT",
                 "3": "WALLET_TOP_UP_OR_ADMIN_ADJUSTMENT"}
    assert "bottype" in cov(res, "Payment_report")["dropped_secret_columns"]
    for f in (res.stage_dir / "records").rglob("*.jsonl"):
        assert tok not in f.read_text(encoding="utf-8")


def test_purpose_is_not_in_source_for_deployed_columns(healthy):
    res, _ = healthy
    assert {r["purpose"]["normalized"] for r in recs(res)} == {"NOT_IN_SOURCE"}


# ---- amounts, dates, Unicode ----------------------------------------------------------------

def test_amounts_are_never_guessed(tmp_path):
    rows = [pay_row(1, price="150,000"), pay_row(2, price="۱۰۰۰"), pay_row(3, price="12.5"), pay_row(4, price=None),
            pay_row(5, price=" 2000 "), pay_row(6, price="-50", status="reject")]
    res = convert(tmp_path, rows)
    a = {r["payment_id"]: (r["amount"]["parse_note"], r["amount"]["amount_minor"]) for r in recs(res)}
    assert a == {"1": ("NOT_A_NUMBER", None), "2": ("NOT_A_NUMBER", None), "3": ("NOT_A_NUMBER", None),
                 "4": ("ABSENT", None), "5": ("OK", "2000"), "6": ("NOT_A_NUMBER", None)}
    assert codes(res)["PAYMENT_AMOUNT_UNREADABLE"] == 4  # successful rows only
    assert res.report["reconciliation"]["payments"]["by_outcome"]["SUCCEEDED"]["unparsed_amounts"] == 4


def test_unicode_text_and_inconsistent_dates(tmp_path):
    rows = [pay_row(1, time="2025/03/04 10:00:00", dec="پرداخت از کارت 4000 0000 0000 0002 انجام شد"),
            pay_row(2, time="2025-03-04 10:00:00"), pay_row(3, time="1403/12/14 10:00:00"),
            pay_row(4, time="1741082400"), pay_row(5, time="2025/02/30 10:00:00"), pay_row(6, time=""),
            pay_row(7, time="۲۰۲۵/۰۳/۰۴ ۱۰:۰۰:۰۰"), pay_row(8, time="1999/01/01 00:00:00")]
    res = convert(tmp_path, rows)
    t = {r["payment_id"]: r["times"]["created"] for r in recs(res)}
    assert t["1"]["local"] == "2025-03-04T10:00:00" and t["1"]["unix"] is None
    assert t["1"]["note"] == "OK_LOCAL_TIME_ZONE_UNPROVEN"
    assert t["2"]["note"] == "FORMAT_UNKNOWN"  # not an evidenced Payment_report format
    assert t["3"]["note"] == "FORMAT_UNKNOWN"  # Jalali-looking: calendar not evidenced
    assert t["4"] == {"raw": "1741082400", "format": "UNIX_SECONDS", "local": None, "unix": 1741082400,
                      "note": "OK_UNIX_SECONDS"}
    assert t["5"]["note"] == "INVALID_DATE" and t["6"]["note"] == "EMPTY"
    assert t["7"]["note"] == "FORMAT_UNKNOWN" and t["7"]["raw"] == "۲۰۲۵/۰۳/۰۴ ۱۰:۰۰:۰۰"
    assert t["8"]["note"] == "OUT_OF_RANGE"
    note = recs(res)[0]["rejection_note"]["text"]
    assert note.startswith("پرداخت از کارت") and "4000 0000 0000 0002" not in note and "****0002" in note
    assert codes(res)["PAYMENT_TIME_FORMAT_UNKNOWN"] == 1


# ---- confidentiality, bounds, determinism ----------------------------------------------------

def test_receipt_trace_and_free_text_never_reach_reports(tmp_path):
    rows = [pay_row(1, trace="RCPT-SECRET-XYZ123", dec="SENSITIVE-NOTE-TEXT"),
            pay_row(2, trace="RCPT-SECRET-XYZ123", dec="SENSITIVE-NOTE-TEXT", uid="5000000001"),
            pay_row(3, status="weird-status", price="1,0")]
    res = convert(tmp_path, rows)
    assert recs(res)[0]["receipt_trace"]["raw"] == "RCPT-SECRET-XYZ123"  # kept inside the package
    for f in [res.report_path, *(res.stage_dir / "reports").rglob("*")]:
        if f.is_file():
            text = f.read_text(encoding="utf-8")
            assert "RCPT-SECRET-XYZ123" not in text, f
            assert "SENSITIVE-NOTE-TEXT" not in text, f


def test_record_level_reviews_are_bounded(tmp_path):
    rows = [pay_row(i, uid=str(5000000000 + i)) for i in range(1, 231)]
    res = convert(tmp_path, rows)
    unk = [r for r in review(res) if r["code"] == "PAYMENT_CUSTOMER_UNKNOWN"]
    assert len(unk) == payments.REVIEW_LIMIT_PER_CODE + 1
    assert unk[-1]["data"] == {"aggregated": True, "total_rows": 230, "listed_individually": 200, "not_listed": 30}
    assert len(recs(res)) == 230


def test_rerun_is_byte_identical(tmp_path):
    zp, _ = make_backup(tmp_path, users=120)
    a = run(tmp_path, zp, ws_name="a")
    b = run(tmp_path, zp, ws_name="b")
    for rel in ("records/payments.jsonl", "reports/review-queue.jsonl"):
        assert (a.stage_dir / rel).read_bytes() == (b.stage_dir / rel).read_bytes(), rel
    assert a.report["reconciliation"]["payments"] == b.report["reconciliation"]["payments"]


def test_parse_time_helper():
    allow = frozenset({payments.FMT_PHP_SLASH})
    assert payments.parse_time(None, allow)["note"] == "ABSENT"
    assert payments.parse_time("1741082400", allow)["note"] == "FORMAT_UNKNOWN"
    assert payments.parse_time("2025/01/01 00:00:00", allow)["local"] == "2025-01-01T00:00:00"

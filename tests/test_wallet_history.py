"""wallet_transaction → read-only wallet history + snapshot check (synthetic data only)."""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import pytest

from conftest import make_backup, read_jsonl, run
from mirza2nexa.modules import payments, wallet_history
from mirza2nexa.synth import SynthSpec, sql_str, write_dump

USER_DDL = ("CREATE TABLE `user` (`id` varchar(500) NOT NULL, `limit_usertest` int, `Balance` varchar(64), "
            "`User_Status` varchar(50), PRIMARY KEY (`id`));\n")
USERS = ("INSERT INTO `user` VALUES ('7100000001',1,'5000','Active'),('7100000002',1,'0','Active'),"
         "('7100000003',1,'-700','Active'),('7100000004',1,'12.5','Active'),('7100000005',1,'300','Active');\n")


def _zip_sql(tmp: Path, sql: str, name: str = "x.zip") -> Path:
    p = tmp / name
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("backup.sql", sql)
    return p


def tx(tid, uid="7100000001", typ="deposit", amount="1000", before="0", after="1000", ref=None, src="bot",
       reason=None, at="2025-06-01 10:00:00", uname=None) -> str:
    def n(v):
        return "NULL" if v is None else (v if re.fullmatch(r"-?[0-9]+", v) else sql_str(v))
    return (f"({tid},{sql_str(uid)},{sql_str(uname)},NULL,{sql_str(typ)},{n(amount)},{n(before)},{n(after)},"
            f"{sql_str(ref)},{sql_str(src)},{sql_str(reason)},{sql_str(at)})")


def convert(tmp: Path, rows: list[str], *, extra: str = "", name: str = "x.zip", ddl: str | None = None):
    sql = USER_DDL + USERS + extra + (ddl or wallet_history.DDL)
    if rows:
        sql += "INSERT INTO `wallet_transaction` VALUES " + ",".join(rows) + ";\n"
    return run(tmp, _zip_sql(tmp, sql, name), ws_name="ws-" + name)


def txs(res) -> list[dict]:
    return read_jsonl(res.stage_dir / "records" / "wallet_history.jsonl")


def checks(res) -> dict[str, dict]:
    return {c["customer"]["source_user_id"]: c
            for c in read_jsonl(res.stage_dir / "records" / "wallet_history_checks.jsonl")}


def review(res) -> list[dict]:
    return [r for r in read_jsonl(res.stage_dir / "reports" / "review-queue.jsonl")
            if r["source_ref"]["table"] == "wallet_transaction"]


def codes(res) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in review(res):
        out[r["code"]] = out.get(r["code"], 0) + 1
    return out


def blockers(res) -> set[str]:
    return {b["code"] for b in res.report["blockers"]}


def recon(res) -> dict:
    return res.report["reconciliation"]["wallet_history"]


@pytest.fixture(scope="module")
def healthy(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("wh-healthy")
    zp, exp = make_backup(tmp, users=400)
    return run(tmp, zp), exp


# ---- healthy synthetic backup -----------------------------------------------------------

def test_healthy_backup_stays_ready(healthy):
    res, exp = healthy
    assert res.report["readiness"] == "ready", res.report["blockers"]
    rows = txs(res)
    assert len(rows) == exp["wallet_transactions"]
    r = recon(res)
    assert r["balanced"] and r["source_rows"] == r["archived"]
    for table in ("wallet_transaction", "Payment_report"):
        c = next(c for c in res.report["coverage"] if c["table"] == table)
        assert c["status"] == "archived" and c["accounted_rows"] == c["rows"] > 0, table
    v = r["customers"]["verdicts"]
    assert v["MATCHES_SNAPSHOT"] >= 5 and v["DIFFERS_FROM_SNAPSHOT"] == 1 and v["NO_SNAPSHOT"] == 1
    assert r["duplicate_suspects"] == {"groups": 1, "rows": 2}
    assert r["inconsistent_rows"] == 1 and r["chain_gaps"] >= 1
    assert r["ref_id"]["relation"] == "UNPROVEN"
    warn = {w["code"]: w["count"] for w in res.report["warnings"]}
    assert warn["WALLET_HISTORY_DIFFERS_FROM_SNAPSHOT"] == 1


def test_records_are_history_only(healthy):
    res, _ = healthy
    keys = set()
    for t in txs(res):
        assert t["record_type"] == "legacy_wallet_transaction" and t["schema"] == "mirza.wallet_transaction.v1"
        assert t["idempotency_key"] == f"legacy:wallet-tx:{t['transaction_id']}"
        assert t["affects_wallet"] is False and t["applied_to_balance"] is False
        assert t["type"]["normalized"] == "UNKNOWN" and t["ref_id"]["ref_relation"] == "UNPROVEN"
        keys.add(t["idempotency_key"])
    for c in checks(res).values():
        assert c["idempotency_key"].startswith("legacy:wallet-history-check:")
        assert c["affects_wallet"] is False and c["applied_to_balance"] is False
        keys.add(c["idempotency_key"])
    assert len(keys) == len(txs(res)) + len(checks(res))


# ---- the snapshot is the only opening figure ------------------------------------------------

def _strip_wallet_table(sql: str) -> str:
    start = sql.index("DROP TABLE IF EXISTS `wallet_transaction`")
    end = sql.index("UNLOCK TABLES;\n", start) + len("UNLOCK TABLES;\n")
    return sql[:start] + sql[end:]


def test_history_never_changes_opening_balances(tmp_path):
    buf = io.StringIO()
    write_dump(buf, SynthSpec(users=300, seed=5))
    with_tx = buf.getvalue()
    without = _strip_wallet_table(with_tx)
    assert "wallet_transaction" in with_tx and "wallet_transaction" not in without
    a = run(tmp_path, _zip_sql(tmp_path, with_tx, "a.zip"), ws_name="a")
    b = run(tmp_path, _zip_sql(tmp_path, without, "b.zip"), ws_name="b")
    assert txs(a) and not txs(b)
    fa, fb = a.report["source_fingerprint"], b.report["source_fingerprint"]
    for rel in ("records/wallet_opening_balances.jsonl", "records/legacy_debts.jsonl", "records/customers.jsonl"):
        ba = (a.stage_dir / rel).read_bytes().replace(fa.encode(), b"<FP>")
        bb = (b.stage_dir / rel).read_bytes().replace(fb.encode(), b"<FP>")
        assert ba == bb, rel  # byte-identical apart from the source fingerprint itself
    assert a.report["reconciliation"]["wallet"] == b.report["reconciliation"]["wallet"]


# ---- findings --------------------------------------------------------------------------

def test_duplicate_transaction_id_blocks(tmp_path):
    res = convert(tmp_path, [tx(1), tx(1, amount="5", after="5"), tx(2, before="1000", after="2000")])
    assert "WALLET_TX_SOURCE_KEY_DUPLICATED" in blockers(res)
    assert [t["transaction_id"] for t in txs(res)] == ["2"]
    dup = [r for r in review(res) if r["code"] == "SOURCE_KEY_DUPLICATED"]
    assert len(dup) == 2 and all(r["severity"] == "blocker" for r in dup)
    c = next(c for c in res.report["coverage"] if c["table"] == "wallet_transaction")
    assert c["archived"] == 1 and c["review"] == 2 and c["accounted_rows"] == 3


def test_history_vs_snapshot_difference_is_a_warning(tmp_path):
    rows = [tx(1, before="0", after="4000", amount="4000"),               # 7100000001 snapshot 5000 → differs
            tx(2, uid="7100000005", before="0", after="300", amount="300"),  # matches
            tx(3, uid="7100000003", before="0", after="-700", amount="700", typ="purchase"),  # matches a debt
            tx(4, uid="7100000004", before="0", after="12", amount="12"),    # snapshot '12.5' unreadable
            tx(5, uid="7100000002", before="0", after="x", amount="5")]      # last after unparseable
    res = convert(tmp_path, rows)
    c = checks(res)
    assert c["7100000001"]["verdict"] == "DIFFERS_FROM_SNAPSHOT"
    assert c["7100000001"]["delta_snapshot_minus_history_minor"] == "1000"
    assert c["7100000001"]["snapshot_balance"]["amount_minor"] == "5000"
    assert c["7100000005"]["verdict"] == "MATCHES_SNAPSHOT"
    assert c["7100000003"]["verdict"] == "MATCHES_SNAPSHOT"
    assert c["7100000004"]["verdict"] == "NO_SNAPSHOT"
    assert c["7100000004"]["snapshot_balance"]["note"] == "SNAPSHOT_UNREADABLE"
    assert c["7100000002"]["verdict"] == "HISTORY_UNPARSEABLE"
    assert codes(res)["WALLET_HISTORY_DIFFERS_FROM_SNAPSHOT"] == 1
    assert not {b for b in blockers(res) if b.startswith("WALLET")}
    r = recon(res)
    assert r["total_delta_snapshot_minus_history_minor"] == "1000"
    assert r["customers"]["with_history"] == 5
    # the snapshot is untouched: opening credits come from user.Balance only
    opening = read_jsonl(res.stage_dir / "records" / "wallet_opening_balances.jsonl")
    assert {o["telegram_user_id"]: o["amount_minor"] for o in opening} == {"7100000001": "5000", "7100000005": "300"}


def test_arithmetic_checks_and_sign_conventions(tmp_path):
    rows = [tx(1, amount="1000", before="0", after="1000"),            # credit, +amount
            tx(2, amount="-400", before="1000", after="600", at="2025-06-01 11:00:00"),   # debit, signed amount
            tx(3, amount="100", before="600", after="500", at="2025-06-01 12:00:00"),     # debit, magnitude
            tx(4, amount="100", before="500", after="550", at="2025-06-01 13:00:00"),     # inconsistent
            tx(5, amount="1.5", before="550", after="551", at="2025-06-01 14:00:00"),     # unparseable
            tx(6, amount="0", before="551", after="551", at="2025-06-01 15:00:00")]
    res = convert(tmp_path, rows)
    a = {t["transaction_id"]: (t["arithmetic"], t["amount_convention_observed"]) for t in txs(res)}
    assert a == {"1": ("ARITHMETIC_CREDIT", "AFTER_MINUS_BEFORE_EQUALS_PLUS_AMOUNT"),
                 "2": ("ARITHMETIC_DEBIT", "AFTER_MINUS_BEFORE_EQUALS_PLUS_AMOUNT"),
                 "3": ("ARITHMETIC_DEBIT", "AFTER_MINUS_BEFORE_EQUALS_MINUS_AMOUNT"),
                 "4": ("INCONSISTENT", None), "5": ("UNPARSEABLE", None), "6": ("ARITHMETIC_NO_CHANGE", None)}
    r = recon(res)
    assert r["credits_observed"] == {"count": 1, "amount_minor": "1000"}
    assert r["debits_observed"] == {"count": 2, "amount_minor": "500"}
    assert r["inconsistent_rows"] == 1 and r["unparseable_rows"] == 1
    assert codes(res)["WALLET_TX_ARITHMETIC_INCONSISTENT"] == 1
    assert {t["chain"] for t in txs(res)} == {"FIRST", "CHAIN_OK"}


def test_chain_is_ordered_by_time_then_id(tmp_path):
    rows = [tx(10, before="0", after="1000", at="2025-06-01 10:00:00"),
            tx(2, amount="500", before="1000", after="1500", at="2025-06-02 10:00:00"),   # later in time, lower id
            tx(3, amount="100", before="9999", after="10099", at="2025-06-03 10:00:00")]  # gap
    res = convert(tmp_path, rows)
    chain = {t["transaction_id"]: t["chain"] for t in txs(res)}
    assert chain == {"10": "FIRST", "2": "CHAIN_OK", "3": "CHAIN_GAP"}
    assert [t["transaction_id"] for t in txs(res)] == ["10", "2", "3"]
    assert codes(res)["WALLET_HISTORY_CHAIN_GAP"] == 1
    assert checks(res)["7100000001"]["chain_gaps"] == 1


def test_duplicate_suspects_and_unknown_types(tmp_path):
    rows = [tx(1, typ="gift", ref="77"), tx(2, typ="gift", ref="77"), tx(3, typ=None, ref="77"),
            tx(4, uid="5000000001", typ="مرموز", reason="هدیه ویژه ✨")]
    res = convert(tmp_path, rows)
    assert recon(res)["duplicate_suspects"] == {"groups": 1, "rows": 2}
    assert {t["transaction_id"]: t["duplicate_suspect"] for t in txs(res)} == {
        "1": True, "2": True, "3": False, "4": False}
    c = codes(res)
    assert c["WALLET_TX_DUPLICATE_SUSPECT"] == 1 and c["WALLET_TX_TYPE_UNKNOWN"] == 1
    assert c["WALLET_TX_CUSTOMER_UNKNOWN"] == 1
    t4 = next(t for t in txs(res) if t["transaction_id"] == "4")
    assert t4["type"] == {"raw": "مرموز", "normalized": "UNKNOWN"} and t4["reason"] == "هدیه ویژه ✨"
    assert t4["customer"]["relation"] == "CUSTOMER_NOT_IN_BACKUP"
    assert "WALLET_TX_SOURCE_KEY_DUPLICATED" not in blockers(res)


def test_ref_id_matches_are_statistics_only(tmp_path):
    pay = payments.DDL + ("INSERT INTO `Payment_report` VALUES (77,'7100000001','0a1b2c3d4e','2025/01/01 10:00:00',"
                          "NULL,'1000',NULL,'zarinpal','paid',NULL,NULL);\n")
    rows = [tx(1, ref="77"), tx(2, ref="0a1b2c3d4e", before="1000", after="2000", at="2025-06-02 10:00:00"),
            tx(3, ref="nothing", before="2000", after="3000", at="2025-06-03 10:00:00")]
    res = convert(tmp_path, rows, extra=pay)
    ref = recon(res)["ref_id"]
    assert ref["relation"] == "UNPROVEN" and ref["rows_with_ref_id"] == 3
    assert ref["statistic_only_equal_to_a_payment_report_id"] == 1
    assert ref["statistic_only_equal_to_a_payment_report_id_order"] == 1
    assert all(t["ref_id"]["ref_relation"] == "UNPROVEN" for t in txs(res))


def test_unicode_and_inconsistent_dates(tmp_path):
    ddl = wallet_history.DDL.replace("`created_at` datetime", "`created_at` varchar(64)")
    rows = [tx(1, at="2025/06/01 10:00:00"), tx(2, at="2025-06-01 09:00:00", before="1000", after="2000"),
            tx(3, at="1404/03/11 10:00:00", before="2000", after="3000"), tx(4, at="۲۰۲۵-۰۶-۰۱ ۱۰:۰۰:۰۰"),
            tx(5, at="1748770000", before="3000", after="4000"), tx(6, at=None)]
    res = convert(tmp_path, rows, ddl=ddl)
    t = {x["transaction_id"]: x["created_at"] for x in txs(res)}
    assert t["1"]["format"] == "PHP_Y/m/d_H:i:s" and t["1"]["format_evidence"] == "INFERRED"
    assert t["2"]["local"] == "2025-06-01T09:00:00" and t["2"]["unix"] is None
    assert t["3"]["note"] == "FORMAT_UNKNOWN" and t["4"]["note"] == "FORMAT_UNKNOWN"
    assert t["5"]["unix"] == 1748770000 and t["6"]["note"] == "ABSENT"
    r = recon(res)
    assert r["created_at"]["ddl_type"] == "varchar(64)" and r["created_at"]["customers_with_mixed_formats"] == 1
    assert codes(res)["WALLET_TX_TIME_FORMAT_UNKNOWN"] == 1


def test_datetime_ddl_is_evidence_for_sql_format(healthy):
    res, _ = healthy
    ev = {t["created_at"]["format_evidence"] for t in txs(res) if t["created_at"]["format"]}
    assert ev == {"DDL_TYPE"}


def test_no_free_text_in_reports(tmp_path):
    rows = [tx(1, reason="SECRET-REASON-TEXT", uname="SECRET_UNAME_77", ref="REF-SECRET-1"),
            tx(2, reason="SECRET-REASON-TEXT", uname="SECRET_UNAME_77", ref="REF-SECRET-1", typ="xyz-type")]
    res = convert(tmp_path, rows)
    assert txs(res)[0]["reason"] == "SECRET-REASON-TEXT"  # kept inside the encrypted package
    for f in [res.report_path, *(res.stage_dir / "reports").rglob("*")]:
        if f.is_file():
            text = f.read_text(encoding="utf-8")
            for s in ("SECRET-REASON-TEXT", "SECRET_UNAME_77", "REF-SECRET-1"):
                assert s not in text, (f, s)


def test_rerun_is_byte_identical(tmp_path):
    zp, _ = make_backup(tmp_path, users=150)
    a = run(tmp_path, zp, ws_name="a")
    b = run(tmp_path, zp, ws_name="b")
    for rel in ("records/wallet_history.jsonl", "records/wallet_history_checks.jsonl", "records/payments.jsonl",
                "reports/review-queue.jsonl"):
        assert (a.stage_dir / rel).read_bytes() == (b.stage_dir / rel).read_bytes(), rel
    assert recon(a) == recon(b)


def test_without_anomalies_every_chain_matches(tmp_path):
    zp, exp = make_backup(tmp_path, users=300, anomalies=False)
    res = run(tmp_path, zp)
    r = recon(res)
    assert res.report["readiness"] == "ready", res.report["blockers"]
    assert r["customers"]["verdicts"] == {"MATCHES_SNAPSHOT": r["customers"]["with_history"]}
    assert r["chain_gaps"] == 0 and r["inconsistent_rows"] == 0 and r["duplicate_suspects"]["rows"] == 0
    assert len(txs(res)) == exp["wallet_transactions"]

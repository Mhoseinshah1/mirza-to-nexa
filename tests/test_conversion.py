"""Conversion engine + financial invariants on synthetic backups (never real data)."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from conftest import make_backup, read_jsonl, run
from mirza2nexa.errors import (
    Cancelled,
    DiskSpaceError,
    PasswordRequired,
    SchemaUnknown,
    SqlParseError,
    WrongPassword,
)
from mirza2nexa.nxpkg import PackageReader
from mirza2nexa.progress import CancelToken


def records(res, name: str) -> list[dict]:
    return read_jsonl(res.stage_dir / "records" / f"{name}.jsonl")


def review(res) -> list[dict]:
    return read_jsonl(res.stage_dir / "reports" / "review-queue.jsonl")


def codes(res) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in review(res):
        out[r["code"]] = out.get(r["code"], 0) + 1
    return out


def blockers(res) -> set[str]:
    return {b["code"] for b in res.report["blockers"]}


@pytest.fixture(scope="module")
def healthy(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("healthy")
    zp, exp = make_backup(tmp, users=400, password="pw-Ab12345")
    res = run(tmp, zp, password="pw-Ab12345")
    return res, exp, tmp


# ---- healthy backup ------------------------------------------------------------------

def test_healthy_backup_is_ready_and_reconciles(healthy):
    res, exp, _ = healthy
    assert res.report["readiness"] == "ready", res.report["blockers"]
    w = res.report["reconciliation"]["wallet"]
    assert w["balanced"] and w["independent_recount_matches"]
    assert w["opening_credits"] == {"count": exp["positive"], "amount_minor": str(exp["positive_sum"])}
    assert w["legacy_debts"] == {"count": exp["negative"], "amount_minor": str(exp["negative_sum"])}
    assert w["source_users"] == exp["users"]


def test_opening_balances_follow_nexa_contract(healthy):
    res, exp, _ = healthy
    credits = records(res, "wallet_opening_balances")
    assert len(credits) == exp["positive"]
    for c in credits:
        assert c["idempotency_key"] == f"legacy:opening:{c['telegram_user_id']}"
        assert c["reason"] == "MIGRATION_OPENING_BALANCE"
        assert c["currency"] == "IRT"
        assert int(c["amount_minor"]) > 0
        assert c["amount_minor"] == c["source_amount_raw"]  # never re-scaled


def test_negative_balances_become_unsettled_debts(healthy):
    res, exp, _ = healthy
    debts = records(res, "legacy_debts")
    assert len(debts) == exp["negative"]
    for d in debts:
        assert d["state"] == "PENDING_REVIEW" and d["auto_settle"] is False
        assert int(d["amount_minor"]) > 0
        assert d["source_amount_raw"] == "-" + d["amount_minor"]
    # a customer never has both a credit and a debt
    tg_credit = {c["telegram_user_id"] for c in records(res, "wallet_opening_balances")}
    assert not tg_credit & {d["legacy_user_id"] for d in debts}


def test_invoices_are_history_only(healthy):
    res, exp, _ = healthy
    inv = records(res, "invoices")
    assert len(inv) == exp["invoices"]
    assert all(r["creates_payment"] is False and r["creates_order"] is False for r in inv)
    assert len({r["idempotency_key"] for r in inv}) == len(inv)


def test_services_never_provisioned(healthy):
    res, _, _ = healthy
    for s in records(res, "services"):
        assert s["provision"] is False and s["requires_live_panel_check"] is True
        assert s["owner_telegram_user_id"] is not None
        assert s["adoption_state"] == "PENDING_PANEL_MAPPING"


def test_review_queue_covers_anomalies(healthy):
    res, _, _ = healthy
    c = codes(res)
    assert c["INVALID_IDENTITY"] == 1            # '0123'
    assert c["STATUS_UNKNOWN"] == 1              # 'frozen'
    assert c["BALANCE_UNREADABLE"] == 1          # '12.5' is never rounded
    assert c["CODE_DUPLICATED"] == 2             # both rows of product code p001
    assert c["ORPHAN"] == 1                      # service without owner
    assert c["INVOICE_WITHOUT_CUSTOMER"] == 1    # invoice without customer
    assert c["AMBIGUOUS_OWNERSHIP"] == 2         # two owners on one panel account
    assert c["NO_PANEL"] == 1
    assert c["UNSUPPORTED_SHAPE"] == 1           # time_unit 'month' (not evidenced)


def test_secret_columns_never_exported(healthy):
    res, _, _ = healthy
    blob = b"".join(p.read_bytes() for p in (res.stage_dir).rglob("*") if p.is_file())
    assert b"never.read" not in blob and b"subscription_url" not in blob
    assert b"never-read" not in blob  # product.inbounds
    assert b"00000000-0000-0000-0000-000000000000" not in blob  # invoice.uuid


def test_package_roundtrip_and_tamper_detection(healthy, tmp_path):
    res, _, _ = healthy
    reader = PackageReader(res.package_path, key=res.key_text, workdir=tmp_path / "v")
    rep = reader.verify()
    assert rep.import_id == res.report["import_id"]
    assert rep.manifest["readiness"] == "ready"
    assert rep.manifest["compatibility"]["verified_by_importer"] is False
    assert sum(1 for _ in reader.iter_jsonl("records/customers.jsonl")) == len(records(res, "customers"))
    # tampered output
    bad = tmp_path / "bad.nxpkg"
    data = bytearray(res.package_path.read_bytes())
    data[len(data) // 2] ^= 0x01
    bad.write_bytes(bytes(data))
    with pytest.raises(Exception) as ei:
        PackageReader(bad, key=res.key_text, workdir=tmp_path / "v2").verify()
    assert getattr(ei.value, "code", "").startswith("package.")


def test_key_never_inside_package(healthy):
    res, _, _ = healthy
    raw = res.package_path.read_bytes()
    assert res.key_text.encode() not in raw
    assert res.key_text.split(":", 1)[1][:20].encode() not in raw


# ---- determinism / idempotency ---------------------------------------------------------

def test_reimport_same_backup_is_deterministic(tmp_path):
    zp, _ = make_backup(tmp_path, users=150)
    a = run(tmp_path, zp, ws_name="a")
    b = run(tmp_path, zp, ws_name="b")
    assert a.report["import_id"] == b.report["import_id"]
    assert a.report["source_fingerprint"] == b.report["source_fingerprint"]
    for rel in ("records/customers.jsonl", "records/wallet_opening_balances.jsonl", "records/legacy_debts.jsonl",
                "records/invoices.jsonl", "records/services.jsonl", "reports/review-queue.jsonl"):
        assert (a.stage_dir / rel).read_bytes() == (b.stage_dir / rel).read_bytes(), rel
    # repeated import cannot double credit: one opening key per customer
    keys = [r["idempotency_key"] for r in records(a, "wallet_opening_balances")]
    assert len(keys) == len(set(keys))


def test_different_physical_order_same_fingerprint(tmp_path):
    from mirza2nexa.synth import SynthSpec, write_dump

    buf = io.StringIO()
    write_dump(buf, SynthSpec(users=60, anomalies=False))
    sql = buf.getvalue()
    lines = sql.split("\n")
    user_inserts = [i for i, ln in enumerate(lines) if ln.startswith("INSERT INTO `user`")]
    assert user_inserts
    # Re-dump the same users in reverse row order inside the INSERT.
    i = user_inserts[0]
    head, values = lines[i].split(" VALUES ", 1)
    rows = values.rstrip(";")[1:-1].split("),(")
    lines[i] = head + " VALUES (" + "),(".join(reversed(rows)) + ");"
    for name, text in (("a.zip", sql), ("b.zip", "\n".join(lines))):
        with zipfile.ZipFile(tmp_path / name, "w") as zf:
            zf.writestr("backup.sql", text)
    a = run(tmp_path, tmp_path / "a.zip", ws_name="a")
    b = run(tmp_path, tmp_path / "b.zip", ws_name="b")
    assert a.report["source_fingerprint"] == b.report["source_fingerprint"]
    assert (a.stage_dir / "records/customers.jsonl").read_bytes() == (b.stage_dir / "records/customers.jsonl").read_bytes()


def test_compare_two_backups_blocks_changed_balances(tmp_path):
    old, _ = make_backup(tmp_path, users=120, name="old.zip")
    new, _ = make_backup(tmp_path, users=125, shift=10, name="new.zip")
    a = run(tmp_path, old, ws_name="a")
    b = run(tmp_path, new, ws_name="b", previous_records_dir=a.stage_dir / "records",
            previous_import_id=a.report["import_id"])
    d = b.report["diff"]
    assert d["previous_import_id"] == a.report["import_id"]
    assert d["added"]["customers"] == 5
    assert d["changed"]["wallet_balances"] == 10
    assert d["balance_delta_total"] == str(10 * 1000)
    assert "BALANCE_CHANGED_SINCE_PREVIOUS" in blockers(b)
    assert b.report["readiness"] == "blocked"
    assert codes(b)["BALANCE_CHANGED_SINCE_PREVIOUS"] == 10


# ---- finance blockers ------------------------------------------------------------------

def test_money_unit_unconfirmed_blocks(tmp_path):
    zp, _ = make_backup(tmp_path, users=40)
    res = run(tmp_path, zp, money_unit=None)
    assert "MONEY_UNIT_UNCONFIRMED" in blockers(res)
    assert all(c["currency"] is None for c in records(res, "wallet_opening_balances"))


def test_rial_is_never_silently_converted(tmp_path):
    zp, _ = make_backup(tmp_path, users=40)
    res = run(tmp_path, zp, money_unit="rial")
    assert "MONEY_UNIT_NOT_IRT" in blockers(res)
    for c in records(res, "wallet_opening_balances"):
        assert c["currency"] == "IRR" and c["amount_minor"] == c["source_amount_raw"]


def test_wallet_mismatch_blocks(tmp_path, monkeypatch):
    import mirza2nexa.pipeline as pl

    real = pl._verify_written

    def skewed(stage_dir):
        t = real(stage_dir)
        t["credits_amount"] += 1  # a single Toman lost between engine and file
        return t

    monkeypatch.setattr(pl, "_verify_written", skewed)
    zp, _ = make_backup(tmp_path, users=40)
    res = run(tmp_path, zp)
    assert "FINANCIAL_RECONCILIATION_MISMATCH" in blockers(res)
    assert res.report["readiness"] == "blocked"


def _zip_sql(tmp: Path, sql: str, name: str = "x.zip") -> Path:
    p = tmp / name
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("backup.sql", sql)
    return p


USER_DDL = ("CREATE TABLE `user` (`id` varchar(500) NOT NULL, `limit_usertest` int, `Balance` varchar(64), "
            "`User_Status` varchar(50), PRIMARY KEY (`id`));\n")


def test_duplicate_telegram_id_imports_nothing(tmp_path):
    sql = USER_DDL + ("INSERT INTO `user` VALUES ('7100000001',1,'5000','Active'),('7100000001',1,'9000','Active'),"
                      "('7100000002',1,'100','Active');\n")
    res = run(tmp_path, _zip_sql(tmp_path, sql))
    c = codes(res)
    assert c["DUPLICATE_SOURCE_ID"] == 1
    tgs = [r["telegram_user_id"] for r in records(res, "customers")]
    assert tgs == ["7100000002"]
    w = res.report["reconciliation"]["wallet"]
    assert w["balanced"] and w["held_for_review"]["amount_minor"] == "14000"


def test_invalid_debts_are_never_guessed(tmp_path):
    sql = USER_DDL + ("INSERT INTO `user` VALUES ('7100000001',1,'-12.5','Active'),('7100000002',1,'-1,000','Active'),"
                      "('7100000003',1,'-۵۰۰','Active'),('7100000004',1,'-2000000000000','Active'),"
                      "('7100000005',1,'-700','Active');\n")
    res = run(tmp_path, _zip_sql(tmp_path, sql))
    debts = records(res, "legacy_debts")
    assert [(d["legacy_user_id"], d["amount_minor"]) for d in debts] == [("7100000005", "700")]
    c = codes(res)
    assert c["BALANCE_UNREADABLE"] == 3 and c["BALANCE_OUT_OF_RANGE"] == 1
    assert res.report["reconciliation"]["wallet"]["balanced"]


def test_missing_invoice_and_product_tables_block_entities(tmp_path):
    sql = USER_DDL + "INSERT INTO `user` VALUES ('7100000001',1,'5000','Active');\n"
    res = run(tmp_path, _zip_sql(tmp_path, sql))
    prof = res.report["schema_profile"]
    assert prof["confidence"] == "partial"
    assert prof["entity_blocked"]["services"] == "SOURCE_SCHEMA_MISSING_TABLE"
    assert res.report["readiness"] == "blocked"
    assert len(records(res, "wallet_opening_balances")) == 1


def test_public_revision_invoice_without_required_columns_is_blocked(tmp_path):
    sql = USER_DDL + ("CREATE TABLE `invoice` (`id_invoice` varchar(200), `id_user` varchar(200), `Status` varchar(200));\n"
                      "INSERT INTO `invoice` VALUES ('1234abcd','7100000001','active');\n")
    res = run(tmp_path, _zip_sql(tmp_path, sql))
    assert res.report["schema_profile"]["missing_columns"]["invoice"]
    assert "SOURCE_SCHEMA_MISSING_COLUMN" in blockers(res)
    assert records(res, "services") == []


def test_unknown_schema_version_fails_closed(tmp_path):
    sql = "CREATE TABLE `users` (`uid` int);\nINSERT INTO `users` VALUES (1);\n"
    with pytest.raises(SchemaUnknown):
        run(tmp_path, _zip_sql(tmp_path, sql))


def test_table_names_are_case_sensitive(tmp_path):
    sql = USER_DDL.replace("`user`", "`User`") + "INSERT INTO `User` VALUES ('7100000001',1,'5000','Active');\n"
    with pytest.raises(SchemaUnknown):
        run(tmp_path, _zip_sql(tmp_path, sql))


def test_incompatible_sql_is_rejected(tmp_path):
    sql = USER_DDL + "INSERT INTO `user` VALUES ('7100000001',1,'5000);\n"  # unterminated string
    with pytest.raises((SqlParseError, SchemaUnknown)):
        run(tmp_path, _zip_sql(tmp_path, sql))


def test_column_count_mismatch_is_rejected(tmp_path):
    sql = USER_DDL + "INSERT INTO `user` VALUES ('7100000001',1,'5000');\n"
    with pytest.raises(SqlParseError):
        run(tmp_path, _zip_sql(tmp_path, sql))


# ---- archive-level scenarios through the full pipeline -----------------------------------

def test_encrypted_zip_requires_password(tmp_path):
    zp, _ = make_backup(tmp_path, users=20, password="right-password")
    with pytest.raises(PasswordRequired):
        run(tmp_path, zp, ws_name="a")
    with pytest.raises(WrongPassword):
        run(tmp_path, zp, ws_name="b", password="wrong-password")


def test_cancel_midway_leaves_no_package(tmp_path):
    zp, _ = make_backup(tmp_path, users=3000)
    tok = CancelToken()
    calls = {"n": 0}

    def progress(done, total):
        calls["n"] += 1
        if calls["n"] == 3:
            tok.cancel()

    with pytest.raises(Cancelled):
        run(tmp_path, zp, cancel=tok, progress=progress)
    assert not list((tmp_path / "ws").rglob("*.nxpkg"))


def test_disk_full_is_reported(tmp_path, monkeypatch):
    import shutil

    zp, _ = make_backup(tmp_path, users=50)

    class Usage:
        total, used, free = 10**9, 10**9, 1024

    monkeypatch.setattr(shutil, "disk_usage", lambda _p: Usage)
    with pytest.raises(DiskSpaceError):
        run(tmp_path, zp)


def test_report_contains_no_password_or_key(tmp_path):
    zp, _ = make_backup(tmp_path, users=30, password="TopSecret-Zip-99")
    res = run(tmp_path, zp, password="TopSecret-Zip-99")
    text = json.dumps(res.report)
    assert "TopSecret-Zip-99" not in text
    assert res.key_text not in text

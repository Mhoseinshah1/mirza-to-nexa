"""Phase 3: why a wallet history differs from the snapshot (synthetic data only).

Every case builds its own small dump, so each cause category has an exact expectation.
The opening balance (user.Balance) is never changed by the analysis.
"""

from __future__ import annotations

import calendar
import re
import zipfile
from pathlib import Path

import pytest

from conftest import make_backup, read_jsonl, run
from mirza2nexa.modules import payments, services_ext, wallet_history
from mirza2nexa.synth import DDL_INVOICE, sql_str

USER_DDL = ("CREATE TABLE `user` (`id` varchar(500) NOT NULL, `limit_usertest` int, `Balance` varchar(64), "
            "`User_Status` varchar(50), PRIMARY KEY (`id`));\n")
U1, U2, U3 = "7200000001", "7200000002", "7200000003"
USERS = (f"INSERT INTO `user` VALUES ('{U1}',1,'50000','Active'),('{U2}',1,'1000','Active'),"
         f"('{U3}',1,'-3000','Active');\n")
LAST = "2026-01-15 10:00:00"  # last history row (local, zone unproven)
WHEEL_DDL = ("CREATE TABLE `wheel_list` (`id` int NOT NULL, `id_user` varchar(200) NOT NULL, `time` varchar(200) "
             "NOT NULL, `first_name` varchar(200) NOT NULL, `wheel_code` varchar(200) NOT NULL, `price` varchar(200) "
             "NOT NULL, PRIMARY KEY (`id`));\n")
REAGENT_DDL = ("CREATE TABLE `reagent_report` (`id` int NOT NULL, `user_id` bigint NOT NULL, `get_gift` tinyint(1) "
               "NOT NULL, `time` varchar(50) NOT NULL, `reagent` varchar(30) NOT NULL, PRIMARY KEY (`id`));\n")
REF_KEYS = {"table", "pk", "pk_ref", "role"}
PK_SAFE = re.compile(r"[0-9A-Za-z_-]{1,64}")


def _zip_sql(tmp: Path, sql: str, name: str) -> Path:
    p = tmp / name
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("backup.sql", sql)
    return p


def history(uid: str, end: int, tid: int = 1, at: str = LAST) -> list[str]:
    """Two consistent rows that end at ``end``; the last one at ``at``."""
    def row(i, amount, before, after, when):
        return (f"({i},{sql_str(uid)},NULL,NULL,'deposit',{amount},{before},{after},NULL,'bot',"
                f"{sql_str('شارژ SECRET-REASON')},{sql_str(when)})")
    return [row(tid, 1000, end - 3000, end - 2000, "2026-01-10 09:00:00"), row(tid + 1, 2000, end - 2000, end, at)]


def pay(pid: int, uid: str, price: int, at: str, method: str = "zarinpal", status: str = "paid") -> str:
    return (f"({pid},{sql_str(uid)},{sql_str(f'{pid:010x}')},{sql_str(at)},NULL,{sql_str(str(price))},"
            f"{sql_str('SECRET-DEC-TEXT')},{sql_str(method)},{sql_str(status)},NULL,NULL)")


def inv(key: str, uid: str, price: int, epoch: int, status: str = "active") -> str:
    return (f"({sql_str(key)},{sql_str(uid)},{sql_str('u' + key[-4:])},'x',{sql_str(str(epoch))},'p',"
            f"{sql_str(str(price))},'10','30',{sql_str(status)},'syn-a','p001','0','0','',NULL,NULL)")


def epoch(text: str) -> int:
    return calendar.timegm(tuple(int(x) for x in re.split(r"[-: ]", text)) + (0, 0, 0))


def convert(tmp: Path, wallet: list[str], *, pays: list[str] = (), invoices: list[str] = (), ops: list[str] = (),
            wheel: list[str] = (), reagent: list[str] = (), name: str = "c.zip", wallet_ddl: str | None = None):
    sql = USER_DDL + USERS
    if pays:
        sql += payments.DDL + "INSERT INTO `Payment_report` VALUES " + ",".join(pays) + ";\n"
    if invoices:
        sql += DDL_INVOICE + "INSERT INTO `invoice` VALUES " + ",".join(invoices) + ";\n"
    if ops:
        sql += services_ext.DDL["service_other"] + "INSERT INTO `service_other` VALUES " + ",".join(ops) + ";\n"
    if wheel:
        sql += WHEEL_DDL + "INSERT INTO `wheel_list` VALUES " + ",".join(wheel) + ";\n"
    if reagent:
        sql += REAGENT_DDL + "INSERT INTO `reagent_report` VALUES " + ",".join(reagent) + ";\n"
    sql += wallet_ddl or wallet_history.DDL
    if wallet:
        sql += "INSERT INTO `wallet_transaction` VALUES " + ",".join(wallet) + ";\n"
    return run(tmp, _zip_sql(tmp, sql, name), ws_name="ws-" + name)


def analysis(res) -> dict[str, dict]:
    path = res.stage_dir / "records" / "wallet_difference_analysis.jsonl"
    return {r["customer"]["source_user_id"]: r for r in read_jsonl(path)} if path.exists() else {}


def recon(res) -> dict:
    return res.report["reconciliation"]["wallet_differences"]


def verdict(res, uid: str) -> tuple[str, str, str]:
    r = analysis(res)[uid]
    return r["cause"], r["certainty"], r["proposed_action"]


# ---- PROVEN causes ------------------------------------------------------------------------

def test_unlogged_topups_proven(tmp_path):
    res = convert(tmp_path, history(U1, 50000 - 30000),
                  pays=[pay(1, U1, 10000, "2026/02/01 10:00:00"), pay(2, U1, 20000, "2026/02/03 10:00:00",
                                                                       method="cart to cart"),
                        pay(3, U1, 99000, "2026/02/04 10:00:00", status="expire"),   # not paid: ignored
                        pay(4, U1, 77000, "2025/12/01 10:00:00")])                   # before the history end
    assert verdict(res, U1) == ("UNLOGGED_TOPUPS", "PROVEN", "ARCHIVE_ONLY")
    f = analysis(res)[U1]["features"]
    assert f["delta_snapshot_minus_history_minor"] == "30000" and f["delta_sign"] == "POSITIVE"
    assert f["groups"]["TOPUP"]["AFTER"] == {"count": 2, "sum_minor": "30000", "unparsed_amounts": 0}
    assert f["exact_matches"] == ["TOPUP_AFTER"] and f["cashback_percent_hint"] is None
    refs = analysis(res)[U1]["evidence_refs"]
    assert {r["pk"] for r in refs if r["table"] == "Payment_report"} == {"1", "2"}
    assert refs[0] == {"table": "wallet_transaction", "pk": "2", "role": "LAST_HISTORY_ROW"}
    rc = recon(res)
    assert rc["by_cause"]["UNLOGGED_TOPUPS"] == 1 and rc["by_certainty"]["PROVEN"] == 1
    assert rc["proven_abs_delta_by_cause_minor"] == {"UNLOGGED_TOPUPS": "30000"}
    assert rc["owner_decisions"] == []


def test_unlogged_purchase_and_epoch_window(tmp_path):
    # invoice.time_sell is an epoch; the last row is a local time: AFTER only beyond L + 12 h
    res = convert(tmp_path, history(U1, 50000 + 7000) + history(U2, 1000 + 4000, tid=10),
                  invoices=[inv("1234567aaaa0001", U1, 7000, epoch("2026-01-16 00:00:00")),   # > L + 12 h
                            inv("1234567aaaa0002", U2, 4000, epoch("2026-01-15 20:00:00")),   # inside the window
                            inv("1234567aaaa0003", U1, 5000, epoch("2026-03-01 10:00:00"), status="unpaid")])
    assert verdict(res, U1) == ("UNLOGGED_PURCHASES", "PROVEN", "ARCHIVE_ONLY")
    assert analysis(res)[U1]["features"]["delta_snapshot_minus_history_minor"] == "-7000"
    assert verdict(res, U2) == ("UNLOGGED_PURCHASES", "PROBABLE", "OWNER_DECISION")
    assert analysis(res)[U2]["features"]["groups"]["PURCHASE"]["IN_WINDOW"]["count"] == 1
    assert "WALLET_DIFF_PROVE_TIME_ZONES" in recon(res)["owner_decisions"]


def test_admin_adjustments_proven_with_signed_debit(tmp_path):
    res = convert(tmp_path, history(U1, 50000 - 6000),
                  pays=[pay(1, U1, 10000, "2026/02/01 10:00:00", method="add balance by admin"),
                        pay(2, U1, 4000, "2026/02/02 10:00:00", method="low balance by admin")])
    assert verdict(res, U1) == ("UNLOGGED_ADMIN_ADJUSTMENTS", "PROVEN", "ARCHIVE_ONLY")
    g = analysis(res)[U1]["features"]["groups"]["ADMIN_ADJUSTMENT"]["AFTER"]
    assert g == {"count": 2, "sum_minor": "6000", "unparsed_amounts": 0}


def test_service_operations_and_wheel_prizes_proven(tmp_path):
    op = (f"(1,{sql_str(U1)},'svc1','{{}}','2026/02/01 10:00:00','8000','extend_user','paid','SECRET-OUTPUT'),"
          f"(2,{sql_str(U1)},'svc1','{{}}','2026/02/02 10:00:00','9000','extend_user','unpaid',NULL),"
          f"(3,{sql_str(U1)},'svc1','{{}}','2026/02/03 10:00:00','0','gift_volume',NULL,NULL)")
    wheel = [f"(1,{sql_str(U2)},'2026/02/01 10:00:00','x','6','2500')",
             f"(2,{sql_str(U2)},'2026/02/02 10:00:00','x','3','0')"]
    res = convert(tmp_path, history(U1, 50000 + 8000) + history(U2, 1000 - 2500, tid=10), ops=[op], wheel=wheel)
    assert verdict(res, U1) == ("UNLOGGED_SERVICE_OPERATIONS", "PROVEN", "ARCHIVE_ONLY")
    assert verdict(res, U2) == ("UNLOGGED_WHEEL_PRIZES", "PROVEN", "ARCHIVE_ONLY")


def test_mixed_activity_proven(tmp_path):
    res = convert(tmp_path, history(U1, 50000 - 7000),
                  pays=[pay(1, U1, 10000, "2026/02/01 10:00:00")],
                  invoices=[inv("1234567aaaa0001", U1, 3000, epoch("2026-02-02 10:00:00"))])
    assert verdict(res, U1) == ("UNLOGGED_MIXED_ACTIVITY", "PROVEN", "ARCHIVE_ONLY")
    assert analysis(res)[U1]["features"]["exact_matches"] == ["ALL_AFTER"]


# ---- PROBABLE / UNEXPLAINED ------------------------------------------------------------------

def test_match_that_ignores_another_group_is_only_probable(tmp_path):
    res = convert(tmp_path, history(U1, 50000 - 10000),
                  pays=[pay(1, U1, 10000, "2026/02/01 10:00:00")],
                  invoices=[inv("1234567aaaa0001", U1, 3000, epoch("2026-02-02 10:00:00"))])
    assert verdict(res, U1) == ("UNLOGGED_TOPUPS", "PROBABLE", "OWNER_DECISION")


def test_unquantified_or_unplaceable_evidence_prevents_proof(tmp_path):
    res = convert(tmp_path, history(U1, 50000 - 10000) + history(U2, 1000 - 500, tid=10),
                  pays=[pay(1, U1, 10000, "2026/02/01 10:00:00"), pay(2, U2, 500, "1404/11/12 10:00:00")],
                  reagent=[f"(1,{U1},1,'2026/02/05 10:00:00','7200000099')"])
    a = analysis(res)
    assert verdict(res, U1) == ("UNLOGGED_TOPUPS", "PROBABLE", "OWNER_DECISION")
    assert a[U1]["features"]["unquantified"]["REFERRAL_GIFT"]["AFTER"] == 1
    assert verdict(res, U2) == ("ACTIVITY_AFTER_HISTORY_INEXACT", "PROBABLE", "OWNER_DECISION")
    assert a[U2]["features"]["groups"]["TOPUP"]["UNPLACEABLE"]["count"] == 1


def test_inexact_activity_unit_suspect_and_unexplained(tmp_path):
    res = convert(tmp_path, history(U1, 50000 - 9000) + history(U2, 10000, tid=10) + history(U3, -3000 + 42, tid=20),
                  pays=[pay(1, U1, 10000, "2026/02/01 10:00:00")])
    assert verdict(res, U1) == ("ACTIVITY_AFTER_HISTORY_INEXACT", "PROBABLE", "OWNER_DECISION")
    assert analysis(res)[U1]["features"]["cashback_percent_hint"] is None   # delta 9000 < top-ups 10000
    assert verdict(res, U2) == ("UNIT_SUSPECT", "PROBABLE", "OWNER_DECISION")   # history = 10 x snapshot 1000
    assert analysis(res)[U2]["features"]["unit_hint"] == {"snapshot_is_10x_history": False,
                                                          "history_is_10x_snapshot": True}
    assert verdict(res, U3) == ("UNEXPLAINED", "UNEXPLAINED", "OWNER_DECISION")
    rc = recon(res)
    assert rc["by_certainty"] == {"PROVEN": 0, "PROBABLE": 2, "UNEXPLAINED": 1}
    assert rc["owner_decisions"] == ["WALLET_DIFF_UNEXPLAINED_REVIEW", "WALLET_DIFF_PROBABLE_CONFIRM",
                                     "WALLET_DIFF_UNIT_SUSPECT_CONFIRM_UNIT"]
    assert rc["proven_abs_delta_by_cause_minor"] == {}


def test_order_tie_and_order_unproven(tmp_path):
    def row(i, uid, before, after, at):
        return f"({i},{sql_str(uid)},NULL,NULL,'x',{abs(after - before)},{before},{after},NULL,'bot',NULL,{sql_str(at)})"
    ddl = wallet_history.DDL.replace("`created_at` datetime", "`created_at` varchar(64)")
    rows = [row(1, U1, 0, 50000, LAST), row(2, U1, 0, 40000, LAST),            # same time: id order picks 40000
            row(10, U2, 0, 1000, "2026-01-10 10:00:00"), row(11, U2, 0, 300, None)]  # missing time → ORDER_UNPROVEN
    res = convert(tmp_path, rows, wallet_ddl=ddl)
    assert verdict(res, U1) == ("ORDER_UNPROVEN", "PROBABLE", "NONE")
    assert verdict(res, U2) == ("ORDER_UNPROVEN", "PROBABLE", "NONE")
    assert analysis(res)[U2]["features"]["delta_snapshot_minus_history_minor"] is None
    assert recon(res)["from_verdict"] == {"DIFFERS_FROM_SNAPSHOT": 1, "ORDER_UNPROVEN": 1}


# ---- invariants ------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def synthetic(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("causes")
    zp, exp = make_backup(tmp, users=400)
    return run(tmp, zp), exp, tmp, zp


def test_synthetic_backup_has_every_provable_kind_and_stays_ready(synthetic):
    res, exp, _, _ = synthetic
    assert res.report["readiness"] == "ready", res.report["blockers"]
    a = analysis(res)
    plan = exp["wallet_cause_plan"]
    assert {p["kind"] for p in plan} == set(payments.WALLET_CAUSE_SCENARIOS)
    for p in plan:
        r = a[p["uid"]]
        assert (r["cause"], r["certainty"]) == (p["cause"], p["certainty"]), p["kind"]
        assert r["features"]["delta_snapshot_minus_history_minor"] == str(p["delta"]), p["kind"]
        assert r["features"]["snapshot_minor"] == str(p["snapshot"])
    rc = recon(res)
    assert rc["total"] == len(a) == sum(rc["by_cause"].values()) == sum(rc["by_certainty"].values()) \
        == sum(rc["proposed_actions"].values()) == sum(rc["by_cause_and_certainty"].values())
    proven = {}
    for r in a.values():
        if r["certainty"] == "PROVEN":
            proven[r["cause"]] = proven.get(r["cause"], 0) + abs(int(r["features"]["delta_snapshot_minus_history_minor"]))
    assert rc["proven_abs_delta_by_cause_minor"] == {k: str(v) for k, v in sorted(proven.items())}
    assert rc["by_certainty"]["PROVEN"] == 4 and rc["opening_balance_changed"] is False
    warn = {w["code"]: w["count"] for w in res.report["warnings"]}
    assert warn["WALLET_HISTORY_DIFFERS_FROM_SNAPSHOT"] == rc["from_verdict"]["DIFFERS_FROM_SNAPSHOT"]


def test_opening_balances_are_the_snapshot(synthetic):
    res, exp, _, _ = synthetic
    opening = {o["telegram_user_id"]: o["amount_minor"]
               for o in read_jsonl(res.stage_dir / "records" / "wallet_opening_balances.jsonl")}
    for p in exp["wallet_cause_plan"]:
        if p["snapshot"] > 0:
            assert opening[p["uid"]] == str(p["snapshot"]), p["kind"]
        else:
            assert p["uid"] not in opening


def test_records_have_no_free_text_and_no_live_flags(synthetic):
    res, _, _, _ = synthetic
    roles = {"LAST_HISTORY_ROW"} | {f"{k}_{p}" for k in (*wallet_history.GROUPS, *wallet_history.UNQUANTIFIED)
                                    for p in ("AFTER", "IN_WINDOW", "UNPLACEABLE")}
    tables = {"wallet_transaction", "Payment_report", "invoice", "service_other", "wheel_list", "reagent_report",
              "Giftcodeconsumed"}
    keys = set()
    for r in analysis(res).values():
        assert r["record_type"] == "legacy_wallet_difference" and r["schema"] == wallet_history.DIFF_SCHEMA
        assert r["idempotency_key"] == f"legacy:wallet-difference:{r['customer']['telegram_user_id']}"
        keys.add(r["idempotency_key"])
        assert r["affects_wallet"] is False and r["applied_to_balance"] is False
        assert r["applies_to_live_state"] is False and r["opening_balance_changed"] is False
        assert r["proposed_action"] in {"ARCHIVE_ONLY", "OWNER_DECISION", "NONE"}
        for ref in r["evidence_refs"]:
            assert set(ref) <= REF_KEYS and ref["table"] in tables and ref["role"] in roles
            assert ref["pk"] is None or PK_SAFE.fullmatch(ref["pk"])
    assert len(keys) == len(analysis(res))
    text = (res.stage_dir / "records" / "wallet_difference_analysis.jsonl").read_text(encoding="utf-8")
    for s in ("شارژ", "خرید", "SECRET", "subscription_url", "synthetic_user"):
        assert s not in text


def test_free_text_never_reaches_analysis_or_reports(tmp_path):
    res = convert(tmp_path, history(U1, 40000), pays=[pay(1, U1, 10000, "2026/02/01 10:00:00")])
    for f in [res.report_path, res.stage_dir / "records" / "wallet_difference_analysis.jsonl",
              *(res.stage_dir / "reports").rglob("*")]:
        if f.is_file():
            text = f.read_text(encoding="utf-8")
            assert "SECRET-REASON" not in text and "SECRET-DEC-TEXT" not in text, f


def test_analysis_is_deterministic(synthetic, tmp_path):
    res, _, _, zp = synthetic
    again = run(tmp_path, zp, ws_name="again")
    rel = "records/wallet_difference_analysis.jsonl"
    assert (res.stage_dir / rel).read_bytes() == (again.stage_dir / rel).read_bytes()
    assert recon(res) == recon(again)
    assert res.report["reconciliation"]["money_unit_evidence"] == again.report["reconciliation"]["money_unit_evidence"]


def test_without_wallet_table_the_section_is_empty(tmp_path):
    res = convert(tmp_path, [], pays=[pay(1, U1, 10000, "2026/02/01 10:00:00")], wallet_ddl="")
    rc = recon(res)
    assert rc["total"] == 0 and rc["status"] == "NO_DIFFERENCES" and rc["owner_decisions"] == []
    assert set(rc["by_certainty"]) == {"PROVEN", "PROBABLE", "UNEXPLAINED"}
    assert not (res.stage_dir / "records" / "wallet_difference_analysis.jsonl").exists()


def test_cashback_hint_is_a_feature_not_a_cause(tmp_path):
    # top-ups 20000 after the history, delta 21000: 5 % more, the shape of an unrecorded cashback
    res = convert(tmp_path, history(U1, 50000 - 21000), pays=[pay(1, U1, 20000, "2026/02/01 10:00:00")])
    assert verdict(res, U1) == ("ACTIVITY_AFTER_HISTORY_INEXACT", "PROBABLE", "OWNER_DECISION")
    assert analysis(res)[U1]["features"]["cashback_percent_hint"] == "5.00"

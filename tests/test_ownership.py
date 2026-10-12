"""Service ownership model (modules/ownership.py, docs/OWNERSHIP.md) on synthetic data only."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from conftest import make_backup, read_jsonl, run
from mirza2nexa.modules import ownership as own
from mirza2nexa.modules import services_ext as sx
from mirza2nexa.synth import TG_BASE, SynthSpec, write_dump

LIVE_FLAGS = ("provision", "applies_to_live_state", "affects_wallet", "creates_payment")
A, B, C, X = (str(TG_BASE + n) for n in (10, 11, 12, 13))
GONE = "6999999888"  # valid Telegram id, not in the user table


def records(res, name: str = "service_ownership") -> list[dict]:
    return read_jsonl(res.stage_dir / "records" / f"{name}.jsonl")


def review(res) -> list[dict]:
    return read_jsonl(res.stage_dir / "reports" / "review-queue.jsonl")


def by_invoice(res) -> dict[str, dict]:
    return {r["invoice_key"]: r for r in records(res) if r["invoice_key"] is not None}


# ---- a controlled dump: one invoice per scenario -------------------------------------------------

INVOICES = [  # (key, owner, username, status)
    ("8000001abcd", A, "own_nt", "active"),        # no transfer, nothing else
    ("8000002abcd", B, "own_ab", "active"),        # A→B (+ prior extend by A, cancel request by A)
    ("8000003abcd", C, "own_abc", "active"),       # A→B→C with ops by every holder
    ("8000004abcd", A, "own_old", "active"),       # old invoice keeps A, transfer names B who operates
    ("8000006abcd", "5000000077", "own_del", "active"),  # owner not in user table
    ("8000007abcd", "not-a-number", "own_bad", "active"),  # invalid owner id
    ("8000008abcd", A, "own_two", "active"),       # two users claim one service
    ("8000009abcd", B, "own_two", "active"),
    ("8000010abcd", B, "own_out", "active"),       # A acts after transferring away
    ("8000011abcd", A, "own_x", "active"),         # stranger's cancel request, no transfer
    ("8000012abcd", C, "own_brk", "active"),       # broken chain A→B, X→C
    ("8000013abcd", A, "own_pay", "active"),       # third-party payer + admin renewal only
    ("8000014abcd", A, "own_gone", "active"),      # requester no longer in user table (account move?)
    ("8000015abcd", A, "own_self", "active"),      # self transfer (MP forbids it)
    ("8000016abcd", A, "own_aba", "active"),       # A→B→A, B operated while holding
    ("8000017abcd", B, "own_hist", "removed"),     # non-live invoice, referenced by a proven transfer
]
SO = [  # (id, id_user, username, value, type)
    (10, A, "own_ab", "{}", "extend_user"),
    (11, A, "own_ab", B, "transfertouser"),
    (20, A, "own_abc", "{}", "extend_user"),
    (21, A, "own_abc", "{}", "extra_user"),
    (22, A, "own_abc", B, "transfertouser"),
    (23, B, "own_abc", "{}", "extra_time_user"),
    (24, B, "own_abc", C, "transfertouser"),
    (25, C, "own_abc", "{}", "extend_user"),
    (30, A, "own_old", B, "transfertouser"),
    (31, B, "own_old", "{}", "extra_user"),
    (35, A, "own_renamed", "{}", "extend_user"),   # username changed: no invoice has it
    (40, A, "own_out", B, "transfertouser"),
    (41, A, "own_out", "{}", "extra_user"),
    (50, A, "own_brk", B, "transfertouser"),
    (51, X, "own_brk", C, "transfertouser"),
    (60, X, "own_pay", "{}", "extends_not_user"),
    (61, "999", "own_pay", "{}", "extend_user_by_admin"),
    (70, A, "own_self", A, "transfertouser"),
    (80, A, "own_aba", B, "transfertouser"),
    (81, B, "own_aba", "{}", "extra_user"),
    (82, B, "own_aba", A, "transfertouser"),
    (90, A, "own_hist", B, "transfertouser"),
]
CX = [(1, A, "own_ab", "reject"), (2, X, "own_x", "waiting"), (3, GONE, "own_gone", "waiting")]


def _sql() -> str:
    q = sx.sql_str
    inv = ",".join(f"({q(k)},{q(o)},{q(u)},'x','1700000000','p','1000','10','30',{q(s)},'syn-a','p001','0','0','',"
                   "NULL,NULL)" for k, o, u, s in INVOICES)
    so = ",".join(f"({i},{q(a)},{q(u)},{q(v)},'2025/06/01 10:00:{i % 60:02d}','0',{q(t)},NULL,NULL)"
                  for i, a, u, v, t in SO)
    cx = ",".join(f"({i},{q(a)},{q(u)},'0',{q(s)})" for i, a, u, s in CX)
    return (f"INSERT INTO `invoice` VALUES {inv};\n"
            + "DROP TABLE IF EXISTS `service_other`;\n" + sx.DDL["service_other"]
            + f"INSERT INTO `service_other` VALUES {so};\n"
            + "DROP TABLE IF EXISTS `cancel_service`;\n" + sx.DDL["cancel_service"]
            + f"INSERT INTO `cancel_service` VALUES {cx};\n")


def _zip(tmp: Path, name: str = "own.zip") -> Path:
    buf = io.StringIO()
    write_dump(buf, SynthSpec(users=40, modules=False, invoices_per_user=1.0))
    buf.write(_sql())
    path = tmp / name
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("backup.sql", buf.getvalue().encode("utf-8"))
    return path


@pytest.fixture(scope="module")
def custom(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("own")
    return run(tmp, _zip(tmp), ws_name="a")


EXPECTED = {  # invoice key → (decision, reason codes)
    "8000001abcd": ("NO_CONFLICT", []),
    "8000002abcd": ("CONFIRMED_TRANSFER", []),
    "8000003abcd": ("CONFIRMED_TRANSFER", []),
    "8000004abcd": ("AMBIGUOUS_OWNER", ["OWNERSHIP_TRANSFER_NOT_APPLIED_TO_INVOICE"]),
    "8000006abcd": ("ORPHAN_OWNER", ["OWNERSHIP_OWNER_NOT_IN_USER_TABLE"]),
    "8000007abcd": ("INVALID_OWNER", ["OWNERSHIP_OWNER_ID_INVALID"]),
    "8000008abcd": ("AMBIGUOUS_OWNER", ["OWNERSHIP_MULTIPLE_CLAIMANTS"]),
    "8000009abcd": ("AMBIGUOUS_OWNER", ["OWNERSHIP_MULTIPLE_CLAIMANTS"]),
    "8000010abcd": ("AMBIGUOUS_OWNER", ["OWNERSHIP_ACTION_OUTSIDE_HOLDING_WINDOW"]),
    "8000011abcd": ("AMBIGUOUS_OWNER", ["OWNERSHIP_OWNER_MISMATCH_NO_TRANSFER_RECORD"]),
    "8000012abcd": ("AMBIGUOUS_OWNER", ["OWNERSHIP_TRANSFER_CHAIN_BROKEN"]),
    "8000013abcd": ("NO_CONFLICT", []),
    "8000014abcd": ("AMBIGUOUS_OWNER", ["OWNERSHIP_ACTOR_NOT_IN_USER_TABLE"]),
    "8000015abcd": ("AMBIGUOUS_OWNER", ["OWNERSHIP_TRANSFER_CHAIN_BROKEN"]),
    "8000016abcd": ("CONFIRMED_TRANSFER", []),
    "8000017abcd": ("CONFIRMED_TRANSFER", []),
}


def test_every_scenario_has_its_exact_decision(custom):
    got = by_invoice(custom)
    for key, (decision, reasons) in EXPECTED.items():
        r = got[key]
        assert (r["ownership_decision"], r["reason_codes"]) == (decision, reasons), key
        assert r["ownership_confidence"] == ("PROVEN" if decision in own.FINAL_DECISIONS else
                                             "NONE" if decision in ("ORPHAN_OWNER", "INVALID_OWNER") else "PARTIAL")
        assert r["manual_review_reason"] == (reasons[0] if reasons else None)


def test_transfer_chains_and_original_owner(custom):
    got = by_invoice(custom)
    ab, abc, aba = got["8000002abcd"], got["8000003abcd"], got["8000016abcd"]
    assert ab["original_owner"]["telegram_user_id"] == A and ab["final_owner_telegram_user_id"] == B
    assert [(t["from_telegram_user_id"], t["to_telegram_user_id"]) for t in abc["transfer_evidence"]] == [(A, B), (B, C)]
    assert all(t["link_ok"] for t in abc["transfer_evidence"])
    assert abc["original_owner"] == {"telegram_user_id": A, "basis": "FIRST_TRANSFER_SENDER", "rule": "OWN-R3",
                                     "source_table": "service_other", "pk": "22"}
    assert abc["final_owner_telegram_user_id"] == C
    # every op of A and B on own_abc lies in their holding windows
    rel = {e["pk"]: e["relation"] for e in abc["ownership_evidence"] if e["source_table"] == "service_other"}
    assert rel == {"20": "PREVIOUS_OWNER_VIA_TRANSFER", "21": "PREVIOUS_OWNER_VIA_TRANSFER",
                   "22": "PREVIOUS_OWNER_VIA_TRANSFER", "23": "PREVIOUS_OWNER_VIA_TRANSFER",
                   "24": "PREVIOUS_OWNER_VIA_TRANSFER", "25": "MATCHES_INVOICE"}
    assert aba["original_owner"]["telegram_user_id"] == A and aba["final_owner_telegram_user_id"] == A
    assert "OWNERSHIP_TRANSFER_CHAIN_PROVEN" in ab["info_codes"]
    # the cancel request of the previous owner A is explained (cancel_service is untimed: holder membership)
    cx = [e for e in ab["ownership_evidence"] if e["source_table"] == "cancel_service"]
    assert [(e["role"], e["relation"], e["rule"]) for e in cx] == [("CANCEL_REQUEST", "PREVIOUS_OWNER_VIA_TRANSFER",
                                                                    "OWN-R8")]
    hist = got["8000017abcd"]
    assert hist["live_service_candidate"] is False and hist["needs_manual_review"] is False
    assert hist["review_scope"] == "HISTORY_ONLY"


def test_no_service_is_ever_assigned_to_another_account(custom):
    invoices = {k: o for k, o, _u, _s in INVOICES}
    for r in records(custom):
        final = r["final_owner_telegram_user_id"]
        if final is not None:
            assert final == invoices.get(r["invoice_key"], final)
            assert r["ownership_decision"] in own.FINAL_DECISIONS
            assert r["current_owner_candidate"]["telegram_user_id"] == final
        else:
            assert r["ownership_decision"] not in own.FINAL_DECISIONS
        cur = r["current_owner_candidate"]
        if cur is not None and r["invoice_key"] in invoices:
            assert cur["telegram_user_id"] == invoices[r["invoice_key"]]  # only invoice.id_user, never a reference
    got = by_invoice(custom)
    # the transfer target B of the old invoice is never made its owner; neither is the claimant of 'own_two'
    assert got["8000004abcd"]["final_owner_telegram_user_id"] is None
    assert got["8000004abcd"]["current_owner_candidate"]["telegram_user_id"] == A
    assert got["8000008abcd"]["final_owner_telegram_user_id"] is None


def test_username_changed_is_not_linked_by_owner(custom):
    unl = [r for r in records(custom) if r["invoice_key"] is None]
    assert [r["panel_account_username"] for r in unl] == ["own_renamed"]
    r = unl[0]
    assert r["ownership_decision"] == "AMBIGUOUS_OWNER" and r["reason_codes"] == ["OWNERSHIP_NO_INVOICE_FOR_USERNAME"]
    assert r["current_owner_candidate"] is None and r["final_owner_telegram_user_id"] is None
    assert r["idempotency_key"].startswith("legacy:service-ownership:unlinked:svc-username:")
    assert "own_renamed" not in r["idempotency_key"]
    assert r["needs_manual_review"] is False and r["review_scope"] == "HISTORY_ONLY"
    # A's own invoice 'own_nt' is not touched by A's rows under another name
    assert by_invoice(custom)["8000001abcd"]["ownership_decision"] == "NO_CONFLICT"


def test_payer_and_admin_rows_are_not_owner_claims(custom):
    r = by_invoice(custom)["8000013abcd"]
    assert r["info_codes"] == ["OWNERSHIP_ADMIN_ACTOR_NOT_OWNER", "OWNERSHIP_PAYER_NOT_OWNER"]
    ops = {o["legacy_id"]: o for o in records(custom, "service_operations")}
    assert ops["60"]["owner_relation"] == "PAYER_NOT_OWNER" and ops["60"]["owner"]["role"] == "PAYER_NOT_OWNER_CLAIM"
    assert "SERVICE_OWNER_CONFLICT" not in ops["60"]["review_codes"]
    assert ops["61"]["owner_relation"] == "NOT_AN_OWNER_FIELD"


def test_services_ext_conflicts_reclassified_with_reasons(custom):
    ops = {o["legacy_id"]: o for o in records(custom, "service_operations")}
    conflict = {k: o["ownership_reason"] for k, o in ops.items() if o["owner_relation"] == "CONFLICT"}
    assert conflict == {"31": "OWNERSHIP_TRANSFER_NOT_APPLIED_TO_INVOICE",
                        "41": "OWNERSHIP_ACTION_OUTSIDE_HOLDING_WINDOW",
                        "50": "OWNERSHIP_TRANSFER_CHAIN_BROKEN", "51": "OWNERSHIP_TRANSFER_CHAIN_BROKEN"}
    explained = sorted(k for k, o in ops.items() if o["owner_relation"] == "PREVIOUS_OWNER_VIA_TRANSFER")
    assert explained == ["10", "11", "20", "21", "22", "23", "24", "40", "81", "82", "90"]
    rv = [r for r in review(custom) if r["code"] == "SERVICE_OWNER_CONFLICT"]
    assert sorted((r["source_ref"]["table"], r["source_ref"]["pk"]) for r in rv) == sorted(
        [("service_other", k) for k in conflict] + [("cancel_service", "2"), ("cancel_service", "3")])
    assert all(r["data"]["ownership_reason"].startswith("OWNERSHIP_") for r in rv)
    rec = custom.report["reconciliation"]["service_ownership"]
    # previous rule: own_old B, own_x X, own_pay payer X, own_gone; it wrongly explained own_out and own_brk
    assert rec["conflict_warnings_before"] == 4
    assert rec["conflict_warnings_after"] == 6
    assert rec["conflict_transitions"] == {"CONFLICT_TO_EXPLAINED": 1, "EXPLAINED_TO_CONFLICT": 3,
                                           "UNCHANGED_CONFLICT": 3}
    warn = {w["code"]: w["count"] for w in custom.report["warnings"]}
    assert warn["SERVICE_OWNER_CONFLICTS"] == rec["conflict_warnings_after"]
    so = custom.report["reconciliation"]["service_operations"]
    cx = custom.report["reconciliation"]["service_requests"]["cancel_service"]
    assert so["owner_conflicts_previous_rule"] + cx["owner_conflicts_previous_rule"] == 4
    assert so["owner_conflicts"] + cx["owner_conflicts"] == 6


def test_reconciliation_contract_and_review_rows(custom):
    rec = custom.report["reconciliation"]["service_ownership"]
    for k in ("total", "by_decision", "needs_manual_review", "confirmed_transfers", "conflict_warnings_before",
              "conflict_warnings_after"):
        assert k in rec, k
    recs = records(custom)
    assert rec["total"] == len(recs) == sum(rec["by_decision"].values())
    assert set(rec["by_decision"]) == set(own.DECISIONS)
    assert rec["confirmed_transfers"] == rec["by_decision"]["CONFIRMED_TRANSFER"] == 4
    assert rec["needs_manual_review"] == sum(r["needs_manual_review"] for r in recs)
    assert rec["final_owner_set"] == rec["final_owner_equals_invoice_owner"]
    own_rv = [r for r in review(custom) if r["kind"] == "service_ownership"]
    assert all(r["code"].startswith("OWNERSHIP_") and r["severity"] == "blocker" for r in own_rv)
    # core already reports ORPHAN / AMBIGUOUS_OWNERSHIP: no duplicate review row for those
    covered = [r for r in recs if r["already_in_review_queue_as"]]
    assert {r["invoice_key"] for r in covered} >= {"8000006abcd", "8000007abcd", "8000008abcd", "8000009abcd"}
    assert len(own_rv) == rec["needs_manual_review"] - rec["covered_by_core_review"]
    assert {r["source_ref"]["pk"] for r in own_rv} >= {"8000004abcd", "8000010abcd", "8000011abcd", "8000012abcd", "8000014abcd",
                                                        "8000015abcd"}
    assert custom.report["readiness"] == "ready"


def test_record_shape_flags_and_keys(custom):
    recs = records(custom)
    keys = [r["idempotency_key"] for r in recs]
    assert len(keys) == len(set(keys))
    for r in recs:
        assert r["record_type"] == "legacy_service_ownership" and r["schema"] == own.SCHEMA
        assert r["idempotency_key"].startswith("legacy:service-ownership:")
        assert r["provenance"]["source_fingerprint"] == custom.report["source_fingerprint"]
        assert r["ownership_decision"] in own.DECISIONS
        assert r["ownership_confidence"] in ("PROVEN", "PARTIAL", "NONE")
        for f in LIVE_FLAGS:
            assert r[f] is False
        for e in r["ownership_evidence"]:
            assert set(e) == {"rule", "source_table", "pk", "role", "type_raw", "telegram_user_id",
                              "in_user_table", "relation", "reason"}
    assert "legacy:service-ownership:8000002abcd" in keys


def test_deterministic(custom, tmp_path):
    again = run(tmp_path, _zip(tmp_path), ws_name="b")
    a = (custom.stage_dir / "records" / "service_ownership.jsonl").read_bytes()
    b = (again.stage_dir / "records" / "service_ownership.jsonl").read_bytes()
    assert a == b
    assert custom.report["reconciliation"]["service_ownership"] == again.report["reconciliation"]["service_ownership"]


def test_coverage_accounting_unaffected(custom):
    cov = {c["table"]: c for c in custom.report["coverage"]}
    assert cov["service_other"]["accounted_rows"] == cov["service_other"]["rows"] == len(SO)
    assert cov["cancel_service"]["accounted_rows"] == cov["cancel_service"]["rows"] == len(CX)
    assert "service_ownership" not in cov


# ---- the default synthetic backup carries every scenario too ------------------------------------

@pytest.fixture(scope="module")
def default(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("own_default")
    zp, exp = make_backup(tmp, users=300, password="pw-Ab12345")
    return run(tmp, zp, password="pw-Ab12345"), exp


def test_default_backup_scenarios(default):
    res, exp = default
    sc = exp["ownership_scenarios"]
    got = by_invoice(res)
    unl = {r["panel_account_username"]: r for r in records(res) if r["invoice_key"] is None}
    want = {
        "no_transfer": ("CONFIRMED_CURRENT_OWNER", []),
        "a_to_b": ("CONFIRMED_TRANSFER", []),
        "round_trip_chain": ("CONFIRMED_TRANSFER", []),
        "old_invoice_new_owner": ("AMBIGUOUS_OWNER", ["OWNERSHIP_TRANSFER_NOT_APPLIED_TO_INVOICE"]),
        "action_outside_window": ("AMBIGUOUS_OWNER", ["OWNERSHIP_ACTION_OUTSIDE_HOLDING_WINDOW"]),
        "actor_not_in_user_table": ("AMBIGUOUS_OWNER", ["OWNERSHIP_ACTOR_NOT_IN_USER_TABLE"]),
        "stranger_no_transfer": ("AMBIGUOUS_OWNER", ["OWNERSHIP_OWNER_MISMATCH_NO_TRANSFER_RECORD"]),
        "chain_broken": ("AMBIGUOUS_OWNER", ["OWNERSHIP_TRANSFER_CHAIN_BROKEN"]),
        "owner_deleted": ("ORPHAN_OWNER", ["OWNERSHIP_OWNER_NOT_IN_USER_TABLE"]),
    }
    for name, (decision, reasons) in want.items():
        r = got[sc[name]]
        assert (r["ownership_decision"], r["reason_codes"]) == (decision, reasons), name
    two = got[sc["two_claimants"]]
    assert two["ownership_decision"] == "AMBIGUOUS_OWNER" and two["reason_codes"][0] == "OWNERSHIP_MULTIPLE_CLAIMANTS"
    assert two["already_in_review_queue_as"] == "AMBIGUOUS_OWNERSHIP"
    assert unl[sc["username_changed"]]["reason_codes"] == ["OWNERSHIP_NO_INVOICE_FOR_USERNAME"]
    assert got[sc["username_changed_invoice"]]["ownership_decision"] in ("NO_CONFLICT", "CONFIRMED_CURRENT_OWNER")
    assert res.report["readiness"] == "ready", res.report["blockers"]
    rec = res.report["reconciliation"]["service_ownership"]
    assert rec["final_owner_set"] == rec["final_owner_equals_invoice_owner"] > 0
    cov = {c["table"]: c for c in res.report["coverage"]}
    for t in ("service_other", "cancel_service"):
        assert cov[t]["accounted_rows"] == cov[t]["rows"]


def test_module_registered_after_services_ext():
    from mirza2nexa.modules.base import modules_in_order

    order = [m.key for m in modules_in_order()]
    assert order.index("services_ext") < order.index("ownership")


# ---- OWN-R4 binds the current owner too (holding windows of the invoice owner) -------------------

WIN_INVOICES = [
    ("8200001abcd", B, "win_pre", "active"),   # B acts before receiving it from A
    ("8200002abcd", A, "win_aba", "active"),   # A→B→A, A acts in its first and in its last window
    ("8200003abcd", B, "win_post", "active"),  # B acts (and asks to cancel) after receiving it
    ("8200004abcd", A, "win_gap", "active"),   # A→B→A, A acts while B holds it
]
WIN_SO = [
    (100, B, "win_pre", "{}", "extend_user"),
    (101, A, "win_pre", B, "transfertouser"),
    (200, A, "win_aba", "{}", "extend_user"),
    (201, A, "win_aba", B, "transfertouser"),
    (202, B, "win_aba", A, "transfertouser"),
    (203, A, "win_aba", "{}", "extra_user"),
    (300, A, "win_post", B, "transfertouser"),
    (301, B, "win_post", "{}", "extra_user"),
    (400, A, "win_gap", B, "transfertouser"),
    (401, A, "win_gap", "{}", "extra_user"),
    (402, B, "win_gap", A, "transfertouser"),
]
WIN_CX = [(1, B, "win_post", "reject")]


@pytest.fixture(scope="module")
def windows(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("own_win")
    q = sx.sql_str
    inv = ",".join(f"({q(k)},{q(o)},{q(u)},'x','1700000000','p','1000','10','30',{q(s)},'syn-a','p001','0','0','',"
                   "NULL,NULL)" for k, o, u, s in WIN_INVOICES)
    so = ",".join(f"({i},{q(a)},{q(u)},{q(v)},'2025/06/01 10:00:{i % 60:02d}','0',{q(t)},NULL,NULL)"
                  for i, a, u, v, t in WIN_SO)
    cx = ",".join(f"({i},{q(a)},{q(u)},'0',{q(s)})" for i, a, u, s in WIN_CX)
    buf = io.StringIO()
    write_dump(buf, SynthSpec(users=40, modules=False, invoices_per_user=1.0))
    buf.write(f"INSERT INTO `invoice` VALUES {inv};\n"
              + "DROP TABLE IF EXISTS `service_other`;\n" + sx.DDL["service_other"]
              + f"INSERT INTO `service_other` VALUES {so};\n"
              + "DROP TABLE IF EXISTS `cancel_service`;\n" + sx.DDL["cancel_service"]
              + f"INSERT INTO `cancel_service` VALUES {cx};\n")
    path = tmp / "win.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("backup.sql", buf.getvalue().encode("utf-8"))
    return run(tmp, path, ws_name="w")


def test_current_owner_acting_before_receiving_is_not_proven(windows):
    r = by_invoice(windows)["8200001abcd"]
    assert r["ownership_decision"] == "AMBIGUOUS_OWNER"
    assert r["reason_codes"] == ["OWNERSHIP_ACTION_OUTSIDE_HOLDING_WINDOW"]
    assert r["ownership_confidence"] != "PROVEN" and r["final_owner_telegram_user_id"] is None
    assert r["original_owner"] is None and r["needs_manual_review"] is True
    ev = {e["pk"]: (e["telegram_user_id"], e["relation"], e["reason"]) for e in r["ownership_evidence"]
          if e["source_table"] == "service_other"}
    assert ev["100"] == (B, "CONFLICT", "OWNERSHIP_ACTION_OUTSIDE_HOLDING_WINDOW")
    rv = [x for x in review(windows) if x["kind"] == "service_ownership" and x["source_ref"]["pk"] == "8200001abcd"]
    assert [x["code"] for x in rv] == ["OWNERSHIP_ACTION_OUTSIDE_HOLDING_WINDOW"]


def test_current_owner_windows_include_earlier_holding(windows):
    got = by_invoice(windows)
    aba = got["8200002abcd"]
    assert (aba["ownership_decision"], aba["reason_codes"]) == ("CONFIRMED_TRANSFER", [])
    assert aba["final_owner_telegram_user_id"] == A and aba["ownership_confidence"] == "PROVEN"
    rel = {e["pk"]: e["relation"] for e in aba["ownership_evidence"] if e["source_table"] == "service_other"}
    assert rel["200"] == rel["203"] == "MATCHES_INVOICE"
    gap = got["8200004abcd"]   # A acting while B held it is outside every window of A
    assert (gap["ownership_decision"], gap["reason_codes"]) == ("AMBIGUOUS_OWNER",
                                                                ["OWNERSHIP_ACTION_OUTSIDE_HOLDING_WINDOW"])
    assert gap["final_owner_telegram_user_id"] is None


def test_current_owner_acting_after_receiving_is_confirmed(windows):
    r = by_invoice(windows)["8200003abcd"]
    assert (r["ownership_decision"], r["reason_codes"]) == ("CONFIRMED_TRANSFER", [])
    assert r["final_owner_telegram_user_id"] == B and r["original_owner"]["telegram_user_id"] == A
    assert all(e["reason"] is None for e in r["ownership_evidence"])

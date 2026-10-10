"""Bulk ownership review: eligibility, two-phase bulk decisions at 5,000+ scale, idempotency,
undo, audit chain, seal, stale bindings, signed export and persistence (synthetic data only)."""

from __future__ import annotations

import json
import sqlite3
import time

import pytest

from mirza2nexa import ownership_review as orv
from mirza2nexa.nxpkg.crypto import WrongPackageKey

TG = 7_100_000_000


def rec(n: int, decision: str = "AMBIGUOUS_OWNER", reasons: tuple[str, ...] = ("OWNERSHIP_ACTION_OUTSIDE_HOLDING_WINDOW",),
        *, in_user: bool = True, customer: str = "IMPORT", invoice: bool = True) -> dict:
    return {
        "idempotency_key": f"legacy:service-ownership:{n:012d}",
        "invoice_key": f"{n:012d}" if invoice else None,
        "ownership_decision": decision,
        "ownership_confidence": "PROVEN" if decision.startswith("CONFIRMED") or decision == "NO_CONFLICT" else "PARTIAL",
        "manual_review_reason": reasons[0] if reasons else None,
        "reason_codes": list(reasons),
        "current_owner_candidate": {"telegram_user_id": str(TG + n), "in_user_table": in_user,
                                    "customer_decision": customer},
        "panel_account_username": f"svc{n}",
    }


def invoices(recs: list[dict], panel: str = "p-a") -> dict[str, dict]:
    return {r["invoice_key"]: {"code_panel": panel, "status_raw": "active", "is_test": False}
            for r in recs if r["invoice_key"]}


@pytest.fixture()
def store(tmp_path):
    s = orv.ReviewStore.for_import(tmp_path / "reviews", "ab" * 32, "cd" * 32)
    yield s
    s.close()


def load(store, n_attest=50, n_proven=20, n_quarantine=10):
    recs = [rec(i) for i in range(n_attest)]
    recs += [rec(100_000 + i, "CONFIRMED_CURRENT_OWNER", ()) for i in range(n_proven)]
    recs += [rec(200_000 + i, reasons=("OWNERSHIP_MULTIPLE_CLAIMANTS",)) for i in range(n_quarantine)]
    store.sync(recs, invoices(recs))
    return recs


# ---- eligibility --------------------------------------------------------------------------------

@pytest.mark.parametrize("r, expected", [
    (rec(1, "CONFIRMED_CURRENT_OWNER", ()), "PROVEN"),
    (rec(1, "CONFIRMED_TRANSFER", ()), "PROVEN"),
    (rec(1, "NO_CONFLICT", ()), "PROVEN"),
    (rec(1), "ATTESTABLE"),
    (rec(1, reasons=("OWNERSHIP_TRANSFER_CHAIN_BROKEN", "OWNERSHIP_ACTOR_NOT_IN_USER_TABLE")), "ATTESTABLE"),
    (rec(1, reasons=("OWNERSHIP_ACTION_OUTSIDE_HOLDING_WINDOW", "OWNERSHIP_MULTIPLE_CLAIMANTS")), "QUARANTINE_ONLY"),
    (rec(1, reasons=("OWNERSHIP_TRANSFER_NOT_APPLIED_TO_INVOICE",)), "QUARANTINE_ONLY"),
    (rec(1, reasons=("OWNERSHIP_USERNAME_SHARED_BY_INVOICES",)), "QUARANTINE_ONLY"),
    (rec(1, "ORPHAN_OWNER", ("OWNERSHIP_OWNER_NOT_IN_USER_TABLE",)), "QUARANTINE_ONLY"),
    (rec(1, "INVALID_OWNER", ("OWNERSHIP_OWNER_ID_INVALID",)), "QUARANTINE_ONLY"),
    (rec(1, in_user=False), "QUARANTINE_ONLY"),
    (rec(1, customer="SKIP"), "QUARANTINE_ONLY"),
    (rec(1, invoice=False), "QUARANTINE_ONLY"),
])
def test_eligibility(r, expected):
    assert orv.eligibility(r) == expected


# ---- bulk at scale ------------------------------------------------------------------------------

def test_bulk_approve_all_filtered_5000_plus(store):
    n = 6000
    load(store, n_attest=n, n_proven=300, n_quarantine=200)
    s0 = store.summary()
    assert s0["totals"]["PENDING"] == n and s0["totals"]["PROVEN"] == 300 and s0["totals"]["QUARANTINED"] == 200

    t = time.time()
    plan = store.plan("APPROVE", {"mode": "filter", "filter": {}, "exclude_keys": []}, "bulk test")
    assert plan["selected"] == n + 500 and plan["applicable"] == n
    assert plan["not_eligible"]["by_reason"] == {"ALREADY_PROVEN": 300, "OWNERSHIP_MULTIPLE_CLAIMANTS": 200}
    risks = {r["code"]: r for r in plan["risks"]}
    assert risks["ADMIN_ATTESTATION_NOT_PROOF"]["count"] == n and risks["ADMIN_ATTESTATION_NOT_PROOF"]["severity"] == "high"
    assert risks["UNVIEWED_ITEMS"]["count"] == n and risks["LIVE_NOT_CHECKED"]["count"] == n
    assert plan["unreviewed"]["count"] == n and len(plan["unreviewed"]["samples"]) == orv.SAMPLE_SIZE
    assert plan["admin_attestation_is_proof"] is False

    with pytest.raises(orv.ReviewError) as e:
        store.commit(plan["plan_id"], confirm_count=n - 1, acknowledge=True)
    assert e.value.code == "ownership.confirm_mismatch"
    with pytest.raises(orv.ReviewError) as e:
        store.commit(plan["plan_id"], confirm_count=n, acknowledge=False)
    assert e.value.code == "ownership.not_acknowledged"
    assert store.summary()["totals"]["ADMIN_ATTESTED"] == 0  # refused commits changed nothing

    res = store.commit(plan["plan_id"], confirm_count=n, acknowledge=True)
    assert res["applied"] == n and res["idempotent_replay"] is False
    assert time.time() - t < 30
    s1 = store.summary()
    assert s1["totals"]["ADMIN_ATTESTED"] == n and s1["totals"]["PROVEN"] == 300  # kept apart
    assert s1["totals"]["QUARANTINED"] == 200 and s1["admin_attested_is_proof"] is False

    # idempotent: same commit again → same batch, nothing re-applied
    again = store.commit(plan["plan_id"], confirm_count=n, acknowledge=True)
    assert again["batch_id"] == res["batch_id"] and again["idempotent_replay"] is True
    assert store.audit(0, 1)["total"] == n + 1
    # the same selection again changes nothing
    p2 = store.plan("APPROVE", {"mode": "filter", "filter": {}})
    assert p2["applicable"] == 0 and p2["unchanged"] == n
    assert store.verify_audit()["ok"] is True


def test_filters_and_exclusions(store):
    recs = [rec(i) for i in range(40)]
    inv = invoices(recs[:20], "p-a") | invoices(recs[20:], "p-b")
    store.sync(recs, inv)
    assert store.items({"panel": "p-b"})["total"] == 20
    assert store.items({"panel": "p-a,p-b", "final_class": ["PENDING"]})["total"] == 40
    assert store.items({"reason": "OWNERSHIP_ACTION_OUTSIDE_HOLDING_WINDOW"})["total"] == 40
    assert store.items({"q": "00000000001"})["total"] == 10  # invoice-key prefix
    excl = [r["idempotency_key"] for r in recs[20:25]]
    plan = store.plan("APPROVE", {"mode": "filter", "filter": {"panel": ["p-b"]}, "exclude_keys": excl})
    assert plan["applicable"] == 15
    store.commit(plan["plan_id"], confirm_count=15, acknowledge=True)
    assert store.items({"review_state": "APPROVED"})["total"] == 15
    f = store.facets()
    assert {x["value"]: x["count"] for x in f["final_class"]} == {"ADMIN_ATTESTED": 15, "PENDING": 25}
    with pytest.raises(orv.ReviewError):
        store.items({"panel": "x' OR 1=1 --", "status_class": 5})


def test_quarantine_can_never_be_approved(store):
    recs = load(store)
    q = [r["idempotency_key"] for r in recs if "MULTIPLE_CLAIMANTS" in (r["manual_review_reason"] or "")]
    plan = store.plan("APPROVE", {"mode": "keys", "keys": q})
    assert plan["applicable"] == 0 and plan["not_eligible"]["total"] == len(q)
    rej = store.plan("REJECT", {"mode": "keys", "keys": q})
    assert rej["applicable"] == 0 and rej["not_eligible"]["by_reason"] == {"ALREADY_QUARANTINED": len(q)}
    for k in q:
        assert store.item(k)["final_class"] == "QUARANTINED"


def test_reject_proven_is_flagged_and_reversible(store):
    recs = load(store)
    proven = [r["idempotency_key"] for r in recs if r["ownership_decision"] == "CONFIRMED_CURRENT_OWNER"][:5]
    plan = store.plan("REJECT", {"mode": "keys", "keys": proven}, "suspicious")
    assert {r["code"]: r["count"] for r in plan["risks"]}["REJECT_PROVEN"] == 5
    res = store.commit(plan["plan_id"], confirm_count=5, acknowledge=True)
    assert store.summary()["totals"]["REJECTED"] == 5
    undo = store.plan("UNDO", {"mode": "batch", "batch_id": res["batch_id"]})
    assert undo["applicable"] == 5
    store.commit(undo["plan_id"], confirm_count=5, acknowledge=True)
    assert store.summary()["totals"]["REJECTED"] == 0 and store.summary()["totals"]["PROVEN"] == 20
    with pytest.raises(orv.ReviewError) as e:
        store.plan("UNDO", {"mode": "batch", "batch_id": res["batch_id"]})
    assert e.value.code == "ownership.already_undone"


def test_undo_skips_superseded_items(store):
    recs = load(store, n_attest=10)
    keys = [r["idempotency_key"] for r in recs[:10]]
    b1 = store.commit(store.plan("APPROVE", {"mode": "keys", "keys": keys})["plan_id"], confirm_count=10,
                      acknowledge=True)
    p = store.plan("REJECT", {"mode": "keys", "keys": keys[:3]})
    store.commit(p["plan_id"], confirm_count=3, acknowledge=True)
    undo = store.plan("UNDO", {"mode": "batch", "batch_id": b1["batch_id"]})
    assert undo["applicable"] == 7 and undo["superseded"] == 3
    store.commit(undo["plan_id"], confirm_count=7, acknowledge=True)
    states = {k: store.item(k)["review_state"] for k in keys}
    assert sum(1 for v in states.values() if v == "REJECTED") == 3
    assert sum(1 for v in states.values() if v == "PENDING") == 7


def test_stale_plan_is_refused(store):
    recs = load(store, n_attest=10)
    keys = [r["idempotency_key"] for r in recs[:10]]
    p1 = store.plan("APPROVE", {"mode": "keys", "keys": keys})
    p2 = store.plan("REJECT", {"mode": "keys", "keys": keys[:2]})
    store.commit(p2["plan_id"], confirm_count=2, acknowledge=True)
    with pytest.raises(orv.ReviewError) as e:
        store.commit(p1["plan_id"], confirm_count=10, acknowledge=True)
    assert e.value.code == "ownership.plan_stale"
    assert store.summary()["totals"]["ADMIN_ATTESTED"] == 0


def test_expired_and_unknown_plans(store, monkeypatch):
    load(store, n_attest=3)
    p = store.plan("APPROVE", {"mode": "filter", "filter": {"eligibility": "ATTESTABLE"}})
    monkeypatch.setattr(orv, "_now", lambda: int(time.time()) + orv.PLAN_TTL_SECONDS + 5)
    with pytest.raises(orv.ReviewError) as e:
        store.commit(p["plan_id"], confirm_count=3, acknowledge=True)
    assert e.value.code == "ownership.plan_expired"
    with pytest.raises(orv.ReviewError):
        store.commit("nope", confirm_count=0, acknowledge=True)


def test_bad_requests(store):
    load(store, n_attest=3)
    for action, sel in (("DELETE", {"mode": "filter"}), ("APPROVE", {"mode": "keys", "keys": []}),
                        ("APPROVE", {"mode": "keys", "keys": ["legacy:service-ownership:nope"]}),
                        ("APPROVE", {"mode": "sql", "where": "1=1"}), ("UNDO", {"mode": "filter"})):
        with pytest.raises(orv.ReviewError):
            store.plan(action, sel)
    with pytest.raises(orv.ReviewError):
        store.plan("APPROVE", {"mode": "filter"}, note="x" * (orv.MAX_NOTE + 1))


def test_audit_chain_detects_tampering(store, tmp_path):
    load(store, n_attest=5)
    p = store.plan("APPROVE", {"mode": "filter", "filter": {"eligibility": ["ATTESTABLE"]}})
    store.commit(p["plan_id"], confirm_count=5, acknowledge=True)
    v = store.verify_audit()
    assert v["ok"] and v["events"] == 6
    db = sqlite3.connect(str(store.path))
    db.execute("UPDATE audit SET to_state = 'REJECTED' WHERE seq = 2")
    db.commit()
    db.close()
    bad = store.verify_audit()
    assert bad["ok"] is False and bad["broken_at"] == 2


def test_seal_freezes_decisions(store):
    load(store, n_attest=5)
    total = store.summary()["totals"]["items"]
    with pytest.raises(orv.ReviewError):
        store.seal(confirm="SEAL", confirm_count=total - 1)
    with pytest.raises(orv.ReviewError):
        store.seal(confirm="seal", confirm_count=total)
    s = store.seal(confirm="SEAL", confirm_count=total)
    assert s["sealed"] is True
    for call in (lambda: store.plan("APPROVE", {"mode": "filter"}),
                 lambda: store.seal(confirm="SEAL", confirm_count=total)):
        with pytest.raises(orv.ReviewError) as e:
            call()
        assert e.value.code == "ownership.sealed"
    assert store.verify_audit()["ok"]


def test_changed_record_makes_decision_stale(store):
    recs = load(store, n_attest=4)
    keys = [r["idempotency_key"] for r in recs[:4]]
    store.commit(store.plan("APPROVE", {"mode": "keys", "keys": keys})["plan_id"], confirm_count=4, acknowledge=True)
    recs[0] = {**recs[0], "ownership_evidence_total": 99}  # newer conversion changed this record
    store.sync(recs, invoices(recs))
    it = store.item(keys[0])
    assert it["stale"] is True and it["final_class"] == "PENDING"
    assert store.summary()["totals"]["ADMIN_ATTESTED"] == 3 and store.summary()["stale"] == 1
    doc = store.export_document()
    e = next(x for x in doc["entries"] if x["key"] == keys[0])
    assert e["stale"] is True and e["class"] == "PENDING"
    # a record that disappears from a later conversion is not counted
    store.sync(recs[1:], invoices(recs))
    assert store.item(keys[0]) is None


def test_persistence_and_replay_after_restart(tmp_path):
    base = tmp_path / "reviews"
    s1 = orv.ReviewStore.for_import(base, "ef" * 32)
    recs = load(s1, n_attest=8)
    p = s1.plan("APPROVE", {"mode": "filter", "filter": {"eligibility": "ATTESTABLE"}})
    res = s1.commit(p["plan_id"], confirm_count=8, acknowledge=True)
    s1.close()
    s2 = orv.ReviewStore.for_import(base, "ef" * 32)
    s2.sync(recs, invoices(recs))
    assert s2.summary()["totals"]["ADMIN_ATTESTED"] == 8
    again = s2.commit(p["plan_id"], confirm_count=8, acknowledge=True)
    assert again["idempotent_replay"] is True and again["batch_id"] == res["batch_id"]
    s2.close()
    with pytest.raises(orv.ReviewError):
        orv.ReviewStore(base / ("ef" * 32 + ".sqlite"), import_id="00" * 32)
    with pytest.raises(orv.ReviewError):
        orv.ReviewStore.for_import(base, "../../etc")


def test_store_holds_no_customer_ids(store):
    load(store, n_attest=30)
    p = store.plan("APPROVE", {"mode": "filter", "filter": {}})
    store.commit(p["plan_id"], confirm_count=p["applicable"], acknowledge=True)
    store.close()
    raw = store.path.read_bytes() + (store.path.with_name(store.path.name + "-wal").read_bytes()
                                     if store.path.with_name(store.path.name + "-wal").exists() else b"")
    assert str(TG).encode()[:7] not in raw and b"svc1" not in raw


# ---- signed export --------------------------------------------------------------------------------

def test_export_signed_with_package_key(tmp_path, store):
    from conftest import make_backup, run

    zp, _ = make_backup(tmp_path, users=40)
    res = run(tmp_path, zp)
    load(store, n_attest=3)
    p = store.plan("APPROVE", {"mode": "filter", "filter": {"eligibility": "ATTESTABLE"}})
    store.commit(p["plan_id"], confirm_count=3, acknowledge=True)
    key = orv.decisions_key(res.package_path, key_text=res.key_text)
    doc = orv.sign_export(store.export_document(), key)
    assert doc["schema"] == orv.EXPORT_SCHEMA and doc["admin_attestation_is_proof"] is False
    assert doc["summary"]["ADMIN_ATTESTED"] == 3 and doc["summary"]["PROVEN"] == 20
    assert {e["basis"] for e in doc["entries"]} >= {"ADMIN_ATTESTATION", "EVIDENCE", "SYSTEM_QUARANTINE"}
    assert orv.verify_export(doc, key)
    tampered = json.loads(json.dumps(doc))
    tampered["entries"][0]["class"] = "PROVEN" if tampered["entries"][0]["class"] != "PROVEN" else "PENDING"
    assert not orv.verify_export(tampered, key)
    from mirza2nexa.nxpkg.crypto import generate_key_file

    with pytest.raises(WrongPackageKey):
        orv.decisions_key(res.package_path, key_text=generate_key_file())
    assert orv.decisions_key(res.package_path, key_text=res.key_text) == key


# ---- regressions from the independent review ------------------------------------------------------

def _commit(store, action, keys):
    p = store.plan(action, {"mode": "keys", "keys": keys})
    return store.commit(p["plan_id"], confirm_count=p["applicable"], acknowledge=True)


def test_undo_never_resurrects_a_decision_replaced_later(store):
    recs = load(store, n_attest=2)
    x = recs[0]["idempotency_key"]
    _commit(store, "APPROVE", [x])
    a = _commit(store, "REJECT", [x])          # batch A: APPROVED -> REJECTED
    _commit(store, "RESET", [x])
    _commit(store, "REJECT", [x])              # latest decision, made by another batch
    undo = store.plan("UNDO", {"mode": "batch", "batch_id": a["batch_id"]})
    assert undo["applicable"] == 0 and undo["superseded"] == 1
    assert store.item(x)["final_class"] == "REJECTED"

    y = recs[1]["idempotency_key"]
    b1 = _commit(store, "APPROVE", [y])
    _commit(store, "RESET", [y])
    _commit(store, "APPROVE", [y])             # same state as b1 set, but a later batch made it
    assert store.plan("UNDO", {"mode": "batch", "batch_id": b1["batch_id"]})["superseded"] == 1
    assert store.item(y)["final_class"] == "ADMIN_ATTESTED"


def test_two_stores_on_one_file_cannot_race(tmp_path):
    base = tmp_path / "reviews"
    a = orv.ReviewStore.for_import(base, "aa" * 32)
    recs = load(a, n_attest=3)
    b = orv.ReviewStore.for_import(base, "aa" * 32)
    k = recs[0]["idempotency_key"]
    pa = a.plan("APPROVE", {"mode": "keys", "keys": [k]})
    pb = b.plan("REJECT", {"mode": "keys", "keys": [k]})
    b.commit(pb["plan_id"], confirm_count=1, acknowledge=True)
    with pytest.raises(orv.ReviewError) as e:
        a.commit(pa["plan_id"], confirm_count=1, acknowledge=True)
    assert e.value.code == "ownership.plan_stale"
    pa2 = a.plan("APPROVE", {"mode": "keys", "keys": [recs[1]["idempotency_key"]]})
    b.seal(confirm="SEAL", confirm_count=b.summary()["totals"]["items"])
    with pytest.raises(orv.ReviewError) as e:
        a.commit(pa2["plan_id"], confirm_count=1, acknowledge=True)
    assert e.value.code == "ownership.sealed"
    assert a.verify_audit()["ok"]
    a.close()
    b.close()


def test_sealed_store_is_frozen_against_resync(store):
    recs = load(store, n_attest=3)
    _commit(store, "APPROVE", [recs[0]["idempotency_key"]])
    store.seal(confirm="SEAL", confirm_count=store.summary()["totals"]["items"])
    before = store.summary()["totals"]
    changed = [{**r, "ownership_evidence_total": 5} for r in recs]
    res = store.sync(changed, invoices(changed))
    assert res["sealed_divergence"] == len(recs)
    s = store.summary()
    assert s["totals"] == before and s["sealed_divergence"] == len(recs)
    doc = store.export_document()
    assert doc["matches_seal"] is True and doc["sealed_digest"] == doc["entries_digest"]
    assert store.verify_audit()["ok"]


def test_audit_replay_detects_rows_written_outside_the_chain(store):
    recs = load(store, n_attest=3)
    _commit(store, "APPROVE", [recs[0]["idempotency_key"]])
    db = sqlite3.connect(str(store.path))
    db.execute("INSERT INTO review (key, state, batch_id, binding, updated_at) VALUES (?, 'APPROVED', 'b-x', ?, 0)",
               (recs[1]["idempotency_key"], orv.binding(recs[1])))
    db.commit()
    v = store.verify_audit()
    assert v["ok"] is False and v["reason"] == "state_mismatch"
    db.execute("DELETE FROM review WHERE batch_id = 'b-x'")
    db.commit()
    assert store.verify_audit()["ok"]
    store.seal(confirm="SEAL", confirm_count=store.summary()["totals"]["items"])
    db.execute("DELETE FROM audit WHERE action = 'SEAL'")  # truncating the seal is detected
    db.commit()
    db.close()
    assert store.verify_audit()["reason"] == "seal_anchor"


def test_plan_breaks_down_reasons_and_stores_only_known_filter_fields(store):
    recs = [rec(i, reasons=("OWNERSHIP_OWNER_MISMATCH_NO_TRANSFER_RECORD",)) for i in range(4)]
    recs += [rec(10 + i) for i in range(2)]
    store.sync(recs, invoices(recs))
    p = store.plan("APPROVE", {"mode": "filter", "filter": {"eligibility": ["ATTESTABLE"], "junk": "x" * 50},
                               "exclude_keys": []})
    assert p["applicable_by_reason"] == {"OWNERSHIP_ACTION_OUTSIDE_HOLDING_WINDOW": 2,
                                         "OWNERSHIP_OWNER_MISMATCH_NO_TRANSFER_RECORD": 4}
    res = store.commit(p["plan_id"], confirm_count=6, acknowledge=True)
    sel = sqlite3.connect(str(store.path)).execute("SELECT selection FROM batches WHERE batch_id = ?",
                                                   (res["batch_id"],)).fetchone()[0]
    assert "junk" not in sel and json.loads(sel)["filter"] == {"eligibility": ["ATTESTABLE"]}

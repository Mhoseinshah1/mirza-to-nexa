"""Migration readiness report and review taxonomy (synthetic data)."""

from __future__ import annotations

import json
import re

import pytest

from conftest import make_backup, read_jsonl, run
from mirza2nexa.review_taxonomy import ACTIONABILITY, GROUPS_FA, classify, summarise


@pytest.fixture(scope="module")
def converted(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("ready")
    zp, _ = make_backup(tmp, users=400)
    res = run(tmp, zp)
    readiness = json.loads((tmp / "ws" / "out" / "migration-readiness.json").read_text(encoding="utf-8"))
    return res, readiness


def test_readiness_sections_and_verdict(converted):
    res, rd = converted
    for key in ("verdict", "package_health", "financial", "coverage", "ownership", "panels", "review",
                "owner_decisions", "import_blockers"):
        assert key in rd, key
    assert rd["verdict"]["code"] in {"NOT_READY_FOR_IMPORT", "READY_FOR_IMPORTER_DRY_RUN", "BLOCKED"}
    # 'ready' is never an import permission while the NEXA importer does not exist
    assert any(b["code"] == "IMPORTER_NOT_BUILT" for b in rd["import_blockers"])
    assert rd["package_health"]["readiness"] == res.report["readiness"]
    assert rd["financial"]["balanced"] is True
    assert rd["coverage"]["tables_total"] == len(res.report["coverage"])
    ids = [d["id"] for d in rd["owner_decisions"]]
    assert "OD-MONEY-UNIT" in ids and "OD-EXISTING-NEXA-CUSTOMERS" in ids


def test_readiness_is_inside_the_package(converted):
    res, rd = converted
    inside = json.loads((res.stage_dir / "reports" / "migration-readiness.json").read_text(encoding="utf-8"))
    assert inside["verdict"] == rd["verdict"]
    assert (res.stage_dir / "reports" / "review-taxonomy.json").exists()


def test_readiness_has_no_pii(converted):
    _, rd = converted
    text = json.dumps(rd, ensure_ascii=False).replace(rd["import_id"], "")
    # aggregate totals (sums over all customers) are allowed; anything else id-like is not
    text = re.sub(r'"(amount_minor|net_minor|[a-z_]*_value_minor)": "-?\d+"', '"x": ""', text)
    assert not re.search(r"\d{7,}", text), "telegram-id-like numbers leaked"
    assert "synthetic_user_" not in text and "@" not in text


def test_review_totals_match_queue(converted):
    res, rd = converted
    items = read_jsonl(res.stage_dir / "reports" / "review-queue.jsonl")
    assert rd["review"]["total_items"] == len(items) == res.report["review_count"]
    assert sum(g["items"] for g in rd["review"]["by_group"]) == len(items)
    for g in rd["review"]["by_group"]:
        assert g["items"] == g["resolvable_by_code"] + g["archive_only"] + g["owner_decision"] + g["adoption_time"]


def test_every_produced_code_has_an_explicit_rule(converted):
    """New review codes must be classified deliberately, not by fallback."""
    _, rd = converted
    assert rd["review"]["codes_without_explicit_rule"] == {}


def test_taxonomy_unit():
    assert classify("ORPHAN")[:2] == ("invalid_owner", "OWNER_DECISION")
    assert classify("OWNERSHIP_X")[0] == "ownership_transfer"
    g, a, explicit = classify("TOTALLY_NEW_CODE")
    assert (g, a, explicit) == ("uncategorized", "OWNER_DECISION", False)
    s = summarise([{"code": "ORPHAN", "source_ref": {"table": "invoice", "pk": "a"}},
                   {"code": "NO_PANEL", "source_ref": {"table": "invoice", "pk": "a"}},
                   {"code": "TOTALLY_NEW_CODE", "source_ref": {"table": "x", "pk": None}}])
    assert s["total_items"] == 3 and s["distinct_records"] == 2
    assert s["codes_without_explicit_rule"] == {"TOTALLY_NEW_CODE": 1}
    assert set(GROUPS_FA) >= {g["group"] for g in s["by_group"]}
    assert set(s["by_actionability"]) == set(ACTIONABILITY)


def test_review_blockers_and_wallet_mismatch_block_the_verdict():
    from mirza2nexa.readiness import build

    tax = summarise([{"code": "SERVICE_OWNER_CONFLICT", "severity": "blocker",
                      "source_ref": {"table": "invoice", "pk": "a"}}])
    report = {"readiness": "ready", "catalogue": {"ok": True},
              "reconciliation": {"wallet": {"opening_credits": {"amount_minor": "100"},
                                            "legacy_debts": {"amount_minor": "30"}},
                                 "wallet_differences": {"total": 1, "by_certainty": {"UNEXPLAINED": 1}}}}
    rd = build(report, tax, package_contract_version="1.2.0")
    assert rd["review"]["blocker_items"] == 1
    assert {"REVIEW_BLOCKERS_OPEN", "OWNER_DECISIONS_PENDING"} <= {b["code"] for b in rd["import_blockers"]}
    assert next(d for d in rd["owner_decisions"] if d["id"] == "OD-WALLET-DIFFERENCES")["blocking"] is True
    assert rd["financial"]["net_minor"] == "70"


def test_malformed_amount_is_reported_not_zeroed():
    from mirza2nexa.readiness import build

    report = {"readiness": "ready", "catalogue": {"ok": True},
              "reconciliation": {"wallet": {"opening_credits": {"amount_minor": "12.5"},
                                            "legacy_debts": {"amount_minor": "0"}}}}
    rd = build(report, summarise([]), package_contract_version="1.2.0")
    assert rd["financial"]["net_minor"] is None and rd["financial"]["amounts_valid"] is False
    assert any(b["code"] == "AMOUNT_UNREADABLE" for b in rd["import_blockers"])
    assert rd["verdict"]["code"] != "READY_FOR_IMPORTER_DRY_RUN"

"""Reseller (agent) archive module on synthetic backups — never real data."""

from __future__ import annotations

import json

import pytest

from conftest import make_backup, read_jsonl, run
from mirza2nexa.modules.agents import parse_time
from mirza2nexa.synth import TG_BASE

AGENT_FILES = ("profiles", "agent_invoices", "agent_logs", "agent_usage", "tier_prices", "agent_requests")
AGENT_TABLES = ("agent_manage", "agent_invoice", "agent_log", "agent_usage_snapshot", "custom_service_tier_prices",
                "Requestagent", "requestagent")


def recs(res, name: str) -> list[dict]:
    return read_jsonl(res.stage_dir / "records" / "agents" / f"{name}.jsonl")


def review(res) -> list[dict]:
    return read_jsonl(res.stage_dir / "reports" / "review-queue.jsonl")


@pytest.fixture(scope="module")
def conv(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("agents")
    zp, _ = make_backup(tmp, users=300)
    return run(tmp, zp)


def test_default_synthetic_backup_has_no_blockers(conv):
    assert conv.report["readiness"] == "ready", conv.report["blockers"]


def test_profiles_one_per_agent_and_never_activate(conv):
    profiles = recs(conv, "profiles")
    rec = conv.report["reconciliation"]["agents"]
    expected_user_agents = len(range(0, 300, 53))  # synth: every 53rd user is tier "n"
    assert rec["user_table_agents"] == expected_user_agents
    assert rec["profiles"]["from_user_table"] == expected_user_agents
    keys = [p["idempotency_key"] for p in profiles]
    assert len(keys) == len(set(keys)) == rec["profiles"]["total"]
    for p in profiles:
        assert p["activates_role"] is False and p["grants_credit"] is False
        assert p["affects_wallet"] is False and p["applies_to_live_state"] is False
        assert p["nexa_role"] is None and p["limits_and_credit"] == "RAW_ONLY_NOT_APPLIED"
        assert p["idempotency_key"].startswith("legacy:")
        assert p["provenance"]["source_fingerprint"] == conv.report["source_fingerprint"]


def test_agent_not_a_customer_is_kept_and_reviewed(conv):
    ghost = [p for p in recs(conv, "profiles") if p["source_pk"] == "5000000400"]
    assert len(ghost) == 1
    g = ghost[0]
    assert g["tier"] == "NOT_AGENT_IN_USER_TABLE"
    assert g["customer"]["valid"] is False and g["customer"]["decision"] == "NOT_IN_BACKUP"
    assert g["agent_manage"] and g["agent_manage"][0]["fields_raw"]["user_id"] == "5000000400"
    assert any(r["code"] == "AGENT_NOT_CUSTOMER" and r["source_ref"]["pk"] == "5000000400" for r in review(conv))
    assert conv.report["reconciliation"]["agents"]["profiles"]["agent_not_customer"] == 1


def test_agent_manage_secret_column_never_staged(conv):
    cov = {c["table"]: c for c in conv.report["coverage"]}
    assert "bot_token" in cov["agent_manage"]["dropped_secret_columns"]
    blob = b"".join(p.read_bytes() for p in conv.stage_dir.rglob("*") if p.is_file())
    assert b"never-staged-token" not in blob


def test_agent_with_several_price_levels_is_not_collapsed(conv):
    levels = recs(conv, "tier_prices")
    first_agent = str(TG_BASE)
    mine = sorted((r for r in levels if r["owner_value_raw"] == first_agent), key=lambda r: r["level_index"])
    assert [r["level_index"] for r in mine] == [1, 2, 3]
    assert {r["levels_for_owner"] for r in mine} == {3}
    assert [r["fields_raw"]["price_per_gb"] for r in mine] == ["2000", "1800", "1500"]
    assert all(r["applies_price"] is False and r["grants_credit"] is False for r in levels)
    pl = conv.report["reconciliation"]["agents"]["price_levels"]
    assert pl["owners_with_multiple_levels"] == 1 and pl["levels_histogram"]["3"] == 1
    assert pl["rows"] == len(levels) == 5


def test_agent_requests_both_tables(conv):
    reqs = recs(conv, "agent_requests")
    by_table: dict[str, list[dict]] = {}
    for r in reqs:
        by_table.setdefault(r["source_table"], []).append(r)
    assert len(by_table["Requestagent"]) == 3 and len(by_table["requestagent"]) == 1
    statuses = sorted(r["status"] for r in by_table["Requestagent"])
    assert statuses == ["ACCEPTED", "REJECTED", "WAITING"]
    accepted = next(r for r in by_table["Requestagent"] if r["status"] == "ACCEPTED")
    assert accepted["granted_tier"] == "n" and accepted["requested_at_note"] == "OK_UNIX"
    assert by_table["requestagent"][0]["status"] == "UNKNOWN"


def test_description_text_not_in_reports(conv):
    report = conv.report_path.read_text(encoding="utf-8")
    rq = (conv.stage_dir / "reports" / "review-queue.jsonl").read_text(encoding="utf-8")
    for text in ("می‌خواهم نماینده شوم", "فروش سرویس آزمایشی"):
        assert text not in report and text not in rq
    # but the archive keeps it inside the package
    assert any("نماینده" in (r.get("description") or "") for r in recs(conv, "agent_requests"))


def test_coverage_accounts_every_agent_row(conv):
    cov = {c["table"]: c for c in conv.report["coverage"]}
    for t in AGENT_TABLES:
        c = cov[t]
        assert c["rows"] > 0, t
        assert c["accounted_rows"] == c["rows"], t
        assert c["status"] in {"archived", "review"}, t


def test_no_activation_flag_is_true_anywhere(conv):
    for name in AGENT_FILES:
        for r in recs(conv, name):
            for flag in ("activates_role", "grants_credit", "affects_wallet", "applies_to_live_state"):
                assert r[flag] is False, (name, flag)
            assert r["record_type"] and r["schema"] and r["provenance"]
    rec = conv.report["reconciliation"]["agents"]
    assert rec["activates_role_true"] == 0 and rec["grants_credit_true"] == 0


def test_agents_deterministic(tmp_path):
    zp, _ = make_backup(tmp_path, users=120)
    a = run(tmp_path, zp, ws_name="a")
    b = run(tmp_path, zp, ws_name="b")
    for name in AGENT_FILES:
        pa = a.stage_dir / "records" / "agents" / f"{name}.jsonl"
        pb = b.stage_dir / "records" / "agents" / f"{name}.jsonl"
        assert pa.read_bytes() == pb.read_bytes(), name
    assert json.dumps(a.report["reconciliation"]["agents"], sort_keys=True) == \
        json.dumps(b.report["reconciliation"]["agents"], sort_keys=True)


@pytest.mark.parametrize(("raw", "note"), [
    ("1700000000", "OK_UNIX"), ("2024/07/01 10:00:00", "OK_TEHRAN_LOCAL"), ("1403/04/12 08:00:00", "JALALI_SUSPECTED"),
    ("2025/13/40", "FORMAT_UNKNOWN"), ("", "EMPTY"), (None, "EMPTY"), ("yesterday", "FORMAT_UNKNOWN"),
    ("99", "FORMAT_UNKNOWN"),
])
def test_parse_time_notes(raw, note):
    assert parse_time(raw)[1] == note

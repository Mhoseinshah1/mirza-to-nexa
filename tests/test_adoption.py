"""adoption module: panel mapping template, service status classes, product mapping proposals (synthetic only)."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from conftest import make_backup, read_jsonl, run
from mirza2nexa.mapping import rules
from mirza2nexa.modules import adoption as ad
from mirza2nexa.modules import services_ext as sx
from mirza2nexa.synth import SynthSpec, write_dump

LIVE_FLAGS = ("provision", "applies_to_live_state", "auto_create", "sellable")
SECRET_FIELDS = ("url_panel", "username_panel", "password_panel", "secret_code", "datelogin", "linksubx",
                 "proxies", "inbounds", "inboundid", "inbound_deactive")


def records(res, name: str) -> list[dict]:
    return read_jsonl(res.stage_dir / "records" / f"{name}.jsonl")


@pytest.fixture(scope="module")
def default(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("adoption")
    zp, _ = make_backup(tmp, users=300, password="pw-Ab12345")
    return run(tmp, zp, password="pw-Ab12345")


def _zip(tmp: Path, extra_sql: str, name: str) -> Path:
    buf = io.StringIO()
    write_dump(buf, SynthSpec(users=40, modules=False, invoices_per_user=1.0))
    buf.write(extra_sql)
    path = tmp / name
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("backup.sql", buf.getvalue().encode("utf-8"))
    return path


# ---- status classes ------------------------------------------------------------------------

@pytest.mark.parametrize(("status", "is_test", "klass"), [
    ("active", "0", "ACTIVE"), ("send_on_hold", "0", "ACTIVE"),
    ("end_of_time", "0", "EXPIRED"), ("end_of_volume", "0", "EXPIRED"), ("sendedwarn", "0", "EXPIRED"),
    ("disabled", "0", "DISABLED"), ("disabledn", "0", "DISABLED"), ("disablebyadmin", "0", "DISABLED"),
    ("removebyuser", "0", "REMOVED"), ("removebyadmin", "0", "REMOVED"), ("removedbyadmin", "0", "REMOVED"),
    ("removeTime", "0", "REMOVED"), ("removevolume", "0", "REMOVED"),
    ("unpaid", "0", "NOT_PROVISIONED"), ("Unsuccessful", "0", "NOT_PROVISIONED"),
    ("active", "1", "TEST"), ("removebyuser", "1", "TEST"),
    ("removed", "0", "UNKNOWN"), ("Active", "0", "UNKNOWN"), (None, "0", "UNKNOWN"), ("expired", None, "UNKNOWN"),
])
def test_status_class(status, is_test, klass):
    assert ad.status_class(status, is_test) == klass


def test_every_nexa_live_status_has_an_evidenced_class():
    assert all(ad.STATUS_CLASS.get(s) not in (None, "UNKNOWN") for s in rules.LIVE_STATUSES)


def test_breakdown_accounts_every_invoice_and_explains_core_skips(default):
    rec = default.report["reconciliation"]
    b = rec["service_status_breakdown"]
    assert set(b["by_class"]) >= {"ACTIVE", "TEST", "EXPIRED", "DISABLED", "REMOVED", "UNKNOWN"}
    assert sum(b["by_class"].values()) == b["invoices"] == default.report["source"]["tables"]["invoice"]["rows"]
    # every core-skipped service is a TEST invoice
    skipped = b["by_core_decision_and_class"].get("TEST_INVOICE_SKIPPED", {})
    assert set(skipped) <= {"TEST"} and sum(skipped.values()) == b["core_skipped_explained"]["TEST_INVOICE_SKIPPED"]
    assert sum(skipped.values()) == rec["services"]["decisions"].get("TEST_INVOICE_SKIPPED", 0) > 0
    # live core decisions in the matrix add up to the core's live invoice count
    live = sum(n for d, cls in b["by_core_decision_and_class"].items() if d != ad.NOT_LIVE for n in cls.values())
    assert live == rec["services"]["live_invoices"]
    # the synthetic 'removed' status has no writer in the public code → UNKNOWN, counted
    assert b["unknown"]["status_values_without_writer"] >= b["by_class"]["UNKNOWN"] > 0  # TEST wins over status
    assert b["live_set_divergence"]["mirza_serviceable_not_nexa_live"].get("end_of_time", 0) > 0


# ---- panels ----------------------------------------------------------------------------------

def test_panel_template_shape_and_no_secrets(default):
    rows = records(default, "panel_mapping_template")
    assert rows
    kinds = {r["identity_kind"] for r in rows}
    assert {"REGISTRY", "INVOICE_CODE_NOT_IN_REGISTRY"} <= kinds
    for r in rows:
        assert r["nexa_panel_id"] is None and r["decision"] == "PENDING_OWNER"
        assert r["credentials"] == "ENTER_IN_NEXA_SECRET_FLOW" and r["credentials_in_package"] is False
        assert all(r[f] is False for f in ("provision", "applies_to_live_state"))
        assert not set(SECRET_FIELDS) & set(r)
    blob = (default.stage_dir / "records" / "panel_mapping_template.jsonl").read_text(encoding="utf-8")
    for planted in sx.SYNTH_SECRETS:
        assert planted not in blob
    pm = default.report["reconciliation"]["panel_mapping"]
    assert pm["mapped"] == 0 and pm["pending_mapping"] == len(rows) and pm["credentials_exported"] is False
    assert pm["registry_rows"] == 4 and pm["codes_in_invoices"] >= 2
    syn_old = next(r for r in rows if r["code_panel"] == "syn-old")
    assert syn_old["suggested_map_section"] == "NONE_NEEDED_NO_INVOICES"


def test_planted_credentials_never_leave_any_output(default):
    for path in default.stage_dir.rglob("*"):
        if path.is_file() and path.suffix in (".json", ".jsonl", ".csv", ".txt", ".md"):
            text = path.read_text(encoding="utf-8", errors="replace")
            leaked = [s for s in sx.SYNTH_SECRETS if s in text]
            assert not leaked, (path.name, leaked)


def test_renamed_panel_and_location_only_identity(tmp_path):
    panels = "DROP TABLE IF EXISTS `marzban_panel`;\n" + sx.DDL["marzban_panel"] + \
        "INSERT INTO `marzban_panel` VALUES " + ",".join([
            sx._panel_row(1, "syn-a", "سرور آزمایشی", "marzban"), sx._panel_row(2, "syn-b", "پنل جدید", "marzban")]) + ";\n"
    res = run(tmp_path, _zip(tmp_path, panels, "p.zip"))
    rows = records(res, "panel_mapping_template")
    pm = res.report["reconciliation"]["panel_mapping"]
    assert pm["registry_rows"] == 2
    # invoices still carry the old name 'سرور آزمایشی' for syn-b → no location-only row for it, but syn-b's
    # name 'پنل جدید' is used by no invoice; syn-z (anomaly) is a code missing from the registry.
    assert "syn-z" in pm["codes_in_invoices_missing_from_registry"]
    b = next(r for r in rows if r["code_panel"] == "syn-b")
    assert b["invoices_by_code_panel"] and not b["invoices_by_service_location_name"]
    blob = json.dumps(rows, ensure_ascii=False)
    assert not any(s in blob for s in sx.SYNTH_SECRETS)


# ---- products ------------------------------------------------------------------------------

def test_product_proposals_never_sellable(default):
    rows = records(default, "product_mapping_proposal")
    assert len(rows) == default.report["source"]["tables"]["product"]["rows"]
    for r in rows:
        assert r["auto_create"] is False and r["sellable"] is False and r["provision"] is False
        assert r["proposed_nexa_catalog"]["status"] == "INACTIVE"
        assert r["proposed_nexa_catalog"]["price"] is None and r["proposed_nexa_catalog"]["panel_id"] is None
        assert r["proposal_status"] == "READY_FOR_OWNER_REVIEW" or r["proposal_status"].startswith("BLOCKED_")
    ok = next(r for r in rows if r["code_product"] == "p002")
    assert ok["conversions"]["traffic"]["proposed_traffic_bytes"] == str(20 * 1024 ** 3)
    assert ok["conversions"]["duration"]["proposed_duration_days"] == 30
    dup = [r for r in rows if r["code_product"] == "p001"]
    assert len(dup) == 2 and all(r["proposal_status"] == "BLOCKED_CODE_DUPLICATED" for r in dup)
    pmap = default.report["reconciliation"]["product_mapping"]
    assert pmap["auto_created"] == 0 and pmap["sellable"] == 0 and pmap["rows"] == len(rows)


def test_zero_volume_and_time_mean_unlimited():
    # documented, evidence-backed rule: 0 → NEXA's own unlimited encoding (catalog.ts:210,212)
    assert rules.parse_traffic_gb("0") == 0


# ---- determinism / coverage / readiness -----------------------------------------------------

def test_deterministic(tmp_path):
    outs = []
    for i in range(2):
        d = tmp_path / f"r{i}"
        d.mkdir()
        zp, _ = make_backup(d, users=120)
        res = run(d, zp)
        outs.append({n: (res.stage_dir / "records" / f"{n}.jsonl").read_bytes()
                     for n in ("panel_mapping_template", "product_mapping_proposal")}
                    | {"rec": json.dumps({k: res.report["reconciliation"][k] for k in
                                          ("panel_mapping", "service_status_breakdown", "product_mapping")},
                                         sort_keys=True).encode()})
    assert outs[0] == outs[1]


def test_coverage_unaffected_and_package_ready(default):
    assert default.report["readiness"] == "ready", default.report["blockers"]
    cov = {c["table"]: c for c in json.loads((default.stage_dir / "reports" / "coverage.json")
                                             .read_text(encoding="utf-8"))["tables"]}
    for t in ("invoice", "product", "marzban_panel", "category"):
        assert cov[t]["accounted_rows"] == cov[t]["rows"], (t, cov[t])


def test_product_ready_when_scope_resolves(tmp_path):
    panels = "DROP TABLE IF EXISTS `marzban_panel`;\n" + sx.DDL["marzban_panel"] + \
        "INSERT INTO `marzban_panel` VALUES " + sx._panel_row(1, "syn-a", "syn-loc", "marzban") + ";\n"
    res = run(tmp_path, _zip(tmp_path, panels, "r.zip"))
    rows = {r["code_product"]: r for r in records(res, "product_mapping_proposal") if r["code_product"] != "p001"}
    p2 = rows["p002"]
    assert p2["proposal_status"] == "READY_FOR_OWNER_REVIEW", p2["blocking_reasons"]
    assert p2["conversions"]["panel_scope"]["resolved_code_panels"] == ["syn-a"]
    assert p2["sellable"] is False and p2["auto_create"] is False
    assert "NEXA_TARIFF_PRICE" in p2["owner_decisions_required"]

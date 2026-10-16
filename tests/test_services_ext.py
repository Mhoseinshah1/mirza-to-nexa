"""services_ext module: service_other, cancel_service, manualsell, marzban_panel, category, vs_* (synthetic only)."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from conftest import make_backup, read_jsonl, run
from mirza2nexa.modules import services_ext as sx
from mirza2nexa.nxpkg import PackageReader
from mirza2nexa.synth import TG_BASE, SynthSpec, write_dump

MY_FILES = ("service_operations", "service_cancellation_requests", "manual_config_inventory", "panel_registry",
            "category_catalogue")
MY_TABLES = ("service_other", "cancel_service", "manualsell", "marzban_panel", "category", *sx.VS_TABLES)
LIVE_FLAGS = ("provision", "applies_to_live_state", "creates_payment", "affects_wallet", "auto_create")


def records(res, name: str) -> list[dict]:
    return read_jsonl(res.stage_dir / "records" / f"{name}.jsonl")


def review(res) -> list[dict]:
    return read_jsonl(res.stage_dir / "reports" / "review-queue.jsonl")


def by_code(res, code: str) -> list[dict]:
    return [r for r in review(res) if r["code"] == code]


@pytest.fixture(scope="module")
def default(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("svcext")
    zp, exp = make_backup(tmp, users=300, password="pw-Ab12345")
    res = run(tmp, zp, password="pw-Ab12345")
    return res, tmp


def _custom_zip(tmp: Path, extra_sql: str, *, users: int = 40, name: str = "c.zip") -> Path:
    buf = io.StringIO()
    write_dump(buf, SynthSpec(users=users, modules=False, invoices_per_user=1.0))
    buf.write(extra_sql)
    path = tmp / name
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("backup.sql", buf.getvalue().encode("utf-8"))
    return path


def _so_rows(rows: list[tuple]) -> str:
    vals = ",".join("(" + ",".join(sx.sql_str(None if v is None else str(v)) for v in r) + ")" for r in rows)
    return "DROP TABLE IF EXISTS `service_other`;\n" + sx.DDL["service_other"] + \
        (f"INSERT INTO `service_other` VALUES {vals};\n" if rows else "")


def _not_ownership_scenario(o: dict) -> bool:
    """Rows appended by the ownership synth hook (ids >= SCENARIO_BASE_ID) are tested in test_ownership.py."""
    from mirza2nexa.modules.ownership import SCENARIO_BASE_ID

    return o["legacy_id"] is None or not o["legacy_id"].isdigit() or int(o["legacy_id"]) < SCENARIO_BASE_ID


def _invoice_of(res, username: str) -> dict:
    return next(r for r in records(res, "invoices") if r.get("panel_account_username", r.get("username")) == username)


# ---- default synthetic backup ----------------------------------------------------------

def test_default_backup_stays_free_of_package_blockers(default):
    res, _ = default
    assert res.report["readiness"] == "ready", res.report["blockers"]
    warn = {w["code"] for w in res.report["warnings"]}
    assert {"SERVICE_OPERATIONS_HISTORY_ONLY", "PANEL_CREDENTIALS_NOT_EXPORTED"} <= warn


def test_service_other_relates_to_invoices_by_username(default):
    res, _ = default
    ops = records(res, "service_operations")
    rec = res.report["reconciliation"]["service_operations"]
    assert rec["rows"] == len(ops) and rec["accounted"]
    found = [o for o in ops if o["invoice_relation"]["state"] == "FOUND"]
    assert len(found) > 0.9 * len(ops)
    for o in ops:
        assert o["record_type"] == "legacy_service_operation"
        assert o["schema"] == "mirza.service_other.v1"
        assert o["idempotency_key"] == f"legacy:service-op:{o['legacy_id']}"
        assert o["provenance"]["source_table"] == "service_other"
        if o["invoice_relation"]["state"] == "FOUND":
            assert o["invoice_relation"]["invoice_key"]
        if o["owner_relation"] == "MATCHES_INVOICE" and _not_ownership_scenario(o):
            assert o["owner"]["customer_decision"] == "IMPORT"
    assert len({o["idempotency_key"] for o in ops}) == len(ops)


def test_money_is_raw_and_never_applied(default):
    res, _ = default
    for o in records(res, "service_operations"):
        a = o["amount"]
        assert a["applied"] is False and "semantics" in a
        if a["parse_note"] == "OK":
            assert a["amount_minor"] == a["raw"].strip() and a["currency"] == "IRT"
        else:
            assert a["amount_minor"] is None and a["raw"] is not None
    odd = next(o for o in records(res, "service_operations") if o["operation_type"]["raw"] == "mystery_op")
    assert odd["operation_type"]["normalized"] == "UNKNOWN"
    assert odd["amount"]["raw"] == "12,500" and odd["amount"]["parse_note"] == "NOT_A_NUMBER"
    assert {"SERVICE_OP_TYPE_UNKNOWN", "SERVICE_OP_AMOUNT_UNREADABLE"} <= set(odd["review_codes"])


def test_owner_conflict_transfer_and_ambiguity(default):
    res, _ = default
    all_ops = records(res, "service_operations")
    ops = [o for o in all_ops if _not_ownership_scenario(o)]
    conflicts = [o for o in ops if o["owner_relation"] == "CONFLICT"]
    assert len(conflicts) == 1
    assert conflicts[0]["ownership_reason"] == "OWNERSHIP_OWNER_MISMATCH_NO_TRANSFER_RECORD"
    rv = [r for r in by_code(res, "SERVICE_OWNER_CONFLICT")
          if r["source_ref"]["table"] == "service_other" and r["source_ref"]["pk"] == conflicts[0]["legacy_id"]]
    assert len(rv) == 1 and rv[0]["severity"] == "blocker"
    assert res.report["reconciliation"]["service_operations"]["owner_conflicts"] == \
        sum(o["owner_relation"] == "CONFLICT" for o in all_ops)
    # an op by the previous owner, explained by a recorded transfertouser row
    assert sum(o["owner_relation"] == "PREVIOUS_OWNER_VIA_TRANSFER" for o in ops) == 2
    # 'shared' is sold to two different owners (core AMBIGUOUS_OWNERSHIP); a third owner's op cannot be placed
    amb = [o for o in ops if o["invoice_relation"]["state"] == "AMBIGUOUS"]
    assert len(amb) == 1 and amb[0]["invoice_relation"]["candidates"] == 2
    assert by_code(res, "SERVICE_OP_INVOICE_AMBIGUOUS")[0]["severity"] == "blocker"
    nf = [o for o in ops if o["invoice_relation"]["state"] == "NOT_FOUND"]
    assert [o["panel_account_username"] for o in nf] == ["ghost_user_1"]


def test_admin_renewal_actor_is_not_an_owner(default):
    res, _ = default
    adm = [o for o in records(res, "service_operations") if o["operation_type"]["raw"] == "extend_user_by_admin"]
    assert adm
    for o in adm:
        assert o["owner_relation"] == "NOT_AN_OWNER_FIELD"
        assert o["owner"]["role"] == "ACTOR_ADMIN_OR_OWNER_UNPROVEN"
        assert "SERVICE_OWNER_CONFLICT" not in o["review_codes"]


def test_panel_secrets_never_leave(default, tmp_path):
    res, _ = default
    planted = [s.encode() for s in (*sx.SYNTH_SECRETS, "SYNTH-VS-STOCK-LINK", "AAEXAMPLEEXAMPLE")]
    blob = b"".join(p.read_bytes() for p in res.stage_dir.rglob("*") if p.is_file())
    blob += res.report_path.read_bytes() + json.dumps(res.report, ensure_ascii=False).encode()
    reader = PackageReader(res.package_path, key=res.key_text, workdir=tmp_path / "v")
    reader.verify()
    with zipfile.ZipFile(reader.payload_path) as zf:
        payload = b"".join(zf.read(n) for n in zf.namelist())
    assert b"panel_registry" in payload
    # the staging database (whole workspace) never held them either
    ws = res.stage_dir.parent
    staged = b"".join(p.read_bytes() for p in ws.rglob("*") if p.is_file() and p.suffix != ".nxpkg")
    for s in planted:
        assert s not in blob, s
        assert s not in payload, s
        assert s not in staged, s
    cov = {c["table"]: c for c in res.report["coverage"]}
    assert set(sx.PANEL_SECRET_COLUMNS) <= set(cov["marzban_panel"]["dropped_secret_columns"])
    assert set(cov["service_other"]["dropped_secret_columns"]) == {"output", "bottype", "panel_response"}
    assert cov["manualsell"]["dropped_secret_columns"] == ["contentrecord"]
    assert "api_token" in cov["vs_package"]["dropped_secret_columns"]


def test_panel_registry_records_and_missing_panels(default):
    res, _ = default
    panels = {p["code_panel"]: p for p in records(res, "panel_registry")}
    assert sorted(panels) == ["syn-a", "syn-b", "syn-m", "syn-old"]
    for p in panels.values():
        assert p["record_type"] == "legacy_panel"
        assert p["idempotency_key"] == f"legacy:panel-registry:{p['legacy_id']}"
        assert p["credentials_required_separately"] is True and p["credentials_in_package"] is False
        assert not any(c in p["settings_raw"] for c in sx.PANEL_SECRET_COLUMNS)
        assert p["provision"] is False and p["nexa_panel_id"] is None
    assert panels["syn-a"]["invoice_links"]["by_code_panel"] > 0
    assert panels["syn-a"]["panel_type"]["normalized"] == "marzban"
    assert panels["syn-m"]["manual_stock_rows"] == 6
    rec = res.report["reconciliation"]["panel_registry"]
    assert [m["code_panel"] for m in rec["invoice_codes_missing_from_registry"]] == ["syn-z"]
    assert rec["registry_codes_without_invoices"] == ["syn-m", "syn-old"]
    assert [r["source_ref"]["pk"] for r in by_code(res, "PANEL_NOT_IN_REGISTRY")] == ["syn-z"]
    assert {r["data"]["code_panel"] for r in by_code(res, "PANEL_WITHOUT_INVOICES")} == {"syn-m", "syn-old"}


def test_category_mapping_and_products_never_auto_created(default):
    res, _ = default
    cats = records(res, "category_catalogue")
    assert len(cats) == 7
    assert all(c["auto_create"] is False and c["idempotency_key"].startswith("legacy:category-table:") for c in cats)
    by_label = {c["label"]: c for c in cats}
    assert by_label["عمومی"]["product_links"]["exact"] > 0
    assert by_label[" ویژه "]["product_links"] ["exact"] == 0
    assert by_label[" ویژه "]["product_links"]["normalized"] > 0
    rec = res.report["reconciliation"]["category_catalogue"]
    assert rec["matched_exact"] + rec["matched_normalized_only"] + rec["unmatched_products"] == \
        rec["products_with_category"]
    assert rec["unmatched_products"] == 0 and rec["categories_without_products"] == 5
    assert all(p["auto_create"] is False for p in records(res, "products"))
    assert all(c["auto_create"] is False for c in records(res, "categories"))


def test_cancellations_and_manual_inventory(default):
    res, _ = default
    cx = records(res, "service_cancellation_requests")
    assert {c["status"]["normalized"] for c in cx} == {"PENDING", "ACCEPTED", "REJECTED"}
    for c in cx:
        assert c["refund_amount"] is None and c["refund_amount_note"] == "NOT_STORED_IN_SOURCE"
        assert c["idempotency_key"] == f"legacy:cancel-service:{c['legacy_id']}"
    ms = records(res, "manual_config_inventory")
    assert all(m["config_content"] == "NOT_EXPORTED_SECRET" for m in ms)
    assert sum(m["requires_manual_transfer"] for m in ms) == 3
    # sold configs whose invoice sits on another panel
    assert len(by_code(res, "SERVICE_PANEL_CONFLICT")) == 2


def test_no_live_state_flags_anywhere(default):
    res, _ = default
    files = [res.stage_dir / "records" / f"{n}.jsonl" for n in MY_FILES]
    files += [res.stage_dir / "records" / "archive" / f"{t}.jsonl" for t in sx.VS_TABLES]
    seen = 0
    for f in files:
        assert f.exists(), f
        for r in read_jsonl(f):
            seen += 1
            for flag in LIVE_FLAGS:
                assert r.get(flag, False) is False, (f.name, flag)
            assert r["idempotency_key"].startswith("legacy:") and r["schema"] and r["provenance"]
    assert seen > 100


def test_coverage_accounts_for_every_row(default):
    res, _ = default
    cov = {c["table"]: c for c in res.report["coverage"]}
    for t in MY_TABLES:
        c = cov[t]
        assert c["rows"] > 0, t
        assert c["accounted_rows"] == c["rows"], t
        assert c["status"] in {"archived", "review"}, t
    for t in sx.VS_TABLES:
        assert cov[t]["status"] == "review"
    assert len(by_code(res, "TABLE_NEEDS_DECISION")) >= len(sx.VS_TABLES)


def test_vs_tables_value_redaction(default):
    res, _ = default
    rows = read_jsonl(res.stage_dir / "records" / "archive" / "vs_setting.jsonl")
    # Settings-like fork tables: the opaque value blob is never staged at all (stronger than redaction).
    assert "value" not in rows[0]["fields"]
    cov = {c["table"]: c for c in res.report["coverage"]}
    assert "value" in cov["vs_setting"]["dropped_secret_columns"]
    assert rows[0]["class"] == "REVIEW"


def test_determinism(tmp_path):
    zp, _ = make_backup(tmp_path, users=120)
    a = run(tmp_path, zp, ws_name="a")
    b = run(tmp_path, zp, ws_name="b")
    for n in MY_FILES:
        rel = f"records/{n}.jsonl"
        assert (a.stage_dir / rel).read_bytes() == (b.stage_dir / rel).read_bytes(), rel
    for k in ("service_operations", "panel_registry", "category_catalogue", "service_requests"):
        assert a.report["reconciliation"][k] == b.report["reconciliation"][k]


# ---- dedicated cases -------------------------------------------------------------------

def test_cancel_request_owner_conflict_and_bad_ids(tmp_path):
    owner = str(TG_BASE + 3)
    stranger = str(TG_BASE + 39)
    # the core synth gives invoice k username syn<k>; look up its owner from a plain run first
    probe = run(tmp_path, _custom_zip(tmp_path, "", name="p.zip"), ws_name="p")
    inv = {r["invoice_key"]: r for r in records(probe, "services")}
    svc = next(iter(sorted(inv.values(), key=lambda r: r["invoice_key"])))
    uname, real_owner = svc["panel_account_username"], svc["owner_telegram_user_id"]
    other = owner if owner != real_owner else stranger
    sql = _so_rows([
        (1, real_owner, uname, "{}", "2025/01/02 10:00:00", "1000", "extend_user", "paid", "x"),
        (1, other, uname, "{}", "2025/01/02 10:00:00", "1000", "extra_user", None, "x"),       # duplicate id
        (None, other, uname, "{}", "bad-time", "", "extends_not_user", None, "x"),             # NULL id
        (4, other, "not_listed_acc", "{}", "2025/01/02 10:00:00", "1000", "extends_not_user", None, "x"),
    ])
    sql += "DROP TABLE IF EXISTS `cancel_service`;\n" + sx.DDL["cancel_service"] + \
        f"INSERT INTO `cancel_service` VALUES (1,{sx.sql_str(other)},{sx.sql_str(uname)},'0','waiting');\n"
    res = run(tmp_path, _custom_zip(tmp_path, sql), ws_name="c")
    assert res.report["readiness"] == "ready"  # review blockers, not package blockers (like core ORPHAN)
    ops = records(res, "service_operations")
    keys = [o["idempotency_key"] for o in ops]
    assert len(keys) == len(set(keys)) == 4
    assert len(by_code(res, "SOURCE_ROW_KEY_INVALID")) == 2
    conflicts = by_code(res, "SERVICE_OWNER_CONFLICT")
    assert {c["source_ref"]["table"] for c in conflicts} == {"service_other", "cancel_service"}
    assert all(c["severity"] == "blocker" for c in conflicts)
    nl = next(o for o in ops if o["panel_account_username"] == "not_listed_acc")
    assert nl["invoice_relation"]["state"] == "NOT_FOUND"
    assert "SERVICE_OP_INVOICE_NOT_FOUND" not in nl["review_codes"]  # 'not in list' renewals are expected
    bad = next(o for o in ops if o["legacy_id"] is None)
    assert bad["time"]["parse_note"] == "FORMAT_UNKNOWN" and bad["amount"]["parse_note"] == "EMPTY"
    cov = {c["table"]: c for c in res.report["coverage"]}
    assert cov["service_other"]["accounted_rows"] == 4
    assert cov["cancel_service"]["accounted_rows"] == 1
    # no registry → no per-code panel reviews (core panels.jsonl already lists the codes)
    assert not by_code(res, "PANEL_NOT_IN_REGISTRY")


def test_category_labels_not_in_table_and_duplicate_panel_codes(tmp_path):
    sql = "DROP TABLE IF EXISTS `category`;\n" + sx.DDL["category"] + \
        "INSERT INTO `category` VALUES (1,'عمومی'),(2,'عمومی ');\n"
    sql += "DROP TABLE IF EXISTS `marzban_panel`;\n" + sx.DDL["marzban_panel"]
    sql += "INSERT INTO `marzban_panel` VALUES " + ",".join([
        sx._panel_row(1, "syn-a", "a", "marzban"), sx._panel_row(2, "syn-a", "a2", "weird-type")]) + ";\n"
    res = run(tmp_path, _custom_zip(tmp_path, sql))
    rec = res.report["reconciliation"]["category_catalogue"]
    assert rec["unmatched_labels"] == 1  # 'ویژه' has no category row
    assert rec["unmatched_products"] > 0
    assert [r["data"]["label"] for r in by_code(res, "CATEGORY_LABEL_NOT_IN_TABLE")] == ["ویژه"]
    assert len(by_code(res, "CATEGORY_REMARK_DUPLICATED")) == 2
    assert len(by_code(res, "PANEL_CODE_DUPLICATED")) == 2
    panels = records(res, "panel_registry")
    assert {p["panel_type"]["normalized"] for p in panels} == {"marzban", "UNKNOWN"}
    blob = (res.stage_dir / "records" / "panel_registry.jsonl").read_bytes()
    for s in sx.SYNTH_SECRETS[:6]:
        assert s.encode() not in blob


def test_parse_time_is_local_and_unconverted():
    assert sx.parse_time("2025/03/21 23:59:01") == {"raw": "2025/03/21 23:59:01", "local": "2025-03-21T23:59:01",
                                                     "time_zone": "Asia/Tehran", "parse_note": "OK"}
    assert sx.parse_time("2025/02/30 00:00:00")["parse_note"] == "OUT_OF_RANGE"
    assert sx.parse_time(None)["parse_note"] == "EMPTY"

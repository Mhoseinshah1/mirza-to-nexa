"""Operator-selected NEXA target panel (RickPanel) per legacy code_panel (synthetic data only)."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import make_backup, read_jsonl, run
from mirza2nexa import panel_targets as pt
from mirza2nexa.jobs import JobManager
from mirza2nexa.pipeline import run_job
from mirza2nexa.server import create_app
from mirza2nexa.settings import Settings

PANEL_ID = "3f2c8a1e-6b7d-4c9a-8e21-0a9b8c7d6e5f"
NEXA = Path(os.environ.get("NEXA_REPO", "/home/user/mhoseinshah1/nexa-bot"))


def test_only_the_nexa_rickpanel_identifier_is_accepted():
    ok = pt.parse_targets([{"code_panel": "syn-a", "provider_type": "rickpanel", "nexa_panel_id": PANEL_ID,
                            "provider_version": "1.0.0"}])
    assert ok == [pt.PanelTarget("syn-a", "rickpanel", PANEL_ID, "1.0.0")]
    for bad in ("RickPanel", "ricpanel", "RIC Panel", "ric_panel", "marzban", "sanaei", "", None):
        with pytest.raises(pt.PanelTargetError):
            pt.parse_targets([{"code_panel": "syn-a", "provider_type": bad}])
    for bad_item in ({"code_panel": " syn-a", "provider_type": "rickpanel"},
                     {"code_panel": "a\nb", "provider_type": "rickpanel"},
                     {"code_panel": "syn-a", "provider_type": "rickpanel", "nexa_panel_id": "not-a-uuid"},
                     {"code_panel": "syn-a", "provider_type": "rickpanel", "nexa_panel_id": PANEL_ID.upper()},
                     {"code_panel": "syn-a", "provider_type": "rickpanel", "provider_version": "v1"},
                     {"code_panel": "syn-a", "provider_type": "rickpanel", "inbound": 3}):
        with pytest.raises(pt.PanelTargetError):
            pt.parse_targets([bad_item])
    with pytest.raises(pt.PanelTargetError):
        pt.parse_targets([{"code_panel": "x", "provider_type": "rickpanel"}] * 2)
    assert pt.parse_cli([f"syn-a=rickpanel,panel_id={PANEL_ID},version=1.0.0"]) == ok
    assert pt.NEXA_TARGET_PROVIDERS["rickpanel"]["display_name"] == "RickPanel"


@pytest.mark.skipif(not (NEXA / "packages/contracts/src/provider.ts").exists(), reason="NEXA source not available")
def test_identifiers_match_the_nexa_source():
    """The provider id and display name are read from NEXA, not guessed."""
    prov = (NEXA / "packages/contracts/src/provider.ts").read_text(encoding="utf-8").splitlines()
    assert re.search(r"PROVIDER_TYPES = \[[^\]]*'rickpanel'", prov[179])
    assert "canonicalName: 'RickPanel'" in prov[1777]
    adapter = (NEXA / "apps/api/src/modules/platform/providers/infrastructure/rickpanel.adapter.ts")
    assert "canonicalName: 'RickPanel'" in adapter.read_text(encoding="utf-8").splitlines()[143]
    importer = (NEXA / "docs/legacy-migration/importer.md").read_text(encoding="utf-8").splitlines()
    assert "`rickpanel`" in importer[283] and '"codePanel"' in importer[260]


def _records(stage: Path) -> dict[str, bytes]:
    return {str(p.relative_to(stage)): p.read_bytes() for p in sorted((stage / "records").rglob("*.jsonl"))}


@pytest.fixture(scope="module")
def pair(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("pt")
    zp, _ = make_backup(tmp, users=200)
    plain = run(tmp, zp, ws_name="plain")
    mapped = run(tmp, zp, ws_name="mapped", panel_targets=pt.parse_targets([
        {"code_panel": "syn-a", "provider_type": "rickpanel", "nexa_panel_id": PANEL_ID, "provider_version": "1.0.0"}]))
    return plain, mapped


def test_marzban_panel_is_mapped_to_rickpanel_in_the_package(pair):
    _, mapped = pair
    recs = {r["code_panel"]: r for r in read_jsonl(mapped.stage_dir / "records" / "panel_target_mapping.jsonl")}
    a = recs["syn-a"]
    assert a["source_panel_type"] == {"normalized": "marzban", "raw": "marzban"}  # source fact untouched
    assert a["target"]["provider_type"] == "rickpanel" and a["target"]["provider_display_name"] == "RickPanel"
    assert a["target"]["provider_version_declared"] == "1.0.0" and a["target"]["nexa_panel_id"] == PANEL_ID
    assert a["target"]["binding"] == pt.CONNECT_EXISTING and a["mapping_state"] == "TARGET_SELECTED"
    assert a["nexa_panel_map_entry"] == {"codePanel": "syn-a", "panelId": PANEL_ID}
    assert a["services_reprovisioned"] is False and a["provision"] is False and a["credentials_in_package"] is False
    assert a["schema"] == "m2n.legacy_panel_target.v1"
    # other panels keep no target and stay operator-mapped
    for code, r in recs.items():
        if code != "syn-a":
            assert r["target"] is None and r["mapping_state"] == "OPERATOR_MUST_MAP"
    rec = mapped.report["reconciliation"]["panel_targets"]
    assert rec["selected"] == 1 and rec["connect_existing"] == 1 and rec["by_provider"] == {"rickpanel": 1}
    assert mapped.report["readiness"] == "ready" and not mapped.report["blockers"]


def test_nothing_else_changes(pair):
    plain, mapped = pair
    assert plain.report["import_id"] == mapped.report["import_id"]
    a, b = _records(plain.stage_dir), _records(mapped.stage_dir)
    extra = set(b) - set(a)
    assert extra <= {"records/panel_target_mapping.jsonl"}
    for name in sorted(set(a) - {"records/panel_target_mapping.jsonl"}):
        assert a[name] == b[name], f"{name} changed by the panel target selection"
    for r in read_jsonl(mapped.stage_dir / "records" / "panel_registry.jsonl"):
        assert r["panel_type"]["normalized"] in {"marzban", None} or r["panel_type"]["normalized"] != "rickpanel"


def test_unknown_code_blocks_and_no_target_creates_no_assumption(tmp_path):
    zp, _ = make_backup(tmp_path, users=60)
    res = run(tmp_path, zp, panel_targets=pt.parse_targets([{"code_panel": "nope", "provider_type": "rickpanel"}]))
    assert {b["code"] for b in res.report["blockers"]} == {"PANEL_TARGET_UNKNOWN_CODE"}
    plain = run(tmp_path, zp, ws_name="ws2")
    recs = read_jsonl(plain.stage_dir / "records" / "panel_target_mapping.jsonl")
    assert recs and all(r["target"] is None for r in recs)  # Marzban type never implies RickPanel


def test_create_in_nexa_when_no_panel_id(tmp_path):
    zp, _ = make_backup(tmp_path, users=60)
    res = run(tmp_path, zp, panel_targets=pt.parse_targets([{"code_panel": "syn-a", "provider_type": "rickpanel"}]))
    r = next(x for x in read_jsonl(res.stage_dir / "records" / "panel_target_mapping.jsonl") if x["code_panel"] == "syn-a")
    assert r["target"]["binding"] == pt.CREATE_THEN_CONNECT and r["nexa_panel_map_entry"] is None


def test_server_accepts_targets_and_lists_panels(tmp_path):
    port, token = 18995, "panel-target-token"
    manager = JobManager(Settings(workspace_dir=str(tmp_path)), run_job)
    app = create_app(manager, port=port, launch_token=token)
    with TestClient(app, base_url=f"http://127.0.0.1:{port}") as client:
        h = {"X-CSRF-Token": client.post("/api/auth", json={"token": token}).json()["csrf"]}
        prov = client.get("/api/panel-targets/providers").json()["providers"]
        assert [(p["provider_type"], p["display_name"]) for p in prov] == [("rickpanel", "RickPanel")]
        zp, _ = make_backup(tmp_path, users=60)
        job = client.post("/api/jobs", content=zp.read_bytes(), headers=h).json()
        bad = client.post(f"/api/jobs/{job['id']}/start", headers=h,
                          json={"money_unit": "toman", "panel_targets": [{"code_panel": "syn-a",
                                                                          "provider_type": "RIC Panel"}]})
        assert bad.status_code == 400 and bad.json()["error"]["code"] == "panel_target.invalid"
        ok = client.post(f"/api/jobs/{job['id']}/start", headers=h, json={
            "money_unit": "toman",
            "panel_targets": [{"code_panel": "syn-a", "provider_type": "rickpanel", "nexa_panel_id": PANEL_ID,
                               "provider_version": "1.0.0"}]})
        assert ok.status_code == 202
        deadline = time.time() + 120
        while client.get(f"/api/jobs/{job['id']}").json()["status"] not in ("completed", "failed"):
            assert time.time() < deadline
            time.sleep(0.1)
        panels = {p["code_panel"]: p for p in client.get(f"/api/jobs/{job['id']}/panels").json()["items"]}
        assert panels["syn-a"]["source_panel_type"] == "marzban"
        assert panels["syn-a"]["target"]["provider_type"] == "rickpanel"
        assert all(p["target"] is None for c, p in panels.items() if c != "syn-a")
        rd = client.get(f"/api/jobs/{job['id']}/readiness").json()
        assert rd["panels"]["targets"]["selected"] == 1
    manager.shutdown()

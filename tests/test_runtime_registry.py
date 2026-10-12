"""Runtime registry: every execution path must load every converter module, or fail closed.

Regression for the v0.2.0 Windows EXE defect: ``modules/__init__.py`` imported the domain modules
dynamically and swallowed ``ModuleNotFoundError``; PyInstaller did not bundle them, so a real run
migrated only user/invoice/product and blocked every Phase-2 table as UNCLASSIFIED_TABLE.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import make_backup, run

ROOT = Path(__file__).resolve().parents[1]
KEY_TABLES = ("Payment_report", "wallet_transaction", "service_other", "ticket", "ticket_message",
              "discount_sell_used", "wheel_list", "reagent_report", "marzban_panel")


def _env(tmp: Path) -> dict[str, str]:
    return {**os.environ, "MIRZA2NEXA_HOME": str(tmp / "home")}


def test_modules_package_uses_static_imports_only():
    """No dynamic import and no try/except may hide a missing module (frozen builds)."""
    tree = ast.parse((ROOT / "src/mirza2nexa/modules/__init__.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        assert not isinstance(node, ast.Try), "try/except in modules/__init__.py can hide a missing module"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"__import__"}, "dynamic __import__ is invisible to PyInstaller"
        if isinstance(node, ast.Attribute):
            assert node.attr != "import_module", "importlib.import_module is invisible to PyInstaller"


def test_selfcheck_in_fresh_process(tmp_path):
    proc = subprocess.run([sys.executable, "-I", "-m", "mirza2nexa", "selfcheck"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=_env(tmp_path), timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    status = json.loads(proc.stdout)
    assert status["ok"] and not status["missing_modules"] and not status["missing_tables"]
    assert status["specs"] >= 52


_BLOCK_PAYMENTS = r"""
import sys
class Block:
    def find_spec(self, name, path=None, target=None):
        if name == "mirza2nexa.modules.payments":
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return None
sys.meta_path.insert(0, Block())
from mirza2nexa.__main__ import main
sys.exit(main(sys.argv[1:]))
"""


def test_missing_converter_module_fails_closed(tmp_path):
    """Simulates a frozen build lacking one module: no package, no 'ready', a clear failure."""
    zp, _ = make_backup(tmp_path, users=50)
    out = tmp_path / "out"
    proc = subprocess.run([sys.executable, "-I", "-c", _BLOCK_PAYMENTS, "convert", str(zp), "-o", str(out),
                           "--money-unit", "toman"], capture_output=True, text=True, encoding="utf-8", errors="replace", env=_env(tmp_path), timeout=300)
    assert proc.returncode != 0
    assert "mirza2nexa.modules.payments" in proc.stderr or "catalogue.incomplete" in proc.stderr
    assert not list(out.glob("*.nxpkg")) if out.exists() else True
    assert not (out / "report.json").exists()

    sc = subprocess.run([sys.executable, "-I", "-c", _BLOCK_PAYMENTS, "selfcheck"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                        env=_env(tmp_path), timeout=120)
    assert sc.returncode != 0


def test_tampered_catalogue_fails_closed(tmp_path, monkeypatch):
    from mirza2nexa.catalogue import CATALOGUE
    from mirza2nexa.modules import CatalogueIncomplete, verify_registry

    zp, _ = make_backup(tmp_path, users=30)
    specs = dict(CATALOGUE.specs)
    specs.pop("Payment_report")
    monkeypatch.setattr(CATALOGUE, "specs", specs)
    with pytest.raises(CatalogueIncomplete):
        verify_registry()
    with pytest.raises(CatalogueIncomplete):
        run(tmp_path, zp)
    assert not list((tmp_path / "ws").rglob("*.nxpkg"))


def _logical(report: dict) -> dict:
    keep = ("import_id", "source_fingerprint", "readiness", "counts", "reconciliation", "review_count")
    out = {k: report[k] for k in keep}
    out["blockers"] = sorted(b["code"] for b in report["blockers"])
    out["coverage"] = sorted(
        (c["table"], c["status"], c.get("class"), c["rows"], c["exported"], c["archived"], c["review"],
         c["skipped"], c["accounted_rows"], tuple(c["dropped_secret_columns"])) for c in report["coverage"])
    return out


def test_fresh_processes_are_repeatable_and_cover_phase2_tables(tmp_path):
    zp, _ = make_backup(tmp_path, users=150, password="Zip-Pw-1")
    results = []
    for i in range(3):
        out = tmp_path / f"out{i}"
        env = {**_env(tmp_path), "ZPW": "Zip-Pw-1"}
        proc = subprocess.run([sys.executable, "-I", "-m", "mirza2nexa", "convert", str(zp), "-o", str(out),
                               "--money-unit", "toman", "--password-env", "ZPW"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=600)
        assert proc.returncode == 0, proc.stderr[-2000:]
        results.append(json.loads((out / "report.json").read_text(encoding="utf-8")))
        # the CLI must keep the (PII-free) readiness report next to the package
        rd = json.loads((out / "migration-readiness.json").read_text(encoding="utf-8"))
        assert any(b["code"] == "IMPORTER_NOT_BUILT" for b in rd["import_blockers"])
        assert "<html" in (out / "migration-readiness.html").read_text(encoding="utf-8").lower()
    first = _logical(results[0])
    for r in results[1:]:
        assert _logical(r) == first
    cov = {c["table"]: c for c in results[0]["coverage"]}
    for t in KEY_TABLES:
        assert cov[t]["class"] is not None, t
        assert cov[t]["status"] in {"archived", "review", "executed"}, (t, cov[t]["status"])
        assert cov[t]["rows"] > 0 and cov[t]["accounted_rows"] == cov[t]["rows"], t
    assert cov["Payment_report"]["archived"] == cov["Payment_report"]["rows"]
    assert cov["wallet_transaction"]["archived"] == cov["wallet_transaction"]["rows"]
    assert results[0]["catalogue"]["ok"] is True


def test_expected_tables_match_evidence_and_catalogue():
    """EXPECTED_TABLES must not drift from the evidence catalogue or the registered specs."""
    from mirza2nexa.catalogue import CATALOGUE
    from mirza2nexa.modules import EXPECTED_TABLES

    evidence = {t["table"] for t in json.loads((ROOT / "docs/evidence/mirza_tables.json").read_text(encoding="utf-8"))}
    assert EXPECTED_TABLES == evidence | {"protocol"}
    assert set(CATALOGUE.specs) == set(EXPECTED_TABLES)


def test_missing_module_gives_clear_selfcheck_json(tmp_path):
    proc = subprocess.run([sys.executable, "-I", "-c", _BLOCK_PAYMENTS, "selfcheck"], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", env=_env(tmp_path), timeout=120)
    assert proc.returncode == 3
    status = json.loads(proc.stdout.strip().splitlines()[-1])
    assert status["ok"] is False and status["missing_modules"] == ["mirza2nexa.modules.payments"]
    assert "catalogue.incomplete" in proc.stderr and "Traceback" not in proc.stderr

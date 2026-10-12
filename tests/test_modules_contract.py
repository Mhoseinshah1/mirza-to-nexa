"""Module contract (docs/MODULES.md), checked on one full synthetic run of every registered module.

Nothing here is skipped: a module that breaks the contract fails these tests.
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

import pytest

from conftest import run
from mirza2nexa.catalogue import CATALOGUE, EXCLUDED, SECRET
from mirza2nexa.nxpkg import PackageReader
from mirza2nexa.synth import SynthSpec, write_zip

USERS = 300
LIVE_FLAGS = (
    "affects_wallet", "creates_payment", "creates_order", "provision", "applies_to_live_state", "activates_role",
    "grants_credit", "counts_as_revenue", "enters_live_payment_state_machine", "triggers_service",
    "applied_to_balance", "auto_create", "redeemable",
)
EVIDENCE = Path(__file__).resolve().parent.parent / "docs" / "evidence" / "mirza_tables.json"


def _convert(tmp: Path, *, modules: bool, name: str):
    zp = tmp / f"{name}.zip"
    write_zip(zp, SynthSpec(users=USERS, modules=modules))
    res = run(tmp, zp, ws_name=f"ws_{name}")
    reader = PackageReader(res.package_path, key=res.key_text, workdir=tmp / f"verify_{name}")
    reader.verify()
    files: dict[str, bytes] = {}
    with zipfile.ZipFile(reader.payload_path) as zf:
        for n in zf.namelist():
            files[n] = zf.read(n)
    reader.cleanup()
    return res, files


@pytest.fixture(scope="module")
def full(tmp_path_factory):
    return _convert(tmp_path_factory.mktemp("contract"), modules=True, name="full")


@pytest.fixture(scope="module")
def core_only(tmp_path_factory):
    return _convert(tmp_path_factory.mktemp("contract_core"), modules=False, name="core")


def _records(files: dict[str, bytes]):
    for name in sorted(files):
        if name.startswith("records/") and name.endswith(".jsonl"):
            for i, line in enumerate(files[name].decode("utf-8").splitlines()):
                if line:
                    yield name, i, json.loads(line)


def _coverage(files) -> dict[str, dict]:
    return {c["table"]: c for c in json.loads(files["reports/coverage.json"])["tables"]}


def _walk_flags(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in LIVE_FLAGS:
                yield f"{path}.{k}", v
            yield from _walk_flags(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_flags(v, f"{path}[{i}]")


# ---- record shape --------------------------------------------------------------------------

def test_every_record_has_contract_fields(full):
    _res, files = full
    bad = []
    n = 0
    for name, i, rec in _records(files):
        n += 1
        prov = rec.get("provenance") or {}
        key = rec.get("idempotency_key")
        problems = [
            "record_type" if not rec.get("record_type") else None,
            "idempotency_key" if not (isinstance(key, str) and key.startswith("legacy:")) else None,
            "provenance.source_table" if not prov.get("source_table") else None,
            "provenance.source_fingerprint" if not prov.get("source_fingerprint") else None,
        ]
        problems = [p for p in problems if p]
        if problems:
            bad.append((name, i, problems))
    assert n > 0
    assert not bad, f"{len(bad)} records break the contract, first: {bad[:5]}"


def test_idempotency_keys_unique_across_package(full):
    _res, files = full
    seen: dict[str, str] = {}
    dups = []
    for name, i, rec in _records(files):
        key = rec.get("idempotency_key")
        if key in seen:
            dups.append((key, seen[key], f"{name}:{i}"))
        seen[key] = f"{name}:{i}"
    assert not dups, f"{len(dups)} duplicate idempotency keys, first: {dups[:5]}"


def test_no_live_state_flag_is_true(full):
    _res, files = full
    bad = [(name, i, path, v) for name, i, rec in _records(files) for path, v in _walk_flags(rec) if v is not False]
    assert not bad, f"{len(bad)} live-state flags not False, first: {bad[:5]}"


def test_provenance_fingerprint_matches_package(full):
    res, files = full
    fp = res.report["source_fingerprint"]
    wrong = [(n, i) for n, i, r in _records(files) if (r.get("provenance") or {}).get("source_fingerprint") != fp]
    assert not wrong, wrong[:5]


# ---- coverage ------------------------------------------------------------------------------

def test_every_dump_table_has_a_coverage_row(full):
    res, files = full
    cov = _coverage(files)
    assert set(res.report["source"]["tables"]) == set(cov)


def test_no_blocked_or_unclassified_table(full):
    _res, files = full
    cov = _coverage(files)
    blocked = sorted(t for t, c in cov.items() if c["status"] == "blocked")
    unclassified = sorted(t for t, c in cov.items() if c.get("class") is None)
    assert not blocked, f"blocked tables: {blocked}"
    assert not unclassified, f"unclassified tables: {unclassified}"


def test_every_row_accounted_for(full):
    _res, files = full
    cov = _coverage(files)
    wrong = {t: (c["rows"], c["accounted_rows"]) for t, c in cov.items()
             if c["rows"] and c.get("class") != EXCLUDED and c["accounted_rows"] != c["rows"]}
    assert not wrong, f"rows != accounted_rows (rows, accounted): {wrong}"


def test_default_synthetic_backup_is_ready(full):
    res, _files = full
    assert res.report["readiness"] == "ready", res.report["blockers"]


def test_every_evidence_table_is_catalogued():
    """Zero uncatalogued tables for every table the public sources / operator report name."""
    tables = [t["table"] for t in json.loads(EVIDENCE.read_text(encoding="utf-8"))]
    missing = sorted(t for t in tables if CATALOGUE.lookup(t) is None)
    assert not missing, f"tables without a catalogue entry: {missing}"


def test_evidence_secret_columns_are_never_staged(full):
    """Every column the evidence marks secret is dropped at parse time wherever it occurs."""
    _res, files = full
    cov = _coverage(files)
    evidence = {t["table"]: {c["name"] for c in t.get("columns", []) if c.get("sensitivity") == SECRET}
                for t in json.loads(EVIDENCE.read_text(encoding="utf-8"))}
    leaks = {}
    for table, c in cov.items():
        secret = evidence.get(table, set())
        spec = CATALOGUE.lookup(table)
        if spec is not None and spec.columns is not None:
            secret |= {col.name for col in spec.columns if col.sensitivity == SECRET}
            secret = {s for s in secret if not (spec.column(s) and _row_filtered(spec, spec.column(s)))}
        staged = set(c["stored_columns"]) & secret
        if staged:
            leaks[table] = sorted(staged)
    assert not leaks, f"secret columns staged: {leaks}"


def test_catalogue_never_weaker_than_evidence():
    """A column the evidence marks secret is never declared storable in the catalogue."""
    weaker = []
    for t in json.loads(EVIDENCE.read_text(encoding="utf-8")):
        spec = CATALOGUE.lookup(t["table"])
        if spec is None or spec.columns is None:
            continue
        for c in t.get("columns", []):
            if c.get("sensitivity") == SECRET:
                col = spec.column(c["name"])
                if col is not None and col.storable and spec.klass != EXCLUDED and not _row_filtered(spec, col):
                    weaker.append(f"{t['table']}.{c['name']}")
    assert not weaker, f"evidence-secret columns declared storable: {weaker}"


def _row_filtered(spec, col) -> bool:
    """Key-value tables whose value column is filtered by an explicit key allow-list in the module."""
    from mirza2nexa.modules.config_tables import KV_TABLES

    kv = KV_TABLES.get(spec.name)
    return kv is not None and kv[1] == col.name


# ---- money / opening balances --------------------------------------------------------------

def test_opening_balances_unchanged_by_modules(full, core_only):
    res_full, files_full = full
    res_core, files_core = core_only
    name = "records/wallet_opening_balances.jsonl"
    a = files_full[name].decode("utf-8")
    b = files_core[name].decode("utf-8")
    # The source fingerprint covers every table, so it differs between the two dumps by design;
    # everything else must be byte-identical.
    a = a.replace(res_full.report["source_fingerprint"], "<FP>")
    b = b.replace(res_core.report["source_fingerprint"], "<FP>")
    assert a and a == b
    assert re.search(r'"amount_minor"', a)

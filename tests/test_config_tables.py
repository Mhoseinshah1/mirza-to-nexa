"""config_tables module: classification, secret columns/keys never leave, allow-listed exports only."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from conftest import make_backup, run
from mirza2nexa.catalogue import CATALOGUE, CONFIGURATION, EXCLUDED, REVIEW, SECRET
from mirza2nexa.modules import config_tables as ct
from mirza2nexa.nxpkg import PackageReader

EXPECTED_CLASS = {
    "setting": CONFIGURATION, "PaySetting": CONFIGURATION, "shopSetting": CONFIGURATION,
    "textbot": CONFIGURATION, "channels": CONFIGURATION, "help": CONFIGURATION, "app": CONFIGURATION,
    "protocol": CONFIGURATION, "help_category": REVIEW,
    "card_number": EXCLUDED, "logs_api": EXCLUDED, "admin": EXCLUDED, "botsaz": EXCLUDED, "topicid": EXCLUDED,
}

# Columns that authenticate (or identify a payout destination) and must never be staged.
SECRET_COLUMNS = {
    "setting": {"webhook_secret"},
    "channels": {"linkjoin"},
    "admin": {"username", "password"},
    "botsaz": {"bot_token", "webhook_secret", "setting"},
    "card_number": {"cardnumber"},
    "logs_api": {"header", "data"},
}


@pytest.fixture(scope="module")
def converted(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("cfg")
    zp, _ = make_backup(tmp, users=120)
    res = run(tmp, zp)
    work = tmp / "verify"
    reader = PackageReader(res.package_path, key=res.key_text, workdir=work)
    reader.verify()
    files: dict[str, bytes] = {}
    with zipfile.ZipFile(reader.payload_path) as zf:
        for name in zf.namelist():
            files[name] = zf.read(name)
    reader.cleanup()
    coverage = {c["table"]: c for c in json.loads(files["reports/coverage.json"])["tables"]}
    return res, files, coverage


def _jsonl(files: dict[str, bytes], name: str) -> list[dict]:
    data = files.get(name)
    if data is None:
        return []
    return [json.loads(line) for line in data.decode("utf-8").splitlines() if line]


# ---- catalogue -------------------------------------------------------------------------------

@pytest.mark.parametrize(("table", "klass"), sorted(EXPECTED_CLASS.items()))
def test_classification(table, klass):
    spec = CATALOGUE.lookup(table)
    assert spec is not None, table
    assert spec.klass == klass
    assert spec.module == "config_tables"
    assert spec.reason and spec.purpose_fa
    if table != "help_category":
        assert spec.evidence, f"{table} must cite evidence"


@pytest.mark.parametrize(("table", "cols"), sorted(SECRET_COLUMNS.items()))
def test_secret_columns_declared_secret(table, cols):
    spec = CATALOGUE.lookup(table)
    for c in cols:
        assert spec.column(c) is not None and spec.column(c).sensitivity == SECRET, (table, c)
        assert not spec.column(c).storable


def test_kv_allow_lists_are_disjoint_from_secret_keys():
    assert not (ct.PAYSETTING_ALLOWED & ct.PAYSETTING_SECRET)
    for key in ("cardnumber", "CartDescription", "merchant_zarinpal", "apinowpayment", "variza_api_token",
                "variza_webhook_secret", "walletaddress", "perfectmoney_PassPhrase", "endpointiranpay4"):
        assert key not in ct.PAYSETTING_ALLOWED


def test_help_category_fails_closed():
    spec = CATALOGUE.lookup("help_category")
    stored, dropped, unmapped = spec.storable_columns(("id", "name", "token"))
    assert stored == () and set(unmapped) == {"id", "name", "token"}


# ---- end to end ------------------------------------------------------------------------------

def test_default_synthetic_backup_has_no_blockers_from_config_tables(converted):
    res, _files, coverage = converted
    for table in EXPECTED_CLASS:
        if table in coverage:
            assert coverage[table]["status"] != "blocked", table
            assert coverage[table]["class"] == EXPECTED_CLASS[table]
    assert not any(b["code"].startswith("CONFIG") for b in res.report["blockers"])


@pytest.mark.parametrize(("table", "cols"), sorted(SECRET_COLUMNS.items()))
def test_secret_columns_never_staged(converted, table, cols):
    _res, _files, coverage = converted
    cov = coverage[table]
    assert not (set(cov["stored_columns"]) & cols), cov["stored_columns"]
    if cov["class"] == EXCLUDED:
        assert cov["stored_columns"] == []
        assert cov["skipped"] == cov["rows"] and cov["status"] == "excluded"
    else:
        assert cols <= set(cov["dropped_secret_columns"]), cov["dropped_secret_columns"]


def test_excluded_tables_produce_no_records(converted):
    _res, files, _cov = converted
    for table, klass in EXPECTED_CLASS.items():
        if klass == EXCLUDED:
            assert not any(n.endswith(f"/{table}.jsonl") for n in files), table


def test_configuration_exports_only_allow_listed_columns(converted):
    _res, files, coverage = converted
    for table, klass in EXPECTED_CLASS.items():
        if klass != CONFIGURATION or table in ct.KV_TABLES:
            continue
        spec = CATALOGUE.lookup(table)
        allowed = {c.name for c in spec.columns if c.storable}
        recs = _jsonl(files, f"records/configuration/{table}.jsonl")
        assert len(recs) == coverage[table]["rows"], table
        for r in recs:
            assert r["record_type"] == "legacy_configuration_row"
            assert r["applies_to_live_state"] is False
            assert set(r["fields"]) <= allowed, (table, set(r["fields"]) - allowed)


def test_setting_unknown_column_not_exported(converted):
    _res, files, coverage = converted
    assert "zz_future_column" in coverage["setting"]["unmapped_columns"]
    (rec,) = _jsonl(files, "records/configuration/setting.jsonl")
    assert "zz_future_column" not in rec["fields"] and "webhook_secret" not in rec["fields"]
    assert rec["fields"]["Bot_Status"] == "botstatuson"
    assert "wheelـluck_price" in rec["fields"]  # non-ASCII column name survives
    assert "[REDACTED:sub_url]" in rec["fields"]["text_edit"]


@pytest.mark.parametrize("table", sorted(ct.KV_TABLES))
def test_kv_tables_export_only_allow_listed_keys(converted, table):
    _res, files, coverage = converted
    key_col, value_col, allowed, known_secret = ct.KV_TABLES[table]
    recs = _jsonl(files, f"records/configuration/{table}.jsonl")
    assert recs
    for r in recs:
        assert set(r["fields"]) == {key_col, value_col}
        assert r["fields"][key_col] in allowed
        assert r["idempotency_key"] == f"legacy:{table}:{r['fields'][key_col]}"
    cov = coverage[table]
    assert cov["archived"] == len(recs)
    assert cov["archived"] + cov["skipped"] == cov["rows"]
    assert set(cov["dropped_secret_keys"]) <= known_secret


def test_paysetting_secret_keys_dropped_and_reported(converted):
    _res, files, coverage = converted
    cov = coverage["PaySetting"]
    assert set(cov["dropped_secret_keys"]) == {
        "cardnumber", "namecard", "CartDirect", "merchant_zarinpal", "apinowpayment", "variza_api_token",
        "variza_webhook_secret", "walletaddress", "perfectmoney_PassPhrase"}
    assert cov["dropped_unknown_keys"] == 1  # zz_undocumented_gateway_key: unknown ⇒ dropped
    recs = {r["fields"]["NamePay"]: r["fields"]["ValuePay"] for r in _jsonl(files, "records/configuration/PaySetting.jsonl")}
    assert recs["minbalance"] == "20000"
    # tutorial text is allow-listed, but the card number and password inside it are redacted
    assert ct.PLANTED_CARD not in recs["helpcart"] and "****9993" in recs["helpcart"]
    assert ct.PLANTED_PASSWORD not in recs["helpcart"]


def test_free_text_redacted_in_configuration(converted):
    _res, files, _cov = converted
    texts = {r["fields"]["id_text"]: r["fields"]["text"] for r in _jsonl(files, "records/configuration/textbot.jsonl")}
    assert "[REDACTED:card:****9993]" in texts["text_cart"] and "[REDACTED:iban]" in texts["text_cart"]
    assert "[REDACTED:proxy_link]" in texts["text_dec_fq"] and "[REDACTED:sub_url]" in texts["text_dec_fq"]
    assert "[REDACTED:bot_token]" in texts["text_admin_note"] and "[REDACTED:jwt]" in texts["text_admin_note"]
    assert texts["text_start"].startswith("سلام!")  # ordinary Persian text untouched
    assert "250000" in texts["text_start"]


def test_help_category_counted_with_review(converted):
    _res, files, coverage = converted
    cov = coverage["help_category"]
    assert cov["stored_columns"] == [] and cov["skipped"] == cov["rows"] == 1 and cov["status"] == "review"
    review = _jsonl(files, "reports/review-queue.jsonl")
    assert any(r["code"] == "CONFIG_NO_STORABLE_COLUMN" and r["source_ref"]["table"] == "help_category"
               for r in review)


def test_no_planted_secret_in_configuration_records(converted):
    _res, files, _cov = converted
    blob = b"".join(v for k, v in files.items() if k.startswith("records/configuration/"))
    text = blob.decode("utf-8")
    for s in ct.PLANTED_SECRETS:
        assert s not in text, s


def test_stage_dir_has_no_staging_db(converted):
    res, _files, _cov = converted
    ws = Path(res.stage_dir).parent
    assert not (ws / "staging.sqlite").exists()

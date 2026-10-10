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
    "protocol": CONFIGURATION, "help_category": REVIEW, "botsaz_help": REVIEW,
    "card_number": EXCLUDED, "logs_api": EXCLUDED, "admin": EXCLUDED, "botsaz": EXCLUDED, "topicid": EXCLUDED,
    "global_hidden_gateways": EXCLUDED, "ai_business_chat": EXCLUDED,
}
#: Fork tables without DDL: generic specs (columns come from the runtime DDL, name-filtered).
GENERIC_TABLES = {"help_category", "botsaz_help", "global_hidden_gateways", "ai_business_chat"}

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
    assert spec.evidence, f"{table} must cite evidence"
    assert (spec.columns is None) == (table in GENERIC_TABLES), table


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


def test_help_category_is_generic_with_secret_name_filter():
    spec = CATALOGUE.lookup("help_category")
    stored, dropped, unmapped = spec.storable_columns(("id", "name", "token", "api_key", "password"))
    assert stored == ("id", "name") and set(dropped) == {"token", "api_key", "password"} and unmapped == ()


@pytest.mark.parametrize("table", ["global_hidden_gateways", "ai_business_chat"])
def test_unverified_excluded_tables_store_nothing(table):
    spec = CATALOGUE.lookup(table)
    stored, _dropped, _unmapped = spec.storable_columns(("id", "gateway", "message", "api_key"))
    assert stored == ()
    assert table in ct.UNVERIFIED_EXCLUDED


def test_new_fork_tables_are_in_evidence_and_expected_tables():
    from mirza2nexa.modules import EXPECTED_TABLES

    ev = {t["table"]: t for t in json.loads(
        (Path(__file__).resolve().parents[1] / "docs/evidence/mirza_tables.json").read_text(encoding="utf-8"))}
    for t in ("ai_business_chat", "botsaz_help", "global_hidden_gateways"):
        assert t in EXPECTED_TABLES and t in ev
        assert ev[t]["recommended_class"] == EXPECTED_CLASS[t]
        assert ev[t]["columns"] == []


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


def test_help_category_archived_for_review_without_secret_columns(converted):
    _res, files, coverage = converted
    cov = coverage["help_category"]
    assert cov["status"] == "review" and cov["rows"] == 1 and cov["archived"] == 1
    assert cov["stored_columns"] == ["id", "name"] and cov["dropped_secret_columns"] == ["token"]
    (rec,) = _jsonl(files, "records/archive/help_category.jsonl")
    assert rec["record_type"] == "legacy_archive_row" and rec["class"] == REVIEW
    assert rec["applies_to_live_state"] is False
    assert set(rec["fields"]) == {"id", "name"} and rec["fields"]["name"] == "عمومی"
    review = _jsonl(files, "reports/review-queue.jsonl")
    assert any(r["code"] == "TABLE_NEEDS_DECISION" and r["source_ref"]["table"] == "help_category" for r in review)
    assert not any(r["code"] == "CONFIG_NO_STORABLE_COLUMN" and r["source_ref"]["table"] == "help_category"
                   for r in review)
    blob = b"".join(v for k, v in files.items() if k.startswith("records/archive/help_category")).decode("utf-8")
    for s in ct.PLANTED_SECRETS:
        assert s not in blob, s


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


# ---- fork tables seen empty in the real backup ----------------------------------------------

_FORK_DDL = (
    "CREATE TABLE `botsaz_help` (\n  `id` int NOT NULL AUTO_INCREMENT,\n  `id_user` varchar(200) NOT NULL,\n"
    "  `text` text,\n  `bot_token` varchar(200) DEFAULT NULL,\n  PRIMARY KEY (`id`)\n) ENGINE=InnoDB;\n"
    "CREATE TABLE `global_hidden_gateways` (\n  `id` int NOT NULL,\n  `gateway` varchar(200) NOT NULL,\n"
    "  PRIMARY KEY (`id`)\n) ENGINE=InnoDB;\n"
    "CREATE TABLE `ai_business_chat` (\n  `id` int NOT NULL,\n  `id_user` varchar(200) NOT NULL,\n"
    "  `message` text NOT NULL,\n  `api_key` varchar(200) DEFAULT NULL,\n  PRIMARY KEY (`id`)\n) ENGINE=InnoDB;\n"
)


def _with_extra_sql(tmp: Path, extra: str, name: str) -> Path:
    src, _ = make_backup(tmp, users=40, name=f"src-{name}")
    with zipfile.ZipFile(src) as zf:
        (entry,) = [n for n in zf.namelist() if n.endswith(".sql")]
        sql = zf.read(entry)
    out = tmp / name
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(entry, sql + extra.encode("utf-8"))
    return out


def test_fork_tables_empty_do_not_block(tmp_path):
    zp = _with_extra_sql(tmp_path, _FORK_DDL, "empty.zip")
    res = run(tmp_path, zp)
    cov = {c["table"]: c for c in res.report["coverage"]}
    for t in ("botsaz_help", "global_hidden_gateways", "ai_business_chat"):
        assert cov[t]["status"] == "empty" and cov[t]["class"] == EXPECTED_CLASS[t], cov[t]
    assert not {"UNCLASSIFIED_TABLE", "COVERAGE_INCOMPLETE"} & {b["code"] for b in res.report["blockers"]}


def test_fork_tables_non_empty(tmp_path):
    rows = (
        f"INSERT INTO `botsaz_help` VALUES (1,'7000000001','راهنمای کارت {ct.PLANTED_CARD}','{ct.PLANTED_BOT_TOKEN}');\n"
        "INSERT INTO `global_hidden_gateways` VALUES (1,'zarinpal');\n"
        f"INSERT INTO `ai_business_chat` VALUES (1,'7000000001','سلام','{ct.PLANTED_API_KEY}');\n"
    )
    zp = _with_extra_sql(tmp_path, _FORK_DDL + rows, "full.zip")
    res = run(tmp_path, zp)
    cov = {c["table"]: c for c in res.report["coverage"]}
    assert cov["botsaz_help"]["status"] == "review" and cov["botsaz_help"]["archived"] == 1
    assert "bot_token" in cov["botsaz_help"]["dropped_secret_columns"]
    for t in ("global_hidden_gateways", "ai_business_chat"):
        assert cov[t]["status"] == "excluded" and cov[t]["stored_columns"] == [] and cov[t]["skipped"] == 1
    reader = PackageReader(res.package_path, key=res.key_text, workdir=tmp_path / "verify")
    reader.verify()
    with zipfile.ZipFile(reader.payload_path) as zf:
        files = {n: zf.read(n) for n in zf.namelist()}
    reader.cleanup()
    review = _jsonl(files, "reports/review-queue.jsonl")
    flagged = {r["source_ref"]["table"] for r in review if r["code"] == "CONFIG_UNVERIFIED_TABLE_EXCLUDED"}
    assert flagged == {"global_hidden_gateways", "ai_business_chat"}
    (rec,) = _jsonl(files, "records/archive/botsaz_help.jsonl")
    assert set(rec["fields"]) == {"id", "id_user", "text"}
    blob = b"".join(files.values()).decode("utf-8", "replace")
    for s in (ct.PLANTED_CARD, ct.PLANTED_BOT_TOKEN, ct.PLANTED_API_KEY):
        assert s not in blob, s

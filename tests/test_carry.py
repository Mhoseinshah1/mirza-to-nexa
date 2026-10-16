"""Column carry (converter 0.8.0, contract 1.5.0): every non-secret source column reaches the package.

Synthetic only: ``SynthSpec.extra_columns`` adds the deployed fork's columns (``modules/carry.py``
SYNTH_EXTRA_COLUMNS); every value of a column that must be dropped contains ``SYNSECRET``.
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import run
from mirza2nexa.catalogue import CATALOGUE, CarryPlan, TableSpec
from mirza2nexa.modules import EXPECTED_TABLES, carry
from mirza2nexa.modules import config_tables as ct
from mirza2nexa.nxpkg import PackageReader
from mirza2nexa.synth import SynthSpec, write_zip

USERS = 120
TELEGRAM_ID = re.compile(r"^[1-9][0-9]{0,19}$")
NEXA_TABLE_FILE = re.compile(r"^[A-Za-z0-9_]{1,64}\.jsonl$")

C, S, M = "carried", "secret_dropped", "minimised"
#: The columns the owner's real-backup coverage report listed as not in the 0.7.0 package, and the
#: disposition each must have now. (affiliates/setting fork columns: see the name-rule test.)
EXPECTED = {
    "user": {
        **dict.fromkeys((
            "roll_Status", "Processing_value", "Processing_value_one", "Processing_value_tow",
            "Processing_value_four", "step", "description_blocking", "pagenumber", "message_count",
            "last_message_time", "affiliatescount", "affiliates", "namecustom", "number_username", "register",
            "verify", "joinchannel", "checkstatus", "score", "limitchangeloc", "status_cron", "date_message",
            "low_balance_alert_sent", "hidden_gateways", "shown_gateways", "first_name", "commission_percentage",
            "min_purchase_warned_month", "min_purchase_evaluated_month", "agent_tier_since",
            "affiliate_alert_time", "ad_source", "card_message_id"), C),
        "codeInvitation": S, "bottype": S, "token": S,
    },
    "invoice": {**dict.fromkeys(("note", "inboundid", "refral", "time_cron", "notifctions", "rate_invoice",
                                 "limit_user", "id_payment", "payable_amount"), C), "bottype": S},
    "product": dict.fromkeys(("limit_user", "display_order", "style", "usage_type", "emoji_id", "time_unit"), C),
    "service_other": {**dict.fromkeys(("id_payment", "payable_amount", "id_invoice", "action", "source", "params",
                                       "created_at"), C), "bottype": S, "panel_response": S},
    "category": {"style": C},
    "reagent_report": {"gift_milestone": C, "gift_invoice": C},
    "setting": {"jwt_secret": S, "ai_api_key": S, "ai_status": C},
    "affiliates": {"commission_type": C, "gift_token": S},
    "marzban_panel": {**dict.fromkeys((
        "hosts", "status_happ", "limit_user", "limit_in_panel", "price_pre_user", "buy_additional_user",
        "display_order", "style", "status_keyboard_config", "content_after_buy", "MethodUsername_key", "btn_config",
        "btn_sublink", "emoji_id", "auth_type", "auto_disabled", "text_afterpay", "text_aftertest", "hide_agent",
        "status_extra_volume", "status_extra_time"), C), "proxy_sockss": S},
    "admin": {"id_admin": C, "rule": C, "username": S, "password": S},
    "botsaz": {**dict.fromkeys(("id", "id_user", "admin_ids", "username", "hide_panel", "time", "product_info"), C),
               "setting": M, "bot_token": S, "webhook_secret": S},
    "logs_api": {"id": C, "time": C, "actions": C, "ip": M, "data": M, "header": S},
    "topicid": {"report": C, "idreport": C},
}


def _convert(tmp: Path, name: str, **synth):
    zp = tmp / f"{name}.zip"
    write_zip(zp, SynthSpec(users=USERS, **synth))
    res = run(tmp, zp, ws_name=f"ws_{name}")
    reader = PackageReader(res.package_path, key=res.key_text, workdir=tmp / f"verify_{name}")
    reader.verify()
    with zipfile.ZipFile(reader.payload_path) as zf:
        files = {n: zf.read(n) for n in zf.namelist()}
    reader.cleanup()
    return res, files


@pytest.fixture(scope="module")
def full(tmp_path_factory):
    return _convert(tmp_path_factory.mktemp("carry"), "full")


def _jsonl(files: dict[str, bytes], name: str) -> list[dict]:
    return [json.loads(line) for line in files.get(name, b"").decode("utf-8").splitlines() if line]


def _coverage(files) -> dict[str, dict]:
    return {c["table"]: c for c in json.loads(files["reports/coverage.json"])["tables"]}


# ---- catalogue -------------------------------------------------------------------------------

def test_every_expected_table_accounts_for_every_column_it_can_have():
    """For each catalogued table, the carry plan over (declared + undeclared) columns leaves nothing."""
    for name in sorted(EXPECTED_TABLES):
        spec = CATALOGUE.lookup(name)
        declared = tuple(c.name for c in (spec.columns or ()) + spec.carry)
        cols = declared + ("zz_fork_column", "zz_api_key")
        stored, dropped, unmapped = spec.storable_columns(cols)
        assert unmapped == (), name
        if spec.klass == "EXCLUDED":
            continue
        owned = tuple(c for c in cols if spec.columns is None or spec.main_column(c) is not None)
        plan = spec.carry_plan(cols, owned)
        accounted = set(stored) | set(dropped) | set(plan.carried) | set(plan.minimised) | set(plan.dropped)
        assert accounted == set(cols), (name, set(cols) - accounted)
        if spec.columns is not None:  # generic specs drop it themselves, by the same name rule
            assert "zz_api_key" in plan.dropped and "zz_api_key" not in plan.carried, name
        else:
            assert "zz_api_key" in dropped, name


def test_secret_carry_columns_are_never_storable():
    for spec in CATALOGUE.specs.values():
        for c in spec.carry:
            if c.sensitivity == "secret":
                assert not c.storable, (spec.name, c.name)


def test_minimisers_never_return_the_value():
    from mirza2nexa.sensitivity import minimise

    assert minimise("ip_family", "198.51.100.7") == "ipv4"
    assert minimise("ip_family", "2001:db8::1") == "ipv6"
    out = minimise("json_keys", json.dumps({"token": ct.PLANTED_BOT_TOKEN, "password": ct.PLANTED_PASSWORD}))
    assert json.loads(out) == {"kind": "object", "keys": ["password", "token"], "key_count": 2}
    assert ct.PLANTED_BOT_TOKEN not in minimise("json_keys", ct.PLANTED_BOT_TOKEN)
    assert minimise("json_keys", None) is None


# ---- end to end ------------------------------------------------------------------------------

def test_default_synthetic_backup_is_ready(full):
    res, _files = full
    assert res.report["readiness"] == "ready", res.report["blockers"]


def test_no_column_unmapped_or_unaccounted(full):
    res, files = full
    cov = _coverage(files)
    catalogued = [c for c in cov.values() if c["class"] is not None]
    assert len(catalogued) >= 50
    for c in catalogued:
        assert c["unmapped_columns"] == [], c["table"]
        assert c["unaccounted_columns"] == [], c["table"]
        # every DDL column of the dump has exactly one disposition
        assert set(c["column_dispositions"]) == set(res.report["source"]["tables"][c["table"]]["columns"]), c["table"]
    assert not any(b["code"] == "COLUMN_UNACCOUNTED" for b in res.report["blockers"])


@pytest.mark.parametrize("table", sorted(EXPECTED))
def test_each_listed_column_has_its_disposition(full, table):
    _res, files = full
    disp = _coverage(files)[table]["column_dispositions"]
    for col, want in EXPECTED[table].items():
        assert disp.get(col) == want, (table, col, disp.get(col))


def test_no_synthetic_secret_leaves_the_converter(full):
    res, files = full
    blob = b"".join(files.values()) + res.report_path.read_bytes()
    blob += b"".join(p.read_bytes() for p in Path(res.stage_dir).rglob("*") if p.is_file())
    assert carry.SYNTH_SECRET_MARK.encode() not in blob
    for s in ct.PLANTED_SECRETS:
        assert s.encode() not in blob, s


def test_user_supplement_records_carry_values_and_link_the_customer(full):
    _res, files = full
    recs = _jsonl(files, "records/archive/user_carried.jsonl")
    cov = _coverage(files)["user"]
    assert len(recs) == cov["rows"] == cov["carry_records"]
    state = SimpleNamespace(user_ids=[], invoice_keys=[])
    linked = 0
    for i, r in enumerate(recs):
        assert r["record_type"] == "legacy_archive_row" and r["schema"] == carry.SCHEMA_SUPPLEMENT
        assert r["idempotency_key"] == f"legacy:carried:user:{r['source_pk']}"
        assert r["applies_to_live_state"] is False and r["carry"] == "supplement"
        assert r["provenance"]["source_table"] == "user"
        assert {"token", "codeInvitation", "bottype"} <= set(r["secret_columns_dropped"])
        assert not {"token", "codeInvitation", "bottype"} & set(r["fields"])
        for col in ("step", "verify", "ad_source", "card_message_id", "commission_percentage"):
            assert r["fields"][col] == carry.synth_value("user", col, i, state), (i, col)
        tg = r["customer"]["telegram_user_id"]
        if tg is not None:
            assert TELEGRAM_ID.fullmatch(tg) and tg == r["source_pk"]
            linked += 1
    assert linked >= USERS


def test_invoice_and_service_other_link_to_the_invoice(full):
    _res, files = full
    inv = _jsonl(files, "records/archive/invoice_carried.jsonl")
    assert inv and all(r["invoice"]["invoice_key"] == r["source_pk"] and r["invoice"]["in_backup"] for r in inv)
    assert all("customer" in r for r in inv)
    so = _jsonl(files, "records/archive/service_other_carried.jsonl")
    assert so and sum(1 for r in so if r["invoice"]["in_backup"]) == len(so)
    assert all(r["fields"]["id_invoice"] == r["invoice"]["invoice_key"] for r in so)
    assert not {"bottype", "panel_response"} & set(so[0]["fields"])


def test_formerly_excluded_tables_are_archived_with_minimisation(full):
    _res, files = full
    cov = _coverage(files)
    for t in ("admin", "botsaz", "logs_api", "topicid"):
        assert cov[t]["class"] == "ARCHIVE" and cov[t]["status"] == "archived", t
        assert cov[t]["archived"] == cov[t]["rows"] == cov[t]["accounted_rows"], t
        assert f"records/archive/{t}.jsonl" in files
    admins = _jsonl(files, "records/archive/admin.jsonl")
    assert {a["fields"]["rule"] for a in admins} == {"administrator", "support"}
    assert all(set(a["fields"]) == {"id_admin", "rule"} for a in admins)
    assert all(a["customer"]["telegram_user_id"] == a["fields"]["id_admin"] for a in admins)
    (log,) = _jsonl(files, "records/archive/logs_api.jsonl")
    assert log["minimised"]["ip"] == {"method": "ip_family", "value": "ipv4"}
    assert json.loads(log["minimised"]["data"]["value"])["keys"] == ["password", "token"]
    assert "header" not in log["fields"] and "198.51.100.7" not in json.dumps(log)
    (bot,) = _jsonl(files, "records/archive/botsaz.jsonl")
    assert "cart_info" in json.loads(bot["minimised"]["setting"]["value"])["keys"]
    assert bot["customer"]["telegram_user_id"] == bot["fields"]["id_user"]
    assert bot["fields"]["product_info"] == "v0"


def test_files_and_records_are_what_nexa_history_ingest_accepts(full):
    """record-map.ts: records/archive|configuration/<[A-Za-z0-9_]{1,64}>.jsonl, the matching record_type,
    a `legacy:` key of 8..512 chars, unique, and no live flag set."""
    _res, files = full
    seen: set[str] = set()
    for name in sorted(files):
        for prefix, rtype in (("records/archive/", "legacy_archive_row"),
                              ("records/configuration/", "legacy_configuration_row")):
            if not name.startswith(prefix):
                continue
            assert NEXA_TABLE_FILE.fullmatch(name[len(prefix):]), name
            for r in _jsonl(files, name):
                assert r["record_type"] == rtype, name
                key = r["idempotency_key"]
                assert key.startswith("legacy:") and 8 <= len(key) <= 512 and key not in seen, key
                seen.add(key)


def test_manual_reconfiguration_report_names_every_secret_and_no_value(full):
    _res, files = full
    doc = json.loads(files["reports/manual-reconfiguration.json"])
    assert doc["format"] == "m2n.manual-reconfiguration.v1"
    items = {(i["table"], i["column"] or i["key"]) for i in doc["items"]}
    for table, cols in EXPECTED.items():
        for col, want in cols.items():
            if want == S:
                assert (table, col) in items, (table, col)
    for table, col in (("marzban_panel", "password_panel"), ("setting", "webhook_secret"), ("card_number", "cardnumber"),
                       ("botsaz", "setting"), ("logs_api", "data"), ("PaySetting", "merchant_zarinpal")):
        assert (table, col) in items, (table, col)
    raw = files["reports/manual-reconfiguration.json"].decode("utf-8")
    assert carry.SYNTH_SECRET_MARK not in raw
    for s in ct.PLANTED_SECRETS:
        assert s not in raw
    by = {(i["table"], i["column"]): i for i in doc["items"] if i["column"]}
    assert by[("botsaz", "setting")]["kept"] == "key_names_only"
    assert by[("user", "token")]["basis"] == "catalogue"
    assert by[("affiliates", "gift_token")]["basis"] == "name_heuristic"


def test_carry_changes_no_existing_record_and_no_fingerprint(tmp_path, monkeypatch):
    """The same dump converted with the carry switched off: identical import_id, fingerprint and
    byte-identical records in every file the carry does not own."""
    zp = tmp_path / "same.zip"
    write_zip(zp, SynthSpec(users=60))
    with_carry = run(tmp_path, zp, ws_name="ws_on")
    monkeypatch.setattr(TableSpec, "carry_plan", lambda self, cols, owned: CarryPlan((), (), (), ()))
    without = run(tmp_path, zp, ws_name="ws_off")
    assert with_carry.report["import_id"] == without.report["import_id"]
    assert with_carry.report["source_fingerprint"] == without.report["source_fingerprint"]
    on, off = Path(with_carry.stage_dir) / "records", Path(without.stage_dir) / "records"
    owned = {f"{t}.jsonl" for t in ("admin", "botsaz", "logs_api", "topicid")}
    compared = 0
    for p in sorted(off.rglob("*.jsonl")):
        if p.name.endswith("_carried.jsonl") or p.name in owned:
            continue
        assert p.read_bytes() == (on / p.relative_to(off)).read_bytes(), p.name
        compared += 1
    assert compared >= 20
    extra = sorted(str(p.relative_to(on)) for p in on.rglob("*.jsonl") if not (off / p.relative_to(on)).exists())
    assert extra and all(e.endswith("_carried.jsonl") or Path(e).name in owned for e in extra), extra


def test_carry_switch_off_in_synth_keeps_old_shape(tmp_path):
    res, files = _convert(tmp_path, "plain", extra_columns=False)
    cov = _coverage(files)
    assert res.report["readiness"] == "ready", res.report["blockers"]
    assert cov["user"]["carried_columns"] == ["affiliates"]  # in NEXA's synthetic user DDL already
    assert "records/archive/product_carried.jsonl" not in files

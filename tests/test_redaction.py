"""Redaction: unit tests for every kind, plus end-to-end proof that planted secrets never leave.

The synthetic backup (``mirza2nexa.modules.config_tables.synth_rows``) plants fabricated secrets in
secret columns and inside free text. After a full run, every output a user or NEXA can see is
searched for them: every payload file of the decrypted package, report.json, the review queue,
coverage, the job history and the local API responses. The ZIP password and the package key
must not appear in any of them either, and nothing sensitive may reach the log.
"""

from __future__ import annotations

import json
import logging
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import make_backup, run
from mirza2nexa.jobs import JobManager, read_history
from mirza2nexa.modules import config_tables as ct
from mirza2nexa.nxpkg import PackageReader
from mirza2nexa.pipeline import run_job
from mirza2nexa.sensitivity import column_name_is_secret, contains_secret, luhn_valid, redact_text
from mirza2nexa.server import create_app
from mirza2nexa.settings import Settings

ZIP_PASSWORD = "Zip-Pass-Synthetic-9"  # noqa: S105 - fabricated test password
PACKAGE_PASSPHRASE = "pkg-passphrase-synthetic-0042"  # noqa: S105 - fabricated test passphrase


def kinds(text: str) -> list[str]:
    hits: list[str] = []
    redact_text(text, hits.append)
    return hits


# ---- unit: each kind -------------------------------------------------------------------------

@pytest.mark.parametrize(("text", "kind", "secret"), [
    (f"bot {ct.PLANTED_BOT_TOKEN} end", "bot_token", ct.PLANTED_BOT_TOKEN),
    (f"config {ct.PLANTED_VLESS}", "proxy_link", ct.PLANTED_VLESS),
    ("vmess://eyJhZGQiOiJ4In0= trailing", "proxy_link", "vmess://eyJhZGQiOiJ4In0="),
    (f"sub {ct.PLANTED_SUB_URL}", "sub_url", "abcdef0123456789syntheticSubToken"),
    ("https://panel.example/api?token=abc123def", "sub_url", "abc123def"),
    ("mysql://root:hunter2pass@db.example/x", "url_creds", "hunter2pass"),
    (f"jwt {ct.PLANTED_JWT}", "jwt", ct.PLANTED_JWT),
    ("Authorization: Bearer abcdefghijklmnop0123456789", "bearer", "abcdefghijklmnop0123456789"),
    (f"password={ct.PLANTED_PASSWORD}", "kv_secret", ct.PLANTED_PASSWORD),
    (f'{{"api_key": "{ct.PLANTED_API_KEY}"}}', "kv_secret", ct.PLANTED_API_KEY),
    (f"رمز: {ct.PLANTED_PASSWORD}", "kv_secret", ct.PLANTED_PASSWORD),
    (f"رمز عبور = {ct.PLANTED_PASSWORD}", "kv_secret", ct.PLANTED_PASSWORD),
    (f"merchant_id: {ct.PLANTED_MERCHANT}", "kv_secret", ct.PLANTED_MERCHANT),
    (f"شبا {ct.PLANTED_IBAN}", "iban", ct.PLANTED_IBAN),
    ("شبا IR06 2960 0000 0010 0324 2000 01", "iban", "0324 2000 01"),
    ("id 11111111-2222-4333-8444-555555555555", "uuid", "11111111-2222-4333-8444-555555555555"),
    (f"key {ct.PLANTED_WEBHOOK_SECRET}", "secret_like", ct.PLANTED_WEBHOOK_SECRET),
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJBAK\n-----END RSA PRIVATE KEY-----", "private_key", "MIIBOgIBAAJBAK"),
    (f"card {ct.PLANTED_CARD}", "card", ct.PLANTED_CARD),
    ("card 6037-9975-9999-9993 ok", "card", "6037-9975-9999-9993"),
])
def test_each_kind_is_redacted(text, kind, secret):
    out = redact_text(text)
    assert secret not in out, out
    assert kind in kinds(text)
    assert f"[REDACTED:{kind}" in out


def test_card_masked_to_last_four_including_persian_digits():
    persian = ct.PLANTED_CARD.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
    for text in (f"کارت {ct.PLANTED_CARD}", f"کارت {persian}"):
        out = redact_text(text)
        assert out.endswith("[REDACTED:card:****9993]"), out
    assert luhn_valid(ct.PLANTED_CARD) and luhn_valid(persian)


@pytest.mark.parametrize("text", [
    "سلام! به ربات ما خوش آمدید. برای خرید سرویس روی دکمه زیر بزنید.",
    "رمز عبور خود را فراموش کرده‌اید؟ با پشتیبانی تماس بگیرید.",
    "مبلغ 250000 تومان از کیف پول شما کسر شد.",
    "مبلغ ۲۵۰٬۰۰۰ تومان",
    "1,500,000 تومان برای ۳۰ روز",
    "Telegram id 7000000001 and 123456789",
    "شماره تماس 09120000000",
    "invoice 1000000000000abc code 1234",
    "کد تخفیف SUMMER25",
    "timestamp 1700000000",
    "plan p001 Volume 30 GB, 30 days",
    '{"keyboard":[[{"text":"text_sell"},{"text":"text_extend"}]]}',
    "https://t.me/synthetic_channel",
    "card 6037997599999990 fails Luhn",
])
def test_no_false_positive(text):
    assert redact_text(text) == text
    assert not contains_secret(text)


def test_short_values_untouched():
    assert redact_text("1234567") == "1234567"
    assert redact_text("") == ""


def test_redaction_is_idempotent():
    text = f"{ct.PLANTED_CARD} password: {ct.PLANTED_PASSWORD} {ct.PLANTED_SUB_URL}"
    once = redact_text(text)
    assert redact_text(once) == once


@pytest.mark.parametrize(("name", "secret"), [
    ("bot_token", True), ("webhook_secret", True), ("merchant_zarinpal", True), ("marchent_tronseller", True),
    ("walletaddress", True), ("endpointiranpay4", True), ("cardnumber", True), ("header", True),
    ("Bot_Status", False), ("name_os", False), ("Description_os", False), ("price", False), ("category", False),
])
def test_column_name_heuristic(name, secret):
    assert column_name_is_secret(name) is secret


# ---- end to end: package + reports -----------------------------------------------------------

def _all_package_files(res, tmp: Path) -> dict[str, str]:
    reader = PackageReader(res.package_path, key=res.key_text, workdir=tmp / "verify")
    reader.verify()
    out: dict[str, str] = {}
    with zipfile.ZipFile(reader.payload_path) as zf:
        for name in zf.namelist():
            out[name] = zf.read(name).decode("utf-8")
    reader.cleanup()
    return out


def _assert_absent(blob: str, where: str, extra: tuple[str, ...] = ()) -> None:
    for s in (*ct.PLANTED_SECRETS, *extra):
        assert s not in blob, f"planted secret {s[:12]}… found in {where}"


@pytest.fixture(scope="module")
def pipeline_run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("redact")
    zp, _ = make_backup(tmp, users=150, password=ZIP_PASSWORD)
    res = run(tmp, zp, password=ZIP_PASSWORD)
    return tmp, res


def test_package_payload_has_no_planted_secret(pipeline_run):
    tmp, res = pipeline_run
    files = _all_package_files(res, tmp)
    assert any(n.startswith("records/configuration/") for n in files)
    assert "reports/coverage.json" in files and "reports/review-queue.jsonl" in files
    for name, text in files.items():
        _assert_absent(text, name, (ZIP_PASSWORD, res.key_text))


def test_report_and_stage_have_no_planted_secret(pipeline_run):
    _tmp, res = pipeline_run
    _assert_absent(res.report_path.read_text(encoding="utf-8"), "report.json", (ZIP_PASSWORD, res.key_text))
    _assert_absent(json.dumps(res.report, ensure_ascii=False), "report dict", (ZIP_PASSWORD, res.key_text))
    for p in Path(res.stage_dir).rglob("*"):
        if p.is_file():
            _assert_absent(p.read_text(encoding="utf-8"), str(p), (ZIP_PASSWORD, res.key_text))


def test_workspace_holds_no_plaintext_source(pipeline_run):
    _tmp, res = pipeline_run
    ws = Path(res.stage_dir).parent
    assert not (ws / "staging.sqlite").exists()
    assert not any((ws / "sql").glob("*")) if (ws / "sql").exists() else True
    raw = res.package_path.read_bytes()
    for s in (*ct.PLANTED_SECRETS, ZIP_PASSWORD, res.key_text):
        assert s.encode() not in raw  # package is encrypted; not even the key is inside


def test_logs_carry_no_secret_or_pii(tmp_path, caplog):
    zp, _ = make_backup(tmp_path, users=60, password=ZIP_PASSWORD)
    with caplog.at_level(logging.DEBUG):
        res = run(tmp_path, zp, password=ZIP_PASSWORD)
    pii = ("synthetic_user_1", "synthetic_user_2", "7000000001", "synthetic payee", "@synthetic_cart_admin",
           "198.51.100.7")
    _assert_absent(caplog.text, "log", (ZIP_PASSWORD, res.key_text, *pii))


# ---- end to end: local server, API and history ----------------------------------------------

PORT = 18998
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = "launch-token-redaction-tests"  # noqa: S105 - test token


def _wait(client, job_id, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("completed", "failed", "cancelled"):
            return j
        time.sleep(0.1)
    raise AssertionError("job did not finish")


@pytest.mark.parametrize("secret_mode", ["generated", "passphrase"])
def test_api_responses_and_history_have_no_secret(tmp_path, caplog, secret_mode):
    settings = Settings(workspace_dir=str(tmp_path / "jobs"))
    manager = JobManager(settings, run_job)
    app = create_app(manager, port=PORT, launch_token=TOKEN)
    zp, _ = make_backup(tmp_path, users=80, password=ZIP_PASSWORD)
    seen: list[str] = []
    with caplog.at_level(logging.DEBUG), TestClient(app, base_url=BASE) as client:
        h = {"X-CSRF-Token": client.post("/api/auth", json={"token": TOKEN}).json()["csrf"]}
        job = client.post("/api/jobs", content=zp.read_bytes(), headers={**h, "X-Filename": "b.zip"}).json()
        seen.append(json.dumps(job, ensure_ascii=False))
        body = {"password": ZIP_PASSWORD, "money_unit": "toman", "package_secret_mode": secret_mode}
        if secret_mode == "passphrase":
            body["package_passphrase"] = PACKAGE_PASSPHRASE
        r = client.post(f"/api/jobs/{job['id']}/start", json=body, headers=h)
        assert r.status_code == 202, r.text
        seen.append(r.text)
        final = _wait(client, job["id"])
        assert final["status"] == "completed", final
        seen.append(json.dumps(final, ensure_ascii=False))
        seen.append(client.get("/api/jobs").text)
        report = client.get(f"/api/jobs/{job['id']}/report")
        assert report.status_code == 200
        seen.append(report.text)
        offset = 0
        while True:
            page = client.get(f"/api/jobs/{job['id']}/review", params={"offset": offset, "limit": 500}).json()
            seen.append(json.dumps(page, ensure_ascii=False))
            offset += 500
            if offset >= page["total"]:
                break
        records = Path(manager.get(job["id"]).workspace) / "stage" / "records"
        entities = sorted(p.stem for p in records.glob("*.jsonl"))
        assert "customers" in entities
        for entity in entities:
            p = client.get(f"/api/jobs/{job['id']}/preview/{entity}", params={"limit": 200})
            assert p.status_code == 200
            seen.append(p.text)
        seen.append(client.get("/api/history").text)
        key_text = manager.get(job["id"]).key_text
        if secret_mode == "generated":
            assert key_text and client.get(f"/api/jobs/{job['id']}/key").text.strip() == key_text
    manager.shutdown()
    seen.append(json.dumps(read_history(), ensure_ascii=False))
    assert read_history(), "history must have been written"
    seen.append(caplog.text)
    extra = (ZIP_PASSWORD, PACKAGE_PASSPHRASE) + ((key_text,) if key_text else ())
    for i, blob in enumerate(seen):
        _assert_absent(blob, f"API/history output #{i}", extra)

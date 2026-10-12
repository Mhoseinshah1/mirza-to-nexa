"""Phase 3 UI: migration-readiness endpoints, Persian HTML rendering, escaping and no-PII checks."""

from __future__ import annotations

import copy
import json
import re
import time

import pytest
from fastapi.testclient import TestClient

from mirza2nexa import report_fa
from mirza2nexa.jobs import JobManager
from mirza2nexa.pipeline import run_job
from mirza2nexa.readiness_html import render_readiness_html
from mirza2nexa.server import REPORT_HTML_CSP, create_app
from mirza2nexa.settings import Settings
from mirza2nexa.synth import TG_BASE

PORT = 18997
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = "launch-token-readiness-tests"
PERSIAN = re.compile(r"[؀-ۿ]")
EVIL = '<script>alert("x")</script>'

# Synthetic readiness document covering every documented key (aggregates only).
FIXTURE: dict = {
    "readiness_version": "1.1.0",
    "converter_version": "0.3.0",
    "package_contract_version": "1.1.0",
    "import_id": "imp-abcdef",
    "generated_at": "2026-10-10T08:00:00Z",
    "verdict": {"code": "NOT_READY_FOR_IMPORT", "label_fa": "آماده Import نیست",
                "reasons": [{"code": "NEXA_RESTORE_PATH_UNRELEASED", "message_fa": "مسیر بازیابی NEXA منتشر نشده است."}]},
    "package_health": {"readiness": "ready", "blockers": [{"code": "MONEY_UNIT_UNCONFIRMED",
                                                          "message_fa": "واحد پول تأیید نشده", "count": 1}],
                       "catalogue_ok": True},
    "financial": {"balanced": True, "source_recount_matches": True,
                  "positive_balances": {"count": 120, "amount_minor": "98765432", "currency": "IRT"},
                  "debts": {"count": 3, "amount_minor": "4500", "currency": "IRT"},
                  "net_minor": "98760932",
                  "money_unit": {"value": "toman", "confirmed_by": "operator"},
                  "history_differences": {"total": 9, "by_cause": {"PRE_HISTORY_BALANCE": 5, "NEW_CAUSE_X": 4},
                                          "proven": 5, "probable": 2, "unexplained": 2},
                  "payments_unknown_status": 4, "cases_needing_review": 11},
    "coverage": {"tables_total": 48, "non_empty": 30, "by_status": {"executed": 10, "archived": 20},
                 "by_class": {"OPERATIONAL": 10, "ARCHIVE": 20}, "uncovered": ["mystery_table"]},
    "ownership": {"total": 70, "by_decision": {"HIDDEN_SHAPE": 60, "AMBIGUOUS_OWNERSHIP": 10},
                  "needs_manual_review": 10, "confirmed_transfers": 2},
    "panels": {"registry_rows": 2, "codes_in_invoices": 3, "mapped": 0, "pending_mapping": 3,
               "status": "PENDING_OWNER_MAPPING"},
    "review": {"total_items": 400, "distinct_records": 250,
               "by_group": [{"group": "financial_history", "label_fa": "اختلاف تاریخچه مالی", "items": 40,
                             "records": 30, "resolvable_by_code": 1, "archive_only": 20, "owner_decision": 19,
                             "adoption_time": 0}],
               "auto_resolved_with_evidence": 7, "owner_decisions_needed": 3, "adoption_stage": 12},
    "owner_decisions": [{"id": "OD-MONEY-UNIT", "title_fa": "تأیید واحد پول", "detail_fa": "جزئیات", "blocking": True},
                        {"id": "OD-OPEN-TICKETS", "title_fa": "تیکت‌های باز", "detail_fa": "", "blocking": False}],
    "import_blockers": [{"code": "NEXA_RESTORE_PATH_UNRELEASED", "message_fa": "مسیر بازیابی NEXA منتشر نشده است."}],
}


def _poison(obj):
    """Replace every string leaf and every dict key with a script payload (keeps structure)."""
    if isinstance(obj, dict):
        return {(EVIL + k): _poison(v) for k, v in obj.items()} | {k: _poison(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_poison(v) for v in obj]
    if isinstance(obj, str):
        return EVIL
    return obj


@pytest.fixture()
def app_client(tmp_path):
    manager = JobManager(Settings(workspace_dir=str(tmp_path)), run_job)
    app = create_app(manager, port=PORT, launch_token=TOKEN)
    with TestClient(app, base_url=BASE) as client:
        yield client, manager
    manager.shutdown()


def login(client) -> dict[str, str]:
    r = client.post("/api/auth", json={"token": TOKEN})
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": r.json()["csrf"]}


ENDPOINTS = ("readiness", "readiness.json", "readiness.html")


# ---- rendering -----------------------------------------------------------------------------

def test_render_fixture_is_persian_rtl_and_complete():
    out = render_readiness_html(FIXTURE)
    assert out.startswith("<!doctype html>") and 'dir="rtl"' in out and 'lang="fa"' in out
    assert "<script" not in out.lower() and "<link" not in out.lower() and out.count("<style>") == 1
    assert report_fa.READINESS_DISCLAIMER_FA in out
    for text in ("سلامت بسته", "تطبیق مالی", "پوشش جدول‌ها", "مالکیت سرویس‌ها", "نگاشت پنل‌ها",
                 "موارد بررسی", "تصمیم‌های لازم مالک", "موانع بازیابی", "mystery_table",
                 "موجودی پیش از شروع تاریخچه", "NEW_CAUSE_X", "در انتظار نگاشت توسط مالک",
                 "اختلاف تاریخچه مالی", "تأیید واحد پول", "NEXA_RESTORE_PATH_UNRELEASED", "تومان"):
        assert text in out, text


def test_render_escapes_every_string_field():
    out = render_readiness_html(_poison(copy.deepcopy(FIXTURE)))
    assert "<script" not in out.lower()
    assert "&lt;script&gt;" in out
    assert '"x")' not in out  # quotes escaped too


@pytest.mark.parametrize("doc", [{}, {"verdict": "x", "financial": [], "coverage": None, "review": {"by_group": "x"},
                                      "owner_decisions": {"a": 1}, "import_blockers": [1, None, "s"]}])
def test_render_tolerates_missing_or_malformed_sections(doc):
    out = render_readiness_html(doc)
    assert "آمادگی مهاجرت به NEXA" in out and "<script" not in out.lower()


def test_rendered_html_has_no_pii_patterns():
    out = render_readiness_html(FIXTURE)
    assert not re.search(r"\d{7,}", out)  # no Telegram-id-like numbers (amounts are grouped Persian digits)
    out = re.sub(r"<style>.*?</style>", "", out, flags=re.S)  # CSS at-rules are not usernames
    assert not re.search(r"@[A-Za-z_]\w{2,}", out)  # no @usernames
    assert "۹۸٬۷۶۵٬۴۳۲" in out  # aggregate amount rendered grouped


def test_labels_cover_readiness_codes():
    lb = report_fa.labels_payload()
    for k in ("readiness_verdicts", "ownership_decisions", "history_causes", "review_groups", "panel_status",
              "readiness_keys", "import_blockers"):
        assert lb[k] and all(PERSIAN.search(v) for v in lb[k].values()), k
    assert set(lb["readiness_verdicts"]) == {"NOT_READY_FOR_IMPORT", "READY_FOR_IMPORTER_DRY_RUN", "BLOCKED"}
    from mirza2nexa.review_taxonomy import GROUPS_FA

    assert set(GROUPS_FA) <= set(lb["review_groups"])
    for key in ("catalogue_ok", "balanced", "source_recount_matches", "by_cause", "needs_manual_review",
                "pending_mapping", "auto_resolved_with_evidence", "owner_decisions_needed", "adoption_stage"):
        assert key in lb["readiness_keys"], key


# ---- endpoints -----------------------------------------------------------------------------

def test_endpoints_require_session_and_same_origin(app_client):
    client, _ = app_client
    for ep in ENDPOINTS:
        r = client.get(f"/api/jobs/x/{ep}")
        assert r.status_code == 401 and r.json()["error"]["code"] == "security.session", ep
    login(client)
    for ep in ENDPOINTS:
        assert client.get(f"/api/jobs/x/{ep}", headers={"Origin": "http://evil.example"}).status_code == 403
        assert client.get(f"/api/jobs/x/{ep}", headers={"Host": "evil.example"}).status_code == 421
        assert client.get(f"/api/jobs/nope/{ep}").status_code == 404
    # read-only endpoints: a state-changing verb is not routed, and CSRF still guards it first
    assert client.post("/api/jobs/x/readiness").status_code == 403


def test_endpoints_404_persian_then_serve_fixture(app_client):
    client, manager = app_client
    login(client)
    job = manager.create("synthetic.zip")
    for ep in ENDPOINTS:
        r = client.get(f"/api/jobs/{job.id}/{ep}")
        assert r.status_code == 404, ep
        assert r.json()["error"]["code"] == "readiness.not_ready"
        assert PERSIAN.search(r.json()["error"]["message"])
    out = job.workspace / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / "migration-readiness.json").write_text(json.dumps(_poison(FIXTURE), ensure_ascii=False), encoding="utf-8")

    r = client.get(f"/api/jobs/{job.id}/readiness")
    assert r.status_code == 200 and r.json()["readiness_version"] == EVIL
    assert r.headers["cache-control"] == "no-store"

    r = client.get(f"/api/jobs/{job.id}/readiness.json")
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith("attachment;")
    assert r.headers["content-type"].startswith("application/json")
    assert json.loads(r.content.decode("utf-8")) == _poison(FIXTURE)

    r = client.get(f"/api/jobs/{job.id}/readiness.html")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["content-disposition"].startswith("attachment;")
    assert r.headers["content-security-policy"] == REPORT_HTML_CSP
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "<script" not in r.text.lower() and "&lt;script&gt;" in r.text


def test_malformed_readiness_file_is_not_ready(app_client):
    client, manager = app_client
    login(client)
    job = manager.create("synthetic.zip")
    out = job.workspace / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / "migration-readiness.json").write_text("[1, 2", encoding="utf-8")
    assert client.get(f"/api/jobs/{job.id}/readiness").status_code == 404
    (out / "migration-readiness.json").write_text("[1, 2]", encoding="utf-8")
    assert client.get(f"/api/jobs/{job.id}/readiness.html").status_code == 404


def test_readiness_after_synthetic_conversion(app_client, tmp_path):
    from conftest import make_backup

    client, _ = app_client
    h = login(client)
    zp, exp = make_backup(tmp_path, users=120, name="ready.zip")
    job = client.post("/api/jobs", content=zp.read_bytes(), headers=h).json()
    assert client.get(f"/api/jobs/{job['id']}/readiness").status_code == 404
    assert client.post(f"/api/jobs/{job['id']}/start", json={"money_unit": "toman"}, headers=h).status_code == 202
    deadline = time.time() + 120
    while client.get(f"/api/jobs/{job['id']}").json()["status"] not in ("completed", "failed", "cancelled"):
        assert time.time() < deadline
        time.sleep(0.1)
    r = client.get(f"/api/jobs/{job['id']}/readiness")
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["verdict"]["code"] in report_fa.READINESS_VERDICT_FA
    html_text = client.get(f"/api/jobs/{job['id']}/readiness.html").text
    assert "<script" not in html_text.lower()
    tg_ids = {str(TG_BASE + n) for n in range(exp["users"])}
    assert not set(re.findall(r"\d{6,}", html_text + r.text)) & tg_ids
    assert "synthetic_user_" not in html_text and "synthetic_user_" not in r.text


def test_labels_cover_producer_codes():
    from mirza2nexa.modules.ownership import DECISIONS
    from mirza2nexa.modules.wallet_history import CAUSES

    assert set(DECISIONS) <= set(report_fa.OWNERSHIP_DECISION_FA)
    assert set(CAUSES) <= set(report_fa.HISTORY_CAUSE_FA)


def test_ui_copy_describes_the_nexa_restore_path_not_a_missing_importer():
    """v0.7.0: the output is «فایل بکاپ NEXA برای بازیابی از میرزا», restored from NEXA's
    «بکاپ و بازیابی ← عملیات بازیابی»; no screen or report may still say the importer is not built."""
    from pathlib import Path

    import mirza2nexa

    static = Path(mirza2nexa.__file__).parent / "web" / "static"
    texts = {f.name: f.read_text(encoding="utf-8") for f in static.iterdir() if f.suffix in {".html", ".js"}}
    texts["labels"] = json.dumps(report_fa.labels_payload(), ensure_ascii=False)
    texts["readiness.html"] = render_readiness_html(FIXTURE)
    for name, text in texts.items():
        for stale in ("ساخته نشده", "IMPORTER_NOT_BUILT", "Importer نکسا", "data-testid=\"importer-warning\""):
            assert stale not in text, (name, stale)
    note = report_fa.NEXA_RESTORE_NOTE_FA
    for phrase in ("فایل بکاپ NEXA برای بازیابی از میرزا", "بکاپ و بازیابی", "عملیات بازیابی", "بکاپ محافظ",
                   "منتشر نشده", "نصب آزمایشی"):
        assert phrase in note, phrase
    assert note in texts["readiness.html"]
    assert 'data-testid="restore-steps"' in texts["index.html"]
    assert set(report_fa.IMPORT_BLOCKER_FA) >= {"NEXA_RESTORE_PATH_UNRELEASED"}

"""Phase 2 UI endpoints: labels, package preview, Persian HTML report, coverage CSV (synthetic data)."""

from __future__ import annotations

import csv
import io
import re
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from mirza2nexa import report_fa
from mirza2nexa.jobs import JobManager
from mirza2nexa.nxpkg.manifest import PACKAGE_SCHEMA_VERSION
from mirza2nexa.nxpkg.reader import PackageReader
from mirza2nexa.pipeline import run_job
from mirza2nexa.server import REPORT_HTML_CSP, create_app
from mirza2nexa.settings import Settings
from mirza2nexa.synth import TG_BASE, SynthSpec, write_dump

PORT = 18998
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = "launch-token-phase2-tests"
EVIL_TABLE = "x<img src=x>"
EVIL_COLUMN = "v<script>"


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


def wait(client, job_id, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("completed", "failed", "cancelled"):
            return j
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def evil_backup(tmp_path, users: int = 60) -> bytes:
    """Synthetic dump plus an uncatalogued table whose name and data are HTML/script payloads."""
    buf = io.StringIO()
    write_dump(buf, SynthSpec(users=users, seed=11))
    buf.write(f"CREATE TABLE `{EVIL_TABLE}` (`id` int NOT NULL, `{EVIL_COLUMN}` varchar(64), PRIMARY KEY (`id`));\n")
    buf.write(f"INSERT INTO `{EVIL_TABLE}` VALUES (1,'<script>alert(1)</script>'),(2,'=HYPERLINK(\"x\")');\n")
    zp = tmp_path / "evil.zip"
    with zipfile.ZipFile(zp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("backup.sql", buf.getvalue())
    return zp.read_bytes()


@pytest.fixture()
def completed_job(app_client, tmp_path):
    client, manager = app_client
    h = login(client)
    job = client.post("/api/jobs", content=evil_backup(tmp_path), headers=h).json()
    # not ready yet: every new endpoint answers 404 with a Persian message
    for path in ("package-preview", "report.html", "coverage.csv"):
        r = client.get(f"/api/jobs/{job['id']}/{path}")
        assert r.status_code == 404, path
        assert re.search(r"[؀-ۿ]", r.json()["error"]["message"])
    r = client.post(f"/api/jobs/{job['id']}/start", json={"money_unit": "toman"}, headers=h)
    assert r.status_code == 202, r.text
    done = wait(client, job["id"])
    assert done["status"] == "completed", done
    report = client.get(f"/api/jobs/{job['id']}/report").json()
    return client, manager, h, job["id"], report


# ---- security: unchanged model applies to the new endpoints --------------------------------

def test_new_endpoints_require_session_and_reject_foreign_origin(app_client):
    client, _ = app_client
    for path in ("/api/labels", "/api/jobs/x/package-preview", "/api/jobs/x/report.html", "/api/jobs/x/coverage.csv"):
        r = client.get(path)
        assert r.status_code == 401 and r.json()["error"]["code"] == "security.session", path
    login(client)
    assert client.get("/api/labels", headers={"Origin": "http://evil.example"}).status_code == 403
    assert client.get("/api/labels", headers={"Host": "evil.example"}).status_code == 421
    assert client.get("/api/jobs/nope/report.html").status_code == 404


def test_csrf_still_enforced(app_client):
    client, _ = app_client
    h = login(client)
    assert client.delete("/api/history").status_code == 403
    assert client.delete("/api/history", headers=h).status_code == 200


def test_labels_payload(app_client):
    client, _ = app_client
    login(client)
    lb = client.get("/api/labels").json()
    assert lb["coverage_status"]["executed"] == "اجرا شد"
    assert lb["coverage_status"]["excluded"] == "غیرقابل انتقال"
    assert lb["keys"]["succeeded"] == "موفق" and lb["keys"]["chain_gaps"]
    assert set(lb["blocker_categories"]) == {"financial", "schema", "data_quality", "security"}
    assert lb["classes"]["ARCHIVE"]
    from mirza2nexa.convert import BLOCKER_MESSAGES, MESSAGES

    assert set(MESSAGES) <= set(lb["review_codes"])
    assert set(BLOCKER_MESSAGES) <= set(lb["blocker_codes"])


# ---- package preview ---------------------------------------------------------------------

def test_package_preview_matches_encrypted_package(completed_job, tmp_path):
    client, manager, _h, job_id, report = completed_job
    p = client.get(f"/api/jobs/{job_id}/package-preview")
    assert p.status_code == 200, p.text
    pv = p.json()
    assert pv["package_schema_version"] == PACKAGE_SCHEMA_VERSION
    assert pv["nexa_restore_released"] is False and pv["verified_by_importer"] is False
    assert "files" not in pv["manifest"]
    assert pv["manifest"]["import_id"] == report["import_id"]
    assert pv["readiness"] == report["readiness"] == "blocked"
    assert "UNCLASSIFIED_TABLE" in {b["code"] for b in pv["blockers"]}
    paths = {f["path"]: f for f in pv["files"]}
    assert paths["records/customers.jsonl"]["records"] == report["counts"]["customers"]["converted"]
    assert all(re.fullmatch(r"[0-9a-f]{64}", f["sha256"]) for f in pv["files"])
    # nothing secret leaks through the preview
    blob = p.text
    assert "nxkey1:" not in blob and "passphrase" not in blob

    # the preview describes exactly what the encrypted package contains
    job = manager.get(job_id)
    key = client.get(f"/api/jobs/{job_id}/key").text.strip()
    vr = PackageReader(job.package_path, key=key, workdir=tmp_path / "verify").verify()
    assert vr.manifest["package_schema_version"] == pv["package_schema_version"]
    for path, entry in vr.files.items():
        assert paths[path]["sha256"] == entry["sha256"]
        assert paths[path]["bytes"] == entry["size"]
        assert paths[path]["records"] == entry["records"]
    assert set(paths) == set(vr.files)


# ---- Persian HTML report -------------------------------------------------------------------

def test_report_html_is_escaped_attachment_without_personal_data(completed_job):
    client, _m, _h, job_id, report = completed_job
    assert any(c["table"] == EVIL_TABLE and c["status"] == "blocked" for c in report["coverage"])
    r = client.get(f"/api/jobs/{job_id}/report.html")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["content-disposition"].startswith("attachment;")
    assert r.headers["content-security-policy"] == REPORT_HTML_CSP
    assert r.headers["x-content-type-options"] == "nosniff"
    body = r.text
    assert 'dir="rtl"' in body and 'lang="fa"' in body
    assert "<script" not in body.lower()
    assert "<img" not in body.lower()
    assert "x&lt;img src=x&gt;" in body
    assert report_fa.NEXA_RESTORE_NOTE_FA in body and "عملیات بازیابی" in body
    assert "ساخته نشده" not in body
    assert PACKAGE_SCHEMA_VERSION in body
    # aggregates only: no Telegram ids, usernames or raw cell values of the backup
    assert not set(re.findall(r"\d{10}", body)) & {str(TG_BASE + n) for n in range(100)}
    assert "synthetic_user_" not in body and "alert(1)" not in body


def test_render_report_html_escapes_every_field():
    evil = '<script>alert("x")</script>'
    rep = {
        "import_id": evil, "source_fingerprint": evil, "readiness": evil,
        "converter": {"name": evil, "version": evil, "mapping_version": evil},
        "schema_profile": {"id": evil, "label": evil},
        "money_unit": {"value": evil},
        "counts": {evil: {"source": evil, "converted": 1}},
        "blockers": [{"code": evil, "message": evil, "count": evil}],
        "warnings": [{"code": evil, "message": evil, "count": 1}],
        "coverage": [{"table": evil, "class": None, "class_fa": evil, "status": evil, "rows": 1,
                      "unmapped_columns": [evil], "dropped_secret_columns": [evil], "purpose_fa": evil,
                      "reason": evil, "reason_blocked": evil, "open_questions": [evil]}],
        "reconciliation": {evil: {evil: [evil, {evil: evil}], "ok": True}},
        "mapping": [{"entity": evil, "source_table": evil, "target": evil, "status": "blocked", "note": evil}],
        "package": {"bytes": evil},
    }
    out = report_fa.render_report_html(rep, manifest={"package_schema_version": evil})
    assert "<script" not in out.lower()
    assert "&lt;script&gt;" in out
    assert out.count("<style>") == 1 and "<link" not in out
    assert '"x")' not in out  # quotes are escaped too


def test_blocker_categories():
    assert report_fa.blocker_category("MONEY_UNIT_UNCONFIRMED") == "financial"
    assert report_fa.blocker_category("UNCLASSIFIED_TABLE") == "schema"
    assert report_fa.blocker_category("DUPLICATE_SOURCE_ID") == "data_quality"
    assert report_fa.blocker_category("SECRET_COLUMN_EXPORTED") == "security"
    assert report_fa.blocker_category("WALLET_HISTORY_CHAIN_MISMATCH") == "financial"
    assert report_fa.blocker_category("SOMETHING_NEW") == "data_quality"


# ---- coverage CSV --------------------------------------------------------------------------

def test_coverage_csv_has_bom_and_one_row_per_table(completed_job):
    client, _m, _h, job_id, report = completed_job
    r = client.get(f"/api/jobs/{job_id}/coverage.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"].startswith("attachment;")
    assert r.content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(r.content[3:].decode("utf-8"))))
    assert len(rows) == len(report["coverage"]) + 1
    assert rows[0][0] == "جدول"
    by_table = {row[0]: row for row in rows[1:]}
    assert set(by_table) == {c["table"] for c in report["coverage"]}
    assert by_table[EVIL_TABLE][4] == "مسدود"
    assert "<script>alert" not in r.content.decode("utf-8")


def test_coverage_csv_neutralises_formulas():
    data = report_fa.render_coverage_csv({"coverage": [{"table": "=HYPERLINK(\"http://x\")", "status": "blocked",
                                                         "reason": "+cmd", "rows": 1}]})
    rows = list(csv.reader(io.StringIO(data[3:].decode("utf-8"))))
    assert rows[1][0].startswith("'=") and rows[1][17].startswith("'+")

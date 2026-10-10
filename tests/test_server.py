"""Local API: loopback-only security model and the full UI flow (synthetic data)."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from conftest import make_backup
from mirza2nexa.jobs import JobManager, read_history
from mirza2nexa.pipeline import run_job
from mirza2nexa.server import create_app
from mirza2nexa.settings import Settings

PORT = 18999
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = "launch-token-for-tests"


@pytest.fixture()
def app_client(tmp_path):
    settings = Settings(workspace_dir=str(tmp_path))
    manager = JobManager(settings, run_job)
    app = create_app(manager, port=PORT, launch_token=TOKEN)
    with TestClient(app, base_url=BASE) as client:
        yield client, manager
    manager.shutdown()


def login(client) -> dict[str, str]:
    r = client.post("/api/auth", json={"token": TOKEN})
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": r.json()["csrf"]}


def wait(client, job_id, statuses=("completed", "failed", "cancelled"), timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in statuses:
            return j
        time.sleep(0.1)
    raise AssertionError("job did not finish")


# ---- security ----------------------------------------------------------------------------

def test_health_is_public_but_api_requires_session(app_client):
    client, _ = app_client
    assert client.get("/api/health").json()["status"] == "ok"
    r = client.get("/api/jobs")
    assert r.status_code == 401 and r.json()["error"]["code"] == "security.session"


def test_foreign_host_header_rejected_dns_rebinding(app_client):
    client, _ = app_client
    r = client.get("/api/health", headers={"Host": "evil.example:18999"})
    assert r.status_code == 421
    r = client.get("/", headers={"Host": f"192.168.1.5:{PORT}"})
    assert r.status_code == 421


def test_foreign_origin_rejected(app_client):
    client, _ = app_client
    h = login(client)
    r = client.post("/api/jobs", content=b"PK\x03\x04", headers={**h, "Origin": "https://evil.example"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "security.origin"


def test_launch_token_is_single_use_and_csrf_enforced(app_client):
    client, _ = app_client
    h = login(client)
    other = TestClient(client.app, base_url=BASE)
    assert other.post("/api/auth", json={"token": TOKEN}).status_code == 401  # already consumed
    assert client.delete("/api/history").status_code == 403  # no CSRF header
    assert client.delete("/api/history", headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert client.delete("/api/history", headers=h).status_code == 200


def test_security_headers(app_client):
    client, _ = app_client
    r = client.get("/")
    assert r.status_code == 200
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"


def test_static_path_traversal_blocked(app_client):
    client, _ = app_client
    assert client.get("/static/..%2Fserver.py").status_code == 404
    assert client.get("/static/../server.py").status_code == 404


def test_upload_rejects_non_zip(app_client):
    client, manager = app_client
    h = login(client)
    r = client.post("/api/jobs", content=b"not a zip at all", headers={**h, "X-Filename": "x.zip"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "archive.not_zip"
    assert manager.list() == []


def test_upload_respects_size_limit(app_client):
    client, manager = app_client
    h = login(client)
    manager.settings.max_archive_mb = 1
    r = client.post("/api/jobs", content=b"PK\x03\x04" + b"\0" * (2 * 1024 * 1024), headers=h)
    assert r.status_code == 413


# ---- full flow -----------------------------------------------------------------------------

def test_full_flow_encrypted_backup(app_client, tmp_path):
    client, _ = app_client
    h = login(client)
    zp, exp = make_backup(tmp_path, users=150, password="Zip-Pass-123")
    r = client.post("/api/jobs", content=zp.read_bytes(), headers={**h, "X-Filename": "backup%20test.zip"})
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["status"] == "awaiting_password"
    assert job["inspection"]["encrypted"] and job["inspection"]["encryption"] == "aes"

    # missing / wrong password: synchronous Persian error, job remains retryable
    r = client.post(f"/api/jobs/{job['id']}/start", json={"money_unit": "toman"}, headers=h)
    assert r.status_code == 400 and r.json()["error"]["code"] == "archive.password_required"
    r = client.post(f"/api/jobs/{job['id']}/start", json={"password": "nope", "money_unit": "toman"}, headers=h)
    assert r.status_code == 400 and r.json()["error"]["code"] == "archive.wrong_password"

    r = client.post(f"/api/jobs/{job['id']}/start", json={"password": "Zip-Pass-123", "money_unit": "toman"},
                    headers=h)
    assert r.status_code == 202, r.text
    done = wait(client, job["id"])
    assert done["status"] == "completed", done
    assert done["percent"] == 100.0 and done["has_package"] and done["has_key"]

    rep = client.get(f"/api/jobs/{job['id']}/report").json()
    assert rep["readiness"] == "ready"
    assert rep["reconciliation"]["wallet"]["opening_credits"]["amount_minor"] == str(exp["positive_sum"])

    rv = client.get(f"/api/jobs/{job['id']}/review?limit=5").json()
    assert rv["total"] == rep["review_count"] and len(rv["items"]) == 5
    rv = client.get(f"/api/jobs/{job['id']}/review?kind=service&severity=blocker").json()
    assert {i["code"] for i in rv["items"]} <= {"ORPHAN", "AMBIGUOUS_OWNERSHIP"}

    pv = client.get(f"/api/jobs/{job['id']}/preview/customers?limit=3").json()
    assert len(pv["items"]) == 3
    assert client.get(f"/api/jobs/{job['id']}/preview/..%2F..%2Fx").status_code in (400, 404)

    pkg = client.get(f"/api/jobs/{job['id']}/download")
    assert pkg.status_code == 200 and pkg.content.startswith(b"NXPKG\x00")
    key = client.get(f"/api/jobs/{job['id']}/key")
    assert key.status_code == 200 and key.text.startswith("nxkey1:")

    # history is non-sensitive
    hist = read_history()
    assert hist and hist[0]["job_id"] == job["id"]
    blob = str(hist)
    assert "Zip-Pass-123" not in blob and "7000000" not in blob and "nxkey1" not in blob

    assert client.delete(f"/api/jobs/{job['id']}", headers=h).status_code == 200
    assert client.get(f"/api/jobs/{job['id']}").status_code == 404


def test_ui_reconnect_reattaches_to_running_job(app_client, tmp_path):
    client, _ = app_client
    h = login(client)
    zp, _ = make_backup(tmp_path, users=100)
    job = client.post("/api/jobs", content=zp.read_bytes(), headers=h).json()
    client.post(f"/api/jobs/{job['id']}/start", json={"money_unit": "toman"}, headers=h)
    # Simulated page reload: the browser keeps its cookie but loses JS state (CSRF).
    r = client.post("/api/auth", json={"token": ""})
    assert r.status_code == 200
    h2 = {"X-CSRF-Token": r.json()["csrf"]}
    assert h2 == h
    ids = [j["id"] for j in client.get("/api/jobs").json()["items"]]
    assert job["id"] in ids
    assert wait(client, job["id"])["status"] == "completed"


def test_cancel_job(app_client, tmp_path):
    client, _ = app_client
    h = login(client)
    zp, _ = make_backup(tmp_path, users=20000)
    job = client.post("/api/jobs", content=zp.read_bytes(), headers=h).json()
    client.post(f"/api/jobs/{job['id']}/start", json={"money_unit": "toman"}, headers=h)
    client.post(f"/api/jobs/{job['id']}/cancel", headers=h)
    done = wait(client, job["id"])
    assert done["status"] == "cancelled"
    assert not done["has_package"]


def test_compare_with_previous_job(app_client, tmp_path):
    client, _ = app_client
    h = login(client)
    a, _ = make_backup(tmp_path, users=80, name="a.zip")
    b, _ = make_backup(tmp_path, users=80, shift=3, name="b.zip")
    ja = client.post("/api/jobs", content=a.read_bytes(), headers=h).json()
    client.post(f"/api/jobs/{ja['id']}/start", json={"money_unit": "toman"}, headers=h)
    assert wait(client, ja["id"])["status"] == "completed"
    jb = client.post("/api/jobs", content=b.read_bytes(), headers=h).json()
    r = client.post(f"/api/jobs/{jb['id']}/start", json={"money_unit": "toman", "previous_job_id": ja["id"]},
                    headers=h)
    assert r.status_code == 202, r.text
    assert wait(client, jb["id"])["status"] == "completed"
    rep = client.get(f"/api/jobs/{jb['id']}/report").json()
    assert rep["diff"]["changed"]["wallet_balances"] == 3
    assert rep["readiness"] == "blocked"


def test_settings_validation(app_client):
    client, _ = app_client
    h = login(client)
    assert client.put("/api/settings", json={"max_ratio": 1}, headers=h).status_code == 400
    r = client.put("/api/settings", json={"max_entries": 10}, headers=h)
    assert r.status_code == 200 and r.json()["max_entries"] == 10

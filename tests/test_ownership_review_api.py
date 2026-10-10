"""Ownership review over HTTP + the read-only panel probe against a local fake panel (127.0.0.1).

No real panel is contacted. The fake panel records every request so the tests can prove that only
the login and per-user GET calls are ever sent.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from conftest import make_backup
from mirza2nexa import panel_probe as pp
from mirza2nexa.jobs import JobManager
from mirza2nexa.pipeline import run_job
from mirza2nexa.server import create_app
from mirza2nexa.settings import Settings

PORT = 18997
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = "launch-token-ownership-review"


# ---- fake Marzban panel -----------------------------------------------------------------------

class FakePanel:
    def __init__(self, statuses: dict[str, str] | None = None, *, redirect: bool = False):
        self.statuses = statuses or {}
        self.requests: list[tuple[str, str]] = []
        self.redirect = redirect
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence
                pass

            def _send(self, code: int, body: dict | None = None, headers: dict | None = None):
                data = json.dumps(body or {}).encode()
                self.send_response(code)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):  # noqa: N802
                outer.requests.append(("POST", self.path))
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n).decode()
                if self.path == "/api/admin/token" and "username=admin" in body and "password=pw" in body:
                    return self._send(200, {"access_token": "tok", "token_type": "bearer"})
                return self._send(401, {"detail": "bad"})

            def do_GET(self):  # noqa: N802
                outer.requests.append(("GET", self.path))
                if outer.redirect:
                    return self._send(302, headers={"Location": "/api/user/x/reset"})
                if self.headers.get("Authorization") != "Bearer tok":
                    return self._send(401)
                name = self.path.rsplit("/", 1)[-1]
                if name in outer.statuses:
                    return self._send(200, {"username": name, "status": outer.statuses[name],
                                            "subscription_url": "SHOULD-NOT-BE-STORED"})
                return self._send(404, {"detail": "User not found"})

            def do_DELETE(self):  # noqa: N802
                outer.requests.append(("DELETE", self.path))
                self._send(200)

            do_PUT = do_DELETE  # noqa: N815

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture()
def panel():
    p = FakePanel({"syn1": "active", "syn2": "disabled", "syn3": "limited", "syn4": "expired", "syn5": "on_hold"})
    yield p
    p.close()


# ---- probe ------------------------------------------------------------------------------------

def test_probe_maps_statuses_and_only_reads(panel):
    probe = pp.make_probe("marzban", panel.url, "admin", "pw")
    assert [probe.check(u) for u in ("syn1", "syn2", "syn3", "syn4", "syn5", "ghost")] == [
        "LIVE_ACTIVE", "LIVE_DISABLED", "LIVE_LIMITED", "LIVE_EXPIRED", "LIVE_ON_HOLD", "NOT_FOUND_ON_PANEL"]
    assert probe.check("bad name/../x") == "CHECK_ERROR"
    probe.close()
    methods = {m for m, _ in panel.requests}
    assert methods == {"POST", "GET"}
    assert [p for m, p in panel.requests if m == "POST"] == ["/api/admin/token"]
    assert all(p.startswith("/api/user/") for m, p in panel.requests if m == "GET")


def test_read_only_client_refuses_everything_else(panel):
    http = pp.ReadOnlyHttp(panel.url)
    for method, path in (("DELETE", "/api/user/syn1"), ("PUT", "/api/user/syn1"), ("POST", "/api/user"),
                         ("POST", "/api/user/syn1/reset"), ("GET", "/api/users"), ("GET", "/api/user/../admin"),
                         ("GET", "/api/user/syn1/usage"), ("POST", "/api/admin/token/../user")):
        with pytest.raises(pp.ReadOnlyViolation):
            http.request(method, path)
    assert panel.requests == []  # refused before any socket was opened


def test_redirects_are_refused():
    p = FakePanel(redirect=True)
    try:
        probe = pp.make_probe("marzban", p.url, "admin", "pw")
        with pytest.raises(pp.ReadOnlyViolation):
            probe.check("syn1")
        assert ("GET", "/api/user/x/reset") not in p.requests
    finally:
        p.close()


def test_probe_configuration_errors(panel):
    with pytest.raises(pp.ProbeError) as e:
        pp.make_probe("x-ui_single", panel.url, "admin", "pw")
    assert e.value.code == "panel.unsupported"
    for url in ("ftp://h", "file:///etc/passwd", "http://user:pw@h", "http://h/?q=1", "", "javascript:x"):
        with pytest.raises(pp.ProbeError):
            pp.make_probe("marzban", url, "admin", "pw")
    probe = pp.make_probe("marzban", panel.url, "admin", "wrong")
    with pytest.raises(pp.ProbeError) as e:
        probe.check("syn1")
    assert e.value.code == "panel.login_failed" and "wrong" not in e.value.message_fa


# ---- HTTP API ---------------------------------------------------------------------------------

@pytest.fixture()
def api(tmp_path):
    settings = Settings(workspace_dir=str(tmp_path))
    manager = JobManager(settings, run_job)
    app = create_app(manager, port=PORT, launch_token=TOKEN)
    with TestClient(app, base_url=BASE) as client:
        csrf = client.post("/api/auth", json={"token": TOKEN}).json()["csrf"]
        zp, exp = make_backup(tmp_path, users=200, name="own.zip")
        job = client.post("/api/jobs", content=zp.read_bytes(), headers={"X-CSRF-Token": csrf}).json()
        assert client.post(f"/api/jobs/{job['id']}/start", json={"money_unit": "toman"},
                           headers={"X-CSRF-Token": csrf}).status_code == 202
        deadline = time.time() + 180
        while client.get(f"/api/jobs/{job['id']}").json()["status"] not in ("completed", "failed", "cancelled"):
            assert time.time() < deadline
            time.sleep(0.1)
        yield client, manager, {"X-CSRF-Token": csrf}, f"/api/jobs/{job['id']}/ownership"
    manager.shutdown()


def test_review_flow_over_http(api):
    client, manager, h, base = api
    s0 = client.get(base + "/summary").json()
    assert s0["totals"]["items"] > 0 and s0["admin_attested_is_proof"] is False
    assert s0["live"]["enabled"] is False
    facets = client.get(base + "/facets").json()
    assert {"panel", "status_class", "reason", "review_state", "live_status", "eligibility", "final_class"} <= set(facets)
    page = client.get(base + "/items", params={"eligibility": "ATTESTABLE", "limit": 500}).json()
    attestable = page["total"]
    assert attestable >= 1

    key = page["items"][0]["key"]
    d = client.get(base + "/items/" + key).json()
    assert d["viewed"] is True and d["owner"]["in_user_table"] is True and isinstance(d["evidence"], list)

    # CSRF is required for state changes
    sel = {"mode": "filter", "filter": {"eligibility": ["ATTESTABLE"]}}
    assert client.post(base + "/plan", json={"action": "APPROVE", "selection": sel}).status_code == 403
    plan = client.post(base + "/plan", json={"action": "APPROVE", "selection": sel}, headers=h).json()
    assert plan["applicable"] == attestable and plan["unreviewed"]["count"] == attestable - 1
    bad = client.post(base + "/commit", json={"plan_id": plan["plan_id"], "confirm_count": 0, "acknowledge": True},
                      headers=h)
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "ownership.confirm_mismatch"
    ok = client.post(base + "/commit", json={"plan_id": plan["plan_id"], "confirm_count": attestable,
                                             "acknowledge": True}, headers=h).json()
    assert ok["applied"] == attestable
    s1 = client.get(base + "/summary").json()
    assert s1["totals"]["ADMIN_ATTESTED"] == attestable and s1["totals"]["PROVEN"] == s0["totals"]["PROVEN"]

    # readiness keeps administrator decisions apart from proven ownership
    job_base = base.rsplit("/ownership", 1)[0]
    rd = client.get(job_base + "/readiness").json()
    ar = rd["ownership"]["admin_review"]
    assert ar["admin_attested"] == attestable and ar["proven"] == s0["totals"]["PROVEN"]
    assert ar["admin_attested_is_proof"] is False

    batches = client.get(base + "/batches").json()["items"]
    assert batches[0]["batch_id"] == ok["batch_id"]
    undo = client.post(base + "/plan", json={"action": "UNDO", "selection": {"mode": "batch",
                                                                             "batch_id": ok["batch_id"]}},
                       headers=h).json()
    client.post(base + "/commit", json={"plan_id": undo["plan_id"], "confirm_count": undo["applicable"],
                                        "acknowledge": True}, headers=h)
    assert client.get(base + "/summary").json()["totals"]["ADMIN_ATTESTED"] == 0
    assert client.get(base + "/audit/verify").json()["ok"] is True
    assert client.get(base + "/audit", params={"limit": 5}).json()["total"] >= 2 * attestable

    # signed export with the in-memory generated key
    ex = client.get(base + "/export.json")
    assert ex.status_code == 200 and "attachment" in ex.headers["content-disposition"]
    doc = ex.json()
    assert doc["authentication"]["alg"] == "HMAC-SHA256" and doc["sealed"] is False
    assert len(doc["package_header_sha256"]) == 64 and doc["audit"]["ok"] is True

    total = client.get(base + "/summary").json()["totals"]["items"]
    assert client.post(base + "/seal", json={"confirm": "SEAL", "confirm_count": total}, headers=h).json()["sealed"]
    r = client.post(base + "/plan", json={"action": "APPROVE", "selection": sel}, headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "ownership.sealed"


def test_live_check_disabled_by_default_then_read_only(api, panel):
    client, manager, h, base = api
    body = {"panel_code": "syn-a", "base_url": panel.url, "username": "admin", "password": "pw"}
    r = client.post(base + "/live-check", json=body, headers=h)
    assert r.status_code == 403 and r.json()["error"]["code"] == "panel.live_check_disabled"
    assert panel.requests == []

    manager.settings.panel_live_check_enabled = True
    r = client.post(base + "/live-check", json={**body, "panel_code": "nope"}, headers=h)
    assert r.status_code == 400
    r = client.post(base + "/live-check", json=body, headers=h)
    assert r.status_code == 202, r.text
    deadline = time.time() + 60
    st = r.json()
    while st["running"]:
        assert time.time() < deadline
        time.sleep(0.05)
        st = client.get(base + "/live-check").json()
    assert st["error"] is None and st["done"] == st["total"] > 0
    assert "pw" not in json.dumps(st) and "admin" not in json.dumps(st)
    assert {m for m, _ in panel.requests} <= {"POST", "GET"}
    s = client.get(base + "/summary").json()
    assert s["live"]["checked"] == st["total"]
    items = client.get(base + "/items", params={"panel": "syn-a", "live_status": "LIVE_ACTIVE"}).json()
    assert items["total"] >= 1
    # live status is a risk signal in the plan, never a change of ownership class
    assert s["totals"]["ADMIN_ATTESTED"] == 0


def test_dot_usernames_and_cleartext_logins_are_refused(panel):
    probe = pp.make_probe("marzban", panel.url, "admin", "pw")
    assert probe.check("..") == "CHECK_ERROR" and probe.check(".") == "CHECK_ERROR"
    assert not any(p.endswith("/..") or p.endswith("/.") for _, p in panel.requests)
    with pytest.raises(pp.ProbeError) as e:
        pp.make_probe("marzban", "http://203.0.113.9:8000", "admin", "pw")
    assert e.value.code == "panel.https_required"
    for bad in ("http://h\r\nX: y", "https://h/../x", "https://h/a b"):
        with pytest.raises(pp.ProbeError):
            pp.validate_base_url(bad)

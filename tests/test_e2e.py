"""Browser end-to-end tests: real uvicorn server + Chromium (Playwright), synthetic data only.

Run with ``pytest -m e2e``. Set ``PLAYWRIGHT_CHROMIUM_EXECUTABLE`` to use a specific Chromium
binary; otherwise Playwright's default install is used.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

playwright_sync = pytest.importorskip("playwright.sync_api")
uvicorn = pytest.importorskip("uvicorn")
httpx = pytest.importorskip("httpx")

from conftest import make_backup  # noqa: E402
from mirza2nexa.jobs import JobManager, history_path, read_history  # noqa: E402
from mirza2nexa.pipeline import run_job  # noqa: E402
from mirza2nexa.server import create_app  # noqa: E402
from mirza2nexa.settings import Settings  # noqa: E402
from mirza2nexa.synth import TG_BASE  # noqa: E402

PASSWORD = "e2e-Secret-pw-۴۲"
LONG = 180_000  # ms; conversion of the large synthetic backup on a slow CI runner
PERSIAN = re.compile(r"[؀-ۿ]")

# Records CSP violations and uncaught errors from inside the page. Init scripts are injected
# by the browser itself, so the page's strict CSP does not block them.
INIT_SCRIPT = """
document.addEventListener('securitypolicyviolation', (e) => {
  console.error('securitypolicyviolation: ' + e.violatedDirective + ' ' + e.blockedURI);
});
"""


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class LiveServer:
    port: int
    app: object
    manager: JobManager
    data_dir: Path

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def new_token(self) -> str:
        """Launch tokens are one-time; arm a fresh one for each browser context."""
        token = secrets.token_urlsafe(16)
        self.app.state.m2n.launch_token = token
        return token


@pytest.fixture(scope="module")
def server(tmp_path_factory) -> Iterator[LiveServer]:
    root = tmp_path_factory.mktemp("e2e")
    home = root / "home"
    home.mkdir()
    ws = root / "ws"
    ws.mkdir()
    old_home = os.environ.get("MIRZA2NEXA_HOME")
    os.environ["MIRZA2NEXA_HOME"] = str(home)
    port = _free_port()
    manager = JobManager(Settings(workspace_dir=str(ws)), run_job)
    app = create_app(manager, port=port, launch_token=secrets.token_urlsafe(16))
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=srv.run, daemon=True, name="e2e-uvicorn")
    th.start()
    deadline = time.monotonic() + 30
    while not srv.started:
        if time.monotonic() > deadline or not th.is_alive():
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.05)
    try:
        yield LiveServer(port=port, app=app, manager=manager, data_dir=root)
    finally:
        srv.should_exit = True
        th.join(timeout=15)
        manager.shutdown()
        if old_home is None:
            os.environ.pop("MIRZA2NEXA_HOME", None)
        else:
            os.environ["MIRZA2NEXA_HOME"] = old_home


@pytest.fixture(scope="module")
def browser() -> Iterator[object]:
    with playwright_sync.sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None)
        try:
            yield b
        finally:
            b.close()


@dataclass
class Problems:
    console: list[str] = field(default_factory=list)
    page_errors: list[str] = field(default_factory=list)

    def assert_clean(self) -> None:
        csp = [m for m in self.console if "securitypolicyviolation" in m or "Content Security Policy" in m]
        assert not csp, csp
        assert not self.page_errors, self.page_errors
        # HTTP error statuses we provoke on purpose (wrong password etc.) are logged by Chromium as
        # "Failed to load resource"; anything else is an application error.
        other = [m for m in self.console if "Failed to load resource" not in m]
        assert not other, other


@pytest.fixture
def ui(server: LiveServer, browser) -> Iterator[tuple[object, Problems]]:
    ctx = browser.new_context(viewport={"width": 1366, "height": 900}, accept_downloads=True, locale="fa-IR")
    ctx.set_default_timeout(30_000)
    ctx.add_init_script(INIT_SCRIPT)
    page = ctx.new_page()
    probs = Problems()
    page.on("console", lambda m: probs.console.append(f"{m.type}: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: probs.page_errors.append(str(e)))
    try:
        yield page, probs
    finally:
        ctx.close()


# ---------------------------------------------------------------------------------- helpers
def open_app(page, server: LiveServer) -> None:
    page.goto(f"{server.base}/?t={server.new_token()}")
    page.wait_for_selector("[data-testid=nav-dashboard]", state="visible")


def upload(page, zip_path: Path) -> None:
    page.click("[data-testid=nav-new]")
    page.set_input_files("[data-testid=zip-input]", str(zip_path))
    page.click("[data-testid=upload-btn]")
    page.wait_for_selector("#step-2:not([hidden])", timeout=60_000)
    page.click("[data-testid=to-step-3]")
    page.wait_for_selector("[data-testid=start-btn]", state="visible")


def start(page, *, password: str | None, money_unit: str | None) -> None:
    if password is not None:
        page.fill("[data-testid=zip-password]", password)
    page.select_option("[data-testid=money-unit]", money_unit or "")
    page.click("[data-testid=start-btn]")


def wait_status(page, *statuses: str, timeout: int = LONG) -> str:
    sel = ", ".join(f"[data-testid=job-status][data-status={s}]" for s in statuses)
    page.wait_for_selector(sel, timeout=timeout)
    return page.get_attribute("[data-testid=job-status]", "data-status")


def wait_terminal(page, timeout: int = LONG) -> str:
    st = wait_status(page, "completed", "failed", "cancelled", timeout=timeout)
    if st != "completed" and page.is_visible("[data-testid=error-box]"):
        print("error box:", page.text_content("[data-testid=error-box]"))
    return st


def read_download(page, testid: str) -> tuple[str, bytes]:
    with page.expect_download() as info:
        page.click(f"[data-testid={testid}]")
    d = info.value
    return d.suggested_filename, Path(d.path()).read_bytes()


# ---------------------------------------------------------------------------------- tests
def test_rtl_document(ui, server):
    page, probs = ui
    open_app(page, server)
    assert page.get_attribute("html", "dir") == "rtl"
    assert page.get_attribute("html", "lang") == "fa"
    probs.assert_clean()


def test_full_happy_path_encrypted(ui, server, tmp_path):
    page, probs = ui
    zp, exp = make_backup(tmp_path, users=300, password=PASSWORD, name="happy.zip")

    open_app(page, server)
    assert "t=" not in page.url, page.url  # one-time launch token is scrubbed from the address bar

    upload(page, zp)
    page.wait_for_selector("[data-testid=zip-password]", state="visible")

    # wrong password -> inline Persian error, no navigation
    start(page, password="definitely-wrong", money_unit="toman")
    err = page.wait_for_selector("[data-testid=zip-password-error]:not([hidden])")
    assert PERSIAN.search(err.text_content() or ""), err.text_content()
    assert page.get_attribute("[data-testid=zip-password]", "aria-invalid") == "true"

    start(page, password=PASSWORD, money_unit="toman")
    assert wait_terminal(page) == "completed"
    page.wait_for_selector("[data-testid=overall-percent][data-value='100']")

    # report / readiness
    page.click("[data-testid=nav-report]")
    page.wait_for_selector("[data-testid=readiness][data-readiness=ready]")

    # review queue
    page.click("[data-testid=nav-review]")
    page.wait_for_selector("[data-testid=review-table] tbody tr[data-severity]")
    assert page.locator("[data-testid=review-table] tbody tr[data-severity]").count() > 0

    # preview
    page.click("[data-testid=nav-preview]")
    page.wait_for_selector("[data-testid=preview-entity] option[value=customers]", state="attached")
    page.select_option("[data-testid=preview-entity]", "customers")
    page.wait_for_selector("[data-testid=preview-table]:not([hidden]) tbody tr")
    assert page.locator("[data-testid=preview-table] tbody tr").count() > 0

    # downloads
    page.click("[data-testid=nav-download]")
    page.wait_for_selector("[data-testid=download-pkg][href]", state="visible")
    name, data = read_download(page, "download-pkg")
    assert name.endswith(".nxpkg"), name
    assert data.startswith(b"NXPKG\x00")
    page.wait_for_selector("[data-testid=download-key][href]", state="visible")
    _, key = read_download(page, "download-key")
    assert key.decode("utf-8").startswith("nxkey1:")

    # history (UI and on disk)
    page.click("[data-testid=nav-history]")
    page.wait_for_selector("[data-testid=history-table]:not([hidden]) tbody tr[data-job-id]")
    assert page.locator("[data-testid=history-table] tbody tr[data-job-id]").count() >= 1

    items = read_history()
    assert items and items[0]["status"] == "completed"
    raw = history_path().read_text(encoding="utf-8")
    assert PASSWORD not in raw
    assert "definitely-wrong" not in raw
    tg_ids = {str(TG_BASE + n) for n in range(exp["users"])}
    numbers = set(re.findall(r"\d{6,}", raw))
    assert not numbers & tg_ids, numbers & tg_ids
    assert "key_text" not in json.dumps(items) and "nxkey1:" not in raw

    probs.assert_clean()


def test_reload_mid_processing_reattaches(ui, server, tmp_path):
    page, probs = ui
    zp, _ = make_backup(tmp_path, users=60_000, password=PASSWORD, name="big.zip")
    open_app(page, server)
    upload(page, zp)
    start(page, password=PASSWORD, money_unit="toman")
    before = wait_status(page, "running", "completed", "failed", "cancelled", timeout=60_000)
    assert before == "running", "job finished before the reload could exercise re-attach"

    page.reload()
    page.wait_for_selector("[data-testid=nav-dashboard]", state="visible")
    # UI re-attaches to the server-side job (status view restored from the session) and follows it
    assert wait_terminal(page) == "completed"
    page.wait_for_selector("[data-testid=overall-percent][data-value='100']")
    probs.assert_clean()


def test_cancel_from_ui(ui, server, tmp_path):
    page, probs = ui
    zp, _ = make_backup(tmp_path, users=60_000, password=PASSWORD, name="cancel.zip")
    open_app(page, server)
    upload(page, zp)
    start(page, password=PASSWORD, money_unit="toman")
    assert wait_status(page, "queued", "running", "completed", timeout=60_000) != "completed"
    page.click("[data-testid=cancel-btn]")
    page.click("[data-testid=confirm-ok]")
    assert wait_status(page, "cancelled", "completed", "failed") == "cancelled"
    page.wait_for_selector("[data-testid=cancel-btn]", state="hidden")
    probs.assert_clean()


def test_without_money_unit_is_blocked(ui, server, tmp_path):
    page, probs = ui
    zp, _ = make_backup(tmp_path, users=200, name="nounit.zip")  # unencrypted path
    open_app(page, server)
    upload(page, zp)
    assert page.is_hidden("[data-testid=zip-password]")
    start(page, password=None, money_unit=None)
    assert wait_terminal(page) == "completed"
    page.click("[data-testid=nav-report]")
    banner = page.wait_for_selector("[data-testid=readiness][data-readiness=blocked]")
    assert PERSIAN.search(banner.text_content() or "")
    probs.assert_clean()


def test_foreign_host_and_origin_rejected(server):
    url = f"{server.base}/api/health"
    assert httpx.get(url).status_code == 200
    r = httpx.get(url, headers={"Host": f"evil.example:{server.port}"})
    assert r.status_code == 421
    assert r.json()["error"]["code"] == "security.host"
    r = httpx.get(url, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "security.origin"
    # a DNS-rebinding style request to the static UI is refused too
    assert httpx.get(f"{server.base}/", headers={"Host": "attacker.test"}).status_code == 421
    # foreign origin cannot exchange a valid launch token for a session
    token = server.new_token()
    r = httpx.post(f"{server.base}/api/auth", json={"token": token},
                   headers={"Origin": f"http://localhost.evil:{server.port}"})
    assert r.status_code == 403
    assert "set-cookie" not in r.headers


def test_coverage_page_package_preview_and_persian_reports(ui, server, tmp_path):
    """Phase 2 pages: coverage table with an uncatalogued (hostile-named) table, Persian HTML
    report and coverage CSV downloads, and the full package preview with the importer warning."""
    import io
    import zipfile

    from mirza2nexa.nxpkg.manifest import PACKAGE_SCHEMA_VERSION
    from mirza2nexa.synth import SynthSpec, write_dump

    evil = "x<img src=x onerror=alert(1)>"
    buf = io.StringIO()
    write_dump(buf, SynthSpec(users=120, seed=5))
    buf.write(f"CREATE TABLE `{evil}` (`id` int NOT NULL, PRIMARY KEY (`id`));\n"
              f"INSERT INTO `{evil}` VALUES (1),(2);\n")
    zp = tmp_path / "coverage.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("backup.sql", buf.getvalue())

    page, probs = ui
    open_app(page, server)
    upload(page, zp)
    start(page, password=None, money_unit="toman")
    assert wait_terminal(page) == "completed"

    # coverage page
    page.click("[data-testid=nav-coverage]")
    page.wait_for_selector("[data-testid=coverage-table] tbody tr[data-table]")
    assert page.is_visible("[data-testid=coverage-summary]")
    assert page.text_content("[data-testid=cov-total] .stat-value")
    row = page.locator("[data-testid=coverage-table] tbody tr[data-table]").filter(has_text=evil)
    assert row.count() == 1
    assert row.get_attribute("data-status") == "blocked"
    assert "مسدود" in (row.locator("[data-testid=coverage-status-badge]").text_content() or "")
    assert page.locator("[data-testid=uncovered-list] li[data-table]").filter(has_text=evil).count() == 1
    assert page.locator("img[src=x]").count() == 0  # rendered as text, never as markup
    executed = page.locator("[data-testid=coverage-table] tbody tr[data-status=executed]").first
    assert "اجرا شد" in (executed.text_content() or "")
    page.select_option("[data-testid=coverage-status]", "blocked")
    page.wait_for_function(
        "() => [...document.querySelectorAll('[data-testid=coverage-table] tbody tr[data-table]')]"
        ".every(r => r.dataset.status === 'blocked')")
    name, data = read_download(page, "download-coverage-csv")
    assert name.endswith(".csv") and data.startswith(b"\xef\xbb\xbf")
    assert evil in data.decode("utf-8")

    # report page: Persian sections and the HTML report download
    page.click("[data-testid=nav-report]")
    page.wait_for_selector("[data-testid=readiness][data-readiness=blocked]")
    assert page.is_visible("[data-testid=section-operational]")
    assert page.is_visible("[data-testid=section-archive]")
    assert page.is_visible("[data-testid=section-review]")
    assert page.locator("[data-testid=blocker-groups] [data-category=schema] [data-code=UNCLASSIFIED_TABLE]").count() == 1
    recon = page.text_content("[data-testid=reconciliation]") or ""
    assert "موجودی افتتاحیه" in recon and "opening_credits" in recon  # Persian label + small code
    name, data = read_download(page, "download-report-html")
    assert name.endswith(".html")
    html_text = data.decode("utf-8")
    assert "<script" not in html_text.lower() and "<img" not in html_text.lower()
    assert "x&lt;img" in html_text

    # download page: full package preview
    page.click("[data-testid=nav-download]")
    page.wait_for_selector("[data-testid=package-preview]:not([hidden]) [data-testid=package-files] tbody tr")
    assert page.text_content("[data-testid=contract-version]") == PACKAGE_SCHEMA_VERSION
    warn = page.locator("[data-testid=importer-warning]")
    assert warn.is_visible() and "Importer" in (warn.text_content() or "")
    assert page.locator("[data-testid=package-files] tbody tr[data-path='records/customers.jsonl']").count() == 1
    probs.assert_clean()


def test_migration_readiness_page_and_downloads(ui, server, tmp_path):
    """Phase 3: «آمادگی مهاجرت به NEXA» renders every section after a synthetic conversion and
    both downloads (JSON + script-free Persian HTML) work; no personal data appears on the page."""
    zp, exp = make_backup(tmp_path, users=150, name="readiness.zip")
    page, probs = ui
    open_app(page, server)

    # before any completed job the page explains itself in Persian
    page.click("[data-testid=nav-readiness]")
    assert PERSIAN.search(page.text_content("#readiness-empty") or "")

    upload(page, zp)
    start(page, password=None, money_unit="toman")
    assert wait_terminal(page) == "completed"

    page.click("[data-testid=nav-readiness]")
    verdict = page.wait_for_selector("[data-testid=readiness-verdict][data-verdict]")
    code = verdict.get_attribute("data-verdict")
    assert code in {"NOT_READY_FOR_IMPORT", "READY_FOR_IMPORTER_DRY_RUN", "BLOCKED"}, code
    assert PERSIAN.search(verdict.text_content() or "")
    assert "مجوز Import نیست" in (verdict.text_content() or "")
    assert page.is_visible("[data-testid=readiness-disclaimer]")
    for tid in ("readiness-financial", "readiness-coverage", "readiness-ownership", "readiness-panels",
                "readiness-review-groups", "readiness-owner-decisions", "readiness-import-blockers"):
        assert page.is_visible(f"[data-testid={tid}]"), tid
        assert PERSIAN.search(page.text_content(f"[data-testid={tid}]") or ""), tid
    blockers = page.locator("[data-testid=readiness-import-blockers] li[data-code=IMPORTER_NOT_BUILT]")
    assert blockers.count() == 1
    assert page.locator("[data-testid=readiness-owner-decisions] li[data-decision]").count() >= 1

    body = page.text_content("#view-readiness") or ""
    tg_ids = {str(TG_BASE + n) for n in range(exp["users"])}
    assert not set(re.findall(r"\d{6,}", body)) & tg_ids
    assert "synthetic_user_" not in body

    name, data = read_download(page, "download-readiness-json")
    assert name.endswith(".json"), name
    doc = json.loads(data.decode("utf-8"))
    assert doc["verdict"]["code"] == code
    name, data = read_download(page, "download-readiness-html")
    assert name.endswith(".html"), name
    html_text = data.decode("utf-8")
    assert 'dir="rtl"' in html_text and "<script" not in html_text.lower()
    assert not set(re.findall(r"\d{6,}", html_text)) & tg_ids
    probs.assert_clean()

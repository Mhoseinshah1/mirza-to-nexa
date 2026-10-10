"""Read-only live status check of legacy services on a panel (optional, off by default).

Hard rule (CLAUDE.md): never provision, delete, renew or otherwise mutate panel services. This
module enforces it structurally: the HTTP client below can send exactly two requests —
``POST /api/admin/token`` (Marzban admin login) and ``GET /api/user/{username}`` — and refuses any
other method or path before a socket is opened. Redirects are refused. Credentials live only in
the probe object for one run and are never logged, stored or returned.

Only the status class is kept (no usage, links, subscription URLs or configs). A live status is a
risk signal for the administrator, never proof of ownership.
"""

from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Protocol

SUPPORTED_TYPES = frozenset({"marzban"})
MARZBAN_STATUS = {"active": "LIVE_ACTIVE", "disabled": "LIVE_DISABLED", "limited": "LIVE_LIMITED",
                  "expired": "LIVE_EXPIRED", "on_hold": "LIVE_ON_HOLD"}
# first character alphanumeric: no "." / ".." path segments can ever be formed
_USERNAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@\-]{0,63}")
_NETLOC = re.compile(r"(?:[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}"
                     r"[A-Za-z0-9])?)*|\[[0-9A-Fa-f:.]+\])(?::[0-9]{1,5})?")
_PATH = re.compile(r"(?:/[A-Za-z0-9_.~\-]+)*/?")
_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
MAX_RESPONSE_BYTES = 256 * 1024


class ProbeError(Exception):
    """Refused or failed probe configuration (message is safe to show; never contains secrets)."""

    def __init__(self, code: str, message_fa: str):
        super().__init__(code)
        self.code = code
        self.message_fa = message_fa


class ReadOnlyViolation(ProbeError):
    def __init__(self, detail: str):
        super().__init__("panel.read_only_violation", "درخواست غیرمجاز به پنل مسدود شد (فقط خواندن مجاز است).")
        self.detail = detail


class PanelProbe(Protocol):
    def check(self, username: str) -> str:  # returns one of ownership_review.LIVE_STATUSES
        ...

    def close(self) -> None:
        ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        raise ReadOnlyViolation(f"redirect {code} refused")


def validate_base_url(base_url: str) -> str:
    if not isinstance(base_url, str) or len(base_url) > 300:
        raise ProbeError("panel.bad_url", "آدرس پنل نامعتبر است.")
    u = urllib.parse.urlsplit(base_url.strip())
    if u.scheme not in {"http", "https"} or not u.hostname or u.username or u.password or u.query or u.fragment \
            or not _NETLOC.fullmatch(u.netloc) or not _PATH.fullmatch(u.path) \
            or any(seg in {".", ".."} for seg in u.path.split("/")):
        raise ProbeError("panel.bad_url", "آدرس پنل باید به شکل https://host[:port][/path] و بدون نام کاربری باشد.")
    if u.scheme == "http" and u.hostname not in _LOOPBACK:
        # the admin password and bearer token must never cross the network in clear text
        raise ProbeError("panel.https_required", "برای امنیت رمز ادمین، آدرس پنل باید با https شروع شود.")
    return urllib.parse.urlunsplit((u.scheme, u.netloc, u.path.rstrip("/"), "", ""))


@dataclass
class _Allowed:
    method: str
    path: str


class ReadOnlyHttp:
    """The only HTTP client a probe may use. It refuses everything except the allow-listed calls."""

    def __init__(self, base_url: str, *, timeout: float = 10.0):
        self.base = validate_base_url(base_url)
        self.base_path = urllib.parse.urlsplit(self.base).path
        self.timeout = timeout
        ctx = ssl.create_default_context()  # certificates are always verified
        self._opener = urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(context=ctx),
                                                   urllib.request.ProxyHandler({}))
        self.sent: list[_Allowed] = []

    def _check_allowed(self, method: str, path: str) -> None:
        rel = path[len(self.base_path):] if path.startswith(self.base_path) else None
        if method == "POST" and rel == "/api/admin/token":
            return
        if method == "GET" and rel is not None and rel.startswith("/api/user/"):
            name = urllib.parse.unquote(rel[len("/api/user/"):])
            if _USERNAME.fullmatch(name):
                return
        raise ReadOnlyViolation(f"{method} {path}")

    def request(self, method: str, path: str, *, data: bytes | None = None,
                headers: dict[str, str] | None = None) -> tuple[int, bytes]:
        full_path = self.base_path + path
        self._check_allowed(method, full_path)
        req = urllib.request.Request(self.base + path, data=data, method=method,  # noqa: S310 - http(s) only
                                     headers=headers or {})
        self.sent.append(_Allowed(method, full_path))
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:  # noqa: S310 - allow-listed above
                return resp.status, resp.read(MAX_RESPONSE_BYTES + 1)[:MAX_RESPONSE_BYTES]
        except urllib.error.HTTPError as e:
            return e.code, b""


class MarzbanReadOnlyProbe:
    """Marzban: login once, then ``GET /api/user/{username}`` per service."""

    def __init__(self, base_url: str, admin_username: str, admin_password: str, *, timeout: float = 10.0):
        if not admin_username or not admin_password:
            raise ProbeError("panel.credentials_required", "نام کاربری و رمز ادمین پنل لازم است.")
        self.http = ReadOnlyHttp(base_url, timeout=timeout)
        self._creds: tuple[str, str] | None = (admin_username, admin_password)
        self._token: str | None = None

    def _login(self) -> None:
        assert self._creds is not None
        body = urllib.parse.urlencode({"username": self._creds[0], "password": self._creds[1]}).encode()
        try:
            status, raw = self.http.request("POST", "/api/admin/token", data=body,
                                            headers={"Content-Type": "application/x-www-form-urlencoded",
                                                     "Accept": "application/json"})
        except (urllib.error.URLError, OSError, TimeoutError):
            raise ProbeError("panel.unreachable", "اتصال به پنل برقرار نشد.") from None
        finally:
            self._creds = None  # used once; never kept longer than needed
        if status != 200:
            raise ProbeError("panel.login_failed", "ورود به پنل ناموفق بود (نام کاربری یا رمز ادمین).")
        try:
            token = json.loads(raw).get("access_token")
        except (ValueError, AttributeError):
            token = None
        if not isinstance(token, str) or not token:
            raise ProbeError("panel.login_failed", "پاسخ ورود پنل نامعتبر است.")
        self._token = token

    def check(self, username: str) -> str:
        if not isinstance(username, str) or not _USERNAME.fullmatch(username):
            return "CHECK_ERROR"
        if self._token is None:
            self._login()
        try:
            status, raw = self.http.request("GET", "/api/user/" + urllib.parse.quote(username, safe=""),
                                            headers={"Authorization": f"Bearer {self._token}",
                                                     "Accept": "application/json"})
        except ReadOnlyViolation:
            raise
        except (urllib.error.URLError, OSError, TimeoutError):
            return "CHECK_ERROR"
        if status == 404:
            return "NOT_FOUND_ON_PANEL"
        if status != 200:
            return "CHECK_ERROR"
        try:
            st = json.loads(raw).get("status")
        except (ValueError, AttributeError):
            return "CHECK_ERROR"
        return MARZBAN_STATUS.get(st, "CHECK_ERROR") if isinstance(st, str) else "CHECK_ERROR"

    def close(self) -> None:
        self._token = None
        self._creds = None


def make_probe(panel_type: str | None, base_url: str, username: str, password: str, **kw) -> PanelProbe:
    if (panel_type or "").lower() not in SUPPORTED_TYPES:
        raise ProbeError("panel.unsupported", "بررسی زنده فقط برای پنل‌های نوع Marzban پشتیبانی می‌شود.")
    return MarzbanReadOnlyProbe(base_url, username, password, **kw)

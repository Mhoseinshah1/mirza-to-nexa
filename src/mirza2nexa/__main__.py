"""Launcher: start the local server on 127.0.0.1 and open the browser.

Usage::

    mirza2nexa                # pick a free port, open the browser
    mirza2nexa --port 8765 --no-browser
    mirza2nexa convert backup.zip -o out.nxpkg   # headless CLI (see cli.py)
"""

from __future__ import annotations

import argparse
import secrets
import socket
import sys
import threading
import webbrowser

BIND_HOST = "127.0.0.1"  # never configurable: the app must not listen on other interfaces


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((BIND_HOST, 0))
        return s.getsockname()[1]


def _utf8_console() -> None:
    """Windows consoles/pipes default to a legacy code page (e.g. cp1252) that cannot encode
    Persian text; printing the banner must never crash the app."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass


def serve(port: int | None = None, open_browser: bool = True, launch_token: str | None = None) -> None:
    import uvicorn

    from .jobs import JobManager, sweep_stale_workspaces
    from .pipeline import run_job
    from .server import create_app
    from .settings import Settings

    sweep_stale_workspaces()
    port = port or _free_port()
    token = launch_token or secrets.token_urlsafe(24)
    manager = JobManager(Settings.load(), run_job)
    app = create_app(manager, port=port, launch_token=token)
    url = f"http://{BIND_HOST}:{port}/?t={token}"

    config = uvicorn.Config(app, host=BIND_HOST, port=port, log_level="warning", access_log=False,
                            server_header=False, date_header=False, timeout_keep_alive=30)
    server = uvicorn.Server(config)

    def _open() -> None:
        if open_browser:
            webbrowser.open(url)

    _utf8_console()
    print("Mirza → NEXA Migration Studio")
    print(f"آدرس برنامه (فقط روی همین کامپیوتر): {url}")
    print("برای خروج این پنجره را ببندید یا Ctrl+C بزنید.")
    sys.stdout.flush()
    threading.Timer(0.8, _open).start()
    try:
        server.run()
    finally:
        manager.shutdown()


def _registry_load_error(argv: list[str]) -> int | None:
    """Load every converter module up front; on failure explain it clearly and exit 3.

    This does not hide anything: the app stops. It only replaces a raw traceback (or a console
    window that flashes and closes) with a Persian explanation and machine-readable JSON.
    """
    try:
        from . import modules  # noqa: F401
    except ImportError as exc:
        import json

        missing = getattr(exc, "name", None) or str(exc)
        msg = ("کاتالوگ جدول‌های میرزا در این نسخه برنامه کامل بارگذاری نشده است (ماژول گم‌شده: "
               f"{missing})؛ تبدیل انجام نمی‌شود. نسخه برنامه را دوباره دریافت و نصب کنید.")
        if argv and argv[0] == "selfcheck":
            from . import __version__

            print(json.dumps({"version": __version__, "ok": False, "missing_modules": [missing],
                              "missing_tables": [], "error": "catalogue.incomplete"}, ensure_ascii=False))
        print(f"error [catalogue.incomplete]: {msg}", file=sys.stderr)
        if getattr(sys, "frozen", False) and not argv and sys.stdin and sys.stdin.isatty():
            try:
                input("برای بستن Enter بزنید…")
            except EOFError:
                pass
        return 3
    return None


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    _utf8_console()
    failed = _registry_load_error(argv)
    if failed is not None:
        return failed
    if argv and argv[0] in {"convert", "verify", "compare", "synth", "selfcheck"}:
        from .cli import main as cli_main

        return cli_main(argv)
    parser = argparse.ArgumentParser(prog="mirza2nexa")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    serve(args.port, open_browser=not args.no_browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

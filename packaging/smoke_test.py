"""Smoke test for a built executable: run ``selfcheck`` (every converter module/table spec bundled),
then start the server, check /api/health and the CSP-protected index, stop it.

Usage: python packaging/smoke_test.py <path-to-executable>
"""

import json
import subprocess
import sys
import time
import urllib.request

PORT = 18765


def selfcheck(exe: str) -> bool:
    p = subprocess.run([exe, "selfcheck"], capture_output=True, timeout=120)  # noqa: S603 - executable under test
    out = p.stdout.decode("utf-8", "replace")
    try:
        status = json.loads(out.strip())
    except json.JSONDecodeError:
        status = None
    if p.returncode != 0 or not isinstance(status, dict) or status.get("ok") is not True \
            or status.get("missing_modules") or status.get("missing_tables"):
        print(f"selfcheck failed (exit {p.returncode}):\n{out}\n{p.stderr.decode('utf-8', 'replace')}", file=sys.stderr)
        return False
    print(f"selfcheck OK: version {status.get('version')}, {status.get('specs')} specs, "
          f"{len(status.get('modules') or [])} modules")
    return True


def main() -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows CI pipes default to cp1252
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
    exe = sys.argv[1]
    if not selfcheck(exe):
        return 1
    proc = subprocess.Popen([exe, "--port", str(PORT), "--no-browser"], stdout=subprocess.PIPE,  # noqa: S603 - path of the executable under test
                            stderr=subprocess.STDOUT)
    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/health", timeout=2) as r:  # noqa: S310
                    body = json.loads(r.read())
                    assert body["status"] == "ok", body
                    break
            except OSError:
                time.sleep(1)
        else:
            proc.terminate()
            out = proc.communicate(timeout=10)[0] or b""
            print("server did not start; output:\n" + out.decode("utf-8", "replace"), file=sys.stderr)
            return 1
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=5) as r:  # noqa: S310
            html = r.read().decode("utf-8")
            assert 'dir="rtl"' in html, "index.html missing"
            assert "default-src 'none'" in r.headers["Content-Security-Policy"]
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/jobs")
        try:
            urllib.request.urlopen(req, timeout=5)  # noqa: S310
            print("unauthenticated API call was accepted", file=sys.stderr)
            return 1
        except urllib.error.HTTPError as e:
            assert e.code == 401, e.code
        print("smoke test OK")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())

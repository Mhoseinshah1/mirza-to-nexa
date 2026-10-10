"""Smoke test for a built executable: start it, check /api/health and the CSP-protected index, stop it.

Usage: python packaging/smoke_test.py <path-to-executable>
"""

import json
import subprocess
import sys
import time
import urllib.request

PORT = 18765


def main() -> int:
    exe = sys.argv[1]
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
            print("server did not start", file=sys.stderr)
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

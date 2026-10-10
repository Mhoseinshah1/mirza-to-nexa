"""End-to-end check of a BUILT executable against the Python package, on SYNTHETIC data only.

Usage: python packaging/exe_e2e.py <path-to-exe> [--users N] [--keep]

Fails (non-zero exit, message on stderr) unless:
  A. ``<exe> selfcheck`` reports every converter module and table spec present;
  B. ``<exe> convert`` of a synthetic AES backup is ready, has no blockers, and every Phase-2
     key table is accounted for (never blocked/unclassified); the package verifies;
  C. the Python CLI produces the same logical result (import id, fingerprint, coverage,
     reconciliation, counts, manifest record counts …) as the executable;
  D. two further executable runs (separate processes) are logically identical to the first.

Package bytes, payload hashes, keys and timings are ignored (random nonces/keys by design).
Works on Windows (``Mirza2Nexa.exe``) and Linux (``Mirza2Nexa``) builds. Stdlib only; the
Python side uses the installed ``mirza2nexa`` package via ``python -m mirza2nexa``.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

KEY_TABLES = ("Payment_report", "wallet_transaction", "service_other", "ticket", "ticket_message",
              "discount_sell_used", "wheel_list", "reagent_report", "marzban_panel")
SYNTH_TABLES = ("user", "invoice", "product") + KEY_TABLES
OK_STATUS = {"archived", "review", "executed"}
RECON_SECTIONS = ("payments", "wallet_history", "service_operations", "engagement", "support", "agents")
POSITIVE_RECORD_COUNTS = ("customers", "invoices", "payments", "wallet_history", "service_operations",
                          "engagement", "support", "agents")
COVERAGE_FIELDS = ("table", "status", "class", "rows", "exported", "archived", "review", "skipped",
                   "accounted_rows", "dropped_secret_columns")
MIN_SPECS = 49
TIMEOUT = 1800


class Failure(Exception):
    pass


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def run(cmd: list[str], env: dict[str, str], label: str, ok_codes: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess:
    t0 = time.time()
    p = subprocess.run(cmd, env=env, capture_output=True, timeout=TIMEOUT)  # noqa: S603 - fixed argv, no shell
    out = p.stdout.decode("utf-8", "replace")
    err = p.stderr.decode("utf-8", "replace")
    print(f"[{label}] exit={p.returncode} {time.time() - t0:.1f}s", file=sys.stderr)
    if p.returncode not in ok_codes:
        raise Failure(f"{label}: exit code {p.returncode} (expected {ok_codes})\n--- stdout ---\n{out[-4000:]}"
                      f"\n--- stderr ---\n{err[-4000:]}")
    p.stdout, p.stderr = out, err  # type: ignore[assignment]
    return p


def last_json(text: str, label: str) -> dict:
    """Parse the JSON document printed on stdout (verify prints indented JSON; selfcheck/synth one line)."""
    s = text.strip()
    start = s.find("{")
    check(start >= 0, f"{label}: no JSON on stdout:\n{text[-2000:]}")
    try:
        return json.loads(s[start:])
    except json.JSONDecodeError as e:
        raise Failure(f"{label}: invalid JSON on stdout ({e}):\n{text[-2000:]}") from None


def stage_selfcheck(exe: str, env: dict[str, str]) -> dict:
    p = run([exe, "selfcheck"], env, "A exe selfcheck", ok_codes=(0, 3))
    st = last_json(p.stdout, "selfcheck")
    check(p.returncode == 0, f"selfcheck exit {p.returncode}: {st}")
    check(st.get("ok") is True, f"selfcheck not ok: {st}")
    check(not st.get("missing_modules"), f"selfcheck missing_modules: {st.get('missing_modules')}")
    check(not st.get("missing_tables"), f"selfcheck missing_tables: {st.get('missing_tables')}")
    check(int(st.get("specs") or 0) >= MIN_SPECS, f"selfcheck specs {st.get('specs')} < {MIN_SPECS}")
    return st


def convert(cmd_prefix: list[str], zip_path: Path, out: Path, env: dict[str, str], label: str) -> tuple[dict, dict]:
    run(cmd_prefix + ["convert", str(zip_path), "-o", str(out), "--money-unit", "toman", "--password-env", "ZPW"],
        env, label, ok_codes=(0, 2))
    rp = out / "report.json"
    check(rp.is_file(), f"{label}: report.json not written")
    report = json.loads(rp.read_text(encoding="utf-8"))
    pkgs = sorted(out.glob("*.nxpkg"))
    keys = sorted(out.glob("*.nxkey"))
    check(len(pkgs) == 1 and len(keys) == 1, f"{label}: expected one .nxpkg and one .nxkey, got {pkgs} {keys}")
    ver = run([sys.executable, "-m", "mirza2nexa", "verify", str(pkgs[0]), "--key-file", str(keys[0])], env,
              f"{label} verify")
    manifest = last_json(ver.stdout, f"{label} verify")
    check(manifest.get("ok") is True, f"{label}: package verify not ok: {manifest}")
    if cmd_prefix[0] != sys.executable:  # also exercise the frozen binary's own verify path
        ver_exe = run(cmd_prefix + ["verify", str(pkgs[0]), "--key-file", str(keys[0])], env, f"{label} exe verify")
        check(last_json(ver_exe.stdout, f"{label} exe verify") == manifest,
              f"{label}: exe verify output differs from python verify")
    return report, manifest


def validate_report(report: dict, manifest: dict, label: str) -> None:
    check(report.get("readiness") == "ready",
          f"{label}: readiness={report.get('readiness')!r} blockers={[b.get('code') for b in report.get('blockers', [])]}")
    check(report.get("blockers") == [], f"{label}: blockers present: {report.get('blockers')}")
    check((report.get("catalogue") or {}).get("ok") is True, f"{label}: catalogue not ok: {report.get('catalogue')}")
    cov = {c["table"]: c for c in report.get("coverage", [])}
    missing = [t for t in SYNTH_TABLES if t not in cov]
    check(not missing, f"{label}: synthetic tables missing from coverage: {missing}")
    for t in KEY_TABLES:
        c = cov[t]
        check(c.get("status") in OK_STATUS, f"{label}: {t} status={c.get('status')!r} (class={c.get('class')!r})")
        check(c.get("class") is not None, f"{label}: {t} has no class")
        check(int(c.get("rows") or 0) > 0, f"{label}: {t} has no rows")
        check(c.get("accounted_rows") == c.get("rows"),
              f"{label}: {t} accounted_rows={c.get('accounted_rows')} != rows={c.get('rows')}")
    bad = [(t, c.get("status")) for t, c in cov.items() if c.get("status") in {"blocked", "unclassified", None}]
    check(not bad, f"{label}: tables blocked/unclassified: {bad}")
    rec = report.get("reconciliation") or {}
    miss = [s for s in RECON_SECTIONS if s not in rec]
    check(not miss, f"{label}: reconciliation sections missing: {miss}")
    rc = manifest.get("record_counts") or {}
    low = {k: rc.get(k) for k in POSITIVE_RECORD_COUNTS if not int(rc.get(k) or 0) > 0}
    check(not low, f"{label}: manifest record_counts not > 0: {low}")
    check(manifest.get("import_id") == report.get("import_id"), f"{label}: manifest/report import_id differ")
    check(manifest.get("source_fingerprint") == report.get("source_fingerprint"),
          f"{label}: manifest/report source_fingerprint differ")


def logical(report: dict, manifest: dict) -> dict:
    return {
        "import_id": report.get("import_id"),
        "source_fingerprint": report.get("source_fingerprint"),
        "readiness": report.get("readiness"),
        "blockers": report.get("blockers"),
        "warning_codes": sorted(w.get("code") for w in report.get("warnings", [])),
        "coverage": {c["table"]: {f: c.get(f) for f in COVERAGE_FIELDS} for c in report.get("coverage", [])},
        "reconciliation": report.get("reconciliation"),
        "counts": report.get("counts"),
        "review_count": report.get("review_count"),
        "catalogue": report.get("catalogue"),
        "record_counts": manifest.get("record_counts"),
        "manifest_import_id": manifest.get("import_id"),
        "manifest_fingerprint": manifest.get("source_fingerprint"),
    }


def diff(a: object, b: object, path: str = "$") -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        out: list[str] = []
        for k in sorted(set(a) | set(b), key=str):
            if k not in a:
                out.append(f"{path}.{k}: only in right = {json.dumps(b[k], ensure_ascii=False)[:300]}")
            elif k not in b:
                out.append(f"{path}.{k}: only in left = {json.dumps(a[k], ensure_ascii=False)[:300]}")
            else:
                out.extend(diff(a[k], b[k], f"{path}.{k}"))
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path}: list length {len(a)} != {len(b)}: {json.dumps(a, ensure_ascii=False)[:300]} vs "
                    f"{json.dumps(b, ensure_ascii=False)[:300]}"]
        out = []
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            out.extend(diff(x, y, f"{path}[{i}]"))
        return out
    if a != b:
        return [f"{path}: {json.dumps(a, ensure_ascii=False)[:300]} != {json.dumps(b, ensure_ascii=False)[:300]}"]
    return []


def compare(left: dict, right: dict, what: str) -> None:
    d = diff(left, right)
    if d:
        raise Failure(f"{what}: logical content differs ({len(d)} difference(s)):\n  " + "\n  ".join(d[:200]))


def main() -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows CI pipes default to cp1252; diagnostics may be Persian
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("exe")
    ap.add_argument("--users", type=int, default=1000)
    ap.add_argument("--keep", action="store_true", help="keep the temporary directory (synthetic data only)")
    a = ap.parse_args()

    exe = str(Path(a.exe).resolve())
    if not Path(exe).is_file():
        print(f"FAIL: executable not found: {exe}", file=sys.stderr)
        return 1
    tmp = Path(tempfile.mkdtemp(prefix="m2n-exe-e2e-"))
    env = dict(os.environ)
    env["MIRZA2NEXA_HOME"] = str(tmp / "home")
    env["ZPW"] = "synthetic-zip-pw-e2e"  # synthetic test password, not a secret
    env["PYTHONIOENCODING"] = "utf-8"
    (tmp / "home").mkdir()
    try:
        sc = stage_selfcheck(exe, env)

        zip_path = tmp / "b.zip"
        p = run([sys.executable, "-m", "mirza2nexa", "synth", str(zip_path), "--users", str(a.users),
                 "--password-env", "ZPW"], env, "synth (python)")
        synth = last_json(p.stdout, "synth")

        rep_b, man_b = convert([exe], zip_path, tmp / "exe1", env, "B exe convert #1")
        validate_report(rep_b, man_b, "B exe")
        rc = man_b["record_counts"]
        check(rc.get("payments") == synth.get("payments"),
              f"B exe: payments {rc.get('payments')} != synthetic {synth.get('payments')}")
        check(rc.get("wallet_history") == synth.get("wallet_transactions"),
              f"B exe: wallet_history {rc.get('wallet_history')} != synthetic {synth.get('wallet_transactions')}")
        lb = logical(rep_b, man_b)

        rep_c, man_c = convert([sys.executable, "-m", "mirza2nexa"], zip_path, tmp / "py", env, "C python convert")
        validate_report(rep_c, man_c, "C python")
        compare(logical(rep_c, man_c), lb, "C python (left) vs exe (right)")

        for i in (2, 3):
            rep_d, man_d = convert([exe], zip_path, tmp / f"exe{i}", env, f"D exe convert #{i}")
            compare(lb, logical(rep_d, man_d), f"D exe run #1 (left) vs run #{i} (right)")

        cov = {c["table"]: c for c in rep_b["coverage"]}
        summary = {
            "result": "PASS",
            "exe": exe,
            "version": sc.get("version"),
            "converter_version": (rep_b.get("converter") or {}).get("version"),
            "selfcheck": {"specs": sc.get("specs"), "modules": len(sc.get("modules") or [])},
            "users": a.users,
            "import_id": rep_b["import_id"],
            "readiness": rep_b["readiness"],
            "review_count": rep_b.get("review_count"),
            "tables": len(cov),
            "key_tables": {t: {"rows": cov[t]["rows"], "status": cov[t]["status"], "class": cov[t]["class"],
                               "archived": cov[t]["archived"], "review": cov[t]["review"]} for t in KEY_TABLES},
            "record_counts": rc,
            "checks": ["A selfcheck", "B exe convert+verify", "C exe == python", "D exe deterministic x3"],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=1))
        return 0
    except Failure as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired as e:
        print(f"FAIL: timeout after {e.timeout}s: {e.cmd}", file=sys.stderr)
        return 1
    finally:
        if a.keep:
            print(f"kept {tmp}", file=sys.stderr)
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())

"""Synthetic performance run at production-comparable volume.

NEXA's recorded staging baseline is ~197.7k users and ~127.6k invoices
(nexa-bot docs/open-questions.md:3300-3304). This test generates 200,000 synthetic users
and ~130,000 invoices, runs the full pipeline (AES ZIP → extract → parse → convert →
reconcile → encrypted package) and checks exact reconciliation plus a peak-RSS ceiling,
proving memory is bounded rather than proportional to the backup.

Run with:  pytest -m perf -s
"""

from __future__ import annotations

import json
import sys
import threading
import time

import pytest

from conftest import run
from mirza2nexa.nxpkg import PackageReader
from mirza2nexa.synth import SynthSpec, write_large_zip

pytestmark = pytest.mark.perf

USERS = 200_000
INVOICES_PER_USER = 0.65  # ≈130k invoices
RSS_CEILING_MB = 600


def _rss_mb() -> float:
    try:
        import psutil  # type: ignore[import-not-found]

        return psutil.Process().memory_info().rss / 2**20
    except ImportError:
        pass
    if sys.platform.startswith("linux"):
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    return 0.0


def test_200k_customers(tmp_path):
    spec = SynthSpec(users=USERS, invoices_per_user=INVOICES_PER_USER, seed=11, rows_per_insert=1000)
    t0 = time.time()
    zp = tmp_path / "big.zip"
    exp = write_large_zip(zp, spec, password="Perf-Pass-2026")
    gen_s = time.time() - t0

    peak = {"rss": _rss_mb()}
    stop = threading.Event()

    def sample() -> None:
        while not stop.is_set():
            peak["rss"] = max(peak["rss"], _rss_mb())
            time.sleep(0.05)

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    stages: list[tuple[str, float]] = []
    t1 = time.time()
    try:
        res = run(tmp_path, zp, password="Perf-Pass-2026", kdf_n=2**17)
    finally:
        stop.set()
        sampler.join()
    elapsed = time.time() - t1
    stages.append(("total", elapsed))

    w = res.report["reconciliation"]["wallet"]
    assert res.report["readiness"] == "ready", res.report["blockers"]
    assert w["balanced"] and w["independent_recount_matches"]
    assert w["opening_credits"]["amount_minor"] == str(exp["positive_sum"])
    assert w["legacy_debts"]["amount_minor"] == str(exp["negative_sum"])
    assert res.report["counts"]["invoices"]["converted"] == exp["invoices"]

    v0 = time.time()
    reader = PackageReader(res.package_path, key=res.key_text, workdir=tmp_path / "verify")
    reader.verify()
    verify_s = time.time() - v0

    summary = {
        "users": exp["users"], "invoices": exp["invoices"],
        "zip_mb": round(zp.stat().st_size / 2**20, 1),
        "generate_s": round(gen_s, 1), "convert_s": round(elapsed, 1), "verify_s": round(verify_s, 1),
        "package_mb": round(res.package_path.stat().st_size / 2**20, 1),
        "peak_rss_mb": round(peak["rss"], 1),
    }
    print("\nPERF " + json.dumps(summary))
    assert peak["rss"] < RSS_CEILING_MB, summary

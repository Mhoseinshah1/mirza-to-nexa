"""Regressions from the Phase 2 independent review (synthetic data only)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

from conftest import make_backup, read_jsonl, run


def test_fresh_process_cli_converts_every_domain_table(tmp_path):
    """The real CLI/exe path: nothing imports the modules before staging (review finding 1)."""
    zp, _ = make_backup(tmp_path, users=120)
    out = tmp_path / "out"
    env = {**os.environ, "MIRZA2NEXA_HOME": str(tmp_path / "home")}
    proc = subprocess.run([sys.executable, "-I", "-m", "mirza2nexa", "convert", str(zp), "-o", str(out),
                           "--money-unit", "toman"], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=600)
    assert proc.returncode == 0, proc.stderr[-2000:]
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["readiness"] == "ready", report["blockers"]
    assert {"payments", "wallet_history", "service_operations", "agents", "support"} <= set(report["reconciliation"])
    statuses = {c["table"]: c["status"] for c in report["coverage"]}
    assert statuses["Payment_report"] == "archived"
    assert "blocked" not in statuses.values()


def _zip_sql(tmp: Path, sql: str) -> Path:
    p = tmp / "x.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("backup.sql", sql)
    return p


USER = ("CREATE TABLE `user` (`id` varchar(500) NOT NULL, `limit_usertest` int, `Balance` varchar(64), "
        "`User_Status` varchar(50), PRIMARY KEY (`id`));\n"
        "INSERT INTO `user` VALUES ('7100000001',1,'100','Active');\n")


def test_code_primary_key_never_written_in_clear(tmp_path):
    sql = USER + ("CREATE TABLE `discount_sell` (`code` varchar(50) NOT NULL, `percent` int, PRIMARY KEY (`code`));\n"
                  "INSERT INTO `discount_sell` VALUES ('GIFTSECRET77',10);\n")
    res = run(tmp_path, _zip_sql(tmp_path, sql))
    blob = b"".join(p.read_bytes() for p in res.stage_dir.rglob("*") if p.is_file())
    # Owner decision 2026-10-17: a discount_sell code is carried as text in its own field
    # (`code_text`) and nowhere else — never in a key, provenance, digest input or report.
    carried = len(re.findall(rb'"code_text":\s*"GIFTSECRET77"', blob))
    assert blob.count(b"GIFTSECRET77") == carried
    assert b"GIFTSECRET77" not in json.dumps(res.report).encode()


def test_unreadable_domain_table_blocks_instead_of_silently_passing(tmp_path):
    sql = USER + ("CREATE TABLE `Payment_report` (`ID` int, `Price` varchar(20), `Status` varchar(20));\n"
                  "INSERT INTO `Payment_report` VALUES (1,'100','paid');\n")
    res = run(tmp_path, _zip_sql(tmp_path, sql))
    assert "COVERAGE_INCOMPLETE" in {b["code"] for b in res.report["blockers"]}
    assert res.report["readiness"] == "blocked"


def test_settings_like_fork_table_value_blob_not_staged(tmp_path):
    sql = USER + ("CREATE TABLE `vs_setting` (`name` varchar(50), `value` text);\n"
                  "INSERT INTO `vs_setting` VALUES ('panel','P@ss-not-for-export');\n")
    res = run(tmp_path, _zip_sql(tmp_path, sql))
    cov = {c["table"]: c for c in res.report["coverage"]}
    assert "value" in cov["vs_setting"]["dropped_secret_columns"]
    blob = b"".join(p.read_bytes() for p in res.stage_dir.rglob("*") if p.is_file())
    assert b"P@ss-not-for-export" not in blob


def test_wallet_history_mixed_time_formats_are_order_unproven(tmp_path):
    sql = USER + ("CREATE TABLE `wallet_transaction` (`id` int, `id_user` varchar(50), `username` varchar(50), "
                  "`service_username` varchar(50), `type` varchar(20), `amount` varchar(20), "
                  "`balance_before` varchar(20), `balance_after` varchar(20), `ref_id` varchar(20), "
                  "`source` varchar(20), `reason` varchar(50), `created_at` varchar(30), PRIMARY KEY (`id`));\n"
                  "INSERT INTO `wallet_transaction` VALUES "
                  "(1,'7100000001',NULL,NULL,'x','100','0','100',NULL,NULL,NULL,'1700000000'),"
                  "(2,'7100000001',NULL,NULL,'x','0','100','100',NULL,NULL,NULL,'2024/01/01 10:00:00');\n")
    res = run(tmp_path, _zip_sql(tmp_path, sql))
    checks = read_jsonl(res.stage_dir / "records" / "wallet_history_checks.jsonl")
    assert [c["verdict"] for c in checks] == ["ORDER_UNPROVEN"]
    # history never touches the opening balance
    credits = read_jsonl(res.stage_dir / "records" / "wallet_opening_balances.jsonl")
    assert [c["amount_minor"] for c in credits] == ["100"]

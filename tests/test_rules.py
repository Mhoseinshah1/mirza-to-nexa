"""Field rules ported from NEXA — including byte-parity with JavaScript where NEXA hashes."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from mirza2nexa.mapping import rules


@pytest.mark.parametrize("raw,expected", [
    ("50000", 50000), ("-20000", -20000), (" 7 ", 7), ("0", 0),
    ("12.5", None), ("1e3", None), ("", None), ("150,000", None), ("۵۰۰", None), (None, None),
    ("+5", None), ("1" * 20, None),
])
def test_parse_balance(raw, expected):
    assert rules.parse_balance(raw) == expected


@pytest.mark.parametrize("raw,ok", [
    ("123456789", True), ("0123", False), ("1" * 19, True), ("1" * 20, False), (" 123", False),
    ("12a", False), ("", False), (None, False),
])
def test_telegram_id(raw, ok):
    assert (rules.telegram_id(raw) is not None) is ok


@pytest.mark.parametrize("key,ok", [
    ("abcd", True), ("1234abcd", True), ("1234567abcd", True), ("123456789abcdef0"[:15], True),
    ("ABCD", False), ("0234567abcd", False), ("abc", False), ("12345678abcd", False),
])
def test_invoice_key(key, ok):
    assert rules.invoice_key_valid(key) is ok


def test_user_status_exact_spellings():
    assert rules.user_status("Active") == "ACTIVE"
    assert rules.user_status("block") == "BLOCKED"
    for v in ("active", "Block", " Active", None, "frozen"):
        assert rules.user_status(v) == "UNKNOWN"


def test_shape_rules():
    assert rules.legacy_shape("p1", "10", "30", "", "0")[1] == "OK"
    assert rules.legacy_shape("p1", "0", "30", "", "0")[1] == "VOLUME_ZERO"
    assert rules.legacy_shape("p1", "1,5", "30", "", "0")[1] == "VOLUME_INVALID"
    assert rules.legacy_shape("p1", "10", "0", "", "0")[1] == "DURATION_ZERO"
    assert rules.legacy_shape("p1", "10", "3651", "", "0")[1] == "DURATION_INVALID"
    assert rules.legacy_shape("p1", "10", "1", "month", "0")[1] == "TIME_UNIT_UNKNOWN"
    assert rules.legacy_shape("p1", "10", "30", "DAYS", "0")[1] == "OK"
    assert rules.legacy_shape("p1", "10", "30", "", " 0")[1] == "IS_CUSTOM_INVALID"
    assert rules.legacy_shape("p\x07", "10", "30", "", "0")[1] == "CODE_PANEL_INVALID"
    key, _, shape = rules.legacy_shape("  ", "0.01", "30", "d", "1")
    assert shape["legacy_code_panel"] is None
    assert shape["traffic_bytes"] == str((1 * 1024**3 + 50) // 100)
    assert key == 'legacy-shape:v1:[null,"10737418",30,1]'


def test_trial_decision():
    assert rules.trial_decision("x", False) == "LEGACY_LIMIT_UNREADABLE"
    assert rules.trial_decision("0", False) == "LEGACY_NO_TRIALS"
    assert rules.trial_decision("1", True) == "LEGACY_TRIAL_CONSUMED"
    assert rules.trial_decision("1", False) == "INHERIT_NEXA_POLICY"
    assert rules.trial_decision("-2147483648", False) == "LEGACY_LIMIT_UNREADABLE"


def test_js_trim_matches_ecmascript():
    assert rules.js_trim("﻿　 x \n") == "x"
    assert rules.js_trim("\x1cx\x85") == "\x1cx\x85"  # Python strip() would remove these


def test_price_and_time_sell():
    assert rules.parse_price("150000") == (150000, "OK")
    assert rules.parse_price("150,000") == (None, "NOT_A_NUMBER")
    assert rules.parse_price("") == (None, "EMPTY")
    assert rules.parse_time_sell("1700000000") == (1700000000, "OK")
    assert rules.parse_time_sell("2023-01-01")[1] == "FORMAT_UNKNOWN"
    assert rules.parse_time_sell("100")[1] == "OUT_OF_RANGE"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_row_checksum_parity_with_nexa_javascript():
    """``legacyRowChecksum`` in NEXA is sha256(`${kind}:${JSON.stringify(facts)}`) — prove byte parity."""
    cases = [
        ["7000000001", "50000", "1", "0", "synthetic_user"],
        ["7000000002", None, "0", "1", None],
        ["abcd", "x", 'quo"te', "back\\slash", "ناشناس", "emoji 😀", "tab\tnl\n", "\x01ctl", " "],
    ]
    script = (
        "const c=require('crypto');const cases=JSON.parse(process.argv[1]);"
        "console.log(JSON.stringify(cases.map(f=>c.createHash('sha256').update('user:v1:'+JSON.stringify(f)).digest('hex'))))"
    )
    out = subprocess.run(["node", "-e", script, json.dumps(cases)], capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    js = json.loads(out.stdout)
    assert js == [rules.row_checksum("user:v1", f) for f in cases]

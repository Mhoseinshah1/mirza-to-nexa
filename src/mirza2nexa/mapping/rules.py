"""Field-level rules, ported 1:1 from the NEXA legacy importer.

Every rule here mirrors a rule that already exists in NEXA (``Mhoseinshah1/nexa-bot`` @
``9e604def``) so that the package and NEXA's own importer reach the *same* decision for
the same source cell. Citations are ``path:line`` in that repository. Nothing in this
module guesses a column, unit or relationship that NEXA does not already decide; where
NEXA records an open question, the rule returns a refusal instead of a value.

All cells are *text or None*, exactly as NEXA reads them (``CAST(col AS CHAR)``,
``source-port.ts:11-14``). :func:`cell_text` performs that normalisation for values coming
out of the dump parser.
"""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal

# JavaScript's String.prototype.trim() whitespace set (ECMA-262 WhiteSpace + LineTerminator).
# NEXA trims with JS semantics; Python's str.strip() differs (e.g. it strips U+001C..U+001F
# and U+0085 but JS does not), so every trim here goes through js_trim.
_JS_WS = "\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"


def js_trim(text: str) -> str:
    return text.strip(_JS_WS)


# ---- source contract (apps/api/.../legacy-importer/application/source-port.ts:36-83) ------

SOURCE_TABLES = ("user", "invoice", "product")
PRIMARY_KEYS = {"user": "id", "invoice": "id_invoice", "product": "id"}
REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    # User_Status is REQUIRED by NEXA's separate user-status read set (read-set.ts:383-399):
    # a source without it is refused, never read as "everybody is active".
    "user": ("id", "Balance", "limit_usertest", "User_Status"),
    "invoice": ("id_invoice", "id_user", "username", "Status", "is_test", "code_panel", "code_product",
                "Volume", "Service_time", "time_unit", "is_custom", "price_product"),
    "product": ("id", "code_product"),
}
# Optional columns read when present. user.User_Status is NEXA's separate `user-status`
# read set (read-set.ts:395-399); invoice archive optional columns from
# invoice-archive-read-set.ts:23-32; product review optional columns from products-read-set.ts:20-35
# (+ status_product: mirza_pro db/tables/product.php:32, sale status, read by modules/adoption.py).
OPTIONAL_COLUMNS: dict[str, tuple[str, ...]] = {
    # maxbuyagent/expire/pricediscount: agent overdraft cap, role expiry, per-user discount %
    # (mirza_pro db/tables/user.php:30,32,39) — read by modules/agents.py, never applied.
    "user": ("agent", "number", "username", "maxbuyagent", "expire", "pricediscount"),
    "invoice": ("Service_location", "time_sell", "name_product"),
    "product": ("agent", "status_product", "name_product", "price_product", "Volume_constraint", "Location", "Service_time",
                "Category", "category", "note", "data_limit_reset", "one_buy_status", "hide_panel"),
}
# Columns that may hold secrets or credentials and are NEVER read
# (invoice-archive-read-set.ts:34-43,56-60; products-read-set.ts:20-35).
NEVER_READ: dict[str, frozenset[str]] = {
    "invoice": frozenset({"user_info", "uuid", "bottype"}),
    "product": frozenset({"inbounds", "proxies"}),
    "user": frozenset(),
}
# Free-text / PII columns NEXA's archive view may read but this converter does not export
# (customer-typed config names, referrer ids): legacy-invoice-archive.ts:136.
NOT_EXPORTED: dict[str, frozenset[str]] = {
    "invoice": frozenset({"note", "refral"}),
    "user": frozenset({"number", "affiliates"}),
    "product": frozenset(),
}

# source-port.ts:112-119 — exact, case-sensitive comparison.
LIVE_STATUSES = frozenset({"active", "disabled", "disabledn", "disablebyadmin", "end_of_volume"})

# ---- identity --------------------------------------------------------------------------

# packages/contracts/src/customer.ts:60-62 AND legacy-import.ts:257-259 (both must hold).
_TELEGRAM_ID = re.compile(r"[1-9][0-9]{0,18}")
_LEGACY_USER_KEY = re.compile(r"[1-9][0-9]{0,19}")
# legacy-import.ts:257-260
INVOICE_KEY = re.compile(r"([1-9][0-9]{6})?([0-9a-f]{4}|[0-9a-f]{8})")


def telegram_id(raw: str | None) -> str | None:
    """``legacyTelegramId`` (decisions.ts:104-109): no trimming, no leading zero."""
    if raw is None:
        return None
    return raw if _TELEGRAM_ID.fullmatch(raw) and _LEGACY_USER_KEY.fullmatch(raw) else None


def invoice_key_valid(raw: str | None) -> bool:
    return raw is not None and INVOICE_KEY.fullmatch(raw) is not None


# source-snapshot.ts:119-124 — exact spellings only.
_USER_STATUS = {"Active": "ACTIVE", "block": "BLOCKED"}


def user_status(raw: str | None) -> str:
    return _USER_STATUS.get(raw, "UNKNOWN") if raw is not None else "UNKNOWN"


_USERNAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{4,31}")


def profile_username(raw: str | None) -> str | None:
    """``legacyProfileUsername`` (decisions.ts:154-159)."""
    if raw is None:
        return None
    text = js_trim(raw)
    if text.startswith("@"):
        text = text[1:]
    return text if _USERNAME.fullmatch(text) else None


def is_agent(raw: str | None) -> bool:
    """``legacyIsAgent`` (decisions.ts:161-171). Reported only; never grants a reseller role."""
    if raw is None:
        return False
    return js_trim(raw).lower() not in {"", "f", "0", "false"}


_PHONE_E164 = re.compile(r"\+?[1-9][0-9]{7,14}")
_PHONE_IR = re.compile(r"09[0-9]{9}")


def phone_class(raw: str | None) -> str:
    """``classifyLegacyPhone`` (decisions.ts:43-53). Only the class is used; the number is never exported."""
    if raw is None:
        return "ABSENT"
    t = js_trim(raw)
    if t.lower() in {"", "none", "0"}:
        return "ABSENT"
    return "VALID" if _PHONE_E164.fullmatch(t) or _PHONE_IR.fullmatch(t) else "INVALID"


# ---- money -----------------------------------------------------------------------------

# packages/contracts/src/payment.ts:239
PAYMENT_AMOUNT_MAX_MINOR = 1_000_000_000_000
# packages/contracts/src/money.ts:38
MAX_MONEY_AMOUNT_MINOR = 2**63 - 1

_BALANCE = re.compile(r"-?[0-9]{1,19}")
_PRICE = re.compile(r"[0-9]{1,19}")


def parse_balance(raw: str | None) -> int | None:
    """``parseLegacyBalance`` (decisions.ts:96-102): a signed whole number, else None.

    ``10.5``, ``1e3``, ``''``, ``150,000`` and Persian digits are all *unreadable* — never rounded.
    """
    if raw is None:
        return None
    text = js_trim(raw)
    return int(text) if _BALANCE.fullmatch(text) else None


def parse_price(raw: str | None) -> tuple[int | None, str]:
    """Historical price (legacy-product-facts.ts:138-153). Returns (minor, parse_note)."""
    if raw is None:
        return None, "ABSENT"
    text = js_trim(raw)
    if text == "":
        return None, "EMPTY"
    if not _PRICE.fullmatch(text):
        return None, "NOT_A_NUMBER"
    value = int(text)
    if value > MAX_MONEY_AMOUNT_MINOR:
        return None, "OUT_OF_RANGE"
    return value, "OK"


def parse_limit_usertest(raw: str | None) -> int | None:
    """``normaliseLegacyLimit`` (commerce/trials/application/legacy-trial-eligibility.ts)."""
    if raw is None:
        return None
    t = js_trim(raw)
    if not re.fullmatch(r"-?[0-9]{1,10}", t):
        return None
    v = int(t)
    return None if abs(v) > 2**31 - 1 else v  # INT4_MAX, legacy-trial-eligibility.ts


# ---- invoices / services ---------------------------------------------------------------

def flag01(raw: str | None) -> bool | None:
    """``is_test``/``is_custom`` for the archive: trimmed ``1``/``0`` only (invoice-archive-row.ts:172-176)."""
    if raw is None:
        return None
    t = js_trim(raw)
    return True if t == "1" else False if t == "0" else None


def blank_to_none(raw: str | None) -> str | None:
    """``code_panel``/``code_product``: trimmed, blank → None (decisions.ts:262-266)."""
    if raw is None:
        return None
    t = js_trim(raw)
    return t or None


# invoice-archive-row.ts:144-170
TIME_SELL_MIN = 1420070400
TIME_SELL_MAX = 4102444800


def parse_time_sell(raw: str | None) -> tuple[int | None, str]:
    """``parseLegacyTimeSell`` (invoice-archive-row.ts:160-170). The column is always present
    (it is in the read set when the table has it), so NULL/blank is ``EMPTY``."""
    if raw is None or js_trim(raw) == "":
        return None, "EMPTY"
    t = js_trim(raw)
    if not re.fullmatch(r"[0-9]{1,15}", t):
        return None, "FORMAT_UNKNOWN"
    v = int(t)
    if not TIME_SELL_MIN <= v < TIME_SELL_MAX:
        return None, "OUT_OF_RANGE"
    return v, "OK"


def parse_invoice_price(raw: str | None) -> tuple[int | None, str]:
    """``parseLegacyInvoicePrice`` (invoice-archive-row.ts:137-142): NULL/blank is ``EMPTY``."""
    if raw is None:
        return None, "EMPTY"
    return parse_price(raw)


# packages/contracts/src/traffic-input.ts:36-60; catalog.ts:253-254
_TRAFFIC_GB = re.compile(r"(0|[1-9][0-9]{0,8})(\.[0-9]{1,2})?")
BYTES_PER_GB = 1024**3
MAX_TRAFFIC_BYTES = 1_099_511_627_776_000
MAX_DURATION_DAYS = 3650
DAY_UNITS = frozenset({"", "d", "day", "days"})


def parse_traffic_gb(text: str) -> int | None:
    m = _TRAFFIC_GB.fullmatch(js_trim(text))
    if m is None:
        return None
    whole = int(m.group(1))
    decimals = (m.group(2) or ".")[1:].ljust(2, "0")
    hundredths = whole * 100 + int(decimals)
    return (hundredths * BYTES_PER_GB + 50) // 100


def legacy_shape(code_panel: str | None, volume: str | None, service_time: str | None,
                 time_unit: str | None, is_custom: str | None) -> tuple[str | None, str, dict | None]:
    """``legacyShapeKey`` (apps/api/.../catalog/application/legacy-shape.ts:72-117).

    Returns (key, reason, shape). ``reason`` is ``OK`` or the NEXA refusal code.
    """
    cp = "" if code_panel is None else js_trim(code_panel)
    if len(cp) > 200 or any(ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F for c in cp):
        return None, "CODE_PANEL_INVALID", None
    vol = None if volume is None else js_trim(volume)
    traffic = None if vol is None else parse_traffic_gb(vol)
    if traffic is None or traffic > MAX_TRAFFIC_BYTES:
        return None, "VOLUME_INVALID", None
    if traffic == 0:
        return None, "VOLUME_ZERO", None
    unit = js_trim(time_unit or "").lower()
    if unit not in DAY_UNITS:
        return None, "TIME_UNIT_UNKNOWN", None
    st = None if service_time is None else js_trim(service_time)
    if st is None or not re.fullmatch(r"[0-9]{1,6}", st):
        return None, "DURATION_INVALID", None
    days = int(st)
    if days == 0:
        return None, "DURATION_ZERO", None
    if days > MAX_DURATION_DAYS:
        return None, "DURATION_INVALID", None
    # legacy-shape.ts:205-209 — exact '1'/'0' (no trimming).
    if is_custom == "1":
        custom = True
    elif is_custom == "0":
        custom = False
    else:
        return None, "IS_CUSTOM_INVALID", None
    shape = {"legacy_code_panel": cp or None, "traffic_bytes": str(traffic), "duration_days": days,
             "is_custom": custom}
    key = "legacy-shape:v1:" + js_json([cp or None, str(traffic), days, 1 if custom else 0])
    return key, "OK", shape


def panel_username(raw: str | None) -> str | None:
    """Account username used to match a panel account (legacy-service-matching.ts:105-110)."""
    if raw is None:
        return None
    if not 1 <= len(raw) <= 128 or any(not 0x21 <= ord(c) <= 0x7E for c in raw):
        return None
    return raw.lower()


# ---- checksums (apps/api/.../legacy-importer/application/source-snapshot.ts:141-143) -----

def js_json(value: object) -> str:
    """``JSON.stringify`` for arrays of str/int/None: compact, non-ASCII kept literally."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def row_checksum(kind: str, facts: list[str | None]) -> str:
    """``legacyRowChecksum``: ``sha256(kind + ':' + JSON.stringify(facts))``."""
    return hashlib.sha256(f"{kind}:{js_json(facts)}".encode()).hexdigest()


def cell_text(value: object) -> str | None:
    """Normalise a parsed dump literal to the text MySQL's ``CAST(... AS CHAR)`` would yield."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, bytes | bytearray):
        try:
            return bytes(value).decode("utf-8")
        except UnicodeDecodeError:
            return None
    return str(value)


def trial_decision(limit_raw: str | None, had_trial: bool) -> str:
    """``decideLegacyTrial`` without the NEXA-side existing override (KEPT_EXISTING_OVERRIDE)."""
    limit = parse_limit_usertest(limit_raw)
    if limit is None:
        return "LEGACY_LIMIT_UNREADABLE"
    if limit <= 0:
        return "LEGACY_NO_TRIALS"
    if had_trial:
        return "LEGACY_TRIAL_CONSUMED"
    return "INHERIT_NEXA_POLICY"

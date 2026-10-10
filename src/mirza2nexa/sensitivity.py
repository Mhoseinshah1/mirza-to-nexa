"""Value-level secret detection and redaction.

Column allow-lists (see :mod:`mirza2nexa.catalogue`) are the first line of defence: columns
known to hold credentials are never even staged. This module is the second line: every text
value that leaves the converter through an archive/configuration record or a report is
scanned for things that can authenticate to something, and replaced with a typed marker.

Detected kinds (deliberately conservative — false positives only cost readability):

* ``private_key`` PEM private keys
* ``proxy_link``  vless/vmess/trojan/ss/ssr/hysteria/tuic/wireguard links (embed credentials)
* ``url_creds``   URLs with ``user:password@``
* ``sub_url``     subscription URLs (``/sub/<token>``, ``?token=`` …)
* ``bot_token``   Telegram bot tokens ``123456789:AA…``
* ``jwt``         JSON Web Tokens
* ``bearer``      ``Bearer <token>`` / ``Basic <token>`` authorization values
* ``kv_secret``   the value after ``password=``, ``"token": "…"``, ``رمز: …`` and similar labels
* ``iban``        Iranian Sheba / IBAN numbers (``IR`` + 24 digits)
* ``uuid``        RFC-4122 UUIDs (panel account ids double as credentials)
* ``secret_like`` long opaque tokens (≥32 chars of hex/base64url without spaces)
* ``card``        payment card numbers (13–19 digits, ASCII or Persian/Arabic-Indic, passing
  Luhn) — masked to the last 4 digits

Policy and residual risks: ``docs/DATA_PROTECTION.md``.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable

# Labels whose *value* is a credential. Matched case-insensitively, as a whole word, optionally
# quoted (JSON) and followed by ':' or '='. The value runs to the next quote, space, comma, brace,
# '&' or '<'.
_KV_LABELS = (
    r"pass(?:word|wd|phrase)?|pwd|secret|client[_-]?secret|webhook[_-]?secret|token|access[_-]?token|"
    r"api[_-]?key|apikey|api[_-]?token|merchant(?:[_-]?id)?|private[_-]?key|auth(?:orization)?|cookie|"
    r"session(?:[_-]?id)?|otp|cvv2?|pin|رمز(?:\s*عبور)?|گذرواژه|کلمه\s*عبور|توکن"
)

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(-----END [A-Z ]*PRIVATE KEY-----|$)", re.S)),
    ("proxy_link", re.compile(r"\b(?:vless|vmess|trojan|ssr?|hysteria2?|hy2|tuic|wireguard|wg)://\S+", re.I)),
    ("url_creds", re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@\S+", re.I)),
    ("sub_url", re.compile(r"\bhttps?://\S*(?:/sub/|/subscription|[?&](?:token|key|secret|sub)=)\S*", re.I)),
    ("bot_token", re.compile(r"(?<![0-9A-Za-z])\d{6,12}:[A-Za-z0-9_-]{30,}(?![A-Za-z0-9_-])")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("bearer", re.compile(r"\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{16,}", re.I)),
    ("kv_secret", re.compile(
        r"(?P<label>[\"']?(?<![\w])(?:" + _KV_LABELS + r")[\"']?\s*[:=]\s*[\"']?)"
        r"(?P<value>(?!\[REDACTED)[^\s\"',}&<]{4,})", re.I)),
    ("iban", re.compile(r"(?<![A-Za-z0-9])IR[ -]?(?:\d[ -]?){23}\d(?!\d)", re.I)),
    ("uuid", re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")),
    ("secret_like", re.compile(r"(?<![A-Za-z0-9_+/=-])[A-Za-z0-9_+/=-]{32,}(?![A-Za-z0-9_+/=-])")),
]
# ``\d`` is Unicode-aware: Persian (۰-۹) and Arabic-Indic (٠-٩) digits match as well.
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")

# Column-name heuristics for tables without a column-level catalogue entry.
_SECRET_NAME = re.compile(
    r"(pass(word|wd)?|secret|token|api[_-]?key|apikey|private|merchant|marchent|cookie|session|"
    r"credential|auth|hash|salt|otp|pin\b|cvv|card|iban|sheba|shaba|access[_-]?key|"
    r"wallet_?address|endpoint|header|"
    r"user_info|subscription|sub_?link|config_?link|uuid|inbound|proxies|link_sub|url_sub)",
    re.I,
)


def column_name_is_secret(name: str) -> bool:
    """True when a column *name* suggests credentials. Used for uncatalogued columns."""
    return bool(_SECRET_NAME.search(name))


def _digits(text: str) -> str:
    """ASCII digits of ``text`` (Persian / Arabic-Indic digits are converted)."""
    out = []
    for ch in text:
        if ch.isdigit():
            d = unicodedata.digit(ch, None)
            if d is not None:
                out.append(str(d))
    return "".join(out)


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = ord(ch) - 48
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def luhn_valid(number: str) -> bool:
    digits = _digits(number)
    return 13 <= len(digits) <= 19 and _luhn_ok(digits)


def _mask_card(m: re.Match[str]) -> str:
    digits = _digits(m.group(0))
    if 13 <= len(digits) <= 19 and _luhn_ok(digits):
        return f"[REDACTED:card:****{digits[-4:]}]"
    return m.group(0)


def redact_text(value: str, on_hit: Callable[[str], None] | None = None) -> str:
    """Return ``value`` with every detected secret replaced by ``[REDACTED:<kind>]``."""
    if not value or len(value) < 8:
        return value
    out = value
    for kind, pat in _PATTERNS:
        if not pat.search(out):
            continue
        if kind == "kv_secret":
            out, n = pat.subn(lambda m: m.group("label") + "[REDACTED:kv_secret]", out)
        else:
            out, n = pat.subn(f"[REDACTED:{kind}]", out)
        if n and on_hit:
            for _ in range(n):
                on_hit(kind)
    if _CARD.search(out):
        hits = 0

        def mask(m: re.Match[str]) -> str:
            nonlocal hits
            new = _mask_card(m)
            if new != m.group(0):
                hits += 1
            return new

        out = _CARD.sub(mask, out)
        if hits and on_hit:
            for _ in range(hits):
                on_hit("card")
    return out


def contains_secret(value: str) -> bool:
    hits: list[str] = []
    redact_text(value, hits.append)
    return bool(hits)

"""Two bounds on what leaves the converter: record keys NEXA can store, and JSON key names that are names.

- ``idempotency_key``: NEXA refuses a key over 512 characters (``char_length(idempotency_key) BETWEEN 8 AND
  512``), and a Mirza ``VARCHAR(500)`` primary key can make ``legacy:carried:<table>:<pk>`` longer than that.
- ``json_keys``: only the top-level key NAMES of a JSON document are kept; a key that is really a credential
  (an API key used as a dynamic object key) must not pass as a name.
"""

from __future__ import annotations

import json

from mirza2nexa.modules.generic import MAX_KEY_LENGTH, idempotency_key
from mirza2nexa.sensitivity import minimise


def _key(table: str, pk_value: str) -> tuple[str, str | None]:
    return idempotency_key(table, ("id",), {"id": pk_value}, "c" * 64, {})


def test_a_short_primary_key_is_kept_verbatim():
    key, source_pk = _key("carried:user", "7000000001")
    assert key == "legacy:carried:user:7000000001"
    assert source_pk == "7000000001"


def test_an_oversized_primary_key_becomes_a_bounded_stable_digest():
    long_pk = "x" * 500  # user.id is VARCHAR(500)
    key, source_pk = _key("carried:user", long_pk)
    assert len(key) <= MAX_KEY_LENGTH < 512
    assert key.startswith("legacy:carried:user:pkh:")
    assert source_pk == long_pk  # the full value is still carried, beside the key
    # the same row in a later backup gets the same key; another value does not
    assert _key("carried:user", long_pk)[0] == key
    assert _key("carried:user", "y" * 500)[0] != key
    # room is left for the duplicate suffix the exporters append
    assert len(f"{key}#dup99999") <= 512


def test_the_bound_is_exact():
    prefix = "legacy:t:"
    fits = "a" * (MAX_KEY_LENGTH - len(prefix))
    assert _key("t", fits)[0] == prefix + fits
    assert ":pkh:" in _key("t", fits + "a")[0]


def test_json_keys_keeps_field_names_and_hides_token_shaped_keys():
    api_key = "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6"  # 32 characters, letters and digits: a credential, not a name
    doc = {"user_id": 1, "request.path": "/x", "status-code": 200, api_key: {"plan": "pro"}}
    out = json.loads(minimise("json_keys", json.dumps(doc)))
    assert api_key not in json.dumps(out)
    assert out["key_count"] == 4
    assert sorted(out["keys"]) == ["[unusual-key]", "request.path", "status-code", "user_id"]


def test_json_keys_hides_a_key_the_secret_detector_flags():
    bot_token = "123456789:" + "A" * 35  # Telegram bot token shape
    out = minimise("json_keys", json.dumps({bot_token: 1, "ok": True}))
    assert bot_token not in out
    assert "ok" in json.loads(out)["keys"]

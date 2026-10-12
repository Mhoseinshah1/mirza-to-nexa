"""Canonical JSON encoding used everywhere inside a ``.nxpkg``.

Rules: UTF-8, sorted object keys, separators ``(",", ":")``, no insignificant whitespace,
``ensure_ascii=False``. Floats are rejected (financial safety: amounts must be integers in
minor units or decimal strings); :class:`decimal.Decimal` is encoded as its string form.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

__all__ = ["canonical_json", "loads_strict"]


def _normalise(obj: Any, depth: int = 0) -> Any:
    if depth > 200:
        raise ValueError("canonical_json: nesting too deep")
    # bool is a subclass of int, check it first for clarity (both are allowed).
    if obj is None or isinstance(obj, (bool, str)):
        return obj
    if isinstance(obj, int):
        return int(obj)
    if isinstance(obj, float):
        raise TypeError("canonical_json: floats are not allowed (use int minor units or Decimal/str)")
    if isinstance(obj, Decimal):
        if not obj.is_finite():
            raise ValueError("canonical_json: non-finite Decimal")
        return str(obj)
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if not isinstance(k, str):
                raise TypeError(f"canonical_json: object keys must be str, got {type(k).__name__}")
            out[k] = _normalise(v, depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        return [_normalise(v, depth + 1) for v in obj]
    raise TypeError(f"canonical_json: unsupported type {type(obj).__name__}")


def canonical_json(obj: Any) -> bytes:
    """Encode ``obj`` as canonical JSON bytes (see module docstring)."""
    return json.dumps(
        _normalise(obj),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _reject_float(s: str) -> Any:
    raise ValueError("float literal not allowed in nxpkg JSON")


def _reject_constant(s: str) -> Any:
    raise ValueError(f"non-standard JSON constant {s!r} not allowed")


def loads_strict(data: bytes | str) -> Any:
    """Parse JSON produced by :func:`canonical_json`; rejects float literals and NaN/Infinity.

    Raises ``ValueError`` (``json.JSONDecodeError`` is a subclass) on any violation.
    """
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    return json.loads(data, parse_float=_reject_float, parse_constant=_reject_constant)

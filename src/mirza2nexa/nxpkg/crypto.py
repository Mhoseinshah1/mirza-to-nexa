"""Cryptographic envelope of the ``.nxpkg`` container (format v1).

Layout (see ``docs/NXPKG_FORMAT.md`` for the normative description)::

    MAGIC (6) || major (1) || header_len (4, BE) || header (canonical JSON)
    || { ct_len (4, BE) || ciphertext_i } for i = 0..N-1

Each chunk is AES-256-GCM with nonce ``nonce_prefix(7) || counter(4, BE) || last(1)`` and
AAD ``SHA-256(MAGIC || major || header_len || header)``. Exactly the final chunk has
``last = 1``; every non-final chunk carries exactly ``chunk_size`` plaintext bytes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from ..errors import PackageError, PackageTampered
from ..progress import CancelToken
from .canonical import canonical_json, loads_strict

MAGIC = b"NXPKG\x00"
FORMAT_MAJOR = 1
FORMAT_NAME = "nxpkg"
CIPHER = "AES-256-GCM-STREAM"
DEFAULT_CHUNK_SIZE = 1 << 20  # 1 MiB
MIN_CHUNK_SIZE = 1 << 12
MAX_CHUNK_SIZE = 1 << 24
MAX_HEADER_LEN = 16 * 1024
TAG_LEN = 16
NONCE_PREFIX_LEN = 7
SALT_LEN = 16
KEY_LEN = 32
MAX_CHUNKS = (1 << 32) - 1

SCRYPT_DEFAULT_N = 1 << 17
SCRYPT_DEFAULT_R = 8
SCRYPT_DEFAULT_P = 1
SCRYPT_MIN_N = 1 << 10
SCRYPT_MAX_N = 1 << 20
MIN_PASSPHRASE_LEN = 12

KEY_CHECK_MSG = b"nxpkg-key-check-v1"
HKDF_INFO_ENC = b"nxpkg-v1/aes-256-gcm-stream/"
HKDF_INFO_CHECK = b"nxpkg-v1/key-check"

KEY_FILE_PREFIX = "nxkey1:"
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

HEADER_KEYS = frozenset(
    {"format", "format_version", "cipher", "chunk_size", "kdf", "key_check", "nonce_prefix", "created_by"}
)


class WrongPackageKey(PackageTampered):
    code = "package.wrong_key"
    message_fa = "رمز یا کلید بسته NEXA صحیح نیست."


class UnsupportedPackageVersion(PackageError):
    code = "package.unsupported_version"
    message_fa = "نسخه این بسته NEXA پشتیبانی نمی‌شود."


class InvalidKeyFile(PackageError):
    code = "package.bad_key_file"
    message_fa = "فایل کلید بسته NEXA معتبر نیست."


# --------------------------------------------------------------------------- encoding


def b64e(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def b64d(text: Any, *, expect_len: int | None = None) -> bytes:
    if not isinstance(text, str):
        raise ValueError("expected base64 string")
    raw = base64.b64decode(text.encode("ascii"), validate=True)
    if b64e(raw) != text:
        raise ValueError("non-canonical base64")
    if expect_len is not None and len(raw) != expect_len:
        raise ValueError("unexpected length")
    return raw


# --------------------------------------------------------------------------- user secrets


def generate_passphrase(groups: int = 6, group_len: int = 5) -> str:
    """Strong random passphrase, e.g. ``7KQ2M-...`` (Crockford base32; 6x5 chars = 150 bits)."""
    return "-".join("".join(secrets.choice(_CROCKFORD) for _ in range(group_len)) for _ in range(groups))


def _key_file_checksum(key: bytes) -> bytes:
    return hashlib.sha256(b"nxkey1" + key).digest()[:4]


def generate_key_file() -> str:
    """Return a fresh random 32-byte key encoded as key-file text (see :func:`parse_key_file`).

    The text is ``"nxkey1:" + base64url(key || sha256("nxkey1" || key)[:4])`` (no padding) and
    must be stored/delivered separately from the package.
    """
    return encode_key_file(secrets.token_bytes(KEY_LEN))


def encode_key_file(key: bytes) -> str:
    if len(key) != KEY_LEN:
        raise ValueError("key must be 32 bytes")
    return KEY_FILE_PREFIX + base64.urlsafe_b64encode(key + _key_file_checksum(key)).decode("ascii").rstrip(
        "="
    )


def parse_key_file(text: str) -> bytes:
    """Decode key-file text (whitespace tolerant) → 32 raw bytes. Raises :class:`InvalidKeyFile`."""
    s = "".join(text.split())
    if not s.startswith(KEY_FILE_PREFIX):
        raise InvalidKeyFile(detail="missing nxkey1 prefix")
    body = s[len(KEY_FILE_PREFIX) :]
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    except (ValueError, TypeError):
        raise InvalidKeyFile(detail="key file is not base64url") from None
    if len(raw) != KEY_LEN + 4:
        raise InvalidKeyFile(detail="key file has wrong length")
    key, check = raw[:KEY_LEN], raw[KEY_LEN:]
    if not hmac.compare_digest(check, _key_file_checksum(key)):
        raise InvalidKeyFile(detail="key file checksum mismatch (typo or truncation)")
    return key


def normalize_passphrase(passphrase: str) -> bytes:
    return unicodedata.normalize("NFC", passphrase).encode("utf-8")


# --------------------------------------------------------------------------- key schedule


@dataclass(frozen=True)
class PackageKeys:
    enc_key: bytes
    key_check: bytes  # 16 bytes


def scrypt_kdf(
    salt: bytes, n: int = SCRYPT_DEFAULT_N, r: int = SCRYPT_DEFAULT_R, p: int = SCRYPT_DEFAULT_P
) -> dict:
    return {"name": "scrypt", "n": n, "r": r, "p": p, "salt": b64e(salt)}


def _hkdf(master: bytes, info: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=KEY_LEN, salt=None, info=info).derive(master)


def derive_keys(kdf: dict, nonce_prefix: bytes, *, passphrase: str | None, key: bytes | None) -> PackageKeys:
    """Derive the per-package encryption key and the key-check value from the user secret."""
    master = _master_key(kdf, passphrase=passphrase, key=key)
    enc_key = _hkdf(master, HKDF_INFO_ENC + nonce_prefix)
    return PackageKeys(enc_key=enc_key, key_check=_key_check(master))


def _key_check(master: bytes) -> bytes:
    check_key = _hkdf(master, HKDF_INFO_CHECK)
    return hmac.new(check_key, KEY_CHECK_MSG, hashlib.sha256).digest()[:16]


def derive_aux_key(header: dict, info: bytes, *, passphrase: str | None, key: bytes | None) -> bytes:
    """A key for a companion document of this package (e.g. ownership decisions).

    Derived from the same package secret with a distinct HKDF ``info`` so it never equals the
    encryption key. The secret is verified against the header's ``key_check`` first, so a wrong
    secret is refused instead of silently producing an unverifiable MAC.
    """
    if not info or info.startswith(HKDF_INFO_ENC) or info == HKDF_INFO_CHECK:
        raise PackageError(code="package.invalid", detail="reserved hkdf info")
    master = _master_key(header["kdf"], passphrase=passphrase, key=key)
    if not hmac.compare_digest(_key_check(master), b64d(header["key_check"], expect_len=16)):
        raise WrongPackageKey()
    return _hkdf(master, info)


def _master_key(kdf: dict, *, passphrase: str | None, key: bytes | None) -> bytes:
    if kdf["name"] == "scrypt":
        if passphrase is None:
            raise PackageError(
                "این بسته با رمز عبور ساخته شده است؛ رمز را وارد کنید.", code="package.passphrase_required"
            )
        salt = b64d(kdf["salt"], expect_len=SALT_LEN)
        master = Scrypt(salt=salt, length=KEY_LEN, n=kdf["n"], r=kdf["r"], p=kdf["p"]).derive(
            normalize_passphrase(passphrase)
        )
    elif kdf["name"] == "raw-key":
        if key is None:
            raise PackageError(
                "این بسته با فایل کلید ساخته شده است؛ فایل کلید را وارد کنید.", code="package.key_required"
            )
        if len(key) != KEY_LEN:
            raise InvalidKeyFile(detail="key must be 32 bytes")
        master = key
    else:  # pragma: no cover - validated earlier
        raise PackageError(code="package.invalid", detail="unknown kdf")
    return master


# --------------------------------------------------------------------------- header


def encode_header(header: dict) -> bytes:
    """Full header prefix bytes: MAGIC || major || len || canonical JSON."""
    body = canonical_json(header)
    if len(body) > MAX_HEADER_LEN:
        raise ValueError("header too large")
    return MAGIC + bytes([FORMAT_MAJOR]) + struct.pack(">I", len(body)) + body


def build_header(
    *, kdf: dict, key_check: bytes, nonce_prefix: bytes, chunk_size: int, created_by: dict
) -> dict:
    return {
        "format": FORMAT_NAME,
        "format_version": FORMAT_MAJOR,
        "cipher": CIPHER,
        "chunk_size": chunk_size,
        "kdf": kdf,
        "key_check": b64e(key_check),
        "nonce_prefix": b64e(nonce_prefix),
        "created_by": created_by,
    }


def _read_exact(fp: BinaryIO, n: int) -> bytes:
    buf = fp.read(n)
    return buf if buf is not None else b""


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validate_header(h: Any) -> None:
    """Structural validation; raises PackageTampered for anything malformed."""

    def bad(why: str) -> PackageTampered:
        return PackageTampered(detail=f"invalid header: {why}")

    if not isinstance(h, dict) or set(h) != HEADER_KEYS:
        raise bad("unexpected key set")
    if h["format"] != FORMAT_NAME or h["cipher"] != CIPHER:
        raise bad("format/cipher")
    if not _is_int(h["format_version"]):
        raise bad("format_version")
    if h["format_version"] != FORMAT_MAJOR:
        raise UnsupportedPackageVersion(detail=f"header format_version {h['format_version']}")
    cs = h["chunk_size"]
    if not _is_int(cs) or not (MIN_CHUNK_SIZE <= cs <= MAX_CHUNK_SIZE):
        raise bad("chunk_size")
    kdf = h["kdf"]
    if not isinstance(kdf, dict):
        raise bad("kdf")
    if kdf.get("name") == "scrypt":
        if set(kdf) != {"name", "n", "r", "p", "salt"}:
            raise bad("kdf keys")
        n, r, p = kdf["n"], kdf["r"], kdf["p"]
        if not (_is_int(n) and _is_int(r) and _is_int(p)):
            raise bad("kdf params")
        if not (SCRYPT_MIN_N <= n <= SCRYPT_MAX_N and n & (n - 1) == 0 and 1 <= r <= 32 and 1 <= p <= 16):
            raise bad("kdf params out of range")
        try:
            b64d(kdf["salt"], expect_len=SALT_LEN)
        except ValueError:
            raise bad("kdf salt") from None
    elif kdf.get("name") == "raw-key":
        if set(kdf) != {"name"}:
            raise bad("kdf keys")
    else:
        raise bad("kdf name")
    try:
        b64d(h["key_check"], expect_len=16)
        b64d(h["nonce_prefix"], expect_len=NONCE_PREFIX_LEN)
    except ValueError:
        raise bad("key_check/nonce_prefix") from None
    cb = h["created_by"]
    if not (
        isinstance(cb, dict)
        and set(cb) == {"name", "version"}
        and all(isinstance(v, str) for v in cb.values())
    ):
        raise bad("created_by")


def read_header_from(fp: BinaryIO) -> tuple[dict, bytes]:
    """Read and validate the header. Returns ``(header_dict, header_prefix_bytes)``."""
    prefix = _read_exact(fp, len(MAGIC) + 1 + 4)
    if len(prefix) < len(MAGIC) + 5 or prefix[: len(MAGIC)] != MAGIC:
        raise PackageError("این فایل یک بسته NEXA (.nxpkg) نیست.", code="package.not_nxpkg")
    major = prefix[len(MAGIC)]
    if major != FORMAT_MAJOR:
        raise UnsupportedPackageVersion(detail=f"container major version {major}")
    (hlen,) = struct.unpack(">I", prefix[len(MAGIC) + 1 :])
    if hlen == 0 or hlen > MAX_HEADER_LEN:
        raise PackageTampered(detail="header length out of range")
    body = _read_exact(fp, hlen)
    if len(body) != hlen:
        raise PackageTampered(detail="truncated header")
    try:
        header = loads_strict(body)
    except (ValueError, UnicodeDecodeError):
        raise PackageTampered(detail="header is not valid JSON") from None
    validate_header(header)
    if canonical_json(header) != body:
        raise PackageTampered(detail="header is not canonical JSON")
    return header, prefix + body


# --------------------------------------------------------------------------- STREAM


def _nonce(prefix: bytes, counter: int, last: bool) -> bytes:
    return prefix + struct.pack(">I", counter) + (b"\x01" if last else b"\x00")


class EncryptingWriter:
    """File-like sink: buffers plaintext, emits length-prefixed AES-GCM STREAM chunks to ``raw``.

    Memory is bounded by ~2 x ``chunk_size``. ``close()`` emits the final chunk (possibly
    empty) and must be called exactly once; it does not close ``raw``. Non-seekable on
    purpose (``tell`` works, ``seek`` does not) so ``zipfile`` streams with data descriptors.
    """

    def __init__(
        self,
        raw: BinaryIO,
        header_prefix: bytes,
        enc_key: bytes,
        nonce_prefix: bytes,
        chunk_size: int,
        *,
        cancel: CancelToken | None = None,
    ) -> None:
        if len(nonce_prefix) != NONCE_PREFIX_LEN:
            raise ValueError("nonce_prefix must be 7 bytes")
        self._raw = raw
        self._aead = AESGCM(enc_key)
        self._aad = hashlib.sha256(header_prefix).digest()
        self._prefix = nonce_prefix
        self._chunk = chunk_size
        self._buf = bytearray()
        self._counter = 0
        self._pos = 0
        self._closed = False
        self._cancel = cancel
        self._hash = hashlib.sha256()
        self.bytes_out = 0
        raw.write(header_prefix)
        self.bytes_out += len(header_prefix)

    # -- file-like API used by zipfile
    def write(self, data: bytes | bytearray | memoryview) -> int:
        if self._closed:
            raise ValueError("write to closed EncryptingWriter")
        mv = memoryview(data).cast("B")
        n = len(mv)
        self._hash.update(mv)
        self._buf += mv
        self._pos += n
        # Only emit when we know more data follows (strictly more than one chunk buffered),
        # so that the last-chunk flag can be decided at close().
        if len(self._buf) > self._chunk:
            off = 0
            while len(self._buf) - off > self._chunk:
                self._emit(memoryview(self._buf)[off : off + self._chunk], last=False)
                off += self._chunk
            del self._buf[:off]
        return n

    def tell(self) -> int:
        return self._pos

    def flush(self) -> None:
        self._raw.flush()

    def seekable(self) -> bool:
        return False

    def writable(self) -> bool:
        return True

    @property
    def plaintext_sha256(self) -> str:
        return self._hash.hexdigest()

    @property
    def plaintext_size(self) -> int:
        return self._pos

    def _emit(self, pt: memoryview | bytes, *, last: bool) -> None:
        if self._cancel is not None:
            self._cancel.check()
        if self._counter > MAX_CHUNKS - 1:
            raise ValueError("too many chunks")
        ct = self._aead.encrypt(_nonce(self._prefix, self._counter, last), bytes(pt), self._aad)
        self._raw.write(struct.pack(">I", len(ct)))
        self._raw.write(ct)
        self.bytes_out += 4 + len(ct)
        self._counter += 1

    def close(self) -> None:
        if self._closed:
            return
        self._emit(bytes(self._buf), last=True)
        self._buf = bytearray()
        self._closed = True
        self._raw.flush()

    @property
    def chunks(self) -> int:
        return self._counter


def iter_decrypt(
    fp: BinaryIO,
    header_prefix: bytes,
    enc_key: bytes,
    nonce_prefix: bytes,
    chunk_size: int,
    *,
    cancel: CancelToken | None = None,
) -> Iterator[bytes]:
    """Yield authenticated plaintext chunks. Raises :class:`PackageTampered` on any violation.

    Callers must treat the plaintext as untrusted until the generator is exhausted without
    error (truncation is only detectable at the end).
    """
    aead = AESGCM(enc_key)
    aad = hashlib.sha256(header_prefix).digest()
    counter = 0
    max_ct = chunk_size + TAG_LEN
    while True:
        if cancel is not None:
            cancel.check()
        lb = _read_exact(fp, 4)
        if len(lb) == 0:
            raise PackageTampered(detail="truncated: final chunk missing")
        if len(lb) != 4:
            raise PackageTampered(detail="truncated chunk length")
        (ct_len,) = struct.unpack(">I", lb)
        if ct_len < TAG_LEN or ct_len > max_ct:
            raise PackageTampered(detail="chunk length out of range")
        ct = _read_exact(fp, ct_len)
        if len(ct) != ct_len:
            raise PackageTampered(detail="truncated chunk")
        # A full-size chunk may be non-final or final; a short one can only be final.
        is_full = ct_len == max_ct
        pt = None
        if is_full:
            try:
                pt = aead.decrypt(_nonce(nonce_prefix, counter, False), ct, aad)
                last = False
            except InvalidTag:
                pt = None
        if pt is None:
            try:
                pt = aead.decrypt(_nonce(nonce_prefix, counter, True), ct, aad)
                last = True
            except InvalidTag:
                raise PackageTampered(detail=f"authentication failed at chunk {counter}") from None
        counter += 1
        yield pt
        if last:
            if fp.read(1):
                raise PackageTampered(detail="trailing data after final chunk")
            return
        if counter > MAX_CHUNKS:
            raise PackageTampered(detail="too many chunks")


def random_nonce_prefix() -> bytes:
    return os.urandom(NONCE_PREFIX_LEN)


def random_salt() -> bytes:
    return os.urandom(SALT_LEN)

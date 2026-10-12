"""Safe intake of untrusted (possibly password-protected) ZIP backups.

Threat model: the ZIP comes from an untrusted source. We never call ``extractall`` and never
use names from the archive as filesystem paths. Every limit that matters (entry size, total size,
compression ratio) is enforced on the *actual* number of decompressed bytes while streaming, so
forged headers cannot be used to bypass them.

Decryption (ZipCrypto and WinZip AES) is delegated to :mod:`pyzipper`, but decompression is done
here with output-bounded decompressors: pyzipper is asked for the *raw* (decrypted, still
compressed) byte stream by opening a copy of the entry's ``ZipInfo`` marked as ``stored``. This
means pyzipper's own trust in the declared uncompressed size (it silently truncates output to it)
and its unbounded bzip2/lzma decompression are never relied upon.

Passwords and file contents never appear in exception messages or details.
"""

from __future__ import annotations

import bz2
import copy
import errno
import hashlib
import lzma
import os
import re
import shutil
import stat
import struct
import tempfile
import time
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import pyzipper
from pyzipper import zipfile as _pz

from .errors import (
    ArchiveError,
    DiskSpaceError,
    LimitExceeded,
    MigrationError,
    PasswordRequired,
    UnsafeArchive,
    WrongPassword,
)
from .progress import CancelToken, ProgressFn

__all__ = [
    "ArchiveCorrupt",
    "ArchiveInspection",
    "ArchiveLimits",
    "ArchiveUnsupported",
    "DEFAULT_LIMITS",
    "EntryInfo",
    "ExtractedFile",
    "WorkspaceError",
    "cleanup_workspace",
    "extract_sql",
    "inspect_archive",
    "secure_workspace",
    "verify_password",
]


# ---------------------------------------------------------------------------------------------
# Errors specific to this module
# ---------------------------------------------------------------------------------------------


class ArchiveCorrupt(ArchiveError):
    code = "archive.corrupt"
    message_fa = "فایل ZIP خراب یا ناقص است."


class ArchiveUnsupported(ArchiveError):
    code = "archive.unsupported"
    message_fa = "روش فشرده‌سازی یا رمزنگاری این فایل ZIP پشتیبانی نمی‌شود."


class WorkspaceError(MigrationError):
    code = "workspace.io"
    message_fa = "نوشتن در پوشه کاری موقت با خطا مواجه شد."


_MSG_NOT_ZIP = "فایل انتخاب‌شده یک فایل ZIP معتبر نیست یا ناقص است."
_MSG_NO_SQL = "داخل فایل ZIP هیچ فایل ‎.sql‎ پیدا نشد."
_MSG_WRONG_OR_CORRUPT = "رمز وارد‌شده صحیح نیست یا فایل ZIP خراب است."


# ---------------------------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ArchiveLimits:
    max_archive_bytes: int = 4 * 1024**3
    max_entries: int = 64
    max_total_uncompressed: int = 20 * 1024**3
    max_entry_uncompressed: int = 16 * 1024**3
    max_ratio: float = 200.0  # per entry uncompressed/compressed, enforced on ACTUAL bytes while streaming
    min_free_disk_margin: int = 512 * 1024**2
    allowed_suffixes: tuple[str, ...] = (".sql",)  # others are ignored (listed as skipped), never extracted


@dataclass(frozen=True)
class EntryInfo:
    name: str
    size: int
    compressed_size: int
    encrypted: bool
    method: str  # "stored" / "deflate" / "bzip2" / "lzma" / "aes" / "unknown(<n>)"
    kind: str  # "sql" | "skipped" | "directory"


@dataclass(frozen=True)
class ArchiveInspection:
    entries: list[EntryInfo]
    encrypted: bool
    encryption: str | None  # "zipcrypto" | "aes" | None
    sql_entries: list[str]
    archive_sha256: str
    archive_bytes: int


@dataclass(frozen=True)
class ExtractedFile:
    entry_name: str
    path: Path
    size: int
    sha256: str


DEFAULT_LIMITS = ArchiveLimits()  # frozen, safe to share as a default argument

# ---------------------------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------------------------

CHUNK = 1024 * 1024  # output / IO chunk size
_RAW_CHUNK_SLOW = 64 * 1024  # raw input chunk for bzip2/lzma (keeps the ratio check tight)
RATIO_GRACE = 1024 * 1024  # ratio is only enforced once an entry produced this many bytes
_DISK_CHECK_EVERY = 64 * 1024 * 1024
_HUGE = 1 << 62

_METHOD_NAMES = {
    _pz.ZIP_STORED: "stored",
    _pz.ZIP_DEFLATED: "deflate",
    _pz.ZIP_BZIP2: "bzip2",
    _pz.ZIP_LZMA: "lzma",
}
_SUPPORTED_METHODS = frozenset(_METHOD_NAMES)

_FLAG_ENCRYPTED = 0x0001
_FLAG_STRONG_ENCRYPTION = 0x0040
_FLAG_CD_ENCRYPTED = 0x2000

_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_SAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9._-]+")
_CENTRAL_HEADER_MAX = 46 + 3 * 0xFFFF  # fixed part + name + extra + comment
_LOCAL_HEADER_MIN = 30


# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------


def _safe_repr(name: str, limit: int = 80) -> str:
    """Developer-facing, bounded representation of an entry name (never contents/passwords)."""
    r = repr(name)
    return r if len(r) <= limit else r[: limit - 3] + "..."


def _is_aes(zinfo: _pz.ZipInfo) -> bool:
    return getattr(zinfo, "wz_aes_vendor_id", None) is not None


def _is_encrypted(zinfo: _pz.ZipInfo) -> bool:
    return bool(zinfo.flag_bits & _FLAG_ENCRYPTED)


def _method_name(zinfo: _pz.ZipInfo) -> str:
    if _is_aes(zinfo):
        return "aes"
    return _METHOD_NAMES.get(zinfo.compress_type, f"unknown({zinfo.compress_type})")


def _normalized_name(raw: str) -> str:
    return raw.replace("\\", "/")


def _check_name(raw: str) -> None:
    """Reject absolute paths, drive letters, traversal (``/`` or ``\\``) and NUL bytes."""
    if not raw:
        raise UnsafeArchive(detail="empty entry name")
    if "\x00" in raw:
        raise UnsafeArchive(detail="entry name contains NUL")
    if any(ord(c) < 0x20 for c in raw):
        raise UnsafeArchive(detail=f"entry name contains control characters: {_safe_repr(raw)}")
    name = _normalized_name(raw)
    if name.startswith("/"):
        raise UnsafeArchive(detail=f"absolute entry name: {_safe_repr(raw)}")
    if _DRIVE_RE.match(name):
        raise UnsafeArchive(detail=f"entry name with drive letter: {_safe_repr(raw)}")
    parts = name.split("/")
    if any(p == ".." for p in parts):
        raise UnsafeArchive(detail=f"path traversal in entry name: {_safe_repr(raw)}")


def _check_file_type(zinfo: _pz.ZipInfo) -> bool:
    """Return True if the entry is a directory; raise for symlinks / special files."""
    is_dir = zinfo.filename.endswith("/") or _normalized_name(zinfo.orig_filename).endswith("/")
    mode = (zinfo.external_attr >> 16) & 0xFFFF
    if mode:
        fmt = stat.S_IFMT(mode)
        if stat.S_ISLNK(mode):
            raise UnsafeArchive(detail=f"symlink entry: {_safe_repr(zinfo.orig_filename)}")
        if fmt and fmt not in (stat.S_IFREG, stat.S_IFDIR):
            raise UnsafeArchive(detail=f"special file entry: {_safe_repr(zinfo.orig_filename)}")
        if stat.S_ISDIR(mode):
            is_dir = True
    return is_dir


def _is_sql_candidate(name: str, limits: ArchiveLimits) -> bool:
    p = PurePosixPath(name)
    if "__MACOSX" in p.parts or p.name.startswith("._"):
        return False  # macOS AppleDouble metadata, never a real dump
    return p.name.lower().endswith(tuple(s.lower() for s in limits.allowed_suffixes))


def _hash_file(path: Path, cancel: CancelToken | None) -> tuple[str, int]:
    h = hashlib.sha256()
    total = 0
    with open(path, "rb") as f:
        while True:
            if cancel is not None:
                cancel.check()
            block = f.read(CHUNK)
            if not block:
                break
            h.update(block)
            total += len(block)
    return h.hexdigest(), total


def _check_archive_size(path: Path, limits: ArchiveLimits) -> int:
    try:
        st = os.stat(path)
    except FileNotFoundError:
        raise ArchiveError(_MSG_NOT_ZIP, detail="archive file not found") from None
    except OSError as exc:
        raise ArchiveError(_MSG_NOT_ZIP, detail=f"cannot stat archive: {exc.strerror}") from None
    if not stat.S_ISREG(st.st_mode):
        raise ArchiveError(_MSG_NOT_ZIP, detail="archive is not a regular file")
    if st.st_size > limits.max_archive_bytes:
        raise LimitExceeded(detail=f"archive is {st.st_size} bytes (limit {limits.max_archive_bytes})")
    return st.st_size


def _precheck_central_directory(fp, limits: ArchiveLimits) -> None:
    """Read the end-of-central-directory record ourselves to bound work before parsing."""
    try:
        endrec = _pz._EndRecData(fp)
    except (OSError, struct.error, ValueError) as exc:
        raise ArchiveError(_MSG_NOT_ZIP, detail=f"cannot read end record: {type(exc).__name__}") from None
    if not endrec:
        raise ArchiveError(_MSG_NOT_ZIP, detail="no end-of-central-directory record")
    total = endrec[_pz._ECD_ENTRIES_TOTAL]
    cd_size = endrec[_pz._ECD_SIZE]
    if total > limits.max_entries:
        raise LimitExceeded(detail=f"archive declares {total} entries (limit {limits.max_entries})")
    if cd_size > (limits.max_entries + 1) * _CENTRAL_HEADER_MAX:
        raise LimitExceeded(detail=f"central directory too large ({cd_size} bytes)")


def _open_zip(path: Path, limits: ArchiveLimits) -> pyzipper.AESZipFile:
    try:
        with open(path, "rb") as fp:
            _precheck_central_directory(fp, limits)
        zf = pyzipper.AESZipFile(path, "r")
    except MigrationError:
        raise
    except (_pz.BadZipFile, _pz.LargeZipFile) as exc:
        raise ArchiveError(_MSG_NOT_ZIP, detail=f"not a valid zip: {type(exc).__name__}") from None
    except (OSError, EOFError, struct.error, ValueError, KeyError, UnicodeDecodeError) as exc:
        raise ArchiveError(_MSG_NOT_ZIP, detail=f"cannot parse zip: {type(exc).__name__}") from None
    return zf


@dataclass(frozen=True)
class _Validated:
    entries: list[EntryInfo]
    sql_infos: list[_pz.ZipInfo]
    encryption: str | None


def _validate(zf: pyzipper.AESZipFile, archive_bytes: int, limits: ArchiveLimits) -> _Validated:
    infos = zf.infolist()
    if len(infos) > limits.max_entries:
        raise LimitExceeded(detail=f"archive has {len(infos)} entries (limit {limits.max_entries})")

    seen: set[str] = set()
    entries: list[EntryInfo] = []
    sql_infos: list[_pz.ZipInfo] = []
    any_aes = any_zipcrypto = False

    for zi in infos:
        raw = zi.orig_filename
        _check_name(raw)
        is_dir = _check_file_type(zi)
        norm = _normalized_name(raw).rstrip("/")
        key = norm.casefold()
        if key in seen:
            raise UnsafeArchive(detail=f"duplicate entry name: {_safe_repr(raw)}")
        seen.add(key)

        encrypted = _is_encrypted(zi)
        if encrypted:
            if _is_aes(zi):
                any_aes = True
            else:
                any_zipcrypto = True

        if is_dir:
            kind = "directory"
        elif _is_sql_candidate(norm, limits):
            kind = "sql"
        else:
            kind = "skipped"

        entries.append(
            EntryInfo(
                name=norm + ("/" if is_dir else ""),
                size=zi.file_size,
                compressed_size=zi.compress_size,
                encrypted=encrypted,
                method=_method_name(zi),
                kind=kind,
            )
        )
        if kind == "sql":
            sql_infos.append(zi)

    _check_overlaps(infos, archive_bytes)

    total = 0
    for zi in sql_infos:
        name = _safe_repr(zi.orig_filename)
        if zi.flag_bits & (_FLAG_STRONG_ENCRYPTION | _FLAG_CD_ENCRYPTED):
            raise ArchiveUnsupported(detail=f"PKWARE strong encryption is not supported: {name}")
        if zi.compress_type not in _SUPPORTED_METHODS:
            raise ArchiveUnsupported(detail=f"compression method {zi.compress_type} not supported: {name}")
        if _is_aes(zi) and getattr(zi, "wz_aes_strength", None) not in (1, 2, 3):
            raise ArchiveUnsupported(detail=f"unknown AES strength: {name}")
        if zi.file_size > limits.max_entry_uncompressed:
            raise LimitExceeded(
                detail=f"entry {name} declares {zi.file_size} bytes (limit {limits.max_entry_uncompressed})"
            )
        if zi.file_size > RATIO_GRACE and zi.file_size > limits.max_ratio * max(zi.compress_size, 1):
            raise LimitExceeded(detail=f"entry {name} declares a compression ratio above {limits.max_ratio}")
        total += zi.file_size
    if total > limits.max_total_uncompressed:
        raise LimitExceeded(
            detail=f"sql entries declare {total} bytes (limit {limits.max_total_uncompressed})"
        )

    if not sql_infos:
        raise ArchiveError(_MSG_NO_SQL, code="archive.no_sql", detail="no entry with an allowed suffix")

    encryption = "aes" if any_aes else ("zipcrypto" if any_zipcrypto else None)
    return _Validated(entries=entries, sql_infos=sql_infos, encryption=encryption)


def _raw_name_len(zi: _pz.ZipInfo) -> int:
    enc = "utf-8" if zi.flag_bits & 0x800 else "cp437"
    try:
        return len(zi.orig_filename.encode(enc))
    except UnicodeEncodeError:
        return len(zi.orig_filename.encode("utf-8", "surrogatepass"))


def _check_overlaps(infos: list[_pz.ZipInfo], archive_bytes: int) -> None:
    """Reject entries whose data regions overlap (overlapping-file zip bombs) or exceed the file."""
    spans = sorted(infos, key=lambda z: z.header_offset)
    prev_end = 0
    for zi in spans:
        if zi.header_offset < prev_end:
            raise UnsafeArchive(detail=f"overlapping entries at {_safe_repr(zi.orig_filename)}")
        min_len = _LOCAL_HEADER_MIN + _raw_name_len(zi) + zi.compress_size
        end = zi.header_offset + min_len
        if end > archive_bytes:
            raise ArchiveCorrupt(detail=f"entry {_safe_repr(zi.orig_filename)} extends past end of file")
        prev_end = end


# ---------------------------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------------------------


def _password_candidates(password: str) -> list[bytes]:
    """UTF-8 first (7-Zip / WinZip AES); legacy Windows code pages for non-ASCII ZipCrypto."""
    out: list[bytes] = []
    for enc in ("utf-8", "cp1256", "cp720", "cp437"):
        try:
            b = password.encode(enc)
        except UnicodeEncodeError:
            continue
        if b not in out:
            out.append(b)
    return out


def _open_raw(zf: pyzipper.AESZipFile, zinfo: _pz.ZipInfo, pwd: bytes | None):
    """Open the entry as a raw (decrypted but still compressed) byte stream.

    A copy of the ZipInfo is marked ``stored`` with an unbounded size, so pyzipper neither
    decompresses nor truncates to the (untrusted) declared size. CRC is verified by us on the
    real decompressed output; AES HMAC (over the ciphertext) is still verified by pyzipper.
    """
    zc = copy.copy(zinfo)
    zc.compress_type = _pz.ZIP_STORED
    zc.file_size = _HUGE
    zef = zf.open(zc, "r", pwd=pwd)
    zef._expected_crc = None  # CRC applies to decompressed data; checked by _stream_entry callers
    return zef


def _resolve_password(zf: pyzipper.AESZipFile, zinfo: _pz.ZipInfo, password: str | None) -> bytes | None:
    """Return the password bytes that pass the entry's password check, or raise."""
    if not _is_encrypted(zinfo):
        return None
    if not password:
        raise PasswordRequired(detail="encrypted entry and no password supplied")
    for cand in _password_candidates(password):
        try:
            zef = _open_raw(zf, zinfo, cand)
        except RuntimeError:
            continue  # password check failed for this encoding
        except NotImplementedError:
            raise ArchiveUnsupported(detail="unsupported encryption") from None
        except (_pz.BadZipFile, EOFError, OSError, struct.error, ValueError) as exc:
            raise ArchiveCorrupt(detail=f"cannot open entry: {type(exc).__name__}") from None
        zef.close()
        return cand
    raise WrongPassword(detail="password check failed")


# ---------------------------------------------------------------------------------------------
# Streaming decompression with real byte accounting
# ---------------------------------------------------------------------------------------------


class _Stream:
    """Yields decompressed chunks (<= CHUNK) and tracks raw compressed bytes consumed."""

    def __init__(self, zf, zinfo, pwd: bytes | None, cancel: CancelToken | None):
        self.zf = zf
        self.zinfo = zinfo
        self.pwd = pwd
        self.cancel = cancel
        self.consumed = 0  # raw compressed bytes actually consumed by the decompressor
        self.zipcrypto = _is_encrypted(zinfo) and not _is_aes(zinfo)

    def _fail(self, exc: BaseException) -> MigrationError:
        kind = type(exc).__name__
        if isinstance(exc, RuntimeError) and "password" in str(exc).lower():
            return WrongPassword(detail="password check failed")
        if isinstance(exc, NotImplementedError):
            return ArchiveUnsupported(detail=f"unsupported feature: {kind}")
        if isinstance(exc, EOFError) and not isinstance(exc, _DecompressorEOF):
            return ArchiveCorrupt(detail="entry data is truncated")
        if self.zipcrypto:
            # The ZipCrypto check byte gives a 1/256 false positive, so garbage output on an
            # encrypted entry is most likely a wrong password (but may be corruption).
            return WrongPassword(_MSG_WRONG_OR_CORRUPT, detail=f"wrong password or corrupt data ({kind})")
        if "HMAC" in str(exc):
            return ArchiveCorrupt(detail="AES authentication (HMAC) failed: corrupt data or wrong password")
        return ArchiveCorrupt(detail=f"decompression failed ({kind})")

    def __iter__(self) -> Iterator[bytes]:
        try:
            zef = _open_raw(self.zf, self.zinfo, self.pwd)
        except MigrationError:
            raise
        except (
            RuntimeError,
            NotImplementedError,
            _pz.BadZipFile,
            EOFError,
            OSError,
            struct.error,
            ValueError,
        ) as exc:
            raise self._fail(exc) from None
        try:
            yield from self._decompress(zef)
        except MigrationError:
            raise
        except (
            RuntimeError,
            NotImplementedError,
            _pz.BadZipFile,
            EOFError,
            OSError,
            struct.error,
            ValueError,
            zlib.error,
            lzma.LZMAError,
        ) as exc:
            raise self._fail(exc) from None
        finally:
            zef.close()

    def _raw(self, zef, size: int) -> Iterator[bytes]:
        while True:
            if self.cancel is not None:
                self.cancel.check()
            data = zef.read(size)
            if not data:
                return
            yield data

    def _decompress(self, zef) -> Iterator[bytes]:
        method = self.zinfo.compress_type
        if method == _pz.ZIP_STORED:
            for data in self._raw(zef, CHUNK):
                self.consumed += len(data)
                yield data
        elif method == _pz.ZIP_DEFLATED:
            d = zlib.decompressobj(-15)
            for data in self._raw(zef, CHUNK):
                if d.eof:
                    continue  # trailing bytes after the deflate stream: drain for HMAC, ignore
                read_before = self.consumed
                buf = data
                while True:
                    out = d.decompress(buf, CHUNK)
                    buf = d.unconsumed_tail
                    self.consumed = read_before + len(data) - len(buf)
                    if out:
                        yield out
                    if d.eof or (not buf and len(out) < CHUNK):
                        break
            if not d.eof:
                tail = d.flush()
                if tail:
                    yield tail
                if not d.eof:
                    raise _DecompressorEOF("deflate stream truncated")
        elif method == _pz.ZIP_BZIP2:
            yield from self._needs_input_loop(zef, bz2.BZ2Decompressor(), require_eof=True)
        elif method == _pz.ZIP_LZMA:
            yield from self._lzma(zef)
        else:  # pragma: no cover - filtered out by _validate
            raise NotImplementedError(f"method {method}")

    def _needs_input_loop(self, zef, d, *, require_eof: bool, first: bytes = b"") -> Iterator[bytes]:
        def feed(data: bytes) -> Iterator[bytes]:
            out = d.decompress(data, CHUNK)
            if out:
                yield out
            while not d.eof and not d.needs_input:
                if self.cancel is not None:
                    self.cancel.check()
                out = d.decompress(b"", CHUNK)
                if out:
                    yield out

        if first:
            yield from feed(first)
        for data in self._raw(zef, _RAW_CHUNK_SLOW):
            if d.eof:
                continue
            self.consumed += len(data)
            yield from feed(data)
        if require_eof and not d.eof:
            raise _DecompressorEOF("compressed stream truncated")

    def _lzma(self, zef) -> Iterator[bytes]:
        header = b""
        raw_iter = self._raw(zef, _RAW_CHUNK_SLOW)
        for data in raw_iter:
            header += data
            if len(header) >= 4:
                break
        if len(header) < 4:
            raise _DecompressorEOF("lzma header truncated")
        psize = struct.unpack("<H", header[2:4])[0]
        while len(header) < 4 + psize:
            more = next(raw_iter, b"")
            if not more:
                raise _DecompressorEOF("lzma header truncated")
            header += more
        props = header[4 : 4 + psize]
        rest = header[4 + psize :]
        self.consumed += 4 + psize
        filt = lzma._decode_filter_properties(lzma.FILTER_LZMA1, props)
        d = lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=[filt])
        # The end-of-stream marker is optional for LZMA in ZIP; size/CRC checks catch truncation.
        self.consumed += len(rest)
        yield from self._needs_input_loop(zef, d, require_eof=False, first=rest)


class _DecompressorEOF(EOFError):
    pass


# ---------------------------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------------------------


def inspect_archive(
    path: Path, limits: ArchiveLimits = DEFAULT_LIMITS, cancel: CancelToken | None = None
) -> ArchiveInspection:
    """Validate the archive structure without extracting anything."""
    path = Path(path)
    size = _check_archive_size(path, limits)
    digest, hashed = _hash_file(path, cancel)
    zf = _open_zip(path, limits)
    with zf:
        v = _validate(zf, hashed, limits)
    return ArchiveInspection(
        entries=v.entries,
        encrypted=v.encryption is not None,
        encryption=v.encryption,
        sql_entries=[e.name for e in v.entries if e.kind == "sql"],
        archive_sha256=digest,
        archive_bytes=max(size, hashed),
    )


def verify_password(path: Path, password: str | None) -> None:
    """Quickly check the password against every encrypted SQL entry.

    Uses the password verifier (AES: 16 bits, ZipCrypto: 8 bits) and then decompresses a small
    prefix of the first encrypted entry to weed out ZipCrypto false positives. A false positive
    on a *stored* ZipCrypto entry is only detectable by the CRC at the end of extraction.
    """
    path = Path(path)
    limits = DEFAULT_LIMITS
    size = _check_archive_size(path, limits)
    zf = _open_zip(path, limits)
    with zf:
        v = _validate(zf, size, limits)
        encrypted = [zi for zi in v.sql_infos if _is_encrypted(zi)]
        first = True
        for zi in encrypted:
            pwd = _resolve_password(zf, zi, password)
            if first:
                first = False
                produced = 0
                for chunk in _Stream(zf, zi, pwd, None):
                    produced += len(chunk)
                    if produced >= 256 * 1024:
                        break


def _check_disk(workspace: Path, needed: int, margin: int) -> None:
    free = shutil.disk_usage(workspace).free
    if free - needed < margin:
        raise DiskSpaceError(detail=f"free={free} needed={needed} margin={margin}")


def _output_name(index: int, entry_name: str) -> str:
    base = PurePosixPath(_normalized_name(entry_name)).name
    stem = base[:-4] if base.lower().endswith(".sql") else base
    stem = _SAFE_CHARS_RE.sub("_", stem).strip("._-")[:80] or "dump"
    return f"{index:03d}-{stem}.sql"


def extract_sql(
    path: Path,
    workspace: Path,
    password: str | None,
    limits: ArchiveLimits = DEFAULT_LIMITS,
    cancel: CancelToken | None = None,
    progress: ProgressFn | None = None,
) -> list[ExtractedFile]:
    """Stream every SQL entry into ``workspace`` under generated names.

    Progress reports compressed bytes actually consumed / total compressed size of SQL entries.
    On any failure (including cancellation) every file written by this call is removed.
    """
    path = Path(path)
    workspace = Path(workspace)
    if not workspace.is_dir():
        raise WorkspaceError(detail="workspace directory does not exist")
    size = _check_archive_size(path, limits)
    written: list[Path] = []
    results: list[ExtractedFile] = []
    try:
        zf = _open_zip(path, limits)
        with zf:
            v = _validate(zf, size, limits)
            # Resolve all passwords up-front so a wrong password fails before any disk writes.
            pwds = [_resolve_password(zf, zi, password) for zi in v.sql_infos]
            declared_total = sum(zi.file_size for zi in v.sql_infos)
            _check_disk(workspace, declared_total, limits.min_free_disk_margin)

            total_compressed = sum(zi.compress_size for zi in v.sql_infos) or None
            done_before = 0
            actual_total = 0
            if progress is not None:
                progress(0, total_compressed)

            for index, (zi, pwd) in enumerate(zip(v.sql_infos, pwds, strict=True)):
                if cancel is not None:
                    cancel.check()
                out_path = workspace / _output_name(index, zi.orig_filename)
                stream = _Stream(zf, zi, pwd, cancel)
                h = hashlib.sha256()
                crc = 0
                produced = 0
                since_disk_check = 0
                fd = _create_private(out_path)
                written.append(out_path)
                with os.fdopen(fd, "wb") as out:
                    for chunk in stream:
                        produced += len(chunk)
                        actual_total += len(chunk)
                        _enforce(zi, stream, produced, actual_total, limits)
                        h.update(chunk)
                        crc = zlib.crc32(chunk, crc)
                        _write(out, chunk)
                        since_disk_check += len(chunk)
                        if since_disk_check >= _DISK_CHECK_EVERY:
                            since_disk_check = 0
                            _check_disk(workspace, 0, limits.min_free_disk_margin)
                        if progress is not None:
                            progress(
                                _clamp(done_before + stream.consumed, total_compressed), total_compressed
                            )
                    _flush(out)
                _final_checks(zi, stream, produced, crc)
                done_before += zi.compress_size
                if progress is not None:
                    progress(_clamp(done_before, total_compressed), total_compressed)
                results.append(
                    ExtractedFile(
                        entry_name=_normalized_name(zi.orig_filename),
                        path=out_path,
                        size=produced,
                        sha256=h.hexdigest(),
                    )
                )
    except BaseException:
        for p in written:
            _unlink_quiet(p)
        raise
    return results


def _clamp(done: int, total: int | None) -> int:
    return done if total is None else min(done, total)


def _enforce(zi, stream: _Stream, produced: int, actual_total: int, limits: ArchiveLimits) -> None:
    name = _safe_repr(zi.orig_filename)
    if produced > zi.file_size:
        if stream.zipcrypto:
            raise WrongPassword(
                _MSG_WRONG_OR_CORRUPT, detail=f"entry {name} produced more data than declared"
            )
        raise UnsafeArchive(detail=f"entry {name} produced more data than its declared size (forged header)")
    if produced > limits.max_entry_uncompressed:
        raise LimitExceeded(detail=f"entry {name} exceeds per-entry limit")
    if actual_total > limits.max_total_uncompressed:
        raise LimitExceeded(detail="total extracted size exceeds limit")
    if produced >= RATIO_GRACE and produced > limits.max_ratio * max(stream.consumed, 1):
        raise LimitExceeded(detail=f"entry {name} exceeds compression ratio {limits.max_ratio}")


def _final_checks(zi, stream: _Stream, produced: int, crc: int) -> None:
    name = _safe_repr(zi.orig_filename)
    crc_applies = not _is_aes(zi) or getattr(zi, "wz_aes_version", None) != 2 or zi.CRC != 0
    if produced != zi.file_size or (crc_applies and crc != zi.CRC):
        what = "size" if produced != zi.file_size else "CRC"
        if stream.zipcrypto:
            raise WrongPassword(
                _MSG_WRONG_OR_CORRUPT, detail=f"{what} mismatch for {name}: wrong password or corrupt"
            )
        raise ArchiveCorrupt(detail=f"{what} mismatch for {name}")


def _create_private(path: Path) -> int:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        return os.open(path, flags, 0o600)
    except OSError as exc:
        raise WorkspaceError(detail=f"cannot create output file ({exc.strerror})") from None


def _write(out, chunk: bytes) -> None:
    try:
        out.write(chunk)
    except OSError as exc:
        if exc.errno in (errno.ENOSPC, getattr(errno, "EDQUOT", -1)):
            raise DiskSpaceError(detail="disk full while extracting") from None
        raise WorkspaceError(detail=f"write failed ({exc.strerror})") from None


def _flush(out) -> None:
    try:
        out.flush()
    except OSError as exc:
        if exc.errno in (errno.ENOSPC, getattr(errno, "EDQUOT", -1)):
            raise DiskSpaceError(detail="disk full while extracting") from None
        raise WorkspaceError(detail=f"write failed ({exc.strerror})") from None


def _unlink_quiet(p: Path) -> None:
    for attempt in range(5):
        try:
            p.unlink(missing_ok=True)
            return
        except PermissionError:  # Windows: AV scanners / indexers may hold the handle briefly
            time.sleep(0.05 * (attempt + 1))
        except OSError:
            return


# ---------------------------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------------------------


def _owned_private_dir(p: Path) -> bool:
    try:
        st = os.lstat(p)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        return False
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        return False
    return True


def secure_workspace(base: Path | None = None) -> Path:
    """Create a fresh private (0o700) working directory and return its path."""
    default = base is None
    root = Path(tempfile.gettempdir()) / "mirza2nexa-work" if base is None else Path(base)
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not _owned_private_dir(root):
            if not default:
                raise WorkspaceError(detail="workspace base is not a directory owned by the current user")
            # Shared temp dir hijack attempt (symlink / foreign owner): fall back to a fresh dir.
            return Path(tempfile.mkdtemp(prefix="mirza2nexa-"))
        if os.name == "posix":
            os.chmod(root, 0o700)
        return Path(tempfile.mkdtemp(prefix="job-", dir=root))
    except OSError as exc:
        raise WorkspaceError(detail=f"cannot create workspace ({exc.strerror})") from None


def cleanup_workspace(path: Path) -> None:
    """Best-effort recursive delete (no overwriting). Retries on Windows sharing violations."""
    path = Path(path)
    try:
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
            return
    except OSError:
        return

    def _onexc(func, p, exc):  # make read-only files deletable on Windows, then retry once
        if isinstance(exc, FileNotFoundError):
            return
        try:
            os.chmod(p, stat.S_IWRITE | stat.S_IREAD | (stat.S_IEXEC if os.path.isdir(p) else 0))
            func(p)
        except FileNotFoundError:
            return

    for attempt in range(6):
        if not path.exists():
            return
        try:
            shutil.rmtree(path, onexc=_onexc)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            time.sleep(0.1 * (attempt + 1))
        except OSError:
            time.sleep(0.1 * (attempt + 1))

"""Streaming, deterministic ``.nxpkg`` writer.

Usage::

    w = PackageWriter(staging_dir)
    with w.open_jsonl("records/customers.jsonl") as j:
        for rec in rows:
            j.write(rec)
    w.write_json("reports/reconciliation.json", report)
    result = w.finalize(out_path, passphrase=pw, manifest={...})

Memory stays bounded: records are written to disk as they are produced; at ``finalize`` the
deterministic ZIP is streamed straight into the STREAM encryptor (no full payload in memory
and no plaintext ZIP on disk).
"""

from __future__ import annotations

import errno
import hashlib
import os
import secrets
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import CONVERTER_NAME, __version__
from ..errors import DiskSpaceError
from ..progress import CancelToken, ProgressFn
from .canonical import canonical_json
from .crypto import (
    DEFAULT_CHUNK_SIZE,
    KEY_LEN,
    MAX_CHUNK_SIZE,
    MIN_CHUNK_SIZE,
    MIN_PASSPHRASE_LEN,
    NONCE_PREFIX_LEN,
    SALT_LEN,
    SCRYPT_DEFAULT_N,
    SCRYPT_MAX_N,
    SCRYPT_MIN_N,
    EncryptingWriter,
    build_header,
    derive_keys,
    encode_header,
    parse_key_file,
    scrypt_kdf,
)
from .manifest import (
    CHECKSUMS_NAME,
    MANIFEST_NAME,
    PAYLOAD_FORMAT,
    RESERVED_NAMES,
    validate_caller_manifest,
    validate_manifest,
    validate_relpath,
)

ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
ZIP_FILE_MODE = 0o100644
IO_BLOCK = 1 << 20


@dataclass(frozen=True)
class FileEntry:
    sha256: str
    size: int
    records: int | None

    def to_dict(self) -> dict[str, Any]:
        return {"sha256": self.sha256, "size": self.size, "records": self.records}


@dataclass(frozen=True)
class PackageResult:
    path: Path
    size: int
    payload_sha256: str
    manifest: dict


class JsonlWriter:
    """Appends one canonical JSON object per line; tracks record count, size and SHA-256."""

    def __init__(self, owner: PackageWriter, relpath: str, path: Path) -> None:
        self._owner = owner
        self.relpath = relpath
        self.path = path
        self._fp = open(path, "wb")  # noqa: SIM115 - closed in close()
        self._hash = hashlib.sha256()
        self.count = 0
        self.size = 0
        self.closed = False

    def write(self, obj: Any) -> None:
        if self.closed:
            raise ValueError("JsonlWriter is closed")
        if not isinstance(obj, dict):
            raise TypeError("JSONL records must be objects (dict)")
        line = canonical_json(obj) + b"\n"
        self._fp.write(line)
        self._hash.update(line)
        self.size += len(line)
        self.count += 1

    @property
    def sha256(self) -> str:
        return self._hash.hexdigest()

    def close(self) -> None:
        if self.closed:
            return
        self._fp.close()
        self.closed = True
        self._owner._register(self.relpath, FileEntry(self.sha256, self.size, self.count))

    def __enter__(self) -> JsonlWriter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class PackageWriter:
    def __init__(self, staging_dir: Path) -> None:
        self.staging_dir = Path(staging_dir)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self._tracked: dict[str, tuple[FileEntry, int, int]] = {}  # relpath -> (entry, size, mtime_ns)
        self._open: dict[str, JsonlWriter] = {}

    # ------------------------------------------------------------------ staging
    def path_for(self, relpath: str) -> Path:
        """Validated absolute path inside the staging dir (parents created) for custom files."""
        validate_relpath(relpath)
        if relpath in RESERVED_NAMES:
            raise ValueError(f"{relpath} is reserved and written by finalize()")
        p = self.staging_dir.joinpath(*relpath.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def open_jsonl(self, relpath: str) -> JsonlWriter:
        if relpath in self._open:
            raise ValueError(f"{relpath} is already open")
        w = JsonlWriter(self, relpath, self.path_for(relpath))
        self._open[relpath] = w
        return w

    def write_json(self, relpath: str, obj: Any) -> None:
        data = canonical_json(obj)
        p = self.path_for(relpath)
        p.write_bytes(data)
        self._register(relpath, FileEntry(hashlib.sha256(data).hexdigest(), len(data), None))

    def _register(self, relpath: str, entry: FileEntry) -> None:
        self._open.pop(relpath, None)
        st = self.staging_dir.joinpath(*relpath.split("/")).stat()
        self._tracked[relpath] = (entry, st.st_size, st.st_mtime_ns)

    # ------------------------------------------------------------------ scanning
    def _scan(self) -> list[tuple[str, Path]]:
        found: list[tuple[str, Path]] = []
        root = self.staging_dir
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames.sort()
            for d in dirnames:
                if os.path.islink(os.path.join(dirpath, d)):
                    raise ValueError("symlinks are not allowed in the staging dir")
            for fn in sorted(filenames):
                full = Path(dirpath) / fn
                rel = full.relative_to(root).as_posix()
                if rel in RESERVED_NAMES:
                    continue  # regenerated by finalize
                st = os.lstat(full)
                if not stat.S_ISREG(st.st_mode):
                    raise ValueError(f"not a regular file in staging dir: {rel}")
                validate_relpath(rel)
                found.append((rel, full))
        found.sort(key=lambda t: t[0])
        return found

    @staticmethod
    def _hash_file(path: Path, *, count_lines: bool, cancel: CancelToken | None, tick) -> FileEntry:
        h = hashlib.sha256()
        size = 0
        lines = 0
        last = b""
        with open(path, "rb") as fp:
            while True:
                if cancel is not None:
                    cancel.check()
                buf = fp.read(IO_BLOCK)
                if not buf:
                    break
                h.update(buf)
                size += len(buf)
                if count_lines:
                    lines += buf.count(b"\n")
                last = buf[-1:]
                tick(len(buf))
        if count_lines and size and last != b"\n":
            raise ValueError(f"JSONL file must end with a newline: {path.name}")
        return FileEntry(h.hexdigest(), size, lines if count_lines else None)

    # ------------------------------------------------------------------ finalize
    def finalize(
        self,
        out_path: Path,
        *,
        passphrase: str | None = None,
        key: bytes | str | None = None,
        manifest: dict,
        cancel: CancelToken | None = None,
        progress: ProgressFn | None = None,
        # --- injection points for tests / reproducibility (keep defaults in production)
        kdf_n: int = SCRYPT_DEFAULT_N,
        salt: bytes | None = None,
        nonce_prefix: bytes | None = None,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> PackageResult:
        out_path = Path(out_path)
        if self._open:
            raise ValueError(f"unclosed JSONL writers: {sorted(self._open)}")
        if (passphrase is None) == (key is None):
            raise ValueError("exactly one of passphrase or key must be given")
        if not (MIN_CHUNK_SIZE <= chunk_size <= MAX_CHUNK_SIZE):
            raise ValueError("chunk_size out of range")
        validate_caller_manifest(manifest)

        if passphrase is not None:
            if len(passphrase) < MIN_PASSPHRASE_LEN:
                raise ValueError(f"passphrase must be at least {MIN_PASSPHRASE_LEN} characters")
            if not (SCRYPT_MIN_N <= kdf_n <= SCRYPT_MAX_N and kdf_n & (kdf_n - 1) == 0):
                raise ValueError("kdf_n out of range")
            salt = salt if salt is not None else secrets.token_bytes(SALT_LEN)
            if len(salt) != SALT_LEN:
                raise ValueError("salt must be 16 bytes")
            kdf = scrypt_kdf(salt, n=kdf_n)
            raw_key = None
        else:
            raw_key = parse_key_file(key) if isinstance(key, str) else bytes(key)  # type: ignore[arg-type]
            if len(raw_key) != KEY_LEN:
                raise ValueError("key must be 32 bytes")
            kdf = {"name": "raw-key"}
        nonce_prefix = nonce_prefix if nonce_prefix is not None else secrets.token_bytes(NONCE_PREFIX_LEN)
        if len(nonce_prefix) != NONCE_PREFIX_LEN:
            raise ValueError("nonce_prefix must be 7 bytes")

        prog = _Progress(progress)

        # 1. checksums of every staged content file
        files = self._scan()
        entries: dict[str, FileEntry] = {}
        untracked: list[tuple[str, Path]] = []
        for rel, full in files:
            t = self._tracked.get(rel)
            st = full.stat()
            if t is not None and t[1] == st.st_size and t[2] == st.st_mtime_ns:
                entries[rel] = t[0]
            else:
                untracked.append((rel, full))
        content_total = sum(full.stat().st_size for _, full in files)
        prog.total = content_total + sum(full.stat().st_size for _, full in untracked)
        for rel, full in untracked:
            entries[rel] = self._hash_file(
                full, count_lines=rel.endswith(".jsonl"), cancel=cancel, tick=prog.add
            )

        # 2. manifest.json + checksums.json
        final_manifest = dict(manifest)
        final_manifest["files"] = {rel: entries[rel].to_dict() for rel in sorted(entries)}
        final_manifest["payload_format"] = PAYLOAD_FORMAT
        validate_manifest(final_manifest)  # self-check with the reader's rules
        manifest_bytes = canonical_json(final_manifest)
        (self.staging_dir / MANIFEST_NAME).write_bytes(manifest_bytes)
        all_entries = dict(entries)
        all_entries[MANIFEST_NAME] = FileEntry(
            hashlib.sha256(manifest_bytes).hexdigest(), len(manifest_bytes), None
        )
        checksums = {
            "algorithm": "sha256",
            "files": {rel: all_entries[rel].to_dict() for rel in sorted(all_entries)},
        }
        checksums_bytes = canonical_json(checksums)
        (self.staging_dir / CHECKSUMS_NAME).write_bytes(checksums_bytes)
        prog.total += len(manifest_bytes) + len(checksums_bytes)

        zip_members = sorted(
            [(rel, full) for rel, full in files]
            + [
                (MANIFEST_NAME, self.staging_dir / MANIFEST_NAME),
                (CHECKSUMS_NAME, self.staging_dir / CHECKSUMS_NAME),
            ],
            key=lambda t: t[0],
        )

        # 3. key schedule + header
        keys = derive_keys(kdf, nonce_prefix, passphrase=passphrase, key=raw_key)
        header = build_header(
            kdf=kdf,
            key_check=keys.key_check,
            nonce_prefix=nonce_prefix,
            chunk_size=chunk_size,
            created_by={"name": CONVERTER_NAME, "version": __version__},
        )
        header_prefix = encode_header(header)

        # 4. stream deterministic zip -> encryptor -> temp file -> atomic rename
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{out_path.name}.", suffix=".partial", dir=out_path.parent)
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as raw:
                enc = EncryptingWriter(
                    raw, header_prefix, keys.enc_key, nonce_prefix, chunk_size, cancel=cancel
                )
                write_deterministic_zip(enc, zip_members, cancel=cancel, tick=prog.add)
                enc.close()
                raw.flush()
                os.fsync(raw.fileno())
            os.replace(tmp, out_path)
        except OSError as e:
            _unlink_quiet(tmp)
            if e.errno == errno.ENOSPC:
                raise DiskSpaceError(detail="no space left while writing package") from None
            raise
        except BaseException:
            _unlink_quiet(tmp)
            raise
        return PackageResult(
            path=out_path,
            size=out_path.stat().st_size,
            payload_sha256=enc.plaintext_sha256,
            manifest=final_manifest,
        )


def write_deterministic_zip(sink: Any, members: list[tuple[str, Path]], *, cancel=None, tick=None) -> None:
    """Write ``members`` (sorted ``(arcname, path)``) as a deterministic ZIP into ``sink``.

    Fixed: timestamps 1980-01-01 00:00:00, create_system=3 (Unix), mode 0644, DEFLATE at the
    zlib default level, no comments/extras, entry order as given (callers pass sorted).
    """
    with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for arcname, path in members:
            zi = zipfile.ZipInfo(arcname, date_time=ZIP_EPOCH)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.create_system = 3
            zi.external_attr = ZIP_FILE_MODE << 16
            zi.file_size = path.stat().st_size  # lets zipfile decide on ZIP64 up front
            with open(path, "rb") as src, zf.open(zi, "w") as dst:
                while True:
                    if cancel is not None:
                        cancel.check()
                    buf = src.read(IO_BLOCK)
                    if not buf:
                        break
                    dst.write(buf)
                    if tick is not None:
                        tick(len(buf))


class _Progress:
    def __init__(self, fn: ProgressFn | None) -> None:
        self.fn = fn
        self.done = 0
        self.total = 0

    def add(self, n: int) -> None:
        self.done += n
        if self.fn is not None:
            self.fn(self.done, self.total)


def _unlink_quiet(p: Path) -> None:
    try:
        p.unlink()
    except FileNotFoundError:
        pass

"""Streaming ``.nxpkg`` reader / verifier.

``verify()`` authenticates *everything* before any content is exposed:

1. header structure (+ canonical encoding), container/format version;
2. key check (wrong key/passphrase is reported as ``package.wrong_key``);
3. every STREAM chunk tag, counter order and the final-chunk flag (truncation, reordering,
   appending and trailing bytes all fail) — plaintext ZIP goes to ``workdir`` and is
   deleted again if anything fails;
4. manifest version compatibility, ``checksums.json`` vs. every ZIP entry (sha256, size,
   JSONL record count), manifest ``files`` vs. ``checksums.json``, manifest structure.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..errors import PackageError, PackageTampered
from ..progress import CancelToken, ProgressFn
from .canonical import loads_strict
from .crypto import (
    WrongPackageKey,
    b64d,
    derive_keys,
    iter_decrypt,
    parse_key_file,
    read_header_from,
)
from .manifest import (
    CHECKSUMS_NAME,
    MANIFEST_NAME,
    PAYLOAD_FORMAT,
    SEMVER_RE,
    SUPPORTED_SCHEMA_MAJOR,
    UnsupportedPackageVersion,
    validate_file_entries,
    validate_manifest,
    validate_relpath,
)

PAYLOAD_NAME = "payload.zip"
MAX_META_JSON = 64 * 1024 * 1024  # manifest.json / checksums.json / read_json() bound
MAX_ENTRIES = 100_000
IO_BLOCK = 1 << 20


@dataclass(frozen=True)
class VerifyReport:
    header: dict
    manifest: dict
    payload_sha256: str
    payload_size: int
    chunks: int
    files: dict = field(default_factory=dict)

    @property
    def source_fingerprint(self) -> str:
        return self.manifest["source_fingerprint"]

    @property
    def import_id(self) -> str:
        return self.manifest["import_id"]


class PackageReader:
    @staticmethod
    def read_header(path: Path) -> dict:
        """Parse and validate the public header (no key needed; contains no customer data)."""
        with open(path, "rb") as fp:
            header, _ = read_header_from(fp)
        return header

    def __init__(
        self,
        path: Path,
        *,
        passphrase: str | None = None,
        key: bytes | str | None = None,
        workdir: Path,
        cancel: CancelToken | None = None,
        progress: ProgressFn | None = None,
    ) -> None:
        self.path = Path(path)
        self.passphrase = passphrase
        self.key = parse_key_file(key) if isinstance(key, str) else key
        self.workdir = Path(workdir)
        self.cancel = cancel
        self.progress = progress
        self._report: VerifyReport | None = None

    @property
    def payload_path(self) -> Path:
        return self.workdir / PAYLOAD_NAME

    # ------------------------------------------------------------------ verify
    def verify(self) -> VerifyReport:
        self._report = None
        self.workdir.mkdir(parents=True, exist_ok=True)
        total_in = self.path.stat().st_size
        partial = self.workdir / (PAYLOAD_NAME + ".partial")
        h = hashlib.sha256()
        size = 0
        chunks = 0
        try:
            with open(self.path, "rb") as fp:
                header, prefix = read_header_from(fp)
                kdf = header["kdf"]
                nonce_prefix = b64d(header["nonce_prefix"])
                keys = derive_keys(kdf, nonce_prefix, passphrase=self.passphrase, key=self.key)
                if not hmac.compare_digest(keys.key_check, b64d(header["key_check"])):
                    raise WrongPackageKey(
                        detail="key check mismatch (wrong key/passphrase or altered header)"
                    )
                with open(partial, "wb") as out:
                    for pt in iter_decrypt(
                        fp, prefix, keys.enc_key, nonce_prefix, header["chunk_size"], cancel=self.cancel
                    ):
                        out.write(pt)
                        h.update(pt)
                        size += len(pt)
                        chunks += 1
                        if self.progress is not None:
                            self.progress(fp.tell(), total_in)
            os.replace(partial, self.payload_path)
        except BaseException:
            _unlink_quiet(partial)
            _unlink_quiet(self.payload_path)
            raise

        try:
            manifest, checksums = self._verify_payload(total_in)
        except BaseException:
            _unlink_quiet(self.payload_path)
            raise
        self._report = VerifyReport(
            header=header,
            manifest=manifest,
            payload_sha256=h.hexdigest(),
            payload_size=size,
            chunks=chunks,
            files=checksums["files"],
        )
        return self._report

    def _verify_payload(self, base_done: int) -> tuple[dict, dict]:
        try:
            zf = zipfile.ZipFile(self.payload_path, "r")
        except (zipfile.BadZipFile, OSError, ValueError):
            raise PackageTampered(detail="payload is not a valid ZIP") from None
        with zf:
            infos = zf.infolist()
            if len(infos) > MAX_ENTRIES:
                raise PackageTampered(detail="too many entries")
            by_name: dict[str, zipfile.ZipInfo] = {}
            for zi in infos:
                if zi.filename in by_name:
                    raise PackageTampered(detail="duplicate ZIP entry")
                try:
                    validate_relpath(zi.filename)
                except ValueError:
                    raise PackageTampered(detail="unsafe ZIP entry name") from None
                if zi.is_dir() or zi.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    raise PackageTampered(detail="unexpected ZIP entry type")
                by_name[zi.filename] = zi
            for req in (MANIFEST_NAME, CHECKSUMS_NAME):
                if req not in by_name:
                    raise PackageTampered(detail=f"{req} missing")

            # Version gate first so a future-format package yields "unsupported", not "tampered".
            manifest = self._load_json_entry(zf, by_name[MANIFEST_NAME])
            if not isinstance(manifest, dict):
                raise PackageTampered(detail="manifest is not an object")
            v = manifest.get("package_schema_version")
            if not isinstance(v, str) or not SEMVER_RE.match(v):
                raise PackageError(code="package.invalid_manifest", detail="package_schema_version")
            if int(v.split(".", 1)[0]) != SUPPORTED_SCHEMA_MAJOR:
                raise UnsupportedPackageVersion(detail=f"package_schema_version {v}")
            if manifest.get("payload_format") != PAYLOAD_FORMAT:
                raise UnsupportedPackageVersion(detail="payload_format")

            checksums = self._load_json_entry(zf, by_name[CHECKSUMS_NAME])
            if not (isinstance(checksums, dict) and set(checksums) == {"algorithm", "files"}):
                raise PackageTampered(detail="checksums.json structure")
            if checksums["algorithm"] != "sha256":
                raise PackageTampered(detail="checksums algorithm")
            validate_file_entries(checksums["files"], err=PackageTampered)
            listed = set(checksums["files"])
            if CHECKSUMS_NAME in listed or MANIFEST_NAME not in listed:
                raise PackageTampered(detail="checksums.json file list")
            if set(by_name) != listed | {CHECKSUMS_NAME}:
                raise PackageTampered(detail="ZIP entries do not match checksums.json")

            total = sum(e["size"] for e in checksums["files"].values())
            done = 0
            for name in sorted(listed):
                entry = checksums["files"][name]
                zi = by_name[name]
                if zi.file_size != entry["size"]:
                    raise PackageTampered(detail=f"size mismatch: {name}")
                digest, n, lines, last = _hash_entry(zf, zi, self.cancel)
                if n != entry["size"] or digest != entry["sha256"]:
                    raise PackageTampered(detail=f"checksum mismatch: {name}")
                if name.endswith(".jsonl"):
                    if n and last != b"\n":
                        raise PackageTampered(detail=f"JSONL not newline-terminated: {name}")
                    if entry["records"] != lines:
                        raise PackageTampered(detail=f"record count mismatch: {name}")
                done += n
                if self.progress is not None:
                    self.progress(base_done + done, base_done + total)

            validate_manifest(manifest)
            expected = {k: v for k, v in checksums["files"].items() if k != MANIFEST_NAME}
            if manifest["files"] != expected:
                raise PackageTampered(detail="manifest files differ from checksums.json")
        return manifest, checksums

    @staticmethod
    def _load_json_entry(zf: zipfile.ZipFile, zi: zipfile.ZipInfo) -> Any:
        if zi.file_size > MAX_META_JSON:
            raise PackageTampered(detail=f"{zi.filename} too large")
        try:
            with zf.open(zi) as fp:
                data = fp.read(MAX_META_JSON + 1)
            return loads_strict(data)
        except (zipfile.BadZipFile, ValueError, UnicodeDecodeError, EOFError):
            raise PackageTampered(detail=f"{zi.filename} unreadable") from None

    # ------------------------------------------------------------------ content access
    def _ensure_verified(self) -> VerifyReport:
        if self._report is None or not self.payload_path.exists():
            return self.verify()
        return self._report

    def _entry(self, relpath: str) -> dict:
        report = self._ensure_verified()
        validate_relpath(relpath)
        if relpath not in report.files:
            raise PackageError(code="package.missing_file", detail=f"{relpath} not in package")
        return report.files[relpath]

    def iter_jsonl(self, relpath: str) -> Iterator[dict]:
        """Yield records of a JSONL file. The file's hash is re-checked at EOF (defence against
        local modification of the plaintext in ``workdir`` after ``verify()``)."""
        entry = self._entry(relpath)
        h = hashlib.sha256()
        with zipfile.ZipFile(self.payload_path, "r") as zf, zf.open(relpath) as fp:
            for line in fp:
                if self.cancel is not None:
                    self.cancel.check()
                h.update(line)
                try:
                    obj = loads_strict(line)
                except (ValueError, UnicodeDecodeError):
                    raise PackageError(
                        code="package.invalid_record", detail=f"bad JSON line in {relpath}"
                    ) from None
                if not isinstance(obj, dict):
                    raise PackageError(
                        code="package.invalid_record", detail=f"non-object record in {relpath}"
                    )
                yield obj
        if h.hexdigest() != entry["sha256"]:
            raise PackageTampered(detail=f"{relpath} changed after verification")

    def read_json(self, relpath: str) -> Any:
        entry = self._entry(relpath)
        if entry["size"] > MAX_META_JSON:
            raise PackageError(code="package.too_large", detail=f"{relpath} too large for read_json")
        with zipfile.ZipFile(self.payload_path, "r") as zf:
            data = zf.read(relpath)
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise PackageTampered(detail=f"{relpath} changed after verification")
        try:
            return loads_strict(data)
        except (ValueError, UnicodeDecodeError):
            raise PackageError(code="package.invalid_record", detail=f"bad JSON in {relpath}") from None

    def cleanup(self) -> None:
        """Delete the decrypted payload from ``workdir``."""
        _unlink_quiet(self.payload_path)
        self._report = None


def _hash_entry(
    zf: zipfile.ZipFile, zi: zipfile.ZipInfo, cancel: CancelToken | None
) -> tuple[str, int, int, bytes]:
    h = hashlib.sha256()
    n = 0
    lines = 0
    last = b""
    try:
        with zf.open(zi) as fp:
            while True:
                if cancel is not None:
                    cancel.check()
                buf = fp.read(IO_BLOCK)
                if not buf:
                    break
                h.update(buf)
                n += len(buf)
                lines += buf.count(b"\n")
                last = buf[-1:]
                if n > zi.file_size:
                    raise PackageTampered(detail="entry larger than declared")
    except (zipfile.BadZipFile, EOFError, OSError, ValueError) as e:  # CRC errors, corrupt deflate
        raise PackageTampered(detail=f"corrupt ZIP entry {zi.filename}") from e
    return h.hexdigest(), n, lines, last


def _unlink_quiet(p: Path) -> None:
    try:
        p.unlink()
    except FileNotFoundError:
        pass

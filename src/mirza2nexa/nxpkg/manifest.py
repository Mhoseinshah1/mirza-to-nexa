"""Manifest / checksums contract of the inner payload (``payload_format = "zip-v1"``)."""

from __future__ import annotations

import re
from typing import Any

from ..errors import PackageError, PackageTampered
from .crypto import UnsupportedPackageVersion

PACKAGE_SCHEMA = "nexa.migration.mirza"
PACKAGE_SCHEMA_VERSION = "1.3.0"
SUPPORTED_SCHEMA_MAJOR = 1
PAYLOAD_FORMAT = "zip-v1"
MIN_IMPORTER_VERSION = "1.0.0"
TARGET = "nexa"

MANIFEST_NAME = "manifest.json"
CHECKSUMS_NAME = "checksums.json"
RESERVED_NAMES = frozenset({MANIFEST_NAME, CHECKSUMS_NAME})

SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
_SEG = r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}"
RELPATH_RE = re.compile(rf"^{_SEG}(?:/{_SEG}){{0,7}}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

CALLER_FIELDS = (
    "package_schema",
    "package_schema_version",
    "import_id",
    "source_fingerprint",
    "converter",
    "created_at",
    "compatibility",
)
WRITER_FIELDS = ("files", "payload_format")


def validate_relpath(relpath: str) -> str:
    """Accept only simple relative POSIX paths (no ``..``, no absolute, no backslash)."""
    if not isinstance(relpath, str) or not RELPATH_RE.match(relpath):
        raise ValueError(f"invalid package path: {relpath!r}")
    if any(seg in (".", "..") or seg.endswith(".") for seg in relpath.split("/")):
        raise ValueError(f"invalid package path: {relpath!r}")
    return relpath


def semver_major(v: str) -> int:
    m = SEMVER_RE.match(v)
    if not m:
        raise ValueError(f"not a semver string: {v!r}")
    return int(m.group(1))


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validate_file_entries(files: Any, *, err: type[Exception]) -> None:
    if not isinstance(files, dict):
        raise err(detail="files must be an object")
    for name, entry in files.items():
        try:
            validate_relpath(name)
        except ValueError:
            raise err(detail="invalid file path in file list") from None
        if not isinstance(entry, dict) or set(entry) != {"sha256", "size", "records"}:
            raise err(detail=f"bad file entry for {name}")
        if not (isinstance(entry["sha256"], str) and SHA256_RE.match(entry["sha256"])):
            raise err(detail=f"bad sha256 for {name}")
        if not (_is_int(entry["size"]) and entry["size"] >= 0):
            raise err(detail=f"bad size for {name}")
        rec = entry["records"]
        if rec is not None and not (_is_int(rec) and rec >= 0):
            raise err(detail=f"bad record count for {name}")


def validate_caller_manifest(manifest: dict) -> None:
    """Writer-side check of the fields the caller supplies (raises ValueError)."""
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be a dict")
    for f in WRITER_FIELDS:
        if f in manifest:
            raise ValueError(f"manifest field {f!r} is set by the writer")
    try:
        _validate_common(manifest)
    except PackageError as e:
        raise ValueError(e.detail) from None


def _validate_common(m: dict) -> None:
    def bad(why: str) -> PackageError:
        return PackageError(code="package.invalid_manifest", detail=f"manifest: {why}")

    missing = [f for f in CALLER_FIELDS if f not in m]
    if missing:
        raise bad(f"missing fields {missing}")
    if m["package_schema"] != PACKAGE_SCHEMA:
        raise bad("package_schema")
    v = m["package_schema_version"]
    if not isinstance(v, str) or not SEMVER_RE.match(v):
        raise bad("package_schema_version must be semver")
    if semver_major(v) != SUPPORTED_SCHEMA_MAJOR:
        raise UnsupportedPackageVersion(detail=f"package_schema_version {v}")
    for f in ("import_id", "source_fingerprint", "created_at"):
        if not isinstance(m[f], str) or not m[f]:
            raise bad(f"{f} must be a non-empty string")
    conv = m["converter"]
    if not (
        isinstance(conv, dict) and isinstance(conv.get("name"), str) and isinstance(conv.get("version"), str)
    ):
        raise bad("converter must be {name, version}")
    comp = m["compatibility"]
    if not isinstance(comp, dict) or comp.get("target") != TARGET:
        raise bad("compatibility.target")
    miv = comp.get("min_importer_version")
    if not isinstance(miv, str) or not SEMVER_RE.match(miv):
        raise bad("compatibility.min_importer_version")


def validate_manifest(m: Any) -> None:
    """Reader-side validation of the full manifest (after authentication)."""
    if not isinstance(m, dict):
        raise PackageError(code="package.invalid_manifest", detail="manifest must be an object")
    _validate_common(m)
    if m.get("payload_format") != PAYLOAD_FORMAT:
        raise UnsupportedPackageVersion(detail=f"payload_format {m.get('payload_format')!r}")
    if "files" not in m:
        raise PackageError(code="package.invalid_manifest", detail="manifest: files missing")
    validate_file_entries(m["files"], err=PackageTampered)
    if RESERVED_NAMES & set(m["files"]):
        raise PackageTampered(detail="manifest lists reserved file")

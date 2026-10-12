"""``.nxpkg`` container: authenticated-encrypted, versioned, deterministic migration package.

See ``docs/NXPKG_FORMAT.md`` for the byte layout and trust model.
"""

from .canonical import canonical_json, loads_strict
from .crypto import (
    InvalidKeyFile,
    UnsupportedPackageVersion,
    WrongPackageKey,
    encode_key_file,
    generate_key_file,
    generate_passphrase,
    parse_key_file,
)
from .manifest import MIN_IMPORTER_VERSION, PACKAGE_SCHEMA, PACKAGE_SCHEMA_VERSION, PAYLOAD_FORMAT
from .reader import PackageReader, VerifyReport
from .writer import JsonlWriter, PackageResult, PackageWriter

__all__ = [
    "MIN_IMPORTER_VERSION",
    "PACKAGE_SCHEMA",
    "PACKAGE_SCHEMA_VERSION",
    "PAYLOAD_FORMAT",
    "InvalidKeyFile",
    "JsonlWriter",
    "PackageReader",
    "PackageResult",
    "PackageWriter",
    "UnsupportedPackageVersion",
    "VerifyReport",
    "WrongPackageKey",
    "canonical_json",
    "encode_key_file",
    "generate_key_file",
    "generate_passphrase",
    "loads_strict",
    "parse_key_file",
]

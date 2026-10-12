# `.nxpkg` container format, version 1

Status: **proposed contract**. The NEXA importer has not consumed it yet, so this file must
not be described as "importable in production" until a versioned importer exists and is
tested against it.

Implementation: `src/mirza2nexa/nxpkg/` (writer, reader, crypto, manifest, canonical JSON).
Tests: `tests/test_nxpkg.py`.

## 1. Overview

```
+-------------------------------------------------------------------+
| MAGIC "NXPKG\x00" (6) | MAJOR=0x01 (1) | HLEN (4, BE) | HEADER    |  public, no secrets
+-------------------------------------------------------------------+
| LEN_0 (4, BE) | CT_0 = AES-256-GCM(chunk 0) || tag(16)            |
| LEN_1 (4, BE) | CT_1                                              |  STREAM body
| ...                                                               |
| LEN_N (4, BE) | CT_N   (last flag = 1)                            |
+-------------------------------------------------------------------+
               plaintext of all chunks = PAYLOAD (deterministic ZIP)
```

All integers are unsigned big-endian. There are no bytes after the final chunk.

## 2. Header

| Offset | Size | Field |
| --- | --- | --- |
| 0 | 6 | magic `4E 58 50 4B 47 00` (`"NXPKG\0"`) |
| 6 | 1 | container major version, `0x01` |
| 7 | 4 | `HLEN`, length of the header JSON, 1 to 16384 |
| 11 | HLEN | header JSON (canonical, see section 7) |

The header JSON has **exactly** these keys (unknown keys are rejected in v1):

| Key | Value |
| --- | --- |
| `format` | `"nxpkg"` |
| `format_version` | `1` (must equal the major byte) |
| `cipher` | `"AES-256-GCM-STREAM"` |
| `chunk_size` | plaintext bytes per non-final chunk; default `1048576` (1 MiB); reader accepts 4 KiB to 16 MiB |
| `kdf` | `{"name":"scrypt","n":131072,"r":8,"p":1,"salt":"<b64 16 bytes>"}` or `{"name":"raw-key"}` |
| `key_check` | standard base64 of 16 bytes (section 4) |
| `nonce_prefix` | standard base64 of 7 random bytes |
| `created_by` | `{"name": "mirza2nexa", "version": "<converter version>"}` |

The header holds no customer data, no import id, no manifest content and no key material.
The reader requires the header bytes to be exactly the canonical re-encoding of the parsed
object, so it has a single valid byte representation.

`HEADER_BYTES` = bytes `0 .. 11+HLEN` (magic, major, length and JSON).
`AAD` = `SHA-256(HEADER_BYTES)` (32 bytes), used as associated data for **every** chunk.
Changing any header byte therefore makes chunk 0 fail authentication.

## 3. Body: STREAM construction

This follows the online-AE "STREAM" construction (Hoang, Reyhanitabar, Rogaway, Vizar 2015)
on top of AES-256-GCM:

* The payload is split into chunks of exactly `chunk_size` bytes. The final chunk holds the
  remainder, from 0 to `chunk_size` bytes. An empty payload is a single empty final chunk.
* `nonce_i = nonce_prefix (7) || i (4, BE, starting at 0) || last (1)`, where `last` is
  `0x01` for the final chunk and `0x00` for every other chunk. That is 12 bytes in total.
* `CT_i = AES-256-GCM-Encrypt(K_enc, nonce_i, chunk_i, AAD)`, ciphertext followed by the 16-byte tag.
* On disk each chunk is `LEN_i (4, BE) || CT_i`, with `16 <= LEN_i <= chunk_size + 16`.
* At most 2^32 - 1 chunks (4 PiB with 1 MiB chunks).

Reader rules:

* Every non-final chunk must be full size. A short chunk can only be valid as the final chunk.
  A full-size chunk is tried as non-final first and then as final.
* Decrypting chunk `i` with counter `i` binds its position, so **reordering or swapping** chunks fails.
* The `last` flag in the nonce means **truncation** is detected: if the file ends without a
  chunk that authenticates with `last = 1`, it is rejected.
* Any bytes after the final chunk, including an extra copy of a chunk, are rejected as
  **appending**.
* Decrypted output is treated as untrusted until the final chunk has been verified. The
  reader writes it to `workdir/payload.zip.partial` and renames it only on success. On any
  failure the plaintext is deleted.

## 4. Keys

The operator chooses one of two key sources:

**Passphrase** (`kdf.name = "scrypt"`):
`master = scrypt(NFC(passphrase) as UTF-8, salt, N=2^17, r=8, p=1, dkLen=32)` with a random
16-byte salt per package. The reader accepts N from 2^10 to 2^20 (power of two), r from 1 to 32
and p from 1 to 16. Lower N exists only so tests run fast; production writes 2^17. The writer
requires passphrases of at least 12 characters. `generate_passphrase()` produces 6 groups of
5 Crockford-base32 characters (150 bits), for example `7KQ2M-0HZ4X-...`.

**Key file** (`kdf.name = "raw-key"`): `master` = 32 random bytes. The key file text is

```
nxkey1:<base64url-no-padding( key(32) || SHA-256("nxkey1" || key)[0:4] )>
```

(48 characters after the prefix). The 4-byte checksum catches typos and truncation and gives
`package.bad_key_file`. The functions are `generate_key_file()`, `parse_key_file()` and
`encode_key_file()`.

Key schedule (HKDF-SHA256, empty salt):

```
K_enc     = HKDF(master, info = "nxpkg-v1/aes-256-gcm-stream/" || nonce_prefix, L = 32)
K_check   = HKDF(master, info = "nxpkg-v1/key-check",                           L = 32)
key_check = HMAC-SHA256(K_check, "nxpkg-key-check-v1")[0:16]
```

* `K_enc` depends on `nonce_prefix`, so a key file reused across packages still gets a
  separate AES key for each package. A GCM nonce can only repeat if both the 56-bit prefix
  and the key repeat.
* `key_check` lets the reader report **`package.wrong_key`** before any decryption, without
  revealing the key. It is a 128-bit PRF output under an independent subkey. It allows
  offline guessing of the passphrase, but no more than chunk 0 already does, and scrypt
  limits that. Because `key_check` is in the unauthenticated part of the header until chunk 0
  verifies, an altered `kdf`, `salt` or `key_check` field is also reported as `wrong_key`.
  `WrongPackageKey` is a subclass of `PackageTampered`, so callers that treat both as fatal
  only need to catch one.
* The package never stores the passphrase, the key or any value derived from them other than
  `key_check`.

## 5. Payload (`payload_format = "zip-v1"`)

The plaintext is a ZIP archive produced with Python's `zipfile`, written as a stream with
data descriptors (bit 3), and using ZIP64 where a file needs it:

* entries are sorted by name, both content files and `manifest.json` / `checksums.json`;
* each entry has timestamp `1980-01-01 00:00:00`, `create_system = 3` (Unix),
  `external_attr = 0o100644 << 16`, DEFLATE at the zlib default level, and no extra fields
  or comments;
* entry names are relative POSIX paths matching
  `seg(/seg){0,7}` with `seg = [A-Za-z0-9_][A-Za-z0-9_.-]{0,127}`. Names cannot contain
  `..`, a leading `/`, backslashes, empty segments or directory entries.

Typical content:

```
checksums.json
manifest.json
records/*.jsonl          one canonical JSON object per line, '\n'-terminated
reports/*.json, *.jsonl
```

**Determinism.** Given the same staged files, the same secret, the same salt, the same
`nonce_prefix` and the same `chunk_size`, the output is byte-identical. The payload SHA-256
(`PackageResult.payload_sha256` / `VerifyReport.payload_sha256`) does not depend on the key,
salt or nonce. That only holds on the same zlib implementation, since DEFLATE output is not
standardised across zlib forks such as zlib-ng. In production the salt and nonce_prefix are
random, so two packages of the same data differ as ciphertext but have the same
payload hash.

**Streaming.** Callers write records to a staging directory (`PackageWriter.open_jsonl()`
streams to disk). `finalize()` streams the ZIP directly into `EncryptingWriter`, which holds
at most about 2 x `chunk_size` in memory, and from there into a temporary file next to the
output. It then fsyncs and calls `os.replace` on the temporary file. The plaintext ZIP is
never kept in memory or written to disk on the writer side. On cancellation or error the
temporary file is deleted and no output file appears. In the tests, a 50 MB incompressible
JSONL file peaks at about 5 MB of traced Python memory, against an asserted limit of 64 MB.

## 6. `checksums.json` and `manifest.json`

`checksums.json` (canonical JSON):

```json
{"algorithm":"sha256","files":{"<path>":{"records":<int|null>,"sha256":"<hex>","size":<int>}, ...}}
```

It lists **every** ZIP entry except itself, including `manifest.json`. `records` is the line
count for `*.jsonl` and `null` for other files.

`manifest.json` (canonical JSON). The caller supplies:

| Field | Rule |
| --- | --- |
| `package_schema` | `"nexa.migration.mirza"` |
| `package_schema_version` | semver; currently `"1.0.0"`; the reader accepts major `1` only |
| `import_id` | non-empty string |
| `source_fingerprint` | non-empty string (for example a SHA-256 of the source backup) |
| `converter` | `{"name": str, "version": str}` |
| `created_at` | non-empty string, supplied by the caller (for determinism) |
| `compatibility` | `{"min_importer_version": "1.0.0", "target": "nexa"}` |

The writer adds:

| Field | Rule |
| --- | --- |
| `files` | same entries as `checksums.json` minus `manifest.json` |
| `payload_format` | `"zip-v1"` |

The reader checks the following, in order, after the STREAM has authenticated:

1. ZIP structure: entry names are safe, there are no duplicates or directories, compression
   is STORED or DEFLATE only, and there are at most 100,000 entries.
2. Version gate: `package_schema_version` major and `payload_format`. Failure gives
   `package.unsupported_version`, so a future package reads as "unsupported" rather than
   "tampered".
3. `checksums.json` structure. The set of ZIP entries must equal the files it lists plus
   `checksums.json`.
4. For each entry: declared size, streamed SHA-256, size, JSONL record count and newline
   termination. Failure gives `package.tampered`.
5. Full manifest validation, and `manifest.files` equals `checksums.files` without
   `manifest.json`.

`iter_jsonl()` and `read_json()` check the file hash again while reading, as a defence
against local changes to `workdir/payload.zip` after `verify()`. `workdir` should still be
private to the importer.

## 7. Canonical JSON

UTF-8, `sort_keys`, separators `(",", ":")`, `ensure_ascii=False`, and no NaN or Infinity.
**Floats are rejected** with a `TypeError` on write, and the reader rejects float literals as
well. Monetary values must be integers in minor units or decimal strings. `Decimal` values are
encoded as `str(Decimal)`. Allowed types are `dict` (string keys), `list`/`tuple`, `str`,
`int`, `bool`, `None` and `Decimal`.

## 8. Error codes

| Code | Class | Meaning |
| --- | --- | --- |
| `package.not_nxpkg` | `PackageError` | the magic bytes are missing |
| `package.unsupported_version` | `UnsupportedPackageVersion(PackageError)` | container major, header `format_version`, `package_schema_version` major or `payload_format` is not supported |
| `package.wrong_key` | `WrongPackageKey(PackageTampered)` | key check failed: wrong passphrase or key, or the KDF fields of the header were altered |
| `package.tampered` | `PackageTampered` | any authentication or integrity failure (header, chunk tag, order, truncation, trailing data, ZIP, checksums, manifest/checksums mismatch) |
| `package.passphrase_required` / `package.key_required` | `PackageError` | the wrong kind of secret was supplied |
| `package.bad_key_file` | `InvalidKeyFile(PackageError)` | the key file text is malformed or its checksum does not match |
| `package.invalid_manifest` | `PackageError` | the manifest is authentic but structurally invalid |

## 9. Trust model

* **Authenticity comes only from AEAD under a secret** that the operator knows: the
  passphrase or the key file. A hash such as `checksums.json` or the payload SHA-256 is **not**
  authentication on its own, because anyone can recompute a hash. The checksums are
  meaningful only because they are inside the authenticated envelope. They give per-file
  integrity and catch inconsistencies inside the payload.
* The **key or passphrase must be delivered out-of-band**, separately from the `.nxpkg`
  file. For example, the package goes by file transfer and the passphrase is shown on
  screen or sent on a different channel. Never send them together.
* AES-GCM is symmetric, so **anyone who holds the key can also create a valid package**,
  including someone who gets the passphrase. The format proves "made by a holder of this
  secret", not "made by this converter installation". The importer should therefore show
  the operator the `source_fingerprint`, `import_id`, `created_at`, converter name and
  version, and record counts, and ask for explicit confirmation before a dry run or import.
  The operator can compare these with what the converter showed when the package was
  created.
* Confidentiality: everything except the header is encrypted. The header shows only the
  converter name and version, the KDF parameters and the package size, which roughly reveals
  the data volume.
* Out of scope: a compromised operator machine, and a local attacker who changes `workdir`
  while the importer is running (this is partly mitigated by the hash checks repeated in
  `iter_jsonl()` and `read_json()`).

**Future (optional, additive):** an Ed25519 signature from a per-installation converter key
over `SHA-256(HEADER_BYTES) || payload_sha256`. It would be stored as a signed trailer or a
detached `.sig` and would give origin authentication that does not depend on the shared
secret. The importer would pin trusted converter public keys. This would be introduced as a
new optional header field under a new container major version, or as a detached file, so
v1 readers stay strict.

## 10. Versioning

* **Container** (`MAJOR` byte and `format_version`): changes to the byte layout or crypto. A
  reader rejects any major version it does not know.
* **Payload** (`payload_format`): the encoding of the inner archive.
* **Content schema** (`package_schema_version`, semver): a minor or patch version is a
  backward-compatible addition that readers accept, and a major version is breaking and
  rejected. `compatibility.min_importer_version` lets a package require a newer importer.

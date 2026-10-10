# Security model and verified threats

The converter is a local application. It handles a ZIP that cannot be trusted and that contains
customer data. The table lists each threat, the defence against it, and the tests that cover it.

| Threat | Defence | Tests |
|---|---|---|
| Zip Slip (traversal, absolute paths, drive letters, UNC paths, NUL, control characters) | Entry names are validated on the raw bytes; output files get generated names | `tests/test_archive.py` (10+ cases) |
| Symlinks and special files | Unix mode bits in `external_attr` are rejected | `test_archive.py` |
| Zip bombs (high ratio, forged sizes, overlapping entries) | Decompression is capped per call; actual bytes are checked against the declared size, the per-entry and total limits, and the ratio; overlapping data regions are rejected | `test_archive.py` (forged headers, 50 MB bombs) |
| Wrong or missing password, ZipCrypto false positives | Every encrypted SQL entry is verified; a 256 KiB prefix is decompressed; a CRC failure maps to `wrong_password` | `test_archive.py`, `test_server.py` |
| Corrupt or truncated archives | `archive.corrupt` / `archive.invalid`; partial output is deleted | `test_archive.py`, `test_conversion.py` |
| Disk exhaustion | Free space is checked before and during extraction, and ENOSPC is handled; everything is cleaned up on failure | `test_archive.py`, `test_conversion.py::test_disk_full_is_reported` |
| Arbitrary SQL execution | The SQL is **never executed**. A tokenizer reads literal values only; staging uses parameterised inserts into a private SQLite file whose statements are fixed in code | `test_sqldump.py`, design |
| Parser resource exhaustion | Chunked reading; maximum value size and maximum statement size | `test_sqldump.py` (memory tests) |
| Leaking secrets from the dump | `user_info`, `uuid`, `bottype`, `inbounds` and `proxies` are dropped at parse time; the phone number is reduced to a class | `test_conversion.py::test_secret_columns_never_exported` |
| DNS rebinding / attacks from other websites | Host allow-list (`127.0.0.1:<port>`, `localhost:<port>`), Origin check | `test_server.py` |
| CSRF | One-time launch token gives a `HttpOnly; SameSite=Strict` cookie; every write needs an `X-CSRF-Token` header | `test_server.py` |
| XSS from crafted SQL data | Strict CSP (`default-src 'none'`, no inline code); the UI writes server data only with `textContent` | E2E with CSP violation listener |
| Network exposure | The address is hard-coded to `127.0.0.1` and cannot be configured | `__main__.py`, smoke test |
| Package tampering, truncation, reordering, appending | AES-256-GCM STREAM with the final-chunk flag and AAD = hash of the header; a separate key check; `checksums.json` | `test_nxpkg.py` (10+ tampering cases), `test_conversion.py` |
| Key stored with the package | The key file or passphrase is never written into the `.nxpkg`; the generated key exists only in memory for the session | `test_conversion.py::test_key_never_inside_package` |
| Passwords in logs, history or reports | Passwords are only function arguments and are cleared after use; there is no access log; history keeps aggregates only | `test_server.py`, `test_conversion.py` |
| Double credit, debt auto-settlement, history turned into payments | See `docs/MAPPING.md` (idempotency keys, flags, reconciliation) | `test_conversion.py` |
| Unknown schema version | Fails closed: `SchemaUnknown`, or the dependent entities are blocked | `test_conversion.py` |
| Telemetry or exfiltration | There are no outbound requests in the code and no analytics; the CSP forbids external `connect-src` | review + CSP |

## Residual risks and limits

* **Plaintext on disk during processing.** Extracted SQL and the staging SQLite live in the private
  workspace while a job runs. That workspace is mode `0700` on POSIX, or the user's own `%TEMP%` on
  Windows. The SQL is deleted right after parsing (configurable); everything is deleted when the job
  is deleted or the app exits. Secure overwrite is not attempted, because it is ineffective on SSDs.
  Use full-disk encryption (BitLocker).
* **Anyone holding the key can forge a package.** AEAD proves the package was made by a key holder,
  not by this converter. The operator must confirm `source_fingerprint` out-of-band. An Ed25519
  signature is optional future work.
* **Other local processes.** Another process running as the same Windows user can read that user's
  temp files. This is out of scope: a local administrator or the same user is trusted.
* **ZIP formats.** PKWARE strong encryption, Deflate64 and PPMd are rejected as unsupported.

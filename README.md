# Mirza → NEXA Migration Studio

Local-first, offline migration converter for password-protected ZIP archives containing Mirza SQL exports, producing a validated, versioned NEXA migration package.

> **Status: architecture and scaffolding, NOT a working financial migration converter.** Do not use with production data until mapping, reconciliation, importer and security tests pass.

## Boundaries
- Source: a locally selected ZIP containing an SQL backup of Mirza. The archive/password and extracted data never go to GitHub or a remote API.
- Target: a *versioned package* for a separately implemented, authorized NEXA importer. **Never execute untrusted source SQL against the NEXA database.**
- Interface: a local web UI served only on `127.0.0.1`, intended for Windows desktop; no internet dependency for processing.
- Migration: dry-run + review + deterministic manifest first; production import requires separately approved NEXA changes.
- Existing NEXA production and Mirza staging are out of scope for this repository.

## Planned flow
1. Select password-protected ZIP locally; supply password only to the local process.
2. Validate ZIP structure and limits (entry count, uncompressed bytes, paths, decompression ratio), reject unsafe archive entries, and isolate workspace.
3. Parse and inspect the SQL dump in a sandboxed process **without running arbitrary SQL against production**.
4. Discover source schema/version and map customers, opening wallet balances, debts, products, categories, invoice history and existing service records.
5. Produce reconciliation totals, unresolved-record queue and safety blockers. Any ambiguous ownership/amount is **blocked**, not guessed.
6. Export an encrypted versioned `.nxpkg` with canonical manifest, integrity checks, source fingerprints, audit report and import-deduplication identifiers.
7. A **separate** NEXA importer validates, previews and dry-runs the package, then requires explicit operator authorization for actual import.

## Important
- Preserve financial history, immutable payment evidence and original source references.
- Import retries MUST NOT double credit wallets, reissue services or duplicate invoices.
- Source services are never re-provisioned merely by being present in the backup.
- No Mirza-specific NEXA UI or historical data is deleted as part of building this converter; old UI can be retired only after approved replacement and full tests.
- Real backups, ZIP passwords, SQL dumps, wallet records, keys, and generated `.nxpkg` files must **never** be committed to this public repository.

See `docs/ARCHITECTURE_FA.md` and `CLAUDE.md` in the implementation branch.

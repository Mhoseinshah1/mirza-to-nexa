# Mirza → NEXA Migration Studio

Local-first, offline migration converter for password-protected ZIP archives containing Mirza SQL exports, producing a validated, versioned NEXA migration package.

> **Status (v0.2.0, Phase 2): full-schema offline converter.** Every table of a Mirza dump is catalogued
> (`docs/SOURCE_TABLES.md`) and converted, archived, sent to review or excluded with a documented reason
> (`reports/coverage.json`). Payment history, wallet history, service operations, resellers, discounts/referrals and
> support are preserved as read-only archives. Tested on synthetic data; v0.1.0 was run by the operator on a real
> backup (197,952 customers, reconciliation passed). The `.nxpkg` is a *proposed* contract: it is **not** confirmed
> importable into NEXA production until the NEXA-side importer (`docs/NEXA_IMPORTER_PLAN.md`) exists and passes its tests.

## Quick start
* **Windows:** see [`docs/INSTALL_WINDOWS_FA.md`](docs/INSTALL_WINDOWS_FA.md) (prebuilt exe from CI artifacts, or `pip install .` then `mirza2nexa`).
* **Developers:** `python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]" && pytest`
  (`pytest -m e2e` for browser tests, `pytest -m perf -s` for the 200k-customer benchmark).
* **CLI:** `mirza2nexa convert backup.zip -o out --money-unit toman --password-env ZIP_PW`, `mirza2nexa verify|compare|synth`.

## Documentation
| Doc | Content |
|---|---|
| `docs/MAPPING.md` | per-entity source→target mapping, keys, validation, blockers, reconciliation |
| `docs/SOURCE_TABLES.md`, `docs/evidence/mirza_tables.json` | all Mirza tables: cited DDL, vocabularies, sensitivity, class |
| `docs/PACKAGE_CONTRACT.md` | package content schema 1.1.0: every file and record type |
| `docs/MODULES.md` | catalogue + domain-module framework and the module contract |
| `docs/FINANCE_HISTORY.md` | payment and wallet history archives and their reconciliation |
| `docs/SERVICES_AND_PRODUCTS.md` | service operations, panels, products/categories mapping proposal |
| `docs/RESELLER_CONTRACT.md`, `docs/ENGAGEMENT_AND_SUPPORT.md` | resellers, discounts/referrals, support/tickets |
| `docs/DATA_PROTECTION.md` | sensitivity policy, redaction, retention and access proposal |
| `docs/NEXA_EVIDENCE.md` | cited evidence from `nexa-bot@9e604def` (what NEXA knows about Mirza, and what it does not) |
| `docs/NXPKG_FORMAT.md` | byte-level container format and trust model |
| `docs/SECURITY.md` | threat model and the tests covering each threat |
| `docs/NEXA_IMPORTER_PLAN.md` | how the NEXA importer should be built; which temporary Mirza pages can be retired later |
| `docs/ARCHITECTURE_FA.md` | Persian architecture overview |

## Architecture
`ZIP → archive.py (guarded extraction) → sqldump.py (non-executing streaming parser) → staging.py (bounded on-disk index,
schema profile, fingerprint) → convert.py (NEXA rule ports, review queue, reconciliation) → diff.py → nxpkg/ (AEAD package)`;
`server.py` + `web/static/` provide the loopback-only Persian UI, `cli.py` the same engine headless.
Stack: Python 3.12+, FastAPI/uvicorn, pyzipper, cryptography; UI is dependency-free vanilla JS (no build step, strict CSP,
works offline); Windows build via PyInstaller in CI.

## Boundaries
- Source: a locally selected ZIP containing an SQL backup of Mirza. The archive/password and extracted data never go to GitHub or a remote API.
- Target: a *versioned package* for a separately implemented, authorized NEXA importer. **Never execute untrusted source SQL against the NEXA database.**
- Interface: a local web UI served only on `127.0.0.1`, intended for Windows desktop; no internet dependency for processing.
- Migration: dry-run + review + deterministic manifest first; production import requires separately approved NEXA changes.
- Existing NEXA production and Mirza staging are out of scope for this repository.

## Flow
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

See `CLAUDE.md` for the agent execution contract.

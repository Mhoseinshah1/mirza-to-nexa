# Agent execution contract — Mirza to NEXA

## Scope
Implement a **local-only Windows-friendly web application** for converting a password-protected ZIP containing Mirza SQL into an encrypted, versioned package for NEXA. Repository: https://github.com/Mhoseinshah1/mirza-to-nexa . Reference NEXA repository (read-only unless the owner separately approves changing it): https://github.com/Mhoseinshah1/nexa-bot .

## Multi-agent orchestration
- Lead agent coordinates source-schema analysis, parser/security, finance mapping/reconciliation, UI, NEXA contract, and testing workstreams.
- Independent workstreams may proceed in parallel on disjoint files. Lead agent reconciles contracts, integration, tests and PR descriptions.
- Continue safe independent tasks when one is blocked; identify blockers rather than inventing source fields.
- No need to stop for routine design choices; ask only for necessary data samples, irreversible actions or authorization boundaries.
- Never assume source schema from names. Inspect current NEXA source and safe **redacted / synthetic** Mirza schema samples first.
- Document every mapping (table, field, units, nullable, semantics, source version, target field) with evidence.
- No merge, deployment, production connection, migration cutover, or Mirza legacy code deletion without explicit approval.

## Hard safety invariants
- App binds only to 127.0.0.1. No upload/network transmission of the ZIP, SQL, password or generated package.
- Never log/commit real customer information, tokens, passwords, card data or SQL dumps. Public GitHub repository.
- Reject ZIP path traversal, symlinks and special files, oversized files, zip bombs, excessive archive entry counts, and unexpected file types.
- Prefer strict streaming extraction, bounded temporary disk use, safe isolated workspace, cleanup on failure and timeout.
- Never execute source SQL as trusted code on a database with production/remote access.
- Any detected ledger mismatch, debt ambiguity, orphan service, duplicate Telegram ID, conflicting currency units or uncertain invoice mapping is a blocking validation issue.
- Preserve historical financial events; opening balance snapshots must be idempotent on repeated import and correct across backup deltas.
- Original service identifiers/ownership must be proven; **never provision, delete, renew, or otherwise mutate panel services** during conversion.
- Package must have versioned schemas, stable source fingerprints, record-level provenance, tamper detection and authenticated encryption with safe key handling.
- A hash alone does not authenticate an attacker-controlled package. Define authenticated encryption, trust model, and package signature/HMAC as appropriate.
- Never permit an arbitrary SQL file to be imported directly by NEXA.

## Gates
1. Map proven Mirza and NEXA schemas, document unresolved mapping cases.
2. Implement archive intake and safety tests with synthetic ZIP fixtures.
3. Implement streaming Mirza SQL parser, schema version detection, negative tests.
4. Implement deterministic transform modules plus financial reconciliation and explicit review queue.
5. Implement portable Windows local web UI with accessible Persian copy, secure localhost browser origin/CSRF checks.
6. Define and test package contract with a matching NEXA importer proposal (do not edit NEXA without approval).
7. E2E rehearsal on synthetic data, then offline staging snapshot with approval; never production by default.

For each stage publish source SHA, tests run, outstanding blockers, and next gate. Do not present scaffold as a working converter.

## Current state (gate tracking)
| Gate | State | Evidence |
|---|---|---|
| 1 Mapping with evidence | all 51 known tables catalogued (Phase 3 adds 3 empty fork tables); 18 fork tables have operator column lists only | `docs/SOURCE_TABLES.md`, `docs/evidence/mirza_tables.json`, `docs/MAPPING.md` |
| 2 Archive intake + safety tests | done | `tests/test_archive.py` |
| 3 Streaming parser + schema detection | done | `tests/test_sqldump.py`, `tests/test_conversion.py` |
| 4 Transform + reconciliation + review queue | done incl. Phase 2 archives and Phase 3 readiness (service ownership model, wallet difference causes, panel/product mapping inputs, review taxonomy) on synthetic data; real-backup re-run of v0.3.0 by the operator pending | `tests/test_*`, `tests/test_ownership.py`, `tests/test_wallet_causes.py`, `tests/test_readiness.py`, `docs/OWNERSHIP.md`, `docs/FINANCE_HISTORY.md` |
| 5 Local Persian UI + localhost security | done; v0.4.0 adds bulk ownership review (plan/commit, audit chain, undo, seal; optional read-only panel status check, off by default) | `tests/test_server.py`, `tests/test_e2e.py`, `tests/test_ownership_review*.py`, `docs/OWNERSHIP_REVIEW.md` |
| 6 Package contract + importer proposal | content contract 1.2.0; importer **design** done, NEXA importer **not built** (needs owner approval) | `docs/NXPKG_FORMAT.md`, `docs/PACKAGE_CONTRACT.md`, `docs/NEXA_IMPORTER_DESIGN.md` |
| 7 Rehearsal | synthetic E2E only; real/staging snapshot **not run** | — |

Dev commands: `pip install -e ".[dev]"`, `ruff check src tests packaging`, `pytest`, `pytest -m e2e`, `pytest -m perf -s`.
Synthetic fixtures are generated at test time (`mirza2nexa.synth`); never add real backups, `.zip`, `.sql`, `.nxpkg` or `.nxkey` files.

Phase 2 framework: every table is resolved through `catalogue.py`; domain converters live in `src/mirza2nexa/modules/` and follow `docs/MODULES.md`. A new Mirza table needs a catalogue entry with evidence before any of its data is staged.

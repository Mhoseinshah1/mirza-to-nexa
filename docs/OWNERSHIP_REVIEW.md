# Bulk ownership review (`ownership_review.py`)

The converter decides service ownership from evidence (`OWNERSHIP.md`). Thousands of services can
still end up `AMBIGUOUS_OWNER` because Mirza logs are incomplete. This tool lets an administrator
settle them **in bulk** before the NEXA import, without ever pretending that an administrator's
decision is proof.

Scope: local development/test tool. No panel is changed, no service is provisioned, renewed,
disabled or deleted, and nothing is imported into NEXA.

## 1. Classes kept apart (never merged in any report)

| Final class | Meaning | Who decides |
|---|---|---|
| `PROVEN` | ownership proven by the converter (`CONFIRMED_CURRENT_OWNER`, `CONFIRMED_TRANSFER`, `NO_CONFLICT`) | evidence |
| `ADMIN_ATTESTED` | ambiguous service whose **invoice owner** (`invoice.id_user`) the administrator explicitly accepted | administrator |
| `PENDING` | ambiguous, attestable, not decided yet | — |
| `REJECTED` | administrator sent the service to quarantine (proven or not) | administrator |
| `QUARANTINED` | no valid owner or a serious conflict; **cannot be approved here**, never gets an arbitrary owner | system |

`ADMIN_ATTESTED` is never counted as proven: the readiness report, the summary, the export and
the importer contract all carry it as a separate class (`basis: ADMIN_ATTESTATION`).

### Eligibility (computed from the ownership record)

* `PROVEN` — decisions above. May be rejected (quarantined) by the administrator.
* `ATTESTABLE` — `AMBIGUOUS_OWNER` **and** the invoice owner exists in `user` and is imported
  **and** no reason is serious. Only the invoice owner can be accepted; no other account can be
  chosen.
* `QUARANTINE_ONLY` — `ORPHAN_OWNER`, `INVALID_OWNER`, no invoice, invoice owner not importable,
  or any serious reason:
  `OWNERSHIP_MULTIPLE_CLAIMANTS`, `OWNERSHIP_USERNAME_SHARED_BY_INVOICES`,
  `OWNERSHIP_TRANSFER_NOT_APPLIED_TO_INVOICE` (the log names a different owner),
  `OWNERSHIP_TRANSFER_TARGET_INVALID`, `OWNERSHIP_OWNER_ID_INVALID`,
  `OWNERSHIP_OWNER_NOT_IN_USER_TABLE`, `OWNERSHIP_NO_INVOICE_FOR_USERNAME`.

## 2. Two-phase bulk decision

1. **Plan** (`POST .../ownership/plan`): the server resolves the selection (explicit keys, *all
   filtered results* with optional exclusions, or a previous batch for undo) and returns counts,
   items that will not change, items that are not eligible (by reason), risks and up to 10 samples
   of items the administrator never opened. Nothing changes.
2. **Commit** (`POST .../ownership/commit`): requires the plan id, the exact number of items that
   will change (typed by the administrator) and an explicit acknowledgement. If any affected item
   changed since the plan, the commit is refused (`ownership.plan_stale`).

The plan also breaks the affected items down by ambiguity reason (`applicable_by_reason`).
Commits are applied in one SQLite transaction, so 5,000+ services take well under a
second. Re-sending the same commit returns the same batch (`idempotent_replay: true`); an item
already in the target state is counted as unchanged.

## 3. Audit, undo and seal

* Every change is an append-only audit event chained with SHA-256 (`prev_hash` → `hash`);
  `GET .../ownership/audit/verify` recomputes the chain.
* A batch can be undone (plan with `selection.mode = "batch"`, action `UNDO`): each item returns
  to its state before the batch only if **that batch** still made the item's current decision; an
  item decided again later (even to the same state), made stale or gone is counted as superseded.
* `verify` recomputes the hash chain **and** replays it: the stored decisions must equal what the
  audited events produced (each item event carries the record binding), and a sealed store must
  still end with its recorded `SEAL` event.
* **Seal** freezes the decisions for the final import. After sealing nothing can change (`409
  ownership.sealed`), a re-conversion cannot rewrite the items (divergence is only reported as
  `sealed_divergence`), and the digest of the sealed entries is kept so the export states
  `matches_seal`. Until then every decision is reversible. State checks of a commit or seal run inside
  the database write transaction, and all jobs of one import share one store.
* Decisions are bound to the ownership record (`binding` = SHA-256 of the canonical record). If a
  newer conversion produces a different record for the same service, the old decision is marked
  `stale` and is not exported as valid.

## 4. Storage and privacy

`<app dir>/reviews/<import_id>.sqlite`: survives restarts and re-conversion of the same backup
(same `import_id`). It stores invoice keys, codes, states, batch notes and the audit chain — no
Telegram ids, usernames, panel credentials or amounts. Owner details are read from the job's
staged records only while that job exists. Notes are free text: do not type customer data.

## 5. Read-only live panel check

Optional and **off by default** (setting `panel_live_check_enabled`). The administrator enters the
panel URL and an admin login for one Mirza panel code; they are held only in memory for that run.
Only Marzban-type panels are supported; `https` is required (plain `http` only for a loopback
address), and the HTTP client allows exactly two requests:
`POST /api/admin/token` (login) and `GET /api/user/{username}`. Redirects are refused. Only the
resulting status class is stored (`LIVE_ACTIVE`, `LIVE_DISABLED`, `LIVE_LIMITED`, `LIVE_EXPIRED`,
`LIVE_ON_HOLD`, `NOT_FOUND_ON_PANEL`, `CHECK_ERROR`, `UNSUPPORTED_PANEL`). A live status is a
**risk signal**, never ownership proof.

In this phase tests use a local fake panel on 127.0.0.1; no real panel is contacted.

## 6. Export for the importer

`ownership-decisions.json` (`schema: m2n.ownership_decisions.v1`): `import_id`,
`source_fingerprint`, `package_header_sha256` (binds the file to one package), `sealed`,
`sealed_digest` / `entries_digest` / `matches_seal`, the audit result, summary by class and one entry per service
(`key`, `invoice_key`, `class`, `basis`, `batch_id`, `binding`, `stale`). It is authenticated with
HMAC-SHA256 under a key derived from the package secret (HKDF info
`nxpkg-v1/ownership-decisions`), so only a holder of the package key can produce or verify it.
The importer must refuse an export that is unsigned, unsealed, not `matches_seal`, for another
`import_id` or package header, with `audit.ok` false, or whose bindings do not match the package records, and must treat `ADMIN_ATTESTATION` separately from
proven ownership.

## 7. HTTP API (all under `/api/jobs/{job_id}/ownership`, same localhost/CSRF guard)

| Method | Path | Purpose |
|---|---|---|
| GET | `/summary` | totals per class, live-check totals, sealed flag |
| GET | `/facets` | filter values with counts: panels, status classes, reasons, review states, live statuses, eligibility |
| GET | `/items` | filtered page; query `panel`, `status_class`, `reason`, `review_state`, `live_status`, `eligibility` (comma-separated), `q` (invoice key prefix), `offset`, `limit` ≤ 500 |
| GET | `/items/{key}` | detail with evidence summary; marks the item as viewed |
| POST | `/plan` | `{action: APPROVE\|REJECT\|RESET\|UNDO, selection: {mode: keys, keys} \| {mode: filter, filter, exclude_keys} \| {mode: batch, batch_id}, note}` |
| POST | `/commit` | `{plan_id, confirm_count, acknowledge: true}` |
| GET | `/batches` | committed batches |
| GET | `/audit` | audit events (`offset`, `limit`) |
| GET | `/audit/verify` | hash-chain verification |
| POST | `/seal` | `{confirm: "SEAL", confirm_count}` |
| GET | `/export.json` | signed export when the job's generated key is in memory |
| POST | `/export` | `{passphrase}` for passphrase-protected packages |
| GET | `/live-check` | progress of the read-only panel check |
| POST | `/live-check` | `{panel_code, base_url, username, password, selection}` |
| POST | `/live-check/cancel` | stop the check |

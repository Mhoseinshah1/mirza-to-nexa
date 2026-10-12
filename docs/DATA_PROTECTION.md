# Data protection: what may leave the converter

This document sets the policy for every value the converter reads from a Mirza backup. It covers
how columns and tables are classified, what never leaves, how free text is redacted, what is kept
and for how long, and who in NEXA should be able to read the archives. The threat model for the
app itself (ZIP safety, localhost, package crypto) is in `docs/SECURITY.md`.

Evidence ids: `BMP` = botmirzapanel@92c0ed06, `PRO` = mirza_pro@b645db74, `NEXA` = nexa-bot@9e604def,
`EVJ` = `docs/evidence/mirza_tables.json` (Source DB agent; column sensitivities with citations).

## 1. Column sensitivity

Every catalogued column has one of four sensitivities (`src/mirza2nexa/catalogue.py`).

| Sensitivity | Meaning | Staged? | Leaves in the package? |
|---|---|---|---|
| `public` | Configuration, labels, status codes | yes | yes, after value redaction |
| `personal` | Identifies a person: Telegram id, username, phone, IP, admin id | yes | only where a record needs it (customers, ownership); never in reports, history or logs |
| `financial` | Amounts, prices, percentages | yes | yes, never re-scaled (`ctx.money`) |
| `secret` | **Anything that authenticates or names a payout destination** | **no** — dropped by the parser before the staging database | **never** |

`secret` covers passwords and password hashes, bot tokens, webhook secrets, API keys, merchant
ids, gateway endpoints, panel credentials, subscription links, panel UUIDs and configs, private
keys, cookies, the business's card numbers, IBAN/Sheba and crypto wallet addresses, private
channel invite links, gift and discount codes (bearer instruments), and request logs. When in
doubt, a column is `secret`.

Two more rules apply:

* **Unknown columns are not staged.** A table with a column list stores only the columns it
  declares. Anything else is reported as `unmapped_columns` and never read. Tables with no column
  list (`columns=None`, generic) drop every column whose *name* looks like a credential
  (`sensitivity.column_name_is_secret`). An unknown table with no public DDL gets an *empty*
  column list (`help_category`), so it fails closed: rows are counted and nothing is stored.
* **The catalogue is never weaker than the evidence.** If `EVJ` marks a column `secret`, the
  catalogue may not declare it storable. `tests/test_modules_contract.py::test_catalogue_never_weaker_than_evidence`
  checks this.

The single exception is the value column of the key-value tables `PaySetting.ValuePay` and
`shopSetting.value`. These columns must be staged so the allow-listed keys can be read. The
module drops every other key (see §3), and the staging database is deleted at the end of the run.
§7 proposes a staging-time filter that would remove this exception.

## 2. Table decisions (config_tables module)

| Table | Class | Leaves | Secret / dropped | Evidence |
|---|---|---|---|---|
| `setting` | CONFIGURATION | allow-listed switch, limit, layout and text columns (single row) | `webhook_secret`; columns not in the list (`unmapped_columns`) | PRO db/tables/setting.php:18-66,114-164; BMP table.php:110-143; PRO migrations 010, 011; EVJ#setting |
| `PaySetting` | CONFIGURATION (key allow-list) | gateway on/off switches, min/max limits, cashback %, fees, tutorial texts | `cardnumber`, `CartDescription`, `namecard`, `CartDirect`, `Exception_auto_cart`, `apinowpayment`, `merchant_id_aqayepardakht`, `marchent_tronseller`, `walletaddress`, `apiternado`, `merchant_zarinpal`, `apiiranpay`, `apiiranpay4`, `endpointiranpay4`, `variza_api_token`, `variza_webhook_secret`, `marchent_floypay`, `urlpaymenttron`, `perfectmoney_*`, **and every unknown key** | PRO db/tables/PaySetting.php:7-110; PRO migrations 005, 008:21-29; BMP table.php:511-532; EVJ#PaySetting |
| `shopSetting` | CONFIGURATION (key allow-list) | the 17 seeded price/switch keys | unknown keys | PRO db/tables/shopSetting.php:5-36; EVJ#shopSetting |
| `textbot` | CONFIGURATION | `id_text`, `text` (redacted) | — | BMP table.php:460-509; EVJ#textbot |
| `channels` | CONFIGURATION | `remark`, public `link` | `linkjoin` (private invite link grants access) | PRO db/tables/channels.php; BMP table.php:192; EVJ#channels |
| `help` | CONFIGURATION | name, media file id (valid only for the old bot), category, description (redacted) | — | PRO db/tables/help.php; BMP table.php:90-96; EVJ#help |
| `app` | CONFIGURATION | app name and download link | — | PRO db/tables/app.php; PRO keyboard.php:844-850; EVJ#app |
| `protocol` | CONFIGURATION | `NameProtocol` | other columns (no DDL exists) | BMP keyboard.php:336-350 |
| `help_category` | REVIEW (fails closed) | nothing; rows counted plus a `CONFIG_NO_STORABLE_COLUMN` review item | all columns, until the operator supplies the DDL | EVJ#help_category (operator report only) |
| `admin` | EXCLUDED | counts only | `username`, `password` (web-panel login and bcrypt or plaintext hash) | PRO db/tables/admin.php; PRO db/migrations/012_admin_panel_credentials.php:7-16; NEXA research admin-management MASTER.md; EVJ#admin |
| `botsaz` | EXCLUDED | counts only | `bot_token`, `webhook_secret`, `setting` (contains `cart_info`) | PRO db/tables/botsaz.php; PRO api/users.php:600-668; EVJ#botsaz |
| `card_number` | EXCLUDED | counts only | `cardnumber`; `namecard` (personal) | PRO db/tables/card_number.php; PRO index.php:4660-4701; EVJ#card_number |
| `logs_api` | EXCLUDED | counts only | `header`, `data` (raw request), `ip` | PRO db/tables/logs_api.php; PRO api/utils.php:173; PRO cronbot/optimize_order.php:15; EVJ#logs_api |
| `topicid` | EXCLUDED | counts only | — (not secret, but bound to the old report group) | PRO db/tables/topicid.php; EVJ#topicid |

**Why `card_number` is excluded instead of masked to the last 4 digits.** Mirza picks a card at
random for each top-up (`PRO index.php:4660`), and no receipt row references a card row. The last 4
digits would therefore have no proven use for reconciliation. NEXA configures its own payout
accounts by hand.

Configuration rows are written as `legacy_configuration_row` records in
`records/configuration/<table>.jsonl`, with `applies_to_live_state: false`. They are reference
material for the operator only. NEXA never applies them to its live settings.

## 3. Key-value allow-lists

`PaySetting` and `shopSetting` store one setting per row. Their rows are handled as follows:

* **Allow-listed key** (`config_tables.PAYSETTING_ALLOWED` / `SHOPSETTING_ALLOWED`): the row is exported, and its value is redacted.
* **Known secret key**: the row is dropped. Its *name* is listed in coverage `dropped_secret_keys`.
* **Unknown key**: the row is dropped and counted in `dropped_unknown_keys`, and the package gets the `CONFIG_UNKNOWN_KEYS_DROPPED` warning. A fork may add keys that hold credentials, so unknown is treated as secret.

## 4. Value-level redaction (`src/mirza2nexa/sensitivity.py`)

Every value that leaves through an archive or configuration record goes through `redact_text`,
and so does any report text derived from source data. A match is replaced by
`[REDACTED:<kind>]`, and `redacted_cells` / `redacted_kinds` are counted in coverage.

| Kind | Detects |
|---|---|
| `private_key` | PEM private keys |
| `proxy_link` | `vless://`, `vmess://`, `trojan://`, `ss://`, `ssr://`, `hysteria(2)://`, `hy2://`, `tuic://`, `wireguard://`, `wg://` |
| `url_creds` | `scheme://user:pass@host` |
| `sub_url` | `http(s)` URLs with `/sub/`, `/subscription` or `?token=` / `key=` / `secret=` / `sub=` |
| `bot_token` | `<6-12 digits>:<30+ token chars>` |
| `jwt` | `eyJ….….…` |
| `bearer` | `Bearer` / `Basic` authorization values |
| `kv_secret` | the value after labels such as `password`, `token`, `api_key`, `secret`, `merchant_id`, `cookie`, `otp`, `pin`, `cvv`, `رمز`, `رمز عبور`, `گذرواژه`, `توکن`, followed by `:` or `=` (JSON quotes allowed) |
| `iban` | `IR` + 24 digits (Sheba), with or without spaces |
| `uuid` | RFC 4122 UUIDs (panel client ids double as credentials) |
| `secret_like` | opaque runs of 32 or more base64url/hex characters |
| `card` | 13-19 digits, ASCII **or Persian/Arabic-Indic**, that pass Luhn. Masked to `****<last 4>` |

Redaction never touches ordinary Persian text, Telegram ids, amounts with or without separators,
10-digit timestamps, short codes, keyboard JSON or `t.me` links. Each of these cases has a test in
`tests/test_redaction.py::test_no_false_positive`.

Redaction is a **second line of defence**. Column and key allow-lists are the first. Redaction
cannot make an unknown column safe, which is why unknown columns are never staged.

## 5. What never leaves the machine, and what never leaves the converter

* **Never leave the machine:** the ZIP, the SQL, the ZIP password and the package key or
  passphrase. The app binds to 127.0.0.1 only (`docs/SECURITY.md`).
* **Never leave the converter (package, report, review queue, coverage, history, API, logs):**
  secret columns, secret and unknown keys of key-value tables, and the ZIP password. The package
  key appears only in the key download (`/api/jobs/{id}/key`) and is never written into the package,
  report or history.
* **Raw source snapshot** (contract 1.4.0, `PACKAGE_CONTRACT.md` §"Source snapshot"): the allowlisted
  columns of `user`, `invoice` and `product` as raw text, including personal cells the records minimise
  (`user.number`, `invoice.note`, `invoice.refral`, usernames), because NEXA's importer re-decides from them.
  They exist only inside the encrypted payload and the job's `stage/source/`. The allowlist can never contain
  a secret column (`source_snapshot.assert_allowlist_safe`); every other column of the row is dropped on read.
* **Reports, review items and coverage** carry codes, counts, table, column and key *names*, and
  record identifiers. They never carry credential values.
* **History** (`jobs.read_history`) keeps aggregates only: counts, a 12-character fingerprint
  prefix, readiness and timings.
* **Logs:** the converter emits no log records containing source values. A test captures all
  logging at DEBUG level during a full run and checks it.

`tests/test_redaction.py` plants fabricated secrets in secret columns and inside free text
(`config_tables.PLANTED_SECRETS`: bot tokens, a vless link, a subscription URL, a JWT, a Luhn-valid
test card, a password, an IBAN, a webhook secret, a merchant UUID, an API key, a wallet address, an
admin login and a bcrypt hash). It then runs the pipeline and the local server, and searches every
decrypted payload file, `report.json`, the workspace's `stage/` directory, every API response (job,
jobs list, report, review pages, preview of every entity), the history file and API, and the log.
None of the planted secrets may appear, and neither may the ZIP password, the package key or the
package passphrase. `tests/test_modules_contract.py::test_evidence_secret_columns_are_never_staged`
checks the same for every column EVJ marks secret, in every table of every module.

## 6. Retention and minimisation

| Artefact | Where | Lifetime |
|---|---|---|
| Uploaded ZIP | job workspace | until the job is deleted or the app exits; stale workspaces are swept after 2 hours (`jobs.sweep_stale_workspaces`) |
| Extracted SQL | `workspace/sql` | deleted right after parsing (`delete_sql_after_parse`, on by default) |
| Staging SQLite | `workspace/staging.sqlite` | deleted at the end of each run, and on failure (`pipeline._cleanup_plaintext`) |
| Snapshot sort SQLite | `workspace/snapshot.sqlite` | deleted as soon as `source/tables/*.jsonl` are written, and on failure |
| Plaintext records (`stage/`) | job workspace | kept for UI preview and backup-to-backup diff; deleted with the job |
| `.nxpkg` | job workspace and the user's download | encrypted (AES-256-GCM STREAM); kept as the operator decides |
| History | app home `history.json` | aggregates only; bounded by `history_limit`; can be cleared from the UI |

Minimisation rules:

* Only columns a NEXA entity or an operator decision needs are staged.
* Phone numbers are reduced to a class in the records (the raw source snapshot keeps `user.number` for
  NEXA's own classification; see §5).
* Logs, caches and secret-only tables are excluded and counted.

## 7. Proposed access policy for the NEXA archives

The archive and configuration records are history. They are not live data. The NEXA importer
proposal should enforce the following access rules. The NEXA side is not built (it needs owner approval).

| Data | Who may read | Notes |
|---|---|---|
| `records/configuration/*` (settings, texts, help) | owner and super-admins | reference for re-configuring NEXA by hand; never auto-applied |
| Support texts (`support_message`, `ticket*`, archived) | support role, for the customer concerned only, with an audit log entry per read | may contain personal data in free text; redacted for secrets only |
| Payment evidence (`Payment_report`, receipts, `wallet_transaction` history) | finance role and owner; read-only; with audit log | never re-enters a payment state machine (`enters_live_payment_state_machine: false`) |
| Agent / reseller archives | owner | decisions only |
| Review queue and coverage | owner and the migration operator | codes and counts only |
| The `.nxpkg` and its key | the migration operator only; keep them apart; delete both after a verified import | the key is never stored next to the package |

Retention in NEXA: archive tables should carry the import id. They should be purgeable per import
(for example after the legal retention period for financial records), and personal free text
should be purgeable on a customer's request without affecting the financial ledger.

## 8. Residual risks

* **Key-value settings (resolved in Phase 2).** `TableSpec.kv_filter` is applied in
  `staging.Staging.on_row`: for `PaySetting`/`shopSetting` the value of every key outside the
  allow-list is set to `NULL` before it is written, so gateway secrets never reach the staging file.
  The SQL dump itself still holds them in plaintext until it is deleted after parsing.
* **Redaction is pattern-based.** A credential with no recognisable shape in a free-text column,
  such as a short password typed without a label, will pass through. This is mitigated by
  allow-listing columns, so free text is exported only from tables whose meaning is proven
  (texts, help, descriptions).
* **Card false positives and negatives.** A 13-19 digit number that happens to pass Luhn, such
  as a millisecond timestamp, is masked. That costs readability, not safety. A card split by
  characters other than spaces or `-` is not detected.
* **Telegram ids and usernames** are personal data and stay in customer and ownership records by
  design. The protection is the package encryption and the NEXA access policy.
* **`help_category` and future forks.** Tables or keys without public evidence fail closed. This
  can hold back useful data until the operator supplies a redacted schema sample.
* **Plaintext `stage/` records** stay in the workspace until the job is deleted, for preview and
  diff. Use full-disk encryption (`docs/SECURITY.md`).

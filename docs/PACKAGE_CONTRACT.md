# `.nxpkg` content contract: package schema `nexa.migration.mirza` 1.5.0

The container format, meaning the encryption, chunking and checksums, is described in `NXPKG_FORMAT.md` and has
not changed. This document covers the **content**: which files and records a package holds, and what an
importer may and may not do with each of them.

## Versioning

| Version | Change | Compatible? |
|---|---|---|
| 1.0.0 | Phase 1: customers, opening balances, debts, products, categories, invoices, services, panels, review queue | — |
| 1.1.0 | Phase 2, additions only: archive and history files, configuration files, `reports/coverage.json`, a `schema` field on every record | A 1.0 reader that ignores unknown files and fields keeps working |
| 1.3.0 | Additions only: `records/panel_target_mapping.jsonl` (operator-selected NEXA target panel type, RickPanel) and `reconciliation.panel_targets` | A 1.2 reader that ignores unknown files keeps working; absence means "no target selected", never "Marzban" |
| 1.4.0 | Additions only: the **raw source snapshot** `source/catalog.json` + `source/tables/{user,invoice,product}.jsonl` (§"Source snapshot"), so NEXA's existing legacy importer can read the package through `LegacySourceSession` unchanged; three blocker codes `SOURCE_SNAPSHOT_*`; `report.source.snapshot` | A 1.3 reader that ignores unknown files keeps working. The NEXA `.nxpkg` importer requires `>= 1.4.0` and refuses a package without the snapshot (`NXPKG_SOURCE_SNAPSHOT_MISSING`) |
| 1.5.0 | Additions only (converter 0.8.0, §"Column carry"): supplement history files `records/archive/<table>_carried.jsonl` / `records/configuration/<table>_carried.jsonl` (existing `legacy_archive_row` / `legacy_configuration_row` types), archives `records/archive/{admin,botsaz,logs_api,topicid,global_hidden_gateways,ai_business_chat}.jsonl` for six tables that were EXCLUDED, `reports/manual-reconfiguration.json`, coverage fields `carried_columns`, `minimised_columns`, `dropped_unknown_key_names`, `carry_heuristic_columns`, `column_dispositions`, `unaccounted_columns`, `carry_file`, `carry_records`; blocker `COLUMN_UNACCOUNTED`; review codes `CARRY_UNVERIFIED_TABLE_ARCHIVED`, `CARRY_DUPLICATE_PK` | A 1.4 reader keeps working: every new record file is under the two generic history directories NEXA already ingests, every record file a 1.4.0 package had, the source snapshot, the source fingerprint and `import_id` stay byte-identical for the same backup (reports such as coverage and the review queue gain the carry facts) (`tests/test_carry.py::test_carry_changes_no_existing_record_and_no_fingerprint`) |
| 1.2.0 | Phase 3, additions only: service ownership, wallet difference analysis, panel/product mapping inputs, agent state, migration readiness and review taxonomy reports (marked *added in 1.2.0* below); three more catalogued fork tables (`botsaz_help` → `records/archive/`, `global_hidden_gateways` and `ai_business_chat` EXCLUDED); `help_category` rows now archived (secret-named columns dropped) | A 1.1 reader that ignores unknown files and fields keeps working. An importer that *consumes* 1.2 files must check `package_schema_version >= 1.2.0` and treat their absence in older packages as "not available", never as "empty" |

The rules:
* A **minor** bump only adds files or fields.
* A **major** bump changes or removes something; readers reject an unknown major version.
* Each record schema is versioned on its own (`schema: "<name>.v<N>"`).

## How NEXA consumes the package (converter 0.7.0; unchanged in 0.8.0)

The converter calls its output «فایل بکاپ NEXA برای بازیابی از میرزا» and names it
`nexa-restore-mirza-<import_id[:12]>.nxpkg`, with the key beside it as `nexa-restore-mirza-<import_id[:12]>.nxkey`
(the content and the container are unchanged; a reader must not depend on the file name). In NEXA it is
restored from the Web Admin «بکاپ و بازیابی ← عملیات بازیابی» as a second restore source: the operator chooses
the file, presses «بررسی» and gives the key file or passphrase; NEXA verifies the package and shows the panel
mapping, ownership and dry-run counts before anything is written; «تأیید بازیابی» then takes a protective backup,
freezes writes, imports through the existing nxpkg migration engine, reconciles and takes a backup. It is not a
`.nxb`: a `.nxb` is encrypted under the installation's own key and replaces the whole database. That restore path
is in nexa-bot draft PR #278 — **under review, not released** — so for now only a test installation restores a
package, and `reports/migration-readiness.json` (readiness 1.1.0) keeps the import blocker
`NEXA_RESTORE_PATH_UNRELEASED` (it replaced `IMPORTER_NOT_BUILT`).

## Fields every record has

| Field | Meaning |
|---|---|
| `record_type` | the kind of record (tables below) |
| `schema` | the versioned schema of the record, e.g. `mirza.payment_report.v1` or `m2n.customer.v1` |
| `idempotency_key` | starts with `legacy:` and is unique within the package. The same source row in a later backup gets the same key. At most 400 characters before a `#…` suffix (NEXA stores up to 512): a key whose primary key would be longer is `legacy:<table>:pkh:<sha256 of the key text>`, and the full value stays in `provenance.source_pk`. |
| `provenance` | `{source_table, source_pk, source_fingerprint, row_checksum?}` |

Many records also carry live-state flags, each fixed to `false` where it applies:
* `affects_wallet`
* `creates_payment`
* `creates_order`
* `counts_as_revenue`
* `enters_live_payment_state_machine`
* `triggers_service`
* `provision`
* `applies_to_live_state`
* `applied_to_balance`
* `activates_role`
* `grants_credit`
* `redeemable`
* `auto_create`

**An importer must refuse any record where one of these flags is `true`.**

## Files

### Operational (become NEXA entities after the importer re-decides them and an operator approves)
| File | `record_type` | NEXA target |
|---|---|---|
| `records/customers.jsonl` | `customer` | customer + `legacy_import_map` |
| `records/wallet_opening_balances.jsonl` | `wallet_opening_balance` | one ledger CREDIT `MIGRATION_OPENING_BALANCE`, reference = `idempotency_key` |
| `records/legacy_debts.jsonl` | `legacy_debt` | `legacy_wallet_debts` (`PENDING_REVIEW`) |
| `records/services.jsonl` | `legacy_service_candidate` | `legacy_service_candidates` → adoption (panel map + live check) |

### Review proposals (nothing is created automatically)
| File | `record_type` |
|---|---|
| `records/products.jsonl` | `legacy_product_review` |
| `records/categories.jsonl`, `records/category_catalogue.jsonl` | `legacy_category_label`, `legacy_category` |
| `records/panels.jsonl`, `records/panel_registry.jsonl` | `legacy_panel_code`, `legacy_panel` |
| `records/agents/profiles.jsonl`, `tier_prices.jsonl` | `legacy_agent_profile`, `legacy_agent_price_level` |

### Phase-3 decision inputs (*added in 1.2.0*; nothing is applied automatically)
| File | `record_type` | Consumer rule (`NEXA_IMPORTER_DESIGN.md`) |
|---|---|---|
| `records/service_ownership.jsonl` | `legacy_service_ownership` | only `CONFIRMED_*` states may enter P6 adoption (after NEXA re-decision, panel map and per-service approval); `AMBIGUOUS_*` / manual-review / unknown states → quarantine (§8) |
| `records/wallet_difference_analysis.jsonl` | `legacy_wallet_difference` | read-only evidence beside the balance snapshot; used for owner decisions on existing customers (§6); never applied to balances |
| `records/panel_mapping_template.jsonl` | `legacy_panel_mapping_template` | input to the owner's panel mapping form; holds no credentials (§7) |
| `records/product_mapping_proposal.jsonl` | `legacy_product_mapping_proposal` | → `legacy_product_reviews` `PENDING_REVIEW`; no product is created (§9) |
| `records/agents/state.jsonl` | `legacy_agent_state` | owner-decision summary per agent; `role_active` is always `false` (§9) |
| `records/panel_target_mapping.jsonl` | `legacy_panel_target` | *added in 1.3.0.* Operator-selected NEXA target per legacy `code_panel` (only `rickpanel` / RickPanel, `PANELS_AND_ADOPTION.md` §3.1a). The importer must refuse any other `provider_type`, connect only to an existing ACTIVE `rickpanel` panel (`nexa_panel_id`) or create one through NEXA's secure settings with owner approval; services are adopted, never re-created |

`record_type` names of the template/proposal files follow the producing module; an importer resolves them from
the record itself and refuses unknown values (gate G5).

### Read-only history archives
| File | `record_type` | Notes |
|---|---|---|
| `records/invoices.jsonl` | `legacy_invoice_archive` | never orders or payments |
| `records/payments.jsonl` | `legacy_payment_history` | `FINANCE_HISTORY.md`: status, method with direction, amounts never rescaled |
| `records/wallet_history.jsonl` | `legacy_wallet_transaction` | never applied to balances |
| `records/wallet_history_checks.jsonl` | `legacy_wallet_history_check` | snapshot vs history, per customer |
| `records/service_operations.jsonl` | `legacy_service_operation` | renewals, extra volume/time, transfers, gifts |
| `records/service_cancellation_requests.jsonl` | `legacy_service_cancellation_request` | |
| `records/manual_config_inventory.jsonl` | `legacy_manual_config_inventory` | configs themselves are secret and not included |
| `records/agents/{agent_invoices,agent_logs,agent_usage,agent_requests}.jsonl` | `legacy_agent_*` | |
| `records/engagement/{discounts,discount_usage,referrals,wheel_results,ad_campaigns,program_settings}.jsonl` | `legacy_*` | discount and gift **code strings are not included**, with one owner-approved exception: `discount_sell` records carry `code_text` (`code_plaintext_exported: true`) so NEXA recreates those codes DISABLED (`DATA_PROTECTION.md` §1) |
| `records/support/{departments,support_messages,tickets,ticket_messages}.jsonl` | `legacy_support_*`, `legacy_ticket*` | text is personal: restricted access (`DATA_PROTECTION.md` §7) |
| `records/archive/<table>.jsonl` | `legacy_archive_row` | generic raw archive for tables whose meaning is still open (`REVIEW` class) |
| `records/configuration/<table>.jsonl` | `legacy_configuration_row` | non-secret settings, bot texts and help pages, for reference only |
| `records/archive/<table>_carried.jsonl`, `records/configuration/<table>_carried.jsonl` | `legacy_archive_row` / `legacy_configuration_row`, `schema: m2n.carried_columns.v1` | *added in 1.5.0.* One supplement record per source row: the columns the table's own converter does not read (§"Column carry") |

### Reports
| File | Content |
|---|---|
| `reports/coverage.json` | One row per source table: its class, status (`executed`, `archived`, `review`, `blocked`, `empty` or `excluded`), counts, dropped secret columns, unmapped columns and reason; since 1.5.0 also the disposition of every column (§"Column carry") |
| `reports/manual-reconfiguration.json` | *added in 1.5.0.* Every secret column / settings key the package does not carry, by NAME: `{table, column, key, rows, basis, category, kept, note, action, action_fa}`; an undocumented settings key is category `unknown_setting`. Never a value |
| `reports/reconciliation.json` | Sections: `wallet`, `invoices`, `services`, `payments`, `wallet_history`, `service_operations`, `service_requests`, `panel_registry`, `category_catalogue`, `agents`, `engagement`, `support`, … |
| `reports/mapping.json` | The mapping version, the entity mapping and the schema profile |
| `reports/review-queue.jsonl` | `{kind, severity, code, message (fa), entity, source_ref, data}`. Never holds secrets or message text. |
| `reports/diff.json` | Only present when the package was compared with a previous backup |
| `reports/migration-readiness.json` | *added in 1.2.0.* Aggregate readiness summary for the owner (counts, sums, codes, table names; no personal data) |
| `reports/review-taxonomy.json` | *added in 1.2.0.* Every review code with its domain and resolution kind (e.g. resolvable by code, needs owner decision, adoption-time); drives quarantine routing |

### Catalogue policy for tables (unchanged in 1.2.0, stated for importers)
* Every table in the dump appears in `reports/coverage.json`.
* An **uncatalogued** table that is **empty** is reported with status `empty` and does not block.
* An **uncatalogued** table that is **non-empty** blocks the package (`UNCLASSIFIED_TABLE`).
* A catalogued `EXCLUDED` table is counted only (`excluded`). Since 1.5.0 only `card_number` and NEXA's
  synthetic marker are EXCLUDED; `global_hidden_gateways` and `ai_business_chat` are carried (ARCHIVE) and add a
  `CARRY_UNVERIFIED_TABLE_ARCHIVED` review item when non-empty (they replace `CONFIG_UNVERIFIED_TABLE_EXCLUDED`).
* `mirza2nexa schema BACKUP.zip -o schema.json` exports table/column structure and row counts (no values, no
  defaults) so a fork's tables can be catalogued without sharing the backup.

## Source snapshot (*added in 1.4.0*)

Purpose: NEXA's legacy importer reads Mirza through its `LegacySourceSession` port
(`nexa-bot` `apps/api/src/modules/platform/legacy-importer/application/source-port.ts`): table and
column metadata as `information_schema` reports it, and the rows of `user`, `invoice` and `product` as
`CAST(col AS CHAR)` text in primary-key byte order. The snapshot is exactly that, taken from the dump, so a
package-backed adapter (`NxpkgLegacySourceConnector`, `nexa-bot` `docs/legacy-migration/nxpkg-importer.md` §3)
can feed the frozen `IMPORT_READ_SET_V1` and every versioned read set (`user-status`, `products`,
`invoice-archive`) **unchanged**, and the importer recomputes its own fingerprints and decisions from it.
It is collected in the same single streaming parse as staging (`src/mirza2nexa/source_snapshot.py`) and
does not change any other file: with or without it, every `records/*` file and the `import_id` are
byte-identical (`tests/test_source_snapshot.py`).

### `source/catalog.json` (canonical JSON, metadata only — no cell value)
```json
{
  "format": "m2n.source-catalog.v1",
  "snapshot_version": 1,
  "synthetic_marker": null,
  "engine_hint": "MYSQL",
  "tables": [
    {"name": "invoice", "table_type": "BASE TABLE", "storage_engine": "InnoDB",
     "charset": "utf8mb4", "collation": "utf8mb4_bin", "rows": 130007,
     "columns": [{"column": "id_invoice", "data_type": "varchar", "ordinal": 1, "charset": "utf8mb4"}, "…"]},
    "…"
  ],
  "snapshot_tables": {
    "user": {"file": "source/tables/user.jsonl", "primary_key": "id",
             "columns": ["id", "Balance", "limit_usertest", "agent", "number", "username", "User_Status"],
             "rows": 200003},
    "…": "…"
  }
}
```
| Field | Meaning (maps to the port) |
|---|---|
| `snapshot_version` | `1`. A reader refuses any other value. |
| `synthetic_marker` | `syntheticMarker()`: the first `label` of NEXA's marker table `nexa_synthetic_fixture` if the dump has that table (`"SYNTHETIC (unlabelled marker)"` when it has no labelled row, like `mysql-legacy-source.ts:347-360`); `null` for a real backup. The converter's synthetic dumps carry `"SYNTHETIC mirza2nexa fixture v1 (not evidence)"`. |
| `engine_hint` | `"MYSQL"` / `"MARIADB"` from the dump's comment header (`-- MySQL dump … Distrib …`, `-- MariaDB dump`, phpMyAdmin `-- Server version:`); `null` when absent or when two SQL files disagree. A hint only — the package is not a live engine. |
| `tables[]` | `tables()` + `countRows()`: **every** table the dump creates or inserts into, sorted by UTF-8 name bytes. `table_type` is always `BASE TABLE` (views are not dumped as tables; the parser skips `CREATE VIEW`). `storage_engine`, `charset`, `collation` are the `CREATE TABLE` options as dumped (`null` when the dump does not state them; `charset` falls back to a known prefix of the stated collation, and a stated `CHARSET` without `COLLATE` leaves `collation` null, because the engine's default collation is not provable). `rows` is the **exact** number of rows the dump inserts (every `INSERT`/`REPLACE` row event, like `COUNT(*)` after loading; a real `mysqldump` never repeats a key). |
| `tables[].columns[]` | `columns()` / `catalogColumns()`: every column of the table in `CREATE TABLE` order. `data_type` is the lowercase base type as `information_schema.COLUMNS.DATA_TYPE` reports it (`varchar`, `int`, `bigint`, `text`, `longtext`, `decimal`, `datetime`, …; aliases resolved: `integer`→`int`, `bool`→`tinyint`, `numeric`/`dec`/`fixed`→`decimal`, `real`→`double`; `json`→`longtext` when `engine_hint` is `MARIADB`). `ordinal` is 1-based. `charset` is the column's character set for `char`/`varchar`/`*text`/`enum`/`set` (column clause, else the table's) and `null` for every other type. |
| `snapshot_tables` | The tables that have a rows file, with their primary key, the exact column list of the file and its row count. A table absent from the dump, or lacking its primary key column, has no file (NEXA then refuses it itself: `SOURCE_SCHEMA_MISSING_TABLE` / `_COLUMN`). |

### `source/tables/<table>.jsonl` (`user`, `invoice`, `product`)
Line 1 is a header, every further line is one row:
```
{"columns":["id","Balance","limit_usertest","agent","number","username","User_Status"],"format":"m2n.source-table.v1","primary_key":"id","table":"user"}
{"c":["7000000000","0","0","n","none","synthetic_user_0","Active"]}
```
* **Columns** are the NEXA allowlist present in the dump, in this fixed order (the union of what
  `IMPORT_READ_SET_V1`, `user-status`, `products` and `invoice-archive` read; exact, case-sensitive names):
  * `user`: `id, Balance, limit_usertest, agent, number, username, User_Status`
  * `invoice`: `id_invoice, id_user, username, Status, is_test, code_panel, code_product, Volume, Service_time,
    time_unit, is_custom, price_product, Service_location, time_sell, name_product, note, refral, time_cron,
    notifctions`
  * `product`: `id, code_product, name_product, price_product, Volume_constraint, Service_time, Location, Category,
    category, agent, note, data_limit_reset, one_buy_status, hide_panel`

  No other column is ever written. Secret columns (`inbounds`, `proxies`, `user_info`, `uuid`, `bottype`, every
  column the catalogue marks secret or whose name looks like a credential) cannot be in the allowlist: the
  converter refuses to run otherwise (`source_snapshot.assert_allowlist_safe`, tested). A reader serves
  `rows(table, cols)` / `readSetRows(table, pk, cols)` by projecting `c` onto `cols` by name and refuses a
  column the header does not list.
* **Cells** are `string | null`: exactly the text MySQL returns for `CAST(col AS CHAR)` of the dumped value under
  the column's declared type, `null` for SQL `NULL`. A column left out of an `INSERT` column list takes its
  literal `DEFAULT` (`DEFAULT NULL` → `null`).
* **Order**: rows sorted by the UTF-8 bytes of the primary-key cell, ascending (`ORDER BY CAST(pk AS BINARY)`,
  `compareKeyBytes`); an `int` key therefore sorts `"10"` before `"2"`. Keys are unique and non-null.
* The manifest's `records` count for a table file is `rows + 1` (the header line).

### Value fidelity (fail closed)
The dump parser unescapes literals (`\'`, `''`, `\n`, `\0`, …; `\%` and `\_` keep their backslash, as in
MySQL). A cell is written only when its MySQL text is provable; otherwise the package is **blocked**:

| Declared type | Written text | Refused (blocks) |
|---|---|---|
| `char`, `varchar`, `*text`, `enum`, `set` | the string as stored (`char`: trailing spaces removed, as MySQL returns them); an unquoted integer or plain decimal literal as written (`15`, `1.50`) | value longer than `varchar(n)`/`char(n)` characters; exponent literals; `''` in `enum`/`set`; bytes that are not UTF-8 |
| `tinyint` … `bigint` (signed/unsigned) | canonical integer (`'-7'` → `-7`, `5.0` → `5`) | non-integral (`'12.5'`), empty string, out of range, `zerofill` |
| `decimal(p,s)` | fixed-point with exactly `s` fraction digits (`5.5` → `5.50` for `s = 2`) | more fraction digits than `s` (MySQL would round), more than `p` digits |
| `date`, `datetime(fsp)`, `timestamp(fsp)`, `time`, `year` | as dumped | anything but the canonical form with exactly `fsp` fraction digits |
| `float`, `double`, `json` (MySQL), unknown types | — | any non-null value (engine-formatted / normalised text is not provable) |
| `binary`, `varbinary`, `*blob`, `bit`, spatial | — | the column itself, if allowlisted |

Blocker codes (in `manifest.blockers` and `report.json`): `SOURCE_SNAPSHOT_BINARY_COLUMN` (an allowlisted column
has a binary type), `SOURCE_SNAPSHOT_VALUE_UNREPRESENTABLE` (count of rows with an unprovable cell),
`SOURCE_SNAPSHOT_KEY_INVALID` (null or duplicate primary key). Known, documented limits: the `utf8` charset
alias is reported as dumped (not rewritten to `utf8mb3`); `INSERT IGNORE` / `ON DUPLICATE KEY` semantics are
not replayed (a repeated key is reported as `SOURCE_SNAPSHOT_KEY_INVALID`, never resolved); a column added by
`ALTER TABLE … ADD COLUMN` is not tracked by the parser.

### Sensitivity
The snapshot holds personal cells the converter's own records minimise — `user.number` (phone),
`invoice.note`, `invoice.refral`, `username` — because NEXA's importer reads them (it classifies the phone and
never writes it; `number` is outside the v1 fingerprint). They exist only inside the encrypted payload and the
job's `stage/` directory (`DATA_PROTECTION.md` §5, §6). No secret column is ever included.

## Column carry (*added in 1.5.0*, converter 0.8.0)

Owner goal: migrate 100 % of the Mirza database, nothing silently dropped. Every column of every catalogued table
now has exactly one **disposition** in `reports/coverage.json` → `column_dispositions` (`src/mirza2nexa/catalogue.py`):

| Disposition | Meaning |
|---|---|
| `stored` | staged for the table's own converter module (as in 1.4.0) |
| `carried` | staged separately and written verbatim (value-level redaction, `DATA_PROTECTION.md` §4) by `modules/carry.py` |
| `minimised` | only a parse-time derivative is staged (`ip_family`: `ipv4`/`ipv6`; `json_keys`: top-level key names) — the raw value never touches disk |
| `secret_dropped` | never staged; named in `reports/manual-reconfiguration.json` so the owner can re-enter it in NEXA |
| `excluded` | a column of an EXCLUDED table (`card_number` has none left: both columns are secret) |

`unaccounted_columns` lists columns without a disposition; it is always empty and a non-empty list blocks the
package (`COLUMN_UNACCOUNTED`). `unmapped_columns` is kept and always empty: an **undeclared** column (a newer fork)
is carried unless its *name* looks like a credential (`sensitivity.column_name_is_secret`) or it is an opaque value
blob of a settings-like table; those are `secret_dropped` with `basis: name_heuristic`. The per-column decisions with
evidence are in `MAPPING.md` §"Column carry".

**Isolation.** Carried columns are staged in their own staging table, not in `stored_columns`, so every record of
1.4.0, the source snapshot, the source fingerprint and `import_id` are byte-identical for the same backup. The carried
data is therefore NOT part of the source fingerprint; each carry record has its own `provenance.row_checksum`.

**Supplement record** (`records/archive/<table>_carried.jsonl`; `records/configuration/…` for CONFIGURATION tables):
```json
{"record_type": "legacy_archive_row", "schema": "m2n.carried_columns.v1",
 "idempotency_key": "legacy:carried:user:7000000001", "class": "OPERATIONAL", "source_table": "user",
 "source_pk": "7000000001", "carry": "supplement", "supplements": "the table's own records (same source_table / source_pk)",
 "fields": {"step": "…", "first_name": "…", "…": "…"},
 "minimised": {}, "secret_columns_dropped": ["token", "codeInvitation", "bottype"],
 "customer": {"source_user_id": "7000000001", "telegram_user_id": "7000000001", "relation": "CUSTOMER_IMPORTED", "customer_decision": "IMPORT"},
 "applies_to_live_state": false, "provenance": {"source_table": "user", "source_pk": "7000000001", "source_fingerprint": "…", "row_checksum": "…"}}
```
* `customer` is present when the table names a Telegram user (`user.id`, `invoice.id_user`, `service_other.id_user`,
  `reagent_report.user_id`, `admin.id_admin`, `botsaz.id_user`, `ai_business_chat.user_id|id_user|chat_id|telegram_id`,
  and `id_user`/`user_id` of any other table that gains a carried column).
* `invoice: {invoice_key, in_backup}` is present for `invoice` and `service_other` (its `id_invoice` column).
* Link identifiers (key, customer, invoice columns) are not value-redacted; every other carried value is.
* Formerly EXCLUDED tables `admin`, `botsaz`, `logs_api`, `topicid`, `global_hidden_gateways`, `ai_business_chat` are
  ARCHIVE (`module: carry`): one record per row in `records/archive/<table>.jsonl` (`carry: "table"`,
  `schema: mirza.<table>.v1`, key `legacy:<table>:<pk>`), counted as `archived`, so NEXA's table accounting
  (`nxpkg-table-accounting.ts`) classes them `ARCHIVE` with no change.

**What NEXA does with it (nexa-bot, read-only review).** `legacy-history/application/record-map.ts` accepts every
new file without change (`HISTORY_DIRECTORIES` `records/archive/` and `records/configuration/`, file name
`[A-Za-z0-9_]{1,64}.jsonl`, known `record_type`, `legacy:` key, no live flag) and stores each record in
`legacy_history_records`. Two gaps remain on the NEXA side; neither blocks an import:
1. **Customer link**: `USER_ID_PATHS` has no entry for `archive_row`, so these records are stored with
   `legacy_user_id = NULL` and Customer 360 (`listForCustomer`) does not list them. One line makes it so:
   `archive_row: [['customer', 'telegram_user_id']]` in `USER_ID_PATHS` (no migration: the column exists).
2. **Display without PII permission**: `redaction.ts` keeps only `class` for `archive_row` /
   `configuration_row`; with `legacy.invoices.pii.view` the whole record is shown (audited). Adding non-personal paths
   (e.g. `carry`, `schema`) to that allowlist is optional.

## Companion file: `ownership-decisions.json` (not inside the package)

The package is immutable once written, so administrator ownership decisions taken afterwards in the
bulk ownership review (`OWNERSHIP_REVIEW.md`) travel as a separate, authenticated companion file
(`schema: m2n.ownership_decisions.v1`). It carries `import_id`, `source_fingerprint`, `sealed`, the
audit-chain head, totals per class (`PROVEN`, `ADMIN_APPROVED_UNVERIFIED`, `PENDING`, `REJECTED`, `QUARANTINED`)
and one entry per service (`key`, `invoice_key`, `class`, `basis`, `review_state`, `batch_id`, `binding`,
`stale`). `authentication.mac` is HMAC-SHA256 over the canonical JSON of the document without
`authentication`, keyed with HKDF-SHA256 of the package secret (`info = nxpkg-v1/ownership-decisions`).
Importer rules: `NEXA_IMPORTER_DESIGN.md` §8.1. `ADMIN_APPROVED_UNVERIFIED` is never proof of ownership.

## Manifest additions (1.1.0)

These fields are added on top of the 1.0.0 manifest:
* `readiness`
* `blockers`
* `money`, holding `declared_unit`, `currency` and `rescaled: false`
* `record_counts`
* `compatibility.verified_by_importer: false`

`verified_by_importer` stays `false` until the NEXA importer described in `NEXA_IMPORTER_DESIGN.md` (background:
`NEXA_IMPORTER_PLAN.md`) exists and has passed its acceptance tests.

The manifest's `package_schema_version` is set by the converter (`nxpkg/manifest.py`); this document describes
content contract 1.5.0.

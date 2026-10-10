# NEXA evidence: MirzaBot (legacy) MySQL schema → NEXA

> Compiled by the Architecture & Source Analysis agent from a read-only checkout of
> `Mhoseinshah1/nexa-bot` @ `9e604def`. Every rule implemented in `src/mirza2nexa/mapping/rules.py`
> cites the same lines. Aggregate figures quoted from nexa-bot are its own historical notes, not data
> this converter has seen.

Source repo: `/home/user/mhoseinshah1/nexa-bot` @ `9e604def867fb9717408f023c3ea05426732d447` (read-only).
All paths below are relative to that repo. `file:line` citations; "NB" = not in nexa-bot / unknown.

## 0. Ground truth about what NEXA actually knows

- NEXA has **never seen the real Mirza database**. "Nothing in this repository has seen the real
  Mirza database. The synthetic fixture's inventory ... is NOT evidence." (`docs/legacy-migration/table-inventory.md:3-6`).
  "Every real-data step below is **NOT RUN**" (`docs/legacy-migration/mirza-full-migration.md:162-167`).
  The seven SQL evidence queries have **no results recorded** (`docs/legacy-migration/sql-evidence.md:3-7`).
- Column knowledge comes from three sources, each cited by NEXA:
  1. Public MirzaBot `mahdiMGF2/botmirzapanel` @ `92c0ed06…` `table.php` (`apps/api/src/modules/platform/legacy-importer/application/source-port.ts:49-51`; `tests/fixtures/legacy/synthetic-legacy.ts:11-14`).
  2. Public fork `mahdiMGF2/mirza_pro` @ `8e551ecf` (`db/tables/product.php`, `db/tables/invoice.php`; version 0.6.0) (`apps/api/.../products-read-set.ts:28-30`; `invoice-archive-read-set.ts:23-26`; `docs/legacy-import-metadata.md:81-84`). Backup format from `mirza_pro` revision `e4966ff` `cronbot/backupbot.php` (`tests/fixtures/legacy/archive/make-fixtures.php:6-11`).
  3. An earlier (non-repo) audit's SQL queries Q1–Q7 that name extra columns the production archive has (`agent`, `code_panel`, `code_product`, `is_test`, `is_custom`, `time_unit`) (`tests/fixtures/legacy/synthetic-legacy.ts:15-17`; `docs/legacy-import-metadata.md:110-113`).
- **Caveat recorded by NEXA**: the deployed Mirza is NOT any public revision read — the archive's
  `invoice` carries columns no public revision declares (`docs/legacy-import-metadata.md:110-114`;
  `packages/contracts/src/legacy-import.ts:240-244`).
- Historical staging baselines quoted (never expected values): 197,700 users (owner) / 197,461 numeric user ids; 127,611 invoices; 64 products; 38 negative balances; 60,217 positive; `SUM(user.Balance)=2,874,365,519` Toman (`docs/open-questions.md:3300-3304`; `docs/migration-opening-balance.md:155-157`).

---

## 1. Mirza source tables NEXA knows about

NEXA's table catalogue classifies exactly 3 Mirza tables (+ its own synthetic marker); every
other table is `UNCLASSIFIED` and fails closed (`packages/contracts/src/legacy-inventory.ts:22-26, 57-87, 89-100`).

| table | class | evidence |
|---|---|---|
| `user` | SUPPORTED | `packages/contracts/src/legacy-inventory.ts:60-66` |
| `invoice` | SUPPORTED | `packages/contracts/src/legacy-inventory.ts:67-73` |
| `product` | SUPPORTED | `packages/contracts/src/legacy-inventory.ts:74-80` |
| `nexa_synthetic_fixture` | SUPPORTED (NEXA marker, never in a real dump) | `legacy-inventory.ts:81-86`; `source-port.ts:121-122` |
| `setting`, `marzban_panel`, `Payment_report`, `textbot`, `admin`, `channels`, `Discount`, gift/affiliate/support tables | **UNCLASSIFIED / unknown** (names only, "appear only as refusal probes") | `docs/open-questions.md:3266-3272`; `legacy-inventory.ts:22-26`; `tests/unit/legacy-table-classification.test.ts:51-68`; `tests/unit/legacy-import-rules.test.ts:156-160` |
| `wallet_transaction` | named, **never replayed** (user.Balance is migration-time truth) | `docs/migration-opening-balance.md:19`; `scripts/legacy-rehearsal-source.sql:28` |

Table names are matched **case-sensitively** (`User`, `INVOICE`, `user ` are UNCLASSIFIED) (`legacy-inventory.ts:52-55`; `tests/unit/legacy-table-classification.test.ts:56-58`).

### 1.1 Frozen v1 import read set (the core column contract)

`apps/api/src/modules/platform/legacy-importer/application/source-port.ts:36-83`:

```ts
export const IMPORT_READ_SET_V1 = deepFreeze({
  fingerprintVersion: 'legacy-source-fingerprint:v1',
  tables: ['user', 'invoice', 'product'],
  primaryKeys: { user: 'id', invoice: 'id_invoice', product: 'id' },
  requiredColumns: {
    user: ['id', 'Balance', 'limit_usertest'],
    invoice: ['id_invoice','id_user','username','Status','is_test','code_panel','code_product',
              'Volume','Service_time','time_unit','is_custom','price_product'],
    product: ['id', 'code_product'],
  },
  optionalColumns: { user: ['agent', 'number', 'username'], invoice: [], product: ['agent'] },
  notFingerprinted: ['user.number'],
} as const);
```

Missing required column → `SOURCE_SCHEMA_MISSING_COLUMN` (`source-port.ts:46-48, 239-240`). Rows are read as text (`CAST(… AS CHAR)`) ordered by `ORDER BY CAST(pk AS BINARY)` (`source-port.ts:11-14, 256-259`; `docs/legacy-migration/importer.md:181-182`).
The archive inspector mirrors the required list (`scripts/legacy-archive-inspect.mjs:77-98`).

### 1.2 `user`

Synthetic DDL (NOT evidence; public source declares `Balance` and `limit_usertest` as **`int`**, fixture uses varchar on purpose — `tests/fixtures/legacy/synthetic-legacy.ts:20-24`):

```sql
CREATE TABLE `user` (            -- tests/fixtures/legacy/synthetic-legacy.sql:4-13
  `id` varchar(500) NOT NULL PRIMARY KEY,
  `limit_usertest` varchar(32) NULL,
  `Balance` varchar(64) NULL,
  `number` varchar(2000) NULL,
  `User_Status` varchar(500) NULL,
  `agent` varchar(100) NULL,
  `affiliates` varchar(100) NULL,
  `username` varchar(1000) NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

| column | type evidence | PK | used by NEXA for | citation |
|---|---|---|---|---|
| `id` | `varchar` PK in public source; Telegram user id | **PK** | customer identity (Telegram id) | `synthetic-legacy.ts:12-14,41`; `legacy-import.ts:222-230` |
| `Balance` | public `int` (fixture varchar); real type UNKNOWN (int vs decimal) | | wallet opening / legacy debt | `synthetic-legacy.ts:21-22`; `docs/open-questions.md:3280-3281`; `sql-evidence.md:334-337` |
| `limit_usertest` | public `int` | | trial-eligibility decision | `source-port.ts:54`; `sql-evidence.md:250-259` |
| `agent` | optional; values `f`,`n`,`n2` (tiers) | | counted only (reseller flag) | `source-port.ts:77`; `docs/open-questions.md:3341-3343` |
| `number` | optional; `none` (older `0`) when absent | | classified ABSENT/VALID/INVALID, **never written**, not fingerprinted | `decisions.ts:43-53`; `source-port.ts:82`; `docs/open-questions.md:3227-3231` |
| `username` | optional; Telegram username | | profile metadata only if `^[A-Za-z][A-Za-z0-9_]{4,31}$` after stripping `@` | `decisions.ts:154-159` |
| `User_Status` | exact `Active` / `block` | | via separate `user-status` read set → ACTIVE/BLOCKED/UNKNOWN | `read-set.ts:395-399`; `legacy-wallet-debt.ts:118-135` |
| `affiliates` | possibly referrer user id (UNKNOWN; Q3) | | **not read** by importer; referral import out of Phase 1 | `sql-evidence.md:304-316` |

### 1.3 `invoice`

```sql
CREATE TABLE `invoice` (         -- tests/fixtures/legacy/synthetic-legacy.sql:26-42 (synthetic)
  `id_invoice` varchar(200) NOT NULL PRIMARY KEY,
  `id_user` varchar(200) NULL,
  `username` varchar(200) NULL,
  `Service_location` varchar(200) NULL,
  `time_sell` varchar(200) NULL,
  `name_product` varchar(200) NULL,
  `price_product` varchar(200) NULL,
  `Volume` varchar(200) NULL,
  `Service_time` varchar(200) NULL,
  `Status` varchar(200) NULL,
  `code_panel` varchar(200) NULL,
  `code_product` varchar(200) NULL,
  `is_test` varchar(10) NULL,
  `is_custom` varchar(10) NULL,
  `time_unit` varchar(50) NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
```

- PK `id_invoice varchar(200) PRIMARY KEY` in **every** public revision (botmirzapanel 535 commits, mirza_pro 508) (`docs/legacy-import-metadata.md:79-84`).
- Key generator: `bin2hex(random_bytes(2|4))`, optionally prefixed by `rand(1000000,9999999)` → regex `^([1-9][0-9]{6})?([0-9a-f]{4}|[0-9a-f]{8})$` (4/8/11/15 chars) (`docs/legacy-import-metadata.md:86-103`; `packages/contracts/src/legacy-import.ts:257-260`).
- Archive read-set optional columns (when present): `Service_location` (panel name), `time_sell` (`time()`), `name_product`, `note` (fork; customer-typed config name, PII), `refral` (referrer Telegram id, PII), `time_cron`, `notifctions` (fork `{"volume","time"}` flags) (`invoice-archive-read-set.ts:23-32, 83-91`).
- **Never read (secrets)**: `user_info` (fork stores panel `subscription_url`), `uuid` (account UUID), `bottype` (reseller sub-bot BOT TOKEN) (`invoice-archive-read-set.ts:34-43, 56-60`).
- Column meanings used: `id_user` → `user.id` (join; `sql-evidence.md:255,376`); `username` = provider/panel account username (matched lowercased printable-ASCII ≤128 chars, `legacy-service-matching.ts:105-110`); `code_panel` = panel CODE e.g. `bac6`, not a credential (`legacy-rehearsal-source.sql:104-105`; `sql-evidence.md:183`); `code_product` → `product.code_product`; `is_test` 1/0; `is_custom` 1/0; `Volume` GB; `Service_time` days; `time_unit`; `price_product` historical price.

### 1.4 `product`

```sql
CREATE TABLE `product` (         -- tests/fixtures/legacy/synthetic-legacy.sql:65-73 (synthetic)
  `id` int(6) unsigned NOT NULL AUTO_INCREMENT PRIMARY KEY,
  `code_product` varchar(200) NULL,
  `name_product` varchar(2000) NULL,
  `price_product` varchar(2000) NULL,
  `Volume_constraint` varchar(2000) NULL,
  `Service_time` varchar(200) NULL,
  `agent` varchar(100) NULL
) ...
```

- PK `id` (public source) (`source-port.ts:43,49-51`).
- Products read set (`products-read-set.ts:20-35, 40-64`): required `id`, `code_product`; optional
  botmirzapanel: `name_product`, `price_product`, `Volume_constraint`, `Location`, `Service_time`, `Category`;
  mirza_pro: `agent`, `note`, `data_limit_reset`, `one_buy_status`, `category` (lowercase), `hide_panel`.
  **Never read**: `inbounds`, `proxies` (may hold proxy/credential-like data).
- Synthetic review variant DDL extras: `Location varchar(1000)`, `Category varchar(600)`, `note text`, `one_buy_status varchar(20)`, `hide_panel text` (`synthetic-legacy.ts:91-98`).
- Field mapping used by review: title←`name_product`, trafficBytes←`Volume_constraint`, durationDays←`Service_time`, historicalPrice←`price_product` (`apps/api/src/modules/commerce/legacy-product-review/domain/legacy-product-facts.ts:34-39`).

### 1.5 Sample synthetic values (NOT evidence, but show expected spellings)

`synthetic-legacy.sql:14-24, 43-63, 74-75`: `User_Status='Active'`, `agent` in `f|n|n2`, `number='none'` or `989121234567`, `Balance` `'50000'|'-20000'|'12.5'`, invoice `Status` `active|removed`, `time_sell='1700000000'`, `time_unit` `''|'month'`, `code_panel` `rp1|''|NULL`.

---

## 2. Money units

**Decision: Mirza amounts are Toman → NEXA `IRT` with exponent 0 (1 Toman = 1 minor unit).** It is an **owner decision**, not data evidence:

- Owner decision 7 (2026-10-07): "the owner decided Mirza prices are Toman" (`docs/legacy-product-review-design.md:309-314`; `packages/contracts/src/legacy-product-review.ts:16-18, 101-102`; `legacy-invoice-archive.ts:138-139`).
- Balance: "the legacy balance is Toman, and NEXA's IRT has zero minor digits, so one Toman is one minor unit. The importer refuses any other selling currency" (`packages/contracts/src/legacy-wallet-debt.ts:44-49`); "`user.Balance` (Toman) … `IRT` has 0 minor digits, so Toman = minor" (`docs/migration-opening-balance.md:8-15`); `decisions.ts:87` "Signed legacy `Balance`, Toman = IRT minor units". Audit is BLOCKED if currency is not IRT (`docs/legacy-migration/importer.md:749-751`). DB CHECK `amount_minor > 0 AND currency = 'IRT'` (`apps/api/drizzle/0226_legacy_wallet_debts.sql:23`).
- Research support: user wallet "exactly 1, single Toman balance" VERIFIED (`docs/research/mirzabotusermanagementinvestigationcomplete/user-management-knowledge/entities-relations.md:27`); admin balance adjust prompts a Toman amount (`…/business-rules.md:97-103`); customer purchase buttons `{price}تومان` (`docs/research/mirzabotstoresettingsfinalv2/store-settings-knowledge/business-rules.md:42`).
- Counter-evidence (why it was an open question): one card-to-card template says تومان, its twin says ریال for the same `{price}` (`docs/research/mirzabotbottextmanagementinvestigationcomplete/bot-text-web-crossmap.md:35-39`; `docs/adr/0005-money-and-rate-snapshots.md:28-33`). Telegram Stars "store the resulting IRR amount" log line still says تومان (`docs/research/mirzabottelegramloggroupinvestigationcomplete/telegram-log-group-knowledge/business-rules.md:58`).
- Status: `price_product` is **PARTLY DECIDED** — unit decided (Toman), but numeric shape unknown: non-integer, grouped `150,000`, Persian digits → `NOT_A_NUMBER`, kept raw (`docs/open-questions.md:3328-3331, 3380-3382`; OQ-MZ-INV-07 `docs/open-questions.md:3292-3294` still lists "may be Toman or Rial").
- Balance type (int vs decimal) UNKNOWN (OQ-MZ-INV-03, `docs/open-questions.md:3280-3281`). A fractional Balance is refused (`balance_fractional_users` expected 0, `scripts/legacy-rehearsal-source.sql:49-53`).

**Parsing grammars** (converter should replicate):
- Balance: `trim`, `^-?[0-9]{1,19}$` → bigint, else null (BALANCE_UNREADABLE); `|value| > PAYMENT_AMOUNT_MAX_MINOR (1_000_000_000_000)` → BALANCE_OUT_OF_RANGE (`decisions.ts:96-102, 121-132`; `packages/contracts/src/payment.ts:239`).
- Price: `trim`, `^[0-9]{1,19}$`, ≤ `MAX_MONEY_AMOUNT_MINOR` (2^63-1) else `NOT_A_NUMBER`/`OUT_OF_RANGE`; zero kept (`legacy-product-facts.ts:138-153`; `packages/contracts/src/money.ts:38`).

**NEXA Money type**: `{ amountMinor: bigint; currency: CurrencyCode }`; `CURRENCY_CODES = ['IRT','IRR','USD','EUR','USDT']`; exponents IRT 0, IRR 0, USD 2, EUR 2, USDT 6; sales currencies `['IRT','IRR']`; non-integer rejected; wire format decimal string (`packages/contracts/src/money.ts:16-26, 55-64, 80-98`; `docs/adr/0005-money-and-rate-snapshots.md:7-10, 41`). DB: `bigint` minor + `currency` text (`ADR-0005:7-8`).

---

## 3. Wallet balance & debt semantics

Mirza side:
- Column `user.Balance`; negative balances exist ("Negative balance is a first-class, queryable state", list view `لیست کاربرانی که موجودی منفی دارند`) (`docs/research/mirzabotusermanagementinvestigationcomplete/user-management-knowledge/business-rules.md:39-46`). A negative-balance ceiling `سقف منفی شدن حساب` exists for `n2` on web (`…/web-admin-crossmap.md:19`); whether balances can actually go below zero via normal flow is UNK-UM-005 (`…/unknowns.md:14`).
- Rehearsal aggregates: `balance_sum`, `balance_positive_*`, `balance_zero_users`, `balance_negative_*`, `balance_fractional_users`, `balance_null_users` (`scripts/legacy-rehearsal-source.sql:29-55`).

NEXA side (decided):
- Positive → one ledger CREDIT, reason `MIGRATION_OPENING_BALANCE`, reference `legacy:opening:<telegram_user_id>` (= idempotency key), currency = tenant sales currency; zero → no entry (`ZERO_NO_ENTRY`); existing customer: additive (`docs/migration-opening-balance.md:8-27, 113-124`). DB: partial unique `(tenant_id, customer_id) WHERE reason='MIGRATION_OPENING_BALANCE'` and shape check `left(reference,15)='legacy:opening:'` (`apps/api/drizzle/0186_migration_opening_balance.sql:2-5`).
- Negative → **legacy debt, never a ledger entry, never collected** (owner decision 6, 2026-10-07) (`docs/migration-opening-balance.md:35-66`; `packages/contracts/src/legacy-wallet-debt.ts:3-17`). Map row stays `IMPORTED/CUSTOMER` with warning `NEGATIVE_BALANCE` (`docs/migration-opening-balance.md:48`; `decisions.ts:146-150`).
- Plan outcomes: `POST`, `ALREADY_POSTED`, `ZERO_NO_ENTRY`, `RECORD_DEBT`, `DEBT_ALREADY_RECORDED`, `PRIOR_DEBIT_OPENING`, `CONFLICT` (`docs/legacy-migration/importer.md:362-370`).
- Blocked users keep money (credit or debt) recorded (`importer.md:334-354`).

`legacy_wallet_debts` (`apps/api/drizzle/0226_legacy_wallet_debts.sql:1-26`): `id uuid`, `tenant_id`, `customer_id`, `legacy_user_id text` (`^[1-9][0-9]{0,19}$`), `amount_minor bigint` (>0, MAGNITUDE), `currency` (='IRT'), `source_fingerprint` (sha256 hex), `row_checksum` (sha256 hex, `user:v1`), `run_id`, `synthetic bool`, `state` (`PENDING_REVIEW|ACKNOWLEDGED|WAIVED`), `decision_reason` (1-500), `decided_by_admin_id`, `decided_at`, `version int ≥1`, `recorded_at`, `updated_at`; UNIQUE `(tenant_id, customer_id)` and `(tenant_id, legacy_user_id)`.

Contract (`packages/contracts/src/legacy-wallet-debt.ts`): states `:20-29`; decisions `ACKNOWLEDGED|WAIVED` `:32-36`; currency const `'IRT'` `:49`; view schema fields `id, customerId, legacyUserId, amountMinor (string), currency, state, sourceFingerprint, rowChecksum, runId, synthetic, decisionReason, decidedByAdminId, decidedAt, version, recordedAt, updatedAt` `:192-218`; decide/reopen requests `{idempotencyKey (8-255), expectedVersion ≥1, decision, reason ≤500 no Cc}` `:178-186, 255-267`. User outcome closure `LEGACY_USER_OUTCOMES` `:91-115`; balance change classes `:142-160`.

Idempotency keys summary: opening `legacy:opening:<tg>`; debt unique `(tenant, customer)` & `(tenant, legacy_user_id)`; rerun with different figure → `platform.idempotency_payload_mismatch` (`docs/migration-opening-balance.md:52-57, 118-124`). Row checksum `user:v1` = sha256 over `[id, Balance, limit_usertest, had_trial ('1'/'0'), username]` (`source-snapshot.ts:348-358`; `importer.md:191-192`).

---

## 4. Invoices / orders / services

Columns: §1.3. Live status vocabulary (exact compare):

```ts
export const LEGACY_LIVE_STATUSES = ['active','disabled','disabledn','disablebyadmin','end_of_volume'] as const;
```
(`source-port.ts:112-119`; same set in every query, `scripts/legacy-rehearsal-source.sql:68`). Non-live statuses UNKNOWN (fixture uses `removed`; `end_of_time`, `sendedwarn`, `send_on_hold`, `unpaid` are NOT live) (`docs/open-questions.md:3287-3288, 3391-3394`).

Parsers:
- `is_test` / `is_custom`: trimmed `1`/`0` only, else invalid (`invoice-archive-row.ts:172-176`; `legacy-shape.ts:195-200`).
- `time_sell`: ASCII digits → unix seconds in [1420070400, 4102444800) else `FORMAT_UNKNOWN`/`OUT_OF_RANGE`; no timezone guessing (`apps/api/src/modules/platform/legacy-invoice-archive/domain/invoice-archive-row.ts:144-170`). PARTLY EVIDENCED (`docs/open-questions.md:3374-3379`).
- `code_panel`/`code_product`: trimmed, blank → NULL (`decisions.ts:262-266`; `invoice-archive-row.ts:178-180`).
- Shape key (`legacy-shape.ts:65-117`): `Volume` decimal GB `^(0|[1-9][0-9]{0,8})(\.[0-9]{1,2})?$`, 1 GB = 1 GiB (`packages/contracts/src/traffic-input.ts:36, 49-52`; `docs/legacy-migration/hidden-legacy-products.md:42`), max 1,024,000 GB; zero refused; `time_unit` ∈ `'' | d | day | days` (case-insensitive); `Service_time` `^[0-9]{1,6}$`, 1..3650 days (`packages/contracts/src/catalog.ts:253-254`); key = `legacy-shape:v1:` + JSON `[codePanel|null, bytes, days, 0|1]`. Refusals: `CODE_PANEL_INVALID, VOLUME_INVALID, VOLUME_ZERO, DURATION_INVALID, DURATION_ZERO, TIME_UNIT_UNKNOWN, IS_CUSTOM_INVALID` (`legacy-shape.ts:50-58`). `price_product` never part of the shape (`legacy-shape.ts:21-25`).

Service-candidate decision order (`decisions.ts:277-372`, categories `:179-217`): `INVOICE_KEY_INVALID` → `TEST_INVOICE_SKIPPED` (is_test=1) → `INVALID_SOURCE_ROW` (is_test≠0) → `ORPHAN` (id_user NULL / not in user) → `CUSTOMER_NOT_IMPORTED` → matcher (`TEST_PANEL_SKIPPED, INVALID_USERNAME, INVENTORY_INCOMPLETE, NO_PANEL, PROVIDER_MISSING, AMBIGUOUS_PANEL, PANEL_UNMAPPED, USERNAME_CASE_COLLISION`) → `UNSUPPORTED_SHAPE` (is_custom invalid / shape refused) → `PRODUCT_UNRESOLVED` → `ADOPTION_ELIGIBLE`; then ownership rule `AMBIGUOUS_OWNERSHIP` (`importer.md:380-391`). Product path: named non-custom `code_product` present in `product` → NAMED_PRODUCT (needs explicit map), else HIDDEN_SHAPE (`decisions.ts:338-364`).

Map decisions (`decisions.ts:409-419`): live trial `SKIPPED/HISTORY_NOT_IMPORTED`; ORPHAN & CUSTOMER_NOT_IMPORTED → `MANUAL_REVIEW/CUSTOMER_MISSING`; UNSUPPORTED_SHAPE; PRODUCT_UNRESOLVED → `PRODUCT_MAPPING_UNRESOLVED`; AMBIGUOUS_OWNERSHIP → `CONFLICTING_EXISTING_ENTITY`. `NO_PANEL` → `MANUAL_REVIEW/PANEL_UNMAPPED` (`importer.md:386-387`).

Ownership: invoices of different legacy owners on one account → all `AMBIGUOUS_OWNERSHIP`; several invoices of ONE owner on one account → first in key byte order adopts, rest AMBIGUOUS_OWNERSHIP (OQ-LSR-03 unknown whether renewals create new invoices) (`docs/open-questions.md:3469-3473`; `legacy-service-review.ts:62-67`).

Orphans: `ORPHAN_OWNER` archived, "never given an owner" (`legacy-invoice-archive.ts:39-43`; `importer.md:636`). Q7 counts them (`sql-evidence.md:371-380`).

Invoice archive classes, first match wins (`packages/contracts/src/legacy-invoice-archive.ts:32-53`): `KEY_SHAPE_UNRECOGNISED, TEST, TEST_FLAG_INVALID, ORPHAN_OWNER, NOT_LIVE, NO_PANEL, LIVE_CANDIDATE`. Product ref `NONE|NAMED|NOT_IN_PRODUCT_TABLE` (`:58-65`). Every invoice row (every status) archived as history; never order/payment/revenue (`importer.md:546-552`). Archive row view fields (`legacy-invoice-archive.ts:213-244`). PII columns `id_user, username, refral, note` (`:136`).

Adopted services (P6): an `orders` row `purpose=NEW_SERVICE, origin=LEGACY_ADOPTION, PAID`, totals 0 — never revenue; runtime facts come from the panel, never from the invoice (`docs/migration-order-origin.md:8-27`; `docs/migration-p6-service-adoption.md:12-27`). Renewals use current NEXA tariff, never legacy price (`packages/contracts/src/legacy-migration.ts:187-197`).

---

## 5. Products / categories / panels

- `product` columns: §1.4. `Location` meaning UNKNOWN (panel name / code_panel / list / text) (OQ-LPR-04, `docs/open-questions.md:3332-3333`). `Volume_constraint`/`Service_time` units UNKNOWN, review proposes GB/days; `0` = `ZERO_MEANING_UNKNOWN` (OQ-LPR-02, `:3324-3327`). No status/test column in either public source (OQ-LPR-08, `:3345-3349`). Duplicate `code_product` → `CODE_DUPLICATED`, nothing parsed, only reject (OQ-LPR-05, `:3334-3336`; `legacy-product-review.ts:94-99`).
- Categories: only `Category`/`category` columns, kept verbatim, no table known (`products-read-set.ts:26-30`). Mirza category table: NB/unknown.
- Panels: only `invoice.code_panel` codes; panel tables (`marzban_panel` etc.) UNCLASSIFIED/unknown (`docs/open-questions.md:3266-3270`). Panel mapping is an explicit operator JSON file `nexa-legacy-panel-map/v1` with `panels[{codePanel,panelId}]`, `testPanels`, `missingPanels`, `unresolvedPanels[{codePanel, reason: OWNER_DECIDES_LATER|DECOMMISSIONED_PANEL|UNKNOWN_ORIGIN}]`, `productionPanels`, `products[{codeProduct,productId}]` (`docs/legacy-migration/importer.md:255-303`). Panel `8255` explicitly never guessed (`docs/legacy-migration/service-review.md:29-39`).
- Product review contract (`packages/contracts/src/legacy-product-review.ts`): states `PENDING_REVIEW, APPROVED_EXISTING, APPROVED_NEW, REJECTED, SOURCE_CHANGED` `:22-36`; parse notes `ABSENT, EMPTY, NOT_A_NUMBER, OUT_OF_RANGE, ZERO_MEANING_UNKNOWN, SOURCE_CONFLICT` `:78-91`; view schema `:168-208` (codeProduct, legacyProductId, facts[], factsChecksum, title, trafficBytes (string), durationDays, historicalPriceRaw/Minor/Currency='IRT', parseNotes, liveInvoiceCount, …); approve-new request (no price/panel/category) `:259-275`. Facts checksum `sha256(JSON{v:'legacy-product-facts:v1', rows})` (`legacy-product-facts.ts:79-84`). DB `legacy_product_reviews` (`apps/api/drizzle/0221_legacy_product_reviews.sql:1-29`). Hidden shapes table `legacy_product_shapes` (`0181_legacy_product_shapes.sql:1-16`), tariff statuses/reasons (`legacy-migration.ts:170-198`).
- Mirza product tiering: product `agent` = reseller group `f/n/n2` (`docs/open-questions.md:3341-3343`; Q5 `sql-evidence.md:345-351`).

---

## 6. Resellers / agents

- `user.agent` optional column; tiers `f` (normal), `n`, `n2` (resellers) — research VERIFIED_BY_UI (`docs/research/mirzabotusermanagementinvestigationcomplete/user-management-knowledge/business-rules.md:70-77, 105-113`); per-tier settings e.g. `f` 50,000 / `n` 100,000 / `n2` 20,000 Toman (`docs/research/mirzabotgeneralsettingsfollowupcomplete/general-settings-followup-knowledge/business-rules.md:86-88`).
- NEXA `legacyIsAgent`: agent iff trimmed-lowercased value not in `'' , 'f', '0', 'false'` (`decisions.ts:161-171`).
- Decision (OQ-LWD-03): agents imported as **ordinary customers** — no reseller row, tier or credit; agent value not persisted (`importer.md:356-360`; `docs/open-questions.md:3422-3425`). Q4 = agent × negative balances (`sql-evidence.md:322-343`). Reseller agent products are review rows like any other (OQ-LPR-07).
- Reseller sub-bots: `invoice.bottype` holds a sub-bot BOT TOKEN in the fork (never read) (`invoice-archive-read-set.ts:40-41`).

---

## 7. Identity, duplicates, decision/rule codes

- `user.id` must satisfy `telegramUserIdSchema` AND `^[1-9][0-9]{0,19}$` (no leading zero) (`decisions.ts:104-109`; `legacy-import.ts:257-259`); else `INVALID_IDENTITY` → no customer, no map key (`SKIPPED_INVALID_IDENTITY`) (`legacy-wallet-debt.ts:96-97`).
- Existing NEXA customer matched by `(tenant, telegram_user_id)`, never modified (`EXISTING_CUSTOMER`) (`importer.md:318-325`).
- `DUPLICATE_SOURCE_ID`: Telegram id on >1 `user` row → nothing imported, ONE review row `INVALID_SOURCE_ROW`, order-free checksum (`importer.md:327-332`; `plan.ts:406`).
- `User_Status`: exactly `Active`→ACTIVE, `block`→BLOCKED, else UNKNOWN → manual review `STATUS_UNKNOWN` (`legacy-wallet-debt.ts:129-135`; `decisions.ts:133-136`; UBR-018 `docs/research/…/user-management-knowledge/business-rules.md:189-198`).
- User decision codes: `INVALID_IDENTITY`, `MANUAL_REVIEW{BALANCE_UNREADABLE | BALANCE_OUT_OF_RANGE | DUPLICATE_SOURCE_ID | STATUS_UNKNOWN}`, `IMPORT{NEW|EXISTING, blocked, POSITIVE|ZERO|NEGATIVE}` (`decisions.ts:59-94`).
- Trial decisions (`legacy-migration.ts:212-218`; `sql-evidence.md:409-421`): `LEGACY_LIMIT_UNREADABLE` (limit not `^-?[0-9]{1,10}$` or |v|>int4) / `LEGACY_NO_TRIALS` (≤0) / `LEGACY_TRIAL_CONSUMED` (≥1 & any `is_test=1` invoice in ANY status) / `INHERIT_NEXA_POLICY` / `KEPT_EXISTING_OVERRIDE` (`apps/api/src/modules/commerce/trials/application/legacy-trial-eligibility.ts:36-42`).
- Map statuses `IMPORTED|SKIPPED|MANUAL_REVIEW|FAILED`; reason codes (18) and review reason codes (12); review states/resolutions (`packages/contracts/src/legacy-import.ts:68-215`).
- Invoice/service outcomes (22) `LEGACY_SERVICE_OUTCOMES` (`legacy-service-review.ts:26-78`); adoptable `:96-106`; panel-required `:113-117`; review states `OPEN, ACKNOWLEDGED, KEPT_AS_HISTORY, ADOPT_APPROVED, ADOPTING, ADOPTED` `:135-142`; approval refusals `:161-178`.
- User username for panel match: printable ASCII `\x21-\x7e`, 1-128 chars, lowercased (`legacy-service-matching.ts:105-110`).
- Phone: absent spellings `'' | none | 0`; valid E.164 or `09xxxxxxxxx`; never written (`decisions.ts:43-53`).
- Archive run failures: `SNAPSHOT_DIVERGED, ABANDONED, STAGED_COUNT_MISMATCH, SOURCE_KEY_DUPLICATED, CELL_UNREPRESENTABLE (NUL or indexed cell >1000 chars), INTERRUPTED` (`legacy-invoice-archive.ts:115-128`).

---

## 8. NEXA legacy import contract shapes (for converter alignment)

There is **no file/JSON import format**: NEXA's importer reads MySQL directly (`--source env:NAME | mysql://… | fixture:PATH`) (`importer.md:35`). The `fixture:` source is a synthetic dataset (`tests/fixtures/legacy/synthetic-legacy.json`, regenerated by `write-fixtures.ts`). Interfaces a converter could target:

- `LegacySourceSession` (`source-port.ts:168-220`): `columns()` → `{table, column, dataType, ordinal}`, `rows(table, columns)` → `AsyncIterable<(string|null)[]>` (all values text, PK-byte order), `tables()`, `catalogColumns()`, `countRows()`, `readSetRows()`, `syntheticMarker()`.
- Panel map JSON `nexa-legacy-panel-map/v1` (§5).
- Fingerprint v1 = `sha256(JSON{v, schema, tables:{user,invoice,product:{rows, columns, digest}}})`; `schema` hashes sorted `table.column:data_type` lines (`importer.md:184-189`). Read-set fingerprint `legacy-read-set:<name>:v<n>` (`legacy-inventory.ts:132-150`); names `inventory, products, invoice-archive, user-status`.
- Row checksums: `user:v1` (§3), `invoice:v1` over `[id_invoice,id_user,username,Status,is_test,code_panel,code_product,Volume,Service_time,time_unit,is_custom]` (`source-snapshot.ts:305-317`).
- Adoption command (`apps/api/src/modules/commerce/legacy-adoption/application/legacy-adoption-ports.ts:78-132`): runId, legacyInvoiceKey, sourceChecksum, telegramUserId, match, runtime{state, usage{usedBytes,totalBytes,expiresAt}, observedAt, subscriptionUrl}, productId, legacyPurchasedAt, idempotencyKey.
- NEXA DB targets: `legacy_import_runs`, `legacy_import_map` (`0190_legacy_import_metadata.sql`), `legacy_read_set_runs` (`0219`), `legacy_product_shapes` (`0181`), `legacy_trial_eligibility` (`0183`), `legacy_product_reviews` (`0221`), `legacy_invoice_archive(_runs,_staging)` (`0223`), `legacy_wallet_debts` (`0226`), `legacy_service_candidates` (`0230`), `legacy_cutover_approvals(_revocations)` (`0233`); `wallet_entries` reason `MIGRATION_OPENING_BALANCE` (`0186`).
- Zod schemas: `legacyWalletDebtViewSchema` etc. (`legacy-wallet-debt.ts:192-267`), `legacyProductReviewViewSchema` (`legacy-product-review.ts:168-286`), `legacyInvoiceArchiveRowViewSchema` (`legacy-invoice-archive.ts:213-342`), `legacyServiceEvidenceSchema` (`legacy-service-review.ts:245+`), cutover binding fields (`legacy-cutover.ts:45-55`).

---

## 9. Schema version detection

- **No explicit Mirza version detection exists.** NEXA handles variants by (a) required vs optional column allowlists (absent optional = not read) and (b) hashing the schema into fingerprints (`importer.md:184-189, 215-218`; `invoice-archive-read-set.ts:193-196`).
- Known variants: botmirzapanel (`92c0ed06`, 2023-08-18…2026-06-30) vs mirza_pro (`8e551ecf`, v0.6.0, 2025-10-15…2026-10-02) (`docs/legacy-import-metadata.md:81-84`); deployed = unknown fork with extra invoice columns (`:110-114`).
- Table classification fail-closed catalogue (`legacy-inventory.ts`), inventory read set records per table: name, class, type, engine, charset, collation, column count, hash of sorted `name:data_type`, exact COUNT(*); findings `UNCLASSIFIED, NOT_A_BASE_TABLE, TABLE_NAME_UNSUPPORTED, NOT_SNAPSHOT_CONSISTENT (non-InnoDB), NOT_UTF8MB4` (`docs/legacy-migration/table-inventory.md:45-76`).
- Dump formats accepted (`scripts/legacy-archive-inspect.mjs:10-23, 100-106`): mysqldump/mariadb-dump `.sql`/`.sql.gz` (header `-- MySQL dump`/`-- MariaDB dump`, must end `-- Dump completed`), MirzaBot PDO fallback (first lines `SET NAMES utf8mb4;` / `SET FOREIGN_KEY_CHECKS=0;` / `SET SQL_MODE='NO_AUTO_VALUE_ON_ZERO';`), `backup_YYYY-MM-DD.zip` with single entry `backup_YYYY-MM-DD.sql`, unencrypted or WinZip AES-256 (mirza_pro `e4966ff`). Refused: DEFINER/stored objects, multiple `USE`, `utf8mb4_0900_*` on MariaDB (`:925-951`). `--columns` lists column NAMES per table (`:41`).
- MySQL 8 is the production engine (`legacy-rehearsal-source.sql:13`; OQ-P7-03).

---

## 10. Temporary "Mirza legacy" modules (retirement candidates)

Web (`apps/web/src/pages/`, routed `apps/web/src/app.tsx:58-62,192-195`, nav `apps/web/src/nav.ts:246-300`):
`legacy-products.tsx`, `legacy-invoices.tsx`, `legacy-debts.tsx`, `legacy-services.tsx`, `legacy-cutover.tsx`.
API controllers (`apps/api/src/surfaces/web/`): `legacy-products.controller.ts`, `legacy-invoices.controller.ts`, `legacy-debts.controller.ts`, `legacy-services.controller.ts`, `legacy-cutover.controller.ts`.
API modules: `apps/api/src/modules/platform/{legacy-import, legacy-importer, legacy-invoice-archive, legacy-service-review, legacy-cutover}`; `apps/api/src/modules/commerce/{legacy-wallet-debts, legacy-product-review, legacy-adoption}`; `commerce/catalog/application/legacy-shape.ts, legacy-product.service.ts, legacy-product-ports.ts`, `catalog/infrastructure/drizzle-legacy-product-shape.repository.ts`; `commerce/trials/*legacy-trial-eligibility*`; `commerce/wallet/application/migration-opening-balance.service.ts`.
CLIs: `apps/api/src/legacy-import.cli.ts`, `legacy-import-{argv,cutover,inventory,invoices,products,review}.ts`.
Contracts: `packages/contracts/src/legacy-{import,wallet-debt,invoice-archive,service-review,product-review,inventory,cutover,migration}.ts`.
Scripts: `scripts/legacy-*`, `scripts/mutate-mirza-pr{1..6}*.py`. Note: legacy shapes/hidden products, `orders.origin=LEGACY_ADOPTION`, `MIGRATION_OPENING_BALANCE` ledger reason, and Telegram stale-callback handling (OQ-C4, `docs/open-questions.md:3083-3140`) persist in core data and are NOT simply removable.

---

## 11. Open questions / blockers recorded in nexa-bot

(`docs/open-questions.md` line ranges; full list `docs/legacy-migration/mirza-full-migration.md:283-301`)
- No real-data evidence at all: Q1–Q7 NOT RUN (`sql-evidence.md:3-7`); table inventory NOT RUN.
- OQ-MZ-INV-01..11 (`:3255-3308`): full table list; which tables/columns hold secrets; real column types/charsets (Balance int vs decimal; latin1 mojibake); non-InnoDB; non-live `Status` values; `time_sell` format & timezone; `price_product` Toman vs Rial shape; units of `Volume`/`Volume_constraint`, meaning of `0`, `time_unit` spellings; real `product` columns; cutover row counts; archive column allowlist.
- OQ-LPR-01..09 (`:3310-3352`), OQ-LIA-01..09 (`:3354-3401`), OQ-LWD-01..07 (`:3403-3447`; 02 changed balance never applied; 03 agents; 04 debt collection out of scope; 07 decided), OQ-LSR-01..05 (`:3449-3480`; panel 8255; declared-missing codes; one owner several invoices), OQ-LCO-01..06 (`:3482-3512`), OQ-P4-01 (invoice key; caveat) (`:3188-3203`), OQ-P7-01..04 (`:3225-3253`; phone not imported; MySQL 8 parity), OQ-I14-01..03 (`:3165-3179`), OQ-I15-01 (`:3181-3186`), OQ-REH-02 freeze commands unknown (`:3217-3223`), OQ-P5-01..03 RickPanel list route (`:3142-3163`).
- `affiliates` = user id? (Q3, evidence only; referrals out of Phase 1) (`sql-evidence.md:304-316`).

### Explicit unknowns for the converter (do not guess)
1. Real Mirza DDL types (int/decimal/varchar) for every column — NEXA only has public-source & synthetic DDL.
2. Any table beyond `user`, `invoice`, `product` (columns, keys, secrets).
3. `price_product` numeric formatting; Toman is an owner decision, not data-proven.
4. `time_sell` format in the deployed fork; legacy timezone.
5. Non-live invoice statuses; `time_unit` spellings beyond `''/d/day/days` (fixture shows `month`, refused).
6. Meaning of `0` volume/duration; `Location`; `affiliates`; `Category` vs `category`.
7. Whether renewals create new invoices on the same account.

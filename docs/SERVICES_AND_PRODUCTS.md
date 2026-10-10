# Services, panels, products and categories (module `services_ext`)

This page covers how these Mirza tables migrate:

* `service_other`, `cancel_service` and `manualsell`;
* `marzban_panel` and `category`;
* the deployed fork's `vs_*` tables.

It also gives the full product and category mapping proposal.

The code is in `src/mirza2nexa/modules/services_ext.py`, with module order 300, and the tests are in `tests/test_services_ext.py`. Everything here is history or reference data:

* No record provisions, renews, deletes or touches a panel account.
* No record credits or debits a wallet.
* No record creates a payment, order, product or category.

Evidence references point to these sources:

| Short name | Source | Commit |
|---|---|---|
| **MP** | `mirza_pro` | `b645db74` |
| **BMP** | `botmirzapanel` | `92c0ed06` |
| **NEXA** | `nexa-bot` | `9e604def` |

The cross-table facts come from `docs/SOURCE_TABLES.md` (Q1) and `docs/evidence/mirza_tables.json`.

None of these sources is the deployed fork. For that reason:

* Every column the converter reads is listed explicitly.
* Any other column is reported as `unmapped` in `reports/coverage.json` and never stored.

## 1. What `service_other` is

`service_other` is an **operation log per service account**. It is not a table of services.

Mirza appends one row each time one of these happens to an existing panel account:

* a renewal;
* an extra-volume or extra-time purchase;
* a location change;
* an ownership transfer;
* a gift.

| Column | Meaning | Evidence |
|---|---|---|
| `id` | AUTO_INCREMENT primary key | MP `db/tables/service_other.php:5` |
| `id_user` | Telegram id of the actor. For `extend_user_by_admin` from the Telegram admin flow, this is the **admin's** id. | MP `index.php:2125`; `admin.php:7646` |
| `username` | The panel account username. It equals `invoice.username`. There is **no** `id_invoice` column. | MP `function.php:1244-1258`; `db/indexes.php:16` |
| `value` | JSON details whose keys depend on the type, or a scalar. For `transfertouser`, it is the new owner's Telegram id. | MP `index.php:1794-1800, 2098-2103, 2305-2312, 2897` |
| `time` | `date('Y/m/d H:i:s')` in Asia/Tehran local time | MP `index.php:3, 2123` |
| `price` | The amount charged, in the same unit as `user.Balance` (see the semantics below) | MP `index.php:1879, 2130`; `function.php:1402` |
| `type` | The operation kind (see the vocabulary below) | MP `admin.php:4365-4377` |
| `status` | `paid` or `unpaid` for `extend_user`. Other writers leave it NULL, and Mirza's reports count NULL as paid. | MP `index.php:1803, 1873`; `api/users.php:111` |
| `output` | The raw panel API response. **SECRET: never staged**, because it may carry subscription links. | MP `index.php:2134`; `function.php:1299` |

### Links to other tables

* **Invoice.** A row links to the invoice through `lower(username)`. This is the same key that NEXA's service matching uses (`legacy-service-matching.ts:105-110`).
* **Payment.** Rows of the direct-pay flows link to `Payment_report` only through text:
  * A direct-pay renewal stores `value.id_order`. The payment row is `Payment_report.id_invoice = 'getextenduser|<username>%<id_order>'` (MP `index.php:1792-1806`; `function.php:1011, 1240-1300`).
  * Extra volume and extra time use `getextravolumeuser|…` and `getextratimeuser|…`, and `price` is then `Payment_report.price` (MP `function.php:1366-1402, 1438-1474`).
  * This converter keeps `id_order` inside `details` and does not join `Payment_report`, which belongs to the payments module.
* **Money.** Every charge was already applied when it happened, either to `user.Balance` or through the gateway. The converter therefore exports the amount raw and parsed, but **never** applies it.

### Type vocabulary

| `type` | Normalized | Meaning proven? | `amount.semantics` |
|---|---|---|---|
| `extend_user` | `RENEWAL` | yes | `PRODUCT_PRICE_CHARGED` |
| `extend_user_by_admin` | `RENEWAL_BY_ADMIN` | yes. `id_user` is not treated as the owner. | `PRODUCT_PRICE_OR_ZERO_NOT_CHARGED` |
| `extends_not_user` | `RENEWAL_NOT_IN_LIST` | yes ("تمدید از نوع نبودن یوزر در لیست", `lang/fa.php:2242`) | `PRODUCT_PRICE_CHARGED` |
| `extra_user` | `EXTRA_VOLUME` | yes | `WALLET_PRICE_OR_DIRECT_PAY_SHORTFALL` |
| `extra_not_user` | `EXTRA_VOLUME_NOT_IN_LIST` | **no** (no label; OQ-SVC-OP-01) | `WALLET_PRICE_CHARGED` |
| `extra_time_user` | `EXTRA_TIME` | yes | `WALLET_PRICE_OR_DIRECT_PAY_SHORTFALL` |
| `change_location` | `CHANGE_LOCATION` | yes | `PRODUCT_PRICE_RECORDED` |
| `transfertouser` | `OWNERSHIP_TRANSFER` | yes | `ZERO_BY_DESIGN` |
| `gift_volume` / `gift_time` | `GIFT_VOLUME` / `GIFT_TIME` | yes | `ZERO_BY_DESIGN` |
| anything else | `UNKNOWN`, raw value kept, review `SERVICE_OP_TYPE_UNKNOWN` | — | `UNKNOWN` |

`price` does not mean the same thing in every row. For direct-pay extra volume and extra time it is only the **shortfall** above the wallet balance, not the full price (`docs/SOURCE_TABLES.md` Q1, gotcha 3). The reconciliation therefore reports sums per type and status. It never reports one revenue total.

## 2. Record shapes

Every record carries these fields:

* `record_type`;
* `schema`;
* `idempotency_key` (`legacy:…`);
* `provenance` (`source_table`, `source_pk`, `source_fingerprint`, `row_checksum`);
* `review_codes`;
* the live-state flags `provision`, `applies_to_live_state`, and where they apply `creates_payment` and `affects_wallet`, always `false`.

### `records/service_operations.jsonl`

Record type `legacy_service_operation`, schema `mirza.service_other.v1`, key `legacy:service-op:<id>`.

```json
{"legacy_id": "17",
 "operation_type": {"raw": "extend_user", "normalized": "RENEWAL", "meaning_proven": true},
 "status": {"raw": "paid", "normalized": "PAID"},
 "owner": {"raw": "7000000012", "telegram_user_id": "7000000012", "customer_decision": "IMPORT", "role": "OWNER"},
 "panel_account_username": "syn12",
 "invoice_relation": {"state": "FOUND", "reason": null, "invoice_key": "…", "candidates": 1, "matched_by": "username"},
 "owner_relation": "MATCHES_INVOICE",
 "amount": {"raw": "50000", "parse_note": "OK", "amount_minor": "50000", "currency": "IRT", "applied": false,
            "semantics": "PRODUCT_PRICE_CHARGED"},
 "time": {"raw": "2025/03/01 10:00:00", "local": "2025-03-01T10:00:00", "time_zone": "Asia/Tehran", "parse_note": "OK"},
 "details": {"format": "JSON", "fields": {"code_product": "p001", "id_order": "…"}, "unlisted_keys": [], "units": "UNKNOWN: …"},
 "provision": false, "applies_to_live_state": false, "creates_payment": false, "affects_wallet": false}
```

The fields take these values:

* **`status.normalized`**: one of these values:

  | Value | Raw source |
  |---|---|
  | `PAID` | `paid` |
  | `UNPAID` | `unpaid`, an abandoned direct-pay attempt |
  | `NOT_RECORDED` | NULL or `''` |
  | `UNKNOWN` | anything else |

* **`owner.customer_decision`**: the core customer decision (`IMPORT`, or a review code), or `NOT_IN_BACKUP`.
* **`invoice_relation.state`**: one of these values:
  * `FOUND`: exactly one invoice has this username, or several do and exactly one of them has the same owner (`matched_by: "username+owner"`).
  * `NOT_FOUND`: the `reason` is one of `NO_INVOICE_WITH_USERNAME`, `USERNAME_INVALID` or `INVOICE_TABLE_UNAVAILABLE`.
  * `AMBIGUOUS`: several invoices match and the owner does not single one out.
* **`owner_relation`**: one of these values:

  | Value | Meaning |
  |---|---|
  | `MATCHES_INVOICE` | the row's owner is the invoice's owner |
  | `PREVIOUS_OWNER_VIA_TRANSFER` | a `transfertouser` row for the same account explains the difference |
  | `CONFLICT` | the owners differ and no transfer explains it |
  | `NO_INVOICE` | no invoice was found |
  | `NOT_AN_OWNER_FIELD` | an admin renewal |

* **`details`**: only keys proven by the writers (`VALUE_KEYS`) are kept as values, passed through redaction. Any other key is listed by name only. Panel figures such as bytes and expiry timestamps are not interpreted.

### `records/service_cancellation_requests.jsonl`

Record type `legacy_service_cancellation_request`, key `legacy:cancel-service:<id>`. The fields are:

* `owner`, `panel_account_username`, `invoice_relation` and `owner_relation`;
* `status`, which maps `waiting`→`PENDING`, `accept`→`ACCEPTED` and `reject`→`REJECTED`, and anything else to `UNKNOWN`;
* `admin_note`: the rejection text, redacted. The creation placeholder `'0'` becomes `null`.

The refund paid on `accept` goes straight to `user.Balance`, and its amount is not stored anywhere (MP `admin.php:4743-4748`). The record therefore carries `refund_amount: null` and `refund_amount_note: "NOT_STORED_IN_SOURCE"`.

### `records/manual_config_inventory.jsonl`

Record type `legacy_manual_config_inventory`, key `legacy:manualsell:<id>`. Each row is one pre-made config of a `Manualsale` panel (MP `panels/Drivers/Manualsale.php`). The fields are:

* `code_panel`, `code_product_raw` and `label`;
* `status`, which maps `active`→`AVAILABLE`, `selled`→`SOLD` and `delete`→`DELETED`;
* `sold_to_panel_username`, with an invoice relation;
* `config_content: "NOT_EXPORTED_SECRET"`. `contentrecord` is the sold config itself and is never staged.
* `requires_manual_transfer`: `true` for unsold stock.

The evidence JSON recommends EXCLUDED for this table. ARCHIVE is used instead because the counts and states are useful for reconciling manual-panel invoices, and no secret leaves.

### `records/panel_registry.jsonl`

Record type `legacy_panel`, key `legacy:panel-registry:<id>`. The fields are:

* `code_panel` and `name_panel`;
* `panel_type`, normalized against MP `panels/PanelRegistry.php:6-17`, or `UNKNOWN`;
* `status`, where `active`/`activepanel` become `ACTIVE` and `disable`/`disablepanel` become `DISABLED`;
* `settings_raw`: the non-secret flags;
* `pricing_raw`: the per-tier price JSON, with `applied: false`;
* `hidden_from_users_count`;
* `invoice_links`, counted by `code_panel`, live status, and `Service_location == name_panel`;
* `product_links`, counted by `product.Location == name_panel` (MP `index.php:1410`);
* `manual_stock_rows`;
* `credentials_required_separately: true` and `credentials_in_package: false`;
* `secret_columns_dropped`;
* `nexa_panel_id: null` and `mapping_state: "OPERATOR_MUST_MAP"`.

These **secret columns are never staged**. They are dropped at parse time, before anything touches disk:

* `url_panel`, `username_panel`, `password_panel` and `secret_code`;
* `datelogin`, which holds the panel session token (MP `function.php:441`; `api/panels.php:88`);
* `linksubx`, the subscription domain;
* `proxies`, `inbounds`, `inboundid` and `inbound_deactive`.

**NEXA panel credentials must be entered through NEXA's own secret flow by the operator. They never travel in the package.** This follows NEXA's `SECRETS_MANUAL` class: "never read beyond its name, column names and count; moved by hand" (NEXA `docs/legacy-migration/table-inventory.md:17`). The registry is input for the operator's `nexa-legacy-panel-map/v1` (NEXA `importer.md:255-303`). It is not a panel import.

### `records/category_catalogue.jsonl`

Record type `legacy_category`, key `legacy:category-table:<id>`. The fields are:

* `label`;
* `label_normalized`, computed as `lower(trim())` (MP `keyboard.php:1379`);
* `product_links.exact` and `product_links.normalized`;
* `nexa_category_id: null`, `auto_create: false` and `review_state: "PENDING_REVIEW"`.

Products refer to a category by its **remark text**, not by its id (MP `index.php:3595-3598`; `api/miniapp.php:642-650`).

### `records/archive/vs_*.jsonl`

These are `legacy_archive_row` records of class REVIEW, written by `generic.export_rows`:

* No public DDL or code exists for `vs_*`, so columns come from the runtime DDL.
* Columns whose names look like credentials are dropped.
* Every value passes through value-level redaction.
* Each non-empty table gets a `TABLE_NEEDS_DECISION` review.

The `vs_*` shapes in `synth.py` are **invented** test data and are not evidence.

## 3. Review codes

| Code | Severity | When |
|---|---|---|
| `SERVICE_OWNER_CONFLICT` | blocker (record) | The `service_other`/`cancel_service` owner differs from the matched invoice's owner, and no transfer explains it. |
| `SERVICE_OP_INVOICE_AMBIGUOUS` | blocker (record) | Several invoices share the username, and the owner does not single one out. |
| `SERVICE_OP_INVOICE_NOT_FOUND` | warning | No invoice has the username. This is not raised for the `*_not_user` "not in list" types. |
| `SERVICE_OP_OWNER_NOT_IN_BACKUP` | warning | `id_user` is not in `user`. Not raised for admin renewals. |
| `SERVICE_OP_TYPE_UNKNOWN` / `SERVICE_OP_AMOUNT_UNREADABLE` | warning | The raw value is kept. |
| `SERVICE_PANEL_CONFLICT` | warning | A sold `manualsell.codepanel` differs from the invoice's `code_panel`. |
| `CANCELLED_SERVICE_STILL_LIVE` | warning | An `accept`ed cancellation whose invoice still has a live status. |
| `PANEL_NOT_IN_REGISTRY` | warning | An `invoice.code_panel` is missing from `marzban_panel`. Raised only when a registry exists. |
| `PANEL_WITHOUT_INVOICES` | warning | A registry panel that no invoice references. |
| `PANEL_CODE_DUPLICATED` | warning | A blank or duplicated `code_panel` in the registry. |
| `CATEGORY_LABEL_NOT_IN_TABLE` / `CATEGORY_REMARK_DUPLICATED` | warning | Category mapping gaps. |
| `SOURCE_ROW_KEY_INVALID` | warning | A NULL or duplicated `id`. The row is kept with a content key or a `#n` suffix. |
| `TABLE_NEEDS_DECISION`, `TABLE_NO_STORABLE_COLUMNS` | warning | `vs_*` tables, or tables without any storable column. |

Blocker-severity reviews are per record, like the core's `ORPHAN` and `AMBIGUOUS_OWNERSHIP`. They do **not** add a package blocker, because these records are history and nothing is applied. The package-level warnings are:

* `SERVICE_OPERATIONS_HISTORY_ONLY`;
* `PANEL_CREDENTIALS_NOT_EXPORTED`;
* `SERVICE_OWNER_CONFLICTS`, with its count.

The review kind is `service_history`, `panel` or `category`. It is never the core's `service`.

## 4. Reconciliation sections (`reports/reconciliation.json`)

* **`service_operations`** reports:
  * `rows`, `records` and `accounted`;
  * counts `by_type_raw`, `by_type`, `by_status`, `invoice_relation`, `owner_relation` and `owner_customer_decision`;
  * `owner_conflicts` and `amount_unreadable`;
  * `historical_amounts_minor`, keyed `"<TYPE>:<STATUS>"`, giving `{count, amount_minor}`;
  * `currency` and `applied_to_wallet: false`.
* **`service_requests`** reports:
  * for `cancel_service`: `by_status`, `invoice_relation`, `owner_conflicts` and `accepted_but_invoice_live`;
  * for `manualsell`: `by_status`, `by_panel` and `panel_conflicts`.
* **`panel_registry`** reports:
  * `registry_rows`, `invoice_panel_codes` and `matched_codes`;
  * `invoice_codes_missing_from_registry`, as a list of codes with counts;
  * `registry_codes_without_invoices`;
  * `duplicate_or_blank_codes`, `secret_columns_dropped` and `credentials_exported: false`.
* **`category_catalogue`** reports:
  * `rows` and `product_column`;
  * `products_with_category`, `matched_exact`, `matched_normalized_only`, `unmatched_products` and `unmatched_labels`;
  * `categories_without_products`.
* **`services_ext_review_tables`** reports row counts for each `vs_*` table.

## 5. Product and category mapping proposal (never auto-created)

The core converter (`convert.py`) exports products as `legacy_product_review` records with `auto_create: false`. These are inputs to NEXA's `legacy_product_reviews`. NEXA's approve-new request carries no price, panel or category (`legacy-product-review.ts:259-275`). Each product field is mapped as follows:

| Mirza `product` field | Evidence | NEXA catalog concept | Status / what blocks automatic creation |
|---|---|---|---|
| `id` | MP `product.php:6` | `legacyProductId` | mapped |
| `code_product` | MP `product.php:7` | `codeProduct`; the key of `products[{codeProduct, productId}]` in the panel map | mapped. A duplicate code becomes `CODE_DUPLICATED`, and the product is not parsed. |
| `name_product` | MP `product.php:8` | product title | Proposal only. NEXA titles are set by the operator. |
| `price_product` | MP `product.php:9` | `historicalPriceRaw/Minor/Currency` | **History only.** NEXA renewals use the current NEXA tariff, never the legacy price (`legacy-migration.ts:187-197`). The unit needs operator confirmation (Toman → IRT). |
| `Volume_constraint` | MP `product.php:10` | `trafficBytes` (proposed GB × 2³⁰) | **Blocks:** the unit is not proven (OQ-LPR-02), and the meaning of `0` (unlimited?) is unknown. |
| `Service_time` | MP `product.php:12` | `durationDays` | **Blocks:** the unit is not proven, and `0` is unknown. Invoices also carry `time_unit`, and `month` is not supported. |
| `Location` | MP `product.php:11`; `index.php:1410` | panel scope: `marzban_panel.name_panel`, or `/all` for every panel | **Blocks:** it must be resolved to a NEXA panel through the operator panel map (name → `code_panel` → panelId). `panel_registry.product_links` gives the counts. |
| `agent` | MP `product.php:13` | reseller tier visibility (`f`/`n`/`n2`) | **Blocks:** agents import as ordinary customers (OQ-LWD-03). NEXA has no per-tier product visibility mapping yet. |
| `category` / `Category` | MP `product.php:18, 30`; `index.php:3597` | NEXA catalog category (match by label) | **Blocks:** NEXA approve-new carries no category, and categories are never auto-created. `category_catalogue` gives the matched and unmatched counts. |
| `note` | MP `product.php:14` | product description | Free text. Not exported by the core. Owner decision. |
| `data_limit_reset` | MP `product.php:15`; `index.php:3866` | panel reset strategy | **Blocks:** NEXA has no equivalent field. Only `no_reset` is evidenced as a default. |
| `one_buy_status` | MP `product.php:16`; `index.php:1410` | "first-purchase only / hidden from repeat buyers" flag | **Blocks:** NEXA has no equivalent. Owner decision. |
| `status_product` | MP `product.php` columns (`active`); `index.php:3704` (`disable` = sold out) | sale status (on sale / sold out) | **Blocks:** NEXA tariff state is decided by the operator, so it is not imported. |
| `hide_panel` | MP `product.php:19`; `index.php:1440-1441` | per-panel hiding list | **Blocks:** this is panel-scoped, so it needs the panel map. |
| `inbounds`, `proxies` | MP `product.php:17-18` | — | **SECRET.** Never read (`products-read-set.ts:20-35`). |

A product reaches NEXA only through an operator decision in NEXA's own review state machine:

* `APPROVED_EXISTING`: maps to an existing NEXA product;
* `APPROVED_NEW`: the operator creates the product in NEXA;
* `REJECTED`.

The converter emits every fact needed for that decision and nothing else. Categories follow the same rule. `legacy_category_label` (core) and `legacy_category` (this module) are proposals, and an operator creates or maps categories in NEXA.

## 6. Open questions for the owner

* **OQ-SVC-OP-01**: What does `extra_not_user` mean? It has no label in the admin map. Does it mean extra volume for a panel account that is not in the bot's list, as `extends_not_user` does?
* **OQ-SVC-OP-02**: Does the deployed fork write `service_other.status` for types other than `extend_user`? Real data will show whether NULL dominates.
* **OQ-SVC-OP-03**: Should NEXA show legacy operations in a customer's service history, for example under a "legacy history" tab, or is an archive enough?
* **OQ-SVC-CX-01**: Should `waiting` cancellation requests be re-opened as NEXA support tickets?
* **OQ-SVC-MS-01**: Unsold `manualsell` stock: must it be re-entered by hand into NEXA, or abandoned? The configs are never exported.
* **OQ-SVC-PN-01/02**: Map every `code_panel` to a NEXA panel. Are panels referenced by invoices but missing from `marzban_panel` decommissioned?
* **OQ-SVC-VS-01..03**: What do the `vs_*` tables of the deployed fork do? Do `vs_order` amounts duplicate `user.Balance` or `Payment_report` money? Should any of it reach NEXA?
* **Category class**: The evidence JSON proposes OPERATIONAL. This module uses ARCHIVE, a proposal only, because NEXA has no category import path. Should a category import step be specified?

# `.nxpkg` content contract: package schema `nexa.migration.mirza` 1.1.0

The container format, meaning the encryption, chunking and checksums, is described in `NXPKG_FORMAT.md` and has
not changed. This document covers the **content**: which files and records a package holds, and what an
importer may and may not do with each of them.

## Versioning

| Version | Change | Compatible? |
|---|---|---|
| 1.0.0 | Phase 1: customers, opening balances, debts, products, categories, invoices, services, panels, review queue | — |
| 1.1.0 | Phase 2, additions only: archive and history files, configuration files, `reports/coverage.json`, a `schema` field on every record | A 1.0 reader that ignores unknown files and fields keeps working |

The rules:
* A **minor** bump only adds files or fields.
* A **major** bump changes or removes something; readers reject an unknown major version.
* Each record schema is versioned on its own (`schema: "<name>.v<N>"`).

## Fields every record has

| Field | Meaning |
|---|---|
| `record_type` | the kind of record (tables below) |
| `schema` | the versioned schema of the record, e.g. `mirza.payment_report.v1` or `m2n.customer.v1` |
| `idempotency_key` | starts with `legacy:` and is unique within the package. The same source row in a later backup gets the same key. |
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
| `records/engagement/{discounts,discount_usage,referrals,wheel_results,ad_campaigns,program_settings}.jsonl` | `legacy_*` | discount and gift **code strings are not included** |
| `records/support/{departments,support_messages,tickets,ticket_messages}.jsonl` | `legacy_support_*`, `legacy_ticket*` | text is personal: restricted access (`DATA_PROTECTION.md` §7) |
| `records/archive/<table>.jsonl` | `legacy_archive_row` | generic raw archive for tables whose meaning is still open (`REVIEW` class) |
| `records/configuration/<table>.jsonl` | `legacy_configuration_row` | non-secret settings, bot texts and help pages, for reference only |

### Reports
| File | Content |
|---|---|
| `reports/coverage.json` | One row per source table: its class, status (`executed`, `archived`, `review`, `blocked`, `empty` or `excluded`), counts, dropped secret columns, unmapped columns and reason |
| `reports/reconciliation.json` | Sections: `wallet`, `invoices`, `services`, `payments`, `wallet_history`, `service_operations`, `service_requests`, `panel_registry`, `category_catalogue`, `agents`, `engagement`, `support`, … |
| `reports/mapping.json` | The mapping version, the entity mapping and the schema profile |
| `reports/review-queue.jsonl` | `{kind, severity, code, message (fa), entity, source_ref, data}`. Never holds secrets or message text. |
| `reports/diff.json` | Only present when the package was compared with a previous backup |

## Manifest additions (1.1.0)

These fields are added on top of the 1.0.0 manifest:
* `readiness`
* `blockers`
* `money`, holding `declared_unit`, `currency` and `rescaled: false`
* `record_counts`
* `compatibility.verified_by_importer: false`

`verified_by_importer` stays `false` until the NEXA importer described in `NEXA_IMPORTER_PLAN.md` exists and has
passed its acceptance tests.

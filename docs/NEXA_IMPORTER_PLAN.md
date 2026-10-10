# NEXA-side importer for `.nxpkg`: design for the next phase

> **Status:** this is a proposal only. Nothing in `Mhoseinshah1/nexa-bot` has been changed. The owner
> must approve each step below before any NEXA code is written. Until that importer exists and passes
> the acceptance tests in §6, no `.nxpkg` may be described as "importable in production". This is why
> every manifest carries `compatibility.verified_by_importer: false`.

## 1. Why we need an importer

NEXA's current legacy importer reads Mirza's **MySQL** directly
(`legacy-import.cli.ts --source mysql://…`, see `docs/NEXA_EVIDENCE.md` §8). The converter produces
the same decisions **offline** from a backup ZIP. The importer is the NEXA component that consumes
those decisions in place of a live MySQL source.

It must reuse NEXA's existing tables, services and invariants rather than introduce parallel ones.

## 2. Where it lives in NEXA

* **Source adapter:** `apps/api/src/modules/platform/legacy-importer/infrastructure/nxpkg-source.ts`.
  It should implement the existing `LegacySourceSession` port (`source-port.ts:168-220`) on top of the
  decrypted package records, so it is a third source type next to `mysql://` and `fixture:`.
* **Plan and apply:** keep the existing ones (`plan.ts`, `decisions.ts`). The package carries raw cells
  (`source_amount_raw`, `status_raw`, `volume_raw`, …), and NEXA **re-decides** every record itself.
  The converter's decision is used only as a cross-check.
* **Container code:** a small `packages/nxpkg` (TypeScript) that mirrors `docs/NXPKG_FORMAT.md`:
  * header parsing;
  * scrypt with HKDF;
  * AES-256-GCM STREAM;
  * zip-v1;
  * `checksums.json` and manifest validation.

  It needs a shared cross-language test vector (see §6).

## 3. Steps

1. **`verify`:** check the container (wrong key versus tampering), `package_schema` major = 1,
   checksums and the manifest. Show the operator `import_id`, `source_fingerprint`, `readiness`,
   `blockers` and record counts, and require them to confirm the fingerprint out-of-band.
2. **Refuse if:**
   * `readiness != "ready"`;
   * `money.currency != "IRT"`;
   * `money.rescaled != false`;
   * the minimum importer version is not met;
   * the tenant's sales currency is not IRT.

   These rules are fail-closed.
3. **Re-decide:** rebuild `LegacySourceSession` rows from `records/*`. Recompute NEXA's own `user:v1` and
   `invoice:v1` checksums and require them to equal `provenance.row_checksum`. Any mismatch is tampering
   or a converter bug, and must abort.
4. **Dry run:** use the existing `legacy_import_runs` dry-run mode. Show a preview that reuses the
   converter's `reports/reconciliation.json` totals. NEXA's own totals must equal them.
5. **Explicit approval:** reuse the cutover approval (`legacy_cutover_approvals`), bound to
   `import_id` and `source_fingerprint`.
6. **Apply:** one transaction per batch, using the existing services.

   | Record | NEXA target | Idempotency |
   |---|---|---|
   | `customers` | customer create (NEW only; EXISTING is never modified) + `legacy_import_map` | `(tenant, telegram_user_id)` |
   | `wallet_opening_balances` | `migration-opening-balance.service.ts`, reason `MIGRATION_OPENING_BALANCE` | unique `(tenant, customer) WHERE reason=…` and reference `legacy:opening:<tg>` |
   | `legacy_debts` | `legacy_wallet_debts` (`PENDING_REVIEW`) | unique `(tenant, legacy_user_id)` |
   | `products` | `legacy_product_reviews` (`PENDING_REVIEW`) | `code_product` + facts checksum |
   | `invoices` | `legacy_invoice_archive` | `id_invoice` |
   | `services` | `legacy_service_candidates`; adoption **later**, using the panel map and a live panel check | `id_invoice` |
   | `reports/review-queue.jsonl` | legacy review rows (`legacy_import_map` MANUAL_REVIEW) | `(source table, pk, code)` |

7. **Re-import:** importing the same `import_id` again is a no-op (`ALREADY_POSTED`,
   `DEBT_ALREADY_RECORDED`). A newer backup brings a new `import_id`. NEXA then follows OQ-LWD-02: a
   changed balance is never applied and becomes a conflict for review. The converter already blocks
   these cases (`BALANCE_CHANGED_SINCE_PREVIOUS`).

## 4. What the importer must never do

* Provision, renew, delete or otherwise mutate panel services. Adoption stays the separate,
  operator-approved P6 flow, with a live panel check.
* Create orders, payments or revenue from invoices.
* Settle, collect or net legacy debts.
* Accept a package with blockers, an unknown major version or a currency other than IRT.
* Execute SQL from a package. There is none: the package holds only JSON records.

## 5. Migration of NEXA's temporary Mirza UI

**Removing the appearance never removes the records.** The tables, the archive and the ledger entries
stay permanently.

Each module below may be retired from navigation **only after all four conditions hold**:

* the `.nxpkg` importer has fully replaced its function;
* all legacy review rows have been resolved;
* the cutover approval has been recorded;
* the owner has approved the retirement.

| NEXA module (temporary) | Paths | Can be retired after | Must be kept |
|---|---|---|---|
| Legacy products review | `apps/web/src/pages/legacy-products.tsx`, `legacy-products.controller.ts`, `commerce/legacy-product-review` | every `legacy_product_reviews` row is APPROVED or REJECTED | `legacy_product_reviews` rows and hidden legacy products/shapes used by adopted services |
| Legacy invoice archive | `legacy-invoices.tsx`, `legacy-invoices.controller.ts`, `platform/legacy-invoice-archive` | a read-only archive viewer exists elsewhere (e.g. a customer history tab) | **all** `legacy_invoice_archive` rows (financial history) |
| Legacy debts | `legacy-debts.tsx`, `legacy-debts.controller.ts`, `commerce/legacy-wallet-debts` | every debt is ACKNOWLEDGED or WAIVED with a reason | `legacy_wallet_debts` rows and decisions (audit) |
| Legacy services review | `legacy-services.tsx`, `legacy-services.controller.ts`, `platform/legacy-service-review` | every candidate is adopted, kept as history or acknowledged | candidates and adoption evidence; orders with `origin=LEGACY_ADOPTION` |
| Legacy cutover | `legacy-cutover.tsx`, `legacy-cutover.controller.ts`, `platform/legacy-cutover` | final cutover is done and approvals are recorded | `legacy_cutover_approvals(_revocations)` |
| MySQL importer CLIs | `apps/api/src/legacy-import*.ts`, `scripts/legacy-*` | the `.nxpkg` path has passed the staging rehearsal | can be archived in git history |

**Never removable:**
* the `MIGRATION_OPENING_BALANCE` ledger reason and its entries;
* `orders.origin = LEGACY_ADOPTION`;
* the legacy shape keys used by renewals;
* stale-callback handling for old Telegram buttons (OQ-C4).

## 6. Acceptance tests for the importer (on synthetic data first, then an approved staging snapshot)

* **Container compatibility:** a test vector produced by `mirza2nexa` (`synth` → `convert` with a fixed
  key, salt and nonce) is verified in TS. Flipping one byte, truncating, using the wrong key, or using
  version 2 each fails.
* **Totals:** NEXA's own re-decision gives exactly the same customer, credit and debt totals as
  `reconciliation.json`.
* **No double credit:** importing the same package twice changes nothing (ledger sum unchanged, debt
  count unchanged).
* **Newer backup:** a backup with changed balances produces conflicts and no ledger change.
* **Blocked packages are refused:** for example `MONEY_UNIT_UNCONFIRMED`, `FINANCIAL_RECONCILIATION_MISMATCH`.
* **No panel calls:** an orphan or ambiguous service is never adopted; a panel adapter mock asserts zero calls.
* **Tenant isolation:** the existing two-tenant suite.
* **Scale:** 200k customers within the time budget.

## 7. Open items owned by NEXA, not by the converter

* Confirm decision 7 (Toman) against real data: run Q1–Q7 from `docs/legacy-migration/sql-evidence.md`
  on an approved offline snapshot.
* Classify the remaining Mirza tables (`marzban_panel`, `Payment_report`, `Discount`, …) before any of
  them is mapped.
* Decide on resellers (OQ-LWD-03) and on renewal semantics (OQ-LSR-03).
* Decide whether NEXA wants an Ed25519 converter signature in addition to AEAD (`docs/NXPKG_FORMAT.md`).

---

## 8. Phase 2: history destinations and operational plan

Phase 2 packages (`package_schema_version` 1.1.0, see `PACKAGE_CONTRACT.md`) carry history and archives
alongside the operational records. The rule for all of them is the same:
* NEXA stores history in **read-only legacy tables**, scoped per tenant and keyed by `idempotency_key`;
* it never replays history into live state machines;
* it never posts history to the ledger.

### 8.1 Destination tables (all new, proposed; nothing in `nexa-bot` exists yet for these)

| Package file | Proposed NEXA table | Visible in | Access |
|---|---|---|---|
| `payments.jsonl` | `legacy_payment_history` (amount_minor, currency, method_raw, direction, outcome, times raw+parsed, customer_id nullable, provenance) | Customer page → «سوابق پرداخت قدیمی» | finance role |
| `wallet_history.jsonl` + `wallet_history_checks.jsonl` | `legacy_wallet_transactions` + `legacy_wallet_history_checks` | Customer wallet → «تاریخچه کیف پول قدیمی» (read-only, labelled as legacy evidence) | finance role |
| `invoices.jsonl` | existing `legacy_invoice_archive` | existing legacy invoices page, later a customer history tab | support and finance |
| `services.jsonl` | existing `legacy_service_candidates` → P6 adoption | existing legacy services page | admin |
| `service_operations.jsonl`, `service_cancellation_requests.jsonl` | `legacy_service_operations`, `legacy_cancellation_requests` | Service detail → «سوابق عملیات قدیمی» | support |
| `products.jsonl`, `category_catalogue.jsonl`, `categories.jsonl` | existing `legacy_product_reviews` + `legacy_category_labels` | existing legacy products page | catalog admin |
| `panel_registry.jsonl`, `panels.jsonl` | panel-map wizard input only; **no credentials**, which are entered through NEXA's secret flow | adoption wizard | owner |
| `agents/*.jsonl` | `legacy_agent_profiles`, `legacy_agent_price_levels`, `legacy_agent_history` | Reseller archive page | owner (role activation needs OQ-LWD-03) |
| `engagement/*.jsonl` | `legacy_discount_history`, `legacy_referrals`, `legacy_wheel_results` | Marketing archive | marketing |
| `support/*.jsonl` | `legacy_support_threads` + `legacy_support_messages` (closed, read-only) | Customer page → «پشتیبانی قدیمی» | support (personal data, audited reads) |
| `archive/*.jsonl` | `legacy_raw_archive(table, key, fields jsonb)` | Owner-only archive browser | owner |
| `configuration/*.jsonl` | not imported; operators read them to configure NEXA templates and settings by hand | — | owner |

### 8.2 Execution order (each step is idempotent and has a dry run)

1. **Verify:** check the container, refuse on blockers and on any live-state flag set to `true`, and show
   the fingerprint for the operator to confirm out-of-band.
2. **Customers, then opening balances and debts**, as in §3. This is the only financial operation.
3. **Read-only history:** payments, wallet history, invoices, service operations and support, in batches
   of 5k. Each table gets `UNIQUE (tenant_id, idempotency_key)`, so a repeated import is a no-op.
4. **Review proposals:** products, categories, panels and agents. Each goes into its existing or new
   review table in `PENDING_REVIEW`.
5. **Reconcile:** NEXA recounts every file and must match `reconciliation.json` and `coverage.json`
   exactly before the run is marked complete.
6. **Adoption (P6):** for each service, a separate operator approval and a live panel check. This is the
   only step that touches a panel.

### 8.3 Additional acceptance tests

* **History is inert.** After importing history, the sums of the wallet ledger, orders, payments and
  revenue are exactly what they were before.
* **Re-import is a no-op.** Importing the same package twice leaves every row count unchanged.
* **Changed history from a later backup:** records that were added are inserted. Records that changed are
  *not* overwritten; they become conflicts for review.
* **Support text is restricted.** A role without the support permission gets 403 on support text.
* **Package-level refusal.** A package with an unknown record type, or with `schema` major version 2, is refused.

### 8.4 Retiring the temporary Mirza pages (after approval)

§5 still applies. The new history views listed in §8.1 replace the legacy invoice, debt and product pages
for **viewing**. Only after a full successful import, all review rows resolved and the owner's approval may
the temporary navigation entries be hidden. The tables underneath are never dropped.

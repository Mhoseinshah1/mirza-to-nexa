# Mirza → NEXA mapping (mapping version `mirza2nexa-mapping:1.0.0`)

Every rule here comes from evidence in `docs/NEXA_EVIDENCE.md`, which is itself built from a
read-only checkout of NEXA (`nexa-bot@9e604def`). The code is in `src/mirza2nexa/mapping/rules.py`
and `src/mirza2nexa/convert.py`.

**Nothing here is guessed.** If NEXA has not decided something, the converter sends the record to
the review queue or blocks it. It never makes up a value.

## Ground rules

* **Source contract:** the converter uses NEXA's frozen legacy read set v1 (`source-port.ts:36-83`).
  * Tables: `user`, `invoice`, `product`. Names are matched case-sensitively.
  * Required columns:
    * `user`: `id`, `Balance`, `limit_usertest`
    * `invoice`: `id_invoice`, `id_user`, `username`, `Status`, `is_test`, `code_panel`, `code_product`, `Volume`, `Service_time`, `time_unit`, `is_custom`, `price_product`
    * `product`: `id`, `code_product`
  * Any other table is *unclassified*. It is counted and reported, but never migrated.
* **Cells:** every value is read as text, the way NEXA reads it (`CAST(col AS CHAR)`). Trimming uses
  JavaScript's `String.prototype.trim()` set of whitespace (`rules.js_trim`), not Python's `str.strip()`.
* **Columns that are never read:** `invoice.user_info` (subscription URL), `invoice.uuid`,
  `invoice.bottype` (sub-bot token), `product.inbounds` and `product.proxies`. They are dropped at parse
  time and never written to disk. `user.number`, `user.affiliates`, `invoice.note` and `invoice.refral`
  are not exported either; for the phone number only its class is kept.
* **Order:** primary-key byte order, which is the same order NEXA uses (`ORDER BY CAST(pk AS BINARY)`).
  Output is deterministic.
* **Money:** an amount is copied **unchanged** into `amount_minor`. The currency label comes only from
  the operator's confirmation:
  * `toman` gives `IRT`, with exponent 0. This matches NEXA owner decision 7.
  * `rial` gives `IRR`, and the package is blocked with `MONEY_UNIT_NOT_IRT`, because NEXA only accepts IRT.
  * If the unit is not confirmed, the currency is `null` and the package is blocked with `MONEY_UNIT_UNCONFIRMED`.
  * Nothing is ever multiplied or divided by 10.
* **Provenance:** every record carries `provenance` with these fields:
  * `source_table` and `source_pk`;
  * `source_fingerprint`;
  * `row_checksum`. For `user` and `invoice` this uses NEXA's own `user:v1` / `invoice:v1` recipe, and
    `tests/test_rules.py` proves it is byte-identical with Node.

## Entities

| Entity (records file) | Source | Target / contract | Type & unit conversion | Unique / idempotency key | Relations | Validation → review code | Blocks the package when |
|---|---|---|---|---|---|---|---|
| Customers (`records/customers.jsonl`) | `user.id`, `User_Status`, `username`, `agent`, `number`, `limit_usertest` | NEXA customer + `legacy_import_map` | `id` text, regex `^[1-9][0-9]{0,18}$`; status: exact `Active`/`block` only; username kept only if it matches `^[A-Za-z][A-Za-z0-9_]{4,31}$`; phone gives class only | `legacy:customer:<tg>` | `trial_decision` uses `invoice.is_test='1'` in any status | `INVALID_IDENTITY`, `DUPLICATE_SOURCE_ID` (one review row; no row is imported), `STATUS_UNKNOWN`, `BALANCE_UNREADABLE`, `BALANCE_OUT_OF_RANGE` | — (record level) |
| Wallet balances (`records/wallet_opening_balances.jsonl`) | `user.Balance` > 0 | Ledger CREDIT, reason `MIGRATION_OPENING_BALANCE` | `^-?[0-9]{1,19}$` only (`12.5`, `150,000` and Persian digits are unreadable); \|v\| ≤ 10¹²; amount unchanged | `legacy:opening:<tg>`. This is the reference NEXA indexes uniquely (`0186_*.sql`), so the same balance can never be credited twice | 1 per customer | see Customers | Unit unconfirmed/rial; reconciliation mismatch |
| Debts (`records/legacy_debts.jsonl`) | `user.Balance` < 0 | `legacy_wallet_debts` (`PENDING_REVIEW`) | magnitude, `auto_settle: false` | `legacy:debt:<tg>`; NEXA unique on (tenant, legacy_user_id) | 1 per customer, never both credit and debt | unreadable → review, never guessed | same as wallet |
| Products (`records/products.jsonl`) | `product.*` (no `inbounds`/`proxies`) | `legacy_product_reviews`, review only | price `^[0-9]{1,19}$` else `NOT_A_NUMBER`; `Volume_constraint`/`Service_time` are *proposals* only (units are OQ-LPR-02); `0` gives `ZERO_MEANING_UNKNOWN` | `legacy:product:<id>` | `live_invoice_count` from live invoices | `CODE_DUPLICATED` (every duplicate goes to review) | table/column missing |
| Categories (`records/categories.jsonl`) | `product.Category`/`category` | labels for the operator | text, verbatim | `legacy:category:<label>` | product count | — | — (no category table is evidenced; status *partial*) |
| Invoices (`records/invoices.jsonl`) | all `invoice` rows | `legacy_invoice_archive` | classes in this order: `KEY_SHAPE_UNRECOGNISED`, `TEST`, `TEST_FLAG_INVALID`, `ORPHAN_OWNER`, `NOT_LIVE`, `NO_PANEL`, `LIVE_CANDIDATE`; `time_sell` as unix seconds in [2015, 2100) or `FORMAT_UNKNOWN`; price kept raw and parsed | `legacy:invoice:<id_invoice>` | owner → customer; `product_ref` `NONE`/`NAMED`/`NOT_IN_PRODUCT_TABLE` | `INVOICE_WITHOUT_CUSTOMER` | `SOURCE_KEY_DUPLICATED` |
| Services (`records/services.jsonl`) | `invoice` rows with live status `active`, `disabled`, `disabledn`, `disablebyadmin`, `end_of_volume` | `legacy_service_candidates` (adoption) | shape key `legacy-shape:v1` (GB with 1 GiB = 2³⁰, days 1–3650, `time_unit` ∈ `''/d/day/days`); `provision: false` | `legacy:service:<id_invoice>` | panel account = (`code_panel`, lower(`username`)) | `ORPHAN`, `CUSTOMER_NOT_IMPORTED`, `INVALID_USERNAME`, `NO_PANEL`, `UNSUPPORTED_SHAPE`, `AMBIGUOUS_OWNERSHIP`, `INVALID_SOURCE_ROW`, `INVOICE_KEY_INVALID`; `TEST_INVOICE_SKIPPED` | invoice table/columns missing |
| Panels (`records/panels.jsonl`) | distinct `invoice.code_panel` (+ `Service_location`) | operator's `nexa-legacy-panel-map/v1` | text | `legacy:panel:<code>` | invoice counts | — | — (`panel_id` is always `null`: NEXA maps panels) |
| Orders | — | none | — | — | NEXA creates `LEGACY_ADOPTION` orders only when it adopts a service | — | — (absent by design) |
| Payments | `Payment_report` etc. | none | — | — | — | — | reported as `blocked`: the table is unclassified in NEXA |
| Resellers | `user.agent` | none; agents become ordinary customers | `is_agent` = value not in `'', f, 0, false` | — | counts by tier (`n`, `n2`, other) in the report | — | reported as `blocked` (OQ-LWD-03) |
| Review queue (`reports/review-queue.jsonl`) | — | operator work list | `{kind, severity, code, message(fa), entity, source_ref, data}` | — | — | — | blocker-severity items exclude their record |

## Service decision order

The order follows `decisions.ts:277-372` and `importer.md:380-391`:

1. `INVOICE_KEY_INVALID`
2. `TEST_INVOICE_SKIPPED`
3. `INVALID_SOURCE_ROW`
4. `ORPHAN`
5. `CUSTOMER_NOT_IMPORTED`
6. `INVALID_USERNAME`
7. `NO_PANEL`
8. `UNSUPPORTED_SHAPE`
9. `NAMED_PRODUCT` or `HIDDEN_SHAPE`
10. Ownership:
    * candidates that share one panel account but have different owners: all become `AMBIGUOUS_OWNERSHIP`;
    * several invoices from the same owner: the first by key byte order stays a candidate, and the rest
      become `AMBIGUOUS_OWNERSHIP` (OQ-LSR-03).

Panel-map checks (`PANEL_UNMAPPED`, `TEST_PANEL_SKIPPED`, `PROVIDER_MISSING`, …) and the live panel
check happen inside NEXA during adoption. The converter never calls a panel.

## Financial reconciliation (`reports/reconciliation.json`)

```
source_readable_sum = credits − debts + held_for_review + invalid_identity
users               = credits# + debts# + zero# + held_for_review# + invalid_identity# + unreadable#
```

Both equations must hold.

After the records are written, the converter re-reads the files and recounts them independently
(`independent_recount`). The recount must match too, and no customer may have two money records.

If any check fails, the package gets `FINANCIAL_RECONCILIATION_MISMATCH` and readiness `blocked`.

## Re-import and backup-to-backup comparison

* **Same backup again:**
  * `import_id = sha256(fingerprint, mapping version, unit)` is the same;
  * the records are byte-identical (tested);
  * the idempotency keys are the same.
* **Fingerprint:** it does not depend on the physical row order (tested).
* **Newer backup:** compare it with the previous job (in the UI) or the previous package (`--previous-package`).
  * The diff lists customers, invoices and services that were added or removed, and balances that changed.
  * NEXA never applies a changed opening balance (OQ-LWD-02). Each change therefore becomes a blocking
    `BALANCE_CHANGED_SINCE_PREVIOUS` review item.

## Blocked: needs real data or an owner decision

| Topic | Why it is blocked | Evidence |
|---|---|---|
| Real DDL types (is `Balance` int or decimal?) | NEXA has never seen the real DB | OQ-MZ-INV-03 |
| Toman vs Rial proven from data | owner decision only; templates disagree | OQ-MZ-INV-07, ADR-0005 |
| `price_product` formatting | grouped/decimal/Persian digits are possible | OQ-LIA |
| Units of `Volume_constraint`/`Service_time` and the meaning of `0` | proposals only | OQ-LPR-02 |
| Non-live invoice statuses and `time_unit` spellings | only five live statuses are evidenced | OQ-MZ-INV-05/09 |
| `time_sell` format and timezone in the deployed fork | partially evidenced | OQ-LIA |
| Panel tables (`marzban_panel`), `Payment_report`, `Discount`, `setting`, gift/affiliate tables | unclassified; columns may hold secrets | OQ-MZ-INV-01/02 |
| Reseller role and pricing | owner decision pending | OQ-LWD-03 |
| Whether a renewal creates a new invoice on the same account | unknown | OQ-LSR-03 |

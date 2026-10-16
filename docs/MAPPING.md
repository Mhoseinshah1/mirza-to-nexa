# Mirza → NEXA mapping (mapping version `mirza2nexa-mapping:1.0.0`)

Every rule here comes from evidence in `docs/NEXA_EVIDENCE.md`, which is itself built from a
read-only checkout of NEXA (`nexa-bot@9e604def`). The code is in `src/mirza2nexa/mapping/rules.py`
and `src/mirza2nexa/convert.py`.

**Nothing here is guessed.** If NEXA has not decided something, the converter sends the record to
the review queue or blocks it. It never makes up a value.

## Ground rules

* **Source contract:** the converter uses NEXA's frozen legacy read set v1 (`source-port.ts:36-83`), plus
  the required `user.User_Status` from NEXA's user-status read set (`read-set.ts:383-399`).
  * Tables: `user`, `invoice`, `product`. Names are matched case-sensitively.
  * Required columns:
    * `user`: `id`, `Balance`, `limit_usertest`, `User_Status`
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

The order follows `decisions.ts:277-372` and `plan.ts:311-342`:

1. `SOURCE_KEY_DUPLICATED` (a NULL key is caught as `INVOICE_KEY_INVALID`)
2. `INVOICE_KEY_INVALID`
3. `TEST_INVOICE_SKIPPED`
4. `INVALID_SOURCE_ROW`
5. `ORPHAN`
6. `CUSTOMER_NOT_IMPORTED`
7. `INVALID_USERNAME`
8. `NO_PANEL`
9. Shape and product:
   * an `is_custom` value other than `0`/`1` → `UNSUPPORTED_SHAPE/IS_CUSTOM_INVALID`;
   * a **named** product (not custom, and its code is in the `product` table) → `NAMED_PRODUCT`.
     A named product is **never** shape-checked;
   * otherwise the shape key decides between `HIDDEN_SHAPE` and `UNSUPPORTED_SHAPE`.
10. Ownership: eligible candidates of **two or more different owners** on one panel account
    (`code_panel`, lowercase username) all become `AMBIGUOUS_OWNERSHIP`.
    * Several invoices of **one** owner stay eligible. NEXA's adoption (P6) reports the later ones as
      conflicts.
    * Limitation: NEXA groups by the *mapped panel id*. Two codes that map to one panel can only be
      detected inside NEXA.

Every live invoice is counted exactly once, as converted, review or skipped. If not, the package gets
`SERVICE_ACCOUNTING_MISMATCH`.

Panel-map checks (`PANEL_UNMAPPED`, `TEST_PANEL_SKIPPED`, `PROVIDER_MISSING`, …) and the live panel
check happen inside NEXA during adoption. The converter never calls a panel.

## Financial reconciliation (`reports/reconciliation.json`)

```
source_readable_sum = credits − debts + held_for_review + invalid_identity
users               = credits# + debts# + zero# + held_for_review# + invalid_identity# + unreadable#
```

Both equations must hold.

Two further checks run after the records are written:

* **Output recount** (`independent_recount`): the written files are re-read. Their totals must match
  the converter's totals, and no customer may have two money records.
* **Source recount** (`source_recount`): the readable balances are summed again straight from the staged
  source, using SQLite `GLOB` matching instead of the converter's regex. The result must equal
  `written credits − written debts + held_for_review + invalid_identity`.

If any check fails, the package gets `FINANCIAL_RECONCILIATION_MISMATCH` or `SOURCE_RECOUNT_MISMATCH`,
and its readiness becomes `blocked`.

**Other blockers**
* a duplicate Telegram id (`DUPLICATE_SOURCE_ID`);
* a duplicate or NULL primary key on an invoice or product (`SOURCE_KEY_DUPLICATED`).

**Refused outright**
* two SQL files in one ZIP that define the same table (`schema.table_defined_twice`);
* rows for a core table that appear before its `CREATE TABLE`.

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

## Column carry (converter 0.8.0, contract 1.5.0)

Every column of every catalogued table has one disposition in `reports/coverage.json` (`column_dispositions`;
`PACKAGE_CONTRACT.md` §"Column carry"). The columns the real backup's 0.7.0 coverage report listed as not in the
package are decided as follows (declarations with notes and evidence: `src/mirza2nexa/modules/carry.py`; meanings of
fork-only columns are inferred from the name and marked UNPROVEN there). C = carried (value-redacted),
M = minimised, S = secret-dropped (listed in `reports/manual-reconfiguration.json`).

| Table | Carried | Secret-dropped | Minimised | Lands in (record_type) | Linked by |
|---|---|---|---|---|---|
| `user` (36) | 33: roll_Status, Processing_value(_one/_tow/_four), step, description_blocking, pagenumber, message_count, last_message_time, affiliatescount, affiliates, namecustom, number_username, register, verify, joinchannel, checkstatus, score, limitchangeloc, status_cron, date_message, low_balance_alert_sent, hidden_gateways, shown_gateways, first_name, commission_percentage, min_purchase_warned_month, min_purchase_evaluated_month, agent_tier_since, affiliate_alert_time, ad_source, card_message_id (a message id, not a card) | 3: codeInvitation (redeemable code), bottype (sub-bot token), token | — | `records/archive/user_carried.jsonl` (`legacy_archive_row`) | `customer.telegram_user_id` = `user.id` |
| `invoice` (9) | 9: note, inboundid (an id, not a credential), refral, time_cron, notifctions, rate_invoice, limit_user, id_payment, payable_amount | (bottype, user_info, uuid as before) | — | `records/archive/invoice_carried.jsonl` | `customer` = `id_user`; `invoice.invoice_key` |
| `product` (6) | 6: limit_user, display_order, style, usage_type, emoji_id, time_unit | (inbounds, proxies as before) | — | `records/archive/product_carried.jsonl` | `source_pk` = product id |
| `service_other` (9) | 7: id_payment, payable_amount, id_invoice, action, source, params, created_at | 2: bottype (sub-bot token), panel_response (raw panel response, like `output`) | — | `records/archive/service_other_carried.jsonl` | `customer` = `id_user`; `invoice` = `id_invoice` |
| `affiliates` (9) | every fork column not named like a credential (name rule) | credential-named ones | — | `records/configuration/affiliates_carried.jsonl` | — |
| `category` (1) | style | — | — | `records/archive/category_carried.jsonl` | — |
| `reagent_report` (2) | gift_milestone, gift_invoice | — | — | `records/archive/reagent_report_carried.jsonl` | `customer` = `user_id` |
| `setting` (33) | every fork column not named like a credential (name rule) | jwt_secret, ai_api_key (declared) + credential-named ones | — | `records/configuration/setting_carried.jsonl` | — |
| `marzban_panel` (22) | 21: hosts, status_happ, limit_user, limit_in_panel, price_pre_user, buy_additional_user, display_order, style, status_keyboard_config, content_after_buy, MethodUsername_key, btn_config, btn_sublink, emoji_id, auth_type, auto_disabled, text_afterpay, text_aftertest, hide_agent, status_extra_volume, status_extra_time | 1: proxy_sockss (a proxy definition can embed user:password; re-entered with the panel credentials) | — | `records/archive/marzban_panel_carried.jsonl` | `source_pk` = panel id |
| `admin` | id_admin, rule | username, password | — | `records/archive/admin.jsonl` | `customer` = `id_admin` |
| `botsaz` | id, id_user, admin_ids, username, hide_panel, time, product_info | bot_token, webhook_secret | setting → JSON key names | `records/archive/botsaz.jsonl` | `customer` = `id_user` |
| `global_hidden_gateways` | id, code, agent, created_at (+ name rule) | credential-named | — | `records/archive/global_hidden_gateways.jsonl` | — |
| `logs_api` | id, time, actions | header | ip → ipv4/ipv6; data → JSON key names | `records/archive/logs_api.jsonl` | — |
| `topicid` | report, idreport | — | — | `records/archive/topicid.jsonl` | — |
| `ai_business_chat` (14) | every column not named like a credential | credential-named (api_key, token, …) | — | `records/archive/ai_business_chat.jsonl` | `customer` = first of user_id, id_user, chat_id, telegram_id |

Counts for the explicitly listed columns: carried 97 (user 33, invoice 9, product 6, service_other 7, category 1,
reagent_report 2, marzban_panel 21, admin 2, botsaz 7, global_hidden_gateways 4, logs_api 3, topicid 2, plus every
non-credential fork column of `affiliates`, `setting`, `ai_business_chat`), secret-dropped 13 (user 3, service_other 2,
setting 2, marzban_panel 1, admin 2, botsaz 2, logs_api 1; invoice.bottype was already dropped), minimised 3 (botsaz.setting,
logs_api.ip, logs_api.data). The supplement records never change a 0.7.0 record: they are written beside them
(`schema: m2n.carried_columns.v1`, key `legacy:carried:<table>:<pk>`).

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

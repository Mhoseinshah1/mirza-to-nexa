# Finance history: `Payment_report` and `wallet_transaction`

Two Mirza tables hold money history. Both migrate as **read-only evidence**. Neither one is
ever a ledger entry, a payment, revenue, or an input to a wallet balance:

| Source table | Module | Records | Class |
|---|---|---|---|
| `Payment_report` | `src/mirza2nexa/modules/payments.py` | `records/payments.jsonl` | ARCHIVE |
| `wallet_transaction` | `src/mirza2nexa/modules/wallet_history.py` | `records/wallet_history.jsonl`, `records/wallet_history_checks.jsonl` | ARCHIVE (owner may reclassify as REVIEW, see below) |

The only opening figure for a wallet is the snapshot `user.Balance`
(`legacy:opening:<tg>`, `convert.py`). NEXA's rule is the same:
`wallet_transaction` and the payment history are never replayed
(nexa-bot@9e604def `docs/migration-opening-balance.md:19`,
`docs/legacy-migration/reconciliation.md:84-85`). `tests/test_wallet_history.py::test_history_never_changes_opening_balances`
proves it. With and without `wallet_transaction`, these outputs are byte-identical apart from
the source fingerprint: `wallet_opening_balances.jsonl`, `legacy_debts.jsonl`,
`customers.jsonl` and the `wallet` reconciliation.

Evidence sources:

* `BMP` = botmirzapanel@92c0ed06
* `PRO` = mirza_pro@b645db74
* `OP` = the operator's column list from the deployed backup (2026-10-10)
* `docs/SOURCE_TABLES.md` and `docs/evidence/mirza_tables.json` (Source DB workstream)

---

## 1. `Payment_report` → `legacy_payment_history`

### Columns and sensitivity

| Column | Sensitivity | Evidence |
|---|---|---|
| `id` | public, PK | BMP `table.php:390`; PRO `db/tables/Payment_report.php:6` |
| `id_user` | personal (`user.id`) | PRO `function.php:1010` |
| `id_order` | public: the payment's own random reference `bin2hex(random_bytes(5))` | PRO `index.php:4691`, `function.php:2590-2593`, `admin.php:1686` |
| `time`, `at_updated` | public: PHP `date('Y/m/d H:i:s')` | PRO `index.php:4690, 5620` |
| `price` | financial: Toman, text | PRO `index.php:4812` + `function.php:2588` (Zarinpal `IRT`) |
| `dec_not_confirmed` | personal: polymorphic text | `docs/SOURCE_TABLES.md` Q2 |
| `Payment_Method`, `payment_Status` | public: vocabularies below | — |
| `message_id` | public: Telegram message id of the payment prompt | PRO `function.php:654` |
| `receipt_trace` | financial: **meaning UNKNOWN** | OP only |
| `invoice` / `id_invoice` | personal: purpose tag `<purpose>\|<arg>` | BMP `table.php:398`; PRO `index.php:4689` |
| `bottype` | **secret**: sub-bot API token. Never staged | PRO `vpnbot/Default/index.php:1292-1296` |

Notes:

* `dec_not_confirmed` can hold receipt text, a reject reason, a gateway authority or JSON, `remove_all`, or the auto-confirm label.
* The deployed backup has no `invoice`, `id_invoice` or `bottype` column.

### Record schema (`mirza.payment_report.v1`)

```
record_type        "legacy_payment_history"
idempotency_key    "legacy:payment:<id>"
payment_id         raw id
customer           {source_user_id, telegram_user_id (valid ids only), relation, customer_decision}
                   relation: CUSTOMER_IMPORTED | CUSTOMER_IN_REVIEW | CUSTOMER_NOT_IN_BACKUP | MISSING
order              {source_order_id, relation, relation_proven, order_ref_shared_with_other_rows}
                   relation: PAYMENT_REFERENCE (10 lower-hex chars, the evidenced generator)
                           | EMPTY | NOT_FOUND (other shape)
                           | TEXT_EQUALS_INVOICE_KEY_UNPROVEN (equal text; statistic only)
purpose            {source_column, normalized}: NOT_IN_SOURCE when no purpose column exists
amount             {raw, parse_note, amount_minor, currency}: rules.parse_price; never re-scaled
method             {raw, normalized, direction}
status             {raw, outcome, reason}
times              {created, updated: {raw, format, local, unix, note}, zone}
rejection_note     {text (ctx.redact), semantics: POLYMORPHIC_UNKNOWN_PER_ROW}
telegram_message_id {raw, value}
receipt_trace      {raw (ctx.redact), meaning: UNKNOWN, redacted, shared_with_other_successful_payments}
creates_payment = affects_wallet = counts_as_revenue = enters_live_payment_state_machine
               = triggers_service = applies_to_live_state = false
provenance         {source_table, source_pk, source_fingerprint, row_checksum}
```

Inside the encrypted package, `receipt_trace` is kept raw, except that card-like numbers are
masked by `ctx.redact`. It is never written to `report.json`, `coverage.json` or the review
queue. Duplicate-receipt review rows carry only payment ids and counts.

### Vocabularies

Matching is exact and case-sensitive, because PHP compares with `==`. Any other spelling
maps to `UNKNOWN`.

| `payment_Status` | outcome | reason | evidence |
|---|---|---|---|
| `Unpaid` | PENDING | AWAITING_PAYMENT | PRO `index.php:4692-4695`; BMP `index.php:2041` |
| `waiting` | PENDING | RECEIPT_AWAITING_REVIEW | PRO `index.php:5618`; BMP `index.php:2264` |
| `paid` | SUCCEEDED | PAID | PRO `admin.php:1730,1765`, `function.php:988-996`, `cronbot/croncard.php:43`; BMP `admin.php:787` |
| `reject` | FAILED | REJECTED_BY_ADMIN | PRO `admin.php:1792,1799`; BMP `admin.php:810` |
| `expire` | CANCELLED | EXPIRED_UNPAID | PRO `cronbot/payment_expire.php:13-21`, `cronbot/plisio.php:46` |

PRO `admin.php:1708` enumerates the same five values. Admins can delete `expire` rows
(PRO `admin.php:5361`), so the archive can be incomplete.

| `Payment_Method` | normalized | direction |
|---|---|---|
| `cart to cart` (PRO `index.php:4694`, BMP `index.php:2265`) | CARD_TO_CARD | CUSTOMER_PAYMENT_MONEY_IN |
| `aqayepardakht` / `zarinpal` / `variza` | GATEWAY_AQAYEPARDAKHT / GATEWAY_ZARINPAL / GATEWAY_VARIZA | CUSTOMER_PAYMENT_MONEY_IN |
| `plisio`, `nowpayment` (PRO) / `Nowpayments` (BMP), `arze digital offline` | CRYPTO_PLISIO, CRYPTO_NOWPAYMENTS, CRYPTO_OFFLINE | CUSTOMER_PAYMENT_MONEY_IN |
| `Currency Rial 1/2/3` (PRO), `Currency Rial gateway` (BMP), `AbanGateway` | GATEWAY_RIAL_1/2/3, GATEWAY_RIAL, GATEWAY_ABAN | CUSTOMER_PAYMENT_MONEY_IN |
| `Star Telegram` | TELEGRAM_STARS | CUSTOMER_PAYMENT_MONEY_IN |
| `add balance by admin` (PRO `admin.php:3567`) | ADMIN_CREDIT_ADJUSTMENT | ADMIN_CREDIT |
| `low balance by admin` (PRO `admin.php:3613,3618`) | ADMIN_DEBIT_ADJUSTMENT | **ADMIN_DEBIT_STORED_POSITIVE** |
| anything else | UNKNOWN | UNKNOWN |

The rows above cite PRO `index.php:4772-5403` for the gateways and BMP `index.php:2042-2124`.

Purpose comes from `id_invoice` (PRO) or `invoice` (BMP), only when that column exists:

* `getconfigafterpay` → BUY_SERVICE_AFTER_PAYMENT
* `getextenduser` → EXTEND_SERVICE
* `getextravolumeuser` → EXTRA_VOLUME
* `getextratimeuser` → EXTRA_TIME

These cite PRO `index.php:3945, 1807, 2082, 2624` and `admin.php:1708`. Anything else maps to
`WALLET_TOP_UP_OR_ADMIN_ADJUSTMENT`, because `DirectPayment()` sends every other tag to
`Balance += price` (PRO `function.php:1509-1512`). The deployed backup has neither column,
so its rows get `NOT_IN_SOURCE`.

### Times

Accepted formats:

* `Y/m/d H:i:s` → `local` (ISO, no zone) with note `OK_LOCAL_TIME_ZONE_UNPROVEN`.
* A bare epoch of 9-10 digits inside NEXA's `time_sell` window → `unix` with note `OK_UNIX_SECONDS`. The weak hint is that mirza_pro's `panel/index.php:27` compares `time` with `strtotime()`.

Every other value keeps its raw text and gets a note:

* `FORMAT_UNKNOWN`: dash dates, Jalali-looking years, or Persian digits.
* `INVALID_DATE`
* `OUT_OF_RANGE`
* `EMPTY`
* `ABSENT`

mirza_pro sets `Asia/Tehran` (`index.php:3`). The deployed fork's zone is unproven, so no
UTC or Unix value is derived from a local time.

### Findings

| Code | Level | When |
|---|---|---|
| `SOURCE_KEY_DUPLICATED` (review, blocker severity) + **`PAYMENT_SOURCE_KEY_DUPLICATED` (package blocker)** | blocker | duplicate or NULL `id`. The rows are reviewed, not archived. |
| `DUPLICATE_RECEIPT` | review, blocker severity, **not** a package blocker | the same trimmed `receipt_trace` on more than one `paid` row. One row per group. |
| `PAYMENT_CUSTOMER_UNKNOWN` | warning, bounded | relation is `CUSTOMER_NOT_IN_BACKUP` or `MISSING` |
| `PAYMENT_AMOUNT_UNREADABLE` | warning, bounded | a `paid` row whose `price` is not a plain integer |
| `PAYMENT_STATUS_UNKNOWN` / `PAYMENT_METHOD_UNKNOWN` | warning | one row per distinct raw value (at most 50, plus one overflow row) |
| `PAYMENT_ORDER_RELATION_UNPROVEN`, `PAYMENT_ORDER_REF_DUPLICATED`, `PAYMENT_TIME_FORMAT_UNKNOWN` | warning | one aggregated row each |
| `PAYMENT_ACCOUNTING_MISMATCH` | package blocker | `archived + duplicate_id_rows ≠ source rows` |
| `PAYMENTS_HISTORY_ONLY` | package warning | always |

"Bounded" means at most 200 record-level rows per code. The rest becomes one aggregated row
with `total_rows` and `not_listed`.

`DUPLICATE_RECEIPT` is not a package blocker for three reasons:

* `receipt_trace` has no public meaning, so a repeated value may be a placeholder.
* These records never move money.
* NEXA never replays them.

An owner who confirms that the value is a bank trace number can make it block.

### Reconciliation (`report.reconciliation.payments`)

* `source_rows = archived + duplicate_id_rows`, giving `balanced`.
* `by_outcome`, `by_method`, `by_customer_relation` and `by_order_relation`, each as `{count, amount_minor, unparsed_amounts}`.
* `by_purpose`.
* `succeeded_excluding_admin_adjustments`. This mirrors mirza_pro's own report (`admin.php:342`).
* `orphans`, `duplicate_receipts`, `duplicate_order_refs`, `unknown_status`, `unknown_method` and `time_notes`.

The sums are **historical evidence, not revenue or credit**:

* For a direct purchase, `price` is only the shortfall above the wallet balance (PRO `index.php:3925-3927`).
* `low balance by admin` is a debit.
* Many changes to `Balance` write no payment row, for example gifts, commission, cashback and refunds (`docs/SOURCE_TABLES.md` Q3).

---

## 2. `wallet_transaction` → `legacy_wallet_transaction` + `legacy_wallet_history_check`

This table exists **only in the deployed fork**. Searching both public trees for
`wallet_transaction` finds nothing (`docs/SOURCE_TABLES.md` Q3). Every semantic below is
therefore either observed arithmetically or marked UNKNOWN.

| Column | Sensitivity | Treatment |
|---|---|---|
| `id` | public | key `legacy:wallet-tx:<id>`. A duplicate or NULL id **blocks** (`WALLET_TX_SOURCE_KEY_DUPLICATED`). |
| `id_user` | personal | customer relation, as for payments. The link to `user.id` is by name only. |
| `username`, `service_username`, `reason` | personal | `ctx.redact`, kept in the package only |
| `type`, `source` | public | raw. `normalized` is always `UNKNOWN`, because no vocabulary is evidenced. |
| `amount`, `balance_before`, `balance_after` | financial | signed integers (`parse_balance` grammar), else `NOT_A_SIGNED_INTEGER`. Never rounded. |
| `ref_id` | public | `ref_relation: "UNPROVEN"`. Matches against `Payment_report.id` and `id_order` are counted as statistics only. |
| `created_at` | public | parsed without a zone. `format_evidence` is `DDL_TYPE` when the dump's own column type is `datetime`/`timestamp` (or `int` for an epoch), else `INFERRED`. |

### Equations

* **Arithmetic:** let `d = balance_after − balance_before`.
  * The row is consistent if `d = +amount` or `d = −amount`. The sign convention is unknown, so both are counted, in `amount_convention_observed`.
  * A consistent row is `ARITHMETIC_CREDIT` if `d > 0`, else `ARITHMETIC_DEBIT`.
  * `ARITHMETIC_NO_CHANGE` if `d = 0` and `amount = 0`.
  * `INCONSISTENT` otherwise. `UNPARSEABLE` if any of the three values is unreadable.
* **Chain:** for each customer, rows are ordered by (`created_at` with no zone, numeric id, id text, dump order).
  * `balance_before(n) = balance_after(n−1)` → `CHAIN_OK`.
  * Otherwise `CHAIN_GAP`.
  * The customer's first row is `FIRST`. If a value is unreadable, the row is `CHAIN_UNPARSEABLE`.
* **Duplicate suspect:** identical (`id_user`, `type`, `amount`, `balance_before`, `balance_after`, `ref_id`, `created_at`) on rows with different ids.
* **Snapshot check:** one record per customer who has history, keyed `legacy:wallet-history-check:<tg>`. An invalid id uses `raw:<sha256>` instead.
  * The fields are `snapshot = user.Balance` (read-only) and `last = balance_after` of that customer's last row.
  * Verdicts:
    * `MATCHES_SNAPSHOT`
    * `DIFFERS_FROM_SNAPSHOT`, with `delta = snapshot − last`
    * `HISTORY_UNPARSEABLE`
    * `NO_SNAPSHOT`: the user is not in the backup, is duplicated, or has an unreadable balance
  * Customers without history are counted, not written: `imported_without_history` and `…_nonzero_snapshot`.

### Findings

* `WALLET_HISTORY_DIFFERS_FROM_SNAPSHOT`: a bounded warning, plus a package warning with its count. Differences are expected. The table was introduced late, and many changes to `Balance` are recorded nowhere. The **snapshot is never changed**.
* `WALLET_TX_ARITHMETIC_INCONSISTENT`, `WALLET_HISTORY_CHAIN_GAP`, `WALLET_TX_DUPLICATE_SUSPECT` (one per group) and `WALLET_TX_CUSTOMER_UNKNOWN`: bounded warnings.
* `WALLET_TX_TYPE_UNKNOWN` and `WALLET_TX_TIME_FORMAT_UNKNOWN`: one aggregated warning each.
* `WALLET_TX_SOURCE_KEY_DUPLICATED` and `WALLET_HISTORY_ACCOUNTING_MISMATCH`: package blockers.
* `WALLET_HISTORY_EVIDENCE_ONLY`: a package warning, always present.

### Reconciliation (`report.reconciliation.wallet_history`)

The section contains:

* `source_rows = archived + duplicate_id_rows`
* `credits_observed` and `debits_observed`
* `arithmetic`, `amount_conventions_observed`, `inconsistent_rows` and `unparseable_rows`
* `chain` and `chain_gaps`
* `duplicate_suspects`, `types` and `sources`
* `ref_id`: statistics only
* `created_at`: DDL type, notes, customers with mixed formats
* `customers`: verdict counts
* `total_delta_snapshot_minus_history_minor`
* `explanation` and `explanation_fa`

The explanation, in English and Persian, says that the history may be incomplete and is
evidence only.

---

## 3. UNKNOWN: needs confirmation on real data

Operator baseline, for orientation only and never hard-coded: 215,082 `Payment_report` rows
and 17,691 `wallet_transaction` rows.

1. Run `SELECT DISTINCT payment_Status` and `Payment_Method` on the real dump. Every value outside the tables above becomes `UNKNOWN` and is reported once.
2. `receipt_trace`: who writes it, whether it is unique, and whether it can hold card or PAN data (`ctx.redact` masks Luhn-valid numbers).
3. The time zone and format of `time`, `at_updated` and `created_at` in the deployed fork. Whether any rows hold epochs or Jalali dates.
4. Top-up versus purchase in the deployed fork. There is no purpose column, so `NOT_IN_SOURCE`. Where does the fork keep it?
5. `wallet_transaction`:
   * the column types (the DDL type is recorded in the reconciliation);
   * the `type` and `source` vocabularies;
   * the sign of `amount`;
   * the target of `ref_id`;
   * the date it was introduced;
   * whether `id` is really the primary key;
   * whether `id_user` is `user.id`.
6. Class of `wallet_transaction`. `docs/SOURCE_TABLES.md` proposes REVIEW/OWNER_DECISION. This module archives it as ARCHIVE so the evidence travels. The owner may switch it.

## 4. How a NEXA importer should store these (proposal; NEXA not edited)

* Use two **read-only** tables: `legacy_payment_history` and `legacy_wallet_history`, plus `legacy_wallet_history_checks`.
  * Each table is keyed by `idempotency_key` with `ON CONFLICT DO NOTHING`, so a re-import is a no-op.
  * Each table stores `provenance.row_checksum`. A changed checksum under the same key is a conflict for review, never an update.
* Never write to `wallet_entries`, `payments`, `payment_receipts`, orders or revenue reports. Never use a row to adjust a balance, settle a debt or trigger a service. The `MIGRATION_OPENING_BALANCE` credit comes from `user.Balance` alone.
* Customer link: use `customer.telegram_user_id` only when `relation = CUSTOMER_IMPORTED`. Otherwise store the raw `source_user_id` and leave the link NULL.
* Show the records to operators as history in customer support views. Only codes and counts appear in dashboards; `receipt_trace` and free text never do.

---

## 5. Phase 3: difference analysis (`records/wallet_difference_analysis.jsonl`)

**Question.** Why does a customer's `wallet_transaction` history end somewhere other than the
snapshot `user.Balance`? (Operator baseline, orientation only: 24 `WALLET_HISTORY_DIFFERS_FROM_SNAPSHOT`.)
The analysis **explains**; it never changes the opening balance, never re-applies history and never
creates a blocker. `tests/test_wallet_history.py::test_history_never_changes_opening_balances`
(byte-identical opening files with and without history) still holds.

### 5.1 Every public code path that changes `user.Balance`

`grep -rn wallet_transaction` over both public trees returns **0 hits**. So every Balance change in
public code is *unlogged* in that table by definition; the deployed fork must have added the logging
(at a date that is unknown). The column "row written" says what other trace a change leaves.

| # | Mechanism | Balance effect | Code (PRO = mirza_pro@b645db74, BMP = botmirzapanel@92c0ed06) | Row written (public code) | Time / amount observable | Analysis group |
|---|---|---|---|---|---|---|
| 1 | Wallet top-up confirmed (gateway callback, admin confirm, card cron) | `+price` | PRO `function.php:1509-1512` (`DirectPayment` default branch), `admin.php:1744`, `cronbot/croncard.php:48`; BMP `functions.php:400-402`, `index.php:2196-2198` | `Payment_report` → `paid` | `time` = creation, **not** the paid time; amount yes | `TOPUP` (+) |
| 2 | Direct purchase after payment (`getconfigafterpay`) | `Balance = max(0, Balance − price_product)` | PRO `function.php:1198-1201`; BMP `functions.php:381-385` | `Payment_report` + `invoice` | invoice `time_sell` (epoch) | `PURCHASE` (−price; inexact by design) |
| 3 | Wallet purchase | `−price_product` | PRO `index.php:3951` (`deductBalance`, `function.php:968-977`), `index.php:4495` (multi-buy); BMP `index.php:1927-1928` | `invoice` (`index.php:3920`, `time_sell = $date = time()`, `index.php:78`) | yes / yes | `PURCHASE` (−) |
| 4 | Renewal, extra volume/time, location change, custom volume, renewal of unlisted user | `−price` | PRO `index.php:1821, 2093, 2294, 2635, 5973, 6688`; BMP `index.php:991-992, 1202-1203` | `service_other` (`extend_user`, `extra_user`, `change_location`, `extra_time_user`, `extra_not_user`, `extends_not_user`; `status` NULL/paid) | `time` / `price` | `SERVICE_OPERATION` (−) |
| 5 | Direct-pay renewal / extra volume / extra time | `Balance = 0`, then renewal cashback **overwrites** it | PRO `function.php:1277-1278, 1337, 1371-1372, 1443-1444` | `service_other` + `Payment_report` | yes | `SERVICE_OPERATION` (inexact by design) |
| 6 | Admin "add balance" / "low balance" | `+text` / `−text` | PRO `admin.php:3565-3573`, `3611-3619`; BMP `admin.php:985-987, 1008-1010` | PRO: `Payment_report` `paid`, `add/low balance by admin` (debit stored positive). **BMP: none** | PRO: yes / yes | `ADMIN_ADJUSTMENT` (±) |
| 7 | Admin adds an amount when confirming a receipt manually | `+text` | PRO `admin.php:4038-4049` | none (the receipt row keeps its own price) | no | — (unobservable) |
| 8 | Admin bulk credit to all/filtered users | `+price` | PRO `admin.php:2129-2130`; BMP `admin.php:1480` | none | no | — |
| 9 | Admin reset to zero | `= 0` | PRO `admin.php:7231-7234`; web API `api/users.php:493` | none | no | — |
| 10 | Web panel / API credit and debit | `±amount` | PRO `panel/user.php:23-26`, `api/users.php:293, 312` | none | no | — |
| 11 | Refund: service removed by admin, cancel request, manual refund amount, API refund | `+price` | PRO `admin.php:3788-3791, 4741-4744, 4787-4790`, `api/invoice.php:159-161`; BMP `admin.php:1742-1744` | invoice status only (`removebyadmin`) | no | — |
| 12 | Refund when panel creation/renewal fails | `+price` | PRO `index.php:1849, 2107, 2338, 2355, 2652, 4003, 4547, 5994, 6694`, `function.php:1065-1066, 1281-1282` | none | no | — |
| 13 | Gateway / card / Stars cashback | `+price × % / 100` | PRO `admin.php:1745-1750`, `cronbot/croncard.php:49-55`, `index.php:6563-6569`, `payment/zarinpal.php:74-79`, `payment/nowpayment.php:29-34`, `payment/iranpay4.php:114-119`, `payment/variza_webhook.php:117-122`, `cronbot/plisio.php:54-60`, `cronbot/iranpay1.php:35-36` | none | no (hint only, §5.3) | — |
| 14 | Referral commission on purchase | `+price × % / 100` to the referrer | PRO `index.php:4066-4102`, `function.php:1138-1175`, `api/miniapp.php:1060-1091`; BMP `index.php:1824-1829`, `functions.php:376` | none | no | — |
| 15 | Referral join gift (both sides) | `+price_Discount` | PRO `index.php:334` (at /start), `index.php:5859-5872` (claim); BMP `index.php:205-210` | `reagent_report` (time = **sign-up**, `index.php:235-238`; amount not stored) | time only | `REFERRAL_GIFT` (unquantified) |
| 16 | Gift code redeemed | `+Discount.price` | PRO `index.php:5777`; BMP `index.php:2333-2334` | `Giftcodeconsumed` (no time, no amount) | no / no | `GIFT_CODE` (unquantified, unplaceable) |
| 17 | Lucky wheel win | `+wheel price` | PRO `index.php:6253` | `wheel_list` (`index.php:6269-6275`, `price`, `time`) | yes / yes | `WHEEL_PRIZE` (+) |
| 18 | Lottery prize | `+prize` | PRO `cronbot/lottery.php:61-63` | none | no | — |
| 19 | Agent request fee / refund on reject | `−fee` / `+fee` | PRO `index.php:6105`, `admin.php:5091-5097` | `Requestagent` (no amount) | no | — |
| 20 | Sub-bot sale debits the main bot owner | `−price_productMain` | PRO `vpnbot/Default/index.php:1221-1222, 1866-1867` | sub-bot invoice | not linkable | — |
| 21 | Account transfer (`transferaccount`) | Balance moves with the renamed `user.id` | PRO `admin.php:7020-7043` updates `user.id`, `Payment_report`, `invoice`, `service_other`, `Giftcodeconsumed`… | the fork's `wallet_transaction.id_user` is **not** in that list (unknown) | — | a history under the old id looks like a different customer |
| 22 | Paid `Payment_report` with an unknown method | unknown | — | `Payment_report` | time yes | `PAYMENT_UNKNOWN_METHOD` (unquantified) |

### 5.2 Features computed per customer (observable only)

For each `DIFFERS_FROM_SNAPSHOT` customer and each `ORDER_UNPROVEN` customer:

* `delta = snapshot − last balance_after`, its sign and magnitude; the last history row's id and time.
* Every evidence row of groups 1-6, 15-17 and 22, placed **relative to the last history row**:
  * a local wall time (zone unproven) is any instant in `[L − 14 h, L + 12 h]`; an epoch is exact;
  * `AFTER` if the event's earliest instant is later than the row's latest instant, `BEFORE` if its
    latest is earlier, otherwise `IN_WINDOW`; an unreadable time is `UNPLACEABLE`;
  * per group and placement: count, signed sum, unreadable amounts.
* `exact_matches`: which sums equal `delta` (`<GROUP>_AFTER`, `<GROUP>_AFTER_AND_WINDOW`, `ALL_AFTER`, `ALL_AFTER_AND_WINDOW`).
* `unit_hint`: `snapshot == 10 × history` or `history == 10 × snapshot` (a flag, never a conversion).
* `order`: whether another row with the **same** last timestamp (or, for `ORDER_UNPROVEN`, any row) ends at the snapshot.
* `cashback_percent_hint`: `(delta − later top-ups) / later top-ups × 100`, for comparison with the `PaySetting` cashback values.

### 5.3 Categories and certainty rules

Evaluated in this order; the first rule that holds wins.

| Cause | Certainty | Rule | Proposed action |
|---|---|---|---|
| `ORDER_UNPROVEN` | PROBABLE / UNEXPLAINED | verdict `ORDER_UNPROVEN`: PROBABLE if some row ends at the snapshot; or a tied last timestamp whose other row ends at the snapshot | `NONE` if PROBABLE, else `OWNER_DECISION` |
| `UNLOGGED_TOPUPS`, `UNLOGGED_PURCHASES`, `UNLOGGED_SERVICE_OPERATIONS`, `UNLOGGED_ADMIN_ADJUSTMENTS`, `UNLOGGED_WHEEL_PRIZES` | **PROVEN** | `delta` = signed sum of all `AFTER` events, all in one group, and **nothing uncertain**: no `IN_WINDOW`/`UNPLACEABLE` event, no unreadable amount, no referral/gift/unknown-method row | `ARCHIVE_ONLY` |
| `UNLOGGED_MIXED_ACTIVITY` | **PROVEN** | as above with ≥ 2 groups | `ARCHIVE_ONLY` |
| the matching group's cause (or MIXED) | PROBABLE | an exact match that needs window events, ignores another group's events, or coexists with uncertain evidence | `OWNER_DECISION` |
| `UNIT_SUSPECT` | PROBABLE | no arithmetic match and a 10× relation (flag only) | `OWNER_DECISION` |
| `ACTIVITY_AFTER_HISTORY_INEXACT` | PROBABLE | evidence after/around the history that does not add up (cashback, refunds, direct-pay clamping, partial logging) | `OWNER_DECISION` |
| `UNEXPLAINED` | UNEXPLAINED | no observable evidence at all (paths 7-14, 18-21 leave none) | `OWNER_DECISION` |

PROVEN is an exact arithmetic identity with a cited mechanism; it is still not a ledger. A paid
`Payment_report` row created before the last history row but confirmed after it cannot be seen
(the paid time is not stored), which can only lower the certainty, never raise it.

Records: `record_type legacy_wallet_difference`, schema `mirza.wallet_transaction.difference.v1`,
key `legacy:wallet-difference:<tg>`, `features`, `cause`, `certainty`, `proposed_action`,
`evidence_refs` (`{table, pk, role}` only — no free text; a pk that is not `[0-9A-Za-z_-]{1,64}` is
replaced by a keyed `pk_ref`; at most 50 per record), all live-state flags `false`,
`opening_balance_changed: false`.

Reconciliation `report.reconciliation.wallet_differences` (always present; `status NO_DIFFERENCES`
when nothing differs or the table is absent): `total`, `from_verdict`, `by_cause`, `by_certainty`
(`PROVEN`/`PROBABLE`/`UNEXPLAINED`), `by_cause_and_certainty`, `proposed_actions`,
`proven_abs_delta_by_cause_minor` (Σ|delta| for PROVEN only), `owner_decisions`,
`evidence_sources`, `time_comparison`, `certainty_rules`.

### 5.4 Owner decisions (codes in `owner_decisions`)

| Code | When | What the owner decides |
|---|---|---|
| `WALLET_DIFF_UNEXPLAINED_REVIEW` | any UNEXPLAINED | accept the snapshot as the opening figure for these customers (default) or investigate with the old bot's Telegram report channel (paths 7-14 leave only Telegram messages) |
| `WALLET_DIFF_PROBABLE_CONFIRM` | any PROBABLE other than an order tie | confirm the probable mechanism, e.g. cashback percentages, refunds |
| `WALLET_DIFF_UNIT_SUSPECT_CONFIRM_UNIT` | any `UNIT_SUSPECT` | whether these histories were written in Rial (see `money_unit_evidence`) |
| `WALLET_DIFF_PROVE_TIME_ZONES` | an event fell in the 26 h window | prove the zones of `wallet_transaction.created_at`, `Payment_report.time` and the server, so the window can be narrowed |

### 5.5 Money unit evidence (`report.reconciliation.money_unit_evidence`)

Statistics only; `decision: OWNER`. Distributions (positive values, zero, unparsed, min/p10/p50/p90/max,
shares that are multiples of 10/100/1000) for paid customer payments, admin adjustments, product
prices, invoice prices, `service_other` prices, non-zero balances and wallet amounts; cross checks
(`payment_customer_paid_vs_product_price`, `invoice_price_vs_product_price`, …) counting values equal
to a reference value, to 10× one and to 1/10 of one, and the median ratio. The code evidence for Toman
is listed in the section. Read: same scale ⇒ many exact equalities and median ratio of order 1; mixed
units ⇒ a visible share at exactly 10× or 1/10. Direct purchases pay only the shortfall, so not every
payment matches a product.

`report.reconciliation.payments.unknown_status` is `{rows, distinct_values, top_values}` (top 20 raw
values, redacted and truncated).

### 5.6 Re-verifying the 24 on the real backup (offline, operator machine only)

1. Convert the real backup with `money_unit=toman` as usual (local only, nothing uploaded).
2. In `report.json`: `reconciliation.wallet_history.customers.differs_from_snapshot` must equal the
   `WALLET_HISTORY_DIFFERS_FROM_SNAPSHOT` warning count (baseline 24), and
   `reconciliation.wallet_differences.from_verdict.DIFFERS_FROM_SNAPSHOT` must equal it too.
3. Check `wallet_differences.evidence_sources`: `Payment_report`, `invoice`, `service_other` and
   `wheel_list` should be `USED`; an `ABSENT`/`COLUMNS_MISSING` source makes some causes unobservable.
4. Read `by_cause_and_certainty`. For each PROVEN record, re-add its `evidence_refs` by hand
   (`SELECT price FROM Payment_report WHERE id IN (…)`, `SELECT price_product FROM invoice WHERE
   id_invoice IN (…)`) and confirm the sum equals `features.delta_snapshot_minus_history_minor`.
5. Take `owner_decisions` to the owner. Nothing in the package changes until then; the opening figure
   remains `user.Balance` in every case.

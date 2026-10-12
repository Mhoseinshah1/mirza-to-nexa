# Engagement and support history

This document covers two modules, both archive-only:
* `src/mirza2nexa/modules/engagement.py` (order 410)
* `src/mirza2nexa/modules/support.py` (order 420)

Neither module redeems, credits, notifies or creates anything in NEXA. Evidence is cited as `file:line`
against mirza_pro@b645db74 and botmirzapanel@92c0ed06, and matches `docs/SOURCE_TABLES.md` Q5–Q7.

## Engagement

### Tables

| Table | Class | Evidence | Records |
|---|---|---|---|
| `Discount` | REVIEW | `table.php:426-429`; `db/tables/Discount.php:5-9`; redeem `index.php:5763-5785` (credits `price` via `addBalance`) | `discounts.jsonl` kind `GIFT_BALANCE_CODE` |
| `DiscountSell` | REVIEW | `table.php:547-562`; `db/tables/DiscountSell.php:5-16`; apply `index.php:1646-1700` (`price` = percent, `index.php:1698`) | `discounts.jsonl` kind `PURCHASE_PERCENT_CODE` |
| `discount_sell` | REVIEW (fork, no DDL) | operator list | `discounts.jsonl` kind `UNKNOWN`, `fields_raw` |
| `Giftcodeconsumed` | ARCHIVE | `table.php:444-447`; `Giftcodeconsumed.php:5-7`; written by both code kinds (`index.php:1830,5783`) | `discount_usage.jsonl` |
| `discount_sell_used` | REVIEW (fork, no DDL) | operator list | `discount_usage.jsonl` |
| `affiliates` | CONFIGURATION (single settings row) | `table.php:573-585`; `affiliates.php:5-25`; gift split `index.php:5864` | `program_settings.jsonl` |
| `reagent_report` | ARCHIVE | `reagent_report.php:6-10`; `index.php:235-238,5859` | `referrals.jsonl` |
| `wheel_list` | ARCHIVE | `wheel_list.php:6-11`; `index.php:6240-6275` | `wheel_results.jsonl` |
| `ad_campaign` | REVIEW (fork, no DDL) | operator list | `ad_campaigns.jsonl` |

Every record has `redeemable: false`, `grants_credit: false`, `affects_wallet: false` and
`applies_to_live_state: false`. Referral records also have `pays_commission: false`.

### Decision: discount and gift codes are bearer secrets

An unused `Discount.code` credits the wallet of anyone who types it, and a `DiscountSell` code gives a
discount. While Mirza is still running, a leaked code is worth money. So:

* **Documented code columns are never staged.** `Discount.code`, `DiscountSell.codeDiscount` and
  `Giftcodeconsumed.code` are SECRET (`docs/evidence/mirza_tables.json`). They are dropped at parse time,
  never touch disk, and appear in coverage as `dropped_secret_columns`.
* **Consequence: usage cannot be joined to codes.** Usage rows carry `code_match: UNAVAILABLE_CODE_SECRET`
  and `code_ref: null`. Code records carry `usage_link: UNAVAILABLE_CODE_SECRET` and `consumed_rows: null`.
* **Code state comes from Mirza's own counters.** It is `UNUSED`, `PARTIALLY_USED`, `EXHAUSTED` or
  `UNKNOWN`, computed from `limitused`/`limituse` or `usedDiscount`/`limitDiscount`.
* **The only usage check is aggregate.**
  * The sum of all use counters is compared with the number of `Giftcodeconsumed` rows.
  * Both code kinds bump a counter and log one row per use (`index.php:1826-1830,5779-5783`).
  * A difference goes to `DISCOUNT_USAGE_TOTAL_MISMATCH`. This is a warning, because admins delete log
    rows and codes (`admin.php:2341,3932`).
* **Fork tables are handled separately.** `discount_sell` and `discount_sell_used` have no public DDL and
  are staged by the generic name filter. A code-named column (`code`/`codeDiscount`/`discount_code`/
  `code_discount`/`coupon`/`gift_code`) is removed from `fields_raw` and exported only as
  `code_ref` = `"code:" + sha256("m2n:code:v1:" + source_fingerprint + ":" + code)[:24]`.
  These refs link fork usage to fork codes by exact string.
* **Reports and review data** never carry a code or a `code_ref`.

### Rules

* **Gift amount.** Parsed with `rules.parse_price`. A readable value becomes `credit_amount` =
  `{amount_minor, currency}` and is never re-scaled. An unreadable value goes to `DISCOUNT_AMOUNT_UNREADABLE`,
  with the raw value kept.
* **Usage rows.** A user who is not an imported customer goes to `DISCOUNT_USAGE_CUSTOMER_MISSING`. A
  fork usage row whose code is not among the fork codes goes to `DISCOUNT_USAGE_CODE_UNKNOWN`.
* **`DiscountSell` fields.**
  * `time` is the expiry in unix seconds; `0` means no expiry.
  * `type` is `all` or `extend`; any other value is `UNKNOWN`, with the raw value kept.
  * The scope fields (`agent`, `code_product`, `code_panel`, `usefirst`, `useuser`) are kept raw.
* **Referrals (`reagent_report`).**
  * `user_id` is the referee and `reagent` is the referrer. Both are validated against the customer
    decisions.
  * A missing party goes to `REFERRAL_PARTY_MISSING`, with data `{missing: [...]}`.
  * A referrer equal to the referee goes to `REFERRAL_SELF`.
  * `get_gift` becomes `referrer_gift_paid`.
* **Wheel.**
  * `price` is the amount already credited in Mirza, so the balance already contains it. It is recorded,
    with `prize_already_in_legacy_balance: true`, and never credited again.
  * `first_name` is personal and not needed, so it is **not exported**.
* **Times.**
  * Unix seconds become `…Z`.
  * `Y/m/d H:i:s` is Asia/Tehran server time with no stored offset (`index.php:3`). It is kept as naive
    local time (`OK_TEHRAN_LOCAL`) and never shifted.
  * A year below 1600 gives `JALALI_SUSPECTED`. Anything else gives `FORMAT_UNKNOWN`, with the raw value kept.

### Code lifecycle (`discounts_state`)

Every code row also gets `lifecycle_state`, `lifecycle_reason`, `remaining_uses` and (wallet gifts only)
`outstanding_gift_value`. The classes come from Mirza's own refusal rules, not from guesses:

| Kind | State | Rule (reason) | Evidence (mirza_pro@b645db74) |
|---|---|---|---|
| `Discount` (wallet gift) | `USED` | `limitused >= limituse` (`LIMIT_REACHED`; includes `limituse` 0) | refusal `index.php:5759` |
| | `HAS_FINANCIAL_OBLIGATION` | uses remain and `price` > 0; outstanding = remaining × price (`REMAINING_USES_CREDIT_WALLET`) | credit `index.php:5776` (`addBalance`), counter `:5778-5779`, once per user `:5763-5769` |
| | `ACTIVE_UNUSED` | uses remain but `price` = 0 (`REMAINING_USES_ZERO_VALUE`) | — |
| | `UNKNOWN` | counters unreadable (`COUNTERS_UNREADABLE`) or price unreadable while uses remain (`GIFT_AMOUNT_UNREADABLE`) | — |
| | (never `EXPIRED`) | the table has no expiry column | `db/tables/Discount.php:5-9` |
| `DiscountSell` (percent) | `USED` | `limitDiscount <= usedDiscount` (`LIMIT_REACHED`) | `index.php:1663,4189` |
| | `EXPIRED` | `time` ≠ 0 and `as_of >= time` (`EXPIRED_BEFORE_BACKUP`) | `index.php:1655,4185` |
| | `ACTIVE_UNUSED` | uses remain, not expired (`REMAINING_USES_PERCENT_DISCOUNT`); a percent code never credits a wallet, so it is never an obligation | `index.php:1698` |
| | `UNKNOWN` | counters/expiry unreadable, or no `as_of` (`EXPIRY_REFERENCE_UNAVAILABLE`) | — |
| fork `discount_sell` | `UNKNOWN` | `FORK_SCHEMA_UNPROVEN` | no public DDL |

`as_of` is the newest valid `invoice.time_sell` in the backup (`lifecycle_as_of_basis: LATEST_INVOICE_TIME_SELL`),
never the wall clock, so the result is deterministic. Mirza keeps running until cutover, so a later backup can
move codes from `ACTIVE_UNUSED` to `EXPIRED`/`USED`.

`discounts_state` (reconciliation) reports `by_state` (all five classes), `by_reason`, `by_kind`,
`outstanding_gift_value_minor` (string, **raw source units, never re-scaled**; `null` when any gift code with
possible remaining uses has an unreadable amount or counter — the readable part is in
`outstanding_gift_value_known_minor`), `outstanding_gift_codes`, `outstanding_remaining_uses`,
`gift_codes_value_unknown`, `currency`, `as_of`, and `issues_new_credit: false`, `new_codes_created: 0`,
`redeemable_true: 0`. The outstanding figure is an **upper bound** of wallet credit Mirza would still pay if every
remaining use were redeemed; it is an owner-decision liability figure, not a ledger entry.

Aggregate reviews (one row each, never per code, never with code text or `code_ref`):
`GIFT_OUTSTANDING_OBLIGATION`, `GIFT_VALUE_UNKNOWN`, `DISCOUNT_ACTIVE_CODES`, `DISCOUNT_STATE_UNKNOWN`.

### Referral and wheel readiness (`referrals_wheel_state`)

* `referrals`, `referrals_both_customers`, `referrals_party_missing`, `referrals_self`.
* `start_gift_claimed` (`get_gift` = 1) and `start_gift_unclaimed_contingent` (`get_gift` = 0): the referee can
  still press *get_gift_start* while Mirza runs (`index.php:5844-5866`, referrer gets `price_Discount`/2). The
  value is read from `affiliates` at claim time, so **no amount is summed**; it is a contingent count only.
* `wheel_spins`, `wheel_wins`, `wheel_prizes_total_minor`, `wheel_prize_already_in_legacy_balance: true`: the prize
  is credited by `addBalance` *before* the row is written (`index.php:6253,6269`), so it is already in
  `user.Balance`. `credited_by_package: "0"`.

The `engagement` reconciliation section reports:
* codes by kind and by state, duplicate fork codes, `used_counter_sum`, `usage_log_rows` and `usage_totals_match`;
* usage by match, and usage rows whose customer is missing;
* referral counts: total, both customers, party missing, self-referral, gift paid;
* wheel counts: spins, wins, unreadable prizes, missing customers, prize total with `credited_by_package: "0"`;
* `codes_exported_as_plaintext: 0`, `redeemable_true: 0` and `grants_credit_true: 0`.

## Support

### Tables

| Table | Class | Evidence | Records |
|---|---|---|---|
| `departman` | CONFIGURATION | `departman.php:7-9`; `admin.php:6596` | `departments.jsonl` (`mapping_state: OPERATOR_MUST_MAP`) |
| `support_message` | ARCHIVE | `support_message.php:6-13`; `index.php:3213-3300` | `support_messages.jsonl` |
| `ticket` | REVIEW (fork, no DDL) | operator list | `tickets.jsonl` |
| `ticket_message` | REVIEW (fork, no DDL) | operator list | `ticket_messages.jsonl` |

Every record has `applies_to_live_state: false`, `notifies_customer: false`, `creates_ticket: false` and
`affects_wallet: false`.

### Rules

* **Message text is personal.**
  * `text`, `result`, fork message columns, and `Requestagent.Description` (agents module) stay
    **only inside the encrypted package**.
  * They are passed through `ctx.redact`, which replaces bot tokens, subscription URLs, proxy links,
    UUIDs, card numbers, JWTs and long opaque tokens. Redaction counts appear in coverage as
    `redacted_cells`.
  * Report, reconciliation, coverage and review data never contain message text. Tests check this, using a
    planted fake bot token and Persian/emoji text.
* **Threads.**
  * `support_message` has one row per message and a fresh random `Tracking` per message. It has no
    parent link.
  * A thread is therefore *inferred* as the group (customer `iduser`, `name_departman`)
    (`thread_basis: INFERRED_SAME_CUSTOMER_AND_DEPARTMENT`).
  * Messages in a thread are ordered by parsed time, then numeric id. Rows with an unreadable time go last.
  * Each message gets `thread_ref`, `seq` and `thread_size`.
* **Fork tickets.**
  * `ticket_message` rows are grouped by the inferred ticket column
    (`ticket_id`/`id_ticket`/`ticketid`/`ticket`) and ordered by time, then id.
  * A message whose ticket is absent goes to `TICKET_MESSAGE_ORPHAN`.
* **Status.**
  * The only `support_message` statuses with a public writer are `Unseen` (`index.php:3224`), `Answered`
    (`:3264`) and `Customerresponse` (`:3290`). They are proposed as NEXA `OPEN`, `WAITING_FOR_CUSTOMER` and
    `WAITING_FOR_SUPPORT` (nexa `tickets.ts:40-45`).
  * `Pending` and `close` have no proven writer. They give `UNKNOWN`, with the raw value kept, and
    `SUPPORT_STATUS_UNKNOWN`.
  * Fork ticket statuses are read as literal tokens with `status_basis: STATUS_TOKEN_UNPROVEN` (see State).
* **Owner.**
  * A `support_message.iduser` or fork ticket owner that is not an imported customer (absent, not in the
    backup, or held for review) goes to `TICKET_OWNER_UNKNOWN`, severity warning.
  * The record is still archived.

### State (`support_state`)

| Source | Class | Rule | Basis |
|---|---|---|---|
| fork `ticket` | `OPEN` / `ANSWERED` / `CLOSED` / `UNKNOWN` | literal reading of the status token (`status`/`state`/`ticket_status` column): open, new, pending, waiting, unseen, customerresponse → OPEN; answered, replied → ANSWERED; closed, close, resolved, done → CLOSED; anything else (e.g. `7`) → UNKNOWN with `status_raw` | `status_basis: STATUS_TOKEN_UNPROVEN` (no public DDL or writer) |
| `support_message` thread | state of its **last** message | `Unseen` (`index.php:3224`) and `Customerresponse` (`:3290`) → OPEN; `Answered` (`:3264`) → ANSWERED; `Pending`/`close` → UNKNOWN | proven writers |

`support_state` reports `tickets`, `open_tickets`, `tickets_by_status`, `ticket_messages`,
`ticket_message_orphans`, `support_messages`, `support_threads`, `support_threads_by_state`,
`support_threads_open`, `imported_into_nexa_tickets: false`, and `creates_ticket_true`, `sends_message_true`,
`notifies_customer_true` (always 0).

**Open items are an owner decision, never re-opened.** One aggregate review each:
`TICKET_OPEN_OWNER_DECISION` (`tickets`, up to 20 `sample_pks`) and `SUPPORT_THREAD_OPEN_OWNER_DECISION`
(`threads`). The owner should answer or close them in Mirza before cutover; nothing is created in NEXA and no
customer is messaged (`creates_ticket`, `sends_message`, `notifies_customer` are `false` on every record).

### Where legacy support history belongs in NEXA (proposal)

Every support record carries `nexa_view: LEGACY_READ_ONLY_THREAD_UNDER_CUSTOMER_SUPPORT`:

* Shown **read-only** inside the customer's support section (admin panel → customer → support), as
  "legacy Mirza conversations", separate from live NEXA tickets (nexa-bot@9e604def
  `packages/contracts/src/tickets.ts:40-45` statuses are for live tickets only).
* One thread per `thread_ref` (support_message) or per fork ticket, messages ordered by `seq`; the legacy
  status is shown as a label (`thread_state` / ticket `status` + basis), never mapped to a live NEXA status.
* No reply box, no notification, no SLA timers; replying means opening a **new** NEXA ticket by hand.
* Keyed by `idempotency_key`; re-import is a no-op. Needs an approved NEXA importer (`docs/NEXA_IMPORTER_PLAN.md`).

### Bounded review output

Per-row findings (`TICKET_OWNER_UNKNOWN`, `TICKET_MESSAGE_ORPHAN`, `DISCOUNT_USAGE_*`, `REFERRAL_*`,
`WHEEL_CUSTOMER_MISSING`, `DISCOUNT_AMOUNT_UNREADABLE`, `AGENT_*`) list the first 20 rows per (code, table); the
rest become one aggregate row `{aggregated: true, rows, total_rows, reason}`. With the operator's real sizes
(`wheel_list` 92,257, `reagent_report` 11,805, `discount_sell_used` 3,991) the queue stays small and the counts stay
exact. Every review row of these modules carries a `reason` tag.

The `support` reconciliation section reports:
* departments, messages, threads, unknown owners and statuses, unreadable times, status proposals;
* tickets with an unknown owner, ticket messages and orphans;
* `text_in_reports: false`.

## Owner decisions needed

* **OQ-M2N-EN-02.** Unredeemed gift and discount codes are an outstanding liability
  (`discounts_state.outstanding_gift_value_minor`, per class in `by_state`). Should any of them be
  honoured in NEXA as **new** NEXA codes or credit, or lapse at cutover? The default is lapse: nothing is
  redeemable and the converter never issues credit.
* **Open tickets / unanswered threads** (`support_state.open_tickets`, `support_threads_open`). Close or answer
  them in Mirza before cutover; the converter never re-opens them in NEXA.
* **OQ-M2N-EN-03.** Per-code usage history is lost because the codes are SECRET. If the owner needs it,
  the framework would have to compute a keyed digest **at parse time** before dropping the column. That
  is a staging change, not this module's.
* **OQ-M2N-EN-01 / OQ-M2N-SU-01.** We need the fork DDL (redacted) for `discount_sell`, `discount_sell_used`,
  `ad_campaign`, `ticket` and `ticket_message`. Are these renames of `DiscountSell`/`Giftcodeconsumed`/
  `support_message`, or new features?
* **Referral history.** Should it seed NEXA referral attribution? The default is no: NEXA referral starts
  fresh (nexa `docs/wp9-referral-audit.md`). `user.affiliates` (the referrer column) is not staged today, so
  only `reagent_report` relations are archived.
* **Support history.** Should it be imported into NEXA tickets as closed read-only threads? This needs a NEXA
  importer and approval. Department → ticket-category mapping is manual.

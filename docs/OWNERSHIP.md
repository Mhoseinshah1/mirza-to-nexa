# Service ownership and transfers (Phase 3)

The question: **which Telegram account owns each Mirza service, and how do we know it?** The model is
in `src/mirza2nexa/modules/ownership.py` (module key `ownership`, order 350). `services_ext` (order 300)
uses the same model to decide which rows get `SERVICE_OWNER_CONFLICT`.

Sources: **MP** = public `mirza_pro@b645db74`, **BMP** = public `botmirzapanel@92c0ed06`. Neither is the
deployed fork, so every statement below is a hypothesis for the deployed bot until the operator checks it
on the real backup (last section).

## 1. Evidence

### 1.1 Customer service transfer ("انتقال سرویس به کاربر دیگر"), MP `index.php`

| Step | Code | Effect |
|---|---|---|
| Button | `index.php:1027-1030`, removed for test services at `index.php:1040-1043` | callback `transfer_<id_invoice>` |
| `transfer_` | `index.php:2833-2859` | `invoiceBelongsToUser(invoice, from_id)` (`function.php:735-738`: `invoice.id_user === from_id`). Refuses the test-service label (`2839-2842`) and a panel status of `expired`/`limited`/`disabled` (`2845-2849`). Stores the panel username in `user.Processing_value_one` and the invoice id in `user.Processing_value_tow`, then sets step `getidfortransfer`. |
| `getidfortransfer` | `index.php:2860-2874` | The target must exist: `rowExists("user","id",$text)`. Overwrites `Processing_value_one` with the target id and shows `confrimtransfers_<id_invoice>`. |
| `confrimtransfers_` | `index.php:2875-2900` | Refuses a transfer to oneself (`2876-2879`), checks `invoiceBelongsToUser` again (`2882-2884`), then: **`UPDATE invoice SET id_user = <target> WHERE id_invoice = …`** (`2885`), notifies the target (`2894-2895`), and **`INSERT IGNORE INTO service_other (id_user, username, value, type, time, price)`** with `id_user = sender`, `username = invoice.username`, `value = target`, `type = 'transfertouser'`, `price = "0"` (`2896-2900`). |

Facts that follow:

* **Where the transfer happens.** `invoice.id_user` changes. Nothing else changes: not `user`, not
  `Payment_report`, not the earlier `service_other` rows, not `cancel_service`, and not the panel
  username. `invoice.username` is never updated anywhere in MP; a grep for `update("invoice", "username"`
  or `invoice SET username` finds nothing.
* **The current owner** is kept in `invoice.id_user`. It is the field every customer service action
  checks, through `invoiceBelongsToUser` at `index.php:1386,1922,1949,1989,2045,2167,2205,2405,2448,2528,2586,2708,2836,2882,5888,5929,6493,6508`.
* **The original owner** stays only in history:
  * the `id_user` of the first `transfertouser` row;
  * the earlier `service_other` rows (extend, extra, …) that the old owner wrote, which are never
    rewritten;
  * the old owner's `cancel_service` rows.

  Payments stay with the payer in `Payment_report`.
* **Transfer history exists** only as `service_other` rows with `type='transfertouser'`, one per
  transfer. Each row has `id_user` (sender), `value` (target), `username` (panel account) and `time`
  (`Y/m/d H:i:s`, Asia/Tehran). `service_other.id` is `AUTO_INCREMENT` (`db/tables/service_other.php:5`),
  so ids give the insertion order.
* The update (`2885`) and the log row (`2896`) read the **same variable**,
  `$user['Processing_value_one']`. So the logged target always equals what was written to
  `invoice.id_user`.
* **The log is not guaranteed.** The insert comes after the update and is `INSERT IGNORE`. The confirm
  step does not re-check that the target still exists. `Processing_value_one` is shared with many other
  flows (63 uses in `index.php`). A missing or stale log row is possible, so an invoice can change owner
  without a usable record.
* **Who can transfer.** Any customer can transfer a service whose invoice they own. There is no agent
  restriction and no fee (`price 0`).
* **Restrictions.**
  * Not a test service.
  * The panel account is not expired, limited or disabled.
  * The target exists in `user`.
  * The target is not the sender.
* **BMP** has no transfer feature: no `transfer` and no `invoice.id_user` update in its PHP.

### 1.2 Admin whole-account transfer ("انتقال حساب"), MP `admin.php:7020-7044` and API `api/users.php:423-448`

Only an `administrator` can do this (`admin.php:7020`). The API needs the API credentials.

1. The target must exist (`7026`) and differ from the source (`7030`).
2. **The target's own user row is deleted** (`7035-7037`).
3. `UPDATE user SET id = target WHERE id = source` (`7038`).
4. `id_user := target` in these tables:

   | Table | Code |
   |---|---|
   | `Payment_report` | `7039` |
   | `invoice` | `7040` |
   | `support_message.iduser` | `7041` |
   | `service_other` | `7042` |
   | `Giftcodeconsumed` | `7043` |
   | `botsaz` (API only) | `api/users.php:447` |

**No log row is written.** The source id is erased everywhere listed above. It survives in two places:

* `cancel_service.id_user` (not rewritten);
* `service_other.value` of `transfertouser` rows where the source had **received** a service.

So after an account move, a cancel request can name an id that is no longer in `user`.

### 1.3 Other writers of an owner-like column

| Row | `id_user` means | Evidence |
|---|---|---|
| `extend_user`, `extra_user`, `extra_time_user`, `change_location` | the owner acting, after `invoiceBelongsToUser` | `index.php:1792-1802,1866-1876,2121-2134,2313-2316,2666-2676` |
| `extra_not_user` | the owner, in MP, checked through an invoice selected by username | `index.php:5885-5889,5926-5930,5973-5990` |
| `extends_not_user` | **the payer.** A user types any panel username ("not in my list"); the bot looks it up on the panel and renews it, with **no** invoice ownership check | `index.php:611-640,6635-6720` |
| `extend_user_by_admin` | **the acting admin** (`admin.php:7646`), or the owner when written through the API (`api/invoice.php:308`) | |
| `gift_volume` / `gift_time` | the owner of the first invoice with that username | `cronbot/gift.php:47,73-80` |
| `cancel_service` | the requester, after `invoiceBelongsToUser` | `index.php:2705-2708,2765-2768`; the table has no time column |

Other facts:

* User rows are deleted only when the user blocked the bot **and** has no invoice and a zero balance
  (`cronbot/sendmessage.php:105-111`). An invoice whose owner is not in `user` is therefore not explained
  by normal deletion.
* Rows reach an invoice by panel username only (`function.php:1258`, `cronbot/gift.php:47`).

## 2. The model

Every live service candidate gets one record in `records/service_ownership.jsonl`, as do every invoice
referenced by `service_other` or `cancel_service` and every referenced panel username that has no invoice:

* `record_type`: `legacy_service_ownership`;
* `schema`: `mirza.service_ownership.v1`;
* `idempotency_key`: `legacy:service-ownership:<invoice key>`; for a username with no invoice,
  `…:unlinked:<keyed ref>`.

| Field | Meaning |
|---|---|
| `current_owner_candidate` | `invoice.id_user` with its user-table state. This is the **only** source an owner can come from (OWN-R1). |
| `final_owner_telegram_user_id` | Set **only** for `CONFIRMED_CURRENT_OWNER`, `CONFIRMED_TRANSFER` and `NO_CONFLICT`. It always equals `invoice.id_user`. |
| `original_owner` | The first transfer's sender (`FIRST_TRANSFER_SENDER`, OWN-R3), or `invoice.id_user` when there is no transfer record (`INVOICE_ID_USER_WITHOUT_TRANSFER_RECORD`). `null` when not proven. |
| `ownership_evidence` | `{rule, source_table, pk, role, type_raw, telegram_user_id, in_user_table, relation, reason}` per row. Telegram ids only, never usernames of people. Capped at 200 items, with the total kept. |
| `transfer_evidence` | The chain in `service_other.id` order: `{seq, pk, service_other_id, from/to ids, time, link_ok}`. Supports A→B→C and A→B→A. |
| `ownership_confidence` | `PROVEN` for the three final decisions. `PARTIAL` for an ambiguous record whose invoice owner exists. Otherwise `NONE`. |
| `ownership_decision` | One of `CONFIRMED_CURRENT_OWNER`, `CONFIRMED_TRANSFER`, `NO_CONFLICT`, `AMBIGUOUS_OWNER`, `ORPHAN_OWNER`, `INVALID_OWNER`. |
| `reason_codes`, `manual_review_reason` | The `OWNERSHIP_*` codes, in precedence order. |
| `needs_manual_review`, `review_scope` | `ADOPTION` for a live, non-test candidate; otherwise `HISTORY_ONLY`. |
| flags | `provision`, `applies_to_live_state`, `affects_wallet` and `creates_payment` are always `false`. |

### Rules

| Rule | Statement |
|---|---|
| OWN-R1 | `invoice.id_user` is the ownership field (§1.1). |
| OWN-R2 | A `transfertouser` row is one link: sender = `id_user`, target = `value`. |
| OWN-R3 | Sort the links by `service_other.id`. The chain is **proven** when all of these hold: every id is an integer from one table; sender and target are valid Telegram ids; no link is a self transfer; each target is the next sender; the last target equals `invoice.id_user`. The original owner is then the first sender. |
| OWN-R4 | An owner-claim row of a non-current holder is explained only if its `service_other.id` lies inside that holder's holding window in the proven chain. `cancel_service` rows have no time, so holder membership is enough. Rows of the current owner are never window-checked: a deferred payment row is written at order time (`function.php:1244-1253`). |
| OWN-R5 | `extend_user_by_admin` is not an owner claim. |
| OWN-R6 | `extends_not_user` is a payer, not an owner claim. |
| OWN-R7 | An unlogged account move (§1.2) is **never** inferred. |
| OWN-R8 | A `cancel_service` row is an owner claim of the requester. |
| OWN-R9 | Rows link to invoices by panel username only. When several invoices share a username, a row is attributed only on an exact owner match. On such a shared username, transfers and unmatched owner claims are not attributable. |

### Decision order

The first rule that applies decides:

1. `INVALID_OWNER` (`OWNERSHIP_OWNER_ID_INVALID`)
2. `ORPHAN_OWNER` (`OWNERSHIP_OWNER_NOT_IN_USER_TABLE`)
3. `AMBIGUOUS_OWNER` when any of these reasons is present:
   * `OWNERSHIP_MULTIPLE_CLAIMANTS`: live invoices with different owners share the username;
   * `OWNERSHIP_USERNAME_SHARED_BY_INVOICES`;
   * a chain reason: `OWNERSHIP_TRANSFER_ORDER_UNPROVEN`, `_TARGET_INVALID`, `_CHAIN_BROKEN` or
     `_NOT_APPLIED_TO_INVOICE`;
   * an unexplained row: `OWNERSHIP_ACTION_OUTSIDE_HOLDING_WINDOW`, `OWNERSHIP_ACTOR_NOT_IN_USER_TABLE` or
     `OWNERSHIP_OWNER_MISMATCH_NO_TRANSFER_RECORD`;
   * `OWNERSHIP_NO_INVOICE_FOR_USERNAME`, for a username with no invoice.
4. `CONFIRMED_TRANSFER`: a proven chain.
5. `CONFIRMED_CURRENT_OWNER`: an owner-claim row by the invoice owner corroborates it.
6. `NO_CONFLICT`: only the invoice speaks, and nothing contradicts it.

These codes are information only and never cause a review: `OWNERSHIP_TRANSFER_CHAIN_PROVEN`,
`OWNERSHIP_ADMIN_ACTOR_NOT_OWNER`, `OWNERSHIP_PAYER_NOT_OWNER`.

**A panel username is never proof of an owner.** It only links rows to an invoice, the same way Mirza
does. An owner never comes from `service_other`, `cancel_service` or a transfer target.

## 3. Reclassifying `SERVICE_OWNER_CONFLICT`

`services_ext` keeps the code `SERVICE_OWNER_CONFLICT` (review, `blocker` severity) and the warning
`SERVICE_OWNER_CONFLICTS`. Each conflict row now carries `ownership_reason`. A mismatched row is **no
longer a conflict** only in two cases:

* a proven chain explains it (OWN-R3/R4). It becomes `owner_relation = PREVIOUS_OWNER_VIA_TRANSFER`, and
  the service becomes `CONFIRMED_TRANSFER`;
* it is an `extends_not_user` payer row (OWN-R6). It becomes `PAYER_NOT_OWNER`.

The previous rule was looser: "the actor appears anywhere in a transfer of this username, and the invoice
owner received any transfer of it". It also explained broken chains and actions taken after transferring
away. Those rows are now conflicts. So `conflict_warnings_after` can be **higher** than
`conflict_warnings_before`. Nothing is dropped to make the numbers smaller.

Reconciliation `service_ownership` holds:

* `total`, `by_decision`, `by_reason`, `by_info_code`;
* `needs_manual_review`, `covered_by_core_review`, `confirmed_transfers`;
* `final_owner_set` and `final_owner_equals_invoice_owner`, which must be equal;
* `conflict_warnings_before` and `conflict_warnings_after`;
* `conflict_rows_by_reason` and `conflict_transitions` (`CONFLICT_TO_EXPLAINED`, `EXPLAINED_TO_CONFLICT`,
  `UNCHANGED_CONFLICT`).

`service_operations` and `service_requests.cancel_service` also carry `owner_conflicts_previous_rule` and
`owner_conflict_reasons`.

Review rows of kind `service_ownership` use code `OWNERSHIP_*` and `blocker` severity. They are written
only for adoption-scope records. They are **not** duplicated when the core already queued the same
finding (`ORPHAN` covers `ORPHAN_OWNER`/`INVALID_OWNER`; `AMBIGUOUS_OWNERSHIP` covers
`OWNERSHIP_MULTIPLE_CLAIMANTS`); the record says `already_in_review_queue_as`.

## 4. What stays manual and what only adoption can confirm

* **Manual (owner/operator):** every `AMBIGUOUS_OWNER`, `ORPHAN_OWNER` or `INVALID_OWNER` in adoption
  scope. In particular, a suspected **unlogged admin account move** (`OWNERSHIP_ACTOR_NOT_IN_USER_TABLE`,
  `OWNERSHIP_TRANSFER_NOT_APPLIED_TO_INVOICE`) can be confirmed only from the bot admin's knowledge or
  logs. The backup cannot prove it.
* **Adoption only (NEXA, live panel):** whether the panel account still exists, belongs to this panel, and
  is the one the invoice names. `final_owner` is the Mirza-side owner only, and
  `confirmable_only_by: NEXA_ADOPTION_LIVE_PANEL_CHECK`. Nothing here provisions, renews, deletes or
  reassigns a panel service.

## 5. Re-verifying the 863 on the real backup

The real backup (v0.1.0) reported `SERVICE_OWNER_CONFLICTS = 863` under the previous rule.

1. Convert the backup again with this version, offline, on the operator's machine.
2. In `report.json → reconciliation.service_ownership`, compare `conflict_warnings_before` with 863. It
   should be equal, unless other modules changed the staged tables.
3. Read `conflict_rows_by_reason` and `conflict_transitions`:
   * `CONFLICT_TO_EXPLAINED` should consist of `extends_not_user` payer rows;
   * `EXPLAINED_TO_CONFLICT` is the set of rows the old rule wrongly accepted.
4. Spot-check, with **no data leaving the machine**:
   * `OWNERSHIP_ACTOR_NOT_IN_USER_TABLE`: if the actor ids of these rows appear as old ids of known account
     moves, the admin should confirm OWN-R7;
   * `OWNERSHIP_OWNER_MISMATCH_NO_TRANSFER_RECORD`: check whether the deployed fork writes
     `transfertouser` at all, and whether it has its own transfer path. The deployed fork is not public.
5. Report only the counts per reason, never ids.

Tests: `tests/test_ownership.py` (exact decisions per scenario, wrong-account guard, determinism, flags,
coverage) and `tests/test_services_ext.py`.

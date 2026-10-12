# Mirza source tables — catalogue with evidence

Machine-readable twin: [`docs/evidence/mirza_tables.json`](evidence/mirza_tables.json) (generated from the same data; one object per table).

**Status: research only.** Nothing here was read from real customer data. Sources:

- `botmirzapanel@92c0ed06` — public MirzaBot (`table.php` DDL + PHP code), path `/home/user/ref/botmirzapanel`.
- `mirza_pro@b645db74` — public fork mirza_pro (`db/tables/*.php`, `db/migrations/*.php`, PHP code), path `/home/user/ref/mirza_pro`.
- `nexa-bot@9e604de` — NEXA reference repo (docs, contracts, drizzle migrations). Read-only.
- `operator-report 2026-10-10` — table names, row counts and two column lists supplied by the operator from the real deployed backup. **The deployed fork is not any public revision** (it has columns no public revision declares), so every public-source fact below is evidence about *a* Mirza revision, not proof about the deployed one.

Rules used: every fact cites `file:line`; anything not proven is written **UNKNOWN**. Sensitivity classes: `public`, `personal` (PII, chat text, Telegram ids, usernames), `financial`, `secret` (anything that can authenticate or move money: passwords, tokens, API keys, merchant ids, panel credentials, card numbers, subscription URLs/UUIDs, bearer gift/discount codes, invite links). Migration classes: OPERATIONAL (feeds a live NEXA entity), ARCHIVE (read-only history), REVIEW (operator decision), CONFIGURATION (non-secret settings only, proposals), EXCLUDED (secret/temporary/no value). The `nexa_inventory_class_proposal` field maps these onto NEXA's own catalogue classes (`packages/contracts/src/legacy-inventory.ts`: SUPPORTED / ARCHIVE / SECRETS_MANUAL / OWNER_DECISION / UNCLASSIFIED).

Time zone: both public sources call `date_default_timezone_set('Asia/Tehran')` (mirza_pro@b645db74 index.php:3; botmirzapanel@92c0ed06 index.php:13); string timestamps carry no offset and are Gregorian (`date()`), not Jalali; Jalali (`jdate`) is used only for display text (e.g. mirza_pro index.php:3221).

Tables catalogued: **48** (public DDL: 30; operator list only, no public DDL: 18). The operator reports 45 tables outside `user`/`invoice`/`product` (48 total); 40 of them are named. Public tables NOT in the operator list: `Discount`, `Giftcodeconsumed`, `DiscountSell`, `app`, `botsaz` — whether they are among the 5 unnamed tables is UNKNOWN.

## 1. Summary

| Table | Sources | Rows (operator) | Class | NEXA inventory class | Sensitivity present | NEXA target |
|---|---|---|---|---|---|---|
| `user` | BMP+PRO | ~197952 | OPERATIONAL | SUPPORTED | secret, financial, personal, public | customers + legacy_import_map |
| `help` | BMP+PRO+OP | — | CONFIGURATION | OWNER_DECISION | public | NEXA delivery_tutorials (nexa-bot@9e604de apps/api/drizzle/0211_delivery_tutorials.sql:1) |
| `setting` | BMP+PRO+OP | — | CONFIGURATION | SECRETS_MANUAL | secret, financial, personal, public | NEXA setting_values (nexa-bot@9e604de apps/api/drizzle/0010_control_plane.sql:87) |
| `admin` | BMP+PRO+OP | — | EXCLUDED | SECRETS_MANUAL | secret, personal, public | NEXA admins (nexa-bot@9e604de apps/api/drizzle/0005_identity_and_rbac.sql:47) |
| `channels` | BMP+PRO+OP | — | CONFIGURATION | SECRETS_MANUAL | secret, public | NEXA setting telegram.channels (nexa-bot@9e604de docs/package-b-channel-membership-audit.md:9-13) |
| `marzban_panel` | BMP+PRO+OP | 5 | REVIEW | SECRETS_MANUAL | secret, financial, personal, public | NEXA panels + panel_credentials (nexa-bot@9e604de apps/api/drizzle/0017_panels_providers_credentials_health.sq |
| `product` | BMP+PRO | 64 | OPERATIONAL | SUPPORTED | secret, financial, personal, public | legacy_product_reviews |
| `invoice` | BMP+PRO | 128703 | OPERATIONAL | SUPPORTED | secret, financial, personal, public | legacy_service_candidates, legacy_invoice_archive, legacy_product_shapes |
| `Payment_report` | BMP+PRO+OP | 215082 | ARCHIVE | SECRETS_MANUAL | secret, financial, personal, public | new archive table proposal (legacy_payment_archive) |
| `Discount` | BMP+PRO | — | REVIEW | SECRETS_MANUAL | secret, financial, public | no direct NEXA equivalent (NEXA discounts are order discounts: nexa-bot@9e604de apps/api/drizzle/0032_phase4_c |
| `Giftcodeconsumed` | BMP+PRO | — | ARCHIVE | SECRETS_MANUAL | secret, personal, public | new archive table proposal (legacy_code_redemptions) |
| `textbot` | BMP+OP | — | CONFIGURATION | OWNER_DECISION | public | NEXA bot texts |
| `PaySetting` | BMP+PRO+OP | — | EXCLUDED | SECRETS_MANUAL | secret, public | NEXA payment_gateways / payment_gateway_credentials / payment_accounts (nexa-bot@9e604de apps/api/drizzle/0070 |
| `DiscountSell` | BMP+PRO | — | REVIEW | SECRETS_MANUAL | secret, public | NEXA discounts / discount_redemptions (nexa-bot@9e604de apps/api/drizzle/0032_phase4_commerce.sql:22,35) |
| `affiliates` | BMP+PRO+OP | — | CONFIGURATION | OWNER_DECISION | financial, public | NEXA referral settings / referral_signup_gifts (nexa-bot@9e604de apps/api/drizzle/0119_customer_ux_completion. |
| `cancel_service` | BMP+PRO+OP | — | ARCHIVE | ARCHIVE | personal, public | archive |
| `category` | BMP+PRO+OP | 7 | OPERATIONAL | SUPPORTED | public | NEXA product_categories (nexa-bot@9e604de apps/api/drizzle/0097_product_categories.sql:1) |
| `Requestagent` | PRO+OP | — | ARCHIVE | ARCHIVE | personal, public | NEXA reseller_tier_grants (nexa-bot@9e604de apps/api/drizzle/0110_wp9_resellers.sql:29) |
| `app` | PRO | — | CONFIGURATION | OWNER_DECISION | public | NEXA tutorials/settings |
| `botsaz` | PRO | — | EXCLUDED | SECRETS_MANUAL | secret, personal, public | NEXA bot_instances (nexa-bot@9e604de apps/api/drizzle/0000_foundation.sql:31) |
| `card_number` | PRO+OP | — | EXCLUDED | SECRETS_MANUAL | secret, personal | NEXA payment_accounts/payment_destinations (nexa-bot@9e604de apps/api/drizzle/0062_payment_accounts.sql:1,24) |
| `departman` | PRO+OP | — | CONFIGURATION | OWNER_DECISION | personal, public | NEXA ticket_categories (nexa-bot@9e604de apps/api/drizzle/0135_wp_a7_support_tickets.sql:16) |
| `logs_api` | PRO+OP | — | EXCLUDED | SECRETS_MANUAL | secret, personal, public | none |
| `manualsell` | PRO+OP | — | EXCLUDED | SECRETS_MANUAL | secret, personal, public | none (manual) |
| `reagent_report` | PRO+OP | — | ARCHIVE | ARCHIVE | personal, public | NEXA referrals (nexa-bot@9e604de apps/api/drizzle/0032_phase4_commerce.sql:185) |
| `service_other` | PRO+OP | 78645 | ARCHIVE | SECRETS_MANUAL | secret, financial, personal, public | new archive table proposal (legacy_service_operation_archive) |
| `shopSetting` | PRO+OP | — | CONFIGURATION | OWNER_DECISION | public | NEXA cashback_rules / custom_service_price_rules (nexa-bot@9e604de apps/api/drizzle/0105_wp8_pricing_discounts |
| `support_message` | PRO+OP | — | ARCHIVE | ARCHIVE | personal, public | NEXA tickets/ticket_messages (nexa-bot@9e604de apps/api/drizzle/0135_wp_a7_support_tickets.sql:35,79) |
| `topicid` | PRO+OP | — | EXCLUDED | OWNER_DECISION | public | NEXA ops_log_topics (nexa-bot@9e604de apps/api/drizzle/0136_wpa4_ops_log_group.sql:40) |
| `wheel_list` | PRO+OP | — | ARCHIVE | ARCHIVE | financial, personal, public | new archive table proposal (wallet credit evidence) |
| `discount_sell` | OP | — | REVIEW | UNCLASSIFIED | UNKNOWN | UNKNOWN until columns are known |
| `discount_sell_used` | OP | — | REVIEW | UNCLASSIFIED | UNKNOWN | UNKNOWN until columns are known |
| `ad_campaign` | OP | — | REVIEW | UNCLASSIFIED | UNKNOWN | possible analogue NEXA campaigns (nexa-bot@9e604de apps/api/drizzle/0147_round_n_campaigns.sql:27) |
| `ticket` | OP | — | REVIEW | UNCLASSIFIED | UNKNOWN | possible analogue NEXA tickets (nexa-bot@9e604de apps/api/drizzle/0135_wp_a7_support_tickets.sql:79) |
| `ticket_message` | OP | — | REVIEW | UNCLASSIFIED | UNKNOWN | possible analogue NEXA ticket_messages (nexa-bot@9e604de apps/api/drizzle/0135_wp_a7_support_tickets.sql:35) |
| `help_category` | OP | — | REVIEW | UNCLASSIFIED | UNKNOWN | UNKNOWN until columns are known |
| `vs_category` | OP | — | REVIEW | UNCLASSIFIED | UNKNOWN | UNKNOWN until columns are known |
| `vs_package` | OP | — | REVIEW | UNCLASSIFIED | UNKNOWN | UNKNOWN until columns are known |
| `vs_order` | OP | — | REVIEW | UNCLASSIFIED | UNKNOWN | UNKNOWN until columns are known |
| `vs_stock` | OP | — | REVIEW | UNCLASSIFIED | UNKNOWN | UNKNOWN until columns are known |
| `vs_setting` | OP | — | REVIEW | UNCLASSIFIED | UNKNOWN | UNKNOWN until columns are known |
| `wallet_transaction` | OP | 17691 | REVIEW | OWNER_DECISION | financial, personal, public | new archive table proposal (legacy_wallet_history) |
| `agent_manage` | OP | — | REVIEW | UNCLASSIFIED | UNKNOWN | possible analogue NEXA reseller_tiers (nexa-bot@9e604de apps/api/drizzle/0110_wp9_resellers.sql:40) |
| `agent_invoice` | OP | — | REVIEW | UNCLASSIFIED | UNKNOWN | UNKNOWN until columns are known |
| `agent_log` | OP | — | REVIEW | UNCLASSIFIED | UNKNOWN | UNKNOWN until columns are known |
| `agent_usage_snapshot` | OP | — | REVIEW | UNCLASSIFIED | UNKNOWN | UNKNOWN until columns are known |
| `custom_service_tier_prices` | OP | — | REVIEW | UNCLASSIFIED | UNKNOWN | possible analogue NEXA custom_service_price_rules (nexa-bot@9e604de apps/api/drizzle/0129_package_d_custom_ser |
| `requestagent` | OP | — | REVIEW | UNCLASSIFIED | UNKNOWN | UNKNOWN until columns are known |

BMP = botmirzapanel@92c0ed06, PRO = mirza_pro@b645db74, OP = operator report 2026-10-10.

## 2. Answers to the deep questions

### Q1. What `service_other` holds and how it relates to `invoice`

- It is a **service-operation log keyed by the panel account username** (`service_other.username` = `invoice.username`; `mirza_pro index.php:928`, `function.php:1244-1250`, index `db/indexes.php:16`). It has no `id_invoice` column (`mirza_pro db/tables/service_other.php:5-13`). It does not exist in botmirzapanel@92c0ed06.
- `type` vocabulary, with the admin label map at `mirza_pro admin.php:4365-4377`:
  - `extend_user`: renewal (`index.php:1802, 1876`).
  - `extend_user_by_admin`: admin renewal (`admin.php:7645`, `api/invoice.php:311`).
  - `extra_user`: extra volume (`index.php:2124`, `function.php:1380`).
  - `extra_time_user`: extra time (`index.php:2669`, `function.php:1452`).
  - `extra_not_user` (`index.php:5982`; it has no label in the map).
  - `extends_not_user`: "renewal due to user not being in the list" (`index.php:6717`, `lang/en.php:2244`).
  - `change_location` (`index.php:2306-2320`).
  - `transfertouser`: ownership transfer, and `value` is the new owner's id (`index.php:2896-2899`).
  - `gift_volume` and `gift_time`: cron gifts with price 0 (`cronbot/gift.php:72-76, 121-125`).
- **Money.** `price` is the amount charged. For wallet-paid operations it is the product or add-on price (`index.php:1879`). For direct-pay extra volume and extra time it is `Payment_report.price` (`function.php:1402`). That is only the shortfall above the wallet balance (`index.php:2077-2078`), so the meaning of `price` is not uniform.
- **Payment linkage.** There is no foreign-key column. A direct-pay renewal first inserts `extend_user` with `status='unpaid'` and `value.id_order=<random>` (`index.php:1792-1806`). The payment's `id_invoice` tag is `getextenduser|<username>%<id_order>`. On payment, `DirectPayment` finds the row with `value LIKE %id_order%` and sets `status='paid'` (`function.php:1244-1250, 1299-1300`). Other types store no status: NULL is counted as paid by the reports (`api/users.php:111`, `admin.php:4360`).
- **Hazards.**
  - In `extend_user_by_admin` from the Telegram admin flow, `id_user` is the **admin's** id (`admin.php:7643-7650`).
  - `output` holds raw panel API responses, so treat it as secret.
  - `time` is a `Y/m/d H:i:s` string, but `admin.php:411` compares it with `NOW()`.
- **Recommendation:** ARCHIVE (78,645 rows, operator). Renewals and add-ons are history, and NEXA must not replay them onto adopted services. Rows with `status='unpaid'` are abandoned attempts.

### Q2. `Payment_report` lifecycle, top-up vs direct purchase, and column meanings

- **Status lifecycle** (mirza_pro):
  1. A gateway link or card request creates `Unpaid` (`index.php:4691-4695` and the other inserts listed in the table section).
  2. A card or offline-crypto receipt upload sets `waiting`, with `at_updated` = the upload time and `dec_not_confirmed` = the user's text and caption (`index.php:5618-5621`). Botmirzapanel inserts card payments directly as `waiting` (`botmirzapanel index.php:2264`).
  3. The row then ends in one of these states:
     - `paid`, through gateway verification `claimPaymentPaid` (`function.php:988-996`, called from `payment/*.php`), admin confirmation (`admin.php:1730`), or the auto-confirm cron (`cronbot/croncard.php:43`).
     - `reject`, with the reason in `dec_not_confirmed` (`admin.php:1792, 1799`), or as a bulk reject with `remove_all` (`admin.php:7438`).
     - `expire`, when an Unpaid row is older than 24h (`cronbot/payment_expire.php:13-21`) or a Plisio payment expires (`cronbot/plisio.php:46`).
  4. Admins can **delete** all `expire` rows (`admin.php:5361`).
  - Admin manual adjustments are inserted directly as `paid`, with method `add balance by admin` or `low balance by admin` and `id_invoice=NULL` (`admin.php:3565-3569, 3611-3615`). **The debit is stored as a positive price**, and only the method implies the sign (`admin.php:3618`).
- **Top-up vs purchase.** On `paid`, `DirectPayment()` splits the column `id_invoice` (mirza_pro) or `invoice` (botmirzapanel) on `|` (`function.php:1011`, `botmirzapanel functions.php:227`). The tag is set from `user.Processing_value_tow|Processing_value_one` (`index.php:4689`):
  - `getconfigafterpay|<panel username>` is a direct purchase (`index.php:3944-3945`; `function.php:1015-1239`). The invoice is claimed `unpaid→active` (`function.php:1051`). The wallet is then reduced by the invoice price, clamped at 0. The payment covered only the shortfall `price − Balance` (`index.php:3925-3927`).
  - `getextenduser|<username>%<id_order>` is a direct-pay renewal (`function.php:1240`).
  - `getextravolumeuser|<username>%<GB>` is direct-pay extra volume (`function.php:1366`).
  - `getextratimeuser|<username>%<days>` is direct-pay extra time (`function.php:1438`). In all three, the wallet balance is set to 0 (`function.php:1371-1372, 1443-1444`).
  - Anything else is a **wallet top-up**: `Balance += price` (`function.php:1509-1512`). Botmirzapanel writes `0|0` for top-ups (`index.php:2046`), and the sub-bot writes `0 | 0` (`vpnbot/Default/index.php:1295`).
- **`id_order`** is a payment-local random id, `bin2hex(random_bytes(5))` (`index.php:4691`). It is used as the gateway order id and description (`function.php:2590-2593`) and in callback data such as `Confirm_pay_<id_order>` (`admin.php:1686`). **It is not `invoice.id_invoice`.**
- **`dec_not_confirmed`** is polymorphic. It can hold:
  - gateway references: Zarinpal authority (`index.php:4833`), Variza slug (`payment/variza_webhook.php:78-79`), or the Rial-gateway id (`index.php:5098, 5298`);
  - gateway JSON (`cronbot/iranpay1.php:29`; Telegram Stars `pre_checkout_query`/`successful_payment`, `index.php:6552-6562`);
  - the customer's receipt text (`index.php:5619`);
  - the admin's rejection reason (`admin.php:1799`);
  - the marker `remove_all` (`admin.php:7438`);
  - the auto-confirm label (`croncard.php:43`).
- **`message_id`** is the Telegram message id of the payment-link message sent to the user (`function.php:654`). The expiry cron deletes that message (`payment_expire.php:19`).
- **`at_updated`** is the receipt-upload time (`index.php:5620-5621`). The auto-confirm delay is measured from it (`croncard.php:31-38`).
- **`receipt_trace`** appears in **no public source**, so its meaning is UNKNOWN.
- **Deployed-fork gap.** The operator's column list has **no `invoice`/`id_invoice`/`bottype`**. In the deployed fork, top-ups cannot be told apart from direct purchases using `Payment_report` alone. This is a blocking question for any financial reconciliation.
- **Unit.** `price` is the amount in the bot's currency. Zarinpal is called with `currency: IRT` and `amount = price` (`index.php:4812`; `function.php:2588-2589`). Customer copy says "تومان" (`lang/fa.php:282`). For the card-to-card copy button the amount gets an extra `0` appended, so it is converted to Rial for the bank transfer (`index.php:4686`). Plisio and Stars convert the amount to USD and stars at request time (`index.php:4925-4945, 5385-5401`).
- **Vocabulary.** For `payment_Status` and `Payment_Method`, see the table section. Botmirzapanel spellings `Nowpayments` and `Currency Rial gateway` differ from the mirza_pro spellings `nowpayment` and `Currency Rial 1/2/3`.

### Q3. Does `wallet_transaction` exist in a public source?

**No.** Searching both public trees for `wallet_transaction` returns 0 hits, and NEXA mentions it only to say it is never replayed (`nexa-bot docs/migration-opening-balance.md:19`; `docs/legacy-migration/reconciliation.md:84-85`). Its columns are known only from the operator report.

In the public sources, wallet history is scattered. `user.Balance` is updated in place by:

- `deductBalance` and `addBalance` (`function.php:968-987`);
- `Payment_report` rows: top-ups and admin adjustments only;
- `service_other.price`: renewals and add-ons;
- `invoice.price_product`: purchases;
- `wheel_list.price`: wheel prizes (`index.php:6253, 6269`);
- `Giftcodeconsumed` with `Discount.price`: gift codes, which store no amount or time per redemption (`index.php:5777-5786`);
- `reagent_report`: join gifts, with no amount stored (`index.php:5859-5872`).

Some credits are recorded **only in Telegram report messages**, with no table at all:

- referral commission (`function.php:1138-1188`);
- gateway and card cashback (`admin.php:1745-1750`, `croncard.php:49-55`);
- renewal cashback after a direct-pay renewal (`function.php:1329-1338`; on the wallet path it is a price reduction instead, `index.php:1816-1819`);
- cancel-request refunds (`admin.php:4741-4745`);
- refunds when service creation or renewal fails (`function.php:1065-1066`; `index.php:2106-2107`, which runs `addBalance` on failure).

Consequence: **no public-schema table can reconcile `user.Balance`.** `wallet_transaction` (deployed fork, 17,691 rows) may be the fork's answer, but its semantics are unproven.

### Q4. Agent / reseller tiers, agent tables, custom prices

- **Tiers.** `user.agent` takes `f` (default normal customer; `db/tables/user.php:51`), `n` or `n2` (labels "normal agent" and "advanced agent", `admin.php:5138-5141`).
  - `n2` may spend below zero, down to `-user.maxbuyagent` (`function.php:974`).
  - `user.expire` is an agent expiry as a unix timestamp (`admin.php:8932-8933`).
  - Agent requests are stored in `Requestagent` (statuses `waiting`/`accept`/`reject`; type `None`→`n`/`n2`; `index.php:6112-6125`, `admin.php:5094, 5142-5143`). The request fee `setting.agentreqprice` is deducted from the wallet (`index.php:6105`).
- **Per-tier pricing.** Prices are kept as JSON keyed by tier:
  - `marzban_panel.priceextravolume`, `pricecustomvolume`, `pricecustomtime` and `priceextratime`, as `{f,n,n2}` (`db/tables/marzban_panel.php:4, 77-80`; used at `index.php:5979-5982`);
  - `PaySetting` `minbalance`/`maxbalance` (`db/migrations/005_paysetting_defaults.php:14-15`);
  - `shopSetting` `chashbackextend_agent` `{n,n2}` (`db/tables/shopSetting.php:3, 22`);
  - `product.agent` scopes a product to one tier (`index.php:3598`);
  - `DiscountSell.agent` takes `f`/`n`/`n2`/`allusers` (`index.php:4169`).
- **Tables in the operator list only.** `agent_manage`, `agent_invoice`, `agent_log`, `agent_usage_snapshot` and `custom_service_tier_prices` appear in **no public source**. Their columns and meaning are UNKNOWN, so they are classed REVIEW and fail closed. `agent_invoice` and `agent_usage_snapshot` may carry reseller money or settlement (`setting.Debtsettlement`, `db/tables/setting.php:56`), but that is unproven.
- **NEXA concepts:** `reseller_tiers` and `reseller_tier_grants` (`nexa-bot apps/api/drizzle/0110_wp9_resellers.sql:29,40`), and `custom_service_price_rules` (`0129_package_d_custom_service.sql:21`). The tier meaning was verified in NEXA research (`docs/NEXA_EVIDENCE.md:231`).

### Q5. `Discount`, `DiscountSell` and `Giftcodeconsumed`

- **`Discount` holds gift codes, not discounts.** Redeeming a code credits `price` Toman to the wallet (`index.php:5777`, `lang/fa.php:461`). It increments `limitused` (`index.php:5779`) and logs a `Giftcodeconsumed` row (`index.php:5783-5786`). An unredeemed code is a bearer liability, so its class is SECRET + REVIEW.
- **`DiscountSell` holds percent purchase discounts** (`price/100 * price_product`, `index.php:4227`). They are scoped by:
  - `type`: `all`, `buy` or `extend` (`index.php:1643, 4169`);
  - `agent` tier, `code_product` (`all` for any product) and `code_panel` (`/all` for any panel);
  - `usefirst='1'`: first purchase only (`index.php:4199`);
  - `useuser`: a per-user limit;
  - `limitDiscount` against `usedDiscount`;
  - `time`: a unix expiry, where `0` means no expiry (`index.php:4185`).
- **`Giftcodeconsumed`** is the shared usage log for both kinds of code (`index.php:3960, 5783`; `function.php:1118, 1306`). It has no amount and no time, and `code` alone does not say which table the code came from.
- **Deployed fork.** The operator lists `discount_sell` and `discount_sell_used` but **not** `DiscountSell`, `Discount` or `Giftcodeconsumed`. Whether these are renamed equivalents is UNKNOWN.

### Q6. `affiliates` and `reagent_report` (referral)

- `affiliates` is a **single configuration row** (`db/tables/affiliates.php:12-20`) with these fields:
  - `status_commission`: `oncommission` enables commission;
  - `porsant_one_buy`: `on_buy_porsant` pays commission only on the first purchase (`function.php:1138-1141`);
  - `price_Discount`: the join-gift amount, split half to the referrer and half to the new user (`index.php:5866-5872`).
- The commission percentage is `setting.affiliatespercentage` (`function.php:1141`).
- The referrer is stored in `user.affiliates`, with a counter in `user.affiliatescount` (`index.php:229-233`).
- `reagent_report` has one row per referred user: `user_id` (UNIQUE), `reagent` (the referrer), `get_gift` (0→1 when claimed) and `time` as `Y/m/d H:i:s` (`index.php:235-238, 5859`).
- Commissions are credited straight to the referrer's `Balance` and leave no table row, only a Telegram report (`function.php:1150, 1175`).
- **NEXA:** `referrals`, `referral_codes`, `referral_signup_gifts` and `order_referral_commissions` exist. NEXA Phase 1 does **not** import referrals (`docs/legacy-migration/sql-evidence.md:21, 304`). The recommended class here is ARCHIVE.

### Q7. `support_message`, `departman` and `ticket*`

- **`support_message`** stores one row per message, not per thread. Each customer message gets a fresh random 8-hex `Tracking` (`index.php:3222, 3288`).
  - Columns: `iduser` (the customer), `idsupport` (the admin Telegram id taken from `departman`), `name_departman` (stored as text), `text` (the customer's message), `result` (the admin's answer) and `time` (`Y/m/d H:i:s`).
  - Status is a MySQL ENUM: `Answered`, `Pending`, `Unseen`, `Customerresponse` or `close` (`db/tables/support_message.php:13`). Only `Unseen` (`index.php:3224`), `Answered` (`3264`) and `Customerresponse` (`3290`) are written by public code.
- **`departman`** maps a department to the admin who handles it. Migration 007 deduplicates it, and a unique index covers `(idsupport, name_departman)` (`db/indexes.php:19`).
- **`ticket` and `ticket_message`** (operator list only) are in **no public source**, so their meaning is UNKNOWN. NEXA research also observed that the live bot's support button is an external URL (`docs/research/telegrambotknowledge4/telegram-knowledge/business-rules.md:120`).
- **NEXA concepts:** `tickets`, `ticket_messages` and `ticket_categories` (`0135_wp_a7_support_tickets.sql`). The recommendation is ARCHIVE for history. Live ticket import is an owner decision.

### Q8. Which settings tables and columns hold secrets

- **`PaySetting`** (key/value): `cardnumber`, `namecard`, `CartDirect`, `apinowpayment`, `merchant_id_aqayepardakht`, `marchent_tronseller`, `walletaddress`, `apiternado`, `merchant_zarinpal`, `apiiranpay`, `marchent_floypay`, `apiiranpay4`, `endpointiranpay4`, `variza_api_token` and `variza_webhook_secret` (`db/tables/PaySetting.php:9-88`). Botmirzapanel also has `CartDescription`, which holds a card number (`botmirzapanel table.php:518`). Any unknown key must be treated as secret.
- **`setting`:** `webhook_secret` (`db/tables/setting.php:66`). `Channel_Report` and `id_support` are personal.
- **`marzban_panel`:** `url_panel`, `username_panel`, `password_panel`, `secret_code`, `datelogin` (a cached login), `linksubx`, `inbounds` and `proxies`. The code itself lists `password`, `token`, `password_panel`, `secret_code` and `datelogin` as sensitive (`function.php:441`).
- **`admin`:** `username` and `password` (a bcrypt hash; `db/tables/admin.php:15`, `db/migrations/012`).
- **`card_number`:** `cardnumber` (with the holder's name in `namecard`).
- **`botsaz`:** `bot_token`, `webhook_secret`, and `setting` (JSON that includes card text).
- **`app`:** none (public links).
- **`shopSetting`:** no secrets in the declared keys (`db/tables/shopSetting.php:5-23`).
- **`channels`:** `linkjoin`, an invite link that grants access.
- **`topicid`:** none (forum topic ids).
- **Secrets in non-settings tables:**
  - `user.token` (mini-app bearer token, `api/miniapp.php:291`);
  - `user.bottype`, `invoice.bottype` and `Payment_report.bottype` (the **sub-bot API token**, `vpnbot/Default/index.php:27, 66, 1296`);
  - `invoice.user_info` (the subscription URL, `index.php:3053`) and `invoice.uuid`;
  - `service_other.output` (raw panel responses);
  - `manualsell.contentrecord` (the sold config);
  - `Discount.code`, `DiscountSell.codeDiscount` and `Giftcodeconsumed.code` (bearer codes);
  - `logs_api.header` and `logs_api.data`.
- NEXA policy is that secrets are never imported (`nexa-bot docs/legacy-migration/table-inventory.md:116-118`).

### Top risks for the converter

1. **The deployed fork differs from public code.** Its `Payment_report` has no purpose column, and it has `receipt_trace`. It also has unknown tables: `vs_*`, `agent_*`, `wallet_transaction`, `ticket*`, `discount_sell*`, `ad_campaign` and `custom_service_tier_prices`. Public vocabularies must be re-validated against the dump's DISTINCT values before any mapping.
2. **The ledger cannot be reconciled from the public schema.** Many wallet credits (cashback, commission, gift codes, refunds, wheel prizes, join gifts) write no row, and admin debits are stored with a positive amount. Any balance-history reconciliation must stay a blocking REVIEW until `wallet_transaction` semantics are proven.
3. **`price` has mixed semantics.** For direct payments, `Payment_report.price` and `service_other.price` are the shortfall, not the full price. Admin manual credits can differ from the `Payment_report` price (`admin.php:4021-4049`).
4. **History is incomplete.** `expire` payment rows can be deleted (`admin.php:5361`), unpaid invoices are purged by a cron (`cronbot/optimize_order.php:11`), and there is no refund amount column.
5. **Bot tokens hide in ordinary-looking columns** (`bottype` in three tables, `user.token`). A column allowlist is mandatory.
6. **Identity anomalies.** `service_other.id_user` can hold an admin id. The tables `Requestagent` and `requestagent` coexist (case-sensitive names). Invoice status spellings `removebyadmin` and `removedbyadmin` both exist.

## 3. Per-table evidence

### `user`

Found in: `botmirzapanel@92c0ed06 table.php:14`; `mirza_pro@b645db74 db/tables/user.php:4`
Rows (operator report): ~197952
Primary key: `id`; unique: `ref_code`

**Purpose.** One row per Telegram user of the bot (customer, reseller, admin). Holds wallet balance, tier, conversation state machine and referral pointer.

**Class:** OPERATIONAL (NEXA: SUPPORTED; read only through an explicit column allowlist that omits the secret columns) — Already the core of the NEXA v1 read set (customers, opening balances, trial decisions); state-machine and secret columns are dropped.

**NEXA target:** customers + legacy_import_map; wallet_entries (MIGRATION_OPENING_BALANCE); legacy_wallet_debts (negative); legacy_trial_eligibility — see docs/MAPPING.md

| Column | Declared type(s) | Sensitivity | Notes |
|---|---|---|---|
| `id` | botmirzapanel: varchar(500)  PRIMARY KEY \| mirza_pro: VARCHAR(500) PRIMARY KEY | personal | Telegram user id; PK |
| `ref_code` | botmirzapanel: CHAR(32) NOT NULL UNIQUE \| botmirzapanel: CHAR(32) (added; default '') | secret | BMP only: random 32-hex referral code (bearer); dropped by mirza_pro migration 006 |
| `limit_usertest` | botmirzapanel: int(100) NOT NULL \| mirza_pro: INT(100) NOT NULL | public | remaining trial count |
| `roll_Status` | botmirzapanel: bool NOT NULL \| botmirzapanel: bool (added; default 'false') \| mirza_pro: BOOL NOT NULL | public | accepted rules flag |
| `Processing_value` | botmirzapanel: TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL \| botmirzapanel: TEXT (added; default '0') \| mirza_pro: TEXT CHARACTER SET utf8mb4 COLL | personal | conversation scratch value (amounts, ids, JSON); transient |
| `Processing_value_one` | botmirzapanel: varchar(1000) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL \| botmirzapanel: VARCHAR(1000) (added; default '0') \| mirza_pro: TEXT CHARACTE | personal | scratch |
| `Processing_value_tow` | botmirzapanel: varchar(1000) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL \| botmirzapanel: VARCHAR(1000) (added; default '0') \| mirza_pro: TEXT CHARACTE | personal | scratch; payment purpose tag e.g. getconfigafterpay |
| `Processing_value_four` | botmirzapanel: varchar(1000) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL \| botmirzapanel: VARCHAR(100) (added; default '0') \| mirza_pro: TEXT CHARACTER | personal | scratch; discount marker dis_<code> |
| `step` | botmirzapanel: varchar(1000) NOT NULL \| mirza_pro: VARCHAR(500) NOT NULL | public | state machine step |
| `description_blocking` | botmirzapanel: TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NULL \| mirza_pro: TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL | personal | admin free text |
| `number` | botmirzapanel: varchar(2000) NOT null \| mirza_pro: VARCHAR(300) NOT NULL \| mirza_pro: VARCHAR(500) (added on upgrade; Schema default type; default 'none') | personal | phone number |
| `Balance` | botmirzapanel: int(255) NOT null \| mirza_pro: INT(255) NOT NULL | financial | wallet balance, signed int |
| `User_Status` | botmirzapanel: varchar(500) NOT NULL \| mirza_pro: VARCHAR(500) NOT NULL | public | Active/block |
| `pagenumber` | botmirzapanel: int(10) NOT NULL \| mirza_pro: INT(10) NOT NULL \| mirza_pro: VARCHAR(500) (added on upgrade; Schema default type; default '') | public | UI paging |
| `message_count` | botmirzapanel: varchar(100) NOT NULL \| botmirzapanel: VARCHAR(100) (added; default '0') \| mirza_pro: VARCHAR(100) NOT NULL \| mirza_pro: VARCHAR(500) (added o | public | anti-spam counter |
| `last_message_time` | botmirzapanel: varchar(100) NOT NULL \| botmirzapanel: VARCHAR(100) (added; default '0') \| mirza_pro: VARCHAR(100) NOT NULL \| mirza_pro: VARCHAR(500) (added o | public | anti-spam unix ts |
| `affiliatescount` | botmirzapanel: varchar(100) NOT NULL \| botmirzapanel: VARCHAR(100) (added; default '0') \| mirza_pro: VARCHAR(100) NOT NULL \| mirza_pro: VARCHAR(500) (added o | public | number of referred users |
| `affiliates` | botmirzapanel: varchar(100) NOT NULL \| botmirzapanel: VARCHAR(100) (added; default '0') \| mirza_pro: VARCHAR(100) NOT NULL \| mirza_pro: VARCHAR(500) (added o | personal | referrer user id |
| `verify` | botmirzapanel: varchar(50) NOT NULL \| botmirzapanel: VARCHAR(50) (added; default '0') \| mirza_pro: VARCHAR(100) NOT NULL \| mirza_pro: VARCHAR(500) (added on  | public | verification flag |
| `username` | botmirzapanel: varchar(1000) NOT NULL \| botmirzapanel: VARCHAR(1000) (added; default 'none') \| mirza_pro: VARCHAR(500) NOT NULL \| mirza_pro: VARCHAR(500) (ad | personal | Telegram @username |
| `agent` | mirza_pro: VARCHAR(100) NOT NULL \| mirza_pro: VARCHAR(500) (added on upgrade; Schema default type; default 'f') | public | tier f/n/n2 |
| `namecustom` | mirza_pro: VARCHAR(300) NOT NULL \| mirza_pro: VARCHAR(500) (added on upgrade; Schema default type; default 'none') | personal | custom service-name prefix |
| `number_username` | mirza_pro: VARCHAR(300) NOT NULL \| mirza_pro: VARCHAR(500) (added on upgrade; Schema default type; default '100') | public | counter for generated panel usernames |
| `register` | mirza_pro: VARCHAR(100) NOT NULL \| mirza_pro: VARCHAR(500) (added on upgrade; Schema default type; default 'none') | personal | registration unix ts |
| `cardpayment` | mirza_pro: VARCHAR(100) NOT NULL \| mirza_pro: VARCHAR(500) (added on upgrade; Schema default type; default '1') | public | card-to-card allowed flag |
| `codeInvitation` | mirza_pro: VARCHAR(100) NULL \| mirza_pro: VARCHAR(500) (added on upgrade; Schema default type; default null) | secret | invitation code (bearer) |
| `pricediscount` | mirza_pro: VARCHAR(100) NULL DEFAULT '0' \| mirza_pro: VARCHAR(500) (added on upgrade; Schema default type; default '0') | public | per-user discount percent |
| `hide_mini_app_instruction` | mirza_pro: VARCHAR(20) NULL DEFAULT '0' \| mirza_pro: VARCHAR(20) (added on upgrade; default '0') | public | UI flag |
| `maxbuyagent` | mirza_pro: VARCHAR(100) NULL DEFAULT '0' \| mirza_pro: VARCHAR(500) (added on upgrade; Schema default type; default '0') | financial | n2 credit limit |
| `joinchannel` | mirza_pro: VARCHAR(100) NULL DEFAULT '0' \| mirza_pro: VARCHAR(100) (added on upgrade; default '0') | public | channel join flag |
| `checkstatus` | mirza_pro: VARCHAR(50) NULL DEFAULT '0' \| mirza_pro: VARCHAR(50) (added on upgrade; default '0') | public | flag |
| `bottype` | mirza_pro: TEXT NULL \| mirza_pro: TEXT (added on upgrade; default '0') | secret | sub-bot API token the user registered through (vpnbot) |
| `score` | mirza_pro: INT(255) NULL DEFAULT '0' \| mirza_pro: INT(255) (added on upgrade; default '0') | public | loyalty points |
| `limitchangeloc` | mirza_pro: VARCHAR(50) NULL DEFAULT '0' \| mirza_pro: VARCHAR(50) (added on upgrade; default '0') | public | location-change counter |
| `status_cron` | mirza_pro: VARCHAR(20) NULL DEFAULT '1' \| mirza_pro: VARCHAR(20) (added on upgrade; default '1') | public | cron notification flag |
| `expire` | mirza_pro: VARCHAR(100) NULL \| mirza_pro: VARCHAR(100) (added on upgrade; default null) | public | agent expiry unix ts |
| `token` | mirza_pro: VARCHAR(100) NULL \| mirza_pro: VARCHAR(100) (added on upgrade; default null) | secret | mini-app auth token; listed as sensitive in function.php:441 |
| `lang` | mirza_pro: VARCHAR(5) NULL DEFAULT 'fa' \| mirza_pro: varchar(5) (added on upgrade; default 'fa') | public | UI language |
| `low_balance_alert_sent` | mirza_pro: TINYINT(1) (added on upgrade; default '0') | public | flag |

Relations:
- `affiliates` → user.id (referrer) (mirza_pro@b645db74 index.php:225-233 (referrer id stored; referrer affiliatescount incremented))
- `bottype` → botsaz.bot_token (sub-bot the user belongs to) (mirza_pro@b645db74 vpnbot/Default/index.php:66 (select user by bottype = $ApiToken))

Vocabularies:
- `agent`: `f` = normal customer (default tier) (mirza_pro@b645db74 db/tables/user.php:51); `n` = reseller tier "normal agent" (mirza_pro@b645db74 admin.php:5138-5141); `n2` = reseller tier "advanced agent"; may go negative down to -maxbuyagent (mirza_pro@b645db74 admin.php:5138-5141; mirza_pro@b645db74 function.php:974)
- `User_Status`: `Active` = not blocked (spelling used by NEXA read set) (nexa-bot@9e604de docs/legacy-migration/importer.md:338; /home/user/mirza-to-nexa/docs/MAPPING.md:44); `block` = blocked by admin (mirza_pro@b645db74 admin.php:3658)

Time columns: `register` = time() unix seconds (or literal "none" default) (mirza_pro@b645db74 index.php:162; mirza_pro@b645db74 db/tables/user.php:56); `last_message_time` = unix seconds (anti-spam) (mirza_pro@b645db74 index.php:174); `expire` = unix seconds = time()+days*86400 (reseller/agent expiry) (mirza_pro@b645db74 admin.php:8932-8933)

Money columns: `Balance` = toman (mirza_pro@b645db74 lang/fa.php:258 (addedNotice "%s تومان"); mirza_pro@b645db74 admin.php:3560 (maxAmountToman); owner decision 7 per /home/user/mirza-to-nexa/docs/NEXA_EVIDENCE.md:153-156); `maxbuyagent` = toman (credit line for n2) (mirza_pro@b645db74 function.php:974 (Balance may reach -maxbuyagent)); `pricediscount` = percent, not money (mirza_pro@b645db74 index.php:5961 (volume*pricediscount/100))

Written/read by: mirza_pro@b645db74 index.php:86 (INSERT IGNORE INTO user on first contact); mirza_pro@b645db74 index.php:229 (affiliates = referrer id); mirza_pro@b645db74 function.php:968-987 (deductBalance/addBalance); mirza_pro@b645db74 admin.php:3565-3575 (admin add balance); botmirzapanel@92c0ed06 table.php:14-35

Open questions:
- Deployed fork columns differ from both public revisions (operator report); verify user columns from the dump header before mapping.
- user.token is a mini-app bearer token (SECRET); must never be exported.
- user.bottype holds a sub-bot TOKEN in mirza_pro (SECRET).

### `help`

Found in: `botmirzapanel@92c0ed06 table.php:90`; `mirza_pro@b645db74 db/tables/help.php:4`; `operator-report 2026-10-10`
Primary key: `id`

**Purpose.** Tutorials (OS name, Telegram media file_id, description, category).

**Class:** CONFIGURATION (NEXA: OWNER_DECISION) — Content; Telegram file_ids are bot-scoped and not portable to another bot token.

**NEXA target:** NEXA delivery_tutorials (nexa-bot@9e604de apps/api/drizzle/0211_delivery_tutorials.sql:1) — manual re-upload

| Column | Declared type(s) | Sensitivity | Notes |
|---|---|---|---|
| `id` | botmirzapanel: INT(6) UNSIGNED AUTO_INCREMENT PRIMARY KEY \| mirza_pro: INT(6) UNSIGNED AUTO_INCREMENT PRIMARY KEY | public |  |
| `name_os` | botmirzapanel: varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL \| mirza_pro: varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT N | public |  |
| `Media_os` | botmirzapanel: varchar(5000) NOT NULL \| mirza_pro: varchar(5000) NOT NULL | public | Telegram file_id |
| `type_Media_os` | botmirzapanel: varchar(500) NOT NULL \| mirza_pro: varchar(500) NOT NULL | public |  |
| `Description_os` | botmirzapanel: TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL \| mirza_pro: TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL | public |  |
| `category` | mirza_pro: TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL \| mirza_pro: TEXT (added on upgrade; default null) | public |  |

Written/read by: mirza_pro@b645db74 admin.php:1371, 1383

Open questions:
- help_category (operator) has no public DDL.

### `setting`

Found in: `botmirzapanel@92c0ed06 table.php:110`; `mirza_pro@b645db74 db/tables/setting.php:18`; `operator-report 2026-10-10`
Primary key: `none declared`

**Purpose.** Single-row global bot settings (feature toggles, trial limits, report channel, keyboard layout, cron flags, webhook secret).

**Class:** CONFIGURATION (NEXA: SECRETS_MANUAL; holds secret columns (NEXA OQ-MZ-INV-02: a table holding any secret is SECRETS_MANUAL); may become ARCHIVE only with an owner-reviewed column allowlist that omits: webhook_secret) — Only non-secret keys may be proposed; webhook_secret excluded.

**NEXA target:** NEXA setting_values (nexa-bot@9e604de apps/api/drizzle/0010_control_plane.sql:87) — owner review per key

| Column | Declared type(s) | Sensitivity | Notes |
|---|---|---|---|
| `Bot_Status` | botmirzapanel: varchar(200)  CHARACTER SET utf8mb4 COLLATE utf8mb4_bin  NULL \| mirza_pro: varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL \| | public |  |
| `help_Status` | botmirzapanel: varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin  NULL | public |  |
| `roll_Status` | botmirzapanel: varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin  NULL \| mirza_pro: varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL \|  | public |  |
| `get_number` | botmirzapanel: varchar(200)  CHARACTER SET utf8mb4 COLLATE utf8mb4_bin  NULL \| mirza_pro: varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL \| | public |  |
| `iran_number` | botmirzapanel: varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin  NULL \| mirza_pro: varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL \|  | public |  |
| `NotUser` | botmirzapanel: varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin  NULL \| mirza_pro: varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL \|  | public |  |
| `Channel_Report` | botmirzapanel: varchar(600)  NULL \| mirza_pro: varchar(600) NULL \| mirza_pro: VARCHAR(200) (added on upgrade; default '0') | personal | report group chat id |
| `limit_usertest_all` | botmirzapanel: varchar(600)  NULL \| mirza_pro: varchar(600) NULL \| mirza_pro: VARCHAR(200) (added on upgrade; default '1') | public |  |
| `time_usertest` | botmirzapanel: varchar(600)  NULL | public |  |
| `val_usertest` | botmirzapanel: varchar(600)  NULL | public |  |
| `Extra_volume` | botmirzapanel: varchar(600)  NULL \| botmirzapanel: VARCHAR(200) (added; default '0') | public |  |
| `namecustome` | botmirzapanel: varchar(100)  NULL \| botmirzapanel: VARCHAR(200) (added; default '0') | public |  |
| `status_verify` | botmirzapanel: varchar(50)  NULL \| botmirzapanel: VARCHAR(50) (added; default '0') | public |  |
| `removedayc` | botmirzapanel: varchar(100)  NULL \| botmirzapanel: VARCHAR(100) (added; default '1') \| mirza_pro: varchar(600) NULL \| mirza_pro: VARCHAR(200) (added on upgra | public |  |
| `copy_cart` | botmirzapanel: varchar(20)  NULL \| botmirzapanel: VARCHAR(20) (added; default '0') | public |  |
| `statuscategory` | botmirzapanel: varchar(100)  NULL \| botmirzapanel: VARCHAR(50) (added; default '1') \| mirza_pro: varchar(200) NULL \| mirza_pro: VARCHAR(600) (added on upgrad | public |  |
| `affiliatespercentage` | mirza_pro: varchar(600) NULL \| mirza_pro: VARCHAR(600) (added on upgrade; default '0') | public |  |
| `showcard` | mirza_pro: varchar(200) NULL \| mirza_pro: VARCHAR(200) (added on upgrade; default '1') | public |  |
| `numbercount` | mirza_pro: varchar(600) NULL \| mirza_pro: VARCHAR(600) (added on upgrade; default '0') | public |  |
| `statusnewuser` | mirza_pro: varchar(600) NULL \| mirza_pro: VARCHAR(600) (added on upgrade; default 'onnewuser') | public |  |
| `statusterffh` | mirza_pro: varchar(200) NULL | public |  |
| `volumewarn` | mirza_pro: varchar(200) NULL \| mirza_pro: VARCHAR(200) (added on upgrade; default '2') | public |  |
| `inlinebtnmain` | mirza_pro: varchar(200) NULL \| mirza_pro: VARCHAR(200) (added on upgrade; default 'offinline') | public |  |
| `verifystart` | mirza_pro: varchar(200) NULL \| mirza_pro: VARCHAR(200) (added on upgrade; default 'offverify') | public |  |
| `id_support` | mirza_pro: varchar(200) NULL \| mirza_pro: VARCHAR(100) (added on upgrade; default '0') | personal | support account |
| `statusnamecustom` | mirza_pro: varchar(100) NULL \| mirza_pro: VARCHAR(100) (added on upgrade; default 'offnamecustom') | public |  |
| `statuscategorygenral` | mirza_pro: varchar(100) NULL \| mirza_pro: VARCHAR(100) (added on upgrade; default 'offcategorys') | public |  |
| `statussupportpv` | mirza_pro: varchar(100) NULL \| mirza_pro: VARCHAR(100) (added on upgrade; default 'offpvsupport') | public |  |
| `agentreqprice` | mirza_pro: varchar(100) NULL \| mirza_pro: VARCHAR(100) (added on upgrade; default '0') | public |  |
| `bulkbuy` | mirza_pro: varchar(100) NULL \| mirza_pro: VARCHAR(100) (added on upgrade; default 'onbulk') | public |  |
| `on_hold_day` | mirza_pro: varchar(100) NULL \| mirza_pro: VARCHAR(100) (added on upgrade; default '4') | public |  |
| `cronvolumere` | mirza_pro: varchar(100) NULL \| mirza_pro: VARCHAR(100) (added on upgrade; default '5') | public |  |
| `verifybucodeuser` | mirza_pro: varchar(100) NULL \| mirza_pro: VARCHAR(100) (added on upgrade; default 'offverify') | public |  |
| `scorestatus` | mirza_pro: varchar(100) NULL \| mirza_pro: VARCHAR(100) (added on upgrade; default '0') | public |  |
| `Lottery_prize` | mirza_pro: TEXT NULL \| mirza_pro: TEXT (added on upgrade; default $awardDefaults) | financial | JSON amounts |
| `wheelـluck_price` | mirza_pro: varchar(45) NULL \| mirza_pro: varchar(45) (added on upgrade; default '0') | public |  |
| `btn_status_extned` | mirza_pro: varchar(45) NULL \| mirza_pro: varchar(45) (added on upgrade; default '0') | public |  |
| `daywarn` | mirza_pro: varchar(45) NULL \| mirza_pro: varchar(45) (added on upgrade; default '2') | public |  |
| `categoryhelp` | mirza_pro: varchar(45) NULL \| mirza_pro: varchar(45) (added on upgrade; default '0') | public |  |
| `linkappstatus` | mirza_pro: varchar(45) NULL \| mirza_pro: varchar(45) (added on upgrade; default '0') | public |  |
| `wheelagent` | mirza_pro: varchar(45) NULL \| mirza_pro: varchar(45) (added on upgrade; default '1') | public |  |
| `Lotteryagent` | mirza_pro: varchar(45) NULL \| mirza_pro: varchar(45) (added on upgrade; default '1') | public |  |
| `statusfirstwheel` | mirza_pro: varchar(45) NULL \| mirza_pro: varchar(45) (added on upgrade; default '0') | public |  |
| `statuslimitchangeloc` | mirza_pro: varchar(45) NULL \| mirza_pro: varchar(45) (added on upgrade; default '0') | public |  |
| `Debtsettlement` | mirza_pro: varchar(45) NULL \| mirza_pro: varchar(45) (added on upgrade; default '1') | public |  |
| `Dice` | mirza_pro: varchar(45) NULL \| mirza_pro: varchar(45) (added on upgrade; default '0') | public |  |
| `keyboardmain` | mirza_pro: TEXT NOT NULL \| mirza_pro: TEXT (added on upgrade; default $keyboardMain) | public | JSON |
| `statusnoteforf` | mirza_pro: varchar(45) NOT NULL \| mirza_pro: varchar(20) (added on upgrade; default '1') | public |  |
| `statuscopycart` | mirza_pro: varchar(45) NOT NULL \| mirza_pro: varchar(20) (added on upgrade; default '0') | public |  |
| `timeauto_not_verify` | mirza_pro: varchar(20) NOT NULL \| mirza_pro: varchar(20) (added on upgrade; default '4') | public |  |
| `status_keyboard_config` | mirza_pro: varchar(20) NULL \| mirza_pro: varchar(20) (added on upgrade; default '1') | public |  |
| `cron_status` | mirza_pro: TEXT NOT NULL \| mirza_pro: TEXT (added on upgrade; default $cronStatus) | public | JSON |
| `text_edit` | mirza_pro: JSON NULL \| mirza_pro: JSON (added on upgrade; default '{}') | public | JSON |
| `limitnumber` | mirza_pro: varchar(200) NULL \| mirza_pro: varchar(200) (added on upgrade; default $limitList) | public |  |
| `webhook_secret` | mirza_pro: varchar(200) NOT NULL DEFAULT '' \| mirza_pro: VARCHAR(200) NOT NULL DEFAULT '' (added on upgrade; default null) | secret | Telegram webhook secret |
| `alert_balance` | mirza_pro: VARCHAR(10) (added on upgrade; default '0') | public |  |
| `balance_value_alert` | mirza_pro: VARCHAR(30) (added on upgrade; default '10000') | public |  |

Vocabularies:
- `Bot_Status`: `botstatuson` = bot on (mirza_pro@b645db74 db/tables/setting.php:70)
- `statuscategory`: `offcategory` =  (mirza_pro@b645db74 db/tables/setting.php:79)

Money columns: `agentreqprice` = toman (charged via deductBalance) (mirza_pro@b645db74 index.php:6105); `wheelـluck_price` = toman (credited via addBalance) (mirza_pro@b645db74 index.php:6253); `affiliatespercentage` = percent (mirza_pro@b645db74 function.php:1141)

Written/read by: mirza_pro@b645db74 db/tables/setting.php:68-113 (seed); botmirzapanel@92c0ed06 table.php:136

Open questions:
- Column wheelـluck_price contains U+0640 (tatweel) in its name.

### `admin`

Found in: `botmirzapanel@92c0ed06 table.php:176`; `mirza_pro@b645db74 db/tables/admin.php:4`; `operator-report 2026-10-10`
Primary key: `id_admin`

**Purpose.** Bot administrators (Telegram ids) with role and web-panel credentials.

**Class:** EXCLUDED (NEXA: SECRETS_MANUAL; holds secret columns (NEXA OQ-MZ-INV-02: a table holding any secret is SECRETS_MANUAL); names and counts only) — Contains password hashes; admins are re-created in NEXA by hand (NEXA has its own RBAC + 2FA).

**NEXA target:** NEXA admins (nexa-bot@9e604de apps/api/drizzle/0005_identity_and_rbac.sql:47) — manual; nexa-bot@9e604de docs/legacy-migration/table-inventory.md:116-118 (secrets are never imported; reconfigured by hand)

| Column | Declared type(s) | Sensitivity | Notes |
|---|---|---|---|
| `id_admin` | botmirzapanel: varchar(200) PRIMARY KEY NOT NULL \| mirza_pro: varchar(500) PRIMARY KEY NOT NULL | personal |  |
| `username` | mirza_pro: varchar(1000) NOT NULL \| mirza_pro: VARCHAR(200) (added on upgrade; default null) | secret | web login name |
| `password` | mirza_pro: varchar(1000) NOT NULL \| mirza_pro: VARCHAR(200) (added on upgrade; default null) | secret | bcrypt hash |
| `rule` | mirza_pro: varchar(500) NOT NULL \| mirza_pro: VARCHAR(200) (added on upgrade; default 'administrator') | public | role |

Relations:
- `id_admin` → user.id (mirza_pro@b645db74 db/tables/admin.php:12)

Vocabularies:
- `rule`: `administrator` = full admin (mirza_pro@b645db74 admin.php (157 checks)); `Seller` = may confirm/reject payments (mirza_pro@b645db74 admin.php:1686); `support` = support only; skipped for receipts (mirza_pro@b645db74 index.php:5601)

Written/read by: botmirzapanel@92c0ed06 table.php:172-178; mirza_pro@b645db74 db/tables/admin.php:10-17; mirza_pro@b645db74 db/migrations/012_admin_panel_credentials.php

Open questions:
- Admin ids are useful to flag service_other rows whose id_user is an admin; export only a hashed id set if needed.

### `channels`

Found in: `botmirzapanel@92c0ed06 table.php:192`; `mirza_pro@b645db74 db/tables/channels.php:4`; `operator-report 2026-10-10`
Primary key: `none declared`

**Purpose.** Mandatory-join channels.

**Class:** CONFIGURATION (NEXA: SECRETS_MANUAL; holds secret columns (NEXA OQ-MZ-INV-02: a table holding any secret is SECRETS_MANUAL); may become ARCHIVE only with an owner-reviewed column allowlist that omits: linkjoin) — Small list; invite links grant access to private channels.

**NEXA target:** NEXA setting telegram.channels (nexa-bot@9e604de docs/package-b-channel-membership-audit.md:9-13) — manual

| Column | Declared type(s) | Sensitivity | Notes |
|---|---|---|---|
| `link` | botmirzapanel: varchar(200) NOT NULL \| mirza_pro: varchar(200) NOT NULL | public | channel handle/link |
| `remark` | mirza_pro: varchar(200) NOT NULL \| mirza_pro: VARCHAR(200) (added on upgrade; default null) | public |  |
| `linkjoin` | mirza_pro: varchar(200) NOT NULL \| mirza_pro: VARCHAR(200) (added on upgrade; default null) | secret | invite link (grants access) |

Written/read by: mirza_pro@b645db74 admin.php:214; botmirzapanel@92c0ed06 table.php:192-193

### `marzban_panel`

Found in: `botmirzapanel@92c0ed06 table.php:208`; `mirza_pro@b645db74 db/tables/marzban_panel.php:11`; `operator-report 2026-10-10`
Rows (operator report): 5
Primary key: `id`

**Purpose.** Provider panels (Marzban, x-ui, s-ui, WGDashboard, Manualsale, ibsng, mikrotik …) with URL, credentials, per-tier add-on prices and behaviour flags.

**Class:** REVIEW (NEXA: SECRETS_MANUAL; holds secret columns (NEXA OQ-MZ-INV-02: a table holding any secret is SECRETS_MANUAL); may become ARCHIVE only with an owner-reviewed column allowlist that omits: url_panel, username_panel, password_panel, linksubx, datelogin, inbounds, proxies, secret_code) — Only id/code_panel/name_panel/type/status are needed to map services to NEXA panels; everything else is credentials or behaviour that NEXA re-creates by hand. Export a non-secret column allowlist only.

**NEXA target:** NEXA panels + panel_credentials (nexa-bot@9e604de apps/api/drizzle/0017_panels_providers_credentials_health.sql:1,38) — operator maps code_panel → NEXA panel; credentials manual

| Column | Declared type(s) | Sensitivity | Notes |
|---|---|---|---|
| `id` | botmirzapanel: INT(6) UNSIGNED AUTO_INCREMENT PRIMARY KEY \| mirza_pro: INT(6) UNSIGNED AUTO_INCREMENT PRIMARY KEY | public |  |
| `name_panel` | botmirzapanel: varchar(2000) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NULL \| mirza_pro: varchar(2000) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL | public |  |
| `url_panel` | botmirzapanel: varchar(2000) NULL \| mirza_pro: varchar(2000) NULL | secret | panel endpoint |
| `username_panel` | botmirzapanel: varchar(200) NULL \| mirza_pro: varchar(200) NULL | secret | panel admin login |
| `password_panel` | botmirzapanel: varchar(200) NULL \| mirza_pro: TEXT NULL | secret | panel password |
| `status` | botmirzapanel: varchar(100) NULL \| botmirzapanel: VARCHAR(100) (added by ALTER) \| mirza_pro: varchar(500) NULL \| mirza_pro: VARCHAR(50) (added on upgrade; de | public |  |
| `statusTest` | botmirzapanel: varchar(100) NULL \| botmirzapanel: VARCHAR(100) (added by ALTER) | public |  |
| `type` | botmirzapanel: varchar(200) NULL \| botmirzapanel: VARCHAR(200) (added by ALTER) \| mirza_pro: varchar(100) NULL \| mirza_pro: VARCHAR(50) (added on upgrade; de | public |  |
| `linksubx` | botmirzapanel: varchar(500) NULL \| botmirzapanel: VARCHAR(500) (added by ALTER) \| mirza_pro: varchar(1000) NULL \| mirza_pro: VARCHAR(200) (added on upgrade;  | secret | subscription base URL |
| `inboundid` | botmirzapanel: varchar(200) NULL \| botmirzapanel: VARCHAR(200) (added by ALTER) \| mirza_pro: varchar(100) NULL \| mirza_pro: VARCHAR(50) (added on upgrade; de | public |  |
| `MethodUsername` | botmirzapanel: varchar(900)  NULL \| botmirzapanel: VARCHAR(900) (added by ALTER) \| mirza_pro: varchar(700) NULL \| mirza_pro: VARCHAR(100) (added on upgrade;  | public |  |
| `sublink` | botmirzapanel: varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin  NULL \| botmirzapanel: VARCHAR(200) (added by ALTER) \| mirza_pro: varchar(500) NULL \| m | public |  |
| `configManual` | botmirzapanel: varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin  NULL \| botmirzapanel: VARCHAR(200) (added by ALTER) | public |  |
| `onholdstatus` | botmirzapanel: varchar(200) NULL \| botmirzapanel: VARCHAR(100) (added by ALTER) | public |  |
| `datelogin` | botmirzapanel: TEXT NULL \| botmirzapanel: TEXT (added by ALTER) \| mirza_pro: TEXT NULL \| mirza_pro: TEXT (added on upgrade; default null) | secret | cached login session/token; function.php:441 |
| `inbounds` | botmirzapanel: TEXT NULL \| botmirzapanel: TEXT (added by ALTER) \| mirza_pro: TEXT NULL \| mirza_pro: TEXT (added on upgrade; default null) | secret | inbound config JSON |
| `proxies` | botmirzapanel: TEXT NULL \| botmirzapanel: TEXT (added by ALTER) \| mirza_pro: TEXT NULL \| mirza_pro: TEXT (added on upgrade; default null) | secret | proxy config JSON |
| `code_panel` | mirza_pro: varchar(200) NULL \| mirza_pro: VARCHAR(50) (added on upgrade; default null) | public |  |
| `agent` | mirza_pro: varchar(200) NULL \| mirza_pro: VARCHAR(50) (added on upgrade; default 'all') | public |  |
| `config` | mirza_pro: varchar(500) NULL \| mirza_pro: VARCHAR(50) (added on upgrade; default 'offconfig') | public |  |
| `TestAccount` | mirza_pro: varchar(100) NULL \| mirza_pro: VARCHAR(50) (added on upgrade; default 'ONTestAccount') | public |  |
| `limit_panel` | mirza_pro: varchar(100) NULL \| mirza_pro: VARCHAR(50) (added on upgrade; default 'unlimted') | public |  |
| `namecustom` | mirza_pro: varchar(100) NULL \| mirza_pro: VARCHAR(100) (added on upgrade; default 'vpn') | public |  |
| `Methodextend` | mirza_pro: varchar(100) NULL \| mirza_pro: VARCHAR(100) (added on upgrade; default 'resetVolumeTime') | public |  |
| `conecton` | mirza_pro: varchar(100) NULL \| mirza_pro: VARCHAR(100) (added on upgrade; default 'offconecton') | public |  |
| `inboundstatus` | mirza_pro: varchar(100) NULL \| mirza_pro: VARCHAR(50) (added on upgrade; default 'offinbounddisable') | public |  |
| `inbound_deactive` | mirza_pro: varchar(100) NULL \| mirza_pro: VARCHAR(100) (added on upgrade; default '0') | public |  |
| `time_usertest` | mirza_pro: varchar(100) NULL \| mirza_pro: VARCHAR(50) (added on upgrade; default '1') | public |  |
| `val_usertest` | mirza_pro: varchar(100) NULL \| mirza_pro: VARCHAR(50) (added on upgrade; default '100') | public |  |
| `secret_code` | mirza_pro: varchar(200) NULL \| mirza_pro: VARCHAR(200) (added on upgrade; default null) | secret | listed as sensitive function.php:441 |
| `priceChangeloc` | mirza_pro: varchar(200) NULL \| mirza_pro: VARCHAR(100) (added on upgrade; default '0') | financial |  |
| `priceextravolume` | mirza_pro: varchar(500) NULL \| mirza_pro: VARCHAR(500) (added on upgrade; default $defaultPrice) | financial |  |
| `pricecustomvolume` | mirza_pro: varchar(500) NULL \| mirza_pro: VARCHAR(500) (added on upgrade; default $defaultPrice) | financial |  |
| `pricecustomtime` | mirza_pro: varchar(500) NULL \| mirza_pro: VARCHAR(500) (added on upgrade; default $defaultPrice) | financial |  |
| `priceextratime` | mirza_pro: varchar(500) NULL \| mirza_pro: VARCHAR(500) (added on upgrade; default $defaultPrice) | financial |  |
| `mainvolume` | mirza_pro: varchar(500) NULL \| mirza_pro: VARCHAR(500) (added on upgrade; default $defaultMain) | public |  |
| `maxvolume` | mirza_pro: varchar(500) NULL \| mirza_pro: VARCHAR(500) (added on upgrade; default $defaultMaxVolume) | public |  |
| `maintime` | mirza_pro: varchar(500) NULL \| mirza_pro: VARCHAR(500) (added on upgrade; default $defaultMain) | public |  |
| `maxtime` | mirza_pro: varchar(500) NULL \| mirza_pro: VARCHAR(500) (added on upgrade; default $defaultMaxTime) | public |  |
| `status_extend` | mirza_pro: varchar(100) NULL \| mirza_pro: VARCHAR(50) (added on upgrade; default 'on_extend') | public |  |
| `subvip` | mirza_pro: varchar(60) NULL \| mirza_pro: VARCHAR(60) (added on upgrade; default 'offsubvip') | public |  |
| `changeloc` | mirza_pro: varchar(60) NULL \| mirza_pro: VARCHAR(60) (added on upgrade; default 'offchangeloc') | public |  |
| `on_hold_test` | mirza_pro: varchar(60) NOT NULL \| mirza_pro: VARCHAR(60) (added on upgrade; default '1') | public |  |
| `version_panel` | mirza_pro: varchar(60) NOT NULL \| mirza_pro: VARCHAR(60) (added on upgrade; default '0') | public |  |
| `customvolume` | mirza_pro: TEXT NULL \| mirza_pro: TEXT (added on upgrade; default $zeroVolume) | public |  |
| `hide_user` | mirza_pro: TEXT NULL \| mirza_pro: TEXT (added on upgrade; default null) | personal |  |

Relations:
- `name_panel` → invoice.Service_location, product.Location (by name) (mirza_pro@b645db74 function.php:1020, 1034)
- `code_panel` → DiscountSell.code_panel, manualsell.codepanel, service_other.value.old_panel/new_panel (mirza_pro@b645db74 index.php:4169, 2310-2311)

Vocabularies:
- `type`: `marzban (default) / Manualsale / WGDashboard / ibsng / mikrotik …` = driver (mirza_pro@b645db74 db/tables/marzban_panel.php:70; mirza_pro@b645db74 function.php:1095-1097)
- `agent`: `all|f|n|n2` = tier allowed (mirza_pro@b645db74 db/tables/marzban_panel.php:59)
- `Methodextend`: `resetVolumeTime (default) …` = renewal method key (mirza_pro@b645db74 db/tables/marzban_panel.php:66; db/migrations/001)

Money columns: `priceextravolume, pricecustomvolume, pricecustomtime, priceextratime` = toman, JSON per tier {f,n,n2} (mirza_pro@b645db74 db/tables/marzban_panel.php:4,77-80; mirza_pro@b645db74 index.php:5979-5982); `priceChangeloc` = toman (mirza_pro@b645db74 db/tables/marzban_panel.php:76)

Written/read by: mirza_pro@b645db74 db/tables/marzban_panel.php:11-95; mirza_pro@b645db74 db/migrations/001,002,009; botmirzapanel@92c0ed06 table.php:208-298

Open questions:
- Real dump has 5 panels (operator); confirm code_panel uniqueness.

### `product`

Found in: `botmirzapanel@92c0ed06 table.php:310`; `mirza_pro@b645db74 db/tables/product.php:4`
Rows (operator report): 64
Primary key: `id`

**Purpose.** Catalogue product (plan) definitions per panel location and tier.

**Class:** OPERATIONAL (NEXA: SUPPORTED; read only through an explicit column allowlist that omits the secret columns) — Already mapped (review-only) in NEXA v1.

**NEXA target:** legacy_product_reviews — see docs/MAPPING.md

| Column | Declared type(s) | Sensitivity | Notes |
|---|---|---|---|
| `id` | botmirzapanel: INT(6) UNSIGNED AUTO_INCREMENT PRIMARY KEY \| mirza_pro: INT(6) UNSIGNED AUTO_INCREMENT PRIMARY KEY | public |  |
| `code_product` | botmirzapanel: varchar(200)  NULL \| botmirzapanel: VARCHAR(200) (added by ALTER) \| mirza_pro: varchar(200) NULL \| mirza_pro: varchar(50) (added on upgrade; d | public |  |
| `name_product` | botmirzapanel: varchar(2000) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NULL \| mirza_pro: varchar(2000) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL | public |  |
| `price_product` | botmirzapanel: varchar(2000) NULL \| mirza_pro: varchar(2000) NULL | financial |  |
| `Volume_constraint` | botmirzapanel: varchar(2000) NULL \| mirza_pro: varchar(2000) NULL | public |  |
| `Location` | botmirzapanel: varchar(1000) NULL \| botmirzapanel: VARCHAR(1000) (added by ALTER) \| mirza_pro: varchar(200) NULL \| mirza_pro: VARCHAR(200) (added on upgrade; | public |  |
| `Service_time` | botmirzapanel: varchar(200) NULL \| mirza_pro: varchar(200) NULL | public |  |
| `Category` | botmirzapanel: varchar(600) NULL \| botmirzapanel: VARCHAR(600) (added by ALTER) | public |  |
| `agent` | mirza_pro: varchar(100) NULL \| mirza_pro: varchar(50) (added on upgrade; default 'f') | public |  |
| `note` | mirza_pro: TEXT NULL \| mirza_pro: TEXT (added on upgrade; default '') | personal | free text |
| `data_limit_reset` | mirza_pro: varchar(200) NULL \| mirza_pro: varchar(100) (added on upgrade; default 'no_reset') | public |  |
| `one_buy_status` | mirza_pro: varchar(20) NOT NULL \| mirza_pro: VARCHAR(20) (added on upgrade; default '0') | public |  |
| `inbounds` | mirza_pro: TEXT NULL \| mirza_pro: TEXT (added on upgrade; default null) | secret | panel inbound config JSON; dropped by converter |
| `proxies` | mirza_pro: TEXT NULL \| mirza_pro: TEXT (added on upgrade; default null) | secret | panel proxy settings JSON (may contain keys); dropped by converter |
| `category` | mirza_pro: varchar(400) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL \| mirza_pro: varchar(200) (added on upgrade; default null) | public |  |
| `hide_panel` | mirza_pro: TEXT NOT NULL \| mirza_pro: TEXT (added on upgrade; default '{}') | public | JSON |
| `status_product` | mirza_pro: VARCHAR(20) (added on upgrade; default 'active') | public |  |

Relations:
- `Location` → marzban_panel.name_panel or '/all' (mirza_pro@b645db74 function.php:1020)
- `category` → category.remark (by text) (mirza_pro@b645db74 index.php:3594-3602)
- `agent` → user.agent tier (mirza_pro@b645db74 index.php:3598)

Vocabularies:
- `agent`: `f|n|n2` = tier the product is sold to (mirza_pro@b645db74 db/tables/product.php:24; mirza_pro@b645db74 index.php:3598)
- `data_limit_reset`: `no_reset` = default; other values pass to panel (mirza_pro@b645db74 db/tables/product.php:26)

Money columns: `price_product` = toman (owner decision) (/home/user/mirza-to-nexa/docs/NEXA_EVIDENCE.md:155)

Written/read by: mirza_pro@b645db74 admin.php:1636; mirza_pro@b645db74 api/product.php:143; mirza_pro@b645db74 panel/product.php:23

Open questions:
- Real product columns of the deployed fork (OQ-MZ-INV-09).

### `invoice`

Found in: `botmirzapanel@92c0ed06 table.php:348`; `mirza_pro@b645db74 db/tables/invoice.php:6`
Rows (operator report): 128703
Primary key: `id_invoice`

**Purpose.** One row per service order / panel account (purchase, test account, manual sale). Status tracks lifecycle of the panel account.

**Class:** OPERATIONAL (NEXA: SUPPORTED; read only through an explicit column allowlist that omits the secret columns) — Already in NEXA v1 contract (service candidates + invoice archive); secret columns dropped at parse time.

**NEXA target:** legacy_service_candidates, legacy_invoice_archive, legacy_product_shapes — see docs/MAPPING.md

| Column | Declared type(s) | Sensitivity | Notes |
|---|---|---|---|
| `id_invoice` | botmirzapanel: varchar(200) PRIMARY KEY \| mirza_pro: varchar(200) PRIMARY KEY | public | random id |
| `id_user` | botmirzapanel: varchar(200) NULL \| mirza_pro: varchar(200) NULL | personal | Telegram id |
| `username` | botmirzapanel: varchar(200) NULL \| mirza_pro: varchar(300) NULL | personal | panel account username |
| `Service_location` | botmirzapanel: varchar(200) NULL \| mirza_pro: varchar(300) NULL | public | panel name |
| `time_sell` | botmirzapanel: varchar(200) NULL \| botmirzapanel: VARCHAR(2000) (added by ALTER) \| mirza_pro: VARCHAR(200) NULL \| mirza_pro: VARCHAR(200) (added on upgrade;  | public | unix ts |
| `name_product` | botmirzapanel: varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NULL \| mirza_pro: varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL | public |  |
| `price_product` | botmirzapanel: varchar(200) NULL \| mirza_pro: varchar(200) NULL | financial | historical price |
| `Volume` | botmirzapanel: varchar(200) NULL \| mirza_pro: varchar(200) NULL | public | GB |
| `Service_time` | botmirzapanel: varchar(200) NULL \| mirza_pro: varchar(200) NULL | public | days |
| `user_info` | botmirzapanel: TEXT NULL \| botmirzapanel: TEXT (added by ALTER) \| mirza_pro: TEXT NULL \| mirza_pro: TEXT (added on upgrade; default null) | secret | subscription_url / manual-sale password |
| `Status` | botmirzapanel: varchar(200) NULL \| botmirzapanel: VARCHAR(2000) (added by ALTER) \| mirza_pro: varchar(200) NULL \| mirza_pro: VARCHAR(100) (added on upgrade;  | public | lifecycle status |
| `uuid` | mirza_pro: TEXT NULL \| mirza_pro: TEXT (added on upgrade; default null) | secret | panel client UUID (credential for V2Ray configs) |
| `note` | mirza_pro: varchar(500) NULL \| mirza_pro: VARCHAR(700) (added on upgrade; default null) | personal | free text |
| `bottype` | mirza_pro: varchar(200) NULL \| mirza_pro: VARCHAR(200) (added on upgrade; default null) | secret | sub-bot token |
| `refral` | mirza_pro: varchar(100) NULL \| mirza_pro: VARCHAR(100) (added on upgrade; default null) | personal | referral marker |
| `time_cron` | mirza_pro: varchar(100) NULL \| mirza_pro: VARCHAR(100) (added on upgrade; default null) | public |  |
| `notifctions` | mirza_pro: TEXT NOT NULL \| mirza_pro: TEXT NOT NULL (added on upgrade; default $notifications) | public | JSON {volume,time} notification flags |

Relations:
- `id_user` → user.id (mirza_pro@b645db74 index.php:3921-3922)
- `Service_location` → marzban_panel.name_panel (by NAME, not id) (mirza_pro@b645db74 function.php:1034)
- `username` → panel account username; join key for service_other.username and Payment_report.id_invoice suffix (mirza_pro@b645db74 function.php:1016, 1441)
- `bottype` → botsaz.bot_token (botmirzapanel@92c0ed06/mirza_pro@b645db74: see docs/MAPPING.md:23 (sub-bot token))

Vocabularies:
- `Status`: `unpaid` = created, awaiting payment (mirza_pro@b645db74 index.php:3922); `active` = paid and provisioned (mirza_pro@b645db74 function.php:1051; mirza_pro@b645db74 index.php:1880); `disabledn` = disabled (user action) (mirza_pro@b645db74 index.php:811); `disablebyadmin` = disabled by admin (mirza_pro@b645db74 admin.php:9020); `removebyuser` = removed by user (mirza_pro@b645db74 index.php:1211); `removebyadmin` = removed via web API (mirza_pro@b645db74 api/invoice.php:157); `removedbyadmin` = removed after cancel request accepted (DIFFERENT spelling) (mirza_pro@b645db74 admin.php:4749, 4778); `send_on_hold` = on-hold notice sent (mirza_pro@b645db74 cronbot/on_hold.php:37); `end_of_time / end_of_volume / sendedwarn` = expiry/volume warning states (read by UI; writer not located) (mirza_pro@b645db74 panel/user.php:299-301); `Unsuccessful` = failed order (deleted by optimize cron) (mirza_pro@b645db74 cronbot/optimize_order.php:11)

Time columns: `time_sell` = time() unix seconds (mirza_pro@b645db74 index.php:3893, 3921; mirza_pro@b645db74 cronbot/statusday.php:32 (FROM_UNIXTIME))

Money columns: `price_product` = toman (owner decision; Zarinpal is called with IRT for the same amounts) (mirza_pro@b645db74 function.php:2588-2589; /home/user/mirza-to-nexa/docs/NEXA_EVIDENCE.md:155-159)

Written/read by: mirza_pro@b645db74 index.php:3921-3923 (INSERT invoice Status=unpaid, time_sell=time()); mirza_pro@b645db74 function.php:1051 (claim invoice → active on paid direct payment); mirza_pro@b645db74 index.php:811 (disabledn); mirza_pro@b645db74 index.php:1211 (removebyuser); mirza_pro@b645db74 admin.php:9020 (disablebyadmin); mirza_pro@b645db74 admin.php:4749 (removedbyadmin on cancel accept); mirza_pro@b645db74 api/invoice.php:157 (removebyadmin); mirza_pro@b645db74 cronbot/on_hold.php:37 (send_on_hold); mirza_pro@b645db74 cronbot/optimize_order.php:11 (DELETE unpaid/Unsuccessful)

Open questions:
- Deployed fork has columns no public revision declares (code_panel, code_product, is_test, is_custom, time_unit per NEXA_EVIDENCE.md:20).
- Two spellings removebyadmin vs removedbyadmin exist in public code.
- optimize_order cron deletes unpaid/Unsuccessful invoices: history is incomplete.

### `Payment_report`

Found in: `botmirzapanel@92c0ed06 table.php:389`; `mirza_pro@b645db74 db/tables/Payment_report.php:5`; `operator-report 2026-10-10`
Rows (operator report): 215082
Primary key: `id`

**Purpose.** Money-in / payment-attempt log. One row per payment attempt (gateway link, card-to-card receipt, crypto, Telegram Stars) and per admin manual balance adjustment. Gateway rows are created Unpaid when the payment link is issued and become paid/expire/reject later. On paid, DirectPayment() either credits user.Balance (top-up) or completes the purchase/renewal/extra described by id_invoice.

**Class:** ARCHIVE (NEXA: SECRETS_MANUAL; holds secret columns (NEXA OQ-MZ-INV-02: a table holding any secret is SECRETS_MANUAL); may become ARCHIVE only with an owner-reviewed column allowlist that omits: bottype) — Historical money-in evidence; NEXA never replays payment history (opening balance only). Needed for reconciliation evidence and audit; dec_not_confirmed/receipt_trace need redaction/hashing.

**NEXA target:** new archive table proposal (legacy_payment_archive); NEXA live concepts payments/payment_receipts (nexa-bot@9e604de apps/api/drizzle/0032_phase4_commerce.sql:102; 0066_payment_receipts.sql:1) must NOT be fed. nexa-bot@9e604de docs/legacy-migration/reconciliation.md:84-85 (wallet_transaction and payment history are never replayed)

| Column | Declared type(s) | Sensitivity | Notes |
|---|---|---|---|
| `id` | botmirzapanel: INT(6) UNSIGNED AUTO_INCREMENT PRIMARY KEY \| mirza_pro: INT(6) UNSIGNED AUTO_INCREMENT PRIMARY KEY | public | auto id |
| `id_user` | botmirzapanel: varchar(200) \| mirza_pro: varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci | personal | Telegram id of payer |
| `id_order` | botmirzapanel: varchar(500) \| mirza_pro: varchar(2000) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci | public | random 10-hex order id |
| `time` | botmirzapanel: varchar(200)  NULL \| mirza_pro: varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL | public | created |
| `price` | botmirzapanel: varchar(400) NULL \| mirza_pro: varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL | financial | amount (Toman), always positive |
| `dec_not_confirmed` | botmirzapanel: varchar(2000) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NULL \| mirza_pro: TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL | personal | POLYMORPHIC: user receipt text+caption (index.php:5619), admin reject reason (admin.php:1799), gateway authority/slug/id (index.php:4833, 5098, 5298), gateway JSON payloads (cronbot/iranpay1.php:29; Stars successful_payment index.php:6552-6562), "remove_all" (admin.php:7438), auto-confirm label (croncard.php:43). Treat as personal + financial; never export raw. |
| `Payment_Method` | botmirzapanel: varchar(400) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NULL \| botmirzapanel: VARCHAR(1000) (added by ALTER) \| mirza_pro: varchar(400) CHARACTER | public | method vocabulary |
| `payment_Status` | botmirzapanel: varchar(2000) NULL \| mirza_pro: varchar(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL | public | status vocabulary |
| `invoice` | botmirzapanel: varchar(300) NULL \| botmirzapanel: VARCHAR(300) (added by ALTER) | personal | BMP name of the purpose tag column |
| `at_updated` | mirza_pro: varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL \| mirza_pro: VARCHAR(200) (added on upgrade; default null) | public | receipt uploaded time |
| `bottype` | mirza_pro: varchar(300) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL \| mirza_pro: VARCHAR(300) (added on upgrade; default null) | secret | sub-bot API token ($ApiToken) |
| `message_id` | mirza_pro: INT NULL \| mirza_pro: INT (added on upgrade; default null) | public | Telegram message id of the payment-link message sent to the user (deleted on expire) |
| `id_invoice` | mirza_pro: varchar(1000) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL \| mirza_pro: VARCHAR(400) (added on upgrade; default 'none') | personal | purpose tag incl. panel username |
| `receipt_trace` | operator: type unknown | financial | operator-only column; semantics UNKNOWN |

Relations:
- `id_user` → user.id (mirza_pro@b645db74 function.php:1010 (select user by Payment_report.id_user))
- `id_order` → NOTHING: payment-local random id bin2hex(random_bytes(5)); used as gateway order_id and callback key. NOT invoice.id_invoice. (mirza_pro@b645db74 index.php:4691; mirza_pro@b645db74 function.php:2590-2593; mirza_pro@b645db74 admin.php:1686)
- `id_invoice (mirza_pro) / invoice (BMP)` → purpose tag '<purpose>|<arg>': getconfigafterpay|<invoice.username>; getextenduser|<username>%<service_other.value.id_order>; getextravolumeuser|<username>%<volume>; getextratimeuser|<username>%<days>; anything else (e.g. '0|0', '0 | 0', NULL) = wallet top-up / admin adjust (mirza_pro@b645db74 index.php:4689; mirza_pro@b645db74 function.php:1011, 1015, 1240-1250, 1366-1369, 1438-1441, 1509-1512; mirza_pro@b645db74 index.php:1806-1807, 2081-2082, 2623-2624, 3944-3945; botmirzapanel@92c0ed06 index.php:2043-2047)
- `bottype` → botsaz.bot_token (sub-bot) (mirza_pro@b645db74 vpnbot/Default/index.php:1292-1296)

Vocabularies:
- `payment_Status`: `Unpaid` = attempt created / link issued, not yet paid (mirza_pro@b645db74 index.php:4693; botmirzapanel@92c0ed06 index.php:2041); `waiting` = card-to-card / offline crypto receipt submitted, awaiting admin or auto-confirm (mirza_pro@b645db74 index.php:5618, 5740; botmirzapanel@92c0ed06 index.php:2264 (BMP inserts directly as waiting)); `paid` = money accepted (gateway verify, admin confirm, auto-confirm, admin manual adjust) (mirza_pro@b645db74 function.php:991; mirza_pro@b645db74 admin.php:1730, 3566, 3612, 4034; mirza_pro@b645db74 cronbot/croncard.php:43); `reject` = receipt rejected by admin (reason in dec_not_confirmed) (mirza_pro@b645db74 admin.php:1792, 1799, 7438); `expire` = Unpaid for >24h (cron) or plisio expiry; rows may be DELETED by admin cleanup (mirza_pro@b645db74 cronbot/payment_expire.php:13-21; mirza_pro@b645db74 cronbot/plisio.php:46; mirza_pro@b645db74 admin.php:5361)
- `Payment_Method`: `cart to cart` = card-to-card bank transfer with receipt photo (mirza_pro@b645db74 index.php:4694; botmirzapanel@92c0ed06 index.php:2265); `aqayepardakht` = IPG gateway (mirza_pro@b645db74 index.php:4772; botmirzapanel@92c0ed06 index.php:2042); `zarinpal` = Zarinpal IPG (called with currency IRT) (mirza_pro@b645db74 index.php:4832; mirza_pro@b645db74 function.php:2588); `variza` = Variza gateway (mirza_pro@b645db74 index.php:4892); `plisio` = crypto (price converted to USD) (mirza_pro@b645db74 index.php:4947; mirza_pro@b645db74 function.php:1530-1531); `nowpayment` = NOWPayments crypto (mirza_pro spelling) (mirza_pro@b645db74 index.php:5014); `Nowpayments` = NOWPayments (BMP spelling) (botmirzapanel@92c0ed06 index.php:2077); `Currency Rial 1` = Rial gateway slot 1 (iranpay1) (mirza_pro@b645db74 index.php:5080); `Currency Rial 2` = Rial gateway slot 2 (mirza_pro@b645db74 index.php:5137); `Currency Rial 3` = Rial gateway slot 3 (mirza_pro@b645db74 index.php:5280; mirza_pro@b645db74 cronbot/iranpay1.php:14); `Currency Rial gateway` = BMP Rial gateway (botmirzapanel@92c0ed06 index.php:2124); `AbanGateway` = iranpay4 slot (mirza_pro@b645db74 index.php:5213); `arze digital offline` = offline crypto with receipt (reviewed like card) (mirza_pro@b645db74 index.php:5352; mirza_pro@b645db74 cronbot/croncard.php:27); `Star Telegram` = Telegram Stars (mirza_pro@b645db74 index.php:5403); `add balance by admin` = admin credit; price positive; id_invoice NULL (mirza_pro@b645db74 admin.php:3567); `low balance by admin` = admin DEBIT; price stored POSITIVE, sign only implied by method (mirza_pro@b645db74 admin.php:3613, 3618)

Time columns: `time` = date('Y/m/d H:i:s') string, Gregorian, server zone Asia/Tehran (no offset stored) (mirza_pro@b645db74 index.php:4690; mirza_pro@b645db74 index.php:3 (date_default_timezone_set Asia/Tehran); compared as string in mirza_pro@b645db74 cronbot/payment_expire.php:13-15); `at_updated` = date('Y/m/d H:i:s') string, Gregorian, server zone Asia/Tehran (no offset stored) — set when the receipt is uploaded; auto-confirm timer measured from it (mirza_pro@b645db74 index.php:5620-5621; mirza_pro@b645db74 cronbot/croncard.php:31-38)

Money columns: `price` = toman (mirza_pro@b645db74 index.php:4812 + function.php:2588-2589 (Zarinpal amount=price with currency IRT); mirza_pro@b645db74 lang/fa.php:282 (chargedThanks shows price as تومان); card-to-card copy amount is price with "0" appended = Rial (mirza_pro@b645db74 index.php:4686))

Written/read by: mirza_pro@b645db74 index.php:4691-4695 (cart to cart, Unpaid); mirza_pro@b645db74 index.php:4770 (aqayepardakht); mirza_pro@b645db74 index.php:4830-4833 (zarinpal, dec_not_confirmed=authority); mirza_pro@b645db74 index.php:4890 (variza); mirza_pro@b645db74 index.php:4945 (plisio); mirza_pro@b645db74 index.php:5012 (nowpayment); mirza_pro@b645db74 index.php:5078 (Currency Rial 1); mirza_pro@b645db74 index.php:5135 (Currency Rial 2); mirza_pro@b645db74 index.php:5212 (AbanGateway); mirza_pro@b645db74 index.php:5278 (Currency Rial 3); mirza_pro@b645db74 index.php:5350 (arze digital offline); mirza_pro@b645db74 index.php:5401 (Star Telegram); mirza_pro@b645db74 admin.php:3565-3569 (add balance by admin, paid); mirza_pro@b645db74 admin.php:3611-3615 (low balance by admin, paid); mirza_pro@b645db74 vpnbot/Default/index.php:1292-1296 (sub-bot top-up, bottype=$ApiToken); mirza_pro@b645db74 index.php:5618-5621 (receipt uploaded → waiting); mirza_pro@b645db74 admin.php:1730 (admin confirm → paid); mirza_pro@b645db74 admin.php:1792 (admin reject); mirza_pro@b645db74 function.php:988-996 (claimPaymentPaid); mirza_pro@b645db74 cronbot/croncard.php:43 (auto-confirm); mirza_pro@b645db74 cronbot/payment_expire.php:15-21 (Unpaid >24h → expire); mirza_pro@b645db74 admin.php:5361 (DELETE expire rows); mirza_pro@b645db74 admin.php:7438 (bulk reject waiting cards); mirza_pro@b645db74 function.php:654 (message_id); botmirzapanel@92c0ed06 index.php:2041-2050, 2076-2090, 2123-2131, 2264-2277 (BMP inserts); botmirzapanel@92c0ed06 functions.php:219-228 (BMP DirectPayment)

Open questions:
- Deployed fork columns (operator): id,id_user,id_order,time,at_updated,price,dec_not_confirmed,Payment_Method,payment_Status,message_id,receipt_trace — it has NO invoice/id_invoice/bottype column, so the purpose (top-up vs direct purchase/renewal) cannot be derived from this table in the deployed fork. UNKNOWN where the fork stores it.
- receipt_trace: no public source declares it; meaning UNKNOWN (possibly a bank trace number of the card receipt — unproven).
- Deployed fork may use other Payment_Method / payment_Status spellings; enumerate DISTINCT values from the dump before mapping.
- Payment_report.price for a direct purchase is only the shortfall (price - wallet balance), not the invoice price (index.php:3925-3927).
- Manual add via addbalamceuser_ credits an admin-typed amount that can differ from Payment_report.price (admin.php:4021-4049).
- Wallet credits from gift codes, referral commission, join gift, cashback, wheel prizes, cancel refunds and failed-provision refunds are NOT recorded here; Payment_report cannot reconcile user.Balance.
- expire rows can be deleted by admin (admin.php:5361); history is incomplete.

### `Discount`

Aliases / variants: (possibly gift-code table of the fork — UNPROVEN)
Found in: `botmirzapanel@92c0ed06 table.php:426`; `mirza_pro@b645db74 db/tables/Discount.php:4`
Primary key: `id`

**Purpose.** Gift codes: redeeming a code credits a fixed amount to user.Balance (not a purchase discount).

**Class:** REVIEW (NEXA: SECRETS_MANUAL; holds secret columns (NEXA OQ-MZ-INV-02: a table holding any secret is SECRETS_MANUAL); may become ARCHIVE only with an owner-reviewed column allowlist that omits: code) — Unredeemed gift codes are a liability (bearer value). Operator must decide to re-issue in NEXA or expire; codes are bearer credentials.

**NEXA target:** no direct NEXA equivalent (NEXA discounts are order discounts: nexa-bot@9e604de apps/api/drizzle/0032_phase4_commerce.sql:35); proposal: archive + manual re-issue

| Column | Declared type(s) | Sensitivity | Notes |
|---|---|---|---|
| `id` | botmirzapanel: INT(6) UNSIGNED AUTO_INCREMENT PRIMARY KEY \| mirza_pro: INT(6) UNSIGNED AUTO_INCREMENT PRIMARY KEY | public |  |
| `code` | botmirzapanel: varchar(2000) NULL \| mirza_pro: varchar(2000) NULL | secret | bearer gift code redeemable for money |
| `price` | botmirzapanel: varchar(200) NULL \| mirza_pro: varchar(200) NULL | financial | credit amount |
| `limituse` | mirza_pro: varchar(200) NULL \| mirza_pro: VARCHAR(200) (added on upgrade; default null) | public | max redemptions |
| `limitused` | mirza_pro: varchar(200) NULL \| mirza_pro: VARCHAR(200) (added on upgrade; default null) | public | redemptions so far |

Relations:
- `code` → Giftcodeconsumed.code (mirza_pro@b645db74 index.php:5763, 5783)

Money columns: `price` = toman (mirza_pro@b645db74 lang/fa.php:461 (giftcodesuccess "%s تومان"); mirza_pro@b645db74 index.php:5777)

Written/read by: mirza_pro@b645db74 admin.php:2330 (create); mirza_pro@b645db74 index.php:5773-5786 (redeem: addBalance(price), limitused+1, Giftcodeconsumed row); mirza_pro@b645db74 api/discount.php:109

Open questions:
- Not in operator table list: does the deployed dump contain Discount at all?

### `Giftcodeconsumed`

Found in: `botmirzapanel@92c0ed06 table.php:444`; `mirza_pro@b645db74 db/tables/Giftcodeconsumed.php:4`
Primary key: `id`

**Purpose.** Redemption log for BOTH gift codes (Discount) and purchase discount codes (DiscountSell): one row per (user, code) use.

**Class:** ARCHIVE (NEXA: SECRETS_MANUAL; holds secret columns (NEXA OQ-MZ-INV-02: a table holding any secret is SECRETS_MANUAL); may become ARCHIVE only with an owner-reviewed column allowlist that omits: code) — Usage history only; no amounts.

**NEXA target:** new archive table proposal (legacy_code_redemptions)

| Column | Declared type(s) | Sensitivity | Notes |
|---|---|---|---|
| `id` | botmirzapanel: INT(6) UNSIGNED AUTO_INCREMENT PRIMARY KEY \| mirza_pro: INT(6) UNSIGNED AUTO_INCREMENT PRIMARY KEY | public |  |
| `code` | botmirzapanel: varchar(2000) NULL \| mirza_pro: varchar(2000) NULL | secret | bearer code |
| `id_user` | botmirzapanel: varchar(200) NULL \| mirza_pro: varchar(200) NULL | personal |  |

Relations:
- `id_user` → user.id (mirza_pro@b645db74 index.php:5784-5786)
- `code` → Discount.code OR DiscountSell.codeDiscount (ambiguous) (mirza_pro@b645db74 index.php:5783; mirza_pro@b645db74 index.php:3960)

Written/read by: mirza_pro@b645db74 index.php:5783; mirza_pro@b645db74 index.php:1830, 3960; mirza_pro@b645db74 function.php:1118, 1306

Open questions:
- No time column; cannot date redemptions.

### `textbot`

Found in: `botmirzapanel@92c0ed06 table.php:460`; `operator-report 2026-10-10`
Primary key: `id_text`

**Purpose.** Editable bot copy (id_text → text). BMP only in DDL; mirza_pro moved texts to lang files.

**Class:** CONFIGURATION (NEXA: OWNER_DECISION) — Copy may be reused by operator by hand; not schema-compatible with NEXA texts.

**NEXA target:** NEXA bot texts — manual

| Column | Declared type(s) | Sensitivity | Notes |
|---|---|---|---|
| `id_text` | botmirzapanel: varchar(600) PRIMARY KEY NOT NULL | public |  |
| `text` | botmirzapanel: TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL | public |  |

Written/read by: botmirzapanel@92c0ed06 table.php:460-500; botmirzapanel@92c0ed06 admin.php:340-431

Open questions:
- Present in deployed dump (operator) although mirza_pro does not declare it.

### `PaySetting`

Found in: `botmirzapanel@92c0ed06 table.php:511`; `mirza_pro@b645db74 db/tables/PaySetting.php:105`; `operator-report 2026-10-10`
Primary key: `NamePay`

**Purpose.** Key/value payment configuration: gateway on/off, API keys, merchant ids, card info, min/max amounts (JSON per tier), cashback percents.

**Class:** EXCLUDED (NEXA: SECRETS_MANUAL; holds secret columns (NEXA OQ-MZ-INV-02: a table holding any secret is SECRETS_MANUAL); names and counts only) — Mixed secret key/value table; only an allowlisted set of non-secret keys (limits, cashback %, on/off) may ever be exported as CONFIGURATION proposals.

**NEXA target:** NEXA payment_gateways / payment_gateway_credentials / payment_accounts (nexa-bot@9e604de apps/api/drizzle/0070_payment_gateways.sql:1; 0122_tonpays_gateway.sql:62; 0062_payment_accounts.sql:1) — manual; nexa-bot@9e604de docs/legacy-migration/table-inventory.md:116-118 (secrets are never imported; reconfigured by hand)

| Column | Declared type(s) | Sensitivity | Notes |
|---|---|---|---|
| `NamePay` | botmirzapanel: varchar(500) PRIMARY KEY NOT NULL \| mirza_pro: varchar(500) PRIMARY KEY NOT NULL | public | key |
| `ValuePay` | botmirzapanel: TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL \| mirza_pro: TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL | secret | value — secret for credential keys |

Vocabularies:
- `NamePay (SECRET keys)`: `cardnumber` = credential / payout destination (mirza_pro@b645db74 db/tables/PaySetting.php:10); `namecard` = credential / payout destination (mirza_pro@b645db74 db/tables/PaySetting.php:11); `CartDirect` = credential / payout destination (mirza_pro@b645db74 db/tables/PaySetting.php:9); `apinowpayment` = credential / payout destination (mirza_pro@b645db74 db/tables/PaySetting.php:13); `merchant_id_aqayepardakht` = credential / payout destination (mirza_pro@b645db74 db/tables/PaySetting.php:18); `marchent_tronseller` = credential / payout destination (mirza_pro@b645db74 db/tables/PaySetting.php:21); `walletaddress` = credential / payout destination (mirza_pro@b645db74 db/tables/PaySetting.php:22); `apiternado` = credential / payout destination (mirza_pro@b645db74 db/tables/PaySetting.php:24); `merchant_zarinpal` = credential / payout destination (mirza_pro@b645db74 db/tables/PaySetting.php:39); `apiiranpay` = credential / payout destination (mirza_pro@b645db74 db/tables/PaySetting.php:41); `marchent_floypay` = credential / payout destination (mirza_pro@b645db74 db/tables/PaySetting.php:46); `apiiranpay4` = credential / payout destination (mirza_pro@b645db74 db/tables/PaySetting.php:79); `endpointiranpay4` = credential / payout destination (mirza_pro@b645db74 db/tables/PaySetting.php:80); `variza_api_token` = credential / payout destination (mirza_pro@b645db74 db/tables/PaySetting.php:87); `variza_webhook_secret` = credential / payout destination (mirza_pro@b645db74 db/tables/PaySetting.php:88); `CartDescription` = card number (BMP) (botmirzapanel@92c0ed06 table.php:518)
- `NamePay (non-secret examples)`: `minbalance / maxbalance` = JSON per tier {f,n,n2} (mirza_pro@b645db74 db/migrations/005_paysetting_defaults.php:14-15); `chashback*` = per-gateway cashback percent (mirza_pro@b645db74 db/tables/PaySetting.php:27-36); `Exception_auto_cart` = JSON list of user ids excluded from auto-confirm (personal) (mirza_pro@b645db74 cronbot/croncard.php:20-35)

Money columns: `minbalance*/maxbalance*` = toman (mirza_pro@b645db74 index.php:4802-4806)

Written/read by: mirza_pro@b645db74 db/tables/PaySetting.php:7-110; mirza_pro@b645db74 db/migrations/005_paysetting_defaults.php; mirza_pro@b645db74 db/migrations/008_abangateway_gateway.php; botmirzapanel@92c0ed06 table.php:518-533

Open questions:
- Deployed fork may have extra keys; any unknown key must be treated as secret by default.

### `DiscountSell`

Aliases / variants: discount_sell (operator list; same-meaning UNPROVEN)
Found in: `botmirzapanel@92c0ed06 table.php:547`; `mirza_pro@b645db74 db/tables/DiscountSell.php:4`
Primary key: `id`

**Purpose.** Percent discount codes applied at purchase/renewal, scoped by tier, product, panel, first-purchase and per-user limits.

**Class:** REVIEW (NEXA: SECRETS_MANUAL; holds secret columns (NEXA OQ-MZ-INV-02: a table holding any secret is SECRETS_MANUAL); may become ARCHIVE only with an owner-reviewed column allowlist that omits: codeDiscount) — Active promotional codes; operator decides whether to recreate in NEXA discounts.

**NEXA target:** NEXA discounts / discount_redemptions (nexa-bot@9e604de apps/api/drizzle/0032_phase4_commerce.sql:22,35) — manual recreation only

| Column | Declared type(s) | Sensitivity | Notes |
|---|---|---|---|
| `id` | botmirzapanel: INT(6) UNSIGNED AUTO_INCREMENT PRIMARY KEY \| mirza_pro: INT(6) UNSIGNED AUTO_INCREMENT PRIMARY KEY | public |  |
| `codeDiscount` | botmirzapanel: varchar(1000)  NOT NULL \| mirza_pro: varchar(1000) NOT NULL | secret | bearer discount code |
| `price` | botmirzapanel: varchar(200)  NOT NULL \| mirza_pro: varchar(200) NOT NULL | public | percent |
| `limitDiscount` | botmirzapanel: varchar(500)  NOT NULL \| mirza_pro: varchar(500) NOT NULL | public | max uses |
| `usedDiscount` | botmirzapanel: varchar(500) NOT NULL \| mirza_pro: varchar(500) NOT NULL | public | uses so far |
| `usefirst` | botmirzapanel: varchar(500) NOT NULL \| botmirzapanel: VARCHAR(500) (added by ALTER) \| mirza_pro: varchar(100) NOT NULL \| mirza_pro: VARCHAR(100) (added on up | public |  |
| `agent` | mirza_pro: varchar(500) NOT NULL \| mirza_pro: VARCHAR(100) (added on upgrade; default null) | public |  |
| `useuser` | mirza_pro: varchar(100) NOT NULL \| mirza_pro: VARCHAR(100) (added on upgrade; default null) | public | per-user max |
| `code_product` | mirza_pro: varchar(100) NOT NULL \| mirza_pro: VARCHAR(100) (added on upgrade; default null) | public |  |
| `code_panel` | mirza_pro: varchar(100) NOT NULL \| mirza_pro: VARCHAR(100) (added on upgrade; default null) | public |  |
| `time` | mirza_pro: varchar(100) NOT NULL \| mirza_pro: VARCHAR(100) (added on upgrade; default null) | public | expiry |
| `type` | mirza_pro: varchar(100) NOT NULL \| mirza_pro: VARCHAR(100) (added on upgrade; default null) | public |  |

Relations:
- `code_product` → product.code_product or 'all' (mirza_pro@b645db74 index.php:4169)
- `code_panel` → marzban_panel.code_panel or '/all' (mirza_pro@b645db74 index.php:4169)
- `codeDiscount` → Giftcodeconsumed.code (usage log shared with gift codes) (mirza_pro@b645db74 index.php:3960; mirza_pro@b645db74 function.php:1118)

Vocabularies:
- `type`: `all` = buy and extend (mirza_pro@b645db74 index.php:1643); `buy` = new purchase only (mirza_pro@b645db74 index.php:4169); `extend` = renewal only (mirza_pro@b645db74 index.php:1643)
- `agent`: `f|n|n2|allusers` = tier scope (mirza_pro@b645db74 index.php:4169)
- `usefirst`: `1` = first purchase only (mirza_pro@b645db74 index.php:4199)

Time columns: `time` = unix seconds expiry; 0 = no expiry (mirza_pro@b645db74 index.php:4185)

Money columns: `price` = percent (not money) (mirza_pro@b645db74 index.php:4227 (price/100*price_product))

Written/read by: mirza_pro@b645db74 api/discount.php:248-300; mirza_pro@b645db74 index.php:1639-1673 (apply on extend); mirza_pro@b645db74 index.php:4165-4233 (apply on buy); mirza_pro@b645db74 index.php:3957-3962 (usedDiscount+1, Giftcodeconsumed row)

Open questions:
- Operator list has discount_sell/discount_sell_used, not DiscountSell: renamed table in the fork? columns unknown.

### `affiliates`

Found in: `botmirzapanel@92c0ed06 table.php:573`; `mirza_pro@b645db74 db/tables/affiliates.php:4`; `operator-report 2026-10-10`
Primary key: `none declared`

**Purpose.** Single-row referral program configuration (commission on/off, join gift amount, first-buy-only commission, banner media).

**Class:** CONFIGURATION (NEXA: OWNER_DECISION) — Program settings only; percentage itself lives in setting.affiliatespercentage.

**NEXA target:** NEXA referral settings / referral_signup_gifts (nexa-bot@9e604de apps/api/drizzle/0119_customer_ux_completion.sql:27) — manual reconfiguration

| Column | Declared type(s) | Sensitivity | Notes |
|---|---|---|---|
| `description` | botmirzapanel: TEXT  CHARACTER SET utf8mb4 COLLATE utf8mb4_bin  NULL \| mirza_pro: TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL | public | banner text |
| `status_commission` | botmirzapanel: varchar(200)  CHARACTER SET utf8mb4 COLLATE utf8mb4_bin  NULL \| mirza_pro: varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL \| | public |  |
| `Discount` | botmirzapanel: varchar(200)  CHARACTER SET utf8mb4 COLLATE utf8mb4_bin  NULL \| mirza_pro: varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL \| | public |  |
| `price_Discount` | botmirzapanel: varchar(200)  CHARACTER SET utf8mb4 COLLATE utf8mb4_bin  NULL \| mirza_pro: varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL \| | financial |  |
| `id_media` | botmirzapanel: varchar(300) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin  NULL \| mirza_pro: varchar(300) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL | public | Telegram file_id |
| `affiliatesstatus` | botmirzapanel: varchar(600)  NULL | public | BMP only |
| `affiliatespercentage` | botmirzapanel: varchar(600)  NULL | public | BMP only |
| `porsant_one_buy` | mirza_pro: varchar(100) \| mirza_pro: VARCHAR(100) (added on upgrade; default 'off_buy_porsant') | public |  |

Vocabularies:
- `status_commission`: `oncommission` = commission enabled (mirza_pro@b645db74 function.php:1138)
- `porsant_one_buy`: `on_buy_porsant|off_buy_porsant` = commission only on first purchase (mirza_pro@b645db74 function.php:1139)
- `Discount`: `onDiscountaffiliates` = join gift enabled (mirza_pro@b645db74 db/tables/affiliates.php:17)

Money columns: `price_Discount` = toman; split half to referrer, half to referee on join-gift claim (mirza_pro@b645db74 index.php:5866-5872)

Written/read by: mirza_pro@b645db74 db/tables/affiliates.php:12-20 (seed); botmirzapanel@92c0ed06 table.php:585 (seed)

### `cancel_service`

Found in: `botmirzapanel@92c0ed06 table.php:597`; `mirza_pro@b645db74 db/tables/cancel_service.php:4`; `operator-report 2026-10-10`
Primary key: `id`

**Purpose.** Customer request to delete a service with refund; admin accepts (refund to wallet, invoice removed) or rejects with reason.

**Class:** ARCHIVE (NEXA: ARCHIVE) — History of refunds that changed user.Balance outside Payment_report; open (waiting) rows need operator decision → REVIEW items.

**NEXA target:** archive; open requests → review queue (NEXA service_refund_requests nexa-bot@9e604de apps/api/drizzle/0125_wp19_service_refund_requests.sql:22 is the live analogue; do not auto-create)

| Column | Declared type(s) | Sensitivity | Notes |
|---|---|---|---|
| `id` | botmirzapanel: INT(6) UNSIGNED AUTO_INCREMENT PRIMARY KEY \| mirza_pro: INT(6) UNSIGNED AUTO_INCREMENT PRIMARY KEY | public |  |
| `id_user` | botmirzapanel: varchar(500)  NOT NULL \| mirza_pro: varchar(500) NOT NULL | personal |  |
| `username` | botmirzapanel: varchar(1000)  NOT NULL \| mirza_pro: varchar(1000) NOT NULL | personal | panel username |
| `description` | botmirzapanel: TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin  NOT NULL \| mirza_pro: TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL | personal | admin reason or "0" |
| `status` | botmirzapanel: varchar(1000)  NOT NULL \| mirza_pro: varchar(1000) NOT NULL | public |  |

Relations:
- `id_user` → user.id (mirza_pro@b645db74 index.php:2768)
- `username` → invoice.username (mirza_pro@b645db74 admin.php:4657)

Vocabularies:
- `status`: `waiting` = open request (mirza_pro@b645db74 index.php:2767); `reject` = rejected; description = admin reason (mirza_pro@b645db74 admin.php:4673); `accept` = accepted; refund credited to Balance (no Payment_report row) (mirza_pro@b645db74 admin.php:4748, 4777)

Written/read by: mirza_pro@b645db74 index.php:2765-2768 (status waiting, description "0"); mirza_pro@b645db74 admin.php:4673-4674 (reject + reason); mirza_pro@b645db74 admin.php:4741-4749 (accept: Balance += refund, invoice Status removedbyadmin); mirza_pro@b645db74 admin.php:4777-4778; botmirzapanel@92c0ed06 index.php:1327

Open questions:
- No time column and no refund amount column: refund amount is not stored anywhere except the Telegram report message.

### `category`

Found in: `botmirzapanel@92c0ed06 table.php:619`; `mirza_pro@b645db74 db/tables/category.php:5`; `operator-report 2026-10-10`
Rows (operator report): 7
Primary key: `id`

**Purpose.** Product categories (label only).

**Class:** OPERATIONAL (NEXA: SUPPORTED) — Trivial, non-sensitive; can seed NEXA product categories for reviewed products.

**NEXA target:** NEXA product_categories (nexa-bot@9e604de apps/api/drizzle/0097_product_categories.sql:1)

| Column | Declared type(s) | Sensitivity | Notes |
|---|---|---|---|
| `id` | botmirzapanel: INT(6) UNSIGNED AUTO_INCREMENT PRIMARY KEY \| mirza_pro: INT(6) UNSIGNED AUTO_INCREMENT PRIMARY KEY | public |  |
| `remark` | botmirzapanel: varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin  NOT NULL \| mirza_pro: varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL | public | category name |

Relations:
- `remark` → product.category (by text) (mirza_pro@b645db74 index.php:3594-3602)

Written/read by: mirza_pro@b645db74 admin.php:9057; mirza_pro@b645db74 api/category.php:97

### `Requestagent`

Aliases / variants: requestagent (operator list lists BOTH spellings; on Linux MySQL these are two different tables)
Found in: `mirza_pro@b645db74 db/tables/Requestagent.php:5`; `operator-report 2026-10-10`
Primary key: `id`

**Purpose.** Requests to become a reseller (agent); fee charged from wallet (setting.agentreqprice).

**Class:** ARCHIVE (NEXA: ARCHIVE) — History of reseller onboarding; current tier lives in user.agent.

**NEXA target:** NEXA reseller_tier_grants (nexa-bot@9e604de apps/api/drizzle/0110_wp9_resellers.sql:29) — archive only

| Column | Declared type(s) | Sensitivity | Notes |
|---|---|---|---|
| `id` | mirza_pro: varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci PRIMARY KEY | personal | Telegram id |
| `username` | mirza_pro: varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL | personal |  |
| `time` | mirza_pro: varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL | public |  |
| `Description` | mirza_pro: varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL | personal | free text |
| `status` | mirza_pro: varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL | public |  |
| `type` | mirza_pro: varchar(500) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL | public |  |

Relations:
- `id` → user.id (mirza_pro@b645db74 index.php:6120)

Vocabularies:
- `status`: `waiting` = open (mirza_pro@b645db74 index.php:6113); `accept` = approved (mirza_pro@b645db74 admin.php:5142); `reject` = rejected (mirza_pro@b645db74 admin.php:5094)
- `type`: `None` = not yet assigned (mirza_pro@b645db74 index.php:6114); `n|n2` = granted tier (mirza_pro@b645db74 admin.php:5138-5143, 5181-5191)

Time columns: `time` = time() unix seconds (mirza_pro@b645db74 index.php:6115)

Written/read by: mirza_pro@b645db74 index.php:6105-6125; mirza_pro@b645db74 admin.php:5094 (reject); mirza_pro@b645db74 admin.php:5142-5143 (accept + type); mirza_pro@b645db74 admin.php:5191; mirza_pro@b645db74 admin.php:4123 (DELETE)

Open questions:
- Why does the dump hold both Requestagent and requestagent? Compare columns/rows.

### `app`

Found in: `mirza_pro@b645db74 db/tables/app.php:4`
Primary key: `id`

**Purpose.** Client app download links.

**Class:** CONFIGURATION (NEXA: OWNER_DECISION) — Public links.

**NEXA target:** NEXA tutorials/settings — manual

| Column | Declared type(s) | Sensitivity | Notes |
|---|---|---|---|
| `id` | mirza_pro: INT(6) UNSIGNED AUTO_INCREMENT PRIMARY KEY | public |  |
| `name` | mirza_pro: varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL | public |  |
| `link` | mirza_pro: varchar(200) NOT NULL | public |  |

Written/read by: mirza_pro@b645db74 admin.php:8176

Open questions:
- Not in operator list.

### `botsaz`

Found in: `mirza_pro@b645db74 db/tables/botsaz.php:4`
Primary key: `id`

**Purpose.** Reseller sub-bots ("bot builder"): bot token, owner, admins, per-bot settings.

**Class:** EXCLUDED (NEXA: SECRETS_MANUAL; holds secret columns (NEXA OQ-MZ-INV-02: a table holding any secret is SECRETS_MANUAL); names and counts only) — Bot tokens and webhook secrets; NEXA re-creates bots by hand.

**NEXA target:** NEXA bot_instances (nexa-bot@9e604de apps/api/drizzle/0000_foundation.sql:31) — manual; nexa-bot@9e604de docs/legacy-migration/table-inventory.md:116-118 (secrets are never imported; reconfigured by hand)

| Column | Declared type(s) | Sensitivity | Notes |
|---|---|---|---|
| `id` | mirza_pro: INT(6) UNSIGNED AUTO_INCREMENT PRIMARY KEY | public |  |
| `id_user` | mirza_pro: varchar(200) NOT NULL | personal |  |
| `bot_token` | mirza_pro: varchar(200) NOT NULL | secret | Telegram bot token |
| `admin_ids` | mirza_pro: TEXT NOT NULL | personal |  |
| `username` | mirza_pro: varchar(200) NOT NULL | public | bot username |
| `setting` | mirza_pro: TEXT NULL | secret | JSON incl. cart_info/card text |
| `hide_panel` | mirza_pro: JSON NOT NULL \| mirza_pro: JSON (added on upgrade; default '{}') | public |  |
| `webhook_secret` | mirza_pro: varchar(200) NOT NULL DEFAULT '' \| mirza_pro: VARCHAR(200) NOT NULL DEFAULT '' (added on upgrade; default null) | secret |  |
| `time` | mirza_pro: varchar(200) NOT NULL | public |  |

Relations:
- `id_user` → user.id (reseller) (mirza_pro@b645db74 admin.php:8086)
- `bot_token` → user.bottype, invoice.bottype, Payment_report.bottype (mirza_pro@b645db74 vpnbot/Default/index.php:27, 66, 1296)

Time columns: `time` = date('Y/m/d H:i:s') string, Gregorian, server zone Asia/Tehran (no offset stored) (mirza_pro@b645db74 admin.php:8090)

Written/read by: mirza_pro@b645db74 admin.php:8085-8095; mirza_pro@b645db74 api/users.php:656

Open questions:
- Not in operator list; does the deployed dump contain it?

### `card_number`

Found in: `mirza_pro@b645db74 db/tables/card_number.php:5`; `operator-report 2026-10-10`
Primary key: `cardnumber`

**Purpose.** Bank card numbers and holder names shown to customers for card-to-card payment (one chosen at random).

**Class:** EXCLUDED (NEXA: SECRETS_MANUAL; holds secret columns (NEXA OQ-MZ-INV-02: a table holding any secret is SECRETS_MANUAL); names and counts only) — Card numbers are SECRET; re-entered by hand in NEXA payment accounts.

**NEXA target:** NEXA payment_accounts/payment_destinations (nexa-bot@9e604de apps/api/drizzle/0062_payment_accounts.sql:1,24) — manual; nexa-bot@9e604de docs/legacy-migration/table-inventory.md:116-118 (secrets are never imported; reconfigured by hand)

| Column | Declared type(s) | Sensitivity | Notes |
|---|---|---|---|
| `cardnumber` | mirza_pro: varchar(500) PRIMARY KEY | secret | bank card number |
| `namecard` | mirza_pro: varchar(1000) NOT NULL | personal | card holder name |

Written/read by: mirza_pro@b645db74 admin.php:2421; mirza_pro@b645db74 index.php:4660-4677 (ORDER BY RAND())

### `departman`

Found in: `mirza_pro@b645db74 db/tables/departman.php:6`; `operator-report 2026-10-10`
Primary key: `id`; unique: `(idsupport, name_departman) via mirza_pro db/indexes.php:19`

**Purpose.** Support departments and which admin receives them.

**Class:** CONFIGURATION (NEXA: OWNER_DECISION) — Small config list.

**NEXA target:** NEXA ticket_categories (nexa-bot@9e604de apps/api/drizzle/0135_wp_a7_support_tickets.sql:16) — manual

| Column | Declared type(s) | Sensitivity | Notes |
|---|---|---|---|
| `id` | mirza_pro: INT(6) UNSIGNED AUTO_INCREMENT PRIMARY KEY | public |  |
| `idsupport` | mirza_pro: VARCHAR(200) NOT NULL | personal | admin Telegram id |
| `name_departman` | mirza_pro: VARCHAR(600) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL | public |  |

Relations:
- `idsupport` → admin.id_admin (mirza_pro@b645db74 db/tables/departman.php:13)

Written/read by: mirza_pro@b645db74 admin.php:6596; mirza_pro@b645db74 db/tables/departman.php:11-16 (seed); mirza_pro@b645db74 db/migrations/007_departman_dedupe.php

### `logs_api`

Found in: `mirza_pro@b645db74 db/tables/logs_api.php:4`; `operator-report 2026-10-10`
Primary key: `id`

**Purpose.** Web API request log (headers minus token/authorization/cookie, body JSON, IP).

**Class:** EXCLUDED (NEXA: SECRETS_MANUAL; holds secret columns (NEXA OQ-MZ-INV-02: a table holding any secret is SECRETS_MANUAL); names and counts only) — Operational log with IPs and request bodies (may include tokens in body or other headers).

**NEXA target:** none

| Column | Declared type(s) | Sensitivity | Notes |
|---|---|---|---|
| `id` | mirza_pro: INT(6) UNSIGNED AUTO_INCREMENT PRIMARY KEY | public |  |
| `header` | mirza_pro: JSON NULL | secret | request headers (other auth headers may survive) |
| `data` | mirza_pro: JSON NULL | secret | request body |
| `ip` | mirza_pro: varchar(200) NOT NULL | personal |  |
| `time` | mirza_pro: varchar(200) NOT NULL | public |  |
| `actions` | mirza_pro: varchar(200) NOT NULL | public |  |

Time columns: `time` = date('Y/m/d H:i:s') string, Gregorian, server zone Asia/Tehran (no offset stored) (mirza_pro@b645db74 api/utils.php:177)

Written/read by: mirza_pro@b645db74 api/utils.php:167-180

### `manualsell`

Found in: `mirza_pro@b645db74 db/tables/manualsell.php:4`; `operator-report 2026-10-10`
Primary key: `id`

**Purpose.** Stock of pre-made configs for "Manualsale" panels; one row per config, status active→selled→delete.

**Class:** EXCLUDED (NEXA: SECRETS_MANUAL; holds secret columns (NEXA OQ-MZ-INV-02: a table holding any secret is SECRETS_MANUAL); names and counts only) — contentrecord is the config itself (credential). Unsold stock count may go to REVIEW as numbers only.

**NEXA target:** none (manual)

| Column | Declared type(s) | Sensitivity | Notes |
|---|---|---|---|
| `id` | mirza_pro: INT(6) UNSIGNED AUTO_INCREMENT PRIMARY KEY | public |  |
| `codepanel` | mirza_pro: varchar(100) NOT NULL | public |  |
| `codeproduct` | mirza_pro: varchar(100) NOT NULL | public |  |
| `namerecord` | mirza_pro: varchar(200) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL | public |  |
| `username` | mirza_pro: varchar(500) NULL | personal |  |
| `contentrecord` | mirza_pro: TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL | secret | config/credential |
| `status` | mirza_pro: varchar(200) NOT NULL | public |  |

Relations:
- `codepanel` → marzban_panel.code_panel (mirza_pro@b645db74 admin.php:5934)
- `codeproduct` → product (name/code as stored by admin flow) (mirza_pro@b645db74 admin.php:5938)
- `username` → invoice.username (mirza_pro@b645db74 panels/Drivers/Manualsale.php:56)

Vocabularies:
- `status`: `active` = in stock (mirza_pro@b645db74 admin.php:5930); `selled` = sold (mirza_pro@b645db74 panels/Drivers/Manualsale.php:21); `delete` = removed with service (mirza_pro@b645db74 panels/Drivers/Manualsale.php:56)

Written/read by: mirza_pro@b645db74 admin.php:5933; mirza_pro@b645db74 panels/Drivers/Manualsale.php:21, 56

### `reagent_report`

Found in: `mirza_pro@b645db74 db/tables/reagent_report.php:5`; `operator-report 2026-10-10`
Primary key: `id`; unique: `user_id`

**Purpose.** Referral join record: who referred whom, and whether the join gift was claimed.

**Class:** ARCHIVE (NEXA: ARCHIVE) — Referral graph history; NEXA does not import referrals in Phase 1 (sql-evidence Q3).

**NEXA target:** NEXA referrals (nexa-bot@9e604de apps/api/drizzle/0032_phase4_commerce.sql:185) — owner decision; default archive

| Column | Declared type(s) | Sensitivity | Notes |
|---|---|---|---|
| `id` | mirza_pro: INT(6) UNSIGNED AUTO_INCREMENT PRIMARY KEY | public |  |
| `user_id` | mirza_pro: BIGINT UNIQUE NOT NULL | personal |  |
| `get_gift` | mirza_pro: BOOL NOT NULL | public |  |
| `time` | mirza_pro: varchar(50) NOT NULL | public |  |
| `reagent` | mirza_pro: varchar(30) NOT NULL | personal |  |

Relations:
- `user_id` → user.id (referee) (mirza_pro@b645db74 index.php:238)
- `reagent` → user.id (referrer) (mirza_pro@b645db74 index.php:238, 5866)

Vocabularies:
- `get_gift`: `0/1` = join gift claimed (mirza_pro@b645db74 index.php:5859)

Time columns: `time` = date('Y/m/d H:i:s') string, Gregorian, server zone Asia/Tehran (no offset stored) (mirza_pro@b645db74 index.php:236)

Written/read by: mirza_pro@b645db74 index.php:235-238 (insert on /start with referral); mirza_pro@b645db74 index.php:5859 (get_gift 0→1 on claim)

Open questions:
- Must agree with user.affiliates; mismatches → review.

### `service_other`

Found in: `mirza_pro@b645db74 db/tables/service_other.php:4`; `operator-report 2026-10-10`
Rows (operator report): 78645
Primary key: `id`

**Purpose.** Secondary service-operation log keyed by panel username: renewals (extend_user), admin renewals, extra volume / extra time purchases, location changes, ownership transfers, cron gifts. Carries the price charged and a JSON snapshot of before/after panel state. Not linked to Payment_report by a column; a direct-pay renewal row is matched through value.id_order.

**Class:** ARCHIVE (NEXA: SECRETS_MANUAL; holds secret columns (NEXA OQ-MZ-INV-02: a table holding any secret is SECRETS_MANUAL); may become ARCHIVE only with an owner-reviewed column allowlist that omits: output) — Financial/service history (renewals, add-ons) with prices; not needed to operate adopted services, but required for revenue history and dispute evidence. Unpaid extend rows are abandoned attempts.

**NEXA target:** new archive table proposal (legacy_service_operation_archive); analogous live NEXA concepts service_commercial_actions/service_addons (nexa-bot@9e604de apps/api/drizzle/0047_commercial_actions.sql:1,25), service_location_changes (0133:20), service_ownership_transfers (0130:23) — do not replay.

| Column | Declared type(s) | Sensitivity | Notes |
|---|---|---|---|
| `id` | mirza_pro: INT(6) UNSIGNED AUTO_INCREMENT PRIMARY KEY | public |  |
| `id_user` | mirza_pro: varchar(500) NOT NULL | personal | Telegram id (or admin id) |
| `username` | mirza_pro: varchar(1000) NOT NULL | personal | panel username |
| `value` | mirza_pro: varchar(1000) NOT NULL | personal | JSON or scalar operation payload (volumes, expiry, product code, new owner id) |
| `time` | mirza_pro: varchar(200) NOT NULL | public |  |
| `price` | mirza_pro: varchar(200) NOT NULL \| mirza_pro: VARCHAR(200) (added on upgrade; default null) | financial |  |
| `type` | mirza_pro: varchar(1000) NOT NULL | public |  |
| `status` | mirza_pro: varchar(200) NOT NULL \| mirza_pro: VARCHAR(200) (added on upgrade; default null) | public |  |
| `output` | mirza_pro: TEXT NOT NULL \| mirza_pro: TEXT (added on upgrade; default null) | secret | raw panel API response JSON (may include subscription_url/links) |

Relations:
- `username` → invoice.username (panel account) (mirza_pro@b645db74 index.php:928; mirza_pro@b645db74 function.php:1244-1250; mirza_pro@b645db74 db/indexes.php:16)
- `id_user` → user.id — EXCEPT extend_user_by_admin from Telegram admin where it is the admin id (mirza_pro@b645db74 admin.php:7643-7650)
- `value.id_order (JSON)` → Payment_report.id_order / random order id for direct-pay renewals (mirza_pro@b645db74 index.php:1792-1806; mirza_pro@b645db74 function.php:1244-1250)

Vocabularies:
- `type`: `extend_user` = customer renewal (mirza_pro@b645db74 index.php:1802, 1876; mirza_pro@b645db74 admin.php:4367); `extend_user_by_admin` = renewal by admin (Telegram or web API) (mirza_pro@b645db74 admin.php:7645; mirza_pro@b645db74 api/invoice.php:311); `extra_user` = extra volume purchase (mirza_pro@b645db74 index.php:2124; mirza_pro@b645db74 function.php:1380); `extra_time_user` = extra time purchase (mirza_pro@b645db74 index.php:2669; mirza_pro@b645db74 function.php:1452); `extra_not_user` = extra volume on a service flow "not user" (label missing in admin map) (mirza_pro@b645db74 index.php:5982); `extends_not_user` = renewal 'due to user not being in the list' (mirza_pro@b645db74 index.php:6717; mirza_pro@b645db74 lang/en.php:2244); `change_location` = service moved to another panel; value JSON old_panel/new_panel/volume/used_traffic/expire (mirza_pro@b645db74 index.php:2306-2320); `transfertouser` = ownership transfer; value = new owner Telegram id (mirza_pro@b645db74 index.php:2896-2899); `gift_volume` = cron gift volume (price 0) (mirza_pro@b645db74 cronbot/gift.php:72-76); `gift_time` = cron gift time (price 0) (mirza_pro@b645db74 cronbot/gift.php:121-125)
- `status`: `unpaid` = renewal awaiting direct payment (mirza_pro@b645db74 index.php:1803); `paid` = renewal completed (mirza_pro@b645db74 index.php:1877; mirza_pro@b645db74 function.php:1300); `NULL` = rows inserted without status (extra/transfer/gift); reports treat NULL as paid (mirza_pro@b645db74 api/users.php:111; mirza_pro@b645db74 admin.php:4360)

Time columns: `time` = date('Y/m/d H:i:s') string, Gregorian, server zone Asia/Tehran (no offset stored) (mirza_pro@b645db74 index.php:1867; mirza_pro@b645db74 index.php:3 (date_default_timezone_set Asia/Tehran); NOTE admin.php:411 compares it to NOW() (string vs datetime))

Money columns: `price` = toman (same unit as product price); for direct-pay extra volume/time it is the Payment_report shortfall, not the full price (mirza_pro@b645db74 index.php:1879 (product price_product); mirza_pro@b645db74 function.php:1402 (Payment_report.price))

Written/read by: mirza_pro@b645db74 index.php:1792-1806 (extend_user, status=unpaid, before direct payment); mirza_pro@b645db74 index.php:1866-1879 (extend_user paid from wallet); mirza_pro@b645db74 function.php:1299-1300 (direct-pay renewal → status paid); mirza_pro@b645db74 index.php:2121-2124 (extra_user); mirza_pro@b645db74 function.php:1396-1404 (extra_user after direct pay; price = Payment_report.price); mirza_pro@b645db74 index.php:2666-2669 (extra_time_user); mirza_pro@b645db74 function.php:1468-1476; mirza_pro@b645db74 index.php:2313-2320 (change_location); mirza_pro@b645db74 index.php:2896-2899 (transfertouser); mirza_pro@b645db74 index.php:5979-5982 (extra_not_user); mirza_pro@b645db74 index.php:6708-6717 (extends_not_user); mirza_pro@b645db74 admin.php:7643-7650 (extend_user_by_admin; id_user = ADMIN id); mirza_pro@b645db74 api/invoice.php:306-311 (extend_user_by_admin via web API); mirza_pro@b645db74 cronbot/gift.php:73-76 (gift_volume), 122-125 (gift_time); mirza_pro@b645db74 vpnbot/Default/index.php:1842-1856

Open questions:
- Deployed fork rows=78,645; verify column set and DISTINCT type/status values from the dump.
- output column holds raw panel API responses (may contain subscription URLs / UUIDs) → treat as SECRET.
- extend_user_by_admin rows carry the admin id in id_user → orphan/ownership ambiguity.

### `shopSetting`

Found in: `mirza_pro@b645db74 db/tables/shopSetting.php:31`; `operator-report 2026-10-10`
Primary key: `Namevalue`

**Purpose.** Key/value shop settings: custom volume/time unit prices per tier, extra/time-extra toggles, direct-buy toggle, renewal cashback.

**Class:** CONFIGURATION (NEXA: OWNER_DECISION) — Non-secret pricing/feature config; owner review.

**NEXA target:** NEXA cashback_rules / custom_service_price_rules (nexa-bot@9e604de apps/api/drizzle/0105_wp8_pricing_discounts_cashback.sql:19; 0129_package_d_custom_service.sql:21) — manual

| Column | Declared type(s) | Sensitivity | Notes |
|---|---|---|---|
| `Namevalue` | mirza_pro: varchar(500) PRIMARY KEY NOT NULL | public | key |
| `value` | mirza_pro: TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL | public | value |

Vocabularies:
- `Namevalue`: `statusdirectpabuy` = ondirectbuy/offdirectbuy: allow paying shortfall at purchase (mirza_pro@b645db74 db/tables/shopSetting.php:14; mirza_pro@b645db74 index.php:3928); `chashbackextend / chashbackextend_agent` = renewal cashback percent (JSON per n/n2) (mirza_pro@b645db74 db/tables/shopSetting.php:21-22; mirza_pro@b645db74 index.php:1811-1814)

Money columns: `customvolmef/n/n2, customtimepricef/n/n2` = toman per GB / per day (inferred from price formula) (mirza_pro@b645db74 index.php:5557)

Written/read by: mirza_pro@b645db74 db/tables/shopSetting.php:5-35

### `support_message`

Found in: `mirza_pro@b645db74 db/tables/support_message.php:4`; `operator-report 2026-10-10`
Primary key: `id`

**Purpose.** Support ticket messages: each customer message (and follow-up) is a row with a tracking code, department, admin answer in result.

**Class:** ARCHIVE (NEXA: ARCHIVE) — Chat text is personal data; keep read-only history only if owner wants it. Not migrated into live tickets.

**NEXA target:** NEXA tickets/ticket_messages (nexa-bot@9e604de apps/api/drizzle/0135_wp_a7_support_tickets.sql:35,79) — owner decision; default archive

| Column | Declared type(s) | Sensitivity | Notes |
|---|---|---|---|
| `id` | mirza_pro: INT(6) UNSIGNED AUTO_INCREMENT PRIMARY KEY | public |  |
| `Tracking` | mirza_pro: VARCHAR(100) NOT NULL | public | 8-hex |
| `idsupport` | mirza_pro: VARCHAR(100) NOT NULL | personal | admin Telegram id |
| `iduser` | mirza_pro: VARCHAR(100) NOT NULL | personal |  |
| `name_departman` | mirza_pro: VARCHAR(600) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL | public |  |
| `text` | mirza_pro: TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL | personal | customer text |
| `result` | mirza_pro: TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL \| mirza_pro: TEXT (added on upgrade; default '0') | personal | admin answer |
| `time` | mirza_pro: VARCHAR(200) NOT NULL | public |  |
| `status` | mirza_pro: ENUM('Answered','Pending','Unseen','Customerresponse','close') CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL | public |  |

Relations:
- `iduser` → user.id (mirza_pro@b645db74 index.php:3227)
- `idsupport` → admin.id_admin via departman.idsupport (mirza_pro@b645db74 index.php:3226)
- `name_departman` → departman.name_departman (by text) (mirza_pro@b645db74 index.php:3228)

Vocabularies:
- `status`: `Unseen` = new (mirza_pro@b645db74 index.php:3224); `Answered` = admin answered (mirza_pro@b645db74 index.php:3264); `Customerresponse` = customer follow-up (mirza_pro@b645db74 index.php:3290); `Pending / close` = declared in ENUM; no writer found in public code (mirza_pro@b645db74 db/tables/support_message.php:13)

Time columns: `time` = date('Y/m/d H:i:s') string, Gregorian, server zone Asia/Tehran (no offset stored) (mirza_pro@b645db74 index.php:3219)

Written/read by: mirza_pro@b645db74 index.php:3223-3231 (new, status Unseen); mirza_pro@b645db74 index.php:3264-3265 (Answered + result); mirza_pro@b645db74 index.php:3289-3297 (Customerresponse)

Open questions:
- Retention/consent for chat text.

### `topicid`

Found in: `mirza_pro@b645db74 db/tables/topicid.php:22`; `operator-report 2026-10-10`
Primary key: `report`

**Purpose.** Mapping report kind → Telegram forum topic id in the report group.

**Class:** EXCLUDED (NEXA: OWNER_DECISION) — Bound to the old Telegram group; NEXA has its own ops log topics.

**NEXA target:** NEXA ops_log_topics (nexa-bot@9e604de apps/api/drizzle/0136_wpa4_ops_log_group.sql:40) — manual

| Column | Declared type(s) | Sensitivity | Notes |
|---|---|---|---|
| `report` | mirza_pro: varchar(500) PRIMARY KEY NOT NULL | public |  |
| `idreport` | mirza_pro: TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL | public | topic id |

Vocabularies:
- `report`: `buyreport` = report topic (mirza_pro@b645db74 db/tables/topicid.php:4-13); `otherservice` = report topic (mirza_pro@b645db74 db/tables/topicid.php:4-13); `paymentreport` = report topic (mirza_pro@b645db74 db/tables/topicid.php:4-13); `otherreport` = report topic (mirza_pro@b645db74 db/tables/topicid.php:4-13); `reporttest` = report topic (mirza_pro@b645db74 db/tables/topicid.php:4-13); `errorreport` = report topic (mirza_pro@b645db74 db/tables/topicid.php:4-13); `porsantreport` = report topic (mirza_pro@b645db74 db/tables/topicid.php:4-13); `reportnight` = report topic (mirza_pro@b645db74 db/tables/topicid.php:4-13); `reportcron` = report topic (mirza_pro@b645db74 db/tables/topicid.php:4-13); `backupfile` = report topic (mirza_pro@b645db74 db/tables/topicid.php:4-13)

Written/read by: mirza_pro@b645db74 db/tables/topicid.php:3-26

### `wheel_list`

Found in: `mirza_pro@b645db74 db/tables/wheel_list.php:4`; `operator-report 2026-10-10`
Primary key: `id`

**Purpose.** Lucky wheel (dice) spins; price = Toman credited when the spin wins (0 otherwise).

**Class:** ARCHIVE (NEXA: ARCHIVE) — Wallet credit history not in Payment_report.

**NEXA target:** new archive table proposal (wallet credit evidence)

| Column | Declared type(s) | Sensitivity | Notes |
|---|---|---|---|
| `id` | mirza_pro: INT(6) UNSIGNED AUTO_INCREMENT PRIMARY KEY | public |  |
| `id_user` | mirza_pro: varchar(200) NOT NULL | personal |  |
| `time` | mirza_pro: varchar(200) NOT NULL | public |  |
| `first_name` | mirza_pro: varchar(200) NOT NULL | personal |  |
| `wheel_code` | mirza_pro: varchar(200) NOT NULL | public | dice value |
| `price` | mirza_pro: varchar(200) NOT NULL | financial |  |

Relations:
- `id_user` → user.id (mirza_pro@b645db74 index.php:6270)

Time columns: `time` = date('Y/m/d H:i:s') string, Gregorian, server zone Asia/Tehran (no offset stored) (mirza_pro@b645db74 index.php:6232)

Money columns: `price` = toman (credited via addBalance) (mirza_pro@b645db74 index.php:6253-6256)

Written/read by: mirza_pro@b645db74 index.php:6253, 6269-6275

### `discount_sell`

Found in: `operator-report 2026-10-10`
Primary key: `UNKNOWN`

**Purpose.** UNKNOWN — no public source declares or references this table. Name suggests a snake_case successor of DiscountSell (UNPROVEN).

**Class:** REVIEW (NEXA: UNCLASSIFIED; fails closed until columns and semantics are supplied) — No DDL, no code: meaning and secrets unknown; fails closed until the operator supplies the column list and semantics.

**NEXA target:** UNKNOWN until columns are known

Columns: **UNKNOWN** (no public DDL, operator did not supply columns).

Written/read by: UNKNOWN (not in any public source)

Open questions:
- Supply column names (CREATE TABLE header from the dump; no values).
- Does it hold secrets or money?
- Is it a rename of DiscountSell?

### `discount_sell_used`

Found in: `operator-report 2026-10-10`
Primary key: `UNKNOWN`

**Purpose.** UNKNOWN — no public source declares or references this table. Name suggests per-use log of discount_sell codes (UNPROVEN).

**Class:** REVIEW (NEXA: UNCLASSIFIED; fails closed until columns and semantics are supplied) — No DDL, no code: meaning and secrets unknown; fails closed until the operator supplies the column list and semantics.

**NEXA target:** UNKNOWN until columns are known

Columns: **UNKNOWN** (no public DDL, operator did not supply columns).

Written/read by: UNKNOWN (not in any public source)

Open questions:
- Supply column names (CREATE TABLE header from the dump; no values).
- Does it hold secrets or money?

### `ad_campaign`

Found in: `operator-report 2026-10-10`
Primary key: `UNKNOWN`

**Purpose.** UNKNOWN — no public source declares or references this table. Name suggests advertising/broadcast campaigns (UNPROVEN).

**Class:** REVIEW (NEXA: UNCLASSIFIED; fails closed until columns and semantics are supplied) — No DDL, no code: meaning and secrets unknown; fails closed until the operator supplies the column list and semantics.

**NEXA target:** possible analogue NEXA campaigns (nexa-bot@9e604de apps/api/drizzle/0147_round_n_campaigns.sql:27) — only if proven

Columns: **UNKNOWN** (no public DDL, operator did not supply columns).

Written/read by: UNKNOWN (not in any public source)

Open questions:
- Supply column names (CREATE TABLE header from the dump; no values).
- Does it hold secrets or money?

### `ticket`

Found in: `operator-report 2026-10-10`
Primary key: `UNKNOWN`

**Purpose.** UNKNOWN — no public source declares or references this table. Name suggests a ticket header table distinct from support_message (UNPROVEN).

**Class:** REVIEW (NEXA: UNCLASSIFIED; fails closed until columns and semantics are supplied) — No DDL, no code: meaning and secrets unknown; fails closed until the operator supplies the column list and semantics.

**NEXA target:** possible analogue NEXA tickets (nexa-bot@9e604de apps/api/drizzle/0135_wp_a7_support_tickets.sql:79) — only if proven

Columns: **UNKNOWN** (no public DDL, operator did not supply columns).

Written/read by: UNKNOWN (not in any public source)

Open questions:
- Supply column names (CREATE TABLE header from the dump; no values).
- Does it hold secrets or money?

### `ticket_message`

Found in: `operator-report 2026-10-10`
Primary key: `UNKNOWN`

**Purpose.** UNKNOWN — no public source declares or references this table. Name suggests ticket messages (personal chat text, UNPROVEN).

**Class:** REVIEW (NEXA: UNCLASSIFIED; fails closed until columns and semantics are supplied) — No DDL, no code: meaning and secrets unknown; fails closed until the operator supplies the column list and semantics.

**NEXA target:** possible analogue NEXA ticket_messages (nexa-bot@9e604de apps/api/drizzle/0135_wp_a7_support_tickets.sql:35)

Columns: **UNKNOWN** (no public DDL, operator did not supply columns).

Written/read by: UNKNOWN (not in any public source)

Open questions:
- Supply column names (CREATE TABLE header from the dump; no values).
- Does it hold secrets or money?

### `help_category`

Found in: `operator-report 2026-10-10`
Primary key: `UNKNOWN`

**Purpose.** UNKNOWN — no public source declares or references this table. Name suggests categories for help tutorials (mirza_pro help.category is free text; UNPROVEN).

**Class:** REVIEW (NEXA: UNCLASSIFIED; fails closed until columns and semantics are supplied) — No DDL, no code: meaning and secrets unknown; fails closed until the operator supplies the column list and semantics.

**NEXA target:** UNKNOWN until columns are known

Columns: **UNKNOWN** (no public DDL, operator did not supply columns).

Written/read by: UNKNOWN (not in any public source)

Open questions:
- Supply column names (CREATE TABLE header from the dump; no values).
- Does it hold secrets or money?

### `vs_category`

Found in: `operator-report 2026-10-10`
Primary key: `UNKNOWN`

**Purpose.** UNKNOWN — no public source declares or references this table. "vs_" family: an unknown add-on module (category/package/order/stock/setting). vs_order may hold money and vs_stock may hold credentials (UNPROVEN).

**Class:** REVIEW (NEXA: UNCLASSIFIED; fails closed until columns and semantics are supplied) — No DDL, no code: meaning and secrets unknown; fails closed until the operator supplies the column list and semantics.

**NEXA target:** UNKNOWN until columns are known

Columns: **UNKNOWN** (no public DDL, operator did not supply columns).

Written/read by: UNKNOWN (not in any public source)

Open questions:
- Supply column names (CREATE TABLE header from the dump; no values).
- Does it hold secrets or money?
- What is the vs_ module? Does vs_order carry payments not in Payment_report?

### `vs_package`

Found in: `operator-report 2026-10-10`
Primary key: `UNKNOWN`

**Purpose.** UNKNOWN — no public source declares or references this table. "vs_" family: an unknown add-on module (category/package/order/stock/setting). vs_order may hold money and vs_stock may hold credentials (UNPROVEN).

**Class:** REVIEW (NEXA: UNCLASSIFIED; fails closed until columns and semantics are supplied) — No DDL, no code: meaning and secrets unknown; fails closed until the operator supplies the column list and semantics.

**NEXA target:** UNKNOWN until columns are known

Columns: **UNKNOWN** (no public DDL, operator did not supply columns).

Written/read by: UNKNOWN (not in any public source)

Open questions:
- Supply column names (CREATE TABLE header from the dump; no values).
- Does it hold secrets or money?
- What is the vs_ module? Does vs_order carry payments not in Payment_report?

### `vs_order`

Found in: `operator-report 2026-10-10`
Primary key: `UNKNOWN`

**Purpose.** UNKNOWN — no public source declares or references this table. "vs_" family: an unknown add-on module (category/package/order/stock/setting). vs_order may hold money and vs_stock may hold credentials (UNPROVEN).

**Class:** REVIEW (NEXA: UNCLASSIFIED; fails closed until columns and semantics are supplied) — No DDL, no code: meaning and secrets unknown; fails closed until the operator supplies the column list and semantics.

**NEXA target:** UNKNOWN until columns are known

Columns: **UNKNOWN** (no public DDL, operator did not supply columns).

Written/read by: UNKNOWN (not in any public source)

Open questions:
- Supply column names (CREATE TABLE header from the dump; no values).
- Does it hold secrets or money?
- What is the vs_ module? Does vs_order carry payments not in Payment_report?

### `vs_stock`

Found in: `operator-report 2026-10-10`
Primary key: `UNKNOWN`

**Purpose.** UNKNOWN — no public source declares or references this table. "vs_" family: an unknown add-on module (category/package/order/stock/setting). vs_order may hold money and vs_stock may hold credentials (UNPROVEN).

**Class:** REVIEW (NEXA: UNCLASSIFIED; fails closed until columns and semantics are supplied) — No DDL, no code: meaning and secrets unknown; fails closed until the operator supplies the column list and semantics.

**NEXA target:** UNKNOWN until columns are known

Columns: **UNKNOWN** (no public DDL, operator did not supply columns).

Written/read by: UNKNOWN (not in any public source)

Open questions:
- Supply column names (CREATE TABLE header from the dump; no values).
- Does it hold secrets or money?
- What is the vs_ module? Does vs_order carry payments not in Payment_report?

### `vs_setting`

Found in: `operator-report 2026-10-10`
Primary key: `UNKNOWN`

**Purpose.** UNKNOWN — no public source declares or references this table. "vs_" family: an unknown add-on module (category/package/order/stock/setting). vs_order may hold money and vs_stock may hold credentials (UNPROVEN).

**Class:** REVIEW (NEXA: UNCLASSIFIED; fails closed until columns and semantics are supplied) — No DDL, no code: meaning and secrets unknown; fails closed until the operator supplies the column list and semantics.

**NEXA target:** UNKNOWN until columns are known

Columns: **UNKNOWN** (no public DDL, operator did not supply columns).

Written/read by: UNKNOWN (not in any public source)

Open questions:
- Supply column names (CREATE TABLE header from the dump; no values).
- Does it hold secrets or money?
- What is the vs_ module? Does vs_order carry payments not in Payment_report?

### `wallet_transaction`

Found in: `operator-report 2026-10-10`
Rows (operator report): 17691
Primary key: `UNKNOWN`

**Purpose.** Per-user wallet movement ledger with before/after balance (deployed fork only). Semantics UNKNOWN: no public source declares or writes it.

**Class:** REVIEW (NEXA: OWNER_DECISION) — Potentially the only wallet history that can reconcile user.Balance, but semantics, sign convention and completeness are unproven; NEXA policy is not to replay it. Keep as archive after operator confirms semantics.

**NEXA target:** new archive table proposal (legacy_wallet_history); reconciliation evidence only. nexa-bot@9e604de docs/legacy-migration/reconciliation.md:84-85 (wallet_transaction and payment history are never replayed); nexa-bot@9e604de docs/migration-opening-balance.md:19

| Column | Declared type(s) | Sensitivity | Notes |
|---|---|---|---|
| `id` | operator: type unknown | public | operator-supplied column; type unknown |
| `id_user` | operator: type unknown | personal | operator-supplied column; type unknown |
| `username` | operator: type unknown | personal | operator-supplied column; type unknown |
| `service_username` | operator: type unknown | personal | operator-supplied column; type unknown |
| `type` | operator: type unknown | public | operator-supplied column; type unknown |
| `amount` | operator: type unknown | financial | operator-supplied column; type unknown |
| `balance_before` | operator: type unknown | financial | operator-supplied column; type unknown |
| `balance_after` | operator: type unknown | financial | operator-supplied column; type unknown |
| `ref_id` | operator: type unknown | public | operator-supplied column; type unknown |
| `source` | operator: type unknown | public | operator-supplied column; type unknown |
| `reason` | operator: type unknown | personal | operator-supplied column; type unknown |
| `created_at` | operator: type unknown | public | operator-supplied column; type unknown |

Relations:
- `id_user` → user.id (by name only — UNPROVEN) (operator-report 2026-10-10)
- `service_username` → invoice.username (by name only — UNPROVEN) (operator-report 2026-10-10)

Vocabularies:
- `type`: `UNKNOWN` = enumerate DISTINCT values from dump (operator-report 2026-10-10)
- `source`: `UNKNOWN` =  (operator-report 2026-10-10)

Time columns: `created_at` = UNKNOWN (operator-report 2026-10-10)

Money columns: `amount` = unknown (operator-report 2026-10-10); `balance_before` = unknown (operator-report 2026-10-10); `balance_after` = unknown (operator-report 2026-10-10)

Written/read by: UNKNOWN — no INSERT in either public source (grep wallet_transaction: 0 hits in botmirzapanel and mirza_pro)

Open questions:
- Sign convention of amount (signed vs type-driven)?
- Does balance_after of the latest row per user equal user.Balance at backup time (testable on synthetic → staging)?
- Is history complete since a known date (17,691 rows vs 215,082 Payment_report rows suggests it started late)?
- Values of type/source/reason; ref_id references what (Payment_report.id_order? invoice.id_invoice?)

### `agent_manage`

Found in: `operator-report 2026-10-10`
Primary key: `UNKNOWN`

**Purpose.** UNKNOWN — no public source declares or references this table. Name suggests reseller (agent) management/config (UNPROVEN).

**Class:** REVIEW (NEXA: UNCLASSIFIED; fails closed until columns and semantics are supplied) — No DDL, no code: meaning and secrets unknown; fails closed until the operator supplies the column list and semantics.

**NEXA target:** possible analogue NEXA reseller_tiers (nexa-bot@9e604de apps/api/drizzle/0110_wp9_resellers.sql:40)

Columns: **UNKNOWN** (no public DDL, operator did not supply columns).

Written/read by: UNKNOWN (not in any public source)

Open questions:
- Supply column names (CREATE TABLE header from the dump; no values).
- Does it hold secrets or money?

### `agent_invoice`

Found in: `operator-report 2026-10-10`
Primary key: `UNKNOWN`

**Purpose.** UNKNOWN — no public source declares or references this table. Name suggests reseller billing/settlement invoices — may carry money (UNPROVEN).

**Class:** REVIEW (NEXA: UNCLASSIFIED; fails closed until columns and semantics are supplied) — No DDL, no code: meaning and secrets unknown; fails closed until the operator supplies the column list and semantics.

**NEXA target:** UNKNOWN until columns are known

Columns: **UNKNOWN** (no public DDL, operator did not supply columns).

Written/read by: UNKNOWN (not in any public source)

Open questions:
- Supply column names (CREATE TABLE header from the dump; no values).
- Does it hold secrets or money?
- Does it record reseller debt settlement (setting.Debtsettlement)?

### `agent_log`

Found in: `operator-report 2026-10-10`
Primary key: `UNKNOWN`

**Purpose.** UNKNOWN — no public source declares or references this table. Name suggests reseller activity log (UNPROVEN).

**Class:** REVIEW (NEXA: UNCLASSIFIED; fails closed until columns and semantics are supplied) — No DDL, no code: meaning and secrets unknown; fails closed until the operator supplies the column list and semantics.

**NEXA target:** UNKNOWN until columns are known

Columns: **UNKNOWN** (no public DDL, operator did not supply columns).

Written/read by: UNKNOWN (not in any public source)

Open questions:
- Supply column names (CREATE TABLE header from the dump; no values).
- Does it hold secrets or money?

### `agent_usage_snapshot`

Found in: `operator-report 2026-10-10`
Primary key: `UNKNOWN`

**Purpose.** UNKNOWN — no public source declares or references this table. Name suggests periodic usage snapshots for reseller billing (UNPROVEN).

**Class:** REVIEW (NEXA: UNCLASSIFIED; fails closed until columns and semantics are supplied) — No DDL, no code: meaning and secrets unknown; fails closed until the operator supplies the column list and semantics.

**NEXA target:** UNKNOWN until columns are known

Columns: **UNKNOWN** (no public DDL, operator did not supply columns).

Written/read by: UNKNOWN (not in any public source)

Open questions:
- Supply column names (CREATE TABLE header from the dump; no values).
- Does it hold secrets or money?

### `custom_service_tier_prices`

Found in: `operator-report 2026-10-10`
Primary key: `UNKNOWN`

**Purpose.** UNKNOWN — no public source declares or references this table. Name suggests per-tier prices for custom (volume/time) services — may supersede marzban_panel.pricecustom* JSON (UNPROVEN).

**Class:** REVIEW (NEXA: UNCLASSIFIED; fails closed until columns and semantics are supplied) — No DDL, no code: meaning and secrets unknown; fails closed until the operator supplies the column list and semantics.

**NEXA target:** possible analogue NEXA custom_service_price_rules (nexa-bot@9e604de apps/api/drizzle/0129_package_d_custom_service.sql:21)

Columns: **UNKNOWN** (no public DDL, operator did not supply columns).

Written/read by: UNKNOWN (not in any public source)

Open questions:
- Supply column names (CREATE TABLE header from the dump; no values).
- Does it hold secrets or money?

### `requestagent`

Found in: `operator-report 2026-10-10`
Primary key: `UNKNOWN`

**Purpose.** UNKNOWN — no public source declares or references this table. Lower-case twin of Requestagent; on case-sensitive MySQL both can coexist (UNPROVEN which one the fork writes).

**Class:** REVIEW (NEXA: UNCLASSIFIED; fails closed until columns and semantics are supplied) — No DDL, no code: meaning and secrets unknown; fails closed until the operator supplies the column list and semantics.

**NEXA target:** UNKNOWN until columns are known

Columns: **UNKNOWN** (no public DDL, operator did not supply columns).

Written/read by: UNKNOWN (not in any public source)

Open questions:
- Supply column names (CREATE TABLE header from the dump; no values).
- Does it hold secrets or money?
- Compare with Requestagent.

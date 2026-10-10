# Reseller (agent) history: package records and a proposed NEXA contract

Status: **proposal**. Nothing in this document is implemented in NEXA, and the converter applies
none of it. NEXA's current decision is OQ-LWD-03 (nexa-bot@9e604def `docs/open-questions.md:3422-3425`):
a legacy agent is imported as an **ordinary customer**, with no reseller row, no tier and never any credit.
The converter follows that decision. It also keeps the reseller history in an independent, archive-only
model (`src/mirza2nexa/modules/agents.py`), so the owner can decide later without another backup.

Sources:
* botmirzapanel@92c0ed06
* mirza_pro@b645db74
* nexa-bot@9e604def
* `docs/SOURCE_TABLES.md` Q4

## 1. What Mirza had (evidence)

| Concept | Where | Evidence | Confidence |
|---|---|---|---|
| Tier | `user.agent`: `f` = customer (default), `n` = normal agent, `n2` = advanced agent | mirza_pro `db/tables/user.php:51`; `admin.php:5137-5143,5191` | proven |
| Is-agent test | value not in `'' f 0 false` after JS trim | nexa `decisions.ts:161-171` (`rules.is_agent`) | proven |
| `n2` overdraft | `n2` may spend down to `-user.maxbuyagent` | mirza_pro `function.php:974` (SOURCE_TABLES.md:155) | proven; **column not staged today** |
| Agent expiry | `user.expire` (unix seconds) | `admin.php:8932-8933` | proven; **not staged today** |
| Per-user discount % | `user.pricediscount` | SOURCE_TABLES.md:274 | meaning partly proven; **not staged today** |
| Per-tier prices | JSON `{f,n,n2}` in `marzban_panel.price*`, `shopSetting.chashbackextend_agent`, `product.agent`, `DiscountSell.agent` | SOURCE_TABLES.md:158-163 | proven (other modules own these tables) |
| Agent request | `Requestagent` (`waiting`/`accept`/`reject`; `type` `None`→`n`/`n2`; `time` unix; fee `agentreqprice` deducted) | `db/tables/Requestagent.php:6-11`; `index.php:6104-6125`; `admin.php:5094,5142-5143,5191` | proven |
| Reseller sales bot | token-identified child bots (`user.bottype` = sub-bot token, **SECRET**) | nexa research `reseller-crossmap.md`; SOURCE_TABLES | never migrated |
| `agent_manage`, `agent_invoice`, `agent_log`, `agent_usage_snapshot`, `custom_service_tier_prices`, `requestagent` | deployed fork only | operator list; **no public DDL** | **UNKNOWN** columns and meaning |

## 2. What the package carries today (`records/agents/*.jsonl`)

Every record has these fields:
* `record_type`, `schema`, `idempotency_key` (`legacy:agents:…`) and `provenance`;
* `activates_role: false`, `grants_credit: false`, `affects_wallet: false` and `applies_to_live_state: false`.

Money-like values in agent tables are kept as **raw text only**.

| File | `record_type` | One record per | Key fields |
|---|---|---|---|
| `profiles.jsonl` | `legacy_agent_profile` | agent: every `user` row with `is_agent`, plus every `agent_manage` owner not found among them | `customer` {raw_id, telegram_user_id, decision, valid}, `tier` (`n`/`n2`/`UNKNOWN`/`NOT_AGENT_IN_USER_TABLE`), `tier_label`, `tier_raw`, `agent_manage[]` (raw redacted rows), `status: ARCHIVED_NOT_ACTIVATED`, `nexa_role: null`, `limits_and_credit: RAW_ONLY_NOT_APPLIED` |
| `agent_invoices.jsonl` | `legacy_agent_invoice` | `agent_invoice` row | `owner`, `invoice_ref` {column, raw, exists_in_invoice_table}, `fields_raw`, `semantics: UNKNOWN` |
| `agent_logs.jsonl` | `legacy_agent_log` | `agent_log` row | `owner`, `fields_raw` (free text redacted) |
| `agent_usage.jsonl` | `legacy_agent_usage` | `agent_usage_snapshot` row | `owner`, `fields_raw` |
| `tier_prices.jsonl` | `legacy_agent_price_level` | `custom_service_tier_prices` row; **levels are never collapsed** | `owner_value_raw`, `level_index` (1..k in PK order), `levels_for_owner` (k), `fields_raw`, `price_semantics: UNKNOWN`, `applies_price: false` |
| `agent_requests.jsonl` | `legacy_agent_request` | `Requestagent` and `requestagent` row (separate tables, separate schemas `…v1.mirza` / `…v1.fork`) | `requester`, `status` (`WAITING`/`ACCEPTED`/`REJECTED`/`UNKNOWN`), `granted_tier`, `requested_at` (+ raw, note), `description` (redacted; personal) |

For the fork tables, the owner column is the first column present from `user_id`, `id_user`, `iduser`,
`userid`, `agent_id`, `id_agent`, `chat_id`, `telegram_id` and `from_id`. Mirza uses these names for a
Telegram user reference in its documented tables, but that does not prove anything for the fork tables.
So every such relation is marked `COLUMN_NAME_CONVENTION_UNPROVEN`, and a row with no valid owner goes to
review as `AGENT_OWNER_UNKNOWN`.

Review codes (all `warning`; none blocks):
* `AGENT_NOT_CUSTOMER`
* `AGENT_OWNER_UNKNOWN`
* `AGENT_MULTIPLE_PRICE_LEVELS`
* `AGENT_REQUEST_REQUESTER_UNKNOWN`
* `TABLE_NEEDS_DECISION` (one per non-empty fork table)
* `TABLE_NOT_STAGED`
* `ARCHIVE_DUPLICATE_PK`

The `agents` reconciliation section reports:
* user-table agents by tier;
* profiles by source and tier, and `agent_not_customer`;
* rows and owner-column resolution per table;
* a price-level histogram and `owners_with_multiple_levels`;
* agent requests by status;
* `activates_role_true` and `grants_credit_true` (always 0).

## 3. Proposed NEXA contract (for an owner decision; do not build without approval)

NEXA already has reseller concepts:
* `RESELLER_STATUSES` `ACTIVE`/`SUSPENDED`;
* pricing modes `LIST_PRICE`/`PERCENTAGE_DISCOUNT`;
* overrides `TIER`/`LIST_PRICE`/`PERCENTAGE_DISCOUNT`;
* grants `PRODUCT`/`CATEGORY`/`PANEL`/`BOT`/`OPERATION`;
* custom-service price rules per tier.

These are in `packages/contracts/src/promotions.ts:554-700` and `docs/reseller-phase3-closure.md`. Reseller
credit was **removed** by an owner decision on 2026-10-01: `RESELLER_DEFAULT_CREDIT_LIMIT_MINOR = 0n`.

The proposal maps the archive onto these concepts. It does not invent a second model.

1. **Role.** A NEXA reseller is created only by an explicit per-agent owner/operator action, starting
   from `legacy_agent_profile`. The import never creates one. Proposed import record:
   `reseller_candidate {legacy_telegram_id, legacy_tier, proposed_nexa_tier_id: null, decision: PENDING}`.
2. **Tiers.** `n` and `n2` become two *candidate* NEXA tiers. The operator chooses or creates the NEXA
   tier and its grants. Mirza's per-tier JSON prices are **not** converted into NEXA prices, because NEXA
   prices by rate and not by price list (`promotions.ts` `RESELLER_PRICING_MODES` comment).
3. **Custom prices.** Each `legacy_agent_price_level` stays a separate archived fact. If the owner wants
   them, an operator recreates them as NEXA `custom_service_price_rules` by hand, after the fork schema
   is proven. Levels are never merged automatically.
4. **Reseller balance / credit / debt.**
   * The wallet balance migrates exactly like any customer's (core: opening credit or legacy debt).
   * `n2` overdraft (`maxbuyagent`) and any `agent_manage` credit fields are **not** granted, because NEXA
     credit is zero by owner decision.
   * A negative `n2` balance is already a `legacy_debt` (`PENDING_REVIEW`, never auto-settled).
5. **Reseller services / sub-bots.** Services stay with their customer (core service candidates). Sub-bot
   tokens (`bottype`) are SECRET and never migrated. The owner re-onboards bots in NEXA.
6. **Limits and expiry.** `user.expire`, `maxbuyagent`, `pricediscount` and the minimum monthly sales
   are archived (after staging is extended, see §5) as reference only. NEXA has `effectiveMonthlyMinimum`,
   but values are never copied automatically.
7. **Sales history.** `agent_invoice`, `agent_log` and `agent_usage_snapshot` stay archive-only. NEXA
   would need a read-only `legacy_reseller_history` view (new table, owner approval) keyed by
   `idempotency_key`.

## 4. Mapping table (Mirza field → package → proposed NEXA target)

| Mirza table.field | Units / type | Nullable | Package field | Proposed NEXA target | Status |
|---|---|---|---|---|---|
| `user.agent` | token `f`/`n`/`n2` | yes (default `f`) | `profiles.tier`, `tier_raw` | `reseller_candidate.legacy_tier` → operator picks a `reseller_tiers.id` | archived; blocked by OQ-LWD-03 |
| `user.id` (agent) | Telegram id text | no | `profiles.customer` | `customers` (core) | core import |
| `user.Balance` (agent) | Toman, int | no | core `wallet_opening_balances` / `legacy_debts` | ledger / `legacy_wallet_debts` | core import |
| `user.maxbuyagent` | Toman (credit limit) | yes | — (not staged) | none (NEXA credit = 0) | **needs staging change** |
| `user.expire` | unix seconds | yes | — (not staged) | reference only | **needs staging change** |
| `user.pricediscount` | percent | yes | — (not staged) | per-reseller `PERCENTAGE_DISCOUNT` override (manual) | **needs staging change** |
| `Requestagent.id` | Telegram id | no | `agent_requests.requester` | none | archived |
| `Requestagent.time` | unix seconds | no | `requested_at` (UTC) | none | archived |
| `Requestagent.status` | `waiting`/`accept`/`reject` | no | `status` | none | archived |
| `Requestagent.type` | `None`/`n`/`n2` | no | `granted_tier` | none | archived |
| `Requestagent.Description` | free text (personal) | no | `description` (redacted) | none | archived (package only) |
| `Requestagent.username` | text (personal) | no | not exported | — | dropped from records |
| `agent_manage.*` | UNKNOWN | UNKNOWN | `profiles.agent_manage[].fields_raw` | possible `resellers` override fields | **UNKNOWN schema** |
| `agent_invoice.*` | UNKNOWN | UNKNOWN | `agent_invoices.fields_raw`, `invoice_ref` | `legacy_reseller_history` (new) | **UNKNOWN schema** |
| `agent_log.*` | UNKNOWN | UNKNOWN | `agent_logs.fields_raw` | `legacy_reseller_history` (new) | **UNKNOWN schema** |
| `agent_usage_snapshot.*` | UNKNOWN | UNKNOWN | `agent_usage.fields_raw` | `legacy_reseller_history` (new) | **UNKNOWN schema** |
| `custom_service_tier_prices.*` | UNKNOWN | UNKNOWN | `tier_prices` (one per level) | `custom_service_price_rules` (manual) | **UNKNOWN schema** |
| `requestagent.*` | UNKNOWN | UNKNOWN | `agent_requests` (`…fork`) | none | **UNKNOWN schema** |

## 5. Blocked until the owner decides

* **OQ-LWD-03.** Does any legacy agent become a NEXA reseller, with which tier and which grants?
  Default: no.
* **OQ-M2N-AG-01.** The fork's DDL for `agent_manage`, `agent_invoice`, `agent_log`, `agent_usage_snapshot`,
  `custom_service_tier_prices` and `requestagent`. This covers columns, units (Toman?), owner column and
  status values. It must come as redacted/synthetic `SHOW CREATE TABLE` output; real data is not needed.
* **Is `requestagent` a rename of `Requestagent`, or a separate feature?** Today they are treated as
  separate tables.
* **Framework change, not in this module's files.** Stage `user.maxbuyagent`, `user.expire`,
  `user.pricediscount` and `user.affiliates` as optional user columns (`mapping/rules.py` `OPTIONAL_COLUMNS` +
  `core_specs.py` sensitivities). The profiles can then archive them raw.
* **Reseller credit.** It stays zero (owner decision 2026-10-01). Any `n2` overdraft history is information
  only.

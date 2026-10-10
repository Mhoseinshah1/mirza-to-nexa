# Panels, service status classes and adoption (module `adoption`)

This page covers three things:

* how Mirza identifies a panel;
* how every invoice is classified by status;
* how NEXA can later adopt existing panel accounts **without provisioning anything again**.

The code is in `src/mirza2nexa/modules/adoption.py` (module key `adoption`, order 360, after `services_ext`). The tests are in `tests/test_adoption.py`.

The module reads only staged tables and the core's temporary `svc` decision table. It owns no Mirza table, so it writes no coverage. It never calls or looks up a panel. It never creates a NEXA panel, product or category.

Evidence sources:

| Short name | Source | Commit |
|---|---|---|
| **MP** | `mirza_pro` | `b645db74` |
| **BMP** | `botmirzapanel` | `92c0ed06` |
| **NEXA** | `nexa-bot` | `9e604def` |

Neither public Mirza source is the deployed fork. The fork's `invoice` has columns that no public revision declares: `code_panel`, `code_product`, `is_test`, `is_custom` and `time_unit` (`docs/NEXA_EVIDENCE.md` §0). Anything below that depends on those columns is marked **fork**.

## 1. Panel identity: `id`, `code_panel`, `name_panel`

| Identifier | What it is | Evidence |
|---|---|---|
| `marzban_panel.id` | AUTO_INCREMENT row id. **No other table references it.** The only use is the web API's delete. | MP `db/tables/marzban_panel.php:11`; `api/panels.php:312` |
| `marzban_panel.code_panel` | A short random hex code made when the panel is added: `bin2hex(random_bytes(2))` (4 hex chars) from the bot, `bin2hex(random_bytes(3))` from the web API. Older panels without a code are backfilled as `7e<N>`. Button callbacks, `DiscountSell.code_panel`, `manualsell.codepanel` and `service_other.value.old_panel/new_panel` use it. | MP `function.php:1580`; `api/panels.php:156`; `db/migrations/002_panel_code_backfill.php`; `keyboard.php:886`; `index.php:2306-2307, 4169` |
| `marzban_panel.name_panel` | The admin's display label, unique by check. It is **the key Mirza uses to talk to a panel**: every driver call looks the panel up by name. | MP `panels.php:39, 80, 105`; `admin.php:773, 2698` |
| `invoice.Service_location` | The panel's **name** (`name_panel`) at sale time. It is not a code. | MP `index.php:3005, 3922`; `function.php:1034`; `cronbot/NoticationsService.php:84` |
| `invoice.code_panel` | **fork** column. NEXA's importer keys its panel map by this column (`codePanel`). | NEXA `source-port.ts:36-83`; `docs/legacy-migration/importer.md:255-303` |
| `product.Location` | A panel **name**, or `/all` for every panel. | MP `index.php:1410`; `panels.php:52` |

Name-based links change over time:

* **Renaming a panel** cascades the new name to `invoice.Service_location` and `product.Location` (MP `admin.php:2703-2706`).
* **Changing a service's location** rewrites that invoice's `Service_location` to the new panel's name (MP `index.php:2385`). The public code does not show whether the fork also rewrites `invoice.code_panel`.
* **Deleting a panel** removes only the `marzban_panel` row (MP `admin.php:2836`; `api/panels.php:312`). Its invoices keep the old name and code, so they point at nothing.

### Why the real backup shows 5 registry rows but only 2 codes in invoices

The operator's baseline is 5 `marzban_panel` rows, with 2 distinct `code_panel` values in invoices. The public code allows each of these explanations. Each one can be checked with a field of `reconciliation.panel_mapping` on the real run, and none is assumed:

| Explanation | Evidence that allows it | Field that confirms or rules it out |
|---|---|---|
| Panels with no sales, such as a test-only panel, a `Manualsale` stock panel, a newly added panel or one taken out of sale | Panels are added freely (MP `function.php:1573-1617`), and `Manualsale` panels sell from stock (MP `panels/Drivers/Manualsale.php`) | `registry_rows_without_any_invoice`; per row `suggested_map_section = NONE_NEEDED_NO_INVOICES` |
| Invoices reference a panel by name only, and `code_panel` is empty on older invoices | `code_panel` is a fork column added after `Service_location`. The backfill migration shows codes arrived late (MP `002_panel_code_backfill.php`). | `invoices_without_code_panel`; the per-row `invoices_by_service_location_name` versus `invoices_by_code_panel` |
| A panel was deleted, so its invoices carry a code or name that the registry no longer holds | MP `admin.php:2836` | `codes_in_invoices_missing_from_registry`; rows of kind `INVOICE_CODE_NOT_IN_REGISTRY` and `INVOICE_LOCATION_NOT_IN_REGISTRY` |
| A panel was renamed or a location changed, and the fork kept `code_panel` | MP `admin.php:2704`; `index.php:2385` | `codes_with_several_locations`; per row `service_locations_seen_with_code` |

NEXA's own notes name one such code, panel **8255**, as one that "nobody has decided" (NEXA `service-review.md` §1). This module never guesses for such a code. It lists the code with `decision: PENDING_OWNER`.

### `records/panel_mapping_template.jsonl`

This file has record type `legacy_panel_mapping_template`, schema `m2n.panel_mapping_template.v1`, and one row per **Mirza panel identity**:

| `identity_kind` | Source | Key |
|---|---|---|
| `REGISTRY` | each `marzban_panel` row | `legacy:panel-map-template:registry:<id>` |
| `INVOICE_CODE_NOT_IN_REGISTRY` (`INVOICE_CODE` when the dump has no registry) | each `invoice.code_panel` that the registry does not hold, including `(no code_panel)` and `(invalid code_panel)` buckets | `…:invoice-code:<code>` |
| `INVOICE_LOCATION_NOT_IN_REGISTRY` | each `Service_location` name that no registry row carries. These rows are written only when a registry exists. | `…:invoice-location:<keyed HMAC of the name>` |

Each row carries these fields:

* `legacy_panel_id`, `code_panel`, `name_panel` (redacted), `panel_type` (raw, and normalized against MP `panels/PanelRegistry.php:6-17`), `registry_status_raw` and `code_unique_in_registry`;
* `invoices_by_code_panel` and `invoices_by_service_location_name`: invoice counts **per status class** (§2), counted separately for the two link kinds;
* `live_core_decisions`: class → core service decision → count, for invoices whose status is in NEXA's live set;
* `service_locations_seen_with_code` and `code_panels_seen_with_location`: the cross-links between code and name;
* `suggested_map_section`: one of these values:

  | Value | Meaning |
  |---|---|
  | `panels_OR_unresolvedPanels` | the identity has ACTIVE, EXPIRED or DISABLED invoices |
  | `testPanels_CANDIDATE` | only TEST invoices |
  | `NONE_NEEDED_NO_SERVICEABLE_INVOICES` | invoices exist, but none of them is ACTIVE, EXPIRED, DISABLED or TEST |
  | `NONE_NEEDED_NO_INVOICES` | no invoice references the identity |

  This is a suggestion only. The owner decides.
* `map_format: "nexa-legacy-panel-map/v1"`, `nexa_panel_id: null`, `decision: "PENDING_OWNER"`, `credentials: "ENTER_IN_NEXA_SECRET_FLOW"` and `credentials_in_package: false`;
* `provision: false`, `applies_to_live_state: false` and `provenance`.

**These fields are never present:** URL, panel username, password, `secret_code`, the `datelogin` session token, `linksubx`, `proxies`, `inbounds` or inbound ids. These columns are dropped at parse time (`services_ext.PANEL_SECRET_COLUMNS`). The test plants fake values in all of them and checks that none reaches any output.

## 2. Service status classes

Every invoice, whatever its status, is classified from `src_invoice`. The comparison is **exact and case-sensitive**, as in NEXA (`source-port.ts:112-119`). A value with no writer in the public code is `UNKNOWN`, and its raw value is counted, never guessed.

| Class | Raw `Status` values | Writer evidence |
|---|---|---|
| **TEST** | any status where `is_test = '1'` (**fork**). This takes precedence over the status. | NEXA `importer.md:380-384` (`TEST_INVOICE_SKIPPED`) |
| **ACTIVE** | `active` | MP `function.php:1051`; `index.php:1880`. Test accounts are also inserted `active` (MP `index.php:3001-3005`). |
| | `send_on_hold` | MP `cronbot/on_hold.php:27-37` (the panel account is `on_hold` and not started) |
| **EXPIRED** | `end_of_time` | BMP `cron/cronday.php:33-38`, written when **≤ 167,000 s remain**. It is a warning state: Mirza still lists the service and allows renewal (MP `index.php:408, 1754`). |
| | `end_of_volume` | BMP `cron/cronvolume.php:36` |
| | `sendedwarn` | BMP `cron/cronday.php:36`; `cron/cronvolume.php:34` |
| **DISABLED** | `disabled` | MP `cronbot/configtest.php:19-20`: an expired **test** account, written **after `RemoveUser`**, so the panel account no longer exists. BMP `cron/cronday.php:41-42`: the panel status is neither active nor on_hold. |
| | `disabledn` | MP `index.php:811` (the customer switched the service off) |
| | `disablebyadmin` | MP `admin.php:9020`; `api/invoice.php:277`; `cronbot/disableconfig.php:30` |
| **REMOVED** | `removebyuser` | MP `index.php:1211` |
| | `removebyadmin` | MP `api/invoice.php:157, 163`; `admin.php:3768, 3788` |
| | `removedbyadmin` | MP `admin.php:4749, 4778`; BMP `admin.php:1737` (a cancellation was accepted) |
| | `removeTime` | MP `cronbot/NoticationsService.php:146-147`; BMP `cron/removeexpire.php:38` |
| | `removevolume` | MP `cronbot/NoticationsService.php:189-190` |
| **NOT_PROVISIONED** | `unpaid` | MP `index.php:3921-3923` (purged by `cronbot/optimize_order.php:11`) |
| | `Unsuccessful` | MP `index.php:3020` (the panel create failed) |
| **UNKNOWN** | anything else, including NULL, `removed`, `expired` and case variants such as `Active` | no writer in MP or BMP |

`NOT_PROVISIONED` is a seventh class, beyond the six the readiness check reads. These rows never had a panel account. `docs/evidence/mirza_tables.json` lacks the writers for `disabled`, `removeTime` and `removevolume`. This page adds them.

### What the 43,093 "skipped" are

The core (`convert.py _services`) considers only invoices whose status is in NEXA's live set: `active`, `disabled`, `disabledn`, `disablebyadmin` and `end_of_volume`. Its `skipped` count is exactly the decision `TEST_INVOICE_SKIPPED`: **a live-status invoice with `is_test = '1'`**. It is not a non-live invoice, because non-live invoices never enter the service count.

On the real backup this means 77,135 live = 31,253 converted + 2,789 review + 43,093 test. That is consistent with the evidence:

* Mirza inserts every test account as `active` (MP `index.php:3001-3005`).
* The only public writer that moves a test invoice out of `active` sets it to `disabled` (MP `configtest.php:20`), which NEXA still counts as live.
* Mirza's own expiry crons skip test invoices (`name_product != testServiceName`, MP `cronbot/NoticationsService.php:73`; BMP `cron/removeexpire.php:14`).

So test invoices pile up in live statuses. `reconciliation.service_status_breakdown` proves this on every run:

* `by_core_decision_and_class.TEST_INVOICE_SKIPPED` holds only `TEST`.
* `core_skipped_explained.TEST_INVOICE_SKIPPED` equals the core's count.
* `is_test_vs_test_product_name` cross-checks `is_test` against the test product names `سرویس تست` (MP `lang/fa.php:163`) and `usertest` (BMP `admin.php:99`).

### NEXA's live set versus Mirza's own

Mirza treats `active`, `end_of_time`, `end_of_volume`, `sendedwarn` and `send_on_hold` as the customer's usable services (MP `index.php:408`). NEXA's live set differs in both directions:

* It **includes** `disabled`, `disabledn` and `disablebyadmin`.
* It **excludes** `end_of_time`, `sendedwarn` and `send_on_hold`.

Invoices in the excluded statuses may still be paid, working accounts, and today they never become adoption candidates. `live_set_divergence` counts both directions. The warning `ADOPTION_LIVE_SET_DIVERGENCE` is raised when Mirza-usable invoices fall outside NEXA's set. This is **owner decision OD-ADOPT-1**.

### `reconciliation.service_status_breakdown`

The section contains these fields:

* `invoices`;
* `by_class` (all seven classes) and `by_class_and_status` (raw values for known statuses only);
* `unknown`, with `status_values_without_writer` and `case_variants_of_known_values`;
* `by_core_decision_and_class` and `by_panel_class_decision`, the matrix of code_panel × class × core decision. Non-live invoices appear under the decision `NOT_LIVE_STATUS`.
* `core_decisions_available`, `is_test_flag`, `is_test_vs_test_product_name`, `core_skipped_explained` and `live_set_divergence`.

## 3. Adoption design: from Mirza panel accounts to NEXA services, with nothing provisioned again

### 3.1 Panel mapping (owner)

1. The operator opens `panel_mapping_template.jsonl` and decides each identity. A code goes into exactly one of these lists of `nexa-legacy-panel-map/v1` (NEXA `importer.md:255-303`):
   * `panels`: `{codePanel, panelId}`. The panel must be an ACTIVE, non-archived RickPanel of the tenant.
   * `testPanels`;
   * `unresolvedPanels`: `{codePanel, reason}`, where the reason is `OWNER_DECIDES_LATER`, `DECOMMISSIONED_PANEL` or `UNKNOWN_ORIGIN`;
   * `missingPanels`, which is searched by username only because the operator declares it.
2. The operator creates panel credentials **in NEXA's own secret flow**. They never come from this package.
3. NEXA fingerprints the map. A resume under a different map is refused (`importer.md:283-288`). Mapping is all-or-nothing per code: `mapped` stays `0` in this package because the package never maps.

Name-only identities (`INVOICE_LOCATION_NOT_IN_REGISTRY`) and the `(no code_panel)` bucket cannot be expressed in the v1 map. NEXA treats an empty code as `NO_PANEL`, which goes to manual review, and only an explicit operator ADOPT approval may adopt it (NEXA `service-review.md` §1, owner decision 8). A proposal to let the map accept a `name_panel → panelId` entry for these is in §5.

### 3.1a NEXA target panel type: RickPanel (operator selection, `records/panel_target_mapping.jsonl`)

Mirza stores the main panel with type `marzban` because RickPanel's API is Marzban-compatible. In
NEXA the panel is a **RickPanel** (version 1.0.0, declared by the operator). The operator selects
the target per `code_panel` before conversion (UI «پنل مقصد در NEXA», CLI
`--panel-target CODE=rickpanel[,panel_id=UUID][,version=1.0.0]`, API `panel_targets`). Nothing
is inferred from the Mirza type: a panel without a selection keeps `OPERATOR_MUST_MAP`.

The identifiers come from the NEXA source (`nexa-bot@9e604def`), never guessed:

| Value | Evidence |
|---|---|
| provider id `rickpanel` | `packages/contracts/src/provider.ts:180` (`PROVIDER_TYPES`), persisted in `panels.provider_type` with CHECK `panels_provider_type_check` (`apps/api/src/infrastructure/persistence/schema.ts:2024,2182`) |
| display name `RickPanel` | `provider.ts:1778`, `apps/api/src/modules/platform/providers/infrastructure/rickpanel.adapter.ts:144` |
| importer rule | mapped panels must be `rickpanel`, ACTIVE, not archived; map entry `{codePanel, panelId}` (`docs/legacy-migration/importer.md:261,284-285`) |

Only `rickpanel` is accepted as a target (`RickPanel`, `RIC Panel`, `marzban` … are refused). One
record per legacy panel code (`legacy_panel_target`, `m2n.legacy_panel_target.v1`):
`source_panel_type` (exactly as read from Mirza), `target` (`provider_type`, `provider_display_name`,
`provider_version_declared`, `nexa_panel_id`, `binding` = `CONNECT_EXISTING_NEXA_PANEL` when a NEXA
panel UUID is given, else `CREATE_IN_NEXA_THEN_CONNECT`, `evidence`), `nexa_panel_map_entry`
(`{codePanel, panelId}` ready for the v1 map) and `services_reprovisioned: false`. A selection for
a code that is not in the backup blocks the package (`PANEL_TARGET_UNKNOWN_CODE`).

Every other record (customers, balances, services, volumes, expiries, legacy ids, panel registry,
mapping template) is byte-identical with or without a selection (`tests/test_panel_targets.py`).
The importer creates (only via NEXA's secure panel settings, with the owner's approval) or connects
a `rickpanel` panel and then adopts existing accounts; it never re-creates a service.

### 3.2 Adoption flow, done by NEXA later, never by this converter

1. **Candidate.** A `legacy_service_candidate` (core `records/services.jsonl`) carries a decision of `NAMED_PRODUCT` or `HIDDEN_SHAPE`, `provision: false` and `requires_live_panel_check: true`.
2. **Ownership gate.** The candidate is eligible only if the ownership model (Ownership agent, `services_ext`) marks it `CONFIRMED_CURRENT_OWNER`, or `CONFIRMED_TRANSFER` when a `service_other.transfertouser` chain explains it.
   * `AMBIGUOUS`, a conflict, or an owner missing from the backup goes to **manual review**, never to automatic adoption. This matches the core's `AMBIGUOUS_OWNERSHIP` (`plan.ts:311-342`).
3. **Read-only panel lookup.** NEXA walks the **complete** inventory of the mapped panel with read calls only, and finds the account by its exact lowercase username (NEXA `importer.md:290, 403-417`). Several outcomes go to manual review:
   * an incomplete walk (`INVENTORY_INCOMPLETE`);
   * a case collision;
   * no hit.
4. **Operator approval.** The operator reviews the adoption report. NEXA's approval gate binds the panel-map, inventory, product and dump fingerprints (`importer.md:41-45`).
5. **Adopt (NEXA P6).** NEXA records ownership of the **existing** account. It does not create, renew, extend, reset, disable or delete the account, and it does not change its expiry or traffic.
6. **Idempotency.** The adoption is keyed by `legacy:service:<id_invoice>`.
   * A second run finds the adoption already done and does nothing.
   * A later backup delta re-runs the gate. It never re-adopts or re-provisions.
   * Several invoices of one owner on one account: the first adopts, and the later ones are reported as conflicts (`convert.py` comment; P6).

### 3.3 Minimum evidence to accept ownership and status

| Fact | Accepted from the backup alone | Confirmable only live, by the read-only lookup |
|---|---|---|
| The owner is a Telegram customer | `invoice.id_user` → `user.id` with the core decision `IMPORT` | — |
| The owner is unique for the account | one owner per (`code_panel`, lowercase `username`) across eligible invoices; a transfer is proven by `service_other.transfertouser` | — |
| The account belongs to this panel | `invoice.code_panel` mapped by the owner (a name-only link needs an explicit approval) | The account **exists** on that panel now (inventory hit) |
| The account is in use | the status class (§2) is evidence of Mirza's last write only | real status, expiry, used and remaining traffic, online state and subscription link: **live only**. A Mirza `disabled` test account has already been deleted on the panel (MP `configtest.php:19-20`). |
| Product and renewal terms | `code_product` and shape (core) | — (the renewal price is always the current NEXA tariff) |

Anything in the right-hand column is never stated by this package. `requires_live_panel_check: true` on every candidate records that.

## 4. Codes and warnings

| Code | Kind | When |
|---|---|---|
| `PANEL_MAPPING_PENDING_OWNER` | warning (package) | The template has rows. Its count is the number of rows. |
| `ADOPTION_STATUS_UNKNOWN` | warning (package) | Invoice status values have no public writer. |
| `ADOPTION_LIVE_SET_DIVERGENCE` | warning (package) | Mirza-usable statuses fall outside NEXA's live set (OD-ADOPT-1). |
| `PRODUCT_PROPOSAL_BLOCKED` | review (warning, `kind: product`) | See `docs/PRODUCTS_MAPPING.md`. |

There are no package blockers. Nothing here is applied.

## 5. Owner decisions and open questions

* **OD-ADOPT-1**: Should NEXA's live set include `end_of_time`, `sendedwarn` and `send_on_hold`? These are usable in Mirza and are excluded today. Should `disabled` stay live, given that in MP it means a test account that was already removed?
* **OD-ADOPT-2**: Map each `code_panel` that has ACTIVE, EXPIRED or DISABLED invoices to a NEXA RickPanel, or declare it in `unresolvedPanels`. Panel 8255 follows the same rule.
* **OD-ADOPT-3**: For invoices without `code_panel`, should the panel map gain a `name_panel → panelId` form (a NEXA contract change), or do they stay under manual ADOPT approval?
* **OD-ADOPT-4**: Are `TEST` invoices ever adopted? The current answer is no. Test panels go into `testPanels`.
* **OQ-ADOPT-1**: Does the deployed fork update `invoice.code_panel` on a location change (MP `index.php:2385` updates only the name)?

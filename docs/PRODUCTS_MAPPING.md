# Product mapping proposal (module `adoption`)

`records/product_mapping_proposal.jsonl` has one record per staged `product` row:

* record type `legacy_product_mapping_proposal`;
* schema `m2n.product_mapping_proposal.v1`;
* key `legacy:product-proposal:<id>`, or `…:row<n>` when the `id` is duplicated.

It complements the core's `legacy_product_review` (`records/products.jsonl`) in three ways:

* It converts every field with evidence.
* It proposes a NEXA catalog shape.
* It gives the proposal a status.

**No proposal is ever sellable or auto-created.** Every record has `auto_create: false`, `sellable: false` and `provision: false`, and every proposed catalog shape has `status: "INACTIVE"`, `price: null`, `panel_id: null` and `category_id: null`. A product reaches NEXA only through NEXA's own `legacy_product_reviews` state machine (`APPROVED_NEW` / `APPROVED_EXISTING` / `REJECTED`, NEXA `legacy-product-review.ts`) and the panel map's `products[{codeProduct, productId}]` (NEXA `importer.md:271-279`).

Evidence short names: **MP** = mirza_pro@b645db74, **NEXA** = nexa-bot@9e604def. The deployed fork is not public. The units below are proven for MP, and the owner confirms they hold in the fork (OD-PROD-1).

## 1. Conversion rules

| Mirza field | Rule | Evidence | NEXA field |
|---|---|---|---|
| `Volume_constraint` | **GiB**. Bytes = value × 1024³. Up to two decimals are accepted (`rules.parse_traffic_gb`). `0` means **unlimited**. | MP `index.php:3982, 4515` (`'data_limit' => Volume_constraint * pow(1024, 3)`); `function.php:1043`. Mirza shows `data_limit = 0` as "unlimited" (`index.php:649`). | `specification.trafficBytes` (string). NEXA's `UNLIMITED_TRAFFIC_BYTES = 0n` (`catalog.ts:212`), so 0 maps to 0. The maximum is `MAX_TRAFFIC_BYTES` (`catalog.ts:254`). |
| `Service_time` | **Days**. Expiry = `strtotime("+N days")`. `0` means expiry 0, which is **unlimited**. | MP `function.php:1035-1037`; `index.php:1701-1702` ("unlimited" label); the invoice copies the product value (`index.php:3922`) | `specification.durationDays`. NEXA's `UNLIMITED_DURATION_DAYS = 0` (`catalog.ts:210`). The maximum is 3650 (`catalog.ts:253`). |
| `price_product` | An integer in the `user.Balance` unit (Toman, owner decision 7). It is **history only**. | `rules.parse_price`; NEXA `legacy-migration.ts:187-197` (renewals use the current NEXA tariff) | `price: null`. The owner sets the NEXA tariff. |
| `agent` | Each product row belongs to **one** customer tier. Mirza lists only `agent = <the user's tier>` (`index.php:1410`). `f` → `CUSTOMER`, `n` → `RESELLER`, `n2` → `RESELLER_LEVEL_2`. | MP `index.php:164` (`f`, `n`, `n2`); `db/tables/product.php` (default `f`) | `audience`: `EVERYONE` for `f`, `RESELLERS_ONLY` for `n` and `n2` (NEXA `catalog.ts:277`). This is a proposal only. NEXA's visibility vocabulary is deliberately not a price tier. |
| `Location` | A panel **name**, or `/all`. It is resolved to `code_panel` through `marzban_panel.name_panel`. | MP `index.php:1410`; `panels.php:52`; a rename cascades (`admin.php:2703-2706`) | `panel_id: null`. It needs the owner's panel map (`docs/PANELS_AND_ADOPTION.md`). |
| `hide_panel` | A JSON list of panel **names** where the product is hidden. The MP default is `{}`. | MP `keyboard.php:1332-1334`; `index.php:1440-1441`; `db/tables/product.php` | Resolved to `code_panels`, plus an unresolved count. NEXA has no per-panel hiding, so this is an owner decision. |
| `one_buy_status` | `'0'` means always offered. `'1'` means **first purchase only**: it is shown only to users with no non-`Unpaid` invoice, and it is excluded from renewal lists. | MP `keyboard.php:1335-1343`; `index.php:1410, 1432, 1501`; `admin.php:9337-9340` (off/on labels) | There is no NEXA equivalent. The owner decision is `FIRST_PURCHASE_ONLY_HAS_NO_NEXA_EQUIVALENT`. |
| `data_limit_reset` | A panel reset strategy. Only `no_reset` is evidenced, as the default. | MP `db/tables/product.php` | There is no NEXA field. Any other value adds the owner decision `DATA_LIMIT_RESET_HAS_NO_NEXA_EQUIVALENT`. |
| `Category` / `category` | The label text. It is matched against `category.remark` by `lower(trim)`. | MP `index.php:3595-3598`; `keyboard.php:1379` | `category_id: null`. Categories are never auto-created. |
| `status_product` | `active`, or `disable` (sold out). | MP `db/tables/product.php` columns; `index.php:3704` | **Not staged**: it is not in the core's product read set. The record says `sale_status.read: false`. The status is always `INACTIVE` regardless. |
| `name_product` | The title. It is redacted and output only as `title_proposal`. | MP `db/tables/product.php` | `title: null`. The owner writes the NEXA title. |
| `note` | Free text. It is **not exported**: only `note_present` is. | — | — |
| `inbounds`, `proxies` | **SECRET**. Never read. | NEXA `products-read-set.ts:20-35` | — |

## 2. Proposal status

A proposal is `READY_FOR_OWNER_REVIEW` only when every one of the following holds. Otherwise its status is `BLOCKED_<first reason>`, and `blocking_reasons` lists every reason.

| Reason | Condition |
|---|---|
| `SOURCE_KEY_DUPLICATED` | The `id` is NULL or duplicated. |
| `CODE_MISSING` / `CODE_DUPLICATED` | `code_product` is blank, or several rows share it. Copies per tier or location are common, and the owner picks one. |
| `VOLUME_UNREADABLE` / `VOLUME_OUT_OF_RANGE` | The value is not a GiB number, or it is above NEXA's maximum. |
| `DURATION_UNREADABLE` / `DURATION_OUT_OF_RANGE` | The value is not an integer number of days, or it is above 3650. |
| `PRICE_UNREADABLE` | The price is not an integer. |
| `AGENT_TIER_UNKNOWN` | The tier is not `f`, `n` or `n2`. |
| `PANEL_SCOPE_MISSING` / `PANEL_SCOPE_UNRESOLVED` / `PANEL_SCOPE_AMBIGUOUS` | `Location` is empty, matches no `name_panel`, or matches several. When the dump has no registry, the scope is `NO_REGISTRY` and does not block. |
| `HIDE_PANEL_UNREADABLE` | `hide_panel` is not a JSON list or object. |
| `ONE_BUY_STATUS_UNKNOWN` | The value is not `0` or `1`. |

Each blocked row also gets a `PRODUCT_PROPOSAL_BLOCKED` review (warning). Even a `READY_FOR_OWNER_REVIEW` proposal always lists `owner_decisions_required`: `NEXA_TARIFF_PRICE`, `NEXA_PANEL_FOR_SCOPE`, `NEXA_CATEGORY` and `APPROVE_NEW_OR_EXISTING`.

`invoices_by_class` gives, per `code_product`, the invoice counts by status class (`docs/PANELS_AND_ADOPTION.md` §2). The owner can use it to judge which products still have live services and need a renewal target.

## 3. `reconciliation.product_mapping`

The section reports `rows`, `by_status`, `blocking_reasons`, `auto_created: 0`, `sellable: 0` and `sale_status_column_read`.

The real backup has 64 products and 7 categories. That is a baseline only. Expected counts are never hard-coded.

## 4. Owner decisions

* **OD-PROD-1**: Confirm that the deployed fork keeps MP's units, GiB for `Volume_constraint` and days for `Service_time`. The fork's invoices carry `time_unit`, and `month` exists there (NEXA `UNSUPPORTED_SHAPE`), so a fork product may also have a unit column that the public DDL lacks.
* **OD-PROD-2**: How should a product duplicated per tier or location (`CODE_DUPLICATED`) be handled: one NEXA product with tier prices, or one product per tier?
* **OD-PROD-3**: What should replace "first purchase only" (`one_buy_status = 1`) and `hide_panel` in NEXA, if anything?
* **OD-PROD-4**: Add `status_product` to the product read set, so sold-out products can be recognized? This is a core change, owned by the lead.

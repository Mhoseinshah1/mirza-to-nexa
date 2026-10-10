# Domain modules: how a Mirza table becomes migration records

Phase 2 handles the whole Mirza schema through **catalogue + modules**:

* `src/mirza2nexa/catalogue.py` defines one `TableSpec` per Mirza table, covering:
  * its class: `OPERATIONAL`, `ARCHIVE`, `REVIEW`, `CONFIGURATION` or `EXCLUDED`;
  * its columns and the sensitivity of each: `public`, `personal`, `financial` or `secret`;
  * its evidence, its NEXA target and its open questions.
* `src/mirza2nexa/modules/<domain>.py` registers the specs of its tables and a `Module`, whose
  `run(ctx)` converts them. A domain module also registers a synthetic-data hook (`synth.register_synth`)
  so tests and the performance run have realistic tables.
* `modules/generic.py` exports every catalogued table whose spec says `module="generic"` as raw,
  redacted archive or configuration rows.
* `convert.Converter._finish_coverage` writes one coverage row per table in the dump. Non-empty
  tables that are not catalogued block the package with `UNCLASSIFIED_TABLE`.

## Module contract

These rules are enforced by `tests/test_modules_contract.py`:

1. **Never mutate live state.** Every record either describes history or proposes a decision. Flags
   such as `affects_wallet`, `creates_payment`, `creates_order`, `provision` and
   `applies_to_live_state` are present and `false` wherever they apply.
2. **Records have a fixed shape.** Every record has these fields:
   * `record_type`;
   * `schema` (`mirza.<table>.v<N>` or a domain schema name);
   * `idempotency_key`, starting with `legacy:` and unique within the package;
   * `provenance`, from `ctx.provenance(table, pk, checksum)`.
3. **No secrets.**
   * Secret columns are never staged.
   * Free text from columns whose meaning is not proven goes through `ctx.redact()`.
   * Reports and review data carry codes, counts and identifiers. They never carry credentials.
4. **Money is never re-scaled.** Use `ctx.money(int)`, which gives `{amount_minor, currency}`, and keep
   the raw source text next to it. Amounts that can't be parsed stay raw, with a parse note.
5. **Unknown is a value.** A status, method or type that the evidence does not define maps to
   `UNKNOWN`, with the raw value kept. It is never guessed.
6. **Every row is accounted for.** For each table, call
   `ctx.coverage(table, exported=…, archived=…, review=…, skipped=…, status=…)`. `status` is one of
   `executed`, `archived`, `review`, `blocked`, `empty` or `excluded`. The counts must add up to the
   staged row count.
7. **Stay deterministic.** Read rows with `ctx.rows(stats, order_by=[…])`, which uses primary-key byte
   order. Build no sets that are iterated in hash order.
8. **Mark findings.**
   * Use `ctx.review(...)` for record-level findings.
   * Use `ctx.block(code, n, message_fa)` only for findings that must stop an import, such as a
     financial contradiction.
   * Use `ctx.warn(...)` for package-level notes.

## Record shapes (package schema `1.2.0`; 1.2.0 additions in `PACKAGE_CONTRACT.md`)

| File | `record_type` | Owner |
|---|---|---|
| `records/customers.jsonl`, `wallet_opening_balances.jsonl`, `legacy_debts.jsonl`, `products.jsonl`, `categories.jsonl`, `invoices.jsonl`, `services.jsonl`, `panels.jsonl` | (Phase 1 shapes, unchanged) | `convert.py` |
| `records/archive/<table>.jsonl` | `legacy_archive_row` | `modules/generic.py` |
| `records/configuration/<table>.jsonl` | `legacy_configuration_row` | `modules/generic.py` |
| domain files (`records/payments.jsonl`, `records/wallet_history.jsonl`, …) | see each module's docstring and `docs/PACKAGE_CONTRACT.md` | domain modules |
| `reports/coverage.json` | per-table decision and counts | `convert.py` |

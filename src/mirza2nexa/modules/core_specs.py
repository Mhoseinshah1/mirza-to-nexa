"""Catalogue entries for the three tables of NEXA's frozen legacy read set (converted by convert.py)."""

from __future__ import annotations

from ..catalogue import CATALOGUE, FINANCIAL, OPERATIONAL, PERSONAL, PUBLIC, SECRET, ColumnSpec, TableSpec
from ..mapping import rules

_SENS = {
    "user": {"Balance": FINANCIAL, "username": PERSONAL, "number": PERSONAL, "affiliates": PERSONAL,
             "maxbuyagent": FINANCIAL},
    "invoice": {"price_product": FINANCIAL, "username": PERSONAL, "id_user": PERSONAL, "note": PERSONAL,
                "refral": PERSONAL},
    "product": {"price_product": FINANCIAL},
}


def _cols(table: str) -> tuple[ColumnSpec, ...]:
    names = rules.REQUIRED_COLUMNS[table] + rules.OPTIONAL_COLUMNS[table]
    cols = [ColumnSpec(n, _SENS[table].get(n, PUBLIC)) for n in names]
    cols += [ColumnSpec(n, SECRET, "may authenticate to a panel/bot; never read") for n in sorted(rules.NEVER_READ[table])]
    return tuple(cols)


for _t, _purpose, _fa, _target in (
    ("user", "Telegram customers with wallet balance", "مشتریان تلگرام و موجودی کیف پول",
     "customer + MIGRATION_OPENING_BALANCE / legacy_wallet_debts"),
    ("invoice", "Purchases/service accounts (one row per sold service)", "خریدها و سرویس‌ها",
     "legacy_invoice_archive + legacy_service_candidates"),
    ("product", "Sellable plans", "پلن‌های فروش", "legacy_product_reviews"),
):
    CATALOGUE.register(TableSpec(
        name=_t, klass=OPERATIONAL, module="core", purpose=_purpose, purpose_fa=_fa,
        reason="NEXA frozen legacy read set v1 (source-port.ts:36-83)", nexa_target=_target,
        evidence=("nexa-bot@9e604def source-port.ts:36-83",), primary_key=(rules.PRIMARY_KEYS[_t],),
        columns=_cols(_t), sensitivity=FINANCIAL,
    ))

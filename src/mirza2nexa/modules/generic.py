"""Generic archive / configuration export for catalogued tables without a dedicated converter.

A row becomes one ``legacy_archive_row`` (or ``legacy_configuration_row``) record holding the
stored (never secret) columns as raw text, every value passed through value-level redaction.
Nothing here is applied to any live NEXA entity: these records are history for operators.
"""

from __future__ import annotations

import hashlib
from typing import Any

from ..catalogue import ARCHIVE, CATALOGUE, CONFIGURATION, EXCLUDED, REVIEW
from ..sensitivity import column_name_is_secret
from .base import Module, ModuleContext, register_module

ARCHIVE_SCHEMA_VERSION = "v1"


def pk_is_sensitive(pk: tuple[str, ...]) -> bool:
    """A primary key made of a code/secret-looking column must never be written in clear."""
    return any(column_name_is_secret(c) or "code" in c.lower() for c in pk)


#: Longest ``legacy:<table>:<pk>`` key kept verbatim; NEXA's limit is 512, and suffixes such as
#: ``#dup<n>`` still have to fit.
MAX_KEY_LENGTH = 400


def idempotency_key(table: str, stats_pk: tuple[str, ...], row: dict[str, Any], checksum: str,
                    seen: dict[str, int], mask=None) -> tuple[str, str | None]:
    """``legacy:<table>:<pk…>`` when a usable primary key exists, else a content key.

    Content keys add an occurrence counter so identical rows stay distinct (``…#2``).
    Returns (key, source_pk_text).
    """
    if stats_pk and all(row.get(c) is not None for c in stats_pk):
        pk_text = "|".join(str(row[c]) for c in stats_pk)
        if mask is not None and pk_is_sensitive(stats_pk):
            pk_text = mask(pk_text)
        key = f"legacy:{table}:{pk_text}"
        if len(key) > MAX_KEY_LENGTH:
            # NEXA refuses a key over 512 characters, and a VARCHAR(500) key can hold one: an oversized
            # key is replaced by a digest of the same text; the full value stays in source_pk.
            key = f"legacy:{table}:pkh:{hashlib.sha256(pk_text.encode()).hexdigest()}"
        return key, pk_text
    n = seen.get(checksum, 0) + 1
    seen[checksum] = n
    return f"legacy:{table}:row:{checksum[:32]}" + (f"#{n}" if n > 1 else ""), None


def export_rows(ctx: ModuleContext, stats, *, record_type: str, rel_dir: str, klass: str) -> int:
    sink = ctx.open(f"records/{rel_dir}/{stats.name}.jsonl")
    seen: dict[str, int] = {}
    keys: set[str] = set()
    n = 0
    dup = 0
    for _rowno, row in ctx.rows(stats):
        ctx.tick()
        checksum = ctx.row_checksum(stats.name, row)
        key, pk = idempotency_key(stats.name, stats.primary_key, row, checksum, seen,
                                  mask=lambda v: ctx.secret_ref(v, "pk"))
        if key in keys:  # duplicate primary key in the dump
            dup += 1
            key = f"{key}#dup{dup}"
        keys.add(key)
        sink.write({
            "record_type": record_type,
            "schema": f"mirza.{stats.name}.{ARCHIVE_SCHEMA_VERSION}",
            "idempotency_key": key,
            "class": klass,
            "source_table": stats.name,
            "source_pk": pk,
            "fields": {c: ctx.redact(v, stats.name) for c, v in row.items()},
            "applies_to_live_state": False,
            "provenance": ctx.provenance(stats.name, pk, checksum),
        })
        n += 1
    if dup:
        ctx.review(kind="archive", code="ARCHIVE_DUPLICATE_PK", entity="archive", table=stats.name, pk=None,
                   severity="warning", data={"duplicates": dup},
                   message="کلید اصلی تکراری در جدول آرشیوی؛ همه ردیف‌ها با پسوند متمایز حفظ شدند.")
    return n


def run(ctx: ModuleContext) -> None:
    for spec in sorted(CATALOGUE.specs.values(), key=lambda s: s.name):
        if spec.module != "generic":
            continue
        for stats in ctx.tables_for(spec.name):
            if spec.klass == EXCLUDED:
                ctx.coverage(stats.name, skipped=stats.rows, status="excluded")
                continue
            if spec.klass == CONFIGURATION:
                n = export_rows(ctx, stats, record_type="legacy_configuration_row", rel_dir="configuration",
                                klass=spec.klass)
            else:
                n = export_rows(ctx, stats, record_type="legacy_archive_row", rel_dir="archive", klass=spec.klass)
            status = {ARCHIVE: "archived", REVIEW: "review", CONFIGURATION: "archived"}[spec.klass]
            ctx.coverage(stats.name, archived=n, status=status)
            ctx.count("archive" if spec.klass != CONFIGURATION else "configuration", "converted", n)
            if spec.klass == REVIEW and n:
                ctx.review(kind="table", code="TABLE_NEEDS_DECISION", entity="archive", table=stats.name, pk=None,
                           severity="warning", data={"rows": n, "open_questions": list(spec.open_questions)},
                           message="معنای این جدول قطعی نیست؛ داده‌ها فقط آرشیو شدند و تصمیم مالک لازم است.")


register_module(Module(key="generic", title_fa="آرشیو و تنظیمات عمومی", run=run, order=900))

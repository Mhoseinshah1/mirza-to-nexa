"""Schema export: table/column *structure* of a Mirza backup, with no data and no secrets.

    mirza2nexa schema BACKUP.zip -o schema.json [--password-env VAR | --ask-password]

Used to extend the catalogue for a deployed Mirza fork without ever sharing the backup. The
backup goes through the same archive intake as a conversion (structure checks, bounded streaming
extraction into a private workspace that is deleted afterwards). Then two non-executing passes run
over each SQL file:

1. :func:`~mirza2nexa.sqldump.scan_schema` — ``CREATE TABLE`` / ``ALTER TABLE`` only, INSERT bodies
   skipped;
2. a counting pass with :class:`~mirza2nexa.sqldump.DumpReader` that increments a per-table counter
   for every row event and discards the values immediately (nothing is kept or written).

What leaves (per table): name, columns ``{name, type, nullable, default_present}``, primary key,
unique keys, row count and the catalogue status (class or ``UNCATALOGUED``). What never leaves:
cell values, DEFAULT expressions (``default_present`` is a bool; ``DEFAULT NULL`` counts as no
default), column/table COMMENTs, CHECK expressions, file names inside the ZIP, hashes of the dump,
the password. The parser does not record table charset/collation, so those are not exported.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import __version__
from .archive import (
    DEFAULT_LIMITS,
    ArchiveLimits,
    cleanup_workspace,
    extract_sql,
    inspect_archive,
    secure_workspace,
)
from .errors import SqlParseError
from .sqldump import DumpReader, RowEvent, TableDef, scan_schema

FORMAT = "mirza2nexa.schema-export"
FORMAT_VERSION = "1.0.0"
UNCATALOGUED = "UNCATALOGUED"

NOTICE_FA = ("این فایل فقط نام جدول‌ها و ستون‌ها، نوع ستون‌ها، کلیدها و تعداد ردیف‌ها را دارد و هیچ مقدار یا "
             "مقدار پیش‌فرضی از بکاپ در آن نیست. با این حال پیش از ارسال برای دیگران، آن را خودتان باز کنید و "
             "بررسی کنید.")


def _column(c: Any) -> dict[str, Any]:
    return {"name": c.name, "type": c.type, "nullable": bool(c.nullable), "default_present": c.default is not None}


def _count_rows(path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for ev in DumpReader(path).events():
        if isinstance(ev, RowEvent):
            counts[ev.table] = counts.get(ev.table, 0) + 1
    return counts


def _catalogue_status(name: str) -> dict[str, Any]:
    from .catalogue import CATALOGUE

    spec = CATALOGUE.lookup(name)
    if spec is None:
        return {"catalogue": UNCATALOGUED}
    out: dict[str, Any] = {"catalogue": spec.klass, "module": spec.module}
    if spec.name != name:
        out["catalogue_table"] = spec.name
    return out


def schema_from_sql(paths: list[Path]) -> dict[str, Any]:
    """Build the export document from already-extracted SQL files (no values are retained)."""
    from .modules import verify_registry

    verify_registry()  # catalogue status would be meaningless with modules missing
    defs: dict[str, TableDef] = {}
    differs: set[str] = set()
    counts: dict[str, int] = {}
    count_error: str | None = None
    for p in paths:
        for name, tdef in scan_schema(p).items():
            if name in defs and defs[name] != tdef:
                differs.add(name)
            defs.setdefault(name, tdef)
        if count_error is None:
            try:
                for t, n in _count_rows(p).items():
                    counts[t] = counts.get(t, 0) + n
            except SqlParseError as e:  # structure is still useful; counts are not trustworthy
                count_error = e.code
    tables = []
    for name in sorted(set(defs) | set(counts)):
        tdef = defs.get(name)
        entry: dict[str, Any] = {
            "name": name,
            "create_table_found": tdef is not None,
            "columns": [_column(c) for c in tdef.columns] if tdef else [],
            "primary_key": list(tdef.primary_key) if tdef else [],
            "unique_keys": [list(u) for u in tdef.unique_keys] if tdef else [],
            "row_count": None if count_error else counts.get(name, 0),
        }
        if name in differs:
            entry["definitions_differ_between_files"] = True
        entry.update(_catalogue_status(name))
        tables.append(entry)
    uncat = [t for t in tables if t["catalogue"] == UNCATALOGUED]
    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "tool": {"name": "mirza2nexa", "version": __version__},
        "contains": "table/column names, column types, nullability, key columns, row counts, catalogue status; "
                    "no values, no DEFAULT expressions, no comments",
        "notice_fa": NOTICE_FA,
        "sql_files": len(paths),
        "row_count_error": count_error,
        "summary": {
            "tables": len(tables),
            "catalogued": len(tables) - len(uncat),
            # policy (convert.py): an empty uncatalogued table is reported 'empty'; a non-empty one blocks
            "uncatalogued_empty": [t["name"] for t in uncat if t["row_count"] == 0],
            "uncatalogued_blocking": [t["name"] for t in uncat if t["row_count"] != 0],
        },
        "tables": tables,
    }


def export_schema(archive: Path, output: Path, *, password: str | None = None,
                  limits: ArchiveLimits = DEFAULT_LIMITS, workdir: Path | None = None,
                  stage: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Inspect + extract ``archive`` into a private workspace, export its schema to ``output``."""
    stage = stage or (lambda _s: None)
    ws = secure_workspace(workdir)
    try:
        stage("inspect")
        inspect_archive(Path(archive), limits)
        stage("extract")
        sql_dir = ws / "sql"
        sql_dir.mkdir()
        extracted = extract_sql(Path(archive), sql_dir, password, limits)
        password = None
        stage("schema")
        doc = schema_from_sql([e.path for e in extracted])
    finally:
        cleanup_workspace(ws)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_name(output.name + ".part")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(output)
    return doc

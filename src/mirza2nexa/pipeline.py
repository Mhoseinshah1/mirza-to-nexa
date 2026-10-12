"""End-to-end conversion: ZIP → safe extraction → streaming parse → mapping → reconciliation → .nxpkg.

``convert_archive`` is the engine used by both the web UI (through :func:`run_job`) and the
CLI. It never connects to a database or network and never executes the SQL it reads.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import CONVERTER_NAME, __version__
from .archive import ArchiveLimits, _check_disk, extract_sql, inspect_archive
from .convert import BLOCKER_MESSAGES, MAPPING_VERSION, WARNING_MESSAGES, Converter
from .diff import diff_records
from .nxpkg import PackageWriter, generate_key_file
from .nxpkg.manifest import MIN_IMPORTER_VERSION, PACKAGE_SCHEMA, PACKAGE_SCHEMA_VERSION
from .progress import CancelToken
from .staging import detect_profile, source_fingerprint, stage_dumps

StageFn = Callable[[str, int | None], None]
ProgressCb = Callable[[int, int | None], None]


@dataclass
class ConvertOptions:
    password: str | None = None
    money_unit: str | None = None  # "toman" | "rial" | None (unconfirmed → blocked)
    package_passphrase: str | None = None  # None → generate a random key file
    previous_records_dir: Path | None = None
    previous_import_id: str | None = None
    limits: ArchiveLimits = field(default_factory=ArchiveLimits)
    delete_sql_after_parse: bool = True
    kdf_n: int = 2**17
    created_at: str | None = None
    # Operator-selected NEXA target panel per legacy code_panel (panel_targets.py); never inferred.
    panel_targets: list[Any] = field(default_factory=list)


@dataclass
class ConvertResult:
    package_path: Path
    key_text: str | None
    report: dict[str, Any]
    report_path: Path
    stage_dir: Path


def _import_id(fingerprint: str, money_unit: str | None) -> str:
    """Same backup + same mapping version + same declared unit ⇒ same import id."""
    raw = f"mirza2nexa-import:v1:{fingerprint}:{MAPPING_VERSION}:{money_unit or '-'}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _verify_written(stage_dir: Path) -> dict[str, int]:
    """Independently re-read the written financial records and recompute totals."""
    totals = {"credits_count": 0, "credits_amount": 0, "debts_count": 0, "debts_amount": 0}
    keys: set[str] = set()
    dup = 0
    for fname, prefix in (("wallet_opening_balances.jsonl", "credits"), ("legacy_debts.jsonl", "debts")):
        path = stage_dir / "records" / fname
        if not path.exists():
            continue
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                rec = json.loads(line)
                amount = int(rec["amount_minor"])
                if amount <= 0:
                    raise AssertionError("non-positive financial record")
                tg = rec.get("telegram_user_id") or rec.get("legacy_user_id")
                if tg in keys:
                    dup += 1
                keys.add(tg)
                totals[f"{prefix}_count"] += 1
                totals[f"{prefix}_amount"] += amount
    totals["customers_with_two_money_records"] = dup
    return totals


_SQL_READABLE = """
WITH b AS (SELECT trim(c_Balance, ' ' || char(9, 10, 11, 12, 13)) AS t FROM src_user)
SELECT t FROM b WHERE
  (t GLOB '[0-9]*' AND t NOT GLOB '*[^0-9]*' AND length(t) BETWEEN 1 AND 19)
  OR (t GLOB '-[0-9]*' AND substr(t, 2) NOT GLOB '*[^0-9]*' AND length(t) BETWEEN 2 AND 20)
"""


def _source_recount(db_path: Path) -> tuple[int, int]:
    """Readable-balance count and sum straight from the staged source, using SQLite GLOB
    matching — a code path independent of the converter's regex parsing."""
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        n = total = 0
        for (t,) in conn.execute(_SQL_READABLE):
            n += 1
            total += int(t)
        return n, total
    finally:
        conn.close()


def _cleanup_plaintext(workspace: Path) -> None:
    """Remove every derived plaintext artefact (extracted SQL, staging DB, staged records)."""
    import shutil

    for name in ("sql", "stage", "out"):
        shutil.rmtree(workspace / name, ignore_errors=True)
    for name in ("staging.sqlite", "staging.sqlite-journal", "diff.sqlite", "snapshot.sqlite"):
        (workspace / name).unlink(missing_ok=True)


def convert_archive(archive: Path, workspace: Path, opts: ConvertOptions, *,
                    cancel: CancelToken | None = None, stage: StageFn | None = None,
                    progress: ProgressCb | None = None, on_inspection: Callable[[Any], None] | None = None,
                    ) -> ConvertResult:
    try:
        return _convert_archive(archive, workspace, opts, cancel=cancel, stage=stage, progress=progress,
                                on_inspection=on_inspection)
    except BaseException:
        _cleanup_plaintext(workspace)
        raise
    finally:
        opts.password = None
        opts.package_passphrase = None


def _convert_archive(archive: Path, workspace: Path, opts: ConvertOptions, *,
                    cancel: CancelToken | None = None, stage: StageFn | None = None,
                    progress: ProgressCb | None = None, on_inspection: Callable[[Any], None] | None = None,
                    ) -> ConvertResult:
    cancel = cancel or CancelToken()
    stage = stage or (lambda _s, _t: None)
    progress = progress or (lambda _d, _t: None)
    started = time.time()

    # Fail closed before touching the archive: every converter module and table spec must be
    # registered (a frozen build once shipped without them and silently migrated nothing).
    from .modules import verify_registry

    catalogue_status = verify_registry()

    stage("inspect", None)
    insp = inspect_archive(archive, opts.limits, cancel)
    if on_inspection:
        on_inspection(insp)

    stage("extract", None)
    sql_dir = workspace / "sql"
    sql_dir.mkdir(exist_ok=True)
    extracted = extract_sql(archive, sql_dir, opts.password, opts.limits, cancel, progress)
    opts.password = None

    # Key for references to secret values (see ModuleContext.secret_ref): derived from the raw
    # dump bytes under a domain prefix, so it is deterministic per backup but not computable from
    # the public sha256 of the files written into report.json.
    ref_hash = hashlib.sha256(b"m2n-secret-ref-key:v1\0")
    for e in extracted:
        with open(e.path, "rb") as fh:
            for chunk in iter(lambda fh=fh: fh.read(1 << 20), b""):
                ref_hash.update(chunk)
        cancel.check()
    ref_key = ref_hash.digest()

    stage("schema", None)
    db_path = workspace / "staging.sqlite"
    db_path.unlink(missing_ok=True)
    # Raw source snapshot (contract 1.4.0): collected in the same single parse as staging.
    from .source_snapshot import SnapshotCollector

    snapshot = SnapshotCollector(workspace / "snapshot.sqlite")
    snapshot.disk_check = lambda: _check_disk(workspace, 0, opts.limits.min_free_disk_margin)
    # Any failure before the snapshot is written (staging, schema detection) must release the
    # SQLite handle first: Windows cannot delete an open file during plaintext cleanup.
    try:
        staged = stage_dumps([e.path for e in extracted], db_path, cancel=cancel, progress=progress,
                             observer=snapshot)
        if opts.delete_sql_after_parse:
            for e in extracted:
                e.path.unlink(missing_ok=True)
        profile = detect_profile(staged.tables)
        fingerprint = source_fingerprint(staged.tables, profile)
    except BaseException:
        snapshot.close()
        raise
    import_id = _import_id(fingerprint, opts.money_unit)

    stage("convert", None)
    stage_dir = workspace / "stage"
    writer = PackageWriter(stage_dir)
    sinks: dict[str, Any] = {}

    class _SchemaSink:
        """Every migration record carries a versioned ``schema`` (Phase-1 shapes get ``m2n.<type>.v1``)."""

        def __init__(self, inner):
            self._inner = inner

        def write(self, obj):
            if isinstance(obj, dict) and "record_type" in obj and "schema" not in obj:
                obj = {**obj, "schema": f"m2n.{obj['record_type']}.v1"}
            self._inner.write(obj)

        def close(self):
            self._inner.close()

    def open_jsonl(rel: str):
        rel = rel if rel.startswith("records/") else f"reports/{rel}"
        if rel in sinks:
            return sinks[rel]
        sinks[rel] = _SchemaSink(writer.open_jsonl(rel))
        return sinks[rel]

    try:
        snap = snapshot.write(writer, cancel=cancel)
    finally:
        snapshot.close()

    conv = Converter(db_path, staged.tables, profile, fingerprint, open_jsonl, money_unit=opts.money_unit,
                     cancel=cancel, progress=progress, ref_key=ref_key)
    outcome = conv.run()
    if staged.unrepresentable_cells:
        outcome.block("UNREPRESENTABLE_CELLS", staged.unrepresentable_cells)
    for code, n in sorted(snap.blockers.items()):
        outcome.block(code, n)
    for name in ("records/customers.jsonl", "records/wallet_opening_balances.jsonl", "records/legacy_debts.jsonl"):
        if name in sinks:
            sinks[name].close()

    stage("reconcile", 3)
    written = _verify_written(stage_dir)
    w = outcome.reconciliation["wallet"]
    independent_ok = (
        written["credits_count"] == w["opening_credits"]["count"]
        and str(written["credits_amount"]) == w["opening_credits"]["amount_minor"]
        and written["debts_count"] == w["legacy_debts"]["count"]
        and str(written["debts_amount"]) == w["legacy_debts"]["amount_minor"]
        and written["customers_with_two_money_records"] == 0
    )
    w["independent_recount"] = {k: str(v) for k, v in written.items()}
    w["independent_recount_matches"] = independent_ok
    if not independent_ok:
        outcome.block("FINANCIAL_RECONCILIATION_MISMATCH")
    # Source side, independent of the converter: written credits − written debts + money the
    # converter held back must equal the readable total recomputed from the staged source.
    src_n, src_sum = _source_recount(db_path)
    held = int(w["held_for_review"]["amount_minor"]) + int(w["not_customers_invalid_identity"]["amount_minor"])
    source_ok = (src_sum == written["credits_amount"] - written["debts_amount"] + held
                 and src_n == w["source_readable_balances"]["count"])
    w["source_recount"] = {"readable_count": src_n, "readable_sum_minor": str(src_sum), "matches": source_ok}
    if not source_ok:
        outcome.block("SOURCE_RECOUNT_MISMATCH")
    progress(1, 3)

    diff = None
    if opts.previous_records_dir is not None and opts.previous_records_dir.is_dir():
        review = sinks["reports/review-queue.jsonl"]

        def changed(tg: str, before: int, after: int) -> None:
            review.write({"kind": "wallet", "severity": "blocker", "code": "BALANCE_CHANGED_SINCE_PREVIOUS",
                          "message": "موجودی نسبت به بکاپ قبلی تغییر کرده؛ NEXA تغییر را خودکار اعمال نمی‌کند.",
                          "entity": "customers", "source_ref": {"table": "user", "pk": tg},
                          "data": {"previous_signed_minor": str(before), "current_signed_minor": str(after)}})
            outcome.review_count += 1
            outcome.block("BALANCE_CHANGED_SINCE_PREVIOUS")

        diff = diff_records(opts.previous_records_dir, stage_dir / "records", workspace,
                            previous_import_id=opts.previous_import_id, on_changed_balance=changed)
    progress(2, 3)
    for s in sinks.values():
        s.close()

    # Operator-selected NEXA target panels: an additional record per legacy panel code; the
    # source panel facts and every customer/service record stay exactly as converted.
    from . import panel_targets as _pt

    pt_records, pt_recon, pt_unknown = _pt.build_records(_pt.read_template(stage_dir), list(opts.panel_targets))
    if pt_records:
        pt_sink = _SchemaSink(writer.open_jsonl("records/panel_target_mapping.jsonl"))
        for r in pt_records:
            pt_sink.write(r)
        pt_sink.close()
    outcome.reconciliation["panel_targets"] = pt_recon
    if pt_unknown:
        outcome.block("PANEL_TARGET_UNKNOWN_CODE", len(pt_unknown))

    readiness = "blocked" if outcome.blockers else "ready"
    report = {
        "report_version": "1.0.0",
        "import_id": import_id,
        "source_fingerprint": fingerprint,
        "converter": {"name": CONVERTER_NAME, "version": __version__, "mapping_version": MAPPING_VERSION},
        "source": {
            "archive_sha256": insp.archive_sha256,
            "archive_bytes": insp.archive_bytes,
            "encryption": insp.encryption,
            "sql_files": [{"entry": e.entry_name, "bytes": e.size, "sha256": e.sha256} for e in extracted],
            "parser_warnings": staged.warnings,
            "snapshot": {"synthetic_marker": snap.synthetic_marker, "engine_hint": snap.catalog["engine_hint"],
                         "tables": len(snap.catalog["tables"]), "rows_written": snap.table_files},
            "tables": {n: {"rows": t.rows, "columns": list(t.columns), "classified": t.classified,
                           "never_read_columns_present": list(t.never_read_present)}
                       for n, t in sorted(staged.tables.items())},
        },
        "schema_profile": profile.to_dict(),
        "catalogue": {"modules": catalogue_status["modules"], "specs": catalogue_status["specs"],
                      "expected_tables": catalogue_status["expected_tables"], "ok": catalogue_status["ok"]},
        "readiness": readiness,
        "money_unit": {
            "value": opts.money_unit,
            "source": "operator" if opts.money_unit else None,
            "note": "NEXA owner decision 7 (docs/legacy-product-review-design.md:309-314): Mirza amounts are Toman "
                    "→ IRT. Not proven from data; the operator must confirm. Amounts are never re-scaled.",
        },
        "counts": outcome.counts,
        "reconciliation": outcome.reconciliation,
        "resellers": outcome.resellers,
        "blockers": [{"code": k, "message": outcome.blocker_messages.get(k, BLOCKER_MESSAGES.get(k, k)), "count": v}
                     for k, v in sorted(outcome.blockers.items())],
        "warnings": [{"code": k, "message": outcome.warning_messages.get(k, WARNING_MESSAGES.get(k, k)), "count": v}
                     for k, v in sorted(outcome.warnings.items())],
        "coverage": outcome.coverage,
        "review_count": outcome.review_count,
        "mapping": outcome.mapping,
        "diff": diff,
        "elapsed_seconds": None,
    }
    writer.write_json("reports/reconciliation.json", outcome.reconciliation)
    writer.write_json("reports/coverage.json", {"tables": outcome.coverage})
    # Review taxonomy + migration readiness (aggregates only, no PII); also written next to the
    # package so the UI and the operator can read them without decrypting anything.
    from . import readiness as _readiness
    from .review_taxonomy import summarise_file

    taxonomy = summarise_file(stage_dir / "reports" / "review-queue.jsonl")
    report["review_summary"] = taxonomy
    readiness_doc = _readiness.build(report, taxonomy, package_contract_version=PACKAGE_SCHEMA_VERSION)
    writer.write_json("reports/review-taxonomy.json", taxonomy)
    writer.write_json("reports/migration-readiness.json", readiness_doc)
    writer.write_json("reports/mapping.json", {"mapping_version": MAPPING_VERSION, "entities": outcome.mapping,
                                                "schema_profile": profile.to_dict()})
    if diff is not None:
        writer.write_json("reports/diff.json", diff)

    stage("package", None)
    out_dir = workspace / "out"
    out_dir.mkdir(exist_ok=True)
    key_text = None
    if opts.package_passphrase:
        secret = {"passphrase": opts.package_passphrase}
    else:
        key_text = generate_key_file()
        secret = {"key": key_text}
    manifest = {
        "package_schema": PACKAGE_SCHEMA,
        "package_schema_version": PACKAGE_SCHEMA_VERSION,
        "import_id": import_id,
        "source_fingerprint": fingerprint,
        "source_system": "mirza",
        "schema_profile": profile.id,
        "mapping_version": MAPPING_VERSION,
        "readiness": readiness,
        "blockers": [b["code"] for b in report["blockers"]],
        "money": {"declared_unit": opts.money_unit, "currency": {"toman": "IRT", "rial": "IRR"}.get(opts.money_unit or ""),
                  "rescaled": False},
        "converter": {"name": CONVERTER_NAME, "version": __version__},
        "created_at": opts.created_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "compatibility": {"min_importer_version": MIN_IMPORTER_VERSION, "target": "nexa",
                          "nexa_reference_commit": "9e604def867fb9717408f023c3ea05426732d447",
                          "verified_by_importer": False},
        "record_counts": {k: v.get("converted", 0) for k, v in sorted(outcome.counts.items())},
    }
    pkg_path = out_dir / f"nexa-restore-mirza-{import_id[:12]}.nxpkg"
    result = writer.finalize(pkg_path, manifest=manifest, cancel=cancel, progress=progress, kdf_n=opts.kdf_n,
                             **secret)
    secret.clear()
    opts.package_passphrase = None
    report["package"] = {"file": pkg_path.name, "bytes": result.size, "payload_sha256": result.payload_sha256,
                         "secret": "passphrase" if key_text is None else "key_file"}
    report["elapsed_seconds"] = round(time.time() - started, 2)
    report_path = out_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    readiness_doc["generated_at"] = manifest["created_at"]
    (out_dir / "migration-readiness.json").write_text(json.dumps(readiness_doc, ensure_ascii=False, indent=1),
                                                      encoding="utf-8")
    db_path.unlink(missing_ok=True)
    return ConvertResult(pkg_path, key_text, report, report_path, stage_dir)


def run_job(job, options: dict[str, Any]) -> None:
    """Adapter used by :class:`mirza2nexa.jobs.JobManager`."""
    from .jobs import JobManager  # noqa: F401 - type context only

    settings = options.pop("_settings", None)
    prev_dir = None
    prev_import = None
    prev = options.get("previous_job")
    if prev is not None:
        prev_dir = prev.workspace / "stage" / "records"
        prev_import = (prev.summary or {}).get("import_id")
    opts = ConvertOptions(
        password=options.get("password"),
        money_unit=options.get("money_unit") if options.get("money_unit") in {"toman", "rial"} else None,
        package_passphrase=options.get("package_passphrase") if options.get("package_secret_mode") == "passphrase"
        else None,
        previous_records_dir=prev_dir,
        previous_import_id=prev_import,
        limits=settings.archive_limits() if settings else ArchiveLimits(),
        panel_targets=list(options.get("panel_targets") or []),
        delete_sql_after_parse=settings.delete_sql_after_parse if settings else True,
    )
    options.clear()
    res = convert_archive(job.archive_path, job.workspace, opts, cancel=job.cancel,
                          stage=job.set_stage, progress=job.progress)
    r = res.report
    job.package_path = res.package_path
    job.key_text = res.key_text
    job.summary = {
        "import_id": r["import_id"],
        "source_fingerprint": r["source_fingerprint"],
        "schema_profile": r["schema_profile"]["id"],
        "readiness": r["readiness"],
        "counts": {k: v.get("converted", 0) for k, v in r["counts"].items()},
        "blocker_count": len(r["blockers"]),
        "review_count": r["review_count"],
    }

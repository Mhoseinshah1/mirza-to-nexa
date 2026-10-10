"""Headless command line (same engine as the UI).

    mirza2nexa convert BACKUP.zip -o OUT_DIR [--money-unit toman|rial] [--ask-password] [--previous-package P --previous-key K]
    mirza2nexa verify PACKAGE.nxpkg --key-file KEY.nxkey | --ask-passphrase
    mirza2nexa compare OLD.nxpkg NEW.nxpkg --old-key-file A --new-key-file B
    mirza2nexa synth OUT.zip [--users N] [--password-env VAR]
    mirza2nexa schema BACKUP.zip -o schema.json [--ask-password | --password-env VAR]

Secrets are read interactively (``getpass``) or from an environment variable named on the
command line — never from argv itself, which other local users/processes can see.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import sys
from pathlib import Path

from .archive import cleanup_workspace, secure_workspace
from .errors import MigrationError
from .panel_targets import parse_cli as parse_cli_targets


def _secret(ask: bool, env: str | None, prompt: str) -> str | None:
    if env:
        v = os.environ.get(env)
        if v is None:
            raise SystemExit(f"environment variable {env} is not set")
        return v
    if ask:
        return getpass.getpass(prompt)
    return None


def _key_args(path: str | None, ask: bool, env: str | None) -> dict[str, str]:
    if path:
        return {"key": Path(path).read_text(encoding="utf-8").strip()}
    pp = _secret(ask, env, "Package passphrase: ")
    if pp:
        return {"passphrase": pp}
    raise SystemExit("a key file or passphrase is required")


def cmd_convert(a: argparse.Namespace) -> int:
    from .nxpkg import PackageReader
    from .pipeline import ConvertOptions, convert_archive

    out_dir = Path(a.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    ws = secure_workspace(Path(a.workdir) if a.workdir else None)
    prev_ws = None
    try:
        pp_check = _secret(False, a.package_passphrase_env, "") if a.package_passphrase_env else None
        if pp_check is not None and len(pp_check) < 12:
            print("error: package passphrase must be at least 12 characters", file=sys.stderr)
            return 1
        opts = ConvertOptions(
            password=_secret(a.ask_password, a.password_env, "ZIP password: "),
            money_unit=a.money_unit,
            panel_targets=parse_cli_targets(a.panel_target or []),
            package_passphrase=_secret(a.ask_package_passphrase, a.package_passphrase_env, "Package passphrase: "),
        )
        if a.previous_package:
            prev_ws = secure_workspace()
            reader = PackageReader(Path(a.previous_package), workdir=prev_ws,
                                   **_key_args(a.previous_key_file, False, a.previous_passphrase_env))
            rep = reader.verify()
            rec_dir = prev_ws / "records"
            rec_dir.mkdir()
            for name in ("customers", "wallet_opening_balances", "legacy_debts", "invoices", "services", "products"):
                rel = f"records/{name}.jsonl"
                if rel in rep.files:
                    with open(rec_dir / f"{name}.jsonl", "w", encoding="utf-8") as fh:
                        for r in reader.iter_jsonl(rel):
                            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            reader.cleanup()
            opts.previous_records_dir = rec_dir
            opts.previous_import_id = rep.import_id

        def stage(name: str, _t: int | None) -> None:
            print(f"[stage] {name}", file=sys.stderr)

        res = convert_archive(Path(a.backup), ws, opts, stage=stage)
        pkg = out_dir / res.package_path.name
        shutil.move(str(res.package_path), pkg)
        (out_dir / "report.json").write_text(json.dumps(res.report, ensure_ascii=False, indent=1), encoding="utf-8")
        readiness_src = res.report_path.parent / "migration-readiness.json"
        if readiness_src.is_file():  # aggregates only (no PII); the workspace is deleted below
            from .readiness_html import render_readiness_html

            readiness_doc = json.loads(readiness_src.read_text(encoding="utf-8"))
            (out_dir / "migration-readiness.json").write_text(
                json.dumps(readiness_doc, ensure_ascii=False, indent=1), encoding="utf-8")
            (out_dir / "migration-readiness.html").write_text(render_readiness_html(readiness_doc), encoding="utf-8")
            print(f"migration readiness: {out_dir / 'migration-readiness.html'}")
        if res.key_text:
            key_path = out_dir / (pkg.stem + ".nxkey")
            key_path.write_text(res.key_text + "\n", encoding="utf-8")
            print(f"key file: {key_path}  (store it separately from the package)")
        print(f"package:  {pkg}")
        print(f"readiness: {res.report['readiness']}  blockers: {[b['code'] for b in res.report['blockers']]}")
        return 0 if res.report["readiness"] == "ready" else 2
    except MigrationError as e:
        print(f"error [{e.code}]: {e.message_fa}", file=sys.stderr)
        return 1
    finally:
        cleanup_workspace(ws)
        if prev_ws:
            cleanup_workspace(prev_ws)


def cmd_verify(a: argparse.Namespace) -> int:
    from .nxpkg import PackageReader

    ws = secure_workspace()
    try:
        reader = PackageReader(Path(a.package), workdir=ws,
                               **_key_args(a.key_file, a.ask_passphrase, a.passphrase_env))
        rep = reader.verify()
        m = rep.manifest
        print(json.dumps({"ok": True, "import_id": m["import_id"], "source_fingerprint": m["source_fingerprint"],
                          "readiness": m.get("readiness"), "blockers": m.get("blockers"),
                          "record_counts": m.get("record_counts"), "files": len(rep.files)},
                         ensure_ascii=False, indent=1))
        return 0
    except MigrationError as e:
        print(json.dumps({"ok": False, "code": e.code, "message": e.message_fa}, ensure_ascii=False))
        return 1
    finally:
        cleanup_workspace(ws)


def cmd_compare(a: argparse.Namespace) -> int:
    from .diff import diff_records
    from .nxpkg import PackageReader

    wss = []
    try:
        dirs = []
        ids = []
        for pkg, keyf, env in ((a.old, a.old_key_file, a.old_passphrase_env), (a.new, a.new_key_file,
                                                                             a.new_passphrase_env)):
            ws = secure_workspace()
            wss.append(ws)
            reader = PackageReader(Path(pkg), workdir=ws, **_key_args(keyf, False, env))
            rep = reader.verify()
            ids.append(rep.import_id)
            rd = ws / "records"
            rd.mkdir()
            for rel in rep.files:
                if rel.startswith("records/") and rel.endswith(".jsonl"):
                    with open(ws / rel, "w", encoding="utf-8") as fh:
                        for r in reader.iter_jsonl(rel):
                            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            dirs.append(rd)
        result = diff_records(dirs[0], dirs[1], wss[1], previous_import_id=ids[0])
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0
    except MigrationError as e:
        print(f"error [{e.code}]: {e.message_fa}", file=sys.stderr)
        return 1
    finally:
        for ws in wss:
            cleanup_workspace(ws)


def cmd_synth(a: argparse.Namespace) -> int:
    from .synth import SynthSpec, write_large_zip

    pw = _secret(False, a.password_env, "")
    exp = write_large_zip(Path(a.output), SynthSpec(users=a.users, seed=a.seed,
                                                     balance_shift_users=a.shift_balances), password=pw)
    print(json.dumps(exp))
    return 0


def cmd_schema(a: argparse.Namespace) -> int:
    """Write table/column structure + row counts (no values, no defaults) to a local JSON file."""
    from .schema_export import NOTICE_FA, export_schema

    try:
        doc = export_schema(Path(a.backup), Path(a.output),
                            password=_secret(a.ask_password, a.password_env, "ZIP password: "),
                            workdir=Path(a.workdir) if a.workdir else None,
                            stage=lambda name: print(f"[stage] {name}", file=sys.stderr))
    except MigrationError as e:
        print(f"error [{e.code}]: {e.message_fa}", file=sys.stderr)
        return 1
    s = doc["summary"]
    print(f"schema:   {Path(a.output)}")
    print(f"tables: {s['tables']}  catalogued: {s['catalogued']}  "
          f"uncatalogued (non-empty, would block): {s['uncatalogued_blocking']}")
    print(NOTICE_FA)
    return 0


def cmd_selfcheck(_a: argparse.Namespace) -> int:
    """Print the module/catalogue registry state; exit 3 when anything is missing (frozen-build check)."""
    from . import __version__
    from .modules import registry_status

    status = {"version": __version__, **registry_status()}
    print(json.dumps(status, ensure_ascii=False))
    return 0 if status["ok"] else 3


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="mirza2nexa")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("convert", help="convert a Mirza backup ZIP into a .nxpkg")
    c.add_argument("backup")
    c.add_argument("-o", "--output", required=True)
    c.add_argument("--money-unit", choices=["toman", "rial"], default=None)
    c.add_argument("--panel-target", action="append", metavar="CODE=rickpanel[,panel_id=UUID][,version=1.0.0]",
                   help="NEXA target panel for a legacy code_panel (repeatable); only rickpanel (RickPanel)")
    c.add_argument("--ask-password", action="store_true")
    c.add_argument("--password-env")
    c.add_argument("--ask-package-passphrase", action="store_true")
    c.add_argument("--package-passphrase-env")
    c.add_argument("--previous-package")
    c.add_argument("--previous-key-file")
    c.add_argument("--previous-passphrase-env")
    c.add_argument("--workdir")
    c.set_defaults(fn=cmd_convert)

    v = sub.add_parser("verify", help="verify integrity/authenticity of a .nxpkg")
    v.add_argument("package")
    v.add_argument("--key-file")
    v.add_argument("--ask-passphrase", action="store_true")
    v.add_argument("--passphrase-env")
    v.set_defaults(fn=cmd_verify)

    d = sub.add_parser("compare", help="diff two packages of the same installation")
    d.add_argument("old")
    d.add_argument("new")
    d.add_argument("--old-key-file")
    d.add_argument("--new-key-file")
    d.add_argument("--old-passphrase-env")
    d.add_argument("--new-passphrase-env")
    d.set_defaults(fn=cmd_compare)

    s = sub.add_parser("synth", help="write a SYNTHETIC Mirza backup zip (test data only)")
    s.add_argument("output")
    s.add_argument("--users", type=int, default=1000)
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--shift-balances", type=int, default=0)
    s.add_argument("--password-env")
    s.set_defaults(fn=cmd_synth)

    sm = sub.add_parser("schema", help="export table/column structure and row counts (no data) to JSON")
    sm.add_argument("backup")
    sm.add_argument("-o", "--output", required=True)
    sm.add_argument("--ask-password", action="store_true")
    sm.add_argument("--password-env")
    sm.add_argument("--workdir")
    sm.set_defaults(fn=cmd_schema)

    sc = sub.add_parser("selfcheck", help="verify every converter module and table spec is present")
    sc.set_defaults(fn=cmd_selfcheck)

    a = p.parse_args(argv)
    return a.fn(a)

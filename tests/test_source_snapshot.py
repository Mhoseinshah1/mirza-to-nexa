"""Raw source snapshot (contract 1.4.0): ``source/catalog.json`` + ``source/tables/*.jsonl``. Synthetic only."""

from __future__ import annotations

import json
import zipfile
from decimal import Decimal
from pathlib import Path

import pytest

from conftest import make_backup, read_jsonl, run
from mirza2nexa import source_snapshot as ss
from mirza2nexa.catalogue import CATALOGUE
from mirza2nexa.mapping import rules
from mirza2nexa.nxpkg.manifest import PACKAGE_SCHEMA_VERSION
from mirza2nexa.sensitivity import column_name_is_secret
from mirza2nexa.sqldump import Column, DumpReader, RowEvent, TableEvent
from mirza2nexa.synth import SYNTHETIC_LABEL


def _lines(path: Path) -> list[dict]:
    return read_jsonl(path)


def _parse_dump(zp: Path, tmp: Path) -> Path:
    with zipfile.ZipFile(zp) as zf:
        name = zf.namelist()[0]
        out = tmp / "dump.sql"
        out.write_bytes(zf.read(name))
    return out


@pytest.fixture(scope="module")
def converted(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("snap")
    zp, exp = make_backup(tmp, users=150)
    res = run(tmp, zp)
    return tmp, zp, exp, res


def test_contract_version_and_files_present(converted):
    _tmp, _zp, _exp, res = converted
    assert PACKAGE_SCHEMA_VERSION == "1.4.0"
    src = res.stage_dir / "source"
    assert (src / "catalog.json").is_file()
    for t in ("user", "invoice", "product"):
        assert (src / "tables" / f"{t}.jsonl").is_file()
    cat = json.loads((src / "catalog.json").read_text(encoding="utf-8"))
    assert cat["snapshot_version"] == 1 and cat["format"] == "m2n.source-catalog.v1"
    assert cat["synthetic_marker"] == SYNTHETIC_LABEL
    assert cat["engine_hint"] == "MYSQL"
    assert res.report["source"]["snapshot"]["synthetic_marker"] == SYNTHETIC_LABEL
    assert not any(b["code"].startswith("SOURCE_SNAPSHOT_") for b in res.report["blockers"])


def test_catalog_lists_every_table_with_exact_row_counts(converted, tmp_path):
    _tmp, zp, _exp, res = converted
    cat = json.loads((res.stage_dir / "source" / "catalog.json").read_text(encoding="utf-8"))
    counts: dict[str, int] = {}
    defs = {}
    for ev in DumpReader(_parse_dump(zp, tmp_path)).events():
        if isinstance(ev, RowEvent):
            counts[ev.table] = counts.get(ev.table, 0) + 1
        elif isinstance(ev, TableEvent):
            defs[ev.table.name] = ev.table
    names = [t["name"] for t in cat["tables"]]
    assert names == sorted(set(defs) | set(counts), key=lambda n: n.encode())
    for t in cat["tables"]:
        assert t["rows"] == counts.get(t["name"], 0), t["name"]
        assert t["table_type"] == "BASE TABLE"
        d = defs[t["name"]]
        assert [c["column"] for c in t["columns"]] == list(d.column_names())
        assert [c["ordinal"] for c in t["columns"]] == list(range(1, len(d.columns) + 1))
        # metadata only: no value of any row in the catalog
        assert set(t) == {"name", "table_type", "storage_engine", "charset", "collation", "rows", "columns"}
        for c in t["columns"]:
            assert set(c) == {"column", "data_type", "ordinal", "charset"}
    user = next(t for t in cat["tables"] if t["name"] == "user")
    assert user["storage_engine"] == "InnoDB" and user["charset"] == "utf8mb4" and user["collation"] == "utf8mb4_bin"
    types = {c["column"]: (c["data_type"], c["charset"]) for c in user["columns"]}
    assert types["id"] == ("varchar", "utf8mb4") and types["limit_usertest"] == ("int", None)
    product = next(t for t in cat["tables"] if t["name"] == "product")
    assert {c["column"]: c["data_type"] for c in product["columns"]}["id"] == "int"
    assert {c["column"]: c["data_type"] for c in product["columns"]}["inbounds"] == "text"


def test_columns_are_exactly_the_present_allowlist(converted):
    _tmp, _zp, _exp, res = converted
    cat = json.loads((res.stage_dir / "source" / "catalog.json").read_text(encoding="utf-8"))
    present = {t["name"]: [c["column"] for c in t["columns"]] for t in cat["tables"]}
    for t in ("user", "invoice", "product"):
        head, *rows = _lines(res.stage_dir / "source" / "tables" / f"{t}.jsonl")
        want = [c for c in ss.ALLOWLIST[t] if c in present[t]]
        assert head == {"format": "m2n.source-table.v1", "table": t, "primary_key": rules.PRIMARY_KEYS[t],
                        "columns": want}
        assert cat["snapshot_tables"][t] == {"file": f"source/tables/{t}.jsonl", "primary_key": rules.PRIMARY_KEYS[t],
                                             "columns": want, "rows": len(rows)}
        assert all(set(r) == {"c"} and len(r["c"]) == len(want) for r in rows)
        assert all(c is None or isinstance(c, str) for r in rows for c in r["c"])


def test_secret_columns_never_in_snapshot(converted):
    _tmp, _zp, _exp, res = converted
    ss.assert_allowlist_safe()
    for t, cols in ss.ALLOWLIST.items():
        spec = CATALOGUE.lookup(t)
        secret = {c.name for c in spec.columns if not c.storable} | set(rules.NEVER_READ[t])
        assert not secret & set(cols)
        assert not any(column_name_is_secret(c) for c in cols)
    assert {"inbounds", "proxies"} <= set(rules.NEVER_READ["product"])
    blob = b"".join(p.read_bytes() for p in (res.stage_dir / "source" / "tables").iterdir())
    for needle in (b"never-read", b"never.read", b"00000000-0000-0000-0000-000000000000", b"inbounds",
                   b"user_info", b"uuid"):
        assert needle not in blob


def test_allowlist_refuses_a_secret_column(monkeypatch):
    monkeypatch.setitem(ss.ALLOWLIST, "product", (*ss.ALLOWLIST["product"], "inbounds"))
    with pytest.raises(RuntimeError):
        ss.assert_allowlist_safe()


def test_allowlist_matches_nexa_read_sets():
    v1 = rules.REQUIRED_COLUMNS
    for t in ("user", "invoice", "product"):
        assert set(v1[t]) <= set(ss.ALLOWLIST[t]), t
    assert ss.ALLOWLIST["user"][:3] == ("id", "Balance", "limit_usertest")


def test_rows_sorted_by_primary_key_bytes_and_cells_equal_the_sql_literals(converted, tmp_path):
    _tmp, zp, _exp, res = converted
    expected: dict[str, dict[str, list]] = {t: {} for t in ss.ALLOWLIST}
    for ev in DumpReader(_parse_dump(zp, tmp_path)).events():
        if isinstance(ev, RowEvent) and ev.table in expected:
            row = dict(zip(ev.columns, ev.values, strict=True))
            expected[ev.table][str(row[rules.PRIMARY_KEYS[ev.table]])] = row
    for t in ("user", "invoice", "product"):
        head, *rows = _lines(res.stage_dir / "source" / "tables" / f"{t}.jsonl")
        cols = head["columns"]
        keys = [r["c"][cols.index(head["primary_key"])] for r in rows]
        assert keys == sorted(keys, key=lambda k: k.encode("utf-8"))
        assert len(set(keys)) == len(keys) == len(expected[t])
        for r in rows:
            src = expected[t][r["c"][cols.index(head["primary_key"])]]
            for c, cell in zip(cols, r["c"], strict=True):
                v = src.get(c)  # absent from an INSERT column list → DEFAULT NULL in the synthetic DDL
                assert cell == (None if v is None else str(v)), (t, c)
    # product ids are int: byte order puts "10" before "2", exactly like ORDER BY CAST(id AS BINARY)
    head, *rows = _lines(res.stage_dir / "source" / "tables" / "product.jsonl")
    ids = [r["c"][0] for r in rows]
    assert ids.index("10") < ids.index("2")
    # a Persian literal and an escaped quote survive verbatim
    names = {r["c"][head["columns"].index("name_product")] for r in rows}
    assert "پلن آزمایشی 1" in names


def test_snapshot_is_deterministic_and_other_records_unchanged(converted, tmp_path, monkeypatch):
    tmp, zp, _exp, res = converted
    again = run(tmp_path, zp, ws_name="again")

    def files(stage: Path) -> dict[str, bytes]:
        return {p.relative_to(stage).as_posix(): p.read_bytes() for p in sorted(stage.rglob("*")) if p.is_file()}

    a, b = files(res.stage_dir), files(again.stage_dir)
    for name in a:
        if name.startswith("source/") or name.startswith("records/"):
            assert a[name] == b[name], name
    # Without the snapshot, every record file is byte-identical: the snapshot is purely additive.
    monkeypatch.setattr(ss.SnapshotCollector, "write",
                        lambda self, writer, cancel=None: ss.SnapshotResult(catalog={"engine_hint": None, "tables": []},
                                                               table_files={}))
    plain = run(tmp_path, zp, ws_name="plain")
    c = files(plain.stage_dir)
    assert not any(n.startswith("source/") for n in c)
    assert {n for n in a if n.startswith("records/")} == {n for n in c if n.startswith("records/")}
    for name in c:
        if name.startswith("records/"):
            assert a[name] == c[name], name
    assert res.report["import_id"] == plain.report["import_id"]


def _col(t: str, **kw) -> Column:
    return Column("x", t, kw.get("nullable", True), kw.get("default"))


@pytest.mark.parametrize(("ctype", "value", "text"), [
    ("varchar(10)", "a'b\\c", "a'b\\c"),
    ("varchar(10)", 15, "15"),
    ("varchar(10)", Decimal("1.50"), "1.50"),
    ("varchar(10)", "", ""),
    ("char(5)", "ab  ", "ab"),
    ("text", "سلام", "سلام"),
    ("int(11)", 5, "5"),
    ("int(11)", "-7", "-7"),
    ("bigint(20) unsigned", 18446744073709551615, "18446744073709551615"),
    ("decimal(10,2)", Decimal("5.5"), "5.50"),
    ("decimal(10,2)", "3", "3.00"),
    ("decimal(10,0)", 12, "12"),
    ("datetime", "2025-01-02 03:04:05", "2025-01-02 03:04:05"),
    ("datetime(3)", "2025-01-02 03:04:05.120", "2025-01-02 03:04:05.120"),
    ("date", "0000-00-00", "0000-00-00"),
    ("enum('a','b')", "b", "b"),
    ("varchar(10)", None, None),
])
def test_cast_char_exact(ctype, value, text):
    assert ss.cast_char(value, _col(ctype)) == text


@pytest.mark.parametrize(("ctype", "value"), [
    ("int(11)", "12.5"),
    ("int(11)", ""),
    ("int(11) unsigned", -1),
    ("tinyint(4)", 300),
    ("int(5) zerofill", 5),
    ("decimal(10,2)", Decimal("1.234")),
    ("double", Decimal("1.5")),
    ("float", 1),
    ("json", "{}"),
    ("blob", b"abc"),
    ("varbinary(10)", b"abc"),
    ("bit(1)", b"\x01"),
    ("varchar(3)", "abcd"),
    ("varchar(10)", b"\xff\xfe"),
    ("varchar(10)", Decimal("1E+3")),
    ("datetime", "2025-1-2 3:4:5"),
    ("datetime(3)", "2025-01-02 03:04:05"),
])
def test_cast_char_refuses_what_it_cannot_prove(ctype, value):
    with pytest.raises(ss.Unrepresentable):
        ss.cast_char(value, _col(ctype))


def test_json_on_mariadb_is_longtext():
    assert ss.data_type("json", "MARIADB") == "longtext"
    assert ss.cast_char("{\"a\":1}", _col("json"), "MARIADB") == "{\"a\":1}"
    assert ss.data_type("integer") == "int" and ss.data_type("bool") == "tinyint"
    assert ss.data_type("decimal(10,2) unsigned") == "decimal"


def test_engine_hint_from_header():
    assert ss.engine_hint_from_header(b"-- MySQL dump 10.13  Distrib 8.0.36, for Linux\n") == "MYSQL"
    assert ss.engine_hint_from_header(b"-- MySQL dump 10.19  Distrib 10.6.12-MariaDB, for debian\n") == "MARIADB"
    assert ss.engine_hint_from_header(b"-- MariaDB dump 10.19-11.4.2-MariaDB\n") == "MARIADB"
    assert ss.engine_hint_from_header(b"-- phpMyAdmin SQL Dump\n-- Server version: 8.0.35\n") == "MYSQL"
    assert ss.engine_hint_from_header(b"SET NAMES utf8mb4;\n") is None


def _snapshot_of(tmp: Path, sql: str) -> tuple[ss.SnapshotCollector, dict]:
    from mirza2nexa.nxpkg import PackageWriter

    p = tmp / "d.sql"
    p.write_text(sql, encoding="utf-8")
    col = ss.SnapshotCollector(tmp / "s.sqlite")
    col.on_file(p)
    for ev in DumpReader(p).events():
        if isinstance(ev, TableEvent):
            col.on_table(ev.table)
        elif isinstance(ev, RowEvent):
            col.on_row(ev)
    w = PackageWriter(tmp / "stage")
    res = col.write(w)
    col.close()
    return col, {"result": res, "stage": tmp / "stage"}


_USER = ("CREATE TABLE `user` (`id` varchar(20) NOT NULL, `Balance` {bal} NOT NULL, `limit_usertest` int(11) NOT NULL,"
         " `User_Status` varchar(20) DEFAULT 'Active', `secret_token` varchar(20), PRIMARY KEY (`id`))"
         " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;\n")


def test_binary_allowlisted_column_blocks(tmp_path):
    _c, out = _snapshot_of(tmp_path, _USER.format(bal="varbinary(20)") + "INSERT INTO `user` VALUES ('1',0x31,1,'Active','t');\n")
    assert out["result"].blockers == {ss.BINARY_COLUMN: 1, ss.VALUE_UNREPRESENTABLE: 1}


def test_unprovable_value_and_bad_keys_block(tmp_path):
    sql = _USER.format(bal="int(11)") + (
        "INSERT INTO `user` VALUES ('1','12.5',1,'Active','t'),('2',5,1,NULL,'t'),('2',6,1,NULL,'t');\n"
        "INSERT INTO `user` (`id`,`Balance`,`limit_usertest`) VALUES ('3',7,0);\n")
    _c, out = _snapshot_of(tmp_path, sql)
    assert out["result"].blockers == {ss.VALUE_UNREPRESENTABLE: 1, ss.KEY_INVALID: 1}
    head, *rows = read_jsonl(out["stage"] / "source" / "tables" / "user.jsonl")
    assert head["columns"] == ["id", "Balance", "limit_usertest", "User_Status"]  # secret_token never read
    assert {"c": ["3", "7", "0", "Active"]} in rows  # DEFAULT 'Active' applied for an omitted column
    assert out["result"].synthetic_marker is None
    assert out["result"].catalog["engine_hint"] is None


def test_marker_without_label_still_marks_synthetic(tmp_path):
    sql = _USER.format(bal="int(11)") + "CREATE TABLE `nexa_synthetic_fixture` (`label` varchar(200) NOT NULL);\n"
    _c, out = _snapshot_of(tmp_path, sql)
    assert out["result"].synthetic_marker == ss.UNLABELLED_MARKER
    assert out["result"].table_files == {"user": 0}


def test_catalog_charsets_from_table_and_column_clauses(tmp_path):
    sql = ("CREATE TABLE `t` (`a` varchar(5) CHARACTER SET latin1 COLLATE latin1_bin, `b` text COLLATE utf8mb3_general_ci,"
           " `c` int, `d` decimal(10,2), `e` varchar(3)) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4;\n")
    _c, out = _snapshot_of(tmp_path, sql)
    (t,) = out["result"].catalog["tables"]
    assert (t["storage_engine"], t["charset"], t["collation"]) == ("MyISAM", "utf8mb4", None)
    assert [(c["column"], c["data_type"], c["charset"]) for c in t["columns"]] == [
        ("a", "varchar", "latin1"), ("b", "text", "utf8mb3"), ("c", "int", None), ("d", "decimal", None),
        ("e", "varchar", "utf8mb4")]
    assert out["result"].table_files == {}


def test_snapshot_handle_released_when_schema_detection_fails(tmp_path, monkeypatch):
    """Windows cannot delete an open SQLite file: every failure after staging must close it first."""
    from mirza2nexa.errors import MigrationError

    states: list[bool] = []
    real_close = ss.SnapshotCollector.close

    def spy(self):
        real_close(self)
        states.append(self.conn is None)

    monkeypatch.setattr(ss.SnapshotCollector, "close", spy)
    sql = ("CREATE TABLE `user` (`id` varchar(500), `limit_usertest` int, `Balance` varchar(64));\n"
           "INSERT INTO `user` VALUES ('7100000001',1,'100');\n")
    zp = tmp_path / "bad.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("backup.sql", sql)
    with pytest.raises(MigrationError):
        run(tmp_path, zp)
    assert states and states[0] is True
    assert not (tmp_path / "ws" / "snapshot.sqlite").exists()


# --- Codex review on PR #3 (each test fails when its fix is reverted) --------------------------


@pytest.mark.parametrize(("ctype", "value", "text"), [
    ("decimal(30,2)", Decimal("1234567890123456789012345678.12"), "1234567890123456789012345678.12"),
    ("decimal(65,0)", Decimal("9" * 65), "9" * 65),
    ("decimal(65,30)", Decimal("1." + "0" * 29 + "1"), "1." + "0" * 29 + "1"),
])
def test_cast_char_wide_decimals_beyond_the_default_context(ctype, value, text):
    assert ss.cast_char(value, _col(ctype)) == text


def test_cast_char_wide_decimal_out_of_range_fails_closed():
    with pytest.raises(ss.Unrepresentable):
        ss.cast_char(Decimal("9" * 66), _col("decimal(65,0)"))


def test_engine_hint_survives_a_blocker(tmp_path):
    sql = ("-- MariaDB dump 10.19  Distrib 10.11.6-MariaDB, for debian-linux-gnu (x86_64)\n"
           + _USER.format(bal="int(11)")
           + "INSERT INTO `user` VALUES ('1','12.5',1,'Active','t');\n")
    _c, out = _snapshot_of(tmp_path, sql)
    assert out["result"].blockers == {ss.VALUE_UNREPRESENTABLE: 1}
    assert out["result"].catalog["engine_hint"] == "MARIADB"


def test_snapshot_growth_checks_free_space(tmp_path, monkeypatch):
    from mirza2nexa.errors import DiskSpaceError

    monkeypatch.setattr(ss, "DISK_CHECK_EVERY", 2)
    p = tmp_path / "d.sql"
    p.write_text(_USER.format(bal="int(11)") + "INSERT INTO `user` VALUES "
                 + ",".join(f"('{i}',{i},1,'Active','t')" for i in range(1, 6)) + ";\n", encoding="utf-8")
    col = ss.SnapshotCollector(tmp_path / "s.sqlite")
    calls: list[int] = []

    def full() -> None:
        calls.append(1)
        raise DiskSpaceError(detail="test")

    col.disk_check = full
    col.on_file(p)
    try:
        with pytest.raises(DiskSpaceError):
            for ev in DumpReader(p).events():
                if isinstance(ev, TableEvent):
                    col.on_table(ev.table)
                elif isinstance(ev, RowEvent):
                    col.on_row(ev)
            col.finish()
    finally:
        col.close()
    assert calls


def test_snapshot_write_is_cancellable(tmp_path):
    from mirza2nexa.errors import Cancelled
    from mirza2nexa.nxpkg import PackageWriter
    from mirza2nexa.progress import CancelToken

    p = tmp_path / "d.sql"
    p.write_text(_USER.format(bal="int(11)") + "INSERT INTO `user` VALUES ('1',1,1,'Active','t');\n",
                 encoding="utf-8")
    col = ss.SnapshotCollector(tmp_path / "s.sqlite")
    col.on_file(p)
    for ev in DumpReader(p).events():
        if isinstance(ev, TableEvent):
            col.on_table(ev.table)
        elif isinstance(ev, RowEvent):
            col.on_row(ev)
    token = CancelToken()
    token.cancel()
    try:
        with pytest.raises(Cancelled):
            col.write(PackageWriter(tmp_path / "stage"), cancel=token)
    finally:
        col.close()

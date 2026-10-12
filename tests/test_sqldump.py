from __future__ import annotations

import time
import tracemalloc
from decimal import Decimal
from pathlib import Path

import pytest

from mirza2nexa.errors import Cancelled, SqlParseError
from mirza2nexa.progress import CancelToken
from mirza2nexa.sqldump import (
    Column,
    DumpReader,
    RowEvent,
    TableDef,
    TableEvent,
    WarningEvent,
    scan_schema,
)

CHUNK_SIZES = (1024 * 1024, 7, 1, 64)


def _write(tmp_path: Path, data: str | bytes, name: str = "dump.sql") -> Path:
    p = tmp_path / name
    p.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    return p


def _events(path: Path, chunk: int = 1024 * 1024, **kw) -> list:
    return list(DumpReader(path, _chunk_size=chunk, **kw).events())


def _all_chunkings(path: Path, **kw) -> list:
    """Parse with several chunk sizes and assert identical results (boundary correctness)."""
    ref = _events(path, CHUNK_SIZES[0], **kw)
    for cs in CHUNK_SIZES[1:]:
        assert _events(path, cs, **kw) == ref, f"chunk size {cs} differs"
    return ref


def _rows(events: list, table: str | None = None) -> list[tuple]:
    return [e.values for e in events if isinstance(e, RowEvent) and (table is None or e.table == table)]


def _warnings(events: list) -> list[str]:
    return [e.code for e in events if isinstance(e, WarningEvent)]


MYSQLDUMP = """-- MySQL dump 10.13  Distrib 8.0.36, for Linux (x86_64)
--
-- Host: localhost    Database: mirza
-- ------------------------------------------------------
-- Server version\t8.0.36

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET NAMES utf8mb4 */;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40014 SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0 */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;

--
-- Table structure for table `user`
--

DROP TABLE IF EXISTS `user`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `user` (
  `id` bigint(20) NOT NULL,
  `username` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `balance` decimal(30,2) NOT NULL DEFAULT '0.00',
  `status` enum('active','Blocked') NOT NULL DEFAULT 'active',
  `note` text COMMENT 'it''s; a (comment)',
  `created` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `score` int(11) unsigned DEFAULT -1,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_username` (`username`(191)),
  KEY `idx_status` (`status`),
  CONSTRAINT `fk_x` FOREIGN KEY (`id`) REFERENCES `other` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `user`
--

LOCK TABLES `user` WRITE;
/*!40000 ALTER TABLE `user` DISABLE KEYS */;
INSERT INTO `user` VALUES (1,'ali','12345678901234567890.12','active','it\\'s; (fine)\\nline2',NULL,5),(2,'رضا 🚀','-0.50','Blocked','',NULL,-7),(3,NULL,'0.00','active','say \\"hi\\" \\\\ back','2024-01-01 10:00:00',0);
INSERT INTO `user` VALUES (4,'x''y','1.00','active','a\\0b\\tc\\Zd\\re\\bf\\%g\\_h',NULL,1);
/*!40000 ALTER TABLE `user` ENABLE KEYS */;
UNLOCK TABLES;
/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;

-- Dump completed on 2024-05-01 12:00:00
"""


def test_mysqldump_style(tmp_path: Path) -> None:
    p = _write(tmp_path, MYSQLDUMP)
    events = _all_chunkings(p)
    tevs = [e for e in events if isinstance(e, TableEvent)]
    assert len(tevs) == 1
    t = tevs[0].table
    assert t.name == "user"
    assert t.column_names() == ("id", "username", "balance", "status", "note", "created", "score")
    assert t.primary_key == ("id",)
    assert t.unique_keys == (("username",),)
    cols = {c.name: c for c in t.columns}
    assert cols["id"] == Column("id", "bigint(20)", False, None)
    assert cols["username"].type == "varchar(500)" and cols["username"].nullable
    assert cols["balance"].type == "decimal(30,2)" and cols["balance"].default == "'0.00'"
    assert cols["status"].type == "enum('active','Blocked')"
    assert cols["created"].default == "CURRENT_TIMESTAMP" and cols["created"].nullable
    assert cols["score"].type == "int(11) unsigned" and cols["score"].default == "-1"
    assert cols["note"].default is None

    rows = _rows(events)
    assert rows == [
        (1, "ali", "12345678901234567890.12", "active", "it's; (fine)\nline2", None, 5),
        (2, "رضا 🚀", "-0.50", "Blocked", "", None, -7),
        (3, None, "0.00", "active", 'say "hi" \\ back', "2024-01-01 10:00:00", 0),
        (4, "x'y", "1.00", "active", "a\x00b\tc\x1ad\re\x08f\\%g\\_h", None, 1),
    ]
    revs = [e for e in events if isinstance(e, RowEvent)]
    assert [(e.statement_index, e.row_index) for e in revs][:3] == [
        (revs[0].statement_index, i) for i in range(3)
    ]
    assert revs[3].row_index == 0 and revs[3].statement_index == revs[0].statement_index + 1
    assert all(e.columns == t.column_names() for e in revs)
    assert _warnings(events) == []
    # TableEvent precedes rows
    assert isinstance(events[0], TableEvent)


PHPMYADMIN = """-- phpMyAdmin SQL Dump
-- version 5.2.1
-- https://www.phpmyadmin.net/
--
-- Host: localhost:3306
-- Generation Time: May 01, 2024 at 12:00 PM

SET SQL_MODE = "NO_AUTO_VALUE_ON_ZERO";
START TRANSACTION;
SET time_zone = "+00:00";

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET NAMES utf8mb4 */;

--
-- Database: `mirza`
--

-- --------------------------------------------------------

--
-- Table structure for table `invoice`
--

CREATE TABLE `invoice` (
  `id_invoice` varchar(200) NOT NULL,
  `id_user` varchar(200) DEFAULT NULL,
  `price_product` int(11) DEFAULT NULL,
  `amount` double DEFAULT NULL,
  `blob` longblob
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Dumping data for table `invoice`
--

INSERT INTO `invoice` (`id_invoice`, `id_user`, `price_product`, `amount`, `blob`) VALUES
('a1', '100', 50000, 1.5e3, 0xCAFE00),
('a2', NULL, -3, -0.25, _binary 'ab\\0c'),
('a3', '102', +7, .5, b'0000000100000010');

--
-- Indexes for dumped tables
--

--
-- Indexes for table `invoice`
--
ALTER TABLE `invoice`
  ADD PRIMARY KEY (`id_invoice`),
  ADD UNIQUE KEY `uq_user` (`id_user`,`price_product`),
  ADD KEY `id_user` (`id_user`);
COMMIT;

/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
"""


def test_phpmyadmin_style(tmp_path: Path) -> None:
    p = _write(tmp_path, PHPMYADMIN)
    events = _all_chunkings(p)
    tevs = [e.table for e in events if isinstance(e, TableEvent)]
    assert len(tevs) == 2
    assert tevs[0].primary_key == () and tevs[0].columns[0].nullable is False
    assert tevs[1].primary_key == ("id_invoice",)
    assert tevs[1].unique_keys == (("id_user", "price_product"),)
    # second TableEvent comes after the rows (phpMyAdmin puts keys at the end)
    kinds = [type(e).__name__ for e in events]
    assert kinds == ["TableEvent", "RowEvent", "RowEvent", "RowEvent", "TableEvent"]
    rows = _rows(events)
    assert rows[0] == ("a1", "100", 50000, Decimal("1.5e3"), b"\xca\xfe\x00")
    assert rows[1] == ("a2", None, -3, Decimal("-0.25"), b"ab\x00c")
    assert rows[2] == ("a3", "102", 7, Decimal(".5"), b"\x01\x02")
    assert all(type(v) is not float for r in rows for v in r)
    revs = [e for e in events if isinstance(e, RowEvent)]
    assert revs[0].columns == ("id_invoice", "id_user", "price_product", "amount", "blob")
    reader = DumpReader(p)
    list(reader.events())
    assert reader.tables["invoice"].primary_key == ("id_invoice",)
    assert reader.bytes_read == p.stat().st_size


def test_value_types(tmp_path: Path) -> None:
    sql = (
        "CREATE TABLE t (a int, b text, c decimal(40,2), d varbinary(10), e tinyint);\n"
        "INSERT INTO t VALUES (-9223372036854775808,'',12345678901234567890.12,0x,TRUE),"
        "(18446744073709551615,'semi;colon',-0.000001,X'0a0B',false),"
        "(0,'(paren)',1E-3,_utf8mb4'سلام',null),"
        '(  42 , "dq "" \\" q" , 3.0 , N\'national\' , 0b101 );\n'
    )
    p = _write(tmp_path, sql)
    rows = _rows(_all_chunkings(p))
    assert rows[0] == (-9223372036854775808, "", Decimal("12345678901234567890.12"), b"", 1)
    assert str(rows[0][2]) == "12345678901234567890.12"
    assert rows[1] == (18446744073709551615, "semi;colon", Decimal("-0.000001"), b"\n\x0b", 0)
    assert rows[2] == (0, "(paren)", Decimal("1E-3"), "سلام", None)
    assert rows[3] == (42, 'dq " " q', Decimal("3.0"), "national", b"\x05")
    assert isinstance(rows[3][2], Decimal) and isinstance(rows[0][0], int)


def test_tricky_strings_and_newlines(tmp_path: Path) -> None:
    payloads = [
        "it's",
        "semi;colon), (fake, row);",
        "multi\nline\r\ntext",
        "back\\slash at end\\",
        "فارسی با «گیومه» و ‌نیم‌فاصله",
        "emoji 😀👍🏽🇮🇷",
        "''",
        "-- not a comment /* nor this */ # nor this",
        "",
    ]

    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n").replace("\r", "\\r")

    values = ",".join(f"({i},'{esc(s)}')" for i, s in enumerate(payloads))
    sql = f"CREATE TABLE `s` (`id` int NOT NULL, `v` text, PRIMARY KEY (`id`));\nINSERT INTO `s` VALUES {values};\n"
    # also the doubled-quote style for one row
    sql += "INSERT INTO `s` VALUES (100,'O''Reilly '' ok');\n"
    p = _write(tmp_path, sql)
    rows = _rows(_all_chunkings(p))
    assert rows[:-1] == [(i, s) for i, s in enumerate(payloads)]
    assert rows[-1] == (100, "O'Reilly ' ok")


def test_bom_and_crlf(tmp_path: Path) -> None:
    sql = MYSQLDUMP.replace("\n", "\r\n")
    p = _write(tmp_path, b"\xef\xbb\xbf" + sql.encode("utf-8"))
    events = _all_chunkings(p)
    rows = _rows(events)
    assert len(rows) == 4
    assert rows[0][4] == "it's; (fine)\nline2"  # escaped \n in data, unaffected by CRLF
    assert [e.table.name for e in events if isinstance(e, TableEvent)] == ["user"]


def test_rows_with_crlf_between_rows(tmp_path: Path) -> None:
    sql = "CREATE TABLE t (a int, b varchar(5));\r\nINSERT INTO t (a, b) VALUES\r\n(1, 'x'),\r\n(2, 'y')\r\n;\r\n"
    p = _write(tmp_path, sql)
    assert _rows(_all_chunkings(p)) == [(1, "x"), (2, "y")]


def test_db_qualified_names_and_insert_modifiers(tmp_path: Path) -> None:
    sql = (
        "CREATE TABLE IF NOT EXISTS `mirza`.`product` (`id` int, `name` varchar(10), UNIQUE (`name`));\n"
        "INSERT IGNORE INTO `mirza`.`product` VALUES (1,'a');\n"
        "insert into product (name, id) values ('b', 2);\n"
    )
    p = _write(tmp_path, sql)
    events = _all_chunkings(p)
    t = events[0].table
    assert t.name == "product" and t.unique_keys == (("name",),)
    revs = [e for e in events if isinstance(e, RowEvent)]
    assert revs[0].table == "product" and revs[0].values == (1, "a") and revs[0].columns == ("id", "name")
    assert revs[1].columns == ("name", "id") and revs[1].values == ("b", 2)


def test_replace_into_and_on_duplicate(tmp_path: Path) -> None:
    sql = (
        "CREATE TABLE t (a int, b int);\n"
        "REPLACE INTO t VALUES (1,2),(3,4);\n"
        "INSERT INTO t VALUES (5,6) ON DUPLICATE KEY UPDATE b=VALUES(b);\n"
        "INSERT INTO t VALUES (7,8);\n"
    )
    p = _write(tmp_path, sql)
    events = _all_chunkings(p)
    assert _rows(events) == [(1, 2), (3, 4), (5, 6), (7, 8)]
    assert _warnings(events) == ["replace_into", "on_duplicate_key"]


def test_unknown_table_positional_columns(tmp_path: Path) -> None:
    p = _write(
        tmp_path, "INSERT INTO `ghost` VALUES (1,'a'),(2,'b');\nINSERT INTO `ghost2` (x) VALUES (9);\n"
    )
    events = _all_chunkings(p)
    assert _warnings(events) == ["unknown_table_columns"]
    revs = [e for e in events if isinstance(e, RowEvent)]
    assert revs[0].columns == ("c0", "c1") and revs[1].values == (2, "b")
    assert revs[2].columns == ("x",) and revs[2].values == (9,)
    w = next(e for e in events if isinstance(e, WarningEvent))
    assert w.offset == 0


def test_column_mismatch(tmp_path: Path) -> None:
    p = _write(tmp_path, "CREATE TABLE t (a int, b int);\nINSERT INTO t VALUES (1,2),(3,4,5);\n")
    for cs in CHUNK_SIZES:
        with pytest.raises(SqlParseError) as ei:
            _events(p, cs)
        assert ei.value.code == "sql.column_mismatch"
    p2 = _write(tmp_path, "CREATE TABLE t (a int, b int);\nINSERT INTO t VALUES (1);\n", "b.sql")
    with pytest.raises(SqlParseError) as ei:
        _events(p2)
    assert ei.value.code == "sql.column_mismatch"
    p3 = _write(tmp_path, "INSERT INTO t2 VALUES (1,2),(1);\n", "c.sql")
    with pytest.raises(SqlParseError) as ei:
        _events(p3)
    assert ei.value.code == "sql.column_mismatch"


def test_truncated_mid_string(tmp_path: Path) -> None:
    full = (
        "CREATE TABLE t (a int, b text);\nINSERT INTO t VALUES (1,'secret-data-xyz'),(2,'another secret valu"
    )
    p = _write(tmp_path, full)
    for cs in CHUNK_SIZES:
        reader = DumpReader(p, _chunk_size=cs)
        got = []
        with pytest.raises(SqlParseError) as ei:
            for e in reader.events():
                got.append(e)
        assert ei.value.code == "sql.truncated"
        assert "secret" not in str(ei.value) and "secret" not in (ei.value.detail or "")
        assert "byte offset" in (ei.value.detail or "")
        assert _rows(got) == [(1, "secret-data-xyz")]


@pytest.mark.parametrize(
    "tail",
    ["(2,", "(2", "(", "(2,'x')", "(2,'x'),", "(2,'x'"],
)
def test_truncated_variants(tmp_path: Path, tail: str) -> None:
    p = _write(tmp_path, "CREATE TABLE t (a int, b text);\nINSERT INTO t VALUES (1,'a')," + tail)
    if tail == "(2,'x')":
        # complete row, only the ';' is missing -> accepted with a warning
        events = _events(p)
        assert _rows(events) == [(1, "a"), (2, "x")]
        assert "missing_semicolon" in _warnings(events)
        return
    for cs in CHUNK_SIZES:
        with pytest.raises(SqlParseError) as ei:
            _events(p, cs)
        assert ei.value.code in ("sql.truncated", "sql.malformed_insert")


def test_truncated_in_create_and_comment(tmp_path: Path) -> None:
    p = _write(tmp_path, "CREATE TABLE t (a int COMMENT 'unterminated")
    with pytest.raises(SqlParseError) as ei:
        _events(p, 7)
    assert ei.value.code == "sql.truncated"
    p2 = _write(tmp_path, "/* never closed ", "c.sql")
    with pytest.raises(SqlParseError):
        _events(p2)


def test_malformed_insert(tmp_path: Path) -> None:
    cases = [
        "INSERT INTO t VALUES (1, 'a' 'b');",
        "INSERT INTO t VALUES (1, NOW());",
        "INSERT INTO t VALUES (1, 2) (3, 4);",
        "INSERT INTO t VALUES 1, 2;",
        "INSERT INTO t (a b) VALUES (1);",
        "INSERT INTO t VALUES (1, 'secret-xyz', @var);",
    ]
    for i, sql in enumerate(cases):
        p = _write(tmp_path, "CREATE TABLE t (a int, b int, c int);\n" + sql + "\n", f"m{i}.sql")
        for cs in (1024 * 1024, 7):
            with pytest.raises(SqlParseError) as ei:
                _events(p, cs)
            assert ei.value.code.startswith("sql."), sql
            assert "secret" not in str(ei.value)


def test_garbage_files(tmp_path: Path) -> None:
    # binary garbage
    p = _write(tmp_path, bytes(range(256)) * 50, "bin.sql")
    with pytest.raises(SqlParseError):
        _events(p)
    # gzip
    p = _write(tmp_path, b"\x1f\x8b\x08\x00" + b"\x00" * 100, "gz.sql")
    with pytest.raises(SqlParseError) as ei:
        _events(p)
    assert ei.value.code == "sql.compressed"
    # plain text that isn't SQL
    p = _write(tmp_path, "hello world\nthis is not a dump\n", "txt.sql")
    events = _events(p)
    assert _warnings(events) == ["no_dump_statements"]
    # empty file
    p = _write(tmp_path, b"", "empty.sql")
    assert _events(p) == []


def test_invalid_utf8(tmp_path: Path) -> None:
    sql = b"CREATE TABLE t (a int, b text);\nINSERT INTO t VALUES (1,'ok'),(2,'bad\xff\xfe'),(3,'x');\n"
    p = _write(tmp_path, sql)
    for cs in CHUNK_SIZES:
        with pytest.raises(SqlParseError) as ei:
            _events(p, cs)
        assert ei.value.code == "sql.encoding"
    rows = _rows(_all_chunkings(p, encoding_errors="replace"))
    assert rows == [(1, "ok"), (2, "bad\ufffd\ufffd"), (3, "x")]
    # raw (non-UTF-8) bytes inside _binary strings are fine and preserved exactly
    p2 = _write(tmp_path, b"INSERT INTO t2 (a,b) VALUES (1,_binary '\xff\\0\xc3'),(2,'ok');\n", "b.sql")
    assert _rows(_all_chunkings(p2)) == [(1, b"\xff\x00\xc3"), (2, "ok")]


def test_delimiter_blocks(tmp_path: Path) -> None:
    sql = """CREATE TABLE t (a int);
INSERT INTO t VALUES (1);
DELIMITER ;;
/*!50003 CREATE*/ /*!50017 DEFINER=`root`@`localhost`*/ /*!50003 TRIGGER trg BEFORE INSERT ON t
FOR EACH ROW BEGIN
  INSERT INTO t VALUES (999);
  SET @x = 'unterminated ;
END */;;
DELIMITER ;
INSERT INTO t VALUES (2);
DELIMITER $$
CREATE PROCEDURE p() BEGIN SELECT 1; END$$
delimiter ;
INSERT INTO t VALUES (3);
"""
    p = _write(tmp_path, sql)
    events = _all_chunkings(p)
    assert _rows(events) == [(1,), (2,), (3,)]
    assert _warnings(events) == ["delimiter_block", "delimiter_block"]

    p2 = _write(tmp_path, "DELIMITER //\nCREATE PROCEDURE x() BEGIN END//\n", "u.sql")
    assert _warnings(_events(p2, 7)) == ["delimiter_block", "delimiter_unterminated"]


def test_other_statements_ignored(tmp_path: Path) -> None:
    sql = """CREATE DATABASE IF NOT EXISTS `mirza` /*!40100 DEFAULT CHARACTER SET utf8mb4 */;
USE `mirza`;
DROP TABLE IF EXISTS `a`;
CREATE TABLE `a` (`id` int NOT NULL AUTO_INCREMENT, PRIMARY KEY (`id`)) ENGINE=InnoDB AUTO_INCREMENT=5;
CREATE ALGORITHM=UNDEFINED VIEW v AS SELECT 'x;y' AS c;
CREATE TABLE `b` LIKE `a`;
LOCK TABLES `a` WRITE;
ALTER TABLE `a` MODIFY `id` int NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=6;
ALTER TABLE `unknown` ADD PRIMARY KEY (`z`);
INSERT INTO a SELECT * FROM b;
# hash comment ; with semicolon
INSERT INTO `a` VALUES (1),(2);
UNLOCK TABLES;
"""
    p = _write(tmp_path, sql)
    events = _all_chunkings(p)
    tnames = [e.table.name for e in events if isinstance(e, TableEvent)]
    assert tnames == ["a", "b"]
    assert events[1].table.primary_key == ("id",)
    assert _rows(events) == [(1,), (2,)]
    assert _warnings(events) == ["unsupported_insert"]


def test_alter_variants(tmp_path: Path) -> None:
    sql = """CREATE TABLE t (a int, b int, c int);
ALTER TABLE `db`.`t` ADD CONSTRAINT `pk` PRIMARY KEY (`a`,`b`), ADD UNIQUE INDEX `u` (`c`);
ALTER TABLE t ADD KEY k (b);
ALTER TABLE t DROP PRIMARY KEY;
"""
    p = _write(tmp_path, sql)
    tevs = [e.table for e in _all_chunkings(p) if isinstance(e, TableEvent)]
    assert len(tevs) == 3
    assert tevs[1].primary_key == ("a", "b") and tevs[1].unique_keys == (("c",),)
    assert [c.nullable for c in tevs[1].columns] == [False, False, True]
    assert tevs[2].primary_key == ()


def test_inline_keys_and_constraint(tmp_path: Path) -> None:
    sql = (
        "CREATE TABLE t (id int PRIMARY KEY, code varchar(5) UNIQUE, x int, "
        "CONSTRAINT uq2 UNIQUE KEY (x, code), CONSTRAINT CHECK (x > 0));"
    )
    t = _all_chunkings(_write(tmp_path, sql))[0].table
    assert t.primary_key == ("id",)
    assert t.unique_keys == (("code",), ("x", "code"))
    assert t.columns[0].nullable is False


def test_value_too_large(tmp_path: Path) -> None:
    big = "x" * 5000
    p = _write(tmp_path, f"INSERT INTO t (a) VALUES ('{big}');\n")
    for cs in (1024 * 1024, 7, 256):
        with pytest.raises(SqlParseError) as ei:
            _events(p, cs, max_value_bytes=1000)
        assert ei.value.code == "sql.value_too_large"
    assert _rows(_events(p, 7, max_value_bytes=10_000)) == [(big,)]
    # multi-row INSERT (bulk path)
    p2 = _write(tmp_path, f"INSERT INTO t (a) VALUES ('a'),('{big}'),('b');\n", "b.sql")
    with pytest.raises(SqlParseError) as ei:
        _events(p2, max_value_bytes=1000)
    assert ei.value.code == "sql.value_too_large"


def test_large_value_across_many_chunks(tmp_path: Path) -> None:
    big = ("ab\\'c" * 50_000) + "😀"
    p = _write(tmp_path, f"INSERT INTO t (a, b) VALUES (1, '{big}'), (2, 'z');\n")
    rows = _rows(_events(p, 1000))
    assert rows == [(1, big.replace("\\'", "'")), (2, "z")]


def test_cancellation(tmp_path: Path) -> None:
    rows = ",".join(f"({i},'v{i}')" for i in range(20000))
    p = _write(tmp_path, f"CREATE TABLE t (a int, b text);\nINSERT INTO t VALUES {rows};\n")
    tok = CancelToken()
    reader = DumpReader(p, cancel=tok, _chunk_size=4096)
    n = 0
    with pytest.raises(Cancelled):
        for e in reader.events():
            n += 1
            if n == 100:
                tok.cancel()
    assert n < 20000
    tok2 = CancelToken()
    tok2.cancel()
    with pytest.raises(Cancelled):
        scan_schema(p, cancel=tok2)


def test_progress(tmp_path: Path) -> None:
    rows = ",".join(f"({i},'v{i}')" for i in range(5000))
    p = _write(tmp_path, f"CREATE TABLE t (a int, b text);\nINSERT INTO t VALUES {rows};\n")
    calls: list[tuple[int, int | None]] = []
    list(DumpReader(p, progress=lambda d, t: calls.append((d, t)), _chunk_size=1000).events())
    size = p.stat().st_size
    assert calls[-1] == (size, size)
    assert all(b[0] > a[0] for a, b in zip(calls, calls[1:], strict=False))
    assert len(calls) >= size // 1000


def test_scan_schema(tmp_path: Path) -> None:
    p = _write(tmp_path, MYSQLDUMP + PHPMYADMIN)
    tables = scan_schema(p)
    assert set(tables) == {"user", "invoice"}
    assert tables["invoice"].primary_key == ("id_invoice",)
    assert tables["user"].unique_keys == (("username",),)
    full = DumpReader(p)
    list(full.events())
    assert full.tables == tables
    assert isinstance(tables["user"], TableDef)
    # skipping bodies still honors string boundaries and chunk splits
    reader = DumpReader(p, _chunk_size=7, _schema_only=True)
    assert [type(e) for e in reader.events()] == [TableEvent, TableEvent, TableEvent]
    p2 = _write(tmp_path, "INSERT INTO t VALUES (1,'unterminated", "t.sql")
    with pytest.raises(SqlParseError):
        scan_schema(p2)


def test_early_stop_consumer(tmp_path: Path) -> None:
    rows = ",".join(f"({i})" for i in range(1000))
    p = _write(tmp_path, f"INSERT INTO t (a) VALUES {rows};\n")
    gen = DumpReader(p).events()
    first = [next(gen) for _ in range(3)]
    gen.close()
    assert [e.values for e in first] == [(0,), (1,), (2,)]


def test_events_once(tmp_path: Path) -> None:
    reader = DumpReader(_write(tmp_path, "SELECT 1;"))
    list(reader.events())
    with pytest.raises(RuntimeError):
        list(reader.events())


def test_empty_row_and_whitespace(tmp_path: Path) -> None:
    p = _write(tmp_path, "INSERT INTO t () VALUES ( ) , ();\nINSERT INTO t (a,) VALUES (1);")
    with pytest.raises(SqlParseError):  # trailing comma in the column list -> malformed
        _events(p)
    p1 = _write(tmp_path, "INSERT INTO t () VALUES ( ) , ();", "a.sql")
    revs = [e for e in _all_chunkings(p1) if isinstance(e, RowEvent)]
    assert [(e.columns, e.values) for e in revs] == [((), ()), ((), ())]
    p2 = _write(
        tmp_path, "INSERT INTO t VALUES ( ) , ();\nINSERT INTO u VALUES( 1 ,\n\t'a' )  ,  ( 2,'b')", "b.sql"
    )
    events = _all_chunkings(p2)
    assert _rows(events) == [(), (), (1, "a"), (2, "b")]


def _synthetic_insert(path: Path, nrows: int) -> int:
    with open(path, "w", encoding="utf-8") as f:
        f.write(
            "CREATE TABLE `big` (`id` bigint NOT NULL, `name` varchar(100), `amount` decimal(20,2), "
            "`note` text, PRIMARY KEY (`id`));\nINSERT INTO `big` VALUES "
        )
        for i in range(nrows):
            if i:
                f.write(",")
            f.write(f"({i},'کاربر {i} it\\'s',{i}.25,NULL)")
        f.write(";\n")
    return path.stat().st_size


def test_memory_bounded_single_huge_insert(tmp_path: Path) -> None:
    p = tmp_path / "huge.sql"
    size = _synthetic_insert(p, 200_000)
    tracemalloc.start()
    try:
        n = 0
        last = None
        for e in DumpReader(p).events():
            if isinstance(e, RowEvent):
                n += 1
                last = e
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert n == 200_000
    assert last is not None and last.values == (199_999, "کاربر 199999 it's", Decimal("199999.25"), None)
    print(f"\nsqldump tracemalloc peak: {peak / 1e6:.1f} MB for a {size / 1e6:.1f} MB single INSERT")
    assert peak < 50 * 1024 * 1024, f"peak {peak / 1e6:.1f} MB for a {size / 1e6:.1f} MB file"


def _throughput(tmp_path: Path, nrows: int) -> float:
    p = tmp_path / "perf.sql"
    size = _synthetic_insert(p, nrows)
    t0 = time.perf_counter()
    n = sum(1 for e in DumpReader(p).events() if isinstance(e, RowEvent))
    dt = time.perf_counter() - t0
    assert n == nrows
    mbps = size / 1024 / 1024 / dt
    print(f"\nsqldump throughput: {size / 1e6:.1f} MB in {dt:.2f}s = {mbps:.1f} MiB/s")
    return mbps


def test_throughput_smoke(tmp_path: Path) -> None:
    assert _throughput(tmp_path, 100_000) > 2.0


@pytest.mark.perf
def test_throughput_50mb(tmp_path: Path) -> None:
    assert _throughput(tmp_path, 1_300_000) > 8.0

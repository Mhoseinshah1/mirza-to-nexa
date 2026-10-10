"""Schema export tool: structure + row counts only; no value, default or comment ever leaves."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from archive_fixtures import make_aes_zip, make_zip
from mirza2nexa.schema_export import UNCATALOGUED, export_schema

# Every value below is FABRICATED. Each one is planted in a place the export must not copy:
# DEFAULT expressions, column/table COMMENTs, CHECK expressions, INSERT values, SQL comments.
SECRET_DEFAULT = "DefaultSecret-7f3a9c"
SECRET_COMMENT = "CommentSecret-91bd2e"
SECRET_TABLE_COMMENT = "TableCommentSecret-55aa"
SECRET_CHECK = "CheckSecret-0c0c"
SECRET_ROW = "123456789:AARowSecretRowSecretRowSecretRowSec"
SECRET_SQL_COMMENT = "SqlCommentSecret-e1e1"
SECRET_ENTRY = "customer-bot-name"
PLANTED = (SECRET_DEFAULT, SECRET_COMMENT, SECRET_TABLE_COMMENT, SECRET_CHECK, SECRET_ROW, SECRET_SQL_COMMENT,
           SECRET_ENTRY)

SQL = f"""-- MySQL dump {SECRET_SQL_COMMENT}
/*!40101 SET NAMES utf8mb4 */;
DROP TABLE IF EXISTS `user`;
CREATE TABLE `user` (
  `id` varchar(500) NOT NULL,
  `Balance` int(255) NOT NULL DEFAULT 0,
  `token` varchar(200) DEFAULT '{SECRET_DEFAULT}' COMMENT '{SECRET_COMMENT}',
  `note` text DEFAULT NULL,
  `age` int CHECK (`age` <> '{SECRET_CHECK}'),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_token` (`token`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='{SECRET_TABLE_COMMENT}';
LOCK TABLES `user` WRITE;
INSERT INTO `user` VALUES ('1',10,'{SECRET_ROW}',NULL,3),('2',20,'x;y''z',NULL,4);
INSERT INTO `user` VALUES ('3',30,NULL,'note',5);
UNLOCK TABLES;
CREATE TABLE `zz_fork_full` (`id` int NOT NULL, `secret_value` text, PRIMARY KEY (`id`));
INSERT INTO `zz_fork_full` VALUES (1,'{SECRET_ROW}');
CREATE TABLE `zz_fork_empty` (`id` int NOT NULL);
CREATE TABLE `botsaz_help` (`id` int NOT NULL, `text` text);
"""


def _zip(tmp: Path, password: bytes | None = None) -> Path:
    files = {f"backup/{SECRET_ENTRY}.sql": SQL.encode("utf-8"), "readme.txt": SECRET_ROW.encode()}
    if password:
        return make_aes_zip(tmp / "b.zip", files, password)
    return make_zip(tmp / "b.zip", files)


def _check(doc: dict, raw: str) -> None:
    for s in PLANTED:
        assert s not in raw, s
    t = {x["name"]: x for x in doc["tables"]}
    user = t["user"]
    assert user["row_count"] == 3 and user["catalogue"] != UNCATALOGUED
    assert user["primary_key"] == ["id"] and user["unique_keys"] == [["token"]]
    cols = {c["name"]: c for c in user["columns"]}
    assert set(cols) == {"id", "Balance", "token", "note", "age"}
    assert cols["Balance"]["default_present"] is True and cols["Balance"]["nullable"] is False
    assert cols["token"]["default_present"] is True and cols["token"]["type"] == "varchar(200)"
    assert cols["note"]["default_present"] is False and cols["note"]["nullable"] is True
    assert set(cols["token"]) == {"name", "type", "nullable", "default_present"}
    assert t["zz_fork_full"]["catalogue"] == UNCATALOGUED and t["zz_fork_full"]["row_count"] == 1
    assert t["zz_fork_empty"]["row_count"] == 0
    assert t["botsaz_help"]["catalogue"] == "REVIEW" and t["botsaz_help"]["row_count"] == 0
    assert doc["summary"]["uncatalogued_blocking"] == ["zz_fork_full"]
    assert doc["summary"]["uncatalogued_empty"] == ["zz_fork_empty"]
    assert doc["tool"]["name"] == "mirza2nexa" and doc["tool"]["version"]
    assert doc["row_count_error"] is None and doc["sql_files"] == 1


def test_export_plain_zip(tmp_path):
    out = tmp_path / "schema.json"
    doc = export_schema(_zip(tmp_path), out, workdir=tmp_path / "ws")
    raw = out.read_text(encoding="utf-8")
    assert json.loads(raw) == doc
    _check(doc, raw)
    assert not list((tmp_path / "ws").rglob("*.sql")), "extracted SQL must be removed"


def test_export_encrypted_zip(tmp_path):
    out = tmp_path / "schema.json"
    doc = export_schema(_zip(tmp_path, b"Zip-Pw-1"), out, password="Zip-Pw-1")
    _check(doc, out.read_text(encoding="utf-8"))


def test_wrong_password_writes_nothing(tmp_path):
    from mirza2nexa.errors import MigrationError

    out = tmp_path / "schema.json"
    with pytest.raises(MigrationError):
        export_schema(_zip(tmp_path, b"Zip-Pw-1"), out, password="wrong")
    assert not out.exists()


def test_row_counts_on_synthetic_backup(tmp_path):
    from conftest import make_backup, run

    zp, _ = make_backup(tmp_path, users=60)
    doc = export_schema(zp, tmp_path / "s.json")
    res = run(tmp_path, zp)
    conv = {c["table"]: c["rows"] for c in res.report["coverage"]}
    got = {t["name"]: t["row_count"] for t in doc["tables"]}
    assert got == conv
    raw = (tmp_path / "s.json").read_text(encoding="utf-8")
    from mirza2nexa.modules import config_tables as ct

    for s in ct.PLANTED_SECRETS:
        assert s not in raw, s


def test_cli_fresh_process(tmp_path):
    zp = _zip(tmp_path, b"Zip-Pw-1")
    out = tmp_path / "o" / "schema.json"
    env = {**os.environ, "MIRZA2NEXA_HOME": str(tmp_path / "home"), "ZPW": "Zip-Pw-1"}
    proc = subprocess.run([sys.executable, "-I", "-m", "mirza2nexa", "schema", str(zp), "-o", str(out),
                           "--password-env", "ZPW"], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert "مقدار" in proc.stdout  # Persian review-before-sharing notice
    for s in (*PLANTED, "Zip-Pw-1"):
        assert s not in proc.stdout and s not in proc.stderr
    raw = out.read_text(encoding="utf-8")
    _check(json.loads(raw), raw)

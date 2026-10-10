"""Synthetic Mirza backups for tests, demos and performance runs.

Everything here is **fabricated**: ids are drawn from a range no real Telegram account
uses for test data in this project (``7_000_000_000+``), names are ``synthetic_user_<n>``,
and panel codes are ``syn-*``. The DDL follows NEXA's synthetic fixture
(``tests/fixtures/legacy/synthetic-legacy.sql``) — which is itself *not* evidence of the real
schema — plus a couple of unclassified tables so the converter's fail-closed paths run.
"""

from __future__ import annotations

import io
import random
import zipfile
from dataclasses import dataclass
from pathlib import Path

TG_BASE = 7_000_000_000

HEADER = """-- MySQL dump 10.13  Distrib 8.0.36, for Linux (x86_64)
--
-- Host: localhost    Database: mirza_synthetic
-- ------------------------------------------------------
/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET NAMES utf8mb4 */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
"""

DDL_USER = """DROP TABLE IF EXISTS `user`;
CREATE TABLE `user` (
  `id` varchar(500) NOT NULL,
  `limit_usertest` int(100) NOT NULL,
  `Balance` int(255) NOT NULL,
  `number` varchar(2000) DEFAULT NULL,
  `User_Status` varchar(500) DEFAULT NULL,
  `agent` varchar(100) DEFAULT NULL,
  `affiliates` varchar(100) DEFAULT NULL,
  `username` varchar(1000) DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
"""

DDL_INVOICE = """DROP TABLE IF EXISTS `invoice`;
CREATE TABLE `invoice` (
  `id_invoice` varchar(200) NOT NULL,
  `id_user` varchar(200) DEFAULT NULL,
  `username` varchar(200) DEFAULT NULL,
  `Service_location` varchar(200) DEFAULT NULL,
  `time_sell` varchar(200) DEFAULT NULL,
  `name_product` varchar(200) DEFAULT NULL,
  `price_product` varchar(200) DEFAULT NULL,
  `Volume` varchar(200) DEFAULT NULL,
  `Service_time` varchar(200) DEFAULT NULL,
  `Status` varchar(200) DEFAULT NULL,
  `code_panel` varchar(200) DEFAULT NULL,
  `code_product` varchar(200) DEFAULT NULL,
  `is_test` varchar(10) DEFAULT NULL,
  `is_custom` varchar(10) DEFAULT NULL,
  `time_unit` varchar(50) DEFAULT NULL,
  `user_info` text DEFAULT NULL,
  `uuid` varchar(200) DEFAULT NULL,
  PRIMARY KEY (`id_invoice`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
"""

DDL_PRODUCT = """DROP TABLE IF EXISTS `product`;
CREATE TABLE `product` (
  `id` int(6) unsigned NOT NULL AUTO_INCREMENT,
  `code_product` varchar(200) DEFAULT NULL,
  `name_product` varchar(2000) DEFAULT NULL,
  `price_product` varchar(2000) DEFAULT NULL,
  `Volume_constraint` varchar(2000) DEFAULT NULL,
  `Service_time` varchar(200) DEFAULT NULL,
  `Location` varchar(1000) DEFAULT NULL,
  `Category` varchar(600) DEFAULT NULL,
  `agent` varchar(100) DEFAULT NULL,
  `inbounds` text DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
"""

DDL_EXTRA = """DROP TABLE IF EXISTS `setting`;
CREATE TABLE `setting` (
  `Bot_Status` varchar(200) DEFAULT NULL,
  `help_Status` varchar(200) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
INSERT INTO `setting` VALUES ('on','on');
DROP TABLE IF EXISTS `Payment_report`;
CREATE TABLE `Payment_report` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `id_user` varchar(200) DEFAULT NULL,
  `price` varchar(200) DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


def _sql_str(v: str | None) -> str:
    if v is None:
        return "NULL"
    return "'" + v.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n") + "'"


@dataclass
class SynthSpec:
    users: int = 200
    invoices_per_user: float = 0.6
    products: int = 12
    seed: int = 7
    rows_per_insert: int = 500
    # pathological rows injected for review-queue coverage
    anomalies: bool = True
    # change balances of the first N users (for backup-to-backup diff tests)
    balance_shift_users: int = 0


def write_dump(out, spec: SynthSpec) -> dict[str, int]:
    """Write a synthetic mysqldump to the text stream ``out``. Returns expected aggregates."""
    rng = random.Random(spec.seed)  # noqa: S311 - synthetic data, not security
    out.write(HEADER)
    exp = {"users": 0, "positive_sum": 0, "negative_sum": 0, "positive": 0, "negative": 0, "zero": 0,
           "invoices": 0, "products": spec.products}

    out.write(DDL_PRODUCT)
    rows = []
    for i in range(1, spec.products + 1):
        rows.append(f"({i},{_sql_str(f'p{i:03d}')},{_sql_str(f'پلن آزمایشی {i}')},{_sql_str(str(50000 * i))},"
                    f"{_sql_str(str(10 * i))},{_sql_str('30')},{_sql_str('syn-loc')},{_sql_str('عمومی' if i % 2 else 'ویژه')},"
                    f"{_sql_str('f')},{_sql_str('{\"secret\":\"never-read\"}')})")
    if spec.anomalies:
        rows.append(f"({spec.products + 1},{_sql_str('p001')},{_sql_str('dup code')},{_sql_str('1,000')},"
                    f"{_sql_str('0')},{_sql_str('0')},NULL,NULL,NULL,NULL)")
        exp["products"] += 1
    out.write("LOCK TABLES `product` WRITE;\nINSERT INTO `product` VALUES " + ",".join(rows) + ";\nUNLOCK TABLES;\n")

    out.write(DDL_USER)
    out.write("LOCK TABLES `user` WRITE;\n")
    buf: list[str] = []

    def flush(table: str) -> None:
        if buf:
            out.write(f"INSERT INTO `{table}` VALUES " + ",".join(buf) + ";\n")  # noqa: S608 - writes synthetic dump text, never executed
            buf.clear()

    for n in range(spec.users):
        tg = str(TG_BASE + n)
        r = rng.random()
        bal = 0 if r < 0.6 else rng.randrange(1000, 2_000_000) if r < 0.97 else -rng.randrange(1000, 100_000)
        if n < spec.balance_shift_users:
            bal += 1000
        exp["users"] += 1
        if bal > 0:
            exp["positive"] += 1
            exp["positive_sum"] += bal
        elif bal < 0:
            exp["negative"] += 1
            exp["negative_sum"] += -bal
        else:
            exp["zero"] += 1
        status = "block" if n % 97 == 5 else "Active"
        agent = "n" if n % 53 == 0 else "f"
        buf.append(f"({_sql_str(tg)},{1 if n % 3 else 0},{bal},{_sql_str('none')},{_sql_str(status)},"
                   f"{_sql_str(agent)},NULL,{_sql_str(f'synthetic_user_{n}')})")
        if len(buf) >= spec.rows_per_insert:
            flush("user")
    flush("user")
    if spec.anomalies:
        # invalid identity, unreadable balance, unknown status
        out.write("INSERT INTO `user` VALUES ('0123',1,500,'none','Active','f',NULL,NULL),"
                  "('6999999001',1,0,'none','frozen','f',NULL,NULL);\n")
        out.write("INSERT INTO `user` (`id`,`limit_usertest`,`Balance`,`User_Status`) VALUES "
                  "('6999999002',1,'12.5','Active');\n")
        exp["users"] += 3
    out.write("UNLOCK TABLES;\n")

    out.write(DDL_INVOICE)
    out.write("LOCK TABLES `invoice` WRITE;\n")
    statuses = ["active", "active", "disabled", "end_of_volume", "removed", "end_of_time"]
    total_inv = int(spec.users * spec.invoices_per_user)
    for k in range(total_inv):
        owner = str(TG_BASE + rng.randrange(spec.users))
        key = f"{1000000 + (k % 8999999)}{k & 0xFFFFFFFF:08x}"
        p = rng.randrange(1, spec.products + 1)
        is_test = "1" if k % 41 == 0 else "0"
        buf.append(
            f"({_sql_str(key)},{_sql_str(owner)},{_sql_str(f'syn{k}')},{_sql_str('سرور آزمایشی')},"
            f"{_sql_str(str(1700000000 + k))},{_sql_str(f'پلن {p}')},{_sql_str(str(50000 * p))},"
            f"{_sql_str(str(10 * p))},{_sql_str('30')},{_sql_str(rng.choice(statuses))},"
            f"{_sql_str('syn-a' if k % 2 else 'syn-b')},{_sql_str(f'p{p:03d}')},{_sql_str(is_test)},"
            f"{_sql_str('0')},{_sql_str('')},{_sql_str('{\"subscription_url\":\"https://never.read/x\"}')},"
            f"{_sql_str('00000000-0000-0000-0000-000000000000')})")
        exp["invoices"] += 1
        if len(buf) >= spec.rows_per_insert:
            flush("invoice")
    flush("invoice")
    if spec.anomalies:
        out.write(
            "INSERT INTO `invoice` VALUES "
            # orphan live service (owner not in user table)
            "('9999999abcd','5000000001','orphan1','x','1700000000','p','1000','10','30','active','syn-a','p001','0','0','',NULL,NULL),"
            # two owners on one panel account → ambiguous
            "('9999998abcd','7000000001','shared','x','1700000000','p','1000','10','30','active','syn-z','','0','0','',NULL,NULL),"
            "('9999997abcd','7000000002','shared','x','1700000000','p','1000','10','30','active','syn-z','','0','0','',NULL,NULL),"
            # no panel
            "('9999996abcd','7000000003','nopanel','x','1700000000','p','1000','10','30','active','','','0','0','',NULL,NULL),"
            # unsupported shape (month)
            "('9999995abcd','7000000004','monthly','x','1700000000','p','1000','10','1','active','syn-a','','0','0','month',NULL,NULL);\n"
        )
        exp["invoices"] += 5
    out.write("UNLOCK TABLES;\n")
    out.write(DDL_EXTRA)
    out.write("-- Dump completed on 2026-10-01 10:00:00\n")
    return exp


def write_dump_file(path: Path, spec: SynthSpec) -> dict[str, int]:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        return write_dump(fh, spec)


def write_zip(zip_path: Path, spec: SynthSpec, *, password: str | None = None,
              entry_name: str = "backup_2026-10-01.sql") -> dict[str, int]:
    """Write a synthetic backup ZIP (WinZip AES-256 when ``password`` is given, like mirza_pro's backupbot)."""
    buf = io.StringIO()
    exp = write_dump(buf, spec)
    data = buf.getvalue().encode("utf-8")
    if password:
        import pyzipper

        with pyzipper.AESZipFile(zip_path, "w", compression=pyzipper.ZIP_DEFLATED,
                                 encryption=pyzipper.WZ_AES) as zf:
            zf.setpassword(password.encode("utf-8"))
            zf.writestr(entry_name, data)
    else:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(entry_name, data)
    return exp


def write_large_zip(zip_path: Path, spec: SynthSpec, *, password: str | None = None) -> dict[str, int]:
    """Like :func:`write_zip` but streams the dump through a temp file (for 200k-user perf runs)."""
    tmp = zip_path.with_suffix(".sql.tmp")
    exp = write_dump_file(tmp, spec)
    try:
        if password:
            import pyzipper

            with pyzipper.AESZipFile(zip_path, "w", compression=pyzipper.ZIP_DEFLATED,
                                     encryption=pyzipper.WZ_AES) as zf:
                zf.setpassword(password.encode("utf-8"))
                zf.write(tmp, "backup.sql")
        else:
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.write(tmp, "backup.sql")
    finally:
        tmp.unlink(missing_ok=True)
    return exp

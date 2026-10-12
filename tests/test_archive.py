from __future__ import annotations

import hashlib
import os
import random
import shutil
import stat
import zipfile
from collections import namedtuple
from pathlib import Path

import pytest
import pyzipper

from archive_fixtures import (
    SQL,
    make_aes_zip,
    make_symlink_zip,
    make_zip,
    make_zipcrypto_zip,
    patch_uncompressed_size,
    replace_name_bytes,
)
from mirza2nexa import archive
from mirza2nexa.archive import (
    ArchiveCorrupt,
    ArchiveLimits,
    cleanup_workspace,
    extract_sql,
    inspect_archive,
    secure_workspace,
    verify_password,
)
from mirza2nexa.errors import (
    ArchiveError,
    Cancelled,
    DiskSpaceError,
    LimitExceeded,
    PasswordRequired,
    UnsafeArchive,
    WrongPassword,
)
from mirza2nexa.progress import CancelToken

PW = "رمز-Secret-123"
MiB = 1024 * 1024


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    d = tmp_path / "ws"
    d.mkdir()
    return d


def _random_sql(n: int, seed: int = 1) -> bytes:
    rnd = random.Random(seed)
    words = [b"INSERT", b"INTO", b"`user`", b"VALUES", b"(1,'a')", b"NULL", b"0x7f", b"'x'", b";\n"]
    out = bytearray()
    while len(out) < n:
        out += rnd.choice(words) + b" " + str(rnd.random()).encode()
    return bytes(out[:n])


def _assert_no_password_leak(exc: BaseException, password: str = PW) -> None:
    text = str(exc) + repr(exc) + repr(getattr(exc, "detail", ""))
    assert password not in text
    assert password.encode("utf-8").hex() not in text
    assert "INSERT INTO" not in text


# ------------------------------------------------------------------------------ plain


def test_plain_zip_inspect_and_extract(tmp_path, ws):
    data = _random_sql(3 * MiB)
    z = make_zip(tmp_path / "b.zip", {"backup/Mirza DB.sql": data, "readme.txt": b"hi", "img/": b""})
    insp = inspect_archive(z)
    assert insp.encrypted is False and insp.encryption is None
    assert insp.sql_entries == ["backup/Mirza DB.sql"]
    assert insp.archive_sha256 == hashlib.sha256(z.read_bytes()).hexdigest()
    assert insp.archive_bytes == z.stat().st_size
    kinds = {e.name: e.kind for e in insp.entries}
    assert kinds == {"backup/Mirza DB.sql": "sql", "readme.txt": "skipped", "img/": "directory"}
    assert {e.method for e in insp.entries} == {"deflate"}

    seen = []
    out = extract_sql(z, ws, None, progress=lambda d, t: seen.append((d, t)))
    assert len(out) == 1
    f = out[0]
    assert f.path.parent == ws and f.path.name == "000-Mirza_DB.sql"
    assert f.path.read_bytes() == data
    assert f.size == len(data) and f.sha256 == hashlib.sha256(data).hexdigest()
    assert sorted(p.name for p in ws.iterdir()) == ["000-Mirza_DB.sql"]  # txt was not extracted
    if os.name == "posix":
        assert stat.S_IMODE(f.path.stat().st_mode) == 0o600
    total = seen[0][1]
    assert seen[0][0] == 0 and seen[-1] == (total, total)
    assert all(a[0] <= b[0] for a, b in zip(seen, seen[1:], strict=False))
    assert len(seen) > 3  # real intermediate progress


@pytest.mark.parametrize(
    "compression", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA]
)
def test_compression_methods(tmp_path, ws, compression):
    data = _random_sql(2 * MiB + 123)
    z = make_zip(tmp_path / "b.zip", {"a.sql": data, "b.SQL": SQL}, compression=compression)
    out = extract_sql(z, ws, None)
    assert [f.path.read_bytes() for f in out] == [data, SQL]
    assert [f.path.name for f in out] == ["000-a.sql", "001-b.sql"]


def test_macos_metadata_and_non_sql_skipped(tmp_path, ws):
    z = make_zip(
        tmp_path / "b.zip",
        {"dump.sql": SQL, "__MACOSX/._dump.sql": b"\x00\x05", "x/._y.sql": b"z", "notes.md": b"x"},
    )
    insp = inspect_archive(z)
    assert insp.sql_entries == ["dump.sql"]
    assert len(extract_sql(z, ws, None)) == 1


# ------------------------------------------------------------------------------ AES


@pytest.mark.parametrize(
    "compression", [pyzipper.ZIP_STORED, pyzipper.ZIP_DEFLATED, pyzipper.ZIP_BZIP2, pyzipper.ZIP_LZMA]
)
@pytest.mark.parametrize("nbits", [128, 256])
def test_aes_right_password(tmp_path, ws, compression, nbits):
    data = _random_sql(MiB + 7)
    z = make_aes_zip(tmp_path / "a.zip", {"dump.sql": data}, PW.encode(), compression, nbits)
    insp = inspect_archive(z)
    assert insp.encrypted and insp.encryption == "aes"
    assert insp.entries[0].method == "aes" and insp.entries[0].encrypted
    verify_password(z, PW)
    out = extract_sql(z, ws, PW)
    assert out[0].path.read_bytes() == data


def test_aes_wrong_and_missing_password(tmp_path, ws):
    z = make_aes_zip(tmp_path / "a.zip", {"dump.sql": SQL}, PW.encode())
    with pytest.raises(WrongPassword) as ei:
        verify_password(z, "Wr0ngPw!zz")
    assert ei.value.code == "archive.wrong_password"
    _assert_no_password_leak(ei.value, "Wr0ngPw!zz")
    with pytest.raises(PasswordRequired):
        verify_password(z, None)
    with pytest.raises(PasswordRequired):
        verify_password(z, "")
    with pytest.raises(WrongPassword) as ei:
        extract_sql(z, ws, "N0pe-zz-77")
    _assert_no_password_leak(ei.value, "N0pe-zz-77")
    with pytest.raises(PasswordRequired) as ei:
        extract_sql(z, ws, None)
    assert ei.value.code == "archive.password_required"
    assert list(ws.iterdir()) == []


def test_aes_corrupt_ciphertext_reported_as_corrupt(tmp_path, ws):
    data = _random_sql(200_000)
    z = make_aes_zip(tmp_path / "a.zip", {"dump.sql": data}, PW.encode())
    raw = bytearray(z.read_bytes())
    raw[2000] ^= 0xFF  # inside the encrypted payload
    z.write_bytes(bytes(raw))
    with pytest.raises(ArchiveError) as ei:
        extract_sql(z, ws, PW)
    assert ei.value.code == "archive.corrupt"
    _assert_no_password_leak(ei.value)
    assert list(ws.iterdir()) == []


# ------------------------------------------------------------------------------ ZipCrypto


@pytest.mark.parametrize("deflate", [True, False])
def test_zipcrypto_right_password(tmp_path, ws, deflate):
    data = _random_sql(40_000)
    z = make_zipcrypto_zip(tmp_path / "z.zip", {"dump.sql": data}, PW.encode(), deflate=deflate)
    # sanity: stdlib can read our fixture
    with zipfile.ZipFile(z) as zf:
        assert zf.read("dump.sql", pwd=PW.encode()) == data
    insp = inspect_archive(z)
    assert insp.encryption == "zipcrypto"
    assert insp.entries[0].method == ("deflate" if deflate else "stored")
    verify_password(z, PW)
    out = extract_sql(z, ws, PW)
    assert out[0].path.read_bytes() == data


def test_zipcrypto_wrong_password(tmp_path, ws):
    z = make_zipcrypto_zip(tmp_path / "z.zip", {"dump.sql": _random_sql(20_000)}, PW.encode())
    # Try many wrong passwords: some pass the 1-byte check; all must still end as WrongPassword.
    for i in range(300):
        bad = f"bad-{i}"
        with pytest.raises(WrongPassword) as ei:
            extract_sql(z, ws, bad)
        assert ei.value.code == "archive.wrong_password"
        _assert_no_password_leak(ei.value, bad)
        assert list(ws.iterdir()) == []
    with pytest.raises(WrongPassword):
        verify_password(z, "bad-x")
    with pytest.raises(PasswordRequired):
        extract_sql(z, ws, None)


def test_zipcrypto_stored_wrong_password_false_positive_caught_by_crc(tmp_path, ws):
    z = make_zipcrypto_zip(tmp_path / "z.zip", {"dump.sql": _random_sql(5000)}, PW.encode(), deflate=False)
    for i in range(600):
        with pytest.raises(WrongPassword):
            extract_sql(z, ws, f"x{i}")
    assert list(ws.iterdir()) == []


def test_non_ascii_password_legacy_codepage(tmp_path, ws):
    z = make_zipcrypto_zip(tmp_path / "z.zip", {"dump.sql": SQL}, PW.encode("cp1256"))
    out = extract_sql(z, ws, PW)
    assert out[0].path.read_bytes() == SQL


# ------------------------------------------------------------------------------ invalid archives


def test_not_a_zip(tmp_path, ws):
    p = tmp_path / "x.zip"
    p.write_bytes(b"this is not a zip file" * 100)
    with pytest.raises(ArchiveError) as ei:
        inspect_archive(p)
    assert ei.value.code == "archive.invalid"
    assert ei.value.message_fa
    with pytest.raises(ArchiveError):
        extract_sql(p, ws, None)
    with pytest.raises(ArchiveError):
        inspect_archive(tmp_path / "missing.zip")


def test_truncated_zip(tmp_path, ws):
    z = make_zip(tmp_path / "b.zip", {"dump.sql": _random_sql(100_000)})
    raw = z.read_bytes()
    z.write_bytes(raw[: len(raw) // 2])
    with pytest.raises(ArchiveError):
        inspect_archive(z)
    with pytest.raises(ArchiveError):
        extract_sql(z, ws, None)
    assert list(ws.iterdir()) == []


def test_corrupt_data_unencrypted_is_corrupt_not_wrong_password(tmp_path, ws):
    data = _random_sql(300_000)
    z = make_zip(tmp_path / "b.zip", {"dump.sql": data})
    raw = bytearray(z.read_bytes())
    for i in range(1000, 1100):
        raw[i] ^= 0x5A
    z.write_bytes(bytes(raw))
    inspect_archive(z)  # structure is fine
    with pytest.raises(ArchiveCorrupt) as ei:
        extract_sql(z, ws, None)
    assert ei.value.code == "archive.corrupt"
    assert not isinstance(ei.value, WrongPassword)
    assert list(ws.iterdir()) == []


def test_corrupt_stored_crc_mismatch(tmp_path, ws):
    data = _random_sql(10_000)
    z = make_zip(tmp_path / "b.zip", {"dump.sql": data}, compression=zipfile.ZIP_STORED)
    raw = bytearray(z.read_bytes())
    idx = raw.find(data[:50])
    raw[idx + 100] ^= 1
    z.write_bytes(bytes(raw))
    with pytest.raises(ArchiveCorrupt):
        extract_sql(z, ws, None)
    assert list(ws.iterdir()) == []


def test_no_sql(tmp_path):
    z = make_zip(tmp_path / "b.zip", {"a.txt": b"x", "b.sql.txt": b"y", "dir/": b""})
    with pytest.raises(ArchiveError) as ei:
        inspect_archive(z)
    assert ei.value.code == "archive.no_sql"
    assert ".sql" in ei.value.message_fa


# ------------------------------------------------------------------------------ unsafe names / types


@pytest.mark.parametrize(
    "name",
    [
        "../evil.sql",
        "/abs.sql",
        "C:\\x.sql",
        "c:/x.sql",
        "a/../../b.sql",
        "..\\..\\evil.sql",
        "a\\..\\..\\b.sql",
        "//server/share/x.sql",
        "ok/..",
        "a/\x01b.sql",
    ],
)
def test_zip_slip_names(tmp_path, ws, name):
    z = make_zip(tmp_path / "b.zip", {"dump.sql": SQL, name: SQL})
    with pytest.raises(UnsafeArchive) as ei:
        inspect_archive(z)
    assert ei.value.code == "archive.unsafe"
    with pytest.raises(UnsafeArchive):
        extract_sql(z, ws, None)
    assert list(ws.iterdir()) == []


def test_nul_in_name(tmp_path):
    z = make_zip(tmp_path / "b.zip", {"dump.sql": SQL, "evilXname.sql": SQL})
    replace_name_bytes(z, b"evilXname", b"evil\x00name")
    with pytest.raises(UnsafeArchive):
        inspect_archive(z)


def test_symlink_entry(tmp_path, ws):
    z = make_symlink_zip(tmp_path / "b.zip")
    with pytest.raises(UnsafeArchive):
        inspect_archive(z)
    with pytest.raises(UnsafeArchive):
        extract_sql(z, ws, None)


def test_special_file_entry(tmp_path):
    z = tmp_path / "b.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("dump.sql", SQL)
        zi = zipfile.ZipInfo("fifo")
        zi.create_system = 3
        zi.external_attr = (stat.S_IFIFO | 0o644) << 16
        zf.writestr(zi, b"")
    with pytest.raises(UnsafeArchive):
        inspect_archive(z)


@pytest.mark.parametrize(
    "names", [("dump.sql", "dump.sql"), ("Dump.sql", "dump.SQL"), ("a/b.sql", "a\\b.sql")]
)
def test_duplicate_names(tmp_path, names):
    z = tmp_path / "b.zip"
    with pytest.warns(UserWarning) if names[0] == names[1] else _nullctx(), zipfile.ZipFile(z, "w") as zf:
        for n in names:
            zf.writestr(zipfile.ZipInfo(n), SQL)
    with pytest.raises(UnsafeArchive):
        inspect_archive(z)


class _nullctx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ------------------------------------------------------------------------------ limits / bombs


def test_overlapping_entries_rejected(tmp_path):
    import struct

    z = make_zip(tmp_path / "b.zip", {"a.sql": SQL, "b.sql": SQL})
    raw = bytearray(z.read_bytes())
    second_cd = raw.find(b"PK\x01\x02", raw.find(b"PK\x01\x02") + 4)
    struct.pack_into("<I", raw, second_cd + 42, 0)  # point b.sql at a.sql's local header
    z.write_bytes(bytes(raw))
    with pytest.raises(UnsafeArchive):
        inspect_archive(z)


def test_too_many_entries(tmp_path, ws):
    z = make_zip(tmp_path / "b.zip", {f"f{i}.txt": b"x" for i in range(70)} | {"d.sql": SQL})
    with pytest.raises(LimitExceeded):
        inspect_archive(z)
    with pytest.raises(LimitExceeded):
        extract_sql(z, ws, None)
    inspect_archive(z, ArchiveLimits(max_entries=100))


def test_archive_too_big(tmp_path):
    z = make_zip(tmp_path / "b.zip", {"d.sql": SQL})
    with pytest.raises(LimitExceeded):
        inspect_archive(z, ArchiveLimits(max_archive_bytes=10))


def test_zip_bomb_declared_sizes_rejected(tmp_path, ws):
    z = make_zip(tmp_path / "bomb.zip", {"bomb.sql": bytes(50 * MiB)})
    assert z.stat().st_size < 200_000
    # declared ratio ~1000:1 > 200
    with pytest.raises(LimitExceeded):
        inspect_archive(z)
    with pytest.raises(LimitExceeded):
        extract_sql(z, ws, None)
    small = ArchiveLimits(max_ratio=1e9, max_entry_uncompressed=10 * MiB)
    with pytest.raises(LimitExceeded):
        inspect_archive(z, small)
    with pytest.raises(LimitExceeded):
        inspect_archive(z, ArchiveLimits(max_ratio=1e9, max_total_uncompressed=10 * MiB))
    assert list(ws.iterdir()) == []


def test_forged_header_actual_bytes_exceed_declared(tmp_path, ws):
    data = _random_sql(3 * MiB)
    z = make_zip(tmp_path / "forged.zip", {"dump.sql": data})
    patch_uncompressed_size(z, "dump.sql", 1000)
    insp = inspect_archive(z)  # headers look harmless
    assert insp.entries[0].size == 1000
    with pytest.raises(UnsafeArchive) as ei:
        extract_sql(z, ws, None)
    assert "declared" in ei.value.detail
    assert list(ws.iterdir()) == []


def test_forged_header_bomb_caught_by_streaming_ratio(tmp_path, ws):
    z = make_zip(tmp_path / "forged.zip", {"bomb.sql": bytes(50 * MiB)})
    patch_uncompressed_size(z, "bomb.sql", 8 * MiB)  # declared ratio looks fine (~160:1)
    inspect_archive(z)
    seen = []
    with pytest.raises(LimitExceeded) as ei:
        extract_sql(z, ws, None, progress=lambda d, t: seen.append(d))
    assert "ratio" in ei.value.detail
    assert list(ws.iterdir()) == []


def test_forged_header_caught_by_actual_entry_limit(tmp_path, ws):
    z = make_zip(tmp_path / "forged.zip", {"bomb.sql": _random_sql(3 * MiB)})
    patch_uncompressed_size(z, "bomb.sql", 2 * MiB)
    limits = ArchiveLimits(max_entry_uncompressed=2 * MiB + 1)
    inspect_archive(z, limits)
    with pytest.raises((LimitExceeded, UnsafeArchive)):
        extract_sql(z, ws, None, limits)
    assert list(ws.iterdir()) == []


def test_declared_larger_than_actual_is_corrupt(tmp_path, ws):
    z = make_zip(tmp_path / "f.zip", {"dump.sql": SQL})
    patch_uncompressed_size(z, "dump.sql", len(SQL) + 10)
    with pytest.raises(ArchiveCorrupt):
        extract_sql(z, ws, None)


def test_total_limit_enforced_on_actual_bytes(tmp_path, ws):
    a, b = _random_sql(2 * MiB, 1), _random_sql(2 * MiB, 2)
    z = make_zip(tmp_path / "t.zip", {"a.sql": a, "b.sql": b})
    with pytest.raises(LimitExceeded):
        extract_sql(z, ws, None, ArchiveLimits(max_total_uncompressed=3 * MiB))
    assert list(ws.iterdir()) == []


# ------------------------------------------------------------------------------ cancel / disk


def test_cancel_mid_extraction_cleans_up(tmp_path, ws):
    z = make_zip(tmp_path / "b.zip", {"a.sql": _random_sql(4 * MiB, 3), "b.sql": _random_sql(4 * MiB, 4)})
    token = CancelToken()
    calls = []

    def progress(done, total):
        calls.append(done)
        if len(calls) == 3:
            token.cancel()

    with pytest.raises(Cancelled):
        extract_sql(z, ws, None, cancel=token, progress=progress)
    assert list(ws.iterdir()) == []


def test_cancel_during_inspect(tmp_path):
    z = make_zip(tmp_path / "b.zip", {"a.sql": SQL})
    token = CancelToken()
    token.cancel()
    with pytest.raises(Cancelled):
        inspect_archive(z, cancel=token)


DU = namedtuple("DU", "total used free")


def test_disk_space_insufficient_before(tmp_path, ws, monkeypatch):
    z = make_zip(tmp_path / "b.zip", {"a.sql": SQL})
    monkeypatch.setattr(shutil, "disk_usage", lambda p: DU(10**9, 10**9, 100 * MiB))
    with pytest.raises(DiskSpaceError) as ei:
        extract_sql(z, ws, None)
    assert ei.value.code == "disk.insufficient"
    assert list(ws.iterdir()) == []


def test_disk_space_runs_out_during(tmp_path, ws, monkeypatch):
    z = make_zip(tmp_path / "b.zip", {"a.sql": _random_sql(3 * MiB)})
    monkeypatch.setattr(archive, "_DISK_CHECK_EVERY", MiB)
    state = {"n": 0}

    def fake(p):
        state["n"] += 1
        return DU(10**12, 0, 10**11 if state["n"] == 1 else 10 * MiB)

    monkeypatch.setattr(shutil, "disk_usage", fake)
    with pytest.raises(DiskSpaceError):
        extract_sql(z, ws, None)
    assert state["n"] >= 2
    assert list(ws.iterdir()) == []


def test_failure_in_second_entry_removes_first(tmp_path, ws):
    z = make_zip(tmp_path / "b.zip", {"a.sql": SQL, "b.sql": _random_sql(3 * MiB)})
    patch_uncompressed_size(z, "b.sql", 1000)
    with pytest.raises(UnsafeArchive):
        extract_sql(z, ws, None)
    assert list(ws.iterdir()) == []


def test_output_names_are_sanitized(tmp_path, ws):
    z = make_zip(tmp_path / "b.zip", {"x/دیتابیس میرزا.sql": SQL, "y/CON.sql": SQL, "z/a;b$(c).Sql": SQL})
    names = [f.path.name for f in extract_sql(z, ws, None)]
    assert names[0].startswith("000-") and names[0].endswith(".sql")
    assert names[1] == "001-CON.sql"
    assert names[2] == "002-a_b_c.sql"
    for n in names:
        assert all(c.isalnum() or c in "._-" for c in n)


# ------------------------------------------------------------------------------ workspace


def test_secure_workspace_and_cleanup(tmp_path):
    base = tmp_path / "base"
    ws = secure_workspace(base)
    assert ws.is_dir() and ws.parent == base
    if os.name == "posix":
        assert stat.S_IMODE(ws.stat().st_mode) == 0o700
        assert stat.S_IMODE(base.stat().st_mode) == 0o700
    ws2 = secure_workspace(base)
    assert ws2 != ws
    (ws / "sub").mkdir()
    ro = ws / "sub" / "ro.sql"
    ro.write_bytes(b"x")
    ro.chmod(0o400)
    (ws / "sub").chmod(0o500)
    cleanup_workspace(ws)
    assert not ws.exists()
    cleanup_workspace(ws)  # idempotent
    cleanup_workspace(tmp_path / "never-existed")


def test_secure_workspace_default_location():
    ws = secure_workspace()
    try:
        assert ws.is_dir()
        assert "mirza2nexa" in str(ws)
    finally:
        cleanup_workspace(ws)
    assert not ws.exists()


def test_cleanup_retries_on_permission_error(tmp_path, monkeypatch):
    d = tmp_path / "w"
    d.mkdir()
    (d / "f").write_bytes(b"1")
    real = shutil.rmtree
    calls = {"n": 0}

    def flaky(path, *a, **k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(13, "in use")
        return real(path, *a, **k)

    monkeypatch.setattr(shutil, "rmtree", flaky)
    monkeypatch.setattr(archive.time, "sleep", lambda s: None)
    cleanup_workspace(d)
    assert calls["n"] == 3
    assert not d.exists()


def test_cleanup_does_not_follow_symlink(tmp_path):
    if os.name != "posix":
        pytest.skip("symlinks")
    target = tmp_path / "precious"
    target.mkdir()
    (target / "keep").write_bytes(b"1")
    link = tmp_path / "link"
    link.symlink_to(target)
    cleanup_workspace(link)
    assert not link.exists() and (target / "keep").exists()

from __future__ import annotations

import os
import stat
from contextlib import closing
from pathlib import Path

import pytest

from conftest import make_backup
from mirza2nexa.cli import main
from mirza2nexa.key_output import publish_key
from mirza2nexa.nxpkg import PackageReader

SYNTHETIC_KEY = "fictional key material for filesystem assertions"


def test_key_is_private(tmp_path):
    key = tmp_path / "new.nxkey"
    if os.name == "nt":
        import win32api
        import win32con
        import win32security as security

        publish_key(key, SYNTHETIC_KEY)
        descriptor = security.GetNamedSecurityInfo(str(key), security.SE_FILE_OBJECT,
                                                   security.DACL_SECURITY_INFORMATION)
        assert descriptor.GetSecurityDescriptorControl()[0] & security.SE_DACL_PROTECTED
        dacl = descriptor.GetSecurityDescriptorDacl()
        with closing(security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)) as token:
            user_sid, _ = security.GetTokenInformation(token, security.TokenUser)
        assert dacl.GetAceCount() == 1
        assert dacl.GetAce(0)[2] == user_sid
    else:
        old = os.umask(0)
        try:
            publish_key(key, SYNTHETIC_KEY)
        finally:
            os.umask(old)
        assert stat.S_IMODE(key.stat().st_mode) == 0o600
    assert key.read_bytes() == (SYNTHETIC_KEY + "\n").encode()


@pytest.mark.parametrize("existing", ["file", "directory", "dangling_symlink"])
def test_key_publication_never_replaces_an_entry(tmp_path, existing):
    key = tmp_path / "existing.nxkey"
    if existing == "file":
        key.write_text("original", encoding="utf-8")
    elif existing == "directory":
        key.mkdir()
    else:
        try:
            key.symlink_to(tmp_path / "missing")
        except OSError as exc:
            if os.name != "nt" or exc.winerror != 1314:
                raise
            pytest.skip("Windows runner cannot create a symlink")
    with pytest.raises(OSError):
        publish_key(key, SYNTHETIC_KEY)
    assert list(tmp_path.iterdir()) == [key]
    if existing == "file":
        assert key.read_text(encoding="utf-8") == "original"
    elif existing == "dangling_symlink":
        assert key.is_symlink()
        assert not key.exists()
    else:
        assert key.is_dir()


@pytest.mark.skipif(os.name == "nt", reason="POSIX synchronization failure")
def test_failed_flush_cleans_up_unpublished_key(tmp_path, monkeypatch):
    def fail(_fd):
        raise OSError("synthetic fsync failure")

    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(OSError, match="synthetic fsync failure"):
        publish_key(tmp_path / "new.nxkey", SYNTHETIC_KEY)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("separate", [False, True])
def test_cli_key_output_opens_actual_encrypted_package(tmp_path, monkeypatch, capsys, separate):
    backup, _ = make_backup(tmp_path, users=8, password="synthetic-zip-password", anomalies=False)
    monkeypatch.setenv("MIRZA_TEST_ZIP_PASSWORD", "synthetic-zip-password")
    output, private = tmp_path / "output", tmp_path / "private"
    private.mkdir()
    key_path = private / "explicit.nxkey"
    args = ["convert", str(backup), "-o", str(output), "--money-unit", "toman",
            "--password-env", "MIRZA_TEST_ZIP_PASSWORD"]
    if separate:
        args += ["--key-out", str(key_path)]
    assert main(args) in (0, 2)
    pkg, = output.glob("*.nxpkg")
    if not separate:
        key_path = pkg.with_suffix(".nxkey")
    key = key_path.read_text(encoding="utf-8").strip()
    captured = capsys.readouterr()
    assert key not in captured.out + captured.err
    assert ("same directory" in captured.err) is not separate
    reader = PackageReader(pkg, key=key, workdir=tmp_path / "reader")
    try:
        assert reader.verify().manifest["package_schema_version"] == "1.5.0"
    finally:
        reader.cleanup()
    pkg_before = pkg.read_bytes()
    assert main(args) == 1
    assert pkg.read_bytes() == pkg_before
    assert key_path.read_text(encoding="utf-8").strip() == key


def test_cli_rolls_back_package_after_key_publication_failure(tmp_path, monkeypatch, capsys):
    backup, _ = make_backup(tmp_path, users=8, anomalies=False)
    output = tmp_path / "out"

    def fail(path: Path, _key: str):
        assert not path.exists()
        raise OSError("synthetic key write failure")

    monkeypatch.setattr("mirza2nexa.cli.publish_key", fail)
    assert main(["convert", str(backup), "-o", str(output), "--money-unit", "toman"]) == 1
    assert not list(output.iterdir())
    assert "synthetic key write failure" not in capsys.readouterr().err


@pytest.mark.skipif(os.name != "nt", reason="Windows filename normalization")
@pytest.mark.parametrize("name", ["report.json.", "report.json ", "report.json:key", "NUL.nxkey"])
def test_cli_rejects_windows_output_aliases(tmp_path, name):
    output = tmp_path / "out"
    args = ["convert", str(tmp_path / "unused.zip"), "-o", str(output),
            "--key-out", str(output / name), "--money-unit", "toman"]
    assert main(args) == 1
    assert not output.exists()

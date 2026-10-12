"""Builders for ZIP fixtures used by tests/test_archive.py (all created at test time in tmp_path)."""

from __future__ import annotations

import os
import stat
import struct
import zipfile
import zlib
from pathlib import Path

import pyzipper

SQL = b"-- MySQL dump\nCREATE TABLE `user` (`id` bigint);\nINSERT INTO `user` VALUES (1),(2),(3);\n"


def make_zip(path: Path, files: dict[str, bytes], compression: int = zipfile.ZIP_DEFLATED) -> Path:
    with zipfile.ZipFile(path, "w", compression=compression) as zf:
        for name, data in files.items():
            zf.writestr(zipfile.ZipInfo(name), data, compress_type=compression)
    return path


def make_aes_zip(
    path: Path,
    files: dict[str, bytes],
    password: bytes,
    compression: int = pyzipper.ZIP_DEFLATED,
    nbits: int = 256,
) -> Path:
    with pyzipper.AESZipFile(path, "w", compression=compression, encryption=pyzipper.WZ_AES) as zf:
        zf.setpassword(password)
        zf.setencryption(pyzipper.WZ_AES, nbits=nbits)
        for name, data in files.items():
            zf.writestr(name, data)
    return path


class _ZipCryptoEncrypter:
    def __init__(self, pwd: bytes):
        self.keys = [305419896, 591751049, 878082192]
        for b in pwd:
            self._update(b)

    @staticmethod
    def _crc(ch: int, crc: int) -> int:
        return zlib.crc32(bytes([ch]), crc ^ 0xFFFFFFFF) ^ 0xFFFFFFFF

    def _update(self, b: int) -> None:
        k0, k1, k2 = self.keys
        k0 = self._crc(b, k0)
        k1 = (k1 + (k0 & 0xFF)) & 0xFFFFFFFF
        k1 = (k1 * 134775813 + 1) & 0xFFFFFFFF
        k2 = self._crc((k1 >> 24) & 0xFF, k2)
        self.keys = [k0, k1, k2]

    def encrypt(self, data: bytes) -> bytes:
        out = bytearray()
        for b in data:
            k = (self.keys[2] | 2) & 0xFFFF
            ks = ((k * (k ^ 1)) >> 8) & 0xFF
            out.append(b ^ ks)
            self._update(b)
        return bytes(out)


def make_zipcrypto_zip(path: Path, files: dict[str, bytes], password: bytes, deflate: bool = True) -> Path:
    """Minimal writer for traditional PKWARE (ZipCrypto) encrypted archives."""
    body = bytearray()
    central = bytearray()
    for name, data in files.items():
        fname = name.encode()
        crc = zlib.crc32(data)
        if deflate:
            co = zlib.compressobj(6, zlib.DEFLATED, -15)
            comp = co.compress(data) + co.flush()
            method = 8
        else:
            comp, method = data, 0
        enc = _ZipCryptoEncrypter(password)
        header = os.urandom(11) + bytes([(crc >> 24) & 0xFF])
        payload = enc.encrypt(header + comp)
        offset = len(body)
        flags = 0x0001
        body += struct.pack(
            "<IHHHHHIIIHH",
            0x04034B50,
            20,
            flags,
            method,
            0,
            0x21,
            crc,
            len(payload),
            len(data),
            len(fname),
            0,
        )
        body += fname + payload
        central += struct.pack(
            "<IHHHHHHIIIHHHHHII",
            0x02014B50,
            20,
            20,
            flags,
            method,
            0,
            0x21,
            crc,
            len(payload),
            len(data),
            len(fname),
            0,
            0,
            0,
            0,
            0,
            offset,
        )
        central += fname
    eocd = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, len(files), len(files), len(central), len(body), 0)
    path.write_bytes(bytes(body + central + eocd))
    return path


def make_symlink_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("dump.sql", SQL)
        zi = zipfile.ZipInfo("link.sql")
        zi.create_system = 3
        zi.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(zi, b"/etc/passwd")
    return path


def patch_uncompressed_size(path: Path, name: str, new_size: int) -> None:
    """Forge the declared uncompressed size in both the local and central headers."""
    data = bytearray(path.read_bytes())
    fname = name.encode()
    pos = 0
    while True:
        pos = data.find(b"PK\x03\x04", pos)
        if pos < 0:
            break
        nlen = struct.unpack_from("<H", data, pos + 26)[0]
        if bytes(data[pos + 30 : pos + 30 + nlen]) == fname:
            struct.pack_into("<I", data, pos + 22, new_size)
        pos += 4
    pos = 0
    while True:
        pos = data.find(b"PK\x01\x02", pos)
        if pos < 0:
            break
        nlen = struct.unpack_from("<H", data, pos + 28)[0]
        if bytes(data[pos + 46 : pos + 46 + nlen]) == fname:
            struct.pack_into("<I", data, pos + 24, new_size)
        pos += 4
    path.write_bytes(bytes(data))


def replace_name_bytes(path: Path, old: bytes, new: bytes) -> None:
    assert len(old) == len(new)
    data = path.read_bytes()
    path.write_bytes(data.replace(old, new))

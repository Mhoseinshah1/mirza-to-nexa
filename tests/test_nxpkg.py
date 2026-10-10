from __future__ import annotations

import hashlib
import json
import os
import struct
import tracemalloc
import zipfile
from decimal import Decimal
from pathlib import Path

import pytest

from mirza2nexa.errors import Cancelled, PackageError, PackageTampered
from mirza2nexa.nxpkg import (
    InvalidKeyFile,
    PackageReader,
    PackageWriter,
    UnsupportedPackageVersion,
    WrongPackageKey,
    canonical_json,
    encode_key_file,
    generate_key_file,
    generate_passphrase,
    parse_key_file,
)
from mirza2nexa.nxpkg.crypto import (
    MAGIC,
    EncryptingWriter,
    build_header,
    derive_keys,
    encode_header,
)
from mirza2nexa.nxpkg.writer import write_deterministic_zip
from mirza2nexa.progress import CancelToken

FAST_N = 1 << 10  # scrypt cost for tests only; production default is 2**17
PASS = "correct-horse-battery-staple"
KEY = bytes(range(32))
SALT = b"S" * 16
NONCE = b"N" * 7


def manifest(**over):
    m = {
        "package_schema": "nexa.migration.mirza",
        "package_schema_version": "1.0.0",
        "import_id": "imp-0001",
        "source_fingerprint": "sha256:" + "ab" * 32,
        "converter": {"name": "mirza2nexa", "version": "0.1.0"},
        "created_at": "2026-01-01T00:00:00Z",
        "compatibility": {"min_importer_version": "1.0.0", "target": "nexa"},
    }
    m.update(over)
    return m


def stage(staging: Path, n: int = 50) -> PackageWriter:
    w = PackageWriter(staging)
    with w.open_jsonl("records/customers.jsonl") as j:
        for i in range(n):
            j.write({"id": i, "name": f"مشتری {i}", "balance": Decimal("1250.50"), "active": i % 2 == 0})
    with w.open_jsonl("reports/review-queue.jsonl") as j:
        pass  # empty jsonl
    w.write_json("reports/reconciliation.json", {"customers": n, "total": "62525.00"})
    return w


def build(tmp_path: Path, name="out.nxpkg", *, n=50, chunk_size=1 << 20, **kw) -> tuple[Path, object]:
    w = stage(tmp_path / f"stage-{name}", n)
    if "passphrase" not in kw and "key" not in kw:
        kw["key"] = KEY
    res = w.finalize(
        tmp_path / name,
        manifest=manifest(),
        kdf_n=FAST_N,
        salt=SALT,
        nonce_prefix=NONCE,
        chunk_size=chunk_size,
        **kw,
    )
    return tmp_path / name, res


def reader(tmp_path, path, **kw):
    if "passphrase" not in kw and "key" not in kw:
        kw["key"] = KEY
    return PackageReader(path, workdir=tmp_path / "work", **kw)


def split(data: bytes) -> tuple[bytes, list[bytes]]:
    hlen = struct.unpack(">I", data[7:11])[0]
    head = data[: 11 + hlen]
    chunks, off = [], len(head)
    while off < len(data):
        (n,) = struct.unpack(">I", data[off : off + 4])
        chunks.append(data[off : off + 4 + n])
        off += 4 + n
    return head, chunks


def seal(tmp_path: Path, files: dict[str, bytes], out: Path, key: bytes = KEY) -> Path:
    """Build a package from arbitrary payload files with the real envelope (simulates a key holder)."""
    sdir = tmp_path / "raw-stage"
    sdir.mkdir(exist_ok=True)
    members = []
    for name in sorted(files):
        p = sdir / name.replace("/", "__")
        p.write_bytes(files[name])
        members.append((name, p))
    kdf = {"name": "raw-key"}
    keys = derive_keys(kdf, NONCE, passphrase=None, key=key)
    hdr = encode_header(
        build_header(
            kdf=kdf,
            key_check=keys.key_check,
            nonce_prefix=NONCE,
            chunk_size=4096,
            created_by={"name": "t", "version": "0"},
        )
    )
    with open(out, "wb") as raw:
        enc = EncryptingWriter(raw, hdr, keys.enc_key, NONCE, 4096)
        write_deterministic_zip(enc, members)
        enc.close()
    return out


def payload_files(tmp_path: Path, path: Path) -> dict[str, bytes]:
    r = reader(tmp_path, path)
    r.verify()
    with zipfile.ZipFile(r.payload_path) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


# ---------------------------------------------------------------- roundtrip


def test_roundtrip_passphrase(tmp_path):
    path, res = build(tmp_path, passphrase=PASS)
    rep = reader(tmp_path, path, passphrase=PASS).verify()
    assert rep.payload_sha256 == res.payload_sha256
    assert rep.manifest == res.manifest
    assert rep.manifest["payload_format"] == "zip-v1"
    assert rep.files["records/customers.jsonl"]["records"] == 50
    assert rep.files["reports/review-queue.jsonl"] == {
        "sha256": hashlib.sha256(b"").hexdigest(),
        "size": 0,
        "records": 0,
    }
    assert rep.files["reports/reconciliation.json"]["records"] is None
    assert rep.source_fingerprint == manifest()["source_fingerprint"]
    hdr = PackageReader.read_header(path)
    assert hdr["kdf"]["name"] == "scrypt" and hdr["kdf"]["n"] == FAST_N
    assert hdr["cipher"] == "AES-256-GCM-STREAM" and hdr["chunk_size"] == 1 << 20


def test_roundtrip_key_file_and_content(tmp_path):
    text = generate_key_file()
    path, _ = build(tmp_path, key=text)
    r = PackageReader(path, key=parse_key_file(text), workdir=tmp_path / "w")
    r.verify()
    recs = list(r.iter_jsonl("records/customers.jsonl"))
    assert len(recs) == 50
    assert recs[3] == {"id": 3, "name": "مشتری 3", "balance": "1250.50", "active": False}
    assert list(r.iter_jsonl("reports/review-queue.jsonl")) == []
    assert r.read_json("reports/reconciliation.json") == {"customers": 50, "total": "62525.00"}
    assert PackageReader.read_header(path)["kdf"] == {"name": "raw-key"}
    r.cleanup()
    assert not r.payload_path.exists()


def test_header_contains_no_secrets_or_customer_data(tmp_path):
    path, _ = build(tmp_path, passphrase=PASS)
    raw = path.read_bytes()
    assert PASS.encode() not in raw
    assert "مشتری".encode() not in raw
    assert b"imp-0001" not in raw  # manifest is inside the encrypted payload
    assert set(PackageReader.read_header(path)) == {
        "format",
        "format_version",
        "cipher",
        "chunk_size",
        "kdf",
        "key_check",
        "nonce_prefix",
        "created_by",
    }


def test_empty_payload_files_ok(tmp_path):
    w = PackageWriter(tmp_path / "s")
    w.finalize(tmp_path / "e.nxpkg", key=KEY, manifest=manifest())
    rep = reader(tmp_path, tmp_path / "e.nxpkg").verify()
    assert rep.manifest["files"] == {}


def test_empty_plaintext_has_one_final_chunk(tmp_path):
    out = tmp_path / "x"
    keys = derive_keys({"name": "raw-key"}, NONCE, passphrase=None, key=KEY)
    hdr = encode_header(
        build_header(
            kdf={"name": "raw-key"},
            key_check=keys.key_check,
            nonce_prefix=NONCE,
            chunk_size=4096,
            created_by={"name": "t", "version": "0"},
        )
    )
    with open(out, "wb") as raw:
        enc = EncryptingWriter(raw, hdr, keys.enc_key, NONCE, 4096)
        enc.close()
    assert enc.chunks == 1
    _, chunks = split(out.read_bytes())
    assert len(chunks) == 1 and len(chunks[0]) == 4 + 16


# ---------------------------------------------------------------- key handling


def test_wrong_passphrase(tmp_path):
    path, _ = build(tmp_path, passphrase=PASS)
    with pytest.raises(WrongPackageKey) as ei:
        reader(tmp_path, path, passphrase="not-the-right-one").verify()
    assert ei.value.code == "package.wrong_key"
    assert isinstance(ei.value, PackageTampered)
    assert not (tmp_path / "work" / "payload.zip").exists()


def test_wrong_raw_key(tmp_path):
    path, _ = build(tmp_path)
    with pytest.raises(WrongPackageKey):
        reader(tmp_path, path, key=bytes(32)).verify()


def test_missing_secret_kind(tmp_path):
    path, _ = build(tmp_path, passphrase=PASS)
    with pytest.raises(PackageError) as ei:
        reader(tmp_path, path, key=KEY).verify()
    assert ei.value.code == "package.passphrase_required"


def test_key_file_format():
    text = generate_key_file()
    assert text.startswith("nxkey1:")
    key = parse_key_file(text)
    assert len(key) == 32 and encode_key_file(key) == text
    assert parse_key_file("  " + text[:20] + "\n" + text[20:] + "\n") == key
    bad = text[:-1] + ("A" if text[-1] != "A" else "B")
    with pytest.raises(InvalidKeyFile):
        parse_key_file(bad)
    with pytest.raises(InvalidKeyFile):
        parse_key_file("nokey:abc")


def test_generate_passphrase():
    p = generate_passphrase()
    groups = p.split("-")
    assert len(groups) == 6 and all(len(g) == 5 for g in groups)
    assert generate_passphrase() != p


def test_short_passphrase_rejected(tmp_path):
    w = stage(tmp_path / "s")
    with pytest.raises(ValueError):
        w.finalize(tmp_path / "o.nxpkg", passphrase="short", manifest=manifest(), kdf_n=FAST_N)


def test_production_scrypt_default(tmp_path):
    w = stage(tmp_path / "s", 1)
    w.finalize(tmp_path / "o.nxpkg", passphrase=PASS, manifest=manifest())
    assert PackageReader.read_header(tmp_path / "o.nxpkg")["kdf"]["n"] == 2**17
    reader(tmp_path, tmp_path / "o.nxpkg", passphrase=PASS).verify()


# ---------------------------------------------------------------- tampering


def _expect_tampered(tmp_path, path, **kw):
    with pytest.raises(PackageTampered) as ei:
        reader(tmp_path, path, **kw).verify()
    assert not (tmp_path / "work" / "payload.zip").exists()
    assert not (tmp_path / "work" / "payload.zip.partial").exists()
    return ei.value


def test_flip_body_byte(tmp_path):
    path, _ = build(tmp_path)
    data = bytearray(path.read_bytes())
    data[-5] ^= 0x01
    path.write_bytes(bytes(data))
    e = _expect_tampered(tmp_path, path)
    assert e.code == "package.tampered"


def test_flip_header_byte(tmp_path):
    path, _ = build(tmp_path)
    data = path.read_bytes()
    i = data.index(b'"mirza2nexa"') + 1  # created_by.name: still valid JSON, but AAD changes
    data = data[:i] + b"M" + data[i + 1 :]
    path.write_bytes(data)
    e = _expect_tampered(tmp_path, path)
    assert e.code == "package.tampered"


def test_flip_header_key_check_reports_wrong_key(tmp_path):
    path, _ = build(tmp_path)
    data = bytearray(path.read_bytes())
    i = data.index(b'"key_check":"') + len(b'"key_check":"')
    data[i] = ord("A") if data[i] != ord("A") else ord("B")
    path.write_bytes(bytes(data))
    e = _expect_tampered(tmp_path, path)
    assert e.code in ("package.wrong_key", "package.tampered")


def test_bad_magic(tmp_path):
    p = tmp_path / "x.nxpkg"
    p.write_bytes(b"PK\x03\x04garbage")
    with pytest.raises(PackageError) as ei:
        PackageReader.read_header(p)
    assert ei.value.code == "package.not_nxpkg"


def test_truncate_last_chunk(tmp_path):
    path, _ = build(tmp_path, n=3000, chunk_size=4096)
    head, chunks = split(path.read_bytes())
    assert len(chunks) > 3
    path.write_bytes(head + b"".join(chunks[:-1]))
    _expect_tampered(tmp_path, path)


def test_truncate_mid_chunk(tmp_path):
    path, _ = build(tmp_path, n=3000, chunk_size=4096)
    data = path.read_bytes()
    path.write_bytes(data[:-10])
    _expect_tampered(tmp_path, path)


def test_append_data(tmp_path):
    path, _ = build(tmp_path)
    path.write_bytes(path.read_bytes() + b"\x00")
    _expect_tampered(tmp_path, path)


def test_append_duplicate_chunk(tmp_path):
    path, _ = build(tmp_path, n=3000, chunk_size=4096)
    head, chunks = split(path.read_bytes())
    path.write_bytes(head + b"".join(chunks) + chunks[-1])
    _expect_tampered(tmp_path, path)


def test_swap_chunks(tmp_path):
    path, _ = build(tmp_path, n=3000, chunk_size=4096)
    head, chunks = split(path.read_bytes())
    chunks[0], chunks[1] = chunks[1], chunks[0]
    path.write_bytes(head + b"".join(chunks))
    _expect_tampered(tmp_path, path)


def test_drop_final_flag_by_cutting_after_full_chunk(tmp_path):
    path, _ = build(tmp_path, n=3000, chunk_size=4096)
    head, chunks = split(path.read_bytes())
    path.write_bytes(head + chunks[0])
    _expect_tampered(tmp_path, path)


def test_modified_manifest_reencrypted_mismatched_checksums(tmp_path):
    path, _ = build(tmp_path)
    files = payload_files(tmp_path, path)
    m = json.loads(files["manifest.json"])
    m["source_fingerprint"] = "sha256:" + "cd" * 32
    files["manifest.json"] = canonical_json(m)
    forged = seal(tmp_path, files, tmp_path / "forged.nxpkg")
    e = _expect_tampered(tmp_path, forged)
    assert "checksum mismatch: manifest.json" in e.detail


def test_modified_record_reencrypted(tmp_path):
    path, _ = build(tmp_path)
    files = payload_files(tmp_path, path)
    files["records/customers.jsonl"] = files["records/customers.jsonl"].replace(b"1250.50", b"9999.99", 1)
    _expect_tampered(tmp_path, seal(tmp_path, files, tmp_path / "forged.nxpkg"))


def test_extra_zip_entry_reencrypted(tmp_path):
    path, _ = build(tmp_path)
    files = payload_files(tmp_path, path)
    files["records/extra.jsonl"] = b'{"x":1}\n'
    _expect_tampered(tmp_path, seal(tmp_path, files, tmp_path / "forged.nxpkg"))


def test_manifest_files_must_match_checksums(tmp_path):
    path, _ = build(tmp_path)
    files = payload_files(tmp_path, path)
    m = json.loads(files["manifest.json"])
    del m["files"]["reports/reconciliation.json"]
    files["manifest.json"] = canonical_json(m)
    c = json.loads(files["checksums.json"])
    c["files"]["manifest.json"] = {
        "sha256": hashlib.sha256(files["manifest.json"]).hexdigest(),
        "size": len(files["manifest.json"]),
        "records": None,
    }
    files["checksums.json"] = canonical_json(c)
    e = _expect_tampered(tmp_path, seal(tmp_path, files, tmp_path / "forged.nxpkg"))
    assert "manifest files differ" in e.detail


# ---------------------------------------------------------------- versions


def test_unsupported_schema_major(tmp_path):
    path, _ = build(tmp_path)
    files = payload_files(tmp_path, path)
    m = json.loads(files["manifest.json"])
    m["package_schema_version"] = "2.0.0"
    files["manifest.json"] = canonical_json(m)
    with pytest.raises(UnsupportedPackageVersion) as ei:
        reader(tmp_path, seal(tmp_path, files, tmp_path / "v2.nxpkg")).verify()
    assert ei.value.code == "package.unsupported_version"


def test_minor_schema_version_accepted(tmp_path):
    w = stage(tmp_path / "s")
    w.finalize(tmp_path / "o.nxpkg", key=KEY, manifest=manifest(package_schema_version="1.3.0"))
    assert reader(tmp_path, tmp_path / "o.nxpkg").verify().manifest["package_schema_version"] == "1.3.0"


def test_unsupported_container_major(tmp_path):
    path, _ = build(tmp_path)
    data = bytearray(path.read_bytes())
    assert data[: len(MAGIC)] == MAGIC
    data[len(MAGIC)] = 2
    path.write_bytes(bytes(data))
    with pytest.raises(UnsupportedPackageVersion):
        PackageReader.read_header(path)


def test_writer_rejects_bad_manifest(tmp_path):
    w = stage(tmp_path / "s")
    for bad in (
        manifest(package_schema="other"),
        manifest(files={}),
        {"package_schema": "nexa.migration.mirza"},
        manifest(compatibility={"min_importer_version": "1.0.0", "target": "x"}),
    ):
        with pytest.raises((ValueError, PackageError)):
            w.finalize(tmp_path / "o.nxpkg", key=KEY, manifest=bad)
    assert not (tmp_path / "o.nxpkg").exists()


# ---------------------------------------------------------------- determinism


def test_determinism_identical_bytes(tmp_path):
    a, ra = build(tmp_path, "a.nxpkg", passphrase=PASS)
    b, rb = build(tmp_path, "b.nxpkg", passphrase=PASS)
    assert a.read_bytes() == b.read_bytes()
    assert ra.payload_sha256 == rb.payload_sha256


def test_payload_hash_independent_of_key(tmp_path):
    _, r1 = build(tmp_path, "a.nxpkg", passphrase=PASS)
    _, r2 = build(tmp_path, "b.nxpkg", key=KEY)
    w = stage(tmp_path / "s3")
    r3 = w.finalize(tmp_path / "c.nxpkg", key=parse_key_file(generate_key_file()), manifest=manifest())
    assert r1.payload_sha256 == r2.payload_sha256 == r3.payload_sha256
    assert (tmp_path / "a.nxpkg").read_bytes() != (tmp_path / "b.nxpkg").read_bytes()


def test_random_nonce_and_salt_by_default(tmp_path):
    w = stage(tmp_path / "s")
    w.finalize(tmp_path / "a.nxpkg", key=KEY, manifest=manifest())
    w.finalize(tmp_path / "b.nxpkg", key=KEY, manifest=manifest())
    ha, hb = PackageReader.read_header(tmp_path / "a.nxpkg"), PackageReader.read_header(tmp_path / "b.nxpkg")
    assert ha["nonce_prefix"] != hb["nonce_prefix"]


def test_zip_is_deterministic_and_sorted(tmp_path):
    path, _ = build(tmp_path)
    r = reader(tmp_path, path)
    r.verify()
    with zipfile.ZipFile(r.payload_path) as zf:
        names = zf.namelist()
        assert names == sorted(names)
        for zi in zf.infolist():
            assert zi.date_time == (1980, 1, 1, 0, 0, 0)
            assert zi.create_system == 3
            assert zi.external_attr >> 16 == 0o100644


# ---------------------------------------------------------------- canonical json


def test_canonical_json_rules():
    assert canonical_json({"b": 1, "a": [True, None, "ش"], "d": Decimal("1.10")}) == (
        '{"a":[true,null,"ش"],"b":1,"d":"1.10"}'.encode()
    )
    with pytest.raises(TypeError):
        canonical_json({"amount": 1.5})
    with pytest.raises(TypeError):
        canonical_json([float("nan")])
    with pytest.raises(TypeError):
        canonical_json({1: "x"})
    with pytest.raises(TypeError):
        canonical_json({"x": object()})


def test_jsonl_writer_rejects_float(tmp_path):
    w = PackageWriter(tmp_path / "s")
    with w.open_jsonl("records/x.jsonl") as j, pytest.raises(TypeError):
        j.write({"amount": 10.0})


def test_path_validation(tmp_path):
    w = PackageWriter(tmp_path / "s")
    for bad in (
        "../x.jsonl",
        "/abs.json",
        "a\\b.json",
        "manifest.json",
        "checksums.json",
        "a/../b",
        "",
        "a//b",
    ):
        with pytest.raises(ValueError):
            w.write_json(bad, {})


def test_untracked_staged_file_included(tmp_path):
    w = stage(tmp_path / "s")
    w.path_for("records/raw.jsonl").write_bytes(b'{"a":1}\n{"a":2}\n')
    res = w.finalize(tmp_path / "o.nxpkg", key=KEY, manifest=manifest())
    assert res.manifest["files"]["records/raw.jsonl"]["records"] == 2
    r = reader(tmp_path, tmp_path / "o.nxpkg")
    assert [x["a"] for x in r.iter_jsonl("records/raw.jsonl")] == [1, 2]


def test_unclosed_writer_blocks_finalize(tmp_path):
    w = PackageWriter(tmp_path / "s")
    w.open_jsonl("records/x.jsonl").write({"a": 1})
    with pytest.raises(ValueError):
        w.finalize(tmp_path / "o.nxpkg", key=KEY, manifest=manifest())


# ---------------------------------------------------------------- cancellation / progress


def test_cancel_leaves_no_partial_file(tmp_path):
    w = stage(tmp_path / "s", 5000)
    tok = CancelToken()
    calls = []

    def prog(done, total):
        calls.append((done, total))
        if len(calls) == 2:
            tok.cancel()

    out_dir = tmp_path / "out"
    with pytest.raises(Cancelled):
        w.finalize(
            out_dir / "o.nxpkg", key=KEY, manifest=manifest(), cancel=tok, progress=prog, chunk_size=4096
        )
    assert list(out_dir.iterdir()) == []


def test_cancel_during_verify(tmp_path):
    path, _ = build(tmp_path, n=3000, chunk_size=4096)
    tok = CancelToken()
    tok.cancel()
    with pytest.raises(Cancelled):
        reader(tmp_path, path, cancel=tok).verify()
    assert not any((tmp_path / "work").iterdir())


def test_progress_reported(tmp_path):
    seen = []
    w = stage(tmp_path / "s", 100)
    w.finalize(tmp_path / "o.nxpkg", key=KEY, manifest=manifest(), progress=lambda d, t: seen.append((d, t)))
    assert seen and seen[-1][0] == seen[-1][1]
    rseen = []
    reader(tmp_path, tmp_path / "o.nxpkg", progress=lambda d, t: rseen.append((d, t))).verify()
    assert rseen and rseen[-1][0] == rseen[-1][1]


# ---------------------------------------------------------------- bounded memory


def test_large_content_bounded_memory(tmp_path):
    w = PackageWriter(tmp_path / "s")
    target = 50 * 1024 * 1024
    with w.open_jsonl("records/big.jsonl") as j:
        i = 0
        while j.size < target:
            j.write({"id": i, "pad": os.urandom(450).hex(), "amount": str(i * 1000)})  # incompressible
            i += 1
    n_records = j.count
    tracemalloc.start()
    try:
        res = w.finalize(tmp_path / "big.nxpkg", key=KEY, manifest=manifest())
        r = reader(tmp_path, tmp_path / "big.nxpkg")
        rep = r.verify()
        count = sum(1 for _ in r.iter_jsonl("records/big.jsonl"))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert count == n_records == rep.files["records/big.jsonl"]["records"]
    assert rep.payload_sha256 == res.payload_sha256
    assert rep.chunks >= 20
    assert peak < 64 * 1024 * 1024, peak

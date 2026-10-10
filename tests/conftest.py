from __future__ import annotations

import os
from pathlib import Path

import pytest

from mirza2nexa.archive import ArchiveLimits
from mirza2nexa.pipeline import ConvertOptions, convert_archive
from mirza2nexa.synth import SynthSpec, write_zip

FAST_KDF = 2**10


@pytest.fixture(autouse=True)
def _isolated_app_home(tmp_path_factory, monkeypatch):
    """Settings/history go to a temp dir, never the developer's real profile."""
    monkeypatch.setenv("MIRZA2NEXA_HOME", str(tmp_path_factory.mktemp("apphome")))


def make_backup(tmp: Path, *, users: int = 300, password: str | None = None, seed: int = 7,
                shift: int = 0, name: str = "b.zip", anomalies: bool = True):
    path = tmp / name
    exp = write_zip(path, SynthSpec(users=users, seed=seed, balance_shift_users=shift, anomalies=anomalies),
                    password=password)
    return path, exp


def run(tmp: Path, zip_path: Path, *, ws_name: str = "ws", **kw):
    ws = tmp / ws_name
    ws.mkdir()
    kw.setdefault("kdf_n", FAST_KDF)
    kw.setdefault("money_unit", "toman")
    kw.setdefault("limits", ArchiveLimits())
    cancel = kw.pop("cancel", None)
    progress = kw.pop("progress", None)
    return convert_archive(zip_path, ws, ConvertOptions(**kw), cancel=cancel, progress=progress)


def read_jsonl(path: Path) -> list[dict]:
    import json

    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


os.environ.setdefault("PYTHONHASHSEED", "0")

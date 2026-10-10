"""User-adjustable settings (security and resource limits).

Persisted as JSON in the per-user app directory. Contains no secrets and no customer data.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .archive import ArchiveLimits


def app_dir() -> Path:
    """Per-user directory for settings and the (non-sensitive) operation history."""
    override = os.environ.get("MIRZA2NEXA_HOME")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Mirza2Nexa"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "mirza2nexa"
    base.mkdir(parents=True, exist_ok=True)
    return base


@dataclass
class Settings:
    max_archive_mb: int = 4096
    max_entries: int = 64
    max_total_uncompressed_mb: int = 20480
    max_ratio: int = 200
    min_free_disk_mb: int = 512
    # Empty means the OS temp directory. Must be a local folder the user controls.
    workspace_dir: str = ""
    # Number of non-sensitive history entries kept.
    history_limit: int = 50
    # Delete extracted SQL immediately after parsing (recommended).
    delete_sql_after_parse: bool = True

    def archive_limits(self) -> ArchiveLimits:
        mb = 1024 * 1024
        return ArchiveLimits(
            max_archive_bytes=self.max_archive_mb * mb,
            max_entries=self.max_entries,
            max_total_uncompressed=self.max_total_uncompressed_mb * mb,
            max_entry_uncompressed=self.max_total_uncompressed_mb * mb,
            max_ratio=float(self.max_ratio),
            min_free_disk_margin=self.min_free_disk_mb * mb,
        )

    def validate(self) -> list[str]:
        problems = []
        if not 1 <= self.max_archive_mb <= 64 * 1024:
            problems.append("max_archive_mb")
        if not 1 <= self.max_entries <= 10_000:
            problems.append("max_entries")
        if not 1 <= self.max_total_uncompressed_mb <= 256 * 1024:
            problems.append("max_total_uncompressed_mb")
        if not 2 <= self.max_ratio <= 10_000:
            problems.append("max_ratio")
        if not 0 <= self.min_free_disk_mb <= 1024 * 1024:
            problems.append("min_free_disk_mb")
        if not 0 <= self.history_limit <= 1000:
            problems.append("history_limit")
        if self.workspace_dir:
            p = Path(self.workspace_dir)
            if not p.is_absolute() or not p.is_dir():
                problems.append("workspace_dir")
        return problems

    @classmethod
    def load(cls) -> Settings:
        path = app_dir() / "settings.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        s = cls(**{k: v for k, v in raw.items() if k in known})
        return s if not s.validate() else cls()

    def save(self) -> None:
        path = app_dir() / "settings.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

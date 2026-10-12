"""Operator-selected NEXA target panel per legacy ``code_panel`` (docs/PANELS_AND_ADOPTION.md §6).

Mirza records its main panel with type ``marzban``; in NEXA the same panel is a **RickPanel**.
The operator states this explicitly per panel code; the converter never infers it. The source
facts (``code_panel``, Mirza panel type, every customer/service/volume/expiry/legacy id) are left
exactly as read — the target is an additional, separate record.

Provider identifiers are taken verbatim from the NEXA source (nothing is guessed):

* ``rickpanel`` — ``PROVIDER_TYPES`` in ``packages/contracts/src/provider.ts:180`` (persisted as
  ``panels.provider_type``, CHECK ``panels_provider_type_check``,
  ``apps/api/src/infrastructure/persistence/schema.ts:2024,2182``);
* display name ``RickPanel`` — ``canonicalName`` in ``provider.ts:1778`` and
  ``apps/api/src/modules/platform/providers/infrastructure/rickpanel.adapter.ts:144``;
* the NEXA legacy importer accepts a mapped panel only if it is ``rickpanel``, ACTIVE and not
  archived — ``docs/legacy-migration/importer.md:284-285``; its map entry is
  ``{codePanel, panelId}`` (``importer.md:261``).

All at ``nexa-bot@9e604def``. The RickPanel version (``1.0.0``) is an operator declaration; NEXA's
source pins no panel version, so it is carried as ``provider_version_declared`` only.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import MigrationError
from .nxpkg.canonical import canonical_json

NEXA_REF = "nexa-bot@9e604def"
# The only target this release may write: NEXA's importer refuses any other provider type.
NEXA_TARGET_PROVIDERS: dict[str, dict[str, Any]] = {
    "rickpanel": {
        "provider_type": "rickpanel",
        "display_name": "RickPanel",
        "evidence": [
            f"{NEXA_REF} packages/contracts/src/provider.ts:180 PROVIDER_TYPES",
            f"{NEXA_REF} packages/contracts/src/provider.ts:1778 canonicalName 'RickPanel'",
            f"{NEXA_REF} apps/api/src/modules/platform/providers/infrastructure/rickpanel.adapter.ts:144",
            f"{NEXA_REF} apps/api/src/infrastructure/persistence/schema.ts:2024,2182 panels.provider_type CHECK",
            f"{NEXA_REF} docs/legacy-migration/importer.md:261,284-285 panel map entry; mapped panel must be rickpanel",
        ],
    },
}
CONNECT_EXISTING = "CONNECT_EXISTING_NEXA_PANEL"
CREATE_THEN_CONNECT = "CREATE_IN_NEXA_THEN_CONNECT"

# importer.md:280 — codes are exact, trimmed, no control characters
_CODE = re.compile(r"[^\x00-\x1f\x7f]{1,64}")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_VERSION = re.compile(r"[0-9]{1,4}(?:\.[0-9]{1,4}){0,3}")


class PanelTargetError(MigrationError):
    code = "panel_target.invalid"
    message_fa = "نگاشت پنل مقصد نامعتبر است."


@dataclass(frozen=True)
class PanelTarget:
    code_panel: str
    provider_type: str
    nexa_panel_id: str | None = None
    provider_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"code_panel": self.code_panel, "provider_type": self.provider_type,
                "nexa_panel_id": self.nexa_panel_id, "provider_version": self.provider_version}


def parse_targets(raw: Any) -> list[PanelTarget]:
    """Validate operator input (list of dicts). Raises :class:`PanelTargetError` on anything unclear."""
    if raw in (None, []):
        return []
    if not isinstance(raw, list) or len(raw) > 500:
        raise PanelTargetError(detail="panel_targets must be a list")
    out: dict[str, PanelTarget] = {}
    for item in raw:
        if not isinstance(item, dict) or set(item) - {"code_panel", "provider_type", "nexa_panel_id",
                                                      "provider_version"}:
            raise PanelTargetError(detail="unknown keys")
        code = item.get("code_panel")
        if not isinstance(code, str) or code != code.strip() or not _CODE.fullmatch(code):
            raise PanelTargetError("کد پنل نامعتبر است (بدون فاصله اضافه یا نویسه کنترلی).", detail="code_panel")
        ptype = item.get("provider_type")
        if ptype not in NEXA_TARGET_PROVIDERS:
            raise PanelTargetError("نوع پنل مقصد فقط می‌تواند RickPanel (شناسه rickpanel در NEXA) باشد.",
                                   detail="provider_type")
        pid = item.get("nexa_panel_id") or None
        if pid is not None and (not isinstance(pid, str) or not _UUID.fullmatch(pid)):
            raise PanelTargetError("شناسه پنل NEXA باید UUID کوچک‌حرف باشد.", detail="nexa_panel_id")
        ver = item.get("provider_version") or None
        if ver is not None and (not isinstance(ver, str) or not _VERSION.fullmatch(ver)):
            raise PanelTargetError("نسخه پنل نامعتبر است (مثلاً 1.0.0).", detail="provider_version")
        if code in out:
            raise PanelTargetError("برای هر کد پنل فقط یک مقصد مجاز است.", detail="duplicate code_panel")
        out[code] = PanelTarget(code, ptype, pid, ver)
    return [out[k] for k in sorted(out)]


def parse_cli(values: Iterable[str]) -> list[PanelTarget]:
    """``CODE=rickpanel[,panel_id=UUID][,version=1.0.0]`` (repeatable)."""
    items = []
    for v in values:
        code, sep, rest = v.partition("=")
        if not sep:
            raise PanelTargetError(detail=f"expected CODE=provider: {v!r}")
        parts = rest.split(",")
        item: dict[str, Any] = {"code_panel": code, "provider_type": parts[0]}
        for p in parts[1:]:
            k, _, val = p.partition("=")
            key = {"panel_id": "nexa_panel_id", "version": "provider_version"}.get(k)
            if key is None:
                raise PanelTargetError(detail=f"unknown option {k!r}")
            item[key] = val
        items.append(item)
    return parse_targets(items)


def fingerprint(targets: list[PanelTarget]) -> str | None:
    if not targets:
        return None
    return hashlib.sha256(canonical_json([t.to_dict() for t in targets])).hexdigest()


def build_records(template_rows: Iterable[dict[str, Any]], targets: list[PanelTarget]
                  ) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    """One ``legacy_panel_target`` record per legacy panel code; returns (records, reconciliation, unknown codes).

    A panel without an operator selection keeps ``OPERATOR_MUST_MAP`` and no target: nothing is
    inferred from the Mirza panel type.
    """
    by_code = {t.code_panel: t for t in targets}
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in template_rows:
        code = row.get("code_panel")
        if not isinstance(code, str) or code in seen:
            continue
        seen.add(code)
        t = by_code.get(code)
        target = None
        if t is not None:
            prov = NEXA_TARGET_PROVIDERS[t.provider_type]
            target = {
                "provider_type": prov["provider_type"],
                "provider_display_name": prov["display_name"],
                "provider_version_declared": t.provider_version,
                "nexa_panel_id": t.nexa_panel_id,
                "binding": CONNECT_EXISTING if t.nexa_panel_id else CREATE_THEN_CONNECT,
                "decided_by": "operator",
                "evidence": prov["evidence"],
            }
        records.append({
            "record_type": "legacy_panel_target",
            "schema": "m2n.legacy_panel_target.v1",
            "idempotency_key": f"legacy:panel-target:{code}",
            "code_panel": code,
            "legacy_panel_id": row.get("legacy_panel_id"),
            "name_panel": row.get("name_panel"),
            # source facts, exactly as read from Mirza (never rewritten)
            "source_panel_type": row.get("panel_type"),
            "target": target,
            "mapping_state": "TARGET_SELECTED" if target else "OPERATOR_MUST_MAP",
            "nexa_panel_map_entry": ({"codePanel": code, "panelId": t.nexa_panel_id}
                                     if t is not None and t.nexa_panel_id else None),
            "credentials_in_package": False,
            "services_reprovisioned": False,
            "provision": False,
            "applies_to_live_state": False,
            "provenance": row.get("provenance"),
        })
    unknown = sorted(set(by_code) - seen)
    selected = [r for r in records if r["target"]]
    recon = {
        "panels": len(records),
        "selected": len(selected),
        "connect_existing": sum(1 for r in selected if r["target"]["binding"] == CONNECT_EXISTING),
        "create_in_nexa_then_connect": sum(1 for r in selected if r["target"]["binding"] == CREATE_THEN_CONNECT),
        "unmapped": len(records) - len(selected),
        "by_provider": {p: sum(1 for r in selected if r["target"]["provider_type"] == p)
                        for p in sorted({r["target"]["provider_type"] for r in selected})},
        "unknown_codes": len(unknown),
        "targets_fingerprint": fingerprint(targets),
        "source_panel_types_unchanged": True,
        "services_reprovisioned": False,
    }
    return records, recon, unknown


def read_template(stage_dir: Path) -> list[dict[str, Any]]:
    from .ownership_review import iter_jsonl

    return list(iter_jsonl(stage_dir / "records" / "panel_mapping_template.jsonl"))

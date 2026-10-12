"""Adoption preparation: panel identities, service status classes and product mapping proposals.

This module reads staged tables only (``src_invoice``, ``src_product``, ``src_marzban_panel``,
``src_category`` and the core's temporary ``svc`` decision table). It owns **no** Mirza table, so it
never writes coverage. It never provisions, renews, deletes or looks up a panel account, and it never
creates a NEXA panel, product or category. See ``docs/PANELS_AND_ADOPTION.md`` and
``docs/PRODUCTS_MAPPING.md``.

``records/panel_mapping_template.jsonl``   ``legacy_panel_mapping_template`` — one row per Mirza panel
                                           identity, for the operator's ``nexa-legacy-panel-map/v1``.
                                           ``nexa_panel_id: null``, ``decision: PENDING_OWNER``,
                                           ``credentials: ENTER_IN_NEXA_SECRET_FLOW``; no URL, user
                                           name, password, token or inbound ever.
``records/product_mapping_proposal.jsonl`` ``legacy_product_mapping_proposal`` — one row per
                                           ``product`` row: unit conversions with evidence, a
                                           proposed NEXA catalog shape (always ``INACTIVE``), and
                                           ``proposal_status`` ``READY_FOR_OWNER_REVIEW`` or
                                           ``BLOCKED_<reason>``. ``auto_create`` / ``sellable`` false.

Reconciliation sections: ``panel_mapping``, ``service_status_breakdown``, ``product_mapping``.

Evidence short names: **MP** mirza_pro@b645db74, **BMP** botmirzapanel@92c0ed06, **NEXA**
nexa-bot@9e604def.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..mapping import rules
from .base import Module, ModuleContext, register_module

MP = "mirza_pro@b645db74"
BMP = "botmirzapanel@92c0ed06"
NEXA = "nexa-bot@9e604def"

# ---- invoice status vocabulary → class -------------------------------------------------------
# Exact, case-sensitive (NEXA source-port.ts:112-119 compares exactly). Every value is cited from a
# public WRITER; values with no writer in the public code are UNKNOWN (MODULES.md rule 5).
ACTIVE, TEST, EXPIRED, DISABLED, REMOVED, NOT_PROVISIONED, UNKNOWN = (
    "ACTIVE", "TEST", "EXPIRED", "DISABLED", "REMOVED", "NOT_PROVISIONED", "UNKNOWN")
CLASSES = (ACTIVE, TEST, EXPIRED, DISABLED, REMOVED, NOT_PROVISIONED, UNKNOWN)

STATUS_CLASS: dict[str, str] = {
    "active": ACTIVE,            # MP function.php:1051, index.php:1880, 3001-3005 (also test accounts)
    "send_on_hold": ACTIVE,      # MP cronbot/on_hold.php:27-37 (panel account still on_hold, not started)
    "end_of_time": EXPIRED,      # BMP cron/cronday.php:33-38 (≤167000 s left); MP lists it as serviceable
    "end_of_volume": EXPIRED,    # BMP cron/cronvolume.php:36
    "sendedwarn": EXPIRED,       # BMP cron/cronday.php:36, cron/cronvolume.php:34
    "disabled": DISABLED,        # MP cronbot/configtest.php:19-20 (expired TEST account, RemoveUser first);
                                 # BMP cron/cronday.php:41-42, cron/configtest.php:15-16
    "disabledn": DISABLED,       # MP index.php:811 (customer switched the service off)
    "disablebyadmin": DISABLED,  # MP admin.php:9020, api/invoice.php:277, cronbot/disableconfig.php:30
    "removebyuser": REMOVED,     # MP index.php:1211
    "removebyadmin": REMOVED,    # MP api/invoice.php:157,163; admin.php:3768,3788
    "removedbyadmin": REMOVED,   # MP admin.php:4749,4778; BMP admin.php:1737 (cancel request accepted)
    "removeTime": REMOVED,       # MP cronbot/NoticationsService.php:146-147; BMP cron/removeexpire.php:38
    "removevolume": REMOVED,     # MP cronbot/NoticationsService.php:189-190
    "unpaid": NOT_PROVISIONED,   # MP index.php:3921-3923 (purged by cronbot/optimize_order.php:11)
    "Unsuccessful": NOT_PROVISIONED,  # MP index.php:3020 (panel create failed)
}
# Mirza's own "my services" filter (MP index.php:408, 800, 1754; cronbot/NoticationsService.php:73).
MIRZA_SERVICEABLE = frozenset({"active", "end_of_time", "end_of_volume", "sendedwarn", "send_on_hold"})
# The test-account product name each public revision writes (MP lang/fa.php:163, index.php:3002;
# BMP admin.php:99 ``name_product != 'usertest'``). Cross-check only; ``is_test`` decides.
TEST_NAME_MARKERS = frozenset({"سرویس تست", "usertest"})

# MP panels/PanelRegistry.php:6-17
PANEL_TYPES = frozenset({"marzban", "marzneshin", "x-ui_single", "hiddify", "Manualsale", "WGDashboard", "ibsng",
                         "mikrotik", "mirza_agent", "rebecca", "nexora", "wg_mate"})
# A code a mapping file could name (NEXA importer.md:283-288: trimmed, no control characters). Anything
# else is counted under one bucket and never echoed.
_CODE_OK = re.compile(r"[A-Za-z0-9_.\-/]{1,64}")
INVALID_CODE = "(invalid code_panel)"
NO_CODE = "(no code_panel)"
NOT_LIVE = "NOT_LIVE_STATUS"

# Product agent tiers: MP index.php:164 (f, n, n2), db/tables/product.php columns (default 'f').
AGENT_TIERS = {"f": ("CUSTOMER", "EVERYONE"), "n": ("RESELLER", "RESELLERS_ONLY"),
               "n2": ("RESELLER_LEVEL_2", "RESELLERS_ONLY")}
MAX_DURATION_DAYS = 3650  # NEXA packages/contracts/src/catalog.ts:253
MAX_TRAFFIC_BYTES = 1_099_511_627_776_000  # NEXA catalog.ts:254

WARNINGS = {
    "PANEL_MAPPING_PENDING_OWNER": "هیچ پنل میرزا هنوز به پنل نکسا نگاشت نشده است؛ مالک باید فایل نگاشت پنل را "
                                   "بر اساس الگوی بسته تکمیل کند. اطلاعات ورود پنل فقط در نکسا وارد می‌شود.",
    "ADOPTION_STATUS_UNKNOWN": "برخی وضعیت‌های فاکتور در کد منبع میرزا شواهد نوشتن ندارند و «نامشخص» ثبت شدند.",
    "ADOPTION_LIVE_SET_DIVERGENCE": "مجموعه وضعیت‌های «زنده» نکسا با فهرست سرویس‌های قابل‌استفاده خود میرزا یکی "
                                    "نیست؛ مالک باید درباره وضعیت‌های end_of_time / sendedwarn / send_on_hold تصمیم بگیرد.",
    "PRODUCT_PROPOSALS_REVIEW_ONLY": "پیشنهاد نگاشت محصولات فقط برای بررسی مالک است؛ هیچ محصولی خودکار ساخته "
                                     "یا قابل فروش نمی‌شود.",
}
MESSAGES = {
    "PRODUCT_PROPOSAL_BLOCKED": "تبدیل این محصول به کاتالوگ نکسا با شواهد فعلی ممکن نیست؛ دلیل در پیشنهاد ثبت شده است.",
}


# ---- helpers ---------------------------------------------------------------------------------

def status_class(status: str | None, is_test: str | None) -> str:
    """The class of one invoice. ``is_test == '1'`` wins over every status (NEXA importer.md:380-384)."""
    if is_test == "1":
        return TEST
    return STATUS_CLASS.get(status, UNKNOWN) if status is not None else UNKNOWN


def _safe_code(raw: str | None) -> str:
    cp = rules.blank_to_none(raw)
    if cp is None:
        return NO_CODE
    return cp if _CODE_OK.fullmatch(cp) else INVALID_CODE


def _bump(d: dict, *keys: str, n: int = 1) -> None:
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = d.get(keys[-1], 0) + n


def _sorted(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _sorted(obj[k]) for k in sorted(obj)}
    return obj


def _table(ctx: ModuleContext, spec: str):
    ts = [t for t in ctx.tables_for(spec) if t.stored_columns]
    return ts[0] if ts else None


def _core_decisions(ctx: ModuleContext) -> dict[int, str] | None:
    """rowno → core service decision, from convert.py's temporary ``svc`` table (live invoices only)."""
    try:
        return {r: d for r, d in ctx.conn.execute("SELECT rowno, decision FROM temp.svc ORDER BY rowno")}
    except Exception:  # noqa: BLE001 - the core skipped services (entity blocked): report it, never guess
        return None


# ---- 1+2: invoices ---------------------------------------------------------------------------

class _InvoiceFacts:
    def __init__(self) -> None:
        self.by_class: dict[str, int] = {c: 0 for c in CLASSES}
        self.by_status: dict[str, dict[str, int]] = {}      # class → raw status → n (raw only when known)
        self.unknown_status_values = 0
        self.unknown_case_variants = 0
        self.matrix: dict[str, dict[str, dict[str, int]]] = {}  # code → class → core decision → n
        self.decision_class: dict[str, dict[str, int]] = {}      # core decision → class → n
        self.by_code: dict[str, dict[str, int]] = {}             # code → class → n
        self.by_location: dict[str, dict[str, int]] = {}         # raw Service_location → class → n
        self.code_location: dict[tuple[str, str | None], int] = {}
        self.test_flag: dict[str, int] = {}                     # is_test raw bucket → n
        self.test_name_agreement: dict[str, int] = {}
        self.nexa_live_not_mirza: dict[str, int] = {}
        self.mirza_not_nexa_live: dict[str, int] = {}
        self.rows = 0
        self.core_available = True
        self.code_product_classes: dict[str, dict[str, int]] = {}


def _scan_invoices(ctx: ModuleContext) -> _InvoiceFacts | None:
    stats = _table(ctx, "invoice")
    if stats is None or not stats.sql_name:
        return None
    cols = set(stats.stored_columns)
    f = _InvoiceFacts()
    decisions = _core_decisions(ctx)
    f.core_available = decisions is not None
    pick = [c for c in ("Status", "is_test", "code_panel", "Service_location", "name_product", "code_product")]

    def col(c: str) -> str:
        return '"c_' + c + '"' if c in cols else "NULL"

    sql = (f"SELECT rowno, {', '.join(col(c) for c in pick)} FROM \"{stats.sql_name}\" ORDER BY rowno")  # noqa: S608
    for rowno, status, is_test, code, loc, name, code_product in ctx.conn.execute(sql):
        ctx.tick()
        f.rows += 1
        klass = status_class(status, is_test)
        base = status_class(status, None)
        f.by_class[klass] += 1
        if base == UNKNOWN:
            f.unknown_status_values += 1
            if status is not None and any(status.lower() == k.lower() for k in STATUS_CLASS):
                f.unknown_case_variants += 1
            _bump(f.by_status, klass, "(unknown value)")
        else:
            _bump(f.by_status, klass, status)
        live = status in rules.LIVE_STATUSES
        if decisions is None:
            decision = "CORE_DECISION_UNAVAILABLE" if live else NOT_LIVE
        else:
            decision = decisions.get(rowno, NOT_LIVE) if live else NOT_LIVE
        cp = _safe_code(code)
        _bump(f.matrix, cp, klass, decision)
        _bump(f.decision_class, decision, klass)
        _bump(f.by_code, cp, klass)
        if loc is not None:
            _bump(f.by_location, loc, klass)
        f.code_location[(cp, loc)] = f.code_location.get((cp, loc), 0) + 1
        _bump(f.test_flag, is_test if is_test in ("0", "1") else ("NULL" if is_test is None else "INVALID"))
        if name is not None:
            marker = name.strip() in TEST_NAME_MARKERS
            _bump(f.test_name_agreement, f"is_test={is_test if is_test in ('0', '1') else '?'}|"
                                         f"test_name={'yes' if marker else 'no'}")
        mirza_ok = status in MIRZA_SERVICEABLE
        if live and not mirza_ok:
            _bump(f.nexa_live_not_mirza, status)
        if mirza_ok and not live:
            _bump(f.mirza_not_nexa_live, status)
        cpr = rules.blank_to_none(code_product)
        if cpr is not None:
            _bump(f.code_product_classes, cpr, klass)
    return f


# ---- 1: panel identities ----------------------------------------------------------------------

def _registry(ctx: ModuleContext) -> list[tuple[Any, dict[str, Any]]]:
    out = []
    for stats in ctx.tables_for("marzban_panel"):
        if not stats.stored_columns:
            continue
        for _rowno, r in ctx.rows(stats, order_by=["id"]):
            out.append((stats, r))
    return out


def _suggest(classes: dict[str, int]) -> str:
    live_real = sum(n for k, n in classes.items() if k in (ACTIVE, EXPIRED, DISABLED))
    if live_real:
        return "panels_OR_unresolvedPanels"
    if classes.get(TEST):
        return "testPanels_CANDIDATE"
    if sum(classes.values()):
        return "NONE_NEEDED_NO_SERVICEABLE_INVOICES"
    return "NONE_NEEDED_NO_INVOICES"


def _template_row(ctx: ModuleContext, *, kind: str, key: str, table: str, pk: str | None,
                  checksum: str | None, identity: dict[str, Any], by_code: dict[str, int] | None,
                  by_location: dict[str, int] | None, decisions: dict[str, dict[str, int]] | None,
                  locations: list[dict[str, Any]], codes: list[dict[str, Any]]) -> dict[str, Any]:
    classes = dict(by_code or by_location or {})
    return {
        "record_type": "legacy_panel_mapping_template",
        "schema": "m2n.panel_mapping_template.v1",
        "idempotency_key": f"legacy:panel-map-template:{key}",
        "identity_kind": kind,
        **identity,
        "invoices_by_code_panel": _sorted({c: n for c, n in (by_code or {}).items() if n}),
        "invoices_by_service_location_name": _sorted({c: n for c, n in (by_location or {}).items() if n}),
        "live_core_decisions": _sorted(decisions or {}),
        "service_locations_seen_with_code": locations,
        "code_panels_seen_with_location": codes,
        "suggested_map_section": _suggest(classes),
        "map_format": "nexa-legacy-panel-map/v1",
        "nexa_panel_id": None,
        "credentials": "ENTER_IN_NEXA_SECRET_FLOW",
        "credentials_in_package": False,
        "decision": "PENDING_OWNER",
        "provision": False,
        "applies_to_live_state": False,
        "provenance": ctx.provenance(table, pk, checksum),
    }


def _panel_template(ctx: ModuleContext, f: _InvoiceFacts | None) -> dict[str, Any]:
    reg = _registry(ctx)
    by_code = f.by_code if f else {}
    by_loc = f.by_location if f else {}
    pairs = f.code_location if f else {}

    def live_decisions(code: str) -> dict[str, dict[str, int]]:
        if f is None or code not in f.matrix:
            return {}
        return {k: {d: n for d, n in v.items() if d != NOT_LIVE} for k, v in f.matrix[code].items()
                if any(d != NOT_LIVE for d in v)}

    def locs_for(code: str) -> list[dict[str, Any]]:
        return [{"service_location": ctx.redact(loc, "invoice"), "invoices": n}
                for (c, loc), n in sorted(pairs.items(), key=lambda x: (x[0][0], x[0][1] or "")) if c == code]

    def codes_for(name: str | None) -> list[dict[str, Any]]:
        return [{"code_panel": c, "invoices": n}
                for (c, loc), n in sorted(pairs.items(), key=lambda x: (x[0][0], x[0][1] or "")) if loc == name]

    sink = ctx.open("records/panel_mapping_template.jsonl")
    reg_codes: dict[str, int] = {}
    reg_names: set[str] = set()
    for _stats, r in reg:
        cp = _safe_code(r.get("code_panel"))
        reg_codes[cp] = reg_codes.get(cp, 0) + 1
        if r.get("name_panel") is not None:
            reg_names.add(r["name_panel"])
    rows = 0
    seen_keys: set[str] = set()
    out_codes_matched = 0
    registry_without_invoices = 0
    for stats, r in reg:
        ctx.tick()
        pk = r.get("id")
        cp = _safe_code(r.get("code_panel"))
        name = r.get("name_panel")
        key = f"registry:{pk}" if pk is not None else f"registry-code:{cp}"
        while key in seen_keys:
            key += "#"
        seen_keys.add(key)
        type_raw = r.get("type")
        bc = by_code.get(cp) if cp not in (NO_CODE, INVALID_CODE) else None
        bl = by_loc.get(name) if name is not None else None
        if bc:
            out_codes_matched += 1
        if not bc and not bl:
            registry_without_invoices += 1
        sink.write(_template_row(
            ctx, kind="REGISTRY", key=key, table=stats.name, pk=pk, checksum=ctx.row_checksum(stats.name, {
                c: r.get(c) for c in ("id", "code_panel", "name_panel", "type", "status")}),
            identity={"legacy_panel_id": pk, "code_panel": cp, "name_panel": ctx.redact(name, stats.name),
                      "panel_type": {"raw": type_raw, "normalized": type_raw if type_raw in PANEL_TYPES else "UNKNOWN"},
                      "registry_status_raw": r.get("status"),
                      "code_unique_in_registry": reg_codes.get(cp, 0) == 1 and cp not in (NO_CODE, INVALID_CODE)},
            by_code=bc, by_location=bl, decisions=live_decisions(cp) if bc else {},
            locations=locs_for(cp) if bc else [], codes=codes_for(name) if bl else []))
        rows += 1
    # Codes in invoices that the registry does not hold (deleted panel, or no registry in the dump).
    missing_codes = sorted(c for c in by_code if c not in reg_codes)
    for cp in missing_codes:
        ctx.tick()
        sink.write(_template_row(
            ctx, kind="INVOICE_CODE_NOT_IN_REGISTRY" if reg else "INVOICE_CODE", key=f"invoice-code:{cp}",
            table="invoice", pk=None, checksum=None,
            identity={"legacy_panel_id": None, "code_panel": cp, "name_panel": None,
                      "panel_type": {"raw": None, "normalized": "UNKNOWN"}, "registry_status_raw": None,
                      "code_unique_in_registry": False},
            by_code=by_code[cp], by_location=None, decisions=live_decisions(cp), locations=locs_for(cp), codes=[]))
        rows += 1
    # Service_location names no registry row carries (renamed or deleted panel). Only with a registry.
    missing_names = sorted(n for n in by_loc if n not in reg_names) if reg else []
    for i, name in enumerate(missing_names, 1):
        ctx.tick()
        sink.write(_template_row(
            ctx, kind="INVOICE_LOCATION_NOT_IN_REGISTRY", key=f"invoice-location:{ctx.secret_ref(name, 'loc')}",
            table="invoice", pk=None, checksum=None,
            identity={"legacy_panel_id": None, "code_panel": None, "name_panel": ctx.redact(name, "invoice"),
                      "panel_type": {"raw": None, "normalized": "UNKNOWN"}, "registry_status_raw": None,
                      "code_unique_in_registry": False, "location_ordinal": i},
            by_code=None, by_location=by_loc[name], decisions={}, locations=[], codes=codes_for(name)))
        rows += 1
    ctx.count("panel_mapping_template", "source", rows)
    ctx.count("panel_mapping_template", "converted", rows)
    if rows:
        ctx.warn("PANEL_MAPPING_PENDING_OWNER", rows, message=WARNINGS["PANEL_MAPPING_PENDING_OWNER"])
    real_codes = [c for c in by_code if c not in (NO_CODE, INVALID_CODE)]
    loc_by_code: dict[str, int] = {}
    for (c, _loc) in pairs:
        if c not in (NO_CODE, INVALID_CODE):
            loc_by_code[c] = loc_by_code.get(c, 0) + 1
    return {
        "registry_rows": len(reg),
        "registry_present": bool(reg),
        "codes_in_invoices": len(real_codes),
        "codes_in_invoices_matched_in_registry": out_codes_matched,
        "codes_in_invoices_missing_from_registry": [c for c in missing_codes if c not in (NO_CODE, INVALID_CODE)],
        "invoices_without_code_panel": sum(by_code.get(NO_CODE, {}).values()),
        "invoices_with_invalid_code_panel": sum(by_code.get(INVALID_CODE, {}).values()),
        "service_location_names_in_invoices": len(by_loc),
        "service_location_names_matched_in_registry": sum(1 for n in by_loc if n in reg_names),
        "service_location_names_missing_from_registry": len(missing_names),
        "registry_rows_without_any_invoice": registry_without_invoices,
        "codes_with_several_locations": sorted(c for c, n in loc_by_code.items() if n > 1),
        "template_rows": rows,
        "mapped": 0,
        "pending_mapping": rows,
        "credentials_exported": False,
    }


# ---- 2: status breakdown reconciliation -------------------------------------------------------

def _status_breakdown(ctx: ModuleContext, f: _InvoiceFacts | None) -> dict[str, Any]:
    if f is None:
        return {"invoice_table": "ABSENT", "by_class": {c: 0 for c in CLASSES}}
    unknown = f.by_class[UNKNOWN]
    if f.unknown_status_values:
        ctx.warn("ADOPTION_STATUS_UNKNOWN", f.unknown_status_values, message=WARNINGS["ADOPTION_STATUS_UNKNOWN"])
    diverge = sum(f.mirza_not_nexa_live.values())
    if diverge:
        ctx.warn("ADOPTION_LIVE_SET_DIVERGENCE", diverge, message=WARNINGS["ADOPTION_LIVE_SET_DIVERGENCE"])
    skipped = f.decision_class.get("TEST_INVOICE_SKIPPED", {})
    return _sorted({
        "invoices": f.rows,
        "by_class": dict(f.by_class),
        "by_class_and_status": f.by_status,
        "unknown": {"rows": unknown, "status_values_without_writer": f.unknown_status_values,
                    "case_variants_of_known_values": f.unknown_case_variants},
        "by_core_decision_and_class": f.decision_class,
        "by_panel_class_decision": f.matrix,
        "core_decisions_available": f.core_available,
        "is_test_flag": f.test_flag,
        "is_test_vs_test_product_name": f.test_name_agreement,
        "core_skipped_explained": {
            "TEST_INVOICE_SKIPPED": sum(skipped.values()),
            "meaning": "live-status invoices with is_test='1' (convert.py _services; NEXA importer.md:380-384)",
        },
        "live_set_divergence": {
            "nexa_live_not_mirza_serviceable": f.nexa_live_not_mirza,
            "mirza_serviceable_not_nexa_live": f.mirza_not_nexa_live,
            "nexa_live_statuses": sorted(rules.LIVE_STATUSES),
            "mirza_serviceable_statuses": sorted(MIRZA_SERVICEABLE),
        },
        "classes_doc": "docs/PANELS_AND_ADOPTION.md §2",
        "applies_to_live_state": False,
    })


# ---- 4: product proposals ---------------------------------------------------------------------

def _int_field(raw: str | None) -> tuple[int | None, str]:
    if raw is None:
        return None, "ABSENT"
    t = rules.js_trim(raw)
    if t == "":
        return None, "EMPTY"
    if not (t.isascii() and t.isdigit()) or len(t) > 7:
        return None, "NOT_A_NUMBER"
    return int(t), "OK"


def _product_proposals(ctx: ModuleContext, f: _InvoiceFacts | None) -> dict[str, Any]:
    stats = _table(ctx, "product")
    out: dict[str, Any] = {"rows": 0, "by_status": {}, "auto_created": 0, "sellable": 0}
    if stats is None:
        out["product_table"] = "ABSENT"
        return out
    reg = _registry(ctx)
    name_to_codes: dict[str, list[str]] = {}
    for _s, r in reg:
        if r.get("name_panel") is not None:
            name_to_codes.setdefault(r["name_panel"], []).append(_safe_code(r.get("code_panel")))
    cat_labels: set[str] = set()
    for cs in ctx.tables_for("category"):
        if "remark" in cs.stored_columns:
            for _rn, r in ctx.rows(cs, order_by=["id"]):
                if r.get("remark") is not None:
                    cat_labels.add(r["remark"].strip().lower())  # MP keyboard.php:1379 lower(trim)
    cols = set(stats.stored_columns)
    rows = list(ctx.rows(stats, order_by=["id"]))
    code_n: dict[str, int] = {}
    id_n: dict[str | None, int] = {}
    for _rn, r in rows:
        c = rules.blank_to_none(r.get("code_product"))
        if c is not None:
            code_n[c] = code_n.get(c, 0) + 1
        id_n[r.get("id")] = id_n.get(r.get("id"), 0) + 1
    sink = ctx.open("records/product_mapping_proposal.jsonl")
    cat_col = "Category" if "Category" in cols else "category" if "category" in cols else None
    for rowno, r in rows:
        ctx.tick()
        pid = r.get("id")
        key = f"legacy:product-proposal:{pid}" if pid is not None and id_n[pid] == 1 else \
            f"legacy:product-proposal:row{rowno}"
        blocked: list[str] = []
        decisions = ["NEXA_TARIFF_PRICE", "NEXA_PANEL_FOR_SCOPE", "NEXA_CATEGORY", "APPROVE_NEW_OR_EXISTING"]
        code = rules.blank_to_none(r.get("code_product"))
        if pid is None or id_n[pid] > 1:
            blocked.append("SOURCE_KEY_DUPLICATED")
        if code is None:
            blocked.append("CODE_MISSING")
        elif code_n[code] > 1:
            blocked.append("CODE_DUPLICATED")
        # Volume: GiB, Volume_constraint × 1024³ bytes (MP index.php:3982, 4515; function.php:1043).
        vol_raw = r.get("Volume_constraint")
        vol_bytes = rules.parse_traffic_gb(vol_raw) if vol_raw is not None else None
        if vol_raw is None or vol_bytes is None:
            blocked.append("VOLUME_UNREADABLE")
            vol_note = "ABSENT" if vol_raw is None else "NOT_A_NUMBER"
        elif vol_bytes > MAX_TRAFFIC_BYTES:
            blocked.append("VOLUME_OUT_OF_RANGE")
            vol_note = "OUT_OF_RANGE"
        else:
            vol_note = "ZERO_IS_UNLIMITED" if vol_bytes == 0 else "OK"
        # Duration: days, strtotime("+N days"); 0 → expire 0 = unlimited (MP function.php:1035-1037).
        days, days_note = _int_field(r.get("Service_time"))
        if days is None:
            blocked.append("DURATION_UNREADABLE")
        elif days > MAX_DURATION_DAYS:
            blocked.append("DURATION_OUT_OF_RANGE")
            days_note = "OUT_OF_RANGE"
        elif days == 0:
            days_note = "ZERO_IS_UNLIMITED"
        price, price_note = rules.parse_price(r.get("price_product")) if "price_product" in cols else (None, "ABSENT")
        if price is None:
            blocked.append("PRICE_UNREADABLE")
        tier_raw = r.get("agent")
        tier, audience = AGENT_TIERS.get(tier_raw or "", ("UNKNOWN", None))
        if tier == "UNKNOWN":
            blocked.append("AGENT_TIER_UNKNOWN")
        loc = r.get("Location")
        if loc == "/all":
            scope = {"kind": "ALL_PANELS", "resolved_code_panels": sorted(c for cs in name_to_codes.values() for c in cs),
                     "state": "RESOLVED" if reg else "NO_REGISTRY"}
        elif loc is None or loc.strip() == "":
            scope = {"kind": "NONE", "resolved_code_panels": [], "state": "UNRESOLVED"}
            blocked.append("PANEL_SCOPE_MISSING")
        else:
            codes = sorted(name_to_codes.get(loc, []))
            state = "RESOLVED" if len(codes) == 1 else "AMBIGUOUS" if codes else ("UNRESOLVED" if reg else "NO_REGISTRY")
            scope = {"kind": "PANEL_NAME", "resolved_code_panels": codes, "state": state}
            if state in ("UNRESOLVED", "AMBIGUOUS"):
                blocked.append("PANEL_SCOPE_" + state)
        hidden_raw = r.get("hide_panel")
        hidden: dict[str, Any] = {"raw_present": hidden_raw is not None, "code_panels": [], "unresolved": 0,
                                  "parse_note": "ABSENT" if hidden_raw is None else "OK"}
        if hidden_raw is not None:
            try:
                hv = json.loads(hidden_raw)
            except (ValueError, RecursionError):
                hv = None
            if isinstance(hv, dict):  # MP default '{}' (db/tables/product.php columns)
                hv = list(hv.values())
            if not isinstance(hv, list):
                hidden["parse_note"] = "NOT_A_JSON_LIST"
                blocked.append("HIDE_PANEL_UNREADABLE")
            else:
                for item in hv:
                    cs = name_to_codes.get(item, []) if isinstance(item, str) else []
                    if len(cs) == 1:
                        hidden["code_panels"].append(cs[0])
                    else:
                        hidden["unresolved"] += 1
                hidden["code_panels"] = sorted(set(hidden["code_panels"]))
        one_buy = r.get("one_buy_status")
        one_buy_norm = {"0": "ALWAYS", "1": "FIRST_PURCHASE_ONLY"}.get(one_buy or "", "UNKNOWN" if one_buy else "ABSENT")
        if one_buy_norm == "FIRST_PURCHASE_ONLY":
            decisions.append("FIRST_PURCHASE_ONLY_HAS_NO_NEXA_EQUIVALENT")
        if one_buy_norm == "UNKNOWN":
            blocked.append("ONE_BUY_STATUS_UNKNOWN")
        reset = r.get("data_limit_reset")
        if reset is not None and reset != "no_reset":
            decisions.append("DATA_LIMIT_RESET_HAS_NO_NEXA_EQUIVALENT")
        category = r.get(cat_col) if cat_col else None
        cat_match = category is not None and category.strip().lower() in cat_labels
        sale_raw = r.get("status_product") if "status_product" in cols else None
        status = "READY_FOR_OWNER_REVIEW" if not blocked else "BLOCKED_" + blocked[0]
        out["rows"] += 1
        _bump(out["by_status"], status)
        for b in blocked:
            _bump(out, "blocking_reasons", b)
        if blocked:
            ctx.review(kind="product", code="PRODUCT_PROPOSAL_BLOCKED", entity="product_proposals", table=stats.name,
                       pk=pid, severity="warning", data={"code_product": code, "reasons": blocked},
                       message=MESSAGES["PRODUCT_PROPOSAL_BLOCKED"])
        invoices = (f.code_product_classes.get(code, {}) if f and code else {})
        sink.write({
            "record_type": "legacy_product_mapping_proposal",
            "schema": "m2n.product_mapping_proposal.v1",
            "idempotency_key": key,
            "legacy_product_id": pid,
            "code_product": code,
            "title_proposal": ctx.redact(r.get("name_product"), stats.name),
            "conversions": {
                "traffic": {"raw": vol_raw, "unit": "GiB", "factor_bytes": 1024 ** 3, "note": vol_note,
                            "proposed_traffic_bytes": str(vol_bytes) if vol_bytes is not None else None,
                            "evidence": f"{MP} index.php:3982,4515; function.php:1043; NEXA catalog.ts:212 (0 = unlimited)"},
                "duration": {"raw": r.get("Service_time"), "unit": "days", "note": days_note,
                             "proposed_duration_days": days,
                             "evidence": f"{MP} function.php:1035-1037; index.php:1701-1702; NEXA catalog.ts:210"},
                "price": {"raw": r.get("price_product"), "parse_note": price_note, **ctx.money(price),
                          "use": "HISTORY_ONLY", "evidence": f"{NEXA} legacy-migration.ts:187-197"},
                "agent_tier": {"raw": tier_raw, "normalized": tier, "proposed_audience": audience,
                               "evidence": f"{MP} index.php:164,1410; NEXA catalog.ts:277"},
                "panel_scope": {"raw": ctx.redact(loc, stats.name), **scope,
                                "evidence": f"{MP} index.php:1410; panels.php:52; admin.php:2703-2706"},
                "hidden_on_panels": hidden,
                "category": {"raw": ctx.redact(category, stats.name), "matches_category_table": cat_match,
                             "nexa_category_id": None},
                "one_buy_status": {"raw": one_buy, "normalized": one_buy_norm,
                                   "evidence": f"{MP} keyboard.php:1335-1343; index.php:1410; admin.php:9337-9340"},
                "data_limit_reset": {"raw": reset, "nexa_equivalent": None},
                "sale_status": {"raw": sale_raw, "read": "status_product" in cols,
                                "evidence": f"{MP} db/tables/product.php columns; index.php:3704 (disable = sold out)"},
                "note_present": bool(r.get("note")),
            },
            "proposed_nexa_catalog": {
                "title": None, "status": "INACTIVE", "audience": audience,
                "specification": {"durationDays": days if days is not None and days <= MAX_DURATION_DAYS else None,
                                  "trafficBytes": str(vol_bytes) if vol_bytes is not None
                                  and vol_bytes <= MAX_TRAFFIC_BYTES else None,
                                  "deviceLimit": None},
                "price": None, "category_id": None, "panel_id": None,
            },
            "invoices_by_class": _sorted(invoices),
            "proposal_status": status,
            "blocking_reasons": blocked,
            "owner_decisions_required": decisions,
            "auto_create": False,
            "sellable": False,
            "provision": False,
            "applies_to_live_state": False,
            "provenance": ctx.provenance(stats.name, pid, ctx.row_checksum(stats.name, r)),
        })
    ctx.count("product_proposals", "source", out["rows"])
    ctx.count("product_proposals", "converted", out["rows"])
    if out["rows"]:
        ctx.warn("PRODUCT_PROPOSALS_REVIEW_ONLY", message=WARNINGS["PRODUCT_PROPOSALS_REVIEW_ONLY"])
    out["sale_status_column_read"] = "status_product" in cols
    return _sorted(out)


# ---- run ------------------------------------------------------------------------------------

def run(ctx: ModuleContext) -> None:
    ctx.register_messages(reviews=MESSAGES, warnings=WARNINGS)
    facts = _scan_invoices(ctx)
    ctx.reconciliation("service_status_breakdown", _status_breakdown(ctx, facts))
    ctx.reconciliation("panel_mapping", _panel_template(ctx, facts))
    ctx.reconciliation("product_mapping", _product_proposals(ctx, facts))
    ctx.mapping({"entity": "panel_mapping_template", "source_table": "marzban_panel + invoice.code_panel/Service_location",
                 "source_columns": ["id", "code_panel", "name_panel", "type", "status", "Service_location"],
                 "target": "operator's nexa-legacy-panel-map/v1 (importer.md:255-303)", "status": "proposal",
                 "note": "PENDING_OWNER; credentials only through NEXA's secret flow."})
    ctx.mapping({"entity": "product_proposals", "source_table": "product",
                 "source_columns": ["id", "code_product", "Volume_constraint", "Service_time", "price_product",
                                    "agent", "Location", "hide_panel", "one_buy_status", "data_limit_reset"],
                 "target": "legacy_product_reviews (operator APPROVED_NEW / APPROVED_EXISTING)", "status": "proposal",
                 "note": "Never sellable or auto-created; see docs/PRODUCTS_MAPPING.md."})


register_module(Module(key="adoption", title_fa="آماده‌سازی واگذاری سرویس‌ها، پنل‌ها و محصولات", run=run, order=360))

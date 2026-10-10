"""Resellers ("agents"): an independent, archive-only model of Mirza reseller history.

NEXA decision OQ-LWD-03 (nexa-bot@9e604def ``docs/open-questions.md:3422-3425``): a legacy
agent is imported as an ordinary customer — no reseller row, no tier, never credit. This module
therefore never proposes a role, a price or a credit line. It preserves what Mirza knew about
its resellers so the owner can decide later (``docs/RESELLER_CONTRACT.md``):

``records/agents/profiles.jsonl``      ``legacy_agent_profile`` — one per agent (``user.agent`` tier
                                       and/or an ``agent_manage`` owner), with ``agent_manage`` rows raw
``records/agents/agent_invoices.jsonl`` ``legacy_agent_invoice``  (``agent_invoice`` rows, raw)
``records/agents/agent_logs.jsonl``     ``legacy_agent_log``      (``agent_log`` rows, raw, redacted)
``records/agents/agent_usage.jsonl``    ``legacy_agent_usage``    (``agent_usage_snapshot`` rows, raw)
``records/agents/tier_prices.jsonl``    ``legacy_agent_price_level`` — ONE record per
                                       ``custom_service_tier_prices`` row; several levels of one agent
                                       stay several records (``level_index``/``levels_for_owner``)
``records/agents/agent_requests.jsonl`` ``legacy_agent_request``  (``Requestagent`` + ``requestagent``)

Every record carries ``activates_role: false``, ``grants_credit: false``,
``affects_wallet: false`` and ``applies_to_live_state: false``. Money-like fields of agent tables
are kept as raw text only and are never applied.

Evidence for the tables:

* ``user.agent``: tiers ``n`` (normal agent) / ``n2`` (advanced agent) — mirza_pro@b645db74
  ``admin.php:5137-5143``; ``f`` = ordinary user (``db/tables/user.php:51`` default ``f``).
  ``legacyIsAgent`` = ``rules.is_agent`` (nexa-bot ``decisions.ts:161-171``).
* ``Requestagent``: mirza_pro ``db/tables/Requestagent.php:6-11``; written by ``index.php:6112-6125``
  (``time`` = PHP ``time()`` unix seconds, ``status`` ``waiting``), reviewed by ``admin.php:5094``
  (``reject``), ``admin.php:5142-5143`` (``accept`` + ``type`` ``n``), ``admin.php:5191`` (``type``
  ``n``/``n2``). The request deducted ``agentreqprice`` from the wallet (``index.php:6105``) — history only.
* ``agent_manage``, ``agent_invoice``, ``agent_log``, ``agent_usage_snapshot``,
  ``custom_service_tier_prices``, ``requestagent`` (lowercase): present in the operator's deployed
  fork (operator statement), **no public DDL** in botmirzapanel@92c0ed06 or mirza_pro@b645db74. Their
  columns come from the runtime DDL only (credential-looking names dropped), every value is raw text
  and the owner/relation column is *inferred* from Mirza's naming convention and flagged unproven.

This file also hosts the small helpers shared by ``engagement.py`` and ``support.py``.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any

from ..catalogue import ARCHIVE, CATALOGUE, PERSONAL, PUBLIC, REVIEW, ColumnSpec, TableSpec
from ..mapping import rules
from ..synth import SynthState, register_synth, sql_str
from .base import Module, ModuleContext, register_module
from .generic import idempotency_key

MIRZA_PRO = "mirza_pro@b645db74"
BOTMIRZA = "botmirzapanel@92c0ed06"
NEXA = "nexa-bot@9e604def"
NO_DDL = ("operator: deployed-fork table; no CREATE TABLE in " + BOTMIRZA + " table.php nor "
          + MIRZA_PRO + " db/tables/*.php (searched 2026-10-10)")

# ---------------------------------------------------------------------------------------
# shared helpers (also used by engagement.py / support.py)
# ---------------------------------------------------------------------------------------

# Mirza's own spellings of a Telegram user reference: invoice.id_user, Giftcodeconsumed.id_user,
# wheel_list.id_user, support_message.iduser, reagent_report.user_id (see the table files).
# For an undocumented table this is a *naming convention*, never proof: records say so.
OWNER_COLUMNS = ("user_id", "id_user", "iduser", "userid", "agent_id", "id_agent", "chat_id", "telegram_id",
                 "from_id")
INVOICE_COLUMNS = ("id_invoice", "invoice_id", "idinvoice")
TIME_COLUMNS = ("created_at", "time", "date", "created", "timestamp", "updated_at", "snapshot_at")
FLAGS_OFF = {"activates_role": False, "grants_credit": False, "affects_wallet": False,
             "applies_to_live_state": False}


def pick_column(stats, candidates: tuple[str, ...]) -> str | None:
    """First candidate column present in the staged columns (exact, then case-insensitive)."""
    stored = list(stats.stored_columns)
    for c in candidates:
        if c in stored:
            return c
    lower = {s.lower(): s for s in stored}
    for c in candidates:
        if c.lower() in lower:
            return lower[c.lower()]
    return None


def customer_ref(ctx: ModuleContext, raw: str | None) -> dict[str, Any]:
    """Relation to a ``user`` row: raw id, normalised Telegram id and the core decision."""
    if raw is None or rules.js_trim(raw) == "":
        return {"raw_id": raw, "telegram_user_id": None, "decision": "ABSENT", "valid": False}
    decision = ctx.customer_decision(raw)
    return {"raw_id": raw, "telegram_user_id": rules.telegram_id(raw),
            "decision": decision or "NOT_IN_BACKUP", "valid": decision == "IMPORT"}


def raw_fields(ctx: ModuleContext, table: str, row: dict[str, Any], *, drop: tuple[str, ...] = ()) -> dict[str, Any]:
    """Every staged value, passed through value-level redaction (``drop`` columns omitted)."""
    return {c: ctx.redact(v, table) for c, v in row.items() if c not in drop}


def code_ref(ctx: ModuleContext, code: str | None) -> str | None:
    """Stable, non-reversible-by-reading reference for a redeemable code (never the code)."""
    if code is None:
        return None
    return "code:" + hashlib.sha256(f"m2n:code:v1:{ctx.fingerprint}:{code}".encode()).hexdigest()[:24]


def text_digest(value: str | None) -> str | None:
    return None if value is None else hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()[:16]


_YMD = re.compile(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?")


def parse_time(raw: str | None) -> tuple[str | None, str]:
    """Legacy timestamp → (ISO text, note).

    Notes: ``OK_UNIX`` (unix seconds → UTC ``Z``), ``OK_TEHRAN_LOCAL`` (``date('Y/m/d H:i:s')``,
    Gregorian, server zone Asia/Tehran with no offset stored — mirza_pro@b645db74 ``index.php:3``;
    botmirzapanel@92c0ed06 ``index.php:13``; docs/SOURCE_TABLES.md:14 — kept as a naive local time,
    never shifted), ``EMPTY``, ``JALALI_SUSPECTED`` (year < 1600), ``OUT_OF_RANGE``, ``FORMAT_UNKNOWN``.
    """
    if raw is None or rules.js_trim(raw) == "":
        return None, "EMPTY"
    t = rules.js_trim(raw)
    if re.fullmatch(r"[0-9]{9,11}", t):
        v = int(t)
        if not rules.TIME_SELL_MIN <= v < rules.TIME_SELL_MAX:
            return None, "OUT_OF_RANGE"
        return datetime.fromtimestamp(v, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "OK_UNIX"
    m = _YMD.fullmatch(t)
    if m:
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
        if y < 1600:
            return None, "JALALI_SUSPECTED"
        try:
            dt = datetime(y, mo, d, int(m[4] or 0), int(m[5] or 0), int(m[6] or 0))  # noqa: DTZ001 - Asia/Tehran, no offset stored
        except ValueError:
            return None, "FORMAT_UNKNOWN"
        return dt.strftime("%Y-%m-%dT%H:%M:%S"), "OK_TEHRAN_LOCAL"
    return None, "FORMAT_UNKNOWN"


def pk_sort_key(pk: str | None) -> tuple[int, int, str]:
    if pk is not None and pk.isdigit():
        return (0, int(pk), pk)
    return (1, 0, pk or "")


class RowKeys:
    """Deterministic idempotency keys for one table (generic recipe + ``#dup<n>`` on PK clashes)."""

    def __init__(self, ctx: ModuleContext, stats, prefix: str):
        self.ctx, self.stats, self.prefix = ctx, stats, prefix
        self.seen: dict[str, int] = {}
        self.keys: set[str] = set()
        self.dups = 0

    def __call__(self, row: dict[str, Any]) -> tuple[str, str | None, str]:
        checksum = self.ctx.row_checksum(self.stats.name, row)
        key, pk = idempotency_key(self.stats.name, self.stats.primary_key, row, checksum, self.seen)
        key = key.replace("legacy:", f"legacy:{self.prefix}:", 1)
        if key in self.keys:
            self.dups += 1
            key = f"{key}#dup{self.dups}"
        self.keys.add(key)
        return key, pk, checksum

    def finish(self, entity: str) -> None:
        if self.dups:
            self.ctx.review(kind="archive", code="ARCHIVE_DUPLICATE_PK", entity=entity, table=self.stats.name,
                            pk=None, severity="warning", data={"duplicates": self.dups},
                            message="کلید اصلی تکراری در جدول آرشیوی؛ همه ردیف‌ها با پسوند متمایز حفظ شدند.")


def account_unstaged(ctx: ModuleContext, spec_names: list[str], entity: str) -> None:
    """Rows of an owned table that could not be staged (no storable column) are still accounted."""
    for t in ctx.all_tables():
        if t.spec_name in spec_names and not t.sql_name and t.rows:
            ctx.coverage(t.name, review=t.rows, status="review")
            ctx.review(kind="table", code="TABLE_NOT_STAGED", entity=entity, table=t.name, pk=None,
                       severity="warning", data={"rows": t.rows},
                       message="هیچ ستون قابل‌ذخیره‌ای در این جدول نبود؛ ردیف‌ها شمرده شدند ولی منتقل نشدند.")


def schema_unproven(ctx: ModuleContext, stats, entity: str, spec: TableSpec | None) -> None:
    if stats.rows:
        ctx.review(kind="table", code="TABLE_NEEDS_DECISION", entity=entity, table=stats.name, pk=None,
                   severity="warning",
                   data={"rows": stats.rows, "open_questions": list(spec.open_questions) if spec else []},
                   message="معنای این جدول قطعی نیست؛ داده‌ها فقط آرشیو شدند و تصمیم مالک لازم است.")


def status_for(spec: TableSpec | None) -> str:
    return "review" if spec is not None and spec.klass == REVIEW else "archived"


# ---------------------------------------------------------------------------------------
# catalogue
# ---------------------------------------------------------------------------------------

_FORK_QUESTION = ("OQ-M2N-AG-01: column meaning, units and owner column unproven (no public DDL); "
                  "the owner must supply the fork's schema or confirm the inferred relation.")

for _name, _purpose, _fa in (
    ("agent_manage", "Deployed-fork reseller management rows (limits/credit/status, meaning unproven)",
     "مدیریت نمایندگان (فورک؛ ساختار اثبات‌نشده)"),
    ("agent_invoice", "Deployed-fork reseller invoice rows (meaning unproven)", "فاکتورهای نمایندگان (فورک)"),
    ("agent_log", "Deployed-fork reseller activity log (meaning unproven)", "گزارش فعالیت نمایندگان (فورک)"),
    ("agent_usage_snapshot", "Deployed-fork reseller usage snapshots (meaning unproven)",
     "عکس‌فوری مصرف نمایندگان (فورک)"),
    ("custom_service_tier_prices", "Deployed-fork custom-service price levels per reseller/tier (unproven)",
     "سطوح قیمت سرویس سفارشی نمایندگان (فورک)"),
    ("requestagent", "Deployed-fork lowercase agent-request table (separate from Requestagent; unproven)",
     "درخواست نمایندگی (جدول فورک با حروف کوچک)"),
):
    CATALOGUE.register(TableSpec(
        name=_name, klass=REVIEW, module="agents", purpose=_purpose, purpose_fa=_fa,
        reason="Fork table without public DDL; archived raw (redacted) for an owner decision (OQ-LWD-03).",
        nexa_target="none — legacy reseller archive (docs/RESELLER_CONTRACT.md)",
        evidence=(NO_DDL, f"{NEXA} docs/open-questions.md:3422-3425 (OQ-LWD-03)"),
        columns=None, open_questions=(_FORK_QUESTION,), sensitivity=PERSONAL,
    ))

CATALOGUE.register(TableSpec(
    name="Requestagent", klass=ARCHIVE, module="agents",
    purpose="Customer requests to become a reseller (status waiting/accept/reject, granted type)",
    purpose_fa="درخواست‌های نمایندگی کاربران",
    reason="History of reseller onboarding; NEXA has no reseller import (OQ-LWD-03).",
    nexa_target="none — legacy reseller archive (records/agents/agent_requests.jsonl)",
    evidence=(f"{MIRZA_PRO} db/tables/Requestagent.php:6-11", f"{MIRZA_PRO} index.php:6104-6125",
              f"{MIRZA_PRO} admin.php:5094,5137-5143,5191"),
    primary_key=("id",),
    columns=(
        ColumnSpec("id", PERSONAL, "Telegram id of the requester (index.php:6119 :id => $from_id)"),
        ColumnSpec("username", PERSONAL, "Telegram username at request time"),
        ColumnSpec("time", PUBLIC, "PHP time() unix seconds (index.php:6114)"),
        ColumnSpec("Description", PERSONAL, "free text typed by the customer (index.php:6115)"),
        ColumnSpec("status", PUBLIC, "waiting | accept | reject (index.php:6113, admin.php:5094,5142)"),
        ColumnSpec("type", PUBLIC, "None | n | n2 (index.php:6113, admin.php:5143,5191)"),
    ),
    sensitivity=PERSONAL,
))

AGENT_TABLES = ["agent_manage", "agent_invoice", "agent_log", "agent_usage_snapshot", "custom_service_tier_prices",
                "Requestagent", "requestagent"]
TIER_FA = {"n": "NORMAL_AGENT", "n2": "ADVANCED_AGENT"}  # admin.php:5137-5140
REQUEST_STATUS = {"waiting": "WAITING", "accept": "ACCEPTED", "reject": "REJECTED"}


# ---------------------------------------------------------------------------------------
# conversion
# ---------------------------------------------------------------------------------------

def _base(record_type: str, schema: str, key: str, table: str, pk: str | None) -> dict[str, Any]:
    return {"record_type": record_type, "schema": schema, "idempotency_key": key, "source_table": table,
            "source_pk": pk}


def _agents_from_users(ctx: ModuleContext) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    agents: dict[str, dict[str, Any]] = {}
    tiers: dict[str, int] = {}
    for stats in ctx.tables_for("user"):
        if "agent" not in stats.stored_columns:
            continue
        for _rowno, row in ctx.rows(stats, order_by=["id"]):
            ctx.tick()
            raw = row.get("agent")
            if not rules.is_agent(raw):
                continue
            tier_raw = rules.js_trim(raw or "").lower()
            tier = tier_raw if tier_raw in TIER_FA else "UNKNOWN"
            tiers[tier] = tiers.get(tier, 0) + 1
            uid = row.get("id")
            if uid is None or uid in agents:
                continue
            agents[uid] = {"tier_raw": raw, "tier": tier, "user_checksum": ctx.row_checksum("user", row)}
    return agents, tiers


def _owner_records(ctx: ModuleContext, table: str, relpath: str, record_type: str, schema: str,
                   extra=None) -> dict[str, Any]:
    """Archive one fork table row-by-row with an inferred owner relation. Returns stats."""
    out = {"rows": 0, "owner_column": None, "owner_valid": 0, "owner_missing": 0, "by_owner": {}}
    spec = ctx.spec(table)
    tables = ctx.tables_for(table)
    if not tables:
        return out
    sink = ctx.open(relpath)
    for stats in tables:
        owner_col = pick_column(stats, OWNER_COLUMNS)
        out["owner_column"] = owner_col
        keys = RowKeys(ctx, stats, "agents")
        n = 0
        for _rowno, row in ctx.rows(stats):
            ctx.tick()
            key, pk, checksum = keys(row)
            owner = customer_ref(ctx, row.get(owner_col)) if owner_col else None
            rec = _base(record_type, schema, key, stats.name, pk)
            rec["owner"] = owner
            rec["owner_column"] = owner_col
            rec["owner_relation_basis"] = "COLUMN_NAME_CONVENTION_UNPROVEN" if owner_col else "UNKNOWN"
            if extra is not None:
                extra(stats, row, rec)
            rec["fields_raw"] = raw_fields(ctx, stats.name, row)
            rec["semantics"] = "UNKNOWN"
            rec.update(FLAGS_OFF)
            rec["provenance"] = ctx.provenance(stats.name, pk, checksum)
            sink.write(rec)
            n += 1
            if owner is not None and owner["valid"]:
                out["owner_valid"] += 1
            else:
                out["owner_missing"] += 1
                ctx.review(kind="agent", code="AGENT_OWNER_UNKNOWN", entity="agents", table=stats.name, pk=pk,
                           severity="warning",
                           data={"owner_column": owner_col,
                                 "decision": owner["decision"] if owner else "NO_OWNER_COLUMN"},
                           message="ردیف نمایندگی به مشتری معتبری متصل نشد؛ فقط آرشیو شد.")
            if owner is not None and owner["raw_id"] is not None:
                out["by_owner"].setdefault(owner["raw_id"], []).append(key)
        keys.finish("agents")
        ctx.coverage(stats.name, archived=n, status=status_for(spec))
        ctx.count("agents", "converted", n)
        schema_unproven(ctx, stats, "agents", spec)
        out["rows"] += n
    return out


def _tier_prices(ctx: ModuleContext) -> dict[str, Any]:
    """One record per price level; levels of the same owner are numbered, never merged."""
    out = {"rows": 0, "owners": 0, "levels_histogram": {}, "owners_with_multiple_levels": 0, "owner_column": None,
           "unowned_rows": 0}
    tables = ctx.tables_for("custom_service_tier_prices")
    if not tables:
        return out
    spec = ctx.spec("custom_service_tier_prices")
    sink = ctx.open("records/agents/tier_prices.jsonl")
    for stats in tables:
        owner_col = pick_column(stats, OWNER_COLUMNS) or pick_column(stats, ("agent", "tier", "agent_type"))
        out["owner_column"] = owner_col
        groups: dict[str, list[str | None]] = {}
        # pass 1: level counts per owner value (raw), in deterministic order
        for _rowno, row in ctx.rows(stats):
            groups.setdefault((row.get(owner_col) if owner_col else None) or "", []).append(None)
        counts = {k: len(v) for k, v in groups.items()}
        seen: dict[str, int] = {}
        keys = RowKeys(ctx, stats, "agents")
        n = 0
        for _rowno, row in ctx.rows(stats):
            ctx.tick()
            key, pk, checksum = keys(row)
            gval = row.get(owner_col) if owner_col else None
            gkey = gval or ""
            seen[gkey] = seen.get(gkey, 0) + 1
            owner = customer_ref(ctx, gval) if owner_col in OWNER_COLUMNS else None
            rec = _base("legacy_agent_price_level", "m2n.agents.price_level.v1", key, stats.name, pk)
            rec.update({
                "owner": owner,
                "owner_column": owner_col,
                "owner_value_raw": gval,
                "owner_relation_basis": "COLUMN_NAME_CONVENTION_UNPROVEN" if owner_col else "UNKNOWN",
                "level_index": seen[gkey],
                "levels_for_owner": counts.get(gkey, 1),
                "level_order_basis": "PRIMARY_KEY_BYTE_ORDER",
                "fields_raw": raw_fields(ctx, stats.name, row),
                "price_semantics": "UNKNOWN",
                "applies_price": False,
                **FLAGS_OFF,
                "provenance": ctx.provenance(stats.name, pk, checksum),
            })
            sink.write(rec)
            n += 1
            if not gval:
                out["unowned_rows"] += 1
        keys.finish("agents")
        ctx.coverage(stats.name, archived=n, status=status_for(spec))
        ctx.count("agents", "converted", n)
        schema_unproven(ctx, stats, "agents", spec)
        for gkey, c in sorted(counts.items()):
            if not gkey:
                continue
            out["owners"] += 1
            out["levels_histogram"][str(c)] = out["levels_histogram"].get(str(c), 0) + 1
            if c > 1:
                out["owners_with_multiple_levels"] += 1
        out["rows"] += n
    out["levels_histogram"] = dict(sorted(out["levels_histogram"].items(), key=lambda kv: int(kv[0])))
    if out["owners_with_multiple_levels"]:
        ctx.review(kind="agent", code="AGENT_MULTIPLE_PRICE_LEVELS", entity="agents",
                   table="custom_service_tier_prices", pk=None, severity="warning",
                   data={"owners": out["owners_with_multiple_levels"]},
                   message="برای برخی نمایندگان چند سطح قیمت ثبت شده؛ همه سطوح جداگانه حفظ شدند و هیچ‌کدام اعمال نمی‌شود.")
    return out


def _requests(ctx: ModuleContext) -> dict[str, Any]:
    out = {"rows": 0, "by_status": {}, "requester_not_customer": 0}
    tables = ctx.tables_for("Requestagent") + ctx.tables_for("requestagent")
    if not tables:
        return out
    sink = ctx.open("records/agents/agent_requests.jsonl")
    for stats in tables:
        spec = ctx.spec(stats.name)
        documented = stats.spec_name == "Requestagent"
        owner_col = "id" if documented else pick_column(stats, OWNER_COLUMNS)
        keys = RowKeys(ctx, stats, "agents")
        n = 0
        for _rowno, row in ctx.rows(stats):
            ctx.tick()
            key, pk, checksum = keys(row)
            requester = customer_ref(ctx, row.get(owner_col)) if owner_col else None
            rec = _base("legacy_agent_request", f"m2n.agents.request.v1.{'mirza' if documented else 'fork'}", key,
                        stats.name, pk)
            if documented:
                status_raw = row.get("status")
                status = REQUEST_STATUS.get(rules.js_trim(status_raw or ""), "UNKNOWN")
                type_raw = row.get("type")
                t = rules.js_trim(type_raw or "")
                iso, note = parse_time(row.get("time"))
                rec.update({
                    "requester": requester, "owner_relation_basis": "PROVEN (index.php:6119)",
                    "status": status, "status_raw": status_raw,
                    "granted_tier": t if t in TIER_FA else ("NONE" if t == "None" else "UNKNOWN"),
                    "granted_tier_raw": type_raw,
                    "requested_at": iso, "requested_at_raw": row.get("time"), "requested_at_note": note,
                    "description": ctx.redact(row.get("Description"), stats.name),
                })
            else:
                status = "UNKNOWN"
                rec.update({"requester": requester, "owner_column": owner_col,
                            "owner_relation_basis": "COLUMN_NAME_CONVENTION_UNPROVEN" if owner_col else "UNKNOWN",
                            "status": status, "fields_raw": raw_fields(ctx, stats.name, row)})
            rec.update(FLAGS_OFF)
            rec["provenance"] = ctx.provenance(stats.name, pk, checksum)
            sink.write(rec)
            n += 1
            out["by_status"][status] = out["by_status"].get(status, 0) + 1
            if requester is None or not requester["valid"]:
                out["requester_not_customer"] += 1
                ctx.review(kind="agent", code="AGENT_REQUEST_REQUESTER_UNKNOWN", entity="agents", table=stats.name,
                           pk=pk, severity="warning",
                           data={"decision": requester["decision"] if requester else "NO_OWNER_COLUMN"},
                           message="درخواست نمایندگی متعلق به مشتری معتبری نیست؛ فقط آرشیو شد.")
        keys.finish("agents")
        ctx.coverage(stats.name, archived=n, status=status_for(spec))
        ctx.count("agents", "converted", n)
        if not documented:
            schema_unproven(ctx, stats, "agents", spec)
        out["rows"] += n
    out["by_status"] = dict(sorted(out["by_status"].items()))
    return out


def run(ctx: ModuleContext) -> None:
    agents, tiers = _agents_from_users(ctx)

    # agent_manage → embedded (raw) in profiles + its own coverage
    manage_rows: dict[str, list[dict[str, Any]]] = {}
    manage = {"rows": 0, "owner_column": None, "unowned": 0}
    for stats in ctx.tables_for("agent_manage"):
        spec = ctx.spec("agent_manage")
        owner_col = pick_column(stats, OWNER_COLUMNS)
        manage["owner_column"] = owner_col
        keys = RowKeys(ctx, stats, "agents")
        n = 0
        for _rowno, row in ctx.rows(stats):
            ctx.tick()
            key, pk, checksum = keys(row)
            owner_raw = row.get(owner_col) if owner_col else None
            item = {"manage_key": key, "source_table": stats.name, "source_pk": pk,
                    "fields_raw": raw_fields(ctx, stats.name, row), "row_checksum": checksum}
            if owner_raw is None or rules.js_trim(owner_raw) == "":
                manage["unowned"] += 1
                manage_rows.setdefault("", []).append(item)
            else:
                manage_rows.setdefault(owner_raw, []).append(item)
            n += 1
        keys.finish("agents")
        ctx.coverage(stats.name, archived=n, status=status_for(spec))
        schema_unproven(ctx, stats, "agents", spec)
        manage["rows"] += n

    owners = sorted(set(agents) | {k for k in manage_rows if k})
    sink = ctx.open("records/agents/profiles.jsonl") if owners or manage_rows.get("") else None
    profiles = {"total": 0, "from_user_table": 0, "from_agent_manage_only": 0, "agent_not_customer": 0,
                "by_tier": {}}
    for uid in owners:
        a = agents.get(uid)
        cust = customer_ref(ctx, uid)
        items = manage_rows.get(uid, [])
        tier = a["tier"] if a else "NOT_AGENT_IN_USER_TABLE"
        rec = _base("legacy_agent_profile", "m2n.agents.profile.v1", f"legacy:agents:profile:{uid}",
                    "user" if a else "agent_manage", uid)
        rec.update({
            "customer": cust,
            "tier": tier,
            "tier_label": TIER_FA.get(tier, "UNKNOWN"),
            "tier_raw": a["tier_raw"] if a else None,
            "tier_source": "user.agent" if a else None,
            "agent_manage": items,
            "agent_manage_relation_basis": "COLUMN_NAME_CONVENTION_UNPROVEN" if items else None,
            "limits_and_credit": "RAW_ONLY_NOT_APPLIED",
            "status": "ARCHIVED_NOT_ACTIVATED",
            "nexa_role": None,
            **FLAGS_OFF,
            "provenance": ctx.provenance("user" if a else "agent_manage", uid,
                                         a["user_checksum"] if a else (items[0]["row_checksum"] if items else None)),
        })
        sink.write(rec)
        profiles["total"] += 1
        profiles["by_tier"][tier] = profiles["by_tier"].get(tier, 0) + 1
        profiles["from_user_table" if a else "from_agent_manage_only"] += 1
        if not cust["valid"]:
            profiles["agent_not_customer"] += 1
            ctx.review(kind="agent", code="AGENT_NOT_CUSTOMER", entity="agents", table="agent_manage" if not a else "user",
                       pk=uid, severity="warning", data={"decision": cust["decision"], "tier": tier},
                       message="نماینده به‌عنوان مشتری معتبر منتقل نمی‌شود؛ سابقه نمایندگی فقط آرشیو شد.")
        ctx.count("agents", "converted")
    if manage_rows.get(""):
        for item in manage_rows[""]:
            rec = _base("legacy_agent_profile", "m2n.agents.profile.v1",
                        "legacy:agents:profile:unowned:" + item["manage_key"], item["source_table"], item["source_pk"])
            rec.update({"customer": None, "tier": "UNKNOWN", "tier_label": "UNKNOWN", "tier_raw": None,
                        "tier_source": None, "agent_manage": [item], "agent_manage_relation_basis": "UNKNOWN",
                        "limits_and_credit": "RAW_ONLY_NOT_APPLIED", "status": "ARCHIVED_NOT_ACTIVATED",
                        "nexa_role": None, **FLAGS_OFF,
                        "provenance": ctx.provenance(item["source_table"], item["source_pk"], item["row_checksum"])})
            sink.write(rec)
            ctx.review(kind="agent", code="AGENT_OWNER_UNKNOWN", entity="agents", table=item["source_table"],
                       pk=item["source_pk"], severity="warning", data={"owner_column": manage["owner_column"]},
                       message="ردیف نمایندگی به مشتری معتبری متصل نشد؛ فقط آرشیو شد.")
            profiles["total"] += 1
            profiles["by_tier"]["UNKNOWN"] = profiles["by_tier"].get("UNKNOWN", 0) + 1
    ctx.count("agents", "source", len(owners))

    inv = _owner_records(ctx, "agent_invoice", "records/agents/agent_invoices.jsonl", "legacy_agent_invoice",
                         "m2n.agents.invoice.v1", extra=_invoice_relation(ctx))
    logs = _owner_records(ctx, "agent_log", "records/agents/agent_logs.jsonl", "legacy_agent_log",
                          "m2n.agents.log.v1")
    usage = _owner_records(ctx, "agent_usage_snapshot", "records/agents/agent_usage.jsonl", "legacy_agent_usage",
                           "m2n.agents.usage.v1")
    prices = _tier_prices(ctx)
    reqs = _requests(ctx)
    account_unstaged(ctx, AGENT_TABLES, "agents")

    ctx.reconciliation("agents", {
        "decision": "OQ-LWD-03: archive only — no role, tier, price or credit is applied",
        "user_table_agents": sum(tiers.values()),
        "user_table_agents_by_tier": dict(sorted(tiers.items())),
        "profiles": {**profiles, "by_tier": dict(sorted(profiles["by_tier"].items()))},
        "agent_manage": manage,
        "agent_invoice": {k: v for k, v in inv.items() if k != "by_owner"},
        "agent_log": {k: v for k, v in logs.items() if k != "by_owner"},
        "agent_usage_snapshot": {k: v for k, v in usage.items() if k != "by_owner"},
        "price_levels": prices,
        "agent_requests": reqs,
        "activates_role_true": 0,
        "grants_credit_true": 0,
    })
    for entry in (
        {"entity": "agent_profiles", "source_table": "user.agent + agent_manage", "source_columns": ["agent"],
         "target": "records/agents/profiles.jsonl (archive; NEXA reseller import not approved)", "status": "archived",
         "note": "tiers n/n2 per mirza_pro admin.php:5137-5140; agent_manage relation inferred"},
        {"entity": "agent_price_levels", "source_table": "custom_service_tier_prices", "source_columns": ["*"],
         "target": "records/agents/tier_prices.jsonl", "status": "review",
         "note": "no public DDL; one record per level, never collapsed"},
        {"entity": "agent_requests", "source_table": "Requestagent / requestagent",
         "source_columns": ["id", "username", "time", "Description", "status", "type"],
         "target": "records/agents/agent_requests.jsonl", "status": "archived",
         "note": "mirza_pro db/tables/Requestagent.php:6-11"},
    ):
        ctx.mapping(entry)


def _invoice_relation(ctx: ModuleContext):
    def extra(stats, row, rec) -> None:
        col = pick_column(stats, INVOICE_COLUMNS)
        val = row.get(col) if col else None
        rec["invoice_ref"] = {"column": col, "raw": val, "exists_in_invoice_table": ctx.invoice_exists(val),
                              "basis": "COLUMN_NAME_CONVENTION_UNPROVEN" if col else "UNKNOWN"}
    return extra


register_module(Module(key="agents", title_fa="نمایندگان (آرشیو)", run=run, order=400))


# ---------------------------------------------------------------------------------------
# synthetic tables (fabricated; the fork DDL below is NOT evidence of the real fork schema)
# ---------------------------------------------------------------------------------------

def _synth(out, state: SynthState) -> None:
    rng = state.rng("agents")
    users = state.user_ids
    agents = [users[i] for i in range(0, len(users), 53)] or ["7000000000"]
    ghost = "5000000400"  # an agent who is not a customer
    s = sql_str
    out.write("DROP TABLE IF EXISTS `agent_manage`;\nCREATE TABLE `agent_manage` (\n  `id` int NOT NULL AUTO_INCREMENT,\n"
              "  `user_id` varchar(100) DEFAULT NULL,\n  `agent_type` varchar(20) DEFAULT NULL,\n"
              "  `discount_percent` varchar(20) DEFAULT NULL,\n  `credit_limit` varchar(50) DEFAULT NULL,\n"
              "  `expire_at` varchar(50) DEFAULT NULL,\n  `status` varchar(20) DEFAULT NULL,\n"
              "  `bot_token` varchar(200) DEFAULT NULL,\n  PRIMARY KEY (`id`)\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n")
    rows = []
    for i, a in enumerate([*agents, ghost], start=1):
        rows.append(f"({i},{s(a)},{s('n2' if i % 3 == 0 else 'n')},{s(str(rng.randrange(0, 30)))},"
                    f"{s(str(rng.randrange(0, 5) * 100000))},{s('1700000000' if i % 2 else '2025/13/40')},"
                    f"{s('active' if i % 4 else 'suspended')},{s('never-staged-token')})")
    state.write_rows(out, "agent_manage", rows)

    out.write("DROP TABLE IF EXISTS `custom_service_tier_prices`;\nCREATE TABLE `custom_service_tier_prices` (\n"
              "  `id` int NOT NULL AUTO_INCREMENT,\n  `agent_id` varchar(100) DEFAULT NULL,\n"
              "  `tier_level` varchar(20) DEFAULT NULL,\n  `price_per_gb` varchar(50) DEFAULT NULL,\n"
              "  `price_per_day` varchar(50) DEFAULT NULL,\n  PRIMARY KEY (`id`)\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n")
    rows = [f"(1,{s(agents[0])},{s('1')},{s('2000')},{s('500')})",
            f"(2,{s(agents[0])},{s('2')},{s('1800')},{s('450')})",
            f"(3,{s(agents[0])},{s('3')},{s('1500')},{s('400')})",
            f"(4,{s(agents[-1])},{s('1')},{s('2100')},{s('520')})",
            f"(5,{s(ghost)},{s('1')},{s('9.5')},NULL)"]
    state.write_rows(out, "custom_service_tier_prices", rows)

    out.write("DROP TABLE IF EXISTS `agent_invoice`;\nCREATE TABLE `agent_invoice` (\n  `id` int NOT NULL,\n"
              "  `agent_id` varchar(100) DEFAULT NULL,\n  `id_invoice` varchar(100) DEFAULT NULL,\n"
              "  `amount` varchar(50) DEFAULT NULL,\n  `created_at` varchar(50) DEFAULT NULL,\n  PRIMARY KEY (`id`)\n"
              ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n")
    inv = state.invoice_keys or ["10000000000000"]
    rows = [f"({i},{s(agents[i % len(agents)])},{s(inv[i % len(inv)] if i != 3 else 'missing-inv')},"
            f"{s(str(10000 * i))},{s(str(1700000000 + i))})" for i in range(1, 7)]
    state.write_rows(out, "agent_invoice", rows)

    out.write("DROP TABLE IF EXISTS `agent_log`;\nCREATE TABLE `agent_log` (\n  `id` int NOT NULL,\n"
              "  `agent_id` varchar(100) DEFAULT NULL,\n  `action` varchar(100) DEFAULT NULL,\n  `detail` text,\n"
              "  `created_at` varchar(50) DEFAULT NULL,\n  PRIMARY KEY (`id`)\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n")
    rows = [f"({i},{s(agents[i % len(agents)])},{s('sell')},{s(f'فروش سرویس آزمایشی 🚀 {i}')},{s(str(1700000100 + i))})"
            for i in range(1, 5)]
    state.write_rows(out, "agent_log", rows)

    out.write("DROP TABLE IF EXISTS `agent_usage_snapshot`;\nCREATE TABLE `agent_usage_snapshot` (\n"
              "  `id` int NOT NULL,\n  `agent_id` varchar(100) DEFAULT NULL,\n  `snapshot_at` varchar(50) DEFAULT NULL,\n"
              "  `services` varchar(20) DEFAULT NULL,\n  `traffic_gb` varchar(20) DEFAULT NULL,\n  PRIMARY KEY (`id`)\n"
              ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n")
    rows = [f"({i},{s(agents[i % len(agents)])},{s(str(1700000200 + i))},{s(str(i * 3))},{s(str(i * 10))})"
            for i in range(1, 4)]
    state.write_rows(out, "agent_usage_snapshot", rows)

    out.write("DROP TABLE IF EXISTS `Requestagent`;\nCREATE TABLE `Requestagent` (\n  `id` varchar(500) NOT NULL,\n"
              "  `username` varchar(500) NOT NULL,\n  `time` varchar(500) NOT NULL,\n  `Description` varchar(500) NOT NULL,\n"
              "  `status` varchar(500) NOT NULL,\n  `type` varchar(500) NOT NULL,\n  PRIMARY KEY (`id`)\n"
              ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;\n")
    rows = [f"({s(users[min(5, len(users) - 1)])},{s('synthetic_user_5')},{s('1700000300')},"
            f"{s('می‌خواهم نماینده شوم 🙏')},{s('waiting')},{s('None')})",
            f"({s(agents[0])},{s('synthetic_user_0')},{s('1700000301')},{s('درخواست')},{s('accept')},{s('n')})",
            f"({s('5000000401')},{s('ghost')},{s('not-a-time')},{s('x')},{s('reject')},{s('None')})"]
    state.write_rows(out, "Requestagent", rows)

    out.write("DROP TABLE IF EXISTS `requestagent`;\nCREATE TABLE `requestagent` (\n  `id` int NOT NULL,\n"
              "  `user_id` varchar(100) DEFAULT NULL,\n  `state` varchar(50) DEFAULT NULL,\n"
              "  `created_at` varchar(50) DEFAULT NULL,\n  PRIMARY KEY (`id`)\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n")
    rows = [f"(1,{s(users[min(7, len(users) - 1)])},{s('pending')},{s('2024/01/02 03:04:05')})"]
    state.write_rows(out, "requestagent", rows)


register_synth("agents", _synth, order=400)

"""Support history: departments, Mirza support messages, deployed-fork tickets and their messages.

Message text is PERSONAL. It is kept **inside the encrypted package only**, after value-level
redaction (``ctx.redact``: tokens, subscription links, card numbers …). It is never copied into
``report.json``, ``reconciliation.json``, the coverage report or review data: those carry codes,
counts and primary keys only.

``records/support/departments.jsonl``      ``legacy_support_department`` (``departman``)
``records/support/support_messages.jsonl`` ``legacy_support_message`` (``support_message``), grouped
                                           into threads with an ordered ``seq``
``records/support/tickets.jsonl``          ``legacy_ticket`` (``ticket``)
``records/support/ticket_messages.jsonl``  ``legacy_ticket_message`` (``ticket_message``), ordered per ticket

Every record: ``applies_to_live_state: false``, ``notifies_customer: false``, ``creates_ticket: false``.

Evidence:

* ``support_message`` — mirza_pro@b645db74 ``db/tables/support_message.php:6-13``. One row per
  *message*, not per thread: each customer message gets a fresh random ``Tracking``
  (``index.php:3222,3288``); ``iduser`` = customer, ``idsupport`` = admin Telegram id copied from
  ``departman``, ``name_departman`` = department name as text, ``text`` = customer message,
  ``result`` = admin answer (``index.php:3265``), ``time`` = ``date('Y/m/d H:i:s')`` Asia/Tehran.
  Status writers: ``Unseen`` (``index.php:3224``), ``Answered`` (``index.php:3264``),
  ``Customerresponse`` (``index.php:3290``). ``Pending`` and ``close`` exist only in the ENUM, with no
  public writer, so their meaning is UNKNOWN. There is no parent link: a *thread* here is the
  inferred group (customer, department name), ordered by time then id — ``thread_basis`` says so.
* ``departman`` — ``db/tables/departman.php:7-9``; written ``admin.php:6596``.
* ``ticket``, ``ticket_message`` — deployed-fork tables, no public DDL; raw, redacted, relations
  inferred from Mirza's column-name convention and flagged unproven.

NEXA target (proposal only, nothing is imported): ``tickets`` / ``ticket_messages``
(nexa-bot@9e604def ``packages/contracts/src/tickets.ts:40-45`` statuses OPEN / WAITING_FOR_CUSTOMER /
WAITING_FOR_SUPPORT / CLOSED; senders CUSTOMER / ADMIN / SYSTEM ``:144``).
"""

from __future__ import annotations

import hashlib
from typing import Any

from ..catalogue import ARCHIVE, CATALOGUE, CONFIGURATION, PERSONAL, PUBLIC, REVIEW, ColumnSpec, TableSpec
from ..synth import SynthState, register_synth, sql_str
from .agents import (
    MIRZA_PRO,
    NEXA,
    NO_DDL,
    OWNER_COLUMNS,
    TIME_COLUMNS,
    RowKeys,
    account_unstaged,
    customer_ref,
    parse_time,
    pick_column,
    pk_sort_key,
    raw_fields,
    schema_unproven,
    status_for,
)
from .base import Module, ModuleContext, register_module

FLAGS_OFF = {"applies_to_live_state": False, "notifies_customer": False, "creates_ticket": False,
             "affects_wallet": False}
TICKET_COLUMNS = ("ticket_id", "id_ticket", "ticketid", "ticket")

CATALOGUE.register(TableSpec(
    name="departman", klass=CONFIGURATION, module="support", purpose="Support departments and their admin",
    purpose_fa="دپارتمان‌های پشتیبانی",
    reason="Reference for mapping to NEXA ticket categories; never applied.",
    nexa_target="proposal: NEXA ticket categories (operator maps by hand)",
    evidence=(f"{MIRZA_PRO} db/tables/departman.php:7-9", f"{MIRZA_PRO} admin.php:6596"),
    primary_key=("id",),
    columns=(ColumnSpec("id"), ColumnSpec("idsupport", PERSONAL, "admin Telegram id"),
             ColumnSpec("name_departman", PUBLIC, "department label")),
    sensitivity=PERSONAL,
))
CATALOGUE.register(TableSpec(
    name="support_message", klass=ARCHIVE, module="support", purpose="Support messages (one row per message)",
    purpose_fa="پیام‌های پشتیبانی",
    reason="Customer conversation history; archived (redacted) inside the encrypted package only.",
    nexa_target="proposal: tickets / ticket_messages (tickets.ts:40-45,144); not imported",
    evidence=(f"{MIRZA_PRO} db/tables/support_message.php:6-13", f"{MIRZA_PRO} index.php:3213-3300"),
    primary_key=("id",),
    columns=(ColumnSpec("id"), ColumnSpec("Tracking", PUBLIC, "random 8-hex per message (index.php:3222)"),
             ColumnSpec("idsupport", PERSONAL, "admin Telegram id"), ColumnSpec("iduser", PERSONAL, "customer"),
             ColumnSpec("name_departman"), ColumnSpec("text", PERSONAL, "customer message (redacted)"),
             ColumnSpec("result", PERSONAL, "admin answer (redacted)"),
             ColumnSpec("time", PUBLIC, "date('Y/m/d H:i:s') Asia/Tehran"),
             ColumnSpec("status", PUBLIC, "Answered|Pending|Unseen|Customerresponse|close")),
    sensitivity=PERSONAL,
))
for _name, _purpose, _fa in (
    ("ticket", "Deployed-fork support tickets (meaning unproven)", "تیکت‌های پشتیبانی (فورک)"),
    ("ticket_message", "Deployed-fork ticket messages (meaning unproven)", "پیام‌های تیکت (فورک)"),
):
    CATALOGUE.register(TableSpec(
        name=_name, klass=REVIEW, module="support", purpose=_purpose, purpose_fa=_fa,
        reason="Fork table without public DDL; archived raw (redacted) for an owner decision.",
        nexa_target="proposal: tickets / ticket_messages; not imported",
        evidence=(NO_DDL, f"{NEXA} packages/contracts/src/tickets.ts:40-45"), columns=None,
        open_questions=("OQ-M2N-SU-01: columns, status values and owner/thread relation unproven; "
                        "does the fork's ticket system replace support_message?",),
        sensitivity=PERSONAL,
    ))

SUPPORT_TABLES = ["departman", "support_message", "ticket", "ticket_message"]
# Only statuses with a proven public writer get a NEXA proposal; the rest stay UNKNOWN.
SUPPORT_STATUS = {"Unseen": "OPEN", "Answered": "WAITING_FOR_CUSTOMER", "Customerresponse": "WAITING_FOR_SUPPORT"}


def _rec(record_type: str, schema: str, key: str, table: str, pk: str | None) -> dict[str, Any]:
    return {"record_type": record_type, "schema": schema, "idempotency_key": key, "source_table": table,
            "source_pk": pk}


def _time_key(iso: str | None, pk: str | None) -> tuple:
    return (0 if iso else 1, iso or "", pk_sort_key(pk))


def _write_ordered(sink, group: list[tuple[tuple, dict[str, Any]]], thread: str | None) -> int:
    """Write one thread's messages in (time, id) order with a 1-based ``seq``; empties ``group``."""
    group.sort(key=lambda item: item[0])
    for seq, (_k, rec) in enumerate(group, start=1):
        if thread is not None:
            rec["thread_ref"] = thread
        rec["seq"] = seq
        rec["thread_size"] = len(group)
        sink.write(rec)
    n = len(group)
    group.clear()
    return n


def _departments(ctx: ModuleContext, out: dict[str, Any]) -> None:
    for stats in ctx.tables_for("departman"):
        sink = ctx.open("records/support/departments.jsonl")
        keys = RowKeys(ctx, stats, "support")
        n = 0
        for _rowno, row in ctx.rows(stats):
            ctx.tick()
            key, pk, checksum = keys(row)
            rec = _rec("legacy_support_department", "m2n.support.department.v1", key, stats.name, pk)
            rec.update({"name": ctx.redact(row.get("name_departman"), stats.name),
                        "handler_admin_telegram_id": row.get("idsupport"), "nexa_category_id": None,
                        "mapping_state": "OPERATOR_MUST_MAP", **FLAGS_OFF,
                        "provenance": ctx.provenance(stats.name, pk, checksum)})
            sink.write(rec)
            n += 1
        keys.finish("support")
        ctx.coverage(stats.name, archived=n, status="archived")
        ctx.count("support", "converted", n)
        out["departments"] += n


def _support_messages(ctx: ModuleContext, out: dict[str, Any]) -> None:
    tables = ctx.tables_for("support_message")
    if not tables:
        return
    sink = ctx.open("records/support/support_messages.jsonl")
    s = out["support_message"]
    for stats in tables:
        keys = RowKeys(ctx, stats, "support")
        n = 0
        group: list[tuple[tuple, dict[str, Any]]] = []
        current: tuple[str | None, str | None] | None = None

        def flush(group: list[tuple[tuple, dict[str, Any]]], current: tuple | None) -> None:
            nonlocal n
            if not group:
                return
            uid, dep = current or (None, None)
            thread = "legacy:support:thread:" + hashlib.sha256(
                f"{uid}\x00{dep}".encode("utf-8", "surrogatepass")).hexdigest()[:24]
            n += _write_ordered(sink, group, thread)
            s["threads"] += 1
            group.clear()

        for _rowno, row in ctx.rows(stats, order_by=["iduser", "name_departman"]):
            ctx.tick()
            gk = (row.get("iduser"), row.get("name_departman"))
            if gk != current:
                flush(group, current)
                current = gk
            key, pk, checksum = keys(row)
            owner = customer_ref(ctx, row.get("iduser"))
            iso, note = parse_time(row.get("time"))
            status_raw = row.get("status")
            proposal = SUPPORT_STATUS.get(status_raw or "", "UNKNOWN")
            rec = _rec("legacy_support_message", "m2n.support.message.v1", key, stats.name, pk)
            rec.update({
                "tracking": row.get("Tracking"), "customer": owner,
                "handler_admin_telegram_id": row.get("idsupport"),
                "department_name": ctx.redact(row.get("name_departman"), stats.name),
                "customer_text": ctx.redact(row.get("text"), stats.name),
                "admin_answer": ctx.redact(row.get("result"), stats.name),
                "status_raw": status_raw, "nexa_status_proposal": proposal,
                "sent_at": iso, "sent_at_raw": row.get("time"), "sent_at_note": note,
                "thread_basis": "INFERRED_SAME_CUSTOMER_AND_DEPARTMENT", "order_basis": "TIME_THEN_ID",
                "text_class": "PERSONAL_REDACTED", **FLAGS_OFF,
                "provenance": ctx.provenance(stats.name, pk, checksum),
            })
            group.append((_time_key(iso, pk), rec))
            s["messages"] += 1
            s["by_status"][proposal] = s["by_status"].get(proposal, 0) + 1
            if note not in ("OK_UNIX", "OK_TEHRAN_LOCAL"):
                s["time_unreadable"] += 1
            if not owner["valid"]:
                s["owner_unknown"] += 1
                ctx.review(kind="support", code="TICKET_OWNER_UNKNOWN", entity="support", table=stats.name, pk=pk,
                           severity="warning", data={"decision": owner["decision"]},
                           message="صاحب پیام/تیکت پشتیبانی مشتری معتبری نیست؛ فقط آرشیو شد.")
            if proposal == "UNKNOWN":
                s["status_unknown"] += 1
        flush(group, current)
        keys.finish("support")
        ctx.coverage(stats.name, archived=n, status="archived")
        ctx.count("support", "converted", n)
    s["by_status"] = dict(sorted(s["by_status"].items()))
    if s["status_unknown"]:
        ctx.review(kind="support", code="SUPPORT_STATUS_UNKNOWN", entity="support", table="support_message",
                   pk=None, severity="warning", data={"messages": s["status_unknown"]},
                   message="وضعیت برخی پیام‌ها نوشتار شناخته‌شده‌ای ندارد؛ مقدار خام حفظ شد (UNKNOWN).")


def _tickets(ctx: ModuleContext, out: dict[str, Any]) -> set[str]:
    """Archive ``ticket`` rows. Returns the set of raw ticket primary keys (for message linking)."""
    ids: set[str] = set()
    tables = ctx.tables_for("ticket")
    if not tables:
        return ids
    sink = ctx.open("records/support/tickets.jsonl")
    t = out["ticket"]
    spec = ctx.spec("ticket")
    for stats in tables:
        owner_col = pick_column(stats, OWNER_COLUMNS)
        keys = RowKeys(ctx, stats, "support")
        n = 0
        for _rowno, row in ctx.rows(stats):
            ctx.tick()
            key, pk, checksum = keys(row)
            owner = customer_ref(ctx, row.get(owner_col)) if owner_col else None
            rec = _rec("legacy_ticket", "m2n.support.ticket.v1", key, stats.name, pk)
            rec.update({"customer": owner, "owner_column": owner_col,
                        "owner_relation_basis": "COLUMN_NAME_CONVENTION_UNPROVEN" if owner_col else "UNKNOWN",
                        "status": "UNKNOWN", "fields_raw": raw_fields(ctx, stats.name, row),
                        "text_class": "PERSONAL_REDACTED", **FLAGS_OFF,
                        "provenance": ctx.provenance(stats.name, pk, checksum)})
            sink.write(rec)
            n += 1
            if pk is not None:
                ids.add(pk)
            if owner is None or not owner["valid"]:
                t["owner_unknown"] += 1
                ctx.review(kind="support", code="TICKET_OWNER_UNKNOWN", entity="support", table=stats.name, pk=pk,
                           severity="warning",
                           data={"decision": owner["decision"] if owner else "NO_OWNER_COLUMN"},
                           message="صاحب پیام/تیکت پشتیبانی مشتری معتبری نیست؛ فقط آرشیو شد.")
        keys.finish("support")
        ctx.coverage(stats.name, archived=n, status=status_for(spec))
        ctx.count("support", "converted", n)
        schema_unproven(ctx, stats, "support", spec)
        t["tickets"] += n
    return ids


def _ticket_messages(ctx: ModuleContext, ticket_ids: set[str], out: dict[str, Any]) -> None:
    tables = ctx.tables_for("ticket_message")
    if not tables:
        return
    sink = ctx.open("records/support/ticket_messages.jsonl")
    m = out["ticket_message"]
    spec = ctx.spec("ticket_message")
    for stats in tables:
        tcol = pick_column(stats, TICKET_COLUMNS)
        time_col = pick_column(stats, TIME_COLUMNS)
        keys = RowKeys(ctx, stats, "support")
        n = 0
        group: list[tuple[tuple, dict[str, Any]]] = []
        current: str | None = None
        first = True

        for _rowno, row in ctx.rows(stats, order_by=[tcol] if tcol else None):
            ctx.tick()
            tid = row.get(tcol) if tcol else None
            if first or tid != current:
                n += _write_ordered(sink, group, None)
                current, first = tid, False
            key, pk, checksum = keys(row)
            iso, note = parse_time(row.get(time_col)) if time_col else (None, "NO_TIME_COLUMN")
            linked = tid is not None and tid in ticket_ids
            rec = _rec("legacy_ticket_message", "m2n.support.ticket_message.v1", key, stats.name, pk)
            rec.update({"ticket_ref": {"column": tcol, "raw": tid, "ticket_exists": linked,
                                       "basis": "COLUMN_NAME_CONVENTION_UNPROVEN" if tcol else "UNKNOWN"},
                        "sent_at": iso, "sent_at_note": note, "time_column": time_col,
                        "order_basis": "TIME_THEN_ID" if time_col else "ID",
                        "fields_raw": raw_fields(ctx, stats.name, row), "text_class": "PERSONAL_REDACTED",
                        **FLAGS_OFF, "provenance": ctx.provenance(stats.name, pk, checksum)})
            group.append((_time_key(iso, pk), rec))
            m["messages"] += 1
            if not linked:
                m["orphans"] += 1
                ctx.review(kind="support", code="TICKET_MESSAGE_ORPHAN", entity="support", table=stats.name, pk=pk,
                           severity="warning", data={"ticket_column": tcol},
                           message="پیام تیکت به تیکت موجودی متصل نیست؛ فقط آرشیو شد.")
        n += _write_ordered(sink, group, None)
        keys.finish("support")
        ctx.coverage(stats.name, archived=n, status=status_for(spec))
        ctx.count("support", "converted", n)
        schema_unproven(ctx, stats, "support", spec)


def run(ctx: ModuleContext) -> None:
    out: dict[str, Any] = {
        "departments": 0,
        "support_message": {"messages": 0, "threads": 0, "owner_unknown": 0, "status_unknown": 0,
                            "time_unreadable": 0, "by_status": {}},
        "ticket": {"tickets": 0, "owner_unknown": 0},
        "ticket_message": {"messages": 0, "orphans": 0},
    }
    _departments(ctx, out)
    _support_messages(ctx, out)
    ids = _tickets(ctx, out)
    _ticket_messages(ctx, ids, out)
    account_unstaged(ctx, SUPPORT_TABLES, "support")
    out["text_in_reports"] = False
    out["decision"] = "archive only inside the encrypted package; no ticket is created and nobody is notified"
    ctx.reconciliation("support", out)
    ctx.mapping({"entity": "support_history", "source_table": "support_message / ticket / ticket_message / departman",
                 "source_columns": ["iduser", "text(redacted)", "result(redacted)", "status", "time"],
                 "target": "records/support/*.jsonl (archive)", "status": "archived",
                 "note": "statuses with a proven writer get a NEXA proposal; Pending/close stay UNKNOWN"})


register_module(Module(key="support", title_fa="پشتیبانی (آرشیو)", run=run, order=420))


# ---------------------------------------------------------------------------------------
# synthetic tables (fabricated). The planted token below is FAKE and assembled at runtime.
# ---------------------------------------------------------------------------------------

def fake_bot_token() -> str:
    """A fabricated, bot-token-shaped string used to prove redaction (never a real credential)."""
    return "1234567" + "89:" + "AAH-synthetic" + "FAKEtokenForRedactionTest01"


def _synth(out, state: SynthState) -> None:
    s = sql_str
    u = state.user_ids or ["7000000000"]

    def U(i: int) -> str:  # noqa: N802
        return u[i % len(u)]

    tok = fake_bot_token()
    out.write("DROP TABLE IF EXISTS `departman`;\nCREATE TABLE `departman` (\n  `id` int(6) unsigned NOT NULL AUTO_INCREMENT,\n"
              "  `idsupport` varchar(200) NOT NULL,\n  `name_departman` varchar(600) NOT NULL,\n  PRIMARY KEY (`id`)\n"
              ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n")
    state.write_rows(out, "departman", [f"(1,{s('7999999001')},{s('عمومی')})", f"(2,{s('7999999002')},{s('فنی 🛠')})"])
    out.write("DROP TABLE IF EXISTS `support_message`;\nCREATE TABLE `support_message` (\n"
              "  `id` int(6) unsigned NOT NULL AUTO_INCREMENT,\n  `Tracking` varchar(100) NOT NULL,\n"
              "  `idsupport` varchar(100) NOT NULL,\n  `iduser` varchar(100) NOT NULL,\n"
              "  `name_departman` varchar(600) NOT NULL,\n  `text` text NOT NULL,\n  `result` text NOT NULL,\n"
              "  `time` varchar(200) NOT NULL,\n  `status` enum('Answered','Pending','Unseen','Customerresponse','close') NOT NULL,\n"
              "  PRIMARY KEY (`id`)\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n")
    state.write_rows(out, "support_message", [
        # thread A (ids out of time order on purpose)
        f"(10,{s('a1b2c3d4')},{s('7999999001')},{s(U(30))},{s('عمومی')},{s('سلام، سرویسم وصل نمی‌شود 😟')},"
        f"{s('بررسی شد ✅')},{s('2024/07/01 10:00:00')},{s('Answered')})",
        f"(9,{s('a1b2c3d5')},{s('7999999001')},{s(U(30))},{s('عمومی')},"
        f"{s('توکن رباتم این است: ' + tok + ' لطفا وصل کنید')},{s('0')},{s('2024/07/02 09:00:00')},"
        f"{s('Customerresponse')})",
        f"(11,{s('a1b2c3d6')},{s('7999999001')},{s(U(30))},{s('عمومی')},{s('ممنون')},{s('0')},"
        f"{s('1403/04/12 08:00:00')},{s('close')})",
        # orphan owner
        f"(12,{s('a1b2c3d7')},{s('7999999002')},{s('5000000800')},{s('فنی 🛠')},{s('پیام بدون صاحب')},{s('0')},"
        f"{s('2024/07/03 12:00:00')},{s('Unseen')})",
        f"(13,{s('a1b2c3d8')},{s('7999999002')},{s(U(31))},{s('فنی 🛠')},{s('Pending test')},{s('0')},"
        f"{s('not a date')},{s('Pending')})",
    ])
    out.write("DROP TABLE IF EXISTS `ticket`;\nCREATE TABLE `ticket` (\n  `id` int NOT NULL,\n"
              "  `user_id` varchar(100) DEFAULT NULL,\n  `subject` varchar(200) DEFAULT NULL,\n"
              "  `status` varchar(50) DEFAULT NULL,\n  `created_at` varchar(50) DEFAULT NULL,\n  PRIMARY KEY (`id`)\n"
              ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n")
    state.write_rows(out, "ticket", [
        f"(1,{s(U(40))},{s('مشکل اتصال 🔌')},{s('open')},{s('2024/08/01 10:00:00')})",
        f"(2,{s('5000000900')},{s('بدون صاحب')},{s('closed')},{s('2024/08/02 10:00:00')})",
        f"(3,NULL,{s('ticket with null owner')},{s('open')},{s('1700000000')})",
    ])
    out.write("DROP TABLE IF EXISTS `ticket_message`;\nCREATE TABLE `ticket_message` (\n  `id` int NOT NULL,\n"
              "  `ticket_id` varchar(20) DEFAULT NULL,\n  `sender` varchar(20) DEFAULT NULL,\n  `message` text,\n"
              "  `created_at` varchar(50) DEFAULT NULL,\n  PRIMARY KEY (`id`)\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n")
    state.write_rows(out, "ticket_message", [
        f"(1,{s('1')},{s('user')},{s('سلام 👋 کار نمی‌کند')},{s('2024/08/01 10:05:00')})",
        f"(2,{s('1')},{s('admin')},{s('لینک جدید: https://syn.example/sub/abcdef123456')},{s('2024/08/01 10:01:00')})",
        f"(3,{s('1')},{s('user')},{s('my token ' + tok)},{s('2024/08/01 11:00:00')})",
        f"(4,{s('77')},{s('user')},{s('orphan message')},{s('2024/08/03 10:00:00')})",
    ])


register_synth("support", _synth, order=420)

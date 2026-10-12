"""Service ownership model (Phase 3): who owned each Mirza service, and how we know.

Module key ``ownership`` (order 350, after ``services_ext`` 300). It reads staged tables only
(``src_invoice``, every table resolving to ``service_other`` / ``cancel_service``, the core's
``cust`` and ``svc`` temp tables). It owns **no** Mirza table, so it never writes coverage, and it
never provisions, deletes, renews or otherwise touches a panel account.

``records/service_ownership.jsonl``  ``legacy_service_ownership`` (schema ``mirza.service_ownership.v1``),
one per live service candidate, per invoice referenced by ``service_other``/``cancel_service``, and
per referenced panel username that has no invoice at all.

Reconciliation section ``service_ownership``; review kind ``service_ownership`` (codes ``OWNERSHIP_*``).
``services_ext`` uses the same model (:func:`model_for`) to decide which ``SERVICE_OWNER_CONFLICT``
rows a proven transfer chain explains. See ``docs/OWNERSHIP.md`` for the evidence and the rules.

Evidence short names: **MP** mirza_pro@b645db74, **BMP** botmirzapanel@92c0ed06 (BMP has no
transfer feature at all: no ``transfer`` and no ``invoice.id_user`` update in its PHP).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..mapping import rules
from ..synth import SynthState, register_synth, sql_str
from .base import Module, ModuleContext, register_module

MP = "mirza_pro@b645db74"
SCHEMA = "mirza.service_ownership.v1"
RECORDS = "records/service_ownership.jsonl"
MAX_EVIDENCE = 200  # per record; the rest is counted, never silently dropped

# ---- rules (each cited; docs/OWNERSHIP.md explains them) ---------------------------------------
RULES: dict[str, str] = {
    "OWN-R1": f"invoice.id_user is the ownership field: every customer service action requires "
              f"invoiceBelongsToUser (= invoice.id_user) ({MP} function.php:735-738; index.php:1386,1922,2836,2882)",
    "OWN-R2": f"customer transfer: UPDATE invoice.id_user := target, then INSERT service_other(id_user=sender, "
              f"username, value=target, type='transfertouser', price 0); target and logged value are the same "
              f"variable ({MP} index.php:2833-2900)",
    "OWN-R3": f"a chain of transfertouser rows ordered by service_other.id (AUTO_INCREMENT, {MP} "
              f"db/tables/service_other.php:5) whose links connect (value[i] == id_user[i+1]), with no self "
              f"transfer ({MP} index.php:2876) and whose last target equals invoice.id_user proves the current "
              f"owner; its first sender is the original owner",
    "OWN-R4": "when a transfer chain exists, an owner-claim row of any holder (previous holders and the current "
              "owner alike) is explained only if its service_other.id lies inside one of that holder's holding "
              "windows of the proven chain; the current owner's windows are the final one plus any earlier one "
              "(A→B→A) (cancel_service has no time: holder membership)",
    "OWN-R5": f"extend_user_by_admin.id_user is the acting admin ({MP} admin.php:7646) or the owner "
              f"({MP} api/invoice.php:308): never an owner claim",
    "OWN-R6": f"extends_not_user.id_user is whoever paid a renewal looked up by panel username, with no invoice "
              f"ownership check ({MP} index.php:611-640,6635-6720): payer, not an owner claim",
    "OWN-R7": f"admin/API whole-account transfer renames user.id and rewrites invoice, Payment_report, "
              f"support_message, service_other, Giftcodeconsumed (+botsaz) with no log row ({MP} "
              f"admin.php:7020-7044; api/users.php:423-448); cancel_service and transfertouser.value keep the old id",
    "OWN-R8": f"cancel_service.id_user is the requester, who passed invoiceBelongsToUser ({MP} index.php:2705-2708,"
              f"2765-2768); the table has no time column",
    "OWN-R9": f"service_other/cancel_service reach an invoice by panel username only ({MP} function.php:1258; "
              f"cronbot/gift.php:47); with several invoices per username a row is attributed only by an exact "
              f"owner match; invoice.username is never updated by MP",
}

# ---- vocabulary --------------------------------------------------------------------------------
TRANSFER_TYPE = "transfertouser"
ADMIN_ACTOR_TYPES = frozenset({"extend_user_by_admin"})   # OWN-R5
PAYER_TYPES = frozenset({"extends_not_user"})            # OWN-R6

DECISIONS = ("CONFIRMED_CURRENT_OWNER", "CONFIRMED_TRANSFER", "NO_CONFLICT", "AMBIGUOUS_OWNER", "ORPHAN_OWNER",
             "INVALID_OWNER")
FINAL_DECISIONS = frozenset({"CONFIRMED_CURRENT_OWNER", "CONFIRMED_TRANSFER", "NO_CONFLICT"})

# Review reasons, in precedence order (the first one present is the manual_review_reason).
REASONS: dict[str, str] = {
    "OWNERSHIP_OWNER_ID_INVALID": "مالک ثبت‌شده در فاکتور شناسهٔ تلگرام معتبر نیست؛ مالک سرویس قابل تعیین نیست.",
    "OWNERSHIP_OWNER_NOT_IN_USER_TABLE": "مالک ثبت‌شده در فاکتور در جدول کاربران نیست؛ مالک سرویس قابل تعیین نیست.",
    "OWNERSHIP_MULTIPLE_CLAIMANTS": "چند فاکتور فعال با مالکان مختلف همین نام کاربری سرویس را دارند.",
    "OWNERSHIP_USERNAME_SHARED_BY_INVOICES": "چند فاکتور این نام کاربری را دارند و ردیف عملیات/انتقال به یکی از آن‌ها "
                                             "قابل نسبت‌دادن نیست.",
    "OWNERSHIP_TRANSFER_ORDER_UNPROVEN": "ترتیب ردیف‌های انتقال قابل اثبات نیست (شناسهٔ غیرعددی یا چند جدول).",
    "OWNERSHIP_TRANSFER_TARGET_INVALID": "گیرنده یا فرستندهٔ یک انتقال شناسهٔ تلگرام معتبر نیست.",
    "OWNERSHIP_TRANSFER_CHAIN_BROKEN": "زنجیرهٔ انتقال‌ها پیوسته نیست (گیرندهٔ یک انتقال فرستندهٔ انتقال بعدی نیست).",
    "OWNERSHIP_TRANSFER_NOT_APPLIED_TO_INVOICE": "آخرین گیرندهٔ انتقال با مالک فاکتور یکی نیست؛ انتقال در فاکتور "
                                                 "اعمال نشده یا انتقال ثبت‌نشده‌ای رخ داده است.",
    "OWNERSHIP_ACTION_OUTSIDE_HOLDING_WINDOW": "یکی از دارندگان سرویس خارج از بازهٔ مالکیت خود (مثلاً پیش از دریافت یا پس از "
                                               "انتقال سرویس) روی آن عملیات ثبت کرده است.",
    "OWNERSHIP_ACTOR_NOT_IN_USER_TABLE": "ردیفی از کاربری که در جدول کاربران نیست به این سرویس اشاره می‌کند "
                                         "(ممکن است انتقال کل حساب توسط ادمین باشد که ثبت نمی‌شود).",
    "OWNERSHIP_OWNER_MISMATCH_NO_TRANSFER_RECORD": "کاربر دیگری روی این سرویس عملیات ثبت کرده و هیچ انتقال ثبت‌شده‌ای "
                                                   "آن را توضیح نمی‌دهد.",
    "OWNERSHIP_NO_INVOICE_FOR_USERNAME": "برای این نام کاربری سرویس هیچ فاکتوری در بکاپ نیست؛ مالک تعیین نمی‌شود.",
}
_REASON_ORDER = {code: i for i, code in enumerate(REASONS)}
CHAIN_REASONS = frozenset({"OWNERSHIP_TRANSFER_ORDER_UNPROVEN", "OWNERSHIP_TRANSFER_TARGET_INVALID",
                           "OWNERSHIP_TRANSFER_CHAIN_BROKEN", "OWNERSHIP_TRANSFER_NOT_APPLIED_TO_INVOICE"})
# Informational codes (never a manual review on their own).
INFO_CODES = ("OWNERSHIP_TRANSFER_CHAIN_PROVEN", "OWNERSHIP_ADMIN_ACTOR_NOT_OWNER", "OWNERSHIP_PAYER_NOT_OWNER")
# Core service decisions whose review row already says the same thing (no duplicate review row).
_CORE_COVERS = {"ORPHAN_OWNER": {"ORPHAN"}, "INVALID_OWNER": {"ORPHAN"},
                "OWNERSHIP_MULTIPLE_CLAIMANTS": {"AMBIGUOUS_OWNERSHIP"}}


# ---- model -------------------------------------------------------------------------------------

@dataclass
class _Inv:
    rowno: int
    key: str | None
    owner: str | None
    uname: str | None          # rules.panel_username (lower-cased) or None
    username_raw: str | None
    status: str | None
    live: bool
    core: str | None           # svc decision for live rows


@dataclass
class _Ref:
    table: str                 # real dump table name
    spec: str                  # service_other | cancel_service
    pk: str | None
    rowno: int
    seq: int | None            # service_other.id as int (insertion order), None if not an integer
    uname: str
    username_raw: str | None
    actor: str | None          # raw id_user
    type_raw: str | None
    role: str                  # TRANSFER | OWNER_CLAIM | ADMIN_ACTOR | PAYER
    target: str | None         # transfertouser value (js_trim'd)
    time_raw: str | None


@dataclass
class _Result:
    inv: _Inv | None
    uname: str | None
    username_raw: str | None
    decision: str
    reasons: list[str]
    info: list[str]
    evidence: list[dict[str, Any]]
    evidence_total: int
    transfers: list[dict[str, Any]]
    original: dict[str, Any] | None
    current: dict[str, Any] | None
    final_owner: str | None
    first_ref: _Ref | None = None
    ref_reasons: dict[str, int] = field(default_factory=dict)


def _int_id(pk: str | None) -> int | None:
    if pk is None:
        return None
    t = rules.js_trim(pk)
    return int(t) if t.isdigit() and len(t) <= 18 else None


def _role(spec: str, type_raw: str | None) -> str:
    if spec == "cancel_service":
        return "OWNER_CLAIM"                       # OWN-R8
    if type_raw == TRANSFER_TYPE:
        return "TRANSFER"                          # OWN-R2
    if type_raw in ADMIN_ACTOR_TYPES:
        return "ADMIN_ACTOR"                       # OWN-R5
    if type_raw in PAYER_TYPES:
        return "PAYER"                             # OWN-R6
    return "OWNER_CLAIM"  # every other type (also undocumented ones): id_user is the acting owner


class OwnershipModel:
    """Deterministic, read-only ownership model over the staged backup."""

    def __init__(self, ctx: ModuleContext):
        self.ctx = ctx
        self._exists_cache: dict[str, str | None] = {}
        self.invoices: list[_Inv] = []
        self.inv_by_uname: dict[str, list[_Inv]] = {}
        self.refs_by_uname: dict[str, list[_Ref]] = {}
        self.ref_rel: dict[tuple[str, int], tuple[str, str | None]] = {}
        self.legacy_conflict: dict[tuple[str, int], bool] = {}
        self.results: list[_Result] = []
        self._load_invoices()
        self._load_refs()
        self._legacy_rule()
        self._evaluate()

    # ---- loading -------------------------------------------------------------------------
    def customer(self, raw: str | None) -> str | None:
        """Core customer decision for a raw user id; ``None`` when the id is not in the user table."""
        if raw is None:
            return None
        if raw not in self._exists_cache:
            self._exists_cache[raw] = self.ctx.customer_decision(raw)
        return self._exists_cache[raw]

    def _load_invoices(self) -> None:
        c = self.ctx.conn
        t = next((s for s in self.ctx.all_tables() if s.name == "invoice"), None)
        cols = t.stored_columns if t is not None and t.sql_name else ()
        if not all(x in cols for x in ("id_invoice", "id_user", "username")):
            return
        core: dict[int, str] = {}
        if c.execute("SELECT 1 FROM sqlite_temp_master WHERE type='table' AND name='svc'").fetchone():
            core = dict(c.execute("SELECT rowno, decision FROM svc"))
        status = '"c_Status"' if "Status" in cols else "NULL"
        cur = c.execute(f"SELECT rowno, c_id_invoice, c_id_user, c_username, {status} "  # noqa: S608
                        "FROM src_invoice ORDER BY rowno")
        for rowno, key, owner, uname, st in iter(cur.fetchone, None):
            inv = _Inv(rowno, key, owner, rules.panel_username(uname), uname, st, st in rules.LIVE_STATUSES,
                       core.get(rowno))
            self.invoices.append(inv)
            if inv.uname is not None:
                self.inv_by_uname.setdefault(inv.uname, []).append(inv)
        for lst in self.inv_by_uname.values():   # services_ext candidate order: key, then dump order
            lst.sort(key=lambda i: (i.key is None, i.key or "", i.rowno))

    def _load_refs(self) -> None:
        for spec in ("service_other", "cancel_service"):
            for stats in [t for t in self.ctx.all_tables() if t.spec_name == spec and t.sql_name]:
                cols = stats.stored_columns
                if not all(x in cols for x in ("id_user", "username")):
                    continue
                for rowno, r in self.ctx.rows(stats, order_by=["id"]):
                    u = rules.panel_username(r.get("username"))
                    if u is None:
                        continue
                    type_raw = r.get("type") if spec == "service_other" else None
                    role = _role(spec, type_raw)
                    tgt = r.get("value") if role == "TRANSFER" else None
                    self.refs_by_uname.setdefault(u, []).append(_Ref(
                        table=stats.name, spec=spec, pk=r.get("id"), rowno=rowno,
                        seq=_int_id(r.get("id")) if spec == "service_other" else None, uname=u,
                        username_raw=r.get("username"), actor=r.get("id_user"), type_raw=type_raw, role=role,
                        target=rules.js_trim(tgt) if tgt else tgt, time_raw=r.get("time")))

    # ---- the previous services_ext rule (for before/after counts only) -------------------
    def _legacy_rule(self) -> None:
        """services_ext before Phase 3: a mismatch was 'explained' when the actor appeared anywhere in a
        transfertouser row of the username and the invoice owner had received any transfer of it."""
        for u, refs in self.refs_by_uname.items():
            invs = self.inv_by_uname.get(u, [])
            if len(invs) != 1:
                continue  # several invoices: FOUND only on an exact owner match, so never a mismatch
            owner = invs[0].owner
            xfers = [(r.actor, r.target) for r in refs if r.role == "TRANSFER"]
            parts = {x for pair in xfers for x in pair if x is not None}
            received = {t for _, t in xfers}
            for r in refs:
                if r.role == "ADMIN_ACTOR" or r.actor == owner:
                    continue
                explained = r.actor is not None and owner is not None and r.actor in parts and owner in received
                self.legacy_conflict[(r.table, r.rowno)] = not explained

    # ---- evaluation ----------------------------------------------------------------------
    def _evaluate(self) -> None:
        seen_rows: set[int] = set()
        for u in sorted(set(self.inv_by_uname) | set(self.refs_by_uname)):
            invs = self.inv_by_uname.get(u, [])
            refs = sorted(self.refs_by_uname.get(u, []), key=lambda r: (r.spec, r.table, r.seq is None,
                                                                         r.seq or 0, r.rowno))
            if not invs:
                self.results.append(self._unlinked(u, refs))
                continue
            attributed, unattributable = self._attribute(invs, refs)
            live_owners = sorted({i.owner or "" for i in invs if i.live})
            for inv in invs:
                if not inv.live and not attributed[inv.rowno] and not unattributable:
                    continue  # neither a live candidate nor referenced: nothing to say
                seen_rows.add(inv.rowno)
                self.results.append(self._evaluate_invoice(inv, attributed[inv.rowno], bool(unattributable),
                                                           len(live_owners) > 1 and inv.live, len(invs)))
        for inv in self.invoices:  # live candidates whose username is not a valid panel username
            if inv.live and inv.uname is None and inv.rowno not in seen_rows:
                self.results.append(self._evaluate_invoice(inv, [], False, False, 1))
        self.results.sort(key=lambda r: (r.inv is None, r.inv.key or "" if r.inv else r.uname or "",
                                         r.inv.rowno if r.inv else 0))

    def _attribute(self, invs: list[_Inv], refs: list[_Ref]) -> tuple[dict[int, list[_Ref]], list[_Ref]]:
        """OWN-R9: one invoice → every row; several → owner-claim/payer rows by exact owner match only."""
        out: dict[int, list[_Ref]] = {i.rowno: [] for i in invs}
        if len(invs) == 1:
            out[invs[0].rowno] = list(refs)
            return out, []
        unattributable: list[_Ref] = []
        for r in refs:
            if r.role == "ADMIN_ACTOR":
                continue  # not ownership evidence and not attributable (services_ext: AMBIGUOUS)
            same = [i for i in invs if r.actor is not None and i.owner == r.actor]
            if r.role != "TRANSFER" and len(same) == 1:
                out[same[0].rowno].append(r)
            elif r.role in ("OWNER_CLAIM", "TRANSFER"):
                unattributable.append(r)
        return out, unattributable

    def _chain(self, transfers: list[_Ref], owner: str | None) -> tuple[str | None, list[dict[str, Any]]]:
        """``(None, links)`` for a proven chain (OWN-R3), else ``(reason, links)``."""
        from .services_ext import parse_time

        links: list[dict[str, Any]] = []
        reason: str | None = None
        if any(t.seq is None for t in transfers) or len({t.table for t in transfers}) > 1:
            reason = "OWNERSHIP_TRANSFER_ORDER_UNPROVEN"
        prev: _Ref | None = None
        for n, t in enumerate(transfers, 1):
            ok = True
            if rules.telegram_id(t.actor) is None or rules.telegram_id(t.target) is None:
                ok = False
                reason = reason or "OWNERSHIP_TRANSFER_TARGET_INVALID"
            elif t.actor == t.target or (prev is not None and prev.target != t.actor):
                ok = False
                reason = reason or "OWNERSHIP_TRANSFER_CHAIN_BROKEN"
            links.append({"seq": n, "source_table": t.table, "pk": t.pk, "service_other_id": t.seq,
                          "from_telegram_user_id": rules.telegram_id(t.actor),
                          "to_telegram_user_id": rules.telegram_id(t.target),
                          "from_in_user_table": self.customer(t.actor) is not None,
                          "to_in_user_table": self.customer(t.target) is not None,
                          "time": parse_time(t.time_raw), "link_ok": ok, "rule": "OWN-R2"})
            prev = t
        if reason is None and transfers and transfers[-1].target != owner:
            reason = "OWNERSHIP_TRANSFER_NOT_APPLIED_TO_INVOICE"
        return reason, links

    @staticmethod
    def _windows(transfers: list[_Ref]) -> list[tuple[str | None, float, float]]:
        """Holding windows (holder, from_seq, to_seq) of a proven chain, by service_other.id."""
        out: list[tuple[str | None, float, float]] = [(transfers[0].actor, float("-inf"), float(transfers[0].seq))]
        for i, t in enumerate(transfers):
            end = float(transfers[i + 1].seq) if i + 1 < len(transfers) else float("inf")
            out.append((t.target, float(t.seq), end))
        return out

    def _in_holding_window(self, r: _Ref, transfers: list[_Ref]) -> tuple[bool, str | None]:
        """OWN-R4 for a holder of a proven chain: does the row lie inside one of the actor's holding windows?"""
        if r.spec == "cancel_service":
            return True, None                                       # untimed: holder membership (OWN-R8)
        if r.seq is None or r.table != transfers[0].table:
            return False, "OWNERSHIP_TRANSFER_ORDER_UNPROVEN"
        if any(h == r.actor and lo < r.seq < hi for h, lo, hi in self._windows(transfers)):
            return True, None
        return False, "OWNERSHIP_ACTION_OUTSIDE_HOLDING_WINDOW"

    def _explain_previous(self, r: _Ref, transfers: list[_Ref], chain_reason: str | None) -> tuple[bool, str | None]:
        """Is an owner-claim row of a non-current holder explained by the chain (OWN-R4)?"""
        senders = {t.actor for t in transfers}
        if transfers and chain_reason is None and r.actor in senders:
            return self._in_holding_window(r, transfers)
        if transfers and chain_reason is not None and (r.actor in senders or r.actor in {t.target for t in transfers}):
            return False, chain_reason
        if self.customer(r.actor) is None:
            return False, "OWNERSHIP_ACTOR_NOT_IN_USER_TABLE"
        return False, "OWNERSHIP_OWNER_MISMATCH_NO_TRANSFER_RECORD"

    def _ev(self, r: _Ref, role: str, rule: str, relation: str, reason: str | None) -> dict[str, Any]:
        return {"rule": rule, "source_table": r.table, "pk": r.pk, "role": role, "type_raw": r.type_raw,
                "telegram_user_id": rules.telegram_id(r.actor), "in_user_table": self.customer(r.actor) is not None,
                "relation": relation, "reason": reason}

    def _evaluate_invoice(self, inv: _Inv, refs: list[_Ref], shared: bool, claimants: bool, n_invs: int) -> _Result:
        owner = inv.owner
        owner_tg = rules.telegram_id(owner)
        owner_cust = self.customer(owner)
        reasons: set[str] = set()
        info: set[str] = set()
        evidence: list[dict[str, Any]] = [{
            "rule": "OWN-R1", "source_table": "invoice", "pk": inv.key, "role": "CURRENT_OWNER_FIELD",
            "type_raw": None, "telegram_user_id": owner_tg, "in_user_table": owner_cust is not None,
            "relation": "INVOICE", "reason": None}]
        if owner_tg is None:
            reasons.add("OWNERSHIP_OWNER_ID_INVALID")
        elif owner_cust is None:
            reasons.add("OWNERSHIP_OWNER_NOT_IN_USER_TABLE")
        if claimants:
            reasons.add("OWNERSHIP_MULTIPLE_CLAIMANTS")
        if shared:
            reasons.add("OWNERSHIP_USERNAME_SHARED_BY_INVOICES")
        transfers = [r for r in refs if r.role == "TRANSFER" and r.spec == "service_other"]
        transfers.sort(key=lambda t: (t.seq is None, t.seq or 0, t.table, t.rowno))
        chain_reason, links = self._chain(transfers, owner) if transfers else (None, [])
        if chain_reason:
            reasons.add(chain_reason)
        corroborated = False
        ref_reasons: dict[str, int] = {}
        for r in refs:
            key = (r.table, r.rowno)
            if r.role == "ADMIN_ACTOR":
                info.add("OWNERSHIP_ADMIN_ACTOR_NOT_OWNER")
                evidence.append(self._ev(r, "ADMIN_ACTOR", "OWN-R5", "NOT_AN_OWNER_FIELD", None))
                continue
            if r.actor == owner:
                rel, why = "MATCHES_INVOICE", None
                if r.role == "OWNER_CLAIM" and transfers and chain_reason is None:
                    # OWN-R4 also binds the current owner: acting outside every window it held (e.g. before
                    # receiving the service) is unexplained, possibly an unlogged account move (OWN-R7).
                    ok, why = self._in_holding_window(r, transfers)
                    rel = "MATCHES_INVOICE" if ok else "CONFLICT"
                corroborated = corroborated or (r.role == "OWNER_CLAIM" and why is None)
            elif r.role == "PAYER":
                rel, why = "PAYER_NOT_OWNER", None
                info.add("OWNERSHIP_PAYER_NOT_OWNER")
            elif r.role == "TRANSFER":
                rel, why = ("PREVIOUS_OWNER_VIA_TRANSFER", None) if chain_reason is None else ("CONFLICT", chain_reason)
            else:
                ok, why = self._explain_previous(r, transfers, chain_reason)
                rel = "PREVIOUS_OWNER_VIA_TRANSFER" if ok else "CONFLICT"
            if n_invs == 1:
                # services_ext asks the model only for rows whose actor differs from the invoice owner; an
                # owner's row outside its window is reported on this service's record and review row instead.
                self.ref_rel[key] = ("MATCHES_INVOICE", None) if r.actor == owner else (rel, why)
            if why:
                reasons.add(why)
                ref_reasons[why] = ref_reasons.get(why, 0) + 1
            role = {"TRANSFER": "TRANSFER", "PAYER": "PAYER"}.get(r.role,
                                                                  "CANCEL_REQUEST" if r.spec == "cancel_service"
                                                                  else "OWNER_CLAIM")
            rule = {"TRANSFER": "OWN-R2", "PAYER": "OWN-R6", "CANCEL_REQUEST": "OWN-R8"}.get(role, "OWN-R4")
            evidence.append(self._ev(r, role, rule, rel, why))
        if transfers and chain_reason is None:
            info.add("OWNERSHIP_TRANSFER_CHAIN_PROVEN")
        ordered = sorted(reasons, key=_REASON_ORDER.__getitem__)
        if "OWNERSHIP_OWNER_ID_INVALID" in reasons:
            decision = "INVALID_OWNER"
        elif "OWNERSHIP_OWNER_NOT_IN_USER_TABLE" in reasons:
            decision = "ORPHAN_OWNER"
        elif reasons:
            decision = "AMBIGUOUS_OWNER"
        elif transfers:
            decision = "CONFIRMED_TRANSFER"
        elif corroborated:
            decision = "CONFIRMED_CURRENT_OWNER"
        else:
            decision = "NO_CONFLICT"
        final = owner_tg if decision in FINAL_DECISIONS else None
        original: dict[str, Any] | None = None
        if decision == "CONFIRMED_TRANSFER":
            original = {"telegram_user_id": rules.telegram_id(transfers[0].actor), "basis": "FIRST_TRANSFER_SENDER",
                        "rule": "OWN-R3", "source_table": transfers[0].table, "pk": transfers[0].pk}
        elif decision in ("CONFIRMED_CURRENT_OWNER", "NO_CONFLICT"):
            original = {"telegram_user_id": owner_tg, "basis": "INVOICE_ID_USER_WITHOUT_TRANSFER_RECORD",
                        "rule": "OWN-R1", "source_table": "invoice", "pk": inv.key}
        current = None if owner_tg is None else {
            "telegram_user_id": owner_tg, "in_user_table": owner_cust is not None,
            "customer_decision": owner_cust if owner_cust is not None else "NOT_IN_BACKUP",
            "basis": "INVOICE_ID_USER", "rule": "OWN-R1"}
        return _Result(inv=inv, uname=inv.uname, username_raw=inv.username_raw, decision=decision,
                       reasons=ordered, info=sorted(info), evidence=evidence[:MAX_EVIDENCE],
                       evidence_total=len(evidence), transfers=links, original=original, current=current,
                       final_owner=final, ref_reasons=dict(sorted(ref_reasons.items())))

    def _unlinked(self, u: str, refs: list[_Ref]) -> _Result:
        evidence = []
        for r in refs:
            role = {"TRANSFER": "TRANSFER", "PAYER": "PAYER", "ADMIN_ACTOR": "ADMIN_ACTOR"}.get(
                r.role, "CANCEL_REQUEST" if r.spec == "cancel_service" else "OWNER_CLAIM")
            evidence.append(self._ev(r, role, "OWN-R9", "NO_INVOICE", None))
        return _Result(inv=None, uname=u, username_raw=refs[0].username_raw, decision="AMBIGUOUS_OWNER",
                       reasons=["OWNERSHIP_NO_INVOICE_FOR_USERNAME"], info=[], evidence=evidence[:MAX_EVIDENCE],
                       evidence_total=len(evidence), transfers=[], original=None, current=None, final_owner=None,
                       first_ref=refs[0])

    # ---- queries used by services_ext ------------------------------------------------------
    def explain(self, table: str, rowno: int) -> tuple[str, str | None]:
        """Relation of a service_other/cancel_service row whose actor differs from its (single) invoice owner."""
        return self.ref_rel.get((table, rowno), ("CONFLICT", "OWNERSHIP_OWNER_MISMATCH_NO_TRANSFER_RECORD"))

    def legacy_is_conflict(self, table: str, rowno: int) -> bool:
        return self.legacy_conflict.get((table, rowno), False)

    def conflict_counts(self) -> tuple[int, int]:
        """(rows flagged by the previous rule, rows still unexplained) over service_other + cancel_service."""
        before = sum(1 for v in self.legacy_conflict.values() if v)
        after = sum(1 for rel, _ in self.ref_rel.values() if rel == "CONFLICT")
        return before, after


def model_for(ctx: ModuleContext) -> OwnershipModel:
    """One model per conversion run (the ModuleContext is shared by every module of a run)."""
    m = getattr(ctx, "_ownership_model", None)
    if m is None or m.ctx is not ctx:
        m = OwnershipModel(ctx)
        ctx._ownership_model = m  # type: ignore[attr-defined]
    return m


# ---- run ---------------------------------------------------------------------------------------

def _adoptable(res: _Result) -> bool:
    return res.inv is not None and res.inv.live and res.inv.core != "TEST_INVOICE_SKIPPED"


def run(ctx: ModuleContext) -> None:
    model = model_for(ctx)
    ctx.register_messages(reviews=REASONS)
    by_decision = {d: 0 for d in DECISIONS}
    by_reason: dict[str, int] = {}
    by_info: dict[str, int] = {}
    stats = {"total": 0, "live_candidates": 0, "history_only": 0, "needs_manual_review": 0,
             "covered_by_core_review": 0, "confirmed_transfers": 0, "final_owner_set": 0,
             "final_owner_equals_invoice_owner": 0, "evidence_truncated_records": 0}
    sink = ctx.open(RECORDS) if model.results else None
    used: set[str] = set()
    for res in model.results:
        ctx.tick()
        inv = res.inv
        if inv is not None:
            base = f"legacy:service-ownership:{inv.key}" if inv.key is not None and rules.js_trim(inv.key) \
                else f"legacy:service-ownership:row:{inv.rowno}"
        else:
            base = f"legacy:service-ownership:unlinked:{ctx.secret_ref(res.uname, 'svc-username')}"
        key, n = base, 1
        while key in used:
            n += 1
            key = f"{base}#{n}"
        used.add(key)
        adoptable = _adoptable(res)
        needs = adoptable and res.decision not in FINAL_DECISIONS
        reason = res.reasons[0] if res.reasons else None
        covered = None
        if needs and inv is not None:
            core = inv.core or ""
            covered = next((core for k, v in _CORE_COVERS.items() if (k == res.decision or k == reason)
                            and core in v), None)
        checksum = rules.row_checksum(f"m2n:{SCHEMA}", [
            key, res.decision, *res.reasons, res.final_owner,
            *(f"{e['source_table']}:{e['pk']}:{e['relation']}" for e in res.evidence)])
        if inv is not None:
            prov = ctx.provenance("invoice", inv.key, checksum)
        else:
            prov = ctx.provenance(res.first_ref.table if res.first_ref else "service_other",
                                  res.first_ref.pk if res.first_ref else None, checksum)
        sink.write({
            "record_type": "legacy_service_ownership",
            "schema": SCHEMA,
            "idempotency_key": key,
            "invoice_key": inv.key if inv else None,
            "invoice_status_raw": inv.status if inv else None,
            "live_service_candidate": bool(inv and inv.live),
            "core_service_decision": inv.core if inv else None,
            "panel_account_username": res.username_raw,
            "original_owner": res.original,
            "current_owner_candidate": res.current,
            "final_owner_telegram_user_id": res.final_owner,
            "ownership_decision": res.decision,
            "ownership_confidence": ("PROVEN" if res.decision in FINAL_DECISIONS else
                                     "PARTIAL" if res.decision == "AMBIGUOUS_OWNER" and res.current is not None
                                     and res.current["in_user_table"] else "NONE"),
            "reason_codes": res.reasons,
            "info_codes": res.info,
            "manual_review_reason": reason if res.decision not in FINAL_DECISIONS else None,
            "needs_manual_review": needs,
            "review_scope": "ADOPTION" if adoptable else "HISTORY_ONLY",
            "already_in_review_queue_as": covered,
            "ownership_evidence": res.evidence,
            "ownership_evidence_total": res.evidence_total,
            "transfer_evidence": res.transfers,
            "confirmable_only_by": "NEXA_ADOPTION_LIVE_PANEL_CHECK",
            "provision": False,
            "applies_to_live_state": False,
            "affects_wallet": False,
            "creates_payment": False,
            "provenance": prov,
        })
        stats["total"] += 1
        stats["live_candidates" if inv is not None and inv.live else "history_only"] += 1
        by_decision[res.decision] += 1
        for r in res.reasons:
            by_reason[r] = by_reason.get(r, 0) + 1
        for c in res.info:
            by_info[c] = by_info.get(c, 0) + 1
        if res.decision == "CONFIRMED_TRANSFER":
            stats["confirmed_transfers"] += 1
        if res.final_owner is not None:
            stats["final_owner_set"] += 1
            if inv is not None and res.final_owner == inv.owner:
                stats["final_owner_equals_invoice_owner"] += 1
        if res.evidence_total > len(res.evidence):
            stats["evidence_truncated_records"] += 1
        if needs:
            stats["needs_manual_review"] += 1
            if covered:
                stats["covered_by_core_review"] += 1
            else:
                ctx.review(kind="service_ownership", code=reason or "OWNERSHIP_OWNER_MISMATCH_NO_TRANSFER_RECORD",
                           entity="service_ownership", table="invoice", pk=inv.key if inv else None,
                           severity="blocker",
                           data={"ownership_decision": res.decision, "reason_codes": res.reasons,
                                 "row_reasons": res.ref_reasons, "core_service_decision": inv.core if inv else None})
        ctx.count("service_ownership", "source")
        ctx.count("service_ownership", "converted")
    before, after = model.conflict_counts()
    transitions = {"CONFLICT_TO_EXPLAINED": 0, "EXPLAINED_TO_CONFLICT": 0, "UNCHANGED_CONFLICT": 0}
    row_reasons: dict[str, int] = {}
    row_relations: dict[str, int] = {}
    for k, (rel, why) in sorted(model.ref_rel.items()):
        row_relations[rel] = row_relations.get(rel, 0) + 1
        if why:
            row_reasons[why] = row_reasons.get(why, 0) + 1
        old = model.legacy_conflict.get(k)
        if old is None:
            continue
        if old and rel != "CONFLICT":
            transitions["CONFLICT_TO_EXPLAINED"] += 1
        elif not old and rel == "CONFLICT":
            transitions["EXPLAINED_TO_CONFLICT"] += 1
        elif old:
            transitions["UNCHANGED_CONFLICT"] += 1
    ctx.reconciliation("service_ownership", {
        **stats,
        "by_decision": by_decision,
        "by_reason": dict(sorted(by_reason.items(), key=lambda kv: _REASON_ORDER.get(kv[0], 99))),
        "by_info_code": dict(sorted(by_info.items())),
        "conflict_warnings_before": before,
        "conflict_warnings_after": after,
        "conflict_rows_by_reason": dict(sorted(row_reasons.items())),
        "conflict_transitions": transitions,
        "row_relations": dict(sorted(row_relations.items())),
        "rules": RULES,
        "panel_username_is_owner_proof": False,
        "applies_to_live_state": False,
        "note": "Only invoice.id_user can become a final owner, and only by a deterministic rule; everything "
                "else stays AMBIGUOUS for manual review. Live panel state is confirmed by NEXA adoption only.",
    })
    ctx.mapping({"entity": "service_ownership", "source_table": "invoice+service_other+cancel_service",
                 "source_columns": ["invoice.id_user", "invoice.username", "service_other.id_user",
                                    "service_other.username", "service_other.type", "service_other.value",
                                    "cancel_service.id_user", "cancel_service.username"],
                 "target": "records/service_ownership.jsonl (decision support; never applied)", "status": "partial",
                 "note": "Rules OWN-R1..R9, docs/OWNERSHIP.md."})


register_module(Module(key="ownership", title_fa="مالکیت سرویس‌ها و انتقال‌ها", run=run, order=350))


# ---- synthetic scenarios (fabricated; appended to the services_ext tables) -----------------------

SCENARIO_BASE_ID = 900_000


def synth_tables(out, state: SynthState) -> None:
    """Ownership scenarios on existing synthetic invoices. Records the invoice keys in ``state.exp``."""
    spec = state.spec
    n = len(state.invoice_keys)
    if not spec.anomalies or n < 12 or len(set(state.user_ids)) < 8:
        return
    users = state.user_ids
    so: list[str] = []
    cx: list[str] = []
    sid = SCENARIO_BASE_ID

    def op(actor: str, uname: str, typ: str, value: str = "{}", price: str = "0", status: str | None = None) -> None:
        nonlocal sid
        sid += 1
        so.append(f"({sid},{sql_str(actor)},{sql_str(uname)},{sql_str(value)},"
                  f"{sql_str(f'2025/06/{1 + sid % 27:02d} 10:{sid % 60:02d}:00')},{sql_str(price)},{sql_str(typ)},"
                  f"{sql_str(status)},NULL)")

    def cancel(actor: str, uname: str, st: str = "reject") -> None:
        cx.append(f"({SCENARIO_BASE_ID + len(cx) + 1},{sql_str(actor)},{sql_str(uname)},'0',{sql_str(st)})")

    def others(owner: str, k: int) -> list[str]:
        pool = [u for u in users if u not in {owner, str(7_000_000_001), str(7_000_000_002)}]
        return [pool[(k * 7 + j) % len(pool)] for j in range(3)]

    sc: dict[str, str] = {}
    # indices 0 and 1 are used by services_ext; scenarios use 2..11
    i = 2   # no transfer, corroborated by the owner; a third party's 'not in list' renewal and an admin renewal
    o = state.invoice_owners[i]
    a = others(o, 0)
    op(o, state.invoice_usernames[i], "extend_user", price="10000", status="paid")
    op(a[0], state.invoice_usernames[i], "extends_not_user", price="10000")
    op(a[1], state.invoice_usernames[i], "extend_user_by_admin")
    sc["no_transfer"] = state.invoice_keys[i]
    i = 3   # A→B
    o = state.invoice_owners[i]
    a = others(o, 1)
    op(a[0], state.invoice_usernames[i], "extra_user", price="20000")
    op(a[0], state.invoice_usernames[i], TRANSFER_TYPE, value=o)
    cancel(a[0], state.invoice_usernames[i])      # same service in another table, by the previous owner
    sc["a_to_b"] = state.invoice_keys[i]
    i = 4   # O→A→B→O with operations by each holder inside its window. The services_ext generator may
    #         already have written rows by the invoice owner O with lower ids; a round trip keeps them inside
    #         O's first holding window (OWN-R4 also binds the current owner).
    o = state.invoice_owners[i]
    a = others(o, 2)
    op(o, state.invoice_usernames[i], TRANSFER_TYPE, value=a[0])
    op(a[0], state.invoice_usernames[i], "extend_user", price="30000", status="paid")
    op(a[0], state.invoice_usernames[i], "extra_user", price="5000")
    op(a[0], state.invoice_usernames[i], TRANSFER_TYPE, value=a[1])
    op(a[1], state.invoice_usernames[i], "extra_time_user", price="5000")
    op(a[1], state.invoice_usernames[i], TRANSFER_TYPE, value=o)
    op(o, state.invoice_usernames[i], "extend_user", price="30000", status="paid")
    sc["round_trip_chain"] = state.invoice_keys[i]
    i = 5   # old invoice keeps the sender, a transfer names a new owner who then operates
    o = state.invoice_owners[i]
    a = others(o, 3)
    op(o, state.invoice_usernames[i], TRANSFER_TYPE, value=a[0])
    op(a[0], state.invoice_usernames[i], "extra_user", price="5000")
    sc["old_invoice_new_owner"] = state.invoice_keys[i]
    i = 6   # username changed on the panel: rows under a name no invoice has
    o = state.invoice_owners[i]
    op(o, "renamed_syn_old6", "extend_user", price="10000", status="paid")
    sc["username_changed_invoice"] = state.invoice_keys[i]
    i = 7   # previous owner keeps operating after transferring the service away
    o = state.invoice_owners[i]
    a = others(o, 4)
    op(a[0], state.invoice_usernames[i], TRANSFER_TYPE, value=o)
    op(a[0], state.invoice_usernames[i], "extra_user", price="5000")
    sc["action_outside_window"] = state.invoice_keys[i]
    i = 8   # a requester that is no longer in the user table (cf. unlogged account move, OWN-R7)
    cancel("6999999555", state.invoice_usernames[i], "waiting")
    sc["actor_not_in_user_table"] = state.invoice_keys[i]
    i = 9   # a stranger, no transfer
    o = state.invoice_owners[i]
    a = others(o, 5)
    cancel(a[0], state.invoice_usernames[i], "reject")
    sc["stranger_no_transfer"] = state.invoice_keys[i]
    i = 10  # broken chain: A→B then C→owner
    o = state.invoice_owners[i]
    a = others(o, 6)
    op(a[0], state.invoice_usernames[i], TRANSFER_TYPE, value=a[1])
    op(a[2], state.invoice_usernames[i], TRANSFER_TYPE, value=o)
    sc["chain_broken"] = state.invoice_keys[i]
    # owner deleted: core 'orphan1' (owner 5000000001 not in user); two claimants: core 'shared'
    op("5000000001", "orphan1", "extend_user", price="1000", status="paid")
    op(str(7_000_000_001), "shared", "extra_user", price="1000")
    sc["owner_deleted"] = "9999999abcd"
    sc["two_claimants"] = "9999998abcd"
    sc["username_changed"] = "renamed_syn_old6"
    if so:
        state.write_rows(out, "service_other", so)
    if cx:
        state.write_rows(out, "cancel_service", cx)
    state.exp["ownership_scenarios"] = sc


register_synth("ownership", synth_tables, order=310)

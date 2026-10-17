"""Column carry (converter 0.8.0, content contract 1.5.0): every non-secret source column reaches NEXA.

Owner goal: *migrate 100 % of the Mirza database; nothing silently dropped.* Up to 0.7.0 a column the
owning module did not read was either ``unmapped`` (a column-listed spec, e.g. the deployed fork's
newer ``user`` columns) or not listed at all (the core tables, and every column of an EXCLUDED
table). This module closes that gap without touching any existing record:

* **Supplement columns** of tables another module converts (``user``, ``invoice``, ``product``,
  ``service_other``, ``category``, ``reagent_report``, ``affiliates``, ``setting``,
  ``marzban_panel`` … and any column-listed table that grows a column) are staged in a SEPARATE
  staging table (``TableStats.carry_sql_name``), so the owning module's ``stored_columns``, its
  records and its checksums are byte-identical to 0.7.0. The staged values (never a secret; a
  minimised column's derivative only) ARE part of the source fingerprint, v3 (hence ``import_id``).
  They are written as one extra history record per source row::

      records/archive/<table>_carried.jsonl          legacy_archive_row        (non-configuration tables)
      records/configuration/<table>_carried.jsonl    legacy_configuration_row  (CONFIGURATION tables)

  NEXA's history ingest already accepts both directories (``record-map.ts`` HISTORY_DIRECTORIES).
* **Formerly EXCLUDED tables** ``admin``, ``botsaz``, ``logs_api``, ``topicid``,
  ``global_hidden_gateways`` and ``ai_business_chat`` are ARCHIVE tables owned here: their
  non-secret columns are carried as ``records/archive/<table>.jsonl``.

Per column the catalogue decides one disposition (``catalogue.DISPOSITIONS``): *stored* (the owning
module reads it), *carried* (verbatim, value-redacted), *minimised* (only a parse-time derivative,
``sensitivity.MINIMISERS``), *secret_dropped* (never staged; named in
``reports/manual-reconfiguration.json``) or *excluded* (a column of an EXCLUDED table). An undeclared
column (a newer fork) is carried unless its NAME looks like a credential. ``reports/coverage.json``
lists the disposition of every column and ``unaccounted_columns`` (always empty; a non-empty list
blocks the package with ``COLUMN_UNACCOUNTED``).

Links: a record about a Telegram user carries ``customer`` (``payments.customer_relation``:
``telegram_user_id``, ``customer_decision``); a record about an invoice carries
``invoice.invoice_key``. NEXA links a history record to a customer only for the record types its
``USER_ID_PATHS`` names; ``archive_row`` is not one of them yet (see ``docs/PACKAGE_CONTRACT.md``
§"Column carry" for the one-line importer change that makes Customer 360 show these records).

Evidence: columns named by the operator's coverage report of the real backup (2026-10, column NAMES
only, no values) are cited as ``OPREP``; their meanings are inferred from the name and marked so.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any

from ..catalogue import (
    ARCHIVE,
    CARRIED,
    CATALOGUE,
    CONFIGURATION,
    EXCLUDED,
    EXCLUDED_COLUMN,
    FINANCIAL,
    MINIMISED,
    PERSONAL,
    PUBLIC,
    SECRET,
    SECRET_DROPPED,
    STORED,
    ColumnSpec,
    TableSpec,
)
from ..sensitivity import column_name_is_secret
from .base import Module, ModuleContext, register_module
from .generic import idempotency_key, pk_is_sensitive

MODULE_KEY = "carry"
SCHEMA_SUPPLEMENT = "m2n.carried_columns.v1"

PRO = "mirza_pro@b645db74"
BMP = "botmirzapanel@92c0ed06"
NEXA_R = "nexa-bot@9e604def docs/research"
EVJ = "docs/evidence/mirza_tables.json"
OPREP = "operator coverage report of the real backup, 2026-10 (column names only; meaning inferred from the name)"
_INFER = "meaning inferred from the name (UNPROVEN); "
_SEC = "never staged; listed in reports/manual-reconfiguration.json"


def _c(name: str, sens: str = PUBLIC, note: str = "", minimise: str | None = None) -> ColumnSpec:
    return ColumnSpec(name, sens, note, minimise)


# ----------------------------------------------------------------------------------------------
# Supplement columns of tables converted by other modules
# ----------------------------------------------------------------------------------------------

USER_CARRY = (
    _c("roll_Status", PUBLIC, "rules accepted flag (BMP table.php user; MP db/tables/user.php)"),
    _c("Processing_value", PERSONAL, "bot conversation scratch value (MP user.php); free text, value-redacted"),
    _c("Processing_value_one", PERSONAL, "bot conversation scratch value; value-redacted"),
    _c("Processing_value_tow", PERSONAL, "bot conversation scratch value; value-redacted"),
    _c("Processing_value_four", PERSONAL, "bot conversation scratch value; value-redacted"),
    _c("step", PUBLIC, "current bot menu step (MP user.php)"),
    _c("description_blocking", PERSONAL, "admin's block reason (MP user.php)"),
    _c("pagenumber", PUBLIC, "pagination state"),
    _c("message_count", PUBLIC, "anti-flood message counter"),
    _c("last_message_time", PUBLIC, "anti-flood timestamp"),
    _c("affiliatescount", PUBLIC, "number of referred users"),
    _c("affiliates", PERSONAL, "referrer Telegram id (NEXA_EVIDENCE: user.affiliates)"),
    _c("namecustom", PERSONAL, "customer-chosen service name prefix"),
    _c("number_username", PUBLIC, "counter for generated usernames"),
    _c("register", PERSONAL, "registration time"),
    _c("verify", PUBLIC, "verification flag"),
    _c("codeInvitation", SECRET, "personal invitation code; redeemable (evidence: secret). " + _SEC),
    _c("joinchannel", PUBLIC, "forced-join state"),
    _c("checkstatus", PUBLIC, "status-check flag"),
    _c("bottype", SECRET, "reseller sub-bot TOKEN (evidence: secret; MP vpnbot/Default/index.php:66). " + _SEC),
    _c("score", PUBLIC, "lottery/score points"),
    _c("limitchangeloc", PUBLIC, "location-change counter"),
    _c("status_cron", PUBLIC, "cron notification flag"),
    _c("token", SECRET, "mini-app/web login token (evidence: secret). " + _SEC),
    _c("date_message", PUBLIC, _INFER + "time of last broadcast/notice"),
    _c("low_balance_alert_sent", PUBLIC, "low-balance alert flag (MP user.php)"),
    _c("hidden_gateways", PUBLIC, _INFER + "per-user list of hidden payment gateway CODES (no gateway key)"),
    _c("shown_gateways", PUBLIC, _INFER + "per-user list of shown payment gateway CODES (no gateway key)"),
    _c("first_name", PERSONAL, "Telegram first name"),
    _c("commission_percentage", FINANCIAL, _INFER + "per-agent commission percentage"),
    _c("min_purchase_warned_month", PUBLIC, _INFER + "agent minimum-purchase warning month"),
    _c("min_purchase_evaluated_month", PUBLIC, _INFER + "agent minimum-purchase evaluation month"),
    _c("agent_tier_since", PUBLIC, _INFER + "time the agent tier was granted"),
    _c("affiliate_alert_time", PUBLIC, _INFER + "time of the last referral alert"),
    _c("ad_source", PUBLIC, _INFER + "advertising campaign attribution code (cf. ad_campaign)"),
    _c("card_message_id", PUBLIC, _INFER + "Telegram message id of a card-payment message; NOT a card number"),
    # in the public sources (evidence JSON) but not reported for the deployed fork:
    _c("ref_code", SECRET, "referral code (evidence: secret). " + _SEC),
    _c("cardpayment", PUBLIC, "card-payment permission flag (evidence JSON)"),
    _c("lang", PUBLIC, "language code (evidence JSON)"),
    _c("hide_mini_app_instruction", PUBLIC, "UI flag (evidence JSON)"),
)

INVOICE_CARRY = (
    _c("note", PERSONAL, "customer-typed note (NEXA invoice-archive read set reads it); value-redacted"),
    _c("inboundid", PUBLIC, _INFER + "panel inbound identifier chosen for this service; an id, not a credential"),
    _c("refral", PERSONAL, "referrer of the purchase (NEXA invoice-archive read set)"),
    _c("time_cron", PUBLIC, "cron bookkeeping time"),
    _c("notifctions", PUBLIC, "notification state JSON (MP invoice)"),
    _c("rate_invoice", PUBLIC, _INFER + "customer rating of the purchase"),
    _c("limit_user", PUBLIC, _INFER + "per-service device/user limit"),
    _c("id_payment", PUBLIC, _INFER + "Payment_report id that paid this invoice (a reference, not money)"),
    _c("payable_amount", FINANCIAL, _INFER + "amount payable; unit as declared for the whole backup; never applied"),
)

PRODUCT_CARRY = (
    _c("limit_user", PUBLIC, _INFER + "device/user limit of the plan"),
    _c("display_order", PUBLIC, _INFER + "sort order"),
    _c("style", PUBLIC, _INFER + "button style"),
    _c("usage_type", PUBLIC, _INFER + "plan usage type"),
    _c("emoji_id", PUBLIC, _INFER + "custom emoji id"),
    _c("time_unit", PUBLIC, "unit of Service_time (cf. invoice.time_unit, NEXA read set)"),
)

SERVICE_OTHER_CARRY = (
    _c("id_payment", PUBLIC, _INFER + "Payment_report id that paid the operation"),
    _c("payable_amount", FINANCIAL, _INFER + "amount payable; never applied"),
    _c("bottype", SECRET, "reseller sub-bot TOKEN (as user/invoice.bottype). " + _SEC),
    _c("id_invoice", PUBLIC, "invoice the operation belongs to (links the record to it)"),
    _c("action", PUBLIC, _INFER + "operation action code"),
    _c("source", PUBLIC, _INFER + "where the operation came from (bot, mini app, api)"),
    _c("params", PERSONAL, _INFER + "operation parameters (JSON); value-redacted"),
    _c("panel_response", SECRET, "raw panel API response; like service_other.output it may carry subscription "
                                 "links (MP index.php:2134). " + _SEC),
    _c("created_at", PUBLIC, "creation time"),
)

CATEGORY_CARRY = (_c("style", PUBLIC, _INFER + "button style"),)

REAGENT_CARRY = (
    _c("gift_milestone", PUBLIC, _INFER + "referral gift milestone reached"),
    _c("gift_invoice", PUBLIC, _INFER + "invoice id of a referral gift"),
)

AFFILIATES_CARRY: tuple[ColumnSpec, ...] = ()  # fork columns are carried by the name rule

SETTING_CARRY = (
    _c("jwt_secret", SECRET, "signing key of the mini app / web API; NEXA generates its own. " + _SEC),
    _c("ai_api_key", SECRET, "AI provider API key. " + _SEC),
)

_PANEL_NOTE = "marzban_panel display/feature switch (deployed fork); not a credential"
MARZBAN_PANEL_CARRY = (
    _c("hosts", PUBLIC, _INFER + "host list shown in configs; value-redacted (credential URLs removed)"),
    _c("status_happ", PUBLIC, _PANEL_NOTE),
    _c("limit_user", PUBLIC, _PANEL_NOTE),
    _c("limit_in_panel", PUBLIC, _PANEL_NOTE),
    _c("price_pre_user", FINANCIAL, _INFER + "price per extra user; reference only"),
    _c("buy_additional_user", PUBLIC, _PANEL_NOTE),
    _c("display_order", PUBLIC, _PANEL_NOTE),
    _c("proxy_sockss", SECRET, _INFER + "outbound SOCKS proxy the bot uses to reach the panel; a proxy "
                               "definition can embed user:password, so it is treated like the panel "
                               "credentials and re-entered with them. " + _SEC),
    _c("style", PUBLIC, _PANEL_NOTE),
    _c("status_keyboard_config", PUBLIC, _PANEL_NOTE),
    _c("content_after_buy", PUBLIC, "admin text after purchase; value-redacted"),
    _c("MethodUsername_key", PUBLIC, _INFER + "parameter of the username method (prefix); value-redacted"),
    _c("btn_config", PUBLIC, _PANEL_NOTE),
    _c("btn_sublink", PUBLIC, _INFER + "show-subscription-link button switch; a link value would be "
                              "removed by value redaction"),
    _c("emoji_id", PUBLIC, _PANEL_NOTE),
    _c("auth_type", PUBLIC, _INFER + "authentication METHOD name of the panel (not the credential)"),
    _c("auto_disabled", PUBLIC, _PANEL_NOTE),
    _c("text_afterpay", PUBLIC, "admin text after payment; value-redacted"),
    _c("text_aftertest", PUBLIC, "admin text after a trial; value-redacted"),
    _c("hide_agent", PUBLIC, _PANEL_NOTE),
    _c("status_extra_volume", PUBLIC, _PANEL_NOTE),
    _c("status_extra_time", PUBLIC, _PANEL_NOTE),
)

#: table → (carry columns, customer columns, invoice column)
SUPPLEMENTS: dict[str, tuple[tuple[ColumnSpec, ...], tuple[str, ...], str | None]] = {
    "user": (USER_CARRY, ("id",), None),
    "invoice": (INVOICE_CARRY, ("id_user",), "id_invoice"),
    "product": (PRODUCT_CARRY, (), None),
    "service_other": (SERVICE_OTHER_CARRY, ("id_user",), "id_invoice"),
    "category": (CATEGORY_CARRY, (), None),
    "reagent_report": (REAGENT_CARRY, ("user_id",), None),
    "affiliates": (AFFILIATES_CARRY, (), None),
    "setting": (SETTING_CARRY, (), None),
    "marzban_panel": (MARZBAN_PANEL_CARRY, (), None),
}

# Tables whose rows are mostly about one Telegram user: their own undeclared fork columns link too.
_DEFAULT_CUSTOMER_COLUMNS = ("id_user", "user_id")


# ----------------------------------------------------------------------------------------------
# Formerly EXCLUDED tables, now ARCHIVE and owned here
# ----------------------------------------------------------------------------------------------

def _spec(name: str, purpose: str, purpose_fa: str, reason: str, evidence: tuple[str, ...],
          carry: tuple[ColumnSpec, ...], *, primary_key: tuple[str, ...] = (), customer: tuple[str, ...] = (),
          sensitivity: str = PERSONAL, open_questions: tuple[str, ...] = ()) -> TableSpec:
    return TableSpec(name=name, klass=ARCHIVE, module=MODULE_KEY, purpose=purpose, purpose_fa=purpose_fa,
                     reason=reason, nexa_target="records/archive/<table>.jsonl (legacy_archive_row history)",
                     evidence=evidence, primary_key=primary_key, columns=(), carry=carry,
                     customer_columns=customer, sensitivity=sensitivity, open_questions=open_questions)


OWNED_SPECS: tuple[TableSpec, ...] = (
    _spec("admin", "Bot/web-panel administrators and their role", "مدیران ربات و نقش آن‌ها",
          "Admin identity and role are history the owner asked to keep; the web-panel login name and password "
          "hash are credentials (dropped, re-created in NEXA's own RBAC + 2FA). Until 0.7.0 EXCLUDED.",
          (f"{PRO} db/tables/admin.php:3-23", f"{PRO} db/migrations/012_admin_panel_credentials.php:7-16",
           f"{BMP} table.php:176-190",
           f"{NEXA_R}/mirzabotadminmanagementinvestigationcomplete/admin-management-knowledge/MASTER.md",
           f"{EVJ}#admin"),
          (_c("id_admin", PERSONAL, "admin Telegram id (links the record to the customer with that id)"),
           _c("rule", PUBLIC, "role name"),
           _c("username", SECRET, "web-panel login name; half of a credential pair. " + _SEC),
           _c("password", SECRET, "bcrypt hash or legacy plaintext (migration 012). " + _SEC)),
          primary_key=("id_admin",), customer=("id_admin",),
          open_questions=("Which Mirza admins become NEXA admins, with which role? (decided by hand)",)),
    _spec("botsaz", "Reseller sub-bots built by agents (one bot per agent)", "ربات‌های نمایندگان",
          "Which agent had which sub-bot, its public username and admins are history; the bot token and webhook "
          "secret are credentials (dropped) and the per-bot setting JSON holds payout card text (cart_info), so "
          "only its key NAMES are kept. NEXA has no reseller sub-bots; agents re-register. Until 0.7.0 EXCLUDED.",
          (f"{PRO} db/tables/botsaz.php:3-19", f"{PRO} api/users.php:600-668", f"{PRO} function.php:1815",
           f"{EVJ}#botsaz", OPREP + " (product_info)"),
          (_c("id"), _c("id_user", PERSONAL, "agent Telegram id"), _c("admin_ids", PERSONAL, "JSON list of ids"),
           _c("username", PUBLIC, "public bot username"),
           _c("setting", SECRET, "JSON incl. cart_info (payout card text); only its key names are staged",
              minimise="json_keys"),
           _c("hide_panel"), _c("time"),
           _c("product_info", PUBLIC, _INFER + "per-bot product/price overrides (JSON); value-redacted"),
           _c("bot_token", SECRET, "Telegram bot token. " + _SEC),
           _c("webhook_secret", SECRET, "Telegram webhook secret. " + _SEC)),
          primary_key=("id",), customer=("id_user",),
          open_questions=("Does NEXA support reseller sub-bots? Agents must re-register their bots.",)),
    _spec("logs_api", "Mini-app / API request log", "لاگ درخواست‌های API",
          "Which API action ran when is kept. Request headers (Authorization) are dropped; the request body may "
          "hold tokens/passwords, so only its JSON key names are kept; the client IP is personal data reduced to "
          "its family (ipv4/ipv6). Mirza itself purges this table (optimize_order.php:15). Until 0.7.0 EXCLUDED.",
          (f"{PRO} db/tables/logs_api.php:3-12", f"{PRO} api/utils.php:173", f"{PRO} cronbot/optimize_order.php:15",
           f"{EVJ}#logs_api"),
          (_c("id"), _c("header", SECRET, "raw request headers incl. Authorization. " + _SEC),
           _c("data", SECRET, "raw request body; only its top-level key names are staged", minimise="json_keys"),
           _c("ip", PERSONAL, "client IP; minimised to the address family", minimise="ip_family"),
           _c("time"), _c("actions")),
          primary_key=("id",)),
    _spec("topicid", "Report type → forum topic id in the admin report group", "شناسه تاپیک گزارش‌ها در گروه مدیریت",
          "Not secret; bound to the old Telegram report group (NEXA ops-log topics are configured by hand) but "
          "kept as reference. Until 0.7.0 EXCLUDED.",
          (f"{PRO} db/tables/topicid.php:3-27", f"{PRO} function.php:298-309",
           f"{NEXA_R}/mirzabottelegramloggroupinvestigationcomplete", f"{EVJ}#topicid"),
          (_c("report", PUBLIC, "report type"), _c("idreport", PUBLIC, "forum topic id")),
          primary_key=("report",), sensitivity=PUBLIC),
    _spec("global_hidden_gateways", "Payment gateways hidden globally (deployed fork only; no public DDL)",
          "درگاه‌های پرداخت پنهان‌شده",
          "Gateway visibility rows (gateway CODE, agent tier, time); no gateway key is declared. Any further "
          "fork column is carried unless its name looks like a credential. Until 0.7.0 EXCLUDED (presumed "
          "secret by name); the operator reported its columns id, code, agent, created_at.",
          (f"{EVJ}#global_hidden_gateways", OPREP),
          (_c("id"), _c("code", PUBLIC, _INFER + "gateway code (e.g. zarinpal), not a credential"),
           _c("agent", PUBLIC, _INFER + "agent tier the rule applies to"), _c("created_at")),
          primary_key=("id",), sensitivity=PUBLIC,
          open_questions=("Confirm that code is a gateway identifier and never a key.",)),
    _spec("api_key", "Mini-app / API access keys (deployed fork, seen 2026-10-17; no public DDL)",
          "کلیدهای دسترسی API",
          "Which API keys existed, their names, permissions and status are kept as reference; the key material "
          "(token_hash, token_prefix) is a credential and is dropped — keys are re-issued in NEXA by hand.",
          (OPREP,),
          (_c("id"), _c("name", PUBLIC, "operator label"),
           _c("token_hash", SECRET, "hash of the API token; a credential verifier. " + _SEC),
           _c("token_prefix", SECRET, "first characters of the API token. " + _SEC),
           _c("permissions", PUBLIC, _INFER + "permission list"), _c("status"), _c("created_at"),
           _c("last_used_at")),
          primary_key=("id",), sensitivity=PUBLIC),
    _spec("custom_button", "Operator-defined bot menu buttons (deployed fork, seen 2026-10-17; no public DDL)",
          "دکمه‌های سفارشی منوی ربات",
          "Button title, type and content (text, Telegram file id, URL) as configuration reference; NEXA's own "
          "button builder is configured by hand. Telegram file ids belong to the old bot.",
          (OPREP,),
          (_c("id"), _c("title", PUBLIC, "button label"), _c("type"), _c("content_type"),
           _c("content_text", PUBLIC, "operator text; value-level redaction applies"),
           _c("file_id", PUBLIC, "Telegram file id of the old bot"), _c("url", PUBLIC, "operator link"),
           _c("status"), _c("created_at")),
          primary_key=("id",), sensitivity=PUBLIC),
    _spec("ai_business_chat", "AI assistant chat log (deployed fork only; no public DDL)",
          "گفتگوهای دستیار هوش مصنوعی",
          "Owner decision 2026-10 (\"nothing silently dropped\"): the business-chat history is the owner's own "
          "customer-conversation record, so it is carried as restricted history (NEXA shows it only with the "
          "legacy PII permission, audited). Columns come from the runtime DDL; credential-like names (api_key, "
          "token, …) are dropped and every value passes value-level redaction (keys, tokens, links, cards). "
          "Residual risk: free text redaction is best-effort (docs/DATA_PROTECTION.md). Until 0.7.0 EXCLUDED.",
          (f"{EVJ}#ai_business_chat", OPREP),
          (), customer=("user_id", "id_user", "chat_id", "telegram_id"), sensitivity=PERSONAL,
          open_questions=("Supply the CREATE TABLE header (no values) to declare its columns.",)),
)

#: Owned tables whose classification rests on names only: non-empty → CARRY_UNVERIFIED_TABLE_ARCHIVED.
UNVERIFIED_TABLES = frozenset({"global_hidden_gateways", "ai_business_chat", "api_key", "custom_button"})

for _s in OWNED_SPECS:
    CATALOGUE.register(_s)

_applied = False


def apply_supplements() -> None:
    """Attach the supplement declarations to the specs other modules registered (idempotent)."""
    global _applied
    if _applied:
        return
    for name, (carry, customer, invoice) in SUPPLEMENTS.items():
        spec = CATALOGUE.specs.get(name)
        if spec is None:
            raise RuntimeError(f"carry: no catalogue spec for {name}")
        clash = [c.name for c in carry if spec.main_column(c.name) is not None]
        if clash:
            raise RuntimeError(f"carry: {name} declares {clash} twice")
        CATALOGUE.specs[name] = dataclasses.replace(spec, carry=carry, customer_columns=customer,
                                                    invoice_column=invoice)
    for name, spec in list(CATALOGUE.specs.items()):  # every other column-listed table links by name
        if spec.module != MODULE_KEY and not spec.customer_columns and spec.klass != EXCLUDED:
            CATALOGUE.specs[name] = dataclasses.replace(spec, customer_columns=_DEFAULT_CUSTOMER_COLUMNS)
    _applied = True


# ----------------------------------------------------------------------------------------------
# Coverage helpers (called from convert._finish_coverage)
# ----------------------------------------------------------------------------------------------

def column_dispositions(stats: Any, spec: TableSpec | None) -> tuple[dict[str, str], list[str]]:
    """``{column: disposition}`` for every DDL column, and the columns with none (must be empty)."""
    out: dict[str, str] = {}
    missing: list[str] = []
    stored, carried = set(stats.stored_columns), set(stats.carried_columns)
    minimised = set(stats.minimised_columns)
    dropped = set(stats.dropped_columns) | set(stats.carry_secret_columns)
    for c in stats.columns:
        if spec is None:
            missing.append(c)
        elif c in stored:
            out[c] = STORED
        elif c in carried:
            out[c] = CARRIED
        elif c in minimised:
            out[c] = MINIMISED
        elif c in dropped:
            out[c] = SECRET_DROPPED
        elif spec.klass == EXCLUDED:
            out[c] = EXCLUDED_COLUMN
        else:
            missing.append(c)
    return out, missing


_ACTIONS = (
    (re.compile(r"password|username_panel|url_panel|secret_code|datelogin|linksubx|proxy|inbound", re.I),
     "panel_credential", "Enter the panel connection again in NEXA's panel settings (secure credential flow).",
     "اطلاعات اتصال پنل را در تنظیمات پنل NEXA (مسیر امن اطلاعات ورود) دوباره وارد کنید."),
    (re.compile(r"bot_?token|bottype|^token$", re.I),
     "bot_token", "Bot tokens are never migrated: register the bot again in NEXA if it is still used.",
     "توکن ربات منتقل نمی‌شود؛ در صورت استفاده، ربات را در NEXA دوباره ثبت کنید."),
    (re.compile(r"webhook|jwt", re.I),
     "generated_secret", "Generated by NEXA itself: nothing to copy.",
     "NEXA این مقدار را خودش می‌سازد؛ نیازی به انتقال نیست."),
    (re.compile(r"api|merchant|marchent|wallet|key", re.I),
     "integration_key", "Enter the key again in NEXA's integration/gateway settings if still needed.",
     "در صورت نیاز، کلید را در تنظیمات اتصال/درگاه NEXA دوباره وارد کنید."),
    (re.compile(r"card|cart|sheba|iban", re.I),
     "payout_card", "Enter the payout card again in NEXA's payout settings.",
     "کارت دریافت وجه را در تنظیمات پرداخت NEXA دوباره وارد کنید."),
    (re.compile(r"code|invitation|ref", re.I),
     "redeemable_code", "Codes are not migrated (redeemable); create new ones in NEXA.",
     "کدها منتقل نمی‌شوند (قابل استفاده‌اند)؛ در NEXA کد جدید بسازید."),
    (re.compile(r"admin|login|username|header|output|response|setting|data|link", re.I),
     "admin_or_raw_secret", "Not needed by NEXA as data; re-create admins/links by hand if still used.",
     "NEXA به این داده نیاز ندارد؛ در صورت نیاز مدیر/لینک را دستی بسازید."),
)


def _action(column: str) -> tuple[str, str, str]:
    for pattern, category, action, action_fa in _ACTIONS:
        if pattern.search(column):
            return category, action, action_fa
    return ("other_secret", "Review: re-enter it by hand in NEXA if it is still needed.",
            "بررسی شود: در صورت نیاز در NEXA دستی وارد شود.")


def manual_reconfiguration_entries(stats: Any, spec: TableSpec) -> list[dict[str, Any]]:
    """One entry per secret (or minimised-secret) column present in the dump. Names only, no value."""
    out: list[dict[str, Any]] = []
    minimised_secret = [c for c in stats.minimised_columns
                        if (spec.column(c) is not None and spec.column(c).sensitivity == SECRET)]
    for c in list(dict.fromkeys(stats.dropped_columns + stats.carry_secret_columns)) + minimised_secret:
        declared = spec.column(c)
        basis = ("catalogue" if declared is not None
                 else "name_heuristic" if column_name_is_secret(c) else "settings_value_blob")
        category, action, action_fa = _action(c)
        out.append({"table": stats.name, "column": c, "key": None, "rows": stats.rows, "basis": basis,
                    "category": category, "kept": "key_names_only" if c in minimised_secret else "nothing",
                    "note": declared.note if declared is not None else None,
                    "action": action, "action_fa": action_fa})
    return out


# ----------------------------------------------------------------------------------------------
# Conversion
# ----------------------------------------------------------------------------------------------

_MESSAGES = {
    "CARRY_UNVERIFIED_TABLE_ARCHIVED": "این جدول بدون مستند عمومی است و فقط بر اساس نام ستون‌ها منتقل شد "
                                       "(ستون‌های شبیه رمز/توکن حذف شدند)؛ مالک باید محتوای آن را تأیید کند.",
    "CARRY_DUPLICATE_PK": "کلید اصلی تکراری در ستون‌های تکمیلی؛ همه ردیف‌ها با پسوند متمایز حفظ شدند.",
}


def _file_for(spec: TableSpec, table: str) -> tuple[str, str]:
    owned = spec.module == MODULE_KEY
    configuration = spec.klass == CONFIGURATION
    rel = "configuration" if configuration else "archive"
    record_type = "legacy_configuration_row" if configuration else "legacy_archive_row"
    name = table if owned else f"{table}_carried"
    if not re.fullmatch(r"[A-Za-z0-9_]{1,58}", name):  # NEXA reads [A-Za-z0-9_]{1,64}.jsonl
        name = "t_" + re.sub(r"[^A-Za-z0-9_]", "_", name)[:40] + ("" if owned else "_carried")
    return f"records/{rel}/{name}.jsonl", record_type


def _write_table(ctx: ModuleContext, stats: Any, spec: TableSpec, taken: set[str]) -> int:
    from .payments import customer_relation

    owned = spec.module == MODULE_KEY
    path, record_type = _file_for(spec, stats.name)
    if path in taken:  # a real table already uses this file name: never mix two tables in one file
        path = path.replace(".jsonl", "_m2n.jsonl")
    taken.add(path)
    sink = ctx.open(path)
    customer_col = next((c for c in spec.customer_columns if c in stats.columns), None)
    invoice_col = spec.invoice_column if spec.invoice_column in stats.columns else None
    minimisers = {c: spec.column(c).minimise for c in stats.minimised_columns if spec.column(c) is not None}
    prefix = stats.name if owned else f"carried:{stats.name}"
    pk = stats.primary_key or spec.primary_key
    raw_links = {c for c in ((() if pk_is_sensitive(pk) else pk) + (customer_col, invoice_col)) if c is not None}
    seen: dict[str, int] = {}
    keys: set[str] = set()
    dup = n = 0
    for _rowno, row in ctx.carry_rows(stats):
        ctx.tick()
        unrepresentable = sorted(c for c, v in row.items() if v == "\x00UNREPRESENTABLE")
        row = {c: (None if v == "\x00UNREPRESENTABLE" else v) for c, v in row.items()}
        checksum = ctx.row_checksum(prefix, row)
        key, source_pk = idempotency_key(prefix, pk, row, checksum, seen, mask=lambda v: ctx.secret_ref(v, "pk"))
        if key in keys:
            dup += 1
            key = f"{key}#dup{dup}"
        keys.add(key)
        rec: dict[str, Any] = {
            "record_type": record_type,
            "schema": f"mirza.{stats.name}.v1" if owned else SCHEMA_SUPPLEMENT,
            "idempotency_key": key,
            "class": spec.klass,
            "source_table": stats.name,
            "source_pk": source_pk,
            "carry": "table" if owned else "supplement",
            # identifiers that link the record (key, customer, invoice) stay raw: a 15-digit invoice
            # key can pass the card checksum, and the same value is in source_pk / invoice_key anyway
            "fields": {c: row.get(c) if c in raw_links else ctx.redact(row.get(c), stats.name)
                       for c in stats.carried_columns},
            "minimised": {c: {"method": minimisers.get(c), "value": row.get(c)} for c in stats.minimised_columns},
            "secret_columns_dropped": list(dict.fromkeys(stats.dropped_columns + stats.carry_secret_columns)),
            "applies_to_live_state": False,
        }
        if not owned:
            rec["supplements"] = "the table's own records (same source_table / source_pk)"
        if customer_col is not None:
            rec["customer"] = customer_relation(ctx, row.get(customer_col))
        if invoice_col is not None:
            inv = row.get(invoice_col)
            rec["invoice"] = {"invoice_key": inv, "in_backup": ctx.invoice_exists(inv)}
        if unrepresentable:
            rec["unrepresentable_columns"] = unrepresentable
        rec["provenance"] = ctx.provenance(stats.name, source_pk, checksum)
        sink.write(rec)
        n += 1
    if dup:
        ctx.review(kind="archive", code="CARRY_DUPLICATE_PK", entity="archive", table=stats.name, pk=None,
                   severity="warning", data={"duplicates": dup})
    ctx.coverage(stats.name, carry_file=path, carry_records=n)
    return n


def run(ctx: ModuleContext) -> None:
    ctx.register_messages(reviews=_MESSAGES)
    taken: set[str] = set()
    for stats in ctx.all_tables():
        spec = ctx.spec(stats.name)
        if spec is None or spec.klass == EXCLUDED:
            continue
        owned = spec.module == MODULE_KEY
        n = _write_table(ctx, stats, spec, taken) if stats.carry_sql_name else 0
        if owned:
            # The carry IS the table's archive: every row is one record (or counted when nothing is
            # storable, so NEXA still sees all rows accounted).
            if stats.carry_sql_name:
                ctx.coverage(stats.name, archived=n, status="archived")
                ctx.count("archive", "converted", n)
            elif stats.rows:
                ctx.coverage(stats.name, skipped=stats.rows, status="archived",
                             reason_skipped="every column is secret: rows counted, nothing carried")
            if spec.name in UNVERIFIED_TABLES and stats.rows:
                ctx.review(kind="table", code="CARRY_UNVERIFIED_TABLE_ARCHIVED", entity="archive", table=stats.name,
                           pk=None, severity="warning",
                           data={"rows": stats.rows, "carried_columns": list(stats.carried_columns),
                                 "dropped_columns": list(stats.carry_secret_columns)})
        elif n:
            ctx.count("carried_columns", "converted", n)


register_module(Module(key=MODULE_KEY, title_fa="انتقال ستون‌های تکمیلی", run=run, order=950))


# ----------------------------------------------------------------------------------------------
# Synthetic columns (``mirza2nexa.synth``, ``SynthSpec.extra_columns``). FABRICATED values only.
# Every value of a column the catalogue drops contains ``SYNSECRET`` so tests can prove none leaves.
# ----------------------------------------------------------------------------------------------

SYNTH_SECRET_MARK = "SYNSECRET"  # noqa: S105 - a marker for fabricated values, not a secret


def synth_value(table: str, column: str, i: int, state: Any) -> str | None:
    """The fabricated value of an extra column in row ``i`` of ``table``."""
    spec = CATALOGUE.lookup(table)
    declared = spec.column(column) if spec is not None else None
    secret = (declared is not None and declared.sensitivity == SECRET and declared.minimise is None) or (
        declared is None and column_name_is_secret(column))
    if secret:
        return f"{SYNTH_SECRET_MARK}-{table}-{column}-{i}"
    invoices = state.invoice_keys or [None]
    special = {
        ("user", "first_name"): f"نام{i}",
        ("user", "hidden_gateways"): '["zarinpal"]',
        ("invoice", "note"): f"note {i}",
        ("invoice", "notifctions"): '{"volume":false}',
        ("service_other", "id_invoice"): invoices[i % len(invoices)],
        ("service_other", "params"): '{"volume":"10"}',
        ("marzban_panel", "hosts"): "host.example",
    }
    if (table, column) in special:
        return special[(table, column)]
    if i % 7 == 3:
        return None
    return f"v{i}"


#: table → extra columns written FIRST in the synthetic DDL (names of fork columns; the
#: ``affiliates``/``setting`` names beyond the declared ones are fabricated to exercise the name rule).
SYNTH_EXTRA_COLUMNS: dict[str, tuple[str, ...]] = {
    "user": tuple(c.name for c in USER_CARRY if c.name != "affiliates"),  # the base DDL already has it
    "invoice": tuple(c.name for c in INVOICE_CARRY) + ("bottype",),
    "product": tuple(c.name for c in PRODUCT_CARRY),
    "service_other": tuple(c.name for c in SERVICE_OTHER_CARRY),
    "category": tuple(c.name for c in CATEGORY_CARRY),
    "reagent_report": tuple(c.name for c in REAGENT_CARRY),
    "affiliates": ("commission_type", "gift_token"),
    "setting": ("jwt_secret", "ai_api_key", "ai_status"),
    "marzban_panel": tuple(c.name for c in MARZBAN_PANEL_CARRY),
    "botsaz": ("product_info",),
}

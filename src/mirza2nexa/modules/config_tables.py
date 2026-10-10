"""Configuration, operator and infrastructure tables: settings, texts, help, channels, admins …

Module key ``config_tables`` (order 500). It owns every Mirza table that the public sources
define and that is not claimed by the core / finance / services / business / support modules,
so that a backup of any known Mirza version has **zero uncatalogued tables**.

Sources (read-only clones):

* ``botmirzapanel`` @ ``92c0ed06`` — ``table.php`` (older Mirza; ``textbot`` lives here);
* ``mirza_pro`` @ ``b645db74`` — ``db/tables.php`` (the table list), ``db/tables/*.php`` (DDL,
  seeds) and ``db/migrations/*.php``;
* ``nexa-bot`` @ ``9e604def`` — ``docs/research/**`` (settings, gateways, admin, texts).

Decisions (full table with citations: ``docs/DATA_PROTECTION.md``):

* **CONFIGURATION** — ``setting`` (column allow-list), ``PaySetting`` and ``shopSetting``
  (key allow-lists), ``textbot``, ``channels`` (without invite links), ``help``, ``app``, ``protocol``.
  Rows become ``legacy_configuration_row`` records in ``records/configuration/<table>.jsonl``;
  every value goes through :func:`~mirza2nexa.sensitivity.redact_text`. NEXA uses them as
  reference for the operator only: nothing is applied to live configuration.
* **REVIEW** — ``help_category`` (no public DDL): fails closed, nothing staged, rows counted and a
  ``CONFIG_NO_STORABLE_COLUMN`` review item asks for the column list.
* **EXCLUDED** — ``card_number`` (payout cards), ``logs_api`` (request log with headers/bodies),
  ``admin`` (web-panel logins + password hashes), ``botsaz`` (sub-bot tokens, webhook secrets) and
  ``topicid`` (bound to the old report group). Nothing is staged; coverage counts the rows.

Column sensitivities follow ``docs/evidence/mirza_tables.json`` (Source DB agent) and are never
weaker than it.

Key-value tables (``PaySetting``, ``shopSetting``): only keys on an explicit allow-list leave
the converter. Known credential keys and keys the evidence does not describe are dropped and
counted (``dropped_secret_keys`` / ``dropped_unknown_keys`` in coverage). NOTE: the value column
of these tables is staged (it must be, to read the allow-listed values) and the staging SQLite is
deleted at the end of the run; see ``docs/DATA_PROTECTION.md`` §"Residual risks" for the proposed
staging-time row filter that would remove even that.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

from .. import synth as _synth
from ..catalogue import (
    CATALOGUE,
    CONFIGURATION,
    EXCLUDED,
    FINANCIAL,
    PERSONAL,
    PUBLIC,
    REVIEW,
    SECRET,
    ColumnSpec,
    TableSpec,
)
from .base import Module, ModuleContext, register_module
from .generic import ARCHIVE_SCHEMA_VERSION, idempotency_key

MODULE_KEY = "config_tables"

BMP = "botmirzapanel@92c0ed06"
PRO = "mirza_pro@b645db74"
NEXA_R = "nexa-bot@9e604def docs/research"

EVJ = "docs/evidence/mirza_tables.json"
_SEC_NOTE = "authenticates to an external service; never staged"


def _c(name: str, sens: str = PUBLIC, note: str = "") -> ColumnSpec:
    return ColumnSpec(name, sens, note)


# ---- setting: one wide row of bot switches ------------------------------------------------
# Union of mirza_pro db/tables/setting.php:18-66,114-164, botmirzapanel table.php:110-143 and the
# columns removed by mirza_pro migrations 010 (statusagentrequest) and 011 (wheelـluck,
# affiliatesstatus). Values are on/off flags, counters, JSON layouts and texts.
SETTING_PUBLIC = (
    "Bot_Status", "help_Status", "roll_Status", "get_number", "iran_number", "NotUser", "limit_usertest_all",
    "time_usertest", "val_usertest", "Extra_volume", "namecustome", "status_verify", "removedayc", "copy_cart",
    "statuscategory", "showcard", "numbercount", "statusnewuser", "statusterffh", "volumewarn", "inlinebtnmain",
    "verifystart", "statusnamecustom", "statuscategorygenral", "statussupportpv", "bulkbuy", "on_hold_day",
    "cronvolumere", "verifybucodeuser", "scorestatus", "btn_status_extned", "daywarn",
    "categoryhelp", "linkappstatus", "wheelagent", "Lotteryagent", "statusfirstwheel", "statuslimitchangeloc",
    "Debtsettlement", "Dice", "keyboardmain", "statusnoteforf", "statuscopycart", "timeauto_not_verify",
    "status_keyboard_config", "cron_status", "text_edit", "limitnumber", "alert_balance",
    "statusagentrequest", "wheelـluck", "affiliatesstatus",
)
SETTING_FINANCIAL = ("affiliatespercentage", "agentreqprice", "wheelـluck_price", "balance_value_alert", "Lottery_prize")
SETTING_COLUMNS = (
    *(_c(n) for n in SETTING_PUBLIC),
    *(_c(n, FINANCIAL, "price/percentage setting; unit as declared for the whole backup") for n in SETTING_FINANCIAL),
    _c("Channel_Report", PERSONAL, "Telegram chat id of the admin report group (index.php:68-70); not a credential"),
    _c("id_support", PERSONAL, "support username shown to users as t.me link (keyboard.php:1099)"),
    _c("webhook_secret", SECRET, "Telegram webhook secret (setting.php:66,161); " + _SEC_NOTE),
)

# ---- PaySetting: NamePay → ValuePay ------------------------------------------------------
# Keys: mirza_pro db/tables/PaySetting.php:7-96, migration 005 and every key the code reads
# (getPaySettingValue / select("PaySetting", …)); botmirzapanel table.php:516-532.
PAYSETTING_ALLOWED = frozenset({
    # gateway on/off switches
    "Cartstatus", "Cartstatuspv", "nowpaymentstatus", "digistatus", "statusSwapWallet", "statusaqayepardakht",
    "statustarnado", "feestatusternado", "zarinpalstatus", "statusiranpay3", "statusiranpay4", "autoconfirmcart",
    "statusstar", "statusnowpayment", "checkpaycartfirst", "variza_status", "paymentstatussnotverify",
    # amounts: top-up limits, fees, cashback percentages, daily limit
    "minbalance", "maxbalance", "feeternado", "dailylimitiranpay4",
    "chashbackcart", "chashbackstar", "chashbackperfect", "chashbackaqaypardokht", "chashbackiranpay1",
    "chashbackiranpay2", "chashbackiranpay3", "chashbackiranpay4", "chashbackplisio", "chashbackzarinpal",
    "cashbacknowpayment", "chashbackvariza",
    *(f"{p}balance{g}" for p in ("min", "max") for g in (
        "cart", "star", "plisio", "digitaltron", "iranpay", "iranpay1", "iranpay2", "iranpay4", "aqayepardakht",
        "paynotverify", "perfect", "zarinpal", "nowpayment", "variza")),
    # tutorial message per gateway: '2' (none) or JSON {type,text,photoid|videoid} (admin.php:6640-6660)
    "helpcart", "helpaqayepardakht", "helpstar", "helpplisio", "helpiranpay1", "helpiranpay2", "helpiranpay3",
    "helpiranpay4", "helpvariza", "helpperfectmony", "helpzarinpal", "helpnowpayment", "helpofflinearze",
})
PAYSETTING_SECRET = frozenset({
    "cardnumber", "CartDescription",  # business payout card (PaySetting.php:10; table.php:516)
    "namecard",  # card holder name tied to the payout card (PaySetting.php:11)
    "CartDirect",  # operator's personal Telegram username for direct card payments (PaySetting.php:9)
    "Exception_auto_cart",  # JSON list of customer ids exempt from auto-confirm (PaySetting.php:45)
    "apinowpayment", "merchant_id_aqayepardakht", "marchent_tronseller", "walletaddress", "apiternado",
    "merchant_zarinpal", "apiiranpay", "apiiranpay4", "endpointiranpay4", "variza_api_token",
    "variza_webhook_secret", "marchent_floypay", "urlpaymenttron",
    "perfectmoney_AccountID", "perfectmoney_PassPhrase", "perfectmoney_Payer_Account",
})

# ---- shopSetting: Namevalue → value (mirza_pro db/tables/shopSetting.php:5-23) -------------
SHOPSETTING_ALLOWED = frozenset({
    "customvolmef", "customvolmen", "customvolmen2", "customtimepricef", "customtimepricen",
    "customtimepricen2", "statusextra", "statustimeextra", "statusdirectpabuy", "minbalancebuybulk",
    "statusdisorder", "statuschangeservice", "statusshowprice", "configshow", "backserviecstatus",
    "chashbackextend", "chashbackextend_agent",
})
SHOPSETTING_SECRET: frozenset[str] = frozenset()

KV_TABLES: dict[str, tuple[str, str, frozenset[str], frozenset[str]]] = {
    "PaySetting": ("NamePay", "ValuePay", PAYSETTING_ALLOWED, PAYSETTING_SECRET),
    "shopSetting": ("Namevalue", "value", SHOPSETTING_ALLOWED, SHOPSETTING_SECRET),
}

# Flags written on every record of a REVIEW table (all False: nothing is applied).
REVIEW_FLAGS: dict[str, dict[str, bool]] = {"help_category": {"activates_role": False}}


def _spec(name: str, klass: str, purpose: str, purpose_fa: str, reason: str, nexa_target: str,
          evidence: tuple[str, ...], columns: tuple[ColumnSpec, ...] | None, *, primary_key: tuple[str, ...] = (),
          open_questions: tuple[str, ...] = (), sensitivity: str = PUBLIC) -> TableSpec:
    return TableSpec(name=name, klass=klass, module=MODULE_KEY, purpose=purpose, purpose_fa=purpose_fa,
                     reason=reason, nexa_target=nexa_target, evidence=evidence, primary_key=primary_key,
                     columns=columns, open_questions=open_questions, sensitivity=sensitivity)


SPECS: tuple[TableSpec, ...] = (
    _spec("setting", CONFIGURATION, "Global bot switches, keyboard layout, cron flags (one row)",
          "تنظیمات عمومی ربات (کلیدهای روشن/خاموش، چیدمان کیبورد)",
          "Non-secret switches useful to the NEXA operator as reference; webhook_secret is a credential.",
          "operator reference only (legacy_configuration_row); never applied",
          (f"{PRO} db/tables/setting.php:18-66,114-164", f"{BMP} table.php:110-143",
           f"{PRO} db/migrations/010_setting_drop_agent_request.php, 011_setting_drop_wheel_affiliates_status.php",
           f"{NEXA_R}/mirzabotgeneralsettingsfollowupcomplete", f"{EVJ}#setting"),
          SETTING_COLUMNS, sensitivity=SECRET,
          open_questions=("Columns added by Mirza versions newer than the sources are not exported until reviewed "
                          "(reported as unmapped_columns).",)),
    _spec("PaySetting", CONFIGURATION, "Payment gateway switches, limits and credentials (key-value)",
          "تنظیمات درگاه‌های پرداخت (فقط کلیدهای غیرحساس)",
          "Only allow-listed keys (switches, limits, cashback, tutorial texts) are exported; gateway API keys, "
          "merchant ids, payout card/wallet and unknown keys are dropped.",
          "operator reference only; NEXA gateways are configured fresh",
          (f"{PRO} db/tables/PaySetting.php:7-110", f"{PRO} db/migrations/005_paysetting_defaults.php",
           f"{PRO} db/migrations/008_abangateway_gateway.php:21-29", f"{BMP} table.php:511-532",
           f"{NEXA_R}/mirzabotfinancialinvestigationcomplete", f"{EVJ}#PaySetting"),
          (_c("NamePay", PUBLIC, "setting key"),
           _c("ValuePay", FINANCIAL, "SECRET by default (evidence JSON); staged only so the module can export the "
                                       "allow-listed keys; every other key is dropped")),
          primary_key=("NamePay",), sensitivity=SECRET),
    _spec("shopSetting", CONFIGURATION, "Shop switches and custom-plan prices (key-value)",
          "تنظیمات فروشگاه و قیمت پلن دلخواه",
          "Seeded keys are prices/switches only (shopSetting.php:5-23); unknown keys are dropped.",
          "operator reference only", (f"{PRO} db/tables/shopSetting.php:5-36", f"{NEXA_R}/mirzabotstoresettingsfinalv2",
                                f"{EVJ}#shopSetting"),
          (_c("Namevalue", PUBLIC, "setting key"), _c("value", FINANCIAL, "prices in the backup's unit")),
          primary_key=("Namevalue",), sensitivity=FINANCIAL),
    _spec("textbot", CONFIGURATION, "Editable bot texts / button labels (older Mirza)",
          "متن‌ها و برچسب دکمه‌های ربات",
          "Templates useful to NEXA operators; free text is redacted (may contain card numbers or links).",
          "operator reference (text templates)", (f"{BMP} table.php:460-509", f"{BMP} index.php:105",
                                                  f"{NEXA_R}/mirzabotbottextmanagementinvestigationcomplete",
                                                  f"{EVJ}#textbot"),
          (_c("id_text"), _c("text", PUBLIC, "free text; redacted")), primary_key=("id_text",)),
    _spec("topicid", EXCLUDED, "Report type → forum topic id in the admin report group",
          "شناسه تاپیک گزارش‌ها در گروه مدیریت",
          "Bound to the old Telegram report group; NEXA has its own ops-log topics (evidence JSON "
          "recommended_class EXCLUDED). Not secret, but has no value outside the old group.",
          "none (NEXA ops_log_topics configured by hand)",
          (f"{PRO} db/tables/topicid.php:3-27", f"{PRO} function.php:298-309",
           f"{NEXA_R}/mirzabottelegramloggroupinvestigationcomplete", f"{EVJ}#topicid"),
          (_c("report"), _c("idreport")), primary_key=("report",)),
    _spec("channels", CONFIGURATION, "Forced-join channels shown to every user",
          "کانال‌های عضویت اجباری",
          "Channel list is useful to the operator; linkjoin (invite link) grants access to a private channel and "
          "is SECRET per the evidence JSON, so only remark and the public @link are exported.",
          "operator reference (NEXA telegram.channels setting, manual)",
          (f"{PRO} db/tables/channels.php:3-13", f"{BMP} table.php:192-193", f"{PRO} index.php:128,296-304",
           f"{EVJ}#channels"),
          (_c("remark"), _c("linkjoin", SECRET, "private-channel invite link; grants access"), _c("link"))),
    _spec("help", CONFIGURATION, "Help / tutorial pages (name, media file id, description)",
          "صفحات راهنما",
          "Customer-facing tutorials; Media_os is a bot-specific Telegram file id (re-upload needed).",
          "operator reference (help pages)", (f"{PRO} db/tables/help.php:3-15", f"{BMP} table.php:90-96",
                                              f"{PRO} index.php:3096-3180", f"{EVJ}#help"),
          (_c("id"), _c("name_os"), _c("Media_os", PUBLIC, "Telegram file_id, valid only for the old bot"),
           _c("type_Media_os"), _c("category"), _c("Description_os", PUBLIC, "free text; redacted")),
          primary_key=("id",)),
    _spec("app", CONFIGURATION, "Client app download links (name → URL)", "لینک دانلود اپلیکیشن‌ها",
          "Public download links shown as buttons (keyboard.php:844-850).", "operator reference",
          (f"{PRO} db/tables/app.php:3-9", f"{PRO} admin.php:8170-8190", f"{PRO} keyboard.php:844-850",
           f"{EVJ}#app"),
          (_c("id"), _c("name"), _c("link")), primary_key=("id",)),
    _spec("protocol", CONFIGURATION, "Protocol names listed in the old admin keyboard", "نام پروتکل‌ها",
          "Referenced only when present (keyboard.php:336-350); no DDL in either source.",
          "operator reference", (f"{BMP} keyboard.php:336-350",), (_c("NameProtocol"),),
          open_questions=("No CREATE TABLE in the public sources; only NameProtocol is read.",)),
    _spec("help_category", REVIEW, "Help categories (deployed fork only; no public DDL)", "دسته‌بندی راهنما",
          "Reported by the operator but absent from both public sources: fails closed — no column is staged "
          "(empty column allow-list) and rows are only counted until the column list and semantics are supplied.",
          "UNKNOWN until columns are known", (f"{EVJ}#help_category",),
          (), open_questions=("Supply the CREATE TABLE header (no values) to classify its columns.",
                              "Does it hold secrets or money?")),
    _spec("admin", EXCLUDED, "Bot/web-panel administrators and their role", "مدیران ربات و نقش آن‌ها",
          "Holds web-panel login names and password hashes; admins are re-created in NEXA by hand (own RBAC + "
          "2FA). Role enforcement in Mirza is NOT_TESTED. Nothing is staged; rows are counted.",
          "none (NEXA admins re-created manually)",
          (f"{PRO} db/tables/admin.php:3-23", f"{PRO} db/migrations/012_admin_panel_credentials.php:7-16",
           f"{BMP} table.php:176-190",
           f"{NEXA_R}/mirzabotadminmanagementinvestigationcomplete/admin-management-knowledge/MASTER.md",
           f"{EVJ}#admin"),
          (_c("id_admin", PERSONAL, "admin Telegram id"), _c("rule", PUBLIC, "role name"),
           _c("username", SECRET, "web-panel login name; half of a credential pair"),
           _c("password", SECRET, "bcrypt hash or legacy plaintext (migration 012); " + _SEC_NOTE)),
          primary_key=("id_admin",), sensitivity=SECRET,
          open_questions=("Which Mirza admins become NEXA admins, with which role? (decided by hand)",)),
    _spec("botsaz", EXCLUDED, "Reseller sub-bots built by agents (one bot per agent)", "ربات‌های نمایندگان",
          "Bot tokens, webhook secrets and per-bot settings (card info); NEXA re-creates bots by hand. Nothing is "
          "staged; rows are counted.", "none (NEXA bot_instances, manual)",
          (f"{PRO} db/tables/botsaz.php:3-19", f"{PRO} api/users.php:600-668", f"{PRO} function.php:1815",
           f"{EVJ}#botsaz"),
          (_c("id"), _c("id_user", PERSONAL, "agent Telegram id"), _c("admin_ids", PERSONAL, "JSON list of ids"),
           _c("username", PUBLIC, "public bot username"),
           _c("setting", SECRET, "JSON incl. cart_info (payout card text)"), _c("hide_panel"), _c("time"),
           _c("bot_token", SECRET, "Telegram bot token; " + _SEC_NOTE),
           _c("webhook_secret", SECRET, "Telegram webhook secret; " + _SEC_NOTE)),
          primary_key=("id",), sensitivity=SECRET,
          open_questions=("Does NEXA support reseller sub-bots? Agents must re-register their bots.",)),
    _spec("card_number", EXCLUDED, "Business payout cards shown for card-to-card top-ups",
          "کارت‌های بانکی دریافت وجه",
          "Card number + holder name of the business payout account: secret, and NEXA configures its own payout "
          "methods. No receipt references a card row (index.php:4660 picks one at random), so even the last 4 "
          "digits have no proven reconciliation use.", "none",
          (f"{PRO} db/tables/card_number.php:3-9", f"{PRO} index.php:4660-4701",
           f"{PRO} db/migrations/003_card_number_charset.php", f"{EVJ}#card_number"),
          (_c("cardnumber", SECRET, "payout card number"), _c("namecard", PERSONAL, "card holder name")),
          primary_key=("cardnumber",), sensitivity=SECRET),
    _spec("logs_api", EXCLUDED, "Mini-app / API request log (headers, bodies, IPs)", "لاگ درخواست‌های API",
          "Raw request headers (Authorization) and bodies; Mirza itself purges it (optimize_order.php:15). "
          "No migration value.", "none",
          (f"{PRO} db/tables/logs_api.php:3-12", f"{PRO} api/utils.php:173", f"{PRO} cronbot/optimize_order.php:15",
           f"{EVJ}#logs_api"),
          (_c("id"), _c("header", SECRET, "raw request headers incl. Authorization"),
           _c("data", SECRET, "raw request body"), _c("ip", PERSONAL), _c("time"), _c("actions")),
          primary_key=("id",), sensitivity=SECRET),
)

for _s in SPECS:
    if _s.name in KV_TABLES:  # staging-time allow-list: unknown/secret keys never reach disk
        _k, _v, _allowed, _secret = KV_TABLES[_s.name]
        _s = dataclasses.replace(_s, kv_filter=(_k, _v, _allowed))
    CATALOGUE.register(_s)

CONFIG_TABLE_NAMES = tuple(s.name for s in SPECS)


# ---- conversion ----------------------------------------------------------------------------

def _record(ctx: ModuleContext, stats, row: dict[str, Any], *, record_type: str, klass: str,
            keys: set[str], seen: dict[str, int], dup: list[int], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    checksum = ctx.row_checksum(stats.name, row)
    key, pk = idempotency_key(stats.name, stats.primary_key, row, checksum, seen)
    if key in keys:
        dup[0] += 1
        key = f"{key}#dup{dup[0]}"
    keys.add(key)
    rec: dict[str, Any] = {
        "record_type": record_type,
        "schema": f"mirza.{stats.name}.{ARCHIVE_SCHEMA_VERSION}",
        "idempotency_key": key,
        "class": klass,
        "source_table": stats.name,
        "source_pk": pk,
        "fields": {c: ctx.redact(v, stats.name) for c, v in row.items()},
        "applies_to_live_state": False,
    }
    rec.update(extra or {})
    rec["provenance"] = ctx.provenance(stats.name, pk, checksum)
    return rec


def _dup_review(ctx: ModuleContext, table: str, dup: int) -> None:
    if dup:
        ctx.review(kind="archive", code="ARCHIVE_DUPLICATE_PK", entity="configuration", table=table, pk=None,
                   severity="warning", data={"duplicates": dup},
                   message="کلید اصلی تکراری در جدول تنظیمات؛ همه ردیف‌ها با پسوند متمایز حفظ شدند.")


def _export_plain(ctx: ModuleContext, stats, spec: TableSpec) -> int:
    configuration = spec.klass == CONFIGURATION
    rel = "configuration" if configuration else "archive"
    record_type = "legacy_configuration_row" if configuration else "legacy_archive_row"
    flags = {} if configuration else REVIEW_FLAGS.get(spec.name, {})
    sink = ctx.open(f"records/{rel}/{stats.name}.jsonl")
    keys: set[str] = set()
    seen: dict[str, int] = {}
    dup = [0]
    n = 0
    for _rowno, row in ctx.rows(stats):
        ctx.tick()
        sink.write(_record(ctx, stats, row, record_type=record_type, klass=spec.klass, keys=keys, seen=seen,
                           dup=dup, extra=dict(flags)))
        n += 1
    _dup_review(ctx, stats.name, dup[0])
    return n


def _export_kv(ctx: ModuleContext, stats, spec: TableSpec) -> tuple[int, int]:
    key_col, value_col, allowed, known_secret = KV_TABLES[spec.name]
    sink = ctx.open(f"records/configuration/{stats.name}.jsonl")
    keys: set[str] = set()
    seen: dict[str, int] = {}
    dup = [0]
    exported = dropped = unknown = 0
    secret_present: list[str] = []
    for _rowno, row in ctx.rows(stats):
        ctx.tick()
        name = row.get(key_col)
        if name is None or name not in allowed:
            dropped += 1
            if name in known_secret:
                if name not in secret_present:
                    secret_present.append(name)
            else:
                unknown += 1
            continue
        kept = {key_col: name, value_col: row.get(value_col)}
        sink.write(_record(ctx, stats, kept, record_type="legacy_configuration_row", klass=spec.klass, keys=keys,
                           seen=seen, dup=dup))
        exported += 1
    _dup_review(ctx, stats.name, dup[0])
    ctx.coverage(stats.name, dropped_secret_keys=sorted(secret_present), dropped_unknown_keys=unknown,
                 key_allow_list=True)
    if unknown:
        ctx.warn("CONFIG_UNKNOWN_KEYS_DROPPED", unknown,
                 message="کلیدهای ناشناخته جداول تنظیمات (بدون مستند) منتقل نشدند؛ فقط کلیدهای مجاز صادر شدند.")
    return exported, dropped


def run(ctx: ModuleContext) -> None:
    owned = {s.name: s for s in SPECS}
    for stats in ctx.all_tables():
        spec = owned.get(stats.spec_name or "")
        if spec is None:
            continue
        if spec.klass == EXCLUDED:
            ctx.coverage(stats.name, status="excluded")
            ctx.count("configuration", "skipped", stats.rows)
            continue
        if not stats.sql_name:  # no documented, storable column: rows are only counted
            if stats.rows:
                ctx.coverage(stats.name, skipped=stats.rows, status="review", reason_skipped="no storable column")
                ctx.review(kind="table", code="CONFIG_NO_STORABLE_COLUMN", entity="configuration",
                           table=stats.name, pk=None, severity="warning", data={"rows": stats.rows},
                           message="هیچ ستون مستندی در این جدول نبود؛ ردیف‌ها فقط شمارش شدند.")
            continue
        if spec.name in KV_TABLES:
            n, skipped = _export_kv(ctx, stats, spec)
            ctx.coverage(stats.name, archived=n, skipped=skipped, status="archived")
            ctx.count("configuration", "converted", n)
            ctx.count("configuration", "skipped", skipped)
            continue
        n = _export_plain(ctx, stats, spec)
        if spec.klass == CONFIGURATION:
            ctx.coverage(stats.name, archived=n, status="archived")
            ctx.count("configuration", "converted", n)
        else:
            ctx.coverage(stats.name, archived=n, status="review" if spec.klass == REVIEW else "archived")
            ctx.count("archive", "converted", n)
            if spec.klass == REVIEW and n:
                ctx.review(kind="table", code="TABLE_NEEDS_DECISION", entity="archive", table=stats.name,
                           pk=None, severity="warning",
                           data={"rows": n, "open_questions": list(spec.open_questions)},
                           message="معنای این جدول قطعی نیست؛ داده‌ها فقط آرشیو شدند و تصمیم مالک لازم است.")
        if spec.name == "setting" and n > 1:
            ctx.review(kind="table", code="SETTING_MULTIPLE_ROWS", entity="configuration", table=stats.name,
                       pk=None, severity="warning", data={"rows": n},
                       message="جدول setting بیش از یک ردیف دارد؛ میرزا فقط ردیف اول را می‌خواند.")


register_module(Module(key=MODULE_KEY, title_fa="تنظیمات، متن‌ها و جداول زیرساختی", run=run, order=500))

# ---- synthetic tables with planted fake secrets ---------------------------------------------
# Every value below is FABRICATED for tests. The secrets are planted so the tests can prove that
# none of them reaches the package, the reports, the API or the history.

PLANTED_BOT_TOKEN = "123456789:AAFakeFakeFakeFakeFakeFakeFakeFakeFak"  # noqa: S105 - fabricated
PLANTED_AGENT_BOT_TOKEN = "987654321:AAsyntheticSyntheticSyntheticSynth0"  # noqa: S105 - fabricated
PLANTED_VLESS = "vless://11111111-2222-4333-8444-555555555555@panel.example:443?type=tcp&security=tls#syn"
PLANTED_SUB_URL = "https://panel.example/sub/abcdef0123456789syntheticSubToken"
PLANTED_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzeW50aGV0aWMtYWRtaW4ifQ.c3ludGhldGljLXNpZ25hdHVyZS12YWx1ZQ"
PLANTED_CARD = "6037997599999993"  # Luhn-valid fabricated test number
PLANTED_PASSWORD = "P@ssw0rd-synthetic"  # noqa: S105 - fabricated test secret
PLANTED_IBAN = "IR062960000000100324200001"
PLANTED_WEBHOOK_SECRET = "5f" * 24
PLANTED_MERCHANT = "a1b2c3d4-0000-4000-8000-123456789abc"
PLANTED_API_KEY = "NOWPAYsyntheticKEY0123456789abcdefXYZ"
PLANTED_WALLET = "TSyntheticWalletAddr000000000000000"
PLANTED_ADMIN_LOGIN = "synthetic_admin_login"
PLANTED_BCRYPT = "$2y$10$syntheticsyntheticsyntheticsyntheticsyntheticsynth"

PLANTED_SECRETS: tuple[str, ...] = (
    PLANTED_BOT_TOKEN, PLANTED_AGENT_BOT_TOKEN, PLANTED_VLESS, PLANTED_SUB_URL, PLANTED_JWT, PLANTED_CARD,
    PLANTED_PASSWORD, PLANTED_IBAN, PLANTED_WEBHOOK_SECRET, PLANTED_MERCHANT, PLANTED_API_KEY, PLANTED_WALLET,
    PLANTED_ADMIN_LOGIN, PLANTED_BCRYPT,
    # tokens embedded inside the links above (must not survive on their own either)
    "abcdef0123456789syntheticSubToken", "AAFakeFakeFakeFakeFakeFakeFakeFakeFak",
)

_DDL = {
    "setting": ("CREATE TABLE `setting` (\n"
                + "".join(f"  `{c}` varchar(600) DEFAULT NULL,\n" for c in (
                    "Bot_Status", "roll_Status", "get_number", "Channel_Report", "limit_usertest_all",
                    "affiliatespercentage", "id_support", "agentreqprice", "wheelـluck_price", "keyboardmain",
                    "cron_status", "text_edit", "webhook_secret", "zz_future_column"))
                + "  `statuscopycart` varchar(45) NOT NULL\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n"),
    "PaySetting": ("CREATE TABLE `PaySetting` (\n  `NamePay` varchar(500) NOT NULL,\n  `ValuePay` text NOT NULL,\n"
                   "  PRIMARY KEY (`NamePay`)\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n"),
    "shopSetting": ("CREATE TABLE `shopSetting` (\n  `Namevalue` varchar(500) NOT NULL,\n  `value` text NOT NULL,\n"
                    "  PRIMARY KEY (`Namevalue`)\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n"),
    "card_number": ("CREATE TABLE `card_number` (\n  `cardnumber` varchar(500) NOT NULL,\n"
                    "  `namecard` varchar(1000) NOT NULL,\n  PRIMARY KEY (`cardnumber`)\n) ENGINE=InnoDB;\n"),
    "textbot": ("CREATE TABLE `textbot` (\n  `id_text` varchar(600) NOT NULL,\n  `text` text NOT NULL,\n"
                "  PRIMARY KEY (`id_text`)\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n"),
    "topicid": ("CREATE TABLE `topicid` (\n  `report` varchar(500) NOT NULL,\n  `idreport` text NOT NULL,\n"
                "  PRIMARY KEY (`report`)\n) ENGINE=InnoDB;\n"),
    "logs_api": ("CREATE TABLE `logs_api` (\n  `id` int(6) unsigned NOT NULL AUTO_INCREMENT,\n  `header` json DEFAULT NULL,\n"
                 "  `data` json DEFAULT NULL,\n  `ip` varchar(200) NOT NULL,\n  `time` varchar(200) NOT NULL,\n"
                 "  `actions` varchar(200) NOT NULL,\n  PRIMARY KEY (`id`)\n) ENGINE=InnoDB;\n"),
    "channels": ("CREATE TABLE `channels` (\n  `remark` varchar(200) NOT NULL,\n  `linkjoin` varchar(200) NOT NULL,\n"
                 "  `link` varchar(200) NOT NULL\n) ENGINE=InnoDB;\n"),
    "help": ("CREATE TABLE `help` (\n  `id` int(6) unsigned NOT NULL AUTO_INCREMENT,\n  `name_os` varchar(500) NOT NULL,\n"
             "  `Media_os` varchar(5000) NOT NULL,\n  `type_Media_os` varchar(500) NOT NULL,\n  `category` text NOT NULL,\n"
             "  `Description_os` text NOT NULL,\n  PRIMARY KEY (`id`)\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n"),
    "admin": ("CREATE TABLE `admin` (\n  `id_admin` varchar(500) NOT NULL,\n  `username` varchar(1000) NOT NULL,\n"
              "  `password` varchar(1000) NOT NULL,\n  `rule` varchar(500) NOT NULL,\n  PRIMARY KEY (`id_admin`)\n"
              ") ENGINE=InnoDB;\n"),
    "botsaz": ("CREATE TABLE `botsaz` (\n  `id` int(6) unsigned NOT NULL AUTO_INCREMENT,\n  `id_user` varchar(200) NOT NULL,\n"
               "  `bot_token` varchar(200) NOT NULL,\n  `admin_ids` text NOT NULL,\n  `username` varchar(200) NOT NULL,\n"
               "  `setting` text,\n  `hide_panel` json NOT NULL,\n  `webhook_secret` varchar(200) NOT NULL DEFAULT '',\n"
               "  `time` varchar(200) NOT NULL,\n  PRIMARY KEY (`id`)\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n"),
    "app": ("CREATE TABLE `app` (\n  `id` int(6) unsigned NOT NULL AUTO_INCREMENT,\n  `name` varchar(200) NOT NULL,\n"
            "  `link` varchar(200) NOT NULL,\n  PRIMARY KEY (`id`)\n) ENGINE=InnoDB;\n"),
    "protocol": "CREATE TABLE `protocol` (\n  `NameProtocol` varchar(200) NOT NULL\n) ENGINE=InnoDB;\n",
    # FABRICATED shape (no public DDL exists): exercises the fail-closed path for help_category.
    "help_category": ("CREATE TABLE `help_category` (\n  `id` int(6) unsigned NOT NULL AUTO_INCREMENT,\n"
                      "  `name` varchar(200) NOT NULL,\n  `token` varchar(200) DEFAULT NULL,\n  PRIMARY KEY (`id`)\n"
                      ") ENGINE=InnoDB;\n"),
}


def synth_rows(state: Any) -> dict[str, list[tuple[Any, ...]]]:
    """Synthetic rows per table (values are Python str/int/None)."""
    admin_id = state.user_ids[0] if state.user_ids else "7000000000"
    agent_id = state.user_ids[1] if len(state.user_ids) > 1 else "7000000001"
    keyboard = json.dumps({"keyboard": [[{"text": "text_sell"}, {"text": "text_extend"}]]})
    pay_help = json.dumps({"type": "text", "text": f"کارت به کارت به {PLANTED_CARD} و password: {PLANTED_PASSWORD}"},
                          ensure_ascii=False)
    return {
        "setting": [("botstatuson", "rolleon", "offAuthenticationphone", "-1001234567890", "1", "10",
                     "synthetic_support", "50000", "0", keyboard, json.dumps({"day": True}),
                     json.dumps({"text_start": f"خوش آمدید {PLANTED_SUB_URL}"}, ensure_ascii=False),
                     PLANTED_WEBHOOK_SECRET, "future", "0")],
        "PaySetting": [
            ("Cartstatus", "oncard"), ("minbalance", "20000"), ("maxbalance", "1000000"), ("helpcart", pay_help),
            ("chashbackcart", "2"), ("zarinpalstatus", "offzarinpal"),
            ("cardnumber", PLANTED_CARD), ("namecard", "synthetic payee"), ("CartDirect", "@synthetic_cart_admin"),
            ("merchant_zarinpal", PLANTED_MERCHANT), ("apinowpayment", PLANTED_API_KEY),
            ("variza_api_token", PLANTED_JWT), ("variza_webhook_secret", PLANTED_WEBHOOK_SECRET),
            ("walletaddress", PLANTED_WALLET), ("perfectmoney_PassPhrase", PLANTED_PASSWORD),
            ("zz_undocumented_gateway_key", PLANTED_IBAN),
        ],
        "shopSetting": [("customvolmef", "4000"), ("statusextra", "offextra"),
                        ("chashbackextend_agent", json.dumps({"n": 0, "n2": 0}))],
        "card_number": [(PLANTED_CARD, "synthetic payee"), ("6037990000000006", "synthetic payee 2")],
        "textbot": [
            ("text_start", "سلام! به ربات آزمایشی خوش آمدید. مبلغ 250000 تومان، کد 1234."),
            ("text_cart", f"لطفاً مبلغ را به کارت {PLANTED_CARD} یا شبا {PLANTED_IBAN} واریز کنید."),
            ("text_dec_fq", f"نمونه کانفیگ: {PLANTED_VLESS} و لینک اشتراک {PLANTED_SUB_URL}"),
            ("text_admin_note", f"bot {PLANTED_BOT_TOKEN} jwt {PLANTED_JWT} رمز: {PLANTED_PASSWORD}"),
        ],
        "topicid": [("buyreport", "12"), ("paymentreport", "13"), ("backupfile", "0")],
        "logs_api": [(1, json.dumps({"Authorization": f"Bearer {PLANTED_JWT}"}),
                      json.dumps({"token": PLANTED_BOT_TOKEN, "password": PLANTED_PASSWORD}), "198.51.100.7",
                      "1700000000", "users/info")],
        "channels": [("کانال آزمایشی", "https://t.me/+SyntheticInvite", "@synthetic_channel")],
        "help": [(1, "اندروید", "BAACAgQAAxkBAAISyntheticFileId", "video", "عمومی",
                  f"آموزش اتصال. لینک نمونه: {PLANTED_SUB_URL}"),
                 (2, "آیفون", "", "none", "عمومی", "ابتدا برنامه را نصب کنید.")],
        "admin": [(admin_id, PLANTED_ADMIN_LOGIN, PLANTED_BCRYPT, "administrator"),
                  (agent_id, "synthetic_support_login", PLANTED_PASSWORD, "support")],
        "botsaz": [(1, agent_id, PLANTED_AGENT_BOT_TOKEN, json.dumps([agent_id]), "synthetic_agent_bot",
                    json.dumps({"minpricetime": 4000, "support_username": "@support",
                                "cart_info": f"کارت {PLANTED_CARD}"}, ensure_ascii=False),
                    "{}", PLANTED_WEBHOOK_SECRET, "2026/01/01 10:00:00")],
        "app": [(1, "v2rayNG", "https://example.invalid/apps/v2rayng"),
                (2, "Streisand", "https://example.invalid/apps/streisand")],
        "protocol": [("vless",), ("vmess",)],
        "help_category": [(1, "عمومی", PLANTED_BOT_TOKEN)],
    }


def _sql_value(v: Any) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, int):
        return str(v)
    return _synth.sql_str(str(v))


def _synth_hook(out: Any, state: Any) -> None:
    rows = synth_rows(state)
    for table, ddl in _DDL.items():
        out.write(f"DROP TABLE IF EXISTS `{table}`;\n{ddl}")
        data = rows.get(table) or []
        if data:
            out.write(f"LOCK TABLES `{table}` WRITE;\n")
            state.write_rows(out, table, ["(" + ",".join(_sql_value(v) for v in r) + ")" for r in data])
            out.write("UNLOCK TABLES;\n")


_synth.register_synth(MODULE_KEY, _synth_hook, order=500)

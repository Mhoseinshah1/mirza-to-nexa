"""Services, panels and catalogue side tables: ``service_other``, ``cancel_service``, ``manualsell``,
``marzban_panel``, ``category`` and the deployed fork's ``vs_*`` tables.

Nothing here touches a panel or a wallet. Every record is history or reference data with
``provision``/``applies_to_live_state``/``creates_payment``/``affects_wallet`` set to ``False``.
See ``docs/SERVICES_AND_PRODUCTS.md`` for the evidence, the record shapes and the open questions.

Evidence references (file:line) are to the public Mirza sources:

* ``mirza_pro@b645db74`` (``MP``), the newer public fork with ``db/tables/*.php`` DDL;
* ``botmirzapanel@92c0ed06`` (``BMP``), the older public bot (``table.php``).

Neither is the deployed fork, so every column the converter reads is listed explicitly. Other
columns are reported as ``unmapped`` and never stored.

Record files written here:

=================================================  ===========================================
``records/service_operations.jsonl``               ``legacy_service_operation`` (service_other)
``records/service_cancellation_requests.jsonl``    ``legacy_service_cancellation_request``
``records/manual_config_inventory.jsonl``          ``legacy_manual_config_inventory`` (manualsell)
``records/panel_registry.jsonl``                   ``legacy_panel`` (marzban_panel, no secrets)
``records/category_catalogue.jsonl``               ``legacy_category`` (category)
``records/archive/vs_*.jsonl``                     ``legacy_archive_row`` (REVIEW class)
=================================================  ===========================================
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from ..catalogue import ARCHIVE, CATALOGUE, FINANCIAL, PERSONAL, PUBLIC, REVIEW, SECRET, ColumnSpec, TableSpec
from ..mapping import rules
from ..synth import TG_BASE, SynthState, register_synth, sql_str
from .base import Module, ModuleContext, register_module
from .generic import export_rows

MP = "mirza_pro@b645db74"
BMP = "botmirzapanel@92c0ed06"
NEXA = "nexa-bot@9e604def"

# --------------------------------------------------------------------------------------
# Catalogue
# --------------------------------------------------------------------------------------

_PANEL_CRED_NOTE = "credential/endpoint of a panel; never read (MP function.php:441, api/panels.php:88)"

SERVICE_OTHER_COLUMNS = (
    ColumnSpec("id", PUBLIC, "AUTO_INCREMENT PK (MP db/tables/service_other.php:5)"),
    ColumnSpec("id_user", PERSONAL, "Telegram id of the buyer (MP index.php:2125 ':id_user' => $from_id)"),
    ColumnSpec("username", PERSONAL, "panel account username = invoice.username (MP index.php:2126, function.php:1258)"),
    ColumnSpec("value", PERSONAL, "JSON details or a scalar; transfertouser stores the new owner id (MP index.php:2897)"),
    ColumnSpec("time", PUBLIC, "date('Y/m/d H:i:s') Asia/Tehran (MP index.php:2123, index.php:3)"),
    ColumnSpec("price", FINANCIAL, "amount charged, same unit as user.Balance (MP index.php:2130, admin.php:333)"),
    ColumnSpec("type", PUBLIC, "operation kind (MP index.php:1802,2124,2315,2669,2899,5982,6717; gift.php:76,125)"),
    ColumnSpec("status", PUBLIC, "paid/unpaid/NULL (MP index.php:1803,1873; service_other.php:17)"),
    ColumnSpec("output", SECRET, "raw panel API response; may carry subscription links (MP index.php:2134, "
                                 "function.php:1299)"),
)

CANCEL_SERVICE_COLUMNS = (
    ColumnSpec("id", PUBLIC),
    ColumnSpec("id_user", PERSONAL, "requester Telegram id (MP index.php:2765-2768)"),
    ColumnSpec("username", PERSONAL, "panel account username of the invoice (MP index.php:2768)"),
    ColumnSpec("description", PERSONAL, "'0' at creation; admin's rejection text (MP admin.php:4674)"),
    ColumnSpec("status", PUBLIC, "waiting/accept/reject (MP index.php:2767, admin.php:4673,4748,4777)"),
)

MANUALSELL_COLUMNS = (
    ColumnSpec("id", PUBLIC),
    ColumnSpec("codepanel", PUBLIC, "marzban_panel.code_panel (MP admin.php:5934)"),
    ColumnSpec("codeproduct", PUBLIC, "product name/code chosen by the admin (MP admin.php:5938)"),
    ColumnSpec("namerecord", PUBLIC, "admin label of the stock item (MP admin.php:5935)"),
    ColumnSpec("username", PERSONAL, "service username after sale (MP panels/Drivers/Manualsale.php:22)"),
    ColumnSpec("contentrecord", SECRET, "the sold config / subscription link itself (MP Manualsale.php:18)"),
    ColumnSpec("status", PUBLIC, "active/selled/delete (MP admin.php:5931, Manualsale.php:21,56)"),
)

# marzban_panel: MP db/tables/marzban_panel.php:11-55 plus BMP table.php:208-224,232-296.
_PANEL_PUBLIC = (
    "id", "code_panel", "name_panel", "status", "type", "agent", "sublink", "config", "MethodUsername",
    "TestAccount", "limit_panel", "namecustom", "Methodextend", "conecton", "inboundstatus", "time_usertest",
    "val_usertest", "mainvolume", "maxvolume", "maintime", "maxtime", "status_extend", "subvip", "changeloc",
    "on_hold_test", "version_panel", "customvolume",
    # BMP-only flags
    "statusTest", "configManual", "onholdstatus",
)
_PANEL_FINANCIAL = ("priceChangeloc", "priceextravolume", "pricecustomvolume", "pricecustomtime", "priceextratime")
_PANEL_SECRET = ("url_panel", "username_panel", "password_panel", "secret_code", "datelogin", "linksubx",
                 "proxies", "inbounds", "inboundid", "inbound_deactive")
PANEL_COLUMNS = (
    tuple(ColumnSpec(c, PUBLIC) for c in _PANEL_PUBLIC)
    + tuple(ColumnSpec(c, FINANCIAL, "per-agent price JSON; reference only") for c in _PANEL_FINANCIAL)
    + (ColumnSpec("hide_user", PERSONAL, "JSON list of Telegram ids (MP keyboard.php:879); counted, not exported"),)
    + tuple(ColumnSpec(c, SECRET, _PANEL_CRED_NOTE) for c in _PANEL_SECRET)
)
PANEL_SECRET_COLUMNS = _PANEL_SECRET

CATEGORY_COLUMNS = (
    ColumnSpec("id", PUBLIC, "AUTO_INCREMENT PK (MP db/tables/category.php:6)"),
    ColumnSpec("remark", PUBLIC, "label; product.category stores this text (MP index.php:3595-3598)"),
)

VS_TABLES = ("vs_category", "vs_package", "vs_order", "vs_stock", "vs_setting")

_VS_QUESTIONS = (
    "OQ-SVC-VS-01: which feature of the deployed fork writes vs_* (no public DDL or code in MP/BMP)?",
    "OQ-SVC-VS-02: do vs_order rows carry money already counted in user.Balance or Payment_report?",
    "OQ-SVC-VS-03: should any vs_* data reach NEXA, or is an archive enough?",
)


def _register() -> None:
    common = {"module": "services_ext", "primary_key": ("id",)}
    CATALOGUE.register(TableSpec(
        name="service_other", klass=ARCHIVE,
        purpose="Per-service operations: renewals, extra volume/time, location change, transfer, gifts",
        purpose_fa="عملیات روی سرویس‌ها (تمدید، حجم/زمان اضافه، تغییر لوکیشن، انتقال، هدیه)",
        reason="History of operations already applied on panels and wallets (MP index.php:1792-1876, "
               "2121-2134, 2313-2316, 2896-2904; cronbot/gift.php:73-80). Never replayed.",
        nexa_target="records/service_operations.jsonl (history; no NEXA table yet)",
        evidence=(f"{MP} db/tables/service_other.php:4-19", f"{MP} index.php:1792-1806,1866-1880",
                  f"{MP} index.php:2121-2134,2313-2316,2666-2676,2896-2904,5979-5990,6708-6724",
                  f"{MP} function.php:1240-1300,1396-1402,1468-1474", f"{MP} api/invoice.php:306-314",
                  f"{MP} cronbot/gift.php:73-80,122-131", f"{MP} admin.php:333,411-429",
                  f"{MP} api/users.php:445"),
        columns=SERVICE_OTHER_COLUMNS, sensitivity=FINANCIAL,
        open_questions=(
            "OQ-SVC-OP-01: semantics of *_not_user types (extra_not_user, extends_not_user) are not proven.",
            "OQ-SVC-OP-02: does the deployed fork write service_other.status for every type?",
            "OQ-SVC-OP-03: should NEXA show these operations in a customer's service history?",
        ), **common))
    CATALOGUE.register(TableSpec(
        name="cancel_service", klass=ARCHIVE,
        purpose="Customer service-deletion requests and the admin's decision",
        purpose_fa="درخواست‌های حذف سرویس و تصمیم ادمین",
        reason="Requests with status waiting/accept/reject; the refund on accept is credited to user.Balance "
               "directly and its amount is not stored here (MP admin.php:4743-4749).",
        nexa_target="records/service_cancellation_requests.jsonl (history)",
        evidence=(f"{MP} db/tables/cancel_service.php:4-10", f"{BMP} table.php:593-603",
                  f"{MP} index.php:2737-2768", f"{MP} admin.php:4657-4680,4693-4777"),
        columns=CANCEL_SERVICE_COLUMNS, sensitivity=PERSONAL,
        open_questions=("OQ-SVC-CX-01: should pending (waiting) requests be re-opened in NEXA support?",),
        **common))
    CATALOGUE.register(TableSpec(
        name="manualsell", klass=ARCHIVE,
        purpose="Stock of manually created configs sold through the 'Manualsale' panel type",
        purpose_fa="موجودی کانفیگ‌های دستی (پنل نوع فروش دستی)",
        reason="Each row is one pre-made config; contentrecord is the config/link itself (secret, never read).",
        nexa_target="records/manual_config_inventory.jsonl (counts and states only)",
        evidence=(f"{MP} db/tables/manualsell.php:4-12", f"{MP} panels/Drivers/Manualsale.php:5-60",
                  f"{MP} admin.php:5925-5940,5967,6175", f"{MP} keyboard.php:868-881"),
        columns=MANUALSELL_COLUMNS, sensitivity=SECRET,
        open_questions=("OQ-SVC-MS-01: unsold stock (status active) must be re-entered in NEXA by hand, "
                        "or abandoned?",),
        **common))
    CATALOGUE.register(TableSpec(
        name="marzban_panel", klass=REVIEW,
        purpose="Panel registry: one row per panel server, with its credentials",
        purpose_fa="فهرست پنل‌ها (اطلاعات ورود هرگز منتقل نمی‌شود)",
        reason="Holds panel credentials (NEXA class SECRETS_MANUAL, table-inventory.md:17). Only identity and "
               "flags are exported; credentials are entered in NEXA's own secret flow.",
        nexa_target="records/panel_registry.jsonl → operator's nexa-legacy-panel-map/v1",
        evidence=(f"{MP} db/tables/marzban_panel.php:10-95", f"{BMP} table.php:204-296",
                  f"{MP} function.php:441", f"{MP} api/panels.php:88,120", f"{MP} panels/PanelRegistry.php:5-18",
                  f"{MP} db/migrations/002_panel_code_backfill.php", f"{NEXA} docs/legacy-migration/table-inventory.md:17",
                  f"{NEXA} docs/legacy-migration/importer.md:255-303"),
        columns=PANEL_COLUMNS, sensitivity=SECRET,
        open_questions=("OQ-SVC-PN-01: map each code_panel to a NEXA panel (operator panel map).",
                        "OQ-SVC-PN-02: panels referenced by invoices but absent from the registry: decommissioned?"),
        **common))
    CATALOGUE.register(TableSpec(
        name="category", klass=ARCHIVE,
        purpose="Product categories (labels); product.category stores the remark text",
        purpose_fa="دسته‌بندی محصولات",
        reason="7-row label table; products refer to it by remark text, not id (MP index.php:3595-3598).",
        nexa_target="records/category_catalogue.jsonl (proposal only; never auto-created)",
        evidence=(f"{MP} db/tables/category.php:4-9", f"{MP} keyboard.php:1376-1388", f"{MP} index.php:3593-3600",
                  f"{MP} api/category.php:31-163", f"{MP} api/miniapp.php:636-650"),
        columns=CATEGORY_COLUMNS, sensitivity=PUBLIC, **common))
    for name in VS_TABLES:
        CATALOGUE.register(TableSpec(
            name=name, klass=REVIEW, module="services_ext",
            purpose="Deployed-fork table without public DDL or code",
            purpose_fa="جدول نسخهٔ سفارشی میرزا بدون مستندات عمومی",
            reason="No evidence in MP/BMP/NEXA; columns come from the runtime DDL, credential-like names dropped.",
            nexa_target="records/archive/<table>.jsonl (review)",
            evidence=("none: absent from MP db/tables and BMP table.php",),
            columns=None, sensitivity=SECRET, open_questions=_VS_QUESTIONS))


_register()

# --------------------------------------------------------------------------------------
# Normalisation tables (evidence in comments)
# --------------------------------------------------------------------------------------

# (normalized, meaning_proven)
OP_TYPES: dict[str, tuple[str, bool]] = {
    # labels: MP admin.php:4365-4377 (lang/fa.php:2238-2242)
    "extend_user": ("RENEWAL", True),                        # MP index.php:1802,1876; function.php:1240-1300
    "extend_user_by_admin": ("RENEWAL_BY_ADMIN", True),      # MP admin.php:7642-7651; api/invoice.php:306-314
    "extends_not_user": ("RENEWAL_NOT_IN_LIST", True),       # MP index.php:6717; lang/fa.php:2242
    "extra_user": ("EXTRA_VOLUME", True),                    # MP index.php:2124; function.php:1380
    "extra_not_user": ("EXTRA_VOLUME_NOT_IN_LIST", False),   # MP index.php:5982 (no label; OQ-SVC-OP-01)
    "extra_time_user": ("EXTRA_TIME", True),                 # MP index.php:2669; function.php:1452
    "change_location": ("CHANGE_LOCATION", True),            # MP index.php:2315
    "transfertouser": ("OWNERSHIP_TRANSFER", True),          # MP index.php:2885-2904 (value = new owner)
    "gift_volume": ("GIFT_VOLUME", True),                    # MP cronbot/gift.php:76 (price 0)
    "gift_time": ("GIFT_TIME", True),                        # MP cronbot/gift.php:125 (price 0)
}
# Types whose service is, by definition, not in the bot's invoice list (lang/fa.php:2242).
NOT_IN_LIST_TYPES = frozenset({"extends_not_user", "extra_not_user"})
# Types whose id_user is the acting ADMIN, not the owner (MP admin.php:7646 binds $from_id = admin).
# api/invoice.php:308 writes the owner instead, so the column is not an owner for this type at all.
ADMIN_ACTOR_TYPES = frozenset({"extend_user_by_admin"})
# What service_other.price means per type (docs/SOURCE_TABLES.md Q1 "Money").
PRICE_SEMANTICS = {
    "extend_user": "PRODUCT_PRICE_CHARGED",              # MP index.php:1805,1879 ($prodcut['price_product'])
    "extends_not_user": "PRODUCT_PRICE_CHARGED",         # MP index.php:6722
    "extend_user_by_admin": "PRODUCT_PRICE_OR_ZERO_NOT_CHARGED",  # admin.php:7651 price; api/invoice.php:313 0
    "extra_user": "WALLET_PRICE_OR_DIRECT_PAY_SHORTFALL",  # index.php:2130 vs function.php:1402 (shortfall)
    "extra_time_user": "WALLET_PRICE_OR_DIRECT_PAY_SHORTFALL",  # index.php:2676 vs function.php:1474
    "extra_not_user": "WALLET_PRICE_CHARGED",            # MP index.php:5973-5989
    "change_location": "PRODUCT_PRICE_RECORDED",         # MP index.php:2316 (charge path not proven)
    "transfertouser": "ZERO_BY_DESIGN",                  # MP index.php:2900
    "gift_volume": "ZERO_BY_DESIGN",                     # MP cronbot/gift.php:72
    "gift_time": "ZERO_BY_DESIGN",                       # MP cronbot/gift.php:121
}
OP_STATUSES = {"paid": "PAID", "unpaid": "UNPAID"}  # MP index.php:1803,1873; function.php:1300
# Keys written into service_other.value by the evidenced writers (all other keys: names only).
VALUE_KEYS = frozenset({
    "volumebuy", "Service_time", "oldvolume", "oldtime", "code_product", "id_order",  # index.php:1794-1800
    "old_panel", "new_panel", "volume", "used_traffic", "expire", "stateus",          # index.php:2305-2312
    "volume_value", "priceـper_gig", "old_volume", "expire_old",                      # index.php:2098-2103
    "day", "priceـper_day",                                                           # index.php:2642-2647
    "time_value",                                                                     # gift.php:122-126
})
CANCEL_STATUSES = {"waiting": "PENDING", "accept": "ACCEPTED", "reject": "REJECTED"}
MANUAL_STATUSES = {"active": "AVAILABLE", "selled": "SOLD", "delete": "DELETED"}
# MP panels/PanelRegistry.php:6-17
PANEL_TYPES = frozenset({"marzban", "marzneshin", "x-ui_single", "hiddify", "Manualsale", "WGDashboard", "ibsng",
                         "mikrotik", "mirza_agent", "rebecca", "nexora", "wg_mate"})
# MP admin.php:8351-8356 (active/disable); BMP admin.php:568,579 (activepanel/disablepanel)
PANEL_STATUSES = {"active": "ACTIVE", "activepanel": "ACTIVE", "disable": "DISABLED", "disablepanel": "DISABLED"}
TIME_ZONE = "Asia/Tehran"  # MP index.php:3 date_default_timezone_set
_TIME = re.compile(r"([0-9]{4})/([0-9]{2})/([0-9]{2}) ([0-9]{2}):([0-9]{2}):([0-9]{2})")

MESSAGES = {
    "SERVICE_OWNER_CONFLICT": "مالک این سرویس در جدول دیگری با مالک فاکتور یکی نیست و انتقال ثبت‌شده‌ای آن را توضیح "
                              "نمی‌دهد؛ مالکیت باید بررسی شود.",
    "SERVICE_OP_INVOICE_AMBIGUOUS": "چند فاکتور با این نام کاربری سرویس وجود دارد و فاکتور مربوط قطعی نیست.",
    "SERVICE_OP_INVOICE_NOT_FOUND": "فاکتوری با این نام کاربری سرویس در بکاپ نیست؛ عملیات فقط آرشیو شد.",
    "SERVICE_OP_OWNER_NOT_IN_BACKUP": "کاربر ثبت‌کنندهٔ این عملیات در جدول کاربران نیست.",
    "SERVICE_OP_TYPE_UNKNOWN": "نوع عملیات سرویس مستند نیست؛ مقدار خام حفظ شد.",
    "SERVICE_OP_AMOUNT_UNREADABLE": "مبلغ عملیات سرویس قابل خواندن نیست؛ مقدار خام حفظ شد و هرگز اعمال نمی‌شود.",
    "SOURCE_ROW_KEY_INVALID": "شناسهٔ ردیف خالی یا تکراری است؛ ردیف با کلید جایگزین حفظ شد.",
    "SERVICE_PANEL_CONFLICT": "کد پنل این کانفیگ دستی با کد پنل فاکتور همان سرویس یکی نیست.",
    "CANCELLED_SERVICE_STILL_LIVE": "درخواست حذف پذیرفته شده ولی فاکتور سرویس هنوز وضعیت فعال دارد.",
    "PANEL_NOT_IN_REGISTRY": "کد پنلی که فاکتورها به آن اشاره می‌کنند در جدول پنل‌ها نیست.",
    "PANEL_WITHOUT_INVOICES": "این پنل در جدول پنل‌ها هست ولی هیچ فاکتوری به آن اشاره نمی‌کند.",
    "PANEL_CODE_DUPLICATED": "کد پنل در جدول پنل‌ها تکراری یا خالی است؛ نگاشت پنل باید دستی انجام شود.",
    "CATEGORY_LABEL_NOT_IN_TABLE": "دستهٔ ثبت‌شده در محصول در جدول دسته‌بندی وجود ندارد.",
    "CATEGORY_REMARK_DUPLICATED": "نام دسته‌بندی تکراری است.",
    "TABLE_NO_STORABLE_COLUMNS": "این جدول ستون غیرحساسی برای انتقال ندارد؛ فقط شمارش شد.",
}
WARNINGS = {
    "SERVICE_OPERATIONS_HISTORY_ONLY": "عملیات سرویس‌ها (تمدید، حجم اضافه، …) فقط به‌عنوان تاریخچه منتقل می‌شوند و "
                                       "هیچ مبلغ یا تغییری دوباره اعمال نمی‌شود.",
    "PANEL_CREDENTIALS_NOT_EXPORTED": "اطلاعات ورود پنل‌ها در بسته نیست؛ باید در NEXA از مسیر امن خود NEXA وارد شود.",
    "SERVICE_OWNER_CONFLICTS": "برای برخی سرویس‌ها مالک در جداول مختلف ناسازگار است (صف بررسی).",
}


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------

def _q(col: str) -> str:
    return '"' + ("c_" + col).replace('"', '""') + '"'


def parse_time(raw: str | None) -> dict[str, Any]:
    """``date('Y/m/d H:i:s')`` in Asia/Tehran local time; never converted (no tz database needed)."""
    if raw is None or rules.js_trim(raw) == "":
        return {"raw": raw, "local": None, "time_zone": None, "parse_note": "EMPTY"}
    m = _TIME.fullmatch(rules.js_trim(raw))
    if m is None:
        return {"raw": raw, "local": None, "time_zone": None, "parse_note": "FORMAT_UNKNOWN"}
    try:
        dt = datetime(*(int(g) for g in m.groups()))  # noqa: DTZ001 - local wall time, zone stated separately
    except ValueError:
        return {"raw": raw, "local": None, "time_zone": None, "parse_note": "OUT_OF_RANGE"}
    return {"raw": raw, "local": dt.strftime("%Y-%m-%dT%H:%M:%S"), "time_zone": TIME_ZONE, "parse_note": "OK"}


def _amount(ctx: ModuleContext, raw: str | None) -> dict[str, Any]:
    value, note = rules.parse_price(raw)
    if raw is None:
        note = "EMPTY"
    out = {"raw": raw, "parse_note": note, **ctx.money(value), "applied": False}
    return out


def _value_details(ctx: ModuleContext, raw: str | None, table: str) -> dict[str, Any]:
    """Evidenced JSON keys of ``service_other.value`` as redacted scalars; unknown keys by name only."""
    if raw is None:
        return {"format": "EMPTY"}
    try:
        obj = json.loads(raw)
    except (ValueError, RecursionError):
        obj = None
    if isinstance(obj, dict):
        known: dict[str, Any] = {}
        other: list[str] = []
        for k in sorted(obj):
            v = obj[k]
            if k in VALUE_KEYS and (v is None or isinstance(v, (str, int, float, bool))):
                known[k] = ctx.redact(v, table) if isinstance(v, str) else v
            else:
                other.append(ctx.redact(str(k), table) or "")
        return {"format": "JSON", "fields": known, "unlisted_keys": other,
                "units": "UNKNOWN: panel values (bytes/timestamps) not interpreted"}
    return {"format": "TEXT", "text": ctx.redact(raw, table)}


class _Keys:
    """``legacy:<prefix>:<id>`` keys, with a stable fallback for NULL/duplicated ids."""

    def __init__(self, ctx: ModuleContext, prefix: str, table: str):
        self.ctx, self.prefix, self.table = ctx, prefix, table
        self.used: set[str] = set()
        self.bad = 0

    def key(self, pk: str | None, checksum: str) -> tuple[str, bool]:
        ok = pk is not None and rules.js_trim(pk) != ""
        key = f"legacy:{self.prefix}:{pk}" if ok else f"legacy:{self.prefix}:row:{checksum[:32]}"
        n = 1
        base = key
        while key in self.used:
            n += 1
            key = f"{base}#{n}"
        clean = ok and n == 1
        if not clean:
            self.bad += 1
        self.used.add(key)
        return key, clean


def _invoice_cols(ctx: ModuleContext) -> tuple[str, ...]:
    t = next((s for s in ctx.all_tables() if s.name == "invoice"), None)
    if t is None or not t.sql_name:
        return ()
    return t.stored_columns


class _Invoices:
    """Read-only index of ``src_invoice`` by panel username (temp table ``sx_inv``)."""

    def __init__(self, ctx: ModuleContext):
        self.ctx = ctx
        cols = _invoice_cols(ctx)
        self.available = all(c in cols for c in ("id_invoice", "id_user", "username"))
        self.has_panel = "code_panel" in cols
        self.has_status = "Status" in cols
        self.has_location = "Service_location" in cols
        c = ctx.conn
        c.execute("DROP TABLE IF EXISTS temp.sx_inv")
        c.execute("CREATE TEMP TABLE sx_inv (uname TEXT, k TEXT, owner TEXT, panel TEXT, status TEXT, rowno INTEGER)")
        if not self.available:
            return
        panel = _q("code_panel") if self.has_panel else "NULL"
        status = _q("Status") if self.has_status else "NULL"
        cur = c.execute(f"SELECT rowno, c_id_invoice, c_id_user, c_username, {panel}, {status} "  # noqa: S608
                        "FROM src_invoice ORDER BY rowno")
        batch: list[tuple] = []
        for rowno, k, owner, uname, cp, st in iter(cur.fetchone, None):
            u = rules.panel_username(uname)
            if u is None:
                continue
            batch.append((u, k, owner, rules.blank_to_none(cp), st, rowno))
            if len(batch) >= 2000:
                c.executemany("INSERT INTO sx_inv VALUES (?,?,?,?,?,?)", batch)
                batch.clear()
        if batch:
            c.executemany("INSERT INTO sx_inv VALUES (?,?,?,?,?,?)", batch)
        c.execute("CREATE INDEX temp.sx_inv_u ON sx_inv (uname)")

    def candidates(self, username: str | None) -> list[tuple[str | None, str | None, str | None, str | None]]:
        u = rules.panel_username(username)
        if u is None or not self.available:
            return []
        return list(self.ctx.conn.execute(
            "SELECT k, owner, panel, status FROM sx_inv WHERE uname = ? ORDER BY k, rowno", (u,)))

    def relate(self, username: str | None, owner: str | None) -> dict[str, Any]:
        """FOUND / NOT_FOUND / AMBIGUOUS by username; ties broken only by an exact owner match."""
        if not self.available:
            return {"state": "NOT_FOUND", "reason": "INVOICE_TABLE_UNAVAILABLE", "invoice_key": None,
                    "candidates": 0, "matched_by": None, "_inv": None}
        if rules.panel_username(username) is None:
            return {"state": "NOT_FOUND", "reason": "USERNAME_INVALID", "invoice_key": None, "candidates": 0,
                    "matched_by": None, "_inv": None}
        cands = self.candidates(username)
        if not cands:
            return {"state": "NOT_FOUND", "reason": "NO_INVOICE_WITH_USERNAME", "invoice_key": None,
                    "candidates": 0, "matched_by": None, "_inv": None}
        if len(cands) == 1:
            return {"state": "FOUND", "reason": None, "invoice_key": cands[0][0], "candidates": 1,
                    "matched_by": "username", "_inv": cands[0]}
        same = [c for c in cands if owner is not None and c[1] == owner]
        if len(same) == 1:
            return {"state": "FOUND", "reason": None, "invoice_key": same[0][0], "candidates": len(cands),
                    "matched_by": "username+owner", "_inv": same[0]}
        return {"state": "AMBIGUOUS", "reason": "SEVERAL_INVOICES_SHARE_USERNAME", "invoice_key": None,
                "candidates": len(cands), "matched_by": None, "_inv": None}


class _Transfers:
    """Ownership transfers recorded in service_other (type ``transfertouser``; MP index.php:2885-2904)."""

    def __init__(self, ctx: ModuleContext, stats_list):
        c = ctx.conn
        c.execute("DROP TABLE IF EXISTS temp.sx_xfer")
        c.execute("CREATE TEMP TABLE sx_xfer (uname TEXT, from_owner TEXT, to_owner TEXT)")
        for stats in stats_list:
            cols = stats.stored_columns
            if not all(x in cols for x in ("type", "username", "id_user", "value")):
                continue
            rows = c.execute(f'SELECT c_username, c_id_user, c_value FROM "{stats.sql_name}" '  # noqa: S608
                             "WHERE c_type = 'transfertouser'").fetchall()
            c.executemany("INSERT INTO sx_xfer VALUES (?,?,?)",
                          [(rules.panel_username(u), f, rules.js_trim(t) if t else t) for u, f, t in rows
                           if rules.panel_username(u) is not None])
        c.execute("CREATE INDEX temp.sx_xfer_u ON sx_xfer (uname)")
        self.conn = c

    def explains(self, username: str | None, op_owner: str | None, invoice_owner: str | None) -> bool:
        u = rules.panel_username(username)
        if u is None or op_owner is None or invoice_owner is None:
            return False
        rows = self.conn.execute("SELECT from_owner, to_owner FROM sx_xfer WHERE uname = ?", (u,)).fetchall()
        owners = {x for r in rows for x in r if x is not None}
        received = {r[1] for r in rows}
        return op_owner in owners and invoice_owner in received


def _owner(ctx: ModuleContext, raw: str | None) -> dict[str, Any]:
    decision = ctx.customer_decision(raw)
    return {"raw": raw, "telegram_user_id": rules.telegram_id(raw),
            "customer_decision": decision if decision is not None else "NOT_IN_BACKUP"}


def _owner_relation(xfer: _Transfers, rel: dict[str, Any], username: str | None, owner: str | None) -> str:
    inv = rel.get("_inv")
    if inv is None:
        return "NO_INVOICE"
    if inv[1] == owner:
        return "MATCHES_INVOICE"
    if xfer.explains(username, owner, inv[1]):
        return "PREVIOUS_OWNER_VIA_TRANSFER"
    return "CONFLICT"


def _public_rel(rel: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in rel.items() if not k.startswith("_")}


def _tables(ctx: ModuleContext, spec_name: str):
    """Every dump table resolving to ``spec_name``, including tables with no storable column."""
    return [t for t in ctx.all_tables() if t.spec_name == spec_name]


def _unstorable(ctx: ModuleContext, stats) -> bool:
    if stats.sql_name:
        return False
    if stats.rows:
        ctx.review(kind="table", code="TABLE_NO_STORABLE_COLUMNS", entity="archive", table=stats.name, pk=None,
                   severity="warning", data={"rows": stats.rows}, message=MESSAGES["TABLE_NO_STORABLE_COLUMNS"])
        ctx.coverage(stats.name, skipped=stats.rows, status="review")
    else:
        ctx.coverage(stats.name, status="empty")
    return True


def _review(ctx: ModuleContext, code: str, entity: str, table: str, pk: str | None, severity: str = "warning",
            data: dict[str, Any] | None = None) -> None:
    ctx.review(kind="service_history", code=code, entity=entity, table=table, pk=pk, severity=severity, data=data,
               message=MESSAGES[code])


# --------------------------------------------------------------------------------------
# service_other
# --------------------------------------------------------------------------------------

def _service_operations(ctx: ModuleContext, inv: _Invoices) -> _Transfers:
    tables = _tables(ctx, "service_other")
    stored = [t for t in tables if t.sql_name]
    xfer = _Transfers(ctx, stored)
    if not tables:
        return xfer
    sink = ctx.open("records/service_operations.jsonl")
    stats_out: dict[str, Any] = {
        "rows": 0, "records": 0, "by_type_raw": {}, "by_type": {}, "by_status": {}, "invoice_relation": {},
        "owner_relation": {}, "owner_customer_decision": {}, "owner_conflicts": 0, "amount_unreadable": 0,
        "historical_amounts_minor": {}, "invalid_or_duplicate_ids": 0,
    }
    for stats in tables:
        if _unstorable(ctx, stats):
            continue
        keys = _Keys(ctx, "service-op", stats.name)
        n_ok = n_review = 0
        for _rowno, r in ctx.rows(stats, order_by=["id"]):
            ctx.tick()
            stats_out["rows"] += 1
            pk = r.get("id")
            checksum = ctx.row_checksum(stats.name, r)
            key, clean = keys.key(pk, checksum)
            codes: list[tuple[str, str]] = []
            if not clean:
                codes.append(("SOURCE_ROW_KEY_INVALID", "warning"))
            type_raw = r.get("type")
            norm, proven = OP_TYPES.get(type_raw or "", ("UNKNOWN", False))
            if norm == "UNKNOWN":
                codes.append(("SERVICE_OP_TYPE_UNKNOWN", "warning"))
            status_raw = r.get("status")
            status = OP_STATUSES.get(status_raw or "", "UNKNOWN")
            if status_raw is None or status_raw == "":
                status = "NOT_RECORDED"  # writers that omit status (MP index.php:2121,2313); api/invoice.php:78
            amount = _amount(ctx, r.get("price"))
            if amount["parse_note"] not in ("OK",):
                stats_out["amount_unreadable"] += 1
                codes.append(("SERVICE_OP_AMOUNT_UNREADABLE", "warning"))
            actor_raw = r.get("id_user")
            admin_actor = type_raw in ADMIN_ACTOR_TYPES
            # For admin renewals id_user may be the ADMIN (MP admin.php:7646); it is never read as the owner.
            owner_raw = None if admin_actor else actor_raw
            owner = _owner(ctx, actor_raw)
            owner["role"] = "ACTOR_ADMIN_OR_OWNER_UNPROVEN" if admin_actor else "OWNER"
            if not admin_actor and owner["customer_decision"] == "NOT_IN_BACKUP":
                codes.append(("SERVICE_OP_OWNER_NOT_IN_BACKUP", "warning"))
            rel = inv.relate(r.get("username"), owner_raw)
            if rel["state"] == "NOT_FOUND" and type_raw not in NOT_IN_LIST_TYPES:
                codes.append(("SERVICE_OP_INVOICE_NOT_FOUND", "warning"))
            elif rel["state"] == "AMBIGUOUS":
                codes.append(("SERVICE_OP_INVOICE_AMBIGUOUS", "blocker"))
            orel = "NOT_AN_OWNER_FIELD" if admin_actor else _owner_relation(xfer, rel, r.get("username"), owner_raw)
            if orel == "CONFLICT":
                stats_out["owner_conflicts"] += 1
                codes.append(("SERVICE_OWNER_CONFLICT", "blocker"))
            amount["semantics"] = PRICE_SEMANTICS.get(type_raw or "", "UNKNOWN")
            for code, sev in codes:
                _review(ctx, code, "service_operations", stats.name, pk, sev,
                        data={"type_raw": type_raw if norm != "UNKNOWN" else None, "invoice_relation": rel["state"],
                              "invoice_key": rel["invoice_key"], "owner_relation": orel,
                              "candidates": rel["candidates"]})
            sink.write({
                "record_type": "legacy_service_operation",
                "schema": "mirza.service_other.v1",
                "idempotency_key": key,
                "legacy_id": pk,
                "operation_type": {"raw": type_raw, "normalized": norm, "meaning_proven": proven},
                "status": {"raw": status_raw, "normalized": status},
                "owner": owner,
                "panel_account_username": r.get("username"),
                "invoice_relation": _public_rel(rel),
                "owner_relation": orel,
                "amount": amount,
                "time": parse_time(r.get("time")),
                "details": _value_details(ctx, r.get("value"), stats.name),
                "review_codes": [c for c, _ in codes],
                "provision": False,
                "applies_to_live_state": False,
                "creates_payment": False,
                "affects_wallet": False,
                "provenance": ctx.provenance(stats.name, pk, checksum),
            })
            stats_out["records"] += 1
            for bucket, k in (("by_type_raw", type_raw if type_raw in OP_TYPES else "(other)"),
                              ("by_type", norm), ("by_status", status), ("invoice_relation", rel["state"]),
                              ("owner_relation", orel), ("owner_customer_decision", owner["customer_decision"])):
                stats_out[bucket][k] = stats_out[bucket].get(k, 0) + 1
            if amount["amount_minor"] is not None:
                b = stats_out["historical_amounts_minor"].setdefault(f"{norm}:{status}", {"count": 0, "sum": 0})
                b["count"] += 1
                b["sum"] += int(amount["amount_minor"])
            if codes:
                n_review += 1
            else:
                n_ok += 1
        stats_out["invalid_or_duplicate_ids"] += keys.bad
        ctx.coverage(stats.name, archived=n_ok, review=n_review, status="review" if n_review else "archived")
        ctx.count("service_operations", "source", n_ok + n_review)
        ctx.count("service_operations", "converted", n_ok + n_review)
    for bucket in ("by_type_raw", "by_type", "by_status", "invoice_relation", "owner_relation",
                   "owner_customer_decision"):
        stats_out[bucket] = dict(sorted(stats_out[bucket].items()))
    stats_out["historical_amounts_minor"] = {
        k: {"count": v["count"], "amount_minor": str(v["sum"])}
        for k, v in sorted(stats_out["historical_amounts_minor"].items())}
    stats_out["currency"] = ctx.currency
    stats_out["accounted"] = stats_out["records"] == stats_out["rows"]
    stats_out["applied_to_wallet"] = False
    stats_out["note"] = ("History only. Charges were already deducted from user.Balance or paid through "
                         "Payment_report when they happened; NEXA must never re-apply them.")
    ctx.reconciliation("service_operations", stats_out)
    if stats_out["rows"]:
        ctx.warn("SERVICE_OPERATIONS_HISTORY_ONLY", message=WARNINGS["SERVICE_OPERATIONS_HISTORY_ONLY"])
    if stats_out["owner_conflicts"]:
        ctx.warn("SERVICE_OWNER_CONFLICTS", stats_out["owner_conflicts"], message=WARNINGS["SERVICE_OWNER_CONFLICTS"])
    return xfer


# --------------------------------------------------------------------------------------
# cancel_service
# --------------------------------------------------------------------------------------

def _cancellations(ctx: ModuleContext, inv: _Invoices, xfer: _Transfers) -> dict[str, Any]:
    out: dict[str, Any] = {"rows": 0, "by_status": {}, "invoice_relation": {}, "owner_conflicts": 0,
                           "accepted_but_invoice_live": 0}
    tables = _tables(ctx, "cancel_service")
    if not tables:
        return out
    sink = ctx.open("records/service_cancellation_requests.jsonl")
    for stats in tables:
        if _unstorable(ctx, stats):
            continue
        keys = _Keys(ctx, "cancel-service", stats.name)
        n_ok = n_review = 0
        for _rowno, r in ctx.rows(stats, order_by=["id"]):
            ctx.tick()
            out["rows"] += 1
            pk = r.get("id")
            checksum = ctx.row_checksum(stats.name, r)
            key, clean = keys.key(pk, checksum)
            codes: list[tuple[str, str]] = [] if clean else [("SOURCE_ROW_KEY_INVALID", "warning")]
            status = CANCEL_STATUSES.get(r.get("status") or "", "UNKNOWN")
            owner_raw = r.get("id_user")
            rel = inv.relate(r.get("username"), owner_raw)
            orel = _owner_relation(xfer, rel, r.get("username"), owner_raw)
            if orel == "CONFLICT":
                out["owner_conflicts"] += 1
                codes.append(("SERVICE_OWNER_CONFLICT", "blocker"))
            live = rel["_inv"] is not None and rel["_inv"][3] in rules.LIVE_STATUSES
            if status == "ACCEPTED" and live:
                out["accepted_but_invoice_live"] += 1
                codes.append(("CANCELLED_SERVICE_STILL_LIVE", "warning"))
            for code, sev in codes:
                _review(ctx, code, "service_requests", stats.name, pk, sev,
                        data={"invoice_relation": rel["state"], "invoice_key": rel["invoice_key"],
                              "owner_relation": orel, "status": status})
            desc = r.get("description")
            sink.write({
                "record_type": "legacy_service_cancellation_request",
                "schema": "mirza.cancel_service.v1",
                "idempotency_key": key,
                "legacy_id": pk,
                "owner": _owner(ctx, owner_raw),
                "panel_account_username": r.get("username"),
                "invoice_relation": _public_rel(rel),
                "owner_relation": orel,
                "status": {"raw": r.get("status"), "normalized": status},
                # '0' is the placeholder written at creation (MP index.php:2766)
                "admin_note": None if desc in (None, "0") else ctx.redact(desc, stats.name),
                # The refund on ACCEPT goes straight to user.Balance; its amount is not stored (admin.php:4743-4748).
                "refund_amount": None, "refund_amount_note": "NOT_STORED_IN_SOURCE",
                "review_codes": [c for c, _ in codes],
                "provision": False, "applies_to_live_state": False, "affects_wallet": False,
                "creates_payment": False,
                "provenance": ctx.provenance(stats.name, pk, checksum),
            })
            out["by_status"][status] = out["by_status"].get(status, 0) + 1
            out["invoice_relation"][rel["state"]] = out["invoice_relation"].get(rel["state"], 0) + 1
            if codes:
                n_review += 1
            else:
                n_ok += 1
        ctx.coverage(stats.name, archived=n_ok, review=n_review, status="review" if n_review else "archived")
        ctx.count("service_requests", "source", n_ok + n_review)
        ctx.count("service_requests", "converted", n_ok + n_review)
    out["by_status"] = dict(sorted(out["by_status"].items()))
    out["invoice_relation"] = dict(sorted(out["invoice_relation"].items()))
    return out


# --------------------------------------------------------------------------------------
# manualsell
# --------------------------------------------------------------------------------------

def _manual_inventory(ctx: ModuleContext, inv: _Invoices) -> dict[str, Any]:
    out: dict[str, Any] = {"rows": 0, "by_status": {}, "by_panel": {}, "panel_conflicts": 0}
    tables = _tables(ctx, "manualsell")
    if not tables:
        return out
    sink = ctx.open("records/manual_config_inventory.jsonl")
    for stats in tables:
        if _unstorable(ctx, stats):
            continue
        keys = _Keys(ctx, "manualsell", stats.name)
        n_ok = n_review = 0
        for _rowno, r in ctx.rows(stats, order_by=["id"]):
            ctx.tick()
            out["rows"] += 1
            pk = r.get("id")
            checksum = ctx.row_checksum(stats.name, r)
            key, clean = keys.key(pk, checksum)
            codes: list[tuple[str, str]] = [] if clean else [("SOURCE_ROW_KEY_INVALID", "warning")]
            status = MANUAL_STATUSES.get(r.get("status") or "", "UNKNOWN")
            cp = rules.blank_to_none(r.get("codepanel"))
            rel = inv.relate(r.get("username"), None) if r.get("username") else \
                {"state": "NOT_SOLD", "reason": None, "invoice_key": None, "candidates": 0, "matched_by": None,
                 "_inv": None}
            if rel["_inv"] is not None and inv.has_panel and rel["_inv"][2] != cp:
                out["panel_conflicts"] += 1
                codes.append(("SERVICE_PANEL_CONFLICT", "warning"))
            for code, sev in codes:
                _review(ctx, code, "manual_inventory", stats.name, pk, sev,
                        data={"code_panel": cp, "invoice_key": rel["invoice_key"], "status": status})
            sink.write({
                "record_type": "legacy_manual_config_inventory",
                "schema": "mirza.manualsell.v1",
                "idempotency_key": key,
                "legacy_id": pk,
                "code_panel": cp,
                "code_product_raw": r.get("codeproduct"),
                "label": ctx.redact(r.get("namerecord"), stats.name),
                "status": {"raw": r.get("status"), "normalized": status},
                "sold_to_panel_username": r.get("username"),
                "invoice_relation": _public_rel(rel),
                "config_content": "NOT_EXPORTED_SECRET",
                "requires_manual_transfer": status == "AVAILABLE",
                "review_codes": [c for c, _ in codes],
                "provision": False, "applies_to_live_state": False,
                "provenance": ctx.provenance(stats.name, pk, checksum),
            })
            out["by_status"][status] = out["by_status"].get(status, 0) + 1
            pkey = cp or "(none)"
            out["by_panel"][pkey] = out["by_panel"].get(pkey, 0) + 1
            if codes:
                n_review += 1
            else:
                n_ok += 1
        ctx.coverage(stats.name, archived=n_ok, review=n_review, status="review" if n_review else "archived")
        ctx.count("manual_inventory", "source", n_ok + n_review)
        ctx.count("manual_inventory", "converted", n_ok + n_review)
    out["by_status"] = dict(sorted(out["by_status"].items()))
    out["by_panel"] = dict(sorted(out["by_panel"].items()))
    return out


# --------------------------------------------------------------------------------------
# marzban_panel
# --------------------------------------------------------------------------------------

def _invoice_panel_counts(ctx: ModuleContext, inv: _Invoices) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    if not inv.available or not inv.has_panel:
        return out
    ph = ",".join("?" for _ in rules.LIVE_STATUSES)
    status = "c_Status" if inv.has_status else "NULL"
    for code, total, live in ctx.conn.execute(
            f"SELECT c_code_panel, COUNT(*), SUM(CASE WHEN {status} IN ({ph}) THEN 1 ELSE 0 END) "  # noqa: S608
            "FROM src_invoice GROUP BY c_code_panel", tuple(sorted(rules.LIVE_STATUSES))):
        cp = rules.blank_to_none(code)
        if cp is None:
            continue
        a = out.setdefault(cp, {"invoices": 0, "live_invoices": 0})
        a["invoices"] += total
        a["live_invoices"] += live or 0
    return out


def _location_counts(ctx: ModuleContext, inv: _Invoices) -> dict[str, int]:
    if not inv.available or not inv.has_location:
        return {}
    out: dict[str, int] = {}
    for loc, n in ctx.conn.execute("SELECT c_Service_location, COUNT(*) FROM src_invoice GROUP BY 1"):
        if loc is not None:
            out[loc] = out.get(loc, 0) + n
    return out


def _product_location_counts(ctx: ModuleContext) -> dict[str, int]:
    t = next((s for s in ctx.all_tables() if s.name == "product"), None)
    if t is None or not t.sql_name or "Location" not in t.stored_columns:
        return {}
    return {loc: n for loc, n in ctx.conn.execute("SELECT c_Location, COUNT(*) FROM src_product GROUP BY 1")
            if loc is not None}


def _panels(ctx: ModuleContext, inv: _Invoices, manual: dict[str, Any]) -> dict[str, Any]:
    by_code = _invoice_panel_counts(ctx, inv)
    by_loc = _location_counts(ctx, inv)
    prod_loc = _product_location_counts(ctx)
    tables = _tables(ctx, "marzban_panel")
    out: dict[str, Any] = {"registry_rows": 0, "invoice_panel_codes": len(by_code), "matched_codes": 0,
                           "invoice_codes_missing_from_registry": [], "registry_codes_without_invoices": [],
                           "duplicate_or_blank_codes": 0, "secret_columns_dropped": [],
                           "credentials_exported": False}
    seen_codes: dict[str, int] = {}
    if tables:
        sink = ctx.open("records/panel_registry.jsonl")
        for stats in tables:
            out["secret_columns_dropped"] = sorted(set(out["secret_columns_dropped"]) | set(stats.dropped_columns))
            if _unstorable(ctx, stats):
                continue
            rows = list(ctx.rows(stats, order_by=["id"]))
            for _rowno, r in rows:
                cp = rules.blank_to_none(r.get("code_panel"))
                if cp is not None:
                    seen_codes[cp] = seen_codes.get(cp, 0) + 1
            keys = _Keys(ctx, "panel-registry", stats.name)
            n_ok = n_review = 0
            for _rowno, r in rows:
                ctx.tick()
                out["registry_rows"] += 1
                pk = r.get("id")
                checksum = ctx.row_checksum(stats.name, r)
                cp = rules.blank_to_none(r.get("code_panel"))
                key, clean = keys.key(pk if pk is not None else cp, checksum)
                codes: list[tuple[str, str]] = [] if clean else [("SOURCE_ROW_KEY_INVALID", "warning")]
                if cp is None or seen_codes.get(cp, 0) > 1:
                    out["duplicate_or_blank_codes"] += 1
                    codes.append(("PANEL_CODE_DUPLICATED", "warning"))
                links = by_code.get(cp or "", {"invoices": 0, "live_invoices": 0})
                if cp is not None and links["invoices"] == 0 and by_code:
                    codes.append(("PANEL_WITHOUT_INVOICES", "warning"))
                for code, sev in codes:
                    ctx.review(kind="panel", code=code, entity="panels_registry", table=stats.name, pk=pk,
                               severity=sev, data={"code_panel": cp}, message=MESSAGES[code])
                name = r.get("name_panel")
                type_raw = r.get("type")
                settings = {c: ctx.redact(r[c], stats.name) for c in _PANEL_PUBLIC
                            if c in r and c not in ("id", "code_panel", "name_panel", "status", "type")}
                pricing = {c: ctx.redact(r[c], stats.name) for c in _PANEL_FINANCIAL if c in r}
                hidden = r.get("hide_user")
                sink.write({
                    "record_type": "legacy_panel",
                    "schema": "mirza.marzban_panel.v1",
                    "idempotency_key": key,
                    "legacy_id": pk,
                    "code_panel": cp,
                    "name_panel": ctx.redact(name, stats.name),
                    "panel_type": {"raw": type_raw, "normalized": type_raw if type_raw in PANEL_TYPES else "UNKNOWN"},
                    "status": {"raw": r.get("status"), "normalized": PANEL_STATUSES.get(r.get("status") or "",
                                                                                      "UNKNOWN")},
                    "settings_raw": settings,
                    "pricing_raw": {"values": pricing, "applied": False,
                                    "note": "per-agent JSON prices; reference only, never applied"},
                    "hidden_from_users_count": _json_len(hidden),
                    "invoice_links": {"by_code_panel": links["invoices"], "live_by_code_panel": links["live_invoices"],
                                      "by_service_location_name": by_loc.get(name, 0) if name is not None else 0},
                    "product_links": {"by_location_name": prod_loc.get(name, 0) if name is not None else 0},
                    "manual_stock_rows": manual.get("by_panel", {}).get(cp or "", 0),
                    "credentials_required_separately": True,
                    "credentials_in_package": False,
                    "secret_columns_dropped": list(stats.dropped_columns),
                    "nexa_panel_id": None,
                    "mapping_state": "OPERATOR_MUST_MAP",
                    "review_codes": [c for c, _ in codes],
                    "provision": False,
                    "applies_to_live_state": False,
                    "provenance": ctx.provenance(stats.name, pk, checksum),
                })
                if codes:
                    n_review += 1
                else:
                    n_ok += 1
            ctx.coverage(stats.name, archived=n_ok, review=n_review, status="review")
            ctx.count("panels_registry", "source", n_ok + n_review)
            ctx.count("panels_registry", "converted", n_ok + n_review)
        if out["registry_rows"]:
            ctx.warn("PANEL_CREDENTIALS_NOT_EXPORTED", message=WARNINGS["PANEL_CREDENTIALS_NOT_EXPORTED"])
    missing = sorted(c for c in by_code if c not in seen_codes)
    out["matched_codes"] = len(by_code) - len(missing)
    out["invoice_codes_missing_from_registry"] = [
        {"code_panel": c, "invoices": by_code[c]["invoices"], "live_invoices": by_code[c]["live_invoices"]}
        for c in missing]
    out["registry_codes_without_invoices"] = sorted(c for c in seen_codes if c not in by_code) if by_code else []
    out["registry_present"] = bool(tables)
    if tables:  # without a registry at all, the core panels.jsonl already lists every code
        for c in missing:
            ctx.review(kind="panel", code="PANEL_NOT_IN_REGISTRY", entity="panels_registry", table="invoice", pk=c,
                       severity="warning", data={"code_panel": c, **by_code[c]}, message=MESSAGES["PANEL_NOT_IN_REGISTRY"])
    return out


def _json_len(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        v = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    return len(v) if isinstance(v, (list, dict)) else None


# --------------------------------------------------------------------------------------
# category
# --------------------------------------------------------------------------------------

def _norm_label(v: str) -> str:
    """keyboard.php:1379 ``mb_strtolower(trim(...))``."""
    return v.strip().lower()


def _product_categories(ctx: ModuleContext) -> tuple[str | None, dict[str, int]]:
    t = next((s for s in ctx.all_tables() if s.name == "product"), None)
    if t is None or not t.sql_name:
        return None, {}
    col = "category" if "category" in t.stored_columns else "Category" if "Category" in t.stored_columns else None
    if col is None:
        return None, {}
    out: dict[str, int] = {}
    for v, n in ctx.conn.execute(f"SELECT {_q(col)}, COUNT(*) FROM src_product GROUP BY 1"):  # noqa: S608
        if v is not None and v.strip():
            out[v] = out.get(v, 0) + n
    return col, out


def _categories(ctx: ModuleContext) -> dict[str, Any]:
    col, prod = _product_categories(ctx)
    tables = _tables(ctx, "category")
    out: dict[str, Any] = {"rows": 0, "product_column": f"product.{col}" if col else None,
                           "products_with_category": sum(prod.values()), "matched_exact": 0,
                           "matched_normalized_only": 0, "unmatched_products": 0, "unmatched_labels": 0,
                           "categories_without_products": 0, "auto_create": False}
    remarks: list[tuple[str | None, str | None]] = []
    if tables:
        sink = ctx.open("records/category_catalogue.jsonl")
        prod_norm: dict[str, int] = {}
        for v, n in prod.items():
            prod_norm[_norm_label(v)] = prod_norm.get(_norm_label(v), 0) + n
        for stats in tables:
            if _unstorable(ctx, stats):
                continue
            rows = list(ctx.rows(stats, order_by=["id"]))
            norm_seen: dict[str, int] = {}
            for _rowno, r in rows:
                if r.get("remark") is not None:
                    k = _norm_label(r["remark"])
                    norm_seen[k] = norm_seen.get(k, 0) + 1
            keys = _Keys(ctx, "category-table", stats.name)
            n_ok = n_review = 0
            for _rowno, r in rows:
                ctx.tick()
                out["rows"] += 1
                pk = r.get("id")
                remark = r.get("remark")
                remarks.append((pk, remark))
                checksum = ctx.row_checksum(stats.name, r)
                key, clean = keys.key(pk, checksum)
                codes: list[str] = [] if clean else ["SOURCE_ROW_KEY_INVALID"]
                if remark is not None and norm_seen.get(_norm_label(remark), 0) > 1:
                    codes.append("CATEGORY_REMARK_DUPLICATED")
                exact = prod.get(remark, 0) if remark is not None else 0
                normalized = prod_norm.get(_norm_label(remark), 0) if remark is not None else 0
                if normalized == 0:
                    out["categories_without_products"] += 1
                for code in codes:
                    ctx.review(kind="category", code=code, entity="categories_table", table=stats.name, pk=pk,
                               severity="warning", data={}, message=MESSAGES[code])
                sink.write({
                    "record_type": "legacy_category",
                    "schema": "mirza.category.v1",
                    "idempotency_key": key,
                    "legacy_id": pk,
                    "label": ctx.redact(remark, stats.name),
                    "label_normalized": _norm_label(remark) if remark is not None else None,
                    "product_links": {"exact": exact, "normalized": normalized,
                                      "match_rule": "product.category == remark (MP index.php:3597); "
                                                    "normalized = lower(trim()) (MP keyboard.php:1379,1386)"},
                    "nexa_category_id": None,
                    "auto_create": False,
                    "review_state": "PENDING_REVIEW",
                    "review_codes": codes,
                    "applies_to_live_state": False,
                    "provenance": ctx.provenance(stats.name, pk, checksum),
                })
                if codes:
                    n_review += 1
                else:
                    n_ok += 1
            ctx.coverage(stats.name, archived=n_ok, review=n_review, status="review" if n_review else "archived")
            ctx.count("categories_table", "source", n_ok + n_review)
            ctx.count("categories_table", "converted", n_ok + n_review)
    exact_set = {rm for _, rm in remarks if rm is not None}
    norm_set = {_norm_label(rm) for _, rm in remarks if rm is not None}
    for label in sorted(prod):
        n = prod[label]
        if label in exact_set:
            out["matched_exact"] += n
        elif _norm_label(label) in norm_set:
            out["matched_normalized_only"] += n
        else:
            out["unmatched_products"] += n
            out["unmatched_labels"] += 1
            if tables:
                ctx.review(kind="category", code="CATEGORY_LABEL_NOT_IN_TABLE", entity="categories_table",
                           table="product", pk=None, severity="warning", data={"label": label, "products": n},
                           message=MESSAGES["CATEGORY_LABEL_NOT_IN_TABLE"])
    out["table_present"] = bool(tables)
    return out


# --------------------------------------------------------------------------------------
# vs_* (deployed fork, no evidence)
# --------------------------------------------------------------------------------------

def _vs_tables(ctx: ModuleContext) -> dict[str, int]:
    out: dict[str, int] = {}
    for name in VS_TABLES:
        spec = CATALOGUE.specs[name]
        for stats in _tables(ctx, name):
            if _unstorable(ctx, stats):
                continue
            n = export_rows(ctx, stats, record_type="legacy_archive_row", rel_dir="archive", klass=REVIEW)
            ctx.coverage(stats.name, archived=n, status="review")
            ctx.count("archive", "converted", n)
            out[stats.name] = n
            if n:
                ctx.review(kind="table", code="TABLE_NEEDS_DECISION", entity="archive", table=stats.name, pk=None,
                           severity="warning", data={"rows": n, "open_questions": list(spec.open_questions)},
                           message="معنای این جدول قطعی نیست؛ داده‌ها فقط آرشیو شدند و تصمیم مالک لازم است.")
    return dict(sorted(out.items()))


# --------------------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------------------

def run(ctx: ModuleContext) -> None:
    inv = _Invoices(ctx)
    xfer = _service_operations(ctx, inv)
    cancels = _cancellations(ctx, inv, xfer)
    manual = _manual_inventory(ctx, inv)
    panels = _panels(ctx, inv, manual)
    cats = _categories(ctx)
    vs = _vs_tables(ctx)
    conflicts = cancels["owner_conflicts"]
    if conflicts:
        ctx.warn("SERVICE_OWNER_CONFLICTS", conflicts, message=WARNINGS["SERVICE_OWNER_CONFLICTS"])
    ctx.reconciliation("service_requests", {"cancel_service": cancels, "manualsell": manual})
    ctx.reconciliation("panel_registry", panels)
    ctx.reconciliation("category_catalogue", cats)
    ctx.reconciliation("services_ext_review_tables", {"vs_tables": vs})
    for t in ("sx_inv", "sx_xfer"):
        ctx.conn.execute(f"DROP TABLE IF EXISTS temp.{t}")
    ctx.mapping({"entity": "service_operations", "source_table": "service_other",
                 "source_columns": [c.name for c in SERVICE_OTHER_COLUMNS if c.storable],
                 "target": "records/service_operations.jsonl (history; never applied)", "status": "partial",
                 "note": "Linked to invoice by panel username (MP function.php:1258); output column never read."})
    ctx.mapping({"entity": "panel_registry", "source_table": "marzban_panel",
                 "source_columns": list(_PANEL_PUBLIC),
                 "target": "operator nexa-legacy-panel-map/v1; credentials via NEXA secret flow only",
                 "status": "partial", "note": "Secret columns dropped at parse time: " + ", ".join(_PANEL_SECRET)})
    ctx.mapping({"entity": "category_catalogue", "source_table": "category", "source_columns": ["id", "remark"],
                 "target": "catalog categories (proposal; never auto-created)", "status": "partial",
                 "note": "product.category stores the remark text (MP index.php:3595-3598)."})


register_module(Module(key="services_ext", title_fa="سرویس‌ها، پنل‌ها و دسته‌بندی‌ها", run=run, order=300))


# --------------------------------------------------------------------------------------
# Synthetic tables (fabricated; DDL follows MP db/tables/*.php, vs_* DDL is INVENTED)
# --------------------------------------------------------------------------------------

DDL = {
    "service_other": """CREATE TABLE `service_other` (
  `id` int(6) unsigned NOT NULL AUTO_INCREMENT,
  `id_user` varchar(500) NOT NULL,
  `username` varchar(1000) NOT NULL,
  `value` varchar(1000) NOT NULL,
  `time` varchar(200) NOT NULL,
  `price` varchar(200) DEFAULT NULL,
  `type` varchar(1000) NOT NULL,
  `status` varchar(200) DEFAULT NULL,
  `output` text,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
""",
    "cancel_service": """CREATE TABLE `cancel_service` (
  `id` int(6) unsigned NOT NULL AUTO_INCREMENT,
  `id_user` varchar(500) NOT NULL,
  `username` varchar(1000) NOT NULL,
  `description` text NOT NULL,
  `status` varchar(1000) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
""",
    "manualsell": """CREATE TABLE `manualsell` (
  `id` int(6) unsigned NOT NULL AUTO_INCREMENT,
  `codepanel` varchar(100) NOT NULL,
  `codeproduct` varchar(100) NOT NULL,
  `namerecord` varchar(200) NOT NULL,
  `username` varchar(500) DEFAULT NULL,
  `contentrecord` text NOT NULL,
  `status` varchar(200) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
""",
    "category": """CREATE TABLE `category` (
  `id` int(6) unsigned NOT NULL AUTO_INCREMENT,
  `remark` varchar(500) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
""",
}
PANEL_DDL_COLUMNS = (
    "id", "code_panel", "name_panel", "status", "url_panel", "username_panel", "password_panel", "agent", "sublink",
    "config", "MethodUsername", "TestAccount", "limit_panel", "namecustom", "Methodextend", "conecton", "linksubx",
    "inboundid", "type", "inboundstatus", "inbound_deactive", "time_usertest", "val_usertest", "secret_code",
    "priceChangeloc", "priceextravolume", "pricecustomvolume", "pricecustomtime", "priceextratime", "mainvolume",
    "maxvolume", "maintime", "maxtime", "status_extend", "datelogin", "proxies", "inbounds", "subvip", "changeloc",
    "on_hold_test", "version_panel", "customvolume", "hide_user",
)
DDL["marzban_panel"] = (
    "CREATE TABLE `marzban_panel` (\n"
    + "".join(f"  `{c}` {'int(6) unsigned NOT NULL AUTO_INCREMENT' if c == 'id' else 'text'},\n"
              for c in PANEL_DDL_COLUMNS)
    + "  PRIMARY KEY (`id`)\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n")
# INVENTED for tests only: the deployed fork's vs_* DDL is not public.
VS_DDL = {
    "vs_category": ("`id` int NOT NULL AUTO_INCREMENT, `title` varchar(200), `status` varchar(50)", ("id", "title", "status")),
    "vs_package": ("`id` int NOT NULL AUTO_INCREMENT, `category_id` int, `name` varchar(200), `price` varchar(50), "
                   "`api_token` varchar(200)", ("id", "category_id", "name", "price", "api_token")),
    "vs_order": ("`id` int NOT NULL AUTO_INCREMENT, `id_user` varchar(50), `package_id` int, `price` varchar(50), "
                 "`status` varchar(50)", ("id", "id_user", "package_id", "price", "status")),
    "vs_stock": ("`id` int NOT NULL AUTO_INCREMENT, `package_id` int, `content` text, `status` varchar(50)",
                 ("id", "package_id", "content", "status")),
    "vs_setting": ("`id` int NOT NULL AUTO_INCREMENT, `name` varchar(100), `value` text", ("id", "name", "value")),
}

# Planted fake secrets: tests assert none of these strings ever leaves the converter.
SYNTH_SECRETS = (
    "SYNTH-PANEL-PASSWORD-a1b2c3", "synth-panel-admin-x9", "SYNTH-SECRET-CODE-77", "SYNTH-SESSION-TOKEN-44",
    "synth-panel-url.invalid", "synth-sub-domain.invalid", "SYNTH-INBOUND-TAG-5", "SYNTH-MANUAL-CONFIG-SECRET",
    "SYNTH-PANEL-RESPONSE-SECRET", "SYNTH-VS-API-TOKEN-XYZ",
)


def _panel_row(i: int, code: str, name: str, ptype: str) -> str:
    vals = {
        "id": str(i), "code_panel": code, "name_panel": name, "status": "active",
        "url_panel": f"https://{code}.synth-panel-url.invalid:2053", "username_panel": "synth-panel-admin-x9",
        "password_panel": "SYNTH-PANEL-PASSWORD-a1b2c3", "agent": "all", "sublink": "onsublink",
        "config": "offconfig", "MethodUsername": "numericIdRandom", "TestAccount": "ONTestAccount",
        "limit_panel": "unlimted", "namecustom": "vpn", "Methodextend": "resetVolumeTime",
        "conecton": "offconecton", "linksubx": "https://synth-sub-domain.invalid/sub", "inboundid": "SYNTH-INBOUND-TAG-5",
        "type": ptype, "inboundstatus": "offinbounddisable", "inbound_deactive": "SYNTH-INBOUND-TAG-5",
        "time_usertest": "1", "val_usertest": "100", "secret_code": "SYNTH-SECRET-CODE-77", "priceChangeloc": "0",
        "priceextravolume": '{"f":"4000","n":"4000","n2":"4000"}',
        "pricecustomvolume": '{"f":"4000","n":"4000","n2":"4000"}',
        "pricecustomtime": '{"f":"4000","n":"4000","n2":"4000"}',
        "priceextratime": '{"f":"4000","n":"4000","n2":"4000"}', "mainvolume": '{"f":"1","n":"1","n2":"1"}',
        "maxvolume": '{"f":"1000","n":"1000","n2":"1000"}', "maintime": '{"f":"1","n":"1","n2":"1"}',
        "maxtime": '{"f":"365","n":"365","n2":"365"}', "status_extend": "on_extend",
        "datelogin": '{"access_token":"SYNTH-SESSION-TOKEN-44"}',
        "proxies": '{"vless":"SYNTH-INBOUND-TAG-5"}', "inbounds": '{"vless":["SYNTH-INBOUND-TAG-5"]}',
        "subvip": "offsubvip", "changeloc": "offchangeloc", "on_hold_test": "1", "version_panel": "0",
        "customvolume": '{"f":"0","n":"0","n2":"0"}', "hide_user": "[]",
    }
    return "(" + ",".join(sql_str(vals[c]) for c in PANEL_DDL_COLUMNS) + ")"


def _ts(rng, base_day: int = 0) -> str:
    d = 1 + rng.randrange(27)
    m = 1 + rng.randrange(12)
    return f"2025/{m:02d}/{d:02d} {rng.randrange(24):02d}:{rng.randrange(60):02d}:{rng.randrange(60):02d}"


def _emit(out, state: SynthState, table: str, rows: list[str]) -> None:
    out.write(f"DROP TABLE IF EXISTS `{table}`;\n" + DDL[table])
    if rows:
        state.write_rows(out, table, rows)


def synth_tables(out, state: SynthState) -> None:
    rng = state.rng("services_ext")
    spec = state.spec
    scale = spec.module_scale
    # -- marzban_panel: syn-a and syn-b are used by invoices, syn-m is a manual-sale panel, syn-old is unused.
    #    syn-z (used by the anomaly invoices) is deliberately absent → PANEL_NOT_IN_REGISTRY (warning).
    _emit(out, state, "marzban_panel", [
        _panel_row(1, "syn-a", "سرور آزمایشی", "marzban"), _panel_row(2, "syn-b", "سرور دوم", "x-ui_single"),
        _panel_row(3, "syn-m", "فروش دستی", "Manualsale"), _panel_row(4, "syn-old", "سرور قدیمی", "marzneshin"),
    ])
    # -- category: 7 rows; product labels are 'عمومی' and 'ویژه' (core synth). ' ویژه ' matches only normalized.
    labels = ["عمومی", " ویژه ", "اقتصادی", "نامحدود", "گیمینگ", "تک‌کاربره", "VIP"]
    _emit(out, state, "category", [f"({i},{sql_str(v)})" for i, v in enumerate(labels, 1)])
    # -- service_other
    n_inv = len(state.invoice_keys)
    rows: list[str] = []
    types = ["extend_user"] * 5 + ["extra_user"] * 3 + ["extra_time_user"] * 2 + ["change_location", "gift_volume",
                                                                                   "gift_time", "extend_user_by_admin"]
    n_ops = int(len(state.user_ids) * 0.4 * scale) if n_inv else 0
    sid = 0

    def op(owner: str, uname: str, typ: str, price: str | None, status: str | None, value: str) -> None:
        nonlocal sid
        sid += 1
        rows.append(f"({sid},{sql_str(owner)},{sql_str(uname)},{sql_str(value)},{sql_str(_ts(rng))},"
                    f"{sql_str(price)},{sql_str(typ)},{sql_str(status)},"
                    f"{sql_str('{\"subscription_url\":\"https://x.invalid/sub/SYNTH-PANEL-RESPONSE-SECRET\"}')})")

    for _ in range(n_ops):
        i = rng.randrange(n_inv)
        owner, uname = state.invoice_owners[i], state.invoice_usernames[i]
        typ = rng.choice(types)
        if typ.startswith("gift") or typ == "extend_user_by_admin":
            price, status = "0", None
        else:
            price = str(10000 * rng.randrange(1, 30))
            status = ("paid" if rng.random() < 0.9 else "unpaid") if typ == "extend_user" else None
        value = json.dumps({"volumebuy": "30", "Service_time": "30", "oldvolume": 1073741824, "oldtime": 1700000000,
                            "code_product": "p001", "id_order": f"syn{rng.randrange(10**8):08d}"}) \
            if typ.startswith("extend") else json.dumps({"volume_value": 5, "old_volume": 0, "expire_old": 0})
        op(owner, uname, typ, price, status, value)
    if spec.anomalies and n_inv >= 3:
        o0, u0 = state.invoice_owners[0], state.invoice_usernames[0]
        other = next(u for u in state.user_ids if u != o0)
        # ownership moved by a recorded transfer: the earlier op by the previous owner is explained
        op(other, u0, "extend_user", "50000", "paid", "{}")
        op(other, u0, "transfertouser", "0", None, o0)
        # owner differs from the invoice, no transfer → SERVICE_OWNER_CONFLICT (blocker severity, review only)
        o1, u1 = state.invoice_owners[1], state.invoice_usernames[1]
        shared_owners = (str(TG_BASE + 1), str(TG_BASE + 2))  # owners of the core 'shared' anomaly invoices
        stranger = next(u for u in state.user_ids if u not in (o0, o1, other, *shared_owners))
        op(stranger, u1, "extra_user", "20000", None, "{}")
        # several invoices share 'shared' (core anomaly) and this owner matches neither → AMBIGUOUS
        op(stranger, "shared", "extend_user", "10000", "paid", "{}")
        # no invoice with this username
        op(o0, "ghost_user_1", "extra_time_user", "10000", None, "{}")
        # undocumented type and unreadable price
        op(o0, u0, "mystery_op", "12,500", "paid", "not json")
    # -- cancel_service
    cx: list[str] = []
    n_cx = max(3, int(len(state.user_ids) * 0.02 * scale)) if n_inv else 0
    for j in range(n_cx):
        i = rng.randrange(n_inv)
        st = ["waiting", "accept", "reject"][j % 3]
        desc = "0" if st != "reject" else "سرویس هنوز استفاده شده است"
        cx.append(f"({j + 1},{sql_str(state.invoice_owners[i])},{sql_str(state.invoice_usernames[i])},"
                  f"{sql_str(desc)},{sql_str(st)})")
    # -- manualsell (panel syn-m); one sold row whose invoice is on another panel → SERVICE_PANEL_CONFLICT
    ms: list[str] = []
    for j in range(6):
        st = ["active", "active", "selled", "delete", "active", "selled"][j]
        uname = state.invoice_usernames[j] if st == "selled" and j < n_inv else None
        ms.append(f"({j + 1},{sql_str('syn-m')},{sql_str('p001')},{sql_str(f'manual-{j}')},{sql_str(uname)},"
                  f"{sql_str(f'vless://SYNTH-MANUAL-CONFIG-SECRET-{j}@203.0.113.1:443')},{sql_str(st)})")
    _emit(out, state, "service_other", rows)
    _emit(out, state, "cancel_service", cx)
    _emit(out, state, "manualsell", ms)
    # -- vs_* (INVENTED shapes)
    vs_rows = {
        "vs_category": [f"(1,{sql_str('پایه')},'on')", f"(2,{sql_str('حرفه‌ای')},'off')"],
        "vs_package": [f"(1,1,{sql_str('بسته ۱')},'100000',{sql_str('SYNTH-VS-API-TOKEN-XYZ')})"],
        "vs_order": [f"({k + 1},{sql_str(state.user_ids[k % len(state.user_ids)])},1,'100000','done')"
                     for k in range(3)] if state.user_ids else [],
        "vs_stock": [f"(1,1,{sql_str('vmess://SYNTH-VS-STOCK-LINK')},'active')"],
        "vs_setting": [f"(1,'bot_token',{sql_str('123456789:AAEXAMPLEEXAMPLEEXAMPLEEXAMPLE00000')})"],
    }
    for name, (cols, _names) in VS_DDL.items():
        out.write(f"DROP TABLE IF EXISTS `{name}`;\nCREATE TABLE `{name}` ({cols}, PRIMARY KEY (`id`)) "
                  "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n")
        if vs_rows[name]:
            state.write_rows(out, name, vs_rows[name])


register_synth("services_ext", synth_tables, order=300)

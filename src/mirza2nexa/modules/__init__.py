"""Domain converter modules. Importing this package registers every module and table spec.

Every domain module is imported **statically** here. Do not replace this with a dynamic
``__import__``/``importlib`` loop, and never wrap these imports in ``try/except``: a frozen
build (PyInstaller) only bundles modules it can see statically, and a swallowed
``ModuleNotFoundError`` once shipped a Windows EXE that silently processed none of the
Phase-2 tables (v0.2.0). :func:`verify_registry` turns any such gap into a hard failure.
"""

from __future__ import annotations

from ..catalogue import CATALOGUE
from ..errors import MigrationError

# Registration side effects (order matters only for readability; modules declare their own order).
from . import (  # noqa: F401
    adoption,
    agents,
    config_tables,
    core_specs,
    engagement,
    generic,
    ownership,
    payments,
    services_ext,
    support,
    wallet_history,
)
from .base import _MODULES, Module, ModuleContext, modules_in_order, register_module

#: Converter modules that MUST be registered for a conversion to start.
EXPECTED_MODULES: frozenset[str] = frozenset({
    "generic", "payments", "wallet_history", "services_ext", "agents", "engagement", "support", "config_tables",
    "adoption", "ownership",
})

#: Every Mirza table the catalogue MUST know (docs/evidence/mirza_tables.json + ``protocol``).
#: Kept here, in code, so a frozen build can verify itself without any data file.
EXPECTED_TABLES: frozenset[str] = frozenset({
    "Discount", "DiscountSell", "Giftcodeconsumed", "PaySetting", "Payment_report", "Requestagent", "ad_campaign",
    "admin", "affiliates", "agent_invoice", "agent_log", "agent_manage", "agent_usage_snapshot", "ai_business_chat",
    "app", "botsaz", "botsaz_help", "global_hidden_gateways",
    "cancel_service", "card_number", "category", "channels", "custom_service_tier_prices", "departman",
    "discount_sell", "discount_sell_used", "help", "help_category", "invoice", "logs_api", "manualsell",
    "marzban_panel", "product", "protocol", "reagent_report", "requestagent", "service_other", "setting",
    "shopSetting", "support_message", "textbot", "ticket", "ticket_message", "topicid", "user", "vs_category",
    "vs_order", "vs_package", "vs_setting", "vs_stock", "wallet_transaction", "wheel_list",
})

#: Catalogued tables that are not Mirza tables: NEXA's synthetic-dataset marker (core_specs.py).
TOOLING_TABLES: frozenset[str] = frozenset({"nexa_synthetic_fixture"})


class CatalogueIncomplete(MigrationError):
    code = "catalogue.incomplete"
    message_fa = ("کاتالوگ جدول‌های میرزا در این نسخه برنامه کامل بارگذاری نشده است؛ تبدیل انجام نمی‌شود. "
                  "نسخه برنامه را دوباره دریافت و نصب کنید و در صورت تکرار، خروجی دستور selfcheck را گزارش کنید.")


def registry_status() -> dict:
    """Machine-readable state of the module registry and catalogue (used by ``selfcheck``)."""
    missing_modules = sorted(EXPECTED_MODULES - set(_MODULES))
    missing_tables = sorted(t for t in EXPECTED_TABLES if CATALOGUE.lookup(t) is None)
    return {
        "ok": not missing_modules and not missing_tables,
        "modules": sorted(_MODULES),
        "specs": len(CATALOGUE.specs),
        "expected_tables": len(EXPECTED_TABLES),
        "missing_modules": missing_modules,
        "missing_tables": missing_tables,
    }


def verify_registry() -> dict:
    """Raise :class:`CatalogueIncomplete` unless every expected module and table spec is registered."""
    status = registry_status()
    if not status["ok"]:
        raise CatalogueIncomplete(detail=f"missing modules={status['missing_modules']} "
                                         f"missing tables={status['missing_tables']}")
    return status


__all__ = ["EXPECTED_MODULES", "EXPECTED_TABLES", "TOOLING_TABLES", "CatalogueIncomplete", "Module", "ModuleContext",
           "modules_in_order", "register_module", "registry_status", "verify_registry"]

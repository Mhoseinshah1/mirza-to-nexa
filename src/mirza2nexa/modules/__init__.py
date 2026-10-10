"""Domain converter modules. Importing this package registers every module and table spec."""

from . import core_specs, generic  # noqa: F401  (registration side effects)
from .base import Module, ModuleContext, modules_in_order, register_module

# Domain modules register themselves when present. Each lives in its own file so domain
# agents/maintainers never edit a shared file to add one.
for _name in ("payments", "wallet_history", "services_ext", "agents", "engagement", "support", "config_tables"):
    try:
        __import__(f"{__name__}.{_name}")
    except ModuleNotFoundError as exc:  # pragma: no cover - only while a module is absent
        if exc.name != f"{__name__}.{_name}":
            raise

__all__ = ["Module", "ModuleContext", "modules_in_order", "register_module"]

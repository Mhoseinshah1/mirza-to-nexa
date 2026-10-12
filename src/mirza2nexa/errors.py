"""Error taxonomy.

Every user-facing failure carries a stable ``code`` and a Persian message that contains
no customer data, no passwords and no file contents. ``detail`` is for developers and must
also be free of sensitive data (it is shown in the local UI only, never sent anywhere).
"""

from __future__ import annotations


class MigrationError(Exception):
    """Base class for all expected, user-explainable failures."""

    code: str = "internal"
    message_fa: str = "خطای داخلی برنامه رخ داد."

    def __init__(self, message_fa: str | None = None, *, code: str | None = None, detail: str | None = None):
        if message_fa is not None:
            self.message_fa = message_fa
        if code is not None:
            self.code = code
        self.detail = detail
        super().__init__(f"[{self.code}] {detail or self.message_fa}")

    def to_dict(self) -> dict[str, str | None]:
        return {"code": self.code, "message": self.message_fa, "detail": self.detail}


class ArchiveError(MigrationError):
    code = "archive.invalid"
    message_fa = "فایل ZIP معتبر نیست."


class PasswordRequired(ArchiveError):
    code = "archive.password_required"
    message_fa = "این فایل ZIP رمزدار است؛ لطفاً رمز بکاپ را وارد کنید."


class WrongPassword(ArchiveError):
    code = "archive.wrong_password"
    message_fa = "رمز وارد‌شده برای فایل ZIP صحیح نیست."


class UnsafeArchive(ArchiveError):
    code = "archive.unsafe"
    message_fa = "فایل ZIP شامل محتوای ناامن است و پردازش نمی‌شود."


class LimitExceeded(ArchiveError):
    code = "archive.limit_exceeded"
    message_fa = "فایل ZIP از محدودیت‌های امنیتی حجم یا تعداد فایل عبور کرده است."


class DiskSpaceError(MigrationError):
    code = "disk.insufficient"
    message_fa = "فضای خالی دیسک برای پردازش کافی نیست."


class SqlParseError(MigrationError):
    code = "sql.parse"
    message_fa = "ساختار فایل SQL قابل خواندن نیست یا خراب است."


class SchemaUnknown(MigrationError):
    code = "schema.unknown"
    message_fa = "نسخه ساختار دیتابیس میرزا شناخته‌شده نیست؛ تبدیل مسدود شد."


class PackageError(MigrationError):
    code = "package.invalid"
    message_fa = "بسته NEXA نامعتبر است."


class PackageTampered(PackageError):
    code = "package.tampered"
    message_fa = "بسته NEXA دستکاری شده یا کلید آن صحیح نیست."


class Cancelled(MigrationError):
    code = "job.cancelled"
    message_fa = "عملیات توسط کاربر لغو شد."

# طرح اجرایی Mirza → NEXA Migration Studio

## تصمیم‌های تأییدشده
- ورودی: فایل ZIP حاوی SQL بکاپ Mirza؛ ZIP ممکن است رمزدار باشد.
- اجرا: برنامه تحت وب **محلی** روی کامپیوتر ویندوز، دسترسی از `http://127.0.0.1:<port>` و نه اینترنت.
- خروجی: یک فایل بسته انتقال رمزگذاری‌شده نسخه‌دار `.nxpkg`.
- ورود نهایی: توسط Importer جدا در پنل NEXA، با پیش‌نمایش، Dry Run، تطبیق و تأیید مدیر.
- تا زمان جایگزین کامل، هیچ صفحه/کد Legacy در NEXA حذف نشود؛ داده‌ها و تاریخچه مالی هرگز برای تمیزکردن ظاهر پنل پاک نشوند.
- Production و Mirza موجود بدون تغییر.

## جریان اطلاعات
`ZIP + password -> archive guard -> isolated SQL parser -> source schema detection -> mapping -> validation / reconciliation -> encrypted package + signed manifest / trusted provenance -> download -> NEXA importer verify -> dry-run -> explicit approval -> import`.

## دامنه تبدیل و محدودیت
| بخش | خروجی | شرط |
| --- | --- | --- |
| مشتریان | ID اصلی و Telegram ID | uniqueness + provenance |
| کیف پول | مانده افتتاحیه | units, ledger neutrality, source watermark |
| بدهی‌ها | مانده و وضعیت بدهی | عدم تسویه یا حذف خودکار |
| محصول / دسته‌بندی | نگاشت نسخه‌دار | بلاک نگاشت‌های نامطمئن |
| فاکتور تاریخی | آرشیو و مرجع قدیمی | عدم credit مجدد |
| سرویس | ثبت سرویس موجود با مالک و پنل | عدم Provision مجدد؛ بررسی live بعداً |
| نمایندگان | گزارش نگاشت | فقط با قرارداد نقش و قیمت تأییدشده |

## فرمت پیشنهادی بسته
- `manifest.json`: schemaVersion، converterVersion، sourceFingerprint، sourceSnapshotAt، importId، فهرست رکوردها و نسخه target
- `records/*.jsonl`: رکوردهای نسخه‌دار با provenance
- `reports/reconciliation.json`: شمارش‌ها، مبالغ، مغایرت‌ها و مقادیر reject
- `reports/review-queue.jsonl`: رکوردهای قابل حل صرفاً توسط اپراتور
- `checksums.json`: هش فایل‌ها در بسته، درون envelope احراز اصالت‌شده
- کل بسته داخل envelope رمزگذاری معتبر قرار گیرد؛ کلید جدا از فایل تحویل داده شود

این تنها **قرارداد پیشنهادی** است؛ تا زمانی که NEXA importer آن را به‌صورت نسخه‌دار و با تست مصرف نکرده، فایل را «قابل Import در Production» معرفی نکنید.

## امنیت
- جلوگیری از zip-slip، symbolic link و zip-bomb؛ حد مجاز حجم و تعداد entries؛ حذف امن فایل‌های موقت.
- جلوگیری از ارسال ZIP/SQL یا رمز در telemetry، exceptions و سرور خارجی.
- مدیریت ZIPهای رمزدار با کتابخانه‌ای که روش رمزگذاری فایل واقعی را پشتیبانی می‌کند؛ پشتیبانی AES و ZipCrypto باید صریحاً تست شود.
- binding تنها به loopback؛ کنترل Host / Origin و CSRF روی API محلی؛ بدون ذخیره رمز.
- گزارش‌های خطا فاقد داده شناسایی مشتری.
- روی داده مالی: بدون حدس درباره ریال/تومان، بدهی منفی، Gift balance و wallet ledger.

## برنامه فازها
A. استخراج schema و تدوین نگاشت با شواهد؛ B. intake امن ZIP؛ C. parser جریانی؛ D. mapping و reconciliation؛ E. رابط فارسی محلی؛ F. package exporter؛ G. NEXA importer قراردادمحور (مستقل و نیازمند اجازه)؛ H. staging tests؛ I. بازنشستگی کنترل‌شده UIهای legacy پس از تصویب.

## آزمون‌های پذیرش
- ZIP فاقد رمز، AES password ZIP، رمز اشتباه، ZIP ناسالم، zip-slip، zip-bomb، چند SQL، encoding ناسازگار.
- نمونه مشتری و کیف پول یکسان دو بار -> عدم تغییر مجدد موجودی.
- نسخه دوم بکاپ -> اختلاف مانده بر مبنای source watermark و رخدادها؛ هر عدم قطعیت Block.
- سرویس بدون مالک، سرویس بدون پنل، فاکتور بدون customer -> صف review و عدم auto adoption.
- پردازش 200k مشتری و حداقل 80k سرویس با محدودیت حافظه و بدون ارسال داده بیرونی.
- قطع عملیات، تلاش مجدد، corruption package، schema version ناشناخته و tampered manifest -> fail closed.
- عدم تغییر یا تماس با Production در کلیه تست‌های محلی.

## گزارش‌های مورد نیاز
Mapping Report، Missing Fields، Reconciliation Summary، Review Queue، Import Preview، Repeat-run Diff، Test Report.

## وضعیت پیاده‌سازی (شاخه فعلی)
- پیاده‌سازی‌شده و تست‌شده با داده مصنوعی: دریافت امن ZIP (AES/ZipCrypto)، Parser جریانی غیراجرایی، تشخیص پروفایل ساختار، تبدیل مشتریان/کیف پول/بدهی/محصولات/دسته‌ها/فاکتورها/سرویس‌ها/پنل‌ها، صف بررسی، تطبیق مالی دوگانه، مقایسه با بکاپ قبلی، بسته رمزگذاری‌شده `.nxpkg`، رابط فارسی محلی، CLI، ساخت ویندوز در CI.
- نگاشت‌ها فقط بر اساس قواعد موجود در NEXA (`docs/MAPPING.md` و `docs/NEXA_EVIDENCE.md`)؛ جداول طبقه‌بندی‌نشده (پنل‌ها، Payment_report، تخفیف، …) و نمایندگی مسدود هستند.
- واحد پول: تصمیم مالک NEXA (تومان → IRT) فقط با تأیید اپراتور اعمال می‌شود و هیچ مبلغی بازمقیاس نمی‌شود.
- خروجی «فایل بکاپ NEXA برای بازیابی از میرزا» است و در NEXA از «بکاپ و بازیابی ← عملیات بازیابی» بازیابی می‌شود؛ این مسیر در نسخه‌ای از NEXA است که هنوز منتشر نشده (nexa-bot PR #278)، پس فعلاً فقط روی نصب آزمایشی و نه Production.

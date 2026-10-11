"""Downloadable Persian "migration readiness" report (``GET /api/jobs/{id}/readiness.html``).

Built only from ``migration-readiness.json`` (aggregates: counts, sums, codes, table names). The
page is self-contained RTL HTML with **inline CSS only and no scripts**; every interpolated value
is HTML-escaped. Missing or malformed sections render as "—" instead of failing, because the
readiness document may come from an older or newer converter.

Nothing here adds record-level data: no Telegram ids, usernames, codes or per-person amounts.
"""

from __future__ import annotations

from typing import Any

from .catalogue import CLASS_FA
from .report_fa import (
    _CSS,
    BLOCKER_CATEGORY_FA,
    BLOCKER_CODE_FA,
    CONFIRMED_BY_FA,
    COVERAGE_STATUS_FA,
    HISTORY_CAUSE_FA,
    IMPORT_BLOCKER_FA,
    MONEY_UNIT_FA,
    NEXA_RESTORE_NOTE_FA,
    OWNERSHIP_DECISION_FA,
    PACKAGE_READINESS_FA,
    PANEL_STATUS_FA,
    READINESS_DISCLAIMER_FA,
    READINESS_KEY_FA,
    READINESS_VERDICT_FA,
    REVIEW_GROUP_FA,
    _e,
    _num,
    _value_html,
    blocker_category,
    humanise,
    readiness_label,
)

_EXTRA_CSS = """
.verdict{font-size:18px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:12px}
section.card{padding:12px 16px}section.card h2{margin-top:0}.num{font-variant-numeric:tabular-nums}
"""


def _d(v: Any) -> dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _l(v: Any) -> list[dict[str, Any]]:
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def _k(key: str) -> str:
    return f'{_e(READINESS_KEY_FA.get(key) or humanise(key))} <span class="code">{_e(key)}</span>'


def _yes_no(v: Any) -> str:
    if v is True:
        return '<span class="badge b-executed">بله</span>'
    if v is False:
        return '<span class="badge b-blocked">خیر</span>'
    return "—"


def _money(amount: Any, currency: Any) -> str:
    if amount is None:
        return "—"
    unit = MONEY_UNIT_FA.get(str(currency), str(currency) if currency else "")
    return f'<span class="num">{_e(_num(amount))}</span> {_e(unit)}'.strip()


def _dl(rows: list[tuple[str, str | None]]) -> str:
    """``rows`` hold (key, already-escaped HTML); ``None`` values are skipped."""
    body = "".join(f"<dt>{_k(k)}</dt><dd>{v}</dd>" for k, v in rows if v is not None)
    return f"<dl>{body}</dl>" if body else '<p class="muted">اطلاعاتی موجود نیست.</p>'


def _count_table(counts: Any, labels: dict[str, str], head: str) -> str:
    c = _d(counts)
    if not c:
        return '<p class="muted">—</p>'
    rows = "".join(
        f"<tr><td>{_e(readiness_label(labels, k))} <span class=\"code\">{_e(k)}</span></td>"
        f"<td class=\"num\">{_e(_num(v))}</td></tr>" for k, v in c.items())
    return f"<table><tr><th>{_e(head)}</th><th>تعداد</th></tr>{rows}</table>"


def _n(v: Any) -> str | None:
    return None if v is None else f'<span class="num">{_e(_num(v))}</span>'


def _coded_list(items: list[dict[str, Any]], *, empty: str, labels: dict[str, str] | None = None) -> str:
    if not items:
        return f'<p class="muted">{_e(empty)}</p>'
    out = []
    for it in items:
        code = str(it.get("code") or "")
        title = (labels or {}).get(code) or ""
        msg = it.get("message_fa") or ""
        count = f' ({_e(_num(it.get("count")))} مورد)' if it.get("count") is not None else ""
        head = f"<strong>{_e(title)}</strong> — " if title and title != msg else ""
        out.append(f'<li>{head}{_e(msg or humanise(code))}{count} <span class="code">{_e(code)}</span></li>')
    return "<ul>" + "".join(out) + "</ul>"


def render_readiness_html(readiness: dict[str, Any]) -> str:
    """Self-contained Persian RTL readiness report. No scripts; every value is escaped."""
    r = _d(readiness)
    verdict = _d(r.get("verdict"))
    vcode = str(verdict.get("code") or "")
    vlabel = verdict.get("label_fa") or READINESS_VERDICT_FA.get(vcode) or humanise(vcode) or "—"
    vclass = {"READY_FOR_IMPORTER_DRY_RUN": "warn"}.get(vcode, "bad")
    parts: list[str] = []
    a = parts.append
    a('<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8">'
      '<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="referrer" content="no-referrer">'
      f"<title>آمادگی مهاجرت به NEXA — {_e(str(r.get('import_id') or '')[:12])}</title>"
      f"<style>{_CSS}{_EXTRA_CSS}</style></head><body><main>")
    a("<h1>آمادگی مهاجرت به NEXA</h1>")
    a('<p class="muted small">این گزارش فقط آمار کلی را دارد و هیچ اطلاعات شخصی، شناسه مشتری، نام کاربری، کد، '
      "رمز یا کلید در آن نیست.</p>")
    a(f'<div class="box {vclass} verdict" role="status"><strong>نتیجه: {_e(vlabel)}</strong> '
      f'<span class="code">{_e(vcode)}</span>')
    reasons = _l(verdict.get("reasons"))
    if reasons:
        a("<ul>" + "".join(f'<li>{_e(x.get("message_fa") or humanise(x.get("code") or ""))} '
                           f'<span class="code">{_e(x.get("code"))}</span></li>' for x in reasons) + "</ul>")
    a("</div>")
    a(f'<div class="box warn" role="note"><strong>مهم:</strong> {_e(READINESS_DISCLAIMER_FA)}</div>')

    a("<h2>مشخصات</h2>")
    a(_dl([(k, f'<span class="code">{_e(r.get(k))}</span>' if r.get(k) is not None else None)
           for k in ("import_id", "generated_at", "converter_version", "package_contract_version",
                     "readiness_version")]))

    a('<div class="grid">')
    # package health
    ph = _d(r.get("package_health"))
    a('<section class="card"><h2>سلامت بسته</h2>')
    prd = ph.get("readiness")
    a(_dl([("readiness", _e(PACKAGE_READINESS_FA.get(str(prd), str(prd))) if prd is not None else None),
           ("catalogue_ok", _yes_no(ph.get("catalogue_ok")) if "catalogue_ok" in ph else None)]))
    pblock = _l(ph.get("blockers"))
    a(f"<h3>موارد مسدودکننده بسته ({_e(_num(len(pblock)))})</h3>")
    a(_coded_list(pblock, empty="موردی وجود ندارد.", labels=BLOCKER_CODE_FA))
    if pblock:
        cats: dict[str, int] = {}
        for b in pblock:
            cat = blocker_category(str(b.get("code")))
            cats[cat] = cats.get(cat, 0) + 1
        a('<p class="small muted">' + " · ".join(f"{_e(BLOCKER_CATEGORY_FA.get(c, c))}: {_e(_num(n))}" for c, n in sorted(cats.items())) + "</p>")
    a("</section>")

    # financial
    fin = _d(r.get("financial"))
    pos = _d(fin.get("positive_balances"))
    mu = _d(fin.get("money_unit"))
    hd = _d(fin.get("history_differences"))
    a('<section class="card"><h2>تطبیق مالی</h2>')
    unit_html = None
    if mu:
        uv = mu.get("value")
        unit_html = (_e(MONEY_UNIT_FA.get(str(uv), str(uv))) if uv else "تعیین‌نشده") + " — "
        cb = mu.get("confirmed_by")
        unit_html += _e("تأییدشده توسط " + CONFIRMED_BY_FA.get(str(cb), str(cb))) if cb else "تأیید نشده"
    a(_dl([
        ("balanced", _yes_no(fin.get("balanced")) if "balanced" in fin else None),
        ("source_recount_matches", _yes_no(fin.get("source_recount_matches")) if "source_recount_matches" in fin else None),
        ("money_unit", unit_html),
        ("positive_balances", (f'{_e(_num(pos.get("count")))} حساب — {_money(pos.get("amount_minor"), pos.get("currency"))}'
                               if pos else None)),
        ("net_minor", _money(fin.get("net_minor"), pos.get("currency")) if fin.get("net_minor") is not None else None),
        ("independent_recount_matches", _yes_no(fin.get("independent_recount_matches"))
         if "independent_recount_matches" in fin else None),
        ("payments_unknown_status", _n(fin.get("payments_unknown_status"))),
        ("cases_needing_review", _n(fin.get("cases_needing_review"))),
    ]))
    debts = fin.get("debts")
    if isinstance(debts, dict) and ("count" in debts or "amount_minor" in debts):
        a(_dl([("debts", f'{_e(_num(debts.get("count")))} حساب — {_money(debts.get("amount_minor"), debts.get("currency"))}')]))
    elif isinstance(debts, dict) and debts:
        a(f"<h3>{_k('debts')}</h3>{_value_html(debts)}")
    if hd:
        a(f"<h3>{_k('history_differences')}</h3>")
        a(_dl([(k, _n(hd.get(k))) for k in ("total", "proven", "probable", "unexplained")]))
        a(_count_table(hd.get("by_cause"), HISTORY_CAUSE_FA, "علت"))
    a("</section>")

    # coverage
    cov = _d(r.get("coverage"))
    a('<section class="card"><h2>پوشش جدول‌ها</h2>')
    a(_dl([(k, _n(cov.get(k))) for k in ("tables_total", "non_empty")]))
    a(_count_table(cov.get("by_status"), COVERAGE_STATUS_FA, "وضعیت"))
    a(_count_table(cov.get("by_class"), dict(CLASS_FA), "طبقه"))
    unc = [str(t) for t in cov.get("uncovered") or [] if isinstance(t, (str, int))] \
        if isinstance(cov.get("uncovered"), list) else []
    a(f"<h3>{_k('uncovered')} ({_e(_num(len(unc)))})</h3>")
    a("<ul>" + "".join(f'<li class="ltr">{_e(t)}</li>' for t in unc) + "</ul>" if unc
      else '<p class="muted">همه جدول‌ها پوشش داده شده‌اند.</p>')
    a("</section>")

    # ownership
    own = _d(r.get("ownership"))
    a('<section class="card"><h2>مالکیت سرویس‌ها</h2>')
    a(_dl([(k, _n(own.get(k))) for k in ("total", "needs_manual_review", "confirmed_transfers")]))
    if own.get("available") is False:
        a('<p class="small muted">تحلیل مالکیت سرویس‌ها در این بکاپ در دسترس نبود.</p>')
    a(_count_table(own.get("by_decision"), OWNERSHIP_DECISION_FA, "تصمیم"))
    ar = _d(own.get("admin_review"))
    if ar:
        a("<h3>بررسی مدیر (جدا از مالکیت اثبات‌شده)</h3>")
        rows = [("proven", "مالکیت اثبات‌شده"), ("admin_approved_unverified", "تأیید مدیر (اثبات نشده)"),
                ("pending", "در انتظار تصمیم"), ("rejected", "رد شده — قرنطینه"), ("quarantined", "قرنطینه سیستمی"),
                ("stale", "تصمیم‌های منسوخ")]
        body = "".join(f'<dt>{_e(fa)} <span class="code">{_e(k)}</span></dt><dd>{_n(ar.get(k)) or "—"}</dd>'
                       for k, fa in rows)
        a(f'<dl>{body}<dt>قفل برای Import نهایی <span class="code">sealed</span></dt>'
          f'<dd>{_yes_no(ar.get("sealed"))}</dd></dl>')
        a('<p class="small muted">«تأیید مدیر» پذیرش مالک ثبت‌شده در فاکتور میرزا با تصمیم صریح مدیر است و '
          "اثبات مالکیت محسوب نمی‌شود.</p>")
    a("</section>")

    # panels
    pn = _d(r.get("panels"))
    a('<section class="card"><h2>نگاشت پنل‌ها</h2>')
    pst = pn.get("status")
    a(_dl([(k, _n(pn.get(k))) for k in ("registry_rows", "codes_in_invoices", "mapped", "pending_mapping")]
          + [("status", (_e(PANEL_STATUS_FA.get(str(pst)) or humanise(str(pst)))
                         + f' <span class="code">{_e(pst)}</span>') if pst is not None else None)]))
    a('<p class="small muted">مبدل هرگز به پنل‌ها وصل نمی‌شود؛ بررسی زنده اختیاری فقط وضعیت سرویس را می‌خواند و هیچ سرویسی را ایجاد، تمدید یا حذف نمی‌کند.</p>')
    a("</section>")
    a("</div>")

    # review
    rv = dict(_d(r.get("review")))
    if rv.get("owner_decisions_needed") is None:
        rv["owner_decisions_needed"] = r.get("owner_decisions_needed")
    a("<h2>موارد بررسی</h2>")
    a('<div class="cards">' + "".join(
        f'<div class="card">{_e(READINESS_KEY_FA.get(k, k))}<b class="num">{_e(_num(rv.get(k)))}</b></div>'
        for k in ("total_items", "distinct_records", "blocker_items", "auto_resolved_with_evidence", "resolvable_by_code",
                  "archive_only", "owner_decision_items", "owner_decisions_needed", "adoption_stage")
        if rv.get(k) is not None and not isinstance(rv.get(k), (dict, list))) + "</div>")
    groups = _l(rv.get("by_group"))
    if groups:
        cols = ("items", "records", "resolvable_by_code", "archive_only", "owner_decision", "adoption_time")
        a("<table><tr><th>گروه</th>" + "".join(f"<th>{_e(READINESS_KEY_FA[c])}</th>" for c in cols) + "</tr>")
        for g in groups:
            gc = str(g.get("group") or "")
            a(f"<tr><td>{_e(REVIEW_GROUP_FA.get(gc) or g.get('label_fa') or humanise(gc))} "
              f'<span class="code">{_e(gc)}</span></td>'
              + "".join(f'<td class="num">{_e(_num(g.get(c)))}</td>' for c in cols) + "</tr>")
        a("</table>")
    else:
        a('<p class="muted">گروهی ثبت نشده است.</p>')

    # owner decisions
    od = _l(r.get("owner_decisions"))
    a(f"<h2>تصمیم‌های لازم مالک ({_e(_num(len(od)))})</h2>")
    if od:
        a("<ul>")
        for d in od:
            tag = '<span class="badge b-blocked">مسدودکننده</span> ' if d.get("blocking") is True else ""
            a(f"<li>{tag}<strong>{_e(d.get('title_fa') or '—')}</strong>"
              + (f" — {_e(d.get('detail_fa'))}" if d.get("detail_fa") else "")
              + f' <span class="code">{_e(d.get("id"))}</span></li>')
        a("</ul>")
    else:
        a('<p class="muted">تصمیمی ثبت نشده است.</p>')

    # import blockers
    ib = _l(r.get("import_blockers"))
    a(f"<h2>موانع بازیابی ({_e(_num(len(ib)))})</h2>")
    a(_coded_list(ib, empty="مانعی ثبت نشده است.", labels=IMPORT_BLOCKER_FA))

    a(f'<div class="box warn">{_e(NEXA_RESTORE_NOTE_FA)}</div>')
    a("</main></body></html>")
    return "".join(parts)

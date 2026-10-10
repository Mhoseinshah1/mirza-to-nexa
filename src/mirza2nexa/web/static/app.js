/* Mirza → NEXA Migration Studio — UI.
 *
 * Security notes:
 *  - All server/SQL-derived data is rendered with textContent / setAttribute only. Never innerHTML.
 *  - Secrets (ZIP password, package passphrase) are read from inputs at submit time, cleared from
 *    the DOM immediately and never stored in sessionStorage/localStorage.
 *  - Every non-GET request carries X-CSRF-Token.
 */
(function () {
  'use strict';

  // ------------------------------------------------------------------ helpers
  const $ = (id) => document.getElementById(id);
  const NF = new Intl.NumberFormat('fa-IR');
  const NF1 = new Intl.NumberFormat('fa-IR', { maximumFractionDigits: 1 });
  let DTF;
  try {
    DTF = new Intl.DateTimeFormat('fa-IR', { dateStyle: 'medium', timeStyle: 'short' });
  } catch (_) {
    DTF = new Intl.DateTimeFormat('fa-IR');
  }

  const FA_DIGITS = '۰۱۲۳۴۵۶۷۸۹';
  function faDigits(s) {
    return String(s).replace(/[0-9]/g, (d) => FA_DIGITS[+d]);
  }
  function latinDigits(s) {
    return String(s)
      .replace(/[۰-۹]/g, (d) => String(d.charCodeAt(0) - 0x06f0))
      .replace(/[٠-٩]/g, (d) => String(d.charCodeAt(0) - 0x0660))
      .replace(/[٬,]/g, '')
      .trim();
  }
  function fmtNum(v) {
    if (v === null || v === undefined || v === '') return '—';
    if (typeof v === 'number' && Number.isFinite(v)) return v < 0 ? '\u2066' + NF.format(v) + '\u2069' : NF.format(v);
    if (typeof v === 'string' && /^-?\d+(\.\d+)?$/.test(v)) {
      // Large decimal strings (money) — keep exact, only localize digits and group.
      const neg = v.startsWith('-');
      const [ip, fp] = v.replace('-', '').split('.');
      const grouped = ip.replace(/\B(?=(\d{3})+(?!\d))/g, '٬');
      const body = faDigits(grouped) + (fp ? '٫' + faDigits(fp) : '');
      // Isolate negatives as LTR so the sign stays on the left inside RTL text.
      return neg ? '\u2066−' + body + '\u2069' : body;
    }
    if (typeof v === 'boolean') return v ? 'بله' : 'خیر';
    if (typeof v === 'object') return JSON.stringify(v);
    return String(v);
  }
  function fmtPct(p) {
    const n = Number(p);
    return NF1.format(Number.isFinite(n) ? n : 0) + '٪';
  }
  function fmtBytes(n) {
    if (n === null || n === undefined || !Number.isFinite(Number(n))) return '—';
    n = Number(n);
    const units = ['بایت', 'کیلوبایت', 'مگابایت', 'گیگابایت', 'ترابایت'];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) {
      n /= 1024;
      i++;
    }
    return NF1.format(i === 0 ? n : Math.round(n * 10) / 10) + ' ' + units[i];
  }
  function fmtDate(epochSec) {
    if (!epochSec) return '—';
    try {
      return DTF.format(new Date(Number(epochSec) * 1000));
    } catch (_) {
      return '—';
    }
  }
  function fmtDuration(sec) {
    if (!Number.isFinite(sec) || sec < 0) return '—';
    sec = Math.floor(sec);
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = sec % 60;
    const pad = (x) => faDigits(String(x).padStart(2, '0'));
    return (h ? faDigits(h) + ':' : '') + pad(m) + ':' + pad(s);
  }
  function truncate(s, n) {
    s = String(s);
    return s.length > n ? s.slice(0, n - 1) + '…' : s;
  }
  function stringify(v) {
    if (v === null || v === undefined) return '';
    if (typeof v === 'object') {
      try {
        return JSON.stringify(v);
      } catch (_) {
        return '[object]';
      }
    }
    return String(v);
  }

  /** Create an element. Text is always set via textContent / text nodes. */
  function el(tag, props, ...children) {
    const n = document.createElement(tag);
    if (props) {
      for (const k of Object.keys(props)) {
        const v = props[k];
        if (v === null || v === undefined || v === false) continue;
        if (k === 'class') n.className = v;
        else if (k === 'text') n.textContent = String(v);
        else if (k === 'dataset') Object.assign(n.dataset, v);
        else n.setAttribute(k, v === true ? '' : String(v));
      }
    }
    for (const c of children.flat()) {
      if (c === null || c === undefined || c === false) continue;
      n.append(c instanceof Node ? c : String(c));
    }
    return n;
  }
  const clear = (node) => node.replaceChildren();

  const store = {
    get(k) {
      try {
        return sessionStorage.getItem(k);
      } catch (_) {
        return null;
      }
    },
    set(k, v) {
      try {
        if (v === null || v === undefined) sessionStorage.removeItem(k);
        else sessionStorage.setItem(k, v);
      } catch (_) {
        /* ignore */
      }
    },
  };

  // ------------------------------------------------------------------ labels
  const STATUS_FA = {
    uploaded: 'بارگذاری‌شده',
    awaiting_password: 'در انتظار رمز',
    queued: 'در صف اجرا',
    running: 'در حال اجرا',
    completed: 'تکمیل‌شده',
    failed: 'ناموفق',
    cancelled: 'لغوشده',
  };
  const STAGE_FA = {
    inspect: 'بررسی بایگانی',
    extract: 'استخراج امن',
    schema: 'تشخیص نسخه ساختار',
    convert: 'تبدیل اطلاعات',
    reconcile: 'تطبیق مالی',
    package: 'ساخت بسته',
  };
  const ENTITY_FA = {
    customers: 'مشتریان',
    wallet_balances: 'موجودی کیف پول',
    debts: 'بدهی‌ها',
    products: 'محصولات',
    categories: 'دسته‌بندی‌ها',
    services: 'سرویس‌ها',
    orders: 'سفارش‌ها',
    invoices: 'فاکتورها',
    resellers: 'نمایندگان',
    panels: 'پنل‌ها',
    payments: 'پرداخت‌ها',
  };
  const MAPPING_FA = { mapped: 'نگاشت‌شده', blocked: 'مسدود', absent: 'در بکاپ نیست', partial: 'ناقص' };
  const SEVERITY_FA = { blocker: 'مسدودکننده', warning: 'هشدار' };
  const KIND_FA = { sql: 'SQL — پردازش می‌شود', skipped: 'نادیده گرفته می‌شود' };
  const UNIT_FA = { toman: 'تومان', rial: 'ریال' };
  const UNIT_SOURCE_FA = { operator: 'انتخاب اپراتور', evidence: 'شواهد داخل بکاپ' };
  const TERMINAL = new Set(['completed', 'failed', 'cancelled']);
  const ACTIVE = new Set(['queued', 'running']);
  const PRESTART = new Set(['uploaded', 'awaiting_password']);
  // Persian labels shared with the downloadable report; loaded from /api/labels at boot.
  // These defaults keep the UI usable (and Persian) if that request fails.
  const L = {
    coverage_status: { executed: 'اجرا شد', archived: 'آرشیو شد', review: 'نیازمند بررسی', blocked: 'مسدود', empty: 'بدون داده', excluded: 'غیرقابل انتقال' },
    coverage_status_help: {},
    classes: {},
    readiness: { ready: 'آماده بررسی Importer', blocked: 'مسدود' },
    entities: {},
    keys: {},
    key_help: {},
    review_codes: {},
    review_kinds: {},
    blocker_codes: {},
    blocker_categories: { financial: 'مالی', schema: 'ساختار داده (Schema)', data_quality: 'کیفیت داده', security: 'امنیت' },
    blocker_category: {},
    entity_block_reason: {},
    mapping_status: {},
    readiness_verdicts: {
      NOT_READY_FOR_IMPORT: 'آماده Import نیست',
      READY_FOR_IMPORTER_DRY_RUN: 'آماده اجرای آزمایشی Importer (نه Import واقعی)',
      BLOCKED: 'مسدود',
    },
    readiness_keys: {},
    ownership_decisions: {},
    history_causes: {},
    review_groups: {},
    panel_status: { PENDING_OWNER_MAPPING: 'در انتظار نگاشت توسط مالک' },
    package_readiness: { ready: 'آماده', blocked: 'مسدود' },
    money_units: { toman: 'تومان', rial: 'ریال', IRT: 'تومان', IRR: 'ریال' },
    confirmed_by: { operator: 'اپراتور', evidence: 'شواهد داخل بکاپ' },
    import_blockers: {},
    ownership_review: {
      final_class: {
        PROVEN: 'مالکیت اثبات‌شده',
        ADMIN_ATTESTED: 'تأیید مدیر (اثبات نشده)',
        PENDING: 'در انتظار تصمیم',
        REJECTED: 'رد شده — قرنطینه',
        QUARANTINED: 'قرنطینه سیستمی',
      },
      review_state: { PENDING: 'در انتظار', APPROVED: 'تأییدشده توسط مدیر', REJECTED: 'ردشده توسط مدیر' },
      eligibility: { PROVEN: 'اثبات‌شده', ATTESTABLE: 'قابل تأیید مدیر', QUARANTINE_ONLY: 'فقط قرنطینه' },
      action: {
        APPROVE: 'تأیید مالک ثبت‌شده در فاکتور',
        REJECT: 'رد و انتقال به قرنطینه',
        RESET: 'بازگشت به در انتظار',
        UNDO: 'بازگرداندن دسته',
      },
      live_status: { NOT_CHECKED: 'بررسی نشده' },
      risk: {},
      reason: {},
      status_class: {},
    },
  };
  const own = (o, k) => (o && Object.prototype.hasOwnProperty.call(o, k) ? o[k] : undefined);
  function humanise(k) {
    const s = String(k).replace(/[_.]+/g, ' ').trim();
    return s || String(k);
  }
  const entityLabel = (k) => ENTITY_FA[k] || own(L.entities, k) || own(L.keys, k) || humanise(k);
  const keyLabel = (k) => own(L.keys, k) || own(L.entities, k) || ENTITY_FA[k] || humanise(k);
  const classLabel = (c, fa) => fa || own(L.classes, c) || (c ? humanise(c) : 'طبقه‌بندی‌نشده');
  const covStatusLabel = (s) => own(L.coverage_status, s) || humanise(s || '—');
  function blockerCategory(code) {
    const c = String(code || '');
    const known = own(L.blocker_category, c);
    if (known) return known;
    const up = c.toUpperCase();
    if (/SECRET|SECUR|TOKEN|CREDENTIAL|PASSWORD|PRIVATE/.test(up)) return 'security';
    if (/MONEY|FINANC|BALANCE|WALLET|PAYMENT|LEDGER|RECONCIL|RECOUNT|AMOUNT|DEBT|CURRENCY/.test(up)) return 'financial';
    if (/SCHEMA|TABLE|COLUMN|UNCLASSIFIED|VERSION/.test(up)) return 'schema';
    return 'data_quality';
  }
  /** Persian label with the technical code shown small next to it (and an optional explanation). */
  function labelWithCode(label, code, help) {
    return el('span', { class: 'lbl' },
      el('span', { class: 'lbl-text', text: label }),
      code !== undefined && code !== null && String(code) !== label ? el('span', { class: 'key-code', text: String(code) }) : null,
      help ? el('span', { class: 'key-help', text: help }) : null);
  }
  const statusLabel = (s) => STATUS_FA[s] || s || '—';

  // ------------------------------------------------------------------ state
  const S = {
    csrf: null,
    version: null,
    view: 'dashboard',
    jobId: store.get('m2n.job'),
    job: null,
    jobs: [],
    settings: null,
    report: null,
    reportJobId: null,
    wizardStep: 1,
    file: null,
    xhr: null,
    entriesPage: 0,
    reviewOffset: 0,
    historyItems: [],
    historyPage: 0,
    poll: { timer: null, delay: 1000, inflight: false },
    dashTimer: null,
    lastAnnounce: '',
    fatal: false,
  };

  // ------------------------------------------------------------------ API
  class ApiError extends Error {
    constructor(status, code, message, detail, network) {
      super(message);
      this.status = status;
      this.code = code;
      this.detail = detail || null;
      this.network = !!network;
    }
  }
  const NETWORK_MSG = 'ارتباط با برنامه برقرار نشد. مطمئن شوید پنجره برنامه هنوز باز است.';

  function errorFromPayload(status, data) {
    const err = (data && data.error) || {};
    return new ApiError(status, err.code || 'http.' + status, err.message || 'درخواست با خطا مواجه شد.', err.detail);
  }

  async function api(method, path, body) {
    const headers = { Accept: 'application/json' };
    if (method !== 'GET' && method !== 'HEAD' && S.csrf) headers['X-CSRF-Token'] = S.csrf;
    const init = { method, headers, credentials: 'same-origin', cache: 'no-store' };
    if (body !== undefined) {
      headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
    }
    let res;
    try {
      res = await fetch(path, init);
    } catch (_) {
      throw new ApiError(0, 'network', NETWORK_MSG, null, true);
    } finally {
      if (init.body) init.body = null;
    }
    let data = null;
    try {
      data = await res.json();
    } catch (_) {
      data = null;
    }
    if (!res.ok) {
      const e = errorFromPayload(res.status, data);
      if (res.status === 401) showFatal(e);
      throw e;
    }
    return data;
  }
  const jobPath = (id, suffix) => '/api/jobs/' + encodeURIComponent(id) + (suffix || '');

  // ------------------------------------------------------------------ global UI
  function showFatal(e) {
    if (S.fatal) return;
    S.fatal = true;
    stopPolling();
    if (S.xhr) S.xhr.abort();
    $('splash').hidden = true;
    $('app').hidden = true;
    if (e && e.network) {
      $('fatal-title').textContent = 'برنامه در دسترس نیست';
      $('fatal-text').textContent =
        'ارتباط با برنامه برقرار نشد. اگر پنجره برنامه را بسته‌اید، آن را دوباره از میانبر اجرا کنید.';
    }
    $('fatal-code').textContent = e && e.code ? e.code : '';
    $('fatal').hidden = false;
  }

  function showError(e) {
    const box = $('error-box');
    $('error-message').textContent = (e && e.message) || 'خطای ناشناخته';
    $('error-code').textContent = e && e.code ? e.code : '';
    const det = $('error-detail');
    det.textContent = e && e.detail ? String(e.detail) : '';
    det.hidden = !(e && e.detail);
    box.hidden = false;
    box.scrollIntoView({ block: 'nearest' });
  }
  function clearError() {
    $('error-box').hidden = true;
  }

  let toastTimer = null;
  function toast(msg) {
    const t = $('toast');
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      t.hidden = true;
    }, 4000);
  }

  function setConnected(ok) {
    $('conn-banner').hidden = ok;
  }

  function confirmDialog(title, text, okLabel) {
    const d = $('confirm-dialog');
    if (typeof d.showModal !== 'function') return Promise.resolve(window.confirm(text));
    return new Promise((resolve) => {
      const ac = new AbortController();
      const prev = document.activeElement;
      $('confirm-title').textContent = title;
      $('confirm-text').textContent = text;
      $('confirm-ok').textContent = okLabel || 'تأیید';
      const done = (v) => {
        ac.abort();
        if (d.open) d.close();
        if (prev && typeof prev.focus === 'function') prev.focus();
        resolve(v);
      };
      $('confirm-ok').addEventListener('click', () => done(true), { signal: ac.signal });
      $('confirm-cancel').addEventListener('click', () => done(false), { signal: ac.signal });
      d.addEventListener('cancel', (ev) => { ev.preventDefault(); done(false); }, { signal: ac.signal });
      $('confirm-form').addEventListener('submit', (ev) => ev.preventDefault(), { signal: ac.signal });
      d.showModal();
      $('confirm-cancel').focus();
    });
  }

  function statusBadge(status) {
    return el('span', { class: 'status-badge st-' + (status || 'none'), text: statusLabel(status), 'data-status': status });
  }

  function setProgress(barId, fillId, pct, opts) {
    const bar = $(barId);
    const p = Math.max(0, Math.min(100, Number(pct) || 0));
    $(fillId).style.width = p + '%';
    bar.setAttribute('aria-valuenow', String(Math.round(p)));
    bar.setAttribute('aria-valuetext', fmtPct(p));
    bar.classList.toggle('is-indeterminate', !!(opts && opts.indeterminate));
    bar.classList.toggle('is-ok', !!(opts && opts.ok));
    bar.classList.toggle('is-bad', !!(opts && opts.bad));
  }

  function renderPager(container, total, offset, limit, onPage) {
    clear(container);
    if (!total) return;
    const from = offset + 1;
    const to = Math.min(total, offset + limit);
    const info = el('span', { class: 'muted small', text: 'نمایش ' + fmtNum(from) + ' تا ' + fmtNum(to) + ' از ' + fmtNum(total) });
    const prev = el('button', { type: 'button', class: 'btn btn-ghost btn-sm', text: 'قبلی', 'data-testid': 'pager-prev' });
    const next = el('button', { type: 'button', class: 'btn btn-ghost btn-sm', text: 'بعدی', 'data-testid': 'pager-next' });
    prev.disabled = offset <= 0;
    next.disabled = offset + limit >= total;
    prev.addEventListener('click', () => onPage(Math.max(0, offset - limit)));
    next.addEventListener('click', () => onPage(offset + limit));
    container.append(info, el('div', { class: 'pager-btns' }, prev, next));
  }

  function kvGrid(dl, pairs) {
    clear(dl);
    for (const [k, v, cls] of pairs) {
      if (v === undefined) continue;
      dl.append(el('dt', { text: k }), v instanceof Node ? el('dd', null, v) : el('dd', { class: cls || null, text: v === null ? '—' : v }));
    }
  }

  // ------------------------------------------------------------------ navigation
  const VIEWS = ['dashboard', 'new', 'status', 'report', 'readiness', 'ownership', 'coverage', 'review', 'preview', 'download', 'history', 'settings'];
  const onShow = {};

  function go(view, opts) {
    if (!VIEWS.includes(view)) view = 'dashboard';
    S.view = view;
    store.set('m2n.view', view);
    clearError();
    for (const v of VIEWS) $('view-' + v).hidden = v !== view;
    document.querySelectorAll('.nav-item').forEach((b) => {
      if (b.dataset.view === view) b.setAttribute('aria-current', 'page');
      else b.removeAttribute('aria-current');
    });
    if (view !== 'dashboard') stopDashTimer();
    if (view !== 'ownership') ownStopLivePoll();
    if (onShow[view]) onShow[view](opts || {});
    if (!opts || opts.focus !== false) {
      const h = document.querySelector('#view-' + view + ' h1');
      if (h) {
        h.setAttribute('tabindex', '-1');
        h.focus({ preventScroll: true });
      }
      window.scrollTo(0, 0);
    }
  }

  // ------------------------------------------------------------------ job tracking
  function setCurrentJob(job) {
    S.job = job || null;
    S.jobId = job ? job.id : null;
    store.set('m2n.job', S.jobId);
    if (!job || S.reportJobId !== job.id) {
      S.report = null;
      S.reportJobId = null;
    }
    renderJobChrome();
  }

  function applyJob(j) {
    const prev = S.job;
    const changedJob = !prev || prev.id !== j.id;
    if (changedJob) setCurrentJob(j);
    else S.job = j;
    if (prev && !changedJob && !TERMINAL.has(prev.status) && TERMINAL.has(j.status)) {
      if (j.status === 'completed') toast('تبدیل با موفقیت به پایان رسید.');
      else if (j.status === 'failed') toast('تبدیل ناموفق بود.');
      else toast('عملیات لغو شد.');
    }
    renderJobChrome();
    if (S.view === 'status') renderStatus();
    if (S.view === 'download') renderDownload();
    ensurePolling();
  }

  function renderJobChrome() {
    const j = S.job;
    $('current-job-box').hidden = !j;
    const badge = $('nav-status-badge');
    if (!j) {
      badge.hidden = true;
      return;
    }
    $('current-job-name').textContent = j.filename;
    $('current-job-name').setAttribute('title', j.filename);
    $('current-job-status').textContent =
      statusLabel(j.status) + (ACTIVE.has(j.status) ? ' — ' + fmtPct(j.percent) : '');
    badge.hidden = !ACTIVE.has(j.status);
    badge.textContent = fmtPct(j.percent);
  }

  function stopPolling() {
    clearTimeout(S.poll.timer);
    S.poll.timer = null;
  }
  function ensurePolling() {
    if (S.fatal || S.poll.timer || S.poll.inflight) return;
    if (!S.job || !ACTIVE.has(S.job.status)) return;
    S.poll.timer = setTimeout(pollTick, S.poll.delay);
  }
  async function pollTick() {
    S.poll.timer = null;
    if (!S.jobId) return;
    const id = S.jobId;
    S.poll.inflight = true;
    let retry = false;
    try {
      const j = await api('GET', jobPath(id));
      S.poll.delay = 1000;
      setConnected(true);
      if (S.jobId === id) applyJob(j);
    } catch (e) {
      if (e.status === 404) {
        jobVanished();
      } else if (e.status !== 401) {
        // Network blip or transient server error: the job keeps running server-side.
        if (e.network) setConnected(false);
        S.poll.delay = Math.min(5000, S.poll.delay * 2);
        retry = true;
      }
    } finally {
      S.poll.inflight = false;
    }
    if (retry && !S.fatal) S.poll.timer = setTimeout(pollTick, S.poll.delay);
    else ensurePolling();
  }

  function jobVanished() {
    setCurrentJob(null);
    showError({
      code: 'job.not_found',
      message: 'عملیات قبلی دیگر در دسترس نیست (احتمالاً برنامه بسته و دوباره اجرا شده یا عملیات حذف شده است).',
    });
    if (S.view === 'status') renderStatus();
  }

  async function refreshJob() {
    if (!S.jobId) return null;
    try {
      const j = await api('GET', jobPath(S.jobId));
      setConnected(true);
      applyJob(j);
      return j;
    } catch (e) {
      if (e.status === 404) jobVanished();
      else if (e.network) setConnected(false);
      else if (e.status !== 401) showError(e);
      return null;
    }
  }

  async function loadJobs() {
    try {
      const r = await api('GET', '/api/jobs');
      S.jobs = (r && r.items) || [];
      setConnected(true);
    } catch (e) {
      if (e.network) setConnected(false);
      else if (e.status !== 401) showError(e);
    }
    return S.jobs;
  }

  // ------------------------------------------------------------------ dashboard
  function stopDashTimer() {
    clearTimeout(S.dashTimer);
    S.dashTimer = null;
  }
  onShow.dashboard = async function () {
    stopDashTimer();
    if (!S.version) {
      try {
        const h = await api('GET', '/api/health');
        S.version = h.version;
      } catch (_) {
        /* shown elsewhere */
      }
    }
    $('app-version').textContent = S.version ? faDigits(S.version) : '—';
    await loadJobs();
    renderJobs();
    if (S.view === 'dashboard' && S.jobs.some((j) => ACTIVE.has(j.status))) {
      S.dashTimer = setTimeout(() => {
        if (S.view === 'dashboard') onShow.dashboard();
      }, 2000);
    }
  };

  function renderJobs() {
    const tbody = $('jobs-tbody');
    clear(tbody);
    const jobs = S.jobs;
    $('jobs-count').textContent = fmtNum(jobs.length);
    $('running-count').textContent = fmtNum(jobs.filter((j) => ACTIVE.has(j.status)).length);
    $('jobs-empty').hidden = jobs.length > 0;
    $('jobs-table').hidden = jobs.length === 0;
    for (const j of jobs.slice(0, 100)) {
      const open = el('button', {
        type: 'button',
        class: 'btn btn-secondary btn-sm',
        text: j.id === S.jobId ? 'عملیات جاری' : 'باز کردن',
        'data-action': 'open-job',
        'data-job-id': j.id,
        'data-testid': 'open-job',
      });
      tbody.append(
        el('tr', { 'data-job-id': j.id, class: j.id === S.jobId ? 'is-current' : null },
          el('td', null, el('bdi', { dir: 'ltr', text: j.filename })),
          el('td', null, statusBadge(j.status)),
          el('td', { class: 'num', text: fmtPct(j.percent) }),
          el('td', { class: 'num', text: fmtBytes(j.archive_bytes) }),
          el('td', { class: 'num', text: fmtDate(j.created_at) }),
          el('td', null, open)));
    }
  }

  function openJob(id) {
    const j = S.jobs.find((x) => x.id === id);
    if (!j) return;
    setCurrentJob(j);
    S.poll.delay = 1000;
    ensurePolling();
    if (PRESTART.has(j.status)) {
      S.wizardStep = 2;
      go('new');
    } else {
      go('status');
    }
  }

  // ------------------------------------------------------------------ wizard
  function wizardJob() {
    const j = S.job;
    return j && (PRESTART.has(j.status) || j.status === 'failed') && j.inspection ? j : null;
  }

  onShow.new = function () {
    if (S.wizardStep > 1 && !wizardJob()) S.wizardStep = 1;
    renderWizard();
  };

  function renderWizard() {
    const step = S.wizardStep;
    document.querySelectorAll('[data-step-panel]').forEach((p) => {
      p.hidden = Number(p.dataset.stepPanel) !== step;
    });
    document.querySelectorAll('.stepper .step').forEach((s) => {
      const n = Number(s.dataset.step);
      s.classList.toggle('is-current', n === step);
      s.classList.toggle('is-done', n < step);
      if (n === step) s.setAttribute('aria-current', 'step');
      else s.removeAttribute('aria-current');
    });
    if (step === 1) renderStep1();
    if (step === 2) renderStep2();
    if (step === 3) renderStep3();
  }

  function setWizardStep(n) {
    S.wizardStep = n;
    renderWizard();
    const panel = document.querySelector('[data-step-panel="' + n + '"] h2');
    if (panel) {
      panel.setAttribute('tabindex', '-1');
      panel.focus();
    }
  }

  function renderStep1() {
    const uploading = !!S.xhr;
    $('upload-btn').disabled = !S.file || uploading;
    $('zip-input').disabled = uploading;
    $('upload-cancel').hidden = !uploading;
    $('upload-progress').hidden = !uploading;
  }

  function setFile(f) {
    clearError();
    S.file = null;
    const info = $('file-info');
    if (!f) {
      info.textContent = 'هنوز فایلی انتخاب نشده است.';
      renderStep1();
      return;
    }
    const maxMb = S.settings && S.settings.max_archive_mb;
    if (maxMb && f.size > maxMb * 1024 * 1024) {
      info.textContent = 'فایل انتخاب‌شده: ' + f.name;
      showError({
        code: 'archive.limit_exceeded',
        message: 'حجم فایل (' + fmtBytes(f.size) + ') از حد مجاز تنظیمات (' + fmtBytes(maxMb * 1024 * 1024) + ') بیشتر است.',
      });
      renderStep1();
      return;
    }
    if (f.size === 0) {
      showError({ code: 'archive.not_zip', message: 'فایل انتخاب‌شده خالی است.' });
      renderStep1();
      return;
    }
    S.file = f;
    info.textContent = 'فایل انتخاب‌شده: ' + f.name + ' — ' + fmtBytes(f.size);
    if (!/\.zip$/i.test(f.name)) info.textContent += ' (پسوند فایل ZIP نیست؛ محتوا بررسی می‌شود)';
    renderStep1();
  }

  function startUpload() {
    const f = S.file;
    if (!f || S.xhr) return;
    clearError();
    const xhr = new XMLHttpRequest();
    S.xhr = xhr;
    renderStep1();
    $('upload-label').textContent = 'در حال بارگذاری فایل…';
    setProgress('upload-bar', 'upload-fill', 0);
    $('upload-percent').textContent = fmtPct(0);
    $('upload-bytes').textContent = fmtBytes(0) + ' از ' + fmtBytes(f.size);

    xhr.open('POST', '/api/jobs');
    xhr.setRequestHeader('X-Filename', encodeURIComponent(f.name));
    xhr.setRequestHeader('X-CSRF-Token', S.csrf || '');
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.upload.addEventListener('progress', (ev) => {
      if (!ev.lengthComputable) return;
      const p = (ev.loaded / ev.total) * 100;
      setProgress('upload-bar', 'upload-fill', p);
      $('upload-percent').textContent = fmtPct(p);
      $('upload-bytes').textContent = fmtBytes(ev.loaded) + ' از ' + fmtBytes(ev.total);
    });
    xhr.upload.addEventListener('load', () => {
      $('upload-label').textContent = 'بارگذاری کامل شد؛ در حال بررسی ساختار بایگانی…';
      $('upload-percent').textContent = fmtPct(100);
      setProgress('upload-bar', 'upload-fill', 100, { indeterminate: true });
    });
    const finish = () => {
      S.xhr = null;
      renderStep1();
    };
    xhr.addEventListener('load', () => {
      let data = null;
      try {
        data = JSON.parse(xhr.responseText);
      } catch (_) {
        data = null;
      }
      finish();
      if (xhr.status === 201 || xhr.status === 200) {
        S.file = null;
        $('zip-input').value = '';
        $('file-info').textContent = 'هنوز فایلی انتخاب نشده است.';
        setCurrentJob(data);
        S.entriesPage = 0;
        setWizardStep(2);
        toast('فایل بارگذاری و بررسی شد.');
      } else {
        const e = errorFromPayload(xhr.status, data);
        if (xhr.status === 401) showFatal(e);
        else showError(e);
      }
    });
    xhr.addEventListener('error', () => {
      finish();
      showError({
        code: 'upload.failed',
        message: 'ارسال فایل کامل نشد. اگر فایل بسیار بزرگ است، حد مجاز حجم را در «امنیت و منابع» بررسی کنید و دوباره تلاش کنید.',
      });
    });
    xhr.addEventListener('abort', () => {
      finish();
      toast('بارگذاری لغو شد.');
    });
    xhr.send(f);
  }

  const ENTRIES_PAGE = 50;
  function renderStep2() {
    const j = wizardJob();
    if (!j) return setWizardStep(1);
    const insp = j.inspection || {};
    const entries = insp.entries || [];
    kvGrid($('inspect-summary'), [
      ['نام فایل', el('bdi', { dir: 'ltr', text: j.filename })],
      ['حجم', fmtBytes(j.archive_bytes)],
      ['رمزگذاری', insp.encrypted ? 'رمزدار (' + (insp.encryption || 'نامشخص') + ')' : 'بدون رمز'],
      ['تعداد فایل‌ها', fmtNum(entries.length)],
      ['فایل‌های SQL قابل پردازش', fmtNum((insp.sql_entries || []).length)],
      ['اثرانگشت SHA-256', insp.archive_sha256 || '—', 'mono'],
    ]);
    const tbody = $('entries-tbody');
    clear(tbody);
    const start = S.entriesPage * ENTRIES_PAGE;
    for (const e of entries.slice(start, start + ENTRIES_PAGE)) {
      tbody.append(
        el('tr', { 'data-kind': e.kind },
          el('td', { class: 'ltr', text: e.name }),
          el('td', null, el('span', { class: 'badge ' + (e.kind === 'sql' ? 'badge-ok' : ''), text: KIND_FA[e.kind] || e.kind })),
          el('td', { class: 'num', text: fmtBytes(e.size) }),
          el('td', { class: 'num', text: fmtBytes(e.compressed_size) }),
          el('td', { class: 'ltr', text: e.method === undefined ? '—' : String(e.method) })));
    }
    if (!entries.length) tbody.append(el('tr', null, el('td', { colspan: 5, class: 'empty', text: 'فایلی در بایگانی یافت نشد.' })));
    renderPager($('entries-pager'), entries.length, start, ENTRIES_PAGE, (off) => {
      S.entriesPage = Math.floor(off / ENTRIES_PAGE);
      renderStep2();
    });
    $('to-step-3').disabled = !(insp.sql_entries || []).length;
    if (!(insp.sql_entries || []).length) {
      showError({ code: 'archive.no_sql', message: 'هیچ فایل SQL قابل پردازشی در این بایگانی پیدا نشد.' });
    }
  }

  async function renderStep3() {
    const j = wizardJob();
    if (!j) return setWizardStep(1);
    const insp = j.inspection || {};
    $('password-field').hidden = !insp.encrypted;
    $('enc-type').textContent = insp.encryption ? '(' + insp.encryption + ')' : '';
    if (j.status === 'awaiting_password' && j.error && /^archive\.(wrong_password|password_required)$/.test(j.error.code)) {
      fieldError('zip-password', j.error.message, j.error.code);
    }
    syncSecretMode();
    // previous job list (completed jobs of this session)
    const sel = $('previous-job');
    const keep = sel.value;
    await loadJobs();
    clear(sel);
    sel.append(el('option', { value: '', text: 'بدون مقایسه' }));
    for (const pj of S.jobs) {
      if (pj.status !== 'completed' || pj.id === j.id) continue;
      sel.append(el('option', { value: pj.id, text: pj.filename + ' — ' + fmtDate(pj.created_at) }));
    }
    sel.value = keep && [...sel.options].some((o) => o.value === keep) ? keep : '';
  }

  function fieldError(inputId, msg, code) {
    const input = $(inputId);
    const box = $(inputId === 'pkg-pass' || inputId === 'pkg-pass2' ? 'pkg-pass-error' : inputId + '-error');
    if (input) input.setAttribute('aria-invalid', 'true');
    if (box) {
      clear(box);
      box.append(msg);
      if (code) box.append(' ', el('span', { class: 'error-code', text: code }));
      box.hidden = false;
    }
  }
  function clearFieldErrors(scope) {
    scope.querySelectorAll('[aria-invalid="true"]').forEach((n) => n.removeAttribute('aria-invalid'));
    scope.querySelectorAll('.field-error').forEach((n) => {
      n.hidden = true;
      clear(n);
    });
  }

  function secretMode() {
    const r = document.querySelector('input[name="secret-mode"]:checked');
    return r ? r.value : 'generated';
  }
  function syncSecretMode() {
    $('passphrase-fields').hidden = secretMode() !== 'passphrase';
  }

  function wipeSecretInputs() {
    for (const id of ['zip-password', 'pkg-pass', 'pkg-pass2']) {
      const n = $(id);
      n.value = '';
      n.type = 'password';
    }
    document.querySelectorAll('.toggle-vis').forEach((b) => {
      b.setAttribute('aria-pressed', 'false');
      b.textContent = 'نمایش';
    });
  }

  async function submitStart(ev) {
    ev.preventDefault();
    const form = $('step-3');
    clearFieldErrors(form);
    clearError();
    const j = wizardJob();
    if (!j) return;
    const encrypted = !!(j.inspection && j.inspection.encrypted);
    const mode = secretMode();
    let ok = true;
    if (encrypted && !$('zip-password').value) {
      fieldError('zip-password', 'رمز فایل ZIP را وارد کنید.');
      ok = false;
    }
    if (mode === 'passphrase') {
      const p1 = $('pkg-pass').value;
      const p2 = $('pkg-pass2').value;
      if ([...p1].length < 12) {
        fieldError('pkg-pass', 'رمز بسته باید حداقل ۱۲ کاراکتر باشد.');
        ok = false;
      } else if (p1 !== p2) {
        fieldError('pkg-pass2', 'تکرار رمز بسته با رمز یکسان نیست.');
        ok = false;
      }
    }
    if (!ok) {
      const first = form.querySelector('[aria-invalid="true"]');
      if (first) first.focus();
      return;
    }
    const body = {
      password: encrypted ? $('zip-password').value : null,
      money_unit: $('money-unit').value || null,
      package_secret_mode: mode,
      package_passphrase: mode === 'passphrase' ? $('pkg-pass').value : null,
      previous_job_id: $('previous-job').value || null,
    };
    // Secrets leave the DOM immediately; the user retypes them on retry.
    wipeSecretInputs();
    const btn = $('start-btn');
    btn.disabled = true;
    btn.textContent = encrypted ? 'در حال بررسی رمز…' : 'در حال شروع…';
    try {
      const res = await api('POST', jobPath(j.id, '/start'), body);
      S.poll.delay = 1000;
      applyJob(res);
      S.wizardStep = 1;
      go('status');
    } catch (e) {
      if (e.code === 'archive.wrong_password' || e.code === 'archive.password_required') {
        fieldError('zip-password', e.message, e.code);
        if (S.job) S.job.status = 'awaiting_password';
        $('zip-password').focus();
      } else if (e.code === 'package.passphrase_weak') {
        fieldError('pkg-pass', e.message, e.code);
        $('pkg-pass').focus();
      } else if (e.status !== 401) {
        showError(e);
      }
    } finally {
      body.password = null;
      body.package_passphrase = null;
      btn.disabled = false;
      btn.textContent = 'شروع تبدیل';
    }
  }

  async function discardUpload() {
    const j = wizardJob();
    if (!j) return setWizardStep(1);
    const ok = await confirmDialog('حذف فایل بارگذاری‌شده', 'فایل بارگذاری‌شده و پوشه موقت آن حذف شود؟', 'حذف');
    if (!ok) return;
    try {
      await api('DELETE', jobPath(j.id));
    } catch (e) {
      if (e.status !== 404 && e.status !== 401) return showError(e);
    }
    setCurrentJob(null);
    setWizardStep(1);
  }

  // ------------------------------------------------------------------ status
  onShow.status = function () {
    renderStatus();
    if (S.jobId) refreshJob();
  };

  function stageStates(j) {
    const stages = j.stages || Object.keys(STAGE_FA);
    const idx = j.stage ? stages.indexOf(j.stage) : -1;
    return stages.map((key, i) => {
      let st = 'pending';
      if (j.status === 'completed') st = 'done';
      else if (PRESTART.has(j.status)) st = i === 0 && j.inspection ? 'done' : 'pending';
      else if (idx >= 0) {
        if (i < idx) st = 'done';
        else if (i === idx) st = ACTIVE.has(j.status) ? 'current' : j.status === 'failed' || j.status === 'cancelled' ? 'failed' : 'current';
      } else if (ACTIVE.has(j.status) && i === 0 && j.inspection) st = 'done';
      return { key, st };
    });
  }

  const STAGE_STATE_FA = { done: 'انجام شد', current: 'در حال انجام', pending: 'در انتظار', failed: 'متوقف شد' };

  function renderStatus() {
    const j = S.job;
    $('status-empty').hidden = !!j;
    $('status-body').hidden = !j;
    const badge = $('job-status');
    if (!j) {
      badge.textContent = '—';
      badge.className = 'status-badge';
      badge.removeAttribute('data-status');
      $('status-file').textContent = '';
      return;
    }
    badge.textContent = statusLabel(j.status);
    badge.className = 'status-badge st-' + j.status;
    badge.setAttribute('data-status', j.status);
    $('status-file').textContent = 'فایل: ' + j.filename + ' — ' + fmtBytes(j.archive_bytes);

    const pct = j.status === 'completed' ? 100 : Number(j.percent) || 0;
    $('overall-percent').textContent = fmtPct(pct);
    $('overall-percent').setAttribute('data-value', String(pct));
    setProgress('overall-bar', 'overall-fill', pct, {
      ok: j.status === 'completed',
      bad: j.status === 'failed' || j.status === 'cancelled',
      indeterminate: j.status === 'queued',
    });

    const stageName = j.stage ? STAGE_FA[j.stage] || j.stage : null;
    let stageText;
    if (j.status === 'running') stageText = 'در حال ' + (stageName || 'پردازش') + '…';
    else if (j.status === 'queued') stageText = 'در صف اجرا…';
    else if (j.status === 'completed') stageText = 'تبدیل کامل شد.';
    else if (j.status === 'failed') stageText = 'تبدیل ناموفق بود' + (stageName ? ' (مرحله ' + stageName + ')' : '') + '.';
    else if (j.status === 'cancelled') stageText = 'عملیات لغو شد.';
    else if (j.status === 'awaiting_password') stageText = 'برای ادامه، رمز فایل ZIP را وارد کنید.';
    else stageText = 'فایل بارگذاری شده؛ تنظیمات تبدیل را تعیین و شروع کنید.';
    $('status-stage-text').textContent = stageText;

    if (ACTIVE.has(j.status) && j.stage) {
      $('stage-counter').textContent =
        (stageName || j.stage) + ' — ' + fmtNum(j.stage_done) + (j.stage_total ? ' از ' + fmtNum(j.stage_total) : '');
    } else {
      $('stage-counter').textContent = stageName || '—';
    }
    updateElapsed();

    // stage list
    const list = $('stage-list');
    clear(list);
    for (const { key, st } of stageStates(j)) {
      const isCur = st === 'current';
      const icon = st === 'done' ? '✓' : st === 'failed' ? '!' : '';
      const li = el('li', { class: 'stage-item is-' + st, 'data-stage': key, 'data-state': st, 'aria-current': isCur ? 'step' : null },
        el('span', { class: 'stage-icon', 'aria-hidden': 'true', text: icon }),
        el('span', { class: 'stage-name', text: STAGE_FA[key] || key }),
        el('span', {
          class: 'stage-state num',
          text: isCur && j.stage_total
            ? fmtNum(j.stage_done) + ' از ' + fmtNum(j.stage_total)
            : STAGE_STATE_FA[st],
        }));
      list.append(li);
    }

    // error
    const errBox = $('job-error');
    if (j.error && j.status !== 'completed') {
      errBox.hidden = false;
      $('job-error-message').textContent = j.error.message || 'خطا';
      $('job-error-code').textContent = j.error.code || '';
      $('job-error-detail').textContent = j.error.detail ? String(j.error.detail) : '';
    } else {
      errBox.hidden = true;
    }

    $('cancel-btn').hidden = TERMINAL.has(j.status);
    $('retry-btn').hidden = !(PRESTART.has(j.status) || (j.status === 'failed' && j.inspection));
    $('retry-btn').textContent = PRESTART.has(j.status) ? 'ادامه تنظیمات و شروع' : 'تنظیم دوباره و تلاش مجدد';
    $('goto-report').hidden = j.status !== 'completed';
    $('goto-download').hidden = !(j.status === 'completed' && j.has_package);

    // screen-reader announcement (status/stage changes and every 10%)
    const bucket = Math.floor(pct / 10);
    const announce = statusLabel(j.status) + ' — ' + (stageName || '') + ' — ' + fmtPct(bucket * 10);
    if (announce !== S.lastAnnounce) {
      S.lastAnnounce = announce;
      $('status-live').textContent = statusLabel(j.status) + (stageName ? '، ' + stageName : '') + '، ' + fmtPct(pct);
    }
  }

  function updateElapsed() {
    const j = S.job;
    if (!j || !j.created_at) {
      $('elapsed').textContent = '—';
      return;
    }
    const end = j.finished_at || (ACTIVE.has(j.status) ? Date.now() / 1000 : null);
    $('elapsed').textContent = end ? fmtDuration(end - j.created_at) : '—';
  }

  async function cancelJob() {
    const j = S.job;
    if (!j) return;
    const ok = await confirmDialog('لغو عملیات', 'عملیات تبدیل لغو شود؟ فایل‌های میانی ناقص خواهند ماند و باید دوباره شروع کنید.', 'لغو عملیات');
    if (!ok) return;
    try {
      const r = await api('POST', jobPath(j.id, '/cancel'));
      applyJob(r);
      ensurePolling();
      if (!TERMINAL.has(r.status)) {
        // cancellation is cooperative; keep polling until the worker stops
        S.poll.delay = 1000;
      }
    } catch (e) {
      if (e.status !== 401) showError(e);
    }
  }

  // ------------------------------------------------------------------ report
  async function ensureReport() {
    const j = S.job;
    if (!j || j.status !== 'completed') return null;
    if (S.report && S.reportJobId === j.id) return S.report;
    const r = await api('GET', jobPath(j.id, '/report'));
    S.report = r;
    S.reportJobId = j.id;
    return r;
  }

  function notReadyMessage(what) {
    const j = S.job;
    if (!j) return 'عملیاتی انتخاب نشده است. ابتدا یک بکاپ را تبدیل کنید.';
    if (j.status === 'completed') return what + ' در دسترس نیست.';
    return what + ' پس از کامل شدن تبدیل در دسترس است. وضعیت فعلی: ' + statusLabel(j.status) + '.';
  }

  onShow.report = async function () {
    $('report-body').hidden = true;
    $('report-empty').hidden = false;
    $('report-download').hidden = true;
    $('download-report-html').hidden = true;
    $('report-empty').textContent = notReadyMessage('گزارش');
    if (!S.job || S.job.status !== 'completed') return;
    $('report-empty').textContent = 'در حال بارگذاری گزارش…';
    try {
      const r = await ensureReport();
      if (S.view !== 'report' || !r) return;
      renderReport(r);
      $('report-empty').hidden = true;
      $('report-body').hidden = false;
      const a = $('report-download');
      a.href = jobPath(S.job.id, '/report/download');
      a.hidden = false;
      const h = $('download-report-html');
      h.href = jobPath(S.job.id, '/report.html');
      h.hidden = false;
    } catch (e) {
      $('report-empty').textContent = notReadyMessage('گزارش');
      if (e.status !== 401) showError(e);
    }
  };

  function renderReport(r) {
    // readiness
    const rd = $('readiness');
    clear(rd);
    const ready = r.readiness === 'ready';
    rd.className = 'readiness ' + (ready ? 'is-ready' : 'is-blocked');
    rd.setAttribute('data-readiness', String(r.readiness));
    rd.append(
      el('span', { 'aria-hidden': 'true', text: ready ? '✓' : '⛔' }),
      el('span', null,
        ready ? 'آماده بررسی Importer' : 'مسدود — تا رفع مغایرت‌ها قابل Import نیست',
        el('span', {
          class: 'readiness-sub',
          text: ready
            ? 'هیچ مورد مسدودکننده‌ای یافت نشد. پیش از Import، گزارش تطبیق مالی را با مدیر میرزا بررسی کنید.'
            : 'موارد مسدودکننده را در فهرست زیر و «صف بررسی» بررسی و در منبع اصلاح کنید، سپس دوباره تبدیل کنید.',
        })));

    // meta
    const mu = r.money_unit || {};
    kvGrid($('report-meta'), [
      ['شناسه Import', r.import_id || '—', 'mono'],
      ['اثرانگشت منبع', r.source_fingerprint || '—', 'mono'],
      ['واحد مبالغ', mu.value ? UNIT_FA[mu.value] || mu.value : 'تعیین‌نشده — مبالغ مسدود هستند'],
      ['منبع تعیین واحد', mu.source ? UNIT_SOURCE_FA[mu.source] || mu.source : '—'],
      ['توضیح واحد', mu.note || undefined],
    ]);

    // counts
    const cards = $('count-cards');
    clear(cards);
    const counts = r.counts || {};
    const keys = Object.keys(counts);
    if (!keys.length) cards.append(el('div', { class: 'card empty', text: 'آماری ثبت نشده است.' }));
    for (const k of keys) {
      const c = counts[k];
      const isObj = c && typeof c === 'object';
      const card = el('div', { class: 'card count-card', 'data-entity': k, 'data-testid': 'count-' + k },
        el('h3', { text: entityLabel(k) }),
        el('div', { class: 'big num', text: fmtNum(isObj ? c.converted : c) }),
        el('div', { class: 'muted small', text: 'تبدیل‌شده' }));
      if (isObj) {
        const sub = el('div', { class: 'sub' });
        const part = (label, v, cls) => {
          if (v === undefined) return;
          sub.append(el('span', { class: v && cls ? cls : null }, label + ': ', el('b', { class: 'num', text: fmtNum(v) })));
        };
        part('مبدأ', c.source);
        part('نیاز به بررسی', c.review, 'is-warn');
        part('ردشده', c.skipped, 'is-bad');
        card.append(sub);
      }
      cards.append(card);
    }

    renderHistoryCards(r);
    renderClassSections(r.coverage || []);
    renderBlockerGroups(r.blockers || [], r.mapping || []);
    renderIssues($('blocker-list'), r.blockers || [], 'blocker');
    renderIssues($('warning-list'), r.warnings || [], 'warning');
    $('blocker-count').textContent = fmtNum((r.blockers || []).length);
    $('warning-count').textContent = fmtNum((r.warnings || []).length);

    // reconciliation
    const rec = $('reconciliation');
    clear(rec);
    const recon = r.reconciliation || {};
    const secs = Object.keys(recon);
    if (!secs.length) rec.append(el('p', { class: 'muted', text: 'اطلاعات تطبیق مالی موجود نیست.' }));
    secs.forEach((sec, i) => {
      const d = el('details', { class: 'recon-section', 'data-section': sec },
        el('summary', null, el('h3', null, labelWithCode(entityLabel(sec), sec))),
        el('div', { class: 'table-wrap' }, reconTable(recon[sec], 0)));
      if (i === 0 || sec === 'wallet') d.open = true;
      rec.append(d);
    });

    // mapping
    const mt = $('mapping-tbody');
    clear(mt);
    for (const m of r.mapping || []) {
      mt.append(el('tr', { 'data-status': m.status },
        el('td', { text: entityLabel(m.entity) }),
        el('td', { class: 'ltr', text: m.source_table || '—' }),
        el('td', null, tagList(m.source_columns || [])),
        el('td', { class: 'ltr', text: m.target || '—' }),
        el('td', null, el('span', { class: 'badge st-' + m.status, text: MAPPING_FA[m.status] || own(L.mapping_status, m.status) || m.status })),
        el('td', { class: 'small', text: m.note || '' })));
    }
    if (!(r.mapping || []).length) mt.append(el('tr', null, el('td', { colspan: 6, class: 'empty', text: 'اطلاعات نگاشت موجود نیست.' })));

    renderDiff(r.diff);
    renderSchema(r.schema_profile);
  }

  function reconValue(v) {
    if (v === null || typeof v !== 'object') return fmtNum(v);
    const box = el('div', { class: 'kv-chips' });
    for (const k of Object.keys(v)) {
      const x = v[k];
      const shown = x !== null && typeof x === 'object' ? truncate(stringify(x), 80) : fmtNum(x);
      box.append(el('span', { class: 'kv-chip', title: k + ': ' + truncate(stringify(x), 2000) },
        el('span', { class: 'k ltr', text: k }), el('b', { class: 'num', text: shown })));
    }
    return box;
  }

  function tagList(arr, cls) {
    const box = el('div', { class: 'tag-list' });
    if (!arr || !arr.length) {
      box.append(el('span', { class: 'muted small', text: '—' }));
      return box;
    }
    for (const t of arr) box.append(el('span', { class: 'tag ' + (cls || ''), text: stringify(t) }));
    return box;
  }

  function renderIssues(ul, items, sev) {
    clear(ul);
    if (!items.length) {
      ul.append(el('li', { class: 'none', text: 'موردی وجود ندارد.' }));
      return;
    }
    for (const it of items) {
      ul.append(el('li', { 'data-code': it.code, 'data-severity': sev },
        el('span', { class: 'msg', text: it.message || it.code }),
        it.count !== undefined && it.count !== null
          ? el('span', { class: 'pill num ' + (sev === 'blocker' ? 'pill-bad' : 'pill-warn'), text: fmtNum(it.count) + ' مورد' })
          : null,
        el('span', { class: 'error-code', text: it.code || '' })));
    }
  }

  function renderDiff(diff) {
    const box = $('diff-body');
    clear(box);
    if (!diff) {
      box.append(el('p', { class: 'muted', text: 'مقایسه‌ای انتخاب نشده است. برای مقایسه، هنگام شروع تبدیل یک تبدیل قبلی را انتخاب کنید.' }));
      return;
    }
    const dl = el('dl', { class: 'kv-grid' });
    const delta = diff.balance_delta_total;
    const deltaCls = typeof delta === 'string' && delta.startsWith('-') ? 'delta-neg' : 'delta-pos';
    kvGrid(dl, [
      ['شناسه Import قبلی', diff.previous_import_id || '—', 'mono'],
      ['تغییر مجموع موجودی‌ها', delta === null || delta === undefined ? '—' : fmtNum(delta), 'num ' + deltaCls],
    ]);
    box.append(dl);
    const ents = new Set([...Object.keys(diff.added || {}), ...Object.keys(diff.removed || {}), ...Object.keys(diff.changed || {})]);
    if (ents.size) {
      const tbody = el('tbody');
      for (const e of ents) {
        tbody.append(el('tr', { 'data-entity': e },
          el('td', { text: entityLabel(e) }),
          el('td', { class: 'num delta-pos', text: fmtNum((diff.added || {})[e] || 0) }),
          el('td', { class: 'num delta-neg', text: fmtNum((diff.removed || {})[e] || 0) }),
          el('td', { class: 'num', text: fmtNum((diff.changed || {})[e] || 0) })));
      }
      box.append(el('div', { class: 'table-wrap' }, el('table', { class: 'table' },
        el('thead', null, el('tr', null,
          el('th', { scope: 'col', text: 'بخش' }), el('th', { scope: 'col', text: 'افزوده' }),
          el('th', { scope: 'col', text: 'حذف‌شده' }), el('th', { scope: 'col', text: 'تغییریافته' }))),
        tbody)));
    } else {
      box.append(el('p', { class: 'muted', text: 'تفاوتی در تعداد رکوردها یافت نشد.' }));
    }
    if ((diff.notes || []).length) {
      box.append(el('ul', { class: 'check-list small' }, diff.notes.map((n) => el('li', { text: stringify(n) }))));
    }
  }

  function renderSchema(sp) {
    const box = $('schema-body');
    clear(box);
    if (!sp) {
      box.append(el('p', { class: 'muted', text: 'اطلاعات تشخیص ساختار موجود نیست.' }));
      return;
    }
    let conf = sp.confidence;
    if (typeof conf === 'number') conf = conf <= 1 ? fmtPct(conf * 100) : fmtPct(conf);
    const dl = el('dl', { class: 'kv-grid' });
    kvGrid(dl, [
      ['نسخه تشخیص‌داده‌شده', sp.label || sp.id || 'ناشناخته'],
      ['شناسه پروفایل', sp.id || '—', 'mono'],
      ['اطمینان', conf === undefined || conf === null ? '—' : String(conf)],
      ['جداول منطبق', el('span', null, fmtNum((sp.matched_tables || []).length))],
    ]);
    box.append(dl,
      el('h3', { class: 'small', text: 'جداول شناخته‌شده' }), tagList(sp.matched_tables),
      el('h3', { class: 'small', text: 'جداول مورد انتظار اما موجود نیست' }), tagList(sp.missing_tables, 'is-bad'),
      el('h3', { class: 'small', text: 'جداول ناشناخته (جزئیات در «پوشش جدول‌ها»)' }), tagList(sp.unknown_tables, 'is-warn'));
  }

  // ------------------------------------------------------------------ report (phase 2 parts)
  function reconTable(val, depth) {
    const tbody = el('tbody');
    if (val && typeof val === 'object' && !Array.isArray(val)) {
      for (const k of Object.keys(val)) {
        tbody.append(el('tr', { 'data-key': k },
          el('th', { scope: 'row' }, labelWithCode(keyLabel(k), k, own(L.key_help, k))),
          el('td', { class: 'num' }, reconCell(val[k], depth + 1))));
      }
      if (!Object.keys(val).length) tbody.append(el('tr', null, el('td', { class: 'muted', text: '—' })));
    } else {
      tbody.append(el('tr', null, el('th', { scope: 'row', text: '—' }), el('td', { class: 'num' }, reconCell(val, depth + 1))));
    }
    return el('table', { class: 'table recon-table' }, tbody);
  }

  function reconCell(v, depth) {
    if (typeof v === 'boolean') return el('span', { class: 'bool ' + (v ? 'is-yes' : 'is-no'), text: v ? 'بله' : 'خیر' });
    if (v === null || v === undefined) return document.createTextNode('—');
    if (Array.isArray(v)) {
      if (!v.length) return document.createTextNode('—');
      if (v.every((x) => x === null || typeof x !== 'object')) return tagList(v.slice(0, 100));
      return el('div', null, v.slice(0, 50).map((x) => reconTable(x, depth)));
    }
    if (typeof v === 'object') return depth > 4 ? reconValue(v) : reconTable(v, depth);
    if (typeof v === 'string' && !/^-?\d+(\.\d+)?$/.test(v)) {
      const lbl = own(L.keys, v);
      return lbl ? labelWithCode(lbl, v) : el('span', { class: 'ltr-text', text: v });
    }
    return document.createTextNode(fmtNum(v));
  }

  function firstCount(v) {
    if (typeof v === 'number' && Number.isFinite(v)) return v;
    if (typeof v === 'string' && /^\d+$/.test(v)) return Number(v);
    if (v && typeof v === 'object' && !Array.isArray(v)) {
      for (const k of ['count', 'records', 'total', 'rows']) {
        const x = firstCount(v[k]);
        if (x !== null) return x;
      }
    }
    return null;
  }

  /** Historical payment / wallet transaction totals, if a module reported them. */
  function historyCount(r, countKeys, reconKeys, fields) {
    const counts = r.counts || {};
    for (const k of countKeys) {
      const c = counts[k];
      if (c !== undefined && c !== null) {
        const n = typeof c === 'object' ? firstCount(c.converted !== undefined ? c.converted : c.source) : firstCount(c);
        if (n !== null) return n;
      }
    }
    const recon = r.reconciliation || {};
    for (const k of reconKeys) {
      const sec = recon[k];
      if (!sec || typeof sec !== 'object') continue;
      for (const f of fields) {
        const n = firstCount(sec[f]);
        if (n !== null) return n;
      }
    }
    return null;
  }

  function renderHistoryCards(r) {
    const box = $('history-cards');
    clear(box);
    const fields = ['records', 'exported', 'archived', 'transactions', 'total', 'count', 'source_rows', 'rows'];
    const items = [
      ['history-payments', 'پرداخت‌های تاریخی', 'فقط سابقه؛ پرداخت یا درآمد جدید ساخته نمی‌شود.',
        historyCount(r, ['payments', 'historical_payments'], ['payments'], ['historical_payments', ...fields])],
      ['history-wallet', 'تراکنش‌های کیف پول', 'تاریخچه فقط‌خواندنی؛ موجودی فقط از Snapshot ساخته می‌شود.',
        historyCount(r, ['wallet_history', 'wallet_transactions'], ['wallet_history'], ['wallet_transactions', ...fields])],
    ];
    for (const [tid, label, help, n] of items) {
      if (n === null) continue;
      box.append(el('div', { class: 'card count-card', 'data-testid': tid },
        el('h3', { text: label }), el('div', { class: 'big num', text: fmtNum(n) }), el('div', { class: 'muted small', text: help })));
    }
    box.hidden = !box.childElementCount;
  }

  const SECTION_DEFS = [
    ['OPERATIONAL', 'section-operational', 'عملیاتی', 'وارد بخش‌های فعال NEXA می‌شود (پس از تأیید Importer).'],
    ['ARCHIVE', 'section-archive', 'آرشیو', 'فقط برای سابقه و جست‌وجو؛ هیچ اثری روی موجودی یا سرویس ندارد.'],
    ['REVIEW', 'section-review', 'نیازمند بررسی', 'نگه داشته شده تا مالک NEXA درباره آن تصمیم بگیرد.'],
  ];

  function renderClassSections(coverage) {
    const box = $('class-sections');
    clear(box);
    for (const [klass, tid, title, help] of SECTION_DEFS) {
      const rows = coverage.filter((c) => c && c.class === klass);
      const sum = (f) => rows.reduce((a, c) => a + (Number(c[f]) || 0), 0);
      const list = el('ul', { class: 'mini-list' });
      for (const c of rows.slice(0, 12)) {
        list.append(el('li', { 'data-table': c.table },
          el('bdi', { dir: 'ltr', class: 'mono', text: c.table }),
          el('span', { class: 'badge cov-' + c.status, text: covStatusLabel(c.status) }),
          el('span', { class: 'muted small num', text: fmtNum(c.rows) + ' ردیف' })));
      }
      if (rows.length > 12) list.append(el('li', { class: 'muted small', text: '+' + fmtNum(rows.length - 12) + ' جدول دیگر (در «پوشش جدول‌ها»)' }));
      if (!rows.length) list.append(el('li', { class: 'muted small', text: 'جدولی در این بخش نیست.' }));
      box.append(el('div', { class: 'card class-section', 'data-testid': tid, 'data-class': klass },
        el('h2', null, title, ' ', el('span', { class: 'pill num', text: fmtNum(rows.length) + ' جدول' })),
        el('p', { class: 'muted small', text: help }),
        el('div', { class: 'sub-stats small' },
          el('span', null, 'ردیف: ', el('b', { class: 'num', text: fmtNum(sum('rows')) })),
          el('span', null, 'خروجی: ', el('b', { class: 'num', text: fmtNum(sum('exported')) })),
          el('span', null, 'آرشیو: ', el('b', { class: 'num', text: fmtNum(sum('archived')) })),
          el('span', null, 'بررسی: ', el('b', { class: 'num', text: fmtNum(sum('review')) }))),
        list));
    }
  }

  function renderBlockerGroups(blockers, mapping) {
    const box = $('blocker-groups');
    clear(box);
    if (!blockers.length) box.append(el('p', { class: 'muted', text: 'موردی وجود ندارد.' }));
    const groups = {};
    for (const b of blockers) (groups[blockerCategory(b.code)] = groups[blockerCategory(b.code)] || []).push(b);
    for (const cat of ['financial', 'schema', 'data_quality', 'security']) {
      const items = groups[cat];
      if (!items) continue;
      const ul = el('ul', { class: 'issue-list' });
      for (const it of items) {
        ul.append(el('li', { 'data-code': it.code, 'data-severity': 'blocker' },
          el('span', { class: 'msg' },
            el('strong', { text: own(L.blocker_codes, it.code) || humanise(it.code || '') }),
            el('span', { class: 'block', text: it.message || '' })),
          it.count !== undefined && it.count !== null ? el('span', { class: 'pill num pill-bad', text: fmtNum(it.count) + ' مورد' }) : null,
          el('span', { class: 'error-code', text: it.code || '' })));
      }
      box.append(el('div', { class: 'blocker-group', 'data-category': cat },
        el('h3', { class: 'small' }, labelWithCode(own(L.blocker_categories, cat) || cat, null)), ul));
    }
    const ents = $('blocked-entities');
    clear(ents);
    const blocked = mapping.filter((m) => m && m.status === 'blocked');
    if (!blocked.length) return;
    const ul = el('ul', { class: 'issue-list' });
    for (const m of blocked) {
      ul.append(el('li', { 'data-entity': m.entity },
        el('span', { class: 'msg' }, el('strong', { text: entityLabel(m.entity) + ': ' }),
          own(L.entity_block_reason, m.entity) || m.note || '—'),
        el('span', { class: 'error-code', text: m.entity })));
    }
    ents.append(el('h3', { class: 'small', text: 'بخش‌های مسدود و دلیل' }), ul);
  }

  // ------------------------------------------------------------------ migration readiness
  // Aggregates only (counts, sums, codes, table names) from migration-readiness.json; every key may
  // be absent, in which case the field is shown as «—» or skipped.
  const obj = (v) => (v && typeof v === 'object' && !Array.isArray(v) ? v : {});
  const objList = (v) => (Array.isArray(v) ? v.filter((x) => x && typeof x === 'object' && !Array.isArray(x)) : []);
  const rkLabel = (k) => own(L.readiness_keys, k) || keyLabel(k);
  const codeLabel = (table, code, fallback) => own(table, String(code)) || fallback || humanise(code === undefined || code === null ? '—' : code);
  function yesNo(v) {
    if (typeof v !== 'boolean') return '—';
    return el('span', { class: 'bool ' + (v ? 'is-yes' : 'is-no'), text: v ? 'بله' : 'خیر' });
  }
  function moneyText(amount, currency) {
    if (amount === null || amount === undefined) return '—';
    const unit = own(L.money_units, String(currency)) || (currency ? String(currency) : '');
    return fmtNum(amount) + (unit ? ' ' + unit : '');
  }
  /** kv-grid rows whose label is a Persian key label with the code shown small. */
  function rkGrid(dl, pairs) {
    clear(dl);
    let any = false;
    for (const [k, v, cls] of pairs) {
      if (v === undefined) continue;
      any = true;
      dl.append(el('dt', { 'data-key': k }, labelWithCode(rkLabel(k), k)),
        v instanceof Node ? el('dd', null, v) : el('dd', { class: cls || null, text: v === null ? '—' : v }));
    }
    if (!any) dl.append(el('dt', { text: '—' }), el('dd', { class: 'muted', text: 'اطلاعاتی موجود نیست.' }));
  }
  const numOrUndef = (v) => (v === undefined ? undefined : fmtNum(v));
  function countTable(counts, labelOf, head, testid) {
    const c = obj(counts);
    const keys = Object.keys(c);
    if (!keys.length) return null;
    return el('div', { class: 'table-wrap readiness-mini' },
      el('table', { class: 'table', 'data-testid': testid || null },
        el('thead', null, el('tr', null, el('th', { scope: 'col', text: head }), el('th', { scope: 'col', text: 'تعداد' }))),
        el('tbody', null, keys.map((k) => el('tr', { 'data-key': k },
          el('td', null, labelWithCode(labelOf(k), k)), el('td', { class: 'num', text: fmtNum(c[k]) }))))));
  }
  function codedItems(ul, items, emptyText, labels) {
    clear(ul);
    if (!items.length) ul.append(el('li', { class: 'none', text: emptyText }));
    for (const it of items) {
      const code = it.code === undefined || it.code === null ? '' : String(it.code);
      const title = labels ? own(labels, code) : undefined;
      ul.append(el('li', { 'data-code': code },
        el('span', { class: 'msg' },
          title && title !== it.message_fa ? el('strong', { text: title + ' — ' }) : null,
          String(it.message_fa || humanise(code || '—'))),
        it.count !== undefined && it.count !== null ? el('span', { class: 'pill num', text: fmtNum(it.count) + ' مورد' }) : null,
        code ? el('span', { class: 'error-code', text: code }) : null));
    }
  }

  onShow.readiness = async function () {
    $('readiness-body').hidden = true;
    $('readiness-empty').hidden = false;
    $('download-readiness-json').hidden = true;
    $('download-readiness-html').hidden = true;
    $('readiness-empty').textContent = notReadyMessage('گزارش آمادگی مهاجرت');
    if (!S.job || S.job.status !== 'completed') return;
    $('readiness-empty').textContent = 'در حال بارگذاری…';
    const jobId = S.job.id;
    try {
      const r = S.readinessFor === jobId && S.readiness ? S.readiness : await api('GET', jobPath(jobId, '/readiness'));
      if (S.view !== 'readiness' || !S.job || S.job.id !== jobId) return;
      S.readiness = r;
      S.readinessFor = jobId;
      renderReadiness(r);
      const j = $('download-readiness-json');
      j.href = jobPath(jobId, '/readiness.json');
      j.hidden = false;
      const h = $('download-readiness-html');
      h.href = jobPath(jobId, '/readiness.html');
      h.hidden = false;
      $('readiness-empty').hidden = true;
      $('readiness-body').hidden = false;
    } catch (e) {
      $('readiness-empty').textContent = e.status === 404 && e.message ? e.message : notReadyMessage('گزارش آمادگی مهاجرت');
      if (e.status !== 404 && e.status !== 401) showError(e);
    }
  };

  function renderReadiness(raw) {
    const r = obj(raw);
    // verdict
    const v = obj(r.verdict);
    const code = v.code === undefined || v.code === null ? '' : String(v.code);
    const vb = $('readiness-verdict');
    clear(vb);
    vb.className = 'readiness ' + (code === 'READY_FOR_IMPORTER_DRY_RUN' ? 'is-dryrun' : 'is-blocked');
    vb.setAttribute('data-verdict', code);
    const reasons = objList(v.reasons);
    vb.append(
      el('span', { 'aria-hidden': 'true', text: code === 'READY_FOR_IMPORTER_DRY_RUN' ? '◐' : '⛔' }),
      el('span', null,
        el('span', { 'data-testid': 'readiness-verdict-label', text: v.label_fa ? String(v.label_fa) : codeLabel(L.readiness_verdicts, code) }),
        code ? el('span', { class: 'key-code', text: code }) : null,
        el('span', { class: 'readiness-sub', text: 'حتی در بهترین حالت، این نتیجه مجوز Import نیست؛ Importer نکسا هنوز ساخته نشده است.' }),
        reasons.length ? el('ul', { class: 'readiness-reasons' }, reasons.map((x) => el('li', { 'data-code': x.code === undefined ? null : String(x.code) },
          String(x.message_fa || humanise(x.code || '—')), x.code ? el('span', { class: 'key-code', text: String(x.code) }) : null))) : null));

    rkGrid($('readiness-meta'), ['import_id', 'generated_at', 'converter_version', 'package_contract_version', 'readiness_version']
      .map((k) => [k, r[k] === undefined || r[k] === null ? undefined : el('bdi', { dir: 'ltr', class: 'mono', text: String(r[k]) })]));

    // package health
    const ph = obj(r.package_health);
    rkGrid($('readiness-health-kv'), [
      ['readiness', ph.readiness === undefined ? undefined : codeLabel(L.package_readiness, ph.readiness), ph.readiness === 'ready' ? 'ok-text' : 'bad-text'],
      ['catalogue_ok', 'catalogue_ok' in ph ? yesNo(ph.catalogue_ok) : undefined],
    ]);
    codedItems($('readiness-health-blockers'), objList(ph.blockers), 'مورد مسدودکننده‌ای برای بسته ثبت نشده است.', L.blocker_codes);

    // financial
    const fin = obj(r.financial);
    const pos = obj(fin.positive_balances);
    const mu = obj(fin.money_unit);
    const hd = obj(fin.history_differences);
    let unit;
    if (fin.money_unit !== undefined) {
      unit = (mu.value ? codeLabel(L.money_units, mu.value) : 'تعیین‌نشده') + ' — ' +
        (mu.confirmed_by ? 'تأییدشده توسط ' + codeLabel(L.confirmed_by, mu.confirmed_by) : 'تأیید نشده');
    }
    rkGrid($('readiness-financial-kv'), [
      ['balanced', 'balanced' in fin ? yesNo(fin.balanced) : undefined],
      ['source_recount_matches', 'source_recount_matches' in fin ? yesNo(fin.source_recount_matches) : undefined],
      ['money_unit', unit, mu.confirmed_by ? '' : 'bad-text'],
      ['positive_balances', fin.positive_balances === undefined ? undefined : fmtNum(pos.count) + ' حساب — ' + moneyText(pos.amount_minor, pos.currency)],
      ['debts', fin.debts && typeof fin.debts === 'object' && ('count' in fin.debts || 'amount_minor' in fin.debts)
        ? fmtNum(fin.debts.count) + ' حساب — ' + moneyText(fin.debts.amount_minor, fin.debts.currency) : undefined],
      ['net_minor', fin.net_minor == null ? undefined : moneyText(fin.net_minor, pos.currency)],
      ['independent_recount_matches', 'independent_recount_matches' in fin ? yesNo(fin.independent_recount_matches) : undefined],
      ['payments_unknown_status', numOrUndef(fin.payments_unknown_status)],
      ['cases_needing_review', numOrUndef(fin.cases_needing_review)],
    ]);
    const fx = $('readiness-financial-extra');
    clear(fx);
    const debtsObj = obj(fin.debts);
    if (Object.keys(debtsObj).length && !('count' in debtsObj || 'amount_minor' in debtsObj)) fx.append(el('div', { class: 'readiness-mini' }, el('h3', { text: rkLabel('debts') }), reconTable(fin.debts, 0)));
    if (fin.history_differences !== undefined) {
      const dl = el('dl', { class: 'kv-grid' });
      rkGrid(dl, ['total', 'proven', 'probable', 'unexplained'].map((k) => [k, numOrUndef(hd[k])]));
      fx.append(el('div', { class: 'readiness-mini', 'data-testid': 'readiness-history-differences' },
        el('h3', { text: rkLabel('history_differences') }), dl,
        countTable(hd.by_cause, (k) => codeLabel(L.history_causes, k), 'علت', 'readiness-causes')));
    }

    // coverage
    const cov = obj(r.coverage);
    rkGrid($('readiness-coverage-kv'), [['tables_total', numOrUndef(cov.tables_total)], ['non_empty', numOrUndef(cov.non_empty)]]);
    const cx = $('readiness-coverage-extra');
    clear(cx);
    cx.append(...[countTable(cov.by_status, covStatusLabel, 'وضعیت'), countTable(cov.by_class, (k) => classLabel(k), 'طبقه')].filter(Boolean));
    const unc = Array.isArray(cov.uncovered) ? cov.uncovered.filter((t) => typeof t === 'string' || typeof t === 'number') : [];
    cx.append(el('div', { class: 'readiness-mini' },
      el('h3', null, rkLabel('uncovered') + ' ', el('span', { class: 'pill num ' + (unc.length ? 'pill-bad' : ''), text: fmtNum(unc.length) })),
      unc.length
        ? el('ul', { class: 'readiness-tables', 'data-testid': 'readiness-uncovered' }, unc.map((t) => el('li', { 'data-table': String(t) }, el('bdi', { dir: 'ltr', text: String(t) }))))
        : el('p', { class: 'muted small', text: 'همه جدول‌ها پوشش داده شده‌اند.' })));

    // ownership
    const ow = obj(r.ownership);
    rkGrid($('readiness-ownership-kv'), ['total', 'needs_manual_review', 'confirmed_transfers'].map((k) => [k, numOrUndef(ow[k])]));
    const ox = $('readiness-ownership-extra');
    clear(ox);
    if (ow.available === false) ox.append(el('p', { class: 'muted small', text: 'تحلیل مالکیت سرویس‌ها در این بکاپ در دسترس نبود.' }));
    const ot = countTable(ow.by_decision, (k) => codeLabel(L.ownership_decisions, k), 'تصمیم', 'readiness-decisions');
    if (ot) ox.append(ot);
    const ar = ow.admin_review && typeof ow.admin_review === 'object' ? ow.admin_review : null;
    if (ar) {
      // Administrator decisions are shown beside (never inside) proven ownership.
      const fcl = (k) => own(own(L.ownership_review || {}, 'final_class') || {}, k) || rkLabel(k);
      const tile = (key, cls, label) => el('div', { class: 'card stat own-card ' + cls, 'data-testid': 'readiness-admin-' + key },
        el('div', { class: 'stat-label', text: label }),
        el('div', { class: 'stat-value num', 'data-value': String(Number(ar[key]) || 0), text: fmtNum(Number(ar[key]) || 0) }));
      ox.append(el('div', { class: 'readiness-mini', 'data-testid': 'readiness-admin-review' },
        el('h3', { text: rkLabel('admin_review') }),
        el('div', { class: 'stat-grid own-summary' },
          tile('proven', 'own-proven', fcl('PROVEN')),
          tile('admin_attested', 'own-attested', fcl('ADMIN_ATTESTED')),
          tile('pending', 'own-pending', fcl('PENDING')),
          tile('rejected', 'own-rejected', fcl('REJECTED')),
          tile('quarantined', 'own-quarantined', fcl('QUARANTINED'))),
        el('p', { class: 'small warn-text', text: 'تأیید مدیر اثبات مالکیت نیست و جدا از «مالکیت اثبات‌شده» شمرده می‌شود.' }),
        el('dl', { class: 'kv-grid' },
          el('dt', null, labelWithCode(rkLabel('stale'), 'stale')), el('dd', { text: fmtNum(Number(ar.stale) || 0) }),
          el('dt', null, labelWithCode(rkLabel('sealed'), 'sealed')), el('dd', null, yesNo(ar.sealed)))));
    }

    // panels
    const pn = obj(r.panels);
    rkGrid($('readiness-panels-kv'), [
      ...['registry_rows', 'codes_in_invoices', 'mapped', 'pending_mapping'].map((k) => [k, numOrUndef(pn[k])]),
      ['status', pn.status === undefined ? undefined : labelWithCode(codeLabel(L.panel_status, pn.status), pn.status)],
    ]);

    // review
    const rv = Object.assign({}, obj(r.review));
    if (rv.owner_decisions_needed === undefined && r.owner_decisions_needed !== undefined) rv.owner_decisions_needed = r.owner_decisions_needed;
    const stats = $('readiness-review-stats');
    clear(stats);
    for (const k of ['total_items', 'distinct_records', 'blocker_items', 'auto_resolved_with_evidence', 'resolvable_by_code', 'archive_only',
      'owner_decision_items', 'owner_decisions_needed', 'adoption_stage']) {
      if (rv[k] === undefined || (rv[k] && typeof rv[k] === 'object')) continue;
      stats.append(el('div', { class: 'card stat', 'data-testid': 'readiness-stat-' + k },
        el('div', { class: 'stat-label', text: rkLabel(k) }), el('div', { class: 'stat-value num', text: fmtNum(rv[k]) })));
    }
    const tb = $('readiness-review-groups-tbody');
    clear(tb);
    const groups = objList(rv.by_group);
    if (!groups.length) tb.append(el('tr', null, el('td', { colspan: '7', class: 'muted', text: 'گروهی ثبت نشده است.' })));
    for (const g of groups) {
      const gc = g.group === undefined || g.group === null ? '' : String(g.group);
      tb.append(el('tr', { 'data-group': gc },
        el('td', null, labelWithCode(codeLabel(L.review_groups, gc, g.label_fa), gc)),
        ...['items', 'records', 'resolvable_by_code', 'archive_only', 'owner_decision', 'adoption_time']
          .map((k) => el('td', { class: 'num', text: fmtNum(g[k]) }))));
    }

    // owner decisions
    const od = objList(r.owner_decisions);
    $('readiness-owner-decision-count').textContent = fmtNum(od.length);
    const ol = $('readiness-owner-decision-list');
    clear(ol);
    if (!od.length) ol.append(el('li', { class: 'none', text: 'تصمیمی ثبت نشده است.' }));
    for (const d of od) {
      ol.append(el('li', { 'data-decision': d.id === undefined ? null : String(d.id), 'data-blocking': d.blocking === true ? 'true' : 'false' },
        d.blocking === true ? el('span', { class: 'pill pill-bad', text: 'مسدودکننده' }) : null,
        el('span', { class: 'msg' }, el('strong', { text: String(d.title_fa || '—') }),
          d.detail_fa ? el('span', { class: 'block small muted', text: String(d.detail_fa) }) : null),
        d.id !== undefined && d.id !== null ? el('span', { class: 'error-code', text: String(d.id) }) : null));
    }

    // import blockers
    const ib = objList(r.import_blockers);
    $('readiness-import-blocker-count').textContent = fmtNum(ib.length);
    codedItems($('readiness-import-blocker-list'), ib, 'مانعی ثبت نشده است (Importer همچنان ساخته نشده است).', L.import_blockers);
  }

  // ------------------------------------------------------------------ coverage
  const COV_STATUSES = ['executed', 'archived', 'review', 'blocked', 'empty', 'excluded'];
  const COV_CLASSES = ['OPERATIONAL', 'ARCHIVE', 'REVIEW', 'CONFIGURATION', 'EXCLUDED'];
  const isUncovered = (c) => c.status === 'blocked' || !c.class;

  onShow.coverage = async function () {
    $('coverage-body').hidden = true;
    $('coverage-empty').hidden = false;
    $('download-coverage-csv').hidden = true;
    $('coverage-empty').textContent = notReadyMessage('پوشش جدول‌ها');
    if (!S.job || S.job.status !== 'completed') return;
    $('coverage-empty').textContent = 'در حال بارگذاری…';
    try {
      const r = await ensureReport();
      if (S.view !== 'coverage' || !r) return;
      renderCoverage(r.coverage || []);
      const a = $('download-coverage-csv');
      a.href = jobPath(S.job.id, '/coverage.csv');
      a.hidden = false;
      $('coverage-empty').hidden = true;
      $('coverage-body').hidden = false;
    } catch (e) {
      $('coverage-empty').textContent = notReadyMessage('پوشش جدول‌ها');
      if (e.status !== 401) showError(e);
    }
  };

  function fillSelect(sel, values, labelOf) {
    const keep = sel.value;
    clear(sel);
    sel.append(el('option', { value: '', text: 'همه' }));
    for (const v of values) sel.append(el('option', { value: v, text: labelOf(v) }));
    if (keep && values.includes(keep)) sel.value = keep;
  }

  function renderCoverage(coverage) {
    const rows = coverage.filter((c) => c && typeof c === 'object');
    // summary cards
    const sum = $('coverage-summary');
    clear(sum);
    const card = (tid, label, n, cls) =>
      el('div', { class: 'card stat ' + (cls || ''), 'data-testid': tid },
        el('div', { class: 'stat-label', text: label }), el('div', { class: 'stat-value num', text: fmtNum(n) }));
    sum.append(card('cov-total', 'کل جدول‌ها', rows.length), card('cov-nonempty', 'جدول‌های دارای داده', rows.filter((c) => Number(c.rows) > 0).length));
    const classes = [...new Set([...COV_CLASSES.filter((k) => rows.some((c) => c.class === k)), ...rows.map((c) => c.class || '')])];
    for (const k of classes) {
      const n = rows.filter((c) => (c.class || '') === k).length;
      if (n) sum.append(card('cov-class-' + (k || 'none'), 'طبقه: ' + classLabel(k || null), n, k ? '' : 'is-bad'));
    }
    const statuses = [...new Set([...COV_STATUSES.filter((s) => rows.some((c) => c.status === s)), ...rows.map((c) => String(c.status))])];
    for (const s of statuses) {
      const n = rows.filter((c) => String(c.status) === s).length;
      if (n) sum.append(card('cov-status-' + s, 'وضعیت: ' + covStatusLabel(s), n, s === 'blocked' ? 'is-bad' : ''));
    }

    // uncovered
    const un = rows.filter(isUncovered);
    const ul = $('uncovered-list');
    clear(ul);
    $('uncovered-count').textContent = fmtNum(un.length);
    $('uncovered-card').classList.toggle('is-clear', !un.length);
    if (!un.length) ul.append(el('li', { class: 'none', text: 'همه جدول‌ها طبقه‌بندی و تعیین تکلیف شده‌اند.' }));
    for (const c of un) {
      ul.append(el('li', { 'data-table': c.table, 'data-severity': 'blocker' },
        el('bdi', { dir: 'ltr', class: 'mono', text: c.table }),
        el('span', { class: 'pill num pill-bad', text: fmtNum(c.rows) + ' ردیف' }),
        el('span', { class: 'msg' },
          !c.class ? 'جدول در فهرست جدول‌های شناخته‌شده نیست؛ معنی آن ثابت نشده و هیچ داده‌ای از آن منتقل نشد.' : own(L.coverage_status_help, 'blocked') || '',
          c.reason || c.reason_blocked ? el('span', { class: 'block small muted ltr-text', text: [c.reason, c.reason_blocked].filter(Boolean).join(' — ') }) : null)));
    }

    fillSelect($('coverage-class'), classes, (k) => classLabel(k || null));
    fillSelect($('coverage-status'), statuses, covStatusLabel);
    S.coverage = rows;
    renderCoverageTable();
  }

  function renderCoverageTable() {
    const rows = S.coverage || [];
    const fc = $('coverage-class').value;
    const fs = $('coverage-status').value;
    const shown = rows.filter((c) => (!fc || (c.class || '') === fc) && (!fs || String(c.status) === fs));
    const tbody = $('coverage-tbody');
    clear(tbody);
    for (const c of shown) {
      const dropped = c.dropped_secret_columns || [];
      const counts = el('div', { class: 'sub-stats small' });
      for (const [k, lbl] of [['exported', 'خروجی'], ['archived', 'آرشیو'], ['review', 'بررسی'], ['skipped', 'کنار']]) {
        counts.append(el('span', { class: Number(c[k]) ? '' : 'muted' }, lbl + ': ', el('b', { class: 'num', text: fmtNum(c[k] || 0) })));
      }
      const explain = el('div', { class: 'small' });
      if (c.purpose_fa) explain.append(el('div', { text: c.purpose_fa }));
      if (c.status && own(L.coverage_status_help, c.status)) explain.append(el('div', { class: 'muted', text: own(L.coverage_status_help, c.status) }));
      if (c.reason) explain.append(el('div', { class: 'muted ltr-text' }, el('span', { text: 'توضیح: ' }), el('span', { text: c.reason })));
      if (c.reason_blocked) explain.append(el('div', { class: 'warn-text ltr-text', text: c.reason_blocked }));
      if (c.nexa_target) explain.append(el('div', { class: 'muted' }, 'مقصد NEXA: ', el('bdi', { dir: 'ltr', class: 'mono', text: c.nexa_target })));
      const qs = c.open_questions || [];
      const cols = el('div', { class: 'sub-stats small' },
        el('span', { title: dropped.length ? dropped.join(', ') : null }, 'حساس حذف‌شده: ', el('b', { class: 'num', text: fmtNum(dropped.length) })),
        el('span', null, 'خانه پوشانده: ', el('b', { class: 'num', text: fmtNum(c.redacted_cells || 0) })));
      const unm = c.unmapped_columns || [];
      if (unm.length) explain.append(el('div', { class: 'muted' }, 'ستون‌های نگاشت‌نشده: ', tagList(unm, 'is-warn')));
      if (qs.length) explain.append(el('div', { class: 'muted' }, 'پرسش‌های باز:', el('ul', { class: 'check-list small' }, qs.map((q) => el('li', { class: 'ltr-text', text: stringify(q) })))));
      tbody.append(el('tr', { 'data-table': c.table, 'data-status': c.status, 'data-class': c.class || '' },
        el('td', null, el('bdi', { dir: 'ltr', class: 'mono', text: c.table })),
        el('td', null, labelWithCode(classLabel(c.class, c.class_fa), c.class || null)),
        el('td', null, el('span', { class: 'badge cov-' + c.status, 'data-testid': 'coverage-status-badge', text: covStatusLabel(c.status) })),
        el('td', { class: 'num', text: fmtNum(c.rows) }),
        el('td', null, counts),
        el('td', null, cols)));
      tbody.append(el('tr', { class: 'cov-detail' }, el('td', { colspan: 6 }, explain)));
    }
    if (!shown.length) tbody.append(el('tr', null, el('td', { colspan: 6, class: 'empty', text: 'جدولی با این فیلتر وجود ندارد.' })));
    $('coverage-shown').textContent = 'نمایش ' + fmtNum(shown.length) + ' از ' + fmtNum(rows.length) + ' جدول';
  }

  // ------------------------------------------------------------------ review
  const reviewKinds = new Set();
  onShow.review = function () {
    const ok = S.job && S.job.status === 'completed';
    $('review-body').hidden = !ok;
    $('review-empty').hidden = ok;
    $('review-empty').textContent = notReadyMessage('صف بررسی');
    if (ok) loadReview(0);
  };

  async function loadReview(offset) {
    const j = S.job;
    if (!j) return;
    const limit = Number($('review-limit').value) || 50;
    const params = new URLSearchParams({ offset: String(offset), limit: String(limit) });
    const sev = $('review-severity').value;
    const kind = $('review-kind').value.trim();
    if (sev) params.set('severity', sev);
    if (kind) params.set('kind', kind);
    const tbody = $('review-tbody');
    $('review-table').setAttribute('aria-busy', 'true');
    try {
      const r = await api('GET', jobPath(j.id, '/review?' + params.toString()));
      if (S.view !== 'review') return;
      S.reviewOffset = offset;
      clear(tbody);
      const items = r.items || [];
      for (const it of items) {
        if (it.kind) reviewKinds.add(it.kind);
        tbody.append(el('tr', { 'data-severity': it.severity, 'data-kind': it.kind },
          el('td', null, el('span', { class: 'badge st-' + it.severity, text: SEVERITY_FA[it.severity] || it.severity })),
          el('td', null, labelWithCode(own(L.review_kinds, it.kind) || humanise(it.kind || ''), it.kind)),
          el('td', null, labelWithCode(own(L.review_codes, it.code) || own(L.blocker_codes, it.code) || humanise(it.code || ''), it.code)),
          el('td', { text: it.message || '' }),
          el('td', { text: it.entity ? entityLabel(it.entity) : '' }),
          el('td', { class: 'ltr small', text: stringify(it.source_ref) }),
          el('td', null, kvChips(it.data))));
      }
      if (!items.length) tbody.append(el('tr', null, el('td', { colspan: 7, class: 'empty', text: 'موردی برای نمایش وجود ندارد.' })));
      renderPager($('review-pager'), r.total || 0, offset, limit, loadReview);
      const dl = $('review-kinds');
      clear(dl);
      for (const k of reviewKinds) dl.append(el('option', { value: k }));
    } catch (e) {
      if (e.status !== 401) showError(e);
    } finally {
      $('review-table').removeAttribute('aria-busy');
    }
  }

  function kvChips(data) {
    const box = el('div', { class: 'kv-chips' });
    if (!data || typeof data !== 'object') {
      if (data !== undefined && data !== null) box.append(el('span', { class: 'kv-chip', text: truncate(stringify(data), 120) }));
      return box;
    }
    const keys = Object.keys(data);
    for (const k of keys.slice(0, 24)) {
      const full = stringify(data[k]);
      box.append(el('span', { class: 'kv-chip', title: k + ': ' + truncate(full, 2000) },
        el('span', { class: 'k', text: k }), el('span', { class: 'v', text: truncate(full, 60) })));
    }
    if (keys.length > 24) box.append(el('span', { class: 'muted small', text: '+' + fmtNum(keys.length - 24) }));
    return box;
  }

  // ------------------------------------------------------------------ preview
  onShow.preview = async function () {
    const ok = S.job && S.job.status === 'completed';
    $('preview-body').hidden = !ok;
    $('preview-empty').hidden = ok;
    $('preview-empty').textContent = notReadyMessage('پیش‌نمایش');
    if (!ok) return;
    const sel = $('preview-entity');
    const keep = sel.value;
    let keys = Object.keys(ENTITY_FA);
    try {
      const r = await ensureReport();
      if (r && r.counts && Object.keys(r.counts).length) keys = Object.keys(r.counts);
    } catch (_) {
      /* fall back to the known list */
    }
    clear(sel);
    for (const k of keys) sel.append(el('option', { value: k, text: entityLabel(k) }));
    if (keep && keys.includes(keep)) sel.value = keep;
    loadPreview();
  };

  async function loadPreview() {
    const j = S.job;
    const entity = $('preview-entity').value;
    if (!j || !entity) return;
    const table = $('preview-table');
    const msg = $('preview-msg');
    table.setAttribute('aria-busy', 'true');
    try {
      const r = await api('GET', jobPath(j.id, '/preview/' + encodeURIComponent(entity) + '?limit=20'));
      const items = (r && r.items) || [];
      const cols = [];
      const seen = new Set();
      for (const it of items) {
        if (!it || typeof it !== 'object') continue;
        for (const k of Object.keys(it)) {
          if (!seen.has(k)) {
            seen.add(k);
            cols.push(k);
          }
        }
      }
      const head = $('preview-head');
      const tbody = $('preview-tbody');
      clear(head);
      clear(tbody);
      for (const c of cols) head.append(el('th', { scope: 'col', class: 'ltr', text: c }));
      for (const it of items) {
        const tr = el('tr');
        for (const c of cols) {
          const v = it ? it[c] : undefined;
          const isObj = v !== null && typeof v === 'object';
          const s = v === undefined || v === null ? '' : stringify(v);
          tr.append(el('td', { class: 'ltr' + (isObj ? ' cell-json' : ''), title: s.length > 40 ? truncate(s, 2000) : null, text: truncate(s, 200) }));
        }
        tbody.append(tr);
      }
      table.hidden = items.length === 0;
      msg.hidden = items.length > 0;
      msg.textContent = 'رکوردی برای این بخش وجود ندارد.';
    } catch (e) {
      table.hidden = true;
      msg.hidden = false;
      msg.textContent = e.message;
      if (e.status !== 404 && e.status !== 401) showError(e);
    } finally {
      table.removeAttribute('aria-busy');
    }
  }

  // ------------------------------------------------------------------ download
  onShow.download = function () {
    renderDownload();
    if (S.jobId) refreshJob();
  };

  function renderDownload() {
    const j = S.job;
    const ok = j && j.status === 'completed' && j.has_package;
    $('download-body').hidden = !j;
    $('download-empty').hidden = !!ok;
    $('download-empty').textContent = j && j.status === 'completed' && !j.has_package
      ? 'بسته خروجی برای این عملیات موجود نیست.'
      : notReadyMessage('بسته');
    if (!j) return;
    const pkg = $('download-pkg');
    const keyLink = $('download-key');
    pkg.closest('.card').hidden = !ok;
    if (ok) pkg.href = jobPath(j.id, '/download');
    else pkg.removeAttribute('href');
    $('key-card').hidden = !(ok && j.has_key);
    if (ok && j.has_key) keyLink.href = jobPath(j.id, '/key');
    else keyLink.removeAttribute('href');
    const s = j.summary || {};
    kvGrid($('package-meta'), [
      ['فایل منبع', el('bdi', { dir: 'ltr', text: j.filename })],
      ['شناسه Import', s.import_id || undefined, 'mono'],
      ['آمادگی', s.readiness ? (s.readiness === 'ready' ? 'آماده بررسی Importer' : 'مسدود') : undefined],
      ['موارد مسدودکننده', s.blocker_count !== undefined ? fmtNum(s.blocker_count) : undefined],
      ['موارد نیازمند بررسی', s.review_count !== undefined ? fmtNum(s.review_count) : undefined],
      ['رمزنگاری بسته', j.has_key ? 'کلید تصادفی جداگانه' : 'رمز عبور اپراتور'],
    ]);
    if (ok) loadPackagePreview(j.id);
    else $('package-preview').hidden = true;
  }

  function fileSection(path) {
    const m = /^records\/(?:([^/]+)\/)?([^/]+)\.jsonl$/.exec(path);
    if (m) return m[1] ? entityLabel(m[1]) + ': ' + m[2] : entityLabel(m[2]);
    if (path.startsWith('reports/')) return 'گزارش';
    if (path === 'manifest.json') return 'مشخصات بسته (Manifest)';
    return '—';
  }

  async function loadPackagePreview(jobId) {
    if (S.pkgPreviewFor === jobId && !$('package-preview').hidden) return;
    try {
      const p = await api('GET', jobPath(jobId, '/package-preview'));
      if (!S.job || S.job.id !== jobId || S.view !== 'download') return;
      S.pkgPreviewFor = jobId;
      renderPackagePreview(p);
    } catch (e) {
      $('package-preview').hidden = true;
      if (e.status !== 404 && e.status !== 401) showError(e);
    }
  }

  function renderPackagePreview(p) {
    const m = p.manifest || {};
    const comp = m.compatibility || {};
    const conv = m.converter || {};
    $('contract-version').textContent = p.package_schema_version || '—';
    const files = p.files || [];
    const totalRecords = files.reduce((a, f) => a + (Number(f.records) || 0), 0);
    kvGrid($('package-preview-meta'), [
      ['قرارداد', el('bdi', { dir: 'ltr', class: 'mono', text: (p.package_schema || m.package_schema || '—') + ' @ ' + (p.package_schema_version || '—') })],
      ['آمادگی', own(L.readiness, p.readiness) || p.readiness || '—', p.readiness === 'ready' ? 'ok-text' : 'bad-text'],
      ['نسخه نگاشت', m.mapping_version || undefined, 'mono'],
      ['مبدل', conv.name ? conv.name + ' ' + (conv.version || '') : undefined, 'mono'],
      ['حداقل نسخه Importer', comp.min_importer_version || undefined, 'mono'],
      ['تأییدشده توسط Importer', p.verified_by_importer ? 'بله' : 'خیر — Importer هنوز ساخته نشده', p.verified_by_importer ? '' : 'bad-text'],
      ['زمان ساخت', m.created_at || undefined, 'mono'],
      ['واحد اعلام‌شده', m.money ? (UNIT_FA[m.money.declared_unit] || 'تعیین‌نشده') : undefined],
      ['تعداد فایل‌ها', fmtNum(files.length)],
      ['مجموع رکوردها', fmtNum(totalRecords)],
      ['حجم بسته رمزنگاری‌شده', fmtBytes(p.package_bytes)],
    ]);
    const bl = $('package-preview-blockers');
    clear(bl);
    const blockers = p.blockers || [];
    if (blockers.length) {
      bl.append(el('h3', { class: 'small', text: 'موارد مسدودکننده بسته' }),
        el('ul', { class: 'issue-list' }, blockers.map((b) => el('li', { 'data-code': b.code },
          el('span', { class: 'msg', text: own(L.blocker_codes, b.code) || b.message || humanise(b.code || '') }),
          el('span', { class: 'error-code', text: b.code || '' })))));
    }
    const tbody = $('package-files-tbody');
    clear(tbody);
    for (const f of files) {
      tbody.append(el('tr', { 'data-path': f.path },
        el('td', { class: 'ltr mono', text: f.path }),
        el('td', { text: fileSection(String(f.path)) }),
        el('td', { class: 'num', text: f.records === null || f.records === undefined ? '—' : fmtNum(f.records) }),
        el('td', { class: 'num', text: fmtBytes(f.bytes) }),
        el('td', { class: 'ltr mono small', title: f.sha256 || null, text: f.sha256 ? String(f.sha256).slice(0, 16) + '…' : '—' })));
    }
    $('package-preview').hidden = false;
  }

  async function deleteJob() {
    const j = S.job;
    if (!j) return;
    const ok = await confirmDialog(
      'حذف عملیات',
      'عملیات و همه فایل‌های موقت آن (شامل بسته و کلید) از این کامپیوتر حذف شود؟ اگر بسته و کلید را ذخیره نکرده‌اید، ابتدا آن‌ها را دانلود کنید.',
      'حذف');
    if (!ok) return;
    try {
      await api('DELETE', jobPath(j.id));
    } catch (e) {
      if (e.status !== 404) {
        if (e.status !== 401) showError(e);
        return;
      }
    }
    stopPolling();
    setCurrentJob(null);
    toast('عملیات و فایل‌های موقت حذف شد.');
    go('dashboard');
  }

  // ------------------------------------------------------------------ history
  const HISTORY_PAGE = 25;
  onShow.history = async function () {
    try {
      const r = await api('GET', '/api/history');
      S.historyItems = (r && r.items) || [];
      S.historyPage = 0;
      renderHistory();
    } catch (e) {
      if (e.status !== 401) showError(e);
    }
  };

  function countsSummary(counts) {
    const box = el('div', { class: 'kv-chips' });
    if (!counts || typeof counts !== 'object') {
      box.append(el('span', { class: 'muted', text: '—' }));
      return box;
    }
    for (const k of Object.keys(counts)) {
      const c = counts[k];
      const v = c && typeof c === 'object' ? c.converted : c;
      box.append(el('span', { class: 'kv-chip' }, el('span', { class: 'k', text: entityLabel(k) }), el('b', { class: 'num', text: fmtNum(v) })));
    }
    return box;
  }

  function renderHistory() {
    const items = S.historyItems;
    const tbody = $('history-tbody');
    clear(tbody);
    $('history-empty').hidden = items.length > 0;
    $('history-table').hidden = items.length === 0;
    $('history-clear').disabled = items.length === 0;
    const start = S.historyPage * HISTORY_PAGE;
    for (const h of items.slice(start, start + HISTORY_PAGE)) {
      const sp = h.schema_profile;
      const spText = sp && typeof sp === 'object' ? sp.label || sp.id || '—' : sp || '—';
      tbody.append(el('tr', { 'data-job-id': h.job_id },
        el('td', { class: 'num', text: fmtDate(h.started_at) }),
        el('td', { class: 'num', text: fmtDate(h.finished_at) }),
        el('td', null, statusBadge(h.status), h.error_code ? el('div', { class: 'error-code', text: h.error_code }) : null),
        el('td', null, h.readiness ? el('span', { class: 'badge st-' + h.readiness, text: h.readiness === 'ready' ? 'آماده' : 'مسدود' }) : '—'),
        el('td', { class: 'num', text: fmtBytes(h.archive_bytes) }),
        el('td', { text: spText }),
        el('td', { class: 'mono', text: h.source_fingerprint_prefix || '—' }),
        el('td', { class: 'num', text: fmtNum(h.blocker_count) }),
        el('td', { class: 'num', text: fmtNum(h.review_count) }),
        el('td', null, countsSummary(h.counts))));
    }
    renderPager($('history-pager'), items.length, start, HISTORY_PAGE, (off) => {
      S.historyPage = Math.floor(off / HISTORY_PAGE);
      renderHistory();
    });
  }

  async function clearHistory() {
    const ok = await confirmDialog('پاک کردن تاریخچه', 'همه رکوردهای تاریخچه پاک شود؟ این کار قابل بازگشت نیست.', 'پاک کردن');
    if (!ok) return;
    try {
      await api('DELETE', '/api/history');
      toast('تاریخچه پاک شد.');
      onShow.history();
    } catch (e) {
      if (e.status !== 401) showError(e);
    }
  }

  // ------------------------------------------------------------------ bulk ownership review
  // Server-side filtering and pagination only (5,000+ services): the page never fetches all items.
  // Selection is either explicit keys or "all filtered results" minus excluded keys; the server
  // resolves it in a two-phase plan → commit flow. Owner details are shown only in the detail dialog.
  const OWN_PAGE = 100;
  const OWN_FILTERS = ['final_class', 'eligibility', 'review_state', 'panel', 'status_class', 'reason', 'live_status'];
  const OWN_CLASSES = ['PROVEN', 'ADMIN_ATTESTED', 'PENDING', 'REJECTED', 'QUARANTINED'];
  const OWN = {
    jobId: null,
    summary: null,
    facets: null,
    filter: {},
    q: '',
    offset: 0,
    total: 0,
    items: [],
    sel: { mode: 'keys', keys: new Set() },
    plan: null,
    planReq: null,
    livePoll: null,
    loadSeq: 0,
  };
  const ownPath = (suffix) => jobPath(OWN.jobId, '/ownership' + (suffix || ''));
  const OR = () => L.ownership_review || {};
  const ownLabel = (group, code) => codeLabel(own(OR(), group) || {}, code);
  function ownReasonLabel(code) {
    const c = String(code);
    return own(own(OR(), 'reason') || {}, c) || own(own(OR(), 'eligibility') || {}, c) || own(own(OR(), 'final_class') || {}, c)
      || own(L.review_codes, c) || own(L.ownership_decisions, c) || humanise(c);
  }
  function facetLabel(group, code) {
    if (group === 'panel') return code === '(none)' ? 'بدون پنل' : String(code);
    if (group === 'reason') return ownReasonLabel(code);
    return ownLabel(group, code);
  }
  function fmtWhen(v) {
    if (v === null || v === undefined || v === '') return '—';
    if (typeof v === 'number' || /^\d+(\.\d+)?$/.test(String(v))) return fmtDate(Number(v));
    const t = Date.parse(String(v));
    return Number.isFinite(t) ? fmtDate(t / 1000) : String(v);
  }
  const ownSealed = () => !!(OWN.summary && OWN.summary.sealed);

  onShow.ownership = function () {
    const ok = S.job && S.job.status === 'completed';
    $('own-body').hidden = !ok;
    $('own-empty').hidden = ok;
    $('own-empty').textContent = notReadyMessage('بررسی مالکیت سرویس‌ها');
    if (!ok) return;
    if (OWN.jobId !== S.job.id) {
      OWN.jobId = S.job.id;
      OWN.filter = {};
      OWN.q = '';
      OWN.offset = 0;
      OWN.summary = null;
      OWN.facets = null;
      ownClearSelection();
      $('own-q').value = '';
      $('own-audit-result').hidden = true;
      $('own-export-pass-form').hidden = true;
    }
    ownRefreshAll();
  };

  async function ownRefreshAll() {
    if (S.readinessFor === OWN.jobId) S.readinessFor = null;  // admin_review overlay may change
    await Promise.all([ownLoadSummary(), ownLoadFacets(), ownLoadItems(OWN.offset)]);
    ownLoadBatches();
    ownLoadAudit();
    ownLoadLive();
  }

  function ownHandleError(e) {
    if (e.status === 401) return;
    if (e.status === 404 && e.code === 'job.not_found') return jobVanished();
    showError(e);
  }

  // ---- summary
  async function ownLoadSummary() {
    try {
      const s = await api('GET', ownPath('/summary'));
      if (S.view !== 'ownership') return;
      OWN.summary = s;
      ownRenderSummary();
    } catch (e) {
      if (e.status === 404 && e.code !== 'job.not_found') {
        $('own-body').hidden = true;
        $('own-empty').hidden = false;
        $('own-empty').textContent = e.message || notReadyMessage('بررسی مالکیت سرویس‌ها');
        return;
      }
      ownHandleError(e);
    }
  }

  function ownRenderSummary() {
    const s = OWN.summary || {};
    const t = obj(s.totals);
    const box = $('own-summary');
    clear(box);
    const card = (cls, label, sub, cssCls) =>
      el('div', { class: 'card stat own-card ' + cssCls, 'data-testid': 'own-card-' + cls, 'data-class': cls },
        el('div', { class: 'stat-label', text: label }),
        el('div', { class: 'stat-value num', 'data-value': String(Number(t[cls]) || 0), text: fmtNum(Number(t[cls]) || 0) }),
        sub ? el('div', { class: 'own-card-sub small', text: sub }) : null);
    box.append(
      el('div', { class: 'card stat own-card own-card-total', 'data-testid': 'own-card-items' },
        el('div', { class: 'stat-label', text: 'کل سرویس‌ها' }),
        el('div', { class: 'stat-value num', 'data-value': String(Number(t.items) || 0), text: fmtNum(Number(t.items) || 0) })),
      card('PROVEN', ownLabel('final_class', 'PROVEN'), 'با شواهد بکاپ', 'own-proven'),
      card('ADMIN_ATTESTED', ownLabel('final_class', 'ADMIN_ATTESTED'), 'تصمیم مدیر — جدا از اثبات‌شده', 'own-attested'),
      card('PENDING', ownLabel('final_class', 'PENDING'), null, 'own-pending'),
      card('REJECTED', ownLabel('final_class', 'REJECTED'), null, 'own-rejected'),
      card('QUARANTINED', ownLabel('final_class', 'QUARANTINED'), 'قابل تأیید در این صفحه نیست', 'own-quarantined'));
    const meta = [];
    if (s.viewed !== undefined) meta.push('بازشده: ' + fmtNum(s.viewed));
    if (s.stale) meta.push('تصمیم‌های کهنه (پس از تبدیل دوباره): ' + fmtNum(s.stale));
    const live = obj(s.live);
    if (live.enabled && live.checked) meta.push('بررسی‌شده روی پنل: ' + fmtNum(live.checked));
    if (s.import_id) meta.push('شناسه Import: ' + String(s.import_id));
    $('own-summary-meta').textContent = meta.join(' — ');
    $('own-disclaimer-text').textContent = s.disclaimer_fa ? String(s.disclaimer_fa)
      : 'سرویس‌هایی که مدیر تأیید می‌کند همیشه با برچسب «تأیید مدیر» و جدا از مالکیت اثبات‌شده گزارش و صادر می‌شوند.';
    const sealed = !!s.sealed;
    const banner = $('own-sealed');
    banner.hidden = !sealed;
    banner.textContent = sealed ? 'تصمیم‌ها مهر و موم شده‌اند' + (s.sealed_at ? ' (' + fmtWhen(s.sealed_at) + ')' : '') + '؛ دیگر هیچ تغییری ممکن نیست.' : '';
    $('own-seal').disabled = sealed;
    ownSyncSelection();
  }

  // ---- facets / filters
  async function ownLoadFacets() {
    try {
      const f = await api('GET', ownPath('/facets'));
      if (S.view !== 'ownership') return;
      OWN.facets = f || {};
      ownRenderFacets();
    } catch (e) {
      if (e.status !== 404) ownHandleError(e);
    }
  }

  function ownRenderFacets() {
    const f = OWN.facets || {};
    for (const k of OWN_FILTERS) {
      const sel = $('own-f-' + k);
      const cur = OWN.filter[k] || '';
      clear(sel);
      sel.append(el('option', { value: '', text: 'همه' }));
      const vals = objList(f[k]);
      let seen = false;
      for (const v of vals) {
        const value = v.value === null || v.value === undefined ? '' : String(v.value);
        if (value === '') continue;
        if (value === cur) seen = true;
        sel.append(el('option', { value, text: facetLabel(k, value) + ' (' + fmtNum(Number(v.count) || 0) + ')' }));
      }
      if (cur && !seen) sel.append(el('option', { value: cur, text: facetLabel(k, cur) + ' (۰)' }));
      sel.value = cur;
    }
    const lp = $('own-live-panel');
    const keep = lp.value;
    clear(lp);
    for (const v of objList(f.panel)) {
      if (v.value === null || v.value === undefined || v.value === '' || v.value === '(none)') continue;
      lp.append(el('option', { value: String(v.value), text: String(v.value) + ' (' + fmtNum(Number(v.count) || 0) + ')' }));
    }
    if (keep && [...lp.options].some((o) => o.value === keep)) lp.value = keep;
  }

  function ownReadFilters() {
    const f = {};
    for (const k of OWN_FILTERS) {
      const v = $('own-f-' + k).value;
      if (v) f[k] = v;
    }
    OWN.filter = f;
    OWN.q = $('own-q').value.trim();
  }
  function ownQuery(offset, limit) {
    const p = new URLSearchParams();
    for (const k of OWN_FILTERS) if (OWN.filter[k]) p.set(k, OWN.filter[k]);
    if (OWN.q) p.set('q', OWN.q);
    p.set('offset', String(offset));
    p.set('limit', String(limit));
    return p.toString();
  }
  /** The applied filter in the plan/live-check body shape (arrays of strings, q string). */
  function ownFilterBody() {
    const f = {};
    for (const k of OWN_FILTERS) if (OWN.filter[k]) f[k] = [OWN.filter[k]];
    if (OWN.q) f.q = OWN.q;
    return f;
  }

  // ---- items table
  async function ownLoadItems(offset) {
    const seq = ++OWN.loadSeq;
    const tbody = $('own-tbody');
    try {
      const r = await api('GET', ownPath('/items?' + ownQuery(offset, OWN_PAGE)));
      if (seq !== OWN.loadSeq || S.view !== 'ownership') return;
      OWN.items = objList(r && r.items);
      OWN.total = Number(r && r.total) || 0;
      OWN.offset = Number(r && r.offset) || 0;
      if (OWN.offset > 0 && OWN.offset >= OWN.total) return ownLoadItems(Math.max(0, OWN.total - OWN_PAGE));
      ownRenderItems();
    } catch (e) {
      if (seq !== OWN.loadSeq) return;
      clear(tbody);
      tbody.append(el('tr', null, el('td', { colspan: 8, class: 'empty', text: 'فهرست سرویس‌ها بارگذاری نشد.' })));
      if (e.status !== 404) ownHandleError(e);
    }
  }

  function ownIsSelected(key) {
    const s = OWN.sel;
    return s.mode === 'filter' ? !s.exclude.has(key) : s.keys.has(key);
  }
  function ownSetSelected(key, on) {
    const s = OWN.sel;
    if (s.mode === 'filter') {
      if (on) s.exclude.delete(key);
      else s.exclude.add(key);
    } else if (on) s.keys.add(key);
    else s.keys.delete(key);
  }
  function ownSelCount() {
    const s = OWN.sel;
    return s.mode === 'filter' ? Math.max(0, s.total - s.exclude.size) : s.keys.size;
  }
  function ownClearSelection() {
    OWN.sel = { mode: 'keys', keys: new Set() };
  }

  function ownRenderItems() {
    const tbody = $('own-tbody');
    clear(tbody);
    for (const it of OWN.items) {
      const key = String(it.key);
      const checked = ownIsSelected(key);
      const box = el('input', { type: 'checkbox', class: 'own-row-check', 'data-key': key, 'aria-label': 'انتخاب فاکتور ' + String(it.invoice_key || key) });
      box.checked = checked;
      const reasons = Array.isArray(it.reasons) ? it.reasons : [];
      const fc = String(it.final_class || '');
      tbody.append(el('tr', {
        'data-key': key, 'data-final-class': fc, 'data-eligibility': String(it.eligibility || ''),
        'data-review-state': String(it.review_state || ''), class: (checked ? 'is-selected' : '') + (it.viewed ? '' : ' is-unviewed'),
      },
      el('td', { class: 'own-check' }, box),
      el('td', null,
        el('button', { type: 'button', class: 'linkish mono', 'data-action': 'own-detail', 'data-key': key, 'data-testid': 'own-open-detail', text: String(it.invoice_key || key) }),
        it.stale ? el('span', { class: 'pill pill-warn', text: 'کهنه' }) : null,
        it.viewed ? null : el('span', { class: 'sr-only', text: ' (هنوز باز نشده)' })),
      el('td', null, el('bdi', { dir: 'ltr', text: it.panel_code ? String(it.panel_code) : '—' })),
      el('td', null, labelWithCode(ownLabel('status_class', it.status_class), it.status_raw || it.status_class)),
      el('td', null, el('span', { class: 'badge own-elig-' + String(it.eligibility || ''), text: ownLabel('eligibility', it.eligibility) })),
      el('td', null, el('span', { class: 'badge own-fc own-fc-' + fc, 'data-testid': 'own-final-class', text: ownLabel('final_class', fc) })),
      el('td', null, el('span', { class: 'small', text: ownLabel('live_status', it.live_status || 'NOT_CHECKED') })),
      el('td', { class: 'small' }, reasons.length
        ? el('span', { title: reasons.map(ownReasonLabel).join('، ') }, ownReasonLabel(reasons[0]), reasons.length > 1 ? ' +' + fmtNum(reasons.length - 1) : '')
        : '—')));
    }
    if (!OWN.items.length) tbody.append(el('tr', null, el('td', { colspan: 8, class: 'empty', text: 'سرویسی با این فیلتر وجود ندارد.' })));
    renderPager($('own-pager'), OWN.total, OWN.offset, OWN_PAGE, (off) => ownLoadItems(off));
    ownSyncSelection();
  }

  function ownSyncSelection() {
    const n = ownSelCount();
    const s = OWN.sel;
    const info = $('own-sel-count');
    info.setAttribute('data-count', String(n));
    info.setAttribute('data-mode', s.mode);
    if (s.mode === 'filter') {
      info.textContent = 'همه ' + fmtNum(s.total) + ' نتیجه فیلترشده انتخاب شده است' +
        (s.exclude.size ? ' (به‌جز ' + fmtNum(s.exclude.size) + ' مورد)' : '') + ' — ' + fmtNum(n) + ' مورد.';
    } else {
      info.textContent = n ? fmtNum(n) + ' مورد انتخاب شده است.' : 'هیچ موردی انتخاب نشده است.';
    }
    const all = $('own-select-filtered');
    all.hidden = s.mode === 'filter' || OWN.total <= 0;
    all.textContent = 'انتخاب همه ' + fmtNum(OWN.total) + ' نتیجه فیلترشده';
    $('own-clear-sel').hidden = n === 0 && s.mode !== 'filter';
    const page = $('own-check-page');
    const onPage = OWN.items.map((it) => String(it.key));
    const selOnPage = onPage.filter(ownIsSelected).length;
    page.checked = onPage.length > 0 && selOnPage === onPage.length;
    page.indeterminate = selOnPage > 0 && selOnPage < onPage.length;
    page.disabled = onPage.length === 0;
    const disabled = n === 0 || ownSealed();
    for (const id of ['own-act-approve', 'own-act-reject', 'own-act-reset']) $(id).disabled = disabled;
  }

  function ownSelectionBody() {
    const s = OWN.sel;
    if (s.mode === 'filter') return { mode: 'filter', filter: s.filter, exclude_keys: [...s.exclude] };
    return { mode: 'keys', keys: [...s.keys] };
  }

  // ---- detail dialog
  async function ownOpenDetail(key, opener) {
    const d = $('own-detail');
    const body = $('own-detail-body');
    clear(body);
    body.append(el('p', { class: 'muted', text: 'در حال بارگذاری…' }));
    OWN.detailOpener = opener || null;
    if (typeof d.showModal === 'function' && !d.open) d.showModal();
    $('own-detail-close').focus();
    try {
      const it = await api('GET', ownPath('/items/' + encodeURIComponent(key)));
      ownRenderDetail(it);
      const row = OWN.items.find((x) => String(x.key) === key);
      if (row && !row.viewed) {
        row.viewed = true;
        const tr = $('own-tbody').querySelector('tr[data-key="' + CSS.escape(key) + '"]');
        if (tr) tr.classList.remove('is-unviewed');
      }
    } catch (e) {
      clear(body);
      body.append(el('p', { class: 'field-error', text: e.message || 'جزئیات بارگذاری نشد.' }));
      if (e.status === 401) return;
    }
  }
  function ownCloseDetail() {
    const d = $('own-detail');
    if (d.open) d.close();
  }

  function ownRenderDetail(it) {
    const body = $('own-detail-body');
    clear(body);
    $('own-detail-title').textContent = 'جزئیات سرویس — ' + String(it.invoice_key || it.key || '');
    const ownr = it.owner && typeof it.owner === 'object' ? it.owner : null;
    const orig = it.original_owner && typeof it.original_owner === 'object' ? it.original_owner : null;
    const dl = el('dl', { class: 'kv-grid', 'data-testid': 'own-detail-kv' });
    const pairs = [
      ['شناسه فاکتور', el('bdi', { dir: 'ltr', class: 'mono', text: String(it.invoice_key || '—') })],
      ['نتیجه نهایی', labelWithCode(ownLabel('final_class', it.final_class), it.final_class)],
      ['تصمیم مبدل', labelWithCode(codeLabel(L.ownership_decisions, it.ownership_decision), it.ownership_decision)],
      ['اطمینان', it.confidence === undefined || it.confidence === null ? '—' : String(it.confidence)],
      ['قابلیت تصمیم', labelWithCode(ownLabel('eligibility', it.eligibility), it.eligibility)],
      ['وضعیت بررسی', labelWithCode(ownLabel('review_state', it.review_state), it.review_state)],
      ['مبنا', it.basis ? labelWithCode(ownLabel('basis', it.basis), it.basis) : '—'],
      ['پنل', el('bdi', { dir: 'ltr', text: it.panel_code ? String(it.panel_code) : '—' })],
      ['وضعیت فاکتور', labelWithCode(ownLabel('status_class', it.status_class), it.status_raw || it.status_class)],
      ['وضعیت روی پنل', ownLabel('live_status', it.live_status || 'NOT_CHECKED') + (it.live_checked_at ? ' — ' + fmtWhen(it.live_checked_at) : '')],
      ['مالک ثبت‌شده در فاکتور', ownr
        ? el('span', null, el('bdi', { dir: 'ltr', class: 'mono', text: String(ownr.telegram_user_id) }), ownr.in_user_table ? ' (در جدول کاربران)' : ' (در جدول کاربران نیست)')
        : '—'],
    ];
    if (orig) pairs.push(['مالک اولیه', el('bdi', { dir: 'ltr', class: 'mono', text: String(orig.telegram_user_id || stringify(orig)) })]);
    if (it.batch_id) pairs.push(['دسته', el('bdi', { dir: 'ltr', class: 'mono', text: String(it.batch_id) })]);
    if (it.stale) pairs.push(['هشدار', el('span', { class: 'warn-text', text: 'این تصمیم مربوط به نسخه قبلی رکورد است و معتبر صادر نمی‌شود.' })]);
    for (const [k, v] of pairs) dl.append(el('dt', { text: k }), v instanceof Node ? el('dd', null, v) : el('dd', { text: v }));
    body.append(dl);

    const reasons = Array.isArray(it.reasons) ? it.reasons : [];
    body.append(el('h3', { text: 'دلیل‌ها' }));
    const rl = el('ul', { class: 'issue-list' });
    if (!reasons.length) rl.append(el('li', { class: 'none', text: 'دلیلی ثبت نشده است.' }));
    for (const r of reasons) rl.append(el('li', { 'data-code': String(r) }, el('span', { class: 'msg', text: ownReasonLabel(r) }), el('span', { class: 'error-code', text: String(r) })));
    body.append(rl);

    const ev = objList(it.evidence);
    body.append(el('h3', { text: 'شواهد (' + fmtNum(ev.length) + ')' }));
    if (ev.length) {
      body.append(el('div', { class: 'table-wrap' }, el('table', { class: 'table table-compact', 'data-testid': 'own-detail-evidence' },
        el('thead', null, el('tr', null, ['جدول', 'کلید', 'نقش', 'رابطه', 'دلیل', 'نوع'].map((h) => el('th', { scope: 'col', text: h })))),
        el('tbody', null, ev.map((x) => el('tr', null,
          el('td', null, el('bdi', { dir: 'ltr', class: 'mono', text: stringify(x.source_table) })),
          el('td', null, el('bdi', { dir: 'ltr', class: 'mono', text: stringify(x.pk) })),
          el('td', { text: stringify(x.role) || '—' }),
          el('td', { text: stringify(x.relation) || '—' }),
          el('td', { text: x.reason ? ownReasonLabel(x.reason) : '—' }),
          el('td', null, el('bdi', { dir: 'ltr', text: stringify(x.type_raw) || '—' }))))))));
    } else body.append(el('p', { class: 'muted small', text: 'شاهدی ثبت نشده است.' }));

    const hist = objList(it.history);
    body.append(el('h3', { text: 'تاریخچه تصمیم‌ها' }));
    if (hist.length) {
      body.append(el('ul', { class: 'issue-list', 'data-testid': 'own-detail-history' }, hist.map((h) => el('li', null,
        el('span', { class: 'msg' },
          ownLabel('action', h.action) + ': ' + ownLabel('review_state', h.from_state) + ' ← ' + ownLabel('review_state', h.to_state),
          h.note ? el('span', { class: 'block small muted', text: String(h.note) }) : null),
        el('span', { class: 'small muted', text: fmtWhen(h.at) })))));
    } else body.append(el('p', { class: 'muted small', text: 'هنوز تصمیمی برای این سرویس ثبت نشده است.' }));
  }

  // ---- plan / commit
  async function ownPlan(action, selection) {
    clearError();
    const req = { action, selection, note: $('own-note').value.trim() || null };
    OWN.planReq = req;
    const btns = ['own-act-approve', 'own-act-reject', 'own-act-reset'].map($);
    btns.forEach((b) => { b.disabled = true; });
    try {
      const p = await api('POST', ownPath('/plan'), req);
      OWN.plan = p;
      ownShowPlan(p);
    } catch (e) {
      if (e.code === 'ownership.sealed') {
        toast('تصمیم‌ها مهر و موم شده‌اند؛ تغییری ممکن نیست.');
        ownLoadSummary();
      } else ownHandleError(e);
    } finally {
      ownSyncSelection();
    }
  }

  function ownRequiredCount(p) {
    const c = obj(p.confirm);
    const n = c.required_count !== undefined ? c.required_count : p.applicable;
    return Number(n) || 0;
  }

  function ownShowPlan(p) {
    const d = $('own-plan-dialog');
    const action = String(p.action || (OWN.planReq && OWN.planReq.action) || '');
    $('own-plan-title').textContent = 'تأیید: ' + ownLabel('action', action);
    $('own-plan-attest').hidden = action !== 'APPROVE';
    const ne = obj(p.not_eligible);
    kvGrid($('own-plan-kv'), [
      ['انتخاب‌شده', fmtNum(Number(p.selected) || 0)],
      ['تغییر می‌کند', fmtNum(Number(p.applicable) || 0), 'ok-text'],
      ['بدون تغییر (از قبل در همین وضعیت)', fmtNum(Number(p.unchanged) || 0)],
      ['جایگزین‌شده با تصمیم بعدی (تغییر نمی‌کند)', Number(p.superseded) ? fmtNum(Number(p.superseded)) : undefined],
      ['غیرقابل اعمال (کنار گذاشته می‌شود)', fmtNum(Number(ne.total) || 0), Number(ne.total) ? 'bad-text' : null],
      ['مهلت این برنامه', p.expires_at ? fmtWhen(p.expires_at) : undefined],
    ]);
    $('own-plan-kv').querySelectorAll('dd')[1].setAttribute('data-testid', 'own-plan-applicable');

    const nbox = $('own-plan-noteligible');
    clear(nbox);
    const byReason = obj(ne.by_reason);
    if (Object.keys(byReason).length) {
      nbox.append(el('h3', { text: 'دلیل غیرقابل اعمال بودن' }),
        el('ul', { class: 'issue-list' }, Object.keys(byReason).map((k) => el('li', { 'data-code': k },
          el('span', { class: 'msg', text: ownReasonLabel(k) }), el('span', { class: 'pill num', text: fmtNum(byReason[k]) + ' مورد' }),
          el('span', { class: 'error-code', text: k })))));
    }

    const rbox = $('own-plan-risks');
    clear(rbox);
    const risks = objList(p.risks);
    if (risks.length) {
      const sevFa = { high: 'ریسک بالا', medium: 'ریسک متوسط', info: 'اطلاع' };
      rbox.append(el('h3', { text: 'ریسک‌ها' }),
        el('ul', { class: 'issue-list own-risks', 'data-testid': 'own-plan-risks' }, risks.map((r) => el('li', { class: 'own-risk sev-' + String(r.severity || 'info'), 'data-code': String(r.code) },
          el('span', { class: 'badge own-sev own-sev-' + String(r.severity || 'info'), text: sevFa[r.severity] || String(r.severity || '') }),
          el('span', { class: 'msg', text: ownLabel('risk', r.code) }),
          r.count !== undefined && r.count !== null ? el('span', { class: 'pill num', text: fmtNum(r.count) + ' مورد' }) : null,
          el('span', { class: 'error-code', text: String(r.code) })))));
    }

    const ubox = $('own-plan-unreviewed');
    clear(ubox);
    const un = obj(p.unreviewed);
    if (Number(un.count)) {
      const samples = objList(un.samples);
      ubox.append(el('h3', { text: fmtNum(Number(un.count)) + ' مورد هرگز باز نشده است' }),
        el('p', { class: 'muted small', text: 'پیشنهاد می‌شود چند نمونه را پیش از ثبت بررسی کنید.' }));
      if (samples.length) {
        ubox.append(el('div', { class: 'table-wrap' }, el('table', { class: 'table table-compact', 'data-testid': 'own-plan-samples' },
          el('thead', null, el('tr', null, ['شناسه فاکتور', 'پنل', 'وضعیت فاکتور', 'دلیل'].map((h) => el('th', { scope: 'col', text: h })))),
          el('tbody', null, samples.map((s) => el('tr', null,
            el('td', null, el('bdi', { dir: 'ltr', class: 'mono', text: String(s.invoice_key || s.key || '') })),
            el('td', null, el('bdi', { dir: 'ltr', text: s.panel_code ? String(s.panel_code) : '—' })),
            el('td', { text: ownLabel('status_class', s.status_class) }),
            el('td', { class: 'small', text: (Array.isArray(s.reasons) ? s.reasons : []).map(ownReasonLabel).join('، ') || '—' })))))));
      }
    }

    const need = ownRequiredCount(p);
    $('own-plan-error').hidden = true;
    $('own-plan-replan').hidden = true;
    $('own-plan-confirm').hidden = need <= 0;
    $('own-plan-count').value = '';
    $('own-plan-count').removeAttribute('aria-invalid');
    $('own-plan-ack').checked = false;
    $('own-plan-count-label').textContent = 'برای تأیید، تعداد مواردی را که تغییر می‌کنند (' + fmtNum(need) + ') تایپ کنید';
    $('own-plan-ack-text').textContent = action === 'APPROVE'
      ? 'می‌دانم این تأیید مدیر است، مالکیت را اثبات نمی‌کند و جدا از موارد اثبات‌شده نگه داشته می‌شود.'
      : 'پیامد این اقدام را خواندم و آن را تأیید می‌کنم.';
    const commit = $('own-plan-commit');
    commit.textContent = need > 0 ? 'ثبت نهایی ' + fmtNum(need) + ' مورد' : 'موردی برای تغییر نیست';
    commit.className = 'btn ' + (action === 'REJECT' ? 'btn-danger' : 'btn-primary');
    commit.hidden = need <= 0;
    ownSyncCommit();
    if (typeof d.showModal === 'function' && !d.open) d.showModal();
    (need > 0 ? $('own-plan-count') : $('own-plan-cancel')).focus({ preventScroll: true });
    d.scrollTop = 0;
  }

  function ownSyncCommit() {
    const p = OWN.plan;
    const need = p ? ownRequiredCount(p) : 0;
    const typed = latinDigits($('own-plan-count').value);
    const ok = need > 0 && /^\d+$/.test(typed) && Number(typed) === need && $('own-plan-ack').checked;
    $('own-plan-commit').disabled = !ok;
  }

  function ownClosePlan() {
    const d = $('own-plan-dialog');
    if (d.open) d.close();
    OWN.plan = null;
  }

  async function ownCommit() {
    const p = OWN.plan;
    if (!p) return;
    const need = ownRequiredCount(p);
    const typed = latinDigits($('own-plan-count').value);
    if (!/^\d+$/.test(typed) || Number(typed) !== need || !$('own-plan-ack').checked) return;
    const btn = $('own-plan-commit');
    btn.disabled = true;
    const err = $('own-plan-error');
    err.hidden = true;
    try {
      const r = await api('POST', ownPath('/commit'), { plan_id: p.plan_id, confirm_count: Number(typed), acknowledge: true });
      ownClosePlan();
      $('own-note').value = '';
      ownClearSelection();
      toast(ownLabel('action', r.action) + ': ' + fmtNum(Number(r.applied) || 0) + ' مورد اعمال شد' +
        (Number(r.unchanged) ? '، ' + fmtNum(r.unchanged) + ' مورد بدون تغییر' : '') +
        (Number(r.superseded) ? '، ' + fmtNum(r.superseded) + ' مورد به‌دلیل تصمیم بعدی تغییر نکرد' : '') + '.');
      ownRefreshAll();
    } catch (e) {
      if (e.status === 401) return;
      err.textContent = '';
      if (e.code === 'ownership.plan_stale' || e.code === 'ownership.plan_expired') {
        err.textContent = (e.code === 'ownership.plan_stale'
          ? 'برخی موارد پس از این برنامه تغییر کرده‌اند.'
          : 'مهلت این برنامه تمام شده است.') + ' لطفاً «برنامه‌ریزی دوباره» را بزنید و نتیجه تازه را بررسی کنید.';
        $('own-plan-replan').hidden = false;
        $('own-plan-commit').hidden = true;
      } else if (e.code === 'ownership.confirm_mismatch') {
        err.textContent = 'تعداد واردشده با تعداد مواردی که تغییر می‌کنند برابر نیست.';
        $('own-plan-count').setAttribute('aria-invalid', 'true');
        $('own-plan-count').focus();
      } else if (e.code === 'ownership.sealed') {
        err.textContent = 'تصمیم‌ها مهر و موم شده‌اند؛ تغییری ممکن نیست.';
        ownLoadSummary();
      } else {
        err.textContent = e.message || 'ثبت انجام نشد.';
      }
      err.append(' ', el('span', { class: 'error-code', text: e.code || '' }));
      err.hidden = false;
      ownSyncCommit();
    }
  }

  async function ownReplan() {
    const req = OWN.planReq;
    if (!req) return ownClosePlan();
    try {
      const p = await api('POST', ownPath('/plan'), req);
      OWN.plan = p;
      ownShowPlan(p);
    } catch (e) {
      ownClosePlan();
      ownHandleError(e);
    }
  }

  // ---- batches / audit
  async function ownLoadBatches() {
    const tbody = $('own-batches-tbody');
    try {
      const r = await api('GET', ownPath('/batches'));
      clear(tbody);
      const items = objList(r && r.items);
      for (const b of items) {
        const canUndo = b.action !== 'UNDO' && !b.undone_by && !ownSealed();
        tbody.append(el('tr', { 'data-batch': String(b.batch_id), 'data-action': String(b.action) },
          el('td', { class: 'small', text: fmtWhen(b.created_at) }),
          el('td', null, ownLabel('action', b.action),
            b.undone_by ? el('span', { class: 'block small muted', text: 'بازگردانده شد' }) : null,
            b.undo_of ? el('span', { class: 'block small muted ltr-text', text: 'بازگرداندن ' + String(b.undo_of).slice(0, 12) }) : null),
          el('td', { class: 'num', text: fmtNum(Number(b.applied) || 0) }),
          el('td', { class: 'num', text: fmtNum(Number(b.unchanged) || 0) + (Number(b.superseded) ? ' / ' + fmtNum(b.superseded) : '') }),
          el('td', { class: 'small', text: b.note ? truncate(b.note, 60) : '—' }),
          el('td', null, canUndo ? el('button', { type: 'button', class: 'btn btn-ghost btn-sm', 'data-action': 'own-undo', 'data-batch': String(b.batch_id), 'data-testid': 'own-undo', text: 'بازگرداندن' }) : null)));
      }
      if (!items.length) tbody.append(el('tr', null, el('td', { colspan: 6, class: 'empty', text: 'هنوز دسته‌ای ثبت نشده است.' })));
    } catch (e) {
      if (e.status !== 404) ownHandleError(e);
    }
  }

  async function ownLoadAudit() {
    const tbody = $('own-audit-tbody');
    try {
      // newest first (server order)
      const r = await api('GET', ownPath('/audit?offset=0&limit=20'));
      clear(tbody);
      const items = objList(r && r.items);
      for (const a of items) {
        tbody.append(el('tr', null,
          el('td', { class: 'num', text: fmtNum(a.seq) }),
          el('td', { text: fmtWhen(a.at) }),
          el('td', { text: ownLabel('action', a.action) }),
          el('td', { class: 'ltr', text: a.key ? String(a.key) : '—' }),
          el('td', { text: a.from_state ? ownLabel('review_state', a.from_state) : '—' }),
          el('td', { text: a.to_state ? ownLabel('review_state', a.to_state) : '—' })));
      }
      if (!items.length) tbody.append(el('tr', null, el('td', { colspan: 6, class: 'empty', text: 'رویدادی ثبت نشده است.' })));
    } catch (e) {
      if (e.status !== 404) ownHandleError(e);
    }
  }

  async function ownVerifyAudit() {
    const out = $('own-audit-result');
    try {
      const r = await api('GET', ownPath('/audit/verify'));
      out.className = 'small ' + (r.ok ? 'ok-text' : 'bad-text');
      out.setAttribute('data-ok', r.ok ? 'true' : 'false');
      clear(out);
      out.append(r.ok ? 'زنجیره رویدادها سالم است' : 'زنجیره رویدادها دست‌کاری شده یا ناقص است!',
        ' — ' + fmtNum(Number(r.events) || 0) + ' رویداد. سر زنجیره: ',
        el('bdi', { dir: 'ltr', class: 'mono', text: r.head ? String(r.head) : '—' }));
      out.hidden = false;
    } catch (e) {
      ownHandleError(e);
    }
  }

  // ---- seal
  function ownOpenSeal() {
    const total = Number(obj(OWN.summary && OWN.summary.totals).items) || 0;
    const t = obj(OWN.summary && OWN.summary.totals);
    $('own-seal-text').textContent = 'پس از مهر و موم، تصمیم‌های ' + fmtNum(total) + ' سرویس قطعی می‌شود و دیگر قابل تغییر یا بازگرداندن نیست. ' +
      fmtNum(Number(t.PENDING) || 0) + ' مورد هنوز «در انتظار تصمیم» است و در خروجی به همین شکل می‌ماند.';
    $('own-seal-count-label').textContent = 'تعداد کل سرویس‌ها (' + fmtNum(total) + ') را تایپ کنید';
    $('own-seal-word').value = '';
    $('own-seal-count').value = '';
    $('own-seal-error').hidden = true;
    ownSyncSeal();
    const d = $('own-seal-dialog');
    if (typeof d.showModal === 'function' && !d.open) d.showModal();
    $('own-seal-word').focus();
  }
  function ownSyncSeal() {
    const total = Number(obj(OWN.summary && OWN.summary.totals).items) || 0;
    const c = latinDigits($('own-seal-count').value);
    $('own-seal-ok').disabled = !($('own-seal-word').value.trim() === 'SEAL' && /^\d+$/.test(c) && Number(c) === total);
  }
  async function ownSeal() {
    const c = Number(latinDigits($('own-seal-count').value));
    $('own-seal-ok').disabled = true;
    try {
      const s = await api('POST', ownPath('/seal'), { confirm: 'SEAL', confirm_count: c });
      const d = $('own-seal-dialog');
      if (d.open) d.close();
      if (s && s.totals) OWN.summary = s;
      ownRenderSummary();
      toast('تصمیم‌های مالکیت مهر و موم شد.');
      ownRefreshAll();
    } catch (e) {
      if (e.status === 401) return;
      const err = $('own-seal-error');
      clear(err);
      err.append(e.message || 'مهر و موم انجام نشد.', ' ', el('span', { class: 'error-code', text: e.code || '' }));
      err.hidden = false;
      ownSyncSeal();
    }
  }

  // ---- export (fetch + blob download, so a 404 can ask for the passphrase)
  async function ownDownload(method, path, body) {
    const headers = { Accept: 'application/json' };
    const init = { method, headers, credentials: 'same-origin', cache: 'no-store' };
    if (method !== 'GET') headers['X-CSRF-Token'] = S.csrf || '';
    if (body !== undefined) {
      headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
    }
    let res;
    try {
      res = await fetch(path, init);
    } catch (_) {
      throw new ApiError(0, 'network', NETWORK_MSG, null, true);
    } finally {
      init.body = null;
    }
    if (!res.ok) {
      let data = null;
      try {
        data = await res.json();
      } catch (_) {
        data = null;
      }
      const e = errorFromPayload(res.status, data);
      if (res.status === 401) showFatal(e);
      throw e;
    }
    const blob = await res.blob();
    const cd = res.headers.get('Content-Disposition') || '';
    const m = /filename="?([^";]+)"?/i.exec(cd);
    const name = m ? m[1] : 'ownership-decisions.json';
    const url = URL.createObjectURL(blob);
    const a = el('a', { href: url, download: name, hidden: true });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  }
  async function ownExport() {
    try {
      await ownDownload('GET', ownPath('/export.json'));
      toast('فایل تصمیم‌ها دانلود شد.');
    } catch (e) {
      if (e.code === 'ownership.export_needs_passphrase') {
        $('own-export-pass-form').hidden = false;
        $('own-export-pass').focus();
      } else if (e.status !== 401) showError(e);
    }
  }
  async function ownExportWithPass(ev) {
    ev.preventDefault();
    const input = $('own-export-pass');
    const passphrase = input.value;
    input.value = '';
    if (!passphrase) return input.focus();
    try {
      await ownDownload('POST', ownPath('/export'), { passphrase });
      $('own-export-pass-form').hidden = true;
      toast('فایل تصمیم‌ها دانلود شد.');
    } catch (e) {
      if (e.status !== 401) showError(e);
    }
  }

  // ---- read-only live panel check
  function ownStopLivePoll() {
    clearTimeout(OWN.livePoll);
    OWN.livePoll = null;
  }
  async function ownLoadLive() {
    ownStopLivePoll();
    let st;
    try {
      st = await api('GET', ownPath('/live-check'));
    } catch (e) {
      if (e.status !== 404) ownHandleError(e);
      $('own-live-off').hidden = false;
      $('own-live-form').hidden = true;
      return;
    }
    ownRenderLive(st || {});
    if (st && st.running && S.view === 'ownership') OWN.livePoll = setTimeout(ownLoadLive, 1000);
    else if (st && st.finished_at && OWN.liveWasRunning) {
      OWN.liveWasRunning = false;
      ownLoadSummary();
      ownLoadFacets();
      ownLoadItems(OWN.offset);
    }
  }
  function ownRenderLive(st) {
    const enabled = !!st.enabled;
    $('own-live-off').hidden = enabled;
    $('own-live-form').hidden = !enabled;
    const running = !!st.running;
    OWN.liveWasRunning = OWN.liveWasRunning || running;
    $('own-live-start').disabled = running;
    $('own-live-cancel').hidden = !running;
    const total = Number(st.total) || 0;
    const done = Number(st.done) || 0;
    const show = enabled && (running || total > 0 || st.error);
    $('own-live-progress').hidden = !show;
    if (!show) return;
    const pct = total ? (done / total) * 100 : 0;
    setProgress('own-live-bar', 'own-live-fill', pct, { indeterminate: running && !total, ok: !running && !st.error && total > 0, bad: !!st.error });
    const parts = [(running ? 'در حال بررسی' : 'بررسی پایان یافت') + (st.panel_code ? ' (پنل ' + String(st.panel_code) + ')' : '') + ': ' +
      fmtNum(done) + ' از ' + fmtNum(total)];
    const by = obj(st.by_status);
    const keys = Object.keys(by);
    if (keys.length) parts.push(keys.map((k) => ownLabel('live_status', k) + ' ' + fmtNum(by[k])).join('، '));
    if (st.error) parts.push((st.error.code === 'panel.cancelled' ? '' : 'خطا: ') + String(st.error.message || st.error.code || st.error));
    $('own-live-text').textContent = parts.join(' — ');
  }
  async function ownLiveStart(ev) {
    ev.preventDefault();
    const panel = $('own-live-panel').value;
    const base = $('own-live-url').value.trim();
    const user = $('own-live-user');
    const pass = $('own-live-pass');
    const body = {
      panel_code: panel,
      base_url: base,
      username: user.value,
      password: pass.value,
      selection: { mode: 'filter', filter: Object.assign(ownFilterBody(), { panel: [panel] }), exclude_keys: [] },
    };
    // Credentials leave the DOM immediately and are never stored by the page.
    user.value = '';
    pass.value = '';
    if (!panel || !base || !body.username || !body.password) {
      body.password = null;
      showError({ code: 'live_check.input', message: 'کد پنل، نشانی، نام کاربری و رمز مدیر پنل را کامل وارد کنید.' });
      return;
    }
    try {
      await api('POST', ownPath('/live-check'), body);
      OWN.liveWasRunning = true;
      toast('بررسی فقط‌خواندنی پنل شروع شد.');
      ownLoadLive();
    } catch (e) {
      ownHandleError(e);
    } finally {
      body.password = null;
      body.username = null;
    }
  }
  async function ownLiveCancel() {
    try {
      await api('POST', ownPath('/live-check/cancel'));
      ownLoadLive();
    } catch (e) {
      ownHandleError(e);
    }
  }

  function wireOwnership() {
    $('own-refresh').addEventListener('click', () => onShow.ownership());
    $('own-filters').addEventListener('submit', (ev) => {
      ev.preventDefault();
      ownReadFilters();
      ownClearSelection();
      ownLoadItems(0);
    });
    $('own-reset-filters').addEventListener('click', () => {
      for (const k of OWN_FILTERS) $('own-f-' + k).value = '';
      $('own-q').value = '';
      ownReadFilters();
      ownClearSelection();
      ownLoadItems(0);
    });
    $('own-tbody').addEventListener('change', (ev) => {
      const box = ev.target.closest('.own-row-check');
      if (!box) return;
      ownSetSelected(box.dataset.key, box.checked);
      const tr = box.closest('tr');
      if (tr) tr.classList.toggle('is-selected', box.checked);
      ownSyncSelection();
    });
    $('own-tbody').addEventListener('click', (ev) => {
      const b = ev.target.closest('[data-action="own-detail"]');
      if (b) return ownOpenDetail(b.dataset.key, b);
      if (ev.target.closest('input, button, a, label')) return;
      const tr = ev.target.closest('tr[data-key]');
      if (tr) ownOpenDetail(tr.dataset.key, tr.querySelector('[data-action="own-detail"]'));
    });
    $('own-check-page').addEventListener('change', (ev) => {
      const on = ev.target.checked;
      for (const it of OWN.items) ownSetSelected(String(it.key), on);
      $('own-tbody').querySelectorAll('.own-row-check').forEach((b) => {
        b.checked = on;
        const tr = b.closest('tr');
        if (tr) tr.classList.toggle('is-selected', on);
      });
      ownSyncSelection();
    });
    $('own-select-filtered').addEventListener('click', () => {
      OWN.sel = { mode: 'filter', filter: ownFilterBody(), exclude: new Set(), total: OWN.total };
      ownRenderItems();
      $('own-clear-sel').focus();
    });
    $('own-clear-sel').addEventListener('click', () => {
      ownClearSelection();
      ownRenderItems();
    });
    for (const id of ['own-act-approve', 'own-act-reject', 'own-act-reset']) {
      $(id).addEventListener('click', () => {
        if (!ownSelCount() || ownSealed()) return;
        ownPlan($(id).dataset.action, ownSelectionBody());
      });
    }
    $('own-batches-tbody').addEventListener('click', (ev) => {
      const b = ev.target.closest('[data-action="own-undo"]');
      if (b) ownPlan('UNDO', { mode: 'batch', batch_id: b.dataset.batch });
    });
    // plan dialog
    $('own-plan-form').addEventListener('submit', (ev) => ev.preventDefault());
    $('own-plan-count').addEventListener('input', () => {
      $('own-plan-count').removeAttribute('aria-invalid');
      ownSyncCommit();
    });
    $('own-plan-count').addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') ev.preventDefault();
    });
    $('own-plan-ack').addEventListener('change', ownSyncCommit);
    $('own-plan-commit').addEventListener('click', ownCommit);
    $('own-plan-replan').addEventListener('click', ownReplan);
    $('own-plan-cancel').addEventListener('click', ownClosePlan);
    $('own-plan-dialog').addEventListener('close', () => {
      OWN.plan = null;
    });
    // detail dialog
    $('own-detail-close').addEventListener('click', ownCloseDetail);
    $('own-detail').addEventListener('close', () => {
      const o = OWN.detailOpener;
      OWN.detailOpener = null;
      if (o && o.isConnected && typeof o.focus === 'function') o.focus();
    });
    // audit / seal / export / live
    $('own-audit-verify').addEventListener('click', ownVerifyAudit);
    $('own-seal').addEventListener('click', () => {
      if (!ownSealed()) ownOpenSeal();
    });
    $('own-seal-form').addEventListener('submit', (ev) => ev.preventDefault());
    $('own-seal-word').addEventListener('input', ownSyncSeal);
    $('own-seal-count').addEventListener('input', ownSyncSeal);
    $('own-seal-ok').addEventListener('click', ownSeal);
    $('own-seal-cancel').addEventListener('click', () => $('own-seal-dialog').close());
    $('own-export').addEventListener('click', ownExport);
    $('own-export-pass-form').addEventListener('submit', ownExportWithPass);
    $('own-live-form').addEventListener('submit', ownLiveStart);
    $('own-live-cancel').addEventListener('click', ownLiveCancel);
    $('own-live-settings').addEventListener('click', () => go('settings'));
  }

  // ------------------------------------------------------------------ settings
  const SETTINGS_FIELDS = [
    { key: 'max_archive_mb', label: 'حداکثر حجم فایل ZIP', unit: 'مگابایت', min: 1, max: 65536, def: 4096,
      help: 'فایل‌های بزرگ‌تر از این مقدار پذیرفته نمی‌شوند.' },
    { key: 'max_entries', label: 'حداکثر تعداد فایل داخل ZIP', unit: 'فایل', min: 1, max: 10000, def: 64,
      help: 'بایگانی‌هایی با تعداد فایل غیرعادی زیاد رد می‌شوند.' },
    { key: 'max_total_uncompressed_mb', label: 'حداکثر حجم کل پس از استخراج', unit: 'مگابایت', min: 1, max: 262144, def: 20480,
      help: 'محافظت در برابر ZIP bomb: مجموع حجم فایل‌ها پس از باز شدن از این مقدار بیشتر نمی‌شود.' },
    { key: 'max_ratio', label: 'حداکثر نسبت فشرده‌سازی', unit: 'برابر', min: 2, max: 10000, def: 200,
      help: 'اگر نسبت حجم باز‌شده به حجم فشرده یک فایل از این مقدار بیشتر باشد، بایگانی ناامن تلقی می‌شود.' },
    { key: 'min_free_disk_mb', label: 'حداقل فضای خالی باقی‌مانده دیسک', unit: 'مگابایت', min: 0, max: 1048576, def: 512,
      help: 'پردازش فقط وقتی انجام می‌شود که پس از استخراج دست‌کم این مقدار فضای خالی باقی بماند.' },
    { key: 'workspace_dir', label: 'پوشه کاری موقت', type: 'text', def: '',
      help: 'خالی بگذارید تا پوشه موقت ویندوز استفاده شود. در غیر این صورت مسیر کامل یک پوشه محلی موجود را وارد کنید (مثلاً D:\\Mirza2NexaTemp). از پوشه‌های همگام‌سازی ابری مانند OneDrive استفاده نکنید.' },
    { key: 'history_limit', label: 'حداکثر رکوردهای تاریخچه', unit: 'رکورد', min: 0, max: 1000, def: 50,
      help: 'صفر یعنی هیچ تاریخچه‌ای ثبت نشود. تاریخچه فقط آمار کلی را نگه می‌دارد.' },
    { key: 'delete_sql_after_parse', label: 'حذف فوری فایل‌های SQL استخراج‌شده پس از خواندن', type: 'bool', def: true,
      help: 'توصیه می‌شود فعال بماند تا داده خام مشتریان کمترین زمان ممکن روی دیسک بماند.' },
    { key: 'panel_live_check_enabled', label: 'اجازه بررسی فقط‌خواندنی وضعیت سرویس‌ها روی پنل', type: 'bool', def: false, optional: true,
      warn: 'هشدار: با روشن کردن این گزینه، صفحه «بررسی مالکیت سرویس‌ها» می‌تواند با اطلاعات ورودی که خودتان وارد می‌کنید به پنل وصل شود. این بررسی فقط وضعیت سرویس را می‌خواند و هرگز سرویسی را ایجاد، تمدید، غیرفعال یا حذف نمی‌کند. اطلاعات ورود پنل ذخیره نمی‌شود.',
      help: 'به‌طور پیش‌فرض خاموش است. وضعیت روی پنل فقط نشانه ریسک است و مالکیت را اثبات نمی‌کند.' },
  ];
  // An optional field is shown and sent only when the server's settings know the key.
  const settingKnown = (f) => !f.optional || !!(S.settings && Object.prototype.hasOwnProperty.call(S.settings, f.key));

  function buildSettingsForm() {
    const box = $('settings-fields');
    clear(box);
    for (const f of SETTINGS_FIELDS) {
      const id = 'set-' + f.key;
      const helpId = id + '-help';
      const errId = id + '-error';
      const help = el('p', { class: 'help', id: helpId, text: f.help + (f.type ? '' : ' (محدوده مجاز: ' + fmtNum(f.min) + ' تا ' + fmtNum(f.max) + ')') });
      const err = el('p', { class: 'field-error', id: errId, role: 'alert', hidden: true });
      let field;
      if (f.type === 'bool') {
        const warnId = id + '-warn';
        field = el('div', { class: 'field', id: id + '-field', hidden: !!f.optional },
          el('label', { class: 'check-row', for: id },
            el('input', { type: 'checkbox', id, 'data-testid': 'setting-' + f.key, 'aria-describedby': helpId + (f.warn ? ' ' + warnId : '') }),
            el('span', { text: f.label })),
          f.warn ? el('p', { class: 'help warn-text', id: warnId, text: f.warn }) : null,
          help, err);
      } else {
        const input = el('input', {
          type: 'text', id, dir: 'ltr', spellcheck: 'false', autocomplete: 'off',
          inputmode: f.type === 'text' ? null : 'numeric',
          'data-testid': 'setting-' + f.key, 'aria-describedby': helpId + ' ' + errId,
        });
        field = el('div', { class: 'field' },
          el('label', { for: id, text: f.label }),
          el('div', { class: 'field-row' }, input, f.unit ? el('span', { class: 'unit', text: f.unit }) : null),
          help, err);
      }
      box.append(field);
    }
  }

  function fillSettings(s) {
    for (const f of SETTINGS_FIELDS) {
      const n = $('set-' + f.key);
      if (f.optional) $('set-' + f.key + '-field').hidden = !settingKnown(f);
      if (f.type === 'bool') n.checked = !!s[f.key];
      else if (f.type === 'text') n.value = s[f.key] || '';
      else n.value = s[f.key] === undefined || s[f.key] === null ? '' : faDigits(s[f.key]);
    }
  }

  onShow.settings = async function () {
    clearFieldErrors($('settings-form'));
    try {
      S.settings = await api('GET', '/api/settings');
      fillSettings(S.settings);
    } catch (e) {
      if (e.status !== 401) showError(e);
    }
  };

  async function saveSettings(ev) {
    ev.preventDefault();
    const form = $('settings-form');
    clearFieldErrors(form);
    clearError();
    const body = {};
    let bad = null;
    for (const f of SETTINGS_FIELDS) {
      const n = $('set-' + f.key);
      if (!settingKnown(f)) continue;
      if (f.type === 'bool') body[f.key] = n.checked;
      else if (f.type === 'text') body[f.key] = n.value.trim();
      else {
        const raw = latinDigits(n.value);
        const v = /^\d+$/.test(raw) ? parseInt(raw, 10) : NaN;
        if (!Number.isFinite(v) || v < f.min || v > f.max) {
          fieldError('set-' + f.key, 'عدد صحیح بین ' + fmtNum(f.min) + ' و ' + fmtNum(f.max) + ' وارد کنید.');
          bad = bad || n;
        }
        body[f.key] = v;
      }
    }
    if (bad) {
      bad.focus();
      return;
    }
    const btn = $('settings-save');
    btn.disabled = true;
    try {
      S.settings = await api('PUT', '/api/settings', body);
      fillSettings(S.settings);
      toast('تنظیمات ذخیره شد.');
    } catch (e) {
      if (e.status === 401) return;
      showError(e);
      if (e.code === 'settings.invalid' && e.detail) {
        let first = null;
        for (const k of String(e.detail).split(',')) {
          const f = SETTINGS_FIELDS.find((x) => x.key === k.trim());
          if (!f) continue;
          fieldError('set-' + f.key, f.key === 'workspace_dir'
            ? 'مسیر باید کامل (مطلق) و یک پوشه موجود باشد.'
            : 'مقدار خارج از محدوده مجاز است.');
          first = first || $('set-' + f.key);
        }
        if (first) first.focus();
      }
    } finally {
      btn.disabled = false;
    }
  }

  // ------------------------------------------------------------------ wiring
  function wire() {
    document.querySelectorAll('.nav-item').forEach((b) => {
      b.addEventListener('click', () => {
        if (b.dataset.view === 'new' && !wizardJob()) S.wizardStep = 1;
        else if (b.dataset.view === 'new' && S.wizardStep === 1 && wizardJob()) S.wizardStep = 2;
        go(b.dataset.view);
      });
    });
    $('quick-start').addEventListener('click', () => {
      S.wizardStep = 1;
      go('new');
    });
    $('jobs-refresh').addEventListener('click', () => onShow.dashboard());
    $('jobs-tbody').addEventListener('click', (ev) => {
      const b = ev.target.closest('[data-action="open-job"]');
      if (b) openJob(b.dataset.jobId);
    });
    $('error-close').addEventListener('click', clearError);

    // file selection + drag & drop
    $('zip-input').addEventListener('change', (ev) => setFile(ev.target.files && ev.target.files[0]));
    const dz = $('dropzone');
    ['dragenter', 'dragover'].forEach((t) =>
      dz.addEventListener(t, (ev) => {
        ev.preventDefault();
        if (ev.dataTransfer) ev.dataTransfer.dropEffect = 'copy';
        dz.classList.add('is-over');
      }));
    ['dragleave', 'dragend'].forEach((t) => dz.addEventListener(t, () => dz.classList.remove('is-over')));
    dz.addEventListener('drop', (ev) => {
      ev.preventDefault();
      dz.classList.remove('is-over');
      if (S.xhr) return;
      const files = ev.dataTransfer && ev.dataTransfer.files;
      if (files && files.length) setFile(files[0]);
    });
    // Prevent the browser from navigating to a file dropped outside the zone.
    window.addEventListener('dragover', (ev) => ev.preventDefault());
    window.addEventListener('drop', (ev) => ev.preventDefault());

    $('upload-btn').addEventListener('click', startUpload);
    $('upload-cancel').addEventListener('click', () => {
      if (S.xhr) S.xhr.abort();
    });
    $('to-step-3').addEventListener('click', () => setWizardStep(3));
    $('back-step-2').addEventListener('click', () => {
      wipeSecretInputs();
      setWizardStep(2);
    });
    $('discard-upload').addEventListener('click', discardUpload);
    $('step-3').addEventListener('submit', submitStart);
    document.querySelectorAll('input[name="secret-mode"]').forEach((r) => r.addEventListener('change', syncSecretMode));
    document.querySelectorAll('.toggle-vis').forEach((b) => {
      b.addEventListener('click', () => {
        const input = $(b.dataset.toggleFor);
        const show = input.type === 'password';
        input.type = show ? 'text' : 'password';
        b.setAttribute('aria-pressed', show ? 'true' : 'false');
        b.setAttribute('aria-label', show ? 'پنهان کردن رمز' : 'نمایش رمز');
        b.textContent = show ? 'پنهان' : 'نمایش';
        input.focus();
      });
    });
    for (const id of ['zip-password', 'pkg-pass', 'pkg-pass2']) {
      $(id).addEventListener('input', () => {
        $(id).removeAttribute('aria-invalid');
      });
    }

    // status
    $('cancel-btn').addEventListener('click', cancelJob);
    $('retry-btn').addEventListener('click', () => {
      S.wizardStep = 3;
      go('new');
    });
    $('goto-report').addEventListener('click', () => go('report'));
    $('goto-download').addEventListener('click', () => go('download'));

    // review / preview
    $('review-filters').addEventListener('submit', (ev) => {
      ev.preventDefault();
      loadReview(0);
    });
    $('review-severity').addEventListener('change', () => loadReview(0));
    $('review-limit').addEventListener('change', () => loadReview(0));
    $('preview-load').addEventListener('click', loadPreview);
    $('coverage-class').addEventListener('change', renderCoverageTable);
    $('coverage-status').addEventListener('change', renderCoverageTable);
    $('coverage-filters').addEventListener('submit', (ev) => ev.preventDefault());
    $('preview-entity').addEventListener('change', loadPreview);

    // download / history / settings
    $('delete-job').addEventListener('click', deleteJob);
    $('download-key').addEventListener('click', () => {
      toast('کلید را در مکانی امن و جدا از بسته ذخیره کنید.');
    });
    $('history-clear').addEventListener('click', clearHistory);
    wireOwnership();
    buildSettingsForm();
    $('settings-form').addEventListener('submit', saveSettings);
    $('settings-defaults').addEventListener('click', () => {
      const d = {};
      for (const f of SETTINGS_FIELDS) d[f.key] = f.def;
      fillSettings(d);
      toast('مقادیر پیش‌فرض در فرم قرار گرفت؛ برای اعمال «ذخیره تنظیمات» را بزنید.');
    });

    document.addEventListener('keydown', (ev) => {
      if (ev.key === 'Escape' && !$('error-box').hidden && !$('confirm-dialog').open) clearError();
    });

    // keep elapsed time ticking without hitting the server
    setInterval(() => {
      if (S.view === 'status' && S.job && ACTIVE.has(S.job.status)) updateElapsed();
    }, 1000);

    // warn before leaving during an upload
    window.addEventListener('beforeunload', (ev) => {
      if (S.xhr) {
        ev.preventDefault();
        ev.returnValue = '';
      }
    });
  }

  // ------------------------------------------------------------------ boot
  async function boot() {
    wire();
    const params = new URLSearchParams(location.search);
    const token = params.get('t') || '';
    try {
      const r = await api('POST', '/api/auth', { token });
      S.csrf = r.csrf;
      store.set('m2n.csrf', r.csrf);
    } catch (e) {
      showFatal(e);
      return;
    } finally {
      if (params.has('t')) {
        params.delete('t');
        const q = params.toString();
        history.replaceState(null, '', location.pathname + (q ? '?' + q : '') + location.hash);
      }
    }
    $('splash').hidden = true;
    $('app').hidden = false;

    // Persian labels (shared with the downloadable report). Defaults above cover a failure.
    try {
      const lb = await api('GET', '/api/labels');
      if (lb && typeof lb === 'object') {
        for (const k of Object.keys(L)) if (lb[k] && typeof lb[k] === 'object') L[k] = Object.assign({}, L[k], lb[k]);
      }
    } catch (_) {
      /* keep defaults */
    }

    // settings (for client-side size check) — non-blocking
    api('GET', '/api/settings').then((s) => { S.settings = s; }).catch(() => {});

    let view = store.get('m2n.view') || 'dashboard';
    if (S.jobId) {
      const j = await refreshJob();
      if (j && PRESTART.has(j.status) && j.inspection) S.wizardStep = 2;
      if (j && ACTIVE.has(j.status) && view === 'dashboard') view = 'status';
    }
    go(view, { focus: false });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();

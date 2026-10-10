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
  const entityLabel = (k) => ENTITY_FA[k] || k;
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
  const VIEWS = ['dashboard', 'new', 'status', 'report', 'review', 'preview', 'download', 'history', 'settings'];
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
    for (const sec of secs) {
      const val = recon[sec];
      const tbody = el('tbody');
      if (val && typeof val === 'object' && !Array.isArray(val)) {
        for (const k of Object.keys(val)) {
          tbody.append(el('tr', { 'data-key': k }, el('th', { scope: 'row', class: 'ltr', text: k }), el('td', { class: 'num' }, reconValue(val[k]))));
        }
      } else {
        tbody.append(el('tr', null, el('th', { scope: 'row', text: '—' }), el('td', { class: 'num', text: fmtNum(val) })));
      }
      rec.append(el('div', { class: 'recon-section', 'data-section': sec },
        el('h3', { text: entityLabel(sec) }),
        el('div', { class: 'table-wrap' }, el('table', { class: 'table' }, tbody))));
    }

    // mapping
    const mt = $('mapping-tbody');
    clear(mt);
    for (const m of r.mapping || []) {
      mt.append(el('tr', { 'data-status': m.status },
        el('td', { text: entityLabel(m.entity) }),
        el('td', { class: 'ltr', text: m.source_table || '—' }),
        el('td', null, tagList(m.source_columns || [])),
        el('td', { class: 'ltr', text: m.target || '—' }),
        el('td', null, el('span', { class: 'badge st-' + m.status, text: MAPPING_FA[m.status] || m.status })),
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
      el('h3', { class: 'small', text: 'جداول ناشناخته (نادیده گرفته می‌شوند)' }), tagList(sp.unknown_tables, 'is-warn'));
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
          el('td', { class: 'ltr', text: it.kind || '' }),
          el('td', null, el('span', { class: 'error-code', text: it.code || '' })),
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
  ];

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
        field = el('div', { class: 'field' },
          el('label', { class: 'check-row', for: id },
            el('input', { type: 'checkbox', id, 'data-testid': 'setting-' + f.key, 'aria-describedby': helpId }),
            el('span', { text: f.label })),
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
    $('preview-entity').addEventListener('change', loadPreview);

    // download / history / settings
    $('delete-job').addEventListener('click', deleteJob);
    $('download-key').addEventListener('click', () => {
      toast('کلید را در مکانی امن و جدا از بسته ذخیره کنید.');
    });
    $('history-clear').addEventListener('click', clearHistory);
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

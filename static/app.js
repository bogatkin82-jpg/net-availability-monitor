/* ==========================================================================
   net-monitor — frontend (vanilla JS, no dependencies)
   Контракт API: docs/api-contract.md
   ========================================================================== */
(function () {
  'use strict';

  /* ------------------------------------------------------------------ *
   *  Константы
   * ------------------------------------------------------------------ */
  var POLL_MS = 3000;          // опрос /api/state
  var STATE_TIMEOUT = 8000;
  var CHECK_TIMEOUT = 30000;   // POST /api/check может идти до ~15 c
  var INC_TIMEOUT = 10000;
  var INC_PAGE = 100;
  var RECENT_CHECKS = 60;      // сегментов в полосе проверок
  var TZ = 'Europe/Moscow';

  var STATUSES = ['ok', 'degraded', 'blocked', 'down', 'unknown'];

  var STATUS_LABEL = {
    ok: 'Доступен',
    degraded: 'Нестабильно',
    blocked: 'Похоже на блокировку',
    down: 'Недоступен',
    unknown: 'Нет данных'
  };

  var INTERNET_LABEL = {
    ok: 'Интернет работает',
    degraded: 'Интернет нестабилен',
    down: 'Нет интернета'
  };

  var SERVICE_ORDER = ['youtube', 'telegram', 'internet'];

  var SERVICE_FALLBACK = {
    youtube: { name: 'YouTube', subtitle: 'видеохостинг · youtube.com', accent: '#ff3040' },
    telegram: { name: 'Telegram', subtitle: 'мессенджер · telegram.org', accent: '#2ea6e0' },
    internet: { name: 'Интернет', subtitle: 'контрольный канал · gstatic.com', accent: '#38d39f' }
  };

  var SKIP_STATUSES = { ok: 0, unknown: 0 };

  /* ------------------------------------------------------------------ *
   *  Иконки (inline SVG, без внешних ресурсов)
   * ------------------------------------------------------------------ */
  var ICONS = {
    youtube: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2.5" y="5.5" width="19" height="13" rx="3.6"/><path d="m10.2 9.3 4.8 2.7-4.8 2.7z"/></svg>',
    telegram: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 4.3 3.3 11.1a.55.55 0 0 0 .04 1l4.9 1.7 1.8 5.3a.6.6 0 0 0 1 .22l2.7-2.9 4.3 3.2a.6.6 0 0 0 .95-.33z"/><path d="m8.2 13.8 9.2-6.6"/></svg>',
    internet: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3c2.6 2.5 3.9 5.6 3.9 9S14.6 18.5 12 21c-2.6-2.5-3.9-5.6-3.9-9S9.4 5.5 12 3z"/></svg>',
    unknown: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 17h.01"/><path d="M9.6 9.2a2.6 2.6 0 0 1 5 .8c0 1.8-2.6 2-2.6 3.4"/></svg>'
  };

  var BANNER_ICONS = {
    warn: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2.5 8.5a15 15 0 0 1 19 0"/><path d="M5.5 12a10.5 10.5 0 0 1 13 0"/><path d="M8.5 15.5a6 6 0 0 1 7 0"/><circle cx="12" cy="19" r="1.1" fill="currentColor" stroke="none"/></svg>',
    critical: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2.5 8.5a15 15 0 0 1 6-3.7"/><path d="M15.5 4.9a15 15 0 0 1 6 3.6"/><path d="M5.5 12a10.5 10.5 0 0 1 3.4-1.9"/><path d="M15.2 10.2a10.5 10.5 0 0 1 3.3 1.8"/><path d="M8.5 15.5a6 6 0 0 1 7 0"/><circle cx="12" cy="19" r="1.1" fill="currentColor" stroke="none"/><path d="m3 3 18 18"/></svg>',
    offline: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4.5 15.5h10.2a3.2 3.2 0 0 0 .5-6.36A5.2 5.2 0 0 0 6.4 8.4 3.5 3.5 0 0 0 4.5 15.5z"/><path d="m3 3 18 18"/></svg>'
  };

  /* ------------------------------------------------------------------ *
   *  Форматирование
   * ------------------------------------------------------------------ */
  var clockFmt = new Intl.DateTimeFormat('ru-RU', { timeZone: TZ, hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
  var shortFmt = new Intl.DateTimeFormat('ru-RU', { timeZone: TZ, day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });
  var fullFmt = new Intl.DateTimeFormat('ru-RU', { timeZone: TZ, day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
  var monthDayFmt = new Intl.DateTimeFormat('ru-RU', { timeZone: TZ, day: '2-digit', month: '2-digit' });

  function toDate(value) {
    if (value === null || value === undefined || value === '') return null;
    var d;
    if (typeof value === 'number') {
      d = new Date(value * 1000);
    } else if (/^\d+(\.\d+)?$/.test(String(value))) {
      d = new Date(Number(value) * 1000);
    } else {
      d = new Date(value);
    }
    return isNaN(d.getTime()) ? null : d;
  }

  function fmtClock(value) {
    var d = toDate(value);
    return d ? clockFmt.format(d) : '—';
  }

  function fmtDateTimeShort(value) {
    var d = toDate(value);
    return d ? shortFmt.format(d) : '—';
  }

  function fmtDateTimeFull(value) {
    var d = toDate(value);
    return d ? fullFmt.format(d) : '—';
  }

  function fmtMonthDay(value) {
    var d = toDate(value);
    return d ? monthDayFmt.format(d) : '—';
  }

  function fmtLatency(value) {
    if (value === null || value === undefined || value === '') return '—';
    var v = Number(value);
    if (!isFinite(v) || v < 0) return '—';
    if (v >= 1000) {
      return (v / 1000).toLocaleString('ru-RU', { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + ' с';
    }
    return Math.round(v).toLocaleString('ru-RU') + ' мс';
  }

  function fmtUptime(value) {
    if (value === null || value === undefined || value === '') return '—';
    var v = Number(value);
    if (!isFinite(v)) return '—';
    return v.toLocaleString('ru-RU', { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + '%';
  }

  function fmtInt(value) {
    if (value === null || value === undefined || value === '') return '—';
    var v = Number(value);
    if (!isFinite(v)) return '—';
    return Math.round(v).toLocaleString('ru-RU');
  }

  function fmtDuration(value) {
    if (value === null || value === undefined || value === '') return '—';
    var sec = Number(value);
    if (!isFinite(sec) || sec < 0) return '—';
    sec = Math.floor(sec);
    if (sec < 60) return sec + ' с';
    var m = Math.floor(sec / 60);
    var s = sec % 60;
    if (m < 60) return s ? m + ' мин ' + s + ' с' : m + ' мин';
    var h = Math.floor(m / 60);
    var mm = m % 60;
    if (h < 24) return mm ? h + ' ч ' + mm + ' мин' : h + ' ч';
    var d = Math.floor(h / 24);
    var hh = h % 24;
    return hh ? d + ' д ' + hh + ' ч' : d + ' д';
  }

  function relTime(epochSec) {
    if (epochSec === null || epochSec === undefined) return '—';
    var diff = Date.now() / 1000 - Number(epochSec);
    if (!isFinite(diff)) return '—';
    if (diff < 0) return 'вот-вот';
    if (diff < 5) return 'только что';
    if (diff < 60) return Math.floor(diff) + ' с назад';
    if (diff < 3600) return Math.floor(diff / 60) + ' мин назад';
    if (diff < 86400) return Math.floor(diff / 3600) + ' ч назад';
    if (diff < 7 * 86400) return Math.floor(diff / 86400) + ' дн назад';
    return fmtMonthDay(epochSec);
  }

  function normalizeStatus(value) {
    var s = String(value || '').toLowerCase();
    return STATUSES.indexOf(s) !== -1 ? s : 'unknown';
  }

  function statusLabel(status) {
    return STATUS_LABEL[normalizeStatus(status)] || STATUS_LABEL.unknown;
  }

  function hexToRgba(hex, alpha) {
    var m = /^#?([a-f\d]{3}|[a-f\d]{6})$/i.exec(String(hex || '').trim());
    if (!m) return 'rgba(127, 167, 201, ' + alpha + ')';
    var h = m[1];
    if (h.length === 3) h = h.charAt(0) + h.charAt(0) + h.charAt(1) + h.charAt(1) + h.charAt(2) + h.charAt(2);
    var n = parseInt(h, 16);
    return 'rgba(' + ((n >> 16) & 255) + ', ' + ((n >> 8) & 255) + ', ' + (n & 255) + ', ' + alpha + ')';
  }

  function isHexColor(value) {
    return /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(String(value || ''));
  }

  function serviceAccent(id) {
    var fb = SERVICE_FALLBACK[id];
    return fb ? fb.accent : '#8798ad';
  }

  function serviceName(id) {
    var fb = SERVICE_FALLBACK[id];
    return fb ? fb.name : null;
  }

  function labelForService(svc) {
    var st = normalizeStatus(svc.status);
    if (svc.id === 'internet') return INTERNET_LABEL[st] || svc.status_label || STATUS_LABEL[st];
    return svc.status_label || STATUS_LABEL[st];
  }

  function epochFrom(epochValue, isoValue) {
    var n = Number(epochValue);
    if (isFinite(n) && n > 0) return n;
    var d = toDate(isoValue);
    return d ? d.getTime() / 1000 : null;
  }

  /* ------------------------------------------------------------------ *
   *  DOM-хелперы
   * ------------------------------------------------------------------ */
  function $(id) { return document.getElementById(id); }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function setText(node, text) {
    if (!node) return;
    var next = text === undefined || text === null ? '' : String(text);
    if (node.textContent !== next) node.textContent = next;
  }

  /* ------------------------------------------------------------------ *
   *  DOM-ссылки
   * ------------------------------------------------------------------ */
  var dom = {};

  function cacheDom() {
    dom.banner = $('banner');
    dom.bannerIcon = $('banner-icon');
    dom.bannerTitle = $('banner-title');
    dom.bannerSub = $('banner-sub');

    dom.overallChip = $('overall-chip');
    dom.overallText = $('overall-text');
    dom.liveDot = $('live-dot');
    dom.liveText = $('live-text');
    dom.nextCheck = $('next-check');

    dom.autoToggle = $('auto-toggle');
    dom.btnCheck = $('btn-check');
    dom.checkSpinner = $('check-spinner');
    dom.checkLabel = $('check-label');
    dom.btnExport = $('btn-export');

    dom.kpiTotal = $('kpi-total');
    dom.kpiTotalSub = $('kpi-total-sub');
    dom.kpiOk = $('kpi-ok');
    dom.kpiOkSub = $('kpi-ok-sub');
    dom.kpiAffected = $('kpi-affected');
    dom.kpiAffectedSub = $('kpi-affected-sub');
    dom.kpiIncidents = $('kpi-incidents');
    dom.kpiIncidentsSub = $('kpi-incidents-sub');
    dom.kpiLatency = $('kpi-latency');
    dom.kpiLatencySub = $('kpi-latency-sub');

    dom.services = $('services');
    dom.servicesEmpty = $('services-empty');
    dom.servicesNote = $('services-note');

    dom.incMeta = $('inc-meta');
    dom.incSearch = $('inc-search');
    dom.incSearchClear = $('inc-search-clear');
    dom.incChips = document.querySelector('.chips');
    dom.incStatus = $('inc-status');
    dom.incHours = $('inc-hours');
    dom.incTableWrap = $('inc-table-wrap');
    dom.incTable = dom.incTableWrap ? dom.incTableWrap.querySelector('.inc-table') : null;
    dom.incTbody = $('inc-tbody');
    dom.incEmpty = $('inc-empty');
    dom.incEmptyTitle = $('inc-empty-title');
    dom.incEmptySub = $('inc-empty-sub');
    dom.incCount = $('inc-count');
    dom.incMore = $('inc-more');

    dom.liveRegion = $('live-status');
    dom.tooltip = $('tooltip');
  }

  /* ------------------------------------------------------------------ *
   *  Состояние
   * ------------------------------------------------------------------ */
  var autoUpdate = true;
  var pollTimer = null;
  var stateInFlight = false;
  var lastState = null;
  var lastSuccessAt = 0;
  var stateOffline = false;
  var nextCheckBase = null;
  var transientNote = null;
  var checkInFlight = false;
  var exportInFlight = false;
  var tickCount = 0;
  var lastAnnounceSig = null;
  var cardMap = new Map();
  var resizeObserver = null;
  var redrawQueued = false;

  var inc = {
    q: '',
    service: 'all',
    status: 'all',
    hours: 0,
    items: [],
    total: 0,
    loading: false,
    error: false,
    reqId: 0,
    lastLoadAt: 0,
    rows: []
  };

  var incSearchTimer = null;
  var incEmptyRetry = null;

  /* ------------------------------------------------------------------ *
   *  Сеть
   * ------------------------------------------------------------------ */
  function fetchJson(url, opts) {
    opts = opts || {};
    var timeout = Number(opts.timeout) || 8000;
    var ctrl = new AbortController();
    var timer = setTimeout(function () {
      try { ctrl.abort(); } catch (e) { /* noop */ }
    }, timeout);
    var init = {
      method: opts.method || 'GET',
      cache: 'no-store',
      headers: { Accept: 'application/json' },
      signal: ctrl.signal
    };
    return fetch(url, init)
      .then(function (res) {
        if (!res.ok) {
          var err = new Error('HTTP ' + res.status);
          err.status = res.status;
          throw err;
        }
        return res.json();
      })
      .finally(function () { clearTimeout(timer); });
  }

  function isAbortError(err) {
    return !!err && (err.name === 'AbortError' || err.name === 'TimeoutError');
  }

  /* ------------------------------------------------------------------ *
   *  Опрос /api/state
   * ------------------------------------------------------------------ */
  function schedulePoll(delay) {
    if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
    if (!autoUpdate) return;
    pollTimer = setTimeout(onPollTick, typeof delay === 'number' ? delay : POLL_MS);
  }

  function onPollTick() {
    pollTimer = null;
    if (!autoUpdate) return;
    if (document.hidden) {
      schedulePoll(1000);
      return;
    }
    loadState();
  }

  function loadState() {
    if (stateInFlight) return;
    stateInFlight = true;
    fetchJson('/api/state', { timeout: STATE_TIMEOUT })
      .then(function (data) {
        if (!data || typeof data !== 'object') throw new Error('bad payload');
        applyState(data);
      })
      .catch(function () {
        markOffline();
      })
      .finally(function () {
        stateInFlight = false;
        schedulePoll(POLL_MS);
      });
  }

  function markOffline() {
    if (stateOffline) return;
    stateOffline = true;
    lastAnnounceSig = 'offline';
    updateLiveIndicator();
    renderBanner(lastState);
    announce('Нет связи с сервером мониторинга. Повторяем попытки.');
    updateTitle();
    tick();
  }

  function applyState(data) {
    var prevState = lastState;
    var wasOffline = stateOffline;
    lastState = data;
    lastSuccessAt = Date.now();
    stateOffline = false;

    var nextRaw = data.next_check_in;
    var nextValue = (nextRaw === null || nextRaw === undefined || nextRaw === '')
      ? (Number(data.poll_interval) || 20)
      : Number(nextRaw);
    if (!isFinite(nextValue)) nextValue = 20;
    nextCheckBase = {
      value: nextValue,
      at: Date.now(),
      checking: !!data.checking
    };

    renderHeader(data);
    renderBanner(data);
    renderKpi(data);

    var services = orderedServices(data);
    renderServices(services);

    maybeRefreshIncidents(prevState, data, wasOffline);
    updateTitle();
    maybeAnnounce(services, wasOffline);
    tick();
  }

  /* ------------------------------------------------------------------ *
   *  Заголовок, live-индикатор
   * ------------------------------------------------------------------ */
  function renderHeader(data) {
    var summary = (data && data.summary) || {};
    var overall = normalizeStatus(summary.overall);
    var overallLabel = summary.overall_label || STATUS_LABEL[overall] || 'Нет данных';

    if (dom.overallChip) dom.overallChip.className = 'overall-chip status-' + overall;
    setText(dom.overallText, overallLabel);

    updateLiveIndicator();
  }

  function updateLiveIndicator() {
    if (!dom.liveDot) return;
    var cls = 'live-unknown';
    var text = 'обновлено —';

    if (stateOffline) {
      cls = 'live-off';
      text = lastState ? 'нет связи · ' + fmtClock(lastState.generated_at || lastState.epoch || lastSuccessAt / 1000) : 'нет связи';
    } else if (lastState) {
      cls = autoUpdate ? 'live-ok' : 'live-warn';
      text = 'обновлено ' + fmtClock(lastState.generated_at || lastState.epoch || lastSuccessAt / 1000);
    }

    dom.liveDot.className = 'live-dot ' + cls;
    dom.liveDot.title = stateOffline
      ? 'Нет связи с сервером мониторинга'
      : (lastSuccessAt ? 'Последнее успешное обновление: ' + fmtDateTimeFull(lastSuccessAt / 1000) : 'Ожидание данных');
    setText(dom.liveText, text);
  }

  function updateTitle() {
    var title = 'Мониторинг доступности';
    if (stateOffline) {
      title = '⚠ Нет связи — ' + title;
    } else if (lastState && lastState.summary && lastState.summary.overall && lastState.summary.overall !== 'ok') {
      title = (lastState.summary.overall_label || 'Есть проблемы') + ' — ' + title;
    }
    if (document.title !== title) document.title = title;
  }

  /* ------------------------------------------------------------------ *
   *  Баннер
   * ------------------------------------------------------------------ */
  function renderBanner(data) {
    if (!dom.banner) return;

    var kind = null;
    var title = '';
    var sub = '';

    if (stateOffline) {
      kind = 'offline';
      title = 'Нет связи с сервером мониторинга';
      sub = lastState
        ? 'Данные могли устареть: последнее обновление ' + fmtClock(lastState.generated_at || lastState.epoch) +
          '. Повторяем попытки каждые ' + Math.round(POLL_MS / 1000) + ' с.'
        : 'Повторяем попытки каждые ' + Math.round(POLL_MS / 1000) + ' с.';
    } else if (data && data.internet && data.internet.status && data.internet.status !== 'ok') {
      var st = normalizeStatus(data.internet.status);
      if (st === 'down' || st === 'degraded') {
        kind = st === 'down' ? 'critical' : 'warn';
        title = st === 'down' ? 'Похоже, нет интернета — проверки недоступны' : 'Интернет нестабилен';
        sub = st === 'down'
          ? 'Локальный сервер работает, но внешние проверки не проходят. Статусы обновятся автоматически, когда канал восстановится.'
          : 'Часть проверок может завершаться ошибками.' +
            (data.internet.latency_ms !== null && data.internet.latency_ms !== undefined
              ? ' Отклик контрольного канала: ' + fmtLatency(data.internet.latency_ms) + '.'
              : '');
      }
    }

    if (!kind) {
      dom.banner.hidden = true;
      return;
    }

    dom.banner.hidden = false;
    dom.banner.className = 'banner' + (kind === 'warn' ? '' : ' is-' + kind);
    if (dom.bannerIcon) dom.bannerIcon.innerHTML = BANNER_ICONS[kind] || BANNER_ICONS.warn;
    setText(dom.bannerTitle, title);
    setText(dom.bannerSub, sub);
  }

  /* ------------------------------------------------------------------ *
   *  KPI
   * ------------------------------------------------------------------ */
  function renderKpi(data) {
    var s = (data && data.summary) || {};
    var total = Number(s.services_total);
    var ok = Number(s.services_ok);
    var affected = Number(s.services_affected);
    var incidents = Number(s.active_incidents);

    setText(dom.kpiTotal, fmtInt(s.services_total));
    setText(dom.kpiOk, fmtInt(s.services_ok));
    setText(dom.kpiAffected, fmtInt(s.services_affected));
    setText(dom.kpiIncidents, fmtInt(s.active_incidents));
    setText(dom.kpiLatency, s.avg_latency_ms === null || s.avg_latency_ms === undefined ? '—' : fmtLatency(s.avg_latency_ms));

    setTone(dom.kpiOk, isFinite(ok) && ok > 0 ? 'tone-ok' : 'tone-muted');
    setTone(dom.kpiAffected, isFinite(affected) && affected > 0 ? 'tone-warn' : 'tone-muted');
    setTone(dom.kpiIncidents, isFinite(incidents) && incidents > 0 ? 'tone-bad' : 'tone-muted');

    setText(dom.kpiTotalSub, 'в мониторинге');
    setText(dom.kpiOkSub, isFinite(total) && total > 0 ? 'из ' + fmtInt(total) + ' сервисов' : '—');
    setText(dom.kpiAffectedSub, isFinite(affected) && affected > 0 ? 'деградация или блокировка' : 'проблем нет');
    setText(dom.kpiIncidentsSub, isFinite(incidents) && incidents > 0 ? 'требуют внимания' : 'нет активных');
    setText(dom.kpiLatencySub, s.checks_total !== null && s.checks_total !== undefined ? fmtInt(s.checks_total) + ' проверок' : 'по всем проверкам');
  }

  function setTone(node, tone) {
    if (!node) return;
    node.classList.remove('tone-ok', 'tone-warn', 'tone-bad', 'tone-muted');
    node.classList.add(tone);
  }

  /* ------------------------------------------------------------------ *
   *  Сервисы
   * ------------------------------------------------------------------ */
  function orderedServices(data) {
    var raw = data && Array.isArray(data.services) ? data.services : [];
    var list = raw.filter(function (s) { return s && typeof s === 'object' && typeof s.id === 'string'; });
    var out = [];

    SERVICE_ORDER.forEach(function (id) {
      var found = null;
      for (var i = 0; i < list.length; i++) {
        if (list[i].id === id) { found = list[i]; break; }
      }
      if (found) out.push(found);
    });

    list.forEach(function (s) {
      if (SERVICE_ORDER.indexOf(s.id) === -1) out.push(s);
    });

    var hasInternet = out.some(function (s) { return s.id === 'internet'; });
    if (!hasInternet && data && data.internet) {
      out.push(synthesizeInternet(data.internet));
    }
    return out;
  }

  function synthesizeInternet(inet) {
    var st = normalizeStatus(inet.status);
    var fb = SERVICE_FALLBACK.internet;
    return {
      id: 'internet',
      name: fb.name,
      subtitle: fb.subtitle,
      icon: 'internet',
      accent: fb.accent,
      status: st,
      status_label: INTERNET_LABEL[st] || inet.label || STATUS_LABEL[st],
      status_reason: null,
      hint: null,
      latency_ms: inet.latency_ms === undefined ? null : inet.latency_ms,
      uptime_1h: null,
      uptime_24h: null,
      checks_ok: null,
      checks_total: null,
      checks: [],
      series: [],
      active_incident: null,
      last_incident: null,
      _synthetic: true
    };
  }

  var CARD_TEMPLATE = [
    '<div class="card-head">',
    '  <span class="card-icon" aria-hidden="true"></span>',
    '  <div class="card-title">',
    '    <h3 class="card-name"></h3>',
    '  </div>',
    '  <span class="pill status-unknown"><span class="pill-dot" aria-hidden="true"></span><span class="pill-text">—</span></span>',
    '</div>',
    '<p class="card-sub"></p>',
    '<p class="card-reason is-muted">—</p>',
    '<p class="card-hint" hidden>',
    '  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9.5 18h5"/><path d="M10 21h4"/><path d="M12 3a6 6 0 0 0-3.6 10.8c.6.5.9 1.2.9 2V16h5.4v-.2c0-.8.3-1.5.9-2A6 6 0 0 0 12 3z"/></svg>',
    '  <span class="card-hint-text"></span>',
    '</p>',
    '<div class="card-incident" hidden><span class="inc-flag" aria-hidden="true"></span><span class="card-incident-text"></span></div>',
    '<dl class="metrics">',
    '  <div class="metric"><dt>Отклик</dt><dd data-m="latency">—</dd></div>',
    '  <div class="metric"><dt>Uptime 1 ч</dt><dd data-m="up1h">—</dd></div>',
    '  <div class="metric"><dt>Uptime 24 ч</dt><dd data-m="up24h">—</dd></div>',
    '</dl>',
    '<p class="card-checks-note" hidden></p>',
    '<div class="viz">',
    '  <div class="viz-head"><span>Отклик</span><span class="viz-legend"><span><i class="lg-ok"></i>норма</span><span><i class="lg-fail"></i>сбой</span></span></div>',
    '  <div class="spark"><canvas></canvas><span class="spark-empty" hidden>нет данных</span></div>',
    '  <div class="band" role="img"></div>',
    '</div>',
    '<div class="stages">',
    '  <div class="stage" data-stage="dns"><span class="stage-label">DNS</span><span class="stage-value">—</span><span class="stage-mark">–</span></div>',
    '  <div class="stage" data-stage="tcp"><span class="stage-label">TCP</span><span class="stage-value">—</span><span class="stage-mark">–</span></div>',
    '  <div class="stage" data-stage="tls"><span class="stage-label">TLS</span><span class="stage-value">—</span><span class="stage-mark">–</span></div>',
    '  <div class="stage" data-stage="http"><span class="stage-label">HTTP</span><span class="stage-value">—</span><span class="stage-mark">–</span></div>',
    '</div>',
    '<details class="checks">',
    '  <summary><span class="checks-title">Детали проверок</span><span class="checks-count">0</span>',
    '    <svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>',
    '  </summary>',
    '  <ul class="checks-list"></ul>',
    '</details>'
  ].join('\n');

  var STAGE_KEYS = [
    { stage: 'dns', timing: 'dns_ms' },
    { stage: 'tcp', timing: 'tcp_ms' },
    { stage: 'tls', timing: 'tls_ms' },
    { stage: 'http', timing: 'http_ms' }
  ];

  function createCard(id) {
    var root = document.createElement('article');
    root.className = 'card';
    root.dataset.id = id;
    root.dataset.status = 'unknown';
    root.setAttribute('aria-labelledby', 'svc-title-' + id);
    root.innerHTML = CARD_TEMPLATE;

    var name = root.querySelector('.card-name');
    if (name) name.id = 'svc-title-' + id;

    var stageEls = {};
    Array.prototype.forEach.call(root.querySelectorAll('.stage'), function (node) {
      stageEls[node.dataset.stage] = {
        root: node,
        value: node.querySelector('.stage-value'),
        mark: node.querySelector('.stage-mark')
      };
    });

    var card = {
      id: id,
      root: root,
      icon: root.querySelector('.card-icon'),
      name: name,
      subtitle: root.querySelector('.card-sub'),
      pill: root.querySelector('.pill'),
      pillText: root.querySelector('.pill-text'),
      reason: root.querySelector('.card-reason'),
      hint: root.querySelector('.card-hint'),
      hintText: root.querySelector('.card-hint-text'),
      incident: root.querySelector('.card-incident'),
      incidentText: root.querySelector('.card-incident-text'),
      latency: root.querySelector('[data-m="latency"]'),
      up1h: root.querySelector('[data-m="up1h"]'),
      up24h: root.querySelector('[data-m="up24h"]'),
      checksNote: root.querySelector('.card-checks-note'),
      spark: root.querySelector('.spark'),
      canvas: root.querySelector('canvas'),
      sparkEmpty: root.querySelector('.spark-empty'),
      band: root.querySelector('.band'),
      bandData: [],
      stageEls: stageEls,
      details: root.querySelector('.checks'),
      checksCount: root.querySelector('.checks-count'),
      checksList: root.querySelector('.checks-list'),
      checksSig: null,
      series: [],
      accent: serviceAccent(id),
      activeStarted: null
    };


    if (card.band) {
      card.band.addEventListener('pointermove', function (e) {
        var seg = e.target && e.target.closest ? e.target.closest('.seg') : null;
        if (!seg) { hideTip(); return; }
        var idx = Number(seg.dataset.i);
        var p = card.bandData[idx];
        if (!p) { hideTip(); return; }
        var st = normalizeStatus(p.status);
        var lines = [
          { text: fmtClock(p.t), cls: 'tip-muted' },
          { text: 'Статус: ' + statusLabel(st), cls: st === 'ok' ? 'tip-ok' : (st === 'unknown' ? 'tip-muted' : 'tip-bad') },
          { text: 'Задержка: ' + fmtLatency(p.latency_ms) }
        ];
        showTip(e.clientX, e.clientY, lines);
      });
      card.band.addEventListener('pointerleave', hideTip);
    }

    return card;
  }

  function updateCard(card, svc) {
    var id = card.id;
    var fallback = SERVICE_FALLBACK[id] || {};
    var status = normalizeStatus(svc.status);
    var accent = isHexColor(svc.accent) ? svc.accent : (fallback.accent || serviceAccent(id));

    card.accent = accent;
    card.root.style.setProperty('--accent', accent);
    card.root.dataset.status = status;

    if (card.icon) card.icon.innerHTML = ICONS[id] || ICONS.unknown;
    setText(card.name, svc.name || fallback.name || id);
    var subtitle = svc.subtitle || fallback.subtitle || '';
    setText(card.subtitle, subtitle);
    if (card.subtitle) card.subtitle.title = subtitle;

    var label = labelForService(svc);
    var pulsing = !!svc.active_incident && (status === 'blocked' || status === 'down' || status === 'degraded');
    card.pill.className = 'pill status-' + status + (pulsing ? ' is-pulsing' : '');
    setText(card.pillText, label);
    if (card.pill) card.pill.title = label;

    var reason = svc.status_reason || '';
    setText(card.reason, reason || '—');
    card.reason.classList.toggle('is-muted', !reason);

    if (svc.hint) {
      card.hint.hidden = false;
      setText(card.hintText, svc.hint);
    } else {
      card.hint.hidden = true;
      setText(card.hintText, '');
    }

    var active = svc.active_incident;
    var last = svc.last_incident;
    card.activeStarted = null;
    if (active && typeof active === 'object') {
      var started = epochFrom(active.started_epoch, active.started_at);
      var dur = started !== null ? Math.max(0, Date.now() / 1000 - started) : null;
      card.activeStarted = started;
      card.incident.hidden = false;
      card.incident.classList.remove('is-past');
      setText(card.incidentText, 'Сбой идёт' + (dur !== null ? ' · ' + fmtDuration(dur) : ''));
      card.incident.title = active.title || active.reason || '';
    } else if (last && typeof last === 'object') {
      var ended = epochFrom(last.ended_epoch, last.ended_at) || epochFrom(last.started_epoch, last.started_at);
      card.incident.hidden = false;
      card.incident.classList.add('is-past');
      setText(card.incidentText, 'Последний сбой · ' + relTime(ended));
      card.incident.title = last.title || last.reason || '';
    } else {
      card.incident.hidden = true;
      setText(card.incidentText, '');
      card.incident.removeAttribute('title');
    }

    var hasLatency = svc.latency_ms !== null && svc.latency_ms !== undefined;
    setText(card.latency, hasLatency ? fmtLatency(svc.latency_ms) : '—');
    card.latency.classList.toggle('dim', !hasLatency);
    var hasUp1 = svc.uptime_1h !== null && svc.uptime_1h !== undefined;
    var hasUp24 = svc.uptime_24h !== null && svc.uptime_24h !== undefined;
    setText(card.up1h, hasUp1 ? fmtUptime(svc.uptime_1h) : '—');
    card.up1h.classList.toggle('dim', !hasUp1);
    setText(card.up24h, hasUp24 ? fmtUptime(svc.uptime_24h) : '—');
    card.up24h.classList.toggle('dim', !hasUp24);

    var checksTotal = (svc.checks_total === null || svc.checks_total === undefined) ? null : Number(svc.checks_total);
    var checksOk = (svc.checks_ok === null || svc.checks_ok === undefined) ? null : Number(svc.checks_ok);
    if (card.checksNote) {
      if (checksTotal !== null && isFinite(checksTotal) && checksTotal > 0) {
        card.checksNote.hidden = false;
        setText(card.checksNote,
          (checksOk !== null && isFinite(checksOk) ? Math.round(checksOk) : '—') + '/' + Math.round(checksTotal) + ' проверок');
        card.checksNote.classList.toggle('is-bad', checksOk !== null && isFinite(checksOk) && checksOk === 0);
      } else {
        card.checksNote.hidden = true;
        setText(card.checksNote, '');
      }
    }

    card.series = Array.isArray(svc.series) ? svc.series.filter(function (p) { return p && typeof p === 'object'; }) : [];
    card.checks = Array.isArray(svc.checks) ? svc.checks.filter(function (c) { return c && typeof c === 'object'; }) : [];

    drawSpark(card);
    renderBand(card);
    renderStages(card);
    renderChecks(card);
  }

  function renderServices(services) {
    if (!dom.services) return;

    var ids = [];
    services.forEach(function (svc) {
      if (!svc || !svc.id) return;
      ids.push(svc.id);
      var card = cardMap.get(svc.id);
      if (!card) {
        card = createCard(svc.id);
        cardMap.set(svc.id, card);
        dom.services.appendChild(card.root);
        if (resizeObserver && card.spark) resizeObserver.observe(card.spark);
      } else {
        dom.services.appendChild(card.root);
      }
      updateCard(card, svc);
    });

    cardMap.forEach(function (card, id) {
      if (ids.indexOf(id) === -1) {
        card.root.remove();
        cardMap.delete(id);
      }
    });

    dom.servicesEmpty.hidden = services.length > 0;

    if (dom.servicesNote) {
      var data = lastState || {};
      var poll = Number(data.poll_interval);
      var note = '';
      if (isFinite(poll) && poll > 0) note = 'опрос каждые ' + Math.round(poll) + ' с';
      if (data.generated_at) note += (note ? ' · ' : '') + 'обновлено ' + fmtClock(data.generated_at);
      dom.servicesNote.textContent = note;
    }
  }

  /* ------------------------------------------------------------------ *
   *  Постадийные проверки
   * ------------------------------------------------------------------ */
  function computeStages(checks) {
    var result = {};
    var core = checks.filter(function (c) { return c && c.core; });
    var pool = core.length ? core : checks;

    STAGE_KEYS.forEach(function (def) {
      var value = null;
      var failed = false;

      pool.forEach(function (c) {
        if (!c) return;
        var timings = c.timings || {};
        var t = timings[def.timing];
        if (value === null && t !== null && t !== undefined && isFinite(Number(t))) {
          value = Number(t);
        }
        if (c.ok === false && c.stage === def.stage) failed = true;
      });

      var state = 'skip';
      if (failed) state = 'fail';
      else if (value !== null) state = 'ok';

      result[def.stage] = { state: state, value: value };
    });

    return result;
  }

  function renderStages(card) {
    var stages = computeStages(card.checks || []);
    STAGE_KEYS.forEach(function (def) {
      var node = card.stageEls[def.stage];
      if (!node) return;
      var info = stages[def.stage];
      node.root.classList.remove('is-ok', 'is-fail', 'is-skip');
      node.root.classList.add('is-' + info.state);
      setText(node.value, info.value !== null ? fmtLatency(info.value) : '—');
      setText(node.mark, info.state === 'ok' ? '✓' : (info.state === 'fail' ? '✕' : '–'));
      node.root.title = 'Этап ' + def.stage.toUpperCase() + ': ' +
        (info.state === 'ok' ? 'пройден' : (info.state === 'fail' ? 'остановился здесь' : 'не выполнялся'));
    });
  }

  /* ------------------------------------------------------------------ *
   *  Полоса последних проверок
   * ------------------------------------------------------------------ */
  function renderBand(card) {
    if (!card.band) return;
    var last = card.series.slice(-RECENT_CHECKS);
    card.bandData = last;

    if (!last.length) {
      var empty = el('span', 'band-empty', 'нет данных');
      card.band.replaceChildren(empty);
      card.band.setAttribute('aria-label', 'Нет данных о последних проверках');
      return;
    }

    var frag = document.createDocumentFragment();
    var ok = 0;
    var fail = 0;
    var unknown = 0;

    last.forEach(function (p, i) {
      var st = normalizeStatus(p.status);
      var seg = document.createElement('span');
      seg.className = 'seg s-' + st;
      seg.dataset.i = String(i);
      seg.setAttribute('aria-hidden', 'true');
      seg.title = fmtClock(p.t) + ' · ' + statusLabel(st) + ' · ' + fmtLatency(p.latency_ms);
      if (st === 'ok') ok++;
      else if (st === 'unknown') unknown++;
      else fail++;
      frag.appendChild(seg);
    });

    card.band.replaceChildren(frag);
    card.band.setAttribute('aria-label',
      'Последние ' + last.length + ' проверок: ' + ok + ' успешных, ' + fail + ' со сбоями' +
      (unknown ? ', ' + unknown + ' без данных' : ''));
  }

  /* ------------------------------------------------------------------ *
   *  Sparkline (canvas, DPR-aware)
   * ------------------------------------------------------------------ */
  function drawSpark(card) {
    var wrap = card.spark;
    var canvas = card.canvas;
    if (!wrap || !canvas) return;

    var w = Math.floor(wrap.clientWidth);
    var h = Math.floor(wrap.clientHeight) || 62;
    if (w < 8) return;

    var dpr = Math.min(window.devicePixelRatio || 1, 2.5);
    var pxW = Math.round(w * dpr);
    var pxH = Math.round(h * dpr);
    if (canvas.width !== pxW || canvas.height !== pxH) {
      canvas.width = pxW;
      canvas.height = pxH;
    }

    var ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    var padX = 2;
    var padTop = 6;
    var padBottom = 10;
    var innerW = Math.max(1, w - padX * 2);
    var innerH = Math.max(1, h - padTop - padBottom);
    var baseline = padTop + innerH;

    // фоновая линия
    ctx.strokeStyle = 'rgba(255,255,255,0.055)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padX, baseline + 0.5);
    ctx.lineTo(w - padX, baseline + 0.5);
    ctx.stroke();

    var series = card.series || [];
    if (!series.length) {
      if (card.sparkEmpty) {
        card.sparkEmpty.hidden = false;
        setText(card.sparkEmpty, 'нет данных');
      }
      return;
    }
    if (card.sparkEmpty) card.sparkEmpty.hidden = true;

    var pts = [];
    series.forEach(function (p) {
      var t = Number(p && p.t);
      if (!isFinite(t)) return;
      var lat = p.latency_ms === null || p.latency_ms === undefined ? null : Number(p.latency_ms);
      if (lat !== null && (!isFinite(lat) || lat < 0)) lat = null;
      pts.push({ t: t, lat: lat, status: normalizeStatus(p.status) });
    });
    if (!pts.length) {
      if (card.sparkEmpty) {
        card.sparkEmpty.hidden = false;
        setText(card.sparkEmpty, 'нет данных');
      }
      return;
    }

    var t0 = pts[0].t;
    var t1 = pts[pts.length - 1].t;
    var span = Math.max(1, t1 - t0);
    var single = pts.length === 1;
    var step = single ? innerW : innerW / (pts.length - 1);

    function xOf(p) {
      if (single) return padX + innerW / 2;
      return padX + ((p.t - t0) / span) * innerW;
    }

    var lats = pts.filter(function (p) { return p.lat !== null; }).map(function (p) { return p.lat; });
    var maxLat = lats.length ? Math.max.apply(null, lats) : 0;
    if (!(maxLat > 0)) maxLat = 1;

    function yOf(lat) {
      return padTop + innerH - (lat / maxLat) * innerH;
    }

    // красные полосы провалов
    var bandW = Math.max(2, Math.min(6, step * 0.72));
    pts.forEach(function (p) {
      if (SKIP_STATUSES[p.status] !== undefined) return;
      var x = xOf(p);
      ctx.fillStyle = 'rgba(255, 64, 78, 0.10)';
      ctx.fillRect(x - bandW / 2, padTop, bandW, innerH);
    });

    // линия задержки, разорванная на null-значениях
    var segments = [];
    var current = [];
    pts.forEach(function (p) {
      if (p.lat !== null) {
        current.push(p);
      } else if (current.length) {
        segments.push(current);
        current = [];
      }
    });
    if (current.length) segments.push(current);

    var accent = card.accent || '#7fa7c9';
    segments.forEach(function (seg) {
      if (seg.length === 1) {
        var p0 = seg[0];
        ctx.fillStyle = accent;
        ctx.beginPath();
        ctx.arc(xOf(p0), yOf(p0.lat), 2.1, 0, Math.PI * 2);
        ctx.fill();
        return;
      }

      ctx.beginPath();
      seg.forEach(function (p, i) {
        var x = xOf(p);
        var y = yOf(p.lat);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });

      // заливка под линией
      var grad = ctx.createLinearGradient(0, padTop, 0, baseline);
      grad.addColorStop(0, hexToRgba(accent, 0.26));
      grad.addColorStop(1, hexToRgba(accent, 0.015));
      ctx.lineTo(xOf(seg[seg.length - 1]), baseline);
      ctx.lineTo(xOf(seg[0]), baseline);
      ctx.closePath();
      ctx.fillStyle = grad;
      ctx.fill();

      // сама линия
      ctx.beginPath();
      seg.forEach(function (p, i) {
        var x = xOf(p);
        var y = yOf(p.lat);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.strokeStyle = accent;
      ctx.lineWidth = 1.6;
      ctx.lineJoin = 'round';
      ctx.lineCap = 'round';
      ctx.stroke();
    });

    // точки: сбои — красным, мало данных — все точки
    var sparse = pts.length < 10;
    pts.forEach(function (p) {
      var x = xOf(p);
      if (p.lat === null) {
        ctx.fillStyle = p.status === 'unknown' ? 'rgba(135,152,173,0.55)' : 'rgba(255, 77, 94, 0.85)';
        ctx.fillRect(x - 1, baseline - 2, 2, 4);
        return;
      }
      if (SKIP_STATUSES[p.status] === undefined) {
        ctx.fillStyle = '#ff4d5e';
        ctx.beginPath();
        ctx.arc(x, yOf(p.lat), 2.1, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = 'rgba(255, 77, 94, 0.22)';
        ctx.beginPath();
        ctx.arc(x, yOf(p.lat), 4.4, 0, Math.PI * 2);
        ctx.fill();
      } else if (sparse) {
        ctx.fillStyle = accent;
        ctx.beginPath();
        ctx.arc(x, yOf(p.lat), 1.9, 0, Math.PI * 2);
        ctx.fill();
      }
    });

    // последняя точка
    var lastP = pts[pts.length - 1];
    if (lastP.lat !== null) {
      var lx = xOf(lastP);
      var ly = yOf(lastP.lat);
      ctx.fillStyle = '#0b1017';
      ctx.beginPath();
      ctx.arc(lx, ly, 3.6, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = accent;
      ctx.beginPath();
      ctx.arc(lx, ly, 2.4, 0, Math.PI * 2);
      ctx.fill();
    }

    // если задержек нет вовсе
    if (!lats.length) {
      ctx.fillStyle = 'rgba(135, 152, 173, 0.9)';
      ctx.font = '11px -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('нет отклика', w / 2, padTop + innerH / 2);
    }
  }

  function scheduleRedraw() {
    if (redrawQueued) return;
    redrawQueued = true;
    requestAnimationFrame(function () {
      redrawQueued = false;
      cardMap.forEach(function (card) { drawSpark(card); });
    });
  }

  /* ------------------------------------------------------------------ *
   *  Детали проверок
   * ------------------------------------------------------------------ */
  function checksSignature(checks) {
    return checks.map(function (c) {
      return [c.id, c.ok, c.stage, c.error, c.http_status, c.ip, c.latency_ms,
        c.error_label, c.error_text].join('~');
    }).join('|');
  }

  function renderChecks(card) {
    var checks = card.checks || [];
    var sig = checksSignature(checks);
    if (sig === card.checksSig) return;
    card.checksSig = sig;

    var fail = checks.filter(function (c) { return c && c.ok === false; }).length;
    setText(card.checksCount, String(checks.length));
    if (card.checksCount) card.checksCount.classList.toggle('has-fail', fail > 0);

    if (!checks.length) {
      card.checksList.replaceChildren(el('li', 'checks-empty', 'Нет данных о проверках'));
      return;
    }

    var frag = document.createDocumentFragment();
    checks.forEach(function (c) { frag.appendChild(buildCheckRow(c)); });
    card.checksList.replaceChildren(frag);
  }

  function buildCheckRow(c) {
    var ok = c.ok !== false;
    var li = el('li', 'check');

    var status = el('span', 'check-status ' + (ok ? 'ok' : 'fail'), ok ? '✓' : '✕');
    status.setAttribute('aria-label', ok ? 'Успешно' : 'Ошибка');

    var body = el('div', 'check-body');

    var line = el('div', 'check-line');
    line.appendChild(el('span', 'check-label', c.label || c.id || 'Проверка'));
    if (c.kind) line.appendChild(el('span', 'check-kind', String(c.kind)));
    if (c.core) {
      var coreBadge = el('span', 'check-kind check-core', 'ключевая');
      coreBadge.title = 'ключевая проверка';
      line.appendChild(coreBadge);
    }
    body.appendChild(line);

    var metaParts = [];
    if (c.target) metaParts.push(String(c.target));
    if (c.ip) metaParts.push(String(c.ip));
    if (c.http_status !== null && c.http_status !== undefined) metaParts.push('HTTP ' + c.http_status);
    if (metaParts.length) {
      var meta = el('div', 'check-meta', metaParts.join(' · '));
      meta.title = metaParts.join(' · ');
      body.appendChild(meta);
    }

    if (!ok) {
      var errBox = el('div', 'check-error');
      var errLabel = c.error_label || c.error || 'Ошибка проверки';
      errBox.appendChild(el('span', 'check-error-label', String(errLabel)));
      if (c.error_text) {
        var errText = el('span', 'check-error-text', String(c.error_text));
        errText.title = String(c.error_text);
        errBox.appendChild(errText);
      }
      body.appendChild(errBox);
    }

    var timings = c.timings || {};
    var parts = [
      'DNS ' + fmtLatency(timings.dns_ms),
      'TCP ' + fmtLatency(timings.tcp_ms),
      'TLS ' + fmtLatency(timings.tls_ms),
      'HTTP ' + fmtLatency(timings.http_ms)
    ];
    var timingsLine = el('div', 'check-timings', parts.join(' · '));
    timingsLine.title = parts.join(' · ');
    body.appendChild(timingsLine);

    var lat = el('span', 'check-lat' + (ok ? '' : ' bad'), fmtLatency(c.latency_ms));

    li.appendChild(status);
    li.appendChild(body);
    li.appendChild(lat);
    return li;
  }

  /* ------------------------------------------------------------------ *
   *  Tooltip
   * ------------------------------------------------------------------ */
  function showTip(clientX, clientY, lines) {
    var tip = dom.tooltip;
    if (!tip || !lines || !lines.length) return;

    var frag = document.createDocumentFragment();
    lines.forEach(function (line) {
      var node = el('div', 'tip-line ' + (line.cls || ''), line.text);
      frag.appendChild(node);
    });
    tip.replaceChildren(frag);
    tip.hidden = false;

    var rect = tip.getBoundingClientRect();
    var pad = 8;
    var left = clientX - rect.width / 2;
    left = Math.max(pad, Math.min(left, window.innerWidth - rect.width - pad));
    var top = clientY - rect.height - 12;
    if (top < pad) top = clientY + 18;
    if (top + rect.height > window.innerHeight - pad) {
      top = Math.max(pad, window.innerHeight - rect.height - pad);
    }
    tip.style.left = Math.round(left) + 'px';
    tip.style.top = Math.round(top) + 'px';
  }

  function hideTip() {
    if (dom.tooltip) dom.tooltip.hidden = true;
  }

  /* ------------------------------------------------------------------ *
   *  Журнал сбоев
   * ------------------------------------------------------------------ */
  function hasActiveFilters() {
    return !!inc.q || inc.service !== 'all' || inc.status !== 'all' || Number(inc.hours) !== 0;
  }

  function buildIncidentsUrl(offset, limit) {
    var params = new URLSearchParams();
    if (inc.q) params.set('q', inc.q);
    params.set('service', inc.service);
    params.set('status', inc.status);
    params.set('hours', String(inc.hours || 0));
    params.set('limit', String(limit));
    params.set('offset', String(offset));
    return '/api/incidents?' + params.toString();
  }

  function loadIncidents(opts) {
    opts = opts || {};
    var append = !!opts.append;
    var reqId = ++inc.reqId;
    var offset = append ? inc.items.length : 0;
    var limit = append ? INC_PAGE : Math.min(500, Math.max(INC_PAGE, inc.items.length));

    inc.loading = true;
    inc.error = false;
    updateIncTableLoading(true);
    if (!append && inc.items.length === 0) renderIncSkeleton();
    renderIncMeta();

    fetchJson(buildIncidentsUrl(offset, limit), { timeout: INC_TIMEOUT })
      .then(function (data) {
        if (reqId !== inc.reqId) return;
        var list = data && Array.isArray(data.incidents) ? data.incidents.filter(function (i) {
          return i && typeof i === 'object';
        }) : [];
        inc.items = append ? inc.items.concat(list) : list;
        var total = Number(data && data.total);
        inc.total = isFinite(total) ? total : inc.items.length;
      })
      .catch(function () {
        if (reqId !== inc.reqId) return;
        inc.error = true;
        if (!append) {
          inc.items = [];
          inc.total = 0;
        }
      })
      .finally(function () {
        if (reqId !== inc.reqId) return;
        inc.loading = false;
        inc.lastLoadAt = Date.now();
        updateIncTableLoading(false);
        renderIncidents();
      });
  }

  function updateIncTableLoading(on) {
    if (dom.incTableWrap) dom.incTableWrap.classList.toggle('is-loading', !!on);
    if (dom.incMore) dom.incMore.disabled = !!on;
  }

  function renderIncSkeleton() {
    if (!dom.incTbody) return;
    var widths = ['w40', 'w60', 'w80', 'w60', 'w80', 'w25'];
    var frag = document.createDocumentFragment();
    for (var r = 0; r < 4; r++) {
      var tr = el('tr', 'sk-row');
      widths.forEach(function (w) {
        var td = el('td');
        td.appendChild(el('div', 'sk ' + w));
        tr.appendChild(td);
      });
      frag.appendChild(tr);
    }
    dom.incTbody.replaceChildren(frag);
    if (dom.incTable) dom.incTable.hidden = false;
    if (dom.incEmpty) dom.incEmpty.hidden = true;
  }

  function renderIncidents() {
    if (!dom.incTbody) return;
    inc.rows = [];

    if (inc.loading && inc.items.length === 0) {
      renderIncSkeleton();
      renderIncFoot();
      renderIncMeta();
      return;
    }

    if (inc.items.length === 0) {
      inc.rows = [];
      dom.incTbody.replaceChildren();
      showIncEmpty(true, inc.error);
      renderIncFoot();
      renderIncMeta();
      return;
    }

    showIncEmpty(false);
    if (dom.incTable) dom.incTable.hidden = false;

    var now = Date.now();
    var frag = document.createDocumentFragment();
    inc.items.forEach(function (item) {
      frag.appendChild(buildIncidentRow(item, now));
    });
    dom.incTbody.replaceChildren(frag);

    renderIncFoot();
    renderIncMeta();
  }

  function renderIncFoot() {
    setText(dom.incCount, 'Показано ' + inc.items.length + ' из ' + (inc.total || 0));
    if (dom.incMore) {
      dom.incMore.hidden = inc.items.length === 0 || inc.items.length >= inc.total;
      dom.incMore.disabled = inc.loading;
    }
  }

  function renderIncMeta() {
    if (!dom.incMeta) return;
    var parts = [];
    if (inc.loading) {
      parts.push('Загрузка…');
    } else if (inc.error) {
      parts.push('Нет связи с сервером');
    } else {
      parts.push('Найдено: ' + (inc.total || 0));
      var activeCount = lastState && lastState.summary ? Number(lastState.summary.active_incidents) : NaN;
      if (isFinite(activeCount)) parts.push('активных: ' + activeCount);
      if (inc.lastLoadAt) parts.push('обновлено ' + fmtClock(inc.lastLoadAt / 1000));
    }
    setText(dom.incMeta, parts.join(' · '));
  }

  function buildIncidentRow(item, now) {
    var isActive = item.status === 'active';
    var tr = el('tr', 'inc-row ' + (isActive ? 'is-active' : 'is-resolved'));
    if (item.id) tr.dataset.id = String(item.id);

    // статус
    var tdStatus = el('td');
    tdStatus.dataset.label = 'Статус';
    var statusWrap = el('span', 'cell-status');
    statusWrap.appendChild(el('span', 'status-dot'));
    statusWrap.appendChild(document.createTextNode(isActive ? 'Активен' : 'Завершён'));
    tdStatus.appendChild(statusWrap);

    // сервис
    var tdService = el('td');
    tdService.dataset.label = 'Сервис';
    var svcWrap = el('span', 'cell-service');
    var svcDot = el('span', 'service-dot');
    svcDot.style.setProperty('--svc', serviceAccent(item.service_id));
    svcWrap.appendChild(svcDot);
    svcWrap.appendChild(el('span', 'service-name', item.service_name || serviceName(item.service_id) || '—'));
    tdService.appendChild(svcWrap);

    // начало
    var startEpoch = epochFrom(item.started_epoch, item.started_at);
    var tdStart = el('td');
    tdStart.dataset.label = 'Начало';
    tdStart.appendChild(el('span', 'cell-main', fmtDateTimeShort(item.started_at || startEpoch)));
    var relEl = el('span', 'cell-sub', startEpoch !== null ? relTime(startEpoch) : '');
    tdStart.appendChild(relEl);
    if (item.started_at) tdStart.title = fmtDateTimeFull(item.started_at);
    else if (startEpoch !== null) tdStart.title = fmtDateTimeFull(startEpoch);

    // длительность
    var tdDuration = el('td');
    tdDuration.dataset.label = 'Длительность';
    var durEl = el('span', 'cell-mono ' + (isActive ? 'is-active' : 'is-resolved'));
    var initialSec = isActive && startEpoch !== null
      ? Math.max(0, now / 1000 - startEpoch)
      : Number(item.duration_sec);
    setText(durEl, fmtDuration(initialSec));
    tdDuration.appendChild(durEl);

    // причина
    var tdReason = el('td');
    tdReason.dataset.label = 'Причина';
    var reasonText = item.reason || item.title || '—';
    var reasonEl = el('span', 'cell-reason', reasonText);
    reasonEl.title = reasonText;
    tdReason.appendChild(reasonEl);

    // проверки
    var tdChecks = el('td');
    tdChecks.dataset.label = 'Проверки';
    var failed = item.failed_checks === null || item.failed_checks === undefined ? null : Number(item.failed_checks);
    var total = item.total_checks === null || item.total_checks === undefined ? null : Number(item.total_checks);
    var checksEl = el('span', 'cell-checks' + (failed === 0 ? ' is-zero' : ''));
    if (failed !== null || total !== null) {
      if (failed !== null) checksEl.appendChild(el('b', null, String(isFinite(failed) ? Math.round(failed) : '—')));
      else checksEl.appendChild(document.createTextNode('—'));
      checksEl.appendChild(document.createTextNode(' / ' + (total !== null && isFinite(total) ? Math.round(total) : '—')));
    } else {
      checksEl.textContent = '—';
    }
    if (failed !== null && total !== null && isFinite(failed) && isFinite(total)) {
      checksEl.title = failed + ' сбоев из ' + total + ' проверок';
    }
    tdChecks.appendChild(checksEl);

    tr.appendChild(tdStatus);
    tr.appendChild(tdService);
    tr.appendChild(tdStart);
    tr.appendChild(tdDuration);
    tr.appendChild(tdReason);
    tr.appendChild(tdChecks);

    inc.rows.push({
      tr: tr,
      durEl: durEl,
      relEl: relEl,
      startedEpoch: startEpoch,
      isActive: isActive,
      durationSec: Number(item.duration_sec)
    });

    return tr;
  }

  function tickIncidents(now) {
    if (!inc.rows.length) return;
    var nowSec = now / 1000;
    inc.rows.forEach(function (row) {
      if (row.isActive && row.startedEpoch !== null) {
        setText(row.durEl, fmtDuration(Math.max(0, nowSec - row.startedEpoch)));
      }
      if (tickCount % 5 === 0 && row.startedEpoch !== null) {
        setText(row.relEl, relTime(row.startedEpoch));
      }
    });
  }

  function tickCardIncidents() {
    cardMap.forEach(function (card) {
      if (card.activeStarted === null || card.incident.hidden) return;
      var dur = Math.max(0, Date.now() / 1000 - card.activeStarted);
      var text = 'Сбой идёт';
      if (card.incident.classList.contains('is-past')) return;
      setText(card.incidentText, text + ' · ' + fmtDuration(dur));
    });
  }

  function showIncEmpty(show, isError) {
    if (!dom.incEmpty) return;
    if (dom.incTable) dom.incTable.hidden = show;
    dom.incEmpty.hidden = !show;

    if (!show) {
      if (incEmptyRetry) { incEmptyRetry.remove(); incEmptyRetry = null; }
      return;
    }

    var retryNeeded = !!isError;
    if (retryNeeded) {
      setText(dom.incEmptyTitle, 'Нет связи с сервером мониторинга');
      setText(dom.incEmptySub, 'Журнал сбоев не загрузился. Повторим попытку автоматически.');
      if (!incEmptyRetry) {
        incEmptyRetry = el('button', 'btn btn-ghost btn-sm empty-retry', 'Повторить');
        incEmptyRetry.type = 'button';
        incEmptyRetry.addEventListener('click', function () { loadIncidents(); });
        dom.incEmpty.appendChild(incEmptyRetry);
      }
    } else {
      if (incEmptyRetry) { incEmptyRetry.remove(); incEmptyRetry = null; }
      setText(dom.incEmptyTitle, 'Сбоев не найдено');
      setText(dom.incEmptySub, hasActiveFilters()
        ? 'Попробуйте изменить запрос или фильтры.'
        : 'За выбранный период сбоев не зафиксировано.');
    }
  }

  function maybeRefreshIncidents(prevState, data, wasOffline) {
    var prevActive = prevState && prevState.summary ? Number(prevState.summary.active_incidents) : NaN;
    var nextActive = data && data.summary ? Number(data.summary.active_incidents) : NaN;
    var now = Date.now();
    var changed = isFinite(prevActive) && isFinite(nextActive) && prevActive !== nextActive;
    if ((changed || wasOffline) && !inc.loading && now - inc.lastLoadAt > 2500) {
      loadIncidents();
    }
  }

  /* ------------------------------------------------------------------ *
   *  Обратная связь для скринридеров
   * ------------------------------------------------------------------ */
  function announce(message) {
    var region = dom.liveRegion;
    if (!region || !message) return;
    region.textContent = '';
    setTimeout(function () { region.textContent = message; }, 40);
  }

  function maybeAnnounce(services, wasOffline) {
    var sig = stateOffline
      ? 'offline'
      : services.map(function (s) { return s.id + ':' + normalizeStatus(s.status); }).join('|');

    if (sig === lastAnnounceSig) return;
    lastAnnounceSig = sig;

    var text;
    if (stateOffline) {
      text = 'Нет связи с сервером мониторинга';
    } else {
      var problems = [];
      services.forEach(function (s) {
        if (normalizeStatus(s.status) !== 'ok') {
          problems.push((s.name || s.id) + ': ' + labelForService(s));
        }
      });
      text = (wasOffline ? 'Связь восстановлена. ' : '') +
        (problems.length ? 'Проблемы — ' + problems.join('; ') : 'Все сервисы доступны');
    }
    announce(text);
  }

  /* ------------------------------------------------------------------ *
   *  Тикер
   * ------------------------------------------------------------------ */
  function tick() {
    tickCount++;
    var now = Date.now();

    var sub;
    if (stateOffline) {
      sub = 'нет связи · повторяем попытки…';
    } else if (checkInFlight) {
      sub = 'проверка идёт…';
    } else if (transientNote && transientNote.until > now) {
      sub = transientNote.text;
    } else if (!autoUpdate) {
      sub = 'автообновление выключено';
    } else if (lastState && lastState.checking) {
      sub = 'проверка идёт…';
    } else if (nextCheckBase && isFinite(nextCheckBase.value)) {
      var elapsed = (now - nextCheckBase.at) / 1000;
      var left = nextCheckBase.value - elapsed;
      sub = left > 0.5 ? 'следующая проверка через ' + Math.ceil(left) + ' с' : 'проверка вот-вот…';
    } else {
      sub = 'ожидание данных…';
    }
    setText(dom.nextCheck, sub);

    tickIncidents(now);
    if (tickCount % 5 === 0) tickCardIncidents();
    if (tickCount % 5 === 0) hideTip();
  }

  /* ------------------------------------------------------------------ *
   *  Действия
   * ------------------------------------------------------------------ */
  function setChecking(on) {
    if (dom.btnCheck) dom.btnCheck.disabled = !!on;
    if (dom.btnCheck) dom.btnCheck.setAttribute('aria-busy', on ? 'true' : 'false');
    if (dom.checkSpinner) dom.checkSpinner.hidden = !on;
    setText(dom.checkLabel, on ? 'Проверяю…' : 'Проверить сейчас');
  }

  function runCheck() {
    if (checkInFlight) return;
    checkInFlight = true;
    setChecking(true);
    transientNote = null;

    fetchJson('/api/check', { method: 'POST', timeout: CHECK_TIMEOUT })
      .then(function (data) {
        if (data && data.state && typeof data.state === 'object') applyState(data.state);
        if (data && data.ok === false && data.reason === 'already_running') {
          transientNote = { text: 'Проверка уже идёт', until: Date.now() + 5000 };
        } else if (data && data.ok) {
          transientNote = { text: 'Проверка завершена', until: Date.now() + 5000 };
        } else {
          transientNote = { text: 'Проверка не запустилась', until: Date.now() + 5000 };
        }
        loadIncidents();
      })
      .catch(function (err) {
        if (isAbortError(err)) {
          transientNote = { text: 'Проверка не ответила за 30 с', until: Date.now() + 6000 };
        } else {
          transientNote = { text: 'Нет связи с сервером', until: Date.now() + 6000 };
          markOffline();
        }
      })
      .finally(function () {
        checkInFlight = false;
        setChecking(false);
        tick();
      });
  }

  function exportReport(e) {
    if (e) e.preventDefault();
    if (exportInFlight) return;
    exportInFlight = true;

    fetch('/api/export?format=json', { cache: 'no-store' })
      .then(function (res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        var name = 'net-monitor-report.json';
        var cd = res.headers.get('Content-Disposition') || '';
        var m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(cd);
        if (m && m[1]) {
          try {
            name = decodeURIComponent(m[1].replace(/"/g, '').trim());
          } catch (err) {
            name = 'net-monitor-report.json';
          }
        }
        return res.blob().then(function (blob) { return { blob: blob, name: name }; });
      })
      .then(function (result) {
        var url = URL.createObjectURL(result.blob);
        var a = document.createElement('a');
        a.href = url;
        a.download = result.name;
        a.rel = 'noopener';
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(function () { URL.revokeObjectURL(url); }, 3000);
        transientNote = { text: 'Отчёт скачан', until: Date.now() + 4000 };
      })
      .catch(function () {
        transientNote = { text: 'Не удалось скачать отчёт', until: Date.now() + 5000 };
      })
      .finally(function () {
        exportInFlight = false;
        tick();
      });
  }

  function reloadIncidents() {
    inc.items = [];
    inc.total = 0;
    loadIncidents();
  }

  function setSearchValue(value) {
    if (dom.incSearch) dom.incSearch.value = value;
    if (dom.incSearchClear) dom.incSearchClear.hidden = !value;
  }

  /* ------------------------------------------------------------------ *
   *  События
   * ------------------------------------------------------------------ */
  function bindEvents() {
    if (dom.btnCheck) dom.btnCheck.addEventListener('click', runCheck);
    if (dom.btnExport) dom.btnExport.addEventListener('click', exportReport);

    if (dom.autoToggle) {
      dom.autoToggle.addEventListener('change', function () {
        autoUpdate = !!dom.autoToggle.checked;
        try { localStorage.setItem('netmon:auto', autoUpdate ? '1' : '0'); } catch (err) { /* noop */ }
        updateLiveIndicator();
        if (autoUpdate) {
          loadState();
          schedulePoll(POLL_MS);
        } else {
          if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
        }
        tick();
      });
    }

    if (dom.incSearch) {
      dom.incSearch.addEventListener('input', function () {
        var value = dom.incSearch.value;
        if (dom.incSearchClear) dom.incSearchClear.hidden = !value;
        clearTimeout(incSearchTimer);
        incSearchTimer = setTimeout(function () {
          inc.q = dom.incSearch.value.trim();
          reloadIncidents();
        }, 300);
      });
      dom.incSearch.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && dom.incSearch.value) {
          e.preventDefault();
          setSearchValue('');
          inc.q = '';
          reloadIncidents();
        }
      });
    }

    if (dom.incSearchClear) {
      dom.incSearchClear.addEventListener('click', function () {
        setSearchValue('');
        inc.q = '';
        reloadIncidents();
        if (dom.incSearch) dom.incSearch.focus();
      });
    }

    if (dom.incChips) {
      dom.incChips.addEventListener('click', function (e) {
        var btn = e.target && e.target.closest ? e.target.closest('.chip') : null;
        if (!btn || !dom.incChips.contains(btn)) return;
        Array.prototype.forEach.call(dom.incChips.querySelectorAll('.chip'), function (chip) {
          chip.classList.toggle('is-active', chip === btn);
          chip.setAttribute('aria-pressed', chip === btn ? 'true' : 'false');
        });
        inc.service = btn.dataset.service || 'all';
        reloadIncidents();
      });
    }

    if (dom.incStatus) {
      dom.incStatus.addEventListener('change', function () {
        inc.status = dom.incStatus.value || 'all';
        reloadIncidents();
      });
    }

    if (dom.incHours) {
      dom.incHours.addEventListener('change', function () {
        inc.hours = Number(dom.incHours.value) || 0;
        reloadIncidents();
      });
    }

    if (dom.incMore) {
      dom.incMore.addEventListener('click', function () {
        if (inc.loading) return;
        loadIncidents({ append: true });
      });
    }

    document.addEventListener('visibilitychange', function () {
      if (document.hidden) {
        if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
        hideTip();
        return;
      }
      loadState();
      if (Date.now() - inc.lastLoadAt > 30000) loadIncidents();
    });

    window.addEventListener('scroll', hideTip, { capture: true, passive: true });
    window.addEventListener('resize', function () { scheduleRedraw(); hideTip(); });
    window.addEventListener('orientationchange', scheduleRedraw);
    window.addEventListener('blur', hideTip);
  }

  /* ------------------------------------------------------------------ *
   *  Инициализация
   * ------------------------------------------------------------------ */
  function setupPrefs() {
    var stored = null;
    try { stored = localStorage.getItem('netmon:auto'); } catch (err) { stored = null; }
    autoUpdate = stored !== '0';
    if (dom.autoToggle) dom.autoToggle.checked = autoUpdate;
  }

  function setupResizeObserver() {
    if (typeof ResizeObserver === 'undefined') return;
    resizeObserver = new ResizeObserver(function () { scheduleRedraw(); });
    cardMap.forEach(function (card) {
      if (card.spark) resizeObserver.observe(card.spark);
    });
  }

  function init() {
    cacheDom();
    setupPrefs();
    bindEvents();

    if (dom.incChips) {
      Array.prototype.forEach.call(dom.incChips.querySelectorAll('.chip'), function (chip) {
        var active = chip.classList.contains('is-active');
        chip.setAttribute('aria-pressed', active ? 'true' : 'false');
      });
    }

    setupResizeObserver();

    inc.loading = true;
    renderIncidents();

    loadState();
    loadIncidents();

    setInterval(tick, 1000);
    setInterval(function () {
      if (document.hidden || !autoUpdate || inc.loading) return;
      if (Date.now() - inc.lastLoadAt < 29000) return;
      loadIncidents();
    }, 10000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();

/* Дежурства (duty.html): форма врача (операции за смену) и доска заведующего
   (выгрузка разлиновки). Vanilla JS, без библиотек и CDN — как needs.js. */
(function () {
  'use strict';

  /* ── утилиты ────────────────────────────────────────────────────── */

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // JSON-запрос с единой обработкой ошибок: 401 → на логин,
  // остальные → Error с текстом из тела (поле error) и кодом статуса.
  function apiFetch(url, opts) {
    return fetch(url, opts).then(function (r) {
      if (r.status === 401) { location.href = '/login'; return null; }
      if (!r.ok) {
        return r.json().catch(function () { return {}; }).then(function (body) {
          var err = new Error(body.error || ('HTTP ' + r.status));
          err.status = r.status;
          throw err;
        });
      }
      return r.json();
    });
  }

  function fmtDate(iso) {
    if (!iso) return '';
    var p = iso.split('-');
    return p[2] + '.' + p[1] + '.' + p[0];
  }

  /* ── время ЧЧ:ММ с шагом 15 минут ──────────────────────────────── */

  // Разобрать ввод в минуты от полуночи; неверный формат → null.
  // Принимает «16:30», «16.30», «16 30», «1630», «830», «16», «8».
  function parseTimeMin(s) {
    var t = String(s == null ? '' : s).trim();
    if (!t) return null;
    var m = t.match(/^(\d{1,2})[:.\s](\d{1,2})$/);
    var h, mm;
    if (m) {
      h = parseInt(m[1], 10); mm = parseInt(m[2], 10);
    } else if (/^\d{1,4}$/.test(t)) {
      if (t.length <= 2) { h = parseInt(t, 10); mm = 0; }
      else { var n = parseInt(t, 10); h = Math.floor(n / 100); mm = n % 100; }
    } else {
      return null;
    }
    if (h > 23 || mm > 59) return null;
    return h * 60 + mm;
  }

  // Минуты от полуночи → 'ЧЧ:ММ'.
  function fmtTimeMin(total) {
    var h = Math.floor(total / 60) % 24;
    var mm = total % 60;
    var p = function (n) { return (n < 10 ? '0' : '') + n; };
    return p(h) + ':' + p(mm);
  }

  // Нормализовать поле времени: разобрать, округлить до 15 минут, записать ЧЧ:ММ.
  function snapTimeInput(input) {
    var parsed = parseTimeMin(input.value);
    input.value = parsed == null ? '' : fmtTimeMin(Math.round(parsed / 15) * 15);
  }

  // Сдвинуть время на deltaMin (скролл), оборачивая через полночь.
  function adjustTimeInput(input, deltaMin) {
    var parsed = parseTimeMin(input.value);
    var total = (parsed == null ? 0 : Math.round(parsed / 15) * 15) + deltaMin;
    total = ((total % 1440) + 1440) % 1440;
    input.value = fmtTimeMin(total);
  }

  var app = document.getElementById('duty-app');
  if (!app) return;
  var ROLE = app.getAttribute('data-role') || '';
  var BASES = JSON.parse(app.getAttribute('data-bases') || '[]');

  var bannerEl = document.getElementById('duty-banner');
  var bannerTimer = null;
  function showBanner(msg, kind) {
    if (!bannerEl) return;
    bannerEl.textContent = msg;
    bannerEl.className = 'duty-banner ' + (kind || 'ok');
    bannerEl.hidden = false;
    if (bannerTimer) clearTimeout(bannerTimer);
    bannerTimer = setTimeout(function () { bannerEl.hidden = true; }, 6000);
  }

  if (ROLE === 'doctor') initDoctor();
  else if (ROLE === 'head') initHead();

  /* ── врач: форма операций за смену ──────────────────────────────── */

  function initDoctor() {
    var baseSel = document.getElementById('duty-base');
    var windowStatus = document.getElementById('duty-window-status');
    var formEl = document.getElementById('duty-form');
    var shiftDateEl = document.getElementById('duty-shift-date');
    var opsTbody = document.getElementById('duty-ops');
    var addBtn = document.getElementById('duty-add');
    var saveBtn = document.getElementById('duty-save');
    var sendBtn = document.getElementById('duty-send');
    var statusEl = document.getElementById('duty-status');

    var report = null;   // текущий отчёт API (или null, если ещё нет)
    var readOnly = false;

    function operations() {
      return (report && report.operations) || [];
    }

    function renderOps() {
      var ops = operations();
      if (!ops.length) {
        opsTbody.innerHTML = '<tr><td colspan="4" class="empty">Добавьте операцию.</td></tr>';
        return;
      }
      opsTbody.innerHTML = ops.map(function (op, i) {
        var dis = readOnly ? ' disabled' : '';
        return '<tr>' +
          '<td><input type="text" class="duty-op-name" data-i="' + i + '" value="' + esc(op.operation) + '"' + dis + '></td>' +
          '<td><input type="text" inputmode="numeric" class="duty-op-start duty-time" data-i="' + i + '" value="' + esc(op.start_time) + '" placeholder="ЧЧ:ММ" autocomplete="off"' + dis + '></td>' +
          '<td><input type="text" inputmode="numeric" class="duty-op-end duty-time" data-i="' + i + '" value="' + esc(op.end_time) + '" placeholder="ЧЧ:ММ" autocomplete="off"' + dis + '></td>' +
          '<td><button type="button" class="duty-remove" data-i="' + i + '" aria-label="Удалить"' + dis + '>×</button></td>' +
          '</tr>';
      }).join('');
    }

    function syncControls() {
      addBtn.disabled = readOnly;
      saveBtn.disabled = readOnly;
      sendBtn.disabled = readOnly;
    }

    function setStatus(t) { if (statusEl) statusEl.textContent = t; }

    function statusText() {
      if (report && report.status === 'closed') return 'Отчёт закрыт — просмотр.';
      if (report && report.status === 'sent') return 'Отчёт отправлен — можно править до закрытия.';
      return 'Черновик — можно редактировать.';
    }

    function collectOps() {
      // нормализовать все поля времени перед чтением (снэп к 15 минутам)
      opsTbody.querySelectorAll('.duty-time').forEach(function (inp) { snapTimeInput(inp); });
      var ops = [];
      opsTbody.querySelectorAll('tr').forEach(function (tr) {
        var name = tr.querySelector('.duty-op-name');
        if (!name) return;
        ops.push({
          operation: name.value,
          start_time: (tr.querySelector('.duty-op-start') || {}).value || '',
          end_time: (tr.querySelector('.duty-op-end') || {}).value || '',
        });
      });
      return ops;
    }

    function loadReport() {
      var base = baseSel.value;
      apiFetch('/duty/api/report?base=' + encodeURIComponent(base)).then(function (data) {
        if (!data) return;
        if (!data.is_open) {
          windowStatus.textContent = 'Смена закрыта. Ввод доступен с 16:00 до 09:30.';
          formEl.hidden = true;
          return;
        }
        formEl.hidden = false;
        windowStatus.textContent = '';
        shiftDateEl.textContent = fmtDate(data.shift_date);
        report = data.report;
        readOnly = !!(report && report.status === 'closed');
        renderOps();
        syncControls();
        setStatus(statusText());
      }).catch(function (err) {
        showBanner('Ошибка загрузки: ' + err.message, 'error');
      });
    }

    function addOp() {
      if (readOnly) return;
      report = report || { operations: [], status: 'draft' };
      report.operations = (report.operations || []).concat([{ operation: '', start_time: '', end_time: '' }]);
      renderOps();
    }

    function saveDraft() {
      if (readOnly) return;
      var base = baseSel.value;
      saveBtn.disabled = true;
      apiFetch('/duty/api/report', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ base: base, operations: collectOps() }),
      }).then(function (data) {
        if (!data) return;
        report = data.report;
        readOnly = !!(report && report.status === 'closed');
        renderOps();
        setStatus('Черновик — сохранено.');
        showBanner('Черновик сохранён.', 'ok');
      }).catch(showApiError).finally(function () {
        saveBtn.disabled = readOnly;
      });
    }

    function sendReport() {
      if (readOnly) return;
      var base = baseSel.value;
      sendBtn.disabled = true;
      // сначала сохранить черновик, затем отправить
      apiFetch('/duty/api/report', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ base: base, operations: collectOps() }),
      }).then(function (data) {
        if (!data) return null;
        return apiFetch('/duty/api/send', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ base: base }),
        });
      }).then(function (data) {
        if (!data) return;
        report = data.report;
        readOnly = !!(report && report.status === 'closed');
        renderOps();
        syncControls();
        setStatus(statusText());
        showBanner('Отчёт отправлен.', 'ok');
      }).catch(showApiError).finally(function () {
        sendBtn.disabled = readOnly;
      });
    }

    function showApiError(err) {
      showBanner('Ошибка: ' + err.message, 'error');
    }

    baseSel.addEventListener('change', loadReport);
    addBtn.addEventListener('click', addOp);
    saveBtn.addEventListener('click', saveDraft);
    sendBtn.addEventListener('click', sendReport);
    opsTbody.addEventListener('click', function (e) {
      var btn = e.target.closest('.duty-remove');
      if (!btn || readOnly) return;
      var i = parseInt(btn.getAttribute('data-i'), 10);
      report.operations = operations().filter(function (_, idx) { return idx !== i; });
      renderOps();
    });

    // Ввод времени: нормализация по уходу фокуса и по Enter,
    // шаг 15 минут скроллом колеса (десктоп).
    opsTbody.addEventListener('focusout', function (e) {
      if (!e.target.classList || !e.target.classList.contains('duty-time')) return;
      snapTimeInput(e.target);
    });
    opsTbody.addEventListener('wheel', function (e) {
      if (!e.target.classList || !e.target.classList.contains('duty-time')) return;
      e.preventDefault();
      adjustTimeInput(e.target, e.deltaY < 0 ? 15 : -15);
    }, { passive: false });
    opsTbody.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter' || !e.target.classList || !e.target.classList.contains('duty-time')) return;
      e.preventDefault();
      snapTimeInput(e.target);
    });

    loadReport();
  }

  /* ── заведующий: доска и выгрузка ───────────────────────────────── */

  function initHead() {
    var fromEl = document.getElementById('duty-from');
    var toEl = document.getElementById('duty-to');
    var exportEl = document.getElementById('duty-export');
    var boardEl = document.getElementById('duty-board');

    var STATUS_LABELS = { draft: 'черновик', sent: 'отправлен', closed: 'закрыт' };

    function today() {
      var d = new Date();
      var p = function (n) { return (n < 10 ? '0' : '') + n; };
      return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
    }
    fromEl.value = today();
    toEl.value = today();

    function post(url, body) {
      return apiFetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
    }

    function showBoardError(err) {
      boardEl.innerHTML = '<p class="empty">Ошибка: ' + esc(err.message) + '</p>';
    }

    function loadBoard() {
      var from = fromEl.value, to = toEl.value;
      if (!from || !to) return;
      exportEl.href = '/duty/report.xlsx?from=' + encodeURIComponent(from) + '&to=' + encodeURIComponent(to);
      boardEl.innerHTML = '<p class="empty">Загрузка…</p>';
      apiFetch('/duty/api/board?from=' + encodeURIComponent(from) + '&to=' + encodeURIComponent(to)).then(function (data) {
        if (!data) return;
        var reports = data.reports || [];
        if (!reports.length) {
          boardEl.innerHTML = '<p class="empty">Отчётов за период нет.</p>';
          return;
        }
        var byDate = {};
        reports.forEach(function (r) {
          (byDate[r.shift_date] = byDate[r.shift_date] || []).push(r);
        });
        var html = '';
        Object.keys(byDate).sort().forEach(function (date) {
          html += '<div class="duty-date-head"><h3>' + fmtDate(date) + '</h3>' +
            '<button type="button" class="btn duty-close-shift" data-date="' + date + '">Закрыть смену</button></div>';
          html += '<div class="table-wrap"><table class="duty-table">' +
            '<thead><tr><th>База</th><th>Врач</th><th>Операций</th><th>Статус</th><th></th></tr></thead><tbody>';
          byDate[date].forEach(function (r) {
            var action = r.status === 'closed'
              ? '<button type="button" class="btn duty-reopen" data-id="' + r.id + '">Открыть</button>'
              : '<button type="button" class="btn duty-close" data-id="' + r.id + '">Закрыть</button>';
            html += '<tr><td>' + esc(r.base) + '</td><td>' + esc(r.doctor_name) + '</td>' +
              '<td>' + (r.operations || []).length + '</td>' +
              '<td>' + (STATUS_LABELS[r.status] || esc(r.status)) + '</td>' +
              '<td>' + action + '</td></tr>';
          });
          html += '</tbody></table></div>';
        });
        boardEl.innerHTML = html;
      }).catch(showBoardError);
    }

    boardEl.addEventListener('click', function (e) {
      var close = e.target.closest('.duty-close');
      if (close) {
        post('/duty/api/close', { report_id: parseInt(close.getAttribute('data-id'), 10) })
          .then(loadBoard).catch(showBoardError);
        return;
      }
      var reopen = e.target.closest('.duty-reopen');
      if (reopen) {
        post('/duty/api/reopen', { report_id: parseInt(reopen.getAttribute('data-id'), 10) })
          .then(loadBoard).catch(showBoardError);
        return;
      }
      var closeShift = e.target.closest('.duty-close-shift');
      if (closeShift) {
        post('/duty/api/close-shift', { shift_date: closeShift.getAttribute('data-date') })
          .then(loadBoard).catch(showBoardError);
        return;
      }
    });

    fromEl.addEventListener('change', loadBoard);
    toEl.addEventListener('change', loadBoard);
    loadBoard();
  }
})();

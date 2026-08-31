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
        return '<tr>' +
          '<td><input type="text" class="duty-op-name" data-i="' + i + '" value="' + esc(op.operation) + '"' + (readOnly ? ' disabled' : '') + '></td>' +
          '<td><input type="time" step="900" class="duty-op-start" data-i="' + i + '" value="' + esc(op.start_time) + '"' + (readOnly ? ' disabled' : '') + '></td>' +
          '<td><input type="time" step="900" class="duty-op-end" data-i="' + i + '" value="' + esc(op.end_time) + '"' + (readOnly ? ' disabled' : '') + '></td>' +
          '<td><button type="button" class="duty-remove" data-i="' + i + '" aria-label="Удалить"' + (readOnly ? ' disabled' : '') + '>×</button></td>' +
          '</tr>';
      }).join('');
    }

    function syncControls() {
      addBtn.disabled = readOnly;
      saveBtn.disabled = readOnly;
      sendBtn.disabled = readOnly;
    }

    function setStatus(t) { if (statusEl) statusEl.textContent = t; }

    function collectOps() {
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
        readOnly = !!(report && report.status === 'sent');
        renderOps();
        syncControls();
        setStatus(readOnly ? 'Отчёт отправлен — просмотр.' : 'Черновик — можно редактировать.');
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
        readOnly = true;
        renderOps();
        syncControls();
        setStatus('Отчёт отправлен.');
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

    loadReport();
  }

  /* ── заведующий: доска и выгрузка ───────────────────────────────── */

  function initHead() {
    var fromEl = document.getElementById('duty-from');
    var toEl = document.getElementById('duty-to');
    var exportEl = document.getElementById('duty-export');
    var boardEl = document.getElementById('duty-board');

    function today() {
      var d = new Date();
      var p = function (n) { return (n < 10 ? '0' : '') + n; };
      return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
    }
    fromEl.value = today();
    toEl.value = today();

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
          html += '<h3>' + fmtDate(date) + '</h3>';
          html += '<div class="table-wrap"><table class="duty-table">' +
            '<thead><tr><th>База</th><th>Врач</th><th>Операций</th><th>Статус</th></tr></thead><tbody>';
          byDate[date].forEach(function (r) {
            html += '<tr><td>' + esc(r.base) + '</td><td>' + esc(r.doctor_name) + '</td>' +
              '<td>' + (r.operations || []).length + '</td>' +
              '<td>' + (r.status === 'sent' ? 'отправлен' : 'черновик') + '</td></tr>';
          });
          html += '</tbody></table></div>';
        });
        boardEl.innerHTML = html;
      }).catch(function (err) {
        boardEl.innerHTML = '<p class="empty">Ошибка: ' + esc(err.message) + '</p>';
      });
    }

    fromEl.addEventListener('change', loadBoard);
    toEl.addEventListener('change', loadBoard);
    loadBoard();
  }
})();

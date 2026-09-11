/* Дежурства (duty.html): форма врача (операции за смену) и доска заведующего
   (выгрузка разлиновки). Vanilla JS, без библиотек и CDN — как needs.js.

   Общее (экранирование, запрос к API, баннер, поле времени) — из
   /static/lib/core.js: объект dc. Здесь остаётся только своё:
   сетка смены, расчёт окна и выгрузка. */
(function () {
  'use strict';

  var esc = dc.esc;
  var apiFetch = dc.apiFetch;

  var app = document.getElementById('duty-app');
  if (!app) return;
  var ROLE = app.getAttribute('data-role') || '';
  var BASES = JSON.parse(app.getAttribute('data-bases') || '[]');

  var bannerEl = document.getElementById('duty-banner');
  function showBanner(msg, kind) { dc.banner(bannerEl, msg, kind); }

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
      // нормализовать все поля времени перед чтением (снэп к шагу поля)
      dc.snapTimeFields(opsTbody, '.duty-time');
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

    // Поля времени: округление по уходу фокуса и по Enter, шаг 10 минут
    // скроллом колеса (десктоп) — общее поведение из dc.timeFields.
    dc.timeFields(opsTbody, { selector: '.duty-time' });

    loadReport();
  }

  /* ── заведующий: доска и выгрузка ───────────────────────────────── */

  function initHead() {
    var fromEl = document.getElementById('duty-from');
    var toEl = document.getElementById('duty-to');
    var exportEl = document.getElementById('duty-export');
    var boardEl = document.getElementById('duty-board');

    var STATUS_LABELS = { draft: 'черновик', sent: 'отправлен', closed: 'закрыт' };

    function isoDate(offsetDays) {
      var d = new Date();
      d.setDate(d.getDate() + (offsetDays || 0));
      var p = function (n) { return (n < 10 ? '0' : '') + n; };
      return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
    }
    fromEl.value = isoDate(-1);
    toEl.value = isoDate(0);

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

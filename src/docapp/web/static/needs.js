/* Потребности (needs.html, analytics.html): форма сестры, доска старшей,
   отчёты, аналитика. Vanilla JS, без библиотек и CDN (F10).
   Страницы используют один скрипт: needs.html — форма+доска, analytics.html —
   фильтры и таблица. Роли: форма — nurse/head_nurse/head, доска/отчёты/
   аналитика — head_nurse/head (ограничение на уровне шаблона и API). */
(function () {
  'use strict';

  /* ── утилиты ────────────────────────────────────────────────────── */

  // Экранирование текста для вставки в HTML (как в consult.js).
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // 'YYYY-MM-DD' для даты d.
  function iso(d) {
    var p = function (n) { return (n < 10 ? '0' : '') + n; };
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
  }

  // Понедельник недели даты d (недели пн–вс, как в service.monday_of_week).
  function mondayOfWeek(d) {
    var shift = (d.getDay() + 6) % 7; // вс=6 → отступить 6, пн=0 → 0
    return new Date(d.getFullYear(), d.getMonth(), d.getDate() - shift);
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

  // Разбор числового количества из строки: пусто/мусор → 0, минус → 0.
  function parseQty(s) {
    var n = parseInt(s, 10);
    return isNaN(n) || n < 0 ? 0 : n;
  }

  /* ── страница /needs ────────────────────────────────────────────── */

  var app = document.getElementById('needs-app');
  if (!app) { initAnalyticsIfNeeded(); return; }

  var ROLE = app.getAttribute('data-role') || '';
  var MY_ID = parseInt(app.getAttribute('data-user-id') || '0', 10);
  var IS_FULL = ROLE === 'head_nurse' || ROLE === 'head';

  var bannerEl = document.getElementById('needs-banner');
  var pointsEl = document.getElementById('needs-points');
  var currentPointEl = document.getElementById('needs-current-point');
  var searchInput = document.getElementById('needs-search');
  var searchResults = document.getElementById('needs-search-results');
  var linesTbody = document.getElementById('needs-lines');
  var saveBtn = document.getElementById('needs-save');
  var submitBtn = document.getElementById('needs-submit');
  var statusEl = document.getElementById('needs-request-status');
  var boardEl = document.getElementById('needs-board');
  var reportLinksEl = document.getElementById('needs-report-links');

  // Состояние страницы.
  var catalog = { bases: {}, groups: {}, allItems: [] }; // каталог из API
  var lines = [];          // выбранные позиции: {item, unit, group, qty}
  var currentBase = '';    // база выбранной точки
  var currentPoint = '';   // выбранная точка пополнения
  var week = iso(mondayOfWeek(new Date())); // текущая неделя (пн)
  var readOnly = false;    // режим просмотра (чужой отправленный / закрытая база)
  var closedBases = {};    // базы с закрытой неделей (для полных ролей — из доски)

  // Баннер: сообщение + вид (ok/warn/error); прячем по таймеру.
  var bannerTimer = null;
  function showBanner(msg, kind) {
    if (!bannerEl) return;
    bannerEl.textContent = msg;
    bannerEl.className = 'needs-banner ' + (kind || 'ok');
    bannerEl.hidden = false;
    if (bannerTimer) clearTimeout(bannerTimer);
    bannerTimer = setTimeout(function () { bannerEl.hidden = true; }, 6000);
  }

  /* ── каталог и выбор точки ───────────────────────────────────────── */

  function loadCatalog() {
    return apiFetch('/needs/api/catalog').then(function (data) {
      if (!data) return;
      catalog.bases = data.bases || {};
      catalog.groups = data.groups || {};
      catalog.allItems = [];
      Object.keys(catalog.groups).forEach(function (group) {
        Object.keys(catalog.groups[group]).forEach(function (name) {
          catalog.allItems.push({
            name: name,
            unit: catalog.groups[group][name],
            group: group,
          });
        });
      });
      renderPointGrid();
      if (reportLinksEl) renderReportLinks();
    }).catch(function (err) {
      showBanner('Не удалось загрузить каталог: ' + err.message, 'error');
    });
  }

  // Сетка точек для медсестры: карточки по базам (без статусов чужих заявок —
  // API доски медсестре недоступен; сестра видит только названия точек).
  function renderPointGrid() {
    if (!pointsEl) return;
    var baseNames = Object.keys(catalog.bases);
    if (!baseNames.length) {
      pointsEl.innerHTML = '<p class="empty">Каталог пуст.</p>';
      return;
    }
    var html = '';
    baseNames.forEach(function (base) {
      var points = catalog.bases[base] || [];
      if (!points.length) return;
      html += '<div class="needs-base">';
      html += '<h3>' + esc(base) + '</h3>';
      html += '<div class="needs-board-grid">';
      points.forEach(function (point) {
        var active = point === currentPoint ? ' needs-point-active' : '';
        html += '<button type="button" class="needs-point-cell' + active + '" ' +
          'data-base="' + esc(base) + '" data-point="' + esc(point) + '">' +
          '<span class="needs-cell-point">' + esc(point) + '</span>' +
          '</button>';
      });
      html += '</div></div>';
    });
    pointsEl.innerHTML = html;
  }

  // Выбрать точку: обновить состояние, заголовок заявки и подсветку сетки.
  function selectPoint(base, point) {
    currentBase = base;
    currentPoint = point;
    if (currentPointEl) currentPointEl.textContent = point ? ('— ' + point) : '';
    if (pointsEl) renderPointGrid();
    searchInput.value = '';
    searchResults.hidden = true;
    searchResults.innerHTML = '';
    readOnly = !!closedBases[base];
    if (reportLinksEl) renderReportLinks();
    return loadRequest();
  }

  /* ── заявка: загрузка, отрисовка, сохранение, отправка ──────────── */

  // Загрузить заявку точки за неделю: есть → показать (чужая отправленная —
  // режим просмотра), 404 (нет заявки или чужой черновик) → пустая форма.
  function loadRequest() {
    if (!currentPoint) {
      lines = [];
      renderLines();
      setStatus('Выберите точку пополнения.');
      return;
    }
    var url = '/needs/api/request?base=' + encodeURIComponent(currentBase) +
      '&point=' + encodeURIComponent(currentPoint) + '&week=' + encodeURIComponent(week);
    return apiFetch(url).then(function (req) {
      if (!req) return;
      lines = (req.lines || []).map(function (l) {
        return { item: l.item, unit: l.unit || '', group: l.grp || '', qty: l.qty || 0 };
      });
      // Чужой отправленный — просмотр; свои и чужие черновики (полная роль)
      // и свои отправленные — правка. Закрытая база — просмотр.
      readOnly = (req.status === 'sent' && req.author_id !== MY_ID && !IS_FULL) ||
        !!closedBases[currentBase];
      renderLines();
      setStatus(
        'Заявка: ' + (req.status === 'sent' ? 'отправлена' : 'черновик') +
        (readOnly ? ' — просмотр (изменения заблокированы)' : '')
      );
      if (readOnly && closedBases[currentBase]) {
        showBanner('Неделя закрыта для базы «' + currentBase + '» — правки запрещены.', 'warn');
      }
    }).catch(function (err) {
      if (err.status === 404) {
        // Заявки нет (или это чужой черновик, скрытый API) — пустая форма.
        lines = [];
        readOnly = !!closedBases[currentBase];
        renderLines();
        setStatus('Заявки на эту неделю ещё нет.');
        return;
      }
      showBanner('Ошибка загрузки заявки: ' + err.message, 'error');
    });
  }

  // Отрисовать таблицу выбранных позиций: Название | Ед. | Количество | ×.
  function renderLines() {
    if (!lines.length) {
      linesTbody.innerHTML = '<tr><td colspan="4" class="empty">' +
        'Позиции не выбраны — найдите препарат поиском выше.</td></tr>';
      syncControls();
      return;
    }
    var html = lines.map(function (line, i) {
      return '<tr>' +
        '<td>' + esc(line.item) + '</td>' +
        '<td>' + esc(line.unit) + '</td>' +
        '<td><input type="number" inputmode="numeric" min="0" step="1" ' +
          'class="needs-qty" data-i="' + i + '" value="' +
          (line.qty > 0 ? line.qty : '0') + '"' +
          (readOnly ? ' disabled' : '') + '></td>' +
        '<td><button type="button" class="needs-remove" data-i="' + i + '" ' +
          'aria-label="Удалить"' + (readOnly ? ' disabled' : '') + '>×</button></td>' +
        '</tr>';
    }).join('');
    linesTbody.innerHTML = html;
    syncControls();
  }

  // Включить/выключить управление формой по режиму (правка/просмотр).
  function syncControls() {
    [searchInput, saveBtn, submitBtn].forEach(function (el) {
      if (el) el.disabled = readOnly;
    });
  }

  function setStatus(text) {
    if (statusEl) statusEl.textContent = text;
  }

  // Собрать строки для API: [{item, qty}] с числами (пустое поле → 0).
  function collectLines() {
    var out = [];
    lines.forEach(function (line, i) {
      var input = linesTbody.querySelector('.needs-qty[data-i="' + i + '"]');
      out.push({ item: line.item, qty: input ? parseQty(input.value) : 0 });
    });
    return out;
  }

  // Общие параметры заявки для POST /needs/api/request.
  function requestPayload() {
    return {
      base: currentBase,
      point: currentPoint,
      week: week,
      lines: collectLines(),
    };
  }

  // «Сохранить»: POST /needs/api/request со status='draft'.
  function saveDraft() {
    if (readOnly || !currentPoint) return;
    var payload = requestPayload();
    payload.status = 'draft';
    saveBtn.disabled = true;
    apiFetch('/needs/api/request', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }).then(function (data) {
      if (!data) return;
      showBanner('Черновик сохранён.', 'ok');
      setStatus('Заявка: черновик — сохранено');
      if (IS_FULL && boardEl) loadBoard(); // статус доски мог измениться
    }).catch(showApiError).finally(function () {
      saveBtn.disabled = readOnly;
    });
  }

  // «Отправить»: сначала сохранить draft, затем POST /needs/api/request/submit;
  // предупреждения о строках с нулевым количеством — из ответа.
  function submitRequest() {
    if (readOnly || !currentPoint) return;
    var payload = requestPayload();
    payload.status = 'draft';
    submitBtn.disabled = true;
    apiFetch('/needs/api/request', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }).then(function (data) {
      if (!data) return;
      return apiFetch('/needs/api/request/submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ base: currentBase, point: currentPoint, week: week }),
      });
    }).then(function (res) {
      if (!res) return;
      var warnings = (res.warnings || []).filter(Boolean);
      var msg = 'Заявка отправлена.';
      if (warnings.length) {
        msg = 'Заявка отправлена. Предупреждение: ' + warnings.length +
          ' позиций с нулевым количеством: ' + warnings.join(', ') + '.';
      }
      showBanner(msg, warnings.length ? 'warn' : 'ok');
      return loadRequest(); // обновить статус (sent) и режим
    }).then(function () {
      if (IS_FULL && boardEl) loadBoard();
    }).catch(showApiError).finally(function () {
      submitBtn.disabled = readOnly;
    });
  }

  // Показать ошибку API понятным сообщением: 409 — «неделя закрыта»,
  // 403/400 — текст из ответа; закрытую базу запоминаем (просмотр).
  function showApiError(err) {
    if (err.status === 409) {
      closedBases[currentBase] = true;
      readOnly = true;
      renderLines();
      showBanner('Неделя закрыта для базы «' + currentBase + '»: ' + err.message, 'error');
    } else {
      showBanner('Ошибка: ' + err.message, 'error');
    }
  }

  /* ── поиск препаратов по каталогу ───────────────────────────────── */

  function searchMatches(q) {
    var needle = q.trim().toLowerCase();
    if (!needle) return [];
    return catalog.allItems.filter(function (item) {
      return item.name.toLowerCase().indexOf(needle) !== -1;
    });
  }

  function renderSearchResults(q) {
    var matches = searchMatches(q);
    if (!matches.length) {
      searchResults.hidden = true;
      searchResults.innerHTML = '';
      return;
    }
    searchResults.innerHTML = matches.slice(0, 20).map(function (item) {
      return '<button type="button" class="needs-search-item" ' +
        'data-item="' + esc(item.name) + '">' +
        '<span class="needs-search-name">' + esc(item.name) + '</span>' +
        '<span class="needs-search-meta">' + esc(item.unit) + ' · ' + esc(item.group) + '</span>' +
        '</button>';
    }).join('');
    searchResults.hidden = false;
  }

  // Добавить позицию в список выбранного; дубль — фокус на количество.
  function addLine(itemName) {
    var idx = lines.findIndex(function (l) { return l.item === itemName; });
    if (idx === -1) {
      var found = catalog.allItems.find(function (i) { return i.name === itemName; });
      lines.push({
        item: itemName,
        unit: found ? found.unit : '',
        group: found ? found.group : '',
        qty: 0,
      });
      renderLines();
      idx = lines.length - 1;
    }
    var input = linesTbody.querySelector('.needs-qty[data-i="' + idx + '"]');
    if (input) { input.focus(); input.select(); }
  }

  /* ── доска старшей ──────────────────────────────────────────────── */

  // Доска: точки обеих баз со статусом (серый — нет заявки, жёлтый —
  // черновик, зелёный — отправлено), автор, кнопки закрытия/переоткрытия.
  function loadBoard() {
    if (!boardEl) return;
    boardEl.innerHTML = '<p class="empty">Загрузка…</p>';
    apiFetch('/needs/api/board?week=' + encodeURIComponent(week)).then(function (data) {
      if (!data) return;
      var cells = data.board || [];
      closedBases = {};
      var byBase = {};
      cells.forEach(function (cell) {
        (byBase[cell.base] = byBase[cell.base] || []).push(cell);
        if (cell.is_closed) closedBases[cell.base] = true;
      });
      renderBoard(byBase);
      if (currentPoint && readOnly === false && closedBases[currentBase]) {
        // база закрыта уже при открытии формы — перевести в просмотр
        readOnly = true;
        renderLines();
        showBanner('Неделя закрыта для базы «' + currentBase + '» — правки запрещены.', 'warn');
      }
    }).catch(function (err) {
      if (boardEl) boardEl.innerHTML = '<p class="empty">Доска недоступна: ' + esc(err.message) + '</p>';
    });
  }

  var STATUS_LABEL = { none: 'нет заявки', draft: 'черновик', sent: 'отправлено' };

  // Винительный падеж для кнопки «Закрыть …» (ТЗ F6): Ленская → Ленскую.
  var BASE_ACCUSATIVE = { 'Ленская': 'Ленскую', 'Таймырская': 'Таймырскую' };
  function baseAccusative(base) { return BASE_ACCUSATIVE[base] || base; }

  function renderBoard(byBase) {
    var html = '';
    Object.keys(catalog.bases).forEach(function (base) {
      var cells = byBase[base] || [];
      var closed = !!closedBases[base];
      html += '<div class="needs-base">';
      html += '<h3>' + esc(base) +
        (closed ? ' <span class="needs-closed-badge">неделя закрыта</span>' : '') +
        '</h3>';
      if (!cells.length) {
        html += '<p class="empty">Точек в каталоге нет.</p>';
      } else {
        html += '<div class="needs-board-grid">';
        cells.forEach(function (cell) {
          html += '<button type="button" class="needs-board-cell" ' +
            'data-base="' + esc(cell.base) + '" data-point="' + esc(cell.point) + '">' +
            '<span class="needs-dot ' + esc(cell.status) + '"></span>' +
            '<span class="needs-cell-main">' +
            '<span class="needs-cell-point">' + esc(cell.point) + '</span>' +
            '<span class="needs-cell-author">' + esc(cell.author_name || STATUS_LABEL[cell.status]) + '</span>' +
            '</span>' +
            '</button>';
        });
        html += '</div>';
      }
      html += '<div class="needs-actions">';
      if (closed) {
        html += '<button type="button" class="btn needs-reopen" data-base="' + esc(base) + '">' +
          'Открыть заново</button>';
      } else {
        html += '<button type="button" class="btn needs-close" data-base="' + esc(base) + '">' +
          'Закрыть ' + baseAccusative(base) + '</button>';
      }
      html += '</div>';
      html += '</div>';
    });
    boardEl.innerHTML = html;
  }

  // Клик по точке доски — открыть её в форме заявки.
  boardEl.addEventListener('click', function (e) {
    var cell = e.target.closest('.needs-board-cell');
    if (cell) {
      selectPoint(cell.getAttribute('data-base'), cell.getAttribute('data-point'));
      return;
    }
    var closeBtn = e.target.closest('.needs-close');
    if (closeBtn) { closeBase(closeBtn.getAttribute('data-base')); return; }
    var reopenBtn = e.target.closest('.needs-reopen');
    if (reopenBtn) { reopenBase(reopenBtn.getAttribute('data-base')); return; }
  });

  // Клик по карточке точки в сетке медсестры — выбрать её.
  if (pointsEl) {
    pointsEl.addEventListener('click', function (e) {
      var cell = e.target.closest('.needs-point-cell');
      if (!cell) return;
      selectPoint(cell.getAttribute('data-base'), cell.getAttribute('data-point'));
    });
  }

  // Закрыть неделю для базы: подтверждение, POST /needs/api/close,
  // затем предупреждение со списком неотправивших точек из ответа.
  function closeBase(base) {
    if (!window.confirm('Закрыть неделю для базы «' + base + '»? Точки без ' +
      'отправленной заявки в отчёт не попадут.')) return;
    apiFetch('/needs/api/close', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ base: base, week: week }),
    }).then(function (data) {
      if (!data) return;
      var unsent = data.unsent_points || [];
      var msg = 'Неделя для базы «' + base + '» закрыта.';
      if (unsent.length) {
        msg += ' Не отправили заявку: ' + unsent.join(', ') + '.';
      }
      showBanner(msg, unsent.length ? 'warn' : 'ok');
      closedBases[base] = true;
      if (base === currentBase) {
        readOnly = true;
        renderLines();
        setStatus('Заявка: просмотр (неделя закрыта)');
      }
      loadBoard();
    }).catch(function (err) {
      showBanner('Ошибка закрытия недели: ' + err.message, 'error');
    });
  }

  // Открыть неделю заново: POST /needs/api/reopen.
  function reopenBase(base) {
    if (!window.confirm('Открыть неделю для базы «' + base + '» заново? ' +
      'Правки и отправка снова станут доступны.')) return;
    apiFetch('/needs/api/reopen', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ base: base, week: week }),
    }).then(function (data) {
      if (!data) return;
      showBanner('Неделя для базы «' + base + '» открыта заново.', 'ok');
      delete closedBases[base];
      if (base === currentBase) {
        readOnly = false;
        return loadRequest(); // вернуть режим правки
      }
      loadBoard();
    }).catch(function (err) {
      showBanner('Ошибка переоткрытия недели: ' + err.message, 'error');
    });
  }

  /* ── отчёт и аналитика (полные роли) ────────────────────────────── */

  // Ссылки на отчёт (.xlsx и просмотр) и аналитику — с текущей базой
  // и неделей; база — выбранной точки, иначе первая из каталога.
  function renderReportLinks() {
    if (!reportLinksEl) return;
    var base = currentBase ||
      Object.keys(catalog.bases)[0] ||
      '';
    var q = 'base=' + encodeURIComponent(base) + '&week=' + encodeURIComponent(week);
    reportLinksEl.innerHTML =
      '<a class="btn" href="/needs/report.xlsx?' + q + '">Отчёт (xlsx)</a>' +
      '<a class="btn" href="/needs/report?' + q + '">Отчёт (просмотр)</a>' +
      '<a class="btn" href="/needs/analytics">Аналитика</a>';
  }

  /* ── события формы ──────────────────────────────────────────────── */

  searchInput.addEventListener('input', function () {
    if (readOnly) return;
    renderSearchResults(searchInput.value);
  });

  // Клик по найденной позиции — добавить в список, поле очистить.
  searchResults.addEventListener('click', function (e) {
    var item = e.target.closest('.needs-search-item');
    if (!item) return;
    addLine(item.getAttribute('data-item'));
    searchInput.value = '';
    searchResults.hidden = true;
    searchResults.innerHTML = '';
    searchInput.focus();
  });

  // Клик вне блока поиска — спрятать подсказки.
  document.addEventListener('click', function (e) {
    if (!searchResults.hidden && !e.target.closest('#needs-search') &&
        !e.target.closest('#needs-search-results')) {
      searchResults.hidden = true;
    }
  });

  // Удаление позиции из списка выбранного.
  linesTbody.addEventListener('click', function (e) {
    var btn = e.target.closest('.needs-remove');
    if (!btn || readOnly) return;
    var i = parseInt(btn.getAttribute('data-i'), 10);
    if (isNaN(i) || i < 0 || i >= lines.length) return;
    lines.splice(i, 1);
    renderLines();
  });

  // Ввод количества — синхронизировать в состояние строк, чтобы значения
  // не терялись при перерисовке (добавление строки, смена точки и т.п.).
  linesTbody.addEventListener('input', function (e) {
    if (!e.target.classList || !e.target.classList.contains('needs-qty')) return;
    var i = parseInt(e.target.getAttribute('data-i'), 10);
    if (isNaN(i) || i < 0 || i >= lines.length) return;
    lines[i].qty = parseQty(e.target.value);
  });

  saveBtn.addEventListener('click', saveDraft);
  submitBtn.addEventListener('click', submitRequest);

  /* ── запуск страницы /needs ─────────────────────────────────────── */

  loadCatalog().then(function () {
    if (IS_FULL && boardEl) loadBoard();
  });

  /* ── страница /needs/analytics ──────────────────────────────────── */

  function initAnalyticsIfNeeded() {
    var analyticsApp = document.getElementById('analytics-app');
    if (!analyticsApp) return;
    initAnalytics(analyticsApp);
  }

  function initAnalytics(root) {
    var fromInput = document.getElementById('analytics-from');
    var toInput = document.getElementById('analytics-to');
    var baseSel = document.getElementById('analytics-base');
    var pointSelA = document.getElementById('analytics-point');
    var groupSel = document.getElementById('analytics-group');
    var showBtn = document.getElementById('analytics-show');
    var formA = document.getElementById('analytics-form');
    var xlsxLink = document.getElementById('analytics-xlsx');
    var resultEl = document.getElementById('analytics-result');
    var bannerElA = document.getElementById('analytics-banner');

    function showBannerA(msg, kind) {
      if (!bannerElA) return;
      bannerElA.textContent = msg;
      bannerElA.className = 'needs-banner ' + (kind || 'ok');
      bannerElA.hidden = false;
    }

    // Недели по умолчанию — текущая (понедельник).
    var today = new Date();
    var curWeek = iso(mondayOfWeek(today));
    fromInput.value = curWeek;
    toInput.value = curWeek;

    var acatalog = { bases: {}, groups: {}, allItems: [] };
    var aPointBase = {};

    // Каталог для фильтров: базы, точки, группы (роль head_nurse/head имеет доступ).
    apiFetch('/needs/api/catalog').then(function (data) {
      if (!data) return;
      acatalog.bases = data.bases || {};
      acatalog.groups = data.groups || {};
      acatalog.allItems = [];
      Object.keys(acatalog.groups).forEach(function (group) {
        Object.keys(acatalog.groups[group]).forEach(function (name) {
          acatalog.allItems.push({
            name: name,
            unit: acatalog.groups[group][name],
            group: group,
          });
        });
      });
      fillAnalyticsSelects();
    }).catch(function (err) {
      showBannerA('Не удалось загрузить каталог: ' + err.message, 'error');
    });

    function fillAnalyticsSelects() {
      var baseNames = Object.keys(acatalog.bases);
      baseSel.innerHTML = '<option value="">Все базы</option>' +
        baseNames.map(function (b) { return '<option value="' + esc(b) + '">' + esc(b) + '</option>'; }).join('');
      groupSel.innerHTML = '<option value="">Все группы</option>' +
        Object.keys(acatalog.groups).map(function (g) {
          return '<option value="' + esc(g) + '">' + esc(g) + '</option>';
        }).join('');
      // Точки — по выбранной базе (без базы — все точки обеих баз).
      baseSel.addEventListener('change', fillPointSelectA);
      fillPointSelectA();
    }

    function fillPointSelectA() {
      var base = baseSel.value;
      var options = [];
      if (base) {
        (acatalog.bases[base] || []).forEach(function (p) { aPointBase[p] = base; });
        options = acatalog.bases[base] || [];
      } else {
        Object.keys(acatalog.bases).forEach(function (b) {
          (acatalog.bases[b] || []).forEach(function (p) { aPointBase[p] = b; });
        });
        options = Object.keys(aPointBase);
      }
      pointSelA.innerHTML = '<option value="">Все точки</option>' +
        options.map(function (p) { return '<option value="' + esc(p) + '">' + esc(p) + '</option>'; }).join('');
    }

    // Параметры фильтров для API (from/to обязательны, остальные — опциональны).
    function analyticsParams() {
      var params = [
        'from=' + encodeURIComponent(fromInput.value),
        'to=' + encodeURIComponent(toInput.value),
      ];
      if (baseSel.value) params.push('base=' + encodeURIComponent(baseSel.value));
      if (pointSelA.value) params.push('point=' + encodeURIComponent(pointSelA.value));
      if (groupSel.value) params.push('group=' + encodeURIComponent(groupSel.value));
      return params.join('&');
    }

    // «Показать»: GET /needs/api/analytics → таблица на экране.
    // Запускается и по кнопке, и по отправке формы (Enter в полях дат).
    function runAnalytics() {
      if (!fromInput.value || !toInput.value) {
        showBannerA('Укажите обе недели периода.', 'warn');
        return;
      }
      if (fromInput.value > toInput.value) {
        showBannerA('«Неделя с» не может быть позже «Недели по».', 'warn');
        return;
      }
      showBtn.disabled = true;
      resultEl.innerHTML = '<p class="empty">Загрузка…</p>';
      resultEl.hidden = false;
      apiFetch('/needs/api/analytics?' + analyticsParams()).then(function (data) {
        if (!data) return;
        renderAnalytics(data);
      }).catch(function (err) {
        resultEl.innerHTML = '';
        showBannerA('Ошибка аналитики: ' + err.message, 'error');
      }).finally(function () {
        showBtn.disabled = false;
      });
    }
    showBtn.addEventListener('click', runAnalytics);
    if (formA) {
      formA.addEventListener('submit', function (e) {
        e.preventDefault();
        runAnalytics();
      });
    }

    // Скачивание .xlsx — та же выборка, что на экране.
    function refreshXlsxLink() {
      xlsxLink.href = '/needs/api/analytics.xlsx?' + analyticsParams();
    }
    [fromInput, toInput, baseSel, pointSelA, groupSel].forEach(function (el) {
      el.addEventListener('change', refreshXlsxLink);
      el.addEventListener('input', refreshXlsxLink);
    });
    refreshXlsxLink();

    // Растворы — таблицей с ИТОГО; остальные группы — секциями с подытогом.
    function renderAnalytics(data) {
      var html = '<h3 class="analytics-period">' + esc(data.period_label || '') + '</h3>';
      var solutions = data.solutions || {};
      var groups = data.groups || {};
      var points = data.points || [];

      if (!Object.keys(solutions).length && !Object.keys(groups).length) {
        html += '<p class="empty">За период и фильтры нет данных (только отправленные заявки).</p>';
      }

      if (Object.keys(solutions).length) {
        html += '<h4>Растворы</h4>';
        html += '<div class="table-wrap"><table class="needs-table">';
        html += '<tr><th>Раствор</th><th>Ед.</th>' +
          points.map(function (p) { return '<th>' + esc(p) + '</th>'; }).join('') +
          '<th>ИТОГО</th></tr>';
        Object.keys(solutions).forEach(function (item) {
          var row = solutions[item];
          html += '<tr><td>' + esc(item) + '</td><td>' + esc(row.unit || '') + '</td>' +
            points.map(function (p) { return '<td>' + (row[p] || 0) + '</td>'; }).join('') +
            '<td>' + (row['ИТОГО'] || 0) + '</td></tr>';
        });
        html += '</table></div>';
      }

      Object.keys(groups).forEach(function (group) {
        var items = groups[group];
        html += '<h4>' + esc(group || 'Без группы') + '</h4>';
        html += '<div class="table-wrap"><table class="needs-table">';
        html += '<tr><th>Препарат</th><th>Ед.</th><th>Кол-во</th></tr>';
        var total = 0;
        Object.keys(items).forEach(function (item) {
          var qty = items[item].qty || 0;
          total += qty;
          html += '<tr><td>' + esc(item) + '</td><td>' + esc(items[item].unit || '') +
            '</td><td>' + qty + '</td></tr>';
        });
        html += '<tr class="needs-subtotal"><td colspan="2">Итого по группе</td><td>' +
          total + '</td></tr>';
        html += '</table></div>';
      });

      resultEl.innerHTML = html;
    }
  }
})();

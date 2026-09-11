/* Потребности (needs.html, analytics.html): форма сестры, доска старшей,
   отчёты, аналитика. Vanilla JS, без библиотек и CDN (F10).
   Форма и доска разделены на два раздела — растворы/медикаменты
   (ТЗ-растворы-медикаменты): у каждого раздела свой поиск по каталогу,
   свои строки, своя отправка/закрытие/отчёт. Страницы используют один
   скрипт: needs.html — форма+доска, analytics.html — фильтры и таблица.
   Роли: форма — nurse/head_nurse/head, доска/отчёты/аналитика —
   head_nurse/head (ограничение на уровне шаблона и API). */
(function () {
  'use strict';

  /* ── утилиты ────────────────────────────────────────────────────── */

  // Экранирование текста для вставки в HTML.
  var esc = dc.esc;
  var apiFetch = dc.apiFetch;
  var iso = dc.iso;
  var mondayOfWeek = dc.mondayOf;

  function parseQty(s) {
    var n = parseInt(s, 10);
    return isNaN(n) || n < 0 ? 0 : n;
  }

  /* ── разделы (ТЗ-растворы-медикаменты) ──────────────────────────── */

  // Граница раздела выводится из каталога: группа «Растворы» — раздел
  // «растворы», все остальные группы — раздел «медикаменты».
  var SOLUTIONS_GROUP = 'Растворы';
  var CATEGORIES = [
    { key: 'solutions', label: 'Растворы' },
    { key: 'medicaments', label: 'Медикаменты' },
  ];
  var CATEGORY_LABELS = { solutions: 'Растворы', medicaments: 'Медикаменты' };

  /* ── страница /needs ────────────────────────────────────────────── */

  var app = document.getElementById('needs-app');
  if (!app) { initAnalyticsIfNeeded(); return; }

  var ROLE = app.getAttribute('data-role') || '';
  var MY_ID = parseInt(app.getAttribute('data-user-id') || '0', 10);
  var IS_FULL = ROLE === 'head_nurse' || ROLE === 'head';

  var bannerEl = document.getElementById('needs-banner');
  var pointsEl = document.getElementById('needs-points');
  var currentPointEl = document.getElementById('needs-current-point');
  var boardEl = document.getElementById('needs-board');
  var boardSolutionsEl = document.getElementById('needs-board-solutions');
  var boardMedicamentsEl = document.getElementById('needs-board-medicaments');
  var boardTabEls = {};
  if (boardEl) {
    var boardTabNodes = boardEl.querySelectorAll('.needs-board-tabs .needs-tab');
    for (var bi = 0; bi < boardTabNodes.length; bi++) {
      var bt = boardTabNodes[bi];
      boardTabEls[bt.getAttribute('data-board-tab')] = bt;
    }
  }
  var formCardEl = document.getElementById('needs-form-card');
  var formEl = document.getElementById('needs-form');

  // Состояние страницы.
  var catalog = { bases: {}, groups: {}, allItems: [] }; // каталог из API
  var currentBase = '';    // база выбранной точки
  var currentPoint = '';   // выбранная точка пополнения
  var week = iso(mondayOfWeek(new Date())); // текущая неделя (пн)
  // Закрытие недель — по базе и разделу: closed[base][category] = true.
  var closed = {};

  // Секции формы: {solutions: Section, medicaments: Section}. Каждая держит
  // свои строки и режим просмотра (нет/черновик/отправлено — на уровне раздела).
  var sections = {};
  CATEGORIES.forEach(function (cat) {
    if (!formEl) return;
    var root = formEl.querySelector('.needs-section[data-category="' + cat.key + '"]');
    if (!root) return;
    sections[cat.key] = {
      key: cat.key,
      label: cat.label,
      root: root,
      search: root.querySelector('.needs-search'),
      results: root.querySelector('.needs-search-results'),
      linesTbody: root.querySelector('.needs-lines'),
      status: root.querySelector('.needs-request-status'),
      saveBtn: root.querySelector('.needs-save'),
      submitBtn: root.querySelector('.needs-submit'),
      lines: [],       // выбранные позиции: {item, unit, group, qty}
      readOnly: false, // просмотр (чужой отправленный / раздел закрыт)
    };
  });

  // Активная вкладка раздела и статусы точек для цветовой индикации сетки.
  var activeCategory = 'solutions';
  var pointStatusByKey = {};  // 'base|point' -> status для активной категории
  var tabEls = {};
  if (formEl) {
    CATEGORIES.forEach(function (cat) {
      var tab = formEl.querySelector('.needs-tab[data-category="' + cat.key + '"]');
      if (tab) tabEls[cat.key] = tab;
    });
  }
  updateFormCardState(); // исходно точка не выбрана — форма приглушена

  // Баннер: сообщение + вид (ok/warn/error); прячем по таймеру.
  function showBanner(msg, kind) { dc.banner(bannerEl, msg, kind); }

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
    }).catch(function (err) {
      showBanner('Не удалось загрузить каталог: ' + err.message, 'error');
    });
  }

  // Закрытые разделы недели (для медсестры: видеть закрытие без доски).
  function loadClosures() {
    return apiFetch('/needs/api/closed?week=' + encodeURIComponent(week)).then(function (data) {
      if (!data) return;
      (data.closed || []).forEach(function (c) {
        (closed[c.base] = closed[c.base] || {})[c.category] = true;
      });
      renderPointGrid(); // обновить бейджи закрытия на сетке точек
    }).catch(function () {
      // тихо: если не вышло — доска (у старшей) или 409 при сохранении подхватят
    });
  }

  // Статусы точек активной категории — для цветовой индикации сетки точек.
  function loadPointStatus() {
    return apiFetch('/needs/api/points?category=' + encodeURIComponent(activeCategory) +
      '&week=' + encodeURIComponent(week)).then(function (data) {
      if (!data) return;
      pointStatusByKey = {};
      (data.points || []).forEach(function (p) {
        pointStatusByKey[p.base + '|' + p.point] = p.status;
      });
      renderPointGrid();
    }).catch(function () {
      // тихо: без индикации можно работать
    });
  }

  // Переключить вкладку раздела: показать нужную секцию и обновить цвета точек.
  function setActiveCategory(catKey) {
    if (activeCategory === catKey) return;
    activeCategory = catKey;
    CATEGORIES.forEach(function (cat) {
      if (sections[cat.key]) sections[cat.key].root.hidden = (cat.key !== catKey);
      if (tabEls[cat.key]) tabEls[cat.key].classList.toggle('needs-tab-active', cat.key === catKey);
    });
    loadPointStatus();
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
      var badges = CATEGORIES.filter(function (cat) {
        return closed[base] && closed[base][cat.key];
      }).map(function (cat) {
        return '<span class="needs-closed-badge">' + esc(cat.label) + ' закрыт</span>';
      }).join('');
      html += '<div class="needs-base">';
      html += '<h3>' + esc(base) + badges + '</h3>';
      html += '<div class="needs-board-grid">';
      points.forEach(function (point) {
        var active = point === currentPoint ? ' needs-point-active' : '';
        var status = pointStatusByKey[base + '|' + point] || 'none';
        html += '<button type="button" class="needs-point-cell' + active + '" ' +
          'data-base="' + esc(base) + '" data-point="' + esc(point) + '">' +
          '<span class="needs-dot ' + esc(status) + '"></span>' +
          '<span class="needs-cell-point">' + esc(point) + '</span>' +
          '</button>';
      });
      html += '</div></div>';
    });
    pointsEl.innerHTML = html;
  }

  // Показать форму заявки (для полных ролей она скрыта до клика по точке доски).
  function showFormCard() {
    if (formCardEl) formCardEl.classList.remove('needs-form-hidden');
  }

  // Приглушить форму, пока точка не выбрана.
  function updateFormCardState() {
    if (formCardEl) formCardEl.classList.toggle('needs-form-inactive', !currentPoint);
  }

  // Выбрать точку: обновить состояние, заголовок и подсветку, очистить поиск
  // обеих секций и загрузить заявки обоих разделов.
  function selectPoint(base, point) {
    currentBase = base;
    currentPoint = point;
    showFormCard();
    updateFormCardState();
    if (currentPointEl) currentPointEl.textContent = point ? ('— ' + point) : '';
    if (pointsEl) renderPointGrid();
    CATEGORIES.forEach(function (cat) {
      var sec = sections[cat.key];
      if (!sec) return;
      sec.search.value = '';
      sec.results.hidden = true;
      sec.results.innerHTML = '';
      syncControls(sec); // активировать форму после выбора точки
    });
    loadRequests();
  }

  /* ── заявка: загрузка, отрисовка, сохранение, отправка ──────────── */

  // Закрыт ли раздел выбранной базы (из доски или после 409 от save/submit).
  function isClosedSection(sec) {
    return !!(closed[currentBase] && closed[currentBase][sec.key]);
  }

  // Режим просмотра раздела: закрыт, либо чужой отправленный (для медсестры).
  function computeReadOnly(sec, req) {
    if (isClosedSection(sec)) return true;
    return req.status === 'sent' && req.author_id !== MY_ID && !IS_FULL;
  }

  // Загрузить заявки обоих разделов точки (два независимых GET).
  function loadRequests() {
    CATEGORIES.forEach(function (cat) {
      if (sections[cat.key]) loadRequest(sections[cat.key]);
    });
  }

  // Загрузить заявку раздела точки за неделю: есть → показать (чужая
  // отправленная — просмотр), 404 (нет заявки или чужой черновик) → пустая
  // форма. Закрытый раздел — просмотр.
  function loadRequest(sec) {
    if (!currentPoint) {
      sec.lines = [];
      renderLines(sec);
      setStatus(sec, 'Выберите точку пополнения.');
      return;
    }
    var url = '/needs/api/request?base=' + encodeURIComponent(currentBase) +
      '&point=' + encodeURIComponent(currentPoint) +
      '&category=' + encodeURIComponent(sec.key) +
      '&week=' + encodeURIComponent(week);
    return apiFetch(url).then(function (req) {
      if (!req) return;
      sec.lines = (req.lines || []).map(function (l) {
        return { item: l.item, unit: l.unit || '', group: l.grp || '', qty: l.qty || 0 };
      });
      sec.readOnly = computeReadOnly(sec, req);
      renderLines(sec);
      setStatus(sec,
        'Заявка: ' + (req.status === 'sent' ? 'отправлена' : 'черновик') +
        (sec.readOnly ? ' — просмотр (изменения заблокированы)' : '')
      );
      if (sec.readOnly && isClosedSection(sec)) {
        showBanner('Раздел «' + sec.label + '» закрыт для базы «' + currentBase +
          '» — правки запрещены.', 'warn');
      }
    }).catch(function (err) {
      if (err.status === 404) {
        // Заявки нет (или это чужой черновик, скрытый API) — пустая форма.
        sec.lines = [];
        sec.readOnly = isClosedSection(sec);
        renderLines(sec);
        setStatus(sec, sec.readOnly
          ? 'Раздел закрыт — правки недоступны.'
          : 'Заявки на эту неделю ещё нет.');
        return;
      }
      showBanner('Ошибка загрузки заявки: ' + err.message, 'error');
    });
  }

  // Отрисовать таблицу выбранных позиций раздела: Название | Ед. | Кол-во | ×.
  function renderLines(sec) {
    if (!sec.lines.length) {
      sec.linesTbody.innerHTML = '<tr><td colspan="4" class="empty">' +
        'Позиции не выбраны — найдите препарат поиском выше.</td></tr>';
      syncControls(sec);
      return;
    }
    var html = sec.lines.map(function (line, i) {
      var dis = sec.readOnly ? ' disabled' : '';
      return '<tr>' +
        '<td>' + esc(line.item) + '</td>' +
        '<td>' + esc(line.unit) + '</td>' +
        '<td><div class="needs-qty-stepper">' +
          '<button type="button" class="needs-qty-btn" data-i="' + i + '" data-delta="-1"' + dis + ' aria-label="Меньше">−</button>' +
          '<input type="number" inputmode="numeric" min="0" step="1" ' +
            'class="needs-qty" data-i="' + i + '" value="' +
            (line.qty > 0 ? line.qty : '0') + '"' + dis + '>' +
          '<button type="button" class="needs-qty-btn" data-i="' + i + '" data-delta="1"' + dis + ' aria-label="Больше">+</button>' +
          '</div></td>' +
        '<td><button type="button" class="needs-remove" data-i="' + i + '" ' +
          'aria-label="Удалить"' + dis + '>×</button></td>' +
        '</tr>';
    }).join('');
    sec.linesTbody.innerHTML = html;
    syncControls(sec);
  }

  // Включить/выключить управление секцией по режиму: неактивно без выбранной
  // точки или в режиме просмотра (чужой отправленный / раздел закрыт).
  function syncControls(sec) {
    var disabled = sec.readOnly || !currentPoint;
    [sec.search, sec.saveBtn, sec.submitBtn].forEach(function (el) {
      if (el) el.disabled = disabled;
    });
  }

  function setStatus(sec, text) {
    if (sec.status) sec.status.textContent = text;
  }

  // Собрать строки раздела для API: [{item, qty}] с числами (пустое поле → 0).
  function collectLines(sec) {
    var out = [];
    sec.lines.forEach(function (line, i) {
      var input = sec.linesTbody.querySelector('.needs-qty[data-i="' + i + '"]');
      out.push({ item: line.item, qty: input ? parseQty(input.value) : 0 });
    });
    return out;
  }

  // Тело заявки раздела для POST /needs/api/request.
  function requestPayload(sec) {
    return {
      base: currentBase,
      point: currentPoint,
      category: sec.key,
      week: week,
      lines: collectLines(sec),
    };
  }

  // «Сохранить» раздела: POST /needs/api/request со status='draft'.
  function saveDraft(sec) {
    if (sec.readOnly || !currentPoint) return;
    var payload = requestPayload(sec);
    payload.status = 'draft';
    sec.saveBtn.disabled = true;
    apiFetch('/needs/api/request', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }).then(function (data) {
      if (!data) return;
      showBanner('Черновик раздела «' + sec.label + '» сохранён.', 'ok');
      setStatus(sec, 'Заявка: черновик — сохранено');
      if (IS_FULL && boardEl) loadBoard(); // статус доски мог измениться
      if (!IS_FULL) loadPointStatus();     // обновить цвет точки у медсестры
    }).catch(function (err) { showApiError(sec, err); }).finally(function () {
      sec.saveBtn.disabled = sec.readOnly;
    });
  }

  // «Отправить» раздела: сохранить draft, затем POST /needs/api/request/submit;
  // предупреждения о строках с нулевым количеством — из ответа.
  function submitRequest(sec) {
    if (sec.readOnly || !currentPoint) return;
    var payload = requestPayload(sec);
    payload.status = 'draft';
    sec.submitBtn.disabled = true;
    apiFetch('/needs/api/request', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }).then(function (data) {
      if (!data) return;
      return apiFetch('/needs/api/request/submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          base: currentBase, point: currentPoint, category: sec.key, week: week,
        }),
      });
    }).then(function (res) {
      if (!res) return;
      var warnings = (res.warnings || []).filter(Boolean);
      var msg = 'Заявка раздела «' + sec.label + '» отправлена.';
      if (warnings.length) {
        msg = 'Заявка раздела «' + sec.label + '» отправлена. Предупреждение: ' +
          warnings.length + ' позиций с нулевым количеством: ' +
          warnings.join(', ') + '.';
      }
      showBanner(msg, warnings.length ? 'warn' : 'ok');
      return loadRequest(sec); // обновить статус (sent) и режим
    }).then(function () {
      if (IS_FULL && boardEl) loadBoard();
      if (!IS_FULL) loadPointStatus();
    }).catch(function (err) { showApiError(sec, err); }).finally(function () {
      sec.submitBtn.disabled = sec.readOnly;
    });
  }

  // Показать ошибку API понятным сообщением: 409 — «раздел закрыт»,
  // 403/400 — текст из ответа; закрытый раздел запоминаем (просмотр).
  function showApiError(sec, err) {
    if (err.status === 409) {
      (closed[currentBase] = closed[currentBase] || {})[sec.key] = true;
      sec.readOnly = true;
      renderLines(sec);
      showBanner('Раздел «' + sec.label + '» недели закрыт для базы «' +
        currentBase + '»: ' + err.message, 'error');
    } else {
      showBanner('Ошибка: ' + err.message, 'error');
    }
  }

  /* ── поиск препаратов по каталогу (внутри раздела) ──────────────── */

  // Поиск только по своему разделу: растворы — группа «Растворы»,
  // медикаменты — все остальные группы. Пустой запрос — весь раздел
  // (для показа списка по фокусу).
  function searchMatches(sec, q) {
    var needle = q.trim().toLowerCase();
    return catalog.allItems.filter(function (item) {
      if (sec.key === 'solutions') {
        if (item.group !== SOLUTIONS_GROUP) return false;
      } else if (item.group === SOLUTIONS_GROUP) {
        return false;
      }
      if (!needle) return true;
      return item.name.toLowerCase().indexOf(needle) !== -1;
    });
  }

  function renderSearchResults(sec, q) {
    var matches = searchMatches(sec, q);
    if (!matches.length) {
      sec.results.hidden = true;
      sec.results.innerHTML = '';
      return;
    }
    sec.results.innerHTML = matches.map(function (item) {
      return '<button type="button" class="needs-search-item" ' +
        'data-item="' + esc(item.name) + '">' +
        '<span class="needs-search-name">' + esc(item.name) + '</span>' +
        '<span class="needs-search-meta">' + esc(item.unit) + ' · ' + esc(item.group) + '</span>' +
        '</button>';
    }).join('');
    sec.results.hidden = false;
  }

  // Добавить позицию в список выбранного раздела; дубль — фокус на количество.
  function addLine(sec, itemName) {
    var idx = sec.lines.findIndex(function (l) { return l.item === itemName; });
    if (idx === -1) {
      var found = catalog.allItems.find(function (i) { return i.name === itemName; });
      sec.lines.push({
        item: itemName,
        unit: found ? found.unit : '',
        group: found ? found.group : '',
        qty: 1,
      });
      renderLines(sec);
      idx = sec.lines.length - 1;
    }
    var input = sec.linesTbody.querySelector('.needs-qty[data-i="' + idx + '"]');
    if (input) { input.focus(); input.select(); }
  }

  /* ── доска старшей ──────────────────────────────────────────────── */

  var STATUS_LABEL = { none: 'нет заявки', draft: 'черновик', sent: 'отправлено' };

  // Винительный падеж для кнопки «Закрыть …» (ТЗ F6): Ленская → Ленскую.
  var BASE_ACCUSATIVE = { 'Ленская': 'Ленскую', 'Таймырская': 'Таймырскую' };
  function baseAccusative(base) { return BASE_ACCUSATIVE[base] || base; }

  // Доска: точки обеих баз по двум разделам со статусом, автором, закрытием
  // раздела. Ячейки API уже содержат category — группируем по разделам.
  function loadBoard() {
    if (!boardSolutionsEl) return;
    boardSolutionsEl.innerHTML = '<p class="empty">Загрузка…</p>';
    apiFetch('/needs/api/board?week=' + encodeURIComponent(week)).then(function (data) {
      if (!data) return;
      var cells = data.board || [];
      closed = {};
      cells.forEach(function (cell) {
        if (cell.is_closed) {
          (closed[cell.base] = closed[cell.base] || {})[cell.category] = true;
        }
      });
      var byBase = {};
      cells.forEach(function (cell) {
        (byBase[cell.base] = byBase[cell.base] || []).push(cell);
      });
      renderBoardSection(boardSolutionsEl, 'solutions', byBase);
      renderBoardSection(boardMedicamentsEl, 'medicaments', byBase);
      // Если открытый раздел выбранной точки оказался закрыт — в просмотр.
      if (currentPoint) {
        CATEGORIES.forEach(function (cat) {
          var sec = sections[cat.key];
          if (sec && !sec.readOnly && isClosedSection(sec)) {
            sec.readOnly = true;
            renderLines(sec);
            showBanner('Раздел «' + sec.label + '» закрыт для базы «' + currentBase +
              '» — правки запрещены.', 'warn');
          }
        });
      }
    }).catch(function (err) {
      if (boardSolutionsEl) boardSolutionsEl.innerHTML = '<p class="empty">Доска недоступна: ' + esc(err.message) + '</p>';
    });
  }

  // Доска: секция раздела — сетка точек обеих баз, кнопки закрытия/отчётов.
  function renderBoardSection(el, catKey, byBase) {
    var html = '';
    Object.keys(catalog.bases).forEach(function (base) {
      var baseCells = (byBase[base] || []).filter(function (c) {
        return c.category === catKey;
      });
      var isClosed = !!(closed[base] && closed[base][catKey]);
      html += '<div class="needs-base">';
      html += '<h3>' + esc(base) +
        (isClosed ? ' <span class="needs-closed-badge">раздел закрыт</span>' : '') +
        '</h3>';
      if (!baseCells.length) {
        html += '<p class="empty">Точек в каталоге нет.</p>';
      } else {
        html += '<div class="needs-board-grid">';
        baseCells.forEach(function (cell) {
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
      // Кнопки отчёта — строго по базе и разделу за текущую неделю.
      var q = 'base=' + encodeURIComponent(base) +
        '&category=' + encodeURIComponent(catKey) +
        '&week=' + encodeURIComponent(week);
      html += '<div class="needs-base-report">' +
        '<a class="btn" href="/needs/report.xlsx?' + q + '">Отчёт (xlsx)</a>' +
        '<a class="btn" href="/needs/report?' + q + '">Отчёт (просмотр)</a>' +
        '</div>';
      html += '<div class="needs-actions">';
      if (isClosed) {
        html += '<button type="button" class="btn needs-reopen" data-base="' + esc(base) +
          '" data-category="' + esc(catKey) + '">Открыть заново</button>';
      } else {
        html += '<button type="button" class="btn needs-close" data-base="' + esc(base) +
          '" data-category="' + esc(catKey) + '">Закрыть ' +
          esc(CATEGORY_LABELS[catKey].toLowerCase()) + ' · ' + baseAccusative(base) + '</button>';
      }
      html += '</div>';
      html += '</div>';
    });
    el.innerHTML = html;
  }

  // Переключить вкладку доски (растворы / медикаменты / аналитика).
  function setBoardTab(tabKey) {
    ['solutions', 'medicaments', 'analytics'].forEach(function (key) {
      var panel = document.getElementById('needs-board-' + key);
      if (panel) panel.hidden = (key !== tabKey);
      var tab = boardTabEls[key];
      if (tab) tab.classList.toggle('needs-tab-active', key === tabKey);
    });
  }

  // Клик по точке доски — открыть её в форме заявки (только у полных ролей,
  // где блок #needs-board есть; у медсестры boardEl отсутствует).
  if (boardEl) {
    boardEl.addEventListener('click', function (e) {
      var cell = e.target.closest('.needs-board-cell');
      if (cell) {
        selectPoint(cell.getAttribute('data-base'), cell.getAttribute('data-point'));
        return;
      }
      var closeBtn = e.target.closest('.needs-close');
      if (closeBtn) {
        closeBase(closeBtn.getAttribute('data-base'), closeBtn.getAttribute('data-category'));
        return;
      }
      var reopenBtn = e.target.closest('.needs-reopen');
      if (reopenBtn) {
        reopenBase(reopenBtn.getAttribute('data-base'), reopenBtn.getAttribute('data-category'));
        return;
      }
    });
  }

  // Клик по вкладке доски — переключить панель.
  Object.keys(boardTabEls).forEach(function (key) {
    boardTabEls[key].addEventListener('click', function () { setBoardTab(key); });
  });

  // Клик по карточке точки в сетке медсестры — выбрать её.
  if (pointsEl) {
    pointsEl.addEventListener('click', function (e) {
      var cell = e.target.closest('.needs-point-cell');
      if (!cell) return;
      selectPoint(cell.getAttribute('data-base'), cell.getAttribute('data-point'));
    });
  }

  // Закрыть неделю раздела для базы: подтверждение, POST /needs/api/close,
  // затем предупреждение со списком неотправивших точек из ответа.
  function closeBase(base, category) {
    var label = CATEGORY_LABELS[category];
    if (!window.confirm('Закрыть раздел «' + label + '» недели для базы «' + base +
      '»? Точки без отправленной заявки в отчёт не попадут.')) return;
    apiFetch('/needs/api/close', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ base: base, category: category, week: week }),
    }).then(function (data) {
      if (!data) return;
      var unsent = data.unsent_points || [];
      var msg = 'Раздел «' + label + '» недели для базы «' + base + '» закрыт.';
      if (unsent.length) {
        msg += ' Не отправили заявку: ' + unsent.join(', ') + '.';
      }
      showBanner(msg, unsent.length ? 'warn' : 'ok');
      (closed[base] = closed[base] || {})[category] = true;
      if (base === currentBase && sections[category]) {
        sections[category].readOnly = true;
        renderLines(sections[category]);
        setStatus(sections[category], 'Заявка: просмотр (раздел закрыт)');
      }
      loadBoard();
    }).catch(function (err) {
      showBanner('Ошибка закрытия недели: ' + err.message, 'error');
    });
  }

  // Открыть неделю раздела заново: POST /needs/api/reopen.
  function reopenBase(base, category) {
    var label = CATEGORY_LABELS[category];
    if (!window.confirm('Открыть раздел «' + label + '» недели для базы «' + base +
      '» заново? Правки и отправка снова станут доступны.')) return;
    apiFetch('/needs/api/reopen', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ base: base, category: category, week: week }),
    }).then(function (data) {
      if (!data) return;
      showBanner('Раздел «' + label + '» недели для базы «' + base + '» открыт заново.', 'ok');
      if (closed[base]) delete closed[base][category];
      if (base === currentBase && sections[category]) {
        return loadRequest(sections[category]); // вернуть режим правки
      }
      loadBoard();
    }).catch(function (err) {
      showBanner('Ошибка переоткрытия недели: ' + err.message, 'error');
    });
  }

  /* ── события формы (по секциям) ─────────────────────────────────── */

  CATEGORIES.forEach(function (cat) {
    var sec = sections[cat.key];
    if (!sec) return;

    sec.search.addEventListener('input', function () {
      if (sec.readOnly) return;
      renderSearchResults(sec, sec.search.value);
    });

    // Фокус на поиске — показать список (весь раздел или по текущему тексту).
    sec.search.addEventListener('focus', function () {
      if (sec.readOnly) return;
      renderSearchResults(sec, sec.search.value);
    });

    // Клик по найденной позиции — добавить в список; фокус уходит в количество
    // (addLine сам фокусирует поле количества добавленной позиции).
    sec.results.addEventListener('click', function (e) {
      var item = e.target.closest('.needs-search-item');
      if (!item) return;
      addLine(sec, item.getAttribute('data-item'));
      sec.search.value = '';
      sec.results.hidden = true;
      sec.results.innerHTML = '';
    });

    // Удаление позиции из списка выбранного раздела.
    sec.linesTbody.addEventListener('click', function (e) {
      var btn = e.target.closest('.needs-remove');
      if (!btn || sec.readOnly) return;
      var i = parseInt(btn.getAttribute('data-i'), 10);
      if (isNaN(i) || i < 0 || i >= sec.lines.length) return;
      sec.lines.splice(i, 1);
      renderLines(sec);
    });

    // Кнопки «− / +» количества: изменить значение и синхронизировать состояние.
    sec.linesTbody.addEventListener('click', function (e) {
      var btn = e.target.closest('.needs-qty-btn');
      if (!btn || sec.readOnly) return;
      var i = parseInt(btn.getAttribute('data-i'), 10);
      var delta = parseInt(btn.getAttribute('data-delta'), 10);
      if (isNaN(i) || i < 0 || i >= sec.lines.length) return;
      var input = sec.linesTbody.querySelector('.needs-qty[data-i="' + i + '"]');
      var qty = parseQty(input ? input.value : '0') + (isNaN(delta) ? 0 : delta);
      if (qty < 0) qty = 0;
      sec.lines[i].qty = qty;
      if (input) input.value = qty;
    });

    // Ввод количества — синхронизировать в состояние строк, чтобы значения
    // не терялись при перерисовке (добавление строки, смена точки и т.п.).
    sec.linesTbody.addEventListener('input', function (e) {
      if (!e.target.classList || !e.target.classList.contains('needs-qty')) return;
      var i = parseInt(e.target.getAttribute('data-i'), 10);
      if (isNaN(i) || i < 0 || i >= sec.lines.length) return;
      sec.lines[i].qty = parseQty(e.target.value);
    });

    // Enter в количестве — вернуть фокус на поиск препарата (следующая позиция).
    sec.linesTbody.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter' || !e.target.classList.contains('needs-qty')) return;
      e.preventDefault();
      sec.search.focus();
    });

    sec.saveBtn.addEventListener('click', function () { saveDraft(sec); });
    sec.submitBtn.addEventListener('click', function () { submitRequest(sec); });
  });

  // Клик по вкладке — переключить активный раздел.
  CATEGORIES.forEach(function (cat) {
    var tab = tabEls[cat.key];
    if (tab) tab.addEventListener('click', function () { setActiveCategory(cat.key); });
  });

  // Клик вне блока поиска секции — спрятать её подсказки.
  document.addEventListener('click', function (e) {
    CATEGORIES.forEach(function (cat) {
      var sec = sections[cat.key];
      if (!sec || sec.results.hidden) return;
      if (!sec.search.contains(e.target) && !sec.results.contains(e.target)) {
        sec.results.hidden = true;
      }
    });
  });

  /* ── запуск страницы /needs ─────────────────────────────────────── */

  loadCatalog().then(function () {
    loadClosures().then(function () {
      return loadPointStatus();
    }).then(function () {
      if (IS_FULL && boardEl) loadBoard();
    });
  });

  // Аналитика вкладкой у старшей — инициализировать, если панель присутствует.
  if (document.getElementById('analytics-form')) initAnalytics();

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
    var sectionSel = document.getElementById('analytics-section');
    var showBtn = document.getElementById('analytics-show');
    var formA = document.getElementById('analytics-form');
    var xlsxLink = document.getElementById('analytics-xlsx');
    var resultEl = document.getElementById('analytics-result');
    var bannerElA = document.getElementById('analytics-banner');

    function showBannerA(msg, kind) { dc.banner(bannerElA, msg, kind); }

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
      if (sectionSel.value) params.push('section=' + encodeURIComponent(sectionSel.value));
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
    [fromInput, toInput, baseSel, pointSelA, groupSel, sectionSel].forEach(function (el) {
      el.addEventListener('change', refreshXlsxLink);
      el.addEventListener('input', refreshXlsxLink);
    });
    refreshXlsxLink();

    // Растворы — таблицей с ИТОГО; остальные группы — секциями с подытогом.
    // При фильтре «Раздел» бэкенд возвращает только один блок (другой пуст),
    // поэтому «Все» рендерится двумя блоками, «растворы/медикаменты» — одним.
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

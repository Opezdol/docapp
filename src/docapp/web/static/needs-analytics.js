/* Аналитика «Потребностей» (analytics.html): фильтры периода и таблица сводки.

   Раньше жила в needs.js и включалась «по отсутствию» элемента #needs-app —
   страница аналитики грузила весь файл формы и доски. Теперь это отдельный
   сценарий: страница берёт только его.

   Общее (экранирование, запрос к API, календарь, баннер) — из /static/lib/core.js:
   объект dc. */
(function () {
  'use strict';

  var esc = dc.esc;
  var iso = dc.iso;
  var mondayOfWeek = dc.mondayOf;
  var apiFetch = dc.apiFetch;

// Форма аналитики есть и на отдельной странице /needs/analytics, и вкладкой
// на доске старшей — сценарий включается по ней, а не по адресу страницы.
function initAnalyticsIfNeeded() {
  if (!document.getElementById('analytics-form')) return;
  initAnalytics();
}

function initAnalytics() {
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

  initAnalyticsIfNeeded();
})();

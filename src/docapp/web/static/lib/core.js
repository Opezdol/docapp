/* Общая библиотека фронтенда docapp.
 *
 * Классический скрипт (не модуль): проект собирается без сборщика, а страницы
 * подключают свои файлы как `<script src=...?v=hash>` — один объект `window.dc`
 * проще и не требует менять разметку всех страниц.
 *
 * Здесь то, что раньше было скопировано по модулям: экранирование, запрос к API,
 * баннер-сообщение, календарные помощники, поле времени и выбор из списка с
 * поиском. Своё у модуля остаётся своим: у «Потребностей» — степпер количества и
 * своя разметка, у «Дежурств» — сетка смены.
 *
 * Подключается в base.html в <head>, поэтому к моменту запуска модульных скриптов
 * (они в конце страницы) объект уже есть.
 */
(function () {
  'use strict';

  /* ── шаг поля времени ────────────────────────────────────────────────
   * Решение владельца от 11.09.2026: ввод времени — шаг 10 минут (было 15).
   * Сетка выгрузки .xlsx остаётся 15-минутной: время всё равно попадает в свою
   * клетку, потому что клетка накрывает свой интервал.
   */
  var TIME_STEP = 10;

  /* ── экранирование ─────────────────────────────────────────────────── */

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  /* ── запрос к API ──────────────────────────────────────────────────── */

  // fetch с общими правилами: нет сессии → на вход; ошибка → Error с текстом
  // из тела ответа (сервер отдаёт {error} или {detail}).
  function apiFetch(url, opts) {
    return fetch(url, opts).then(function (r) {
      if (r.status === 401) { location.href = '/login'; return null; }
      if (!r.ok) {
        return r.json().catch(function () { return {}; }).then(function (body) {
          var err = new Error(body.error || body.detail || ('HTTP ' + r.status));
          err.status = r.status;
          throw err;
        });
      }
      return r.json();
    });
  }

  /* ── сообщение об ошибке или успехе ────────────────────────────────── */

  var bannerTimers = new WeakMap();

  // Показать сообщение в баннере и спрятать через 6 секунд.
  // el — свой баннер каждой страницы, поэтому состояние таймера живёт на нём.
  function banner(el, msg, kind) {
    if (!el) return;
    el.textContent = msg;
    el.className = 'dc-banner ' + (kind || 'ok');
    el.hidden = false;
    var previous = bannerTimers.get(el);
    if (previous) clearTimeout(previous);
    bannerTimers.set(el, setTimeout(function () { el.hidden = true; }, 6000));
  }

  /* ── календарь ─────────────────────────────────────────────────────── */

  function pad2(n) { return (n < 10 ? '0' : '') + n; }

  // Дата → 'ГГГГ-ММ-ДД' (в местном времени: сервер и клиент на одном поясе).
  function iso(d) {
    return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate());
  }

  // 'ГГГГ-ММ-ДД' → 'ДД.ММ.ГГГГ' — как дата показывается человеку.
  // Пустое значение → пусто; неожиданный формат → как есть, без «undefined».
  function fmtDate(value) {
    if (!value) return '';
    var p = String(value).slice(0, 10).split('-');
    return p.length === 3 ? p[2] + '.' + p[1] + '.' + p[0] : String(value);
  }

  // Понедельник недели даты — та же граница недели, что у серверного «периода».
  function mondayOf(d) {
    var x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    var weekday = (x.getDay() + 6) % 7;   // 0 — понедельник
    x.setDate(x.getDate() - weekday);
    return x;
  }

  /* ── время ЧЧ:ММ ───────────────────────────────────────────────────── */

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
    return pad2(h) + ':' + pad2(total % 60);
  }

  // Минуты от полуночи, округлённые до шага поля; null остаётся null.
  function snapMinutes(parsed, step) {
    step = step || TIME_STEP;
    return parsed == null ? null : Math.round(parsed / step) * step;
  }

  // Нормализовать одно поле: разобрать, округлить до шага, записать 'ЧЧ:ММ'.
  function snapTimeInput(input, step) {
    input.value = fmtTimeMinOrEmpty(snapMinutes(parseTimeMin(input.value), step));
  }

  // Сдвинуть время на deltaMin (скролл колесом), оборачивая через полночь.
  function adjustTimeInput(input, deltaMin, step) {
    var total = (snapMinutes(parseTimeMin(input.value), step) || 0) + deltaMin;
    total = ((total % 1440) + 1440) % 1440;
    input.value = fmtTimeMin(total);
  }

  function fmtTimeMinOrEmpty(parsed) {
    return parsed == null ? '' : fmtTimeMin(parsed);
  }

  // Повесить на контейнер поведение полей времени: округление при уходе и по Enter,
  // шаг скроллом колеса. Делегирование — поля создаются заново при отрисовке таблицы.
  function timeFields(container, opts) {
    if (!container) return;
    opts = opts || {};
    var selector = opts.selector || 'input[type="text"]';
    var step = opts.step || TIME_STEP;

    function isTimeField(target) {
      return !!(target && target.matches && target.matches(selector));
    }

    container.addEventListener('blur', function (e) {
      if (isTimeField(e.target)) snapTimeInput(e.target, step);
    }, true);

    container.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && isTimeField(e.target)) snapTimeInput(e.target, step);
    });

    container.addEventListener('wheel', function (e) {
      if (!isTimeField(e.target)) return;
      e.preventDefault();
      adjustTimeInput(e.target, e.deltaY < 0 ? step : -step, step);
    }, { passive: false });
  }

  // Нормализовать все поля времени в контейнере — перед чтением значений.
  function snapTimeFields(container, selector, step) {
    if (!container) return;
    container.querySelectorAll(selector || 'input[type="text"]').forEach(function (input) {
      snapTimeInput(input, step);
    });
  }

  /* ── выбор из списка с поиском ─────────────────────────────────────── */

  // Надстройка над <select>: поле ввода с фильтром по набираемому тексту и
  // выпадающий список.
  //
  // Разметку страницы менять не нужно: select остаётся в форме (только скрыт),
  // поэтому уходит ровно то же значение, а без JS работает прежний нативный
  // список. Выбор строки выставляет значение select и шлёт обычное событие
  // change — обработчики страницы остаются как были.
  //
  // Требование «значение выбрано» переезжает на видимое поле: скрытый
  // обязательный select браузер показать не может и молча блокирует отправку
  // формы, поэтому required снимается с select и ставится на поле ввода.
  function combobox(select) {
    if (!select || !select.options || select.dataset.dcCombo) return;
    select.dataset.dcCombo = '1';

    var options = [];
    var placeholder = '';
    for (var i = 0; i < select.options.length; i++) {
      var option = select.options[i];
      var label = String(option.text).trim();
      if (!option.value) { placeholder = label; continue; }  // «— выберите… —»
      if (option.disabled) continue;
      options.push({ value: option.value, label: label });
    }

    var wrapper = document.createElement('div');
    wrapper.className = 'dc-combo';

    var input = document.createElement('input');
    input.type = 'text';
    input.className = 'dc-combo-input';
    input.autocomplete = 'off';
    input.placeholder = placeholder;
    input.required = !!select.required;
    select.required = false;
    select.classList.add('dc-combo-native');

    var list = document.createElement('div');
    list.className = 'dc-combo-list';
    list.hidden = true;

    wrapper.appendChild(input);
    select.parentNode.insertBefore(wrapper, select);

    // Список — сосед подписи, а не её содержимое: кнопками внутри <label>
    // браузер подписывает не то поле (та же разметка, что у поиска препарата
    // в «Потребностях» — поле в подписи, список под ней).
    var label = select.closest ? select.closest('label') : null;
    if (label && label.parentNode) label.parentNode.insertBefore(list, label.nextSibling);
    else wrapper.appendChild(list);

    function labelOf(value) {
      for (var k = 0; k < options.length; k++) {
        if (options[k].value === String(value)) return options[k].label;
      }
      return '';
    }

    // Совпадение — по подстроке без учёта регистра: «иван» находит «Иванова».
    function render(query) {
      var q = String(query == null ? '' : query).trim().toLowerCase();
      var found = options;
      if (q) {
        found = options.filter(function (o) {
          return o.label.toLowerCase().indexOf(q) >= 0;
        });
      }
      if (!found.length) {
        list.innerHTML = '<div class="dc-combo-empty">Никого не найдено</div>';
        return;
      }
      list.innerHTML = found.map(function (o) {
        return '<button type="button" class="dc-combo-item" data-value="' + esc(o.value) + '">' +
          esc(o.label) + '</button>';
      }).join('');
    }

    // Показать в поле выбранное значение. Набранный, но не выбранный текст
    // иначе выглядит как выбор — поэтому фильтр при уходе сбрасывается.
    function syncFromSelect() {
      input.value = labelOf(select.value);
    }

    function pick(value) {
      var changed = String(select.value) !== String(value);
      select.value = value;
      input.value = labelOf(value);
      list.hidden = true;
      if (changed) select.dispatchEvent(new Event('change', { bubbles: true }));
    }

    input.addEventListener('focus', function () {
      render('');
      list.hidden = false;
    });

    input.addEventListener('input', function () {
      render(input.value);
      list.hidden = false;
    });

    input.addEventListener('blur', function () {
      list.hidden = true;
      syncFromSelect();
    });

    input.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { list.hidden = true; syncFromSelect(); return; }
      if (e.key !== 'Enter' || list.hidden) return;
      var first = list.querySelector('.dc-combo-item');
      if (!first) return;
      e.preventDefault();          // Enter выбирает строку, а не отправляет форму
      pick(first.getAttribute('data-value'));
    });

    // Клик по строке: mousedown гасим, иначе поле теряет фокус и список
    // закрывается раньше, чем придёт click.
    list.addEventListener('mousedown', function (e) { e.preventDefault(); });
    list.addEventListener('click', function (e) {
      var item = e.target && e.target.closest ? e.target.closest('.dc-combo-item') : null;
      if (item) pick(item.getAttribute('data-value'));
    });

    syncFromSelect();
  }

  window.dc = {
    TIME_STEP: TIME_STEP,
    esc: esc,
    apiFetch: apiFetch,
    banner: banner,
    iso: iso,
    fmtDate: fmtDate,
    mondayOf: mondayOf,
    parseTimeMin: parseTimeMin,
    fmtTimeMin: fmtTimeMin,
    snapTimeInput: snapTimeInput,
    adjustTimeInput: adjustTimeInput,
    timeFields: timeFields,
    snapTimeFields: snapTimeFields,
    combobox: combobox,
  };
})();

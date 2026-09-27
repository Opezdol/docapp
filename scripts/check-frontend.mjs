#!/usr/bin/env node
/* Проверка фронтенда без браузера.
 *
 * Зачем: серверную часть закрывает pytest, а JS остаётся непроверенным —
 * браузера на рабочей машине нет (Chromium не установлен). Этот скрипт
 * прогоняет то, что можно проверить честно:
 *   1) чистые функции общей библиотеки (экранирование, неделя, шаг времени);
 *   2) делегированные обработчики полей времени и баннер;
 *   3) загрузку сценариев страниц в подставном DOM: файл обязан молча выйти на
 *      чужой странице и дойти до первого запроса к API на своей;
 *   4) структуру: нет копий общего в файлах модулей, шаблоны грузят библиотеку
 *      раньше своего сценария, классы баннеров приведены к одному;
 *   5) вызовы помощников: имя обязано быть объявлено в самом файле, в общей
 *      библиотеке (dc) или быть браузерным глобальным. Так ловится ошибка вида
 *      `fmtDate is not defined`, которую подставной DOM пропускает: ответ API
 *      там никогда не приходит, и код внутри .then() не выполняется.
 *
 * Запуск: node scripts/check-frontend.mjs
 * Из pytest: tests/test_frontend_js.py (пропускается, если в системе нет node).
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const STATIC = join(ROOT, "src", "docapp", "web", "static");
const SOURCE = join(ROOT, "src", "docapp");
const read = (path) => readFileSync(path, "utf8");

let failed = 0;
let checks = 0;

function check(name, run) {
  checks++;
  try {
    const problem = run();
    if (problem) throw new Error(problem);
    console.log("ок    " + name);
  } catch (error) {
    failed++;
    console.log("ПЛОХО " + name + ": " + error.message);
  }
}

function equal(name, actual, expected) {
  check(name + " → " + JSON.stringify(expected), () => {
    if (JSON.stringify(actual) !== JSON.stringify(expected)) {
      return "получено " + JSON.stringify(actual);
    }
  });
}

// ── 1. общая библиотека ──────────────────────────────────────────────

/* Подставной DOM для проверки комбобокса: `dc.combobox` трогает ровно это —
   создание узлов, вставку в разметку, обработчики и классы. В песочнице
   `document` не глобальный объект, поэтому передаём его явно (как и dc). */
function comboNode(tag) {
  const node = {
    tagName: tag, className: "", value: "", hidden: false, innerHTML: "", text: "",
    required: false, type: "", placeholder: "", autocomplete: "",
    dataset: {}, children: [], attributes: {}, handlers: {}, events: [],
    classList: { add(name) { node.className += " " + name; }, remove() {}, contains: () => false },
    addEventListener(name, fn) { node.handlers[name] = fn; },
    appendChild(child) { node.children.push(child); return child; },
    insertBefore(child) { node.children.push(child); return child; },
    getAttribute(name) { return name in node.attributes ? node.attributes[name] : null; },
    setAttribute(name, value) { node.attributes[name] = String(value); },
    closest: () => null,
    querySelector: () => null,
    dispatchEvent(event) { node.events.push(event); return true; },
  };
  return node;
}
const comboDocument = { createElement: (tag) => comboNode(tag) };

const window = { location: { href: "" } };
const timers = [];
/* Один счётчик запросов на всю проверку: dc.apiFetch — замыкание библиотеки,
   и запрос страницы придёт именно в этот fetch, а не в отдельный для файла. */
const netCalls = [];
const recordingFetch = (url) => { netCalls.push(url); return new Promise(() => {}); };
new Function("window", "fetch", "setTimeout", "clearTimeout", "document", read(join(STATIC, "lib", "core.js")))(
  window,
  recordingFetch,
  (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
  () => {},
  comboDocument
);
const dc = window.dc;

check("библиотека положила объект dc", () => {
  if (!dc || typeof dc !== "object") return "dc не создан";
});
for (const name of ["esc", "apiFetch", "banner", "iso", "mondayOf", "parseTimeMin",
                    "fmtTimeMin", "timeFields", "snapTimeFields", "combobox"]) {
  check("dc." + name + " — функция", () => (typeof dc[name] === "function" ? null : typeof dc[name]));
}

equal("esc экранирует", dc.esc('<b>&"x"\'</b>'), "&lt;b&gt;&amp;&quot;x&quot;&#39;&lt;/b&gt;");
equal("esc(null) — пусто", dc.esc(null), "");
equal("iso(11.09.2026)", dc.iso(new Date(2026, 8, 11)), "2026-09-11");
equal("неделя от пятницы", dc.iso(dc.mondayOf(new Date(2026, 8, 11))), "2026-09-07");
equal("неделя от понедельника", dc.iso(dc.mondayOf(new Date(2026, 8, 7))), "2026-09-07");
equal("неделя от воскресенья", dc.iso(dc.mondayOf(new Date(2026, 8, 13))), "2026-09-07");
equal("разбор 16:30", dc.parseTimeMin("16:30"), 990);
equal("разбор 1630", dc.parseTimeMin("1630"), 990);
equal("разбор 8", dc.parseTimeMin("8"), 480);
equal("разбор мусора", dc.parseTimeMin("двадцать"), null);
equal("разбор 25:00", dc.parseTimeMin("25:00"), null);
equal("вывод 990", dc.fmtTimeMin(990), "16:30");
equal("вывод 0", dc.fmtTimeMin(0), "00:00");

// шаг поля времени — решение владельца: 10 минут
equal("шаг времени", dc.TIME_STEP, 10);
const snap = (value) => { const field = { value }; dc.snapTimeInput(field); return field.value; };
const roll = (value, delta) => { const field = { value }; dc.adjustTimeInput(field, delta); return field.value; };
equal("округление 16:23", snap("16:23"), "16:20");
equal("округление 16:27", snap("16:27"), "16:30");
equal("округление пустого", snap(""), "");
equal("колесо +10", roll("16:20", 10), "16:30");
equal("колесо −10", roll("16:20", -10), "16:10");
equal("колесо через полночь", roll("23:50", 10), "00:00");

// ── 2. обработчики и баннер ──────────────────────────────────────────
const handlers = {};
const container = {
  addEventListener: (name, fn) => { handlers[name] = fn; },
  querySelectorAll: () => [],
};
dc.timeFields(container, { selector: ".check-time" });
equal("обработчики на контейнере", Object.keys(handlers).sort(), ["blur", "keydown", "wheel"]);

const field = { matches: (sel) => sel === ".check-time", value: "16:07" };
handlers.blur({ target: field });
equal("уход из поля округляет до 10", field.value, "16:10");

const rolled = { matches: (sel) => sel === ".check-time", value: "16:20" };
let prevented = false;
handlers.wheel({ target: rolled, deltaY: -1, preventDefault: () => { prevented = true; } });
equal("колесо вверх +10", rolled.value, "16:30");
check("страница не прокручивается", () => (prevented ? null : "preventDefault не вызван"));

const alien = { matches: () => false, value: "16:07" };
handlers.blur({ target: alien });
equal("чужое поле не трогаем", alien.value, "16:07");

const banner = { hidden: true, className: "", textContent: "" };
dc.banner(banner, "Готово", "ok");
equal("баннер показан", [banner.textContent, banner.className, banner.hidden], ["Готово", "dc-banner ok", false]);
check("баннер уходит по таймеру", () => {
  if (!timers.length || timers[0].ms !== 6000) return "таймер " + JSON.stringify(timers);
  timers[0].fn();
  return banner.hidden ? null : "остался на экране";
});

netCalls.length = 0;
dc.apiFetch("/проверка");
equal("apiFetch идёт в сеть", netCalls, ["/проверка"]);

// ── 2b. выбор из списка с поиском (dc.combobox) ──────────────────────

/* Разметка страницы записей: select с сестрами вложен в подпись. Узлы
   подставные — проверяем поведение библиотеки, а не устройство браузера. */
function makeNurseSelect() {
  const label = comboNode("label");
  const holder = comboNode("div");
  const select = comboNode("select");
  select.required = true;
  select.value = "2";
  select.options = [
    { value: "", text: "— выберите сестру —", disabled: true },
    { value: "1", text: "Сидорова Анна Петровна", disabled: false },
    { value: "2", text: "Волкова Вера Сергеевна", disabled: false },
  ];
  select.parentNode = label;
  select.closest = (sel) => (sel === "label" ? label : null);
  label.parentNode = holder;
  return { label, holder, select };
}

const valuesIn = (html) => Array.from(html.matchAll(/data-value="([^"]*)"/g), (m) => m[1]);

const { label, holder, select } = makeNurseSelect();
dc.combobox(select);
const input = label.children[0] && label.children[0].children[0];
const list = holder.children[0];

check("комбобокс: поле в подписи, список рядом с ней", () => {
  if (!input || !list) {
    return "узлы не созданы: label=" + label.children.length + ", holder=" + holder.children.length;
  }
  if (label.children[0].className.indexOf("dc-combo") < 0) return "обёртка " + label.children[0].className;
  return list.className === "dc-combo-list" ? null : "список " + list.className;
});

equal("комбобокс: поле показывает выбранную сестру", input.value, "Волкова Вера Сергеевна");
equal("комбобокс: подсказка — из пункта «не выбрано»", input.placeholder, "— выберите сестру —");
equal("комбобокс: обязательность переехала на видимое поле", [input.required, select.required], [true, false]);
check("комбобокс: нативный select скрыт, но остался в форме", () =>
  (select.className.indexOf("dc-combo-native") >= 0 ? null : "классы: " + select.className));

input.handlers.focus();
equal("комбобокс: на фокусе — весь список сестёр", valuesIn(list.innerHTML), ["1", "2"]);

input.value = "сидор";
input.handlers.input();
equal("комбобокс: фильтр по подстроке без учёта регистра", valuesIn(list.innerHTML), ["1"]);

input.value = "михайлов";
input.handlers.input();
check("комбобокс: ничего не найдено — подсказка, а не пустой список", () =>
  (/Никого не найдено/.test(list.innerHTML) ? null : list.innerHTML));

let mousedownPrevented = false;
input.value = "сидор";
input.handlers.input();
list.handlers.mousedown({ preventDefault() { mousedownPrevented = true; } });
check("комбобокс: mousedown гасится, иначе список закроется до клика", () =>
  (mousedownPrevented ? null : "preventDefault не вызван"));

list.handlers.click({ target: { closest: () => ({ getAttribute: () => "1" }) } });
equal("комбобокс: клик выбирает сестру", [select.value, input.value, list.hidden],
      ["1", "Сидорова Анна Петровна", true]);
equal("комбобокс: выбор шлёт change — на нём держится сохранение", select.events.map((e) => e.type),
      ["change"]);

input.value = "не то";
input.handlers.blur();
equal("комбобокс: уход с набранным мусором возвращает выбранную сестру",
      input.value, "Сидорова Анна Петровна");

input.value = "волк";
input.handlers.input();
list.querySelector = () => ({ getAttribute: () => valuesIn(list.innerHTML)[0] });
let enterPrevented = false;
input.handlers.keydown({ key: "Enter", preventDefault() { enterPrevented = true; } });
equal("комбобокс: Enter выбирает первую найденную", [select.value, enterPrevented], ["2", true]);

input.value = "не то";
input.handlers.keydown({ key: "Escape", preventDefault() {} });
equal("комбобокс: Escape сбрасывает набранное", [input.value, list.hidden],
      ["Волкова Вера Сергеевна", true]);

// ── 3. загрузка сценариев в подставном DOM ───────────────────────────
function fakeElement(id) {
  return {
    id, hidden: false, innerHTML: "", textContent: "", value: "", className: "",
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    style: {}, dataset: {}, addEventListener() {}, removeEventListener() {},
    querySelector: () => fakeElement(id + "-child"), querySelectorAll: () => [],
    appendChild() {}, setAttribute() {}, getAttribute: () => "", hasAttribute: () => false,
    closest: () => null, matches: () => false, focus() {},
  };
}

/* Прогоняет файл страницы в песочнице. В браузере window — глобальный объект,
   а в песочнице нет: dc и fetch передаём явно, иначе файл их «не увидит». */
function runPageFile(file, presentIds) {
  const elements = {};
  const document = {
    getElementById: (id) => {
      if (!presentIds.includes(id)) return null;
      return (elements[id] = elements[id] || fakeElement(id));
    },
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener() {}, createElement: (tag) => fakeElement(tag), body: fakeElement("body"),
  };
  const sandbox = { dc, location: { href: "" } };
  netCalls.length = 0;  // запрос «зависает»: дальше инициализации файл не пойдёт
  new Function("window", "document", "fetch", "location", "dc", "setTimeout", "clearTimeout", read(file))(
    sandbox, document, recordingFetch, sandbox.location, dc, setTimeout, clearTimeout
  );
  return netCalls.slice();
}

check("needs.js молча выходит на чужой странице", () => {
  runPageFile(join(STATIC, "needs.js"), []);
});
check("needs-analytics.js молча выходит без своей формы", () => {
  runPageFile(join(STATIC, "needs-analytics.js"), []);
});
check("needs.js стартует на странице заявок", () => {
  const calls = runPageFile(join(STATIC, "needs.js"), ["needs-app", "needs-banner"]);
  return calls.length ? null : "не дошёл до запроса к API";
});
check("analytics стартует по своей форме", () => {
  const ids = ["analytics-form", "analytics-banner", "analytics-from", "analytics-to",
               "analytics-base", "analytics-point", "analytics-group", "analytics-section",
               "analytics-show", "analytics-xlsx", "analytics-result"];
  const calls = runPageFile(join(STATIC, "needs-analytics.js"), ids);
  return calls.includes("/needs/api/catalog") ? null : "каталог не запрошен: " + JSON.stringify(calls);
});
check("на странице заявок уживаются оба сценария", () => {
  const ids = ["needs-app", "needs-banner", "needs-board", "needs-points", "analytics-form",
               "analytics-banner", "analytics-from", "analytics-to", "analytics-base",
               "analytics-point", "analytics-group", "analytics-section", "analytics-show",
               "analytics-xlsx", "analytics-result"];
  const calls = runPageFile(join(STATIC, "needs.js"), ids);
  runPageFile(join(STATIC, "needs-analytics.js"), ids);
  return calls.length ? null : "форма заявок не стартовала";
});

// ── 4. структура ─────────────────────────────────────────────────────
const pageFiles = readdirSync(STATIC).filter((name) => name.endsWith(".js"));

check("в файлах страниц нет своих копий общего", () => {
  const copies = [];
  for (const name of pageFiles) {
    const text = read(join(STATIC, name));
    for (const pattern of [/function\s+esc\s*\(/, /function\s+apiFetch\s*\(/,
                           /window\.(needs|duty|compendium)\s*=/]) {
      if (pattern.test(text)) copies.push(name + " — " + pattern);
    }
    // обёртка над dc.banner допустима, своя реализация баннера — нет
    const banner = text.match(/function\s+showBanner\s*\([^)]*\)\s*\{((?:[^{}]|\{[^{}]*\})*)\}/);
    if (banner && !/dc\.banner\s*\(/.test(banner[1])) copies.push(name + " — свой баннер");
  }
  return copies.length ? copies.join("; ") : null;
});

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) walk(path, out);
    else if (name.endsWith(".html")) out.push(path);
  }
  return out;
}

const templates = walk(SOURCE);
check("шаблоны найдены", () => (templates.length ? null : "ни одного .html под src/docapp"));

check("библиотека подключается в base.html раньше страничных сценариев", () => {
  const base = join(SOURCE, "web", "templates", "base.html");
  const head = read(base);
  return /lib\/core\.js/.test(head) ? null : "в base.html нет /static/lib/core.js";
});

check("страницы со сценарием наследуют base.html (а библиотека — в нём)", () => {
  const guilty = [];
  for (const path of templates) {
    const text = read(path);
    const own = text.search(/\/static\/(needs|needs-analytics|duty|compendium|compendium-article)\.js/);
    if (own < 0) continue;
    const extendsBase = /\{%\s*extends\s+["'][^"']*base\.html["']\s*%\}/.test(text);
    if (!extendsBase && text.indexOf("/static/lib/core.js") < 0) guilty.push(relative(ROOT, path));
  }
  return guilty.length ? guilty.join(", ") : null;
});

check("класс баннера один — dc-banner", () => {
  const guilty = [];
  for (const path of templates) {
    const text = read(path);
    if (/class="(needs|duty|analytics)-banner/.test(text)) guilty.push(relative(ROOT, path));
  }
  return guilty.length ? guilty.join(", ") : null;
});

check("баннер стилизован в style.css", () => {
  const css = read(join(STATIC, "style.css"));
  return /\.dc-banner\b/.test(css) ? null : "нет правила .dc-banner";
});

check("выбор сестры с поиском подключён в шаблонах записей", () => {
  const guilty = [];
  for (const name of ["index.html", "edit.html"]) {
    const path = join(SOURCE, "records", "templates", name);
    if (!/dc\.combobox\s*\(/.test(read(path))) guilty.push(name);
  }
  return guilty.length ? "нет вызова dc.combobox: " + guilty.join(", ") : null;
});

check("комбобокс стилизован в style.css", () => {
  const css = read(join(STATIC, "style.css"));
  return /\.dc-combo-item\b/.test(css) ? null : "нет правила .dc-combo-item";
});

check("service worker кэширует статику правилом, а не списком", () => {
  const sw = read(join(STATIC, "sw.js"));
  return /startsWith\(\s*["']\/static\//.test(sw) ? null : "нет правила по /static/";
});

/* ── 5. вызовы помощников без объявления ───────────────────────────── */

/* Браузерные глобальные и встроенные объекты — их можно вызывать «сами по себе». */
const BROWSER_GLOBALS = new Set([
  "console", "fetch", "setTimeout", "clearTimeout", "setInterval", "clearInterval",
  "requestAnimationFrame", "cancelAnimationFrame", "queueMicrotask", "structuredClone",
  "getComputedStyle", "matchMedia", "parseInt", "parseFloat", "isNaN", "isFinite",
  "encodeURIComponent", "decodeURIComponent", "alert", "confirm", "prompt", "atob", "btoa",
  "JSON", "Object", "Array", "Math", "Number", "String", "Boolean", "Date", "RegExp",
  "Error", "TypeError", "Promise", "Set", "Map", "WeakMap", "WeakSet", "FormData",
  "URLSearchParams", "URL", "Blob", "File", "FileReader", "Image", "Event", "CustomEvent",
  "MutationObserver", "IntersectionObserver", "Intl", "Symbol", "BigInt",
  "Uint8Array", "Int32Array", "Float64Array",
]);

/* Ключевые слова стоят рядом со скобкой и попадают в тот же шаблон. */
const KEYWORDS = new Set([
  "if", "for", "while", "switch", "catch", "return", "typeof", "function", "new",
  "delete", "void", "in", "of", "do", "else", "case", "await", "yield",
]);

/* В шаблонах сначала убираем Jinja: там свои вызовы (url_for, фильтры). */
const withoutJinja = (html) =>
  html.replace(/\{\{[\s\S]*?\}\}/g, " ").replace(/\{[\s\S]*?%\}/g, " ");

/* Имена, объявленные в тексте: function f(), var f =, f: function, {a, b: c} = dc,
   плюс параметры функций и стрелок — их тоже можно вызывать. */
function declaredIn(text) {
  const names = new Set();
  for (const m of text.matchAll(/\bfunction\s+([A-Za-z_$][\w$]*)/g)) names.add(m[1]);
  for (const m of text.matchAll(/\b(?:var|let|const)\s+([A-Za-z_$][\w$]*)\s*=/g)) names.add(m[1]);
  for (const m of text.matchAll(/\b([A-Za-z_$][\w$]*)\s*:\s*function\b/g)) names.add(m[1]);
  for (const m of text.matchAll(/\b(?:var|let|const)\s*\{([^}]*)\}/g)) {
    for (const part of m[1].split(",")) {
      const tail = (part.includes(":") ? part.split(":")[1] : part).trim();
      if (/^[A-Za-z_$][\w$]*$/.test(tail)) names.add(tail);
    }
  }
  const addParams = (list) => {
    for (const part of list.split(",")) {
      const name = part.trim().replace(/^\.\.\./, "").split(/[=:{\s]/)[0];
      if (/^[A-Za-z_$][\w$]*$/.test(name)) names.add(name);
    }
  };
  for (const m of text.matchAll(/\bfunction\b\s*[A-Za-z_$\w]*\s*\(([^)]*)\)/g)) addParams(m[1]);
  for (const m of text.matchAll(/\(([^()]*)\)\s*=>/g)) addParams(m[1]);
  for (const m of text.matchAll(/(?:^|[^\w$.])([A-Za-z_$][\w$]*)\s*=>/g)) names.add(m[1]);
  return names;
}

/* Только код: убираем комментарии и строковые литералы. В комментариях и
   текстах подсказок тоже встречается «слово (» — это не вызов. */
function codeOnly(text) {
  let out = "";
  let i = 0;
  const n = text.length;
  while (i < n) {
    const ch = text[i];
    const next = text[i + 1];
    if (ch === "/" && next === "*") {
      const end = text.indexOf("*/", i + 2);
      i = end < 0 ? n : end + 2;
      out += " ";
      continue;
    }
    if (ch === "/" && next === "/") {
      const end = text.indexOf("\n", i);
      i = end < 0 ? n : end + 1;
      out += " ";
      continue;
    }
    if (ch === '"' || ch === "'" || ch === "`") {
      const quote = ch;
      i += 1;
      while (i < n) {
        if (text[i] === "\\") { i += 2; continue; }
        if (text[i] === quote) { i += 1; break; }
        i += 1;
      }
      out += " ";
      continue;
    }
    out += ch;
    i += 1;
  }
  return out;
}

/* Имена, вызываемые «сами по себе»: f( — но не obj.f( и не new Foo(. */
function calledIn(text) {
  const names = new Set();
  const re = /([A-Za-z_$][\w$]*)\s*\(/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    const before = text.slice(0, m.index);
    if (/[.\w$]$/.test(before)) continue;
    if (/\bnew\s+$/.test(before)) continue;
    names.add(m[1]);
  }
  return names;
}

function walkFiles(dir, ext, out = []) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) walkFiles(path, ext, out);
    else if (entry.name.endsWith(ext)) out.push(path);
  }
  return out;
}

check("помощники вызываются там, где объявлены (файл или браузер), а dc.* — только существующие", () => {
  const core = read(join(STATIC, "lib", "core.js"));
  const block = core.match(/window\.dc\s*=\s*\{([\s\S]*?)\n\s*\};/);
  const inLibrary = new Set(block ? [...block[1].matchAll(/([A-Za-z_$][\w$]*)\s*:/g)].map((m) => m[1]) : []);

  const pieces = walkFiles(STATIC, ".js").map((path) => [path, codeOnly(read(path))]);
  for (const path of walkFiles(SOURCE, ".html")) {
    const html = read(path);
    for (const m of html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)) {
      const body = codeOnly(withoutJinja(m[1])).trim();
      if (body) pieces.push([path, body]);
    }
  }

  const guilty = [];
  for (const [path, text] of pieces) {
    const where = relative(ROOT, path);
    const here = declaredIn(text);
    for (const name of calledIn(text)) {
      if (here.has(name) || BROWSER_GLOBALS.has(name) || KEYWORDS.has(name)) continue;
      guilty.push(where + ": " + name + "()");
    }
    // Общее берётся из dc: имя после dc. обязано быть в библиотеке.
    for (const m of text.matchAll(/\bdc\.([A-Za-z_$][\w$]*)/g)) {
      if (!inLibrary.has(m[1])) guilty.push(where + ": dc." + m[1] + " — нет в библиотеке");
    }
  }
  return guilty.length ? "без объявления: " + guilty.join(", ") : null;
});

console.log(`\n${checks - failed} из ${checks} проверок прошло`);
if (failed) {
  console.log(`ПРОВАЛЕНО: ${failed}`);
  process.exit(1);
}

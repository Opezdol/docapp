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
 *      раньше своего сценария, классы баннеров приведены к одному.
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
const window = { location: { href: "" } };
const timers = [];
/* Один счётчик запросов на всю проверку: dc.apiFetch — замыкание библиотеки,
   и запрос страницы придёт именно в этот fetch, а не в отдельный для файла. */
const netCalls = [];
const recordingFetch = (url) => { netCalls.push(url); return new Promise(() => {}); };
new Function("window", "fetch", "setTimeout", "clearTimeout", read(join(STATIC, "lib", "core.js")))(
  window,
  recordingFetch,
  (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
  () => {}
);
const dc = window.dc;

check("библиотека положила объект dc", () => {
  if (!dc || typeof dc !== "object") return "dc не создан";
});
for (const name of ["esc", "apiFetch", "banner", "iso", "mondayOf", "parseTimeMin",
                    "fmtTimeMin", "timeFields", "snapTimeFields"]) {
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

check("service worker кэширует статику правилом, а не списком", () => {
  const sw = read(join(STATIC, "sw.js"));
  return /startsWith\(\s*["']\/static\//.test(sw) ? null : "нет правила по /static/";
});

console.log(`\n${checks - failed} из ${checks} проверок прошло`);
if (failed) {
  console.log(`ПРОВАЛЕНО: ${failed}`);
  process.exit(1);
}

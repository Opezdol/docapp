/* Service worker: статика — network-first (кэш только как офлайн-фолбэк),
   чтобы обновления JS/CSS/иконок доходили до телефонов без «жёсткого» сброса.
   HTML-страницы всегда по сети (серверный рендер).

   Кэшируется **любой** файл из /static/, а не список из четырёх имён: файлы
   модулей и общая библиотека меняются, и жёсткий список от них отстаёт —
   в офлайне страница оставалась без JS и CSS. */

const CACHE = "docapp-v3";

/* Оболочка: то, что нужно уметь показать до первого успешного запроса. */
const SHELL = [
  "/static/style.css",
  "/static/lib/core.js",
  "/static/manifest.json",
  "/static/icon-192.png",
  "/static/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.addAll(SHELL).catch(() => null))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.origin !== location.origin) return;
  if (event.request.method !== "GET") return;

  // Только статика приложения; страницы и API — всегда по сети.
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      // network-first: свежая версия всегда предпочтительна; при офлайне —
      // последняя закешированная.
      fetch(event.request)
        .then((resp) => {
          const copy = resp.clone();
          caches.open(CACHE).then((cache) => cache.put(event.request, copy));
          return resp;
        })
        .catch(() => caches.match(event.request))
    );
  }
});

/* Service worker: статика — network-first (кэш только как офлайн-фолбэк),
   чтобы обновления JS/CSS/иконок доходили до телефонов без «жёсткого» сброса.
   HTML-страницы всегда по сети (серверный рендер). */

const CACHE = "docapp-v2";
const ASSETS = [
  "/static/style.css",
  "/static/manifest.json",
  "/static/icon-192.png",
  "/static/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.addAll(ASSETS).catch(() => null))
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

  // Только ассеты приложения; остальное (страницы, API) — по сети.
  if (ASSETS.includes(url.pathname)) {
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

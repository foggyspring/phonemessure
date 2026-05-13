/* phonemessure service worker — minimal app-shell cache for offline reload. */
const VERSION = "pm-v1";
const SHELL = [
  "/",
  "/static/style.css",
  "/static/app.js",
  "/static/measure.js",
  "/static/calibrate.js",
  "/static/history.js",
  "/static/manifest.webmanifest",
  "/static/icons/icon.svg",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  // Never intercept the camera-bearing main HTML's API calls or the video stream.
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.pathname.startsWith("/api/")) return;

  // Cache-first for app shell, network-first for everything else.
  if (SHELL.includes(url.pathname) || url.pathname.startsWith("/static/")) {
    e.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((res) => {
        const copy = res.clone();
        caches.open(VERSION).then((c) => c.put(req, copy)).catch(() => {});
        return res;
      }).catch(() => hit))
    );
  }
});

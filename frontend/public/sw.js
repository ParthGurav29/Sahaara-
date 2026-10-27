/*
 * Sahaara PWA service worker.
 *
 * This is deliberately a small app-shell cache for installability only.
 * It never caches /api, /auth, /stream, or /ws requests, so caregiver data
 * and event streams always come from the network.
 *
 * Push and notification handlers are added in later M5 steps.
 */

const CACHE_NAME = "sahaara-shell-v1";
const SHELL_ASSETS = [
  "/",
  "/manifest.webmanifest",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "/icons/maskable-512.png",
];

const BYPASS_PREFIXES = ["/api", "/auth", "/stream", "/ws"];

function shouldBypass(pathname) {
  return BYPASS_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE_NAME);

      // Cache each asset on its own. `cache.addAll` rejects the entire
      // install if one asset is unavailable (server down, missing icon),
      // which leaves the worker silently unactivated. One bad asset must
      // not stop the shell from installing.
      const results = await Promise.allSettled(
        SHELL_ASSETS.map((asset) => cache.add(asset)),
      );
      const failed = results.filter((result) => result.status === "rejected");
      if (failed.length > 0) {
        console.warn(
          `Sahaara shell: ${failed.length}/${SHELL_ASSETS.length} assets failed to cache`,
        );
      }

      await self.skipWaiting();
    })(),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(
        keys
          .filter((key) => key.startsWith("sahaara-shell-") && key !== CACHE_NAME)
          .map((key) => caches.delete(key)),
      );
      await self.clients.claim();
    })(),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (shouldBypass(url.pathname)) return;

  if (request.mode === "navigate") {
    event.respondWith(
      (async () => {
        try {
          const response = await fetch(request);
          const cache = await caches.open(CACHE_NAME);
          await cache.put("/", response.clone());
          return response;
        } catch {
          return (await caches.match("/")) || Response.error();
        }
      })(),
    );
    return;
  }

  if (url.pathname.startsWith("/icons/") || url.pathname === "/manifest.webmanifest") {
    event.respondWith(
      (async () => {
        const cached = await caches.match(request);
        if (cached) return cached;

        const response = await fetch(request);
        const cache = await caches.open(CACHE_NAME);
        await cache.put(request, response.clone());
        return response;
      })(),
    );
  }
});

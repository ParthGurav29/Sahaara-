/*
 * Sahaara PWA service worker.
 *
 * This is deliberately a small app-shell cache for installability only.
 * It never caches /api, /auth, /stream, or /ws requests, so caregiver data
 * and event streams always come from the network.
 *
 * It also renders push notifications and handles a click on them (M5 Step 4),
 * but nothing in this file decides *whether* to notify: the server has no send
 * path yet. No delivery can be claimed.
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

/* ---------------------------------------------------------------------------
 * Push (M5 Step 4) — display only.
 *
 * The body is a fixed constant and the incoming payload is deliberately
 * ignored, so no symptom wording, quoted speech, or sender-supplied text can
 * ever reach a notification. `userVisibleOnly` is honoured: every push shows a
 * notification. Whether to notify at all is a server decision (Step 5).
 * ------------------------------------------------------------------------- */
const NOTIFICATION_TITLE = "Sahaara";
const NOTIFICATION_BODY = "Asha needs a check-in";
const NOTIFICATION_ICON = "/icons/icon-192.png";
const DASHBOARD_URL = "/";

self.addEventListener("push", (event) => {
  event.waitUntil(
    self.registration.showNotification(NOTIFICATION_TITLE, {
      body: NOTIFICATION_BODY,
      icon: NOTIFICATION_ICON,
      badge: NOTIFICATION_ICON,
      tag: "sahaara-checkin",
      data: { url: DASHBOARD_URL },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();

  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      });

      const existing = windows.find((client) => {
        try {
          return new URL(client.url).origin === self.location.origin;
        } catch {
          return false;
        }
      });

      if (existing && typeof existing.focus === "function") {
        await existing.focus();
        return;
      }

      await self.clients.openWindow(DASHBOARD_URL);
    })(),
  );
});

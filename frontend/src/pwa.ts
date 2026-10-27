import { apiFetch } from "./api";
import { decidePwaAction, parseFlag } from "./pwaGate";

const MANIFEST_LINK_ID = "sahaara-manifest";
const CACHE_PREFIX = "sahaara-shell-";
const DISABLE_QUERY = "pwa";
const DISABLE_RELOAD_GUARD = "sahaara-pwa-disabled-reload";
const OPT_OUT_KEY = "sahaara-pwa-opt-out";

/**
 * Build-time gate (`VITE_PUSH_ENABLED`). Default OFF: a normal build never
 * registers a service worker and renders no push surface at all.
 */
const BUILD_ENABLED = parseFlag(
  (import.meta as any).env?.VITE_PUSH_ENABLED as string | undefined,
);

function ensureManifestLink(): void {
  if (document.getElementById(MANIFEST_LINK_ID)) return;

  const link = document.createElement("link");
  link.id = MANIFEST_LINK_ID;
  link.rel = "manifest";
  link.href = "/manifest.webmanifest";
  document.head.appendChild(link);
}

function removeManifestLink(): void {
  document.getElementById(MANIFEST_LINK_ID)?.remove();
}

async function clearSahaaraCaches(): Promise<void> {
  if (!("caches" in window)) return;

  const keys = await caches.keys();
  await Promise.all(
    keys
      .filter((key) => key.startsWith(CACHE_PREFIX))
      .map((key) => caches.delete(key)),
  );
}

/** Returns true when a Sahaara worker was actually unregistered. */
async function unregisterSahaaraWorkers(): Promise<boolean> {
  if (!("serviceWorker" in navigator)) return false;

  const registrations = await navigator.serviceWorker.getRegistrations();
  let removed = false;

  await Promise.all(
    registrations.map(async (registration) => {
      const workers = [
        registration.active,
        registration.installing,
        registration.waiting,
      ].filter((worker): worker is ServiceWorker => Boolean(worker));

      if (workers.some((worker) => worker.scriptURL.endsWith("/sw.js"))) {
        removed = (await registration.unregister()) || removed;
      }
    }),
  );

  return removed;
}

/** Returns true when a Sahaara worker is currently registered on this origin. */
async function hasSahaaraWorker(): Promise<boolean> {
  if (!("serviceWorker" in navigator)) return false;

  const registrations = await navigator.serviceWorker.getRegistrations();
  return registrations.some((registration) =>
    [registration.active, registration.installing, registration.waiting].some(
      (worker) => worker?.scriptURL.endsWith("/sw.js"),
    ),
  );
}

/**
 * Remove the Sahaara shell worker and its cache.
 *
 * Returns true when a worker was unregistered, so the caller can decide
 * whether a single reload is needed to drop the old controller.
 */
export async function disablePwaShell(): Promise<boolean> {
  removeManifestLink();
  const unregistered = await unregisterSahaaraWorkers();
  await clearSahaaraCaches();
  return unregistered;
}

async function readServerFlag(): Promise<boolean | null> {
  try {
    const response = await apiFetch("/config");
    if (!response.ok) return null;

    const data = (await response.json()) as { push_enabled?: unknown };
    return data.push_enabled === true;
  } catch {
    return null;
  }
}

/**
 * Reload once after tearing the shell down, but only when something was
 * actually removed. Guarded so a flag flip cannot cause a reload loop.
 */
function reloadAfterDisable(unregistered: boolean): void {
  const hadController = Boolean(navigator.serviceWorker?.controller);
  if (!unregistered && !hadController) return;
  if (sessionStorage.getItem(DISABLE_RELOAD_GUARD) === "1") return;

  sessionStorage.setItem(DISABLE_RELOAD_GUARD, "1");
  window.location.reload();
}

/**
 * Initialize the optional PWA shell.
 *
 * With `VITE_PUSH_ENABLED` OFF (the default) nothing registers, no manifest
 * link is added and `GET /config` is never called. With it ON, the backend's
 * `push_enabled` decides: ON enables the shell, OFF tears it down, and an
 * unreachable backend leaves the installed app alone.
 */
export async function initPwa(): Promise<void> {
  if (typeof window === "undefined" || typeof navigator === "undefined") return;

  const url = new URL(window.location.href);
  const pwaQuery = url.searchParams.get(DISABLE_QUERY);

  if (pwaQuery === "off") {
    localStorage.setItem(OPT_OUT_KEY, "1");
    const unregistered = await disablePwaShell();
    url.searchParams.delete(DISABLE_QUERY);
    window.history.replaceState(null, "", url);
    reloadAfterDisable(unregistered);
    return;
  }

  if (pwaQuery === "on") {
    localStorage.removeItem(OPT_OUT_KEY);
    url.searchParams.delete(DISABLE_QUERY);
    window.history.replaceState(null, "", url);
  }

  if (localStorage.getItem(OPT_OUT_KEY) === "1") {
    reloadAfterDisable(await disablePwaShell());
    return;
  }

  // Do not ask the backend at all when this build is not allowed to install.
  const serverEnabled = BUILD_ENABLED ? await readServerFlag() : null;

  const action = decidePwaAction({
    buildEnabled: BUILD_ENABLED,
    serverEnabled,
  });

  if (action === "keep") {
    // Backend unreachable: leave the installed app exactly as it was. That
    // includes the manifest link, which a previous enabling load added.
    if (await hasSahaaraWorker()) ensureManifestLink();
    return;
  }

  if (action === "disable") {
    reloadAfterDisable(await disablePwaShell());
    return;
  }

  sessionStorage.removeItem(DISABLE_RELOAD_GUARD);
  ensureManifestLink();

  if (!("serviceWorker" in navigator)) return;

  try {
    await navigator.serviceWorker.register("/sw.js", { scope: "/" });
  } catch (error) {
    console.warn("Sahaara PWA service worker registration failed", error);
  }
}

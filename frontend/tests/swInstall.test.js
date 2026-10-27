import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

const SW_SOURCE = readFileSync(new URL("../public/sw.js", import.meta.url), "utf8");

/**
 * Load `public/sw.js` into a fake service-worker environment.
 *
 * `failingAssets` is the set of URLs whose `cache.add()` rejects, which is
 * what an offline or half-deployed server looks like at install time.
 */
function loadWorker({ failingAssets = new Set() } = {}) {
  const handlers = new Map();
  const stored = [];
  const warnings = [];
  let skippedWaiting = false;
  let claimed = false;

  const cache = {
    stored,
    async add(url) {
      if (failingAssets.has(url)) throw new Error(`unavailable: ${url}`);
      stored.push(url);
    },
    async put(key) {
      stored.push(String(key));
    },
    async match() {
      return undefined;
    },
  };

  const self = {
    location: { origin: "http://127.0.0.1:5173" },
    clients: {
      async claim() {
        claimed = true;
      },
    },
    async skipWaiting() {
      skippedWaiting = true;
    },
    addEventListener(type, handler) {
      handlers.set(type, handler);
    },
  };

  const caches = {
    async open() {
      return cache;
    },
    async keys() {
      return [];
    },
    async delete() {
      return true;
    },
    async match() {
      return undefined;
    },
    async addAll() {
      throw new Error("cache.addAll must not be used for the shell install");
    },
  };

  const consoleStub = {
    warn(...args) {
      warnings.push(args.join(" "));
    },
    log() {},
    error() {},
  };

  runInNewContext(SW_SOURCE, {
    self,
    caches,
    console: consoleStub,
    URL,
    Promise,
    Response: { error: () => ({ error: true }) },
    fetch: async () => ({ ok: true, clone: () => ({}) }),
  });

  /** Run a captured event handler and wait for its work to finish. */
  async function dispatchInstall() {
    let pending;
    handlers.get("install")({
      waitUntil(promise) {
        pending = promise;
      },
    });
    await pending;
  }

  function dispatchFetch(request) {
    let responded = false;
    handlers.get("fetch")({
      request,
      respondWith() {
        responded = true;
      },
    });
    return responded;
  }

  return {
    dispatchInstall,
    dispatchFetch,
    stored,
    warnings,
    get skippedWaiting() {
      return skippedWaiting;
    },
    get claimed() {
      return claimed;
    },
  };
}

test("install completes when one shell asset is unavailable (defect 2)", async () => {
  const worker = loadWorker({
    failingAssets: new Set(["/icons/maskable-512.png"]),
  });

  await worker.dispatchInstall();

  assert.equal(worker.skippedWaiting, true, "worker must still activate");
  assert.equal(worker.warnings.length, 1, "failure must be reported, not silent");
  assert.match(worker.warnings[0], /1\/5 assets failed/);
  assert.equal(worker.stored.length, 4, "the other four assets still get cached");
  assert.ok(!worker.stored.includes("/icons/maskable-512.png"));
});

test("install caches the whole shell when every asset is available", async () => {
  const worker = loadWorker();

  await worker.dispatchInstall();

  assert.equal(worker.skippedWaiting, true);
  assert.equal(worker.warnings.length, 0);
  assert.deepEqual(worker.stored, [
    "/",
    "/manifest.webmanifest",
    "/icons/icon-192.png",
    "/icons/icon-512.png",
    "/icons/maskable-512.png",
  ]);
});

test("API, auth, stream and socket requests are never intercepted", async () => {
  const worker = loadWorker();
  await worker.dispatchInstall();

  for (const path of [
    "/api/alerts",
    "/api/stream/events",
    "/api/auth/me",
    "/auth/login",
    "/stream/events",
    "/ws/voice",
  ]) {
    const intercepted = worker.dispatchFetch({
      method: "GET",
      url: `http://127.0.0.1:5173${path}`,
      mode: "cors",
    });
    assert.equal(intercepted, false, `${path} must bypass the service worker`);
  }

  const navigation = worker.dispatchFetch({
    method: "GET",
    url: "http://127.0.0.1:5173/",
    mode: "navigate",
  });
  assert.equal(navigation, true, "navigations still get the shell fallback");
});

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

const SW_SOURCE = readFileSync(new URL("../public/sw.js", import.meta.url), "utf8");

const ORIGIN = "http://127.0.0.1:4180";

/** Anything that would betray a symptom, a quote, or sender-supplied text. */
const FORBIDDEN_IN_NOTIFICATION =
  /(pain|hurt|fell|fall|dizzy|chest|breath|hospital|emergency|bleed|weak|sick|fever|stroke|seizure|ambulance|doctor|diagnos)/i;

/**
 * Load `public/sw.js` into a fake service-worker scope.
 *
 * `windows` are the clients `matchAll()` returns for a notification click.
 */
function loadWorker({ windows = [] } = {}) {
  const handlers = new Map();
  const notifications = [];
  const focused = [];
  const opened = [];
  const warnings = [];

  const self = {
    location: { origin: ORIGIN },
    registration: {
      async showNotification(title, options) {
        notifications.push({ title, options });
      },
    },
    clients: {
      async claim() {},
      async matchAll() {
        return windows;
      },
      async openWindow(url) {
        opened.push(url);
        return null;
      },
    },
    async skipWaiting() {},
    addEventListener(type, handler) {
      handlers.set(type, handler);
    },
  };

  const context = {
    self,
    URL,
    console: {
      log() {},
      warn(message) {
        warnings.push(String(message));
      },
    },
    caches: {
      async open() {
        return {
          async add() {},
          async put() {},
          async match() {
            return undefined;
          },
          async keys() {
            return [];
          },
        };
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
    },
    Response: { error: () => ({}) },
    Promise,
  };

  runInNewContext(SW_SOURCE, context);

  return { handlers, notifications, focused, opened, warnings };
}

/** Dispatch a `push` event; returns after every waitUntil promise settles. */
async function dispatchPush(worker, data) {
  const waits = [];
  const event = {
    data:
      data === undefined
        ? null
        : {
            text: () => data,
            json: () => JSON.parse(data),
          },
    waitUntil(promise) {
      waits.push(promise);
    },
  };

  worker.handlers.get("push")(event);
  await Promise.all(waits);
}

/** Dispatch a `notificationclick` event; returns after it settles. */
async function dispatchClick(worker, notification = { close() {} }) {
  const waits = [];
  const event = {
    notification,
    waitUntil(promise) {
      waits.push(promise);
    },
  };

  worker.handlers.get("notificationclick")(event);
  await Promise.all(waits);
}

test("a push event always shows exactly one notification", async () => {
  const worker = loadWorker();
  await dispatchPush(worker);

  assert.equal(worker.notifications.length, 1, "one notification per push");
  assert.equal(worker.notifications[0].title, "Sahaara");
});

test("a valid alert_id gives each alert its own tag (Red is not replaced)", async () => {
  const worker = loadWorker();
  await dispatchPush(worker, JSON.stringify({ alert_id: "alert_1" }));
  await dispatchPush(worker, JSON.stringify({ alert_id: "alert_2" }));

  assert.equal(worker.notifications[0].options.tag, "sahaara-alert_1");
  assert.equal(worker.notifications[1].options.tag, "sahaara-alert_2");
  assert.notEqual(worker.notifications[0].options.tag, worker.notifications[1].options.tag);
});

test("an unsafe or missing alert_id falls back to the shared tag", async () => {
  const unsafe = loadWorker();
  await dispatchPush(unsafe, JSON.stringify({ alert_id: "../admin" }));
  assert.equal(unsafe.notifications[0].options.tag, "sahaara-checkin");

  const empty = loadWorker();
  await dispatchPush(empty, "");
  assert.equal(empty.notifications[0].options.tag, "sahaara-checkin");
});

test("the notification vibrates with a calm fixed pattern", async () => {
  const worker = loadWorker();
  await dispatchPush(worker);
  assert.deepEqual([...worker.notifications[0].options.vibrate], [120, 60, 120]);
});

test("the notification body is the fixed check-in sentence", async () => {
  const worker = loadWorker();
  await dispatchPush(worker);

  const { options } = worker.notifications[0];
  assert.equal(options.body, "Asha needs a check-in");
  assert.equal(options.tag, "sahaara-checkin");
  assert.equal(options.icon, "/icons/icon-192.png");
  assert.equal(options.badge, "/icons/icon-192.png");
});

test("the body carries no quotes and no symptom wording", async () => {
  const worker = loadWorker();
  await dispatchPush(worker);

  const body = worker.notifications[0].options.body;
  assert.doesNotMatch(body, /["'“”‘’]/, "no quoted speech in a notification");
  assert.doesNotMatch(body, FORBIDDEN_IN_NOTIFICATION, "no symptom wording");
});

test("a hostile payload never reaches the notification", async () => {
  const worker = loadWorker();
  await dispatchPush(
    worker,
    JSON.stringify({
      title: "Asha said \"I can't breathe\"",
      body: "chest pain, she fell — call an ambulance",
      quote: "I want to die",
    }),
  );

  const notification = worker.notifications[0];
  assert.equal(notification.options.body, "Asha needs a check-in");

  const serialized = JSON.stringify(notification);
  assert.doesNotMatch(serialized, /can't breathe|chest pain|ambulance|want to die/i);
  assert.doesNotMatch(notification.options.body, FORBIDDEN_IN_NOTIFICATION);
});

test("an empty or non-JSON payload still shows the notification", async () => {
  const empty = loadWorker();
  await dispatchPush(empty, "");
  assert.equal(empty.notifications.length, 1);
  assert.equal(empty.notifications[0].options.body, "Asha needs a check-in");

  const garbage = loadWorker();
  await dispatchPush(garbage, "not json at all");
  assert.equal(garbage.notifications.length, 1);
  assert.equal(garbage.notifications[0].options.body, "Asha needs a check-in");
});

test("the push handler extends the event lifetime", async () => {
  const worker = loadWorker();
  let extended = false;
  const event = {
    data: null,
    waitUntil() {
      extended = true;
    },
  };

  worker.handlers.get("push")(event);
  assert.equal(extended, true, "a push that returns without waitUntil can be killed");
});

test("a notification click closes it and focuses an existing window", async () => {
  let closed = false;
  const windows = [];
  const worker = loadWorker({ windows });

  windows.push({
    url: `${ORIGIN}/`,
    async focus() {
      worker.focused.push("called");
    },
  });

  await dispatchClick(worker, {
    close() {
      closed = true;
    },
  });

  assert.equal(closed, true, "the notification is dismissed on click");
  assert.deepEqual(worker.focused, ["called"], "the open tab is focused");
  assert.deepEqual(worker.opened, [], "no new window when one is already open");
});

test("a notification click opens the dashboard when no window exists", async () => {
  const worker = loadWorker({ windows: [] });
  await dispatchClick(worker);

  assert.deepEqual(worker.opened, ["/"], "opens the dashboard root");
});

test("a notification click ignores windows from another origin", async () => {
  const worker = loadWorker({
    windows: [
      {
        url: "https://example.com/somewhere",
        async focus() {
          worker.focused.push("wrong-window");
        },
      },
    ],
  });

  await dispatchClick(worker);

  assert.deepEqual(worker.focused, [], "never focuses a foreign tab");
  assert.deepEqual(worker.opened, ["/"]);
});

test("a payload alert_id becomes a deep link, never visible text", async () => {
  const worker = loadWorker();
  await dispatchPush(worker, JSON.stringify({ v: 1, type: "caregiver_alert", alert_id: "alert_12" }));

  const notification = worker.notifications[0];
  assert.equal(notification.options.body, "Asha needs a check-in");
  assert.equal(notification.options.data.url, "/?alert=alert_12");
  assert.doesNotMatch(JSON.stringify(notification), /alert_12.*alert_12.*alert_12/);
});

test("an unsafe alert_id is dropped and the tap goes to the dashboard", async () => {
  for (const id of ["../admin", "alert 1", "alert<script>", "x".repeat(80), ""]) {
    const worker = loadWorker();
    await dispatchPush(worker, JSON.stringify({ alert_id: id }));
    assert.equal(worker.notifications[0].options.data.url, "/", `id ${JSON.stringify(id)} must be ignored`);
  }
});

test("clicking an alert notification navigates the open window to that alert", async () => {
  const focused = [];
  const navigations = [];
  const worker = loadWorker({
    windows: [
      {
        url: `${ORIGIN}/`,
        async focus() {
          focused.push("called");
        },
        async navigate(target) {
          navigations.push(target);
        },
      },
    ],
  });

  await dispatchClick(worker, {
    close() {},
    data: { url: "/?alert=alert_12" },
  });

  assert.deepEqual(focused, ["called"]);
  assert.deepEqual(navigations, ["/?alert=alert_12"], "the SPA is routed to the alert");
  assert.deepEqual(worker.opened, [], "no second window");
});

test("a click with a foreign deep link falls back to the dashboard", async () => {
  const worker = loadWorker({
    windows: [
      {
        url: `${ORIGIN}/`,
        async focus() {
          worker.focused.push("called");
        },
      },
    ],
  });

  await dispatchClick(worker, {
    close() {},
    data: { url: "https://evil.example/?alert=alert_1" },
  });

  assert.deepEqual(worker.opened, [], "still focuses the app");
  assert.deepEqual(worker.focused, ["called"]);
});

test("an open window also gets the alert over postMessage", async () => {
  const messages = [];
  const worker = loadWorker({
    windows: [
      {
        url: `${ORIGIN}/`,
        async focus() {},
        async postMessage(message) {
          messages.push(message);
        },
      },
    ],
  });

  await dispatchClick(worker, {
    close() {},
    data: { url: "/?alert=alert_12" },
  });

  // The message object comes from the VM realm, so compare its fields.
  assert.equal(messages.length, 1);
  assert.equal(messages[0].type, "sahaara-open-alert");
  assert.equal(messages[0].url, "/?alert=alert_12");
});

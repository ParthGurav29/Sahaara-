import test from "node:test";
import assert from "node:assert/strict";

import {
  base64UrlToUint8Array,
  classifyAlertsState,
  isIos,
  isStandalone,
  pushSupport,
} from "../src/pushSupport.ts";

/**
 * Run `fn` with fake `window` / `navigator` globals, then restore them.
 * The module reads its globals at call time, so this is enough to exercise
 * every browser shape without a DOM.
 */
function withBrowser(
  options: {
    userAgent?: string;
    serviceWorker?: boolean;
    pushManager?: boolean;
    notification?: boolean;
    notificationPermission?: string;
    standalone?: boolean;
    maxTouchPoints?: number;
  } = {},
  fn: () => void,
) {
  const {
    userAgent = "Mozilla/5.0 (Linux; Android 17; motorola edge 70 fusion) Chrome/155.0.0.0",
    serviceWorker = true,
    pushManager = true,
    notification = true,
    notificationPermission = "default",
    standalone = false,
    maxTouchPoints = 0,
  } = options;

  const windowDescriptor = Object.getOwnPropertyDescriptor(globalThis, "window");
  const navigatorDescriptor = Object.getOwnPropertyDescriptor(globalThis, "navigator");

  const fakeWindow: any = {
    matchMedia: (query: string) => ({
      matches: query.includes("display-mode: standalone") ? standalone : false,
    }),
  };
  if (pushManager) fakeWindow.PushManager = function PushManager() {};
  if (notification) fakeWindow.Notification = { permission: notificationPermission };

  const fakeNavigator: any = { userAgent, maxTouchPoints };
  if (serviceWorker) fakeNavigator.serviceWorker = {};

  Object.defineProperty(globalThis, "window", {
    value: fakeWindow,
    configurable: true,
    writable: true,
  });
  Object.defineProperty(globalThis, "navigator", {
    value: fakeNavigator,
    configurable: true,
    writable: true,
  });

  try {
    fn();
  } finally {
    if (windowDescriptor) Object.defineProperty(globalThis, "window", windowDescriptor);
    else delete (globalThis as any).window;
    if (navigatorDescriptor) Object.defineProperty(globalThis, "navigator", navigatorDescriptor);
    else delete (globalThis as any).navigator;
  }
}

test("unsupported browsers are detected before anything else", () => {
  withBrowser({ serviceWorker: false }, () => {
    assert.equal(pushSupport(), "unsupported", "no service worker");
  });
  withBrowser({ pushManager: false }, () => {
    assert.equal(pushSupport(), "unsupported", "no PushManager");
  });
  withBrowser({ notification: false }, () => {
    assert.equal(pushSupport(), "unsupported", "no Notification");
  });
});

test("an Android browser is supported", () => {
  withBrowser({}, () => {
    assert.equal(pushSupport(), "supported");
    assert.equal(isIos(), false);
  });
});

test("iOS without installation is needs-install, never a prompt", () => {
  const iphone =
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Mobile/15E148 Safari/604.1";
  withBrowser({ userAgent: iphone }, () => {
    assert.equal(isIos(), true);
    assert.equal(isStandalone(), false);
    assert.equal(pushSupport(), "needs-install");
  });
});

test("iPadOS with touch points counts as iOS", () => {
  const ipadDesktopUA =
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15";
  withBrowser({ userAgent: ipadDesktopUA, maxTouchPoints: 5 }, () => {
    assert.equal(isIos(), true);
  });
  withBrowser({ userAgent: ipadDesktopUA, maxTouchPoints: 0 }, () => {
    assert.equal(isIos(), false);
  });
});

test("an installed iOS web app is supported", () => {
  const iphone = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_2 like Mac OS X) Mobile/15E148";
  withBrowser({ userAgent: iphone, standalone: true }, () => {
    assert.equal(isStandalone(), true);
    assert.equal(pushSupport(), "supported");
  });
});

test("state mapping: unsupported and needs-install pass through", () => {
  assert.equal(
    classifyAlertsState({ support: "unsupported", permission: "granted", hasSubscription: true }),
    "unsupported",
  );
  assert.equal(
    classifyAlertsState({ support: "needs-install", permission: "default", hasSubscription: false }),
    "needs-install",
  );
});

test("state mapping: denied wins over an existing subscription", () => {
  assert.equal(
    classifyAlertsState({ support: "supported", permission: "denied", hasSubscription: true }),
    "denied",
  );
});

test("state mapping: enabled vs available", () => {
  assert.equal(
    classifyAlertsState({ support: "supported", permission: "granted", hasSubscription: true }),
    "enabled",
  );
  assert.equal(
    classifyAlertsState({ support: "supported", permission: "granted", hasSubscription: false }),
    "available",
  );
  assert.equal(
    classifyAlertsState({ support: "supported", permission: "default", hasSubscription: false }),
    "available",
  );
});

test("base64url keys decode to an uncompressed P-256 point", () => {
  // The shape the backend's /config serves: 65 bytes, base64url, no padding.
  const key = "BKaRGy_1UO0X94axFulqH-NivSF9mVYRAj7NM_Ify70K7VqX8gOpTvixC9BFKxDGub8SoFlySHMJncnHkZawpgo";
  const bytes = base64UrlToUint8Array(key);

  assert.equal(key.length, 87);
  assert.equal(bytes.length, 65);
  assert.equal(bytes[0], 0x04, "uncompressed EC point");
});

test("base64url padding and URL-safe characters are handled", () => {
  assert.deepEqual(Array.from(base64UrlToUint8Array("AQID")), [1, 2, 3]);
  assert.deepEqual(Array.from(base64UrlToUint8Array("-_8")), [0xfb, 0xff]);
});

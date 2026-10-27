/**
 * Pure push-support helpers (M5 Step 4).
 *
 * No imports and no side effects on purpose: `tests/pushSupport.test.ts` runs
 * this file directly under Node's type stripping.
 */

export type AlertsState =
  | "available"
  | "enabled"
  | "denied"
  | "unsupported"
  | "needs-install"
  | "error";

export type PushSupport = "unsupported" | "needs-install" | "supported";

export function isIos(): boolean {
  if (typeof navigator === "undefined") return false;
  const ua = navigator.userAgent || "";
  if (/iphone|ipad|ipod/i.test(ua)) return true;
  // iPadOS reports a desktop Mac user agent.
  return /macintosh/i.test(ua) && (navigator.maxTouchPoints ?? 0) > 1;
}

export function isStandalone(): boolean {
  if (typeof window === "undefined") return false;
  const displayMode = window.matchMedia?.("(display-mode: standalone)")?.matches;
  return Boolean(displayMode) || (window.navigator as any)?.standalone === true;
}

/**
 * `needs-install` is the iOS state: Safari only exposes PushManager to a web app
 * added to the home screen. It is implemented and unit-tested, but M5 is
 * Android-only, so it is **not device-tested and not claimed**.
 */
export function pushSupport(): PushSupport {
  if (typeof window === "undefined" || typeof navigator === "undefined") {
    return "unsupported";
  }
  if (!("serviceWorker" in navigator)) return "unsupported";
  if (!("PushManager" in window)) return "unsupported";
  if (!("Notification" in window)) return "unsupported";
  if (isIos() && !isStandalone()) return "needs-install";
  return "supported";
}

/**
 * base64url -> Uint8Array, the shape `applicationServerKey` expects.
 *
 * The explicit `ArrayBuffer` (and the `Uint8Array<ArrayBuffer>` return type)
 * matter: `subscribe()` wants `ArrayBufferView<ArrayBuffer>`, and a plain
 * `Uint8Array` widens to `ArrayBufferLike` and fails the build.
 */
export function base64UrlToUint8Array(value: string): Uint8Array<ArrayBuffer> {
  const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized + "=".repeat((4 - (normalized.length % 4)) % 4);
  const binary = atob(padded);
  const bytes = new Uint8Array(new ArrayBuffer(binary.length));
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

/** Map the environment to one of the six UI states. */
export function classifyAlertsState(input: {
  support: PushSupport;
  permission: string;
  hasSubscription: boolean;
}): AlertsState {
  if (input.support !== "supported") return input.support;
  if (input.permission === "denied") return "denied";
  return input.hasSubscription ? "enabled" : "available";
}

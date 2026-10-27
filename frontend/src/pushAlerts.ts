import { apiFetch } from "./api";
import { parseFlag } from "./pwaGate";
import {
  base64UrlToUint8Array,
  classifyAlertsState,
  pushSupport,
  type AlertsState,
} from "./pushSupport";

/**
 * Web Push subscription logic for M5 Step 4.
 *
 * Two gates, both default OFF: `VITE_PUSH_ENABLED` (this build) and
 * `SAHAARA_PUSH_ENABLED` (the server, delivered via `GET /config`). With either
 * off, nothing renders and `Notification.requestPermission()` is never called.
 *
 * No delivery can be claimed from this module: it only registers a browser
 * subscription. Sending arrives in a later step.
 */

export type { AlertsState };

export const BUILD_ENABLED = parseFlag(
  (import.meta as any).env?.VITE_PUSH_ENABLED as string | undefined,
);

export interface PushConfig {
  enabled: boolean;
  publicKey: string | null;
}

/** Server gate + public key from the public `/config` endpoint. */
export async function readPushConfig(): Promise<PushConfig | null> {
  try {
    const response = await apiFetch("/config");
    if (!response.ok) return null;
    const data = (await response.json()) as {
      push_enabled?: unknown;
      vapid_public_key?: unknown;
    };
    return {
      enabled: data.push_enabled === true,
      publicKey:
        typeof data.vapid_public_key === "string" ? data.vapid_public_key : null,
    };
  } catch {
    return null;
  }
}

async function currentRegistration(): Promise<ServiceWorkerRegistration | null> {
  if (!("serviceWorker" in navigator)) return null;
  try {
    return (await navigator.serviceWorker.getRegistration("/")) ?? null;
  } catch {
    return null;
  }
}

/** Read the state without ever prompting. Safe to call on page load. */
export async function alertsState(): Promise<AlertsState> {
  const support = pushSupport();
  if (support !== "supported") return support;

  const registration = await currentRegistration();
  const subscription = await registration?.pushManager.getSubscription();
  return classifyAlertsState({
    support,
    permission: Notification.permission,
    hasSubscription: Boolean(subscription),
  });
}

/**
 * Turn alerts on. **Must be called from a user gesture** — that is the only
 * place `Notification.requestPermission()` runs.
 */
export async function enableAlerts(): Promise<AlertsState> {
  const support = pushSupport();
  if (support !== "supported") return support;

  try {
    const permission = await Notification.requestPermission();
    if (permission === "denied") return "denied";
    if (permission !== "granted") return "available";

    const config = await readPushConfig();
    if (!config?.enabled || !config.publicKey) return "error";

    const registration = await navigator.serviceWorker.ready;
    const existing = await registration.pushManager.getSubscription();
    const subscription =
      existing ??
      (await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: base64UrlToUint8Array(config.publicKey),
      }));

    const keys = subscription.toJSON().keys ?? {};
    const response = await apiFetch("/push/subscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        endpoint: subscription.endpoint,
        keys: { p256dh: keys.p256dh ?? "", auth: keys.auth ?? "" },
        user_agent: navigator.userAgent,
      }),
    });

    if (!response.ok) {
      // The server refused the subscription: don't leave the browser holding
      // one that nothing knows about.
      if (!existing) await subscription.unsubscribe().catch(() => false);
      return "error";
    }

    return "enabled";
  } catch {
    return "error";
  }
}

/** Turn alerts off: revoke on the server first, then in the browser. */
export async function disableAlerts(): Promise<AlertsState> {
  try {
    const registration = await currentRegistration();
    const subscription = await registration?.pushManager.getSubscription();
    if (!subscription) return "available";

    await apiFetch("/push/unsubscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ endpoint: subscription.endpoint }),
    });
    await subscription.unsubscribe();
    return "available";
  } catch {
    return "error";
  }
}

/**
 * Best-effort cleanup when the shell is torn down (`?pwa=off`, opt-out, or the
 * server gate flipping OFF). Never throws: a dead endpoint must not block the
 * unregister path.
 */
export async function revokeStoredSubscription(): Promise<void> {
  try {
    const registration = await currentRegistration();
    const subscription = await registration?.pushManager.getSubscription();
    if (!subscription) return;

    await apiFetch("/push/unsubscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ endpoint: subscription.endpoint }),
    });
  } catch {
    // A subscription that cannot be revoked here is cleaned up by the send
    // path's 404/410 handling in Step 5.
  }
}

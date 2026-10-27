/**
 * pushStatus.ts
 *
 * Honest one-line descriptions of a Web Push attempt, for the caregiver
 * dashboard (M5 Step 6).
 *
 * The app can only ever know that the push service **accepted** a message
 * (HTTP 2xx). It cannot know whether a phone woke for it, showed it, or was
 * read. Every label below says exactly that and nothing more, and
 * `tests/pushStatus.test.ts` forbids the words "delivered", "displayed",
 * "seen" and "read".
 *
 * Pure module (no React, no browser globals) so `node --test` can import it
 * directly, the same way `pushSupport.ts` is tested.
 */

export type PushTone = "neutral" | "muted" | "warn";

export interface PushAttempt {
  status?: string;
  reason?: string;
  sent?: number;
  failed?: number;
  revoked?: number;
  at?: number;
  at_iso?: string;
}

export interface PushDescription {
  label: string;
  tone: PushTone;
}

/** What the push service answered, phrased without claiming a phone showed it. */
export function describePushAttempt(
  push: PushAttempt | null | undefined,
): PushDescription | null {
  if (!push) return null;

  const status = (push.status || "").toLowerCase();
  const reason = (push.reason || "").toLowerCase();

  if (status === "sent") {
    const devices = typeof push.sent === "number" ? push.sent : 0;
    return {
      label:
        devices > 1
          ? `Phone alert accepted by the push service (${devices} devices)`
          : "Phone alert accepted by the push service",
      tone: "neutral",
    };
  }

  if (status === "skipped") {
    switch (reason) {
      case "cooldown":
        return {
          label: "Phone alert skipped — a recent alert was already sent",
          tone: "muted",
        };
      case "no_subscriptions":
        return { label: "No phone registered for alerts", tone: "muted" };
      case "dead_subscriptions":
        return {
          label: "Phone no longer registered — stale alerts cleaned up",
          tone: "muted",
        };
      case "gate_off":
        // The feature is off on the server; there is nothing to report.
        return null;
      default:
        return { label: "Phone alert skipped", tone: "muted" };
    }
  }

  if (status === "error") {
    if (reason === "no_vapid_key") {
      return {
        label: "Phone alert could not be sent — server not configured",
        tone: "warn",
      };
    }
    return { label: "Phone alert not accepted", tone: "warn" };
  }

  return { label: "Phone alert not accepted", tone: "warn" };
}

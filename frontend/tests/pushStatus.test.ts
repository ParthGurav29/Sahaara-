import test from "node:test";
import assert from "node:assert/strict";

import { describePushAttempt } from "../src/pushStatus.ts";

/** Words that would overclaim what a push service response means. */
const FORBIDDEN = /\b(delivered|delivery|displayed|seen|read|received by|viewed)\b/i;

test("a missing record renders nothing", () => {
  assert.equal(describePushAttempt(null), null);
  assert.equal(describePushAttempt(undefined), null);
});

test("sent says accepted, never delivered", () => {
  const result = describePushAttempt({ status: "sent", sent: 1 });
  assert.ok(result);
  assert.equal(result.label, "Phone alert accepted by the push service");
  assert.equal(result.tone, "neutral");
});

test("sent reports how many devices the service accepted for", () => {
  const result = describePushAttempt({ status: "sent", sent: 2 });
  assert.ok(result);
  assert.equal(result.label, "Phone alert accepted by the push service (2 devices)");
});

test("cooldown is explained without sounding like a failure", () => {
  const result = describePushAttempt({ status: "skipped", reason: "cooldown" });
  assert.deepEqual(result, {
    label: "Phone alert skipped — a recent alert was already sent",
    tone: "muted",
  });
});

test("no subscriptions is the honest 'no phone registered' line", () => {
  const result = describePushAttempt({ status: "skipped", reason: "no_subscriptions" });
  assert.deepEqual(result, { label: "No phone registered for alerts", tone: "muted" });
});

test("dead subscriptions says the phone was cleaned up", () => {
  const result = describePushAttempt({ status: "skipped", reason: "dead_subscriptions" });
  assert.deepEqual(result, {
    label: "Phone no longer registered — stale alerts cleaned up",
    tone: "muted",
  });
});

test("gate_off is silent", () => {
  assert.equal(describePushAttempt({ status: "skipped", reason: "gate_off" }), null);
});

test("missing VAPID config is a warning, not a delivery claim", () => {
  const result = describePushAttempt({ status: "error", reason: "no_vapid_key" });
  assert.deepEqual(result, {
    label: "Phone alert could not be sent — server not configured",
    tone: "warn",
  });
});

test("a failed send is reported plainly", () => {
  for (const reason of ["send_failed", "storage_error", ""]) {
    const result = describePushAttempt({ status: "error", reason, failed: 1 });
    assert.deepEqual(result, { label: "Phone alert not accepted", tone: "warn" });
  }
});

test("unknown or partial records fall back to the cautious label", () => {
  for (const push of [{}, { status: "" }, { status: "weird" }, { status: "SENT" }]) {
    const result = describePushAttempt(push);
    assert.ok(result);
    assert.ok(
      result!.label === "Phone alert not accepted" ||
        result!.label === "Phone alert accepted by the push service",
      result!.label,
    );
  }
});

test("status and reason matching is case-insensitive", () => {
  const result = describePushAttempt({ status: "SKIPPED", reason: "COOLDOWN" });
  assert.equal(result?.label, "Phone alert skipped — a recent alert was already sent");
});

test("no label ever overclaims what happened on the phone", () => {
  const cases: Array<Record<string, unknown> | null> = [
    null,
    { status: "sent", sent: 1 },
    { status: "sent", sent: 3 },
    { status: "skipped", reason: "cooldown" },
    { status: "skipped", reason: "no_subscriptions" },
    { status: "skipped", reason: "dead_subscriptions" },
    { status: "skipped", reason: "gate_off" },
    { status: "skipped", reason: "something_new" },
    { status: "error", reason: "no_vapid_key" },
    { status: "error", reason: "send_failed" },
    { status: "error", reason: "storage_error" },
  ];

  for (const push of cases) {
    const result = describePushAttempt(push);
    if (!result) continue;
    assert.doesNotMatch(result.label, FORBIDDEN, `overclaiming label: ${result.label}`);
  }
});

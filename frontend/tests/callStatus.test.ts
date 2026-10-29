/**
 * callStatus.test.ts
 *
 * Unit tests for callStatus.ts — wording table + forbidden words + null handling.
 */

import { describeCallStatus, describeNextContact, FORBIDDEN_WORDS } from "../src/callStatus.ts";

function assert(condition: boolean, message: string): void {
  if (!condition) throw new Error(`Assertion failed: ${message}`);
}

function assertEqual(actual: unknown, expected: unknown, message: string): void {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${message}\n  Expected: ${JSON.stringify(expected)}\n  Actual: ${JSON.stringify(actual)}`);
  }
}

function test(name: string, fn: () => void): void {
  try {
    fn();
    console.log(`  ✔ ${name}`);
  } catch (e) {
    console.error(`  ✘ ${name}: ${(e as Error).message}`);
    throw e;
  }
}

console.log("callStatus tests");

// 1. Null/undefined handling
test("a missing record renders nothing", () => {
  const result = describeCallStatus(null);
  assertEqual(result, null);
});

test("undefined renders nothing", () => {
  const result = describeCallStatus(undefined);
  assertEqual(result, null);
});

// 2. Simulated mode (decision 1)
test("a simulated call always says so", () => {
  const result = describeCallStatus({ status: "dialing", simulated: true, contact_name: "Rohan" });
  assertEqual(result, { label: "Calling Rohan… · simulated", tone: "neutral" });
});

test("a simulated call still reports the provider status", () => {
  // Regression: returning only "SIMULATED" made a ladder of three attempts
  // read identically, so a caregiver could not tell a no-answer from a
  // confirmed call.
  const noAnswer = describeCallStatus({ status: "no_answer", simulated: true });
  const acked = describeCallStatus({ status: "acknowledged", simulated: true, contact_name: "Rohan" });
  assert(noAnswer !== null && acked !== null, "both describe");
  assert(noAnswer!.label !== acked!.label, "different statuses give different lines");
  assert(/simulated/i.test(noAnswer!.label), "no_answer label carries the simulated marker");
  assert(/simulated/i.test(acked!.label), "acknowledged label carries the simulated marker");
});

// 3. Incident-level terminal states
test("exhausted shows honest 'no one reached' line", () => {
  const result = describeCallStatus({ incident_status: "exhausted" });
  assertEqual(result, { label: "No contact acknowledged a call — dashboard alert only", tone: "warn" });
});

test("cancelled shows caregiver cancelled", () => {
  const result = describeCallStatus({ incident_status: "cancelled" });
  assertEqual(result, { label: "Call campaign cancelled by caregiver", tone: "neutral" });
});

test("failed/disabled shows honest disabled line", () => {
  const result = describeCallStatus({ incident_status: "failed", reason: "disabled" });
  assertEqual(result, { label: "Emergency calling is turned off — dashboard alert only", tone: "muted" });
});

test("failed/no_callable_contact shows masked phone or no contact", () => {
  const result1 = describeCallStatus({ incident_status: "failed", reason: "no_callable_contact", contact_name: "Rohan", phone_masked: "+91 **** 3210" });
  assertEqual(result1, { label: "No callable contact for Rohan — no call was placed", tone: "muted" });

  const result2 = describeCallStatus({ incident_status: "failed", reason: "no_callable_contact", contact_name: "Priya", phone_masked: "" });
  assertEqual(result2, { label: "No contact has both a number and permission — no call was placed", tone: "muted" });
});

test("failed/cooldown shows cooldown line", () => {
  const result = describeCallStatus({ incident_status: "failed", reason: "cooldown" });
  assertEqual(result, { label: "No new call — a recent emergency call was already placed", tone: "muted" });
});

test("failed/unknown reason falls back to cautious label", () => {
  const result = describeCallStatus({ incident_status: "failed", reason: "unknown" });
  assertEqual(result, { label: "Call could not be placed — dashboard alert only", tone: "warn" });
});

// 4. Per-attempt states
test("dialing shows 'Calling…'", () => {
  const result = describeCallStatus({ status: "dialing", contact_name: "Rohan" });
  assertEqual(result, { label: "Calling Rohan…", tone: "neutral" });
});

test("ringing shows 'Ringing'", () => {
  const result = describeCallStatus({ status: "ringing", contact_name: "Priya" });
  assertEqual(result, { label: "Ringing Priya", tone: "neutral" });
});

test("answered shows 'answered — confirmation not received'", () => {
  const result = describeCallStatus({ status: "answered", contact_name: "Rohan" });
  assertEqual(result, { label: "Rohan's phone answered — confirmation not received", tone: "neutral" });
});

test("acknowledged shows 'pressed 1 to acknowledge'", () => {
  const result = describeCallStatus({ status: "acknowledged", contact_name: "Priya" });
  assertEqual(result, { label: "Priya pressed 1 to acknowledge", tone: "neutral" });
});

test("no_answer does not promise a retry and does not contradict 'answered'", () => {
  // The provider's `no_answer` means the line opened but no keypress arrived
  // (or it never opened). One sentence must be true in both cases, and the
  // last attempt has no retry so the label must not say "trying again".
  const result = describeCallStatus({ status: "no_answer", contact_name: "Rohan" });
  assertEqual(result, { label: "No acknowledgement from the call", tone: "warn" });
});

test("failed shows 'failed'", () => {
  const result = describeCallStatus({ status: "failed", contact_name: "Priya" });
  assertEqual(result, { label: "Call to Priya failed", tone: "warn" });
});

test("cancelled shows 'cancelled'", () => {
  const result = describeCallStatus({ status: "cancelled", contact_name: "Rohan" });
  assertEqual(result, { label: "Call to Rohan cancelled", tone: "muted" });
});

test("the orchestrator spelling 'canceled' is understood", () => {
  const result = describeCallStatus({ status: "canceled", contact_name: "Rohan" });
  assertEqual(result, { label: "Call to Rohan cancelled", tone: "muted" });
});

test("unknown status falls back to cautious label", () => {
  const result = describeCallStatus({ status: "weird_status" });
  assertEqual(result, { label: "Call status unknown", tone: "warn" });
});

// 5. Next contact transition
test("a status without a contact name invents no name", () => {
  // The dashboard timeline's status event carries no contact, so the label
  // must not fall back to a made-up one ("caregiver's phone answered").
  const answered = describeCallStatus({ status: "answered" });
  const ringing = describeCallStatus({ status: "ringing" });
  assertEqual(answered, { label: "Phone answered — confirmation not received", tone: "neutral" });
  assertEqual(ringing, { label: "Ringing", tone: "neutral" });
  assert(!/caregiver/i.test(answered!.label), "no invented contact in the answered label");
});

test("describeNextContact returns 'Trying X next'", () => {
  const result = describeNextContact("Priya", "+91 **** 3211");
  assertEqual(result, { label: "Trying Priya next", tone: "warn" });
});

// 6. Forbidden words enforcement
test("no reachable label contains forbidden words", () => {
  const allLabels: string[] = [];

  // Collect all reachable labels
  const states = [
    { status: "dialing" },
    { status: "ringing" },
    { status: "answered" },
    { status: "acknowledged" },
    { status: "no_answer" },
    { status: "failed" },
    { status: "cancelled" },
    { incident_status: "exhausted" },
    { incident_status: "cancelled" },
    { incident_status: "failed", reason: "disabled" },
    { incident_status: "failed", reason: "no_callable_contact", contact_name: "Rohan", phone_masked: "+91 **** 3210" },
    { incident_status: "failed", reason: "cooldown" },
    { incident_status: "failed", reason: "unknown" },
    { status: "unknown_status" },
    // Simulated variants go through the same forbidden-word gate.
    { status: "ringing", simulated: true, contact_name: "Rohan" },
    { status: "acknowledged", simulated: true, contact_name: "Rohan" },
    { incident_status: "exhausted", simulated: true },
  ];

  for (const state of states) {
    const desc = describeCallStatus(state);
    if (desc) allLabels.push(desc.label);
  }

  const next = describeNextContact("Priya", "+91 **** 3211");
  if (next) allLabels.push(next.label);

  for (const label of allLabels) {
    for (const forbidden of FORBIDDEN_WORDS) {
      // Match whole words only (word boundary) to avoid false positives like "already" containing "read"
      const regex = new RegExp(`\\b${forbidden}\\b`, "i");
      assert(!regex.test(label), `Forbidden word "${forbidden}" found in label: "${label}"`);
    }
  }
});

// 7. Case insensitivity
test("status matching is case-insensitive", () => {
  const result1 = describeCallStatus({ status: "ANSWERED" });
  const result2 = describeCallStatus({ status: "answered" });
  assertEqual(result1, result2);
});

test("reason matching is case-insensitive", () => {
  const result1 = describeCallStatus({ incident_status: "FAILED", reason: "DISABLED" });
  const result2 = describeCallStatus({ incident_status: "failed", reason: "disabled" });
  assertEqual(result1, result2);
});

console.log("\nAll callStatus tests passed!");
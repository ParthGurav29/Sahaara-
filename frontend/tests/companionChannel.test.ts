import test from "node:test";
import assert from "node:assert/strict";

import {
  COMPANION_KINDS,
  FORBIDDEN_COMPANION_KEY,
  parseCompanionMessage,
} from "../src/companionChannel.ts";

const envelope = (type: string, data: unknown) => JSON.stringify({ type, data });

test("the allowlist is exactly the five companion kinds", () => {
  assert.deepEqual([...COMPANION_KINDS].sort(), [
    "caption",
    "clock",
    "next_routine",
    "now_playing",
    "reminder_countdown",
  ]);
});

test("a caption is accepted and keeps only text and at", () => {
  const parsed = parseCompanionMessage(
    envelope("caption", { text: "I'm right here.", at: 123, extra: "nope" }),
  );
  assert.deepEqual(parsed, {
    type: "caption",
    data: { text: "I'm right here.", at: 123 },
  });
});

test("a clock is accepted", () => {
  const parsed = parseCompanionMessage(
    envelope("clock", { now: 123.5, iso: "2026-10-29T11:00:00", date: "Thursday" }),
  );
  assert.deepEqual(parsed, {
    type: "clock",
    data: { now: 123.5, iso: "2026-10-29T11:00:00", date: "Thursday" },
  });
});

test("a reminder countdown is accepted, and present:false clears it", () => {
  assert.deepEqual(
    parseCompanionMessage(
      envelope("reminder_countdown", {
        present: true,
        id: 7,
        label: "Tea",
        fire_at: 200,
        seconds_remaining: 77,
        at: 123,
      }),
    ),
    {
      type: "reminder_countdown",
      data: {
        present: true,
        id: 7,
        label: "Tea",
        fire_at: 200,
        seconds_remaining: 77,
        at: 123,
      },
    },
  );

  assert.deepEqual(
    parseCompanionMessage(envelope("reminder_countdown", { present: false })),
    { type: "reminder_countdown", data: { present: false } },
  );
});

test("a next routine is accepted", () => {
  assert.deepEqual(
    parseCompanionMessage(
      envelope("next_routine", { label: "Lunch", time: "13:00", day: "today" }),
    ),
    { type: "next_routine", data: { label: "Lunch", time: "13:00", day: "today" } },
  );
});

test("now playing is accepted for songs and radio", () => {
  assert.deepEqual(
    parseCompanionMessage(
      envelope("now_playing", { title: "Lag Jaa Gale", kind: "song", playing: true }),
    ),
    {
      type: "now_playing",
      data: { title: "Lag Jaa Gale", kind: "song", playing: true },
    },
  );
  assert.equal(
    parseCompanionMessage(
      envelope("now_playing", { title: "Radio Mirchi", kind: "radio", playing: true }),
    )?.data.kind,
    "radio",
  );
});

test("caregiver kinds are refused outright", () => {
  for (const type of [
    "tier_change",
    "alert",
    "consent_asked",
    "consent_answered",
    "danger_statement",
    "physical_symptom",
    "distress",
    "repeat",
    "fallback",
    "status",
    "events",
    "event",
  ]) {
    assert.equal(
      parseCompanionMessage(envelope(type, { text: "Should not pass" })),
      null,
      `type ${type}`,
    );
  }
});

test("a forbidden key rejects the whole message, not just the key", () => {
  for (const key of [
    "tier",
    "alert_id",
    "consent_state",
    "danger_level",
    "symptom",
    "emotion",
    "severity",
    "distress",
    "repeat_count",
    "fallback_type",
    "physical_urgency",
    "diagnosis",
  ]) {
    assert.equal(
      parseCompanionMessage(envelope("caption", { text: "Hi", [key]: "x" })),
      null,
      `key ${key}`,
    );
  }
});

test("the forbidden-key detector matches the backend vocabulary", () => {
  for (const key of ["tier", "alert_id", "consent", "danger", "symptom"]) {
    assert.ok(FORBIDDEN_COMPANION_KEY.test(key), key);
  }
  for (const key of ["text", "now", "label", "seconds_remaining", "playing", "day"]) {
    assert.ok(!FORBIDDEN_COMPANION_KEY.test(key), key);
  }
});

test("malformed or non-text frames are ignored", () => {
  for (const raw of ["", "not json", "null", "[]", "42", "{}", '{"type":"caption"}']) {
    assert.equal(parseCompanionMessage(raw), null, JSON.stringify(raw));
  }
  assert.equal(parseCompanionMessage(undefined), null);
  assert.equal(parseCompanionMessage({ type: "caption", data: { text: "Hi" } }), null);
});

test("a message missing its required fields is ignored", () => {
  assert.equal(parseCompanionMessage(envelope("caption", { at: 1 })), null);
  assert.equal(parseCompanionMessage(envelope("caption", { text: "   " })), null);
  assert.equal(parseCompanionMessage(envelope("clock", { date: "Thursday" })), null);
  assert.equal(parseCompanionMessage(envelope("clock", { now: 1 })), null);
  assert.equal(
    parseCompanionMessage(envelope("reminder_countdown", { present: true, id: 1 })),
    null,
  );
  assert.equal(
    parseCompanionMessage(envelope("next_routine", { label: "Lunch", time: "13:00" })),
    null,
  );
  assert.equal(
    parseCompanionMessage(
      envelope("now_playing", { title: "Song", kind: "video", playing: true }),
    ),
    null,
  );
});

import test from "node:test";
import assert from "node:assert/strict";

import {
  ALARM_RING_OFFSETS_MS,
  ALARM_TOTAL_MS,
  alarmSchedule,
  reminderBannerLabel,
  reminderBannerText,
} from "../src/alarm.ts";

test("the chime rings three times", () => {
  assert.deepEqual(ALARM_RING_OFFSETS_MS, [0, 2500, 5000]);
  const tones = alarmSchedule();
  // two tones per ring: a high note then a softer low note
  assert.equal(tones.length, ALARM_RING_OFFSETS_MS.length * 2);
});

test("the schedule is ordered and finishes before the self-stop", () => {
  const tones = alarmSchedule();
  for (let i = 1; i < tones.length; i += 1) {
    assert.ok(tones[i].atMs >= tones[i - 1].atMs, "tones are scheduled in order");
  }
  const last = tones[tones.length - 1];
  assert.ok(last.atMs + last.durationMs <= ALARM_TOTAL_MS, "the last tone ends before the alarm stops");
});

test("every tone is quiet enough to stay calm", () => {
  for (const tone of alarmSchedule()) {
    assert.ok(tone.gain > 0 && tone.gain <= 0.25, `gain ${tone.gain} stays low`);
    assert.ok(tone.freqHz >= 440 && tone.freqHz <= 1000, `freq ${tone.freqHz} is a gentle bell`);
    assert.ok(tone.durationMs >= 200 && tone.durationMs <= 1200, `duration ${tone.durationMs}`);
  }
});

test("the banner repeats what actually fired", () => {
  assert.equal(reminderBannerText({ kind: "timer", spoken: "Your 5 minute timer is done." }),
    "Your 5 minute timer is done.");
  assert.equal(reminderBannerText({ kind: "reminder", spoken: "Time to take your medication." }),
    "Time to take your medication.");
});

test("the banner never invents a timer", () => {
  // No spoken line: a neutral fallback that still says a real fire happened.
  assert.equal(reminderBannerText({ kind: "timer" }), "Your timer is done.");
  assert.equal(reminderBannerText({ kind: "reminder" }), "Your reminder is due.");
  assert.equal(reminderBannerText(null), "Your timer is done.");
  // A non-string spoken value is not rendered as-is.
  assert.equal(reminderBannerText({ kind: "timer", spoken: 42 }), "Your timer is done.");
  assert.equal(reminderBannerText({ kind: "timer", spoken: "   " }), "Your timer is done.");
});

test("the label distinguishes a timer from a reminder", () => {
  assert.equal(reminderBannerLabel({ kind: "timer" }), "Timer finished");
  assert.equal(reminderBannerLabel({ kind: "reminder" }), "Reminder");
  assert.equal(reminderBannerLabel(undefined), "Timer finished");
});

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (relativePath) =>
  readFileSync(new URL(`../${relativePath}`, import.meta.url), "utf8");

const COMPANION_FILES = [
  "src/CompanionView.tsx",
  "src/useCompanionChannel.ts",
  "src/companionChannel.ts",
];

const stripComments = (source) =>
  source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");

/**
 * The companion screen must not import any caregiver data module. This is the
 * cheap, permanent version of the manual "network tab" check: the person's
 * page cannot fetch what it has no code to fetch.
 */
const FORBIDDEN_IMPORTS = [
  "AuthContext",
  "eventStream",
  "EscalationLadder",
  "AlertCenter",
  "DailySummaryScreen",
  "SuggestedFacts",
  "ProfileForm",
  "PushButton",
  "TaskAlarm",
  "deepLink",
  "pushAlerts",
  "pushStatus",
  "pushSupport",
];

test("companion modules import no caregiver data or stream module", () => {
  for (const file of COMPANION_FILES) {
    const source = read(file);
    for (const name of FORBIDDEN_IMPORTS) {
      assert.ok(
        !new RegExp(`from\\s+["'][^"']*${name}`).test(source),
        `${file} imports ${name}`,
      );
    }
  }
});

test("the companion channel is the only stream the person's view opens", () => {
  const hook = read("src/useCompanionChannel.ts");
  assert.ok(hook.includes("/stream/ws/companion"));
  assert.ok(!hook.includes("/stream/ws/events"));
  assert.ok(!hook.includes("/stream/events"));

  const view = read("src/CompanionView.tsx");
  assert.ok(!view.includes("apiFetch("), "CompanionView must not call the API directly");
  assert.ok(!view.includes("/escalation"), "CompanionView must not read escalation data");
  assert.ok(!view.includes("/alerts"), "CompanionView must not read alerts");
});

test("the companion view has no caregiver controls or vocabulary", () => {
  const view = stripComments(read("src/CompanionView.tsx"));
  // The "Caregiver" button is a navigation label (goes to /caregiver), not a caregiver control.
  // We check for caregiver vocabulary in control contexts, not navigation buttons or URLs.
  for (const word of [
    "Logout",
    "Tier",
    "tier",
    "Alert",
    "alert",
    "Daily Summary",
    "Suggested Facts",
    "Profile",
    "Rohan",
  ]) {
    assert.ok(!view.includes(word), `CompanionView contains ${JSON.stringify(word)}`);
  }
  // "caregiver" is allowed in: navigation button label, redirect URL, and PIN modal title
  const caregiverMatches = view.match(/caregiver/gi);
  assert.ok(caregiverMatches && caregiverMatches.length === 3,
    `CompanionView should mention 'caregiver' exactly 3 times (nav button, URL, PIN modal), found ${caregiverMatches?.length ?? 0}`);
});

test("the route module keeps the companion behind its own path", () => {
  const route = read("src/route.ts");
  assert.ok(route.includes('"/companion"'));
  assert.ok(route.includes('"/caregiver"'));
  // Default '/' must resolve to caregiver, not companion.
  assert.ok(!/path === "\/"[\s\S]{0,80}return "companion"/.test(route));
});

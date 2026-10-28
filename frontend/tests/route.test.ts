import test from "node:test";
import assert from "node:assert/strict";

import { parseRoute } from "../src/route.ts";

test("the person's route is /companion, with or without a trailing slash", () => {
  assert.equal(parseRoute("/companion", ""), "companion");
  assert.equal(parseRoute("/companion/", ""), "companion");
  assert.equal(parseRoute("/companion/", "?demo=split"), "companion");
});

test("the caregiver route is /caregiver", () => {
  assert.equal(parseRoute("/caregiver", ""), "caregiver");
  assert.equal(parseRoute("/caregiver/", ""), "caregiver");
  assert.equal(parseRoute("/caregiver", "?demo=split"), "caregiver");
});

test("the default entry point is the caregiver login, not the companion", () => {
  assert.equal(parseRoute("/", ""), "caregiver");
  assert.equal(parseRoute("", ""), "caregiver");
  assert.equal(parseRoute("/", "?foo=bar"), "caregiver");
});

test("the hidden demo split only exists at /?demo=split", () => {
  assert.equal(parseRoute("/", "?demo=split"), "demo-split");
  assert.equal(parseRoute("/", "?demo=other"), "caregiver");
});

test("an unknown path falls back to the caregiver entry, never the companion", () => {
  for (const path of ["/nope", "/companionx", "/caregiver/extra", "/..", "/index.html"]) {
    assert.equal(parseRoute(path, ""), "caregiver", `path ${path}`);
  }
});

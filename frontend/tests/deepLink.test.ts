import test from "node:test";
import assert from "node:assert/strict";

import {
  ALERT_NOT_FOUND_MESSAGE,
  parseAlertId,
  withoutAlertParam,
} from "../src/deepLink.ts";

test("a valid alert id is read from the query string", () => {
  assert.equal(parseAlertId("?alert=alert_1"), "alert_1");
  assert.equal(parseAlertId("?foo=bar&alert=alert-9_x"), "alert-9_x");
  assert.equal(parseAlertId("?alert=%20alert_1%20"), "alert_1");
});

test("missing, empty, or unsafe ids are ignored", () => {
  for (const search of ["", "?", "?alert=", "?alert=../etc", "?alert=a b", "?alert=" + "x".repeat(80), "?other=1"]) {
    assert.equal(parseAlertId(search), null, `should ignore ${JSON.stringify(search)}`);
  }
});

test("a malformed query string never throws", () => {
  assert.equal(parseAlertId("?%"), null);
});

test("the alert parameter can be stripped so a refresh is quiet", () => {
  assert.equal(
    withoutAlertParam("http://127.0.0.1:5174/?alert=alert_1&x=1#top"),
    "/?x=1#top",
  );
  assert.equal(withoutAlertParam("http://127.0.0.1:5174/"), "/");
});

test("the not-found message is calm and names no cause", () => {
  assert.match(ALERT_NOT_FOUND_MESSAGE, /no longer available/i);
  assert.doesNotMatch(ALERT_NOT_FOUND_MESSAGE, /error|failed|expired|deleted|crash/i);
});

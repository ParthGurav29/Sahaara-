import test from "node:test";
import assert from "node:assert/strict";

import { decidePwaAction, parseFlag } from "../src/pwaGate.ts";

test("parseFlag accepts the same spellings as the backend gate", () => {
  for (const value of ["true", "TRUE", " true ", "1", "yes", "on"]) {
    assert.equal(parseFlag(value), true, `expected ${JSON.stringify(value)} -> true`);
  }
  for (const value of ["false", "0", "no", "off", "", "  ", undefined, null, 1, true]) {
    assert.equal(parseFlag(value), false, `expected ${JSON.stringify(value)} -> false`);
  }
});

test("build gate OFF never enables the shell (default build)", () => {
  assert.equal(decidePwaAction({ buildEnabled: false, serverEnabled: true }), "disable");
  assert.equal(decidePwaAction({ buildEnabled: false, serverEnabled: false }), "disable");
  assert.equal(decidePwaAction({ buildEnabled: false, serverEnabled: null }), "disable");
});

test("an unreachable backend leaves an installed app alone (defect 1)", () => {
  // This is the regression test: a failed /config read used to be treated as
  // "server says off", which unregistered the worker and wiped the shell.
  assert.equal(decidePwaAction({ buildEnabled: true, serverEnabled: null }), "keep");
});

test("both gates ON enables the shell", () => {
  assert.equal(decidePwaAction({ buildEnabled: true, serverEnabled: true }), "enable");
});

test("build ON but backend send gate OFF tears the shell down", () => {
  assert.equal(decidePwaAction({ buildEnabled: true, serverEnabled: false }), "disable");
});

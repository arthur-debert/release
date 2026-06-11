import * as assert from "node:assert/strict";
import { test } from "node:test";

import { report } from "./liveness";

test("report includes the name", () => {
  assert.equal(report("canary"), "canary is alive");
});

test("report works for a custom name", () => {
  assert.equal(report("tweety"), "tweety is alive");
});

import assert from "node:assert/strict";
import { test } from "node:test";

import { greeting } from "../src/index.js";

test("greets", () => {
  assert.match(greeting(), /quiet-otter/);
});

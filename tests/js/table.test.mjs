// Behaviour tests for management's table.js (sorting), run with `node --test`.
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import assert from "node:assert/strict";

const here = dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const { compare, order } = require(join(here, "..", "..", "management", "src", "management", "static", "management", "table.js"));

const rows = (keys) => keys.map((key, index) => ({ key, index }));
const keys = (list) => list.map((r) => r.key);

test("text sorts as text and numbers as numbers", () => {
  assert.deepEqual(keys(order(rows(["10", "9", "100"]), "ascending")), ["9", "10", "100"]);
  assert.deepEqual(keys(order(rows(["b", "a", "c"]), "descending")), ["c", "b", "a"]);
  assert.ok(compare("pi-sw1-p9", "pi-sw1-p10") < 0);
});

test("none restores the served order and ties keep it", () => {
  const served = rows(["x", "a", "x", "b"]);
  assert.deepEqual(order(served, "ascending").map((r) => r.index), [1, 3, 0, 2]);
  assert.deepEqual(order(order(served, "descending"), "none").map((r) => r.index), [0, 1, 2, 3]);
});

test("an empty cell sorts as text, never as zero", () => {
  assert.deepEqual(keys(order(rows(["5", "", "3"]), "ascending")), ["", "3", "5"]);
});

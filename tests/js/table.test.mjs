// Behaviour tests for management's table.js (sorting), run with `node --test`.
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import assert from "node:assert/strict";

const here = dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const { compare, order, value } = require(join(here, "..", "..", "management", "src", "management", "static", "management", "table.js"));

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

// The wiring, in a minimal fake document: a header button per column, aria-sort on the header, the rows moved.
function cell(text, sort) {
  const c = { textContent: text, attrs: sort === undefined ? {} : { "data-sort": String(sort) }, children: [] };
  c.getAttribute = (n) => (n in c.attrs ? c.attrs[n] : null);
  return c;
}
function fakeTable(data) {
  const body = { rows: [], appendChild(row) { this.rows = this.rows.filter((r) => r !== row).concat([row]); } };
  body.rows = data.map((cells, i) => {
    const row = { cells, attrs: {} };
    row.setAttribute = (n, v) => { row.attrs[n] = v; };
    row.getAttribute = (n) => row.attrs[n];
    return row;
  });
  const heads = data[0].map((_, i) => {
    const pending = [{ name: `h${i}` }];
    const th = { attrs: {}, children: [], get firstChild() { return pending.shift() || null; } };
    th.setAttribute = (n, v) => { th.attrs[n] = v; };
    th.getAttribute = (n) => th.attrs[n];
    th.appendChild = (child) => { th.button = child; };
    return th;
  });
  return { tBodies: [body], tHead: { rows: [{ cells: heads }] }, body, heads };
}

test("a click on a header sorts, then reverses, then restores the served order", () => {
  const table = fakeTable([[cell("b"), cell("x", 3)], [cell("a"), cell("y", 1)], [cell("c"), cell("z", 2)]]);
  const buttons = [];
  globalThis.document = {
    createElement: () => {
      const b = { listeners: {}, firstChild: null, appendChild(c) { b.child = c; }, addEventListener(e, f) { b.listeners[e] = f; } };
      buttons.push(b);
      return b;
    },
    querySelectorAll: () => [table],
  };
  delete require.cache[require.resolve(join(here, "..", "..", "management", "src", "management", "static", "management", "table.js"))];
  require(join(here, "..", "..", "management", "src", "management", "static", "management", "table.js"));
  const served = () => table.body.rows.map((r) => r.cells[0].textContent).join("");
  assert.deepEqual(table.heads.map((h) => h.attrs["aria-sort"]), ["none", "none"]);
  buttons[1].listeners.click();  // by the second column's data-sort: 1, 2, 3
  assert.equal(served(), "acb");
  assert.deepEqual(table.heads.map((h) => h.attrs["aria-sort"]), ["none", "ascending"]);
  buttons[1].listeners.click();
  assert.equal(served(), "bca");
  assert.equal(table.heads[1].attrs["aria-sort"], "descending");
  buttons[1].listeners.click();
  assert.equal(served(), "bac");  // the order the page was served in
  assert.equal(table.heads[1].attrs["aria-sort"], "none");
  buttons[0].listeners.click();  // another column: the first column's text, and the second header resets
  assert.equal(served(), "abc");
  assert.deepEqual(table.heads.map((h) => h.attrs["aria-sort"]), ["ascending", "none"]);
});


test("a row with fewer cells than the column sorts as empty, not an error", () => {
  assert.equal(value(undefined), "");
  assert.equal(value({ getAttribute: () => "7", textContent: "x" }), "7");
});

test("a body rebuilt after the page loaded is sorted as the headers say (resort) and by a later click", () => {
  const table = fakeTable([[cell("b")], [cell("a")], [cell("c")]]);
  const buttons = [];
  globalThis.document = {
    createElement: () => {
      const b = { listeners: {}, firstChild: null, appendChild(c) { b.child = c; }, addEventListener(e, f) { b.listeners[e] = f; } };
      buttons.push(b);
      return b;
    },
    querySelectorAll: () => [table],
  };
  const path = join(here, "..", "..", "management", "src", "management", "static", "management", "table.js");
  delete require.cache[require.resolve(path)];
  const { resort } = require(path);
  buttons[0].listeners.click();  // ascending
  // the refresh puts a new body in, in the served order, its rows not numbered yet
  const fresh = fakeTable([[cell("z")], [cell("x")], [cell("y")]]);
  table.tBodies[0] = fresh.body;
  resort(table);
  assert.equal(fresh.body.rows.map((r) => r.cells[0].textContent).join(""), "xyz");
  buttons[0].listeners.click();  // descending, on the new body
  assert.equal(fresh.body.rows.map((r) => r.cells[0].textContent).join(""), "zyx");
  buttons[0].listeners.click();  // the served order of the new body
  assert.equal(fresh.body.rows.map((r) => r.cells[0].textContent).join(""), "zxy");
});

// site #138: the order the reader chose survives the table being drawn again (a refresh in place, a reload).
function namedTable(data, names) {
  const t = fakeTable(data);
  t.heads.forEach((th, i) => { th.childNodes = [{ nodeType: 3, nodeValue: ` ${names[i]} ` }]; });
  t.closest = () => ({ getAttribute: () => "Raspberry Pis" });
  return t;
}
function loadWith(table, store) {
  const buttons = [];
  globalThis.location = { pathname: "/fleet/", search: "?condition=attention" };
  globalThis.sessionStorage = {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = v; },
  };
  globalThis.document = {
    createElement: () => {
      const b = { listeners: {}, firstChild: null, appendChild(c) { b.child = c; }, addEventListener(e, f) { b.listeners[e] = f; } };
      buttons.push(b);
      return b;
    },
    querySelectorAll: () => [table],
  };
  const path = join(here, "..", "..", "management", "src", "management", "static", "management", "table.js");
  delete require.cache[require.resolve(path)];
  require(path);
  return buttons;
}
const texts = (t) => t.body.rows.map((r) => r.cells[0].textContent + r.cells[1].textContent).join(" ");

test("a table drawn again is sorted as the reader left it, by the header's text, even with a column added", () => {
  const store = {};
  const first = namedTable([[cell("b"), cell("x", 3)], [cell("a"), cell("y", 1)], [cell("c"), cell("z", 2)]], ["Serial", "Seen"]);
  const buttons = loadWith(first, store);
  buttons[1].listeners.click();
  buttons[1].listeners.click();  // Seen, descending
  assert.equal(texts(first), "bx cz ay");
  assert.deepEqual(JSON.parse(store["mgmt-sort:/fleet/?condition=attention"]),
                   { "Raspberry Pis": { column: "Seen", direction: "descending" } });
  // the page is drawn again (refresh or reload) with a new column in front: Seen is now the third column
  const again = namedTable([[cell("-"), cell("q"), cell("1")], [cell("-"), cell("r"), cell("9")], [cell("-"), cell("s"), cell("5")]],
                           ["New", "Serial", "Seen"]);
  loadWith(again, store);
  assert.deepEqual(again.heads.map((h) => h.attrs["aria-sort"]), ["none", "none", "descending"]);
  assert.equal(again.body.rows.map((r) => r.cells[2].textContent).join(""), "951");
});

test("a third click forgets the order, so the next drawing keeps the served order", () => {
  const store = {};
  const t = namedTable([[cell("b"), cell("x")], [cell("a"), cell("y")]], ["Serial", "Seen"]);
  const buttons = loadWith(t, store);
  buttons[0].listeners.click(); buttons[0].listeners.click(); buttons[0].listeners.click();
  assert.deepEqual(JSON.parse(store["mgmt-sort:/fleet/?condition=attention"]), {});
  const again = namedTable([[cell("b"), cell("x")], [cell("a"), cell("y")]], ["Serial", "Seen"]);
  loadWith(again, store);
  assert.equal(texts(again), "bx ay");
});

test("storage that throws (a private window) still sorts and keeps no order across a reload", () => {
  const t = namedTable([[cell("b"), cell("x")], [cell("a"), cell("y")]], ["Serial", "Seen"]);
  const buttons = loadWith(t, {});
  globalThis.sessionStorage = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } };
  buttons[0].listeners.click();
  assert.equal(texts(t), "ay bx");
});

test("a header's text leaves out its words for a screen reader", () => {
  const { headText } = require(join(here, "..", "..", "management", "src", "management", "static", "management", "table.js"));
  const th = { childNodes: [{ nodeType: 3, nodeValue: "jtag " },
                            { nodeType: 1, classList: { contains: (c) => c === "mgmt-sr" }, childNodes: [{ nodeType: 3, nodeValue: "check" }] }] };
  assert.equal(headText(th), "jtag");
});

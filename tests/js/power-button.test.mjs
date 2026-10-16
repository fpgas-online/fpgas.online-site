// Behaviour tests for management's power-button.js (the switch dashboard's PoE button, round 5), run with
// `node --test`. The tracker and the answer reader are plain functions; draw() and toast() get a small fake page.
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import assert from "node:assert/strict";

const here = dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const PB = require(
  join(here, "..", "..", "management", "src", "management", "static", "management", "power-button.js"));

const K = "2/9";

test("an untouched port's button is the reading's, ⏻, and can be pressed", () => {
  const t = PB.createTracker();
  assert.deepEqual(t.view(K, "off", 0), { action: "off", text: "⏻", busy: false, disabled: false, seconds: 0 });
});

test("a pressed button turns until the switch confirms, and cannot be pressed twice", () => {
  const t = PB.createTracker();
  t.start(K);
  const v = t.view(K, "off", 1000);
  assert.equal(v.busy, true);
  assert.equal(v.disabled, true);
  assert.equal(v.text, "⏻");
  assert.equal(t.ticking(1000), true);
});

test("once confirmed off, the button offers on and counts Tim's ten seconds down inside it", () => {
  const t = PB.createTracker();
  t.start(K);
  t.confirmed(K, "off", 10, 0);
  // the reading (15 s server cache) still says the port is powered: the confirmed state wins
  assert.deepEqual(t.view(K, "off", 0), { action: "on", text: "10", busy: false, disabled: true, seconds: 10 });
  assert.equal(t.view(K, "off", 400).text, "10");
  assert.equal(t.view(K, "off", 1000).text, "9");
  assert.equal(t.view(K, "off", 9001).text, "1");
  const free = t.view(K, "off", 10000);
  assert.deepEqual(free, { action: "on", text: "⏻", busy: false, disabled: false, seconds: 0 });
});

test("once confirmed on, the wait before the next off is sixty seconds", () => {
  const t = PB.createTracker();
  t.start(K);
  t.confirmed(K, "on", 60, 0);
  assert.equal(t.view(K, "on", 0).text, "60");
  assert.equal(t.view(K, "on", 0).action, "off");
  assert.equal(t.view(K, "on", 59500).text, "1");
  assert.equal(t.view(K, "on", 60000).disabled, false);
});

test("a reading that agrees with the confirmed state ends the override", () => {
  const t = PB.createTracker();
  t.confirmed(K, "off", 10, 0);
  assert.equal(t.view(K, "on", 11000).action, "on");  // the reading caught up: the port is off, its button turns it on
  // later readings are believed again, whatever they say (someone else may switch the port)
  assert.equal(t.view(K, "off", 12000).action, "off");
});

test("a confirmed state the reading never shows is believed for a while only", () => {
  const t = PB.createTracker();
  t.confirmed(K, "off", 10, 0);
  assert.equal(t.view(K, "off", PB.CONFIRMED_KEPT_MS - 1).action, "on");
  assert.equal(t.view(K, "off", PB.CONFIRMED_KEPT_MS + 1).action, "off");
});

test("a press inside someone else's hold counts that hold down: never silently dropped", () => {
  const t = PB.createTracker();
  t.start(K);
  t.held(K, 42, 0);
  const v = t.view(K, "off", 0);
  assert.deepEqual(v, { action: "off", text: "42", busy: false, disabled: true, seconds: 42 });
});

test("a failed press frees the button at once", () => {
  const t = PB.createTracker();
  t.start(K);
  t.failed(K);
  assert.deepEqual(t.view(K, "on", 0), { action: "on", text: "⏻", busy: false, disabled: false, seconds: 0 });
  assert.equal(t.ticking(0), false);
});

test("the answer is read as confirmation, hold or error", () => {
  assert.deepEqual(PB.readAnswer(200, { state: "off", next_in: 10 }, null, "switch 2 port 9"),
    { kind: "ok", state: "off", seconds: 10, text: "PoE off on switch 2 port 9" });
  assert.deepEqual(PB.readAnswer(429, { error: "x" }, "37", "switch 2 port 9"),
    { kind: "held", seconds: 37, text: "switch 2 port 9 was switched a moment ago; it can be switched again in 37 s" });
  assert.equal(PB.readAnswer(429, {}, null, "port 9").seconds, 1);
  assert.deepEqual(PB.readAnswer(403, { error: "not a board" }, null, "port 9"),
    { kind: "error", text: "port 9: not a board (HTTP 403)" });
  // a 200 without a state is not a confirmation
  assert.equal(PB.readAnswer(200, {}, null, "port 9").kind, "error");
});

class Element {
  constructor(tag, doc) {
    Object.assign(this, { tag, ownerDocument: doc, textContent: "", className: "", title: "", disabled: false,
                          dataset: {}, attrs: {}, children: [], listeners: {}, removed: false });
  }
  setAttribute(n, v) { this.attrs[n] = v; }
  appendChild(c) { this.children.push(c); return c; }
  querySelector(sel) { return this.children.find((c) => sel === "." + c.className) || null; }
  addEventListener(type, fn) { this.listeners[type] = fn; }
  remove() { this.removed = true; }
}
function fakeDoc() {
  const doc = { byId: {} };
  doc.createElement = (tag) => new Element(tag, doc);
  doc.getElementById = (id) => doc.byId[id] || null;
  return doc;
}

test("draw puts the countdown inside the button and keeps the action for the next press", () => {
  const doc = fakeDoc();
  const b = doc.createElement("button");
  b.dataset.port = "9";
  PB.draw(b, { action: "on", text: "7", busy: false, disabled: true, seconds: 7 });
  assert.equal(b.querySelector(".mgmt-poe-glyph").textContent, "7");
  assert.equal(b.className, "mgmt-poe-button mgmt-poe-to-on mgmt-poe-wait");
  assert.equal(b.dataset.action, "on");
  assert.equal(b.disabled, true);
  assert.equal(b.title, "can be switched again in 7 s");
  PB.draw(b, { action: "on", text: "⏻", busy: true, disabled: true, seconds: 0 });
  assert.equal(b.className, "mgmt-poe-button mgmt-poe-to-on mgmt-poe-busy");
  assert.equal(b.attrs["aria-busy"], "true");
});

test("a toast goes into the stack at the top, and away by itself", () => {
  const doc = fakeDoc();
  const stack = doc.createElement("div");
  doc.byId["mgmt-toasts"] = stack;
  const later = [];
  PB.toast(doc, "PoE off on switch 2 port 9", "ok", (fn, ms) => later.push([fn, ms]));
  assert.equal(stack.children.length, 1);
  const t = stack.children[0];
  assert.equal(t.className, "mgmt-toast mgmt-toast-ok");
  assert.equal(t.attrs.role, "status");
  assert.equal(t.children[0].textContent, "PoE off on switch 2 port 9");
  assert.equal(later[0][1], 6000);
  later[0][0]();
  assert.equal(t.removed, true);
  PB.toast(doc, "refused", "error", (fn, ms) => later.push([fn, ms]));
  assert.equal(stack.children[1].attrs.role, "alert");
  assert.equal(later[1][1], 20000);
  // "switched a moment ago; ... in 8 s" goes when those 8 s are over, never naming a wait that has ended
  PB.toast(doc, "port 9 was switched a moment ago; it can be switched again in 8 s", "held", (fn, ms) => later.push([fn, ms]), 8);
  assert.equal(later[2][1], 8000);
  PB.toast(doc, "held long", "held", (fn, ms) => later.push([fn, ms]), 60);
  assert.equal(later[3][1], 20000);
});

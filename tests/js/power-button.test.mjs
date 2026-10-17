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
// the gateway's clock (ms) at which the switch confirmed a press (the answer's `at`); a reading that started before it
// is "older", one after it "newer"
const AT = "2026-10-17T15:30:01.250+10:30";
const AFTER = Date.parse(AT);
const OLDER = AFTER - 5000, NEWER = AFTER + 2000;

test("an untouched port's button is the reading's, ⏻, and can be pressed", () => {
  const t = PB.createTracker();
  assert.deepEqual(t.view(K, "off", 0), { action: "off", text: "⏻", busy: false, disabled: false, seconds: 0, confirmed: null });
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
  t.confirmed(K, "off", 10, 0, AFTER);
  // the reading (15 s server cache) still says the port is powered: the confirmed state wins
  assert.deepEqual(t.view(K, "off", 0, OLDER), { action: "on", text: "10", busy: false, disabled: true, seconds: 10, confirmed: "off" });
  assert.equal(t.view(K, "off", 400, OLDER).text, "10");
  assert.equal(t.view(K, "off", 1000, OLDER).text, "9");
  assert.equal(t.view(K, "off", 9001, OLDER).text, "1");
  const free = t.view(K, "off", 10000, OLDER);
  assert.deepEqual(free, { action: "on", text: "⏻", busy: false, disabled: false, seconds: 0, confirmed: "off" });
});

test("once confirmed on, the wait before the next off is sixty seconds", () => {
  const t = PB.createTracker();
  t.start(K);
  t.confirmed(K, "on", 60, 0, AFTER);
  assert.equal(t.view(K, "on", 0, OLDER).text, "60");
  assert.equal(t.view(K, "on", 0, OLDER).action, "off");
  assert.equal(t.view(K, "on", 59500, OLDER).text, "1");
  assert.equal(t.view(K, "on", 60000, OLDER).disabled, false);
});

test("a reading begun after the switch confirmed ends the override", () => {
  const t = PB.createTracker();
  t.confirmed(K, "off", 10, 0, AFTER);
  assert.equal(t.view(K, "on", 11000, NEWER).action, "on");  // the reading caught up: the port is off, its button turns it on
  // later readings are believed again, whatever they say (someone else may switch the port)
  assert.equal(t.view(K, "off", 12000, NEWER + 15000).action, "off");
});

test("a newer reading that contradicts the confirmed state is believed (someone else switched the port)", () => {
  const t = PB.createTracker();
  t.confirmed(K, "off", 10, 0, AFTER);
  assert.equal(t.view(K, "off", 20000, NEWER).confirmed, null);
  assert.equal(t.view(K, "off", 20000, NEWER).action, "off");
});

test("an older reading that happens to agree does not end the override (the deploy 45 wart)", () => {
  const t = PB.createTracker();
  // a reading older than the press shows the state the press set: kept, not ended
  t.confirmed(K, "off", 10, 0, AFTER);
  assert.equal(t.view(K, "on", 1000, OLDER).confirmed, "off");
  // a reading stamped the same millisecond as the confirmation is not known to follow it
  assert.equal(t.view(K, "on", 1000, AFTER).confirmed, "off");
  assert.equal(t.view(K, "on", 1000, AFTER + 1).confirmed, null);
});

test("the confirmed state is kept as long as no reading began after the change, however long that is", () => {
  const t = PB.createTracker();
  t.confirmed(K, "off", 10, 0, AFTER);
  assert.equal(t.view(K, "off", 10 * 60 * 1000, OLDER).action, "on");
  // a switch that is not answering keeps its last ports: no fresh read (NaN), the press stands
  assert.equal(t.view(K, "off", 10 * 60 * 1000, NaN).action, "on");
});

test("a press inside someone else's hold counts that hold down: never silently dropped", () => {
  const t = PB.createTracker();
  t.start(K);
  t.held(K, 42, 0);
  const v = t.view(K, "off", 0);
  assert.deepEqual(v, { action: "off", text: "42", busy: false, disabled: true, seconds: 42, confirmed: null });
});

test("a failed press frees the button at once", () => {
  const t = PB.createTracker();
  t.start(K);
  t.failed(K);
  assert.deepEqual(t.view(K, "on", 0), { action: "on", text: "⏻", busy: false, disabled: false, seconds: 0, confirmed: null });
  assert.equal(t.ticking(0), false);
});

test("the answer is read as confirmation, hold or error", () => {
  assert.deepEqual(PB.readAnswer(200, { state: "off", next_in: 10, at: AT }, null, "switch 2 port 9", "power"),
    { kind: "ok", state: "off", seconds: 10, after: AFTER, text: "PoE off on switch 2 port 9" });
  assert.deepEqual(PB.readAnswer(429, { error: "x" }, "37", "switch 2 port 9"),
    { kind: "held", seconds: 37, text: "switch 2 port 9 was switched a moment ago; it can be switched again in 37 s" });
  assert.equal(PB.readAnswer(429, {}, null, "port 9").seconds, 1);
  assert.deepEqual(PB.readAnswer(403, { error: "not a board" }, null, "port 9"),
    { kind: "error", text: "port 9: not a board (HTTP 403)" });
  // a 200 without a state is not a confirmation
  assert.equal(PB.readAnswer(200, { at: AT }, null, "port 9", "power").kind, "error");
});

test("the PoE service's own form of `at` (microseconds, an offset) is read to the millisecond", () => {
  const a = PB.readAnswer(200, { link: "enabled", next_in: 60, at: "2026-10-17T15:30:01.250731+10:30" }, null,
    "switch 2 port 9", "link");
  assert.equal(a.kind, "ok");
  assert.equal(a.after, Date.parse("2026-10-17T05:00:01.250Z"));
});

test("a confirmation without its time fails loud: the page cannot tell which readings follow it", () => {
  // none, empty, not a time, and a time with no offset (a browser would read it in its own zone)
  for (const at of [undefined, "", "yesterday", "2026-10-17T15:30:01"]) {
    const a = PB.readAnswer(200, { state: "off", next_in: 10, at }, null, "switch 2 port 9", "power");
    assert.equal(a.kind, "error");
    assert.match(a.text, /^PoE off on switch 2 port 9, but the answer carried no time/);
  }
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

// --- the Link badge (round 5, items 4-6) ---

test("a link tracker offers enable once the switch confirmed the port disabled, and counts the short hold", () => {
  const t = PB.createTracker(PB.LINK_ACTION_OF);
  t.start(K);
  assert.equal(t.view(K, "disable", 0).busy, true);
  t.confirmed(K, "disabled", 10, 0, AFTER);
  const v = t.view(K, "disable", 0, OLDER);
  assert.equal(v.action, "enable");
  assert.equal(v.confirmed, "disabled");
  assert.equal(v.seconds, 10);
  // a reading begun after the change, showing the port disabled, ends the override
  assert.equal(t.view(K, "enable", 11000, NEWER).confirmed, null);
});

test("disable then enable: no reading drawn during the enable's hold shows the port disabled (the deploy 45 wart)", () => {
  // As seen on deploy 45 (sw1 p1): disable at t=0, enable after the 10 s hold, the 15 s cached readings lagging.
  const t = PB.createTracker(PB.LINK_ACTION_OF);
  const disabledAt = AFTER, enabledAt = AFTER + 12000;
  t.confirmed(K, "disabled", 10, 0, disabledAt);
  // a reading from before both presses still shows the port enabled (its badge offers "disable")
  assert.equal(t.view(K, "disable", 1000, disabledAt - 3000).action, "enable");
  t.start(K);
  t.confirmed(K, "enabled", 60, 12000, enabledAt);
  // the same old reading "agrees" with the enable: the enabled state is kept, not handed to the reading
  let v = t.view(K, "disable", 12500, disabledAt - 3000);
  assert.equal(v.confirmed, "enabled");
  // the next reading caught up with the disable only (began between the two presses): still enabled, counting
  v = t.view(K, "enable", 15000, disabledAt + 4000);
  assert.equal(v.confirmed, "enabled");
  assert.equal(v.action, "disable");
  assert.equal(v.text, "57");
  // a reading begun after the enable is believed
  v = t.view(K, "disable", 30000, enabledAt + 1000);
  assert.equal(v.confirmed, null);
  assert.equal(v.action, "disable");
});

test("the link answer is read from its own field", () => {
  assert.deepEqual(PB.readAnswer(200, { link: "disabled", next_in: 10, at: AT }, null, "switch 2 port 9", "link"),
    { kind: "ok", state: "disabled", seconds: 10, after: AFTER, text: "link disabled on switch 2 port 9" });
  // a power answer is not a link confirmation, nor the reverse
  assert.equal(PB.readAnswer(200, { state: "off", at: AT }, null, "port 9", "link").kind, "error");
  assert.equal(PB.readAnswer(200, { link: "enabled", at: AT }, null, "port 9", "power").kind, "error");
  assert.equal(PB.readAnswer(429, {}, "8", "switch 2 port 9", "link").text,
    "the link of switch 2 port 9 was switched a moment ago; it can be switched again in 8 s");
});

function badge(doc, cls, text, action, title = "link up at 1G; MACs seen: aa:00:00:00:00:01; a click disables the port at the switch") {
  const b = doc.createElement("button");
  b.dataset.port = "9"; b.dataset.cls = cls; b.dataset.text = text; b.dataset.action = action; b.dataset.title = title;
  const g = doc.createElement("span"); g.className = "mgmt-link-glyph"; b.appendChild(g);
  return b;
}

test("the badge turns while waiting, then shows the confirmed state with the countdown, then the state", () => {
  const doc = fakeDoc();
  const b = badge(doc, "mgmt-badge-fast", "1G", "disable");
  PB.drawLink(b, { action: "disable", busy: true, disabled: true, seconds: 0, confirmed: null });
  assert.equal(b.querySelector(".mgmt-link-glyph").textContent, PB.BUSY);
  assert.equal(b.className, "mgmt-link-badge mgmt-link-button mgmt-badge-fast mgmt-badge-busy");
  PB.drawLink(b, { action: "enable", busy: false, disabled: true, seconds: 7, confirmed: "disabled" });
  assert.equal(b.querySelector(".mgmt-link-glyph").textContent, "7");
  assert.equal(b.className, "mgmt-link-badge mgmt-link-button mgmt-badge-off mgmt-badge-wait");
  assert.equal(b.title, "link disabled at the switch; the link can be switched again in 7 s");
  assert.equal(b.attrs["aria-label"], "port 9: link disabled at the switch; the link can be switched again in 7 s");
  PB.drawLink(b, { action: "enable", busy: false, disabled: false, seconds: 0, confirmed: "disabled" });
  assert.equal(b.querySelector(".mgmt-link-glyph").textContent, "off");
  assert.equal(b.className, "mgmt-link-badge mgmt-link-button mgmt-badge-off");
  assert.equal(b.title, "link disabled at the switch; a click enables the port at the switch");
  // with no override the reading's badge is drawn
  PB.drawLink(b, { action: "disable", busy: false, disabled: false, seconds: 0, confirmed: null });
  assert.equal(b.querySelector(".mgmt-link-glyph").textContent, "1G");
  assert.equal(b.className, "mgmt-link-badge mgmt-link-button mgmt-badge-fast");
  // the reading's own words: what the badge is, the MACs, what a click does (the review of #168)
  assert.equal(b.title, "link up at 1G; MACs seen: aa:00:00:00:00:01; a click disables the port at the switch");
  assert.equal(b.attrs["aria-label"], "port 9: " + b.title);
});

test("an enabled port with no link yet is the empty badge", () => {
  const doc = fakeDoc();
  const b = badge(doc, "mgmt-badge-off", "off", "enable");
  PB.drawLink(b, { action: "disable", busy: false, disabled: false, seconds: 0, confirmed: "enabled" });
  assert.equal(b.querySelector(".mgmt-link-glyph").textContent, "");
  assert.equal(b.className, "mgmt-link-badge mgmt-link-button mgmt-badge-none");
});

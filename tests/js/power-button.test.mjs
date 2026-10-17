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

const V = (o) => Object.assign({ busy: false, disabled: false, seconds: 0, confirmed: null, stalled: false, text: "⏻" }, o);

test("an untouched port's button is the reading's, ⏻, and can be pressed", () => {
  const t = PB.createTracker();
  assert.deepEqual(t.view(K, "off", 0), V({ action: "off" }));
});

test("from the press on the button shows the state pressed for, turning, and cannot be pressed twice", () => {
  // Tim, 18 Oct 2026: an OFF press showed a red box, then a GREEN box with a countdown (site #170)
  const t = PB.createTracker();
  t.start(K, "off");
  const v = t.view(K, "off", 1000, NEWER);  // even a newer reading cannot end a press that has no answer yet
  assert.deepEqual(v, V({ action: "on", busy: true, disabled: true, confirmed: "off" }));
  assert.equal(t.ticking(1000), true);
});

test("off: turning while no reading follows the confirmation, then the stable look counts the hold down", () => {
  // coord-85: "The countdown should only be shown when the state is stable ... the twirl should appear while
  // transitioning"
  const t = PB.createTracker();
  t.start(K, "off");
  t.confirmed(K, "off", 10, 0, AFTER);
  // the reading (15 s server cache) still says the port is powered: the OFF look, turning, no digits
  assert.deepEqual(t.view(K, "off", 0, OLDER), V({ action: "on", busy: true, disabled: true, confirmed: "off" }));
  assert.equal(t.view(K, "off", 3000, OLDER).busy, true);
  // a reading begun after the confirmation, showing the port off: stable, the hold's seconds left count down
  assert.deepEqual(t.view(K, "on", 4000, NEWER), V({ action: "on", text: "6", disabled: true, seconds: 6 }));
  assert.equal(t.view(K, "on", 9001, NEWER).text, "1");
  assert.deepEqual(t.view(K, "on", 10000, NEWER), V({ action: "on" }));
  assert.equal(t.ticking(10000), false);
});

test("on: the ON look from the press on, never the OFF look while the Pi boots and detection catches up", () => {
  // Tim, 18 Oct 2026: "When turning the device back on I got the green for a few seconds and then it turned red".
  // The reading carries the PoE admin state (poe #24), so a reading after the on shows "off" (the action) whatever
  // the detection status says.
  const t = PB.createTracker();
  t.start(K, "on");
  assert.equal(t.view(K, "on", 0, OLDER).action, "off");
  t.confirmed(K, "on", 60, 500, AFTER);
  for (const now of [500, 5000, 14000]) {
    const v = t.view(K, "on", now, OLDER);
    assert.equal(v.action, "off"); assert.equal(v.busy, true); assert.equal(v.seconds, 0);
  }
  const v = t.view(K, "off", 15000, NEWER);
  assert.equal(v.action, "off"); assert.equal(v.busy, false); assert.equal(v.text, "46");
  assert.equal(t.view(K, "off", 60500, NEWER).disabled, false);
});

test("a reading begun after the confirmation that contradicts it is believed (someone else switched the port)", () => {
  const t = PB.createTracker();
  t.start(K, "off");
  t.confirmed(K, "off", 10, 0, AFTER);
  const v = t.view(K, "off", 20000, NEWER);
  assert.equal(v.confirmed, null);
  assert.equal(v.action, "off");
  assert.equal(v.busy, false);
});

test("an older reading that happens to agree does not end the transition (the deploy 45 wart)", () => {
  const t = PB.createTracker();
  t.start(K, "off");
  t.confirmed(K, "off", 10, 0, AFTER);
  assert.equal(t.view(K, "on", 1000, OLDER).confirmed, "off");
  // a reading stamped the same millisecond as the confirmation is not known to follow it
  assert.equal(t.view(K, "on", 1000, AFTER).busy, true);
  assert.equal(t.view(K, "on", 1000, AFTER + 1).busy, false);
});

test("no reading within SETTLE_MS: the twirl stops once, loud, on what the switch answered", () => {
  const t = PB.createTracker();
  t.start(K, "off");
  t.confirmed(K, "off", 10, 0, AFTER);
  assert.equal(t.view(K, "off", PB.SETTLE_MS, NaN).busy, true);
  const v = t.view(K, "off", PB.SETTLE_MS + 1, NaN);
  assert.deepEqual(v, V({ action: "on", confirmed: "off", stalled: true }));
  // said once; the switch's answer stays drawn until a reading begun after it arrives
  assert.equal(t.view(K, "off", PB.SETTLE_MS + 500, NaN).stalled, false);
  assert.equal(t.view(K, "off", PB.SETTLE_MS + 500, OLDER).action, "on");
  assert.equal(t.view(K, "off", PB.SETTLE_MS + 900, NEWER).confirmed, null);
});

test("a press inside someone else's hold counts that hold down: never silently dropped", () => {
  const t = PB.createTracker();
  t.start(K, "on");
  t.held(K, 42, 0);
  assert.deepEqual(t.view(K, "off", 0), V({ action: "off", text: "42", disabled: true, seconds: 42 }));
});

test("a failed press frees the button at once and draws the reading again", () => {
  const t = PB.createTracker();
  t.start(K, "off");
  t.failed(K);
  assert.deepEqual(t.view(K, "on", 0), V({ action: "on" }));
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

test("a link press shows the disabled look, turning, until a reading follows; then the short hold counts", () => {
  const t = PB.createTracker(PB.LINK_ACTION_OF);
  t.start(K, "disabled");
  let v = t.view(K, "disable", 0);
  assert.equal(v.busy, true); assert.equal(v.confirmed, "disabled"); assert.equal(v.action, "enable");
  t.confirmed(K, "disabled", 10, 0, AFTER);
  v = t.view(K, "disable", 0, OLDER);
  assert.equal(v.busy, true); assert.equal(v.confirmed, "disabled"); assert.equal(v.seconds, 0);
  // a reading begun after the change, showing the port disabled: stable, counting
  v = t.view(K, "enable", 3000, NEWER);
  assert.equal(v.confirmed, null); assert.equal(v.busy, false); assert.equal(v.seconds, 7); assert.equal(v.action, "enable");
});

test("disable then enable: no reading drawn during the enable shows the port disabled (the deploy 45 wart)", () => {
  // As seen on deploy 45 (sw1 p1): disable at t=0, enable after the 10 s hold, the 15 s cached readings lagging.
  const t = PB.createTracker(PB.LINK_ACTION_OF);
  const disabledAt = AFTER, enabledAt = AFTER + 12000;
  t.start(K, "disabled");
  t.confirmed(K, "disabled", 10, 0, disabledAt);
  // a reading from before both presses still shows the port enabled: the disabled look, turning
  assert.equal(t.view(K, "disable", 1000, disabledAt - 3000).action, "enable");
  t.start(K, "enabled");
  t.confirmed(K, "enabled", 60, 12000, enabledAt);
  // the same old reading "agrees" with the enable: kept as the press's, turning
  let v = t.view(K, "disable", 12500, disabledAt - 3000);
  assert.equal(v.confirmed, "enabled"); assert.equal(v.busy, true);
  // the next reading caught up with the disable only (began between the two presses): still enabled, turning
  v = t.view(K, "enable", 15000, disabledAt + 4000);
  assert.equal(v.confirmed, "enabled"); assert.equal(v.action, "disable"); assert.equal(v.busy, true);
  // a reading begun after the enable is believed, and the hold's seconds left count on it
  v = t.view(K, "disable", 30000, enabledAt + 1000);
  assert.equal(v.confirmed, null); assert.equal(v.action, "disable"); assert.equal(v.text, "42");
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

// --- the page's glue: what a press asks first (round 6, item 3) ---

function fakePage(button) {
  const asked = [], posted = [];
  const doc = fakeDoc();
  doc.querySelectorAll = (sel) => (sel === button.selector ? [button.el] : []);
  doc.addEventListener = (type, fn) => { doc.click = fn; };
  const win = {
    document: doc, confirm: (q) => { asked.push(q); return false; },
    fetch: (url, opts) => { posted.push([url, JSON.parse(opts.body)]); return new Promise(() => {}); },
    setInterval: () => 1, clearInterval: () => {}, setTimeout: () => 1, clearTimeout: () => {},
  };
  return { win, doc, asked, posted };
}

function pressable(doc, cls, action) {
  const b = doc.createElement("button");
  Object.assign(b.dataset, { switch: "1", port: "2", action, powers: "pi-sw1-p2" });
  b.closest = (sel) => (sel === "." + cls ? b : sel === ".mgmt-switch" ? null : null);
  const g = doc.createElement("span"); g.className = cls === "mgmt-poe-button" ? "mgmt-poe-glyph" : "mgmt-link-glyph";
  b.appendChild(g);
  return b;
}

test("a PoE press asks no question (Tim, 18 Oct 2026: no confirmation popup for power cycling)", () => {
  for (const action of ["off", "on"]) {
    const holder = { selector: ".mgmt-poe-button" };
    const page = fakePage(holder);
    holder.el = pressable(page.doc, "mgmt-poe-button", action);
    PB.attach(page.win, { power: "/snmp/power", link: "/snmp/link" }, () => {});
    page.doc.click({ target: holder.el });
    assert.deepEqual(page.asked, []);
    assert.deepEqual(page.posted, [["/snmp/power", { on: action === "on", switch: 1, port: "2" }]]);
    // drawn at once as the state pressed for, turning
    assert.match(holder.el.className, new RegExp("mgmt-poe-to-" + (action === "off" ? "on" : "off") + " mgmt-poe-busy"));
  }
});

test("disabling a link still asks first; declining sends nothing", () => {
  const holder = { selector: ".mgmt-link-button" };
  const page = fakePage(holder);
  holder.el = pressable(page.doc, "mgmt-link-button", "disable");
  PB.attach(page.win, { power: "/snmp/power", link: "/snmp/link" }, () => {});
  page.doc.click({ target: holder.el });
  assert.equal(page.asked.length, 1);
  assert.match(page.asked[0], /^Disable the link of switch 1 port 2\?/);
  assert.deepEqual(page.posted, []);
});

test("a press the site never answers stops turning after ANSWER_MS, loud", async () => {
  const holder = { selector: ".mgmt-poe-button" };
  const page = fakePage(holder);
  holder.el = pressable(page.doc, "mgmt-poe-button", "off");
  const stack = page.doc.createElement("div"); page.doc.byId["mgmt-toasts"] = stack;
  const timers = [];
  page.win.setTimeout = (fn, ms) => { timers.push([fn, ms]); return timers.length; };
  class Abort { constructor() { this.signal = { aborted: false }; this.rejects = []; }
                abort() { this.signal.aborted = true; this.rejects.forEach((r) => r(new Error("AbortError"))); } }
  let ctl = null;
  page.win.AbortController = function () { ctl = new Abort(); return ctl; };
  page.win.fetch = (url, opts) => new Promise((_, reject) => { ctl.rejects.push(reject); });
  PB.attach(page.win, { power: "/snmp/power", link: "/snmp/link" }, () => {});
  page.doc.click({ target: holder.el });
  assert.match(holder.el.className, /mgmt-poe-busy/);
  const late = timers.find(([, ms]) => ms === PB.ANSWER_MS);
  assert.ok(late, "a timer for the answer");
  late[0]();
  await new Promise((r) => setImmediate(r));
  assert.doesNotMatch(holder.el.className, /mgmt-poe-busy/);
  assert.equal(holder.el.disabled, false);
  assert.match(stack.children[0].children[0].textContent, /no answer from the site in 30 s/);
});

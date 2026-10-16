// Behaviour tests for management's switch-events.js (pushed link changes), run with `node --test`.
//
// The script is loaded as a CommonJS module and given a small fake of the page: a port's table row and its
// front-panel mark, and a WebSocket whose server side the tests drive.
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import assert from "node:assert/strict";

const here = dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const { applyRecord, listen } = require(
  join(here, "..", "..", "management", "src", "management", "static", "management", "switch-events.js"));

class Element {
  constructor(attrs = {}) {
    Object.assign(this, { textContent: "", className: "", title: "", attrs: { ...attrs }, kids: {}, parentNode: null,
                          dataset: {}, tagName: "SPAN" });
  }
  getAttribute(name) { return this.attrs[name] ?? null; }
  setAttribute(name, value) { this.attrs[name] = value; }
  querySelector(selector) { return this.kids[selector]; }
}

const TITLE_UP = "port 9 · Pi 5 2 GB 1234, Acorn, operational, leased an address here 2026-10-14 10:00:00 · link up 1G · PoE delivering power 8.6 W · VLAN 2209 · as it should be: PoE";

// One row (port 9 of switch 2) and its panel box, as the served page has them: the Link cell (its badge, a button on
// an access port, and its words for a screen reader), the box's classes and title.
function badgeOf(up, speed, disabled) {
  if (disabled) { return ["mgmt-badge-off", "off", "link disabled at the switch"]; }
  if (!up) { return ["mgmt-badge-none", "", "no link"]; }
  return ["mgmt-badge-fast", speed, `link up at ${speed}`];
}
function page({ up = true, speed = "1G", title = TITLE_UP, disabled = false }) {
  const row = new Element();
  const link = new Element({ "data-sort": up ? "1010" : "0" });
  const [cls, text, words] = badgeOf(up, speed, disabled);
  link.className = "mgmt-mark c-link";
  link.title = words;
  const badge = Object.assign(new Element(), { tagName: "BUTTON", className: `mgmt-link-badge mgmt-link-button ${cls}`,
                                               dataset: { cls, text }, title: words });
  badge.kids[".mgmt-link-glyph"] = Object.assign(new Element(), { textContent: text });
  link.kids[".mgmt-link-badge"] = badge;
  link.kids[".mgmt-sr"] = Object.assign(new Element(), { textContent: " " + words });
  row.kids[".c-link"] = link;
  const box = new Element({ class: `mgmt-port mgmt-box-operational mgmt-link-${up ? "up" : "down"}` });
  box.kids.title = Object.assign(new Element(), { textContent: title });
  box.parentNode = new Element({ "aria-label": title });
  const byId = { "switch-2-port-9": row, "switch-2-panel-9": box };
  const glyph = badge.kids[".mgmt-link-glyph"];
  return { row, link, badge, glyph, box, doc: { getElementById: (id) => byId[id] ?? null } };
}

const record = (link, over = {}) => ({ schema: 1, switch: 2, port: 9, hostname: "pi-sw2-p9", link, at: "2026-10-14T01:02:03+00:00", ...over });

test("a link-down record empties the row's Link badge and dashes the box's edge", () => {
  const p = page({});
  assert.equal(applyRecord(p.doc, record("down")), true);
  assert.equal(p.glyph.textContent, "");
  assert.equal(p.badge.className, "mgmt-link-badge mgmt-link-button mgmt-badge-none");
  assert.deepEqual(p.badge.dataset, { cls: "mgmt-badge-none", text: "", title: "no link" });  // power-button.js redraws from these
  assert.equal(p.link.kids[".mgmt-sr"].textContent, " no link");
  assert.equal(p.link.title, "no link");
  assert.equal(p.badge.title, "no link");
  assert.equal(p.link.attrs["data-sort"], "0");
  // the box keeps its State fill; only its edge changes
  assert.equal(p.box.attrs.class, "mgmt-port mgmt-box-operational mgmt-link-down");
  assert.equal(p.box.kids.title.textContent, TITLE_UP.replace("link up 1G", "link down"));
  assert.equal(p.box.parentNode.attrs["aria-label"], p.box.kids.title.textContent);
});

test("a link-up record lights the link light; the badge is a green ↑ until a reading gives the speed", () => {
  const down = TITLE_UP.replace("link up 1G", "link down");
  const p = page({ up: false, speed: "", title: down });
  applyRecord(p.doc, record("up"));
  assert.equal(p.glyph.textContent, "↑");
  assert.equal(p.badge.className, "mgmt-link-badge mgmt-link-button mgmt-badge-fast");
  assert.equal(p.link.title, "link up; the speed at the next reading");
  assert.equal(p.box.attrs.class, "mgmt-port mgmt-box-operational mgmt-link-up");
  assert.equal(p.box.kids.title.textContent, down.replace("link down", "link up"));
  const again = page({});
  applyRecord(again.doc, record("up"));
  assert.equal(again.glyph.textContent, "1G");
  assert.equal(again.link.attrs["data-sort"], "1010");
});

test("a port disabled at the switch keeps its disabled badge whatever a link record says", () => {
  const p = page({ up: false, disabled: true });
  applyRecord(p.doc, record("up"));
  assert.equal(p.glyph.textContent, "off");
  assert.equal(p.badge.className, "mgmt-link-badge mgmt-link-button mgmt-badge-off");
  assert.equal(p.link.title, "link disabled at the switch");
});

test("a pressed badge's turning or countdown class is left for power-button.js to draw again", () => {
  const p = page({});
  p.badge.className += " mgmt-badge-busy";
  applyRecord(p.doc, record("down"));
  // the badge classes are replaced; power-button.js redraws busy/wait from its tracker every 250 ms
  assert.equal(p.badge.dataset.cls, "mgmt-badge-none");
});

test("a title's other words are left alone, a machine's words that say 'link' too", () => {
  const title = "port 9 · link up 1G · PoE off · not as it should be: link down under Pi 5 2 GB 1 (operational)";
  const p = page({ title });
  applyRecord(p.doc, record("down"));
  assert.equal(p.box.kids.title.textContent,
    "port 9 · link down · PoE off · not as it should be: link down under Pi 5 2 GB 1 (operational)");
});

test("a bounced record, another port, or garbage draws nothing", () => {
  const p = page({});
  assert.equal(applyRecord(p.doc, record("bounced")), false);
  assert.equal(applyRecord(p.doc, record("down", { port: 10 })), false);
  assert.equal(applyRecord(p.doc, null), false);
  assert.equal(p.glyph.textContent, "1G");
  assert.equal(p.box.attrs.class, "mgmt-port mgmt-box-operational mgmt-link-up");
});

test("a row without a panel box still gets its link cell", () => {
  const p = page({});
  const doc = { getElementById: (id) => (id === "switch-2-panel-9" ? null : p.doc.getElementById(id)) };
  assert.equal(applyRecord(doc, record("down")), true);
  assert.equal(p.glyph.textContent, "");
});

class Socket {
  constructor(env, url) { this.url = url; env.sockets.push(this); }
}

function env(protocol = "https:") {
  const e = { sockets: [], timers: [], now: 0, location: { protocol, host: "example.test" } };
  e.WebSocket = class extends Socket { constructor(url) { super(e, url); } };
  e.setTimeout = (fn, ms) => { e.timers.push({ fn, at: e.now + ms }); return e.timers.length; };
  e.clearTimeout = (id) => { if (id) e.timers[id - 1].fn = () => {}; };
  e.advance = (ms) => { e.now += ms; e.timers.filter((t) => t.at <= e.now && !t.done).forEach((t) => { t.done = true; t.fn(); }); };
  return e;
}

test("the socket is wss on https, ws on http, and hands each record on", () => {
  for (const [protocol, scheme] of [["https:", "wss"], ["http:", "ws"]]) {
    const e = env(protocol);
    const got = [];
    listen(e, (r) => got.push(r));
    assert.equal(e.sockets[0].url, `${scheme}://example.test/ws/switches/`);
    e.sockets[0].onmessage({ data: JSON.stringify(record("down")) });
    e.sockets[0].onmessage({ data: "{not json" });
    assert.deepEqual(got, [record("down")]);
  }
});

test("it reconnects with a doubling backoff that resets only after a stable connection", () => {
  const e = env();
  listen(e, () => {});
  e.sockets[0].onclose();
  e.advance(999); assert.equal(e.sockets.length, 1);
  e.advance(1); assert.equal(e.sockets.length, 2);
  e.sockets[1].onclose();  // dropped at once: 2 s now
  e.advance(1999); assert.equal(e.sockets.length, 2);
  e.advance(1); assert.equal(e.sockets.length, 3);
  e.sockets[2].onopen(); e.advance(30000);  // stayed up 30 s: back to 1 s
  e.sockets[2].onclose();
  e.advance(1000); assert.equal(e.sockets.length, 4);
});

// --- the keeper: the 15 s refresh must not undo a pushed change with an older reading ---

const { createKeeper } = require(
  join(here, "..", "..", "management", "src", "management", "static", "management", "switch-events.js"));

// What tick() does: rebuild the row and the box from a reading (the port is up), then reapply.
function rebuild(p) {
  p.glyph.textContent = "1G"; p.badge.className = "mgmt-link-badge mgmt-link-button mgmt-badge-fast";
  p.badge.dataset = { cls: "mgmt-badge-fast", text: "1G" };
  p.box.attrs.class = "mgmt-port mgmt-box-operational mgmt-link-up";
}
const reading = (goodAt) => [{ index: 2, good_at_iso: goodAt }];

test("a record, then a tick with an older reading, keeps the pushed state", () => {
  const p = page({});
  const keeper = createKeeper();
  assert.equal(keeper.apply(p.doc, record("down", { at: "2026-10-14T01:02:03+00:00" })), true);
  rebuild(p);  // the cached reading, taken before the change
  keeper.reapply(p.doc, reading("2026-10-14T01:02:00+00:00"));
  assert.equal(p.glyph.textContent, "");
  assert.match(p.box.attrs.class, /mgmt-link-down/);
  // and again on the next older reading: still kept
  rebuild(p);
  keeper.reapply(p.doc, reading("2026-10-14T01:02:02+00:00"));
  assert.equal(p.glyph.textContent, "");
  assert.equal(keeper.size(), 1);
});

test("a tick with a newer reading replaces the pushed state and drops the record", () => {
  const p = page({});
  const keeper = createKeeper();
  keeper.apply(p.doc, record("down", { at: "2026-10-14T01:02:03+00:00" }));
  rebuild(p);  // the reading says up (the port came back, or the change was wrong)
  keeper.reapply(p.doc, reading("2026-10-14T01:02:04+00:00"));
  assert.equal(p.glyph.textContent, "1G");
  assert.equal(p.box.attrs.class, "mgmt-port mgmt-box-operational mgmt-link-up");
  assert.equal(keeper.size(), 0);
  // a reading exactly as old as the record has caught up with it too
  keeper.apply(p.doc, record("down", { at: "2026-10-14T01:03:00+00:00" }));
  rebuild(p);
  keeper.reapply(p.doc, reading("2026-10-14T01:03:00+00:00"));
  assert.equal(p.glyph.textContent, "1G");
  assert.equal(keeper.size(), 0);
});

test("the keeper keeps the newest record per port, ignores records the page cannot draw, and compares per switch", () => {
  const p = page({});
  const keeper = createKeeper();
  keeper.apply(p.doc, record("down", { at: "2026-10-14T01:05:00+00:00" }));
  assert.equal(keeper.apply(p.doc, record("up", { at: "2026-10-14T01:04:00+00:00" })), false);  // older: ignored
  assert.equal(keeper.apply(p.doc, record("bounced")), false);
  assert.equal(keeper.apply(p.doc, record("down", { port: 10 })), false);  // no such row: nothing to keep
  assert.equal(keeper.size(), 1);
  rebuild(p);
  // another switch's fresh reading says nothing about switch 2; no reading for switch 2 at all: forget it
  keeper.reapply(p.doc, [{ index: 1, good_at_iso: "2026-10-14T02:00:00+00:00" }]);
  assert.equal(p.glyph.textContent, "1G");
  assert.equal(keeper.size(), 0);
});

test("a record redraws the box in the narrow drawing too, and keeps the MACs in the link badge's title", () => {
  const p = page({});
  p.link.title = "link up at 1G; MACs seen: aa:00:00:00:00:01";
  const twin = new Element({ class: "mgmt-port mgmt-box-operational mgmt-link-up" });
  twin.kids.title = Object.assign(new Element(), { textContent: TITLE_UP });
  twin.parentNode = new Element({ "aria-label": TITLE_UP });
  const doc = { getElementById: (id) => (id === "switch-2-panel-9-n" ? twin : p.doc.getElementById(id)) };
  applyRecord(doc, record("down"));
  assert.equal(p.link.title, "no link; MACs seen: aa:00:00:00:00:01");
  assert.equal(p.badge.title, p.link.title);
  assert.equal(twin.attrs.class, "mgmt-port mgmt-box-operational mgmt-link-down");
  assert.equal(twin.kids.title.textContent, TITLE_UP.replace("link up 1G", "link down"));
});

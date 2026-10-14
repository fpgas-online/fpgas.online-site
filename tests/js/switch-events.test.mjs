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
const { applyRecord, stateFor, listen } = require(
  join(here, "..", "..", "management", "src", "management", "static", "management", "switch-events.js"));

// panel.STATES: [state, glyph, words]
const STATES = [
  ["power", "P", "link up, PoE delivering"],
  ["starting", "*", "link down, PoE delivering (the device is powered, its link not up yet)"],
  ["up", "L", "link up, no PoE drawn"],
  ["searching", "s", "link down, PoE on and searching"],
  ["off", "o", "link down, PoE off"],
  ["down", "–", "link down, no PoE on the port"],
  ["fault", "F", "PoE fault"],
  ["unknown", "?", "PoE state not known (the switch gave none it names, or it was not read)"],
];

class Element {
  constructor(attrs = {}) {
    Object.assign(this, { textContent: "", className: "", attrs: { ...attrs }, kids: {}, parentNode: null });
  }
  getAttribute(name) { return this.attrs[name] ?? null; }
  setAttribute(name, value) { this.attrs[name] = value; }
  querySelector(selector) { return this.kids[selector]; }
}

// One row (port 9 of switch 2) and its panel mark, as the served page has them.
function page({ link = "up 1G", poe = "8.6W", state = "power", title = "port 9 · link up, PoE delivering · VLAN 2109" }) {
  const row = new Element();
  row.children = [new Element(), new Element(), Object.assign(new Element(), { textContent: link, className: "mgmt-up" })];
  row.kids[".mgmt-poe"] = Object.assign(new Element(), { textContent: poe });
  const mark = new Element({ class: `mgmt-port mgmt-port-${state} mgmt-port-known` });
  mark.kids.title = Object.assign(new Element(), { textContent: title });
  mark.kids[".mgmt-glyph"] = Object.assign(new Element(), { textContent: STATES.find((s) => s[0] === state)[1] });
  mark.parentNode = new Element({ "aria-label": title });
  const byId = { "switch-2-port-9": row, "switch-2-panel-9": mark };
  return { row, mark, doc: { getElementById: (id) => byId[id] ?? null } };
}

const record = (link, over = {}) => ({ schema: 1, switch: 2, port: 9, hostname: "pi-sw2-p9", link, at: "2026-10-14T01:02:03+00:00", ...over });

test("a link-down record changes the row's link cell and the port's panel mark", () => {
  const p = page({});
  assert.equal(applyRecord(p.doc, STATES, record("down")), true);
  const cell = p.row.children[2];
  assert.equal(cell.textContent, "down");
  assert.equal(cell.className, "mgmt-down");
  // link down with PoE still delivering is panel.py's "starting"; other classes (the inventory mark) are kept
  assert.equal(p.mark.attrs.class, "mgmt-port mgmt-port-known mgmt-port-starting");
  assert.equal(p.mark.kids[".mgmt-glyph"].textContent, "*");
  assert.equal(p.mark.kids.title.textContent, `port 9 · ${STATES[1][2]} · VLAN 2109`);
  assert.equal(p.mark.parentNode.attrs["aria-label"], p.mark.kids.title.textContent);
});

test("a link-up record gives the state the PoE part says, and keeps a speed the cell already shows", () => {
  const p = page({ link: "down", poe: "searching", state: "searching", title: "port 9 · link down, PoE on and searching" });
  p.row.children[2].className = "mgmt-down";
  applyRecord(p.doc, STATES, record("up"));
  assert.equal(p.row.children[2].textContent, "up");
  assert.equal(p.row.children[2].className, "mgmt-up");
  assert.match(p.mark.attrs.class, /mgmt-port-up/);
  assert.equal(p.mark.kids[".mgmt-glyph"].textContent, "L");

  const again = page({ link: "up 1G" });
  applyRecord(again.doc, STATES, record("up"));
  assert.equal(again.row.children[2].textContent, "up 1G");
});

test("a state that is unknown carries the link in its title", () => {
  const p = page({ poe: "not known", state: "unknown", title: "port 9 · PoE state not known (x) · link up · VLAN 2109" });
  applyRecord(p.doc, STATES, record("down"));
  assert.equal(p.mark.attrs.class.includes("mgmt-port-unknown"), true);
  assert.equal(p.mark.kids.title.textContent, "port 9 · PoE state not known (the switch gave none it names, or it was not read) · link down · VLAN 2109");
});

test("stateFor is panel.py's state() for each PoE the row can show", () => {
  const cases = [
    ["8.6W", true, "power"], ["on", false, "starting"], ["searching", true, "up"], ["searching", false, "searching"],
    ["off", true, "up"], ["off", false, "off"], ["fault", true, "fault"], ["not known", false, "unknown"],
  ];
  for (const [text, up, want] of cases) assert.equal(stateFor(text, up, "up"), want, `${text} ${up}`);
  // no PoE: a port without it, or a PoE port whose reading is missing
  assert.equal(stateFor("no PoE", false, "up"), "down");
  assert.equal(stateFor("no PoE", true, "down"), "up");
  assert.equal(stateFor("no PoE", true, "unknown"), "unknown");
});

test("a bounced record, another port, or garbage draws nothing", () => {
  const p = page({});
  assert.equal(applyRecord(p.doc, STATES, record("bounced")), false);
  assert.equal(applyRecord(p.doc, STATES, record("down", { port: 10 })), false);
  assert.equal(applyRecord(p.doc, STATES, null), false);
  assert.equal(p.row.children[2].textContent, "up 1G");
  assert.equal(p.mark.attrs.class, "mgmt-port mgmt-port-power mgmt-port-known");
});

test("a row without a panel mark still gets its link cell", () => {
  const p = page({});
  const doc = { getElementById: (id) => (id === "switch-2-panel-9" ? null : p.doc.getElementById(id)) };
  assert.equal(applyRecord(doc, STATES, record("down")), true);
  assert.equal(p.row.children[2].textContent, "down");
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

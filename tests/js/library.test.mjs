// Behaviour tests for ttsite's library.js (the board page's VGA library panel), run with `node --test`. The
// matching, paging and badge rules are plain functions; renderPage() and the panel controller get a small fake
// page and a fake fetch.
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import assert from "node:assert/strict";

const here = dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const L = require(join(here, "..", "..", "ttsite", "src", "ttsite", "static", "ttsite", "library.js"));
const INDEX = JSON.parse(readFileSync(join(here, "..", "data", "fpga-library.json"), "utf8"));
const ROWS = INDEX.designs;
const BASE = "https://data.wafer.space/big-storage/tinytapeout-vga-videos/";
const WIRE = { schema: 1, generated: INDEX.generated, base_url: BASE, designs: ROWS };

// --- a small fake page: elements with children, textContent, properties and onclick/oninput/onchange

class FakeNode {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.parentNode = null;
    this._text = "";
    this.hidden = false;
    this.disabled = false;
    this.className = "";
  }
  get firstChild() { return this.children[0] || null; }
  appendChild(c) {
    if (typeof c === "string") c = Object.assign(new FakeNode("#text"), { _text: c });
    if (c.parentNode) c.parentNode.removeChild(c);
    c.parentNode = this;
    this.children.push(c);
    return c;
  }
  append(...cs) { cs.forEach((c) => this.appendChild(c)); }
  removeChild(c) {
    this.children = this.children.filter((x) => x !== c);
    c.parentNode = null;
    return c;
  }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  set textContent(t) {
    this.children.forEach((c) => { c.parentNode = null; });
    this.children = [];
    this._text = String(t);
  }
  setAttribute(k, v) { this[k] = String(v); }
  get innerHTML() { throw new Error("library.js must not use innerHTML"); }
  set innerHTML(v) { throw new Error("library.js must not use innerHTML"); }
  // every descendant, depth first
  all() { return this.children.flatMap((c) => [c, ...c.all()]); }
  find(pred) { return this.all().filter(pred); }
}

const fakeDoc = () => ({ createElement: (tag) => new FakeNode(tag) });
const byClass = (root, cls) => root.find((n) => (" " + n.className + " ").includes(" " + cls + " "));
const buttons = (root, text) => root.find((n) => n.tagName === "BUTTON" && (text === undefined || n.textContent === text));

// --- matching, shuttles, filter, paging

test("matches all terms over title author shuttle id macro", () => {
  const e = ROWS[3]; // Sea Battle VGA Game, Yuri Panchul, ttihp25a, a hashed id
  assert.equal(L.matches(e, ""), true);
  assert.equal(L.matches(e, "   "), true);
  assert.equal(L.matches(e, "sea"), true);           // title
  assert.equal(L.matches(e, "PANCHUL"), true);       // author, any case
  assert.equal(L.matches(e, "ttihp25a"), true);      // shuttle
  assert.equal(L.matches(e, "09a2562b"), true);      // id only
  assert.equal(L.matches(e, "battle_vga_game"), true); // macro only
  assert.equal(L.matches(e, "sea  yuri\tttihp"), true); // every term, any whitespace
  assert.equal(L.matches(e, "sea flappy"), false);   // one term misses
  assert.equal(L.matches(ROWS[0], "dragoste patterns"), true);
});

test("shuttles follow first appearance in the index", () => {
  assert.deepEqual(L.shuttles(ROWS), ["ttsky25b", "ttsky25a", "ttihp26a", "ttihp25a", "tt05"]);
  const again = [ROWS[1], ROWS[0], ROWS[1], ROWS[4]];
  assert.deepEqual(L.shuttles(again), ["ttsky25a", "ttsky25b", "tt05"]);
  assert.deepEqual(L.shuttles([]), []);
});

test("filter applies the query and the shuttle, keeping index order", () => {
  assert.deepEqual(L.filter(ROWS, "", "").map((e) => e.id), ROWS.map((e) => e.id));
  assert.deepEqual(L.filter(ROWS, "vga example", "").map((e) => e.id), ["ttsky25a_vga_example", "ttihp26a_vga_example"]);
  assert.deepEqual(L.filter(ROWS, "vga example", "ttihp26a").map((e) => e.id), ["ttihp26a_vga_example"]);
  assert.deepEqual(L.filter(ROWS, "", "tt05").map((e) => e.id), ["tt05_flappy_vga_cutout1"]);
  assert.deepEqual(L.filter(ROWS, "nothing-like-this", ""), []);
});

test("paging clamps and resets", () => {
  assert.deepEqual(L.page([], 7), { items: [], pageNo: 1, pages: 1 });
  const thirty = Array.from({ length: 30 }, (_, i) => i);
  const p = L.page(thirty, 9);
  assert.equal(p.pageNo, 2); assert.equal(p.pages, 2); assert.equal(p.items.length, 6);
  assert.deepEqual(p.items, [24, 25, 26, 27, 28, 29]);
  assert.deepEqual(L.page(thirty, 1).items, thirty.slice(0, 24));
  assert.equal(L.page(thirty, 0).pageNo, 1);
  assert.equal(L.page(thirty, -3).pageNo, 1);
  assert.equal(L.page(thirty, NaN).pageNo, 1);
  assert.equal(L.page(thirty, 2, 10).pages, 3);
  assert.equal(L.page(Array.from({ length: 24 }), 2).pages, 1);
});

// --- badges: the spec's table, and the Commander's texts (tt-commander-app src/model/fpgaLibrary.ts)

test("badges match the spec table", () => {
  assert.deepEqual(L.badges(ROWS[4]), [
    { text: "timing: 17.08 MHz < 25.175 MHz", cls: "warn" },
    { text: "not hardware-validated", cls: "info" },
  ]);
  const b0 = L.badges(ROWS[0]);
  assert.deepEqual(b0, [
    { text: "not hardware-validated", cls: "info" },
    { text: "video: barely-moving", cls: "info" },
    { text: "inputs set: 0x01", cls: "info" },
  ]);
  // Ruling AN: the main clock passes, another clock or path fails
  const secondary = L.badges({ ...ROWS[4], fmax_hz: 25175000 });
  assert.deepEqual(secondary[0], { text: "timing not met on a secondary clock or path", cls: "warn" });
  assert.equal(secondary.some((b) => b.text.includes("MHz <")), false);
  assert.equal(L.badges({ ...ROWS[4], fmax_hz: 40000000 })[0].text, "timing not met on a secondary clock or path");
  // Ruling AH: an all-zero ui_in is not "inputs set", whatever the mask
  const zero = L.badges({ ...ROWS[0], ui_in: 0, ui_in_mask: 3 });
  assert.equal(zero.some((b) => b.text.startsWith("inputs set")), false);
  // lowercase hex, two digits
  assert.equal(L.badges({ ...ROWS[0], ui_in: 0xab, ui_in_mask: 0xff }).at(-1).text, "inputs set: 0xab");
  // the rest of the table, in the Commander's order
  assert.deepEqual(
    L.badges({ ...ROWS[1], validation: "mcu", qspi: "emulated", verdict: "static" }),
    [
      { text: "validated at MCU speed only", cls: "info" },
      { text: "QSPI emulated", cls: "info" },
      { text: "video: static", cls: "info" },
    ],
  );
  assert.deepEqual(L.badges({ ...ROWS[1], validation: "full-speed" }), []);
  // only timing is a warning in the fixture, which has no latch designs
  for (const e of ROWS) {
    for (const b of L.badges(e)) assert.equal(b.cls === "warn", b.text.startsWith("timing"));
  }
});

// Ruling BJ: PDK latch or clock-gate cells are a warning, after the timing one
const LATCH = { text: "PDK latch cells: may not behave as on silicon", cls: "warn" };

test("badges warn about PDK latch cells", () => {
  assert.deepEqual(L.badges({ ...ROWS[1], validation: "full-speed", pdk_latches: true }), [LATCH]);
  assert.deepEqual(L.badges({ ...ROWS[1], validation: "full-speed", pdk_latches: false }), []);
  assert.deepEqual(L.badges({ ...ROWS[4], pdk_latches: true }), [
    { text: "timing: 17.08 MHz < 25.175 MHz", cls: "warn" },
    LATCH,
    { text: "not hardware-validated", cls: "info" },
  ]);
  // only a real true counts
  assert.deepEqual(L.badges({ ...ROWS[1], validation: "full-speed", pdk_latches: "true" }), []);
});

// Ruling CH: a nullable bool; only a real true is a warning, after the PDK latch one and before the validation ones
const SYNTH = { text: "Board picture may differ from the preview", cls: "warn" };

test("badges warn when the synthesised netlist differs from the preview (synthesis_differs)", () => {
  assert.deepEqual(L.badges({ ...ROWS[1], validation: "full-speed", synthesis_differs: true }), [SYNTH]);
  for (const v of [false, null, "true", 1, "yes"]) {
    assert.deepEqual(L.badges({ ...ROWS[1], validation: "full-speed", synthesis_differs: v }), [], String(v));
  }
  // absent
  assert.deepEqual(L.badges({ ...ROWS[1], validation: "full-speed" }), []);
  // order: timing, pdk_latches, synthesis_differs, then validation
  assert.deepEqual(L.badges({ ...ROWS[4], pdk_latches: true, synthesis_differs: true }), [
    { text: "timing: 17.08 MHz < 25.175 MHz", cls: "warn" },
    LATCH,
    SYNTH,
    { text: "not hardware-validated", cls: "info" },
  ]);
});

test("renderPage: a latch design gets the warn badge and Load stays enabled", () => {
  const doc = fakeDoc();
  const box = doc.createElement("div");
  let loaded = null;
  const designs = ROWS.map((e) => (e.id === "ttihp25a_yuri_panchul_sea_battl_09a2562b" ? { ...e, pdk_latches: true } : e));
  L.renderPage(box, ready({ designs, onLoad: (e) => { loaded = e.id; } }), doc);
  const card = byClass(box, "tt-library-card").find((c) => c.textContent.includes("Sea Battle"));
  assert.ok(byClass(card, "tt-badge--warn").map((b) => b.textContent).includes(LATCH.text));
  const load = buttons(card, "Load")[0];
  assert.equal(load.disabled, false);
  load.onclick();
  assert.equal(loaded, "ttihp25a_yuri_panchul_sea_battl_09a2562b");
});

test("assetUrl joins archive paths onto the base and refuses anything else", () => {
  assert.equal(L.assetUrl(BASE, ROWS[0].poster), BASE + ROWS[0].poster);
  assert.equal(L.assetUrl(BASE, null), null);
  assert.equal(L.assetUrl(BASE, ""), null);
  assert.equal(L.assetUrl(BASE, "https://evil.example/x.png"), null);
  assert.equal(L.assetUrl(BASE, "//evil.example/x.png"), null);
  assert.equal(L.assetUrl(BASE, "/etc/passwd"), null);
  assert.equal(L.assetUrl(BASE, "tt05/tt_um_x/../../x.png"), null);
  assert.equal(L.assetUrl(BASE, "tt05/tt_um_x/a b.png"), null);
  assert.equal(L.assetUrl("http://data.wafer.space/", ROWS[0].poster), null);
  assert.equal(L.assetUrl("https://data.wafer.space", ROWS[0].poster), null);
  assert.equal(L.assetUrl(undefined, ROWS[0].poster), null);
});

// --- renderPage

const ready = (o = {}) => ({
  kind: "ready", designs: ROWS, baseUrl: BASE, query: "", shuttle: "", pageNo: 1, onBoard: [], enabled: null,
  busy: false, onQuery() {}, onShuttle() {}, onPage() {}, onLoad() {}, ...o,
});

test("renderPage draws each state", () => {
  const doc = fakeDoc();
  const box = doc.createElement("div");
  L.renderPage(box, { kind: "too-old" }, doc);
  assert.equal(box.textContent, "This board's software is too old for the VGA library.");
  L.renderPage(box, { kind: "loading" }, doc);
  assert.equal(box.textContent, "Loading the VGA library...");
  L.renderPage(box, { kind: "not-published" }, doc);
  assert.equal(box.textContent, "The VGA library is not published yet.");
  let retried = 0;
  L.renderPage(box, { kind: "error", message: "VGA library unavailable — archive unreachable", onRetry: () => retried++ }, doc);
  assert.match(box.textContent, /^VGA library unavailable — archive unreachable/);
  buttons(box, "Retry")[0].onclick();
  assert.equal(retried, 1);
  L.renderPage(box, { kind: "empty" }, doc);
  assert.equal(box.textContent, "The VGA library is empty.");
  L.renderPage(box, ready(), doc);
  assert.match(box.textContent, /5 of 5 designs/);
  assert.match(box.textContent, /page 1 of 1/);
  assert.equal(byClass(box, "tt-library-card").length, 5);
});

test("renderPage ready: one page of cards, never the whole library", () => {
  const doc = fakeDoc();
  const box = doc.createElement("div");
  // 450 entries, as many as the real library holds
  const many = Array.from({ length: 450 }, (_, i) => ({ ...ROWS[i % 5], id: "d" + i, title: "Design " + i }));
  let asked = null;
  L.renderPage(box, ready({ designs: many, pageNo: 3, onPage: (n) => { asked = n; } }), doc);
  const cards = byClass(box, "tt-library-card");
  assert.equal(cards.length, 24);
  assert.match(cards[0].textContent, /Design 48/);
  assert.match(box.textContent, /450 of 450 designs/);
  assert.match(box.textContent, /page 3 of 19/);
  buttons(box, "Next")[0].onclick();
  assert.equal(asked, 4);
  buttons(box, "Previous")[0].onclick();
  assert.equal(asked, 2);
  // the last page is short and Next is off; the first page's Previous is off
  L.renderPage(box, ready({ designs: many, pageNo: 19 }), doc);
  assert.equal(byClass(box, "tt-library-card").length, 450 - 18 * 24);
  assert.equal(buttons(box, "Next")[0].disabled, true);
  L.renderPage(box, ready({ designs: many, pageNo: 1 }), doc);
  assert.equal(buttons(box, "Previous")[0].disabled, true);
  // the posters are lazy and sized; an entry without one gets a blank box
  const imgs = box.find((n) => n.tagName === "IMG");
  assert.ok(imgs.length > 0);
  for (const img of imgs) {
    assert.equal(img.loading, "lazy"); assert.equal(img.decoding, "async");
    assert.equal(String(img.width), "160"); assert.equal(String(img.height), "120"); assert.equal(img.alt, "");
    assert.ok(img.src.startsWith(BASE));
  }
  assert.ok(byClass(box, "tt-library-noposter").length > 0);
});

test("renderPage ready: a card's text, badges, state and links", () => {
  const doc = fakeDoc();
  const box = doc.createElement("div");
  let loaded = null;
  L.renderPage(box, ready({ onBoard: ["tt05_flappy_vga_cutout1"], enabled: "tt05_flappy_vga_cutout1",
                            onLoad: (e) => { loaded = e.id; } }), doc);
  const flappy = byClass(box, "tt-library-card").find((c) => c.textContent.includes("Flappy VGA"));
  const t = flappy.textContent;
  assert.match(t, /by Tiny Tapeout Contributor/);
  assert.match(t, /tt05/); assert.match(t, /tt_um_flappy_vga_cutout1/);
  assert.match(t, /on this board/); assert.match(t, /Running/);
  const warn = byClass(flappy, "tt-badge--warn");
  assert.deepEqual(warn.map((b) => b.textContent), ["timing: 17.08 MHz < 25.175 MHz"]);
  assert.deepEqual(byClass(flappy, "tt-badge--info").map((b) => b.textContent), ["not hardware-validated"]);
  buttons(flappy, "Load")[0].onclick();
  assert.equal(loaded, "tt05_flappy_vga_cutout1");
  const links = flappy.find((n) => n.tagName === "A");
  assert.deepEqual(links.map((a) => a.textContent), ["Docs ↗", "Source ↗", "Video ↗"]);
  assert.equal(links[2].href, BASE + ROWS[4].video_10s_webm);
  for (const a of links) { assert.equal(a.target, "_blank"); assert.equal(a.rel, "noopener"); }
  // another card is neither on the board nor running
  const ex = byClass(box, "tt-library-card").find((c) => c.textContent.includes("Sea Battle"));
  assert.doesNotMatch(ex.textContent, /on this board|Running/);
  // a busy page disables every Load
  L.renderPage(box, ready({ busy: true }), doc);
  assert.ok(buttons(box, "Load").every((b) => b.disabled));
});

test("renderPage ready: the controls, and the search box survives a redraw", () => {
  const doc = fakeDoc();
  const box = doc.createElement("div");
  const seen = [];
  const st = ready({ onQuery: (q) => seen.push(["q", q]), onShuttle: (s) => seen.push(["s", s]) });
  L.renderPage(box, st, doc);
  const search = box.find((n) => n.tagName === "INPUT")[0];
  const select = box.find((n) => n.tagName === "SELECT")[0];
  assert.deepEqual(select.children.map((o) => o.textContent),
    ["All shuttles", "ttsky25b", "ttsky25a", "ttihp26a", "ttihp25a", "tt05"]);
  assert.deepEqual(select.children.map((o) => o.value), ["", "ttsky25b", "ttsky25a", "ttihp26a", "ttihp25a", "tt05"]);
  search.value = "vga example"; search.oninput();
  select.value = "tt05"; select.onchange();
  assert.deepEqual(seen, [["q", "vga example"], ["s", "tt05"]]);
  L.renderPage(box, { ...st, query: "vga example" }, doc);
  // the same input element, so the visitor's focus and caret stay put
  assert.equal(box.find((n) => n.tagName === "INPUT")[0], search);
  assert.match(box.textContent, /2 of 5 designs/);
  assert.equal(byClass(box, "tt-library-card").length, 2);
  L.renderPage(box, { ...st, query: "", shuttle: "tt05" }, doc);
  assert.equal(select.value, "tt05");
  assert.match(box.textContent, /1 of 5 designs/);
  // a state change away from ready and back draws fresh controls
  L.renderPage(box, { kind: "loading" }, doc);
  L.renderPage(box, st, doc);
  assert.notEqual(box.find((n) => n.tagName === "INPUT")[0], search);
});

test("renderPage ready: no matches says so", () => {
  const doc = fakeDoc();
  const box = doc.createElement("div");
  L.renderPage(box, ready({ query: "zzzz" }), doc);
  assert.match(box.textContent, /0 of 5 designs/);
  assert.match(box.textContent, /No design matches/);
  assert.equal(byClass(box, "tt-library-card").length, 0);
});

// --- the panel controller, with a fake fetch

const resp = (status, body, headers = {}) => ({
  ok: status >= 200 && status < 300, status, statusText: "X",
  headers: { get: (k) => headers[k] ?? headers[k.toLowerCase()] ?? null },
  json: async () => { if (body === undefined) throw new SyntaxError("not JSON"); return body; },
});

function harness(answers) {
  const doc = fakeDoc();
  const box = doc.createElement("div");
  const calls = [];
  const msgs = [];
  const log = { hidden: [], loaded: 0, busy: [], indexes: 0 };
  const fetch = async (url, init = {}) => {
    calls.push({ url, method: init.method || "GET", headers: init.headers || {}, body: init.body });
    const a = answers.shift();
    if (!a) throw new Error("unexpected fetch " + url);
    if (a instanceof Error) throw a;
    return a;
  };
  const panel = L.createPanel({
    container: box, doc, fetch, apiBase: "/api/board/fpga-1", csrf: () => "tok",
    showMsg: (text, isError) => msgs.push([text, !!isError]),
    setHidden: (h) => log.hidden.push(h),
    setBusy: (b) => log.busy.push(b),
    onIndex: () => { log.indexes++; },
    onLoaded: async () => { log.loaded++; },
  });
  return { panel, box, calls, msgs, log };
}

test("the index states: ready, empty, not published, not enabled, board not ready, error and retry", async () => {
  let h = harness([resp(200, WIRE)]);
  await h.panel.loadIndex();
  assert.equal(h.calls[0].url, "/api/board/fpga-1/library");
  assert.equal(byClass(h.box, "tt-library-card").length, 5);
  assert.equal(h.panel.title("tt05_flappy_vga_cutout1"), "Flappy VGA");
  assert.equal(h.panel.title("nope"), null);
  assert.equal(h.log.indexes, 1);

  h = harness([resp(200, { ...WIRE, designs: [] })]);
  await h.panel.loadIndex();
  assert.equal(h.box.textContent, "The VGA library is empty.");

  h = harness([resp(404, { error: "VGA library not published", detail: "" })]);
  await h.panel.loadIndex();
  assert.equal(h.box.textContent, "The VGA library is not published yet.");

  h = harness([resp(404, { error: "VGA library not enabled", detail: "" })]);
  await h.panel.loadIndex();
  assert.deepEqual(h.log.hidden, [true]);

  h = harness([resp(503, { error: "board not ready", detail: "its check has not passed" }), resp(200, WIRE)]);
  await h.panel.loadIndex();
  assert.match(h.box.textContent, /^board not ready — its check has not passed/);
  buttons(h.box, "Retry")[0].onclick();
  await h.panel.idle();
  assert.equal(h.calls.length, 2);
  assert.equal(byClass(h.box, "tt-library-card").length, 5);

  h = harness([resp(502, undefined)]);
  await h.panel.loadIndex();
  assert.match(h.box.textContent, /^HTTP 502 X/);

  h = harness([new TypeError("network down")]);
  await h.panel.loadIndex();
  assert.match(h.box.textContent, /network down/);
});

test("the daemon's /designs decides too-old, on this board and running", async () => {
  const h = harness([resp(200, WIRE)]);
  h.panel.setDesigns({ enabled: "tt_um_vga_pattern", designs: [] }); // an old daemon: no top-level library
  await h.panel.loadIndex();
  assert.equal(h.box.textContent, "This board's software is too old for the VGA library.");
  h.panel.setDesigns({
    enabled: "ttsky25a_vga_example", library: { max_bytes: 1, used_bytes: 0 },
    designs: [{ name: "ttsky25a_vga_example", source: "library" }, { name: "tt05_flappy_vga_cutout1", source: "upload" }],
  });
  const cards = byClass(h.box, "tt-library-card");
  const ex = cards.find((c) => c.textContent.includes("VGA Example") && c.textContent.includes("ttsky25a"));
  assert.match(ex.textContent, /on this board/);
  assert.match(ex.textContent, /Running/);
  const flappy = cards.find((c) => c.textContent.includes("Flappy"));
  assert.doesNotMatch(flappy.textContent, /on this board/); // an upload of the same name is not the library entry
});

test("search and the shuttle filter go back to page 1", async () => {
  const many = Array.from({ length: 60 }, (_, i) => ({ ...ROWS[i % 5], id: "d" + i, title: "Design " + i }));
  const h = harness([resp(200, { ...WIRE, designs: many })]);
  await h.panel.loadIndex();
  buttons(h.box, "Next")[0].onclick();
  assert.match(h.box.textContent, /page 2 of 3/);
  const search = h.box.find((n) => n.tagName === "INPUT")[0];
  search.value = "design"; search.oninput();
  assert.match(h.box.textContent, /page 1 of 3/);
  buttons(h.box, "Next")[0].onclick();
  const select = h.box.find((n) => n.tagName === "SELECT")[0];
  select.value = "tt05"; select.onchange();
  assert.match(h.box.textContent, /12 of 60 designs/);
  assert.match(h.box.textContent, /page 1 of 1/);
});

test("load posts with the CSRF token, says what happened, then refreshes the gallery", async () => {
  const ok = { enabled: "ttsky25b_lukasdragoste_vga_patterns", ui_in: 1, ui_in_mask: 1, ui_in_driven: [0],
               ui_in_held: [], released: [], pushed: true, hides_upload: true };
  const h = harness([resp(200, WIRE), resp(200, ok)]);
  await h.panel.loadIndex();
  const card = byClass(h.box, "tt-library-card").find((c) => c.textContent.includes("VGA Patterns"));
  buttons(card, "Load")[0].onclick();
  // while it runs: every Load is off, and the visitor is told it can take a minute
  assert.ok(buttons(h.box, "Load").every((b) => b.disabled));
  assert.deepEqual(h.msgs[0], ["Loading VGA Patterns: fetching from the archive, then programming the FPGA (up to a minute)...", false]);
  await h.panel.idle();
  const post = h.calls[1];
  assert.equal(post.url, "/api/board/fpga-1/library/ttsky25b_lukasdragoste_vga_patterns/load");
  assert.equal(post.method, "POST");
  assert.equal(post.headers["X-CSRFToken"], "tok");
  assert.equal(post.body, "{}");
  const [text, isError] = h.msgs.at(-1);
  assert.equal(isError, false);
  assert.match(text, /^VGA Patterns is running/);
  assert.match(text, /inputs set: 0x01/);
  assert.match(text, /an uploaded design with the same name is hidden while this one is on the board/);
  assert.equal(h.log.loaded, 1);
  assert.deepEqual(h.log.busy, [true, false]);
  assert.ok(buttons(h.box, "Load").every((b) => !b.disabled));
});

test("load reports inputs the board held high", async () => {
  const ok = { enabled: "x", ui_in: 6, ui_in_mask: 6, ui_in_driven: [1], ui_in_held: [2], released: [],
               pushed: false, hides_upload: false };
  const h = harness([resp(200, WIRE), resp(200, ok)]);
  await h.panel.loadIndex();
  await h.panel.load("ttsky25a_vga_example");
  const [text] = h.msgs.at(-1);
  assert.equal(text, "VGA Example is running; inputs set: 0x06; ui_in 2 held high on the board, left as inputs");
});

test("load of a vanished id reloads the index", async () => {
  const shrunk = { ...WIRE, designs: ROWS.filter((e) => e.id !== "tt05_flappy_vga_cutout1") };
  const h = harness([resp(200, WIRE), resp(404, { error: "no such library design", detail: "" }), resp(200, shrunk)]);
  await h.panel.loadIndex();
  await h.panel.load("tt05_flappy_vga_cutout1");
  assert.equal(h.calls.length, 3);
  assert.equal(h.calls[2].url, "/api/board/fpga-1/library");
  assert.equal(h.calls[2].method, "GET");
  assert.ok(h.msgs.some(([t, e]) => e && t === "Flappy VGA is no longer in the library"));
  assert.equal(byClass(h.box, "tt-library-card").length, 4);
  assert.equal(h.log.loaded, 0);
});

test("load failures: busy, too old, still syncing, out of time, not enabled, unreachable", async () => {
  const cases = [
    [resp(409, { error: "a library load is already running on this board", detail: "" }),
      "a library load is already running on this board"],
    [resp(501, { error: "board software too old", detail: "this board's fpgas-tt daemon has no VGA library support yet" }),
      "board software too old — this board's fpgas-tt daemon has no VGA library support yet"],
    [resp(503, { error: "archive file missing: the archive is still syncing; try again in a few minutes", detail: "" },
          { "Retry-After": "30" }),
      "archive file missing: the archive is still syncing; try again in a few minutes (try again in 30 s)"],
    [resp(503, { error: "the site cannot take the board lock; try again", detail: "" }),
      "the site cannot take the board lock; try again"],
    [resp(504, { error: "the load ran out of time; try again", detail: "" }), "the load ran out of time; try again"],
    [resp(504, undefined), "HTTP 504 X"],
    [new TypeError("Failed to fetch"), "Could not reach the site: TypeError: Failed to fetch"],
  ];
  for (const [answer, want] of cases) {
    const h = harness([resp(200, WIRE), answer]);
    await h.panel.loadIndex();
    await h.panel.load("ttsky25a_vga_example");
    assert.deepEqual(h.msgs.at(-1), [want, true]);
    assert.equal(h.log.loaded, 0);
    assert.deepEqual(h.log.busy, [true, false]);
    assert.ok(buttons(h.box, "Load").every((b) => !b.disabled));
  }
  // a 501 also tells the panel the board cannot take library designs
  let h = harness([resp(200, WIRE), resp(501, { error: "board software too old", detail: "" })]);
  await h.panel.loadIndex();
  await h.panel.load("ttsky25a_vga_example");
  assert.equal(h.box.textContent, "This board's software is too old for the VGA library.");
  // the library switched off meanwhile: the panel goes away
  h = harness([resp(200, WIRE), resp(404, { error: "VGA library not enabled", detail: "" })]);
  await h.panel.loadIndex();
  await h.panel.load("ttsky25a_vga_example");
  assert.deepEqual(h.log.hidden, [true]);
});

test("setBusy from the gallery disables the panel's Load buttons", async () => {
  const h = harness([resp(200, WIRE)]);
  await h.panel.loadIndex();
  h.panel.busy(true);
  assert.ok(buttons(h.box, "Load").every((b) => b.disabled));
  h.panel.busy(false);
  assert.ok(buttons(h.box, "Load").every((b) => !b.disabled));
});

test("holdButtons re-enables only the buttons it disabled, so a Running button stays disabled", () => {
  const run = { disabled: false }, running = { disabled: true }, upload = { disabled: false };
  const release = L.holdButtons([run, running, upload]);
  assert.deepEqual([run.disabled, running.disabled, upload.disabled], [true, true, true]);
  release(); // a failed load or enable: nothing re-rendered the gallery
  assert.deepEqual([run.disabled, running.disabled, upload.disabled], [false, true, false]);
});

test("a failed library load leaves the gallery's Running button disabled", async () => {
  const doc = fakeDoc();
  const box = doc.createElement("div");
  const gallery = [{ disabled: false }, { disabled: true }]; // Run, Running
  let release = null;
  const answers = [resp(200, WIRE), resp(504, { error: "the load ran out of time; try again", detail: "" })];
  const panel = L.createPanel({
    container: box, doc, fetch: async () => answers.shift(), apiBase: "/api/board/fpga-1", csrf: () => "tok",
    showMsg() {}, setHidden() {},
    setBusy: (on) => { if (on) release = L.holdButtons(gallery); else { release(); release = null; } },
  });
  await panel.loadIndex();
  await panel.load("ttsky25a_vga_example");
  assert.deepEqual(gallery.map((b) => b.disabled), [false, true]);
});

test("a reloaded index without the selected shuttle resets the filter and the page", async () => {
  const many = Array.from({ length: 60 }, (_, i) => ({ ...ROWS[i % 5], id: "d" + i, title: "Design " + i }));
  const noTt05 = { ...WIRE, designs: many.filter((e) => e.shuttle !== "tt05") };
  const h = harness([resp(200, { ...WIRE, designs: many }), resp(200, noTt05)]);
  await h.panel.loadIndex();
  const select = h.box.find((n) => n.tagName === "SELECT")[0];
  select.value = "tt05"; select.onchange();
  assert.match(h.box.textContent, /12 of 60 designs/);
  await h.panel.loadIndex(true);
  const sel = h.box.find((n) => n.tagName === "SELECT")[0];
  assert.equal(sel.value, "");
  assert.match(h.box.textContent, /48 of 48 designs/);
  assert.match(h.box.textContent, /page 1 of 2/);
  // a shuttle still in the index is kept
  const h2 = harness([resp(200, WIRE), resp(200, WIRE)]);
  await h2.panel.loadIndex();
  const s2 = h2.box.find((n) => n.tagName === "SELECT")[0];
  s2.value = "tt05"; s2.onchange();
  await h2.panel.loadIndex(true);
  assert.equal(h2.box.find((n) => n.tagName === "SELECT")[0].value, "tt05");
  assert.match(h2.box.textContent, /1 of 5 designs/);
});

test("safeUrl takes https:// links only", () => {
  assert.equal(L.safeUrl(" https://github.com/x "), "https://github.com/x");
  assert.equal(L.safeUrl("HTTPS://example.invalid/"), "HTTPS://example.invalid/");
  assert.equal(L.safeUrl("http://example.invalid/"), null);
  assert.equal(L.safeUrl("javascript:alert(1)"), null);
  assert.equal(L.safeUrl(""), null);
  assert.equal(L.safeUrl(null), null);
});

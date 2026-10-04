// Behaviour tests for pibfpgas/static/js/no-picture.js, run with `node --test`.
//
// no-picture.js is a browser script, so it is loaded into a vm context with
// just enough of a browser around it: a document holding one board's video.js
// player (and on the board page its WHEP view and "reset video player"
// button), a fetch() whose answers each test chooses, a videojs that has or
// has not set the player up yet, a MutationObserver for class changes, and
// timers that only fire when a test advances the clock.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";

const here = dirname(fileURLToPath(import.meta.url));
const SCRIPT = join(here, "..", "..", "pibfpgas", "src", "pibfpgas", "static", "js", "no-picture.js");
const STREAM = "https://site.example/live/pi-sw9-p1.m3u8";
const SOURCES = [{ src: STREAM, type: "application/x-mpegURL" }];
const MESSAGE = "No camera picture from this board right now";
const POLL_MS = 15000;

class Element {
    constructor(page, tag, attrs = {}) {
        this.page = page;
        this.tagName = tag.toUpperCase();
        this.attrs = { ...attrs };
        this.children = [];
        this.parentNode = null;
        this.style = { cssText: "" };
        this.textContent = "";
        this.listeners = {};
        const el = this;
        this.classList = {
            contains: (name) => el.className.split(" ").includes(name),
            add: (name) => {
                if (!el.classList.contains(name)) el.className = `${el.className} ${name}`.trim();
            },
            remove: (name) => {
                el.className = el.className.split(" ").filter((c) => c && c !== name).join(" ");
            },
        };
    }

    get id() {
        return this.attrs.id || "";
    }

    set id(value) {
        this.attrs.id = value;
    }

    get className() {
        return this.attrs.class || "";
    }

    set className(value) {
        this.attrs.class = value;
        for (const observer of this.page.observers) {
            if (observer.target === this) observer.callback([]);
        }
    }

    get dataset() {
        const data = {};
        for (const [key, value] of Object.entries(this.attrs)) {
            if (key.startsWith("data-")) {
                data[key.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = value;
            }
        }
        return data;
    }

    getAttribute(name) {
        return name in this.attrs ? this.attrs[name] : null;
    }

    addEventListener(name, fn) {
        (this.listeners[name] ||= []).push(fn);
    }

    click() {
        for (const fn of this.listeners.click || []) fn({});
    }

    appendChild(child) {
        child.parentNode = this;
        this.children.push(child);
        return child;
    }

    insertBefore(child, ref) {
        child.parentNode = this;
        this.children.splice(this.children.indexOf(ref), 0, child);
        return child;
    }

    remove() {
        if (!this.parentNode) return;
        const siblings = this.parentNode.children;
        siblings.splice(siblings.indexOf(this), 1);
        this.parentNode = null;
    }

    *walk() {
        for (const child of this.children) {
            yield child;
            yield* child.walk();
        }
    }
}

// A page with one board player in a table cell, as the templates write it.
// `answers` are the playlist's answers in turn (a status, or "network-error");
// the last one repeats.
function loadPage({ answers, videojsReady = false, style = null, boardPage = false, whepLive = false,
                    readyState = "interactive" }) {
    const page = { observers: [], timers: [], now: 0, nextTimer: 1, fetches: [], visibility: "visible" };
    const doc = { readyState, listeners: {} };
    const head = new Element(page, "head");
    const body = new Element(page, "body");
    const cell = body.appendChild(new Element(page, "td"));
    let whep = null;
    if (boardPage) {
        whep = cell.appendChild(new Element(page, "div", {
            class: whepLive ? "whep-live whep-active" : "whep-live", "data-hls-id": "video-player1",
        }));
    }
    const attrs = {
        id: "video-player1", class: "video-js", width: "320", height: "200",
        "data-setup": JSON.stringify({ liveui: true, sources: SOURCES }),
    };
    if (style) attrs.style = style;
    const video = cell.appendChild(new Element(page, "video", attrs));
    const reset = boardPage ? body.appendChild(new Element(page, "input", { id: "refresh-video-player1" })) : null;

    const all = () => [...head.walk(), ...body.walk()];
    doc.head = head;
    doc.getElementById = (id) => all().find((el) => el.id === id) || null;
    doc.querySelectorAll = (selector) => {
        if (selector === ".video-js[data-setup]") {
            return all().filter((el) => el.classList.contains("video-js") && "data-setup" in el.attrs);
        }
        assert.equal(selector, ".whep-live");
        return all().filter((el) => el.classList.contains("whep-live"));
    };
    doc.createElement = (tag) => new Element(page, tag);
    doc.addEventListener = (name, fn) => {
        doc.listeners[name] = fn;
    };
    Object.defineProperty(doc, "visibilityState", { get: () => page.visibility });

    // video.js's setup, as Player.createEl in 8.4.0 does it: a wrapper <div>
    // takes every attribute of the <video> but width and height (so its id,
    // class, data-setup and style), and the <video> inside becomes
    // "<id>_html5_api" with class "vjs-tech" and loses width and height.
    const players = new Map();
    const videojs = { getPlayer: (id) => players.get(id) };
    let wrapper = null;
    const player = { srcCalls: [], plays: 0, disposed: 0 };
    const setUp = () => {
        const { width, height, ...kept } = video.attrs;
        wrapper = new Element(page, "div", kept);
        cell.insertBefore(wrapper, video);
        video.remove();
        video.attrs = { ...kept, id: `${kept.id}_html5_api`, class: "vjs-tech" };
        wrapper.appendChild(video);
        players.set(wrapper.id, {
            width: () => Number(width),
            height: () => Number(height),
            src: (sources) => player.srcCalls.push(sources),
            play: () => {
                player.plays += 1;
                return Promise.resolve();
            },
            dispose: () => {
                player.disposed += 1;
            },
        });
    };
    if (videojsReady) setUp();

    const fetch = (url, options) => {
        page.fetches.push({ url, options });
        const answer = answers.length > 1 ? answers.shift() : answers[0];
        if (answer === "network-error") return Promise.reject(new TypeError("Failed to fetch"));
        return Promise.resolve({ status: answer, ok: answer >= 200 && answer < 300 });
    };
    const setTimeout = (fn, ms = 0) => {
        const id = page.nextTimer++;
        page.timers.push({ id, fn, at: page.now + ms });
        return id;
    };
    const clearTimeout = (id) => {
        page.timers = page.timers.filter((t) => t.id !== id);
    };
    class MutationObserver {
        constructor(callback) {
            this.callback = callback;
        }

        observe(target, options) {
            // across the vm boundary: compared as JSON, objects from the script have the context's prototypes
            assert.equal(JSON.stringify(options), JSON.stringify({ attributes: true, attributeFilter: ["class"] }));
            this.target = target;
            page.observers.push(this);
        }
    }

    const context = vm.createContext({
        document: doc, fetch, setTimeout, clearTimeout, MutationObserver, JSON, Promise, Array, console,
    });
    context.window = context;
    context.videojs = videojs;
    vm.runInContext(readFileSync(SCRIPT, "utf8"), context, { filename: SCRIPT });

    // run the timers due within `ms`, letting each fetch they start answer
    const advance = async (ms) => {
        const until = page.now + ms;
        await settle();
        for (;;) {
            const due = page.timers.filter((t) => t.at <= until).sort((a, b) => a.at - b.at || a.id - b.id)[0];
            if (!due) break;
            page.timers = page.timers.filter((t) => t !== due);
            page.now = due.at;
            due.fn();
            await settle();
        }
        page.now = until;
    };
    const setVisibility = async (state) => {
        page.visibility = state;
        doc.listeners.visibilitychange();
        await settle();
    };
    const setWhepLive = async (live) => {
        if (live) whep.classList.add("whep-active");
        else whep.classList.remove("whep-active");
        await settle();
    };
    return {
        page, doc, cell, video, reset, player, setUp, advance, setVisibility, setWhepLive,
        wrapper: () => wrapper,
        // what video.js's id now names: the wrapper once set up, else the <video>
        playerBox: () => doc.getElementById("video-player1"),
    };
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

function boxes(t) {
    return t.cell.children.filter((el) => el.className === "no-picture");
}

function hidden(el) {
    return el.classList.contains("no-picture-hidden");
}

test("a board whose playlist is 404 shows the static box and hides the player", async () => {
    const t = loadPage({ answers: [404] });
    await settle();
    assert.deepEqual(t.page.fetches.map((f) => f.url), [STREAM]);
    assert.equal(t.page.fetches[0].options.cache, "no-store");
    const [box] = boxes(t);
    assert.equal(box.textContent, MESSAGE);
    assert.equal(t.cell.children.indexOf(box), t.cell.children.indexOf(t.video) - 1, "in the player's place");
    assert.ok(hidden(t.video));
    assert.equal(t.video.id, "video-player1", "hidden, not removed: the id stays");
    // the player's size, so the grid stays aligned
    assert.match(box.style.cssText, /width: 320px; height: 200px;$/);
    // display: none !important, because whep-live.js writes style.display on the same element
    assert.ok(t.doc.head.children.some((el) => el.textContent === ".no-picture-hidden { display: none !important; }"));
});

test("a player video.js has already set up is hidden, not disposed of, and keeps its structure", async () => {
    const t = loadPage({ answers: [404], videojsReady: true });
    await settle();
    assert.equal(t.player.disposed, 0);
    assert.ok(hidden(t.wrapper()));
    assert.equal(t.wrapper().id, "video-player1");
    assert.equal(t.video.id, "video-player1_html5_api");
    assert.equal(t.video.parentNode, t.wrapper());
    // the size from the player: video.js dropped the wrapper's width and height
    assert.match(boxes(t)[0].style.cssText, /width: 320px; height: 200px;$/);
});

test("a <video> hidden before video.js sets it up stays hidden: video.js copies its class to the wrapper", async () => {
    const t = loadPage({ answers: [404] });
    await settle();
    t.setUp();
    assert.ok(hidden(t.wrapper()));
    assert.ok(!hidden(t.video));
});

test("the board page's own size for the player is kept", async () => {
    for (const videojsReady of [false, true]) {
        const t = loadPage({ answers: [404], style: "width: 100%; height: 100%;", videojsReady });
        await settle();
        // the inline style comes last, so it wins over the player's width and height
        assert.match(boxes(t)[0].style.cssText, /width: 320px; height: 200px;.*width: 100%; height: 100%;$/);
    }
});

test("404 then 200: the player comes back working, with its source given again, and the button works", async () => {
    const t = loadPage({ answers: [404, 404, 200], videojsReady: true, boardPage: true });
    await settle();
    assert.equal(boxes(t).length, 1);
    await t.advance(POLL_MS);
    assert.equal(t.page.fetches.length, 2);
    assert.equal(boxes(t).length, 1);
    await t.advance(POLL_MS);
    assert.equal(t.page.fetches.length, 3);
    assert.equal(boxes(t).length, 0);
    assert.ok(!hidden(t.wrapper()));
    assert.equal(t.playerBox(), t.wrapper(), "the same player, ids and structure intact");
    assert.equal(t.video.id, "video-player1_html5_api");
    assert.deepEqual(JSON.parse(JSON.stringify(t.player.srcCalls)), [SOURCES], "video.js's own src(), no <source>");
    assert.equal(t.player.plays, 1);
    assert.equal(t.player.disposed, 0);
    assert.equal(t.reset.disabled, undefined, "the button was never disabled");
    // and with the player back, polling stops
    assert.equal(t.page.timers.length, 0);
    await t.advance(10 * POLL_MS);
    assert.equal(t.page.fetches.length, 3);
});

test("a <video> that video.js set up while hidden comes back with its source given again", async () => {
    const t = loadPage({ answers: [404, 200] });
    await settle();
    t.setUp(); // video.js loads the 404 playlist and gives up
    await t.advance(POLL_MS);
    assert.equal(boxes(t).length, 0);
    assert.ok(!hidden(t.wrapper()));
    assert.equal(t.player.srcCalls.length, 1);
});

test("repeated 404s keep one box and one timer", async () => {
    const t = loadPage({ answers: [404], videojsReady: true });
    await settle();
    for (let i = 1; i <= 8; i++) {
        await t.advance(POLL_MS);
        assert.equal(t.page.fetches.length, 1 + i);
        assert.equal(t.page.timers.length, 1);
        assert.equal(boxes(t).length, 1);
    }
    assert.equal(t.player.srcCalls.length, 0);
});

test("while the box is shown, errors and no answer keep the box and the polling", async () => {
    const t = loadPage({ answers: [404, 500, "network-error", 502, 200], videojsReady: true });
    await settle();
    for (let i = 0; i < 3; i++) {
        await t.advance(POLL_MS);
        assert.equal(boxes(t).length, 1);
        assert.equal(t.page.timers.length, 1);
    }
    await t.advance(POLL_MS);
    assert.equal(boxes(t).length, 0);
});

test("a hidden page does not poll, and asks at once when it is visible again", async () => {
    const t = loadPage({ answers: [404, 404, 200], videojsReady: true });
    await settle();
    await t.setVisibility("hidden");
    assert.equal(t.page.timers.length, 0);
    await t.advance(10 * POLL_MS);
    assert.equal(t.page.fetches.length, 1);
    await t.setVisibility("visible");
    assert.equal(t.page.fetches.length, 2);
    assert.equal(t.page.timers.length, 1, "polling again, one timer");
    await t.advance(POLL_MS);
    assert.equal(boxes(t).length, 0);
});

test("a page hidden while a question is out does not start polling", async () => {
    const t = loadPage({ answers: [404], videojsReady: true });
    t.page.visibility = "hidden";
    await settle();
    assert.equal(boxes(t).length, 1);
    assert.equal(t.page.timers.length, 0);
});

test("pressing 'reset video player' while the box is shown asks at once", async () => {
    const t = loadPage({ answers: [404, 200], videojsReady: true, boardPage: true });
    await settle();
    t.reset.click();
    await settle();
    assert.equal(t.page.fetches.length, 2);
    assert.equal(boxes(t).length, 0);
    assert.equal(t.page.timers.length, 0, "the poll timer went with the box");
});

test("pressing 'reset video player' with the player shown asks nothing", async () => {
    const t = loadPage({ answers: [200], videojsReady: true, boardPage: true });
    await settle();
    t.reset.click();
    await settle();
    assert.equal(t.page.fetches.length, 1);
});

for (const answer of [200, 500, 502, 403, "network-error"]) {
    test(`a playlist answering ${answer} leaves the player exactly as it was`, async () => {
        const t = loadPage({ answers: [answer], videojsReady: true, boardPage: true });
        await settle();
        assert.equal(boxes(t).length, 0);
        assert.ok(!hidden(t.wrapper()));
        assert.equal(t.player.srcCalls.length, 0);
        assert.equal(t.page.timers.length, 0);
    });
}

test("playlist 404 while the low-latency view is live: nothing is hidden", async () => {
    const t = loadPage({ answers: [404], videojsReady: true, boardPage: true, whepLive: true });
    await settle();
    assert.equal(boxes(t).length, 0);
    assert.ok(!hidden(t.wrapper()));
    assert.equal(t.page.timers.length, 0);
});

test("the low-latency view going live takes the box away; losing it asks again", async () => {
    const t = loadPage({ answers: [404, 200], videojsReady: true, boardPage: true });
    await settle();
    assert.equal(boxes(t).length, 1);
    await t.setWhepLive(true);
    assert.equal(boxes(t).length, 0);
    assert.ok(!hidden(t.wrapper()));
    assert.equal(t.page.timers.length, 0);
    // behind the WHEP view the player is left paused, as whep-live.js wants it
    assert.equal(t.player.srcCalls.length, 0);
    await t.setWhepLive(false);
    assert.equal(t.page.fetches.length, 2);
    // the playlist is back: the player that gave up on a 404 is given its source again
    assert.equal(boxes(t).length, 0);
    assert.deepEqual(JSON.parse(JSON.stringify(t.player.srcCalls)), [SOURCES]);
});

test("losing the low-latency view with no HLS stream shows the box", async () => {
    const t = loadPage({ answers: [404], videojsReady: true, boardPage: true, whepLive: true });
    await settle();
    await t.setWhepLive(false);
    assert.equal(boxes(t).length, 1);
    assert.ok(hidden(t.wrapper()));
});

test("while the page is still loading, the check waits for DOMContentLoaded", async () => {
    const t = loadPage({ answers: [404], readyState: "loading" });
    await settle();
    assert.equal(t.page.fetches.length, 0);
    t.doc.listeners.DOMContentLoaded();
    await settle();
    assert.equal(boxes(t).length, 1);
});

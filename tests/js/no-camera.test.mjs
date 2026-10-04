// Behaviour tests for pibfpgas/static/js/no-camera.js, run with `node --test`.
//
// no-camera.js is a browser script, so it is loaded into a vm context with
// just enough of a browser around it: a document holding the page's video.js
// <video> elements, a fetch() whose answer each test chooses, and a videojs
// that has or has not set the players up yet.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";

const here = dirname(fileURLToPath(import.meta.url));
const SCRIPT = join(here, "..", "..", "pibfpgas", "src", "pibfpgas", "static", "js", "no-camera.js");
const STREAM = "https://site.example/live/pi-sw9-p1.m3u8";

class Element {
    constructor(doc, tag, attrs = {}) {
        this.doc = doc;
        this.tagName = tag.toUpperCase();
        this.attrs = { ...attrs };
        this.children = [];
        this.parentNode = null;
        this.style = { cssText: "" };
        this.textContent = "";
        this.className = attrs.class || "";
        this.disabled = false;
    }

    get id() {
        return this.attrs.id || "";
    }

    set id(value) {
        this.attrs.id = value;
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
function loadPage({ status, videojsReady = false, style = null, resetButton = false, readyState = "interactive",
                   held = false }) {
    const doc = { readyState, listeners: {} };
    const body = new Element(doc, "body");
    const cell = body.appendChild(new Element(doc, "td"));
    const attrs = {
        id: "video-player1", class: "video-js", width: "320", height: "200",
        "data-setup": JSON.stringify({ liveui: true, sources: [{ src: STREAM, type: "application/x-mpegURL" }] }),
    };
    if (style) attrs.style = style;
    const video = cell.appendChild(new Element(doc, "video", attrs));
    let reset = null;
    if (resetButton) reset = body.appendChild(new Element(doc, "input", { id: "refresh-video-player1" }));

    const all = () => [...body.walk()];
    doc.getElementById = (id) => all().find((el) => el.id === id) || null;
    doc.querySelectorAll = (selector) => {
        assert.equal(selector, ".video-js[data-setup]");
        return all().filter((el) => el.className.split(" ").includes("video-js") && "data-setup" in el.attrs);
    };
    doc.createElement = (tag) => new Element(doc, tag);
    doc.addEventListener = (name, fn) => {
        doc.listeners[name] = fn;
    };

    // video.js's setup, as Player.createEl in 8.4.0 does it: a wrapper <div>
    // takes every attribute of the <video> but width and height (so its id,
    // class "video-js", data-setup and style), the <video> inside becomes
    // "<id>_html5_api" with class "vjs-tech" and loses width and height, and
    // dispose() removes the wrapper and forgets the player.
    const players = new Map();
    const videojs = { getPlayer: (id) => players.get(id) };
    let wrapper = null;
    let disposed = 0;
    const setUp = () => {
        const { width, height, ...kept } = video.attrs;
        wrapper = new Element(doc, "div", kept);
        cell.insertBefore(wrapper, video);
        video.remove();
        video.attrs = { ...kept, id: `${kept.id}_html5_api`, class: "vjs-tech" };
        video.className = "vjs-tech";
        wrapper.appendChild(video);
        players.set(wrapper.id, {
            width: () => Number(width),
            height: () => Number(height),
            dispose() {
                disposed += 1;
                players.delete(wrapper.id);
                wrapper.remove();
            },
        });
    };
    if (videojsReady) setUp();

    const fetches = [];
    let answer = null;
    const answered = new Promise((resolve) => {
        answer = resolve;
    });
    const fetch = (url, options) => {
        fetches.push({ url, options });
        if (status === "network-error") return Promise.reject(new TypeError("Failed to fetch"));
        const response = { status, ok: status >= 200 && status < 300 };
        return held ? answered.then(() => response) : Promise.resolve(response);
    };

    const context = vm.createContext({ document: doc, window: {}, fetch, JSON, Promise, console });
    context.window = context;
    context.videojs = videojs;
    vm.runInContext(readFileSync(SCRIPT, "utf8"), context, { filename: SCRIPT });
    return { doc, cell, video, reset, fetches, disposed: () => disposed, wrapper: () => wrapper, setUp, answer };
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

function boxes(page) {
    return page.cell.children.filter((el) => el.className === "no-camera");
}

test("a board whose playlist is 404 shows the static box in place of the player", async () => {
    const page = loadPage({ status: 404 });
    await settle();
    assert.deepEqual(page.fetches.map((f) => f.url), [STREAM]);
    assert.equal(page.fetches[0].options.cache, "no-store");
    const [box] = boxes(page);
    assert.equal(box.textContent, "No camera on this board");
    assert.equal(page.cell.children.length, 1, "the <video> is gone, so video.js never sets it up");
    assert.equal(page.video.parentNode, null);
    // the player's size, so the grid stays aligned
    assert.match(box.style.cssText, /width: 320px; height: 200px;/);
});

test("a player video.js has already set up is disposed of, not left playing nothing", async () => {
    const page = loadPage({ status: 404, videojsReady: true });
    await settle();
    assert.equal(page.disposed(), 1);
    assert.equal(page.wrapper().parentNode, null);
    assert.deepEqual(page.cell.children.map((el) => el.className), ["no-camera"]);
});

test("a player video.js set up first still gets a box of its size", async () => {
    // the wrapper has lost the width and height attributes: the player knows them
    const page = loadPage({ status: 404, videojsReady: true });
    await settle();
    const [box] = boxes(page);
    assert.match(box.style.cssText, /width: 320px; height: 200px;$/);
});

test("a player video.js sets up while the playlist is being asked for is disposed of too", async () => {
    const page = loadPage({ status: 404, held: true });
    page.setUp();
    page.answer();
    await settle();
    assert.equal(page.disposed(), 1);
    assert.deepEqual(page.cell.children.map((el) => el.className), ["no-camera"]);
    assert.match(boxes(page)[0].style.cssText, /width: 320px; height: 200px;$/);
});

test("the board page's own size for the player is kept", async () => {
    for (const videojsReady of [false, true]) {
        const page = loadPage({ status: 404, style: "width: 100%; height: 100%;", videojsReady });
        await settle();
        const [box] = boxes(page);
        // the inline style comes last, so it wins over the player's width and height
        assert.match(box.style.cssText, /width: 320px; height: 200px;.*width: 100%; height: 100%;$/);
    }
});

test("the board page's reset video player button is disabled: there is no player to reset", async () => {
    const page = loadPage({ status: 404, resetButton: true });
    await settle();
    assert.equal(page.reset.disabled, true);
});

for (const status of [200, 500, 502, 403, "network-error"]) {
    test(`a playlist answering ${status} leaves the player exactly as it was`, async () => {
        const page = loadPage({ status, videojsReady: true, resetButton: true });
        await settle();
        assert.equal(boxes(page).length, 0);
        assert.equal(page.disposed(), 0);
        assert.equal(page.wrapper().parentNode, page.cell);
        assert.equal(page.reset.disabled, false);
    });
}

test("while the page is still loading, the check waits for DOMContentLoaded", async () => {
    const page = loadPage({ status: 404, readyState: "loading" });
    await settle();
    assert.equal(page.fetches.length, 0);
    page.doc.listeners.DOMContentLoaded();
    await settle();
    assert.equal(boxes(page).length, 1);
});

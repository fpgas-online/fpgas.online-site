// Behaviour tests for pistat/static/dcws.js, run with `node --test`.
//
// dcws.js is a browser script, so it is loaded into a vm context with just
// enough of a browser around it: elements looked up by id, a WebSocket that
// follows the spec where it matters here (send() throws while CONNECTING and
// silently drops data once CLOSING/CLOSED), and timers that only fire when a
// test advances the clock.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";

const here = dirname(fileURLToPath(import.meta.url));
const DCWS = process.env.DCWS_JS || join(here, "..", "..", "pistat", "src", "pistat", "static", "dcws.js");
const PI = 33;

class FakeWebSocket {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSING = 2;
    static CLOSED = 3;

    constructor(page, url) {
        this.page = page;
        this.url = url;
        this.readyState = FakeWebSocket.CONNECTING;
        this.sent = [];
        this.dropped = [];
        page.sockets.push(this);
    }

    send(data) {
        if (this.readyState === FakeWebSocket.CONNECTING) {
            throw new Error("InvalidStateError: send() while CONNECTING");
        }
        (this.readyState === FakeWebSocket.OPEN ? this.sent : this.dropped).push(JSON.parse(data).message);
    }

    close() {
        if (this.readyState >= FakeWebSocket.CLOSING) return;
        this.readyState = FakeWebSocket.CLOSING;
        this.page.setTimeout(() => this.#closed(1000), 0);
    }

    // Server side of the connection.
    open() {
        this.readyState = FakeWebSocket.OPEN;
        this.onopen?.({});
    }

    serverClose(code = 1011) {
        this.#closed(code);
    }

    #closed(code) {
        if (this.readyState === FakeWebSocket.CLOSED) return;
        this.readyState = FakeWebSocket.CLOSED;
        this.onclose?.({ code });
    }
}

function loadPage() {
    const page = { sockets: [], timers: [], now: 0, nextTimer: 1, elements: new Map() };

    page.setTimeout = (fn, ms = 0) => {
        const id = page.nextTimer++;
        page.timers.push({ id, fn, at: page.now + ms });
        return id;
    };
    page.clearTimeout = (id) => {
        page.timers = page.timers.filter((t) => t.id !== id);
    };
    page.advance = (ms) => {
        const until = page.now + ms;
        for (;;) {
            const due = page.timers.filter((t) => t.at <= until).sort((a, b) => a.at - b.at || a.id - b.id)[0];
            if (!due) break;
            page.timers = page.timers.filter((t) => t !== due);
            page.now = due.at;
            due.fn();
        }
        page.now = until;
    };
    page.el = (id) => {
        if (!page.elements.has(id)) {
            page.elements.set(id, {
                id,
                value: "",
                placeholder: "",
                scrollTop: 0,
                scrollHeight: 0,
                player: { load() {} },
                contentWindow: { wssh: { connect() {} } },
            });
        }
        return page.elements.get(id);
    };
    page.click = (id) => page.el(id).onclick({ preventDefault() {} });
    page.log = () => page.el(`log${PI}`).value;
    page.live = () => page.sockets.filter((s) => s.readyState < FakeWebSocket.CLOSING);

    const WebSocket = class extends FakeWebSocket {
        constructor(url) {
            super(page, url);
        }
    };
    const context = vm.createContext({
        document: { getElementById: page.el },
        window: { location: { host: "example.test" } },
        WebSocket,
        setTimeout: page.setTimeout,
        clearTimeout: page.clearTimeout,
        fetch: () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ state: "on" }) }),
        console: { log() {}, error() {} },
        JSON,
        Math,
        Date,
    });
    vm.runInContext(readFileSync(DCWS, "utf8"), context, { filename: DCWS });
    context.PiStatus(PI, 2);
    return page;
}

test("connects on load and asks for the PoE status", () => {
    const page = loadPage();
    assert.equal(page.sockets.length, 1);
    assert.equal(page.sockets[0].url, `WSS://example.test/ws/pistat/pi${PI}/`);
    page.sockets[0].open();
    assert.match(page.log(), /socket connected/);
    assert.deepEqual(page.sockets[0].sent, [`checking status: ${PI}`]);
});

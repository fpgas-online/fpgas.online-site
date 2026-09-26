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

test("reconnects by itself after the server drops the socket", () => {
    const page = loadPage();
    page.sockets[0].open();
    page.sockets[0].serverClose(1011);
    assert.match(page.log(), /socket closed/);
    page.advance(999);
    assert.equal(page.sockets.length, 1, "waits before reconnecting");
    page.advance(1);
    assert.equal(page.sockets.length, 2);
    page.sockets[1].open();
    assert.equal(page.live().length, 1);
    assert.deepEqual(page.sockets[1].sent, [`checking status: ${PI}`], "status refreshed after reconnect");
});

// Waits, in ms, between each close and the reconnect it triggers, for
// `drops` drops in a row. `openFor` is how long each socket stays open before
// the server drops it (null: it never opens at all).
function retryDelays(page, drops, openFor) {
    const delays = [];
    for (let i = 0; i < drops; i++) {
        const socket = page.sockets.at(-1);
        if (openFor !== null) {
            socket.open();
            page.advance(openFor);
        }
        socket.serverClose();
        const before = page.sockets.length;
        let waited = 0;
        while (page.sockets.length === before) {
            page.advance(500);
            waited += 500;
            assert.ok(waited <= 60000, "never reconnected");
        }
        delays.push(waited);
    }
    return delays;
}

test("backs off 1 s doubling to 30 s while the server is unreachable", () => {
    const page = loadPage();
    assert.deepEqual(retryDelays(page, 7, null), [1000, 2000, 4000, 8000, 16000, 30000, 30000]);
});

test("a server that accepts and then drops each connection still gets the backoff, not a reconnect storm", () => {
    // Found in a real browser: resetting the backoff as soon as a socket
    // opened meant one reconnect (and one SNMP status query) per second.
    // redis-py 8 did exactly this, killing every socket 5 s after it opened.
    const page = loadPage();
    assert.deepEqual(retryDelays(page, 6, 5000), [1000, 2000, 4000, 8000, 16000, 30000]);
});

test("after a connection that stayed up, the next drop retries after 1 s again", () => {
    const page = loadPage();
    retryDelays(page, 5, null);
    assert.deepEqual(retryDelays(page, 1, 30000), [1000]);
});

test("the reconnect button leaves exactly one socket, even after the old one's close event", () => {
    const page = loadPage();
    page.sockets[0].open();
    page.click(`reconnect${PI}`);
    page.advance(60000);
    assert.equal(page.sockets.length, 2, "no extra reconnect from the replaced socket");
    assert.equal(page.live().length, 1);
    assert.equal(page.live()[0], page.sockets[1]);
});

test("the reconnect button cancels a pending automatic retry", () => {
    const page = loadPage();
    page.sockets[0].open();
    page.sockets[0].serverClose();
    page.click(`reconnect${PI}`);
    page.advance(60000);
    assert.equal(page.sockets.length, 2);
    assert.equal(page.live().length, 1);
});

test("reset reconnects at once and its log line goes out on the new socket", () => {
    const page = loadPage();
    page.sockets[0].open();
    page.sockets[0].serverClose();
    page.click(`reset${PI}`); // must not wait out the backoff, nor throw
    assert.equal(page.sockets.length, 2);
    page.sockets[1].open();
    assert.ok(page.sockets[1].sent.includes(`reset: ${PI}`), JSON.stringify(page.sockets[1].sent));
    page.advance(60000);
    assert.equal(page.live().length, 1);
});

test("a message typed while disconnected is sent once the socket is back", () => {
    const page = loadPage();
    page.sockets[0].open();
    page.sockets[0].serverClose();
    page.el(`log-text${PI}`).value = "hello";
    page.click(`log-submit${PI}`);
    page.advance(1000);
    page.sockets[1].open();
    assert.ok(page.sockets[1].sent.includes("hello"), JSON.stringify(page.sockets[1].sent));
    assert.deepEqual(page.sockets[0].dropped, []);
});

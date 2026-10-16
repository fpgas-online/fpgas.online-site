"use strict";

/*
 * The PoE power button on the switch port dashboard (Tim, 17 Oct 2026, round 5):
 *
 *   "Text pops up in the power control column next to the button expanding the column. These should be popup type
 *    messages that float at the top of the page."
 *   "Pressing the button should have some type of indication that the change is occuring and it is waiting on the
 *    state to be confirmed. Maybe the circle inside the button twirls."
 *   "The button icon should show a countdown inside the button when waiting. The time delay between power off to
 *    power on should be ~10 seconds. The time delay from power on -> power off should be ~60 seconds."
 *
 * So a press goes: the ⏻ turns while the switch is written and read back (the PoE service answers only with the state
 * it read from the switch afterwards: that is the confirmation); then the button shows the new state (filled while
 * powered, hollow while off) with the seconds the PoE service holds the port for counting down inside it; then the ⏻
 * again. Every message (what the switch confirmed, a refusal, a failure) is a toast at the top of the page, never text
 * in the table. A press inside someone else's hold is answered 429 with the seconds left, and the button counts those
 * down: a press is never silently dropped.
 *
 * The table is rebuilt from the dashboard's reading every refresh, and that reading comes through a 15 s server cache:
 * it can still show the old PoE state after the switch confirmed the new one. The tracker therefore keeps each port's
 * confirmed state and draws the button from it until a reading agrees (or CONFIRMED_KEPT_MS has passed, after which the
 * reading is believed: someone else may have switched the port since).
 *
 * The tracker is plain data and functions (tested with node: tests/js/power-button.test.mjs); attach() is the page's
 * glue.
 */
(function () {
  var ICON = "⏻";
  var CONFIRMED_KEPT_MS = 90 * 1000;  /* longer than the longest hold plus a server cache period */
  var ACTION_OF = {on: "off", off: "on"};  /* a powered port's button turns it off, an unpowered port's turns it on */

  function createTracker() {
    var ports = {};  /* "switch/port" -> {busy, until, confirmed, confirmedAt} */
    function get(k) { return ports[k] || (ports[k] = {busy: false, until: 0, confirmed: null, confirmedAt: 0}); }
    return {
      /* the press was sent */
      start: function (k) { get(k).busy = true; },
      /* the switch confirmed `state` ("on"/"off") and the port is held for `seconds` */
      confirmed: function (k, state, seconds, now) {
        var p = get(k);
        p.busy = false; p.until = now + seconds * 1000;
        if (state === "on" || state === "off") { p.confirmed = state; p.confirmedAt = now; }
      },
      /* the port is held by an earlier switch for `seconds` (a 429); nothing was sent */
      held: function (k, seconds, now) { var p = get(k); p.busy = false; p.until = Math.max(p.until, now + seconds * 1000); },
      /* the press failed: nothing is known to have changed, the button is free again */
      failed: function (k) { get(k).busy = false; },
      /* How the button of port `k` is drawn, given the action the latest reading gives it ("on"/"off").
         {action, text, busy, disabled, seconds} */
      view: function (k, readAction, now) {
        var p = ports[k], action = readAction;
        if (!p) { return {action: action, text: ICON, busy: false, disabled: false, seconds: 0}; }
        if (p.confirmed) {
          if (ACTION_OF[p.confirmed] === readAction || now - p.confirmedAt > CONFIRMED_KEPT_MS) { p.confirmed = null; }
          else { action = ACTION_OF[p.confirmed]; }
        }
        if (p.busy) { return {action: action, text: ICON, busy: true, disabled: true, seconds: 0}; }
        var seconds = Math.max(0, Math.ceil((p.until - now) / 1000));
        if (seconds) { return {action: action, text: String(seconds), busy: false, disabled: true, seconds: seconds}; }
        if (!p.confirmed) { delete ports[k]; }
        return {action: action, text: ICON, busy: false, disabled: false, seconds: 0};
      },
      /* whether any button still needs redrawing as time passes */
      ticking: function (now) {
        return Object.keys(ports).some(function (k) { return ports[k].busy || ports[k].until > now; });
      },
    };
  }

  /* What the PoE service's answer to a press means: {kind: "ok"|"held"|"error", state, seconds, text}. `where` names
     the port ("switch 2 port 9"). */
  function readAnswer(status, body, retryAfter, where) {
    if (status >= 200 && status < 300 && body && (body.state === "on" || body.state === "off")) {
      var seconds = typeof body.next_in === "number" && body.next_in > 0 ? body.next_in : 0;
      return {kind: "ok", state: body.state, seconds: seconds, text: "PoE " + body.state + " on " + where};
    }
    if (status === 429) {
      var wait = parseInt(retryAfter, 10);
      if (!(wait > 0)) { wait = 1; }
      return {kind: "held", seconds: wait,
              text: where + " was switched a moment ago; it can be switched again in " + wait + " s"};
    }
    var why = body && typeof body.error === "string" && body.error ? body.error : "refused";
    return {kind: "error", text: where + ": " + why + " (HTTP " + status + ")"};
  }

  /* Draw `view` on a button element (the served one or one the refresh built). */
  function draw(b, view) {
    var glyph = b.querySelector(".mgmt-poe-glyph");
    if (!glyph) { b.textContent = ""; glyph = b.ownerDocument.createElement("span"); glyph.className = "mgmt-poe-glyph"; glyph.setAttribute("aria-hidden", "true"); b.appendChild(glyph); }
    glyph.textContent = view.text;
    b.dataset.action = view.action;
    b.className = "mgmt-poe-button mgmt-poe-to-" + view.action + (view.busy ? " mgmt-poe-busy" : "") +
      (view.seconds ? " mgmt-poe-wait" : "");
    b.disabled = view.disabled;
    b.setAttribute("aria-busy", view.busy ? "true" : "false");
    var words = view.busy ? "switching, waiting for the switch to confirm" :
      view.seconds ? "can be switched again in " + view.seconds + " s" : "turn PoE " + view.action;
    b.title = words;
    b.setAttribute("aria-label", words + " on port " + b.dataset.port);
  }

  /* The toasts: one stack fixed at the top of the page. A confirmation goes after a few seconds, a refusal or a failure
     stays longer; each has a close button. */
  function toast(doc, text, kind, setTimeout) {
    var stack = doc.getElementById("mgmt-toasts");
    if (!stack) { return; }
    var t = doc.createElement("div");
    t.className = "mgmt-toast mgmt-toast-" + kind;
    t.setAttribute("role", kind === "error" ? "alert" : "status");
    var words = doc.createElement("span"); words.textContent = text; t.appendChild(words);
    var close = doc.createElement("button"); close.type = "button"; close.className = "mgmt-toast-close";
    close.textContent = "×"; close.setAttribute("aria-label", "close this message");
    close.addEventListener("click", function () { t.remove(); });
    t.appendChild(close);
    stack.appendChild(t);
    setTimeout(function () { t.remove(); }, kind === "ok" ? 6000 : 20000);
  }

  function key(sw, port) { return sw + "/" + port; }

  /* The page's glue: presses, the countdown, and redrawing after the table refresh. `refresh` reads the switches
     again. Returns {redraw}: the refresh calls it after rebuilding the table. */
  function attach(win, powerUrl, refresh) {
    var doc = win.document, tracker = createTracker(), timer = null;
    function redraw() {
      var now = Date.now();
      doc.querySelectorAll(".mgmt-poe-button").forEach(function (b) {
        if (!b.dataset.readAction) { b.dataset.readAction = b.dataset.action; }  /* what the reading said */
        draw(b, tracker.view(key(b.dataset.switch, b.dataset.port), b.dataset.readAction, now));
      });
      if (tracker.ticking(now)) { if (!timer) { timer = win.setInterval(redraw, 250); } }
      else if (timer) { win.clearInterval(timer); timer = null; }
    }
    function press(b) {
      var sw = b.dataset.switch, port = b.dataset.port, action = b.dataset.action, k = key(sw, port);
      var where = (sw === "" ? "" : "switch " + sw + " ") + "port " + port;
      var powers = b.dataset.powers || "nothing seen";
      if (!win.confirm("Turn PoE " + action + " on " + where + "? It powers " + powers + ".")) { return; }
      tracker.start(k); redraw();
      var body = {switch: sw === "" ? null : Number(sw), port: String(port), on: action === "on"};
      win.fetch(powerUrl, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)})
        .then(function (r) {
          return r.json().catch(function () { return {}; }).then(function (d) {
            return readAnswer(r.status, d, r.headers.get("Retry-After"), where);
          });
        })
        .catch(function (e) { return {kind: "error", text: where + ": the site did not answer (" + e + ")"}; })
        .then(function (a) {
          var now = Date.now();
          if (a.kind === "ok") { tracker.confirmed(k, a.state, a.seconds, now); }
          else if (a.kind === "held") { tracker.held(k, a.seconds, now); }
          else { tracker.failed(k); }
          toast(doc, a.text, a.kind, win.setTimeout.bind(win));
          redraw(); refresh();
        });
    }
    doc.addEventListener("click", function (e) {
      var b = e.target.closest ? e.target.closest(".mgmt-poe-button") : null;
      if (b && !b.disabled) { press(b); }
    });
    redraw();
    return {redraw: redraw};
  }

  var api = {createTracker: createTracker, readAnswer: readAnswer, draw: draw, toast: toast, attach: attach,
             ICON: ICON, CONFIRMED_KEPT_MS: CONFIRMED_KEPT_MS};
  if (typeof module !== "undefined") { module.exports = api; }
  if (typeof window !== "undefined") { window.PowerButton = api; }
})();

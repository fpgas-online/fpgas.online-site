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
 * it can still show the old state after the switch confirmed the new one. The tracker therefore keeps each port's
 * confirmed state and draws the button from it until a reading that STARTED after the switch confirmed it arrives;
 * that reading is then believed, whatever it says (someone else may have switched the port since). A reading that
 * merely agrees is not enough (round 5b, the wart seen on deploy 45): after a disable and then an enable, a reading
 * older than both still shows the port enabled, "agreed" with the enable, and the next reading, which had caught up with
 * the disable only, drew the badge "off" during the enable's hold. Both times are the gateway's clock: the reading's
 * start (the switch section's data-read-from, ms) and the answer's `at` (the PoE service's time once the switch was
 * written and read back).
 *
 * The Link badge (round 5, items 4-6: "Clicking the badge should turn the link on / off") is pressed the same way:
 * the same tracker, confirmation, countdown and toasts, against /snmp/link, which holds a port's link apart from its
 * power. While it waits its text turns as the ⏻ does; once confirmed it shows the confirmed state (the PoE-off look for
 * a disabled port, the empty badge for an enabled one with no link yet) until a reading begun after the change arrives.
 *
 * Round 6 (Tim, 18 Oct 2026, site #170, coord-85): from the press on, the button shows the state pressed for (the
 * admin state, never the PoE detection status, which lags), with the twirl until a reading begun after the switch
 * confirmed the change arrives; only then, the state stable, does the hold's countdown show. A PoE press asks no
 * question first; disabling a link still does.
 *
 * The tracker is plain data and functions (tested with node: tests/js/power-button.test.mjs); attach() is the page's
 * glue.
 */
(function () {
  var ICON = "⏻";
  var ACTION_OF = {on: "off", off: "on"};  /* a powered port's button turns it off, an unpowered port's turns it on */
  var LINK_ACTION_OF = {enabled: "disable", disabled: "enable"};
  var BUSY = "⟳";  /* the Link badge's text while the switch confirms */

  /* How long a press waits for a reading begun after the switch confirmed it (the dashboard reads through a 15 s
     cache and the page refreshes every few seconds, so one comes within about 30 s). Past this the twirl stops, the
     state the switch confirmed is drawn and the page says no reading followed: never a twirl that turns for ever. */
  var SETTLE_MS = 90 * 1000;
  /* How long a press waits for the PoE service's answer (it writes the switch and reads it back: a few seconds). Past
     this the request is dropped and the page says so: never a twirl that turns for ever (the review of #171). */
  var ANSWER_MS = 30 * 1000;

  /* `actionOf`: {state: the action a press then does}; the power button's by default.

     Round 6 (Tim, 18 Oct 2026: "I just clicked power off on a device and it showed a red box with countdown for a
     second and then a green box with a countdown"; coord-85: "The countdown should only be shown when the state is
     stable (not in the transition from on->off or off->on, the twirl should appear while transitioning)"): from the
     press on, the button shows the state the press asked for (the admin state, what Tim set), turning, until a
     reading that began after the switch confirmed the change arrives; only then is the state stable, and the hold's
     seconds that are left count down on that look. A reading begun after the confirmation is believed whatever it
     says (someone else may have switched the port since). */
  function createTracker(actionOf) {
    actionOf = actionOf || ACTION_OF;
    /* "switch/port" -> {busy: the press is out, no answer yet; settling: answered, no reading since; until: the hold's
       end (ms); state: what the press set, drawn until a reading begun after `after` arrives; answered: when the
       answer came (ms); stalled: no reading came within SETTLE_MS} */
    var ports = {};
    function get(k) {
      return ports[k] || (ports[k] = {busy: false, settling: false, until: 0, state: null, after: Infinity,
                                      answered: 0, stalled: false});
    }
    function forget(p) { p.busy = false; p.settling = false; p.state = null; p.after = Infinity; p.stalled = false; }
    return {
      /* the press was sent, asking for `state` ("on"/"off"; "enabled"/"disabled"): the button shows it from now */
      start: function (k, state) {
        var p = get(k);
        p.busy = true; p.settling = false; p.stalled = false; p.after = Infinity;
        p.state = Object.prototype.hasOwnProperty.call(actionOf, state) ? state : null;
      },
      /* the switch confirmed `state` and the port is held for `seconds`; `after`: the gateway's time (ms) from which a
         reading is sure to show the change (readAnswer's) */
      confirmed: function (k, state, seconds, now, after) {
        var p = get(k);
        p.busy = false; p.until = now + seconds * 1000;
        if (Object.prototype.hasOwnProperty.call(actionOf, state)) {
          p.state = state; p.after = after; p.settling = true; p.answered = now; p.stalled = false;
        } else { forget(p); }
      },
      /* the port is held by an earlier switch for `seconds` (a 429); nothing was sent */
      held: function (k, seconds, now) { var p = get(k); forget(p); p.until = Math.max(p.until, now + seconds * 1000); },
      /* the press failed: nothing is known to have changed, the button is free again */
      failed: function (k) { forget(get(k)); },
      /* How the button of port `k` is drawn, given the action the latest reading gives it ("on"/"off"; for a link
         "disable"/"enable") and when that reading started (`readFrom`, the gateway's ms; NaN where the ports are not
         from a fresh read). {action, text, busy, disabled, seconds, confirmed, stalled}: `confirmed` is the state the
         press set that no reading since has shown or contradicted, else null; `busy` is the twirl (the press is out, or
         answered with no reading since); `stalled` is true once, at the redraw that gave up waiting; `text` is the
         power button's. */
      view: function (k, readAction, now, readFrom) {
        var p = ports[k], action = readAction, stalled = false;
        if (!p) { return {action: action, text: ICON, busy: false, disabled: false, seconds: 0, confirmed: null, stalled: false}; }
        if (p.state) {
          if (!p.busy && readFrom > p.after) { forget(p); }  /* a reading begun after the change: believe it */
          else {
            action = actionOf[p.state];
            if (p.settling && now - p.answered > SETTLE_MS) { p.settling = false; p.stalled = stalled = true; }
          }
        }
        var confirmed = p.state, turning = p.busy || p.settling;
        if (turning) {
          return {action: action, text: ICON, busy: true, disabled: true, seconds: 0, confirmed: confirmed, stalled: false};
        }
        var seconds = Math.max(0, Math.ceil((p.until - now) / 1000));
        if (seconds) {
          return {action: action, text: String(seconds), busy: false, disabled: true, seconds: seconds,
                  confirmed: confirmed, stalled: stalled};
        }
        if (!p.state) { delete ports[k]; }
        return {action: action, text: ICON, busy: false, disabled: false, seconds: 0, confirmed: confirmed, stalled: stalled};
      },
      /* whether any button still needs redrawing as time passes */
      ticking: function (now) {
        return Object.keys(ports).some(function (k) {
          var p = ports[k];
          return p.busy || p.settling || p.until > now;
        });
      },
    };
  }

  /* What the PoE service's answer to a press means: {kind: "ok"|"held"|"error", state, seconds, after, text}. `where`
     names the port ("switch 2 port 9"); `what` is "power" (/snmp/power answers {"state"}) or "link" (/snmp/link answers
     {"link"}). A confirmation's `at` is the PoE service's time once the switch was written and read back: `after`, in
     ms, the moment after which a reading that started is sure to show the change. An answer with no `at` cannot say
     which readings follow the change: that fails loud, as an error, and the next reading shows the port as it is. */
  function readAnswer(status, body, retryAfter, where, what) {
    var link = what === "link", state = body && (link ? body.link : body.state);
    var states = link ? LINK_ACTION_OF : ACTION_OF;
    if (status >= 200 && status < 300 && typeof state === "string" && Object.prototype.hasOwnProperty.call(states, state)) {
      var seconds = typeof body.next_in === "number" && body.next_in > 0 ? body.next_in : 0;
      var text = (link ? "link " : "PoE ") + state + " on " + where;
      /* an ISO 8601 time with its offset, never a bare one (which a browser reads in its own zone) */
      var at = typeof body.at === "string" && /([+-]\d\d:\d\d|Z)$/.test(body.at) ? Date.parse(body.at) : NaN;
      if (!(at === at)) {  /* NaN */
        return {kind: "error", text: text + ", but the answer carried no time it was confirmed (at: " +
                                     JSON.stringify(body.at) + "), so this page cannot tell which readings follow it"};
      }
      return {kind: "ok", state: state, seconds: seconds, after: at, text: text};
    }
    if (status === 429) {
      var wait = parseInt(retryAfter, 10);
      if (!(wait > 0)) { wait = 1; }
      return {kind: "held", seconds: wait,
              text: (link ? "the link of " : "") + where + " was switched a moment ago; it can be switched again in " +
                    wait + " s"};
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
    /* only on a change: a screen reader re-reads a focused button's name each time it is set (every 250 ms here) */
    if (b.title !== words) { b.title = words; b.setAttribute("aria-label", words + " on port " + b.dataset.port); }
  }

  /* Draw `view` on a Link badge button. The reading's badge is the button's data-cls / data-text (switch-events.js
     changes them when a pushed link change arrives); a state the switch confirmed that the reading does not show yet
     draws as that state: the PoE-off look for a disabled port, the empty badge for an enabled one. */
  function drawLink(b, view) {
    var glyph = b.querySelector(".mgmt-link-glyph");
    /* the reading's badge and its words (data-title: the words, the MACs seen, what a click does) */
    var cls = b.dataset.cls, text = b.dataset.text, state = b.dataset.title || "";
    if (view.confirmed === "disabled") { cls = "mgmt-badge-off"; text = "off"; state = "link disabled at the switch"; }
    else if (view.confirmed === "enabled") { cls = "mgmt-badge-none"; text = ""; state = "link enabled at the switch"; }
    glyph.textContent = view.busy ? BUSY : view.seconds ? String(view.seconds) : text;
    b.dataset.action = view.action;
    b.className = "mgmt-link-badge mgmt-link-button " + cls + (view.busy ? " mgmt-badge-busy" : "") +
      (view.seconds ? " mgmt-badge-wait" : "");
    b.disabled = view.disabled;
    b.setAttribute("aria-busy", view.busy ? "true" : "false");
    /* the badge says what it is first, then what a press does now (or why it cannot yet). The reading's title already
       ends with what a click does; a confirmed state the reading does not show yet, a turning badge and a countdown
       say their own. */
    var words;
    if (view.busy) { words = state.split(";")[0] + "; switching the link, waiting for the switch to confirm"; }
    else if (view.seconds) { words = state.split(";")[0] + "; the link can be switched again in " + view.seconds + " s"; }
    else if (view.confirmed) { words = state + "; a click " + view.action + "s the port at the switch"; }
    else { words = state; }
    /* only on a change: a screen reader re-reads a focused button's name each time it is set */
    if (b.title !== words) {
      b.title = words;
      b.setAttribute("aria-label", "port " + b.dataset.port + ": " + words);
      if (b.parentNode && b.parentNode.title !== undefined) { b.parentNode.title = words; }
    }
  }

  /* The toasts: one stack fixed at the top of the page. A confirmation goes after a few seconds, a refusal or a failure
     stays longer; a "switched a moment ago" goes when the hold it names ends (`seconds`), so it never names a wait that
     is over. Each has a close button. */
  function toast(doc, text, kind, setTimeout, seconds) {
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
    var ms = kind === "ok" ? 6000 : kind === "held" && seconds > 0 ? Math.min(seconds * 1000, 20000) : 20000;
    setTimeout(function () { t.remove(); }, ms);
  }

  function key(sw, port) { return sw + "/" + port; }

  /* The two kinds of button: what they post, what their answer holds, how they are drawn, what a press asks first. */
  var KINDS = {
    power: {selector: ".mgmt-poe-button", actionOf: ACTION_OF, stateOf: {on: "on", off: "off"}, draw: draw,
            body: function (action) { return {on: action === "on"}; },
            /* no question before a PoE press (Tim, 18 Oct 2026: "We also don't need the confirmation popup for power
               cycling."): the twirl, the countdown and the toast are the feedback */
            ask: function () { return ""; }},
    link: {selector: ".mgmt-link-button", actionOf: LINK_ACTION_OF, stateOf: {enable: "enabled", disable: "disabled"},
           draw: drawLink,
           body: function (action) { return {enabled: action === "enable"}; },
           /* enabling a link takes nothing away from anyone: only disabling asks */
           ask: function (action, where, powers) {
             return action === "disable" ? "Disable the link of " + where + "? It carries " + powers + "." : "";
           }},
  };

  /* The page's glue: presses, the countdown, and redrawing after the table refresh. `urls`: {power, link}, the
     PoE service's endpoints. `refresh` reads the switches again. Returns {redraw}: the refresh calls it after
     rebuilding the table. */
  function attach(win, urls, refresh) {
    var doc = win.document, timer = null, trackers = {};
    Object.keys(KINDS).forEach(function (what) { trackers[what] = createTracker(KINDS[what].actionOf); });
    function redraw() {
      var now = Date.now(), ticking = false;
      Object.keys(KINDS).forEach(function (what) {
        var kind = KINDS[what], tracker = trackers[what];
        doc.querySelectorAll(kind.selector).forEach(function (b) {
          if (!b.dataset.readAction) { b.dataset.readAction = b.dataset.action; }  /* what the reading said */
          /* when the reading the table shows started (ms, the gateway's clock); none for a switch not answering */
          var box = b.closest(".mgmt-switch"), from = box ? box.dataset.readFrom : "";
          var readFrom = from ? Number(from) : NaN;
          var view = tracker.view(key(b.dataset.switch, b.dataset.port), b.dataset.readAction, now, readFrom);
          kind.draw(b, view);
          if (view.stalled) {
            toast(doc, (b.dataset.switch === "" ? "" : "switch " + b.dataset.switch + " ") + "port " + b.dataset.port +
                  ": the switch confirmed the change, but no reading of the switch has followed it in " +
                  SETTLE_MS / 1000 + " s, so this page shows what the switch answered, not a reading",
                  "error", win.setTimeout.bind(win), 0);
          }
        });
        ticking = ticking || tracker.ticking(now);
      });
      if (ticking) { if (!timer) { timer = win.setInterval(redraw, 250); } }
      else if (timer) { win.clearInterval(timer); timer = null; }
    }
    function press(what, b) {
      var kind = KINDS[what], tracker = trackers[what];
      var sw = b.dataset.switch, port = b.dataset.port, action = b.dataset.action, k = key(sw, port);
      var where = (sw === "" ? "" : "switch " + sw + " ") + "port " + port;
      var question = kind.ask(action, where, b.dataset.powers || "nothing seen");
      if (question && !win.confirm(question)) { return; }
      tracker.start(k, kind.stateOf[action]); redraw();
      var body = kind.body(action);
      body.switch = sw === "" ? null : Number(sw); body.port = String(port);
      var abort = win.AbortController ? new win.AbortController() : null;
      var late = win.setTimeout(function () { if (abort) { abort.abort(); } }, ANSWER_MS);
      win.fetch(urls[what], {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
                             signal: abort ? abort.signal : undefined})
        .then(function (r) {
          return r.json().catch(function () { return {}; }).then(function (d) {
            return readAnswer(r.status, d, r.headers.get("Retry-After"), where, what);
          });
        })
        .catch(function (e) {
          if (abort && abort.signal.aborted) {
            return {kind: "error", text: where + ": no answer from the site in " + ANSWER_MS / 1000 + " s; the switch may " +
                                         "or may not have been switched, and the next reading shows the port as it is"};
          }
          return {kind: "error", text: where + ": the site did not answer (" + e + ")"};
        })
        .then(function (a) {
          win.clearTimeout(late);
          var now = Date.now();
          if (a.kind === "ok") { tracker.confirmed(k, a.state, a.seconds, now, a.after); }
          else if (a.kind === "held") { tracker.held(k, a.seconds, now); }
          else { tracker.failed(k); }
          toast(doc, a.text, a.kind, win.setTimeout.bind(win), a.seconds);
          redraw(); refresh();
        });
    }
    doc.addEventListener("click", function (e) {
      if (!e.target.closest) { return; }
      Object.keys(KINDS).forEach(function (what) {
        var b = e.target.closest(KINDS[what].selector);
        if (b && !b.disabled) { press(what, b); }
      });
    });
    redraw();
    return {redraw: redraw};
  }

  var api = {createTracker: createTracker, readAnswer: readAnswer, draw: draw, drawLink: drawLink, toast: toast,
             attach: attach, ICON: ICON, BUSY: BUSY, LINK_ACTION_OF: LINK_ACTION_OF, SETTLE_MS: SETTLE_MS,
             ANSWER_MS: ANSWER_MS};
  if (typeof module !== "undefined") { module.exports = api; }
  if (typeof window !== "undefined") { window.PowerButton = api; }
})();

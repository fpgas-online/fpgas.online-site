"use strict";

/*
 * Pushed link changes for the switch port dashboard.
 *
 * The gateway's switch poller publishes each board port's link change; the site forwards it on ws/switches/ as the
 * record {"schema": 1, "switch", "port", "hostname", "link": "up"|"down"|"bounced", "at"}. applyRecord draws it on
 * the page the way the next reading would: the port's link cell and its front-panel mark. It does not fetch
 * anything: the dashboard's JSON comes through a 15 s server cache and would still show the old state. The page's
 * own 15 s refresh stays as the fallback, and corrects anything this drew.
 *
 * The panel state is panel.py's state(): the new link with the port's PoE as the row shows it. The PoE is not in
 * a link record, so the row's PoE cell is the port's PoE part and only the link part changes.
 */
(function () {
  /* The port's PoE as its cell says it (views/switches.py POE_WORDS): "8.6W" and "on" are delivering. */
  function poeOf(text) {
    text = (text || "").trim();
    if (/W$/.test(text) || text === "on") { return "delivering"; }
    return {"searching": "searching", "off": "disabled", "fault": "fault", "not known": "other"}[text] || null;
  }

  /* panel.py state(), from the PoE the row shows. `current` is the mark's state now: a port whose cell says "no PoE"
     is a port without PoE only when it shows plain up or down, else its PoE reading is missing (unknown). */
  function stateFor(poeText, linkUp, current) {
    var poe = poeOf(poeText);
    if (poe === null) {
      if (poeText.trim() === "no PoE" && (current === "up" || current === "down")) { return linkUp ? "up" : "down"; }
      return "unknown";
    }
    if (poe === "other") { return "unknown"; }
    if (poe === "fault") { return "fault"; }
    if (poe === "delivering") { return linkUp ? "power" : "starting"; }
    if (linkUp) { return "up"; }
    return poe === "searching" ? "searching" : "off";
  }

  function stateOf(g, states) {
    var names = states.map(function (s) { return s[0]; });
    var classes = (g.getAttribute("class") || "").split(/\s+/);
    for (var i = 0; i < classes.length; i++) {
      if (classes[i].indexOf("mgmt-port-") === 0 && names.indexOf(classes[i].slice(10)) >= 0) {
        return classes[i].slice(10);
      }
    }
    return null;
  }

  /* The port's title is "port N · <the state in words> · …" (panel.py), with "link up" or "link down" after it
     for an unknown state. */
  function retitle(title, was, now, linkUp, states) {
    var words = {};
    states.forEach(function (s) { words[s[0]] = s[2]; });
    var parts = title.split(" · ");
    if (parts.length < 2) { return title; }
    parts[1] = words[now];
    if (was === "unknown") { parts.splice(2, 1); }
    if (now === "unknown") { parts.splice(2, 0, linkUp ? "link up" : "link down"); }
    return parts.join(" · ");
  }

  /* Draw one link record on `doc`. `states` is panel.STATES ([state, glyph, words] triples). Returns whether the
     page had the port. A "bounced" record says the link flapped between two readings, not where it is now, so it
     draws nothing and the next reading says. */
  function applyRecord(doc, states, record) {
    if (!record || (record.link !== "up" && record.link !== "down")) { return false; }
    var linkUp = record.link === "up";
    var row = doc.getElementById("switch-" + record.switch + "-port-" + record.port);
    if (!row) { return false; }
    var cell = row.children[2];
    if (cell.textContent.split(" ")[0] !== record.link) { cell.textContent = record.link; }
    cell.className = linkUp ? "mgmt-up" : "mgmt-down";
    var g = doc.getElementById("switch-" + record.switch + "-panel-" + record.port);
    if (g) {
      var was = stateOf(g, states);
      var now = stateFor(row.querySelector(".mgmt-poe").textContent, linkUp, was);
      var kept = (g.getAttribute("class") || "").split(/\s+/).filter(function (c) {
        return c !== "mgmt-port-" + was && c !== "mgmt-port-" + now;
      });
      g.setAttribute("class", kept.concat("mgmt-port-" + now).join(" "));
      var title = retitle(g.querySelector("title").textContent, was, now, linkUp, states);
      g.querySelector("title").textContent = title;
      g.querySelector(".mgmt-glyph").textContent = states.filter(function (s) { return s[0] === now; })[0][1];
      g.parentNode.setAttribute("aria-label", title);
    }
    return true;
  }

  /* Keep the socket open: reconnect 1 s after it drops, doubling up to 30 s while the server stays away, and back
     to 1 s only once a connection has stayed up for 30 s (as pistat's dcws.js does). `env` is the browser's
     WebSocket, location and setTimeout/clearTimeout; `onrecord` gets each record. */
  function listen(env, onrecord) {
    var delay = 1000, stable = null;
    function connect() {
      var scheme = env.location.protocol === "https:" ? "wss://" : "ws://";
      var socket = new env.WebSocket(scheme + env.location.host + "/ws/switches/");
      socket.onopen = function () { stable = env.setTimeout(function () { delay = 1000; }, 30000); };
      socket.onmessage = function (e) {
        var record;
        try { record = JSON.parse(e.data); } catch (err) { return; }  /* not a record: the poll still covers it */
        onrecord(record);
      };
      socket.onclose = function () {
        env.clearTimeout(stable);
        env.setTimeout(connect, delay);
        delay = Math.min(delay * 2, 30000);
      };
    }
    connect();
  }

  var api = {applyRecord: applyRecord, stateFor: stateFor, listen: listen};
  if (typeof module !== "undefined") { module.exports = api; }
  if (typeof window !== "undefined") { window.SwitchEvents = api; }
})();

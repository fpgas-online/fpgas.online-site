"use strict";

/*
 * Pushed link changes for the switch port dashboard.
 *
 * The gateway's switch poller publishes each board port's link change; the site forwards it on ws/switches/ as the
 * record {"schema": 1, "switch", "port", "hostname", "link": "up"|"down"|"bounced", "at"}. applyRecord draws it on
 * the page the way the next reading would: the row's Link mark (↑ / ↓, its words in its title and for a screen
 * reader), its Speed (empty while down; a link that comes up keeps what the cell shows until the next reading gives
 * the speed), and the front-panel box's edge (solid while up, dashed while down) and the link words in its title.
 * It does not fetch anything: the dashboard's JSON comes through a 15 s server cache and would still show the old
 * state. The rest of what a link change means (the Net check of a Pi's port, the State) follows at the next reading.
 * The page's own 15 s refresh stays as the fallback and rebuilds the table, so a Keeper remembers each applied record
 * per (switch, port) and draws it again after the rebuild for as long as the reading is older than the record; the
 * first reading at least as new as the record replaces it and the Keeper forgets it.
 */
(function () {
  var WORDS = {up: "link up", down: "link down"};
  var MARKS = {up: "↑", down: "↓"};
  /* the link part of a port's title ("… · link up 1G · …" or "… · link down · …", views/switches.py panel.title) */
  var LINK_PART = /(^| · )link (?:up(?: [^ ·]+)?|down)(?= · |$)/;

  /* Draw one link record on `doc`. Returns whether the page had the port. A "bounced" record says the link flapped
     between two readings, not where it is now, so it draws nothing and the next reading says. */
  function applyRecord(doc, record) {
    if (!record || (record.link !== "up" && record.link !== "down")) { return false; }
    var up = record.link === "up";
    var row = doc.getElementById("switch-" + record.switch + "-port-" + record.port);
    if (!row) { return false; }
    var cell = row.querySelector(".c-link");
    cell.className = "mgmt-mark c-link " + (up ? "mgmt-up" : "mgmt-down");
    cell.title = WORDS[record.link];
    cell.querySelector(".mgmt-glyph").textContent = MARKS[record.link];
    cell.querySelector(".mgmt-sr").textContent = " " + WORDS[record.link];
    if (!up) {
      var speed = row.querySelector(".c-speed");
      speed.textContent = ""; speed.setAttribute("data-sort", "0");
    }
    var g = doc.getElementById("switch-" + record.switch + "-panel-" + record.port);
    if (g) {
      var kept = (g.getAttribute("class") || "").split(/\s+/).filter(function (c) {
        return c && c !== "mgmt-edge-up" && c !== "mgmt-edge-down";
      });
      g.setAttribute("class", kept.concat("mgmt-edge-" + record.link).join(" "));
      var title = g.querySelector("title").textContent;
      var now = LINK_PART.test(title) ? title.replace(LINK_PART, function (all, sep) { return sep + WORDS[record.link]; })
                                      : title;
      /* a link that comes up keeps no speed in the title: the next reading says it */
      g.querySelector("title").textContent = now;
      g.parentNode.setAttribute("aria-label", now);
    }
    return true;
  }

  /* Remembers the records it applied, so the refresh cannot undo them with a reading older than the change. */
  function createKeeper() {
    var kept = {};
    function when(record) { return Date.parse(record.at); }
    return {
      /* Draw `record` and remember it (an older record than the one kept for the port is ignored). */
      apply: function (doc, record) {
        if (!record) { return false; }
        var k = record.switch + "/" + record.port, old = kept[k];
        if (old && when(record) < when(old)) { return false; }
        var drawn = applyRecord(doc, record);
        if (drawn) { kept[k] = record; }
        return drawn;
      },
      /* After the table was rebuilt from `switches` (the dashboard JSON: index and good_at_iso, when the ports were
         last read well): forget each record the reading has caught up with, draw the others again. A switch the
         JSON gives no reading time for cannot be compared, so its records are forgotten (the poll is the truth). */
      reapply: function (doc, switches) {
        var read = {};
        (switches || []).forEach(function (s) { read[s.index] = Date.parse(s.good_at_iso); });
        Object.keys(kept).forEach(function (k) {
          var record = kept[k], at = when(record), seen = read[record.switch];
          if (!(seen < at) || !(at === at)) { delete kept[k]; return; }  /* caught up, unknown, or a bad time */
          applyRecord(doc, record);
        });
      },
      size: function () { return Object.keys(kept).length; }
    };
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

  var api = {applyRecord: applyRecord, listen: listen, createKeeper: createKeeper};
  if (typeof module !== "undefined") { module.exports = api; }
  if (typeof window !== "undefined") { window.SwitchEvents = api; }
})();

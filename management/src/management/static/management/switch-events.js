"use strict";

/*
 * Pushed link changes for the switch port dashboard.
 *
 * The gateway's switch poller publishes each board port's link change; the site forwards it on ws/switches/ as the
 * record {"schema": 1, "switch", "port", "hostname", "link": "up"|"down"|"bounced", "at"}. applyRecord draws it on
 * the page the way the next reading would: the row's Link badge (round 5: the empty badge while down; a link that
 * comes up keeps the speed the badge shows, or is a green ↑ until the next reading gives the speed; a port disabled at
 * the switch keeps its disabled badge, which a link record does not change), its words in its title and for a screen
 * reader, and the front-panel box's link light (lit green while up, dark while down) and the link words in its title.
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
    var cell = row.querySelector(".c-link"), badge = cell.querySelector(".mgmt-link-badge");
    var cls = badge.dataset.cls, text = badge.dataset.text;
    if (cls !== "mgmt-badge-off") {  /* a disabled port has no link whatever a record says; its badge stays */
      if (!up) { cls = "mgmt-badge-none"; text = ""; }
      else if (cls === "mgmt-badge-none") { cls = "mgmt-badge-fast"; text = MARKS.up; }
      var words = up ? (text === MARKS.up ? WORDS.up + "; the speed at the next reading" : WORDS.up) : "no link";
      badge.dataset.cls = cls; badge.dataset.text = text;
      /* the badge's look: the power-button.js glue redraws a pressed one from data-cls / data-text */
      badge.className = badge.className.split(/\s+/).filter(function (c) {
        return c && c.indexOf("mgmt-badge-") !== 0;
      }).concat(cls).join(" ");
      badge.querySelector(".mgmt-link-glyph").textContent = text;
      /* the cell's title is its words, then the MACs the switch saw (and what a click does): only the words change */
      var rest = (cell.title || "").replace(/^(no link|link up[^;]*|link disabled at the switch)/, "");
      cell.title = words + rest;
      if (badge.tagName === "BUTTON" || badge.tagName === "button") {
        badge.title = cell.title; badge.dataset.title = cell.title;  /* power-button.js redraws from data-title */
      }
      cell.querySelector(".mgmt-sr").textContent = " " + words;
      cell.setAttribute("data-sort", up ? (text === MARKS.up ? "10" : cell.getAttribute("data-sort")) : "0");
    }
    /* the port's box in the wide drawing and its twin in the narrow one (a phone's) */
    ["", "-n"].forEach(function (suffix) {
      var g = doc.getElementById("switch-" + record.switch + "-panel-" + record.port + suffix);
      if (!g) { return; }
      var kept = (g.getAttribute("class") || "").split(/\s+/).filter(function (c) {
        return c && c !== "mgmt-link-up" && c !== "mgmt-link-down";
      });
      g.setAttribute("class", kept.concat("mgmt-link-" + record.link).join(" "));
      var title = g.querySelector("title").textContent;
      var now = LINK_PART.test(title) ? title.replace(LINK_PART, function (all, sep) { return sep + WORDS[record.link]; })
                                      : title;
      /* a link that comes up keeps no speed in the title: the next reading says it */
      g.querySelector("title").textContent = now;
      g.parentNode.setAttribute("aria-label", now);
    });
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
      /* After the table was rebuilt from `switches` (the dashboard JSON: index and read_from_ms, when the read that
         gave the ports STARTED, in ms; "" for a switch not answering, whose ports are older): forget each record the
         reading has caught up with (a read begun at or after the change), draw the others again. good_at is not
         used: it is stamped after the ports were read, so a read begun before the change could carry a later one
         (round 5b). A switch not answering has no fresh reading, so its records are kept; a switch the JSON does not
         list cannot be compared, so its records are forgotten (the poll is the truth). */
      reapply: function (doc, switches) {
        var read = {};
        (switches || []).forEach(function (s) {
          read[s.index] = s.read_from_ms === "" ? -Infinity : Number(s.read_from_ms);
        });
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

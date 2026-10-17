"use strict";

/*
 * One switch port's table row, built in the page from a row of /management/switches.json (views.switches._row): the
 * same cells, classes, titles and buttons as the served row (templates/management/port_row.html). The switch port
 * dashboard redraws every row with it after each refresh, and a Pi's own page (/fleet/<serial>/) its port's row (Tim,
 * 18 Oct 2026: "The RPi status page ... should have the same set of data as shown for the port on the switches
 * dashboard"), so the two pages never drift. power-button.js presses the buttons it builds.
 */
(function () {
  function cell(tag, text) { var c = document.createElement(tag); c.textContent = text; return c; }
  /* a cell as the served table has it: its class, its phone label, its title and sort key where it has them */
  function td(cls, label, title, sort) {
    var c = document.createElement("td"); c.className = cls;
    if (label) { c.dataset.label = label; }
    if (title) { c.title = title; }
    if (sort !== undefined && sort !== null) { c.dataset.sort = String(sort); }
    return c;
  }
  /* a one-character mark and its words for a screen reader */
  function mark(parent, text, words) {
    parent.appendChild(document.createTextNode(text));
    var sr = cell("span", " " + words); sr.className = "mgmt-sr"; parent.appendChild(sr);
  }
  /* The PoE button (#67): power-button.js presses it, turns its ⏻ while the switch confirms, counts the port's hold
     down inside it and says the result in a toast at the top of the page (round 5). There is no login (#66): the
     guard is the server, which answers /snmp/power for an access port only (403 for a trunk, an uplink or a port
     outside the access range) and holds a port after each switch (10 s after off, 60 s after on). */
  function powerButton(sw, p) {
    var b = document.createElement("button");
    var g = cell("span", p.power_icon); g.className = "mgmt-poe-glyph"; g.setAttribute("aria-hidden", "true");
    b.appendChild(g);
    b.type = "button"; b.className = "mgmt-poe-button mgmt-poe-to-" + p.poe_action;
    b.setAttribute("aria-label", p.power_title + " on port " + p.port); b.title = p.power_title;
    b.dataset.switch = sw === null || sw === undefined ? "" : String(sw);
    b.dataset.port = String(p.port); b.dataset.action = p.poe_action; b.dataset.powers = p.powers;
    return b;
  }
  /* one row, the same cells as the served table (#130) */
  function fill(tr, p, sw) {
    var th = cell("th", p.port); th.scope = "row"; th.className = "c-port"; if (p.port_title) { th.title = p.port_title; }
    tr.appendChild(th);
    if (p.empty) { tr.className = "mgmt-empty-port"; }
    /* the hardware columns sort the ports with a machine before the empty ones */
    var first = p.machines[0];
    var model = td("c-model", "Model", "", p.hw_sort + (first ? " " + first.model : ""));
    var memory = td("c-memory", "RAM", "", p.memory_sort);
    var board = td("c-board", "Board", "", p.hw_sort + (first ? " " + first.board : ""));
    var state = td("mgmt-mark c-state", "State", "", p.state_sort);
    var serial = td("c-serial", "Serial", "", p.hw_sort + (first ? " " + first.serial : ""));
    var lease = td("c-lease", "Lease", "", p.lease_sort);
    p.machines.forEach(function (m) {
      var d = cell("div", m.model); d.title = m.model_title; model.appendChild(d);
      d = cell("div", m.memory); if (m.memory_title) { d.title = m.memory_title; } memory.appendChild(d);
      d = cell("div", m.board); d.title = m.board_title; board.appendChild(d);
      d = document.createElement("div"); d.className = "mgmt-state-" + m.condition; d.title = m.state_title;
      mark(d, m.mark, m.state_title); state.appendChild(d);
      d = document.createElement("div");
      if (m.url) { var a = cell("a", m.serial); a.href = m.url; d.appendChild(a); } else { d.textContent = m.serial; }
      serial.appendChild(d);
      d = cell("div", m.lease_ago); d.title = m.lease_at; lease.appendChild(d);
    });
    if (p.unregistered) {
      var u = document.createElement("div"); u.className = "mgmt-state-unregistered"; u.title = p.unregistered_title;
      mark(u, p.unregistered, p.unregistered_title); state.appendChild(u);
    }
    [model, memory, board, state, serial, lease].forEach(function (c) { tr.appendChild(c); });
    var net = td("mgmt-mark c-net", "Net", p.net_title, p.net_ok ? 1 : 0), ns = cell("span", p.net);
    ns.className = p.net_cls; net.appendChild(ns);
    var nsr = cell("span", " " + p.net_title); nsr.className = "mgmt-sr"; net.appendChild(nsr); tr.appendChild(net);
    /* the Link badge (round 5): the link and its speed in one 3-character badge; a button on an access port */
    var link = td("mgmt-mark c-link", "Link", p.badge_title, p.badge_sort);
    var badge = document.createElement(p.link_action ? "button" : "span");
    badge.className = "mgmt-link-badge " + (p.link_action ? "mgmt-link-button " : "") + p.badge_cls;
    badge.dataset.cls = p.badge_cls; badge.dataset.text = p.badge;
    if (p.link_action) {
      badge.type = "button"; badge.title = p.badge_title; badge.dataset.title = p.badge_title;
      badge.setAttribute("aria-label", "port " + p.port + ": " + p.badge_title);
      badge.dataset.switch = sw === null || sw === undefined ? "" : String(sw);
      badge.dataset.port = String(p.port); badge.dataset.action = p.link_action; badge.dataset.powers = p.powers;
    }
    var bg = cell("span", p.badge); bg.className = "mgmt-link-glyph"; bg.setAttribute("aria-hidden", "true");
    badge.appendChild(bg); link.appendChild(badge);
    var lsr = cell("span", " " + p.badge_words); lsr.className = "mgmt-sr"; link.appendChild(lsr); tr.appendChild(link);
    var poe = td("mgmt-mark c-poe " + p.poe_cls, "PoE", p.poe_title, p.poe_sort); mark(poe, p.poe, p.poe_title);
    tr.appendChild(poe);
    var w = td("mgmt-n c-watts", "W"); w.textContent = p.watts; tr.appendChild(w);
    /* the PoE button in a column of its own (#89), with what the last press said */
    /* the power symbol on every PoE port (#139): a button where a press is accepted, else greyed with the reason */
    var power = td("c-power", "", p.power_title);
    if (p.poe_action) {
      power.appendChild(powerButton(sw, p));
    } else {
      if (p.power_icon) {
        var none = cell("span", p.power_icon); none.className = "mgmt-poe-none"; none.setAttribute("aria-hidden", "true");
        power.appendChild(none);
      }
      var why = cell("span", p.power_title); why.className = "mgmt-sr"; power.appendChild(why);
    }
    tr.appendChild(power);
    var vlan = td("mgmt-n c-vlan", "VLAN", p.vlan_title, p.vlan_sort); vlan.textContent = p.vlan; tr.appendChild(vlan);
    var gw = td("c-gw", "GW", p.gw_title); gw.textContent = p.gw; tr.appendChild(gw);
    var l = td("c-lldp", "LLDP", p.lldp_title);
    if (p.lldp_url) { var la = cell("a", p.lldp_name); la.href = p.lldp_url; l.appendChild(la); }
    else { l.textContent = p.lldp_name; }
    tr.appendChild(l);
    var lp = td("c-lldp-port", "LLDP port"); lp.textContent = p.lldp_port; tr.appendChild(lp);
    /* an error count above zero is bold bright red (Tim, 17 Oct: "Make the numbers in the errors column bold and right
       red."); the class comes from the view, which says why a zero is not */
    [["c-rx", "In", p.rx, ""], ["c-tx", "Out", p.tx, ""], ["c-rx-errors", "Rx err", p.rx_errors, p.rx_errors_cls],
     ["c-tx-errors", "Tx err", p.tx_errors, p.tx_errors_cls]].forEach(function (n) {
      var c = td("mgmt-n " + n[0] + (n[3] ? " " + n[3] : ""), n[1]); c.textContent = n[2]; tr.appendChild(c);
    });
    var role = td("c-role", "Role"); role.textContent = p.role; tr.appendChild(role);
  }

  var api = {cell: cell, td: td, mark: mark, fill: fill};
  if (typeof module !== "undefined") { module.exports = api; }
  if (typeof window !== "undefined") { window.PortRow = api; }
})();

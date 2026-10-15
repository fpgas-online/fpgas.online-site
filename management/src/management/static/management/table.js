"use strict";

/*
 * Sortable columns for the management tables (Tim, 9 Oct 2026: tables behave like tables).
 *
 * A table with class "mgmt-sortable" gets a button in each column header; a click sorts by that column,
 * a second click reverses it, a third restores the order the page was served in (which stays the default on
 * load). The header carries aria-sort (ascending, descending, none), so a screen reader says which column
 * orders the table. A cell sorts by its data-sort attribute when it has one (a number for a time or a
 * severity), else by its text; numbers sort as numbers. Without JavaScript the table is served sorted and
 * its headers stay plain text. A page that rebuilds a table's body calls MgmtTable.resort(table) to keep the order.
 *
 * The order the reader chose is kept (Tim, 16 Oct 2026, site #138: "The page seems to reload and drop the sort order
 * every few seconds."): it is remembered per page by the table's name and the column's header text, so a table
 * drawn again (a refresh in place below, a reload of the page) is sorted the same way again. A page whose live part
 * is marked data-mgmt-refresh="<seconds>" is refreshed in place: the same URL (its filter included) is fetched, the
 * marked part is swapped for the new one, its tables sorted as the reader had them and scrolled where they were. A
 * refresh that fails says so in #mgmt-live and leaves the tables as they were.
 */
(function () {
  function value(cell) {
    if (!cell) { return ""; }  /* a row with fewer cells (a "nothing here" row spanning the table) sorts as empty */
    var v = cell.getAttribute("data-sort");
    return (v === null ? cell.textContent : v).trim();
  }

  function compare(a, b) {
    var x = Number(a), y = Number(b);
    if (a !== "" && b !== "" && !isNaN(x) && !isNaN(y)) { return x - y; }
    return a.localeCompare(b, undefined, {numeric: true});
  }

  /* The rows' new order: `rows` is [{key, index}], index the served position. direction: "ascending",
     "descending" or "none" (the served order). Equal keys keep the served order. */
  function order(rows, direction) {
    var out = rows.slice();
    out.sort(function (p, q) {
      if (direction === "none") { return p.index - q.index; }
      var c = compare(p.key, q.key);
      if (direction === "descending") { c = -c; }
      return c || p.index - q.index;
    });
    return out;
  }

  /* Sort `table`'s body by column `column` in `direction`. The body is read now, not when the page loaded: a page that
     rebuilds its body (the switch page's refresh) keeps its sort. A row without data-served is numbered by where it
     is, which is the served order of a body that was just built. */
  function sortBody(table, column, direction) {
    var body = table.tBodies[0];
    var rows = Array.prototype.slice.call(body.rows);
    rows.forEach(function (row, i) {
      var served = row.getAttribute("data-served");
      if (served === null || served === undefined) { row.setAttribute("data-served", String(i)); }
    });
    var keyed = rows.map(function (row) {
      return {row: row, key: value(row.cells[column]), index: Number(row.getAttribute("data-served"))};
    });
    order(keyed, direction).forEach(function (item) { body.appendChild(item.row); });
  }

  function heads(table) {
    /* the column headers are the head's last row: a row above it may name groups of columns (#127) */
    return table.tHead.rows[table.tHead.rows.length - 1].cells;
  }

  /* Apply the order the headers say again, after the body was rebuilt. */
  function resort(table) {
    Array.prototype.forEach.call(heads(table), function (th, column) {
      var now = th.getAttribute("aria-sort");
      if (now === "ascending" || now === "descending") { sortBody(table, column, now); }
    });
  }

  /* The reader's sort, per table of this page: {tableName: {column: header text, direction}}. Kept in memory, and in
     sessionStorage so that a reload of the page (or the switch page drawing itself afresh) keeps it too; storage that
     is not there (a private window, blocked site data) only loses the reload case. */
  var chosen = {};
  var storeKey = null;
  if (typeof location !== "undefined" && location) { storeKey = "mgmt-sort:" + location.pathname + location.search; }
  try {
    if (storeKey && typeof sessionStorage !== "undefined") { chosen = JSON.parse(sessionStorage.getItem(storeKey)) || {}; }
  } catch (e) { chosen = {}; }
  function remember() {
    try {
      if (storeKey && typeof sessionStorage !== "undefined") { sessionStorage.setItem(storeKey, JSON.stringify(chosen)); }
    } catch (e) { /* the order is still kept in memory for the refreshes of this page */ }
  }

  /* A header's text as the reader sees it (its words for a screen reader left out). */
  function headText(th) {
    var text = "";
    (function walk(node) {
      Array.prototype.forEach.call(node.childNodes || [], function (child) {
        if (child.nodeType === 3) { text += child.nodeValue; }
        else if (!(child.classList && child.classList.contains("mgmt-sr"))) { walk(child); }
      });
    })(th);
    return text.replace(/\s+/g, " ").trim();
  }

  /* A table's name on its page: the label of the region it scrolls in, else its place among the page's tables. */
  function tableName(table, n) {
    var region = table.closest ? table.closest("[aria-label]") : null;
    var name = region ? region.getAttribute("aria-label") : "";
    return name || "table " + n;
  }

  function enhance(table, n) {
    var cells = heads(table);
    var name = tableName(table, n);
    sortBody(table, 0, "none");  /* numbers the served rows */
    Array.prototype.forEach.call(cells, function (th, column) {
      var text = headText(th);
      var button = document.createElement("button");
      button.type = "button";
      button.className = "mgmt-sort";
      while (th.firstChild) { button.appendChild(th.firstChild); }
      th.appendChild(button);
      th.setAttribute("aria-sort", "none");
      button.addEventListener("click", function () {
        var now = th.getAttribute("aria-sort");
        var next = now === "none" ? "ascending" : now === "ascending" ? "descending" : "none";
        Array.prototype.forEach.call(cells, function (other) { other.setAttribute("aria-sort", "none"); });
        th.setAttribute("aria-sort", next);
        sortBody(table, column, next);
        if (next === "none") { delete chosen[name]; } else { chosen[name] = {column: text, direction: next}; }
        remember();
      });
    });
    /* the order the reader chose before this table was drawn; a column that is no longer there sorts nothing */
    var want = chosen[name];
    if (want && want.column) {
      Array.prototype.forEach.call(cells, function (th, column) {
        if (headText(th) === want.column) {
          th.setAttribute("aria-sort", want.direction);
          sortBody(table, column, want.direction);
        }
      });
    }
  }

  function enhanceAll(root) {
    Array.prototype.forEach.call(root.querySelectorAll("table.mgmt-sortable"), function (table) {
      enhance(table, Array.prototype.indexOf.call(document.querySelectorAll("table.mgmt-sortable"), table));
    });
  }

  function say(text) {
    var p = document.getElementById("mgmt-live");
    if (p) { p.textContent = text; p.hidden = !text; }
  }

  /* Refresh the marked part of the page in place every `seconds` (site #138). */
  function refreshEvery(region, seconds) {
    var busy = false, drawn = new Date();  /* when the tables on screen were read */
    function tick() {
      if (busy) { return; }
      busy = true;
      fetch(location.href, {headers: {"Accept": "text/html"}, cache: "no-store"})
        .then(function (r) {
          if (!r.ok) { throw new Error("HTTP " + r.status); }
          return r.text();
        })
        .then(function (html) {
          var fresh = new DOMParser().parseFromString(html, "text/html").querySelector("[data-mgmt-refresh]");
          if (!fresh) { throw new Error("the page came back without its tables"); }
          /* each scroller's place, by its name, so a wide table stays scrolled where the reader left it */
          var scrolled = {};
          Array.prototype.forEach.call(region.querySelectorAll(".mgmt-scroll"), function (s, i) {
            scrolled[s.getAttribute("aria-label") || i] = [s.scrollLeft, s.scrollTop];
          });
          fresh = document.importNode(fresh, true);
          region.replaceWith(fresh);
          region = fresh;
          enhanceAll(region);
          Array.prototype.forEach.call(region.querySelectorAll(".mgmt-scroll"), function (s, i) {
            var at = scrolled[s.getAttribute("aria-label") || i];
            if (at) { s.scrollLeft = at[0]; s.scrollTop = at[1]; }
          });
          drawn = new Date();
          say("");
        })
        .catch(function (e) {
          say("This page could not be refreshed (" + e.message + "). The tables below were read " +
              Math.round((new Date() - drawn) / 60000) + " min ago.");
        })
        .then(function () { busy = false; });
    }
    setInterval(tick, seconds * 1000);
  }

  if (typeof module !== "undefined") {
    module.exports = {compare: compare, order: order, value: value, resort: resort, headText: headText};
  }
  if (typeof window !== "undefined") { window.MgmtTable = {resort: resort}; }
  if (typeof document !== "undefined") {
    Array.prototype.forEach.call(document.querySelectorAll("table.mgmt-sortable"), enhance);
    var live = document.querySelector ? document.querySelector("[data-mgmt-refresh]") : null;
    if (live) { refreshEvery(live, Number(live.getAttribute("data-mgmt-refresh"))); }
  }
})();

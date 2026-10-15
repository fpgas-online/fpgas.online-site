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

  function enhance(table) {
    var cells = heads(table);
    sortBody(table, 0, "none");  /* numbers the served rows */
    Array.prototype.forEach.call(cells, function (th, column) {
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
      });
    });
  }

  if (typeof module !== "undefined") { module.exports = {compare: compare, order: order, value: value, resort: resort}; }
  if (typeof window !== "undefined") { window.MgmtTable = {resort: resort}; }
  if (typeof document !== "undefined") {
    Array.prototype.forEach.call(document.querySelectorAll("table.mgmt-sortable"), enhance);
  }
})();

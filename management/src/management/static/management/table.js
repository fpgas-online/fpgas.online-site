"use strict";

/*
 * Sortable columns for the management tables (Tim, 9 Oct 2026: tables behave like tables).
 *
 * A table with class "mgmt-sortable" gets a button in each column header; a click sorts by that column,
 * a second click reverses it, a third restores the order the page was served in (which stays the default on
 * load). The header carries aria-sort (ascending, descending, none), so a screen reader says which column
 * orders the table. A cell sorts by its data-sort attribute when it has one (a number for a time or a
 * severity), else by its text; numbers sort as numbers. Without JavaScript the table is served sorted and
 * its headers stay plain text.
 */
(function () {
  function value(cell) {
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

  function enhance(table) {
    var body = table.tBodies[0], heads = table.tHead.rows[0].cells;
    var rows = Array.prototype.slice.call(body.rows);
    rows.forEach(function (row, i) { row.setAttribute("data-served", String(i)); });
    Array.prototype.forEach.call(heads, function (th, column) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "mgmt-sort";
      while (th.firstChild) { button.appendChild(th.firstChild); }
      th.appendChild(button);
      th.setAttribute("aria-sort", "none");
      button.addEventListener("click", function () {
        var now = th.getAttribute("aria-sort");
        var next = now === "none" ? "ascending" : now === "ascending" ? "descending" : "none";
        Array.prototype.forEach.call(heads, function (other) { other.setAttribute("aria-sort", "none"); });
        th.setAttribute("aria-sort", next);
        var keyed = rows.map(function (row) {
          return {row: row, key: value(row.cells[column]), index: Number(row.getAttribute("data-served"))};
        });
        order(keyed, next).forEach(function (item) { body.appendChild(item.row); });
      });
    });
  }

  if (typeof module !== "undefined") { module.exports = {compare: compare, order: order}; }
  if (typeof document !== "undefined") {
    Array.prototype.forEach.call(document.querySelectorAll("table.mgmt-sortable"), enhance);
  }
})();

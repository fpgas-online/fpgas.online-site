// SPDX-License-Identifier: Apache-2.0
// The board page's VGA library panel: prebuilt iCE40UP5K bitstreams of Tiny Tapeout VGA designs, the third design
// source beside the board's demos and uploads (fpga-library spec, section 3, The VGA library panel).
//
// The rules (matching, shuttle order, paging, badges, archive URLs) are plain functions. renderPage() draws one
// panel state with createElement and textContent only. createPanel() is the panel's controller: board.js hands it
// the page's fetch, CSRF reader and hooks, so the node tests can drive it with fakes.
//
// CommonJS for `node --test` (tests/js/library.test.mjs), and window.ttsiteLibrary in the browser.
(function () {
  'use strict';

  const PER_PAGE = 24;
  const NOT_PUBLISHED = 'VGA library not published';
  const NOT_ENABLED = 'VGA library not enabled';
  const NO_SUCH = 'no such library design';
  // the archive path rule (spec section 1): <shuttle>/<macro>/<file>, joined onto the one base URL
  const PATH_RE = /^[a-z0-9]{1,16}\/tt_um_[A-Za-z0-9_]{1,58}\/[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/;
  const BASE_RE = /^https:\/\/[^/?#]+\/([^?#]*\/)?$/;

  /** Every whitespace-separated term is a case-insensitive substring of title, author, shuttle, id or macro. */
  function matches(entry, query) {
    const terms = String(query || '').toLowerCase().split(/\s+/).filter(Boolean);
    const hay = [entry.title, entry.author, entry.shuttle, entry.id, entry.macro].map((v) => String(v || '').toLowerCase());
    return terms.every((t) => hay.some((h) => h.includes(t)));
  }

  /** The shuttles in the order each first appears in the index: the filter's order (neither UI keeps its own). */
  function shuttles(designs) {
    const seen = [];
    designs.forEach((e) => { if (!seen.includes(e.shuttle)) seen.push(e.shuttle); });
    return seen;
  }

  /** The entries matching the query and, unless it is empty, the shuttle, in index order. */
  function filter(designs, query, shuttle) {
    return designs.filter((e) => (!shuttle || e.shuttle === shuttle) && matches(e, query));
  }

  /** One page of items, its number clamped to 1..pages; an empty list is page 1 of 1. */
  function page(items, pageNo, perPage = PER_PAGE) {
    const pages = Math.max(1, Math.ceil(items.length / perPage));
    let n = Math.floor(Number(pageNo));
    if (!Number.isFinite(n) || n < 1) n = 1;
    if (n > pages) n = pages;
    return { items: items.slice((n - 1) * perPage, n * perPage), pageNo: n, pages };
  }

  function hex2(n) {
    return '0x' + n.toString(16).padStart(2, '0');
  }

  /**
   * The badges of an entry: the spec's table (section 1, Warnings), with the same texts, order and classes as the
   * Commander's libraryWarnings (tt-commander-app src/model/fpgaLibrary.ts). A warning is a doubt about how the
   * design behaves on the FPGA: missed timing, PDK latch cells (Ruling BJ), or
   * a board picture that may differ from the preview (Ruling CH). No badge ever disables Load.
   */
  function badges(e) {
    const out = [];
    if (e.build_status === 'timing-fail') {
      out.push(e.fmax_hz < e.clock_hz
        ? { text: 'timing: ' + (e.fmax_hz / 1e6).toFixed(2) + ' MHz < ' + (e.clock_hz / 1e6).toFixed(3) + ' MHz', cls: 'warn' }
        // Ruling AN: the main clock passes; another clock or a cross-domain path fails
        : { text: 'timing not met on a secondary clock or path', cls: 'warn' });
    }
    // Ruling BJ: the design uses PDK latch or clock-gate cells
    if (e.pdk_latches === true) out.push({ text: 'PDK latch cells: may not behave as on silicon', cls: 'warn' });
    // Ruling CH: the board's capture matches the synthesised netlist, not the RTL the preview is rendered from.
    // Nullable: only the JSON boolean true warns; false, null, absent and any other value show nothing.
    if (e.synthesis_differs === true) out.push({ text: 'Board picture may differ from the preview', cls: 'warn' });
    if (e.validation === 'none') {
      out.push({ text: 'not hardware-validated', cls: 'info' });
    } else if (e.validation === 'mcu') {
      out.push({ text: 'validated at MCU speed only', cls: 'info' });
    }
    if (e.qspi === 'emulated') out.push({ text: 'QSPI emulated', cls: 'info' });
    if (e.verdict !== 'ok') out.push({ text: 'video: ' + e.verdict, cls: 'info' });
    // Ruling AH: an all-zero ui_in is not "inputs set"
    if (e.ui_in !== null && e.ui_in !== undefined && e.ui_in !== 0) out.push({ text: 'inputs set: ' + hex2(e.ui_in), cls: 'info' });
    return out;
  }

  /** base + path for an archive path that passes the path rule, under an https:// base ending in /; else null. */
  function assetUrl(base, path) {
    if (typeof base !== 'string' || !BASE_RE.test(base)) return null;
    if (typeof path !== 'string' || !PATH_RE.test(path) || path.includes('..')) return null;
    return base + path;
  }

  // only Docs/Source links whose URL we trust enough to point a click at: https:// only (board.js's safeUrl)
  const safeUrl = (u) => (typeof u === 'string' && /^https:\/\//i.test(u.trim()) ? u.trim() : null);

  /**
   * Disable the buttons that are enabled now and return a function that re-enables exactly those. A button that was
   * already disabled (the gallery's "Running") stays disabled when a failed load or enable lets go. After a
   * re-render the held nodes are detached, and re-enabling them is a no-op.
   */
  function holdButtons(nodes) {
    const held = Array.from(nodes).filter((btn) => !btn.disabled);
    held.forEach((btn) => { btn.disabled = true; });
    return () => held.forEach((btn) => { btn.disabled = false; });
  }

  const STATE_TEXT = {
    'too-old': "This board's software is too old for the VGA library.",
    'loading': 'Loading the VGA library...',
    'not-published': 'The VGA library is not published yet.',
    'empty': 'The VGA library is empty.',
  };

  /**
   * Draw one panel state into container. state.kind is "too-old", "loading", "not-published", "error" (with
   * message and onRetry), "empty" or "ready". A ready state carries designs, baseUrl, query, shuttle, pageNo,
   * onBoard (ids /designs lists with source library), enabled, busy, and the handlers onQuery, onShuttle, onPage
   * and onLoad. Only the current page's cards are in the DOM. A ready redraw keeps the search box and the filter,
   * so the visitor's focus survives typing. Returns the page number drawn (clamped), or null.
   */
  function renderPage(container, state, doc) {
    const el = (tag, cls, text) => {
      const n = doc.createElement(tag);
      if (cls) n.className = cls;
      if (text !== undefined) n.textContent = text;
      return n;
    };
    if (state.kind !== 'ready') {
      container.__ttLibrary = null;
      container.textContent = '';
      if (state.kind === 'error') {
        const p = el('p', 'tt-msg tt-msg--error', state.message || 'unexpected error');
        const retry = el('button', 'tt-btn', 'Retry');
        retry.type = 'button';
        retry.onclick = () => state.onRetry();
        container.append(p, retry);
      } else {
        container.appendChild(el('p', 'tt-library-state', STATE_TEXT[state.kind] || ''));
      }
      return null;
    }

    let ui = container.__ttLibrary;
    if (!ui) {
      container.textContent = '';
      const controls = el('div', 'tt-library-controls');
      const search = el('input', 'tt-library-search');
      search.type = 'search';
      search.placeholder = 'Search title, author, shuttle, macro';
      search.setAttribute('aria-label', 'Search the VGA library');
      const select = el('select', 'tt-library-shuttle');
      select.setAttribute('aria-label', 'Shuttle');
      const count = el('span', 'tt-library-count');
      controls.append(search, select, count);
      const results = el('div', 'tt-library-results');
      container.append(controls, results);
      ui = { search, select, count, results, shuttleKey: null };
      container.__ttLibrary = ui;
    }
    ui.search.oninput = () => state.onQuery(ui.search.value);
    ui.select.onchange = () => state.onShuttle(ui.select.value);
    if (ui.search.value !== state.query) ui.search.value = state.query;
    const sh = shuttles(state.designs);
    const key = sh.join(' ');
    if (ui.shuttleKey !== key) {
      ui.select.textContent = '';
      [['', 'All shuttles']].concat(sh.map((s) => [s, s])).forEach(([value, label]) => {
        const o = el('option', '', label);
        o.value = value;
        ui.select.appendChild(o);
      });
      ui.shuttleKey = key;
    }
    ui.select.value = state.shuttle;

    const items = filter(state.designs, state.query, state.shuttle);
    const p = page(items, state.pageNo);
    ui.count.textContent = items.length + ' of ' + state.designs.length + ' designs';
    ui.results.textContent = '';
    const onBoard = state.onBoard || [];
    if (!items.length) {
      ui.results.appendChild(el('p', 'tt-library-state', 'No design matches the search.'));
    } else {
      const grid = el('ul', 'tt-library-grid');
      p.items.forEach((e) => grid.appendChild(card(e, state, onBoard, el, doc)));
      ui.results.appendChild(grid);
    }
    const pager = el('div', 'tt-library-pager');
    const prev = el('button', 'tt-btn', 'Previous');
    prev.type = 'button';
    prev.disabled = p.pageNo <= 1;
    prev.onclick = () => state.onPage(p.pageNo - 1);
    const next = el('button', 'tt-btn', 'Next');
    next.type = 'button';
    next.disabled = p.pageNo >= p.pages;
    next.onclick = () => state.onPage(p.pageNo + 1);
    pager.append(prev, el('span', 'tt-library-pageno', 'page ' + p.pageNo + ' of ' + p.pages), next);
    ui.results.appendChild(pager);
    return p.pageNo;
  }

  function card(e, state, onBoard, el, doc) {
    const running = state.enabled === e.id;
    const li = el('li', 'tt-library-card' + (running ? ' tt-library-card--enabled' : ''));
    const poster = assetUrl(state.baseUrl, e.poster);
    if (poster) {
      const img = doc.createElement('img');
      img.className = 'tt-library-poster';
      img.loading = 'lazy';
      img.decoding = 'async';
      img.width = 160;
      img.height = 120;
      img.alt = '';
      img.src = poster;
      li.appendChild(img);
    } else {
      li.appendChild(el('div', 'tt-library-poster tt-library-noposter'));
    }
    li.appendChild(el('h3', '', e.title || e.id));
    if (e.author) li.appendChild(el('p', 'tt-library-author', 'by ' + e.author));
    li.appendChild(el('p', 'tt-library-meta', e.shuttle + ' · ' + e.macro));
    const tags = el('p', 'tt-library-badges');
    badges(e).forEach((b) => tags.appendChild(el('span', 'tt-badge tt-badge--' + b.cls, b.text)));
    if (onBoard.includes(e.id)) tags.appendChild(el('span', 'tt-badge tt-badge--library', 'on this board'));
    if (running) tags.appendChild(el('span', 'tt-badge tt-badge--running', 'Running'));
    li.appendChild(tags);
    const load = el('button', 'tt-btn', 'Load');
    load.type = 'button';
    load.disabled = !!state.busy;
    load.onclick = () => state.onLoad(e);
    li.appendChild(load);
    [['Docs', safeUrl(e.docs_url)], ['Source', safeUrl(e.repo_url)], ['Video', assetUrl(state.baseUrl, e.video_10s_webm)]]
      .forEach(([label, url]) => {
        if (!url) return;
        const a = el('a', 'tt-design-link', label + ' ↗');
        a.href = url;
        a.target = '_blank';
        a.rel = 'noopener';
        li.appendChild(a);
      });
    return li;
  }

  // nginx/Django error pages (413, 502, 504, 403, ...) aren't JSON: fall back to the status line (board.js's rule)
  const readJson = async (r) => { try { return await r.json(); } catch { return null; } };
  const fmtErr = (b) => (b && b.error ? b.error + (b.detail ? ' — ' + b.detail : '') : 'unexpected error');
  const errMsg = (b, r) => (b ? fmtErr(b) : 'HTTP ' + r.status + ' ' + r.statusText);

  /** What the visitor is told after a load: the site's answer, the daemon's enable body plus pushed/hides_upload. */
  function loadNotice(title, result) {
    const parts = [title + ' is running'];
    if (result.ui_in !== null && result.ui_in !== undefined && result.ui_in !== 0) parts.push('inputs set: ' + hex2(result.ui_in));
    const held = Array.isArray(result.ui_in_held) ? result.ui_in_held : [];
    if (held.length) parts.push('ui_in ' + held.join(', ') + ' held high on the board, left as inputs');
    if (result.hides_upload) parts.push('an uploaded design with the same name is hidden while this one is on the board');
    return parts.join('; ');
  }

  /**
   * The panel's controller. opts: container, doc, fetch, apiBase, csrf (a function), showMsg(text, isError),
   * setHidden(bool) (the library is switched off: hide the section), setBusy(bool) (disable or restore the
   * gallery's buttons), onIndex() (the index arrived: the gallery can title library entries), onLoaded(result)
   * (reload the gallery and the Commander's list).
   */
  function createPanel(opts) {
    const st = {
      indexKind: 'loading', indexError: '', index: null,
      designs: null, // from /designs: {supported, onBoard, enabled}, or null until it answers
      query: '', shuttle: '', pageNo: 1, busy: false,
    };
    const running = new Set();
    const track = (p) => { running.add(p); p.finally(() => running.delete(p)); return p; };

    function view() {
      if (st.designs && !st.designs.supported) return { kind: 'too-old' };
      if (st.indexKind === 'error') return { kind: 'error', message: st.indexError, onRetry: () => track(loadIndex(true)) };
      if (st.indexKind !== 'ready') return { kind: st.indexKind };
      if (!st.index.designs.length) return { kind: 'empty' };
      return {
        kind: 'ready', designs: st.index.designs, baseUrl: st.index.baseUrl,
        query: st.query, shuttle: st.shuttle, pageNo: st.pageNo, busy: st.busy,
        onBoard: st.designs ? st.designs.onBoard : [], enabled: st.designs ? st.designs.enabled : null,
        onQuery: (q) => { st.query = q; st.pageNo = 1; draw(); },
        onShuttle: (s) => { st.shuttle = s; st.pageNo = 1; draw(); },
        onPage: (n) => { st.pageNo = n; draw(); },
        onLoad: (e) => track(load(e.id)),
      };
    }
    function draw() {
      const n = renderPage(opts.container, view(), opts.doc);
      if (n !== null) st.pageNo = n;
    }
    function busy(on) {
      st.busy = !!on;
      draw();
    }
    function setBusy(on) {
      busy(on);
      if (opts.setBusy) opts.setBusy(!!on);
    }

    async function loadIndex(fresh) {
      st.indexKind = 'loading';
      draw();
      let r;
      try {
        // a reload revalidates (the route sends max-age=60 and an ETag), so a republished index shows at once
        r = await opts.fetch(opts.apiBase + '/library', fresh ? { cache: 'no-cache' } : {});
      } catch (e) {
        st.indexKind = 'error';
        st.indexError = 'Could not reach the site: ' + e;
        draw();
        return;
      }
      const b = await readJson(r);
      if (r.ok && b && Array.isArray(b.designs)) {
        const byId = {};
        b.designs.forEach((e) => { byId[e.id] = e; });
        st.index = { designs: b.designs, baseUrl: b.base_url, byId };
        // a republished index may have dropped the shuttle the visitor filtered on
        if (st.shuttle && !shuttles(b.designs).includes(st.shuttle)) {
          st.shuttle = '';
          st.pageNo = 1;
        }
        st.indexKind = 'ready';
        draw();
        if (opts.onIndex) opts.onIndex();
        return;
      }
      st.index = null;
      if (r.status === 404 && b && b.error === NOT_ENABLED) {
        if (opts.setHidden) opts.setHidden(true);
        return;
      }
      if (r.status === 404 && b && b.error === NOT_PUBLISHED) {
        st.indexKind = 'not-published';
      } else {
        // 503 "board not ready", 502 "VGA library unavailable", an nginx page: the text, and Retry
        st.indexKind = 'error';
        st.indexError = r.ok ? 'unexpected answer from the site' : errMsg(b, r);
      }
      draw();
    }

    async function load(id) {
      if (st.busy) return;
      const entry = st.index && st.index.byId[id];
      const title = entry ? entry.title || id : id;
      setBusy(true);
      opts.showMsg('Loading ' + title + ': fetching from the archive, then programming the FPGA (up to a minute)...', false);
      try {
        let r;
        try {
          r = await opts.fetch(opts.apiBase + '/library/' + encodeURIComponent(id) + '/load', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': opts.csrf() },
            body: '{}',
          });
        } catch (e) {
          opts.showMsg('Could not reach the site: ' + e, true);
          return;
        }
        const b = await readJson(r);
        if (r.ok && b) {
          opts.showMsg(loadNotice(title, b), false);
          if (opts.onLoaded) await opts.onLoaded(b, title);
          return;
        }
        const error = b && b.error;
        if (r.status === 404 && error === NO_SUCH) {
          // a republished index dropped it since this page fetched the index
          opts.showMsg(title + ' is no longer in the library', true);
          await loadIndex(true);
          return;
        }
        if (r.status === 404 && error === NOT_ENABLED) {
          if (opts.setHidden) opts.setHidden(true);
          opts.showMsg(errMsg(b, r), true);
          return;
        }
        let text = errMsg(b, r);
        const retryAfter = r.status === 503 && r.headers && r.headers.get ? r.headers.get('Retry-After') : null;
        if (retryAfter) text += ' (try again in ' + retryAfter + ' s)'; // the archive is still syncing its bins
        if (r.status === 501) st.designs = Object.assign({ onBoard: [], enabled: null }, st.designs, { supported: false });
        opts.showMsg(text, true);
      } finally {
        setBusy(false);
      }
    }

    function setDesigns(b) {
      const list = b && Array.isArray(b.designs) ? b.designs : [];
      st.designs = {
        // an old daemon's /designs has no top-level library object
        supported: !!(b && b.library && typeof b.library === 'object'),
        onBoard: list.filter((d) => d.source === 'library').map((d) => d.name),
        enabled: b ? b.enabled : null,
      };
      draw();
    }

    function title(name) {
      const e = st.index && st.index.byId[name];
      return e ? e.title || name : null;
    }

    return {
      loadIndex: (fresh) => track(loadIndex(fresh)),
      load: (id) => track(load(id)),
      setDesigns, title, busy,
      /** Resolves when every fetch the panel started has finished (for tests). */
      idle: async () => { while (running.size) await Promise.all(Array.from(running)); },
    };
  }

  const api = {
    PER_PAGE, matches, shuttles, filter, page, badges, assetUrl, safeUrl, holdButtons, renderPage, createPanel, loadNotice,
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof window !== 'undefined') window.ttsiteLibrary = api;
})();

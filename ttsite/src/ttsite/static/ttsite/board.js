// SPDX-License-Identifier: Apache-2.0
// board page glue: mount Commander, status pill, pistat log, power-cycle.
// reads #ttsite-board's dataset: data-slug data-kind data-shuttle data-ws-path
// data-api-base data-status-url data-port data-pistat-groups data-commander-js
function mount() {
  const root = document.getElementById('ttsite-board');
  if (!root) return;
  const d = root.dataset;
  const log = document.getElementById('tt-log');
  const appendLog = (t) => {
    if (!log) return;
    log.value += new Date().toLocaleTimeString() + ': ' + t + '\n';
    log.scrollTop = log.scrollHeight;
  };

  // status pill from /board/<slug>/status.json (5 s server cache; poll every 10 s)
  const pill = document.getElementById('tt-status');
  async function refreshStatus() {
    try {
      const r = await fetch(d.statusUrl, { cache: 'no-store' });
      const s = await r.json();
      let cls = 'tt-pill--offline';
      let text = 'Pi offline';
      if (s.reachable && s.board && s.board.present) {
        cls = 'tt-pill--ok';
        text = 'board present' + (s.clients ? ` · ${s.clients} viewer${s.clients === 1 ? '' : 's'}` : '');
      } else if (s.reachable) {
        cls = 'tt-pill--noboard';
        text = 'board not detected';
      }
      if (s.config_error) text += ' · config error';
      pill.className = 'tt-pill ' + cls;
      pill.textContent = text;
      pill.title = s.error || '';
    } catch (e) {
      pill.className = 'tt-pill tt-pill--unknown';
      pill.textContent = 'status unavailable';
    }
  }
  if (pill && d.statusUrl) {
    refreshStatus();
    setInterval(refreshStatus, 10000);
  }

  // pistat status log: subscribe to every group the board is known by
  (d.pistatGroups || '').split(' ').filter(Boolean).forEach((g) => {
    const url = (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws/pistat/' + g + '/';
    const connect = () => {
      const ws = new WebSocket(url);
      ws.onmessage = (e) => {
        try {
          appendLog(JSON.parse(e.data).message);
        } catch {
          appendLog(e.data);
        }
      };
      ws.onclose = () => setTimeout(connect, 5000);
    };
    connect();
  });

  // power-cycle: through the site's gateway proxy when the page provides
  // data-power-url, else via the legacy snmp_switch toggle endpoint
  const power = document.getElementById('tt-power');
  if (power && (d.powerUrl || d.port)) {
    power.onclick = async () => {
      if (!confirm('Power-cycle this board? Anyone else using it will be interrupted.')) return;
      appendLog('power-cycle requested');
      try {
        const r = d.powerUrl
          ? await fetch(d.powerUrl, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ action: 'cycle' }),
            })
          : await fetch('/snmp/toggle', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              // snmp_switch builds "<oid>.<port>", so the port must go over as a string
              body: JSON.stringify({ port: d.port }),
            });
        appendLog(r.ok ? 'power-cycle: done' : `power-cycle: HTTP ${r.status}`);
      } catch (e) {
        appendLog('power-cycle: failed: ' + e);
      }
    };
  }
  const vreset = document.getElementById('tt-video-reset');
  if (vreset) {
    vreset.onclick = () => {
      const v = document.getElementById('tt-video');
      if (v && v.player) v.player.load();
    };
  }

  // Commander embed. data-ws-url is an absolute wss:// URL from the gateway
  // API (consumer mode) and wins over the same-host data-ws-path.
  const mountEl = document.getElementById('tt-commander');
  if (mountEl && d.commanderJs && (d.wsUrl || d.wsPath)) {
    import(d.commanderJs)
      .then(({ mountCommander }) => {
        window.ttCommander = mountCommander(mountEl, {
          transport: {
            kind: 'websocket',
            url: d.wsUrl || (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + d.wsPath,
          },
          board: { slug: d.slug, kind: d.kind, shuttle: d.shuttle || undefined },
          apiBase: d.apiBase,
        });
        appendLog('Commander mounted');
      })
      .catch((e) => {
        mountEl.textContent = 'Could not load the Commander bundle: ' + e;
      });
  }

  // FPGA boards: demo gallery + upload (independent of the Commander bundle)
  const gallery = document.getElementById('tt-gallery');
  if (gallery) {
    const MAX_BITSTREAM_BYTES = 262144; // keep in sync with daemon.MAX_BITSTREAM_BYTES server-side
    const api = gallery.dataset.apiBase;
    const list = document.getElementById('tt-gallery-list');
    const msg = document.getElementById('tt-gallery-msg');
    const csrf = () => (document.cookie.match(/(?:^|; )csrftoken=([^;]+)/) || [])[1] || '';
    const showMsg = (text, isError) => { msg.hidden = !text; msg.textContent = text || ''; msg.className = 'tt-msg' + (isError ? ' tt-msg--error' : ''); };
    const fmtErr = (b) => (b && b.error ? b.error + (b.detail ? ' — ' + b.detail : '') : 'unexpected error');
    // nginx/Django error pages (413, 502, 504, 403, 405, ...) aren't JSON: fall back to the status line
    const readJson = async (r) => { try { return await r.json(); } catch { return null; } };
    const errMsg = (b, r) => (b ? fmtErr(b) : 'HTTP ' + r.status + ' ' + r.statusText);
    // only render Docs/Source links whose URL we trust enough to point a click at
    const safeUrl = (u) => (typeof u === 'string' && /^https?:\/\//i.test(u.trim()) ? u.trim() : null);
    async function loadDesigns() {
      try {
        const r = await fetch(api + '/designs', { cache: 'no-store' });
        const b = await readJson(r);
        if (!r.ok) { list.innerHTML = ''; showMsg(errMsg(b, r), true); return; }
        render(b);
      } catch (e) { list.innerHTML = ''; showMsg('Could not reach the Pi: ' + e, true); }
    }
    function render(b) {
      list.innerHTML = '';
      const items = b.designs || [];
      if (!items.length) { list.innerHTML = '<li class="tt-design">No designs on this board yet.</li>'; return; }
      items.forEach((design) => {
        const li = document.createElement('li');
        const isRunning = b.enabled === design.name;
        li.className = 'tt-design' + (isRunning ? ' tt-design--enabled' : '');
        const h = document.createElement('h4'); h.textContent = design.title || design.name;
        const source = design.source === 'demo' || design.source === 'upload' ? design.source : 'other';
        const badge = document.createElement('span'); badge.className = 'tt-badge tt-badge--' + source; badge.textContent = design.source;
        h.appendChild(badge);
        const p = document.createElement('p'); p.textContent = (design.author ? 'by ' + design.author + ' — ' : '') + (design.description || '');
        const run = document.createElement('button'); run.className = 'tt-btn'; run.type = 'button';
        run.textContent = isRunning ? 'Running' : 'Run';
        run.disabled = isRunning;
        run.onclick = () => enable(design.name, design.clock_hz);
        li.append(h, p, run);
        [['Docs', design.docs_url], ['Source', design.repo_url]].forEach(([label, url]) => {
          const safe = safeUrl(url);
          if (!safe) return;
          const a = document.createElement('a'); a.href = safe; a.target = '_blank'; a.rel = 'noopener'; a.textContent = label + ' ↗'; a.className = 'tt-design-link';
          li.appendChild(a);
        });
        list.appendChild(li);
      });
    }
    async function enable(name, clockHz) {
      showMsg('Loading ' + name + '…', false);
      const buttons = Array.from(list.querySelectorAll('button'));
      buttons.forEach((btn) => { btn.disabled = true; });
      try {
        const r = await fetch(api + '/designs/' + encodeURIComponent(name) + '/enable', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
          body: JSON.stringify(clockHz ? { clock_hz: clockHz } : {}),
        });
        const b = await readJson(r);
        if (!r.ok) { showMsg(errMsg(b, r), true); return; }
        showMsg(name + ' is running', false);
        appendLog('design ' + name + ' loaded');
        await loadDesigns();
        await (window.ttCommander && window.ttCommander.refreshDesigns ? window.ttCommander.refreshDesigns() : Promise.resolve());
      } catch (e) {
        showMsg('Could not reach the Pi: ' + e, true);
      } finally {
        // if loadDesigns() re-rendered, these are stale/detached nodes and this is a no-op;
        // the fresh render already disabled the (new) currently-running design's button
        buttons.forEach((btn) => { btn.disabled = false; });
      }
    }
    const form = document.getElementById('tt-upload');
    form.onsubmit = async (ev) => {
      ev.preventDefault();
      const fileInput = form.elements.file;
      const file = fileInput && fileInput.files && fileInput.files[0];
      if (file && file.size > MAX_BITSTREAM_BYTES) {
        showMsg('Bitstream too large: ' + file.size + ' bytes (limit ' + MAX_BITSTREAM_BYTES + ')', true);
        return;
      }
      const fd = new FormData(form);
      fd.delete('csrfmiddlewaretoken');
      showMsg('Uploading…', false);
      // snapshot the buttons: after enable() re-renders, these are detached nodes and
      // re-enabling them is a no-op, so the fresh 'Running' button stays disabled
      const uploadButtons = Array.from(list.querySelectorAll('button'));
      uploadButtons.forEach((btn) => { btn.disabled = true; });
      try {
        const r = await fetch(api + '/bitstream', { method: 'POST', headers: { 'X-CSRFToken': csrf() }, body: fd });
        const b = await readJson(r);
        if (!r.ok) { showMsg(errMsg(b, r), true); return; }
        const evicted = b.evicted || [];
        showMsg('Uploaded ' + b.name + ' (' + b.size + ' bytes)' + (evicted.length ? '; evicted ' + evicted.join(', ') : ''), false);
        appendLog('uploaded ' + b.name);
        form.reset();
        await enable(b.name, null); // enable() reloads the gallery itself; no need to loadDesigns() first
      } catch (e) {
        showMsg('Upload failed: ' + e, true);
      } finally {
        uploadButtons.forEach((btn) => { btn.disabled = false; });
      }
    };
    loadDesigns();
  }
}

mount();

window.ttsiteBoard = { mount };

// SPDX-License-Identifier: Apache-2.0
// board page glue: mount Commander, status pill, pistat log, power-cycle.
// reads #ttsite-board's dataset: data-slug data-kind data-shuttle data-ws-path
// data-api-base data-status-url data-port data-pistat-groups data-commander-js data-commander-css
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

  // power-cycle via the existing snmp_switch toggle endpoint
  const power = document.getElementById('tt-power');
  if (power && d.port) {
    power.onclick = async () => {
      if (!confirm('Power-cycle this board? Anyone else using it will be interrupted.')) return;
      appendLog('power-cycle requested');
      try {
        // snmp_switch builds "<oid>.<port>", so the port must go over as a string
        const r = await fetch('/snmp/toggle', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
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

  // Commander embed
  const mountEl = document.getElementById('tt-commander');
  if (mountEl && d.commanderJs && d.wsPath) {
    import(d.commanderJs)
      .then(({ mountCommander }) => {
        mountCommander(mountEl, {
          transport: {
            kind: 'websocket',
            url: (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + d.wsPath,
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
}

mount();

window.ttsiteBoard = { mount };

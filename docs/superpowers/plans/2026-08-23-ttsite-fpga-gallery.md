# ttsite phase 2 — FPGA gallery, upload form and daemon API proxies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** FPGA emulation board pages get the demo gallery (cards from the daemon's `/designs` with "Run" buttons), an "Upload your own bitstream" form, and the thin Django proxies to the Pi daemon the embedded Commander and the page use: `/api/board/<slug>/designs`, `…/designs/<name>/enable`, `…/bitstream`.

**Architecture:** `ttsite/daemon.py` grows three helpers (`designs`, `enable`, `upload`) next to `health`, all `requests`-based with a 30 s timeout, never raising into views; `ttsite/views.py` adds `api_designs`, `api_enable`, `api_bitstream` (JSON pass-through of the daemon's status + body, CSRF-protected POSTs via the normal Django middleware, multipart forwarded as a stream); `board.html` + `board.js` render the gallery/upload for `board.kind == "fpga"` and call `window.ttCommander.refreshDesigns()` (the embed 0.2.0 handle) after a successful upload or Run. Everything proxies at the Django layer — nothing but the web UI runs on tweed.

**Tech Stack:** Django 5 (pytest-django), `requests`, vanilla JS module (`board.js`), existing TT theme CSS.

**Spec:** `fpgas.online-infra/docs/superpowers/specs/2026-08-22-tinytapeout-fpgas-online-design.md` §6.4 (API rows), §6.5 item 5 (fpga extras), §9. Daemon contract: see fpgas.online-tt plan `2026-08-23-fpgas-tt-fpga-api.md` Global Constraints (`GET /designs`, `POST /designs/<name>/enable`, `POST /bitstream` multipart `name`+`file` → 201 `{name,size,evicted}`; errors `{error, detail}` with 4xx/5xx). Embed handle: `mountCommander(...)` returns `{unmount, refreshDesigns}` (fork plan `2026-08-23-commander-fpga-kind.md`).

## Global Constraints

- Repo `fpgas-online/fpgas.online-site`; feature branch in `.worktrees/`; PR to `main`; CI (ruff, pytest py3.11/3.12, build) green before merge. Commit trailer on every commit:
  ```
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01UdRpsg6jY8PbcQxxX6txKE
  ```
- URLs (exact): `api/board/<slug:slug>/designs` (GET), `api/board/<slug:slug>/designs/<str:name>/enable` (POST), `api/board/<slug:slug>/bitstream` (POST) in `ttsite/urls.py`; names `ttsite-api-designs`, `ttsite-api-enable`, `ttsite-api-bitstream`.
- Proxy behaviour: board must exist and be `live` else 404 `{"error": "no such live board"}`; kind must be `fpga` else 404 `{"error": "not an fpga board"}`; daemon unreachable/timeout → 502 `{"error": "Pi unreachable", "detail": "<exception text>"}`; daemon response is passed through with **its** status code and JSON body (non-JSON body → 502 `{"error": "bad response from daemon", "detail": "<first 200 chars>"}`); timeouts 30 s (upload 60 s); `enable` forwards the request's JSON body verbatim (empty body allowed); `bitstream` forwards `request.FILES["file"]` and `request.POST["name"]` as multipart; uploads larger than 256 KiB are rejected by Django with 400 before contacting the Pi; `DATA_UPLOAD_MAX_MEMORY_SIZE` default (2.5 MB) is fine.
- CSRF: POST views are normal Django views (CSRF middleware applies); `board.js` sends the `csrftoken` cookie value in the `X-CSRFToken` header for POSTs (the cookie is set by the page: add `{% csrf_token %}` to the upload form so the cookie is issued; `CSRF_COOKIE_HTTPONLY` stays False).
- Templates/JS: the gallery is rendered client-side from `/api/board/<slug>/designs` (the page never blocks on the Pi); `board.html` adds a `<section class="tt-gallery" id="tt-gallery">` with a heading "Designs on this board", a `<ul>` filled by JS (card per design: title, author, description, source badge `demo`/`upload`, Run button, docs/repo links), and the upload form `<form id="tt-upload" enctype="multipart/form-data">` (fields `name` pattern `[a-z0-9_]{1,40}`, `file` accept `.bin`, submit "Upload & load"), plus a short help text linking to the demo repo and the `tt-fpga-compiler`/FabricFox build flow. Only for `board.kind == "fpga"` and `live`.
- `board.js` keeps working without the Commander bundle (gallery + upload are independent of `#tt-commander`); after a successful Run/upload it calls `window.ttCommander?.refreshDesigns?.()` and re-renders the gallery; errors are shown in `#tt-gallery-msg` (`error` + `detail`).
- ISO dates; `uv run pytest`, `uv run ruff check .`; never `/tmp`.

---

## File structure

```
ttsite/src/ttsite/daemon.py                          (modify) designs(), enable(), upload() + _pass_through()
ttsite/src/ttsite/views.py                           (modify) api_designs, api_enable, api_bitstream
ttsite/src/ttsite/urls.py                            (modify) three routes
ttsite/src/ttsite/templates/ttsite/board.html        (modify) fpga gallery + upload sections
ttsite/src/ttsite/static/ttsite/board.js             (modify) gallery rendering, run, upload, refresh hook
ttsite/src/ttsite/static/ttsite/ttsite.css           (modify) .tt-gallery, .tt-design, .tt-upload styles
tests/test_daemon.py, tests/test_views.py, tests/test_static.py  (modify) new tests
```

---

### Task 1: `daemon.py` proxies + views + urls (TDD)

**Files:**
- Modify: `ttsite/src/ttsite/daemon.py`, `ttsite/src/ttsite/views.py`, `ttsite/src/ttsite/urls.py`
- Test: `tests/test_daemon.py`, `tests/test_views.py`

**Interfaces:**
- Produces in `daemon.py`: `DAEMON_API_TIMEOUT = 30.0`, `DAEMON_UPLOAD_TIMEOUT = 60.0`, `def designs(board) -> tuple[int, dict]`, `def enable(board, name: str, body: bytes) -> tuple[int, dict]`, `def upload(board, name: str, fileobj, filename: str) -> tuple[int, dict]` — each returns `(status, json_body)`; on transport errors `(502, {"error": "Pi unreachable", "detail": str(exc)})`; on non-JSON `(502, {"error": "bad response from daemon", "detail": text[:200]})`.
- Produces views: `api_designs(request, slug)`, `api_enable(request, slug, name)`, `api_bitstream(request, slug)` returning `JsonResponse(body, status=status)`; helper `_fpga_board_or_error(slug) -> (board|None, JsonResponse|None)`.

- [ ] **Step 1: Failing tests** — append to `tests/test_daemon.py`:
```python
@pytest.mark.django_db
def test_designs_passes_through_status_and_body(monkeypatch):
    b = Board.objects.create(slug="fpga-1", switch=2, port=33, kind="fpga", title="f")
    calls = {}

    def fake_get(url, timeout, **kw):
        calls["url"], calls["timeout"] = url, timeout
        return FakeResp(200, {"enabled": None, "designs": []})

    monkeypatch.setattr(daemon.requests, "get", fake_get)
    assert daemon.designs(b) == (200, {"enabled": None, "designs": []})
    assert calls == {"url": "http://10.21.2.33:8765/designs", "timeout": 30.0}


@pytest.mark.django_db
def test_enable_posts_body_and_returns_daemon_error_status(monkeypatch):
    b = Board.objects.create(slug="fpga-1", switch=2, port=33, kind="fpga", title="f")
    calls = {}

    def fake_post(url, data=None, headers=None, files=None, timeout=None):
        calls.update(url=url, data=data, headers=headers, timeout=timeout)
        return FakeResp(409, {"error": "another task is running", "detail": ""})

    monkeypatch.setattr(daemon.requests, "post", fake_post)
    assert daemon.enable(b, "tt_um_x", b'{"clock_hz": 10}') == (409, {"error": "another task is running", "detail": ""})
    assert calls["url"] == "http://10.21.2.33:8765/designs/tt_um_x/enable"
    assert calls["data"] == b'{"clock_hz": 10}' and calls["headers"] == {"Content-Type": "application/json"}


@pytest.mark.django_db
def test_upload_sends_multipart(monkeypatch):
    import io
    b = Board.objects.create(slug="fpga-1", switch=2, port=33, kind="fpga", title="f")
    calls = {}

    def fake_post(url, data=None, headers=None, files=None, timeout=None):
        calls.update(url=url, data=data, files=files, timeout=timeout)
        return FakeResp(201, {"name": "my", "size": 4, "evicted": []})

    monkeypatch.setattr(daemon.requests, "post", fake_post)
    status, body = daemon.upload(b, "my", io.BytesIO(b"\x7e\xaa\x99\x7e"), "my.bin")
    assert (status, body["name"]) == (201, "my")
    assert calls["url"] == "http://10.21.2.33:8765/bitstream" and calls["data"] == {"name": "my"}
    assert calls["files"]["file"][0] == "my.bin" and calls["timeout"] == 60.0


@pytest.mark.django_db
def test_daemon_unreachable_and_bad_json(monkeypatch):
    b = Board.objects.create(slug="fpga-1", switch=2, port=33, kind="fpga", title="f")

    def boom(*a, **k):
        raise requests.ConnectionError("no route")

    monkeypatch.setattr(daemon.requests, "get", boom)
    assert daemon.designs(b) == (502, {"error": "Pi unreachable", "detail": "no route"})
    monkeypatch.setattr(daemon.requests, "get", lambda *a, **k: FakeResp(200, bad_json=True))
    status, body = daemon.designs(b)
    assert status == 502 and body["error"] == "bad response from daemon"
```
(`FakeResp` needs a `.text` attribute — add `self.text = "<not json>"` when `bad_json`; keep the class compatible with the existing tests.) Append to `tests/test_views.py`:
```python
def test_api_designs_proxies(c, boards, monkeypatch):
    monkeypatch.setattr(daemon, "designs", lambda b: (200, {"enabled": "x", "designs": []}))
    r = c.get("/api/board/fpga-1/designs")
    assert r.status_code == 200 and r.json() == {"enabled": "x", "designs": []}


def test_api_designs_rejects_non_fpga_and_unknown(c, boards):
    assert c.get("/api/board/tt06/designs").status_code == 404
    assert c.get("/api/board/tt06/designs").json()["error"] == "not an fpga board"
    assert c.get("/api/board/nope/designs").status_code == 404
    assert c.get("/api/board/kianv-1/designs").status_code == 404  # not live


def test_api_enable_forwards_body_and_status(c, boards, monkeypatch):
    seen = {}

    def fake_enable(b, name, body):
        seen["name"], seen["body"] = name, body
        return 502, {"error": "REPL task failed", "detail": "x"}

    monkeypatch.setattr(daemon, "enable", fake_enable)
    r = c.post("/api/board/fpga-1/designs/tt_um_a/enable", data='{"clock_hz": 5}', content_type="application/json")
    assert r.status_code == 502 and r.json()["error"] == "REPL task failed"
    assert seen == {"name": "tt_um_a", "body": b'{"clock_hz": 5}'}
    assert c.get("/api/board/fpga-1/designs/tt_um_a/enable").status_code == 405


def test_api_bitstream_forwards_upload_and_rejects_oversize(c, boards, monkeypatch):
    from django.core.files.uploadedfile import SimpleUploadedFile

    seen = {}

    def fake_upload(b, name, fileobj, filename):
        seen.update(name=name, filename=filename, data=fileobj.read())
        return 201, {"name": name, "size": len(seen["data"]), "evicted": []}

    monkeypatch.setattr(daemon, "upload", fake_upload)
    f = SimpleUploadedFile("d.bin", b"\x7e\xaa\x99\x7e" + b"\x00" * 10, content_type="application/octet-stream")
    r = c.post("/api/board/fpga-1/bitstream", {"name": "my_design", "file": f})
    assert r.status_code == 201 and r.json()["name"] == "my_design"
    assert seen["filename"] == "d.bin" and seen["data"].startswith(b"\x7e\xaa\x99\x7e")
    big = SimpleUploadedFile("big.bin", b"\x00" * (256 * 1024 + 1))
    r = c.post("/api/board/fpga-1/bitstream", {"name": "big", "file": big})
    assert r.status_code == 400 and "large" in r.json()["error"]
    r = c.post("/api/board/fpga-1/bitstream", {"name": "nofile"})
    assert r.status_code == 400
```
(`c` is `Client(HTTP_HOST="tinytapeout.fpgas.online")` — CSRF checks are off in the Django test client by default, which is what we want here; `boards` fixture already has `fpga-1` with `port=12` — that board is live.)

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_daemon.py tests/test_views.py -q` → AttributeError/404.

- [ ] **Step 3: Implement** — `daemon.py`:
```python
DAEMON_API_TIMEOUT = 30.0
DAEMON_UPLOAD_TIMEOUT = 60.0
MAX_BITSTREAM_BYTES = 256 * 1024


def _base(board):
    return f"http://{board.ip}:{DAEMON_PORT}"


def _pass_through(call):
    """Run a requests call; return (status, json) with transport/shape failures folded into 502s."""
    try:
        resp = call()
    except requests.RequestException as exc:
        return 502, {"error": "Pi unreachable", "detail": str(exc)}
    try:
        body = resp.json()
    except ValueError:
        return 502, {"error": "bad response from daemon", "detail": (getattr(resp, "text", "") or "")[:200]}
    if not isinstance(body, dict):
        return 502, {"error": "bad response from daemon", "detail": "not a JSON object"}
    return resp.status_code, body


def designs(board):
    return _pass_through(lambda: requests.get(f"{_base(board)}/designs", timeout=DAEMON_API_TIMEOUT))


def enable(board, name, body: bytes):
    return _pass_through(lambda: requests.post(
        f"{_base(board)}/designs/{name}/enable", data=body or b"{}",
        headers={"Content-Type": "application/json"}, timeout=DAEMON_API_TIMEOUT))


def upload(board, name, fileobj, filename):
    return _pass_through(lambda: requests.post(
        f"{_base(board)}/bitstream", data={"name": name},
        files={"file": (filename, fileobj, "application/octet-stream")}, timeout=DAEMON_UPLOAD_TIMEOUT))
```
`views.py`:
```python
from django.views.decorators.http import require_GET, require_POST


def _fpga_board_or_error(slug):
    try:
        b = Board.objects.get(slug=slug)
    except Board.DoesNotExist:
        return None, JsonResponse({"error": "no such live board", "detail": ""}, status=404)
    if not b.live:
        return None, JsonResponse({"error": "no such live board", "detail": ""}, status=404)
    if b.kind != "fpga":
        return None, JsonResponse({"error": "not an fpga board", "detail": ""}, status=404)
    return b, None


@require_GET
def api_designs(request, slug):
    b, err = _fpga_board_or_error(slug)
    if err:
        return err
    status, body = daemon.designs(b)
    return JsonResponse(body, status=status)


@require_POST
def api_enable(request, slug, name):
    b, err = _fpga_board_or_error(slug)
    if err:
        return err
    status, body = daemon.enable(b, name, request.body)
    return JsonResponse(body, status=status)


@require_POST
def api_bitstream(request, slug):
    b, err = _fpga_board_or_error(slug)
    if err:
        return err
    f = request.FILES.get("file")
    name = (request.POST.get("name") or "").strip()
    if f is None or not name:
        return JsonResponse({"error": "fields 'name' and 'file' are required", "detail": ""}, status=400)
    if f.size > daemon.MAX_BITSTREAM_BYTES:
        return JsonResponse({"error": f"bitstream too large (limit {daemon.MAX_BITSTREAM_BYTES} bytes)", "detail": ""}, status=400)
    status, body = daemon.upload(b, name, f, f.name)
    return JsonResponse(body, status=status)
```
`urls.py`: the three `path(...)` entries before `admin/`.

- [ ] **Step 4: Run** — `uv run pytest -q && uv run ruff check .`.
- [ ] **Step 5: Commit** — `feat(ttsite): daemon API proxies for FPGA boards (designs, enable, bitstream)` + trailer.

---

### Task 2: Gallery + upload UI (template, JS, CSS, tests)

**Files:**
- Modify: `ttsite/src/ttsite/templates/ttsite/board.html`, `static/ttsite/board.js`, `static/ttsite/ttsite.css`, `tests/test_views.py`, `tests/test_static.py`

- [ ] **Step 1: Failing tests** — append to `tests/test_views.py`:
```python
def test_fpga_board_page_has_gallery_and_upload_form(c, boards):
    html = c.get("/board/fpga-1/").content.decode()
    assert 'id="tt-gallery"' in html and 'id="tt-upload"' in html
    assert 'data-api-base="/api/board/fpga-1"' in html
    assert 'name="csrfmiddlewaretoken"' in html
    assert "tinytapeout-fpga-demos" in html


def test_asic_board_page_has_no_gallery(c, boards):
    html = c.get("/board/tt06/").content.decode()
    assert 'id="tt-gallery"' not in html and 'id="tt-upload"' not in html
```
and to `tests/test_static.py` (follow its style — it reads static files from the package): assert `board.js` contains `refreshDesigns` and `/designs` and `X-CSRFToken`, and that `ttsite.css` contains `.tt-gallery`.

- [ ] **Step 2: Implement**
  - `board.html`: inside the `{% if live %}` branch, after the `tt-board-grid` div, add for `board.kind == "fpga"`:
    ```html
    {% if board.kind == "fpga" %}
    <section class="tt-gallery" id="tt-gallery" data-api-base="{{ board.api_base }}">
      <h2>Designs on this board</h2>
      <p id="tt-gallery-msg" class="tt-msg" hidden></p>
      <ul id="tt-gallery-list" class="tt-design-list"><li class="tt-design tt-design--loading">Loading designs from the Pi…</li></ul>
      <h3>Upload your own bitstream</h3>
      <form id="tt-upload" enctype="multipart/form-data" method="post" action="{{ board.api_base }}/bitstream">
        {% csrf_token %}
        <label>Name <input name="name" required pattern="[a-z0-9_]{1,40}" placeholder="tt_um_my_design" maxlength="40"></label>
        <label>Bitstream (.bin, iCE40UP5K) <input name="file" type="file" accept=".bin,application/octet-stream" required></label>
        <button class="tt-btn" type="submit">Upload &amp; load</button>
      </form>
      <p class="tt-help">Build one from a Tiny Tapeout project with the FabricFox flow (yosys → nextpnr-ice40 --up5k --package sg48 → icepack, top <code>tt_fpga_top</code>) — see the
        <a href="https://github.com/fpgas-online/tinytapeout-fpga-demos" target="_blank" rel="noopener">demo repo</a> for working examples and the
        <a href="https://github.com/TinyTapeout/tt-fpga-compiler" target="_blank" rel="noopener">tt-fpga-compiler</a> harness. Uploads are capped at 256 KiB and 16 files (oldest uploads are evicted); everyone sees the same board.</p>
    </section>
    {% endif %}
    ```
    and the existing `fpga` `tt-notice` paragraph is removed (replaced by the gallery).
  - `board.js`: after the Commander mount, keep the handle: `window.ttCommander = mountCommander(...)`. Add:
    ```js
    // FPGA boards: demo gallery + upload (independent of the Commander bundle)
    const gallery = document.getElementById('tt-gallery');
    if (gallery) {
      const api = gallery.dataset.apiBase;
      const list = document.getElementById('tt-gallery-list');
      const msg = document.getElementById('tt-gallery-msg');
      const csrf = () => (document.cookie.match(/(?:^|; )csrftoken=([^;]+)/) || [])[1] || '';
      const showMsg = (text, isError) => { msg.hidden = !text; msg.textContent = text || ''; msg.className = 'tt-msg' + (isError ? ' tt-msg--error' : ''); };
      const fmtErr = (b) => (b && b.error ? b.error + (b.detail ? ' — ' + b.detail : '') : 'unexpected error');
      async function loadDesigns() {
        try {
          const r = await fetch(api + '/designs', { cache: 'no-store' });
          const b = await r.json();
          if (!r.ok) { list.innerHTML = ''; showMsg(fmtErr(b), true); return; }
          render(b);
        } catch (e) { showMsg('Could not reach the Pi: ' + e, true); }
      }
      function render(b) {
        list.innerHTML = '';
        if (!b.designs.length) { list.innerHTML = '<li class="tt-design">No designs on this board yet.</li>'; return; }
        b.designs.forEach((d) => {
          const li = document.createElement('li');
          li.className = 'tt-design' + (b.enabled === d.name ? ' tt-design--enabled' : '');
          const h = document.createElement('h4'); h.textContent = d.title || d.name;
          const badge = document.createElement('span'); badge.className = 'tt-badge tt-badge--' + d.source; badge.textContent = d.source;
          h.appendChild(badge);
          const p = document.createElement('p'); p.textContent = (d.author ? 'by ' + d.author + ' — ' : '') + (d.description || '');
          const run = document.createElement('button'); run.className = 'tt-btn'; run.type = 'button';
          run.textContent = b.enabled === d.name ? 'Running' : 'Run';
          run.onclick = () => enable(d.name, d.clock_hz);
          li.append(h, p, run);
          [['Docs', d.docs_url], ['Source', d.repo_url]].forEach(([label, url]) => {
            if (!url) return; const a = document.createElement('a'); a.href = url; a.target = '_blank'; a.rel = 'noopener'; a.textContent = label + ' ↗'; a.className = 'tt-design-link'; li.appendChild(a);
          });
          list.appendChild(li);
        });
      }
      async function enable(name, clockHz) {
        showMsg('Loading ' + name + '…', false);
        try {
          const r = await fetch(api + '/designs/' + encodeURIComponent(name) + '/enable', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() }, body: JSON.stringify(clockHz ? { clock_hz: clockHz } : {}) });
          const b = await r.json();
          if (!r.ok) { showMsg(fmtErr(b), true); return; }
          showMsg(name + ' is running', false); appendLog('design ' + name + ' loaded');
          await loadDesigns(); await (window.ttCommander && window.ttCommander.refreshDesigns ? window.ttCommander.refreshDesigns() : Promise.resolve());
        } catch (e) { showMsg('Could not reach the Pi: ' + e, true); }
      }
      const form = document.getElementById('tt-upload');
      form.onsubmit = async (ev) => {
        ev.preventDefault();
        const fd = new FormData(form); fd.delete('csrfmiddlewaretoken');
        showMsg('Uploading…', false);
        try {
          const r = await fetch(api + '/bitstream', { method: 'POST', headers: { 'X-CSRFToken': csrf() }, body: fd });
          const b = await r.json();
          if (!r.ok) { showMsg(fmtErr(b), true); return; }
          showMsg('Uploaded ' + b.name + ' (' + b.size + ' bytes)' + (b.evicted.length ? '; evicted ' + b.evicted.join(', ') : ''), false);
          appendLog('uploaded ' + b.name);
          form.reset();
          await loadDesigns(); await enable(b.name, null);
        } catch (e) { showMsg('Upload failed: ' + e, true); }
      };
      loadDesigns();
    }
    ```
    (`appendLog` already exists in `board.js`; keep everything inside `mount()`.)
  - `ttsite.css`: `.tt-gallery { margin: 1.5rem 0 }`, `.tt-design-list { list-style: none; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 0.8rem }`, `.tt-design { border: 1px solid #e3e3ea; border-radius: 6px; padding: 0.8rem }`, `.tt-design--enabled { border-color: var(--tt-primary); box-shadow: 0 0 0 2px var(--tt-secondary) }`, `.tt-design h4 { margin: 0 0 0.3rem; font-size: 1rem }`, `.tt-badge { font-size: 0.7rem; margin-left: 0.4rem; padding: 0.1rem 0.4rem; border-radius: 3px; background: #eee }`, `.tt-badge--demo { background: var(--tt-secondary) }`, `.tt-design-link { margin-left: 0.6rem; font-size: 0.85rem }`, `.tt-msg { padding: 0.5rem 0.8rem; border-radius: 6px; background: #eef }`, `.tt-msg--error { background: #fde8e8 }`, `#tt-upload label { display: block; margin: 0.4rem 0 }` (use the theme's existing custom properties; check their names in the `:root` block).

- [ ] **Step 3: Run** — `uv run pytest -q && uv run ruff check .`; manual render check via the test client is covered by the tests.
- [ ] **Step 4: Commit** — `feat(ttsite): FPGA board page — design gallery with Run, bitstream upload form, Commander refresh` + trailer.

---

### Task 3: Bump embed version expectation, docs, PR

- [ ] **Step 1:** README (ttsite section) — document the three API routes and the fpga page extras; note `TTSITE_COMMANDER_VERSION` should be `0.2.0` for `refreshDesigns` (older bundles still work; the page guards the call).
- [ ] **Step 2:** PR `ttsite-fpga-gallery` → CI green → merge.

---

## Self-review

- Spec §6.4: `/api/board/<slug>/designs`, `…/enable`, `…/bitstream` proxies with 30 s timeout, daemon error JSON passed through with status ✓; `/kianv/boot` is phase 3. §6.5 item 5 fpga extras (gallery with Run, upload form, link to instructions + demo repo) ✓. §9 upload rejected → daemon error+detail shown ✓; Pi unreachable → 502 message ✓.
- Placeholders: none. Types: `daemon.designs/enable/upload` → `(status, dict)`; views use them; JS uses `api_base` from the existing `Board.api_base`.

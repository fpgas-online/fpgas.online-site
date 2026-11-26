import json
import logging
import time

import redis
from django.conf import settings
from django.core.cache import cache, caches
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.gzip import gzip_page
from django.views.decorators.http import condition, require_GET, require_POST

from . import boards, daemon, library
from .docs_links import SECTIONS

log = logging.getLogger(__name__)

STATUS_CACHE_SECONDS = 5
STATUS_PENDING_SECONDS = 3
NOT_CONNECTED = {"reachable": False, "error": "not connected"}
# nginx's one internal location for a board's serial bridge (fpgas.online-infra roles/ttsite ws-board.conf.j2);
# the Pi's address follows it.
SERIAL_INTERNAL = "/_tt-serial/"
PENDING = {"reachable": False, "error": "pending"}
DESIGNS_CACHE_SECONDS = 5

# the VGA library routes (spec section 3, Routes)
now = time.monotonic  # the clock the load deadline is read against; tests replace it
LOAD_DEADLINE_SECONDS = 50  # under nginx's 60 s proxy_read_timeout for /api/
# The lock's TTL is above the deadline, so a lock left by a dead worker expires after its load could have ended.
# The lock is freed with a plain delete, with no owner token: that is safe because every step of a load (the index
# read, the state, the fetch, the push, each enable) is bounded by the one deadline D = start + 50 s, so the load has
# ended, and its delete has run, before its key could expire at 70 s and be taken by another load.
LOCK_SECONDS = 70
ENABLE_MIN_SECONDS = 27  # the daemon's 25 s load deadline plus a margin
RETRY_MIN_SECONDS = 32  # enough for a second push and the enable
INDEX_DEADLINE_SECONDS = 20  # the index route's own fetch of fpga-library.json
UPLOAD_CHECK_SECONDS = 5  # the upload-name check's fetch of a cold index
BOARD_CHECK_SECONDS = 60  # the index route's board check is cached per slug (an answer or a 404)
BOARD_NOT_READY_SECONDS = STATUS_CACHE_SECONDS  # but a 503 "board not ready" only as long as the status (Ruling AW)
MISSING_BIN_RETRY_AFTER = "30"  # the onward rsync is still copying the bins
INDEX_CACHE_CONTROL = "private, max-age=60"
STALE_HEADER = "X-TTSite-Library-Stale"


def _common(request):
    return {
        "TTSITE_HOST": settings.TTSITE_HOST,
        "COMMANDER_VERSION": settings.TTSITE_COMMANDER_VERSION,
        "STATIC_URL": settings.STATIC_URL,
    }


def index(request):
    """Every board, filed by what it is: a board some Pi's boot check reported by what that check read from it,
    a catalogue row nothing reported by the row's own kind."""
    ctx = _common(request)
    every = boards.pages()
    ctx.update(
        asic_boards=[p for p in every if p.kind == boards.ASIC],
        kianv_boards=[p for p in every if p.kind == "kianv"],
        fpga_boards=[p for p in every if p.kind == boards.FPGA],
        unknown_boards=[p for p in every if p.kind == boards.UNKNOWN],
    )
    return render(request, "ttsite/index.html", ctx)


def _page_or_404(slug):
    page = boards.page(slug)
    if page is None:
        raise Http404(f"no board {slug}")
    return page


def board(request, slug):
    b = _page_or_404(slug)
    ctx = _common(request)
    # Per-board Commander flavour: each pins its own bundle version and lives
    # in its own static directory, but ships the same filenames + mount API.
    if b.commander == "legacy":
        commander_version = settings.TTSITE_COMMANDER_LEGACY_VERSION
        commander_dir = f"tt-commander/legacy-{commander_version}"
    else:
        commander_version = settings.TTSITE_COMMANDER_VERSION
        commander_dir = f"tt-commander/{commander_version}"
    ctx.update(
        board=b,
        live=b.live is not None,
        commander_version=commander_version,
        commander_dir=commander_dir,
        shuttle_url=f"https://tinytapeout.com/chips/{b.shuttle}/" if b.shuttle else "",
        # the Pi's status log group is its hostname: its own pistat curls
        # (/pistat/stat/%l/) and the fleet bridge both send there
        pistat_groups=[b.hostname] if b.live else [],
        can_power_cycle=b.can_power_cycle,
        # the VGA library panel: off while the setting is empty (the routes answer 404 then too)
        library_enabled=bool(settings.TTSITE_LIBRARY_BASE_URL),
        library_base_url=settings.TTSITE_LIBRARY_BASE_URL,
    )
    return render(request, "ttsite/board.html", ctx)


def board_status(request, slug):
    # the cache first: every viewer of a board polls this, and finding the board costs the fleet's tables
    key = f"ttsite:health:{slug}"
    data = cache.get(key)
    if data is None:
        b = _page_or_404(slug)
        if b.live is None:
            data = dict(NOT_CONNECTED)
        else:
            # short-lived negative placeholder: concurrent pollers get an answer
            # instead of each opening their own request to the daemon
            cache.set(key, dict(PENDING), STATUS_PENDING_SECONDS)
            data = daemon.health(b)
        cache.set(key, data, STATUS_CACHE_SECONDS)
    return JsonResponse(data)


@require_GET
def serial_ws(request, slug):
    """Hand the Commander's WebSocket to the Pi that carries the board now. nginx sends /ws/board/<slug>/serial
    here and follows X-Accel-Redirect to its one internal location, which proxies to the address named (the
    request's Upgrade headers go with it). No nginx location names a board or a port."""
    b = boards.page(slug)
    if b is None or b.live is None:
        return JsonResponse({"error": "no such live board", "detail": ""}, status=404)
    if not b.has_commander:
        return JsonResponse({"error": "board not ready", "detail": b.why_no_controls}, status=503)
    response = HttpResponse(status=200)
    response["X-Accel-Redirect"] = f"{SERIAL_INTERNAL}{b.ip}"
    response["X-Accel-Buffering"] = "no"
    return response


def docs(request):
    ctx = _common(request)
    ctx.update(sections=SECTIONS)
    return render(request, "ttsite/docs.html", ctx)


def _fpga_board_or_error(slug):
    """The page of a board a boot check reported as an FPGA demo board, or the refusal to send."""
    b = boards.page(slug)
    if b is None or b.live is None:
        return None, JsonResponse({"error": "no such live board", "detail": ""}, status=404)
    if not b.live.current:
        return None, JsonResponse({"error": "board not ready", "detail": b.why_no_controls}, status=503)
    if b.live.kind != boards.FPGA:
        return None, JsonResponse({"error": "not an fpga board", "detail": b.live.reason}, status=404)
    if not b.has_gallery:  # an FPGA board whose Pi is not offered: its check did not pass (issue #70)
        return None, JsonResponse({"error": "board not ready", "detail": b.why_no_controls}, status=503)
    return b, None


def _designs_cache_key(slug):
    return f"ttsite:designs:{slug}"


@require_GET
def api_designs(request, slug):
    # the cache first: the gallery polls this, and finding the board costs the fleet's tables
    key = _designs_cache_key(slug)
    cached = cache.get(key)
    if cached is not None:
        status, body = cached
    else:
        b, err = _fpga_board_or_error(slug)
        if err:
            return err
        status, body = daemon.designs(b)
        # a daemon/transport failure (5xx) is transient -- don't freeze it in the cache
        if status < 500:
            cache.set(key, (status, body), DESIGNS_CACHE_SECONDS)
    return JsonResponse(body, status=status)


ENABLE_FORWARDED_KEYS = ("clock_hz",)
LIBRARY_ONLY_KEYS = ("ui_in", "ui_in_mask")


def _refuse_constant(name):
    raise ValueError(f"{name} is not JSON")


def _enable_body(raw: bytes):
    """The body api_enable sends to the daemon: only `clock_hz`. ui_in and ui_in_mask are set only by a VGA library
    load, from the index (review I1): (bytes, None), or (None, the refusal)."""
    try:
        sent = json.loads(raw.decode("utf-8"), parse_constant=_refuse_constant) if raw.strip() else {}
    except (ValueError, RecursionError):  # UnicodeDecodeError is a ValueError
        sent = None
    if not isinstance(sent, dict):
        return None, JsonResponse({"error": "body must be a JSON object", "detail": ""}, status=400)
    if any(key in sent for key in LIBRARY_ONLY_KEYS):
        return None, JsonResponse(
            {"error": "ui_in and ui_in_mask are set only by a VGA library load", "detail": ""}, status=400
        )
    return json.dumps({k: sent[k] for k in ENABLE_FORWARDED_KEYS if k in sent}).encode(), None


@require_POST
def api_enable(request, slug, name):
    b, err = _fpga_board_or_error(slug)
    if err:
        return err
    body, refusal = _enable_body(request.body)
    if refusal:
        return refusal
    status, body = daemon.enable(b, name, body)
    if status < 400:
        cache.delete(_designs_cache_key(slug))
    return JsonResponse(body, status=status)


def _library_ids():
    """The VGA library's ids, for refusing an upload that takes one. A worker with no copy of the index fetches it,
    with a 5 s deadline; when the library is off, not published, or the archive is down with no copy, there are
    none and the upload goes ahead."""
    if not settings.TTSITE_LIBRARY_BASE_URL:
        return set()
    try:
        return library.ids(now() + UPLOAD_CHECK_SECONDS)
    except library.LibraryDisabled:
        return set()
    except library.NotPublished:
        log.debug("VGA library: upload-name check: the library is not published")
        return set()
    except library.ArchiveError as exc:
        log.warning("VGA library: upload-name check without an index (%s)", exc.message)
        return set()


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
        return JsonResponse(
            {"error": f"bitstream too large (limit {daemon.MAX_BITSTREAM_BYTES} bytes)", "detail": ""}, status=400
        )
    if name in _library_ids():
        return JsonResponse({"error": f"{name} is a VGA library design; pick another", "detail": ""}, status=409)
    status, body = daemon.upload(b, name, f, f.name)
    if status < 400:
        cache.delete(_designs_cache_key(slug))
    return JsonResponse(body, status=status)


# --- the VGA library


def _library_error(status, error, detail=""):
    return JsonResponse({"error": error, "detail": detail}, status=status)


def _library_off():
    """The answer while TTSITE_LIBRARY_BASE_URL is empty: the library is switched off."""
    return _library_error(404, "VGA library not enabled")


def _archive_error(exc):
    """The answer for a failed index read. LibraryDisabled is not an archive error, so it is never caught here."""
    if isinstance(exc, library.NotPublished):
        return _library_error(404, "VGA library not published")
    return _library_error(502, "VGA library unavailable", exc.message)


def _library_board_or_error(slug):
    """_fpga_board_or_error for the index route, its outcome cached for 60 s per slug: (ok, error response)."""
    key = f"ttsite:library:board:{slug}"
    cached = cache.get(key)
    if cached is None:
        _b, err = _fpga_board_or_error(slug)
        cached = (True, None) if err is None else (False, (err.status_code, json.loads(err.content)))
        ttl = BOARD_NOT_READY_SECONDS if err is not None and err.status_code == 503 else BOARD_CHECK_SECONDS
        cache.set(key, cached, ttl)
    ok, refusal = cached
    return ok, (None if ok else JsonResponse(refusal[1], status=refusal[0]))


def _library_index_outcome(request, slug):
    """The index route's whole answer, worked out once per request (the ETag function runs first, then the view):
    ("ok", (body, etag, stale age)) or ("error", response)."""
    outcome = getattr(request, "_ttsite_library", None)
    if outcome is None:
        if not settings.TTSITE_LIBRARY_BASE_URL:
            outcome = ("error", _library_off())
        else:
            ok, err = _library_board_or_error(slug)
            if not ok:
                outcome = ("error", err)
            else:
                try:
                    outcome = ("ok", library.index(now() + INDEX_DEADLINE_SECONDS))
                except library.LibraryDisabled:
                    outcome = ("error", _library_off())
                except library.ArchiveError as exc:
                    outcome = ("error", _archive_error(exc))
        request._ttsite_library = outcome
    return outcome


def _library_etag(request, slug):
    kind, value = _library_index_outcome(request, slug)
    return value[1] if kind == "ok" else None


@require_GET
@condition(etag_func=_library_etag)  # outside gzip_page: the 304 is decided on the uncompressed body's ETag
@gzip_page
def api_library(request, slug):
    """The normalised VGA library index, the same for every FPGA board."""
    kind, value = _library_index_outcome(request, slug)
    if kind == "error":
        return value
    body, _etag, stale = value
    response = HttpResponse(body, content_type="application/json")
    response["Cache-Control"] = INDEX_CACHE_CONTROL
    if stale is not None:
        response[STALE_HEADER] = str(stale)
    return response


@require_POST
def api_library_load(request, slug, name):
    """Load one VGA library design: push its bytes to the Pi when it lacks them, then enable it (spec section 3,
    the load route's steps 1 to 9). The body is ignored; every value comes from the index."""
    deadline = now() + LOAD_DEADLINE_SECONDS
    if not settings.TTSITE_LIBRARY_BASE_URL:
        return _library_off()
    # 1. the board, the name and the entry
    b, err = _fpga_board_or_error(slug)
    if err:
        return err
    if not library.NAME_RE.match(name):
        return _library_error(404, "no such library design")
    try:
        entry = library.entry(name, deadline)
    except library.LibraryDisabled:
        return _library_off()
    except library.ArchiveError as exc:
        return _archive_error(exc)
    if entry is None:
        return _library_error(404, "no such library design")
    # 2. the board's lock, shared by every worker
    key = f"library-load:{slug}"
    locks = caches["ttsite"]
    try:
        taken = locks.add(key, 1, timeout=LOCK_SECONDS)
    except (redis.exceptions.RedisError, OSError) as exc:
        log.warning("VGA library: the load lock for %s could not be taken: %s", slug, exc)
        return _library_error(503, "the site cannot take the board lock; try again")
    if not taken:
        return _library_error(409, "a library load is already running on this board")
    try:
        status, body = _load_locked(b, slug, name, entry, deadline)
    finally:
        try:
            locks.delete(key)
        except (redis.exceptions.RedisError, OSError) as exc:  # the TTL frees it
            log.warning("VGA library: the load lock for %s could not be freed: %s", slug, exc)
    response = JsonResponse(body, status=status)
    if status == 503 and body.get("error") == library.MSG_BIN_MISSING:
        response["Retry-After"] = MISSING_BIN_RETRY_AFTER
    return response


def _push(b, name, entry, deadline):
    """Fetch the entry's bin from the archive and push it to the Pi: None and hides_upload, or the failure."""
    try:
        data = library.fetch_bin(entry, deadline)
    except library.LibraryDisabled:
        return (404, {"error": "VGA library not enabled", "detail": ""}), False
    except library.ArchiveError as exc:
        # the index arrives before its bins during every publish: a missing bin is a wait, not a fault
        status = 503 if exc.message == library.MSG_BIN_MISSING else 502
        return (status, {"error": exc.message, "detail": ""}), False
    status, body = daemon.library_push(b, name, data, entry["bin_sha256"], deadline=deadline)
    if status >= 400:
        return (status, body), False
    return None, bool(body.get("hides_upload"))


def _held(state, name, sha256):
    entries = state.get("entries") if isinstance(state, dict) else None
    if not isinstance(entries, list):
        return False
    return any(isinstance(e, dict) and e.get("id") == name and e.get("bin_sha256") == sha256 for e in entries)


def _load_locked(b, slug, name, entry, deadline):
    """Steps 3 to 9 of the load, under the board's lock: (status, body)."""
    # 3. what the Pi holds
    try:
        status, state = daemon.library_state(b, deadline=deadline)
    except daemon.Unsupported:
        return 501, {"error": "board software too old",
                     "detail": "this board's fpgas-tt daemon has no VGA library support yet"}  # fmt: skip
    if status != 200:
        return status, state
    # 4. and 5. push unless the Pi holds this build
    pushed, hides_upload = False, False
    if not _held(state, name, entry["bin_sha256"]):
        failure, hides_upload = _push(b, name, entry, deadline)
        if failure:
            return failure
        pushed = True
    enable_body = json.dumps(
        {"clock_hz": entry["clock_hz"], "ui_in": entry["ui_in"], "ui_in_mask": entry["ui_in_mask"]}
    ).encode()
    # 6. and 7. enable, with time left for the daemon's own deadline
    if deadline - now() < ENABLE_MIN_SECONDS:
        return daemon._out_of_time()
    status, body = daemon.enable(b, name, enable_body, deadline=deadline)
    # 8. the Pi lost the entry since step 3 (a reboot, an eviction): push once more, then enable once more
    if status == 404 and not pushed and deadline - now() >= RETRY_MIN_SECONDS:
        failure, hides_upload = _push(b, name, entry, deadline)
        if failure:
            return failure
        pushed = True
        if deadline - now() < ENABLE_MIN_SECONDS:
            return daemon._out_of_time()
        status, body = daemon.enable(b, name, enable_body, deadline=deadline)
    if status >= 400:
        return status, body
    # 9. the gallery's next poll sees the new design
    cache.delete(_designs_cache_key(slug))
    return status, {**body, "pushed": pushed, "hides_upload": hides_upload}

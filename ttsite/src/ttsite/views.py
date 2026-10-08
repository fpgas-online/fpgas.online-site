from django.conf import settings
from django.core.cache import cache
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from . import boards, daemon
from .docs_links import SECTIONS

STATUS_CACHE_SECONDS = 5
STATUS_PENDING_SECONDS = 3
NOT_CONNECTED = {"reachable": False, "error": "not connected"}
# nginx's one internal location for a board's serial bridge (fpgas.online-infra roles/ttsite ws-board.conf.j2);
# the Pi's address follows it.
SERIAL_INTERNAL = "/_tt-serial/"
PENDING = {"reachable": False, "error": "pending"}
DESIGNS_CACHE_SECONDS = 5


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


@require_POST
def api_enable(request, slug, name):
    b, err = _fpga_board_or_error(slug)
    if err:
        return err
    status, body = daemon.enable(b, name, request.body)
    if status < 400:
        cache.delete(_designs_cache_key(slug))
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
        return JsonResponse(
            {"error": f"bitstream too large (limit {daemon.MAX_BITSTREAM_BYTES} bytes)", "detail": ""}, status=400
        )
    status, body = daemon.upload(b, name, f, f.name)
    if status < 400:
        cache.delete(_designs_cache_key(slug))
    return JsonResponse(body, status=status)

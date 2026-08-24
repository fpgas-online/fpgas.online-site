from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST

from . import daemon
from .docs_links import SECTIONS
from .models import Board

STATUS_CACHE_SECONDS = 5
STATUS_PENDING_SECONDS = 3
COMING_SOON = {"reachable": False, "error": "coming soon"}
PENDING = {"reachable": False, "error": "pending"}
DESIGNS_CACHE_SECONDS = 5


def _common(request):
    return {
        "TTSITE_HOST": settings.TTSITE_HOST,
        "COMMANDER_VERSION": settings.TTSITE_COMMANDER_VERSION,
        "STATIC_URL": settings.STATIC_URL,
    }


def index(request):
    ctx = _common(request)
    ctx.update(
        asic_boards=Board.objects.filter(kind="asic"),
        kianv_boards=Board.objects.filter(kind="kianv"),
        fpga_boards=Board.objects.filter(kind="fpga"),
    )
    return render(request, "ttsite/index.html", ctx)


def board(request, slug):
    b = get_object_or_404(Board, slug=slug)
    ctx = _common(request)
    ctx.update(
        board=b,
        live=b.live,
        shuttle_url=f"https://tinytapeout.com/chips/{b.shuttle}/" if b.shuttle else "",
        pistat_groups=[b.hostname, f"pi{b.port}"] if b.live else [],
        # /snmp/toggle drives the first switch only, so hide the button elsewhere
        can_power_cycle=b.live and b.switch == 1,
    )
    return render(request, "ttsite/board.html", ctx)


def board_status(request, slug):
    b = get_object_or_404(Board, slug=slug)
    if not b.live:
        return JsonResponse(dict(COMING_SOON))
    key = f"ttsite:health:{b.slug}"
    data = cache.get(key)
    if data is None:
        # short-lived negative placeholder: concurrent pollers get an answer
        # instead of each opening their own request to the daemon
        cache.set(key, dict(PENDING), STATUS_PENDING_SECONDS)
        data = daemon.health(b)
        cache.set(key, data, STATUS_CACHE_SECONDS)
    return JsonResponse(data)


def docs(request):
    ctx = _common(request)
    ctx.update(sections=SECTIONS)
    return render(request, "ttsite/docs.html", ctx)


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


def _designs_cache_key(slug):
    return f"ttsite:designs:{slug}"


@require_GET
def api_designs(request, slug):
    b, err = _fpga_board_or_error(slug)
    if err:
        return err
    key = _designs_cache_key(slug)
    cached = cache.get(key)
    if cached is not None:
        status, body = cached
    else:
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

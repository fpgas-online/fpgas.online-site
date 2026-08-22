from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render

from . import daemon
from .docs_links import SECTIONS
from .models import Board

STATUS_CACHE_SECONDS = 5


def _common(request):
    return {
        "TTSITE_HOST": settings.TTSITE_HOST,
        "COMMANDER_VERSION": settings.TTSITE_COMMANDER_VERSION,
        "STATIC_URL": settings.STATIC_URL,
    }


def index(request):
    ctx = _common(request)
    ctx.update(
        asic_slots=Board.asic_slots(),
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
    )
    return render(request, "ttsite/board.html", ctx)


def board_status(request, slug):
    b = get_object_or_404(Board, slug=slug)
    key = f"ttsite:health:{b.slug}"
    data = cache.get(key)
    if data is None:
        data = daemon.health(b)
        cache.set(key, data, STATUS_CACHE_SECONDS)
    return JsonResponse(data)


def docs(request):
    ctx = _common(request)
    ctx.update(sections=SECTIONS)
    return render(request, "ttsite/docs.html", ctx)

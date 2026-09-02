import json
import logging

from django.conf import settings
from django.core.cache import cache
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from . import daemon
from . import gateway as gw
from .docs_links import SECTIONS
from .models import Board

logger = logging.getLogger(__name__)

STATUS_CACHE_SECONDS = 5
STATUS_PENDING_SECONDS = 3
COMING_SOON = {"reachable": False, "error": "coming soon"}
PENDING = {"reachable": False, "error": "pending"}
DESIGNS_CACHE_SECONDS = 5


# -- gateway consumer mode helpers (FPGAS_GATEWAYS non-empty) --


def _gateways():
    return gw.gateways_from_settings()


def _site_location():
    """Text for the 'hosted at ...' lines; from /api/site per gateway, or the legacy setting."""
    gws = _gateways()
    if not gws:
        return settings.TTSITE_SITE_LOCATION
    parts = []
    for g in gws:
        try:
            info = g.site_info()
            parts.append(info.get("location") or info.get("name") or g.id)
        except gw.GatewayError as exc:
            logger.warning("gateway %s: %s", g.id, exc)
            parts.append(g.id)
    return " and ".join(parts)


def _gateway_boards():
    """Boards merged from every gateway: configured-gateway order, then /api/boards order (stable)."""
    boards = []
    for g in _gateways():
        try:
            site_name = g.site_name()
            boards.extend(gw.GatewayBoard(b, g, site_name) for b in g.boards())
        except gw.GatewayError as exc:
            logger.warning("gateway %s unavailable, boards skipped: %s", g.id, exc)
    return boards


def _resolve_gateway(site):
    for g in _gateways():
        if g.id == site:
            return g
    return None


def _owning_gateway(slug, site):
    """The gateway owning (site, slug); with site=None, the unique owner of slug across all."""
    if site is not None:
        return _resolve_gateway(site)
    owners = []
    for g in _gateways():
        try:
            if any(b.get("slug") == slug for b in g.boards()):
                owners.append(g)
        except gw.GatewayError:
            continue
    return owners[0] if len(owners) == 1 else None


def _gateway_error_response(exc):
    status = 503 if exc.kind == gw.GatewayError.UNREACHABLE else 502
    return JsonResponse({"error": "gateway error", "detail": str(exc)}, status=status)


def _common(request):
    return {
        "TTSITE_HOST": settings.TTSITE_HOST,
        "COMMANDER_VERSION": settings.TTSITE_COMMANDER_VERSION,
        "STATIC_URL": settings.STATIC_URL,
        "SITE_LOCATION": _site_location(),
    }


def index(request):
    ctx = _common(request)
    if _gateways():
        boards = _gateway_boards()
        ctx.update(
            asic_boards=[b for b in boards if b.kind == "asic"],
            kianv_boards=[b for b in boards if b.kind == "kianv"],
            fpga_boards=[b for b in boards if b.kind == "fpga"],
        )
    else:
        ctx.update(
            asic_boards=Board.objects.filter(kind="asic"),
            kianv_boards=Board.objects.filter(kind="kianv"),
            fpga_boards=Board.objects.filter(kind="fpga"),
        )
    return render(request, "ttsite/index.html", ctx)


def board(request, slug, site=None):
    if site is None and _gateways():
        # legacy single-site route: redirect when the slug has exactly one owner
        owners = [b for b in _gateway_boards() if b.slug == slug]
        if len(owners) == 1:
            return redirect(owners[0].page_url)
        raise Http404("no such board")
    if site is not None:
        return _gateway_board_page(request, site, slug)

    b = get_object_or_404(Board, slug=slug)
    ctx = _common(request)
    ctx.update(
        board=b,
        live=b.live,
        site_name=settings.TTSITE_SITE_NAME,
        shuttle_url=f"https://tinytapeout.com/chips/{b.shuttle}/" if b.shuttle else "",
        pistat_groups=[b.hostname, f"pi{b.port}"] if b.live else [],
        # /snmp/toggle drives the first switch only, so hide the button elsewhere
        can_power_cycle=b.live and b.switch == 1,
        status_url=f"/board/{b.slug}/status.json",
        api_base=b.api_base,
        ws_path=b.serial_ws_path,
        ws_url="",
        power_url="",
        gw_api_base="",
    )
    return render(request, "ttsite/board.html", ctx)


def _gateway_board_page(request, site, slug):
    g = _resolve_gateway(site)
    if g is None:
        raise Http404("no such site")
    try:
        site_name = g.site_name()
        data = next((b for b in g.boards() if b.get("slug") == slug), None)
    except gw.GatewayError as exc:
        return JsonResponse({"error": "gateway error", "detail": str(exc)}, status=503)
    if data is None:
        raise Http404("no such board")
    b = gw.GatewayBoard(data, g, site_name)
    proxy_base = f"/api/board/{site}/{slug}"
    ctx = _common(request)
    ctx.update(
        board=b,
        live=b.live,
        site_name=b.site_name,
        shuttle_url=f"https://tinytapeout.com/chips/{b.shuttle}/" if b.shuttle else "",
        pistat_groups=[],  # consumer mode drops the local channels layer (D-11)
        can_power_cycle=b.live,  # the gateway answers 503 when PoE is not configured
        status_url=f"/board/{site}/{slug}/status.json",
        api_base=proxy_base,
        ws_path="",
        # absolute URLs from /api/boards, used verbatim -- never derived here
        ws_url=b.serial_ws_url,
        power_url=f"{proxy_base}/power",
        gw_api_base=b.api_base,
    )
    return render(request, "ttsite/board.html", ctx)


def board_status(request, slug, site=None):
    if site is None and _gateways():
        owners = [b for b in _gateway_boards() if b.slug == slug]
        if len(owners) == 1:
            return redirect(f"/board/{owners[0].site_id}/{slug}/status.json")
        raise Http404("no such board")
    if site is not None:
        return _gateway_board_status(request, site, slug)

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


def _gateway_board_status(request, site, slug):
    g = _resolve_gateway(site)
    if g is None:
        raise Http404("no such site")
    try:
        status, body = g.board_status(slug)
    except gw.GatewayError as exc:
        # same shape the status pill already understands; 200 like the legacy view
        return JsonResponse({"reachable": False, "error": str(exc)})
    if status == 404:
        return JsonResponse({"error": "no such board", "detail": slug}, status=404)
    if status != 200:
        return JsonResponse({"reachable": False, "error": str(body.get("error") or f"HTTP {status}")})
    data = dict(body)
    data.setdefault("reachable", False)
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


def _proxy_gateway_or_error(slug, site):
    """(gateway, None) to proxy to, or (None, JsonResponse) when it cannot be resolved."""
    g = _owning_gateway(slug, site)
    if g is None:
        return None, JsonResponse({"error": "no such live board", "detail": ""}, status=404)
    return g, None


def _designs_cache_key(slug):
    return f"ttsite:designs:{slug}"


@require_GET
def api_designs(request, slug, site=None):
    if site is not None or _gateways():
        g, err = _proxy_gateway_or_error(slug, site)
        if err:
            return err
        try:
            status, body = g.designs(slug)
        except gw.GatewayError as exc:
            return _gateway_error_response(exc)
        return JsonResponse(body, status=status)

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
def api_enable(request, slug, name, site=None):
    if site is not None or _gateways():
        g, err = _proxy_gateway_or_error(slug, site)
        if err:
            return err
        try:
            status, body = g.enable(slug, name, request.body)
        except gw.GatewayError as exc:
            return _gateway_error_response(exc)
        return JsonResponse(body, status=status)

    b, err = _fpga_board_or_error(slug)
    if err:
        return err
    status, body = daemon.enable(b, name, request.body)
    if status < 400:
        cache.delete(_designs_cache_key(slug))
    return JsonResponse(body, status=status)


@require_POST
def api_bitstream(request, slug, site=None):
    gateway_mode = site is not None or bool(_gateways())
    if gateway_mode:
        g, err = _proxy_gateway_or_error(slug, site)
        if err:
            return err
    else:
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
    if gateway_mode:
        try:
            status, body = g.bitstream(slug, f.read(), name, f.name)
        except gw.GatewayError as exc:
            return _gateway_error_response(exc)
        return JsonResponse(body, status=status)
    status, body = daemon.upload(b, name, f, f.name)
    if status < 400:
        cache.delete(_designs_cache_key(slug))
    return JsonResponse(body, status=status)


# csrf_exempt for parity with the legacy /snmp/toggle flow this replaces: the
# site is fully open (no accounts) and the browser JS posts without a form.
@csrf_exempt
@require_POST
def api_power(request, slug, site=None):
    if not _gateways() and site is None:
        # legacy mode keeps using /snmp/toggle (snmp_switch app); no local SNMP here
        return JsonResponse({"error": "no such live board", "detail": ""}, status=404)
    g, err = _proxy_gateway_or_error(slug, site)
    if err:
        return err
    try:
        payload = json.loads(request.body) if request.body else {}
    except ValueError:
        payload = {}
    action = payload.get("action") or "cycle"
    if action not in ("on", "off", "cycle"):
        return JsonResponse({"error": "invalid action", "detail": str(action)}, status=400)
    try:
        status, body = g.power(slug, action)
    except gw.GatewayError as exc:
        return _gateway_error_response(exc)
    return JsonResponse(body, status=status)

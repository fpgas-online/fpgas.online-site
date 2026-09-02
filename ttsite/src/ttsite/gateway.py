"""HTTP client for the fpgas-online-gw gateway service.

Implements the consumer side of the "Gateway service contract"
(tweed-split-design docs/07-pr-contract.md).  One :class:`Gateway` per
configured entry in ``settings.FPGAS_GATEWAYS``; the site talks to the
gateway's public API instead of deriving ``10.21.<switch>.<port>``
addresses and speaking to Pis/switches itself.

Method shapes mirror how the views consume them:

* ``site()`` / ``boards()`` / ``board_status(slug)`` return parsed JSON and
  raise :class:`GatewayError` on any failure; results (and failures) are
  cached in-process for ``CACHE_SECONDS``.
* The action/proxy calls -- ``designs``, ``enable``, ``bitstream``,
  ``power`` -- return ``(status_code, body_dict)`` so the Django views can
  pass gateway/daemon statuses through verbatim (like ``ttsite.daemon``
  does today); :class:`GatewayError` is raised only for transport failures
  or unparseable responses.
"""

import threading
import time
from urllib.parse import quote

import httpx
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

CONNECT_TIMEOUT = 3.0
READ_TIMEOUT = 3.0
# designs/enable/bitstream are daemon pass-throughs on the gateway side and can
# legitimately take longer than 3 s (REPL tasks, flashing); mirror ttsite.daemon.
DAEMON_READ_TIMEOUT = 30.0
UPLOAD_READ_TIMEOUT = 45.0
CACHE_SECONDS = 5.0
MAX_BITSTREAM_BYTES = 256 * 1024

KIND_DISPLAY = {"asic": "TT ASIC", "kianv": "KianV RISC-V", "fpga": "FPGA emulation"}


class GatewayError(Exception):
    """A gateway could not be used: unreachable, bad response, or HTTP error."""

    UNREACHABLE = "unreachable"
    BAD_RESPONSE = "bad-response"
    HTTP = "http"

    def __init__(self, gateway_id, kind, detail="", status=None):
        self.gateway_id = gateway_id
        self.kind = kind
        self.detail = detail
        self.status = status
        super().__init__(f"gateway {gateway_id}: {kind}" + (f": {detail}" if detail else ""))


class Gateway:
    """Client for one gateway (``/api/site``, ``/api/boards``, per-board calls)."""

    def __init__(self, id, url, token="", transport=None):
        self.id = id
        self.url = url.rstrip("/")
        self.token = token
        self._client = httpx.Client(
            base_url=self.url,
            timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT),
            transport=transport,
        )
        self._cache = {}
        self._lock = threading.Lock()

    def close(self):
        self._client.close()

    # -- plumbing --

    def _headers(self):
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _request(self, method, path, read_timeout=None, **kw):
        if read_timeout is not None:
            kw["timeout"] = httpx.Timeout(read_timeout, connect=CONNECT_TIMEOUT)
        headers = self._headers()
        headers.update(kw.pop("headers", {}))
        try:
            return self._client.request(method, path, headers=headers, **kw)
        except httpx.TimeoutException as exc:
            raise GatewayError(self.id, GatewayError.UNREACHABLE, f"timeout: {exc}") from exc
        except httpx.HTTPError as exc:
            raise GatewayError(self.id, GatewayError.UNREACHABLE, str(exc)) from exc

    def _json(self, resp):
        try:
            body = resp.json()
        except ValueError as exc:
            raise GatewayError(
                self.id, GatewayError.BAD_RESPONSE, f"invalid JSON: {exc}", status=resp.status_code
            ) from exc
        if not isinstance(body, dict):
            raise GatewayError(self.id, GatewayError.BAD_RESPONSE, "not a JSON object", status=resp.status_code)
        return body

    def _get_json(self, path):
        resp = self._request("GET", path)
        body = self._json(resp)
        if resp.status_code != 200:
            detail = body.get("error") or body.get("detail") or f"HTTP {resp.status_code}"
            raise GatewayError(self.id, GatewayError.HTTP, str(detail), status=resp.status_code)
        return body

    def _cached(self, key, fetch):
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
        if hit is not None and now - hit[0] < CACHE_SECONDS:
            if isinstance(hit[1], GatewayError):
                raise hit[1]
            return hit[1]
        try:
            value = fetch()
        except GatewayError as exc:
            # cache failures too: a down gateway must not add a 3 s stall to
            # every page render, only one per CACHE_SECONDS
            with self._lock:
                self._cache[key] = (time.monotonic(), exc)
            raise
        with self._lock:
            self._cache[key] = (time.monotonic(), value)
        return value

    def _pass_through(self, method, path, read_timeout=READ_TIMEOUT, **kw):
        """(status, dict) from a gateway proxy endpoint, statuses forwarded verbatim."""
        resp = self._request(method, path, read_timeout=read_timeout, **kw)
        return resp.status_code, self._json(resp)

    # -- API --

    def site(self):
        """``GET /api/site`` -> ``{"site": {...}, "public_base": ..., "version": ...}`` (cached)."""
        return self._cached("site", lambda: self._get_json("/api/site"))

    def site_info(self):
        """The ``site`` object from ``/api/site`` (``{id, name, location, timezone}``)."""
        info = self.site().get("site")
        return info if isinstance(info, dict) else {}

    def site_name(self):
        return self.site_info().get("name") or self.id

    def boards(self):
        """``GET /api/boards`` -> list of board dicts (cached)."""

        def fetch():
            boards = self._get_json("/api/boards").get("boards")
            if not isinstance(boards, list):
                raise GatewayError(self.id, GatewayError.BAD_RESPONSE, "no 'boards' list in /api/boards")
            return boards

        return self._cached("boards", fetch)

    def board_status(self, slug):
        """``GET /api/board/<slug>/status`` -> (status, body), cached per slug."""
        path = f"/api/board/{quote(slug, safe='')}/status"
        return self._cached(("status", slug), lambda: self._pass_through("GET", path))

    def designs(self, slug):
        return self._pass_through(
            "GET", f"/api/board/{quote(slug, safe='')}/designs", read_timeout=DAEMON_READ_TIMEOUT
        )

    def enable(self, slug, name, body=b""):
        return self._pass_through(
            "POST",
            f"/api/board/{quote(slug, safe='')}/designs/{quote(name, safe='')}/enable",
            read_timeout=DAEMON_READ_TIMEOUT,
            content=body or b"{}",
            headers={"Content-Type": "application/json"},
        )

    def bitstream(self, slug, blob, name, filename="bitstream.bin"):
        return self._pass_through(
            "POST",
            f"/api/board/{quote(slug, safe='')}/bitstream",
            read_timeout=UPLOAD_READ_TIMEOUT,
            data={"name": name},
            files={"file": (filename, blob, "application/octet-stream")},
        )

    def power(self, slug, action=None):
        """``GET`` the PoE admin state, or ``POST {"action": on|off|cycle}``."""
        path = f"/api/board/{quote(slug, safe='')}/power"
        if action is None:
            return self._pass_through("GET", path)
        return self._pass_through("POST", path, read_timeout=DAEMON_READ_TIMEOUT, json={"action": action})


# -- settings plumbing --

_instances = {}
_instances_lock = threading.Lock()
# test hook: an httpx transport (e.g. MockTransport) used for new Gateway instances
_default_transport = None


def gateways_from_settings():
    """Gateway clients for ``settings.FPGAS_GATEWAYS``, in configured order.

    Instances are memoised per (id, url, token) so their in-process caches and
    connection pools survive across requests.  An empty/missing setting returns
    ``[]`` -- the site then behaves exactly as before (co-located legacy mode).
    """
    entries = getattr(settings, "FPGAS_GATEWAYS", None) or []
    out = []
    for entry in entries:
        try:
            gw_id, url = entry["id"], entry["url"]
        except (TypeError, KeyError) as exc:
            raise ImproperlyConfigured(f"FPGAS_GATEWAYS entries need 'id' and 'url': {entry!r}") from exc
        token = entry.get("token", "")
        key = (gw_id, url, token, id(_default_transport))
        with _instances_lock:
            gw = _instances.get(key)
            if gw is None:
                gw = _instances[key] = Gateway(gw_id, url, token, transport=_default_transport)
        out.append(gw)
    return out


def reset():
    """Drop memoised clients and their caches (used by tests)."""
    with _instances_lock:
        for gw in _instances.values():
            gw.close()
        _instances.clear()


class GatewayBoard:
    """Adapter giving a board dict from ``/api/boards`` the attribute surface templates use."""

    def __init__(self, data, gateway, site_name):
        self._data = data
        self.gateway = gateway
        self.site_id = data.get("site_id") or gateway.id
        self.site_name = site_name
        self.slug = data["slug"]
        self.kind = data.get("kind", "")
        self.switch = data.get("switch")
        self.port = data.get("port")
        self.hostname = data.get("hostname", "")
        self.title = data.get("title", self.slug)
        self.blurb = data.get("blurb", "")
        self.description = data.get("description", "")
        self.links = data.get("links") or []
        self.enabled = bool(data.get("enabled", True))
        self.live = bool(data.get("live", self.enabled and self.port is not None))
        # absolute URLs from the gateway, used verbatim -- never derived here
        self.stream_url = data.get("stream_url", "")
        self.serial_ws_url = data.get("serial_ws_url", "")
        self.api_base = data.get("api_base", "")
        self.ssh = data.get("ssh") or {}
        # not part of the gateway contract; templates fall back gracefully
        self.shuttle = data.get("shuttle", "")
        self.pcb = data.get("pcb", "")
        self.pmods = data.get("pmods") or []

    def get_kind_display(self):
        return KIND_DISPLAY.get(self.kind, self.kind)

    @property
    def page_url(self):
        return f"/board/{self.site_id}/{self.slug}/"

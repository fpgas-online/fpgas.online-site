"""The VGA library archive client: `fpga-library.json` and the bitstreams it names.

The archive is one fixed base URL (settings.TTSITE_LIBRARY_BASE_URL). Every request goes through `SESSION`
(no .netrc, no proxy environment), refuses redirects, is streamed, is bounded in bytes and in time, and has its
response closed in `finally`. Every call takes `deadline`, a `now()` value: each read timeout is the time left
(at least 0.5 s), and a watchdog armed at the deadline shuts down the request's socket, so an archive that
trickles bytes (one read can wait for a whole 64 KiB chunk) runs into the deadline instead of holding the request
open.

The schema 1 rules are duplicated here from the spec (tinytapeout-vga-videos `ttvga/fpgalibrary.py` is the
reference checker); nothing is imported from that repository.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import socket
import threading
import time
from typing import Any
from urllib.parse import urlsplit

import requests
import urllib3
from django.conf import settings
from django.core.cache import cache

log = logging.getLogger(__name__)

now = time.monotonic  # the clock deadlines and cache ages are read against; tests replace it

FRESH_SECONDS = 300  # the index copy is used without asking the archive
STALE_SECONDS = 86400  # the last good copy is served while the archive fails
NOT_PUBLISHED_SECONDS = 60  # a 404 for the index is remembered
INDEX_MAX_BYTES = 4 * 1024 * 1024
BIN_MAX_BYTES = 262144
CONNECT_TIMEOUT = 3.05
MIN_READ_TIMEOUT = 0.5
CHUNK = 65536

INDEX_NAME = "fpga-library.json"
INDEX_KEY = "ttsite:library:index"  # (archive_etag, body_bytes, site_etag, fetched_at)
NOT_PUBLISHED_KEY = "ttsite:library:not-published"  # the now() of the archive's 404

MSG_UNREACHABLE = "archive unreachable"
MSG_REDIRECT = "archive redirected; refusing"
MSG_BIN_MISSING = "archive file missing: the archive is still syncing; try again in a few minutes"
MSG_BIN_MISMATCH = "archive file does not match the index"


# --- the deadline watchdog
#
# A blocking read cannot see the clock: iter_content(65536) waits for the whole chunk, and each socket read may take
# up to the read timeout. So every request in `_get` arms a timer at the deadline, before SESSION.get. The session's
# connection pools hand each connection they give out to the request's watchdog; when the timer fires it shuts the
# connection's socket down, which wakes the blocked read. A connection still connecting is shut as soon as its
# connect returns (the connect itself is bounded by its 3.05 s timeout).

_current = threading.local()  # .watchdog: the _Watchdog of the request running on this thread


def _shut(sock) -> None:
    try:
        socket.socket.shutdown(sock, socket.SHUT_RDWR)  # the plain socket's, also under TLS: wakes the reader
    except OSError:
        pass  # already closed


class _Watchdog:
    """Shuts down every socket handed to it once `seconds` have passed.

    It keeps the sockets, not only the connections: http.client drops `conn.sock` when a response will close the
    connection (no Content-Length), and the response goes on reading from that socket."""

    def __init__(self, seconds: float):
        self.fired = False
        self._socks: list = []
        self._lock = threading.Lock()
        self._timer = threading.Timer(seconds, self._fire)
        self._timer.daemon = True

    def start(self) -> None:
        self._timer.start()

    def cancel(self) -> None:
        self._timer.cancel()

    def attach(self, conn) -> None:
        sock = getattr(conn, "sock", None)
        if sock is None:
            return  # not connected yet: connect() attaches it again
        with self._lock:
            self._socks.append(sock)
            fired = self.fired
        if fired:
            _shut(sock)

    def _fire(self) -> None:
        with self._lock:
            self.fired = True
            socks = list(self._socks)
        for sock in socks:
            _shut(sock)


def _attach(conn) -> None:
    watchdog = getattr(_current, "watchdog", None)
    if watchdog is not None:
        watchdog.attach(conn)


class _HTTPConnection(urllib3.connection.HTTPConnection):
    def connect(self):
        super().connect()
        _attach(self)  # shut at once if the deadline passed while connecting


class _HTTPSConnection(urllib3.connection.HTTPSConnection):
    def connect(self):
        super().connect()
        _attach(self)


class _HTTPPool(urllib3.HTTPConnectionPool):
    ConnectionCls = _HTTPConnection

    def _get_conn(self, timeout=None):
        conn = super()._get_conn(timeout)
        _attach(conn)
        return conn


class _HTTPSPool(urllib3.HTTPSConnectionPool):
    ConnectionCls = _HTTPSConnection

    def _get_conn(self, timeout=None):
        conn = super()._get_conn(timeout)
        _attach(conn)
        return conn


class _WatchedAdapter(requests.adapters.HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = {"http": _HTTPPool, "https": _HTTPSPool}


SESSION = requests.Session()
SESSION.trust_env = False
SESSION.mount("https://", _WatchedAdapter())
SESSION.mount("http://", _WatchedAdapter())  # the base is always https; http only serves the local socket tests


class ArchiveError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotPublished(ArchiveError):
    """The archive has no fpga-library.json (404)."""


class LibraryDisabled(Exception):
    """TTSITE_LIBRARY_BASE_URL is empty: the library is switched off (the routes answer 404, no panel)."""


class _TooLarge(ArchiveError):
    """The archive's answer is over the byte cap."""


# --- schema 1 (spec section 1)

PREAMBLE = bytes.fromhex("7eaa997e")
PREAMBLE_WINDOW = 68
MAX_DESIGNS = 2000
DEMO_NAMES = frozenset({"tt_um_counter_7seg", "tt_um_factory_test", "tt_um_pwm_breathe", "tt_um_uart_hello",
                        "tt_um_vga_pattern"})  # fmt: skip
IDLE_NAME = "idle_display"

SHUTTLE_RE = re.compile(r"[a-z0-9]{1,16}\Z")
MACRO_RE = re.compile(r"tt_um_[A-Za-z0-9_]{1,58}\Z")
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
GENERATED_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
VERDICT_RE = re.compile(r"[a-z-]{1,32}\Z")
PATH_RE = re.compile(r"[a-z0-9]{1,16}/tt_um_[A-Za-z0-9_]{1,58}/[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}\Z")
NAME_RE = re.compile(r"[a-z0-9_]{1,40}\Z")

ENTRY_KEYS = (
    "id", "shuttle", "macro", "title", "author", "description", "docs_url", "repo_url", "commit",
    "clock_hz", "pinout", "ui_in_mask", "ui_in", "bin", "bin_bytes", "bin_sha256", "build_status",
    "fmax_hz", "validation", "verdict", "qspi", "poster", "preview_gif", "video_10s_webm",
)  # fmt: skip
MEDIA_KEYS = ("poster", "preview_gif", "video_10s_webm")


def library_id(shuttle: str, macro: str) -> str:
    """The design's name on the Pi (Ruling AC). `macro` starts with tt_um_ (checked by the caller)."""
    name = re.sub(r"[^a-z0-9_]", "_", f"{shuttle}_{macro[len('tt_um_') :]}".lower())
    if len(name) > 40:
        name = f"{name[:31]}_{hashlib.sha256(f'{shuttle}/{macro}'.encode()).hexdigest()[:8]}"
    return name


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _int_in(v: Any, lo: int, hi: int) -> str | None:
    if not _is_int(v):
        return "must be an int"
    return None if lo <= v <= hi else f"must be {lo} to {hi}"


def _matches(v: Any, regex: re.Pattern[str]) -> str | None:
    if not isinstance(v, str):
        return "must be a string"
    return None if regex.match(v) else f"must match {regex.pattern}"


def _length(v: Any, lo: int, hi: int) -> str | None:
    if not isinstance(v, str):
        return "must be a string"
    return None if lo <= len(v) <= hi else f"must be {lo} to {hi} characters"


def _choice(v: Any, *options: str) -> str | None:
    return None if isinstance(v, str) and v in options else "must be one of " + ", ".join(options)


def _url(v: Any) -> str | None:
    if not isinstance(v, str):
        return "must be a string"
    if v and not (v.startswith("https://") and len(v) <= 500):
        return "must be empty, or https:// and at most 500 characters"
    return None


def _archive_path(v: Any, shuttle: str, macro: str) -> str | None:
    if not isinstance(v, str) or not PATH_RE.match(v) or ".." in v:
        return "must be a relative archive path <shuttle>/<macro>/<file> without .."
    if v.split("/")[:2] != [shuttle, macro]:
        return "must start with the entry's <shuttle>/<macro>/"
    return None


def _pinout(v: Any) -> str | None:
    if not isinstance(v, dict):
        return "must be an object"
    for key in ("ui_in", "uo_out", "uio"):
        labels = v.get(key)
        if not isinstance(labels, list) or len(labels) != 8:
            return f"{key} must be an array of exactly 8 strings"
        if not all(isinstance(s, str) and len(s) <= 64 for s in labels):
            return f"{key} must hold only strings of at most 64 characters"
    return None


SIMPLE_RULES = {
    "shuttle": lambda v: _matches(v, SHUTTLE_RE),
    "macro": lambda v: _matches(v, MACRO_RE),
    "title": lambda v: _length(v, 1, 200),
    "author": lambda v: _length(v, 0, 200),
    "description": lambda v: _length(v, 0, 2000),
    "docs_url": _url,
    "repo_url": _url,
    "commit": lambda v: _matches(v, COMMIT_RE),
    "clock_hz": lambda v: _int_in(v, 1, 200000000),
    "pinout": _pinout,
    "ui_in_mask": lambda v: _int_in(v, 0, 255),
    "bin_bytes": lambda v: _int_in(v, 1, BIN_MAX_BYTES),
    "bin_sha256": lambda v: _matches(v, SHA_RE),
    "build_status": lambda v: _choice(v, "ok", "timing-fail"),
    "fmax_hz": lambda v: _int_in(v, 1, 1000000000),
    "validation": lambda v: _choice(v, "none", "mcu", "full-speed"),
    "verdict": lambda v: _matches(v, VERDICT_RE),
    "qspi": lambda v: _choice(v, "none", "emulated"),
}


def _entry_violation(e: Any) -> tuple[str, str] | None:
    """The first (field, rule) this entry breaks, or None. Fields that others depend on are checked first."""
    if not isinstance(e, dict):
        return "entry", "must be an object"
    for key in ENTRY_KEYS:
        if key not in e:
            return key, "missing"
    for key, rule in SIMPLE_RULES.items():
        if (why := rule(e[key])) is not None:
            return key, why
    shuttle, macro, mask, ui_in = e["shuttle"], e["macro"], e["ui_in_mask"], e["ui_in"]
    want = library_id(shuttle, macro)
    if e["id"] != want:
        return "id", f"must equal library_id(shuttle, macro) = {want!r}"
    if e["id"] in DEMO_NAMES or e["id"] == IDLE_NAME:
        return "id", "is a reserved name"
    if ui_in is None:
        if mask:
            return "ui_in", "must not be null when ui_in_mask is not 0"
    elif (why := _int_in(ui_in, 0, 255)) is not None:
        return "ui_in", why
    elif mask == 0:
        return "ui_in", "must be null when ui_in_mask is 0"
    elif ui_in & ~mask:
        return "ui_in", "has bits outside ui_in_mask"
    if e["bin"] != f"{shuttle}/{macro}/{want}-{e['bin_sha256'][:12]}.bin":
        return "bin", "must equal <shuttle>/<macro>/<id>-<bin_sha256[:12]>.bin"
    for key in MEDIA_KEYS:
        if e[key] is not None and (why := _archive_path(e[key], shuttle, macro)) is not None:
            return key, why
    return None


def _top_level_violation(doc: Any) -> str | None:
    if not isinstance(doc, dict):
        return "document: must be a JSON object"
    for key, why in (
        ("schema", None if _is_int(doc.get("schema")) and doc.get("schema") == 1 else "must be the int 1"),
        ("generated", _matches(doc.get("generated"), GENERATED_RE)),
        ("tthfv_commit", _matches(doc.get("tthfv_commit"), COMMIT_RE)),
        ("videos_repo_commit", _matches(doc.get("videos_repo_commit"), COMMIT_RE)),
    ):
        if key not in doc:
            return f"{key}: missing"
        if why is not None:
            return f"{key}: {why}"
    designs = doc.get("designs")
    if not isinstance(designs, list):
        return "designs: must be an array"
    if len(designs) > MAX_DESIGNS:
        return f"designs: must hold 0 to {MAX_DESIGNS} entries"
    return None


def normalise(doc: object, base_url: str) -> tuple[dict, list[str]]:
    """Apply schema 1 to `doc`: the site's wire format and the dropped entries' reasons.

    A wrong top level raises ArchiveError. A bad entry is dropped and logged; every entry that shares an id is
    dropped; a shuttle that comes back after another one's entries loses its later entries (judged over every
    entry with a string shuttle, valid or not, as the reference checker does). The order is kept.
    """
    if (why := _top_level_violation(doc)) is not None:
        raise ArchiveError(f"archive index is not schema 1: {why}")
    dropped: list[tuple[int, str]] = []
    valid: list[tuple[int, dict]] = []
    # contiguity is judged over every entry with a string shuttle, as the reference checker does
    seen_shuttles: set[str] = set()
    previous: object = None
    for i, e in enumerate(doc["designs"]):
        bad = _entry_violation(e)
        shuttle = e.get("shuttle") if isinstance(e, dict) else None
        if bad is None and shuttle != previous and shuttle in seen_shuttles:
            bad = ("shuttle", f"{shuttle!r} entries must form one unbroken run")
        if isinstance(shuttle, str):
            seen_shuttles.add(shuttle)
            previous = shuttle
        if bad is not None:
            dropped.append((i, f"{bad[0]}: {bad[1]}"))
        else:
            valid.append((i, e))
    counts: dict[str, int] = {}
    for _, e in valid:
        counts[e["id"]] = counts.get(e["id"], 0) + 1
    kept: list[dict] = []
    for i, e in valid:
        if counts[e["id"]] > 1:
            dropped.append((i, f"id: duplicate id {e['id']!r}"))
            continue
        kept.append({key: e[key] for key in ENTRY_KEYS})
    reasons = [f"designs[{i}] {why}" for i, why in sorted(dropped)]
    for reason in reasons:
        log.warning("VGA library: dropped %s", reason)
    return {"schema": 1, "generated": doc["generated"], "base_url": base_url, "designs": kept}, reasons


def _refuse_constant(name: str) -> Any:
    raise ValueError(f"{name} is not allowed in {INDEX_NAME}")


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"{text} is not a finite number")
    return value


def _parse(body: bytes) -> object:
    try:
        return json.loads(body.decode("utf-8"), parse_constant=_refuse_constant, parse_float=_finite_float)
    except (ValueError, RecursionError) as exc:  # UnicodeDecodeError and JSONDecodeError are ValueErrors
        raise ArchiveError(f"archive index is not valid JSON: {exc}") from None


# --- HTTP


def base_url() -> str:
    """The archive base. LibraryDisabled when the setting is empty; ArchiveError unless it is https:// and ends
    in "/"."""
    base = settings.TTSITE_LIBRARY_BASE_URL
    if not base:
        raise LibraryDisabled("TTSITE_LIBRARY_BASE_URL is empty")
    if not (isinstance(base, str) and base.startswith("https://") and base.endswith("/")):
        raise ArchiveError("TTSITE_LIBRARY_BASE_URL must be https:// and end in /")
    return base


def _url_for(path: str) -> str:
    """`path`, a checked relative archive path, joined onto the base, staying on the base's scheme and host."""
    base = base_url()
    if path != INDEX_NAME and (not isinstance(path, str) or not PATH_RE.match(path) or ".." in path):
        raise ArchiveError(f"refusing archive path {path!r}")
    url = base + path
    if not _same_origin(url, base):
        raise ArchiveError(f"refusing archive path {path!r}")
    return url


def _same_origin(url: str, base: str) -> bool:
    a, b = urlsplit(url), urlsplit(base)
    return (a.scheme, a.netloc) == (b.scheme, b.netloc) and a.path.startswith(b.path)


def _get(url: str, deadline: float, max_bytes: int, headers: dict | None = None) -> tuple[int, dict, bytes]:
    """(status, headers, body) of a GET. The body is read only for a 200, and at most `max_bytes` + 1 of it.

    Raises ArchiveError("archive unreachable") on a transport error or when the deadline passes, and _TooLarge
    when the answer is over `max_bytes`."""
    left = deadline - now()
    if left <= 0:
        raise ArchiveError(MSG_UNREACHABLE)
    resp = None
    watchdog = _Watchdog(left)
    _current.watchdog = watchdog
    watchdog.start()  # before SESSION.get: it covers the connect and the headers too
    try:
        resp = SESSION.get(url, headers=headers or {}, stream=True, allow_redirects=False,
                           timeout=(CONNECT_TIMEOUT, max(MIN_READ_TIMEOUT, left)))  # fmt: skip
        if watchdog.fired:
            raise ArchiveError(MSG_UNREACHABLE)
        if 300 <= resp.status_code < 400 and resp.status_code != 304:
            raise ArchiveError(MSG_REDIRECT)
        if not _same_origin(getattr(resp, "url", None) or url, base_url()):
            raise ArchiveError(MSG_REDIRECT)
        if resp.status_code != 200:
            return resp.status_code, dict(resp.headers), b""
        declared = resp.headers.get("Content-Length")
        if declared is not None and declared.strip().isdigit() and int(declared) > max_bytes:
            raise _TooLarge(f"archive answer is {int(declared)} bytes, over {max_bytes}")
        body = bytearray()
        for chunk in resp.iter_content(CHUNK):
            body.extend(chunk)
            if len(body) > max_bytes:
                break
            if now() >= deadline:
                raise ArchiveError(MSG_UNREACHABLE)
        if watchdog.fired:  # a shut socket can end a body with no Content-Length early, without an error
            raise ArchiveError(MSG_UNREACHABLE)
        if len(body) > max_bytes:
            raise _TooLarge(f"archive answer is over {max_bytes} bytes")
        return 200, dict(resp.headers), bytes(body)
    except (requests.RequestException, urllib3.exceptions.HTTPError, OSError):
        raise ArchiveError(MSG_UNREACHABLE) from None
    finally:
        watchdog.cancel()
        _current.watchdog = None
        if resp is not None:
            resp.close()


# --- the index


def _site_etag(body: bytes) -> str:
    return f'W/"sha256-{hashlib.sha256(body).hexdigest()[:32]}"'


def _fetch_index(deadline: float, copy: tuple | None) -> tuple:
    """Ask the archive; the new cache entry. Raises NotPublished on a 404 and ArchiveError on any other failure."""
    headers = {"If-None-Match": copy[0]} if copy and copy[0] else {}
    status, resp_headers, body = _get(_url_for(INDEX_NAME), deadline, INDEX_MAX_BYTES, headers)
    t = now()
    if status == 304 and copy:
        return (copy[0], copy[1], copy[2], t)
    if status == 404:
        cache.set(NOT_PUBLISHED_KEY, t, timeout=None)
        cache.delete(INDEX_KEY)
        raise NotPublished("VGA library not published")
    if status != 200:
        raise ArchiveError(f"archive answered {status}")
    index, _dropped = normalise(_parse(body), base_url())
    out = json.dumps(index, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    return (resp_headers.get("ETag") or resp_headers.get("etag") or "", out, _site_etag(out), t)


def index(deadline: float) -> tuple[bytes, str, int | None]:
    """The normalised index: (body, weak ETag, stale age in seconds or None when fresh).

    Raises NotPublished (remembered for 60 s) when the archive has no index, and ArchiveError when the archive
    fails and no copy younger than 24 h is kept.
    """
    t = now()
    gone = cache.get(NOT_PUBLISHED_KEY)
    if gone is not None and t - gone < NOT_PUBLISHED_SECONDS:
        raise NotPublished("VGA library not published")
    copy = cache.get(INDEX_KEY)
    if copy and t - copy[3] < FRESH_SECONDS:
        return copy[1], copy[2], None
    try:
        fresh = _fetch_index(deadline, copy)
    except NotPublished:
        raise
    except ArchiveError as exc:
        t = now()
        if copy and t - copy[3] < STALE_SECONDS:
            log.warning("VGA library: archive failed (%s); serving the copy from %d s ago", exc.message, t - copy[3])
            return copy[1], copy[2], int(t - copy[3])
        raise
    cache.set(INDEX_KEY, fresh, timeout=None)
    cache.delete(NOT_PUBLISHED_KEY)
    return fresh[1], fresh[2], None


_parsed: tuple[str, dict] | None = None  # (site_etag, the parsed index): this worker's last parse


def _designs(body: bytes, site_etag: str) -> dict[str, dict]:
    global _parsed
    if _parsed is None or _parsed[0] != site_etag:
        _parsed = (site_etag, {e["id"]: e for e in json.loads(body)["designs"]})
    return _parsed[1]


def entry(id: str, deadline: float) -> dict | None:
    """The normalised entry named `id`, or None. Raises as `index` does."""
    body, site_etag, _age = index(deadline)
    return _designs(body, site_etag).get(id)


def cached_ids() -> set[str] | None:
    """The ids in this worker's copy of the index, without asking the archive; None when there is no usable copy."""
    copy = cache.get(INDEX_KEY)
    if not copy or now() - copy[3] >= STALE_SECONDS:
        return None
    return set(_designs(copy[1], copy[2]))


# --- the bins


def fetch_bin(entry: dict, deadline: float) -> bytes:
    """The bytes of a normalised entry's bitstream, checked for size, hash and preamble."""
    try:
        status, _headers, data = _get(_url_for(entry["bin"]), deadline, BIN_MAX_BYTES)
    except _TooLarge:
        raise ArchiveError(MSG_BIN_MISMATCH) from None
    if status == 404:
        raise ArchiveError(MSG_BIN_MISSING)
    if status != 200:
        raise ArchiveError(MSG_UNREACHABLE)
    if (
        len(data) != entry["bin_bytes"]
        or hashlib.sha256(data).hexdigest() != entry["bin_sha256"]
        or PREAMBLE not in data[:PREAMBLE_WINDOW]
    ):
        raise ArchiveError(MSG_BIN_MISMATCH)
    return data

"""Tiny HTTP client for the Pi-side fpgas-tt daemon (port 8765). Never raises into views, except `Unsupported`
from `library_state` when the daemon is too old for the VGA library."""

import json
import time
from urllib.parse import quote

import requests

DAEMON_PORT = 8765


def health(board, timeout=3.0):
    if board.ip is None:
        return {"reachable": False, "error": "no Pi reports this board"}
    url = f"http://{board.ip}:{DAEMON_PORT}/health"
    try:
        resp = requests.get(url, timeout=timeout)
    except requests.RequestException as exc:
        return {"reachable": False, "error": str(exc)}
    if resp.status_code != 200:
        return {"reachable": False, "error": f"HTTP {resp.status_code} from {url}"}
    try:
        data = resp.json()
    except ValueError as exc:
        return {"reachable": False, "error": f"invalid JSON from {url}: {exc}"}
    if not isinstance(data, dict):
        return {"reachable": False, "error": "unexpected JSON shape"}
    data["reachable"] = True
    return data


DAEMON_API_TIMEOUT = 30.0
DAEMON_UPLOAD_TIMEOUT = 45.0  # nginx's default proxy_read_timeout is 60 s
DAEMON_CONNECT_TIMEOUT = 3.05  # fast-fail on a dead connect; four times the TCP SYN retransmit interval
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
    return _pass_through(
        lambda: requests.get(f"{_base(board)}/designs", timeout=(DAEMON_CONNECT_TIMEOUT, DAEMON_API_TIMEOUT))
    )


def enable(board, name, body: bytes, *, deadline: float | None = None):
    """POST /designs/<name>/enable, forwarding body verbatim.

    An empty body is sent as ``{}`` -- the daemon requires JSON. With a `deadline` (a `now()` value), the read
    timeout is cut to the time left, and nothing is sent once it has passed.
    """
    timeout = _timeout(DAEMON_API_TIMEOUT, deadline)
    if timeout is None:
        return _OUT_OF_TIME
    return _pass_through(
        lambda: requests.post(
            f"{_base(board)}/designs/{quote(name, safe='')}/enable",
            data=body or b"{}",
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
    )


def upload(board, name, fileobj, filename):
    return _pass_through(
        lambda: requests.post(
            f"{_base(board)}/bitstream",
            data={"name": name},
            files={"file": (filename, fileobj, "application/octet-stream")},
            timeout=(DAEMON_CONNECT_TIMEOUT, DAEMON_UPLOAD_TIMEOUT),
        )
    )


# --- the VGA library (fpgas.online-tt GET /library, POST /library/{id})

now = time.monotonic  # the clock deadlines are read against; tests replace it


class Unsupported(Exception):
    """The daemon has no VGA library routes: its aiohttp answers GET /library with a plain-text 404."""


_OUT_OF_TIME = (504, {"error": "the load ran out of time; try again", "detail": ""})


def _timeout(own: float, deadline: float | None):
    """(connect, read) for a call bound by `deadline`: read is min(own, time left). None once the time is up."""
    if deadline is None:
        return (DAEMON_CONNECT_TIMEOUT, own)
    left = deadline - now()
    if left <= 0:
        return None
    return (DAEMON_CONNECT_TIMEOUT, min(own, left))


def library_state(board, *, deadline: float):
    """GET /library: (status, body). Raises Unsupported when the daemon is too old to have the route."""
    timeout = _timeout(DAEMON_API_TIMEOUT, deadline)
    if timeout is None:
        return _OUT_OF_TIME
    try:
        resp = requests.get(f"{_base(board)}/library", timeout=timeout)
    except requests.RequestException as exc:
        return 502, {"error": "Pi unreachable", "detail": str(exc)}
    if resp.status_code == 404:
        try:
            resp.json()
        except ValueError:
            raise Unsupported(f"{_base(board)}/library answered a 404 that is not JSON") from None
    return _pass_through(lambda: resp)


def library_push(board, name, data: bytes, sha256: str, *, deadline: float):
    """POST /library/<name>: multipart `meta` ({"id", "bin_sha256", "bin_bytes"}) and `file` (the bytes)."""
    timeout = _timeout(DAEMON_UPLOAD_TIMEOUT, deadline)
    if timeout is None:
        return _OUT_OF_TIME
    meta = json.dumps({"id": name, "bin_sha256": sha256, "bin_bytes": len(data)}, allow_nan=False)
    return _pass_through(
        lambda: requests.post(
            f"{_base(board)}/library/{quote(name, safe='')}",
            files={
                "meta": (None, meta.encode(), "application/json"),
                "file": (f"{name}.bin", data, "application/octet-stream"),
            },
            timeout=timeout,
        )
    )

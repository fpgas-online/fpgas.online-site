"""Tiny HTTP client for the Pi-side fpgas-tt daemon (port 8765). Never raises into views."""

import requests

DAEMON_PORT = 8765


def health(board, timeout=3.0):
    if board.port is None:
        return {"reachable": False, "error": "board is not wired to a port"}
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
    return _pass_through(
        lambda: requests.post(
            f"{_base(board)}/designs/{name}/enable",
            data=body or b"{}",
            headers={"Content-Type": "application/json"},
            timeout=DAEMON_API_TIMEOUT,
        )
    )


def upload(board, name, fileobj, filename):
    return _pass_through(
        lambda: requests.post(
            f"{_base(board)}/bitstream",
            data={"name": name},
            files={"file": (filename, fileobj, "application/octet-stream")},
            timeout=DAEMON_UPLOAD_TIMEOUT,
        )
    )

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

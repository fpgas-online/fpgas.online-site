"""Topic routing for the fleet MQTT consumer.

`dispatch` is pure routing over (topic, payload bytes) so it is testable
without a broker; the `fleet_consumer` management command feeds paho
messages into it. Foreign topics (e.g. sensors2mqtt shares the broker) and
malformed payloads are ignored, never raised.
"""

import json
import logging
import re

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings

from . import services

log = logging.getLogger(__name__)

KINDS = ("registration", "status", "event")

# A Pi's short hostname, in its one spelling (as pibfpgas.pis reads it):
# pi-sw<s>-p<p>, or pi<p> at a flat site.
_HOSTNAME_RE = re.compile(r"pi(?:-sw[1-9][0-9]*-p)?[1-9][0-9]*")


def _widget_group(hostname):
    """Channel group the board page for this Pi listens on: pistat_<its
    short hostname> (dcws.js; the Pi's own pistat curls send there too), or
    None for a hostname that names no Pi."""
    short = (hostname or "").split(".")[0]
    return f"pistat_{short}" if _HOSTNAME_RE.fullmatch(short) else None


def _bridge(hostname, stage):
    """Mirror a stage into the board page's status log widget (resolved
    D-5). Must never break ingest: any failure is logged and swallowed."""
    try:
        group = _widget_group(hostname)
        layer = get_channel_layer()
        if group is None or layer is None:
            return
        message = {"type": "stat.message", "status": stage,
                   "message": f"piview: {stage}"}
        async_to_sync(layer.group_send)(group, message)
    except Exception:
        log.exception("widget bridge failed for %s (%s)", hostname, stage)


def port_prefix_enabled():
    """Whether `port/<port>/` topics are believed: the broker only stamps
    them once its per-port listeners are in place, and until then any Pi can
    publish one itself, so with this off (the default) such a topic is as
    foreign as any other and is ignored."""
    return bool(settings.FLEET_MQTT.get("port_prefix", False))


def _split(topic):
    """(port, serial, kind) of a fleet topic, or None for a foreign one.

    The broker's per-port listeners stamp a Pi's topics with
    port/<port>/ (mosquitto mount_point), so `port/pi-sw2-p9/fpgas/<site>/pi/
    <serial>/<kind>` is whatever the Pi on that port sent as
    `fpgas/<site>/pi/<serial>/<kind>`; port is then the stamped port and the
    Pi had no say in it. An unstamped topic gives port None. A stamp that
    is no port's hostname gives (False, ...): refused, not ignored. Stamps
    are only read when port_prefix_enabled()."""
    parts = topic.split("/")
    port = None
    if parts[0] == "port" and len(parts) > 1 and port_prefix_enabled():
        port, parts = parts[1], parts[2:]
    if len(parts) != 5 or parts[0] != "fpgas" or parts[2] != "pi":
        return None
    if port is not None and not _is_port(port):
        port = False
    return port, parts[3], parts[4]


def _is_port(name):
    return _HOSTNAME_RE.fullmatch(name) is not None


def dispatch(topic, payload):
    """Route one message. Returns the handler that ran, "rejected" for a
    registration or topic stamp that is refused (logged; a refused status or
    event is logged by fleet.services and returns as it always did), or
    "ignored"."""
    split = _split(topic)
    if split is None:
        return "ignored"
    port, serial, kind = split
    if port is False:
        log.warning("%r rejected: the port prefix is no port's hostname", topic)
        return "rejected"
    if kind not in KINDS:
        return "ignored"
    try:
        doc = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        log.warning("malformed JSON on %r ignored", topic)
        return "ignored"
    if not isinstance(doc, dict):
        log.warning("non-object payload on %r ignored", topic)
        return "ignored"
    if kind == "registration":
        if doc.get("machine", {}).get("serial") != serial:
            log.warning("registration serial mismatch on %r ignored", topic)
            return "ignored"
        machine, _ = services.register_document(doc, port)
        return "registration" if machine is not None else "rejected"
    if kind == "status":
        machine = services.status(serial, doc, port)
        if machine is not None:
            stage = "online" if machine.online \
                else f"offline ({doc.get('reason', 'unknown')})"
            _bridge(machine.hostname, stage)
        return "status"
    event = services.boot_event(serial, doc, port)
    if event is not None:
        _bridge(event.machine.hostname, event.stage)
    return "event"

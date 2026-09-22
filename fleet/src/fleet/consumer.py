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

from . import services

log = logging.getLogger(__name__)

KINDS = ("registration", "status", "event")

_HOSTNAME_RE = re.compile(r"^pi(?:-sw\d+-p)?(\d+)$")


def _widget_group(hostname):
    """Channel group the board page for this Pi listens on (dcws.js
    subscribes to pistat_pi<port>), or None for unparseable hostnames."""
    m = _HOSTNAME_RE.match(hostname or "")
    return f"pistat_pi{int(m.group(1))}" if m else None


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


def dispatch(topic, payload):
    """Route one message. Returns the handler that ran, or "ignored"."""
    parts = topic.split("/")
    if len(parts) != 5 or parts[0] != "fpgas" or parts[2] != "pi":
        return "ignored"
    _, _site, _, serial, kind = parts
    if kind not in KINDS:
        return "ignored"
    try:
        doc = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        log.warning("malformed JSON on %s ignored", topic)
        return "ignored"
    if not isinstance(doc, dict):
        log.warning("non-object payload on %s ignored", topic)
        return "ignored"
    if kind == "registration":
        if doc.get("machine", {}).get("serial") != serial:
            log.warning("registration serial mismatch on %s ignored", topic)
            return "ignored"
        services.register_document(doc)
        return "registration"
    if kind == "status":
        machine = services.status(serial, doc)
        if machine is not None:
            stage = "online" if machine.online \
                else f"offline ({doc.get('reason', 'unknown')})"
            _bridge(machine.hostname, stage)
        return "status"
    event = services.boot_event(serial, doc)
    if event is not None:
        _bridge(event.machine.hostname, event.stage)
    return "event"

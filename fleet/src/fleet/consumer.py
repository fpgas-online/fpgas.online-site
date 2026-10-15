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
from pistat.consumers import SWITCH_EVENTS_GROUP

from . import services

log = logging.getLogger(__name__)

KINDS = ("registration", "status", "event")
# The gateway's own records (the switch poller's, dnsmasq's script's): fpgas/<site>/gw/<kind>
GATEWAY_KINDS = ("port", "lease")

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


def _send(group, message):
    """Send to a channel group; never breaks ingest: a failure is logged and swallowed."""
    try:
        layer = get_channel_layer()
        if layer is not None:
            async_to_sync(layer.group_send)(group, message)
    except Exception:
        log.exception("channel send to %s failed", group)


def _gateway_text(record):
    """The line a gateway record adds to the board page's status log."""
    doc = record.doc
    if record.kind == "port":
        return f"switch: link {doc['link']}"
    text = f"dhcp: {doc['event']} {doc['address']} {doc.get('mac') or ''}".rstrip()
    return f"{text} ({doc['cause']})" if doc.get("cause") else text


def _bridge_gateway(record):
    """Mirror a stored gateway record into the board page's status log, and a port record into the switch
    dashboard's group."""
    group = _widget_group(record.hostname)
    if group is not None:
        text = _gateway_text(record)
        _send(group, {"type": "stat.message", "status": record.kind, "message": text})
    if record.kind == "port":
        _send(SWITCH_EVENTS_GROUP, {"type": "switch.event", "record": record.doc})


def _gateway(topic, kind, site, payload):
    try:
        doc = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        log.warning("malformed JSON on %s ignored", topic)
        return "ignored"
    if not isinstance(doc, dict):
        log.warning("non-object payload on %s ignored", topic)
        return "ignored"
    try:
        record = services.gateway_record(site, kind, doc)
    except ValueError as exc:
        log.warning("invalid gateway record on %s ignored: %s", topic, exc)
        return "ignored"
    _bridge_gateway(record)
    return f"gw/{kind}"


def dispatch(topic, payload, retained=False):
    """Route one message. Returns the handler that ran, or "ignored".

    `retained`: MQTT's retain flag as delivered, set only on a stored copy the
    broker hands over at subscribe (MQTT 3.1.1 section 3.3.1.3, the protocol the
    consumer's client speaks), never on a message forwarded live; such a copy updates state but is no sign the Pi is
    alive now (services.status, register_document; site #119)."""
    parts = topic.split("/")
    if len(parts) == 4 and parts[0] == "fpgas" and parts[2] == "gw" and parts[3] in GATEWAY_KINDS:
        return _gateway(topic, parts[3], parts[1], payload)
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
        services.register_document(doc, retained=retained)
        return "registration"
    if kind == "status":
        machine = services.status(serial, doc, retained=retained)
        if machine is not None and not retained:  # a replayed old status is no news for the board page
            stage = "online" if machine.online \
                else f"offline ({doc.get('reason', 'unknown')})"
            _bridge(machine.hostname, stage)
        return "status"
    event = services.boot_event(serial, doc)
    if event is not None:
        _bridge(event.machine.hostname, event.stage)
    return "event"

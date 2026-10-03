# pici pistat/views.py

import json
import subprocess

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from pibfpgas.pis import Pi


def humanize(m):
    # given a key, add a bunch of text to make humans happy

    # d = {"type": message_type, "message": message_text}

    xtra=None

    if 'kernel' in m:
        xtra = "Linux kernel booting, (this can take up to 2 minutes - see https://github.com/CarlFK/pici/issues/33)"
    else:
        xtra = {
            'cam': 'cam sending video feed to server (60 second latency.)',
            'ssh': 'ssh server started.',
            'ArtyHere': 'Arty board detected.',
            'NoArty': 'Arty board not detected. probably because https://github.com/CarlFK/pici/issues/39',
            'ArtyWire': 'Arty pmod wire test passed.',
            'ArtyNoWire': 'Arty pmod wire test failed.',
            }.get(m)

    if xtra is not None:
        m = f"{m} {xtra}"

    return m


@csrf_exempt
def status(request, pi_name, status):

    group = f"pistat_{pi_name}"
    message_type = "stat.message"
    message = humanize(status)
    message_text = f"piview: {message}"
    d = {
            "type": message_type,
            "status": status,
            "message": message_text,
            }

    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)( group, d )

    d['group'] = group

    response = HttpResponse(content_type="application/json")
    json.dump(d, response, indent=2)

    return response

@csrf_exempt
def ping(request, pi_name):

    # pi_name is "pi{port}" (the status log's group); the board page posts
    # {"port", "switch"} as it does to /snmp/, with no switch on a
    # legacy flat site. The address derives from them: 10.21.<switch>.<port>
    # (VLAN-per-port) or 10.21.0.<100+port> (flat).
    port = int(pi_name[2:])
    try:
        switch = json.loads(request.body or b"{}").get("switch")
    except (ValueError, AttributeError):
        return HttpResponseBadRequest("the body is not a JSON object")
    if switch is not None and (not isinstance(switch, int) or isinstance(switch, bool)):
        return HttpResponseBadRequest("switch is not a number")
    pi_ip = Pi(port=port, switch=switch).ip

    cmd = ["ping",
            "-c", "3",
            "-w", "5",
            pi_ip]

    proc = subprocess.Popen(cmd,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,stderr=subprocess.PIPE)

    stdouts=[]
    while (line := proc.stdout.readline()) or (proc.poll() is None):
        line = line.decode().strip()
        status(request, pi_name, line)
        stdouts.append(line)

    d = {"stdouts": stdouts}

    response = HttpResponse(content_type="application/json")
    json.dump(d, response, indent=2)

    return response


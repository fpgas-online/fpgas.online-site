"""Long-running MQTT consumer: subscribes fpgas/+/pi/+/+ and feeds every
message through fleet.consumer.dispatch. Run under systemd (Restart=always);
paho's loop_forever handles broker reconnects itself."""

import logging

import paho.mqtt.client as mqtt
from django.conf import settings
from django.core.management.base import BaseCommand

from fleet import consumer

log = logging.getLogger(__name__)

TOPIC = "fpgas/+/pi/+/+"


class Command(BaseCommand):
    help = "Consume fleet registration/status/event messages from MQTT"

    def handle(self, *args, **options):
        cfg = settings.FLEET_MQTT
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

        def on_connect(client, userdata, flags, reason_code, properties):
            log.info("connected to %(host)s:%(port)s", cfg)
            client.subscribe(TOPIC, qos=1)

        def on_message(client, userdata, msg):
            try:
                handled = consumer.dispatch(msg.topic, msg.payload)
                log.debug("%s -> %s", msg.topic, handled)
            except Exception:
                log.exception("dispatch failed for %s", msg.topic)

        client.on_connect = on_connect
        client.on_message = on_message
        client.connect(cfg["host"], cfg["port"])
        client.loop_forever(retry_first_connection=True)

import json
import logging
import threading
import asyncio
from datetime import datetime
import paho.mqtt.client as mqtt

logger = logging.getLogger("MQTTService")

# Topics
TOPIC_CHAT     = "atak/chat/+"       # atak/chat/{room}
TOPIC_LOCATION = "atak/location/+"   # atak/location/{room}
TOPIC_MARKER   = "atak/marker/+"     # atak/marker/{room}
TOPIC_TARGETS  = "aaron_nev/atak_targets"  # legacy external targets


class MQTTService:
    """
    Local Mosquitto MQTT bridge for ATAK_MI_Server.
    - Subscribes to all ATAK topics and forwards to WebSocket clients.
    - Provides publish() for the FastAPI server to push messages to MQTT.
    """

    def __init__(self, broker="localhost", port=1883, db_pool=None, ws_manager=None):
        self.broker = broker
        self.port = port
        self.db_pool = db_pool
        self.ws_manager = ws_manager
        self._loop: asyncio.AbstractEventLoop = None

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id="atak_mi_server")
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect

    # ── Paho callbacks ──────────────────────────────────────────────────────

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            logger.info(f"MQTT connected to {self.broker}:{self.port}")
            client.subscribe(TOPIC_CHAT)
            client.subscribe(TOPIC_LOCATION)
            client.subscribe(TOPIC_MARKER)
            client.subscribe(TOPIC_TARGETS)
        else:
            logger.warning(f"MQTT connect failed rc={rc}")

    def _on_disconnect(self, client, userdata, rc, properties=None, reasonCode=None):
        logger.warning(f"MQTT disconnected rc={rc}, will auto-reconnect")

    def _on_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8", errors="ignore")
            data = json.loads(payload)
            topic = msg.topic

            if self._loop and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    self._dispatch(topic, data), self._loop
                )
        except Exception as e:
            logger.error(f"MQTT message error: {e}")

    # ── Dispatch incoming MQTT → WebSocket ─────────────────────────────────

    async def _dispatch(self, topic: str, data: dict):
        parts = topic.split("/")

        # Skip messages published by this server — WebSocket broadcast already handled delivery.
        # Only relay messages originating from external MQTT publishers (e.g. Trident).
        if data.get("_srv"):
            return

        # atak/chat/{room} — relay external MQTT chat message to WS clients
        if len(parts) == 3 and parts[0] == "atak" and parts[1] == "chat":
            room = parts[2]
            if self.ws_manager:
                await self.ws_manager.broadcast(room, {
                    "type": "message",
                    "user": data.get("user", "MQTT"),
                    "room": room,
                    "text": data.get("text", ""),
                    "ts": data.get("ts", datetime.now().timestamp() * 1000),
                    "source": "mqtt"
                })

        # atak/location/{room}
        elif len(parts) == 3 and parts[0] == "atak" and parts[1] == "location":
            room = parts[2]
            if self.ws_manager:
                await self.ws_manager.broadcast(room, {
                    "type": "location",
                    "user": data.get("user", "MQTT"),
                    "room": room,
                    "lat": data.get("lat"),
                    "lon": data.get("lon"),
                    "ts": data.get("ts", datetime.now().timestamp() * 1000),
                    "source": "mqtt"
                })

        # atak/marker/{room}
        elif len(parts) == 3 and parts[0] == "atak" and parts[1] == "marker":
            room = parts[2]
            if self.ws_manager and self.db_pool:
                await self._ingest_marker(data, room)

        # Legacy external targets (aaron_nev/atak_targets)
        elif topic == TOPIC_TARGETS:
            targets = data.get("targets", [])
            for t in targets:
                await self._ingest_target(t)

    async def _ingest_marker(self, data: dict, room: str):
        try:
            uid = data.get("id") or f"mqtt-{datetime.now().timestamp()}"
            lat = float(data.get("lat", 0))
            lon = float(data.get("lon", 0))
            await self.db_pool.execute(
                """INSERT INTO markers
                   (marker_uid, device_id, username, room, marker_type, color, latitude, longitude, label)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                   ON CONFLICT (marker_uid) DO UPDATE
                   SET latitude=EXCLUDED.latitude, longitude=EXCLUDED.longitude""",
                uid, "MQTT", data.get("user", "MQTT"), room,
                data.get("markerType", "circle"), data.get("color", "#FACC15"), lat, lon,
                data.get("label", "")
            )
        except Exception as e:
            logger.error(f"Marker ingest error: {e}")
        if self.ws_manager:
            await self.ws_manager.broadcast(room, {**data, "type": "marker", "source": "mqtt"})

    async def _ingest_target(self, t: dict):
        try:
            lat = float(t.get("lat", 0))
            lon = float(t.get("lon", 0))
            uid = t.get("id") or f"target-{datetime.now().timestamp()}"
            if self.db_pool:
                await self.db_pool.execute(
                    """INSERT INTO markers
                       (marker_uid, device_id, username, room, marker_type, color, latitude, longitude, label)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                       ON CONFLICT (marker_uid) DO UPDATE
                       SET latitude=EXCLUDED.latitude, longitude=EXCLUDED.longitude""",
                    uid, "MQTT-EXT", "MQTT_Service", "lobby",
                    "triangle", "#FF0000", lat, lon, "Target"
                )
            if self.ws_manager:
                await self.ws_manager.broadcast("lobby", {
                    "type": "marker", "user": "MQTT_Service", "room": "lobby",
                    "id": uid, "markerType": "triangle", "color": "#FF0000",
                    "lat": lat, "lon": lon,
                    "ts": datetime.now().timestamp() * 1000
                })
        except Exception as e:
            logger.error(f"Target ingest error: {e}")

    # ── Publish helper ──────────────────────────────────────────────────────

    def publish(self, topic: str, payload: dict):
        """Publish a message to an MQTT topic (non-blocking)."""
        try:
            self.client.publish(topic, json.dumps(payload), qos=1)
        except Exception as e:
            logger.error(f"MQTT publish error: {e}")

    def publish_chat(self, room: str, user: str, text: str):
        self.publish(f"atak/chat/{room}", {
            "user": user, "room": room, "text": text,
            "ts": datetime.now().timestamp() * 1000,
            "_srv": 1  # marks as server-originated — prevents WS echo loop
        })

    def publish_location(self, room: str, user: str, lat: float, lon: float):
        self.publish(f"atak/location/{room}", {
            "user": user, "room": room, "lat": lat, "lon": lon,
            "ts": datetime.now().timestamp() * 1000,
            "_srv": 1
        })

    def publish_marker(self, room: str, data: dict):
        self.publish(f"atak/marker/{room}", {**data, "_srv": 1})

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def start(self, loop: asyncio.AbstractEventLoop = None):
        self._loop = loop or asyncio.get_event_loop()
        logger.info(f"Connecting to MQTT broker {self.broker}:{self.port}")
        try:
            self.client.connect_async(self.broker, self.port, keepalive=60)
            self.client.reconnect_delay_set(min_delay=2, max_delay=30)
            threading.Thread(target=self.client.loop_forever, daemon=True).start()
        except Exception as e:
            logger.warning(f"MQTT startup skipped (broker unreachable): {e}")

    def stop(self):
        try:
            self.client.disconnect()
        except Exception:
            pass


# Backwards-compat alias used in main.py
MQTTSubscriber = MQTTService

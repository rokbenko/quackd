"""Robot Passport on an MQTT broker (spec 5.4), behind `quackd[lan]` like the flock bus.

A robot already on a broker publishes its passport retained under `robot-passport/<key>/`, so a
subscriber that arrives an hour later still gets it at once, and an `online` status whose last
will is `offline`, so the broker itself says when the robot dropped off without a goodbye. That
is VDA 5050's connection pattern, borrowed because it works. paho is imported lazily and every
client is injectable, so the tests run on a fake broker with no socket.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from quackd.lan import LanNotInstalled
from quackd.passport.document import MAX_BYTES
from quackd.passport.errors import PassportError
from quackd.passport.keys import short_id_of
from quackd.passport.talk import MAX_HELLO_BYTES

TOPIC_PREFIX = "robot-passport"
QOS = 1
READ_TIMEOUT_S = 5.0


def topics(key: str) -> tuple[str, str, str]:
    """The passport, status and inbox topics of the robot whose short id, or UUID's 32 hex
    digits for a robot with no key, is `key`."""
    base = f"{TOPIC_PREFIX}/{key}"
    return f"{base}/passport", f"{base}/status", f"{base}/inbox"


def key_for(robot_id: str, short_id: str | None) -> str:
    if short_id:
        return short_id
    return robot_id.removeprefix("urn:uuid:").replace("-", "").lower()


def inbox_url(host: str, port: int, robot_id: str) -> str:
    """The talk channel a passport names for a robot's inbox on a broker (spec 6.2)."""
    return f"mqtt://{host}:{port}/{topics(key_for(robot_id, short_id_of(robot_id)))[2]}"


def inbox_topic(url: str) -> str:
    """The topic an `mqtt://` talk channel names."""
    return urlsplit(url).path.lstrip("/")


def _paho_client(client_id: str) -> Any:
    try:
        import paho.mqtt.client as mqtt
    except ImportError as e:  # pragma: no cover - depends on the environment
        raise LanNotInstalled("Robot Passport over MQTT") from e
    return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)


class PassportPublisher:
    """One robot on a broker: its passport retained for as long as this is open, and with
    `on_inbox` its talk inbox too (spec 6.2), every message to it handed over as it arrives.

    A robot that only visits, saying hello to one that lives on the broker, has no passport to
    retain: `payload` None keeps the inbox and nothing else, and leaves no will behind."""

    def __init__(
        self,
        key: str,
        payload: bytes | None,
        *,
        host: str,
        port: int = 1883,
        client: Any = None,
        on_inbox: Callable[[bytes], None] | None = None,
    ) -> None:
        self.passport_topic, self.status_topic, self.inbox_topic = topics(key)
        self._payload = payload
        self.host, self.port = host, port
        self._on_inbox = on_inbox
        self._client = client or _paho_client(f"quackd-passport-{uuid.uuid4().hex[:8]}")
        self.started = False

    def start(self) -> PassportPublisher:
        if self._on_inbox is not None:
            self._client.on_message = self._message
        if self._payload is not None:
            # the will is part of the CONNECT packet, so it has to be set before connecting
            self._client.will_set(self.status_topic, b"offline", qos=QOS, retain=True)
        self._client.connect(self.host, self.port, 60)
        self._client.loop_start()
        if self._payload is not None:
            self._client.publish(self.passport_topic, self._payload, qos=QOS, retain=True)
            self._client.publish(self.status_topic, b"online", qos=QOS, retain=True)
        if self._on_inbox is not None:
            self._client.subscribe(self.inbox_topic, qos=QOS)
        self.started = True
        return self

    def send(self, topic: str, payload: bytes) -> None:
        """A talk message to another robot's inbox on this broker: QoS 1, never retained."""
        self._client.publish(topic, payload, qos=QOS, retain=False)

    def _message(self, _client: Any, _userdata: Any, message: Any) -> None:
        if message.topic == self.inbox_topic and self._on_inbox is not None:
            self._on_inbox(bytes(message.payload)[: MAX_HELLO_BYTES + 1])

    def close(self) -> None:
        """Leave the passport retained and say `offline` ourselves: a last will fires only
        when a connection drops, and this one is closing on purpose."""
        if not self.started:
            return
        if self._payload is not None:
            self._client.publish(self.status_topic, b"offline", qos=QOS, retain=True)
        self._client.loop_stop()
        self._client.disconnect()
        self.started = False


def read_passport(
    host: str, key: str, *, port: int = 1883, timeout_s: float = READ_TIMEOUT_S, client: Any = None
) -> bytes:
    """The passport retained under `key` on the broker at `host`, or a `PassportError`."""
    passport_topic, _status, _inbox = topics(key)
    got = threading.Event()
    box: list[bytes] = []

    def on_message(_client: Any, _userdata: Any, message: Any) -> None:
        if message.topic == passport_topic and not got.is_set():
            box.append(bytes(message.payload)[: MAX_BYTES + 1])
            got.set()

    mqtt = client or _paho_client(f"quackd-meet-{uuid.uuid4().hex[:8]}")
    mqtt.on_message = on_message
    try:
        mqtt.connect(host, port, 60)
    except OSError as e:
        raise PassportError("5.4", f"the broker at {host}:{port} could not be reached: {e}") from e
    mqtt.loop_start()
    try:
        mqtt.subscribe(passport_topic, qos=QOS)
        if not got.wait(timeout_s):
            raise PassportError("5.4", f"nothing is retained on {passport_topic} at {host}:{port}")
    finally:
        mqtt.loop_stop()
        mqtt.disconnect()
    return box[0]


__all__ = ["PassportPublisher", "inbox_topic", "inbox_url", "key_for", "read_passport", "topics"]

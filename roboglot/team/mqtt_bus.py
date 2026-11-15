"""A team bus over an MQTT broker, behind `roboglot[lan]` (ADR-0021).

Same two-method `Bus` protocol as `InProcessBus`, same hard rule: **nobody ever awaits the
bus**. `publish` is a synchronous local fan-out plus a non-blocking hand-off to paho's
network thread; consumers still `drain()` between sim sleeps. Four invariants:

- **Tap exactly once per message per node.** Local messages are tapped on publish, remote
  ones on receive, so `team.jsonl` on the node that owns the run directory carries every
  message once.
- **Broker echo is dropped.** A broker sends our own publications back; a message whose
  `src` is one of this node's subscribers is discarded (`dropped` counts them).
- **At least once is tolerated.** Control messages go at QoS 1, so a remote message can
  arrive twice. The coordinator's handlers are idempotent (the minimum bid per source, the
  maximum exclusion, an idempotent last heartbeat), which is why a duplicate changes no
  decision; a test pins that.
- **Remote messages land on the event loop.** paho calls back on its own thread; the team
  transcript writer is not thread-safe, so a remote message is marshalled with
  `call_soon_threadsafe` before it is tapped and pushed. Without a running loop (a
  synchronous test) it is delivered inline.

Library-only: both runners take `bus_factory=...`. There is still no `--bus` flag: a
coordinator team across machines also needs a distributed clock, which is out of scope, and a
pilot team needs no shared clock at all but has simply never been run across two machines.
Roboglot does not ship a flag for a thing nobody has done.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from typing import Any, Protocol

from pydantic import TypeAdapter, ValidationError

from roboglot.lan import LanNotInstalled
from roboglot.team.bus import Subscription
from roboglot.team.messages import TeamMessage

_WIRE: TypeAdapter[Any] = TypeAdapter(TeamMessage)
QOS_CTL = 1
QOS_HB = 0


def encode(msg: TeamMessage) -> bytes:
    return msg.model_dump_json().encode("utf-8")


def decode(payload: bytes | str) -> TeamMessage:
    """Raises `pydantic.ValidationError` on anything that is not a team message."""
    msg: TeamMessage = _WIRE.validate_json(payload)
    return msg


class MqttClient(Protocol):
    """The slice of `paho.mqtt.client.Client` the bus uses; the tests hand in a fake."""

    on_message: Any

    def connect(self, host: str, port: int = 1883, keepalive: int = 60) -> Any: ...

    def loop_start(self) -> Any: ...

    def loop_stop(self) -> Any: ...

    def disconnect(self) -> Any: ...

    def subscribe(self, topic: str, qos: int = 0) -> Any: ...

    def publish(
        self, topic: str, payload: bytes | None = None, qos: int = 0, retain: bool = False
    ) -> Any: ...


def make_paho_client(client_id: str) -> Any:
    """paho 2.x with the v2 callback API; the constructor opens no socket."""
    try:
        import paho.mqtt.client as mqtt
    except ImportError as e:  # pragma: no cover - depends on the environment
        raise LanNotInstalled("MqttBus") from e
    return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)


class MqttBus:
    def __init__(
        self,
        team_id: str,
        *,
        host: str = "localhost",
        port: int = 1883,
        tap: Callable[[TeamMessage], None] | None = None,
        client: MqttClient | None = None,
        log: Callable[[str], None] = lambda _m: None,
    ) -> None:
        self.team_id = team_id
        self.topic_ctl = f"roboglot/{team_id}/ctl"
        self.topic_hb = f"roboglot/{team_id}/hb"
        self._host, self._port = host, port
        self._tap = tap
        self._log = log
        self._subs: list[Subscription] = []
        self.local_ids: set[str] = set()
        self.published = 0
        self.received = 0
        self.dropped = 0
        self.rejected = 0
        self.started = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: MqttClient = (
            client
            if client is not None
            else make_paho_client(f"roboglot-{team_id}-{uuid.uuid4().hex[:8]}")
        )
        self._client.on_message = self._on_message

    # ── lifecycle (outside the Bus protocol) ────────────────────────────────────────

    def start(self) -> None:
        """Connect, subscribe, start paho's thread. Called inside the event loop that
        will consume messages, so remote deliveries can be marshalled onto it."""
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        self._client.connect(self._host, self._port, 60)
        self._client.subscribe(self.topic_ctl, qos=QOS_CTL)
        self._client.subscribe(self.topic_hb, qos=QOS_HB)
        self._client.loop_start()
        self.started = True

    def close(self) -> None:
        if self.started:
            self._client.loop_stop()
            self._client.disconnect()
            self.started = False

    # ── the Bus protocol ────────────────────────────────────────────────────────────

    def publish(self, msg: TeamMessage) -> None:
        self.published += 1
        if self._tap is not None:
            self._tap(msg)
        self._fan_out(msg)
        hb = msg.kind == "HB"
        self._client.publish(
            self.topic_hb if hb else self.topic_ctl,
            encode(msg),
            qos=QOS_HB if hb else QOS_CTL,
            retain=False,
        )

    def subscribe(self, subscriber_id: str) -> Subscription:
        sub = Subscription(subscriber_id, self._subs.remove)
        self._subs.append(sub)
        self.local_ids.add(subscriber_id)
        return sub

    # ── receive side ────────────────────────────────────────────────────────────────

    def _fan_out(self, msg: TeamMessage) -> None:
        for sub in self._subs:
            if sub.subscriber_id != msg.src:  # no echo to the sender
                sub._push(msg)

    def _on_message(self, _client: Any, _userdata: Any, message: Any) -> None:
        """paho's network thread: decode, drop our own echo, hand over to the loop."""
        try:
            msg = decode(message.payload)
        except (ValidationError, ValueError) as e:
            self.rejected += 1
            self._log(f"mqtt: dropped an unreadable message on {message.topic}: {e}")
            return
        if msg.src in self.local_ids:
            self.dropped += 1
            return
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._deliver, msg)
        else:
            self._deliver(msg)

    def _deliver(self, msg: TeamMessage) -> None:
        self.received += 1
        if self._tap is not None:
            self._tap(msg)
        self._fan_out(msg)


__all__ = ["QOS_CTL", "QOS_HB", "MqttBus", "MqttClient", "decode", "encode", "make_paho_client"]

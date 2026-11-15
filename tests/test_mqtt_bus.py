"""The MQTT team bus on a fake broker: no sockets, no paho needed.

Two `MqttBus` nodes share a synchronous fake broker that routes like a real one (it echoes
the sender's own publications back). What is proven: every message round-trips, echo is
dropped, the tap fires exactly once per message per node, QoS and retain are what the ADR
says, unreadable payloads are dropped, remote messages land on the event loop, `drain()` loses
nothing to a concurrent producer, and a pilot team runs over it.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import threading
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from roboglot.adapters.factory import RobotSpec
from roboglot.agent.providers.fake import FakeProvider
from roboglot.glotfile.parser import load_glot
from roboglot.lan import LanNotInstalled
from roboglot.team.bus import Subscription
from roboglot.team.messages import TalkMsg
from roboglot.team.mqtt_bus import QOS_CTL, MqttBus, decode, encode
from roboglot.team.pilots import roster_from_specs, run_pilot_team


class FakeMessage:
    def __init__(self, topic: str, payload: bytes) -> None:
        self.topic = topic
        self.payload = payload


class FakeBroker:
    """Routes synchronously to every subscribed client, the sender included (brokers echo).
    `duplicate=True` delivers QoS-1 publications twice, as at-least-once allows."""

    def __init__(self, *, duplicate: bool = False) -> None:
        self.clients: list[FakeClient] = []
        self.duplicate = duplicate
        self.routed = 0

    def route(self, topic: str, payload: bytes, qos: int) -> None:
        for client in list(self.clients):
            if topic in client.subscriptions:
                for _ in range(2 if (self.duplicate and qos == 1) else 1):
                    self.routed += 1
                    client.on_message(client, None, FakeMessage(topic, payload))

    def inject(self, topic: str, payload: bytes) -> None:
        """Something else on the broker published on our topic."""
        self.route(topic, payload, 0)


class FakeClient:
    def __init__(self, broker: FakeBroker) -> None:
        self.broker = broker
        self.subscriptions: dict[str, int] = {}
        self.connected = False
        self.looping = False
        self.on_message: Any = None
        self.published: list[tuple[str, bytes, int, bool]] = []

    def connect(self, host: str, port: int = 1883, keepalive: int = 60) -> None:
        self.connected = True
        self.broker.clients.append(self)

    def subscribe(self, topic: str, qos: int = 0) -> None:
        self.subscriptions[topic] = qos

    def loop_start(self) -> None:
        self.looping = True

    def loop_stop(self) -> None:
        self.looping = False

    def disconnect(self) -> None:
        self.connected = False
        self.broker.clients.remove(self)

    def publish(
        self, topic: str, payload: bytes | None = None, qos: int = 0, retain: bool = False
    ) -> None:
        assert payload is not None
        self.published.append((topic, payload, qos, retain))
        self.broker.route(topic, payload, qos)


def node(broker: FakeBroker, *subscribers: str, tap: list[Any] | None = None) -> MqttBus:
    bus = MqttBus("t1", tap=None if tap is None else tap.append, client=FakeClient(broker))
    for name in subscribers:
        bus.subscribe(name)
    bus.start()
    return bus


def talks(src: str = "duck-0") -> list[TalkMsg]:
    """What one pilot says in a team: to everyone, to one member, and the runner's own notice."""
    base = {"t": 1.0, "task_id": "abc"}
    return [
        TalkMsg(**base, src=src, text="I see the ball by the door"),
        TalkMsg(**base, src=src, to="duck-1", text="you take the left side"),
        TalkMsg(**base, src="team", text="duck-2's loop ended: out of steps"),
    ]


def test_every_message_round_trips_and_echo_is_dropped() -> None:
    broker = FakeBroker()
    tap_a: list[Any] = []
    tap_b: list[Any] = []
    a = node(broker, "team", "duck-0", tap=tap_a)
    b = node(broker, "duck-1", tap=tap_b)
    local = a.subscribe("duck-0-observer")  # a second local subscriber on A
    sent = talks()
    for msg in sent:
        a.publish(msg)
    got = b._subs[0].drain()
    assert got == sent  # same models, same values, in order
    assert {m.kind for m in got} == {"TALK"}
    # A's own subscribers got them by local fan-out, never from the broker echo
    assert local.drain() == sent
    assert a.published == 3 and a.dropped == 3 and a.received == 0
    assert b.received == 3 and b.published == 0 and b.dropped == 0
    # the tap fired exactly once per message per node: on publish at A, on receive at B
    assert tap_a == sent and tap_b == sent
    a.close()
    b.close()
    assert broker.clients == []


def test_control_is_qos1_and_nothing_is_retained() -> None:
    broker = FakeBroker()
    a = node(broker, "duck-0")
    client = a._client
    assert isinstance(client, FakeClient)
    assert client.subscriptions == {"roboglot/t1/ctl": QOS_CTL}
    for msg in talks("duck-0"):
        a.publish(msg)
    by_topic = {topic: (qos, retain) for topic, _p, qos, retain in client.published}
    assert by_topic == {"roboglot/t1/ctl": (1, False)}
    assert [json.loads(p)["kind"] for _t, p, _q, _r in client.published] == ["TALK"] * 3


def test_unreadable_payloads_are_dropped_and_counted() -> None:
    broker = FakeBroker()
    logged: list[str] = []
    b = MqttBus("t1", client=FakeClient(broker), log=logged.append)
    sub = b.subscribe("duck-1")
    b.start()
    broker.inject("roboglot/t1/ctl", b"not json")
    broker.inject(
        "roboglot/t1/ctl", json.dumps({"kind": "TALK", "src": "x"}).encode()
    )  # fields missing
    broker.inject(
        "roboglot/t1/ctl", json.dumps({"kind": "NOPE", "src": "x", "t": 0, "task_id": "a"}).encode()
    )
    assert b.rejected == 3 and b.received == 0 and sub.drain() == []
    assert len(logged) == 3 and "roboglot/t1/ctl" in logged[0]
    with pytest.raises(ValidationError):
        decode(b"{}")
    assert decode(encode(talks()[1])) == talks()[1]


async def test_remote_messages_land_on_the_event_loop_not_the_network_thread() -> None:
    broker = FakeBroker()
    delivered_on: list[int] = []
    b = MqttBus(
        "t1", client=FakeClient(broker), tap=lambda _m: delivered_on.append(threading.get_ident())
    )
    sub = b.subscribe("duck-1")
    b.start()  # inside the running loop: captures it
    payload = encode(talks("duck-0")[0])

    thread = threading.Thread(target=broker.inject, args=("roboglot/t1/ctl", payload))
    thread.start()
    thread.join()
    assert sub.drain() == [] and b.received == 0  # not delivered inline on the thread
    await asyncio.sleep(0)
    assert b.received == 1 and len(sub.drain()) == 1
    assert delivered_on == [threading.get_ident()]  # the tap ran on the loop's thread
    b.close()


def test_drain_loses_nothing_to_a_concurrent_producer() -> None:
    sub = Subscription("x", lambda _s: None)
    n = 20_000
    talk = talks()[0]

    def produce() -> None:
        for _ in range(n):
            sub._push(talk)

    thread = threading.Thread(target=produce)
    thread.start()
    drained = 0
    while thread.is_alive() or sub._queue:
        drained += len(sub.drain())
    thread.join()
    drained += len(sub.drain())
    assert drained == n


async def test_a_pilot_team_runs_over_the_mqtt_bus(tmp_path: Path) -> None:
    broker = FakeBroker()
    buses: list[MqttBus] = []

    def factory(tap: Any) -> MqttBus:
        bus = MqttBus("team", tap=tap, client=FakeClient(broker))
        buses.append(bus)
        return bus

    roster = roster_from_specs(
        {"duck": RobotSpec("microduck", "mock", "duck"), "arm": RobotSpec("lerobot", "mock", "arm")}
    )
    result = await asyncio.wait_for(
        run_pilot_team(
            load_glot("team-hello"),
            roster,
            # one instance per member: a shared strategy closure would be shared state
            providers={name: FakeProvider.for_glot("team-hello") for name in roster},
            runs_dir=tmp_path,
            bus_factory=factory,
        ),
        timeout=120,
    )
    assert result.ok, result.reason
    bus = buses[0]
    assert bus.started is False and not broker.clients  # closed at the end of the run
    assert bus.published > 0 and bus.dropped == bus.published and bus.received == 0
    summary = json.loads((result.run_dir / "summary.json").read_text(encoding="utf-8"))
    lines = (result.run_dir / "team.jsonl").read_text(encoding="utf-8").splitlines()
    assert summary["bus_messages"] == bus.published
    assert sum(1 for line in lines if json.loads(line)["kind"] == "bus") == bus.published


@pytest.mark.skipif(
    importlib.util.find_spec("paho") is None, reason="paho-mqtt is not installed here"
)
def test_the_real_client_is_paho_v2() -> None:
    import paho.mqtt.client as mqtt

    bus = MqttBus("t1")  # the constructor opens no socket
    assert isinstance(bus._client, mqtt.Client)
    assert bus._client._callback_api_version == mqtt.CallbackAPIVersion.VERSION2


@pytest.mark.skipif(
    importlib.util.find_spec("paho") is not None, reason="paho-mqtt is installed here"
)
def test_without_paho_the_message_names_the_extra() -> None:
    with pytest.raises(LanNotInstalled, match=r"roboglot\[lan\]"):
        MqttBus("t1")

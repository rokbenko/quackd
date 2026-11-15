"""The MQTT team bus on a fake broker: no sockets, no paho needed.

Two `MqttBus` nodes share a synchronous fake broker that routes like a real one (it echoes
the sender's own publications back). What is proven: every message kind round-trips, echo
is dropped, the tap fires exactly once per message per node, QoS and retain are what the
ADR says, unreadable payloads are dropped, duplicates change no decision, remote messages
land on the event loop, and `drain()` loses nothing to a concurrent producer.
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

from roboglot.agent.providers.fake import FakeProvider
from roboglot.glotfile.parser import load_glot
from roboglot.lan import LanNotInstalled
from roboglot.team.auction import Auction, AuctionPolicy
from roboglot.team.bus import Subscription
from roboglot.team.messages import (
    BidMsg,
    ClaimMsg,
    HbMsg,
    Hint,
    HintMsg,
    ResultMsg,
    RoleMsg,
    TaskMsg,
    TeamTask,
    VerdictMsg,
)
from roboglot.team.mqtt_bus import QOS_CTL, QOS_HB, MqttBus, decode, encode
from roboglot.team.runner import run_team


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


def one_of_each(src: str = "coordinator") -> list[Any]:
    base = {"t": 1.0, "src": src, "task_id": "abc"}
    task = TeamTask(task_id="abc", name="kick", goal="kick the ball")
    hint = Hint(target="ball", x_m=1.0, y_m=2.0, by=src, est_dist_m=1.5, bearing_deg=10.0)
    return [
        TaskMsg(**base, task=task, members=["duck-0", "duck-1"]),
        BidMsg(**base, bearing_deg=10.0, ball_dist_m=0.5, confidence=0.9, role="kicker"),
        ClaimMsg(**base, kicker="duck-1", lease_s=6.0, assignments={"kicker": "duck-1"}),
        RoleMsg(**base, duck="duck-1", role="KICK", team_role="kicker", seq=1, hint=hint),
        HbMsg(**base, role="SEARCH", x=0.1, y=0.2),
        ResultMsg(**base, status="kick_done", ball_moved_m=0.2),
        HintMsg(**base, hint=hint),
        VerdictMsg(**base, target="ball", kicker="duck-1", verdict="moved", moved_m=0.5),
    ]


def test_every_message_kind_round_trips_and_echo_is_dropped() -> None:
    broker = FakeBroker()
    tap_a: list[Any] = []
    tap_b: list[Any] = []
    a = node(broker, "coordinator", "duck-0", tap=tap_a)
    b = node(broker, "duck-1", tap=tap_b)
    local = a.subscribe("duck-0-observer")  # a second local subscriber on A
    sent = one_of_each()
    for msg in sent:
        a.publish(msg)
    got = b._subs[0].drain()
    assert got == sent  # same models, same values, in order
    assert {m.kind for m in got} == {
        "TASK",
        "BID",
        "CLAIM",
        "ROLE",
        "HB",
        "RESULT",
        "HINT",
        "VERDICT",
    }
    # A's own subscribers got them by local fan-out, never from the broker echo
    assert local.drain() == sent
    assert a.published == 8 and a.dropped == 8 and a.received == 0
    assert b.received == 8 and b.published == 0 and b.dropped == 0
    # the tap fired exactly once per message per node: on publish at A, on receive at B
    assert tap_a == sent and tap_b == sent
    a.close()
    b.close()
    assert broker.clients == []


def test_hb_is_qos0_control_is_qos1_and_nothing_is_retained() -> None:
    broker = FakeBroker()
    a = node(broker, "duck-0")
    client = a._client
    assert isinstance(client, FakeClient)
    assert client.subscriptions == {"roboglot/t1/ctl": QOS_CTL, "roboglot/t1/hb": QOS_HB}
    for msg in one_of_each("duck-0"):
        a.publish(msg)
    by_topic = {topic: (qos, retain) for topic, _p, qos, retain in client.published}
    assert by_topic == {"roboglot/t1/ctl": (1, False), "roboglot/t1/hb": (0, False)}
    hb = [p for topic, p, _q, _r in client.published if topic.endswith("/hb")]
    assert len(hb) == 1 and json.loads(hb[0])["kind"] == "HB"


def test_unreadable_payloads_are_dropped_and_counted() -> None:
    broker = FakeBroker()
    logged: list[str] = []
    b = MqttBus("t1", client=FakeClient(broker), log=logged.append)
    sub = b.subscribe("duck-1")
    b.start()
    broker.inject("roboglot/t1/ctl", b"not json")
    broker.inject(
        "roboglot/t1/ctl", json.dumps({"kind": "TASK", "src": "x"}).encode()
    )  # fields missing
    broker.inject(
        "roboglot/t1/ctl", json.dumps({"kind": "NOPE", "src": "x", "t": 0, "task_id": "a"}).encode()
    )
    assert b.rejected == 3 and b.received == 0 and sub.drain() == []
    assert len(logged) == 3 and "roboglot/t1/ctl" in logged[0]
    with pytest.raises(ValidationError):
        decode(b"{}")
    assert decode(encode(one_of_each()[1])) == one_of_each()[1]


def test_a_duplicate_bid_changes_no_decision() -> None:
    # QoS 1 may deliver twice; the auction keeps the minimum per source, so twice is once
    broker = FakeBroker(duplicate=True)
    a = node(broker, "duck-1")
    b = node(broker, "coordinator")
    a.publish(
        BidMsg(
            t=1.0,
            src="duck-1",
            task_id="abc",
            bearing_deg=0,
            ball_dist_m=0.6,
            confidence=1,
        )
    )
    a.publish(
        BidMsg(
            t=1.1,
            src="duck-2",
            task_id="abc",
            bearing_deg=0,
            ball_dist_m=0.9,
            confidence=1,
        )
    )
    got = b._subs[0].drain()
    assert len(got) == 4 and b.received == 4  # every control message arrived twice
    auction = Auction(AuctionPolicy(), now=lambda: 10.0)
    bids = [m for m in got if isinstance(m, BidMsg)]
    auction.open(bids[0])
    for bid in bids[1:]:
        auction.add(bid)
    decision = auction.decide(None, set())
    assert decision is not None and decision.kicker == "duck-1"
    assert decision.bids == {"duck-1": 0.6, "duck-2": 0.9} and not decision.tie


async def test_remote_messages_land_on_the_event_loop_not_the_network_thread() -> None:
    broker = FakeBroker()
    delivered_on: list[int] = []
    b = MqttBus(
        "t1", client=FakeClient(broker), tap=lambda _m: delivered_on.append(threading.get_ident())
    )
    sub = b.subscribe("duck-1")
    b.start()  # inside the running loop: captures it
    payload = encode(one_of_each("coordinator")[0])

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
    hb = one_of_each()[4]

    def produce() -> None:
        for _ in range(n):
            sub._push(hb)

    thread = threading.Thread(target=produce)
    thread.start()
    drained = 0
    while thread.is_alive() or sub._queue:
        drained += len(sub.drain())
    thread.join()
    drained += len(sub.drain())
    assert drained == n


async def test_run_team_over_the_mqtt_bus(tmp_path: Path) -> None:
    broker = FakeBroker()
    buses: list[MqttBus] = []

    def factory(tap: Any) -> MqttBus:
        bus = MqttBus("team", tap=tap, client=FakeClient(broker))
        buses.append(bus)
        return bus

    result = await asyncio.wait_for(
        run_team(
            load_glot("team-kick"),
            provider=FakeProvider.for_glot("team-kick"),
            seed=3,
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

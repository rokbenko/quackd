"""Talk over a broker (spec 5.4 and 6.2): two robots that meet, speak and part over MQTT, with
every reply going to the inbox the sender's id names on the same broker, and nowhere else.

The broker is in memory: a message published to a topic reaches every client subscribed to it at
once, in the publisher's thread, which is the hardest order for the three-step hello, since the
second step arrives before the call that sent the first has returned.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from quackd.cli import app
from quackd.lan import passport_mqtt
from quackd.lan.passport_mqtt import PassportPublisher, inbox_url, topics
from quackd.passport import PassportError, SigningKey, document
from quackd.passport.talk import read_message
from quackd.passport_store import PassportStore
from quackd.passport_talk import Heard, Talker, route_on_broker

DUCK, CART = SigningKey.generate(), SigningKey.generate()


class Message:
    def __init__(self, topic: str, payload: bytes) -> None:
        self.topic, self.payload = topic, payload


class Bus:
    """A broker in memory: retained messages kept, every other one handed to each subscriber."""

    def __init__(self) -> None:
        self.retained: dict[str, bytes] = {}
        self.subscribers: dict[str, list[Client]] = {}
        self.published: list[tuple[str, bytes]] = []


class Client:
    def __init__(self, bus: Bus) -> None:
        self.bus, self.on_message = bus, None

    def will_set(self, topic: str, payload: bytes, qos: int = 0, retain: bool = False) -> None:
        pass

    def connect(self, host: str, port: int = 1883, keepalive: int = 60) -> None:
        pass

    def loop_start(self) -> None:
        pass

    def loop_stop(self) -> None:
        pass

    def disconnect(self) -> None:
        for clients in self.bus.subscribers.values():
            if self in clients:
                clients.remove(self)

    def publish(self, topic: str, payload: bytes, qos: int = 0, retain: bool = False) -> None:
        self.bus.published.append((topic, payload))
        if retain:
            self.bus.retained[topic] = payload
            return
        for client in list(self.bus.subscribers.get(topic, [])):
            assert client.on_message is not None
            client.on_message(client, None, Message(topic, payload))

    def subscribe(self, topic: str, qos: int = 0) -> None:
        self.bus.subscribers.setdefault(topic, []).append(self)
        if topic in self.bus.retained and self.on_message is not None:
            self.on_message(self, None, Message(topic, self.bus.retained[topic]))


def on_broker(
    bus: Bus, key: SigningKey, name: str, tmp_path: Path, *, serve: bool
) -> tuple[Talker, PassportPublisher, list[Heard]]:
    """A robot on the broker: its passport naming its inbox there, retained when it `serve`s,
    and its talk end listening on that inbox."""
    doc = {
        "passport": "0.1",
        "id": key.did,
        "name": name,
        "talk": [{"via": "mqtt", "url": inbox_url("broker.local", 1883, key.did)}],
    }
    token = document.sign(doc, key)
    heard: list[Heard] = []
    talker = Talker(name, key, token, PassportStore(tmp_path / name), on_heard=heard.append)
    publisher = PassportPublisher(
        key.short_id,
        token.encode() if serve else None,
        host="broker.local",
        client=Client(bus),
        on_inbox=talker.inbox_mqtt,
    )
    talker.mqtt = publisher
    publisher.start()
    return talker, publisher, heard


def test_two_robots_meet_speak_and_part_over_a_broker(tmp_path: Path) -> None:
    bus = Bus()
    duck, _duck_pub, heard_by_duck = on_broker(bus, DUCK, "duck-01", tmp_path, serve=True)
    cart, _cart_pub, _heard_by_cart = on_broker(bus, CART, "cart-01", tmp_path, serve=False)
    reading = document.read_jws(bus.retained[topics(DUCK.short_id)[0]].decode())
    route = route_on_broker(reading, "broker.local", 1883)
    session = cart.meet(reading, route)
    assert session.authenticated and session.route == route
    cart.say(DUCK.did, "passing on your left")
    # the duck answers over the same broker, to the inbox the cart's id names
    duck.say(cart.id, "go ahead")
    assert any(h.kind == "said" and h.text == "go ahead" for h in cart.drain())
    cart.bye(DUCK.did)
    assert [(h.kind, h.text) for h in heard_by_duck] == [
        ("met", None),
        ("said", "passing on your left"),
        ("left", None),
    ]
    cart_inbox = topics(CART.short_id)[2]
    to_cart = [read_message(p).type for t, p in bus.published if t == cart_inbox]
    assert to_cart == ["hello", "say"]  # the duck's second hello step, then its words


def test_a_robot_listens_on_no_other_broker_than_its_own(tmp_path: Path) -> None:
    bus = Bus()
    cart, _pub, _heard = on_broker(bus, CART, "cart-01", tmp_path, serve=False)
    elsewhere = {
        "passport": "0.1",
        "id": DUCK.did,
        "name": "duck-01",
        "talk": [{"via": "mqtt", "url": inbox_url("10.9.9.9", 1883, DUCK.did)}],
    }
    reading = document.read_jws(document.sign(elsewhere, DUCK))
    with pytest.raises(PassportError, match=r"names no talk channel on broker\.local"):
        route_on_broker(reading, "broker.local", 1883)
    with pytest.raises(PassportError, match="another broker"):
        cart.meet(reading, inbox_url("10.9.9.9", 1883, DUCK.did))


def test_what_nobody_can_read_on_an_inbox_is_dropped(tmp_path: Path) -> None:
    bus = Bus()
    duck, _pub, heard = on_broker(bus, DUCK, "duck-01", tmp_path, serve=True)
    Client(bus).publish(topics(DUCK.short_id)[2], b"{not json")
    Client(bus).publish(topics(DUCK.short_id)[2], b'{"passport": "0.1"}')
    assert heard == [] and duck.endpoint.sessions == {}


def test_nothing_published_to_an_inbox_ends_it_or_steers_its_answers(tmp_path: Path) -> None:
    bus = Bus()
    _duck, _pub, heard = on_broker(bus, DUCK, "duck-01", tmp_path, serve=True)
    inbox = topics(DUCK.short_id)[2]
    before = len(bus.published)
    e_acute = "\N{LATIN SMALL LETTER E WITH ACUTE}"
    hostile = [f"eyJhbGciOiJFZDI1NTE5In0.{e_acute}t{e_acute}.c2ln".encode()]
    # a version this robot does not read, from ids that would have named another topic, or none
    for sender in ("victim/passport#", "x+y", "did:key:zBAD"):
        doc = {"passport": "9.0", "type": "say", "from": sender, "msg": "m" * 22}
        hostile.append(json.dumps(doc).encode())
    for payload in hostile:
        Client(bus).publish(inbox, payload)  # raised into paho's thread, this ended the inbox
    assert [t for t, _p in bus.published[before:] if t != inbox] == []  # nothing answered
    cart, _cart_pub, _heard_by_cart = on_broker(bus, CART, "cart-01", tmp_path, serve=False)
    reading = document.read_jws(bus.retained[topics(DUCK.short_id)[0]].decode())
    cart.meet(reading, route_on_broker(reading, "broker.local", 1883))
    assert [h.kind for h in heard] == ["met"]  # and the inbox still hears whoever comes next


def test_meet_over_a_broker_as_one_of_your_robots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bus = Bus()
    _duck, _pub, heard_by_duck = on_broker(bus, DUCK, "duck-01", tmp_path, serve=True)
    real = passport_mqtt.PassportPublisher

    def on_this_bus(*args: Any, **kwargs: Any) -> PassportPublisher:
        return real(*args, **{**kwargs, "client": Client(bus)})

    monkeypatch.setattr(passport_mqtt, "PassportPublisher", on_this_bus)
    monkeypatch.setattr(
        passport_mqtt,
        "read_passport",
        lambda host, key, **_kw: bus.retained[topics(key)[0]],
    )
    met = CliRunner().invoke(
        app,
        [
            "meet",
            f"mqtt://broker.local/{DUCK.short_id}",
            "--as",
            "lerobot:mock",
            "--say",
            "passing on your left",
            "--registry-dir",
            str(tmp_path / "visitor"),
        ],
    )
    assert met.exit_code == 0, met.output
    flat = " ".join(met.output.split())
    assert "hello: duck-01 proved it holds its key, and so did lerobot-mock" in flat
    assert [(h.kind, h.text) for h in heard_by_duck] == [
        ("met", None),
        ("said", "passing on your left"),
        ("left", None),
    ]

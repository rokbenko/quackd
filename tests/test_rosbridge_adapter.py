"""The rosbridge adapter: a wheeled base that takes a Twist, and the messages it speaks."""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import io
import json
import math
import time
from typing import Any

import pytest
from PIL import Image

from quackd.adapters.base import AdapterNotInstalled
from quackd.adapters.factory import describe, make_adapter, parse_robot_spec
from quackd.perception.color_blob import ColorBlobDetector
from quackd.safety import Executor
from quackd.space.places import Place, PlaceBook, PlaceStore
from quackd.transport.base import HeartbeatError, Intent, TransportError
from quackd.verbs.core import TICK_S, speed_limits
from quackd.verbs.navigate import Gait
from quackd.verbs.places import PLACE_VERBS, GoToPlaceParams
from quackd.verbs.registry import VerbContext, VerbResult, registry_from_manifest
from quackd_microduck import microduck_manifest
from quackd_rosbridge import DATASHEET, RosbridgeAdapter, rosbridge_manifest
from quackd_rosbridge.introspection import datasheet_from_introspection, parse_urdf
from quackd_rosbridge.mock import MOCK_URDF, RosbridgeMock, mock_introspection
from quackd_rosbridge.ws import (
    RosbridgeWs,
    decode_compressed_image,
    parse_address,
    twist_message,
    yaw_from_quaternion,
)

PLACES = {"map", "mark_place", "forget_place", "go_to_place"}
MOCK_VERBS = {
    "observe",
    "report_state",
    "stop",
    "move",
    "go_to",
    "search_scan",
    "approach_and",
    "introspect",
    *PLACES,
}
NO_ROSLIBPY = importlib.util.find_spec("roslibpy") is None


def test_the_address_carries_the_topics() -> None:
    default = parse_address(None)
    assert (default.host, default.port, default.secure) == ("localhost", 9090, False)
    assert (default.cmd_vel, default.odom, default.image) == ("/cmd_vel", "/odom", None)
    full = parse_address("wss://robot.local:9443?cmd_vel=/base/cmd_vel&image=/cam/compressed")
    assert full.secure and full.url == "wss://robot.local:9443"
    assert full.cmd_vel == "/base/cmd_vel" and full.image == "/cam/compressed"
    assert full.odom == "/odom"
    with pytest.raises(TransportError, match="ws://"):
        parse_address("http://robot.local:9090")
    with pytest.raises(TransportError, match="must start with /"):
        parse_address("ws://robot.local:9090?cmd_vel=cmd_vel")


def test_manifest_is_a_wheeled_base_with_twist_only() -> None:
    m = rosbridge_manifest("mock", camera=True)
    assert m.embodiment == "wheeled" and m.mobility == "wheeled" and m.intents == ["twist"]
    assert set(m.verb_names()) == MOCK_VERBS
    assert not any(m.provides(v) for v in ("say", "gaze", "kick", "sit", "express"))
    assert m.provides("walk_to") and m.provides("walk")  # the aliases of go_to and move
    assert m.safety_authority.native == "none" and not m.safety_authority.deadman
    assert m.limits == {"max_vx": 0.3, "max_vy": 0.0, "max_wz": 1.0}
    # the mock serves a canned description, so its static row already knows the body
    described = describe(parse_robot_spec("rosbridge:mock"))
    assert described == rosbridge_manifest("mock", camera=True, datasheet=described.datasheet)
    assert described.datasheet is not None and described.datasheet.mass_kg is not None
    assert described.datasheet.mass_kg.value == 11.0

    blind = describe(parse_robot_spec("rosbridge:ws"))
    assert set(blind.verb_names()) == {"report_state", "stop", "move", "introspect", *PLACES}
    assert blind.sensors == ["odometry"] and blind.extras["image"] is None
    # a name says nothing about a body: the ws backend knows nothing until it has asked
    assert blind.datasheet is not None and blind.datasheet.mass_kg is None
    assert blind.datasheet.dof is None


async def test_mock_base_drives_coasts_on_silence_and_reaches_the_ball() -> None:
    adapter = RosbridgeAdapter(RosbridgeMock())
    manifest = await adapter.connect()
    ex = Executor(
        registry_from_manifest(manifest, adapter),
        adapter,
        detector=ColorBlobDetector(),
        manifest=manifest,
    )
    mock = adapter.transport
    assert isinstance(mock, RosbridgeMock)
    assert (await ex.run_verb("move", {"vx": 0.2, "duration_s": 1.0})).ok
    assert abs(mock.x - 0.2) < 0.03 and mock.twists[-1] == {"vx": 0.0, "vy": 0.0, "wz": 0.0}
    # a raw Twist with nobody re-sending it: the deadman coasts the base to zero
    x0 = mock.x
    assert (await adapter.send_intent(Intent.move(0.2, 0.0, 0.0))).accepted
    await adapter.sleep(2.0)
    assert 0.09 < mock.x - x0 < 0.13
    assert not (await adapter.send_intent(Intent.look(1.0, 0.0, 0.0))).accepted
    found = await ex.run_verb("search_scan", {"target": "ball"})
    assert found.ok and found.data["steps"] == 0  # 18 degrees off the nose: in view already
    reached = await ex.run_verb("go_to", {"target": "ball"})
    assert reached.ok, reached.summary
    rel = mock.ball_relative()
    assert rel is not None and rel[0] < 0.4
    state = await ex.run_verb("report_state")
    assert state.ok and state.data["state"]["extras"]["odom"]["x"] > 0.8
    assert (await adapter.health()).ok


async def test_speed_limits_come_from_the_manifest() -> None:
    assert speed_limits(None) == (0.3, 0.2, 1.5)
    # the Microduck narrows the schema's turn to its own, 1.0 rad/s from 0.21.0
    assert speed_limits(microduck_manifest("sim2d")) == (0.3, 0.2, 1.0)
    adapter = RosbridgeAdapter(RosbridgeMock())
    await adapter.connect()
    slow = rosbridge_manifest("mock", camera=True, max_vx=0.1, max_wz=0.5)
    ex = Executor(registry_from_manifest(slow, adapter), adapter, manifest=slow)
    res = await ex.run_verb("move", {"vx": 0.3, "wz": 1.0, "duration_s": 0.5})
    assert res.ok and "clamped" in res.summary
    mock = adapter.transport
    assert isinstance(mock, RosbridgeMock)
    assert mock.twists[0] == {"vx": 0.1, "vy": 0.0, "wz": 0.5}


class FakeTopic:
    def __init__(self, ros: Any, name: str, message_type: str) -> None:
        self.name = name
        self.message_type = message_type
        self.published: list[dict[str, Any]] = []
        self.callback: Any = None
        self.unsubscribed = False
        self.unadvertised = False

    def publish(self, message: Any) -> None:
        self.published.append(dict(message))

    def subscribe(self, callback: Any) -> None:
        self.callback = callback

    def unsubscribe(self) -> None:
        self.unsubscribed = True

    def unadvertise(self) -> None:
        self.unadvertised = True


class FakeRos:
    def __init__(self) -> None:
        self.is_connected = False
        self.runs = 0
        self.terminated = False

    def run(self, timeout: float = 5.0) -> None:
        self.runs += 1
        self.is_connected = True

    def close(self, timeout: float = 5.0) -> None:
        self.is_connected = False

    def terminate(self) -> None:
        self.terminated = True
        self.is_connected = False


def _png(color: tuple[int, int, int], size: tuple[int, int] = (8, 6), fmt: str = "PNG") -> str:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format=fmt, quality=95)
    return base64.b64encode(buf.getvalue()).decode("ascii")


async def test_ws_backend_speaks_verified_topics_and_messages() -> None:
    ros = FakeRos()
    topics: dict[str, FakeTopic] = {}

    def factory(client: Any, name: str, message_type: str) -> FakeTopic:
        return topics.setdefault(name, FakeTopic(client, name, message_type))

    ws = RosbridgeWs(
        "ws://robot.local:9090?image=/cam/compressed&cmd_vel=/base/cmd_vel",
        ros=ros,
        topic_factory=factory,
    )
    adapter = RosbridgeAdapter(ws)
    manifest = await adapter.connect()
    assert ros.runs == 1 and set(topics) == {"/base/cmd_vel", "/odom", "/cam/compressed"}
    assert topics["/base/cmd_vel"].message_type == "geometry_msgs/msg/Twist"
    assert topics["/odom"].message_type == "nav_msgs/msg/Odometry"
    assert topics["/cam/compressed"].message_type == "sensor_msgs/msg/CompressedImage"
    assert manifest.provides("observe") and manifest.extras["image"] == "/cam/compressed"
    assert manifest.extras["cmd_vel"] == "/base/cmd_vel"

    assert (await adapter.send_intent(Intent.move(0.2, 0.0, 0.5))).accepted
    assert topics["/base/cmd_vel"].published == [twist_message(0.2, 0.0, 0.5)]
    assert twist_message(0.2, 0.0, 0.5) == {
        "linear": {"x": 0.2, "y": 0.0, "z": 0.0},
        "angular": {"x": 0.0, "y": 0.0, "z": 0.5},
    }
    before = await adapter.get_state()
    assert before.x is None and before.extras["odom_seen"] is False
    topics["/odom"].callback(
        {
            "pose": {
                "pose": {
                    "position": {"x": 1.0, "y": 2.0, "z": 0.0},
                    "orientation": {
                        "x": 0.0,
                        "y": 0.0,
                        "z": math.sin(math.pi / 4),
                        "w": math.cos(math.pi / 4),
                    },
                }
            }
        }
    )
    after = await adapter.get_state()
    assert after.x == 1.0 and after.y == 2.0 and after.theta is not None
    assert abs(after.theta - math.pi / 2) < 1e-6
    assert await adapter.get_frame() is None  # nothing published on the image topic yet
    topics["/cam/compressed"].callback(
        {"format": "rgb8; png compressed rgb8", "data": _png((255, 0, 0))}
    )
    frame = await adapter.get_frame()
    assert frame is not None and frame.size == (8, 6) and frame.getpixel((1, 1)) == (255, 0, 0)
    topics["/cam/compressed"].callback(
        {"format": "bgr8; jpeg compressed bgr8", "data": _png((255, 0, 0), fmt="JPEG")}
    )
    swapped = await adapter.get_frame()
    assert swapped is not None
    r, _g, b = swapped.getpixel((1, 1))
    assert b > 200 and r < 50  # the format said bgr8, so the channels were swapped
    await adapter.stop()
    assert topics["/base/cmd_vel"].published[-1] == twist_message(0.0, 0.0, 0.0)
    await adapter.heartbeat()
    ros.is_connected = False
    with pytest.raises(HeartbeatError):
        await adapter.heartbeat()
    ros.is_connected = True
    await adapter.close()
    assert topics["/odom"].unsubscribed and topics["/cam/compressed"].unsubscribed
    assert topics["/base/cmd_vel"].unadvertised and ros.terminated
    assert topics["/base/cmd_vel"].published[-1] == twist_message(0.0, 0.0, 0.0)


def test_message_helpers() -> None:
    assert abs(yaw_from_quaternion({"x": 0, "y": 0, "z": 0, "w": 1})) < 1e-9
    with pytest.raises(ValueError, match="jpeg or png"):
        decode_compressed_image({"format": "rgb8; tiff compressed", "data": _png((1, 2, 3))})
    plain = decode_compressed_image({"format": "", "data": _png((0, 255, 0))})
    assert plain.getpixel((0, 0)) == (0, 255, 0)


@pytest.mark.skipif(not NO_ROSLIBPY, reason="roslibpy is installed here")
async def test_ws_backend_without_the_extra_names_it() -> None:
    adapter = make_adapter("rosbridge:ws", address="ws://robot.local:9090")
    with pytest.raises(AdapterNotInstalled, match=r"quackd\[rosbridge\]"):
        await adapter.connect()


# ── what the bridge says about the body ─────────────────────────────────────────────────


def test_parse_urdf_reads_the_mass_the_joints_and_their_limits() -> None:
    summary = parse_urdf(MOCK_URDF)
    assert summary.parsed and summary.name == "mock-base"
    assert (summary.links, summary.links_with_mass) == (4, 3)
    assert summary.mass_kg == 11.0  # the caster has no inertial and is not guessed at
    assert summary.dof == 2 and len(summary.joints) == 3
    wheel = summary.joints[0]
    assert (wheel.name, wheel.type) == ("left_wheel_joint", "continuous")
    assert (wheel.parent, wheel.child) == ("base_link", "left_wheel")
    assert (wheel.effort, wheel.velocity) == (5.0, 10.0)
    assert wheel.lower is None and wheel.upper is None  # a continuous joint has no stops
    caster = summary.joints[-1]
    assert caster.type == "fixed" and not caster.moves
    assert summary.errors == ()


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ("", "empty"),
        ("<<not xml", "not XML"),
        ("<html/>", "not <robot>"),
        ('<robot name="x">${arm_mass}</robot>', "unexpanded xacro"),
        ('<robot><link name="a"><inertial><mass/></inertial></link></robot>', "no readable mass"),
        (
            '<robot><link name="a"><inertial><mass value="heavy"/></inertial></link></robot>',
            "no readable mass",
        ),
        ('<robot><joint name="j" type="wobbly"/></robot>', "unknown type"),
    ],
)
def test_parse_urdf_never_raises_on_a_description_it_cannot_use(text: str, needle: str) -> None:
    """A robot that answered with nonsense still answered, and the pilot is told which."""
    summary = parse_urdf(text)
    assert any(needle in e for e in summary.errors), summary.errors
    assert summary.mass_kg is None


def test_an_oversize_description_is_refused_rather_than_parsed() -> None:
    huge = "<robot>" + ("<link name='x'/>" * 600_000) + "</robot>"
    summary = parse_urdf(huge)
    assert not summary.parsed and "over" in summary.errors[0]


def test_the_datasheet_says_only_what_the_urdf_says() -> None:
    sheet = datasheet_from_introspection(mock_introspection(), base=DATASHEET)
    assert sheet.mass_kg is not None
    assert sheet.mass_kg.value == 11.0 and sheet.mass_kg.confidence == "official"
    assert "canned URDF" in sheet.mass_kg.source
    assert "3 link inertials" in sheet.mass_kg.note
    assert sheet.dof is not None and sheet.dof.value == 2.0
    # a description says nothing about what a gripper holds, so nothing is claimed
    assert sheet.payload_kg is None and sheet.reach_m is None and sheet.terrain is None
    assert sheet.manipulator == "none"
    assert any("4 links (3 with inertials)" in n for n in sheet.notes)
    assert any("left_wheel_joint (continuous" in n for n in sheet.notes)


def test_a_bridge_that_says_nothing_leaves_the_body_unknown() -> None:
    sheet = datasheet_from_introspection(mock_introspection(urdf=None), base=DATASHEET)
    assert sheet.mass_kg is None and sheet.dof is None
    sheet = datasheet_from_introspection(None, base=DATASHEET)
    assert any("nothing discovered on the bridge" in n for n in sheet.notes)
    assert sheet.mass_kg is None


def test_the_address_names_both_places_a_description_hides() -> None:
    default = parse_address(None)
    assert default.urdf_param == "/robot_state_publisher:robot_description"
    assert default.urdf_topic == "/robot_description"
    off = parse_address("ws://robot.local:9090?urdf_param=off&urdf_topic=off")
    assert off.urdf_param is None and off.urdf_topic is None
    named = parse_address("ws://h:9090?urdf_param=/my_node:urdf&urdf_topic=/desc")
    assert named.urdf_param == "/my_node:urdf" and named.urdf_topic == "/desc"
    with pytest.raises(TransportError, match="must start with /"):
        parse_address("ws://h:9090?urdf_topic=robot_description")
    with pytest.raises(TransportError, match="name a node and a parameter"):
        parse_address("ws://h:9090?urdf_param=robot_description")


async def test_the_mock_serves_a_description_and_the_verb_reads_it_again() -> None:
    adapter = RosbridgeAdapter(RosbridgeMock())
    manifest = await adapter.connect()
    assert manifest.provides("introspect")
    assert manifest.datasheet is not None and manifest.datasheet.mass_kg is not None
    assert manifest.datasheet.mass_kg.value == 11.0

    ex = Executor(registry_from_manifest(manifest, adapter), adapter, manifest=manifest)
    result = await ex.run_verb("introspect")
    assert result.ok
    assert result.data["urdf"]["name"] == "mock-base"
    assert result.data["urdf"]["dof"] == 2
    assert result.data["topics"]["/cmd_vel"] == "geometry_msgs/msg/Twist"
    assert result.data["topics"]["/robot_description"] == "std_msgs/msg/String"
    assert result.data["errors"] == []
    mock = adapter.transport
    assert isinstance(mock, RosbridgeMock)
    assert mock.introspections == 1
    assert mock.twists == [], "asking about the body sends nothing"


async def test_reading_the_description_runs_even_under_dry_run() -> None:
    adapter = RosbridgeAdapter(RosbridgeMock())
    manifest = await adapter.connect()
    ex = Executor(
        registry_from_manifest(manifest, adapter), adapter, manifest=manifest, dry_run=True
    )
    assert (await ex.run_verb("introspect")).ok, "it reads, it does not act"


async def test_a_mock_without_a_description_still_connects() -> None:
    adapter = RosbridgeAdapter(RosbridgeMock(urdf=None))
    manifest = await adapter.connect()
    assert manifest.datasheet is not None and manifest.datasheet.mass_kg is None
    assert any(
        "nothing discovered" in n or "without a description" in n for n in manifest.datasheet.notes
    )


class FakeService:
    """The slice of `roslibpy.Service` the backend uses, with the failures a bridge has."""

    def __init__(
        self,
        answer: dict[str, Any] | None = None,
        *,
        raise_with: Exception | None = None,
        hang_s: float = 0.0,
    ) -> None:
        self.answer = answer or {}
        self.raise_with = raise_with
        self.hang_s = hang_s
        self.calls: list[dict[str, Any]] = []

    def call(self, args: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        self.calls.append(dict(args))
        if self.hang_s:
            time.sleep(self.hang_s)
        if self.raise_with is not None:
            raise self.raise_with
        return dict(self.answer)


def _services(**answers: FakeService) -> tuple[Any, dict[str, FakeService], dict[str, str]]:
    """A service factory over a fixed table, plus what it was asked for."""
    by_name = {
        "/rosapi/topics": answers.get("topics", FakeService()),
        "/rosapi/get_param": answers.get("get_param", FakeService()),
    }
    types: dict[str, str] = {}

    def factory(client: Any, name: str, service_type: str) -> FakeService:
        types[name] = service_type
        return by_name[name]

    return factory, by_name, types


async def test_the_ws_backend_asks_the_bridge_what_the_body_is() -> None:
    topics = FakeService({"topics": ["/cmd_vel", "/odom"], "types": ["T", "O"]})
    param = FakeService({"value": json.dumps(MOCK_URDF), "successful": True, "reason": ""})
    factory, _by_name, types = _services(topics=topics, get_param=param)
    ws = RosbridgeWs(
        "ws://robot.local:9090",
        ros=FakeRos(),
        topic_factory=lambda c, n, t: FakeTopic(c, n, t),
        service_factory=factory,
    )
    adapter = RosbridgeAdapter(ws)
    manifest = await adapter.connect()

    assert types["/rosapi/topics"] == "rosapi_msgs/srv/Topics"
    assert types["/rosapi/get_param"] == "rosapi_msgs/srv/GetParam"
    assert param.calls == [
        {"name": "/robot_state_publisher:robot_description", "default_value": ""}
    ]
    intro = ws.introspection
    assert intro is not None and intro.source == "param"
    assert intro.topics == {"/cmd_vel": "T", "/odom": "O"}
    assert manifest.datasheet is not None and manifest.datasheet.mass_kg is not None
    assert manifest.datasheet.mass_kg.value == 11.0
    assert "robot_description" in manifest.datasheet.mass_kg.source

    state = await adapter.get_state()
    assert "URDF_MASS_IS_THE_BODY" in state.extras["assumptions"]
    assert "DOF_IS_NON_FIXED_JOINTS" in state.extras["assumptions"]
    assert "DESCRIPTION_NAMES" in state.extras["assumptions"]
    assert state.extras["introspection"]["source"] == "param"


async def test_the_ws_backend_falls_back_to_the_latched_topic() -> None:
    topics = FakeService({"topics": ["/cmd_vel"], "types": ["T"]})
    param = FakeService({"value": "", "successful": False, "reason": "node not found"})
    factory, _by_name, _types = _services(topics=topics, get_param=param)

    class LatchedTopic(FakeTopic):
        def subscribe(self, callback: Any) -> None:
            super().subscribe(callback)
            if self.name == "/robot_description":
                callback({"data": MOCK_URDF})

    made: dict[str, LatchedTopic] = {}

    def topic_factory(client: Any, name: str, message_type: str) -> LatchedTopic:
        return made.setdefault(name, LatchedTopic(client, name, message_type))

    ws = RosbridgeWs(
        "ws://robot.local:9090", ros=FakeRos(), topic_factory=topic_factory, service_factory=factory
    )
    await RosbridgeAdapter(ws).connect()
    intro = ws.introspection
    assert intro is not None and intro.source == "topic"
    assert intro.where == "/robot_description"
    assert intro.mass_kg == 11.0
    assert made["/robot_description"].message_type == "std_msgs/msg/String"
    assert made["/robot_description"].unsubscribed, "a one-shot read leaves no subscription"
    assert any("node not found" in e for e in intro.errors)


async def test_a_bridge_that_will_not_answer_still_drives() -> None:
    """Introspection is bounded and never fails a connect: the datasheet says it knows
    nothing, and the pilot declines whatever hinges on that."""
    factory, _by_name, _types = _services(
        topics=FakeService(raise_with=RuntimeError("no rosapi here")),
        get_param=FakeService(hang_s=1.0),
    )
    ws = RosbridgeWs(
        "ws://robot.local:9090?urdf_topic=off",
        ros=FakeRos(),
        topic_factory=lambda c, n, t: FakeTopic(c, n, t),
        service_factory=factory,
        timeout_s=0.3,
    )
    adapter = RosbridgeAdapter(ws)
    started = time.monotonic()
    manifest = await adapter.connect()
    assert time.monotonic() - started < 3.0, "one deadline covers the whole look"

    intro = ws.introspection
    assert intro is not None and not intro.discovered
    assert any("no rosapi here" in e for e in intro.errors)
    assert manifest.datasheet is not None and manifest.datasheet.mass_kg is None
    assert any("nothing discovered on the bridge" in n for n in manifest.datasheet.notes)
    # and it still drives
    assert (await adapter.send_intent(Intent.move(0.2, 0.0, 0.0))).accepted


async def test_a_parameter_reads_whether_or_not_it_arrives_as_json() -> None:
    for value in (MOCK_URDF, json.dumps(MOCK_URDF)):
        factory, _by_name, _types = _services(
            topics=FakeService({"topics": [], "types": []}),
            get_param=FakeService({"value": value, "successful": True, "reason": ""}),
        )
        ws = RosbridgeWs(
            "ws://h:9090",
            ros=FakeRos(),
            topic_factory=lambda c, n, t: FakeTopic(c, n, t),
            service_factory=factory,
        )
        await RosbridgeAdapter(ws).connect()
        assert ws.introspection is not None and ws.introspection.mass_kg == 11.0


async def test_the_verb_refreshes_a_datasheet_that_was_empty_at_connect() -> None:
    param = FakeService({"value": "", "successful": False, "reason": "not up yet"})
    factory, _by_name, _types = _services(
        topics=FakeService({"topics": [], "types": []}), get_param=param
    )
    ws = RosbridgeWs(
        "ws://h:9090?urdf_topic=off",
        ros=FakeRos(),
        topic_factory=lambda c, n, t: FakeTopic(c, n, t),
        service_factory=factory,
    )
    adapter = RosbridgeAdapter(ws)
    manifest = await adapter.connect()
    assert manifest.datasheet is not None and manifest.datasheet.mass_kg is None

    # the robot came up; now the parameter is there
    param.answer = {"value": json.dumps(MOCK_URDF), "successful": True, "reason": ""}
    ex = Executor(registry_from_manifest(manifest, adapter), adapter, manifest=manifest)
    assert (await ex.run_verb("introspect")).ok
    assert manifest.datasheet.mass_kg is not None
    assert manifest.datasheet.mass_kg.value == 11.0


# ── where the base is, by its /odom ─────────────────────────────────────────────────────


def _odom(x: float, y: float = 0.0, *, stamp: float | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {
        "pose": {
            "pose": {
                "position": {"x": x, "y": y, "z": 0.0},
                "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
            }
        }
    }
    if stamp is not None:
        sec = int(stamp)
        message["header"] = {"stamp": {"sec": sec, "nanosec": int((stamp - sec) * 1e9)}}
    return message


class _TimedTopic(FakeTopic):
    """A topic that writes down when each message went out, on this machine's clock."""

    def __init__(self, ros: Any, name: str, message_type: str) -> None:
        super().__init__(ros, name, message_type)
        self.times: list[float] = []

    def publish(self, message: Any) -> None:
        self.times.append(time.monotonic())
        super().publish(message)


async def _ws() -> tuple[RosbridgeWs, RosbridgeAdapter, dict[str, _TimedTopic]]:
    topics: dict[str, _TimedTopic] = {}

    def factory(client: Any, name: str, message_type: str) -> _TimedTopic:
        return topics.setdefault(name, _TimedTopic(client, name, message_type))

    ws = RosbridgeWs("ws://robot.local:9090", ros=FakeRos(), topic_factory=factory)
    adapter = RosbridgeAdapter(ws)
    await adapter.connect()
    return ws, adapter, topics


async def test_odom_is_where_the_base_is_dated_by_its_stamp_and_the_place_verbs_are_offered() -> (
    None
):
    ws, adapter, topics = await _ws()
    assert adapter.manifest is not None and set(adapter.manifest.verb_names()) >= PLACES
    assert ws.spatial is not None and ws.spatial.fix().lost == "no odometry reading yet"
    topics["/odom"].callback(_odom(1.5, -0.5, stamp=1234.25))
    fix = ws.spatial.fix()
    assert fix.pose is not None and (fix.pose.x, fix.pose.y) == (1.5, -0.5)
    assert fix.t == pytest.approx(1234.25) and fix.kind == "odometry"
    where = (await adapter.get_state()).extras["where"]
    assert where["kind"] == "odometry" and where["frame"].startswith("odom:")


async def test_an_odom_the_stack_does_not_stamp_is_dated_by_its_arrival() -> None:
    ws, _adapter, topics = await _ws()
    before = time.monotonic()
    topics["/odom"].callback(_odom(0.2))
    fix = ws.spatial.fix() if ws.spatial is not None else None
    assert fix is not None and fix.t is not None and before <= fix.t <= time.monotonic()


async def _go(adapter: RosbridgeAdapter, book: PlaceBook, timeout_s: float) -> VerbResult:
    assert adapter.manifest is not None
    ctx = VerbContext(transport=adapter, manifest=adapter.manifest, places=book)
    return await PLACE_VERBS["go_to_place"].execute(
        ctx, GoToPlaceParams(name="dock", face=False, timeout_s=timeout_s)
    )


async def test_odom_gone_quiet_holds_the_base_still_and_then_ends_the_walk() -> None:
    """rosbridge has no deadman, so a twist left standing drives the base on: a walk on a reading
    that stopped moving on holds the base still within a reading's freshness, and ends."""
    ws, adapter, topics = await _ws()
    assert ws.spatial is not None and adapter.manifest is not None
    t0 = 100.0
    topics["/odom"].callback(_odom(0.0, stamp=t0))
    book = PlaceBook(PlaceStore("base", persist=False), ws.spatial)
    frame = book.frame()
    assert frame is not None
    book.store.mark(Place("dock", 2.0, 0.0, 0.0, frame.id, "odometry", 0.0))
    ctx = VerbContext(transport=adapter, manifest=adapter.manifest, places=book)
    stopped: list[float] = []

    async def odometry_then_quiet() -> None:
        x, t = 0.0, t0
        for _ in range(15):  # a second and a half of a base that drives as told, then nothing
            await asyncio.sleep(0.1)
            x, t = x + 0.015, t + 0.1
            topics["/odom"].callback(_odom(x, stamp=t))
        stopped.append(time.monotonic())

    feed = asyncio.create_task(odometry_then_quiet())
    result = await PLACE_VERBS["go_to_place"].execute(
        ctx, GoToPlaceParams(name="dock", face=False, timeout_s=30.0)
    )
    await feed
    assert not result.ok and result.data["outcome"] in ("stale", "lost"), result.summary
    gait = Gait.of(ctx, ws.spatial, {})
    cmd = topics["/cmd_vel"]
    sent = list(zip(cmd.times, cmd.published, strict=True))
    moving = [m for _, m in sent if m["linear"]["x"] or m["angular"]["z"]]
    assert moving, "it never drove"
    held = [m for at, m in sent if at > stopped[0] + gait.fresh_s + 0.2]
    assert held, "nothing went out after the reading stopped"
    assert all(not m["linear"]["x"] and not m["angular"]["z"] for m in held), held
    assert sent[-1][1] == twist_message(0.0, 0.0, 0.0)


async def test_the_period_is_the_streams_own_however_seldom_quackd_reads_it() -> None:
    """quackd reads where the base is at each verb, seconds apart while a pilot thinks, and
    /odom streams all the while. Taken only when read, the track measured how often quackd
    looked, and a walk sized its tolerance from that: a base its own joystick had driven three
    metres off in half a minute was said to be at its place already."""
    ws, adapter, topics = await _ws()
    assert ws.spatial is not None
    odom, period, t0 = topics["/odom"], 0.02, 100.0
    odom.callback(_odom(0.0, stamp=t0))
    book = PlaceBook(PlaceStore("base", persist=False), ws.spatial)
    assert book.mark("dock")[0].x == 0.0
    steps = 1500  # half a minute of /odom, none of it read
    for i in range(1, steps + 1):
        odom.callback(_odom(3.0 * i / steps, stamp=t0 + period * i))
    assert ws.spatial.period_s == pytest.approx(period)
    result = await _go(adapter, book, timeout_s=5.0)
    assert not result.ok and "already at" not in result.summary, result.summary


async def test_a_pause_in_odom_before_a_walk_does_not_stretch_how_long_it_drives_blind() -> None:
    """rosbridge has no deadman. The walk holds a twist for a reading's freshness and then
    zeroes it, and that freshness is sized from the period: a pause before the walk began read
    as the stream's period, and the base drove on long after /odom stopped."""
    ws, adapter, topics = await _ws()
    assert ws.spatial is not None and adapter.manifest is not None
    odom = topics["/odom"]
    odom.callback(_odom(0.0, stamp=100.0))
    book = PlaceBook(PlaceStore("base", persist=False), ws.spatial)
    book.mark("dock")
    odom.callback(_odom(-3.0, stamp=106.0))  # pushed off by hand while /odom was quiet
    last: list[float] = []

    async def drives_then_dies() -> None:
        x, t = -3.0, 106.0
        for _ in range(20):  # a second of a base that drives as told, then nothing
            await asyncio.sleep(0.05)
            x, t = x + 0.0075, t + 0.05
            odom.callback(_odom(x, stamp=t))
        last.append(time.monotonic())

    feed = asyncio.create_task(drives_then_dies())
    result = await _go(adapter, book, timeout_s=30.0)
    await feed
    assert not result.ok, result.summary
    gait = Gait.of(
        VerbContext(transport=adapter, manifest=adapter.manifest, places=book), ws.spatial, {}
    )
    cmd = topics["/cmd_vel"]
    moving = [at for at, m in zip(cmd.times, cmd.published, strict=True) if m["linear"]["x"]]
    assert moving, "it never drove"
    assert moving[-1] - last[0] <= gait.fresh_s + 2 * TICK_S, (moving[-1] - last[0], gait)


async def test_an_unstamped_odom_on_a_stamped_stream_is_no_reading() -> None:
    """A stamp of nothing on a stream the stack stamps (sim time before /clock, a second
    publisher) is another clock's: dated by its arrival in the same track, it sent the frame
    back and forth between two clocks and orphaned every place."""
    ws, _adapter, topics = await _ws()
    assert ws.spatial is not None
    odom, t0 = topics["/odom"], 1_761_000_000.0  # ROS time, in epoch seconds
    odom.callback(_odom(0.0, stamp=t0))
    book = PlaceBook(PlaceStore("base", persist=False), ws.spatial)
    place, _ = book.mark("dock")
    odom.callback(_odom(0.01))
    odom.callback(_odom(0.02, stamp=t0 + 0.1))
    odom.callback(_odom(0.03))
    fix = ws.spatial.fix()
    assert fix.pose is not None and fix.pose.x == 0.02 and fix.t == pytest.approx(t0 + 0.1)
    assert book.view(place).status == "here"


async def test_odom_stamped_after_some_that_were_not_goes_on_in_the_same_frame() -> None:
    """A stack whose first messages go unstamped, sim time before /clock: from its first stamp
    on the readings are dated by the stack's clock, far behind this machine's, in one frame."""
    ws, _adapter, topics = await _ws()
    assert ws.spatial is not None
    odom = topics["/odom"]
    odom.callback(_odom(0.0))
    book = PlaceBook(PlaceStore("base", persist=False), ws.spatial)
    place, _ = book.mark("dock")
    odom.callback(_odom(0.01))
    for i in range(1, 4):
        odom.callback(_odom(0.01 + 0.01 * i, stamp=3.0 + 0.1 * i))
    assert book.view(place).status == "here"
    assert ws.spatial.fix().t == pytest.approx(3.3)
    assert ws.spatial.period_s == pytest.approx(0.1)


async def test_the_same_stamp_again_is_no_new_reading_and_the_stream_goes_stale() -> None:
    """A stack that has frozen sends its last /odom again, stamped as before. That says nothing
    of where the base is now, so the reading goes stale as if nothing came at all."""
    ws, _adapter, topics = await _ws()
    assert ws.spatial is not None
    odom = topics["/odom"]
    odom.callback(_odom(0.0, stamp=100.0))
    assert ws.spatial.fix().localized
    deadline = time.monotonic() + 3.0
    while ws.spatial.fix().localized and time.monotonic() < deadline:
        await asyncio.sleep(TICK_S)
        odom.callback(_odom(0.0, stamp=100.0))
    fix = ws.spatial.fix()
    assert not fix.localized and "no new /odom" in (fix.lost or ""), fix.lost


async def test_the_mocks_places_last_one_connection_as_the_ws_backends_do(tmp_path: Any) -> None:
    first = RosbridgeMock()
    await first.connect()
    assert first.spatial is not None
    PlaceBook(PlaceStore("base", base_dir=tmp_path), first.spatial).mark("dock")
    await first.close()
    second = RosbridgeMock()
    await second.connect()
    assert second.spatial is not None
    book = PlaceBook(PlaceStore("base", base_dir=tmp_path), second.spatial)
    assert {v.place.name: v.status for v in book.views()} == {"dock": "orphaned"}
